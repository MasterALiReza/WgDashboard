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
