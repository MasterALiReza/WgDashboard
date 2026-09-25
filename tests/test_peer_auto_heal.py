import sys
import os
import pytest
import sqlalchemy as db
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from modules.Peer import Peer
from modules.Utilities import CheckPeerKey, GenerateWireguardPublicKey


class MockConfigInfo:
    class OverrideSettings:
        MTU = None
        DNS = None
        EndpointAllowedIPs = "0.0.0.0/0, ::/0"
        PeerRemoteEndpoint = "vpn.example.com"
        ListenPort = "51820"
        PersistentKeepalive = None
    OverridePeerSettings = OverrideSettings()


class MockWireguardConfiguration:
    def __init__(self, engine):
        self.Name = "wg0"
        self.Protocol = "wg"
        self.Status = False
        self.PublicKey = "SERVER_PUBKEY_TEST_TEST_TEST_TEST_TEST_TEST="
        self.ListenPort = "51820"
        self.configPath = "/tmp/wg0.conf"
        self.engine = engine
        self.metadata = db.MetaData()
        self.configurationInfo = MockConfigInfo()
        self.DashboardConfig = MagicMock()
        self.DashboardConfig.GetConfig.return_value = (True, "vpn.example.com")
        self.AllPeerJobs = MagicMock()
        self.AllPeerJobs.Jobs = []
        self.AllPeerShareLinks = MagicMock()
        self.AllPeerShareLinks.ShareLinks = []
        self._peers_dict = {}

        self.peersTable = db.Table(
            'peers', self.metadata,
            db.Column('id', db.String(255), primary_key=True),
            db.Column('private_key', db.String(255)),
            db.Column('DNS', db.Text),
            db.Column('endpoint_allowed_ip', db.Text),
            db.Column('name', db.Text),
            db.Column('total_receive', db.Float, default=0.0),
            db.Column('total_sent', db.Float, default=0.0),
            db.Column('total_data', db.Float, default=0.0),
            db.Column('endpoint', db.String(255), default="N/A"),
            db.Column('status', db.String(255), default="stopped"),
            db.Column('latest_handshake', db.String(255), default="N/A"),
            db.Column('allowed_ip', db.String(255)),
            db.Column('cumu_receive', db.Float, default=0.0),
            db.Column('cumu_sent', db.Float, default=0.0),
            db.Column('cumu_data', db.Float, default=0.0),
            db.Column('mtu', db.Integer),
            db.Column('keepalive', db.Integer),
            db.Column('notes', db.Text),
            db.Column('remote_endpoint', db.String(255)),
            db.Column('preshared_key', db.String(255)),
            db.Column('restricted_reason', db.String(255))
        )
        self.peerShareLinksTable = db.Table(
            'peerShareLinks', self.metadata,
            db.Column('ShareID', db.String(255), primary_key=True),
            db.Column('Peer', db.String(255)),
            db.Column('Configuration', db.String(255))
        )
        self.metadata.create_all(self.engine)

    def getStatus(self):
        return self.Status

    def saveConfiguration(self):
        return True


@pytest.fixture
def mock_peer_env():
    engine = db.create_engine("sqlite:///:memory:")
    config = MockWireguardConfiguration(engine)
    old_pub = "OLD_PEER_PUBLIC_KEY_12345678901234567890123="
    with engine.begin() as conn:
        conn.execute(config.peersTable.insert().values({
            "id": old_pub,
            "private_key": "",
            "DNS": "1.1.1.1",
            "endpoint_allowed_ip": "0.0.0.0/0",
            "name": "TestPeer",
            "allowed_ip": "10.0.0.2/32",
            "total_receive": 0.0,
            "total_sent": 0.0,
            "total_data": 0.0,
            "latest_handshake": "No Handshake",
            "status": "stopped",
            "mtu": 1420,
            "keepalive": 25,
            "preshared_key": ""
        }))

    peer_data = {
        "id": old_pub,
        "private_key": "",
        "DNS": "1.1.1.1",
        "endpoint_allowed_ip": "0.0.0.0/0",
        "name": "TestPeer",
        "allowed_ip": "10.0.0.2/32",
        "total_receive": 0.0,
        "total_sent": 0.0,
        "total_data": 0.0,
        "latest_handshake": "No Handshake",
        "status": "stopped",
        "mtu": 1420,
        "keepalive": 25,
        "preshared_key": ""
    }
    peer = Peer(peer_data, config)
    config.Peers = [peer]
    config._peers_dict[old_pub] = peer
    return config, peer


class TestPeerAutoHeal:
    def test_auto_heal_generates_valid_keys_and_updates_db(self, mock_peer_env):
        config, peer = mock_peer_env
        old_id = peer.id
        assert peer.private_key == ""

        # Run autoHealKeys
        healed = peer.autoHealKeys()
        assert healed is True
        assert peer.id != old_id
        assert CheckPeerKey(peer.private_key) is True
        assert CheckPeerKey(peer.id) is True

        # Verify derivation: peer.id == GenerateWireguardPublicKey(peer.private_key)
        _, derived_pub = GenerateWireguardPublicKey(peer.private_key)
        assert peer.id == derived_pub

        # Verify DB updated
        with config.engine.connect() as conn:
            rows = conn.execute(config.peersTable.select()).mappings().fetchall()
            assert len(rows) == 1
            assert rows[0]["id"] == peer.id
            assert rows[0]["private_key"] == peer.private_key

    def test_download_peer_triggers_auto_heal_when_missing_key(self, mock_peer_env):
        config, peer = mock_peer_env
        assert peer.private_key == ""

        dl = peer.downloadPeer()
        assert "file" in dl
        assert "error" not in dl or dl.get("error") is None
        assert f"PrivateKey = {peer.private_key}" in dl["file"]
        assert f"Address = {peer.allowed_ip}" in dl["file"]
        assert f"PublicKey = {config.PublicKey}" in dl["file"]

    def test_download_peer_sanitizes_preshared_key(self, mock_peer_env):
        config, peer = mock_peer_env
        # Set invalid / dummy PSKs
        for bad_psk in ["None", "null", "(none)", "N/A", "   "]:
            peer.preshared_key = bad_psk
            dl = peer.downloadPeer()
            assert "PresharedKey = None" not in dl["file"]
            assert "PresharedKey = null" not in dl["file"]
            assert "PresharedKey = (none)" not in dl["file"]
            assert "PresharedKey = N/A" not in dl["file"]

    def test_download_peer_sanitizes_mtu_and_keepalive(self, mock_peer_env):
        config, peer = mock_peer_env
        peer.mtu = 0
        peer.keepalive = -1
        dl = peer.downloadPeer()
        assert "MTU = 0" not in dl["file"]
        assert "PersistentKeepalive = -1" not in dl["file"]

    def test_download_peer_flags_error_if_key_cannot_be_healed(self, mock_peer_env):
        config, peer = mock_peer_env
        # Simulate an active connected peer with client-side key (traffic exists)
        peer.latest_handshake = "1 minute ago"
        peer.total_receive = 500000.0
        peer.total_sent = 200000.0
        peer.private_key = ""

        dl = peer.downloadPeer()
        assert "error" in dl
        assert "PrivateKey is missing" in dl["error"]

    def test_configuration_heal_peers_with_missing_private_keys(self, mock_peer_env):
        config, peer = mock_peer_env
        assert peer.private_key == ""
        # Mock healPeersWithMissingPrivateKeys behavior
        from modules.WireguardConfiguration import WireguardConfiguration
        healed_count = WireguardConfiguration.healPeersWithMissingPrivateKeys(config)
        assert healed_count == 1
        assert peer.private_key != ""
        assert CheckPeerKey(peer.private_key) is True
        assert CheckPeerKey(peer.id) is True

    def test_configuration_save_configuration_method(self, mock_peer_env):
        config, peer = mock_peer_env
        from modules.WireguardConfiguration import WireguardConfiguration
        # Mock __wgSave
        setattr(config, '_WireguardConfiguration__wgSave', lambda: True)
        assert WireguardConfiguration.saveConfiguration(config) is True
