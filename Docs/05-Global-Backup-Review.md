# ۰۵ — بررسی سیستم Global Backup

**فایل‌ها:** [GlobalBackup.py](../src/modules/GlobalBackup.py)، endpointها در [dashboard.py:860-1120](../src/dashboard.py#L860-L1120)، thread زمان‌بندی در [dashboard.py:225-297](../src/dashboard.py#L225-L297)، UI در [globalBackup.vue](../src/static/app/src/components/settingsComponent/globalBackup.vue)

## نقاط قوت (درست انجام شده)
- محافظت در برابر **Zip Slip** در استخراج ([GlobalBackup.py:472-478](../src/modules/GlobalBackup.py#L472-L478)).
- پاک‌سازی `label` و استفاده از `basename` در دانلود/حذف؛ حذف فقط فایل‌های `GlobalBackup_*.zip`.
- بررسی magic bytes فایل آپلودی (`PK\x03\x04`).
- استفاده از `wg syncconf` برای همگام‌سازی بدون قطع اتصال کاربران ([GlobalBackup.py:16-49](../src/modules/GlobalBackup.py#L16-L49)) — انتخاب خوبی است.
- گزارش پیشرفت با SSE و پاک‌سازی job ها با TTL.
- جلوگیری از دو بکاپ هم‌زمان با `_is_backup_running`.

## جدول خلاصه‌ی مشکلات

| ID | شدت | عنوان |
|----|------|-------|
| [GB-01](#gb-01) | 🔴 | ریستور فایل SQLite را زیر پای برنامه‌ی در حال اجرا پاک می‌کند؛ threadها همچنان می‌نویسند |
| [GB-02](#gb-02) | 🟠 | ریستور بدون بکاپ ایمنی؛ شکست وسط کار = دیتابیس خالی؛ نتیجه‌ی ریستور DB نادیده گرفته می‌شود |
| [GB-03](#gb-03) | 🟠 | هش‌های MANIFEST هرگز بررسی نمی‌شوند |
| [GB-04](#gb-04) | 🟠 | فایل بکاپ آپلودی می‌تواند کد را به‌عنوان root اجرا کند |
| [GB-05](#gb-05) | 🟡 | پنجره‌ی ۳ ثانیه‌ای بعد از ریستور: حالت قدیمی حافظه، ini ریستورشده را بازنویسی می‌کند |
| [GB-06](#gb-06) | 🟡 | بکاپ اتمیک نیست (`.conf` و دیتابیس در دو زمان مختلف) |
| [GB-07](#gb-07) | 🟡 | `iterdump` snapshot سازگار نمی‌دهد؛ API `backup()` بهتر است |
| [GB-08](#gb-08) | 🟡 | dump/restore برای MySQL/PostgreSQL روی `;` داخل داده می‌شکند و خطاها بلعیده می‌شوند |
| [GB-09](#gb-09) | 🟡 | Retention بکاپ‌های **دستی** را هم پاک می‌کند |
| [GB-10](#gb-10) | 🟡 | هم‌زمانی ریستور و بکاپ، و تلاش مجدد بی‌وقفه‌ی بکاپ خودکار |
| [GB-11](#gb-11) | 🟡 | SSE بدون سقف زمانی threadهای Gunicorn را اشغال می‌کند؛ سقف ۵ دقیقه‌ی ریستور |
| [GB-12](#gb-12) | 🟡 | بکاپ‌ها رمزنگاری‌نشده و با مجوز پیش‌فرض در پوشه‌ی برنامه |
| [GB-13](#gb-13) | 🟢 | محدودیت حجم آپلود/استخراج و فایل آپلودی باقی‌مانده |
| [GB-14](#gb-14) | 🟢 | `includeExistingBackups` ذخیره می‌شود ولی هرگز ریستور نمی‌شود |
| [GB-15](#gb-15) | 🟢 | `syncconf` تغییرات Address/MTU/PostUp را اعمال نمی‌کند |
| [GB-16](#gb-16) | 🟢 | نسخه‌ی ثابت در MANIFEST و نبود بررسی سازگاری |

---

<a id="gb-01"></a>
## GB-01 🔴 جایگزینی دیتابیس زنده حین اجرا

**محل:** [GlobalBackup.py:178-188](../src/modules/GlobalBackup.py#L178-L188)، [dashboard.py:973](../src/dashboard.py#L973)، [dashboard.py:1017-1049](../src/dashboard.py#L1017-L1049)

```python
if os.path.exists(db_path):
    os.remove(db_path)                         # فایل اصلی حذف می‌شود
with sqlite3.connect(db_path) as con:
    con.executescript(sql_content)             # فایل جدید ساخته می‌شود
```
مشکلات:
1. دیتابیس در حالت **WAL** است ([DatabaseConnection.py:26](../src/modules/DatabaseConnection.py#L26)). فایل‌های `-wal` و `-shm` حذف نمی‌شوند ولی فایل اصلی حذف می‌شود. connectionهای باز SQLAlchemy (هر کلاس engine جدای خودش را دارد) هنوز به **inode قدیمیِ حذف‌شده** اشاره می‌کنند و همان فایل‌های `-wal/-shm` را با دیتابیس جدید **به اشتراک** می‌گذارند. این دقیقاً شرایط کلاسیک خرابی SQLite است.
2. `MAINTENANCE_MODE` فقط درخواست‌های `/api/` را رد می‌کند. **threadهای پس‌زمینه متوقف نمی‌شوند**: thread #1 هر ۱۰ ثانیه ترافیک را در دیتابیس می‌نویسد ([dashboard.py:84-133](../src/dashboard.py#L84-L133))، thread #2 job ها را اجرا و peerها را restrict/delete می‌کند، پلاگین‌ها هم ادامه می‌دهند.
3. نوشته‌هایی که بعد از ریستور روی connection قدیمی انجام می‌شوند در فایل حذف‌شده گم می‌شوند.

این به احتمال زیاد یکی از ریشه‌های خطاهای `malformed` است که commitهای «self-healing» برایشان اضافه شده‌اند.

**راه‌حل:**
1. یک `threading.Event` سراسری `MAINTENANCE` که حلقه‌ی threadهای ۱، ۲ و ۳ ابتدای هر دور آن را چک کنند و صبر کنند؛ قبل از ریستور منتظر بمانید تا همه idle شوند.
2. همه‌ی engineها را `dispose()` کنید (یک registry مرکزی engine بسازید؛ [PERF-04](07-Performance-and-Stability.md#perf-04)).
3. دیتابیس جدید را در فایل موقت بسازید، `PRAGMA integrity_check` بگیرید، بعد `-wal`/`-shm` قدیمی را حذف و با `os.replace` جایگزین کنید.
4. در پایان کل سرویس را ری‌استارت کنید (نه فقط worker).

---

<a id="gb-02"></a>
## GB-02 🟠 ریستور بدون بکاپ ایمنی

**محل:** [GlobalBackup.py:172-211](../src/modules/GlobalBackup.py#L172-L211)، [GlobalBackup.py:544-557](../src/modules/GlobalBackup.py#L544-L557)

- قبل از ریستور هیچ بکاپی از وضعیت فعلی گرفته نمی‌شود؛ اگر فایل بکاپ اشتباه/قدیمی انتخاب شود، راه برگشتی نیست.
- برای SQLite فایل **قبل** از `executescript` حذف می‌شود؛ اگر اسکریپت وسط کار خطا بدهد، دیتابیس نیمه‌خالی می‌ماند.
- خروجی `restore_database_from_sql` نادیده گرفته می‌شود ([GlobalBackup.py:554](../src/modules/GlobalBackup.py#L554)) و ریستور «موفق» گزارش می‌شود.
- در MySQL/PostgreSQL هر خطای statement با `except: pass` بلعیده می‌شود ([GlobalBackup.py:202-207](../src/modules/GlobalBackup.py#L202-L207)).

**راه‌حل:** اولین قدم ریستور: `create_global_backup(label="PreRestore")` (که از retention هم مستثنی باشد). ریستور در فایل موقت، و اگر هر قدمی False داد، کل عملیات شکست بخورد و فایل‌های اصلی دست‌نخورده بمانند.

---

<a id="gb-03"></a>
## GB-03 🟠 MANIFEST بررسی نمی‌شود

**محل:** [GlobalBackup.py:446-456](../src/modules/GlobalBackup.py#L446-L456)

در ساخت بکاپ برای هر فایل `sha256` و `size` ذخیره می‌شود، ولی `validate_backup_manifest` فقط **وجود** `MANIFEST.json` را چک می‌کند. یک zip خراب (دانلود ناقص، دیسک خراب) یا دستکاری‌شده بی‌صدا ریستور می‌شود.

**راه‌حل:**
```python
for entry in manifest["files"]:
    data = zipf.read(entry["path"])
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        return False, f"Checksum mismatch: {entry['path']}"
allowed = {e["path"] for e in manifest["files"]} | {"MANIFEST.json"}
extra = [n for n in zipf.namelist() if n not in allowed and not n.endswith("/")]
if extra:
    return False, f"Unexpected files in archive: {extra[:5]}"
```
و برای جلوگیری از جعل: یک امضای HMAC روی MANIFEST با کلیدی که فقط روی سرور است (پیشنهاد در [FEAT-06](09-Feature-Proposals.md#feat-06)).

---

<a id="gb-04"></a>
## GB-04 🟠 بکاپ آپلودی = اجرای کد به‌عنوان root

**محل:** [GlobalBackup.py:484-498](../src/modules/GlobalBackup.py#L484-L498)، [GlobalBackup.py:510-517](../src/modules/GlobalBackup.py#L510-L517)، [GlobalBackup.py:519-542](../src/modules/GlobalBackup.py#L519-L542)، [DashboardPlugins.py:39-66](../src/modules/DashboardPlugins.py#L39-L66)

یک zip دستکاری‌شده می‌تواند:
- `gunicorn.conf.py` را بازنویسی کند (کد Python که در start بعدی به‌عنوان root اجرا می‌شود)،
- پوشه‌ی `plugins/<x>/main.py` اضافه کند (بعد از ری‌استارت خودکار ریستور، اجرا می‌شود)،
- `wg_conf_path` را در ini خودش به مسیر دلخواه (مثلاً `/etc/modprobe.d`) تغییر دهد؛ چون مسیر مقصد `.conf`ها از **ini داخل همان zip** خوانده می‌شود، فایل‌های `*.conf` دلخواه به‌عنوان root در هر پوشه‌ای نوشته می‌شوند،
- کانفیگ WireGuard با `PostUp` دلخواه بگذارد.

پیش‌شرط: دسترسی ادمین یا API Key (که در سند ۰۱ افشا شده) یا فریب ادمین به ریستور یک فایل «بکاپ». 

**راه‌حل:**
- هرگز `gunicorn.conf.py`، `certbot.ini` و `plugins/` را ریستور نکنید (یا فقط با یک گزینه‌ی صریح و جدا).
- مسیر مقصد `.conf`ها را از تنظیمات **فعلی** سرور بخوانید، نه از ini بکاپ.
- اگر `.conf` بکاپ `PreUp/PostUp/PreDown/PostDown` دارد که با نسخه‌ی فعلی فرق دارد، به ادمین نمایش دهید و تأیید بگیرید.
- ریستور با API Key را ممنوع کنید (فقط session ادمین + تأیید TOTP).

---

<a id="gb-05"></a>
## GB-05 🟡 پنجره‌ی ۳ ثانیه‌ای بعد از ریستور

**محل:** [dashboard.py:1043-1049](../src/dashboard.py#L1043-L1049)

`MAINTENANCE_MODE = False` **قبل از** `sleep(3)` و `SIGTERM` ست می‌شود. در این ۳ ثانیه:
- درخواست‌های ربات روی اشیای قدیمی درون‌حافظه (peerهای قدیمی، API Keyهای قدیمی) اجرا می‌شوند.
- هر `SetConfig` (مثلاً `addAutostart` در `toggleConfiguration`) کل ini **قدیمی درون‌حافظه** را روی ini ریستورشده می‌نویسد ([DashboardConfig.py:319](../src/modules/DashboardConfig.py#L319)) و تنظیمات بکاپ از دست می‌رود.
- فرانت هم در همین بازه اولین poll را می‌زند و ممکن است صفحه را از پروسه‌ای که در حال مرگ است بارگذاری کند.

همچنین `os.kill(os.getpid(), SIGTERM)` فقط worker را می‌کشد؛ اگر روزی بدون Gunicorn اجرا شود (`./wgd.sh debug`) کل برنامه بسته می‌شود و بالا نمی‌آید.

**راه‌حل:** maintenance را تا خروج پروسه روشن نگه دارید و برای ری‌استارت کامل `systemctl restart wg-dashboard` (یا `SIGHUP` به master با `os.getppid()`) استفاده کنید.

---

<a id="gb-06"></a>
## GB-06 🟡 بکاپ اتمیک نیست

**محل:** [GlobalBackup.py:273-347](../src/modules/GlobalBackup.py#L273-L347)

فایل‌های `.conf` در ۳۰٪ کپی می‌شوند و دیتابیس در ۵۰٪ dump می‌شود. اگر ربات بین این دو peer اضافه/حذف کند، بکاپ ناسازگار است. بعد از ریستور، peerی که در `.conf` هست و در دیتابیس نیست با `private_key` خالی وارد و توسط auto-heal **re-key** می‌شود ([BUG-03](03-Backend-Bugs-and-Logic.md#bug-03)). همچنین ریستور، کانفیگ‌هایی را که الان هستند ولی در بکاپ نیستند حذف نمی‌کند ← ترکیبی از وضعیت قدیم و جدید.

**راه‌حل:** در زمان بکاپ `lock` همه‌ی کانفیگ‌ها را بگیرید (یا maintenance کوتاه)، یا `.conf` را از روی `wg showconf` + دیتابیس همان لحظه بسازید. در ریستور، لیست کانفیگ‌های اضافه را به ادمین نشان دهید.

---

<a id="gb-07"></a>
## GB-07 🟡 `iterdump` به‌جای Online Backup API

**محل:** [GlobalBackup.py:117-128](../src/modules/GlobalBackup.py#L117-L128)

`iterdump` چندین SELECT جدا اجرا می‌کند؛ اگر هم‌زمان peerی restrict شود (انتقال بین دو جدول)، ممکن است در هر دو جدول یا هیچ‌کدام ثبت شود. SQLite یک API مخصوص دارد که snapshot سازگار و سریع می‌دهد:
```python
src = sqlite3.connect(db_path)
dst = sqlite3.connect(os.path.join(sql_target, f"{db_name}.db"))
src.backup(dst)          # کپی سازگار حتی در حالت WAL و حین نوشتن
dst.close(); src.close()
```
و در ریستور همان فایل `.db` را (بعد از `integrity_check`) جایگزین کنید — سریع‌تر و بدون مشکل parse.

---

<a id="gb-08"></a>
## GB-08 🟡 MySQL/PostgreSQL

**محل:** [GlobalBackup.py:130-168](../src/modules/GlobalBackup.py#L130-L168)، [GlobalBackup.py:190-209](../src/modules/GlobalBackup.py#L190-L209)

`sql_content.split(";")` روی مقادیری که `;` دارند (نام/یادداشت peer، JSON تنظیمات با `PostUp` که معمولاً `;` دارد) می‌شکند و آن INSERTها بی‌صدا رد می‌شوند. اگر فقط SQLite استفاده می‌کنید، اولویت پایین است؛ در غیر این صورت از `pg_dump`/`mysqldump` یا export با فرمت JSON استفاده کنید.

---

<a id="gb-09"></a>
## GB-09 🟡 Retention بکاپ‌های دستی را هم پاک می‌کند

**محل:** [GlobalBackup.py:378-379](../src/modules/GlobalBackup.py#L378-L379)، [GlobalBackup.py:433-443](../src/modules/GlobalBackup.py#L433-L443)، متن UI در [globalBackup.vue:143](../src/static/app/src/components/settingsComponent/globalBackup.vue#L143)

بعد از **هر** بکاپ، همه به‌جز ۱۰ تای آخر حذف می‌شوند — شامل بکاپ‌های دستی. با زمان‌بندی ۳ ساعته، بکاپ دستی «قبل از مهاجرت» شما بعد از ۳۰ ساعت پاک می‌شود. متن UI می‌گوید «حداکثر ۱۰ بکاپ **خودکار**» که گمراه‌کننده است.

**راه‌حل:** retention جداگانه برای `Auto_*` و دستی، عدد قابل تنظیم، و امکان «قفل/pin» کردن بکاپ.

---

<a id="gb-10"></a>
## GB-10 🟡 هم‌زمانی و تلاش مجدد

- `API_restoreGlobalBackup` فقط `MAINTENANCE_MODE` را چک می‌کند، نه `_is_backup_running` ([dashboard.py:967-973](../src/dashboard.py#L967-L973)) ← ریستور می‌تواند وسط ساخت بکاپ شروع شود و بکاپ فایل‌هایی را بخواند که در حال حذف‌اند. check-and-set هم اتمیک نیست (دو درخواست هم‌زمان).
- اگر بکاپ خودکار شکست بخورد (مثلاً دیسک پر)، `last_backup_time` به‌روز نمی‌شود و **هر ۶۰ ثانیه** دوباره تلاش می‌کند — هر بار dump کامل دیتابیس‌ها ([dashboard.py:283-294](../src/dashboard.py#L283-L294)). نیاز به backoff و هشدار دارد.
- مشکل busy loop در [BUG-13](03-Backend-Bugs-and-Logic.md#bug-13).

---

<a id="gb-11"></a>
## GB-11 🟡 SSE و threadهای Gunicorn

**محل:** [dashboard.py:921-939](../src/dashboard.py#L921-L939)، [dashboard.py:1065-1107](../src/dashboard.py#L1065-L1107)

هر SSE باز یکی از **۸ thread** Gunicorn را تا پایان job نگه می‌دارد. stream بکاپ سقف زمانی ندارد؛ چند تب باز = threadهای کمتر برای ربات ← timeout در ربات. stream ریستور بعد از ۵ دقیقه «Restore timed out» می‌فرستد، در حالی که ریستور بزرگ هنوز ادامه دارد و فرانت حالت را خطا نشان می‌دهد. `EventSource` هم نمی‌تواند هدر API Key بفرستد، پس در حالت Cross-Server کار نمی‌کند.

**راه‌حل:** polling ساده با یک endpoint `GET .../progress` (JSON) هر ۱ ثانیه از فرانت؛ یا سقف زمانی بالا + heartbeat.

---

<a id="gb-12"></a>
## GB-12 🟡 بکاپ‌ها رمزنگاری‌نشده

**محل:** [GlobalBackup.py:51](../src/modules/GlobalBackup.py#L51)، [GlobalBackup.py:215-217](../src/modules/GlobalBackup.py#L215-L217)

هر بکاپ شامل همه‌چیز است: هش پسورد ادمین، `flask_secret_key`، کلید TOTP، API Keyها (متن ساده)، کلیدهای خصوصی سرور و همه‌ی کاربران. فایل‌ها با umask پیش‌فرض (معمولاً 644) در `src/GlobalBackups` ذخیره می‌شوند و این پوشه در `.gitignore` نیست (سه بکاپ تست در ریپو commit شده‌اند — سند ۰۱).

**راه‌حل:** `os.makedirs(..., mode=0o700)` و `os.chmod(zip, 0o600)`؛ افزودن به `.gitignore`؛ رمزنگاری اختیاری با passphrase و ارسال خودکار به جای امن ([FEAT-06](09-Feature-Proposals.md#feat-06)).

---

<a id="gb-13"></a>
## GB-13 🟢 محدودیت حجم و فایل باقی‌مانده

- `MAX_CONTENT_LENGTH` تنظیم نشده و `extractall` محدودیت حجم ندارد (zip bomb) — فقط ادمین، ولی بهتر است حد بگذارید ([GlobalBackup.py:471-478](../src/modules/GlobalBackup.py#L471-L478)).
- اگر بعد از ذخیره‌ی فایل آپلودی و قبل از شروع thread خطایی رخ دهد، فایل `uploaded_*.zip` باقی می‌ماند ([dashboard.py:990-993](../src/dashboard.py#L990-L993)، [dashboard.py:1060-1062](../src/dashboard.py#L1060-L1062)).

<a id="gb-14"></a>
## GB-14 🟢 `includeExistingBackups` نیمه‌کاره

بکاپ‌های per-config در `per_config_backups/` داخل zip قرار می‌گیرند ([GlobalBackup.py:314-319](../src/modules/GlobalBackup.py#L314-L319)) ولی ریستور هیچ‌وقت آن‌ها را برنمی‌گرداند. UI هم گزینه‌ای برایش ندارد.

<a id="gb-15"></a>
## GB-15 🟢 محدودیت `syncconf`

`wg-quick strip` + `wg syncconf` فقط کلید، پورت و peerها را اعمال می‌کند؛ اگر `Address`، `MTU` یا `PostUp` در بکاپ متفاوت باشد، تا ری‌استارت اینترفیس اعمال نمی‌شود. بهتر است در پایان ریستور این تفاوت‌ها گزارش شود.

<a id="gb-16"></a>
## GB-16 🟢 نسخه و سازگاری

`dashboard_version` در MANIFEST ثابت `"v4.3.3"` است ([GlobalBackup.py:353](../src/modules/GlobalBackup.py#L353)) و هنگام ریستور هیچ بررسی سازگاری schema انجام نمی‌شود. `EnsureDatabaseIntegrity` ستون‌های جاافتاده را اضافه می‌کند که خوب است، ولی ریستور بکاپ نسخه‌ی جدیدتر روی کد قدیمی‌تر بررسی نمی‌شود.

---

## جریان پیشنهادی ریستور ایمن

```
1. احراز هویت session ادمین (نه API Key) + تأیید
2. قفل سراسری: MAINTENANCE.set()  → منتظر idle شدن threadهای ۱، ۲، ۳ و پلاگین‌ها
3. بکاپ PreRestore از وضعیت فعلی (مستثنی از retention)
4. اعتبارسنجی zip: MANIFEST، sha256 همه‌ی فایل‌ها، نبود فایل اضافه، مسیرهای مجاز
5. ساخت دیتابیس‌ها در فایل موقت + PRAGMA integrity_check
6. dispose همه‌ی engineها → حذف -wal/-shm → os.replace
7. نوشتن اتمیک ini (بدون تغییر مسیرهای سرور) و .confها در مسیرهای فعلی
8. systemctl restart wg-dashboard   (maintenance تا لحظه‌ی خروج روشن می‌ماند)
```
