import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from modules.Utilities import (
    CheckAddress,
    CheckPeerKey,
    ValidateDNSAddress,
    ValidateEndpointAllowedIPs,
    GenerateWireguardPrivateKey,
    GenerateWireguardPublicKey,
    _fallback_x25519_genkey,
    _fallback_x25519_pubkey
)


class TestWireguardKeyGeneration:
    def test_fallback_x25519_genkey_valid(self):
        ok, priv = _fallback_x25519_genkey()
        assert ok is True
        assert isinstance(priv, str)
        assert len(priv) == 44
        assert priv.endswith("=")
        assert CheckPeerKey(priv) is True

    def test_fallback_x25519_pubkey_valid(self):
        ok, priv = _fallback_x25519_genkey()
        assert ok is True
        pub_ok, pub = _fallback_x25519_pubkey(priv)
        assert pub_ok is True
        assert isinstance(pub, str)
        assert len(pub) == 44
        assert pub.endswith("=")
        assert CheckPeerKey(pub) is True

    def test_fallback_x25519_pubkey_deterministic(self):
        ok, priv = _fallback_x25519_genkey()
        assert ok is True
        pub_ok1, pub1 = _fallback_x25519_pubkey(priv)
        pub_ok2, pub2 = _fallback_x25519_pubkey(priv)
        assert pub_ok1 is True
        assert pub_ok2 is True
        assert pub1 == pub2

    def test_fallback_x25519_pubkey_invalid_priv(self):
        ok, pub = _fallback_x25519_pubkey("invalid-private-key")
        assert ok is False
        assert pub is None

    def test_generate_wireguard_private_key(self):
        ok, priv = GenerateWireguardPrivateKey()
        assert ok is True
        assert isinstance(priv, str)
        assert len(priv) == 44
        assert priv.endswith("=")
        assert CheckPeerKey(priv) is True

    def test_generate_wireguard_public_key(self):
        ok, priv = GenerateWireguardPrivateKey()
        assert ok is True
        pub_ok, pub = GenerateWireguardPublicKey(priv)
        assert pub_ok is True
        assert isinstance(pub, str)
        assert len(pub) == 44
        assert CheckPeerKey(pub) is True

    def test_check_peer_key(self):
        assert CheckPeerKey("yAnNsAlZQUtvVIxAlUmAneQum6NqJuNuSTGHI12345U=") is True
        assert CheckPeerKey("IJWqJJXZxiuH6O3wlKFYPSxlul5riHOwnObLMSe+Sm4=") is True
        assert CheckPeerKey("") is False
        assert CheckPeerKey(None) is False
        assert CheckPeerKey(12345) is False
        assert CheckPeerKey("too_short=") is False
        assert CheckPeerKey("toolong_toolong_toolong_toolong_toolong_toolong==") is False
        assert CheckPeerKey("with spaces in middle aaaaaaaaaaaaaaaaaaaaa=") is False
        assert CheckPeerKey("IJWqJJXZxiuH6O3wlKFYPSxlul5riHOwnObLMSe+Sm4=\n") is True  # strip handled

    def test_check_address(self):
        assert CheckAddress("10.0.0.1/32") is True
        assert CheckAddress("192.168.1.1/24, 10.0.0.1/32") is True
        assert CheckAddress("fd00::1/128") is True
        assert CheckAddress("") is False
        assert CheckAddress(None) is False
        assert CheckAddress("invalid_ip") is False
        assert CheckAddress("256.256.256.256/32") is False

    def test_validate_dns_address(self):
        assert ValidateDNSAddress("8.8.8.8, 1.1.1.1")[0] is True
        assert ValidateDNSAddress("dns.google, one.one.one.one")[0] is True
        assert ValidateDNSAddress("")[0] is False
        assert ValidateDNSAddress("invalid DNS with spaces")[0] is False

    def test_validate_endpoint_allowed_ips(self):
        assert ValidateEndpointAllowedIPs("0.0.0.0/0, ::/0")[0] is True
        assert ValidateEndpointAllowedIPs("192.168.1.0/24")[0] is True
        assert ValidateEndpointAllowedIPs("")[0] is False
        assert ValidateEndpointAllowedIPs("invalid")[0] is False
