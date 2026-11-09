import os, sqlite3, time, json, hashlib, secrets, uuid

import providers as P

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "gateway.db")

def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    return c

def _columns(c, table):
    return {r[1] for r in c.execute("PRAGMA table_info(%s)" % table).fetchall()}

def _ensure_column(c, table, col, decl):
    if col not in _columns(c, table):
        c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))

def init_db():
    c = conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS providers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        base_url TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS api_keys (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        label TEXT NOT NULL,
        key_value TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        priority INTEGER NOT NULL DEFAULT 0,
        failure_count INTEGER NOT NULL DEFAULT 0,
        last_used_at REAL,
        last_error_at REAL,
        cooldown_until REAL,
        created_at REAL
    );
    CREATE TABLE IF NOT EXISTS gateway_models (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        display_name TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        context_window INTEGER,
        supports_streaming INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS client_keys (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        key_hash TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL,
        last_used_at REAL
    );
    CREATE TABLE IF NOT EXISTS request_logs (
        id TEXT PRIMARY KEY,
        timestamp REAL NOT NULL,
        client_key_id TEXT,
        provider TEXT,
        model TEXT,
        endpoint TEXT,
        status_code INTEGER,
        latency_ms INTEGER,
        stream INTEGER NOT NULL DEFAULT 0,
        key_id TEXT,
        error_code TEXT,
        error_message TEXT
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)
    # nâng cấp cột cho các bản cũ
    for col, decl in (("created_at", "REAL"),):
        _ensure_column(c, "api_keys", col, decl)
    for col, decl in (("protocol", "TEXT"), ("chat_path", "TEXT"), ("messages_path", "TEXT"),
                      ("models_path", "TEXT"), ("key_url", "TEXT")):
        _ensure_column(c, "providers", col, decl)
    for col, decl in (("available", "INTEGER NOT NULL DEFAULT 1"),
                      ("is_free", "INTEGER NOT NULL DEFAULT 0"),
                      ("last_seen_at", "REAL")):
        _ensure_column(c, "gateway_models", col, decl)
    c.commit()

    # ---- nhà cung cấp ----
    for name, spec in P.PROVIDERS.items():
        row = c.execute("SELECT id, base_url FROM providers WHERE name=?", (name,)).fetchone()
        if row is None:
            c.execute("""INSERT INTO providers(id,name,base_url,enabled,protocol,chat_path,messages_path,models_path,key_url)
                         VALUES (?,?,?,1,?,?,?,?,?)""",
                      (str(uuid.uuid4()), name, spec["base_url"], spec["protocol"],
                       spec["chat_path"], spec["messages_path"], spec["models_path"], spec["key_url"]))
        else:
            if spec.get("dynamic"):
                # nhà cung cấp tự nhập: giao thức/đường dẫn do lúc thiết lập quyết định
                continue
            c.execute("""UPDATE providers SET protocol=?,chat_path=?,messages_path=?,models_path=?,key_url=? WHERE name=?""",
                      (spec["protocol"], spec["chat_path"], spec["messages_path"], spec["models_path"], spec["key_url"], name))

    # ---- model mặc định khi bảng còn trống ----
    if c.execute("SELECT COUNT(*) FROM gateway_models").fetchone()[0] == 0:
        for name, spec in P.PROVIDERS.items():
            for mid, free in P.classify(name, spec["default_models"]):
                c.execute("""INSERT INTO gateway_models(id,provider,display_name,enabled,context_window,supports_streaming,available,is_free,last_seen_at)
                             VALUES (?,?,?,?,?,1,0,?,?)""",
                          (mid, name, mid, 1 if free else 0, None, 1 if free else 0, now()))

    # ---- cài đặt mặc định ----
    defaults = {
        "port": "15199",
        "host": "127.0.0.1",
        "background": "0",
        "client_key_required": "1",
        "log_retention_days": "7",
        "max_log_count": "5000",
        "cors_enabled": "1",
        "lan_enabled": "0",
        "log_body_enabled": "0",
        "tls_enabled": "0",
        "tls_mode": "none",   # none|selfsigned|imported
        "tls_cert_path": "",
        "tls_key_path": "",
        "rate_limit_enabled": "1",
        "rate_limit_rpm": "60",
        "rate_limit_burst": "10",
        # chỉ dùng model miễn phí / rẻ nhất, bật sẵn
        "free_models_only": "1",
        "auto_discover_models": "1",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
    c.commit()
    c.close()

    recompute_model_flags()
    apply_models_policy()

def get_setting(key, default=None):
    c = conn()
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    c.close()
    return row["value"] if row else default

def set_setting(key, value):
    c = conn()
    c.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    c.commit()
    c.close()

def all_settings():
    c = conn()
    rows = c.execute("SELECT key,value FROM settings").fetchall()
    c.close()
    return {r["key"]: r["value"] for r in rows}

# ---- model: đánh dấu free / còn dùng được / bật tắt theo chính sách ----
def recompute_model_flags():
    c = conn()
    for r in c.execute("SELECT id,provider,is_free FROM gateway_models").fetchall():
        free = 1 if P.is_free(r["provider"], r["id"]) else 0
        if r["is_free"] != free:
            c.execute("UPDATE gateway_models SET is_free=? WHERE id=?", (free, r["id"]))
    c.commit()
    c.close()

def apply_models_policy(provider=None):
    """Áp chính sách: chỉ model miễn phí, hay tất cả model đã xác nhận."""
    free_only = get_setting("free_models_only", "1") == "1"
    c = conn()
    where_extra = " AND provider=?" if provider else ""
    args = (provider,) if provider else ()
    if free_only:
        c.execute("UPDATE gateway_models SET enabled=0 WHERE is_free=0 AND available=1" + where_extra, args)
        c.execute("UPDATE gateway_models SET enabled=1 WHERE is_free=1 AND available=1" + where_extra, args)
    else:
        c.execute("UPDATE gateway_models SET enabled=1 WHERE available=1" + where_extra, args)
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM gateway_models WHERE enabled=1 AND available=1").fetchone()[0]
    c.close()
    return n

def upsert_models(provider, model_ids, mark_missing_unavailable=True):
    """Ghi danh sách model dò được từ nhà cung cấp; không phá lựa chọn cũ."""
    free_only = get_setting("free_models_only", "1") == "1"
    pair = P.classify(provider, model_ids)
    t = now()
    c = conn()
    for mid, free in pair:
        row = c.execute("SELECT id FROM gateway_models WHERE id=?", (mid,)).fetchone()
        if row:
            c.execute("""UPDATE gateway_models SET provider=?, available=1, is_free=?, last_seen_at=?,
                         display_name=CASE WHEN display_name IS NULL OR display_name='' THEN ? ELSE display_name END
                         WHERE id=?""", (provider, 1 if free else 0, t, mid, mid))
        else:
            enabled = 1 if (free or not free_only) else 0
            c.execute("""INSERT INTO gateway_models(id,provider,display_name,enabled,context_window,supports_streaming,available,is_free,last_seen_at)
                         VALUES (?,?,?,?,NULL,1,1,?,?)""",
                      (mid, provider, mid, enabled, 1 if free else 0, t))
    if mark_missing_unavailable and pair:
        ids = [m for m, _ in pair]
        q = ",".join("?" * len(ids))
        c.execute("UPDATE gateway_models SET available=0 WHERE provider=? AND id NOT IN (%s)" % q, [provider] + ids)
    c.commit()
    c.close()
    return len(pair)

def list_models(enabled_only=False):
    c = conn()
    q = "SELECT * FROM gateway_models"
    if enabled_only:
        q += " WHERE enabled=1 AND available=1"
    rows = c.execute(q + " ORDER BY provider, is_free DESC, id").fetchall()
    c.close()
    return rows

# ---- provider ----
def set_provider_base_url(name, url):
    n = P.canonical(name)
    if n is None or not url:
        return False
    c = conn()
    c.execute("UPDATE providers SET base_url=? WHERE name=?", (str(url).rstrip("/"), n))
    c.commit()
    c.close()
    return True

def update_provider(name, **fields):
    """Cập nhật vài cột của một provider (dùng cho nhà cung cấp tự nhập)."""
    n = P.canonical(name)
    allowed = ("base_url", "protocol", "chat_path", "messages_path", "models_path", "key_url", "enabled")
    cols = [(k, v) for k, v in fields.items() if k in allowed]
    if n is None or not cols:
        return False
    if "base_url" in dict(cols):
        cols = [(k, (str(v).rstrip("/") if k == "base_url" else v)) for k, v in cols]
    c = conn()
    c.execute("UPDATE providers SET " + ",".join("%s=?" % k for k, _ in cols) + " WHERE name=?",
              [v for _, v in cols] + [n])
    c.commit()
    c.close()
    return True

def list_providers():
    c = conn()
    rows = c.execute("""SELECT p.name, p.base_url, p.protocol, p.chat_path, p.messages_path, p.models_path,
                               p.key_url, p.enabled,
                               (SELECT COUNT(*) FROM api_keys k WHERE k.provider=p.name AND k.enabled=1) AS key_count,
                               (SELECT COUNT(*) FROM gateway_models m WHERE m.provider=p.name AND m.enabled=1 AND m.available=1) AS model_count
                        FROM providers p ORDER BY p.name""").fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        spec = P.spec(r["name"]) or {}
        d["label"] = spec.get("label", r["name"])
        d["key_hint"] = spec.get("key_hint", "")
        d["free_note"] = spec.get("free_note", "")
        d["connected"] = bool(d["key_count"])
        out.append(d)
    return out

# helpers
def now(): return time.time()

def hash_key(plaintext):
    return hashlib.sha256(plaintext.encode()).hexdigest()

def new_client_key(name):
    raw = secrets.token_urlsafe(32)
    cid = str(uuid.uuid4())
    c = conn()
    c.execute("INSERT INTO client_keys(id,name,key_hash,enabled,created_at) VALUES (?,?,?,?,?)",
              (cid, name, hash_key(raw), 1, now()))
    c.commit()
    c.close()
    return cid, raw

def validate_client_key(plaintext):
    if not plaintext:
        return None
    h = hash_key(plaintext)
    c = conn()
    row = c.execute("SELECT id,enabled FROM client_keys WHERE key_hash=? AND enabled=1", (h,)).fetchone()
    if row:
        c.execute("UPDATE client_keys SET last_used_at=? WHERE id=?", (now(), row["id"]))
        c.commit()
    c.close()
    return row["id"] if row else None
