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
    """Gọi một đường dẫn của provider, thử tối đa 3 key khi lỗi tạm thời."""
    spec = P.spec(provider)
    if spec is None:
        return False, 400, {}, b"", None, json.dumps(
            {"error": {"message": "provider không hợp lệ", "type": "invalid_request"}}).encode()
    path = spec.get(path_key)
    if not path:
        return False, 400, {}, b"", None, json.dumps(
            {"error": {"message": "%s không hỗ trợ giao thức này" % spec["name"],
                       "type": "invalid_request"}}).encode()
    url = spec["base_url"].rstrip("/") + path
    last_err, key_id, status = b"", None, 0
    for _ in range(3):
        key = keypool.select_key(spec["name"])
        if key is None:
            if key_id is None:
                return False, 503, {}, b"", None, json.dumps(
                    {"error": {"message": "chưa có key cho %s" % spec["label"], "type": "no_key"}}).encode()
            break
        key_id = key["id"]
        ok, status, resp_headers, payload = _request(url, "POST", _headers(spec["protocol"], key["key_value"]), body_bytes)
        if ok:
            keypool.mark_success(key_id)
            return True, status, resp_headers, payload, key_id, b""
        err_bytes = payload if isinstance(payload, (bytes, bytearray)) else b""
        keypool.mark_failure(key_id, status)
        last_err = err_bytes or ("HTTP %d" % status).encode()
        if status not in RETRYABLE:
            break
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


def check_key(provider, key_value, model=None):
    """Kiểm tra key bằng một câu hỏi rất ngắn. -> (ok, thông báo)"""
    spec = P.spec(provider)
    if spec is None:
        return False, "provider không hợp lệ"
    if spec["protocol"] == P.ANTHROPIC:
        body = json.dumps({"model": model or (spec.get("default_models") or [""])[0],
                           "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}).encode()
        url = spec["base_url"].rstrip("/") + (spec.get("messages_path") or "/v1/messages")
    else:
        body = json.dumps({"model": model or (spec.get("default_models") or [""])[0],
                           "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}).encode()
        url = spec["base_url"].rstrip("/") + (spec.get("chat_path") or "/v1/chat/completions")
    ok, status, _, payload = _request(url, "POST", _headers(spec["protocol"], key_value), body, timeout=45)
    if ok:
        return True, ""
    body = payload.decode(errors="ignore") if isinstance(payload, (bytes, bytearray)) else str(payload)
    return False, "HTTP %s %s" % (status, body[:200])
