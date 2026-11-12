"""Thử thật không cần trình duyệt: chạy mã giao diện của static/index.html trong node
(async/fetch) đối diện với một bản gateway thật, có máy chủ giả đóng vai nhà cung cấp.

Bài thử này bắt đúng thứ người dùng làm lần đầu:
    mở bảng thiết lập -> chọn nhà cung cấp -> dán key -> "Thiết lập ngay"
    -> màn kết quả -> "Vào bảng điều khiển" -> dùng client key vừa cấp.

Chạy:  python3 Tests/live_ui_test.py
Cần node trên PATH (fetch tự viết, vì bản node trên máy này không có WebAssembly).
Bản chạy thử được chép sang thư mục tạm nên không đụng tới data/ của dự án.
"""
import http.server
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST = "/tmp/gwui"
MOCK_PORT, PORT = 15911, 15197
MOCK = "http://127.0.0.1:%d" % MOCK_PORT
GW = "http://127.0.0.1:%d" % PORT
UPSTREAM_MODELS = []          # model mà gateway gửi lên thượng nguồn
RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond), extra))
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  | " + str(extra)))


# ---------- máy chủ giả: đóng vai OpenAI ----------
class Upstream(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send({"object": "list", "data": [
                {"id": "mock-gpt-4o"},               # đắt
                {"id": "mock-gpt-4o-mini"},          # miễn phí
                {"id": "mock-text-embedding-nano"}]}) # miễn phí nhưng không phải model trò chuyện
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        UPSTREAM_MODELS.append(body.get("model"))
        if "anthropic" in self.path:
            self._send({"id": "m1", "model": body.get("model"), "role": "assistant",
                        "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                        "usage": {"input_tokens": 1, "output_tokens": 1}})
        else:
            self._send({"id": "c1", "object": "chat.completion", "model": body.get("model"),
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                                     "finish_reason": "stop"}]})


def call(method, path, payload=None, key=None, timeout=30):
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(GW + path, data, h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body or "{}")
        except Exception:
            return e.code, {"raw": body[:200]}


# ---------- khung giả lập trang (đủ cho index.html) ----------
PRELUDE = """
const _http = require("http"), _fs = require("fs");
globalThis.fetch = (u, o) => new Promise((res, rej) => {
  const url = new URL(u), h = Object.assign({}, (o && o.headers) || {});
  if (o && o.body) h["Content-Length"] = Buffer.byteLength(o.body);
  const req = _http.request({hostname:url.hostname, port:url.port||80, path:url.pathname+url.search,
                             method:(o&&o.method)||"GET", headers:h},
    r => { let b=""; r.setEncoding("utf8"); r.on("data",c=>b+=c);
           r.on("end",()=>res({ok:r.statusCode>=200&&r.statusCode<300, status:r.statusCode,
                               text:async()=>b, json:async()=>JSON.parse(b)})); });
  req.on("error", rej);
  if (o && o.body) req.write(o.body);
  req.end();
});
const _els = {};
function _mk(id){ const set = new Set(id==="setupFree"?["on"]:[]);
  return { id, value:"", textContent:"", innerHTML:"", disabled:false, style:{}, dataset:{}, checked:true,
    classList:{ add:c=>set.add(c), remove:c=>set.delete(c),
                toggle:c=>set.has(c)?set.delete(c):set.add(c), contains:c=>set.has(c) },
    focus(){}, select(){}, appendChild(){}, removeChild(){}, remove(){}, setAttribute(){},
    getAttribute(){return null}, insertAdjacentHTML(){}, addEventListener(){}, querySelector(){return _mk(id+"-c")} }; }
globalThis.document = {
  getElementById(id){ return _els[id] || (_els[id]=_mk(id)); },
  addEventListener(){}, createElement(){ return _mk("tmp"); },
  body:{ style:{}, appendChild(){}, removeChild(){} },
  documentElement:{ t:"dark", setAttribute(k,v){ this.t=v; }, getAttribute(){ return this.t; } },
  querySelectorAll(){ return []; }, execCommand(){ return true; } };
globalThis.localStorage = { d:{}, getItem(k){ return k in this.d ? this.d[k] : null; },
  setItem(k,v){ this.d[k]=String(v); }, removeItem(k){ delete this.d[k]; } };
globalThis.navigator = { userAgent:"node" };
globalThis.confirm = () => true;
globalThis.window = globalThis; globalThis.history = { replaceState(){} }; globalThis.scrollTo = ()=>{};
globalThis.location = { href:"%s/", origin:"%s", search:"", reload(){} };
const GW = "%s", MOCK = "%s";
""" % (GW, GW, GW, MOCK)

DRIVER = """
(async()=>{
  const $ = id => document.getElementById(id);
  const say = (k,v) => console.log(k + ": " + (typeof v === "string" ? v : JSON.stringify(v)));
  openSetup(); pickProv("openai");
  $("setupKey").value = "sk-live-000"; $("setupBase").value = MOCK;
  await loadSetupModels();
  await runSetup();
  const done = $("setupDone").innerHTML || "";
  const text = done.replace(/<[^>]+>/g," ").replace(/\\s+/g," ").trim();
  // khung giả lập không tự tạo phần tử con từ innerHTML: dựng lại ô chứa client key
  const km = done.match(/id="doneKey"[^>]*>([^<]+)</);
  if (km) $("doneKey").textContent = km[1].trim();
  const key  = ($("doneKey").textContent || "").trim();
  say("loi", ($("setupMsg").innerHTML||"").replace(/<[^>]+>/g," ").trim() || "(khong co)");
  say("an_form", $("setupForm").style.display === "none");
  say("ket_qua", text.slice(0, 400));
  say("co_nut_vao", /bảng điều khiển/i.test(done));
  say("key", key);
  _fs.writeFileSync("/tmp/gwui_key.txt", key);
  await finishSetup();
  await new Promise(r => setTimeout(r, 800));
  say("da_dang_nhap", $("loginView").style.display === "none" && $("appView").classList.contains("ready"));
  say("key_luu", localStorage.getItem("gw_client_key") || "");
  say("dia_chi_luu", localStorage.getItem("gw_base_url") || "");

  /* ---------- sao lưu / khôi phục ---------- */
  const strip = s => (s||"").replace(/<[^>]+>/g," ").replace(/\\s+/g," ").trim();
  const saved = [];
  URL.createObjectURL = b => { saved.push(b); return "blob:ui"; };
  URL.revokeObjectURL = () => {};
  await exportBackup();
  const xuatMsg = strip($("backupMsg").innerHTML);
  say("xuat_co_tep", /gateway-backup-\\d{8}-\\d{4}\\.json/.test(xuatMsg));
  say("xuat_co_canh_bao", /đừng chia sẻ/i.test(xuatMsg));
  const bkText = saved.length ? await saved[0].text() : "";
  let bk = {}; try { bk = JSON.parse(bkText); } catch(e) {}
  say("xuat_dung_dinh_dang", bk.format === "gateway-backup" && bk.version === 1);
  say("xuat_co_key_trong_file", typeof bk.client_key === "string" && bk.client_key.length > 20);
  say("xuat_du_du_lieu", (bk.api_keys||[]).length > 0 && (bk.models||[]).length > 0
       && Object.keys(bk.settings||{}).length > 5 && (bk.client_keys||[]).length > 0);
  const keyTruoc = CLIENT_KEY;

  await importBackupText("day khong phai json", "x.json");
  say("bao_file_hong", /không phải JSON hợp lệ/i.test(strip($("backupResult").innerHTML)));
  await importBackupText(JSON.stringify({ format:"khac" }), "x.json");
  say("bao_file_la", /không phải file sao lưu/i.test(strip($("backupResult").innerHTML)));
  say("sheet_dang_mo", $("backupSheet").classList.contains("open"));
  closeBackupSheet();
  say("sheet_da_dong", !$("backupSheet").classList.contains("open"));

  // nhập chính file vừa xuất, đổi một cài đặt để thấy nó được nạp
  bk.settings.rate_limit_rpm = "999";
  const r1 = await importBackupText(JSON.stringify(bk), "sao-luu.json");
  const nhapMsg = strip($("backupResult").innerHTML);
  say("nhap_xong", !!r1 && /Đã nhập xong/.test(nhapMsg));
  say("nhap_ghi_ten_tep", nhapMsg.indexOf("sao-luu.json") >= 0);
  say("nhap_giu_key_cu", CLIENT_KEY === keyTruoc && localStorage.getItem("gw_client_key") === keyTruoc);
  say("nhap_khong_doi_cong", /khởi động lại cổng/i.test(nhapMsg) === false);
  importEnterApp();
  await new Promise(r => setTimeout(r, 900));
  say("vao_bang_dieu_khien", $("loginView").style.display === "none" && $("appView").classList.contains("ready"));
  say("key_sau_nhap", CLIENT_KEY);
  const st = await api("/gateway/settings");
  say("cai_dat_sau_nhap", String(((st||{}).settings||{}).rate_limit_rpm));

  // file cũ không kèm client key: vẫn phải vào được, máy tự cấp key mới
  CLIENT_KEY = "";
  const bk2 = JSON.parse(bkText);
  delete bk2.client_key;
  bk2.settings.rate_limit_rpm = "888";
  const r2 = await importBackupText(JSON.stringify(bk2), "khong-key.json");
  const msg2 = strip($("backupResult").innerHTML);
  say("nhap_khong_key_bao_tao_moi", !!r2 && /đã tạo key mới/i.test(msg2));
  say("nhap_khong_key_co_key_moi", typeof CLIENT_KEY === "string" && CLIENT_KEY.length > 20);
  say("nhap_khong_key_luu_key", localStorage.getItem("gw_client_key") === CLIENT_KEY);
  say("key_moi", CLIENT_KEY);
})().catch(e => { console.log("loi_node: " + (e && e.message)); process.exit(3); });
"""


def main():
    if os.path.isdir(DST):
        shutil.rmtree(DST)
    os.makedirs(DST)
    for f in sorted(os.listdir(SRC)):
        if f in ("data", "Tests", ".git", "Documents", "__pycache__", "run.log", "README.md", "AGENTS.md"):
            continue
        s = os.path.join(SRC, f)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(DST, f))
        elif f.endswith((".py", ".json", ".txt", ".sh")):
            shutil.copy2(s, os.path.join(DST, f))

    class Srv(socketserver.TCPServer):
        allow_reuse_address = True

    upstream = Srv(("127.0.0.1", MOCK_PORT), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    subprocess.run([sys.executable, "-c",
                    "import db; db.init_db(); db.set_setting('port','%d'); db.set_setting('host','127.0.0.1')" % PORT],
                   cwd=DST, check=True)
    log = open("/tmp/gwui_srv.log", "w")
    proc = subprocess.Popen([sys.executable, "main.py"], cwd=DST, stdout=log, stderr=subprocess.STDOUT)

    up = False
    for _ in range(120):
        s = socket.socket()
        up = s.connect_ex(("127.0.0.1", PORT)) == 0
        s.close()
        if up:
            break
        time.sleep(0.1)
    check("bản gateway thật mở cổng %d" % PORT, up, open("/tmp/gwui_srv.log").read()[-400:])

    try:
        html = open(os.path.join(SRC, "static/index.html"), encoding="utf-8").read()
        js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
        open("/tmp/gwui_ui.js", "w", encoding="utf-8").write(PRELUDE + js + DRIVER)
        r = subprocess.run(["node", "/tmp/gwui_ui.js"], capture_output=True, text=True, timeout=120)
        out = {}
        for line in r.stdout.splitlines():
            if ": " in line:
                k, v = line.split(": ", 1)
                out[k.strip()] = v.strip()
        check("mã giao diện chạy được trong node", r.returncode == 0, r.stderr.strip()[-400:])
        check("thiết lập xong không báo lỗi", out.get("loi") in ("(khong co)", ""), out.get("loi"))
        check("form ẩn, màn kết quả hiện", out.get("an_form") == "true", out.get("an_form"))
        res = out.get("ket_qua", "")
        check("kết quả ghi đã kiểm tra key", "Đã kiểm tra key" in res, res[:200])
        check("kết quả ghi model mặc định là model trò chuyện", "Model mặc định mock-gpt-4o-mini" in res, res[:200])
        check("kết quả ghi chế độ chỉ model miễn phí", "chỉ model miễn phí" in res, res[:200])
        check("có nút vào bảng điều khiển", out.get("co_nut_vao") == "true", out.get("co_nut_vao"))
        key = out.get("key", "")
        check("màn kết quả đưa client key để dán", len(key) > 20, key)
        check("bấm vào bảng điều khiển là đăng nhập được", out.get("da_dang_nhap") == "true", out.get("da_dang_nhap"))
        check("client key được lưu lại trên máy", out.get("key_luu") == key, out.get("key_luu"))
        check("địa chỉ gateway được lưu lại", out.get("dia_chi_luu") == GW, out.get("dia_chi_luu"))

        code, m = call("GET", "/v1/models", key=key)
        ids = [x["id"] for x in (m.get("data") or [])]
        check("client key vừa cấp vào được /v1/models", code == 200, code)
        check("chỉ model miễn phí được bật", "mock-gpt-4o-mini" in ids and "mock-gpt-4o" not in ids, ids)

        UPSTREAM_MODELS.clear()
        code, out2 = call("POST", "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]}, key=key)
        check("chat không gửi tên model vẫn chạy", code == 200, out2.get("error"))
        check("chat tự dùng model mặc định trò chuyện", UPSTREAM_MODELS == ["mock-gpt-4o-mini"], UPSTREAM_MODELS)

        UPSTREAM_MODELS.clear()
        code, _ = call("POST", "/v1/messages", {"max_tokens": 20, "messages": [{"role": "user", "content": "hi"}]}, key=key)
        check("kiểu Claude không gửi tên model vẫn chạy", code == 200, code)
        check("kiểu Claude dùng đúng model mặc định", UPSTREAM_MODELS == ["mock-gpt-4o-mini"], UPSTREAM_MODELS)

        code, st = call("GET", "/gateway/status", key=key)
        check("bảng điều khiển gọi được /gateway/status", code == 200 and st.get("status") == "running", st)

        # ---- sao lưu / khôi phục qua chính mã giao diện ----
        check("giao diện xuất được file có tên", out.get("xuat_co_tep") == "true", out.get("xuat_co_tep"))
        check("giao diện nhắc file chứa key, đừng chia sẻ", out.get("xuat_co_canh_bao") == "true", out.get("xuat_co_canh_bao"))
        check("file xuất ra đúng định dạng Gateway", out.get("xuat_dung_dinh_dang") == "true", out.get("xuat_dung_dinh_dang"))
        check("file xuất ra có client key để máy mới dùng ngay",
              out.get("xuat_co_key_trong_file") == "true", out.get("xuat_co_key_trong_file"))
        check("file xuất ra đủ key, model, cài đặt", out.get("xuat_du_du_lieu") == "true", out.get("xuat_du_du_lieu"))
        check("file hỏng bị báo đúng", out.get("bao_file_hong") == "true", out.get("bao_file_hong"))
        check("file của phần mềm khác bị báo đúng", out.get("bao_file_la") == "true", out.get("bao_file_la"))
        check("hộp nhập mở ra khi báo lỗi rồi đóng được",
              out.get("sheet_dang_mo") == "true" and out.get("sheet_da_dong") == "true",
              (out.get("sheet_dang_mo"), out.get("sheet_da_dong")))
        check("nhập file báo đã nhập xong", out.get("nhap_xong") == "true", out.get("nhap_xong"))
        check("nhập file ghi rõ tên tệp", out.get("nhap_ghi_ten_tep") == "true", out.get("nhap_ghi_ten_tep"))
        keep_key = out.get("key_sau_nhap", "")
        check("nhập xong vẫn giữ client key đang dùng", out.get("nhap_giu_key_cu") == "true", keep_key[:8])
        check("cùng cổng thì không đòi khởi động lại", out.get("nhap_khong_doi_cong") == "true", out.get("nhap_khong_doi_cong"))
        check("nhập xong bấm vào là vào bảng điều khiển", out.get("vao_bang_dieu_khien") == "true",
              out.get("vao_bang_dieu_khien"))
        check("cài đặt trong file được nạp thật", out.get("cai_dat_sau_nhap") == "999", out.get("cai_dat_sau_nhap"))
        code, cfg = call("GET", "/gateway/settings", key=keep_key)
        check("client key giữ lại vẫn gọi được máy chủ", code == 200, code)
        new_key = out.get("key_moi", "")
        check("file cũ không kèm key thì tự cấp key mới", out.get("nhap_khong_key_bao_tao_moi") == "true"
              and out.get("nhap_khong_key_co_key_moi") == "true", new_key[:8])
        check("key mới được lưu lại trên máy", out.get("nhap_khong_key_luu_key") == "true", out.get("nhap_khong_key_luu_key"))
        code, _ = call("GET", "/gateway/status", key=new_key)
        check("key tự cấp dùng được ngay", code == 200, code)
        code, cfg2 = call("GET", "/gateway/settings", key=new_key)
        check("lần nhập sau ghi đè cài đặt lần trước", code == 200
              and str((cfg2.get("settings") or {}).get("rate_limit_rpm")) == "888",
              cfg2.get("settings", {}).get("rate_limit_rpm"))
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
        upstream.shutdown()
        log.close()
        shutil.rmtree(DST, ignore_errors=True)
        for f in ("/tmp/gwui_ui.js", "/tmp/gwui_key.txt", "/tmp/gwui_srv.log"):
            if os.path.exists(f):
                os.remove(f)

    failed = [r for r in RESULTS if not r[1]]
    print("\n%d/%d kiểm tra đạt" % (len(RESULTS) - len(failed), len(RESULTS)))
    for name, _, extra in failed:
        print(" - %s | %s" % (name, extra))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())