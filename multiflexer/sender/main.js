const socket = io('http://localhost:3001');

let localStream = null;
let peerConnections = {}; // receiver별 연결 관리

document.getElementById('join').onclick = async () => {
    const password = document.getElementById('password').value.trim();
    const name = document.getElementById('name').value.trim();
    if (!password) return alert("비밀번호 입력!");

    socket.emit('join-room', { role: 'sender', password, senderName: name });

    try {
        localStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
        document.getElementById('preview').srcObject = localStream;
        console.log('화면 공유 시작됨');
    } catch (e) {
        alert('화면 공유 권한이 거부되었습니다.');
    }
};

socket.on('signal', async (data) => {
    if (data.type === 'offer') {
        const receiverId = data.from;
        if (!localStream) {
            alert('화면 공유가 아직 시작되지 않았습니다.');
            return;
        }

        // 각 receiver별로 peerConnection 관리!
        const pc = new RTCPeerConnection();
        peerConnections[receiverId] = pc;

        localStream.getTracks().forEach(track => pc.addTrack(track, localStream));

        pc.onicecandidate = (e) => {
            if (e.candidate) {
                socket.emit('signal', {
                    to: receiverId,
                    from: socket.id,
                    type: 'candidate',
                    payload: e.candidate
                });
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

    } else if (data.type === 'candidate') {
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
