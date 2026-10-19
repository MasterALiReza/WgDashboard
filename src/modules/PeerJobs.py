"""
Peer Jobs
"""
import sqlalchemy, threading

from .DatabaseConnection import ConnectionString, get_engine
from .PeerJob import PeerJob
from .PeerJobLogger import PeerJobLogger
import sqlalchemy as db
from datetime import datetime, timezone
from flask import current_app

def _parse_job_datetime(val: str) -> tuple[datetime, bool]:
    """
    Parses a date string, ISO-8601 with timezone offset, or unix timestamp.
    Returns (datetime_object, is_utc_aware).
    """
    val_str = str(val).strip()
    # 1. UNIX epoch timestamp
    try:
        if val_str.replace('.', '', 1).isdigit() and len(val_str) >= 9:
            return datetime.fromtimestamp(float(val_str), tz=timezone.utc), True
    except Exception:
        pass

    # 2. ISO-8601 with 'T' or timezone offset
    try:
        dt = datetime.fromisoformat(val_str.replace('Z', '+00:00'))
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc), True
        return dt, False
    except Exception:
        pass

    # 3. Standard naive string format '%Y-%m-%d %H:%M:%S'
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(val_str, fmt), False
        except Exception:
            pass

    raise ValueError(f"Invalid date/time format for job value: {val}")

class PeerJobs:
    def __init__(self, DashboardConfig, WireguardConfigurations, AllPeerShareLinks):
        self.lock = threading.RLock()
        self.Jobs: list[PeerJob] = []
        self._index: dict[tuple[str, str], list[PeerJob]] = {}
        self._job_id_index: dict[str, PeerJob] = {}
        # PERF-04: Use shared engine pool
        self.engine = get_engine('wgdashboard_job')
        self.metadata = db.MetaData()
        self.peerJobTable = db.Table('PeerJobs', self.metadata,
                                     db.Column('JobID', db.String(255), nullable=False, primary_key=True),
                                     db.Column('Configuration', db.String(255), nullable=False),
                                     db.Column('Peer', db.String(255), nullable=False),
                                     db.Column('Field', db.String(255), nullable=False),
                                     db.Column('Operator', db.String(255), nullable=False),
                                     db.Column('Value', db.String(255), nullable=False),
                                     db.Column('CreationDate', (db.DATETIME if DashboardConfig.GetConfig("Database", "type")[1] == 'sqlite' else db.TIMESTAMP), nullable=False),
                                     db.Column('ExpireDate', (db.DATETIME if DashboardConfig.GetConfig("Database", "type")[1] == 'sqlite' else db.TIMESTAMP)),
                                     db.Column('Action', db.String(255), nullable=False),
                                     )
        self.metadata.create_all(self.engine)
        self.__getJobs()
        self.JobLogger: PeerJobLogger = PeerJobLogger(self, DashboardConfig)
        self.WireguardConfigurations = WireguardConfigurations
        self.AllPeerShareLinks = AllPeerShareLinks
        self.cleanJob(init=True)

    def __rebuild_index(self):
        """PERF-01: Build dictionary index for O(1) job lookups."""
        idx = {}
        id_idx = {}
        for j in self.Jobs:
            idx.setdefault((j.Configuration, j.Peer), []).append(j)
            id_idx[j.JobID] = j
        self._index = idx
        self._job_id_index = id_idx

    def __getJobs(self):
        with self.lock:
            with self.engine.connect() as conn:
                jobs = conn.execute(self.peerJobTable.select().where(
                    self.peerJobTable.columns.ExpireDate.is_(None)
                )).mappings().fetchall()
                new_jobs = []
                for job in jobs:
                    new_jobs.append(PeerJob(
                        job['JobID'], job['Configuration'], job['Peer'], job['Field'], job['Operator'], job['Value'],
                        job['CreationDate'], job['ExpireDate'], job['Action']))
                self.Jobs = new_jobs
                self.__rebuild_index()

    def getAllJobs(self, configuration: str = None, active_only: bool = False):
        if configuration is not None:
            with self.engine.connect() as conn:
                stmt = self.peerJobTable.select().where(
                    self.peerJobTable.columns.Configuration == configuration
                )
                if active_only:
                    stmt = stmt.where(self.peerJobTable.columns.ExpireDate.is_(None))
                jobs = conn.execute(stmt).mappings().fetchall()
                j = []
                for job in jobs:
                    j.append(PeerJob(
                        job['JobID'], job['Configuration'], job['Peer'], job['Field'], job['Operator'], job['Value'],
                        job['CreationDate'], job['ExpireDate'], job['Action']))
                return j
        return []

    def toJson(self):
        with self.lock:
            return [x.toJson() for x in self.Jobs]

    def searchJob(self, Configuration: str, Peer: str):
        """PERF-01: O(1) dictionary lookup instead of linear filtering."""
        with self.lock:
            if hasattr(self, '_index'):
                return list(self._index.get((Configuration, Peer), []))
            return list(filter(lambda x: x.Configuration == Configuration and x.Peer == Peer, self.Jobs))

    def searchJobById(self, JobID):
        """PERF-01: O(1) dictionary lookup by JobID."""
        with self.lock:
            if hasattr(self, '_job_id_index'):
                j = self._job_id_index.get(JobID)
                return [j] if j is not None else []
            return list(filter(lambda x: x.JobID == JobID, self.Jobs))

    def saveJob(self, Job: PeerJob) -> tuple[bool, list] | tuple[bool, str]:
        import traceback
        with self.lock:
            try:
                with self.engine.begin() as conn:
                    currentJob = self.searchJobById(Job.JobID)
                    if len(currentJob) == 0:
                        conn.execute(
                            self.peerJobTable.insert().values(
                                {
                                    "JobID": Job.JobID,
                                    "Configuration": Job.Configuration,
                                    "Peer": Job.Peer,
                                    "Field": Job.Field,
                                    "Operator": Job.Operator,
                                    "Value": Job.Value,
                                    "CreationDate": datetime.now(),
                                    "ExpireDate": None,
                                    "Action": Job.Action
                                }
                            )
                        )
                        self.JobLogger.log(Job.JobID, Message=f"Job is created if {Job.Field} {Job.Operator} {Job.Value} then {Job.Action}")
                    else:
                        conn.execute(
                            self.peerJobTable.update().values({
                                "Field": Job.Field,
                                "Operator": Job.Operator,
                                "Value": Job.Value,
                                "Action": Job.Action
                            }).where(self.peerJobTable.columns.JobID == Job.JobID)
                        )
                        self.JobLogger.log(Job.JobID, Message=f"Job is updated from if {currentJob[0].Field} {currentJob[0].Operator} {currentJob[0].Value} then {currentJob[0].Action}; to if {Job.Field} {Job.Operator} {Job.Value} then {Job.Action}")
                self.__getJobs()
                conf = self.WireguardConfigurations.get(Job.Configuration)
                if conf:
                    found_peer, peer_obj = conf.searchPeer(Job.Peer)
                    if found_peer and peer_obj:
                        peer_obj.getJobs()
                return True, list(
                    filter(lambda x: x.Configuration == Job.Configuration and x.Peer == Job.Peer and x.JobID == Job.JobID,
                           self.Jobs))
            except Exception as e:
                traceback.print_exc()
                return False, str(e)

    def deleteJob(self, Job: PeerJob) -> tuple[bool, None] | tuple[bool, str]:
        with self.lock:
            try:
                if len(self.searchJobById(Job.JobID)) == 0:
                    return False, "Job does not exist"
                with self.engine.begin() as conn:
                    conn.execute(
                        self.peerJobTable.update().values(
                            {
                                "ExpireDate": datetime.now()
                            }
                        ).where(self.peerJobTable.columns.JobID == Job.JobID)
                    )
                    self.JobLogger.log(Job.JobID, Message=f"Job is removed due to being deleted or finished.")
                self.__getJobs()
                conf = self.WireguardConfigurations.get(Job.Configuration)
                if conf:
                    found_peer, peer_obj = conf.searchPeer(Job.Peer)
                    if found_peer and peer_obj:
                        peer_obj.getJobs()
                return True, None
            except Exception as e:
                return False, str(e)

    def updateJobConfigurationName(self, ConfigurationName: str, NewConfigurationName: str) -> tuple[bool, str] | tuple[bool, None]:
        with self.lock:
            try:
                with self.engine.begin() as conn:
                    conn.execute(
                        self.peerJobTable.update().values({
                            "Configuration": NewConfigurationName
                        }).where(self.peerJobTable.columns.Configuration == ConfigurationName)
                    )
                self.__getJobs()
                return True, None
            except Exception as e:
                return False, str(e)
    
    def getPeerJobLogs(self, configurationName):
        return self.JobLogger.getLogs(configurationName)


    def runJob(self):
        with self.lock:
            self.cleanJob()
            self.__getJobs()
            current_jobs = list(self.Jobs)

        needToDelete = []
        # PERF-06: Group actions by configuration to batch execution and avoid repeated wg-quick save
        batch_restrict: dict[tuple[str, str], list[tuple[PeerJob, any]]] = {}
        batch_delete: dict[str, list[tuple[PeerJob, any]]] = {}
        individual_actions: list[tuple[PeerJob, any, str]] = []

        for job in current_jobs:
            try:
                c = self.WireguardConfigurations.get(job.Configuration)
                if c is not None:
                    f, fp = c.searchPeer(job.Peer)
                    if f:
                        if job.Field in ["total_receive", "total_sent", "total_data"]:
                            s = job.Field.split("_")[1]
                            x: float = float((getattr(fp, f"total_{s}", 0.0) or 0.0) + (getattr(fp, f"cumu_{s}", 0.0) or 0.0))
                            y: float = float(job.Value)
                        else:
                            y, is_utc = _parse_job_datetime(job.Value)
                            if is_utc:
                                x = datetime.now(timezone.utc)
                            else:
                                x = datetime.now()
                        runAction: bool = self.__runJob_Compare(x, y, job.Operator)
                        if runAction:
                            if job.Field in ["total_receive", "total_sent", "total_data"]:
                                reason = "Volume Limit Reached"
                            else:
                                reason = "Time Limit Reached"

                            if job.Action == "restrict":
                                batch_restrict.setdefault((job.Configuration, reason), []).append((job, fp))
                            elif job.Action == "delete":
                                batch_delete.setdefault(job.Configuration, []).append((job, fp))
                            else:
                                individual_actions.append((job, fp, reason))
                    else:
                        self.JobLogger.log(job.JobID, False,
                                      f"Somehow can't find this peer {job.Peer} from {c.Name} failed {job.Action}ed."
                                      )
                else:
                    self.JobLogger.log(job.JobID, False,
                                  f"Somehow can't find this peer {job.Peer} from {job.Configuration} failed {job.Action}ed."
                                  )
            except Exception as e:
                try:
                    current_app.logger.error(f"Job {job.JobID} evaluation failed: {e}")
                except Exception:
                    pass
                self.JobLogger.log(job.JobID, False, f"Job crashed during evaluation: {e}")

        # Execute batched restrictions (PERF-06: single wg set + single wg-quick save per interface)
        for (conf_name, reason), items in batch_restrict.items():
            c = self.WireguardConfigurations.get(conf_name)
            if not c:
                continue
            peer_ids = [fp.id for (job, fp) in items]
            try:
                with c.lock:
                    s, msg = c.restrictPeers(peer_ids, reason=reason)
                for (job, fp) in items:
                    if s is True:
                        self.JobLogger.log(job.JobID, s, f"Peer {fp.id} from {c.Name} is successfully restricted.")
                        needToDelete.append(job)
                    else:
                        self.JobLogger.log(job.JobID, False, f"Peer {fp.id} from {c.Name} failed restricted: {msg}")
            except TimeoutError as te:
                try:
                    current_app.logger.warning(f"Lock timeout during batch restrict on {conf_name}: {te}")
                except Exception:
                    pass
                for (job, fp) in items:
                    self.JobLogger.log(job.JobID, False, "Server busy (lock timeout); will retry next cycle.")
            except Exception as e:
                try:
                    current_app.logger.error(f"Batch restriction error on {conf_name}: {e}")
                except Exception:
                    pass
                for (job, fp) in items:
                    self.JobLogger.log(job.JobID, False, f"Job crashed during batch restriction: {e}")

        # Execute batched deletions (PERF-06: single wg set + single wg-quick save per interface)
        for conf_name, items in batch_delete.items():
            c = self.WireguardConfigurations.get(conf_name)
            if not c:
                continue
            peer_ids = [fp.id for (job, fp) in items]
            try:
                with c.lock:
                    s, msg = c.deletePeers(peer_ids, self, self.AllPeerShareLinks)
                for (job, fp) in items:
                    if s is True:
                        self.JobLogger.log(job.JobID, s, f"Peer {fp.id} from {c.Name} is successfully deleted.")
                        needToDelete.append(job)
                    else:
                        self.JobLogger.log(job.JobID, False, f"Peer {fp.id} from {c.Name} failed deleted: {msg}")
            except TimeoutError as te:
                try:
                    current_app.logger.warning(f"Lock timeout during batch delete on {conf_name}: {te}")
                except Exception:
                    pass
                for (job, fp) in items:
                    self.JobLogger.log(job.JobID, False, "Server busy (lock timeout); will retry next cycle.")
            except Exception as e:
                try:
                    current_app.logger.error(f"Batch deletion error on {conf_name}: {e}")
                except Exception:
                    pass
                for (job, fp) in items:
                    self.JobLogger.log(job.JobID, False, f"Job crashed during batch deletion: {e}")

        # Execute individual actions (e.g. reset_total_data_usage)
        for (job, fp, reason) in individual_actions:
            c = self.WireguardConfigurations.get(job.Configuration)
            if not c:
                continue
            try:
                s = False
                with c.lock:
                    if job.Action == "reset_total_data_usage":
                        s = fp.resetDataUsage("total")
                        c.restrictPeers([fp.id], reason="Resetting data usage")
                        c.allowAccessPeers([fp.id])
                if s is True:
                    self.JobLogger.log(job.JobID, s, f"Peer {fp.id} from {c.Name} is successfully {job.Action}ed.")
                    needToDelete.append(job)
                else:
                    self.JobLogger.log(job.JobID, s, f"Peer {fp.id} from {c.Name} failed {job.Action}ed.")
            except TimeoutError as te:
                try:
                    current_app.logger.warning(f"Lock timeout during job {job.JobID}: {te}")
                except Exception:
                    pass
                self.JobLogger.log(job.JobID, False, "Server busy (lock timeout); will retry next cycle.")
            except Exception as e:
                try:
                    current_app.logger.error(f"Job {job.JobID} execution failed: {e}")
                except Exception:
                    pass
                self.JobLogger.log(job.JobID, False, f"Job crashed: {e}")

        for j in needToDelete:
            self.deleteJob(j)
            
    def cleanJob(self, init = False):
        # During __init__ the WireGuard configurations are not loaded yet, so no job can be
        # proven stale here; runJob() calls cleanJob() again once they are.
        if init:
            return
        failingJobs = self.JobLogger.getFailingJobs()
        with self.engine.begin() as conn:
            for job in failingJobs:
                job_id = job.get('JobID') if hasattr(job, 'get') else str(job)
                job_detail = conn.execute(
                    self.peerJobTable.select().where(self.peerJobTable.columns.JobID == job_id)
                ).mappings().fetchone()
                if not job_detail:
                    continue

                conf_name = job_detail.get('Configuration')
                peer_id = job_detail.get('Peer')
                action = job_detail.get('Action')

                conf = self.WireguardConfigurations.get(conf_name)
                if conf is None:
                    # An interface that is not loaded (e.g. right after a restore or a server
                    # migration) is not evidence that the peer was deleted; keep the job.
                    continue
                f, _ = conf.searchPeer(peer_id)
                peer_exists = bool(f)

                # BUG-05: Never auto-delete active restriction jobs for existing peers
                if action == "restrict" and peer_exists:
                    try:
                        current_app.logger.error(f"Job {job_id} for peer {peer_id} failed repeatedly (>10 times) but was NOT deleted because it is an active restriction job.")
                    except Exception:
                        pass
                    continue

                conn.execute(
                    self.peerJobTable.update().values(
                        {
                            "ExpireDate": datetime.now()
                        }
                    ).where(self.peerJobTable.columns.JobID == job_id)
                )
                self.JobLogger.deleteLogs(JobID=job_id)
                self.JobLogger.log(job_id, Message=f"Job is removed due to being stale.")
        if len(failingJobs) > 0:
            self.__getJobs()
        


    def __runJob_Compare(self, x: float | datetime, y: float | datetime, operator: str):
        if operator in ("eq", "==", "str_eq"):
            return x == y
        if operator in ("neq", "!="):
            return x != y
        if operator in ("lgt", ">"):
            return x > y
        if operator in ("lst", "<"):
            return x < y
        if operator == ">=":
            return x >= y
        if operator == "<=":
            return x <= y
        return False