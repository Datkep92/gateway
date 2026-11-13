"""Kiểm thử vòng tự kiểm key & model, chạy hoàn toàn trên máy, không cần mạng.

Dựng một "nhà cung cấp giả" nói giao thức OpenAI với ba model:
mock-mini-good (chạy tốt), mock-mini-dead (404 model không tồn tại),
mock-mini-flaky (429 lỗi tạm thời); và một key sai sk-bad (401) có thể bật/tắt
lỗi bằng cờ STATE["bad_ok"]. Rồi gọi thật qua HTTP + gọi thẳng vòng tự kiểm:

    python3 Tests/autocheck_test.py

Kiểm: một vòng tự kiểm kết luận đúng từng loại lỗi; model lỗi tạm thời chưa bị
loại và được hẹn thử lại; key 401 tự tắt rồi tự bật lại khi nhà cung cấp sống
lại, và không làm mất model tốt; key người dùng tự tắt thì để yên; chế độ ẩn
model chưa kiểm; vòng nền tự chạy và tỉnh ngay khi có việc mới.
"""
import http.server
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import db  # noqa: E402

# dùng CSDL riêng, không đụng tới gateway đang chạy
db.DATA_DIR = tempfile.mkdtemp(prefix="gwauto-")
db.DB_PATH = os.path.join(db.DATA_DIR, "gateway.db")

import autocheck  # noqa: E402
import keypool  # noqa: E402
import proxy  # noqa: E402
import server  # noqa: E402

GATEWAY_PORT = 15193
MOCK_PORT = 15931
GOOD = "mock-mini-good"      # tên chứa "mini" nên được coi là model free
DEAD = "mock-mini-dead"
FLAKY = "mock-mini-flaky"
STATE = {"bad_ok": False, "calls": []}
RESULTS = []


# ---------------- nhà cung cấp giả ----------------
class MockOpenAI(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _key(self):
        a = self.headers.get("Authorization", "")
        return a[7:] if a.startswith("Bearer ") else a

    def do_GET(self):
        if self._key() == "sk-bad" and not STATE["bad_ok"]:
            self._json(401, {"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}})
            return
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": GOOD}, {"id": DEAD}, {"id": FLAKY}]})
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8", "replace") or "{}")
        except Exception:
            body = {}
        key = self._key()
        model = body.get("model") or ""
        STATE["calls"].append((key, model))
        if key == "sk-bad" and not STATE["bad_ok"]:
            self._json(401, {"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}})
            return
        if model == DEAD:
            self._json(404, {"error": {"message": "The model `%s` does not exist." % DEAD, "code": "model_not_found"}})
            return
        if model == FLAKY:
            self._json(429, {"error": {"message": "Rate limit reached for requests", "type": "rate_limit_error"}})
            return
        self._json(200, {"id": "chatcmpl-1", "object": "chat.completion", "model": model,
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": "hi"}}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


def start_mock(port, handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------- gọi HTTP ----------------
def call(method, path, body=None, key=None, port=GATEWAY_PORT):
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        return resp.getcode(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def jcall(method, path, body=None, key=None):
    code, text = call(method, path, body, key)
    try:
        return code, json.loads(text or "{}")
    except Exception:
        return code, {"__raw": text}


def check(name, cond, extra=None):
    RESULTS.append((name, bool(cond), extra))
    print("%s %s" % ("ok  " if cond else "FAIL", name))


# ---------------- đọc thẳng CSDL ----------------
def model_row(mid):
    c = db.conn()
    r = c.execute("SELECT health,last_test_message,retry_at FROM gateway_models WHERE id=?", (mid,)).fetchone()
    c.close()
    return dict(r) if r else {}


def key_row(kid):
    c = db.conn()
    r = c.execute("SELECT * FROM api_keys WHERE id=?", (kid,)).fetchone()
    c.close()
    return dict(r) if r else {}


def key_id_of(value):
    c = db.conn()
    r = c.execute("SELECT id FROM api_keys WHERE key_value=?", (value,)).fetchone()
    c.close()
    return r["id"] if r else None


def retry_in(mid):
    r = model_row(mid)
    if r.get("retry_at") is None:
        return None
    return round(r["retry_at"] - db.now())


def wait_busy_false(secs=40, key=None):
    end = time.time() + secs
    while time.time() < end:
        code, st = jcall("GET", "/gateway/auto/status", key=key)
        if code == 200 and not st.get("busy"):
            return st
        time.sleep(0.2)
    return None


def main():
    start_mock(MOCK_PORT, MockOpenAI)
    db.init_db()
    db.set_setting("port", str(GATEWAY_PORT))
    db.set_setting("rate_limit_rpm", "600")
    db.set_setting("rate_limit_burst", "200")
    db.set_setting("auto_check", "0")      # vòng nền tắt; bài thử tự gọi từng vòng
    httpd = server.make_server("127.0.0.1", GATEWAY_PORT)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)
    MOCK = "http://127.0.0.1:%d" % MOCK_PORT

    # ---- 1. chỉ thêm nhà cung cấp: model chưa ai kiểm ----
    code, r = jcall("POST", "/gateway/onboard", {"provider": "OpenAI", "api_key": "sk-good", "base_url": MOCK})
    check("thêm nhà cung cấp -> 200", code == 200 and r.get("ok"), r)
    ckey = r.get("client_key")
    ids = sorted(m["id"] for m in (r.get("models") or []))
    check("dò đủ 3 model giả", ids == [DEAD, FLAKY, GOOD], ids)

    code, st = jcall("GET", "/gateway/auto/status", key=ckey)
    check("xem trạng thái tự kiểm -> 200", code == 200, code)
    check("báo đúng số model đang chờ kiểm", st.get("pending_models") == 3, st.get("pending_models"))
    check("vòng nền tắt theo cài đặt", st.get("enabled") is False, st.get("enabled"))
    check("báo chu kỳ kiểm", st.get("period") == 300, st.get("period"))

    # ---- 2. một vòng: ba loại lỗi khác nhau ----
    res = autocheck.run_once(force=True, batch=10)
    check("vòng kiểm đi hết 3 model", res and res.get("models") == 3, res)
    check("chỉ 1 model dùng được", res and res.get("models_ok") == 1, res)
    check("model tốt -> ok", model_row(GOOD).get("health") == "ok", model_row(GOOD))
    check("model không tồn tại -> hỏng", model_row(DEAD).get("health") == "fail", model_row(DEAD))
    check("ghi rõ lý do model hỏng", "does not exist" in (model_row(DEAD).get("last_test_message") or ""),
          model_row(DEAD).get("last_test_message"))
    check("model lỗi tạm thời (429) chưa bị loại",
          model_row(FLAKY).get("health") in (None, "unknown"), model_row(FLAKY))
    check("model lỗi tạm thời được hẹn thử lại sớm (60 s)", retry_in(FLAKY) is not None and 0 < retry_in(FLAKY) <= 120,
          retry_in(FLAKY))
    check("model tốt được hẹn kiểm lại sau (6 giờ)", retry_in(GOOD) is not None and 3000 < retry_in(GOOD) <= 21600,
          retry_in(GOOD))
    krow = key_row(key_id_of("sk-good"))
    check("lỗi tạm thời của một model không làm key bị nghỉ oan",
          (krow.get("failure_count") or 0) == 0 and not krow.get("cooldown_until")
          and krow.get("health") != "fail", krow)

    # ---- 3. vòng sau: không còn việc tới hạn thì không gọi thượng nguồn nữa ----
    check("xong một vòng thì hàng chờ sạch", db.pending_model_count() == 0, db.pending_model_count())
    res = autocheck.run_once()
    check("vòng không ép không kiểm lại model vừa kiểm", res and res.get("models") == 0, res)

    # ---- 4. nút "kiểm ngay" ----
    code, r = jcall("POST", "/gateway/auto/check", {"force": True}, key=ckey)
    check("kiểm ngay -> 200 và bắt đầu chạy", code == 200 and r.get("started") is True, r)
    st = wait_busy_false(40, ckey)
    check("kiểm ngay chạy xong", st is not None, autocheck.status())
    check("có kiểm lại model", st and st.get("last_models") == 3, st.get("last_models") if st else None)
    check("có ghi lại kết quả vòng vừa chạy", st and "kiểm" in (st.get("last_message") or ""),
          st.get("last_message") if st else None)

    # ---- 5. key sai (401): tự tắt, không làm mất model tốt ----
    kid_good = key_id_of("sk-good")
    kid_bad = keypool.add_key("openai", "key lỗi", "sk-bad", -1)
    # chỉ để key sai lại: model đang tốt bị thử bằng key sai
    keypool.set_enabled(kid_good, False)
    ok, msg = proxy.test_model(GOOD)
    check("thử model bằng key sai -> không đạt", ok is False, msg)
    row = key_row(kid_bad)
    check("key 401 tự tắt", row.get("enabled") == 0, row)
    check("key bị tắt là do hệ thống (còn tự bật lại được)", row.get("auto_disabled") == 1, row)
    check("key 401 được hẹn thử lại", (row.get("retry_at") or 0) > db.now(), row)
    check("một key sai không làm model tốt bị loại", model_row(GOOD).get("health") == "ok", model_row(GOOD))
    check("model vừa gặp key sai được hẹn kiểm lại sớm (60 s)",
          retry_in(GOOD) is not None and 0 < retry_in(GOOD) <= 120, retry_in(GOOD))
    # cả vòng đầy đủ: key sai bị gạt, các model còn kiểm được bằng key tốt
    keypool.set_enabled(kid_good, True)
    res = autocheck.run_once(force=True, batch=10)
    check("vòng đầy đủ vẫn kiểm xong model", res and res.get("models") == 3, res)
    check("key sai vẫn nằm ngoài vòng dùng key", key_row(kid_bad).get("enabled") == 0, key_row(kid_bad))
    check("model tốt vẫn tốt sau khi gặp key sai", model_row(GOOD).get("health") == "ok", model_row(GOOD))

    # ---- 6. nhà cung cấp sống lại: key tự bật lại, model được hẹn kiểm lại ----
    STATE["bad_ok"] = True
    res = autocheck.run_once(force=True, batch=10)
    row = key_row(kid_bad)
    check("key sống lại thì được bật lại", row.get("enabled") == 1 and row.get("auto_disabled") == 0, row)
    check("bật lại thì xóa hẹn thử lại", row.get("retry_at") is None, row)
    check("bật lại thì hẹn kiểm lại model của nhà cung cấp", db.pending_model_count() > 0,
          db.pending_model_count())

    # ---- 7. key người dùng tự tắt thì không ai tự bật ----
    keypool.set_enabled(kid_bad, False)
    row = key_row(kid_bad)
    check("tắt tay thì mất cờ tự động", row.get("auto_disabled") == 0, row)
    autocheck.run_once(force=True, batch=1, key_batch=10)
    row = key_row(kid_bad)
    check("tắt tay thì vòng tự kiểm để yên", row.get("enabled") == 0 and row.get("auto_disabled") == 0, row)
    check("không đưa key người dùng tắt vào hàng thử lại",
          kid_bad not in [k["id"] for k in keypool.due_keys(50, force=True)], None)
    keypool.set_enabled(kid_bad, True)

    # ---- 8. ẩn model chưa kiểm thấy dùng được ----
    jcall("POST", "/gateway/settings", {"hide_unverified": "1"}, key=ckey)
    code, ml = jcall("GET", "/v1/models", key=ckey)
    ids = sorted(m["id"] for m in (ml.get("data") or []))
    check("bật ẩn thì chỉ còn model đã kiểm thấy dùng được", code == 200 and ids == [GOOD], ids)
    jcall("POST", "/gateway/settings", {"hide_unverified": "0"}, key=ckey)
    code, ml = jcall("GET", "/v1/models", key=ckey)
    ids = sorted(m["id"] for m in (ml.get("data") or []))
    check("tắt ẩn thì hiện lại model chưa kiểm, vẫn ẩn model đã kết luận hỏng",
          code == 200 and ids == [FLAKY, GOOD], ids)

    # ---- 9. vòng nền: tự chạy, nghỉ dài khi hết việc, tỉnh ngay khi có việc ----
    db.set_setting("auto_check", "1")
    db.set_setting("auto_check_secs", "300")
    autocheck.run_once(force=True, batch=24, key_batch=24)   # dọn hết việc tồn trước khi bật vòng nền
    check("bật được vòng nền", autocheck.start() is True, None)
    check("gọi bật lần hai không tạo vòng thứ hai", autocheck.start() is False, None)
    end = time.time() + 20
    while time.time() < end and autocheck.STATE["rounds"] < 1:
        time.sleep(0.2)
    check("vòng nền chạy vòng đầu tiên", autocheck.STATE["rounds"] >= 1, autocheck.STATE)
    end = time.time() + 6            # để vòng nền xử lý nốt yêu cầu đánh thức cũ rồi ngủ dài
    while time.time() < end and (autocheck.STATE["next_at"] - time.time()) <= 30:
        time.sleep(0.2)
    r0 = autocheck.STATE["rounds"]
    left = autocheck.STATE["next_at"] - time.time()
    check("hết việc thì vòng nền nghỉ dài", left > 30, left)
    code, added = jcall("POST", "/gateway/keys", {"provider": "OpenAI", "label": "key 2", "key": "sk-good-2"}, key=ckey)
    check("thêm key -> 200", code == 200 and added.get("id"), added)
    end = time.time() + 10
    while time.time() < end and autocheck.STATE["rounds"] < r0 + 1:
        time.sleep(0.2)
    check("có việc mới thì vòng nền tỉnh ngay", autocheck.STATE["rounds"] >= r0 + 1, autocheck.STATE)
    autocheck.stop(5)
    check("dừng được vòng nền", autocheck.STATE["running"] is False, autocheck.STATE)

    httpd.shutdown()
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
