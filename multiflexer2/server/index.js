const express = require('express');
const http = require('http');
const socketIO = require('socket.io');

const app = express();
const server = http.createServer(app);
const io = socketIO(server, { cors: { origin: "*" } });

const rooms = {};

// 클라이언트 소켓 연결 시 이벤트
io.on('connection', (socket) => {
  console.log('New client:', socket.id);

  // 화면 공유 요청 처리
  socket.on('share-request', ({ to }) => {
    // to = sender socket id
    io.to(to).emit('share-request');
  });

  // sender가 화면 공유 시작을 알릴 때
  socket.on('share-started', () => {
    const password = socket.password;
    if (rooms[password] && rooms[password].receiver) {
      // receiver에게 sender가 화면 공유를 시작했다고 알림
      io.to(rooms[password].receiver).emit('sender-share-started', { senderId: socket.id });
      // 
    }
  });

  // 방 입장 처리
  socket.on('join-room', ({ role, password, senderName }) => {
    socket.join(password); // 소켓을 해당 방에 참가

    // 방 정보가 없다면 초기화
    rooms[password] = rooms[password] || { senders: {}, receiver: null };

    if (role === 'receiver') {
      // receiver 입장
      rooms[password].receiver = socket.id;
      // 이미 존재하는 sender 목록을 receiver에게 전달
      socket.emit('sender-list', Object.values(rooms[password].senders));
    } else if (role === 'sender') {
      // sender 입장
      rooms[password].senders[socket.id] = { id: socket.id, name: senderName || `Sender-${socket.id.slice(0, 5)}` };
      // receiver에게 새 sender 알림
      if (rooms[password].receiver) {
        io.to(rooms[password].receiver).emit('new-sender', rooms[password].senders[socket.id]);
      }
    }
    socket.password = password;
  });


  // WebRTC 시그널 메시지 중계
  socket.on('signal', (data) => {
    io.to(data.to).emit('signal', data);
  });

  // 클라이언트 연결 해제(나가기) 시 처리
  socket.on('disconnect', () => {
    const password = socket.password;
    if (!password || !rooms[password]) return;
    if (socket.role === 'sender') {
      // 송신자였던 경우: rooms에서 제거
      delete rooms[password].senders[socket.id];
      if (rooms[password].receiver) {
        io.to(rooms[password].receiver).emit('remove-sender', socket.id);
      }
    } else if (socket.role === 'receiver') {
      // 리시버가 나가면 해당 방의 receiver 정보 비우기
      rooms[password].receiver = null;
    }
  });
});

const PORT = 3001;
server.listen(PORT, () => {
  console.log(`Signaling server listening on port ${PORT}`);
});
