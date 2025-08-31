import json, paho.mqtt.client as mqtt

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
                # JSON 메시지 파싱
                layout_data = json.loads(msg.payload.decode())
                print(f"받은 화면 배치 데이터: {layout_data}")
            
                # 화면 배치 적용
                self.view_mode_manager.apply_layout_data(layout_data)
                # self._apply_screen_layout(layout_data)
            
            except json.JSONDecodeError as e:
               print(f"[ERROR] JSON 파싱 실패: {e}")
            except Exception as e:
              print(f"[ERROR] 화면 배치 적용 실패: {e}")
       
    # 현재 화면 정보 가져오기 (screen/request 처리용)
    def _get_current_screen_info(self):
        """현재 화면 배치 정보 반환"""
        try:
            if not self.view_mode_manager:
                return {"layout": 1, "participants": []}
        
            # view_mode_manager에서 현재 상태 가져오기
            current_layout = getattr(self.view_mode_manager, 'current_layout', 1)
            active_screens = []
        
            # 현재 활성화된 화면들 가져오기
            if hasattr(self.view_mode_manager, 'get_active_screens'):
                active_screens = self.view_mode_manager.get_active_screens()
        
            # 참여자 정보 구성
            participants = []
            for screen_info in active_screens:
                if 'peer_id' in screen_info and 'position' in screen_info:
                    peer_id = screen_info['peer_id']
                    if self.receiver_manager and peer_id in self.receiver_manager.peers:
                        peer = self.receiver_manager.peers[peer_id]
                        participants.append({
                            'id': peer_id,
                            'name': peer.sender_name,
                            'position': screen_info['position']
                        })
        
            return {
                "layout": current_layout,
                "participants": participants
            }

        except Exception as e:
            print(f"[ERROR] 현재 화면 정보 조회 중 오류: {e}")
            return {"layout": 1, "participants": []}