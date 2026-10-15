# ۰۶ — ارتباط ربات OxBot با پنل

> کد ربات بررسی‌شده: نسخه‌ی موجود در `scratch/` (`remote_WGDashboard.php` = فایل `WGDashboard.php` ربات، و `remote_panels.php` = `panels.php`). اگر نسخه‌ی فعلی ربات روی سرور با این‌ها فرق دارد، شماره خط‌ها را با آن تطبیق دهید.
> لینک‌های `scratch/` در این سند بعد از پاک کردن آن پوشه از ریپو (سند ۰۱) کار نمی‌کنند؛ شماره خط‌ها برای مرجع نگه داشته شده‌اند.

## نقشه‌ی ارتباط فعلی

| تابع ربات | endpoint پنل | کی صدا زده می‌شود |
|-----------|--------------|-------------------|
| `get_userwg` | `GET /api/getWireguardConfigurationInfo` (**کل** اینترفیس) | نمایش سرویس، تمدید، حجم/زمان اضافه، ویرایش، ریست، تأیید بعد از timeout ساخت |
| `getUsedIPs` / `getCachedSubnet` | همان بالا | هر خرید (subnet کش می‌شود ✅) |
| `addpear` | `POST /api/addPeers/<conf>` | خرید |
| `setjob` / `deletejob` | `POST /api/savePeerScheduleJob` / `deletePeerScheduleJob` | خرید، تمدید، حجم/زمان اضافه |
| `downloadconfig` | `GET /api/downloadPeer/<conf>?id=` (تا ۲۰ بار تلاش) | خرید، نمایش سرویس |
| `restrictPeers` / `allowAccessPeers` | `POST /api/restrictPeers/` / `allowAccessPeers/` | فعال/غیرفعال، قبل از اکثر عملیات |
| `ResetUserDataUsagewg` | `POST /api/resetPeerData/<conf>` | تمدید |
| `remove_userwg` | `allowAccessPeers` + `POST /api/deletePeers/` | حذف سرویس |
| `updatepear` | `POST /api/updatePeerSettings/<conf>` | تغییر نام/تنظیمات |

## جدول خلاصه

| ID | سمت | شدت | عنوان |
|----|------|------|-------|
| [BOT-01](#bot-01) | پنل + ربات | 🟠 | کاربر restrict‌شده در ربات «فعال» دیده می‌شود؛ دکمه‌ی فعال‌سازی کار نمی‌کند |
| [BOT-02](#bot-02) | پنل + ربات | 🟠 | هر عملیات روی یک کاربر، کل لیست peerها را می‌کشد ← فریز پنل |
| [BOT-03](#bot-03) | ربات | 🔴 | «حجم اضافه» بعد از اتمام حجم، مصرف را ریست می‌کند (حجم رایگان) |
| [BOT-04](#bot-04) | ربات + پنل | 🟠 | «زمان اضافه» برای کاربری که حجمش تمام شده دسترسی **نامحدود** می‌دهد |
| [BOT-05](#bot-05) | ربات | 🟠 | نتیجه‌ی `setjob`/`deletejob` بررسی نمی‌شود ← سرویس بدون محدودیت |
| [BOT-06](#bot-06) | پنل | 🟠 | شکنندگی job ها (BUG-02, BUG-05) مستقیماً محدودیت‌های فروش را از بین می‌برد |
| [BOT-07](#bot-07) | ربات + پنل | 🟡 | اختلاف منطقه‌ی زمانی در job تاریخ |
| [BOT-08](#bot-08) | ربات | 🟡 | جستجوی کاربر با `name` به‌جای public key |
| [BOT-09](#bot-09) | ربات | 🟡 | `remove_userwg` قبل از حذف، دسترسی را باز می‌کند |
| [BOT-10](#bot-10) | ربات + پنل | 🟡 | تمدید = ۶ عملیات سنگین و ۳ بار `wg-quick save` |
| [BOT-11](#bot-11) | ربات | 🟡 | `downloadconfig` تا ۱۱۰ ثانیه webhook را قفل می‌کند |
| [BOT-12](#bot-12) | ربات | 🟡 | DNS/MTU/Keepalive ثابت در ربات، تنظیمات پنل را نادیده می‌گیرد |
| [BOT-13](#bot-13) | ربات | 🟡 | ذخیره‌ی کلید خصوصی و JSON کامل در `invoice.user_info` |
| [BOT-14](#bot-14) | معماری | 🟠 | ربات دوستتان با API Key کامل به همین پنل وصل است |
| [BOT-15](#bot-15) | پنل | 🟡 | حذف «موفق» برای peer ناموجود و re-key خودکار، ربات را گمراه می‌کنند |

---

<a id="bot-01"></a>
## BOT-01 🟠 کاربر محدودشده «فعال» دیده می‌شود

**پنل:** [dashboard.py:1566](../src/dashboard.py#L1566) — peerهای restrict‌شده هم در `configurationPeers` هستند ([BUG-01](03-Backend-Bugs-and-Logic.md#bug-01)).
**ربات:** `scratch/remote_WGDashboard.php:44-60` — اول `configurationPeers` جستجو می‌شود و اولین تطبیق برگردانده می‌شود؛ شاخه‌ی `configurationRestrictedPeers` (که `configuration.Status = false` می‌گذارد) هرگز اجرا نمی‌شود.

**پیامد:**
- در `DataUser` (`remote_panels.php:871-873`) وضعیت کاربر غیرفعال‌شده «active» محاسبه می‌شود.
- در toggle (`remote_panels.php:1845-1849`): چون وضعیت «active» است، همیشه `restrictPeers` صدا زده می‌شود ← **کاربری که ادمین غیرفعال کرده، از طریق ربات دیگر فعال نمی‌شود**.

**اصلاح پنل:** `configurationPeers` فقط peerهای فعال (و افزودن فیلد `restricted` به JSON peer).
**اصلاح ربات (مستقل از پنل):**
```php
foreach ($configurationRestrictedPeers as $u) {          // اول محدودشده‌ها
    if ($u['id'] === $publicKey) { $u['configuration']['Status'] = false; return $u; }
}
foreach ($configurationPeers as $u) {
    if ($u['id'] === $publicKey) { return $u; }
}
```

---

<a id="bot-02"></a>
## BOT-02 🟠 کشیدن کل اینترفیس برای هر کاربر ← فریز پنل

**ربات:** `get_userwg` (`remote_WGDashboard.php:6-65`)، `getUsedIPs` (`:540-588`)
**پنل:** [dashboard.py:1556-1568](../src/dashboard.py#L1556-L1568)

برای دیدن وضعیت **یک** کاربر، ربات `getWireguardConfigurationInfo` را می‌گیرد که JSON **همه‌ی** peerها (فعال + محدود، با job ها و share linkها) را برمی‌گرداند — با ۱۶۰۰ کاربر حدود ۱.۵ تا ۲ مگابایت، به‌علاوه‌ی کار CPU برای ساختن آن در پنل (که با GIL بقیه‌ی درخواست‌ها را هم کند می‌کند).

اگر کرون‌های ربات (هشدار حجم، هشدار زمان، `cronvolumere` و ...) برای **هر** کاربر `DataUser` را صدا بزنند، هزینه از مرتبه‌ی **N²** است: ۱۶۰۰ کاربر × ۱.۶ مگابایت ≈ ۲.۵ گیگابایت JSON در هر دور کرون. commitهای «prevent freezing on bot polling» نشانه‌ی همین مشکل هستند.

**راه‌حل:**
- **پنل:** endpoint سبک برای یک peer و endpoint فشرده‌ی گروهی ([FEAT-01](09-Feature-Proposals.md#feat-01)).
- **ربات (بدون تغییر پنل):** در کرون، یک بار در ابتدای دور `getWireguardConfigurationInfo` را بگیرید و نتیجه را بر اساس `id` در یک آرایه ایندکس کنید؛ بعد برای هر کاربر از همان آرایه بخوانید. همچنین یک کش ۳۰ ثانیه‌ای (APCu/Redis/فایل) برای `get_userwg` در مسیرهای تعاملی.

---

<a id="bot-03"></a>
## BOT-03 🔴 «حجم اضافه» مصرف را ریست می‌کند

**ربات:** `remote_panels.php:2230-2231` و `:2285-2303` (تابع `extra_volume`)

```php
$new_limit = ($limit_volume_new * 1024^3) + $old_limit_volume;   // حجم جدید = قبلی + اضافه
...
foreach ($datauser['jobs'] as $jobsvolume) { if Field == "total_data" break; $count++; }
if (isset($datauser['jobs'][$count])) {
    deletejob(...);                         // job حجم پیدا شد ← حذف
} else {
    $this->ResetUserDataUsage(...);         // ← job پیدا نشد ← مصرف صفر می‌شود!
}
setjob(..., "total_data", $new_limit, ...);
```
وقتی حجم کاربر **تمام شده**، job حجم در پنل اجرا و **حذف** شده است ([PeerJobs.py:194-212](../src/modules/PeerJobs.py#L194-L212)). پس دقیقاً در رایج‌ترین حالت خرید حجم اضافه، شاخه‌ی `else` اجرا می‌شود: مصرف صفر و محدودیت `قبلی + اضافه` ← کاربر **کل حجم قبلی را رایگان** دوباره می‌گیرد.

مثال: بسته‌ی ۲۰ گیگ، کاربر ۲۰ گیگ مصرف کرده و ۵ گیگ اضافه می‌خرد ← محدودیت ۲۵ گیگ و مصرف ۰ ← ۲۵ گیگ قابل استفاده به‌جای ۵ گیگ.

**اصلاح ربات:** ریست را حذف کنید؛ فقط job جدید با `new_limit` ثبت شود (مصرف فعلی ~۲۰ گیگ می‌ماند، پس ۵ گیگ قابل استفاده است). اگر job قدیمی وجود داشت حذف شود:
```php
if (isset($datauser['jobs'][$count])) { deletejob(...); }
$log = setjob($panel['name_panel'], "total_data", $new_limit / pow(1024, 3), $datauser['id']);
```

---

<a id="bot-04"></a>
## BOT-04 🟠 «زمان اضافه» دسترسی نامحدود می‌دهد

**ربات:** `remote_panels.php:2407-2423` (تابع `extra_time`)

ربات بی‌قیدوشرط `allowAccessPeers` می‌زند، job تاریخ را عوض می‌کند و تمام. اگر کاربر به‌خاطر **اتمام حجم** محدود شده بود، job حجمش قبلاً اجرا و حذف شده؛ حالا دسترسی باز است و **هیچ job حجمی وجود ندارد** ← مصرف نامحدود تا تاریخ جدید.

همین الگو در `extend` (`:2128-2178`) هم هست: اگر `$new_limit == 0` یا `setjob` شکست بخورد، کاربر نامحدود می‌ماند.

**اصلاح:**
- ربات: قبل از `allowAccessPeers` بررسی کنید `restricted_reason` (در JSON peer هست) چیست؛ اگر «Volume Limit Reached» است و حجم اضافه نخریده، دسترسی را باز نکنید. بعد از تمدید زمان، اگر job حجم وجود ندارد ولی فاکتور حجم دارد، دوباره بسازید.
- پنل (ریشه‌ای): محدودیت‌ها به‌جای «job یک‌بارمصرف» به‌صورت ویژگی دائمی peer ذخیره شوند و `allowAccessPeers` اگر محدودیت هنوز رد شده، خطا بدهد ([FEAT-02](09-Feature-Proposals.md#feat-02)).

---

<a id="bot-05"></a>
## BOT-05 🟠 نتیجه‌ی `setjob` بررسی نمی‌شود

**ربات:** `remote_panels.php:337-342` (خرید)، `:2172-2178` (تمدید)، `:2153`, `:2169` (deletejob)

اگر `savePeerScheduleJob` به هر دلیلی شکست بخورد (timeout، پنل در حال ری‌استارت، maintenance ریستور که 503 برمی‌گرداند)، ربات کانفیگ را تحویل می‌دهد و سرویس **بدون محدودیت حجم و زمان** فعال می‌ماند. همچنین اگر `deletejob` شکست بخورد و `setjob` موفق شود، دو job حجم هم‌زمان وجود دارد و کاربر زودتر از موعد قطع می‌شود.

**اصلاح ربات:** پاسخ را بررسی کنید (`status == 200` و `body.status === true`)، در صورت شکست تا ۳ بار تلاش و در نهایت peer را restrict و به ادمین گزارش دهید.
**اصلاح پنل:** ساخت peer و محدودیت‌ها در یک درخواست اتمیک ([FEAT-03](09-Feature-Proposals.md#feat-03)).

---

<a id="bot-06"></a>
## BOT-06 🟠 job ها پایه‌ی فروش شما هستند و شکننده‌اند

همه‌ی محدودیت‌های فروش (حجم، تاریخ) به‌صورت job پنل پیاده شده‌اند. سه باگ پنل مستقیماً باعث از بین رفتن این محدودیت‌ها می‌شود:
- [BUG-02](03-Backend-Bugs-and-Logic.md#bug-02): یک job با `Value` نامعتبر ← هیچ job دیگری اجرا نمی‌شود.
- [BUG-05](03-Backend-Bugs-and-Logic.md#bug-05): job بعد از ۱۱ شکست بی‌صدا پاک می‌شود.
- [BUG-06](03-Backend-Bugs-and-Logic.md#bug-06): race هنگام ثبت job توسط ربات هم‌زمان با اجرای حلقه.

**پایش پیشنهادی (تا رفع):** روزانه این کوئری را اجرا کنید و با تعداد سرویس‌های فعال ربات مقایسه کنید:
```bash
sqlite3 src/db/wgdashboard_job.db \
 "SELECT Configuration, Field, COUNT(*) FROM PeerJobs WHERE ExpireDate IS NULL GROUP BY 1,2;"
sqlite3 src/db/wgdashboard_log.db \
 "SELECT Message, COUNT(*) FROM JobLog WHERE Message LIKE '%stale%' GROUP BY 1;"
```

---

<a id="bot-07"></a>
## BOT-07 🟡 منطقه‌ی زمانی job تاریخ

**ربات:** `date('Y-m-d H:i:s', $expire)` (`remote_panels.php:341`, `:2174`, `:2423`) — در timezone پیش‌فرض PHP سرور ربات.
**پنل:** `datetime.now()` در timezone سرور پنل ([PeerJobs.py:173-176](../src/modules/PeerJobs.py#L173-L176)).

اگر سرور ربات `Asia/Tehran` (+03:30) و سرور پنل UTC باشد، همه‌ی سرویس‌ها **۳.۵ ساعت دیرتر** قطع می‌شوند (و برعکس، زودتر). بررسی:
```bash
timedatectl | grep "Time zone"                     # روی سرور پنل
php -r 'echo date_default_timezone_get(), PHP_EOL;'  # روی سرور ربات
```
**اصلاح:** هر دو را یکسان کنید یا (بهتر) ربات timestamp یونیکس/ISO-8601 با offset بفرستد و پنل در UTC مقایسه کند.

---

<a id="bot-08"></a>
## BOT-08 🟡 جستجو با `name`

**ربات:** `remote_WGDashboard.php:44-60` — `if ($userinfo['name'] == $username)`

نام peer در پنل قابل ویرایش است و یکتا نیست. اگر ادمین نام را عوض کند، ربات کاربر را پیدا نمی‌کند؛ اگر دو peer هم‌نام باشند (ساخت مجدد با همان username)، اولی برگردانده می‌شود. در حالی که public key در `invoice.user_info` ذخیره است و `restrict/allow/delete` ربات از همان استفاده می‌کنند.

**اصلاح:** `get_userwg` با public key (از `user_info['public_key']`) جستجو کند و `name` فقط fallback باشد.

---

<a id="bot-09"></a>
## BOT-09 🟡 `remove_userwg` اول دسترسی را باز می‌کند

**ربات:** `remote_WGDashboard.php:465-467`

قبل از حذف، `allowAccessPeers` صدا زده می‌شود ← peer به kernel برمی‌گردد (کاربر چند لحظه دسترسی دارد) و یک `wg-quick save` اضافه. پنل فعلی peer محدودشده را مستقیم حذف می‌کند ([WireguardConfiguration.py:973-979](../src/modules/WireguardConfiguration.py#L973-L979)) و مصرفش را هم در Total نگه می‌دارد، پس این خط لازم نیست و باید حذف شود.

---

<a id="bot-10"></a>
## BOT-10 🟡 تمدید سنگین است

مسیر تمدید (`remote_panels.php:2127-2178` + `ResetUserDataUsage` در `:1965-1968`):
`allowAccess` ← `get_userwg` (کل اینترفیس) ← `resetPeerData` (که خودش در پنل `restrict` + `allow` می‌کند: [dashboard.py:1237-1239](../src/dashboard.py#L1237-L1239)) ← `allowAccess` ← `get_userwg` ← `deletejob` ×۲ ← `setjob` ×۲.

یعنی حدود ۱۰ درخواست HTTP، دو بار دانلود کل اینترفیس و **۳-۴ بار `wg-quick save`** برای یک تمدید. با endpoint ترکیبی [FEAT-03](09-Feature-Proposals.md#feat-03) (`renew`: ریست + محدودیت جدید + allow در یک تراکنش) به ۱ درخواست می‌رسد.

---

<a id="bot-11"></a>
## BOT-11 🟡 `downloadconfig` تا ۱۱۰ ثانیه

**ربات:** `remote_WGDashboard.php:92-116`

۲۰ تلاش × (timeout ۵ ثانیه + ۰.۵ ثانیه) برای هر پاسخ غیر-true. در پنل فعلی peer بلافاصله بعد از پاسخ `addPeers` در دیتابیس هست (درج هم‌زمان)، پس این همه تلاش لازم نیست و فقط وقتی پنل کند است، webhook تلگرام را قفل می‌کند (تلگرام بعد از ~۶۰ ثانیه دوباره همان update را می‌فرستد ← پردازش تکراری). پیشنهاد: حداکثر ۳ تلاش با backoff.

---

<a id="bot-12"></a>
## BOT-12 🟡 تنظیمات ثابت در ربات

**ربات:** `remote_WGDashboard.php:309-320` و `remote_panels.php:1682-1685`

`DNS = 1.1.1.1`، `endpoint_allowed_ip = 0.0.0.0/0`، `mtu = 1420`، `keepalive = 21` ثابت فرستاده می‌شوند و تنظیمات پیش‌فرض Peer پنل نادیده گرفته می‌شوند. البته در کانفیگ دانلودی، اگر در «Override Peer Settings» اینترفیس مقداری گذاشته باشید، آن اولویت دارد ([Peer.py:364-381](../src/modules/Peer.py#L364-L381)) — پس برای تغییر DNS/MTU همه‌ی کاربران از Override استفاده کنید، یا این فیلدها را از درخواست ربات حذف کنید تا پیش‌فرض پنل اعمال شود.

---

<a id="bot-13"></a>
## BOT-13 🟡 داده‌ی حساس در دیتابیس ربات

ربات کل JSON پاسخ (شامل `private_key` و `preshared_key` کاربر) را در `invoice.user_info` نگه می‌دارد (`remote_panels.php:345`). نسخه‌های قبلی کلید خصوصی **سرور** را هم ذخیره کرده‌اند (سند ۰۱). پیشنهاد: فقط `public_key`، `allowed_ip` و نام را ذخیره کنید و کانفیگ را هر بار از پنل بگیرید؛ و رکوردهای قدیمی را از فیلد `configuration` پاک کنید:
```sql
UPDATE invoice SET user_info = JSON_REMOVE(user_info, '$.configuration')
WHERE JSON_EXTRACT(user_info, '$.configuration') IS NOT NULL;
```

---

<a id="bot-14"></a>
## BOT-14 🟠 ربات دوستتان با دسترسی کامل

هر API Key در این پنل معادل ادمین کامل است ([SEC-03](02-Security-Vulnerabilities.md#sec-03)). ربات دوستتان می‌تواند:
- peerهای اینترفیس **شما** را بخواند، حذف یا محدود کند،
- API Key ربات شما را ببیند و کلیدها را حذف کند،
- با `PostUp` به root سرور برسد.

اگر به او اعتماد کامل ندارید یا سرور ربات او ممکن است هک شود، این یک ریسک جدی است. راه‌حل: API Key با scope اینترفیس ([FEAT-05](09-Feature-Proposals.md#feat-05)) یا دست‌کم allowlist در SEC-03.

---

<a id="bot-15"></a>
## BOT-15 🟡 پاسخ‌های گمراه‌کننده‌ی پنل

- `deletePeers` برای id ناموجود `status: true` می‌دهد ([BUG-04](03-Backend-Bugs-and-Logic.md#bug-04)) ← ربات فکر می‌کند حذف شد.
- Auto-heal ممکن است کلید peer را عوض کند ([BUG-03](03-Backend-Bugs-and-Logic.md#bug-03)) ← کلید ذخیره‌شده در ربات دیگر معتبر نیست و restrict/delete/reset شکست می‌خورند (یا با BUG-04 «موفق» گزارش می‌شوند).
- `restrictPeers` برای کاربری که از قبل محدود است پیام موفق می‌دهد ولی `restricted_reason` را بازنویسی می‌کند.

---

## قرارداد پیشنهادی بعد از اصلاحات

| عملیات ربات | الان | پیشنهاد |
|-------------|------|---------|
| وضعیت یک کاربر | `getWireguardConfigurationInfo` (کل) | `GET /api/v2/peer?configurationName=wg0&id=<pubkey>` |
| کرون مصرف همه | N × کل اینترفیس | `GET /api/v2/peers/usage?configurationName=wg0` یک بار در هر دور |
| خرید | `addPeers` + ۲× `savePeerScheduleJob` + `downloadPeer` (با retry) | `POST /api/v2/peers` با `limits` و `Idempotency-Key`؛ پاسخ شامل فایل کانفیگ |
| تمدید | ~۱۰ درخواست | `POST /api/v2/peer/renew` {reset, data_limit_gb, expire_at} |
| حجم/زمان اضافه | حذف/ساخت job + ریست اشتباه | `POST /api/v2/peer/limits` {add_data_gb / expire_at} |
| اطلاع از اتمام حجم/زمان | polling کرون | webhook `peer_restricted` ([FEAT-04](09-Feature-Proposals.md#feat-04)) |

جزئیات در [سند ۰۹](09-Feature-Proposals.md).
