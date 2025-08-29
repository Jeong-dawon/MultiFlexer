// SignalingServer - index.js

const fs = require('fs');
const path = require('path');
const express = require('express');
const https = require('https');
const socketIO = require('socket.io');

const app = express();

// 인증서 파일 경로
const certPath = path.join(__dirname, '../sender/cert.pem');
const keyPath  = path.join(__dirname, '../sender/key.pem');
const options = {
  key: fs.readFileSync(keyPath),
  cert: fs.readFileSync(certPath),
};

const server = https.createServer(options, app);
const io = socketIO(server, { cors: { origin: "*" } });


let receiver = null;                // 단일 리시버 소켓 ID
const senders = {};                 // senderId -> { id, name }

// ---------- Helper ----------
function emitSenderList() {
  if (!receiver) return;
  const senderArr = Object.values(senders).map(s => ({ id: s.id, name: s.name }));
  io.to(receiver).emit('sender-list', senderArr);
}

// ---------- Socket Events ----------
io.on('connection', (socket) => {
  // --- misc events ---
  socket.on('share-request', ({ to }) => {
    io.to(to).emit('share-request', { from: socket.id });
  });

  // sender가 공유 시작
  socket.on('share-started', ({ name }) => {
    if (!receiver) return;
    const senderInfo = senders[socket.id];
    const displayName = senderInfo?.name || name || `Sender-${socket.id.slice(0,5)}`;
    io.to(receiver).emit('sender-share-started', { id: socket.id, name: displayName });
    emitSenderList();
  });

  // sender가 공유 중지
  socket.on('sender-share-stopped', () => {
    if (receiver) io.to(receiver).emit('sender-share-stopped', { id: socket.id });
    // 공유 중지 = 방 퇴장은 아님
  });

  // receiver가 방 삭제
  socket.on('del-room', ({ role }) => {
    if (role === 'receiver') {
      Object.keys(senders).forEach(senderId => {
        io.to(senderId).emit('room-deleted');
      });
      receiver = null;
      for (const k of Object.keys(senders)) delete senders[k];
    }
  });

  // --- join-room ---
  socket.on('join-room', ({ role, name }, cb) => {
    if (role === 'receiver') {
      receiver = socket.id;
      emitSenderList();
      socket.role = role;
      cb?.({ success: true });
      return;
    }

    // sender
    if (!receiver) {
      const msg = '리시버가 없습니다.';
      socket.emit('join-error', msg);
      cb?.({ success: false, message: msg });
      return;
    }

    // 이름 중복 방지
    const exists = Object.values(senders).some(s => s.name === name);
    if (exists) {
      const msg = '이미 사용 중인 이름입니다.';
      socket.emit('join-error', msg);
      cb?.({ success: false, message: msg });
      return;
    }

    const assignedName = name || `Sender-${socket.id.slice(0,5)}`;
    senders[socket.id] = { id: socket.id, name: assignedName };

    emitSenderList();

    socket.role = role;
    cb?.({ success: true, name: assignedName });
    socket.emit('joined-room', { name: assignedName });
    socket.emit('join-complete', { name: assignedName });
  });

  // --- signaling relay ---
  socket.on('signal', (data) => {
    data = data || {};
    data.from = socket.id;

    if (socket.role === 'sender') {
      if (!receiver) return;
      data.to = receiver;
    } else if (socket.role === 'receiver') {
      const target = data?.to;
      if (!target || !senders[target]) return;
    } else {
      return;
    }

    console.log('[SRV] relay', data?.type, 'from', socket.id, 'to', data?.to, '(role:', socket.role, ')');
    io.to(data.to).emit('signal', data);
  });

  // --- disconnect cleanup ---
  socket.on('disconnect', () => {
    const role = socket.role;
    if (role === 'sender') {
      delete senders[socket.id];
      if (receiver) {
        io.to(receiver).emit('sender-disconnected', { id: socket.id });
        emitSenderList();
      }
    } else if (role === 'receiver') {
      Object.keys(senders).forEach(senderId => {
        io.to(senderId).emit('room-deleted');
      });
      receiver = null;
      for (const k of Object.keys(senders)) delete senders[k];
    }
  });
});

// ---------- Start Server ----------
const PORT = 3000;
server.listen(PORT, () => {
  console.log(`Signaling server listening on port ${PORT}`);
});
