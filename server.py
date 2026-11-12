import http.server, socketserver, threading, time, json, os, urllib.parse, ssl, socket, sys, gzip

import db, keypool, proxy, rate_limit, providers, translate

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
START_TIME = time.time()
# địa chỉ tiến trình này đang nghe: để biết nhập file xong có phải khởi động lại không
LISTEN = {"host": None, "port": None, "tls": False}
MAX_IMPORT_BYTES = 4 * 1024 * 1024
# Từ 1.3.0 không còn cờ "tạm dừng": cổng sống theo phiên Apelin, đóng/mở bằng
# Cài đặt Apelin hoặc app "Cổng" (gatewayctl on|off).

def json_response(handler, status, obj, headers=None):
    data = json.dumps(obj).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    if headers:
        for k,v in headers.items():
            handler.send_header(k, v)
    handler.end_headers()
    try:
        handler.wfile.write(data)
    except BrokenPipeError:
        pass

def text_response(handler, status, text, content_type="text/plain"):
    data = text.encode()
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    try:
        handler.wfile.write(data)
    except BrokenPipeError:
        pass

def read_body(handler):
    length = int(handler.headers.get("Content-Length", 0) or 0)
    if length <= 0:
        return b""
    return handler.rfile.read(length)

def extract_key(headers):
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    x = headers.get("x-api-key")
    if x:
        return x.strip()
    return None

def redact_key_value(v):
    if not v:
        return ""
    return v[:4] + "..." if len(v) > 4 else v

# Model không dùng để trò chuyện: không bao giờ chọn làm mặc định.
_NON_CHAT = ("embed", "whisper", "tts", "dall-e", "moderation", "audio", "rerank",
             "text-similarity", "image", "vision-encoder", "guard", "davinci", "babbage")

def chat_model(model_id):
    m = (model_id or "").lower()
    return bool(m) and not any(p in m for p in _NON_CHAT)

def log_request(client_key_id, provider, model, endpoint, status_code, latency_ms, stream, key_id, error_code=None, error_message=None, body=None):
    try:
        c = db.conn()
        c.execute("""INSERT INTO request_logs(id,timestamp,client_key_id,provider,model,endpoint,status_code,latency_ms,stream,key_id,error_code,error_message)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (str(__import__("uuid").uuid4()), db.now(), client_key_id, provider, model, endpoint, status_code, latency_ms, 1 if stream else 0, key_id, error_code, (error_message or "")[:500]))
        c.commit()
        # retention trim
        maxc = int(db.get_setting("max_log_count", "5000"))
        c.execute("DELETE FROM request_logs WHERE id IN (SELECT id FROM request_logs ORDER BY timestamp DESC LIMIT -1 OFFSET ?)", (maxc,))
        c.commit()
        c.close()
    except Exception:
        pass

def handle_cors(handler):
    origin = handler.headers.get("Origin", "*")
    handler.send_header("Access-Control-Allow-Origin", origin if db.get_setting("cors_enabled","1")=="1" else "")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization,x-api-key")

class GatewayHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _start(self):
        self._t0 = time.time()

    def do_OPTIONS(self):
        self.send_response(204)
        handle_cors(self)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._start()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/health":
                json_response(self, 200, {"status":"ok","uptime":int(time.time()-START_TIME)})
                log_request(None, None, None, path, 200, int((time.time()-self._t0)*1000), False, None)
                return
            if path == "/" or path == "/index.html":
                self._serve_static("index.html")
                return
            if path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
                return
            if path == "/v1/models":
                self._v1_models()
                return
            if path.startswith("/gateway/"):
                self._gateway_get(path, parsed)
                return
            json_response(self, 404, {"error":{"message":"not found","type":"not_found"}})
        except BrokenPipeError:
            pass

    def do_POST(self):
        self._start()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/v1/chat/completions":
                self._chat_completions()
                return
            if path == "/v1/messages":
                self._anthropic_messages()
                return
            if path == "/gateway/onboard":
                self._gateway_onboard()
                return
            if path == "/gateway/config/import":
                self._gateway_config_import()
                return
            if path.startswith("/gateway/"):
                self._gateway_post(path)
                return
            json_response(self, 404, {"error":{"message":"not found","type":"not_found"}})
        except BrokenPipeError:
            pass

    def do_DELETE(self):
        self._start()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/gateway/"):
            self._gateway_delete(path)
            return
        json_response(self, 404, {"error":{"message":"not found","type":"not_found"}})

    def do_PATCH(self):
        self._start()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/gateway/"):
            self._gateway_patch(path)
            return
        json_response(self, 404, {"error":{"message":"not found","type":"not_found"}})

    def _serve_static(self, name):
        # prevent traversal
        name = os.path.basename(name)
        fp = os.path.join(STATIC_DIR, name)
        if not os.path.exists(fp):
            text_response(self, 404, "not found")
            return
        with open(fp, "rb") as f:
            data = f.read()
        ct = "text/html" if name.endswith(".html") else "text/plain"
        if name.endswith(".css"): ct="text/css"
        if name.endswith(".js"): ct="application/javascript"
        # HTML luôn đọc từ đĩa mỗi request (để sửa thấy ngay) -> không cache.
        # File css/js có thể cache lâu.
        if name.endswith(".html"):
            cache = "no-store"
        else:
            cache = "public, max-age=86400"
        ae = self.headers.get("Accept-Encoding", "")
        if "gzip" in ae and len(data) > 1024:
            gz = gzip.compress(data, compresslevel=6)
            if len(gz) < len(data):
                data = gz
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
                self.send_header("Cache-Control", cache)
                self.send_header("Content-Length", str(len(data)))
                handle_cors(self)
                self.end_headers()
                try:
                    self.wfile.write(data)
                except BrokenPipeError:
                    pass
                return
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(data)))
        handle_cors(self)
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def _auth_client(self):
        required = db.get_setting("client_key_required", "1") == "1"
        key = extract_key(self.headers)
        cid = db.validate_client_key(key) if key else None
        if required and cid is None:
            return None, 401
        # if not required, allow anonymous
        return cid, None

    def _v1_models(self):
        cid, err = self._auth_client()
        if err:
            json_response(self, 401, {"error":{"message":"invalid client key","type":"auth_error"}})
            log_request(cid, None, None, "/v1/models", 401, int((time.time()-self._t0)*1000), False, None)
            return
        # rate limit
        if cid:
            ok, wait = rate_limit.check(cid)
            if not ok:
                self.send_response(429)
                self.send_header("Retry-After", str(int(wait)+1))
                self.send_header("Content-Type", "application/json")
                _rl = json.dumps({"error":{"message":"rate limited"}}).encode()
                self.send_header("Content-Length", str(len(_rl)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(_rl)
                log_request(cid, None, None, "/v1/models", 429, int((time.time()-self._t0)*1000), False, None)
                return
        c = db.conn()
        rows = c.execute("SELECT id,provider,display_name FROM gateway_models WHERE enabled=1 AND available=1 AND (health IS NULL OR health!='fail')").fetchall()
        c.close()
        data = {"object":"list","data":[]}
        for r in rows:
            data["data"].append({"id": r["id"], "object":"model", "created": int(time.time()), "owned_by": r["provider"]})
        json_response(self, 200, data)
        log_request(cid, None, None, "/v1/models", 200, int((time.time()-self._t0)*1000), False, None)

    # ---- chọn nhà cung cấp theo tên model ----
    def _connected_providers(self):
        c = db.conn()
        rows = c.execute("SELECT provider, COUNT(*) n FROM api_keys WHERE enabled=1 GROUP BY provider").fetchall()
        c.close()
        return [r["provider"] for r in rows]

    def _resolve_provider(self, model):
        """Tên model -> nhà cung cấp sẽ phục vụ nó."""
        name = (model or "").strip()
        c = db.conn()
        row = None
        if name:
            row = c.execute("SELECT provider FROM gateway_models WHERE id=?", (name,)).fetchone()
            if row is None:
                row = c.execute("""SELECT provider FROM gateway_models WHERE id LIKE ?
                                   ORDER BY available DESC, enabled DESC LIMIT 1""", (name + "%",)).fetchone()
        connected = [r["provider"] for r in c.execute(
            "SELECT DISTINCT provider FROM api_keys WHERE enabled=1").fetchall()]
        c.close()
        if row is not None:
            p = providers.canonical(row["provider"])
            if p and (not connected or p in connected):
                return p
        g = providers.guess(name)
        if g and (not connected or g in connected):
            return g
        if connected:
            return connected[0]
        return g or "openai"

    def _apply_default_model(self, j):
        """Client không cần gửi tên model: thiếu thì lấy model mặc định dùng được."""
        model = (j.get("model") or "").strip()
        if model:
            return model
        c = db.conn()
        rows = c.execute("""SELECT id FROM gateway_models WHERE enabled=1 AND available=1
                            AND (health IS NULL OR health!='fail')
                            ORDER BY is_free DESC, provider, id""").fetchall()
        c.close()
        ids = [r["id"] for r in rows]
        keep = db.get_setting("default_model", "") or ""
        if keep in ids and chat_model(keep):
            return keep
        chat = [i for i in ids if chat_model(i)]
        model = (chat or ids or [""])[0]
        if model:
            db.set_setting("default_model", model)
        return model

    def _pipe_stream(self, payload, translator=None, chunked=True):
        """Chuyển tiếp luồng SSE; có translator thì dịch từng mẩu.

        Dùng Transfer-Encoding: chunked để client biết điểm kết thúc, nếu không
        kết nối sẽ mở mãi và bên gọi bị treo cho tới lúc hết thời gian chờ.
        """
        def write(data):
            if not data:
                return
            if chunked:
                self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            else:
                self.wfile.write(data)
            self.wfile.flush()

        try:
            while True:
                block = payload.read(4096)
                if not block:
                    break
                write(translator.feed(block) if translator is not None else block)
            if translator is not None:
                write(translator.feed(b"\n\n"))
        except Exception:
            pass
        finally:
            try:
                payload.close()
            except Exception:
                pass
            if chunked:
                try:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except Exception:
                    pass

    def _chat_completions(self):
        cid, err = self._auth_client()
        if err:
            json_response(self, 401, {"error":{"message":"invalid client key","type":"auth_error"}})
            log_request(cid, None, None, "/v1/chat/completions", 401, int((time.time()-self._t0)*1000), False, None)
            return
        body = read_body(self)
        try:
            j = json.loads(body or b"{}")
        except Exception:
            json_response(self, 400, {"error":{"message":"invalid json","type":"invalid_request"}})
            log_request(cid, None, None, "/v1/chat/completions", 400, int((time.time()-self._t0)*1000), False, None)
            return
        stream = bool(j.get("stream", False))
        model = self._apply_default_model(j)
        if model and not j.get("model"):
            j["model"] = model
            body = json.dumps(j).encode()
        if cid:
            ok, wait = rate_limit.check(cid)
            if not ok:
                self.send_response(429)
                self.send_header("Retry-After", str(int(wait)+1))
                self.send_header("Content-Type", "application/json")
                _rl = json.dumps({"error":{"message":"rate limited"}}).encode()
                self.send_header("Content-Length", str(len(_rl)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(_rl)
                log_request(cid, "openai", model, "/v1/chat/completions", 429, int((time.time()-self._t0)*1000), stream, None)
                return
        provider = self._resolve_provider(model)
        spec = providers.spec(provider) or {}
        upstream_anthropic = spec.get("protocol") == providers.ANTHROPIC
        out_body = body
        if upstream_anthropic:
            try:
                out_body = json.dumps(translate.openai_to_anthropic(j)).encode()
            except Exception as e:
                json_response(self, 400, {"error":{"message":"khong dich duoc yeu cau: %s" % e,"type":"invalid_request"}})
                return
        t0 = time.time()
        if upstream_anthropic:
            ok, status, headers, payload, key_id, errb = proxy.messages(provider, out_body, stream)
        else:
            ok, status, headers, payload, key_id, errb = proxy.chat(provider, out_body, stream)
        latency = int((time.time()-t0)*1000)
        if not ok:
            self.send_response(status if status else 502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(errb)))
            handle_cors(self)
            self.end_headers()
            try:
                self.wfile.write(errb)
            except BrokenPipeError:
                pass
            log_request(cid, provider, model, "/v1/chat/completions", status if status else 502, latency, stream, key_id, "upstream_error", errb.decode(errors="ignore")[:500])
            return
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Transfer-Encoding", "chunked")
            handle_cors(self)
            self.end_headers()
            translator = translate.AnthropicToOpenAIStream(model) if upstream_anthropic else None
            self._pipe_stream(payload, translator)
            log_request(cid, provider, model, "/v1/chat/completions", 200, int((time.time()-t0)*1000), True, key_id)
        else:
            data = payload.read() if hasattr(payload, "read") else payload
            try:
                payload.close()
            except Exception:
                pass
            ctype = headers.get("Content-Type", "application/json")
            if upstream_anthropic:
                try:
                    data = json.dumps(translate.anthropic_to_openai(json.loads(data or b"{}"), model)).encode()
                    ctype = "application/json"
                except Exception:
                    pass
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            handle_cors(self)
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass
            log_request(cid, provider, model, "/v1/chat/completions", status, int((time.time()-t0)*1000), False, key_id)

    def _anthropic_messages(self):
        cid, err = self._auth_client()
        if err:
            json_response(self, 401, {"error":{"message":"invalid client key","type":"auth_error"}})
            log_request(cid, None, None, "/v1/messages", 401, int((time.time()-self._t0)*1000), False, None)
            return
        body = read_body(self)
        try:
            j = json.loads(body or b"{}")
        except Exception:
            json_response(self, 400, {"error":{"message":"invalid json","type":"invalid_request"}})
            log_request(cid, None, None, "/v1/messages", 400, int((time.time()-self._t0)*1000), False, None)
            return
        stream = bool(j.get("stream", False))
        model = self._apply_default_model(j)
        if model and not j.get("model"):
            j["model"] = model
            body = json.dumps(j).encode()
        if cid:
            ok, wait = rate_limit.check(cid)
            if not ok:
                self.send_response(429)
                self.send_header("Retry-After", str(int(wait)+1))
                self.send_header("Content-Type", "application/json")
                _rl = json.dumps({"error":{"message":"rate limited"}}).encode()
                self.send_header("Content-Length", str(len(_rl)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(_rl)
                log_request(cid, "anthropic", model, "/v1/messages", 429, int((time.time()-self._t0)*1000), stream, None)
                return
        provider = self._resolve_provider(model)
        spec = providers.spec(provider) or {}
        upstream_openai = spec.get("protocol") == providers.OPENAI
        out_body = body
        if upstream_openai:
            try:
                out_body = json.dumps(translate.anthropic_to_openai_request(j)).encode()
            except Exception as e:
                json_response(self, 400, {"error":{"message":"khong dich duoc yeu cau: %s" % e,"type":"invalid_request"}})
                return
        t0 = time.time()
        if upstream_openai:
            ok, status, headers, payload, key_id, errb = proxy.chat(provider, out_body, stream)
        else:
            ok, status, headers, payload, key_id, errb = proxy.messages(provider, out_body, stream)
        latency = int((time.time()-t0)*1000)
        if not ok:
            self.send_response(status if status else 502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(errb)))
            handle_cors(self)
            self.end_headers()
            try:
                self.wfile.write(errb)
            except BrokenPipeError:
                pass
            log_request(cid, provider, model, "/v1/messages", status if status else 502, latency, stream, key_id, "upstream_error", errb.decode(errors="ignore")[:500])
            return
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Transfer-Encoding", "chunked")
            handle_cors(self)
            self.end_headers()
            translator = translate.OpenAIToAnthropicStream(model) if upstream_openai else None
            self._pipe_stream(payload, translator)
            log_request(cid, provider, model, "/v1/messages", 200, int((time.time()-t0)*1000), True, key_id)
        else:
            data = payload.read() if hasattr(payload, "read") else payload
            try:
                payload.close()
            except Exception:
                pass
            ctype = headers.get("Content-Type", "application/json")
            if upstream_openai:
                try:
                    data = json.dumps(translate.openai_to_anthropic_response(json.loads(data or b"{}"), model)).encode()
                    ctype = "application/json"
                except Exception:
                    pass
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            handle_cors(self)
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass
            log_request(cid, provider, model, "/v1/messages", status, int((time.time()-t0)*1000), False, key_id)

    # ---- thiết lập một bước: dán key là xong ----
    def _setup_allowed(self):
        if self.client_address[0] in ("127.0.0.1", "::1", "localhost"):
            return True
        return db.validate_client_key(extract_key(self.headers)) is not None

    def _probe_models(self, base, protocol, api_key, paths=("/v1/models", "/models")):
        """Thử dò model ở một địa chỉ. -> (ok, [id...], models_path, lỗi)

        ok=True khi máy chủ trả lời được (kể cả danh sách rỗng): key sống, chỉ là
        nhà cung cấp không kèm model nào.
        """
        err, saw_ok = "", False
        for mp in paths:
            ok, ids, e = proxy.discover_models_at((base or "").rstrip("/") + mp, protocol,
                                                  api_key, timeout=25)
            if ok:
                saw_ok = True
                if ids:
                    return True, ids, mp, ""
            err = e or err
        return (saw_ok, [], paths[0], "") if saw_ok else (False, [], paths[0], err)

    def _gateway_onboard(self):
        """Một bước: chỉ cần API key (kèm base URL nếu là nhà cung cấp lạ).

        Tự đoán nhà cung cấp, tự dò model, tự bật model miễn phí, tự tạo client key.
        """
        if not self._setup_allowed():
            json_response(self, 403, {"error":{"message":"chi thiet lap duoc tu chinh may nay","type":"forbidden"}})
            return
        body = read_body(self)
        try:
            j = json.loads(body or b"{}")
        except Exception:
            json_response(self, 400, {"error":{"message":"invalid json"}})
            return
        provider_in = j.get("provider")
        if provider_in and providers.canonical(provider_in) is None:
            json_response(self, 400, {"error":{"message":"provider khong hop le","type":"invalid_request"}})
            return
        api_key = (j.get("api_key") or "").strip()
        if not api_key:
            json_response(self, 400, {"error":{"message":"thieu API key","type":"invalid_request"}})
            return
        raw_base = providers.normalize_base(j.get("base_url"))

        # 1. đoán nhà cung cấp (tên nhập tay > máy chủ > đường dẫn > tiền tố key)
        guess = providers.detect(provider=provider_in, base_url=raw_base, api_key=api_key)
        cands = providers.candidates(provider=provider_in, base_url=raw_base, api_key=api_key)
        chosen, base_used, protocol, models_path = None, "", providers.OPENAI, "/v1/models"
        ids, verify_ok, verify_msg, detected_by = [], False, "", ""

        if guess:
            sp = providers.spec(guess) or {}
            base_used = raw_base or sp.get("base_url") or ""
            protocol = sp.get("protocol") or providers.OPENAI
            ok, ids, models_path, err = self._probe_models(base_used, protocol, api_key)
            chosen, verify_ok, verify_msg = guess, ok, ("" if ok else err)
            detected_by = "provider" if providers.canonical(provider_in) else ("base_url" if raw_base else "key")
        else:
            # 2. key không nói lên điều gì: thử lần lượt các nhà cung cấp quen
            err = ""
            for cand in cands:
                sp = providers.spec(cand) or {}
                ok, ids, mp, e = self._probe_models(sp.get("base_url") or "",
                                                    sp.get("protocol") or providers.OPENAI, api_key)
                err = e or err
                if ok:
                    chosen, base_used = cand, sp.get("base_url") or ""
                    protocol, models_path = sp.get("protocol") or providers.OPENAI, mp
                    verify_ok, verify_msg, detected_by = True, "", "probe"
                    break
            # 3. địa chỉ lạ: thử trực tiếp như nhà cung cấp tự nhập
            if chosen is None and raw_base:
                for prot in (providers.OPENAI, providers.ANTHROPIC):
                    ok, ids, mp, e = self._probe_models(raw_base, prot, api_key)
                    err = e or err
                    if ok:
                        chosen, base_used = "custom", raw_base
                        protocol, models_path = prot, mp
                        verify_ok, verify_msg, detected_by = True, "", "probe"
                        break
                if chosen is None:
                    # chưa gọi được vẫn nhận: đoán giao thức theo tiền tố key
                    protocol = providers.ANTHROPIC if api_key.lower().startswith("sk-ant-") else providers.OPENAI
                    chosen, base_used, models_path = "custom", raw_base, "/v1/models"
                    verify_ok, verify_msg, detected_by = False, err, "key"
            if chosen is None:
                json_response(self, 400, {"error":{
                    "message":"chua nhan ra nha cung cap - dan them base URL hoac chon nha cung cap",
                    "type":"invalid_request"}})
                return

        probe_only = bool(j.get("probe_only"))
        spec = providers.spec(chosen) or {}
        if probe_only:
            probe_ids = list(ids) if ids else list(spec.get("default_models") or [])
            models = [{"id": mid, "free": bool(providers.is_free(chosen, mid))} for mid in probe_ids]
            json_response(self, 200, {
                "ok": True, "provider": chosen, "label": spec.get("label", chosen),
                "base_url": base_used or spec.get("base_url") or "",
                "protocol": protocol, "detected_by": detected_by,
                "models_path": models_path or spec.get("models_path") or "/v1/models",
                "verify_ok": verify_ok, "verify_error": verify_msg,
                "models": models,
                "free_only": db.get_setting("free_models_only", "1") == "1",
            })
            return

        # 4. lưu địa chỉ/giao thức (tạo row nếu ncc đã bị xoá / chưa có)
        import uuid as _uuid
        was_custom = (chosen == "custom")
        if was_custom and base_used:
            # mỗi base URL là một nhà cung cấp riêng: dùng base_url làm tên
            c = db.conn()
            existing = c.execute("SELECT name FROM providers WHERE base_url=?", (base_used,)).fetchone()
            c.close()
            if existing:
                chosen = existing["name"]
            else:
                chosen = base_used
        c = db.conn()
        row = c.execute("SELECT name FROM providers WHERE name=?", (chosen,)).fetchone()
        c.close()
        sp = providers.spec(chosen) or {}
        if row is None:
            c = db.conn()
            c.execute("""INSERT INTO providers(id,name,base_url,enabled,protocol,chat_path,messages_path,models_path,key_url)
                         VALUES (?,?,?,?,?,?,?,?,?)""",
                      (str(_uuid.uuid4()), chosen, base_used or sp.get("base_url") or "",
                       1, sp.get("protocol") or protocol,
                       sp.get("chat_path") or "/v1/chat/completions",
                       sp.get("messages_path") or "/v1/messages",
                       models_path or sp.get("models_path") or "/v1/models",
                       sp.get("key_url") or ""))
            c.commit()
            c.close()
        if was_custom:
            db.update_provider(chosen, base_url=base_used, protocol=protocol,
                               chat_path="/v1/chat/completions", messages_path="/v1/messages",
                               models_path=models_path or "/v1/models", enabled=1)
        elif raw_base:
            db.set_provider_base_url(chosen, raw_base)
        # nếu từng bị xoá vĩnh viễn thì cho phép xuất hiện lại
        deleted = {x.strip() for x in (db.get_setting("deleted_providers", "") or "").split(",") if x.strip()}
        if chosen in deleted:
            deleted.discard(chosen)
            db.set_setting("deleted_providers", ",".join(sorted(deleted)))
        spec = providers.spec(chosen) or {}
        web_url_in = (j.get("web_url") or j.get("key_url") or "").strip()
        if web_url_in:
            db.update_provider(chosen, key_url=web_url_in)
            spec = dict(spec); spec["key_url"] = web_url_in

        # 5. lưu key (cùng key thì dùng lại, không tạo trùng)
        c = db.conn()
        row = c.execute("SELECT id FROM api_keys WHERE provider=? AND key_value=?", (chosen, api_key)).fetchone()
        c.close()
        if row is not None:
            key_id = row["id"]
            keypool.set_enabled(key_id, True)
        else:
            key_id = keypool.add_key(chosen, j.get("label") or (spec["label"] + " key"), api_key, 0)

        # 6. model: dò được thì dùng, không thì dùng danh sách mặc định của nhà cung cấp
        fo = j.get("free_only")
        if fo is not None:
            db.set_setting("free_models_only",
                           "1" if (fo is True or str(fo).lower() in ("1", "true", "yes", "on")) else "0")
        model_ids = list(ids) or list(spec.get("default_models") or [])
        selected_models = j.get("selected_models")
        if isinstance(selected_models, list):
            if selected_models:
                allowed = set(model_ids) if model_ids else set(selected_models)
                model_ids = [m for m in selected_models if (not allowed or m in allowed)]
            else:
                model_ids = []
        if model_ids:
            db.upsert_models(chosen, model_ids)
        db.recompute_model_flags()
        db.apply_models_policy(chosen)

        # 7. client key: luôn cấp một key mới để dán vào app ngay
        c = db.conn()
        n = c.execute("SELECT COUNT(*) FROM client_keys WHERE enabled=1").fetchone()[0]
        c.close()
        _, raw_client_key = db.new_client_key(spec["label"] + " · thiết lập")
        db.set_setting("last_provider", chosen)

        # 8. model mặc định để client không cần gửi tên model
        models = [{"id": r["id"], "free": bool(r["is_free"])}
                  for r in db.list_models(enabled_only=True) if r["provider"] == chosen]
        default_model = db.get_setting("default_model", "") or ""
        ids_on = [m["id"] for m in models]
        if ids_on and (default_model not in ids_on or not chat_model(default_model)):
            chat = [i for i in ids_on if chat_model(i)]
            default_model = (chat or ids_on)[0]
            db.set_setting("default_model", default_model)

        host = db.get_setting("host", "127.0.0.1")
        port = db.get_setting("port", "15199")
        json_response(self, 200, {
            "ok": True, "provider": chosen, "label": spec["label"], "base_url": spec.get("base_url"),
            "protocol": spec.get("protocol"), "detected_by": detected_by,
            "models_path": spec.get("models_path"), "default_model": default_model,
            "verify_ok": verify_ok, "verify_error": verify_msg,
            "key_id": key_id, "models": models,
            "free_only": db.get_setting("free_models_only", "1") == "1",
            "web_url": (web_url_in or spec.get("key_url") or ""),
            "client_key": raw_client_key, "has_client_key": n > 0,
            "openai_base_url": "http://%s:%s/v1" % (host, port),
            "anthropic_base_url": "http://%s:%s" % (host, port),
        })


    # ---- sao lưu / khôi phục toàn bộ cấu hình ----
    def _gateway_config_export(self):
        key = extract_key(self.headers)
        data = db.export_config(client_key=key if (key and db.validate_client_key(key)) else None)
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        name = "gateway-backup-%s.json" % time.strftime("%Y%m%d-%H%M%S")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        handle_cors(self)
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _gateway_config_import(self):
        """Nhập file sao lưu: thay toàn bộ cấu hình và trả key để vào ngay."""
        if not self._setup_allowed():
            json_response(self, 403, {"error":{
                "message":"chi nhap duoc tu chinh may nay hoac voi client key hop le",
                "type":"forbidden"}})
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            length = 0
        if length > MAX_IMPORT_BYTES:
            json_response(self, 413, {"error":{"message":"file sao luu qua lon",
                                               "type":"invalid_request"}})
            return
        body = read_body(self)
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except Exception:
            json_response(self, 400, {"error":{"message":"file khong doc duoc (JSON loi)",
                                               "type":"invalid_request"}})
            return
        try:
            res = db.import_config(data, keep_client_key=extract_key(self.headers))
        except db.BackupError as e:
            json_response(self, 400, {"error":{"message":str(e),"type":"invalid_request"}})
            return
        except Exception as e:
            json_response(self, 500, {"error":{"message":"nhap that bai: %s" % e,
                                               "type":"server_error"}})
            return
        host, port, tls = listen_target()
        res.update({"ok": True, "host": host, "port": port, "tls_enabled": tls,
                    "restart_needed": restart_needed(host, port, tls)})
        json_response(self, 200, res)

    def _gateway_restart(self):
        """Khởi động lại chính tiến trình này để nạp host/port/TLS vừa nhập."""
        host, port, tls = listen_target()
        json_response(self, 200, {"ok": True, "restarting": True, "host": host,
                                  "port": port, "tls_enabled": tls})
        threading.Timer(0.6, reexec_process).start()

    # ---- gateway management ----
    def _require_mgmt_auth(self):
        cid = db.validate_client_key(extract_key(self.headers))
        if cid is None:
            json_response(self, 401, {"error":{"message":"invalid client key","type":"auth_error"}})
            return None
        # loopback only
        if self.client_address[0] not in ("127.0.0.1", "::1", "localhost"):
            json_response(self, 403, {"error":{"message":"management only from loopback","type":"forbidden"}})
            return None
        return cid

    def _gateway_get(self, path, parsed):
        cid = self._require_mgmt_auth()
        if cid is None:
            return
        if path == "/gateway/config/export":
            self._gateway_config_export()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        if path == "/gateway/status":
            json_response(self, 200, {"status":"running","uptime":int(time.time()-START_TIME),"port":db.get_setting("port"),"host":db.get_setting("host")})
            return
        if path == "/gateway/keys":
            provider = qs.get("provider", [None])[0]
            rows = keypool.list_keys(provider)
            out = []
            for r in rows:
                out.append({
                    "id": r["id"], "provider": r["provider"], "label": r["label"],
                    "key_prefix": redact_key_value(r["key_value"]), "enabled": bool(r["enabled"]),
                    "priority": r["priority"], "failure_count": r["failure_count"],
                    "cooldown_until": r["cooldown_until"], "last_used_at": r["last_used_at"],
                })
            json_response(self, 200, {"keys": out})
            return
        if path == "/gateway/models":
            c = db.conn()
            rows = c.execute("SELECT * FROM gateway_models ORDER BY provider,id").fetchall()
            c.close()
            json_response(self, 200, {"models": [dict(r) for r in rows]})
            return
        if path == "/gateway/logs":
            limit = int(qs.get("limit", ["100"])[0])
            c = db.conn()
            rows = c.execute("SELECT * FROM request_logs ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
            c.close()
            json_response(self, 200, {"logs": [dict(r) for r in rows]})
            return
        if path == "/gateway/settings":
            json_response(self, 200, {"settings": db.all_settings()})
            return
        if path == "/gateway/client-keys":
            c = db.conn()
            rows = c.execute("SELECT id,name,enabled,created_at,last_used_at FROM client_keys ORDER BY created_at DESC").fetchall()
            c.close()
            json_response(self, 200, {"client_keys": [dict(r) for r in rows]})
            return
        if path == "/gateway/providers":
            c = db.conn()
            n = c.execute("SELECT COUNT(*) FROM client_keys WHERE enabled=1").fetchone()[0]
            c.close()
            json_response(self, 200, {
                "providers": db.list_providers(),
                "free_only": db.get_setting("free_models_only", "1") == "1",
                "client_keys": n,
                "last_provider": db.get_setting("last_provider", ""),
            })
            return
        json_response(self, 404, {"error":{"message":"not found"}})

    def _gateway_post(self, path):
        cid = self._require_mgmt_auth()
        if cid is None:
            return
        if path == "/gateway/restart":
            self._gateway_restart()
            return
        body = read_body(self)
        try:
            j = json.loads(body or b"{}")
        except Exception:
            json_response(self, 400, {"error":{"message":"invalid json"}})
            return
        if path == "/gateway/keys":
            name = providers.canonical(j.get("provider","openai")) or j.get("provider","openai")
            kid = keypool.add_key(name, j.get("label",""), j.get("key",""), int(j.get("priority",0)))
            # dò model ngay để model của nhà cung cấp này hiện ra được
            found = None
            if db.get_setting("auto_discover_models", "1") == "1":
                ok, ids, err = proxy.discover_models(name)
                if ok and ids:
                    db.upsert_models(name, ids)
                    db.recompute_model_flags()
                    found = db.apply_models_policy(name)
            json_response(self, 200, {"id": kid, "models": found})
            return
        if path == "/gateway/client-keys":
            name = j.get("name", "default")
            newid, raw = db.new_client_key(name)
            json_response(self, 200, {"id": newid, "key": raw})
            return
        if path == "/gateway/settings":
            for k,v in j.items():
                db.set_setting(k, v)
            json_response(self, 200, {"ok": True})
            return
        if path == "/gateway/providers/discover":
            name = providers.canonical(j.get("provider"))
            if name is None:
                json_response(self, 400, {"error":{"message":"provider khong hop le"}})
                return
            ok, ids, err = proxy.discover_models(name)
            if not ok:
                json_response(self, 502, {"error":{"message":err,"type":"upstream_error"}})
                return
            db.upsert_models(name, ids)
            db.recompute_model_flags()
            json_response(self, 200, {"ok": True, "found": len(ids), "models": ids, "enabled": db.apply_models_policy()})
            return
        if path == "/gateway/providers/base-url":
            ok = db.set_provider_base_url(j.get("provider"), j.get("base_url"))
            json_response(self, 200 if ok else 400, {"ok": ok})
            return
        if path == "/gateway/models/policy":
            free_only = bool(j.get("free_only", True))
            db.set_setting("free_models_only", "1" if free_only else "0")
            json_response(self, 200, {"ok": True, "free_only": free_only,
                                      "enabled": db.apply_models_policy()})
            return
        if path == "/gateway/models/test":
            mid = (j.get("id") or "").strip()
            if not mid:
                json_response(self, 400, {"error": {"message": "thieu model id"}})
                return
            ok, msg = proxy.test_model(mid)
            json_response(self, 200, {"ok": ok, "message": msg})
            return
        if path == "/gateway/keys/test":
            kid = (j.get("id") or "").strip()
            if not kid:
                json_response(self, 400, {"error": {"message": "thieu key id"}})
                return
            ok, msg = proxy.test_key_by_id(kid, j.get("model"))
            json_response(self, 200, {"ok": ok, "message": msg})
            return
        json_response(self, 404, {"error":{"message":"not found"}})

    def _gateway_patch(self, path):
        cid = self._require_mgmt_auth()
        if cid is None:
            return
        body = read_body(self)
        try:
            j = json.loads(body or b"{}")
        except Exception:
            json_response(self, 400, {"error":{"message":"invalid json"}})
            return
        # /gateway/keys/:id
        if path.startswith("/gateway/keys/"):
            kid = path.split("/")[-1]
            if "enabled" in j:
                keypool.set_enabled(kid, bool(j["enabled"]))
            if "label" in j:
                keypool.update_key(kid, label=j["label"])
            if "priority" in j:
                keypool.update_key(kid, priority=int(j["priority"]))
            json_response(self, 200, {"ok": True})
            return
        if path.startswith("/gateway/models/"):
            kid = path.split("/")[-1]
            c = db.conn()
            if "enabled" in j:
                c.execute("UPDATE gateway_models SET enabled=? WHERE id=?", (1 if j["enabled"] else 0, kid))
                c.commit()
            c.close()
            json_response(self, 200, {"ok": True})
            return
        json_response(self, 404, {"error":{"message":"not found"}})

    def _gateway_delete(self, path):
        cid = self._require_mgmt_auth()
        if cid is None:
            return
        if path.startswith("/gateway/keys/"):
            kid = path.split("/")[-1]
            keypool.delete_key(kid)
            json_response(self, 200, {"ok": True})
            return
        if path.startswith("/gateway/client-keys/"):
            kid = path.split("/")[-1]
            c = db.conn()
            c.execute("DELETE FROM client_keys WHERE id=?", (kid,))
            c.commit()
            c.close()
            json_response(self, 200, {"ok": True})
            return
        if path.startswith("/gateway/models/"):
            mid = urllib.parse.unquote(path.split("/gateway/models/", 1)[-1])
            ok = db.delete_model(mid)
            if not ok:
                json_response(self, 404, {"error": {"message": "model not found"}})
                return
            json_response(self, 200, {"ok": True})
            return
        if path.startswith("/gateway/providers/"):
            name = urllib.parse.unquote(path.split("/gateway/providers/", 1)[-1])
            ok = db.delete_provider(name)
            if not ok:
                json_response(self, 404, {"error": {"message": "provider not found"}})
                return
            json_response(self, 200, {"ok": True})
            return
        json_response(self, 404, {"error":{"message":"not found"}})

    def log_message(self, format, *args):
        pass

# ---- địa chỉ theo cài đặt: dùng cho nhập file và khởi động lại ----
def listen_target():
    """Địa chỉ mà main.py sẽ nghe theo cài đặt hiện tại. -> (host, port, tls)"""
    host = db.get_setting("host", "127.0.0.1") or "127.0.0.1"
    if db.get_setting("lan_enabled", "0") == "1":
        host = "0.0.0.0"
    try:
        port = int(db.get_setting("port", "15199"))
    except (TypeError, ValueError):
        port = 15199
    if not (0 <= port <= 65535):
        port = 15199
    return host, port, db.get_setting("tls_enabled", "0") == "1"

def restart_needed(host=None, port=None, tls=None):
    """Nhập file xong có phải khởi động lại mới đúng địa chỉ không."""
    if LISTEN["port"] is None:
        return False
    host, port, tls = listen_target() if host is None else (host, port, tls)
    return bool(LISTEN["port"] != port or LISTEN["host"] != host or bool(LISTEN["tls"]) != bool(tls))

def reexec_process():
    """Thay chính tiến trình này bằng một tiến trình mới (giữ nguyên session screen)."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    argv0 = (str(sys.argv[0]) if sys.argv and str(sys.argv[0]).endswith(".py")
             else os.path.join(BASE_DIR, "main.py"))
    try:
        os.execv(sys.executable, [sys.executable, os.path.abspath(argv0)] + list(sys.argv[1:]))
    except Exception:
        os._exit(3)

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def make_server(host, port, use_tls=False, certfile=None, keyfile=None):
    httpd = ThreadedHTTPServer((host, port), GatewayHandler)
    LISTEN.update({"host": host, "port": port, "tls": bool(use_tls)})
    if use_tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    return httpd
