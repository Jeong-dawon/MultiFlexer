# fps 추가하기 전 버전

# ======================================
# WebRTC Receiver (PyQt + GStreamer)
# ======================================
# - GStreamer 의 webrtcbin을 사용해 실시간 화면을 수신하는 코드
# - PyQt 위젯 안에 영상을 임베딩하여 표시
# - Socket.IO를 통해 시그널링 서버와 통신
# ======================================

import sys, signal, threading, os, platform # 시스템, 신호 처리, 쓰레딩, os/플랫폼 관련 모듈
import gi # GObject Introspection -> GStreamer 파이썬 바인딩

# GStreamer 관련 모듈 버전 설정
gi.require_version('Gst', '1.0') # GStreamer 코어 플러그인
gi.require_version('GstWebRTC', '1.0') # WebRTC 플러그인
gi.require_version('GstSdp', '1.0') # SDP 플러그인 
gi.require_version('GstVideo', '1.0') # 비디오 플러그인

# GStreamer/PyQt 모듈 불러오기
from gi.repository import Gst, GstWebRTC, GstSdp, GLib, GstVideo # 필요한 GStreamer 모듈 불러오기
import socketio # Socket.IO 클라이언트(WebRTC 시그널링용)
from PyQt5 import QtWidgets, QtCore # GUI 출력용 PyQt5 모듈

# ---------- 설정 ----------
SIGNALING_URL = "http://localhost:3001" # 시그널링 서버 주소
ROOM_PASSWORD  = "1" # 방 비밀번호
RECEIVER_NAME  = "Receiver-1" # 이 클라이언트 이름
#PREFERRED_SENDER_ID = None # 특정 송신자 ID를 강제 선택할 경우 (현재 None → 사용 안 함)
 
# GStreamer 초기화
Gst.init(None) 











# ---------- 유틸 함수 ----------
# 주어진 name의 GStreamer element를 생성
def _make(name):
    return Gst.ElementFactory.make(name) if name else None

# 후보 element 이름 중에서 사용 가능한 첫 번째 element를 생성하여 반환
def _first_available(*names):
    for n in names:
        if Gst.ElementFactory.find(n): # element 존재 여부 확인
            e = Gst.ElementFactory.make(n) 
            if e: # 생성 성공한 첫 번째 element 반환
                return e
    return None





# ---------- OS/플랫폼별 하드웨어 디코딩 + Sink 선택 ---------- 
# 현재 플랫폼(OS)에 맞는 하드웨어 디코더/변환기/비디오 출력 sink를 선택
def get_decoder_and_sink(video_widget):
    sysname = platform.system().lower()  
    decoder, conv, sink = None, None, None

    if "linux" in sysname:
        if os.path.isfile("/etc/nv_tegra_release"):
            # Jetson 계열
            decoder = _first_available("nvv4l2decoder", "omxh264dec")
            conv    = _first_available("nvvidconv", "videoconvert")
            sink    = _first_available("glimagesink", "nveglglessink")
        else:
            # 일반 Linux
            decoder = _first_available("vaapih264dec", "v4l2h264dec", "avdec_h264")
            conv    = _first_available("videoconvert")
            sink    = _first_available("glimagesink", "xvimagesink")

    elif "windows" in sysname: 
        decoder = _first_available("d3d11h264dec", "avdec_h264")
        conv    = _first_available("d3d11convert", "videoconvert")
        sink    = _first_available("d3d11videosink", "autovideosink")

    elif "darwin" in sysname:  # macOS
        decoder = _first_available("vtdec", "avdec_h264")
        conv    = _first_available("videoconvert")
        sink    = _first_available("glimagesink")

    else: # etc
        decoder = _first_available("avdec_h264")
        conv    = _first_available("videoconvert")
        sink    = _first_available("autovideosink")

    # PyQt5 위젯 안에서만 출력되도록 sink 윈도우 핸들 연결
    if sink and video_widget:
        xid = int(video_widget.winId()) # PyQt 위젯의 윈도우 핸들 가져오기
        sink.set_window_handle(xid) # GStreamer sink 출력창을 PyQt 위젯에 박아넣음

    return decoder, conv, sink














# ---------- Receiver 클래스 ----------
class Receiver:
    """WebRTC 수신기: webrtcbin + (depay→parse→decode) + PyQt 임베드"""

    def __init__(self, video_widget=None):
        # 상태 변수들
        self._negotiating = False                  # 협상 중 여부
        self._gst_playing = False                  # GStreamer 실행 여부
        self._pending_offer_sdp = None             # 보낼 offer SDP 저장
        self._transceivers = []                    # 등록된 RTP 트랜시버
        self._transceivers_added = False           # 트랜시버 추가 여부
        self._sender_ready = False                 # 송신자가 준비됐는지 여부
        self._pending_offer_after_playing = False  # 플레이 이후 offer 대기 여부

        self.video_widget = video_widget # PyQt 위젯(비디오 출력용)

        # GStreamer 파이프라인 생성
        self.pipeline = Gst.Pipeline.new("webrtc-pipeline")
        self.webrtc = _make("webrtcbin") # WebRTC 핵심 element
        if not self.webrtc:
            raise RuntimeError("webrtcbin 생성 실패") # 필수 요소 없으면 종료
        
        self.pipeline.add(self.webrtc) # 파이프라인에 webrtcbin 추가

        # STUN 서버(NAT traversal) 
        self.webrtc.set_property('stun-server', 'stun://stun.l.google.com:19302')

        # webrtcbin 시그널 연결
        self.webrtc.connect('notify::ice-connection-state', self._on_ice_conn_change)
        self.webrtc.connect('on-ice-candidate', self.on_ice_candidate)
        self.webrtc.connect('pad-added', self.on_incoming_stream)
        self.webrtc.connect('on-negotiation-needed', self._on_negotiation_needed)

        # GStreamer Bus (상태/에러 이벤트 핸들링)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::state-changed", self._on_state_changed)
        bus.connect("message::error", self._on_error)

        # Socket.IO 클라이언트 생성 및 이벤트 바인딩
        self.sio = socketio.Client(logger=False, engineio_logger=False)
        self._bind_socket_events()

    # ------------------- GStreamer 이벤트 -------------------
    # ICE 연결 상태 변화 시 출력
    def _on_ice_conn_change(self, obj, pspec):
        try:
            state = int(self.webrtc.get_property('ice-connection-state'))
        except Exception as e:
            print("[RTC] ICE state read error:", e); return
        print(f"[RTC] ICE state: {state}")

    # RECVONLY 트랜시버 추가 (수신 전용)
    def _add_recv(self, caps_str):
        t = self.webrtc.emit(
            'add-transceiver',
            GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY,
            Gst.Caps.from_string(caps_str)
        )
        self._transceivers.append(t)
        print('[RTC] transceiver added:', caps_str, '->', bool(t))

    # 최소 1개의 비디오 트랜시버 보장
    def _ensure_transceivers(self):
        if self._transceivers_added:
            return
        self._add_recv("application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,"
                       "payload=102,packetization-mode=(string)1,profile-level-id=(string)42e01f")
        self._transceivers_added = True

    # 파이프라인 실행 및 Socket.IO 연결
    def start(self):
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        print("[GST] set_state ->", ret.value_nick)
        threading.Thread(target=self._sio_connect, daemon=True).start()

    # 파이프라인 및 소켓 종료
    def stop(self):
        try:
            if self.sio.connected:
                self.sio.disconnect()
        except: pass
        try:
            self.pipeline.set_state(Gst.State.NULL)
        except: pass

    # 파이프라인 상태 변화 감지
    def _on_state_changed(self, bus, msg):
        if msg.src is self.pipeline:
            _, new, _ = msg.parse_state_changed()
            if new == Gst.State.PLAYING and not self._gst_playing:
                self._gst_playing = True
                print("[GST] pipeline → PLAYING")
                self._ensure_transceivers()
                if self._pending_offer_after_playing and self._sender_ready:
                    self._pending_offer_after_playing = False
                    GLib.idle_add(lambda: self._maybe_create_offer())

    # GStreamer 에러 메시지 출력
    def _on_error(self, bus, msg):
        err, dbg = msg.parse_error()
        print(f"[GST][ERROR] {err.message} (debug: {dbg})")








    # ------------------- Socket.IO -------------------
    # 시그널링 서버 연결
    def _sio_connect(self):
        try:
            self.sio.connect(SIGNALING_URL, transports=['websocket'])
            self.sio.wait()
        except Exception as e:
            print("[SIO] connect error:", e)

    # Socket.IO 이벤트 핸들러 정의
    def _bind_socket_events(self):
        @self.sio.event
        def connect():
            print("[SIO] connected:", self.sio.sid)
            self.sio.emit('join-room',
                          {'role':'receiver','password':ROOM_PASSWORD,'name':RECEIVER_NAME},
                          callback=lambda ack: print("[SIO] join-room ack:", ack))

        @self.sio.on('sender-list')
        # sender 리스트 수신
        def on_sender_list(sender_arr):
            print("[SIO] sender-list:", sender_arr)
            if not sender_arr:
                print("[SIO] sender 없음. 대기."); return
            chosen = sender_arr[0] # 무조건 첫 번째 송신자 선택
            self.target_sender_id = chosen['id']
            print(f"[SIO] target sender = {self.target_sender_id} ({chosen.get('name')})")
            self.sio.emit('share-request', {'to': self.target_sender_id})
            if self._gst_playing and not self._pending_offer_sdp:
                GLib.idle_add(lambda: self._maybe_create_offer())

        # 송신자 화면 공유 시작 알림
        @self.sio.on('sender-share-started')
        def on_sender_share_started(data):
            print("[SIO] sender-share-started:", data)
            self._sender_ready = True
            if self._gst_playing:
                GLib.idle_add(lambda: self._maybe_create_offer())
            else:
                self._pending_offer_after_playing = True

        # Offer/Answer/ICE candidate 수신
        @self.sio.on('signal')
        def on_signal(data):
            typ, frm, payload = data.get('type'), data.get('from'), data.get('payload')
            print("[SIO] signal recv:", typ, "from", frm)
            if typ == 'answer' and payload:
                sdp_text = payload['sdp'] if isinstance(payload, dict) else payload
                GLib.idle_add(self._apply_remote_answer, sdp_text)
            elif typ == 'candidate' and payload:
                cand  = payload.get('candidate')
                mline = int(payload.get('sdpMLineIndex') or 0)
                if cand is not None:
                    GLib.idle_add(self.webrtc.emit, 'add-ice-candidate', mline, cand)








    # ------------------- Negotiation -------------------
    # webrtcbin → 협상 필요 이벤트 발생
    def _on_negotiation_needed(self, element, *args):
        if not self._gst_playing or not self._sender_ready:
            print("[RTC] negotiation-needed (ignored)")
            return
        if self._negotiating:
            print("[RTC] skip offer: already negotiating"); return
        GLib.idle_add(lambda: self._maybe_create_offer())

    # Offer 생성 시도
    def _maybe_create_offer(self):
        if self._negotiating: return False
        self._negotiating = True
        def _do():
            p = Gst.Promise.new_with_change_func(self._on_offer_created, self.webrtc)
            self.webrtc.emit('create-offer', None, p)
            return False
        GLib.idle_add(_do)
        return False

    # Offer 생성 완료 시
    def _on_offer_created(self, promise, element):
        reply = promise.get_reply()
        if not reply: self._negotiating=False; return
        offer = reply.get_value('offer')
        if not offer: self._negotiating=False; return
        self._pending_offer_sdp = offer.sdp.as_text()
        p2 = Gst.Promise.new_with_change_func(self._on_local_desc_set, element)
        element.emit('set-local-description', offer, p2)

    # 로컬 SDP 설정 완료
    def _on_local_desc_set(self, promise, element):
        print("[RTC] Local description set (offer)")
        if hasattr(self, 'target_sender_id') and self._gst_playing:
            self._send_offer()
        self._negotiating = False

    # Offer를 시그널링 서버로 전송
    def _send_offer(self):
        if not self._pending_offer_sdp or not hasattr(self, 'target_sender_id'):
            return
        self.sio.emit('signal', {
            'to': self.target_sender_id,
            'from': self.sio.sid,
            'type': 'offer',
            'payload': {'type': 'offer', 'sdp': self._pending_offer_sdp}
        })
        print('[SIO] offer 전송 →', self.target_sender_id)

    # 수신한 Answer SDP 적용
    def _apply_remote_answer(self, sdp_text: str):
        ok, sdpmsg = GstSdp.SDPMessage.new()
        if ok != GstSdp.SDPResult.OK: return False
        GstSdp.sdp_message_parse_buffer(sdp_text.encode('utf-8'), sdpmsg)
        answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg)
        self.webrtc.emit('set-remote-description', answer, None)
        print("[RTC] Remote ANSWER 적용 완료")
        return False

    # ICE candidate를 시그널링 서버로 전송
    def on_ice_candidate(self, element, mlineindex, candidate):
        if not hasattr(self, 'target_sender_id'): return
        self.sio.emit('signal', {
            'to': self.target_sender_id,
            'from': self.sio.sid,
            'type': 'candidate',
            'payload': {'candidate': candidate,
                        'sdpMid': f"video{mlineindex}",
                        'sdpMLineIndex': int(mlineindex)}
        })

    



    # 윈도우, 맥에서는 작동하지만 리눅스에서 화면 수신이 안됨
    # ------------------- RTP Pad -------------------
    # 수신된 RTP 스트림이 들어올 때 처리
    def on_incoming_stream(self, webrtc, pad):
        caps = pad.get_current_caps().to_string()
        print("[RTC] pad caps:", caps)

        if caps.startswith("application/x-rtp"):
            depay = _make("rtph264depay")   # RTP → H.264 추출
            parse = _make("h264parse")      # H.264 스트림 파싱
            decoder, conv, sink = get_decoder_and_sink(self.video_widget)

            # fpsdisplaysink 추가 (FPS / 드롭율 확인)
            fpssink = _make("fpsdisplaysink")
            fpssink.set_property("signal-fps-measurements", True)
            fpssink.set_property("text-overlay", False)
            fpssink.connect("fps-measurements",
                lambda el, fps, drop, avg:
                    print(f"[STATS][RX] FPS={fps:.2f}, drop={drop:.2f}, avg={avg:.2f}")
            )

            if not all([depay, parse, decoder, conv, sink]):
                print("[RTC] 요소 부족으로 링크 실패"); return

            for e in (depay, parse, decoder, conv, sink):
                self.pipeline.add(e); e.sync_state_with_parent()

            if pad.link(depay.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
                print("[RTC] pad link 실패"); return

            depay.link(parse)
            parse.link(decoder)
            decoder.link(conv)
            conv.link(sink)
            fpssink.link(sink)
            print(f"[OK] Incoming video linked → {decoder.name}")






    # ---- webrtcbin get-stats 추가 ----
    def log_receiver_stats(self):
        def on_stats(promise, element):
            reply = promise.get_reply()
            if not reply: return
            stats = reply.get_value("stats")
            for k, v in stats.items():
                if "inbound-rtp" in k and v.get("mediaType") == "video":
                    print(f"[STATS][RX] recv_bytes={v.get('bytesReceived')}, "
                        f"framesDecoded={v.get('framesDecoded')}, "
                        f"jitter={v.get('jitter')}, "
                        f"packetsLost={v.get('packetsLost')}")
        p = Gst.Promise.new_with_change_func(on_stats, self.webrtc)
        self.webrtc.emit("get-stats", None, p)

        # 파이프라인 실행 후 주기적으로 호출
        GLib.timeout_add_seconds(2, lambda: (self.log_receiver_stats() or True))



















# ---------- PyQt 메인 ----------
# PyQt5 메인 윈도우
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WebRTC Receiver (PyQt + GStreamer)")
        self.resize(1280, 800)
        
        # 비디오 출력 위젯
        self.video = QtWidgets.QWidget(self)
        self.video.setAttribute(QtCore.Qt.WA_NativeWindow)
        self.video.setStyleSheet("background-color: black;")
        self.setCentralWidget(self.video)

        # Receiver 생성
        self.rx = Receiver(video_widget=self.video)

        # GLib 이벤트 루프를 Qt 타이머로 통합
        self._glib_ctx = GLib.MainContext.default()
        self._glib_pump = QtCore.QTimer(self)
        self._glib_pump.timeout.connect(lambda: self._glib_ctx.iteration(False))
        self._glib_pump.start(10)

        # Receiver 시작
        self.rx.start()

    # 창 닫기 이벤트
    def closeEvent(self, e):
        try: self._glib_pump.stop()
        except: pass
        try: self.rx.stop()
        except: pass
        super().closeEvent(e)





# ---------- 프로그램 실행 ----------
if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow(); win.show()
    signal.signal(signal.SIGINT, lambda *a: app.quit()) # Ctrl+C 처리
    sys.exit(app.exec_())
