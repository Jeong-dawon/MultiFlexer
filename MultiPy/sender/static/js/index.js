// ======================================
// WebRTC Sender (화면 공유 송신자) - 단일 Receiver 전용
// ======================================

const socket = io(`https://${window.location.hostname}:3001`);

let localStream = null;      // 현재 송출 중인 화면 스트림
let pc = null;               // 단일 RTCPeerConnection
let pendingOffer = null;     // 보류된 offer
let pendingCandidates = [];  // 보류 ICE 후보
const servers = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };

let senderName = '';         // 송신자 이름
let shareAnnounced = false;  // sender-share-started 전송 여부
let statsInterval = null;    // 송신 통계 타이머

// --- UI 요소 ---
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
const localPreview = document.getElementById('localPreview');

// ---------- 미리보기 초기화 ----------
function resetLocalPreview() {
  localPreview.innerHTML = '';
  const placeholder = document.createElement('div');
  placeholder.style.color = '#555';
  placeholder.style.fontSize = '14px';
  placeholder.textContent = '화면 공유를 시작하면 여기에 미리보기가 뜹니다.';
  localPreview.appendChild(placeholder);
}
resetLocalPreview();

// ---------- RTCPeerConnection ----------
function createPc() {
  if (pc) return pc;
  pc = new RTCPeerConnection(servers);

  pc.onicecandidate = (e) => {
    if (e.candidate) {
      socket.emit('signal', {
        type: 'candidate',
        payload: e.candidate,
        from: socket.id
      });
    }
  };

  pc.oniceconnectionstatechange = () =>
    console.log(`[SENDER] ICE:`, pc.iceConnectionState);
  pc.onconnectionstatechange = () =>
    console.log(`[SENDER] PC state:`, pc.connectionState);
  pc.onsignalingstatechange = () =>
    console.log(`[SENDER] signaling:`, pc.signalingState);
  pc.onicecandidateerror = (e) =>
    console.warn('[SENDER] onicecandidateerror:', e);

  return pc;
}

// ---------- ICE Candidate 보류 처리 ----------
async function flushPendingCandidates() {
  if (!pc || !pc.remoteDescription) return;
  while (pendingCandidates.length > 0) {
    const c = pendingCandidates.shift();
    try {
      await pc.addIceCandidate(new RTCIceCandidate(c));
    } catch (e) {
      console.warn('[SENDER] queued candidate add failed:', e);
    }
  }
}

// ---------- 송신 통계 ----------
let lastStats = {};
async function logSenderStats() {
  if (!pc) return;
  const stats = await pc.getStats();
  stats.forEach(report => {
    if (report.type === "outbound-rtp" && report.kind === "video") {
      const prev = lastStats[report.id];
      if (prev) {
        const bytes = report.bytesSent - prev.bytesSent;
        const time = (report.timestamp - prev.timestamp) / 1000;
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
function ensureStatsTimer() {
  if (!statsInterval) {
    statsInterval = setInterval(() => logSenderStats(), 5000);
  }
}
function clearStatsTimer() {
  if (statsInterval) {
    clearInterval(statsInterval);
    statsInterval = null;
  }
}

// ---------- 화면 캡처 & 미리보기 ----------
async function startLocalCaptureAndPreview() {
  if (localStream) return true;
  try {
    localStream = await navigator.mediaDevices.getDisplayMedia({
      video: {
        width: { max: 1920 },
        height: { max: 1080 },
        frameRate: { max: 60, ideal: 30 }
      },
      audio: false
    });

    const previewVideo = document.createElement('video');
    previewVideo.autoplay = true;
    previewVideo.playsInline = true;
    previewVideo.muted = true;
    previewVideo.srcObject = localStream;
    previewVideo.style.width = '100%';
    previewVideo.style.height = '100%';
    localPreview.innerHTML = '';
    localPreview.appendChild(previewVideo);

    shareStartBtn.style.display = 'none';
    shareStopBtn.style.display = 'inline-block';
    shareStopBtn.disabled = false;

    ensureStatsTimer();
    return true;
  } catch (e) {
    console.warn('[SENDER] getDisplayMedia 실패:', e);
    shareStartBtn.style.display = 'inline-block';
    shareStartBtn.disabled = false;
    shareStopBtn.style.display = 'none';
    resetLocalPreview();
    return false;
  }
}

// ---------- 공유 시작 알림 + Offer 처리 ----------
async function announceShareAndProcessOffer() {
  if (!localStream) return;
  if (!shareAnnounced) {
    socket.emit('sender-share-started', { senderId: socket.id, name: senderName });
    shareAnnounced = true;
  }
  if (pendingOffer) {
    try {
      const pc = createPc();

      // H264 우선
      pc.getTransceivers().forEach(t => {
        if (t.sender?.track?.kind === "video") {
          const h264 = RTCRtpSender.getCapabilities("video").codecs
            .find(c => c.mimeType.toLowerCase() === "video/h264");
          if (h264) t.setCodecPreferences([h264]);
        }
      });

      await pc.setRemoteDescription(new RTCSessionDescription(pendingOffer));

      localStream.getTracks().forEach(track => {
        const already = pc.getSenders().some(s => s.track === track);
        if (!already) pc.addTrack(track, localStream);
      });

      await flushPendingCandidates();

      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      socket.emit('signal', {
        type: 'answer',
        from: socket.id,
        payload: { type: 'answer', sdp: answer.sdp }
      });
      console.log('[SENDER] answer 전송');

      pendingOffer = null;
    } catch (e) {
      console.warn('[SENDER] 보류 offer 처리 실패:', e);
    }
  }
}

// ---------- Join Flow ----------
shareStartBtn.style.display = 'none';
shareStopBtn.style.display = 'none';

enterBtn.addEventListener('click', async () => {
  const name = document.getElementById('senderName').value.trim();
  if (!name) return alert('이름 입력!');

  enterBtn.disabled = true;
  await startLocalCaptureAndPreview();

  let handled = false;
  const cleanUp = () => {
    socket.off('joined-room', onSuccess);
    socket.off('join-complete', onSuccess);
    socket.off('join-error', onError);
    enterBtn.disabled = false;
  };

  const onSuccess = async ({ name: confirmedName }) => {
    if (handled) return; handled = true;
    senderName = confirmedName || name;
    myNameEl.innerText = senderName;

    startCard.style.display = 'none';
    mainHeader.style.display = 'flex';
    mainContainer.style.display = 'block';

    cleanUp();
    await announceShareAndProcessOffer();
  };

  const onError = (message) => {
    if (handled) return; handled = true;
    alert(message || '입장에 실패했습니다.');
    cleanUp();
  };

  socket.once('joined-room', onSuccess);
  socket.once('join-complete', onSuccess);
  socket.once('join-error', onError);

  socket.emit('join-room', { role: 'sender', name }, (ack) => {
    if (handled) return;
    if (ack?.success) onSuccess({ name: ack.name || name });
    else onError(ack?.message || '입장 실패');
  });
});

// ---------- 시그널 처리 ----------
socket.on('signal', async (data) => {
  console.log('[SENDER] signal recv:', data.type);

  if (data.type === 'offer') {
    try {
      if (!localStream) {
        const ok = await startLocalCaptureAndPreview();
        if (!ok) {
          pendingOffer = data.payload;
          return;
        }
      }
      pendingOffer = data.payload;
      const pc = createPc();

      // H264 강제
      pc.getTransceivers().forEach(t => {
        if (t.sender?.track?.kind === "video") {
          const h264 = RTCRtpSender.getCapabilities("video").codecs
            .find(c => c.mimeType.toLowerCase() === "video/h264");
          if (h264) t.setCodecPreferences([h264]);
        }
      });

      await pc.setRemoteDescription(new RTCSessionDescription(pendingOffer));

      localStream.getTracks().forEach(track => {
        const already = pc.getSenders().some(s => s.track === track);
        if (!already) pc.addTrack(track, localStream);
      });

      await flushPendingCandidates();

      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      socket.emit('signal', {
        type: 'answer',
        from: socket.id,
        payload: { type: 'answer', sdp: answer.sdp }
      });
      console.log('[SENDER] answer 전송');

      pendingOffer = null;
      await announceShareAndProcessOffer();
    } catch (e) {
      console.warn('[SENDER] offer 처리 실패:', e);
    }
  } else if (data.type === 'candidate') {
    if (!pc || !pc.remoteDescription) {
      pendingCandidates.push(data.payload);
      return;
    }
    try {
      await pc.addIceCandidate(new RTCIceCandidate(data.payload));
    } catch (e) {
      console.warn('ICE candidate 에러:', e);
    }
  }
});

// ---------- 방 삭제 처리 ----------
socket.on('room-deleted', () => {
  alert('방이 삭제되었습니다.');
  if (pc) {
    pc.close();
    pc = null;
  }
  if (localStream) {
    localStream.getTracks().forEach(t => t.stop());
    localStream = null;
  }
  senderName = '';
  shareAnnounced = false;
  clearStatsTimer();
  resetLocalPreview();
  myNameEl.textContent = '';
  roomDisplay.textContent = '';
  location.reload();
});

// ---------- 공유 시작/중지 ----------
shareStartBtn.addEventListener('click', async () => {
  const ok = await startLocalCaptureAndPreview();
  if (ok) await announceShareAndProcessOffer();
});

shareStopBtn.addEventListener('click', () => {
  clearStatsTimer();
  socket.emit('sender-share-stopped', { senderId: socket.id });
  if (localStream) {
    localStream.getTracks().forEach(t => t.stop());
    localStream = null;
  }
  if (pc) {
    pc.close();
    pc = null;
  }
  shareStopBtn.disabled = true;
  shareStopBtn.style.display = 'none';
  shareStartBtn.style.display = 'inline-block';
  shareStartBtn.disabled = false;
  shareAnnounced = false;
  resetLocalPreview();
});

// ---------- 보조 UI ----------
toggleThemeBtn?.addEventListener('click', () =>
  document.body.classList.toggle('dark-mode'));
refreshBtn?.addEventListener('click', () => location.reload());
fullscreenBtn?.addEventListener('click', () => {
  if (!document.fullscreenElement)
    document.documentElement.requestFullscreen().catch(() => { });
  else document.exitFullscreen();
});
document.getElementById('theme')?.addEventListener('click', () => {
  document.body.classList.toggle('dark-mode');
});
