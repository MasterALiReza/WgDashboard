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
    print(f'=== Connected to {host} ===')
    
    commands = [
        "systemctl status wg-dashboard --no-pager -l",
        "ps aux | grep -E 'gunicorn|python.*dashboard'",
        "find / -name 'gunicorn.conf.py' 2>/dev/null"
    ]
    
    for cmd in commands:
        print(f"\n--- Running: {cmd} ---")
        _, stdout, stderr = c.exec_command(cmd)
        out = clean(stdout.read())
        err = clean(stderr.read())
        if out: print(out)
        if err: print("ERR:", err)
        
        if 'find / -name' in cmd and out:
            for path in out.splitlines():
                path = path.strip()
                if '/src/gunicorn.conf.py' in path:
                    repo_dir = path.rsplit('/src/gunicorn.conf.py', 1)[0]
                    print(f"\n--- Checking Repo at {repo_dir} ---")
                    _, out2, _ = c.exec_command(f"cd {repo_dir} && git log -n 5 --oneline && git status")
                    print(clean(out2.read()))
                    _, out3, _ = c.exec_command(f"cd {repo_dir} && cat src/gunicorn.conf.py | grep -E 'workers =|timeout =|keepalive ='")
                    print("Gunicorn Conf Values:")
                    print(clean(out3.read()))

    c.close()
except Exception as e:
    print('SSH Error:', str(e))
