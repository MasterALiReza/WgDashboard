import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)

service_content = """[Unit]
Description=WGDashboard Service
After=network.target

[Service]
Type=forking
PIDFile=/root/WgDashboard/src/gunicorn.pid
WorkingDirectory=/root/WgDashboard/src
Environment=PATH=/root/WgDashboard/src/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/root/WgDashboard/src/venv/bin/gunicorn --config ./gunicorn.conf.py dashboard:app
ExecReload=/bin/kill -s HUP $MAINPID
ExecStop=/bin/kill -s TERM $MAINPID
Restart=always
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
"""

sftp = c.open_sftp()
with sftp.file('/etc/systemd/system/wg-dashboard.service', 'w') as f:
    f.write(service_content)
sftp.close()

c.exec_command('systemctl daemon-reload')
c.exec_command('systemctl stop wg-dashboard')
c.exec_command('pkill -f gunicorn; pkill -f "[p]ython3.*dashboard"')
import time
time.sleep(2)

_, stdout, _ = c.exec_command('cd /root/WgDashboard/src && ./wgd.sh restart')
out = stdout.read().decode('utf-8', errors='ignore')
print("RESTART COMMAND OUTPUT:\n", out)

_, stdout, _ = c.exec_command('systemctl status wg-dashboard --no-pager')
status_out = stdout.read().decode('utf-8', errors='ignore')
print("SERVICE STATUS:\n", status_out[:600])

c.close()
