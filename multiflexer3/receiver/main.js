// receiver
const socket = io('http://localhost:3001'); // 172.20.10.6

let senders = {}; // 현재 방에 있는 송신자 정보
let peerConnections = {}; // 각 송신자에 대한 RTCPeerConnection 객체
let streams = {}; // 각 송신자의 미디어 스트림

const servers = { //추가됨 stun서버 명시
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
};

// '방 참가' 버튼 클릭 이벤트 핸들러
document.getElementById('join').onclick = () => {
    const password = document.getElementById('password').value.trim();
    const room = document.getElementById('roomNum');
    const createBtn = document.getElementById('join');

    if (!password) return alert("비밀번호 입력!");
    socket.emit('join-room', { role: 'receiver', password });

    room.innerText = `방 : ${password}`;
    passwordInput.value = '';
    createBtn.style.display = 'none';
};

// '방 삭제' 버튼 클릭 이벤트 핸들러
document.getElementById('del').onclick = () => {
    socket.emit('del-room', { role: 'receiver' });
    alert('방이 삭제되었습니다.');

    // UI를 다시 초기화하거나, 페이지 리로드
    location.reload(); // 💡 간단하고 효과적
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

    const resenderName = document.getElementById('sender-item-' + senderId);
    if (resenderName) resenderName.remove();
});

// 송신자 리스트 UI를 새로 그려주는 함수
function renderSenderList(senderArr) {
    const listDiv = document.getElementById('senderList');
    listDiv.innerHTML = '';
    senderArr.forEach(sender => {
        const item = document.createElement('div');
        item.id = 'sender-item-' + sender.id;
        item.className = 'sender-container';

        const resenderName = document.createElement('div');
        resenderName.innerText = sender.name;
        resenderName.className = 'sender-name';
        item.appendChild(resenderName);

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
        // Mbps 측정 코드
        startBitrateMonitoring(senderId, pc); // 🔁 비트레이트 측정 시작

        // 🚨🚨--- H.264 디코딩 강제 ---
        if (RTCRtpReceiver.getCapabilities) {
            const { codecs } = RTCRtpReceiver.getCapabilities('video');
            const h264Codec = codecs.find(c => c.mimeType.toLowerCase() === 'video/h264');
            if (h264Codec) {
                const transceiver = pc.addTransceiver('video'); // 추가
                transceiver.setCodecPreferences([h264Codec, ...codecs.filter(c => c.mimeType.toLowerCase() !== 'video/h264')]);
                console.log('Receiver H.264 codec forced:', h264Codec);
            }
        }
        // 🚨🚨------------------------

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

// Mbps 측정 코드 추가
function startBitrateMonitoring(senderId, pc) {
    const bitrateStats = {
        prevBytes: 0,
        prevTime: 0,
        prevFrames: 0
    };

    const intervalId = setInterval(async () => {
        if (!peerConnections[senderId]) {
            clearInterval(intervalId);
            return;
        }

        const stats = await pc.getStats(null);
        let mbps = null;
        let fps = null;

        stats.forEach(report => {
            if (report.type === 'inbound-rtp' && report.kind === 'video') {
                const now = performance.now();
                const secondsElapsed = (now - (bitrateStats.prevTime || now)) / 1000;

                if (bitrateStats.prevTime) {
                    const bytesDelta = report.bytesReceived - bitrateStats.prevBytes;
                    mbps = (bytesDelta * 8) / (secondsElapsed * 1000 * 1000); // Mbps

                    const framesDelta = report.framesDecoded - bitrateStats.prevFrames;
                    fps = framesDelta / secondsElapsed;
                }

                bitrateStats.prevBytes = report.bytesReceived;
                bitrateStats.prevFrames = report.framesDecoded;
                bitrateStats.prevTime = now;
            }
        });

        // 비디오 해상도 가져오기
        const video = document.getElementById('video-' + senderId);
        const width = video?.videoWidth || 0;
        const height = video?.videoHeight || 0;

        if (mbps !== null && fps !== null) {
            const senderName = senders[senderId]?.name || senderId;
            console.log(`[${senderName}]`);
            console.log(`🌊 수신 비트레이트: ${mbps.toFixed(2)} Mbps`);
            console.log(`🎞 FPS: ${fps.toFixed(1)} fps`);
            console.log(`🖥 해상도: ${width} x ${height}`);
        }

    }, 1000);
}