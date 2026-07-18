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
    def toJson(self):
        self.CPU.getCPUPercent()
        self.CPU.getPerCPUPercent()
        self.NetworkInterfaces.getData()
        
        return {
            "CPU": self.CPU,
            "Memory": {
                "VirtualMemory": self.MemoryVirtual,
                "SwapMemory": self.MemorySwap
            },
            "Disks": self.Disks,
            "NetworkInterfaces": self.NetworkInterfaces,
            "NetworkInterfacesPriority": self.NetworkInterfaces.getInterfacePriorities(),
            "Processes": self.Processes
        }
        

class CPU:
    def __init__(self):
        self.cpu_percent: float = 0
        self.cpu_percent_per_cpu: list[float] = []
        self.last_update = 0
        
    def getCPUPercent(self):
        try:
            now = time.time()
            if now - self.last_update > 0.5:
                self.cpu_percent = psutil.cpu_percent(interval=None)
        except Exception as e:
            current_app.logger.error(f"Get CPU Percent error: {e}")
    
    def getPerCPUPercent(self):
        try:
            now = time.time()
            if now - self.last_update > 0.5:
                self.cpu_percent_per_cpu = psutil.cpu_percent(interval=None, percpu=True)
                self.last_update = now
        except Exception as e:
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
            current_app.logger.error(f"Get Memory percent error: {e}")
    def toJson(self):
        self.getData()
        return self.__dict__

class Disks:
    def __init__(self):
        self.disks : list[Disk] = []
    def getData(self):
        try:
            self.disks = list(map(lambda x : Disk(x.mountpoint), psutil.disk_partitions()))
        except Exception as e:
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
                result = subprocess.check_output(["ip", "route", "show"], timeout=5).decode()
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
                return # Skip update if called too frequently
                
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
    def getData(self):
        try:
            processes = list(psutil.process_iter())

            cpu_processes = []
            memory_processes = []

            for proc in processes:
                try:
                    name = proc.name()
                    cmdline = " ".join(proc.cmdline())
                    pid = proc.pid
                    cpu_percent = proc.cpu_percent()
                    mem_percent = proc.memory_percent()

                    # Create Process object for CPU and memory tracking
                    cpu_process = Process(name, cmdline, pid, cpu_percent)
                    mem_process = Process(name, cmdline, pid, mem_percent)

                    cpu_processes.append(cpu_process)
                    memory_processes.append(mem_process)

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    # Skip processes we can’t access or that no longer exist
                    continue

            # Sort by CPU and memory usage (descending order)
            cpu_sorted = sorted(cpu_processes, key=lambda p: p.percent, reverse=True)
            mem_sorted = sorted(memory_processes, key=lambda p: p.percent, reverse=True)

            # Get top 20 processes for each
            self.CPU_Top_10_Processes = cpu_sorted[:20]
            self.Memory_Top_10_Processes = mem_sorted[:20]

        except Exception as e:
            current_app.logger.error(f"Get processes error: {e}")

    def toJson(self):
        self.getData()
        return {
            "cpu_top_10": self.CPU_Top_10_Processes,
            "memory_top_10": self.Memory_Top_10_Processes
        }