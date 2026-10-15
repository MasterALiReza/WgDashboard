import os
from flask import current_app
import random
import re
import subprocess
import uuid

from flask import current_app
from .Peer import Peer
from .Utilities import CheckAddress, ValidateDNSAddress, GenerateWireguardPublicKey, write_psk_tmp


class AmneziaPeer(Peer):
    def __init__(self, tableData, configuration):
        super().__init__(tableData, configuration)


    def updatePeer(self, name: str, private_key: str,
                   preshared_key: str,
                   dns_addresses: str,
                   allowed_ip: str,
                   endpoint_allowed_ip: str,
                   mtu: int,
                   keepalive: int,
                   notes: str
                   ) -> tuple[bool, str | None]:

        # Before we do any compute, let us check if the given endpoint allowed ip is valid at all
        if not CheckAddress(endpoint_allowed_ip):
            return False, f"Endpoint Allowed IPs format is incorrect"

        peers = []
        for peer in self.configuration.getPeersList():
            # Make sure to exclude your own data when updating since its not really relevant
            if peer.id == self.id:
                continue
            peers.append(peer)

        used_allowed_ips = []
        for peer in peers:
            ips = (peer.allowed_ip or '').split(',')
            for ip in ips:
                if ip.strip():
                    used_allowed_ips.append(ip.strip())

        for ip in (allowed_ip or '').split(','):
            if ip.strip() and ip.strip() in used_allowed_ips:
                return False, "Allowed IP already taken by another peer"

        ok, msg = ValidateDNSAddress(dns_addresses)
        if not ok:
            return False, f"DNS IP-Address or FQDN is incorrect"

        if mtu is None or isinstance(mtu, str):
            try:
                mtu = int(mtu) if mtu is not None and str(mtu).strip().isdigit() else 0
            except Exception:
                mtu = 0

        if keepalive is None or isinstance(keepalive, str):
            try:
                keepalive = int(keepalive) if keepalive is not None and str(keepalive).strip().isdigit() else 0
            except Exception:
                keepalive = 0
        
        if mtu not in range(0, 1461):
            return False, "MTU format is not correct"

        if keepalive < 0:
            return False, "Persistent Keepalive format is not correct"

        if len(private_key) > 0:
            pubKey = GenerateWireguardPublicKey(private_key)
            if not pubKey[0] or pubKey[1] != self.id:
                return False, "Private key does not match with the public key"
    
        try:
            uid = None
            psk_exist = len(preshared_key) > 0

            newAllowedIPs = allowed_ip.replace(" ", "")
            if not CheckAddress(newAllowedIPs):
                return False, "Allowed IPs entry format is incorrect"

            if self.configuration.getStatus():
                try:
                    if psk_exist:
                        uid = write_psk_tmp(preshared_key)

                    command = [self.configuration.Protocol, "set", self.configuration.Name, "peer", self.id, "allowed-ips", newAllowedIPs, "preshared-key", uid if psk_exist else "/dev/null"]
                    updateAllowedIp = subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=15)
                finally:
                    if uid and os.path.exists(uid):
                        try:
                            os.remove(uid)
                        except Exception:
                            pass

                clean_output = updateAllowedIp.decode().strip()
                if len(clean_output) != 0:
                    current_app.logger.error(f"Update peer failed when updating Allowed IPs.\nInput: {newAllowedIPs}\nOutput: {clean_output}")
                    return False, "Internal server error"

                command = [f"{self.configuration.Protocol}-quick", "save", self.configuration.Name]
                saveConfig = subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=15)

                expected_cmd = f"{self.configuration.Protocol} showconf {self.configuration.Name}"
                if expected_cmd not in saveConfig.decode().strip('\n'):
                    current_app.logger.error(f"Update peer failed when saving the configuration: expected '{expected_cmd}' in output")
                    return False, "Internal server error"

            with self.configuration.engine.begin() as conn:
                conn.execute(
                    self.configuration.peersTable.update().values({
                        "name": name,
                        "private_key": private_key,
                        "DNS": dns_addresses,
                        "endpoint_allowed_ip": endpoint_allowed_ip,
                        "allowed_ip": newAllowedIPs,
                        "mtu": mtu,
                        "keepalive": keepalive,
                        "notes": notes,
                        "preshared_key": preshared_key
                    }).where(
                        self.configuration.peersTable.c.id == self.id
                    )
                )
            self.name = name
            self.allowed_ip = newAllowedIPs
            self.DNS = dns_addresses
            self.endpoint_allowed_ip = endpoint_allowed_ip
            self.mtu = mtu
            self.keepalive = keepalive
            self.notes = notes
            self.preshared_key = preshared_key

            if not self.configuration.getStatus():
                self.configuration.saveConfiguration()

            self.configuration.getPeers()
            return True, None
        except subprocess.CalledProcessError as exc:
            current_app.logger.error(f"Subprocess call failed:\n{exc.output.decode('UTF-8')}")
            return False, "Internal server error"
