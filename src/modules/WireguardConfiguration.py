"""
WireGuard Configuration
"""
from typing import Any

import jinja2
from jinja2.sandbox import SandboxedEnvironment
_jinja_env = SandboxedEnvironment()
import sqlalchemy, random, shutil, configparser, ipaddress, os, subprocess, time, re, uuid, psutil, traceback, threading
from zipfile import ZipFile
from datetime import datetime, timedelta
from itertools import islice
from flask import current_app

from .DatabaseConnection import ConnectionString
from .DashboardConfig import DashboardConfig
from .Peer import Peer
from .PeerJobs import PeerJobs
from .PeerShareLinks import PeerShareLinks
from .Utilities import StringToBoolean, \
    GenerateWireguardPublicKey, \
    RegexMatch, \
    ValidateDNSAddress, \
    ValidateEndpointAllowedIPs, \
    CheckAddress, \
    CheckPeerKey, \
    ProcessLock
from .WireguardConfigurationInfo import WireguardConfigurationInfo, PeerGroupsClass
from .DashboardWebHooks import DashboardWebHooks


class WireguardConfiguration:
    class InvalidConfigurationFileException(Exception):
        def __init__(self, m):
            self.message = m

        def __str__(self):
            return self.message

    def __init__(self, DashboardConfig: DashboardConfig, 
                 AllPeerJobs: PeerJobs,
                 AllPeerShareLinks: PeerShareLinks,
                 DashboardWebHooks: DashboardWebHooks,
                 name: str = None,
                 data: dict = None,
                 backup: dict = None,
                 startup: bool = False,
                 wg: bool = True
                 ):
        self.Peers = []
        self.__parser: configparser.ConfigParser = configparser.RawConfigParser(strict=False)
        self.__parser.optionxform = str
        self.__configFileModifiedTime = None
        self.Status: bool = False
        self.PrivateKey: str = ""
        self.PublicKey: str = ""
        self.ListenPort: str = ""
        self.Address: str = ""
        self.DNS: str = ""
        self.Table: str = ""
        self.MTU: str = ""
        self.PreUp: str = ""
        self.PostUp: str = ""
        self.PreDown: str = ""
        self.PostDown: str = ""
        self.SaveConfig: bool = True
        self.Name = name
        self.Protocol = "wg" if wg else "awg"
        self.AllPeerJobs = AllPeerJobs
        self.DashboardConfig = DashboardConfig
        self.DashboardConfig.EnsureDatabaseIntegrity({self.Name: self})
        self.AllPeerShareLinks = AllPeerShareLinks
        self.DashboardWebHooks = DashboardWebHooks
        self.configPath = os.path.join(self.__getProtocolPath(), f'{self.Name}.conf')
        self.engine: sqlalchemy.Engine = sqlalchemy.create_engine(ConnectionString("wgdashboard"))
        self.metadata: sqlalchemy.MetaData = sqlalchemy.MetaData()
        self.dbType = self.DashboardConfig.GetConfig("Database", "type")[1]

        if name is not None:
            if data is not None and "Backup" in data.keys():
                db = self.__importDatabase(
                    os.path.join(
                        self.__getProtocolPath(),
                        'WGDashboard_Backup',
                        data["Backup"].replace(".conf", ".sql")), True)
            else:
                self.createDatabase()

            self.__parseConfigurationFile()
            self.__initPeersList()
        else:
            self.Name = data["ConfigurationName"]
            self.configPath = os.path.join(self.__getProtocolPath(), f'{self.Name}.conf')
        
        self.lock = ProcessLock(f"/tmp/wgdashboard_{self.Name}.lock")

        if name is None:
            for i in dir(self):
                if str(i) in data.keys():
                    if isinstance(getattr(self, i), bool):
                        setattr(self, i, StringToBoolean(data[i]))
                    else:
                        setattr(self, i, str(data[i]))

            self.__parser["Interface"] = {
                "PrivateKey": self.PrivateKey,
                "Address": self.Address,
                "ListenPort": self.ListenPort,
                "PreUp": f"{self.PreUp}",
                "PreDown": f"{self.PreDown}",
                "PostUp": f"{self.PostUp}",
                "PostDown": f"{self.PostDown}",
                "SaveConfig": "true"
            }

            if self.Protocol == 'awg':
                self.__parser["Interface"]["Jc"] = self.Jc
                self.__parser["Interface"]["Jc"] = self.Jc
                self.__parser["Interface"]["Jmin"] = self.Jmin
                self.__parser["Interface"]["Jmax"] = self.Jmax
                self.__parser["Interface"]["S1"] = self.S1
                self.__parser["Interface"]["S2"] = self.S2
                self.__parser["Interface"]["S3"] = self.S3
                self.__parser["Interface"]["S4"] = self.S4
                self.__parser["Interface"]["H1"] = self.H1
                self.__parser["Interface"]["H2"] = self.H2
                self.__parser["Interface"]["H3"] = self.H3
                self.__parser["Interface"]["H4"] = self.H4
                self.__parser["Interface"]["I1"] = self.I1
                self.__parser["Interface"]["I2"] = self.I2
                self.__parser["Interface"]["I3"] = self.I3
                self.__parser["Interface"]["I4"] = self.I4
                self.__parser["Interface"]["I5"] = self.I5

            if "Backup" not in data.keys():
                self.createDatabase()
                with open(self.configPath, "w+") as configFile:
                    self.__parser.write(configFile)
                    current_app.logger.info(f"Configuration file {self.configPath} created")
                self.__initPeersList()

        if not os.path.exists(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup')):
            os.mkdir(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup'))

        current_app.logger.info(f"Initialized Configuration: {name}")
        self.__dumpDatabase()
        if self.getAutostartStatus() and not self.getStatus() and startup:
            status, ext = self.toggleConfiguration()
            if not status:
                current_app.logger.error(f"Failed to autostart configuration: {name}. Reason: {ext}")
            else:
                current_app.logger.info(f"Autostart Configuration: {name}")
            
        self.configurationInfo: WireguardConfigurationInfo | None = None
        configurationInfoJson = self.readConfigurationInfo()
        if not configurationInfoJson:
            self.configurationInfo = WireguardConfigurationInfo(**{})
            self.initConfigurationInfo()
        else:
            self.configurationInfo = WireguardConfigurationInfo.model_validate_json(configurationInfoJson.get("Info"))
        
        if self.Status:
            self.addAutostart()

    def __getProtocolPath(self) -> str:
        _, path = self.DashboardConfig.GetConfig("Server", "wg_conf_path") if self.Protocol == "wg" \
            else self.DashboardConfig.GetConfig("Server", "awg_conf_path")
        return path

    def __initPeersList(self):
        self.Peers: list[Peer] = []
        self.getPeers()
        self.getRestrictedPeersList()

    def getRawConfigurationFile(self):
        return open(self.configPath, 'r').read()

    def updateRawConfigurationFile(self, newRawConfiguration):
        backupStatus, backup = self.backupConfigurationFile()
        if not backupStatus:
            return False, "Cannot create backup"

        if self.Status:
            self.toggleConfiguration()

        with open(self.configPath, 'w') as f:
            f.write(newRawConfiguration)

        status, err = self.toggleConfiguration()
        if not status:
            restoreStatus = self.restoreBackup(backup['filename'])
            current_app.logger.error(f"Backup restore status: {restoreStatus}")
            self.toggleConfiguration()
            return False, err
        return True, None

    def __parseConfigurationFile(self):
        with open(self.configPath, 'r') as f:
            original = [l.rstrip("\n") for l in f.readlines()]
            try:
                start = original.index("[Interface]")

                # Clean
                for i in range(start, len(original)):
                    if original[i] == "[Peer]":
                        break
                    split = re.split(r'\s*=\s*', original[i], 1)
                    if len(split) == 2:
                        key = split[0]
                        if key in dir(self):
                            if isinstance(getattr(self, key), bool):
                                setattr(self, key, False)
                            else:
                                setattr(self, key, "")

                # Set
                for i in range(start, len(original)):
                    if original[i] == "[Peer]":
                        break
                    split = re.split(r'\s*=\s*', original[i], 1)
                    if len(split) == 2:
                        key = split[0]
                        value = split[1]
                        if key in dir(self):
                            if isinstance(getattr(self, key), bool):
                                setattr(self, key, StringToBoolean(value))
                            else:
                                if len(getattr(self, key)) > 0:
                                    setattr(self, key, f"{getattr(self, key)}, {value}")
                                else:
                                    setattr(self, key, value)
            except ValueError as e:
                raise self.InvalidConfigurationFileException(
                    "[Interface] section not found in " + self.configPath)
            if self.PrivateKey:
                self.PublicKey = self.__getPublicKey()
            self.Status = self.getStatus()

    def __dropDatabase(self):
        existingTables = [self.Name, f'{self.Name}_restrict_access', f'{self.Name}_transfer', f'{self.Name}_deleted', f'{self.Name}_traffic_snapshot']
        try:
            with self.engine.begin() as conn:
                for t in existingTables:
                    conn.execute(
                        sqlalchemy.text(
                            f'DROP TABLE "{t}"'
                        )
                    )
        except Exception as e:
            current_app.logger.error("Dropping table failed")
            return False
        return True

    def createDatabase(self, dbName = None):
        def generate_column_obj():
            return [
                sqlalchemy.Column('id', sqlalchemy.String(255), nullable=False, primary_key=True),
                sqlalchemy.Column('private_key', sqlalchemy.String(255)),
                sqlalchemy.Column('DNS', sqlalchemy.Text),
                sqlalchemy.Column('endpoint_allowed_ip', sqlalchemy.Text),
                sqlalchemy.Column('name', sqlalchemy.Text),
                sqlalchemy.Column('total_receive', sqlalchemy.Float),
                sqlalchemy.Column('total_sent', sqlalchemy.Float),
                sqlalchemy.Column('total_data', sqlalchemy.Float),
                sqlalchemy.Column('endpoint', sqlalchemy.String(255)),
                sqlalchemy.Column('status', sqlalchemy.String(255)),
                sqlalchemy.Column('latest_handshake', sqlalchemy.String(255)),
                sqlalchemy.Column('allowed_ip', sqlalchemy.String(255)),
                sqlalchemy.Column('cumu_receive', sqlalchemy.Float),
                sqlalchemy.Column('cumu_sent', sqlalchemy.Float),
                sqlalchemy.Column('cumu_data', sqlalchemy.Float),
                sqlalchemy.Column('mtu', sqlalchemy.Integer),
                sqlalchemy.Column('keepalive', sqlalchemy.Integer),
                sqlalchemy.Column('notes', sqlalchemy.Text),
                sqlalchemy.Column('remote_endpoint', sqlalchemy.String(255)),
                sqlalchemy.Column('preshared_key', sqlalchemy.String(255)),
                sqlalchemy.Column('restricted_reason', sqlalchemy.String(255))
            ]

        if dbName is None:
            dbName = self.Name

        self.peersTable = sqlalchemy.Table(
            f'{dbName}', self.metadata, *generate_column_obj(), extend_existing=True
        )

        self.peersRestrictedTable = sqlalchemy.Table(
            f'{dbName}_restrict_access', self.metadata, *generate_column_obj(), extend_existing=True
        )

        self.peersDeletedTable = sqlalchemy.Table(
            f'{dbName}_deleted', self.metadata, *generate_column_obj(), extend_existing=True
        )

        if self.DashboardConfig.GetConfig("Database", "type")[1] == 'sqlite':
            time_col_type = sqlalchemy.DATETIME
        else:
            time_col_type = sqlalchemy.TIMESTAMP

        self.peersTransferTable = sqlalchemy.Table(
            f'{dbName}_transfer', self.metadata,
            sqlalchemy.Column('id', sqlalchemy.String(255), nullable=False),
            sqlalchemy.Column('total_receive', sqlalchemy.Float),
            sqlalchemy.Column('total_sent', sqlalchemy.Float),
            sqlalchemy.Column('total_data', sqlalchemy.Float),
            sqlalchemy.Column('cumu_receive', sqlalchemy.Float),
            sqlalchemy.Column('cumu_sent', sqlalchemy.Float),
            sqlalchemy.Column('cumu_data', sqlalchemy.Float),
            sqlalchemy.Column('time', time_col_type, server_default=sqlalchemy.func.now()),
            extend_existing=True
        )
        
        self.peersHistoryEndpointTable = sqlalchemy.Table(
            f'{dbName}_history_endpoint', self.metadata,
            sqlalchemy.Column('id', sqlalchemy.String(255), nullable=False),
            sqlalchemy.Column('endpoint', sqlalchemy.String(255), nullable=False),
            sqlalchemy.Column('time', time_col_type)
        )
        
        self.infoTable = sqlalchemy.Table(
            'ConfigurationsInfo', self.metadata,
            sqlalchemy.Column('ID', sqlalchemy.String(255), primary_key=True),
            sqlalchemy.Column('Info', sqlalchemy.Text),
            extend_existing=True
        )

        self.interfaceTrafficSnapshotTable = sqlalchemy.Table(
            f'{dbName}_traffic_snapshot', self.metadata,
            sqlalchemy.Column('configuration_name', sqlalchemy.String(255), primary_key=True),
            sqlalchemy.Column('total_receive', sqlalchemy.Float, server_default='0.0'),
            sqlalchemy.Column('total_sent', sqlalchemy.Float, server_default='0.0'),
            sqlalchemy.Column('total_data', sqlalchemy.Float, server_default='0.0'),
            sqlalchemy.Column('last_updated', time_col_type, server_default=sqlalchemy.func.now()),
            extend_existing=True
        )

        self.metadata.create_all(self.engine)

    def __dumpDatabase(self):
        with self.engine.connect() as conn:
            tables = [self.peersTable, self.peersRestrictedTable, self.peersTransferTable, self.peersDeletedTable, self.peersHistoryEndpointTable, self.interfaceTrafficSnapshotTable]
            for i in tables:
                rows = conn.execute(i.select()).mappings().fetchall()
                for row in rows:
                    insert_stmt = i.insert().values(dict(row))
                    yield str(insert_stmt.compile(compile_kwargs={"literal_binds": True}))

    def __importDatabase(self, sqlFilePath, restore = False) -> bool:
        if not restore:
            self.__dropDatabase()
        self.createDatabase()
        allowed_tables = [
            self.Name,
            f"{self.Name}_restrict_access",
            f"{self.Name}_deleted",
            f"{self.Name}_transfer",
            f"{self.Name}_history_endpoint"
        ]
        escaped_tables = "|".join(re.escape(t) for t in allowed_tables)
        allowed_insert_pattern = re.compile(
            rf'^INSERT\s+INTO\s+["`]?({escaped_tables})["`]?\s*\(',
            re.IGNORECASE
        )
        if not os.path.exists(sqlFilePath):
            return False
        with self.engine.begin() as conn:
            with open(sqlFilePath, 'r') as f:
                for l in f.readlines():
                    l = l.rstrip("\n").strip()
                    if len(l) > 0:
                        if allowed_insert_pattern.match(l):
                            conn.execute(sqlalchemy.text(l))
                        else:
                            current_app.logger.warning(f"Unsafe SQL line skipped during import: {l[:100]}")
        return True

    def __getPublicKey(self) -> str:
        return GenerateWireguardPublicKey(self.PrivateKey)[1]

    def getStatus(self) -> bool:
        self.Status = self.Name in psutil.net_if_addrs().keys()
        return self.Status

    def getAutostartStatus(self):
        s, d = self.DashboardConfig.GetConfig("WireGuardConfiguration", "autostart")
        return self.Name in d
    
    def addAutostart(self):
        s, d = self.DashboardConfig.GetConfig("WireGuardConfiguration", "autostart")
        if self.Name not in d:
            d.append(self.Name)
            self.DashboardConfig.SetConfig("WireGuardConfiguration", "autostart", d)
    
    def removeAutostart(self):
        s, d = self.DashboardConfig.GetConfig("WireGuardConfiguration", "autostart")
        if self.Name in d:
            d.remove(self.Name)
            self.DashboardConfig.SetConfig("WireGuardConfiguration", "autostart", d)

    def getRestrictedPeers(self):
        self.RestrictedPeers = []
        with self.engine.connect() as conn:
            restricted = conn.execute(self.peersRestrictedTable.select()).mappings().fetchall()
            for i in restricted:
                self.RestrictedPeers.append(Peer(i, self))

    def configurationFileChanged(self) :
        mt = os.path.getmtime(self.configPath)
        changed = self.__configFileModifiedTime is None or self.__configFileModifiedTime != mt
        self.__configFileModifiedTime = mt
        return changed

    def getPeers(self):
        tmpList = []        
        if self.configurationFileChanged():
            with open(self.configPath, 'r') as configFile:
                p = []
                pCounter = -1
                content = configFile.read().split('\n')
                try:
                    if "[Peer]" not in content:
                        current_app.logger.info(f"{self.Name} config has no [Peer] section")
                        self.Peers = []
                        return

                    peerStarts = content.index("[Peer]")
                    content = content[peerStarts:]
                    for i in content:
                        if not RegexMatch("#(.*)", i) and not RegexMatch(";(.*)", i):
                            if i == "[Peer]":
                                pCounter += 1
                                p.append({})
                                p[pCounter]["name"] = ""
                            else:
                                if len(i) > 0:
                                    split = re.split(r'\s*=\s*', i, 1)
                                    if len(split) == 2:
                                        p[pCounter][split[0]] = split[1]

                        if RegexMatch("#Name# = (.*)", i):
                            split = re.split(r'\s*=\s*', i, 1)
                            if len(split) == 2:
                                p[pCounter]["name"] = split[1]
                    
                    existing_peers = {}
                    with self.engine.connect() as conn:
                        for row in conn.execute(self.peersTable.select()).mappings().fetchall():
                            existing_peers[row['id']] = row

                    inserts = []
                    updates = []
                    
                    for i in p:
                        if "PublicKey" in i.keys():
                            tempPeer = existing_peers.get(i['PublicKey'])
                            
                            if tempPeer is None:
                                newPeer = {
                                    "id": i['PublicKey'],
                                    "private_key": "",
                                    "DNS": self.DashboardConfig.GetConfig("Peers", "peer_global_DNS")[1],
                                    "endpoint_allowed_ip": self.DashboardConfig.GetConfig("Peers", "peer_endpoint_allowed_ip")[1],
                                    "name": i.get("name"),
                                    "total_receive": 0,
                                    "total_sent": 0,
                                    "total_data": 0,
                                    "endpoint": "N/A",
                                    "status": "stopped",
                                    "latest_handshake": "N/A",
                                    "allowed_ip": i.get("AllowedIPs", "N/A"),
                                    "cumu_receive": 0,
                                    "cumu_sent": 0,
                                    "cumu_data": 0,
                                    "mtu": self.DashboardConfig.GetConfig("Peers", "peer_mtu")[1] if len(self.DashboardConfig.GetConfig("Peers", "peer_mtu")[1]) > 0 else None,
                                    "keepalive": self.DashboardConfig.GetConfig("Peers", "peer_keep_alive")[1] if len(self.DashboardConfig.GetConfig("Peers", "peer_keep_alive")[1]) > 0 else None,
                                    "notes": "",
                                    "remote_endpoint": self.DashboardConfig.GetConfig("Peers", "remote_endpoint")[1],
                                    "preshared_key": i["PresharedKey"] if "PresharedKey" in i.keys() else ""
                                }
                                inserts.append(newPeer)
                                tmpList.append(Peer(newPeer, self))
                            else:
                                updates.append({
                                    "b_id": i['PublicKey'],
                                    "b_allowed_ip": i.get("AllowedIPs", "N/A")
                                })
                                merged = dict(tempPeer)
                                merged["allowed_ip"] = i.get("AllowedIPs", "N/A")
                                tmpList.append(Peer(merged, self))
                                
                    if len(inserts) > 0:
                        with self.engine.begin() as conn:
                            conn.execute(self.peersTable.insert(), inserts)
                            
                    if len(updates) > 0:
                        with self.engine.begin() as conn:
                            stmt = self.peersTable.update().where(
                                self.peersTable.columns.id == sqlalchemy.bindparam('b_id')
                            ).values(
                                allowed_ip=sqlalchemy.bindparam('b_allowed_ip')
                            )
                            conn.execute(stmt, updates)
                except Exception as e:
                    current_app.logger.error(f"{self.Name} getPeers() Error: {e}")
        else:
            with self.engine.connect() as conn:
                existingPeers = conn.execute(self.peersTable.select()).mappings().fetchall()
                for i in existingPeers:
                    tmpList.append(Peer(i, self))
        self.Peers = tmpList
    
    def logPeersTraffic(self):
        inserts = []
        now = datetime.now()
        for tempPeer in self.Peers:
            if tempPeer.status == "running":
                inserts.append({
                    "id": tempPeer.id,
                    "total_receive": tempPeer.total_receive,
                    "total_sent": tempPeer.total_sent,
                    "total_data": tempPeer.total_data,
                    "cumu_sent": tempPeer.cumu_sent,
                    "cumu_receive": tempPeer.cumu_receive,
                    "cumu_data": tempPeer.cumu_data,
                    "time": now
                })
        if len(inserts) > 0:
            with self.engine.begin() as conn:
                conn.execute(self.peersTransferTable.insert(), inserts)
    
    def logPeersHistoryEndpoint(self):
        peer_ids = [tempPeer.id for tempPeer in self.Peers if tempPeer.status == "running"]
        if not peer_ids:
            return
            
        existing = set()
        with self.engine.connect() as conn:
            rows = conn.execute(
                self.peersHistoryEndpointTable.select().where(
                    self.peersHistoryEndpointTable.c.id.in_(peer_ids)
                )
            ).mappings().fetchall()
            for r in rows:
                existing.add((r['id'], r['endpoint']))
                
        inserts = []
        now = datetime.now()
        for tempPeer in self.Peers:
            if tempPeer.status == "running":
                endpoint = tempPeer.endpoint.rsplit(":", 1)    
                if len(endpoint) == 2 and len(endpoint[0]) > 0:
                    if (tempPeer.id, endpoint[0]) not in existing:
                        inserts.append({
                            "id": tempPeer.id,
                            "endpoint": endpoint[0],
                            "time": now
                        })
                        existing.add((tempPeer.id, endpoint[0]))
                        
        if len(inserts) > 0:
            with self.engine.begin() as conn:
                conn.execute(self.peersHistoryEndpointTable.insert(), inserts)
                          
    def addPeers(self, peers: list) -> tuple[bool, list, str]:
        result = {
            "message": None,
            "peers": []
        }
        try:
            cleanedAllowedIPs = {}
            for p in peers:
                newAllowedIPs = p['allowed_ip'].replace(" ", "")
                if not CheckAddress(newAllowedIPs):
                    return False, [], "Allowed IPs entry format is incorrect"
                if not CheckPeerKey(p["id"]):
                    return False, [], "Peer key format is incorrect"
                cleanedAllowedIPs[p["id"]] = newAllowedIPs

            with self.engine.begin() as conn:
                for i in peers:
                    newPeer = {
                        "id": i['id'],
                        "private_key": i['private_key'],
                        "DNS": i['DNS'],
                        "endpoint_allowed_ip": i['endpoint_allowed_ip'],
                        "name": i['name'],
                        "total_receive": 0,
                        "total_sent": 0,
                        "total_data": 0,
                        "endpoint": "N/A",
                        "status": "stopped",
                        "latest_handshake": "N/A",
                        "allowed_ip": i.get("allowed_ip", "N/A"),
                        "cumu_receive": 0,
                        "cumu_sent": 0,
                        "cumu_data": 0,
                        "mtu": i['mtu'],
                        "keepalive": i['keepalive'],
                        "notes": i.get("notes", ""),
                        "remote_endpoint": self.DashboardConfig.GetConfig("Peers", "remote_endpoint")[1],
                        "preshared_key": i["preshared_key"]
                    }
                    conn.execute(
                        self.peersTable.insert().values(newPeer)
                    )
            for p in peers:
                presharedKeyExist = len(p['preshared_key']) > 0
                rd = random.Random()
                uid = str(uuid.UUID(int=rd.getrandbits(128), version=4))
                try:
                    if presharedKeyExist:
                        with open(uid, "w+") as f:
                            f.write(p['preshared_key'])

                    command = [self.Protocol, "set", self.Name, "peer", p['id'], "allowed-ips", cleanedAllowedIPs[p["id"]], "preshared-key", uid if presharedKeyExist else "/dev/null"]
                    subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)
                finally:
                    if presharedKeyExist and os.path.exists(uid):
                        os.remove(uid)

            command = [f"{self.Protocol}-quick", "save", self.Name]
            subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)

            self.getPeers()
            for p in peers:
                p = self.searchPeer(p['id'])
                if p[0]:
                    result['peers'].append(p[1])
            self.DashboardWebHooks.RunWebHook("peer_created", {
                "configuration": self.Name,
                "peers": list(map(lambda k : k['id'], peers))
            })
        except Exception as e:
            current_app.logger.error(f"Add peers error: {e}")
            return False, [], "Internal server error"
        return True, result['peers'], ""

    def searchPeer(self, publicKey):
        if not publicKey or not CheckPeerKey(publicKey):
            return False, None
        if not hasattr(self, '_peers_dict_cache_id') or self._peers_dict_cache_id != id(self.Peers):
            self._peers_dict = {p.id: p for p in self.Peers}
            self._peers_dict_cache_id = id(self.Peers)
            
        p = self._peers_dict.get(publicKey)
        if p is not None:
            return True, p
        return False, None

    def allowAccessPeers(self, listOfPublicKeys) -> tuple[bool, str]:
        if not self.getStatus():
            self.toggleConfiguration()
        with self.engine.begin() as conn:
            for i in listOfPublicKeys:
                stmt = self.peersRestrictedTable.select().where(
                    self.peersRestrictedTable.columns.id == i
                )
                restrictedPeer = conn.execute(stmt).mappings().fetchone()
                if restrictedPeer is not None:
                    conn.execute(
                        self.peersTable.insert().from_select(
                            [c.name for c in self.peersTable.columns],
                            stmt
                        )
                    )
                    conn.execute(
                        self.peersRestrictedTable.delete().where(
                            self.peersRestrictedTable.columns.id == i
                        )
                    )

                    presharedKeyExist = len(restrictedPeer['preshared_key']) > 0
                    rd = random.Random()
                    uid = str(uuid.UUID(int=rd.getrandbits(128), version=4))
                    newAllowedIPs = restrictedPeer['allowed_ip'].replace(" ", "")
                    if not CheckAddress(newAllowedIPs):
                        return False, "Allowed IPs entry format is incorrect"

                    if not CheckPeerKey(restrictedPeer["id"]):
                        return False, "Peer key format is incorrect"

                    try:
                        if presharedKeyExist:
                            with open(uid, "w+") as f:
                                f.write(restrictedPeer['preshared_key'])

                        command = [self.Protocol, "set", self.Name, "peer", restrictedPeer["id"], "allowed-ips", newAllowedIPs, "preshared-key", uid if presharedKeyExist else "/dev/null"]
                        subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)
                    finally:
                        if presharedKeyExist and os.path.exists(uid):
                            os.remove(uid)
                else:
                    return False, "Failed to allow access of peer " + i
        if not self.__wgSave():
            return False, "Failed to save configuration through WireGuard"
        self.getPeers()
        return True, "Allow access successfully"

    def restrictPeers(self, listOfPublicKeys, reason=None) -> tuple[bool, str]:
        numOfRestrictedPeers = 0
        numOfFailedToRestrictPeers = 0
        if not self.getStatus():
            self.toggleConfiguration()

        with self.engine.begin() as conn:
            for p in listOfPublicKeys:
                found, pf = self.searchPeer(p)
                if found:
                    try:
                        command = [self.Protocol, "set", self.Name, "peer", pf.id, "remove"]
                        subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)

                        conn.execute(
                            self.peersRestrictedTable.insert().from_select(
                                [c.name for c in self.peersTable.columns],
                                self.peersTable.select().where(
                                    self.peersTable.columns.id == pf.id
                                )
                            )
                        )
                        conn.execute(
                            self.peersRestrictedTable.update().values({
                                "status": "stopped",
                                "restricted_reason": reason
                            }).where(
                                self.peersRestrictedTable.columns.id == pf.id
                            )
                        )
                        conn.execute(
                            self.peersTable.delete().where(
                                self.peersTable.columns.id == pf.id
                            )
                        )
                        numOfRestrictedPeers += 1
                    except Exception as e:
                        traceback.print_stack()
                        numOfFailedToRestrictPeers += 1

        if not self.__wgSave():
            return False, "Failed to save configuration through WireGuard"
        self.getRestrictedPeers()
        self.getPeers()
        if numOfRestrictedPeers == len(listOfPublicKeys):
            return True, f"Restricted {numOfRestrictedPeers} peer(s)"
        return False, f"Restricted {numOfRestrictedPeers} peer(s) successfully. Failed to restrict {numOfFailedToRestrictPeers} peer(s)"


    def deletePeers(self, listOfPublicKeys, AllPeerJobs: PeerJobs, AllPeerShareLinks: PeerShareLinks) -> tuple[bool, str]:
        numOfDeletedPeers = 0
        numOfFailedToDeletePeers = 0
        deleted = []
        if not self.getStatus():
            try:
                self.toggleConfiguration()
            except Exception:
                pass
        with self.engine.begin() as conn:
            for p in listOfPublicKeys:
                found, pf = self.searchPeer(p)
                is_restricted = False
                if not found:
                    for restricted_peer in self.RestrictedPeers:
                        if restricted_peer.id == p:
                            found = True
                            pf = restricted_peer
                            is_restricted = True
                            break
                if found:
                    for job in pf.jobs:
                        AllPeerJobs.deleteJob(job)
                    for shareLink in pf.ShareLink:
                        AllPeerShareLinks.updateLinkExpireDate(shareLink.ShareID, datetime.now())
                    try:
                        peer_total_receive = (pf.cumu_receive or 0) + (pf.total_receive or 0)
                        peer_total_sent    = (pf.cumu_sent or 0) + (pf.total_sent or 0)
                        peer_total_data    = (pf.cumu_data or 0) + (pf.total_data or 0)
                        self._add_to_traffic_snapshot(conn, peer_total_receive, peer_total_sent, peer_total_data)
                        
                        if not is_restricted:
                            try:
                                command = [self.Protocol, "set", self.Name, "peer", pf.id, "remove"]
                                subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)
                            except Exception:
                                pass
                        conn.execute(
                            self.peersTable.delete().where(
                                self.peersTable.columns.id == pf.id
                            )
                        )
                        conn.execute(
                            self.peersRestrictedTable.delete().where(
                                self.peersRestrictedTable.columns.id == pf.id
                            )
                        )
                        deleted.append(pf.id)
                        numOfDeletedPeers += 1
                    except Exception as e:
                        numOfFailedToDeletePeers += 1

        if not self.__wgSave():
            return False, "Failed to save configuration through WireGuard"

        self.getPeers()
        
        if numOfDeletedPeers == 0 and numOfFailedToDeletePeers == 0:
            return False, "No peer(s) to delete found"
        
        if numOfDeletedPeers == len(listOfPublicKeys):
            self.DashboardWebHooks.RunWebHook("peer_deleted", {
                "configuration": self.Name,
                "peers": deleted
            })
            return True, f"Deleted {numOfDeletedPeers} peer(s)"
        
        return False, f"Deleted {numOfDeletedPeers} peer(s) successfully. Failed to delete {numOfFailedToDeletePeers} peer(s)"

    def __wgSave(self) -> tuple[bool, str] | tuple[bool, None]:
        try:
            command = [f"{self.Protocol}-quick", "save", self.Name]
            subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)

            return True, None
        except Exception as e:
            current_app.logger.error(f"Failed to process command:\n{str(e)}")
            return False, "Internal server error"

    def getPeersLatestHandshake(self):
        try:
            if not self.getStatus():
                self.toggleConfiguration()
            try:
                command = [self.Protocol, "show", self.Name, "latest-handshakes"]
                latestHandshake = subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)
            except subprocess.CalledProcessError:
                return "stopped"
            latestHandshake = latestHandshake.decode("UTF-8").split()
            count = 0
            now = datetime.now()
            time_delta = timedelta(minutes=3)

            updates = []
            peer_dict = {p.id: p for p in self.Peers}
            for _ in range(int(len(latestHandshake) / 2)):
                minus = now - datetime.fromtimestamp(int(latestHandshake[count + 1]))
                if minus < time_delta:
                    status = "running"
                else:
                    status = "stopped"
                
                if int(latestHandshake[count + 1]) > 0:
                    lh_str = str(minus).split(".", maxsplit=1)[0]
                else:
                    lh_str = "No Handshake"
                    
                pubkey = latestHandshake[count]
                p = peer_dict.get(pubkey)
                if p is None or p.latest_handshake != lh_str or p.status != status:
                    updates.append({
                        "b_id": pubkey,
                        "b_latest_handshake": lh_str,
                        "b_status": status
                    })
                count += 2
                
            if len(updates) > 0:
                with self.engine.begin() as conn:
                    stmt = self.peersTable.update().where(
                        self.peersTable.columns.id == sqlalchemy.bindparam('b_id')
                    ).values(
                        latest_handshake=sqlalchemy.bindparam('b_latest_handshake'),
                        status=sqlalchemy.bindparam('b_status')
                    )
                    conn.execute(stmt, updates)
        except Exception as e:
            current_app.logger.error(f"Error in getPeersLatestHandshake for {self.Name}: {e}")

    def getPeersTransfer(self):
        try:
            if not self.getStatus():
                self.toggleConfiguration()
            # try:
            command = [self.Protocol, "show", self.Name, "transfer"]
            data_usage = subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)

            data_usage = data_usage.decode("UTF-8").split("\n")
            
            data_usage = [p.split("\t") for p in data_usage]
            
            existing_peers = {}
            with self.engine.connect() as conn:
                for row in conn.execute(self.peersTable.select()).mappings().fetchall():
                    existing_peers[row['id']] = row
                    
            cumu_updates = []
            total_updates = []
            
            for i in range(len(data_usage)):
                if len(data_usage[i]) == 3:
                    cur_i = existing_peers.get(data_usage[i][0])
                    if cur_i is not None:
                        total_sent = cur_i['total_sent']
                        total_receive = cur_i['total_receive']
                        cur_total_sent = float(data_usage[i][2]) / (1024 ** 3)
                        cur_total_receive = float(data_usage[i][1]) / (1024 ** 3)
                        cumulative_receive = cur_i['cumu_receive'] + total_receive
                        cumulative_sent = cur_i['cumu_sent'] + total_sent
                        if (total_sent * 0.999 ) <= cur_total_sent and (total_receive * 0.999) <= cur_total_receive: 
                            total_sent = cur_total_sent
                            total_receive = cur_total_receive
                        else:
                            cumu_updates.append({
                                "b_id": data_usage[i][0],
                                "b_cumu_receive": cumulative_receive,
                                "b_cumu_sent": cumulative_sent,
                                "b_cumu_data": cumulative_sent + cumulative_receive
                            })
                            total_sent = 0
                            total_receive = 0
                        
                        status, p = self.searchPeer(data_usage[i][0])
                        if status and (p.total_receive != total_receive or p.total_sent != total_sent):
                            total_updates.append({
                                "b_id": data_usage[i][0],
                                "b_total_receive": total_receive,
                                "b_total_sent": total_sent,
                                "b_total_data": total_receive + total_sent
                            })

            with self.engine.begin() as conn:
                if len(cumu_updates) > 0:
                    stmt_cumu = self.peersTable.update().where(
                        self.peersTable.columns.id == sqlalchemy.bindparam('b_id')
                    ).values(
                        cumu_receive=sqlalchemy.bindparam('b_cumu_receive'),
                        cumu_sent=sqlalchemy.bindparam('b_cumu_sent'),
                        cumu_data=sqlalchemy.bindparam('b_cumu_data')
                    )
                    conn.execute(stmt_cumu, cumu_updates)
                    
                if len(total_updates) > 0:
                    stmt_total = self.peersTable.update().where(
                        self.peersTable.columns.id == sqlalchemy.bindparam('b_id')
                    ).values(
                        total_receive=sqlalchemy.bindparam('b_total_receive'),
                        total_sent=sqlalchemy.bindparam('b_total_sent'),
                        total_data=sqlalchemy.bindparam('b_total_data')
                    )
                    conn.execute(stmt_total, total_updates)
        except Exception as e:
            current_app.logger.error(f"Error in getPeersTransfer for {self.Name}: {e}")

    def getPeersEndpoint(self):
        try:
            if not self.getStatus():
                self.toggleConfiguration()
            try:
                command = [self.Protocol, "show", self.Name, "endpoints"]
                data_usage = subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)
            except subprocess.CalledProcessError:
                return "stopped"

            data_usage = data_usage.decode("UTF-8").split()
            count = 0
            updates = []
            peer_dict = {p.id: p for p in self.Peers}
            for _ in range(int(len(data_usage) / 2)):
                pubkey = data_usage[count]
                endpoint = data_usage[count + 1]
                p = peer_dict.get(pubkey)
                if p is None or p.endpoint != endpoint:
                    updates.append({
                        "b_id": pubkey,
                        "b_endpoint": endpoint
                    })
                count += 2
                
            if len(updates) > 0:
                with self.engine.begin() as conn:
                    stmt = self.peersTable.update().where(
                        self.peersTable.columns.id == sqlalchemy.bindparam('b_id')
                    ).values(
                        endpoint=sqlalchemy.bindparam('b_endpoint')
                    )
                    conn.execute(stmt, updates)
        except Exception as e:
            current_app.logger.error(f"Error in getPeersEndpoint for {self.Name}: {e}")

    def toggleConfiguration(self) -> tuple[bool, str] | tuple[bool, None]:
        self.getStatus()
        if self.Status:
            try:
                command = [f"{self.Protocol}-quick", "down", self.Name]
                check = subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)

                self.removeAutostart()
            except subprocess.CalledProcessError as exc:
                return False, str(exc.output.strip().decode("utf-8"))
        else:
            try:
                command = [f"{self.Protocol}-quick", "up", self.Name]
                check = subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)

                self.addAutostart()
            except subprocess.CalledProcessError as exc:
                return False, str(exc.output.strip().decode("utf-8"))
        self.__parseConfigurationFile()
        self.getStatus()
        return True, None

    def getPeersList(self):
        return self.Peers

    def getRestrictedPeersList(self) -> list:
        self.getRestrictedPeers()
        return self.RestrictedPeers

    def _get_traffic_snapshot(self):
        try:
            with self.engine.connect() as conn:
                existing = conn.execute(
                    self.interfaceTrafficSnapshotTable.select().where(
                        self.interfaceTrafficSnapshotTable.c.configuration_name == self.Name
                    )
                ).mappings().first()
                if existing:
                    return dict(existing)
        except Exception as e:
            current_app.logger.error(f"{self.Name} _get_traffic_snapshot() Error: {e}")
        return None

    def _add_to_traffic_snapshot(self, conn, recv, sent, total):
        try:
            existing = conn.execute(
                self.interfaceTrafficSnapshotTable.select().where(
                    self.interfaceTrafficSnapshotTable.c.configuration_name == self.Name
                )
            ).mappings().first()
            
            if existing:
                conn.execute(
                    self.interfaceTrafficSnapshotTable.update()
                    .where(self.interfaceTrafficSnapshotTable.c.configuration_name == self.Name)
                    .values(
                        total_receive = existing['total_receive'] + recv,
                        total_sent    = existing['total_sent'] + sent,
                        total_data    = existing['total_data'] + total,
                        last_updated  = datetime.now()
                    )
                )
            else:
                conn.execute(
                    self.interfaceTrafficSnapshotTable.insert().values(
                        configuration_name = self.Name,
                        total_receive = recv,
                        total_sent    = sent,
                        total_data    = total,
                        last_updated  = datetime.now()
                    )
                )
        except Exception as e:
            current_app.logger.error(f"{self.Name} _add_to_traffic_snapshot() Error: {e}")

    def toJson(self):
        self.Status = self.getStatus()
        
        snapshot = self._get_traffic_snapshot()
        snap_total = snapshot.get('total_data', 0) if snapshot else 0
        snap_sent = snapshot.get('total_sent', 0) if snapshot else 0
        snap_receive = snapshot.get('total_receive', 0) if snapshot else 0
        
        return {
            "Status": self.Status,
            "Name": self.Name,
            "PrivateKey": self.PrivateKey,
            "PublicKey": self.PublicKey,
            "Address": self.Address,
            "ListenPort": self.ListenPort,
            "PreUp": self.PreUp,
            "PreDown": self.PreDown,
            "PostUp": self.PostUp,
            "PostDown": self.PostDown,
            "SaveConfig": self.SaveConfig,
            "DataUsage": {
                "Total": sum(list(map(lambda x: (x.cumu_data or 0) + (x.total_data or 0), self.Peers))) + sum(list(map(lambda x: (x.cumu_data or 0) + (x.total_data or 0), self.RestrictedPeers))) + snap_total,
                "Sent": sum(list(map(lambda x: (x.cumu_sent or 0) + (x.total_sent or 0), self.Peers))) + sum(list(map(lambda x: (x.cumu_sent or 0) + (x.total_sent or 0), self.RestrictedPeers))) + snap_sent,
                "Receive": sum(list(map(lambda x: (x.cumu_receive or 0) + (x.total_receive or 0), self.Peers))) + sum(list(map(lambda x: (x.cumu_receive or 0) + (x.total_receive or 0), self.RestrictedPeers))) + snap_receive
            },
            "ConnectedPeers": len(list(filter(lambda x: x.status == "running", self.Peers))),
            "TotalPeers": len(self.Peers),
            "Protocol": self.Protocol,
            "Table": self.Table,
            "Info": self.configurationInfo.model_dump()
        }

    def backupConfigurationFile(self) -> tuple[bool, dict[str, str]]:
        if not os.path.exists(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup')):
            os.mkdir(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup'))
        time = datetime.now().strftime("%Y%m%d%H%M%S")
        shutil.copy(
            self.configPath,
            os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', f'{self.Name}_{time}.conf')
        )
        with open(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', f'{self.Name}_{time}.sql'), 'w+') as f:
            for l in self.__dumpDatabase():
                f.write(l + "\n")

        return True, {
            "filename": f'{self.Name}_{time}.conf',
            "backupDate": datetime.now().strftime("%Y%m%d%H%M%S")
        }

    def getBackups(self, databaseContent: bool = False) -> list[dict[str, str]]:
        backups = []

        directory = os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup')
        if not os.path.exists(directory):
            return []

        files = [(file, os.path.getctime(os.path.join(directory, file)))
                 for file in os.listdir(directory) if os.path.isfile(os.path.join(directory, file))]
        files.sort(key=lambda x: x[1], reverse=True)

        for f, ct in files:
            pattern = rf"^({re.escape(self.Name)})_(\d+)\.conf$"
            if RegexMatch(pattern, f):
                s = re.search(pattern, f)
                date = s.group(2)
                conf_file_path = os.path.join(directory, f)
                with open(conf_file_path, 'r', encoding='utf-8', errors='ignore') as conf_file:
                    conf_content = conf_file.read()
                d = {
                    "filename": f,
                    "backupDate": date,
                    "content": conf_content
                }
                sql_filename = f.replace(".conf", ".sql")
                if sql_filename in os.listdir(directory):
                    d['database'] = True
                    if databaseContent:
                        sql_file_path = os.path.join(directory, sql_filename)
                        with open(sql_file_path, 'r', encoding='utf-8', errors='ignore') as sql_file:
                            d['databaseContent'] = sql_file.read()
                backups.append(d)

        return backups

    def restoreBackup(self, backupFileName: str) -> bool:
        backups = list(map(lambda x : x['filename'], self.getBackups()))
        if backupFileName not in backups:
            return False
        if self.Status:
            self.toggleConfiguration()
        target = os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backupFileName)
        targetSQL = os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backupFileName.replace(".conf", ".sql"))
        if not os.path.exists(target):
            return False

        # Backup current config content for rollback
        original_content = None
        if os.path.exists(self.configPath):
            with open(self.configPath, 'r', encoding='utf-8', errors='ignore') as f:
                original_content = f.read()

        with open(target, 'r', encoding='utf-8', errors='ignore') as f:
            targetContent = f.read()

        try:
            with open(self.configPath, 'w', encoding='utf-8') as f:
                f.write(targetContent)
            self.__parseConfigurationFile()
            self.__importDatabase(targetSQL, restore=True)
            self.__initPeersList()
            return True
        except Exception as e:
            current_app.logger.error(f"Restoring backup failed: {e}")
            if original_content is not None:
                with open(self.configPath, 'w', encoding='utf-8') as f:
                    f.write(original_content)
                try:
                    self.__parseConfigurationFile()
                except Exception:
                    pass
            return False

    def deleteBackup(self, backupFileName: str) -> bool:
        backups = list(map(lambda x : x['filename'], self.getBackups()))
        if backupFileName not in backups:
            return False
        try:
            conf_path = os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backupFileName)
            sql_path = os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backupFileName.replace(".conf", ".sql"))
            if os.path.exists(conf_path):
                os.remove(conf_path)
            if os.path.exists(sql_path):
                os.remove(sql_path)
        except Exception as e:
            current_app.logger.error(f"Deleting backup failed: {e}")
            return False
        return True

    def downloadBackup(self, backupFileName: str) -> tuple[bool, str] | tuple[bool, None]:
        backup = list(filter(lambda x : x['filename'] == backupFileName, self.getBackups()))
        if len(backup) == 0:
            return False, None
        zip_name = f'{str(uuid.UUID(int=random.Random().getrandbits(128), version=4))}.zip'
        download_dir = 'download'
        os.makedirs(download_dir, exist_ok=True)
        zip_path = os.path.join(download_dir, zip_name)
        with ZipFile(zip_path, 'w') as zipF:
            zipF.write(
                os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backup[0]['filename']),
                os.path.basename(os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', backup[0]['filename']))
            )
            if backup[0].get('database', False):
                sql_file = backup[0]['filename'].replace('.conf', '.sql')
                sql_path = os.path.join(self.__getProtocolPath(), 'WGDashboard_Backup', sql_file)
                if os.path.exists(sql_path):
                    zipF.write(sql_path, os.path.basename(sql_path))

        return True, zip_name

    def updateConfigurationSettings(self, newData: dict) -> tuple[bool, str]:
        if self.Status:
            self.toggleConfiguration()
        original = []
        dataChanged = False
        with open(self.configPath, 'r') as f:
            original = [l.rstrip("\n") for l in f.readlines()]
            allowEdit = ["Address", "PreUp", "PostUp", "PreDown", "PostDown", "ListenPort", "Table"]
            if self.Protocol == 'awg':
                allowEdit += ["Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4", "I1", "I2", "I3", "I4", "I5"]
            start = original.index("[Interface]")
            try:
                end = original.index("[Peer]")
            except ValueError as e:
                end = len(original)
            new = ["[Interface]"]
            peerFound = False
            for line in range(start, end):
                split = re.split(r'\s*=\s*', original[line], 1)
                if len(split) == 2:
                    if split[0] not in allowEdit:
                        new.append(original[line])
            for key in allowEdit:
                new.insert(1, f"{key} = {str(newData[key]).strip()}")
            new.append("")
            for line in range(end, len(original)):
                new.append(original[line])
            self.backupConfigurationFile()
            with open(self.configPath, 'w') as f:
                f.write("\n".join(new))

        status, msg = self.toggleConfiguration()
        if not status:
            return False, msg
        for i in allowEdit:
            setattr(self, i, str(newData[i]))
                
        return True, ""

    def deleteConfiguration(self):
        if self.getStatus():
            self.toggleConfiguration()
        os.remove(self.configPath)
        self.__dropDatabase()
        return True

    def renameConfiguration(self, newConfigurationName) -> tuple[bool, str]:
        newConfigurationName = os.path.basename(newConfigurationName)

        if len(newConfigurationName) > 15 or not re.match(r'^[a-zA-Z0-9_=\+\.\-]{1,15}$', newConfigurationName):
            return False, "Configuration name is either too long or contains an illegal character"
        
        newConfigurationName = newConfigurationName.replace("`", "") # double check
    
        try:
            if self.getStatus():
                self.toggleConfiguration()
            self.createDatabase(newConfigurationName)
            with self.engine.begin() as conn:
                def doRenameStatement(suffix):
                    newConfig = f"{newConfigurationName}{suffix}"
                    oldConfig = f"{self.Name}{suffix}"

                    conn.execute(
                        sqlalchemy.text(
                            f'INSERT INTO `{newConfig}` SELECT * FROM `{oldConfig}`'
                        )
                    )

                doRenameStatement("")
                doRenameStatement("_restrict_access")
                doRenameStatement("_deleted")
                doRenameStatement("_transfer")

            self.AllPeerJobs.updateJobConfigurationName(self.Name, newConfigurationName)
            shutil.copy(
                self.configPath,
                os.path.join(self.__getProtocolPath(), f'{newConfigurationName}.conf')
            )
            self.deleteConfiguration()
        except Exception as e:
            current_app.logger.error(f"Failed to rename configuration.\nNew Configuration Name: {newConfigurationName}\nError: {str(e)}")
            return False, "Internal server error"
        return True, None

    def getNumberOfAvailableIP(self):
        if len(self.Address) < 0:
            return False, None
        existedAddress = set()
        availableAddress = {}
        for p in self.Peers + self.getRestrictedPeersList():
            peerAllowedIP = p.allowed_ip.split(',')
            for pip in peerAllowedIP:
                pip = pip.strip()
                if pip == "N/A":
                    continue
                ppip = pip.split('/')
                if len(ppip) == 2:
                    try:
                        check = ipaddress.ip_network(ppip[0])
                        existedAddress.add(check)
                    except Exception as e:
                        current_app.logger.error(f"{self.Name} peer {p.id} have invalid ip: {e}")
        configurationAddresses = self.Address.split(',')
        for ca in configurationAddresses:
            ca = ca.strip()
            caSplit = ca.split('/')
            try:
                if len(caSplit) == 2:
                    network = ipaddress.ip_network(ca, False)
                    existedAddress.add(ipaddress.ip_network(caSplit[0]))
                    availableAddress[ca] = network.num_addresses
                    for p in existedAddress:
                        if p.version == network.version and p.subnet_of(network):
                            availableAddress[ca] -= 1
            except Exception as e:
                current_app.logger.error(f"Error: Failed to parse IP address {ca} from {self.Name}: {e}")
        return True, availableAddress

    def getAvailableIP(self, threshold = 255):
        if len(self.Address) < 0:
            return False, None
        existedAddress = set()
        availableAddress = {}
        for p in self.Peers + self.getRestrictedPeersList():
            peerAllowedIP = p.allowed_ip.split(',')
            for pip in peerAllowedIP:
                pip = pip.strip()
                if pip == "N/A":
                    continue
                ppip = pip.split('/')
                if len(ppip) == 2:
                    try:
                        check = ipaddress.ip_network(ppip[0])
                        existedAddress.add(check.compressed)
                    except Exception as e:
                        current_app.logger.error(f"{self.Name} peer {p.id} have invalid ip: {e}")
        configurationAddresses = self.Address.split(',')
        for ca in configurationAddresses:
            ca = ca.strip()
            caSplit = ca.split('/')
            try:
                if len(caSplit) == 2:
                    network = ipaddress.ip_network(ca, False)
                    existedAddress.add(ipaddress.ip_network(caSplit[0]).compressed)
                    if threshold == -1:
                        availableAddress[ca] = filter(lambda ip : ip not in existedAddress,
                                                      map(lambda iph : ipaddress.ip_network(iph).compressed, network.hosts()))
                    else:
                        availableAddress[ca] = list(islice(filter(lambda ip : ip not in existedAddress,
                                                                  map(lambda iph : ipaddress.ip_network(iph).compressed, network.hosts())), threshold))
            except Exception as e:
                current_app.logger.error(f"Failed to parse IP address {ca} from {self.Name}: {e}")
        return True, availableAddress

    def getRealtimeTrafficUsage(self):
        import time
        now = time.time()
        stats = psutil.net_io_counters(pernic=True, nowrap=True)
        if self.Name in stats.keys():
            stat = stats[self.Name]
            if not hasattr(self, 'last_traffic_time') or not hasattr(self, 'last_traffic_stats'):
                self.last_traffic_time = now
                self.last_traffic_stats = stat
                return { "sent": 0.0, "recv": 0.0 }
            
            time_diff = now - self.last_traffic_time
            if time_diff <= 0:
                time_diff = 1.0
                
            recv_diff = stat.bytes_recv - self.last_traffic_stats.bytes_recv
            sent_diff = stat.bytes_sent - self.last_traffic_stats.bytes_sent
            
            net_in = round((recv_diff / 1024 / 1024) / time_diff, 3)
            net_out = round((sent_diff / 1024 / 1024) / time_diff, 3)
            
            self.last_traffic_time = now
            self.last_traffic_stats = stat
            
            return {
                "sent": max(0.0, net_out),
                "recv": max(0.0, net_in)
            }
        else:
            return { "sent": 0.0, "recv": 0.0 }
    
    '''
    Manager WireGuard Configuration Information
    '''
    
    def readConfigurationInfo(self):
        with self.engine.connect() as conn:
            result = conn.execute(
                self.infoTable.select().where(
                    self.infoTable.c.ID == self.Name
                )
            ).mappings().fetchone()
        return result
    
    def initConfigurationInfo(self):
        with self.engine.begin() as conn:
            conn.execute(
                self.infoTable.insert().values(
                    {
                        "ID": self.Name,
                        "Info": self.configurationInfo.model_dump_json()
                    }
                )
            )
    
    def storeConfigurationInfo(self):
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    self.infoTable.update().values(
                        {
                            "Info": self.configurationInfo.model_dump_json()
                        }
                    ).where(
                        self.infoTable.c.ID == self.Name
                    )
                )
        except Exception as e:
            return False
        
    def updateConfigurationInfo(self, key: str, value: str | dict[str, str] | dict[str, dict] | bool) -> tuple[bool, Any, str] | tuple[
        bool, str, None] | tuple[bool, None, None]:
        if key == "Description":
            self.configurationInfo.Description = value
        elif key == "OverridePeerSettings":
            for (key, val) in value.items():
                try:
                    status, msg = self.__validateOverridePeerSettings(key, _jinja_env.from_string(val).render(configuration=self.toJson()))
                    if not status:
                        return False, msg, key
                except Exception as e:
                    return False, str(e), None
            self.configurationInfo.OverridePeerSettings = (
                self.configurationInfo.OverridePeerSettings.model_validate(value))
        elif key == "PeerGroups":
            peerGroups = {}
            for name, data in value.items():
                peerGroups[name] = PeerGroupsClass(**data)
            self.configurationInfo.PeerGroups = peerGroups
        elif key == "PeerTrafficTracking":
            self.configurationInfo.PeerTrafficTracking = value
        elif key == "PeerHistoricalEndpointTracking":
            self.configurationInfo.PeerHistoricalEndpointTracking = value
        else: 
            return False, "Key does not exist", None
        self.storeConfigurationInfo()
        return True, None, None
    
    def __validateOverridePeerSettings(self, key: str, value: str | int) -> tuple[bool, None] | tuple[bool, str]:
        status = True
        msg = None
        if key == "DNS" and value:
            status, msg = ValidateDNSAddress(value)
        elif key == "EndpointAllowedIPs" and value:
            status, msg = ValidateEndpointAllowedIPs(value)
        elif key == "ListenPort" and value:
            if not value.isnumeric() or not (1 <= int(value) <= 65535):
                status = False
                msg = "Listen Port must be >= 1 and <= 65535"        
        return status, msg
        
    def getTransferTableSize(self):
        with self.engine.connect() as db:
            row_count = db.execute(
                sqlalchemy.select(sqlalchemy.func.count()).select_from(self.peersTransferTable)
            ).scalar()
            return int(row_count)

    def getHistoricalEndpointTableSize(self):
        with self.engine.connect() as db:
            row_count = db.execute(
                sqlalchemy.select(sqlalchemy.func.count()).select_from(self.peersHistoryEndpointTable)
            ).scalar()
            return int(row_count)
        
    def downloadTransferTable(self):
        with self.engine.connect() as db:
            data = db.execute(
                self.peersTransferTable.select()
            ).mappings().fetchall()
            return data

    def downloadHistoricalEndpointTable(self):
        with self.engine.connect() as db:
            data = db.execute(
                self.peersHistoryEndpointTable.select()
            ).mappings().fetchall()
            return data
    
    def deleteTransferTable(self):
        try:
            with self.engine.begin() as db:
                db.execute(
                    self.peersTransferTable.delete()
            )
            with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                if conn.dialect.name == 'sqlite':
                    print("[WGDashboard] SQLite Vacuuming Database")
                    conn.execute(sqlalchemy.text('VACUUM;'))
        except Exception as e:
            return False
        return True

    def deleteHistoryEndpointTable(self):
        try:
            with self.engine.begin() as db:
                db.execute(
                    self.peersHistoryEndpointTable.delete()
                )
            with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                if conn.dialect.name == 'sqlite':
                    print("[WGDashboard] SQLite Vacuuming Database")
                    conn.execute(sqlalchemy.text('VACUUM;'))
        except Exception as e:
            return False
        return True
