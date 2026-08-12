# Bảo mật và quyền hạn

- ZLAPI là unofficial client dùng tài khoản người dùng thật; có rủi ro vi phạm
  điều khoản Zalo hoặc bị khóa tài khoản. Dùng tài khoản/phạm vi được ủy quyền.
- Không commit cookie, IMEI, QR, session, request body, database URL thật, API key,
  `.env`, PFX, mật khẩu PFX hoặc DPAPI blob.
- Recovery envelope là ciphertext, có thể lưu private Git; vẫn phải review quyền
  repo và rotation khi PFX bị nghi lộ.
- Chỉ `zlapi-web/worker/listener` đọc secret. Modify được cấp theo thư mục chức
  năng; không cấp Modify toàn bộ `C:\ZlapiData`.
- Backend bind loopback 8000. Chỉ shared Caddy mở 80/443.
- `Install-Services.ps1` all-in-one bị khóa bằng flag xác nhận vì nó có network,
  firewall, service-start và scheduled-task side effects.
- Caddy access log không được ghi Authorization, cookie hoặc body. Hạn chế người
  đọc log và xoay theo kích thước.

Chạy `deploy/windows/Audit-Secrets.ps1`, `ruff`, test và review `git diff --cached`
trước commit. Nếu cookie/key lộ, thu hồi tại nguồn, đổi session secret/PFX và kiểm
tra toàn bộ Git history; commit xóa mới không xóa secret khỏi lịch sử.
