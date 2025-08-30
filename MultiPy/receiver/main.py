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
    ui.show()
    
    ui.activateWindow()
    ui.raise_()
    ui.setFocus()
    
    # GLib와 PyQt5 이벤트 루프 통합
    _glib_timer = integrate_glib_into_qt()
    
    view_manager = ViewModeManager(ui)   

    # MultiReceiverManager 생성 및 시작
    manager = MultiReceiverManager(ui, view_manager)
    manager.start()
    
    # Mqtt - MultiReceiverManager 양방향 연결
    mqtt_manager = MqttManager(receiver_manager=manager, ip="localhost", port=1883)
    manager.mqtt_publisher = mqtt_manager  # MQTT 클라이언트 설정
    
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