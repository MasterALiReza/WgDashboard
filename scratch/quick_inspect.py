import paramiko
import sys

def clean(s):
    if not isinstance(s, str):
        s = s.decode('utf-8', errors='ignore')
    return s.encode('ascii', errors='replace').decode('ascii').strip()

def inspect(host, pwd, label):
    print(f"\n=========================================\n=== {label}: {host} ===\n=========================================")
    try:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(host, username='root', password=pwd, timeout=12)
        
        cmds = [
            ("IP & Default Network Interface", "ip -4 route show default | awk '{print $5}' && ip -o -4 addr show"),
            ("WireGuard Interfaces (/etc/wireguard)", "ls -lh /etc/wireguard/ 2>/dev/null || echo 'No /etc/wireguard directory'"),
            ("WGDashboard Folder & Code version", "ls -la /root/WGDashboard/ 2>/dev/null && cd /root/WGDashboard && git log -n 2 --oneline 2>/dev/null || echo 'No /root/WGDashboard'"),
            ("WGDashboard Service Status", "systemctl status wg-dashboard --no-pager -l 2>/dev/null | head -n 10 || echo 'Service not found/running'"),
            ("Database & Config Files in /root/WGDashboard/src", "ls -lh /root/WGDashboard/src/db/ /root/WGDashboard/src/wg-dashboard.ini /root/WGDashboard/src/gunicorn.conf.py 2>/dev/null || echo 'Files missing'"),
            ("Check Sample Conf PostUp/PostDown rules", "head -n 20 /etc/wireguard/$(ls /etc/wireguard/ 2>/dev/null | grep '\.conf$' | head -n 1) 2>/dev/null || echo 'No conf file'")
        ]
        
        for name, cmd in cmds:
            print(f"\n--- {name} ---")
            _, stdout, stderr = c.exec_command(cmd)
            out = clean(stdout.read())
            err = clean(stderr.read())
            if out: print(out)
            if err and "No such file" not in err: print("ERR:", err)
            
        c.close()
    except Exception as e:
        print(f"SSH Error on {host}: {e}")

inspect('94.183.225.232', 'clgWc4fHtn', 'SOURCE (OLD SERVER)')
inspect('85.9.99.250', '7K)U-yAu)+hp3B22', 'TARGET (NEW SERVER)')
