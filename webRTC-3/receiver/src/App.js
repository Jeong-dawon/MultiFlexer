import "./App.css";
import React, { useEffect, useState, useRef } from "react";

const App = () => {
  const [socket, setSocket] = useState(null); // signaling 서버와 통신할 WebSocket
  const [roomId, setRoomId] = useState(""); // 사용자가 입력한 방 ID 저장

  const remoteVideoRef = useRef(null); // 상대방(보내는 쪽)의 영상을 표시할 <video> 태그 접근용 ref
  const peerConnection = useRef(null); // WebRTC 연결 객체 저장 (Ref 사용)
  const dataChannelRef = useRef(null); // ✅ RTT 측정을 위한 DataChannel 참조
  const pingStartTime = useRef(null);   // ✅ RTT 측정용 timestamp

  const servers = {
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }], // STUN 서버 설정 (P2P 연결 지원)
  };

  useEffect(() => {
    const ws = new WebSocket("ws://localhost:8080"); // signaling 서버에 연결 (로컬호스트 기준)
    setSocket(ws); // signaling 서버에 연결 (로컬호스트 기준)

    ws.onmessage = async (event) => { // 메시지 수신 시 실행
      const data = JSON.parse(event.data); // 문자열 메시지를 JSON 객체로 변환

      // 직접 offer / answer / candidate 구분
      if (data.type === "offer") { // offer를 받으면 sender가 연결을 시도한 것
        console.log("📩 Received offer from sender");

        peerConnection.current = new RTCPeerConnection(servers); // 피어 연결 객체 생성

        // ✅ RTT 기반 측정용 DataChannel 핸들러
        peerConnection.current.ondatachannel = (event) => {
          const channel = event.channel;
          if (channel.label === "timestampChannel") {
            console.log("📥 Received DataChannel (RTT enabled)");
            dataChannelRef.current = channel;

            channel.onopen = () => {
              console.log("📡 DataChannel is open (receiver)");

              // ✅ ping 보내기 시작
              const sendPing = () => {
                if (channel.readyState === "open") {
                  const now = Date.now();
                  pingStartTime.current = now;
                  channel.send(JSON.stringify({ type: "ping", t: now }));
                }
              };
              setInterval(sendPing, 1000); // 1초마다 ping 전송
            };

            channel.onmessage = (e) => {
              const msg = JSON.parse(e.data);
              if (msg.type === "pong" && pingStartTime.current) {
                const now = Date.now();
                const rtt = now - pingStartTime.current;
                const latency = rtt / 2;
                console.log(`⏳ RTT: ${rtt}ms → Estimated one-way latency: ${latency}ms`);
                pingStartTime.current = null;
              }
            };
          }
        };
        
        // sender가 보낸 영상 스트림 수신 처리
        peerConnection.current.ontrack = (event) => {
          console.log("🎥 Remote track received");
          remoteVideoRef.current.srcObject = event.streams[0]; // 영상 스트림을 video 태그에 연결

          // 📉 FPS 측정 시작
          const video = remoteVideoRef.current;
          let frameCount = 0;
          let lastTime = performance.now();

          const countFrame = (now, metadata) => {
            frameCount++;
            if (now - lastTime >= 1000) {
              console.log(`📉 FPS: ${frameCount}`);
              frameCount = 0;
              lastTime = now;
            }

            video.requestVideoFrameCallback(countFrame);
          };
          video.requestVideoFrameCallback(countFrame);

      };

      // ICE 후보가 준비되었을 때 실행
      peerConnection.current.onicecandidate = (event) => {
        if (event.candidate) {
          console.log("📤 Sending ICE candidate");
          ws.send(
            JSON.stringify({
              type: "signal", // signaling 메시지로 ICE 후보 전송
              roomId, // 어떤 방인지 포함
              signalData: {
                type: "candidate", // 후보 타입
                candidate: event.candidate, // ICE 후보 자체
              },
            })
          );
        }
      };

      await peerConnection.current.setRemoteDescription(data); // sender의 SDP 설정
      const answer = await peerConnection.current.createAnswer(); // 응답 SDP 생성
      await peerConnection.current.setLocalDescription(answer); // 내 SDP 설정

      // 서버로 내 SDP(answer) 전송
      ws.send(
        JSON.stringify({
          type: "signal",
          roomId,
          signalData: peerConnection.current.localDescription,
        })
      );
    }

    if (data.type === "candidate") {
      console.log("📥 Received ICE candidate");
      await peerConnection.current?.addIceCandidate(data.candidate); // 수신한 ICE 후보 등록
    }
  };

  return () => ws.close(); // 컴포넌트 언마운트 시 WebSocket 닫기
}, [roomId]); // roomId가 바뀔 때마다 WebSocket 새로 연결

const joinRoom = () => {
  if (!roomId || !socket) return; // 방 ID나 소켓이 없으면 아무 것도 하지 않음
  socket.send(JSON.stringify({ type: "join", roomId, role: "receiver" })); // 서버에 방 참가 요청 전송
  console.log(`📡 Joined room ${roomId} as receiver`); // 콘솔에 참가 로그 출력
};

return (
  <div className="App">
    <h1>Receiver</h1>
    <input
      type="text"
      placeholder="Enter Room ID"
      value={roomId}
      onChange={(e) => setRoomId(e.target.value)}
    />
    <button onClick={joinRoom}>Connect to Sender</button>
    <div>
      Remote Video
      <video className="video" ref={remoteVideoRef} autoPlay playsInline />
    </div>
  </div>
);
};

export default App; // 이 컴포넌트를 다른 곳에서 사용할 수 있도록 export


/*

webRTC를 이용해 실시간 화면 공유를 시그널링 서버와 sender, receiver로 구현.

*/