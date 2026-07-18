import paramiko

host = '94.183.225.232'
pwd = 'clgWc4fHtn'

try:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username='root', password=pwd, timeout=12)
    
    cmds = [
        "systemctl list-units --type=service | grep -E 'wg|dash|wireguard|panel'",
        "find / -name 'wg-dashboard.ini' -o -name 'wgd.sh' -o -name '*.db' 2>/dev/null | grep -v '/proc/'",
        "ps aux | grep -E 'python|gunicorn|node|php'"
    ]
    for cmd in cmds:
        print(f"\n--- {cmd} ---")
        _, stdout, stderr = c.exec_command(cmd)
        print(stdout.read().decode('utf-8', errors='replace').strip())
    c.close()
except Exception as e:
    print(f"SSH Error: {e}")
