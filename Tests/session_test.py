"""Bộ chạy cổng theo phiên: chạy thật `gateway-session` rồi kiểm nó tắt theo cha.

    python3 Tests/session_test.py

Không đụng `data/gateway.db` thật: mỗi ca dựng một thư mục dự án tạm với
`main.py` giả (một HTTP server nhỏ trả 200, ghi pid ra `main.pid`) rồi chạy
`packaging/gateway-session.in` — `@PREFIX@` thay bằng gốc bootstrap thật của
máy — với `GATEWAY_PROJECT_DIR` trỏ vào thư mục tạm, `GATEWAY_PROBE_SECS=1`.

Kiểm:
  1. cha bị SIGKILL  -> phiên tự tắt, `main.py` không còn, không mồ côi
  2. TERM vào phiên  -> dừng `main.py` rồi thoát 0
  3. `standalone`     -> cha chết vẫn sống (chạy tay không theo phiên Apelin)
  4. sau tất cả       -> không còn tiến trình nào nhắc tới thư mục tạm

Chạy tuần tự từng ca; mỗi ca tự dọn tiến trình trước khi sang ca sau.
"""
import http.server
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond), str(extra)[:200]))
    line = ("PASS  " if cond else "FAIL  ") + name
    if str(extra) != "":
        line += "   [%s]" % str(extra)[:200]
    print(line)


# --------------------------------------------------------------------------
# môi trường: shell, gốc bootstrap
# --------------------------------------------------------------------------
def find_shell():
    for p in ("/var/jb/bin/sh", "/var/jb/bin/bash", "/bin/sh", "/usr/bin/sh"):
        if os.path.exists(p):
            return p
    return shutil.which("sh") or ""


SH = find_shell()

# Gốc bootstrap: rootless là /var/jb, roothide là rỗng. Lấy theo nơi có python3.
PREFIX = "/var/jb" if os.path.exists("/var/jb/usr/bin/python3") else ""
CURL = os.path.join(PREFIX, "usr/bin/curl")
if not os.path.exists(CURL):
    CURL = shutil.which("curl") or ""

FAKE_MAIN = '''\
"""main.py gia cho Tests/session_test.py: mot HTTP server tra 200."""
import http.server
import os

PORT = int(os.environ.get("FAKE_PORT", "15999"))
with open("main.pid", "w") as f:
    f.write(str(os.getpid()))
print("Gateway starting on 127.0.0.1:%d" % PORT, flush=True)
http.server.ThreadingHTTPServer(("127.0.0.1", PORT),
                                http.server.SimpleHTTPRequestHandler).serve_forever()
'''

PARENT_DRIVER = '''\
"""Cha gia: chay gateway-session lam con roi ngu, de bai kiem giet cha."""
import os
import subprocess
import sys
import time

env = dict(os.environ)
env.pop("GATEWAY_PARENT_PID", None)
p = subprocess.Popen([sys.argv[1], sys.argv[2]] + sys.argv[3:], env=env)
print(p.pid, flush=True)
time.sleep(600)
'''


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def session_script(tmp):
    """packaging/gateway-session.in với @PREFIX@ đã thay, để chạy thẳng được."""
    src = os.path.join(ROOT, "packaging", "gateway-session.in")
    with open(src, "r", encoding="utf-8") as f:
        text = f.read()
    text = text.replace("@PREFIX@", PREFIX)
    out = os.path.join(tmp, "gateway-session")
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(out, 0o755)
    return out


def make_project(tmp, name, port):
    proj = os.path.join(tmp, name)
    os.makedirs(proj)
    with open(os.path.join(proj, "main.py"), "w", encoding="utf-8") as f:
        f.write(FAKE_MAIN)
    env = dict(os.environ)
    env["GATEWAY_PROJECT_DIR"] = proj
    env["GATEWAY_PROBE_SECS"] = "1"
    env["GATEWAY_FAIL_START"] = "100"      # đừng khởi động lại vì chậm khởi động
    env["GATEWAY_FAIL_HUNG"] = "100"
    env["FAKE_PORT"] = str(port)
    env.pop("GATEWAY_STANDALONE", None)
    return proj, env


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError, TypeError, OverflowError):
        return True
    return True


def read_pid(proj):
    path = os.path.join(proj, "main.pid")
    for _ in range(100):
        if os.path.exists(path):
            with open(path) as f:
                t = f.read().strip()
            if t.isdigit():
                return int(t)
        time.sleep(0.1)
    return 0


def port_answers(port, timeout=1.0):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def wait_for(fn, timeout, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(step)
    return fn()


def wait_port(port, timeout=20):
    return wait_for(lambda: port_answers(port), timeout)


def ps_lines():
    for argv in (["ps", "-A", "-o", "pid=,command="],
                 ["ps", "-ax", "-o", "pid=,command="],
                 ["ps", "-ef"]):
        try:
            p = subprocess.run(argv, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if p.returncode == 0:
            return p.stdout.decode("utf-8", "replace").splitlines()
    return []


def pids_matching(text):
    out = []
    for line in ps_lines():
        if text in line:
            tok = line.strip().split(None, 1)[0]
            if tok.isdigit():
                out.append(int(tok))
    return out


def kill_all(pids):
    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            pass


def read_log(proj):
    path = os.path.join(proj, "run.log")
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# --------------------------------------------------------------------------
# các ca
# --------------------------------------------------------------------------
def case_parent_killed(tmp, sh, sess, parent_py):
    """Cha bị SIGKILL: phiên phải tự tắt, con không được mồ côi."""
    port = free_port()
    proj, env = make_project(tmp, "a", port)
    parent = subprocess.Popen([sys.executable, parent_py, sh, sess],
                              stdout=subprocess.PIPE, text=True, env=env)
    try:
        line = parent.stdout.readline().strip()
        spid = int(line) if line.isdigit() else 0
        check("A: dựng được phiên dưới một cha giả", alive(spid), spid)
        up = wait_port(port, 25)
        mpid = read_pid(proj)
        check("A: phiên chạy main.py và cổng trả lời 200", up and alive(mpid), (up, mpid))

        parent.kill()
        parent.wait(timeout=10)
        t0 = time.time()
        gone = wait_for(lambda: not alive(spid) and not alive(mpid), 8)
        dt = time.time() - t0
        check("A: cha bị giết -> phiên tự tắt và main.py dừng", gone, "%.1fs" % dt)
        check("A: tắt trong vòng 5 giây", gone and dt <= 5, "%.1fs" % dt)
        log = read_log(proj)
        check("A: run.log ghi phiên bắt đầu", "Gateway session: session start" in log)
        check("A: run.log ghi lý do cha mất", "cha đã mất" in log)
        check("A: cổng đã đóng", not port_answers(port, 1.0))
    finally:
        kill_all([pid for pid in (locals().get("spid", 0), locals().get("mpid", 0)) if pid])
        kill_all(pids_matching(proj))


def case_term(tmp, sh, sess):
    """TERM vào phiên: dừng con rồi thoát 0."""
    port = free_port()
    proj, env = make_project(tmp, "b", port)
    env["GATEWAY_PARENT_PID"] = str(os.getpid())   # cha là chính bài kiểm, còn sống
    proc = subprocess.Popen([sh, sess], env=env)
    try:
        up = wait_port(port, 25)
        mpid = read_pid(proj)
        check("B: phiên chạy và cổng trả lời 200", up and alive(mpid), (up, mpid))
        proc.send_signal(signal.SIGTERM)
        try:
            rc = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            rc = None
        check("B: TERM -> phiên thoát 0", rc == 0, rc)
        check("B: TERM -> main.py cũng dừng", not alive(mpid), mpid)
        check("B: không còn tiến trình nào của thư mục này",
              not pids_matching(proj), pids_matching(proj))
        log = read_log(proj)
        check("B: run.log ghi 'session stop'", "session stop" in log)
    finally:
        kill_all([locals().get("mpid", 0)])
        kill_all([proc.pid])
        kill_all(pids_matching(proj))


def case_standalone(tmp, sh, sess, parent_py):
    """Chạy tay (standalone): không theo dõi cha, cha chết vẫn sống."""
    port = free_port()
    proj, env = make_project(tmp, "c", port)
    parent = subprocess.Popen([sys.executable, parent_py, sh, sess, "standalone"],
                              stdout=subprocess.PIPE, text=True, env=env)
    spid = 0
    try:
        line = parent.stdout.readline().strip()
        spid = int(line) if line.isdigit() else 0
        up = wait_port(port, 25)
        mpid = read_pid(proj)
        check("C: standalone chạy được", alive(spid) and up, (spid, up))
        parent.kill()
        parent.wait(timeout=10)
        time.sleep(4)              # quá vài nhịp dò cổng
        check("C: cha chết vẫn còn sống", alive(spid) and alive(mpid), (spid, mpid))
        check("C: cổng vẫn trả lời 200", port_answers(port, 2.0))
        os.kill(spid, signal.SIGTERM)
        gone = wait_for(lambda: not alive(spid) and not alive(mpid), 15)
        check("C: TERM vẫn dừng được phiên chạy tay", gone, (spid, mpid))
    finally:
        kill_all([spid])
        kill_all([pid for pid in (locals().get("mpid", 0),) if pid])
        kill_all(pids_matching(proj))


def main():
    if not SH:
        print("Không tìm thấy shell (/var/jb/bin/sh) — bỏ qua.")
        return 0
    if not os.path.exists(CURL):
        print("Không thấy curl (dò cổng không chạy được) — bỏ qua.")
        return 0

    tmp = tempfile.mkdtemp(prefix="gwsess-")
    try:
        sess = session_script(tmp)
        parent_py = os.path.join(tmp, "parent.py")
        with open(parent_py, "w", encoding="utf-8") as f:
            f.write(PARENT_DRIVER)

        case_parent_killed(tmp, SH, sess, parent_py)
        case_term(tmp, SH, sess)
        case_standalone(tmp, SH, sess, parent_py)

        leftovers = pids_matching(tmp)
        check("D: không còn tiến trình nào nhắc tới thư mục tạm", not leftovers, leftovers)
    finally:
        leftover = pids_matching(tmp)
        kill_all(leftover)
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print("\n%d/%d kiểm tra đạt" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("Lỗi:")
        for name, _, extra in failed:
            print(" - %s | %s" % (name, extra))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
