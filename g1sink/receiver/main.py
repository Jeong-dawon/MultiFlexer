
# ================================================================
# Multi-Sender WebRTC Receiver (GStreamer + Socket.IO, no PyQt)
# ================================================================
# - GStreamer 의 webrtcbin을 사용해 실시간 화면을 수신하는 코드
# - 송신자(sender)별로 webrtcbin/pipeline을 완전히 분리(독립 SDP/ICE)
# - Socket.IO를 통해 시그널링 서버와 통신
# - 각 sender는 자체 창(sink가 생성)으로 출력 (풀스크린)
# - 다중 sender 수신: 모두 수신/디코드하지만, 화면 출력은 활성 sender만 PLAYING
# ================================================================

import sys, signal, threading, os, platform
import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstWebRTC', '1.0')
gi.require_version('GstSdp', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst, GstWebRTC, GstSdp, GLib, GstVideo
import socketio

# ---------- 설정 ----------
SIGNALING_URL = "http://localhost:3001"
ROOM_PASSWORD  = "1"
RECEIVER_NAME  = "Receiver-1"

# GStreamer 초기화
Gst.init(None)












# ---------- GStreamer 유틸리티 함수 ----------
# name에 해당하는 GStreamer 요소(webrtcbin, rtph264depay ...) 생성
def _make(name):
    return Gst.ElementFactory.make(name) if name else None

# 여러 요소 중 가장 먼저 사용 가능한 요소 찾아 생성(HW decoder -> SW decoder)
def _first_available(*names):
    for n in names:
        if Gst.ElementFactory.find(n):
            e = Gst.ElementFactory.make(n)
            if e:
                return e
    return None

# 🧩 특정 속성 안전하게 설정
def _set_props_if_supported(element, **kwargs):
    if not element:
        return
    klass = element.__class__
    for k, v in kwargs.items():
        try:
            # hasattr로는 GObject 속성 확인이 애매하므로 set_property 시도/예외 무시
            element.set_property(k, v)
        except Exception:
            pass



# ---------- OS/플랫폼별 하드웨어 디코딩 + Sink 선택 ---------- 
# 현재 플랫폼(OS)에 맞는 하드웨어 디코더/변환기/비디오 출력 sink를 선택
def get_decoder_and_sink(video_widget=None):
    sysname = platform.system().lower() # 현재 OS 이름 (linux/windows/darwin)
    decoder, conv, sink = None, None, None

    if "linux" in sysname:
        if os.path.isfile("/etc/nv_tegra_release"): # Jetson 계열
            decoder = _first_available("nvv4l2decoder", "omxh264dec")
            conv    = _first_available("nvvidconv", "videoconvert")
            sink    = _first_available("glimagesink", "nveglglessink", "autovideosink")
        else: # 일반 Linux
            decoder = _first_available("vaapih264dec", "v4l2h264dec", "avdec_h264")
            conv    = _first_available("videoconvert")
            sink    = _first_available("glimagesink", "xvimagesink", "autovideosink")
    elif "windows" in sysname:
        decoder = _first_available("d3d11h264dec", "avdec_h264")
        conv    = _first_available("d3d11convert", "videoconvert")
        sink    = _first_available("d3d11videosink", "autovideosink")
    elif "darwin" in sysname: # macOS
        decoder = _first_available("vtdec", "avdec_h264")
        conv    = _first_available("videoconvert")
        sink    = _first_available("avfvideosink", "glimagesink", "autovideosink")
    else:
        decoder = _first_available("avdec_h264")
        conv    = _first_available("videoconvert")
        sink    = _first_available("autovideosink")

    # 🧩 가능한 범위에서 풀스크린/비율 유지 속성 적용 (싱크별 지원 상이)
    _set_props_if_supported(sink, force_aspect_ratio=True)      # 일부 싱크: force-aspect-ratio
    _set_props_if_supported(sink, fullscreen=True)              # 일부 싱크: fullscreen
    _set_props_if_supported(sink, handle_events=True)           # 자체 창 이벤트 처리(플랫폼별)

    return decoder, conv, sink

















# ================================================================
# PeerReceiver: "송신자 1명"을 담당하는 독립 webrtcbin + pipeline
# pipeline: webrtcbin -> depay -> parse -> decode -> convert -> sink
# ================================================================
class PeerReceiver:
    
    # PeerReceiver 클래스 생성자
    def __init__(self, sio, sender_id, sender_name):
        self.sio = sio # Socket.IO 클라이언트 인스턴스(시그널링 서버 통신용)
        self.sender_id = sender_id
        self.sender_name = sender_name

        # 상태 변수: 현재 연결 및 협상 상태 추적
        self._gst_playing = False # GStreamer 파이프라인 PLAYING 상태
        self._negotiating = False # 현재 SDP 협상 상태
        self._sender_ready = False # sender 미디어 전송 시작 준비 완료 여부
        self._pending_offer_sdp = None # 아직 전송되지 않은 로컬 offer sdp 텍스트
        self._transceivers = [] # WebRTC 트랜시버 객체 리스트(송수신 방향 제어)
        self._transceivers_added = False # 트랜시버 파이프라인 추가 여부

        # 표시 제어 변수: 수신된 비디오 스트림의 화면 출력(렌더링) 관리
        self._display_bin = None # 🧩 표시 브랜치의 최종 요소(fpsdisplaysink)
        self._visible = False # 현재 송신자 비디오 화면 표시 여부

        # 각 sender별 "독립" 파이프라인/요소 구성
        self.pipeline = Gst.Pipeline.new(f"webrtc-pipeline-{sender_id}") # GStreamer 파이프라인 생성
        self.webrtc = _make("webrtcbin") # webrtcbin 생성 
        if not self.webrtc:
            raise RuntimeError("webrtcbin 생성 실패")

        self.pipeline.add(self.webrtc) # 생성된 webrtcbin 파이프라인에 추가
        self.webrtc.set_property('stun-server', 'stun://stun.l.google.com:19302') # STUN서버 webrtcbin에 설정(NAT 통과 목적)

        # webrtcbin 시그널 연결: WebRTC 연결 과정 ~ 여러 이벤트에 반응하도록 콜백 함수 연결
        self.webrtc.connect('notify::ice-connection-state', self._on_ice_conn_change) # ICE 연결 상태 변경 시 호출
        self.webrtc.connect('on-ice-candidate', self.on_ice_candidate) # ICE 후보가 생성될 때 호출(상대방에게 전송)
        self.webrtc.connect('pad-added', self.on_incoming_stream) # 원격 피어로부터 미디어 스트림(pad)이 추가될 때 호출
        self.webrtc.connect('on-negotiation-needed', self._on_negotiation_needed) # SDP 협상이 필요할 때 호출 (offer 생성 시작)

        # Bus: 파이프라인에서 발생하는 메시지를 수신
        bus = self.pipeline.get_bus()
        bus.add_signal_watch() # 메시지를 시그널 형태로 받기
        bus.connect("message::state-changed", self._on_state_changed) # 파이프라인의 상태가 변경될 때 호출
        bus.connect("message::error", self._on_error) # 파이프라인에서 오류가 발생할 때 호출



    # ---------------------- GStreamer 이벤트 ----------------------
    # ICE 연결 상태 변경 시 호출되는 콜백
    def _on_ice_conn_change(self, obj, pspec):
        try:
            state = int(self.webrtc.get_property('ice-connection-state'))
        except Exception as e:
            print(f"[RTC][{self.sender_name}] ICE state read error:", e); return
        print(f"[RTC][{self.sender_name}] ICE state: {state}") # 현재 ICE 연결 상태를 출력

    # 수신 전용(RECVONLY) 트랜시버를 webrtcbin에 추가
    def _add_recv(self, caps_str):
        t = self.webrtc.emit(
            'add-transceiver',
            GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY, # 수신 전용 방향 설정
            Gst.Caps.from_string(caps_str)
        )
        self._transceivers.append(t) # 생성된 트랜시버 객체를 리스트에 추가
        print(f'[RTC][{self.sender_name}] transceiver added:', bool(t))

    # 트랜시버가 아직 추가되지 않았다면 추가하도록 보장
    def _ensure_transceivers(self):
        if self._transceivers_added:
            return
        # H.264 비디오 수신을 위한 트랜시버를 추가
        self._add_recv("application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,"
                       "payload=102,packetization-mode=(string)1,profile-level-id=(string)42e01f")
        self._transceivers_added = True # 트랜시버 추가 표시

    # GStreamer 파이프라인을 PLAYING 상태로 전환하여 미디어 처리를 시작
    def start(self):
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        print(f"[GST][{self.sender_name}] set_state ->", ret.value_nick) # 상태 전환 결과를 출력(PLAYING, PAUSED)

    # GStreamer 파이프라인을 NULL 상태로 전환하여 미디어 처리를 중지하고 자원을 해제
    def stop(self):
        try: self.pipeline.set_state(Gst.State.NULL)
        except: pass

    # 파이프라인의 상태 변경 메시지를 처리하는 콜백 함수
    def _on_state_changed(self, bus, msg):
        if msg.src is self.pipeline: # 메시지 소스가 현재 파이프라인인지 확인
            _, new, _ = msg.parse_state_changed() # 변경된 상태 값을 파싱
            if new == Gst.State.PLAYING and not self._gst_playing: # 파이프라인이 PLAYING 상태로 전환, PLAYING 아니었다면 상태 업데이트
                self._gst_playing = True
                print(f"[GST][{self.sender_name}] pipeline → PLAYING")
                self._ensure_transceivers() # 트랜시버가 추가되었는지 확인하고 필요하면 추가
                if self._sender_ready and not self._pending_offer_sdp: # 송신자가 준비되었고, 아직 Offer를 보내지 않았다면 Offer 생성 시작
                    GLib.idle_add(lambda: self._maybe_create_offer())

    # 파이프라인에서 오류 메시지가 발생했을 때 처리하는 콜백 함수
    def _on_error(self, bus, msg):
        err, dbg = msg.parse_error() # 오류 정보와 디버그 메시지를 파싱
        print(f"[GST][{self.sender_name}][ERROR] {err.message} (debug: {dbg})")

    # ---------------------- Negotiation ----------------------
    # webrtcbin이 SDP 협상이 필요하다고 알릴 때 호출되는 콜백 함수
    def _on_negotiation_needed(self, element, *args):
        if self._negotiating:
            print(f"[RTC][{self.sender_name}] skip offer: already negotiating"); return
        GLib.idle_add(lambda: self._maybe_create_offer()) # GLib 메인 루프에 Offer 생성 시작 함수를 스케줄링

    # Offer 생성을 시작할지 여부를 결정하는 함수
    def _maybe_create_offer(self):
        if self._negotiating: return False
        self._negotiating = True # 협상 시작 플래그를 True로 설정
        def _do():
            # Offer 생성을 위한 Promise 객체를 생성하고, 콜백 함수를 연결
            p = Gst.Promise.new_with_change_func(self._on_offer_created, self.webrtc)
            self.webrtc.emit('create-offer', None, p) # webrtcbin에 'create-offer' 시그널을 발생시켜 Offer 생성을 요청
            return False # GLib.idle_add의 콜백은 항상 False를 반환하여 한 번만 실행
        GLib.idle_add(_do) # Offer 생성 로직을 GLib 메인 루프에 스케줄링
        return False

    # Offer 생성이 완료되었을 때 호출되는 콜백 함수
    def _on_offer_created(self, promise, element):
        reply = promise.get_reply() # Promise로부터 응답을 가져오기
        if not reply: self._negotiating=False; return # 응답 없으면 협상 플래그를 초기화하고 반환
        offer = reply.get_value('offer') # 응답에서 Offer SDP 메시지 가져오기
        if not offer: self._negotiating=False; return # Offer가 없으면 협상 플래그 초기화 및 반환
        self._pending_offer_sdp = offer.sdp.as_text() # Offer SDP를 텍스트 형태로 저장
        p2 = Gst.Promise.new_with_change_func(self._on_local_desc_set, element) # 로컬 Offer를 webrtcbin에 설정하기 위한 Promise 객체를 생성하고 콜백 함수를 연결
        element.emit('set-local-description', offer, p2) #webrtcbin에 'set-local-description' 시그널을 발생시켜 로컬 Offer를 설정

    def _on_local_desc_set(self, promise, element):
        # 로컬 Offer SDP가 webrtcbin에 성공적으로 설정되었을 때 호출되는 콜백 함수
        print(f"[RTC][{self.sender_name}] Local description set (offer)")
        # GStreamer 파이프라인이 PLAYING 상태이고 sender ID가 있다면 Offer를 전송
        if self._gst_playing and self.sender_id:
            self._send_offer()
        self._negotiating = False # 협상 플래그를 초기화

    # 저장된 Offer SDP를 시그널링 서버를 통해 상대방에게 전송하는 함수
    def _send_offer(self):
        if not self._pending_offer_sdp:
            return
        self.sio.emit('signal', { # Socket.IO를 통해 'signal' 이벤트를 전송
            'to': self.sender_id, # 수신자 ID (상대방 sender의 ID)
            'from': self.sio.sid, # 발신자 ID (나의 Socket.IO 세션 ID)
            'type': 'offer', # 시그널 타입: offer
            'payload': {'type': 'offer', 'sdp': self._pending_offer_sdp} # Offer SDP 데이터
        })
        print(f'[SIO][{self.sender_name}] offer 전송 → {self.sender_id}')

    # 원격 Answer SDP를 수신하여 webrtcbin에 적용하는 함수
    def apply_remote_answer(self, sdp_text: str): # sdp_text: 원격 Answer SDP 문자열
        ok, sdpmsg = GstSdp.SDPMessage.new() # 새로운 SDPMessage 객체를 생성
        if ok != GstSdp.SDPResult.OK: return False # 생성 실패 시 False 반환
        GstSdp.sdp_message_parse_buffer(sdp_text.encode('utf-8'), sdpmsg) # SDP 텍스트를 파싱하여 SDPMessage 객체에 채움
        answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg) # Answer 타입의 WebRTCSessionDescription 객체를 생성
        self.webrtc.emit('set-remote-description', answer, None) # webrtcbin에 'set-remote-description' 시그널 발생시켜 원격 Answer를 설정, Promise는 필요 없으므로 None
        print(f"[RTC][{self.sender_name}] Remote ANSWER 적용 완료")
        return False # GLib.idle_add 콜백용 반환 값

    # ICE 후보(Candidate)가 생성될 때 호출되는 콜백 함수
    # element: webrtcbin 요소
    # mlineindex: 미디어 라인 인덱스
    # candidate: ICE 후보 문자열
    def on_ice_candidate(self, element, mlineindex, candidate):
        self.sio.emit('signal', { # Socket.IO를 통해 'signal' 이벤트를 전송
            'to': self.sender_id, # 수신자 ID (상대방 sender의 ID)
            'from': self.sio.sid, # 발신자 ID (나의 Socket.IO 세션 ID)
            'type': 'candidate', # 시그널 타입 'candidate'
            'payload': {'candidate': candidate, # ICE 후보 데이터
                        'sdpMid': f"video{mlineindex}", # 미디어 라인 ID (SDP에서 해당 미디어 식별)
                        'sdpMLineIndex': int(mlineindex)} # 미디어 라인 인덱스
        })

    # ---------- Media 수신 및 렌더링 메서드들 ----------
    # webrtcbin에서 원격 미디어 스트림(pad)이 추가될 때 호출되는 콜백 함수
    def on_incoming_stream(self, webrtc, pad):
        caps = pad.get_current_caps().to_string() # 현재 pad의 캡스(Caps) 정보를 문자열로 가져오기
        print("Streaming Start!")
        # print(f"[RTC][{self.sender_name}] pad caps:", caps)

        if caps.startswith("application/x-rtp"): # 캡스가 RTP 비디오 스트림인지 확인
            # H.264 RTP 스트림을 위한 디코딩 파이프라인 요소들 생성
            depay = _make("rtph264depay") # RTP 패킷에서 H.264 페이로드를 추출
            parse = _make("h264parse") # H.264 비트스트림을 파싱
            decoder, conv, sink = get_decoder_and_sink(None) # 플랫폼에 맞는 디코더, 컨버터, 비디오 싱크를 가져옴(PyQt-None)

            # 🧩 큐(queue)와 FPS 표시 싱크를 사용
            q = _make("queue") # 🧩 요소 간 버퍼링 및 디커플링을 위한 큐
            fpssink = _make("fpsdisplaysink") # FPS를 측정하고 비디오 싱크에 연결하여 출력하는 요소
            if fpssink: # fpsdisplaysink가 성공적으로 생성
                fpssink.set_property("signal-fps-measurements", True) # FPS 측정 시그널을 활성화
                fpssink.set_property("text-overlay", False) # FPS 텍스트 오버레이를 비활성화
                if sink:
                    fpssink.set_property("video-sink", sink) # 실제 비디오 렌더링을 담당할 싱크 엘리먼트를 연결
                # FPS 측정값 시그널에 콜백 함수를 연결하여 로그를 출력
                fpssink.connect("fps-measurements",
                    lambda el, fps, drop, avg:
                        print(f"[STATS][{self.sender_name}] FPS={fps:.2f}, drop={drop:.2f}, avg={avg:.2f}")
                )

            # 필요한 모든 요소가 성공적으로 생성되었는지 확인
            if not all([depay, parse, decoder, conv, sink, fpssink, q]):
                print(f"[RTC][{self.sender_name}] 요소 부족으로 링크 실패"); return

            # 생성된 요소들을 파이프라인에 추가하고 상태를 부모 파이프라인과 동기화
            for e in (depay, parse, decoder, conv, q, fpssink):
                self.pipeline.add(e); e.sync_state_with_parent()

            # 원격 스트림(pad)을 디코딩 파이프라인의 시작점(depay의 싱크 패드)에 연결
            if pad.link(depay.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
                print(f"[RTC][{self.sender_name}] pad link 실패"); return

            # 디코딩 파이프라인의 나머지 요소들을 순서대로 연결
            depay.link(parse)
            parse.link(decoder)
            decoder.link(conv)
            conv.link(q)
            q.link(fpssink)

            # 🧩 표시 상태는 '현재 의도(self._visible)'를 그대로 반영
            prev = self._visible # 현재 스트림이 보여지고 싶은 상태
            self._display_bin = fpssink # 표시 제어를 위한 최종 요소를 fpssink로 설정
            self.set_visible(prev) # 이전 가시성 설정에 따라 현재 스트림의 표시 상태를 설정

            print(f"[OK][{self.sender_name}] Incoming video linked → {decoder.name}")

    # 🧩 이 송신자의 비디오 스트림을 화면에 표시(PLAYING)하거나 일시 중지(PAUSED)하는 함수
    # on: True면 표시, False면 일시 중지
    def set_visible(self, on: bool):
        self._visible = bool(on) # 내부 가시성 상태를 업데이트
        if not self._display_bin: # 표시할 요소가 아직 설정되지 않았다면 반환
            return
        try:
            self._display_bin.set_state(Gst.State.PLAYING if on else Gst.State.PAUSED)
        except Exception as e:
            print(f"[GST][{self.sender_name}] set_visible error:", e)

    # ---------- Stats (통계) 메서드들 ----------
    def log_receiver_stats(self):
        # WebRTC 수신 통계를 주기적으로 로그로 출력하는 함수
        def on_stats(promise, element):
            reply = promise.get_reply() # Promise로부터 통계 응답을 가져오기
            if not reply: return
            stats = reply.get_value("stats") # 응답에서 통계 데이터를 가져오기
            for k, v in stats.items(): # 통계 항목들을 순회
                if "inbound-rtp" in k and v.get("mediaType") == "video": # 수신 RTP 비디오 스트림에 대한 통계인 경우 상세 정보를 출력
                    print(f"[STATS][{self.sender_name}] recv_bytes={v.get('bytesReceived')}, "
                          f"framesDecoded={v.get('framesDecoded')}, "
                          f"jitter={v.get('jitter')}, "
                          f"packetsLost={v.get('packetsLost')}")
        # webrtcbin에 'get-stats' 시그널을 발생시켜 통계 데이터를 요청
        p = Gst.Promise.new_with_change_func(on_stats, self.webrtc)
        self.webrtc.emit("get-stats", None, p)
        # 2초마다 log_receiver_stats 함수를 다시 호출하도록 GLib 메인 루프에 스케줄링
        GLib.timeout_add_seconds(2, lambda: (self.log_receiver_stats() or True))




























# ================================================================
# 🧩 MultiReceiverManager: 여러 sender를 관리(소켓 공유) + 활성 표시 제어
# ================================================================
class MultiReceiverManager:
    def __init__(self):
        self.sio = socketio.Client(logger=False, engineio_logger=False)
        self.peers = {}            # sender_id -> PeerReceiver
        # 현재 화면에 비디오를 표시하고 있는 송신자의 ID를 저장
        self.active_sender_id = None  # 🧩 현재 화면에 띄울 sender

        # Socket.IO 이벤트와 핸들러 함수들을 연결
        self._bind_socket_events() 

    def start(self):
        # Socket.IO 연결을 별도의 스레드에서 시작하여 메인 루프를 방해하지 않도록 설정
        threading.Thread(target=self._sio_connect, daemon=True).start()

    def stop(self):
        # 프로그램 종료 시 모든 연결과 자원을 정리
        try:
            # 모든 PeerReceiver 인스턴스를 순회하며 stop() 메서드를 호출
            for pid, peer in list(self.peers.items()):
                peer.stop()
        except: pass
        try:
            if self.sio.connected:
                self.sio.disconnect() # Socket.IO 클라이언트 연결 끊기
        except: pass

    # 🧩 활성 표시 전환(키보드 이벤트는 아직 연동하지 않음)
    def _set_active_sender(self, sid):
        self.active_sender_id = sid
        for pid, peer in self.peers.items(): # 현재 순회 중인 PeerReceiver의 ID가 활성 송신자 ID와 같으면 True, 아니면 False
            peer.set_visible(pid == sid)
        if sid:
            print(f"[VIEW] now showing sender: {sid}")

    # ---------- Socket.IO 이벤트 핸들러 메서드들 ----------
    # Socket.IO 서버에 연결하는 함수(스레드에서 실행) 
    def _sio_connect(self):
        try:
            self.sio.connect(SIGNALING_URL, transports=['websocket']) # 시그널링 서버 URL과 전송 프로토콜을 설정하여 연결을 시도
            self.sio.wait() # 연결이 끊길 때까지 대기
        except Exception as e:
            print("[SIO] connect error:", e)

    # Socket.IO 클라이언트 객체에 이벤트 핸들러 함수들을 연결   
    def _bind_socket_events(self):
        @self.sio.event
        # 서버에 성공적으로 연결되었을 때 호출되는 핸들러
        def connect():
            print("[SIO] connected:", self.sio.sid)
            # 서버의 특정 방에 'receiver' 역할로 참여를 요청
            self.sio.emit('join-room',
                          {'role':'receiver','password':ROOM_PASSWORD,'name':RECEIVER_NAME},
                          callback=lambda ack: print("[SIO] join-room ack:", ack))

        @self.sio.on('sender-list')
        # 방에 있는 송신자들의 리스트를 받았을 때 호출되는 핸들러
        def on_sender_list(sender_arr):
            print("[SIO] sender-list:", sender_arr)
            if not sender_arr: # 리스트가 비어있으면 대기 상태
                print("[SIO] sender 없음. 대기."); return

            # 모든 sender에 대해 구독 요청 + PeerReceiver 생성, 받은 sender 리스트 순회
            for s in sender_arr:
                sid = s.get('id')
                name = s.get('name', sid)
                if sid in self.peers:
                    continue # 이미 처리된 sender 건너뛰기

                # 해당 sedner를 위한 새로운 PeerReceiver 인스턴스를 생성
                peer = PeerReceiver(self.sio, sid, name)
                self.peers[sid] = peer # 딕셔너리에 추가
                peer.start() # GStreamer 파이프라인을 시작 상태로 만듬

                # 🧩 메인 루프에서 PeerReceiver의 트랜시버를 확인하고 Offer를 생성하도록 스케줄링
                GLib.idle_add(lambda: (peer._ensure_transceivers(), peer._maybe_create_offer()))
                # 첫 번째로 발견된 송신자를 즉시 화면에 표시하도록 설정
                if self.active_sender_id is None:
                    self._set_active_sender(sid)

                # 송신자에게 미디어 공유를 요청하는 메시지 보냄
                self.sio.emit('share-request', {'to': sid})
                print(f"[SIO] share-request → {sid} ({name})")

        @self.sio.on('sender-share-started')
        # 송신자가 미디어 공유를 시작했다는 알림을 받았을 때 호출
        def on_sender_share_started(data):
            sid = data.get('id') or data.get('from')
            if not sid or sid not in self.peers:
                print("[SIO] share-started from unknown sender:", data); return
            peer = self.peers[sid]
            peer._sender_ready = True # 송신자가 준비 표시
            if peer._gst_playing:
                GLib.idle_add(lambda: peer._maybe_create_offer()) # Offer 생성을 다시 시도
            # 첫 sender면 즉시 활성 표시로 전환
            if self.active_sender_id is None:
                self._set_active_sender(sid)
            print(f"[SIO] sender-share-started: {peer.sender_name}")

        @self.sio.on('signal')
        # SDP 또는 ICE 후보 시그널링 메시지를 받았을 때 호출
        def on_signal(data):
            typ, frm, payload = data.get('type'), data.get('from'), data.get('payload')
            print("[SIO] signal recv:", typ, "from", frm)

            # 알 수 없는 송신자로부터의 메시지는 무시
            if not frm or frm not in self.peers:
                print("[SIO] unknown sender in signal:", frm)
                return
            peer = self.peers[frm] # 해당 송신자의 PeerReceiver 객체 가져오기

            # 'answer' 메시지인 경우, SDP 텍스트를 파싱하여 PeerReceiver에 적용하도록 스케줄링
            if typ == 'answer' and payload:
                sdp_text = payload['sdp'] if isinstance(payload, dict) else payload
                GLib.idle_add(peer.apply_remote_answer, sdp_text)

            # 'candidate' 메시지인 경우, ICE 후보를 PeerReceiver에 추가하도록 스케줄링
            elif typ == 'candidate' and payload:
                cand  = payload.get('candidate')
                mline = int(payload.get('sdpMLineIndex') or 0)
                if cand is not None:
                    GLib.idle_add(peer.webrtc.emit, 'add-ice-candidate', mline, cand)

        @self.sio.on('sender-share-stopped')
        # 송신자가 미디어 공유를 중단했을 때 호출
        def on_sender_share_stopped(data):
            sid = data.get('id') or data.get('from')
            if sid in self.peers:
                print(f"[SIO] sender-share-stopped: {sid}")
                # 현재 비디오를 표시하고 있는 송신자인지 확인
                was_active = (sid == self.active_sender_id)
                # 딕셔너리에서 해당 PeerReceiver를 제거하고 정지
                peer = self.peers.pop(sid)
                peer.stop()
                # 활성 sender가 빠졌으면 다른 sender로 전환
                if was_active:
                    next_sid = next(iter(self.peers.keys()), None) # peers 딕셔너리에서 다음 사용 가능한 sender ID 가져오기
                    self._set_active_sender(next_sid) # 새 sender를 활성 상태로 설정
















# ---------- 프로그램 실행 (GLib 메인 루프) ----------
if __name__ == "__main__":
    # 이 코드는 스크립트가 직접 실행될 때만 작동
    # PyQt 없이 GLib 메인루프만 사용
    manager = MultiReceiverManager() # MultiReceiverManager 인스턴스를 생성
    manager.start() # 관리자 객체를 시작

    loop = GLib.MainLoop() # GStreamer의 이벤트 처리를 위한 GLib 메인 루프를 생성

    def _quit(*_): # Ctrl+C와 같은 종료 시그널을 받았을 때 실행되는 콜백 함수
        try: manager.stop() # 관리자 객체를 정지시켜 모든 파이프라인과 연결을 정리
        except: pass
        loop.quit() # GLib 메인 루프를 종료

    # 시스템 종료(SIGTERM) 및 키보드 인터럽트(SIGINT, Ctrl+C) 시그널에 _quit 함수 연결
    signal.signal(signal.SIGINT, _quit)
    signal.signal(signal.SIGTERM, _quit)

    print("[MAIN] Running GLib MainLoop. Press Ctrl+C to quit.")

    # 메인 루프를 실행하여 모든 GStreamer 및 Socket.IO 이벤트를 처리
    loop.run()