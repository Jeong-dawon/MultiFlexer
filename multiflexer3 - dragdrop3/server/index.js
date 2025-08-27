/*
// server - index.js
const express = require('express');
const http = require('http');
const socketIO = require('socket.io');

const app = express();
const server = http.createServer(app);
const io = socketIO(server, { cors: { origin: "*" } });

const rooms = {};

// 클라이언트 소켓 연결 시 이벤트
io.on('connection', (socket) => {
  // 화면 공유 요청 처리 (receiver → sender)
  socket.on('share-request', ({ to }) => {
    io.to(to).emit('share-request', { from: socket.id });
  });

  // sender가 화면 공유 시작을 알릴 때
  socket.on('share-started', () => {
    const password = socket.password;
    if (rooms[password] && rooms[password].receiver) {
      const senderInfo = rooms[password].senders[socket.id];
      const name = senderInfo?.name || `Sender-${socket.id.slice(0,5)}`;
      // receiver에게 sender가 화면 공유를 시작했다고 알림 (이름 포함)
      io.to(rooms[password].receiver).emit('sender-share-started', { senderId: socket.id, name });
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
  socket.on('join-room', ({ role, password, name }, cb) => {
    socket.join(password); // 소켓을 해당 방에 참가

    // 방 정보가 없다면 초기화
    rooms[password] = rooms[password] || { senders: {}, receiver: null };

    if (role === 'receiver') {
      // receiver 입장
      rooms[password].receiver = socket.id;
      // 이미 존재하는 sender 목록을 receiver에게 전달
      const senderArr = Object.values(rooms[password].senders).map(s => ({ id: s.id, name: s.name }));
      socket.emit('sender-list', senderArr);
    } else if (role === 'sender') {
      // 이름 중복 체크 (같은 room 안에서)
      const senderExist = Object.values(rooms[password].senders).some(
        sender => sender.name === name
      );

      if (senderExist) {
        socket.emit('join-error', '이미 사용 중인 이름입니다.');
        cb?.({ error: '이미 사용 중인 이름입니다.' });
        return;
      }

      // sender 입장
      const assignedName = name || `Sender-${socket.id.slice(0, 5)}`;
      rooms[password].senders[socket.id] = { id: socket.id, name: assignedName };

      // receiver에게 갱신된 sender 리스트 보내기 (전체 리스트로 동기화)
      if (rooms[password].receiver) {
        const senderArr = Object.values(rooms[password].senders).map(s => ({ id: s.id, name: s.name }));
        io.to(rooms[password].receiver).emit('sender-list', senderArr);
      }

      // 성공 콜백 / 확인
      cb?.({ success: true, name: assignedName });
      socket.emit('join-complete', { password, name: assignedName });
    }

    socket.password = password;
    socket.role = role;
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

    // (선택) receiver가 나갔을 때 방을 정리하려면 여기서 rooms[roomId] 정리
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
*/

// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------



// SignalingServer - index.js
const express = require('express');
const http = require('http');
const socketIO = require('socket.io');

const app = express();
const server = http.createServer(app);
const io = socketIO(server, { cors: { origin: "*" } });

// rooms 구조: key = password, value = { senders: { [id]: { id, name } }, receiver: socketId }
const rooms = {};

io.on('connection', (socket) => {
  socket.on('share-request', ({ to }) => {
    io.to(to).emit('share-request', { from: socket.id });
  });

  socket.on('share-started', ({ name }) => {
    const password = socket.password;
    if (rooms[password] && rooms[password].receiver) {
      const senderInfo = rooms[password].senders[socket.id];
      const displayName = senderInfo?.name || name || `Sender-${socket.id.slice(0,5)}`;
      io.to(rooms[password].receiver).emit('sender-share-started', { senderId: socket.id, name: displayName });
    }
  });

  // *** ADDED: sender가 공유를 중지했을 때 receiver에게 중계
  socket.on('sender-share-stopped', () => {
    const password = socket.password;
    const receiverId = rooms[password].receiver;
    if (receiverId) {
      io.to(receiverId).emit('sender-share-stopped', { senderId: socket.id });
    } 
  });
  //


  socket.on('del-room', ({ role }) => {
    const roomId = socket.password;
    if (!roomId || !rooms[roomId]) return;

    if (role === 'receiver') {
      // 모든 sender에게 삭제 알림
      Object.keys(rooms[roomId].senders).forEach(senderId => {
        io.to(senderId).emit('room-deleted');
      });
      delete rooms[roomId];
    }
  });

  socket.on('join-room', ({ role, password, name }, cb) => {
    // receiver는 방을 새로 만들거나 기존 방에 다시 들어옴
    if (role === 'receiver') {
      // 방이 없으면 만들고, 있으면 receiver 갱신
      rooms[password] = rooms[password] || { senders: {}, receiver: null };
      rooms[password].receiver = socket.id;

      // 기존 sender 목록 전달
      const senderArr = Object.values(rooms[password].senders).map(s => ({ id: s.id, name: s.name }));
      socket.emit('sender-list', senderArr);

      socket.password = password;
      socket.role = role;
      cb?.({ success: true });
      // (선택) receiver에게도 성공 신호를 줄 필요가 있다면 emit 가능
      return;
    }

    // sender인 경우: 존재하는, receiver가 있는 방만 허용
    if (!rooms[password] || !rooms[password].receiver) {
      const msg = '없는 방입니다.';
      socket.emit('join-error', msg);
      cb?.({ success: false, message: msg });
      return;
    }

    // 이름 중복 체크
    const senderExist = Object.values(rooms[password].senders).some(
      sender => sender.name === name
    );
    if (senderExist) {
      const msg = '이미 사용 중인 이름입니다.';
      socket.emit('join-error', msg);
      cb?.({ success: false, message: msg });
      return;
    }

    // sender 입장
    const assignedName = name || `Sender-${socket.id.slice(0, 5)}`;
    rooms[password].senders[socket.id] = { id: socket.id, name: assignedName };

    // receiver에게 갱신된 sender 리스트 전달
    if (rooms[password].receiver) {
      const senderArr = Object.values(rooms[password].senders).map(s => ({ id: s.id, name: s.name }));
      io.to(rooms[password].receiver).emit('sender-list', senderArr);
    }

    // 성공 응답
    cb?.({ success: true, name: assignedName });
    socket.emit('joined-room', { room: password, name: assignedName }); // 새로운 성공 이벤트
    socket.emit('join-complete', { password, name: assignedName }); // 기존 호환

    socket.password = password;
    socket.role = role;
  });

  socket.on('disconnect', () => {
    const roomId = socket.password;
    const role = socket.role;

    if (!roomId || !rooms[roomId]) return;

    if (role === 'sender') {
      delete rooms[roomId].senders[socket.id];
      if (rooms[roomId].receiver) {
        io.to(rooms[roomId].receiver).emit('remove-sender', socket.id);
      }
    }

    // (선택) receiver가 나갔을 때 rooms[roomId]를 정리하려면 여기 처리
  });

  socket.on('signal', (data) => {
    io.to(data.to).emit('signal', data);
  });
});

const PORT = 3001;
server.listen(PORT, () => {
  console.log(`Signaling server listening on port ${PORT}`);
});
