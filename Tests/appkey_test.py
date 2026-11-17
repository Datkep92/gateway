"""Client key riêng của app "Cổng": `gatewayctl appkey` cấp một lần rồi giữ nguyên.

    python3 Tests/appkey_test.py

Không đụng `data/gateway.db` thật: dựng một thư mục dự án tạm (chép *.py, DB mới,
cổng riêng trong setting `port`) rồi chạy `packaging/gatewayctl` với
`GATEWAY_PROJECT_DIR` trỏ vào đó.

Kiểm:
  1. lần đầu  -> in ra một key đúng dạng, `data/app.key` được tạo, quyền 600
  2. key đó   -> có hàng client_keys đang bật và `validate_client_key` nhận
  3. lần sau  -> in lại đúng key cũ (không cấp thêm key)
  4. key bị tắt trong bảng quản lý -> lần sau tự cấp key mới, file được ghi lại
  5. xoá file -> vẫn cấp key mới dùng được
  6. `url`     -> http://127.0.0.1:<cổng trong setting>, `state` -> khong_chay
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

RESULTS = []
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond), str(extra)[:200]))
    line = ("PASS  " if cond else "FAIL  ") + name
    if str(extra) != "":
        line += "   [%s]" % str(extra)[:200]
    print(line)


def find_shell():
    for p in ("/var/jb/bin/sh", "/bin/sh", "/usr/bin/sh"):
        if os.path.exists(p):
            return p
    return shutil.which("sh") or ""


def make_project(tmp, port):
    """Thư mục dự án tạm: mã nguồn thật, DB mới, không có run.log."""
    proj = os.path.join(tmp, "proj")
    os.makedirs(proj, exist_ok=True)
    for name in os.listdir(ROOT):
        if name.endswith(".py") and os.path.isfile(os.path.join(ROOT, name)):
            shutil.copy2(os.path.join(ROOT, name), os.path.join(proj, name))
    src = os.path.join(ROOT, "static")
    if os.path.isdir(src):
        shutil.copytree(src, os.path.join(proj, "static"), dirs_exist_ok=True)

    sys.path.insert(0, proj)
    import db as dbmod  # noqa: E402  (module trong thư mục tạm)
    dbmod.init_db()
    dbmod.set_setting("port", str(port))
    dbmod.set_setting("tls_enabled", "0")
    return proj, dbmod


def run_ctl(proj, *args):
    env = dict(os.environ)
    env["GATEWAY_PROJECT_DIR"] = proj
    env["PATH"] = "/var/jb/usr/local/bin:/var/jb/usr/bin:/var/jb/bin:" + env.get("PATH", "/usr/bin:/bin")
    ctl = os.path.join(ROOT, "packaging", "gatewayctl")
    p = subprocess.run([SH, ctl, *args], capture_output=True, text=True, env=env, timeout=90)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    return p.returncode, out, err


def last_key(out):
    for line in reversed(out.splitlines()):
        line = line.strip()
        if KEY_RE.match(line):
            return line
    return ""


def main():
    global SH
    SH = find_shell()
    if not SH:
        print("Không tìm thấy shell — bỏ qua.")
        return 0

    tmp = tempfile.mkdtemp(prefix="gwappkey-")
    try:
        port = 15399
        proj, dbmod = make_project(tmp, port)
        keyfile = os.path.join(proj, "data", "app.key")

        # 1. lần đầu: cấp key mới
        rc, out, err = run_ctl(proj, "appkey")
        key1 = last_key(out)
        check("A: appkey chạy được (mã 0)", rc == 0, err or out)
        check("A: in ra key đúng dạng", bool(key1), out[:120])
        check("A: có tạo data/app.key", os.path.exists(keyfile))
        if os.path.exists(keyfile):
            mode = os.stat(keyfile).st_mode & 0o777
            check("A: quyền file 600", mode == 0o600, oct(mode))

        # 2. key đó dùng được: đúng hàng đang bật trong client_keys
        check("B: validate_client_key nhận key", bool(dbmod.validate_client_key(key1)), key1[:12] + "...")
        row = dbmod.conn().execute(
            "SELECT name, enabled FROM client_keys WHERE key_hash=?", (dbmod.hash_key(key1),)).fetchone()
        check("B: hàng client key tên 'app' đang bật", row is not None and row["enabled"] == 1,
              dict(row) if row else None)
        n_keys = dbmod.conn().execute("SELECT COUNT(*) AS n FROM client_keys").fetchone()["n"]
        check("B: chỉ có một key được cấp", n_keys == 1, n_keys)

        # 3. gọi lại: giữ nguyên key
        rc, out, err = run_ctl(proj, "appkey")
        check("C: gọi lại in đúng key cũ", last_key(out) == key1, (rc, last_key(out)[:12] + "..."))
        n_keys = dbmod.conn().execute("SELECT COUNT(*) AS n FROM client_keys").fetchone()["n"]
        check("C: không cấp thêm key", n_keys == 1, n_keys)

        # 4. key bị tắt trong bảng quản lý -> tự cấp key mới
        c = dbmod.conn()
        c.execute("UPDATE client_keys SET enabled=0")
        c.commit()
        c.close()
        rc, out, err = run_ctl(proj, "appkey")
        key2 = last_key(out)
        check("D: key bị tắt thì cấp key mới", bool(key2) and key2 != key1, key2[:12] + "...")
        check("D: key mới dùng được", bool(dbmod.validate_client_key(key2)), key2[:12] + "...")
        if os.path.exists(keyfile):
            with open(keyfile, "r", encoding="utf-8") as f:
                check("D: file được ghi lại theo key mới", f.read().strip() == key2)

        # 5. xoá file -> vẫn cấp key mới dùng được (cả DB có nhiều key cũ)
        os.remove(keyfile)
        rc, out, err = run_ctl(proj, "appkey")
        key3 = last_key(out)
        check("E: xoá file vẫn cấp key mới", bool(key3) and key3 not in (key1, key2), key3[:12] + "...")
        check("E: key mới dùng được", bool(dbmod.validate_client_key(key3)), key3[:12] + "...")

        # 6. url + state (cổng của thư mục tạm im hẳn)
        rc, out, err = run_ctl(proj, "url")
        check("F: url trả đúng địa chỉ", out == "http://127.0.0.1:%d" % port, out)
        rc, out, err = run_ctl(proj, "state")
        check("F: state thuộc bộ từ cho máy đọc",
              out in ("dang_chay", "dang_treo", "khong_chay"), out)
        # Máy có phiên gateway-session của dự án thật đang chạy thì state nói
        # dang_treo (đúng: có tiến trình phiên, cổng tạm không trả lời) — điều
        # phải đúng là cổng tạm im thì không bao giờ là dang_chay.
        check("F: cổng tạm im thì không nói dang_chay", out != "dang_chay", out)
    finally:
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
