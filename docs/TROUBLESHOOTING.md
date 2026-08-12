# Chẩn đoán production

## Không thể xác minh cookie/IMEI mới

Giữ phiên cũ là hành vi an toàn. Kiểm tra log listener/account identity, clock,
outbound TLS/DNS và response Zalo đã bị thay đổi hay chưa. Không xóa phiên cũ hoặc
ghi đè cookie trước khi probe mới vượt qua xác minh.

## UI không tự hiện tin mới

Kiểm tra theo thứ tự: listener service/process singleton, event được ghi DB,
`/api/v1/events/stream` không bị Caddy timeout/buffer, event broker cursor và
console browser. Reload thấy tin chứng minh DB/API đọc được, chưa chứng minh realtime
stream khỏe.

## Người khác gửi file nhưng không tải được

Xác nhận message attachment có URL/token hợp lệ, account session được chọn đúng,
worker có outbound access, giới hạn `ZLAPI_INBOUND_DOWNLOAD_MAX_MB`, timeout và
Content-Type/Content-Length. Không chuyển cookie hoặc URL riêng tư về browser;
backend phải tải/proxy sau kiểm tra SSRF và kích thước.

## Backend ready lỗi

`/health/live` chỉ xác nhận process; `/health/ready` còn kiểm tra dependency.
Kiểm tra Aiven TLS/connection budget, master key DPAPI và migrations. Khi chuyển
máy, lỗi decrypt gần như chắc chắn do copy blob DPAPI thay vì import envelope.

## Backup không chạy

Chạy `control_center.maintenance backup` dưới cùng service identity, xác nhận
`pg_dump.exe`, database URL, quyền thư mục backup và rclone remote. Chạy
`Test-BackupFreshness.ps1`; file cũ không được tính là backup hoạt động.
