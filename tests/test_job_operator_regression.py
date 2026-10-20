import sys
import os
import threading
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from modules.DashboardAPIKey import DashboardAPIKey
from modules.PeerJobs import PeerJobs, PeerJob
from modules.Utilities import GenerateWireguardPrivateKey, GenerateWireguardPublicKey
import sqlalchemy as db


@pytest.fixture
def api_auth(monkeypatch):
    from dashboard import DashboardConfig
    test_key = DashboardAPIKey(
        Key="test_job_operator_key",
        CreatedAt="2026-01-01",
        ExpiredAt="None",
        AllowedConfigurations="*",
        Scope="admin"
    )
    monkeypatch.setattr(DashboardConfig, "DashboardAPIKeys", [test_key])

    orig_get_config = DashboardConfig.GetConfig
    def mock_get_config(section, key):
        if section == "Server" and key == "dashboard_api_key":
            return True, True
        return orig_get_config(section, key)
    monkeypatch.setattr(DashboardConfig, "GetConfig", mock_get_config)
    return {"wg-dashboard-apikey": "test_job_operator_key"}


@pytest.fixture
def wg0_with_peer():
    from dashboard import WireguardConfigurations
    _, pub_key = GenerateWireguardPublicKey(GenerateWireguardPrivateKey()[1])
    mock_wgc = MagicMock()
    mock_wgc.Name = "wg0"
    mock_wgc.searchPeer.return_value = (True, MagicMock(id=pub_key))
    mock_wgc.RestrictedPeers = []
    with patch.dict(WireguardConfigurations, {"wg0": mock_wgc}):
        yield pub_key


def _bot_job(pub_key, field, value, operator="lgt"):
    # Exact payload sent by setjob() in the OxBot/Mirza WGDashboard.php
    return {"Job": {
        "JobID": "8c0d9a1e-1111-4222-8333-944455556666",
        "Configuration": "wg0",
        "Peer": pub_key,
        "Field": field,
        "Operator": operator,
        "Value": value,
        "CreationDate": "",
        "ExpireDate": None,
        "Action": "restrict",
    }}


class TestSavePeerScheduleJobOperators:
    @pytest.mark.parametrize("field,value", [
        ("total_data", "10"),
        ("total_data", "20.5"),
        ("date", "2026-11-13 20:30:00"),
    ])
    def test_bot_payload_with_lgt_is_accepted(self, api_auth, wg0_with_peer, field, value):
        from dashboard import app, AllPeerJobs
        with patch.object(AllPeerJobs, "saveJob", return_value=(True, [])) as save:
            res = app.test_client().post("/api/savePeerScheduleJob",
                                         json=_bot_job(wg0_with_peer, field, value), headers=api_auth)
        assert res.status_code == 200, res.get_json()
        saved: PeerJob = save.call_args[0][0]
        assert saved.Operator == "lgt"
        assert saved.Field == field
        assert saved.Value == value

    @pytest.mark.parametrize("sent,stored", [(">", "lgt"), ("<", "lst"), ("==", "eq"), ("!=", "neq"),
                                             ("lst", "lst"), (">=", ">="), ("<=", "<=")])
    def test_operator_aliases_are_normalized(self, api_auth, wg0_with_peer, sent, stored):
        from dashboard import app, AllPeerJobs
        with patch.object(AllPeerJobs, "saveJob", return_value=(True, [])) as save:
            res = app.test_client().post("/api/savePeerScheduleJob",
                                         json=_bot_job(wg0_with_peer, "total_data", "10", sent), headers=api_auth)
        assert res.status_code == 200, res.get_json()
        assert save.call_args[0][0].Operator == stored

    def test_failed_save_returns_error_status(self, api_auth, wg0_with_peer):
        # The bots treat HTTP 200 as "limit stored"; a failed save must not look like success
        from dashboard import app, AllPeerJobs
        with patch.object(AllPeerJobs, "saveJob", return_value=(False, "database is locked")):
            res = app.test_client().post("/api/savePeerScheduleJob",
                                         json=_bot_job(wg0_with_peer, "total_data", "10"), headers=api_auth)
        assert res.status_code == 500
        assert res.get_json()["status"] is False

    @pytest.mark.parametrize("value", ["2026-11-19T20:30:00+03:30", "2026-11-19T17:00:00+00:00", "1763571000"])
    def test_bot_date_with_offset_is_accepted(self, api_auth, wg0_with_peer, value):
        from dashboard import app, AllPeerJobs
        with patch.object(AllPeerJobs, "saveJob", return_value=(True, [])) as save:
            res = app.test_client().post("/api/savePeerScheduleJob",
                                         json=_bot_job(wg0_with_peer, "date", value), headers=api_auth)
        assert res.status_code == 200, res.get_json()
        assert save.call_args[0][0].Value == value

    @pytest.mark.parametrize("operator", ["foo", "", None, "gt"])
    def test_unknown_operator_is_rejected(self, api_auth, wg0_with_peer, operator):
        from dashboard import app, AllPeerJobs
        with patch.object(AllPeerJobs, "saveJob", return_value=(True, [])) as save:
            res = app.test_client().post("/api/savePeerScheduleJob",
                                         json=_bot_job(wg0_with_peer, "total_data", "10", operator), headers=api_auth)
        assert res.status_code == 400
        assert not save.called


class TestConfigurationInfoServesLiveJobs:
    def test_jobs_come_from_job_index_not_stale_peer_object(self, api_auth):
        # A bot renewal reads the jobs right after a purchase saved them; a cached Peer object must not hide them
        from dashboard import app, AllPeerJobs, WireguardConfigurations
        from modules.Peer import Peer
        from datetime import datetime
        _, pub = GenerateWireguardPublicKey(GenerateWireguardPrivateKey()[1])
        conf = MagicMock()
        conf.Name = "wg0"
        conf.toJson.return_value = {"Name": "wg0"}
        stale = Peer.__new__(Peer)
        stale.__dict__.update({"id": pub, "name": "u1", "jobs": [], "restricted": False})
        conf.Peers = [stale]
        conf.getRestrictedPeersList.return_value = []
        job = PeerJob("j1", "wg0", pub, "date", "lgt", "2099-01-01T00:00:00+03:30", datetime.now(), None, "restrict")
        with patch.dict(WireguardConfigurations, {"wg0": conf}), \
             patch.object(AllPeerJobs, "searchJob", return_value=[job]):
            res = app.test_client().get("/api/getWireguardConfigurationInfo?configurationName=wg0", headers=api_auth)
        jobs = res.get_json()["data"]["configurationPeers"][0]["jobs"]
        assert [j["JobID"] for j in jobs] == ["j1"]


class TestApiResponsesAreNeverCached:
    @pytest.mark.parametrize("path", ["/api/getWireguardConfigurationInfo?configurationName=wg0", "/api/getDashboardVersion"])
    def test_api_responses_forbid_proxy_caching(self, api_auth, path):
        from dashboard import app
        res = app.test_client().get(path, headers=api_auth)
        assert res.headers.get("X-Accel-Expires") == "0"
        assert "no-store" in res.headers.get("Cache-Control", "")

    def test_unauthenticated_response_also_not_cacheable(self):
        from dashboard import app
        res = app.test_client().get("/api/getWireguardConfigurationInfo?configurationName=wg0")
        assert res.headers.get("X-Accel-Expires") == "0"


class TestJobDateTimezones:
    def test_offset_dates_compare_in_utc(self):
        from modules.PeerJobs import _parse_job_datetime
        from datetime import timezone
        tehran, aware1 = _parse_job_datetime("2026-11-19T20:30:00+03:30")
        utc, aware2 = _parse_job_datetime("2026-11-19T17:00:00+00:00")
        assert aware1 and aware2
        assert tehran == utc
        assert tehran.tzinfo == timezone.utc

    def test_v2_add_days_extends_from_existing_expiry_and_keeps_offset(self, api_auth, wg0_with_peer):
        from dashboard import app, AllPeerJobs
        from datetime import datetime, timezone, timedelta
        existing = PeerJob("old-date", "wg0", wg0_with_peer, "date", "lgt",
                           "2030-01-01T00:00:00+00:00", datetime.now(), None, "restrict")
        with patch.object(AllPeerJobs, "searchJob", return_value=[existing]), \
             patch.object(AllPeerJobs, "saveJob", return_value=(True, [])) as save, \
             patch.object(AllPeerJobs, "deleteJob", return_value=(True, None)) as delete:
            res = app.test_client().post("/api/v2/peer/limits/add", headers=api_auth,
                                         json={"configurationName": "wg0", "id": wg0_with_peer, "add_days": 10})
        assert res.status_code == 200, res.get_json()
        saved = save.call_args[0][0]
        assert datetime.fromisoformat(saved.Value) == datetime(2030, 1, 11, tzinfo=timezone.utc)
        assert delete.called

    def test_v2_add_days_keeps_old_job_when_save_fails(self, api_auth, wg0_with_peer):
        from dashboard import app, AllPeerJobs
        from datetime import datetime
        existing = PeerJob("old-date", "wg0", wg0_with_peer, "date", "lgt",
                           "2030-01-01 00:00:00", datetime.now(), None, "restrict")
        with patch.object(AllPeerJobs, "searchJob", return_value=[existing]), \
             patch.object(AllPeerJobs, "saveJob", return_value=(False, "locked")), \
             patch.object(AllPeerJobs, "deleteJob", return_value=(True, None)) as delete:
            app.test_client().post("/api/v2/peer/limits/add", headers=api_auth,
                                   json={"configurationName": "wg0", "id": wg0_with_peer, "add_days": 10})
        assert not delete.called


def _peer_jobs_with_failing_restrict_job(configurations):
    pj = PeerJobs.__new__(PeerJobs)
    pj.lock = threading.RLock()
    metadata = db.MetaData()
    table = db.Table('PeerJobs', metadata,
        db.Column('JobID', db.String(255), primary_key=True),
        db.Column('Configuration', db.String(255)),
        db.Column('Peer', db.String(255)),
        db.Column('Field', db.String(255)),
        db.Column('Operator', db.String(255)),
        db.Column('Value', db.String(255)),
        db.Column('CreationDate', db.DATETIME),
        db.Column('ExpireDate', db.DATETIME),
        db.Column('Action', db.String(255))
    )
    engine = db.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(table.insert().values(
            JobID="job_vol", Configuration="wg0", Peer="peer1", Field="total_data", Operator="lgt",
            Value="10", CreationDate=datetime.now(), ExpireDate=None, Action="restrict"))
    pj.engine = engine
    pj.peerJobTable = table
    pj.JobLogger = MagicMock()
    pj.JobLogger.getFailingJobs.return_value = [{"JobID": "job_vol"}]
    pj.WireguardConfigurations = configurations
    pj.Jobs = []
    pj._index = {}
    pj._job_id_index = {}
    return pj, engine, table


def _expire_date(engine, table):
    with engine.connect() as conn:
        return conn.execute(table.select().where(table.c.JobID == "job_vol")).mappings().fetchone()["ExpireDate"]


class TestCleanJobDoesNotDropLimitsWhenInterfaceIsMissing:
    def test_init_clean_with_no_configurations_loaded_keeps_job(self):
        # PeerJobs is constructed before InitWireguardConfigurationsList() runs
        pj, engine, table = _peer_jobs_with_failing_restrict_job({})
        from flask import Flask
        with Flask("t").app_context():
            pj.cleanJob(init=True)
        assert _expire_date(engine, table) is None
        assert not pj.JobLogger.getFailingJobs.called

    def test_runtime_clean_with_interface_not_loaded_keeps_job(self):
        pj, engine, table = _peer_jobs_with_failing_restrict_job({})
        from flask import Flask
        with Flask("t").app_context():
            pj.cleanJob()
        assert _expire_date(engine, table) is None

    def test_runtime_clean_with_deleted_peer_still_expires_job(self):
        conf = MagicMock()
        conf.searchPeer.return_value = (False, None)
        pj, engine, table = _peer_jobs_with_failing_restrict_job({"wg0": conf})
        pj._PeerJobs__getJobs = MagicMock()
        from flask import Flask
        with Flask("t").app_context():
            pj.cleanJob()
        assert _expire_date(engine, table) is not None
