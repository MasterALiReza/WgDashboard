import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('94.183.225.232', username='root', password='clgWc4fHtn', timeout=10)
_, stdout, _ = c.exec_command("ls -lh /root/WgDashboard/src/db/ /root/WgDashboard/src/*.ini /root/WgDashboard/src/wgd.sh")
print(stdout.read().decode('utf-8'))
c.close()
