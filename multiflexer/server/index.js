const express = require('express');
const http = require('http');
const socketIO = require('socket.io');

const app = express();
const server = http.createServer(app);
const io = socketIO(server, { cors: { origin: "*" } });

const rooms = {};

io.on('connection', (socket) => {
    console.log('New client:', socket.id);

    socket.on('join-room', ({ role, password, senderName }) => {
        socket.join(password);

        if (role === 'receiver') {
            rooms[password] = rooms[password] || { senders: {}, receiver: null };
            rooms[password].receiver = socket.id;
            // 방에 이미 접속한 sender 목록 전달
            socket.emit('sender-list', Object.values(rooms[password].senders));
        } else if (role === 'sender') {
            rooms[password] = rooms[password] || { senders: {}, receiver: null };
            rooms[password].senders[socket.id] = { id: socket.id, name: senderName || `Sender-${socket.id.slice(0, 5)}` };
            // receiver에게 새 sender 알림
            if (rooms[password].receiver) {
                io.to(rooms[password].receiver).emit('new-sender', rooms[password].senders[socket.id]);
            }
        }
        socket.password = password;
        socket.role = role;
    });

    // WebRTC 시그널 메시지 중계
    socket.on('signal', (data) => {
        io.to(data.to).emit('signal', data);
    });

    socket.on('disconnect', () => {
        const password = socket.password;
        if (!password || !rooms[password]) return;
        if (socket.role === 'sender') {
            delete rooms[password].senders[socket.id];
            if (rooms[password].receiver) {
                io.to(rooms[password].receiver).emit('remove-sender', socket.id);
            }
        } else if (socket.role === 'receiver') {
            rooms[password].receiver = null;
        }
    });
});

const PORT = 3001;
server.listen(PORT, () => {
    console.log(`Signaling server listening on port ${PORT}`);
});
