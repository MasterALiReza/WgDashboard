import json

mapping = {}

with open('scratch/bot_user_info_dump.txt', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line or line == 'user_info':
            continue
        try:
            user_info = json.loads(line)
            if 'public_key' in user_info and 'name' in user_info:
                pub = user_info['public_key']
                name = user_info['name']
                priv = user_info.get('private_key', '')
                allowed_ip = user_info.get('allowed_ip', '')
                
                # Some might be a dictionary or have extra quotes, we handle strings here.
                if isinstance(name, str):
                    mapping[pub] = {
                        "name": name,
                        "private_key": priv,
                        "allowed_ip": allowed_ip
                    }
        except Exception as e:
            pass

print(f"Loaded {len(mapping)} peers from bot DB.")
with open('scratch/bot_peers_mapping.json', 'w', encoding='utf-8') as f:
    json.dump(mapping, f, indent=2)

