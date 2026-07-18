import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)

sftp = c.open_sftp()
sftp.put('src/wgd.sh', '/root/WgDashboard/src/wgd.sh')
sftp.close()

_, stdout, _ = c.exec_command('chmod +x /root/WgDashboard/src/wgd.sh && cd /root/WgDashboard/src && ./wgd.sh restart')
out = stdout.read().decode('utf-8', errors='ignore')
print("RESTART OUTPUT:\n", out)

_, stdout, _ = c.exec_command('systemctl status wg-dashboard --no-pager')
status_out = stdout.read().decode('utf-8', errors='ignore')
print("SERVICE STATUS:\n", status_out[:600])

c.close()
