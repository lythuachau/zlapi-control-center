# Production deployment manifest

| Item | Value |
| --- | --- |
| Public host | `zlapi.choulee.indevs.in` |
| Loopback listener | `127.0.0.1:8000` |
| Services | `zlapi-web`, `zlapi-worker`, `zlapi-listener` |
| Service root | `C:\Services\ZlapiControl` |
| Mutable data | `C:\ZlapiData` |
| Environment file | `C:\ZlapiData\secrets\zlapi.env` (hard-linked as `shared\.env`) |
| DPAPI key | `C:\ZlapiData\secrets\master-key.dpapi` |
| Reverse proxy | shared Caddy on ports 80/443 |
| External database | Aiven PostgreSQL |
| Scheduled task | `ZLAPI Control Maintenance`, daily 02:15 |

Tested on 2026-08-12 with Windows x64, PowerShell 7.6.3, Python 3.12.10,
Node.js 24.18.0/npm 11.16.0, PostgreSQL client 17.10, FFmpeg 8.1.2,
Caddy 2.11.4 and WinSW 2.12.0. Application minimum is Python 3.12.

Production uses one web worker to keep session/event semantics deterministic.
All three app services bind no public port; only Caddy accepts Internet traffic.
The shared Caddy definition is owned by `choulee-windows-vps-stack`.
