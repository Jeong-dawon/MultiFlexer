# ui_components.py
from PyQt5 import QtCore, QtWidgets, QtGui
from config import DEFAULT_WINDOW_SIZE, WINDOW_TITLE


class Cell(QtWidgets.QFrame):
    clicked = QtCore.pyqtSignal()
    def __init__(self):
        super().__init__()
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
        self.setWindowTitle("Multiplexer")
        self.resize(1280, 720)
        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0   rgba(240, 248, 255, 255),
                stop:0.5 rgba(230, 240, 250, 255),
                stop:1   rgba(250, 245, 250, 255)
                );
            }
        """)    

        self._first_sender_connected = False 

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

        landing = self._build_landing_card()
        self._stack.addWidget(landing)
        self._stack.setCurrentWidget(landing)
        self._landing = landing

        # grid 컨테이너
        self._grid_container = QtWidgets.QWidget()
        self._grid = QtWidgets.QGridLayout(self._grid_container)
        self._grid.setContentsMargins(0,0,0,0)
        self._grid.setSpacing(0)

        # 메인 스택에 두 컨테이너 추가 (0=stack, 1=grid)
        self._main.addWidget(self._stack_container)
        self._main.addWidget(self._grid_container)
        self._main.setCurrentIndex(0)  # 기본: 단일 모드


        self._setup_shortcuts()

    def enter_sender_mode(self):
        if hasattr(self, "_landing_status") and self._landing_status:
            self._landing_status.setText("접속자 선택 대기 중")
            self._landing_status.setStyleSheet("color: black;")
        t = getattr(self, "_status_timer", None)
        if t:
            t.stop()
        self.setStyleSheet("""
            QMainWindow {
                background: #f5f5f5;
            }
        """)


    def enter_landing_mode(self):
        self._main.setCurrentIndex(0)
        if hasattr(self, "_landing_status") and self._landing_status:
            self._landing_status.setText("· · ·   접속자 대기 중   · · ·")
            self._landing_status.setStyleSheet("color: #6b7280;")
        t = getattr(self, "_status_timer", None)
        if t:
            t.start(600)
        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0   rgba(4,210,175,13),
                    stop:0.5 rgba(96,170,255,13),
                    stop:1   rgba(255,143,107,13)
                );
            }
        """)
    


    def _build_landing_card(self):
        wrapper = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(wrapper)
        root.setAlignment(QtCore.Qt.AlignCenter)

        card = QtWidgets.QFrame(objectName="card")
        card.setStyleSheet("""
            QFrame#card {
                background: white;
                border-radius: 24px;
                border: 1px solid #e5e7eb;
            }
        """)
        card.setFixedSize(360, 360)

        c = QtWidgets.QVBoxLayout(card)
        c.setContentsMargins(32, 40, 32, 40) 
        c.setSpacing(0)


        c.addStretch(1)
        logo = QtWidgets.QLabel("M")
        logo.setAlignment(QtCore.Qt.AlignCenter)
        logo.setFixedSize(96, 96)
        logo.setStyleSheet("""
            QLabel {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                                            stop:0 #04d2af, stop:1 #60aaff);
                color: white; font-size: 36px; font-weight: 700;
                border-radius: 20px;
            }
        """)
        glow = QtWidgets.QGraphicsDropShadowEffect(logo)
        glow.setBlurRadius(32)
        glow.setOffset(0, 4)
        glow.setColor(QtGui.QColor(4, 210, 175, int(0.4 * 255)))
        logo.setGraphicsEffect(glow)

        c.addWidget(logo, alignment=QtCore.Qt.AlignHCenter)
        c.addSpacing(20)
        c.addStretch(1)

       
        tb = QtWidgets.QVBoxLayout()
        tb.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        tb.setSpacing(6) 

        title = QtWidgets.QLabel("Multiplexer")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setFont(QtGui.QFont("Inter", 24, QtGui.QFont.DemiBold))
        title.setStyleSheet("color: #34a6ff;")
        tb.addWidget(title)

        subtitle = QtWidgets.QLabel("화면 공유 시스템")
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        subtitle.setFont(QtGui.QFont("Inter", 13))
        subtitle.setStyleSheet("color: #6b7280;")
        tb.addWidget(subtitle)

        tb.addSpacing(24) 

        self._landing_status = QtWidgets.QLabel("· · ·  접속자 대기 중  · · ·")
        self._landing_status.setAlignment(QtCore.Qt.AlignCenter)
        self._landing_status.setFont(QtGui.QFont("Inter", 13))
        self._landing_status.setStyleSheet("color: #6b7280;")
        tb.addWidget(self._landing_status)

        c.addLayout(tb)
        c.addStretch(1)
        root.addWidget(card)

        self._dots = 0
        eff = QtWidgets.QGraphicsOpacityEffect(self._landing_status)
        self._landing_status.setGraphicsEffect(eff)
        self._status_opacity_effect = eff
        self._fade_dir = 1

        timer = QtCore.QTimer(self)
        timer.timeout.connect(self._tick)
        timer.start(600)
        self._status_timer = timer


        return wrapper

    def _tick(self):
        
        self._dots = (self._dots + 1) % 4
        dots = "· " * self._dots
        self._landing_status.setText(f"{dots}접속자 대기 중{dots}".center(17, " "))

       
        if hasattr(self, "_status_opacity_effect"):
            cur = self._status_opacity_effect.opacity()
            step = 0.15 * self._fade_dir
            new = cur + step
            if new >= 1.0:
                new, self._fade_dir = 1.0, -1
            elif new <= 0.5:
                new, self._fade_dir = 0.5, +1
        self._status_opacity_effect.setOpacity(new)

    # ===== 표시 모드 전환 =====
    def set_landing_visible(self, on: bool):
        self._main.setCurrentIndex(0 if on else 1)

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
            self._grid.addWidget(cells[0], 0, 0, 2, 1)
            self._grid.addWidget(cells[1], 0, 1, 1, 1)
            self._grid.addWidget(cells[2], 1, 1, 1, 1)
            self._grid.setColumnStretch(0, 1)
            self._grid.setColumnStretch(1, 1)
            self._grid.setRowStretch(0, 1)
            self._grid.setRowStretch(1, 1)
        elif mode == 4:
            for i, cell in enumerate(cells[:4]):
                r, c = divmod(i, 2)
                self._grid.addWidget(cell, r, c)

    def _setup_shortcuts(self):
        shortcuts = [
            (QtCore.Qt.Key_Left, lambda: self.switchRequested.emit(-1)),
            (QtCore.Qt.Key_Right, lambda: self.switchRequested.emit(+1)),
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
        self._stack.setCurrentWidget(w if w else self._landing)

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
            self._stack.setCurrentWidget(self._landing)
            

    
