import os
import configparser
from datetime import datetime
global sqldb, cursor, DashboardConfig, WireguardConfigurations, AllPeerJobs, JobLogger, Dash

def get_bind():
    parser = configparser.ConfigParser(strict=False)
    parser.read_file(open('wg-dashboard.ini', "r+"))
    return f"{parser.get('Server', 'app_ip')}:{parser.get('Server', 'app_port')}"

app_host, app_port = get_bind().split(":")
date = datetime.today().strftime('%Y_%m_%d_%H_%M_%S')

def post_worker_init(worker):
    import dashboard
    dashboard.startThreads()
    dashboard.DashboardPlugins.startThreads()

worker_class = 'gthread'
workers = 1
threads = 8
bind = f"{app_host}:{app_port}"
daemon = True
pidfile = './gunicorn.pid'
wsgi_app = "dashboard:app"
accesslog = f"./log/access_{date}.log"
loglevel = os.environ['log_level'] if 'log_level' in os.environ else 'info'
capture_output = True
errorlog = f"./log/error_{date}.log"
pythonpath = "., ./modules"

print(f"[Gunicorn] WGDashboard w/ Gunicorn will be running on {bind}", flush=True)
print(f"[Gunicorn] Access log file is at {accesslog}", flush=True)
print(f"[Gunicorn] Error log file is at {errorlog}", flush=True)
