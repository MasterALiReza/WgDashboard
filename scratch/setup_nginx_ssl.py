import sys
sys.stdout.reconfigure(encoding='utf-8')
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('85.9.99.250', username='root', password='7K)U-yAu)+hp3B22', timeout=15)

script = """
export DEBIAN_FRONTEND=noninteractive
echo "=== INSTALLING NGINX AND CERTBOT ==="
apt-get update -y
apt-get install -y nginx python3-certbot-nginx

echo "=== CONFIGURING NGINX FOR codm.vipvirtualnet.eu ==="
rm -f /etc/nginx/sites-enabled/default

cat << 'EOF' > /etc/nginx/conf.d/wgdashboard.conf
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name codm.vipvirtualnet.eu _;

    location / {
        proxy_pass http://127.0.0.1:10086;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF

nginx -t && systemctl reload nginx
systemctl enable nginx

echo "=== RUNNING CERTBOT FOR codm.vipvirtualnet.eu ==="
certbot --nginx -d codm.vipvirtualnet.eu --non-interactive --agree-tos --register-unsafely-without-email --redirect || echo "Certbot failed or already exists"

echo "=== FINAL NGINX STATUS & CONFIG ==="
systemctl status nginx --no-pager -l | head -n 15
cat /etc/nginx/conf.d/wgdashboard.conf
"""

_, stdout, _ = c.exec_command(script)
print(stdout.read().decode('utf-8', errors='replace'))
c.close()
