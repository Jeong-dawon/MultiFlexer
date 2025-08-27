# receiver_gst.py
import argparse
import asyncio
import socketio
import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
from gi.repository import Gst, GstWebRTC

Gst.init(None)

parser = argparse.ArgumentParser()
parser.add_argument('--sender', required=True, help='Sender socket ID')
parser.add_argument('--room', required=True, help='Room password')
parser.add_argument('--signal', default='http://localhost:3001', help='Signaling server URL')
args = parser.parse_args()

SIGNALING_SERVER = args.signal
SENDER_ID = args.sender
ROOM_PASSWORD = args.room
RECEIVER_ID = 'gst-receiver-' + SENDER_ID[:6]

sio = socketio.AsyncClient()
pipeline = None
webrtcbin = None

@sio.event
async def connect():
    print(f"[GST] ✅ signaling 서버 연결됨: {SIGNALING_SERVER}")
    await sio.emit("join-room", {"role": "receiver-gst", "password": ROOM_PASSWORD})

@sio.on("signal")
async def on_signal(data):
    global pipeline, webrtcbin
    if data.get("from") != SENDER_ID:
        return

    typ = data.get("type")
    payload = data.get("payload")

    if typ == "offer":
        sdp_str = payload["sdp"]
        ret, sdpmsg = Gst.SDPMessage.new()
        Gst.SDPMessage.parse_buffer(sdp_str.encode(), sdpmsg)
        desc = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.OFFER, sdpmsg)

        """
        pipeline = Gst.parse_launch(f'''
        webrtcbin name=recv stun-server=stun://stun.l.google.com:19302
        recv. ! application/x-rtp, media=video, encoding-name=H264, payload=96 ! \
            rtph264depay ! h264parse ! nvh264dec ! videoconvert ! autovideosink
        ''')"""

        pipeline_descr = (
            "webrtcbin name=recv stun-server=stun://stun.l.google.com:19302 "
            "recv. ! rtpjitterbuffer "
            "! application/x-rtp,media=video,encoding-name=H264,payload=96 "
            "! rtph264depay "
            "! h264parse "
            "! qsvh264dec low-latency=true "
            "! videoconvert "
            "! autovideosink"
        )
        pipeline = Gst.parse_launch(pipeline_descr)


        
        webrtcbin = pipeline.get_by_name("recv")
        webrtcbin.connect("on-ice-candidate", on_ice_candidate)

        webrtcbin.emit("set-remote-description", desc)

        def on_answer_created(promise, _, __):
            reply = promise.get_reply()
            answer = reply.get_value("answer")
            answer_sdp = answer.sdp.as_text()
            asyncio.ensure_future(sio.emit("signal", {
                "to": SENDER_ID,
                "from": RECEIVER_ID,
                "type": "answer",
                "payload": {
                    "type": "answer",
                    "sdp": answer_sdp
                }
            }))

        promise = Gst.Promise.new_with_change_func(on_answer_created, None, None)
        webrtcbin.emit("create-answer", None, promise)
        pipeline.set_state(Gst.State.PLAYING)

    elif typ == "candidate":
        webrtcbin.emit("add-ice-candidate", payload["sdpMLineIndex"], payload["candidate"])

def on_ice_candidate(_, mlineindex, candidate):
    asyncio.ensure_future(sio.emit("signal", {
        "to": SENDER_ID,
        "from": RECEIVER_ID,
        "type": "candidate",
        "payload": {
            "candidate": candidate,
            "sdpMLineIndex": mlineindex
        }
    }))

async def main():
    await sio.connect(SIGNALING_SERVER)
    await sio.wait()

if __name__ == '__main__':
    asyncio.run(main())

