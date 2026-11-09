"""Kiểm thử đầu-cuối cho Gateway, chạy hoàn toàn trên máy, không cần mạng.

Dựng hai "nhà cung cấp giả" (một nói giao thức OpenAI, một nói giao thức
Anthropic), trỏ cấu hình gateway vào đó rồi gọi thật qua HTTP:

    python3 Tests/e2e_test.py

Kiểm: thiết lập một bước (/gateway/onboard), dò model, chính sách chỉ model
free, định tuyến theo tên model, dịch hai chiều OpenAI <-> Anthropic (cả
luồng thường lẫn SSE), và quyền truy cập (client key, chỉ từ máy này).
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
db.DATA_DIR = tempfile.mkdtemp(prefix="gwtest-")
db.DB_PATH = os.path.join(db.DATA_DIR, "gateway.db")

import providers  # noqa: E402
import server  # noqa: E402

GATEWAY_PORT = 15198
MOCK_OPENAI_PORT = 15901
MOCK_ANTHROPIC_PORT = 15902
STATE = {"openai": [], "anthropic": []}
RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond), str(extra)[:200]))
    print(("PASS  " if cond else "FAIL  ") + name + ("   " + str(extra)[:200] if not cond and extra else ""))


# ---------------- nhà cung cấp giả ----------------
class MockOpenAI(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json({"object": "list", "data": [
                {"id": "mock-gpt-4o-mini"}, {"id": "mock-gpt-4o"}, {"id": "mock-claude-x"},
                {"id": "mock-text-embedding-nano"}]})
            return
        self._json({"error": {"message": "not found"}}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        body["__auth"] = self.headers.get("Authorization", "")
        STATE["openai"].append(body)
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for piece in ("hello-", "from-", "openai"):
                chunk = {"id": "cc1", "object": "chat.completion.chunk", "model": body.get("model"),
                         "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                self.wfile.write(("data: %s\n\n" % json.dumps(chunk)).encode())
                self.wfile.flush()
            end = {"id": "cc1", "object": "chat.completion.chunk", "model": body.get("model"),
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(("data: %s\n\ndata: [DONE]\n\n" % json.dumps(end)).encode())
            self.wfile.flush()
            self.close_connection = True
            return
        self._json({"id": "cc1", "object": "chat.completion", "created": 1, "model": body.get("model"),
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "hello-from-openai"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})


class MockAnthropic(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json({"data": [{"id": "claude-3-5-haiku-20241022"}, {"id": "claude-3-5-sonnet-20241022"}]})
            return
        self._json({"error": {"message": "not found"}}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        body["__auth"] = self.headers.get("x-api-key", "")
        STATE["anthropic"].append(body)
        model = body.get("model")
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            events = [
                ("message_start", {"type": "message_start", "message": {
                    "id": "msg_1", "type": "message", "role": "assistant", "model": model,
                    "content": [], "stop_reason": None,
                    "usage": {"input_tokens": 4, "output_tokens": 0}}}),
                ("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": "hello-from-anthropic"}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta",
                                   "delta": {"stop_reason": "end_turn"},
                                   "usage": {"output_tokens": 4}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            for name, payload in events:
                self.wfile.write(("event: %s\ndata: %s\n\n" % (name, json.dumps(payload))).encode())
                self.wfile.flush()
            self.close_connection = True
            return
        self._json({"id": "msg_1", "type": "message", "role": "assistant", "model": model,
                    "content": [{"type": "text", "text": "hello-from-anthropic"}],
                    "stop_reason": "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": 4, "output_tokens": 3}})


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
        resp = urllib.request.urlopen(req, timeout=30)
        return resp.getcode(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def jcall(method, path, body=None, key=None, port=GATEWAY_PORT):
    code, text = call(method, path, body, key, port)
    try:
        return code, json.loads(text or "{}")
    except Exception:
        return code, {"__raw": text}


def main():
    start_mock(MOCK_OPENAI_PORT, MockOpenAI)
    start_mock(MOCK_ANTHROPIC_PORT, MockAnthropic)
    db.init_db()
    db.set_setting("port", str(GATEWAY_PORT))
    db.set_setting("rate_limit_rpm", "600")
    db.set_setting("rate_limit_burst", "200")
    httpd = server.make_server("127.0.0.1", GATEWAY_PORT)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)
    OA = "http://127.0.0.1:%d" % MOCK_OPENAI_PORT
    AN = "http://127.0.0.1:%d" % MOCK_ANTHROPIC_PORT

    # ---- 1. thiết lập một bước ----
    code, r = jcall("POST", "/gateway/onboard",
                    {"provider": "OpenAI", "api_key": "sk-test-openai", "base_url": OA})
    check("onboard openai -> 200", code == 200 and r.get("ok"), r)
    check("onboard dò được model thật", "mock-gpt-4o-mini" in [m["id"] for m in r.get("models", [])], r.get("verify_error"))
    check("onboard xác nhận key sống", r.get("verify_ok") is True, r.get("verify_error"))
    check("onboard tạo client key", bool(r.get("client_key")), r.get("has_client_key"))
    ckey = r.get("client_key")
    check("model mặc định là model trò chuyện, không phải model nhúng",
          r.get("default_model") == "mock-gpt-4o-mini", r.get("default_model"))

    code, r2 = jcall("POST", "/gateway/onboard",
                     {"provider": "Claude", "api_key": "sk-ant-test", "base_url": AN})
    check("onboard claude -> 200", code == 200 and r2.get("ok"), r2)
    check("claude giữ đúng base_url riêng", r2.get("base_url") == AN, r2.get("base_url"))
    check("onboard lần hai cấp thêm client key dùng được",
          bool(r2.get("client_key")) and r2.get("client_key") != ckey, r2.get("has_client_key"))
    code, _ = jcall("GET", "/v1/models", key=ckey)
    check("key cấp lần đầu vẫn dùng được sau đó", code == 200, code)

    code, r3 = jcall("POST", "/gateway/onboard", {"provider": "khong-co", "api_key": "x"})
    check("onboard provider lạ -> 400", code == 400, code)
    code, r4 = jcall("POST", "/gateway/onboard", {"provider": "openai", "api_key": "  "})
    check("onboard thiếu key -> 400", code == 400, code)

    # ---- 2. cổng vào /v1 ----
    code, _ = jcall("GET", "/v1/models")
    check("/v1/models không key -> 401", code == 401, code)
    code, models = jcall("GET", "/v1/models", key=ckey)
    ids = [m["id"] for m in models.get("data", [])]
    check("/v1/models có key -> 200", code == 200, code)
    check("chỉ bật model free (mini/haiku)", "mock-gpt-4o-mini" in ids and "claude-3-5-haiku-20241022" in ids, ids)
    check("model đắt bị tắt khi chỉ dùng free", "mock-gpt-4o" not in ids and "claude-3-5-sonnet-20241022" not in ids, ids)

    # ---- 3. định tuyến + dịch: OpenAI vào, Claude thượng nguồn ----
    code, out = jcall("POST", "/v1/chat/completions",
                      {"model": "mock-gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}, key=ckey)
    check("chat model openai -> 200", code == 200, out)
    check("chat trả nội dung đúng", out.get("choices", [{}])[0].get("message", {}).get("content") == "hello-from-openai", out)
    check("chat dùng đúng key OpenAI", any(c["__auth"].startswith("Bearer sk-test-openai") for c in STATE["openai"]), STATE["openai"][-1].get("__auth"))

    before = len(STATE["anthropic"])
    code, out = jcall("POST", "/v1/chat/completions",
                      {"model": "claude-3-5-haiku-20241022",
                       "messages": [{"role": "system", "content": "be nice"},
                                    {"role": "user", "content": "hi"}],
                       "max_tokens": 50, "temperature": 0.2}, key=ckey)
    check("chat model claude -> 200", code == 200, out)
    check("chat dịch Claude -> OpenAI", out.get("object") == "chat.completion"
          and out.get("choices", [{}])[0].get("message", {}).get("content") == "hello-from-anthropic", out)
    check("usage được dịch sang dạng OpenAI", "prompt_tokens" in out.get("usage", {}) and "completion_tokens" in out.get("usage", {}), out.get("usage"))
    sent = STATE["anthropic"][before] if len(STATE["anthropic"]) > before else {}
    check("yêu cầu gửi Claude đúng giao thức (system riêng, có max_tokens)",
          sent.get("system") == "be nice" and sent.get("max_tokens") == 50 and "messages" in sent and "choices" not in sent, sent)
    check("Claude nhận đúng key riêng", str(sent.get("__auth", "")).startswith("sk-ant-"), sent.get("__auth"))

    code, text = call("POST", "/v1/chat/completions",
                      {"model": "claude-3-5-haiku-20241022", "stream": True,
                       "messages": [{"role": "user", "content": "hi"}]}, key=ckey)
    check("chat stream claude -> 200", code == 200, text[:120])
    check("luồng Claude dịch thành luồng OpenAI", "hello-from-anthropic" in text and "[DONE]" in text, text[-200:])

    code, text = call("POST", "/v1/chat/completions",
                      {"model": "mock-gpt-4o-mini", "stream": True,
                       "messages": [{"role": "user", "content": "hi"}]}, key=ckey)
    check("luồng OpenAI đi thẳng, không dịch",
          '"content": "hello-"' in text and "[DONE]" in text, text[-200:])

    # ---- 4. định tuyến + dịch: Claude vào, OpenAI thượng nguồn ----
    before = len(STATE["openai"])
    code, out = jcall("POST", "/v1/messages",
                      {"model": "mock-gpt-4o-mini", "max_tokens": 40, "system": "be brief",
                       "messages": [{"role": "user", "content": "hi"}]}, key=ckey)
    check("messages model openai -> 200", code == 200, out)
    check("messages dịch OpenAI -> Claude", out.get("type") == "message"
          and out.get("content", [{}])[0].get("text") == "hello-from-openai", out)
    check("stop_reason được dịch", out.get("stop_reason") == "end_turn", out.get("stop_reason"))
    sent = STATE["openai"][before] if len(STATE["openai"]) > before else {}
    check("yêu cầu gửi OpenAI đúng dạng chat/completions",
          [m.get("role") for m in sent.get("messages", [])] == ["system", "user"],
          sent.get("messages"))

    code, text = call("POST", "/v1/messages",
                      {"model": "mock-gpt-4o-mini", "max_tokens": 40, "stream": True,
                       "messages": [{"role": "user", "content": "hi"}]}, key=ckey)
    check("messages stream openai -> 200", code == 200, text[:120])
    check("luồng OpenAI dịch thành luồng Claude",
          "event: message_start" in text and "content_block_delta" in text and "message_stop" in text, text[-300:])

    code, out = jcall("POST", "/v1/messages",
                      {"model": "claude-3-5-haiku-20241022", "max_tokens": 40,
                       "messages": [{"role": "user", "content": "hi"}]}, key=ckey)
    check("messages model claude đi thẳng", code == 200 and out.get("content", [{}])[0].get("text") == "hello-from-anthropic", out)

    # ---- 4b. client không cần gửi tên model ----
    code, out = jcall("POST", "/v1/chat/completions",
                      {"messages": [{"role": "user", "content": "hi"}]}, key=ckey)
    check("chat thiếu model -> 200", code == 200, out)
    check("chat thiếu model dùng model mặc định",
          out.get("choices", [{}])[0].get("message", {}).get("content") == "hello-from-anthropic", out)
    code, out = jcall("POST", "/v1/messages",
                      {"max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]}, key=ckey)
    check("messages thiếu model -> 200", code == 200, out)

    # ---- 5. quản trị ----
    code, _ = jcall("GET", "/gateway/status")
    check("/gateway/status không key -> 401", code == 401, code)
    code, st = jcall("GET", "/gateway/status", key=ckey)
    check("/gateway/status có key -> 200", code == 200 and st.get("status") == "running", st)
    code, pv = jcall("GET", "/gateway/providers", key=ckey)
    cmap = {p["name"]: p for p in pv.get("providers", [])}
    check("/gateway/providers -> 200", code == 200, code)
    check("provider báo đã kết nối", cmap.get("openai", {}).get("connected") and cmap.get("anthropic", {}).get("connected"), cmap)
    check("provider báo đúng số model đang bật", cmap.get("openai", {}).get("model_count") == 2 and cmap.get("anthropic", {}).get("model_count") == 1, cmap)

    code, pol = jcall("POST", "/gateway/models/policy", {"free_only": False}, key=ckey)
    check("tắt chế độ chỉ free -> bật thêm model", code == 200 and pol.get("enabled", 0) > 2, pol)
    code, models = jcall("GET", "/v1/models", key=ckey)
    ids = [m["id"] for m in models.get("data", [])]
    check("model đắt xuất hiện khi tắt chế độ free", "mock-gpt-4o" in ids and "claude-3-5-sonnet-20241022" in ids, ids)

    code, dis = jcall("POST", "/gateway/providers/discover", {"provider": "deepseek"}, key=ckey)
    check("dò model provider chưa có key -> 502", code == 502, dis)

    code, lg = jcall("GET", "/gateway/logs?limit=200", key=ckey)
    logs = lg.get("logs", [])
    check("nhật ký ghi đúng provider theo model",
          any(l.get("model") == "claude-3-5-haiku-20241022" and l.get("provider") == "anthropic" for l in logs)
          and any(l.get("model") == "mock-gpt-4o-mini" and l.get("provider") == "openai" for l in logs),
          [(l.get("provider"), l.get("model")) for l in logs[:6]])

    code, _ = jcall("GET", "/", port=GATEWAY_PORT)
    check("trang chủ trả HTML", code == 200, code)

    # ---- 6. thiết lập một bước: chọn chế độ chỉ-free ngay trong lúc dán key ----
    code, r5 = jcall("POST", "/gateway/onboard",
                     {"provider": "DeepSeek", "api_key": "sk-test-deepseek",
                      "base_url": OA, "free_only": False})
    check("onboard deepseek -> 200", code == 200 and r5.get("ok"), r5)
    check("onboard đặt được chế độ tất cả model", r5.get("free_only") is False, r5.get("free_only"))
    check("onboard trả model mặc định để client khỏi gửi tên model", bool(r5.get("default_model")), r5.get("default_model"))
    check("model mặc định không phải model nhúng", "embed" not in (r5.get("default_model") or ""), r5.get("default_model"))
    code, r6 = jcall("POST", "/gateway/onboard",
                     {"provider": "DeepSeek", "api_key": "sk-test-deepseek", "base_url": OA, "free_only": True})
    check("onboard bật lại chế độ chỉ free", code == 200 and r6.get("free_only") is True, r6.get("free_only"))

    # ---- 7. sao lưu / khôi phục ----
    code, before_models = jcall("GET", "/v1/models", key=ckey)
    before_ids = sorted(m["id"] for m in before_models.get("data", []))
    code, _ = jcall("GET", "/gateway/config/export")
    check("xuất sao lưu không key -> 401", code == 401, code)
    code, text = call("GET", "/gateway/config/export", key=ckey)
    check("xuất sao lưu -> 200", code == 200, code)
    try:
        bk = json.loads(text)
    except Exception:
        bk = {}
    check("file sao lưu đúng định dạng", bk.get("format") == "gateway-backup" and bk.get("version") == 1,
          {k: bk.get(k) for k in ("format", "version")})
    check("file sao lưu mang theo client key đang dùng", bk.get("client_key") == ckey, bool(bk.get("client_key")))
    check("file sao lưu đủ API key, model, cài đặt",
          len(bk.get("api_keys") or []) >= 2 and len(bk.get("models") or []) >= 3
          and len(bk.get("settings") or {}) >= 10 and len(bk.get("client_keys") or []) >= 1,
          {k: len(bk.get(k) or {}) for k in ("api_keys", "models", "settings", "client_keys")})
    check("file sao lưu có key thượng nguồn nguyên văn",
          any("sk-test-openai" == r.get("key_value") for r in (bk.get("api_keys") or [])), "khong thay key")

    # xoá sạch cấu hình rồi khôi phục từ chính file vừa xuất
    con = db.conn()
    for t in ("api_keys", "gateway_models", "providers"):
        con.execute("DELETE FROM %s" % t)
    con.commit()
    con.close()
    code, imp = jcall("POST", "/gateway/config/import", bk, key=ckey)
    check("nhập sao lưu -> 200", code == 200 and imp.get("ok"), imp)
    check("nhập xong trả key dùng được ngay",
          imp.get("client_key") == ckey and imp.get("client_key_source") == "file",
          (imp.get("client_key_source"), imp.get("error")))
    code, models = jcall("GET", "/v1/models", key=ckey)
    ids = sorted(m["id"] for m in models.get("data", []))
    check("nhập xong danh sách model giống trước khi xuất",
          code == 200 and ids == before_ids, {"truoc": before_ids, "sau": ids})
    check("nhập xong model free bật lại đúng", "claude-3-5-haiku-20241022" in ids and bool(ids), ids)
    code, out = jcall("POST", "/v1/chat/completions",
                      {"model": "claude-3-5-haiku-20241022", "messages": [{"role": "user", "content": "hi"}]}, key=ckey)
    check("nhập xong gọi được thượng nguồn",
          code == 200 and out.get("choices", [{}])[0].get("message", {}).get("content") == "hello-from-anthropic", out)
    code, st = jcall("GET", "/gateway/status", key=ckey)
    check("nhập xong giữ nguyên cài đặt cổng", code == 200 and str(st.get("port")) == str(GATEWAY_PORT), st)
    check("cùng địa chỉ thì không đòi khởi động lại", imp.get("restart_needed") is False, imp.get("restart_needed"))

    code, imp2 = jcall("POST", "/gateway/config/import", bk)
    check("nhập từ chính máy này không cần key -> 200", code == 200 and imp2.get("ok"), code)
    code, bad = jcall("POST", "/gateway/config/import", {"format": "la"}, key=ckey)
    check("nhập file lạ -> 400", code == 400, bad)
    code, bad2 = jcall("POST", "/gateway/config/import", {"format": "gateway-backup", "version": 1}, key=ckey)
    check("nhập file thiếu cài đặt -> 400", code == 400, bad2)

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
