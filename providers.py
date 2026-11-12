"""Sổ đăng ký nhà cung cấp (provider).

Một chỗ duy nhất mô tả từng nhà cung cấp: giao thức (openai | anthropic),
địa chỉ gốc, đường dẫn chat/models, cách nhận biết model miễn phí và danh sách
model mặc định. proxy.py, server.py và db.py đều đọc từ đây.

Người dùng chỉ cần dán API key (và base URL nếu không phải nhà cung cấp quen):
detect() tự đoán nhà cung cấp từ tên máy chủ, đường dẫn hoặc tiền tố key;
candidates() trả về danh sách cần thử khi chưa đoán được chắc chắn.
"""
import re
from urllib.parse import urlparse

# ------- giao thức -------
OPENAI = "openai"
ANTHROPIC = "anthropic"

PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "protocol": OPENAI,
        "base_url": "https://api.openai.com",
        "chat_path": "/v1/chat/completions",
        "messages_path": None,
        "models_path": "/v1/models",
        "key_url": "https://platform.openai.com/api-keys",
        "key_hint": "sk-…",
        "free_rules": [r"-mini$", r"-mini-", r"nano"],
        "free_note": "OpenAI không có model miễn phí — mặc định chỉ bật nhóm rẻ nhất (mini/nano).",
        "default_models": [
            "gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1-nano",
            "gpt-4o", "gpt-4.1", "o4-mini",
        ],
    },
    "anthropic": {
        "label": "Claude",
        "protocol": ANTHROPIC,
        "base_url": "https://api.anthropic.com",
        "chat_path": None,
        "messages_path": "/v1/messages",
        "models_path": "/v1/models",
        "key_url": "https://console.anthropic.com/settings/keys",
        "key_hint": "sk-ant-…",
        "free_rules": [r"haiku"],
        "free_note": "Claude không có model miễn phí — mặc định chỉ bật nhóm rẻ nhất (haiku).",
        "default_models": [
            "claude-3-5-haiku-20241022", "claude-3-haiku-20240307",
            "claude-3-5-sonnet-20241022", "claude-sonnet-4-20250514",
        ],
    },
    "deepseek": {
        "label": "DeepSeek",
        "protocol": OPENAI,
        "base_url": "https://api.deepseek.com",
        "chat_path": "/v1/chat/completions",
        "messages_path": None,
        "models_path": "/v1/models",
        "key_url": "https://platform.deepseek.com/api_keys",
        "key_hint": "sk-…",
        "free_rules": [r"deepseek-chat", r"deepseek-reasoner"],
        "free_note": "DeepSeek tính phí rất thấp; hai model chat/reasoner là rẻ nhất.",
        "default_models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "opencode": {
        "label": "OpenCode Zen",
        "protocol": OPENAI,
        "base_url": "https://opencode.ai/inference",
        "chat_path": "/openai/v1/chat/completions",
        "messages_path": "/anthropic/v1/messages",
        "models_path": "/v1/models",
        "key_url": "https://opencode.ai/console",
        "key_hint": "console API key",
        "free_rules": [r"-free$", r"^big-pickle$", r"^iceman-free$"],
        "free_note": "OpenCode Zen có nhiều model gắn đuôi -free; mặc định chỉ bật những model đó.",
        "default_models": [
            "big-pickle", "glm-5.3-flash-free", "deepseek-v4.1-flash-free",
            "mimo-v2.6-flash-free", "longcat-2.5-free", "beam-free",
        ],
    },
    # Nhà cung cấp người dùng tự nhập địa chỉ: giao thức và đường dẫn do
    # /gateway/onboard quyết định (cột trong bảng providers), không ghi đè mỗi lần khởi động.
    "custom": {
        "label": "Nhà cung cấp khác",
        "protocol": OPENAI,
        "base_url": "",
        "chat_path": "/v1/chat/completions",
        "messages_path": "/v1/messages",
        "models_path": "/v1/models",
        "key_url": "",
        "key_hint": "API key",
        "dynamic": True,
        "free_rules": [r"."],
        "free_note": "Nhà cung cấp tự nhập: máy không biết giá nên mặc định bật mọi model đã dò được.",
        "default_models": [],
    },
}

# đoán nhà cung cấp từ tên máy chủ trong base URL
HOST_RULES = (
    ("anthropic", ("anthropic.com", "claude.ai")),
    ("openai", ("openai.com", "openai.azure.com")),
    ("deepseek", ("deepseek.com", "deepseek.ai")),
    ("opencode", ("opencode.ai", "opencodezen")),
)

# đoán nhà cung cấp từ đường dẫn (proxy nội bộ, ví dụ /anthropic/v1)
PATH_RULES = (
    ("anthropic", ("/anthropic", "/claude")),
    ("deepseek", ("/deepseek",)),
    ("openai", ("/openai", "/chatgpt", "/v1/chat/completions")),
)

# đoán nhà cung cấp từ tiền tố key
KEY_RULES = (
    (r"^sk-ant-", "anthropic"),
    (r"^sk-proj-", "openai"),
    (r"^sk-or-", None),          # OpenRouter: giao thức OpenAI, địa chỉ riêng
    (r"^sk-", None),             # OpenAI hoặc DeepSeek: phải thử mới biết
)

# nhà cung cấp cần thử theo thứ tự khi key không nói lên điều gì
KEY_CANDIDATES = ("openai", "deepseek", "opencode")

# đuôi đường dẫn người dùng hay dán kèm, cắt bỏ vì mọi đường dẫn đã có sẵn
_BASE_TAILS = ("/v1/chat/completions", "/v1/messages", "/v1/models",
               "/chat/completions", "/messages", "/models", "/v1")

# tên gọi khác người dùng hay nhập
ALIASES = {
    "claude": "anthropic",
    "anthropic": "anthropic",
    "gpt": "openai",
    "chatgpt": "openai",
    "zen": "opencode",
    "opencode-zen": "opencode",
    "opencode.ai": "opencode",
    "ds": "deepseek",
    "deep-seek": "deepseek",
}

_compiled = {name: [re.compile(p, re.I) for p in spec["free_rules"]] for name, spec in PROVIDERS.items()}


def canonical(name):
    """Đổi tên người dùng nhập về tên chuẩn, hoặc None nếu không biết.

    Ngoài các nhà cung cấp có sẵn (openai, anthropic, ...), cũng chấp nhận
    tên đang có trong bảng providers (ví dụ: base URL dùng làm tên cho nhà
    cung cấp tự nhập) để mỗi base URL là một nhà cung cấp riêng.
    """
    if not name:
        return None
    n = str(name).strip()
    low = n.lower()
    low = ALIASES.get(low, low)
    if low in PROVIDERS:
        return low
    # nhà cung cấp tự nhập: tên = base URL, kiểm tra trong db
    try:
        import db
        c = db.conn()
        row = c.execute("SELECT name FROM providers WHERE name=?", (n,)).fetchone()
        c.close()
        if row is not None:
            return n
    except Exception:
        pass
    return None


def spec(name):
    """Thông số của provider; cột trong db (base URL, giao thức, đường dẫn) được ưu tiên."""
    n = canonical(name)
    if n is None:
        return None
    if n in PROVIDERS:
        out = dict(PROVIDERS[n])
    else:
        # nhà cung cấp tự nhập lưu trong db: dùng mẫu "custom"
        out = dict(PROVIDERS.get("custom", {}))
    out["name"] = n
    try:
        import db
        c = db.conn()
        row = c.execute("""SELECT base_url, protocol, chat_path, messages_path, models_path, key_url
                           FROM providers WHERE name=?""", (n,)).fetchone()
        c.close()
        if row is not None:
            keys = row.keys()
            for f in ("protocol", "chat_path", "messages_path", "models_path", "key_url"):
                v = row[f] if f in keys else None
                if v:
                    out[f] = v
            v = row["base_url"] if "base_url" in keys else None
            if v:
                out["base_url"] = str(v).rstrip("/")
    except Exception:
        pass
    return out


def all_specs():
    return [spec(n) for n in PROVIDERS]


def normalize_base(url):
    """Chuẩn hoá base URL người dùng dán: thêm https://, bỏ đuôi /v1 hay đường dẫn đầy đủ."""
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    u = u.rstrip("/")
    low = u.lower()
    for tail in _BASE_TAILS:
        if low.endswith(tail) and len(u) > len(tail) + 8:
            u = u[: len(u) - len(tail)].rstrip("/")
            break
    return u


def detect(provider=None, base_url=None, api_key=None):
    """Đoán nhà cung cấp: tên nhập tay > máy chủ trong base URL > đường dẫn > tiền tố key.

    -> tên chuẩn, hoặc None khi phải thử mới biết (xem candidates()).
    """
    n = canonical(provider)
    if n:
        return n
    u = normalize_base(base_url)
    if u:
        p = urlparse(u)
        host = (p.hostname or "").lower()
        for name, keys in HOST_RULES:
            if any(k in host for k in keys):
                return name
        path = (p.path or "").lower()
        for name, keys in PATH_RULES:
            if any(k in path for k in keys):
                return name
    k = (api_key or "").strip().lower()
    for rx, name in KEY_RULES:
        if re.match(rx, k):
            return name
    return None


def candidates(provider=None, base_url=None, api_key=None):
    """Danh sách nhà cung cấp nên thử, theo thứ tự. Rỗng = thử base URL như nhà cung cấp tự nhập."""
    n = detect(provider=provider, base_url=base_url, api_key=api_key)
    if n:
        return [n]
    if canonical(provider):
        return [canonical(provider)]
    u = normalize_base(base_url)
    if u:
        return []          # địa chỉ lạ: thử trực tiếp như "custom"
    k = (api_key or "").strip().lower()
    if re.match(r"^sk-", k):
        return list(KEY_CANDIDATES)
    return []


def is_free(provider, model_id):
    n = canonical(provider)
    if n is None or not model_id:
        return False
    if n in _compiled:
        return any(rx.search(model_id) for rx in _compiled[n])
    # nhà cung cấp tự nhập (base URL làm tên): mặc định bật mọi model
    return True


def classify(provider, model_ids):
    """[(model_id, free?)] theo đúng thứ tự đầu vào, bỏ trùng."""
    seen, out = set(), []
    for m in model_ids or []:
        if not m or m in seen:
            continue
        seen.add(m)
        out.append((m, is_free(provider, m)))
    return out


def guess(model_id):
    """Đoán nhà cung cấp từ tên model, khi model chưa có trong sổ."""
    m = (model_id or "").strip().lower()
    if not m:
        return None
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4", "chatgpt", "davinci", "text-embedding")):
        return "openai"
    if m.startswith("deepseek"):
        return "deepseek"
    if m.endswith("-free") or m in ("big-pickle", "grok-code", "qwen3-coder", "kimi-k2"):
        return "opencode"
    return None
