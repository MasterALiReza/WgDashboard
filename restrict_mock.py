import sqlite3
conn = sqlite3.connect('C:\\Users\\iWexort\\Documents\\Github\\WGDashboard-main\\WGDashboard-main\\src\\db\\wgdashboard.db')
cursor = conn.cursor()
cursor.execute("SELECT * FROM wg0 WHERE id='xyz123'")
peer = cursor.fetchone()
if peer:
    # Update restricted reason before insert if it's the last column
    # peer is a tuple, let's cast to list
    p = list(peer)
    p[-1] = "Testing restrict functionality"
    
    # insert into wg0_restrict_access
    placeholders = ','.join(['?']*len(p))
    cursor.execute(f"INSERT INTO wg0_restrict_access VALUES ({placeholders})", p)
    cursor.execute("DELETE FROM wg0 WHERE id='xyz123'")
    conn.commit()
    print("Peer moved to restricted")
else:
    print("Peer not found in wg0")
conn.close()
