import http.server, socketserver, threading, time, json, os, urllib.parse, ssl, socket

import db, keypool, proxy, rate_limit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
START_TIME = time.time()

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
        self.send_response(200)
        self.send_header("Content-Type", ct)
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
                self.end_headers()
                self.wfile.write(json.dumps({"error":{"message":"rate limited"}}).encode())
                log_request(cid, None, None, "/v1/models", 429, int((time.time()-self._t0)*1000), False, None)
                return
        c = db.conn()
        rows = c.execute("SELECT id,provider,display_name FROM gateway_models WHERE enabled=1").fetchall()
        c.close()
        data = {"object":"list","data":[]}
        for r in rows:
            data["data"].append({"id": r["id"], "object":"model", "created": int(time.time()), "owned_by": r["provider"]})
        json_response(self, 200, data)
        log_request(cid, None, None, "/v1/models", 200, int((time.time()-self._t0)*1000), False, None)

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
        model = j.get("model", "")
        if cid:
            ok, wait = rate_limit.check(cid)
            if not ok:
                self.send_response(429)
                self.send_header("Retry-After", str(int(wait)+1))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error":{"message":"rate limited"}}).encode())
                log_request(cid, "openai", model, "/v1/chat/completions", 429, int((time.time()-self._t0)*1000), stream, None)
                return
        t0 = time.time()
        ok, status, headers, payload, key_id, errb = proxy.openai_chat(body, stream)
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
            log_request(cid, "openai", model, "/v1/chat/completions", status if status else 502, latency, stream, key_id, "upstream_error", errb.decode(errors="ignore")[:200])
            return
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            handle_cors(self)
            self.end_headers()
            try:
                while True:
                    chunk = payload.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                try:
                    payload.close()
                except Exception:
                    pass
            log_request(cid, "openai", model, "/v1/chat/completions", 200, int((time.time()-t0)*1000), True, key_id)
        else:
            data = payload.read() if hasattr(payload, "read") else payload
            try:
                payload.close()
            except Exception:
                pass
            self.send_response(status)
            self.send_header("Content-Type", headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            handle_cors(self)
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass
            log_request(cid, "openai", model, "/v1/chat/completions", status, int((time.time()-t0)*1000), False, key_id)

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
        model = j.get("model", "")
        if cid:
            ok, wait = rate_limit.check(cid)
            if not ok:
                self.send_response(429)
                self.send_header("Retry-After", str(int(wait)+1))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error":{"message":"rate limited"}}).encode())
                log_request(cid, "anthropic", model, "/v1/messages", 429, int((time.time()-self._t0)*1000), stream, None)
                return
        t0 = time.time()
        ok, status, headers, payload, key_id, errb = proxy.anthropic_messages(body, stream)
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
            log_request(cid, "anthropic", model, "/v1/messages", status if status else 502, latency, stream, key_id, "upstream_error", errb.decode(errors="ignore")[:200])
            return
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            handle_cors(self)
            self.end_headers()
            try:
                while True:
                    chunk = payload.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                try:
                    payload.close()
                except Exception:
                    pass
            log_request(cid, "anthropic", model, "/v1/messages", 200, int((time.time()-t0)*1000), True, key_id)
        else:
            data = payload.read() if hasattr(payload, "read") else payload
            try:
                payload.close()
            except Exception:
                pass
            self.send_response(status)
            self.send_header("Content-Type", headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            handle_cors(self)
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass
            log_request(cid, "anthropic", model, "/v1/messages", status, int((time.time()-t0)*1000), False, key_id)

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
        json_response(self, 404, {"error":{"message":"not found"}})

    def _gateway_post(self, path):
        cid = self._require_mgmt_auth()
        if cid is None:
            return
        body = read_body(self)
        try:
            j = json.loads(body or b"{}")
        except Exception:
            json_response(self, 400, {"error":{"message":"invalid json"}})
            return
        if path == "/gateway/keys":
            kid = keypool.add_key(j.get("provider","openai"), j.get("label",""), j.get("key",""), int(j.get("priority",0)))
            json_response(self, 200, {"id": kid})
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
        json_response(self, 404, {"error":{"message":"not found"}})

    def log_message(self, format, *args):
        pass

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def make_server(host, port, use_tls=False, certfile=None, keyfile=None):
    httpd = ThreadedHTTPServer((host, port), GatewayHandler)
    if use_tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    return httpd
