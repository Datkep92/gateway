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
python3 Tests/e2e_test.py             # đầu-cuối qua HTTP, máy chủ giả + DB tạm
python3 Tests/backup_restore_test.py  # xuất ở máy A, nhập ở máy B mới, khởi động lại
python3 Tests/live_ui_test.py         # chạy chính mã giao diện trong node, đối diện gateway thật
```
- Cả ba bài thử tự dựng máy chủ giả và dùng thư mục/dữ liệu tạm, **không đụng `data/gateway.db` thật**.
- Node trên máy **không có WebAssembly** nên `fetch` thật không chạy: bài thử tự viết `fetch` bằng module `http`, và phải gửi kèm `Content-Length` (nếu không, máy chủ đọc thân rỗng).
- Khung DOM giả trong bài thử **không tạo phần tử con từ `innerHTML`**: muốn kiểm phần tử sinh bằng `innerHTML` thì tự dựng lại từ chuỗi HTML.
- `live_ui_test.py` chạy tuần tự nên **trạng thái cuối** mới kiểm được từ Python: việc gì phải kiểm ngay sau một bước thì kiểm trong mã node (`say(...)`) rồi mới in ra.

## Sao lưu / khôi phục
- `GET /gateway/config/export` → file JSON `{"format":"gateway-backup","version":1,...}` gồm `providers`, `api_keys` (key thượng nguồn **nguyên văn**), `models`, `client_keys`, `settings`, `client_key` (key của chính máy, để máy mới vào thẳng).
- `POST /gateway/config/import` → thay toàn bộ các bảng trên bằng nội dung file, trong một transaction; setting thiếu thì thêm mặc định; `port` sai/thiếu thì về `15199`; **không** áp lại chính sách model (giữ đúng bật/tắt như trong file, chỉ tính lại cờ free). Trần thân 4 MB (`MAX_IMPORT_BYTES`).
- Luôn để lại một client key dùng được, theo thứ tự: key trong file → key đang gửi request (`client_key_source:"file"|"request"`) → cấp key mới (`"new"`).
- File mang cổng khác cổng đang nghe thì trả `restart_needed:true` + `port`; nút trong giao diện gọi `POST /gateway/restart` (chỉ loopback) rồi tự `execv` lại tiến trình.
- Giao diện: hộp `#backupSheet`, ô nhập `#backupFile` (ẩn) + `pickBackupFile()`, `exportBackup()`, `importBackupText()`. iPhone/iPad lưu file qua hộp chia sẻ (`navigator.share`), nơi khác thì tải xuống. Hộp chọn tệp của hệ điều hành **không kiểm được trong node** — phải thử tay trên máy.

## Quy ước code
- `server.py`: HTTP + các endpoint `/v1/*` và `/gateway/*`. `providers.py`: nhận diện nhà cung cấp, luật model miễn phí. `proxy.py`: gọi thượng nguồn. `db.py`: SQLite. `static/index.html`: toàn bộ giao diện.
- Endpoint `/gateway/*` chỉ cho máy này (loopback) hoặc khi có client key hợp lệ. Riêng **xuất sao lưu luôn cần client key** (đã thử: loopback không key → 401), còn **nhập sao lưu và khởi động lại thì loopback không key vẫn được** (máy mới chưa có key vẫn khôi phục được).
- Gọi API mà không gửi tên model thì dùng model mặc định; model mặc định **phải là model trò chuyện**, bỏ qua model nhúng/embedding (`chat_model()` trong `server.py`).
- `db.init_db()` phải chạy trước `db.set_setting` (không thì lỗi `no such table: settings`).
- `data/` và `run.log` nằm ngoài git (`.gitignore`).

## Gói cài đặt & công tắc bật/tắt
- Nguồn nằm trong `packaging/`: `daemon.plist.in`, `gateway-supervise.in`, `gatewayctl`, `postinst`, `prerm`; `apelin/build.sh` thay `@PREFIX@` rồi gói bằng `dpkg-deb -b --root-owner-group`. Build bằng `apelin_project_build`, cài bằng `apelin_package_install` (mã gói `wiki.qaq.gateway`, rootless, chỉ chứa plist + script trong `/var/jb` và app `GatewaySwitch.app`, không chứa mã dự án).
- **Trên máy này launchd KHÔNG chạy thẳng một script**: đặt script làm `ProgramArguments[0]` thì dịch vụ nạp được nhưng chết ngay (`last exit code = 78: EX_CONFIG`, `state = spawn scheduled`). Phải gọi qua shell: `["@PREFIX@/bin/sh", "@PREFIX@/usr/bin/gateway-supervise"]` — giống `com.ofm.watchdog.plist`.
- `gateway-supervise` chạy vòng lặp: có `data/paused` → `paused.py` (trang "Bật cổng" ở **đúng cổng** đang dùng), không thì `main.py`. Dịch vụ: `UserName mobile`, `RunAtLoad` + `KeepAlive`, log vào `run.log` của dự án.
- Công tắc: nút **Tắt cổng** trong giao diện gọi `POST /gateway/off` (**cần client key**, chỉ loopback không đủ); lệnh `gatewayctl on|off|restart|status|pid`. `gatewayctl off` phải **dừng tiến trình** (không chỉ ghi cờ) thì bộ giám sát mới chuyển sang trang bật lại. Trang tắt gọi `POST /on` **không cần key**.
- Khi đang tắt: trang chủ trả 200 kèm nút "Bật cổng", `/v1/*` trả 503. Muốn kiểm nhanh: `gatewayctl status`.
- Chưa kiểm: tự mở lại sau khi khởi động máy (chưa reboot lại máy từ lúc cài).

## App công tắc (thư mục `app/`)
- App Objective-C một màn hình, **không dùng HTML**: `app/main.m` (giao diện + logic), `app/Info.plist`, `app/ents.plist` (quyền), `app/makeicon.py` sinh `AppIcon60x60@2x.png` / `@3x.png` (chỉ chạy khi muốn vẽ lại biểu tượng).
- **Máy có sẵn clang 16 và ldid** trong `/var/jb/usr/bin` và SDK iPhoneOS 16.4 của Apelin, nên app được biên dịch ngay trên máy trong `apelin/build.sh`:
  `clang -target arm64-apple-ios16.0 -isysroot <SDK> -fobjc-arc -O2 -framework UIKit -framework Foundation -framework QuartzCore` rồi `ldid -S app/ents.plist`. **Bắt buộc ký kèm quyền `platform-application` + `com.apple.private.security.no-sandbox`**: ký trơn `ldid -S` thì chạy là bị `Killed: 9`.
- App đọc cờ `data/paused` (đang tắt) và đuôi `run.log` (`Gateway starting on` / `Gateway paused on`) để hiện trạng thái + địa chỉ; nút bấm chạy `gatewayctl off|on` qua `posix_spawn("/var/jb/bin/sh", …)` rồi đọc lại. **Không dùng `popen`**: máy không có `/bin/sh` (iOS không có shell), và `pkill`/`pgrep` nằm ở `/var/jb/usr/local/bin` nên `gatewayctl` tự đặt `PATH`.
- Cài/gỡ biểu tượng bằng `uicache`: `postinst` chạy `uicache -p <app>`, `prerm` chạy `uicache -u <app>`. Kiểm nhanh: `uicache -i wiki.qaq.gateway.switch`.
- Cài lại app đè lên bản đang chạy thì phải `uicache -p` lại (hoặc respring) mới thấy bản mới.

## Ghi nhớ khi làm việc
- Việc đang dở ghi vào ghi nhớ `handoff` (phạm vi project) theo skill `sep-working-rules`.
- Sửa giao diện thì nêu rõ phần nào **chưa** nhìn thấy trên màn hình (bố cục/màu) để người dùng tự xem.
