# sender 입장하면 playing

# receiver_manager.py
# 멀티 수신기 관리자 클래스
import json
import threading
import ssl
import socketio
from gi.repository import GLib
from PyQt5 import QtCore

from config import SIGNALING_URL, RECEIVER_NAME, UI_OVERLAY_DELAY_MS
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

        # 현재 레이아웃에서 어떤 셀에 어떤 sender가 들어가 있는지
        self._cell_assign: dict[int, str] = {}   # cell_index -> sender_id

        self._bind_socket_events()

        if self.view_manager:
            self.view_manager.bind_manager(self)
            self.view_manager.set_senders_provider(self.list_active_senders)

    def _qt(callable_):
        QtCore.QTimer.singleShot(0, callable_)

    def start(self):
        """매니저 시작"""
        threading.Thread(target=self._sio_connect, daemon=True).start()

    def stop(self):
        """매니저 정지"""
        try:
            for _, peer in list(self.peers.items()):
                peer.stop()
        except:
            pass
        try:
            if self.sio.connected:
                self.sio.disconnect()
        except:
            pass

    # ----- 상태 쿼리 -----
    def _active_sender_ids(self):
        """현재 '공유 중'인 sender들만"""
        return [sid for sid, p in self.peers.items() if p.share_active]

    def list_active_senders(self):
        return [(sid, p.sender_name) for sid, p in self.peers.items()]

    # ----- 모드 전환/셀 배정 보조 -----
    def pause_all_streams(self):
        """모드 전환 시 전체 정지(준비상태)"""
        for p in self.peers.values():
            try:
                p.pause_pipeline()
            except Exception:
                pass
        self._cell_assign.clear()

    def _sync_play_states(self):
        """셀에 배정된 sender는 PLAYING, 그 외는 PAUSED"""
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

        # 동일 sender가 다른 셀에 있으면 제거
        for idx, sid in list(self._cell_assign.items()):
            if sid == sender_id and idx != cell_index:
                try:
                    if self.view_manager and 0 <= idx < len(self.view_manager.cells):
                        self.view_manager.cells[idx].clear()
                except Exception:
                    pass
                self._cell_assign.pop(idx, None)

        # 해당 셀의 이전 매핑 제거
        prev_sid = self._cell_assign.get(cell_index)
        if prev_sid and prev_sid != sender_id:
            self._cell_assign.pop(cell_index, None)

        # UI 스레드에서 위젯 배치
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
                    target.resume_pipeline()       # 배정된 것은 재생
                    target._force_overlay_handle()
                    self._sync_play_states()       # 전체 동기화
                    return False
                GLib.timeout_add(UI_OVERLAY_DELAY_MS, _rebind)
            return False
        GLib.idle_add(_ensure_and_put)

        # 매핑 갱신 + 전체 동기화
        self._cell_assign[cell_index] = sender_id
        self._sync_play_states()

    # ----- 소켓 연결 -----
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
            # 초기 입장 시 sender 목록. 각 sender에 PeerReceiver 세팅.
            print("[SIO] sender-list:", sender_arr)
            if not sender_arr:
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
                    on_ready=None,     # 전환 측정/로그 제거
                    on_down=lambda x, reason="ice", **_: self._remove_sender(x, reason=reason)
                )
                self.peers[sid] = peer
                if sid not in self._order:
                    self._order.append(sid)

                GLib.idle_add(peer.prepare_window_handle)

                peer.start()
                GLib.idle_add(lambda p=peer: (p._ensure_transceivers(), p._maybe_create_offer()))

                # 자동 share 요청(기존 유지)
                self.sio.emit('share-request', {'to': sid})
                print(f"[SIO] share-request → {sid} ({name})")
                
                # 참여자 목록 변경 MQTT 알림
                self._notify_mqtt_change()  

        # --- on_sender_share_started 만 교체 ---
        @self.sio.on('sender-share-started')
        def on_sender_share_started(data):
            sid  = data.get('id') or data.get('senderId') or data.get('from')
            name = data.get('name')
            if not sid:
                return

            # 1) peer 없으면 생성
            if sid not in self.peers:
                _qt(lambda: self.ui.ensure_widget(sid, name or sid))
                peer = PeerReceiver(
                    self.sio, sid, name or sid, self.ui,
                    on_ready=None,
                    on_down=lambda x, reason="ice", **_: self._remove_sender(x, reason=reason)
                )
                self.peers[sid] = peer
                if sid not in self._order:
                    self._order.append(sid)
                _qt(peer.prepare_window_handle)  # Qt 스레드에서 핸들 준비
                peer.start()
                _qt(lambda p=peer: (p._ensure_transceivers(), p._maybe_create_offer()))

            peer = self.peers[sid]

            if not self._cell_assign:
                # (A) 스택에 즉시 표시 + 오버레이 바인딩 + 재생 — 모두 Qt 스레드
                def _show_now():
                    w = self.ui.ensure_widget(sid, name or peer.sender_name)
                    if w and not w.isVisible():
                        w.show()
                    self.ui.set_active_sender_name(sid, name or peer.sender_name)
                    peer.update_window_from_widget(w)   # winId 확보 + 최초 바인딩
                    peer.resume_pipeline()              # PLAYING
                _qt(_show_now)

                # (B) 아주 짧은 지연 후 1분할 전환 → cells 준비되면 셀0에 배정
                def _enter_single_mode_and_assign():
                    if self.view_manager and self.view_manager.mode != 1:
                        self.view_manager.set_mode(1)   # 내부에서 pause_all 발생
                    # cells 생성 완료까지 폴링 (Qt 타이머로)
                    def _try_assign():
                        if not self.view_manager or not self.view_manager.cells:
                            QtCore.QTimer.singleShot(0, _try_assign)
                            return
                        self.assign_sender_to_cell(0, sid)  # 내부에서 resume + overlay 재바인딩 + sync
                    QtCore.QTimer.singleShot(0, _try_assign)

                QtCore.QTimer.singleShot(50, _enter_single_mode_and_assign)  # 스택 표시 직후 약간 뒤
            else:
                # 두 번째 이후는 입장 즉시 대기(PAUSED)
                peer.pause_pipeline()

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

        # 남은 배정 기준으로 재생/정지 동기화
        self._sync_play_states()
        
        # 참여자 퇴장: 참여자 목록 변경 MQTT 알림
        self._notify_mqtt_change()     

# ---------- 상태 조회 메서드들 ----------
    
    def _notify_mqtt_change(self):
       """참여자 변경시 MQTT 알림"""
       if self.mqtt_publisher:
           user_names = self.get_all_senders_name()
           self.mqtt_publisher.publish("participant/update", json.dumps(user_names))

    def get_all_senders_name(self):
        """MQTT 응답용 사용자 이름 리스트"""
        return [ self.peers[sid].sender_name 
                for sid, peer in self.peers.items()] 

    def get_all_senders(self):
        """연결된 모든 sender들의 정보 반환
        
        Returns:
            list: [{sender_id, sender_name, share_active}, ...] 형태의 리스트
                  모든 연결된 sender들 포함 (공유 중지된 것도 포함)
        
        Example:
             [
                {id: 'abc123def456', name: 'Desktop-Computer', active: True}, 
                {id: '789xyz321', name: 'Laptop-User', active: True},
                {id: 'qwe456rty', name: 'Mobile-Phone', active: False}
            ]

        """
        return [{"id": sid, 
                 "name": peer.sender_name,
                 "active": peer.share_active
                 } 
                for sid, peer in self.peers.items()] 
        
    # 추후, sender들의 화면 공유 상태에 따라 공유 활성화된 사용자 리스트가 필요할 때를 대비한 함수.  
    def get_active_senders(self):
        """현재 화면 공유 중인 sender들의 정보 반환
        
        Returns:
            list: [{sender_id, sender_name}, ...] 형태의 리스트
                  공유 중인 sender들만 포함 (share_active=True)
        
        Example:
            [
                {id: 'abc123def456', name: 'Desktop-Computer'}, 
                {id: '789xyz321', name: 'Laptop-User'}
            ]
        """
        
        return [{"id": sid, 
                 "name":self.peers[sid].sender_name} 
                for sid in self._active_sender_ids()
                ]
