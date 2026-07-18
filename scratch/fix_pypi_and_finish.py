import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko
import time

host = '85.9.99.250'
pwd = '7K)U-yAu)+hp3B22'

try:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username='root', password=pwd, timeout=15)
    
    # Check internet connectivity & DNS
    print("Checking ping to 8.8.8.8 and pypi.org on target server...")
    _, stdout, _ = c.exec_command("ping -c 2 8.8.8.8; curl -I https://pypi.org -m 5 || echo 'PyPI curl failed'")
    print(stdout.read().decode('utf-8', errors='ignore'))
    
    # Finish setup using robust mirror or pip options
    finish_script = """
set -e
cd /root/WgDashboard/src
python3 -m venv venv
echo "Installing python requirements (with mirror fallback if needed)..."
./venv/bin/pip install --no-cache-dir -r requirements.txt || \
./venv/bin/pip install --no-cache-dir -i https://mirror-pypi.runsite.ir/simple/ -r requirements.txt || \
./venv/bin/pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple/ -r requirements.txt

echo "Writing Gunicorn config..."
cat << 'EOF' > /root/WgDashboard/src/gunicorn.conf.py
bind = "0.0.0.0:10086"
workers = 1
worker_class = "gthread"
threads = 10
timeout = 120
keepalive = 5
capture_output = True
accesslog = "-"
errorlog = "-"
loglevel = "info"
EOF

echo "Writing Systemd service..."
cat << 'EOF' > /etc/systemd/system/wg-dashboard.service
[Unit]
Description=WGDashboard Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/WgDashboard/src
Environment=PATH=/root/WgDashboard/src/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/root/WgDashboard/src/venv/bin/python3 /root/WgDashboard/src/venv/bin/gunicorn --config ./gunicorn.conf.py dashboard:app
Restart=always
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable wg-dashboard.service

echo "Enabling and starting WireGuard interfaces..."
for conf in /etc/wireguard/*.conf; do
    if [ -f "$conf" ]; then
        iface=$(basename "$conf" .conf)
        echo "Starting $iface..."
        systemctl enable --now wg-quick@${iface}.service || echo "Failed starting $iface"
    fi
done

echo "Restarting and checking WGDashboard service..."
systemctl restart wg-dashboard.service
sleep 3
systemctl status wg-dashboard.service --no-pager -l | head -n 25
"""
    print("Executing final setup on server...")
    stdin, stdout, stderr = c.exec_command(finish_script)
    while not stdout.channel.exit_status_ready():
        if stdout.channel.recv_ready():
            print(stdout.channel.recv(1024).decode('utf-8', errors='ignore'), end='')
        if stderr.channel.recv_stderr_ready():
            print(stderr.channel.recv_stderr(1024).decode('utf-8', errors='ignore'), end='')
        time.sleep(0.1)
    print(stdout.read().decode('utf-8', errors='ignore'))
    print(stderr.read().decode('utf-8', errors='ignore'))
    c.close()
except Exception as e:
    print(f"Error: {e}")
