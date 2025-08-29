# node.js 동시 실행 버전(별도 프로세스)
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
# 현재 OS가 Windows인지 여부 체크
is_windows = platform.system().lower().startswith("win")

@app.route('/')
def home():
    return render_template('index.html') # 기본 라우트: sender UI 반환

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

    print(f"[Flask] Node.js signaling server started (PID: {node_process.pid})")

def stop_node(): # Flask 종료 시 Node.js 프로세스도 같이 종료
    global node_process
    if node_process and node_process.poll() is None:
        print("[Flask] Stopping Node.js signaling server...")
        try:
            if is_windows: # Windows: CTRL_BREAK_EVENT 신호로 종료
                node_process.send_signal(signal.CTRL_BREAK_EVENT)
            else: # Linux/Mac/Jetson: 프로세스 그룹 전체에 SIGTERM 보내기
                os.killpg(os.getpgid(node_process.pid), signal.SIGTERM)
        except Exception as e:
            print("[Flask] Error while stopping Node.js:", e)

# Flask 종료 시 stop_node() 자동 실행
atexit.register(stop_node)

if __name__ == '__main__':
    # Node.js 서버 실행
    start_node()
    # Flask 서버 실행
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True, # 코드 변경 감지를 위해 프로세스 두 번 실행
        use_reloader=False, # 중복 실행 방지
        ssl_context=("cert.pem", "key.pem")
    )