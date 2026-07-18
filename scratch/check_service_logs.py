import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)

_, stdout, _ = c.exec_command('journalctl -u wg-dashboard -n 40 --no-pager')
print("JOURNAL LOGS:\n", stdout.read().decode('utf-8', errors='ignore'))

_, stdout, _ = c.exec_command('cat /root/WgDashboard/src/gunicorn.conf.py')
print("GUNICORN CONF:\n", stdout.read().decode('utf-8', errors='ignore'))

_, stdout, _ = c.exec_command('cat /etc/systemd/system/wg-dashboard.service')
print("SERVICE FILE:\n", stdout.read().decode('utf-8', errors='ignore'))

c.close()
