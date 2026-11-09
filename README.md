# Gateway

Local HTTP gateway tương thích OpenAI & Anthropic, chạy trên máy (Python 3 stdlib). Có UI HTML để quản lý key, model, log, cấu hình.

## Chạy
```bash
python3 main.py
# hoặc
sh run.sh
```
Mặc định: `127.0.0.1:151199`. Bật LAN: set setting `lan_enabled=1` (hoặc qua UI Settings) → bind `0.0.0.0`.

## Client key
Lần chạy đầu tạo client key mặc định và in ra console (chỉ 1 lần). Dùng key này cho `Authorization: Bearer <key>`.

## Endpoints
- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions` (stream & non-stream)
- `POST /v1/messages` (Anthropic)
- `GET /` UI quản lý
- `/gateway/*` (chỉ loopback + client key)

## Ghi chú bảo mật
- API key upstream lưu SQLite (`data/gateway.db`); nên bảo vệ file.
- Log body tắt mặc định; bật debug qua setting `log_body_enabled=1`.
- TLS tuỳ chọn: `tls_enabled=1`, `tls_mode=selfsigned|imported`.
