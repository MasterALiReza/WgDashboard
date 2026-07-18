import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)

_, stdout, _ = c.exec_command('ls -lat /root/WgDashboard/src/log | head -n 10')
print("LOG FILES:\n", stdout.read().decode('utf-8', errors='ignore'))

_, stdout, _ = c.exec_command('cat $(ls -t /root/WgDashboard/src/log/error_*.log | head -n 3)')
print("LATEST ERRORS:\n", stdout.read().decode('utf-8', errors='ignore'))

_, stdout, _ = c.exec_command('cat /root/WgDashboard/src/gunicorn.conf.py')
print("GUNICORN CONF:\n", stdout.read().decode('utf-8', errors='ignore'))

c.close()
