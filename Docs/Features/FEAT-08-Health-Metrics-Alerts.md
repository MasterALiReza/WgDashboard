# FEAT-08 — Health Check، متریک و هشدار به ادمین

| | |
|---|---|
| اولویت | ⭐⭐ ارزش بالا |
| حجم کار | S (۱ روز) |
| حل می‌کند | خطاهای بی‌صدا: [BUG-02](../03-Backend-Bugs-and-Logic.md#bug-02) (حلقه‌ی job خوابیده)، [BUG-05](../03-Backend-Bugs-and-Logic.md#bug-05)، شکست بکاپ ([GB-10](../05-Global-Backup-Review.md#gb-10))، خرابی دیتابیس ([PERF-07](../07-Performance-and-Stability.md#perf-07))، فریز ([PERF-05](../07-Performance-and-Stability.md#perf-05))؛ فروش در زمان خرابی پنل |
| وابستگی | ندارد |

## ۱. مسئله
وقتی بخشی از پنل از کار می‌افتد (thread job ها گیر کرده، بکاپ شکست می‌خورد، دیسک پر می‌شود، اینترفیس down می‌شود، همه‌ی ۸ thread مشغول‌اند)، هیچ‌کس مطلع نمی‌شود تا وقتی کاربران شکایت کنند. ربات هم در همان حالت پول می‌گیرد و سرویس تحویل نمی‌دهد.

## ۲. اهداف
1. endpoint سبک و بدون احراز برای مانیتورهای uptime و ربات.
2. گزارش جزئی سلامت هر زیرسیستم برای ادمین.
3. متریک‌های Prometheus برای Grafana (اختیاری).
4. هشدار خودکار تلگرامی و webhook با جلوگیری از هشدار تکراری.

## ۳. غیر اهداف
راه‌اندازی خود Prometheus/Grafana (فقط فرمت خروجی فراهم می‌شود).

## ۴. طراحی

### ۴.۱ ضربان (Heartbeat)
ماژول `src/modules/Health.py`:
```python
_beats: dict[str, float] = {}
def beat(name: str): _beats[name] = time.time()
EXPECTED = {                       # نام: حداکثر فاصله‌ی مجاز (ثانیه)
    "peer_info_loop": 60,          # thread 1 هر ۱۰ ثانیه
    "jobs_loop": 600,              # thread 2 هر ۱۸۰ ثانیه
    "limits_engine": 300,          # FEAT-02 هر ۶۰ ثانیه
    "scheduler_loop": 180,         # thread 3 هر ۶۰ ثانیه
}
```
هر حلقه ابتدای هر دور `beat(...)` صدا می‌زند. اگر یک حلقه به‌خاطر استثنای تکرارشونده کار نکند (مثل BUG-02)، ضربانش قدیمی می‌شود.

### ۴.۲ بررسی‌ها

| بررسی | روش | خراب اگر |
|-------|------|----------|
| `db` | `SELECT 1` روی هر سه دیتابیس با timeout ۲ ثانیه | خطا یا > ۲ ثانیه |
| `interfaces` | اینترفیس‌های autostart باید up باشند (`getStatus`) | autostart ولی down |
| `loops` | ضربان‌ها طبق جدول بالا | قدیمی |
| `jobs` | تعداد job های منقضی‌شده‌ی «stale» در ۲۴ ساعت | > ۰ |
| `backup` | سن آخرین بکاپ موفق | > ۲ × بازه‌ی زمان‌بندی |
| `disk` | `shutil.disk_usage` پوشه‌ی db و بکاپ | > ۹۰٪ |
| `memory` | RSS پروسه (psutil) | > حد تنظیم‌شده |
| `workers` | شمارنده‌ی درخواست‌های در حال اجرا (`before_request`/`teardown_request`) | ≥ ۷ از ۸ برای بیش از ۳۰ ثانیه |
| `maintenance` | پرچم ریستور | روشن (وضعیت `maintenance`، نه خطا) |

### ۴.۳ هشدارها — `src/modules/Alerts.py`
- قوانین هر ۶۰ ثانیه در thread شماره ۳ ارزیابی می‌شوند. وضعیت هر قانون: `ok` ← `firing` ← `resolved`.
- ارسال در شروع `firing`، یادآوری هر ۶ ساعت تا رفع، و پیام «رفع شد» در `resolved`.
- کانال‌ها: تلگرام (ربات **جدا** از OxBot) و رویدادهای `system.*` در [FEAT-04](FEAT-04-Signed-Event-Webhooks.md).
- علاوه بر بررسی‌های بالا، رویدادهای امنیتی: فعال شدن rate limit ورود بیش از N بار در ساعت، ساخت API Key جدید، تغییر پسورد ادمین، اجرای ریستور.

```ini
[Alerts]
enabled = true
telegram_bot_token = …
telegram_chat_id = …
disk_percent = 90
reminder_hours = 6
```
نمونه‌ی پیام:
```
🔴 WGDashboard (vpn-tr-1)
حلقه‌ی job ها ۱۴ دقیقه است اجرا نشده.
آخرین خطا: ValueError: could not convert string to float: ''
محدودیت‌های حجم/زمان کاربران الان اعمال نمی‌شوند.
```

## ۵. API
```http
GET /api/health                       بدون احراز (در whitelist)
→ 200 {"status":"ok"}  |  503 {"status":"degraded"}  |  503 {"status":"maintenance"}
```
بدون جزئیات (تا اطلاعات داخلی به بیرون نشت نکند).

```http
GET /api/v2/health        (metrics:read یا session)
→ { "status": "degraded", "version": "…", "uptime_seconds": 86400,
    "checks": { "db": {"status":"ok","latency_ms":3},
                "loops": {"status":"fail","details":{"jobs_loop":{"last_beat_seconds_ago":840}}},
                ... } }

GET /metrics              (metrics:read)   — فرمت متنی Prometheus
```
متریک‌ها:
```
wgd_up 1
wgd_peers{configuration="wg0",state="active|restricted|online"} …
wgd_interface_bytes_total{configuration="wg0",direction="rx|tx"} …
wgd_loop_last_beat_timestamp_seconds{loop="jobs_loop"} …
wgd_backup_last_success_timestamp_seconds …
wgd_http_requests_total{route="/api/addPeers",status="200"} …
wgd_http_request_duration_seconds_bucket{route="…",le="0.1"} …
wgd_http_inflight_requests …
wgd_db_errors_total{database="wgdashboard"} …
wgd_jobs_stale_total …
```

## ۶. امنیت
- `/api/health` عمومی ولی فقط یک کلمه برمی‌گرداند.
- `/metrics` با scope جدا ([FEAT-05](FEAT-05-Scoped-API-Keys.md))؛ برچسب‌ها شامل public key یا IP کاربر نیستند (فقط تجمیعی).
- توکن ربات هشدار در ini و جدا از توکن OxBot.

## ۷. سازگاری
افزودنی محض.

## ۸. تغییرات ربات OxBot
**قبل از گرفتن پول** برای محصولات WireGuard:
```php
function wg_panel_healthy($namepanel) {
    $p = select("marzban_panel", "*", "name_panel", $namepanel, "select");
    $req = new CurlRequest(rtrim($p['url_panel'], '/') . '/api/health');
    $req->setTimeout(3000);
    $r = $req->get();
    return ($r['status'] ?? 0) == 200;
}
// در مسیر خرید، قبل از نمایش درگاه:
if (!wg_panel_healthy($panel['name_panel'])) {
    sendmessage($from_id, "⛔️ این سرور موقتاً در حال نگهداری است. لطفاً چند دقیقه دیگر تلاش کنید.", null, 'HTML');
    return;
}
```
این از «پول گرفته شد ولی سرویس ساخته نشد» جلوگیری می‌کند.

## ۹. تغییرات UI
- کارت «سلامت سیستم» در صفحه‌ی System Status با وضعیت هر بررسی.
- تنظیمات هشدار (توکن، چت، آستانه‌ها) و دکمه‌ی «ارسال پیام آزمایشی».

## ۱۰. تست
- شبیه‌سازی استثنای دائمی در `runJob` ← بعد از ۱۰ دقیقه هشدار `jobs_loop`.
- دیسک پر (mock) ← هشدار؛ رفع ← پیام resolved؛ عدم ارسال تکراری در بازه‌ی یادآوری.
- `/api/health` بدون کوکی ← 200؛ در حین ریستور ← 503 `maintenance`.

## ۱۱. استقرار
بی‌خطر. ابتدا با `enabled = false` برای هشدارها، بررسی خروجی `/api/v2/health`، سپس روشن کردن.

## ۱۲. فایل‌های درگیر
`src/modules/Health.py`، `src/modules/Alerts.py`، `src/modules/Metrics.py` (جدید)، [dashboard.py](../../src/dashboard.py) (ضربان در threadها، whitelist، شمارنده‌ی درخواست)، `systemStatus.vue`، `tests/test_health.py`.
