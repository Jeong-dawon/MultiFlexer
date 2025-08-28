// SignalingServer - index.js
const fs = require('fs');    
const path = require('path');
const express = require('express');
const https = require('https');
const socketIO = require('socket.io');

const app = express();


//  인증서 파일 경로 (L
const certPath = path.join(__dirname, '../sender/cert.pem');
const keyPath  = path.join(__dirname, '../sender/key.pem');
const options = {
  key: fs.readFileSync(keyPath),
  cert: fs.readFileSync(certPath),
};


const server = https.createServer(options, app);
const io = socketIO(server, { cors: { origin: "*" } });

// rooms: { [password]: { senders: { [id]: { id, name } }, receiver: socketId } }
const rooms = {};


function emitSenderList(password) {
  const r = rooms[password];
  if (!r || !r.receiver) return;
  const senderArr = Object.values(r.senders).map(s => ({ id: s.id, name: s.name }));
  io.to(r.receiver).emit('sender-list', senderArr);
}

io.on('connection', (socket) => {
  // --- misc events ---
  socket.on('share-request', ({ to }) => {
    io.to(to).emit('share-request', { from: socket.id });
  });

  // sender가 공유 시작했을 때 → receiver에게 알림
  socket.on('share-started', ({ name }) => {
    const password = socket.password;
    if (!password || !rooms[password]) return;
    const receiverId = rooms[password].receiver;
    if (!receiverId) return;

    const senderInfo = rooms[password].senders[socket.id];
    const displayName = senderInfo?.name || name || `Sender-${socket.id.slice(0,5)}`;

    // ✅ 리시버가 기대하는 키로 통일: { id, name }
    io.to(receiverId).emit('sender-share-started', { id: socket.id, name: displayName });
    // 필요 시 목록도 동기화
    emitSenderList(password);
  });

  // sender가 공유 중지 → 해당 화면만 내려가도록 알림
  socket.on('sender-share-stopped', () => {
    const password = socket.password;
    if (!password || !rooms[password]) return;
    const receiverId = rooms[password].receiver;
    if (receiverId) io.to(receiverId).emit('sender-share-stopped', { id: socket.id });
    // 공유 중지 = 방 퇴장은 아님 → 목록은 유지
  });

  // receiver가 방 삭제
  socket.on('del-room', ({ role }) => {
    const roomId = socket.password;
    if (!roomId || !rooms[roomId]) return;
    if (role === 'receiver') {
      Object.keys(rooms[roomId].senders).forEach(senderId => {
        io.to(senderId).emit('room-deleted');
      });
      delete rooms[roomId];
    }
  });

  // --- join-room ---
  socket.on('join-room', ({ role, password, name }, cb) => {
    if (!password) return cb?.({ success: false, message: '비밀번호 필요' });

    if (role === 'receiver') {
      rooms[password] = rooms[password] || { senders: {}, receiver: null };
      rooms[password].receiver = socket.id;

      // 현재 sender 목록 전달
      emitSenderList(password);

      socket.password = password;
      socket.role = role;
      cb?.({ success: true });
      return;
    }

    // sender
    if (!rooms[password] || !rooms[password].receiver) {
      const msg = '없는 방입니다.';
      socket.emit('join-error', msg);
      cb?.({ success: false, message: msg });
      return;
    }

    // 이름 중복 방지
    const exists = Object.values(rooms[password].senders).some(s => s.name === name);
    if (exists) {
      const msg = '이미 사용 중인 이름입니다.';
      socket.emit('join-error', msg);
      cb?.({ success: false, message: msg });
      return;
    }

    const assignedName = name || `Sender-${socket.id.slice(0,5)}`;
    rooms[password].senders[socket.id] = { id: socket.id, name: assignedName };

    // receiver 갱신
    emitSenderList(password);

    socket.password = password;
    socket.role = role;

    cb?.({ success: true, name: assignedName });
    socket.emit('joined-room', { room: password, name: assignedName });
    socket.emit('join-complete', { password, name: assignedName });
  });

  // --- signaling relay ---
  socket.on('signal', (data) => {
    const roomId = socket.password;
    if (!roomId || !rooms[roomId]) return;

    // 항상 from을 보장(클라가 빠뜨려도)
    data = data || {};
    data.from = socket.id;

    if (socket.role === 'sender') {
      const receiverId = rooms[roomId].receiver;
      if (!receiverId) return;
      data.to = receiverId;
    } else if (socket.role === 'receiver') {
      const target = data?.to;
      if (!target || !rooms[roomId].senders[target]) return; // 존재 검증
    } else {
      return;
    }

    console.log('[SRV] relay', data?.type, 'from', socket.id, 'to', data?.to, '(role:', socket.role, ')');
    io.to(data.to).emit('signal', data);
  });

  // --- disconnect cleanup ---
  socket.on('disconnect', () => {
    const roomId = socket.password;
    const role = socket.role;
    if (!roomId || !rooms[roomId]) return;

    if (role === 'sender') {
      // 방에서 sender 제거 + 리시버에게 알려주기
      delete rooms[roomId].senders[socket.id];

      const receiverId = rooms[roomId].receiver;
      if (receiverId) {
        // ✅ 리시버가 처리할 이벤트명/키에 맞춤
        io.to(receiverId).emit('sender-disconnected', { id: socket.id });
        // 목록도 갱신해서 UI가 상태를 재동기화할 수 있게
        emitSenderList(roomId);
      }
    } else if (role === 'receiver') {
      // 방의 모든 sender에게 방 종료 알림 후 방 제거
      Object.keys(rooms[roomId].senders).forEach(senderId => {
        io.to(senderId).emit('room-deleted');
      });
      delete rooms[roomId];
    }
  });
});

const PORT = 3001;
server.listen(PORT, () => {
  console.log(`Signaling server listening on port ${PORT}`);
});
