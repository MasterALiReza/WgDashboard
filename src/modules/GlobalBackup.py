import os
import re
import json
import zipfile
import hashlib
import shutil
import uuid
from datetime import datetime
import sqlalchemy as db
from sqlalchemy.schema import CreateTable
from .DatabaseConnection import ConnectionString

GLOBAL_BACKUP_DIR = os.getenv('GLOBAL_BACKUP_PATH', os.path.join(os.getenv('CONFIGURATION_PATH', '.'), 'GlobalBackups'))

def calculate_sha256(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def dump_database_to_sql(db_name: str, dump_path: str = None) -> str:
    try:
        cn = ConnectionString(db_name)
        # Use sqlite3.iterdump if it's a SQLite database for much better performance
        if cn.startswith('sqlite:///'):
            import sqlite3
            db_path = cn.split('sqlite:///')[1].split('?')[0]
            con = sqlite3.connect(db_path)
            
            if dump_path:
                with open(dump_path, 'w', encoding='utf-8') as f:
                    for line in con.iterdump():
                        f.write(line + "\n")
                con.close()
                return ""
            else:
                statements = [line for line in con.iterdump()]
                con.close()
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
        engine = db.create_engine(ConnectionString(db_name))
        metadata = db.MetaData()
        try:
            metadata.reflect(bind=engine)
            with engine.begin() as conn:
                for table in reversed(metadata.sorted_tables):
                    conn.execute(db.text(f'DROP TABLE IF EXISTS "{table.name}"'))
        except Exception:
            pass

        statements = [s.strip() for s in sql_content.split(";") if s.strip()]
        with engine.begin() as conn:
            for stmt in statements:
                if stmt and not stmt.startswith("--"):
                    try:
                        conn.execute(db.text(stmt))
                    except Exception as e:
                        pass
        engine.dispose()
        return True
    except Exception as e:
        return False

class GlobalBackupManager:
    @staticmethod
    def get_backup_dir() -> str:
        os.makedirs(GLOBAL_BACKUP_DIR, exist_ok=True)
        return GLOBAL_BACKUP_DIR

    @staticmethod
    def create_global_backup(label: str = "", include_logs: bool = True, include_existing_backups: bool = False) -> tuple[bool, str | dict]:
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

            # 2. WireGuard interface configs (.conf)
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

            # 3. Database dumps
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
            manifest = {
                "version": "1.0",
                "dashboard_version": "v4.3.3",
                "timestamp": datetime.now().isoformat(),
                "label": label,
                "interfaces": interfaces_found,
                "files": manifest_files
            }
            manifest_path = os.path.join(temp_dir, 'MANIFEST.json')
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=2)

            # 5. Create final ZIP archive
            zip_filename = f"{backup_name}.zip"
            zip_filepath = os.path.join(backup_dir, zip_filename)

            with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        full_p = os.path.join(root, file)
                        rel_p = os.path.relpath(full_p, temp_dir)
                        zipf.write(full_p, rel_p)

            # Clean up temp
            shutil.rmtree(temp_dir, ignore_errors=True)

            # Enforce retention policy
            GlobalBackupManager.enforce_retention_policy(10)

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
            if len(backups) > max_backups:
                to_delete = backups[max_backups:]
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
                if 'MANIFEST.json' not in zipf.namelist():
                    return False, "Invalid backup: MANIFEST.json missing"
                manifest = json.loads(zipf.read('MANIFEST.json').decode('utf-8'))
                return True, manifest
        except Exception as e:
            return False, f"Failed to validate archive: {str(e)}"

    @staticmethod
    def restore_global_backup(zip_filepath: str) -> tuple[bool, str]:
        valid, manifest_or_err = GlobalBackupManager.validate_backup_manifest(zip_filepath)
        if not valid:
            return False, str(manifest_or_err)

        temp_extract = os.path.join(GlobalBackupManager.get_backup_dir(), f"restore_{uuid.uuid4().hex}")
        try:
            with zipfile.ZipFile(zip_filepath, 'r') as zipf:
                zipf.extractall(temp_extract)

            config_path = os.getenv('CONFIGURATION_PATH', '.')

            # 1. Restore ini & config files
            ini_src = os.path.join(temp_extract, 'wg-dashboard.ini')
            if os.path.exists(ini_src):
                shutil.copy(ini_src, os.path.join(config_path, 'wg-dashboard.ini'))

            oidc_src = os.path.join(temp_extract, 'wg-dashboard-oidc-providers.json')
            if os.path.exists(oidc_src):
                shutil.copy(oidc_src, os.path.join(config_path, 'wg-dashboard-oidc-providers.json'))

            for opt_file in ['certbot.ini', 'gunicorn.conf.py']:
                p = os.path.join(temp_extract, opt_file)
                if os.path.exists(p):
                    shutil.copy(p, os.path.join(config_path, opt_file))

            plugins_src = os.path.join(temp_extract, 'plugins')
            if os.path.exists(plugins_src) and os.path.isdir(plugins_src):
                shutil.copytree(plugins_src, os.path.join(config_path, 'plugins'), dirs_exist_ok=True)

            # 2. Restore WireGuard & AmneziaWG .conf files
            configs_src = os.path.join(temp_extract, 'configs')
            if os.path.exists(configs_src) and os.path.isdir(configs_src):
                target_wg = "/etc/wireguard"
                target_amnezia = "/etc/amnezia/amneziawg"
                
                restored_ini = os.path.join(temp_extract, 'wg-dashboard.ini')
                if os.path.exists(restored_ini):
                    try:
                        import configparser
                        config = configparser.ConfigParser()
                        config.read(restored_ini)
                        if 'Server' in config:
                            if 'wg_conf_path' in config['Server']:
                                target_wg = config['Server']['wg_conf_path']
                            if 'awg_conf_path' in config['Server']:
                                target_amnezia = config['Server']['awg_conf_path']
                    except Exception:
                        pass
                        
                os.makedirs(target_wg, exist_ok=True)
                
                # Backwards compatibility: root .conf files are wireguard
                for fname in os.listdir(configs_src):
                    if fname.endswith('.conf'):
                        shutil.copy(os.path.join(configs_src, fname), os.path.join(target_wg, fname))
                
                # New structure: separated by type
                wg_src = os.path.join(configs_src, 'wireguard')
                if os.path.exists(wg_src) and os.path.isdir(wg_src):
                    for fname in os.listdir(wg_src):
                        if fname.endswith('.conf'):
                            shutil.copy(os.path.join(wg_src, fname), os.path.join(target_wg, fname))
                            
                amnezia_src = os.path.join(configs_src, 'amnezia')
                if os.path.exists(amnezia_src) and os.path.isdir(amnezia_src):
                    os.makedirs(target_amnezia, exist_ok=True)
                    for fname in os.listdir(amnezia_src):
                        if fname.endswith('.conf'):
                            shutil.copy(os.path.join(amnezia_src, fname), os.path.join(target_amnezia, fname))

            # 3. Restore databases from SQL dumps
            sql_src = os.path.join(temp_extract, 'sql')
            if os.path.exists(sql_src) and os.path.isdir(sql_src):
                for dump_file in os.listdir(sql_src):
                    if dump_file.endswith('_dump.sql'):
                        db_name = dump_file.replace('_dump.sql', '')
                        with open(os.path.join(sql_src, dump_file), 'r', encoding='utf-8', errors='ignore') as f:
                            sql_content = f.read()
                        restore_database_from_sql(db_name, sql_content)

            shutil.rmtree(temp_extract, ignore_errors=True)
            return True, "Global backup restored successfully. Please restart WGDashboard to apply changes."

        except Exception as e:
            if os.path.exists(temp_extract):
                shutil.rmtree(temp_extract, ignore_errors=True)
            return False, f"Restore failed: {str(e)}"
