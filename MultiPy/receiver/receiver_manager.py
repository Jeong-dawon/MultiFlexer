# receiver_manager.py
# 멀티 수신기 관리자 클래스

import time
import threading
import ssl
import socketio
from gi.repository import GLib

from config import SIGNALING_URL, RECEIVER_NAME, SWITCH_COOLDOWN_MS
from peer_receiver import PeerReceiver

class MultiReceiverManager:
    def __init__(self, ui_window, view_manager=None):
        self.ui = ui_window
        self.view_manager = view_manager 
        self.sio = socketio.Client(
            logger=False, 
            engineio_logger=False, 
            ssl_verify=False, 
            websocket_extra_options={"sslopt": {"cert_reqs": ssl.CERT_NONE}}
        )
        self.peers = {}          # sender_id -> PeerReceiver
        self._order = []         # 등록 순서 유지
        self.active_sender_id = None

        self._last_switch_ms = 0
        self._last_switch_t0 = None  # 전환 시작 시각(ns)

        self._bind_socket_events()
        self.ui.switchRequested.connect(self.switch_by_offset)

        # 현재 레이아웃에서 어떤 셀에 어떤 sender가 들어가 있는지
        self._cell_assign: dict[int, str] = {}   # cell_index -> sender_id

        if self.view_manager:
            self.view_manager.bind_manager(self)
            self.view_manager.set_senders_provider(self.list_active_senders)

    def start(self):
        """매니저 시작"""
        threading.Thread(target=self._sio_connect, daemon=True).start()

    def stop(self):
        """매니저 정지"""
        try:
            for pid, peer in list(self.peers.items()):
                peer.stop()
        except: 
            pass
        try:
            if self.sio.connected:
                self.sio.disconnect()
        except: 
            pass

    def _active_sender_ids(self):
        """현재 '공유 중'인 sender들만 대상으로 전환"""
        return [sid for sid, p in self.peers.items() if p.share_active]

    def list_active_senders(self):
        return [(sid, p.sender_name) for sid, p in self.peers.items()]
    
    def pause_all_streams(self):
        """모드 전환 시 전체 정지(준비상태)"""
        for p in self.peers.values():
            try:
                p.pause_pipeline()
            except Exception:
                pass
        self._cell_assign.clear()

    # ---- 핵심: 배정된 셀 기준으로 재생/정지 상태 동기화 ----
    def _sync_play_states(self):
        """셀에 배정된 sender는 PLAYING, 그 외는 PAUSED로 동기화"""
        assigned = set(self._cell_assign.values())
        for sid, p in self.peers.items():
            try:
                if sid in assigned:
                    p.resume_pipeline()
                else:
                    p.pause_pipeline()
            except Exception:
                pass

    def assign_sender_to_cell(self, cell_index: int, sender_id: str):
        """특정 셀에 sender 배정. 배정된 모든 sender는 PLAYING 유지."""
        if sender_id not in self.peers or not (0 <= cell_index):
            return
        target = self.peers[sender_id]

        # 1) 동일 sender가 다른 셀에 들어가 있으면 그 셀에서 제거(중복 배정 방지)
        for idx, sid in list(self._cell_assign.items()):
            if sid == sender_id and idx != cell_index:
                try:
                    if self.view_manager and 0 <= idx < len(self.view_manager.cells):
                        self.view_manager.cells[idx].clear()
                except Exception:
                    pass
                self._cell_assign.pop(idx, None)

        # 2) 해당 셀에 이전에 배정돼 있던 sender가 있으면, 매핑만 제거
        prev_sid = self._cell_assign.get(cell_index)
        if prev_sid and prev_sid != sender_id:
            self._cell_assign.pop(cell_index, None)

        # 3) UI 스레드에서 대상 sender 위젯을 셀에 재배치
        def _ensure_and_put():
            w = self.ui.ensure_widget(sender_id, target.sender_name)
            if w and self.view_manager and 0 <= cell_index < len(self.view_manager.cells):
                try:
                    w.setParent(None)
                except Exception:
                    pass
                self.view_manager.cells[cell_index].put_widget(w)

                if not w.isVisible():
                    w.show()

                GLib.idle_add(target.update_window_from_widget, w)

                from config import UI_OVERLAY_DELAY_MS
                def _rebind():
                    # 선택 셀에 들어간 대상은 확실히 재생/바인딩
                    target.resume_pipeline()
                    target._force_overlay_handle()
                    # 이후 전체 동기화로 여러 셀에 배정된 sender들도 PLAYING 되도록
                    self._sync_play_states()
                    return False
                GLib.timeout_add(UI_OVERLAY_DELAY_MS, _rebind)

            return False
        GLib.idle_add(_ensure_and_put)

        # 4) 상태 갱신(매핑 업데이트 후, 전체 상태 동기화)
        self._cell_assign[cell_index] = sender_id
        self.active_sender_id = sender_id
        # 즉시 한 번 동기화(지연 리바인딩 후에도 한 번 더 동기화됨)
        self._sync_play_states()

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
        if now_ms - self._last_switch_ms < SWITCH_COOLDOWN_MS:
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
                          {'role':'receiver', 'name':RECEIVER_NAME},
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
                    on_down=lambda x, reason="ice", **_: self._remove_sender(x, reason=reason)
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
            if not sid:
                return

            # peer가 아직 없으면 생성
            if sid not in self.peers:
                GLib.idle_add(self.ui.ensure_widget, sid, name or sid)
                peer = PeerReceiver(
                    self.sio, sid, name or sid, self.ui,
                    on_ready=self._on_switch_ready,
                    on_down=lambda x, reason="ice", **_: self._remove_sender(x, reason=reason)
                )
                self.peers[sid] = peer
                if sid not in self._order:
                    self._order.append(sid)
                GLib.idle_add(peer.prepare_window_handle)
                peer.start()
                GLib.idle_add(lambda p=peer: (p._ensure_transceivers(), p._maybe_create_offer()))

            peer = self.peers[sid]
            # 새 스트림 수신 준비: 우선 재생
            peer.resume_pipeline()
            GLib.idle_add(self.ui.ensure_widget, sid, name or peer.sender_name)

            # 첫 진입: 모드=1 만들고, 선택 셀로 옮기는 로직(기존 유지)
            if self.view_manager and self.view_manager.mode is None:
                GLib.idle_add(lambda: self.view_manager.set_mode(1))

                def _assign_into_cell():
                    w = self.ui._widgets.get(sid)
                    if w and self.view_manager:
                        try:
                            w.setParent(None)
                        except Exception:
                            pass
                        # view_manager가 선택된 셀로 배치해주는 헬퍼가 있다고 가정
                        # (없다면 외부에서 requestAssign 신호를 사용)
                        try:
                            self.view_manager.assign_sender_to_selected(w)
                        except Exception:
                            pass
                    return False
                GLib.idle_add(_assign_into_cell)

                from config import UI_OVERLAY_DELAY_MS
                GLib.timeout_add(UI_OVERLAY_DELAY_MS,
                                 lambda p=peer: (p._force_overlay_handle() or False))

            # 여러 셀에 배정된 sender는 모두 PLAYING 되도록 동기화
            self._sync_play_states()

            if self.active_sender_id is None:
                self._set_active_sender(sid)

            print(f"[SIO] sender-share-started: {peer.sender_name}")

        @self.sio.on('sender-share-stopped')
        def on_sender_share_stopped(data):
            sid = data.get('id') or data.get('senderId') or data.get('from')
            if not sid: 
                return
            peer = self.peers.get(sid)
            if not peer:
                return

            peer.pause_pipeline()  # PAUSED

            # 이 sender가 들어가 있던 모든 셀 비우고 매핑 제거
            for idx, s in list(self._cell_assign.items()):
                if s == sid:
                    try:
                        if self.view_manager and 0 <= idx < len(self.view_manager.cells):
                            self.view_manager.cells[idx].clear()
                    except Exception:
                        pass
                    self._cell_assign.pop(idx, None)

            GLib.idle_add(self.ui.remove_sender_widget, sid)

            if self.active_sender_id == sid:
                actives = self._active_sender_ids()
                next_sid = actives[0] if actives else None
                self._set_active_sender(next_sid)

            # 남은 배정 기준으로 재생/정지 동기화
            self._sync_play_states()
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
        peer = self.peers.pop(sid, None)
        try:
            if peer:
                peer.stop()
        except:
            pass

        # 이 sender 매핑 제거 + 셀 비우기
        for idx, s in list(self._cell_assign.items()):
            if s == sid:
                try:
                    if self.view_manager and 0 <= idx < len(self.view_manager.cells):
                        self.view_manager.cells[idx].clear()
                except Exception:
                    pass
                self._cell_assign.pop(idx, None)

        try:
            self._order.remove(sid)
        except ValueError:
            pass

        GLib.idle_add(self.ui.remove_sender_widget, sid)

        if self.active_sender_id == sid:
            actives = self._active_sender_ids()
            self._set_active_sender(actives[0] if actives else None)

        # 남은 배정 기준으로 재생/정지 동기화
        self._sync_play_states()
