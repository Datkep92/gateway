"""Chọn và xoay API key thượng nguồn.

Key lỗi tạm thời thì nghỉ vài chục giây rồi tự dùng lại; key sai (401/403) thì
tắt và hẹn giờ thử lại, để tác vụ nền tự bật lại khi key sống lại (nạp thêm
tiền, key được cấp lại...). Người dùng tắt key bằng tay thì không ai tự bật.
"""
import db, time, uuid

COOLDOWN_STEPS = [30, 60, 120, 300, 600]

def list_keys(provider=None):
    c = db.conn()
    if provider:
        rows = c.execute("SELECT * FROM api_keys WHERE provider=? ORDER BY priority", (provider,)).fetchall()
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

def update_key(kid, label=None, priority=None):
    c = db.conn()
    if label is not None:
        c.execute("UPDATE api_keys SET label=? WHERE id=?", (label, kid))
    if priority is not None:
        c.execute("UPDATE api_keys SET priority=? WHERE id=?", (priority, kid))
    c.commit()
    c.close()

def set_enabled(kid, enabled):
    """Người dùng bật/tắt key bằng tay: tắt luôn việc tự thử lại."""
    c = db.conn()
    c.execute("UPDATE api_keys SET enabled=?, auto_disabled=0, retry_at=NULL WHERE id=?",
              (1 if enabled else 0, kid))
    c.commit()
    c.close()

def set_retry(kid, seconds):
    """Hẹn giờ tác vụ nền thử lại key này."""
    c = db.conn()
    c.execute("UPDATE api_keys SET retry_at=? WHERE id=?", (db.now() + float(seconds), kid))
    c.commit()
    c.close()

def reenable_auto(kid):
    """Key do hệ thống tắt vì lỗi mà đã sống lại thì bật lại; key người dùng tự tắt thì để nguyên.

    -> True nếu key vừa được bật lại (bên gọi dùng để hẹn kiểm lại model).
    """
    c = db.conn()
    cur = c.execute("""UPDATE api_keys SET enabled=1, auto_disabled=0, retry_at=NULL
                       WHERE id=? AND enabled=0 AND auto_disabled=1""", (kid,))
    n = cur.rowcount
    c.commit()
    c.close()
    return n > 0

def due_keys(limit=4, force=False):
    """Key bị hệ thống tắt vì lỗi và đã tới hạn thử lại (force=True: bỏ qua hẹn giờ)."""
    c = db.conn()
    if force:
        rows = c.execute("""SELECT * FROM api_keys WHERE enabled=0 AND auto_disabled=1
                            ORDER BY retry_at IS NOT NULL, retry_at, provider, priority LIMIT ?""",
                         (int(limit),)).fetchall()
    else:
        rows = c.execute("""SELECT * FROM api_keys WHERE enabled=0 AND auto_disabled=1
                            AND (retry_at IS NULL OR retry_at <= ?)
                            ORDER BY retry_at, provider, priority LIMIT ?""",
                         (db.now(), int(limit))).fetchall()
    c.close()
    return [dict(r) for r in rows]

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
    c.execute("""UPDATE api_keys SET failure_count=0, last_used_at=?, cooldown_until=NULL,
                 auto_disabled=0, retry_at=NULL WHERE id=?""", (db.now(), kid))
    c.commit()
    c.close()

def mark_failure(kid, status=None):
    c = db.conn()
    row = c.execute("SELECT failure_count FROM api_keys WHERE id=?", (kid,)).fetchone()
    fc = (row["failure_count"] if row else 0) + 1
    idx = min(fc-1, len(COOLDOWN_STEPS)-1)
    cooldown = db.now() + COOLDOWN_STEPS[idx]
    # 401/403 -> key sai: tắt và hẹn tác vụ nền thử lại
    if status in (401, 403):
        retry = db.now() + db.float_setting("key_retry_secs", 1800)
        c.execute("""UPDATE api_keys SET enabled=0, auto_disabled=1, failure_count=?, last_error_at=?,
                     cooldown_until=?, retry_at=? WHERE id=?""",
                  (fc, db.now(), cooldown, retry, kid))
    else:
        c.execute("UPDATE api_keys SET failure_count=?, last_error_at=?, cooldown_until=?, last_used_at=? WHERE id=?",
                  (fc, db.now(), cooldown, db.now(), kid))
    c.commit()
    c.close()
    return fc, cooldown

def enabled_key_count(provider):
    c = db.conn()
    row = c.execute("SELECT COUNT(*) FROM api_keys WHERE provider=? AND enabled=1", (provider,)).fetchone()
    c.close()
    return int(row[0]) if row else 0

def wait_ready(provider):
    """Số giây phải chờ cho key sớm nhất của ncc này hết nghỉ.

    -> 0 nếu đang có key dùng được ngay; None nếu ncc không có key nào đang bật
    (chờ cũng vô ích).
    """
    c = db.conn()
    now = db.now()
    row = c.execute("""SELECT MIN(cooldown_until) AS w FROM api_keys WHERE provider=? AND enabled=1
                       AND cooldown_until IS NOT NULL AND cooldown_until > ?""", (provider, now)).fetchone()
    n = c.execute("SELECT COUNT(*) FROM api_keys WHERE provider=? AND enabled=1", (provider,)).fetchone()[0]
    c.close()
    if not n:
        return None
    if row is None or row["w"] is None:
        return 0.0
    return max(0.0, float(row["w"]) - now)

def all_in_cooldown(provider):
    """Không còn key nào dùng được ngay (đang nghỉ hết) — nhưng vẫn có key đang bật."""
    w = wait_ready(provider)
    return w is not None and w > 0
