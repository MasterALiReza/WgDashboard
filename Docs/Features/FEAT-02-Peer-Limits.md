# FEAT-02 — محدودیت‌های دائمی Peer (پلن: حجم، انقضا، On-Hold)

| | |
|---|---|
| اولویت | ⭐⭐⭐ ضروری |
| حجم کار | M-L (۲-۳ روز پنل، ۱ روز UI، ۱ روز ربات) |
| حل می‌کند | [BOT-03](../06-OxBot-Integration.md#bot-03)، [BOT-04](../06-OxBot-Integration.md#bot-04)، [BOT-05](../06-OxBot-Integration.md#bot-05)، [BOT-06](../06-OxBot-Integration.md#bot-06)، [BOT-07](../06-OxBot-Integration.md#bot-07)، [BUG-02](../03-Backend-Bugs-and-Logic.md#bug-02)، [BUG-05](../03-Backend-Bugs-and-Logic.md#bug-05)، [PERF-06](../07-Performance-and-Stability.md#perf-06) |
| وابستگی | [FEAT-01](FEAT-01-Lightweight-Bot-API.md) (قالب peer)، رفع [BUG-11](../03-Backend-Bugs-and-Logic.md#bug-11) (عملیات روی اینترفیس خاموش) |

## ۱. مسئله
محدودیت‌های فروش (حجم و تاریخ) الان با **job های یک‌بارمصرف** پیاده شده‌اند ([PeerJobs.py](../../src/modules/PeerJobs.py)):
- بعد از اجرا **حذف** می‌شوند؛ پس بعد از اتمام حجم، دیگر «محدودیتی» وجود ندارد. ربات در خرید حجم/زمان اضافه دنبال job می‌گردد، پیدا نمی‌کند و تصمیم غلط می‌گیرد (BOT-03 حجم رایگان، BOT-04 مصرف نامحدود).
- در دیتابیس جدا (`wgdashboard_job`) هستند، پس با ساخت peer در یک تراکنش نمی‌آیند (BOT-05).
- یک job خراب کل حلقه را می‌خواباند (BUG-02) و job های ناموفق بی‌صدا پاک می‌شوند (BUG-05).
- مقدار تاریخ بدون منطقه‌ی زمانی است (BOT-07).
- هر محدودسازی جداگانه `wg-quick save` می‌کند (PERF-06).

## ۲. اهداف
1. محدودیت حجم و تاریخ انقضا به‌عنوان **ویژگی ماندگار** هر peer که هیچ‌وقت خودکار پاک نمی‌شود.
2. اعمال پیوسته (هر ۶۰ ثانیه) و **گروهی** (یک `wg-quick save` برای همه‌ی peerهای یک اینترفیس).
3. **بازگشت خودکار (Auto-Resume):** اگر peer به‌خاطر محدودیت قطع شده و محدودیت افزایش یافت (یا مصرف ریست شد)، خودش وصل شود. اگر هنوز محدودیت رد شده، باز نشود.
4. **On-Hold:** شروع شمارش انقضا از اولین اتصال (مثل تنظیمات `on_hold_day` در Mirza).
5. هشدار پیش از اتمام (درصد حجم / زمان باقی‌مانده) از طریق [FEAT-04](FEAT-04-Signed-Event-Webhooks.md).
6. همه‌ی زمان‌ها UTC.

## ۳. غیر اهداف
- حذف سیستم job فعلی (برای کاربرد دستی در UI می‌ماند؛ فقط ربات دیگر از آن استفاده نمی‌کند).
- محدودیت سرعت (→ [FEAT-10](FEAT-10-Per-Peer-Speed-Limit.md)).

## ۴. طراحی

### ۴.۱ مدل داده — جدول `PeerLimits` (دیتابیس `wgdashboard`)

| ستون | نوع | توضیح |
|------|------|--------|
| `Configuration` | String(255), PK | نام اینترفیس |
| `PeerID` | String(255), PK | public key |
| `DataLimitBytes` | BigInteger, NULL | `NULL` = نامحدود |
| `ExpireAt` | DateTime (UTC), NULL | `NULL` = بدون انقضا |
| `OnHoldSeconds` | Integer, NULL | اگر ست باشد و `ActivatedAt` خالی، `ExpireAt` در اولین handshake محاسبه می‌شود |
| `OnHoldDeadline` | DateTime (UTC), NULL | اگر تا این زمان وصل نشد، شمارش به‌هرحال شروع شود |
| `ActivatedAt` | DateTime (UTC), NULL | زمان فعال‌سازی |
| `State` | String(32) | `active` · `on_hold` · `limited_volume` · `limited_time` |
| `AutoResume` | Boolean, default `true` | |
| `WarnPercent` | Integer, default `80` | `0` = خاموش |
| `WarnBeforeSeconds` | Integer, default `86400` | `0` = خاموش |
| `WarnedVolumeAt` / `WarnedTimeAt` | DateTime, NULL | جلوگیری از هشدار تکراری؛ با تغییر محدودیت خالی می‌شوند |
| `PlanName` | String(255), NULL | برچسب دلخواه (نام محصول ربات) |
| `CreatedAt` / `UpdatedAt` | DateTime (UTC) | |

ایندکس: `(Configuration, State)`.

> چرا در دیتابیس `wgdashboard`؟ تا ساخت/حذف/تغییر نام peer و محدودیتش در **یک تراکنش** انجام شود.

### ۴.۲ منطق ارزیابی (تابع خالص و قابل تست)
```python
@dataclass
class Decision:
    action: str | None          # "restrict" | "resume" | None
    reason: str | None          # "Volume Limit Reached" | "Time Limit Reached"
    new_state: str
    events: list[str]           # "peer.limit_warning.volume", "peer.activated", ...
    activate_at: datetime | None = None

def evaluate(limit: LimitRow, usage_bytes: int, last_handshake: datetime | None,
             restricted: bool, restricted_reason: str | None, now: datetime) -> Decision:
    # 1) فعال‌سازی On-Hold
    if limit.State == "on_hold":
        if last_handshake or (limit.OnHoldDeadline and now >= limit.OnHoldDeadline):
            start = last_handshake or limit.OnHoldDeadline
            return Decision(None, None, "active", ["peer.activated"], activate_at=start)
        return Decision(None, None, "on_hold", [])
    vol_exceeded  = limit.DataLimitBytes is not None and usage_bytes >= limit.DataLimitBytes
    time_exceeded = limit.ExpireAt is not None and now >= limit.ExpireAt
    # 2) محدودسازی
    if not restricted and (vol_exceeded or time_exceeded):
        reason = "Volume Limit Reached" if vol_exceeded else "Time Limit Reached"
        return Decision("restrict", reason, "limited_volume" if vol_exceeded else "limited_time", [])
    # 3) بازگشت خودکار — فقط اگر قطعی به‌خاطر خود محدودیت‌ها بوده
    if restricted and limit.AutoResume and restricted_reason in LIMIT_REASONS \
            and not vol_exceeded and not time_exceeded:
        return Decision("resume", None, "active", ["peer.resumed"])
    # 4) هشدارها
    events = []
    if limit.DataLimitBytes and limit.WarnPercent and not limit.WarnedVolumeAt \
            and usage_bytes >= limit.DataLimitBytes * limit.WarnPercent / 100:
        events.append("peer.limit_warning.volume")
    if limit.ExpireAt and limit.WarnBeforeSeconds and not limit.WarnedTimeAt \
            and (limit.ExpireAt - now).total_seconds() <= limit.WarnBeforeSeconds:
        events.append("peer.limit_warning.time")
    return Decision(None, None, limit.State, events)
```
`LIMIT_REASONS = {"Volume Limit Reached", "Time Limit Reached"}` — همان رشته‌هایی که UI فعلی ترجمه می‌کند. **قطع دستی ادمین (دلیل دیگر) هرگز خودکار باز نمی‌شود.**

### ۴.۳ موتور اعمال — `src/modules/PeerLimits.py`
```python
class PeerLimitsManager:
    def run_once(self):
        for conf in list(WireguardConfigurations.values()):
            try:
                self._run_config(conf)          # خطای یک کانفیگ بقیه را متوقف نمی‌کند
            except Exception as e:
                log.error(...); alerts.emit("limits.engine_error", conf.Name, str(e))
        heartbeat("limits_engine")               # برای FEAT-08

    def _run_config(self, conf):
        conf._force_refresh_stats()             # مصرف دقیق (هر دقیقه یک بار)
        rows = self._load(conf)                 # یک JOIN روی PeerLimits + دو جدول peer
        decisions = [(r, evaluate(r, ...)) for r in rows]
        to_restrict = {}                        # reason -> [ids]
        to_resume = []
        with conf.lock:
            ...                                 # جمع‌آوری
            if to_restrict: conf.restrictPeersBatch(to_restrict)   # یک wg-quick save
            if to_resume:   conf.allowAccessPeers(to_resume)       # یک wg-quick save
            self._update_states(decisions)      # یک تراکنش
        self._emit_events(decisions)            # FEAT-04
```
- اجرا در thread شماره ۲ (کنار job ها) هر **۶۰ ثانیه**، با `try/except` جدا برای هر کانفیگ.
- `restrictPeers` گسترش می‌یابد تا دلیل جداگانه برای هر id بپذیرد (`restrictPeersBatch(dict[reason, list[id]])`).
- اگر اینترفیس خاموش است، طبق رفع BUG-11 فقط دیتابیس و فایل `.conf` تغییر می‌کنند.

### ۴.۴ یکپارچگی با بقیه‌ی سیستم

| رویداد | رفتار |
|--------|--------|
| حذف peer (`deletePeers`) | ردیف `PeerLimits` در همان تراکنش حذف می‌شود |
| تغییر نام کانفیگ | `UPDATE PeerLimits SET Configuration = :new` در همان تراکنش rename |
| تغییر کلید (auto-heal یا FEAT-14) | `UPDATE PeerLimits SET PeerID = :new` |
| ریست مصرف | بعد از ریست، ارزیابی فوری همان peer ← اگر `limited_volume` بود و دیگر نیست، بازگشت خودکار |
| `allowAccessPeers` نسخه‌ی v1 | رفتار تغییر نمی‌کند، ولی اگر محدودیت رد شده باشد، موتور در کمتر از ۶۰ ثانیه دوباره قطع می‌کند (تور ایمنی برای ربات قدیمی) |
| بکاپ per-config | ردیف‌های `PeerLimits` همان کانفیگ در dump و import می‌آیند |
| Global Backup | جدول در همان دیتابیس است و خودکار می‌آید |
| job های قدیمی | کنار هم کار می‌کنند؛ توصیه: برای یک peer فقط یکی از دو روش |

## ۵. API
همه با قالب [قراردادهای مشترک](README.md).

### ۵.۱ خواندن
```http
GET /api/v2/peer/limits?configuration=wg0&id=<pk>          (peers:read)
GET /api/v2/limits?configuration=wg0&state=limited_volume  (peers:read)  — لیست، صفحه‌بندی با cursor
```
```json
{ "status": true, "data": {
  "configuration": "wg0", "id": "RvKt…31k=",
  "data_limit_bytes": 21474836480, "expire_at": "2026-11-13T20:30:00Z",
  "on_hold_seconds": null, "activated_at": "2026-10-14T20:30:00Z",
  "state": "active", "auto_resume": true, "warn_percent": 80, "warn_before_seconds": 86400,
  "plan_name": "گیمینگ ۲۰ گیگ",
  "usage_bytes": 6442450944, "remaining_bytes": 15032385536, "remaining_seconds": 2592000
}}
```

### ۵.۲ تعیین کامل (جایگزینی)
```http
PUT /api/v2/peer/limits        (peers:write)
{ "configuration": "wg0", "id": "<pk>",
  "data_limit_bytes": 21474836480,       // null = نامحدود
  "expire_at": "2026-11-13T20:30:00Z",   // یا epoch؛ null = بدون انقضا
  "on_hold_seconds": null,
  "on_hold_deadline": null,
  "auto_resume": true, "warn_percent": 80, "warn_before_seconds": 86400,
  "plan_name": "گیمینگ ۲۰ گیگ" }
```
پاسخ: همان قالب خواندن به‌علاوه‌ی `"applied": {"restricted": false, "resumed": true}` — ارزیابی **فوری** برای همان peer انجام می‌شود تا ربات بلافاصله نتیجه را ببیند.

اگر `on_hold_seconds` ست و `expire_at` خالی باشد ← `state = on_hold`.

### ۵.۳ افزایش (حجم/زمان اضافه)
```http
POST /api/v2/peer/limits/add     (peers:write)
{ "configuration": "wg0", "id": "<pk>",
  "add_data_bytes": 5368709120,
  "add_seconds": 0,
  "time_base": "auto" }          // auto | now | expire_at
```
- حجم: `DataLimitBytes += add`. اگر peer نامحدود است ← `VALIDATION_ERROR` (افزودن به نامحدود بی‌معناست).
- زمان با `time_base = auto`: اگر منقضی شده یا `ExpireAt` خالی است، از **الان**؛ وگرنه از `ExpireAt` فعلی (همان منطق `extra_time` در Mirza).
- مصرف **ریست نمی‌شود** (اصلاح BOT-03).
- پاسخ شامل `applied.resumed`: فقط وقتی هیچ محدودیتی رد نشده باشد `true` است (اصلاح BOT-04).

### ۵.۴ حذف محدودیت
```http
DELETE /api/v2/peer/limits?configuration=wg0&id=<pk>     (peers:write)
```

### ۵.۵ مهاجرت از job ها
```http
POST /api/v2/admin/limits/migrate-jobs     (admin)
{ "configuration": "wg0", "dry_run": true, "job_timezone": "Asia/Tehran" }
```
برای هر peer: آخرین job فعال `total_data / lgt / restrict` ← `DataLimitBytes = Value × 1024³`؛ آخرین job فعال `date / lgt / restrict` ← `ExpireAt` (تبدیل از `job_timezone` به UTC). برای peerهای **قبلاً** محدودشده‌ای که job شان اجرا و حذف شده، محدودیت از روی `restricted_reason` قابل بازسازی نیست؛ در گزارش با عنوان «needs_input» فهرست می‌شوند تا ربات مقدار را از فاکتور بدهد. در حالت `dry_run=false`، job های مهاجرت‌شده منقضی می‌شوند.

## ۶. امنیت
- نوشتن فقط با scope `peers:write` روی همان اینترفیس ([FEAT-05](FEAT-05-Scoped-API-Keys.md)).
- اعتبارسنجی: `data_limit_bytes ≥ 0`، حداکثر معقول (مثلاً ۱۰۰ ترابایت)، `expire_at` قابل parse، `warn_percent` بین ۰ و ۱۰۰.
- هر تغییر محدودیت در [Audit Log](FEAT-09-Audit-Log.md) با مقدار قبل/بعد ثبت می‌شود.

## ۷. سازگاری و مهاجرت
1. استقرار با `[Features] peer_limits = false` (جدول ساخته می‌شود، موتور خاموش).
2. اجرای `migrate-jobs` با `dry_run=true` و مقایسه‌ی تعداد با فاکتورهای فعال ربات.
3. روشن کردن موتور؛ اجرای واقعی مهاجرت.
4. آپدیت ربات (بخش ۸).
5. پس از یک هفته بدون مشکل، ربات دیگر `setjob` نمی‌زند.

## ۸. تغییرات ربات OxBot

| تابع ربات | الان | بعد |
|-----------|------|-----|
| خرید (`createUser`) | `setjob` × ۲ (نتیجه بررسی نمی‌شود) | `PUT /peer/limits` (یا یک‌جا در [FEAT-03](FEAT-03-Atomic-Idempotent-Operations.md)) |
| `DataUser` | محاسبه از job ها + fallback فاکتور | خواندن `limits` و `state` از `GET /peer` |
| `extend` (تمدید) | ریست + allow + حذف/ساخت job | [FEAT-03 renew](FEAT-03-Atomic-Idempotent-Operations.md) |
| `extra_volume` | حذف job یا **ریست** (باگ) + ساخت job | `POST /peer/limits/add {add_data_bytes}` |
| `extra_time` | allow بی‌قیدوشرط + job تاریخ | `POST /peer/limits/add {add_seconds}` |
| کرون هشدار حجم/زمان | polling همه‌ی کاربران | webhook `peer.limit_warning.*` ([FEAT-04](FEAT-04-Signed-Event-Webhooks.md)) |

نگاشت وضعیت به `invoice.Status` ربات:

| وضعیت پنل | `Status` ربات |
|-----------|---------------|
| `active` و محدود نیست | `active` |
| `on_hold` | `send_on_hold` / `on_hold` |
| `limited_volume` | `end_of_volume` |
| `limited_time` | `end_of_time` |
| محدود با دلیل دستی | `disablebyadmin` |

نمونه‌ی `extra_volume`:
```php
$r = wg_api($panel['name_panel'], 'POST', '/api/v2/peer/limits/add', [], [
    'configuration' => $panel['inboundid'], 'id' => $publicKey,
    'add_data_bytes' => (int)($limit_volume_new * 1024 ** 3),
]);
if (empty($r['json']['status'])) {
    return ['status' => false, 'msg' => $r['json']['message'] ?? 'panel error'];
}
// اگر $r['json']['data']['applied']['resumed'] === false یعنی زمانش هم تمام شده؛ به کاربر اطلاع بده.
```

## ۹. تغییرات UI
- تب جدید **Limits** در مودال تنظیمات peer: حجم (GB)، تاریخ انقضا (date-picker با نمایش زمان محلی)، On-Hold (روز)، آستانه‌ی هشدار، Auto-Resume، نام پلن.
- روی کارت peer: نوار پیشرفت «مصرف/سقف» و نشان «X روز باقی‌مانده» یا «On-Hold».
- فیلتر لیست: Limited (Volume) / Limited (Time) / On-Hold.
- در Select Peers: «تنظیم محدودیت گروهی».

## ۱۰. تست
- **واحد (`evaluate`):** هر شاخه‌ی جدول تصمیم؛ مرز دقیق `usage == limit`؛ `cumu` منفی بعد از ریست؛ On-Hold با/بدون deadline؛ دلیل دستی ← بدون بازگشت؛ هشدار فقط یک بار.
- **یکپارچه:** پر شدن حجم ← قطع در کمتر از ۶۰ ثانیه؛ `limits/add` ← وصل؛ تمدید زمان برای کاربر با حجم تمام‌شده ← وصل **نمی‌شود**؛ ۱۰۰ peer هم‌زمان منقضی ← یک `wg-quick save`.
- **منطقه‌ی زمانی:** سرور پنل UTC، ورودی `+03:30` ← انقضا در لحظه‌ی درست.
- **پایداری:** خطا در یک کانفیگ (مثلاً فایل خراب) بقیه را متوقف نکند.

## ۱۱. استقرار و Rollback
Rollback: `[Features] peer_limits = false` ← موتور متوقف می‌شود؛ جدول می‌ماند و job ها (اگر مهاجرت نشده‌اند) کار می‌کنند. اگر مهاجرت واقعی انجام شده، endpoint `migrate-jobs` یک حالت معکوس (`direction: "to_jobs"`) هم دارد.

## ۱۲. فایل‌های درگیر
`src/modules/PeerLimits.py` (جدید)، `src/api_v2.py`، [WireguardConfiguration.py](../../src/modules/WireguardConfiguration.py) (`restrictPeersBatch`، حذف/rename/dump)، [dashboard.py](../../src/dashboard.py) (thread)، [Peer.py](../../src/modules/Peer.py) (`limits` در JSON)، کامپوننت‌های `peerSettings.vue` و `peer.vue`، `tests/test_peer_limits.py`.

## ۱۳. ریسک‌ها و سؤالات باز
- **دقت حجم:** با بازه‌ی ۶۰ ثانیه، کاربر پرسرعت ممکن است چند صد مگابایت بیش از سقف مصرف کند. اگر مهم است، بازه را ۲۰ ثانیه کنید (هزینه: یک JOIN سبک).
- **کاربرانی که از قبل محدود شده‌اند:** مقدار حجم/زمانشان را فقط ربات می‌داند ← یک‌بار با اسکریپت ربات `PUT limits` برایشان ارسال شود.
- آیا On-Hold در فروش‌های فعلی شما استفاده می‌شود؟ اگر نه، می‌توان آن را در فاز دوم اضافه کرد.
