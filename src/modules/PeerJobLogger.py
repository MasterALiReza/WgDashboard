"""
Peer Job Logger
"""
import uuid
from typing import Sequence

import sqlalchemy as db
from flask import current_app
from sqlalchemy import RowMapping

from .DatabaseConnection import ConnectionString
from .Log import Log

class PeerJobLogger:
    def __init__(self, AllPeerJobs, DashboardConfig):
        self.engine = db.create_engine(ConnectionString("wgdashboard_log"))                
        self.metadata = db.MetaData()
        self.jobLogTable = db.Table('JobLog', self.metadata,
                                    db.Column('LogID', db.String(255), nullable=False, primary_key=True),
                                    db.Column('JobID', db.String(255), nullable=False, index=True),
                                    db.Column('LogDate', (db.DATETIME if DashboardConfig.GetConfig("Database", "type")[1] == 'sqlite' else db.TIMESTAMP), 
                                              server_default=db.func.now(), index=True),
                                    db.Column('Status', db.String(255), nullable=False, index=True),
                                    db.Column('Message', db.Text),
                                    extend_existing=True
                                    )
        self.logs: list[Log] = []
        self.metadata.create_all(self.engine)
        try:
            with self.engine.begin() as conn:
                conn.execute(db.text("CREATE INDEX IF NOT EXISTS idx_joblog_jobid_status ON JobLog (JobID, Status);"))
                conn.execute(db.text("CREATE INDEX IF NOT EXISTS idx_joblog_logdate ON JobLog (LogDate);"))
        except Exception:
            pass
        self.AllPeerJobs = AllPeerJobs
    def log(self, JobID: str, Status: bool = True, Message: str = "") -> bool:
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    self.jobLogTable.insert().values(
                        {
                            "LogID": str(uuid.uuid4()), 
                            "JobID": JobID, 
                            "Status": Status, 
                            "Message": Message
                        }
                    )
                )
        except Exception as e:
            current_app.logger.error(f"Peer Job Log Error: {e}")
            return False
        return True

    def getLogs(self, configName = None) -> list[Log]:
        logs: list[Log] = []
        try:
            allJobs = self.AllPeerJobs.getAllJobs(configName, active_only=True)
            if not allJobs:
                allJobs = self.AllPeerJobs.getAllJobs(configName)
            allJobsID = [x.JobID for x in allJobs]
            if not allJobsID:
                return logs

            # Limit the IN clause to avoid SQLite expression limits and excessive memory
            allJobsID = allJobsID[:500]
            stmt = self.jobLogTable.select().where(self.jobLogTable.columns.JobID.in_(
                allJobsID
            )).order_by(self.jobLogTable.columns.LogDate.desc()).limit(500)
            with self.engine.connect() as conn:
                table = conn.execute(stmt).fetchall()
                for l in table:
                    log_date_str = l.LogDate.strftime("%Y-%m-%d %H:%M:%S") if hasattr(l.LogDate, 'strftime') else str(l.LogDate)
                    logs.append(
                        Log(l.LogID, l.JobID, log_date_str, l.Status, l.Message))
        except Exception as e:
            current_app.logger.error(f"Getting Peer Job Log Error: {e}")
            return logs
        return logs
    
    def getFailingJobs(self) -> Sequence[RowMapping]:
        with self.engine.connect() as conn:
            table = conn.execute(
                db.select(
                    self.jobLogTable.c.JobID
                ).where(
                    (db.or_(
                        self.jobLogTable.c.Status == 'false',
                        self.jobLogTable.c.Status == 0
                    ) if conn.dialect.name == 'sqlite' else self.jobLogTable.c.Status == 'false')
                ).group_by(
                    self.jobLogTable.c.JobID
                ).having(
                    db.func.count(
                        self.jobLogTable.c.JobID
                    ) > 10
                )
            ).mappings().fetchall()
            return table
    
    def deleteLogs(self, LogID = None, JobID = None):
        with self.engine.begin() as conn:
            print(f"[WGDashboard] Deleted stale logs of JobID: {JobID}")
            conn.execute(
                self.jobLogTable.delete().where(
                    db.and_(
                        (self.jobLogTable.c.LogID == LogID if LogID is not None else True),
                        (self.jobLogTable.c.JobID == JobID if JobID is not None else True),
                    )
                )
            )
    
    def vacuum(self):
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            if conn.dialect.name == 'sqlite':
                print("[WGDashboard] SQLite Vacuuming PeerJobLogs Database")
                conn.execute(db.text('VACUUM;'))