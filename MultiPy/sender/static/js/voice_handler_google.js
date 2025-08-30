// config.json에서 API 키를 불러오는 함수
async function loadConfig() {
    try {
        const response = await fetch('/static/config.json');
        if (!response.ok) {
            throw new Error('config.json 파일을 불러올 수 없습니다.');
        }
        return await response.json();
    } catch (error) {
        console.error('설정 파일 로드 오류:', error);
        throw error;
    }
}

// MQTT 브로커 설정
const MQTT_BROKER_URL = 'ws://localhost:9001'; // WebSocket 포트
const MQTT_TOPIC = 'admin/called';

// 감지할 참여자 이름 배열
const PARTICIPANT_NAMES = ["아린", "단비", "은비", "다원", "유진"];

// 음성 인식 주기 (밀리초 단위)
const STT_INTERVAL = 5000; // 5초

// --- 전역 변수 ---
let mediaRecorder;
let audioChunks = [];
let intervalId = null;
let mqttClient = null;
let GOOGLE_CLOUD_API_KEY = null;
let audioStream = null;
let isRecording = false;
let lastSentMessages = {}; // 중복 메시지 방지용

/**
 * MQTT 클라이언트 초기화
 */
function initializeMQTT() {
    try {
        // Paho MQTT 클라이언트 생성 (WebSocket 사용)
        mqttClient = new Paho.MQTT.Client("localhost", 9001, "clientId_" + parseInt(Math.random() * 100, 10));

        mqttClient.onConnectionLost = function (responseObject) {
            if (responseObject.errorCode !== 0) {
                console.log("MQTT 연결 끊어짐: " + responseObject.errorMessage);
            }
        };

        mqttClient.onMessageArrived = function (message) {
            console.log("수신된 메시지: " + message.payloadString);
        };

        // MQTT 브로커에 연결
        mqttClient.connect({
            onSuccess: function () {
                console.log("MQTT 브로커에 연결되었습니다.");
            },
            onFailure: function (error) {
                console.error("MQTT 연결 실패:", error);
            }
        });

    } catch (error) {
        console.error('MQTT 초기화 오류:', error);
    }
}

/**
 * 마이크 스트림을 설정하는 함수 (녹음 시작하지 않음)
 */
async function setupMicrophone() {
    try {
        // 오디오 스트림 가져오기
        audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });

        console.log('마이크 설정 완료');
        return true;

    } catch (error) {
        console.error('마이크에 접근할 수 없습니다:', error);
        throw new Error('마이크 접근 권한이 필요합니다.');
    }
}

/**
 * 녹음을 시작하고 주기적으로 STT를 실행하는 함수
 */
function startRecording() {
    if (!audioStream) {
        console.error('오디오 스트림이 없습니다. 먼저 마이크를 설정해주세요.');
        return;
    }

    try {
        mediaRecorder = new MediaRecorder(audioStream);

        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                audioChunks.push(event.data);
            }
        };

        mediaRecorder.onstop = async () => {
            // Blob 형태로 음성 데이터를 병합하여 변환
            if (audioChunks.length > 0) {
                const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                await sendAudioToGoogleSTT(audioBlob);
                audioChunks = []; // 새로운 녹음을 위해 비워줌
            }
        };

        mediaRecorder.start(); // 녹음 시작
        isRecording = true;

        // 5초마다 녹음 중지 후 변환, 그리고 다시 녹음 시작
        intervalId = setInterval(() => {
            if (mediaRecorder && mediaRecorder.state === "recording") {
                mediaRecorder.stop(); // STT 변환을 위해 중지
                mediaRecorder.start(); // 중지 후 다시 녹음 시작
            }
        }, STT_INTERVAL);

        console.log('녹음이 시작되었습니다.');
    } catch (err) {
        console.error('음성 녹음 중 오류 발생:', err);
    }
}

/**
 * 녹음된 오디오를 Google Cloud Speech-to-Text API로 전송하는 함수
 */
async function sendAudioToGoogleSTT(audioBlob) {
    const url = `https://speech.googleapis.com/v1/speech:recognize?key=${GOOGLE_CLOUD_API_KEY}`;

    console.log('Audio blob size:', audioBlob.size);
    console.log('Audio blob type:', audioBlob.type);

    try {
        const reader = new FileReader();
        reader.readAsArrayBuffer(audioBlob);

        reader.onloadend = async function () {
            const arrayBuffer = reader.result;
            const base64Audio = btoa(
                new Uint8Array(arrayBuffer).reduce(
                    (data, byte) => data + String.fromCharCode(byte),
                    ''
                )
            );

            const response = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    config: {
                        encoding: 'WEBM_OPUS',
                        sampleRateHertz: 48000,
                        languageCode: 'ko-KR',
                    },
                    audio: {
                        content: base64Audio,
                    },
                }),
            });

            const result = await response.json();
            console.log('API Response:', result);

            if (result.error) {
                console.error('API Error:', result.error);
                return;
            }

            if (result.results && result.results.length > 0) {
                const transcript = result.results[0].alternatives[0].transcript;
                console.log('STT 결과:', transcript);

                // 참여자 이름 찾기 및 MQTT 전송
                findAndPublishName(transcript);
            } else {
                console.log('인식된 텍스트가 없습니다.');
            }
        };
    } catch (e) {
        console.error('텍스트 변환 중 오류 발생:', e);
    }
}

/**
 * 텍스트에서 참여자 이름을 찾아 MQTT로 전송하는 함수
 * @param {string} text - 음성 인식으로 변환된 텍스트
 */
function findAndPublishName(text) {
    const nameRegex = new RegExp(PARTICIPANT_NAMES.join('|'), 'g');
    const foundNames = text.match(nameRegex);

    if (foundNames) {
        // 중복 제거
        const uniqueNames = [...new Set(foundNames)];

        uniqueNames.forEach(name => {
            console.log(`'${name}' 이름이 감지되었습니다.`);

            // MQTT로 전송
            publishToMQTT(name);

            // 관리자 페이지에 직접 알림 (MQTT가 없어도 작동)
            if (window.handleParticipantCalled) {
                window.handleParticipantCalled(name);
            }
        });
    }
}

/**
 * MQTT로 참여자 이름을 전송하는 함수
 */
function publishToMQTT(participantName) {
    if (mqttClient && mqttClient.isConnected()) {
        // 중복 메시지 방지 (2초 내 같은 메시지는 전송하지 않음)
        const currentTime = Date.now();
        const lastTime = lastSentMessages[participantName] || 0;

        if (currentTime - lastTime < 2000) {
            console.log(`중복 메시지 방지: ${participantName} (${currentTime - lastTime}ms 전에 전송됨)`);
            return;
        }

        const message = new Paho.MQTT.Message(participantName);
        message.destinationName = MQTT_TOPIC;

        mqttClient.send(message);
        lastSentMessages[participantName] = currentTime;
        console.log(`MQTT 메시지 전송: ${MQTT_TOPIC} -> ${participantName}`);
    } else {
        console.error('MQTT 클라이언트가 연결되지 않았습니다.');
    }
}

/**
 * 녹음 중지 함수
 */
function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop(); // 마지막으로 변환 처리
        clearInterval(intervalId); // 주기 처리 중단
        intervalId = null;
        isRecording = false;
        console.log('녹음이 중지되었습니다.');
    }
}

/**
 * 마이크 토글 함수 - 마이크 버튼에서 호출됨
 */
function toggleMicrophone() {
    if (isRecording) {
        stopRecording();
        return false; // 녹음 중지됨
    } else {
        if (audioStream) {
            startRecording();
            return true; // 녹음 시작됨
        } else {
            console.error('마이크가 설정되지 않았습니다.');
            return false;
        }
    }
}

/**
 * 스트림 정리 함수
 */
function cleanupStream() {
    if (audioStream) {
        audioStream.getTracks().forEach(track => track.stop());
        audioStream = null;
        console.log('오디오 스트림이 해제되었습니다.');
    }
}

/**
 * 전체 정리 함수
 */
function cleanup() {
    stopRecording();
    cleanupStream();

    if (mqttClient && mqttClient.isConnected()) {
        mqttClient.disconnect();
        console.log('MQTT 연결이 해제되었습니다.');
    }
}

/**
 * 애플리케이션 초기화 함수
 */
async function initializeApp() {
    try {
        // 설정 파일에서 API 키 로드
        const config = await loadConfig();
        GOOGLE_CLOUD_API_KEY = config.GOOGLE_CLOUD_API_KEY;

        if (!GOOGLE_CLOUD_API_KEY) {
            throw new Error('Google Cloud API 키가 설정되지 않았습니다.');
        }

        console.log('설정 로드 완료');

        // MQTT 초기화
        initializeMQTT();

        // 마이크 설정 (자동 녹음 시작하지 않음)
        await setupMicrophone();

        console.log('애플리케이션 초기화 완료 - 마이크 버튼을 클릭하여 녹음을 시작하세요.');

    } catch (error) {
        console.error('애플리케이션 초기화 실패:', error);
        alert('마이크 권한이 필요합니다. 페이지를 새로고침하고 마이크 권한을 허용해주세요.');
    }
}

// 페이지가 로드되면 애플리케이션 초기화
window.addEventListener('load', initializeApp);

// 페이지를 떠날 때 정리 작업
window.addEventListener('beforeunload', cleanup);

// 외부에서 사용할 수 있는 함수들
window.toggleMicrophone = toggleMicrophone;
window.startRecording = startRecording;
window.stopRecording = stopRecording;
window.isRecording = () => isRecording;