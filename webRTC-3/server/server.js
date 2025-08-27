const WebSocket = require("ws");
const wss = new WebSocket.Server({ port: 8080 });

const rooms = {};          // { roomId: Set<socket> }
const pendingOffers = {};  // { roomId: offer }

wss.on("connection", (socket) => {
  console.log("A user connected");

  socket.roomIds = new Set(); // 여러 방 지원!
  socket.role = null;

  socket.on("message", (message) => {
    let data;
    try {
      data = JSON.parse(message);
    } catch (err) {
      console.error("Invalid JSON", message);
      return;
    }

    switch (data.type) {
      case "join": {
        const { roomId, role } = data;
        if (!rooms[roomId]) rooms[roomId] = new Set();
        rooms[roomId].add(socket);
        socket.roomIds.add(roomId);
        socket.role = role;
        console.log(`${role} joined room ${roomId}`);

        // 방에 pendingOffer(=offer가 먼저 도착해 저장되어 있음)가 있다면
        if (pendingOffers[roomId]) {
          // 리시버만 offer를 받음!
          if (role === "receiver") {
            socket.send(JSON.stringify({ ...pendingOffers[roomId], roomId }));
            console.log(`Cached offer sent to receiver for room ${roomId}`);
          }
        }
        break;
      }
      case "signal": {
        const { roomId, signalData } = data;

        if (!rooms[roomId]) {
          rooms[roomId] = new Set();
        }

        let hasOther = false;
        rooms[roomId].forEach((client) => {
          if (client !== socket && client.readyState === WebSocket.OPEN) {
            client.send(JSON.stringify({ ...signalData, roomId }));
            hasOther = true;
          }
        });

        // 상대방이 없으면 offer는 pending에 저장
        if (!hasOther && signalData.type === "offer") {
          pendingOffers[roomId] = signalData;
          console.log(`Offer cached for room ${roomId}`);
        }

        // answer가 도착하면 해당 pendingOffer는 삭제 (연결 성립)
        if (signalData.type === "answer" && pendingOffers[roomId]) {
          delete pendingOffers[roomId];
          console.log(`Pending offer cleared for room ${roomId}`);
        }

        break;
      }
      default:
        console.log("Unknown message type:", data.type);
    }
  });

  socket.on("close", () => {
    socket.roomIds.forEach((roomId) => {
      if (rooms[roomId]) {
        rooms[roomId].delete(socket);
        console.log(`User disconnected from room ${roomId}`);
        if (rooms[roomId].size === 0) {
          delete rooms[roomId];
          console.log(`Room ${roomId} deleted`);
        }
      }
    });
  });
});
