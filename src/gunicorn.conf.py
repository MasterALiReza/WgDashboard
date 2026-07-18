import os
import configparser
from datetime import datetime
global sqldb, cursor, DashboardConfig, WireguardConfigurations, AllPeerJobs, JobLogger, Dash

base_dir = os.path.dirname(os.path.abspath(__file__))
log_dir = os.path.join(base_dir, 'log')
os.makedirs(log_dir, exist_ok=True)

def get_bind():
    ini_path = os.path.join(base_dir, 'wg-dashboard.ini')
    if not os.path.exists(ini_path):
        from modules.DashboardConfig import DashboardConfig
        DashboardConfig()
    parser = configparser.ConfigParser(strict=False)
    parser.read_file(open(ini_path, "r+"))
    return f"{parser.get('Server', 'app_ip')}:{parser.get('Server', 'app_port')}"

app_host, app_port = get_bind().split(":")
date = datetime.today().strftime('%Y_%m_%d_%H_%M_%S')

def post_worker_init(worker):
    import dashboard
    dashboard.startThreads()
    dashboard.DashboardPlugins.startThreads()

worker_class = 'gthread'
# CRITICAL ARCHITECTURAL NOTE:
# workers MUST remain 1. WGDashboard relies on in-process threading.RLock() and background threads
# (peerInformationBackgroundThread, peerJobScheduleBackgroundThread) inside a single Python process.
# Increasing workers > 1 will spawn duplicate background loops and break lock synchronization across
# processes, causing SQLite 'database is locked' errors and WireGuard configuration corruption.
workers = 1
threads = 8
timeout = 120
keepalive = 5
bind = f"{app_host}:{app_port}"
daemon = True
pidfile = os.path.join(base_dir, 'gunicorn.pid')
wsgi_app = "dashboard:app"
accesslog = os.path.join(log_dir, f"access_{date}.log")
loglevel = os.environ['log_level'] if 'log_level' in os.environ else 'info'
capture_output = True
errorlog = os.path.join(log_dir, f"error_{date}.log")
pythonpath = "., ./modules"

print(f"[Gunicorn] WGDashboard w/ Gunicorn will be running on {bind}", flush=True)
print(f"[Gunicorn] Access log file is at {accesslog}", flush=True)
print(f"[Gunicorn] Error log file is at {errorlog}", flush=True)
