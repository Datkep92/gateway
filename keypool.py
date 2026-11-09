import db, time, uuid

COOLDOWN_STEPS = [30, 60, 120, 300, 600]

def list_keys(provider=None):
    c = db.conn()
    if provider:
        rows = c.execute("SELECT * FROM api_keys WHERE provider=? ORDER BY priority, created_at" if False else "SELECT * FROM api_keys WHERE provider=? ORDER BY priority", (provider,)).fetchall()
    else:
        rows = c.execute("SELECT * FROM api_keys ORDER BY provider,priority").fetchall()
    c.close()
    return [dict(r) for r in rows]

def add_key(provider, label, key_value, priority=0):
    kid = str(uuid.uuid4())
    c = db.conn()
    c.execute("INSERT INTO api_keys(id,provider,label,key_value,enabled,priority,failure_count) VALUES (?,?,?,?,1,?,0)",
              (kid, provider, label, key_value, priority))
    c.commit()
    c.close()
    return kid

def delete_key(kid):
    c = db.conn()
    c.execute("DELETE FROM api_keys WHERE id=?", (kid,))
    c.commit()
    c.close()

def set_enabled(kid, enabled):
    c = db.conn()
    c.execute("UPDATE api_keys SET enabled=? WHERE id=?", (1 if enabled else 0, kid))
    c.commit()
    c.close()

def select_key(provider):
    c = db.conn()
    now = db.now()
    rows = c.execute("""SELECT * FROM api_keys WHERE provider=? AND enabled=1
        AND (cooldown_until IS NULL OR cooldown_until <= ?)""", (provider, now)).fetchall()
    c.close()
    if not rows:
        return None
    # sort by failure_count asc, last_used_at asc (null first), priority asc
    def key(r):
        lu = r["last_used_at"] if r["last_used_at"] is not None else 0
        return (r["failure_count"], lu, r["priority"])
    rows_sorted = sorted(rows, key=key)
    return dict(rows_sorted[0])

def mark_success(kid):
    c = db.conn()
    c.execute("UPDATE api_keys SET failure_count=0, last_used_at=?, cooldown_until=NULL WHERE id=?", (db.now(), kid))
    c.commit()
    c.close()

def mark_failure(kid, status=None):
    c = db.conn()
    row = c.execute("SELECT failure_count FROM api_keys WHERE id=?", (kid,)).fetchone()
    fc = (row["failure_count"] if row else 0) + 1
    idx = min(fc-1, len(COOLDOWN_STEPS)-1)
    cooldown = db.now() + COOLDOWN_STEPS[idx]
    # 401/403 -> disable
    if status in (401, 403):
        c.execute("UPDATE api_keys SET enabled=0, failure_count=?, last_error_at=?, cooldown_until=? WHERE id=?",
                  (fc, db.now(), cooldown, kid))
    else:
        c.execute("UPDATE api_keys SET failure_count=?, last_error_at=?, cooldown_until=?, last_used_at=? WHERE id=?",
                  (fc, db.now(), cooldown, db.now(), kid))
    c.commit()
    c.close()
    return fc, cooldown

def all_in_cooldown(provider):
    c = db.conn()
    now = db.now()
    row = c.execute("""SELECT COUNT(*) FROM api_keys WHERE provider=? AND enabled=1
        AND (cooldown_until IS NULL OR cooldown_until <= ?)""", (provider, now)).fetchone()
    c.close()
    return row[0] == 0
