# 상태바 제거, sender 끊김 반영(sender, server 모두 수정) -> PeerReceiver 삭제
# ================================================================
# Multi-Sender WebRTC Receiver (GStreamer + Socket.IO + PyQt5, Overlay)
# ================================================================

import sys, signal, threading, os, platform, time
import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstWebRTC', '1.0')
gi.require_version('GstSdp', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst, GstWebRTC, GstSdp, GLib, GstVideo
import socketio

from PyQt5 import QtCore, QtWidgets, QtGui

# ---------- 설정 ----------
SIGNALING_URL = "http://localhost:3001"
ROOM_PASSWORD  = "1"
RECEIVER_NAME  = "Receiver-1"

Gst.init(None)


# ---------- PyQt5 UI ----------
class InfoPopup(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent, flags=(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.Tool
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.WindowDoesNotAcceptFocus
        ))
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self._label = QtWidgets.QLabel("", self)
        self._label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._label.setStyleSheet("""
            QLabel { color: white; font-size: 16px; padding: 10px 14px; background: rgba(0,0,0,0); }
        """)
        self.setStyleSheet("""
            QFrame { background: rgba(0,0,0,160); border: 1px solid rgba(255,255,255,90); border-radius: 10px; }
        """)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.addWidget(self._label)

    def set_text(self, text: str):
        self._label.setText(text)
        self.adjustSize()

    def show_at_parent_corner(self, parent: QtWidgets.QWidget, margin: int = 16):
        try:
            top_left = parent.mapToGlobal(QtCore.QPoint(margin, margin))
        except Exception:
            top_left = QtCore.QPoint(margin, margin)
        self.move(top_left)
        self.show()


class ReceiverWindow(QtWidgets.QMainWindow):
    switchRequested = QtCore.pyqtSignal(int)
    quitRequested = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("WebRTC Receiver")
        self.resize(1280, 720)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        self._widgets = {}  # sender_id -> QWidget
        self._names   = {}  # sender_id -> sender_name
        self._current_sender_id = None

        self._info_popup = InfoPopup(self)
        self._info_popup.hide()

        cw = QtWidgets.QWidget(self)
        self.setCentralWidget(cw)
        self._stack = QtWidgets.QStackedLayout()
        lay = QtWidgets.QVBoxLayout(cw)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._stack)

        self._placeholder = QtWidgets.QLabel("Waiting for senders...", alignment=QtCore.Qt.AlignCenter)
        self._placeholder.setStyleSheet("color:#888; font-size:18px;")
        self._stack.addWidget(self._placeholder)

        # ---- 전역 단축키 ----
        self._scLeft  = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Left),  self)
        self._scLeft.setContext(QtCore.Qt.ApplicationShortcut)
        self._scLeft.activated.connect(lambda: self.switchRequested.emit(-1))

        self._scRight = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Right), self)
        self._scRight.setContext(QtCore.Qt.ApplicationShortcut)
        self._scRight.activated.connect(lambda: self.switchRequested.emit(+1))

        self._scUp = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Up), self)
        self._scUp.setContext(QtCore.Qt.ApplicationShortcut)
        self._scUp.activated.connect(self.show_sender_info_popup)

        self._scDown = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Down), self)
        self._scDown.setContext(QtCore.Qt.ApplicationShortcut)
        self._scDown.activated.connect(self.hide_sender_info_popup)

        self._scEsc = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self)
        self._scEsc.setContext(QtCore.Qt.ApplicationShortcut)
        self._scEsc.activated.connect(self._toggle_fullscreen)

        self._scQuit = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Q), self)
        self._scQuit.setContext(QtCore.Qt.ApplicationShortcut)
        self._scQuit.activated.connect(self.quitRequested.emit)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    @QtCore.pyqtSlot(str, str, result=object)
    def ensure_widget(self, sender_id: str, sender_name: str):
        w = self._widgets.get(sender_id)
        if w is None:
            w = QtWidgets.QWidget(self)
            w.setObjectName(f"video-{sender_id}")
            w.setStyleSheet("background:black;")
            w.setFocusPolicy(QtCore.Qt.NoFocus)
            w.setAttribute(QtCore.Qt.WA_NativeWindow, True)
            _ = w.winId()
            self._widgets[sender_id] = w
            self._stack.addWidget(w)
        if sender_name:
            self._names[sender_id] = sender_name
        return w

    def get_widget(self, sender_id: str):
        return self._widgets.get(sender_id)

    @QtCore.pyqtSlot(str)
    def set_active_sender(self, sender_id: str):
        self._current_sender_id = sender_id
        w = self._widgets.get(sender_id)
        if w:
            self._stack.setCurrentWidget(w)
        else:
            self._stack.setCurrentWidget(self._placeholder)

    @QtCore.pyqtSlot(str, str)
    def set_active_sender_name(self, sender_id: str, sender_name: str):
        if sender_name:
            self._names[sender_id] = sender_name
        self.set_active_sender(sender_id)

    @QtCore.pyqtSlot(str)
    def remove_sender_widget(self, sender_id: str):
        w = self._widgets.pop(sender_id, None)
        self._names.pop(sender_id, None)
        if w:
            try:
                self._stack.removeWidget(w)
                w.setParent(None)
                w.deleteLater()
            except Exception:
                pass
        if self._current_sender_id == sender_id:
            self._current_sender_id = None
            self._stack.setCurrentWidget(self._placeholder)
            self.hide_sender_info_popup()

    @QtCore.pyqtSlot()
    def show_sender_info_popup(self):
        sid = self._current_sender_id
        if not sid:
            return
        name = self._names.get(sid, sid)
        short_id = sid[:8] if sid else ""
        self._info_popup.set_text(f"Sender: {name}  ({short_id})")
        self._info_popup.show_at_parent_corner(self, margin=16)

    @QtCore.pyqtSlot()
    def hide_sender_info_popup(self):
        self._info_popup.hide()


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

    if "linux" in sysname:
        if os.path.isfile("/etc/nv_tegra_release"):
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

    if "windows" in sysname:
        sink = _first_available("d3d11videosink", "autovideosink")
    elif "darwin" in sysname:
        sink = _first_available("avfvideosink", "autovideosink")
    else:
        sink = _first_available("glimagesink", "xvimagesink", "autovideosink")
    if sink:
        print(f"[INFO] 비디오 싱크 사용: {sink.get_name()}")

    _set_props_if_supported(sink, force_aspect_ratio=True, fullscreen=False, handle_events=False)
    return decoder, conv, sink


# ================================================================
# PeerReceiver
# ================================================================
class PeerReceiver:
    def __init__(self, sio, sender_id, sender_name, ui_window: ReceiverWindow, on_down=None):
        self.sio = sio
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.ui = ui_window
        self._on_down = on_down  # ICE 끊김/실패 시 매니저 호출 콜백

        self._gst_playing = False
        self._negotiating = False
        self._sender_ready = False
        self._pending_offer_sdp = None
        self._transceivers = []
        self._transceivers_added = False

        self._display_bin = None
        self._visible = False

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
        bus.set_sync_handler(self._on_sync_message)
        bus.add_signal_watch()
        bus.connect("message::state-changed", self._on_state_changed)
        bus.connect("message::error", self._on_error)

    def prepare_window_handle(self):
        try:
            w = self.ui.ensure_widget(self.sender_id, self.sender_name)
            w.setAttribute(QtCore.Qt.WA_NativeWindow, True)
            self._winid = int(w.winId())
            print(f"[UI][{self.sender_name}] winId=0x{self._winid:x}")
        except Exception as e:
            print(f"[UI][{self.sender_name}] winId 준비 실패:", e)
        return False

    def _on_sync_message(self, bus, msg):
        try:
            if GstVideo.is_video_overlay_prepare_window_handle_message(msg):
                if self._winid is not None:
                    GstVideo.VideoOverlay.set_window_handle(msg.src, self._winid)
                    print(f"[UI][{self.sender_name}] overlay handle set (0x{self._winid:x})")
                    return Gst.BusSyncReply.DROP
                else:
                    print(f"[UI][{self.sender_name}] winId not ready (will fallback to sink window)")
        except Exception as e:
            print(f"[BUS][{self.sender_name}] sync handler error:", e)
        return Gst.BusSyncReply.PASS

    def _on_ice_conn_change(self, obj, pspec):
        try:
            state = int(self.webrtc.get_property('ice-connection-state'))
        except Exception as e:
            print(f"[RTC][{self.sender_name}] ICE state read error:", e); return
        # 0 NEW, 1 CHECKING, 2 CONNECTED, 3 COMPLETED, 4 FAILED, 5 DISCONNECTED, 6 CLOSED
        print(f"[RTC][{self.sender_name}] ICE state:", state)
        if state in (4, 5, 6):  # FAILED/DISCONNECTED/CLOSED
            # 약간의 그레이스 기간 후 제거(일시적 DISCONNECTED 대비)
            def _maybe_remove():
                try:
                    st2 = int(self.webrtc.get_property('ice-connection-state'))
                    if st2 in (4, 6) or (st2 == 5):  # 여전히 끊겨있음
                        if self._on_down:
                            self._on_down(self.sender_id, reason=f"ice-{st2}")
                except Exception:
                    if self._on_down:
                        self._on_down(self.sender_id, reason="ice-unknown")
                return False
            GLib.timeout_add(800, _maybe_remove)

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
        self.peers = {}          # sender_id -> PeerReceiver
        self._order = []         # 등록 순서 유지
        self.active_sender_id = None
        self._bind_socket_events()

        self._last_switch_ms = 0
        self.ui.switchRequested.connect(self.switch_by_offset)

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

    def _nudge_focus(self):
        def _do():
            try:
                self.ui.activateWindow()
                self.ui.raise_()
                self.ui.setFocus()
            except Exception:
                pass
            return False
        GLib.idle_add(_do)

    def _set_active_sender(self, sid):
        self.active_sender_id = sid
        for pid, peer in self.peers.items():
            peer.set_visible(pid == sid)
        if sid:
            name = self.peers[sid].sender_name if sid in self.peers else sid
            print(f"[VIEW] now showing sender: {name}")
            GLib.idle_add(self.ui.set_active_sender_name, sid, name)
        else:
            GLib.idle_add(self.ui.set_active_sender, sid)
        self._nudge_focus()

    def _remove_sender(self, sid: str, reason: str = ""):
        if sid not in self.peers:
            return
        print(f"[CLEANUP] remove sender {sid} ({reason})")

        try:
            idx_in_order = self._order.index(sid)
        except ValueError:
            idx_in_order = None

        was_active = (sid == self.active_sender_id)
        peer = self.peers.pop(sid)
        try:
            peer.stop()
        except Exception:
            pass

        try:
            self._order.remove(sid)
        except ValueError:
            pass

        GLib.idle_add(self.ui.remove_sender_widget, sid)

        if was_active:
            if self._order:
                next_sid = self._order[min(idx_in_order or 0, len(self._order) - 1)]
            else:
                next_sid = None
            self._set_active_sender(next_sid)

    def switch_by_offset(self, offset: int):
        now = int(time.time() * 1000)
        if now - self._last_switch_ms < 150:
            return
        self._last_switch_ms = now

        if not self._order:
            return
        if self.active_sender_id not in self._order:
            self._set_active_sender(self._order[0])
            return
        cur = self._order.index(self.active_sender_id)
        nxt = (cur + offset) % len(self._order)
        self._set_active_sender(self._order[nxt])

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
                if self.active_sender_id is not None and not self._order:
                    self._set_active_sender(None)
                return

            for s in sender_arr:
                sid = s.get('id')
                name = s.get('name', sid)
                if sid in self.peers:
                    if sid not in self._order:
                        self._order.append(sid)
                    continue

                GLib.idle_add(self.ui.ensure_widget, sid, name)

                # on_down 콜백 연결(ICE 실패시 매니저가 제거)
                peer = PeerReceiver(self.sio, sid, name, self.ui, on_down=lambda x, r="ice": self._remove_sender(x, reason=r))
                self.peers[sid] = peer
                if sid not in self._order:
                    self._order.append(sid)

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
            if not sid:
                print("[SIO] share-started missing id:", data); return

            if sid not in self.peers:
                name = data.get('name', sid)
                GLib.idle_add(self.ui.ensure_widget, sid, name)
                peer = PeerReceiver(self.sio, sid, name, self.ui, on_down=lambda x, r="ice": self._remove_sender(x, reason=r))
                self.peers[sid] = peer
                if sid not in self._order:
                    self._order.append(sid)
                GLib.idle_add(peer.prepare_window_handle)
                peer.start()
                GLib.idle_add(lambda p=peer: (p._ensure_transceivers(), p._maybe_create_offer()))
                self.sio.emit('share-request', {'to': sid})
                print(f"[SIO] (late) share-request → {sid} ({name})")

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
            if typ in ('bye', 'hangup', 'close'):
                if frm:
                    self._remove_sender(frm, reason=typ)
                return

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
            if not sid:
                return
            self._remove_sender(sid, reason="share-stopped")

        @self.sio.on('sender-disconnected')
        def on_sender_disconnected(data):
            sid = data.get('id') or data.get('from')
            if not sid:
                return
            self._remove_sender(sid, reason="disconnected")

        @self.sio.on('sender-left')
        def on_sender_left(data):
            sid = data.get('id') or data.get('from')
            if not sid:
                return
            self._remove_sender(sid, reason="left")

        @self.sio.on('room-deleted')
        def on_room_deleted(_=None):
            print("[SIO] room-deleted → all cleanup")
            for sid in list(self.peers.keys()):
                self._remove_sender(sid, reason="room-deleted")
            self._set_active_sender(None)


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
    ui.showFullScreen() # show(), showFullScreen()
    ui.activateWindow(); ui.raise_(); ui.setFocus()

    _glib_timer = integrate_glib_into_qt()

    manager = MultiReceiverManager(ui)
    manager.start()

    def _quit(*_):
        try:
            manager.stop()
        except:
            pass
        QtWidgets.QApplication.quit()

    ui.quitRequested.connect(_quit)
    app.aboutToQuit.connect(manager.stop)
    signal.signal(signal.SIGINT, _quit)
    signal.signal(signal.SIGTERM, _quit)

    print("[MAIN] PyQt5 + GStreamer (Overlay) event loop started.")
    sys.exit(app.exec_())