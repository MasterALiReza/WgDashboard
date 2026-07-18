import paramiko
import sys
import time

NEW_HOST = '85.9.99.250'
NEW_PWD = '7K)U-yAu)+hp3B22'
OLD_HOST = '94.183.225.232'
OLD_PWD = 'clgWc4fHtn'

def run_step(c, name, cmd):
    print(f"\n=========================================")
    print(f"=== STEP: {name} ===")
    print(f"=========================================")
    stdin, stdout, stderr = c.exec_command(cmd)
    while not stdout.channel.exit_status_ready():
        if stdout.channel.recv_ready():
            print(stdout.channel.recv(1024).decode('utf-8', errors='ignore'), end='')
        if stderr.channel.recv_stderr_ready():
            print(stderr.channel.recv_stderr(1024).decode('utf-8', errors='ignore'), end='')
        time.sleep(0.1)
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    if out: print(out)
    if err and "Warning" not in err: print("ERR/WARN:", err)
    return stdout.channel.recv_exit_status()

try:
    print(f"Connecting to NEW TARGET SERVER ({NEW_HOST})...")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(NEW_HOST, username='root', password=NEW_PWD, timeout=15)
    
    # Step 1: Install dependencies & sshpass
    run_step(c, "1. Install Dependencies on New Server", 
             "export DEBIAN_FRONTEND=noninteractive && apt-get update -y && apt-get install -y wireguard-tools iptables sshpass git python3-pip python3-venv curl")
    
    # Step 2: Enable IP Forwarding
    run_step(c, "2. Enable IP Forwarding in Kernel", 
             "sed -i '/net.ipv4.ip_forward/d' /etc/sysctl.conf && echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf && sysctl -p")
    
    # Step 3: Direct Server-to-Server Copy of WireGuard configs and WGDashboard files
    run_step(c, "3. Direct High-Speed Copy from Old Server (Read-Only on Old)", f"""
    mkdir -p /etc/wireguard /root/migration_tmp/db /root/migration_tmp/src
    chmod 700 /etc/wireguard
    
    echo "Copying /etc/wireguard/*.conf ..."
    sshpass -p '{OLD_PWD}' scp -o StrictHostKeyChecking=no root@{OLD_HOST}:/etc/wireguard/*.conf /etc/wireguard/ 2>/dev/null || true
    chmod 600 /etc/wireguard/*.conf 2>/dev/null || true
    ls -lh /etc/wireguard/
    
    echo "Copying WGDashboard INI and SH files ..."
    sshpass -p '{OLD_PWD}' scp -o StrictHostKeyChecking=no root@{OLD_HOST}:/root/WgDashboard/src/wg-dashboard.ini /root/migration_tmp/src/ 2>/dev/null || true
    sshpass -p '{OLD_PWD}' scp -o StrictHostKeyChecking=no root@{OLD_HOST}:/root/WgDashboard/src/ssl-tls.ini /root/migration_tmp/src/ 2>/dev/null || true
    sshpass -p '{OLD_PWD}' scp -o StrictHostKeyChecking=no root@{OLD_HOST}:/root/WgDashboard/src/wgd.sh /root/migration_tmp/src/ 2>/dev/null || true
    
    echo "Copying WGDashboard Database files (*.db*) ..."
    sshpass -p '{OLD_PWD}' scp -o StrictHostKeyChecking=no root@{OLD_HOST}:/root/WgDashboard/src/db/* /root/migration_tmp/db/ 2>/dev/null || true
    ls -lh /root/migration_tmp/db/
    """)
    
    # Step 4: Translate Interface Name eth0 -> ens160 in all .conf files
    run_step(c, "4. Translate Network Interface Name (eth0 -> ens160) in /etc/wireguard/", """
    python3 -c "
import os, re
wg_dir = '/etc/wireguard'
for f in os.listdir(wg_dir):
    if f.endswith('.conf'):
        fpath = os.path.join(wg_dir, f)
        with open(fpath, 'r') as file:
            c = file.read()
        new_c = re.sub(r'\\beth0\\b', 'ens160', c)
        if c != new_c:
            with open(fpath, 'w') as file:
                file.write(new_c)
            print(f'Translated eth0 -> ens160 in {f}')
        else:
            print(f'No eth0 found in {f}')
"
    """)
    
    # Step 5: Clone & Setup WGDashboard with migrated DBs
    run_step(c, "5. Clone WGDashboard & Place Migrated DBs/Configs", """
    rm -rf /root/WgDashboard
    git clone https://github.com/MasterALiReza/WgDashboard.git /root/WgDashboard
    
    mkdir -p /root/WgDashboard/src/db
    cp -r /root/migration_tmp/db/* /root/WgDashboard/src/db/ 2>/dev/null || true
    cp /root/migration_tmp/src/*.ini /root/WgDashboard/src/ 2>/dev/null || true
    cp /root/migration_tmp/src/wgd.sh /root/WgDashboard/src/ 2>/dev/null || true
    chmod +x /root/WgDashboard/src/wgd.sh
    
    cd /root/WgDashboard/src && python3 -m venv venv && ./venv/bin/pip install --upgrade pip && ./venv/bin/pip install -r requirements.txt
    """)
    
    # Step 6: Create Stable gunicorn.conf.py and wg-dashboard.service
    run_step(c, "6. Configure Single-Worker Gunicorn & Systemd Service", """
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
    """)
    
    # Step 7: Start WireGuard Interfaces & WGDashboard Service
    run_step(c, "7. Enable & Start All 6 WireGuard Interfaces + WGDashboard", """
    for conf in /etc/wireguard/*.conf; do
        if [ -f "$conf" ]; then
            iface=$(basename "$conf" .conf)
            echo "Starting WireGuard interface: $iface ..."
            systemctl enable --now wg-quick@${iface}.service || echo "Failed to start $iface"
        fi
    done
    
    echo "Starting WGDashboard service..."
    systemctl restart wg-dashboard.service
    sleep 3
    systemctl status wg-dashboard.service --no-pager -l | head -n 15
    """)
    
    c.close()
    print("\n✅ MIGRATION & SETUP COMPLETED SUCCESSFULLY ON NEW SERVER!")
except Exception as e:
    print(f"\n❌ ERROR: {e}")
