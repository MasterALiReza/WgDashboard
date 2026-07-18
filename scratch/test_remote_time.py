import os
import sys
import paramiko

ssh_key_path = os.path.expanduser('~/.ssh/id_rsa')
if not os.path.exists(ssh_key_path):
    print(f"Key not found at {ssh_key_path}")
    sys.exit(1)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    client.connect('91.223.61.233', port=22, username='root', key_filename=ssh_key_path)
    
    script = '''
import sys, os, time
sys.path.append(os.path.join(os.getcwd(), 'src'))
from dashboard import app
from src.modules.WireguardConfiguration import WireguardConfiguration

with app.app_context():
    t0 = time.time()
    c = WireguardConfiguration("wg0")
    t1 = time.time()
    print(f"Time to init config: {t1-t0:.4f}s")

    t0 = time.time()
    c.getPeersTransfer()
    t1 = time.time()
    print(f"Time for getPeersTransfer: {t1-t0:.4f}s")
'''

    # Run script on remote server
    stdin, stdout, stderr = client.exec_command('cd ~/WGDashboard && cat << "EOF" > /tmp/timing.py\n' + script + '\nEOF\npython3 /tmp/timing.py')
    print("STDOUT:", stdout.read().decode())
    print("STDERR:", stderr.read().decode())
    
finally:
    client.close()
