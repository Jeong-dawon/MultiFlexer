from PyQt5 import QtCore, QtWidgets


class Cell(QtWidgets.QFrame):
    clicked = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    def mousePressEvent(self, e):
        self.clicked.emit()

    def put_widget(self, w: QtWidgets.QWidget):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        self._layout.addWidget(w)

    def clear(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
