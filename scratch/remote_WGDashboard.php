<?php
include('config.php');
ini_set('error_log', 'error_log');


function get_userwg($username, $namepanel)
{
    $marzban_list_get = select("marzban_panel", "*", "name_panel", $namepanel, "select");
    $url = $marzban_list_get['url_panel'] . '/api/getWireguardConfigurationInfo?configurationName=' . $marzban_list_get['inboundid'];
    $headers = array(
        'Accept: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel']
    );
    
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
    $req->setTimeout(15000); // 15s timeout to prevent hanging
    $api_res = $req->get();
    
    if (!empty($api_res['error'])) {
        return ['status' => false, 'msg' => 'API Connection Error: ' . $api_res['error']];
    }
    
    if (empty($api_res['status']) || $api_res['status'] != 200) {
        return ['status' => false, 'msg' => 'API Error: HTTP ' . ($api_res['status'] ?? 'unknown')];
    }
    
    $response_str = $api_res['body'];
    
    $response = json_decode($response_str, true);
    if (!is_array($response)) {
        return ['status' => false, 'msg' => 'Invalid JSON from WGDashboard'];
    }
    
    if (empty($response['status'])) {
        if (isset($response['message'])) {
            $response['msg'] = $response['message'];
        }
        return $response;
    }
    $configurationPeers = $response['data']['configurationPeers'] ?? [];
    $configurationRestrictedPeers = $response['data']['configurationRestrictedPeers'] ?? [];
    $output = [];
    foreach ($configurationPeers as $userinfo) {
        if ($userinfo['name'] == $username) {
            $output = $userinfo;
            break;
        }
    }
    if (count($output) != 0) {
        $output['id'] = $output['id'] ?? $output['publicKey'] ?? $output['name'] ?? null;
        return $output;
    }
    foreach ($configurationRestrictedPeers as $userinfo) {
        if ($userinfo['name'] == $username) {
            $output = $userinfo;
            $output['configuration']['Status'] = false;
            break;
        }
    }
    if (count($output) != 0) {
        $output['id'] = $output['id'] ?? $output['publicKey'] ?? $output['name'] ?? null;
    }
    return $output;
}

function ipslast($namepanel)
{

    $marzban_list_get = select("marzban_panel", "*", "name_panel", $namepanel, "select");
    $url = $marzban_list_get['url_panel'] . '/api/getAvailableIPs/' . $marzban_list_get['inboundid'];
    $headers = array(
        'Accept: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel']
    );
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
    $req->setTimeout(15000); // Prevent hanging
    $response = $req->get();
    return $response;
}
function downloadconfig($namepanel, $publickey)
{

    $marzban_list_get = select("marzban_panel", "*", "name_panel", $namepanel, "select");
    $url = $marzban_list_get['url_panel'] . "/api/downloadPeer/{$marzban_list_get['inboundid']}?id=" . urlencode($publickey);
    $headers = array(
        'Accept: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel']
    );

    $max_retries = 20;
    $retry_delay = 500000; // microseconds (0.5s)
    $response = null;

    for ($i = 0; $i < $max_retries; $i++) {
        $req = new CurlRequest($url);
        $req->setHeaders($headers);
        $req->setTimeout(5000); // 5s timeout to avoid hanging if panel is down
        $response = $req->get();

        $body = json_decode($response['body'], true);
        if (isset($body['status']) && $body['status'] === true) {
            return $response;
        }

        // Abort early if the peer definitely does not exist (to avoid a 10s wait block for deleted peers)
        if (isset($body['status']) && $body['status'] === false && isset($body['message']) && stripos($body['message'], 'does not exist') !== false) {
            return $response;
        }

        // If not successful (e.g. peer not found due to WGDashboard sync delay), wait and retry
        usleep($retry_delay);
    }

    return $response;
}

/**
 * getCachedSubnet: get subnet from DB cache or fetch from API (once per panel).
 * Saves ~3-5s by skipping getWireguardConfigurationInfo on every peer creation.
 */
function getCachedSubnet($namepanel, $marzban_list_get, $force_refresh = false)
{
    global $pdo;
    // Try DB cache first
    if ($pdo && !$force_refresh) {
        try {
            $stmt = $pdo->prepare("SELECT subnet_cache FROM marzban_panel WHERE name_panel = :name LIMIT 1");
            $stmt->execute([':name' => $namepanel]);
            $row = $stmt->fetch(PDO::FETCH_ASSOC);
            if ($row && !empty($row['subnet_cache'])) {
                return $row['subnet_cache'];
            }
        } catch (\Exception $e) {
            // Column may not exist yet, will fall through to API fetch
        }
    }

    // Cache miss: fetch from API
    $url = $marzban_list_get['url_panel'] . '/api/getWireguardConfigurationInfo?configurationName=' . $marzban_list_get['inboundid'];
    $headers = [
        'Accept: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel']
    ];
    $req = new CurlRequest($url);
    $req->setTimeout(15000);
    $req->setHeaders($headers);
    $api_res = $req->get();

    if (empty($api_res['status']) || $api_res['status'] != 200 || empty($api_res['body'])) {
        return null;
    }

    $response = json_decode($api_res['body'], true);
    $subnet = null;
    if (is_array($response) && isset($response['data'])) {
        if (!empty($response['data']['configurationInfo']['Address'])) {
            $subnet = $response['data']['configurationInfo']['Address'];
        } elseif (!empty($response['data']['conf_address'])) {
            $subnet = $response['data']['conf_address'];
        }
    }

    if (empty($subnet)) return null;

    // Extract first IPv4 CIDR if multiple
    if (strpos($subnet, ',') !== false) {
        foreach (explode(',', $subnet) as $part) {
            $part = trim($part);
            if (strpos($part, ':') === false && strpos($part, '/') !== false) {
                $subnet = $part;
                break;
            }
        }
    }

    // Save to DB cache for next time
    if ($pdo) {
        try {
            // Check if column exists, add if not (compatible with MySQL 5.7)
            $stmt = $pdo->prepare("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'marzban_panel' AND column_name = 'subnet_cache'");
            $stmt->execute();
            if ($stmt->fetchColumn() == 0) {
                $pdo->exec("ALTER TABLE marzban_panel ADD COLUMN subnet_cache VARCHAR(50) DEFAULT NULL");
            }
            
            $stmt = $pdo->prepare("UPDATE marzban_panel SET subnet_cache = :subnet WHERE name_panel = :name");
            $stmt->execute([':subnet' => $subnet, ':name' => $namepanel]);
        } catch (\Exception $e) {
            error_log("subnet_cache save failed: " . $e->getMessage());
        }
    }

    return $subnet;
}

function addpear($namepanel, $usernameac)
{
    $marzban_list_get = select("marzban_panel", "*", "name_panel", $namepanel, "select");

    // --- STEP 1: Generate keys (fast, no network) ---
    $pubandprivate = publickey();
    if ($pubandprivate === false) {
        return array(
            'status' => false,
            'msg' => 'PHP sodium extension is missing. Cannot generate WireGuard keys.'
        );
    }

    // --- STEP 2: Get subnet from DB cache (instant) or API (once per panel) ---
    $subnet = getCachedSubnet($namepanel, $marzban_list_get);
    if (empty($subnet)) {
        return array(
            'status' => false,
            'msg' => 'Could not determine subnet for panel. Check WGDashboard connectivity.'
        );
    }

    // --- STEP 3 & 4: Atomic IP assignment with advisory lock (prevents race condition) ---
    // If two users buy simultaneously, MySQL lock ensures only one gets each IP.
    global $pdo, $connect;
    // Use md5 to ensure the lock name never exceeds MySQL's 64 character limit
    // even if the panel name contains long Farsi descriptions.
    $lockName = 'wg_ip_lock_' . md5($namepanel);
    $lockAcquired = false;
    $lockDebugInfo = 'unknown';
    if ($pdo) {
        try {
            // Set lock timeout to 15 seconds. If it takes longer, WGDashboard is extremely backed up.
            // Previously was 120s, which could cause webhook timeouts on Telegram's side.
            $lockStmt = $pdo->query("SELECT GET_LOCK('" . $lockName . "', 15)");
            $lockVal = $lockStmt ? $lockStmt->fetchColumn() : 'stmt_false';
            $lockAcquired = ($lockVal == 1);
            $lockDebugInfo = 'val_' . $lockVal;
        } catch (\Exception $e) {
            $lockDebugInfo = 'exception_' . substr($e->getMessage(), 0, 150);
            error_log("Advisory lock failed: " . $e->getMessage());
        }
    } else {
        $lockDebugInfo = 'no_pdo';
    }

    // SAFETY: Abort if we couldn't get the lock (prevents concurrent IP corruption)
    if (!$lockAcquired) {
        return array('status' => false, 'msg' => 'Server is currently busy configuring other users. Please try again in a few moments. (Code: ' . $lockDebugInfo . ')');
    }

    // Merge IPs from BOTH our DB and WGDashboard API to prevent duplicate assignment.
    // Our DB covers peers created by the bot; WGDashboard API covers peers created manually.
    $db_used_ips = getUsedIPsFromDb($namepanel);
    $api_used_ips = getUsedIPs($namepanel);
    
    // SAFETY: Abort if panel is down or restarting so we don't spam it with addPeers requests
    if ($api_used_ips === false) {
        if ($lockAcquired && $pdo) {
            try { $pdo->query("SELECT RELEASE_LOCK('" . $lockName . "')"); } catch (\Exception $e) {}
        }
        return array('status' => false, 'msg' => 'Could not connect to WGDashboard to verify available IPs. Panel might be restarting or offline. Please try again.');
    }

    $all_used_ips = array_merge($db_used_ips, $api_used_ips);
    $clean_used_ips = [];
    foreach ($all_used_ips as $ip) {
        $clean_ip = explode('/', $ip)[0];
        if (filter_var($clean_ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
            $clean_used_ips[$clean_ip] = true; // Use hash map for dedup
        }
    }
    $clean_used_ips = array_keys($clean_used_ips);

    $is_full = isSubnetFull($subnet, $clean_used_ips);
    $ipToAssign = getNextAvailableIP($subnet, $clean_used_ips);
    
    if ($is_full || empty($ipToAssign) || !filter_var($ipToAssign, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
        // Maybe the admin upgraded the subnet size in WGDashboard but DB cache is stale?
        // Let's force a refresh from the API and try exactly ONE more time.
        $new_subnet = getCachedSubnet($namepanel, $marzban_list_get, true);
        if (!empty($new_subnet) && $new_subnet !== $subnet) {
            $subnet = $new_subnet; // Subnet was indeed upgraded!
            $is_full = isSubnetFull($subnet, $clean_used_ips);
            $ipToAssign = getNextAvailableIP($subnet, $clean_used_ips);
        }
    }

    if ($is_full || empty($ipToAssign) || !filter_var($ipToAssign, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
        if ($lockAcquired && $pdo) {
            try { $pdo->query("SELECT RELEASE_LOCK('" . $lockName . "')"); } catch (\Exception $e) {}
        }
        
        // Compute debug metrics so the admin knows WHY it failed
        $capacity = 0; $used_count = count($clean_used_ips);
        if (strpos($subnet, '/') !== false) {
            list($dummy, $c) = explode('/', $subnet);
            $c = intval($c);
            if ($c >= 0 && $c <= 32) {
                $total_ips = 1 << (32 - $c);
                $skipped = (max(1, $total_ips >> 8) * 2) + 1;
                $capacity = max(0, $total_ips - $skipped);
            }
        }
        
        return array(
            'status' => false, 
            'msg' => "Server capacity is full or no IPs available.\n(Debug -> Subnet: $subnet, Capacity: $capacity, Total Reserved IPs: $used_count)"
        );
    }
    // --- STEP 5: POST to WGDashboard addPeers ---
    $peerConfig = array(
        'name'                    => $usernameac,
        'allowed_ips'             => [$ipToAssign . '/32'],
        'allowed_ip'              => $ipToAssign . '/32',
        'private_key'             => $pubandprivate['private_key'],
        'public_key'              => $pubandprivate['public_key'],
        'preshared_key'           => $pubandprivate['preshared_key'],
        'endpoint_allowed_ip'     => '0.0.0.0/0',
        'DNS'                     => '1.1.1.1',
        'mtu'                     => 1420,
        'keepalive'               => 21,
    );
    // addPeers expects the peer config as a single JSON object
    $configpanel = json_encode($peerConfig);
    $url = $marzban_list_get['url_panel'] . '/api/addPeers/' . $marzban_list_get['inboundid'];
    $headers = array(
        'Accept: application/json',
        'Content-Type: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel']
    );
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
    // Allow up to 90s for WGDashboard's slow getAvailableIP on large subnets and WG restart
    $req->setTimeout(90000);
    $response = $req->post($configpanel);

    // If curl error (timeout/connection refused), still return success if IP was assigned
    // because WGDashboard may have added the peer before its slow check finished
    if (!empty($response['error'])) {
        // Try a quick GET to verify peer was actually added using existing get_userwg
        $vdata = get_userwg($usernameac, $namepanel);
        if (!empty($vdata) && (isset($vdata['id']) || isset($vdata['publicKey']) || isset($vdata['name']))) {
            // Peer exists — success despite timeout
            $response['status'] = 200;
            $response['error'] = null;
        } else {
            // Release lock before returning error
            if ($lockAcquired && $pdo) {
                try { $pdo->query("SELECT RELEASE_LOCK('" . $lockName . "')"); } catch (\Exception $e) {}
            }
            return array(
                'status' => false,
                'msg' => 'WGDashboard did not respond in time: ' . $response['error']
            );
        }
    }

    // Release advisory lock after peer is confirmed added and IP is successfully updated
    if ($lockAcquired && $pdo) {
        try { $pdo->query("SELECT RELEASE_LOCK('" . $lockName . "')"); } catch (\Exception $e) {}
    }

    $result_response = $response['body'];
    // Use peerConfig (has all fields) instead of old $config
    $response['body'] = $peerConfig;
    $response['body']['response'] = $result_response;
    return $response;
}
function setjob($namepanel, $type, $value, $publickey)
{
    $marzban_list_get = select("marzban_panel", "*", "name_panel", $namepanel, "select");
    $data = json_encode(array(
        "Job" => array(
            "JobID" => generateUUID(),
            "Configuration" => $marzban_list_get['inboundid'],
            "Peer" => $publickey,
            "Field" => $type,
            "Operator" => "lgt",
            "Value" => strval($value),
            "CreationDate" => "",
            "ExpireDate" => null,
            "Action" => "restrict"
        )
    ));
    $url = $marzban_list_get['url_panel'] . '/api/savePeerScheduleJob';
    $headers = array(
        'Accept: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel'],
        'Content-Type: application/json',
    );
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
    $req->setTimeout(15000); // Prevent hanging
    $response = $req->post($data);
    return $response;

}
function updatepear($namepanel, array $config)
{
    // STRICT DEFENSIVE SHIELD: Validate IP address before sending request to WGDashboard panel API
    if (isset($config['allowed_ip'])) {
        // allowed_ip is a string like "10.0.0.9/32" or "10.0.0.9/32, 10.0.0.10/32"
        $ips = explode(',', $config['allowed_ip']);
        foreach ($ips as $ip) {
            $clean_ip = explode('/', trim($ip))[0];
            if (!filter_var($clean_ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
                return array(
                    'status' => false,
                    'msg' => 'Aborted: Invalid IP address in update config (' . var_export($ip, true) . ') to prevent WGDashboard infinite loop.'
                );
            }
        }
    }

    $marzban_list_get = select("marzban_panel", "*", "name_panel", $namepanel, "select");
    $configpanel = json_encode($config);
    $url = $marzban_list_get['url_panel'] . '/api/updatePeerSettings/' . $marzban_list_get['inboundid'];
    $headers = array(
        'Accept: application/json',
        'Content-Type: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel']
    );
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
    $req->setTimeout(15000); // Prevent hanging
    $response = $req->post($configpanel);
    return $response;
}
function deletejob($namepanel, array $config)
{

    $marzban_list_get = select("marzban_panel", "*", "name_panel", $namepanel, "select");
    $configpanel = json_encode($config);
    $url = $marzban_list_get['url_panel'] . '/api/deletePeerScheduleJob';
    $headers = array(
        'Accept: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel'],
        'Content-Type: application/json',
    );
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
    $req->setTimeout(15000); // Prevent hanging
    $response = $req->post($configpanel);
    return $response;
}
function ResetUserDataUsagewg($publickey, $namepanel)
{

    $marzban_list_get = select("marzban_panel", "*", "name_panel", $namepanel, "select");
    $config = array(
        "id" => $publickey,
        "type" => "total"
    );
    $configpanel = json_encode($config);
    $url = $marzban_list_get['url_panel'] . '/api/resetPeerData/' . $marzban_list_get['inboundid'];
    $headers = array(
        'Accept: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel'],
        'Content-Type: application/json',
    );
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
    $req->setTimeout(15000); // Prevent hanging
    $response = $req->post($configpanel);
    return $response;
}
function remove_userwg($location, $username)
{
    allowAccessPeers($location, $username);
    $marzban_list_get = select("marzban_panel", "*", "name_panel", $location, "select");
    $invoice = select("invoice", "user_info", "username", $username, "select");
    $user_info = $invoice ? json_decode($invoice['user_info'], true) : null;
    $data_user = is_array($user_info) ? ($user_info['public_key'] ?? $user_info['id'] ?? null) : null;
    if (empty($data_user)) {
        return false;
    }
    $url = $marzban_list_get['url_panel'] . '/api/deletePeers/' . $marzban_list_get['inboundid'];
    $headers = array(
        'Accept: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel'],
        'Content-Type: application/json',
    );
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
    $req->setTimeout(15000); // Prevent hanging
    $response = $req->post(json_encode(array(
        "peers" => array(
            $data_user
        )
    )));
    return $response;
}
function allowAccessPeers($location, $username)
{

    $marzban_list_get = select("marzban_panel", "*", "name_panel", $location, "select");
    $invoice = select("invoice", "user_info", "username", $username, "select");
    $user_info = $invoice ? json_decode($invoice['user_info'], true) : null;
    $data_user = is_array($user_info) ? ($user_info['public_key'] ?? $user_info['id'] ?? null) : null;
    if (empty($data_user)) {
        return false;
    }
    $url = $marzban_list_get['url_panel'] . '/api/allowAccessPeers/' . $marzban_list_get['inboundid'];
    $headers = array(
        'Accept: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel'],
        'Content-Type: application/json',
    );
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
    $req->setTimeout(15000); // Prevent hanging
    $response = $req->post(json_encode(array(
        "peers" => array(
            $data_user
        )
    )));
    return $response;
}
function restrictPeers($location, $username)
{
    $marzban_list_get = select("marzban_panel", "*", "name_panel", $location, "select");
    $invoice = select("invoice", "user_info", "username", $username, "select");
    $user_info = $invoice ? json_decode($invoice['user_info'], true) : null;
    $data_user = is_array($user_info) ? ($user_info['public_key'] ?? $user_info['id'] ?? null) : null;
    if (empty($data_user)) {
        return false;
    }
    // Use CurlRequest with API key (safe) and proper timeout (not 0!)
    $url = $marzban_list_get['url_panel'] . '/api/restrictPeers/' . $marzban_list_get['inboundid'];
    $headers = array(
        'Accept: application/json',
        'Content-Type: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel']
    );
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
    $req->setTimeout(15000);
    $response = $req->post(json_encode(array('peers' => array($data_user))));
    return !empty($response['body']) ? json_decode($response['body'], true) : false;
}

function getUsedIPs($namepanel)
{
    $marzban_list_get = select("marzban_panel", "*", "name_panel", $namepanel, "select");
    if (!$marzban_list_get) {
        return [];
    }
    $url = $marzban_list_get['url_panel'] . '/api/getWireguardConfigurationInfo?configurationName=' . $marzban_list_get['inboundid'];
    $headers = array(
        'Accept: application/json',
        'wg-dashboard-apikey: ' . $marzban_list_get['password_panel']
    );
    
    $req = new CurlRequest($url);
    $req->setHeaders($headers);
    $req->setTimeout(10000); // 10s max wait to fail fast if panel is crashing
    $api_res = $req->get();
    
    if (empty($api_res['status']) || $api_res['status'] != 200 || empty($api_res['body'])) {
        return false;
    }
    
    $response = json_decode($api_res['body'], true);
    if (!is_array($response) || empty($response['status'])) {
        return false;
    }
    
    $peers = array_merge(
        $response['data']['configurationPeers'] ?? [],
        $response['data']['configurationRestrictedPeers'] ?? []
    );
    
    $used_ips = [];
    foreach ($peers as $peer) {
        if (isset($peer['allowed_ips']) && is_array($peer['allowed_ips'])) {
            foreach ($peer['allowed_ips'] as $ip) {
                $used_ips[] = trim($ip);
            }
        } elseif (isset($peer['allowed_ip']) && is_string($peer['allowed_ip'])) {
            // WGDashboard API returns "allowed_ip" as a comma-separated string
            $ips = explode(',', $peer['allowed_ip']);
            foreach ($ips as $ip) {
                if (trim($ip) !== '' && trim($ip) !== 'N/A') {
                    $used_ips[] = trim($ip);
                }
            }
        }
    }
    return $used_ips;
}

function getUsedIPsFromDb($namepanel)
{
    global $pdo;
    $used_ips = [];
    if (!$pdo) {
        return [];
    }
    try {
        // Use JSON_EXTRACT for fast IP retrieval without parsing all JSON in PHP
        // Falls back to PHP-side JSON parse if JSON_EXTRACT not supported
        $stmt = $pdo->prepare(
            "SELECT JSON_UNQUOTE(JSON_EXTRACT(user_info, '$.allowed_ips[0]')) AS ip0,
                    JSON_UNQUOTE(JSON_EXTRACT(user_info, '$.allowed_ips[1]')) AS ip1
             FROM invoice
             WHERE Service_location = :location AND user_info IS NOT NULL AND user_info != ''
               AND LOWER(Status) IN ('active', 'end_of_time', 'end_of_volume', 'sendedwarn', 'send_on_hold', 'disablebyadmin', 'disabledn', 'disabled', 'test', 'testing')"
        );
        $stmt->execute([':location' => $namepanel]);
        $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
        foreach ($rows as $row) {
            if (!empty($row['ip0']) && $row['ip0'] !== 'null') $used_ips[] = $row['ip0'];
            if (!empty($row['ip1']) && $row['ip1'] !== 'null') $used_ips[] = $row['ip1'];
        }
    } catch (\Exception $e) {
        // Fallback: PHP-side JSON parse (older MySQL without JSON_EXTRACT)
        error_log("JSON_EXTRACT not supported, falling back: " . $e->getMessage());
        try {
            $stmt = $pdo->prepare(
                "SELECT user_info FROM invoice
                 WHERE Service_location = :location AND user_info IS NOT NULL AND user_info != ''
                   AND LOWER(Status) IN ('active', 'end_of_time', 'end_of_volume', 'sendedwarn', 'send_on_hold', 'disablebyadmin', 'disabledn', 'disabled', 'test', 'testing')"
            );
            $stmt->execute([':location' => $namepanel]);
            $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
            foreach ($rows as $row) {
                if (!empty($row['user_info'])) {
                    $info = json_decode($row['user_info'], true);
                    if (is_array($info) && isset($info['allowed_ips']) && is_array($info['allowed_ips'])) {
                        foreach ($info['allowed_ips'] as $ip) {
                            $used_ips[] = $ip;
                        }
                    }
                }
            }
        } catch (\Exception $e2) {
            error_log("Failed to get used IPs from DB: " . $e2->getMessage());
        }
    }
    return $used_ips;
}

function getNextAvailableIP($subnet_cidr, $used_ips)
{
    if (strpos($subnet_cidr, ',') !== false) {
        $parts = explode(',', $subnet_cidr);
        foreach ($parts as $part) {
            $part = trim($part);
            if (strpos($part, ':') === false && strpos($part, '/') !== false) {
                $subnet_cidr = $part;
                break;
            }
        }
    }
    if (strpos($subnet_cidr, '/') === false) {
        $subnet_cidr .= '/24';
    }
    list($subnet_ip, $cidr) = explode('/', $subnet_cidr);
    $cidr = intval($cidr);
    if ($cidr < 0 || $cidr > 32) {
        $cidr = 24;
    }
    
    $subnet_long = ip2long($subnet_ip);
    if ($subnet_long === false) {
        return null;
    }
    
    // PHP safe mask calculation for both 32-bit and 64-bit systems
    $num_ips = 1 << (32 - $cidr);
    $mask = ~((1 << (32 - $cidr)) - 1);
    $network_long = $subnet_long & $mask;
    
    // Use an associative array (hash map) for O(1) lookups -> zero performance bottleneck!
    $used_longs = [];
    foreach ($used_ips as $ip) {
        $clean_ip = explode('/', $ip)[0];
        $long_ip = ip2long($clean_ip);
        if ($long_ip !== false && ($long_ip & $mask) === $network_long) {
            $used_longs[$long_ip] = true;
        }
    }
    
    // Collect all available IPs
    $available_ips = [];
    for ($i = 2; $i < $num_ips - 1; $i++) {
        $candidate_long = $network_long + $i;
        
        // Skip .0 and .255 boundaries completely.
        $last_octet = $candidate_long & 0xFF;
        if ($last_octet === 0 || $last_octet === 255) {
            continue;
        }
        
        if (!isset($used_longs[$candidate_long])) {
            $candidate_ip = long2ip($candidate_long);
            if ($candidate_ip !== false && filter_var($candidate_ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
                $available_ips[] = $candidate_ip;
            }
        }
    }
    
    // Pick a random available IP to drastically reduce collision probability
    if (!empty($available_ips)) {
        return $available_ips[array_rand($available_ips)];
    }
    
    return null;
}

function isSubnetFull($subnet_cidr, $used_ips_array)
{
    if (strpos($subnet_cidr, ',') !== false) {
        $parts = explode(',', $subnet_cidr);
        foreach ($parts as $part) {
            $part = trim($part);
            if (strpos($part, ':') === false && strpos($part, '/') !== false) {
                $subnet_cidr = $part;
                break;
            }
        }
    }
    if (empty($subnet_cidr) || strpos($subnet_cidr, '/') === false) {
        $subnet_cidr .= '/24';
    }
    
    list($subnet_ip, $cidr) = explode('/', $subnet_cidr);
    $cidr = intval($cidr);

    if ($cidr < 0 || $cidr > 32) {
        $cidr = 24;
    }

    $subnet_long = ip2long($subnet_ip);
    if ($subnet_long === false) {
        return false; // Invalid subnet (e.g., IPv6), do not block
    }

    // Calculate usable capacity correctly for all subnet sizes.
    // For any subnet: total IPs - number of .0 boundaries - number of .255 boundaries - 1 (gateway .1)
    $total_ips   = 1 << (32 - $cidr);
    $num_slices  = max(1, $total_ips >> 8); // number of /24 blocks inside this subnet
    $skipped     = ($num_slices * 2) + 1;   // .0 and .255 per slice, plus gateway .1
    $capacity    = max(0, $total_ips - $skipped);

    $mask = ~((1 << (32 - $cidr)) - 1);
    $network = $subnet_long & $mask;

    // Fast check for used IPs matching the strict network mask
    $unique_ips = [];
    foreach ($used_ips_array as $ip) {
        $clean_ip = explode('/', $ip)[0];
        $ip_long = ip2long($clean_ip);
        // ONLY count IPs that actually belong to this specific subnet
        if ($ip_long !== false && ($ip_long & $mask) === $network) {
            $unique_ips[$clean_ip] = true;
        }
    }
    
    $used_count = count($unique_ips);

    return $used_count >= $capacity;
}