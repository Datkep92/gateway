# Kế hoạch: nút Cổng trong Cài đặt Apelin + cổng tự mở/đóng theo Apelin

Người chốt: Sếp. Trạng thái: **kế hoạch, chưa viết mã**. Việc viết mã nằm ở dự án **Apelin ứng dụng** (`5dd2fd28-77d6-417a-a862-3cf3b1fe7089`, thư mục `Projects/Apelin ứng dụng`).

Phía Gateway đã xong và **đã cài**: gói `wiki.qaq.gateway` **1.3.0**, có `/var/jb/usr/bin/gateway-session` (thoát khi cha mất, có `standalone`), `gatewayctl on|off|restart|state|status|pid`; không còn LaunchDaemon, không còn `paused.py`, không còn `POST /gateway/off`.

## 1. Dữ kiện đọc từ mã Apelin hôm nay

| Điều | Sự thật trong mã |
| --- | --- |
| apelind | Chạy theo yêu cầu (launchd dựng khi có tin nhắn tới Mach service `wiki.qaq.apelin.service`), **thoát sau 900 s (15 phút)** không còn việc — `Apelind/Proxy/WorkLedger.swift` (`idleGraceSeconds = 15 * 60`, đổi từ 30 s ngày 2026-11-06 bởi commit `67b28fd`). |
| Dọn tiến trình con | plist `Packaging/wiki.qaq.apelind.plist` không `AbandonProcessGroup`; `gateway-session` cũng tự thoát khi cha mất → hai lớp bảo hiểm. |
| Kênh app → apelind | XPC Mach service; thao tác khai ở `Packages/ApelinKit/Sources/ApelinProtocol/AppOperation.swift`; apelind định tuyến ở `Apelind/Dispatch/Dispatcher.swift` + mỗi miền một `Apelind/Dispatch/*Handler.swift`; phía app là `Apelin/DaemonClient.swift`. |
| Chạy tiến trình con | `Apelind/Spawn.swift`: `posix_spawn`, đòi tệp chạy thuộc root, hạ quyền trước `exec` (`SpawnIdentity.mobile` = uid/gid 501), fd 3 là kênh socketpair, `POSIX_SPAWN_CLOEXEC_DEFAULT`. |
| Thư mục trạng thái | `~/Documents/wiki.qaq.apelin` (`Apelind/Session/SessionHost.swift`) — chỗ đặt tệp chính sách nhỏ nếu cần. |
| Màn Cài đặt | `Apelin/Interface/Settings/SettingsViewController.swift` (mục agent / environment / git / services / about, hàng khai bằng `enum Row`), mẫu trang con: `Interface/Settings/Services/ServicesSettingsController.swift`. |
| Lưu cài đặt phía app | `UserDefaults.standard` (ví dụ `Interface/Settings/AgentPermissions.swift`). |
| Đường dẫn nhị phân cổng | `<gốc cài>/usr/bin/gateway-session`, tức `/var/jb/usr/bin/gateway-session`; gốc cài apelind tự suy từ đường dẫn chính nó (`Apelind/main.swift`). |

## 2. Việc phải làm ở phía Apelin

1. **Giao thức**: thêm thao tác mới (ví dụ `gatewayStatus`, `gatewaySetEnabled`, `gatewayRestart`) vào `AppOperation.swift` + kiểu request/response tương ứng.
2. **apelind**: thêm `Apelind/Dispatch/GatewayHandler.swift` + một `GatewayService`:
   - dựng `gateway-session` bằng `posix_spawn` với uid/gid 501, **không cần** fd 3 (một nhánh spawn riêng, không truyền kênh);
   - nhớ pid, `waitpid` để không để lại zombie, `TERM` → chờ ngắn → `KILL` khi muốn tắt;
   - thiếu tệp nhị phân → trạng thái `chua_cai`, không lỗi, không log ồn;
   - trạng thái đọc lại đúng `gatewayctl state` (một từ `dang_chay | dang_treo | khong_chay`).
3. **Chính sách tự mở/đóng**: một tệp nhỏ (JSON) trong `~/Documents/wiki.qaq.apelin` do apelind đọc lúc khởi động và ghi khi app đổi công tắc → apelind tự dựng cổng khi bật, không phụ thuộc app còn sống hay không.
4. **App**: thêm mục **Cổng** trong Cài đặt (mục Dịch vụ hoặc mục riêng) + trang con: công tắc bật/tắt, dòng trạng thái, nút Bật ngay / Tắt ngay, câu "chưa cài gói Cổng" khi thiếu nhị phân.
5. **Tài liệu**: một dòng vào `CA_NHAN_HOA.md` §6.1; cập nhật `ARCHITECTURE.md`; bài kiểm trong `Scripts/`.

## 3. Điểm cần Sếp chốt

1. **Cổng mở lúc nào** — (a) tự mở mỗi khi Apelin thức nếu công tắc đang bật; (b) chỉ mở khi bấm nút.
2. **Cổng đóng lúc nào** — (a) ngay khi app Apelin rời (vài giây); (b) theo apelind: 15 phút sau khi hết việc; (c) N phút cố định.
3. **Tắt tay có nhớ không** — (a) chỉ cho phiên này, lần sau mở Apelin cổng tự lên lại; (b) nhớ là đang tắt tới khi bật lại.
4. **Chỗ đặt nút** — trong mục **Dịch vụ** sẵn có, hay một mục **Cổng** riêng.

## 4. Dựng, cài, kiểm

- Sửa mã trong dự án Apelin → đẩy lên repo fork → **CI GitHub dựng** (~20–27 phút) → tải `.deb` → `icli pkg install` → mở lại Apelin.
- Kiểm trên máy: bật trong Cài đặt → `curl` cổng 15199 trả 200; tắt → `curl` không kết nối được và `pgrep -f gateway-session` rỗng; rời Apelin → đo thời điểm cổng tắt theo mục 3.2 đã chốt; thiếu gói Cổng → trang Cài đặt báo "chưa cài", không lỗi.
- Ghi rõ phần **chưa nhìn thấy trên màn hình** (bố cục/màu trang Cài đặt) để Sếp tự xem.
