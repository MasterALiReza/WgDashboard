import paramiko
import sys

host = '178.239.146.188'
pwd = 'iWasKarlYT#4494'

def clean(s):
    if not isinstance(s, str):
        s = s.decode('utf-8', errors='ignore')
    return s.encode('ascii', errors='replace').decode('ascii')

try:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username='root', password=pwd, timeout=15)
    
    commands = [
        "ls -lt /root/WGDashboard/src/log/ | head -n 8",
        "LATEST_ERR=$(ls -t /root/WGDashboard/src/log/error_*.log 2>/dev/null | head -n 1) && [ -n \"$LATEST_ERR\" ] && tail -n 35 \"$LATEST_ERR\" || echo 'No error logs found'",
        "LATEST_ACC=$(ls -t /root/WGDashboard/src/log/access_*.log 2>/dev/null | head -n 1) && [ -n \"$LATEST_ACC\" ] && tail -n 15 \"$LATEST_ACC\" || echo 'No access logs found'"
    ]
    
    for cmd in commands:
        print(f"\n=========================================\n--- CMD: {cmd} ---")
        _, stdout, stderr = c.exec_command(cmd)
        out = clean(stdout.read())
        err = clean(stderr.read())
        if out: print(out)
        if err: print("ERR:", err)

    c.close()
except Exception as e:
    print('SSH Error:', str(e))
