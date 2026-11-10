# Phương án: cổng chỉ sống theo Apelin (bỏ dịch vụ nền tự chạy)

Trạng thái: **chỉ là thiết kế, chưa viết mã**. Người chốt: Sếp (qua hội thoại điều phối).

Quyết định đã chốt (1A / 2A / 3A):

1. **1A** — "Apelin ngừng" = đóng app, cổng đóng sau đó một lúc.
2. **2A** — bỏ hẳn tự chạy nền; cổng chỉ sống theo Apelin.
3. **3A** — cổng không phục vụ app/proxy chạy lâu, nên đóng giữa chừng không cần ân hạn dài.

## 1. Dữ kiện đo trên máy (sửa lại một giả định trong câu hỏi)

Đọc `/var/jb/Library/LaunchDaemons/wiki.qaq.apelind.plist` và `ps`:

- `apelind` là dịch vụ **on-demand**: không `RunAtLoad`, không `KeepAlive`; launchd dựng nó khi có tin nhắn đầu tiên tới Mach service `wiki.qaq.apelin.service`.
- Nó sống khi còn việc (app còn kết nối, còn request, còn tác vụ, còn worker trong session host) và **thoát 30 giây sau khi hết việc cuối cùng**.
- Chạy bằng **root**; session host và các worker là **con của nó, cùng process group**, và plist ghi rõ "go with it: no AbandonProcessGroup" — tức launchd dọn cả nhóm tiến trình khi apelind thoát.

Hệ quả: mốc "đóng app → cổng đóng sau ~15 phút" **không đúng**; thực tế là **~30 giây**, và chỉ khi không còn tác vụ nào đang chạy. Nếu Sếp muốn đúng "15 phút" thì phải thêm phần giữ lại (mục 5, câu hỏi 1).

## 2. Kiến trúc đề xuất

Neo duy nhất là `apelind`, không móc vào app iOS (app có thể bị iOS tắt mà không chạy mã dọn dẹp — đúng điểm B của câu hỏi 1).

```
app Apelin đóng  ──►  hết việc  ──►  apelind thoát (+30s)
                                        │  (launchd dọn cả process group)
                                        ▼
                                    cổng Gateway tắt theo
```

- apelind thức (lần đầu có việc) → nó chạy tiến trình cổng như **con ruột**, trong cùng process group.
- Hết việc → apelind thoát → launchd dọn nhóm → cổng tắt. Không cần mã dọn dẹp ở phía nào.
- apelind chết bất thường → cùng cơ chế, không còn cổng mồ côi.
- Trong lúc sống, cổng vẫn tự dò và tự dựng lại nếu tiến trình chính treo (giữ như hiện nay), chỉ khác là không tự mọc lại sau khi phiên Apelin kết thúc.

Hợp đồng giữa hai bên rất mỏng, một chiều:

> apelind chạy `/var/jb/usr/bin/gateway-session` (không tham số, chạy bằng user `mobile`, thư mục làm việc là thư mục dự án Gateway). Tiến trình này tự lo cổng, tự ghi log, tự thoát khi cha mất. Phía Apelin không cần biết gì thêm, cũng không cần API điều khiển.

## 3. Phía Gateway (dự án này) — các thay đổi cụ thể

| Việc | Chi tiết |
| --- | --- |
| Bỏ dịch vụ nền | Không cài `wiki.qaq.gateway.plist` nữa (`packaging/daemon.plist.in` ra khỏi gói). `postinst` **một lần** bootout daemon cũ để bản 1.2.x đang cài không đá nhau với cơ chế mới. |
| `gateway-supervise` → `gateway-session` | Chạy `main.py` ở tiến trình con; vòng lặp dò cổng mỗi 10 s như cũ (treo → giết → chạy lại); **thoát khi cha mất** (`kill -0 $PPID` thất bại hoặc `getppid()` = 1); nhận `TERM`/`INT` thì dừng con (TERM → 1 s → KILL) rồi thoát 0. Ghi 1 dòng "session start / session stop" vào `run.log`. |
| Cổng | Vẫn đọc setting `port` trong `data/gateway.db`; vẫn chỉ nghe loopback trừ khi bật LAN. |
| Cờ `data/paused` + `paused.py` | Không còn nghĩa (không có "đang tắt mà vẫn nghe cổng"). Đề xuất bỏ khỏi luồng chính; giữ file cũng vô hại nhưng nên xoá để khỏi hiểu nhầm. |
| App "Cổng" (`app/`) | Theo 2A thì công tắc nằm ở Apelin, nên app thành thừa → đề xuất **bỏ khỏi gói**. Nếu muốn giữ để gỡ lỗi thì giữ nguyên, chỉ đổi chữ. |
| `gatewayctl` | Giữ như công cụ gỡ lỗi chạy tay: `on` = chạy `gateway-session` nền (không tự phục hồi sau khi reboot), `off` = dừng hẳn, `status`/`pid` chỉ đọc thực tế (bỏ phụ thuộc cờ `paused`, sửa lỗi `pgrep` khớp nhầm chính nó). |
| Gói | Lên **1.3.0**, mô tả "cổng chỉ chạy cùng Apelin"; không còn mục "tự chạy khi khởi động máy". |
| Trang quản lý | Thẻ "Công tắc cổng" trong `static/index.html` không còn ý nghĩa → bỏ hoặc đổi thành "Cổng đang chạy theo phiên Apelin". |

## 4. Phía Apelin (apelind — hội thoại dự án Apelin lo)

1. Lúc apelind khởi động (sau khi dựng session host): `posix_spawn` `/var/jb/usr/bin/gateway-session`, cùng process group, uid `mobile` (không chạy cổng bằng root).
2. Trước khi thoát: `TERM` → chờ ≤ 5 s → `KILL` (hoặc để launchd dọn nhóm).
3. File không tồn tại (chưa cài gói Gateway) → bỏ qua, không lỗi, không log ồn.
4. Đường dẫn gói là cố định (`/var/jb/usr/bin/gateway-session`); thư mục dự án nằm trong script Gateway, nên đổi chỗ code chỉ sửa một dòng, không phải sửa lại Apelin.
5. Không cần gì trong app iOS: đóng app → apelind hết việc → +30 s thoát → cổng tắt.

Chi phí: sửa mã Swift của apelind, phải đẩy CI GitHub (~20–27 phút) rồi cài lại; không phải sửa vài phút.

## 5. Điểm cần Sếp chốt trước khi viết mã

1. **30 giây thật, không phải 15 phút.** Có muốn "giữ thêm N phút sau khi Apelin hết việc" không? (0 = đóng sau ~30 s; 15 phút = thêm bộ đếm ở phía apelind.)
2. **Cổng mở lúc nào:** ngay khi apelind thức (đơn giản nhất, nhưng tốn ~50 MB RAM khi Apelin chạy dù không ai dùng cổng), hay chỉ khi Apelin bật mục "Cổng" trong Cài đặt (thêm một cờ ở phía Apelin)?
3. **App "Cổng" và nút Tắt cổng trong trang quản lý:** bỏ hẳn, hay giữ lại làm công cụ gỡ lỗi?

## 6. Kế hoạch kiểm

- Trong dự án: 3 bài kiểm hiện có phải còn đạt (67/67, 24/24, 38/38) sau khi đổi sang `gateway-session`; thêm bài mới: chạy `gateway-session` dưới một tiến trình cha giả → giết cha → con phải tắt trong ≤ 2 s và không còn tiến trình nào; gửi `TERM` → tắt gọn; cổng vẫn 200 trong lúc chạy.
- Trên máy: gỡ bản cũ (còn daemon) → cài 1.3.0 → `gatewayctl on` để chắc cổng sống → mở rồi đóng app Apelin → đo thời điểm cổng tắt (kỳ vọng ~30 s sau khi hết tác vụ) → `curl` 200 trước, không kết nối được sau.
- Kiểm chốt: `pgrep -f gateway-session` rỗng sau khi apelind thoát — không có tiến trình mồ côi.

## 7. Rủi ro và cách chặn

| Rủi ro | Chặn |
| --- | --- |
| Bản 1.2.x đang cài vẫn có daemon tự chạy → hai cơ chế đá nhau | `postinst` bản mới bootout daemon cũ một lần; cài xong kiểm `launchctl print` rỗng. |
| apelind thoát 30 s làm cổng tắt giữa lúc đang dùng | App còn kết nối thì apelind còn sống, nên chỉ tắt khi thật sự rời Apelin; đây chính là điều 1A chọn. |
| Cổng chạy root | Đặt uid `mobile` ngay trong `posix_spawn` của apelind. |
| Mở/đóng app liên tục làm cổng nhấp nháy | Chọn mục 5.1 (giữ thêm N phút) nếu thấy phiền. |
| Cài/gỡ gói lúc apelind đang chạy | Cổng chỉ lên ở lần apelind sau; muốn ngay thì `gatewayctl on`. |
