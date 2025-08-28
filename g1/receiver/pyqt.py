import sys, signal, threading, os, platform
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstWebRTC', '1.0')
gi.require_version('GstSdp', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GstWebRTC, GstSdp, GLib, GstVideo
import socketio
from PyQt5 import QtWidgets, QtCore

# ---------- 설정 ----------
SIGNALING_URL = "http://localhost:3001"
ROOM_PASSWORD  = "1"
RECEIVER_NAME  = "Receiver-1"
PREFERRED_SENDER_ID = None

Gst.init(None)

def _make(name):
    return Gst.ElementFactory.make(name) if name else None

def _first_available(*names):
    for n in names:
        if Gst.ElementFactory.find(n):
            e = Gst.ElementFactory.make(n)
            if e:
                return e
    return None

# ---------- OS/플랫폼별 하드웨어 디코딩 + Sink 선택 ----------
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

    else:
        decoder = _first_available("avdec_h264")
        conv    = _first_available("videoconvert")
        sink    = _first_available("autovideosink")

    # PyQt5 위젯 안에서만 출력되도록 sink 윈도우 핸들 연결
    if sink and video_widget:
        xid = int(video_widget.winId())
        sink.set_window_handle(xid)

    return decoder, conv, sink


class Receiver:
    """WebRTC 수신기: webrtcbin + (depay→parse→decode) + PyQt 임베드"""
    def __init__(self, video_widget=None):
        self._negotiating = False
        self._gst_playing = False
        self._pending_offer_sdp = None
        self._transceivers = []
        self._transceivers_added = False
        self._sender_ready = False
        self._pending_offer_after_playing = False

        self.video_widget = video_widget

        self.pipeline = Gst.Pipeline.new("webrtc-pipeline")
        self.webrtc = _make("webrtcbin")
        if not self.webrtc:
            raise RuntimeError("webrtcbin 생성 실패")
        self.pipeline.add(self.webrtc)
        self.webrtc.set_property('stun-server', 'stun://stun.l.google.com:19302')

        # webrtcbin 시그널
        self.webrtc.connect('notify::ice-connection-state', self._on_ice_conn_change)
        self.webrtc.connect('on-ice-candidate', self.on_ice_candidate)
        self.webrtc.connect('pad-added', self.on_incoming_stream)
        self.webrtc.connect('on-negotiation-needed', self._on_negotiation_needed)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::state-changed", self._on_state_changed)
        bus.connect("message::error", self._on_error)

        self.sio = socketio.Client(logger=False, engineio_logger=False)
        self._bind_socket_events()

    # ------------------- GStreamer 이벤트 -------------------
    def _on_ice_conn_change(self, obj, pspec):
        try:
            state = int(self.webrtc.get_property('ice-connection-state'))
        except Exception as e:
            print("[RTC] ICE state read error:", e); return
        print(f"[RTC] ICE state: {state}")

    def _add_recv(self, caps_str):
        t = self.webrtc.emit(
            'add-transceiver',
            GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY,
            Gst.Caps.from_string(caps_str)
        )
        self._transceivers.append(t)
        print('[RTC] transceiver added:', caps_str, '->', bool(t))

    def _ensure_transceivers(self):
        if self._transceivers_added:
            return
        self._add_recv("application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,"
                       "payload=102,packetization-mode=(string)1,profile-level-id=(string)42e01f")
        self._transceivers_added = True

    def start(self):
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        print("[GST] set_state ->", ret.value_nick)
        threading.Thread(target=self._sio_connect, daemon=True).start()

    def stop(self):
        try:
            if self.sio.connected:
                self.sio.disconnect()
        except: pass
        try:
            self.pipeline.set_state(Gst.State.NULL)
        except: pass

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

    def _on_error(self, bus, msg):
        err, dbg = msg.parse_error()
        print(f"[GST][ERROR] {err.message} (debug: {dbg})")

    # ------------------- Socket.IO -------------------
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
            chosen = sender_arr[0]
            self.target_sender_id = chosen['id']
            print(f"[SIO] target sender = {self.target_sender_id} ({chosen.get('name')})")
            self.sio.emit('share-request', {'to': self.target_sender_id})
            if self._gst_playing and not self._pending_offer_sdp:
                GLib.idle_add(lambda: self._maybe_create_offer())

        @self.sio.on('sender-share-started')
        def on_sender_share_started(data):
            print("[SIO] sender-share-started:", data)
            self._sender_ready = True
            if self._gst_playing:
                GLib.idle_add(lambda: self._maybe_create_offer())
            else:
                self._pending_offer_after_playing = True

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
    def _on_negotiation_needed(self, element, *args):
        if not self._gst_playing or not self._sender_ready:
            print("[RTC] negotiation-needed (ignored)")
            return
        if self._negotiating:
            print("[RTC] skip offer: already negotiating"); return
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
        print("[RTC] Local description set (offer)")
        if hasattr(self, 'target_sender_id') and self._gst_playing:
            self._send_offer()
        self._negotiating = False

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

    def _apply_remote_answer(self, sdp_text: str):
        ok, sdpmsg = GstSdp.SDPMessage.new()
        if ok != GstSdp.SDPResult.OK: return False
        GstSdp.sdp_message_parse_buffer(sdp_text.encode('utf-8'), sdpmsg)
        answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg)
        self.webrtc.emit('set-remote-description', answer, None)
        print("[RTC] Remote ANSWER 적용 완료")
        return False

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

    # ------------------- RTP Pad -------------------
    def on_incoming_stream(self, webrtc, pad):
        caps = pad.get_current_caps().to_string()
        print("[RTC] pad caps:", caps)

        if caps.startswith("application/x-rtp"):
            depay = _make("rtph264depay")
            parse = _make("h264parse")
            decoder, conv, sink = get_decoder_and_sink(self.video_widget)

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
            print(f"[OK] Incoming video linked → {decoder.name}")


# ---------- PyQt 메인 ----------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WebRTC Receiver (PyQt + GStreamer)")
        self.resize(1280, 800)
        self.video = QtWidgets.QWidget(self)
        self.video.setAttribute(QtCore.Qt.WA_NativeWindow)
        self.video.setStyleSheet("background-color: black;")
        self.setCentralWidget(self.video)
        self.rx = Receiver(video_widget=self.video)

        self._glib_ctx = GLib.MainContext.default()
        self._glib_pump = QtCore.QTimer(self)
        self._glib_pump.timeout.connect(lambda: self._glib_ctx.iteration(False))
        self._glib_pump.start(10)
        self.rx.start()

    def closeEvent(self, e):
        try: self._glib_pump.stop()
        except: pass
        try: self.rx.stop()
        except: pass
        super().closeEvent(e)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow(); win.show()
    signal.signal(signal.SIGINT, lambda *a: app.quit())
    sys.exit(app.exec_())
