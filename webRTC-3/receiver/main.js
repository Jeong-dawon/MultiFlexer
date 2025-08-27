// peer 관리용
const state = {
  screens: [],
  // screens: [{roomId: '', joinedRoom: null, peerConnection, socket, videoEl}]
};

const grid = document.getElementById("grid");
const screenCountSelect = document.getElementById("screenCount");

function renderGrid() {
  const count = parseInt(screenCountSelect.value, 10);
  state.screens = [];
  grid.innerHTML = "";

  // 그리드 레이아웃 설정
  if (count === 1) {
    grid.style.gridTemplateColumns = "1fr";
    grid.style.gridTemplateRows = "1fr";
  } else {
    grid.style.gridTemplateColumns = "repeat(2, 1fr)";
    grid.style.gridAutoRows = "1fr";
  }

  for (let idx = 0; idx < count; idx++) {
    const screen = {
      roomId: "",
      joinedRoom: null,
      peerConnection: null,
      socket: null,
      videoEl: null,
    };
    state.screens.push(screen);

    // 셀 생성
    const cell = document.createElement("div");
    cell.className = "grid-cell";
    cell.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;">
        <span class="screen-label">화면 ${idx + 1}</span>
        <input style="flex:1;" type="text" placeholder="방 ID" id="roomInput_${idx}" />
        <button id="btn_${idx}">연결</button>
      </div>
      <video id="video_${idx}" autoplay playsinline muted style="margin-top:8px;width:100%;height:100%;"></video>
    `;
    grid.appendChild(cell);

    // 연결/해제 이벤트
    const input = cell.querySelector(`#roomInput_${idx}`);
    const btn = cell.querySelector(`#btn_${idx}`);
    const video = cell.querySelector(`#video_${idx}`);
    screen.videoEl = video;

    btn.onclick = async () => {
      if (!screen.joinedRoom) {
        screen.roomId = input.value.trim();
        if (!screen.roomId) {
          alert("방 ID를 입력하세요!");
          return;
        }
        btn.disabled = true;
        btn.textContent = "연결중...";
        try {
          await joinRoom(idx);
          btn.textContent = "해제";
          btn.style.color = "#c22";
          input.disabled = true;
        } catch (e) {
          alert("연결 실패: " + (e.message || e));
          btn.disabled = false;
          btn.textContent = "연결";
        }
      } else {
        stopReceiving(idx);
        btn.textContent = "연결";
        btn.style.color = "";
        input.disabled = false;
        input.value = "";
        screen.roomId = "";
      }
    };
  }
}

// 화면 분할 수가 바뀔 때마다 그리드 다시 그림
screenCountSelect.onchange = renderGrid;

// 최초 한 번 렌더
renderGrid();

// 연결 함수
async function joinRoom(idx) {
  const screen = state.screens[idx];
  const roomId = screen.roomId;
  // WebSocket
  const socket = new WebSocket("ws://localhost:8080");
  screen.socket = socket;

  // RTC peer 생성
  const pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }]
  });
  screen.peerConnection = pc;

  // 수신 트랙
  pc.ontrack = event => {
    screen.videoEl.srcObject = event.streams[0];
  };

  // ICE candidate 전송
  pc.onicecandidate = event => {
    if (event.candidate) {
      socket.send(JSON.stringify({
        type: "signal",
        roomId,
        signalData: { type: "candidate", candidate: event.candidate }
      }));
    }
  };

  // WebSocket 메시지 핸들러
  socket.onmessage = async event => {
    const data = JSON.parse(event.data);
    if (data.type === "offer") {
      await pc.setRemoteDescription(data);
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      socket.send(JSON.stringify({
        type: "signal",
        roomId,
        signalData: pc.localDescription
      }));
    } else if (data.type === "candidate") {
      try {
        await pc.addIceCandidate(data.candidate);
      } catch (e) {
        console.error("ICE candidate 오류", e);
      }
    }
  };

  await new Promise((resolve, reject) => {
    socket.onopen = resolve;
    socket.onerror = reject;
  });

  // 방 참가
  socket.send(JSON.stringify({ type: "join", roomId }));

  screen.joinedRoom = roomId;
}

// 연결 해제 함수
function stopReceiving(idx) {
  const screen = state.screens[idx];
  if (screen.peerConnection) {
    screen.peerConnection.close();
    screen.peerConnection = null;
  }
  if (screen.socket) {
    screen.socket.close();
    screen.socket = null;
  }
  if (screen.videoEl) {
    screen.videoEl.srcObject = null;
  }
  screen.joinedRoom = null;
}

