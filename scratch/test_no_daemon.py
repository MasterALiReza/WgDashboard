import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)

_, stdout, stderr = c.exec_command('cd /root/WgDashboard/src && /root/WgDashboard/src/venv/bin/gunicorn --config ./gunicorn.conf.py --daemon false dashboard:app 2>&1')
import time
time.sleep(3)
c.exec_command('pkill -f gunicorn')

out = stdout.read().decode('utf-8', errors='ignore')
err = stderr.read().decode('utf-8', errors='ignore')
print("NO DAEMON OUT:\n", out)
print("NO DAEMON ERR:\n", err)

c.close()
