# Phát triển và kiểm thử

## Chạy cục bộ

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
npm ci
npm run build

$env:ZLAPI_APP_ENV = "development"
$env:ZLAPI_DATABASE_URL = "sqlite:///./zlapi-control.db"
$env:ZLAPI_DATA_DIR = ".\.data"
$env:ZLAPI_BOOTSTRAP_ADMIN_PASSWORD = "ChangeThisPassword123!"

.\.venv\Scripts\python.exe -m uvicorn control_center.main:app --reload
```

SQLite và tự tạo schema chỉ dành cho development/test. Production bắt buộc PostgreSQL và Alembic.

## Kiểm tra

```powershell
ruff check control_center tests
pytest
npm run build
alembic upgrade head
alembic check
```

Worker chạy một job để debug:

```powershell
python -m control_center.worker --once
```

## Trạng thái action

- `ready`: connector có method và schema tương ứng.
- `unstable`: endpoint có trong thư viện nhưng dễ bị Zalo thay đổi.
- `unsupported`: chưa có endpoint/implementation; backend từ chối xếp job.

Action nguy hiểm cần hai lần xác nhận ở UI và trường `confirmation` phải trùng chính xác tên action ở API.
