# ================================================================
# Multi-Sender WebRTC
# Receiver (GStreamer + Socket.IO + PyQt5, Overlay)
# ================================================================
# - qt5videosink 대신 GstVideoOverlay 지원 싱크(glimagesink/d3d11videosink/avfvideosink 등) 사용
# - prepare-window-handle 시점에 PyQt 위젯 winId를 넘겨 임베드
# - 나머지 구조(webrtcbin/멀티 sender/GLib<->Qt 통합) 동일
# ================================================================

import sys, signal, threading, os, platform
import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstWebRTC', '1.0')
gi.require_version('GstSdp', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst, GstWebRTC, GstSdp, GLib, GstVideo
import socketio

from PyQt5 import QtCore, QtWidgets

# ---------- 설정 ----------
SIGNALING_URL = "http://localhost:3001"
ROOM_PASSWORD  = "1"
RECEIVER_NAME  = "Receiver-1"

Gst.init(None)


# ---------- PyQt5 UI ----------
class ReceiverWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WebRTC Receiver")
        self.resize(1280, 720)

        self._widgets = {}  # sender_id -> QWidget

        cw = QtWidgets.QWidget(self)
        self.setCentralWidget(cw)
        self._stack = QtWidgets.QStackedLayout()
        lay = QtWidgets.QVBoxLayout(cw)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._stack)

        self._placeholder = QtWidgets.QLabel("Waiting for senders...", alignment=QtCore.Qt.AlignCenter)
        self._placeholder.setStyleSheet("color:#888; font-size:18px;")
        self._stack.addWidget(self._placeholder)

        self.statusBar().showMessage("Idle")

    @QtCore.pyqtSlot(str, str, result=object)
    def ensure_widget(self, sender_id: str, sender_name: str):
        """sender 전용 비디오 QWidget 생성/반환 (네이티브 핸들 확보)"""
        w = self._widgets.get(sender_id)
        if w is None:
            w = QtWidgets.QWidget(self)
            w.setObjectName(f"video-{sender_id}")
            w.setStyleSheet("background:black;")
            # ▶ Overlay 임베드를 위해 네이티브 핸들 강제 및 확보
            w.setAttribute(QtCore.Qt.WA_NativeWindow, True)
            _ = w.winId()  # 핸들 실체화
            self._widgets[sender_id] = w
            self._stack.addWidget(w)
        return w

    def get_widget(self, sender_id: str):
        return self._widgets.get(sender_id)

    @QtCore.pyqtSlot(str)
    def set_active_sender(self, sender_id: str):
        w = self._widgets.get(sender_id)
        if w:
            self._stack.setCurrentWidget(w)
            self.statusBar().showMessage(f"Viewing sender: {sender_id}")
        else:
            self._stack.setCurrentWidget(self._placeholder)
            self.statusBar().showMessage("Idle")


# ---------- GStreamer 유틸 ----------
def _make(name):
    return Gst.ElementFactory.make(name) if name else None

def _first_available(*names):
    for n in names:
        if Gst.ElementFactory.find(n):
            e = Gst.ElementFactory.make(n)
            if e:
                return e
    return None

def _set_props_if_supported(element, **kwargs):
    if not element:
        return
    for k, v in kwargs.items():
        try:
            element.set_property(k, v)
        except Exception:
            pass


# ---------- HW 디코더 & Overlay 싱크 선택 ----------
def get_decoder_and_sink():
    sysname = platform.system().lower()
    decoder, conv, sink = None, None, None

    # 디코더
    if "linux" in sysname:
        if os.path.isfile("/etc/nv_tegra_release"):  # Jetson
            decoder = _first_available("nvv4l2decoder", "omxh264dec")
            conv    = _first_available("nvvidconv", "videoconvert")
        else:
            decoder = _first_available("vaapih264dec", "v4l2h264dec", "avdec_h264")
            conv    = _first_available("videoconvert")
    elif "windows" in sysname:
        decoder = _first_available("d3d11h264dec", "avdec_h264")
        conv    = _first_available("d3d11convert", "videoconvert")
    elif "darwin" in sysname:
        decoder = _first_available("vtdec", "avdec_h264")
        conv    = _first_available("videoconvert")
    else:
        decoder = _first_available("avdec_h264")
        conv    = _first_available("videoconvert")

    # ▶ Overlay 가능한 싱크 우선
    if "windows" in sysname:
        sink = _first_available("d3d11videosink", "autovideosink")
    elif "darwin" in sysname:
        sink = _first_available("avfvideosink", "autovideosink")  # avfvideosink가 Overlay 임베드 안 되면 별도 창 폴백
    else:
        sink = _first_available("glimagesink", "xvimagesink", "autovideosink")

    # ⬅️ Fallback 싱크에도 로그 추가
    if sink:
        print(f"[INFO] 비디오 싱크 사용: {sink.get_name()}")

    _set_props_if_supported(sink, force_aspect_ratio=True, fullscreen=False, handle_events=True)
    return decoder, conv, sink


# ================================================================
# PeerReceiver
# ================================================================
class PeerReceiver:

    def __init__(self, sio, sender_id, sender_name, ui_window: ReceiverWindow):
        self.sio = sio
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.ui = ui_window

        self._gst_playing = False
        self._negotiating = False
        self._sender_ready = False
        self._pending_offer_sdp = None
        self._transceivers = []
        self._transceivers_added = False

        self._display_bin = None
        self._visible = False

        # ▶ PyQt 임베드용 네이티브 윈도 핸들 캐시
        self._winid = None

        self.pipeline = Gst.Pipeline.new(f"webrtc-pipeline-{sender_id}")
        self.webrtc = _make("webrtcbin")
        if not self.webrtc:
            raise RuntimeError("webrtcbin 생성 실패")

        self.pipeline.add(self.webrtc)
        self.webrtc.set_property('stun-server', 'stun://stun.l.google.com:19302')

        self.webrtc.connect('notify::ice-connection-state', self._on_ice_conn_change)
        self.webrtc.connect('on-ice-candidate', self.on_ice_candidate)
        self.webrtc.connect('pad-added', self.on_incoming_stream)
        self.webrtc.connect('on-negotiation-needed', self._on_negotiation_needed)

        bus = self.pipeline.get_bus()
        # ▶ Overlay 핸들 세팅을 위한 sync handler
        bus.set_sync_handler(self._on_sync_message)
        bus.add_signal_watch()
        bus.connect("message::state-changed", self._on_state_changed)
        bus.connect("message::error", self._on_error)

    # ▶ UI 스레드에서 미리 winId 캐싱
    def prepare_window_handle(self):
        try:
            w = self.ui.ensure_widget(self.sender_id, self.sender_name)
            w.setAttribute(QtCore.Qt.WA_NativeWindow, True)
            self._winid = int(w.winId())
            print(f"[UI][{self.sender_name}] winId=0x{self._winid:x}")
        except Exception as e:
            print(f"[UI][{self.sender_name}] winId 준비 실패:", e)
        return False

    # -------- Bus sync: prepare-window-handle 처리 --------
    def _on_sync_message(self, bus, msg):
        try:
            if GstVideo.is_video_overlay_prepare_window_handle_message(msg):
                if self._winid is not None:
                    # msg.src 는 실제 비디오 싱크 (예: glimagesink)
                    GstVideo.VideoOverlay.set_window_handle(msg.src, self._winid)
                    print(f"[UI][{self.sender_name}] overlay handle set (0x{self._winid:x})")
                    return Gst.BusSyncReply.DROP  # 우리가 처리했으니 드롭
                else:
                    print(f"[UI][{self.sender_name}] winId not ready (will fallback to sink window)")
        except Exception as e:
            print(f"[BUS][{self.sender_name}] sync handler error:", e)
        return Gst.BusSyncReply.PASS

    # ---------------------- GStreamer 이벤트 ----------------------
    def _on_ice_conn_change(self, obj, pspec):
        try:
            state = int(self.webrtc.get_property('ice-connection-state'))
        except Exception as e:
            print(f"[RTC][{self.sender_name}] ICE state read error:", e); return
        print(f"[RTC][{self.sender_name}] ICE state: {state}")

    def _add_recv(self, caps_str):
        t = self.webrtc.emit(
            'add-transceiver',
            GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY,
            Gst.Caps.from_string(caps_str)
        )
        self._transceivers.append(t)
        print(f'[RTC][{self.sender_name}] transceiver added:', bool(t))

    def _ensure_transceivers(self):
        if self._transceivers_added:
            return
        self._add_recv("application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,"
                       "payload=102,packetization-mode=(string)1,profile-level-id=(string)42e01f")
        self._transceivers_added = True

    def start(self):
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        print(f"[GST][{self.sender_name}] set_state ->", ret.value_nick)

    def stop(self):
        try: self.pipeline.set_state(Gst.State.NULL)
        except: pass

    def _on_state_changed(self, bus, msg):
        if msg.src is self.pipeline:
            _, new, _ = msg.parse_state_changed()
            if new == Gst.State.PLAYING and not self._gst_playing:
                self._gst_playing = True
                print(f"[GST][{self.sender_name}] pipeline → PLAYING")
                self._ensure_transceivers()
                if self._sender_ready and not self._pending_offer_sdp:
                    GLib.idle_add(lambda: self._maybe_create_offer())

    def _on_error(self, bus, msg):
        err, dbg = msg.parse_error()
        print(f"[GST][{self.sender_name}][ERROR] {err.message} (debug: {dbg})")

    # ---------------------- Negotiation ----------------------
    def _on_negotiation_needed(self, element, *args):
        if self._negotiating:
            print(f"[RTC][{self.sender_name}] skip offer: already negotiating"); return
        GLib.idle_add(lambda: self._maybe_create_offer())

    def _maybe_create_offer(self):
        if self._negotiating: return False
        self._negotiating = True
        def _do():
            p = Gst.Promise.new_with_change_func(self._on_offer_created, self.webrtc)
            self.webrtc.emit('create-offer', None, p)
            return False
        GLib.idle_add(_do)
        return False

    def _on_offer_created(self, promise, element):
        reply = promise.get_reply()
        if not reply: self._negotiating=False; return
        offer = reply.get_value('offer')
        if not offer: self._negotiating=False; return
        self._pending_offer_sdp = offer.sdp.as_text()
        p2 = Gst.Promise.new_with_change_func(self._on_local_desc_set, element)
        element.emit('set-local-description', offer, p2)

    def _on_local_desc_set(self, promise, element):
        print(f"[RTC][{self.sender_name}] Local description set (offer)")
        if self._gst_playing and self.sender_id:
            self._send_offer()
        self._negotiating = False

    def _send_offer(self):
        if not self._pending_offer_sdp:
            return
        self.sio.emit('signal', {
            'to': self.sender_id,
            'from': self.sio.sid,
            'type': 'offer',
            'payload': {'type': 'offer', 'sdp': self._pending_offer_sdp}
        })
        print(f'[SIO][{self.sender_name}] offer 전송 → {self.sender_id}')

    def apply_remote_answer(self, sdp_text: str):
        ok, sdpmsg = GstSdp.SDPMessage.new()
        if ok != GstSdp.SDPResult.OK: return False
        GstSdp.sdp_message_parse_buffer(sdp_text.encode('utf-8'), sdpmsg)
        answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg)
        self.webrtc.emit('set-remote-description', answer, None)
        print(f"[RTC][{self.sender_name}] Remote ANSWER 적용 완료")
        return False

    def on_ice_candidate(self, element, mlineindex, candidate):
        self.sio.emit('signal', {
            'to': self.sender_id,
            'from': self.sio.sid,
            'type': 'candidate',
            'payload': {'candidate': candidate,
                        'sdpMid': f"video{mlineindex}",
                        'sdpMLineIndex': int(mlineindex)}
        })

    # ---------- Media 수신 및 렌더링 ----------
    def on_incoming_stream(self, webrtc, pad):
        caps = pad.get_current_caps().to_string()
        print("Streaming Start!")
        if not caps.startswith("application/x-rtp"):
            return

        depay = _make("rtph264depay")
        parse = _make("h264parse")
        decoder, conv, sink = get_decoder_and_sink()

        q = _make("queue")
        fpssink = _make("fpsdisplaysink")
        if fpssink:
            fpssink.set_property("signal-fps-measurements", True)
            fpssink.set_property("text-overlay", False)
            if sink:
                fpssink.set_property("video-sink", sink)
            fpssink.connect("fps-measurements",
                lambda el, fps, drop, avg:
                    print(f"[STATS][{self.sender_name}] FPS={fps:.2f}, drop={drop:.2f}, avg={avg:.2f}")
            )

        if not all([depay, parse, decoder, conv, sink, fpssink, q]):
            print(f"[RTC][{self.sender_name}] 요소 부족으로 링크 실패"); return

        for e in (depay, parse, decoder, conv, q, fpssink):
            self.pipeline.add(e); e.sync_state_with_parent()

        if pad.link(depay.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            print(f"[RTC][{self.sender_name}] pad link 실패"); return

        depay.link(parse)
        parse.link(decoder)
        decoder.link(conv)
        conv.link(q)
        q.link(fpssink)

        prev = self._visible
        self._display_bin = fpssink
        self.set_visible(prev)

        print(f"[OK][{self.sender_name}] Incoming video linked → {decoder.name}")

    def set_visible(self, on: bool):
        self._visible = bool(on)
        if not self._display_bin:
            return
        try:
            self._display_bin.set_state(Gst.State.PLAYING if on else Gst.State.PAUSED)
        except Exception as e:
            print(f"[GST][{self.sender_name}] set_visible error:", e)

    def log_receiver_stats(self):
        def on_stats(promise, element):
            reply = promise.get_reply()
            if not reply: return
            stats = reply.get_value("stats")
            for k, v in stats.items():
                if "inbound-rtp" in k and v.get("mediaType") == "video":
                    print(f"[STATS][{self.sender_name}] recv_bytes={v.get('bytesReceived')}, "
                          f"framesDecoded={v.get('framesDecoded')}, "
                          f"jitter={v.get('jitter')}, "
                          f"packetsLost={v.get('packetsLost')}")
        p = Gst.Promise.new_with_change_func(on_stats, self.webrtc)
        self.webrtc.emit("get-stats", None, p)
        GLib.timeout_add_seconds(2, lambda: (self.log_receiver_stats() or True))


# ================================================================
# MultiReceiverManager
# ================================================================
class MultiReceiverManager:
    def __init__(self, ui_window: ReceiverWindow):
        self.ui = ui_window
        self.sio = socketio.Client(logger=False, engineio_logger=False)
        self.peers = {}
        self.active_sender_id = None
        self._bind_socket_events()

    def start(self):
        threading.Thread(target=self._sio_connect, daemon=True).start()

    def stop(self):
        try:
            for pid, peer in list(self.peers.items()):
                peer.stop()
        except: pass
        try:
            if self.sio.connected:
                self.sio.disconnect()
        except: pass

    def _set_active_sender(self, sid):
        self.active_sender_id = sid
        for pid, peer in self.peers.items():
            peer.set_visible(pid == sid)
        if sid:
            print(f"[VIEW] now showing sender: {sid}")
        GLib.idle_add(self.ui.set_active_sender, sid)

    def _sio_connect(self):
        try:
            self.sio.connect(SIGNALING_URL, transports=['websocket'])
            self.sio.wait()
        except Exception as e:
            print("[SIO] connect error:", e)

    def _bind_socket_events(self):
        @self.sio.event
        def connect():
            print("[SIO] connected:", self.sio.sid)
            self.sio.emit('join-room',
                          {'role':'receiver','password':ROOM_PASSWORD,'name':RECEIVER_NAME},
                          callback=lambda ack: print("[SIO] join-room ack:", ack))

        @self.sio.on('sender-list')
        def on_sender_list(sender_arr):
            print("[SIO] sender-list:", sender_arr)
            if not sender_arr:
                print("[SIO] sender 없음. 대기."); return

            for s in sender_arr:
                sid = s.get('id')
                name = s.get('name', sid)
                if sid in self.peers:
                    continue

                # ▶ UI 스레드에서 비디오 위젯/핸들 준비
                GLib.idle_add(self.ui.ensure_widget, sid, name)

                peer = PeerReceiver(self.sio, sid, name, self.ui)
                self.peers[sid] = peer

                # ▶ 핸들 캐시 (UI 스레드)
                GLib.idle_add(peer.prepare_window_handle)

                peer.start()
                GLib.idle_add(lambda p=peer: (p._ensure_transceivers(), p._maybe_create_offer()))
                if self.active_sender_id is None:
                    self._set_active_sender(sid)

                self.sio.emit('share-request', {'to': sid})
                print(f"[SIO] share-request → {sid} ({name})")

        @self.sio.on('sender-share-started')
        def on_sender_share_started(data):
            sid = data.get('id') or data.get('from')
            if not sid or sid not in self.peers:
                print("[SIO] share-started from unknown sender:", data); return
            peer = self.peers[sid]
            peer._sender_ready = True
            if peer._gst_playing:
                GLib.idle_add(lambda: peer._maybe_create_offer())
            if self.active_sender_id is None:
                self._set_active_sender(sid)
            print(f"[SIO] sender-share-started: {peer.sender_name}")

        @self.sio.on('signal')
        def on_signal(data):
            typ, frm, payload = data.get('type'), data.get('from'), data.get('payload')
            print("[SIO] signal recv:", typ, "from", frm)
            if not frm or frm not in self.peers:
                print("[SIO] unknown sender in signal:", frm); return
            peer = self.peers[frm]

            if typ == 'answer' and payload:
                sdp_text = payload['sdp'] if isinstance(payload, dict) else payload
                GLib.idle_add(peer.apply_remote_answer, sdp_text)
            elif typ == 'candidate' and payload:
                cand  = payload.get('candidate')
                mline = int(payload.get('sdpMLineIndex') or 0)
                if cand is not None:
                    GLib.idle_add(peer.webrtc.emit, 'add-ice-candidate', mline, cand)

        @self.sio.on('sender-share-stopped')
        def on_sender_share_stopped(data):
            sid = data.get('id') or data.get('from')
            if sid in self.peers:
                print(f"[SIO] sender-share-stopped: {sid}")
                was_active = (sid == self.active_sender_id)
                peer = self.peers.pop(sid)
                peer.stop()
                if was_active:
                    next_sid = next(iter(self.peers.keys()), None)
                    self._set_active_sender(next_sid)


# ---------- GLib ↔ PyQt 이벤트루프 통합 ----------
def integrate_glib_into_qt():
    ctx = GLib.MainContext.default()
    timer = QtCore.QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ctx.iteration(False))
    timer.start()
    return timer


# ---------- main ----------
if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    ui = ReceiverWindow()
    ui.show()  # 필요 시 ui.showFullScreen()

    _glib_timer = integrate_glib_into_qt()

    manager = MultiReceiverManager(ui)
    manager.start()

    app.aboutToQuit.connect(manager.stop)

    def _quit(*_):
        try: manager.stop()
        except: pass
        QtWidgets.QApplication.quit()
    signal.signal(signal.SIGINT, _quit)
    signal.signal(signal.SIGTERM, _quit)

    print("[MAIN] PyQt5 + GStreamer (Overlay) event loop started.")
    sys.exit(app.exec_())