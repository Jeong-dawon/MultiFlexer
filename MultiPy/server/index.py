import os
from flask import Flask, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

receiver = None
senders = {}  # sender_id -> {id, name}


# ---------- Helper ----------
def emit_sender_list():
    global receiver, senders
    if receiver:
        sender_arr = [{"id": s["id"], "name": s["name"]} for s in senders.values()]
        socketio.emit("sender-list", sender_arr, to=receiver)


# ---------- Socket Events ----------
@socketio.on("share-request")
def handle_share_request(data):
    to = data.get("to")
    emit("share-request", {"from": request.sid}, to=to)


@socketio.on("share-started")
def handle_share_started(data):
    global receiver
    if not receiver:
        return
    sender_info = senders.get(request.sid, {})
    display_name = sender_info.get("name") or data.get("name") or f"Sender-{request.sid[:5]}"
    emit("sender-share-started", {"id": request.sid, "name": display_name}, to=receiver)
    emit_sender_list()


@socketio.on("sender-share-stopped")
def handle_sender_stopped():
    global receiver
    if receiver:
        emit("sender-share-stopped", {"id": request.sid}, to=receiver)


@socketio.on("del-room")
def handle_del_room(data):
    global receiver, senders
    if data.get("role") == "receiver":
        for sender_id in list(senders.keys()):
            emit("room-deleted", to=sender_id)
        receiver = None
        senders.clear()


@socketio.on("join-room")
def handle_join_room(data):
    """
    Flask-SocketIO에서는 서버 핸들러가 return 값을 주면
    클라이언트 emit 의 ack(callback) 함수로 전달됨.
    """
    global receiver, senders
    role = data.get("role")
    name = data.get("name")

    if role == "receiver":
        receiver = request.sid
        emit_sender_list()
        return {"success": True}

    # sender
    if not receiver:
        return {"success": False, "message": "리시버가 없습니다."}

    if any(s["name"] == name for s in senders.values()):
        return {"success": False, "message": "이미 사용 중인 이름입니다."}

    assigned_name = name or f"Sender-{request.sid[:5]}"
    senders[request.sid] = {"id": request.sid, "name": assigned_name}

    emit_sender_list()
    emit("joined-room", {"name": assigned_name}, to=request.sid)
    emit("join-complete", {"name": assigned_name}, to=request.sid)

    return {"success": True, "name": assigned_name}


@socketio.on("signal")
def handle_signal(data):
    global receiver, senders
    data = data or {}
    data["from"] = request.sid

    if request.sid in senders:  # sender
        if receiver:
            data["to"] = receiver
            emit("signal", data, to=receiver)
    elif request.sid == receiver:  # receiver
        target = data.get("to")
        if target and target in senders:
            emit("signal", data, to=target)


@socketio.on("disconnect")
def handle_disconnect():
    global receiver, senders
    if request.sid in senders:  # sender out
        del senders[request.sid]
        if receiver:
            emit("sender-disconnected", {"id": request.sid}, to=receiver)
            emit_sender_list()
    elif request.sid == receiver:  # receiver out
        for sender_id in list(senders.keys()):
            emit("room-deleted", to=sender_id)
        receiver = None
        senders.clear()


# ---------- Start Server ----------
if __name__ == "__main__":
    base_dir = os.path.dirname(__file__)
    sender_dir = os.path.join(base_dir, "../sender")

    cert_path = os.path.abspath(os.path.join(sender_dir, "cert.pem"))
    key_path = os.path.abspath(os.path.join(sender_dir, "key.pem"))

    socketio.run(
        app,
        host="0.0.0.0",
        port=3001,
        ssl_context=(cert_path, key_path)
    )
