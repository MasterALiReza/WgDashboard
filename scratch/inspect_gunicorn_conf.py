import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)
_, stdout, _ = c.exec_command("cat /root/WgDashboard/src/gunicorn.conf.py; sed -n '415,445p' /root/WgDashboard/src/wgd.sh")
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
