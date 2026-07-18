import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)

c.exec_command('systemctl stop wg-dashboard; pkill -f gunicorn; pkill -f "[p]ython3.*dashboard"')
import time
time.sleep(2)

_, stdout, stderr = c.exec_command('cd /root/WgDashboard/src && /root/WgDashboard/src/venv/bin/gunicorn --config ./gunicorn.conf.py dashboard:app 2>&1')
out = stdout.read().decode('utf-8', errors='ignore')
err = stderr.read().decode('utf-8', errors='ignore')
print("MANUAL RUN OUT:\n", out)
print("MANUAL RUN ERR:\n", err)

_, stdout, _ = c.exec_command('lsof -i :10086; ls -la /root/WgDashboard/src/gunicorn.pid /root/WgDashboard/src/log/ 2>/dev/null')
print("AFTER MANUAL:\n", stdout.read().decode('utf-8', errors='ignore'))

c.close()
