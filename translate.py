"""Dịch qua lại giữa hai giao thức: OpenAI Chat Completions <-> Anthropic Messages.

Nhờ vậy một client chỉ cần biết một giao thức vẫn dùng được mọi nhà cung cấp:
- gọi /v1/chat/completions với model của Claude  -> dịch sang /v1/messages
- gọi /v1/messages với model DeepSeek/OpenCode    -> dịch sang chat/completions
"""

# ---------- stop reason ----------
_A2O_STOP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
    "pause_turn": "stop",
}
_O2A_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "refusal",
}


def _text_of(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for p in content:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict) and p.get("type") == "text":
                out.append(p.get("text", ""))
        return "\n".join(x for x in out if x)
    if isinstance(content, dict):
        return content.get("text", "") or ""
    return str(content)


# ================= OpenAI request -> Anthropic request =================
def openai_to_anthropic(body):
    """body: dict theo chuẩn OpenAI -> dict theo chuẩn Anthropic."""
    j = dict(body or {})
    out = {"model": j.get("model", "")}
    system_parts = []
    msgs = []
    for m in j.get("messages", []) or []:
        role = (m.get("role") or "user").lower()
        content = m.get("content")
        if role == "system":
            system_parts.append(_text_of(content))
            continue
        if role == "tool":
            msgs.append({"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id", ""),
                "content": _text_of(content),
            }]})
            continue
        if role == "assistant" and m.get("tool_calls"):
            blocks = []
            if _text_of(content):
                blocks.append({"type": "text", "text": _text_of(content)})
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    import json as _json
                    try:
                        args = _json.loads(args or "{}")
                    except Exception:
                        args = {"_raw": args}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": args or {},
                })
            msgs.append({"role": "assistant", "content": blocks})
            continue
        # user / assistant thường
        if isinstance(content, list):
            blocks = []
            for p in content:
                if not isinstance(p, dict):
                    blocks.append({"type": "text", "text": str(p)})
                    continue
                t = p.get("type")
                if t == "text":
                    blocks.append({"type": "text", "text": p.get("text", "")})
                elif t == "image_url":
                    url = ((p.get("image_url") or {}).get("url") or "")
                    if url.startswith("data:"):
                        try:
                            head, b64 = url.split(",", 1)
                            media = head.split(";")[0].replace("data:", "") or "image/png"
                            blocks.append({"type": "image", "source": {
                                "type": "base64", "media_type": media, "data": b64}})
                        except Exception:
                            pass
                    elif url:
                        blocks.append({"type": "image", "source": {"type": "url", "url": url}})
                else:
                    blocks.append({"type": "text", "text": _text_of(p)})
            msgs.append({"role": "assistant" if role == "assistant" else "user", "content": blocks})
        else:
            txt = _text_of(content)
            if txt:
                msgs.append({"role": "assistant" if role == "assistant" else "user",
                             "content": [{"type": "text", "text": txt}]})
    # Anthropic bắt đầu bằng user và không cho hai vai trùng nhau liên tiếp
    merged = []
    for m in msgs:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] = (merged[-1]["content"] if isinstance(merged[-1]["content"], list) else []) + m["content"]
        else:
            merged.append(m)
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    out["messages"] = merged
    if system_parts:
        out["system"] = "\n\n".join(p for p in system_parts if p)
    mt = j.get("max_tokens") or j.get("max_completion_tokens")
    out["max_tokens"] = int(mt) if mt else 4096
    for k_src, k_dst in (("temperature", "temperature"), ("top_p", "top_p")):
        if j.get(k_src) is not None:
            out[k_dst] = j[k_src]
    if j.get("stop"):
        stop = j["stop"]
        out["stop_sequences"] = stop if isinstance(stop, list) else [stop]
    if j.get("stream"):
        out["stream"] = True
    if j.get("tools"):
        tools = []
        for t in j["tools"]:
            fn = t.get("function") or t
            tools.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", "") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        out["tools"] = tools
        tc = j.get("tool_choice")
        if isinstance(tc, str):
            if tc == "auto":
                out["tool_choice"] = {"type": "auto"}
            elif tc == "required":
                out["tool_choice"] = {"type": "any"}
            elif tc == "none":
                pass
        elif isinstance(tc, dict):
            fn = tc.get("function") or {}
            if fn.get("name"):
                out["tool_choice"] = {"type": "tool", "name": fn["name"]}
    if j.get("user"):
        out["metadata"] = {"user_id": str(j["user"])}
    return out


# ================= Anthropic response -> OpenAI response =================
def anthropic_to_openai(resp, model=None):
    resp = resp or {}
    text_parts, tool_calls = [], []
    for b in resp.get("content", []) or []:
        if b.get("type") == "text":
            text_parts.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            import json as _json
            tool_calls.append({
                "id": b.get("id", ""),
                "type": "function",
                "function": {"name": b.get("name", ""),
                             "arguments": _json.dumps(b.get("input") or {})},
            })
    usage = resp.get("usage") or {}
    pt = int(usage.get("input_tokens") or 0)
    ct = int(usage.get("output_tokens") or 0)
    msg = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    import time as _time
    return {
        "id": resp.get("id") or "chatcmpl-gateway",
        "object": "chat.completion",
        "created": int(_time.time()),
        "model": model or resp.get("model") or "",
        "choices": [{
            "index": 0,
            "message": msg,
            "finish_reason": _A2O_STOP.get(resp.get("stop_reason") or "end_turn", "stop"),
        }],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
    }


# ================= Anthropic request -> OpenAI request =================
def anthropic_to_openai_request(body):
    j = dict(body or {})
    out = {"model": j.get("model", "")}
    msgs = []
    sysp = j.get("system")
    if sysp:
        msgs.append({"role": "system", "content": _text_of(sysp)})
    for m in j.get("messages", []) or []:
        role = m.get("role") or "user"
        content = m.get("content")
        if isinstance(content, str):
            msgs.append({"role": role, "content": content})
            continue
        texts, images, tool_calls, tool_results = [], [], [], []
        for b in content or []:
            if not isinstance(b, dict):
                texts.append(str(b))
                continue
            t = b.get("type")
            if t == "text":
                texts.append(b.get("text", ""))
            elif t == "image":
                src = b.get("source") or {}
                if src.get("type") == "base64":
                    url = "data:%s;base64,%s" % (src.get("media_type", "image/png"), src.get("data", ""))
                    images.append({"type": "image_url", "image_url": {"url": url}})
                elif src.get("url"):
                    images.append({"type": "image_url", "image_url": {"url": src["url"]}})
            elif t == "tool_use":
                import json as _json
                tool_calls.append({"id": b.get("id", ""), "type": "function",
                                   "function": {"name": b.get("name", ""),
                                                "arguments": _json.dumps(b.get("input") or {})}})
            elif t == "tool_result":
                tool_results.append((b.get("tool_use_id", ""), _text_of(b.get("content"))))
        if tool_results and role == "user":
            if texts:
                msgs.append({"role": "user", "content": "\n".join(texts)})
            for tid, txt in tool_results:
                msgs.append({"role": "tool", "tool_call_id": tid, "content": txt})
            continue
        if images:
            parts = ([{"type": "text", "text": "\n".join(texts)}] if texts else []) + images
            msg = {"role": role, "content": parts}
        else:
            msg = {"role": role, "content": "\n".join(texts)}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        msgs.append(msg)
    out["messages"] = msgs
    if j.get("max_tokens") is not None:
        out["max_tokens"] = j["max_tokens"]
    if j.get("temperature") is not None:
        out["temperature"] = j["temperature"]
    if j.get("top_p") is not None:
        out["top_p"] = j["top_p"]
    if j.get("stop_sequences"):
        out["stop"] = j["stop_sequences"]
    if j.get("stream"):
        out["stream"] = True
    if j.get("tools"):
        out["tools"] = [{"type": "function", "function": {
            "name": t.get("name", ""),
            "description": t.get("description", "") or "",
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        }} for t in j["tools"]]
        tc = j.get("tool_choice") or {}
        if isinstance(tc, dict):
            if tc.get("type") == "auto":
                out["tool_choice"] = "auto"
            elif tc.get("type") == "any":
                out["tool_choice"] = "required"
            elif tc.get("type") == "tool":
                out["tool_choice"] = {"type": "function", "function": {"name": tc.get("name", "")}}
    return out


# ================= OpenAI response -> Anthropic response =================
def openai_to_anthropic_response(resp, model=None):
    resp = resp or {}
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    blocks = []
    if _text_of(msg.get("content")):
        blocks.append({"type": "text", "text": _text_of(msg.get("content"))})
    for tc in msg.get("tool_calls") or []:
        import json as _json
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = _json.loads(args or "{}")
            except Exception:
                args = {"_raw": args}
        blocks.append({"type": "tool_use", "id": tc.get("id", ""),
                       "name": fn.get("name", ""), "input": args or {}})
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    usage = resp.get("usage") or {}
    return {
        "id": resp.get("id") or "msg-gateway",
        "type": "message",
        "role": "assistant",
        "model": model or resp.get("model") or "",
        "content": blocks,
        "stop_reason": _O2A_STOP.get(choice.get("finish_reason") or "stop", "end_turn"),
        "stop_sequence": None,
        "usage": {"input_tokens": int(usage.get("prompt_tokens") or 0),
                  "output_tokens": int(usage.get("completion_tokens") or 0)},
    }


# ================= luồng SSE =================
def _sse(events):
    """events: [(event_name, obj)] -> bytes SSE theo chuẩn Anthropic."""
    import json as _json
    out = []
    for name, obj in events:
        out.append("event: %s\ndata: %s\n\n" % (name, _json.dumps(obj)))
    return "".join(out).encode()


class AnthropicToOpenAIStream:
    """Nhận SSE của Anthropic, phát ra SSE của OpenAI."""

    def __init__(self, model=""):
        self.model = model
        self.buf = b""
        self.msg_id = None
        self.created = None
        self.finish = None
        self.usage = None
        self.tool_index = -1
        self.started = False

    def _chunk(self, delta, finish_reason=None, extra=None):
        import json as _json, time as _time
        obj = {
            "id": self.msg_id or "chatcmpl-gateway",
            "object": "chat.completion.chunk",
            "created": self.created or int(_time.time()),
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if extra:
            obj.update(extra)
        return ("data: " + _json.dumps(obj) + "\n\n").encode()

    def feed(self, data):
        import json as _json
        out = b""
        self.buf += data
        while b"\n\n" in self.buf:
            raw, self.buf = self.buf.split(b"\n\n", 1)
            name, payload = None, None
            for line in raw.split(b"\n"):
                line = line.strip()
                if line.startswith(b"event:"):
                    name = line[6:].strip().decode(errors="ignore")
                elif line.startswith(b"data:"):
                    payload = line[5:].strip()
            if payload is None:
                continue
            try:
                obj = _json.loads(payload.decode(errors="ignore") or "{}")
            except Exception:
                continue
            name = name or obj.get("type") or ""
            if name == "message_start":
                m = obj.get("message") or {}
                self.msg_id = m.get("id") or self.msg_id
                self.created = None
                u = m.get("usage") or {}
                if u:
                    self.usage = {"prompt_tokens": int(u.get("input_tokens") or 0), "completion_tokens": 0}
                if not self.started:
                    self.started = True
                    out += self._chunk({"role": "assistant", "content": ""})
            elif name == "content_block_start":
                b = obj.get("content_block") or {}
                if b.get("type") == "tool_use":
                    self.tool_index += 1
                    out += self._chunk({"tool_calls": [{
                        "index": self.tool_index, "id": b.get("id", ""), "type": "function",
                        "function": {"name": b.get("name", ""), "arguments": ""}}]})
            elif name == "content_block_delta":
                d = obj.get("delta") or {}
                if d.get("type") == "text_delta":
                    out += self._chunk({"content": d.get("text", "")})
                elif d.get("type") == "input_json_delta":
                    idx = self.tool_index if self.tool_index >= 0 else 0
                    out += self._chunk({"tool_calls": [{
                        "index": idx, "function": {"arguments": d.get("partial_json", "")}}]})
            elif name == "message_delta":
                d = obj.get("delta") or {}
                self.finish = _A2O_STOP.get(d.get("stop_reason") or "end_turn", "stop")
                u = obj.get("usage") or {}
                if u:
                    self.usage = self.usage or {}
                    self.usage["completion_tokens"] = int(u.get("output_tokens") or 0)
            elif name == "message_stop":
                extra = {"usage": self.usage} if self.usage else None
                out += self._chunk({}, self.finish or "stop", extra)
                out += b"data: [DONE]\n\n"
                self.started = False
            elif name == "error":
                import json as _json2
                err = obj.get("error") or {}
                out += ("data: " + _json2.dumps({"error": {"message": err.get("message", "upstream error"),
                                                           "type": err.get("type", "upstream_error")}}) + "\n\n").encode()
                out += b"data: [DONE]\n\n"
        return out


class OpenAIToAnthropicStream:
    """Nhận SSE của OpenAI, phát ra SSE của Anthropic."""

    def __init__(self, model=""):
        self.model = model
        self.buf = b""
        self.msg_id = "msg-gateway"
        self.text_started = False
        self.tool_index = -1
        self.tool_buf = {}
        self.done_sent = False

    def _ev(self, name, obj):
        return _sse([(name, obj)])

    def feed(self, data):
        import json as _json
        out = b""
        self.buf += data
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                # dòng "event:" của một số upstream bỏ qua
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                if not self.done_sent:
                    self.done_sent = True
                    if self.text_started:
                        out += self._ev("content_block_stop", {"type": "content_block_stop", "index": 0})
                    out += self._ev("message_stop", {"type": "message_stop"})
                continue
            try:
                obj = _json.loads(payload.decode(errors="ignore") or "{}")
            except Exception:
                continue
            if obj.get("id"):
                self.msg_id = obj["id"]
            choice = (obj.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if not self.text_started and (delta.get("content") or delta.get("role")):
                self.text_started = True
                out += self._ev("message_start", {"type": "message_start", "message": {
                    "id": self.msg_id, "type": "message", "role": "assistant",
                    "model": obj.get("model") or self.model, "content": [],
                    "stop_reason": None, "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0}}})
                out += self._ev("content_block_start", {"type": "content_block_start", "index": 0,
                                                        "content_block": {"type": "text", "text": ""}})
            for tc in delta.get("tool_calls") or []:
                i = tc.get("index", 0)
                fn = tc.get("function") or {}
                if i not in self.tool_buf:
                    self.tool_index = max(self.tool_index, i)
                    self.tool_buf[i] = fn.get("name", "")
                    out += self._ev("content_block_start", {
                        "type": "content_block_start", "index": 1 + i,
                        "content_block": {"type": "tool_use", "id": tc.get("id", "tool_%d" % i),
                                          "name": fn.get("name", ""), "input": {}}})
                if fn.get("arguments"):
                    out += self._ev("content_block_delta", {
                        "type": "content_block_delta", "index": 1 + i,
                        "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]}})
            if delta.get("content"):
                out += self._ev("content_block_delta", {
                    "type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": delta["content"]}})
            if choice.get("finish_reason"):
                if self.text_started:
                    out += self._ev("content_block_stop", {"type": "content_block_stop", "index": 0})
                for i in sorted(self.tool_buf):
                    out += self._ev("content_block_stop", {"type": "content_block_stop", "index": 1 + i})
                out += self._ev("message_delta", {"type": "message_delta", "delta": {
                    "stop_reason": _O2A_STOP.get(choice["finish_reason"], "end_turn"),
                    "stop_sequence": None}, "usage": {"output_tokens": 0}})
        return out
