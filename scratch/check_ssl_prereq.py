import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)
_, stdout, _ = c.exec_command("echo '=== DNS CHECK ==='; getent ahosts codm.vipvirtualnet.eu || echo 'DNS not resolved locally'; echo '=== PORTS 80/443 ==='; ss -tulpn | grep -E ':80|:443|:10086'; echo '=== NGINX/CERTBOT STATUS ==='; dpkg -l | grep -E 'nginx|certbot'")
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
