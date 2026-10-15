import sys
import os
import hmac
import hashlib
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from modules.DashboardAPIKey import DashboardAPIKey
from modules.DashboardWebHooks import DashboardWebHooks, WebHookSession, WebHook, WebHookActions
from modules.PeerJobs import _parse_job_datetime, PeerJob
from modules.Peer import Peer
from modules.Utilities import GenerateWireguardPrivateKey, GenerateWireguardPublicKey
import sqlalchemy as db


@pytest.fixture
def api_auth(monkeypatch):
    from dashboard import DashboardConfig
    test_key = DashboardAPIKey(
        Key="test_oxbot_valid_key",
        CreatedAt="2026-01-01",
        ExpiredAt="None",
        AllowedConfigurations="*",
        Scope="admin"
    )
    monkeypatch.setattr(DashboardConfig, "DashboardAPIKeys", [test_key])

    orig_get_config = DashboardConfig.GetConfig
    def mock_get_config(section, key):
        if section == "Server" and key == "dashboard_api_key":
            return True, True
        return orig_get_config(section, key)
    monkeypatch.setattr(DashboardConfig, "GetConfig", mock_get_config)
    return {"wg-dashboard-apikey": "test_oxbot_valid_key"}


class TestOxBotIntegration:
    def test_parse_job_datetime_iso8601_and_epoch(self):
        # 1. Standard format
        dt1, is_utc1 = _parse_job_datetime("2026-11-01 12:00:00")
        assert is_utc1 is False
        assert dt1.year == 2026 and dt1.month == 11 and dt1.day == 1

        # 2. ISO-8601 UTC with 'Z'
        dt2, is_utc2 = _parse_job_datetime("2026-11-01T12:00:00Z")
        assert is_utc2 is True
        assert dt2.tzinfo is not None

        # 3. ISO-8601 with Tehran timezone offset +03:30
        dt3, is_utc3 = _parse_job_datetime("2026-11-01T15:30:00+03:30")
        assert is_utc3 is True
        utc_dt3 = dt3.astimezone(timezone.utc)
        assert utc_dt3.hour == 12 and utc_dt3.minute == 0

        # 4. UNIX epoch timestamp string
        epoch_ts = "1793448000"
        dt4, is_utc4 = _parse_job_datetime(epoch_ts)
        assert is_utc4 is True
        assert dt4.timestamp() == pytest.approx(1793448000, 1.0)

    def test_apikey_scoping_configuration(self):
        key_wildcard = DashboardAPIKey(Key="key1", CreatedAt="2026-01-01", ExpiredAt="None", AllowedConfigurations="*", Scope="admin")
        assert key_wildcard.is_configuration_allowed("wg0") is True
        assert key_wildcard.is_configuration_allowed("awg0") is True

        key_scoped = DashboardAPIKey(Key="key2", CreatedAt="2026-01-01", ExpiredAt="None", AllowedConfigurations="wg0, wg1", Scope="peers")
        assert key_scoped.is_configuration_allowed("wg0") is True
        assert key_scoped.is_configuration_allowed("wg1") is True
        assert key_scoped.is_configuration_allowed("wg2") is False

    def test_apikey_scoping_permissions(self):
        admin_key = DashboardAPIKey(Key="key1", CreatedAt="2026-01-01", ExpiredAt="None", Scope="admin")
        assert admin_key.is_scope_allowed("read") is True
        assert admin_key.is_scope_allowed("peers") is True
        assert admin_key.is_scope_allowed("admin") is True

        peers_key = DashboardAPIKey(Key="key2", CreatedAt="2026-01-01", ExpiredAt="None", Scope="peers")
        assert peers_key.is_scope_allowed("read") is True
        assert peers_key.is_scope_allowed("peers") is True
        assert peers_key.is_scope_allowed("admin") is False

        read_key = DashboardAPIKey(Key="key3", CreatedAt="2026-01-01", ExpiredAt="None", Scope="read")
        assert read_key.is_scope_allowed("read") is True
        assert read_key.is_scope_allowed("peers") is False
        assert read_key.is_scope_allowed("admin") is False

    def test_webhook_actions_contain_new_events(self):
        assert "peer_restricted" in WebHookActions
        assert "peer_allowed" in WebHookActions
        assert "peer_usage_reset" in WebHookActions

    def test_webhook_hmac_sha256_signature(self):
        session = WebHookSession.__new__(WebHookSession)
        session.webHookSessionLogs = MagicMock()
        session.sessionID = "sess1"
        session.webHook = MagicMock()
        session.webHook.PayloadURL = "https://example.com/webhook"
        session.webHook.Method = "POST"
        session.webHook.ContentType = "application/json"
        session.webHook.Headers = {}
        session.webHook.Secret = "my_oxbot_super_secret"
        session.webHook.VerifySSL = True
        session.data = {"configuration": "wg0", "peers": ["test_peer_id"]}
        session.UpdateSessionLog = MagicMock()
        session.UpdateStatus = MagicMock()

        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "OK"
            mock_post.return_value = mock_resp

            session.Execute()
            assert mock_post.called

            call_kwargs = mock_post.call_args[1]
            headers = call_kwargs.get("headers", {})
            assert "X-WGD-Timestamp" in headers
            assert "X-WGD-Signature" in headers

            sig_header = headers["X-WGD-Signature"]
            assert sig_header.startswith("sha256=")
            ts = headers["X-WGD-Timestamp"]
            expected_sig = "sha256=" + hmac.new(
                session.webHook.Secret.encode("utf-8"),
                f"{ts}.{call_kwargs.get('data')}".encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            assert sig_header == expected_sig

    def test_restrict_peers_already_restricted_and_preserve_reason(self):
        from modules.WireguardConfiguration import WireguardConfiguration

        _, pub_key = GenerateWireguardPublicKey(GenerateWireguardPrivateKey()[1])
        mock_config = MagicMock()
        mock_jobs = MagicMock()
        mock_share = MagicMock()
        mock_webhooks = MagicMock()

        engine = db.create_engine("sqlite:///:memory:")
        wgc = WireguardConfiguration.__new__(WireguardConfiguration)
        wgc.Name = "wg0"
        wgc.Protocol = "wg"
        wgc.configPath = "/tmp/wg0.conf"
        wgc.DashboardConfig = mock_config
        wgc.AllPeerJobs = mock_jobs
        wgc.AllPeerShareLinks = mock_share
        wgc.DashboardWebHooks = mock_webhooks
        wgc.engine = engine
        wgc.metadata = db.MetaData()
        wgc.lock = MagicMock()
        wgc.lock.__enter__.return_value = None
        wgc.lock.__exit__.return_value = None
        wgc._force_refresh_stats = MagicMock()
        wgc.getStatus = MagicMock(return_value=False)
        wgc._WireguardConfiguration__wgSave = MagicMock(return_value=True)
        wgc.getPeers = MagicMock()
        wgc.getRestrictedPeers = MagicMock()

        wgc.createDatabase("wg0")
        wgc.metadata.create_all(engine)
        wgc.Peers = []
        wgc.RestrictedPeers = []

        table_data = {
            "id": pub_key,
            "private_key": "PRIV1",
            "DNS": "1.1.1.1",
            "endpoint_allowed_ip": "0.0.0.0/0",
            "name": "User1",
            "total_receive": 1.0,
            "total_sent": 1.0,
            "total_data": 2.0,
            "endpoint": "",
            "status": "running",
            "latest_handshake": "",
            "allowed_ip": "10.0.0.2/32",
            "cumu_receive": 0.0,
            "cumu_sent": 0.0,
            "cumu_data": 0.0,
            "mtu": 1420,
            "keepalive": 25,
            "notes": "",
            "preshared_key": "",
            "restricted_reason": ""
        }
        with engine.begin() as conn:
            conn.execute(wgc.peersTable.insert().values(table_data))
        peer_obj = Peer(table_data, wgc)
        wgc.Peers = [peer_obj]
        wgc.getPeersList = MagicMock(return_value=wgc.Peers)

        # 1. Restrict active peer
        ok, msg = wgc.restrictPeers([pub_key], reason="Volume Limit Reached")
        assert ok is True
        assert "Restricted 1 peer(s)" in msg
        assert mock_webhooks.RunWebHook.called

        # 2. Peer is now restricted
        wgc.Peers = []
        peer_obj.restricted_reason = "Volume Limit Reached"
        wgc.RestrictedPeers = [peer_obj]
        wgc.getPeersList = MagicMock(return_value=[])

        # Calling restrictPeers again on already restricted peer returns True without error
        ok2, msg2 = wgc.restrictPeers([pub_key])
        assert ok2 is True
        assert "Specified peer(s) are already restricted" in msg2

    def test_allow_access_peers_volume_guard(self):
        from modules.WireguardConfiguration import WireguardConfiguration

        _, pub_key = GenerateWireguardPublicKey(GenerateWireguardPrivateKey()[1])
        mock_config = MagicMock()
        mock_jobs = MagicMock()
        mock_share = MagicMock()
        mock_webhooks = MagicMock()

        engine = db.create_engine("sqlite:///:memory:")
        wgc = WireguardConfiguration.__new__(WireguardConfiguration)
        wgc.Name = "wg0"
        wgc.Protocol = "wg"
        wgc.configPath = "/tmp/wg0.conf"
        wgc.DashboardConfig = mock_config
        wgc.AllPeerJobs = mock_jobs
        wgc.AllPeerShareLinks = mock_share
        wgc.DashboardWebHooks = mock_webhooks
        wgc.engine = engine
        wgc.metadata = db.MetaData()
        wgc.lock = MagicMock()
        wgc.lock.__enter__.return_value = None
        wgc.lock.__exit__.return_value = None
        wgc._force_refresh_stats = MagicMock()
        wgc.getStatus = MagicMock(return_value=False)
        wgc._WireguardConfiguration__wgSave = MagicMock(return_value=True)
        wgc.getPeers = MagicMock()
        wgc.getRestrictedPeers = MagicMock()

        wgc.createDatabase("wg0")
        wgc.metadata.create_all(engine)
        wgc.Peers = []
        wgc.RestrictedPeers = []

        table_data = {
            "id": pub_key,
            "private_key": "PRIV_V",
            "DNS": "1.1.1.1",
            "endpoint_allowed_ip": "0.0.0.0/0",
            "name": "VolUser",
            "total_receive": 5.0,
            "total_sent": 5.0,
            "total_data": 10.0,
            "endpoint": "",
            "status": "stopped",
            "latest_handshake": "",
            "allowed_ip": "10.0.0.3/32",
            "cumu_receive": 0.0,
            "cumu_sent": 0.0,
            "cumu_data": 0.0,
            "mtu": 1420,
            "keepalive": 25,
            "notes": "",
            "preshared_key": "",
            "restricted_reason": "Volume Limit Reached"
        }
        with engine.begin() as conn:
            conn.execute(wgc.peersRestrictedTable.insert().values(table_data))
        p_obj = Peer(table_data, wgc)
        wgc.RestrictedPeers = [p_obj]

        # 1. allowAccessPeers with force=False MUST fail (BOT-04 prevention)
        ok, msg = wgc.allowAccessPeers([pub_key], force=False)
        assert ok is False
        assert "volume limit" in msg.lower()

        # 2. allowAccessPeers with force=True succeeds (e.g. renewal or manual override)
        ok2, msg2 = wgc.allowAccessPeers([pub_key], force=True)
        assert ok2 is True
        assert "Allow access successfully" in msg2
        assert mock_webhooks.RunWebHook.called

    def test_v2_peer_endpoint(self, api_auth):
        from dashboard import app, WireguardConfigurations

        _, pub_key = GenerateWireguardPublicKey(GenerateWireguardPrivateKey()[1])
        mock_wgc = MagicMock()
        mock_wgc.Name = "wg0"
        mock_peer = MagicMock()
        mock_peer.id = pub_key
        mock_peer.name = "TestOxPeer"
        mock_peer.allowed_ip = "10.0.0.5/32"
        mock_peer.restricted = False
        mock_peer.restricted_reason = ""
        mock_peer.status = "running"
        mock_peer.endpoint = "1.2.3.4:51820"
        mock_peer.latest_handshake = "1 minute ago"
        mock_peer.cumu_receive = 1.5
        mock_peer.total_receive = 0.5
        mock_peer.cumu_sent = 0.5
        mock_peer.total_sent = 0.5

        mock_wgc.searchPeer.return_value = (True, mock_peer)
        mock_wgc.RestrictedPeers = []
        mock_wgc.Peers = [mock_peer]

        with patch.dict(WireguardConfigurations, {"wg0": mock_wgc}):
            client = app.test_client()
            res = client.get(f"/api/v2/peer?configurationName=wg0&id={pub_key}", headers=api_auth)
            assert res.status_code == 200
            data = res.get_json()["data"]
            assert data["id"] == pub_key
            assert data["name"] == "TestOxPeer"
            assert data["usage"]["total_gb"] == 3.0  # (1.5+0.5) + (0.5+0.5) = 3.0
            assert data["restricted"] is False

            # Test 404 for unknown peer
            mock_wgc.searchPeer.return_value = (False, None)
            res404 = client.get("/api/v2/peer?configurationName=wg0&id=unknown", headers=api_auth)
            assert res404.status_code == 404

    def test_v2_peers_usage_etag_and_304(self, api_auth):
        from dashboard import app, WireguardConfigurations

        _, pub_key = GenerateWireguardPublicKey(GenerateWireguardPrivateKey()[1])
        mock_wgc = MagicMock()
        mock_wgc.Name = "wg0"
        mock_peer = MagicMock()
        mock_peer.id = pub_key
        mock_peer.name = "TestUsagePeer"
        mock_peer.restricted = False
        mock_peer.cumu_receive = 2.0
        mock_peer.total_receive = 0.0
        mock_peer.cumu_sent = 1.0
        mock_peer.total_sent = 0.0
        mock_peer.latest_handshake = "5 mins ago"

        mock_wgc.getRestrictedPeersList.return_value = []
        mock_wgc.Peers = [mock_peer]

        with patch.dict(WireguardConfigurations, {"wg0": mock_wgc}):
            client = app.test_client()
            res = client.get("/api/v2/peers/usage?configurationName=wg0", headers=api_auth)
            assert res.status_code == 200
            assert "ETag" in res.headers
            etag = res.headers["ETag"]

            # Sending same ETag should return 304 Not Modified
            res304 = client.get("/api/v2/peers/usage?configurationName=wg0", headers={**api_auth, "If-None-Match": etag})
            assert res304.status_code == 304

    def test_v2_create_peer_idempotency_key(self, api_auth):
        from dashboard import app, WireguardConfigurations

        mock_wgc = MagicMock()
        mock_wgc.Name = "wg0"
        mock_wgc.lock = MagicMock()
        mock_wgc.lock.__enter__.return_value = None
        mock_wgc.lock.__exit__.return_value = None
        mock_wgc.getAvailableIP.return_value = (True, {"10.0.0.0/24": ["10.0.0.15"]})

        mock_created_peer = MagicMock()
        mock_created_peer.id = "CREATED_PUBKEY_123"
        mock_created_peer.toJson.return_value = {"id": "CREATED_PUBKEY_123", "name": "Buyer1"}
        mock_created_peer.downloadPeer.return_value = {"file": "[Interface]\nPrivateKey = ..."}

        mock_wgc.addPeers.return_value = (True, [mock_created_peer], "")

        with patch.dict(WireguardConfigurations, {"wg0": mock_wgc}):
            client = app.test_client()
            idem_key = "invoice_unique_9988"
            payload = {
                "configurationName": "wg0",
                "name": "Buyer1",
                "limits": {"data_limit_gb": 50.0}
            }

            headers = {**api_auth, "Idempotency-Key": idem_key}
            res1 = client.post("/api/v2/peers", json=payload, headers=headers)
            assert res1.status_code == 200
            data1 = res1.get_json()["data"]
            assert data1["peer"]["name"] == "Buyer1"
            assert mock_wgc.addPeers.call_count == 1

            # Second call with same key replays cached response
            res2 = client.post("/api/v2/peers", json=payload, headers=headers)
            assert res2.status_code == 200
            assert "idempotent replay" in res2.get_json()["message"].lower()
            assert mock_wgc.addPeers.call_count == 1

    def test_api_key_auth_enforces_read_scope_and_config_scope(self, monkeypatch):
        from dashboard import app, DashboardConfig, WireguardConfigurations

        mock_wgc = MagicMock()
        mock_wgc.Name = "wg0"
        mock_wgc.Peers = []
        mock_wgc.getRestrictedPeersList.return_value = []

        # Read-only API Key
        read_key = DashboardAPIKey(Key="read_only_key", CreatedAt="2026-01-01", ExpiredAt="None", AllowedConfigurations="wg0", Scope="read")
        monkeypatch.setattr(DashboardConfig, "DashboardAPIKeys", [read_key])

        orig_get_config = DashboardConfig.GetConfig
        def mock_get_config(section, key):
            if section == "Server" and key == "dashboard_api_key":
                return True, True
            return orig_get_config(section, key)
        monkeypatch.setattr(DashboardConfig, "GetConfig", mock_get_config)

        with patch.dict(WireguardConfigurations, {"wg0": mock_wgc}):
            client = app.test_client()

            # 1. GET is allowed for read scope
            res_get = client.get("/api/v2/peers/usage?configurationName=wg0", headers={"wg-dashboard-apikey": "read_only_key"})
            assert res_get.status_code == 200

            # 2. POST is rejected with 403 Forbidden for read scope
            res_post = client.post("/api/v2/peers", json={"configurationName": "wg0", "name": "Test"}, headers={"wg-dashboard-apikey": "read_only_key"})
            assert res_post.status_code == 403
            assert "read-only scope" in res_post.get_json()["message"]

            # 3. Access to unauthorized config 'wg1' is rejected with 403 Forbidden
            res_unauth_config = client.get("/api/v2/peers/usage?configurationName=wg1", headers={"wg-dashboard-apikey": "read_only_key"})
            assert res_unauth_config.status_code == 403
            assert "not authorized for configuration" in res_unauth_config.get_json()["message"]

    def test_v2_peer_renew(self, api_auth):
        from dashboard import app, WireguardConfigurations, AllPeerJobs

        _, pub_key = GenerateWireguardPublicKey(GenerateWireguardPrivateKey()[1])
        mock_wgc = MagicMock()
        mock_wgc.Name = "wg0"
        mock_wgc.lock = MagicMock()
        mock_wgc.lock.__enter__.return_value = None
        mock_wgc.lock.__exit__.return_value = None

        mock_peer = MagicMock()
        mock_peer.id = pub_key
        mock_peer.resetDataUsage.return_value = True

        mock_wgc.searchPeer.return_value = (False, None)
        mock_wgc.RestrictedPeers = [mock_peer]

        with patch.dict(WireguardConfigurations, {"wg0": mock_wgc}):
            client = app.test_client()
            payload = {
                "configurationName": "wg0",
                "id": pub_key,
                "reset_usage": True,
                "data_limit_gb": 100.0,
                "expire_at": "2026-12-01 00:00:00"
            }
            res = client.post("/api/v2/peer/renew", json=payload, headers=api_auth)
            assert res.status_code == 200
            assert "renewed successfully" in res.get_json()["message"].lower()
            assert mock_peer.resetDataUsage.called
            assert mock_wgc.allowAccessPeers.called

    def test_v2_peer_limits_extra_volume_and_time(self, api_auth):
        from dashboard import app, WireguardConfigurations, AllPeerJobs

        _, pub_key = GenerateWireguardPublicKey(GenerateWireguardPrivateKey()[1])
        mock_wgc = MagicMock()
        mock_wgc.Name = "wg0"
        mock_wgc.lock = MagicMock()
        mock_wgc.lock.__enter__.return_value = None
        mock_wgc.lock.__exit__.return_value = None

        mock_peer = MagicMock()
        mock_peer.id = pub_key
        mock_peer.cumu_data = 20.0
        mock_peer.total_data = 5.0  # current usage = 25GB
        mock_peer.restricted_reason = "Volume Limit Reached"

        mock_wgc.searchPeer.return_value = (False, None)
        mock_wgc.RestrictedPeers = [mock_peer]

        with patch.dict(WireguardConfigurations, {"wg0": mock_wgc}):
            client = app.test_client()

            # 1. Add extra volume (10 GB) -> new limit becomes 25 + 10 = 35 GB
            payload_vol = {
                "configurationName": "wg0",
                "id": pub_key,
                "add_data_gb": 10.0
            }
            res_vol = client.post("/api/v2/peer/limits", json=payload_vol, headers=api_auth)
            assert res_vol.status_code == 200
            assert mock_wgc.allowAccessPeers.called

            # 2. Add extra days (30 days)
            payload_days = {
                "configurationName": "wg0",
                "id": pub_key,
                "add_days": 30
            }
            res_days = client.post("/api/v2/peer/limits/add", json=payload_days, headers=api_auth)
            assert res_days.status_code == 200

    def test_v2_peers_bulk_actions(self, api_auth):
        from dashboard import app, WireguardConfigurations

        mock_wgc = MagicMock()
        mock_wgc.Name = "wg0"
        mock_wgc.lock = MagicMock()
        mock_wgc.lock.__enter__.return_value = None
        mock_wgc.lock.__exit__.return_value = None

        mock_wgc.restrictPeers.return_value = (True, "Restricted 2 peer(s)")
        mock_wgc.allowAccessPeers.return_value = (True, "Allowed 2 peer(s)")
        mock_wgc.deletePeers.return_value = (True, "Deleted 2 peer(s)")

        with patch.dict(WireguardConfigurations, {"wg0": mock_wgc}):
            client = app.test_client()

            # Bulk restrict
            res_r = client.post("/api/v2/peers/bulk", json={
                "configurationName": "wg0",
                "action": "restrict",
                "ids": ["id1", "id2"],
                "reason": "Admin action"
            }, headers=api_auth)
            assert res_r.status_code == 200
            assert mock_wgc.restrictPeers.called

            # Bulk allow
            res_a = client.post("/api/v2/peers/bulk", json={
                "configurationName": "wg0",
                "action": "allow",
                "ids": ["id1", "id2"],
                "force": True
            }, headers=api_auth)
            assert res_a.status_code == 200
            assert mock_wgc.allowAccessPeers.called

            # Bulk delete
            res_d = client.post("/api/v2/peers/bulk", json={
                "configurationName": "wg0",
                "action": "delete",
                "ids": ["id1", "id2"]
            }, headers=api_auth)
            assert res_d.status_code == 200
            assert mock_wgc.deletePeers.called
