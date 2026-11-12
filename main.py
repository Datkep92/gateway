import db, server, os, subprocess, sys

def ensure_client_key():
    c = db.conn()
    n = c.execute("SELECT COUNT(*) FROM client_keys WHERE enabled=1").fetchone()[0]
    c.close()
    if n == 0:
        cid, raw = db.new_client_key("default")
        print("="*60)
        print("Generated default client key (shown once):")
        print(raw)
        print("Save this key. It will not be shown again.")
        print("="*60)

def ensure_tls_cert():
    cert = db.get_setting("tls_cert_path", "")
    key = db.get_setting("tls_key_path", "")
    mode = db.get_setting("tls_mode", "none")
    if mode == "selfsigned" and (not cert or not os.path.exists(cert)):
        d = os.path.join(db.DATA_DIR, "tls")
        os.makedirs(d, exist_ok=True)
        cert = os.path.join(d, "cert.pem")
        key = os.path.join(d, "key.pem")
        if not (os.path.exists(cert) and os.path.exists(key)):
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-keyout", key, "-out", cert, "-days", "365",
                            "-subj", "/CN=gateway.local"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        db.set_setting("tls_cert_path", cert)
        db.set_setting("tls_key_path", key)
    return cert, key

def main():
    db.init_db()
    ensure_client_key()
    host = db.get_setting("host", "127.0.0.1")
    if db.get_setting("lan_enabled", "0") == "1":
        host = "0.0.0.0"
    port = int(db.get_setting("port", "15199"))
    if not (0 <= port <= 65535):
        print(f"Port {port} không hợp lệ, dùng 15199")
        port = 15199
        db.set_setting("port", "15199")
    tls_enabled = db.get_setting("tls_enabled", "0") == "1"
    cert, key = ("", "")
    if tls_enabled:
        cert, key = ensure_tls_cert()
    print(f"Gateway starting on {host}:{port} (tls={tls_enabled})", flush=True)
    httpd = server.make_server(host, port, use_tls=tls_enabled, certfile=cert or None, keyfile=key or None)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down...")
        httpd.shutdown()

if __name__ == "__main__":
    main()
