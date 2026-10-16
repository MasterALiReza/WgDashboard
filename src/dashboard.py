import logging, hmac
import random, shutil, sqlite3, configparser, hashlib, ipaddress, json, os, secrets, subprocess
import time, re, uuid, bcrypt, psutil, pyotp, threading, signal
import traceback
from uuid import uuid4
from zipfile import ZipFile
from datetime import datetime, timedelta, timezone

import sqlalchemy
from jinja2.sandbox import SandboxedEnvironment
_jinja_env = SandboxedEnvironment()
from flask import Flask, request, render_template, session, send_file, current_app, Response, g
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_cors import CORS
from icmplib import ping, traceroute
from flask.json.provider import DefaultJSONProvider
from itertools import islice

from sqlalchemy import RowMapping

from modules.Utilities import (
    RegexMatch, StringToBoolean, ValidateDNSAddress,
    GenerateWireguardPublicKey, GenerateWireguardPrivateKey,
    SimpleRateLimiter
)
admin_limiter = SimpleRateLimiter()
_last_used_totp = {"code": None, "timestamp": 0}
_last_used_totp_lock = threading.Lock()
from packaging import version
from modules.Email import EmailSender
from modules.DashboardLogger import DashboardLogger
from modules.PeerJob import PeerJob
from modules.SystemStatus import SystemStatus
from modules.PeerShareLinks import PeerShareLinks
from modules.PeerJobs import PeerJobs, _parse_job_datetime
from modules.DashboardConfig import DashboardConfig
from modules.WireguardConfiguration import WireguardConfiguration
from modules.AmneziaConfiguration import AmneziaConfiguration
from modules.GlobalBackup import GlobalBackupManager, _restore_jobs, _restore_jobs_lock, _cleanup_expired_jobs, _backup_jobs, _backup_jobs_lock, _is_backup_running_lock

try:
    import qrcode
    import io
    import base64
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

_idempotency_cache = {}
_idempotency_lock = threading.Lock()
_IDEMPOTENCY_TTL_SECONDS = 3600

def _get_idempotent_response(key: str):
    if not key:
        return None
    with _idempotency_lock:
        entry = _idempotency_cache.get(key)
        if entry:
            res_data, timestamp = entry
            if time.time() - timestamp < _IDEMPOTENCY_TTL_SECONDS:
                return res_data
            else:
                del _idempotency_cache[key]
    return None

def _store_idempotent_response(key: str, data: dict):
    if not key:
        return
    with _idempotency_lock:
        now = time.time()
        if len(_idempotency_cache) > 1000:
            expired = [k for k, v in _idempotency_cache.items() if now - v[1] >= _IDEMPOTENCY_TTL_SECONDS]
            for k in expired:
                del _idempotency_cache[k]
        _idempotency_cache[key] = (data, now)

from client import createClientBlueprint

from logging.config import dictConfig

from modules.DashboardClients import DashboardClients
from modules.DashboardPlugins import DashboardPlugins
from modules.DashboardWebHooks import DashboardWebHooks
from modules.NewConfigurationTemplates import NewConfigurationTemplates

class CustomJsonEncoder(DefaultJSONProvider):
    def __init__(self, app):
        super().__init__(app)

    def default(self, o):
        if callable(getattr(o, "toJson", None)):
            return o.toJson()
        if type(o) is RowMapping:
            return dict(o)
        if type(o) is datetime:
            return o.strftime("%Y-%m-%d %H:%M:%S")
        return super().default(o)



'''
Response Object
'''
def ResponseObject(status=True, message=None, data=None, status_code = 200) -> Flask.response_class:
    response = Flask.make_response(app, {
        "status": status,
        "message": message,
        "data": data
    })
    response.status_code = status_code
    response.content_type = "application/json"
    return response

'''
Flask App
'''
_, APP_PREFIX_INIT = DashboardConfig().GetConfig("Server", "app_prefix")
app = Flask("WGDashboard",
            template_folder=os.path.abspath("./static/dist/WGDashboardAdmin"),
            static_folder=os.path.abspath("./static/dist/WGDashboardAdmin"),
            static_url_path=APP_PREFIX_INIT if APP_PREFIX_INIT else '')

@app.errorhandler(TimeoutError)
def handle_lock_timeout_error(e):
    """
    PERF-11: Return 503 with retry message when a ProcessLock times out under heavy concurrency,
    allowing clients / OxBot to backoff and retry gracefully without experiencing a 500 crash.
    """
    app.logger.warning(f"Lock timeout occurred: {e}")
    return ResponseObject(
        status=False,
        message="Server is busy processing another request. Please retry shortly.",
        status_code=503
    )

@app.errorhandler(400)
def handle_bad_request(e):
    if request.path.startswith('/api/') or (APP_PREFIX and request.path.startswith(f"{APP_PREFIX}/api/")):
        msg = e.description if hasattr(e, 'description') and e.description else "Bad Request"
        return ResponseObject(False, f"Bad Request: {msg}", status_code=400)
    return e

@app.errorhandler(404)
def handle_not_found(e):
    if request.path.startswith('/api/') or (APP_PREFIX and request.path.startswith(f"{APP_PREFIX}/api/")):
        return ResponseObject(False, "API resource not found", status_code=404)
    return e

@app.errorhandler(405)
def handle_method_not_allowed(e):
    if request.path.startswith('/api/') or (APP_PREFIX and request.path.startswith(f"{APP_PREFIX}/api/")):
        return ResponseObject(False, "Method Not Allowed", status_code=405)
    return e

@app.errorhandler(500)
def handle_internal_server_error(e):
    if request.path.startswith('/api/') or (APP_PREFIX and request.path.startswith(f"{APP_PREFIX}/api/")):
        app.logger.error(f"Internal Server Error on {request.path}: {e}")
        return ResponseObject(False, "Internal server error occurred. Please check server logs.", status_code=500)
    return e

def peerInformationBackgroundThread():
    global WireguardConfigurations
    app.logger.info("Background Thread #1 Started")
    app.logger.info("Background Thread #1 PID:" + str(threading.get_native_id()))
    delay = 6
    reconcile_patrol_counter = 0
    while True:
        if app.config.get('MAINTENANCE_MODE', False):
            time.sleep(2)
            continue
        with app.app_context():
            try:
                reconcile_patrol_counter += 1
                should_reconcile = (reconcile_patrol_counter >= 30)
                if should_reconcile:
                    reconcile_patrol_counter = 0

                with WireguardConfigurationsLock:
                    curKeys = list(WireguardConfigurations.keys())
                for name in curKeys:
                    with WireguardConfigurationsLock:
                        c = WireguardConfigurations.get(name)
                    if c is not None:
                        if c.getStatus():
                            if should_reconcile:
                                try:
                                    rec_res = c.reconcileWithKernel()
                                    if rec_res.get("repaired_peers") or rec_res.get("restricted_removed"):
                                        app.logger.warning(
                                            f"[Self-Healing] Reconciled {name}: repaired {len(rec_res.get('repaired_peers', []))} peers, "
                                            f"removed {len(rec_res.get('restricted_removed', []))} restricted peers."
                                        )
                                except Exception as rec_err:
                                    app.logger.error(f"[Self-Healing] Error reconciling {name}: {rec_err}")
                            c.refreshPeersRuntimeStats()
                            c.getPeers()
                            if DashboardConfig.GetConfig('WireGuardConfiguration', 'peer_tracking')[1] is True:
                                app.logger.debug("[WGDashboard] Tracking Peers")
                                if delay == 6:
                                    if c.configurationInfo.PeerTrafficTracking:
                                        c.logPeersTraffic()
                                    if c.configurationInfo.PeerHistoricalEndpointTracking:
                                        c.logPeersHistoryEndpoint()
                            c.getRestrictedPeersList()
            except Exception as e:
                app.logger.error(f"[WGDashboard] Background Thread #1 Error: {e}")

        if delay == 6:
            delay = 1
        else:
            delay += 1
        time.sleep(10)

def peerJobScheduleBackgroundThread():
    with app.app_context():
        app.logger.info(f"Background Thread #2 Started")
        app.logger.info(f"Background Thread #2 PID:" + str(threading.get_native_id()))
        time.sleep(10)
        while True:
            if app.config.get('MAINTENANCE_MODE', False):
                time.sleep(2)
                continue
            try:
                AllPeerJobs.runJob()
            except Exception as e:
                app.logger.error(f"Background Thread #2 Error: {e}")
            time.sleep(180)

def autoPruneDatabaseLogs():
    """
    PERF-04 / PERF-09: Periodically trims bloated log tables (DashboardLog, JobLog),
    expired PeerJobs, transfer tracking history (>30 days), and checkpoints SQLite databases.
    Prevents SQLite databases from growing indefinitely and avoids performance bottlenecks.
    """
    try:
        from sqlalchemy import text, inspect
        import glob
        from modules.DatabaseConnection import ConnectionString, get_engine
        
        # 1. Prune SQL Log database
        log_engine = get_engine("wgdashboard_log")
        try:
            with log_engine.connect() as conn:
                # Keep latest 5,000 DashboardLog rows
                conn.execute(text("""
                    DELETE FROM "DashboardLog" 
                    WHERE rowid NOT IN (
                        SELECT rowid FROM "DashboardLog" ORDER BY rowid DESC LIMIT 5000
                    )
                """))
                # Keep latest 1,000 JobLog rows
                conn.execute(text("""
                    DELETE FROM "JobLog" 
                    WHERE rowid NOT IN (
                        SELECT rowid FROM "JobLog" ORDER BY rowid DESC LIMIT 1000
                    )
                """))
                conn.commit()
                
                if log_engine.dialect.name == "sqlite":
                    conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                    conn.commit()
        except Exception as e:
            app.logger.warning(f"[WGDashboard] Error pruning log tables: {e}")

        # 2. Prune expired PeerJobs (older than 14 days) and checkpoint wgdashboard_job
        job_engine = get_engine("wgdashboard_job")
        try:
            cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
            with job_engine.connect() as conn:
                conn.execute(
                    text('DELETE FROM "PeerJobs" WHERE "ExpireDate" IS NOT NULL AND "ExpireDate" < :cutoff'),
                    {"cutoff": cutoff}
                )
                conn.commit()
                if job_engine.dialect.name == "sqlite":
                    conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                    conn.commit()
        except Exception as e:
            app.logger.warning(f"[WGDashboard] Error pruning job tables: {e}")

        # 3. Prune peer tracking transfer history (keep 30 days) and checkpoint main wgdashboard.db (PERF-09)
        main_engine = get_engine("wgdashboard")
        try:
            cutoff_transfer = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            inspector = inspect(main_engine)
            table_names = inspector.get_table_names()
            with main_engine.connect() as conn:
                for table_name in table_names:
                    if table_name.endswith("_transfer"):
                        try:
                            conn.execute(
                                text(f'DELETE FROM "{table_name}" WHERE "time" IS NOT NULL AND "time" < :cutoff'),
                                {"cutoff": cutoff_transfer}
                            )
                        except Exception:
                            pass
                conn.commit()
                if main_engine.dialect.name == "sqlite":
                    conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                    conn.commit()
        except Exception as e:
            app.logger.warning(f"[WGDashboard] Error pruning transfer tables: {e}")

        # 4. Prune old text log files (keep 7 days, preserve active log files)
        log_dir = os.path.abspath("./log")
        if os.path.exists(log_dir):
            cutoff = time.time() - (7 * 86400)
            today_str = datetime.today().strftime('%Y_%m_%d')
            for f in glob.glob(os.path.join(log_dir, "*.log")):
                try:
                    # Never delete today's active logs or files modified within 7 days
                    if today_str not in os.path.basename(f) and os.path.getmtime(f) < cutoff:
                        os.remove(f)
                except Exception:
                    pass
        app.logger.info("[WGDashboard] Completed scheduled database and log pruning.")
    except Exception as e:
        app.logger.warning(f"[WGDashboard] Auto log pruning warning: {e}")

def globalBackupBackgroundThread():
    with app.app_context():
        app.logger.info("Background Thread #3 Started (Global Backup & Maintenance)")
        app.logger.info("Background Thread #3 PID:" + str(threading.get_native_id()))
        
        # Initialize last_backup_time from existing backups if possible
        last_backup_time = datetime.fromtimestamp(0)
        last_prune_time = datetime.now() - timedelta(hours=5, minutes=50) # Run first prune ~10 mins after boot
        backups = GlobalBackupManager.list_global_backups()
        for b in backups:
            if b['label'].startswith("Auto_"):
                try:
                    last_backup_time = datetime.fromisoformat(b['created'])
                except:
                    pass
                break # Since backups are sorted newest first, this is the latest Auto backup

        while True:
            if app.config.get('MAINTENANCE_MODE', False):
                time.sleep(2)
                continue
            try:
                now = datetime.now()

                # Periodic Database & Log Pruning (Every 6 hours)
                if (now - last_prune_time).total_seconds() >= 6 * 3600:
                    autoPruneDatabaseLogs()
                    last_prune_time = now

                auto_backup_status, auto_backup = DashboardConfig.GetConfig("GlobalBackup", "auto_backup")
                if auto_backup_status and auto_backup:
                    schedule_status, schedule = DashboardConfig.GetConfig("GlobalBackup", "auto_backup_schedule")
                    if schedule_status and schedule:
                        time_diff = now - last_backup_time
                        should_backup = False
                        
                        if schedule == "3h" and time_diff.total_seconds() >= 3 * 3600:
                            should_backup = True
                        elif schedule == "6h" and time_diff.total_seconds() >= 6 * 3600:
                            should_backup = True
                        elif schedule == "12h" and time_diff.total_seconds() >= 12 * 3600:
                            should_backup = True
                        elif schedule == "daily" and time_diff.total_seconds() >= 24 * 3600:
                            should_backup = True
                        elif schedule == "weekly" and time_diff.total_seconds() >= 7 * 24 * 3600:
                            should_backup = True
                        elif schedule == "monthly" and time_diff.total_seconds() >= 30 * 24 * 3600:
                            should_backup = True
                            
                        if should_backup:
                            if app.config.get('MAINTENANCE_MODE', False):
                                app.logger.warning("Skipping scheduled backup because system is in maintenance mode.")
                                time.sleep(60)
                                continue
                            
                            import modules.GlobalBackup
                            can_run = False
                            with _is_backup_running_lock:
                                if not modules.GlobalBackup._is_backup_running:
                                    modules.GlobalBackup._is_backup_running = True
                                    can_run = True
                            
                            if can_run:
                                try:
                                    app.logger.info(f"Starting scheduled global backup ({schedule})")
                                    with WireguardConfigurationsLock:
                                        success, result = GlobalBackupManager.create_global_backup(label=f"Auto_{schedule}")
                                    if success:
                                        last_backup_time = now
                                        app.logger.info(f"Scheduled global backup completed successfully: {result.get('filename') if isinstance(result, dict) else result}")
                                    else:
                                        # GB-10: On failure, set backoff to prevent 60s busy loop
                                        last_backup_time = now - timedelta(hours=1)
                                        app.logger.error(f"Scheduled global backup failed: {result}")
                                finally:
                                    with _is_backup_running_lock:
                                        modules.GlobalBackup._is_backup_running = False
            except Exception as e:
                app.logger.error(f"Background Thread #3 Error: {e}")
            time.sleep(60)

def gunicornConfig():
    _, app_ip = DashboardConfig.GetConfig("Server", "app_ip")
    _, app_port = DashboardConfig.GetConfig("Server", "app_port")
    return app_ip, app_port

def ProtocolsEnabled() -> list[str]:
    from shutil import which
    protocols = []
    if which('awg') is not None and which('awg-quick') is not None:
        protocols.append("awg")
    if which('wg') is not None and which('wg-quick') is not None:
        protocols.append("wg")
    return protocols

def InitWireguardConfigurationsList(startup: bool = False):
    with WireguardConfigurationsLock:
        active_wg_confs = set()
        wg_path = DashboardConfig.GetConfig("Server", "wg_conf_path")[1]
        if os.path.exists(wg_path):
            confs = os.listdir(wg_path)
            confs.sort()
            for i in confs:
                if RegexMatch("^(.{1,}).(conf)$", i):
                    i = i.replace('.conf', '')
                    active_wg_confs.add(i)
                    try:
                        if i in WireguardConfigurations.keys():
                            if WireguardConfigurations[i].configurationFileChanged():
                                with app.app_context():
                                    WireguardConfigurations[i].reloadConfiguration()
                        else:
                            with app.app_context():
                                WireguardConfigurations[i] = WireguardConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, i, startup=startup)
                    except WireguardConfiguration.InvalidConfigurationFileException as e:
                        app.logger.error(f"{i} have an invalid configuration file.")

        active_awg_confs = set()
        if "awg" in ProtocolsEnabled():
            awg_path = DashboardConfig.GetConfig("Server", "awg_conf_path")[1]
            if awg_path and os.path.exists(awg_path):
                confs = os.listdir(awg_path)
                confs.sort()
                for i in confs:
                    if RegexMatch("^(.{1,}).(conf)$", i):
                        i = i.replace('.conf', '')
                        active_awg_confs.add(i)
                        try:
                            if i in WireguardConfigurations.keys():
                                if WireguardConfigurations[i].configurationFileChanged():
                                    with app.app_context():
                                        WireguardConfigurations[i].reloadConfiguration()
                            else:
                                with app.app_context():
                                    WireguardConfigurations[i] = AmneziaConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, i, startup=startup)
                        except WireguardConfiguration.InvalidConfigurationFileException as e:
                            app.logger.error(f"{i} have an invalid configuration file.")

        # PERF-03: Prune configurations removed from disk
        all_active_confs = active_wg_confs | active_awg_confs
        for conf_name in list(WireguardConfigurations.keys()):
            if conf_name not in all_active_confs:
                app.logger.info(f"Removing deleted configuration from memory: {conf_name}")
                del WireguardConfigurations[conf_name]

        if startup:
            for conf_name, conf_obj in list(WireguardConfigurations.items()):
                try:
                    if conf_obj.getStatus():
                        app.logger.info(f"Startup: Running reconcileWithKernel for active interface {conf_name}...")
                        conf_obj.reconcileWithKernel()
                except Exception as e:
                    app.logger.error(f"Startup reconcile failed for {conf_name}: {e}")

def startThreads():
    bgThread = threading.Thread(target=peerInformationBackgroundThread, daemon=True)
    bgThread.start()
    scheduleJobThread = threading.Thread(target=peerJobScheduleBackgroundThread, daemon=True)
    scheduleJobThread.start()
    backupThread = threading.Thread(target=globalBackupBackgroundThread, daemon=True)
    backupThread.start()

dictConfig({
    'version': 1,
    'formatters': {'default': {
        'format': '[%(asctime)s] [%(levelname)s] in [%(module)s] %(message)s',
    }},
    'root': {
        'level': 'INFO'
    }
})


WireguardConfigurations: dict[str, WireguardConfiguration] = {}
WireguardConfigurationsLock = threading.RLock()
CONFIGURATION_PATH = os.getenv('CONFIGURATION_PATH', '.')

app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 5206928
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12)
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.json = CustomJsonEncoder(app)
with app.app_context():
    SystemStatus = SystemStatus()
    DashboardConfig = DashboardConfig()
    app.secret_key = DashboardConfig.GetConfig("Server", "flask_secret_key")[1]
    EmailSender = EmailSender(DashboardConfig)
    AllPeerShareLinks: PeerShareLinks = PeerShareLinks(DashboardConfig, WireguardConfigurations)
    AllPeerJobs: PeerJobs = PeerJobs(DashboardConfig, WireguardConfigurations, AllPeerShareLinks)
    DashboardLogger: DashboardLogger = DashboardLogger()
    DashboardPlugins: DashboardPlugins = DashboardPlugins(app, WireguardConfigurations)
    DashboardWebHooks: DashboardWebHooks = DashboardWebHooks(DashboardConfig)
    NewConfigurationTemplates: NewConfigurationTemplates = NewConfigurationTemplates()
    InitWireguardConfigurationsList(startup=True)
    DashboardClients: DashboardClients = DashboardClients(WireguardConfigurations)
    app.register_blueprint(createClientBlueprint(WireguardConfigurations, DashboardConfig, DashboardClients))

_, APP_PREFIX = DashboardConfig.GetConfig("Server", "app_prefix")
_, app_ip = DashboardConfig.GetConfig("Server", "app_ip")
_, app_port = DashboardConfig.GetConfig("Server", "app_port")
_, WG_CONF_PATH = DashboardConfig.GetConfig("Server", "wg_conf_path")

cors_origins_setting = DashboardConfig.GetConfig("Server", "cors_origins")[1] if DashboardConfig.GetConfig("Server", "cors_origins")[0] else None
if cors_origins_setting and cors_origins_setting.strip() == "*":
    cors_allowed = "*"
elif cors_origins_setting and len(cors_origins_setting.strip()) > 0:
    cors_allowed = [o.strip() for o in cors_origins_setting.split(",") if o.strip()]
else:
    cors_allowed = [
        f"http://{app_ip}:{app_port}", f"https://{app_ip}:{app_port}",
        f"http://127.0.0.1:{app_port}", f"https://127.0.0.1:{app_port}",
        f"http://localhost:{app_port}", f"https://localhost:{app_port}"
    ]

cors = CORS(app, resources={rf"{APP_PREFIX}/api/*": {
    "origins": cors_allowed,
    "methods": "DELETE, POST, GET, OPTIONS",
    "allow_headers": ["Content-Type", "wg-dashboard-apikey"]
}})

BOT_ALLOWED_ENDPOINTS = {
    f"{APP_PREFIX}/api/getWireguardConfigurations",
    f"{APP_PREFIX}/api/getWireguardConfigurationInfo",
    f"{APP_PREFIX}/api/getAvailableIPs",
    f"{APP_PREFIX}/api/downloadPeer",
    f"{APP_PREFIX}/api/addPeers",
    f"{APP_PREFIX}/api/updatePeerSettings",
    f"{APP_PREFIX}/api/deletePeers",
    f"{APP_PREFIX}/api/restrictPeers",
    f"{APP_PREFIX}/api/allowAccessPeers",
    f"{APP_PREFIX}/api/resetPeerData",
    f"{APP_PREFIX}/api/savePeerScheduleJob",
    f"{APP_PREFIX}/api/deletePeerScheduleJob",
    f"{APP_PREFIX}/api/handshake",
    # API v2 endpoints
    f"{APP_PREFIX}/api/v2/peer",
    f"{APP_PREFIX}/api/v2/peers/usage",
    f"{APP_PREFIX}/api/v2/peers",
    f"{APP_PREFIX}/api/v2/peer/renew",
    f"{APP_PREFIX}/api/v2/peer/limits",
    f"{APP_PREFIX}/api/v2/peer/limits/add",
    f"{APP_PREFIX}/api/v2/peers/bulk",
}

def is_bot_endpoint_allowed(path: str) -> bool:
    clean_path = path.rstrip("/")
    for ep in BOT_ALLOWED_ENDPOINTS:
        clean_ep = ep.rstrip("/")
        if clean_path == clean_ep or clean_path.startswith(clean_ep + "/"):
            return True
    return False

def extract_target_configuration() -> str | None:
    parts = request.path.rstrip('/').split('/')
    if len(parts) > 0 and parts[-1] in WireguardConfigurations:
        return parts[-1]
    for p in ('configurationName', 'configuration', 'configName'):
        val = request.args.get(p)
        if val:
            return val
    if request.is_json:
        try:
            body = request.get_json(silent=True)
            if isinstance(body, dict):
                for p in ('configurationName', 'configuration', 'configName', 'Configuration'):
                    val = body.get(p)
                    if val:
                        return str(val)
        except Exception:
            pass
    return None

'''
API Routes
'''

@app.before_request
def auth_req():
    if request.method.lower() == 'options':
        return ResponseObject(True)        

    _maintenance_whitelist = (
        '/api/globalBackup/restore',
        '/api/globalBackup/restore/progress',
        '/api/globalBackup/restore/progress/poll',
        '/api/globalBackup/create/progress/poll'
    )
    if app.config.get('MAINTENANCE_MODE', False) and not any(request.path.endswith(p) for p in _maintenance_whitelist):
        if request.path.startswith('/api/'):
            return ResponseObject(False, "System is currently under maintenance due to restore process", status_code=503)

    g.api_accessed = False
    authenticationRequired = DashboardConfig.GetConfig("Server", "auth_req")[1]
    d = request.headers
    if authenticationRequired:
        apiKey = d.get('wg-dashboard-apikey')
        apiKeyEnabled = DashboardConfig.GetConfig("Server", "dashboard_api_key")[1]
        if apiKey is not None and len(apiKey) > 0 and apiKeyEnabled:
            now = datetime.now()
            matching_keys = [
                x for x in DashboardConfig.DashboardAPIKeys
                if hmac.compare_digest(x.Key, apiKey)
            ]
            valid_keys = []
            for k in matching_keys:
                if k.ExpiredAt is None or k.ExpiredAt == "" or k.ExpiredAt == "None":
                    valid_keys.append(k)
                else:
                    try:
                        exp_dt = datetime.strptime(k.ExpiredAt, "%Y-%m-%d %H:%M:%S")
                        if exp_dt > now:
                            valid_keys.append(k)
                    except Exception:
                        pass

            masked_key = apiKey[:4] + "****" + apiKey[-4:] if len(apiKey) > 8 else "****"
            if len(valid_keys) != 1:
                DashboardLogger.log(str(request.url), str(request.remote_addr), Status="false", Message=f"Unauthorized API Key attempt: {masked_key}")
                g.api_accessed = False
                response = Flask.make_response(app, {
                    "status": False,
                    "message": "API Key does not exist or has expired",
                    "data": None
                })
                response.content_type = "application/json"
                response.status_code = 401
                return response

            current_key = valid_keys[0]
            g.api_key = current_key

            # Enforce bot allowlist (SEC-03)
            if not is_bot_endpoint_allowed(request.path):
                DashboardLogger.log(str(request.url), str(request.remote_addr), Status="false", Message=f"API Key endpoint not allowed: {request.path}")
                g.api_accessed = False
                return ResponseObject(False, "API key not authorized for this endpoint", status_code=403)

            # Scope permission check (FEAT-05)
            if current_key.Scope == "read" and request.method.upper() not in ('GET', 'HEAD', 'OPTIONS'):
                DashboardLogger.log(str(request.url), str(request.remote_addr), Status="false", Message=f"API Key scope violation: write attempted with read-only scope")
                g.api_accessed = False
                return ResponseObject(False, "API key has read-only scope", status_code=403)

            # Configuration permission check (BOT-14)
            target_config = extract_target_configuration()
            if target_config and not current_key.is_configuration_allowed(target_config):
                DashboardLogger.log(str(request.url), str(request.remote_addr), Status="false", Message=f"API Key config scope violation: attempted {target_config}")
                g.api_accessed = False
                return ResponseObject(False, f"API key not authorized for configuration '{target_config}'", status_code=403)

            # For valid API keys: only log mutating actions (POST/PUT/DELETE/PATCH),
            # preventing millions of noisy log rows from continuous background GET polling.
            if request.method.upper() not in ('GET', 'HEAD', 'OPTIONS'):
                DashboardLogger.log(str(request.url), str(request.remote_addr), Status="true", Message=f"API Action: {request.method} {request.path} - Key: {masked_key}")
            g.api_accessed = True
        else:
            g.api_accessed = False
            appPrefix = APP_PREFIX if len(APP_PREFIX) > 0 else ''
            whiteList = [
                # f'/static/', 
                f'{appPrefix}/api/validateAuthentication', 
                f'{appPrefix}/api/authenticate', 
                # f'{appPrefix}/api/getDashboardConfiguration',
                f'{appPrefix}/api/getDashboardTheme', 
                f'{appPrefix}/api/getDashboardVersion', 
                f'{appPrefix}/api/sharePeer/get', 
                f'{appPrefix}/api/isTotpEnabled', 
                f'{appPrefix}/api/locale',
                f'{appPrefix}/api/requireAuthentication',
                f'{appPrefix}/api/handshake',
            ]

            is_public_path = (
                (f"{appPrefix}/" == request.path or f"{appPrefix}" == request.path)
                or request.path.startswith(f'{appPrefix}/client')
                or request.path.startswith(f'{appPrefix}/img')
                or request.path.startswith(f'{appPrefix}/json')
                or request.path.startswith(f'{appPrefix}/assets')
                or request.path in whiteList
            )

            is_admin = False
            if "username" in session and session.get("role") == "admin":
                current_epoch = str(DashboardConfig.GetConfig("Account", "session_epoch")[1] or "1")
                cookie_token = request.cookies.get("authToken")
                if session.get("session_epoch") == current_epoch and cookie_token and cookie_token == session.get("username"):
                    is_admin = True
                else:
                    session.clear()

            if not is_admin and not is_public_path:
                response = Flask.make_response(app, {
                    "status": False,
                    "message": "Unauthorized access.",
                    "data": None
                })
                response.content_type = "application/json"
                response.status_code = 401
                response.delete_cookie("authToken")
                return response

@app.route(f'{APP_PREFIX}/api/handshake', methods=["GET", "OPTIONS"])
def API_Handshake():
    return ResponseObject(True)

@app.get(f'{APP_PREFIX}/api/validateAuthentication')
def API_ValidateAuthentication():
    token = request.cookies.get("authToken")
    if DashboardConfig.GetConfig("Server", "auth_req")[1]:
        current_epoch = str(DashboardConfig.GetConfig("Account", "session_epoch")[1] or "1")
        if token is None or token == "" or "username" not in session or session["username"] != token or session.get("session_epoch") != current_epoch:
            return ResponseObject(False, "Invalid authentication.")
    return ResponseObject(True)

@app.get(f'{APP_PREFIX}/api/requireAuthentication')
def API_RequireAuthentication():
    return ResponseObject(data=DashboardConfig.GetConfig("Server", "auth_req")[1])

@app.post(f'{APP_PREFIX}/api/authenticate')
def API_AuthenticateLogin():
    ip = request.remote_addr
    if admin_limiter.is_rate_limited(f"login_{ip}", limit=5, period=60):
        DashboardLogger.log(str(request.url), str(request.remote_addr), Message=f"Login rate limited for IP: {ip}")
        return ResponseObject(False, "Too many login attempts. Please try again in a minute.", status_code=429)

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    if not DashboardConfig.GetConfig("Server", "auth_req")[1]:
        return ResponseObject(True, DashboardConfig.GetConfig("Other", "welcome_session")[1])

    valid = False
    try:
        valid = bcrypt.checkpw(str(data.get('password', '')).encode("utf-8"),
                               DashboardConfig.GetConfig("Account", "password")[1].encode("utf-8"))
    except Exception:
        valid = False

    totpEnabled = DashboardConfig.GetConfig("Account", "enable_totp")[1]
    totpValid = False
    if totpEnabled:
        totp_code = str(data.get('totp') or '').strip()
        totp_key = DashboardConfig.GetConfig("Account", "totp_key")[1]
        with _last_used_totp_lock:
            if totp_code and totp_code == _last_used_totp["code"] and (time.time() - _last_used_totp["timestamp"] < 60):
                totpValid = False
            else:
                try:
                    totpValid = pyotp.TOTP(totp_key).verify(totp_code, valid_window=1)
                    if totpValid:
                        _last_used_totp["code"] = totp_code
                        _last_used_totp["timestamp"] = time.time()
                except Exception:
                    totpValid = False

    if (valid
            and data.get('username') == DashboardConfig.GetConfig("Account", "username")[1]
            and ((totpEnabled and totpValid) or not totpEnabled)
    ):
        admin_limiter.reset(f"login_{ip}")
        authToken = secrets.token_hex(32)
        session['role'] = 'admin'
        session['username'] = authToken
        session['session_epoch'] = str(DashboardConfig.GetConfig("Account", "session_epoch")[1] or "1")
        session.permanent = True
        resp = ResponseObject(True, DashboardConfig.GetConfig("Other", "welcome_session")[1])
        resp.set_cookie("authToken", authToken, httponly=True, samesite="Lax")
        DashboardLogger.log(str(request.url), str(request.remote_addr), Message=f"Login success: {data.get('username')}")
        return resp

    admin_limiter.hit(f"login_{ip}", period=60)
    DashboardLogger.log(str(request.url), str(request.remote_addr), Message=f"Login failed: {data.get('username')}")
    if totpEnabled:
        return ResponseObject(False, "Sorry, your username, password or OTP is incorrect.")
    else:
        return ResponseObject(False, "Sorry, your username or password is incorrect.")

@app.route(f'{APP_PREFIX}/api/signout', methods=["GET", "POST"])
def API_SignOut():
    if request.args.get("all") in ("true", "1"):
        DashboardConfig.bump_session_epoch()
    resp = ResponseObject(True, "")
    resp.delete_cookie("authToken")
    session.clear()
    return resp

@app.get(f'{APP_PREFIX}/api/getWireguardConfigurations')
def API_getWireguardConfigurations():
    InitWireguardConfigurationsList()
    return ResponseObject(data=[wc for wc in WireguardConfigurations.values()])

@app.get(f'{APP_PREFIX}/api/newConfigurationTemplates')
def API_NewConfigurationTemplates():
    return ResponseObject(data=NewConfigurationTemplates.GetTemplates())

@app.get(f'{APP_PREFIX}/api/newConfigurationTemplates/createTemplate')
def API_NewConfigurationTemplates_CreateTemplate():
    return ResponseObject(data=NewConfigurationTemplates.CreateTemplate().model_dump())

@app.post(f'{APP_PREFIX}/api/newConfigurationTemplates/updateTemplate')
def API_NewConfigurationTemplates_UpdateTemplate():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    template = data.get('Template', None)
    if not template:
        return ResponseObject(False, "Please provide template")
    
    status, msg = NewConfigurationTemplates.UpdateTemplate(template)
    return ResponseObject(status, msg)

@app.post(f'{APP_PREFIX}/api/newConfigurationTemplates/deleteTemplate')
def API_NewConfigurationTemplates_DeleteTemplate():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    template = data.get('Template', None)
    if not template:
        return ResponseObject(False, "Please provide template")

    status, msg = NewConfigurationTemplates.DeleteTemplate(template)
    return ResponseObject(status, msg)

@app.post(f'{APP_PREFIX}/api/addWireguardConfiguration')
def API_addWireguardConfiguration():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    requiredKeys = [
        "ConfigurationName", "Address", "ListenPort", "PrivateKey", "Protocol"
    ]
    for i in requiredKeys:
        if i not in data.keys():
            return ResponseObject(False, "Please provide all required parameters.", status_code=400)
    
    if data.get("Protocol") not in ProtocolsEnabled():
        return ResponseObject(False, "Please provide a valid protocol: wg / awg.")

    config_name = str(data.get("ConfigurationName", "")).strip()
    if not re.match(r"^[a-zA-Z0-9_=\+\.\-]{1,15}$", config_name):
        return ResponseObject(False, "Invalid configuration name. Must be 1-15 characters (letters, numbers, _, =, +, ., -).", "ConfigurationName")

    # Check duplicate names, ports, address
    for i in WireguardConfigurations.values():
        if i.Name == config_name:
            return ResponseObject(False,
                                  f"Already have a configuration with the name \"{config_name}\"",
                                  "ConfigurationName")

        if str(i.ListenPort) == str(data["ListenPort"]):
            return ResponseObject(False,
                                  f"Already have a configuration with the port \"{data['ListenPort']}\"",
                                  "ListenPort")

        if i.Address == data["Address"]:
            return ResponseObject(False,
                                  f"Already have a configuration with the address \"{data['Address']}\"",
                                  "Address")

    if "Backup" in data.keys() and data["Backup"]:
        backup_name = os.path.basename(str(data["Backup"]))
        path = {
            "wg": DashboardConfig.GetConfig("Server", "wg_conf_path")[1],
            "awg": DashboardConfig.GetConfig("Server", "awg_conf_path")[1]
        }
     
        if (os.path.exists(os.path.join(path['wg'], 'WGDashboard_Backup', backup_name)) and
                os.path.exists(os.path.join(path['wg'], 'WGDashboard_Backup', backup_name.replace('.conf', '.sql')))):
            protocol = "wg"
        elif (os.path.exists(os.path.join(path['awg'], 'WGDashboard_Backup', backup_name)) and
              os.path.exists(os.path.join(path['awg'], 'WGDashboard_Backup', backup_name.replace('.conf', '.sql')))):
            protocol = "awg"
        else:
            return ResponseObject(False, "Backup does not exist")
        
        shutil.copy(
            os.path.join(path[protocol], 'WGDashboard_Backup', backup_name),
            os.path.join(path[protocol], f'{config_name}.conf')
        )
        WireguardConfigurations[config_name] = (
            WireguardConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, data=data, name=config_name)) if protocol == 'wg' else (
            AmneziaConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, data=data, name=config_name))
    else:
        WireguardConfigurations[config_name] = (
            WireguardConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, data=data)) if data.get('Protocol') == 'wg' else (
            AmneziaConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, data=data))
    return ResponseObject()

@app.route(f'{APP_PREFIX}/api/toggleWireguardConfiguration', methods=["GET", "POST"])
def API_toggleWireguardConfiguration():
    data = request.get_json(silent=True) or {}
    configurationName = request.args.get('configurationName') or data.get('configurationName')
    if configurationName is None or len(
            configurationName) == 0 or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide a valid configuration name", status_code=404)
    toggleStatus, msg = WireguardConfigurations[configurationName].toggleConfiguration()
    return ResponseObject(toggleStatus, msg, WireguardConfigurations[configurationName].Status)

@app.post(f'{APP_PREFIX}/api/updateWireguardConfiguration')
def API_updateWireguardConfiguration():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    requiredKeys = ["Name"]
    for i in requiredKeys:
        if i not in data.keys():
            return ResponseObject(False, "Please provide these following field: " + ", ".join(requiredKeys), status_code=400)
    name = data.get("Name")
    if name not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    
    status, msg = WireguardConfigurations[name].updateConfigurationSettings(data)
    
    return ResponseObject(status, message=msg, data=WireguardConfigurations[name])

@app.post(f'{APP_PREFIX}/api/updateWireguardConfigurationInfo')
def API_updateWireguardConfigurationInfo():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    name = data.get('Name')
    key = data.get('Key')
    value = data.get('Value')
    if not all([data, key, name]):
        return ResponseObject(status=False, message="Please provide configuration name, key and value", status_code=400)
    if name not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    
    status, msg, key = WireguardConfigurations[name].updateConfigurationInfo(key, value)
    
    return ResponseObject(status=status, message=msg, data=key)

@app.get(f'{APP_PREFIX}/api/getWireguardConfigurationRawFile')
def API_GetWireguardConfigurationRawFile():
    configurationName = request.args.get('configurationName')
    if configurationName is None or len(
            configurationName) == 0 or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide a valid configuration name", status_code=404)
    
    return ResponseObject(data={
        "path": WireguardConfigurations[configurationName].configPath,
        "content": WireguardConfigurations[configurationName].getRawConfigurationFile()
    })

@app.post(f'{APP_PREFIX}/api/updateWireguardConfigurationRawFile')
def API_UpdateWireguardConfigurationRawFile():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    configurationName = data.get('configurationName')
    rawConfiguration = data.get('rawConfiguration')
    if configurationName is None or len(
            configurationName) == 0 or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide a valid configuration name", status_code=404)
    if rawConfiguration is None or len(rawConfiguration) == 0:
        return ResponseObject(False, "Please provide content", status_code=400)
    
    status, err = WireguardConfigurations[configurationName].updateRawConfigurationFile(rawConfiguration)

    return ResponseObject(status=status, message=err)

@app.post(f'{APP_PREFIX}/api/deleteWireguardConfiguration')
def API_deleteWireguardConfiguration():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    if "ConfigurationName" not in data.keys() or data.get("ConfigurationName") is None or data.get("ConfigurationName") not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide the configuration name you want to delete", status_code=404)
    rp =  WireguardConfigurations.pop(data.get("ConfigurationName"))
    
    status = rp.deleteConfiguration()
    if not status:
        WireguardConfigurations[data.get("ConfigurationName")] = rp
    return ResponseObject(status)

@app.post(f'{APP_PREFIX}/api/renameWireguardConfiguration')
def API_renameWireguardConfiguration():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    keys = ["ConfigurationName", "NewConfigurationName"]
    for k in keys:
        if (k not in data.keys() or data.get(k) is None or len(data.get(k)) == 0 or 
                (k == "ConfigurationName" and data.get(k) not in WireguardConfigurations.keys())): 
            return ResponseObject(False, "Please provide the configuration name you want to rename", status_code=404)
    
    if data.get("NewConfigurationName") in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration name already exist", status_code=400)
    
    rc = WireguardConfigurations.pop(data.get("ConfigurationName"))
    
    status, message = rc.renameConfiguration(data.get("NewConfigurationName"))
    if status:
        new_name = data.get("NewConfigurationName")
        WireguardConfigurations[new_name] = (
            WireguardConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, name=new_name)
            if rc.Protocol == 'wg'
            else AmneziaConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, name=new_name)
        )
        return ResponseObject(True, "Configuration renamed successfully. Notice: If you are using OxBot or an external integration, update inboundid to the new configuration name.")
    else:
        WireguardConfigurations[data.get("ConfigurationName")] = rc
        return ResponseObject(False, message)

@app.get(f'{APP_PREFIX}/api/getWireguardConfigurationRealtimeTraffic')
def API_getWireguardConfigurationRealtimeTraffic():
    configurationName = request.args.get('configurationName')
    if configurationName is None or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    return ResponseObject(data=WireguardConfigurations[configurationName].getRealtimeTrafficUsage())

@app.get(f'{APP_PREFIX}/api/getWireguardConfigurationBackup')
def API_getWireguardConfigurationBackup():
    configurationName = request.args.get('configurationName')
    if configurationName is None or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist",  status_code=404)
    return ResponseObject(data=WireguardConfigurations[configurationName].getBackups())

@app.get(f'{APP_PREFIX}/api/getAllWireguardConfigurationBackup')
def API_getAllWireguardConfigurationBackup():
    data = {
        "ExistingConfigurations": {},
        "NonExistingConfigurations": {}
    }
    existingConfiguration = WireguardConfigurations.keys()
    for i in existingConfiguration:
        b = WireguardConfigurations[i].getBackups(True)
        if len(b) > 0:
            data['ExistingConfigurations'][i] = WireguardConfigurations[i].getBackups(True)
            
    for protocol in ProtocolsEnabled():
        directory = os.path.join(DashboardConfig.GetConfig("Server", f"{protocol}_conf_path")[1], 'WGDashboard_Backup')
        if os.path.exists(directory):
            files = [(file, os.path.getctime(os.path.join(directory, file)))
                     for file in os.listdir(directory) if os.path.isfile(os.path.join(directory, file))]
            files.sort(key=lambda x: x[1], reverse=True)
        
            for f, ct in files:
                if RegexMatch(r"^(.+)_(\d+)\.(conf)$", f):
                    s = re.search(r"^(.+)_(\d+)\.(conf)$", f)
                    name = s.group(1)
                    if name not in existingConfiguration:
                        if name not in data['NonExistingConfigurations'].keys():
                            data['NonExistingConfigurations'][name] = []
                        
                        date = s.group(2)
                        conf_path = os.path.join(DashboardConfig.GetConfig("Server", f"{protocol}_conf_path")[1], 'WGDashboard_Backup', f)
                        with open(conf_path, 'r', encoding='utf-8', errors='ignore') as conf_f:
                            conf_content = conf_f.read()
                        d = {
                            "protocol": protocol,
                            "filename": f,
                            "backupDate": date,
                            "content": conf_content
                        }
                        sql_filename = f.replace(".conf", ".sql")
                        if sql_filename in os.listdir(directory):
                            d['database'] = True
                            sql_path = os.path.join(DashboardConfig.GetConfig("Server", f"{protocol}_conf_path")[1], 'WGDashboard_Backup', sql_filename)
                            with open(sql_path, 'r', encoding='utf-8', errors='ignore') as sql_f:
                                d['databaseContent'] = sql_f.read()
                        data['NonExistingConfigurations'][name].append(d)
    return ResponseObject(data=data)

@app.route(f'{APP_PREFIX}/api/createWireguardConfigurationBackup', methods=["GET", "POST"])
def API_createWireguardConfigurationBackup():
    data = request.get_json(silent=True) or {}
    configurationName = request.args.get('configurationName') or data.get('configurationName')
    if configurationName is None or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist",  status_code=404)
    return ResponseObject(status=WireguardConfigurations[configurationName].backupConfigurationFile()[0], 
                          data=WireguardConfigurations[configurationName].getBackups())

@app.post(f'{APP_PREFIX}/api/deleteWireguardConfigurationBackup')
def API_deleteWireguardConfigurationBackup():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    if ("ConfigurationName" not in data.keys() or 
            "BackupFileName" not in data.keys() or
            len(data['ConfigurationName']) == 0 or 
            len(data['BackupFileName']) == 0):
        return ResponseObject(False, 
        "Please provide configurationName and backupFileName in body",  status_code=400)
    configurationName = data['ConfigurationName']
    backupFileName = data['BackupFileName']
    if configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    
    status = WireguardConfigurations[configurationName].deleteBackup(backupFileName)
    return ResponseObject(status=status, message=(None if status else 'Backup file does not exist'), 
                          status_code = (200 if status else 404))

@app.get(f'{APP_PREFIX}/api/downloadWireguardConfigurationBackup')
def API_downloadWireguardConfigurationBackup():
    configurationName = os.path.basename(request.args.get('configurationName'))
    backupFileName = os.path.basename(request.args.get('backupFileName'))

    if configurationName is None or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)

    status, zip = WireguardConfigurations[configurationName].downloadBackup(backupFileName)

    if not status:
        current_app.logger.error(f"Failed to download a requested backup.\nConfiguration Name: {configurationName}\nBackup File Name: {backupFileName}")
        return ResponseObject(False, "Internal server error", status_code=500)

    return send_file(os.path.join('download', zip), as_attachment=True)

@app.post(f'{APP_PREFIX}/api/restoreWireguardConfigurationBackup')
def API_restoreWireguardConfigurationBackup():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    if ("ConfigurationName" not in data.keys() or
            "BackupFileName" not in data.keys() or
            len(data['ConfigurationName']) == 0 or
            len(data['BackupFileName']) == 0):
        return ResponseObject(False,
                              "Please provide ConfigurationName and BackupFileName in body", status_code=400)
    configurationName = data['ConfigurationName']
    backupFileName = data['BackupFileName']
    if configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    
    status = WireguardConfigurations[configurationName].restoreBackup(backupFileName)
    return ResponseObject(status=status, message=(None if status else 'Restore backup failed'))

# ==========================================
# GLOBAL BACKUP API ENDPOINTS
# ==========================================

@app.get(f'{APP_PREFIX}/api/globalBackup/list')
def API_listGlobalBackups():
    backups = GlobalBackupManager.list_global_backups()
    return ResponseObject(data=backups)

@app.post(f'{APP_PREFIX}/api/globalBackup/create')
def API_createGlobalBackup():
    import modules.GlobalBackup
    data = request.get_json(silent=True) or {}
    label = data.get('label', '')
    include_logs = data.get('includeLogs', True)
    include_existing = data.get('includeExistingBackups', False)
    
    if app.config.get('MAINTENANCE_MODE', False):
        return ResponseObject(False, "Cannot create backup while system is in maintenance mode or restoring.", status_code=409)

    with _is_backup_running_lock:
        if modules.GlobalBackup._is_backup_running:
            return ResponseObject(False, "A backup or restore job is currently running.", status_code=409)
        modules.GlobalBackup._is_backup_running = True

    job_id = str(uuid.uuid4())
    with _backup_jobs_lock:
        _backup_jobs[job_id] = {
            "pct": 0,
            "step": "initializing",
            "done": False,
            "error": None,
            "filename": None,
            "created_at": datetime.utcnow()
        }

    def _do_backup(jid: str, lbl: str, inc_logs: bool, inc_ext: bool):
        try:
            with WireguardConfigurationsLock:
                status, result = GlobalBackupManager.create_global_backup(
                    label=lbl,
                    include_logs=inc_logs,
                    include_existing_backups=inc_ext,
                    job_id=jid
                )
            with _backup_jobs_lock:
                if status:
                    _backup_jobs[jid].update({"done": True, "pct": 100, "step": "done", "filename": result.get("filename"), "finished_at": datetime.utcnow()})
                else:
                    _backup_jobs[jid].update({"done": True, "error": str(result), "finished_at": datetime.utcnow()})
        except Exception as e:
            with _backup_jobs_lock:
                _backup_jobs[jid].update({"done": True, "error": str(e), "finished_at": datetime.utcnow()})
        finally:
            with _is_backup_running_lock:
                modules.GlobalBackup._is_backup_running = False

    t = threading.Thread(target=_do_backup, args=(job_id, label, include_logs, include_existing), daemon=True)
    t.start()
    
    return ResponseObject(True, "Global backup started successfully", data={"job_id": job_id})

@app.get(f'{APP_PREFIX}/api/globalBackup/create/progress/<job_id>')
def API_createGlobalBackup_progress(job_id):
    def event_stream():
        # GB-11: 15-minute timeout (900 seconds) with periodic keepalive
        for i in range(900):
            _cleanup_expired_jobs()
            with _backup_jobs_lock:
                info = _backup_jobs.get(job_id)
            
            if not info:
                yield f"data: {json.dumps({'error': 'Job not found', 'done': True})}\n\n"
                break
                
            yield f"data: {json.dumps(info, default=str)}\n\n"
            
            if info.get("done"):
                break

            # SSE comment keepalive every 15s
            if i % 15 == 0:
                yield ": keepalive\n\n"

            time.sleep(1)
            
    return Response(event_stream(), mimetype="text/event-stream")

@app.post(f'{APP_PREFIX}/api/globalBackup/delete')
def API_deleteGlobalBackup():
    data = request.get_json(silent=True) or {}
    filename = data.get('filename')
    if not filename:
        return ResponseObject(False, "Filename is required", status_code=400)
    
    clean_filename = os.path.basename(filename)
    if not clean_filename.startswith('GlobalBackup_') or not clean_filename.endswith('.zip'):
        return ResponseObject(False, "Invalid backup filename", status_code=400)
    
    status = GlobalBackupManager.delete_global_backup(clean_filename)
    if not status:
        return ResponseObject(False, "Backup file not found or deletion failed", status_code=404)
    
    return ResponseObject(True, "Backup file deleted successfully")

@app.get(f'{APP_PREFIX}/api/globalBackup/download')
def API_downloadGlobalBackup():
    filename = request.args.get('filename')
    if not filename:
        return ResponseObject(False, "Filename is required", status_code=400)
    
    clean_filename = os.path.basename(filename)
    if not clean_filename.startswith('GlobalBackup_') or not clean_filename.endswith('.zip'):
        return ResponseObject(False, "Invalid backup filename", status_code=400)
    
    fpath = os.path.join(GlobalBackupManager.get_backup_dir(), clean_filename)
    if not os.path.exists(fpath):
        return ResponseObject(False, "Backup file not found", status_code=404)
    
    return send_file(fpath, as_attachment=True, download_name=clean_filename)

@app.post(f'{APP_PREFIX}/api/globalBackup/restore')
def API_restoreGlobalBackup():
    """Start restore in a background thread and immediately return a job_id."""
    import modules.GlobalBackup

    # GB-04: Require authenticated admin session; reject API key access
    if g.get('api_accessed', False) or session.get("role") != "admin":
        return ResponseObject(False, "Restore requires an authenticated admin session and cannot be performed via API key.", status_code=403)

    # GB-13: Reject oversized payload (max 500 MB)
    if request.content_length and request.content_length > 500 * 1024 * 1024:
        return ResponseObject(False, "Backup file exceeds maximum allowed size (500 MB)", status_code=413)

    if app.config.get('MAINTENANCE_MODE', False):
        return ResponseObject(False, "System is already undergoing maintenance or restoring.", status_code=409)

    # GB-10: Check and acquire backup concurrency lock atomically
    with _is_backup_running_lock:
        if modules.GlobalBackup._is_backup_running:
            return ResponseObject(False, "A backup or restore job is currently running.", status_code=409)
        modules.GlobalBackup._is_backup_running = True

    app.config['MAINTENANCE_MODE'] = True
    _cleanup_expired_jobs()

    target_path = None
    is_uploaded = False

    try:
        if 'backupFile' in request.files:
            file = request.files['backupFile']
            if file and file.filename.endswith('.zip'):
                # Validate ZIP magic bytes (PK\x03\x04)
                header = file.read(4)
                file.seek(0)
                if header != b'PK\x03\x04':
                    with _is_backup_running_lock:
                        modules.GlobalBackup._is_backup_running = False
                    app.config['MAINTENANCE_MODE'] = False
                    return ResponseObject(False, "Invalid backup file format", status_code=400)

                clean_name = os.path.basename(file.filename)
                target_path = os.path.join(GlobalBackupManager.get_backup_dir(), f"uploaded_{uuid.uuid4().hex}_{clean_name}")
                file.save(target_path)
                is_uploaded = True

        if not target_path:
            data = request.get_json(silent=True) or {}
            filename = data.get('filename')
            if filename:
                clean_name = os.path.basename(filename)
                target_path = os.path.join(GlobalBackupManager.get_backup_dir(), clean_name)

        if not target_path or not os.path.exists(target_path):
            if is_uploaded and target_path and os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except Exception:
                    pass
            with _is_backup_running_lock:
                modules.GlobalBackup._is_backup_running = False
            app.config['MAINTENANCE_MODE'] = False
            return ResponseObject(False, "Valid backup file or filename must be provided", status_code=400)

        # Register a new job
        job_id = str(uuid.uuid4())
        with _restore_jobs_lock:
            _restore_jobs[job_id] = {
                "pct": 0,
                "step": "queued",
                "done": False,
                "error": None,
                "created_at": datetime.utcnow().isoformat()
            }

        def _run_restore(tp: str, jid: str, uploaded: bool):
            app.config['MAINTENANCE_MODE'] = True
            try:
                status, message = GlobalBackupManager.restore_global_backup(tp, job_id=jid)
            except Exception as exc:
                status, message = False, str(exc)
            finally:
                if uploaded and os.path.exists(tp):
                    try:
                        os.remove(tp)
                    except Exception:
                        pass
                with _is_backup_running_lock:
                    modules.GlobalBackup._is_backup_running = False

            with _restore_jobs_lock:
                if jid in _restore_jobs:
                    if not status:
                        _restore_jobs[jid].update({
                            "done": True, "error": message,
                            "finished_at": datetime.utcnow()
                        })
                    else:
                        _restore_jobs[jid].update({
                            "done": True, "error": None,
                            "finished_at": datetime.utcnow()
                        })

            if not status:
                # Restore failed: reset maintenance mode
                app.config['MAINTENANCE_MODE'] = False
            else:
                # GB-05: Successful restore - keep MAINTENANCE_MODE = True until process exit!
                # Delay slightly so client receives the final 100% update
                time.sleep(3)
                _cleanup_expired_jobs()
                try:
                    if os.name != 'nt':
                        res = subprocess.run(["systemctl", "restart", "wg-dashboard"], capture_output=True, timeout=5)
                        if res.returncode != 0:
                            os.kill(os.getpid(), signal.SIGTERM)
                except Exception:
                    try:
                        if os.name != 'nt':
                            os.kill(os.getpid(), signal.SIGTERM)
                    except Exception:
                        pass

        threading.Thread(
            target=_run_restore,
            args=(target_path, job_id, is_uploaded),
            daemon=True,
            name=f"restore-{job_id[:8]}"
        ).start()

        return ResponseObject(True, "Restore started", data={"job_id": job_id})

    except Exception as e:
        if is_uploaded and target_path and os.path.exists(target_path):
            try:
                os.remove(target_path)
            except Exception:
                pass
        with _is_backup_running_lock:
            modules.GlobalBackup._is_backup_running = False
        app.config['MAINTENANCE_MODE'] = False
        return ResponseObject(False, f"Restore start error: {str(e)}", status_code=500)


@app.get(f'{APP_PREFIX}/api/globalBackup/restore/progress')
def API_restoreGlobalBackupProgress():
    """SSE endpoint — streams restore job progress to the frontend."""
    job_id = request.args.get('job_id', '')
    if not job_id:
        return ResponseObject(False, "job_id is required", status_code=400)

    def _event_stream():
        sent_done = False
        # GB-11: 15 minutes timeout (1800 * 0.5s) with periodic keepalive comments
        for i in range(1800):
            with _restore_jobs_lock:
                job = dict(_restore_jobs.get(job_id, {}))

            if not job:
                yield f"data: {json.dumps({'error': 'Job not found', 'done': True})}\n\n"
                break

            payload = {
                "pct": job.get("pct", 0),
                "step": job.get("step", ""),
                "done": job.get("done", False),
                "error": job.get("error")
            }
            yield f"data: {json.dumps(payload)}\n\n"

            if job.get("done") or job.get("error"):
                sent_done = True
                break

            if i % 30 == 0:
                yield ": keepalive\n\n"

            time.sleep(0.5)

        if not sent_done:
            yield f"data: {json.dumps({'error': 'Restore timed out', 'done': True})}\n\n"

    return Response(
        _event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )

@app.get(f'{APP_PREFIX}/api/globalBackup/create/progress/poll/<job_id>')
def API_createGlobalBackup_progress_poll(job_id):
    """JSON polling endpoint for global backup creation progress (GB-11)."""
    _cleanup_expired_jobs()
    with _backup_jobs_lock:
        info = _backup_jobs.get(job_id)
    if not info:
        return ResponseObject(False, "Job not found", status_code=404)
    return ResponseObject(True, data=dict(info))

@app.get(f'{APP_PREFIX}/api/globalBackup/restore/progress/poll')
def API_restoreGlobalBackupProgress_poll():
    """JSON polling endpoint for global backup restore progress (GB-11)."""
    job_id = request.args.get('job_id', '')
    if not job_id:
        return ResponseObject(False, "job_id is required", status_code=400)
    _cleanup_expired_jobs()
    with _restore_jobs_lock:
        job = dict(_restore_jobs.get(job_id, {}))
    if not job:
        return ResponseObject(False, "Job not found", status_code=404)
    return ResponseObject(True, data=job)

@app.get(f'{APP_PREFIX}/api/getDashboardConfiguration')
def API_getDashboardConfiguration():
    return ResponseObject(data=DashboardConfig.toJson())

@app.get(f'{APP_PREFIX}/api/globalBackup/settings')
def API_getGlobalBackupSettings():
    _, auto_backup = DashboardConfig.GetConfig("GlobalBackup", "auto_backup")
    _, schedule = DashboardConfig.GetConfig("GlobalBackup", "auto_backup_schedule")
    return ResponseObject(data={
        "auto_backup": auto_backup,
        "auto_backup_schedule": schedule
    })

@app.post(f'{APP_PREFIX}/api/updateDashboardConfigurationItem')
def API_updateDashboardConfigurationItem():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    if "section" not in data.keys() or "key" not in data.keys() or "value" not in data.keys():
        return ResponseObject(False, "Invalid request.", status_code=400)
    valid, msg = DashboardConfig.SetConfig(
        data["section"], data["key"], data['value'])
    if not valid:
        return ResponseObject(False, msg, status_code=400)
    if data['section'] == "Server":
        if data['key'] == 'wg_conf_path':
            with WireguardConfigurationsLock:
                WireguardConfigurations.clear()
            InitWireguardConfigurationsList()
    if data.get('section') == "Account" and data.get('key') == "password":
        session["session_epoch"] = str(DashboardConfig.GetConfig("Account", "session_epoch")[1] or "1")
    return ResponseObject(True, data=DashboardConfig.GetConfig(data["section"], data["key"])[1])

@app.get(f'{APP_PREFIX}/api/getDashboardAPIKeys')
def API_getDashboardAPIKeys():
    if DashboardConfig.GetConfig('Server', 'dashboard_api_key')[1]:
        return ResponseObject(data=DashboardConfig.DashboardAPIKeys)
    return ResponseObject(False, "WGDashboard API Keys function is disabled")

@app.post(f'{APP_PREFIX}/api/newDashboardAPIKey')
def API_newDashboardAPIKey():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    if DashboardConfig.GetConfig('Server', 'dashboard_api_key')[1]:
        try:
            if data.get('NeverExpire'):
                expiredAt = None
            else:
                expired_str = data.get('ExpiredAt')
                if not expired_str:
                    return ResponseObject(False, "ExpiredAt is required when NeverExpire is false", status_code=400)
                expiredAt = datetime.strptime(expired_str, '%Y-%m-%d %H:%M:%S')
            DashboardConfig.createAPIKeys(expiredAt)
            return ResponseObject(True, data=DashboardConfig.DashboardAPIKeys)
        except Exception as e:
            return ResponseObject(False, str(e), status_code=400)
    return ResponseObject(False, "Dashboard API Keys function is disabled")

@app.post(f'{APP_PREFIX}/api/deleteDashboardAPIKey')
def API_deleteDashboardAPIKey():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    if DashboardConfig.GetConfig('Server', 'dashboard_api_key')[1]:
        key = data.get('Key', '')
        if len(key) > 0 and len(list(filter(lambda x : x.Key == key, DashboardConfig.DashboardAPIKeys))) > 0:
            DashboardConfig.deleteAPIKey(key)
            return ResponseObject(True, data=DashboardConfig.DashboardAPIKeys)
        else:
            return ResponseObject(False, "API Key does not exist", status_code=404)
    return ResponseObject(False, "Dashboard API Keys function is disabled")
    
@app.post(f'{APP_PREFIX}/api/updatePeerSettings/<configName>')
def API_updatePeerSettings(configName):
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    id = data.get('id')
    if not id:
        return ResponseObject(False, "Peer ID is required", status_code=400)
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)

    name = data.get('name', '')
    private_key = data.get('private_key', '')
    dns_addresses = data.get('DNS', '')
    allowed_ip = data.get('allowed_ip', '')
    endpoint_allowed_ip = data.get('endpoint_allowed_ip', '')
    preshared_key = data.get('preshared_key', '')
    mtu = data.get('mtu', 0)
    keepalive = data.get('keepalive', 0)
    notes = data.get('notes', '')
    wireguardConfig = WireguardConfigurations[configName]
    with wireguardConfig.lock:
        foundPeer, peer = wireguardConfig.searchPeer(id)
        if foundPeer:
            status, msg = peer.updatePeer(name,
                                          private_key,
                                          preshared_key, 
                                          dns_addresses,
                                          allowed_ip,
                                          endpoint_allowed_ip,
                                          mtu,
                                          keepalive,
                                          notes)
            wireguardConfig.getPeers()
            DashboardWebHooks.RunWebHook('peer_updated', {
                "configuration": wireguardConfig.Name,
                "peers": [peer.id]
            })
            return ResponseObject(status, msg)
        
    return ResponseObject(False, "Peer does not exist", status_code=404)

@app.post(f'{APP_PREFIX}/api/resetPeerData/<configName>')
def API_resetPeerData(configName):
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    id = data.get('id')
    type = data.get('type')
    if not id or not type:
        return ResponseObject(False, "Peer ID and reset type are required", status_code=400)
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    wgc = WireguardConfigurations.get(configName)
    with wgc.lock:
        foundPeer, peer = wgc.searchPeer(id)
        is_restricted = False
        if not foundPeer:
            for r_peer in (wgc.RestrictedPeers or []):
                if r_peer.id == id or r_peer.id == (id.strip().replace(' ', '+') if id else id):
                    foundPeer, peer = True, r_peer
                    is_restricted = True
                    break
        else:
            for r_peer in (wgc.RestrictedPeers or []):
                if r_peer.id == id or r_peer.id == (id.strip().replace(' ', '+') if id else id):
                    is_restricted = True
                    break

        if not foundPeer:
            return ResponseObject(False, "Configuration/Peer does not exist", status_code=404)
        
        resetStatus = peer.resetDataUsage(type)
        if resetStatus:
            if not is_restricted and wgc.getStatus():
                wgc.restrictPeers([id], reason="Resetting data usage")
                wgc.allowAccessPeers([id], force=True)
            try:
                wgc.DashboardWebHooks.RunWebHook("peer_usage_reset", {
                    "configuration": configName,
                    "peer_id": id,
                    "type": type
                })
            except Exception as e:
                _logger.error(f"Error triggering peer_usage_reset webhook: {e}")
        
        return ResponseObject(status=resetStatus, status_code=200 if resetStatus else 400)

@app.post(f'{APP_PREFIX}/api/deletePeers/<configName>')
def API_deletePeers(configName: str) -> ResponseObject:
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    peers = data.get('peers')
    if not isinstance(peers, list) or len(peers) == 0:
        return ResponseObject(False, "Please specify one or more peers", status_code=400)
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)

    configuration = WireguardConfigurations.get(configName)
    with configuration.lock:
        status, msg = configuration.deletePeers(peers, AllPeerJobs, AllPeerShareLinks)
        if not status:
            return ResponseObject(False, msg, status_code=404 if "No peer(s) to delete found" in msg else 400)
        
        # Delete Assignment
        for p in peers:
            assignments = DashboardClients.DashboardClientsPeerAssignment.GetAssignedClients(configName, p)
            for c in assignments:
                DashboardClients.DashboardClientsPeerAssignment.UnassignClients(c.AssignmentID)
        
        return ResponseObject(status, msg)

@app.post(f'{APP_PREFIX}/api/restrictPeers/<configName>')
def API_restrictPeers(configName: str) -> ResponseObject:
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    peers = data.get('peers')
    reason = data.get('reason')
    if not isinstance(peers, list) or len(peers) == 0:
        return ResponseObject(False, "Please specify one or more peers", status_code=400)
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)

    configuration = WireguardConfigurations.get(configName)
    with configuration.lock:
        status, msg = configuration.restrictPeers(peers, reason=reason)
        return ResponseObject(status, msg, status_code=200 if status else 400)

@app.post(f'{APP_PREFIX}/api/sharePeer/create')
def API_sharePeer_create():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    Configuration = data.get('Configuration')
    Peer = data.get('Peer')
    ExpireDate = data.get('ExpireDate')
    if not Configuration or not Peer:
        return ResponseObject(False, "Please specify configuration and peer", status_code=400)
    activeLink = AllPeerShareLinks.getLink(Configuration, Peer)
    if len(activeLink) > 0:
        return ResponseObject(True, 
                              "This peer is already sharing. Please view data for shared link.",
                                data=activeLink[0]
        )
    expire_dt = None
    if ExpireDate:
        try:
            expire_dt = datetime.strptime(ExpireDate, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return ResponseObject(False, "Invalid date format for ExpireDate. Expected YYYY-MM-DD HH:MM:SS", status_code=400)
    status, message = AllPeerShareLinks.addLink(Configuration, Peer, expire_dt)
    if not status:
        return ResponseObject(status, message)
    return ResponseObject(data=AllPeerShareLinks.getLinkByID(message))

@app.post(f'{APP_PREFIX}/api/sharePeer/update')
def API_sharePeer_update():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    ShareID = data.get("ShareID")
    ExpireDate = data.get("ExpireDate")
    
    if not ShareID:
        return ResponseObject(False, "Please specify ShareID", status_code=400)
    
    if len(AllPeerShareLinks.getLinkByID(ShareID)) == 0:
        return ResponseObject(False, "ShareID does not exist", status_code=404)
    
    expire_dt = None
    if ExpireDate:
        try:
            expire_dt = datetime.strptime(ExpireDate, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return ResponseObject(False, "Invalid date format for ExpireDate. Expected YYYY-MM-DD HH:MM:SS", status_code=400)

    status, message = AllPeerShareLinks.updateLinkExpireDate(ShareID, expire_dt)
    if not status:
        return ResponseObject(status, message)
    return ResponseObject(data=AllPeerShareLinks.getLinkByID(ShareID))

@app.get(f'{APP_PREFIX}/api/sharePeer/get')
def API_sharePeer_get():
    data = request.args
    ShareID = data.get("ShareID")
    if ShareID is None or len(ShareID) == 0:
        return ResponseObject(False, "Please provide ShareID", status_code=400)
    link = AllPeerShareLinks.getLinkByID(ShareID)
    if len(link) == 0:
        return ResponseObject(False, "This link is either expired to invalid", status_code=404)
    l = link[0]
    if l.Configuration not in WireguardConfigurations.keys():
        return ResponseObject(False, "The peer you're looking for does not exist", status_code=404)
    c = WireguardConfigurations.get(l.Configuration)
    fp, p = c.searchPeer(l.Peer)
    if not fp:
        return ResponseObject(False, "The peer you're looking for does not exist", status_code=404)
    dl = p.downloadPeer()
    if dl.get("error"):
        return ResponseObject(False, dl["error"], data=dl)
    return ResponseObject(data=dl)
    
@app.post(f'{APP_PREFIX}/api/allowAccessPeers/<configName>')
def API_allowAccessPeers(configName: str) -> ResponseObject:
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    peers = data.get('peers')
    force = bool(data.get('force', False))
    if not isinstance(peers, list) or len(peers) == 0:
        return ResponseObject(False, "Please specify one or more peers", status_code=400)
    if configName in WireguardConfigurations.keys():
        configuration = WireguardConfigurations.get(configName)
        with configuration.lock:
            status, msg = configuration.allowAccessPeers(peers, force=force)
            return ResponseObject(status, msg, status_code=200 if status else 400)
    return ResponseObject(False, "Configuration does not exist", status_code=404)

@app.post(f'{APP_PREFIX}/api/reconcileConfiguration/<configName>')
def API_reconcileConfiguration(configName: str) -> ResponseObject:
    if configName in WireguardConfigurations.keys():
        configuration = WireguardConfigurations.get(configName)
        if not configuration.getStatus():
            return ResponseObject(False, f"Configuration {configName} is not active", status_code=400)
        res = configuration.reconcileWithKernel()
        return ResponseObject(True, "Configuration reconciled successfully", data=res)
    return ResponseObject(False, "Configuration does not exist", status_code=404)

@app.post(f'{APP_PREFIX}/api/addPeers/<configName>')
def API_addPeers(configName):
    if configName in WireguardConfigurations.keys():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return ResponseObject(False, "Invalid JSON body", status_code=400)
        try:
            

            bulkAdd: bool = data.get("bulkAdd", False)
            bulkAddAmount: int = data.get('bulkAddAmount', 0)
            preshared_key_bulkAdd: bool = data.get('preshared_key_bulkAdd', False)

            public_key: str = data.get('public_key', "")
            allowed_ips: list[str] = data.get('allowed_ips', [])
            allowed_ips_validation: bool = data.get('allowed_ips_validation', True)
            
            endpoint_allowed_ip: str = data.get('endpoint_allowed_ip', DashboardConfig.GetConfig("Peers", "peer_endpoint_allowed_ip")[1])
            dns_addresses: str = data.get('DNS', DashboardConfig.GetConfig("Peers", "peer_global_DNS")[1])
            
            
            mtu: int = data.get('mtu', None)
            keep_alive: int = data.get('keepalive', None)
            notes: str = data.get('notes', '')
            preshared_key: str = data.get('preshared_key', "")            
    
            if type(mtu) is not int or mtu < 0 or mtu > 1460:
                default: str = DashboardConfig.GetConfig("Peers", "peer_mtu")[1]
                if default.isnumeric():
                    try:
                        mtu = int(default)
                    except Exception as e:
                        mtu = 0
                else:
                    mtu = 0
            if type(keep_alive) is not int or keep_alive < 0:
                default = DashboardConfig.GetConfig("Peers", "peer_keep_alive")[1]
                if default.isnumeric():
                    try:
                        keep_alive = int(default)
                    except Exception as e:
                        keep_alive = 0
                else:
                    keep_alive = 0
            
            config = WireguardConfigurations.get(configName)
            with config.lock:
                ipStatus, availableIps = config.getAvailableIP(-1)
                ipCountStatus, numberOfAvailableIPs = config.getNumberOfAvailableIP()
                defaultIPSubnet = list(availableIps.keys())[0] if availableIps and len(availableIps.keys()) > 0 else ""
                if bulkAdd:
                    if type(preshared_key_bulkAdd) is not bool:
                        preshared_key_bulkAdd = False
                    if type(bulkAddAmount) is not int or bulkAddAmount < 1:
                        return ResponseObject(False, "Please specify amount of peers you want to add")
                    if not ipStatus:
                        return ResponseObject(False, "No more available IP can assign")
                    if len(availableIps.keys()) == 0:
                        return ResponseObject(False, "This configuration does not have any IP address available")
                    if bulkAddAmount > sum(list(numberOfAvailableIPs.values())):
                        return ResponseObject(False,
                                f"The maximum number of peers can add is {sum(list(numberOfAvailableIPs.values()))}")
                    keyPairs = []
                    addedCount = 0
                    for subnet in availableIps.keys():
                        for ip in availableIps[subnet]:
                            newPrivateKey = GenerateWireguardPrivateKey()[1]
                            addedCount += 1
                            keyPairs.append({
                                "private_key": newPrivateKey,
                                "id": GenerateWireguardPublicKey(newPrivateKey)[1],
                                "preshared_key": (GenerateWireguardPrivateKey()[1] if preshared_key_bulkAdd else ""),
                                "allowed_ip": ip,
                                "name": f"BulkPeer_{addedCount}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                                "DNS": dns_addresses,
                                "endpoint_allowed_ip": endpoint_allowed_ip,
                                "mtu": mtu,
                                "keepalive": keep_alive,
                                "notes": ""
                            })
                            if addedCount == bulkAddAmount:
                                break
                        if addedCount == bulkAddAmount:
                            break
                    if len(keyPairs) == 0 or (bulkAdd and len(keyPairs) != bulkAddAmount):
                        return ResponseObject(False, "Generating key pairs by bulk failed")
                    status, addedPeers, message = config.addPeers(keyPairs)
                    return ResponseObject(status=status, message=message, data=addedPeers)
        
                else:
                    if config.searchPeer(public_key)[0] is True:
                        return ResponseObject(False, f"This peer already exist")
                    name = data.get("name", "")
                    private_key = data.get("private_key", "")
    
                    if len(public_key) == 0:
                        if len(private_key) == 0:
                            private_key = GenerateWireguardPrivateKey()[1]
                            public_key = GenerateWireguardPublicKey(private_key)[1]
                        else:
                            public_key = GenerateWireguardPublicKey(private_key)[1]
                    else:
                        if len(private_key) > 0:
                            genPub = GenerateWireguardPublicKey(private_key)[1]
                            # Check if provided pubkey match provided private key
                            if public_key != genPub:
                                return ResponseObject(False, "Provided Public Key does not match provided Private Key")
                    if len(allowed_ips) == 0:
                        if ipStatus:
                            for subnet in availableIps.keys():
                                for ip in availableIps[subnet]:
                                    allowed_ips = [ip]
                                    break
                                break  
                        else:
                            return ResponseObject(False, "No more available IP can assign") 
    
                    if allowed_ips_validation:
                        for i in allowed_ips:
                            found = False
                            for subnet in availableIps.keys():
                                try:
                                    network = ipaddress.ip_network(subnet, False)
                                    ap = ipaddress.ip_network(i)
                                except ValueError as e:
                                    return ResponseObject(False, str(e))
                                if network.version == ap.version and ap.subnet_of(network):
                                    found = True
                            
                            if not found:
                                return ResponseObject(False, f"This IP is not available: {i}")
    
                    status, addedPeers, message = config.addPeers([
                        {
                            "name": name,
                            "id": public_key,
                            "private_key": private_key,
                            "allowed_ip": ','.join(allowed_ips),
                            "preshared_key": preshared_key,
                            "endpoint_allowed_ip": endpoint_allowed_ip,
                            "DNS": dns_addresses,
                            "mtu": mtu,
                            "keepalive": keep_alive,
                            "notes": notes
                        }]
                    )
                    return ResponseObject(status=status, message=message, data=addedPeers)
        except Exception as e:
            app.logger.error(f"Add peers failed: {e}")
            return ResponseObject(False, f"Add peers failed.")

    return ResponseObject(False, "Configuration does not exist")

@app.get(f"{APP_PREFIX}/api/downloadPeer/<configName>")
def API_downloadPeer(configName):
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    peer_id = request.args.get('id')
    if not peer_id:
        return ResponseObject(False, "Peer ID is required", status_code=400)
    configuration = WireguardConfigurations[configName]
    peerFound, peer = configuration.searchPeer(peer_id)
    if not peerFound:
        return ResponseObject(False, "Peer does not exist", status_code=404)
    dl = peer.downloadPeer()
    if dl.get("error"):
        return ResponseObject(False, dl["error"], data=dl)
    return ResponseObject(data=dl)

@app.get(f"{APP_PREFIX}/api/downloadAllPeers/<configName>")
def API_downloadAllPeers(configName):
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    configuration = WireguardConfigurations[configName]
    peerData = []
    untitledPeer = 0
    for i in configuration.Peers:
        file = i.downloadPeer()
        if file.get("error"):
            continue
        if file["fileName"] == "UntitledPeer":
            file["fileName"] = str(untitledPeer) + "_" + file["fileName"]
            untitledPeer += 1
        peerData.append(file)
    return ResponseObject(data=peerData)

@app.get(f"{APP_PREFIX}/api/getAvailableIPs/<configName>")
def API_getAvailableIPs(configName):
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    status, ips = WireguardConfigurations.get(configName).getAvailableIP()
    return ResponseObject(status=status, data=ips)

@app.get(f"{APP_PREFIX}/api/getNumberOfAvailableIPs/<configName>")
def API_getNumberOfAvailableIPs(configName):
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    status, ips = WireguardConfigurations.get(configName).getNumberOfAvailableIP()
    return ResponseObject(status=status, data=ips)

@app.get(f'{APP_PREFIX}/api/getWireguardConfigurationInfo')
def API_getConfigurationInfo():
    configurationName = request.args.get("configurationName")
    if not configurationName or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide configuration name", status_code=400)
    config = WireguardConfigurations[configurationName]

    include_peers = request.args.get("include_peers", "true").lower() != "false"
    peer_id = request.args.get("peer_id") or request.args.get("id")

    if not include_peers:
        return ResponseObject(data={
            "configurationInfo": config.toJson(),
            "configurationPeers": [],
            "configurationRestrictedPeers": []
        })

    restricted_peers = config.getRestrictedPeersList() or []
    restricted_ids = {p.id for p in restricted_peers}
    active_peers = [p for p in (config.Peers or []) if p.id not in restricted_ids and not getattr(p, 'restricted', False)]

    if peer_id:
        p_clean = peer_id.strip().replace(' ', '+')
        active_peers = [p for p in active_peers if p.id == p_clean or p.id == peer_id or getattr(p, 'name', '') == peer_id]
        restricted_peers = [p for p in restricted_peers if p.id == p_clean or p.id == peer_id or getattr(p, 'name', '') == peer_id]

    return ResponseObject(data={
        "configurationInfo": config.toJson(),
        "configurationPeers": active_peers,
        "configurationRestrictedPeers": restricted_peers
    })

@app.get(f'{APP_PREFIX}/api/getPeerHistoricalEndpoints')
def API_GetPeerHistoricalEndpoints():
    configurationName = request.args.get("configurationName")
    id = request.args.get('id')
    if not configurationName or not id or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide valid configurationName and id")
    fp, p = WireguardConfigurations.get(configurationName).searchPeer(id)
    if fp:
        result = p.getEndpoints()
        geo = {}
        try:
            r = requests.post(f"https://ip-api.com/batch?fields=city,country,lat,lon,query",
                              data=json.dumps([x['endpoint'] for x in result]), timeout=10)
            d = r.json()
            
                
        except Exception as e:
            return ResponseObject(data=result, message="Failed to request IP address geolocation. " + str(e))
        
        return ResponseObject(data={
            "endpoints": p.getEndpoints(),
            "geolocation": d
        })
    return ResponseObject(False, "Peer does not exist")

@app.get(f'{APP_PREFIX}/api/getPeerSessions')
def API_GetPeerSessions():
    configurationName = request.args.get("configurationName")
    id = request.args.get('id')
    try:
        startDate = request.args.get('startDate', None)
        endDate = request.args.get('endDate', None)
        
        if startDate is None:
            endDate = None
        else:
            startDate = datetime.strptime(startDate, "%Y-%m-%d")
            if endDate:
                endDate = datetime.strptime(endDate, "%Y-%m-%d")
                if startDate > endDate:
                    return ResponseObject(False, "startDate must be smaller than endDate")
    except Exception as e:
        return ResponseObject(False, "Dates are invalid")
    if not configurationName or not id or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide valid configurationName and id")
    fp, p = WireguardConfigurations.get(configurationName).searchPeer(id)
    if fp:
        return ResponseObject(data=p.getSessions(startDate, endDate))
    return ResponseObject(False, "Peer does not exist")

@app.get(f'{APP_PREFIX}/api/getPeerTraffics')
def API_GetPeerTraffics():
    configurationName = request.args.get("configurationName")
    id = request.args.get('id')
    try:
        interval = request.args.get('interval', 30)
        startDate = request.args.get('startDate', None)
        endDate = request.args.get('endDate', None)
        if type(interval) is str:
            if not interval.isdigit():
                return ResponseObject(False, "Interval must be integers in minutes")
            interval = int(interval)
        if startDate is None:
            endDate = None
        else:
            startDate = datetime.strptime(startDate, "%Y-%m-%d")
            if endDate:
                endDate = datetime.strptime(endDate, "%Y-%m-%d")
                if startDate > endDate:
                    return ResponseObject(False, "startDate must be smaller than endDate")
    except Exception as e:
        return ResponseObject(False, f"Dates are invalid: {e}")
    if not configurationName or not id or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Please provide valid configurationName and id")
    fp, p = WireguardConfigurations.get(configurationName).searchPeer(id)
    if fp:
        return ResponseObject(data=p.getTraffics(interval, startDate, endDate))
    return ResponseObject(False, "Peer does not exist")

@app.get(f'{APP_PREFIX}/api/getPeerTrackingTableCounts')
def API_GetPeerTrackingTableCounts():
    configurationName = request.args.get("configurationName")
    if configurationName and configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    
    if configurationName:
        c = WireguardConfigurations.get(configurationName)
        return ResponseObject(data={
            "TrafficTrackingTableSize": c.getTransferTableSize(),
            "HistoricalTrackingTableSize": c.getHistoricalEndpointTableSize()
        })
    
    d = {}
    for i in WireguardConfigurations.keys():
        c = WireguardConfigurations.get(i)
        d[i] = {
            "TrafficTrackingTableSize": c.getTransferTableSize(),
            "HistoricalTrackingTableSize": c.getHistoricalEndpointTableSize()
        }
    return ResponseObject(data=d)

@app.get(f'{APP_PREFIX}/api/downloadPeerTrackingTable')
def API_DownloadPeerTackingTable():
    configurationName = request.args.get("configurationName")
    table = request.args.get('table')
    if configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    if table not in ['TrafficTrackingTable', 'HistoricalTrackingTable']:
        return ResponseObject(False, "Table does not exist")
    c = WireguardConfigurations.get(configurationName)
    return ResponseObject(
        data=c.downloadTransferTable() if table == 'TrafficTrackingTable' 
        else c.downloadHistoricalEndpointTable())

@app.post(f'{APP_PREFIX}/api/deletePeerTrackingTable')
def API_DeletePeerTrackingTable():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    configurationName = data.get('configurationName')
    table = data.get('table')
    if not configurationName or configurationName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    if not table or table not in ['TrafficTrackingTable', 'HistoricalTrackingTable']:
        return ResponseObject(False, "Table does not exist", status_code=400)
    c = WireguardConfigurations.get(configurationName)
    return ResponseObject(
        status=c.deleteTransferTable() if table == 'TrafficTrackingTable'
        else c.deleteHistoryEndpointTable())

@app.get(f'{APP_PREFIX}/api/getDashboardTheme')
def API_getDashboardTheme():
    return ResponseObject(data=DashboardConfig.GetConfig("Server", "dashboard_theme")[1])

@app.get(f'{APP_PREFIX}/api/getDashboardVersion')
def API_getDashboardVersion():
    return ResponseObject(data=DashboardConfig.GetConfig("Server", "version")[1])

@app.post(f'{APP_PREFIX}/api/savePeerScheduleJob')
def API_savePeerScheduleJob():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or "Job" not in data:
        return ResponseObject(False, "Please specify job", status_code=400)
    job = data.get('Job')
    if not isinstance(job, dict) or "Peer" not in job or "Configuration" not in job:
        return ResponseObject(False, "Please specify peer and configuration", status_code=400)
    
    # BUG-02: Validate Job parameters
    field = job.get('Field')
    operator = job.get('Operator')
    action = job.get('Action')
    val = job.get('Value')

    if field not in ["total_receive", "total_sent", "total_data", "date"]:
        return ResponseObject(False, f"Invalid Job Field: {field}", status_code=400)
    if operator not in [">", "<", ">=", "<=", "==", "str_eq"]:
        return ResponseObject(False, f"Invalid Job Operator: {operator}", status_code=400)
    if action not in ["restrict", "delete", "reset_total_data_usage"]:
        return ResponseObject(False, f"Invalid Job Action: {action}", status_code=400)

    if field in ["total_receive", "total_sent", "total_data"]:
        try:
            val_num = float(val)
            if val_num < 0:
                return ResponseObject(False, "Job Value must be non-negative", status_code=400)
        except (ValueError, TypeError):
            return ResponseObject(False, "Job Value must be a valid number for data usage", status_code=400)
    elif field == "date":
        try:
            _parse_job_datetime(str(val))
        except (ValueError, TypeError):
            return ResponseObject(False, "Job Value must be a valid date in format YYYY-MM-DD HH:MM:SS, ISO-8601, or epoch timestamp", status_code=400)

    configuration = WireguardConfigurations.get(job.get('Configuration'))
    if configuration is None:
        return ResponseObject(False, "Configuration does not exist", status_code=404)
    f, fp = configuration.searchPeer(job.get('Peer'))
    if not f:
        for rp in (configuration.RestrictedPeers or []):
            if rp.id == job.get('Peer') or rp.id == (job.get('Peer').strip().replace(' ', '+') if job.get('Peer') else ''):
                f, fp = True, rp
                break
    if not f:
        return ResponseObject(False, "Peer does not exist", status_code=404)
    
    s, p = AllPeerJobs.saveJob(PeerJob(
        job.get('JobID') or str(uuid.uuid4()),
        job.get('Configuration'),
        job.get('Peer'),
        job.get('Field'),
        job.get('Operator'),
        job.get('Value'),
        job.get('CreationDate') or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        job.get('ExpireDate'),
        job.get('Action')
    ))
    if s:
        return ResponseObject(s, data=p)
    return ResponseObject(s, message=p)

@app.post(f'{APP_PREFIX}/api/deletePeerScheduleJob')
def API_deletePeerScheduleJob():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or "Job" not in data:
        return ResponseObject(False, "Please specify job", status_code=400)
    job = data.get('Job')
    if not isinstance(job, dict) or "Peer" not in job or "Configuration" not in job:
        return ResponseObject(False, "Please specify peer and configuration", status_code=400)
    configuration = WireguardConfigurations.get(job['Configuration'])
    if configuration is None:
        return ResponseObject(False, "Configuration does not exist", status_code=404)

    s, p = AllPeerJobs.deleteJob(PeerJob(
        job.get('JobID'), job.get('Configuration'), job.get('Peer'), job.get('Field'), job.get('Operator'), job.get('Value'),
        job.get('CreationDate'), job.get('ExpireDate'), job.get('Action')))
    if s:
        return ResponseObject(s)
    return ResponseObject(s, message=p)

@app.get(f'{APP_PREFIX}/api/getPeerScheduleJobLogs/<configName>')
def API_getPeerScheduleJobLogs(configName):
    if configName not in WireguardConfigurations.keys():
        return ResponseObject(False, "Configuration does not exist")
    data = request.args.get("requestAll")
    requestAll = False
    if data is not None and data == "true":
        requestAll = True
    return ResponseObject(data=AllPeerJobs.getPeerJobLogs(configName))

'''
Tools
'''

@app.get(f'{APP_PREFIX}/api/ping/getAllPeersIpAddress')
def API_ping_getAllPeersIpAddress():
    ips = {}
    for c in WireguardConfigurations.values():
        cips = {}
        for p in c.Peers:
            allowed_ip = p.allowed_ip.replace(" ", "").split(",")
            parsed = []
            for x in allowed_ip:
                try:
                    ip = ipaddress.ip_network(x, strict=False)
                    host = list(ip.hosts())
                    if len(host) == 1:
                        parsed.append(str(host[0]))
                except ValueError as e:
                    app.logger.error(f"Failed to parse IP address of {p.id} - {c.Name}: {e}")
            endpoint = p.endpoint.replace(" ", "").replace("(none)", "")
            if len(p.name) > 0:
                cips[f"{p.name} - {p.id}"] = {
                    "allowed_ips": parsed,
                    "endpoint": endpoint
                }
            else:
                cips[f"{p.id}"] = {
                    "allowed_ips": parsed,
                    "endpoint": endpoint
                }
        ips[c.Name] = cips
    return ResponseObject(data=ips)

import requests

@app.get(f'{APP_PREFIX}/api/ping/execute')
def API_ping_execute():
    if "ipAddress" in request.args.keys() and "count" in request.args.keys():
        ip = request.args['ipAddress']
        count = request.args['count']
        try:
            if ip is not None and len(ip) > 0 and count is not None and count.isnumeric():
                # PERF-08: Cap ping count to 20 to avoid monopolizing Gunicorn worker threads
                ping_count = min(max(int(count), 1), 20)
                result = ping(ip, count=ping_count, timeout=2, source=None)
                data = {
                    "address": result.address,
                    "is_alive": result.is_alive,
                    "min_rtt": result.min_rtt,
                    "avg_rtt": result.avg_rtt,
                    "max_rtt": result.max_rtt,
                    "package_sent": result.packets_sent,
                    "package_received": result.packets_received,
                    "package_loss": result.packet_loss,
                    "geo": None
                }
                try:
                    r = requests.get(f"https://ip-api.com/json/{result.address}?field=city", timeout=5)
                    data['geo'] = r.json()
                except Exception as e:
                    pass
                return ResponseObject(data=data)
            return ResponseObject(False, "Please specify an IP Address (v4/v6)")
        except Exception as exp:
            return ResponseObject(False, str(exp))
    return ResponseObject(False, "Please provide ipAddress and count")


@app.get(f'{APP_PREFIX}/api/traceroute/execute')
def API_traceroute_execute():
    if "ipAddress" in request.args.keys() and len(request.args.get("ipAddress")) > 0:
        ipAddress = request.args.get('ipAddress')
        try:
            # PERF-08: Cap max_hops to 20 to prevent hanging worker threads
            tracerouteResult = traceroute(ipAddress, timeout=1, max_hops=20)
            result = []
            for hop in tracerouteResult:
                if len(result) > 1:
                    skipped = False
                    for i in range(result[-1]["hop"] + 1, hop.distance):
                        result.append(
                            {
                                "hop": i,
                                "ip": "*",
                                "avg_rtt": "*",
                                "min_rtt": "*",
                                "max_rtt": "*"
                            }
                        )
                        skip = True
                    if skipped: continue
                result.append(
                    {
                        "hop": hop.distance,
                        "ip": hop.address,
                        "avg_rtt": hop.avg_rtt,
                        "min_rtt": hop.min_rtt,
                        "max_rtt": hop.max_rtt
                    })
            try:
                r = requests.post(f"https://ip-api.com/batch?fields=city,country,lat,lon,query",
                                  data=json.dumps([x['ip'] for x in result]), timeout=10)
                d = r.json()
                for i in range(len(result)):
                    result[i]['geo'] = d[i]

                return ResponseObject(data=result)

            except Exception as e:
                app.logger.error(f"Failed to gather the geolocation data: {e}")
                return ResponseObject(data=result, message="Failed to request IP address geolocation")
    
        except Exception as e:
            app.logger.error(f"Failed to execute the traceroute: {e}")
            return ResponseObject(data=[], message="Failed to traceroute the given parameter")
    else:
        return ResponseObject(False, "Please provide ipAddress")

@app.get(f'{APP_PREFIX}/api/getDashboardUpdate')
def API_getDashboardUpdate():
    # BUG-19: Upstream WGDashboard release check is disabled to prevent overwriting fork customizations
    return ResponseObject(message="You're on the latest version", data={"current_version": DashboardConfig.DashboardVersion})

'''
Sign Up
'''

@app.get(f'{APP_PREFIX}/api/isTotpEnabled')
def API_isTotpEnabled():
    return (
        ResponseObject(data=DashboardConfig.GetConfig("Account", "enable_totp")[1] and DashboardConfig.GetConfig("Account", "totp_verified")[1]))


@app.route(f'{APP_PREFIX}/api/Welcome_GetTotpLink', methods=["GET", "POST"])
def API_Welcome_GetTotpLink():
    if not DashboardConfig.GetConfig("Account", "totp_verified")[1]:
        DashboardConfig.SetConfig("Account", "totp_key", pyotp.random_base32(), True)
        return ResponseObject(
            data=pyotp.totp.TOTP(DashboardConfig.GetConfig("Account", "totp_key")[1]).provisioning_uri(
                issuer_name="WGDashboard"))
    return ResponseObject(False)


@app.post(f'{APP_PREFIX}/api/Welcome_VerifyTotpLink')
def API_Welcome_VerifyTotpLink():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    totp_key = DashboardConfig.GetConfig("Account", "totp_key")[1]
    totp_code = str(data.get('totp') or '').strip()
    is_valid = False
    try:
        is_valid = pyotp.TOTP(totp_key).verify(totp_code, valid_window=1)
    except Exception:
        is_valid = False
    if is_valid:
        DashboardConfig.SetConfig("Account", "totp_verified", "true")
        DashboardConfig.SetConfig("Account", "enable_totp", "true")
    return ResponseObject(is_valid)

@app.post(f'{APP_PREFIX}/api/Welcome_Finish')
def API_Welcome_Finish():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    is_welcome = DashboardConfig.GetConfig("Other", "welcome_session")[1] is True or str(DashboardConfig.GetConfig("Other", "welcome_session")[1]).strip().lower() == 'true'
    if is_welcome:
        if data.get("username", "") == "":
            return ResponseObject(False, "Username cannot be blank.", status_code=400)

        if data.get("newPassword", "") == "" or len(str(data.get("newPassword", ""))) < 8:
            return ResponseObject(False, "Password must be at least 8 characters", status_code=400)

        updateUsername, updateUsernameErr = DashboardConfig.SetConfig("Account", "username", data["username"])
        updatePassword, updatePasswordErr = DashboardConfig.SetConfig("Account", "password",
                                                                      {
                                                                          "newPassword": data["newPassword"],
                                                                          "repeatNewPassword": data.get("repeatNewPassword", ""),
                                                                          "currentPassword": "admin"
                                                                      })
        if not updateUsername or not updatePassword:
            return ResponseObject(False, f"{updateUsernameErr},{updatePasswordErr}".strip(","), status_code=400)

        DashboardConfig.SetConfig("Other", "welcome_session", False)
        return ResponseObject()
    else:
        return ResponseObject(False, "Welcome session is not active.", status_code=400)

class Locale:
    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.localePath = os.path.join(base_dir, 'static', 'locales')
        self.activeLanguages = {}
        supported_path = os.path.join(self.localePath, "supported_locales.json")
        if os.path.exists(supported_path):
            with open(supported_path, "r", encoding="utf-8") as f:
                self.activeLanguages = sorted(json.loads(''.join(f.readlines())), key=lambda x : x['lang_name'])
        
    def getLanguage(self) -> dict | None:
        currentLanguage = DashboardConfig.GetConfig("Server", "dashboard_language")[1]
        valid_ids = [x['lang_id'] for x in self.activeLanguages]
        if currentLanguage not in valid_ids or currentLanguage == "en":
            return None
        safe_path = os.path.join(self.localePath, f"{os.path.basename(currentLanguage)}.json")
        if os.path.exists(safe_path):
            with open(safe_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None
    
    def updateLanguage(self, lang_id):
        valid_ids = [x['lang_id'] for x in self.activeLanguages]
        if lang_id not in valid_ids:
            DashboardConfig.SetConfig("Server", "dashboard_language", "en-US")
        else:
            DashboardConfig.SetConfig("Server", "dashboard_language", lang_id)
        
Locale = Locale()

@app.get(f'{APP_PREFIX}/api/locale')
def API_Locale_CurrentLang():    
    return ResponseObject(data=Locale.getLanguage())

@app.get(f'{APP_PREFIX}/api/locale/available')
def API_Locale_Available():
    return ResponseObject(data=Locale.activeLanguages)
        
@app.post(f'{APP_PREFIX}/api/locale/update')
def API_Locale_Update():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    if 'lang_id' not in data.keys():
        return ResponseObject(False, "Please specify a lang_id", status_code=400)
    Locale.updateLanguage(data['lang_id'])
    return ResponseObject(data=Locale.getLanguage())

@app.get(f'{APP_PREFIX}/api/email/ready')
def API_Email_Ready():
    return ResponseObject(EmailSender.is_ready())

@app.post(f'{APP_PREFIX}/api/email/send')
def API_Email_Send():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    if "Receiver" not in data.keys() or "Subject" not in data.keys():
        return ResponseObject(False, "Please at least specify receiver and subject", status_code=400)
    body = str(data.get('Body') or '')
    subject = str(data.get('Subject') or '')
    download = None
    attachment_path = None
    attachmentName = ""
    try:
        if ("ConfigurationName" in data.keys() 
                and "Peer" in data.keys()):
            if data.get('ConfigurationName') in WireguardConfigurations.keys():
                configuration = WireguardConfigurations.get(data.get('ConfigurationName'))
                if configuration is not None:
                    fp, p = configuration.searchPeer(data.get('Peer'))
                    if fp:
                        try:
                            template = _jinja_env.from_string(body)
                            download = p.downloadPeer()
                            body = template.render(peer=p.toJson(), configurationFile=download)
                            subject = _jinja_env.from_string(subject).render(peer=p.toJson(), configurationFile=download)
                        except Exception as e:
                            return ResponseObject(False, f"Template rendering failed: {str(e)}", status_code=400)

                        if data.get('IncludeAttachment', False):
                            u = str(uuid4())
                            attachmentName = f'{u}.conf'
                            os.makedirs('./attachments', exist_ok=True)
                            attachment_path = os.path.join('./attachments', attachmentName)
                            with open(attachment_path, 'w', encoding='utf-8') as f:
                                f.write(download['file'])   
        
        s, m = EmailSender.send(data.get('Receiver'), subject, body,  
                                data.get('IncludeAttachment', False), (attachmentName if download and data.get('IncludeAttachment', False) else ''))
        return ResponseObject(s, m)
    finally:
        if attachment_path and os.path.exists(attachment_path):
            try:
                os.remove(attachment_path)
            except Exception:
                pass

@app.post(f'{APP_PREFIX}/api/email/preview')
def API_Email_PreviewBody():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    subject = str(data.get('Subject') or '')
    body = str(data.get('Body') or '')
    
    if ("ConfigurationName" not in data.keys() 
            or "Peer" not in data.keys() or data.get('ConfigurationName') not in WireguardConfigurations.keys()):
        return ResponseObject(False, "Please specify configuration and peer", status_code=400)
    
    configuration = WireguardConfigurations.get(data.get('ConfigurationName'))
    fp, p = configuration.searchPeer(data.get('Peer'))
    if not fp:
        return ResponseObject(False, "Peer does not exist")

    try:
        template = _jinja_env.from_string(body)
        download = p.downloadPeer()
        return ResponseObject(data={
            "Body": template.render(peer=p.toJson(), configurationFile=download),
            "Subject": _jinja_env.from_string(subject).render(peer=p.toJson(), configurationFile=download)
        })
    except Exception as e:
        return ResponseObject(False, message=str(e))

@app.get(f'{APP_PREFIX}/api/systemStatus')
def API_SystemStatus():
    return ResponseObject(data=SystemStatus)

@app.get(f'{APP_PREFIX}/api/protocolsEnabled')
def API_ProtocolsEnabled():
    return ResponseObject(data=ProtocolsEnabled())

'''
OIDC Controller
'''
@app.route(f'{APP_PREFIX}/api/oidc/toggle', methods=["GET", "POST"])
def API_OIDC_Toggle():
    data = request.get_json(silent=True) or request.args
    if not data.get('mode'):
        return ResponseObject(False, "Please provide mode")
    mode = data.get('mode')
    if mode == 'Client':
        DashboardConfig.SetConfig("OIDC", "client_enable", 
                                  not DashboardConfig.GetConfig("OIDC", "client_enable")[1])
    elif mode == 'Admin':
        DashboardConfig.SetConfig("OIDC", "admin_enable",
                                  not DashboardConfig.GetConfig("OIDC", "admin_enable")[1])
    else:
        return ResponseObject(False, "Mode does not exist")
    return ResponseObject()

@app.get(f'{APP_PREFIX}/api/oidc/status')
def API_OIDC_Status():
    data = request.args
    if not data.get('mode'):
        return ResponseObject(False, "Please provide mode")
    mode = data.get('mode')
    if mode == 'Client':
        return ResponseObject(data=DashboardConfig.GetConfig("OIDC", "client_enable")[1])
    elif mode == 'Admin':
        return ResponseObject(data=DashboardConfig.GetConfig("OIDC", "admin_enable")[1])
    return ResponseObject(False, "Mode does not exist")

'''
Client Controller
'''

@app.route(f'{APP_PREFIX}/api/clients/toggleStatus', methods=["GET", "POST"])
def API_Clients_ToggleStatus():
    DashboardConfig.SetConfig("Clients", "enable",
                              not DashboardConfig.GetConfig("Clients", "enable")[1])
    return ResponseObject(data=DashboardConfig.GetConfig("Clients", "enable")[1])


@app.get(f'{APP_PREFIX}/api/clients/allClients')
def API_Clients_AllClients():
    return ResponseObject(data=DashboardClients.GetAllClients())

@app.get(f'{APP_PREFIX}/api/clients/allClientsRaw')
def API_Clients_AllClientsRaw():
    return ResponseObject(data=DashboardClients.GetAllClientsRaw())

@app.post(f'{APP_PREFIX}/api/clients/assignClient')
def API_Clients_AssignClient():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    configurationName = data.get('ConfigurationName')
    id = data.get('Peer')
    client = data.get('ClientID')
    if not all([configurationName, id, client]):
        return ResponseObject(False, "Please provide all required fields", status_code=400)
    if not DashboardClients.GetClient(client):
        return ResponseObject(False, "Client does not exist", status_code=404)
    
    status, data = DashboardClients.AssignClient(configurationName, id, client)
    if not status:
        return ResponseObject(status, message="Client already assiged to this peer", status_code=400)
    
    return ResponseObject(data=data)

@app.post(f'{APP_PREFIX}/api/clients/unassignClient')
def API_Clients_UnassignClient():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    assignmentID = data.get('AssignmentID')
    if not assignmentID:
        return ResponseObject(False, "Please provide AssignmentID", status_code=400)
    return ResponseObject(status=DashboardClients.UnassignClient(assignmentID))

@app.get(f'{APP_PREFIX}/api/clients/assignedClients')
def API_Clients_AssignedClients():
    data = request.args
    configurationName = data.get('ConfigurationName')
    peerID = data.get('Peer')
    if not all([configurationName, peerID]):
        return ResponseObject(False, "Please provide all required fields", status_code=400)
    return ResponseObject(
        data=DashboardClients.GetAssignedPeerClients(configurationName, peerID))

@app.get(f'{APP_PREFIX}/api/clients/allConfigurationsPeers')
def API_Clients_AllConfigurationsPeers():
    c = {}
    for (key, val) in WireguardConfigurations.items():
        c[key] = list(map(lambda x : {
            "id": x.id,
            "name": x.name
        }, val.Peers))
    
    return ResponseObject(
        data=c
    )

@app.get(f'{APP_PREFIX}/api/clients/assignedPeers')
def API_Clients_AssignedPeers():
    data = request.args
    clientId = data.get("ClientID")
    if not clientId:
        return ResponseObject(False, "Please provide ClientID", status_code=400)
    if not DashboardClients.GetClient(clientId):
        return ResponseObject(False, "Client does not exist", status_code=404)
    d = DashboardClients.GetClientAssignedPeersGrouped(clientId)
    if d is None:
        return ResponseObject(False, "Client does not exist", status_code=404)
    return ResponseObject(data=d)

@app.post(f'{APP_PREFIX}/api/clients/generatePasswordResetLink')
def API_Clients_GeneratePasswordResetLink():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    clientId = data.get("ClientID")
    if not clientId:
        return ResponseObject(False, "Please provide ClientID", status_code=400)
    if not DashboardClients.GetClient(clientId):
        return ResponseObject(False, "Client does not exist", status_code=404)
    
    token = DashboardClients.GenerateClientPasswordResetToken(clientId)
    if token:
        return ResponseObject(data=token)
    return ResponseObject(False, "Failed to generate link")

@app.post(f'{APP_PREFIX}/api/clients/updateProfileName')
def API_Clients_UpdateProfile():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    clientId = data.get("ClientID")
    if not clientId:
        return ResponseObject(False, "Please provide ClientID", status_code=400)
    if not DashboardClients.GetClient(clientId):
        return ResponseObject(False, "Client does not exist", status_code=404)
    
    value = data.get('Name')
    return ResponseObject(status=DashboardClients.UpdateClientProfile(clientId, value))

@app.post(f'{APP_PREFIX}/api/clients/deleteClient')
def API_Clients_DeleteClient():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    clientId = data.get("ClientID")
    if not clientId:
        return ResponseObject(False, "Please provide ClientID", status_code=400)
    if not DashboardClients.GetClient(clientId):
        return ResponseObject(False, "Client does not exist", status_code=404)
    return ResponseObject(status=DashboardClients.DeleteClient(clientId))   

@app.get(f'{APP_PREFIX}/api/webHooks/getWebHooks')
def API_WebHooks_GetWebHooks():
    return ResponseObject(data=DashboardWebHooks.GetWebHooks())

@app.get(f'{APP_PREFIX}/api/webHooks/createWebHook')
def API_WebHooks_createWebHook():
    return ResponseObject(data=DashboardWebHooks.CreateWebHook().model_dump(
        exclude={'CreationDate'}
    ))

@app.post(f'{APP_PREFIX}/api/webHooks/updateWebHook')
def API_WebHooks_UpdateWebHook():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    status, msg = DashboardWebHooks.UpdateWebHook(data)
    return ResponseObject(status, msg)

@app.post(f'{APP_PREFIX}/api/webHooks/deleteWebHook')
def API_WebHooks_DeleteWebHook():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)
    status, msg = DashboardWebHooks.DeleteWebHook(data)
    return ResponseObject(status, msg)

@app.get(f'{APP_PREFIX}/api/webHooks/getWebHookSessions')
def API_WebHooks_GetWebHookSessions():
    webhookID = request.args.get('WebHookID')
    if not webhookID:
        return ResponseObject(False, "Please provide WebHookID")
    
    webHook = DashboardWebHooks.SearchWebHookByID(webhookID)
    if not webHook:
        return ResponseObject(False, "Webhook does not exist")
    
    return ResponseObject(data=DashboardWebHooks.GetWebHookSessions(webHook))
    

'''
API v2 Routes (OxBot & Automation Integration)
'''

@app.get(f'{APP_PREFIX}/api/v2/peer')
def API_v2_getPeer():
    config_name = request.args.get('configurationName') or request.args.get('configuration')
    if not config_name or config_name not in WireguardConfigurations:
        return ResponseObject(False, "Valid configurationName is required", status_code=400)
    config = WireguardConfigurations[config_name]
    
    peer_id = request.args.get('id')
    peer_name = request.args.get('name')
    if not peer_id and not peer_name:
        return ResponseObject(False, "Either 'id' (public key) or 'name' is required", status_code=400)
    
    found_peer = None
    is_restricted = False
    
    # 1. Search by id
    if peer_id:
        p_clean = peer_id.strip().replace(' ', '+')
        found, p = config.searchPeer(p_clean)
        if found:
            found_peer = p
        else:
            for rp in (config.RestrictedPeers or []):
                if rp.id == p_clean or rp.id == peer_id:
                    found_peer = rp
                    is_restricted = True
                    break

    # 2. Fallback search by name
    if not found_peer and peer_name:
        for p in (config.Peers or []):
            if p.name == peer_name:
                found_peer = p
                break
        if not found_peer:
            for rp in (config.RestrictedPeers or []):
                if rp.name == peer_name:
                    found_peer = rp
                    is_restricted = True
                    break

    if not found_peer:
        return ResponseObject(False, "Peer not found", status_code=404)

    cumu_recv = float(getattr(found_peer, 'cumu_receive', 0.0) or 0.0)
    cur_recv = float(getattr(found_peer, 'total_receive', 0.0) or 0.0)
    cumu_sent = float(getattr(found_peer, 'cumu_sent', 0.0) or 0.0)
    cur_sent = float(getattr(found_peer, 'total_sent', 0.0) or 0.0)
    rx_gb = cumu_recv + cur_recv
    tx_gb = cumu_sent + cur_sent
    total_gb = rx_gb + tx_gb

    jobs = [j.toJson() for j in AllPeerJobs.searchJob(config_name, found_peer.id)]
    
    share_links = AllPeerShareLinks.getLink(config_name, found_peer.id)
    share_link = share_links[0] if share_links else None

    return ResponseObject(True, data={
        "id": found_peer.id,
        "name": getattr(found_peer, 'name', '') or '',
        "allowed_ip": getattr(found_peer, 'allowed_ip', '') or '',
        "restricted": is_restricted or bool(getattr(found_peer, 'restricted', False)),
        "restricted_reason": getattr(found_peer, 'restricted_reason', '') or '',
        "status": getattr(found_peer, 'status', 'stopped' if is_restricted else 'running'),
        "endpoint": getattr(found_peer, 'endpoint', '') or '',
        "latest_handshake": getattr(found_peer, 'latest_handshake', '') or '',
        "usage": {
            "rx_gb": round(rx_gb, 4),
            "tx_gb": round(tx_gb, 4),
            "total_gb": round(total_gb, 4)
        },
        "jobs": jobs,
        "share_link": share_link
    })

@app.get(f'{APP_PREFIX}/api/v2/peers/usage')
def API_v2_getPeersUsage():
    config_name = request.args.get('configurationName') or request.args.get('configuration')
    if not config_name or config_name not in WireguardConfigurations:
        return ResponseObject(False, "Valid configurationName is required", status_code=400)
    config = WireguardConfigurations[config_name]

    restricted_dict = {rp.id: rp for rp in (config.getRestrictedPeersList() or [])}
    active_peers = [p for p in (config.Peers or []) if p.id not in restricted_dict and not getattr(p, 'restricted', False)]

    usage_list = []
    for p in active_peers:
        c_rx = float(getattr(p, 'cumu_receive', 0.0) or 0.0) + float(getattr(p, 'total_receive', 0.0) or 0.0)
        c_tx = float(getattr(p, 'cumu_sent', 0.0) or 0.0) + float(getattr(p, 'total_sent', 0.0) or 0.0)
        hs = getattr(p, 'latest_handshake', '') or ''
        usage_list.append([
            p.id,
            getattr(p, 'name', '') or '',
            False,
            round(c_rx + c_tx, 4),
            round(c_rx, 4),
            round(c_tx, 4),
            hs
        ])

    for r_id, rp in restricted_dict.items():
        c_rx = float(getattr(rp, 'cumu_receive', 0.0) or 0.0) + float(getattr(rp, 'total_receive', 0.0) or 0.0)
        c_tx = float(getattr(rp, 'cumu_sent', 0.0) or 0.0) + float(getattr(rp, 'total_sent', 0.0) or 0.0)
        hs = getattr(rp, 'latest_handshake', '') or ''
        usage_list.append([
            r_id,
            getattr(rp, 'name', '') or '',
            True,
            round(c_rx + c_tx, 4),
            round(c_rx, 4),
            round(c_tx, 4),
            hs
        ])

    payload_str = json.dumps(usage_list, separators=(',', ':'))
    etag = f'"{hashlib.md5(payload_str.encode("utf-8")).hexdigest()}"'
    
    if request.headers.get('If-None-Match') == etag:
        res = Response("", status=304)
        res.headers['ETag'] = etag
        return res

    resp = Response(
        json.dumps({"status": True, "message": "Success", "data": usage_list}),
        status=200,
        mimetype="application/json"
    )
    resp.headers['ETag'] = etag
    return resp

@app.post(f'{APP_PREFIX}/api/v2/peers')
def API_v2_createPeer():
    idempotency_key = request.headers.get('Idempotency-Key')
    if idempotency_key:
        cached = _get_idempotent_response(idempotency_key)
        if cached is not None:
            return ResponseObject(True, "Success (idempotent replay)", data=cached)

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)

    config_name = data.get('configurationName') or data.get('configuration')
    if not config_name or config_name not in WireguardConfigurations:
        return ResponseObject(False, "Valid configurationName is required", status_code=400)

    name = data.get('name', '').strip()
    if not name:
        return ResponseObject(False, "Peer 'name' is required", status_code=400)

    config = WireguardConfigurations[config_name]
    with config.lock:
        allowed_ips = data.get('allowed_ips')
        if not allowed_ips:
            ipStatus, availableIps = config.getAvailableIP(-1)
            if not ipStatus or not availableIps:
                return ResponseObject(False, "No available IP addresses in subnet", status_code=400)
            allocated_ip = None
            for subnet in availableIps.keys():
                for ip in availableIps[subnet]:
                    allocated_ip = ip
                    break
                if allocated_ip:
                    break
            if not allocated_ip:
                return ResponseObject(False, "No available IP addresses in subnet", status_code=400)
            allowed_ips = [allocated_ip]
        elif isinstance(allowed_ips, str):
            allowed_ips = [allowed_ips]

        private_key = data.get('private_key', '')
        public_key = data.get('public_key', '')
        if not public_key:
            if not private_key:
                status, priv = GenerateWireguardPrivateKey()
                private_key = priv
                status, pub = GenerateWireguardPublicKey(private_key)
                public_key = pub
            else:
                status, pub = GenerateWireguardPublicKey(private_key)
                public_key = pub

        dns = data.get('DNS', DashboardConfig.GetConfig("Peers", "peer_global_DNS")[1])
        mtu = data.get('mtu', 0)
        keepalive = data.get('keepalive', 0)
        notes = data.get('notes', '')
        preshared_key = data.get('preshared_key', '')
        endpoint_allowed_ip = data.get('endpoint_allowed_ip', DashboardConfig.GetConfig("Peers", "peer_endpoint_allowed_ip")[1])

        peer_dict = {
            "name": name,
            "id": public_key,
            "private_key": private_key,
            "allowed_ip": ','.join(allowed_ips),
            "DNS": dns,
            "endpoint_allowed_ip": endpoint_allowed_ip,
            "mtu": mtu,
            "keepalive": keepalive,
            "notes": notes,
            "preshared_key": preshared_key
        }

        status, added_peers, msg = config.addPeers([peer_dict])
        if not status or not added_peers:
            return ResponseObject(False, f"Failed to create peer: {msg}", status_code=400)

        created_peer = added_peers[0]

        limits = data.get('limits') or {}
        jobs_created = []
        if isinstance(limits, dict):
            data_limit_gb = limits.get('data_limit_gb')
            if data_limit_gb is not None:
                try:
                    val_num = float(data_limit_gb)
                    if val_num > 0:
                        job_id = str(uuid.uuid4())
                        pj = PeerJob(
                            job_id, config_name, public_key,
                            "total_data", ">=", str(val_num),
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            None, "restrict"
                        )
                        s, res = AllPeerJobs.saveJob(pj)
                        if s:
                            jobs_created.append(pj.toJson())
                except (ValueError, TypeError):
                    pass

            expire_at = limits.get('expire_at')
            if expire_at:
                try:
                    _parse_job_datetime(str(expire_at))
                    job_id = str(uuid.uuid4())
                    pj = PeerJob(
                        job_id, config_name, public_key,
                        "date", ">=", str(expire_at),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        None, "restrict"
                    )
                    s, res = AllPeerJobs.saveJob(pj)
                    if s:
                        jobs_created.append(pj.toJson())
                except (ValueError, TypeError):
                    pass

        dl = created_peer.downloadPeer()
        config_file_text = dl.get('file', '')
        qr_b64 = None
        if HAS_QRCODE and config_file_text:
            try:
                qr = qrcode.QRCode(box_size=10, border=2)
                qr.add_data(config_file_text)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                qr_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
            except Exception as e:
                app.logger.error(f"Error generating QR code: {e}")

        result_data = {
            "peer": created_peer.toJson(),
            "config_file": config_file_text,
            "qr_png_base64": qr_b64,
            "jobs": jobs_created
        }

        if idempotency_key:
            _store_idempotent_response(idempotency_key, result_data)

        return ResponseObject(True, "Peer created successfully", data=result_data)

@app.post(f'{APP_PREFIX}/api/v2/peer/renew')
def API_v2_renewPeer():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)

    config_name = data.get('configurationName') or data.get('configuration')
    peer_id = data.get('id')
    if not config_name or config_name not in WireguardConfigurations:
        return ResponseObject(False, "Valid configurationName is required", status_code=400)
    if not peer_id:
        return ResponseObject(False, "Peer ID ('id') is required", status_code=400)

    config = WireguardConfigurations[config_name]
    with config.lock:
        p_clean = peer_id.strip().replace(' ', '+')
        found, peer = config.searchPeer(p_clean)
        is_restricted = False
        if not found:
            for rp in (config.RestrictedPeers or []):
                if rp.id == p_clean or rp.id == peer_id:
                    found, peer = True, rp
                    is_restricted = True
                    break

        if not found:
            return ResponseObject(False, "Peer not found", status_code=404)

        reset_usage = data.get('reset_usage', True)
        if reset_usage:
            peer.resetDataUsage("total")
            try:
                config.DashboardWebHooks.RunWebHook("peer_usage_reset", {
                    "configuration": config_name,
                    "peer_id": peer.id,
                    "type": "total"
                })
            except Exception:
                pass

        existing_jobs = AllPeerJobs.searchJob(config_name, peer.id)
        for ej in existing_jobs:
            if ej.Field in ("total_data", "total_receive", "total_sent", "date"):
                AllPeerJobs.deleteJob(ej)

        data_limit_gb = data.get('data_limit_gb')
        if data_limit_gb is not None:
            try:
                val_num = float(data_limit_gb)
                if val_num > 0:
                    pj = PeerJob(
                        str(uuid.uuid4()), config_name, peer.id,
                        "total_data", ">=", str(val_num),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        None, "restrict"
                    )
                    AllPeerJobs.saveJob(pj)
            except (ValueError, TypeError):
                pass

        expire_at = data.get('expire_at')
        if expire_at:
            try:
                _parse_job_datetime(str(expire_at))
                pj = PeerJob(
                    str(uuid.uuid4()), config_name, peer.id,
                    "date", ">=", str(expire_at),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    None, "restrict"
                )
                AllPeerJobs.saveJob(pj)
            except (ValueError, TypeError):
                pass

        if is_restricted:
            config.allowAccessPeers([peer.id], force=True)

        return ResponseObject(True, "Peer renewed successfully")

@app.post(f'{APP_PREFIX}/api/v2/peer/limits')
@app.post(f'{APP_PREFIX}/api/v2/peer/limits/add')
def API_v2_peerLimits():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)

    config_name = data.get('configurationName') or data.get('configuration')
    peer_id = data.get('id')
    if not config_name or config_name not in WireguardConfigurations:
        return ResponseObject(False, "Valid configurationName is required", status_code=400)
    if not peer_id:
        return ResponseObject(False, "Peer ID ('id') is required", status_code=400)

    config = WireguardConfigurations[config_name]
    with config.lock:
        p_clean = peer_id.strip().replace(' ', '+')
        found, peer = config.searchPeer(p_clean)
        is_restricted = False
        if not found:
            for rp in (config.RestrictedPeers or []):
                if rp.id == p_clean or rp.id == peer_id:
                    found, peer = True, rp
                    is_restricted = True
                    break

        if not found:
            return ResponseObject(False, "Peer not found", status_code=404)

        current_usage = float(getattr(peer, 'cumu_data', 0.0) or 0.0) + float(getattr(peer, 'total_data', 0.0) or 0.0)
        existing_jobs = AllPeerJobs.searchJob(config_name, peer.id)
        existing_vol_job = next((j for j in existing_jobs if j.Field == "total_data"), None)
        existing_date_job = next((j for j in existing_jobs if j.Field == "date"), None)

        unrestrict_needed = False

        add_data_gb = data.get('add_data_gb')
        if add_data_gb is not None:
            try:
                add_vol = float(add_data_gb)
                if add_vol > 0:
                    base_limit = float(existing_vol_job.Value) if existing_vol_job else current_usage
                    new_limit = max(base_limit, current_usage) + add_vol
                    if existing_vol_job:
                        AllPeerJobs.deleteJob(existing_vol_job)
                    pj = PeerJob(
                        str(uuid.uuid4()), config_name, peer.id,
                        "total_data", ">=", str(round(new_limit, 4)),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        None, "restrict"
                    )
                    AllPeerJobs.saveJob(pj)
                    if is_restricted and getattr(peer, 'restricted_reason', '') == "Volume Limit Reached":
                        unrestrict_needed = True
            except (ValueError, TypeError):
                pass

        data_limit_gb = data.get('data_limit_gb')
        if data_limit_gb is not None:
            try:
                set_limit = float(data_limit_gb)
                if set_limit > 0:
                    if existing_vol_job:
                        AllPeerJobs.deleteJob(existing_vol_job)
                    pj = PeerJob(
                        str(uuid.uuid4()), config_name, peer.id,
                        "total_data", ">=", str(round(set_limit, 4)),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        None, "restrict"
                    )
                    AllPeerJobs.saveJob(pj)
                    if set_limit > current_usage and is_restricted and getattr(peer, 'restricted_reason', '') == "Volume Limit Reached":
                        unrestrict_needed = True
            except (ValueError, TypeError):
                pass

        add_days = data.get('add_days')
        if add_days is not None:
            try:
                days_num = float(add_days)
                if days_num > 0:
                    base_dt = datetime.now(timezone.utc)
                    if existing_date_job:
                        try:
                            edt, _ = _parse_job_datetime(existing_date_job.Value)
                            if edt > base_dt:
                                base_dt = edt
                        except Exception:
                            pass
                    new_dt = base_dt + timedelta(days=days_num)
                    new_dt_str = new_dt.strftime("%Y-%m-%d %H:%M:%S")
                    if existing_date_job:
                        AllPeerJobs.deleteJob(existing_date_job)
                    pj = PeerJob(
                        str(uuid.uuid4()), config_name, peer.id,
                        "date", ">=", new_dt_str,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        None, "restrict"
                    )
                    AllPeerJobs.saveJob(pj)
                    if is_restricted and getattr(peer, 'restricted_reason', '') == "Time Limit Reached":
                        unrestrict_needed = True
            except (ValueError, TypeError):
                pass

        expire_at = data.get('expire_at')
        if expire_at:
            try:
                _parse_job_datetime(str(expire_at))
                if existing_date_job:
                    AllPeerJobs.deleteJob(existing_date_job)
                pj = PeerJob(
                    str(uuid.uuid4()), config_name, peer.id,
                    "date", ">=", str(expire_at),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    None, "restrict"
                )
                AllPeerJobs.saveJob(pj)
                if is_restricted and getattr(peer, 'restricted_reason', '') == "Time Limit Reached":
                    unrestrict_needed = True
            except (ValueError, TypeError):
                pass

        if unrestrict_needed:
            config.allowAccessPeers([peer.id], force=True)

        return ResponseObject(True, "Limits updated successfully")

@app.post(f'{APP_PREFIX}/api/v2/peers/bulk')
def API_v2_peersBulk():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return ResponseObject(False, "Invalid JSON body", status_code=400)

    config_name = data.get('configurationName') or data.get('configuration')
    action = data.get('action')
    ids = data.get('ids')

    if not config_name or config_name not in WireguardConfigurations:
        return ResponseObject(False, "Valid configurationName is required", status_code=400)
    if not action or action not in ('restrict', 'allow', 'delete'):
        return ResponseObject(False, "Valid 'action' (restrict, allow, delete) is required", status_code=400)
    if not isinstance(ids, list) or len(ids) == 0:
        return ResponseObject(False, "List of peer 'ids' is required", status_code=400)

    config = WireguardConfigurations[config_name]
    with config.lock:
        if action == 'restrict':
            reason = data.get('reason')
            status, msg = config.restrictPeers(ids, reason=reason)
            return ResponseObject(status, msg, status_code=200 if status else 400)
        elif action == 'allow':
            force = bool(data.get('force', False))
            status, msg = config.allowAccessPeers(ids, force=force)
            return ResponseObject(status, msg, status_code=200 if status else 400)
        elif action == 'delete':
            status, msg = config.deletePeers(ids, AllPeerJobs, AllPeerShareLinks)
            if not status:
                return ResponseObject(False, msg, status_code=404 if "No peer(s) to delete found" in msg else 400)
            for p in ids:
                assignments = DashboardClients.DashboardClientsPeerAssignment.GetAssignedClients(config_name, p)
                for c in assignments:
                    DashboardClients.DashboardClientsPeerAssignment.UnassignClients(c.AssignmentID)
            return ResponseObject(status, msg)

'''
Index Page
'''

@app.get(f'{APP_PREFIX}/')
def index():
    return render_template('index.html', APP_PREFIX=APP_PREFIX)

if __name__ == "__main__":
    startThreads()
    DashboardPlugins.startThreads()
    app.run(host=app_ip, debug=False, port=app_port)
