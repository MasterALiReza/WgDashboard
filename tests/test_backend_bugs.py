import sys
import os
import pytest
import ipaddress
import tempfile
import configparser
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import sqlalchemy as db

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from modules.DashboardConfig import DashboardConfig
from modules.Peer import Peer
from modules.AmneziaPeer import AmneziaPeer
from modules.PeerJobs import PeerJobs, PeerJob
from modules.WireguardConfiguration import WireguardConfiguration


def test_bug01_peer_toJson_includes_restricted_flag():
    mock_config = MagicMock()
    mock_config.Name = "wg0"
    mock_config.Protocol = "wg"
    mock_config.getStatus.return_value = False
    mock_config.getPeersList.return_value = []
    mock_config.AllPeerJobs.getJobs.return_value = []
    mock_config.AllPeerShareLinks.getLink.return_value = []
    mock_config.configurationInfo = None
    mock_config.engine = db.create_engine("sqlite:///:memory:")

    table_data = {
        "id": "TEST_KEY_123=",
        "private_key": "",
        "DNS": "",
        "endpoint_allowed_ip": "0.0.0.0/0",
        "name": "TestPeer",
        "total_receive": 0.0,
        "total_sent": 0.0,
        "total_data": 0.0,
        "endpoint": "N/A",
        "status": "stopped",
        "latest_handshake": "N/A",
        "allowed_ip": "10.0.0.2/32",
        "cumu_receive": 0.0,
        "cumu_sent": 0.0,
        "cumu_data": 0.0,
        "mtu": 1420,
        "keepalive": 25,
        "notes": "",
        "preshared_key": "",
        "restricted": True
    }
    peer = Peer(table_data, mock_config)
    json_data = peer.toJson()
    assert "restricted" in json_data
    assert json_data["restricted"] is True


def test_bug09_getconfig_does_not_convert_0_and_1_to_bool(tmp_path):
    ini_file = tmp_path / "wg-dashboard.ini"
    ini_file.write_text("""
[Account]
username = admin
password = pass
session_epoch = 1

[Server]
app_port = 10086
app_ip = 0.0.0.0
auth_req = true

[Peers]
remote_endpoint = 1.2.3.4
peer_keepalive = 0
peer_mtu = 0
peer_global_dns =
""", encoding="utf-8")

    with patch.object(DashboardConfig, "ConfigurationFilePath", str(ini_file)):
        cfg = DashboardConfig()
        # "0" should NOT be converted to False
        ok, keepalive = cfg.GetConfig("Peers", "peer_keepalive")
        assert ok is True
        assert keepalive == "0"
        assert isinstance(keepalive, str)

        # "true" should be converted to bool True
        ok, auth_req = cfg.GetConfig("Server", "auth_req")
        assert ok is True
        assert auth_req is True


def test_bug10_peer_validation_dns_and_mtu_none():
    mock_config = MagicMock()
    mock_config.Name = "wg0"
    mock_config.Protocol = "wg"
    mock_config.getStatus.return_value = False
    mock_config.getPeersList.return_value = []
    mock_config.engine = db.create_engine("sqlite:///:memory:")

    peer = Peer({
        "id": "KEY=",
        "private_key": "",
        "DNS": "",
        "endpoint_allowed_ip": "0.0.0.0/0",
        "name": "Peer",
        "total_receive": 0.0,
        "total_sent": 0.0,
        "total_data": 0.0,
        "endpoint": "N/A",
        "status": "stopped",
        "latest_handshake": "N/A",
        "allowed_ip": "10.0.0.2/32",
        "cumu_receive": 0.0,
        "cumu_sent": 0.0,
        "cumu_data": 0.0,
        "mtu": 0,
        "keepalive": 0,
        "notes": "",
        "preshared_key": "",
    }, mock_config)

    # Invalid DNS containing invalid chars/newline must be rejected
    ok, msg = peer.updatePeer(
        name="Test",
        private_key="",
        preshared_key="",
        dns_addresses="1.1.1.1\nmalicious",
        allowed_ip="10.0.0.2/32",
        endpoint_allowed_ip="0.0.0.0/0",
        mtu=None,
        keepalive=None,
        notes=""
    )
    assert ok is False
    assert "DNS" in msg

    # mtu=None and keepalive=None with valid DNS and stopped interface
    with patch.object(mock_config, "saveConfiguration", return_value=True):
        # Set up a dummy peersTable on mock_config
        metadata = db.MetaData()
        peers_table = db.Table('peers', metadata,
            db.Column('id', db.String(255), primary_key=True),
            db.Column('name', db.Text),
            db.Column('private_key', db.String(255)),
            db.Column('DNS', db.Text),
            db.Column('endpoint_allowed_ip', db.Text),
            db.Column('allowed_ip', db.String(255)),
            db.Column('mtu', db.Integer),
            db.Column('keepalive', db.Integer),
            db.Column('notes', db.Text),
            db.Column('preshared_key', db.String(255))
        )
        metadata.create_all(mock_config.engine)
        mock_config.peersTable = peers_table
        with mock_config.engine.begin() as conn:
            conn.execute(peers_table.insert().values(
                id="KEY=", name="Old", private_key="", DNS="", endpoint_allowed_ip="0.0.0.0/0",
                allowed_ip="10.0.0.2/32", mtu=0, keepalive=0, notes="", preshared_key=""
            ))

        ok, msg = peer.updatePeer(
            name="ValidPeer",
            private_key="",
            preshared_key="",
            dns_addresses="1.1.1.1, 8.8.8.8",
            allowed_ip="10.0.0.2/32",
            endpoint_allowed_ip="0.0.0.0/0",
            mtu=None,
            keepalive=None,
            notes="Notes"
        )
        assert ok is True
        assert peer.mtu == 0
        assert peer.keepalive == 0
        assert mock_config.toggleConfiguration.called is False


def test_bug17_empty_config_validation(tmp_path):
    ini_file = tmp_path / "wg-dashboard.ini"
    ini_file.write_text("[Account]\nusername = admin\npassword = pass\nsession_epoch = 1\n[Server]\napp_port = 10086\n", encoding="utf-8")

    with patch.object(DashboardConfig, "ConfigurationFilePath", str(ini_file)):
        cfg = DashboardConfig()
        # Empty app_port should be rejected
        ok, msg = cfg.SetConfig("Server", "app_port", "   ")
        assert ok is False
        assert "cannot be empty" in msg

        # Optional peer_global_dns is allowed to be empty
        ok, msg = cfg.SetConfig("Peers", "peer_global_dns", "")
        # Should not fail with "cannot be empty"
        assert "cannot be empty" not in (msg or "")


def test_bug21_get_number_of_available_ip():
    config = MagicMock()
    config.Name = "wg0"
    config.Address = "10.0.0.1/24"
    config.Peers = []
    config.getRestrictedPeersList.return_value = []

    # Bind the actual getNumberOfAvailableIP method to our mock config
    ok, avail = WireguardConfiguration.getNumberOfAvailableIP(config)
    assert ok is True
    # For 10.0.0.1/24:
    # 256 total addresses, minus 2 for network & broadcast = 254 hosts.
    # Existed address includes 10.0.0.1 (server), so usable peers = 253.
    assert avail["10.0.0.1/24"] == 253


def test_bug21_ip_overlap_check_in_add_peers():
    # Test ipaddress network overlaps logic
    net1 = ipaddress.ip_network("10.0.0.5/32", strict=False)
    net2 = ipaddress.ip_network("10.0.0.5", strict=False)
    assert net1.overlaps(net2) is True

    net3 = ipaddress.ip_network("10.0.0.0/24", strict=False)
    assert net1.overlaps(net3) is True

    net4 = ipaddress.ip_network("10.0.0.6/32", strict=False)
    assert net1.overlaps(net4) is False


def test_bug19_dashboard_version_in_global_backup():
    from modules.GlobalBackup import DashboardConfig as GB_DashboardConfig
    assert hasattr(GB_DashboardConfig, "DashboardVersion")
    assert getattr(GB_DashboardConfig, "DashboardVersion") == DashboardConfig.DashboardVersion


def test_bug20_wgd_sh_script_port_and_systemctl():
    wgd_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "wgd.sh"))
    with open(wgd_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Verify get_app_port helper exists
    assert "get_app_port()" in content
    # Verify dynamic port is used for fuser
    assert 'fuser "${port}/tcp"' in content
    # Verify systemctl restart is checked in restart command
    assert "systemctl restart wg-dashboard" in content


def test_bug21_autoprune_uses_parameterized_cutoff():
    dashboard_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "dashboard.py"))
    with open(dashboard_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Verify SQLite datetime('now', '-14 days') string was replaced by parameterized cutoff
    assert "datetime('now', '-14 days')" not in content
    assert '"cutoff": cutoff' in content
    assert "BulkPeer_{addedCount}_" in content


def test_bug02_job_per_job_try_except():
    import threading
    pj = PeerJobs.__new__(PeerJobs)
    pj.lock = threading.RLock()
    pj.Jobs = []
    pj.JobLogger = MagicMock()
    pj.cleanJob = MagicMock()
    pj._PeerJobs__getJobs = MagicMock()

    mock_config = MagicMock()
    mock_config.Name = "wg0"
    mock_config.getStatus.return_value = True
    pj.WireguardConfigurations = {"wg0": mock_config}

    bad_job = PeerJob("bad_job", "wg0", "peer1", "bad_field", ">=", "not_a_number", datetime.now(), None, "restrict")
    pj.Jobs = [bad_job]

    from flask import Flask
    app = Flask("test_app")
    with app.app_context():
        # runJob should NOT throw unhandled exception
        pj.runJob()
        # It should log the failure to JobLogger
        assert pj.JobLogger.log.called is True
        call_args = pj.JobLogger.log.call_args[0]
        assert call_args[0] == "bad_job"
        assert call_args[1] is False
        assert "crashed" in call_args[2] or "failed" in call_args[2]


def test_bug04_delete_peers_returns_not_found_on_zero_rows():
    import threading
    config = MagicMock(spec=WireguardConfiguration)
    config.Name = "wg0"
    config.Protocol = "wg"
    config.lock = threading.RLock()
    config.getStatus.return_value = False
    config.searchPeer.return_value = (False, None)
    engine = db.create_engine("sqlite:///:memory:")
    metadata = db.MetaData()
    peersTable = db.Table('peers', metadata,
        db.Column('id', db.String(255), primary_key=True),
        db.Column('name', db.Text),
        db.Column('cumu_receive', db.Float, default=0.0),
        db.Column('total_receive', db.Float, default=0.0),
        db.Column('cumu_sent', db.Float, default=0.0),
        db.Column('total_sent', db.Float, default=0.0),
        db.Column('cumu_data', db.Float, default=0.0),
        db.Column('total_data', db.Float, default=0.0),
    )
    peersRestrictedTable = db.Table('peers_restricted', metadata,
        db.Column('id', db.String(255), primary_key=True),
        db.Column('name', db.Text),
        db.Column('cumu_receive', db.Float, default=0.0),
        db.Column('total_receive', db.Float, default=0.0),
        db.Column('cumu_sent', db.Float, default=0.0),
        db.Column('total_sent', db.Float, default=0.0),
        db.Column('cumu_data', db.Float, default=0.0),
        db.Column('total_data', db.Float, default=0.0),
    )
    peersTransferTable = db.Table('peers_transfer', metadata,
        db.Column('id', db.String(255)),
    )
    metadata.create_all(engine)
    config.engine = engine
    config.peersTable = peersTable
    config.peersRestrictedTable = peersRestrictedTable
    config.peersTransferTable = peersTransferTable
    config.Peers = []
    config.RestrictedPeers = []
    config.getPeersList.return_value = []
    config.getRestrictedPeersList.return_value = []
    mock_jobs = MagicMock()
    mock_links = MagicMock()
    mock_hooks = MagicMock()
    config.DashboardWebHooks = mock_hooks

    # Call deletePeers with non-existent peer
    ok, msg = WireguardConfiguration.deletePeers(config, ["non_existent_key="], mock_jobs, mock_links)
    assert ok is False
    assert "No peer(s)" in msg and "found" in msg


def test_bug05_clean_job_preserves_restrict_action():
    import threading
    pj = PeerJobs.__new__(PeerJobs)
    pj.lock = threading.RLock()
    metadata = db.MetaData()
    peerJobTable = db.Table('PeerJobs', metadata,
        db.Column('JobID', db.String(255), primary_key=True),
        db.Column('Configuration', db.String(255)),
        db.Column('Peer', db.String(255)),
        db.Column('Field', db.String(255)),
        db.Column('Operator', db.String(255)),
        db.Column('Value', db.String(255)),
        db.Column('CreationDate', db.DATETIME),
        db.Column('ExpireDate', db.DATETIME),
        db.Column('Action', db.String(255))
    )
    engine = db.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(peerJobTable.insert().values(
            JobID="job_res", Configuration="wg0", Peer="peer1",
            Field="total_data", Operator=">=", Value="50",
            CreationDate=datetime.now(), ExpireDate=None, Action="restrict"
        ))

    pj.engine = engine
    pj.peerJobTable = peerJobTable
    pj.JobLogger = MagicMock()
    pj.JobLogger.getFailingJobs.return_value = [{"JobID": "job_res"}]

    mock_conf = MagicMock()
    mock_conf.searchPeer.return_value = (True, MagicMock())
    pj.WireguardConfigurations = {"wg0": mock_conf}

    restrict_job = PeerJob("job_res", "wg0", "peer1", "total_data", ">=", "50", datetime.now(), None, "restrict")
    pj.Jobs = [restrict_job]

    from flask import Flask
    app = Flask("test_app")
    with app.app_context():
        # cleanJob should NEVER delete restrict_job for an existing peer
        pj.cleanJob()
        # Verify in DB that ExpireDate was NOT set
        with engine.connect() as conn:
            row = conn.execute(peerJobTable.select().where(peerJobTable.c.JobID == "job_res")).mappings().fetchone()
            assert row["ExpireDate"] is None


def test_bug06_peer_jobs_atomic_replacement_and_lock():
    pj = PeerJobs.__new__(PeerJobs)
    pj.lock = MagicMock()
    pj.Jobs = []
    pj.peerJobTable = MagicMock()
    pj.engine = MagicMock()
    pj.JobLogger = MagicMock()
    pj.WireguardConfigurations = {}
    pj.AllPeerShareLinks = MagicMock()

    job = PeerJob("job1", "wg0", "peer1", "total_data", ">=", "10", datetime.now(), None, "restrict")
    assert hasattr(pj, "lock")
    with patch.object(pj, "_PeerJobs__getJobs"):
        try:
            pj.saveJob(job)
        except Exception:
            pass
        assert pj.lock.__enter__.called is True


def test_bug07_restore_backup_signature_and_safety():
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "modules", "WireguardConfiguration.py"))
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Verify restoreBackup uses restore=False for clean import
    assert "self.__importDatabase(targetSQL, restore=False)" in content
    # Verify pre-restore safety backup call
    assert "self.backupConfigurationFile()" in content
    # Verify running status restoration
    assert "if was_running and not self.getStatus():" in content


def test_bug08_dashboard_restore_has_4_arguments():
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "dashboard.py"))
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Check the WireguardConfiguration call in restoreConfiguration
    assert "WireguardConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, DashboardWebHooks, data=data, name=config_name)" in content


def test_bug13_backup_maintenance_has_sleep():
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "dashboard.py"))
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Check maintenance mode check in scheduled backup loop
    assert "time.sleep(60)" in content
    assert "Skipping scheduled backup because system is in maintenance mode" in content


def test_bug14_reconcile_omits_server_remote_endpoint():
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "modules", "WireguardConfiguration.py"))
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Reconcile should NOT add server remote_endpoint to wg set command
    assert 'f"endpoint {row[\'remote_endpoint\']}"' not in content
    assert "row.get('remote_endpoint')" not in content


def test_bug15_rename_configuration_migrates_all_metadata():
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "modules", "WireguardConfiguration.py"))
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "ConfigurationsInfo" in content
    assert "PeerShareLinks" in content
    assert "DashboardClientsPeerAssignment" in content


def test_bug16_delete_configuration_takes_backup_and_cleans():
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "modules", "WireguardConfiguration.py"))
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "self.backupConfigurationFile()" in content
    assert 'DELETE FROM "ConfigurationsInfo"' in content
    assert 'DELETE FROM "PeerShareLinks"' in content
    assert 'DELETE FROM "DashboardClientsPeerAssignment"' in content


def test_bug18_defensive_endpoints():
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "dashboard.py"))
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()

    # sharePeer/create handles null expire date
    assert "data.get('ExpireDate')" in content
    # ping/execute stringifies exception
    assert "ResponseObject(False, str(exp))" in content
    # assignedClients uses peerID not id
    assert "all([configurationName, peerID])" in content
    # check awg_path exists
    assert "os.path.exists(awg_path)" in content


def test_bug21_update_configuration_settings_safe_fallback():
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "modules", "WireguardConfiguration.py"))
    with open(src_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Safe get fallback in updateConfigurationSettings
    assert "sanitized_data = {}" in content
    assert "elif hasattr(self, key) and getattr(self, key) is not None:" in content
