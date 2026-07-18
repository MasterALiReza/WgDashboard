import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)

_, stdout, _ = c.exec_command('ls -ld /root/WgDashboard/src/log /root/WgDashboard/src/gunicorn.pid 2>&1')
print("DIR CHECK:\n", stdout.read().decode('utf-8', errors='ignore'))

c.exec_command('mkdir -p /root/WgDashboard/src/log')
c.exec_command('systemctl restart wg-dashboard')
import time
time.sleep(3)

_, stdout, _ = c.exec_command('systemctl status wg-dashboard --no-pager')
print("STATUS AFTER MKDIR:\n", stdout.read().decode('utf-8', errors='ignore')[:600])

c.close()
