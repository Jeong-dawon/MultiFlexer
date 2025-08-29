# ui_components.py
from PyQt5 import QtCore, QtWidgets, QtGui
from config import DEFAULT_WINDOW_SIZE, WINDOW_TITLE


class InfoPopup(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent, flags=(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.Tool
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.WindowDoesNotAcceptFocus
        ))
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self._setup_ui()

    def _setup_ui(self):
        self._label = QtWidgets.QLabel("", self)
        self._label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._label.setStyleSheet("color:white; font-size:16px; padding:10px;")
        self.setStyleSheet("QFrame { background:rgba(0,0,0,160); border-radius:10px; }")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.addWidget(self._label)

    def set_text(self, text: str):
        self._label.setText(text)
        self.adjustSize()

    def show_at_parent_corner(self, parent: QtWidgets.QWidget, margin: int = 16):
        try:
            top_left = parent.mapToGlobal(QtCore.QPoint(margin, margin))
        except Exception:
            top_left = QtCore.QPoint(margin, margin)
        self.move(top_left)
        self.show()


class Cell(QtWidgets.QFrame):
    clicked = QtCore.pyqtSignal()
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:black;")
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0,0,0,0)
        self._layout.setSpacing(0)

    def mousePressEvent(self, e):
        self.clicked.emit()

    def put_widget(self, w: QtWidgets.QWidget):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget(): item.widget().setParent(None)
        self._layout.addWidget(w)

    def clear(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget(): item.widget().setParent(None)


class ReceiverWindow(QtWidgets.QMainWindow):
    switchRequested = QtCore.pyqtSignal(int)
    quitRequested = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("WebRTC Receiver")
        self.resize(1280, 720)

        self._widgets = {}
        self._names = {}
        self._current_sender_id = None

        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        # 중앙 위젯 + 2개 레이아웃
        self._central = QtWidgets.QWidget()
        self.setCentralWidget(self._central)
        self._main = QtWidgets.QStackedLayout(self._central)  # ← 메인은 스택형

        # stack 컨테이너
        self._stack_container = QtWidgets.QWidget()
        self._stack = QtWidgets.QStackedLayout(self._stack_container)
        self._placeholder = QtWidgets.QLabel("Waiting for senders...", alignment=QtCore.Qt.AlignCenter)
        self._placeholder.setStyleSheet("color:#888; font-size:18px;")
        self._stack.addWidget(self._placeholder)

        # grid 컨테이너
        self._grid_container = QtWidgets.QWidget()
        self._grid = QtWidgets.QGridLayout(self._grid_container)
        self._grid.setContentsMargins(0,0,0,0)
        self._grid.setSpacing(0)

        # 메인 스택에 두 컨테이너 추가 (0=stack, 1=grid)
        self._main.addWidget(self._stack_container)
        self._main.addWidget(self._grid_container)
        self._main.setCurrentIndex(0)  # 기본: 단일 모드

        # placeholder
        self._placeholder = QtWidgets.QLabel("Waiting for senders...", alignment=QtCore.Qt.AlignCenter)
        self._placeholder.setStyleSheet("color:#888; font-size:18px;")
        self._stack.addWidget(self._placeholder)

        self._info_popup = InfoPopup(self)
        self._info_popup.hide()

        self._setup_shortcuts()

    def set_mode(self, use_grid: bool):
        """stack <-> grid 전환 (레이아웃 파괴 금지)"""
        self._main.setCurrentIndex(1 if use_grid else 0)

    def apply_layout(self, mode: int, cells: list[Cell]):
        self.set_mode(True)

        # 레이아웃만 비우기 (부모/위젯 파괴 금지)
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                self._grid.removeWidget(w)

        # 보기 좋게 늘리기
        self._grid.setRowStretch(0, 1)
        self._grid.setRowStretch(1, 1)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)

        if mode == 1 and cells:
            self._grid.addWidget(cells[0], 0, 0, 2, 2)  # 창 꽉 채움
        elif mode == 2 and len(cells) >= 2:
            self._grid.addWidget(cells[0], 0, 0, 2, 1)
            self._grid.addWidget(cells[1], 0, 1, 2, 1)
        elif mode == 3 and len(cells) >= 3:
            self._grid.addWidget(cells[0], 0, 0, 1, 1)
            self._grid.addWidget(cells[1], 0, 1, 1, 1)
            self._grid.addWidget(cells[2], 1, 0, 1, 2)
        elif mode == 4:
            for i, cell in enumerate(cells[:4]):
                r, c = divmod(i, 2)
                self._grid.addWidget(cell, r, c)

    def _setup_shortcuts(self):
        shortcuts = [
            (QtCore.Qt.Key_Left, lambda: self.switchRequested.emit(-1)),
            (QtCore.Qt.Key_Right, lambda: self.switchRequested.emit(+1)),
            (QtCore.Qt.Key_Up, self.show_sender_info_popup),
            (QtCore.Qt.Key_Down, self.hide_sender_info_popup),
            (QtCore.Qt.Key_Escape, self._toggle_fullscreen),
            (QtCore.Qt.Key_Q, self.quitRequested.emit),
        ]
        for key, cb in shortcuts:
            sc = QtWidgets.QShortcut(QtGui.QKeySequence(key), self)
            sc.setContext(QtCore.Qt.ApplicationShortcut)
            sc.activated.connect(cb)

    def get_widget(self, sender_id: str):
        return self._widgets.get(sender_id)

    def _toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    @QtCore.pyqtSlot(str, str, result=object)
    def ensure_widget(self, sender_id: str, sender_name: str):
        w = self._widgets.get(sender_id)
        def _is_dead(obj: QtWidgets.QWidget) -> bool:
            # PyQt 래퍼가 죽었으면 아래 접근에서 RuntimeError가 난다.
            try:
                # 가벼운 접근 몇 개: 어느 하나라도 RuntimeError가 나면 죽었다고 판단
                _ = obj.objectName()
                _ = obj.winId()       # 네이티브 핸들 접근
                return False
            except Exception:
                return True

        # 기존 위젯이 없거나, 죽었으면 재생성
        if (w is None) or _is_dead(w):
            w = QtWidgets.QWidget(self)
            w.setObjectName(f"video-{sender_id}")
            w.setStyleSheet("background:black;")
            w.setFocusPolicy(QtCore.Qt.NoFocus)
            w.setAttribute(QtCore.Qt.WA_NativeWindow, True)
            _ = w.winId()  # 핸들 실체화
            self._widgets[sender_id] = w
            self._stack.addWidget(w)

        if sender_name:
            self._names[sender_id] = sender_name
        return w

    def get_widget(self, sender_id: str):
        return self._widgets.get(sender_id)

    def set_active_sender(self, sender_id: str):
        self._current_sender_id = sender_id
        w = self._widgets.get(sender_id)
        self._stack.setCurrentWidget(w if w else self._placeholder)

    def set_active_sender_name(self, sender_id: str, sender_name: str):
        if sender_name:
            self._names[sender_id] = sender_name
        self.set_active_sender(sender_id)

    def remove_sender_widget(self, sender_id: str):
        w = self._widgets.pop(sender_id, None)
        self._names.pop(sender_id, None)
        if w:
            self._stack.removeWidget(w)
            w.setParent(None)
            w.deleteLater()
        if self._current_sender_id == sender_id:
            self._current_sender_id = None
            self._stack.setCurrentWidget(self._placeholder)
            self.hide_sender_info_popup()

    def show_sender_info_popup(self):
        sid = self._current_sender_id
        if sid:
            name = self._names.get(sid, sid)
            self._info_popup.set_text(f"Sender: {name} ({sid[:8]})")
            self._info_popup.show_at_parent_corner(self)

    def hide_sender_info_popup(self):
        self._info_popup.hide()
