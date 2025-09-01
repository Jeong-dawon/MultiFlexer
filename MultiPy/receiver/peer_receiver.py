# peer_receiver.py
# WebRTC 피어 수신기 클래스

import time
import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstWebRTC', '1.0')
gi.require_version('GstSdp', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst, GstWebRTC, GstSdp, GLib, GstVideo
from PyQt5 import QtCore
from gst_utils import _make, get_decoder_and_sink
from config import STUN_SERVER, GST_VIDEO_CAPS, UI_OVERLAY_DELAY_MS, ICE_STATE_CHECK_DELAY_MS

class PeerReceiver:
    """WebRTC 피어 연결을 관리하는 수신기 클래스"""
    
    def __init__(self, sio, sender_id, sender_name, ui_window,
                 on_ready=None, on_down=None):
        """
        Args:
            sio: Socket.IO 클라이언트 인스턴스
            sender_id: Sender의 고유 ID
            sender_name: Sender의 표시 이름
            ui_window: UI 윈도우 인스턴스
            on_ready: 전환 완료 콜백 함수 (sender_id, duration_ms)
            on_down: 연결 종료 콜백 함수 (sender_id, reason)
        """
        self.sio = sio
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.ui = ui_window

        # 콜백 함수들
        self._on_ready = on_ready  # (sid, dt_ms)
        self._on_down = on_down
        
        # 전환 시간 측정용
        self._switch_t0 = None

        # WebRTC 연결 상태 플래그들
        self._gst_playing = False
        self._negotiating = False
        self._sender_ready = False
        self._pending_offer_sdp = None
        self._transceivers = []
        self._transceivers_added = False

        # 렌더링 관련
        self._display_bin = None
        self._visible = True   # Always-Playing 모드 의미상 True
        self._winid = None
        
        # 공유 상태 플래그 (sender-share-started/stopped로 갱신)
        self.share_active = True

        # GStreamer 파이프라인 초기화
        self._setup_pipeline()

    def update_window_from_widget(self, w):
        try:
            if not w:  # 위젯이 None이면 skip
                return
            # 여기서 show()가 안 된 상태면 winId()가 dummy일 수 있음
            if not w.isVisible():
                w.show()

            self._winid = int(w.winId())
            print(f"[DEBUG] update_window_from_widget: {self.sender_id} winId=0x{self._winid:x}")
            self._force_overlay_handle()
        except Exception as e:
            print(f"[UI][{self.sender_name}] update_window_from_widget failed:", e)

    
    def _setup_pipeline(self):
        """GStreamer 파이프라인 초기화"""
        self.pipeline = Gst.Pipeline.new(f"webrtc-pipeline-{self.sender_id}")
        self.webrtc = _make("webrtcbin")
        
        if not self.webrtc:
            raise RuntimeError("webrtcbin 생성 실패")

        self.pipeline.add(self.webrtc)
        self.webrtc.set_property('stun-server', STUN_SERVER)

        # WebRTC 이벤트 연결
        self._connect_webrtc_signals()
        
        # 버스 설정
        self._setup_bus()

    def _connect_webrtc_signals(self):
        """WebRTC 관련 시그널 연결"""
        self.webrtc.connect('notify::ice-connection-state', self._on_ice_conn_change)
        self.webrtc.connect('on-ice-candidate', self.on_ice_candidate)
        self.webrtc.connect('pad-added', self.on_incoming_stream)
        self.webrtc.connect('on-negotiation-needed', self._on_negotiation_needed)

    def _setup_bus(self):
        """GStreamer 버스 설정"""
        bus = self.pipeline.get_bus()
        bus.set_sync_handler(self._on_sync_message)
        bus.add_signal_watch()
        
        # 버스 메시지 핸들러들 연결
        message_handlers = [
            ("message::state-changed", self._on_state_changed),
            ("message::error", self._on_error),
            ("message::async-done", self._on_async_done),
            ("message::qos", self._on_qos),
        ]
        
        for message_type, handler in message_handlers:
            bus.connect(message_type, handler)

    # ========== UI 임베드 관련 ==========
    
    def prepare_window_handle(self):
        """윈도우 핸들 준비"""
        try:
            w = self.ui.ensure_widget(self.sender_id, self.sender_name)
            w.setAttribute(QtCore.Qt.WA_NativeWindow, True)
            self._winid = int(w.winId())
            print(f"[UI][{self.sender_name}] winId=0x{self._winid:x}")
        except Exception as e:
            print(f"[UI][{self.sender_name}] winId 준비 실패:", e)
        return False

    def _force_overlay_handle(self):
        """오버레이 핸들 강제 재설정"""
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
        """버스 동기 메시지 핸들러 - prepare-window-handle 처리"""
        try:
            if GstVideo.is_video_overlay_prepare_window_handle_message(msg):
                if self._winid is not None:
                    GstVideo.VideoOverlay.set_window_handle(msg.src, self._winid)
                    print(f"[UI][{self.sender_name}] overlay handle set (0x{self._winid:x})")
                    return Gst.BusSyncReply.DROP
        except Exception as e:
            print(f"[BUS][{self.sender_name}] sync handler error:", e)
        return Gst.BusSyncReply.PASS

    # ========== 파이프라인 상태 관리 ==========
    
    def start(self):
        """파이프라인 시작"""
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        print(f"[GST][{self.sender_name}] set_state ->", ret.value_nick)

    def stop(self):
        """파이프라인 완전 정지"""
        try: self.pipeline.set_state(Gst.State.NULL)
        except: pass

    def pause_pipeline(self):
        """공유 중지 시 파이프라인 일시정지"""
        if not ALWAYS_PLAYING:  # config.py에 전역 옵션 두기
            self.share_active = False
            self.pipeline.set_state(Gst.State.PAUSED)
        try:
            self.pipeline.set_state(Gst.State.PAUSED)
            print(f"[GST][{self.sender_name}] → PAUSED (share stopped)")
        except Exception as e:
            print(f"[GST][{self.sender_name}] pause err:", e)

    def resume_pipeline(self):
        """공유 재개 시 파이프라인 재생"""
        self.share_active = True
        try:
            self.pipeline.set_state(Gst.State.PLAYING)
            print(f"[GST][{self.sender_name}] → PLAYING (share started)")
            GLib.timeout_add(UI_OVERLAY_DELAY_MS, lambda: (self._force_overlay_handle() or False))
        except Exception as e:
            print(f"[GST][{self.sender_name}] resume err:", e)

    # Always-Playing: 실제 전환시에만 측정 시작
    def set_visible(self, on: bool, t0: int = None):
        """가시성 설정 및 전환 시간 측정 시작"""
        if on:
            self._switch_t0 = t0 if t0 is not None else time.time_ns()

    # ========== GStreamer 이벤트 핸들러들 ==========
    
    def _on_state_changed(self, bus, msg):
        """파이프라인 상태 변경 핸들러"""
        if msg.src is self.pipeline:
            _, new, _ = msg.parse_state_changed()
            if new == Gst.State.PLAYING and not self._gst_playing:
                self._gst_playing = True
                print(f"[GST][{self.sender_name}] pipeline → PLAYING")
                self._ensure_transceivers()
                if self._sender_ready and not self._pending_offer_sdp:
                    GLib.idle_add(lambda: self._maybe_create_offer())

    def _on_error(self, bus, msg):
        """에러 메시지 핸들러"""
        err, dbg = msg.parse_error()
        print(f"[GST][{self.sender_name}][ERROR] {err.message} (debug: {dbg})")

    # 전환 완료 후보 1: async-done
    def _on_async_done(self, bus, msg):
        """비동기 완료 핸들러 - 전환 완료 감지용"""
        if self._switch_t0 is None:
            return
        dt_ms = (time.time_ns() - self._switch_t0) / 1e6
        self._emit_ready_once(dt_ms)
            
    # 전환 완료 후보 2: QoS
    def _on_qos(self, bus, msg):
        """QoS 메시지 핸들러 - 전환 완료 감지용"""
        if self._switch_t0 is None:
            return
        dt_ms = (time.time_ns() - self._switch_t0) / 1e6
        self._emit_ready_once(dt_ms)

    # 전환 완료 후보 3: 첫 handoff(identity)
    def _on_handoff(self, identity, buffer, pad=None, *args):
        """Identity handoff 핸들러 - 첫 버퍼 시점 감지용"""
        if self._switch_t0 is None:
            return
        dt_ms = (time.time_ns() - self._switch_t0) / 1e6
        self._emit_ready_once(dt_ms)
        
    def _emit_ready_once(self, dt_ms: float):
        """전환 완료 콜백 호출 (한 번만)"""
        t0 = self._switch_t0
        self._switch_t0 = None
        if t0 is not None and self._on_ready:
            GLib.idle_add(self._on_ready, self.sender_id, float(dt_ms))

    def _on_ice_conn_change(self, obj, pspec):
        """ICE 연결 상태 변경 핸들러"""
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
            GLib.timeout_add(ICE_STATE_CHECK_DELAY_MS, _maybe_remove)

    # ========== WebRTC Negotiation ==========
    
    def _add_recv(self, caps_str):
        """수신 전용 transceiver 추가"""
        t = self.webrtc.emit(
            'add-transceiver',
            GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY,
            Gst.Caps.from_string(caps_str)
        )
        self._transceivers.append(t)
        print(f'[RTC][{self.sender_name}] transceiver added:', bool(t))

    def _ensure_transceivers(self):
        """Transceiver 생성 보장"""
        if self._transceivers_added:
            return
        self._add_recv(GST_VIDEO_CAPS)
        self._transceivers_added = True

    def _on_negotiation_needed(self, element, *args):
        """협상 필요 시그널 핸들러"""
        if self._negotiating:
            return
        GLib.idle_add(lambda: self._maybe_create_offer())

    def _maybe_create_offer(self):
        """Offer 생성 (중복 방지)"""
        if self._negotiating: return False
        self._negotiating = True
        def _do():
            p = Gst.Promise.new_with_change_func(self._on_offer_created, self.webrtc)
            self.webrtc.emit('create-offer', None, p)
            return False
        GLib.idle_add(_do)
        return False 

    def _on_offer_created(self, promise, element):
        """Offer 생성 완료 핸들러"""
        reply = promise.get_reply()
        if not reply: self._negotiating=False; return
        offer = reply.get_value('offer')
        if not offer: self._negotiating=False; return
        self._pending_offer_sdp = offer.sdp.as_text()
        p2 = Gst.Promise.new_with_change_func(self._on_local_desc_set, element)
        element.emit('set-local-description', offer, p2)

    def _on_local_desc_set(self, promise, element):
        """로컬 SDP 설정 완료 핸들러"""
        print(f"[RTC][{self.sender_name}] Local description set (offer)")
        if self._gst_playing and self.sender_id:
            self._send_offer()
        self._negotiating = False
 
    def _send_offer(self):
        """시그널링 서버로 Offer 전송"""
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
        """원격 Answer SDP 적용"""
        ok, sdpmsg = GstSdp.SDPMessage.new()
        if ok != GstSdp.SDPResult.OK: return False
        GstSdp.sdp_message_parse_buffer(sdp_text.encode('utf-8'), sdpmsg)
        answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdpmsg)
        self.webrtc.emit('set-remote-description', answer, None)
        print(f"[RTC][{self.sender_name}] Remote ANSWER 적용 완료")
        return False   

    def on_ice_candidate(self, element, mlineindex, candidate):
        """ICE 후보 수신 시 시그널링 서버로 전송"""
        self.sio.emit('signal', {
            'to': self.sender_id,
            'from': self.sio.sid,
            'type': 'candidate',
            'payload': {'candidate': candidate,
                        'sdpMid': f"video{mlineindex}",
                        'sdpMLineIndex': int(mlineindex)}
        })
        
    # ========== 미디어 스트림 처리 ==========
    
    def on_incoming_stream(self, webrtc, pad):
        """들어오는 미디어 스트림 처리"""
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

        # FPS 측정 싱크 설정
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