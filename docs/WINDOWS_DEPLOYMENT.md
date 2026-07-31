# Triển khai ZLAPI Control Center trên Windows Server 2025

Hệ thống chạy trực tiếp bằng Windows Service. Không cần Docker, WSL, VM, Redis hoặc PostgreSQL cục bộ.

## Điều kiện

- Windows Server 2025 x64, PowerShell chạy bằng Administrator.
- Python 3.12, Node.js 22, Git và Caddy.
- Domain trỏ bản ghi A/AAAA về VPS.
- Aiven PostgreSQL URL có `sslmode=require`.
- Tối thiểu 50 GB trống trên ổ chứa `C:\ZlapiData`.

Production từ chối khởi động với SQLite hoặc cookie không bật chế độ secure.

## Triển khai lần đầu

Mở PowerShell bằng Administrator tại repository:

```powershell
Set-ExecutionPolicy -Scope Process Bypass

.\deploy\windows\Initialize-Config.ps1 `
  -DatabaseUrl "postgresql://avnadmin:PASSWORD@HOST:PORT/defaultdb?sslmode=require" `
  -Domain "control.example.com"
```

Lưu mật khẩu administrator được in đúng một lần. Sau đó tạo release đầu tiên:

```powershell
.\deploy\windows\Deploy-Release.ps1 -SkipServiceRestart
.\deploy\windows\Install-Services.ps1 -Domain "control.example.com"
```

Script thứ hai cài bốn Windows Service, mở firewall 80/443, đăng ký maintenance task, kiểm tra health và xóa bootstrap password khỏi `.env`.

Truy cập `https://control.example.com`, đăng nhập rồi đổi mật khẩu bootstrap bằng API/UI quản trị.

## Cấu trúc trên VPS

```text
C:\Services\ZlapiControl\
  bin\                 WinSW wrappers và Caddy
  current\             Junction đến release đang chạy
  releases\            Các release bất biến
  shared\.env          Cấu hình production
  shared\Caddyfile     Reverse proxy

C:\ZlapiData\
  archive\             Event payload trên 90 ngày
  backups\             pg_dump đã mã hóa
  caddy\               ACME certificate state
  logs\                 Service và access logs
  secrets\             DPAPI-protected AES data key
  uploads\             Media tạm, hết hạn sau 24 giờ
```

Các service chạy bằng virtual service account riêng:

- `NT SERVICE\zlapi-web`
- `NT SERVICE\zlapi-worker`
- `NT SERVICE\zlapi-listener`
- `NT SERVICE\zlapi-caddy`

FastAPI chỉ nghe tại `127.0.0.1:8000`; chỉ Caddy nhận kết nối ngoài tại 80/443.

## Phát hành và rollback

Mỗi lần cập nhật:

```powershell
git pull
.\deploy\windows\Deploy-Release.ps1
```

Script build asset, tạo virtual environment, cài dependency khóa phiên bản, chạy Alembic, chuyển junction, restart web → worker → listener và rollback tự động nếu health check thất bại.

Rollback thủ công:

```powershell
Get-ChildItem C:\Services\ZlapiControl\releases
.\deploy\windows\Rollback-Release.ps1 -ReleaseName "20260729-140000"
```

Không xóa release đang được `current` trỏ tới.

## Aiven và connection pool

Tổng connection budget mặc định dưới 10:

- Web: pool 3, overflow 1.
- Worker: pool 2, overflow 1.
- Listener: pool 1, không overflow.
- Migration/maintenance: tối đa 2.

Theo dõi `/api/v1/health`. Khi database đạt 70% của 1 GB hoặc latency p95 vượt 150 ms, nâng gói Aiven hoặc chuyển PostgreSQL bằng `pg_dump/pg_restore`.

## Backup, archive và cảnh báo

Maintenance chạy hàng ngày lúc 02:15:

1. Nén Zstandard và mã hóa AES-GCM event payload trên 90 ngày.
2. Giữ metadata/cursor trong PostgreSQL để tra cứu.
3. Xóa media tạm hết hạn.
4. Chạy `pg_dump`, mã hóa bản dump và tùy chọn upload bằng rclone.
5. Kiểm tra database, dung lượng đĩa, job failure rate và tài khoản lỗi.

Cài riêng PostgreSQL client tools để có `pg_dump.exe`, rồi cập nhật `ZLAPI_PG_DUMP_PATH`. PostgreSQL server vẫn nằm ngoài VPS.

Để sao chép offsite, cài `rclone`, cấu hình remote S3-compatible và đặt:

```dotenv
ZLAPI_RCLONE_REMOTE=myremote:zlapi-backups
```

Đặt `ZLAPI_ALERT_WEBHOOK_URL` để nhận JSON alert qua webhook.

Thử restore backup vào database tạm trước khi go-live.

## Rewrap khóa DPAPI khi chuyển máy

`master-key.dpapi` dùng DPAPI `LocalMachine`, vì vậy không thể sao chép blob
sang VPS khác. Dùng quy trình envelope sau; không in hoặc ghi AES data key rõ:

1. Trên VPS mới, tạo certificate RSA 3072-bit, non-exportable:

   ```powershell
   .\deploy\windows\New-RewrapCertificate.ps1 `
     -PublicCertificatePath C:\Migration\transfer\zlapi-rewrap-public.cer
   ```

2. Chỉ chuyển file `.cer` công khai sang VPS cũ. Trên VPS cũ, chạy:

   ```powershell
   .\deploy\windows\Export-RewrapEnvelope.ps1 `
     -ProtectedKeyPath C:\ZlapiData\secrets\master-key.dpapi `
     -PublicCertificatePath C:\Migration\transfer\zlapi-rewrap-public.cer `
     -EnvelopePath C:\Migration\transfer\zlapi-master-key.envelope.json
   ```

3. Chuyển envelope sang VPS mới và import bằng thumbprint được tạo ở bước 1:

   ```powershell
   .\deploy\windows\Import-RewrappedMasterKey.ps1 `
     -EnvelopePath C:\Migration\transfer\zlapi-master-key.envelope.json `
     -CertificateThumbprint CERTIFICATE_THUMBPRINT `
     -OutputKeyPath C:\ZlapiData\secrets\master-key.dpapi
   ```

Script từ chối ghi đè key đã tồn tại. Trước khi service đọc key, bảo vệ ACL của
`C:\ZlapiData\secrets` và file key để chỉ `SYSTEM`, Administrators cùng ba tài
khoản service ZLAPI có quyền cần thiết. Sau khi xác minh backup/database test,
xóa envelope và certificate migration. Không xóa key/blob gốc trên VPS cũ cho
đến khi hết thời gian rollback.

## Kiểm tra vận hành

```powershell
Get-Service zlapi-web,zlapi-worker,zlapi-listener,zlapi-caddy
Invoke-RestMethod http://127.0.0.1:8000/health/ready
Get-ScheduledTask -TaskName "ZLAPI Control Maintenance"
```

Sau khi cài đặt, reboot Windows và xác minh bốn service tự khởi động.

Chạy bộ acceptance check:

```powershell
.\deploy\windows\Test-Deployment.ps1 -Domain "control.example.com"
```

Thử một backup trên database Aiven tạm có tên chứa `restore_test`:

```powershell
.\deploy\windows\Test-BackupRestore.ps1 `
  -BackupPath "C:\ZlapiData\backups\zlapi-YYYYMMDDTHHMMSSZ.dump.aes" `
  -TemporaryDatabaseUrl "postgresql://USER:PASSWORD@HOST:PORT/zlapi_restore_test?sslmode=require"
```

## API nội bộ

OpenAPI nằm tại `https://control.example.com/api/docs`.

Ví dụ dùng API key:

```powershell
$headers = @{ Authorization = "Bearer zlc_PREFIX_SECRET" }
Invoke-RestMethod `
  -Uri "https://control.example.com/api/v1/catalog" `
  -Headers $headers
```

Mọi action trả `job_id`. Đọc trạng thái tại `/api/v1/jobs/{job_id}`. API key không dùng CSRF; session trình duyệt bắt buộc gửi `X-CSRF-Token`.

## Giới hạn quan trọng

ZLAPI là reverse-engineered API của tài khoản Zalo thông thường, không phải API chính thức. Endpoint hoặc cơ chế phiên có thể thay đổi bất kỳ lúc nào. Những action chưa có implementation thật được đánh dấu `unsupported` và không bao giờ trả kết quả thành công giả.
