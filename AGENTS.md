# AGENTS.md — Gateway

Cổng HTTP nội bộ tương thích OpenAI & Anthropic, chạy trên chính máy này. Chỉ dùng Python 3 thư viện chuẩn, giao diện là **một file** `static/index.html` (HTML + CSS + JS nội tuyến). Không thêm phụ thuộc ngoài.

## Chạy trên máy
```bash
screen -L -Logfile run.log -dmS gateway python3 main.py   # chạy nền, log vào run.log
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:15199/   # 200 là sống
```
- Cổng đọc từ setting `port` trong `data/gateway.db` (đang dùng `15199`).
- **Từ 1.3.0 nên dựng cổng bằng bộ chạy phiên**: `screen -dmS gateway /var/jb/bin/sh /var/jb/usr/bin/gateway-session standalone` (app Cổng và `gatewayctl on` cũng làm việc này). Bộ chạy phiên tự dò cổng và tự dựng lại `main.py` khi treo.
- **Chạy từ phiên agent phải tách khỏi nhóm tiến trình**: tiến trình nền dựng trong `shell_run` bị giết cùng nhóm khi lệnh xong — gọi thẳng `/var/jb/usr/bin/gatewayctl restart` trong `shell_run` (thử 10:21 ngày 13-11) làm cổng **tắt hẳn**, không kịp ghi dòng nào vào `run.log` và không còn tiến trình nào. Máy không có `setsid`; cách đúng là `screen -dmS gateway …` như trên rồi `curl` kiểm (200) và `ps` xem `SCREEN` + `gateway-session` + `main.py`.
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
python3 Tests/session_test.py         # chạy thật gateway-session: cha chết → tự tắt, TERM → thoát 0
python3 Tests/autocheck_test.py       # vòng tự kiểm: model lỗi/tạm thời, key 401 tự tắt rồi tự bật lại
```
- Cả năm bài thử tự dựng máy chủ giả và dùng thư mục/dữ liệu tạm, **không đụng `data/gateway.db` thật**.
- Thử thật mà không đụng dữ liệu thật: chép `*.py` + `static/` + một bản `data/gateway.db` sang `/tmp/gwlive`, đổi `port` sang cổng khác (15299), cấp một client key tạm (`db.new_client_key(...)`), rồi `screen -L -Logfile /tmp/gwlive/run.log -dmS gwlive python3 /tmp/gwlive/main.py` và đọc `/gateway/auto/status` **kèm key** đó; xong dọn session + thư mục tạm. Cổng thật của người dùng chỉ khởi động lại khi đã hỏi (nó đang chạy mã cũ, nên giao diện mới có thể báo lỗi cho tới lúc đó).
- `session_test.py` thay `@PREFIX@` trong `packaging/gateway-session.in` bằng gốc bootstrap của máy rồi chạy script thật dưới một tiến trình cha giả; cổng giả lấy cổng trống, `GATEWAY_PROJECT_DIR` trỏ vào thư mục tạm (nên `run.log` cũng ở đó).
- Node trên máy **không có WebAssembly** nên `fetch` thật không chạy: bài thử tự viết `fetch` bằng module `http`, và phải gửi kèm `Content-Length` (nếu không, máy chủ đọc thân rỗng).
- Khung DOM giả trong bài thử **không tạo phần tử con từ `innerHTML`**: muốn kiểm phần tử sinh bằng `innerHTML` thì tự dựng lại từ chuỗi HTML.
- `live_ui_test.py` chạy tuần tự nên **trạng thái cuối** mới kiểm được từ Python: việc gì phải kiểm ngay sau một bước thì kiểm trong mã node (`say(...)`) rồi mới in ra.

## Sao lưu / khôi phục
- `GET /gateway/config/export` → file JSON `{"format":"gateway-backup","version":1,...}` gồm `providers`, `api_keys` (key thượng nguồn **nguyên văn**), `models`, `client_keys`, `settings`, `client_key` (key của chính máy, để máy mới vào thẳng).
- `POST /gateway/config/import` → thay toàn bộ các bảng trên bằng nội dung file, trong một transaction; setting thiếu thì thêm mặc định; `port` sai/thiếu thì về `15199`; **không** áp lại chính sách model (giữ đúng bật/tắt như trong file, chỉ tính lại cờ free). Trần thân 4 MB (`MAX_IMPORT_BYTES`).
- Luôn để lại một client key dùng được, theo thứ tự: key trong file → key đang gửi request (`client_key_source:"file"|"request"`) → cấp key mới (`"new"`).
- File mang cổng khác cổng đang nghe thì trả `restart_needed:true` + `port`; nút trong giao diện gọi `POST /gateway/restart` (chỉ loopback) rồi tự `execv` lại tiến trình.
- Giao diện: hộp `#backupSheet`, ô nhập `#backupFile` (ẩn) + `pickBackupFile()`, `exportBackup()`, `importBackupText()`. iPhone/iPad lưu file qua hộp chia sẻ (`navigator.share`), nơi khác thì tải xuống. Hộp chọn tệp của hệ điều hành **không kiểm được trong node** — phải thử tay trên máy.

## Tự động kiểm key & model (người dùng chỉ cần thêm nhà cung cấp)
- Mục tiêu: dán key rồi dùng base URL, **không phải bấm 🧪**. `autocheck.py` là vòng nền, `main.py` gọi `start()` khi cổng khởi động; tắt bằng setting `auto_check=0`.
- Một vòng (`run_once(force)`) lấy tối đa `auto_check_batch` model từ `db.due_models()` (chưa kiểm, tới hạn thử lại, quá `model_stale_secs`) đem thử bằng `proxy.test_model()`, rồi thử lại tối đa 4 key từ `keypool.due_keys()` bằng `proxy.test_key_by_id()`. Xong thì nghỉ `auto_check_secs`; còn việc tới hạn thì nghỉ ngắn 3 s.
- Kết luận: `ok` → kiểm lại sau `model_stale_secs`; lỗi thuộc model (404 `model not found`, không có quyền) → `fail`, thử lại sau `model_retry_secs`; lỗi tạm thời (429/5xx/mạng) và "chưa có key" → **giữ nguyên sức khỏe cũ**, hẹn 60 s; key 401/403 → tắt key kèm hẹn `key_retry_secs` và **không** kết luận model hỏng (một key sai không được làm mất model tốt).
- Lỗi tạm thời **không được phạt key**: `proxy.test_model` gặp 429/5xx thì chỉ hẹn kiểm lại model, không `mark_failure`/`set_key_health`, vì 429 của một model đang bận không phải lỗi của key — phạt sẽ cho key của ncc nghỉ 30 s và chặn cả việc thật. Key chỉ bị kết luận qua `proxy.test_key_by_id` (401/403) và qua lượt gọi thật.
- Key tự bật lại: `mark_failure(401/403)` đặt `auto_disabled=1` + `retry_at`; `test_key_by_id` thấy key sống lại thì `reenable_auto()` bật lại và `db.mark_provider_models_due()` hẹn kiểm lại model của ncc đó ngay. Key người dùng tự tắt (`set_enabled(0)`) thì không ai tự bật.
- Không có vòng lặp vô hạn: mọi kết cục đều đặt `retry_at`, vòng chỉ chạy lại ngay khi thật sự còn việc tới hạn.
- `hide_unverified=1`: `/v1/models` và model mặc định chỉ dùng model `health='ok'`; để `0` (mặc định) thì chỉ ẩn model đã kết luận `fail`.
- Tuyến: `GET /gateway/auto/status` (trạng thái + số việc tồn), `POST /gateway/auto/check` `{force:true}` (kiểm ngay ở luồng riêng). Thêm nhà cung cấp/key/model hoặc đổi chính sách model đều gọi `autocheck.wake()` để kiểm ngay.
- Giao diện: thẻ "🤖 Tự động kiểm key & model" ở mục Keys (`#autoBadge`/`#autoLine`/`#autoSub`/`#autoBtn`), `loadAutoStatus()` và `checkNow()`.

## Quy ước code
- `server.py`: HTTP + các endpoint `/v1/*` và `/gateway/*`. `providers.py`: nhận diện nhà cung cấp, luật model miễn phí. `proxy.py`: gọi thượng nguồn. `db.py`: SQLite. `static/index.html`: toàn bộ giao diện.
- Chuỗi động đặt vào thuộc tính `onclick="…'${jsq(x)}'…"` phải qua `jsq()` (an toàn cho cả chuỗi JS trong attribute); `esc()` chỉ dùng cho nội dung chữ/HTML.
- Markup sinh bằng `innerHTML` **không được lồng `<button>` trong `<button>`** (hay `<a>` trong `<a>`): HTML tự đóng thẻ ngoài, phần còn lại thành anh em trong `.item` (grid) nên mỗi thứ một dòng và giãn hết chiều ngang — đúng gốc lỗi bố cục thẻ nhà cung cấp đã gặp (commit cdb581a). Thẻ vừa hiện chữ vừa chứa nút thì dùng `<div role="button" tabindex="0" onclick=...>`.
- Bố cục ưu tiên di động: thẻ nhà cung cấp gập còn **2 dòng** (dòng 1 = tên + mũi tên, dòng 2 = số liệu + nút 📋/↗); tile model và hàng key cũng **một dòng**, nút nằm bên phải; nút phụ cao ≥32px, hàng 5 nút hành động cao 40px. Muốn thêm dòng thì mở rộng phần `.prov-detail` thay vì chèn vào thẻ gập.
- Mọi tuyến `/gateway/*` đều đòi **client key hợp lệ**; riêng nhập sao lưu và khởi động lại thì loopback không key vẫn được (máy mới chưa có key vẫn khôi phục được). Loopback **không** thay cho key: `GET /gateway/auto/status`, `/gateway/status`, xuất sao lưu… gọi từ máy này mà thiếu key đều 401 (`invalid client key`) — `curl` thử phải kèm `-H "Authorization: Bearer <key>"`.
- Gọi API mà không gửi tên model thì dùng model mặc định; model mặc định **phải là model trò chuyện**, bỏ qua model nhúng/embedding (`chat_model()` trong `server.py`).
- `db.init_db()` phải chạy trước `db.set_setting` (không thì lỗi `no such table: settings`).
- `data/` và `run.log` nằm ngoài git (`.gitignore`).

## Gói cài đặt & bộ chạy cổng theo phiên
- Nguồn nằm trong `packaging/`: `gateway-session.in`, `gatewayctl`, `postinst`, `prerm`; `apelin/build.sh` thay `@PREFIX@` rồi gói bằng `dpkg-deb -b --root-owner-group`. Build bằng `apelin_project_build`, cài bằng `apelin_package_install` (mã gói `wiki.qaq.gateway`, rootless; gói chỉ chứa script trong `/var/jb/usr/bin` + app `GatewaySwitch.app`, **không có LaunchDaemon**, không chứa mã dự án).
- Từ 1.3.0 **không còn dịch vụ nền**: cổng sống theo phiên Apelin. Hợp đồng với apelind: nó `posix_spawn` `<prefix>/usr/bin/gateway-session` (không tham số, uid `mobile`, cùng process group). Script này chạy `main.py` ở tiến trình con, dò cổng mỗi 10 bước (không trả lời 3 lần → TERM → chờ ngắn → KILL → chạy lại), **thoát khi cha mất** (`kill -0 $PPID` hỏng hoặc ppid = 1) sau khi dừng con, và nhận `TERM`/`INT` thì dừng con rồi thoát 0. Log vào `run.log`: "Gateway session: session start / session stop / cha đã mất".
- Chạy tay không theo phiên Apelin: `gateway-session standalone` (hoặc `GATEWAY_STANDALONE=1`) — không theo dõi cha; đây là thứ `gatewayctl on` và app Cổng dùng.
- Biến môi trường khi gỡ lỗi: `GATEWAY_PROJECT_DIR` (mặc định thư mục dự án), `GATEWAY_PROBE_SECS` (10 bước), `GATEWAY_FAIL_HUNG` (3), `GATEWAY_FAIL_START` (6), `GATEWAY_PARENT_PID` (mặc định `$PPID`).
- **Máy này mỗi lần gọi `sleep` tốn thêm ~0,6 s** (exec): `sleep 1` thật ra ~1,5 s, nên `PROBE=10` là chu kỳ dò ~15 s, và chỗ nào cần chờ ngắn thì dùng `sleep 0.2`. Cha mất → phiên tắt đo được **3,3 s** (bài thử); trên máy thật với gói 1.3.0: cổng trả lời sau ~2 s, **cổng tắt 1,2–1,8 s sau khi cha mất** (giết rồi thu dọn, hoặc cha tự thoát → phiên mồ côi ppid=1), phiên thoát mã 0, không sót `main.py`.
- **Đo "cha mất" phải thu dọn tiến trình cha** (`wait()`): tiến trình chết nhưng chưa được thu dọn thành zombie thì `kill -0` vẫn thành công và phiên **không** thấy cha mất — lần đo đầu tiên đã sai vì lý do này.
- `gatewayctl on|off|restart|state|status|pid`: `state` trả **một từ** `dang_chay | dang_treo | khong_chay` (dò bằng `pgrep` + `curl`); `off` dừng hẳn (bootout daemon cũ → dừng phiên → TERM/KILL `main.py` → chờ cổng im); `status` in cho người đọc. Đây là công cụ gỡ lỗi và là thứ app Cổng gọi.
- 1.3.0 **đã bỏ** nút Tắt cổng trong trang quản lý và tuyến `POST /gateway/off` (giữ `POST /gateway/restart`); `paused.py`, cờ `data/paused`, `packaging/daemon.plist.in`, `packaging/gateway-supervise.in` đã xoá — `postinst`/`prerm` chỉ còn dọn chúng khi nâng cấp từ bản ≤1.2.x.
- **Chưa làm**: phía apelind (tự chạy `gateway-session` khi có việc, tự đóng khi hết việc) — gói 1.3.0 mới có phía Gateway; hiện cổng chỉ mở bằng app Cổng hoặc `gatewayctl on`.

## App công tắc (thư mục `app/`)
- App Objective-C một màn hình, **không dùng HTML**: `app/main.m` (giao diện + logic), `app/Info.plist`, `app/ents.plist` (quyền), `app/makeicon.py` sinh `AppIcon60x60@2x.png` / `@3x.png` (chỉ chạy khi muốn vẽ lại biểu tượng).
- **Máy có sẵn clang 16 và ldid** trong `/var/jb/usr/bin` và SDK iPhoneOS 16.4 của Apelin, nên app được biên dịch ngay trên máy trong `apelin/build.sh`:
  `clang -target arm64-apple-ios16.0 -isysroot <SDK> -fobjc-arc -O2 -framework UIKit -framework Foundation -framework QuartzCore` rồi `ldid -S app/ents.plist`. **Bắt buộc ký kèm quyền `platform-application` + `com.apple.private.security.no-sandbox`**: ký trơn `ldid -S` thì chạy là bị `Killed: 9`.
- App hỏi trạng thái bằng `gatewayctl state` (một từ) và lấy địa chỉ từ dòng `Gateway starting on` cuối trong `run.log`; nút bấm chạy `gatewayctl off|on` qua `posix_spawn("/var/jb/bin/sh", …)` rồi đọc lại. **Không dùng `popen`**: máy không có `/bin/sh` (iOS không có shell), và `pkill`/`pgrep` nằm ở `/var/jb/usr/local/bin` nên `gatewayctl` tự đặt `PATH`.
- Cài/gỡ biểu tượng bằng `uicache`: `postinst` chạy `uicache -p <app>`, `prerm` chạy `uicache -u <app>`. Kiểm nhanh: `uicache -i wiki.qaq.gateway.switch`.
- Cài lại app đè lên bản đang chạy thì phải `uicache -p` lại (hoặc respring) mới thấy bản mới.

## Ghi nhớ khi làm việc
- Việc đang dở ghi vào ghi nhớ `handoff` (phạm vi project) theo skill `sep-working-rules`.
- Sửa giao diện thì nêu rõ phần nào **chưa** nhìn thấy trên màn hình (bố cục/màu) để người dùng tự xem.
