"""Gọi nhà cung cấp thượng nguồn.

Chọn key theo provider, thử lại khi lỗi tạm thời, và biết đường dẫn riêng của
từng nhà cung cấp (OpenAI/DeepSeek/OpenCode dùng chat/completions, Claude dùng
messages, OpenCode có cả hai).
"""
import db, keypool, json, urllib.request, urllib.error, time

import providers as P

RETRYABLE = {0, 401, 403, 408, 409, 425, 429, 500, 502, 503, 504, 522, 524, 529}


def _request(url, method, headers, body=None, timeout=180):
    data = None if body is None else (body if isinstance(body, (bytes, bytearray)) else body.encode())
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return True, resp.getcode(), dict(resp.headers), resp
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read()
        except Exception:
            err_body = b""
        return False, e.code, dict(e.headers or {}), err_body
    except Exception as e:
        return False, 0, {}, ("%s: %s" % (type(e).__name__, e)).encode()


def _headers(protocol, key_value):
    if protocol == P.ANTHROPIC:
        return {"x-api-key": key_value, "anthropic-version": "2023-06-01",
                "Content-Type": "application/json", "Accept": "application/json"}
    return {"Authorization": "Bearer " + key_value, "Content-Type": "application/json"}


def _call(provider, path_key, body_bytes, stream):
    """Gọi một đường dẫn của provider, thử nhiều key/nhà cung cấp khi lỗi tạm thời."""
    primary = P.canonical(provider) or provider
    # model trong thân để tự chuyển nhà cung cấp cùng model nếu key hết
    model = None
    try:
        _j = json.loads(body_bytes or b"{}")
        if isinstance(_j, dict):
            model = (_j.get("model") or "").strip() or None
    except Exception:
        model = None

    # danh sách nhà cung cấp sẽ thử: chính + các ncc khác có cùng model
    candidates = [primary]
    if model:
        c = db.conn()
        try:
            for r in c.execute("SELECT DISTINCT provider FROM gateway_models WHERE id=? AND available=1", (model,)).fetchall():
                p = P.canonical(r["provider"])
                if p and p not in candidates:
                    candidates.append(p)
        finally:
            c.close()

    last_err, key_id, status = b"", None, 0
    waited, wait_budget = 0.0, db.float_setting("key_wait_max", 8)
    for prov in candidates:
        spec = P.spec(prov)
        if spec is None:
            continue
        path = spec.get(path_key)
        if not path:
            continue
        url = spec["base_url"].rstrip("/") + path
        # mỗi key bật được thử một lần (tối đa 4), để key lỗi chuyển sang key kế tiếp
        attempts = max(2, min(keypool.enabled_key_count(spec["name"]), 4))
        i = 0
        while i < attempts:
            key = keypool.select_key(spec["name"])
            if key is None:
                # mọi key đang nghỉ: chờ một chút rồi thử lại, thay vì trả lỗi ngay
                wait = keypool.wait_ready(spec["name"])
                if wait is None or wait <= 0 or waited + wait > wait_budget:
                    break
                time.sleep(wait + 0.05)
                waited += wait
                key = keypool.select_key(spec["name"])
                if key is None:
                    break
            key_id = key["id"]
            ok, status, resp_headers, payload = _request(url, "POST", _headers(spec["protocol"], key["key_value"]), body_bytes)
            if ok:
                keypool.mark_success(key_id)
                return True, status, resp_headers, payload, key_id, b""
            err_bytes = payload if isinstance(payload, (bytes, bytearray)) else b""
            keypool.mark_failure(key_id, status)
            if status in (401, 403):
                db.set_key_health(key_id, "fail", "HTTP %d" % status)
            last_err = err_bytes or ("HTTP %d" % status).encode()
            if status not in RETRYABLE:
                break
            i += 1
    if key_id is None:
        return False, 503, {}, b"", None, json.dumps(
            {"error": {"message": "chưa có key khả dụng", "type": "no_key"}}).encode()
    return False, status, {}, b"", key_id, last_err


def chat(provider, body_bytes, stream):
    """Gọi chat/completions (giao thức OpenAI)."""
    return _call(provider, "chat_path", body_bytes, stream)


def messages(provider, body_bytes, stream):
    """Gọi messages (giao thức Anthropic)."""
    return _call(provider, "messages_path", body_bytes, stream)


def _model_ids(payload):
    """Đọc danh sách model từ phản hồi JSON của nhà cung cấp. -> (ids, lỗi)"""
    try:
        obj = json.loads((payload.read() if hasattr(payload, "read") else payload) or b"{}")
    except Exception as e:
        return [], "phản hồi không phải JSON: %s" % e
    items = obj.get("data") if isinstance(obj, dict) else obj
    if not isinstance(items, list):
        items = (obj or {}).get("models") or []
    ids = []
    for it in items:
        if isinstance(it, str):
            ids.append(it)
        elif isinstance(it, dict):
            mid = it.get("id") or it.get("name") or it.get("slug")
            if mid:
                ids.append(mid)
    return ids, ""


def discover_models_at(url, protocol, key_value, timeout=30):
    """Dò model tại đúng một địa chỉ, khi chưa (hoặc không) biết nhà cung cấp. -> (ok, [id...], lỗi)"""
    if not url:
        return False, [], "thiếu địa chỉ"
    ok, status, _, payload = _request(url, "GET", _headers(protocol, key_value), timeout=timeout)
    if not ok:
        body = payload if isinstance(payload, (bytes, bytearray)) else b""
        return False, [], "HTTP %s %s" % (status, body.decode(errors="ignore")[:160])
    ids, err = _model_ids(payload)
    if err:
        return False, [], err
    return True, ids, ""


def discover_models(provider, key_value=None, timeout=180):
    """Dò danh sách model thật của nhà cung cấp. -> (ok, [id...], lỗi)"""
    spec = P.spec(provider)
    if spec is None or not spec.get("models_path"):
        return False, [], "provider không hỗ trợ dò model"
    if not key_value:
        c = db.conn()
        row = c.execute("SELECT key_value FROM api_keys WHERE provider=? AND enabled=1 ORDER BY failure_count, priority LIMIT 1",
                        (spec["name"],)).fetchone()
        c.close()
        if row is None:
            return False, [], "chưa có key"
        key_value = row["key_value"]
    url = spec["base_url"].rstrip("/") + spec["models_path"]
    return discover_models_at(url, spec["protocol"], key_value, timeout=timeout)


def test_key_by_id(kid, model=None):
    """Kiểm tra một API key; cập nhật health của key. -> (ok, thông báo)"""
    c = db.conn()
    row = c.execute("SELECT * FROM api_keys WHERE id=?", (kid,)).fetchone()
    c.close()
    if row is None:
        return False, "key không tồn tại"
    provider = row["provider"]
    key_value = row["key_value"]
    if not model:
        c = db.conn()
        # ưu tiên model đã kiểm thấy dùng được, để lỗi (nếu có) là lỗi của key
        mrow = c.execute("""SELECT id FROM gateway_models
                            WHERE provider=? AND enabled=1 AND available=1
                            ORDER BY (health='ok') DESC, available DESC, id LIMIT 1""",
                         (provider,)).fetchone()
        c.close()
        model = (mrow["id"] if mrow else None) or (P.spec(provider) or {}).get("default_models", [""])[0]
    ok, status, msg = check_key(provider, key_value, model)
    if ok:
        db.set_key_health(kid, "ok", msg)
        if keypool.reenable_auto(kid):
            # key do hệ thống tắt vì lỗi vừa sống lại: kiểm lại model của ncc này ngay
            db.mark_provider_models_due(provider)
        keypool.mark_success(kid)
    elif _model_level(status, msg):
        return False, msg                # lỗi thuộc về model: không kết luận gì về key
    else:
        db.set_key_health(kid, "fail", msg)
        keypool.mark_failure(kid, status)
    return ok, msg


def test_model(model_id, timeout=45):
    """Kiểm tra một model bằng một key khả dụng của ncc; cập nhật health model+key."""
    c = db.conn()
    row = c.execute("SELECT * FROM gateway_models WHERE id=?", (model_id,)).fetchone()
    c.close()
    if row is None:
        return False, "model không tồn tại"
    provider = P.canonical(row["provider"]) or row["provider"]
    key = keypool.select_key(provider)
    if key is None:
        c = db.conn()
        krow = c.execute("SELECT * FROM api_keys WHERE provider=? AND enabled=1 LIMIT 1", (provider,)).fetchone()
        c.close()
        key = dict(krow) if krow else None
    if key is None:
        # chưa có key nào dùng được: chưa nói được gì về model, giữ nguyên và thử lại sớm
        health = row["health"] or "unknown"
        db.set_model_health(model_id, health, "chưa có key", retry_in=60)
        return False, "chưa có key"
    ok, status, msg = check_key(provider, key["key_value"], model_id, timeout=timeout)
    if ok:
        db.set_model_health(model_id, "ok", "")
        db.set_key_health(key["id"], "ok", "")
        keypool.mark_success(key["id"])
        return True, ""
    health = row["health"] or "unknown"
    if status in (401, 403):
        # key sai: tắt key (kèm hẹn thử lại). Model thì chưa kiểm được — giữ nguyên
        # sức khỏe cũ và hẹn kiểm lại sớm bằng key khác, không kết luận model hỏng.
        db.set_key_health(key["id"], "fail", msg)
        keypool.mark_failure(key["id"], status)
        db.set_model_health(model_id, health, "" if health == "ok" else msg, retry_in=60)
    elif _model_level(status, msg):
        # model không tồn tại / không dùng được với giao thức này
        db.set_model_health(model_id, "fail", msg)
    else:
        # lỗi tạm thời (429/5xx/mạng): chưa kết luận model hỏng, hẹn kiểm lại sớm.
        # Không đánh dấu key ở đây: 429 này là của một model đang bận, không phải
        # của key, đánh dấu sẽ cho key của ncc nghỉ oan và chặn việc thật.
        db.set_model_health(model_id, health, msg, retry_in=60)
    return False, msg


def _model_level(status, text):
    """Lỗi này có phải do model không (không tồn tại, không được phép dùng...)."""
    if status not in (400, 404, 410, 422):
        return False
    low = (text or "").lower()
    if "model" not in low:
        return False
    for w in ("not found", "does not exist", "doesn't exist", "not exist", "unknown model",
              "invalid model", "no such model", "unsupported model", "not available",
              "model_not_found", "no permission to access"):
        if w in low:
            return True
    return False


def traffic_model_result(model, ok, status, errb=b""):
    """Ghi kết quả một lượt gọi thật vào sức khỏe model, để danh sách tự đúng."""
    mid = (model or "").strip()
    if not mid:
        return
    c = db.conn()
    row = c.execute("SELECT id, health FROM gateway_models WHERE id=?", (mid,)).fetchone()
    c.close()
    if row is None:
        return
    health = row["health"] or "unknown"
    if ok:
        if health != "ok":
            db.set_model_health(mid, "ok", "")
        return
    if status in (0, 499, 503):
        # mạng phía máy này, client tự ngắt, hay cổng hết key: không nói gì về model
        return
    text = errb.decode(errors="ignore") if isinstance(errb, (bytes, bytearray)) else str(errb or "")
    if _model_level(status, text):
        db.set_model_health(mid, "fail", text)
    elif health == "fail":
        db.set_model_health(mid, "fail", text)
    else:
        # lỗi tạm thời: vẫn để model trong danh sách, chỉ hẹn kiểm lại sớm
        db.set_model_health(mid, health, text, retry_in=60)


def check_key(provider, key_value, model=None, timeout=45):
    """Kiểm tra key bằng một câu hỏi rất ngắn. -> (ok, mã HTTP, thông báo)"""
    spec = P.spec(provider)
    if spec is None:
        return False, 0, "provider không hợp lệ"
    body = json.dumps({"model": model or (spec.get("default_models") or [""])[0],
                       "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}).encode()
    if spec["protocol"] == P.ANTHROPIC:
        url = spec["base_url"].rstrip("/") + (spec.get("messages_path") or "/v1/messages")
    else:
        url = spec["base_url"].rstrip("/") + (spec.get("chat_path") or "/v1/chat/completions")
    ok, status, _, payload = _request(url, "POST", _headers(spec["protocol"], key_value), body, timeout=timeout)
    if ok:
        return True, status, ""
    text = payload.decode(errors="ignore") if isinstance(payload, (bytes, bytearray)) else str(payload)
    return False, status, "HTTP %s %s" % (status, text[:200])
