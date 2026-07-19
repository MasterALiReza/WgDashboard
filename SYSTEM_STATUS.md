# WGDashboard System Status & Backend Enhancements

## Latest Production Updates (2026-07-19)

### 1. Service Management & Port Collision Prevention (`wgd.sh`)
- Modified `./wgd.sh restart` and `start` commands to intelligently detect and delegate to `systemd` when `wg-dashboard.service` is active.
- Prevented `Errno 98 Address already in use` and `No application module specified` errors during manual or automated service restarts.

### 2. Gunicorn Configuration (`src/gunicorn.conf.py`)
- Standardized absolute file paths (`base_dir`) for `wg-dashboard.ini`, `gunicorn.pid`, and self-healing log directory generation (`./log/access.log` and `./log/error.log`).
- Stabilized multi-threaded worker handling and locked API endpoint execution for heavy peer generation.
