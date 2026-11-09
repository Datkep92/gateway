# GATEWAY — Kế hoạch chi tiết ứng dụng iOS Gateway (OpenAI + Anthropic)

> Tài liệu này là **kế hoạch**, chưa là code. Chỉ tài liệu và kiến trúc.

## 1. Tổng quan sản phẩm

**Gateway** là ứng dụng iOS chạy local HTTP server, đóng vai trò gateway tương thích OpenAI và Anthropic. Client (ứng dụng khác, script, CLI) gọi đến gateway trên máy iPhone/iPad; gateway nhận request, chọn API key (xoay khi lỗi), gọi upstream (OpenAI/Anthropic), trả response (có hỗ trợ SSE streaming) về client.

### Quyết định đã chốt
- **Loại dự án**: app iOS riêng (không phải tweak).
- **Upstream hỗ trợ**: OpenAI và Anthropic.
- **Streaming**: SSE streaming nếu tối ưu (ưu tiên).
- **Chạy nền**: tuỳ chọn (background mode, có thể bật/tắt).
- **Client auth**: có API key riêng cho gateway (client phải gửi key).
- **Port mặc định**: `15199` (TCP port max 65535; 151199 không hợp lệ).
- **Key rotation**: xoay key khi gặp **bất cứ lỗi nào** (error-based rotation).
- **7 điểm mở**: đã chốt → xem §18 (Hummingbird, GRDB, LAN opt-in, log body opt-in, token bucket rate limit, TLS opt-in, tool calling passthrough).

---

## 2. Mục tiêu & phạm vi

### Mục tiêu
- Cung cấp endpoint tương thích OpenAI (`/v1/chat/completions`, `/v1/models`, …) và Anthropic (`/v1/messages`, …).
- Quản lý nhiều API key cho mỗi provider, xoay tự động khi lỗi.
- Chạy local, không cần server ngoài; dữ liệu lưu on-device.
- UI quản lý key, model, log, cấu hình.

### Phạm vi v1 (in-scope)
- HTTP server local (IPv4/IPv6 loopback + LAN tuỳ chọn).
- OpenAI chat completions (stream + non-stream).
- Anthropic messages (stream + non-stream).
- Models list (chỉ enabled).
- Client API key auth.
- Key pool + rotation on error.
- Persistence (SwiftData/Core Data + Keychain).
- Background mode tuỳ chọn.
- Log request/response: metadata mặc định; **không log body** mặc định. Tuỳ chọn **debug log body** (tắt mặc định) với cảnh báo bảo mật rõ ràng; khi bật, body được redact theo policy (API key, token) và lưu có TTL ngắn.

### Out-of-scope v1
- Tool/function calling: passthrough OpenAI `tools`/`tool_calls` (v1); Anthropic `tools` map cơ bản (v1); đầy đủ (parallel tool calls, tool result loop) ở v1.1.
- Vision/multimodal (v1.1).
- Billing/usage metering chi tiết.
- Multi-user / remote access qua tunnel.
- OAuth upstream.

---

## 3. Kiến trúc tổng thể

```
┌─────────────────────────────────────────────────────┐
│  iOS App (SwiftUI)                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ Dashboard│ │  Keys    │ │ Models   │ │ Logs   │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│  ┌──────────────────────────────────────────────┐   │
│  │ Settings (port, background, client key, ...) │   │
│  └──────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│  Gateway Core (Swift)                               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ HTTP Server │  │ Router       │  │ Auth       │  │
│  │ (Hummingbird│  │ (OpenAI /    │  │ (client    │  │
│  │  or Vapor)  │  │  Anthropic)  │  │  key)      │  │
│  └──────┬──────┘  └──────┬───────┘  └────────────┘  │
│         │                │                          │
│  ┌──────▼────────────────▼───────┐                  │
│  │  Provider Adapter Layer       │                  │
│  │  OpenAIAdapter / AnthropicAdapter│                 │
│  └──────┬────────────────┬───────┘                  │
│         │                │                          │
│  ┌──────▼──────┐  ┌──────▼──────┐                  │
│  │ Key Manager │  │ SSE Streamer│                  │
│  │ (rotation)  │  │             │                  │
│  └──────┬──────┘  └─────────────┘                  │
│         │                                          │
│  ┌──────▼──────────────────────────────────────┐   │
│  │  Persistence: SwiftData + Keychain           │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Stack đề xuất
- **Ngôn ngữ**: Swift 5.9+ (iOS 16+ để khớp thiết bị).
- **UI**: SwiftUI.
- **HTTP server**: **Hummingbird 2** (đã chốt) — nhẹ, async/await, SSE support. Không dùng Vapor.
- **Networking upstream**: URLSession (async) + AsyncStream cho SSE.
- **Persistence**: **GRDB.swift + SQLite** (đã chốt) — tương thích iOS 16+, migration dễ, lightweight.
- **Keychain**: Security framework / KeychainAccess.
- **Background**: `BGTaskScheduler` + `UIApplication` background task; Network Extension không cần.
- **Logging**: os.Logger + in-memory ring buffer cho UI.

---

## 4. Module breakdown

| Module | Trách nhiệm |
|---|---|
| `App` | Entry point, DI container, lifecycle |
| `UI` | SwiftUI views, view models |
| `Server` | HTTP server, routing, middleware (auth, logging, CORS) |
| `Adapters` | OpenAI/Anthropic request/response mapping, SSE |
| `Keys` | Key pool, rotation policy, health tracking |
| `Models` | Data models (Provider, APIKey, GatewayModel, RequestLog) |
| `Storage` | GRDB/Core Data repos, Keychain |
| `Background` | Background tasks, keep-alive |
| `Security` | Client key validation, rate limit (optional), redaction |
| `Telemetry` | Logging, metrics counters |

---

## 5. Data model

### 5.1 Entities (GRDB)

```swift
struct Provider: Codable, FetchableRecord, PersistableRecord {
    var id: UUID
    var name: String           // "openai" | "anthropic"
    var baseURL: String        // default upstream URL
    var isEnabled: Bool
    var createdAt: Date
}

struct APIKey: Codable, FetchableRecord, PersistableRecord {
    var id: UUID
    var providerId: UUID
    var label: String
    var keyRef: String         // Keychain reference (không lưu plaintext trong DB)
    var isEnabled: Bool
    var priority: Int          // thứ tự ưu tiên
    var failureCount: Int      // số lỗi liên tiếp
    var lastUsedAt: Date?
    var lastErrorAt: Date?
    var cooldownUntil: Date?   // tạm disable
}

struct GatewayModel: Codable, FetchableRecord, PersistableRecord {
    var id: String            // "gpt-4o", "claude-3-5-sonnet-..."
    var providerId: UUID
    var displayName: String
    var isEnabled: Bool       // chỉ model enabled mới list
    var contextWindow: Int?
    var supportsStreaming: Bool
}

struct ClientKey: Codable, FetchableRecord, PersistableRecord {
    var id: UUID
    var name: String
    var keyHash: String       // bcrypt/sha256 hash, plaintext chỉ hiện 1 lần
    var createdAt: Date
    var lastUsedAt: Date?
    var isEnabled: Bool
}

struct RequestLog: Codable, FetchableRecord, PersistableRecord {
    var id: UUID
    var timestamp: Date
    var clientKeyId: UUID?
    var provider: String
    var model: String
    var endpoint: String
    var statusCode: Int
    var latencyMs: Int
    var stream: Bool
    var keyId: UUID?          // key đã dùng (redacted trong UI)
    var errorCode: String?
    var errorMessage: String? // truncated
}
```

### 5.2 Keychain
- API key plaintext lưu trong Keychain với `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`.
- DB chỉ lưu reference id.

---

## 6. API endpoints

### 6.1 OpenAI-compatible

| Method | Path | Mô tả |
|---|---|---|
| GET | `/v1/models` | List models (chỉ enabled) |
| POST | `/v1/chat/completions` | Chat completions (stream via `stream: true`) |
| POST | `/v1/completions` | Legacy completions (optional) |
| GET | `/health` | Health check |

### 6.2 Anthropic-compatible

| Method | Path | Mô tả |
|---|---|---|
| POST | `/v1/messages` | Messages API (stream via `stream: true`) |
| GET | `/v1/models`? | Anthropic không có models endpoint chuẩn → map sang OpenAI-style hoặc custom |

### 6.3 Gateway management (local only, client key required)

| Method | Path | Mô tả |
|---|---|---|
| GET | `/gateway/status` | Trạng thái server, uptime |
| GET | `/gateway/keys` | List keys (redacted) |
| POST | `/gateway/keys` | Thêm key |
| DELETE | `/gateway/keys/:id` | Xoá key |
| GET | `/gateway/models` | List models với enabled flag |
| PATCH | `/gateway/models/:id` | Bật/tắt model |
| GET | `/gateway/logs` | Recent logs |

> Management endpoints chỉ expose khi client key hợp lệ và (tuỳ chọn) chỉ từ loopback.

### 6.4 Auth
- Header: `Authorization: Bearer <client_key>` cho OpenAI-style.
- Anthropic-style: `x-api-key: <client_key>` hoặc `Authorization: Bearer`.
- Gateway chấp nhận cả hai để tương thích.

---

## 7. Key rotation policy

### 7.1 Nguyên tắc
- **Rotate on any error**: bất cứ lỗi nào (HTTP 4xx/5xx, network error, timeout, parse error) → xoay key.
- Không rotate khi request thành công.
- Key bị lỗi → tăng `failureCount`, set cooldown (exponential backoff: 30s, 60s, 120s, max 10min).
- Sau cooldown, key eligible lại.
- Nếu tất cả key đều cooldown → trả 503 với `Retry-After`.

### 7.2 Selection algorithm
```
func selectKey(provider) -> APIKey?:
    candidates = keys where enabled && (cooldownUntil == nil || now >= cooldownUntil)
    if empty: return nil  // all in cooldown
    sort by (failureCount asc, lastUsedAt asc, priority asc)
    return first
```

### 7.3 Rotation flow
```
attempt 1: selectKey → call upstream
  on success: reset failureCount, update lastUsedAt → return
  on error:   markFailure(key, error)
              if more candidates: retry with next key (up to N keys, max 3 retries)
              else: return error to client
```

### 7.4 Error classification
- 401/403 → key invalid → disable key (isEnabled=false) + notify UI.
- 429 → rate limit → cooldown dài hơn.
- 5xx / network → cooldown ngắn, retry.
- Parse error → cooldown.

### 7.5 Concurrency
- Key selection dùng lock (actor) để tránh race khi nhiều request đồng thời.
- Per-key semaphore optional để tránh burst.

---

## 8. Streaming (SSE)

### 8.1 OpenAI streaming
- Request `stream: true` → upstream SSE `data: {...}\n\n`, kết thúc `data: [DONE]`.
- Gateway proxy: đọc stream upstream, forward chunks đến client với cùng format.
- Buffer: chunk-by-chunk, flush ngay để giảm latency.
- Giữ connection alive (heartbeat comment `: keepalive` mỗi 15s nếu idle).

### 8.2 Anthropic streaming
- `event: message_start`, `content_block_delta`, `message_stop`, v.v.
- Forward nguyên vẹn events.

### 8.3 Error mid-stream
- Nếu lỗi giữa stream → gửi SSE error event (OpenAI: `data: {"error":{...}}`, Anthropic: `event: error`) rồi đóng.
- Rotate key chỉ áp dụng cho request mới (không retry mid-stream để tránh duplicate).

### 8.4 Backpressure
- Dùng AsyncStream với buffer size giới hạn; nếu client chậm → upstream pause (URLSession delegate).

---

## 9. Storage

### 9.1 Database (GRDB/SQLite)
- File: `Application Support/Gateway/gateway.sqlite`.
- Migration versioning.
- Backup: iTunes/iCloud backup exclude (NSFileProtectionComplete).

### 9.2 Keychain
- Service: `com.gateway.keys`.
- Account: key id UUID.
- Access: `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`.

### 9.3 Preferences (UserDefaults)
- port, backgroundEnabled, clientKeyRequired, logRetentionDays, maxLogCount, corsEnabled, lanEnabled, logBodyEnabled (default false, cảnh báo bảo mật), tlsEnabled, tlsMode (self-signed/imported), rateLimitEnabled, rateLimitRpm, rateLimitBurst.

---

## 10. UI (SwiftUI)

### 10.1 Tabs
1. **Dashboard**: server status (running/stopped), port, uptime, request count, error rate, quick toggle start/stop.
2. **Keys**: list keys per provider, add (paste), enable/disable, delete, health badge, failure count, cooldown.
3. **Models**: list models per provider, toggle enabled (chỉ enabled mới xuất hiện ở `/v1/models`).
4. **Logs**: recent requests (time, provider, model, status, latency), filter, export, clear.
5. **Settings**: port, background mode, client key management, data export/import, about.

### 10.2 Client key management
- Generate key (random 32 bytes base64url) → hiển thị 1 lần → copy.
- List keys (name, created, last used) — không hiện plaintext.
- Revoke.

### 10.3 Onboarding
- First launch: tạo client key mặc định, hướng dẫn cài đặt endpoint trong app khác.

---

## 11. Background execution

### 11.1 Tuỳ chọn
- Setting `backgroundEnabled` (default off để tiết kiệm pin).
- Khi bật: dùng `BGProcessingTask` + `beginBackgroundTask` để giữ server chạy khi app background.
- iOS giới hạn background time (~30s thông thường, BGTask longer). Thực tế: server chỉ chạy khi app foreground hoặc background task active.
- Hiển thị cảnh báo: "iOS có thể dừng server khi app bị suspend; bật Background App Refresh trong Settings."

### 11.2 Keep-alive
- Local notification nhắc user mở app nếu server stopped.
- Không dùng VoIP/background location (vi phạm App Store).

### 11.3 Network
- Bind mặc định `127.0.0.1:port` (localhost only). Tuỳ chọn **LAN access**: bind `0.0.0.0:port` khi `lanEnabled = true` (tắt mặc định). Bật LAN → cảnh báo bảo mật (expose ra mạng LAN). Management endpoints luôn chỉ loopback dù LAN bật.

---

## 12. Bảo mật

- Client key bắt buộc (không có anonymous mode trong v1) → mọi request phải có key hợp lệ.
- Key hash bằng SHA-256 + salt (hoặc bcrypt nếu thư viện).
- Rate limit per client key: **token bucket**, cấu hình được (default 60 req/min, burst 10), trả 429 + `Retry-After`.
- Management endpoints chỉ loopback (127.0.0.1) + valid client key.
- Không log API key plaintext; redact trong logs (chỉ prefix 4 ký tự).
- TLS: mặc định **không TLS** (local HTTP). Tuỳ chọn HTTPS với cert tự ký (auto-generate) hoặc nhập cert người dùng (PEM/P12); bật qua Settings. ATS allow local network.
- App Transport Security: allow local network.
- Data protection: NSFileProtectionComplete.

---

## 13. Testing

### 13.1 Unit tests
- Key rotation logic (mock upstream).
- Model filtering (enabled only).
- Request/response mapping (OpenAI ↔ internal ↔ Anthropic).
- SSE parser.

### 13.2 Integration tests
- Local server end-to-end với mock upstream.
- Streaming end-to-end.
- Key cooldown & rotation.

### 13.3 Manual / device tests
- Chạy trên iPhone, gọi từ Shortcuts/curl.
- Background behaviour.
- Memory/perf under load.

### 13.4 Tools
- XCTest + HummingbirdTesting (nếu Hummingbird).
- MockURLProtocol cho upstream.

---

## 14. Tối ưu đề xuất

1. **Connection pooling** upstream (URLSession với HTTP/2 nếu upstream hỗ trợ) — giảm latency.
2. **Streaming flush** ngay từng chunk — giảm TTFB.
3. **Key selection cache** (in-memory) với invalidation on failure — tránh DB query mỗi request.
4. **Log batching** (write batch mỗi 1s hoặc N records) — giảm I/O.
5. **Model list cache** với TTL 60s.
6. **Compression** (gzip) cho non-stream response nếu client hỗ trợ.
7. **Keep-alive** connection reuse giữa client và gateway.
8. **Lazy init** server chỉ khi user bật.

---

## 15. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|---|---|
| iOS suspend server khi background | Cảnh báo user; foreground-only mặc định; BGTask |
| App Store reject (background networking) | Không dùng VoIP; tuân thủ BGTask API |
| Key leak trong log | Redact; không log body mặc định |
| Upstream API thay đổi | Adapter layer cô lập; version pin |
| IPv6/loopback edge cases | Test cả 127.0.0.1 và ::1 |
| Port conflict (151199 in use) | Fallback port + notify; cho user đổi |
| Memory pressure khi stream lớn | Stream chunk, không buffer toàn bộ |
| Race condition key selection | Actor isolation |
| DB migration fail | Versioned migrations + backup before migrate |

---

## 16. Cấu trúc thư mục đề xuất (app)

```
Gateway/
├── App/
│   ├── GatewayApp.swift
│   └── ContentView.swift
├── Core/
│   ├── Server/
│   ├── Adapters/
│   ├── Keys/
│   ├── Models/
│   ├── Storage/
│   ├── Security/
│   └── Background/
├── UI/
│   ├── Dashboard/
│   ├── Keys/
│   ├── Models/
│   ├── Logs/
│   └── Settings/
├── Resources/
└── Tests/
```

---

## 17. Roadmap

- **v0.1**: Server skeleton + OpenAI chat completions (non-stream) + key management + client auth.
- **v0.2**: SSE streaming OpenAI.
- **v0.3**: Anthropic adapter + streaming.
- **v0.4**: Models filtering, logs, settings.
- **v0.5**: Background mode + polish.
- **v1.0**: TestFlight / release.

---

## 18. Quyết định đã chốt (7 điểm mở) — tự quyết định

1. **HTTP framework**: **Hummingbird 2**.
   - *Lý do*: nhẹ, async/await native, SSE support tốt, phù hợp iOS app; Vapor nặng và middleware không cần thiết cho scope v1.

2. **Persistence**: **GRDB.swift + SQLite**.
   - *Lý do*: tương thích iOS 16.6 (SwiftData chỉ iOS 17+), migration versioning rõ ràng, lightweight, không phụ thuộc Core Data stack; Keychain vẫn cho secret.

3. **LAN access**: mặc định **localhost only** (`127.0.0.1`); tuỳ chọn bind `0.0.0.0` qua `lanEnabled` (tắt mặc định).
   - *Lý do*: mặc định an toàn (chỉ máy này); LAN là opt-in có cảnh báo; management endpoints luôn loopback.

4. **Log body**: mặc định **không log body**; tuỳ chọn **debug log body** (tắt mặc định) với cảnh báo bảo mật.
   - *Lý do*: tránh leak dữ liệu nhạy cảm; debug mode redact secret, TTL ngắn, opt-in rõ ràng.

5. **Rate limit**: **token bucket** theo client key, cấu hình được (default 60 req/min, burst 10), trả 429 + `Retry-After`.
   - *Lý do*: linh hoạt, bảo vệ upstream khỏi abuse, cấu hình được per deployment; token bucket xử lý burst tốt hơn fixed window.

6. **TLS**: mặc định **không TLS** (local HTTP); tuỳ chọn HTTPS với cert tự ký (auto-generate) hoặc nhập cert (PEM/P12) qua Settings.
   - *Lý do*: local traffic không cần TLS; HTTPS là opt-in cho môi trường cần (reverse proxy/tunnel); tự ký đủ cho local trust.

7. **Tool calling**: **passthrough OpenAI tools** (`tools`/`tool_calls`) ở v1; Anthropic `tools` map cơ bản ở v1; đầy đủ (parallel tool calls, tool result loop) ở v1.1.
   - *Lý do*: passthrough đơn giản, không cần xử lý tool execution phía gateway; Anthropic map cơ bản để tương thích client; đầy đủ hoãn v1.1.

> Tất cả lựa chọn trên đã phản ánh vào các section liên quan (3, 9, 11, 12, scope v1). Không còn điểm mở.

*Kế hoạch này chưa là code. Khi user đồng ý, bắt đầu với v0.1.*
