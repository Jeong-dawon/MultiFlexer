import sys, signal, threading
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


class Receiver:
    """WebRTC 수신기: webrtcbin + (depay→parse→decode) + PyQt 임베드"""
    def __init__(self, video_widget=None):
        # 상태
        self._negotiating = False
        self._gst_playing = False
        self._pending_offer_sdp = None
        self._transceivers = []
        self._transceivers_added = False

        # ★ 선택 1 핵심 플래그들
        self._sender_ready = False
        self._pending_offer_after_playing = False

        self.video_widget = video_widget

        # 파이프라인
        self.pipeline = Gst.Pipeline.new("webrtc-pipeline")
        self.webrtc = _make("webrtcbin")
        if not self.webrtc:
            raise RuntimeError("webrtcbin 생성 실패(플러그인/경로 확인)")
        self.pipeline.add(self.webrtc)

        # STUN
        self.webrtc.set_property('stun-server', 'stun://stun.l.google.com:19302')

        # webrtcbin 시그널
        self.webrtc.connect('notify::ice-connection-state', self._on_ice_conn_change)
        self.webrtc.connect('on-ice-candidate', self.on_ice_candidate)
        self.webrtc.connect('pad-added', self.on_incoming_stream)
        self.webrtc.connect('on-negotiation-needed', self._on_negotiation_needed)

        # 버스
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::state-changed", self._on_state_changed)
        bus.connect("message::error", self._on_error)

        # 시그널링
        self.sio = socketio.Client(logger=False, engineio_logger=False)
        self._bind_socket_events()

    # ---------- helpers ----------
    # ICE 상태 콜백 — enum 이름 교정
    def _on_ice_conn_change(self, obj, pspec):
        try:
            state = int(self.webrtc.get_property('ice-connection-state'))
        except Exception as e:
            print("[RTC] ICE (receiver): state read error:", e); return
        W = GstWebRTC
        names = {
            int(W.WebRTCICEConnectionState.NEW): 'new',
            int(W.WebRTCICEConnectionState.CHECKING): 'checking',
            int(W.WebRTCICEConnectionState.CONNECTED): 'connected',
            int(W.WebRTCICEConnectionState.COMPLETED): 'completed',
            int(W.WebRTCICEConnectionState.FAILED): 'failed',
            int(W.WebRTCICEConnectionState.DISCONNECTED): 'disconnected',
            int(W.WebRTCICEConnectionState.CLOSED): 'closed',
        }
        print(f"[RTC] ICE (receiver): {names.get(state, state)}")

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
        
        # vp8
        #self._add_recv("application/x-rtp,media=video,encoding-name=VP8,clock-rate=90000,payload=96")
        
        # H264
        self._add_recv("application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,"
               "payload=102,packetization-mode=(string)1,profile-level-id=(string)42e01f")

        self._transceivers_added = True
        # ★ 여기서 더 이상 강제로 offer 만들지 않음 (sender-ready까지 대기)

    # ----- lifecycle -----
    def start(self):
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        print("[GST] set_state ->", ret.value_nick)
        threading.Thread(target=self._sio_connect, daemon=True).start()

    def stop(self):
        try:
            if self.sio.connected:
                self.sio.disconnect()
        except Exception:
            pass
        try:
            self.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass

    # ----- Bus -----
    def _on_state_changed(self, bus, msg):
        if msg.src is self.pipeline:
            _, new, _ = msg.parse_state_changed()
            if new == Gst.State.PLAYING and not self._gst_playing:
                self._gst_playing = True
                print("[GST] pipeline → PLAYING")
                self._ensure_transceivers()
                # sender-ready가 이미 온 경우 보류분 처리
                if self._pending_offer_after_playing and self._sender_ready:
                    self._pending_offer_after_playing = False
                    GLib.idle_add(lambda: self._maybe_create_offer())

    def _on_error(self, bus, msg):
        err, dbg = msg.parse_error()
        print(f"[GST][ERROR] {err.message} (debug: {dbg})")

    # ----- Socket.IO -----
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
            chosen = None
            if PREFERRED_SENDER_ID:
                chosen = next((s for s in sender_arr if s.get('id') == PREFERRED_SENDER_ID), None)
            if not chosen and sender_arr:
                chosen = sender_arr[0]
            if not chosen:
                print("[SIO] 아직 참여한 sender 없음. 대기.")
                return

            self.target_sender_id = chosen['id']
            print(f"[SIO] target sender = {self.target_sender_id} ({chosen.get('name')})")

            # 1) 송신자에게 UI 노출 요청
            self.sio.emit('share-request', {'to': self.target_sender_id})

            # 2) ★ 폴백: 아직 오퍼를 보낸 적이 없고(= SDP 캐시 없음), 이미 PLAYING이면 지금 오퍼 생성
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
            typ = data.get('type')
            frm = data.get('from')
            payload = data.get('payload')
            print("[SIO] signal recv:", typ, "from", frm)

            if typ == 'answer' and payload:
                sdp_text = payload['sdp'] if isinstance(payload, dict) else payload
                GLib.idle_add(self._apply_remote_answer, sdp_text)

            elif typ == 'candidate' and payload:
                cand  = payload.get('candidate')
                mline = int(payload.get('sdpMLineIndex') or 0)
                print("[RECV] candidate recv mline=", mline, "cand head=", (cand or '')[:60])
                if cand is not None:
                    GLib.idle_add(self.webrtc.emit, 'add-ice-candidate', mline, cand)

        @self.sio.event
        def disconnect():
            print("[SIO] disconnected")

    # ----- Negotiation -----
    def _on_negotiation_needed(self, element, *args):
        if not self._gst_playing or not self._sender_ready:
            print("[RTC] negotiation-needed (ignored: waiting sender-ready/PLAYING)")
            return
        if self._negotiating:
            print("[RTC] skip offer: already negotiating"); return
        print("[RTC] on-negotiation-needed → try create offer")
        GLib.idle_add(lambda: self._maybe_create_offer())

    def _maybe_create_offer(self):
        if self._negotiating:
            return False
        self._negotiating = True
        print("[RTC] creating offer...")

        def _do():
            p = Gst.Promise.new_with_change_func(self._on_offer_created, self.webrtc)
            self.webrtc.emit('create-offer', None, p)
            return False
        GLib.idle_add(_do)
        return False

    def _on_offer_created(self, promise, element):
        reply = promise.get_reply()
        if not reply:
            print("[RTC] create-offer: empty reply"); self._negotiating = False; return
        offer = reply.get_value('offer')
        if not offer:
            print("[RTC] create-offer: no offer"); self._negotiating = False; return

        self._pending_offer_sdp = offer.sdp.as_text()

        p2 = Gst.Promise.new_with_change_func(self._on_local_desc_set, element)
        element.emit('set-local-description', offer, p2)

    def _on_local_desc_set(self, promise, element):
        print("[RTC] Local description set (offer)")
        print("---- LOCAL OFFER SDP (head) ----")
        print((self._pending_offer_sdp or "")[:1500])
        print("---- END ----")

        if self.target_sender_id and self._gst_playing:
            self._send_offer()
        else:
            print('[RTC] 아직 전송 보류 (target or PLAYING 대기)')
        self._negotiating = False

    def _send_offer(self):
        if not self._pending_offer_sdp or not self.target_sender_id:
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
        if ok != GstSdp.SDPResult.OK:
            print("[RTC] SDPMessage.new 실패"); return False
        GstSdp.sdp_message_parse_buffer(sdp_text.encode('utf-8'), sdpmsg)
        answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg)
        self.webrtc.emit('set-remote-description', answer, None)
        print("[RTC] Remote ANSWER 적용 완료")
        return False

    # on_ice_candidate — 최종형
    def on_ice_candidate(self, element, mlineindex, candidate):
        mi = int(mlineindex)
        print("[RECV] send candidate mline=", mi)
        if not self.target_sender_id:
            return
        self.sio.emit('signal', {
            'to': self.target_sender_id,
            'from': self.sio.sid,
            'type': 'candidate',
            'payload': {
                'candidate': candidate,       # 문자열 그대로!
                'sdpMid': f"video{mi}",       # Offer의 a=mid와 일치
                'sdpMLineIndex': mi,
            }
        })


    # ----- pad-added: RTP → 디코드 → PyQt 위젯에 출력 -----
    def on_incoming_stream(self, webrtc, pad):
        print("[RTC] pad-added from webrtc:", pad.get_name())
        caps = pad.get_current_caps() or pad.query_caps(None)
        print("[RTC] pad caps:", caps.to_string() if caps else "None")
        if not caps: return
        s = caps.get_structure(0)
        if not s or s.get_string("media") != "video":
            return
        enc = s.get_string("encoding-name")

        if enc == "H264":
            depay = _make("rtph264depay"); parser = _make("h264parse")
            decoder = None
            for name in ["vtdec","nvh264dec","vah264dec","d3d11h264dec","avdec_h264"]:
                if Gst.ElementFactory.find(name):
                    decoder = _make(name); break
        elif enc == "VP8":
            depay = _make("rtpvp8depay"); parser = None; decoder = _make("vp8dec")
        elif enc == "VP9":
            depay = _make("rtpvp9depay"); parser = None; decoder = _make("vp9dec")
        else:
            print("[RTC] Unsupported encoding:", enc); return

        convert = _make("videoconvert")
        sink = _make("glimagesink") or _make("autovideosink")
        if sink:
            sink.set_property("force-aspect-ratio", True)
            try:
                if self.video_widget and hasattr(sink, "set_window_handle"):
                    wid = int(self.video_widget.winId())
                    sink.set_window_handle(wid)
            except Exception as e:
                print("[PYQT] set_window_handle 실패:", e)

        for e in [depay, parser, decoder, convert, sink]:
            if e:
                self.pipeline.add(e); e.sync_state_with_parent()

        chain = [depay] + ([parser] if parser else []) + [decoder, convert, sink]
        for a, b in zip(chain, chain[1:]):
            if not a.link(b):
                print(f"[RTC] link fail: {a.name}->{b.name}"); return

        if pad.link(depay.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            print("[RTC] pad link 실패(webrtc→depay)"); return

        print(f"[OK] Incoming video: {enc} → {decoder.name if decoder else '???'}")


# ---------- PyQt 메인 윈도우 ----------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WebRTC Receiver (PyQt + GStreamer)")
        self.resize(1280, 800)

        self.video = QtWidgets.QWidget(self)
        self.video.setAttribute(QtCore.Qt.WA_NativeWindow)
        self.video.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.video.setMinimumSize(960, 540)
        self.video.setStyleSheet("background-color: black;")
        self.setCentralWidget(self.video)

        self.rx = Receiver(video_widget=self.video)

        self._glib_ctx = GLib.MainContext.default()
        self._glib_pump = QtCore.QTimer(self)
        self._glib_pump.timeout.connect(lambda: self._glib_ctx.iteration(False))
        self._glib_pump.start(10)

        self._sig_pump = QtCore.QTimer(self)
        self._sig_pump.timeout.connect(lambda: None)
        self._sig_pump.start(100)

        self.rx.start()

    def closeEvent(self, e):
        try:
            self._glib_pump.stop(); self._sig_pump.stop()
        except Exception:
            pass
        try:
            self.rx.stop()
        except Exception:
            pass
        super().closeEvent(e)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    signal.signal(signal.SIGINT, lambda *a: app.quit())
    sys.exit(app.exec_())
