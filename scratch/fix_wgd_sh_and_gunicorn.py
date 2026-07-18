import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=10)

script = """
# 1. Update gunicorn.conf.py to include wsgi_app
if ! grep -q 'wsgi_app' /root/WgDashboard/src/gunicorn.conf.py; then
    echo 'wsgi_app = "dashboard:app"' >> /root/WgDashboard/src/gunicorn.conf.py
    echo 'Added wsgi_app to gunicorn.conf.py'
fi

# 2. Update wgd.sh so gunicorn_start includes dashboard:app
sed -i 's/sudo "$venv_gunicorn" --config \.\/gunicorn\.conf\.py$/sudo "$venv_gunicorn" --config \.\/gunicorn\.conf\.py dashboard:app/g' /root/WgDashboard/src/wgd.sh

echo "=== VERIFYING PATCH IN wgd.sh & gunicorn.conf.py ==="
grep 'wsgi_app' /root/WgDashboard/src/gunicorn.conf.py
grep 'dashboard:app' /root/WgDashboard/src/wgd.sh | grep gunicorn
"""

_, stdout, _ = c.exec_command(script)
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
