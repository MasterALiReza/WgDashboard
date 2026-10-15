import os
import re
import json
import zipfile
import hashlib
import shutil
import uuid
import threading
import logging
from datetime import datetime, timedelta
import subprocess
import tempfile
import sqlalchemy as db
from sqlalchemy.schema import CreateTable
from .DatabaseConnection import ConnectionString
from .DashboardConfig import DashboardConfig

_logger = logging.getLogger("WGDashboard.GlobalBackup")

def sync_interface_runtime(interface: str, protocol: str, conf_path: str) -> tuple[bool, str]:
    """
    Safely synchronizes the running WireGuard or AmneziaWG kernel runtime with the .conf file on disk
    using non-disruptive syncconf. Active connections remain alive without dropping handshakes.
    """
    try:
        check_cmd = [protocol, "show", interface]
        subprocess.check_output(check_cmd, stderr=subprocess.STDOUT, timeout=10)
    except Exception:
        return True, f"Interface {interface} is not active in kernel, skipping syncconf."

    if not os.path.exists(conf_path):
        return False, f"Configuration file {conf_path} does not exist."

    tmp_stripped = None
    try:
        strip_cmd = [f"{protocol}-quick", "strip", conf_path]
        stripped_content = subprocess.check_output(strip_cmd, stderr=subprocess.STDOUT, timeout=15)

        with tempfile.NamedTemporaryFile(mode="wb", delete=False, prefix="wgd_sync_", suffix=".conf") as tf:
            tf.write(stripped_content)
            tmp_stripped = tf.name

        sync_cmd = [protocol, "syncconf", interface, tmp_stripped]
        subprocess.check_output(sync_cmd, stderr=subprocess.STDOUT, timeout=15)
        return True, f"Interface {interface} kernel synchronized successfully. Note: Interface-level changes (Address/MTU/PostUp) require restart."
    except Exception as e:
        return False, f"Failed to sync interface {interface} runtime: {e}"
    finally:
        if tmp_stripped and os.path.exists(tmp_stripped):
            try:
                os.remove(tmp_stripped)
            except Exception:
                pass

GLOBAL_BACKUP_DIR = os.getenv('GLOBAL_BACKUP_PATH', os.path.join(os.getenv('CONFIGURATION_PATH', '.'), 'GlobalBackups'))

# ─── Restore Job Tracking ────────────────────────────────────────────────────
# Thread-safe dict: { job_id: {pct, step, done, error, created_at} }
_restore_jobs: dict = {}
_restore_jobs_lock = threading.RLock()

# ─── Backup Job Tracking ─────────────────────────────────────────────────────
# Thread-safe dict: { job_id: {pct, step, done, error, created_at} }
_backup_jobs: dict = {}
_backup_jobs_lock = threading.RLock()

# Global state to prevent concurrent backup executions
_is_backup_running = False
_is_backup_running_lock = threading.RLock()

JOB_TTL_SECONDS = 300  # پاکسازی job ها بعد از ۵ دقیقه از اتمام


def _report_progress(job_id: str | None, pct: int, step: str) -> None:
    """Thread-safe progress reporter for restore jobs."""
    if not job_id:
        return
    with _restore_jobs_lock:
        if job_id in _restore_jobs:
            _restore_jobs[job_id].update({"pct": pct, "step": step})

def _report_backup_progress(job_id: str | None, pct: int, step: str) -> None:
    """Thread-safe progress reporter for backup jobs."""
    if not job_id:
        return
    with _backup_jobs_lock:
        if job_id in _backup_jobs:
            _backup_jobs[job_id].update({"pct": pct, "step": step})


def _cleanup_expired_jobs() -> None:
    """Remove restore jobs that finished more than JOB_TTL_SECONDS ago."""
    cutoff = datetime.utcnow() - timedelta(seconds=JOB_TTL_SECONDS)
    with _restore_jobs_lock:
        expired = [
            jid for jid, info in _restore_jobs.items()
            if info.get("done") and info.get("finished_at", datetime.utcnow()) < cutoff
        ]
        for jid in expired:
            del _restore_jobs[jid]

    with _backup_jobs_lock:
        expired_backup = [
            jid for jid, info in _backup_jobs.items()
            if info.get("done") and info.get("finished_at", datetime.utcnow()) < cutoff
        ]
        for jid in expired_backup:
            del _backup_jobs[jid]

def calculate_sha256(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def split_sql_statements(sql_text: str) -> list[str]:
    """
    Splits SQL statements respecting single quotes, double quotes, dollar quotes, escape characters,
    and single-line comments so semicolons inside string literals (e.g. PostUp) don't break.
    """
    statements = []
    current = []
    in_single_quote = False
    in_double_quote = False
    in_dollar_quote = False
    escape = False

    i = 0
    n = len(sql_text)
    while i < n:
        char = sql_text[i]
        
        # Check single-line comment (-- ...)
        if not in_single_quote and not in_double_quote and not in_dollar_quote and char == '-' and i + 1 < n and sql_text[i+1] == '-':
            while i < n and sql_text[i] != '\n':
                i += 1
            continue

        if escape:
            current.append(char)
            escape = False
            i += 1
            continue

        if char == '\\':
            current.append(char)
            escape = True
            i += 1
            continue

        if char == '$' and not in_single_quote and not in_double_quote and i + 1 < n and sql_text[i+1] == '$':
            in_dollar_quote = not in_dollar_quote
            current.append("$$")
            i += 2
            continue

        if not in_dollar_quote:
            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
                current.append(char)
                i += 1
                continue

            if char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
                current.append(char)
                i += 1
                continue

        if char == ';' and not in_single_quote and not in_double_quote and not in_dollar_quote:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue

        current.append(char)
        i += 1

    last_stmt = "".join(current).strip()
    if last_stmt:
        statements.append(last_stmt)
    return statements

def dump_database_to_sql(db_name: str, dump_path: str = None) -> str:
    try:
        cn = ConnectionString(db_name)
        # Use sqlite3.iterdump if it's a SQLite database for much better performance
        if cn.startswith('sqlite:///'):
            import sqlite3
            db_path = cn.split('sqlite:///')[1].split('?')[0]
            with sqlite3.connect(db_path, timeout=30.0) as con:
                # Flush WAL to main database before dumping (GB-07)
                try:
                    con.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                except Exception:
                    pass
                if dump_path:
                    with open(dump_path, 'w', encoding='utf-8') as f:
                        for line in con.iterdump():
                            f.write(line + "\n")
                    return ""
                else:
                    statements = [line for line in con.iterdump()]
                    return "\n".join(statements)

        # Fallback to SQLAlchemy for PostgreSQL/MySQL
        engine = db.create_engine(cn)
        metadata = db.MetaData()
        metadata.reflect(bind=engine)
        
        if dump_path:
            f = open(dump_path, 'w', encoding='utf-8')
        else:
            statements = []
            
        def write_stmt(stmt):
            if dump_path:
                f.write(stmt + "\n")
            else:
                statements.append(stmt)

        # Dump DDL
        for table in metadata.sorted_tables:
            ddl = str(CreateTable(table).compile(engine)).strip()
            write_stmt(f"{ddl};")

        # Dump DML in chunks
        with engine.connect() as conn:
            for table in metadata.sorted_tables:
                result = conn.execute(table.select())
                while True:
                    rows = result.fetchmany(5000)
                    if not rows:
                        break
                    for row in rows:
                        insert_stmt = table.insert().values(dict(row._mapping))
                        compiled = str(insert_stmt.compile(compile_kwargs={"literal_binds": True})).strip()
                        write_stmt(f"{compiled};")

        engine.dispose()
        if dump_path:
            f.close()
            return ""
        return "\n".join(statements)
    except Exception as e:
        return f"-- Error dumping database {db_name}: {str(e)}\n"

def restore_database_from_sql(db_name: str, sql_content: str) -> bool:
    if not sql_content or not sql_content.strip():
        return True
    try:
        cn_str = ConnectionString(db_name)
        # Native execution for SQLite with isolated tmp file and integrity check (GB-01, GB-02)
        if cn_str.startswith('sqlite:///'):
            import sqlite3
            db_path = cn_str.split('sqlite:///')[1].split('?')[0]
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

            tmp_db = os.path.abspath(db_path + f".restore_{uuid.uuid4().hex}.tmp")
            try:
                with sqlite3.connect(tmp_db, timeout=30.0) as con:
                    con.executescript(sql_content)
                    cursor = con.cursor()
                    cursor.execute("PRAGMA integrity_check;")
                    rows = cursor.fetchall()
                    if not rows or rows[0][0] != "ok":
                        _logger.error(f"Integrity check failed for restored SQLite database {db_name}: {rows}")
                        if os.path.exists(tmp_db):
                            os.remove(tmp_db)
                        return False
                    con.execute("PRAGMA wal_checkpoint(TRUNCATE);")

                for ext in ["-wal", "-shm"]:
                    p = tmp_db + ext
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass

                for ext in ["-wal", "-shm"]:
                    p = db_path + ext
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass

                # PERF-04 / GB-01: Dispose active connection pools so file handle is free for replacement
                try:
                    from .DatabaseConnection import dispose_all
                    dispose_all()
                except Exception:
                    pass

                try:
                    os.replace(tmp_db, db_path)
                except (PermissionError, OSError) as e:
                    _logger.warning(f"os.replace failed ({e}), falling back to file copy for {db_path}")
                    with open(tmp_db, 'rb') as f_src, open(db_path, 'wb') as f_dst:
                        shutil.copyfileobj(f_src, f_dst)
                    if os.path.exists(tmp_db):
                        try:
                            os.remove(tmp_db)
                        except Exception:
                            pass

                # Clear self-healing cache for this db
                try:
                    from .DatabaseConnection import _healed_on_boot
                    _healed_on_boot.discard(db_path)
                except Exception:
                    pass

                return True
            except Exception as e:
                _logger.error(f"Error restoring SQLite database {db_name}: {e}")
                if os.path.exists(tmp_db):
                    try:
                        os.remove(tmp_db)
                    except Exception:
                        pass
                return False

        # Fallback for PostgreSQL / MySQL (GB-08)
        engine = db.create_engine(cn_str)
        metadata = db.MetaData()
        try:
            metadata.reflect(bind=engine)
            with engine.begin() as conn:
                for table in reversed(metadata.sorted_tables):
                    conn.execute(db.text(f'DROP TABLE IF EXISTS "{table.name}"'))
        except Exception as e:
            _logger.warning(f"Warning dropping tables for {db_name}: {e}")

        statements = split_sql_statements(sql_content)
        with engine.begin() as conn:
            for stmt in statements:
                if stmt and not stmt.startswith("--"):
                    conn.execute(db.text(stmt))
        engine.dispose()
        return True
    except Exception as e:
        _logger.error(f"Failed to restore database {db_name}: {e}")
        return False

class GlobalBackupManager:
    @staticmethod
    def get_backup_dir() -> str:
        backup_dir = os.getenv('GLOBAL_BACKUP_PATH', os.path.join(os.getenv('CONFIGURATION_PATH', '.'), 'GlobalBackups'))
        os.makedirs(backup_dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(backup_dir, 0o700)
        except Exception:
            pass
        return backup_dir

    @staticmethod
    def create_global_backup(label: str = "", include_logs: bool = True, include_existing_backups: bool = False, job_id: str | None = None) -> tuple[bool, str | dict]:
        try:
            backup_dir = GlobalBackupManager.get_backup_dir()
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"GlobalBackup_{timestamp_str}"
            if label:
                clean_label = re.sub(r'[^a-zA-Z0-9_\-]', '', label)
                if clean_label:
                    backup_name += f"_{clean_label}"
            
            temp_dir = os.path.join(backup_dir, f"tmp_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)
            
            manifest_files = []
            interfaces_found = []

            # 1. Config files
            _report_backup_progress(job_id, 10, "gathering_configs")
            config_path = os.getenv('CONFIGURATION_PATH', '.')
            ini_file = os.path.join(config_path, 'wg-dashboard.ini')
            if os.path.exists(ini_file):
                shutil.copy(ini_file, os.path.join(temp_dir, 'wg-dashboard.ini'))
                manifest_files.append({
                    "path": "wg-dashboard.ini",
                    "sha256": calculate_sha256(ini_file),
                    "size": os.path.getsize(ini_file)
                })

            oidc_file = os.path.join(config_path, 'wg-dashboard-oidc-providers.json')
            if os.path.exists(oidc_file):
                shutil.copy(oidc_file, os.path.join(temp_dir, 'wg-dashboard-oidc-providers.json'))
                manifest_files.append({
                    "path": "wg-dashboard-oidc-providers.json",
                    "sha256": calculate_sha256(oidc_file),
                    "size": os.path.getsize(oidc_file)
                })

            for opt_file in ['certbot.ini', 'gunicorn.conf.py']:
                p = os.path.join(config_path, opt_file)
                if os.path.exists(p):
                    shutil.copy(p, os.path.join(temp_dir, opt_file))
                    manifest_files.append({
                        "path": opt_file,
                        "sha256": calculate_sha256(p),
                        "size": os.path.getsize(p)
                    })

            # Plugins directory
            plugins_dir = os.path.join(config_path, 'plugins')
            if os.path.exists(plugins_dir) and os.path.isdir(plugins_dir):
                target_plugins = os.path.join(temp_dir, 'plugins')
                shutil.copytree(plugins_dir, target_plugins, dirs_exist_ok=True)
                for root, dirs, files in os.walk(target_plugins):
                    for file in files:
                        p_full = os.path.join(root, file)
                        p_rel = os.path.relpath(p_full, temp_dir).replace('\\', '/')
                        manifest_files.append({
                            "path": p_rel,
                            "sha256": calculate_sha256(p_full),
                            "size": os.path.getsize(p_full)
                        })

            # 2. WireGuard interface configs (.conf)
            _report_backup_progress(job_id, 30, "backing_up_interfaces")
            configs_target = os.path.join(temp_dir, 'configs')
            os.makedirs(configs_target, exist_ok=True)
            
            wg_conf_path = "/etc/wireguard"
            awg_conf_path = "/etc/amnezia/amneziawg"
            if os.path.exists(ini_file):
                try:
                    import configparser
                    config = configparser.ConfigParser()
                    config.read(ini_file)
                    if 'Server' in config:
                        if 'wg_conf_path' in config['Server']:
                            wg_conf_path = config['Server']['wg_conf_path']
                        if 'awg_conf_path' in config['Server']:
                            awg_conf_path = config['Server']['awg_conf_path']
                except Exception:
                    pass
            
            search_paths = [
                ("wireguard", wg_conf_path),
                ("amnezia", awg_conf_path)
            ]
            for conf_type, sp in search_paths:
                if os.path.exists(sp) and os.path.isdir(sp):
                    type_target = os.path.join(configs_target, conf_type)
                    os.makedirs(type_target, exist_ok=True)
                    for fname in os.listdir(sp):
                        if fname.endswith('.conf'):
                            src_conf = os.path.join(sp, fname)
                            if os.path.isfile(src_conf):
                                if fname[:-5] not in interfaces_found:
                                    interfaces_found.append(fname[:-5])
                                dst_conf = os.path.join(type_target, fname)
                                shutil.copy(src_conf, dst_conf)
                                manifest_files.append({
                                    "path": f"configs/{conf_type}/{fname}",
                                    "sha256": calculate_sha256(src_conf),
                                    "size": os.path.getsize(src_conf)
                                })
                                # Include per-config backup folder if requested
                                if include_existing_backups:
                                    b_dir = os.path.join(sp, 'WGDashboard_Backup')
                                    if os.path.exists(b_dir) and os.path.isdir(b_dir):
                                        dst_b_dir = os.path.join(temp_dir, 'per_config_backups', conf_type, fname[:-5])
                                        shutil.copytree(b_dir, dst_b_dir, dirs_exist_ok=True)
                                        for root, dirs, files in os.walk(dst_b_dir):
                                            for file in files:
                                                b_full = os.path.join(root, file)
                                                b_rel = os.path.relpath(b_full, temp_dir).replace('\\', '/')
                                                manifest_files.append({
                                                    "path": b_rel,
                                                    "sha256": calculate_sha256(b_full),
                                                    "size": os.path.getsize(b_full)
                                                })

            # 3. Database dumps
            _report_backup_progress(job_id, 50, "dumping_databases")
            sql_target = os.path.join(temp_dir, 'sql')
            os.makedirs(sql_target, exist_ok=True)

            databases_to_dump = ['wgdashboard', 'wgdashboard_job']
            if include_logs:
                databases_to_dump.append('wgdashboard_log')

            for db_name in databases_to_dump:
                dump_path = os.path.join(sql_target, f"{db_name}_dump.sql")
                # Let dump_database_to_sql stream directly into the file
                dump_result = dump_database_to_sql(db_name, dump_path=dump_path)
                
                # If there's a returned string, it means an error occurred during dumping or it didn't stream
                if dump_result:
                    if dump_result.startswith("-- Error"):
                        raise Exception(dump_result)
                    else:
                        # Fallback just in case it didn't stream
                        with open(dump_path, 'w', encoding='utf-8') as f:
                            f.write(dump_result)
                manifest_files.append({
                    "path": f"sql/{db_name}_dump.sql",
                    "sha256": calculate_sha256(dump_path),
                    "size": os.path.getsize(dump_path)
                })

            # 4. Create MANIFEST.json
            _report_backup_progress(job_id, 80, "creating_manifest")
            manifest = {
                "version": "1.0",
                "dashboard_version": getattr(DashboardConfig, 'DashboardVersion', 'v4.3.3'),
                "timestamp": datetime.now().isoformat(),
                "label": label,
                "interfaces": interfaces_found,
                "files": manifest_files
            }
            manifest_path = os.path.join(temp_dir, 'MANIFEST.json')
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=2)

            # 5. Create final ZIP archive
            _report_backup_progress(job_id, 90, "creating_archive")
            zip_filename = f"{backup_name}.zip"
            zip_filepath = os.path.join(backup_dir, zip_filename)

            with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        full_p = os.path.join(root, file)
                        rel_p = os.path.relpath(full_p, temp_dir).replace('\\', '/')
                        zipf.write(full_p, rel_p)

            # GB-12: Secure backup archive permissions (0o600)
            try:
                os.chmod(zip_filepath, 0o600)
            except Exception:
                pass

            # Clean up temp
            shutil.rmtree(temp_dir, ignore_errors=True)

            # Enforce retention policy for automated backups
            GlobalBackupManager.enforce_retention_policy(10)

            _report_backup_progress(job_id, 100, "done")
            return True, {
                "filename": zip_filename,
                "size": os.path.getsize(zip_filepath),
                "timestamp": manifest["timestamp"],
                "interfaces": interfaces_found
            }

        except Exception as e:
            if 'temp_dir' in locals() and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            return False, str(e)

    @staticmethod
    def list_global_backups() -> list[dict]:
        backup_dir = GlobalBackupManager.get_backup_dir()
        result = []
        if not os.path.exists(backup_dir):
            return result

        for fname in os.listdir(backup_dir):
            if fname.endswith('.zip') and fname.startswith('GlobalBackup_'):
                fpath = os.path.join(backup_dir, fname)
                try:
                    with zipfile.ZipFile(fpath, 'r') as zipf:
                        if 'MANIFEST.json' in zipf.namelist():
                            manifest_data = json.loads(zipf.read('MANIFEST.json').decode('utf-8'))
                            result.append({
                                "filename": fname,
                                "size": os.path.getsize(fpath),
                                "created": manifest_data.get("timestamp"),
                                "label": manifest_data.get("label", ""),
                                "interfaces": manifest_data.get("interfaces", []),
                                "version": manifest_data.get("dashboard_version", "unknown")
                            })
                except Exception:
                    pass

        result.sort(key=lambda x: x.get("created") or "", reverse=True)
        return result

    @staticmethod
    def delete_global_backup(filename: str) -> bool:
        clean_name = os.path.basename(filename)
        if not clean_name.endswith('.zip') or not clean_name.startswith('GlobalBackup_'):
            return False
        fpath = os.path.join(GlobalBackupManager.get_backup_dir(), clean_name)
        if os.path.exists(fpath):
            os.remove(fpath)
            return True
        return False

    @staticmethod
    def enforce_retention_policy(max_backups: int = 10) -> bool:
        try:
            backups = GlobalBackupManager.list_global_backups()
            # GB-09: Retention applies ONLY to automated backups (label starts with "Auto_").
            # Manual backups and PreRestore safety backups must NEVER be deleted automatically.
            auto_backups = [b for b in backups if b.get('label', '').startswith('Auto_')]
            if len(auto_backups) > max_backups:
                to_delete = auto_backups[max_backups:]
                for b in to_delete:
                    GlobalBackupManager.delete_global_backup(b['filename'])
            return True
        except Exception:
            return False

    @staticmethod
    def validate_backup_manifest(zip_filepath: str) -> tuple[bool, str | dict]:
        if not os.path.exists(zip_filepath):
            return False, "Backup file does not exist"
        try:
            with zipfile.ZipFile(zip_filepath, 'r') as zipf:
                raw_namelist = zipf.namelist()

                # GB-13: Defend against zip bombs
                if len(raw_namelist) > 5000:
                    return False, f"Archive contains too many files ({len(raw_namelist)} > 5000)"

                total_uncompressed = 0
                for info in zipf.infolist():
                    total_uncompressed += info.file_size
                    if total_uncompressed > 1024 * 1024 * 1024:  # 1 GB
                        return False, "Archive uncompressed size exceeds limit (1 GB)"

                namelist = [n.replace('\\', '/') for n in raw_namelist]

                if 'MANIFEST.json' not in namelist:
                    return False, "Invalid backup: MANIFEST.json missing"

                try:
                    manifest_raw = zipf.read(raw_namelist[namelist.index('MANIFEST.json')]).decode('utf-8')
                    manifest = json.loads(manifest_raw)
                except Exception as e:
                    return False, f"Corrupted MANIFEST.json: {e}"

                if not isinstance(manifest, dict) or "version" not in manifest:
                    return False, "Invalid manifest schema: missing version"

                manifest_files = manifest.get("files", [])
                allowed_paths = {"MANIFEST.json"}

                # GB-03: Verify sha256 checksum and presence of all declared files
                for entry in manifest_files:
                    path = entry.get("path")
                    expected_sha256 = entry.get("sha256")
                    if not path or not expected_sha256:
                        return False, "Manifest file entry missing path or sha256"

                    norm_path = path.replace('\\', '/')
                    if norm_path not in namelist:
                        return False, f"Missing file from manifest: {norm_path}"

                    allowed_paths.add(norm_path)
                    raw_entry_name = raw_namelist[namelist.index(norm_path)]
                    data = zipf.read(raw_entry_name)
                    actual_sha256 = hashlib.sha256(data).hexdigest()
                    if actual_sha256.lower() != expected_sha256.lower():
                        return False, f"Checksum mismatch for file: {norm_path}"

                # GB-03 & GB-04: Reject any rogue/unexpected files not listed in manifest
                # Note: Allow directory entries ending with '/'
                extra_files = [n for n in namelist if n not in allowed_paths and not n.endswith("/")]
                if extra_files:
                    return False, f"Unexpected/unmanifested files in archive: {extra_files[:5]}"

                return True, manifest
        except zipfile.BadZipFile:
            return False, "Archive is corrupted or not a valid zip file"
        except Exception as e:
            return False, f"Failed to validate archive: {str(e)}"

    @staticmethod
    def restore_global_backup(zip_filepath: str, job_id: str | None = None) -> tuple[bool, str]:
        def report(pct: int, step: str) -> None:
            _report_progress(job_id, pct, step)

        report(5, "validating")
        valid, manifest_or_err = GlobalBackupManager.validate_backup_manifest(zip_filepath)
        if not valid:
            return False, str(manifest_or_err)

        # GB-02: Safety Backup! Create a PreRestore backup of current state before touching anything
        report(8, "creating_safety_backup")
        try:
            safety_ok, safety_res = GlobalBackupManager.create_global_backup(label="PreRestore")
            if not safety_ok:
                _logger.warning(f"Failed to create PreRestore safety backup: {safety_res}")
            else:
                _logger.info(f"Created PreRestore safety backup: {safety_res}")
        except Exception as e:
            _logger.warning(f"Exception creating PreRestore safety backup: {e}")

        temp_extract = os.path.join(GlobalBackupManager.get_backup_dir(), f"restore_{uuid.uuid4().hex}")
        try:
            report(10, "extracting")
            with zipfile.ZipFile(zip_filepath, 'r') as zipf:
                # Zip Slip Protection: sanitize extracted member paths
                abs_temp = os.path.abspath(temp_extract)
                for member in zipf.namelist():
                    target_member_path = os.path.abspath(os.path.join(abs_temp, member))
                    if not target_member_path.startswith(abs_temp + os.sep) and target_member_path != abs_temp:
                        return False, f"Zip member attempted path traversal: {member}"
                zipf.extractall(temp_extract)

            config_path = os.getenv('CONFIGURATION_PATH', '.')
            
            # GB-04: Read wireguard and amnezia destination paths EXCLUSIVELY from current host server configuration!
            # NEVER trust paths in restored ini to prevent hijacking root filesystem
            target_wg = "/etc/wireguard"
            target_amnezia = "/etc/amnezia/amneziawg"
            current_host_ini = os.path.join(config_path, 'wg-dashboard.ini')
            current_server_section = {}
            if os.path.exists(current_host_ini):
                try:
                    import configparser
                    h_cfg = configparser.ConfigParser()
                    h_cfg.read(current_host_ini)
                    if 'Server' in h_cfg:
                        current_server_section = dict(h_cfg['Server'])
                        if 'wg_conf_path' in current_server_section:
                            target_wg = current_server_section['wg_conf_path']
                        if 'awg_conf_path' in current_server_section:
                            target_amnezia = current_server_section['awg_conf_path']
                except Exception:
                    pass

            # Record existing host configurations before restoring (GB-06)
            existing_host_confs = set()
            if os.path.exists(target_wg):
                existing_host_confs.update([f[:-5] for f in os.listdir(target_wg) if f.endswith('.conf')])
            if os.path.exists(target_amnezia):
                existing_host_confs.update([f[:-5] for f in os.listdir(target_amnezia) if f.endswith('.conf')])

            # 1. Restore ini & config files
            report(20, "restoring_configs")
            ini_src = os.path.join(temp_extract, 'wg-dashboard.ini')
            if os.path.exists(ini_src):
                # Preserve host's Server section
                if current_server_section:
                    try:
                        import configparser
                        restored_cfg = configparser.ConfigParser()
                        restored_cfg.read(ini_src)
                        if 'Server' not in restored_cfg:
                            restored_cfg.add_section('Server')
                        for k, v in current_server_section.items():
                            restored_cfg.set('Server', k, str(v))
                        with open(os.path.join(config_path, 'wg-dashboard.ini'), 'w', encoding='utf-8') as out_f:
                            restored_cfg.write(out_f)
                    except Exception as e:
                        _logger.warning(f"Failed to preserve server section in ini: {e}")
                        shutil.copy(ini_src, os.path.join(config_path, 'wg-dashboard.ini'))
                else:
                    shutil.copy(ini_src, os.path.join(config_path, 'wg-dashboard.ini'))

            oidc_src = os.path.join(temp_extract, 'wg-dashboard-oidc-providers.json')
            if os.path.exists(oidc_src):
                shutil.copy(oidc_src, os.path.join(config_path, 'wg-dashboard-oidc-providers.json'))

            # GB-04: DO NOT restore gunicorn.conf.py, certbot.ini, or plugins/
            # This completely closes remote code execution vectors via uploaded backups.

            # 2. Restore WireGuard & AmneziaWG .conf files
            report(40, "restoring_wireguard")
            configs_src = os.path.join(temp_extract, 'configs')
            conf_regex = re.compile(r'^[a-zA-Z0-9_=+.-]+\.conf$')
            restored_confs = {}
            if os.path.exists(configs_src) and os.path.isdir(configs_src):
                os.makedirs(target_wg, exist_ok=True)
                
                # Backwards compatibility: root .conf files are wireguard
                for fname in os.listdir(configs_src):
                    if fname.endswith('.conf') and conf_regex.match(fname):
                        dest_f = os.path.join(target_wg, fname)
                        shutil.copy(os.path.join(configs_src, fname), dest_f)
                        restored_confs[fname[:-5]] = ('wg', dest_f)
                
                # New structure: separated by type
                wg_src = os.path.join(configs_src, 'wireguard')
                if os.path.exists(wg_src) and os.path.isdir(wg_src):
                    for fname in os.listdir(wg_src):
                        if fname.endswith('.conf') and conf_regex.match(fname):
                            dest_f = os.path.join(target_wg, fname)
                            shutil.copy(os.path.join(wg_src, fname), dest_f)
                            restored_confs[fname[:-5]] = ('wg', dest_f)
                            
                amnezia_src = os.path.join(configs_src, 'amnezia')
                if os.path.exists(amnezia_src) and os.path.isdir(amnezia_src):
                    os.makedirs(target_amnezia, exist_ok=True)
                    for fname in os.listdir(amnezia_src):
                        if fname.endswith('.conf') and conf_regex.match(fname):
                            dest_f = os.path.join(target_amnezia, fname)
                            shutil.copy(os.path.join(amnezia_src, fname), dest_f)
                            restored_confs[fname[:-5]] = ('awg', dest_f)

            # GB-14: Restore per_config_backups if present in the archive
            per_cfg_src = os.path.join(temp_extract, 'per_config_backups')
            if os.path.exists(per_cfg_src) and os.path.isdir(per_cfg_src):
                report(50, "restoring_per_config_backups")
                for ctype in os.listdir(per_cfg_src):
                    ctype_path = os.path.join(per_cfg_src, ctype)
                    if os.path.isdir(ctype_path):
                        target_base = target_wg if ctype == 'wireguard' else target_amnezia
                        for iface_dir in os.listdir(ctype_path):
                            src_b_dir = os.path.join(ctype_path, iface_dir)
                            if os.path.isdir(src_b_dir):
                                dst_b_dir = os.path.join(target_base, 'WGDashboard_Backup')
                                os.makedirs(dst_b_dir, exist_ok=True)
                                for b_file in os.listdir(src_b_dir):
                                    src_file_path = os.path.join(src_b_dir, b_file)
                                    if os.path.isfile(src_file_path):
                                        shutil.copy(src_file_path, os.path.join(dst_b_dir, b_file))

            # 3. Restore databases from SQL dumps
            report(60, "restoring_databases")
            sql_src = os.path.join(temp_extract, 'sql')
            if os.path.exists(sql_src) and os.path.isdir(sql_src):
                dump_files = [f for f in os.listdir(sql_src) if f.endswith('_dump.sql')]
                total_dumps = len(dump_files) or 1
                for idx, dump_file in enumerate(dump_files):
                    db_name = dump_file.replace('_dump.sql', '')
                    with open(os.path.join(sql_src, dump_file), 'r', encoding='utf-8', errors='ignore') as f:
                        sql_content = f.read()
                    # GB-02: Validate return status of database restore
                    db_success = restore_database_from_sql(db_name, sql_content)
                    if not db_success:
                        return False, f"Failed to restore database: {db_name}"
                    pct = 60 + int(25 * (idx + 1) / total_dumps)
                    report(pct, "restoring_databases")

            # 4. Synchronize running kernel interfaces with restored configs
            report(90, "syncing_runtime")

            sync_notes = []
            hook_warnings = []
            for iface, (proto, cpath) in restored_confs.items():
                try:
                    s_ok, s_msg = sync_interface_runtime(iface, proto, cpath)
                    if not s_ok:
                        sync_notes.append(f"{iface}: {s_msg}")
                    # Check for script hooks in restored confs (GB-04)
                    with open(cpath, 'r', encoding='utf-8', errors='ignore') as f:
                        c_text = f.read()
                    for hook in ['PreUp', 'PostUp', 'PreDown', 'PostDown']:
                        found = re.findall(rf'^\s*{hook}\s*=\s*(.+)$', c_text, re.MULTILINE | re.IGNORECASE)
                        if found:
                            hook_warnings.append(f"{iface} [{hook}]: {found[0].strip()[:40]}")
                except Exception as e:
                    sync_notes.append(f"{iface}: {e}")

            # Report unmanaged local configs (GB-06)
            extra_local = sorted(list(existing_host_confs - set(restored_confs.keys())))
            if extra_local:
                sync_notes.append(f"Unmanaged local interfaces preserved (not in backup): {', '.join(extra_local)}")
            if hook_warnings:
                sync_notes.append(f"Script hooks present in restored configs: {'; '.join(hook_warnings[:5])}")

            report(95, "finalizing")
            shutil.rmtree(temp_extract, ignore_errors=True)
            report(100, "done")
            msg = "Global backup restored successfully."
            if sync_notes:
                msg += " Notes: " + "; ".join(sync_notes)
            return True, msg

        except Exception as e:
            if os.path.exists(temp_extract):
                shutil.rmtree(temp_extract, ignore_errors=True)
            return False, f"Restore failed: {str(e)}"

