import paramiko

host = '85.9.99.250'
pwd = '7K)U-yAu)+hp3B22'

try:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username='root', password=pwd, timeout=12)
    
    cmds = [
        "cat /etc/os-release | grep -E 'PRETTY_NAME|ID='",
        "which python3 git curl wg iptables || echo 'Some dependencies missing'",
        "python3 --version 2>/dev/null || echo 'python3 not found'"
    ]
    for cmd in cmds:
        print(f"\n--- {cmd} ---")
        _, stdout, stderr = c.exec_command(cmd)
        print(stdout.read().decode('utf-8', errors='replace').strip())
    c.close()
except Exception as e:
    print(f"SSH Error: {e}")
