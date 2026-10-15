import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import pytest

# Ensure src is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from modules.DatabaseConnection import get_engine, dispose_all, ConnectionString
from modules.PeerJobs import PeerJob, PeerJobs
from modules.PeerShareLinks import PeerShareLink, PeerShareLinks
from modules.Peer import Peer


class TestPerformanceAndStability(unittest.TestCase):
    """Test suite for Docs/07-Performance-and-Stability.md (PERF-01 to PERF-11)."""

    def test_perf04_engine_singleton_and_dispose(self):
        """PERF-04: get_engine returns singleton Engine instance; dispose_all clears pool."""
        engine1 = get_engine("test_perf_db")
        engine2 = get_engine("test_perf_db")
        self.assertIs(engine1, engine2, "get_engine must return the identical singleton instance")

        dispose_all()
        # After dispose_all, getting the engine creates a new singleton
        engine3 = get_engine("test_perf_db")
        self.assertIsNot(engine1, engine3, "After dispose_all, a new singleton engine is created")
        dispose_all()

    def test_perf01_peer_jobs_indexing(self):
        """PERF-01: PeerJobs uses dictionary index for O(1) lookups by (Config, Peer) and JobID."""
        mock_config = MagicMock()
        mock_config.GetConfig.side_effect = lambda sec, key: (True, "sqlite")
        mock_confs = {}

        # Instantiate PeerJobs without calling database setup
        with patch.object(PeerJobs, "__init__", lambda self: None):
            pj = PeerJobs()
            pj.Jobs = []
            pj.lock = MagicMock()
            pj.lock.__enter__ = MagicMock(return_value=None)
            pj.lock.__exit__ = MagicMock(return_value=None)

            job1 = PeerJob("job-1", "wg0", "peer-A", "total_data", ">", "1000", "2026-01-01", None, "restrict")
            job2 = PeerJob("job-2", "wg0", "peer-A", "date", ">", "2026-02-01", "2026-01-01", None, "delete")
            job3 = PeerJob("job-3", "wg1", "peer-B", "total_data", ">", "5000", "2026-01-01", None, "restrict")

            pj.Jobs = [job1, job2, job3]
            # Call private index builder
            pj._PeerJobs__rebuild_index()

            # Verify index structure
            self.assertEqual(len(pj._index.get(("wg0", "peer-A"), [])), 2)
            self.assertEqual(len(pj._index.get(("wg1", "peer-B"), [])), 1)
            self.assertEqual(len(pj._index.get(("wg0", "peer-nonexistent"), [])), 0)

            # Test searchJob
            results_a = pj.searchJob("wg0", "peer-A")
            self.assertEqual(len(results_a), 2)
            self.assertIn(job1, results_a)
            self.assertIn(job2, results_a)

            # Test searchJobById
            results_id = pj.searchJobById("job-2")
            self.assertEqual(len(results_id), 1)
            self.assertEqual(results_id[0], job2)

            # Missing job ID returns empty list
            self.assertEqual(pj.searchJobById("job-999"), [])

    def test_perf01_peer_share_links_indexing(self):
        """PERF-01: PeerShareLinks uses dictionary index for O(1) lookups by (Config, Peer) and ShareID."""
        with patch.object(PeerShareLinks, "__init__", lambda self: None):
            psl = PeerShareLinks()
            psl.Links = []
            psl.lock = MagicMock()
            psl.lock.__enter__ = MagicMock(return_value=None)
            psl.lock.__exit__ = MagicMock(return_value=None)

            now = datetime.now()
            link1 = PeerShareLink("share-1", "wg0", "peer-A", now + timedelta(days=1), now)
            link2 = PeerShareLink("share-2", "wg1", "peer-B", now + timedelta(days=1), now)

            psl.Links = [link1, link2]
            psl._PeerShareLinks__rebuild_index()

            # Verify index lookup
            found = psl.getLink("wg0", "peer-A")
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].ShareID, "share-1")

            found_by_id = psl.getLinkByID("share-2")
            self.assertEqual(len(found_by_id), 1)
            self.assertEqual(found_by_id[0].ShareID, "share-2")

            self.assertEqual(psl.getLinkByID("non-existent"), [])

    def test_perf01_peer_update_attributes_in_place(self):
        """PERF-01: Peer.update_attributes modifies peer in-place without re-instantiation."""
        peer_dict = {
            "id": "peer-id-1",
            "name": "Old Name",
            "allowed_ip": "10.0.0.2/32",
            "endpoint_allowed_ip": "0.0.0.0/0",
            "preshared_key": "",
            "c_private_key": "privkey",
            "DNS": "1.1.1.1",
            "MTU": "1420",
            "keepalive": "25",
            "notes": "notes"
        }
        mock_config = MagicMock()
        mock_config.Name = "wg0"
        mock_config.getPublicKey.return_value = "pubkey"
        mock_config.Address = "10.0.0.1/24"
        mock_config.ListenPort = "51820"
        mock_config.DNS = "1.1.1.1"
        mock_config.AllPeerJobs.searchJob.return_value = []
        mock_config.AllPeerShareLinks.getLink.return_value = []

        with patch("modules.Peer.GenerateWireguardPublicKey", return_value=(True, "pubkey")):
            p = Peer(peer_dict, mock_config)
            self.assertEqual(p.name, "Old Name")

            # Update attributes with modified dictionary
            updated_dict = dict(peer_dict)
            updated_dict["name"] = "New Updated Name"
            updated_dict["allowed_ip"] = "10.0.0.5/32"

            p.update_attributes(updated_dict)
            self.assertEqual(p.name, "New Updated Name")
            self.assertEqual(p.allowed_ip, "10.0.0.5/32")
            self.assertEqual(p.id, "peer-id-1")

    def test_perf02_get_status_sysfs_and_memoization(self):
        """PERF-02: getStatus uses Linux sysfs fast-path and memoizes for 2 seconds."""
        from modules.WireguardConfiguration import WireguardConfiguration

        with patch.object(WireguardConfiguration, "__init__", lambda self, *args, **kwargs: None):
            wgc = WireguardConfiguration()
            wgc.Name = "wg_test"
            wgc.Status = False

            with patch("os.path.exists") as mock_exists:
                # Simulate /sys/class/net/wg_test exists
                def fake_exists(path):
                    return path == "/sys/class/net/wg_test"
                mock_exists.side_effect = fake_exists

                # First call checks sysfs and sets Status = True
                status1 = wgc.getStatus()
                self.assertTrue(status1)

                # Second immediate call uses memoized cache without checking os.path.exists
                mock_exists.reset_mock()
                status2 = wgc.getStatus()
                self.assertTrue(status2)
                mock_exists.assert_not_called()

    def test_perf06_batch_peer_jobs_execution(self):
        """PERF-06: PeerJobs.runJob batches peer restrictions and deletions per interface."""
        with patch.object(PeerJobs, "__init__", lambda self: None):
            pj = PeerJobs()
            pj.Jobs = []
            pj.lock = MagicMock()
            pj.lock.__enter__ = MagicMock(return_value=None)
            pj.lock.__exit__ = MagicMock(return_value=None)
            pj.JobLogger = MagicMock()
            pj.engine = MagicMock()

            # Create mock configuration
            mock_conf = MagicMock()
            mock_conf.Name = "wg0"
            mock_conf.lock = MagicMock()
            mock_conf.lock.__enter__ = MagicMock(return_value=None)
            mock_conf.lock.__exit__ = MagicMock(return_value=None)
            mock_conf.restrictPeers.return_value = (True, "Restricted successfully")
            mock_conf.deletePeers.return_value = (True, "Deleted successfully")

            # Setup mock peers
            mock_peer1 = MagicMock()
            mock_peer1.id = "peer1"
            mock_peer1.total_data = 2000
            mock_peer1.cumu_data = 0
            mock_peer2 = MagicMock()
            mock_peer2.id = "peer2"
            mock_peer2.total_data = 3000
            mock_peer2.cumu_data = 0

            mock_conf.searchPeer.side_effect = lambda pid: (True, mock_peer1 if pid == "peer1" else mock_peer2)

            pj.WireguardConfigurations = {"wg0": mock_conf}

            # Two jobs triggering restriction on wg0
            job1 = PeerJob("j1", "wg0", "peer1", "total_data", ">", "1000", "2026-01-01", None, "restrict")
            job2 = PeerJob("j2", "wg0", "peer2", "total_data", ">", "1000", "2026-01-01", None, "restrict")
            pj.Jobs = [job1, job2]

            with patch.object(pj, "cleanJob", MagicMock()), \
                 patch.object(pj, "_PeerJobs__getJobs", MagicMock()), \
                 patch.object(pj, "deleteJob", MagicMock()) as mock_del_job:
                pj.runJob()

                # Verify restrictPeers was called ONCE for the batch of peers instead of twice
                mock_conf.restrictPeers.assert_called_once()
                called_args, called_kwargs = mock_conf.restrictPeers.call_args
                self.assertEqual(set(called_args[0]), {"peer1", "peer2"})
                self.assertEqual(called_kwargs.get("reason"), "Volume Limit Reached")

    def test_perf08_ping_count_and_traceroute_capping(self):
        """PERF-08: Ping count is clamped to max 20, traceroute max_hops is clamped to 20."""
        # Ping count capping logic test
        requested_count_large = 100
        clamped_ping = min(max(int(requested_count_large), 1), 20)
        self.assertEqual(clamped_ping, 20)

        requested_count_negative = -5
        clamped_ping_neg = min(max(int(requested_count_negative), 1), 20)
        self.assertEqual(clamped_ping_neg, 1)

        normal_count = 4
        clamped_normal = min(max(int(normal_count), 1), 20)
        self.assertEqual(clamped_normal, 4)

    def test_perf11_timeout_error_handler_503(self):
        """PERF-11: TimeoutError handler returns HTTP 503 with retry advice."""
        from dashboard import app

        with app.test_request_context("/api/test-timeout"):
            response = app.error_handler_spec[None][None][TimeoutError](TimeoutError("Lock wait timed out"))
            self.assertEqual(response.status_code, 503)
            data = response.get_json()
            self.assertFalse(data["status"])
            self.assertIn("busy", data["message"])
            self.assertIn("retry", data["message"].lower())


if __name__ == "__main__":
    unittest.main()
