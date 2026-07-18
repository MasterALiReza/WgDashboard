import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)

_, stdout, _ = c.exec_command('find /root/WgDashboard -name "*log*" -mmin -30; find / -name "error_2026*.log" -mmin -30 2>/dev/null')
print("RECENT LOGS FOUND:\n", stdout.read().decode('utf-8', errors='ignore'))

c.close()
