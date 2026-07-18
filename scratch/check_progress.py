import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)
_, stdout, _ = c.exec_command("ls -lh /etc/wireguard/ /root/migration_tmp/db/ /root/WgDashboard/src/db/ 2>/dev/null; ps aux | grep -E 'python|pip|git|apt|scp'")
print(stdout.read().decode('utf-8'))
c.close()
