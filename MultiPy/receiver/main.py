#!/usr/bin/env python3
# main.py
# 메인 실행 파일

import sys
import signal
import gi

# GStreamer 초기화
gi.require_version('Gst', '1.0')
gi.require_version('GstWebRTC', '1.0')
gi.require_version('GstSdp', '1.0')
gi.require_version('GstVideo', '1.0')

from gi.repository import Gst
from PyQt5 import QtWidgets

# 로컬 모듈들
from ui_components import ReceiverWindow
from receiver_manager import MultiReceiverManager
from glib_qt_integration import integrate_glib_into_qt
from view_mode_manager import ViewModeManager 
from mqtt_manager import MqttManager

# GStreamer 초기화
Gst.init(None)

def main():
    """메인 함수"""
    # PyQt5 애플리케이션 초기화
    app = QtWidgets.QApplication(sys.argv)
    
    # UI 윈도우 생성 및 표시
    ui = ReceiverWindow()
    ui.showFullScreen() #FullScreen
    
    ui.activateWindow()
    ui.raise_()
    ui.setFocus()
    
    # GLib와 PyQt5 이벤트 루프 통합
    _glib_timer = integrate_glib_into_qt()
    
    view_manager = ViewModeManager(ui)   

    # manager 생성할 때 mqtt_manager를 먼저 만든 뒤 넘김
    mqtt_manager = MqttManager(receiver_manager=None, view_mode_manager=view_manager)
    manager = MultiReceiverManager(ui, view_manager, mqtt_manager=mqtt_manager)
    manager.start()

    # 양방향 연결 (manager도 mqtt_manager를 알고, mqtt_manager도 manager를 알도록)
    mqtt_manager.receiver_manager = manager
    manager.mqtt_publisher = mqtt_manager
    
    # 종료 핸들러 정의 및 연결
    def _quit(*_):
        try:
            manager.stop()
        except:
            pass
        QtWidgets.QApplication.quit()
    
    ui.quitRequested.connect(_quit)
    app.aboutToQuit.connect(manager.stop)
    signal.signal(signal.SIGINT, _quit)
    signal.signal(signal.SIGTERM, _quit)
    
    # 이벤트 루프 시작
    print("[MAIN] PyQt5 + GStreamer (Overlay) event loop started.")
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()