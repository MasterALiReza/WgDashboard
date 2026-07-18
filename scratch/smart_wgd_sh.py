import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)

script = """
cat << 'EOF' > /tmp/wgd_patch.py
with open('/root/WgDashboard/src/wgd.sh', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace start_wgd, stop_wgd, and add systemd awareness
old_funcs = '''start_wgd () {
	_checkWireguard
    gunicorn_start
}

stop_wgd() {
	if test -f "$PID_FILE"; then
		gunicorn_stop
	else
		kill "$(ps aux | grep "[p]ython3 $app_name" | awk '{print $2}')"
	fi
}'''

new_funcs = '''start_wgd () {
	_checkWireguard
	if systemctl list-unit-files wg-dashboard.service >/dev/null 2>&1; then
		printf "[WGDashboard] Managed by systemd. Starting wg-dashboard.service...\\n"
		sudo systemctl start wg-dashboard
		printf "[WGDashboard] WGDashboard service started successfully.\\n"
	else
		gunicorn_start
	fi
}

stop_wgd() {
	if systemctl list-unit-files wg-dashboard.service >/dev/null 2>&1; then
		printf "[WGDashboard] Managed by systemd. Stopping wg-dashboard.service...\\n"
		sudo systemctl stop wg-dashboard
		printf "[WGDashboard] WGDashboard service stopped successfully.\\n"
	elif test -f "$PID_FILE"; then
		gunicorn_stop
	else
		kill "$(ps aux | grep "[p]ython3 $app_name" | awk '{print $2}')"
	fi
}'''

if old_funcs in content:
    content = content.replace(old_funcs, new_funcs)
    with open('/root/WgDashboard/src/wgd.sh', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Successfully patched start_wgd and stop_wgd in wgd.sh")
else:
    print("Could not exact match old_funcs, checking if already patched or updating directly")
EOF
python3 /tmp/wgd_patch.py

# Also let's ensure restart in wgd.sh handles systemctl cleanly if systemd unit exists
sed -i 's/stop_wgd\n\t\t\tstart_wgd/if systemctl list-unit-files wg-dashboard.service >\/dev\/null 2>\&1; then\n\t\t\t\tsudo systemctl restart wg-dashboard\n\t\t\telse\n\t\t\t\tstop_wgd\n\t\t\t\tstart_wgd\n\t\t\tfi/' /root/WgDashboard/src/wgd.sh

echo "=== TESTING ./wgd.sh restart ==="
cd /root/WgDashboard/src && ./wgd.sh restart
"""

_, stdout, _ = c.exec_command(script)
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
