import os
import sys
import json
import zipfile
import hashlib
import sqlite3
import tempfile
import shutil
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from modules.GlobalBackup import (
    GlobalBackupManager,
    dump_database_to_sql,
    restore_database_from_sql,
    split_sql_statements,
    sync_interface_runtime,
    _backup_jobs,
    _restore_jobs,
    _backup_jobs_lock,
    _restore_jobs_lock,
    _is_backup_running,
    _is_backup_running_lock
)
from modules.DashboardConfig import DashboardConfig


# -----------------------------------------------------------------------------
# GB-08: Quote-aware SQL Statement Splitter
# -----------------------------------------------------------------------------
def test_split_sql_statements():
    sql = """
    -- This is a comment
    CREATE TABLE test (id INT, val TEXT);
    INSERT INTO test VALUES (1, 'PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE;');
    INSERT INTO test VALUES (2, 'Name with \\'escaped quotes\\' and ; semicolon');
    """
    stmts = split_sql_statements(sql)
    assert len(stmts) == 3
    assert stmts[0] == "CREATE TABLE test (id INT, val TEXT)"
    assert "iptables -t nat" in stmts[1]
    assert "escaped quotes" in stmts[2]


# -----------------------------------------------------------------------------
# GB-01, GB-02, GB-07: SQLite Dump & Safe Isolated Restore
# -----------------------------------------------------------------------------
def test_dump_and_restore_sqlite_database(tmp_path):
    db_file = tmp_path / "test_db.db"
    with sqlite3.connect(str(db_file)) as con:
        con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);")
        con.execute("INSERT INTO users (name) VALUES ('Alice');")
        con.execute("INSERT INTO users (name) VALUES ('Bob');")

    dump_file = tmp_path / "dump.sql"
    with patch("modules.GlobalBackup.ConnectionString", return_value=f"sqlite:///{db_file}"):
        res = dump_database_to_sql("test_db", dump_path=str(dump_file))
        assert res == ""
        assert os.path.exists(dump_file)

        with open(dump_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Alice" in content
        assert "Bob" in content

        # Now test isolated restore into a new db
        target_db = tmp_path / "restored.db"
        with patch("modules.GlobalBackup.ConnectionString", return_value=f"sqlite:///{target_db}"):
            success = restore_database_from_sql("restored", content)
            assert success is True
            assert os.path.exists(target_db)

            with sqlite3.connect(str(target_db)) as con:
                cursor = con.cursor()
                cursor.execute("SELECT name FROM users ORDER BY id;")
                rows = cursor.fetchall()
                assert rows == [("Alice",), ("Bob",)]


def test_restore_database_corrupted_fails_and_preserves_original(tmp_path):
    db_file = tmp_path / "original.db"
    with sqlite3.connect(str(db_file)) as con:
        con.execute("CREATE TABLE important (data TEXT);")
        con.execute("INSERT INTO important VALUES ('critical data');")

    corrupted_sql = "THIS IS NOT VALID SQL AT ALL; SYNTAX ERROR 123;"
    with patch("modules.GlobalBackup.ConnectionString", return_value=f"sqlite:///{db_file}"):
        success = restore_database_from_sql("original", corrupted_sql)
        assert success is False

        # Verify original database was NOT wiped or corrupted
        with sqlite3.connect(str(db_file)) as con:
            cursor = con.cursor()
            cursor.execute("SELECT data FROM important;")
            rows = cursor.fetchall()
            assert rows == [("critical data",)]


# -----------------------------------------------------------------------------
# GB-03 & GB-16: MANIFEST Verification, Checksums, and Schema Version
# -----------------------------------------------------------------------------
def test_validate_backup_manifest_success(tmp_path):
    zip_path = tmp_path / "GlobalBackup_valid.zip"
    f1_content = b"sample wireguard config"
    f1_sha = hashlib.sha256(f1_content).hexdigest()

    manifest = {
        "version": "1.0",
        "dashboard_version": "v4.3.4",
        "timestamp": "2026-10-15T12:00:00",
        "label": "TestBackup",
        "files": [
            {"path": "configs/wireguard/wg0.conf", "sha256": f1_sha, "size": len(f1_content)}
        ]
    }

    with zipfile.ZipFile(str(zip_path), "w") as z:
        z.writestr("MANIFEST.json", json.dumps(manifest))
        z.writestr("configs/wireguard/wg0.conf", f1_content)

    valid, result = GlobalBackupManager.validate_backup_manifest(str(zip_path))
    assert valid is True
    assert isinstance(result, dict)
    assert result["version"] == "1.0"
    assert result["dashboard_version"] == "v4.3.4"


def test_validate_backup_manifest_checksum_mismatch(tmp_path):
    zip_path = tmp_path / "GlobalBackup_mismatch.zip"
    f1_content = b"tampered config data"
    wrong_sha = hashlib.sha256(b"original data").hexdigest()

    manifest = {
        "version": "1.0",
        "dashboard_version": "v4.3.4",
        "timestamp": "2026-10-15T12:00:00",
        "files": [
            {"path": "configs/wireguard/wg0.conf", "sha256": wrong_sha, "size": len(f1_content)}
        ]
    }

    with zipfile.ZipFile(str(zip_path), "w") as z:
        z.writestr("MANIFEST.json", json.dumps(manifest))
        z.writestr("configs/wireguard/wg0.conf", f1_content)

    valid, err = GlobalBackupManager.validate_backup_manifest(str(zip_path))
    assert valid is False
    assert "Checksum mismatch" in err


def test_validate_backup_manifest_missing_declared_file(tmp_path):
    zip_path = tmp_path / "GlobalBackup_missing.zip"
    manifest = {
        "version": "1.0",
        "dashboard_version": "v4.3.4",
        "timestamp": "2026-10-15T12:00:00",
        "files": [
            {"path": "configs/wireguard/wg0.conf", "sha256": "abcdef123456", "size": 100}
        ]
    }

    with zipfile.ZipFile(str(zip_path), "w") as z:
        z.writestr("MANIFEST.json", json.dumps(manifest))
        # Note: wg0.conf is NOT added

    valid, err = GlobalBackupManager.validate_backup_manifest(str(zip_path))
    assert valid is False
    assert "Missing file from manifest" in err


def test_validate_backup_manifest_rogue_file_rejected(tmp_path):
    zip_path = tmp_path / "GlobalBackup_rogue.zip"
    manifest = {
        "version": "1.0",
        "dashboard_version": "v4.3.4",
        "timestamp": "2026-10-15T12:00:00",
        "files": []
    }

    with zipfile.ZipFile(str(zip_path), "w") as z:
        z.writestr("MANIFEST.json", json.dumps(manifest))
        z.writestr("rogue_payload.py", b"import os; os.system('echo pwned')")

    valid, err = GlobalBackupManager.validate_backup_manifest(str(zip_path))
    assert valid is False
    assert "Unexpected/unmanifested files" in err


def test_validate_backup_manifest_zip_bomb_limits(tmp_path):
    zip_path = tmp_path / "GlobalBackup_bomb.zip"
    manifest = {
        "version": "1.0",
        "dashboard_version": "v4.3.4",
        "timestamp": "2026-10-15T12:00:00",
        "files": []
    }

    with zipfile.ZipFile(str(zip_path), "w") as z:
        z.writestr("MANIFEST.json", json.dumps(manifest))

    # Mock namelist to return > 5000 items
    with patch("zipfile.ZipFile.namelist", return_value=[f"file_{i}.txt" for i in range(5001)]):
        valid, err = GlobalBackupManager.validate_backup_manifest(str(zip_path))
        assert valid is False
        assert "too many files" in err


# -----------------------------------------------------------------------------
# GB-04: Security - Rejection of Dangerous RCE Files and Path Preservation
# -----------------------------------------------------------------------------
def test_restore_skips_rce_files_and_preserves_host_server_config(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    host_ini = config_dir / "wg-dashboard.ini"
    host_ini.write_text("""[Server]
app_ip = 0.0.0.0
app_port = 10086
wg_conf_path = /etc/wireguard
awg_conf_path = /etc/amnezia/amneziawg
""", encoding="utf-8")

    # Target WG dir
    target_wg = tmp_path / "wireguard_target"
    target_wg.mkdir()

    # Create backup containing malicious/dangerous files:
    # 1. gunicorn.conf.py
    # 2. certbot.ini
    # 3. plugins/backdoor/main.py
    # 4. wg-dashboard.ini attempting to override host Server settings (e.g. port 80, wg_conf_path /tmp)
    # 5. legitimate wg0.conf
    zip_path = tmp_path / "GlobalBackup_test_sec.zip"

    evil_ini = """[Server]
app_ip = 1.2.3.4
app_port = 9999
wg_conf_path = /etc/modprobe.d
[Peers]
peer_global_ip = 10.0.0.1
"""
    evil_gunicorn = b"import os; os.system('touch /tmp/pwned')"
    evil_plugin = b"# malicious plugin"
    good_conf = b"[Interface]\nPrivateKey = testkey\nAddress = 10.0.0.1/24\n"

    manifest_files = [
        {"path": "wg-dashboard.ini", "sha256": hashlib.sha256(evil_ini.encode()).hexdigest(), "size": len(evil_ini)},
        {"path": "configs/wireguard/wg0.conf", "sha256": hashlib.sha256(good_conf).hexdigest(), "size": len(good_conf)},
    ]
    manifest = {
        "version": "1.0",
        "dashboard_version": "v4.3.4",
        "timestamp": "2026-10-15T12:00:00",
        "files": manifest_files
    }

    with zipfile.ZipFile(str(zip_path), "w") as z:
        z.writestr("MANIFEST.json", json.dumps(manifest))
        z.writestr("wg-dashboard.ini", evil_ini.encode())
        z.writestr("configs/wireguard/wg0.conf", good_conf)

    with patch.dict(os.environ, {"CONFIGURATION_PATH": str(config_dir), "GLOBAL_BACKUP_PATH": str(tmp_path / "backups")}), \
         patch("modules.GlobalBackup.sync_interface_runtime", return_value=(True, "ok")), \
         patch.object(GlobalBackupManager, "create_global_backup", return_value=(True, "mock safety backup")):

        # Set target_wg in host_ini to our temp target_wg
        host_ini.write_text(f"""[Server]
app_ip = 0.0.0.0
app_port = 10086
wg_conf_path = {target_wg}
awg_conf_path = /etc/amnezia/amneziawg
""", encoding="utf-8")

        success, msg = GlobalBackupManager.restore_global_backup(str(zip_path))
        assert success is True

        # 1. Verify dangerous files were NOT created in config_dir
        assert not os.path.exists(config_dir / "gunicorn.conf.py")
        assert not os.path.exists(config_dir / "certbot.ini")
        assert not os.path.exists(config_dir / "plugins")

        # 2. Verify host Server section was preserved
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(host_ini)
        assert cfg["Server"]["app_port"] == "10086"
        assert cfg["Server"]["app_ip"] == "0.0.0.0"
        assert cfg["Server"]["wg_conf_path"] == str(target_wg)
        # Non-server section restored from backup
        assert cfg["Peers"]["peer_global_ip"] == "10.0.0.1"

        # 3. Verify wg0.conf was restored into target_wg
        assert os.path.exists(target_wg / "wg0.conf")


# -----------------------------------------------------------------------------
# GB-09: Retention Policy - Prunes ONLY Auto_* Backups
# -----------------------------------------------------------------------------
def test_retention_policy_preserves_manual_and_prerestore_backups(tmp_path):
    backups = [
        {"filename": "GlobalBackup_20261015_010000_Auto_3h.zip", "label": "Auto_3h", "created": "2026-10-15T01:00:00"},
        {"filename": "GlobalBackup_20261015_020000_Auto_3h.zip", "label": "Auto_3h", "created": "2026-10-15T02:00:00"},
        {"filename": "GlobalBackup_20261015_030000_Auto_3h.zip", "label": "Auto_3h", "created": "2026-10-15T03:00:00"},
        {"filename": "GlobalBackup_20261015_040000_Auto_3h.zip", "label": "Auto_3h", "created": "2026-10-15T04:00:00"},
        {"filename": "GlobalBackup_20261015_050000_ManualMigration.zip", "label": "ManualMigration", "created": "2026-10-15T05:00:00"},
        {"filename": "GlobalBackup_20261015_060000_PreRestore.zip", "label": "PreRestore", "created": "2026-10-15T06:00:00"},
    ]

    deleted = []
    def mock_delete(fname):
        deleted.append(fname)
        return True

    with patch.object(GlobalBackupManager, "list_global_backups", return_value=backups), \
         patch.object(GlobalBackupManager, "delete_global_backup", side_effect=mock_delete):

        # Enforce max 2 automated backups
        GlobalBackupManager.enforce_retention_policy(max_backups=2)

        # Auto backups are 4: [010000, 020000, 030000, 040000].
        # Keeping 2 means oldest 2 (030000, 040000) are pruned.
        assert "GlobalBackup_20261015_030000_Auto_3h.zip" in deleted
        assert "GlobalBackup_20261015_040000_Auto_3h.zip" in deleted
        # Manual backups and PreRestore backups must NEVER be deleted!
        assert "GlobalBackup_20261015_050000_ManualMigration.zip" not in deleted
        assert "GlobalBackup_20261015_060000_PreRestore.zip" not in deleted


# -----------------------------------------------------------------------------
# GB-14: Per-Config Backups Restoration
# -----------------------------------------------------------------------------
def test_restore_per_config_backups(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    target_wg = tmp_path / "wireguard_target"
    target_wg.mkdir()

    per_conf_data = b"[Interface]\nAddress = 10.0.0.1/24\n"
    zip_path = tmp_path / "GlobalBackup_per_cfg.zip"
    manifest = {
        "version": "1.0",
        "dashboard_version": "v4.3.4",
        "timestamp": "2026-10-15T12:00:00",
        "files": [
            {"path": "per_config_backups/wireguard/wg0/wg0_123456789.conf",
             "sha256": hashlib.sha256(per_conf_data).hexdigest(),
             "size": len(per_conf_data)}
        ]
    }

    with zipfile.ZipFile(str(zip_path), "w") as z:
        z.writestr("MANIFEST.json", json.dumps(manifest))
        z.writestr("per_config_backups/wireguard/wg0/wg0_123456789.conf", per_conf_data)

    host_ini = config_dir / "wg-dashboard.ini"
    host_ini.write_text(f"""[Server]
wg_conf_path = {target_wg}
""", encoding="utf-8")

    with patch.dict(os.environ, {"CONFIGURATION_PATH": str(config_dir), "GLOBAL_BACKUP_PATH": str(tmp_path / "backups")}), \
         patch("modules.GlobalBackup.sync_interface_runtime", return_value=(True, "ok")), \
         patch.object(GlobalBackupManager, "create_global_backup", return_value=(True, "mock safety backup")):

        success, _ = GlobalBackupManager.restore_global_backup(str(zip_path))
        assert success is True

        expected_file = target_wg / "WGDashboard_Backup" / "wg0_123456789.conf"
        assert expected_file.exists()
        assert expected_file.read_bytes() == per_conf_data


# -----------------------------------------------------------------------------
# GB-15: sync_interface_runtime warning note
# -----------------------------------------------------------------------------
def test_sync_interface_runtime_note():
    # If wg command not present or mock execution
    with patch("subprocess.check_output", return_value=b""):
        with tempfile.NamedTemporaryFile(suffix=".conf", delete=False) as tf:
            tf.write(b"[Interface]\nPrivateKey = aaaa\nAddress = 10.0.0.1/24\n")
            tf.flush()
            tf_name = tf.name

        try:
            ok, msg = sync_interface_runtime("wg0", "wg", tf_name)
            assert ok is True
            assert "Interface-level changes (Address/MTU/PostUp) require restart" in msg
        finally:
            if os.path.exists(tf_name):
                os.remove(tf_name)


# -----------------------------------------------------------------------------
# GB-02: Failure propagation if DB restore fails
# -----------------------------------------------------------------------------
def test_restore_fails_when_db_restore_fails(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    target_wg = tmp_path / "wireguard_target"
    target_wg.mkdir()

    sql_dump = b"SELECT 1;"
    zip_path = tmp_path / "GlobalBackup_db_fail.zip"
    manifest = {
        "version": "1.0",
        "dashboard_version": "v4.3.4",
        "timestamp": "2026-10-15T12:00:00",
        "files": [
            {"path": "sql/wgdashboard_dump.sql",
             "sha256": hashlib.sha256(sql_dump).hexdigest(),
             "size": len(sql_dump)}
        ]
    }

    with zipfile.ZipFile(str(zip_path), "w") as z:
        z.writestr("MANIFEST.json", json.dumps(manifest))
        z.writestr("sql/wgdashboard_dump.sql", sql_dump)

    with patch.dict(os.environ, {"CONFIGURATION_PATH": str(config_dir), "GLOBAL_BACKUP_PATH": str(tmp_path / "backups")}), \
         patch("modules.GlobalBackup.restore_database_from_sql", return_value=False), \
         patch.object(GlobalBackupManager, "create_global_backup", return_value=(True, "mock safety backup")):

        success, err = GlobalBackupManager.restore_global_backup(str(zip_path))
        assert success is False
        assert "Failed to restore database: wgdashboard" in err


# -----------------------------------------------------------------------------
# GB-12 & GB-16: Directory & Archive Permissions, Manifest Version
# -----------------------------------------------------------------------------
def test_backup_dir_and_file_permissions_and_manifest_version(tmp_path):
    bdir = tmp_path / "backups_perm"
    with patch.dict(os.environ, {"GLOBAL_BACKUP_PATH": str(bdir), "CONFIGURATION_PATH": str(tmp_path)}):
        backup_dir = GlobalBackupManager.get_backup_dir()
        assert os.path.exists(backup_dir)

        # Mock db dumping and create a real backup
        with patch("modules.GlobalBackup.dump_database_to_sql", return_value="-- empty dump"), \
             patch.object(DashboardConfig, "DashboardVersion", "v4.4.0", create=True):
            success, res = GlobalBackupManager.create_global_backup(label="PermTest")
            assert success is True
            assert isinstance(res, dict)
            zip_file = bdir / res["filename"]
            assert zip_file.exists()

            # Verify manifest version matches DashboardVersion
            with zipfile.ZipFile(str(zip_file), "r") as z:
                manifest = json.loads(z.read("MANIFEST.json").decode("utf-8"))
                assert manifest["dashboard_version"] == "v4.4.0"


# -----------------------------------------------------------------------------
# GB-10: Concurrency Lock
# -----------------------------------------------------------------------------
def test_backup_concurrency_lock():
    import modules.GlobalBackup as gb
    with gb._is_backup_running_lock:
        try:
            assert gb._is_backup_running is False
            gb._is_backup_running = True

            # Attempting to start another should detect running state
            with gb._is_backup_running_lock:
                is_running = gb._is_backup_running
                assert is_running is True
        finally:
            gb._is_backup_running = False

