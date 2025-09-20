from PyQt5 import QtCore, QtWidgets, QtGui

class LandingManager(QtCore.QObject):
    def __init__(self, ui, receiver_manager):
        super().__init__()
        self.ui = ui
        self.receiver_manager = receiver_manager
        self.ui.centralWidget().setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0   rgba(240, 248, 255, 255),
                    stop:0.5 rgba(230, 240, 250, 255),
                    stop:1   rgba(250, 245, 250, 255)
                );
            }
        """)



        # 상태표시 관련 속성
        self._dots = 0
        self._fade_dir = 1
        self._status_opacity_effect = None
        self._landing_status = None
        self._status_timer = None

        # 랜딩 카드 UI를 여기서 직접 생성
        self._landing = self._build_landing_card()
        self.ui._stack.addWidget(self._landing)
        self.ui._stack.setCurrentWidget(self._landing)
        

    # =================== 상태 업데이트 ===================
    def update_state(self):
        """현재 sender 상태에 따라 화면 모드 전환"""
        senders = self.receiver_manager.get_all_senders()
        active_senders = self.receiver_manager.get_active_senders()

        if not senders:
            self.enter_landing_mode()
        elif senders and not active_senders:
            self.enter_sender_mode()
    

    # =================== 모드 전환 ===================
    def enter_sender_mode(self):
        self.ui._main.setCurrentIndex(0)
        if self._landing_status:
            self._landing_status.setText("접속자 선택 대기 중")
            self._landing_status.setStyleSheet("color: black;")
        if self._status_timer:
            self._status_timer.stop()
        self.ui.setStyleSheet("QMainWindow { background: #f5f5f5; }")

    def enter_landing_mode(self):
        self.ui._main.setCurrentIndex(0)
        if self._landing_status:
            self._landing_status.setText("· · ·   접속자 대기 중   · · ·")
            self._landing_status.setStyleSheet("color: #6b7280;")
        if self._status_timer:
            self._status_timer.start(600)
        self.ui.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0   rgba(4,210,175,13),
                    stop:0.5 rgba(96,170,255,13),
                    stop:1   rgba(255,143,107,13)
                );
            }
        """)

    def reset_to_landing(self):
        """모든 sender 제거 후 랜딩 화면으로 전환"""
        for sid, w in list(self.ui._widgets.items()):
            self.ui._stack.removeWidget(w)
            w.setParent(None)
            w.deleteLater()
        self.ui._widgets.clear()
        self.ui._names.clear()
        self.ui._current_sender_id = None

        self.ui._stack.setCurrentWidget(self._landing)
        self.ui._first_sender_connected = False
        self.ui.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0   rgba(240, 248, 255, 255),
                    stop:0.5 rgba(230, 240, 250, 255),
                    stop:1   rgba(250, 245, 250, 255)
                );
            }
        """)




        if self._landing_status:
            self._landing_status.setText("· · ·   접속자 대기 중   · · ·")
            self._landing_status.setStyleSheet("color: #6b7280;")

        print("[DEBUG] 초기화면(랜딩)으로 복귀 완료")

    # =================== 랜딩 카드 ===================
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
            QLabel {
                    background: transparent;   /* 라벨 배경 투명화 */
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

        eff = QtWidgets.QGraphicsOpacityEffect(self._landing_status)
        self._landing_status.setGraphicsEffect(eff)
        self._status_opacity_effect = eff

        timer = QtCore.QTimer(self)
        timer.timeout.connect(self._tick)
        timer.start(600)
        self._status_timer = timer

        return wrapper

    def _tick(self):
        self._dots = (self._dots + 1) % 4
        dots = "· " * self._dots
        self._landing_status.setText(f"{dots}접속자 대기 중{dots}".center(17, " "))

        if self._status_opacity_effect:
            cur = self._status_opacity_effect.opacity()
            step = 0.15 * self._fade_dir
            new = cur + step
            if new >= 1.0:
                new, self._fade_dir = 1.0, -1
            elif new <= 0.5:
                new, self._fade_dir = 0.5, +1
            self._status_opacity_effect.setOpacity(new)
