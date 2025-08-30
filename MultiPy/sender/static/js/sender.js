let client = null;                // MQTT 클라이언트 객체
let connectionFlag = false;       // 연결 상태
const CLIENT_ID = "client-" + Math.floor((1 + Math.random()) * 0x10000000000).toString(16); // 랜덤 클라이언트 ID

// HTML이 완전히 로드된 후 실행
document.addEventListener('DOMContentLoaded', function () {
	console.log("[DEBUG] DOM 로드 완료 - MQTT 연결 시작");
	connect();
});

window.onload = function () {
	const userName = sessionStorage.getItem('userName');
	// const clientId = sessionStorage.getItem('clientId');
	// const userRole = sessionStorage.getItem('userRole');

	console.log('사용자 이름:', userName);

	// UI에 사용자 이름 표시
	document.getElementById('welcomeMessage').innerHTML =
		`<span class="user-name">${userName}</span>님의 화면이 공유됩니다`;

	// 이제 userName을 사용해서 MQTT 참여 로직 실행
};

// 창 닫기/새로고침 이벤트 감지
window.addEventListener('beforeunload', function (event) {
	console.log("[DEBUG] 창 닫기/새로고침 감지 - user/leave 토픽 발행");

	if (connectionFlag) {
		// 동기적으로 메시지 전송 (창이 닫히기 전에 확실히 전송)
		publish("user/leave", userName);

		// 약간의 지연을 주어 메시지가 전송되도록 함
		const now = Date.now();
		while (Date.now() - now < 100) {
			// 100ms 동안 대기
		}

		disconnect();
	}
});

function connect() {
	if (connectionFlag) return;

	const broker = "localhost"; // 웹소켓 브로커 URL
	const port = 9001;               // 웹소켓 포트

	console.log(`[DEBUG] 브로커 연결 시도: ${broker}:${port} | 클라이언트ID: ${CLIENT_ID}`);

	client = new Paho.MQTT.Client(broker, Number(port), CLIENT_ID);

	client.onConnectionLost = onConnectionLost;
	client.onMessageArrived = onMessageArrived;

	client.connect({
		onSuccess: onConnect,
		onFailure: (err) => console.error("[ERROR] 브로커 연결 실패:", err)
	});
}

function onConnect() {
	connectionFlag = true;
	console.log("[DEBUG] 브로커 연결 성공");

	publish("user/join", userName) // 접속을 알림
}

function subscribe(topic) {
	if (!connectionFlag) {
		alert("연결되지 않았음");
		return false;
	}

	client.subscribe(topic);
	console.log(`[DEBUG] 구독 신청: ${topic}`);
	return true;
}

function publish(topic, msg) {

	if (!connectionFlag) {
		alert("연결되지 않았음");
		return false;
	}

	client.send(topic, msg, 0, false);

	console.log(`[DEBUG] 메시지 전송: 토픽=${topic}, 내용=${msg}`);
	return true;
}

function unsubscribe(topic) {
	if (!connectionFlag) return;

	client.unsubscribe(topic);
	console.log(`[DEBUG] 구독 취소: ${topic}`);
}

function onConnectionLost(responseObject) {
	connectionFlag = false;
	console.warn("[WARN] 연결 끊김", responseObject);
}

function onMessageArrived(msg) {
	console.log(`[DEBUG] 메시지 도착: 토픽=${msg.destinationName}, 내용=${msg.payloadString}`);
}

function disconnect() {
	if (!connectionFlag) return;

	client.disconnect();
	connectionFlag = false;
	console.log("[DEBUG] 브로커 연결 종료");
}
