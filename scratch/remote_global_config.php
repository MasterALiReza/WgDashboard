<?php
// This variable added for high load panels which their response time is long and bot can't communicate with online panel!
// null for default settings
$request_exec_timeout = null;
$dbhost = 'localhost';
$dbname = 'mirzaprobot';
$usernamedb = 'DtkfmSmq';
$passworddb = '7uP8E2aI';
$connect = mysqli_connect($dbhost, $usernamedb, $passworddb, $dbname);
if ($connect->connect_error) { die("error" . $connect->connect_error); }
mysqli_set_charset($connect, "utf8mb4");
$options = [ PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC, PDO::ATTR_EMULATE_PREPARES => false, ];
$dsn = "mysql:host=$dbhost;dbname=$dbname;charset=utf8mb4";
try { $pdo = new PDO($dsn, $usernamedb, $passworddb, $options); } catch (\PDOException $e) { error_log("Database connection failed: " . $e->getMessage()); }
$APIKEY = '8883975569:AAEmwIevmN3xVHrVuXLdwKccAqXMda77-5Q';
$adminnumber = '680561287';
$domainhosts = 'bot.vipvirtualnet.eu';
$usernamebot = 'WexortVPN_bot';
$backup_secure_token = 'SecureBackupToken_f5213f97b01fd615e78e8fac28450c0d';
?>
