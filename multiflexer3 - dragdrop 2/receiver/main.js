const socket = io('http://localhost:3001');

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
let draggingSenderId = null;

document.addEventListener('dragstart', (e) => {
  const vid = e.target.closest('video');
  if (!vid) return;
  draggingSenderId = vid.id.replace('video-', '');
  snapOverlay.style.display = 'block';
});

document.addEventListener('dragend', () => {
  snapOverlay.style.display = 'none';
  dropZones.forEach(z => z.classList.remove('active'));
});

dropZones.forEach(zone => {
  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZones.forEach(z => z.classList.remove('active'));
    zone.classList.add('active');
  });

  zone.addEventListener('dragleave', () => {
    zone.classList.remove('active');
  });

  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    snapOverlay.style.display = 'none';
    dropZones.forEach(z => z.classList.remove('active'));

    if (!draggingSenderId || !streams[draggingSenderId]) return;
    const position = zone.dataset.position;

    // 기존 영상 제거 (같은 위치에 있는 경우만)
    const existing = document.querySelector(`video[data-position="${position}"]`);
    if (existing) existing.remove();

    const video = document.createElement('video');
    video.autoplay = true;
    video.playsInline = true;
    video.srcObject = streams[draggingSenderId];
    video.dataset.position = position;

    const layoutMap = {
      full:        { top: '0%', left: '0%', width: '100%', height: '100%' },
      left:        { top: '0%', left: '0%', width: '50%', height: '100%' },
      right:       { top: '0%', left: '50%', width: '50%', height: '100%' },
      topleft:     { top: '0%', left: '0%', width: '50%', height: '50%' },
      topright:    { top: '0%', left: '50%', width: '50%', height: '50%' },
      bottomleft:  { top: '50%', left: '0%', width: '50%', height: '50%' },
      bottomright: { top: '50%', left: '50%', width: '50%', height: '50%' }
    };

    Object.assign(video.style, {
      position: 'absolute',
      objectFit: 'cover',
      zIndex: 1,
      ...layoutMap[position]
    });

    document.getElementById('mainFrame').appendChild(video);
  });
});