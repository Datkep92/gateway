"""Tác vụ nền tự kiểm model và key, để người dùng không phải bấm kiểm bằng tay.

Nhờ đây, chỉ cần thêm nhà cung cấp rồi dùng địa chỉ gateway:

- model mới dò được, model đang bị đánh dấu lỗi, model lâu chưa kiểm -> tự thử
  bằng một câu hỏi rất ngắn; chỉ lỗi thuộc về model (không tồn tại, không có
  quyền dùng) mới bị loại khỏi danh sách;
- key bị hệ thống tắt vì lỗi (401/403) -> tự thử lại sau `key_retry_secs`, sống
  lại thì tự bật lại; key người dùng tự tắt thì không ai tự bật;
- lỗi tạm thời (429, 5xx, mạng) không kết luận model hỏng, chỉ hẹn kiểm lại sớm.

Vòng nền bật bằng `start()` (main.py gọi), tắt bằng cài đặt `auto_check`.
`run_once()` chạy đúng một vòng — dùng cho nút "kiểm ngay" và cho bài thử.
"""
import threading
import time

import db
import keypool
import proxy

CHECK_TIMEOUT = 30        # mỗi lượt kiểm model chờ tối đa bao lâu
KEY_BATCH = 4             # số key thử lại mỗi vòng
IDLE_WHEN_OFF = 30        # chu kỳ chờ khi tác vụ đang tắt (giây)
BURST_WAIT = 3            # còn việc tồn đọng thì nghỉ ngắn rồi làm tiếp

_lock = threading.Lock()
_wake = threading.Event()
_stop = threading.Event()
_force = False
_thread = None

STATE = {
    "busy": False,        # đang chạy một vòng
    "running": False,     # vòng nền có đang sống
    "rounds": 0,          # số vòng đã chạy
    "last_finish": 0.0,   # lúc xong vòng gần nhất
    "last_models": 0,     # số model đã kiểm ở vòng gần nhất
    "last_keys": 0,       # số key đã thử lại ở vòng gần nhất
    "last_message": "",
    "next_at": 0.0,       # lúc vòng kế tiếp dự kiến chạy
    "last_error": "",
}


def _enabled():
    return db.get_setting("auto_check", "1") == "1"


def _batch():
    try:
        n = int(db.float_setting("auto_check_batch", 8))
    except (TypeError, ValueError):
        n = 8
    return max(1, min(n, 24))


def status():
    """Trạng thái tác vụ nền cho giao diện (một dict, an toàn với JSON)."""
    d = dict(STATE)
    d["enabled"] = _enabled()
    d["period"] = int(db.float_setting("auto_check_secs", 300))
    d["pending_models"] = db.pending_model_count()
    d["due_keys"] = len(keypool.due_keys(50))
    d["now"] = time.time()
    return d


def wake(force=False):
    """Báo vòng nền có việc mới (thêm nhà cung cấp, dò ra model mới).

    force=True: bỏ qua hẹn giờ, kiểm ngay cả thứ vừa kiểm xong.
    """
    global _force
    if force:
        _force = True
    _wake.set()


def _reserve():
    """Giữ chỗ một vòng; False nếu vòng trước chưa xong."""
    with _lock:
        if STATE["busy"]:
            return False
        STATE["busy"] = True
        return True


def _release():
    with _lock:
        STATE["busy"] = False


def run_once(force=False, batch=None, key_batch=KEY_BATCH):
    """Chạy một vòng tự kiểm ngay tại chỗ. -> dict kết quả, None nếu đang bận."""
    if not _reserve():
        return None
    try:
        return _round(force=force, batch=batch, key_batch=key_batch)
    finally:
        _release()


def run_async(force=True, batch=None, key_batch=KEY_BATCH):
    """Chạy một vòng ở luồng riêng (nút "kiểm ngay"). -> True nếu đã bắt đầu."""
    if not _reserve():
        return False

    def go():
        try:
            _round(force=force, batch=batch, key_batch=key_batch)
        except Exception as e:  # không để luồng chết vì một lỗi lẻ
            STATE["last_error"] = str(e)
        finally:
            _release()

    threading.Thread(target=go, name="autocheck-once", daemon=True).start()
    return True


def _round(force=False, batch=None, key_batch=KEY_BATCH):
    if batch is None:
        batch = _batch()
    batch = max(1, min(int(batch), 24))
    key_batch = max(1, min(int(key_batch), 24))

    n_models = n_model_ok = 0
    for m in db.due_models(batch, force=force):
        if _stop.is_set():
            break
        ok, _msg = proxy.test_model(m["id"], timeout=CHECK_TIMEOUT)
        n_models += 1
        n_model_ok += 1 if ok else 0

    n_keys = n_key_ok = 0
    for k in keypool.due_keys(key_batch, force=force):
        if _stop.is_set():
            break
        ok, _msg = proxy.test_key_by_id(k["id"])
        n_keys += 1
        n_key_ok += 1 if ok else 0

    msg = "kiểm %d model (%d dùng được), thử lại %d key (%d sống)" % (
        n_models, n_model_ok, n_keys, n_key_ok)
    STATE.update({"rounds": STATE["rounds"] + 1, "last_finish": time.time(),
                  "last_models": n_models, "last_keys": n_keys,
                  "last_message": msg, "last_error": ""})
    return {"models": n_models, "models_ok": n_model_ok, "keys": n_keys,
            "keys_ok": n_key_ok, "message": msg}


def _loop():
    """Vòng nền: làm liền khi còn tồn đọng, nghỉ theo chu kỳ khi đã xong."""
    global _force, _thread
    while not _stop.is_set():
        if not _enabled():
            STATE["last_message"] = "đang tắt"
            STATE["next_at"] = time.time() + IDLE_WHEN_OFF
            _wake.wait(IDLE_WHEN_OFF)
            _wake.clear()
            continue
        force = _force
        _force = False
        try:
            res = run_once(force=force)
        except Exception as e:
            STATE["last_error"] = str(e)
            res = None
        if res is None:
            # vòng "kiểm ngay" đang chạy: chờ rồi làm tiếp
            _wake.wait(BURST_WAIT)
            _wake.clear()
            continue
        wait = max(30.0, db.float_setting("auto_check_secs", 300))
        if db.pending_model_count() > 0 or keypool.due_keys(1):
            wait = min(wait, float(BURST_WAIT))
        STATE["next_at"] = time.time() + wait
        _wake.wait(wait)
        _wake.clear()
    STATE["running"] = False


def start():
    """Bật vòng nền (chỉ một lần). main.py gọi khi cổng khởi động."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="autocheck", daemon=True)
        STATE["running"] = True
        _thread.start()
    return True


def stop(timeout=5):
    """Dừng vòng nền (bài thử dùng)."""
    _stop.set()
    _wake.set()
    t = _thread
    if t is not None:
        t.join(timeout)
    STATE["running"] = False
