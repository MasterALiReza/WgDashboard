<?php
// Note: Content-Type header is set by each page individually

// Central session lifetime: 14 days
$session_lifetime = 1209600;

// Set cookie parameters and options before starting the session
ini_set('session.gc_maxlifetime', $session_lifetime);
ini_set('session.cookie_lifetime', $session_lifetime);

// Use a secure custom session directory inside the project to prevent shared hosting GC from deleting files
$session_save_path = __DIR__ . '/../sessions';
if (!is_dir($session_save_path)) {
    @mkdir($session_save_path, 0755, true);
}
if (is_writable($session_save_path)) {
    ini_set('session.save_path', $session_save_path);
}

// Start session with secure options
if (session_status() === PHP_SESSION_NONE) {
    session_start([
        'cookie_lifetime' => $session_lifetime,
        'gc_maxlifetime' => $session_lifetime,
        'cookie_secure' => isset($_SERVER['HTTPS']) && ($_SERVER['HTTPS'] === 'on' || $_SERVER['SERVER_PORT'] == 443),
        'cookie_httponly' => true,
        'cookie_samesite' => 'Lax'
    ]);
}

require __DIR__ . '/../../config.php';
require __DIR__ . '/../../function.php';


// Panel UI language strings (loaded from project text.json)
$textbotlang = languagechange(__DIR__ . '/../../text.json');

function db_query(PDO $pdo, string $sql, array $params = []): PDOStatement
{
    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    return $stmt;
}

function db_fetch(PDO $pdo, string $sql, array $params = []): ?array
{
    return db_query($pdo, $sql, $params)->fetch() ?: null;
}

function db_fetchAll(PDO $pdo, string $sql, array $params = []): array
{
    return db_query($pdo, $sql, $params)->fetchAll();
}

function db_count(PDO $pdo, string $sql, array $params = []): int
{
    return (int) db_query($pdo, $sql, $params)->fetchColumn();
}
function render_flags($text) {
    // 1. First, handle emoji regional indicator symbols
    $emojiPattern = '/([\xf0\x9f\x87][\xa6-\xbf])([\xf0\x9f\x87][\xa6-\xbf])/';
    $text = preg_replace_callback($emojiPattern, function($matches) {
        $char1 = ord($matches[1][3]) - 0xa6 + ord('a');
        $char2 = ord($matches[2][3]) - 0xa6 + ord('a');
        $countryCode = chr($char1) . chr($char2);
        return '<img src="https://flagcdn.com/w40/' . $countryCode . '.png" class="flag-icon" style="width: 21px; height: 14px; object-fit: cover; vertical-align: middle; margin: 0 3px; border-radius: 2px; box-shadow: 0 1px 2px rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.15);" alt="' . $countryCode . '">';
    }, $text);

    // 2. Next, handle plain text codes like DEUSTRFI or IR
    $countries = ['DE', 'US', 'TR', 'FI', 'IR', 'NL', 'GB', 'FR', 'CA', 'SG', 'AE', 'PL', 'RU', 'UA', 'CN', 'JP', 'KR', 'IN', 'AU', 'NZ', 'BR', 'ZA'];
    $pattern = '/\b(' . implode('|', $countries) . ')+\b/';
    $text = preg_replace_callback($pattern, function($matches) use ($countries) {
        $word = $matches[0];
        $chunks = str_split($word, 2);
        $html = '';
        foreach ($chunks as $chunk) {
            if (in_array($chunk, $countries)) {
                $code = strtolower($chunk);
                $html .= '<img src="https://flagcdn.com/w40/' . $code . '.png" class="flag-icon" style="width: 21px; height: 14px; object-fit: cover; vertical-align: middle; margin: 0 3px; border-radius: 2px; box-shadow: 0 1px 2px rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.15);" alt="' . $code . '">';
            } else {
                $html .= $chunk;
            }
        }
        return $html;
    }, $text);

    return $text;
}
function require_auth(): void
{
    if (session_status() === PHP_SESSION_NONE)
        session_start();
    global $pdo;
    if (empty($_SESSION['admin_user'])) {
        http_response_code(401);
        if (isset($_SERVER['HTTP_HX_REQUEST'])) {
            header('HX-Redirect: login.php');
        } else {
            header('Location: login.php');
        }
        exit;
    }
    try {
        $admin = db_fetch($pdo, "SELECT id_admin FROM admin WHERE username = ?", [$_SESSION['admin_user']]);
        if (!$admin) {
            session_destroy();
            http_response_code(401);
            if (isset($_SERVER['HTTP_HX_REQUEST'])) {
                header('HX-Redirect: login.php');
            } else {
                header('Location: login.php');
            }
            exit;
        }
    } catch (Exception $e) {
        session_destroy();
        http_response_code(401);
        if (isset($_SERVER['HTTP_HX_REQUEST'])) {
            header('HX-Redirect: login.php');
        } else {
            header('Location: login.php');
        }
        exit;
    }
}

function csrf_token(): string
{
    if (session_status() === PHP_SESSION_NONE)
        session_start();
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
    }
    return $_SESSION['csrf'];
}

function csrf_check_post(): void
{
    global $textbotlang;
    $token = $_POST['_csrf'] ?? '';
    if (!hash_equals($_SESSION['csrf'] ?? '', $token)) {
        http_response_code(403);
        die($textbotlang['panel']['configInvalidRequest']);
    }
}

function csrf_check_get(): void
{
    global $textbotlang;
    $token = $_GET['_csrf'] ?? '';
    if (!hash_equals($_SESSION['csrf'] ?? '', $token)) {
        http_response_code(403);
        die($textbotlang['panel']['configInvalidRequest']);
    }
}

function flash(string $key, string $msg): void
{
    if (session_status() === PHP_SESSION_NONE)
        session_start();
    $_SESSION["flash_{$key}"] = $msg;
}

function get_flash(string $key): ?string
{
    if (session_status() === PHP_SESSION_NONE)
        session_start();
    $msg = $_SESSION["flash_{$key}"] ?? null;
    unset($_SESSION["flash_{$key}"]);
    return $msg;
}

function trunc(string $str, int $max = 30): string
{
    return mb_strlen($str, 'UTF-8') > $max
        ? mb_substr($str, 0, $max, 'UTF-8') . '…'
        : $str;
}

function safe_date($ts, string $fmt = 'Y/m/d'): string
{
    if (!$ts)
        return '—';
    if (!is_numeric($ts)) {
        $parsed = strtotime($ts);
        if ($parsed !== false && $parsed > 0) {
            return date($fmt, $parsed);
        }
        return htmlspecialchars((string) $ts);
    }
    return date($fmt, (int) $ts);
}
function check_login_rate(string $ip): bool
{
    $file = sys_get_temp_dir() . '/panel_login_' . md5($ip);
    $data = @json_decode(@file_get_contents($file) ?: '{}', true) ?: [];
    $now = time();
    $data = array_filter($data, fn($t) => ($now - $t) < 900);
    if (count($data) >= 10)
        return false;
    $data[] = $now;
    @file_put_contents($file, json_encode(array_values($data)), LOCK_EX);
    return true;
}

function clear_login_rate(string $ip): void
{
    @unlink(sys_get_temp_dir() . '/panel_login_' . md5($ip));
}

function user_role_label(string $agent): string
{
    global $textbotlang;
    return match ($agent) {
        'n' => $textbotlang['panel']['configRoleN'],
        'n2' => $textbotlang['panel']['configRoleN2'],
        'all' => $textbotlang['panel']['configRoleAll'],
        default => $textbotlang['panel']['configRoleDefault'],
    };
}

function user_role_tag(string $agent): string
{
    return match ($agent) {
        'f' => 'tag-info',
        'n' => 'tag-info',
        'n2' => 'tag-warn',
        'all' => 'tag-ok',
        default => 'tag-plain',
    };
}

function eng_num($str): string
{
    $persian = ['۰', '۱', '۲', '۳', '۴', '۵', '۶', '۷', '۸', '۹'];
    $arabic  = ['٠', '١', '٢', '٣', '٤', '٥', '٦', '٧', '٨', '٩'];
    $english = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'];
    return str_replace($persian, $english, str_replace($arabic, $english, (string)$str));
}

