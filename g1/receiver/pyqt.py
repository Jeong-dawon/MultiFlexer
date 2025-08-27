# mainframe에 바로 송출하는 버전
# receiver - main.py
# -*- coding: utf-8 -*-
import os, sys, signal, threading
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstWebRTC', '1.0')
gi.require_version('GstSdp', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GstWebRTC, GstSdp, GLib, GstVideo

from PyQt5 import QtCore, QtGui, QtWidgets
import socketio

# ------------------- 설정 -------------------
SIGNALING_URL = os.environ.get("SIGNALING_URL", "http://localhost:3001")
RECEIVER_NAME = os.environ.get("RECEIVER_NAME", "Receiver-PyQt")
USE_TURN      = False
TURN_URL      = os.environ.get("TURN_URL", "turn://user:pass@your.turn.host:3478")

def map_brightness(percent):
    try:
        p = max(50, min(150, int(percent)))
    except:
        p = 100
    return (p - 100) / 100.0

Gst.init(None)

def make(name):
    return Gst.ElementFactory.make(name) if name else None

# ============================================================
# 브릿지: 소켓 스레드 → Qt 메인 스레드 안전 전달용
# ============================================================
class EventBridge(QtCore.QObject):
    sender_list     = QtCore.pyqtSignal(list)          # [ {id,name}, ... ]
    remove_sender   = QtCore.pyqtSignal(str)           # senderId
    share_started   = QtCore.pyqtSignal(str, str)      # senderId, name
    answer          = QtCore.pyqtSignal(str, str)      # from, sdp_text
    candidate       = QtCore.pyqtSignal(str, int, str) # from, mline, cand

# ------------------- 비디오 위젯 -------------------
class VideoWidget(QtWidgets.QWidget):
    def __init__(self, parent=None, title=None, thumbnail=False):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_NativeWindow)
        self.setAutoFillBackground(False)
        self.setMinimumSize(160, 90 if thumbnail else 240)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QtGui.QColor("black"))
        self.setPalette(pal)
        if title:
            self.setToolTip(title)

    def winid_int(self):
        wid = int(self.winId() or 0)
        return wid

# ------------------- Snap 레이아웃 컨테이너 -------------------
class SnapFrame(QtWidgets.QFrame):
    dropped = QtCore.pyqtSignal(str, str)  # (sender_id, position)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setStyleSheet("background:#111; border-radius:18px;")
        self.setMinimumHeight(560)
        self.positions = {
            "full":       QtCore.QRectF(0, 0, 1.0, 1.0),
            "left":       QtCore.QRectF(0, 0, 0.5, 1.0),
            "right":      QtCore.QRectF(0.5, 0, 0.5, 1.0),
            "small-left": QtCore.QRectF(0, 0, 1/3, 1.0),
            "big-right":  QtCore.QRectF(1/3, 0, 2/3, 1.0),
            "topleft":    QtCore.QRectF(0, 0, 0.5, 0.5),
            "topright":   QtCore.QRectF(0.5, 0, 0.5, 0.5),
            "bottomleft": QtCore.QRectF(0, 0.5, 0.5, 0.5),
            "bottomright":QtCore.QRectF(0.5, 0.5, 0.5, 0.5),
        }
        self.hover_pos = None
        self._assigned = {}  # position -> (sender_id, VideoWidget)

    def _pos_from_point(self, pos: QtCore.QPoint):
        w, h = self.width(), self.height()
        x, y = pos.x()/max(1,w), pos.y()/max(1,h)
        for name, r in self.positions.items():
            if r.contains(x, y):
                return name
        return None

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor(255,255,255,60), 2, QtCore.Qt.SolidLine)
        p.setPen(pen)
        for name, rect in self.positions.items():
            rr = QtCore.QRectF(rect.x()*self.width(), rect.y()*self.height(),
                               rect.width()*self.width(), rect.height()*self.height())
            p.drawRoundedRect(rr, 12, 12)
        if self.hover_pos:
            r = self.positions[self.hover_pos]
            rr = QtCore.QRectF(r.x()*self.width(), r.y()*self.height(),
                               r.width()*self.width(), r.height()*self.height())
            p.fillRect(rr, QtGui.QColor(4,210,175,60))

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent):
        if e.mimeData().hasFormat("application/x-sender-id"):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        self.hover_pos = self._pos_from_point(e.pos())
        self.update()

    def dragLeaveEvent(self, e):
        self.hover_pos = None
        self.update()

    def dropEvent(self, e: QtGui.QDropEvent):
        sid = str(e.mimeData().data("application/x-sender-id"), "utf-8")
        pos = self._pos_from_point(e.pos())
        self.hover_pos = None
        self.update()
        if sid and pos:
            self.dropped.emit(sid, pos)

    def assign_widget(self, sender_id: str, widget: VideoWidget, position: str):
        for pos, (sid, w) in list(self._assigned.items()):
            if pos == position:
                w.setParent(None)
                w.deleteLater()
                del self._assigned[pos]
        r = self.positions[position]
        ww = int(r.width()*self.width())
        hh = int(r.height()*self.height())
        xx = int(r.x()*self.width())
        yy = int(r.y()*self.height())
        widget.setParent(self)
        widget.setGeometry(xx, yy, ww, hh)
        widget.show()
        self._assigned[position] = (sender_id, widget)

# ------------------- Peer (송신자별 webrtcbin) -------------------
class Peer(QtCore.QObject):
    ice_candidate_out = QtCore.pyqtSignal(str, int, str)  # (sender_id, mline, cand_str)
    offer_ready = QtCore.pyqtSignal(str, str)             # (sender_id, sdp_text)
    first_frame = QtCore.pyqtSignal(str)                  # (sender_id)

    def __init__(self, sender_id: str, sender_name: str, parent=None):
        super().__init__(parent)
        self.sender_id = sender_id
        self.sender_name = sender_name

        self.pipeline = Gst.Pipeline.new(f"pipe-{sender_id[:6]}")
        self.webrtc = make("webrtcbin")
        if not self.webrtc:
            raise RuntimeError("webrtcbin not found")
        self.pipeline.add(self.webrtc)

        # STUN/TURN
        self.webrtc.set_property('stun-server', 'stun://stun.l.google.com:19302')
        if USE_TURN:
            self.webrtc.set_property('turn-server', TURN_URL)

        # 콜백
        self.webrtc.connect('on-ice-candidate', self._on_ice_candidate)
        self.webrtc.connect('pad-added', self._on_pad_added)
        self.webrtc.connect('on-negotiation-needed', self._on_negotiation_needed)
        self.webrtc.connect('notify::ice-connection-state', self._on_ice_state)

        # 🔸 중복 오퍼 방지 플래그 초기화
        self._negotiating = False

        # 수신 코덱(H264) 지정
        h264_caps = (
            "application/x-rtp, "
            "media=(string)video, encoding-name=(string)H264, "
            "clock-rate=(int)90000, "
            "packetization-mode=(string)1"
        )
        self.webrtc.emit(
            "add-transceiver",
            GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY,
            Gst.Caps.from_string(h264_caps),
        )

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)

        self._local_sdp = None
        self._started = False

        self._branches_ready = False
        self.thumb_balance = None
        self.thumb_sink = None
        self.main_balance  = None
        self.main_sink  = None

        # !! __init__ 끝부분에 추가
        self._negotiating = False


    def start(self):
        if self._started:
            return
        self.pipeline.set_state(Gst.State.PLAYING)
        self._started = True

    def stop(self):
        try:
            self.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass

    # --- WebRTC ---
    def _on_negotiation_needed(self, webrtc):
        if self._negotiating:
            return
        self._negotiating = True

        def _on_offer_created(promise, _):
            try:
                promise.wait()
                reply = promise.get_reply()
                offer = reply.get_value('offer')
                webrtc.emit('set-local-description', offer, None)
                sdp_text = offer.sdp.as_text()
                # ✅ MainWindow가 소켓으로 내보내도록 시그널 emit
                self.offer_ready.emit(self.sender_id, sdp_text)
            finally:
                self._negotiating = False

        promise = Gst.Promise.new_with_change_func(_on_offer_created, None)
        webrtc.emit('create-offer', None, promise)

    def apply_remote_answer(self, sdp_text: str):
        if not isinstance(sdp_text, str):
            print("[Peer] apply_remote_answer: not a string payload:", type(sdp_text)); return
        if "v=" not in sdp_text or "m=" not in sdp_text:
            print("[Peer] apply_remote_answer: malformed SDP\n", sdp_text[:200], "...")
            return
        res, sdpmsg = GstSdp.SDPMessage.new()
        if res != GstSdp.SDPResult.OK:
            print("[Peer] SDPMessage.new failed:", res); return
        res = GstSdp.sdp_message_parse_buffer(sdp_text.encode("utf-8"), sdpmsg)
        if res != GstSdp.SDPResult.OK:
            print("[Peer] sdp_message_parse_buffer failed:", res); return
        answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg
        )
        def _on_set_remote_done(promise, _):
            print(f"[Peer {self.sender_id[:6]}] Remote ANSWER applied")
        promise = Gst.Promise.new_with_change_func(_on_set_remote_done, None)
        self.webrtc.emit("set-remote-description", answer, promise)

    def add_remote_candidate(self, mline: int, cand: str):
        self.webrtc.emit('add-ice-candidate', int(mline), cand)

    def _on_ice_candidate(self, webrtc, mlineindex, candidate):
        # ✅ MainWindow가 소켓으로 내보내도록 시그널 emit
        self.ice_candidate_out.emit(self.sender_id, int(mlineindex), candidate or "")

    def _on_ice_state(self, obj, pspec):
        try:
            st = int(self.webrtc.get_property('ice-connection-state'))
        except Exception:
            return
        name = {
            int(GstWebRTC.WebRTCICEConnectionState.NEW): 'new',
            int(GstWebRTC.WebRTCICEConnectionState.CHECKING): 'checking',
            int(GstWebRTC.WebRTCICEConnectionState.CONNECTED): 'connected',
            int(GstWebRTC.WebRTCICEConnectionState.COMPLETED): 'completed',
            int(GstWebRTC.WebRTCICEConnectionState.FAILED): 'failed',
            int(GstWebRTC.WebRTCICEConnectionState.DISCONNECTED): 'disconnected',
            int(GstWebRTC.WebRTCICEConnectionState.CLOSED): 'closed',
        }.get(st, str(st))
        print(f"[Peer {self.sender_id[:6]}] ICE:", name)

    def _on_error(self, bus, msg):
        err, dbg = msg.parse_error()
        print(f"[GST][Peer {self.sender_id[:6]}][ERROR] {err.message} (debug: {dbg})")

    # --- pad-added: 메인 루프로 디코드 체인 구축 ---
    def _on_pad_added(self, webrtc, pad: Gst.Pad):
        caps = pad.get_current_caps() or pad.query_caps(None)
        st = caps.get_structure(0) if caps else None
        if not st or st.get_string("media") != "video":
            return
        enc = (st.get_string("encoding-name") or "").upper()
        pad_ref = pad
        GLib.idle_add(self._build_decode_chain, enc, pad_ref)

    def _build_decode_chain(self, enc: str, pad: Gst.Pad):
        if enc == "H264":
            depay, parser = make("rtph264depay"), make("h264parse")
            decoder = None
            for name in ["nvh264dec","vah264dec","d3d11h264dec","vtdec","avdec_h264"]:
                if Gst.ElementFactory.find(name):
                    decoder = make(name); break
            if decoder is None:
                print("[Peer] No H264 decoder available"); return False
        elif enc == "VP8":
            depay, parser, decoder = make("rtpvp8depay"), None, make("vp8dec")
        else:
            print("[Peer] Unsupported encoding:", enc); return False

        convert = make("videoconvert")
        tee     = make("tee")
        q1      = make("queue")
        q1.set_property("leaky", 2); q1.set_property("max-size-buffers", 1)

        self.thumb_balance = make("videobalance")
        self._apply_brightness()

        self.thumb_sink = make("glimagesink") or make("autovideosink")
        try:
            if self.thumb_sink:
                self.thumb_sink.set_property("force-aspect-ratio", True)
        except Exception:
            pass

        for e in filter(None, [depay, parser, decoder, convert, tee, q1, self.thumb_balance, self.thumb_sink]):
            self.pipeline.add(e); e.sync_state_with_parent()

        chain = [depay] + ([parser] if parser else []) + [decoder, convert, tee]
        for a, b in zip(chain, chain[1:]):
            if not a.link(b):
                print("[Peer] link fail:", a.name, "->", b.name); return False
        if not tee.link(q1):
            print("[Peer] tee link fail"); return False
        if not q1.link(self.thumb_balance) or not self.thumb_balance.link(self.thumb_sink):
            print("[Peer] thumb branch link fail"); return False

        if pad.link(depay.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            print("[Peer] pad link 실패(webrtc→depay)"); return False

        self._branches_ready = True
        print(f"[Peer {self.sender_id[:6]}] Incoming video: {enc}")
        self.first_frame.emit(self.sender_id)
        return False

    # --- Overlay attach ---
    def _attach_overlay(self, sink, widget):
        if not sink or not widget:
            return
        wid = widget.winid_int()
        if not wid:
            QtCore.QTimer.singleShot(50, lambda: self._attach_overlay(sink, widget))
            return
        try:
            GstVideo.VideoOverlay.prepare_window_handle(sink)
        except Exception:
            pass
        try:
            GstVideo.VideoOverlay.set_window_handle(sink, wid)
        except Exception as e:
            print("[Overlay] set_window_handle error:", e)

    def attach_thumb_to(self, widget: VideoWidget):
        self._attach_overlay(self.thumb_sink, widget)

    def attach_main_to(self, widget: VideoWidget):
        self._attach_overlay(self.main_sink, widget)

    def _apply_brightness(self):
        val = map_brightness(MainWindow.current_brightness_percent)
        for bal in (self.thumb_balance, self.main_balance):
            if bal:
                try: bal.set_property("brightness", float(val))
                except Exception: pass

# ------------------- 메인 윈도우 -------------------
class MainWindow(QtWidgets.QMainWindow):
    current_brightness_percent = 100
    current_volume_percent = 50

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Realtime Viewer (PyQt + GStreamer)")
        self.resize(1280, 800)

        # GLib 펌프
        self._glib_ctx = GLib.MainContext.default()
        self._glib_timer = QtCore.QTimer(self)
        self._glib_timer.timeout.connect(lambda: self._glib_ctx.iteration(False))
        self._glib_timer.start(10)

        # UI
        self._build_ui()

        # 브릿지
        self.bridge = EventBridge()
        self.bridge.sender_list.connect(self._render_sender_list)
        self.bridge.remove_sender.connect(self._remove_sender_ui_and_peer)
        self.bridge.share_started.connect(self._on_share_started)
        self.bridge.answer.connect(self._apply_answer)
        self.bridge.candidate.connect(self._apply_candidate)

        # 소켓
        self.sio = socketio.Client(logger=False, engineio_logger=False)
        self._bind_socket_events()

        self.peers = {}            # sender_id -> Peer
        self.main_widgets = {}     # ✅ sender_id -> VideoWidget (메인 프레임용)
        self.thumb_widgets = {}    # (더 이상 사용 안 하지만, 기존 코드 호환을 위해 남김)

        self._sio_running = False
        self._pending_join_payload = None
        self.current_room = ""

    # ---------- UI ----------
    def _build_ui(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        v = QtWidgets.QVBoxLayout(root); v.setContentsMargins(12,12,12,12); v.setSpacing(10)

        header = QtWidgets.QFrame(); header.setStyleSheet("background:#0f172a; color:white; border-radius:12px;")
        h = QtWidgets.QHBoxLayout(header); h.setContentsMargins(16,10,16,10)
        self.room_label = QtWidgets.QLabel("ROOM: -"); self.room_label.setStyleSheet("font-weight:600;")
        title = QtWidgets.QLabel("실시간 화면 공유"); title.setStyleSheet("font-size:18px; font-weight:600;")
        h.addWidget(title); h.addStretch(1); h.addWidget(self.room_label)
        header.setVisible(False)
        v.addWidget(header)
        self.header = header

        bar = QtWidgets.QFrame(); hb = QtWidgets.QHBoxLayout(bar); hb.setContentsMargins(0,0,0,0); hb.setSpacing(8)
        self.password_edit = QtWidgets.QLineEdit(); self.password_edit.setPlaceholderText("방 비밀번호 입력")
        self.btn_join = QtWidgets.QPushButton("변경")
        self.btn_delete = QtWidgets.QPushButton("삭제")
        self.btn_refresh = QtWidgets.QPushButton("새로고침")
        self.btn_full = QtWidgets.QPushButton("⛶")
        self.btn_theme = QtWidgets.QPushButton("☀️")
        self.btn_settings = QtWidgets.QPushButton("⚙️")
        for b in (self.btn_join, self.btn_delete, self.btn_refresh, self.btn_full, self.btn_theme, self.btn_settings):
            b.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        hb.addWidget(self.password_edit); hb.addWidget(self.btn_join); hb.addWidget(self.btn_delete)
        hb.addStretch(1); hb.addWidget(self.btn_refresh); hb.addWidget(self.btn_full); hb.addWidget(self.btn_theme); hb.addWidget(self.btn_settings)
        v.addWidget(bar)

        self.sender_list = QtWidgets.QVBoxLayout()
        senders_box = QtWidgets.QGroupBox("현재 연결된 송신자")
        senders_w = QtWidgets.QWidget(); senders_w.setLayout(self.sender_list)
        gb_v = QtWidgets.QVBoxLayout(senders_box); gb_v.addWidget(senders_w)
        v.addWidget(senders_box)

        self.snap = SnapFrame()
        v.addWidget(self.snap, 1)

        thumb_area = QtWidgets.QScrollArea(); thumb_area.setWidgetResizable(True)
        thumb_host = QtWidgets.QWidget(); self.thumb_row = QtWidgets.QHBoxLayout(thumb_host)
        self.thumb_row.setContentsMargins(8,8,8,8); self.thumb_row.setSpacing(12)
        thumb_area.setWidget(thumb_host)
        v.addWidget(QtWidgets.QLabel("보내는 화면 목록")); v.addWidget(thumb_area)

        self.start_card = QtWidgets.QDialog(self); self.start_card.setModal(True)
        self.start_card.setWindowTitle("방 입장")
        form = QtWidgets.QFormLayout(self.start_card)
        self.card_password = QtWidgets.QLineEdit(); self.card_password.setPlaceholderText("방 비밀번호 입력")
        btn_enter = QtWidgets.QPushButton("입장하기"); btn_enter.setDefault(True)
        form.addRow(QtWidgets.QLabel("<b>MultiFlexer</b><br>방 생성 / 입장"))
        form.addRow("비밀번호", self.card_password); form.addRow(btn_enter)
        btn_enter.clicked.connect(self._enter_room_from_card)

        self.btn_join.clicked.connect(self._join_or_change_room)
        self.btn_delete.clicked.connect(self._delete_room)
        self.btn_refresh.clicked.connect(lambda: QtWidgets.QApplication.instance().exit(11))
        self.btn_full.clicked.connect(self._toggle_fullscreen)
        self.btn_theme.clicked.connect(self._toggle_theme)
        self.btn_settings.clicked.connect(self._open_settings)
        self.snap.dropped.connect(self._on_dropped_to_cell)

    def showEvent(self, e):
        super().showEvent(e)
        QtCore.QTimer.singleShot(50, lambda: self.start_card.show())

    # ---------- 방 제어 ----------
    def _enter_room_from_card(self):
        pw = self.card_password.text().strip()
        if not pw:
            QtWidgets.QMessageBox.warning(self, "안내", "비밀번호를 입력하세요.")
            return
        self.start_card.accept()
        self._connect_socket()
        self._join_room(pw)

    def _join_or_change_room(self):
        pw = self.password_edit.text().strip()
        if not pw:
            QtWidgets.QMessageBox.information(self, "안내", "비밀번호를 입력하세요.")
            return
        if self.current_room:
            if pw == self.current_room:
                QtWidgets.QMessageBox.information(self, "안내", "같은 방입니다.")
            else:
                self._change_room(pw)
        else:
            self._join_room(pw)

    def _join_room(self, password: str):
        self.current_room = password
        self.room_label.setText(f"ROOM: {password}")
        self.header.setVisible(True)
        self._queue_join({'role':'receiver','password':password,'name':RECEIVER_NAME})

    def _change_room(self, new_password: str):
        self._emit('del-room', {'role':'receiver'})
        self._teardown_all_peers()
        self.current_room = new_password
        self.room_label.setText(f"ROOM: {new_password}")
        self._queue_join({'role':'receiver','password':new_password,'name':RECEIVER_NAME})
        QtWidgets.QMessageBox.information(self, "안내", "방 비밀번호가 변경되었습니다.")

    def _delete_room(self):
        if not self.current_room:
            return
        self._emit('del-room', {'role':'receiver'})
        self._teardown_all_peers()
        self.current_room = ""
        self.room_label.setText("ROOM: -")
        QtWidgets.QMessageBox.information(self, "안내", "방이 삭제되었습니다.")

    # ---------- 소켓 ----------
    def _connect_socket(self):
        if getattr(self, "_sio_running", False):
            return
        def run():
            try:
                self.sio.connect(SIGNALING_URL, transports=['websocket'])
                self.sio.wait()
            except Exception as e:
                print("[SIO] connect error:", e)
        threading.Thread(target=run, daemon=True).start()
        self._sio_running = True

    def _emit(self, ev, payload):
        try:
            self.sio.emit(ev, payload)
        except Exception as e:
            print("[SIO] emit error", ev, e)

    def _queue_join(self, payload: dict):
        self._pending_join_payload = payload
        self._try_send_join()

    def _try_send_join(self):
        if self.sio.connected and self._pending_join_payload:
            payload = self._pending_join_payload
            self._pending_join_payload = None
            try:
                self.sio.emit('join-room', payload)
            except Exception as e:
                print('[SIO] emit join-room failed:', e)

    def _bind_socket_events(self):
        @self.sio.event
        def connect():
            print("[SIO] connected", self.sio.sid)
            QtCore.QTimer.singleShot(0, self._try_send_join)

        @self.sio.on('sender-list')
        def on_sender_list(sender_arr):
            print("[SIO] sender-list:", sender_arr)
            self.bridge.sender_list.emit(sender_arr or [])

        @self.sio.on('remove-sender')
        def on_remove_sender(sender_id):
            print("[SIO] remove-sender", sender_id)
            self.bridge.remove_sender.emit(sender_id)

        @self.sio.on('sender-share-started')
        def on_sender_share_started(data):
            sid = data.get('senderId'); name = data.get('name') or sid
            print("[SIO] sender-share-started", sid)
            self.bridge.share_started.emit(sid, name)

        @self.sio.on('signal')
        def on_signal(data):
            typ = data.get('type')
            frm = data.get('from')
            payload = data.get('payload') or {}
            if typ == 'answer' and frm:
                sdp_text = ""
                if isinstance(payload, dict):
                    sdp_text = payload.get("sdp") or ""
                elif isinstance(payload, str):
                    sdp_text = payload
                if not sdp_text:
                    print("[SIO] answer without sdp, ignored:", type(payload))
                    return
                self.bridge.answer.emit(frm, sdp_text)
            elif typ == 'candidate' and frm:
                cand = payload.get('candidate')
                mline = int(payload.get('sdpMLineIndex') or 0)
                if cand is not None:
                    self.bridge.candidate.emit(frm, mline, cand)

        @self.sio.event
        def disconnect():
            print("[SIO] disconnected")

    # ---------- 브릿지 슬롯들(메인 스레드) ----------
    @QtCore.pyqtSlot(list)
    def _render_sender_list(self, sender_arr):
        # 싹 비우고 다시
        for i in reversed(range(self.sender_list.count())):
            w = self.sender_list.itemAt(i).widget()
            if w:
                w.deleteLater()
        for s in sender_arr:
            sid = s.get('id')
            name = s.get('name') or sid
            self._add_sender_row(sid, name)

    @QtCore.pyqtSlot(str)
    def _remove_sender_ui_and_peer(self, sender_id: str):
        row = self.findChild(QtWidgets.QFrame, f"row-{sender_id}")
        if row:
            row.deleteLater()
        # 메인 뷰 제거
        if sender_id in self.main_widgets:
            w = self.main_widgets.pop(sender_id)
            w.setParent(None); w.deleteLater()
        # 기존 썸네일 자료구조도 비워줌(안 쓰지만 혹시 남아있을 수 있으니)
        if sender_id in self.thumb_widgets:
            w = self.thumb_widgets.pop(sender_id)
            w.setParent(None); w.deleteLater()
        p = self.peers.pop(sender_id, None)
        if p: p.stop()

    @QtCore.pyqtSlot(str, str)
    def _on_share_started(self, sender_id: str, name: str):
        self._ensure_peer_and_offer(sender_id, display_name=name)

    @QtCore.pyqtSlot(str, str)
    def _apply_answer(self, frm: str, sdp_text: str):
        p = self.peers.get(frm)
        if p:
            p.apply_remote_answer(sdp_text)

    @QtCore.pyqtSlot(str, int, str)
    def _apply_candidate(self, frm: str, mline: int, cand: str):
        p = self.peers.get(frm)
        if p:
            p.add_remote_candidate(mline, cand)

    # ---------- 송신자 UI/동작 ----------
    def _add_sender_row(self, sender_id: str, name: str):
        row = QtWidgets.QFrame(objectName=f"row-{sender_id}")
        row.setStyleSheet("background:#fff; border-radius:8px;")
        hb = QtWidgets.QHBoxLayout(row); hb.setContentsMargins(10,10,10,10)
        lb = QtWidgets.QLabel(name); lb.setStyleSheet("font-weight:600;")
        btn = QtWidgets.QPushButton("화면 공유 요청")
        btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        btn.clicked.connect(lambda: (self._emit('share-request', {'to': sender_id}),
                                     self._ensure_peer_and_offer(sender_id, display_name=name)))
        hb.addWidget(lb); hb.addStretch(1); hb.addWidget(btn)
        self.sender_list.addWidget(row)

    def _ensure_peer_and_offer(self, sender_id: str, display_name: str = None):
        if not sender_id:
            return
        if sender_id not in self.peers:
            peer = Peer(sender_id, display_name or sender_id)
            self.peers[sender_id] = peer
            peer.ice_candidate_out.connect(self._on_ice_candidate_out)
            peer.offer_ready.connect(self._on_offer_ready)
            peer.first_frame.connect(self._on_first_frame)

            # ✅ 썸네일 대신 즉시 메인 프레임에 풀 화면으로 자리 배치
            vw = VideoWidget(title=(display_name or sender_id))
            self.main_widgets[sender_id] = vw
            self.snap.assign_widget(sender_id, vw, "full")

            peer.start()  # pad-added → first_frame에서 attach

        # webrtcbin이 on-negotiation-needed에서 자동으로 offer 생성

    def _on_first_frame(self, sender_id: str):
        p = self.peers.get(sender_id)
        if not p: return
        vw = self.main_widgets.get(sender_id)
        if vw and p.thumb_sink:
            # 분기 미구현: thumb_sink를 메인 뷰에 바로 붙인다
            p.attach_thumb_to(vw)

    def _on_ice_candidate_out(self, sender_id: str, mline: int, cand: str):
        self._emit('signal', {
            'to': sender_id,
            'from': self.sio.sid if hasattr(self.sio, "sid") else None,
            'type': 'candidate',
            'payload': {'candidate': cand, 'sdpMLineIndex': int(mline)}
        })

    def _on_offer_ready(self, sender_id: str, sdp_text: str):
        self._emit('signal', {
            'to': sender_id,
            'from': self.sio.sid if hasattr(self.sio, "sid") else None,
            'type': 'offer',
            'payload': {'type':'offer', 'sdp': sdp_text}
        })
        print("[SIO] offer 전송 →", sender_id)

    # 드롭 처리: (현재는 미사용) – 기존 UI 유지용
    def _on_dropped_to_cell(self, sender_id: str, position: str):
        pass

    # ---------- 설정/유틸 ----------
    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_theme(self):
        dark = self.palette().color(QtGui.QPalette.Window).value() < 128
        if dark:
            self.setStyleSheet("")
        else:
            self.setStyleSheet("QMainWindow{background:#0f1622; color:#cfcfcf;} QLabel{color:#cfcfcf;} QPushButton{background:#ff8f6b; color:#fff; border-radius:8px; padding:6px 10px;} QLineEdit{background:#1f233a; color:#fff; border:1px solid #444; border-radius:6px; padding:6px;} QGroupBox{color:#cfcfcf;}")

    def _open_settings(self):
        dlg = QtWidgets.QDialog(self); dlg.setWindowTitle("설정")
        lv = QtWidgets.QVBoxLayout(dlg)
        lv.addWidget(QtWidgets.QLabel("밝기"))
        br = QtWidgets.QSlider(QtCore.Qt.Horizontal); br.setMinimum(50); br.setMaximum(150)
        br.setValue(MainWindow.current_brightness_percent)
        lv.addWidget(br)
        lv.addWidget(QtWidgets.QLabel("볼륨(참고: 현재 비디오만 수신)"))
        vol = QtWidgets.QSlider(QtCore.Qt.Horizontal); vol.setMinimum(0); vol.setMaximum(100)
        vol.setValue(MainWindow.current_volume_percent); lv.addWidget(vol)
        btn = QtWidgets.QPushButton("닫기"); lv.addWidget(btn)

        def on_brightness(v):
            MainWindow.current_brightness_percent = v
            for p in self.peers.values():
                p._apply_brightness()
        br.valueChanged.connect(on_brightness)
        btn.clicked.connect(dlg.accept)
        dlg.exec_()

    def _teardown_all_peers(self):
        for sid, p in list(self.peers.items()):
            p.stop()
        self.peers.clear()
        for sid, w in list(self.main_widgets.items()):
            w.setParent(None); w.deleteLater()
        self.main_widgets.clear()
        for sid, w in list(self.thumb_widgets.items()):
            w.setParent(None); w.deleteLater()
        self.thumb_widgets.clear()

# ------------------- 드래그 가능한 썸네일(미사용, 호환 유지) -------------------
class DraggableThumb(QtWidgets.QFrame):
    def __init__(self, sender_id: str, title: str):
        super().__init__()
        self.sender_id = sender_id
        self.setStyleSheet("background:#d1d5db; border-radius:10px;")
        self.setFixedSize(220, 130)
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(8,8,8,8); lay.setSpacing(4)
        self.video = VideoWidget(thumbnail=True)
        name = QtWidgets.QLabel(title)
        name.setStyleSheet("background:rgba(0,0,0,0.4); color:#fff; padding:2px 6px; border-radius:4px;")
        lay.addWidget(self.video, 1); lay.addWidget(name, 0, QtCore.Qt.AlignLeft)

    def mousePressEvent(self, e: QtGui.QMouseEvent):
        pass  # 미사용

# ------------------- 엔트리 -------------------
def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    signal.signal(signal.SIGINT, lambda *a: app.quit())
    rc = app.exec_()
    if rc == 11:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    sys.exit(rc)

if __name__ == "__main__":
    main()
