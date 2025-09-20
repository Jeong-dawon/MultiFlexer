# view_mode_manager.py
# 화면 분할 모드를 관리하는 매니저 클래스

from PyQt5 import QtCore, QtWidgets, QtGui
from ui_components import ReceiverWindow, Cell


class ViewModeManager(QtCore.QObject):
    """ReceiverWindow의 화면 분할 모드를 관리"""

    # 시그널: 모드 전환 시 전체 pause, 특정 셀에 sender 할당 요청
    requestPauseAll = QtCore.pyqtSignal()
    requestAssign = QtCore.pyqtSignal(int, str)  # (cell_index, sender_id)

    def __init__(self, ui: ReceiverWindow):
        super().__init__()
        self.ui = ui
        self.mode: int | None = None    # 분할 모드 (1-4)
        self.cells: list[Cell] = []     # 셀 목록
        self.focus_index: int = 0       # 현재 포커스된 셀
        self.cell_assignments: dict[int, str] = {}  # {cell_index: sender_id, ... ,cell_index: sender_id}
        self.active_senders: list[str] = []         # 현재 표시 중인 sender들 [sender_id, sender_id, sender_id] 

        self._shortcuts: list[QtWidgets.QShortcut] = []
        self._senders_provider = None  # callable -> list[(sid, name)]
        self._manager = None           # MultiReceiverManager 참조

        self._setup_shortcuts()
        QtWidgets.QApplication.instance().installEventFilter(self)

    # 외부에서 매니저 바인딩
    def bind_manager(self, manager):
        self._manager = manager
        self.requestPauseAll.connect(self._manager.pause_all_streams)
        self.requestAssign.connect(self._manager.assign_sender_to_cell)

    def set_senders_provider(self, provider_fn):
        """provider_fn() -> list[(sender_id, sender_name)]"""
        self._senders_provider = provider_fn


    # 외부 배치 데이터로 화면 설정
    @QtCore.pyqtSlot(dict)
    def apply_layout_data(self, layout_data: dict):
        """
        외부 배치 데이터를 받아서 화면 분할 모드를 설정
        layout_data = {
            'layout': 1,
            'participants': [
                {'id': 'tOQnjQ1l63p98Nc0AAAJ', 'name': '은비'},
                ...
            ]
        }
        """
        print(f"[DEBUG] apply_layout_data 호출: {layout_data}")
        
        try:
            # 레이아웃 모드와 참가자 정보 추출
            layout_mode = layout_data.get('layout', 1)
            participants = layout_data.get('participants', [])
            
            print(f"[DEBUG] 레이아웃 모드: {layout_mode}, 참가자 수: {len(participants)}")
            
            # 기존 상태 정리
            self.cell_assignments.clear()
            self.active_senders.clear()

            # 모드 설정
            self.set_mode(layout_mode)

            # peers 에 실제 존재하는 참가자만 필터링
            if self._manager:
                participants = [
                    p for p in participants
                    if p.get('id') in self._manager.peers
                ]


            # 참가자들을 각 셀에 할당
            self._assign_participants(participants)
            
        except Exception as e:
            print(f"[ERROR] apply_layout_data 처리 중 오류: {e}")
            # 오류 시 기본 모드로 설정
            self.set_mode(1)

    def _assign_participants(self, participants: list):
        """참가자들을 생성된 셀들에 순서대로 할당"""
        print(f"[DEBUG] _assign_participants 호출: {participants}")
        
        if not self.cells:
            print("[WARNING] 셀이 생성되지 않았습니다")
            return
        
        # 각 참가자를 셀에 할당
        for idx, participant in enumerate(participants):
            if idx >= len(self.cells):
                print(f"[WARNING] 참가자가 셀 수보다 많습니다. 인덱스 {idx}는 건너뜁니다")
                break
                
            sender_id = participant.get('id')
            sender_name = participant.get('name')
            
            if sender_id:
                print(f"[DEBUG] 셀 {idx}에 {sender_name}({sender_id}) 할당")
                
                # 상태 정보 업데이트
                self.cell_assignments[idx] = sender_id
                self.active_senders.append(sender_id)
                
                # 실제 할당 요청
                self.requestAssign.emit(idx, sender_id)

    def _setup_shortcuts(self):
        # ✅ 메인 윈도우(self.ui)를 부모로 해야 전역 단축키처럼 동작
        for num in (1, 2, 3, 4):
            sc = QtWidgets.QShortcut(QtGui.QKeySequence(str(num)), self.ui)
            sc.setContext(QtCore.Qt.ApplicationShortcut)
            sc.activated.connect(lambda n=num: self.set_mode(n))
            self._shortcuts.append(sc)

        # 🔑 S 키: sender 선택 메뉴
        sc_s = QtWidgets.QShortcut(QtGui.QKeySequence("S"), self.ui)
        sc_s.setContext(QtCore.Qt.ApplicationShortcut)
        sc_s.activated.connect(self._open_sender_picker)
        self._shortcuts.append(sc_s)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.KeyPress:
            k = event.key()
            if k in (QtCore.Qt.Key_1, QtCore.Qt.Key_2, QtCore.Qt.Key_3, QtCore.Qt.Key_4):
                self.set_mode({QtCore.Qt.Key_1: 1, QtCore.Qt.Key_2: 2,
                               QtCore.Qt.Key_3: 3, QtCore.Qt.Key_4: 4}[k])
                return True
            if k == QtCore.Qt.Key_S:
                self._open_sender_picker()
                return True
        return super().eventFilter(obj, event)

    def set_mode(self, mode: int):
        print(f"[DEBUG] set_mode called: {mode}")
        self.mode = mode

        # 전체 pause (지금 활성 재생을 잠깐 멈춤)
        self.requestPauseAll.emit()

        # 기존 셀 정리
        for c in self.cells:
            try:
                c.clear()
                c.setParent(None)
                c.deleteLater()
            except Exception:
                pass
        self.cells.clear()

        # 새 셀 생성
        self.cells = [Cell() for _ in range(mode)]

        # Grid 재배치
        self.ui.apply_layout(mode, self.cells)
        self._set_focus(0 if self.cells else -1)

        # 다시 한 번 전체 pause (레이아웃 전환 직후 상태 수립)
        self.requestPauseAll.emit()

    def _set_focus(self, idx: int):
        self.focus_index = idx
        for i, cell in enumerate(self.cells):
            cell.setStyleSheet("""
                QFrame {
                    background: white;
                    border: 1px solid black;
                }
            """)
            placeholder = QtWidgets.QWidget(cell)
            placeholder.setStyleSheet("background: transparent; border: none;")
            layout = QtWidgets.QVBoxLayout(placeholder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setAlignment(QtCore.Qt.AlignCenter)

            # 아이콘 (PNG 불러오기)
            icon_label = QtWidgets.QLabel()
            pixmap = QtGui.QPixmap("icons/person.png").scaled(90, 90, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            icon_label.setPixmap(pixmap)
            icon_label.setAlignment(QtCore.Qt.AlignCenter)

            # 텍스트
            text_label = QtWidgets.QLabel("· · ·  대기 중  · · ·")
            text_label.setAlignment(QtCore.Qt.AlignCenter)
            text_label.setStyleSheet("""
                QLabel {
                    color: #6b7280;
                    font-size: 22px;
                    font-weight: bold;
                }
            """)

            layout.addWidget(icon_label)
            layout.addSpacing(8)
            layout.addWidget(text_label)

            cell.put_widget(placeholder)

    def _open_sender_picker(self):
        if not self._senders_provider:
            return
        entries = self._senders_provider()
        if not entries:
            return

        menu = QtWidgets.QMenu(self.ui)
        for sid, name in entries:
            act = QtWidgets.QAction(f"{name}  ({sid[:8]})", menu)

            def on_pick(checked=False, s=sid):
                if not self.cells:
                    self.set_mode(1)
                # 레이아웃 적용 한 틱 뒤 배정
                QtCore.QTimer.singleShot(0, lambda: self._assign_to_focus(s))
                # ✅ 메뉴 닫힌 뒤 포커스 복구 (단축키 계속 먹게)
                QtCore.QTimer.singleShot(0, lambda: (
                    self.ui.activateWindow(),
                    self.ui.raise_(),
                    self.ui.setFocus()
                ))
            act.triggered.connect(on_pick)
            menu.addAction(act)

        menu.exec_(QtGui.QCursor.pos())

    def _assign_to_focus(self, sender_id: str):
        if not self.cells:
            # 혹시 모를 타이밍 이슈 보강
            self.set_mode(1)
        idx = self.focus_index if (0 <= self.focus_index < len(self.cells)) else 0
        self.requestAssign.emit(idx, sender_id)
