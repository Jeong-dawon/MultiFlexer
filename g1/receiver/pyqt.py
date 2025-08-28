# -*- coding: utf-8 -*-
# ===============================================================
# WebRTC Receiver (PyQt5 + GStreamer + socket.io signaling)
# ===============================================================
# 이 파일은 다음 4가지 축으로 구성됩니다.
#  1) [# PyQt] UI 구성 및 사용자 이벤트 처리
#  2) [# Socket.IO] 시그널링 서버와의 통신 (sender 목록/offer/answer/candidate 교환)
#  3) [# WebRTC]/[# GStreamer] webrtcbin 기반 수신 파이프라인 구성/협상/ICE
#  4) [# Utils]/[# UI/Widget] OS별 디코더/싱크 선택, 스타일, 드래그&드롭 등
# ===============================================================

import sys, signal, threading
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstWebRTC', '1.0')
gi.require_version('GstSdp', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GstWebRTC, GstSdp, GLib, GstVideo   # [# GStreamer] GStreamer & WebRTC 바인딩
import socketio                                                   # [# Socket.IO] signaling 클라이언트
import platform, os

from PyQt5 import QtWidgets, QtCore, QtGui                        # [# PyQt] PyQt5 UI

# ---------- 설정 ----------
SIGNALING_URL = "http://localhost:3001"   # [# Socket.IO] 시그널링 서버 주소
ROOM_PASSWORD  = "1"                      # [# Socket.IO] 방 비밀번호(서버에 전달)
RECEIVER_NAME  = "Receiver-1"             # [# Socket.IO] 수신자 이름(서버에 전달)
PREFERRED_SENDER_ID = None                # [# Utils] (옵션) 특정 sender 우선 선택 시 사용 가능

Gst.init(None)                            # [# GStreamer] GStreamer 전역 초기화

# ---------- 유틸 ----------
def _make(name):                          # [# Utils] element 팩토리 헬퍼
    return Gst.ElementFactory.make(name) if name else None

def _first_available(*names):             # [# Utils] 후보 중 첫 사용가능 element 생성
    for n in names:
        if Gst.ElementFactory.find(n):
            e = Gst.ElementFactory.make(n)
            if e:
                return e
    return None

# ---------- OS/플랫폼별 하드웨어 디코딩 + Sink 선택 ----------
def get_decoder_and_sink(video_widget):
    """
    [# GStreamer][# Utils]
    - OS/플랫폼별로 적절한 하드웨어 디코더/컨버터/싱크를 선택
    - PyQt 위젯에 직접 바인딩 가능한 sink를 선호 (VideoOverlay)
    """
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

    else:
        decoder = _first_available("avdec_h264")
        conv    = _first_available("videoconvert")
        sink    = _first_available("autovideosink")

    # [# PyQt] PyQt 위젯 안에서만 출력되도록 sink 윈도우 핸들 연결
    if sink and video_widget:
        xid = int(video_widget.winId())
        sink.set_window_handle(xid)  # VideoOverlay 핸들

    return decoder, conv, sink

# ---------- Qt 신호 채널 ----------
class UiEvents(QtCore.QObject):  # [# PyQt] 앱 내부에서 쓸 커스텀 Qt 시그널
    senderListUpdated   = QtCore.pyqtSignal(list)   # 시그널링서버 -> UI (sender 목록 변경)
    senderShareStarted  = QtCore.pyqtSignal(dict)   # 시그널링서버 -> UI (어떤 sender가 공유 시작)
    videoStarted        = QtCore.pyqtSignal()       # GStreamer -> UI (첫 프레임/체인 준비됨)

class DropVideoCanvas(QtWidgets.QWidget):  # [# PyQt][# UI/Widget] 드래그&드롭 캔버스(메인 화면)
    senderDropped = QtCore.pyqtSignal(str) # 타일에서 드래그된 sender_id 수신 시그널
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setAttribute(QtCore.Qt.WA_NativeWindow)  # VideoOverlay 가능하도록 네이티브 창 속성
    def dragEnterEvent(self, e: QtGui.QDragEnterEvent):  # [# PyQt 이벤트 핸들러]
        if e.mimeData().hasFormat("application/x-sender-id"):
            e.acceptProposedAction()
    def dragMoveEvent(self, e: QtGui.QDragMoveEvent):    # [# PyQt 이벤트 핸들러]
        if e.mimeData().hasFormat("application/x-sender-id"):
            e.acceptProposedAction()
    def dropEvent(self, e: QtGui.QDropEvent):            # [# PyQt 이벤트 핸들러]
        if not e.mimeData().hasFormat("application/x-sender-id"):
            return
        sender_id = bytes(e.mimeData().data("application/x-sender-id")).decode("utf-8", errors="ignore")
        self.senderDropped.emit(sender_id)  # 드롭된 sender_id를 상위 로직으로 전달
        e.acceptProposedAction()

# ---------- Receiver ----------
class Receiver(QtCore.QObject):
    """
    [핵심 모듈]
    - [# WebRTC]/[# GStreamer]: webrtcbin 파이프라인 생성/협상/ICE/수신체인 구성
    - [# Socket.IO]: signaling 서버와 offer/answer/candidate 교환
    - [# PyQt]: VideoOverlay 싱크를 PyQt 위젯에 바인딩
    """
    def __init__(self, video_widget=None, ui_events: UiEvents = None):
        super().__init__()
        # 내부 상태
        self._negotiating = False
        self._gst_playing = False
        self._pending_offer_sdp = None
        self._sender_ready = False
        self._pending_offer_after_playing = False

        self.video_widget = None       # 현재 바인딩된 출력 위젯
        self.video_sink = None         # 현재 사용중인 sink
        self._queue = None             # sink 앞단 queue(싱크 교체시 BLOCK 용)
        self.ui = ui_events or UiEvents()

        # [# GStreamer] 파이프라인과 webrtcbin 생성
        self.pipeline = Gst.Pipeline.new("webrtc-pipeline")
        self.webrtc = _make("webrtcbin")
        if not self.webrtc:
            raise RuntimeError("webrtcbin 생성 실패")
        self.pipeline.add(self.webrtc)

        # [# WebRTC] STUN/콜백 연결
        self.webrtc.set_property('stun-server', 'stun://stun.l.google.com:19302')
        self.webrtc.connect('notify::ice-connection-state', self._on_ice_conn_change)  # ICE 상태 변경
        self.webrtc.connect('on-ice-candidate', self.on_ice_candidate)                 # 로컬 ICE 발생
        self.webrtc.connect('pad-added', self.on_incoming_stream)                      # 원격 트랙 추가
        self.webrtc.connect('on-negotiation-needed', self._on_negotiation_needed)      # 협상 트리거

        # [# GStreamer] Bus 메시지 처리(상태/에러/VideoOverlay 준비)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::state-changed", self._on_state_changed)
        bus.connect("message::error", self._on_error)
        bus.enable_sync_message_emission()
        bus.connect("sync-message::element", self._on_sync_element)

        # [# Socket.IO] signaling client
        self.sio = socketio.Client(logger=False, engineio_logger=False)
        self._bind_socket_events()  # 이벤트 바인딩

        self.target_sender_id = None
        if video_widget:
            self.set_video_widget(video_widget)  # 초기 출력 대상 위젯

    def _log(self, *msg): print(*msg)  # [# Utils] 간단한 로그

    # ---------- VideoOverlay helpers ----------
    def _bind_sink_to_widget(self, sink, widget, tag=""):  # [# PyQt][# GStreamer] sink를 PyQt 위젯에 바인딩
        if not sink or not widget: return False
        try:
            wid = int(widget.winId())
            try:
                if hasattr(sink, "set_window_handle"):
                    sink.set_window_handle(0)  # 먼저 클리어
            except Exception: pass
            if hasattr(sink, "set_window_handle"):
                sink.set_window_handle(wid)
            else:
                GstVideo.VideoOverlay.set_window_handle(sink, wid)
            try:
                # 초기 렌더 사각형 설정(선택)
                GstVideo.VideoOverlay.set_render_rectangle(sink, 0, 0,
                                                           max(1, widget.width()),
                                                           max(1, widget.height()))
            except Exception: pass
            try:
                if hasattr(sink, "expose"): sink.expose()
                else: GstVideo.VideoOverlay.expose(sink)
            except Exception: pass
            self._log(f"[PYQT] bind({tag}) -> wid={wid} size=({widget.width()}x{widget.height()})")
            return True
        except Exception as e:
            self._log("[PYQT] bind 실패:", e); return False

    def _on_sync_element(self, bus, msg):   # [# GStreamer] sink가 윈도우 핸들을 필요로 할 때 호출
        s = msg.get_structure()
        if not s or s.get_name() != 'prepare-window-handle': return
        if msg.src is self.video_sink and self.video_widget:
            self._bind_sink_to_widget(self.video_sink, self.video_widget, "prepare-window")

    # ---------- Pipeline lifecycle ----------
    def _ensure_transceivers(self):  # [# WebRTC] RECVONLY 비디오 트랜시버 추가
        if getattr(self, "_added", False): return
        caps = "application/x-rtp,media=video,encoding-name=H264,clock-rate=90000," \
               "payload=102,packetization-mode=(string)1,profile-level-id=(string)42e01f"
        self.webrtc.emit('add-transceiver',
                         GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY,
                         Gst.Caps.from_string(caps))
        self._added = True
        self._log('[RTC] transceiver added:', caps)

    def start(self):  # [# GStreamer][# Socket.IO] 파이프라인 PLAYING, 시그널링 연결 스레드 시작
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        self._log("[GST] set_state ->", ret.value_nick)
        threading.Thread(target=self._sio_connect, daemon=True).start()

    def stop(self):   # [# GStreamer][# Socket.IO] 정리
        try:
            if self.sio.connected: self.sio.disconnect()
        except Exception: pass
        try: self.pipeline.set_state(Gst.State.NULL)
        except Exception: pass

    def _on_state_changed(self, bus, msg):   # [# GStreamer] 파이프라인 상태 변화
        if msg.src is self.pipeline:
            _, new, _ = msg.parse_state_changed()
            if new == Gst.State.PLAYING and not self._gst_playing:
                self._gst_playing = True
                self._log("[GST] pipeline → PLAYING")
                self._ensure_transceivers()
                # 파이프라인이 올라오고 sender가 준비됐다면 offer 생성
                if self._pending_offer_after_playing and self._sender_ready:
                    self._pending_offer_after_playing = False
                    GLib.idle_add(lambda: self._maybe_create_offer())

    def _on_error(self, bus, msg):  # [# GStreamer] 에러 로깅
        err, dbg = msg.parse_error()
        self._log(f"[GST][ERROR] {err.message} (debug: {dbg})")

    # ---------- Signaling ----------
    def _sio_connect(self):  # [# Socket.IO] 서버 연결 + 대기
        try:
            self.sio.connect(SIGNALING_URL, transports=['websocket']); self.sio.wait()
        except Exception as e:
            self._log("[SIO] connect error:", e)

    def _bind_socket_events(self):  # [# Socket.IO] 이벤트 바인딩
        @self.sio.event
        def connect():
            # 연결되면 방에 조인
            self._log("[SIO] connected:", self.sio.sid)
            self.sio.emit('join-room',
                          {'role':'receiver','password':ROOM_PASSWORD,'name':RECEIVER_NAME},
                          callback=lambda ack: self._log("[SIO] join-room ack:", ack))

        @self.sio.on('sender-list')
        def on_sender_list(sender_arr):
            # 서버가 알려주는 현재 참여중 sender 목록
            self._log("[SIO] sender-list:", sender_arr)
            try: self.ui.senderListUpdated.emit(sender_arr)  # [# PyQt] UI 스레드로 신호
            except Exception: pass

        @self.sio.on('sender-share-started')
        def on_sender_share_started(data):
            # 특정 sender가 화면 공유 시작
            self._log("[SIO] sender-share-started:", data)
            self._sender_ready = True
            try: self.ui.senderShareStarted.emit(data or {})  # [# PyQt]
            except Exception: pass
            # 파이프라인/상대 준비상태 보고 offer 생성
            if self._gst_playing: GLib.idle_add(lambda: self._maybe_create_offer())
            else: self._pending_offer_after_playing = True

        @self.sio.on('signal')
        def on_signal(data):
            # 서버를 통해 수신되는 WebRTC 시그널링 메시지(answer/candidate)
            typ = data.get('type'); payload = data.get('payload')
            self._log("[SIO] signal recv:", typ, "from", data.get('from'))
            if typ == 'answer' and payload:
                sdp_text = payload['sdp'] if isinstance(payload, dict) else payload
                GLib.idle_add(self._apply_remote_answer, sdp_text)  # [# WebRTC]
            elif typ == 'candidate' and payload:
                cand  = payload.get('candidate'); mline = int(payload.get('sdpMLineIndex') or 0)
                self._log("[RECV] candidate recv mline=", mline, "cand head=", (cand or '')[:60])
                if cand is not None: GLib.idle_add(self.webrtc.emit, 'add-ice-candidate', mline, cand)

        @self.sio.event
        def disconnect(): self._log("[SIO] disconnected")

    # ---------- WebRTC negotiation ----------
    def _on_negotiation_needed(self, element, *args):  # [# WebRTC] webrtcbin이 협상 필요 알림
        if not self._gst_playing or not self._sender_ready:
            self._log("[RTC] negotiation-needed (ignored)")
            return
        if self._negotiating:
            self._log("[RTC] skip offer: already negotiating"); return
        self._log("[RTC] on-negotiation-needed → try create offer")
        GLib.idle_add(lambda: self._maybe_create_offer())

    def _maybe_create_offer(self):  # [# WebRTC] offer 생성 시작
        if self._negotiating: return False
        self._negotiating = True
        self._log("[RTC] creating offer...")
        def _do():
            p = Gst.Promise.new_with_change_func(self._on_offer_created, self.webrtc)
            self.webrtc.emit('create-offer', None, p); return False
        GLib.idle_add(_do); return False

    def _on_offer_created(self, promise, element):  # [# WebRTC] offer 생성 완료 콜백
        reply = promise.get_reply()
        if not reply: self._log("[RTC] create-offer: empty reply"); self._negotiating=False; return
        offer = reply.get_value('offer')
        if not offer: self._log("[RTC] create-offer: no offer"); self._negotiating=False; return
        self._pending_offer_sdp = offer.sdp.as_text()
        p2 = Gst.Promise.new_with_change_func(self._on_local_desc_set, element)
        element.emit('set-local-description', offer, p2)  # 로컬 SDP 설정

    def _on_local_desc_set(self, promise, element):  # [# WebRTC] 로컬 SDP 설정 완료
        self._log("[RTC] Local description set (offer)")
        if self.target_sender_id and self._gst_playing: self._send_offer()
        self._negotiating = False

    def _send_offer(self):  # [# Socket.IO] offer를 상대(sender)에게 전송
        if not self._pending_offer_sdp or not self.target_sender_id: return
        self.sio.emit('signal', {
            'to': self.target_sender_id, 'from': self.sio.sid, 'type': 'offer',
            'payload': {'type': 'offer', 'sdp': self._pending_offer_sdp}
        })
        self._log('[SIO] offer 전송 →', self.target_sender_id)

    def _apply_remote_answer(self, sdp_text: str):  # [# WebRTC] remote answer 적용
        ok, sdpmsg = GstSdp.SDPMessage.new()
        if ok != GstSdp.SDPResult.OK: self._log("[RTC] SDPMessage.new 실패"); return False
        GstSdp.sdp_message_parse_buffer(sdp_text.encode('utf-8'), sdpmsg)
        answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg)
        self.webrtc.emit('set-remote-description', answer, None)
        self._log("[RTC] Remote ANSWER 적용 완료"); return False

    def on_ice_candidate(self, element, mlineindex, candidate):  # [# WebRTC -> # Socket.IO] 로컬 ICE 발생 시 전송
        if not self.target_sender_id: return
        self.sio.emit('signal', {
            'to': self.target_sender_id, 'from': self.sio.sid, 'type': 'candidate',
            'payload': {'candidate': candidate, 'sdpMid': f"video{int(mlineindex)}",
                        'sdpMLineIndex': int(mlineindex)}
        })

    # ---------- Sink create/replace (BLOCK probe 방식) ----------
    def _create_sink_for_widget(self, widget):  # [# GStreamer][# PyQt] 위젯용 sink 생성 및 바인딩
        _dec, _conv, sink = get_decoder_and_sink(widget)
        if not sink:
            sink = _make("d3d11videosink") or _make("glimagesink") or _make("autovideosink")
        if sink:
            try:
                sink.set_property("force-aspect-ratio", True)
            except Exception:
                pass
            if widget:
                self._bind_sink_to_widget(sink, widget, "create")
        return sink

    def _replace_sink_blocking(self, new_widget):
        """
        [# GStreamer][# PyQt]
        - queue 뒤 src pad를 BLOCK_DOWNSTREAM probe로 막은 뒤 안전하게 sink 교체
        - 동영상 재생 중 출력 대상 위젯을 바꿀 때 테어링/크래시 방지
        """
        if not self._queue:
            return
        srcpad = self._queue.get_static_pad("src")
        if not srcpad:
            return

        def _probe_cb(pad, info):
            try:
                # 1) 이전 sink 정리
                if self.video_sink:
                    try: self._queue.unlink(self.video_sink)
                    except Exception: pass
                    try: self.video_sink.set_state(Gst.State.NULL)
                    except Exception: pass
                    try: self.pipeline.remove(self.video_sink)
                    except Exception: pass

                # 2) 새 sink 생성/바인딩/링크
                new_sink = self._create_sink_for_widget(new_widget)
                if not new_sink:
                    self._log("[RTC] 새 sink 생성 실패"); return Gst.PadProbeReturn.REMOVE
                self.pipeline.add(new_sink)
                new_sink.sync_state_with_parent()
                if not self._queue.link(new_sink):
                    self._log("[RTC] link fail: queue->sink")
                    try: self.pipeline.remove(new_sink)
                    except Exception: pass
                    return Gst.PadProbeReturn.REMOVE

                # 3) 상태 올리고 표시
                new_sink.set_state(Gst.State.PLAYING)
                self.video_sink = new_sink
                self.video_widget = new_widget
                self._bind_sink_to_widget(self.video_sink, self.video_widget, "replace")

                self._log("[PYQT] sink replaced ->",
                          self.video_widget.objectName() if self.video_widget else "None")
            finally:
                pass
            return Gst.PadProbeReturn.REMOVE

        # BLOCK 걸고 GLib idle에서 실제 교체 수행되도록 함
        srcpad.add_probe(Gst.PadProbeType.BLOCK_DOWNSTREAM, _probe_cb)

    def set_video_widget(self, widget: QtWidgets.QWidget | None):  # [# PyQt] 현재 출력 대상 위젯 지정/변경
        self.video_widget = widget
        if self.video_sink and self._queue:
            GLib.idle_add(lambda: (self._replace_sink_blocking(widget), False)[1])

    # ---------- pad-added ----------
    def on_incoming_stream(self, webrtc, pad):
        """
        [# WebRTC]/[# GStreamer]
        - 원격 비디오 트랙이 추가되면 호출
        - depay → (parser) → decoder → convert → queue → sink 체인 구성 및 링크
        - sink는 현재 video_widget에 바인딩
        """
        self._log("[RTC] pad-added from webrtc:", pad.get_name())
        caps = pad.get_current_caps() or pad.query_caps(None)
        self._log("[RTC] pad caps:", caps.to_string() if caps else "None")
        if not caps: return
        s = caps.get_structure(0)
        if not s or s.get_string("media") != "video": return
        enc = s.get_string("encoding-name")

        # OS별 추천 디코더/싱크
        dec_suggest, conv_suggest, sink_suggest = get_decoder_and_sink(self.video_widget)

        depay = parser = decoder = convert = queue = sink = None

        if enc == "H264":
            depay = _make("rtph264depay"); parser = _make("h264parse")
            decoder = dec_suggest
            if not decoder:
                for name in ["vtdec","nvv4l2decoder","vaapih264dec","v4l2h264dec","d3d11h264dec","avdec_h264"]:
                    if Gst.ElementFactory.find(name):
                        decoder = _make(name); break
        elif enc == "VP8":
            depay = _make("rtpvp8depay"); parser = None; decoder = _make("vp8dec")
        elif enc == "VP9":
            depay = _make("rtpvp9depay"); parser = None; decoder = _make("vp9dec")
        else:
            self._log("[RTC] Unsupported encoding:", enc); return

        convert = conv_suggest or _make("videoconvert")
        queue = _make("queue")
        sink = sink_suggest or self._create_sink_for_widget(self.video_widget)

        self._queue = queue
        self.video_sink = sink

        # 파이프라인에 element 추가 후 상태 동기화
        for e in [depay, parser, decoder, convert, queue, sink]:
            if e:
                self.pipeline.add(e); e.sync_state_with_parent()

        # 체인 링크
        chain = [depay] + ([parser] if parser else []) + [decoder, convert, queue, sink]
        for a, b in zip(chain, chain[1:]):
            if not a.link(b):
                self._log(f"[RTC] link fail: {a.name}->{b.name}"); return

        # webrtcbin src pad → depay sink pad 링크
        if pad.link(depay.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            self._log("[RTC] pad link 실패(webrtc→depay)"); return

        self._log(f"[OK] Incoming video: {enc} → {decoder.name if decoder else '???'}")
        try: self.ui.videoStarted.emit()  # [# PyQt] UI에 "영상 시작됨" 알리기
        except Exception: pass

    # ---------- Misc ----------
    def _on_ice_conn_change(self, obj, pspec):  # [# WebRTC] ICE 연결 상태 로깅
        try: state = int(self.webrtc.get_property('ice-connection-state'))
        except Exception as e: self._log("[RTC] ICE state read error:", e); return
        W = GstWebRTC
        names = {int(W.WebRTCICEConnectionState.NEW):'new',
                 int(W.WebRTCICEConnectionState.CHECKING):'checking',
                 int(W.WebRTCICEConnectionState.CONNECTED):'connected',
                 int(W.WebRTCICEConnectionState.COMPLETED):'completed',
                 int(W.WebRTCICEConnectionState.FAILED):'failed',
                 int(W.WebRTCICEConnectionState.DISCONNECTED):'disconnected',
                 int(W.WebRTCICEConnectionState.CLOSED):'closed'}
        self._log(f"[RTC] ICE (receiver): {names.get(state, state)}")

    def request_share(self, sender_id: str):
        """
        [# Socket.IO] 특정 sender에게 화면 공유 요청을 보냄
        [# WebRTC] target_sender_id 설정 후 조건 충족 시 offer 생성
        """
        self.target_sender_id = sender_id
        if self.sio.connected:
            self.sio.emit('share-request', {'to': sender_id})
            self._log("[UI] share-request →", sender_id)
        if self._gst_playing and (not self._pending_offer_sdp):
            GLib.idle_add(lambda: self._maybe_create_offer())

# ---------- 좌측 SenderList ----------
class SenderList(QtWidgets.QWidget):  # [# PyQt][# UI/Widget] 왼쪽 목록 패널
    def __init__(self, on_request_click):
        super().__init__()
        self.setObjectName("senderList")
        self.on_request_click = on_request_click
        self.v = QtWidgets.QVBoxLayout(self)
        self.v.setContentsMargins(0,0,0,0); self.v.setSpacing(6)
        self.v.addStretch(1)
    def set_senders(self, arr):  # [# PyQt 이벤트 핸들러] sender 목록 갱신 → 버튼/행 재구성
        while self.v.count():
            item = self.v.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()
        for s in arr:
            row = QtWidgets.QFrame(); row.setObjectName("senderRow")
            hl = QtWidgets.QHBoxLayout(row)
            name = QtWidgets.QLabel(s.get("name") or s.get("id") or "unknown")
            btn_req = QtWidgets.QPushButton("화면 공유 요청")
            btn_req.clicked.connect(lambda _, sid=s.get("id"): self.on_request_click(sid))
            hl.addWidget(name); hl.addStretch(1); hl.addWidget(btn_req)
            self.v.addWidget(row)
        self.v.addStretch(1)

# ---- 하단 타일 ----
class SenderTile(QtWidgets.QFrame):  # [# PyQt][# UI/Widget] 하단 썸네일 타일
    TILE_MIN_W = 140
    TILE_MIN_H = 96
    def __init__(self, sender_id: str, sender_name: str, on_drag_start):
        super().__init__()
        self.sender_id = sender_id
        self.on_drag_start = on_drag_start
        self.setObjectName("senderTile")
        self.setMinimumSize(self.TILE_MIN_W, self.TILE_MIN_H)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6,6,6,6); v.setSpacing(4)
        self.video = QtWidgets.QWidget()
        self.video.setObjectName("thumbVideo")
        self.video.setAttribute(QtCore.Qt.WA_NativeWindow)  # 비디오 바인딩 가능
        self.video.setMinimumHeight(self.TILE_MIN_H - 32)
        v.addWidget(self.video, 1)
        self.label = QtWidgets.QLabel(sender_name or sender_id or "unknown")
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        self.label.setObjectName("senderName")
        self.label.setFixedHeight(22)
        v.addWidget(self.label)
    def mousePressEvent(self, e: QtGui.QMouseEvent):  # [# PyQt 이벤트 핸들러] 드래그 시작
        if e.button() == QtCore.Qt.LeftButton:
            drag = QtGui.QDrag(self)
            mime = QtCore.QMimeData()
            mime.setData("application/x-sender-id", self.sender_id.encode("utf-8"))
            drag.setMimeData(mime)
            self.on_drag_start(self.sender_id)
            drag.exec_(QtCore.Qt.CopyAction)

class SenderTileRow(QtWidgets.QWidget):  # [# PyQt][# UI/Widget] 타일들을 가로로 담는 컨테이너
    def __init__(self, on_drag_start):
        super().__init__()
        self.on_drag_start = on_drag_start
        self.h = QtWidgets.QHBoxLayout(self)
        self.h.setContentsMargins(8,0,8,0); self.h.setSpacing(12)
        self.tiles = {}
        self.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)
    def ensure_tile(self, sender_id: str, sender_name: str):
        # sender_id에 해당하는 타일을 보장(있으면 업데이트, 없으면 생성)
        if sender_id in self.tiles:
            self.tiles[sender_id].label.setText(sender_name or sender_id)
            return self.tiles[sender_id]
        tile = SenderTile(sender_id, sender_name, self.on_drag_start)
        self.tiles[sender_id] = tile
        self.h.addWidget(tile)
        return tile

# ---------- 메인 윈도우 ----------
class MainWindow(QtWidgets.QMainWindow):  # [# PyQt] 전체 UI 및 앱 진입점
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WebRTC Receiver (PyQt5 + GStreamer)")
        self.resize(1200, 800)

        self.sender_names = {}                        # sender_id → 이름 맵
        self.uiEvents = UiEvents()                    # [# PyQt] 내부 커스텀 시그널 객체

        # 레이아웃 구성 ---------------------------------------------------------
        root = QtWidgets.QWidget(); self.setCentralWidget(root)
        main = QtWidgets.QVBoxLayout(root)
        main.setContentsMargins(16,16,16,16); main.setSpacing(10)

        title = QtWidgets.QLabel("Receiver"); title.setObjectName("title")
        font = QtGui.QFont(); font.setPointSize(20); font.setBold(True)
        title.setFont(font); main.addWidget(title)

        ctr = QtWidgets.QHBoxLayout()
        self.input_pw = QtWidgets.QLineEdit(); self.input_pw.setPlaceholderText("방 비밀번호")
        self.btn_join = QtWidgets.QPushButton("방 입장")   # (현재 서버로 보내진 join은 Receiver.start에서 처리)
        self.btn_del  = QtWidgets.QPushButton("방 삭제")   # (예시 버튼, 동작 구현 X)
        ctr.addWidget(self.input_pw, 2); ctr.addWidget(self.btn_join); ctr.addWidget(self.btn_del)
        main.addLayout(ctr)

        self.room_label = QtWidgets.QLabel("센더 연결 대기 중…"); self.room_label.setObjectName("roomNum")
        main.addWidget(self.room_label)

        hsplit = QtWidgets.QHBoxLayout(); hsplit.setSpacing(12)

        # 좌측: Sender 목록
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True)
        self.senderList = SenderList(self._on_request_click)
        scroll.setWidget(self.senderList); scroll.setMinimumWidth(300)
        hsplit.addWidget(scroll, 3)

        # 우측: 메인 프레임(비디오 캔버스)
        mainFrame = QtWidgets.QFrame(); mainFrame.setObjectName("mainFrame")
        mv = QtWidgets.QVBoxLayout(mainFrame); mv.setContentsMargins(12,12,12,12); mv.setSpacing(10)

        self.videoCanvas = DropVideoCanvas()  # [# PyQt][# UI/Widget] 메인 드롭 대상 & 출력
        self.videoCanvas.setObjectName("canvas")
        self.videoCanvas.setMinimumSize(640, 360)

        # 대기/영상 페이지 전환용 스택
        self.overlayWrap = QtWidgets.QStackedLayout(); self.overlayWrap.setContentsMargins(0,0,0,0)
        overlayHost = QtWidgets.QWidget(); overlayHost.setLayout(self.overlayWrap)

        self.waitPage = QtWidgets.QWidget()
        waitLayout = QtWidgets.QVBoxLayout(self.waitPage)
        waitLayout.setContentsMargins(0,0,0,0); waitLayout.setSpacing(0)
        waitLayout.addStretch(1)
        waitLabel = QtWidgets.QLabel("⏳ 센더 연결 대기 중")
        waitLabel.setStyleSheet("color:#9ca3af; font-size:18px;"); waitLabel.setAlignment(QtCore.Qt.AlignCenter)
        waitLayout.addWidget(waitLabel); waitLayout.addStretch(1)

        self.videoPage = QtWidgets.QWidget()
        vpLayout = QtWidgets.QVBoxLayout(self.videoPage)
        vpLayout.setContentsMargins(0,0,0,0); vpLayout.setSpacing(0)
        vpLayout.addWidget(self.videoCanvas)

        self.overlayWrap.addWidget(self.waitPage)
        self.overlayWrap.addWidget(self.videoPage)
        self.overlayWrap.setCurrentIndex(0)

        mv.addWidget(overlayHost, 1)
        hsplit.addWidget(mainFrame, 7)
        main.addLayout(hsplit, 1)

        # 하단 타일 영역
        sub = QtWidgets.QLabel("Sender 화면 목록")
        f2 = QtGui.QFont(); f2.setPointSize(12); f2.setBold(True)
        sub.setFont(f2); main.addWidget(sub)

        self.tileRow = SenderTileRow(self._on_drag_start_request)

        self.tileScroll = QtWidgets.QScrollArea()
        self.tileScroll.setWidgetResizable(True)
        self.tileScroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.tileScroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.tileScroll.setFixedHeight(SenderTile.TILE_MIN_H + 20)
        self.tileScroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        tileWrap = QtWidgets.QWidget()
        tileLayout = QtWidgets.QHBoxLayout(tileWrap)
        tileLayout.setContentsMargins(0,0,0,0); tileLayout.setSpacing(0)
        tileLayout.addWidget(self.tileRow); tileLayout.addStretch(1)
        self.tileScroll.setWidget(tileWrap); main.addWidget(self.tileScroll)

        self.apply_styles()  # [# UI/Widget] 스타일 적용

        # Receiver 생성 ---------------------------------------------------------
        self.rx = Receiver(video_widget=None, ui_events=self.uiEvents)  # [# WebRTC]/[# Socket.IO]/[# PyQt]

        # GLib ↔ Qt 이벤트루프 브릿지 ------------------------------------------
        # [# PyQt][# GStreamer] GStreamer(GLib) 메인컨텍스트를 주기적으로 펌프
        self._glib_ctx = GLib.MainContext.default()
        self._glib_pump = QtCore.QTimer(self)
        self._glib_pump.timeout.connect(lambda: self._glib_ctx.iteration(False))
        self._glib_pump.start(10)  # 10ms 간격

        # [# PyQt] 커스텀 시그널 연결
        self.uiEvents.senderListUpdated.connect(self._update_sender_list)
        self.uiEvents.senderShareStarted.connect(self._on_sender_share_started)
        self.uiEvents.videoStarted.connect(self._on_video_started)

        # [# PyQt] 드롭 이벤트 연결
        self.videoCanvas.senderDropped.connect(self._on_main_drop_sender)

        # 시작! (파이프라인 PLAYING & socket.io 연결)
        self.rx.start()

    # ----- UI 핸들러 -----
    def _update_sender_list(self, arr):  # [# PyQt 이벤트 핸들러] sender 목록 갱신 → 좌측 리스트/라벨 업데이트
        self.sender_names = { (s.get("id") or ""): (s.get("name") or s.get("id") or "unknown") for s in arr }
        self.senderList.set_senders(arr)
        if arr: self.room_label.setText(f"참여 센더: {len(arr)}명")

    def _on_request_click(self, sender_id: str):  # [# PyQt 이벤트 핸들러] "화면 공유 요청" 버튼 클릭
        try: self.rx.request_share(sender_id)
        except Exception as e: print("[UI] share-request 실패:", e)

    def _on_drag_start_request(self, sender_id: str):  # [# PyQt 이벤트 핸들러] 타일 드래그 시작 시 추가 처리(옵션)
        pass

    def _on_sender_share_started(self, data: dict):  # [# PyQt 이벤트 핸들러] 서버가 "sender 공유 시작" 알림
        sender_id = data.get("senderId") or data.get("sender_id") or data.get("id") or ""
        if not sender_id: return
        sender_name = self.sender_names.get(sender_id, sender_id)
        tile = self.tileRow.ensure_tile(sender_id, sender_name)
        self.overlayWrap.setCurrentIndex(1)
        # 첫 출력은 해당 sender의 타일 썸네일 위젯에 바인딩
        QtCore.QTimer.singleShot(0, lambda: self.rx.set_video_widget(tile.video))

    def _on_video_started(self):  # [# PyQt 이벤트 핸들러] GStreamer 체인 준비 완료(첫 비디오)
        sid = getattr(self.rx, "target_sender_id", None)
        if not sid:
            self.overlayWrap.setCurrentIndex(1); return
        tile = self.tileRow.ensure_tile(sid, self.sender_names.get(sid, sid))
        QtCore.QTimer.singleShot(0, lambda: self.rx.set_video_widget(tile.video))
        self.overlayWrap.setCurrentIndex(1)

    def _on_main_drop_sender(self, sender_id: str):  # [# PyQt 이벤트 핸들러] 메인 캔버스로 드롭 시 메인으로 전환
        self.overlayWrap.setCurrentIndex(1)
        # 드롭 직후/잠시 후/더 잠시 후 3번 바인딩(플랫폼/렌더 타이밍 보정용)
        QtCore.QTimer.singleShot(0,   lambda: self.rx.set_video_widget(self.videoCanvas))
        QtCore.QTimer.singleShot(50,  lambda: self.rx.set_video_widget(self.videoCanvas))
        QtCore.QTimer.singleShot(200, lambda: self.rx.set_video_widget(self.videoCanvas))

    def apply_styles(self):  # [# UI/Widget] 전체 스타일시트
        self.setStyleSheet("""
            QWidget { font-family: 'Pretendard', 'Apple SD Gothic Neo', 'Malgun Gothic', Arial, sans-serif; }
            #title { color: #222; }
            QLineEdit { padding: 8px 10px; border: 1px solid #d0d5dd; border-radius: 8px; }
            QPushButton { padding: 8px 14px; border: 1px solid #d0d5dd; background: #f8fafc; border-radius: 8px; }
            QPushButton:hover { background: #eef2f7; }
            #roomNum { color: #0ea5e9; font-weight: 600; padding: 4px 0; }

            #senderList #senderRow { border: 1px solid #e5e7eb; border-radius: 12px; padding: 10px; background: #ffffff; }
            QScrollArea { border: none; background: transparent; }

            #mainFrame { background: #0b1220; border-radius: 16px; border: 1px solid #1f2937; }
            #canvas { background: #111827; border-radius: 12px; border: 1px dashed #334155; }

            #senderTile { background: #0f172a; border: 1px solid #182033; border-radius: 12px; }
            #senderTile:hover { border-color:#334155; }
            #thumbVideo { background:#0b1220; border-radius: 8px; }
            #senderName { color:#e5e7eb; }
        """)

    def closeEvent(self, e):  # [# PyQt 이벤트 핸들러] 창 닫을 때 정리
        try: self._glib_pump.stop()
        except Exception: pass
        try: self.rx.stop()
        except Exception: pass
        super().closeEvent(e)

# ---------- 엔트리포인트 ----------
if __name__ == "__main__":  # [# PyQt] QApplication 실행
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    signal.signal(signal.SIGINT, lambda *a: app.quit())  # Ctrl+C로 종료 가능(터미널 실행 시)
    sys.exit(app.exec_())
