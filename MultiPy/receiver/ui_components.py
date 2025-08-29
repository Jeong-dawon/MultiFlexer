# ui_components.py
# PyQt5 UI 컴포넌트들

from PyQt5 import QtCore, QtWidgets, QtGui
from config import DEFAULT_WINDOW_SIZE, WINDOW_TITLE


class InfoPopup(QtWidgets.QFrame):
    """좌상단에 뜨는 작은 정보 팝업(포커스 훔치지 않음)"""
    
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
        """UI 초기화"""
        self._label = QtWidgets.QLabel("", self)
        self._label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._label.setStyleSheet("""
            QLabel { 
                color: white; 
                font-size: 16px; 
                padding: 10px 14px; 
                background: rgba(0,0,0,0); 
            }
        """)
        
        self.setStyleSheet("""
            QFrame { 
                background: rgba(0,0,0,160); 
                border: 1px solid rgba(255,255,255,90); 
                border-radius: 10px; 
            }
        """)
        
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.addWidget(self._label)

    def set_text(self, text: str):
        """텍스트 설정"""
        self._label.setText(text)
        self.adjustSize()

    def show_at_parent_corner(self, parent: QtWidgets.QWidget, margin: int = 16):
        """부모 위젯 모서리에 표시"""
        try:
            top_left = parent.mapToGlobal(QtCore.QPoint(margin, margin))
        except Exception:
            top_left = QtCore.QPoint(margin, margin)
        self.move(top_left)
        self.show()


class ReceiverWindow(QtWidgets.QMainWindow):
    """메인 수신기 윈도우"""
    
    # 시그널 정의
    switchRequested = QtCore.pyqtSignal(int)  # 좌우 전환: +1(다음), -1(이전)
    quitRequested = QtCore.pyqtSignal() # Q 종료 요청

    def __init__(self):
        super().__init__()
        self._widgets = {}  # sender_id -> QWidget
        self._names = {}    # sender_id -> sender_name
        self._current_sender_id = None
        
        self._setup_ui()
        self._setup_shortcuts()

    def _setup_ui(self):
        """UI 초기화"""
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*DEFAULT_WINDOW_SIZE)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        # 정보 팝업
        self._info_popup = InfoPopup(self)
        self._info_popup.hide()

        # 메인 레이아웃
        cw = QtWidgets.QWidget(self)
        self.setCentralWidget(cw)
        self._stack = QtWidgets.QStackedLayout()
        lay = QtWidgets.QVBoxLayout(cw)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._stack)

        # 플레이스홀더
        self._placeholder = QtWidgets.QLabel(
            "Waiting for senders...", 
            alignment=QtCore.Qt.AlignCenter
        )
        self._placeholder.setStyleSheet("color:#888; font-size:18px;")
        self._stack.addWidget(self._placeholder)

    def _setup_shortcuts(self):
        """단축키 설정"""
        shortcuts = [
            (QtCore.Qt.Key_Left, lambda: self.switchRequested.emit(-1)),
            (QtCore.Qt.Key_Right, lambda: self.switchRequested.emit(+1)),
            (QtCore.Qt.Key_Up, self.show_sender_info_popup),
            (QtCore.Qt.Key_Down, self.hide_sender_info_popup),
            (QtCore.Qt.Key_Escape, self._toggle_fullscreen),
            (QtCore.Qt.Key_Q, self.quitRequested.emit),
        ]
        
        for key, callback in shortcuts:
            shortcut = QtWidgets.QShortcut(QtGui.QKeySequence(key), self)
            shortcut.setContext(QtCore.Qt.ApplicationShortcut)
            shortcut.activated.connect(callback)

    def _toggle_fullscreen(self):
        """전체화면 토글"""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    @QtCore.pyqtSlot(str, str, result=object)
    def ensure_widget(self, sender_id: str, sender_name: str):
        """위젯 생성 또는 반환"""
        w = self._widgets.get(sender_id)
        if w is None:
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
        """위젯 반환"""
        return self._widgets.get(sender_id)

    @QtCore.pyqtSlot(str)
    def set_active_sender(self, sender_id: str):
        """활성 sender 설정"""
        self._current_sender_id = sender_id
        w = self._widgets.get(sender_id)
        if w:
            self._stack.setCurrentWidget(w)
        else:
            self._stack.setCurrentWidget(self._placeholder)

    @QtCore.pyqtSlot(str, str)
    def set_active_sender_name(self, sender_id: str, sender_name: str):
        """활성 sender 이름과 함께 설정"""
        if sender_name:
            self._names[sender_id] = sender_name
        self.set_active_sender(sender_id)

    @QtCore.pyqtSlot(str)
    def remove_sender_widget(self, sender_id: str):
        """sender 위젯 제거"""
        w = self._widgets.pop(sender_id, None)
        self._names.pop(sender_id, None)
        
        if w:
            try:
                self._stack.removeWidget(w)
                w.setParent(None)
                w.deleteLater()
            except Exception:
                pass
        
        if self._current_sender_id == sender_id:
            self._current_sender_id = None
            self._stack.setCurrentWidget(self._placeholder)
            self.hide_sender_info_popup()

    @QtCore.pyqtSlot()
    def show_sender_info_popup(self):
        """sender 정보 팝업 표시"""
        sid = self._current_sender_id
        if not sid:
            return
        
        name = self._names.get(sid, sid)
        short_id = sid[:8] if sid else ""
        self._info_popup.set_text(f"Sender: {name}  ({short_id})")
        self._info_popup.show_at_parent_corner(self)

    @QtCore.pyqtSlot()
    def hide_sender_info_popup(self):
        """sender 정보 팝업 숨기기"""
        self._info_popup.hide()