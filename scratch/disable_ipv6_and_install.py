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
    
    # 1. Disable IPv6 to prevent [Errno 101] Network is unreachable on pip
    print("Disabling IPv6 on target server to force fast IPv4 connections...")
    c.exec_command("sysctl -w net.ipv6.conf.all.disable_ipv6=1 && sysctl -w net.ipv6.conf.default.disable_ipv6=1 && killall -9 pip pip3 python3 bash 2>/dev/null || true")
    time.sleep(2)
    
    script = """
set -e
cd /root/WgDashboard/src
python3 -m venv venv
echo "Installing requirements with IPv4 forced and fast mirrors..."
./venv/bin/pip install --no-cache-dir -i https://mirror-pypi.runsite.ir/simple/ -r requirements.txt || \
./venv/bin/pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple/ -r requirements.txt || \
./venv/bin/pip install --no-cache-dir -r requirements.txt --break-system-packages

echo "Starting all WireGuard interfaces..."
for conf in /etc/wireguard/*.conf; do
    if [ -f "$conf" ]; then
        iface=$(basename "$conf" .conf)
        systemctl enable --now wg-quick@${iface}.service || echo "Warning on $iface"
    fi
done

echo "Starting WGDashboard service..."
systemctl daemon-reload
systemctl enable wg-dashboard.service
systemctl restart wg-dashboard.service
sleep 3

echo "=== SYSTEMCTL STATUS ==="
systemctl status wg-dashboard.service --no-pager -l | head -n 20 || true
echo "=== WG SHOW ==="
wg show | grep -E 'interface|listening port|peer' | head -n 25 || true
"""
    print("Executing install & start...")
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
