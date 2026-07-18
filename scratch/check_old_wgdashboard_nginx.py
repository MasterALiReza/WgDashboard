import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('94.183.225.232', username='root', password='clgWc4fHtn', timeout=10)
_, stdout, _ = c.exec_command("cat /etc/nginx/conf.d/wgdashboard.conf /etc/nginx/conf.d/arvan.conf")
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
