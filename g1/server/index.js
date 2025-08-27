// server - index.js
const express = require('express');
const http = require('http');
const { Server } = require('socket.io');

const app = express();
const server = http.createServer(app);
const io = new Server(server, { cors: { origin: '*' } });

// rooms[password] = { receiver: socketId|null, receiverName: string|null, senders: { [id]: {id, name} } }
const rooms = {};

io.on('connection', (socket) => {
  console.log('[SRV] connect', socket.id);

  socket.on('join-room', ({ role, password, name }, cb) => {
    if (!password) return cb?.({ success: false, message: '비밀번호 필요' });
    rooms[password] = rooms[password] || { receiver: null, receiverName: null, senders: {} };
    const room = rooms[password];

    if (role === 'receiver') {
      room.receiver = socket.id;
      room.receiverName = name || `Receiver-${socket.id.slice(0,5)}`;
      socket.password = password; socket.role = 'receiver'; socket.join(password);
      cb?.({ success: true });
      io.to(socket.id).emit('joined-room', { room: password, name: room.receiverName });
      io.to(socket.id).emit('sender-list', Object.values(room.senders));
      // 송신자에게 현재 리시버 정보(선택)
      Object.keys(room.senders).forEach((sid) => {
        io.to(sid).emit('receiver-list', [{ id: room.receiver, name: room.receiverName }]);
      });
      return;
    }

    if (role === 'sender') {
      const assignedName = name || `Sender-${socket.id.slice(0,5)}`;
      room.senders[socket.id] = { id: socket.id, name: assignedName };
      socket.password = password; socket.role = 'sender'; socket.join(password);
      cb?.({ success: true, name: assignedName });
      io.to(socket.id).emit('joined-room', { room: password, name: assignedName });
      io.to(socket.id).emit('join-complete', { password, name: assignedName });
      // 리시버에게 목록 갱신
      if (room.receiver) {
        io.to(room.receiver).emit('sender-list', Object.values(room.senders));
        io.to(socket.id).emit('receiver-list', [{ id: room.receiver, name: room.receiverName }]);
      } else {
        io.to(socket.id).emit('receiver-list', []);
      }
      return;
    }

    cb?.({ success: false, message: 'role 필요' });
  });

  socket.on('share-request', ({ to }) => { if (to) io.to(to).emit('share-request', { from: socket.id }); });

  socket.on('sender-share-started', ({ name }) => {
    const roomId = socket.password;
    if (!roomId || !rooms[roomId]) return;
    const receiverId = rooms[roomId].receiver;
    if (!receiverId) return;

    const info = rooms[roomId].senders[socket.id];
    const displayName = info?.name || name || `Sender-${socket.id.slice(0,5)}`;
    io.to(receiverId).emit('sender-share-started', { senderId: socket.id, name: displayName });
  });

  socket.on('sender-share-stopped', () => {
    const roomId = socket.password;
    if (!roomId || !rooms[roomId]) return;
    const receiverId = rooms[roomId].receiver;
    if (receiverId) io.to(receiverId).emit('sender-share-stopped', { senderId: socket.id });
  });

  socket.on('signal', (data) => {
    const roomId = socket.password;
    if (!roomId || !rooms[roomId]) return;
    data.from = socket.id;

    if (socket.role === 'sender') {
      const receiverId = rooms[roomId].receiver;
      if (!receiverId) return;
      data.to = receiverId;
      io.to(receiverId).emit('signal', data);
    } else if (socket.role === 'receiver') {
      const target = data?.to;
      if (!target || !rooms[roomId].senders[target]) return;
      io.to(target).emit('signal', data);
    }
  });

  socket.on('del-room', ({ role }) => {
    const roomId = socket.password;
    if (!roomId || !rooms[roomId]) return;
    if (role === 'receiver' && rooms[roomId].receiver === socket.id) {
      Object.keys(rooms[roomId].senders).forEach((sid) => io.to(sid).emit('room-deleted'));
      delete rooms[roomId];
    }
  });

  socket.on('disconnect', () => {
    const roomId = socket.password;
    const role = socket.role;
    if (!roomId || !rooms[roomId]) return;
    const room = rooms[roomId];

    if (role === 'sender') {
      delete room.senders[socket.id];
      if (room.receiver) {
        io.to(room.receiver).emit('remove-sender', socket.id);
        io.to(room.receiver).emit('sender-list', Object.values(room.senders));
      }
    } else if (role === 'receiver') {
      Object.keys(room.senders).forEach((sid) => io.to(sid).emit('room-deleted'));
      delete rooms[roomId];
    }
  });
});

const PORT = process.env.PORT || 3001;
server.listen(PORT, () => console.log(`Signaling server listening on port ${PORT}`));
