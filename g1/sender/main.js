/**
 * sender.js - Sender(Web UI) 클라이언트
 * ======================================
 * - Socket.IO를 통해 시그널링 서버와 연결
 * - WebRTC RTCPeerConnection 생성 및 관리
 * - 화면 공유 시작/중지 제어
 * - 리시버 목록 UI 렌더링
 * - 테마 토글, 새로고침, 전체화면 등 보조 UI
 */

const socket = io('http://localhost:3001');

let localStream = null;
const peerConnections = {};               // receiverId -> RTCPeerConnection
const pendingOffers = {};                 // receiverId -> RTCSessionDescriptionInit
const pendingCandidates = {};             // receiverId -> RTCIceCandidateInit[]
const servers = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };

// ---- UI 엘리먼트 캐싱 ----
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

let currentRoom = '';
let senderName = '';

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

// ---- Join flow (그대로) ----
enterBtn.addEventListener('click', () => {
  // 입력값 검증
  const pw = document.getElementById('startPassword').value.trim();
  const name = document.getElementById('senderName').value.trim();
  if (!pw) return alert('방 비밀번호 입력!');
  if (!name) return alert('이름 입력!');

  enterBtn.disabled = true;
  let handled = false

  // 내부 핸들러 해제 & 버튼 복원
  const cleanUp = () => {
    socket.off('joined-room', onSuccess);
    socket.off('join-complete', onSuccess);
    socket.off('join-error', onError);
    enterBtn.disabled = false;
  };

  // 성공 처리
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

  // 실패 처리
  const onError = (message) => {
    if (handled) return; handled = true;
    alert(message || '입장에 실패했습니다.');
    cleanUp();
  };

  // 시그널 이벤트 등록
  socket.once('joined-room', onSuccess);
  socket.once('join-complete', onSuccess);
  socket.once('join-error', onError);

  // 서버에 join 요청
  socket.emit('join-room', { role: 'sender', password: pw, name }, (ack) => {
    if (handled) return;
    if (ack) {
      if (ack.success) onSuccess({ room: pw, name: ack.name || name });
      else onError(ack.message || '입장 실패');
    }
  });
});

// 리시버 목록 갱신
socket.on('receiver-list', renderReceiverList);

// 방 삭제 시 초기화
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
  location.reload();
});

// ---- 초기 버튼 상태 ----
shareStartBtn.style.display = 'none';
shareStopBtn.style.display = 'none';

// 리시버가 공유 요청 시 버튼 표시
socket.on('share-request', () => {
  alert('리시버가 화면 공유를 요청했습니다! "화면 공유 시작" 버튼을 눌러주세요.');
  shareStartBtn.style.display = 'inline-block';
  shareStartBtn.disabled = false;
  shareStopBtn.style.display = 'none';
});

// ================== WebRTC 헬퍼 ==================
/**
 * RTCPeerConnection 가져오기/생성
 */
function getPc(id) {
  let pc = peerConnections[id];
  if (pc) return pc;
  pc = new RTCPeerConnection(servers);
  peerConnections[id] = pc;

  // ICE candidate 전송
  pc.onicecandidate = (e) => {
    if (e.candidate) {
      console.log('[SENDER] send candidate ->', id);
      socket.emit('signal', {
        to: id, from: socket.id, type: 'candidate', payload: e.candidate
      });
    }
  };

  // 상태 모니터링
  pc.oniceconnectionstatechange = () => console.log(`[SENDER] ICE (${id}):`, pc.iceConnectionState);
  pc.onconnectionstatechange   = () => console.log(`[SENDER] PC state (${id}):`, pc.connectionState);
  pc.onsignalingstatechange    = () => console.log(`[SENDER] signaling (${id}):`, pc.signalingState);
  pc.onicecandidateerror       = (e) => console.warn('[SENDER] onicecandidateerror:', e);

  return pc;
}

/**
 * 보류된 ICE 후보 처리
 */
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

// ================== SIGNAL 처리 ==================
socket.on('signal', async (data) => {
  const from = data.from; // receiver id
  console.log('[SENDER] signal recv:', data.type, 'from', from);

  // ---- OFFER 처리 ----
  if (data.type === 'offer') {
    if (!localStream) {
      localStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    }
    // 1) 일단 오퍼 보류 저장 (자동 캡처 시도하지 않음 — 브라우저가 막음)
    pendingOffers[from] = data.payload;

    // 2) 이미 캡처 중(스트림이 있다면)이라면 즉시 처리
    if (localStream) {
      try {
        const pc = getPc(from);
        await pc.setRemoteDescription(new RTCSessionDescription(pendingOffers[from]));
        localStream.getTracks().forEach(track => {
          const already = pc.getSenders().some(s => s.track === track);
          if (!already) pc.addTrack(track, localStream);
        });
        await flushPendingCandidates(from);
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

  // ---- CANDIDATE 처리 ----
  } else if (data.type === 'candidate') {
    const pc = peerConnections[from];
    // pc/remoteDescription 준비 전이면 큐에 저장
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

// ================== 화면 공유 ==================
/**
 * 화면 공유 시작 버튼 핸들러
 */
shareStartBtn.addEventListener('click', async () => {
  try {
    if (!localStream) {
      // 화면 캡처 권한 요청
      localStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });

      // 미리보기 표시
      const previewVideo = document.createElement('video');
      previewVideo.autoplay = true;
      previewVideo.playsInline = true;
      previewVideo.muted = true;
      previewVideo.srcObject = localStream;
      previewVideo.style.width = '100%';
      previewVideo.style.height = '100%';
      localPreview.innerHTML = '';
      localPreview.appendChild(previewVideo);
    }

    // 버튼 토글
    shareStartBtn.disabled = true;
    shareStartBtn.style.display = 'none';
    shareStopBtn.style.display = 'inline-block';
    shareStopBtn.disabled = false;

    // 서버에 알림
    socket.emit('sender-share-started', { senderId: socket.id, name: senderName });

    // 보류된 offer들 처리
    for (const [rid, offer] of Object.entries(pendingOffers)) {
      try {
        const pc = getPc(rid);
        await pc.setRemoteDescription(new RTCSessionDescription(offer));
        
        localStream.getTracks().forEach(track => {
          const already = pc.getSenders().some(s => s.track === track);
          if (!already) pc.addTrack(track, localStream);
        });


        // 🚀 H264 코덱 강제
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

/**
 * 화면 공유 중지 및 미리보기 리셋
 */
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

// ================== 보조 UI ==================
toggleThemeBtn?.addEventListener('click', () => document.body.classList.toggle('dark-mode'));
refreshBtn?.addEventListener('click', () => location.reload());
fullscreenBtn?.addEventListener('click', () => {
  if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(()=>{});
  else document.exitFullscreen();
});
document.getElementById('theme')?.addEventListener('click', () => {
  document.body.classList.toggle('dark-mode');
});
