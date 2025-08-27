/*
// sender - main.js
const socket = io('http://localhost:3001');

let localStream = null;
let peerConnections = {}; // receiver별 연결 관리

const shareBtn = document.getElementById('share'); // 화면 공유 버튼
shareBtn.style.display = 'none'; // 초기 화면엔 숨김 처리

// '방 참가' 버튼 클릭 이벤트 핸들러
document.getElementById('join').onclick = () => {
    const password = document.getElementById('password').value.trim(); // 비밀번호 입력값
    const name = document.getElementById('name').value.trim(); // 이름 입력값
    if (!password) return alert("비밀번호 입력!");

    // 소켓으로 방 참가 요청 (role: sender로)
    socket.emit('join-room', { role: 'sender', password, senderName: name });
};

//이름 중복되는 경우
socket.on('join-error', (message) => {
    alert(message); // 예: "이미 사용 중인 이름입니다."
});

socket.on('join-complete', ({ password }) => {
    const room = document.getElementById('roomNum');
    const pwInput = document.getElementById('password');
    const nameInput = document.getElementById('name');
    const joinBtn = document.getElementById('join');

    room.innerText = `방 : ${password}`;
    pwInput.style.display = '';
    nameInput.style.display = '';
    joinBtn.style.display = 'none';
});


// receiver 연결 종료
socket.on('room-deleted', () => {
    alert('방이 삭제되었습니다.');

    // peerConnection 초기화
    peerConnections = {};
    localStream = null;

    // 소켓 연결 끊기
    socket.disconnect();

    // UI를 다시 초기화하거나, 페이지 리로드
    location.reload(); // 💡 간단하고 효과적
});


// 리시버가 화면 공유를 요청하면 실행되는 이벤트 핸들러
socket.on('share-request', () => {
    shareBtn.style.display = 'inline-block'; // 화면 공유 버튼 활성화
    alert('리시버가 화면 공유를 요청했습니다! "화면 공유 시작" 버튼을 눌러주세요.');
});

// '화면 공유 시작' 버튼 클릭 시 실행
shareBtn.onclick = async () => {
    try {
        localStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false }); // 화면 캡처 권한 요청
        document.getElementById('preview').srcObject = localStream; // 화면 미리보기에 스트림 출력
        console.log('화면 공유 시작됨');

        shareBtn.style.display = 'none'; // 공유 버튼 다시 숨김 (중복 클릭 방지)

        socket.emit('share-started'); // 서버에 화면 공유 시작 알림

    } catch (e) {
        alert('화면 공유 권한이 거부되었습니다.');
    }
};

socket.on('signal', async (data) => {
    console.log("📨 receiver received signal:", data.type);
    if (data.type === 'offer') { //receiver가 offer를 보냄
        const receiverId = data.from;
        if (!localStream) {
            alert('화면 공유가 아직 시작되지 않았습니다.');
            return;
        }

        // 각 receiver별로 peerConnection 관리!
        const servers = {
            iceServers: [{ urls: "stun:stun.l.google.com:19302" }]
        };

        const pc = new RTCPeerConnection(servers);
        peerConnections[receiverId] = pc;

        pc.oniceconnectionstatechange = () => {
            console.log(`🌐 ICE 상태 (${receiverId}):`, pc.iceConnectionState);
        };

        //localStream 트랙 추가 (화면 공유)
        localStream.getTracks().forEach(track => pc.addTrack(track, localStream));

        pc.onicecandidate = (e) => {
            if (e.candidate) {
                console.log("📤 sender ICE candidate 전송:", e.candidate); // ✅ 로그 추가
                socket.emit('signal', {
                    to: receiverId,
                    from: socket.id,
                    type: 'candidate',
                    payload: e.candidate
                });
            } else {
                console.log("📭 sender ICE candidate 완료 (null)");
            }
        };

        await pc.setRemoteDescription(new RTCSessionDescription(data.payload));
        const answer = await pc.createAnswer();
        let sdp = answer.sdp;

        // H.264만 남기기
        sdp = filterOnlyH264(sdp);

        function filterOnlyH264(sdp) {
            const lines = sdp.split('\r\n');
            const mLineIndex = lines.findIndex(line => line.startsWith('m=video'));

            // H.264 코덱 payload type 찾기
            const h264Payloads = lines
                .filter(line => line.startsWith('a=rtpmap') && line.includes('H264'))
                .map(line => line.match(/a=rtpmap:(\d+) H264/)[1]);

            if (mLineIndex === -1 || h264Payloads.length === 0) return sdp;

            // m=video 라인에서 H.264만 남기기
            const mLineParts = lines[mLineIndex].split(' ');
            const newMLine = [...mLineParts.slice(0, 3), ...h264Payloads];
            lines[mLineIndex] = newMLine.join(' ');

            // H.264 외의 코덱 관련 라인 제거
            const allowedPayloads = new Set(h264Payloads);
            const filteredLines = lines.filter(line => {
                if (line.startsWith('a=rtpmap:') || line.startsWith('a=fmtp:') || line.startsWith('a=rtcp-fb:')) {
                    const pt = line.match(/:(\d+)/)?.[1];
                    return allowedPayloads.has(pt);
                }
                return true;
            });

            return filteredLines.join('\r\n');
        }

        await pc.setLocalDescription({ type: 'answer', sdp });

        socket.emit('signal', {
            to: receiverId,
            from: socket.id,
            type: 'answer',
            payload: { type: 'answer', sdp }
        });
        console.log("📤 sender → answer 전송:", receiverId);

    } else if (data.type === 'candidate') { //(ICE candidate 전달)
        const receiverId = data.from;
        const pc = peerConnections[receiverId];
        if (pc) {
            try {
                await pc.addIceCandidate(new RTCIceCandidate(data.payload));
            } catch (e) {
                console.warn('ICE candidate 에러:', e);
            }
        }
    }
}); 
*/


// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------


// sender - main.js
const socket = io('http://localhost:3001');

let localStream = null;
const peerConnections = {}; // receiverId => RTCPeerConnection
const servers = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };

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

// helper: update receiver list UI
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

// 입장 / 방 생성 (변경: 성공/실패 확실히 구분)
enterBtn.addEventListener('click', () => {
    const pw = document.getElementById('startPassword').value.trim();
    const name = document.getElementById('senderName').value.trim();
    const nameInput = document.getElementById('senderName');
    const pwInput = document.getElementById('startPassword');
    if (!pw) return alert('방 비밀번호 입력!');
    if (!name) return alert('이름 입력!');

    // 중복 클릭 방지
    enterBtn.disabled = true;

    let handled = false;
    const cleanUp = () => {
        socket.off('joined-room', onSuccess);
        socket.off('join-complete', onSuccess);
        socket.off('join-error', onError);
        enterBtn.disabled = false;
    };

    const onSuccess = ({ room, name: confirmedName }) => {
        if (handled) return;
        handled = true;

        currentRoom = room || pw;
        senderName = confirmedName || name;
        roomDisplay.innerText = currentRoom;
        myNameEl.innerText = senderName;

        // UI 전환 (오직 성공한 경우)
        startCard.style.display = 'none';
        mainHeader.style.display = 'flex';
        mainContainer.style.display = 'block';

        cleanUp();
    };

    const onError = (message) => {
        if (handled) return;
        handled = true;
        alert(message || '입장에 실패했습니다.');

        // 중복 이름 또는 없는 방일 때 입력창 초기화
        if (message && (message.includes('이미 사용 중인 이름') || message.includes('없는 방'))) {
            nameInput.value = '';
            pwInput.value = '';
            nameInput.focus();
        }

        cleanUp();
    };

    // 이벤트 리스너 설정
    socket.once('joined-room', onSuccess);
    socket.once('join-complete', onSuccess); // 기존 호환
    socket.once('join-error', onError);

    // 서버로 요청: 콜백 방식도 지원하면 그쪽 처리
    socket.emit('join-room', { role: 'sender', password: pw, name }, (ack) => {
        if (handled) return;
        if (ack) {
            if (ack.success) {
                onSuccess({ room: pw, name: ack.name || name });
            } else {
                onError(ack.message || '입장 실패');
            }
        }
        // otherwise 기다리는 이벤트로 처리
    });
});

// receiver-list 업데이트
socket.on('receiver-list', (list) => {
    renderReceiverList(list);
});

// 방 삭제 알림
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

// 초기 상태: 둘 다 숨기기 (안 보여야 함)
shareStartBtn.style.display = 'none';
shareStopBtn.style.display = 'none';

// share-request 수신: 요청 왔을 때만 시작 버튼 보여주기
socket.on('share-request', () => {
  alert('리시버가 화면 공유를 요청했습니다! "화면 공유 시작" 버튼을 눌러주세요.');
  shareStartBtn.style.display = 'inline-block';
  shareStartBtn.disabled = false; // 요청마다 활성화
  // 공유 중지 버튼은 아직 숨김 유지
  shareStopBtn.style.display = 'none';
});

// 화면 공유 시작
shareStartBtn.addEventListener('click', async () => {
  try {
    if (!localStream) {
      localStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
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
    // UI 전환: 시작 버튼 비활성화/숨기고, 중지 버튼 보이기
    shareStartBtn.disabled = true;
    shareStartBtn.style.display = 'none';
    shareStopBtn.style.display = 'inline-block';
    shareStopBtn.disabled = false;

    socket.emit('sender-share-started', { senderId: socket.id, name: senderName });
    socket.emit('share-started', { name: senderName }); // 기존 호환
  } catch (e) {
    alert('화면 공유를 시작할 수 없습니다: ' + (e.message || e));
  }
});

// preview를 초기 상태로 되돌리는 함수
function resetLocalPreview() {
  // 기존 비디오 요소나 기타 자식들을 제거하고 placeholder 복원
  localPreview.innerHTML = '';
  const placeholder = document.createElement('div');
  placeholder.style.color = '#555';
  placeholder.style.fontSize = '14px';
  placeholder.textContent = '화면 공유를 시작하면 여기에 미리보기가 뜹니다.';
  localPreview.appendChild(placeholder);
}

resetLocalPreview();

// 기존 공유 중지 핸들러를 아래로 교체
shareStopBtn.addEventListener('click', () => {

    // *** ADD
    // shareStopBtn 클릭 핸들러 안에 (기존에 스트림 정리한 다음)
    socket.emit('sender-share-stopped', { senderId: socket.id });

    // 스트림이 있으면 정리
    if (localStream) {
        localStream.getTracks().forEach(t => t.stop());
        localStream = null;
    }

    // 모든 peer connection 닫기
    Object.keys(peerConnections).forEach(id => {
        peerConnections[id]?.close();
        delete peerConnections[id];
    });

    // UI: 버튼 숨기기 / 비활성화
    shareStopBtn.disabled = true;
    shareStopBtn.style.display = 'none';
    shareStartBtn.style.display = 'none';

    // 프리뷰 영역 초기화 (검은 화면 제거하고 placeholder)
    resetLocalPreview();
});




// UI 보조
toggleThemeBtn?.addEventListener('click', () => {
    document.body.classList.toggle('dark-mode');
});
refreshBtn?.addEventListener('click', () => location.reload());
fullscreenBtn?.addEventListener('click', () => {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(() => { });
    } else {
        document.exitFullscreen();
    }
});

// 다크모드
document.getElementById('theme')?.addEventListener('click', () => {
    document.body.classList.toggle('dark-mode');
});

// 시그널 처리
socket.on('signal', async (data) => {
    const from = data.from; // receiver id
    if (data.type === 'offer') {
        if (!localStream) {
            try {
                localStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
                const previewVideo = document.createElement('video');
                previewVideo.autoplay = true;
                previewVideo.playsInline = true;
                previewVideo.muted = true;
                previewVideo.srcObject = localStream;
                localPreview.innerHTML = '';
                localPreview.appendChild(previewVideo);
                shareStartBtn.disabled = true;
                shareStopBtn.disabled = false;
            } catch (e) {
                console.warn('자동 화면 공유 실패:', e);
                return;
            }
        }

        let pc = peerConnections[from];
        if (!pc) {
            pc = new RTCPeerConnection(servers);
            peerConnections[from] = pc;

            pc.onicecandidate = (e) => {
                if (e.candidate) {
                    socket.emit('signal', {
                        to: from,
                        from: socket.id,
                        type: 'candidate',
                        payload: e.candidate
                    });
                }
            };

            pc.oniceconnectionstatechange = () => {
                console.log(`ICE 상태 (${from}):`, pc.iceConnectionState);
            };
        }

        await pc.setRemoteDescription(new RTCSessionDescription(data.payload));

        if (localStream) {
            localStream.getTracks().forEach(track => {
                const already = pc.getSenders().some(s => s.track === track);
                if (!already) pc.addTrack(track, localStream);
            });
        }

        const answer = await pc.createAnswer();
        let sdp = answer.sdp;

        sdp = filterOnlyH264(sdp);

        await pc.setLocalDescription({ type: 'answer', sdp });

        socket.emit('signal', {
            to: from,
            from: socket.id,
            type: 'answer',
            payload: { type: 'answer', sdp }
        });
        console.log("answer 전송:", from);
    } else if (data.type === 'candidate') {
        const pc = peerConnections[data.from];
        if (pc) {
            try {
                await pc.addIceCandidate(new RTCIceCandidate(data.payload));
            } catch (e) {
                console.warn('ICE candidate 에러:', e);
            }
        }
    }
});

function filterOnlyH264(sdp) {
    const lines = sdp.split('\r\n');
    const mLineIndex = lines.findIndex(line => line.startsWith('m=video'));
    if (mLineIndex === -1) return sdp;

    const h264Payloads = lines
        .filter(line => line.startsWith('a=rtpmap') && line.toUpperCase().includes('H264'))
        .map(line => {
            const m = line.match(/a=rtpmap:(\d+) H264/i);
            return m ? m[1] : null;
        })
        .filter(Boolean);

    if (h264Payloads.length === 0) return sdp;

    const parts = lines[mLineIndex].split(' ');
    const newMLine = [...parts.slice(0, 3), ...h264Payloads];
    lines[mLineIndex] = newMLine.join(' ');

    const allowed = new Set(h264Payloads);
    const filtered = lines.filter(line => {
        if (
            line.startsWith('a=rtpmap:') ||
            line.startsWith('a=fmtp:') ||
            line.startsWith('a=rtcp-fb:')
        ) {
            const ptMatch = line.match(/:(\d+)/);
            const pt = ptMatch ? ptMatch[1] : null;
            return allowed.has(pt);
        }
        return true;
    });

    return filtered.join('\r\n');
}