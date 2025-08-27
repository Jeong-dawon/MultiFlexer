# WebRTC Multi-Sender Receiver Demo

## 구조
- server/: 시그널링 서버 (Node.js + Socket.IO)
- sender/: sender 화면 공유 클라이언트 (HTML/JS)
- receiver/: receiver 화면 분할 & 제어 클라이언트 (HTML/JS)

## 사용법
1. server/index.js 실행 (`npm install express socket.io` → `node server/index.js`)
2. sender/index.html, receiver/index.html 각각 브라우저에서 실행
3. 동일한 비밀번호로 방 입장
4. receiver에서 sender 리스트 보고 원하는 sender 화면 켜기/끄기

## 주요 기능
- 여러 sender가 한 방에 들어와 receiver에게 화면 공유
- receiver는 각 sender의 화면을 개별적으로 켜고 끔
- sender 구분 가능 (이름/ID)
- (추후 UI에서 화면 배치/drag&drop 등 확장 가능)
