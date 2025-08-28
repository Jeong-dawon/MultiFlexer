// 화면 공유 버튼 별도로 눌러야 미리보기 띄우는 버전
// ======================================
// WebRTC Sender (화면 공유 송신자)
// ======================================
// - Socket.IO를 통해 signaling 서버와 연결
// - 화면 공유 시작 시 RTCPeerConnection 생성
// - receiver 측 offer를 받아 answer 생성 및 전송
// - ICE candidate 교환 처리
// ======================================

// --- 시그널링 서버 연결 ---
const socket = io('http://localhost:3001');

let localStream = null;                   // 현재 송출 중인 화면 스트림
const peerConnections = {};               // receiverId -> RTCPeerConnection 객체
const pendingOffers = {};                 // offer를 받았지만 아직 처리 못한 경우 저장
const pendingCandidates = {};             // ICE candidate 보류 큐
const servers = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] }; // STUN 서버 설정

// --- UI 요소 참조 ---
const enterBtn = document.getElementById('enterBtn');
const shareStartBtn = document.getElementById('shareStart');
const shareStopBtn = document.getElementById('shareStop');
const toggleThemeBtn = document.getElementById('toggleTheme');
const refreshBtn = document.getElementById('refreshSender');
const fullscreenBtn = document.getElementById('fullscreen');
const settingsBtn = document.getElementById('settings');

const startCard = document.getElementById('startCard');
const mainHeader = document.getElementById('mainHeader');
const mainContainer = document.getElementById('mainContainer');
const roomDisplay = document.getElementById('roomDisplay');
const myNameEl = document.getElementById('myName');
const receiverListEl = document.getElementById('receiverList');
const localPreview = document.getElementById('localPreview');

let currentRoom = '';   // 현재 참여 중인 방 이름
let senderName = '';    // 송신자 이름

// ---------- 수신자 목록 렌더링 ----------
function renderReceiverList(list) {
  receiverListEl.innerHTML = '';
  list.forEach(r => {
    const item = document.createElement('div');
    item.className = 'receiver-item';

    // 수신자 이름/ID 표시
    const nameDiv = document.createElement('div');
    nameDiv.textContent = r.name || r.id;

    const idDiv = document.createElement('div');
    idDiv.style.fontSize = '12px';
    idDiv.style.color = '#555';
    idDiv.textContent = r.id;

    item.appendChild(nameDiv);
    item.appendChild(idDiv);
    receiverListEl.appendChild(item);
  });
}

// ---------- Join Flow (입장) ----------
enterBtn.addEventListener('click', () => {
  const pw = document.getElementById('startPassword').value.trim();
  const name = document.getElementById('senderName').value.trim();
  if (!pw) return alert('방 비밀번호 입력!');
  if (!name) return alert('이름 입력!');

  enterBtn.disabled = true;

  let handled = false;

  // 이벤트 등록 해제 함수
  const cleanUp = () => {
    socket.off('joined-room', onSuccess);
    socket.off('join-complete', onSuccess);
    socket.off('join-error', onError);
    enterBtn.disabled = false;
  };

  // 입장 성공 시
  const onSuccess = ({ room, name: confirmedName }) => {
    if (handled) return; handled = true;
    currentRoom = room || pw;
    senderName = confirmedName || name;
    roomDisplay.innerText = currentRoom;
    myNameEl.innerText = senderName;
    startCard.style.display = 'none';
    mainHeader.style.display = 'flex';
    mainContainer.style.display = 'block';
    cleanUp();
  };

  // 입장 실패 시
  const onError = (message) => {
    if (handled) return; handled = true;
    alert(message || '입장에 실패했습니다.');
    cleanUp();
  };

  // 이벤트 리스너 등록
  socket.once('joined-room', onSuccess);
  socket.once('join-complete', onSuccess);
  socket.once('join-error', onError);

  // 서버에 join-room 요청
  socket.emit('join-room', { role: 'sender', password: pw, name }, (ack) => {
    if (handled) return;
    if (ack) {
      if (ack.success) onSuccess({ room: pw, name: ack.name || name });
      else onError(ack.message || '입장 실패');
    }
  });
});

// 수신자 목록 업데이트 이벤트
socket.on('receiver-list', renderReceiverList);

// 방이 삭제된 경우
socket.on('room-deleted', () => {
  alert('방이 삭제되었습니다.');
  // 모든 PeerConnection 종료
  Object.keys(peerConnections).forEach(id => {
    peerConnections[id]?.close();
    delete peerConnections[id];
  });
  // 스트림 정리
  if (localStream) {
    localStream.getTracks().forEach(t => t.stop());
    localStream = null;
  }
  location.reload();
});

// 초기 UI 상태
shareStartBtn.style.display = 'none';
shareStopBtn.style.display = 'none';

// 수신자 → 공유 요청 받음
socket.on('share-request', () => {
  alert('리시버가 화면 공유를 요청했습니다! "화면 공유 시작" 버튼을 눌러주세요.');
  shareStartBtn.style.display = 'inline-block';
  shareStartBtn.disabled = false;
  shareStopBtn.style.display = 'none';
});

// ---- RTCPeerConnection helpers ----
function getPc(id) {
  // 이미 있으면 반환
  let pc = peerConnections[id];
  if (pc) return pc;

  // 새로운 PeerConnection 생성
  pc = new RTCPeerConnection(servers);
  peerConnections[id] = pc;

  // ICE candidate 발생 시 서버로 전송
  pc.onicecandidate = (e) => {
    if (e.candidate) {
      console.log('[SENDER] send candidate ->', id);
      socket.emit('signal', {
        to: id, from: socket.id, type: 'candidate', payload: e.candidate
      });
    }
  };

  // 연결 상태 로그
  pc.oniceconnectionstatechange = () => console.log(`[SENDER] ICE (${id}):`, pc.iceConnectionState);
  pc.onconnectionstatechange   = () => console.log(`[SENDER] PC state (${id}):`, pc.connectionState);
  pc.onsignalingstatechange    = () => console.log(`[SENDER] signaling (${id}):`, pc.signalingState);
  pc.onicecandidateerror       = (e) => console.warn('[SENDER] onicecandidateerror:', e);

  return pc;
}

// 보류 중인 candidate가 있으면 모두 추가
async function flushPendingCandidates(id) {
  const pc = peerConnections[id];
  if (!pc || !pc.remoteDescription) return;
  const q = pendingCandidates[id];
  if (!q || !q.length) return;
  for (const c of q.splice(0)) {
    try { await pc.addIceCandidate(new RTCIceCandidate(c)); }
    catch (e) { console.warn('[SENDER] queued candidate add failed:', e); }
  }
}

// ---------- 시그널 처리 ----------
socket.on('signal', async (data) => {
  const from = data.from; // receiver id
  console.log('[SENDER] signal recv:', data.type, 'from', from);

  if (data.type === 'offer') { // 수신자가 offer 보냄 → answer 생성
    if (!localStream) {
      // 화면 캡처 시작
      localStream = await navigator.mediaDevices.getDisplayMedia({ 
        video: {
          width:  { max: 1920 },   // 최대 해상도 1280px (HD), 1920px(FHD)
          height: { max: 1080 },
          frameRate: { max: 60, ideal: 30 } // FPS 최대 30, 선호 24
        },
        audio: false 
      });
    }
    pendingOffers[from] = data.payload;

    if (localStream) {
      try {
        const pc = getPc(from);

        // H264 코덱 강제 설정 (answer 생성 전에!)
        pc.getTransceivers().forEach(t => {
          if (t.sender?.track?.kind === "video") {
            const h264 = RTCRtpSender.getCapabilities("video").codecs
              .find(c => c.mimeType.toLowerCase() === "video/h264");
            if (h264) {
              t.setCodecPreferences([h264]);
              console.log("[SENDER] forcing codec to H264");
            }
          }
        });

        // offer 적용
        await pc.setRemoteDescription(new RTCSessionDescription(pendingOffers[from]));
        
        // 트랙 추가
        localStream.getTracks().forEach(track => {
          const already = pc.getSenders().some(s => s.track === track);
          if (!already) pc.addTrack(track, localStream);
        });

        await flushPendingCandidates(from);

        // answer 생성 및 전송
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        socket.emit('signal', { to: from, from: socket.id, type: 'answer',
          payload: { type: 'answer', sdp: answer.sdp } });
        console.log('[SENDER] answer 전송 →', from);

        delete pendingOffers[from];
      } catch (e) {
        console.warn('[SENDER] offer 처리 실패:', e);
      }
    }

  } else if (data.type === 'candidate') {
    // 수신자로부터 ICE candidate 수신
    const pc = peerConnections[from];
    if (!pc || !pc.remoteDescription) {
      // remoteDescription 설정 전이면 보류
      (pendingCandidates[from] ||= []).push(data.payload);
      return;
    }
    try {
      await pc.addIceCandidate(new RTCIceCandidate(data.payload));
    } catch (e) {
      console.warn('ICE candidate 에러:', e);
    }
  }
});

// ---- Sender Stats ----
let lastStats = {};
async function logSenderStats(pc) {
  const stats = await pc.getStats();
  stats.forEach(report => {
    if (report.type === "outbound-rtp" && report.kind === "video") {
      const prev = lastStats[report.id];
      if (prev) {
        const bytes = report.bytesSent - prev.bytesSent;
        const time  = (report.timestamp - prev.timestamp) / 1000; // 초 단위
        const bitrate = (bytes * 8 / 1000) / time;
        console.log(`[STATS][TX] bitrate≈${bitrate.toFixed(1)} kbps, FPS=${report.framesPerSecond || 'N/A'}`);
      }
      lastStats[report.id] = report;
    }
    if (report.type === "track" && report.kind === "video") {
      console.log(`[STATS][TX] resolution=${report.frameWidth}x${report.frameHeight}, FPS=${report.framesPerSecond || 'N/A'}`);
    }
  });
}

// ---------- 화면 공유 시작 ----------
shareStartBtn.addEventListener('click', async () => {
  try {
    if (!localStream) { // 화면 캡처
      localStream = await navigator.mediaDevices.getDisplayMedia({
        video: {
          width:  { max: 1920 },
          height: { max: 1080 },
          frameRate: { max: 30, ideal: 24 }
        },
        audio: false
      });
    }

    // 화면 공유 시작 후 10초 단위로 송신 상태 출력
    if (!window._statsInterval) {   // 중복 실행 방지
      window._statsInterval = setInterval(() => {
        Object.values(peerConnections).forEach(pc => logSenderStats(pc));
      }, 5000); // 5초 간격
    }

    // 미리보기 video 엘리먼트 생성
    const previewVideo = document.createElement('video');
    previewVideo.autoplay = true;
    previewVideo.playsInline = true;
    previewVideo.muted = true;
    previewVideo.srcObject = localStream;
    previewVideo.style.width = '100%';
    previewVideo.style.height = '100%';
    localPreview.innerHTML = '';
    localPreview.appendChild(previewVideo);

    // UI 상태 전환
    shareStartBtn.disabled = true;
    shareStartBtn.style.display = 'none';
    shareStopBtn.style.display = 'inline-block';
    shareStopBtn.disabled = false;

    // 서버에 공유 시작 알림
    socket.emit('sender-share-started', { senderId: socket.id, name: senderName });

    // 보류 중인 offer 처리
    for (const [rid, offer] of Object.entries(pendingOffers)) {
      try {
        const pc = getPc(rid);

        // H264 강제
        pc.getTransceivers().forEach(t => {
          if (t.sender?.track?.kind === "video") {
            const h264 = RTCRtpSender.getCapabilities("video").codecs
              .find(c => c.mimeType.toLowerCase() === "video/h264");
            if (h264) {
              t.setCodecPreferences([h264]);
              console.log("[SENDER] forcing codec to H264");
            }
          }
        });

        await pc.setRemoteDescription(new RTCSessionDescription(offer));
        localStream.getTracks().forEach(track => {
          const already = pc.getSenders().some(s => s.track === track);
          if (!already) pc.addTrack(track, localStream);
        });

        await flushPendingCandidates(rid);
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        socket.emit('signal', { to: rid, from: socket.id, type: 'answer',
          payload: { type: 'answer', sdp: answer.sdp } });
        console.log('[SENDER] answer 전송 →', rid);

        delete pendingOffers[rid];
      } catch (e) {
        console.warn('[SENDER] 보류 offer 처리 실패:', e);
      }
    }
  } catch (e) {
    alert('화면 공유를 시작할 수 없습니다: ' + (e.message || e));
  }
});

// ---------- 화면 공유 중지 ----------
function resetLocalPreview() {
  localPreview.innerHTML = '';
  const placeholder = document.createElement('div');
  placeholder.style.color = '#555';
  placeholder.style.fontSize = '14px';
  placeholder.textContent = '화면 공유를 시작하면 여기에 미리보기가 뜹니다.';
  localPreview.appendChild(placeholder);
}
resetLocalPreview();

shareStopBtn.addEventListener('click', () => {
  // 화면 공유 중지 시 로그 측정도 멈춤
  if (window._statsInterval) {
    clearInterval(window._statsInterval);
    window._statsInterval = null;
  }

  socket.emit('sender-share-stopped', { senderId: socket.id });

  if (localStream) {
    localStream.getTracks().forEach(t => t.stop());
    localStream = null;
  }
  Object.keys(peerConnections).forEach(id => {
    peerConnections[id]?.close();
    delete peerConnections[id];
  });

  shareStopBtn.disabled = true;
  shareStopBtn.style.display = 'none';
  shareStartBtn.style.display = 'none';
  resetLocalPreview();
});

// ---------- 보조 UI ----------
toggleThemeBtn?.addEventListener('click', () => document.body.classList.toggle('dark-mode'));
refreshBtn?.addEventListener('click', () => location.reload());
fullscreenBtn?.addEventListener('click', () => {
  if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(()=>{});
  else document.exitFullscreen();
});
document.getElementById('theme')?.addEventListener('click', () => {
  document.body.classList.toggle('dark-mode');
});