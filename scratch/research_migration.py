import paramiko
import sys

def clean(s):
    if not isinstance(s, str):
        s = s.decode('utf-8', errors='ignore')
    return s.encode('ascii', errors='replace').decode('ascii').strip()

def inspect_server(host, pwd, label):
    print(f"\n=========================================")
    print(f"=== INSPECTING {label}: {host} ===")
    print(f"=========================================")
    try:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(host, username='root', password=pwd, timeout=15)
        
        commands = [
            ("IP & Default Interface", "ip -4 route show default | head -n 1 && ip -o -4 addr show"),
            ("WireGuard Interfaces (/etc/wireguard)", "ls -la /etc/wireguard/ 2>/dev/null || echo 'No /etc/wireguard'"),
            ("Sample Conf PostUp/PostDown", "head -n 25 $(find /etc/wireguard/ -name '*.conf' 2>/dev/null | head -n 1) 2>/dev/null || echo 'No conf file'"),
            ("Find WGDashboard Installation", "find / -name 'gunicorn.conf.py' -o -name 'wgd.sh' 2>/dev/null | grep -E 'WGDashboard|wg'"),
            ("Check WGDashboard Service", "systemctl status wg-dashboard --no-pager -l || echo 'Service not installed/running'"),
            ("Check WGDashboard Database Files", "find /root -name '*.db' -o -name '*.sqlite*' 2>/dev/null | grep -i wg"),
            ("IP Forwarding Status", "sysctl net.ipv4.ip_forward")
        ]
        
        for name, cmd in commands:
            print(f"\n--- {name} ---")
            _, stdout, stderr = c.exec_command(cmd)
            out = clean(stdout.read())
            err = clean(stderr.read())
            if out: print(out)
            if err and "No such file" not in err: print("ERR:", err)
            
        c.close()
    except Exception as e:
        print(f"SSH Error on {host}: {e}")

inspect_server('94.183.225.232', 'clgWc4fHtn', 'SOURCE SERVER (OLD)')
inspect_server('85.9.99.250', '7K)U-yAu)+hp3B22', 'TARGET SERVER (NEW)')
