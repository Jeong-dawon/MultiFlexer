# 2.py에서 변환할 sender 없으면 switching 되지 않게 반영한 버전
# 추가할 사항: sender 공유 중지 후 다시 화면 공유 시, 다시 화면 공유 가능하게끔 로직 정리



# ================================================================
# Multi-Sender WebRTC Receiver (GStreamer + Socket.IO + PyQt5, Overlay)
# - 공유 중지: 파이프라인 PAUSED + UI 제거(연결 유지)
# - 공유 재개: 파이프라인 PLAYING + UI 복구(재협상 없이)
# - 좌/우 전환: "공유 중인 sender가 2명 이상"이고 "실제 다른 sender로 바뀔 때만"
#   전환 시간 측정/로그 출력 (키만 눌러도, 대상이 같으면 측정/로그 하지 않음)
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
    """좌상단에 뜨는 작은 정보 팝업(포커스 훔치지 않음)"""
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
    # 좌/우 전환 시그널: +1(다음), -1(이전)
    switchRequested = QtCore.pyqtSignal(int)
    # Q 종료 요청
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

        # ---- 단축키 ----
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

    # ---- 이름/위젯 관리 ----
    @QtCore.pyqtSlot(str, str, result=object)
    def ensure_widget(self, sender_id: str, sender_name: str):
        w = self._widgets.get(sender_id)
        if w is None:
            w = QtWidgets.QWidget(self)
            w.setObjectName(f"video-{sender_id}")
            w.setStyleSheet("background:black;")
            w.setFocusPolicy(QtCore.Qt.NoFocus)
            w.setAttribute(QtCore.Qt.WA_NativeWindow, True)  # Overlay 임베드
            _ = w.winId()  # 핸들 실체화
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
    def __init__(self, sio, sender_id, sender_name, ui_window: ReceiverWindow,
                 on_ready=None, on_down=None):
        self.sio = sio
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.ui = ui_window

        self._on_ready = on_ready   # (sid, dt_ms)
        self._on_down  = on_down
        self._switch_t0 = None

        self._gst_playing = False
        self._negotiating = False
        self._sender_ready = False
        self._pending_offer_sdp = None
        self._transceivers = []
        self._transceivers_added = False

        self._display_bin = None
        self._visible = True  # Always-Playing 모드 의미상 True
        self._winid = None

        # 공유 상태 플래그 (sender-share-started/stopped로 갱신)
        self.share_active = True

        # Pipeline
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
        bus.connect("message::async-done", self._on_async_done)
        bus.connect("message::qos", self._on_qos)

    # ---------- UI 임베드 ----------
    def prepare_window_handle(self):
        try:
            w = self.ui.ensure_widget(self.sender_id, self.sender_name)
            w.setAttribute(QtCore.Qt.WA_NativeWindow, True)
            self._winid = int(w.winId())
            print(f"[UI][{self.sender_name}] winId=0x{self._winid:x}")
        except Exception as e:
            print(f"[UI][{self.sender_name}] winId 준비 실패:", e)
        return False

    def _force_overlay_handle(self):
        try:
            if self._winid and self._display_bin:
                sink = self._display_bin.get_property("video-sink")
                if sink:
                    GstVideo.VideoOverlay.set_window_handle(sink, self._winid)
                    print(f"[UI][{self.sender_name}] overlay rebind (0x{self._winid:x})")
        except Exception as e:
            print(f"[UI][{self.sender_name}] overlay rebind failed:", e)

    # Bus sync: prepare-window-handle
    def _on_sync_message(self, bus, msg):
        try:
            if GstVideo.is_video_overlay_prepare_window_handle_message(msg):
                if self._winid is not None:
                    GstVideo.VideoOverlay.set_window_handle(msg.src, self._winid)
                    print(f"[UI][{self.sender_name}] overlay handle set (0x{self._winid:x})")
                    return Gst.BusSyncReply.DROP
        except Exception as e:
            print(f"[BUS][{self.sender_name}] sync handler error:", e)
        return Gst.BusSyncReply.PASS

    # ---------- 파이프라인 상태 ----------
    def start(self):
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        print(f"[GST][{self.sender_name}] set_state ->", ret.value_nick)

    def stop(self):
        try: self.pipeline.set_state(Gst.State.NULL)
        except: pass

    def pause_pipeline(self):
        self.share_active = False
        try:
            self.pipeline.set_state(Gst.State.PAUSED)
            print(f"[GST][{self.sender_name}] → PAUSED (share stopped)")
        except Exception as e:
            print(f"[GST][{self.sender_name}] pause err:", e)

    def resume_pipeline(self):
        self.share_active = True
        try:
            self.pipeline.set_state(Gst.State.PLAYING)
            print(f"[GST][{self.sender_name}] → PLAYING (share started)")
            GLib.timeout_add(50, lambda: (self._force_overlay_handle() or False))
        except Exception as e:
            print(f"[GST][{self.sender_name}] resume err:", e)

    # Always-Playing: 실제 전환시에만 측정 시작
    def set_visible(self, on: bool, t0: int = None):
        if on:
            self._switch_t0 = t0 if t0 is not None else time.time_ns()

    # ---------- GStreamer 이벤트 ----------
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

    # 전환 완료 후보 1: async-done
    def _on_async_done(self, bus, msg):
        if self._switch_t0 is None:
            return
        dt_ms = (time.time_ns() - self._switch_t0) / 1e6
        self._emit_ready_once(dt_ms)

    # 전환 완료 후보 2: QoS
    def _on_qos(self, bus, msg):
        if self._switch_t0 is None:
            return
        dt_ms = (time.time_ns() - self._switch_t0) / 1e6
        self._emit_ready_once(dt_ms)

    # 전환 완료 후보 3: 첫 handoff(identity)
    def _on_handoff(self, identity, buffer, pad=None, *args):
        if self._switch_t0 is None:
            return
        dt_ms = (time.time_ns() - self._switch_t0) / 1e6
        self._emit_ready_once(dt_ms)

    def _emit_ready_once(self, dt_ms: float):
        t0 = self._switch_t0
        self._switch_t0 = None
        if t0 is not None and self._on_ready:
            GLib.idle_add(self._on_ready, self.sender_id, float(dt_ms))

    def _on_ice_conn_change(self, obj, pspec):
        try:
            state = int(self.webrtc.get_property('ice-connection-state'))
        except Exception as e:
            print(f"[RTC][{self.sender_name}] ICE state read error:", e); return
        print(f"[RTC][{self.sender_name}] ICE state:", state)
        if state in (4, 5, 6):
            def _maybe_remove():
                try:
                    st2 = int(self.webrtc.get_property('ice-connection-state'))
                    if st2 in (4, 6) or st2 == 5:
                        if self._on_down:
                            self._on_down(self.sender_id, reason=f"ice-{st2}")
                except Exception:
                    if self._on_down:
                        self._on_down(self.sender_id, reason="ice-unknown")
                return False
            GLib.timeout_add(800, _maybe_remove)

    # ---------- Negotiation ----------
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

    def _on_negotiation_needed(self, element, *args):
        if self._negotiating:
            return
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
        caps = pad.get_current_caps()
        if not caps:
            return
        caps_str = caps.to_string()
        if not caps_str.startswith("application/x-rtp"):
            return

        depay = _make("rtph264depay")
        parse = _make("h264parse")
        decoder, conv, sink = get_decoder_and_sink()

        q = _make("queue")
        ident = _make("identity")
        fpssink = _make("fpsdisplaysink")

        # 첫 버퍼 시점용 handoff
        if ident:
            try:
                ident.set_property("signal-handoffs", True)
                ident.connect("handoff", self._on_handoff)
            except Exception:
                pass

        if fpssink:
            fpssink.set_property("signal-fps-measurements", True)
            fpssink.set_property("text-overlay", False)
            if sink:
                fpssink.set_property("video-sink", sink)
            fpssink.connect("fps-measurements",
                lambda el, fps, drop, avg:
                    print(f"[STATS][{self.sender_name}] FPS={fps:.2f}, drop={drop:.2f}, avg={avg:.2f}")
            )

        if not all([depay, parse, decoder, conv, q, fpssink]):
            print(f"[RTC][{self.sender_name}] 요소 부족으로 링크 실패"); return

        # 파이프라인 구성
        for e in (depay, parse, decoder, conv, q, ident, fpssink) if ident else (depay, parse, decoder, conv, q, fpssink):
            self.pipeline.add(e); e.sync_state_with_parent()

        # 링크
        if pad.link(depay.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            print(f"[RTC][{self.sender_name}] pad link 실패"); return
        depay.link(parse); parse.link(decoder); decoder.link(conv); conv.link(q)
        if ident:
            q.link(ident); ident.link(fpssink)
        else:
            q.link(fpssink)

        self._display_bin = fpssink
        print(f"[OK][{self.sender_name}] Incoming video linked → {decoder.name}")


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

        self._last_switch_ms = 0
        self._last_switch_t0 = None  # 전환 시작 시각(ns)

        self._bind_socket_events()
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

    def _active_sender_ids(self):
        """현재 '공유 중'인 sender들만 대상으로 전환"""
        return [sid for sid, p in self.peers.items() if p.share_active]

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
        # 실제로 바뀔 때만 동작
        if sid == self.active_sender_id:
            return
        self.active_sender_id = sid
        if sid:
            name = self.peers[sid].sender_name if sid in self.peers else sid
            GLib.idle_add(self.ui.set_active_sender_name, sid, name)
        else:
            GLib.idle_add(self.ui.set_active_sender, sid)
        # 선택된 peer에만 측정 시작 t0 주입(Always-Playing)
        if sid and sid in self.peers and self._last_switch_t0 is not None:
            self.peers[sid].set_visible(True, t0=self._last_switch_t0)
        self._nudge_focus()

    # ----- 전환 완료 콜백: 실제 표시 시점 -----
    def _on_switch_ready(self, sid: str, dt_ms: float):
        if self.active_sender_id != sid:
            return
        # 전환이 실제 발생한 경우(2명 이상 공유 중)만 출력
        if len(self._active_sender_ids()) < 2:
            return
        name = self.peers[sid].sender_name if sid in self.peers else sid
        print(f"[VIEW] switching to sender: {name}")
        print(f"[VIEW] switching time: {dt_ms:.1f} ms")

    # ----- ←/→ 입력 처리 -----
    def switch_by_offset(self, offset: int):
        now_ms = int(time.time() * 1000)
        if now_ms - self._last_switch_ms < 150:
            return
        self._last_switch_ms = now_ms

        actives = self._active_sender_ids()
        # 전환 대상이 2명 미만이면 아무 것도 하지 않음 (측정/로그 X)
        if len(actives) < 2:
            return

        # 아직 활성 표시가 없으면(초기 상태) 그냥 첫 대상만 보여주고 측정/로그는 하지 않음
        if self.active_sender_id not in actives:
            self._last_switch_t0 = None  # 측정 시작 안함
            self._set_active_sender(actives[0])
            return

        cur = actives.index(self.active_sender_id)
        nxt = (cur + offset) % len(actives)
        target_sid = actives[nxt]

        # 실제로 다른 sender로 바뀌는 경우에만 측정/전환
        if target_sid == self.active_sender_id:
            return

        self._last_switch_t0 = time.time_ns()  # 측정 시작
        self._set_active_sender(target_sid)

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

                peer = PeerReceiver(
                    self.sio, sid, name, self.ui,
                    on_ready=self._on_switch_ready,
                    on_down=lambda x, r="ice": self._remove_sender(x, reason=r)
                )
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
            sid = data.get('id') or data.get('senderId') or data.get('from')
            name = data.get('name')
            if not sid: return
            if sid not in self.peers:
                GLib.idle_add(self.ui.ensure_widget, sid, name or sid)
                peer = PeerReceiver(
                    self.sio, sid, name or sid, self.ui,
                    on_ready=self._on_switch_ready, on_down=lambda x, r="ice": self._remove_sender(x, reason=r)
                )
                self.peers[sid] = peer
                if sid not in self._order: self._order.append(sid)
                GLib.idle_add(peer.prepare_window_handle)
                peer.start()
                GLib.idle_add(lambda p=peer: (p._ensure_transceivers(), p._maybe_create_offer()))

            peer = self.peers[sid]
            peer.resume_pipeline()  # PLAYING
            GLib.idle_add(self.ui.ensure_widget, sid, name or peer.sender_name)
            if self.active_sender_id is None:
                self._set_active_sender(sid)
            print(f"[SIO] sender-share-started: {peer.sender_name}")

        @self.sio.on('sender-share-stopped')
        def on_sender_share_stopped(data):
            sid = data.get('id') or data.get('senderId') or data.get('from')
            if not sid: return
            peer = self.peers.get(sid)
            if not peer: return
            peer.pause_pipeline()  # PAUSED
            GLib.idle_add(self.ui.remove_sender_widget, sid)
            if self.active_sender_id == sid:
                actives = self._active_sender_ids()
                next_sid = actives[0] if actives else None
                self._set_active_sender(next_sid)
            print(f"[SIO] sender-share-stopped: {peer.sender_name}")

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

        @self.sio.on('remove-sender')
        def on_remove_sender(sid):
            if not sid: return
            self._remove_sender(sid, reason="server-remove")

        @self.sio.on('sender-disconnected')
        def on_sender_disconnected(data):
            sid = data.get('id') or data.get('senderId') or data.get('from')
            if sid:
                self._remove_sender(sid, reason="disconnected")

        @self.sio.on('sender-left')
        def on_sender_left(data):
            sid = data.get('id') or data.get('senderId') or data.get('from')
            if sid:
                self._remove_sender(sid, reason="left")

        @self.sio.on('room-deleted')
        def on_room_deleted(_=None):
            print("[SIO] room-deleted → all cleanup")
            for sid in list(self.peers.keys()):
                self._remove_sender(sid, reason="room-deleted")
            self._set_active_sender(None)

    def _remove_sender(self, sid: str, reason: str = ""):
        if sid not in self.peers:
            return
        name = self.peers[sid].sender_name
        print(f"[CLEANUP] remove sender {name} ({reason})")
        peer = self.peers.pop(sid)
        try: peer.stop()
        except: pass
        try: self._order.remove(sid)
        except ValueError: pass
        GLib.idle_add(self.ui.remove_sender_widget, sid)
        if self.active_sender_id == sid:
            actives = self._active_sender_ids()
            self._set_active_sender(actives[0] if actives else None)


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
    ui.show()

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
