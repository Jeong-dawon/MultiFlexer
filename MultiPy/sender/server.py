'''
# receiver까지 동시 실행
import os
import subprocess
import platform
import signal
import atexit
from flask import Flask, render_template

# Flask 앱 생성
app = Flask(__name__)

# Node.js 프로세스 핸들 저장용 변수
node_process = None
# Receiver 프로세스 핸들 저장용 변수
receiver_process = None
# 현재 OS가 Windows인지 여부 체크
is_windows = platform.system().lower().startswith("win")

@app.route('/')
def main():
	return render_template('enter.html')

@app.route('/manage')
def manage():
	return render_template('administrator.html')

@app.route('/share')
def share():
	return render_template('index.html')

def start_node(): # Node.js 시그널링 서버 실행
    global node_process
    base_dir = os.path.dirname(__file__)
    node_path = os.path.abspath(os.path.join(base_dir, "../server"))

    if is_windows: # windows: 새로운 프로세스 그룹으로 실행
        node_process = subprocess.Popen(
            ["node", "index.js"],
            cwd=node_path,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else: # Linux/Mac/Jetson: 새로운 세션/그룹으로 실행
        node_process = subprocess.Popen(
            ["node", "index.js"],
            cwd=node_path,
            preexec_fn=os.setsid
        )
    print(f"[Flask] Node.js signaling server started (PID {node_process.pid})")

def start_receiver(): # Receiver 실행
    global receiver_process
    recv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../receiver"))

    if is_windows: # windows: 새로운 프로세스 그룹으로 실행
        receiver_process = subprocess.Popen(
            ["python", "main.py"],
            cwd=recv_path,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else: # Linux/Mac/Jetson: 새로운 세션/그룹으로 실행
        receiver_process = subprocess.Popen(
            ["python3", "main.py"],
            cwd=recv_path,
            preexec_fn=os.setsid
        )
    print(f"[Flask] Receiver started (PID {receiver_process.pid})")

def stop_all(): # Flask 종료 시 Node.js + Receiver도 같이 종료
    global node_process, receiver_process
    for proc, name in [(node_process, "Node.js"), (receiver_process, "Receiver")]:
        if proc and proc.poll() is None:
            print(f"[Flask] Stopping {name}...")
            try:
                if is_windows: # Windows: CTRL_BREAK_EVENT 신호로 종료
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else: # Linux/Mac/Jetson: 프로세스 그룹 전체에 SIGTERM 보내기
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception as e:
                print(f"[Flask] Error while stopping {name}:", e)

# Flask 종료 시 stop_node() 자동 실행
atexit.register(stop_all)

if __name__ == "__main__":
    # Node.js 서버 실행
    start_node()
    # Receiver 실행
    start_receiver()
    # Flask 서버 실행
    app.run(
        host="0.0.0.0",
        port=5001,
        debug=False, # 프로세스 한 번만 실행
        ssl_context=("cert.pem", "key.pem")
    )
    '''

import os
import sys
import subprocess
import platform
import signal
import atexit
import tempfile
import shutil

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

# ---------------- Helper ----------------
def resource_path(relative_path):
    """PyInstaller 실행 환경에서도 리소스 파일 찾기"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


# ---------------- Flask + SocketIO ----------------
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

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
    return render_template("administrator.html")


@app.route("/share")
def share():
    return render_template("index.html")


# ---------- SocketIO Events ----------
# (이전 시그널링 이벤트 코드 동일) ...


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
    cafile = os.path.join(cert_dir, "ca.pem")
    certfile = os.path.join(cert_dir, "cert.pem")
    keyfile = os.path.join(cert_dir, "key.pem")

    # 임시 conf 파일 생성 (placeholder 치환)
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
                    proc.wait(timeout=5)  # 5초 기다림
                except Exception:
                    proc.kill()           # 안 죽으면 강제 종료
                    print(f"[Flask] {name} force killed.")

# 정상 종료 시 실행
atexit.register(stop_all)

# 시그널 핸들러 등록 (Ctrl+C, kill 처리)
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

