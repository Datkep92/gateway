"""Khôi phục sang máy mới: xuất file ở máy A, nhập vào máy B sạch rồi dùng ngay.

    python3 Tests/backup_restore_test.py

Máy A và máy B là hai CSDL riêng, chạy lần lượt trong cùng tiến trình. Máy B
nhập file khi chưa có client key nào (đúng cảnh máy mới) và sau đó phải:
  - vào được bảng điều khiển bằng key trong file, key cũ của máy B hết hiệu lực
  - giữ nguyên API key thượng nguồn, model, cài đặt cổng
  - gọi được model qua thượng nguồn giả
  - báo cần khởi động lại vì cổng trong file khác cổng đang nghe
Không đụng tới data/gateway.db thật: mỗi máy dùng một thư mục tạm.
"""
import http.client, http.server, json, os, shutil, sys, tempfile, threading, time
import urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import db, server  # noqa: E402

MOCK_PORT = 15921
A_PORT = 15194          # cổng máy A đang nghe lúc xuất file
B_PORT = 15196          # cổng máy B đang nghe lúc nhập file
CUR_PORT = A_PORT       # cổng của máy đang được thử
FILE_PORT = 15195       # cổng ghi trong file -> máy B phải khởi động lại
UPSTREAM = "http://127.0.0.1:%d/v1" % MOCK_PORT
UPSTREAM_ROOT = "http://127.0.0.1:%d" % MOCK_PORT  # dạng được lưu sau khi thiết lập
UPSTREAM_CALLS = []
RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "   %s" % (extra,)))


# ---------------------------------------------------------------- thượng nguồn giả
class MockUpstream(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._send(200, {"object": "list", "data": [
                {"id": "restore-mini", "object": "model"},
                {"id": "restore-pro", "object": "model"}]})
        return self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        UPSTREAM_CALLS.append({"auth": self.headers.get("Authorization", ""),
                               "body": raw.decode("utf-8", "replace")})
        return self._send(200, {"id": "chatcmpl-restore", "object": "chat.completion",
                                "model": "restore-mini",
                                "choices": [{"index": 0, "finish_reason": "stop",
                                             "message": {"role": "assistant", "content": "hello-restored"}}],
                                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


def start_mock():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockUpstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- gọi HTTP
def call(method, path, body=None, key=None, port=None, raw=None):
    port = port or CUR_PORT
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def jcall(method, path, body=None, key=None, port=None, raw=None):
    code, text = call(method, path, body, key, port, raw)
    try:
        return code, json.loads(text)
    except Exception:
        return code, {"_raw": text}


def use_db(path):
    db.DATA_DIR = os.path.dirname(path)
    db.DB_PATH = path


def serve(port):
    global CUR_PORT
    CUR_PORT = port
    httpd = server.make_server("127.0.0.1", port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.4)
    return httpd


# ---------------------------------------------------------------- máy A
def machine_a():
    use_db(os.path.join(A_DIR, "gateway.db"))
    db.init_db()
    db.set_setting("port", str(FILE_PORT))
    db.set_setting("rate_limit_rpm", "777")
    db.set_setting("log_body_enabled", "1")
    db.set_setting("log_retention_days", "11")
    httpd = serve(A_PORT)

    code, r = jcall("POST", "/gateway/onboard",
                    {"provider": "OpenAI", "api_key": "sk-A-12345", "base_url": UPSTREAM, "free_only": True})
    check("máy A thiết lập một bước được", code == 200 and r.get("ok"), r)
    ckey = r.get("client_key") or ""
    db.new_client_key("phu")
    code, text = call("GET", "/gateway/config/export", key=ckey)
    check("máy A xuất được file", code == 200 and '"gateway-backup"' in text, code)
    with open(os.path.join(A_DIR, "backup.json"), "w") as f:
        f.write(text)
    httpd.shutdown()
    time.sleep(0.2)
    return text, ckey


# ---------------------------------------------------------------- máy B
def machine_b(backup_text, ckey_a):
    use_db(os.path.join(B_DIR, "gateway.db"))
    db.init_db()
    db.set_setting("port", str(B_PORT))
    _, key_b = db.new_client_key("default")
    httpd = serve(B_PORT)

    code, st = jcall("GET", "/gateway/status", key=key_b)
    check("máy B mới chạy được bằng key của chính nó", code == 200, (code, st))
    code, _ = jcall("GET", "/gateway/status", key=ckey_a)
    check("key của máy A chưa dùng được ở máy B", code == 401, code)

    # dán file, không kèm Authorization: đúng cảnh máy mới chưa có key nào dùng được
    code, imp = jcall("POST", "/gateway/config/import", raw=backup_text.encode(), key=None)
    check("nhập file trên máy mới -> 200", code == 200 and imp.get("ok"), (code, imp))
    check("nhập xong đưa luôn key trong file",
          imp.get("client_key") == ckey_a and imp.get("client_key_source") == "file",
          (imp.get("client_key_source"), imp.get("error")))
    check("file có key khác cổng đang nghe -> báo khởi động lại", imp.get("restart_needed") is True,
          imp.get("restart_needed"))
    check("đếm được những gì đã khôi phục",
          (imp.get("counts") or {}).get("providers", 0) >= 1 and (imp.get("counts") or {}).get("api_keys", 0) >= 1
          and (imp.get("counts") or {}).get("models", 0) >= 2 and (imp.get("counts") or {}).get("settings", 0) >= 10,
          imp.get("counts"))

    code, st = jcall("GET", "/gateway/status", key=ckey_a)
    check("vào bảng điều khiển được bằng key trong file", code == 200, (code, st))
    code, _ = jcall("GET", "/gateway/status", key=key_b)
    check("key cũ của máy B đã bị thay", code == 401, code)

    code, cfg = jcall("GET", "/gateway/settings", key=ckey_a)
    s = (cfg.get("settings") or {}) if isinstance(cfg, dict) else {}
    check("cài đặt cổng khôi phục nguyên", code == 200 and str(s.get("port")) == str(FILE_PORT)
          and str(s.get("rate_limit_rpm")) == "777" and str(s.get("log_retention_days")) == "11",
          {k: s.get(k) for k in ("port", "rate_limit_rpm", "log_retention_days")})
    check("bật ghi nhật ký thân yêu cầu được khôi phục", str(s.get("log_body_enabled")) == "1",
          s.get("log_body_enabled"))

    code, pv = jcall("GET", "/gateway/providers", key=ckey_a)
    ps = [p for p in (pv.get("providers") or []) if p.get("name") == "openai"]
    check("provider và địa chỉ riêng khôi phục nguyên",
          code == 200 and len(ps) == 1 and ps[0].get("base_url") == UPSTREAM_ROOT
          and ps[0].get("key_count") == 1 and ps[0].get("connected") is True, ps)

    code, ms = jcall("GET", "/v1/models", key=ckey_a)
    ids = sorted(m["id"] for m in (ms.get("data") or []))
    check("model miễn phí bật lại, model trả tiền vẫn tắt", code == 200 and ids == ["restore-mini"], ids)

    UPSTREAM_CALLS.clear()
    code, out = jcall("POST", "/v1/chat/completions",
                      {"model": "restore-mini", "messages": [{"role": "user", "content": "chao"}]}, key=ckey_a)
    check("gọi được model sau khi khôi phục",
          code == 200 and (out.get("choices") or [{}])[0].get("message", {}).get("content") == "hello-restored",
          out)
    check("key thượng nguồn khôi phục nguyên văn",
          bool(UPSTREAM_CALLS) and UPSTREAM_CALLS[0]["auth"] == "Bearer sk-A-12345",
          UPSTREAM_CALLS[:1])

    code, again = jcall("POST", "/gateway/config/import", raw=backup_text.encode())
    check("nhập lại lần hai vẫn ổn", code == 200 and again.get("ok") and again.get("client_key") == ckey_a, again)

    code, bad = jcall("POST", "/gateway/config/import", raw=b"khong-phai-json", key=ckey_a)
    check("file rác -> 400", code == 400, (code, bad))
    code, bad2 = jcall("POST", "/gateway/config/import", {"format": "gateway-backup", "version": 99}, key=ckey_a)
    check("file sai phiên bản -> 400", code == 400, (code, bad2))
    code, bad3 = jcall("POST", "/gateway/config/import", {"format": "gateway-backup", "version": 1}, key=ckey_a)
    check("file thiếu nội dung -> 400", code == 400, (code, bad3))
    check("nhập file rác không làm mất cấu hình", jcall("GET", "/v1/models", key=ckey_a)[0] == 200)

    # thân quá lớn: máy chủ phải từ chối trước khi đọc
    try:
        conn = http.client.HTTPConnection("127.0.0.1", B_PORT, timeout=10)
        conn.putrequest("POST", "/gateway/config/import")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(9 * 1024 * 1024))
        conn.endheaders()
        big = conn.getresponse()
        code_big = big.status
        big.read()
        conn.close()
    except Exception as e:
        code_big = "loi: %s" % e
    check("thân quá lớn -> 413", code_big == 413, code_big)

    # nút khởi động lại: chặn việc thay tiến trình để không giết chính bài thử
    called = []
    real = server.reexec_process
    server.reexec_process = lambda: called.append(1)
    try:
        code, rs = jcall("POST", "/gateway/restart", {}, key=ckey_a)
        time.sleep(1.2)
    finally:
        server.reexec_process = real
    check("nút khởi động lại trả địa chỉ mới", code == 200 and rs.get("restarting") is True
          and str(rs.get("port")) == str(FILE_PORT) and rs.get("host") == "127.0.0.1", (code, rs))
    check("nút khởi động lại thật sự dựng lại tiến trình", called == [1], called)

    httpd.shutdown()
    time.sleep(0.2)


A_DIR = tempfile.mkdtemp(prefix="gwbackup-a-")
B_DIR = tempfile.mkdtemp(prefix="gwbackup-b-")
mock = None
try:
    mock = start_mock()
    text, ckey_a = machine_a()
    machine_b(text, ckey_a)
finally:
    if mock:
        mock.shutdown()
    shutil.rmtree(A_DIR, ignore_errors=True)
    shutil.rmtree(B_DIR, ignore_errors=True)

failed = [r for r in RESULTS if not r[1]]
print("\n%d/%d kiểm tra đạt" % (len(RESULTS) - len(failed), len(RESULTS)))
if failed:
    print("Lỗi:")
    for name, _ in failed:
        print(" - " + name)
sys.exit(1 if failed else 0)
