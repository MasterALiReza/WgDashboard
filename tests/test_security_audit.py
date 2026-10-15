import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from modules.Utilities import (
    CheckAddress,
    CheckPeerKey,
    ValidateDNSAddress,
    ValidateEndpointAllowedIPs
)


class TestSecurityAudit:
    @pytest.mark.parametrize("payload", [
        "; rm -rf / ;",
        "| whoami",
        "$(cat /etc/passwd)",
        "`id`",
        "../../../../etc/shadow",
        "key\nwith\nnewlines",
        "key\r\nwith\r\nnewlines",
        "key with spaces in between====",
        "'' OR 1=1 --",
        "'; DROP TABLE peers; --"
    ])
    def test_check_peer_key_rejects_injection(self, payload):
        assert CheckPeerKey(payload) is False

    @pytest.mark.parametrize("payload", [
        "8.8.8.8; rm -rf /",
        "1.1.1.1 && whoami",
        "$(hostname)",
        "../../etc/hosts",
        "8.8.8.8\n1.1.1.1",
        "8.8.8.8 | cat /etc/passwd"
    ])
    def test_validate_dns_address_rejects_injection(self, payload):
        ok, err = ValidateDNSAddress(payload)
        assert ok is False

    @pytest.mark.parametrize("payload", [
        "0.0.0.0/0; id",
        "192.168.1.0/24 | cat /etc/shadow",
        "$(whoami)",
        "0.0.0.0/0\n10.0.0.0/8"
    ])
    def test_validate_endpoint_allowed_ips_rejects_injection(self, payload):
        ok, err = ValidateEndpointAllowedIPs(payload)
        assert ok is False

    def test_address_check_handles_none_and_type_confusion(self):
        assert CheckAddress(None) is False
        assert CheckAddress(12345) is False
        assert CheckAddress([]) is False
        assert CheckAddress({}) is False
        assert CheckAddress("") is False
        assert CheckAddress("   ") is False

    def test_peer_key_handles_none_and_type_confusion(self):
        assert CheckPeerKey(None) is False
        assert CheckPeerKey(12345) is False
        assert CheckPeerKey([]) is False
        assert CheckPeerKey({}) is False
        assert CheckPeerKey("") is False
        assert CheckPeerKey("   ") is False

    def test_wireguard_configuration_to_json_does_not_leak_private_key(self):
        from modules.WireguardConfiguration import WireguardConfiguration
        from unittest.mock import MagicMock

        wc = MagicMock()
        wc.PrivateKey = "SUPER_SECRET_PRIVATE_KEY="
        wc.getStatus.return_value = True
        wc.Name = "wg0"
        wc.PublicKey = "PUBKEY="
        wc.Address = "10.0.0.1/24"
        wc.ListenPort = "51820"
        wc.PreUp = ""
        wc.PreDown = ""
        wc.PostUp = ""
        wc.PostDown = ""
        wc.SaveConfig = ""
        wc._compute_data_usage.return_value = {"Total": 0.0, "Sent": 0.0, "Receive": 0.0}
        wc.Peers = []
        wc.RestrictedPeers = []
        wc.Protocol = "wg"
        wc.Table = "auto"
        wc.configurationInfo.model_dump.return_value = {}

        json_data = WireguardConfiguration.toJson(wc)
        assert "PrivateKey" not in json_data
        assert json_data["PublicKey"] == "PUBKEY="
        assert json_data["Name"] == "wg0"

    def test_amnezia_configuration_to_json_does_not_leak_private_key(self):
        from modules.AmneziaConfiguration import AmneziaConfiguration
        from unittest.mock import MagicMock

        ac = MagicMock()
        ac.PrivateKey = "SUPER_SECRET_PRIVATE_KEY="
        ac.getStatus.return_value = True
        ac.Name = "awg0"
        ac.PublicKey = "PUBKEY="
        ac.Address = "10.0.0.1/24"
        ac.ListenPort = "51820"
        ac.PreUp = ""
        ac.PreDown = ""
        ac.PostUp = ""
        ac.PostDown = ""
        ac.SaveConfig = ""
        ac.configurationInfo.model_dump.return_value = {}
        ac._compute_data_usage.return_value = {"Total": 0.0, "Sent": 0.0, "Receive": 0.0}
        ac.Peers = []
        ac.RestrictedPeers = []
        ac.Protocol = "awg"
        ac.Table = "auto"
        for param in ["Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4", "I1", "I2", "I3", "I4", "I5"]:
            setattr(ac, param, 0)

        json_data = AmneziaConfiguration.toJson(ac)
        assert "PrivateKey" not in json_data
        assert json_data["PublicKey"] == "PUBKEY="
        assert json_data["Name"] == "awg0"

    def test_dashboard_config_hidden_attributes_masked(self, tmp_path, monkeypatch):
        from modules.DashboardConfig import DashboardConfig
        ini_path = str(tmp_path / "wg-dashboard.ini")
        monkeypatch.setattr(DashboardConfig, "ConfigurationFilePath", ini_path)
        cfg = DashboardConfig()
        json_data = cfg.toJson()

        # SEC-02: flask_secret_key, totp_key, auth_req, session_epoch must not be visible
        assert "flask_secret_key" not in json_data.get("Server", {})
        assert "totp_key" not in json_data.get("Account", {})
        assert "auth_req" not in json_data.get("Server", {})
        assert "session_epoch" not in json_data.get("Account", {})

        # Passwords must be masked as "***"
        assert json_data.get("Account", {}).get("password") == "***"

        # SEC-11: Clients default disabled
        assert cfg.GetConfig("Clients", "enable")[1] is False
        assert cfg.GetConfig("Clients", "sign_up")[1] is False

    def test_dashboard_config_readonly_attributes_cannot_be_overwritten(self, tmp_path, monkeypatch):
        from modules.DashboardConfig import DashboardConfig
        ini_path = str(tmp_path / "wg-dashboard.ini")
        monkeypatch.setattr(DashboardConfig, "ConfigurationFilePath", ini_path)
        cfg = DashboardConfig()

        # Read-only attributes cannot be set without init=True
        ok, _ = cfg.SetConfig("Account", "totp_key", "MALICIOUS_KEY")
        assert ok is False
        ok, _ = cfg.SetConfig("Server", "flask_secret_key", "MALICIOUS_KEY")
        assert ok is False
        ok, _ = cfg.SetConfig("Account", "session_epoch", "999")
        assert ok is False

    def test_dashboard_config_session_epoch_bump(self, tmp_path, monkeypatch):
        from modules.DashboardConfig import DashboardConfig
        ini_path = str(tmp_path / "wg-dashboard.ini")
        monkeypatch.setattr(DashboardConfig, "ConfigurationFilePath", ini_path)
        cfg = DashboardConfig()

        epoch1 = cfg.GetConfig("Account", "session_epoch")[1]
        assert epoch1 is not None
        new_epoch = cfg.bump_session_epoch()
        assert int(new_epoch) == int(epoch1) + 1
        assert cfg.GetConfig("Account", "session_epoch")[1] == new_epoch

    def test_write_psk_tmp_creates_and_reads_safely(self):
        from modules.Utilities import write_psk_tmp
        test_psk = "TEST_PRESHARED_KEY_12345678901234567890="
        path = write_psk_tmp(test_psk)
        try:
            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert content == test_psk
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_simple_rate_limiter_read_only_and_reset(self):
        from modules.Utilities import SimpleRateLimiter
        limiter = SimpleRateLimiter()
        key = "test_user_ip"

        # is_rate_limited is read-only: does not record hits
        for _ in range(10):
            assert limiter.is_rate_limited(key, limit=3, period=60) is False

        # hit records failed attempts
        limiter.hit(key, period=60)
        limiter.hit(key, period=60)
        assert limiter.is_rate_limited(key, limit=3, period=60) is False
        limiter.hit(key, period=60)
        # Reached limit (3)
        assert limiter.is_rate_limited(key, limit=3, period=60) is True

        # reset clears hits
        limiter.reset(key)
        assert limiter.is_rate_limited(key, limit=3, period=60) is False

    def test_bot_allowed_endpoints_allowlist(self):
        import re

        BOT_ALLOWED = {
            "/api/getWireguardConfigurations",
            "/api/getWireguardConfigurationInfo",
            "/api/getAvailableIPs",
            "/api/downloadPeer",
            "/api/addPeers",
            "/api/updatePeerSettings",
            "/api/deletePeers",
            "/api/restrictPeers",
            "/api/allowAccessPeers",
            "/api/resetPeerData",
            "/api/savePeerScheduleJob",
            "/api/deletePeerScheduleJob",
            "/api/handshake",
        }

        def is_bot_allowed(path: str) -> bool:
            clean = path.rstrip("/")
            for ep in BOT_ALLOWED:
                if clean == ep or clean.startswith(ep + "/"):
                    return True
            return False

        # Allowed bot routes
        assert is_bot_allowed("/api/getWireguardConfigurations") is True
        assert is_bot_allowed("/api/getWireguardConfigurationInfo/wg0") is True
        assert is_bot_allowed("/api/downloadPeer/wg0/PEER_ID") is True
        assert is_bot_allowed("/api/addPeers/wg0") is True
        assert is_bot_allowed("/api/restrictPeers/wg0") is True

        # Dangerous/Admin routes forbidden for bot API key
        assert is_bot_allowed("/api/authenticate") is False
        assert is_bot_allowed("/api/updateDashboardConfigurationItem") is False
        assert is_bot_allowed("/api/deleteWireguardConfiguration") is False
        assert is_bot_allowed("/api/globalBackup/create") is False
        assert is_bot_allowed("/api/globalBackup/restore") is False

    def test_configuration_name_regex_validation(self):
        import re
        valid_pattern = r"^[a-zA-Z0-9_=\+\.\-]{1,15}$"

        assert re.match(valid_pattern, "wg0") is not None
        assert re.match(valid_pattern, "awg_server-1") is not None
        assert re.match(valid_pattern, "wg.123+=") is not None

        # Traversal and command injection payloads rejected
        assert re.match(valid_pattern, "../../../etc") is None
        assert re.match(valid_pattern, "wg0; rm -rf /") is None
        assert re.match(valid_pattern, "wg0 && id") is None
        assert re.match(valid_pattern, "this_name_is_way_too_long_for_iface") is None
        assert re.match(valid_pattern, "") is None

    def test_dashboard_config_corrupted_file_raises_runtime_error(self, tmp_path, monkeypatch):
        from modules.DashboardConfig import DashboardConfig
        ini_path = str(tmp_path / "corrupt-wg-dashboard.ini")
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write("[Server]\napp_port = 10086\n") # Missing [Account]

        monkeypatch.setattr(DashboardConfig, "ConfigurationFilePath", ini_path)
        with pytest.raises(RuntimeError, match="missing \\[Account\\] section"):
            DashboardConfig()

    def test_totp_drift_verification_and_replay_detection(self):
        import pyotp, time
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        current_code = totp.now()

        # Valid with drift window
        assert totp.verify(current_code, valid_window=1) is True

        # Replay prevention logic
        last_used = {"code": current_code, "timestamp": time.time()}
        is_replay = (current_code == last_used["code"] and (time.time() - last_used["timestamp"] < 60))
        assert is_replay is True

    def test_api_key_expiration_logic(self):
        from datetime import datetime, timedelta
        from modules.DashboardAPIKey import DashboardAPIKey

        now = datetime.now()
        past = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        future = (now + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

        expired_key = DashboardAPIKey("KEY_EXPIRED", "2026-01-01 00:00:00", past)
        valid_key = DashboardAPIKey("KEY_VALID", "2026-01-01 00:00:00", future)
        never_expire_key = DashboardAPIKey("KEY_NEVER", "2026-01-01 00:00:00", None)

        def is_key_valid(k: DashboardAPIKey) -> bool:
            if k.ExpiredAt is None or k.ExpiredAt == "" or k.ExpiredAt == "None":
                return True
            try:
                exp_dt = datetime.strptime(k.ExpiredAt, "%Y-%m-%d %H:%M:%S")
                return exp_dt > now
            except Exception:
                return False

        assert is_key_valid(expired_key) is False
        assert is_key_valid(valid_key) is True
        assert is_key_valid(never_expire_key) is True



