import "./App.css";
import React, { useEffect, useState, useRef } from "react";

const MAX_SPLIT = 4; // 최대 화면 분할 개수

const App = () => {
  const [socket, setSocket] = useState(null);
  const [screenCount, setScreenCount] = useState(1); // 기본: 1화면
  const [screens, setScreens] = useState(
    Array(1).fill({ roomId: "", joinedRoom: "", status: "idle" })
  );
  // screens: [{roomId, joinedRoom, status: "idle"|"joined"}]

  // roomId별로 PeerConnection, videoRef 관리
  const peerConnections = useRef({});
  const remoteVideoRefs = useRef({});
  const pendingOffers = useRef({});
  const offerReceived = useRef({});
  const connectionStarted = useRef({});
  const [refresh, setRefresh] = useState(0);

  const servers = {
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  };

  useEffect(() => {
    const ws = new WebSocket("ws://localhost:8080");
    setSocket(ws);

    ws.onmessage = async (event) => {
      const data = JSON.parse(event.data);
      const { type, roomId } = data;
      if (!roomId) return;

      if (type === "offer") {
        pendingOffers.current[roomId] = data;
        offerReceived.current[roomId] = true;
        setRefresh(r => r + 1);
      }
      if (type === "candidate") {
        if (peerConnections.current[roomId]) {
          try {
            await peerConnections.current[roomId].addIceCandidate(data.candidate);
          } catch { }
        }
      }
    };

    return () => ws.close();
  }, []);

  // 화면 개수 바뀔 때 screens 상태 조정
  useEffect(() => {
    setScreens((prev) => {
      const arr = [];
      for (let i = 0; i < screenCount; i++) {
        arr.push(prev[i] || { roomId: "", joinedRoom: "", status: "idle" });
      }
      return arr;
    });
  }, [screenCount]);

  // 각 칸의 방 접속 및 화면 수신 시작
  const joinRoom = async (idx) => {
    const roomId = screens[idx].roomId.trim();
    if (!roomId || !socket) return;
    // 이미 해당 칸에 연결된 방이 있다면 연결 끊기
    if (screens[idx].joinedRoom) {
      stopReceiving(screens[idx].joinedRoom, idx);
    }

    socket.send(JSON.stringify({ type: "join", roomId, role: "receiver" }));
    remoteVideoRefs.current[roomId] = React.createRef();

    // offer 기다림
    let offer = pendingOffers.current[roomId];
    if (!offer) {
      socket.send(JSON.stringify({ type: "need-offer", roomId }));
      await new Promise((resolve) => {
        const handler = (event) => {
          const data = JSON.parse(event.data);
          if (data.type === "offer" && data.roomId === roomId) {
            pendingOffers.current[roomId] = data;
            offerReceived.current[roomId] = true;
            setRefresh((r) => r + 1);
            socket.removeEventListener("message", handler);
            resolve();
          }
        };
        socket.addEventListener("message", handler);
      });
      offer = pendingOffers.current[roomId];
    }

    // clean-up (덮어쓰기)
    stopReceiving(roomId, idx, { keepOffer: true });

    const pc = new RTCPeerConnection(servers);
    peerConnections.current[roomId] = pc;

    pc.ontrack = (event) => {
      if (remoteVideoRefs.current[roomId]?.current) {
        remoteVideoRefs.current[roomId].current.srcObject = event.streams[0];
      }
    };

    pc.onicecandidate = (event) => {
      if (event.candidate) {
        socket.send(
          JSON.stringify({
            type: "signal",
            roomId,
            signalData: {
              type: "candidate",
              candidate: event.candidate,
            },
          })
        );
      }
    };

    await pc.setRemoteDescription(offer);
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);

    socket.send(
      JSON.stringify({
        type: "signal",
        roomId,
        signalData: pc.localDescription,
      })
    );

    // 화면 상태 갱신
    setScreens((prev) => {
      const arr = [...prev];
      arr[idx] = { roomId, joinedRoom: roomId, status: "joined" };
      return arr;
    });
    connectionStarted.current[roomId] = true;
    setRefresh(r => r + 1);
  };

  // 화면 끊기
  const stopReceiving = (roomId, idx, options = {}) => {
    const pc = peerConnections.current[roomId];
    if (pc) {
      pc.close();
      delete peerConnections.current[roomId];
    }
    if (remoteVideoRefs.current[roomId]?.current) {
      remoteVideoRefs.current[roomId].current.srcObject = null;
    }
    connectionStarted.current[roomId] = false;
    setScreens((prev) => {
      const arr = [...prev];
      arr[idx] = { ...arr[idx], joinedRoom: "", status: "idle" };
      return arr;
    });
    setRefresh(r => r + 1);
  };

  // ...
  return (
    <div className="App">
      <div className="split-controls">
        <b>화면 분할:</b>
        <select
          value={screenCount}
          onChange={e => setScreenCount(Number(e.target.value))}
        >
          {[1, 2, 3, 4].map(n =>
            <option key={n} value={n}>{n}개</option>
          )}
        </select>
      </div>
      <div className="grid-split">
        <div
          className="grid-split"
          style={
            screenCount === 1
              ? { gridTemplateColumns: "1fr", gridTemplateRows: "1fr", height: "calc(100vh - 60px)" }
              : { gridTemplateColumns: "repeat(2, 1fr)", gridAutoRows: "1fr", height: "calc(100vh - 60px)" }
          }
        >
          {screens.map((screen, idx) => (
            <div className="grid-cell" key={idx}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ color: "#7af" }}>화면 {idx + 1}</span>
                <input
                  style={{ flex: 1 }}
                  type="text"
                  placeholder="방 ID"
                  value={screen.roomId}
                  onChange={e => {
                    setScreens(screens => {
                      const arr = [...screens];
                      arr[idx] = { ...arr[idx], roomId: e.target.value };
                      return arr;
                    });
                  }}
                  disabled={!!screen.joinedRoom}
                />
                {!screen.joinedRoom ? (
                  <button onClick={() => joinRoom(idx)}>
                    연결
                  </button>
                ) : (
                  <button
                    style={{ color: "#c22" }}
                    onClick={() => stopReceiving(screen.joinedRoom, idx)}
                  >해제</button>
                )}
              </div>
              <video
                ref={
                  remoteVideoRefs.current[screen.joinedRoom || screen.roomId]
                  || (remoteVideoRefs.current[screen.roomId] = React.createRef())
                }
                autoPlay
                playsInline
                muted
                style={{
                  marginTop: 8,
                  width: "100%",
                  height: "100%",
                }}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};


export default App;
