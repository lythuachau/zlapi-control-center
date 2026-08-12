# Disaster recovery ZLAPI

## Điều kiện để phục hồi đúng trạng thái cũ

Repository GitHub khôi phục code, migrations, dependency lock, script Windows,
test và tài liệu. Nó cố ý không chứa Zalo cookie/IMEI, database credential,
session secret, PFX recovery, DPAPI blob production hoặc database dump.

Để giữ tài khoản, tin nhắn và cấu hình cũ cần đủ bốn thành phần:

1. PostgreSQL Aiven còn hoạt động hoặc encrypted pg_dump offsite gần nhất.
2. Recovery envelope mới nhất của `master-key.dpapi`.
3. Recovery PFX ngoại tuyến và mật khẩu lưu tách biệt.
4. `zlapi.env` được phục hồi từ password manager hoặc tạo lại với đúng DB URL.

GitHub một mình chỉ dựng được instance sạch. Đây là giới hạn bảo mật bắt buộc,
không phải thiếu sót của script.

## Chuẩn bị trước sự cố

Trên máy tin cậy ngoại tuyến, dùng PowerShell 7 để tạo PFX/CER:

```powershell
$pfxPassword = Read-Host "Recovery PFX password" -AsSecureString
.\deploy\windows\New-OfflineRecoveryCertificate.ps1 `
  -PublicCertificatePath D:\Offline\zlapi-recovery.cer `
  -PrivateKeyPath D:\Offline\zlapi-recovery.pfx `
  -PrivateKeyPassword $pfxPassword
```

Chỉ đưa `.cer` lên VPS rồi tạo envelope:

```powershell
.\deploy\windows\Export-RewrapEnvelope.ps1 `
  -ProtectedKeyPath C:\ZlapiData\secrets\master-key.dpapi `
  -PublicCertificatePath C:\Transfer\zlapi-recovery.cer `
  -EnvelopePath C:\Transfer\zlapi-master-key.envelope.json
```

Envelope có thể commit vào repo hạ tầng private. PFX không bao giờ lên Git/VPS;
mật khẩu không lưu cùng PFX. Chạy `Test-OfflineKeyRecovery.ps1` bằng key giả.

Cài rclone và đặt `ZLAPI_RCLONE_REMOTE` đến bucket private/versioned. Hằng ngày
chạy maintenance và `Test-BackupFreshness.ps1 -MaximumAgeHours 30`. Định kỳ dùng
`Test-BackupRestore.ps1` với database tạm có tên `restore_test`.

## Khôi phục trên VPS mới

1. Cài Git, Python 3.12, Node/npm, PostgreSQL 17 client, FFmpeg/ffprobe, Caddy,
   WinSW và rclone. Chưa đổi DNS/chưa mở 80/443.
2. Clone đúng commit/tag repository vào `C:\Deploy\zlapi-control-center`.
3. Tạo `C:\ZlapiData\secrets` và restore `zlapi.env`. Nếu database Aiven mất,
   tải encrypted dump offsite về nhưng chưa giải mã ra ngoài thư mục tạm bảo vệ.
4. Đưa recovery PFX từ thiết bị offline vào ổ tạm gắn riêng, rồi tạo DPAPI blob
   mới trên VPS:

   ```powershell
   $pfxPassword = Read-Host "Recovery PFX password" -AsSecureString
   .\deploy\windows\Import-OfflineRecoveryKey.ps1 `
     -EnvelopePath C:\Transfer\zlapi-master-key.envelope.json `
     -PrivateKeyPath E:\Offline\zlapi-recovery.pfx `
     -PrivateKeyPassword $pfxPassword `
     -OutputKeyPath C:\ZlapiData\secrets\master-key.dpapi
   ```

5. Nếu phải phục hồi database, dùng `Test-BackupRestore.ps1` trước trên database
   tạm, sau đó mới thực hiện restore chính thức theo runbook của nhà cung cấp.
6. Chuẩn bị layout và release khi các secret đã đủ:

   ```powershell
   .\deploy\windows\Prepare-Layout.ps1
   .\deploy\windows\Deploy-Release.ps1 -SkipServiceRestart
   .\deploy\windows\Prepare-Services.ps1
   ```

7. Cài shared Caddy từ repo stack. Giữ service Manual/Stopped để kiểm tra ACL,
   XML, config Caddy và port. Không dùng installer all-in-one trong restore chuẩn.
8. Cấu hình rclone rồi đăng ký maintenance task (script từ chối ghi đè task cũ):

   ```powershell
   .\deploy\windows\Register-MaintenanceTask.ps1
   ```
9. Trong maintenance window, đặt Automatic và start theo thứ tự:

   ```powershell
   Set-Service zlapi-web -StartupType Automatic
   Set-Service zlapi-worker -StartupType Automatic
   Set-Service zlapi-listener -StartupType Automatic
   Start-Service zlapi-web
   Start-Service zlapi-worker
   Start-Service zlapi-listener
   ```

10. Chạy local acceptance, sau đó mới mở 80/443, đổi DNS và chạy public check:

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/health/live
   Invoke-RestMethod http://127.0.0.1:8000/health/ready
   .\deploy\windows\Test-Deployment.ps1 -Domain zlapi.choulee.indevs.in -UseExistingCaddy
   ```

11. Đăng nhập bằng tài khoản thật, xác minh account identity/cookie/IMEI, tải một
    file nhận được, gửi message test có kiểm soát và quan sát tin mới tự hiện trên
    UI. Chỉ sau đó xóa bản PFX tạm khỏi VPS/ổ gắn.

Hoàn tất khi ba service Running/Automatic, mỗi module có đúng một process tree,
ready local/public, maintenance task tồn tại, backup mới/offsite và hành vi Zalo
thật đã kiểm tra. Giữ VPS cũ/database cũ trong thời gian rollback nếu còn.
