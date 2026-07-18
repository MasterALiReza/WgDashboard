<?php
// Buffer all output
ob_start();
require '../inc/config.php';
require_once '../inc/icons.php';
ob_end_clean();

require_auth();
csrf_check_get();

$old_cwd = getcwd();
chdir(__DIR__ . '/../../');
require_once 'function.php';
require_once 'panels.php';
chdir($old_cwd);

$id_invoice = $_GET['id_invoice'] ?? '';
$id_user = (int)($_GET['id_user'] ?? 0);

if (empty($id_invoice) || !$id_user) {
    http_response_code(400);
    echo '<div style="padding:20px;text-align:center;color:var(--no);">درخواست نامعتبر است.</div>';
    exit;
}

try {
    $invoice = db_fetch($pdo, "SELECT * FROM invoice WHERE id_invoice = ? AND id_user = ?", [$id_invoice, $id_user]);
    if (!$invoice) {
        http_response_code(404);
        echo '<div style="padding:20px;text-align:center;color:var(--no);">سفارش مورد نظر یافت نشد.</div>';
        exit;
    }

    $panelInfo = db_fetch($pdo, "SELECT type FROM marzban_panel WHERE name_panel = ?", [$invoice['Service_location']]);
    $panelType = $panelInfo['type'] ?? '';

    $ManagePanel = new ManagePanel();
    $DataUserOut = $ManagePanel->DataUser($invoice['Service_location'], $invoice['username']);

    $invStatus = strtolower($invoice['Status'] ?? '');
    $isUnprovisioned = in_array($invStatus, ['unpaid', 'paying', 'pending', 'send_on_hold', 'cancled', 'canceled', 'waiting']);

    if (isset($DataUserOut['status']) && $DataUserOut['status'] == "Unsuccessful") {
        $err_msg = !empty($DataUserOut['msg']) ? htmlspecialchars(is_string($DataUserOut['msg']) ? $DataUserOut['msg'] : json_encode($DataUserOut['msg'])) : "نامشخص";
        
        if ($isUnprovisioned) {
            // Unpaid or Pending Orders UI
            echo '<div style="padding: 30px 20px; text-align: center; color: var(--text);">
                    <div style="margin-bottom: 20px;">
                        <span style="font-size: 3rem; color: var(--warn); opacity: 0.9;">' . icon('clock', 48) . '</span>
                    </div>
                    <h3 style="margin-bottom: 12px;">سفارش تکمیل نشده است</h3>
                    <p style="opacity: 0.8; margin-bottom: 24px; font-size: 0.95em;">این سفارش هنوز در وضعیت <strong>«' . htmlspecialchars($invStatus) . '»</strong> قرار دارد، بنابراین اطلاعات آن به سمت سرور ارسال نشده و کانفیگ ساخته نشده است.</p>
                    
                    <div style="background: var(--sf); border: 1px solid var(--bd); border-radius: 12px; padding: 16px; text-align: right; margin-bottom: 24px;">
                        <div style="font-weight: 600; margin-bottom: 10px;">جزئیات سفارش ثبت شده در ربات:</div>
                        <div style="font-size: 0.9em; opacity: 0.8; margin-bottom: 6px;">شناسه فاکتور: ' . htmlspecialchars($invoice['id_invoice']) . '</div>
                        <div style="font-size: 0.9em; opacity: 0.8; margin-bottom: 6px;">محصول: ' . htmlspecialchars($invoice['name_product']) . '</div>
                        <div style="font-size: 0.9em; opacity: 0.8;">مبلغ: ' . number_format((int)$invoice['price_product']) . ' تومان</div>
                    </div>
                    
                    <div>
                        <a href="user_action.php?action=removeservice&id_invoice=' . $id_invoice . '&id=' . $id_user . '&_csrf=' . csrf_token() . '&back=user.php" class="btn btn-no" style="width: 100%; justify-content: center;" data-confirm="آیا از حذف این فاکتور مطمئن هستید؟">
                            ' . icon('trash', 14) . ' حذف فاکتور
                        </a>
                    </div>
                  </div>';
        } else {
            // Created order but deleted from Marzban manually (Error State)
            echo '<div style="padding: 30px 20px; text-align: center; color: var(--text);">
                    <div style="margin-bottom: 20px;">
                        <span style="font-size: 3rem; color: var(--red); opacity: 0.9;">' . icon('alert-triangle', 48) . '</span>
                    </div>
                    <h3 style="margin-bottom: 12px;">خطا در یافتن سرویس</h3>
                    <p style="opacity: 0.8; margin-bottom: 10px; font-size: 0.95em;">کاربر در پنل یافت نشد یا سرور در دسترس نیست.</p>
                    <p style="font-size: 0.8em; opacity: 0.6; word-break: break-all; margin-bottom: 24px;">جزئیات خطا: ' . $err_msg . '</p>
                    
                    <div style="background: var(--sf); border: 1px solid var(--bd); border-radius: 12px; padding: 16px; text-align: right; margin-bottom: 24px;">
                        <div style="font-weight: 600; margin-bottom: 10px;">وضعیت فاکتور در ربات:</div>
                        <div style="font-size: 0.9em; opacity: 0.8;">' . htmlspecialchars($invStatus) . '</div>
                    </div>
                    
                    <div>
                        <a href="user_action.php?action=removeservice&id_invoice=' . $id_invoice . '&id=' . $id_user . '&_csrf=' . csrf_token() . '&back=user.php" class="btn btn-no" style="width: 100%; justify-content: center;" data-confirm="آیا می‌خواهید این فاکتور خراب را از ربات حذف کنید؟">
                            ' . icon('trash', 14) . ' پاکسازی فاکتور
                        </a>
                    </div>
                  </div>';
        }
        exit;
    }

    // Prepare status translation
    $statusMap = [
        'active' => ['tag-ok', $textbotlang['users']['status']['active'] ?? 'فعال'],
        'limited' => ['tag-warn', $textbotlang['users']['status']['limited'] ?? 'محدود شده'],
        'disabled' => ['tag-no', $textbotlang['users']['status']['disabled'] ?? 'غیرفعال'],
        'expired' => ['tag-no', $textbotlang['users']['status']['expired'] ?? 'منقضی شده'],
        'on_hold' => ['tag-plain', $textbotlang['users']['status']['on_hold'] ?? 'در انتظار'],
        'Unknown' => ['tag-plain', $textbotlang['users']['status']['unknown'] ?? 'نامشخص'],
        'deactivev' => ['tag-no', $textbotlang['users']['status']['disabled'] ?? 'غیرفعال'],
    ];

    $panelStatus = $DataUserOut['status'] ?? 'Unknown';
    $statusText = $statusMap[$panelStatus][1] ?? $statusMap['Unknown'][1];
    
    // Remove checkmark emoji from status text for cleaner UI in the web panel
    $statusText = trim(str_replace(['✅', '☑️', '✔', '🟢', '🔴'], '', $statusText));

    $statusClass = $statusMap[$panelStatus][0] ?? $statusMap['Unknown'][0];

    // Dates & JDF
    require_once __DIR__ . '/../../jdf.php';
    
    // Initial Purchase Date
    $purchaseDate = !empty($invoice['time_sell']) ? jdate('Y/m/d H:i', $invoice['time_sell']) : 'نامشخص';
    
    // Expiration date
    $expirationDate = !empty($DataUserOut['expire']) ? jdate('Y/m/d', $DataUserOut['expire']) : 'نامحدود';
    $timeDiff = !empty($DataUserOut['expire']) ? $DataUserOut['expire'] - time() : 0;
    $daysRemaining = $timeDiff > 0 ? floor($timeDiff / 86400) . ' روز' : 'منقضی / نامحدود';

    // Last Online
    $lastonline = 'نامشخص';
    if (isset($DataUserOut['online_at'])) {
        if ($DataUserOut['online_at'] == "online") {
            $lastonline = 'متصل 🟢';
        } elseif ($DataUserOut['online_at'] == "offline") {
            $lastonline = 'آفلاین 🔴';
        } elseif ($DataUserOut['online_at'] !== null) {
            $lastonline = jdate('Y/m/d H:i', strtotime($DataUserOut['online_at']));
        }
    }

    // Sub link updates
    $lastupdate = 'بروزرسانی نشده';
    if (!empty($DataUserOut['sub_updated_at'])) {
        $dateTime = new DateTime($DataUserOut['sub_updated_at'], new DateTimeZone('UTC'));
        $dateTime->setTimezone(new DateTimeZone('Asia/Tehran'));
        $lastupdate = jdate('Y/m/d H:i:s', $dateTime->getTimestamp());
    }

    // Traffic Formatting
    function format_bytes_fa($bytes) {
        if ($bytes <= 0) return '۰ مگابایت';
        $mb = $bytes / pow(1024, 2);
        if ($mb < 1024) {
            return round($mb, 1) . ' مگابایت';
        } else {
            $gb = $bytes / pow(1024, 3);
            return round($gb, 2) . ' گیگابایت';
        }
    }

    $limitValue = (float)($DataUserOut['data_limit'] ?? 0);
    $usedTrafficValue = (float)($DataUserOut['used_traffic'] ?? 0);
    $remainingBytes = $limitValue - $usedTrafficValue;

    $LastTraffic = $limitValue > 0 ? format_bytes_fa($limitValue) : 'نامحدود';
    $RemainingVolume = $limitValue > 0 ? format_bytes_fa($remainingBytes) : 'نامحدود';
    $usedTrafficGb = $usedTrafficValue > 0 ? format_bytes_fa($usedTrafficValue) : 'مصرف نشده';

    // Percentage Calculation
    $remainingPercent = 100;
    if ($limitValue > 0) {
        $remainingPercent = (($limitValue - $usedTrafficValue) * 100) / $limitValue;
        if ($remainingPercent < 0) $remainingPercent = 0;
        $remainingPercent = round($remainingPercent, 2);
    }
    
    // Consumed percentage for progress bar
    $usedPercent = 100 - $remainingPercent;
    if ($usedPercent < 0) $usedPercent = 0;
    if ($usedPercent > 100) $usedPercent = 100;

    $subUrl = $DataUserOut['subscription_url'] ?? '';
    $configLinks = $DataUserOut['links'] ?? [];

    // FIX FOR WGDashboard: Often the "subscription_url" actually contains the raw WireGuard .conf content
    if (!empty($subUrl) && (stripos(trim($subUrl), '[Interface]') === 0 || stripos(trim($subUrl), 'PrivateKey') !== false)) {
        $configLinks[] = $subUrl;
        $subUrl = '';
    }

?>
<style>
    .service-details {
        direction: rtl;
        text-align: right;
        font-family: inherit;
        display: flex;
        flex-direction: column;
        gap: 16px;
        color: var(--text);
    }
    .bento-grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 12px;
    }
    @media (max-width: 480px) {
        .bento-grid {
            grid-template-columns: 1fr;
        }
    }
    .bento-card {
        background: var(--sf);
        border: 1px solid var(--bd);
        border-radius: 12px;
        padding: 14px 18px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        transition: all 0.2s ease;
        box-shadow: var(--sh);
    }
    .bento-card:hover {
        border-color: var(--bds);
        transform: translateY(-2px);
    }
    .bento-full {
        grid-column: 1 / -1;
    }
    .card-title {
        font-size: 0.82em;
        color: var(--mute);
        margin-bottom: 8px;
        display: flex;
        align-items: center;
        gap: 8px;
        font-weight: 500;
    }
    .card-title svg {
        color: var(--ac);
        opacity: 0.85;
    }
    .card-value {
        font-size: 1.1em;
        font-weight: 600;
        word-break: break-all;
    }
    .card-desc {
        font-size: 0.78em;
        opacity: 0.6;
        margin-top: 6px;
    }
    .config-box {
        background: var(--sf2);
        border: 1px solid var(--bd);
        border-radius: 8px;
        padding: 10px 14px;
        font-family: monospace;
        font-size: 0.85em;
        word-break: break-all;
        max-height: 90px;
        overflow-y: auto;
        color: var(--text2);
        margin-top: 6px;
        direction: ltr;
        text-align: left;
    }
    .btn-grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 10px;
        margin-top: 8px;
    }
    .btn-grid-full {
        grid-column: span 2;
    }
    .btn-sm-action {
        padding: 11px 16px;
        font-size: 0.85em;
        border-radius: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        font-weight: 500;
        cursor: pointer;
        transition: opacity 0.2s, background-color 0.2s;
        border: none;
    }
    .btn-sm-action:hover {
        opacity: 0.95;
    }
    .progress-bar-container {
        background: var(--sf3);
        height: 6px;
        border-radius: 3px;
        overflow: hidden;
        margin-top: 8px;
    }
    .progress-bar-fill {
        height: 100%;
        border-radius: 3px;
        transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
        min-width: 3px;
    }
    /* Collapsible Details Styles */
    details.configs-details {
        margin-top: 10px;
        width: 100%;
    }
    details.configs-details summary {
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 11px 15px;
        background: var(--sf2);
        border: 1px solid var(--bd);
        border-radius: 10px;
        font-size: 0.9em;
        font-weight: 500;
        outline: none;
        user-select: none;
        list-style: none;
        transition: background-color 0.2s;
    }
    details.configs-details summary::-webkit-details-marker {
        display: none;
    }
    details.configs-details summary:hover {
        background: var(--sf3);
    }
    details.configs-details .chevron {
        transition: transform 0.2s ease;
        display: inline-flex;
        align-items: center;
        justify-content: center;
    }
    /* default RTL state: arrow-left (pointing left) */
    details.configs-details[open] .chevron {
        transform: rotate(-90deg); /* point down */
    }
</style>

<div class="service-details">
    
    <div class="bento-grid">
        <!-- Status & Location Card -->
        <div class="bento-card">
            <div>
                <div class="card-title">
                    <?= icon('user', 15) ?>
                    <span>وضعیت و مشخصات عمومی</span>
                </div>
                <div class="card-value" style="display: flex; justify-content: space-between; align-items: center; margin-top: 4px;">
                    <span class="tag <?= $statusClass ?>"><?= $statusText ?></span>
                    <span style="font-size: 0.85em; opacity: 0.9; font-weight: 500;"><?= htmlspecialchars($invoice['Service_location']) ?></span>
                </div>
            </div>
            <div class="card-desc">سفارش: <?= htmlspecialchars($invoice['id_invoice']) ?></div>
        </div>

        <!-- Purchase Info Card -->
        <div class="bento-card">
            <div>
                <div class="card-title">
                    <?= icon('invoice', 15) ?>
                    <span>جزئیات خرید فاکتور</span>
                </div>
                <div class="card-value" style="font-size: 0.95em;">
                    <?= htmlspecialchars($invoice['name_product']) ?>
                </div>
            </div>
            <div class="card-desc">مبلغ پرداختی: <?= number_format((int)$invoice['price_product']) ?> تومان</div>
        </div>

        <!-- Volume Usage Card -->
        <div class="bento-card">
            <div>
                <div class="card-title">
                    <?= icon('chart', 15) ?>
                    <span>حجم مصرفی و باقی‌مانده</span>
                </div>
                <div class="card-value" style="color: var(--text);"><?= $RemainingVolume ?></div>
                <div class="progress-bar-container">
                    <?php
                        // Direct Hex colors for inline styling reliability
                        $pgColor = $usedPercent > 80 ? '#f43f5e' : ($usedPercent > 50 ? '#f59e0b' : '#22c55e');
                    ?>
                    <div class="progress-bar-fill" style="width: <?= $usedPercent ?>%; background: <?= $pgColor ?>;"></div>
                </div>
            </div>
            <div class="card-desc">مصرف شده: <?= $usedTrafficGb ?> از <?= $LastTraffic ?> (<?= round($usedPercent, 1) ?>٪)</div>
        </div>

        <!-- Expiration Card -->
        <div class="bento-card">
            <div>
                <div class="card-title">
                    <?= icon('clock', 15) ?>
                    <span>اعتبار زمانی سرویس</span>
                </div>
                <div class="card-value"><?= $expirationDate ?></div>
            </div>
            <div class="card-desc">زمان باقی‌مانده: <?= $daysRemaining ?> (تاریخ خرید: <?= $purchaseDate ?>)</div>
        </div>

        <!-- Connection Details Card -->
        <div class="bento-card bento-full">
            <div class="card-title">
                <?= icon('activity', 15) ?>
                <span>جزئیات اتصال و به‌روزرسانی</span>
            </div>
            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; font-size: 0.85em; margin-top: 4px;">
                <div>
                    <span style="color: var(--mute);">آخرین اتصال:</span>
                    <div style="font-weight: 500; margin-top: 2px; color: <?= $panelType === 'WGDashboard' ? 'var(--dim)' : 'inherit' ?>;"><?= $panelType === 'WGDashboard' ? 'نامشخص (بدون پشتیبانی)' : $lastonline ?></div>
                </div>
                <div>
                    <span style="color: var(--mute);">بروزرسانی لینک:</span>
                    <div style="font-weight: 500; margin-top: 2px; color: <?= ($lastupdate == 'بروزرسانی نشده' || $panelType === 'WGDashboard') ? 'var(--dim)' : 'inherit' ?>;"><?= $panelType === 'WGDashboard' ? 'ندارد' : $lastupdate ?></div>
                </div>
                <div>
                    <span style="color: var(--mute);">سیستم‌عامل/کلاینت:</span>
                    <?php $ua = $DataUserOut['sub_last_user_agent'] ?? ''; ?>
                    <div style="font-weight: 500; margin-top: 2px; word-break: break-all; color: <?= (empty($ua) || $panelType === 'WGDashboard') ? 'var(--dim)' : 'inherit' ?>;" title="<?= htmlspecialchars($ua) ?>">
                        <?= htmlspecialchars($panelType === 'WGDashboard' ? 'نامشخص' : (empty($ua) ? 'نامشخص (در این پنل ثبت نشده)' : trunc($ua, 22))) ?>
                    </div>
                </div>
            </div>
        </div>

        <!-- Subscription & Configs Card -->
        <div class="bento-card bento-full">
            <div class="card-title">
                <?= icon('link', 15) ?>
                <span>اشتراک و کانفیگ‌ها</span>
            </div>
            
            <?php if ($subUrl): ?>
            <div style="display:flex; flex-direction:column; gap:4px; background: var(--sf2); padding: 10px 14px; border-radius: 8px; margin-bottom: 6px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:0.85em; color:var(--ac); cursor:pointer;" onclick="navigator.clipboard.writeText('<?= htmlspecialchars($subUrl) ?>').then(()=>alert('کپی شد!'))">
                        لینک اشتراک: برای کپی کردن کلیک کنید
                    </span>
                    <button class="btn btn-ghost btn-sm" style="padding: 2px 6px; display:flex; align-items:center; gap:4px;" onclick="navigator.clipboard.writeText('<?= htmlspecialchars($subUrl) ?>').then(()=>alert('کپی شد!'))">
                        <?= icon('copy', 13) ?> کپی
                    </button>
                </div>
                <!-- Subscription QR Code -->
                <details style="margin-top: 4px;">
                    <summary style="font-size: 0.8em; color: var(--ac); cursor: pointer; list-style: none; outline: none; display: inline-flex; align-items: center; gap: 4px;">
                        <?= icon('qr-code', 14) ?> نمایش QR کد لینک اشتراک
                    </summary>
                    <div style="text-align: center; padding: 10px; background: #fff; border-radius: 8px; margin-top: 6px; width: fit-content; margin-left: auto; margin-right: auto;">
                        <img src="https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=<?= urlencode(trim($subUrl)) ?>" alt="QR Code" style="display: block; width: 150px; height: 150px;" />
                    </div>
                </details>
            </div>
            <?php endif; ?>

            <?php if (!empty($configLinks)): ?>
            <div style="margin-top: 10px; display: flex; flex-direction: column; gap: 14px;">
                <?php foreach ($configLinks as $index => $link): if(empty(trim($link))) continue; 
                    $linkClean = trim($link);
                    $isWireguard = (stripos($linkClean, '[Interface]') !== false || stripos($linkClean, 'PrivateKey') !== false);
                ?>
                    <div style="background: var(--sf2); border: 1px solid var(--bd); border-radius: 10px; padding: 14px; display:flex; flex-direction:column; gap:8px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; font-size: 0.9em; font-weight: 500;">
                            <span style="display: flex; align-items: center; gap: 6px;">
                                <?= icon('sliders', 14) ?> کانفیگ <?php if(count($configLinks) > 1) echo '#'.($index + 1); ?> <?= $isWireguard ? '(وایرگارد)' : '' ?>
                            </span>
                            <div style="display:flex; gap:8px;">
                                <?php if ($isWireguard): ?>
                                <a href="data:text/plain;charset=utf-8,<?= rawurlencode($linkClean) ?>" download="<?= htmlspecialchars($invoice['username']) ?>_wg_<?= ($index + 1) ?>.conf" class="btn btn-primary btn-sm" style="display:inline-flex; align-items:center; gap:4px; text-decoration: none;">
                                    <?= icon('download', 14) ?> دانلود کانفیگ
                                </a>
                                <?php endif; ?>
                                <button class="btn btn-ghost btn-sm" style="display:inline-flex; align-items:center; gap:4px;" onclick="navigator.clipboard.writeText('<?= htmlspecialchars($linkClean) ?>').then(()=>alert('کپی شد!'))">
                                    <?= icon('copy', 14) ?> کپی
                                </button>
                            </div>
                        </div>
                        <div class="config-box" style="margin-top: 4px;"><?= htmlspecialchars($linkClean) ?></div>
                        
                        <!-- Config QR Code -->
                        <?php if (!$isWireguard): ?>
                        <details style="margin-top: 6px;">
                            <summary style="font-size: 0.85em; color: var(--ac); cursor: pointer; list-style: none; outline: none; display: inline-flex; align-items: center; gap: 4px;">
                                <?= icon('qr-code', 14) ?> نمایش بارکد (QR Code) کانفیگ
                            </summary>
                            <div style="text-align: center; padding: 10px; background: #fff; border-radius: 8px; margin-top: 6px; width: fit-content; margin-left: auto; margin-right: auto;">
                                <img src="https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=<?= urlencode($linkClean) ?>" alt="QR Code" style="display: block; width: 150px; height: 150px;" />
                            </div>
                        </details>
                        <?php else: ?>
                        <!-- prominent QR code for WGDashboard without dropdown -->
                        <div style="margin-top: 10px; border-top: 1px solid var(--bd); padding-top: 10px; text-align: center;">
                            <span style="font-size: 0.8em; color: var(--mute); display: block; margin-bottom: 8px;"><?= icon('qr-code', 13) ?> بارکد کانفیگ جهت اسکن در گوشی</span>
                            <div style="padding: 10px; background: #fff; border-radius: 8px; width: fit-content; margin-left: auto; margin-right: auto; box-shadow: var(--sh);">
                                <img src="https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=<?= urlencode($linkClean) ?>" alt="QR Code" style="display: block; width: 150px; height: 150px;" />
                            </div>
                        </div>
                        <?php endif; ?>
                    </div>
                <?php endforeach; ?>
            </div>
            <?php else: ?>
            <div style="font-size: 0.8em; color: var(--mute); text-align: center; margin-top: 8px;">هیچ کانفیگی یافت نشد.</div>
            <?php endif; ?>
        </div>
    </div>

    <div class="btn-grid">
        <?php $csrf = csrf_token(); ?>

        <!-- Refresh (AJAX) -->
        <button type="button" class="btn-sm-action btn-grid-full" style="background: var(--sf3); color: var(--text); border: 1px solid var(--bds);" onclick="manageService('<?= $id_invoice ?>')">
            <?= icon('refresh-cw', 13) ?> بروزرسانی اطلاعات لحظه‌ای
        </button>

        <!-- Toggle Status -->
        <?php if ($panelStatus == "active" || $panelStatus == "limited"): ?>
        <a href="user_action.php?action=toggle_status&id=<?= $id_user ?>&id_invoice=<?= urlencode($id_invoice) ?>&_csrf=<?= $csrf ?>&back=user.php" 
           class="btn-sm-action" style="background:#f43f5e; color:#fff; text-decoration:none;" 
           data-confirm="آیا از غیرفعال کردن (خاموش کردن) این اکانت مطمئن هستید؟" hx-boost="false">
           <?= icon('block', 13) ?> خاموش کردن اکانت
        </a>
        <?php else: ?>
        <a href="user_action.php?action=toggle_status&id=<?= $id_user ?>&id_invoice=<?= urlencode($id_invoice) ?>&_csrf=<?= $csrf ?>&back=user.php" 
           class="btn-sm-action" style="background:#22c55e; color:#fff; text-decoration:none;" 
           data-confirm="آیا از فعال کردن (روشن کردن) این اکانت مطمئن هستید؟" hx-boost="false">
           <?= icon('check', 13) ?> روشن کردن اکانت
        </a>
        <?php endif; ?>

        <!-- Extend Service -->
        <a href="user_action.php?action=extendservice&id=<?= $id_user ?>&id_invoice=<?= urlencode($id_invoice) ?>&_csrf=<?= $csrf ?>&back=user.php" 
           class="btn-sm-action" style="background:#3b82f6; color:#fff; text-decoration:none;" 
           data-confirm="آیا مایلید این سرویس را با حجم <?= $invoice['Volume'] ?> گیگابایت و زمان <?= $invoice['Service_time'] ?> روز تمدید رایگان کنید؟" hx-boost="false">
           <?= icon('plus', 13) ?> تمدید سرویس
        </a>

        <!-- Remove Service (No Refund) -->
        <a href="user_action.php?action=removeservice&id=<?= $id_user ?>&id_invoice=<?= urlencode($id_invoice) ?>&_csrf=<?= $csrf ?>&back=user.php" 
           class="btn-sm-action" style="background:rgba(244, 63, 94, 0.15); color:#f43f5e; border: 1px solid #f43f5e; text-decoration:none;" 
           data-confirm="آیا از حذف کامل این سرویس مطمئن هستید؟ این کار غیرقابل بازگشت است و وجهی بازگردانده نمی‌شود." hx-boost="false">
           <?= icon('trash', 13) ?> حذف کامل سرویس
        </a>
        
        <!-- Remove Service (Refund) -->
        <a href="user_action.php?action=removeserviceandrefund&id=<?= $id_user ?>&id_invoice=<?= urlencode($id_invoice) ?>&_csrf=<?= $csrf ?>&back=user.php" 
           class="btn-sm-action" style="background:rgba(245, 158, 11, 0.15); color:#f59e0b; border: 1px solid #f59e0b; text-decoration:none;" 
           data-confirm="آیا مطمئن هستید؟ کاربر حذف شده و مبلغ <?= number_format((int)($invoice['price_product'] ?? 0)) ?> تومان به کیف پول او برگشت داده می‌شود." hx-boost="false">
           <?= icon('refresh-cw', 13) ?> حذف سفارش و بازگشت وجه
        </a>
    </div>

</div>

<?php
} catch (Exception $e) {
    http_response_code(500);
    echo '<div style="padding:20px;text-align:center;color:var(--no); direction: rtl;">خطای سرور: ' . htmlspecialchars($e->getMessage()) . '</div>';
}
