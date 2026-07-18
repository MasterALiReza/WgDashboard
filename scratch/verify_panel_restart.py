import sys, time
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

time.sleep(2)
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)
_, stdout, _ = c.exec_command("systemctl status wg-dashboard --no-pager -l | head -n 20; curl -I https://codm.vipvirtualnet.eu -m 5")
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
