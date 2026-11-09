# Gateway

Local HTTP gateway tương thích OpenAI & Anthropic, chạy trên máy (Python 3 stdlib). Có UI HTML để quản lý key, model, log, cấu hình.

## Chạy
```bash
python3 main.py
# hoặc
sh run.sh
```
Mặc định: `127.0.0.1:15199`. Bật LAN: set setting `lan_enabled=1` (hoặc qua UI Settings) → bind `0.0.0.0`.

## Thiết lập một bước (khuyến nghị)
Mở `http://127.0.0.1:15199/`, bấm **⚡ Thiết lập một bước**, rồi chỉ cần:

1. chọn nhà cung cấp (Tự nhận diện / OpenAI / Claude / DeepSeek / OpenCode / Khác),
2. dán API key (nhà cung cấp riêng thì thêm base URL).

Phần còn lại tự làm: đoán đúng nhà cung cấp (theo tên, base URL hoặc tiền tố key), dò model thật, bật model miễn phí (mặc định chỉ dùng free), chọn model trò chuyện làm mặc định, cấp một client key mới và đăng nhập luôn vào bảng điều khiển.

Màn kết quả hiện: số model dò được, model mặc định, chế độ chỉ-free, client key (có nút Copy) và hai địa chỉ để dán vào app:
- OpenAI: `http://127.0.0.1:15199/v1`
- Claude: `http://127.0.0.1:15199`

Gọi API không cần gửi tên model: thiếu `model` thì gateway dùng model mặc định (luôn là model trò chuyện, không chọn model nhúng/embedding).

## Client key
Mỗi lần thiết lập một bước cấp thêm một client key mới và hiện ngay trong màn kết quả; key cũ vẫn dùng được (xoá ở mục Keys nếu không cần). Client gọi API bằng `Authorization: Bearer <key>` (hoặc `x-api-key`).

## Endpoints
- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions` (stream & non-stream)
- `POST /v1/messages` (Anthropic)
- `POST /gateway/onboard` (chỉ từ máy này): dán key → thiết lập xong
- `GET /` UI quản lý
- `/gateway/*` (chỉ loopback + client key)

## Kiểm tra
```bash
python3 -m py_compile server.py
python3 Tests/e2e_test.py     # 51 kiểm tra đầu-cuối qua HTTP
python3 Tests/live_ui_test.py # 19 kiểm tra chạy chính mã giao diện, đối diện gateway thật
```
Hai bài thử đều dùng máy chủ giả và thư mục dữ liệu tạm, không đụng `data/gateway.db`.

## Ghi chú bảo mật
- API key upstream lưu SQLite (`data/gateway.db`); nên bảo vệ file.
- Log body tắt mặc định; bật debug qua setting `log_body_enabled=1`.
- TLS tuỳ chọn: `tls_enabled=1`, `tls_mode=selfsigned|imported`.