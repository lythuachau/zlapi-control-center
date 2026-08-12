# Kiến trúc production

```text
Browser/API -> Caddy :443 -> FastAPI :8000 -> PostgreSQL Aiven
                              |               ^
                              v               |
                         job queue -> worker -+
                              ^
                              |
                    Zalo listener/WebSocket
```

- `zlapi-web`: UI, REST API, auth, SSE/realtime broker; một Uvicorn worker.
- `zlapi-worker`: lấy job PostgreSQL và thực hiện action Zalo.
- `zlapi-listener`: giữ listener tài khoản, ghi event/message vào PostgreSQL và
  phát notification cho UI.
- Maintenance task: archive, cleanup, encrypted pg_dump và health checks.

Release bất biến nằm trong `C:\Services\ZlapiControl\releases`; junction
`current` chọn release. Env, key, uploads, archive, backup và log nằm ngoài release.

## Mã hóa và trạng thái

Cookie/IMEI/API config trong PostgreSQL và file backup được mã hóa bằng AES-GCM.
AES data key 32 byte được Windows DPAPI LocalMachine bảo vệ tại
`master-key.dpapi`. Database không đủ để khôi phục tài khoản nếu thiếu data key;
blob DPAPI không dùng được trên máy khác. Recovery envelope giải quyết việc này
mà không ghi khóa rõ ra đĩa.

Realtime UI phụ thuộc listener, database và event broker. Nếu UI chỉ thấy tin mới
sau reload, kiểm tra listener singleton, event stream/SSE và cursor trước khi sửa
polling hoặc cache.
