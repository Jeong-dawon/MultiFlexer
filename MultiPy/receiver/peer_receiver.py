# peer_receiver.py
# WebRTC 피어 수신기 클래스

import gi, json

gi.require_version('Gst', '1.0')
gi.require_version('GstWebRTC', '1.0')
gi.require_version('GstSdp', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst, GstWebRTC, GstSdp, GLib, GstVideo
from PyQt5 import QtCore
from gst_utils import _make, get_decoder_and_sink
from config import STUN_SERVER, GST_VIDEO_CAPS, UI_OVERLAY_DELAY_MS, ICE_STATE_CHECK_DELAY_MS
import time, sys

class PeerReceiver:
    """WebRTC 피어 연결을 관리하는 수신기 클래스"""
    
    def __init__(self, sio, sender_id, sender_name, ui_window,
                 on_ready=None, on_down=None, mqtt_manager=None):
        """
        Args:
            sio: Socket.IO 클라이언트 인스턴스
            sender_id: Sender의 고유 ID
            sender_name: Sender의 표시 이름
            ui_window: UI 윈도우 인스턴스
            on_ready: (더 이상 사용하지 않음) 전환 완료 콜백
            on_down: 연결 종료 콜백 함수 (sender_id, reason)
        """
        self.sio = sio
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.ui = ui_window
        self.current_fps = 0.0
        self.drop_rate = 0.0
        self.avg_fps = 0.0
        self.mqtt_manager = mqtt_manager

        # 콜백
        self._on_ready = on_ready  # 현재는 호출하지 않음
        self._on_down = on_down
        
        # WebRTC 연결 상태 플래그들
        self._gst_playing = False
        self._negotiating = False
        self._sender_ready = False
        self._pending_offer_sdp = None
        self._transceivers = []
        self._transceivers_added = False

        # 렌더링 관련
        self._display_bin = None
        self._visible = True
        self._winid = None
        
        # 공유 상태 플래그 (sender-share-started/stopped로 갱신)
        self.share_active = True

        # 통계 관련 상태
        self._byte_accum = 0
        self._last_ts = time.time()
        self._bitrate_mbps = 0.0
        self._width = None
        self._height = None

        # GStreamer 파이프라인 초기화
        self._setup_pipeline()

        # 1초 주기 통계 tick
        self._stats_timer_id = GLib.timeout_add(1000, self._stats_tick)

    def _stats_tick(self):
        try:
            # 해상도 가져오기
            if self._display_bin:
                sink = self._display_bin.get_property("video-sink")
                if sink:
                    pad = sink.get_static_pad("sink")
                    if pad:
                        caps = pad.get_current_caps()
                        if caps:
                            s = caps.get_structure(0)
                            self.width = s.get_value("width")
                            self.height = s.get_value("height")

            # Mbps 계산 (bitrate는 on_incoming_stream에서 identity나 rtpjitterbuffer 활용 가능)
            mbps = self.bitrate / 1e6 if hasattr(self, "bitrate") else 0.0

            print(f"[STATS][{self.sender_name}] "
                f"FPS={self.current_fps:.2f}, "
                f"drop={self.drop_rate:.2f}, "
                f"avg={self.avg_fps:.2f}, "
                f"Mbps={mbps:.2f}, "
                f"res={getattr(self, 'width', '?')}x{getattr(self, 'height', '?')}")
        except Exception as e:
            print(f"[STATS][{self.sender_name}] stats_tick error:", e)

        return True  # 타이머 계속 반복

    def update_window_from_widget(self, w):
        try:
            if not w:
                return
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
        
        # 전환시간 측정 관련 핸들러 제거 (async-done, QoS)
        message_handlers = [
            ("message::state-changed", self._on_state_changed),
            ("message::error", self._on_error),
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
        try:
            if self._stats_timer_id:
                GLib.source_remove(self._stats_timer_id)
                self._stats_timer_id = None
        except:
            pass
        try:
            if self.pipeline:
                self.pipeline.set_state(Gst.State.NULL)
                self.pipeline = None
        except:
            pass

    def pause_pipeline(self):
        """공유 중지 시 파이프라인 일시정지"""
        # NOTE: ALWAYS_PLAYING 옵션은 외부 config에 둘 수 있음
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
        decoder, conv, _ = get_decoder_and_sink()

        q = _make("queue")
        fpssink = _make("fpsdisplaysink")

        # 여기서 OS별 싱크 생성
        sink = None
        if sys.platform.startswith("linux"):
            # Jetson / 일반 Linux
            sink = _make("nv3dsink") or _make("glimagesink")
        elif sys.platform == "win32":
            sink = _make("d3d11videosink")
        elif sys.platform == "darwin":
            sink = _make("glimagesink")

        if sink:
            sink.set_property("sync", False)              # 지연 방지
            fpssink.set_property("video-sink", sink)      # fpsdisplaysink → 실제 싱크 연결


        # FPS 측정 싱크 설정
        if fpssink:
            fpssink.set_property("signal-fps-measurements", True)
            fpssink.set_property("text-overlay", False)
            fpssink.set_property("sync", False)  # [MODIFIED] 측정만 하고 렌더링은 빠르게
            if sink:
                fpssink.set_property("video-sink", sink)  # [MODIFIED] 강제 지정
            fpssink.connect("fps-measurements", self._on_fps_measurements)

        if not all([depay, parse, decoder, conv, q, fpssink]):
            print(f"[RTC][{self.sender_name}] 요소 부족으로 링크 실패")
            return

        # 파이프라인 구성
        for e in (depay, parse, decoder, conv, q, fpssink):
            self.pipeline.add(e)
            e.sync_state_with_parent()

        identity = _make("identity")
        if identity:
            identity.set_property("signal-handoffs", True)
            identity.connect("handoff", self._on_rtp_handoff)

        # 요소 추가
        for e in (depay, identity, parse, decoder, conv, q, fpssink):
            self.pipeline.add(e)
            e.sync_state_with_parent()

        # pad 링크
        if pad.link(depay.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            print(f"[RTC][{self.sender_name}] pad link 실패")
            return

        # 링크
        depay.link(identity)
        identity.link(parse)
        parse.link(decoder)
        decoder.link(conv)
        conv.link(q)
        q.link(fpssink)

        self._display_bin = fpssink
        print(f"[OK][{self.sender_name}] Incoming video linked → {decoder.name}")

    # ========== FPS 콜백 ==========
    def _on_fps_measurements(self, element, fps, drop, avg):
        self.current_fps = fps
        self.drop_rate = drop
        self.avg_fps = avg

        # 해상도
        if self._width is None or self._height is None:
            try:
                sink = self._display_bin.get_property("video-sink")
                if sink:
                    pad = sink.get_static_pad("sink")
                    if pad:
                        caps = pad.get_current_caps()
                        if caps:
                            s = caps.get_structure(0)
                            self._width = s.get_value("width")
                            self._height = s.get_value("height")
            except Exception:
                self._width, self._height = None, None

        res_str = f"{self._width}x{self._height}" if self._width and self._height else "?"

        print(f"[STATS][{self.sender_name}] "
            f"FPS={fps:.2f}, drop={drop:.2f}, avg={avg:.2f}, "
            f"Mbps={self._bitrate_mbps:.2f}, res={res_str}")
        
        stats = {
            "name": self.sender_name,
            "fps": float(fps),
            "drop": float(drop),
            "avg_fps": float(avg),
            "mbps": float(self._bitrate_mbps),
            "width": int(self._width or 0),
            "height": int(self._height or 0),
        }

        print(f"[DEBUG][{self.sender_name}] mqtt_manager = {self.mqtt_manager}")
        if self.mqtt_manager:
            self.mqtt_manager.publish_stats(self.sender_name, stats, interval=1.0)


    # ========== 비트레이트 계산 ==========
    def _on_rtp_handoff(self, identity, buffer):
        size = buffer.get_size()
        self._byte_accum += size
        now = time.time()
        elapsed = now - self._last_ts
        if elapsed >= 1.0:
            # Mbps로 변환
            self._bitrate_mbps = (self._byte_accum * 8) / (elapsed * 1_000_000)
            self._byte_accum = 0
            self._last_ts = now
