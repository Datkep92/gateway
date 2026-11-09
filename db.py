import os, sqlite3, time, json, hashlib, secrets, uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "gateway.db")

def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL;")
    return c

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
        cooldown_until REAL
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
    # seed providers
    for name, url in [("openai","https://api.openai.com"),("anthropic","https://api.anthropic.com")]:
        if not c.execute("SELECT 1 FROM providers WHERE name=?", (name,)).fetchone():
            c.execute("INSERT INTO providers(id,name,base_url,enabled) VALUES (?,?,?,1)", (str(uuid.uuid4()), name, url))
    # seed models if empty
    if c.execute("SELECT COUNT(*) FROM gateway_models").fetchone()[0] == 0:
        seeds = [
            ("gpt-4o","openai","GPT-4o",1,128000,1),
            ("gpt-4o-mini","openai","GPT-4o mini",1,128000,1),
            ("claude-3-5-sonnet-20241022","anthropic","Claude 3.5 Sonnet",1,200000,1),
            ("claude-3-5-haiku-20241022","anthropic","Claude 3.5 Haiku",1,200000,1),
        ]
        for m in seeds:
            c.execute("INSERT INTO gateway_models(id,provider,display_name,enabled,context_window,supports_streaming) VALUES (?,?,?,?,?,?)", m)
    # default settings
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
    }
    for k,v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k,v))
    c.commit()
    c.close()

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
