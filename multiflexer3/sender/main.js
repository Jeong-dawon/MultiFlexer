// sender
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

        // 🚨🚨 🔽 H.264 코덱 강제 적용 추가
        if (RTCRtpSender.getCapabilities) {
            const { codecs } = RTCRtpSender.getCapabilities('video');
            const h264Codec = codecs.find(c => c.mimeType.toLowerCase() === 'video/h264');
            if (h264Codec) {
                const transceiver = pc.getTransceivers().find(t => t.sender && t.sender.track && t.sender.track.kind === 'video');
                if (transceiver && transceiver.setCodecPreferences) {
                    const newCodecs = [h264Codec, ...codecs.filter(c => c.mimeType.toLowerCase() !== 'video/h264')];
                    transceiver.setCodecPreferences(newCodecs);
                    console.log('Sender H.264 codec forced:', h264Codec);
                }
            }
        }
        // 🚨🚨

        // 🟢 품질 제한 코드 추가

        // ✅ 여기에서 sender 목록 출력
        pc.getSenders().forEach(s => {
            if (s.track) {
                console.log('🎥 Sender:', s.track.kind, s.track.label);
            }
        });

        // 트랙 추가 직후 바로 setParameters() 하지 말고 약간 지연
        setTimeout(() => {
            const sender = pc.getSenders().find(s => s.track && s.track.kind === 'video');
            if (sender) {
                const parameters = sender.getParameters();
                if (!parameters.encodings) parameters.encodings = [{}];
                parameters.encodings[0].maxBitrate = 5_000_000; // 고정 Mbps 설정(2.5Mbps)
                // parameters.encodings[0].maxFramerate = 30; // 고정 fps 설정
                sender.setParameters(parameters).then(() => {
                    console.log('✅ 송신 품질 제한 적용 (5.0 Mbps)');
                }).catch(e => {
                    console.warn('❌ 송신 품질 제한 적용 실패:', e);
                });
            } else {
                console.warn('❗ video sender를 찾지 못했습니다.');
            }
        }, 0); 
        // 🟢 품질 제한 코드 종료


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
        await pc.setLocalDescription(answer);

        socket.emit('signal', {
            to: receiverId,
            from: socket.id,
            type: 'answer',
            payload: answer
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