const express = require('express');
const http = require('http');
const socketIO = require('socket.io');

const app = express();
const server = http.createServer(app);
const io = socketIO(server, { cors: { origin: "*" } });

const rooms = {};

// 클라이언트 소켓 연결 시 이벤트
io.on('connection', (socket) => {
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

  // 방 삭제 처리
  socket.on('del-room', ({ role }) => {
    const roomId = socket.password;
    if (!roomId || !rooms[roomId]) return;

    if (role === 'receiver') {
      // 방에 있는 모든 sender에게 알림 (선택)
      Object.keys(rooms[roomId].senders).forEach(senderId => {
        io.to(senderId).emit('room-deleted');
      });

      // 방 제거
      delete rooms[roomId];
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
      const senderExist = Object.values(rooms[password].senders).some(
        sender => sender.name === senderName
      );

      if (senderExist) {
        socket.emit('join-error', '이미 사용 중인 이름입니다.');
        return;
      }

      // sender 입장
      rooms[password].senders[socket.id] = { id: socket.id, name: senderName || `Sender-${socket.id.slice(0, 5)}` };
      // receiver에게 새 sender 알림
      if (rooms[password].receiver) {
        io.to(rooms[password].receiver).emit('new-sender', rooms[password].senders[socket.id]);
      }
    }
    socket.password = password;
    socket.role = role; 

    // 서버: sender 입장 처리 시
    socket.emit('join-complete', { password });

  });

  socket.on('disconnect', () => {
    const roomId = socket.password;
    const role = socket.role;
    
    if (!roomId || !rooms[roomId]) return;

    if (role === 'sender') {
      // 송신자 제거
      delete rooms[roomId].senders[socket.id];

      // receiver에게 송신자 제거 알림
      if (rooms[roomId].receiver) {
        io.to(rooms[roomId].receiver).emit('remove-sender', socket.id);
      }
    }
  });

  // WebRTC 시그널 메시지 중계
  socket.on('signal', (data) => {
    io.to(data.to).emit('signal', data);
  });
});

const PORT = 3001;
server.listen(PORT, () => {
  console.log(`Signaling server listening on port ${PORT}`);
});
