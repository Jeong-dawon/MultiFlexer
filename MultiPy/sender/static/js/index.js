// ======================================
// WebRTC Sender (화면 공유 송신자) - 자동 시작 버전
// ======================================

const socket = io('https://localhost:3001');

let localStream = null;                   // 현재 송출 중인 화면 스트림
const peerConnections = {};               // receiverId -> RTCPeerConnection
const pendingOffers = {};                 // offer 보류
const pendingCandidates = {};             // ICE candidate 보류 큐
const servers = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };


let senderName = '';    // 송신자 이름
let shareAnnounced = false; // sender-share-started 전송 여부
let statsInterval = null;   // 송신 통계 타이머

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
const receiverListEl = document.getElementById('receiverList');
const localPreview = document.getElementById('localPreview');

// ---------- 수신자 목록 렌더링 ----------

console.log('[SENDER] script start');
document.addEventListener('DOMContentLoaded', () => {
  console.log('[SENDER] dom loaded');
  const el = document.getElementById('enterBtn');
  console.log('[SENDER] enterBtn =', el);
  if (!el) return;
  el.addEventListener('click', () => console.log('[SENDER] enter clicked'));
});
window.addEventListener('error', e => console.error('[SENDER] window error', e.error || e.message));
window.addEventListener('unhandledrejection', e => console.error('[SENDER] unhandled', e.reason));


function renderReceiverList(list) {
  receiverListEl.innerHTML = '';
  list.forEach(r => {
    const item = document.createElement('div');
    item.className = 'receiver-item';

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
socket.on('receiver-list', renderReceiverList);

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

// ---------- RTCPeerConnection helpers ----------
function getPc(id) {
  if (peerConnections[id]) return peerConnections[id];

  const pc = new RTCPeerConnection(servers);
  peerConnections[id] = pc;

  pc.onicecandidate = (e) => {
    if (e.candidate) {
      socket.emit('signal', {
        to: id, from: socket.id, type: 'candidate', payload: e.candidate
      });
    }
  };

  pc.oniceconnectionstatechange = () => console.log(`[SENDER] ICE (${id}):`, pc.iceConnectionState);
  pc.onconnectionstatechange = () => console.log(`[SENDER] PC state (${id}):`, pc.connectionState);
  pc.onsignalingstatechange = () => console.log(`[SENDER] signaling (${id}):`, pc.signalingState);
  pc.onicecandidateerror = (e) => console.warn('[SENDER] onicecandidateerror:', e);
  return pc;
}

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

// ---------- 송신 통계 ----------
let lastStats = {};
async function logSenderStats(pc) {
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
    statsInterval = setInterval(() => {
      Object.values(peerConnections).forEach(pc => logSenderStats(pc));
    }, 5000);
  }
}
function clearStatsTimer() {
  if (statsInterval) {
    clearInterval(statsInterval);
    statsInterval = null;
  }
}

// ---------- 화면 캡처 & 미리보기 (사용자 제스처 직후 호출 권장) ----------
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

    // 미리보기 video 엘리먼트 구성
    const previewVideo = document.createElement('video');
    previewVideo.autoplay = true;
    previewVideo.playsInline = true;
    previewVideo.muted = true;
    previewVideo.srcObject = localStream;
    previewVideo.style.width = '100%';
    previewVideo.style.height = '100%';
    localPreview.innerHTML = '';
    localPreview.appendChild(previewVideo);

    // UI: 시작 버튼 숨김, 중지 버튼 표시
    shareStartBtn.style.display = 'none';
    shareStopBtn.style.display = 'inline-block';
    shareStopBtn.disabled = false;

    ensureStatsTimer();
    return true;
  } catch (e) {
    console.warn('[SENDER] getDisplayMedia 실패:', e);
    // 실패 시에만 시작 버튼 노출하여 재시도 허용
    shareStartBtn.style.display = 'inline-block';
    shareStartBtn.disabled = false;
    shareStopBtn.style.display = 'none';
    resetLocalPreview();
    return false;
  }
}

// ---------- 공유 시작 알림 + 보류된 offer 처리 ----------
async function announceShareAndProcessOffers() {
  if (!localStream) return;
  if (!shareAnnounced) {
    socket.emit('sender-share-started', { senderId: socket.id, name: senderName });
    shareAnnounced = true;
  }
  // 보류된 offer 처리
  for (const [rid, offer] of Object.entries(pendingOffers)) {
    try {
      const pc = getPc(rid);

      // H264 강제 (answer 생성 전에)
      pc.getTransceivers().forEach(t => {
        if (t.sender?.track?.kind === "video") {
          const h264 = RTCRtpSender.getCapabilities("video").codecs
            .find(c => c.mimeType.toLowerCase() === "video/h264");
          if (h264) t.setCodecPreferences([h264]);
        }
      });

      await pc.setRemoteDescription(new RTCSessionDescription(offer));

      // 트랙 추가 (중복 방지)
      localStream.getTracks().forEach(track => {
        const already = pc.getSenders().some(s => s.track === track);
        if (!already) pc.addTrack(track, localStream);
      });

      await flushPendingCandidates(rid);

      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      socket.emit('signal', {
        to: rid, from: socket.id, type: 'answer',
        payload: { type: 'answer', sdp: answer.sdp }
      });
      console.log('[SENDER] answer 전송 →', rid);

      delete pendingOffers[rid];
    } catch (e) {
      console.warn('[SENDER] 보류 offer 처리 실패:', e);
    }
  }
}

// ---------- Join Flow (입장) ----------
shareStartBtn.style.display = 'none';  // 시작 버튼은 기본 숨김
shareStopBtn.style.display = 'none';

enterBtn.addEventListener('click', async () => {
  const name = document.getElementById('senderName').value.trim();
  if (!name) return alert('이름 입력!');
  if (!name) return alert('이름 입력!');

  enterBtn.disabled = true;

  // ✅ 사용자 제스처가 살아있는 동안 캡처 & 미리보기 먼저 시도
  await startLocalCaptureAndPreview();

  let handled = false;
  const cleanUp = () => {
    socket.off('joined-room', onSuccess);
    socket.off('join-complete', onSuccess);
    socket.off('join-error', onError);
    enterBtn.disabled = false;
  };

  const onSuccess = async ({ room, name: confirmedName }) => {
    if (handled) return; handled = true;
    senderName = confirmedName || name;
    myNameEl.innerText = senderName;

    startCard.style.display = 'none';
    mainHeader.style.display = 'flex';
    mainContainer.style.display = 'block';

    cleanUp();

    // 방 입장 성공 후 공유 시작 알림 + 보류 offer 처리
    await announceShareAndProcessOffers();
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
    if (ack) {
      if (ack.success) onSuccess({ name: ack.name || name });
      else onError(ack.message || '입장 실패');
    }
  });
});

// ---------- 시그널 처리 ----------
socket.on('signal', async (data) => {
  const from = data.from; // receiver id
  console.log('[SENDER] signal recv:', data.type, 'from', from);

  if (data.type === 'offer') {
    // 수신자 → offer 수신: 스트림 준비 후 answer
    try {
      if (!localStream) {
        const ok = await startLocalCaptureAndPreview();
        if (!ok) {
          // 캡처 거부 시 offer 보류
          pendingOffers[from] = data.payload;
          return;
        }
      }
      pendingOffers[from] = data.payload;

      const pc = getPc(from);

      // H264 강제
      pc.getTransceivers().forEach(t => {
        if (t.sender?.track?.kind === "video") {
          const h264 = RTCRtpSender.getCapabilities("video").codecs
            .find(c => c.mimeType.toLowerCase() === "video/h264");
          if (h264) t.setCodecPreferences([h264]);
        }
      });

      await pc.setRemoteDescription(new RTCSessionDescription(pendingOffers[from]));

      // 트랙 추가
      localStream.getTracks().forEach(track => {
        const already = pc.getSenders().some(s => s.track === track);
        if (!already) pc.addTrack(track, localStream);
      });

      await flushPendingCandidates(from);

      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      socket.emit('signal', {
        to: from, from: socket.id, type: 'answer',
        payload: { type: 'answer', sdp: answer.sdp }
      });
      console.log('[SENDER] answer 전송 →', from);

      delete pendingOffers[from];

      // 방에 이미 들어와 있다면 공유 시작 알림(1회)
      await announceShareAndProcessOffers();

    } catch (e) {
      console.warn('[SENDER] offer 처리 실패:', e);
    }

  } else if (data.type === 'candidate') {
    // 수신자로부터 ICE candidate 수신
    const pc = peerConnections[from];
    if (!pc || !pc.remoteDescription) {
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

// ---------- 리시버의 공유 요청 (자동 처리) ----------
socket.on('share-request', async () => {
  // 예전처럼 alert 띄우지 않음. 자동 처리.
  const ok = await startLocalCaptureAndPreview();
  if (ok) await announceShareAndProcessOffers();
});

// ---------- 방 삭제 처리 ----------
socket.on('room-deleted', () => {
  alert('방이 삭제되었습니다.');
  Object.keys(peerConnections).forEach(id => {
    peerConnections[id]?.close();
    delete peerConnections[id];
  });
  if (localStream) {
    localStream.getTracks().forEach(t => t.stop());
    localStream = null;
  }
  clearStatsTimer();
  location.reload();
});

// ---------- 수동 “시작” 버튼 (폴백용) ----------
shareStartBtn.addEventListener('click', async () => {
  const ok = await startLocalCaptureAndPreview();
  if (ok) await announceShareAndProcessOffers();
});

// ---------- 화면 공유 중지 ----------
shareStopBtn.addEventListener('click', () => {
  clearStatsTimer();

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
  // 자동 시작 정책이지만, 사용자가 중지한 뒤 재시작할 수도 있어 폴백 버튼만 보이게 함
  shareStartBtn.style.display = 'inline-block';
  shareStartBtn.disabled = false;
  shareAnnounced = false;
  resetLocalPreview();
});

// ---------- 보조 UI ----------
toggleThemeBtn?.addEventListener('click', () => document.body.classList.toggle('dark-mode'));
refreshBtn?.addEventListener('click', () => location.reload());
fullscreenBtn?.addEventListener('click', () => {
  if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => { });
  else document.exitFullscreen();
});
document.getElementById('theme')?.addEventListener('click', () => {
  document.body.classList.toggle('dark-mode');
});

//------------
// 현재 참여중인지를 판별
function isUserJoined() {
  return senderName !== '' &&
    socket.connected &&
    startCard.style.display === 'none';
}