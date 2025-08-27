const WebSocket = require("ws"); // 클라이언트와 실시간으로 데이터를 주고받기 위해 WebSocket 모듈을 가져온다. 
const wss = new WebSocket.Server({ port: 8080 }); // 포트 8080에서 WebSocket 서버를 열기 - 클라이언트가 이 포트로 접속

const rooms = {}; // 방 정보 저장할 객체 - 각 방마다 참가한 클라이언트들 저장

wss.on("connection", (socket) => { // 클라이언트가 서버에 연결되었을 때 실행
  console.log("A user connected");

  socket.on("message", (message) => { // 클라이언트로부터 메시지 받았을 때 실행
    let data;
    try {
      data = JSON.parse(message); // 메시지 문자열 형태 ->JSON 형식 변환
    } catch (err) {
      console.error("Invalid JSON", message);
      return;
    }

    switch (data.type) { // 메시지 종류에 따라 다르게 처리
      case "join":
        {
          // 🚨
          const { roomId, role } = data; // 클라이언트가 보낸 방 ID, 역할 가져오기

          if (!rooms[roomId]) { // 방 없으면 새로 만들기
            rooms[roomId] = new Set();
          }

          // 🚨 sender 중복 접속 방지
          if (role === "sender") {
            const senderExists = Array.from(rooms[roomId]).some(s => s.role === "sender"); // sender 역할 존재 확인

            if (senderExists) { // sender 이미 존재하면 에러 메시지 보내고 join 요청 거부
              socket.send(JSON.stringify({ type: "error", message: "Sender already exists in this room." }));
              return;
            }
          }

          if (rooms[roomId].size >= 2) { // 방에 이미 2명 있으면 더 입장 못하게 하고 에러 전송
            socket.send(JSON.stringify({ type: "error", message: "Room full" }));
            return;
          }

          rooms[roomId].add(socket); // 해당 사용자 방에 추가
          socket.roomId = roomId; // 사용자가 어떤 방에 속해 있는지 소켓에 기록
          // 🚨
          socket.role = role; // 사용자 역할 소켓에 저장

          console.log(`User joined room ${roomId}`);
        }
        break;

      case "signal": // 시그널링 메시지 전송 시
        {
          const { roomId, signalData } = data; // 방 번호, 전달 데이터 가져오기

          if (!rooms[roomId]) {
            console.warn(`No such room: ${roomId}`); // 방이 존재하지 않으면 경고 출력하고 무시
            return;
          }

          // 🚨 sender2가 거절당했는데도 signal을 보내는 걸 막음
          if (!rooms[roomId].has(socket)) {
            console.warn("Socket not part of room; ignoring signal"); // 이 소켓이 해당 방 구성원 아니면 무시
            return;
          }

          rooms[roomId].forEach((client) => {
            if (client !== socket && client.readyState === WebSocket.OPEN) { // 자신 제외 다른 클라이언트에게만 데이터 전송
              client.send(JSON.stringify(signalData)); // 시그널링데이터 JSON 형태로 보내기
            }
          });

          console.log(`Signal relayed in room ${roomId}`);
        }
        break;

      default:
        console.log("Unknown message type:", data.type); // 알 수 없는 메시지 타입 경고
    }
  });

  socket.on("close", () => { // 클라이언트가 연결 끊었을 때 실행
    const { roomId } = socket; // 이 소켓이 속했던 방 가져오기

    if (roomId && rooms[roomId]) {
      rooms[roomId].delete(socket); // 해당 방에서 이 소켓(사용자) 제거
      console.log(`User disconnected from room ${roomId}`); // 콘솔에 어떤 방에서 누가 나갔는지 출력

      if (rooms[roomId].size === 0) {
        delete rooms[roomId]; // 방에 아무도 없으면 방 자체를 삭제(메모리 절약)
        console.log(`Room ${roomId} deleted`);
      }
    }
  });
});
