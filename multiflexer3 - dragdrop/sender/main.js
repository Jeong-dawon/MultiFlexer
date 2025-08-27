/*const socket = io('http://localhost:3001');

let localStream = null;
let peerConnection = null;

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

// 리시버가 화면 공유를 요청하면 실행되는 이벤트 핸들러
socket.on('share-request', () => {
    shareBtn.style.display = 'inline-block'; // 화면 공유 버튼 활성화
    alert('리시버가 화면 공유를 요청했습니다! "화면 공유 시작" 버튼을 눌러주세요.');
});

// '화면 공유 시작' 버튼 클릭 시 실행
shareBtn.onclick = async () => {
    try {
        localStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false}); // 화면 캡처 권한 요청
        document.getElementById('preview').srcObject = localStream; // 화면 미리보기에 스트림 출력
        console.log('화면 공유 시작됨');

        shareBtn.style.display = 'none'; // 공유 버튼 다시 숨김 (중복 클릭 방지)

        socket.emit('share-started'); // 서버에 화면 공유 시작 알림

    } catch (e) {
        alert('화면 공유 권한이 거부되었습니다.');
    }
};

// 소켓으로 신호(signal)를 받았을 때 (SDP, ICE 등)
socket.on('signal', async (data) => {
    if (data.type === 'offer') {
        const receiverId = data.from; // offer를 보낸 리시버의 소켓 ID
        if (!localStream) { // 화면 공유가 시작되지 않았다면 거부
            alert('아직 화면 공유를 시작하지 않았습니다.');
            return;
        }
        // // 이전 피어 연결이 있다면 닫기 (중복 연결 방지)
        if (peerConnection) {
            peerConnection.close();
        }

        // 새로운 피어 연결 생성
        peerConnection = new RTCPeerConnection();

        // 캡처된 화면 트랙(비디오)을 피어 연결에 추가
        localStream.getTracks().forEach(track => {
            peerConnection.addTrack(track, localStream);
        });

        // ICE 후보(네트워크 정보)가 생성될 때마다 상대에게 전달
        peerConnection.onicecandidate = (e) => {
            if (e.candidate) {
                socket.emit('signal', { to: receiverId, from: socket.id, type: 'candidate', payload: e.candidate });
                console.log(`ice send success!`);
            }
        };

        // offer SDP를 원격 설명으로 등록
        await peerConnection.setRemoteDescription(new RTCSessionDescription(data.payload));
        // answer SDP 생성 및 로컬 설명으로 등록
        const answer = await peerConnection.createAnswer();
        await peerConnection.setLocalDescription(answer);

        // 리시버에게 answer 전송
        socket.emit('signal', { to: receiverId, from: socket.id, type: 'answer', payload: answer });
    } else if (data.type === 'candidate') {
        // ICE 후보를 받았을 때
        if (peerConnection) {
            try {
                await peerConnection.addIceCandidate(new RTCIceCandidate(data.payload));
            } catch (e) {
                console.warn('ICE candidate 추가 중 오류:', e);
            }
        }
    }
});
*/

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
    /*
    // UI 초기화
    const room = document.getElementById('roomNum');
    const pwInput = document.getElementById('password');
    const nameInput = document.getElementById('name');
    const joinBtn = document.getElementById('join');

    if (room) room.innerText = '';
    if (pwInput) {
        pwInput.style.display = 'inline-block';
        pwInput.value = ''; //
    }
    if (nameInput) {
        nameInput.style.display = 'inline-block';
        nameInput.value = '';
    }
    if (joinBtn) joinBtn.style.display = 'inline-block';

    // 공유 중이던 화면도 제거
    const preview = document.getElementById('preview');
    if (preview) preview.srcObject = null;*/

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