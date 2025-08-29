# glib_qt_integration.py
# GLib와 PyQt5 이벤트 루프 통합

from PyQt5 import QtCore
from gi.repository import GLib
from config import GLIB_TIMER_INTERVAL_MS


def integrate_glib_into_qt():
    """GLib 이벤트 루프를 PyQt5에 통합"""
    ctx = GLib.MainContext.default()
    timer = QtCore.QTimer()
    timer.setInterval(GLIB_TIMER_INTERVAL_MS)
    timer.timeout.connect(lambda: ctx.iteration(False))
    timer.start()
    return timer