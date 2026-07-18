import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)

_, stdout, _ = c.exec_command('journalctl -u wg-dashboard -n 30 --no-pager')
print("JOURNAL:\n", stdout.read().decode('utf-8', errors='ignore'))

_, stdout, _ = c.exec_command('lsof -i :10086; netstat -tulnp | grep 10086')
print("PORT 10086:\n", stdout.read().decode('utf-8', errors='ignore'))

_, stdout, _ = c.exec_command('ls -la /root/WgDashboard/src/gunicorn.pid; cat /root/WgDashboard/src/gunicorn.pid')
print("PID FILE:\n", stdout.read().decode('utf-8', errors='ignore'))

_, stdout, _ = c.exec_command('tail -n 20 /root/WgDashboard/src/log/error_*.log')
print("ERROR LOGS:\n", stdout.read().decode('utf-8', errors='ignore'))

c.close()
