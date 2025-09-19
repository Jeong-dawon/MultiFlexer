import json, paho.mqtt.client as mqtt
from PyQt5 import QtCore
import time 

# 전역 변수로 receiver_manager 저장
receiver_manager = None
class MqttManager:
    def __init__(self, receiver_manager=None, view_mode_manager=None, ip="localhost", port=1883):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(ip, port)
        self.client.loop_start()
        self.receiver_manager = receiver_manager
        self.view_mode_manager = view_mode_manager
        self._last_stats_publish = {}  # sender_id별 마지막 발행 시각 기록
        
    # ---------- MQTT 중단 ----------
    def stop(self):
        """MQTT 클라이언트 종료"""
        self.client.loop_stop()
        self.client.disconnect()
        
    # ---------- 내부 유틸 ----------
    def _get_user_list_for_mqtt(self):
        """MQTT 응답용 사용자 이름 리스트"""
        if not self.receiver_manager:
            return []
            
        # get_active_senders_name() 메서드 직접 사용
        return self.receiver_manager.get_all_senders()
        
    # ---------- 외부 호출 메서드 ----------
    def broadcast_participant_update(self):
        """참여자 목록 변경시 자동 브로드캐스트"""
        user_list = self._get_user_list_for_mqtt()
        self.publish("participant/update", json.dumps(user_list))
        print(f"[MQTT] Broadcasted participant update: {user_list}")

    def publish(self, topic, payload):
        """MQTT 메시지 발행"""
        self.client.publish(topic, payload)

    # ---------- 콜백 ----------
    def _on_connect(self, client, userdata, flag, rc, prop=None):
        client.subscribe("participant/request") # "participant/request" 토픽으로 구독, 참여자 목록 요청 
        client.subscribe("screen/request") # "screen/request" 토픽으로 구독, 화면 상태 요청
        client.subscribe("screen/update") # "screen/update" 토픽으로 구독, 관리자의 화면 배치 정보 수신

    def _on_message(self, client, userdata, msg):
        print(f"Topic: {msg.topic}")        # 토픽 확인
        print(f"Message: {msg.payload.decode()}")  # 메시지 내용 확인
    
        if msg.topic == "participant/request":
            print(f"관리자가 사용자 목록을 요청합니다.")

            # 리스트를 JSON 문자열로 변환해서 전송
            self.publish("participant/response", json.dumps(self._get_user_list_for_mqtt()))
        
        elif msg.topic == "screen/request":
            print(f"관리자가 공유 화면 정보를 요청합니다.")        
            current_screen_info = self._get_current_screen_info()
            self.publish("screen/response", json.dumps(current_screen_info))
        
        elif msg.topic == "screen/update":
            print(f"관리자로부터 화면 배치 변경 요청을 받았습니다.")
            try:
                layout_data = json.loads(msg.payload.decode())
                print(f"받은 화면 배치 데이터: {layout_data}")
        
                from PyQt5 import QtCore
        
                print("[DEBUG] QMetaObject.invokeMethod 사용")
        
                # QMetaObject.invokeMethod 사용
                result = QtCore.QMetaObject.invokeMethod(
                    self.view_mode_manager,
                    "apply_layout_data",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(dict, layout_data)
                )
        
                print(f"[DEBUG] invokeMethod 결과: {result}")
        
            except Exception as e:
                print(f"[ERROR] screen/update 처리 중 오류: {e}")
                import traceback
                traceback.print_exc()

    # 현재 화면 정보 가져오기 (screen/request 처리용)
    def _get_current_screen_info(self):
        """현재 화면 배치 정보 반환"""
        try:
            if not self.view_mode_manager:
                return {"layout": 1, "participants": []}
        
            # view_mode_manager에서 현재 상태 가져오기
            current_layout = getattr(self.view_mode_manager, 'mode', 1) or 1
            cell_assignments = getattr(self.view_mode_manager, 'cell_assignments', {})
            
            # 참여자 정보 구성
            participants = []
            for cell_index, sender_id in cell_assignments.items():
                 # receiver_manager에서 이름 찾기
                sender_name = "Unknown"
                if self.receiver_manager:
                    if hasattr(self.receiver_manager, 'peers') and sender_id in self.receiver_manager.peers:
                        peer = self.receiver_manager.peers[sender_id]
                        sender_name = getattr(peer, 'sender_name', sender_id)
            
                participants.append({
                    'id': sender_id,
                    'name': sender_name
                })
        
            return {
                "layout": current_layout,
                "participants": participants
            }

        except Exception as e:
            print(f"[ERROR] 현재 화면 정보 조회 중 오류: {e}")
            return {"layout": 1, "participants": []}
        
    def publish_stats(self, sender_id, stats: dict, sender_name=None, interval: float = 1.0):
        """송신자별 STATS를 1초에 한 번만 MQTT로 발행"""
        now = time.time()
        last_time = self._last_stats_publish.get(sender_id, 0)

        if now - last_time >= interval:  # 최소 interval 초 간격 유지
            payload = {
            "name": sender_name,  
            **stats
        }
            self.publish("stats/update", json.dumps(payload))
            self._last_stats_publish[sender_id] = now
            print(f"[MQTT] Published stats: {payload}")