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

