import requests
import json
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SESSION = requests.Session()
BASE_URL = 'https://hcodm.vipvirtualnet.eu:8443'

# Login
success = False
for user in ["Wexort", "admin", "root", "administrator"]:
    resp = SESSION.post(f"{BASE_URL}/api/authenticate", json={"username": user, "password": "weoxrt123"}, verify=False)
    if resp.json().get("status"):
        print(f"Logged in as {user}!")
        success = True
        break

if not success:
    print("Login failed!")
    exit(1)

with open("scratch/bot_peers_mapping.json", "r", encoding="utf-8") as f:
    bot_mapping = json.load(f)

for config in ['EGPROXY-4', 'Hvipgaming']:
    print(f"\nProcessing {config}...")
    resp = SESSION.get(f"{BASE_URL}/api/getPeers/{config}", verify=False)
    if resp.status_code != 200:
        print(f"Failed to get peers for {config}: {resp.status_code}")
        continue
    
    data = resp.json()
    if not data.get("status"):
        print(f"Error fetching peers for {config}:", data.get("message"))
        continue
    
    peers = data.get("data", [])
    print(f"Found {len(peers)} peers in {config}.")
    
    updated_count = 0
    for peer in peers:
        pub = peer.get("id")  # In WGDashboard API, id is the public_key
        name = peer.get("name")
        private_key = peer.get("private_key")
        
        needs_update = False
        new_name = name
        new_priv = private_key
        
        # Check if name is missing or "UntitledPeer"
        if not name or name == "UntitledPeer" or name == "None":
            if pub in bot_mapping:
                new_name = bot_mapping[pub]["name"]
                needs_update = True
        
        # Check if private key is missing
        if not private_key:
            if pub in bot_mapping and bot_mapping[pub]["private_key"]:
                new_priv = bot_mapping[pub]["private_key"]
                needs_update = True
                
        if needs_update:
            print(f"Updating peer {pub[:10]}... Old Name: {name}, New Name: {new_name}, Has Priv: {bool(private_key)} -> {bool(new_priv)}")
            # We must provide all other settings to avoid resetting them
            update_data = {
                "id": pub,
                "name": new_name or name or "",
                "private_key": new_priv or private_key or "",
                "DNS": peer.get("endpoint_allowed_ip", ""), # wait, API expects DNS but peer object might have different keys
                # We need to map the peer keys correctly
            }
            # Actually, let's print them first to see what we need to update
            # print("Peer obj:", peer)
            updated_count += 1
            
    print(f"Total peers needing update in {config}: {updated_count}")

