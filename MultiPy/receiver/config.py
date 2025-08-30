# config.py
# 전역 설정 값들을 관리하는 모듈

import ssl

# 서버 설정
SIGNALING_URL = "https://192.168.0.54:3001"
RECEIVER_NAME = "Receiver-1"

# SSL 설정
ssl._create_default_https_context = ssl._create_unverified_context

# UI 설정
WINDOW_TITLE = "WebRTC Receiver"
DEFAULT_WINDOW_SIZE = (1280, 720)
INFO_POPUP_MARGIN = 16
SWITCH_COOLDOWN_MS = 150

# GStreamer 설정
STUN_SERVER = "stun://stun.l.google.com:19302"
GST_VIDEO_CAPS = ("application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,"
                  "payload=102,packetization-mode=(string)1,profile-level-id=(string)42e01f")

# 타이머 설정
GLIB_TIMER_INTERVAL_MS = 5
UI_OVERLAY_DELAY_MS = 50
ICE_STATE_CHECK_DELAY_MS = 800