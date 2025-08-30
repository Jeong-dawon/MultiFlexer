import json, paho.mqtt.client as mqtt

# 전역 변수로 receiver_manager 저장
receiver_manager = None
class MqttManager:
    def __init__(self, receiver_manager=None, ip="192.168.0.54", port=1883):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(ip, port)
        self.client.loop_start()
        self.receiver_manager = receiver_manager
        
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
        return self.receiver_manager.get_all_senders_name()
        
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
    def _on_connect(slef, client, userdata, flag, rc, prop=None):
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
            ##### client.publish("screen/response", get_user_list_for_mqtt)
        