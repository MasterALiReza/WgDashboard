import sqlite3
import uuid

conn = sqlite3.connect('C:\\Users\\iWexort\\Documents\\Github\\WGDashboard-main\\WGDashboard-main\\src\\db\\wgdashboard.db')
cursor = conn.cursor()

# Insert 1 active peer
# cursor.execute("INSERT INTO wg0 (id, private_key, preshared_key, name, total_receive, total_sent, total_data, endpoint, status, latest_handshake, allowed_ip, endpoint_allowed_ip, cumu_receive, cumu_sent, cumu_data, mtu, keepalive, DNS, remote_endpoint, notes, restricted_reason) VALUES ('active_peer1', 'priv1', '', 'Active Peer 1', 0, 0, 0, 'N/A', 'running', 'N/A', '10.0.0.10/32', '10.0.0.10/32', 0, 0, 0, '1420', 21, '1.1.1.1', 'N/A', '', NULL)")

# Insert 2 restricted peers
cursor.execute("INSERT INTO wg0_restrict_access (id, private_key, preshared_key, name, total_receive, total_sent, total_data, endpoint, status, latest_handshake, allowed_ip, endpoint_allowed_ip, cumu_receive, cumu_sent, cumu_data, mtu, keepalive, DNS, remote_endpoint, notes, restricted_reason) VALUES ('rest_peer1', 'priv2', '', 'Restricted Peer 1', 0, 0, 0, 'N/A', 'stopped', 'N/A', '10.0.0.11/32', '10.0.0.11/32', 0, 0, 0, '1420', 21, '1.1.1.1', 'N/A', '', 'Time Limit Reached')")

cursor.execute("INSERT INTO wg0_restrict_access (id, private_key, preshared_key, name, total_receive, total_sent, total_data, endpoint, status, latest_handshake, allowed_ip, endpoint_allowed_ip, cumu_receive, cumu_sent, cumu_data, mtu, keepalive, DNS, remote_endpoint, notes, restricted_reason) VALUES ('rest_peer2', 'priv3', '', 'Restricted Peer 2', 0, 0, 0, 'N/A', 'stopped', 'N/A', '10.0.0.12/32', '10.0.0.12/32', 0, 0, 0, '1420', 21, '1.1.1.1', 'N/A', '', 'Volume Limit Reached')")

conn.commit()
conn.close()
print("3 peers added to database.")
