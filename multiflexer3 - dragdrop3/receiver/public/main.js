/*const socket = io('http://localhost:3001');

let senders = {}, peerConnections = {}, streams = {};
const servers = {
  iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
};

document.getElementById('join').onclick = () => {
  const password = document.getElementById('password').value.trim();
  if (!password) return alert("비밀번호 입력!");
  socket.emit('join-room', { role: 'receiver', password });
  document.getElementById('roomNum').innerText = `방 : ${password}`;
  document.getElementById('join').style.display = 'none';
};

document.getElementById('del').onclick = () => {
  socket.emit('del-room', { role: 'receiver' });
  alert('방이 삭제되었습니다.');
  location.reload();
};

socket.on('sender-list', (senderArr) => {
  senders = {};
  senderArr.forEach(sender => senders[sender.id] = sender);
  renderSenderList(senderArr);
});

socket.on('new-sender', (sender) => {
  senders[sender.id] = sender;
  renderSenderList(Object.values(senders));
});

socket.on('remove-sender', (senderId) => {
  delete senders[senderId];
  closeConnection(senderId);
  document.getElementById('sender-item-' + senderId)?.remove();
});

function renderSenderList(senderArr) {
  const listDiv = document.getElementById('senderList');
  listDiv.innerHTML = '';
  senderArr.forEach(sender => {
    const item = document.createElement('div');
    item.id = 'sender-item-' + sender.id;
    item.className = 'sender-container';

    const resenderName = document.createElement('div');
    resenderName.innerText = sender.name;
    item.appendChild(resenderName);

    const reqBtn = document.createElement('button');
    reqBtn.innerText = '화면 공유 요청';
    reqBtn.onclick = () => {
      socket.emit('share-request', { to: sender.id });
      alert(`${sender.name}에게 화면 공유를 요청했습니다.`);
    };
    item.appendChild(reqBtn);

    const toggleBtn = document.createElement('button');
    toggleBtn.innerText = peerConnections[sender.id] ? '끄기' : '켜기';
    toggleBtn.onclick = () => toggleStream(sender.id);
    item.appendChild(toggleBtn);

    listDiv.appendChild(item);
  });
}

async function toggleStream(senderId) {
  if (peerConnections[senderId]) {
    closeConnection(senderId);
  } else {
    const pc = new RTCPeerConnection(servers);
    peerConnections[senderId] = pc;

    pc.ontrack = (e) => {
      if (!streams[senderId]) {
        streams[senderId] = e.streams[0];
        showStream(senderId, e.streams[0]);
      }
    };

    pc.onicecandidate = (e) => {
      if (e.candidate) {
        socket.emit('signal', {
          to: senderId,
          from: socket.id,
          type: 'candidate',
          payload: e.candidate
        });
      }
    };

    const offer = await pc.createOffer({ offerToReceiveVideo: true });
    await pc.setLocalDescription(offer);
    socket.emit('signal', {
      to: senderId,
      from: socket.id,
      type: 'offer',
      payload: offer
    });
  }
}

function closeConnection(senderId) {
  peerConnections[senderId]?.close();
  delete peerConnections[senderId];
  delete streams[senderId];
  removeStream(senderId);
}

socket.on('signal', async (data) => {
  if (!peerConnections[data.from]) return;
  if (data.type === 'answer') {
    await peerConnections[data.from].setRemoteDescription(new RTCSessionDescription(data.payload));
  } else if (data.type === 'candidate') {
    try {
      await peerConnections[data.from].addIceCandidate(new RTCIceCandidate(data.payload));
    } catch (e) {
      console.warn('ICE candidate 오류:', e);
    }
  }
});

socket.on('sender-share-started', ({ senderId }) => {
  alert((senders[senderId]?.name || '송신자') + '의 화면 공유가 시작되었습니다!');
  toggleStream(senderId);
});

function showStream(senderId, stream) {
  const screens = document.getElementById('screens');
  if (document.getElementById('video-' + senderId)) return;

  const wrapper = document.createElement('div');
  wrapper.className = 'video-wrapper';
  wrapper.id = 'video-wrapper-' + senderId;

  const video = document.createElement('video');
  video.id = 'video-' + senderId;
  video.autoplay = true;
  video.playsInline = true;
  video.muted = false;
  video.srcObject = stream;
  video.setAttribute('draggable', true);

  video.addEventListener('dragstart', (e) => {
    e.dataTransfer.setData('text/plain', senderId);
    setTimeout(() => video.classList.add('hide'), 0);
  });

  video.addEventListener('dragend', () => {
    video.classList.remove('hide');
  });

  const label = document.createElement('div');
  label.className = 'video-label';
  label.innerText = senders[senderId]?.name || 'unknown';

  wrapper.appendChild(video);
  wrapper.appendChild(label);
  screens.appendChild(wrapper);
}

function removeStream(senderId) {
  document.getElementById('video-wrapper-' + senderId)?.remove();
}
// Snap Layout cell drop 처리
let draggingSenderId = null;

document.querySelectorAll('.cell').forEach(cell => {
  cell.addEventListener('dragover', e => {
    e.preventDefault();
    cell.classList.add('active');
  });

  cell.addEventListener('dragleave', () => {
    cell.classList.remove('active');
  });

  cell.addEventListener('drop', e => {
    e.preventDefault();
    cell.classList.remove('active');

    const layout = cell.dataset.layout;
    if (!draggingSenderId || !streams[draggingSenderId]) return;

    const video = document.createElement('video');
    video.srcObject = streams[draggingSenderId];
    video.autoplay = true;
    video.playsInline = true;
    video.dataset.position = layout;

    const layoutMap = {
      full:        { top: '0%', left: '0%', width: '100%', height: '100%' },
      left:        { top: '0%', left: '0%', width: '50%', height: '100%' },
      right:       { top: '0%', left: '50%', width: '50%', height: '100%' },
      smallleft:  { top: '0%', left: '0%', width: '33.33%', height: '100%' },
      bigright:   { top: '0%', left: '33.33%', width: '66.66%', height: '100%' },
      topleft:     { top: '0%', left: '0%', width: '50%', height: '50%' },
      topright:    { top: '0%', left: '50%', width: '50%', height: '50%' },
      bottomleft:  { top: '50%', left: '0%', width: '50%', height: '50%' },
      bottomright: { top: '50%', left: '50%', width: '50%', height: '50%' }
    };

    Object.assign(video.style, {
      position: 'absolute',
      objectFit: 'cover',
      zIndex: 1,
      ...layoutMap[layout]
    });

    const existing = document.querySelector(`video[data-position="${layout}"]`);
    if (existing) existing.remove();

    document.getElementById('mainFrame').appendChild(video);
  });

});


// 드롭 핸들러 (mainFrame)
window.handleDrop = (e) => {
  e.preventDefault();
  const senderId = e.dataTransfer.getData('text/plain');
  if (!senderId || !streams[senderId]) return;

  const mainFrame = document.getElementById('mainFrame');

  // 이전 영상 제거
  const prevVideo = mainFrame.querySelector('video');
  if (prevVideo) prevVideo.remove();

  // 새 영상 추가
  const video = document.createElement('video');
  video.id = 'main-video-' + senderId;
  video.autoplay = true;
  video.playsInline = true;
  video.srcObject = streams[senderId];

  mainFrame.appendChild(video);
};

// 기존 코드들은 그대로 유지한 상태에서 아래 부분만 mainFrame용 드롭 로직 대체

const snapOverlay = document.getElementById('snapOverlay');
const dropZones = document.querySelectorAll('.drop-zone');

document.addEventListener('dragstart', (e) => {
  const vid = e.target.closest('video');
  if (!vid) return;
  draggingSenderId = vid.id.replace('video-', '');
  snapOverlay.style.display = 'block';
});

document.addEventListener('dragend', () => {
  snapOverlay.style.display = 'none';
  dropZones.forEach(z => z.classList.remove('active'));
});*/




// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------


/*

// receiver - main.js
const socket = io('http://localhost:3001');

let senders = {}, peerConnections = {}, streams = {};
const servers = {
  iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
};

let currentRoom = '';
let draggingSenderId = null;
let currentBrightness = 100;
let currentVolume = 50;

document.addEventListener('DOMContentLoaded', () => {
  const startCard = document.getElementById('startCard');
  const mainHeader = document.getElementById('mainHeader');
  const mainContainer = document.getElementById('mainContainer');
  const roomControl = document.getElementById('roomControl');
  const roomNumEl = document.getElementById('roomNum');
  const senderListEl = document.getElementById('senderList');
  const screensEl = document.getElementById('screens');

  // 엔터룸: 카드에서 호출
  window.enterRoom = () => {
    const password = document.getElementById('startPassword').value.trim();
    if (!password) return alert('비밀번호를 입력하세요.');
    joinRoom(password);
  };

  // join/change 버튼 (툴바)
  document.getElementById('join')?.addEventListener('click', () => {
    const password = document.getElementById('password').value.trim();
    if (!password) return alert("비밀번호 입력!");

    if (!currentRoom) {
      // 최초 입장
      joinRoom(password);
    } else if (password === currentRoom) {
      alert('같은 방 비밀번호입니다.');
    } else {
      // 기존 방 폐기하고 비밀번호 변경 (방 변경)
      changeRoom(password);
    }
  });

  function joinRoom(password) {
    currentRoom = password;
    socket.emit('join-room', { role: 'receiver', password });
    roomNumEl.innerText = `ROOM: ${password}`;
    if (roomControl) roomControl.style.display = 'flex';
    // UI 전환
    startCard.classList.remove('active');
    mainHeader.style.display = 'flex';
    mainContainer.style.display = 'block';
  }

  function changeRoom(newPassword) {
    // 이전 방 삭제 (기존 송신자들이 더 이상 이 방에 연결 못 하도록)
    if (currentRoom) {
      socket.emit('del-room', { role: 'receiver' });
    }

    // 기존 연결/상태 정리 (입장한 방을 완전히 교체)
    Object.keys(peerConnections).forEach(id => closeConnection(id));
    senders = {};
    peerConnections = {};
    streams = {};
    senderListEl.innerHTML = '';
    screensEl.innerHTML = '';

    // 새 방으로 재입장
    currentRoom = newPassword;
    socket.emit('join-room', { role: 'receiver', password: newPassword });
    roomNumEl.innerText = `ROOM: ${newPassword}`;
    if (roomControl) roomControl.style.display = 'flex';

    alert('방 비밀번호가 변경되었습니다.');
  }

  // del 버튼: 방 삭제 및 초기화
  document.getElementById('del')?.addEventListener('click', () => {
    if (!currentRoom) return;
    socket.emit('del-room', { role: 'receiver' });
    alert('방이 삭제되었습니다.');

    // 정리
    Object.keys(peerConnections).forEach(id => closeConnection(id));
    senders = {};
    peerConnections = {};
    streams = {};

    senderListEl.innerHTML = '';
    screensEl.innerHTML = '';
    currentRoom = '';
    roomNumEl.innerText = 'ROOM: -';
    if (roomControl) roomControl.style.display = 'none';

    // UI 복귀
    startCard.classList.add('active');
    mainHeader.style.display = 'none';
    mainContainer.style.display = 'none';
  });

  // 새로고침
  document.getElementById('refresh')?.addEventListener('click', () => location.reload());

  // 전체화면
  document.getElementById('fullscreen')?.addEventListener('click', () => {
    const elem = document.documentElement;
    if (!document.fullscreenElement) {
      elem.requestFullscreen?.().catch(() => {});
    } else {
      document.exitFullscreen?.();
    }
  });

  // 다크모드
  document.getElementById('theme')?.addEventListener('click', () => {
    document.body.classList.toggle('dark-mode');
  });

  // 설정
  document.getElementById('settings')?.addEventListener('click', () => {
    openSettingsModal();
  });

  // 소켓 수신
  socket.on('sender-list', (senderArr) => {
    senders = {};
    senderArr.forEach(sender => senders[sender.id] = sender);
    renderSenderList(senderArr);
  });

  socket.on('new-sender', (sender) => {
    senders[sender.id] = sender;
    renderSenderList(Object.values(senders));
  });

  socket.on('remove-sender', (senderId) => {
    delete senders[senderId];
    closeConnection(senderId);
    document.getElementById('sender-item-' + senderId)?.remove();
  });

  socket.on('signal', async (data) => {
    if (!peerConnections[data.from]) return;
    if (data.type === 'answer') {
      await peerConnections[data.from].setRemoteDescription(new RTCSessionDescription(data.payload));
    } else if (data.type === 'candidate') {
      try {
        await peerConnections[data.from].addIceCandidate(new RTCIceCandidate(data.payload));
      } catch (e) {
        console.warn('ICE candidate 오류:', e);
      }
    }
  });

  socket.on('sender-share-started', ({ senderId }) => {
    alert((senders[senderId]?.name || '송신자') + '의 화면 공유가 시작되었습니다!');
    toggleStream(senderId);
  });

  // Sender 리스트 렌더링
  function renderSenderList(senderArr) {
    senderListEl.innerHTML = '';
    senderArr.forEach(sender => {
      const container = document.createElement('div');
      container.id = 'sender-item-' + sender.id;
      container.className = 'sender-container';
      container.style.cssText = 'margin-bottom:12px; padding:10px; display:flex; gap:12px; align-items:center; justify-content:space-between; background:#fff; border-radius:8px; box-shadow:0 2px 8px rgba(0,0,0,0.05);';

      const left = document.createElement('div');
      left.textContent = sender.name;
      left.style.fontWeight = '600';

      const right = document.createElement('div');
      right.style.display = 'flex';
      right.style.gap = '8px';

      const shareBtn = document.createElement('button');
      shareBtn.textContent = '화면 공유 요청';
      shareBtn.onclick = () => {
        socket.emit('share-request', { to: sender.id });
        alert(`${sender.name}에게 화면 공유를 요청했습니다.`);
      };

      const toggleBtn = document.createElement('button');
      toggleBtn.textContent = peerConnections[sender.id] ? '끄기' : '켜기';
      toggleBtn.onclick = () => {
        toggleStream(sender.id);
        setTimeout(() => {
          toggleBtn.textContent = peerConnections[sender.id] ? '끄기' : '켜기';
        }, 100);
      };

      right.appendChild(shareBtn);
      right.appendChild(toggleBtn);
      container.appendChild(left);
      container.appendChild(right);
      senderListEl.appendChild(container);
    });
  }

  // WebRTC 연결 토글
  async function toggleStream(senderId) {
    if (peerConnections[senderId]) {
      closeConnection(senderId);
      return;
    }
    const pc = new RTCPeerConnection(servers);
    peerConnections[senderId] = pc;

    pc.ontrack = (e) => {
      if (!streams[senderId]) {
        streams[senderId] = e.streams[0];
        showStream(senderId, e.streams[0]);
      }
    };

    pc.onicecandidate = (e) => {
      if (e.candidate) {
        socket.emit('signal', {
          to: senderId,
          from: socket.id,
          type: 'candidate',
          payload: e.candidate
        });
      }
    };

    const offer = await pc.createOffer({ offerToReceiveVideo: true });
    await pc.setLocalDescription(offer);
    socket.emit('signal', {
      to: senderId,
      from: socket.id,
      type: 'offer',
      payload: offer
    });
  }

  // *** ADD - 기존 closeConnection 함수 수정: mainFrame에 배치된 영상도 제거하도록 확장
  function closeConnection(senderId) {
    peerConnections[senderId]?.close();
    delete peerConnections[senderId];
    delete streams[senderId];
    removeStream(senderId);
    removeMainFrameVideos(senderId); // *** ADDED: mainFrame에 놓인 해당 송신자 영상도 제거
  } 

  // *** ADDED: mainFrame에 배치된 senderId 관련 video 요소 제거
  function removeMainFrameVideos(senderId) {
    document
      .querySelectorAll(`#mainFrame video[data-sender-id="${senderId}"]`)
      .forEach(v => v.remove());
  }
  // 


  // 스트림 보여주기
  function showStream(senderId, stream) {
    if (document.getElementById('video-' + senderId)) return;

    const wrapper = document.createElement('div');
    wrapper.className = 'video-wrapper';
    wrapper.id = 'video-wrapper-' + senderId;

    const video = document.createElement('video');
    video.id = 'video-' + senderId;
    video.autoplay = true;
    video.playsInline = true;
    video.muted = false;
    video.srcObject = stream;
    video.setAttribute('draggable', true);
    video.style.transition = 'filter .2s';

    applyBrightnessToVideo(video);
    applyVolumeToVideo(video);

    video.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('text/plain', senderId);
      setTimeout(() => video.classList.add('hide'), 0);
      draggingSenderId = senderId;
    });
    video.addEventListener('dragend', () => {
      video.classList.remove('hide');
      draggingSenderId = null;
    });

    const label = document.createElement('div');
    label.className = 'video-label';
    label.innerText = senders[senderId]?.name || 'unknown';
    label.style.cssText = 'position:absolute; bottom:4px; left:6px; padding:4px 8px; background:rgba(0,0,0,0.4); color:#fff; border-radius:4px; font-size:12px;';

    wrapper.appendChild(video);
    wrapper.appendChild(label);
    screensEl.appendChild(wrapper);
  }

  function removeStream(senderId) {
    document.getElementById('video-wrapper-' + senderId)?.remove();
  }

  // Snap layout drag & drop
  const layoutMap = {
    full:        { top: '0%', left: '0%', width: '100%', height: '100%' },
    left:        { top: '0%', left: '0%', width: '50%', height: '100%' },
    right:       { top: '0%', left: '50%', width: '50%', height: '100%' },
    "small-left": { top: '0%', left: '0%', width: '33.33%', height: '100%' },
    "big-right":  { top: '0%', left: '33.33%', width: '66.66%', height: '100%' },
    topleft:     { top: '0%', left: '0%', width: '50%', height: '50%' },
    topright:    { top: '0%', left: '50%', width: '50%', height: '50%' },
    bottomleft:  { top: '50%', left: '0%', width: '50%', height: '50%' },
    bottomright: { top: '50%', left: '50%', width: '50%', height: '50%' }
  };

  document.querySelectorAll('.cell').forEach(cell => {
    cell.addEventListener('dragover', e => {
      e.preventDefault();
      cell.classList.add('active');
    });

    cell.addEventListener('dragleave', () => {
      cell.classList.remove('active');
    });

    cell.addEventListener('drop', e => {
      e.preventDefault();
      cell.classList.remove('active');

      const layout = cell.dataset.layout;
      if (!draggingSenderId || !streams[draggingSenderId]) return;

      const video = document.createElement('video');
      video.autoplay = true;
      video.playsInline = true;
      video.srcObject = streams[draggingSenderId];
      video.dataset.position = layout;
      video.style.position = 'absolute';
      video.style.objectFit = 'cover';
      video.style.zIndex = '1';
      
      const resolved = layoutMap[layout];
      if (resolved) {
        Object.assign(video.style, resolved);
      }

      const existing = document.querySelector(`video[data-position="${layout}"]`);
      if (existing) existing.remove();

      document.getElementById('mainFrame').appendChild(video);
    });
  });

  // *** ADDED: sender가 공유를 중지했다는 신호 수신 처리
  socket.on('sender-share-stopped', ({ senderId }) => {
    console.log('[receiver] sender-share-stopped 수신:', senderId);
    // (선택) 사용자에게 알림을 줄 수도 있음:
    alert(`${senders[senderId]?.name || '송신자'}의 화면 공유가 중지되었습니다.`);

    // 스트림/썸네일/메인 프레임 영상 정리
    closeConnection(senderId);
  });
  // 


  // Settings modal (밝기/볼륨)
  function openSettingsModal() {
    if (document.getElementById('settings-modal')) return;

    const overlay = document.createElement('div');
    overlay.id = 'settings-modal';
    overlay.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,0.5); display:flex; align-items:center; justify-content:center; z-index:2000;';

    const box = document.createElement('div');
    box.style.cssText = 'background:#fff; padding:24px; border-radius:14px; min-width:320px; position:relative; box-shadow:0 12px 30px rgba(0,0,0,0.15);';

    const title = document.createElement('h2');
    title.innerText = '설정';
    title.style.marginBottom = '12px';

    const brightnessLabel = document.createElement('label');
    brightnessLabel.innerText = '밝기';
    brightnessLabel.style.display = 'block';
    brightnessLabel.style.marginTop = '8px';

    const brightnessRange = document.createElement('input');
    brightnessRange.type = 'range';
    brightnessRange.min = 50;
    brightnessRange.max = 150;
    brightnessRange.value = currentBrightness;
    brightnessRange.style.width = '100%';
    brightnessRange.oninput = (e) => {
      currentBrightness = e.target.value;
      applyBrightnessToAll();
    };

    const volumeLabel = document.createElement('label');
    volumeLabel.innerText = '볼륨';
    volumeLabel.style.display = 'block';
    volumeLabel.style.marginTop = '8px';

    const volumeRange = document.createElement('input');
    volumeRange.type = 'range';
    volumeRange.min = 0;
    volumeRange.max = 100;
    volumeRange.value = currentVolume;
    volumeRange.style.width = '100%';
    volumeRange.oninput = (e) => {
      currentVolume = e.target.value;
      applyVolumeToAll();
    };

    const closeBtn = document.createElement('button');
    closeBtn.innerText = '닫기';
    closeBtn.style.marginTop = '16px';
    closeBtn.onclick = () => overlay.remove();

    box.appendChild(title);
    box.appendChild(brightnessLabel);
    box.appendChild(brightnessRange);
    box.appendChild(volumeLabel);
    box.appendChild(volumeRange);
    box.appendChild(closeBtn);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
  }

  function applyBrightnessToVideo(video) {
    video.style.filter = `brightness(${currentBrightness}%)`;
  }

  function applyVolumeToVideo(video) {
    video.volume = currentVolume / 100;
  }

  function applyBrightnessToAll() {
    document.querySelectorAll('video').forEach(v => applyBrightnessToVideo(v));
  }

  function applyVolumeToAll() {
    document.querySelectorAll('video').forEach(v => applyVolumeToVideo(v));
  }

  // 다크모드 스타일 (이미 HTML에서 클래스에 대응)
  // 초기 상태 적용
  applyBrightnessToAll();
  applyVolumeToAll();
});
*/





// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------







// receiver - main.js
const socket = io('http://localhost:3001');

let senders = {}, peerConnections = {}, streams = {};
const servers = {
  iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
};

let currentRoom = '';
let draggingSenderId = null;
let currentBrightness = 100;
let currentVolume = 50;

document.addEventListener('DOMContentLoaded', () => {
  const startCard = document.getElementById('startCard');
  const mainHeader = document.getElementById('mainHeader');
  const mainContainer = document.getElementById('mainContainer');
  const roomControl = document.getElementById('roomControl');
  const roomNumEl = document.getElementById('roomNum');
  const senderListEl = document.getElementById('senderList');
  const screensEl = document.getElementById('screens');

  // 엔터룸: 카드에서 호출
  window.enterRoom = () => {
    const password = document.getElementById('startPassword').value.trim();
    if (!password) return alert('비밀번호를 입력하세요.');
    joinRoom(password);
  };

  // join/change 버튼 (툴바)
  document.getElementById('join')?.addEventListener('click', () => {
    const password = document.getElementById('password').value.trim();
    if (!password) return alert("비밀번호 입력!");

    if (!currentRoom) {
      joinRoom(password);
    } else if (password === currentRoom) {
      alert('같은 방 비밀번호입니다.');
    } else {
      changeRoom(password);
    }
  });

  function joinRoom(password) {
    currentRoom = password;
    socket.emit('join-room', { role: 'receiver', password });
    roomNumEl.innerText = `ROOM: ${password}`;
    if (roomControl) roomControl.style.display = 'flex';
    startCard.classList.remove('active');
    mainHeader.style.display = 'flex';
    mainContainer.style.display = 'block';
  }

  function changeRoom(newPassword) {
    if (currentRoom) {
      socket.emit('del-room', { role: 'receiver' });
    }

    Object.keys(peerConnections).forEach(id => closeConnection(id));
    senders = {};
    peerConnections = {};
    streams = {};
    senderListEl.innerHTML = '';
    screensEl.innerHTML = '';

    currentRoom = newPassword;
    socket.emit('join-room', { role: 'receiver', password: newPassword });
    roomNumEl.innerText = `ROOM: ${newPassword}`;
    if (roomControl) roomControl.style.display = 'flex';

    alert('방 비밀번호가 변경되었습니다.');
  }

  // del 버튼: 방 삭제 및 초기화
  document.getElementById('del')?.addEventListener('click', () => {
    if (!currentRoom) return;
    socket.emit('del-room', { role: 'receiver' });
    alert('방이 삭제되었습니다.');

    Object.keys(peerConnections).forEach(id => closeConnection(id));
    senders = {};
    peerConnections = {};
    streams = {};

    senderListEl.innerHTML = '';
    screensEl.innerHTML = '';
    currentRoom = '';
    roomNumEl.innerText = 'ROOM: -';
    if (roomControl) roomControl.style.display = 'none';

    startCard.classList.add('active');
    mainHeader.style.display = 'none';
    mainContainer.style.display = 'none';
  });

  // 새로고침
  document.getElementById('refresh')?.addEventListener('click', () => location.reload());

  // 전체화면
  document.getElementById('fullscreen')?.addEventListener('click', () => {
    const elem = document.documentElement;
    if (!document.fullscreenElement) {
      elem.requestFullscreen?.().catch(() => {});
    } else {
      document.exitFullscreen?.();
    }
  });

  // 다크모드
  document.getElementById('theme')?.addEventListener('click', () => {
    document.body.classList.toggle('dark-mode');
  });

  // 설정
  document.getElementById('settings')?.addEventListener('click', () => {
    openSettingsModal();
  });

  // 소켓 수신
  socket.on('sender-list', (senderArr) => {
    senders = {};
    senderArr.forEach(sender => senders[sender.id] = sender);
    renderSenderList(senderArr);
  });

  socket.on('new-sender', (sender) => {
    senders[sender.id] = sender;
    renderSenderList(Object.values(senders));
  });

  socket.on('remove-sender', (senderId) => {
    delete senders[senderId];
    closeConnection(senderId);
    document.getElementById('sender-item-' + senderId)?.remove();
  });

  socket.on('signal', async (data) => {
    if (!peerConnections[data.from]) return;
    if (data.type === 'answer') {
      await peerConnections[data.from].setRemoteDescription(new RTCSessionDescription(data.payload));
    } else if (data.type === 'candidate') {
      try {
        await peerConnections[data.from].addIceCandidate(new RTCIceCandidate(data.payload));
      } catch (e) {
        console.warn('ICE candidate 오류:', e);
      }
    }
  });

  socket.on('sender-share-started', ({ senderId }) => {
    // alert((senders[senderId]?.name || '송신자') + '의 화면 공유가 시작되었습니다!'); // 공유 중에 알림을 주는 건 적절치 않은 듯?
    toggleStream(senderId);
  });

  // *** ADDED: sender가 공유를 중지했다는 신호 수신 처리
  socket.on('sender-share-stopped', ({ senderId }) => {
    console.log('[receiver] sender-share-stopped 수신:', senderId);
    // alert(`${senders[senderId]?.name || '송신자'}의 화면 공유가 중지되었습니다.`);
    closeConnection(senderId);
  });

  // Sender 리스트 렌더링 (처음엔 요청 버튼만)
  function renderSenderList(senderArr) {
    senderListEl.innerHTML = '';

    if (!senderArr || senderArr.length === 0) {
      const empty = document.createElement('div');
      empty.textContent = '송신자가 없습니다.';
      empty.style.opacity = '0.6';
      empty.style.padding = '8px';
      senderListEl.appendChild(empty);
      return;
    }

    senderArr.forEach(sender => {
      const container = document.createElement('div');
      container.id = 'sender-item-' + sender.id;
      container.className = 'sender-container';
      container.style.cssText = 'margin-bottom:12px; padding:10px; display:flex; gap:12px; align-items:center; justify-content:space-between; background:#fff; border-radius:8px; box-shadow:0 2px 8px rgba(0,0,0,0.05);';

      const left = document.createElement('div');
      left.textContent = sender.name;
      left.style.fontWeight = '600';

      const right = document.createElement('div');
      right.style.display = 'flex';
      right.style.gap = '8px';

      const shareBtn = document.createElement('button');
      shareBtn.textContent = '화면 공유 요청';
      shareBtn.onclick = () => {
        socket.emit('share-request', { to: sender.id });
        // alert(`${sender.name}에게 화면 공유를 요청했습니다.`);
      };

      right.appendChild(shareBtn);
      container.appendChild(left);
      container.appendChild(right);
      senderListEl.appendChild(container);
    });
  }

  // WebRTC 연결 토글
  async function toggleStream(senderId) {
    if (peerConnections[senderId]) {
      closeConnection(senderId);
      return;
    }
    const pc = new RTCPeerConnection(servers);
    peerConnections[senderId] = pc;

    pc.ontrack = (e) => {
      if (!streams[senderId]) {
        streams[senderId] = e.streams[0];
        showStream(senderId, e.streams[0]);
      }
    };

    pc.onicecandidate = (e) => {
      if (e.candidate) {
        socket.emit('signal', {
          to: senderId,
          from: socket.id,
          type: 'candidate',
          payload: e.candidate
        });
      }
    };

    const offer = await pc.createOffer({ offerToReceiveVideo: true });
    await pc.setLocalDescription(offer);
    socket.emit('signal', {
      to: senderId,
      from: socket.id,
      type: 'offer',
      payload: offer
    });
  }

  // closeConnection / mainFrame 제거 강화
  function closeConnection(senderId) {
    peerConnections[senderId]?.close();
    delete peerConnections[senderId];
    const stream = streams[senderId];
    delete streams[senderId];
    removeStream(senderId);
    removeMainFrameVideos(senderId, stream);
  }

  function removeMainFrameVideos(senderId, stream) {
    // wrapper 기준 제거
    document
      .querySelectorAll(`#mainFrame .main-video-wrapper[data-sender-id="${senderId}"]`)
      .forEach(wrapper => {
        const v = wrapper.querySelector('video');
        if (v) {
          try { v.pause(); } catch {}
          v.srcObject = null;
        }
        wrapper.remove();
      });

    // 이전 구조 호환: video만 있는 경우
    document.querySelectorAll('#mainFrame video').forEach(v => {
      if (v.dataset.senderId === senderId || (stream && v.srcObject === stream)) {
        try { v.pause(); } catch {}
        v.srcObject = null;
        v.remove();
      }
    });
  }

  // 스트림 썸네일 보여주기
  function showStream(senderId, stream) {
    if (document.getElementById('video-' + senderId)) return;

    const wrapper = document.createElement('div');
    wrapper.className = 'video-wrapper';
    wrapper.id = 'video-wrapper-' + senderId;

    const video = document.createElement('video');
    video.id = 'video-' + senderId;
    video.autoplay = true;
    video.playsInline = true;
    video.muted = false;
    video.srcObject = stream;
    video.setAttribute('draggable', true);
    video.style.transition = 'filter .2s';

    applyBrightnessToVideo(video);
    applyVolumeToVideo(video);

    video.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('text/plain', senderId);
      setTimeout(() => video.classList.add('hide'), 0);
      draggingSenderId = senderId;
    });
    video.addEventListener('dragend', () => {
      video.classList.remove('hide');
      draggingSenderId = null;
    });

    const label = document.createElement('div');
    label.className = 'video-label';
    label.innerText = senders[senderId]?.name || 'unknown';
    label.style.cssText = 'position:absolute; bottom:4px; left:6px; padding:4px 8px; background:rgba(0,0,0,0.4); color:#fff; border-radius:4px; font-size:12px;';

    wrapper.appendChild(video);
    wrapper.appendChild(label);
    screensEl.appendChild(wrapper);
  }

  function removeStream(senderId) {
    document.getElementById('video-wrapper-' + senderId)?.remove();
  }

  // Snap layout drag & drop
  const layoutMap = {
    full:        { top: '0%', left: '0%', width: '100%', height: '100%' },
    left:        { top: '0%', left: '0%', width: '50%', height: '100%' },
    right:       { top: '0%', left: '50%', width: '50%', height: '100%' },
    "small-left": { top: '0%', left: '0%', width: '33.33%', height: '100%' },
    "big-right":  { top: '0%', left: '33.33%', width: '66.66%', height: '100%' },
    topleft:     { top: '0%', left: '0%', width: '50%', height: '50%' },
    topright:    { top: '0%', left: '50%', width: '50%', height: '50%' },
    bottomleft:  { top: '50%', left: '0%', width: '50%', height: '50%' },
    bottomright: { top: '50%', left: '50%', width: '50%', height: '50%' }
  };

  document.querySelectorAll('.cell').forEach(cell => {
    cell.addEventListener('dragover', e => {
      e.preventDefault();
      cell.classList.add('active');
    });

    cell.addEventListener('dragleave', () => {
      cell.classList.remove('active');
    });

    cell.addEventListener('drop', e => {
      e.preventDefault();
      cell.classList.remove('active');

      const layout = cell.dataset.layout;
      if (!draggingSenderId || !streams[draggingSenderId]) return;

      const resolved = layoutMap[layout];
      if (!resolved) return;

      // 기존 같은 layout 영상 있으면 제거 (wrapper 기준)
      const existingWrapper = document.querySelector(`.main-video-wrapper[data-position="${layout}"]`);
      if (existingWrapper) existingWrapper.remove();

      // wrapper 만들기
      const wrapper = document.createElement('div');
      wrapper.className = 'main-video-wrapper';
      wrapper.dataset.position = layout;
      wrapper.dataset.senderId = draggingSenderId;
      wrapper.style.position = 'absolute';
      wrapper.style.zIndex = '1';
      wrapper.style.pointerEvents = 'auto';
      Object.assign(wrapper.style, resolved);

      // video 요소
      const video = document.createElement('video');
      video.autoplay = true;
      video.playsInline = true;
      video.srcObject = streams[draggingSenderId];
      video.dataset.senderId = draggingSenderId;
      video.style.width = '100%';
      video.style.height = '100%';
      video.style.objectFit = 'cover';
      video.style.background = 'transparent';
      video.setAttribute('draggable', false);

      // close 버튼 (이 layout에 배치된 화면만 제거)
      const closeBtn = document.createElement('button');
      closeBtn.innerText = '끄기';
      closeBtn.style.cssText = `
        position:absolute;
        top:6px;
        right:6px;
        background:rgba(0,0,0,0.6);
        color:#fff;
        border:none;
        border-radius:6px;
        padding:4px 8px;
        cursor:pointer;
        font-size:12px;
        z-index:2;
      `;
      closeBtn.onclick = () => {
        wrapper.remove(); // 해당 mainFrame 배치만 제거
      };

      wrapper.appendChild(video);
      wrapper.appendChild(closeBtn);
      document.getElementById('mainFrame').appendChild(wrapper);
    });
  });

  // Settings modal (밝기/볼륨)
  function openSettingsModal() {
    if (document.getElementById('settings-modal')) return;

    const overlay = document.createElement('div');
    overlay.id = 'settings-modal';
    overlay.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,0.5); display:flex; align-items:center; justify-content:center; z-index:2000;';

    const box = document.createElement('div');
    box.style.cssText = 'background:#fff; padding:24px; border-radius:14px; min-width:320px; position:relative; box-shadow:0 12px 30px rgba(0,0,0,0.15);';

    const title = document.createElement('h2');
    title.innerText = '설정';
    title.style.marginBottom = '12px';

    const brightnessLabel = document.createElement('label');
    brightnessLabel.innerText = '밝기';
    brightnessLabel.style.display = 'block';
    brightnessLabel.style.marginTop = '8px';

    const brightnessRange = document.createElement('input');
    brightnessRange.type = 'range';
    brightnessRange.min = 50;
    brightnessRange.max = 150;
    brightnessRange.value = currentBrightness;
    brightnessRange.style.width = '100%';
    brightnessRange.oninput = (e) => {
      currentBrightness = e.target.value;
      applyBrightnessToAll();
    };

    const volumeLabel = document.createElement('label');
    volumeLabel.innerText = '볼륨';
    volumeLabel.style.display = 'block';
    volumeLabel.style.marginTop = '8px';

    const volumeRange = document.createElement('input');
    volumeRange.type = 'range';
    volumeRange.min = 0;
    volumeRange.max = 100;
    volumeRange.value = currentVolume;
    volumeRange.style.width = '100%';
    volumeRange.oninput = (e) => {
      currentVolume = e.target.value;
      applyVolumeToAll();
    };

    const closeBtn = document.createElement('button');
    closeBtn.innerText = '닫기';
    closeBtn.style.marginTop = '16px';
    closeBtn.onclick = () => overlay.remove();

    box.appendChild(title);
    box.appendChild(brightnessLabel);
    box.appendChild(brightnessRange);
    box.appendChild(volumeLabel);
    box.appendChild(volumeRange);
    box.appendChild(closeBtn);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
  }

  function applyBrightnessToVideo(video) {
    video.style.filter = `brightness(${currentBrightness}%)`;
  }

  function applyVolumeToVideo(video) {
    video.volume = currentVolume / 100;
  }

  function applyBrightnessToAll() {
    document.querySelectorAll('video').forEach(v => applyBrightnessToVideo(v));
  }

  function applyVolumeToAll() {
    document.querySelectorAll('video').forEach(v => applyVolumeToVideo(v));
  }

  // 다크모드 스타일 (이미 HTML에서 클래스에 대응)
  // 초기 상태 적용
  applyBrightnessToAll();
  applyVolumeToAll();
});


// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------




/*
// python

const socket = io('http://localhost:3001');

let senders = {};
let currentRoom = '';

window.addEventListener('DOMContentLoaded', () => {
  const startCard = document.getElementById('startCard');
  const mainHeader = document.getElementById('mainHeader');
  const mainContainer = document.getElementById('mainContainer');
  const roomControl = document.getElementById('roomControl');
  const roomNumEl = document.getElementById('roomNum');
  const senderListEl = document.getElementById('senderList');

  window.enterRoom = () => {
    const password = document.getElementById('startPassword').value.trim();
    if (!password) return alert('비밀번호를 입력하세요.');
    joinRoom(password);
  };

  document.getElementById('join')?.addEventListener('click', () => {
    const password = document.getElementById('password').value.trim();
    if (!password) return alert("비밀번호 입력!");

    if (!currentRoom) {
      joinRoom(password);
    } else if (password === currentRoom) {
      alert('같은 방 비밀번호입니다.');
    } else {
      changeRoom(password);
    }
  });

  function joinRoom(password) {
    currentRoom = password;
    socket.emit('join-room', { role: 'receiver', password });
    roomNumEl.innerText = `ROOM: ${password}`;
    if (roomControl) roomControl.style.display = 'flex';
    startCard.classList.remove('active');
    mainHeader.style.display = 'flex';
    mainContainer.style.display = 'block';
    socket.emit('get-sender-list');
  }

  function changeRoom(newPassword) {
    if (currentRoom) {
      socket.emit('del-room', { role: 'receiver' });
    }
    senders = {};
    senderListEl.innerHTML = '';
    currentRoom = newPassword;
    socket.emit('join-room', { role: 'receiver', password: newPassword });
    roomNumEl.innerText = `ROOM: ${newPassword}`;
    if (roomControl) roomControl.style.display = 'flex';
    alert('방 비밀번호가 변경되었습니다.');
    socket.emit('get-sender-list');
  }

  document.getElementById('del')?.addEventListener('click', () => {
    if (!currentRoom) return;
    socket.emit('del-room', { role: 'receiver' });
    alert('방이 삭제되었습니다.');
    senders = {};
    senderListEl.innerHTML = '';
    currentRoom = '';
    roomNumEl.innerText = 'ROOM: -';
    if (roomControl) roomControl.style.display = 'none';
    startCard.classList.add('active');
    mainHeader.style.display = 'none';
    mainContainer.style.display = 'none';
  });

  document.getElementById('refresh')?.addEventListener('click', () => location.reload());
  document.getElementById('fullscreen')?.addEventListener('click', () => {
    const elem = document.documentElement;
    if (!document.fullscreenElement) {
      elem.requestFullscreen?.();
    } else {
      document.exitFullscreen?.();
    }
  });
  document.getElementById('theme')?.addEventListener('click', () => {
    document.body.classList.toggle('dark-mode');
  });

  socket.on('sender-list', (senderArr) => {
    senders = {};
    senderArr.forEach(sender => senders[sender.id] = sender);
    renderSenderList(senderArr);
  });

  socket.on('new-sender', (sender) => {
    senders[sender.id] = sender;
    renderSenderList(Object.values(senders));
  });

  socket.on('remove-sender', (senderId) => {
    delete senders[senderId];
    document.getElementById('sender-item-' + senderId)?.remove();
  });

  
  socket.on('sender-share-started', ({ senderId }) => {
    fetch('/start-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ senderId })
    }).catch(console.error);
    // 수동으로 리스트 갱신 요청
    socket.emit('get-sender-list');
  });



  socket.on('sender-share-stopped', ({ senderId }) => {
    fetch('/stop-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ senderId })
    }).catch(console.error);
    // 수동으로 리스트 갱신 요청
    socket.emit('get-sender-list');
  });

  function renderSenderList(senderArr) {
    senderListEl.innerHTML = '';
    if (!senderArr.length) {
      const empty = document.createElement('div');
      empty.textContent = '송신자가 없습니다.';
      empty.style.opacity = '0.6';
      empty.style.padding = '8px';
      senderListEl.appendChild(empty);
      return;
    }

    senderArr.forEach(sender => {
      const container = document.createElement('div');
      container.id = 'sender-item-' + sender.id;
      container.className = 'sender-container';
      container.style.cssText = 'margin-bottom:12px; padding:10px; display:flex; gap:12px; align-items:center; justify-content:space-between; background:#fff; border-radius:8px; box-shadow:0 2px 8px rgba(0,0,0,0.05);';

      const left = document.createElement('div');
      left.textContent = sender.name;
      left.style.fontWeight = '600';

      const right = document.createElement('div');
      right.style.display = 'flex';
      right.style.gap = '8px';

      const shareBtn = document.createElement('button');
      shareBtn.textContent = '화면 공유 요청';
      shareBtn.onclick = () => {
        socket.emit('share-request', { to: sender.id });
      };

      right.appendChild(shareBtn);
      container.appendChild(left);
      container.appendChild(right);
      senderListEl.appendChild(container);
    });
  }
});
*/