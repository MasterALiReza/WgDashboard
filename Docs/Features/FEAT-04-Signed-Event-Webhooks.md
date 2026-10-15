# FEAT-04 — Webhookهای امضاشده برای رویدادها

| | |
|---|---|
| اولویت | ⭐⭐ ارزش بالا |
| حجم کار | S-M (۱ روز پنل، نیم روز ربات) |
| حل می‌کند | polling سنگین کرون‌های ربات ([BOT-02](../06-OxBot-Integration.md#bot-02))؛ اطلاع‌رسانی فوری به کاربر؛ دیده شدن خطاهای پنهان (BUG-02, BUG-05، شکست بکاپ) |
| وابستگی | [FEAT-02](FEAT-02-Peer-Limits.md) برای رویدادهای محدودیت (بقیه‌ی رویدادها مستقل‌اند) |

## ۱. مسئله
- ربات برای فهمیدن «حجم کاربر ۸۰٪ شد» یا «سرویسش تمام شد»، دوره‌ای همه‌ی کاربران را از پنل می‌کشد.
- زیرساخت webhook در پنل وجود دارد ([DashboardWebHooks.py](../../src/modules/DashboardWebHooks.py)) ولی فقط سه رویداد دارد (`peer_created`، `peer_deleted`، `peer_updated` — [DashboardWebHooks.py:15](../../src/modules/DashboardWebHooks.py#L15))، **امضا ندارد** (هر کسی که آدرس webhook ربات را بداند می‌تواند رویداد جعلی بفرستد) و فیلتر بر اساس اینترفیس ندارد (ربات دوستتان رویدادهای اینترفیس شما را هم می‌گیرد).

## ۲. اهداف
1. کاتالوگ رویدادهای کامل (peer، محدودیت، job، بکاپ، اینترفیس، سیستم).
2. امضای **HMAC-SHA256** با timestamp برای جلوگیری از جعل و replay.
3. فیلتر هر webhook بر اساس لیست اینترفیس‌ها و رویدادها.
4. تحویل **حداقل یک‌بار** (at-least-once) با شناسه‌ی یکتا برای حذف تکرار در گیرنده.
5. حفظ سازگاری کامل با webhookهای فعلی.

## ۳. غیر اهداف
- تضمین ترتیب رویدادها (ترتیب تضمین نمی‌شود؛ گیرنده باید idempotent باشد).
- صف پایدار خارجی (Redis/RabbitMQ) — برای مقیاس فعلی لازم نیست.

## ۴. طراحی

### ۴.۱ تغییر دیتابیس — ستون‌های جدید `DashboardWebHooks`

| ستون | نوع | پیش‌فرض | توضیح |
|------|------|---------|--------|
| `Secret` | String(64) | تصادفی (`secrets.token_hex(32)`) | کلید امضا |
| `PayloadVersion` | Integer | `1` | `1` = قالب فعلی؛ `2` = پاکت جدید |
| `Configurations` | JSON | `[]` | خالی = همه‌ی اینترفیس‌ها |
| `OwnerKeyID` | String(64), NULL | | اگر webhook را یک API Key ساخته (FEAT-05)، فقط رویداد اینترفیس‌های مجاز آن کلید |

### ۴.۲ کاتالوگ رویدادها

| رویداد (v2) | معادل قدیمی | منبع | `data` |
|-------------|-------------|------|--------|
| `peer.created` | `peer_created` | `addPeers` ([WireguardConfiguration.py:788](../../src/modules/WireguardConfiguration.py#L788))، v2 create | `peer_id, name, allowed_ip` |
| `peer.deleted` | `peer_deleted` | `deletePeers` ([WireguardConfiguration.py:1098](../../src/modules/WireguardConfiguration.py#L1098)) | `peer_id, name, final_usage_bytes` |
| `peer.updated` | `peer_updated` | [dashboard.py:1209](../../src/dashboard.py#L1209)، v2 update | `peer_id, changed_fields[]` |
| `peer.restricted` | — | `restrictPeers` | `peer_id, name, reason, source (limits/job/admin/api), usage_bytes` |
| `peer.allowed` | — | `allowAccessPeers` | `peer_id, name, source` |
| `peer.usage_reset` | — | `resetDataUsage` | `peer_id, previous_usage_bytes` |
| `peer.limit_warning.volume` | — | FEAT-02 | `peer_id, usage_bytes, limit_bytes, percent` |
| `peer.limit_warning.time` | — | FEAT-02 | `peer_id, expire_at, remaining_seconds` |
| `peer.limits_changed` | — | FEAT-02 | `peer_id, before, after` |
| `peer.activated` | — | FEAT-02 (On-Hold) | `peer_id, activated_at, expire_at` |
| `peer.resumed` | — | FEAT-02 (Auto-Resume) | `peer_id, previous_reason` |
| `peer.rekeyed` | — | [FEAT-14](FEAT-14-Interface-Key-Rotation.md) (Hard Rotate) | `peer_id_old, peer_id_new` |
| `interface.usage_anomaly` | — | [FEAT-07](FEAT-07-Interface-Usage-History.md) | `rx_delta_bytes, tx_delta_bytes` (منفی) |
| `peer.sharing_suspected` | — | [FEAT-11](FEAT-11-Account-Sharing-Detection.md) | `peer_id, distinct_endpoints, window_seconds` |
| `peer.migrated` | — | [FEAT-14](FEAT-14-Interface-Key-Rotation.md) | `old_peer_id, new_peer_id, new_configuration` |
| `job.failed` | — | `PeerJobs.runJob` | `job_id, peer_id, field, error` |
| `backup.completed` / `backup.failed` | — | `GlobalBackup` | `filename, size_bytes` / `error` |
| `interface.up` / `interface.down` | — | `toggleConfiguration` + تشخیص در thread #1 | `expected (true/false)` |
| `system.maintenance_started` / `_finished` | — | ریستور | |
| `system.db_error` | — | خطاهای دیتابیس/self-heal | `database, error` |

### ۴.۳ پاکت پیام (PayloadVersion = 2)
```json
{
  "id": "5d0c1e1e-5a1b-4a4e-9e7e-3a1f5c7b9d21",
  "event": "peer.restricted",
  "occurred_at": "2026-10-14T18:30:05Z",
  "configuration": "wg0",
  "revision": 48214,
  "data": {
    "peer_id": "RvKt…31k=", "name": "1721341_egproxy_100",
    "reason": "Volume Limit Reached", "source": "limits",
    "usage_bytes": 10737418240, "limit_bytes": 10737418240
  }
}
```
برای `PayloadVersion = 1` قالب فعلی (`action`, `configuration`, `peers`, `time`, ...) **دقیقاً همان** می‌ماند و رویدادهای جدید برای آن ارسال نمی‌شوند.

### ۴.۴ امضا
هدرها:
```
X-WGD-Event:      peer.restricted
X-WGD-Delivery:   <id>
X-WGD-Timestamp:  1760466605
X-WGD-Signature:  v1=<hex(HMAC_SHA256(Secret, Timestamp + "." + RawBody))>
```
```python
ts = str(int(time.time()))
sig = hmac.new(webhook.Secret.encode(), (ts + "." + body).encode(), hashlib.sha256).hexdigest()
headers.update({"X-WGD-Timestamp": ts, "X-WGD-Signature": "v1=" + sig, ...})
```
در هر تلاش مجدد timestamp و امضا دوباره ساخته می‌شوند؛ `id` ثابت می‌ماند.

### ۴.۵ ارسال
- از `ThreadPoolExecutor` فعلی (۵ worker) و retry نمایی فعلی (۲، ۴، ۸، ۱۶ ثانیه) استفاده می‌شود ([DashboardWebHooks.py:260-297](../../src/modules/DashboardWebHooks.py#L260-L297)).
- لیست webhookها در حافظه کش می‌شود و فقط با `UpdateWebHook`/`DeleteWebHook` تازه می‌شود (الان در هر `RunWebHook` از دیتابیس خوانده می‌شود: [DashboardWebHooks.py:194](../../src/modules/DashboardWebHooks.py#L194)).
- `RunWebHook` باید از threadهای پس‌زمینه هم کار کند؛ برای لاگ به‌جای `current_app.logger` از logger مستقل از context استفاده شود (مثل `_ContextSafeLoggerProxy` در [WireguardConfiguration.py:17-26](../../src/modules/WireguardConfiguration.py#L17-L26)).
- **از دست رفتن:** اگر پروسه وسط retry ری‌استارت شود، تحویل از دست می‌رود (sessionهای ناتمام هنگام boot با `Status=2` علامت می‌خورند — [DashboardWebHooks.py:88-96](../../src/modules/DashboardWebHooks.py#L88-L96)). برای جبران: endpoint «ارسال مجدد ناموفق‌ها» و یک کرون ساعتی سبک در ربات با [FEAT-01](FEAT-01-Lightweight-Bot-API.md) `peers/usage` به‌عنوان تور ایمنی.

### ۴.۶ فیلتر
رویداد به webhook ارسال می‌شود اگر: `IsActive` و رویداد در `SubscribedActions` و (`Configurations` خالی یا شامل اینترفیس رویداد) و (اگر `OwnerKeyID` دارد، اینترفیس در scope آن کلید باشد).

## ۵. API
endpointهای فعلی (`/api/webHooks/...`) با فیلدهای جدید گسترش می‌یابند، به‌علاوه:
```http
POST /api/v2/webhooks/rotate-secret      (admin یا webhooks:manage)   { "WebHookID": "…" }   → { "Secret": "…" } (فقط یک بار نمایش)
POST /api/v2/webhooks/test               { "WebHookID": "…", "event": "peer.restricted" }  → ارسال رویداد آزمایشی با data نمونه
POST /api/v2/webhooks/redeliver          { "WebHookID": "…", "since": "2026-10-14T00:00:00Z" }  → ارسال مجدد sessionهای ناموفق
```

## ۶. امنیت
- **SSRF:** ساخت webhook فقط با scope `admin` یا `webhooks:manage`. به‌طور پیش‌فرض فقط `https://`؛ آدرس‌های خصوصی/loopback فقط اگر در تنظیم `[WebHooks] allow_private_targets` باشند (اگر ربات روی همین سرور است، `127.0.0.1` را صریحاً مجاز کنید).
- `Secret` هرگز در لیست webhookها برگردانده نمی‌شود (فقط هنگام ساخت/چرخش).
- پیام‌ها کلید خصوصی یا PSK ندارند.
- Timeout همان ۱۰ ثانیه؛ پاسخ‌های بزرگ‌تر از ۶۴ کیلوبایت در لاگ کوتاه می‌شوند.

## ۷. سازگاری
webhookهای موجود `PayloadVersion = 1` می‌گیرند و هیچ تغییری حس نمی‌کنند. امضا برای v1 هم اضافه می‌شود (هدر اضافه، بی‌ضرر).

## ۸. تغییرات ربات OxBot
### ۸.۱ دیتابیس ربات
```sql
ALTER TABLE invoice ADD COLUMN wg_public_key VARCHAR(64) NULL, ADD INDEX idx_wg_pk (wg_public_key);
UPDATE invoice SET wg_public_key = JSON_UNQUOTE(JSON_EXTRACT(user_info, '$.public_key'))
 WHERE user_info IS NOT NULL AND JSON_VALID(user_info);
CREATE TABLE wgd_webhook_deliveries (delivery_id CHAR(36) PRIMARY KEY, received_at DATETIME NOT NULL);
```
### ۸.۲ گیرنده (`wgd_webhook.php`)
```php
<?php
require 'config.php';
$body = file_get_contents('php://input');
$ts   = $_SERVER['HTTP_X_WGD_TIMESTAMP'] ?? '';
$sig  = $_SERVER['HTTP_X_WGD_SIGNATURE'] ?? '';
if (!ctype_digit($ts) || abs(time() - (int)$ts) > 300) { http_response_code(400); exit; }
$expected = 'v1=' . hash_hmac('sha256', $ts . '.' . $body, $WGD_WEBHOOK_SECRET);
if (!hash_equals($expected, $sig)) { http_response_code(401); exit; }

$evt = json_decode($body, true);
$ins = $pdo->prepare("INSERT IGNORE INTO wgd_webhook_deliveries (delivery_id, received_at) VALUES (?, NOW())");
$ins->execute([$evt['id']]);
if ($ins->rowCount() === 0) { http_response_code(200); exit; }        // تکراری

$st = $pdo->prepare("SELECT * FROM invoice WHERE wg_public_key = ? LIMIT 1");
$st->execute([$evt['data']['peer_id'] ?? '']);
$inv = $st->fetch(PDO::FETCH_ASSOC);
http_response_code(200);                                               // پاسخ سریع؛ کار سنگین بعدش
if (function_exists('fastcgi_finish_request')) fastcgi_finish_request();
if (!$inv) exit;

switch ($evt['event']) {
    case 'peer.limit_warning.volume':
        sendmessage($inv['id_user'], "⚠️ {$evt['data']['percent']}٪ حجم سرویس {$inv['username']} مصرف شد.", null, 'HTML');
        break;
    case 'peer.limit_warning.time':
        sendmessage($inv['id_user'], "⏳ سرویس {$inv['username']} به‌زودی منقضی می‌شود.", null, 'HTML');
        break;
    case 'peer.restricted':
        $map = ['Volume Limit Reached' => 'end_of_volume', 'Time Limit Reached' => 'end_of_time'];
        update("invoice", "Status", $map[$evt['data']['reason']] ?? 'disablebyadmin', "id_invoice", $inv['id_invoice']);
        break;
    case 'peer.allowed':
        update("invoice", "Status", "active", "id_invoice", $inv['id_invoice']);
        break;
}
```
### ۸.۳ کرون‌ها
کرون‌های هشدار حجم/زمان ربات (مثل `volumewarn` و `daywarn`) برای پنل‌های WGDashboard غیرفعال یا به «تور ایمنی ساعتی» با `peers/usage` تبدیل می‌شوند.

## ۹. تغییرات UI
صفحه‌ی Webhooks: انتخاب نسخه‌ی پیام، چک‌باکس رویدادها (گروه‌بندی‌شده)، انتخاب اینترفیس‌ها، نمایش/کپی/چرخش Secret، دکمه‌ی «ارسال آزمایشی» و «ارسال مجدد ناموفق‌ها».

## ۱۰. تست
- بردار تست امضا (یک body و secret ثابت ← امضای مشخص) در هر دو طرف Python و PHP.
- فیلتر اینترفیس: webhook با `["wg1"]` رویداد `wg0` را نگیرد.
- v1 دقیقاً همان JSON قبلی را بفرستد (تست snapshot).
- ارسال از thread job ها بدون خطای «outside application context».
- retry: سرور گیرنده‌ی ۵۰۰ ← ۵ تلاش با فاصله‌ی نمایی؛ `id` ثابت.

## ۱۱. استقرار و Rollback
ستون‌ها nullable/پیش‌فرض‌دار هستند. Rollback = غیرفعال کردن webhook v2 در UI.

## ۱۲. فایل‌های درگیر
[DashboardWebHooks.py](../../src/modules/DashboardWebHooks.py)، نقاط انتشار در [WireguardConfiguration.py](../../src/modules/WireguardConfiguration.py)، [PeerJobs.py](../../src/modules/PeerJobs.py)، [GlobalBackup.py](../../src/modules/GlobalBackup.py)، `PeerLimits.py`، کامپوننت‌های `dashboardWebHooksComponents/*.vue`، `tests/test_webhook_signature.py`.
