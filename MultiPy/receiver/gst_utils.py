# gst_utils.py
# GStreamer 관련 유틸리티 함수들

import os
import platform
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst

def _make(name):
    """GStreamer 엘리먼트 생성 헬퍼"""
    return Gst.ElementFactory.make(name) if name else None

def _first_available(*names):
    """사용 가능한 첫 번째 GStreamer 엘리먼트 반환"""
    for n in names:
        if Gst.ElementFactory.find(n):
            e = Gst.ElementFactory.make(n)
            if e:
                return e
    return None

def _set_props_if_supported(element, **kwargs):
    """엘리먼트에 지원되는 속성들만 설정"""
    if not element:
        return
    for k, v in kwargs.items():
        try:
            element.set_property(k, v)
        except Exception:
            pass

def get_decoder_and_sink():
    """플랫폼별 HW 디코더와 비디오 싱크 선택"""
    sysname = platform.system().lower()
    decoder, conv, sink = None, None, None

    # 디코더 선택
    if "linux" in sysname:
        if os.path.isfile("/etc/nv_tegra_release"):
            # NVIDIA Jetson
            decoder = _first_available("nvv4l2decoder", "omxh264dec")
            conv = _first_available("nvvidconv", "videoconvert")
        else:
            # 일반 Linux
            decoder = _first_available("vaapih264dec", "v4l2h264dec", "avdec_h264")
            conv = _first_available("videoconvert")
    elif "windows" in sysname:
        decoder = _first_available("d3d11h264dec", "avdec_h264")
        conv = _first_available("d3d11convert", "videoconvert")
    elif "darwin" in sysname:
        decoder = _first_available("vtdec", "avdec_h264")
        conv = _first_available("videoconvert")
    else:
        decoder = _first_available("avdec_h264")
        conv = _first_available("videoconvert")

    # 싱크 선택
    if "windows" in sysname:
        sink = _first_available("d3d11videosink", "autovideosink")
    elif "darwin" in sysname:
        sink = _first_available("avfvideosink", "autovideosink")
    else:
        sink = _first_available("glimagesink", "xvimagesink", "autovideosink")
    
    if sink:
        print(f"[INFO] 비디오 싱크 사용: {sink.get_name()}")
        _set_props_if_supported(sink, force_aspect_ratio=True, fullscreen=False, handle_events=False)

    return decoder, conv, sink