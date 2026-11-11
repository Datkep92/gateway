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

# ---- cài đặt mặc định ----
DEFAULT_SETTINGS = {
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
                      ("last_seen_at", "REAL"),
                      ("health", "TEXT NOT NULL DEFAULT 'unknown'"),
                      ("last_test_at", "REAL"),
                      ("last_test_message", "TEXT")):
        _ensure_column(c, "gateway_models", col, decl)
    # nâng cấp cột cho api_keys
    for col, decl in (("health", "TEXT NOT NULL DEFAULT 'unknown'"),
                      ("last_test_at", "REAL"),
                      ("last_test_message", "TEXT")):
        _ensure_column(c, "api_keys", col, decl)
    c.commit()

    # ---- nhà cung cấp ----
    # Chỉ seed nhà cung cấp tự nhập (custom); các nhà cung cấp có sẵn (openai,
    # anthropic, deepseek, opencode...) không tự thêm nữa — người dùng chủ động
    # thêm khi cần qua /gateway/onboard, và có thể xoá vĩnh viễn.
    deleted = _deleted_providers()
    for name, spec in P.PROVIDERS.items():
        if name != "custom":
            continue
        if name in deleted:
            continue
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
        deleted = _deleted_providers()
        for name, spec in P.PROVIDERS.items():
            if name != "custom":
                continue
            if name in deleted:
                continue
            for mid, free in P.classify(name, spec["default_models"]):
                c.execute("""INSERT INTO gateway_models(id,provider,display_name,enabled,context_window,supports_streaming,available,is_free,last_seen_at)
                             VALUES (?,?,?,?,?,1,0,?,?)""",
                          (mid, name, mid, 1 if free else 0, None, 1 if free else 0, now()))

    # ---- cài đặt mặc định ----
    for k, v in DEFAULT_SETTINGS.items():
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

def set_key_health(kid, health, message=""):
    c = conn()
    c.execute("UPDATE api_keys SET health=?, last_test_at=?, last_test_message=? WHERE id=?",
              (health, now(), (message or "")[:300], kid))
    c.commit()
    c.close()

def set_model_health(mid, health, message=""):
    c = conn()
    c.execute("UPDATE gateway_models SET health=?, last_test_at=?, last_test_message=? WHERE id=?",
              (health, now(), (message or "")[:300], mid))
    c.commit()
    c.close()

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

def _deleted_providers():
    raw = get_setting("deleted_providers", "") or ""
    return {x.strip() for x in raw.split(",") if x.strip()}

def delete_provider(name):
    """Xoá một nhà cung cấp cùng toàn bộ API key và model của nó."""
    n = (name or "").strip()
    if not n:
        return False
    # cho phép tên chuẩn hoặc tên đang có trong bảng providers
    c = conn()
    row = c.execute("SELECT name FROM providers WHERE name=?", (n,)).fetchone()
    if row is None:
        # thử chuẩn hoá qua canonical (chỉ built-in)
        try:
            import providers as P
            cn = P.canonical(n)
        except Exception:
            cn = None
        if cn:
            row = c.execute("SELECT name FROM providers WHERE name=?", (cn,)).fetchone()
            if row is not None:
                n = cn
    if row is None:
        c.close()
        return False
    c.execute("DELETE FROM api_keys WHERE provider=?", (n,))
    c.execute("DELETE FROM gateway_models WHERE provider=?", (n,))
    c.execute("DELETE FROM providers WHERE name=?", (n,))
    c.commit()
    c.close()
    # xoá vĩnh viễn nhà cung cấp mặc định: ghi nhớ để init_db không seed lại
    try:
        import providers as P
        if n in P.PROVIDERS and n != "custom":
            deleted = _deleted_providers()
            deleted.add(n)
            set_setting("deleted_providers", ",".join(sorted(deleted)))
    except Exception:
        pass
    # nếu model mặc định bị xoá theo, xoá setting để máy tự chọn lại
    dm = get_setting("default_model", "") or ""
    if dm:
        c = conn()
        exists = c.execute("SELECT 1 FROM gateway_models WHERE id=?", (dm,)).fetchone()
        c.close()
        if not exists:
            set_setting("default_model", "")
    return True

def delete_model(mid):
    """Xoá một model khỏi gateway."""
    mid = (mid or "").strip()
    if not mid:
        return False
    c = conn()
    row = c.execute("SELECT 1 FROM gateway_models WHERE id=?", (mid,)).fetchone()
    if row is None:
        c.close()
        return False
    c.execute("DELETE FROM gateway_models WHERE id=?", (mid,))
    c.commit()
    c.close()
    dm = get_setting("default_model", "") or ""
    if dm == mid:
        set_setting("default_model", "")
    return True
    return True


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

# ---------------------------------------------------------------- sao lưu / khôi phục
# File sao lưu là JSON mang đủ cấu hình để máy khác nhập vào là chạy ngay:
# cài đặt, nhà cung cấp + API key (nguyên văn), models, client key (chỉ hash)
# và client key đang dùng (nguyên văn) để máy mới vào thẳng bảng điều khiển.
BACKUP_FORMAT = "gateway-backup"
BACKUP_VERSION = 1

_BACKUP_TABLES = {
    "providers": ("providers", ("id", "name", "base_url", "enabled", "protocol",
                                "chat_path", "messages_path", "models_path", "key_url")),
    "api_keys": ("api_keys", ("id", "provider", "label", "key_value", "enabled", "priority",
                              "failure_count", "last_used_at", "last_error_at", "cooldown_until",
                              "created_at")),
    "gateway_models": ("models", ("id", "provider", "display_name", "enabled", "context_window",
                                  "supports_streaming", "available", "is_free", "last_seen_at")),
    "client_keys": ("client_keys", ("id", "name", "key_hash", "enabled", "created_at", "last_used_at")),
}

_BOOL_COLUMNS = {"enabled", "priority", "failure_count", "supports_streaming", "available", "is_free"}

class BackupError(Exception):
    """File sao lưu thiếu hoặc sai, không nhập được."""

def export_config(client_key=None):
    """Toàn bộ cấu hình + key, dạng ghi được ra file JSON."""
    c = conn()
    data = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "app": "Gateway",
        "exported_at": now(),
        "settings": {r["key"]: r["value"] for r in
                     c.execute("SELECT key,value FROM settings").fetchall()},
        "providers": [dict(r) for r in c.execute("SELECT * FROM providers").fetchall()],
        "api_keys": [dict(r) for r in c.execute("SELECT * FROM api_keys").fetchall()],
        "models": [dict(r) for r in c.execute("SELECT * FROM gateway_models").fetchall()],
        "client_keys": [dict(r) for r in c.execute(
            "SELECT id,name,key_hash,enabled,created_at,last_used_at FROM client_keys").fetchall()],
    }
    c.close()
    key = (client_key or "").strip()
    if key:
        data["client_key"] = key
    return data

def _backup_rows(data, section):
    rows = data.get(section)
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise BackupError("phan %s trong file khong hop le" % section)
    return [r for r in rows if isinstance(r, dict)]

def _fix_value(col, v):
    if v is None and col not in ("context_window", "last_used_at", "last_error_at",
                                 "cooldown_until", "created_at", "last_seen_at"):
        return None
    if col in _BOOL_COLUMNS:
        return 1 if (v is True or str(v) in ("1", "true", "True")) else 0
    if col in ("failure_count", "cooldown_until", "last_error_at"):
        # trạng thái lỗi cũ không mang sang máy mới
        return 0 if col == "failure_count" else None
    return v

def import_config(data, keep_client_key=None):
    """Thay toàn bộ cấu hình bằng nội dung file sao lưu.

    Trả {counts, client_key, client_key_source} — client_key là key dùng được
    để vào bảng điều khiển ngay trên máy mới.
    """
    if not isinstance(data, dict) or data.get("format") != BACKUP_FORMAT:
        raise BackupError("day khong phai file sao luu cua Gateway")
    ver = data.get("version")
    if not isinstance(ver, int) or ver < 1 or ver > BACKUP_VERSION:
        raise BackupError("phien ban file khong ho tro: %s" % (ver,))
    settings = data.get("settings")
    if not isinstance(settings, dict) or not settings:
        raise BackupError("file thieu phan cai dat")
    sections = {t: _backup_rows(data, s) for t, (s, _cols) in _BACKUP_TABLES.items()}

    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.isolation_level = None
    counts, skipped = {}, 0
    try:
        c.execute("BEGIN IMMEDIATE")
        for table, (section, cols) in _BACKUP_TABLES.items():
            c.execute("DELETE FROM %s" % table)
            n = 0
            for row in sections[table]:
                if not row.get(cols[0]):
                    skipped += 1
                    continue
                vals = [_fix_value(col, row.get(col)) for col in cols]
                try:
                    c.execute("INSERT OR REPLACE INTO %s(%s) VALUES (%s)"
                              % (table, ",".join(cols), ",".join("?" * len(cols))), vals)
                except sqlite3.Error:
                    skipped += 1
                    continue
                n += 1
            counts[section] = n

        c.execute("DELETE FROM settings")
        for k, v in settings.items():
            if k is None:
                continue
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (str(k), str(v)))
        # bản cũ thiếu setting mới: thêm mặc định, không ghi đè
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
        row = c.execute("SELECT value FROM settings WHERE key='port'").fetchone()
        try:
            port = int(row["value"]) if row else -1
        except (TypeError, ValueError):
            port = -1
        if not (0 <= port <= 65535):
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('port','15199')")
        c.execute("COMMIT")
    except Exception:
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        c.close()
    counts["settings"] = len(settings)
    if skipped:
        counts["skipped"] = skipped

    # luôn để lại một client key dùng được: ưu tiên key ghi trong file
    cand = (data.get("client_key") or "").strip()
    source = "file"
    if not cand:
        cand = (keep_client_key or "").strip()
        source = "request"
    if not cand:
        _cid, cand = new_client_key("khoi phuc")
        source = "new"
    else:
        c = conn()
        row = c.execute("SELECT id,enabled FROM client_keys WHERE key_hash=?",
                        (hash_key(cand),)).fetchone()
        if row is None:
            c.execute("INSERT INTO client_keys(id,name,key_hash,enabled,created_at) VALUES (?,?,?,1,?)",
                      (str(uuid.uuid4()),
                       "khoi phuc (tu file)" if source == "file" else "khoi phuc (dang dung)",
                       hash_key(cand), now()))
        elif not row["enabled"]:
            c.execute("UPDATE client_keys SET enabled=1 WHERE id=?", (row["id"],))
        c.commit()
        c.close()

    # giữ nguyên trạng thái bật/tắt của từng model như trong file:
    # chỉ tính lại cờ free theo luật của bản đang chạy, không áp lại chính sách
    recompute_model_flags()
    return {"counts": counts, "client_key": cand, "client_key_source": source}
