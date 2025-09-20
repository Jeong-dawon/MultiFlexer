// mqttClient.js - MQTT 통신 담당
let client = null;                // MQTT 클라이언트 객체
let connectionFlag = false;       // 연결 상태
const CLIENT_ID = "client-" + Math.floor((1 + Math.random()) * 0x10000000000).toString(16); // 랜덤 클라이언트 ID

// HTML이 완전히 로드된 후 실행
document.addEventListener('DOMContentLoaded', function () {
	console.log("[DEBUG] DOM 로드 완료 - MQTT 연결 시작");
	connect();
});

function connect() {
	if (connectionFlag) return;

	const broker = window.location.hostname; // 웹소켓 브로커 URL
	const port = 9001;               // 웹소켓 포트

	console.log(`[DEBUG] 브로커 연결 시도: ${broker}:${port} | 클라이언트ID: ${CLIENT_ID}`);

	client = new Paho.MQTT.Client(broker, Number(port), "/mqtt", CLIENT_ID);

	client.onConnectionLost = onConnectionLost;
	client.onMessageArrived = onMessageArrived;

	client.connect({
		useSSL: true,
		onSuccess: onConnect,
		onFailure: (err) => console.error("[ERROR] 브로커 연결 실패:", err)
	});
}

function onConnect() {
	connectionFlag = true;
	console.log("[DEBUG] 브로커 연결 성공");

	// 브로커에 구독 신청
	subscribe("participant/response"); // (요청시) Reciver로부터 참여자 목록 받아옴
	subscribe("screen/response"); // (요청시) Reciver로부터 화면 공유 정보 받아옴
	subscribe("participant/update"); // (참여자 목록이 변할 때마다) Reciver로부터 참여자 목록 받아옴
	subscribe("stats/update");

	// 초기 데이터 요청
	publish("participant/request", "") // Reciver에게 참여자 목록 요청
	publish("screen/request", "") // Reciver에게 화면 공유 정보 요청
}

function subscribe(topic) {
	if (!connectionFlag) {
		console.error("[MQTT] 연결되지 않음");
		return false;
	}

	client.subscribe(topic);
	console.log(`[MQTT] 구독 신청: ${topic}`);
	return true;
}

function publish(topic, msg) {
	if (!connectionFlag) {
		console.error("[MQTT] 연결되지 않음");
		return false;
	}

	client.send(topic, msg, 0, false);

	console.log(`[MQTT] 메시지 전송: 토픽=${topic}, 내용=${msg}`);
	return true;
}

function unsubscribe(topic) {
	if (!connectionFlag) return;

	client.unsubscribe(topic);
	console.log(`[DEBUG] 구독 취소: ${topic}`);
}

function onConnectionLost(responseObject) {
	connectionFlag = false;
	console.warn("[MQTT] 연결 끊김", responseObject);
}

function onMessageArrived(msg) {
	console.log(`[MQTT] 메시지 도착: 토픽=${msg.destinationName}, 내용=${msg.payloadString}`);

	if (msg.destinationName == "participant/response" || msg.destinationName == "participant/update") {
		try {
			// JSON 문자열을 JavaScript 배열로 변환
			const userList = JSON.parse(msg.payloadString);
			console.log("[MQTT] 사용자 목록:", userList);

			// 데이터 구조 확인
			if (Array.isArray(userList)) {
				userList.forEach((user, index) => {
					console.log(`사용자 ${index + 1}:`, {
						id: user.id,
						name: user.name,
						active: user.active
					});
				});

				// 사용자 목록 UI 업데이트
				if (window.stateManager) {
					window.stateManager.updateAllParticipants(userList);
				}
			} else {
				console.warn("[MQTT] 예상과 다른 데이터 구조:", userList);
			}

		} catch (error) {
			console.error("[MQTT] JSON 파싱 실패:", error);
			console.error("[MQTT] 원본 데이터:", msg.payloadString);
		}
	}

	else if (msg.destinationName === "screen/response") {
		try {
			const screenData = JSON.parse(msg.payloadString);
			console.log("[MQTT] 화면 공유 정보:", screenData);

			// 상태 관리자 업데이트
			if (window.stateManager) {
				window.stateManager.updateSharingInfo(screenData);  // updateSharingInfo 구현 필요
			}

		} catch (error) {
			console.error("[MQTT ERROR] 화면 공유 정보 파싱 실패:", error);
		}
	}

	else if (msg.destinationName === "stats/update") {
		try {
			const stats = JSON.parse(msg.payloadString);
			console.log("[DEBUG][STATS 메시지 수신]", stats);

			requestAnimationFrame(() => {
				handleStatsMessage(stats);
			});
		} catch (err) {
			console.error("[STATS] JSON parse error:", err);
			console.error("[STATS] 원본 메시지:", msg.payloadString);
		}
	}

	if (msg.destinationName == "participant/update") {
		try {
			const userList = JSON.parse(msg.payloadString);
			console.log("[MQTT] 사용자 목록:", userList);

			if (Array.isArray(userList)) {
				if (window.stateManager) {
					window.stateManager.updateAllParticipants(userList);
				}

				// 🔑 여기서 화면 상태 다시 요청
				publish("screen/request", "");
			}
		} catch (error) {
			console.error("[MQTT] JSON 파싱 실패:", error);
		}
	}

}

function disconnect() {
	if (!connectionFlag) return;

	client.disconnect();
	connectionFlag = false;
	console.log("[MQTT] 브로커 연결 종료");
}

// 배치 상태 전송 (상태 관리자에서 호출)
function publishPlacementState(placementData) {
	publish("screen/update", placementData);
}

// 연결 상태 확인
function isConnected() {
	return connectionFlag && client && client.isConnected();
}

// 전역 함수로 노출
window.publishPlacementState = publishPlacementState;
window.isConnected = isConnected;