"""Trang tắt/mở cổng.

Chạy khi có file data/paused: giữ đúng cổng của gateway nhưng chỉ phục vụ một
trang nhỏ có nút "Bật lại". Bấm nút (hoặc POST /on từ máy này) là xoá cờ, tiến
trình thoát, để bộ giám sát bật lại main.py.
"""

import http.server
import json
import os
import ssl
import threading
import urllib.parse

import db

FLAG = os.path.join(db.DATA_DIR, "paused")


def listen_target():
    host = db.get_setting("host", "127.0.0.1")
    if db.get_setting("lan_enabled", "0") == "1":
        host = "0.0.0.0"
    try:
        port = int(db.get_setting("port", "15199"))
    except (TypeError, ValueError):
        port = 15199
    if not (0 <= port <= 65535):
        port = 15199
    return host, port


PAGE = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Gateway đang tắt</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b1020;color:#e7ecff;
font:16px/1.5 -apple-system,system-ui,"Segoe UI",Roboto,sans-serif;padding:24px}
.card{width:100%;max-width:420px;background:linear-gradient(180deg,#151d36,#101733);
border:1px solid #253055;border-radius:22px;padding:26px;text-align:center;
box-shadow:0 24px 60px -30px rgba(0,0,0,.9)}
.ico{font-size:44px;line-height:1}
h1{font-size:20px;margin:14px 0 6px}
p{color:#9aa8ce;margin:0 0 20px;font-size:14px}
button{width:100%;border:0;border-radius:16px;padding:16px;font-size:16px;font-weight:600;
background:linear-gradient(135deg,#6c8cff,#8f6cff);color:#fff}
button:active{transform:scale(.985)}
#msg{margin-top:14px;font-size:14px;color:#8ff0c0;min-height:20px}
</style></head><body>
<div class="card">
  <div class="ico">⏻</div>
  <h1>Cổng đang tắt</h1>
  <p>API của bạn chưa nhận yêu cầu nào. Bật lại để dùng tiếp.</p>
  <button id="on" onclick="turnOn()">Bật cổng</button>
  <div id="msg"></div>
</div>
<script>
function turnOn(){
  var b=document.getElementById('on'), m=document.getElementById('msg');
  b.disabled=true; b.textContent='Đang bật…'; m.textContent='';
  fetch('/on',{method:'POST'}).then(function(r){return r.json()}).then(function(){
    m.textContent='Đã bật, đang mở lại trang…';
    setTimeout(function(){location.replace('/')},2500);
  }).catch(function(e){
    b.disabled=false; b.textContent='Bật cổng'; m.textContent='Không bật được: '+e;
  });
}
</script>
</body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "GatewayPaused/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/v1") or path.startswith("/gateway"):
            self._send(503, json.dumps({
                "error": {"message": "cổng đang tắt (paused)", "type": "gateway_paused"}
            }), "application/json; charset=utf-8")
            return
        self._send(200, PAGE, "text/html; charset=utf-8")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/on":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._send(403, json.dumps({"error": {"message": "loopback only"}}),
                           "application/json; charset=utf-8")
                return
            try:
                os.remove(FLAG)
            except FileNotFoundError:
                pass
            self._send(200, json.dumps({"ok": True}), "application/json; charset=utf-8")
            threading.Timer(0.4, lambda: os._exit(0)).start()
            return
        self._send(404, json.dumps({"error": {"message": "not found"}}),
                   "application/json; charset=utf-8")


def main():
    host, port = listen_target()
    httpd = http.server.ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    if db.get_setting("tls_enabled", "0") == "1":
        cert = db.get_setting("tls_cert_path", "")
        key = db.get_setting("tls_key_path", "")
        if cert and key and os.path.exists(cert) and os.path.exists(key):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    print("Gateway paused on %s:%d - chỉ phục vụ trang bật lại" % (host, port), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()