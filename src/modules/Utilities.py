import re, ipaddress
import subprocess
import sqlalchemy

def RegexMatch(regex, text) -> bool:
    """
    Regex Match
    @param regex: Regex patter
    @param text: Text to match
    @return: Boolean indicate if the text match the regex pattern
    """
    pattern = re.compile(regex)
    return pattern.search(text) is not None

def GetRemoteEndpoint() -> str:
    """
    Using socket to determine default interface IP address. Thanks, @NOXICS
    @return: 
    """
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))  # Connecting to a public IP
        wgd_remote_endpoint = s.getsockname()[0]
        return str(wgd_remote_endpoint)
    except (socket.error, OSError):
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except (socket.error, OSError):
        pass
    return "127.0.0.1"


def StringToBoolean(value: str):
    """
    Convert string boolean to boolean
    @param value: Boolean value in string came from Configuration file
    @return: Boolean value
    """
    return (value.strip().replace(" ", "").lower() in 
            ("yes", "true", "t", "1", 1))

def CheckAddress(ips_str: str) -> bool:
    if len(ips_str) == 0:
        return False

    for ip in ips_str.split(','):
        stripped_ip = ip.strip()
        if '.' not in stripped_ip and ':' not in stripped_ip:
            return False
        try:
            # Verify the IP-address, with the strict flag as false also allows for /32 and /128
            ipaddress.ip_network(stripped_ip, strict=False)
        except ValueError:
            return False
    return True

def CheckPeerKey(peer_key: str) -> bool:
    return re.match(r"^[A-Za-z0-9+/]{43}=$", peer_key)

def ValidateDNSAddress(addresses_str: str) -> tuple[bool, str | None]:
    if len(addresses_str) == 0:
        return False, "Got an empty list/string to check for valid DNS-addresses"

    addresses = addresses_str.split(',')
    for address in addresses:
        stripped_address = address.strip()

        if not CheckAddress(stripped_address) and not RegexMatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z]{0,61}[a-z]", stripped_address):
            return False, f"{stripped_address} does not appear to be a valid IP-address or FQDN"

    return True, None


def ValidateEndpointAllowedIPs(IPs) -> tuple[bool, str] | tuple[bool, None]:
    ips = IPs.replace(" ", "").split(",")
    for ip in ips:
        try:
            ipaddress.ip_network(ip, strict=False)
        except ValueError as e:
            return False, str(e)
    return True, None

def GenerateWireguardPublicKey(privateKey: str) -> tuple[bool, str] | tuple[bool, None]:
    try:
        publicKey = subprocess.check_output(["wg", "pubkey"], input=privateKey.encode(),
                                            stderr=subprocess.STDOUT, timeout=10)
        return True, publicKey.decode().strip('\n')
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False, None
    
def GenerateWireguardPrivateKey() -> tuple[bool, str] | tuple[bool, None]:
    try:
        publicKey = subprocess.check_output(["wg", "genkey"],
                                            stderr=subprocess.STDOUT, timeout=10)
        return True, publicKey.decode().strip('\n')
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False, None
    
def ValidatePasswordStrength(password: str) -> tuple[bool, str] | tuple[bool, None]:
    # Rules:
    #     - Must be over 8 characters & numbers
    #     - Must contain at least 1 Uppercase & Lowercase letters
    #     - Must contain at least 1 Numbers (0-9)
    #     - Must contain at least 1 special characters from $&+,:;=?@#|'<>.-^*()%!~_-
    if len(password) < 8:
        return False, "Password must be 8 characters or more"
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least 1 lowercase character"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least 1 uppercase character"
    if not re.search(r'\d', password):
        return False, "Password must contain at least 1 number"
    if not re.search(r'[$&+,:;=?@#|\'<>.\-^*()%!~_-]', password):
        return False, "Password must contain at least 1 special character from $&+,:;=?@#|'<>.-^*()%!~_-"
    
    return True, None

import threading
import os
try:
    import fcntl
except ImportError:
    fcntl = None

class ProcessLock:
    def __init__(self, lock_file):
        self.lock_file = lock_file
        self.lock_fd = None
        self.thread_lock = threading.Lock()

    def __enter__(self):
        self.thread_lock.acquire()
        if fcntl:
            self.lock_fd = open(self.lock_file, "w")
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.lock_fd and fcntl:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            self.lock_fd.close()
            self.lock_fd = None
        self.thread_lock.release()

import time

class SimpleRateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._requests = {}

    def check_rate_limit(self, key: str, limit: int = 5, period: int = 60) -> bool:
        """Returns True if the rate limit is exceeded (i.e. blocked), False otherwise."""
        now = time.time()
        with self._lock:
            timestamps = self._requests.get(key, [])
            timestamps = [t for t in timestamps if now - t < period]
            if len(timestamps) >= limit:
                self._requests[key] = timestamps
                return True
            timestamps.append(now)
            self._requests[key] = timestamps
            if len(self._requests) > 10000:
                self._cleanup_all(now, period)
            return False

    def _cleanup_all(self, now: float, period: int):
        keys_to_delete = []
        for k, v in self._requests.items():
            self._requests[k] = [t for t in v if now - t < period]
            if not self._requests[k]:
                keys_to_delete.append(k)
        for k in keys_to_delete:
            del self._requests[k]
