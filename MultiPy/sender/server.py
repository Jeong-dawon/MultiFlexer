import os
import sys
import subprocess
import platform
import signal
import atexit
import tempfile

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO

# ---------------- Helper ----------------
def resource_path(relative_path):
    """PyInstaller 실행 환경에서도 리소스 파일 찾기"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


# ---------------- Flask + SocketIO ----------------
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")  # 세션 암호화 키
socketio = SocketIO(app, cors_allowed_origins="*")

# 관리자 비밀번호 (환경변수에서 불러오기 권장)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "319319319")

receiver = None
senders = {}


def emit_sender_list():
    global receiver, senders
    if receiver:
        sender_arr = [{"id": s["id"], "name": s["name"]} for s in senders.values()]
        socketio.emit("sender-list", sender_arr, to=receiver)


@app.route("/")
def main():
    return render_template("enter.html")


@app.route("/manage")
def manage():
    # 비번 인증이 안 되었으면 접근 불가
    if not session.get("is_admin"):
        return redirect(url_for("main"))
    return render_template("administrator.html")


@app.route("/share")
def share():
    return render_template("index.html")


@app.route("/check_admin", methods=["POST"])
def check_admin():
    data = request.get_json()
    if not data:
        return jsonify({"success": False}), 400

    password = data.get("password")
    if password == ADMIN_PASSWORD:
        session["is_admin"] = True
        return jsonify({"success": True})
    return jsonify({"success": False})


# ---------------- 외부 프로세스 관리 ----------------
receiver_process = None
mosquitto_process = None
signaling_process = None
is_windows = platform.system().lower().startswith("win")


def start_receiver():
    global receiver_process
    recv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../receiver"))
    if is_windows:
        receiver_process = subprocess.Popen(
            ["python", "main.py"],
            cwd=recv_path,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        receiver_process = subprocess.Popen(
            ["python3", "main.py"],
            cwd=recv_path,
            preexec_fn=os.setsid,
        )
    print(f"[Flask] Receiver started (PID {receiver_process.pid})")


def start_signaling():
    """index.py 시그널링 서버 실행"""
    global signaling_process
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../server"))
    if is_windows:
        signaling_process = subprocess.Popen(
            ["python", "index.py"],
            cwd=base_dir,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        signaling_process = subprocess.Popen(
            ["python3", "index.py"],
            cwd=base_dir,
            preexec_fn=os.setsid,
        )
    print(f"[Flask] Signaling server started (PID {signaling_process.pid})")


def start_mosquitto():
    global mosquitto_process

    # 원본 mosquitto.conf
    conf_template = resource_path("mosquitto.conf")

    # 실행 파일 안에 들어 있는 인증서들
    cert_dir = resource_path("certs")

    with open(conf_template, "r", encoding="utf-8") as f:
        conf_data = f.read()
    conf_data = conf_data.replace("CERT_DIR", cert_dir)

    tmp_conf = os.path.join(tempfile.gettempdir(), "mosquitto_runtime.conf")
    with open(tmp_conf, "w", encoding="utf-8") as f:
        f.write(conf_data)

    mosq_bin = resource_path("mosquitto.exe" if is_windows else "mosquitto")

    if is_windows:
        mosquitto_process = subprocess.Popen(
            [mosq_bin, "-c", tmp_conf],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        mosquitto_process = subprocess.Popen(
            [mosq_bin, "-c", tmp_conf],
            preexec_fn=os.setsid,
        )
    print(f"[Flask] Mosquitto started (PID {mosquitto_process.pid})")


import sys

def stop_all(*args):
    global receiver_process, mosquitto_process, signaling_process
    for proc, name in [
        (receiver_process, "Receiver"),
        (mosquitto_process, "Mosquitto"),
        (signaling_process, "Signaling"),
    ]:
        if proc and proc.poll() is None:
            print(f"[Flask] Stopping {name} (PID {proc.pid})...")
            try:
                if is_windows:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception as e:
                print(f"[Flask] Error while stopping {name}: {e}")
            finally:
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
                    print(f"[Flask] {name} force killed.")

    # 👉 Flask 서버까지 완전히 종료
    sys.exit(0)

atexit.register(stop_all)
signal.signal(signal.SIGINT, stop_all)
signal.signal(signal.SIGTERM, stop_all)


# ---------------- Main ----------------
if __name__ == "__main__":
    start_mosquitto()
    start_signaling()
    start_receiver()

    cert_path = resource_path("cert.pem")
    key_path = resource_path("key.pem")
    socketio.run(
        app,
        host="0.0.0.0",
        port=5001,
        debug=False,
        ssl_context=(cert_path, key_path),
    )
