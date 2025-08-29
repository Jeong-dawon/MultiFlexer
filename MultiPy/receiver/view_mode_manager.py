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
        self.mode: int | None = None
        self.cells: list[Cell] = []
        self.focus_index: int = 0

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

    def _setup_shortcuts(self):
        for num in (1, 2, 3, 4):
            sc = QtWidgets.QShortcut(QtGui.QKeySequence(str(num)), self.ui.centralWidget())
            sc.setContext(QtCore.Qt.ApplicationShortcut)
            sc.activated.connect(lambda n=num: self.set_mode(n))
            self._shortcuts.append(sc)

        # 🔑 S 키: sender 선택 메뉴
        sc_s = QtWidgets.QShortcut(QtGui.QKeySequence("S"), self.ui.centralWidget())
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

        self.requestPauseAll.emit()

        for c in self.cells:
            try:
                c.clear()         # 🔑 내부 위젯 제거 (Qt 쪽 parent 해제)
                c.setParent(None)
                c.deleteLater()
            except Exception:
                pass
        self.cells.clear()

        # 3) 새 셀 생성
        self.cells = [Cell() for _ in range(mode)]
        for idx, cell in enumerate(self.cells):
            cell.clicked.connect(lambda i=idx: self._set_focus(i))

        # Grid 재배치
        self.ui.apply_layout(mode, self.cells)
        self._set_focus(0 if self.cells else -1)

        # 전체 pause
        self.requestPauseAll.emit()

    def _set_focus(self, idx: int):
        self.focus_index = idx
        for i, cell in enumerate(self.cells):
            cell.setStyleSheet(
                "background:black; border: 3px solid #66aaff;" if i == idx
                else "background:black; border:none;"
            )

    def _open_sender_picker(self):
        if not self._senders_provider:
            return
        entries = self._senders_provider()
        if not entries:
            return

        menu = QtWidgets.QMenu(self.ui)
        for sid, name in entries:
            act = QtWidgets.QAction(f"{name}  ({sid[:8]})", menu)
            act.triggered.connect(lambda _, s=sid: self._assign_to_focus(s))
            menu.addAction(act)

        pos = QtGui.QCursor.pos()
        menu.exec_(pos)

    def _assign_to_focus(self, sender_id: str):
        if 0 <= self.focus_index < len(self.cells):
            self.requestAssign.emit(self.focus_index, sender_id)
