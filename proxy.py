import db, keypool, json, urllib.request, urllib.error, time

DEFAULT_OPENAI_BASE = "https://api.openai.com"
DEFAULT_ANTHROPIC_BASE = "https://api.anthropic.com"

def base_url(provider):
    c = db.conn()
    row = c.execute("SELECT base_url FROM providers WHERE name=? AND enabled=1", (provider,)).fetchone()
    c.close()
    if row:
        return row["base_url"].rstrip("/")
    return DEFAULT_OPENAI_BASE if provider == "openai" else DEFAULT_ANTHROPIC_BASE

def _request(url, method, headers, body, stream=False, timeout=120):
    data = body if body is None else (body if isinstance(body, (bytes, bytearray)) else body.encode())
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
        return False, 0, {}, str(e).encode()

def openai_chat(body_bytes, stream):
    # returns (ok, status, headers, payload, key_id, error_bytes)
    # payload: bytes if not stream else response object
    max_retries = 3
    keys_tried = 0
    last_err = b""
    key_id = None
    while keys_tried < max_retries:
        key = keypool.select_key("openai")
        if key is None:
            return False, 503, {}, b"", None, b'{"error":{"message":"no available openai key"}}'
        key_id = key["id"]
        url = base_url("openai") + "/v1/chat/completions"
        headers = {
            "Authorization": "Bearer " + key["key_value"],
            "Content-Type": "application/json",
        }
        ok, status, resp_headers, payload = _request(url, "POST", headers, body_bytes, stream=stream)
        if ok:
            keypool.mark_success(key_id)
            db_log = True
            return True, status, resp_headers, payload, key_id, b""
        # error
        err_bytes = payload if isinstance(payload, (bytes, bytearray)) else b""
        keypool.mark_failure(key_id, status)
        last_err = err_bytes if err_bytes else ("HTTP %d" % status).encode()
        keys_tried += 1
        if stream:
            # no retry mid-stream attempt actually before stream started; can retry
            continue
    return False, status, {}, b"", key_id, last_err

def anthropic_messages(body_bytes, stream):
    max_retries = 3
    keys_tried = 0
    last_err = b""
    key_id = None
    status = 0
    while keys_tried < max_retries:
        key = keypool.select_key("anthropic")
        if key is None:
            return False, 503, {}, b"", None, b'{"error":{"message":"no available anthropic key"}}'
        key_id = key["id"]
        url = base_url("anthropic") + "/v1/messages"
        headers = {
            "x-api-key": key["key_value"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        ok, status, resp_headers, payload = _request(url, "POST", headers, body_bytes, stream=stream)
        if ok:
            keypool.mark_success(key_id)
            return True, status, resp_headers, payload, key_id, b""
        err_bytes = payload if isinstance(payload, (bytes, bytearray)) else b""
        keypool.mark_failure(key_id, status)
        last_err = err_bytes if err_bytes else ("HTTP %d" % status).encode()
        keys_tried += 1
    return False, status, {}, b"", key_id, last_err
