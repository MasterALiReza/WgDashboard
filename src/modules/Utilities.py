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
import time
try:
    import fcntl
except ImportError:
    fcntl = None

class ProcessLock:
    _instances = {}
    _registry_lock = threading.Lock()

    def __new__(cls, lock_file, timeout=30):
        canonical_path = os.path.abspath(lock_file)
        with cls._registry_lock:
            if canonical_path not in cls._instances:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[canonical_path] = instance
            return cls._instances[canonical_path]

    def __init__(self, lock_file, timeout=30):
        if getattr(self, '_initialized', False):
            self.timeout = timeout
            return
        self.lock_file = os.path.abspath(lock_file)
        self.lock_fd = None
        self.thread_lock = threading.RLock()
        self.timeout = timeout
        self._count = 0
        self._owner = None
        self._initialized = True

    def __enter__(self):
        current_thread = threading.get_ident()
        # Acquire thread lock with timeout to prevent thread deadlock
        acquired = self.thread_lock.acquire(timeout=self.timeout)
        if not acquired:
            raise TimeoutError(f"ProcessLock: Thread timeout ({self.timeout}s) waiting for lock on {self.lock_file}")

        # If already owned by this thread, just increment recursion count
        if self._owner == current_thread:
            self._count += 1
            return self

        # First acquisition by this thread: acquire OS-level file lock
        if fcntl:
            try:
                os.makedirs(os.path.dirname(self.lock_file), exist_ok=True)
                self.lock_fd = open(self.lock_file, "a+")
                # Attempt non-blocking flock in a loop with timeout
                start_time = time.time()
                while True:
                    try:
                        fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except (BlockingIOError, IOError, OSError):
                        if time.time() - start_time >= self.timeout:
                            try:
                                self.lock_fd.close()
                            except Exception:
                                pass
                            self.lock_fd = None
                            self.thread_lock.release()
                            raise TimeoutError(f"ProcessLock: OS flock timeout ({self.timeout}s) on {self.lock_file}")
                        time.sleep(0.05)
            except Exception:
                if self.lock_fd:
                    try:
                        self.lock_fd.close()
                    except Exception:
                        pass
                    self.lock_fd = None
                self.thread_lock.release()
                raise

        self._owner = current_thread
        self._count = 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        current_thread = threading.get_ident()
        if self._owner != current_thread:
            try:
                self.thread_lock.release()
            except RuntimeError:
                pass
            return

        self._count -= 1
        if self._count == 0:
            self._owner = None
            if self.lock_fd and fcntl:
                try:
                    fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                    self.lock_fd.close()
                except Exception:
                    pass
                self.lock_fd = None
            self.thread_lock.release()
        else:
            self.thread_lock.release()


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
