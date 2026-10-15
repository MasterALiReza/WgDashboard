import sys
import os
import time
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
import sqlalchemy as db

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from modules.Peer import Peer
from modules.WireguardConfiguration import WireguardConfiguration


def create_mock_configuration(tmp_path, name="wg0"):
    mock_config = MagicMock()
    mock_config.Name = name
    mock_config.Protocol = "wg"
    mock_config.getStatus.return_value = False
    mock_config.getPeersList.return_value = []
    mock_config.engine = db.create_engine("sqlite:///:memory:")
    mock_config.metadata = db.MetaData()
    mock_config._last_data_usage = {"Total": 0.0, "Sent": 0.0, "Receive": 0.0}
    mock_config._last_data_usage_time = 0.0
    mock_config._last_stats_refresh_time = 0.0

    # Create tables
    time_col_type = db.DATETIME
    mock_config.peersTable = db.Table(
        f'{name}', mock_config.metadata,
        db.Column('id', db.String(255), primary_key=True),
        db.Column('name', db.Text),
        db.Column('private_key', db.String(255)),
        db.Column('DNS', db.Text),
        db.Column('endpoint_allowed_ip', db.Text),
        db.Column('allowed_ip', db.String(255)),
        db.Column('total_receive', db.Float, default=0.0),
        db.Column('total_sent', db.Float, default=0.0),
        db.Column('total_data', db.Float, default=0.0),
        db.Column('cumu_receive', db.Float, default=0.0),
        db.Column('cumu_sent', db.Float, default=0.0),
        db.Column('cumu_data', db.Float, default=0.0),
        db.Column('mtu', db.Integer, default=0),
        db.Column('keepalive', db.Integer, default=0),
        db.Column('notes', db.Text),
        db.Column('endpoint', db.String(255)),
        db.Column('status', db.String(255)),
        db.Column('latest_handshake', db.String(255)),
        db.Column('preshared_key', db.String(255)),
        db.Column('restricted_reason', db.String(255)),
        extend_existing=True
    )

    mock_config.peersRestrictedTable = db.Table(
        f'{name}_restrict_access', mock_config.metadata,
        db.Column('id', db.String(255), primary_key=True),
        db.Column('name', db.Text),
        db.Column('private_key', db.String(255)),
        db.Column('DNS', db.Text),
        db.Column('endpoint_allowed_ip', db.Text),
        db.Column('allowed_ip', db.String(255)),
        db.Column('total_receive', db.Float, default=0.0),
        db.Column('total_sent', db.Float, default=0.0),
        db.Column('total_data', db.Float, default=0.0),
        db.Column('cumu_receive', db.Float, default=0.0),
        db.Column('cumu_sent', db.Float, default=0.0),
        db.Column('cumu_data', db.Float, default=0.0),
        db.Column('mtu', db.Integer, default=0),
        db.Column('keepalive', db.Integer, default=0),
        db.Column('notes', db.Text),
        db.Column('endpoint', db.String(255)),
        db.Column('status', db.String(255)),
        db.Column('latest_handshake', db.String(255)),
        db.Column('preshared_key', db.String(255)),
        db.Column('restricted_reason', db.String(255)),
        extend_existing=True
    )

    mock_config.interfaceTrafficSnapshotTable = db.Table(
        f'{name}_traffic_snapshot', mock_config.metadata,
        db.Column('configuration_name', db.String(255), primary_key=True),
        db.Column('total_receive', db.Float, server_default='0.0'),
        db.Column('total_sent', db.Float, server_default='0.0'),
        db.Column('total_data', db.Float, server_default='0.0'),
        db.Column('last_updated', time_col_type, server_default=db.func.now()),
        extend_existing=True
    )

    mock_config.metadata.create_all(mock_config.engine)

    # Bind real implementation methods to test them
    mock_config._add_to_traffic_snapshot = lambda conn, recv, sent, total: WireguardConfiguration._add_to_traffic_snapshot(mock_config, conn, recv, sent, total)
    mock_config._compute_data_usage = lambda: WireguardConfiguration._compute_data_usage(mock_config)
    mock_config._get_traffic_snapshot = lambda: WireguardConfiguration._get_traffic_snapshot(mock_config)

    return mock_config


def test_tu01_peer_deletion_no_double_counting_cumu(tmp_path):
    """
    TU-01: When deleting a peer where total_data == 0 and cumu_data > 0,
    cumulative data must not be counted twice in interface snapshot.
    """
    cfg = create_mock_configuration(tmp_path)
    peer_id = "TEST_PEER_KEY="

    # Insert a peer whose kernel counters were reset/restarted: cumu=10.0 (recv=4.0, sent=6.0), total=0.0
    with cfg.engine.begin() as conn:
        conn.execute(cfg.peersTable.insert().values(
            id=peer_id,
            name="ExpiredPeer",
            cumu_receive=4.0,
            cumu_sent=6.0,
            cumu_data=10.0,
            total_receive=0.0,
            total_sent=0.0,
            total_data=0.0
        ))

    # Simulate deletePeers calculation logic
    with cfg.engine.begin() as conn:
        stmt_peer = cfg.peersTable.select().where(cfg.peersTable.c.id == peer_id)
        db_row = conn.execute(stmt_peer).mappings().fetchone()
        assert db_row is not None

        peer_total_receive = max(0.0, float(db_row.get('cumu_receive') or 0.0) + float(db_row.get('total_receive') or 0.0))
        peer_total_sent    = max(0.0, float(db_row.get('cumu_sent') or 0.0) + float(db_row.get('total_sent') or 0.0))
        peer_total_data    = peer_total_receive + peer_total_sent

        cfg._add_to_traffic_snapshot(conn, peer_total_receive, peer_total_sent, peer_total_data)

    # Verify snapshot row
    snap = cfg._get_traffic_snapshot()
    assert snap is not None
    assert snap['total_receive'] == 4.0
    assert snap['total_sent'] == 6.0
    # Must be 10.0, NOT 20.0
    assert snap['total_data'] == 10.0
    drift = snap['total_data'] - (snap['total_receive'] + snap['total_sent'])
    assert round(drift, 4) == 0.0


def test_tu01_peer_reset_data_usage(tmp_path):
    """
    TU-01: Peer.resetDataUsage must compute peer_total_data as peer_total_receive + peer_total_sent
    and add it without duplication to the snapshot.
    """
    cfg = create_mock_configuration(tmp_path)
    peer_data = {
        "id": "PEER_RESET=",
        "name": "ResetPeer",
        "total_receive": 2.5,
        "total_sent": 3.5,
        "total_data": 6.0,
        "cumu_receive": 1.0,
        "cumu_sent": 2.0,
        "cumu_data": 3.0,
        "endpoint": "N/A",
        "status": "stopped",
        "latest_handshake": "N/A",
        "allowed_ip": "10.0.0.3/32"
    }

    with cfg.engine.begin() as conn:
        conn.execute(cfg.peersTable.insert().values(**peer_data))

    cfg.lock = MagicMock()
    cfg.lock.__enter__.return_value = None
    cfg.lock.__exit__.return_value = None

    peer = Peer(peer_data, cfg)

    # Call resetDataUsage in mode="total"
    peer.resetDataUsage(mode="total")

    snap = cfg._get_traffic_snapshot()
    assert snap is not None
    # total_receive: 1.0 (cumu) + 2.5 (cur) = 3.5
    assert snap['total_receive'] == 3.5
    # total_sent: 2.0 (cumu) + 3.5 (cur) = 5.5
    assert snap['total_sent'] == 5.5
    # total_data: 3.5 + 5.5 = 9.0
    assert snap['total_data'] == 9.0
    assert round(snap['total_data'] - (snap['total_receive'] + snap['total_sent']), 4) == 0.0


def test_tu03_snapshot_data_drift_auto_heal(tmp_path):
    """
    TU-03: Auto-heal any historic drift in traffic snapshot
    (where total_data > total_receive + total_sent) so interface never shows inflated Total.
    """
    cfg = create_mock_configuration(tmp_path)

    # Seed intentional drift from prior buggy TU-01 run
    with cfg.engine.begin() as conn:
        conn.execute(cfg.interfaceTrafficSnapshotTable.insert().values(
            configuration_name=cfg.Name,
            total_receive=4.0,
            total_sent=6.0,
            total_data=20.0  # Drift of +10.0!
        ))

    # 1. _compute_data_usage() clamps drift on the fly
    usage = cfg._compute_data_usage()
    assert usage["Receive"] == 4.0
    assert usage["Sent"] == 6.0
    assert usage["Total"] == 10.0  # Clamped to 4.0 + 6.0

    # 2. Database auto-heal query corrects the persistent row
    with cfg.engine.begin() as conn:
        t = cfg.interfaceTrafficSnapshotTable
        conn.execute(
            t.update()
            .where(
                (t.c.configuration_name == cfg.Name) &
                (t.c.total_data > (db.func.coalesce(t.c.total_receive, 0.0) + db.func.coalesce(t.c.total_sent, 0.0) + 0.0001))
            )
            .values(
                total_data=db.func.coalesce(t.c.total_receive, 0.0) + db.func.coalesce(t.c.total_sent, 0.0)
            )
        )

    snap = cfg._get_traffic_snapshot()
    assert snap["total_data"] == 10.0
    assert snap["total_data"] == snap["total_receive"] + snap["total_sent"]


def test_tu04_atomic_snapshot_update(tmp_path):
    """
    TU-04: _add_to_traffic_snapshot must perform atomic SQL updates without read-modify-write lost updates.
    """
    cfg = create_mock_configuration(tmp_path)

    # First call: table is empty, inserts row
    with cfg.engine.begin() as conn:
        cfg._add_to_traffic_snapshot(conn, 1.5, 2.5, 4.0)

    snap1 = cfg._get_traffic_snapshot()
    assert snap1["total_receive"] == 1.5
    assert snap1["total_sent"] == 2.5
    assert snap1["total_data"] == 4.0

    # Second call: updates existing row atomically
    with cfg.engine.begin() as conn:
        cfg._add_to_traffic_snapshot(conn, 3.0, 5.0, 8.0)

    snap2 = cfg._get_traffic_snapshot()
    assert snap2["total_receive"] == 4.5
    assert snap2["total_sent"] == 7.5
    assert snap2["total_data"] == 12.0


def test_tu05_zero_total_reporting(tmp_path):
    """
    TU-05: Legitimate zero Total usage must not be suppressed by stale cache fallback.
    Fallback to _last_data_usage must strictly occur during exceptions.
    """
    cfg = create_mock_configuration(tmp_path)

    # Pre-populate _last_data_usage with old non-zero data
    cfg._last_data_usage = {"Total": 100.0, "Sent": 60.0, "Receive": 40.0}
    cfg._last_data_usage_time = 0.0  # Force query

    # With empty tables, _compute_data_usage must return 0.0, not stale 100.0
    res = cfg._compute_data_usage()
    assert res["Total"] == 0.0
    assert res["Sent"] == 0.0
    assert res["Receive"] == 0.0

    # Exception fallback test: if database connection fails, return cached usage
    cfg._last_data_usage = {"Total": 42.0, "Sent": 21.0, "Receive": 21.0}
    cfg._last_data_usage_time = 0.0
    with patch.object(cfg.engine, "connect", side_effect=Exception("DB Connection Down")):
        fallback = cfg._compute_data_usage()
        assert fallback["Total"] == 42.0


def test_tu06_refresh_runtime_stats_lock_on_cumu_updates(tmp_path):
    """
    TU-06: When cumu_updates is present (kernel rollover detected),
    refreshPeersRuntimeStats must execute updates under self.lock.
    """
    cfg = create_mock_configuration(tmp_path)
    cfg.lock = MagicMock()
    cfg.getStatus.return_value = True
    cfg.Peers = []
    cfg.RestrictedPeers = []
    cfg._last_stats_refresh_time = 0.0

    # Insert a peer with total_sent=10.0, total_receive=10.0
    with cfg.engine.begin() as conn:
        conn.execute(cfg.peersTable.insert().values(
            id="ROLLOVER_PEER=",
            name="RolloverPeer",
            total_receive=10.0,
            total_sent=10.0,
            total_data=20.0,
            cumu_receive=0.0,
            cumu_sent=0.0,
            cumu_data=0.0,
            status="running",
            latest_handshake="1600000000"
        ))

    # Kernel dump format:
    # Line 0: Interface line
    # Line 1: Peer line with 0 rx / 0 tx (rollover!)
    fake_dump = "IFACE_KEY\tIFACE_PUBKEY\t51820\toff\nROLLOVER_PEER=\t(none)\t1.2.3.4:51820\t10.0.0.2/32\t1600000001\t0\t0\toff\n"

    with patch("subprocess.check_output", return_value=fake_dump.encode("utf-8")), \
         patch("time.time", return_value=100.0):
        WireguardConfiguration.refreshPeersRuntimeStats(cfg)

    # Rollover was triggered -> lock.__enter__ should have been called!
    assert cfg.lock.__enter__.called is True


def test_partial_reset_modes(tmp_path):
    """
    Test resetDataUsage in mode='receive' and mode='sent'.
    Verifies that directional resets update snapshot correctly and keep total_data == total_receive + total_sent.
    """
    cfg = create_mock_configuration(tmp_path)
    peer_data = {
        "id": "PEER_PARTIAL=",
        "name": "PartialPeer",
        "total_receive": 3.0,
        "total_sent": 5.0,
        "total_data": 8.0,
        "cumu_receive": 1.0,
        "cumu_sent": 2.0,
        "cumu_data": 3.0,
        "endpoint": "N/A",
        "status": "stopped",
        "latest_handshake": "N/A",
        "allowed_ip": "10.0.0.4/32"
    }

    with cfg.engine.begin() as conn:
        conn.execute(cfg.peersTable.insert().values(**peer_data))

    cfg.lock = MagicMock()
    peer = Peer(peer_data, cfg)

    # 1. Reset receive only
    peer.resetDataUsage(mode="receive")
    snap1 = cfg._get_traffic_snapshot()
    assert snap1 is not None
    # Receive was 1.0 + 3.0 = 4.0; Sent remains 0.0 in snapshot
    assert snap1["total_receive"] == 4.0
    assert snap1["total_sent"] == 0.0
    assert snap1["total_data"] == 4.0

    # 2. Reset sent only
    peer.resetDataUsage(mode="sent")
    snap2 = cfg._get_traffic_snapshot()
    assert snap2 is not None
    # Sent was 2.0 + 5.0 = 7.0; Total in snapshot = 4.0 + 7.0 = 11.0
    assert snap2["total_receive"] == 4.0
    assert snap2["total_sent"] == 7.0
    assert snap2["total_data"] == 11.0
    assert round(snap2["total_data"] - (snap2["total_receive"] + snap2["total_sent"]), 4) == 0.0


def test_batch_peer_deletion(tmp_path):
    """
    Test deleting multiple peers (active, restricted, and zero usage) in a single batch.
    Verifies atomic batch snapshot addition without duplicate cumulative data.
    """
    cfg = create_mock_configuration(tmp_path)

    # Peer 1 (active): cumu=5.0 (recv=2, sent=3), total=0
    # Peer 2 (restricted): cumu=0, total=4.0 (recv=1, sent=3)
    # Peer 3 (active): cumu=0, total=0
    with cfg.engine.begin() as conn:
        conn.execute(cfg.peersTable.insert().values(
            id="PEER_1=", name="P1", cumu_receive=2.0, cumu_sent=3.0, cumu_data=5.0,
            total_receive=0.0, total_sent=0.0, total_data=0.0
        ))
        conn.execute(cfg.peersRestrictedTable.insert().values(
            id="PEER_2=", name="P2", cumu_receive=0.0, cumu_sent=0.0, cumu_data=0.0,
            total_receive=1.0, total_sent=3.0, total_data=4.0
        ))
        conn.execute(cfg.peersTable.insert().values(
            id="PEER_3=", name="P3", cumu_receive=0.0, cumu_sent=0.0, cumu_data=0.0,
            total_receive=0.0, total_sent=0.0, total_data=0.0
        ))

    # Simulate deletePeers loop logic
    peers_to_delete = [
        (MagicMock(id="PEER_1="), False),
        (MagicMock(id="PEER_2="), True),
        (MagicMock(id="PEER_3="), False)
    ]

    with cfg.engine.begin() as conn:
        batch_recv = 0.0
        batch_sent = 0.0
        batch_data = 0.0

        for pf, is_restricted in peers_to_delete:
            stmt_peer = cfg.peersTable.select().where(cfg.peersTable.c.id == pf.id)
            db_row = conn.execute(stmt_peer).mappings().fetchone()
            if not db_row:
                stmt_restricted = cfg.peersRestrictedTable.select().where(cfg.peersRestrictedTable.c.id == pf.id)
                db_row = conn.execute(stmt_restricted).mappings().fetchone()

            if db_row:
                p_recv = max(0.0, float(db_row.get('cumu_receive') or 0.0) + float(db_row.get('total_receive') or 0.0))
                p_sent = max(0.0, float(db_row.get('cumu_sent') or 0.0) + float(db_row.get('total_sent') or 0.0))
                p_data = p_recv + p_sent
            else:
                p_recv = p_sent = p_data = 0.0

            batch_recv += p_recv
            batch_sent += p_sent
            batch_data += p_data

        if batch_data > 0.0:
            cfg._add_to_traffic_snapshot(conn, batch_recv, batch_sent, batch_data)

    snap = cfg._get_traffic_snapshot()
    # P1: recv=2, sent=3 -> data=5
    # P2: recv=1, sent=3 -> data=4
    # P3: 0
    # Expected: recv=3, sent=6, data=9
    assert snap["total_receive"] == 3.0
    assert snap["total_sent"] == 6.0
    assert snap["total_data"] == 9.0
    assert round(snap["total_data"] - (snap["total_receive"] + snap["total_sent"]), 4) == 0.0

