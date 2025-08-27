# -*- coding: utf-8 -*-
"""
PyQt 기반 UI 컴포넌트 정의 파일
================================================
- GStreamer/WebRTC와 직접 연결되는 로직은 없음
- 단순히 UI 요소, 레이아웃, Drag&Drop, 시그널 브리지만 포함
- 실제 스트리밍 처리(네트워크/디코딩)는 gstreamer.py(main)에서 담당
"""

from PyQt5 import QtCore, QtGui, QtWidgets

# ============================================================
# 브릿지: 소켓 스레드 → Qt 메인 스레드 안전 전달용
# ============================================================
class EventBridge(QtCore.QObject):
    """
    GStreamer / Socket 이벤트 → PyQt 메인 스레드로 안전하게 전달하는 브릿지 클래스.
    
    Qt의 GUI 객체는 반드시 메인 스레드에서만 안전하게 다뤄야 함.
    따라서 GStreamer/WebRTC 스레드에서 발생한 이벤트를 
    pyqtSignal로 포장해서 메인 스레드로 emit.
    """
    sender_list     = QtCore.pyqtSignal(list)          # 전체 송신자 목록 갱신 [ {id,name}, ... ]
    remove_sender   = QtCore.pyqtSignal(str)           # 특정 송신자 제거 (senderId)
    share_started   = QtCore.pyqtSignal(str, str)      # 화면 공유 시작됨 (senderId, senderName)
    answer          = QtCore.pyqtSignal(str, str)      # WebRTC answer 수신 (from, sdp_text)
    candidate       = QtCore.pyqtSignal(str, int, str) # WebRTC candidate 수신 (from, mline, candidate)


# ============================================================
# VideoWidget: GStreamer sink와 연결되는 실제 출력 영역
# ============================================================
class VideoWidget(QtWidgets.QWidget):
    """
    GStreamer sink(VideoOverlay)와 연결될 QWidget.
    - QWidget은 윈도우 핸들(ID)을 가지므로 GStreamer가 직접 그 위에 영상을 그릴 수 있음.
    - GStreamer 파이프라인의 "appsink"나 "xvimagesink" 같은 element와 연결 가능.
    """

    # Args:
        #parent: 부모 위젯
        #title: 툴팁으로 표시할 제목 (보통 송신자 이름)
        #thumbnail: True이면 작은 썸네일 용도로 사용됨
    def __init__(self, parent=None, title=None, thumbnail=False):

        super().__init__(parent)

        # GStreamer와 연결하려면 반드시 NativeWindow 플래그 필요
        self.setAttribute(QtCore.Qt.WA_NativeWindow)
        self.setAutoFillBackground(False)

        # 크기 지정 (썸네일은 작게, 일반은 크게)
        self.setMinimumSize(160, 90 if thumbnail else 240)

        # 배경색은 기본 검정색
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QtGui.QColor("black"))
        self.setPalette(pal)

        # 마우스 오버 시 툴팁으로 이름 보이게
        if title:
            self.setToolTip(title)

    # PyQt의 winId() → 정수형으로 반환
    # GStreamer의 overlay set_window_handle()에 넘길 때 필요.
    def winid_int(self):
        wid = int(self.winId() or 0)
        return wid


# ============================================================
# SnapFrame: 여러 VideoWidget을 격자 배치하는 컨테이너
# ============================================================
class SnapFrame(QtWidgets.QFrame):
    """
    여러 VideoWidget을 drag & drop으로 원하는 위치에 배치할 수 있는 컨테이너.
    - 예: 화면 분할 (좌/우, 2x2, 3분할, 전체화면 등)
    - drop 이벤트를 받아 sender_id와 position을 외부로 시그널 발행.
    """

    # 시그널: sender_id를 특정 position에 배치
    dropped = QtCore.pyqtSignal(str, str)  # (sender_id, position)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True) # Drag & Drop 허용
        self.setStyleSheet("background:#111; border-radius:18px;")
        self.setMinimumHeight(560)

        # 미리 정의된 레이아웃 포지션 (비율 단위)
        self.positions = {
            "full":       QtCore.QRectF(0, 0, 1.0, 1.0),
            "left":       QtCore.QRectF(0, 0, 0.5, 1.0),
            "right":      QtCore.QRectF(0.5, 0, 0.5, 1.0),
            "small-left": QtCore.QRectF(0, 0, 1/3, 1.0),
            "big-right":  QtCore.QRectF(1/3, 0, 2/3, 1.0),
            "topleft":    QtCore.QRectF(0, 0, 0.5, 0.5),
            "topright":   QtCore.QRectF(0.5, 0, 0.5, 0.5),
            "bottomleft": QtCore.QRectF(0, 0.5, 0.5, 0.5),
            "bottomright":QtCore.QRectF(0.5, 0.5, 0.5, 0.5),
        }

        # 마우스 hover 시 현재 포지션 저장
        self.hover_pos = None

        # 실제 배치된 VideoWidget 정보 저장 { position: (sender_id, widget) }
        self._assigned = {}  # position -> (sender_id, VideoWidget)

    # 마우스 좌표 → 해당하는 position 이름 반환.
    # 비율 좌표(x,y)를 구한 뒤 self.positions의 rect 안에 포함되는지 검사.
    def _pos_from_point(self, pos: QtCore.QPoint):
        w, h = self.width(), self.height()
        x, y = pos.x()/max(1,w), pos.y()/max(1,h)
        for name, r in self.positions.items():
            if r.contains(x, y):
                return name
        return None

    # UI 다시 그리기: 격자선 + hover 위치 강조
    def paintEvent(self, e):
        super().paintEvent(e)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        # 가이드 라인 (흰색, 반투명)
        pen = QtGui.QPen(QtGui.QColor(255,255,255,60), 2, QtCore.Qt.SolidLine)
        p.setPen(pen)

        # 전체 위치들에 대해 가이드 박스 그리기
        for name, rect in self.positions.items():
            rr = QtCore.QRectF(rect.x()*self.width(), rect.y()*self.height(),
                               rect.width()*self.width(), rect.height()*self.height())
            p.drawRoundedRect(rr, 12, 12)

        # Hover 시 강조 표시
        if self.hover_pos:
            r = self.positions[self.hover_pos]
            rr = QtCore.QRectF(r.x()*self.width(), r.y()*self.height(),
                               r.width()*self.width(), r.height()*self.height())
            p.fillRect(rr, QtGui.QColor(4,210,175,60)) # 청록색 반투명

    # Drag 시작 시 MIME 타입 체크
    def dragEnterEvent(self, e: QtGui.QDragEnterEvent):
        if e.mimeData().hasFormat("application/x-sender-id"):
            e.acceptProposedAction()
        else:
            e.ignore()

    # Drag 이동 중 → hover 포지션 업데이트
    def dragMoveEvent(self, e):
        self.hover_pos = self._pos_from_point(e.pos())
        self.update()

    # Drag 영역 벗어나면 hover 해제
    def dragLeaveEvent(self, e):
        self.hover_pos = None
        self.update()

    # Drop 발생 시 sender_id + 위치를 시그널로 전달.
    # 외부에서는 dropped(sid, pos)로 받아서 assign_widget 호출 가능.
    def dropEvent(self, e: QtGui.QDropEvent):
        sid = str(e.mimeData().data("application/x-sender-id"), "utf-8")
        pos = self._pos_from_point(e.pos())
        self.hover_pos = None
        self.update()
        if sid and pos:
            self.dropped.emit(sid, pos)

    # 특정 위치(position)에 VideoWidget 배치.
    # 기존에 그 위치에 다른 위젯이 있으면 제거 후 교체.
    def assign_widget(self, sender_id: str, widget: VideoWidget, position: str):
        for pos, (sid, w) in list(self._assigned.items()):
            if pos == position:
                w.setParent(None)
                w.deleteLater()
                del self._assigned[pos]

        # 지정된 비율 → 실제 픽셀 좌표 변환
        r = self.positions[position]
        ww = int(r.width()*self.width())
        hh = int(r.height()*self.height())
        xx = int(r.x()*self.width())
        yy = int(r.y()*self.height())

        # VideoWidget 배치
        widget.setParent(self)
        widget.setGeometry(xx, yy, ww, hh)
        widget.show()

        # 기록
        self._assigned[position] = (sender_id, widget)


# ============================================================
# DraggableThumb: (현재 미사용) 드래그 가능한 썸네일
# ============================================================
class DraggableThumb(QtWidgets.QFrame):
    """
    송신자별 썸네일을 Drag & Drop 할 수 있도록 디자인된 컴포넌트.
    - 현재는 사용하지 않음 (TODO: 필요시 구현).
    - SnapFrame과 연동해서 drag source로 활용 가능.
    """

    def __init__(self, sender_id: str, title: str):
        super().__init__()
        self.sender_id = sender_id

        # 썸네일 디자인
        self.setStyleSheet("background:#d1d5db; border-radius:10px;")
        self.setFixedSize(220, 130)

        # 내부 레이아웃 (Video + Label)
        lay = QtWidgets.QVBoxLayout(self); 
        lay.setContentsMargins(8,8,8,8); l
        ay.setSpacing(4)

        # 실제 비디오 영역 (썸네일 모드)
        self.video = VideoWidget(thumbnail=True)

        # 이름 라벨 (송신자 이름)
        name = QtWidgets.QLabel(title)
        name.setStyleSheet("background:rgba(0,0,0,0.4); color:#fff; padding:2px 6px; border-radius:4px;")
        
        lay.addWidget(self.video, 1); 
        lay.addWidget(name, 0, QtCore.Qt.AlignLeft)

    def mousePressEvent(self, e: QtGui.QMouseEvent):
        """
        Drag 시작 로직 (현재 미구현).
        보통 QDrag 객체를 만들어서 application/x-sender-id MIME 타입 설정 후 exec_() 호출.
        """

        pass  # 미사용
