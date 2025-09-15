// administrator.js - UI 관리 및 상태 관리

// === 상태 관리 ===
const stateManager = {
    // 전체 참여자 목록 (MQTT로부터 받은 전체 사용자 - 객체 배열)
    allParticipants: [],

    // 비디오 영역에 배치된 참여자들 
    placedParticipants: [], //[{id: "...", name: "..."}, ...] 형태

    // 현재 레이아웃
    currentLayout: 1,

    // 전체 참여자 목록 업데이트 (MQTT에서 호출)
    updateAllParticipants(participants) {
        this.allParticipants = [...participants];
        console.log("[STATE] 전체 참여자 목록 업데이트:", this.allParticipants);

        // 모든 참여자 이름 추출
        const allParticipantNames = this.getAllParticipantNames();

        // 기존 배치된 참여자 중 목록에서 제거된 사용자가 있는지 확인
        this.placedParticipants = this.placedParticipants.filter(placedParticipant =>
            allParticipantNames.includes(placedParticipant.name)
        );

        // UI 업데이트 - 모든 참여자 표시
        uiManager.updateParticipantList(allParticipantNames);
    },

    // 모든 참여자 이름 목록 반환 (활성/비활성 구분 없이)
    getAllParticipantNames() {
        return this.allParticipants.map(participant => participant.name);
    },

    // 이름으로 참여자 전체 정보 찾기
    getParticipantByName(participantName) {
        return this.allParticipants.find(p => p.name === participantName);
    },

    // 참여자를 비디오 영역에 배치
    addToVideoArea(participantName) {
        const allNames = this.getAllParticipantNames();

        if (!allNames.includes(participantName)) {
            console.warn("[STATE] 존재하지 않는 참여자:", participantName);
            return false;
        }

        // 이미 배치된 참여자인지 확인 (객체 배열에서 이름으로 확인)
        const isAlreadyPlaced = this.placedParticipants.some(p => p.name === participantName);
        if (!isAlreadyPlaced) {
            // 전체 정보 찾아서 추가
            const participantInfo = this.getParticipantByName(participantName);
            if (participantInfo) {
                this.placedParticipants.push({
                    id: participantInfo.id,
                    name: participantInfo.name
                });
                console.log("[STATE] 참여자 배치:", participantInfo);

                // MQTT로 화면 배치 상태 전송
                this.publishPlacementUpdate();
                return true;
            }
        }
        return false;
    },

    // 참여자를 비디오 영역에서 제거
    removeFromVideoArea(participantName) {
        const index = this.placedParticipants.findIndex(p => p.name === participantName);
        if (index > -1) {
            const removed = this.placedParticipants.splice(index, 1)[0];
            console.log("[STATE] 참여자 제거:", removed);

            // MQTT로 화면 배치 상태 전송
            this.publishPlacementUpdate();
            return true;
        }
        return false;
    },

    // 배치 상태를 MQTT로 전송 
    publishPlacementUpdate() {
        if (window.publishPlacementState) {
            const placementData = {
                layout: this.currentLayout,
                participants: this.placedParticipants  // 객체 배열 [{id, name}, ...]
            };
            window.publishPlacementState(JSON.stringify(placementData));
            console.log("[STATE] 배치 상태 전송:", placementData);
        }
    },

    /* 전송되는 데이터 구조
        {
            layout: 2,
            participants: [
                {id: "sender_id_123", name: "은비"},
                {id: "sender_id_456", name: "아린"}
            ]
        }
    */

    // 레이아웃 업데이트
    setLayout(layout) {
        if (this.currentLayout !== layout) {
            this.currentLayout = layout;
            console.log("[STATE] 레이아웃 변경:", layout);
        }
    },

    // 참여자가 비디오 영역에 배치되어 있는지 확인
    isPlaced(participantName) {
        return this.placedParticipants.some(p => p.name === participantName);
    },

    // 음성 인식에 사용할 참여자 이름 목록 반환 
    getAllParticipants() {
        return this.getAllParticipantNames();
    },

    // 최적 레이아웃 계산
    getOptimalLayout(participantCount) {
        if (participantCount <= 1) return 1;
        if (participantCount <= 2) return 2;
        if (participantCount <= 3) return 3;
        return 4;
    },

    // 배치된 참여자 이름 목록만 반환 (UI 호환성을 위해)
    getPlacedParticipantNames() {
        return this.placedParticipants.map(p => p.name);
    },

    // 초기 접속 시 실시간으로 공유되고 있는 화면 상태 동기화
    updateSharingInfo(screenData) {
        try {
            // 서버 상태로 업데이트
            this.currentLayout = screenData.layout || 1;
            this.placedParticipants = screenData.participants || [];

            console.log(`[STATE] 동기화: 레이아웃 ${this.currentLayout}, 참가자 ${this.placedParticipants.length}명`);

            // HTML UI를 서버 상태에 맞춰 업데이트
            this._syncWithServerState();

        } catch (error) {
            console.error("[STATE ERROR] 화면 상태 동기화 실패:", error);
        }
    },

    // 서버 상태와 HTML 동기화
    _syncWithServerState() {
        if (this.placedParticipants.length === 0) {
            // 서버에 배치된 참가자가 없으면 초기화
            uiManager.resetVideoArea();
            return;
        }

        // 서버의 레이아웃으로 HTML 화면 구성
        uiManager.selectLayout(this.currentLayout);

        // 서버에 배치된 참가자들을 HTML에 표시
        this.placedParticipants.forEach((participant, index) => {
            const targetSlot = document.querySelector(`#slot-${index}`);
            if (targetSlot && !targetSlot.hasAttribute('data-occupied')) {
                uiManager.addParticipantToSlot(participant.name, targetSlot);
            }
        });

        // 참가자 목록의 버튼 색상도 동기화
        this._updateParticipantButtonStates();
    },

    // 참가자 버튼 상태 동기화
    _updateParticipantButtonStates() {
        const placedNames = this.placedParticipants.map(p => p.name);

        // 모든 참가자 요소의 버튼 상태 업데이트
        document.querySelectorAll('.participant').forEach(participantElement => {
            const participantName = participantElement.querySelector('span').textContent;
            const isPlaced = placedNames.includes(participantName);
            uiManager.updateParticipantButtonColor(participantName, isPlaced);
        });
    }
};

// === UI 관리 ===
const uiManager = {
    // DOM 요소들
    videoArea: null,
    participantArea: null,
    layoutMenu: null,
    layoutOptions: [],
    plusIcon: null,
    micBtn: null,

    // 드래그 관련
    draggedParticipant: null,
    participantElements: [],

    // 초기화
    initialize() {
        this.videoArea = document.querySelector('.video-area');
        this.participantArea = document.querySelector('.participant-area');
        this.layoutMenu = document.querySelector('.layout-menu');
        this.layoutOptions = document.querySelectorAll('.layout-option');
        this.plusIcon = document.querySelector('.plus');
        this.micBtn = document.querySelector('.mic-btn');

        if (!this.videoArea || !this.participantArea) {
            console.error('[UI ERROR] 필수 DOM 요소를 찾을 수 없습니다.');
            return false;
        }

        this.setupEventListeners();
        console.log('[UI] UI 관리자 초기화 완료');
        return true;
    },

    // 이벤트 리스너 설정
    setupEventListeners() {
        // 비디오 영역 드래그 이벤트
        this.videoArea.addEventListener('dragover', (e) => this.handleDragOver(e));
        this.videoArea.addEventListener('drop', (e) => this.handleDrop(e));
        this.videoArea.addEventListener('dragenter', (e) => this.handleDragEnter(e));
        this.videoArea.addEventListener('dragleave', (e) => this.handleDragLeave(e));

        // 레이아웃 옵션 이벤트
        this.layoutOptions.forEach((option, index) => {
            option.addEventListener('click', () => {
                const layout = index + 1;
                this.selectLayout(layout);
            });

            // 레이아웃 옵션에 드롭 이벤트 추가
            option.addEventListener('dragover', (e) => this.handleLayoutDragOver(e));
            option.addEventListener('drop', (e) => this.handleLayoutDrop(e, index + 1));
            option.addEventListener('dragleave', (e) => {
                e.target.style.background = '';
            });
        });

        // 마이크 버튼 이벤트
        this.setupMicrophoneButton();
    },

    // 마이크 버튼 이벤트 설정
    setupMicrophoneButton() {
        if (!this.micBtn) return;

        // 마우스 이벤트
        this.micBtn.addEventListener('mousedown', (e) => {
            e.preventDefault();
            if (window.startPushToTalk) {
                window.startPushToTalk();
            }
        });

        this.micBtn.addEventListener('mouseup', (e) => {
            e.preventDefault();
            if (window.stopPushToTalk) {
                window.stopPushToTalk();
            }
        });

        this.micBtn.addEventListener('mouseleave', (e) => {
            e.preventDefault();
            if (window.stopPushToTalk) {
                window.stopPushToTalk();
            }
        });

        // 터치 이벤트
        this.micBtn.addEventListener('touchstart', (e) => {
            e.preventDefault();
            if (window.startPushToTalk) {
                window.startPushToTalk();
            }
        });

        this.micBtn.addEventListener('touchend', (e) => {
            e.preventDefault();
            if (window.stopPushToTalk) {
                window.stopPushToTalk();
            }
        });

        // 키보드 이벤트 (스페이스바)
        document.addEventListener('keydown', (e) => {
            if (e.code === 'Space' && window.isPushed && !window.isPushed()) {
                e.preventDefault();
                if (window.startPushToTalk) {
                    window.startPushToTalk();
                }
            }
        });

        document.addEventListener('keyup', (e) => {
            if (e.code === 'Space' && window.isPushed && window.isPushed()) {
                e.preventDefault();
                if (window.stopPushToTalk) {
                    window.stopPushToTalk();
                }
            }
        });
    },

    // 참여자 목록 UI 업데이트
    updateParticipantList(participantList) {
        if (!this.participantArea) return;

        // 기존 참여자 요소들 제거
        this.participantArea.innerHTML = '';

        // 참여자가 없을 때 메시지 표시
        if (participantList.length === 0) {
            const noParticipantsMsg = document.createElement('div');
            noParticipantsMsg.className = 'no-participants';
            noParticipantsMsg.textContent = '참여자를 기다리는 중...';
            noParticipantsMsg.style.cssText = `
                text-align: center;
                color: #888;
                padding: 20px;
                font-style: italic;
            `;
            this.participantArea.appendChild(noParticipantsMsg);
            this.participantElements = [];
            return;
        }

        // 새로운 참여자 목록으로 UI 생성
        participantList.forEach(userName => {
            const participantDiv = document.createElement('div');
            participantDiv.className = 'participant';
            participantDiv.draggable = true;
            participantDiv.setAttribute('data-name', userName);

            // 배치 상태에 따른 버튼 텍스트와 색상 설정
            const isPlaced = stateManager.isPlaced(userName);
            const buttonText = isPlaced ? '공유 중' : '공유 X';
            const buttonColor = isPlaced ? '#ff4444' : '#04d2af';

            participantDiv.innerHTML = `
                <span>${userName}</span>
                <button class="mute-btn" style="background: ${buttonColor}; color: white;">${buttonText}</button>
            `;

            // 드래그 이벤트 리스너 추가
            participantDiv.addEventListener('dragstart', (e) => this.handleDragStart(e));
            participantDiv.addEventListener('dragend', (e) => this.handleDragEnd(e));

            this.participantArea.appendChild(participantDiv);
        });

        // 참여자 요소 목록 업데이트
        this.participantElements = document.querySelectorAll('.participant');
        console.log(`[UI] 참여자 UI 업데이트 완료: ${participantList.length}명`);
    },

    // 드래그 관련 이벤트 처리
    handleDragStart(e) {
        const participantName = e.target.querySelector('span').textContent;

        // 4분할이고 4명이 모두 참여 중이면 드래그 방지
        if (stateManager.currentLayout === 4 && stateManager.placedParticipants.length >= 4) {
            e.preventDefault();
            return false;
        }

        // 이미 배치된 참여자면 드래그 방지 (객체 배열에서 이름으로 확인)
        if (stateManager.isPlaced(participantName)) {
            e.preventDefault();
            return false;
        }

        this.draggedParticipant = e.target;
        e.target.style.opacity = '0.5';
        e.dataTransfer.setData('text/plain', participantName);
        e.dataTransfer.effectAllowed = 'move';
    },

    handleDragEnd(e) {
        e.target.style.opacity = '1';
        this.draggedParticipant = null;
    },

    handleDragOver(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
    },

    handleDragEnter(e) {
        e.preventDefault();
        // 빈 video-area에 드래그할 때만 레이아웃 메뉴 표시
        if (stateManager.placedParticipants.length === 0) {
            this.showLayoutMenu();
        }
    },

    handleDragLeave(e) {
        if (!this.videoArea.contains(e.relatedTarget)) {
            if (stateManager.placedParticipants.length === 0) {
                this.hideLayoutMenu();
            }
        }
    },

    handleDrop(e) {
        e.preventDefault();
        const participantName = e.dataTransfer.getData('text/plain');

        // 이미 배치된 참여자인지 확인
        if (stateManager.isPlaced(participantName)) {
            e.target.style.background = '';
            return;
        }

        // 4분할에서 4명이 모두 참여 중이면 드롭 방지
        if (stateManager.currentLayout === 4 && stateManager.placedParticipants.length >= 4) {
            return;
        }

        // 참여자가 없으면 1분할로 시작
        if (stateManager.placedParticipants.length === 0) {
            this.selectLayout(1);
        }

        // 빈 슬롯이 있으면 자동으로 배치
        const emptySlot = document.querySelector('.slot:not([data-occupied])');
        if (emptySlot) {
            this.addParticipantToSlot(participantName, emptySlot);
            this.checkAndExpandLayout();
        }
    },

    // 레이아웃 드래그 이벤트
    handleLayoutDragOver(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        e.target.style.background = '#008f77';
    },

    handleLayoutDrop(e, layout) {
        e.preventDefault();
        e.stopPropagation();

        const participantName = e.dataTransfer.getData('text/plain');

        // 이미 배치된 참여자인지 확인
        if (stateManager.isPlaced(participantName)) {
            e.target.style.background = '';
            return;
        }

        // 레이아웃 선택 및 참가자 추가
        this.selectLayout(layout);

        // 첫 번째 슬롯에 자동 배치
        const firstSlot = document.querySelector('.slot');
        if (firstSlot) {
            this.addParticipantToSlot(participantName, firstSlot);
            this.checkAndExpandLayout();
        }

        e.target.style.background = '';
    },

    // 레이아웃 메뉴 표시/숨김
    showLayoutMenu() {
        if (this.layoutMenu) {
            this.layoutMenu.classList.remove('hidden');
        }
        if (this.plusIcon) {
            this.plusIcon.style.display = 'none';
        }
    },

    hideLayoutMenu() {
        if (this.layoutMenu) {
            this.layoutMenu.classList.add('hidden');
        }
        if (stateManager.placedParticipants.length === 0 && this.plusIcon) {
            this.plusIcon.style.display = 'block';
        }
    },

    // 레이아웃 선택
    selectLayout(layout) {
        stateManager.setLayout(layout);
        this.hideLayoutMenu();
        this.createVideoSlots();
    },

    // 비디오 슬롯 생성
    createVideoSlots() {
        if (!this.videoArea) return;

        const currentLayout = stateManager.currentLayout;

        // 기존 슬롯 제거
        const existingSlots = this.videoArea.querySelectorAll('.slot, .slots-container');
        existingSlots.forEach(slot => slot.remove());

        // 플러스 아이콘 숨김
        if (this.plusIcon) {
            this.plusIcon.style.display = 'none';
        }

        // 슬롯 컨테이너 생성
        const slotsContainer = document.createElement('div');
        slotsContainer.className = 'slots-container';

        if (currentLayout === 1) {
            this.create1Layout(slotsContainer);
        } else if (currentLayout === 2) {
            this.create2Layout(slotsContainer);
        } else if (currentLayout === 3) {
            this.create3Layout(slotsContainer);
        } else if (currentLayout === 4) {
            this.create4Layout(slotsContainer);
        }

        this.videoArea.appendChild(slotsContainer);
    },

    // 레이아웃별 슬롯 생성 메서드들
    create1Layout(container) {
        container.style.cssText = `
            display: flex;
            width: 100%;
            height: 100%;
            padding: 10px;
            box-sizing: border-box;
        `;

        const slot = this.createSlot('slot-0');
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
        container.appendChild(slot);
    },

    create2Layout(container) {
        container.style.cssText = `
            display: flex;
            flex-direction: row;
            width: 100%;
            height: 100%;
            padding: 10px;
            box-sizing: border-box;
            gap: 2%;
        `;

        for (let i = 0; i < 2; i++) {
            const slot = this.createSlot(`slot-${i}`);
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
            container.appendChild(slot);
        }
    },

    create3Layout(container) {
        container.style.cssText = `
            display: flex;
            flex-direction: row;
            width: 100%;
            height: 100%;
            padding: 10px;
            box-sizing: border-box;
            gap: 2%;
        `;

        // 큰 슬롯 (왼쪽)
        const mainSlot = this.createSlot('slot-0');
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
            const smallSlot = this.createSlot(`slot-${i}`);
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

        container.appendChild(mainSlot);
        container.appendChild(smallSlotsContainer);
    },

    create4Layout(container) {
        container.style.cssText = `
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
            const slot = this.createSlot(`slot-${i}`);
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
            container.appendChild(slot);
        }
    },

    // 슬롯 생성 헬퍼
    createSlot(id) {
        const slot = document.createElement('div');
        slot.className = 'slot';
        slot.id = id;
        slot.addEventListener('dragover', (e) => this.handleSlotDragOver(e));
        slot.addEventListener('drop', (e) => this.handleSlotDrop(e));
        slot.addEventListener('dragleave', (e) => {
            if (!e.target.hasAttribute('data-occupied')) {
                e.target.style.background = 'transparent';
                e.target.style.border = '2px dashed rgba(255, 255, 255, 1)';
            }
        });
        return slot;
    },

    // 슬롯 드래그 이벤트
    handleSlotDragOver(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (!e.target.hasAttribute('data-occupied')) {
            e.target.style.background = '#0f172a';
            e.target.style.border = '2px dashed white';
        }
    },

    handleSlotDrop(e) {
        e.preventDefault();
        e.stopPropagation();

        const participantName = e.dataTransfer.getData('text/plain');

        // 이미 배치된 참가자인지 확인
        if (stateManager.isPlaced(participantName)) {
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
        this.addParticipantToSlot(participantName, e.target);
        this.checkAndExpandLayout();
    },

    // 슬롯에 참가자 추가
    addParticipantToSlot(participantName, slot) {
        // 상태 관리자에 추가
        stateManager.addToVideoArea(participantName);

        // UI 업데이트
        slot.innerHTML = `
            <div style="text-align: center; position: relative; width: 100%; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                <div style="width: 60px; height: 60px; background: linear-gradient(135deg, #04d2af, #60aaff); border-radius: 50%; display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; margin-bottom: 10px;">
                    ${participantName.charAt(0)}
                </div>
                <div style="color: white; font-weight: bold;">${participantName}</div>
                <button onclick="uiManager.removeParticipant('${participantName}', this)" style="background: #ff4444; color: white; border: none; border-radius: 50%; width: 24px; height: 24px; position: absolute; top: 5px; right: 5px; cursor: pointer; font-size: 16px; display: flex; align-items: center; justify-content: center;">×</button>
            </div>
        `;

        slot.style.background = '#0f172a';
        slot.style.position = 'relative';
        slot.setAttribute('data-occupied', 'true');

        // 원본 참가자 요소의 시각적 표시 변경
        this.updateParticipantButtonColor(participantName, true);
    },

    // 참가자 버튼 색상 업데이트
    updateParticipantButtonColor(participantName, isPlaced) {
        const originalParticipant = Array.from(this.participantElements).find(p =>
            p.querySelector('span').textContent === participantName
        );

        if (originalParticipant) {
            const muteBtn = originalParticipant.querySelector('.mute-btn');
            if (muteBtn) {
                if (isPlaced) {
                    // 배치된 상태 - "공유 중" (빨간색)
                    muteBtn.style.background = '#ff4444';
                    muteBtn.style.color = 'white';
                    muteBtn.textContent = '공유 중';
                } else {
                    // 배치 안된 상태 - "공유 X" (초록색)
                    muteBtn.style.background = '#04d2af';
                    muteBtn.style.color = 'white';
                    muteBtn.textContent = '공유 X';
                }
            }
        }
    },

    // 참가자 제거
    removeParticipant(participantName, buttonElement) {
        // 슬롯에서 제거
        const slot = buttonElement.closest('.slot');
        slot.innerHTML = '';
        slot.removeAttribute('data-occupied');
        slot.style.background = 'transparent';
        slot.style.border = '2px dashed rgba(255, 255, 255, 1)';

        // 상태 관리자에서 제거
        stateManager.removeFromVideoArea(participantName);

        // 원본 참가자 요소의 버튼 색상을 원래대로 변경
        this.updateParticipantButtonColor(participantName, false);

        // 레이아웃 재조정
        this.adjustLayoutAfterRemoval();
    },

    // 제거 후 레이아웃 조정
    adjustLayoutAfterRemoval() {
        if (stateManager.placedParticipants.length > 0) {
            const optimalLayout = stateManager.getOptimalLayout(stateManager.placedParticipants.length);

            // 현재 참가자들 정보 백업
            const currentParticipantNames = stateManager.getPlacedParticipantNames();

            // 상태 초기화
            currentParticipantNames.forEach(name => {
                stateManager.removeFromVideoArea(name);
            });

            // 새 레이아웃 생성
            stateManager.setLayout(optimalLayout);
            this.createVideoSlots();

            // 참가자들 재배치
            currentParticipantNames.forEach((name, index) => {
                const targetSlot = document.querySelector(`#slot-${index}`);
                if (targetSlot) {
                    this.addParticipantToSlot(name, targetSlot);
                }
            });

        } else {
            // 모든 참가자가 제거되면 초기 상태로
            this.resetVideoArea();
        }
    },

    // 레이아웃 자동 확장 체크
    checkAndExpandLayout() {
        const currentParticipantCount = stateManager.placedParticipants.length;
        let targetLayout = stateManager.currentLayout;

        // 자동 확장 규칙
        if (stateManager.currentLayout === 1 && currentParticipantCount === 2) {
            targetLayout = 2;
        } else if (stateManager.currentLayout === 2 && currentParticipantCount === 3) {
            targetLayout = 3;
        } else if (stateManager.currentLayout === 3 && currentParticipantCount === 4) {
            targetLayout = 4;
        }

        // 레이아웃 확장이 필요한 경우
        if (targetLayout > stateManager.currentLayout) {
            // 현재 참가자들 정보 백업
            const currentParticipantNames = stateManager.getPlacedParticipantNames();

            // 상태 초기화
            currentParticipantNames.forEach(name => {
                stateManager.removeFromVideoArea(name);
            });

            // 새 레이아웃 생성
            stateManager.setLayout(targetLayout);
            this.createVideoSlots();

            // 참가자들 재배치
            currentParticipantNames.forEach((name, index) => {
                const targetSlot = document.querySelector(`#slot-${index}`);
                if (targetSlot) {
                    this.addParticipantToSlot(name, targetSlot);
                }
            });
        }
    },

    // 비디오 영역 초기화
    resetVideoArea() {
        if (!this.videoArea) return;

        const slotsContainer = this.videoArea.querySelector('.slots-container');
        if (slotsContainer) {
            slotsContainer.remove();
        }

        if (this.plusIcon) {
            this.plusIcon.style.display = 'block';
        }

        // 상태 관리자 초기화
        stateManager.setLayout(1);

        // 모든 참가자 요소의 버튼 색상을 원래대로 복원
        this.participantElements.forEach(participant => {
            const participantName = participant.querySelector('span').textContent;
            this.updateParticipantButtonColor(participantName, false);
        });
    },

    // 참여자 호출 처리 (음성 인식에서 호출)
    handleParticipantCalled(participantName) {
        console.log(`[UI] '${participantName}' 호출됨`);

        // 해당 참여자가 전체 목록에 있는지 확인
        const allNames = stateManager.getAllParticipantNames();
        if (!allNames.includes(participantName)) {
            console.warn(`[UI] 존재하지 않는 참여자: ${participantName}`);
            return;
        }

        // 이미 배치된 참여자인지 확인
        if (stateManager.isPlaced(participantName)) {
            console.log(`[UI] 이미 배치된 참여자: ${participantName}`);
            return;
        }

        // 참여자가 없으면 1분할로 시작
        if (stateManager.placedParticipants.length === 0) {
            this.selectLayout(1);
        }

        // 빈 슬롯 찾기
        let emptySlot = document.querySelector('.slot:not([data-occupied])');

        // 빈 슬롯이 없다면 레이아웃 확장
        if (!emptySlot && stateManager.placedParticipants.length < 4) {
            const newParticipantCount = stateManager.placedParticipants.length + 1;
            const targetLayout = Math.min(newParticipantCount, 4);

            // 현재 참가자들 정보 백업
            const currentParticipantNames = stateManager.getPlacedParticipantNames();

            // 상태 초기화
            currentParticipantNames.forEach(name => {
                stateManager.removeFromVideoArea(name);
            });

            // 새 레이아웃 생성
            stateManager.setLayout(targetLayout);
            this.createVideoSlots();

            // 기존 참가자들 재배치
            currentParticipantNames.forEach((name, index) => {
                const targetSlot = document.querySelector(`#slot-${index}`);
                if (targetSlot) {
                    this.addParticipantToSlot(name, targetSlot);
                }
            });

            // 빈 슬롯 다시 찾기
            emptySlot = document.querySelector('.slot:not([data-occupied])');
        }

        // 빈 슬롯에 참여자 배치
        if (emptySlot) {
            this.addParticipantToSlot(participantName, emptySlot);
        }
    }
};

// 마이크 버튼 상태 업데이트 함수 (음성 모듈에서 호출)
function updateMicButtonState(isRecording) {
    if (uiManager.micBtn) {
        if (isRecording) {
            uiManager.micBtn.style.background = '#ff4444';
            uiManager.micBtn.style.boxShadow = '0 2px 8px rgba(255, 68, 68, 0.3)';
            uiManager.micBtn.title = '녹음 중 - 버튼을 떼면 전송됩니다';
            uiManager.micBtn.classList.add('recording');
        } else {
            uiManager.micBtn.style.background = '#04d2af';
            uiManager.micBtn.style.boxShadow = '0 2px 8px rgba(4, 210, 175, 0.3)';
            uiManager.micBtn.title = '누르고 있으면 녹음됩니다';
            uiManager.micBtn.classList.remove('recording');
        }
    }
}

// 전역 함수들 노출 (다른 모듈에서 사용)
window.stateManager = stateManager;
window.uiManager = uiManager;
window.handleParticipantCalled = uiManager.handleParticipantCalled.bind(uiManager);
window.updateMicButtonState = updateMicButtonState;

// DOM 로드 후 초기화
document.addEventListener('DOMContentLoaded', function () {
    console.log('[ADMIN] DOM 로드 완료 - UI 초기화');
    uiManager.initialize();
});

// 대시보드 토글 버튼 이벤트 
document.addEventListener('DOMContentLoaded', function () {
  const dashboardBtn = document.getElementById('dashboard-btn');
  const dashLeft = document.querySelector('.dash-left');
  const dashRight = document.querySelector('.dash-right');

  let isDashboardActive = false; // 토글 상태 저장

  if (dashboardBtn) {
    dashboardBtn.addEventListener('click', () => {
      isDashboardActive = !isDashboardActive;

      if (isDashboardActive) {
        // 대시보드 모드 ON
        dashboardBtn.textContent = '대시보드 중지';
        dashboardBtn.style.background = '#ff4444';

        // 덮개 켜기
        dashLeft.classList.remove('hidden');
        dashRight.classList.remove('hidden');
      } else {
        // 대시보드 모드 OFF
        dashboardBtn.textContent = '대시보드 보기';
        dashboardBtn.style.background = '#04d2af';

        // 덮개 끄기
        dashLeft.classList.add('hidden');
        dashRight.classList.add('hidden');
      }
    });
  }
});
