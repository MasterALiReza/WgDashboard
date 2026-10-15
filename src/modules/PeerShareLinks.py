import threading
from .DatabaseConnection import ConnectionString, get_engine
from .PeerShareLink import PeerShareLink
import sqlalchemy as db
from datetime import datetime
import uuid

"""
Peer Share Links
"""
class PeerShareLinks:
    def __init__(self, DashboardConfig, WireguardConfigurations):
        self.lock = threading.Lock()
        self.Links: list[PeerShareLink] = []
        self._index: dict[tuple[str, str], list[PeerShareLink]] = {}
        self._share_id_index: dict[str, PeerShareLink] = {}
        # PERF-04: Shared engine pool
        self.engine = get_engine("wgdashboard")
        self.metadata = db.MetaData()
        self.peerShareLinksTable = db.Table(
            'PeerShareLinks', self.metadata,
            db.Column('ShareID', db.String(255), nullable=False, primary_key=True),
            db.Column('Configuration', db.String(255), nullable=False),
            db.Column('Peer', db.String(255), nullable=False),
            db.Column('ExpireDate', (db.DATETIME if DashboardConfig.GetConfig("Database", "type")[1] == 'sqlite' else db.TIMESTAMP)),
            db.Column('SharedDate', (db.DATETIME if DashboardConfig.GetConfig("Database", "type")[1] == 'sqlite' else db.TIMESTAMP),
                      server_default=db.func.now()),
        )
        self.metadata.create_all(self.engine)
        self.__getSharedLinks()
        self.wireguardConfigurations = WireguardConfigurations

    def __rebuild_index(self):
        """PERF-01: Build dictionary index for O(1) link lookups."""
        idx = {}
        id_idx = {}
        for link in self.Links:
            idx.setdefault((link.Configuration, link.Peer), []).append(link)
            id_idx[link.ShareID] = link
        self._index = idx
        self._share_id_index = id_idx

    def __getSharedLinks(self):
        with self.lock:
            self.Links.clear()
            with self.engine.connect() as conn:
                allLinks = conn.execute(
                    self.peerShareLinksTable.select().where(
                        db.or_(self.peerShareLinksTable.columns.ExpireDate.is_(None), self.peerShareLinksTable.columns.ExpireDate > datetime.now())
                    )
                ).mappings().fetchall()
                for link in allLinks:
                    self.Links.append(PeerShareLink(**link))
            self.__rebuild_index()

    def getLink(self, Configuration: str, Peer: str) -> list[PeerShareLink]:
        """PERF-01: O(1) dictionary lookup instead of linear filtering."""
        with self.lock:
            now = datetime.now()
            if hasattr(self, '_index'):
                links = self._index.get((Configuration, Peer), [])
                return [x for x in links if x.ExpireDate is None or x.ExpireDate > now]
            return list(filter(lambda x : x.Configuration == Configuration and x.Peer == Peer and (x.ExpireDate is None or x.ExpireDate > now), self.Links))

    def getLinkByID(self, ShareID: str) -> list[PeerShareLink]:
        """PERF-01: O(1) dictionary lookup by ShareID."""
        with self.lock:
            now = datetime.now()
            if hasattr(self, '_share_id_index'):
                link = self._share_id_index.get(ShareID)
                if link and (link.ExpireDate is None or link.ExpireDate > now):
                    return [link]
                return []
            return list(filter(lambda x : x.ShareID == ShareID and (x.ExpireDate is None or x.ExpireDate > now), self.Links))

    def addLink(self, Configuration: str, Peer: str, ExpireDate: datetime = None) -> tuple[bool, str]:
        try:
            newShareID = str(uuid.uuid4())
            with self.engine.begin() as conn:
                if len(self.getLink(Configuration, Peer)) > 0:
                    conn.execute(
                        self.peerShareLinksTable.update().values(
                            {
                                "ExpireDate": datetime.now()
                            }
                        ).where(db.and_(self.peerShareLinksTable.columns.Configuration == Configuration, self.peerShareLinksTable.columns.Peer == Peer))
                    )

                conn.execute(
                    self.peerShareLinksTable.insert().values(
                        {
                            "ShareID": newShareID,
                            "Configuration": Configuration,
                            "Peer": Peer,
                            "ExpireDate": ExpireDate
                        }
                    )
                )
            self.__getSharedLinks()
            conf = self.wireguardConfigurations.get(Configuration)
            if conf:
                found_peer, peer_obj = conf.searchPeer(Peer)
                if found_peer and peer_obj:
                    peer_obj.getShareLink()
        except Exception as e:
            return False, str(e)
        return True, newShareID

    def updateLinkExpireDate(self, ShareID, ExpireDate: datetime = None) -> tuple[bool, str]:
        try:
            with self.engine.connect() as conn:
                existing = conn.execute(
                    self.peerShareLinksTable.select().where(self.peerShareLinksTable.columns.ShareID == ShareID)
                ).mappings().fetchone()
            if not existing:
                return False, "Share link does not exist"
            with self.engine.begin() as conn:
                conn.execute(
                    self.peerShareLinksTable.update().values(
                        {
                            "ExpireDate": ExpireDate
                        }
                    ).where(self.peerShareLinksTable.columns.ShareID == ShareID)
                )
            self.__getSharedLinks()
            conf = self.wireguardConfigurations.get(existing['Configuration'])
            if conf:
                found_peer, peer_obj = conf.searchPeer(existing['Peer'])
                if found_peer and peer_obj:
                    peer_obj.getShareLink()
            return True, ""
        except Exception as e:
            return False, str(e)