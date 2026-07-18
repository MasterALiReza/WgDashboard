import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('94.183.225.232', username='root', password='clgWc4fHtn', timeout=10)
_, stdout, _ = c.exec_command("dpkg -l | grep -E 'nginx|certbot'; ls -la /etc/nginx/sites-enabled/ /etc/nginx/conf.d/; cat /etc/nginx/sites-enabled/* /etc/nginx/conf.d/* 2>/dev/null | head -n 100")
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
