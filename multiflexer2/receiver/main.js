const socket = io('http://localhost:3001');

let senders = {}; // 현재 방에 있는 송신자 정보
let peerConnections = {}; // 각 송신자에 대한 RTCPeerConnection 객체
let streams = {}; // 각 송신자의 미디어 스트림

const servers = { //추가됨 stun서버 명시
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
};

// '방 참가' 버튼 클릭 이벤트 핸들러
document.getElementById('join').onclick = () => {
    const password = document.getElementById('password').value.trim();
    if (!password) return alert("비밀번호 입력!");
    socket.emit('join-room', { role: 'receiver', password });
};

// sender 리스트 받아오기
socket.on('sender-list', (senderArr) => {
    senders = {};
    // id별로 정리
    senderArr.forEach(sender => senders[sender.id] = sender);
    renderSenderList(senderArr);
});

// 새로운 송신자가 들어오면 리스트에 추가
socket.on('new-sender', (sender) => {
    senders[sender.id] = sender;
    renderSenderList(Object.values(senders));
});

// 송신자가 나가면 리스트에서 제거
socket.on('remove-sender', (senderId) => {
    delete senders[senderId];
    closeConnection(senderId);
    renderSenderList(Object.values(senders));
});

// 송신자 리스트 UI를 새로 그려주는 함수
function renderSenderList(senderArr) {
    const listDiv = document.getElementById('senderList');
    listDiv.innerHTML = '';
    senderArr.forEach(sender => {
        const item = document.createElement('div');
        item.style.marginBottom = '10px';
        item.innerText = `${sender.name} `;

        // 화면 공유 요청 버튼
        const reqBtn = document.createElement('button');
        reqBtn.innerText = '화면 공유 요청';
        reqBtn.onclick = () => {
            socket.emit('share-request', { to: sender.id });
            alert(`${sender.name}에게 화면 공유를 요청했습니다.`);
        };
        item.appendChild(reqBtn);

        // 켜기/끄기 버튼
        const toggleBtn = document.createElement('button');
        toggleBtn.innerText = peerConnections[sender.id] ? '끄기' : '켜기';
        toggleBtn.onclick = () => toggleStream(sender.id);
        item.appendChild(toggleBtn);

        listDiv.appendChild(item);
    });
}

// 송신자 stream 켜기/끄기
async function toggleStream(senderId) {
    if (peerConnections[senderId]) {
        // 끄기
        closeConnection(senderId);
        // renderSenderList(Object.values(senders));
    } else {
        // 켜기 (WebRTC 연결)
        const pc = new RTCPeerConnection(servers);
        peerConnections[senderId] = pc;

        // 스트림(트랙) 수신시 실행되는 콜백
        pc.ontrack = (e) => {
            if (!streams[senderId]) {
                streams[senderId] = e.streams[0]; // 스트림 저장
                showStream(senderId, e.streams[0]); // UI에 표시
            }
        };

        // ICE 후보 발생시 송신자에게 전달
        pc.onicecandidate = (e) => {
            if (e.candidate) {
                socket.emit('signal', { to: senderId, from: socket.id, type: 'candidate', payload: e.candidate });
            }
        };

        // offer SDP 생성 후 로컬에 등록
        const offer = await pc.createOffer({ 
            offerToReceiveVideo: true,//추가된 코드
            offerToReceiveAudio: false
        });

        await pc.setLocalDescription(offer);

        // 송신자에게 offer SDP 전송 (WebRTC 연결 요청)
        socket.emit('signal', { to: senderId, from: socket.id, type: 'offer', payload: offer });
        // renderSenderList(Object.values(senders));
    }
}

// 연결 해제 및 스트림 제거 함수
function closeConnection(senderId) {
    if (peerConnections[senderId]) {
        peerConnections[senderId].close(); // 피어 연결 종료
        delete peerConnections[senderId];
    }
    if (streams[senderId]) {
        removeStream(senderId); // 화면에서 제거
        delete streams[senderId];
    }
}

// signaling
socket.on('signal', async (data) => {
    if (!peerConnections[data.from]) return;
    if (data.type === 'answer') {
        // 송신자가 보내온 answer SDP 등록
        await peerConnections[data.from].setRemoteDescription(new RTCSessionDescription(data.payload));
    } else if (data.type === 'candidate') {
        // ICE candidate 수신시 등록
        try {
            await peerConnections[data.from].addIceCandidate(new RTCIceCandidate(data.payload));
        } catch (e) {
            console.warn('ICE candidate 추가 중 오류:', e);
        }
    }
});

socket.on('sender-share-started', ({ senderId }) => {
    alert((senders[senderId]?.name || '송신자') + '의 화면 공유가 시작되었습니다! ');
    // 자동으로 해당 샌더의 스트림을 켜고 싶다면:
    toggleStream(senderId);
});


// 화면 UI에 출력/제거
function showStream(senderId, stream) {
    const screens = document.getElementById('screens');
    let video = document.getElementById('video-' + senderId);
    if (!video) {
        video = document.createElement('video');
        video.id = 'video-' + senderId;
        video.autoplay = true;
        video.playsInline = true;
        video.muted = false;
        video.style = 'width:800px; height:500px; border:1px solid #333; margin:5px;';
        screens.appendChild(video);
    }
    video.srcObject = stream;
}

// 화면 스트림을 UI에서 제거하는 함수
function removeStream(senderId) {
    const v = document.getElementById('video-' + senderId);
    if (v) v.remove();
}
