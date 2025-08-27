const socket = io('http://localhost:3001'); // 서버 주소

let senders = {};
let peerConnections = {};
let streams = {};

document.getElementById('join').onclick = () => {
    const password = document.getElementById('password').value.trim();
    if (!password) return alert("비밀번호 입력!");
    socket.emit('join-room', { role: 'receiver', password });
};

// sender 리스트 받아오기
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
    renderSenderList(Object.values(senders));
});

// sender 리스트를 UI로
function renderSenderList(senderArr) {
    const listDiv = document.getElementById('sender-list');
    listDiv.innerHTML = '';
    senderArr.forEach(sender => {
        const btn = document.createElement('button');
        btn.innerText = `${sender.name} (켜기/끄기)`;
        btn.onclick = () => toggleStream(sender.id);
        listDiv.appendChild(btn);
    });
}

// sender stream 켜기/끄기
async function toggleStream(senderId) {
    if (peerConnections[senderId]) {
        // 끄기
        closeConnection(senderId);
    } else {
        // 켜기 (WebRTC 연결)
        const pc = new RTCPeerConnection();
        peerConnections[senderId] = pc;

        pc.ontrack = (e) => {
            if (!streams[senderId]) {
                streams[senderId] = e.streams[0];
                showStream(senderId, e.streams[0]);
            }
        };

        pc.onicecandidate = (e) => {
            if (e.candidate) {
                socket.emit('signal', { to: senderId, from: socket.id, type: 'candidate', payload: e.candidate });
            }
        };

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        socket.emit('signal', { to: senderId, from: socket.id, type: 'offer', payload: offer });
    }
}

function closeConnection(senderId) {
    if (peerConnections[senderId]) {
        peerConnections[senderId].close();
        delete peerConnections[senderId];
    }
    if (streams[senderId]) {
        removeStream(senderId);
        delete streams[senderId];
    }
}

// signaling
socket.on('signal', async (data) => {
    if (!peerConnections[data.from]) return;
    if (data.type === 'answer') {
        await peerConnections[data.from].setRemoteDescription(new RTCSessionDescription(data.payload));
    } else if (data.type === 'candidate') {
        try {
            await peerConnections[data.from].addIceCandidate(new RTCIceCandidate(data.payload));
        } catch (e) {
            console.warn('ICE candidate 추가 중 오류:', e);
        }
    }
});

// 화면 UI에 출력/제거
function showStream(senderId, stream) {
    const screens = document.getElementById('screens');
    let video = document.createElement('video');
    video.id = 'video-' + senderId;
    video.autoplay = true;
    video.playsInline = true;
    video.srcObject = stream;
    video.style = 'width: 800px; height: 500px border: 1px solid #333; margin:5px;';
    screens.appendChild(video);
}

function removeStream(senderId) {
    const v = document.getElementById('video-' + senderId);
    if (v) v.remove();
}
