# ۰۷ — کارایی و پایداری

> معماری: یک پروسه‌ی Gunicorn با ۸ thread ([gunicorn.conf.py:27-35](../src/gunicorn.conf.py#L27-L35)) + سه thread پس‌زمینه + پلاگین‌ها. همه در یک GIL. هر کار سنگین CPU در یک thread، بقیه (از جمله درخواست‌های ربات) را کند می‌کند.

## کارهای خوبی که انجام شده
- Debounce پنج‌ثانیه‌ای `wg show dump` ([WireguardConfiguration.py:1281-1285](../src/modules/WireguardConfiguration.py#L1281-L1285)) و کش ۳ ثانیه‌ای `_compute_data_usage`.
- به‌روزرسانی‌های دسته‌ای با `bindparam` به‌جای یک UPDATE برای هر peer.
- افزودن peer به‌صورت تراکنشی با rollback در kernel ([WireguardConfiguration.py:700-777](../src/modules/WireguardConfiguration.py#L700-L777)).
- Reconcile دوره‌ای دیتابیس ↔ kernel هر ۵ دقیقه.
- هرس دوره‌ای لاگ‌ها و WAL checkpoint ([dashboard.py:147-223](../src/dashboard.py#L147-L223)).
- WAL و `synchronous=NORMAL` برای SQLite.

## جدول خلاصه

| ID | شدت | عنوان |
|----|------|-------|
| [PERF-01](#perf-01) | 🟠 | بازسازی همه‌ی اشیای Peer هر ۱۰ ثانیه با هزینه‌ی O(P×J) |
| [PERF-02](#perf-02) | 🟠 | پاسخ سنگین `getWireguardConfigurationInfo` برای ربات (BOT-02) |
| [PERF-03](#perf-03) | 🟡 | لیست کانفیگ‌ها در هر درخواست اشیا را دوباره می‌سازد |
| [PERF-04](#perf-04) | 🟡 | ده‌ها engine دیتابیس که هرگز بسته نمی‌شوند |
| [PERF-05](#perf-05) | 🟡 | `busy_timeout` شصت‌ثانیه‌ای ← قفل شدن threadها |
| [PERF-06](#perf-06) | 🟡 | اجرای job ها یکی‌یکی با `wg-quick save` جدا |
| [PERF-07](#perf-07) | 🟡 | Self-heal زمان اجرا فایل دیتابیس را زیر پای connectionها عوض می‌کند |
| [PERF-08](#perf-08) | 🟡 | درخواست‌های طولانی (SSE، ping، traceroute) threadها را اشغال می‌کنند |
| [PERF-09](#perf-09) | 🟢 | رشد بی‌پایان جداول tracking |
| [PERF-10](#perf-10) | 🟢 | `logPeersHistoryEndpoint` هر دقیقه همه‌ی تاریخچه را می‌خواند |
| [PERF-11](#perf-11) | 🟢 | قفل‌های بدون مدیریت `TimeoutError` |

---

<a id="perf-01"></a>
## PERF-01 🟠 بازسازی Peerها با هزینه‌ی O(P×J)

**محل:** [WireguardConfiguration.py:533-538](../src/modules/WireguardConfiguration.py#L533-L538)، [Peer.py:56-59](../src/modules/Peer.py#L56-L59)، [Peer.py:509-513](../src/modules/Peer.py#L509-L513)، [PeerJobs.py:68-69](../src/modules/PeerJobs.py#L68-L69)، [PeerShareLinks.py:40-41](../src/modules/PeerShareLinks.py#L40-L41)، thread در [dashboard.py:116-125](../src/dashboard.py#L116-L125)

`getPeers()` هر بار همه‌ی ردیف‌ها را می‌خواند و برای هر کدام یک `Peer` جدید می‌سازد. سازنده‌ی `Peer` دو جستجوی **خطی** روی کل لیست job ها و share linkها انجام می‌دهد:
```python
self.jobs = list(filter(lambda x: x.Configuration == C and x.Peer == P, self.Jobs))
```
با ۱۶۰۰ peer و ~۳۲۰۰ job (دو job برای هر کاربر) یعنی حدود **۵ میلیون** فراخوانی lambda در هر `getPeers` — تقریباً نیم تا یک ثانیه CPU با GIL، هر ۱۰ ثانیه، به‌علاوه‌ی بعد از هر add/delete/restrict/allow. این مستقیماً پاسخ‌دهی به ربات را کند می‌کند.

**راه‌حل:** ایندکس دیکشنری:
```python
# در PeerJobs
def __rebuild_index(self):
    idx = {}
    for j in self.Jobs:
        idx.setdefault((j.Configuration, j.Peer), []).append(j)
    self._index = idx
def searchJob(self, Configuration, Peer):
    return list(self._index.get((Configuration, Peer), []))
```
(همین برای `PeerShareLinks.getLink`.) بهتر از آن: به‌جای ساخت مجدد همه‌ی اشیا، فقط ردیف‌های تغییرکرده را به‌روز کنید.

---

<a id="perf-02"></a>
## PERF-02 🟠 پاسخ سنگین برای ربات

به [BOT-02](06-OxBot-Integration.md#bot-02) و [FEAT-01](09-Feature-Proposals.md#feat-01) مراجعه کنید. خلاصه: هر بار JSON کل اینترفیس (بیش از ۱.۵ مگابایت) ساخته می‌شود و peerهای محدودشده **دو بار** در آن هستند ([BUG-01](03-Backend-Bugs-and-Logic.md#bug-01)) که حجم را بیشتر می‌کند. `toJson` اینترفیس هم در هر فراخوانی `psutil.net_if_addrs()` را صدا می‌زند ([WireguardConfiguration.py:398-401](../src/modules/WireguardConfiguration.py#L398-L401)).

---

<a id="perf-03"></a>
## PERF-03 🟡 بازسازی اشیای کانفیگ در `getWireguardConfigurations`

**محل:** [dashboard.py:551-554](../src/dashboard.py#L551-L554)، [dashboard.py:313-356](../src/dashboard.py#L313-L356)، [WireguardConfiguration.py:434-438](../src/modules/WireguardConfiguration.py#L434-L438)

هر درخواست صفحه‌ی اصلی (که فرانت به‌طور دوره‌ای می‌زند) `InitWireguardConfigurationsList` را صدا می‌زند. اگر `mtime` فایل `.conf` تغییر کرده باشد (که بعد از **هر** `wg-quick save` هست)، شیء کانفیگ **کاملاً از نو ساخته می‌شود**: inspector دیتابیس، `create_all`، parse، بارگذاری همه‌ی peerها، auto-heal ([BUG-03](03-Backend-Bugs-and-Logic.md#bug-03))، خواندن Info — آن هم زیر قفل سراسری `WireguardConfigurationsLock` که thread پس‌زمینه را هم متوقف می‌کند. اینکه کدام thread اول `configurationFileChanged()` را صدا بزند (که حالت داخلی را به‌روز می‌کند) تصادفی است.

**راه‌حل:** فقط کانفیگ‌های جدید/حذف‌شده را اضافه/حذف کنید و برای فایل تغییرکرده `obj.reload()` درجا (بدون ساخت شیء جدید) داشته باشید.

---

<a id="perf-04"></a>
## PERF-04 🟡 Engineهای بی‌شمار

**محل:** [WireguardConfiguration.py:90](../src/modules/WireguardConfiguration.py#L90)، [Peer.py:284](../src/modules/Peer.py#L284)، [Peer.py:294](../src/modules/Peer.py#L294)، [DatabaseConnection.py:243-283](../src/modules/DatabaseConnection.py#L243-L283) و سازنده‌ی تقریباً همه‌ی کلاس‌ها

هر کلاس (و هر بار ساخت مجدد هر کانفیگ در PERF-03، و هر فراخوانی `autoHealKeys`) یک `create_engine` جدید با pool خودش دارد که هیچ‌وقت `dispose` نمی‌شود. `ConnectionString` هم در هر فراخوانی فایل ini را از دیسک parse می‌کند. پیامدها: نشت file descriptor در طول زمان، و غیرممکن شدن بستن تمیز همه‌ی connectionها هنگام ریستور ([GB-01](05-Global-Backup-Review.md#gb-01)).

**راه‌حل:**
```python
_ENGINES = {}
_ENGINES_LOCK = threading.Lock()
def get_engine(db_name):
    with _ENGINES_LOCK:
        if db_name not in _ENGINES:
            _ENGINES[db_name] = sqlalchemy.create_engine(ConnectionString(db_name))
        return _ENGINES[db_name]
def dispose_all():
    with _ENGINES_LOCK:
        for e in _ENGINES.values(): e.dispose()
        _ENGINES.clear()
```

---

<a id="perf-05"></a>
## PERF-05 🟡 انتظار ۶۰ ثانیه‌ای روی قفل SQLite

**محل:** [DatabaseConnection.py:28](../src/modules/DatabaseConnection.py#L28)، [DatabaseConnection.py:269](../src/modules/DatabaseConnection.py#L269)

`busy_timeout=60000` یعنی اگر یک نوشتن طولانی (مثلاً `VACUUM` در [WireguardConfiguration.py:2090-2093](../src/modules/WireguardConfiguration.py#L2090-L2093)، هرس لاگ‌ها، یا ریستور) قفل را نگه دارد، هر ۸ thread تا ۶۰ ثانیه منتظر می‌مانند. timeout ربات ۱۵ ثانیه است ← ربات دوباره تلاش می‌کند ← درخواست‌ها انباشته می‌شوند ← «فریز». پیشنهاد: ۵-۱۰ ثانیه و خطای سریع؛ `VACUUM` فقط در پنجره‌ی نگهداری.

---

<a id="perf-06"></a>
## PERF-06 🟡 اجرای job ها یکی‌یکی

**محل:** [PeerJobs.py:185-193](../src/modules/PeerJobs.py#L185-L193) ← [WireguardConfiguration.py:887-955](../src/modules/WireguardConfiguration.py#L887-L955)

برای هر کاربری که محدود می‌شود: refresh کامل آمار، `wg set`، تراکنش، `wg-quick save`، `getPeers` کامل (PERF-01) و `getRestrictedPeers`. بعد از یک قطعی که ۱۰۰ کاربر هم‌زمان منقضی شده‌اند، این ۱۰۰ بار تکرار می‌شود. همه‌ی کاربران یک کانفیگ را در یک فراخوانی `restrictPeers([...])` جمع کنید.

---

<a id="perf-07"></a>
## PERF-07 🟡 Self-heal در زمان اجرا

**محل:** [DatabaseConnection.py:198-212](../src/modules/DatabaseConnection.py#L198-L212)، فراخوانی از [WireguardConfiguration.py:761-769](../src/modules/WireguardConfiguration.py#L761-L769)

اگر هنگام افزودن peer خطای «malformed» رخ دهد، `heal_database` فایل دیتابیس را با نسخه‌ی بازسازی‌شده جایگزین می‌کند **در حالی که** همه‌ی engineها connection باز به فایل قدیمی دارند ← نوشته‌های بعدی به فایل حذف‌شده می‌روند و با ری‌استارت گم می‌شوند. همچنین بازیابی «ردیف‌به‌ردیف» در اولین صفحه‌ی خراب متوقف می‌شود ([DatabaseConnection.py:152-162](../src/modules/DatabaseConnection.py#L152-L162)) و بقیه‌ی ردیف‌ها بی‌صدا از دست می‌روند؛ peerهای گم‌شده بعد از parse بعدی `.conf` با BUG-03 کلید جدید می‌گیرند.

**راه‌حل:** در زمان اجرا فقط حالت maintenance + هشدار؛ heal فقط هنگام boot قبل از ساخت engineها، و فقط وقتی تعداد ردیف‌های بازیابی‌شده با تخمین برابر است (وگرنه توقف و درخواست دخالت دستی).

---

<a id="perf-08"></a>
## PERF-08 🟡 درخواست‌های طولانی

- SSE بکاپ/ریستور ([GB-11](05-Global-Backup-Review.md#gb-11)).
- `ping` با `count` بدون سقف ([dashboard.py:1797-1804](../src/dashboard.py#L1797-L1804)) و `traceroute` با `max_hops=64` ([dashboard.py:1833](../src/dashboard.py#L1833)).

با فقط ۸ thread، چند تب باز Global Backup یا یک ping طولانی کافی است تا درخواست‌های ربات در صف بمانند. سقف بگذارید (`count ≤ 20`) یا این‌ها را async کنید.

---

<a id="perf-09"></a>
## PERF-09 🟢 رشد جداول tracking

**محل:** [WireguardConfiguration.py:569-619](../src/modules/WireguardConfiguration.py#L569-L619)، [WireguardConfigurationInfo.py:22-23](../src/modules/WireguardConfigurationInfo.py#L22-L23)

اگر `peer_tracking` روشن شود، برای هر peer فعال هر دقیقه یک ردیف در `<conf>_transfer` ثبت می‌شود (۱۰۰۰ کاربر آنلاین ≈ ۱.۴ میلیون ردیف در روز) و هیچ هرسی برای این جدول وجود ندارد. اگر این قابلیت را روشن کرده‌اید، retention (مثلاً ۳۰ روز) یا تجمیع ساعتی اضافه کنید.

---

<a id="perf-10"></a>
## PERF-10 🟢 `logPeersHistoryEndpoint`

**محل:** [WireguardConfiguration.py:588-619](../src/modules/WireguardConfiguration.py#L588-L619)

هر دقیقه همه‌ی ردیف‌های تاریخچه‌ی peerهای آنلاین (با `IN` بزرگ) خوانده می‌شود تا تکراری‌ها حذف شوند. یک ایندکس یکتا روی `(id, endpoint)` و `INSERT OR IGNORE` همین کار را بدون خواندن انجام می‌دهد.

---

<a id="perf-11"></a>
## PERF-11 🟢 `TimeoutError` قفل‌ها

**محل:** [Utilities.py:195-240](../src/modules/Utilities.py#L195-L240)

`ProcessLock` بعد از ۳۰ ثانیه `TimeoutError` می‌دهد. در endpointها مدیریت نمی‌شود (خطای 500 خام) و در `runJob` کل حلقه را متوقف می‌کند ([BUG-02](03-Backend-Bugs-and-Logic.md#bug-02)). یک handler سراسری `@app.errorhandler(TimeoutError)` با پاسخ 503 و پیام «busy, retry» برای ربات بهتر است.
