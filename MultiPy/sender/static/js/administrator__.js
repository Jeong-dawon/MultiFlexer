// administrator.js - UI 관리 및 상태 관리

// === 상태 관리 ===
const stateManager = {
    // 전체 참여자 목록 (MQTT로부터 받은 전체 사용자 - 모두 화면 공유 중)
    allParticipants: [],

    // 비디오 영역에 배치된 참여자들 (관리자가 선택한 표시 대상)
    placedParticipants: [],

    // 현재 레이아웃
    currentLayout: 1,

    // 전체 참여자 목록 업데이트 (MQTT에서 호출)
    updateAllParticipants(participants) {
        this.allParticipants = [...participants];
        console.log("[STATE] 전체 참여자 목록 업데이트:", this.allParticipants);

        // 기존 배치된 참여자 중 목록에서 제거된 사용자가 있는지 확인
        this.placedParticipants = this.placedParticipants.filter(name =>
            this.allParticipants.includes(name)
        );

        // UI 업데이트
        uiManager.updateParticipantList(this.allParticipants);
    },

    // 참여자를 비디오 영역에 배치
    addToVideoArea(participantName) {
        if (!this.allParticipants.includes(participantName)) {
            console.warn("[STATE] 존재하지 않는 참여자:", participantName);
            return false;
        }

        if (!this.placedParticipants.includes(participantName)) {
            this.placedParticipants.push(participantName);
            console.log("[STATE] 참여자 배치:", participantName);
            return true;
        }

        return false;
    },

    // 참여자를 비디오 영역에서 제거
    removeFromVideoArea(participantName) {
        const index = this.placedParticipants.indexOf(participantName);
        if (index > -1) {
            this.placedParticipants.splice(index, 1);
            console.log("[STATE] 참여자 제거:", participantName);
            return true;
        }
        return false;
    },

    // 레이아웃 업데이트
    setLayout(layout) {
        if (this.currentLayout !== layout) {
            this.currentLayout = layout;
            console.log("[STATE] 레이아웃 변경:", layout);
        }
    },

    // 참여자가 비디오 영역에 배치되어 있는지 확인
    isPlaced(participantName) {
        return this.placedParticipants.includes(participantName);
    },

    // 음성 인식에 사용할 참여자 이름 목록 반환
    getAllParticipants() {
        return [...this.allParticipants];
    },

    // 최적 레이아웃 계산
    getOptimalLayout(participantCount) {
        if (participantCount <= 1) return 1;
        if (participantCount <= 2) return 2;
        if (participantCount <= 3) return 3;
        return 4;
    }
};

// 전역 변수
let currentLayout = 1;
let participants = [];
let draggedParticipant = null;
let participantElements = [];

// DOM 요소들
const videoArea = document.querySelector('.video-area');
const layoutMenu = document.querySelector('.layout-menu');
const layoutOptions = document.querySelectorAll('.layout-option');
const plusIcon = document.querySelector('.plus');
const micBtn = document.querySelector('.mic-btn');

// 초기화
document.addEventListener('DOMContentLoaded', function () {
    setupLayoutOptions();
    setupMicrophoneButton();
    initializeVideoAreaEvents();
});

// 새로 생성된 참여자 요소들에 이벤트 연결하는 함수
function bindDragEventsToNewParticipants() {
    participantElements = document.querySelectorAll('.participant');

    participantElements.forEach(participant => {
        participant.addEventListener('dragstart', handleDragStart);
        participant.addEventListener('dragend', handleDragEnd);
    });

    console.log(`[DEBUG] 새 참여자 요소에 드래그 이벤트 연결 완료: ${participantElements.length}개 요소`);
}

// mqttClient.js에서 호출될 전역 함수
window.bindDragEventsToNewParticipants = bindDragEventsToNewParticipants;

// 마이크 버튼 설정
function setupMicrophoneButton() {
    if (micBtn) {
        micBtn.addEventListener('click', function () {
            if (window.toggleMicrophone) {
                const isRecording = window.toggleMicrophone();
                updateMicButtonState(isRecording);
            } else {
                console.error('음성 처리 모듈이 로드되지 않았습니다.');
            }
        });
    }
}

// 마이크 버튼 상태 업데이트
function updateMicButtonState(isRecording) {
    if (micBtn) {
        if (isRecording) {
            micBtn.style.background = '#ff4444';
            micBtn.style.boxShadow = '0 2px 8px rgba(255, 68, 68, 0.3)';
            micBtn.title = '녹음 중 - 클릭하여 중지';
        } else {
            micBtn.style.background = '#04d2af';
            micBtn.style.boxShadow = '0 2px 8px rgba(4, 210, 175, 0.3)';
            micBtn.title = '클릭하여 녹음 시작';
        }
    }
}

// MQTT 메시지로 참여자 호출 처리 (voiceHandler.js에서 호출됨)
function handleParticipantCalled(participantName) {
    console.log(`관리자 페이지에서 '${participantName}' 호출됨`);

    // 해당 참여자를 자동으로 첫 번째 빈 슬롯에 추가
    const participant = Array.from(participantElements).find(p =>
        p.querySelector('span').textContent.includes(participantName) ||
        participantName.includes(p.querySelector('span').textContent.replace(/^(윤|전|김|정|서)/, ''))
    );

    if (participant && !participants.includes(participant.querySelector('span').textContent)) {
        // 참여자가 없으면 1분할로 시작
        if (participants.length === 0) {
            selectLayout(1);
        }

        // 빈 슬롯 찾기 - 현재 레이아웃에서 빈 슬롯이 없으면 레이아웃 확장 필요
        let emptySlot = document.querySelector('.slot:not([data-occupied])');

        // 빈 슬롯이 없다면 레이아웃을 확장해야 함
        if (!emptySlot && participants.length < 4) {
            // 참여자 수에 맞는 레이아웃으로 미리 확장
            const newParticipantCount = participants.length + 1;
            const targetLayout = Math.min(newParticipantCount, 4);

            // 현재 참여자들 정보 백업
            const currentParticipants = [...participants];

            // 기존 슬롯 제거 및 참가자 목록 초기화
            participants = [];

            // 새 레이아웃 생성
            currentLayout = targetLayout;
            createVideoSlots();

            // 기존 참가자들 재배치
            currentParticipants.forEach((name, index) => {
                const targetSlot = document.querySelector(`#slot-${index}`);
                if (targetSlot) {
                    addParticipantToSlot(name, targetSlot);
                }
            });

            // 이제 빈 슬롯을 다시 찾기
            emptySlot = document.querySelector('.slot:not([data-occupied])');
        }

        if (emptySlot) {
            const participantFullName = participant.querySelector('span').textContent;
            addParticipantToSlot(participantFullName, emptySlot);

            // 시각적 피드백 - 참여자 강조
            participant.style.background = '#dcfce7';
            participant.style.borderColor = '#16a34a';
            setTimeout(() => {
                participant.style.background = '';
                participant.style.borderColor = '';
            }, 2000);
        }
    }
}

// 비디오 영역 이벤트 등록 (한 번만)
function initializeVideoAreaEvents() {
    videoArea.addEventListener('dragover', handleDragOver);
    videoArea.addEventListener('drop', handleDrop);
    videoArea.addEventListener('dragenter', handleDragEnter);
    videoArea.addEventListener('dragleave', handleDragLeave);
}

// 드래그 시작
function handleDragStart(e) {
    // 4분할이고 4명이 모두 참여 중이면 드래그 방지
    if (currentLayout === 4 && participants.length >= 4) {
        e.preventDefault();
        return false;
    }

    const participantName = e.target.querySelector('span').textContent;

    // 이미 참가 중인 사용자면 드래그 방지
    if (participants.includes(participantName)) {
        e.preventDefault();
        return false;
    }

    draggedParticipant = e.target;
    e.target.style.opacity = '0.5';

    // 데이터 전송
    e.dataTransfer.setData('text/plain', participantName);
    e.dataTransfer.effectAllowed = 'move';
}

// 드래그 종료
function handleDragEnd(e) {
    e.target.style.opacity = '1';
    draggedParticipant = null;
}

// 드래그 오버
function handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
}

// 드래그 진입
function handleDragEnter(e) {
    e.preventDefault();
    // 빈 video-area에 드래그할 때만 레이아웃 메뉴 표시
    if (participants.length === 0) {
        showLayoutMenu();
    }
}

// 드래그 이탈
function handleDragLeave(e) {
    // video-area를 완전히 벗어날 때만 메뉴 숨김
    if (!videoArea.contains(e.relatedTarget)) {
        if (participants.length === 0) {
            hideLayoutMenu();
        }
    }
}

// 드롭 처리 (video-area 전체)
function handleDrop(e) {
    e.preventDefault();
    const participantName = e.dataTransfer.getData('text/plain');

    // 이미 참가 중인 사용자인지 확인
    if (participants.includes(participantName)) {
        return;
    }

    // 4분할에서 4명이 모두 참여 중이면 드롭 방지
    if (currentLayout === 4 && participants.length >= 4) {
        return;
    }

    // 참여자가 없으면 1분할로 시작
    if (participants.length === 0) {
        selectLayout(1);
    }

    // 빈 슬롯이 있으면 자동으로 배치
    const emptySlot = document.querySelector('.slot:not([data-occupied])');
    if (emptySlot) {
        addParticipantToSlot(participantName, emptySlot);

        // 참여자 추가 후 레이아웃 자동 확장 체크
        checkAndExpandLayout();
    }
}

// 레이아웃 메뉴 표시
function showLayoutMenu() {
    layoutMenu.classList.remove('hidden');
    plusIcon.style.display = 'none';
}

// 레이아웃 메뉴 숨김
function hideLayoutMenu() {
    layoutMenu.classList.add('hidden');
    if (participants.length === 0) {
        plusIcon.style.display = 'block';
    }
}

// 레이아웃 옵션 설정
function setupLayoutOptions() {
    layoutOptions.forEach((option, index) => {
        option.addEventListener('click', function () {
            const layout = index + 1;
            selectLayout(layout);
        });

        // 레이아웃 옵션에 드롭 이벤트 추가
        option.addEventListener('dragover', handleLayoutDragOver);
        option.addEventListener('drop', function (e) {
            handleLayoutDrop(e, index + 1);
        });

        option.addEventListener('dragleave', function (e) {
            e.target.style.background = '';
        });
    });
}

// 레이아웃 드래그 오버
function handleLayoutDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    e.target.style.background = '#008f77';
}

// 레이아웃 드롭
function handleLayoutDrop(e, layout) {
    e.preventDefault();
    e.stopPropagation();

    const participantName = e.dataTransfer.getData('text/plain');

    // 이미 참가 중인 사용자인지 확인
    if (participants.includes(participantName)) {
        e.target.style.background = '';
        return;
    }

    // 레이아웃 선택 및 참가자 추가
    selectLayout(layout);

    // 첫 번째 슬롯에 자동 배치
    const firstSlot = document.querySelector('.slot');
    if (firstSlot) {
        addParticipantToSlot(participantName, firstSlot);

        // 참여자 추가 후 레이아웃 자동 확장 체크
        checkAndExpandLayout();
    }

    // 스타일 원복
    e.target.style.background = '';
}

// 레이아웃 선택
function selectLayout(layout) {
    currentLayout = layout;
    hideLayoutMenu();
    createVideoSlots();
}

// 참가자 수에 따른 자동 레이아웃 결정
function getOptimalLayout(participantCount) {
    if (participantCount <= 1) return 1;
    if (participantCount <= 2) return 2;
    if (participantCount <= 3) return 3;
    return 4;
}

// 레이아웃 자동 확장 체크 및 실행
function checkAndExpandLayout() {
    const currentParticipantCount = participants.length;

    // 참여자 수에 따른 자동 확장 규칙
    let targetLayout = currentLayout;

    if (currentLayout === 1 && currentParticipantCount === 2) {
        targetLayout = 2;  // 1분할에서 2번째 참여자 추가 시 2분할로
    } else if (currentLayout === 2 && currentParticipantCount === 3) {
        targetLayout = 3;  // 2분할에서 3번째 참여자 추가 시 3분할로
    } else if (currentLayout === 3 && currentParticipantCount === 4) {
        targetLayout = 4;  // 3분할에서 4번째 참여자 추가 시 4분할로
    }

    // 레이아웃 확장이 필요한 경우
    if (targetLayout > currentLayout) {
        // 현재 참가자들 정보 백업
        const currentParticipants = [...participants];

        // 기존 슬롯 제거 및 참가자 목록 초기화
        participants = [];

        // 새 레이아웃 생성
        currentLayout = targetLayout;
        createVideoSlots();

        // 참가자들 재배치
        currentParticipants.forEach((name, index) => {
            const targetSlot = document.querySelector(`#slot-${index}`);
            if (targetSlot) {
                addParticipantToSlot(name, targetSlot);
            }
        });
    }
}

// 비디오 슬롯 생성
function createVideoSlots() {
    // 기존 슬롯 제거
    const existingSlots = videoArea.querySelectorAll('.slot, .slots-container');
    existingSlots.forEach(slot => slot.remove());

    // 플러스 아이콘과 레이아웃 메뉴 숨김
    plusIcon.style.display = 'none';

    // 슬롯 컨테이너 생성
    const slotsContainer = document.createElement('div');
    slotsContainer.className = 'slots-container';

    if (currentLayout === 1) {
        // 1분할 : 전체 화면
        slotsContainer.style.cssText = `
            display: flex;
            width: 100%;
            height: 100%;
            padding: 10px;
            box-sizing: border-box;
        `;

        const slot = createSlot('slot-0');
        slot.style.cssText = `
            width: 100%;
            height: 100%;
            background: transparent;
            border: 2px dashed rgba(255, 255, 255, 1);
            border-radius: 10px;
            display: flex;
            justify-content: center;
            align-items: center;
            color: white;
            font-weight: bold;
            box-sizing: border-box;
        `;
        slotsContainer.appendChild(slot);

    } else if (currentLayout === 2) {
        // 2분할 : 좌우 분할
        slotsContainer.style.cssText = `
            display: flex;
            flex-direction: row;
            width: 100%;
            height: 100%;
            padding: 10px;
            box-sizing: border-box;
            gap: 2%;
        `;

        for (let i = 0; i < 2; i++) {
            const slot = createSlot(`slot-${i}`);
            slot.style.cssText = `
                width: 49%;
                height: 100%;
                background: transparent;
                border: 2px dashed rgba(255, 255, 255, 1);
                border-radius: 10px;
                display: flex;
                justify-content: center;
                align-items: center;
                color: white;
                font-weight: bold;
                box-sizing: border-box;
            `;
            slotsContainer.appendChild(slot);
        }

    } else if (currentLayout === 3) {
        // 3분할 : 큰 화면 1개 + 작은 화면 2개
        slotsContainer.style.cssText = `
            display: flex;
            flex-direction: row;
            width: 100%;
            height: 100%;
            padding: 10px;
            box-sizing: border-box;
            gap: 2%;
        `;

        // 큰 슬롯 (왼쪽)
        const mainSlot = createSlot('slot-0');
        mainSlot.style.cssText = `
            width: 65%;
            height: 100%;
            background: transparent;
            border: 2px dashed rgba(255, 255, 255, 1);
            border-radius: 10px;
            display: flex;
            justify-content: center;
            align-items: center;
            color: white;
            font-weight: bold;
            box-sizing: border-box;
        `;

        // 작은 슬롯들 컨테이너 (오른쪽)
        const smallSlotsContainer = document.createElement('div');
        smallSlotsContainer.style.cssText = `
            width: 33%;
            height: 100%;
            display: flex;
            flex-direction: column;
            gap: 2%;
        `;

        // 작은 슬롯 2개
        for (let i = 1; i < 3; i++) {
            const smallSlot = createSlot(`slot-${i}`);
            smallSlot.style.cssText = `
                width: 100%;
                height: 49%;
                background: transparent;
                border: 2px dashed rgba(255, 255, 255, 1);
                border-radius: 10px;
                display: flex;
                justify-content: center;
                align-items: center;
                color: white;
                font-weight: bold;
                box-sizing: border-box;
            `;
            smallSlotsContainer.appendChild(smallSlot);
        }

        slotsContainer.appendChild(mainSlot);
        slotsContainer.appendChild(smallSlotsContainer);

    } else if (currentLayout === 4) {
        // 4분할 : 2x2 격자
        slotsContainer.style.cssText = `
            display: grid;
            grid-template-columns: 1fr 1fr;
            grid-template-rows: 1fr 1fr;
            width: 100%;
            height: 100%;
            padding: 10px;
            box-sizing: border-box;
            gap: 2%;
        `;

        for (let i = 0; i < 4; i++) {
            const slot = createSlot(`slot-${i}`);
            slot.style.cssText = `
                width: 100%;
                height: 100%;
                background: transparent;
                border: 2px dashed rgba(255, 255, 255, 1);
                border-radius: 10px;
                display: flex;
                justify-content: center;
                align-items: center;
                color: white;
                font-weight: bold;
                box-sizing: border-box;
            `;
            slotsContainer.appendChild(slot);
        }
    }

    videoArea.appendChild(slotsContainer);
}

// 슬롯 생성 헬퍼 함수
function createSlot(id) {
    const slot = document.createElement('div');
    slot.className = 'slot';
    slot.id = id;
    slot.addEventListener('dragover', handleSlotDragOver);
    slot.addEventListener('drop', handleSlotDrop);
    slot.addEventListener('dragleave', function (e) {
        if (!e.target.hasAttribute('data-occupied')) {
            e.target.style.background = 'transparent';
            e.target.style.border = '2px dashed rgba(255, 255, 255, 1)';
        }
    });
    return slot;
}

// 슬롯 드래그 오버
function handleSlotDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (!e.target.hasAttribute('data-occupied')) {
        e.target.style.background = '#0f172a';
        e.target.style.border = '2px dashed white';
    }
}

// 슬롯 드롭
function handleSlotDrop(e) {
    e.preventDefault();
    e.stopPropagation();

    const participantName = e.dataTransfer.getData('text/plain');

    // 이미 배치된 참가자인지 확인
    if (participants.includes(participantName)) {
        if (!e.target.hasAttribute('data-occupied')) {
            e.target.style.background = 'transparent';
            e.target.style.border = '2px dashed rgba(255, 255, 255, 1)';
        }
        return;
    }

    // 이미 점유된 슬롯인지 확인
    if (e.target.hasAttribute('data-occupied')) {
        return;
    }

    // 참가자 배치
    addParticipantToSlot(participantName, e.target);

    // 참여자 추가 후 레이아웃 자동 확장 체크
    checkAndExpandLayout();
}

// 슬롯에 참가자 추가
function addParticipantToSlot(participantName, slot) {
    // 참가자 배치
    slot.innerHTML = `
        <div style="text-align: center; position: relative; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
            <div style="width: 60px; height: 60px; background: linear-gradient(135deg, #04d2af, #60aaff); border-radius: 50%; display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; margin-bottom: 10px;">
                ${participantName.charAt(0)}
            </div>
            <div style="color: white; font-weight: bold;">${participantName}</div>
            <button onclick="removeParticipant('${participantName}', this)" style="background: #ff4444; color: white; border: none; border-radius: 50%; width: 24px; height: 24px; position: absolute; top: 5px; right: 5px; cursor: pointer; font-size: 16px; display: flex; align-items: center; justify-content: center;">×</button>
        </div>
    `;

    slot.style.background = '#0f172a';
    slot.style.position = 'relative';
    slot.setAttribute('data-occupied', 'true');

    // 참가자 목록에 추가
    participants.push(participantName);

    // 원본 참가자 요소의 버튼 색상 변경
    updateParticipantButtonColor(participantName, true);
}

// 참가자 버튼 색상 업데이트 함수
function updateParticipantButtonColor(participantName, isSharing) {
    const originalParticipant = Array.from(participantElements).find(p =>
        p.querySelector('span').textContent === participantName
    );

    if (originalParticipant) {
        const muteBtn = originalParticipant.querySelector('.mute-btn');
        if (isSharing) {
            // 화면 공유 중일 때 빨간색
            muteBtn.style.background = '#ff4444';
            muteBtn.style.color = 'white';
            muteBtn.textContent = '공유 중';
        } else {
            // 일반 상태일 때 원래 색상
            muteBtn.style.background = '#04d2af';
            muteBtn.style.color = 'white';
            muteBtn.textContent = '공유 X';
        }
    }
}

// 참가자 제거
function removeParticipant(participantName, buttonElement) {
    // 슬롯에서 제거
    const slot = buttonElement.closest('.slot');
    slot.innerHTML = '';
    slot.removeAttribute('data-occupied');
    slot.style.background = 'transparent';
    slot.style.border = '2px dashed rgba(255, 255, 255, 1)';

    // 참가자 목록에서 제거
    const index = participants.indexOf(participantName);
    if (index > -1) {
        participants.splice(index, 1);
    }

    // 원본 참가자 요소의 버튼 색상을 원래대로 변경
    updateParticipantButtonColor(participantName, false);

    // 남은 참가자가 있으면 최적 레이아웃으로 재조정
    if (participants.length > 0) {
        const optimalLayout = getOptimalLayout(participants.length);

        // 현재 참가자들 정보 백업
        const currentParticipants = [...participants];

        // 기존 슬롯 제거 및 참가자 목록 초기화
        participants = [];

        // 새 레이아웃 생성
        currentLayout = optimalLayout;
        createVideoSlots();

        // 참가자들 재배치
        currentParticipants.forEach((name, index) => {
            const targetSlot = document.querySelector(`#slot-${index}`);
            if (targetSlot) {
                addParticipantToSlot(name, targetSlot);
            }
        });

    } else {
        // 모든 참가자가 제거되면 초기 상태로
        resetVideoArea();
    }
}

// 비디오 영역 초기화
function resetVideoArea() {
    const slotsContainer = videoArea.querySelector('.slots-container');
    if (slotsContainer) {
        slotsContainer.remove();
    }

    plusIcon.style.display = 'block';
    participants = [];
    currentLayout = 1;

    // 모든 참가자 요소의 버튼 색상을 원래대로 복원
    participantElements.forEach(participant => {
        const participantName = participant.querySelector('span').textContent;
        updateParticipantButtonColor(participantName, false);
    });
}

// 전역 함수로 노출 (HTML에서 onclick으로 사용)
window.removeParticipant = removeParticipant;
window.handleParticipantCalled = handleParticipantCalled;