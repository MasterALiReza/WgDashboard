# ۰۴ — بررسی اصلاحیه‌ی Total Usage اینترفیس

## سؤال شما
> «قبلاً وقتی کاربران منقضی را حذف می‌کردم، از Total Usage اینترفیس کم می‌شد. با Antigravity درستش کردم؛ آیا درست شده؟»

## پاسخ کوتاه
**بله، در اصل درست شده.** حذف peer، ریست مصرف، restrict/allow و ری‌استارت اینترفیس دیگر Total Usage را کم نمی‌کنند. ولی **۳ اشکال واقعی** و چند نکته‌ی جزئی پیدا شد:

| ID | شدت | خلاصه |
|----|------|-------|
| [TU-01](#tu-01) | 🟠 | در حذف peer با `total_data = 0`، مصرف تجمعی **دو بار** به Total اضافه می‌شود (Total بیش از واقع) |
| [TU-02](#tu-02) | 🟠 | ریستور بکاپِ یک کانفیگ همیشه شکست می‌خورد و اینترفیس خاموش می‌ماند |
| [TU-03](#tu-03) | 🟡 | مصرف کاربرانی که **قبل از** اصلاحیه حذف شده‌اند برنمی‌گردد |
| [TU-04](#tu-04) | 🟢 | به‌روزرسانی snapshot به‌صورت read-modify-write است (فعلاً به‌خاطر lock امن) |
| [TU-05](#tu-05) | 🟢 | Total هرگز صفر نمایش داده نمی‌شود (حتی وقتی واقعاً صفر است) |
| [TU-06](#tu-06) | 🟢 | Race نظری بین refresh پس‌زمینه و ریست مصرف |

---

## Total Usage الان چطور حساب می‌شود؟

**محل:** [WireguardConfiguration.py:1550-1618](../src/modules/WireguardConfiguration.py#L1550-L1618)

```
Total   = Σ(cumu_data + total_data)  روی peerهای فعال
        + Σ(cumu_data + total_data)  روی peerهای restrict‌شده
        + snapshot.total_data         ← مصرف peerهای حذف‌شده/ریست‌شده
```
(همین برای `Sent` و `Receive` جداگانه.)

| ستون | معنی |
|------|------|
| `total_*` | مقدار فعلی شمارنده‌ی kernel (از آخرین ری‌استارت اینترفیس یا آخرین re-add peer) |
| `cumu_*` | مجموع شمارنده‌های قبلی که به‌خاطر ریست kernel «جمع» شده‌اند (می‌تواند منفی باشد؛ پایین را ببینید) |
| `*_traffic_snapshot` | جدول یک‌ردیفه برای هر اینترفیس؛ هر بار که peer حذف یا ریست می‌شود، مصرف آن peer اینجا اضافه می‌شود |

## بررسی سناریو به سناریو

| سناریو | چه اتفاقی می‌افتد | وضعیت |
|--------|-------------------|--------|
| مصرف عادی | هر ۱۰ ثانیه `wg show dump` خوانده و `total_*` به‌روز می‌شود ([1363-1395](../src/modules/WireguardConfiguration.py#L1363-L1395)) | ✅ |
| ری‌استارت اینترفیس/سرور (شمارنده‌ی kernel صفر می‌شود) | تشخیص با `stored*0.999 > current` ← `cumu += total` و `total = 0` ([1368-1383](../src/modules/WireguardConfiguration.py#L1368-L1383)) | ✅ (حداکثر ۱۰ ثانیه ترافیک قبل از ری‌استارت گم می‌شود) |
| Restrict کاربر | ردیف با همان مقادیر به جدول `_restrict_access` منتقل می‌شود و آنجا هم جمع زده می‌شود؛ قبلش `_force_refresh_stats` | ✅ |
| Allow دوباره | peer با شمارنده‌ی صفر به kernel برمی‌گردد ← rollover بالا | ✅ |
| **حذف کاربر** (ربات، UI یا job با Action=delete) | `cumu + total` peer به snapshot اضافه می‌شود ([1031-1065](../src/modules/WireguardConfiguration.py#L1031-L1065)) | ✅ به‌جز [TU-01](#tu-01) |
| **ریست مصرف** (تمدید در ربات) | مصرف کل peer به snapshot می‌رود و `cumu = -current_kernel` می‌شود تا مصرف peer صفر دیده شود ([Peer.py:545-587](../src/modules/Peer.py#L545-L587)) | ✅ |
| ریست + restrict/allow که `API_resetPeerData` انجام می‌دهد ([dashboard.py:1230-1239](../src/dashboard.py#L1230-L1239)) | بعد از re-add: `cumu = -cur + cur = 0`، `total = 0` | ✅ (از نظر عددی سازگار) |
| ریست partial (`receive` یا `sent`) | فقط همان جهت به snapshot می‌رود؛ `cumu_data` درست بازمحاسبه می‌شود | ✅ |
| Rename کانفیگ | جدول snapshot کپی و `configuration_name` به‌روز می‌شود ([1844-1850](../src/modules/WireguardConfiguration.py#L1844-L1850)) | ✅ |
| Global restore | جدول snapshot در dump هست و برمی‌گردد | ✅ |
| ریستور per-configuration | شکست | ❌ [TU-02](#tu-02) |
| حذف کل کانفیگ | جداول drop می‌شوند (انتظار می‌رود) | — |
| peer خراب با Auto-heal (کلید عوض می‌شود) | id ردیف عوض می‌شود ولی ترافیک می‌ماند | ✅ (ولی BUG-03 را ببینید) |

> **چرا `cumu` منفی می‌شود؟** بعد از ریست، شمارنده‌ی kernel هنوز مقدار قبلی (مثلاً ۸ گیگ) را دارد. برای اینکه مصرف کاربر صفر دیده شود، `cumu = -8` گذاشته می‌شود؛ `cumu + total = -8 + 8 = 0`. وقتی کاربر ۱ گیگ مصرف کند، `-8 + 9 = 1`. این ترفند درست است و فرانت هم همیشه `cumu + total` را نشان می‌دهد ([peer.vue:64-68](../src/static/app/src/components/configurationComponents/peer.vue#L64-L68)) و ربات هم همین را حساب می‌کند. فقط اگر جایی مستقیم `cumu_data` یا `total_data` را جدا نمایش دهید، عدد عجیب می‌بینید.

---

<a id="tu-01"></a>
## TU-01 🟠 دو بار شمردن مصرف در حذف peer

**محل:** [WireguardConfiguration.py:1046-1049](../src/modules/WireguardConfiguration.py#L1046-L1049)

```python
peer_total_receive = cumu_receive + total_receive
peer_total_sent    = cumu_sent    + total_sent
peer_total_data    = cumu_data + (total_data or (peer_total_receive + peer_total_sent))
#                                              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   اگر total_data == 0 باشد، fallback شامل cumu هم هست ← cumu دو بار جمع می‌شود
```

**کی رخ می‌دهد؟** هر وقت `total_data` یک peer دقیقاً صفر باشد ولی `cumu` غیرصفر:
- سرور/اینترفیس ری‌استارت شده (rollover: `cumu = U`، `total = 0`) و کاربر بعد از آن دیگر وصل نشده — دقیقاً وضعیت رایج **کاربران منقضی** که ربات حذفشان می‌کند.
- کاربر restrict شده، بعد allow شده (rollover) و دیگر مصرف نکرده.

**مثال عددی:** کاربر ۱۰ گیگ مصرف کرده، سرور ریبوت شده، کاربر دیگر وصل نشده:

| | قبل از حذف | snapshot اضافه‌شده | باید باشد |
|---|---|---|---|
| Receive | 4 | +4 | +4 ✅ |
| Sent | 6 | +6 | +6 ✅ |
| **Total** | 10 | **+20** | +10 ❌ |

**نشانه روی سرور شما:** `Total > Sent + Receive` در کارت اینترفیس.

**بررسی روی سرور:**
```bash
sqlite3 /path/to/src/db/wgdashboard.db \
 'SELECT configuration_name, total_receive, total_sent, total_data,
         ROUND(total_data - (total_receive + total_sent), 4) AS drift
  FROM "wg0_traffic_snapshot";'
```
(به‌جای `wg0` نام اینترفیس خودتان را بگذارید.) اگر `drift > 0` است، این باگ اتفاق افتاده.

**اصلاح کد:**
```python
peer_total_receive = float(db_row.get('cumu_receive') or 0.0) + float(db_row.get('total_receive') or 0.0)
peer_total_sent    = float(db_row.get('cumu_sent') or 0.0)    + float(db_row.get('total_sent') or 0.0)
peer_total_data    = peer_total_receive + peer_total_sent
```

**اصلاح داده‌ی موجود (بعد از رفع کد و با بکاپ قبلی):**
```sql
UPDATE "wg0_traffic_snapshot" SET total_data = total_receive + total_sent;
```

---

<a id="tu-02"></a>
## TU-02 🟠 ریستور بکاپ per-configuration همیشه شکست می‌خورد

**محل:** [WireguardConfiguration.py:1719](../src/modules/WireguardConfiguration.py#L1719) — جزئیات کامل در [BUG-07](03-Backend-Bugs-and-Logic.md#bug-07).

بکاپ‌های per-config (دکمه‌ی Backup در صفحه‌ی هر کانفیگ) جدول snapshot را هم dump می‌کنند ✅، ولی در ریستور، به‌خاطر `restore=True` جداول drop نمی‌شوند و INSERT ردیف snapshot (کلید اصلی `configuration_name`) و peerها خطای `UNIQUE constraint failed` می‌دهد. کل ریستور rollback می‌شود و **اینترفیس خاموش می‌ماند**. توجه: این را با «نادیده گرفتن خطای INSERT» رفع نکنید؛ جدول `_transfer` کلید اصلی ندارد و ردیف‌هایش تکراری می‌شوند. راه درست همان drop + import (رفتار قبلی) است.

---

<a id="tu-03"></a>
## TU-03 🟡 مصرف کاربران حذف‌شده‌ی قبل از اصلاحیه

جدول snapshot فقط از زمان اصلاحیه پر می‌شود. مصرف کاربرانی که قبل از آن حذف شده‌اند **برای همیشه** از Total کم شده است. اگر عدد واقعی را از جایی دیگر دارید (مثلاً جمع مصرف فاکتورهای ربات، یا آمار شبکه‌ی هاستینگ)، می‌توانید اختلاف را یک‌بار دستی اضافه کنید:
```sql
-- مثال: 1234.5 گیگ (Receive) و 4321.0 گیگ (Sent) گم‌شده
UPDATE "wg0_traffic_snapshot"
SET total_receive = total_receive + 1234.5,
    total_sent    = total_sent    + 4321.0,
    total_data    = total_data    + 1234.5 + 4321.0;
-- اگر ردیفی وجود ندارد:
INSERT INTO "wg0_traffic_snapshot"(configuration_name,total_receive,total_sent,total_data)
VALUES('wg0',1234.5,4321.0,5555.5);
```
پیشنهاد بهتر: [FEAT-07](09-Feature-Proposals.md#feat-07) (ثبت روزانه/ماهانه‌ی مصرف اینترفیس) تا این عدد دیگر فقط به یک ردیف وابسته نباشد.

> 🔎 تفاوت مهم: Total Usage این پنل «مجموع شمارنده‌های peerها» است، نه شمارنده‌ی کارت شبکه. با آمار پنل هاستینگ (که overhead هندشیک، keepalive و ترافیک خود سرور را هم دارد) دقیقاً برابر نیست.

---

<a id="tu-04"></a>
## TU-04 🟢 snapshot با read-modify-write

**محل:** [WireguardConfiguration.py:1508-1548](../src/modules/WireguardConfiguration.py#L1508-L1548)

مقدار قبلی خوانده، جمع زده و نوشته می‌شود. چون همه‌ی فراخوان‌ها (`deletePeers`, `resetDataUsage` از API و از job) داخل `self.lock` کانفیگ هستند، الان مشکلی پیش نمی‌آید؛ ولی اگر روزی فراخوانی خارج از lock اضافه شود، به‌روزرسانی‌ها گم می‌شوند. نسخه‌ی اتمیک:
```python
t = self.interfaceTrafficSnapshotTable
res = conn.execute(t.update().where(t.c.configuration_name == self.Name).values(
    total_receive=t.c.total_receive + recv,
    total_sent=t.c.total_sent + sent,
    total_data=t.c.total_data + total,
    last_updated=datetime.now()))
if res.rowcount == 0:
    conn.execute(t.insert().values(configuration_name=self.Name, total_receive=recv,
                                   total_sent=sent, total_data=total, last_updated=datetime.now()))
```

---

<a id="tu-05"></a>
## TU-05 🟢 Total هرگز صفر نمایش داده نمی‌شود

**محل:** [WireguardConfiguration.py:1610-1614](../src/modules/WireguardConfiguration.py#L1610-L1614)

اگر نتیجه‌ی کوئری صفر باشد و قبلاً مقدار مثبت داشته‌ایم، مقدار قبلی برگردانده می‌شود (برای جلوگیری از «پرش به صفر» در خطاهای موقت). اگر روزی بخواهید Total را عمداً صفر کنید (مثلاً شروع ماه جدید)، تا ری‌استارت پنل عدد قدیمی دیده می‌شود. برای «خطای موقت» بهتر است فقط در شاخه‌ی `except` از مقدار قبلی استفاده شود.

---

<a id="tu-06"></a>
## TU-06 🟢 Race نظری بین refresh و ریست

`refreshPeersRuntimeStats` بدون `self.lock` اجرا می‌شود ([dashboard.py:116](../src/dashboard.py#L116)، [WireguardConfiguration.py:1279](../src/modules/WireguardConfiguration.py#L1279)). طراحی فعلی به‌خوبی از این محافظت می‌کند: refresh فقط `total_*` را با مقدار kernel می‌نویسد و ریست فقط `cumu_*` را تغییر می‌دهد، و dump kernel **قبل از** خواندن دیتابیس گرفته می‌شود. تنها حالت مشکل‌ساز وقتی است که یک rollover دقیقاً هم‌زمان با تراکنش ریست اتفاق بیفتد؛ احتمال بسیار کم. برای اطمینان کامل، نوشتن `cumu_updates` در refresh را هم داخل `self.lock` ببرید (قسمت `wg show dump` می‌تواند بیرون lock بماند).

---

## چک‌لیست تست پیشنهادی روی سرور (بعد از رفع TU-01)
1. عدد Total، Sent و Receive اینترفیس را یادداشت کنید.
2. یک peer تست بسازید، ۱۰۰ مگ مصرف کنید ← Total باید ~۰.۱ گیگ زیاد شود.
3. اینترفیس را خاموش/روشن کنید ← Total نباید کم شود.
4. peer را restrict و allow کنید ← Total ثابت.
5. مصرف peer را ریست کنید ← مصرف peer صفر، Total ثابت.
6. peer را حذف کنید ← Total ثابت و `Total == Sent + Receive` (± گردکردن).
7. کوئری `drift` بالا را اجرا کنید ← باید صفر باشد.
