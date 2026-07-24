import shutil, subprocess, time, threading, psutil
from flask import current_app

class SystemStatus:
    def __init__(self):
        self.CPU = CPU()
        self.MemoryVirtual = Memory('virtual')
        self.MemorySwap = Memory('swap')
        self.Disks = Disks()
        self.NetworkInterfaces = NetworkInterfaces()
        self.Processes = Processes()
        self._lock = threading.Lock()
        self._cached_data = None
        self._last_cache_time = 0

        # Pre-warm psutil CPU percent counters so first call returns valid reading
        try:
            psutil.cpu_percent(interval=None)
            psutil.cpu_percent(interval=None, percpu=True)
        except Exception:
            pass

    def _update_all(self):
        self.CPU.getCPUPercent()
        self.CPU.getPerCPUPercent()
        self.NetworkInterfaces.getData()
        data = {
            "CPU": self.CPU.toJson(),
            "Memory": {
                "VirtualMemory": self.MemoryVirtual.toJson(),
                "SwapMemory": self.MemorySwap.toJson()
            },
            "Disks": self.Disks.toJson(),
            "NetworkInterfaces": self.NetworkInterfaces.toJson(),
            "NetworkInterfacesPriority": self.NetworkInterfaces.getInterfacePriorities(),
            "Processes": self.Processes.toJson()
        }
        with self._lock:
            self._cached_data = data
            self._last_cache_time = time.time()
        return data

    def toJson(self):
        now = time.time()
        with self._lock:
            if self._cached_data is not None and (now - self._last_cache_time) < 2.0:
                return self._cached_data
        return self._update_all()


class CPU:
    def __init__(self):
        self.cpu_percent: float = 0
        self.cpu_percent_per_cpu: list[float] = []
        self.last_update = 0
        try:
            self.cpu_percent = psutil.cpu_percent(interval=None)
            self.cpu_percent_per_cpu = psutil.cpu_percent(interval=None, percpu=True)
        except Exception:
            pass

    def getCPUPercent(self):
        try:
            now = time.time()
            if now - self.last_update > 0.5:
                val = psutil.cpu_percent(interval=None)
                if val > 0 or self.cpu_percent == 0:
                    self.cpu_percent = val
        except Exception as e:
            if current_app:
                current_app.logger.error(f"Get CPU Percent error: {e}")

    def getPerCPUPercent(self):
        try:
            now = time.time()
            if now - self.last_update > 0.5:
                vals = psutil.cpu_percent(interval=None, percpu=True)
                if any(v > 0 for v in vals) or not self.cpu_percent_per_cpu:
                    self.cpu_percent_per_cpu = vals
                self.last_update = now
        except Exception as e:
            if current_app:
                current_app.logger.error(f"Get Per CPU Percent error: {e}")

    def toJson(self):
        return {"cpu_percent": self.cpu_percent, "cpu_percent_per_cpu": self.cpu_percent_per_cpu}


class Memory:
    def __init__(self, memoryType: str):
        self.__memoryType__ = memoryType
        self.total = 0
        self.available = 0
        self.percent = 0

    def getData(self):
        try:
            if self.__memoryType__ == "virtual":
                memory = psutil.virtual_memory()
                self.available = memory.available
            else:
                memory = psutil.swap_memory()
                self.available = memory.free
            self.total = memory.total
            self.percent = memory.percent
        except Exception as e:
            if current_app:
                current_app.logger.error(f"Get Memory percent error: {e}")

    def toJson(self):
        self.getData()
        return self.__dict__


class Disks:
    def __init__(self):
        self.disks: list[Disk] = []

    def getData(self):
        try:
            partitions = psutil.disk_partitions(all=False)
            filtered = []
            seen_mounts = set()
            for p in partitions:
                # Exclude loop devices, snap packages, docker volumes, proc/sys virtual filesystems
                mp = p.mountpoint
                if mp.startswith('/snap') or mp.startswith('/var/lib/docker') or mp.startswith('/proc') or mp.startswith('/sys') or 'loop' in p.device:
                    continue
                if mp in seen_mounts:
                    continue
                seen_mounts.add(mp)
                d = Disk(mp)
                d.getData()
                filtered.append(d)

            if not filtered:
                d = Disk('/')
                d.getData()
                filtered.append(d)

            self.disks = filtered
        except Exception as e:
            if current_app:
                current_app.logger.error(f"Get Disk percent error: {e}")

    def toJson(self):
        self.getData()
        return self.disks


class Disk:
    def __init__(self, mountPoint: str):
        self.total = 0
        self.used = 0
        self.free = 0
        self.percent = 0
        self.mountPoint = mountPoint

    def getData(self):
        try:
            disk = psutil.disk_usage(self.mountPoint)
            self.total = disk.total
            self.free = disk.free
            self.used = disk.used
            self.percent = disk.percent
        except Exception as e:
            if current_app:
                current_app.logger.error(f"Get Disk percent error: {e}")

    def toJson(self):
        self.getData()
        return self.__dict__


class NetworkInterfaces:
    def __init__(self):
        self.interfaces = {}
        self.last_network = None
        self.last_time = None

    def getInterfacePriorities(self):
        if shutil.which("ip"):
            try:
                result = subprocess.check_output(["ip", "route", "show"], timeout=3).decode()
                priorities = {}
                for line in result.splitlines():
                    if "metric" in line and "dev" in line:
                        parts = line.split()
                        dev = parts[parts.index("dev")+1]
                        metric = int(parts[parts.index("metric")+1])
                        if dev not in priorities:
                            priorities[dev] = metric
                return priorities
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                return {}
        return {}

    def getData(self):
        try:
            current_network = psutil.net_io_counters(pernic=True, nowrap=True)
            current_time = time.time()

            if self.last_network is None or self.last_time is None:
                self.last_network = current_network
                self.last_time = current_time
                for i in current_network.keys():
                    self.interfaces[i] = current_network[i]._asdict()
                    self.interfaces[i]['realtime'] = {'sent': 0.0, 'recv': 0.0}
                return

            time_diff = current_time - self.last_time
            if time_diff < 0.5:
                return  # Skip update if called too frequently

            for i in current_network.keys():
                self.interfaces[i] = current_network[i]._asdict()
                if i in self.last_network:
                    sent_diff = current_network[i].bytes_sent - self.last_network[i].bytes_sent
                    recv_diff = current_network[i].bytes_recv - self.last_network[i].bytes_recv

                    self.interfaces[i]['realtime'] = {
                        'sent': round((sent_diff / 1024 / 1024) / time_diff, 4),
                        'recv': round((recv_diff / 1024 / 1024) / time_diff, 4)
                    }
                else:
                    self.interfaces[i]['realtime'] = {'sent': 0.0, 'recv': 0.0}

            self.last_network = current_network
            self.last_time = current_time
        except Exception as e:
            if current_app:
                current_app.logger.error(f"Get network error: {e}")

    def toJson(self):
        return {k: {key: val for key, val in v.items() if key != 'last_network'} for k, v in self.interfaces.items()}


class Process:
    def __init__(self, name, command, pid, percent):
        self.name = name
        self.command = command
        self.pid = pid
        self.percent = percent

    def toJson(self):
        return self.__dict__


class Processes:
    def __init__(self):
        self.CPU_Top_10_Processes: list[Process] = []
        self.Memory_Top_10_Processes: list[Process] = []
        self.last_update = 0

    def getData(self):
        now = time.time()
        # Fast cache for process iteration (5 seconds) to avoid CPU spikes
        if now - self.last_update < 5.0 and self.CPU_Top_10_Processes:
            return
        try:
            # Batch fetch process attributes for maximum performance
            processes = list(psutil.process_iter(['name', 'cmdline', 'pid', 'cpu_percent', 'memory_percent']))

            cpu_processes = []
            memory_processes = []

            for proc in processes:
                try:
                    info = proc.info
                    name = info.get('name') or ""
                    cmdline = " ".join(info.get('cmdline') or [])
                    pid = info.get('pid') or 0
                    cpu_percent = info.get('cpu_percent') or 0.0
                    mem_percent = info.get('memory_percent') or 0.0

                    cpu_processes.append(Process(name, cmdline, pid, cpu_percent))
                    memory_processes.append(Process(name, cmdline, pid, mem_percent))

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue

            cpu_sorted = sorted(cpu_processes, key=lambda p: p.percent, reverse=True)
            mem_sorted = sorted(memory_processes, key=lambda p: p.percent, reverse=True)

            self.CPU_Top_10_Processes = cpu_sorted[:20]
            self.Memory_Top_10_Processes = mem_sorted[:20]
            self.last_update = now

        except Exception as e:
            if current_app:
                current_app.logger.error(f"Get processes error: {e}")

    def toJson(self):
        self.getData()
        return {
            "cpu_top_10": self.CPU_Top_10_Processes,
            "memory_top_10": self.Memory_Top_10_Processes
        }