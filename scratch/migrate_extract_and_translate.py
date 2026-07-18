import paramiko
import os
import re

OLD_HOST = '94.183.225.232'
OLD_PWD = 'clgWc4fHtn'
BACKUP_DIR = 'scratch/migration_backup'

os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(f"{BACKUP_DIR}/wireguard", exist_ok=True)
os.makedirs(f"{BACKUP_DIR}/db", exist_ok=True)

print("Connecting to OLD SERVER in Read-Only mode...")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(OLD_HOST, username='root', password=OLD_PWD, timeout=15)
sftp = c.open_sftp()

# 1. Download Wireguard Conf files
print("\n--- Downloading WireGuard Configs ---")
wg_files = sftp.listdir('/etc/wireguard')
for wf in wg_files:
    if wf.endswith('.conf'):
        remote_path = f"/etc/wireguard/{wf}"
        local_path = f"{BACKUP_DIR}/wireguard/{wf}"
        print(f"Downloading {remote_path} -> {local_path} ...")
        sftp.get(remote_path, local_path)

# 2. Download WGDashboard DB and ini files
print("\n--- Downloading WGDashboard DB & INI ---")
db_dir = '/root/WgDashboard/src/db'
for dbf in sftp.listdir(db_dir):
    if dbf.endswith('.db'):
        sftp.get(f"{db_dir}/{dbf}", f"{BACKUP_DIR}/db/{dbf}")
        print(f"Downloaded DB: {dbf}")

sftp.get('/root/WgDashboard/src/wg-dashboard.ini', f"{BACKUP_DIR}/wg-dashboard.ini")
print("Downloaded wg-dashboard.ini")
sftp.get('/root/WgDashboard/src/wgd.sh', f"{BACKUP_DIR}/wgd.sh")
print("Downloaded wgd.sh")

sftp.close()
c.close()
print("\nDownload complete! Now translating network interface names in .conf files...")

# 3. Translate eth0 -> ens160 in .conf files
for wf in os.listdir(f"{BACKUP_DIR}/wireguard"):
    if wf.endswith('.conf'):
        fpath = f"{BACKUP_DIR}/wireguard/{wf}"
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # Replace eth0 with ens160 in PostUp and PostDown
        new_content = re.sub(r'\beth0\b', 'ens160', content)
        
        if content != new_content:
            print(f"Translated eth0 -> ens160 in {wf}")
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(new_content)
        else:
            print(f"No eth0 reference needed change in {wf}")

print("\nPhase 1 & 2 Completed Successfully!")
