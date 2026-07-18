import paramiko
import time

host = '85.9.99.250'
pwd = '7K)U-yAu)+hp3B22'

try:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username='root', password=pwd, timeout=15)
    
    # Kill hanging pip process
    print("Killing hanging pip/bash processes on target...")
    c.exec_command("kill -9 $(pgrep -f 'pip install') $(pgrep -f 'rm -rf /root/WgDashboard') 2>/dev/null || true")
    time.sleep(1)
    
    # Run setup script directly on server
    setup_script = """
set -e
echo "Starting clean setup..."
cd /root/WgDashboard/src
python3 -m venv venv
./venv/bin/pip install --no-cache-dir -r requirements.txt || ./venv/bin/pip install -r requirements.txt --break-system-packages

echo "Creating Gunicorn config..."
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

echo "Creating Systemd service..."
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

echo "Enabling and Starting WireGuard interfaces..."
for conf in /etc/wireguard/*.conf; do
    if [ -f "$conf" ]; then
        iface=$(basename "$conf" .conf)
        echo "Starting $iface..."
        systemctl enable --now wg-quick@${iface}.service || echo "Failed $iface"
    fi
done

echo "Starting WGDashboard service..."
systemctl restart wg-dashboard.service
sleep 3
systemctl status wg-dashboard.service --no-pager -l | head -n 20
"""
    print("Executing setup & start on target server...")
    stdin, stdout, stderr = c.exec_command(setup_script)
    
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
