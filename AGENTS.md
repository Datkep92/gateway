# AGENTS.md — Gateway

Cổng HTTP nội bộ tương thích OpenAI & Anthropic, chạy trên chính máy này. Chỉ dùng Python 3 thư viện chuẩn, giao diện là **một file** `static/index.html` (HTML + CSS + JS nội tuyến). Không thêm phụ thuộc ngoài.

## Chạy trên máy
```bash
screen -L -Logfile run.log -dmS gateway python3 main.py   # chạy nền, log vào run.log
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:15199/   # 200 là sống
```
- Cổng đọc từ setting `port` trong `data/gateway.db` (đang dùng `15199`).
- **Khởi động lại**: `screen -S gateway -X quit; pkill -f "python3 main.py"; sleep 1; screen -L -Logfile run.log -dmS gateway python3 main.py`.
  Kiểm `pgrep -f "python3 main.py"` sau khi dừng: tiến trình con có thể sống sót khỏi session.
- Trang HTML được đọc lại từ đĩa mỗi lần gọi nên sửa `static/index.html` là thấy ngay; **sửa `server.py` thì phải khởi động lại**.
- `lsof`/`netstat` không đáng tin trên máy này; dùng `curl` để dò.

## Kiểm tra
```bash
python3 -m py_compile server.py
python3 Tests/e2e_test.py       # đầu-cuối qua HTTP, máy chủ giả + DB tạm
python3 Tests/live_ui_test.py   # chạy chính mã giao diện trong node, đối diện gateway thật
```
- Cả hai bài thử tự dựng máy chủ giả và dùng thư mục/dữ liệu tạm, **không đụng `data/gateway.db` thật**.
- Node trên máy **không có WebAssembly** nên `fetch` thật không chạy: bài thử tự viết `fetch` bằng module `http`, và phải gửi kèm `Content-Length` (nếu không, máy chủ đọc thân rỗng).
- Khung DOM giả trong bài thử **không tạo phần tử con từ `innerHTML`**: muốn kiểm phần tử sinh bằng `innerHTML` thì tự dựng lại từ chuỗi HTML.

## Quy ước code
- `server.py`: HTTP + các endpoint `/v1/*` và `/gateway/*`. `providers.py`: nhận diện nhà cung cấp, luật model miễn phí. `proxy.py`: gọi thượng nguồn. `db.py`: SQLite. `static/index.html`: toàn bộ giao diện.
- Endpoint `/gateway/*` chỉ cho máy này (loopback) hoặc khi có client key hợp lệ.
- Gọi API mà không gửi tên model thì dùng model mặc định; model mặc định **phải là model trò chuyện**, bỏ qua model nhúng/embedding (`chat_model()` trong `server.py`).
- `db.init_db()` phải chạy trước `db.set_setting` (không thì lỗi `no such table: settings`).
- `data/` và `run.log` nằm ngoài git (`.gitignore`).

## Ghi nhớ khi làm việc
- Việc đang dở ghi vào ghi nhớ `handoff` (phạm vi project) theo skill `sep-working-rules`.
- Sửa giao diện thì nêu rõ phần nào **chưa** nhìn thấy trên màn hình (bố cục/màu) để người dùng tự xem.
