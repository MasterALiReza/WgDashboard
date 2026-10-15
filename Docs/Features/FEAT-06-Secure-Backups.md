# FEAT-06 — بکاپ امن، خودکار و قابل اعتماد

| | |
|---|---|
| اولویت | ⭐⭐ ارزش بالا (فاز A آن در عمل رفع باگ بحرانی است) |
| حجم کار | M-L — فاز A: ۲ روز، فاز B: ۱.۵ روز، فاز C: ۲ روز |
| حل می‌کند | [GB-01](../05-Global-Backup-Review.md#gb-01) تا [GB-16](../05-Global-Backup-Review.md#gb-16)، بخشی از [SEC-03](../02-Security-Vulnerabilities.md#sec-03) و [BUG-13](../03-Backend-Bugs-and-Logic.md#bug-13) |
| وابستگی | registry موتورهای دیتابیس ([PERF-04](../07-Performance-and-Stability.md#perf-04))، [FEAT-08](FEAT-08-Health-Metrics-Alerts.md) برای هشدار |

## ۱. مسئله
خلاصه‌ی [سند ۰۵](../05-Global-Backup-Review.md): ریستور فایل SQLite را زیر پای برنامه‌ی زنده عوض می‌کند و threadها همچنان می‌نویسند؛ هیچ بکاپ ایمنی قبل از ریستور گرفته نمی‌شود؛ MANIFEST بررسی نمی‌شود؛ بکاپ آپلودی می‌تواند کد اجرا کند؛ retention بکاپ‌های دستی را هم پاک می‌کند؛ بکاپ‌ها رمزنگاری‌نشده و فقط روی همان سرورند (اگر سرور از دست برود، بکاپ هم رفته).

## ۲. اهداف
1. **سازگاری:** snapshot هم‌زمان و سازگار از دیتابیس‌ها و فایل‌های `.conf`.
2. **صحت:** بررسی sha256 همه‌ی فایل‌ها و امضای HMAC قبل از ریستور.
3. **محرمانگی:** رمزنگاری اختیاری؛ ترجیحاً با کلید عمومی تا سرور نتواند بکاپ‌های بیرونی را باز کند.
4. **در دسترس بودن:** ارسال خودکار به تلگرام یا هر مقصد rclone.
5. **ایمنی ریستور:** بکاپ خودکار قبل از ریستور، ریستور مرحله‌ای (staged)، توقف threadها، ری‌استارت کامل سرویس.
6. **کنترل:** retention جداگانه، pin، پیش‌نمایش تغییرات (dry-run)، ریستور انتخابی یک اینترفیس.

## ۳. غیر اهداف
بکاپ افزایشی/deduplicated و Point-in-Time Recovery.

## ۴. طراحی

### ۴.۱ قالب آرشیو نسخه‌ی ۲
```
GlobalBackup_20261014_183000_<label>.zip        (بدون رمز)
GlobalBackup_20261014_183000_<label>.wgdbak     (رمزنگاری‌شده، قالب age)
 ├─ MANIFEST.json
 ├─ MANIFEST.sig                  hex(HMAC_SHA256(signing_key, MANIFEST.json))
 ├─ db/wgdashboard.db             ← sqlite3 Online Backup API
 ├─ db/wgdashboard_job.db
 ├─ db/wgdashboard_log.db         (اختیاری)
 ├─ config/wg-dashboard.ini
 ├─ config/wg-dashboard-oidc-providers.json
 ├─ interfaces/wireguard/<name>.conf
 ├─ interfaces/amnezia/<name>.conf
 └─ code/…                        فقط اگر include_code=true (gunicorn.conf.py، plugins/)
```
`MANIFEST.json`:
```json
{ "format": 2, "created_at": "2026-10-14T18:30:00Z", "kind": "auto|manual|pre_restore",
  "label": "", "hostname": "vpn-tr-1", "dashboard_version": "v4.3.3-oxbot.1", "schema_version": 7,
  "interfaces": [{"name": "wg0", "protocol": "wg", "peers": 1610}],
  "options": {"include_logs": true, "include_code": false},
  "files": [{"path": "db/wgdashboard.db", "sha256": "…", "size": 52428800}] }
```
برای MySQL/PostgreSQL به‌جای فایل `.db`، خروجی `mysqldump`/`pg_dump` با فرمت خود ابزار ذخیره می‌شود (نه split روی `;` — [GB-08](../05-Global-Backup-Review.md#gb-08)).

### ۴.۲ snapshot سازگار
```python
def snapshot(staging):
    confs = sorted(WireguardConfigurations.values(), key=lambda c: c.Name)   # ترتیب ثابت ← بدون deadlock
    with ExitStack() as stack:
        for c in confs:
            stack.enter_context(c.lock)          # چند ثانیه؛ درخواست‌های ربات صبر می‌کنند
        for c in confs:
            copy_atomic(c.configPath, staging / "interfaces" / proto_dir(c) / f"{c.Name}.conf")
        for name in DBS:
            src = sqlite3.connect(db_path(name)); dst = sqlite3.connect(staging / "db" / f"{name}.db")
            src.backup(dst)                      # سازگار حتی در WAL و حین نوشتن
            dst.close(); src.close()
```
قفل‌ها فقط برای کپی (چند ثانیه) نگه داشته می‌شوند؛ فشرده‌سازی و رمزنگاری بعد از رها شدن قفل‌ها انجام می‌شود.

### ۴.۳ رمزنگاری
پیشنهاد: قالب استاندارد **age** با کتابخانه‌ی `pyrage` (wheel آماده برای لینوکس x86_64/arm64؛ فایل با ابزار رسمی `age` هم باز می‌شود). دو حالت:

| حالت | تنظیم | مزیت | عیب |
|------|-------|------|-----|
| **کلید عمومی (توصیه‌شده)** | `[GlobalBackup] age_recipient = age1…` | سرور فقط کلید عمومی دارد؛ بکاپ‌های تلگرام/ابر حتی با هک سرور باز نمی‌شوند | کلید خصوصی (`AGE-SECRET-KEY-…`) را باید جای امن (مدیر رمز) نگه دارید؛ هنگام ریستور وارد می‌شود |
| Passphrase | `[GlobalBackup] passphrase_hash` + passphrase در زمان ساخت | ساده | برای بکاپ خودکار، passphrase باید روی سرور ذخیره شود |

جایگزین بدون وابستگی جدید: AES-256-GCM با `cryptography` (به‌صورت chunked، KDF = scrypt). در این صورت `cryptography` به `requirements.txt` اضافه می‌شود (که fallback تولید کلید در [Utilities.py:92-123](../../src/modules/Utilities.py#L92-L123) را هم واقعاً فعال می‌کند).

### ۴.۴ امضا
`[GlobalBackup] signing_key` (۳۲ بایت تصادفی، در اولین اجرا ساخته می‌شود). ریستور، `MANIFEST.sig` را بررسی می‌کند. اگر امضا نامعتبر یا وجود نداشت (بکاپ قالب قدیمی یا از سرور دیگر)، ریستور فقط با تیک صریح «به این بکاپ اعتماد دارم» و پسورد ادمین ادامه می‌یابد. برای مهاجرت به سرور جدید، `signing_key` را از ini قدیمی کپی کنید.

### ۴.۵ Retention
```ini
[GlobalBackup]
auto_keep = 10
manual_keep = 20
pre_restore_keep = 5
```
- نوع بکاپ از `MANIFEST.kind` خوانده می‌شود؛ هر نوع جداگانه شمرده می‌شود.
- بکاپ‌های **pin‌شده** هرگز حذف نمی‌شوند (فایل جانبی `GlobalBackups/index.json`).
- متن UI با رفتار واقعی یکی می‌شود ([GB-09](../05-Global-Backup-Review.md#gb-09)).

### ۴.۶ ارسال خارج از سرور
```ini
[GlobalBackup]
offsite_targets = telegram,rclone
telegram_bot_token = <توکن یک ربات جدا، نه توکن OxBot>
telegram_chat_id = <آیدی چت ادمین یا کانال خصوصی>
rclone_remote = b2:my-bucket/wgdashboard
offsite_require_encryption = true
```
- **تلگرام:** `sendDocument` (حداکثر ۵۰ مگابایت در Bot API عمومی). کپشن: نام سرور، زمان، حجم، sha256. اگر فایل بزرگ‌تر بود، ارسال نمی‌شود و هشدار می‌آید.
- **rclone:** `rclone copyto <file> <remote>/<file>` با timeout؛ هر مقصدی که rclone پشتیبانی می‌کند (S3، Backblaze B2، Google Drive، ...).
- اگر `offsite_require_encryption = true` باشد، بکاپ بدون رمز هرگز بیرون فرستاده نمی‌شود.
- نتیجه‌ی هر ارسال در `index.json` و رویداد `backup.completed`/`backup.failed` ([FEAT-04](FEAT-04-Signed-Event-Webhooks.md)).

### ۴.۷ خط لوله‌ی ریستور
```
1. احراز: فقط session ادمین + ورود مجدد پسورد (+TOTP اگر فعال)؛ API Key ← 403
2. دریافت فایل در staging (مجوز 0700) با سقف حجم (MAX_CONTENT_LENGTH، مثلاً 512MB)
3. رمزگشایی در صورت نیاز (identity/passphrase فقط در حافظه)
4. اعتبارسنجی:
     MANIFEST موجود و format پشتیبانی‌شده
     sha256 همه‌ی فایل‌ها برابر؛ هیچ فایل اضافه‌ای؛ مسیرها فقط در db/، config/، interfaces/ (و code/ اگر انتخاب شده)
     سقف حجم استخراج (ضد zip bomb)
     امضای HMAC (یا تأیید صریح)
5. Plan (dry-run) ← به UI برگردانده می‌شود:
     اینترفیس‌های اضافه/حذف/تغییرکرده، تعداد peer هر کدام، تفاوت تنظیمات مهم،
     اینترفیس‌هایی که نیاز به down/up دارند، نسخه‌ی schema
     + یک confirm_token با اعتبار ۱۰ دقیقه
6. اجرا (با confirm_token):
   a. بکاپ pre_restore (pin خودکار برای ۷ روز)
   b. MAINTENANCE.set() ← API: 503؛ threadهای ۱، ۲، ۳، موتور محدودیت و پلاگین‌ها در ابتدای حلقه منتظر می‌مانند؛ انتظار تا idle (حداکثر ۶۰ ثانیه)
   c. دیتابیس‌ها در db/.staging/ + PRAGMA integrity_check
   d. dispose همه‌ی engineها (registry در PERF-04)
   e. برای هر دیتابیس: حذف -wal و -shm قدیمی ← os.replace(staged, live)
   f. نوشتن اتمیک ini — با حفظ مقادیر «محلی» سرور فعلی:
        wg_conf_path، awg_conf_path، app_ip، app_port، app_prefix، signing_key
   g. نوشتن اتمیک .confها در مسیرهای **فعلی** (نه مسیر داخل بکاپ — GB-04)
      اینترفیس‌هایی که در بکاپ نیستند: فقط اگر گزینه‌ی remove_missing_interfaces انتخاب شده باشد
   h. code/ فقط با تیک جداگانه
   i. اینترفیس‌های روشن: wg syncconf؛ آن‌هایی که Address/ListenPort/MTU/PostUp تغییر کرده: wg-quick down/up
   j. نوشتن نشانگر restore_pending.json و اجرای «systemctl restart wg-dashboard» به‌صورت detached
      (maintenance تا خروج پروسه روشن می‌ماند — GB-05)
7. هنگام boot: اگر restore_pending وجود دارد و health سالم است ← حذف نشانگر + رویداد backup.restore_completed
```

### ۴.۸ ریستور انتخابی (یک اینترفیس) — فاز C
با `ATTACH DATABASE` فایل بکاپ و کپی ردیف‌های همان اینترفیس، فقط زیر قفل همان اینترفیس (بدون maintenance کل سیستم):
- جداول `<conf>`، `<conf>_restrict_access`، `<conf>_transfer`، `<conf>_history_endpoint`، `<conf>_traffic_snapshot`، `<conf>_deleted`
- ردیف‌های `ConfigurationsInfo` (ID = conf)، `PeerLimits`، `PeerShareLinks`، `DashboardClientsPeerAssignment` با Configuration = conf، و `PeerJobs` در دیتابیس job
- فایل `.conf` و سپس `wg syncconf`

### ۴.۹ زمان‌بندی
- رفع busy loop ([BUG-13](../03-Backend-Bugs-and-Logic.md#bug-13)).
- در شکست: backoff (۱ ساعت، ۳ ساعت، ۶ ساعت) به‌جای تلاش هر ۶۰ ثانیه، و هشدار.
- هشدار اگر آخرین بکاپ موفق قدیمی‌تر از دو برابر بازه‌ی زمان‌بندی باشد.

## ۵. API
```http
GET    /api/v2/backups                                    لیست با kind، pinned، encrypted، offsite status
POST   /api/v2/backups              { "label": "", "include_logs": true, "encrypt": true, "offsite": true }  → { job_id }
POST   /api/v2/backups/pin          { "filename": "…", "pinned": true }
POST   /api/v2/backups/delete       { "filename": "…" }
GET    /api/v2/backups/download?filename=…
POST   /api/v2/backups/offsite/test { "target": "telegram" }
POST   /api/v2/backups/restore/plan     (multipart: file یا filename، identity/passphrase، interfaces[] اختیاری)  → { plan, confirm_token }
POST   /api/v2/backups/restore/execute  { "confirm_token": "…", "password": "…", "totp": "…", "options": {...} } → { job_id }
GET    /api/v2/jobs/<job_id>                               پیشرفت با polling (به‌جای SSE — GB-11)
```
همه با session ادمین؛ API Key (حتی admin) برای restore پذیرفته نمی‌شود.

## ۶. امنیت
- پوشه‌ی بکاپ `0700` و فایل‌ها `0600`؛ staging بعد از هر عملیات پاک می‌شود.
- کد (`gunicorn.conf.py`، پلاگین‌ها) به‌طور پیش‌فرض نه در بکاپ است نه ریستور می‌شود.
- مسیر مقصد `.conf`ها هرگز از بکاپ خوانده نمی‌شود.
- identity/passphrase رمزگشایی هرگز ذخیره یا لاگ نمی‌شود.
- توکن ربات تلگرامِ بکاپ، جدا از توکن OxBot است تا نشت یکی دیگری را لو ندهد.

## ۷. سازگاری
بکاپ‌های قالب ۱ (فعلی) قابل ریستورند: مسیر `configs/…` به `interfaces/…` نگاشت و dump SQL در یک فایل SQLite **staging** اجرا می‌شود (نه روی فایل زنده). چون امضا ندارند، تأیید صریح لازم است.

## ۸. تغییرات ربات OxBot
لازم نیست. (اختیاری: اگر می‌خواهید بکاپ‌ها به همان چت ادمین ربات بروند، `telegram_chat_id` همان چت باشد ولی با توکن ربات جدا.)

## ۹. تغییرات UI
- لیست: نشان نوع (Auto/Manual/Pre-restore)، قفل (رمزنگاری)، سنجاق (pin)، وضعیت ارسال بیرونی.
- تنظیمات: زمان‌بندی، تعداد نگهداری هر نوع، حالت رمزنگاری و کلید عمومی، مقصدهای بیرونی با دکمه‌ی «تست».
- ویزارد ریستور: انتخاب/آپلود ← وارد کردن کلید ← نمایش Plan ← تأیید با پسورد(+TOTP) ← پیشرفت ← بارگذاری مجدد خودکار. همه‌ی آدرس‌ها با `getUrl` ([FE-03](../08-Frontend-Integration.md#fe-03)).

## ۱۰. تست
- **واحد:** بررسی MANIFEST (فایل اضافه، sha256 غلط، مسیر خارج از whitelist)، HMAC، رمزنگاری/رمزگشایی رفت‌وبرگشت، retention با pin.
- **یکپارچه:** ریستور در حالی که یک اسکریپت بار، هر ثانیه peer می‌سازد/حذف می‌کند ← بعد از ریستور `PRAGMA integrity_check = ok` و تعداد peerها برابر بکاپ.
- **کرش:** `kill -9` وسط مرحله‌ی e ← سیستم بالا می‌آید و بکاپ pre_restore در لیست است.
- **امنیت:** zip حاوی `code/plugins/x/main.py` بدون تیک ← نادیده گرفته شود؛ ini با `wg_conf_path=/etc/modprobe.d` ← مسیر فعلی حفظ شود.
- **سازگاری:** ریستور یک بکاپ قالب ۱ واقعی.

## ۱۱. استقرار (فازبندی)
| فاز | شامل | چرا اول |
|-----|------|---------|
| A | snapshot سازگار، بررسی MANIFEST، pre_restore، خط لوله‌ی ریستور مرحله‌ای، retention جدا، polling به‌جای SSE | رفع GB-01..05 و GB-09 — جلوی خرابی دیتابیس را می‌گیرد |
| B | رمزنگاری، امضا، ارسال به تلگرام/rclone | در دسترس بودن بکاپ اگر سرور از دست برود |
| C | Plan/dry-run کامل، ریستور انتخابی اینترفیس | راحتی و ریسک کمتر |

## ۱۲. فایل‌های درگیر
[GlobalBackup.py](../../src/modules/GlobalBackup.py) (بازنویسی بخش عمده)، `src/modules/Maintenance.py` (جدید: Event و هماهنگی threadها)، `src/modules/EngineRegistry.py` (PERF-04)، [dashboard.py](../../src/dashboard.py) (thread‌ها و endpointها)، [globalBackup.vue](../../src/static/app/src/components/settingsComponent/globalBackup.vue)، `requirements.txt` (`pyrage` یا `cryptography`)، `tests/test_backup_v2.py`.
