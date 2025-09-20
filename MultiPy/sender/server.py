import os
import sys
import subprocess
import platform
import signal
import atexit
import tempfile

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO # WebSocket(실시간 통신)을 위한 Flask-SocketIO

# ---------------- Helper ----------------
def resource_path(relative_path):
    """PyInstaller 실행 환경에서도 리소스 파일 찾기"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path) # 일반 실행 시 현재 작업 디렉터리 기준


# ---------------- Flask + SocketIO ----------------
app = Flask(__name__) # Flask 앱 인스턴스 생성
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")  # 세션 암호화 키
socketio = SocketIO(app, cors_allowed_origins="*")

# 관리자 비밀번호
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "319319319") # 관리 패스워드

receiver = None # 현재 등록된 리시버의 소켓 ID
senders = {} # 연결된 sender 목록


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
    # 비번 인증이 안 되면 접근 불가
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
        session["is_admin"] = True # 세션에 관리자 인증 플래그 설정
        return jsonify({"success": True})
    return jsonify({"success": False})


# ---------------- 외부 프로세스 관리 ----------------
receiver_process = None
mosquitto_process = None
signaling_process = None
is_windows = platform.system().lower().startswith("win") # Windows 여부 판단


def start_receiver():
    global receiver_process # 전역 프로세스 핸들 갱신
    recv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../receiver"))
    if is_windows: # 윈도우
        receiver_process = subprocess.Popen(
            ["python", "main.py"],
            cwd=recv_path,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else: # 유닉스
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
    if is_windows: # 윈도우
        signaling_process = subprocess.Popen(
            ["python", "index.py"],
            cwd=base_dir,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else: # 유닉스
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

    if is_windows: # 윈도우
        mosquitto_process = subprocess.Popen(
            [mosq_bin, "-c", tmp_conf],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else: # 유닉스
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
        if proc and proc.poll() is None: # 프로세스가 존재하고 아직 실행 중이면
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

    # Flask 서버까지 완전히 종료
    sys.exit(0)

atexit.register(stop_all) # 인터프리터 종료 시 stop_all을 자동 실행 등록
signal.signal(signal.SIGINT, stop_all) # Ctrl+C(SIGINT) 수신 시 stop_all 실행
signal.signal(signal.SIGTERM, stop_all) # SIGTERM 수신 시 stop_all 실행


# ---------------- Main ----------------
if __name__ == "__main__":
    start_mosquitto()
    start_signaling()
    start_receiver()

    cert_path = resource_path("cert.pem") # HTTPS 인증서 경로
    key_path = resource_path("key.pem") # HTTPS 개인키 경로
    socketio.run(
        app,
        host="0.0.0.0", # 외부 접속 허용
        port=5001,
        debug=False, # 디버그/리로더 비활성화(중복 실행 방지용)
        ssl_context=(cert_path, key_path), # TLS 설정
    )
