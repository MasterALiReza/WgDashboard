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
    
    # Kill all previous bash/pip setups that are hanging on default pypi timeout
    print("Killing hanging pip/setup processes on target server...")
    c.exec_command("kill -9 $(pgrep -f 'pip install') $(pgrep -f 'requirements.txt') 2>/dev/null || true")
    time.sleep(2)
    
    script = """
set -e
cd /root/WgDashboard/src
python3 -m venv venv
echo "Installing requirements via high-speed mirror with 10s timeout..."
./venv/bin/pip install --default-timeout=10 -i https://mirror-pypi.runsite.ir/simple/ -r requirements.txt || \
./venv/bin/pip install --default-timeout=10 -i https://pypi.tuna.tsinghua.edu.cn/simple/ -r requirements.txt || \
./venv/bin/pip install --default-timeout=10 --break-system-packages -r requirements.txt

echo "Enabling and starting all WireGuard interfaces..."
for conf in /etc/wireguard/*.conf; do
    if [ -f "$conf" ]; then
        iface=$(basename "$conf" .conf)
        echo "Starting $iface..."
        systemctl enable --now wg-quick@${iface}.service || echo "Warning: Failed starting $iface"
    fi
done

echo "Starting WGDashboard service..."
systemctl daemon-reload
systemctl enable wg-dashboard.service
systemctl restart wg-dashboard.service
sleep 3
echo "--- SYSTEMCTL STATUS ---"
systemctl status wg-dashboard.service --no-pager -l | head -n 25 || true
echo "--- WG SHOW ---"
wg show || true
echo "--- PS AUX ---"
ps aux | grep -E 'gunicorn|python' | grep -v 'unattended' || true
"""
    print("Executing instant setup on target server...")
    stdin, stdout, stderr = c.exec_command(script)
    while not stdout.channel.exit_status_ready():
        if stdout.channel.recv_ready():
            print(stdout.channel.recv(1024).decode('utf-8', errors='replace'), end='')
        if stderr.channel.recv_stderr_ready():
            print(stderr.channel.recv_stderr(1024).decode('utf-8', errors='replace'), end='')
        time.sleep(0.1)
    print(stdout.read().decode('utf-8', errors='replace'))
    print(stderr.read().decode('utf-8', errors='replace'))
    c.close()
except Exception as e:
    print(f"Error: {e}")
