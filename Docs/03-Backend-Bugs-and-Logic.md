# ۰۳ — باگ‌ها و خطاهای منطقی بک‌اند

> باگ‌های مخصوص Total Usage در [سند ۰۴](04-Total-Usage-Review.md)، مخصوص Global Backup در [سند ۰۵](05-Global-Backup-Review.md) و مخصوص ربات در [سند ۰۶](06-OxBot-Integration.md) آمده‌اند. اینجا باگ‌های عمومی بک‌اند است.

## جدول خلاصه

| ID | شدت | عنوان |
|----|------|-------|
| [BUG-01](#bug-01) | 🟠 | peerهای restrict‌شده در API دو بار برگردانده می‌شوند |
| [BUG-02](#bug-02) | 🟠 | یک job نامعتبر کل سیستم job را برای همیشه متوقف می‌کند |
| [BUG-03](#bug-03) | 🟠 | Auto-heal کلید peerها را بی‌صدا عوض می‌کند |
| [BUG-04](#bug-04) | 🟠 | `deletePeers` برای peer ناموجود هم «موفق» برمی‌گرداند |
| [BUG-05](#bug-05) | 🟠 | job محدودیت بعد از ۱۱ شکست بی‌صدا پاک می‌شود ← کاربر نامحدود |
| [BUG-06](#bug-06) | 🟡 | Race روی لیست job ها بین API و thread پس‌زمینه |
| [BUG-07](#bug-07) | 🟠 | ریستور بکاپ per-configuration همیشه شکست می‌خورد (regression) |
| [BUG-08](#bug-08) | 🟡 | ساخت کانفیگ WireGuard از روی بکاپ با `TypeError` کرش می‌کند |
| [BUG-09](#bug-09) | 🟡 | `GetConfig` مقدار `"0"` و `"1"` را bool می‌کند ← خرابی addPeers |
| [BUG-10](#bug-10) | 🟡 | اعتبارسنجی DNS در ویرایش peer عملاً غیرفعال است |
| [BUG-11](#bug-11) | 🟡 | عملیات روی peerها اینترفیس خاموش را بی‌صدا روشن می‌کنند |
| [BUG-12](#bug-12) | 🟡 | حذف peer وقتی اینترفیس خاموش است در `.conf` ذخیره نمی‌شود ← برگشت peer |
| [BUG-13](#bug-13) | 🟡 | حلقه‌ی بی‌وقفه (busy loop) در thread بکاپ خودکار هنگام ریستور |
| [BUG-14](#bug-14) | 🟡 | Reconcile از `remote_endpoint` سرور به‌عنوان endpoint کلاینت استفاده می‌کند |
| [BUG-15](#bug-15) | 🟡 | Rename کانفیگ، تنظیمات Override و Share Linkها را از دست می‌دهد |
| [BUG-16](#bug-16) | 🟢 | حذف کانفیگ داده‌های یتیم باقی می‌گذارد |
| [BUG-17](#bug-17) | 🟢 | بررسی «مقدار خالی» در تنظیمات هرگز اجرا نمی‌شود |
| [BUG-18](#bug-18) | 🟢 | خطاهای ورودی ← 500 به‌جای پیام خطا (چندین endpoint) |
| [BUG-19](#bug-19) | 🟢 | بررسی آپدیت به ریپوی اصلی WGDashboard اشاره می‌کند |
| [BUG-20](#bug-20) | 🟢 | `wgd.sh`: پورت ثابت 10086 و kill گسترده |
| [BUG-21](#bug-21) | 🟢 | موارد ریز دیگر |

---

<a id="bug-01"></a>
## BUG-01 🟠 peerهای restrict‌شده دو بار برگردانده می‌شوند

**محل:** [dashboard.py:1562-1568](../src/dashboard.py#L1562-L1568) (تغییر در commit `15dde64`) و [peerList.vue:91-109](../src/static/app/src/components/configurationComponents/peerList.vue#L91-L109)

```python
"configurationPeers": active_peers + (restricted_peers or []),   # ← restrictedها اینجا هم هستند
"configurationRestrictedPeers": restricted_peers
```
فرانت همه‌ی `configurationPeers` را `restricted = false` می‌کند و سپس `configurationRestrictedPeers` را دوباره با `restricted = true` اضافه می‌کند. نتیجه:
- هر peer محدودشده **دو کارت** در UI دارد؛ یکی در فیلتر «Active».
- شمارنده‌ی `X / N` به اندازه‌ی تعداد restrict‌شده‌ها بیشتر است.
- عملیات روی کپی «Active» (مثلاً Restrict) با پیام «No active peer(s) found» شکست می‌خورد.
- **ربات** در `get_userwg` اول `configurationPeers` را جستجو می‌کند، peer محدودشده را آنجا پیدا می‌کند و آن را «فعال» می‌بیند (به [BOT-01](06-OxBot-Integration.md#bot-01) مراجعه کنید).

**راه‌حل:** قرارداد upstream را برگردانید:
```python
"configurationPeers": active_peers,
"configurationRestrictedPeers": restricted_peers
```
و برای اطمینان در `Peer.toJson` یک فیلد صریح اضافه کنید (`"restricted": <bool>`) که هم فرانت و هم ربات از آن استفاده کنند.

---

<a id="bug-02"></a>
## BUG-02 🟠 یک job نامعتبر کل حلقه‌ی job را متوقف می‌کند

**محل:** [PeerJobs.py:159-212](../src/modules/PeerJobs.py#L159-L212)

داخل حلقه هیچ `try/except` برای هر job وجود ندارد:
```python
x = float((getattr(fp, f"total_{s}", 0.0) ...))
y = float(job.Value)                                    # ValueError اگر Value خالی/نامعتبر باشد
...
y = datetime.strptime(job.Value, "%Y-%m-%d %H:%M:%S")   # ValueError برای هر فرمت دیگر
...
with c.lock:                                            # TimeoutError از ProcessLock
```
استثنا از `runJob` بیرون می‌رود، thread شماره ۲ آن را لاگ می‌کند و ۱۸۰ ثانیه بعد **دوباره روی همان job** گیر می‌کند. چون لاگ شکست برای آن job ثبت نمی‌شود، `cleanJob` هم هیچ‌وقت آن را پاک نمی‌کند. یعنی **هیچ کاربری دیگر محدود نمی‌شود** (نه حجم، نه زمان) تا وقتی کسی متوجه شود. `savePeerScheduleJob` هم هیچ اعتبارسنجی روی `Field`/`Operator`/`Value`/`Action` ندارد ([dashboard.py:1706-1727](../src/dashboard.py#L1706-L1727)).

**راه‌حل:**
```python
for job in list(self.Jobs):
    try:
        ...  # منطق فعلی
    except Exception as e:
        current_app.logger.error(f"Job {job.JobID} failed: {e}")
        self.JobLogger.log(job.JobID, False, f"Job crashed: {e}")
```
و در `API_savePeerScheduleJob` مقدار `Value` را بر اساس `Field` اعتبارسنجی کنید (عدد برای حجم، فرمت تاریخ برای `date`).

---

<a id="bug-03"></a>
## BUG-03 🟠 Auto-heal کلید peerها را بی‌صدا عوض می‌کند

**محل:** [WireguardConfiguration.py:186-190](../src/modules/WireguardConfiguration.py#L186-L190)، [WireguardConfiguration.py:540-566](../src/modules/WireguardConfiguration.py#L540-L566)، [Peer.py:197-329](../src/modules/Peer.py#L197-L329)، [Peer.py:331-340](../src/modules/Peer.py#L331-L340) (commit `99bdc5a`)

هر peer که `private_key` خالی داشته باشد و هنوز handshake/ترافیک نداشته باشد، در `__initPeersList` (هر بار ساخت شیء کانفیگ) و در `downloadPeer` **کلیدش عوض می‌شود**: peer قدیمی از kernel حذف و کلید جدید جایگزین می‌شود.

مشکل این است که «private_key خالی» یعنی «کلید را کلاینت ساخته» یا «peer از فایل `.conf` وارد شده» — نه «peer خراب است». سناریوهای واقعی که در آن‌ها **همه‌ی کاربران یک اینترفیس قطع می‌شوند**:
1. **مهاجرت سرور / دیتابیس جدید:** فایل‌های `.conf` قدیمی با دیتابیس خالی. `getPeers` همه را با `private_key=""` و `latest_handshake="N/A"` وارد می‌کند ([WireguardConfiguration.py:488-511](../src/modules/WireguardConfiguration.py#L488-L511)) و بلافاصله همه re-key می‌شوند.
2. **Global restore** با دیتابیس قدیمی‌تر از `.conf` ها، یا self-heal دیتابیس که بخشی از ردیف‌ها را از دست بدهد ([DatabaseConnection.py:146-165](../src/modules/DatabaseConnection.py#L146-L165)).
3. **اضافه کردن peer با Raw Config Editor** برای کاربری که کلید خودش را دارد.
4. کاربری که کلید را خودش ساخته و هنوز وصل نشده؛ اگر ادمین/ربات «Download» بزند، کلیدش عوض می‌شود.

پیامدهای جانبی:
- ربات کلید قدیمی را در `invoice.user_info` دارد؛ `restrictPeers`/`deletePeers` ربات دیگر peer را پیدا نمی‌کنند (و به‌خاطر BUG-04 حذف «موفق» گزارش می‌شود ولی peer باقی می‌ماند).
- به‌روزرسانی Share Linkها کار نمی‌کند: `self.configuration.peerShareLinksTable` وجود ندارد ([Peer.py:273-280](../src/modules/Peer.py#L273-L280)) و لیست درون‌حافظه `Links` نام دارد نه `ShareLinks` ([Peer.py:317-323](../src/modules/Peer.py#L317-L323)).
- جداول `_transfer` و `_history_endpoint` به id جدید منتقل نمی‌شوند (تاریخچه از دست می‌رود).
- برای هر فراخوانی یک engine جدید ساخته و dispose نمی‌شود ([Peer.py:284](../src/modules/Peer.py#L284)، [Peer.py:294](../src/modules/Peer.py#L294)).

**راه‌حل:** auto-heal خودکار را کاملاً حذف کنید. `downloadPeer` برای peer بدون کلید خصوصی همان خطای فعلی (`"PrivateKey is missing..."`) را برگرداند و یک دکمه‌ی صریح «Regenerate keys» با تأیید در UI باشد. فراخوانی در `__initPeersList` را حذف کنید.

---

<a id="bug-04"></a>
## BUG-04 🟠 `deletePeers` برای peer ناموجود «موفق» برمی‌گرداند

**محل:** [WireguardConfiguration.py:998-1001](../src/modules/WireguardConfiguration.py#L998-L1001)، [WireguardConfiguration.py:1046-1082](../src/modules/WireguardConfiguration.py#L1046-L1082)، [WireguardConfiguration.py:1097-1102](../src/modules/WireguardConfiguration.py#L1097-L1102)

اگر id در هیچ جدولی نباشد، یک `PeerFallback` ساخته می‌شود، در `valid_delete_ids` قرار می‌گیرد، DELETE روی صفر ردیف اجرا می‌شود و `numOfDeletedPeers` افزایش می‌یابد. خروجی: `True, "Deleted 1 peer(s)"` و webhook `peer_deleted` هم ارسال می‌شود. ربات (یا ادمین) فکر می‌کند کاربر حذف شده در حالی که peer واقعی (مثلاً با کلید عوض‌شده در BUG-03 یا id اشتباه) باقی است و IP را اشغال کرده.

**راه‌حل:** پاک‌سازی kernel برای کلیدهای یتیم را نگه دارید، ولی در شمارش موفقیت فقط ردیف‌هایی را حساب کنید که `result.rowcount > 0` داشته‌اند و برای بقیه پیام `not found` برگردانید.

---

<a id="bug-05"></a>
## BUG-05 🟠 job محدودیت بی‌صدا پاک می‌شود

**محل:** [PeerJobs.py:214-226](../src/modules/PeerJobs.py#L214-L226)، [PeerJobLogger.py:80-97](../src/modules/PeerJobLogger.py#L80-L97)، [PeerJobs.py:199-210](../src/modules/PeerJobs.py#L199-L210)

هر job که بیش از ۱۰ لاگ شکست داشته باشد (هر ۳ دقیقه یکی ← حدود ۳۳ دقیقه) **منقضی** می‌شود و لاگ‌هایش هم پاک می‌شوند. موقعیت‌هایی که شکست ثبت می‌شود:
- کانفیگ بارگذاری نشده (مثلاً فایل `.conf` بعد از ریستور یا ویرایش دستی خطای parse دارد) ← `c is None`؛
- peer موقتاً پیدا نمی‌شود؛
- `restrictPeers` به هر دلیلی False برگرداند (مثلاً خطای `wg-quick save`).

نتیجه: job حجم/زمان کاربر **برای همیشه** پاک می‌شود و هیچ‌کس مطلع نمی‌شود ← کاربر نامحدود می‌ماند. برای فروشنده‌ی VPN این مستقیماً ضرر مالی است.

**راه‌حل:** job های محدودیت (`total_*`, `date` با Action=`restrict`) را هرگز خودکار حذف نکنید؛ به‌جای آن هشدار (لاگ ERROR + webhook `job_failed`) بفرستید. اگر می‌خواهید job های خراب پاک شوند، فقط job هایی که peerشان واقعاً در دیتابیس وجود ندارد.

---

<a id="bug-06"></a>
## BUG-06 🟡 Race روی لیست job ها

**محل:** [PeerJobs.py:37-46](../src/modules/PeerJobs.py#L37-L46)، [PeerJobs.py:74-140](../src/modules/PeerJobs.py#L74-L140)، [PeerJobs.py:159-164](../src/modules/PeerJobs.py#L159-L164)

`__getJobs` لیست مشترک `self.Jobs` را `clear()` و دوباره پر می‌کند. `saveJob`/`deleteJob` (از thread درخواست‌های ربات) بدون `self.lock` این کار را می‌کنند، در حالی که `runJob` در thread دیگر روی همان لیست iterate می‌کند ← بعضی job ها در یک دور اجرا نمی‌شوند یا تکراری اجرا می‌شوند. `Peer.getJobs` هم هم‌زمان روی آن فیلتر می‌زند.

**راه‌حل:** لیست جدید بسازید و یک‌جا جایگزین کنید، و `saveJob`/`deleteJob` را داخل `with self.lock` ببرید:
```python
def __getJobs(self):
    with self.engine.connect() as conn:
        rows = conn.execute(...).mappings().fetchall()
    self.Jobs = [PeerJob(...) for job in rows]   # جایگزینی اتمیک مرجع
```

---

<a id="bug-07"></a>
## BUG-07 🟠 ریستور بکاپ per-configuration همیشه شکست می‌خورد

**محل:** [WireguardConfiguration.py:1695-1731](../src/modules/WireguardConfiguration.py#L1695-L1731) و [WireguardConfiguration.py:365-393](../src/modules/WireguardConfiguration.py#L365-L393)

در commit `5dbe527` فراخوانی `self.__importDatabase(targetSQL)` به `self.__importDatabase(targetSQL, restore=True)` تغییر کرد. با `restore=True` جداول **drop نمی‌شوند** و `INSERT`های بکاپ با ردیف‌های موجود (کلید اصلی `id` و `configuration_name` در جدول snapshot) برخورد می‌کنند ← `IntegrityError` ← کل تراکنش rollback ← `restoreBackup` فایل `.conf` قبلی را برمی‌گرداند و `False` می‌دهد.

ولی اینترفیس قبلاً در خط ۱۶۹۹-۱۷۰۰ **خاموش شده** و دوباره روشن نمی‌شود ← همه‌ی کاربران آن اینترفیس قطع می‌مانند. جزئیات اثر روی Total Usage در [TU-02](04-Total-Usage-Review.md#tu-02).

**راه‌حل:**
```python
self.__importDatabase(targetSQL)          # drop + import (رفتار upstream)
...
finally:
    if was_running and not self.getStatus():
        self.toggleConfiguration()
```
و قبل از drop، یک بکاپ خودکار از وضعیت فعلی بگیرید.

---

<a id="bug-08"></a>
## BUG-08 🟡 ساخت کانفیگ WireGuard از روی بکاپ کرش می‌کند

**محل:** [dashboard.py:633-635](../src/dashboard.py#L633-L635)

```python
WireguardConfiguration(DashboardConfig, AllPeerJobs, AllPeerShareLinks, data=data, name=...)
```
آرگومان چهارم اجباری `DashboardWebHooks` ([WireguardConfiguration.py:54-58](../src/modules/WireguardConfiguration.py#L54-L58)) جا افتاده ← `TypeError` ← خطای 500 در صفحه‌ی «Restore Configuration» برای کانفیگ‌های `wg`. (شاخه‌ی `awg` درست است.) فایل `.conf` قبل از کرش کپی شده و یک کانفیگ نیمه‌کاره باقی می‌ماند.

**راه‌حل:** `DashboardWebHooks` را اضافه کنید.

---

<a id="bug-09"></a>
## BUG-09 🟡 `GetConfig` مقادیر `"0"`/`"1"` را به bool تبدیل می‌کند

**محل:** [DashboardConfig.py:346-351](../src/modules/DashboardConfig.py#L346-L351)

هر مقداری که `0`، `1`، `yes`، `no`، `on` یا `off` باشد bool برگردانده می‌شود. اگر ادمین در تنظیمات پیش‌فرض Peer مقدار **Keepalive = 0** یا **MTU = 0** بگذارد (هر دو مقدار معتبر و رایج‌اند):
- در `API_addPeers` ([dashboard.py:1382-1399](../src/dashboard.py#L1382-L1399)) فراخوانی `default.isnumeric()` روی `False` خطای `AttributeError` می‌دهد ← «Add peers failed.» برای هر افزودنی که mtu/keepalive را صریح نفرستد (UI پنل).
- در `getPeers` ([WireguardConfiguration.py:505-506](../src/modules/WireguardConfiguration.py#L505-L506)) `len(False)` خطای `TypeError` می‌دهد ← وارد کردن peer از `.conf` شکست می‌خورد.

**راه‌حل:** تبدیل bool را فقط برای کلیدهای bool شناخته‌شده انجام دهید یا متدهای `GetBool`/`GetInt`/`GetStr` جدا بسازید.

---

<a id="bug-10"></a>
## BUG-10 🟡 اعتبارسنجی DNS در ویرایش peer کار نمی‌کند

**محل:** [Peer.py:110](../src/modules/Peer.py#L110)، [AmneziaPeer.py:53](../src/modules/AmneziaPeer.py#L53)

`ValidateDNSAddress` یک tuple برمی‌گرداند؛ `if not (False, "msg")` همیشه False است ← هر مقداری (حتی همراه با newline) در فیلد DNS کانفیگ کاربر نوشته می‌شود. همچنین اگر `keepalive` مقدار `None` باشد، `keepalive < 0` خطای `TypeError` (500) می‌دهد ([Peer.py:122](../src/modules/Peer.py#L122)) و `mtu=None` با پیام «MTU format is not correct» رد می‌شود ([Peer.py:119](../src/modules/Peer.py#L119)).

**راه‌حل:** `ok, msg = ValidateDNSAddress(dns_addresses); if not ok: return False, msg` و `None` را مثل `0` در نظر بگیرید.

---

<a id="bug-11"></a>
## BUG-11 🟡 روشن شدن ناخواسته‌ی اینترفیس خاموش

**محل:** [dashboard.py:1403-1404](../src/dashboard.py#L1403-L1404)، [WireguardConfiguration.py:628-629](../src/modules/WireguardConfiguration.py#L628-L629)، [813-814](../src/modules/WireguardConfiguration.py#L813-L814)، [892-893](../src/modules/WireguardConfiguration.py#L892-L893)، [961-965](../src/modules/WireguardConfiguration.py#L961-L965)، [AmneziaPeer.py:28-29](../src/modules/AmneziaPeer.py#L28-L29)

اگر ادمین اینترفیسی را برای نگهداری خاموش کند، هر درخواست ربات (افزودن، حذف، restrict، allow) یا اجرای job پس‌زمینه (هر ۳ دقیقه) آن را **دوباره روشن** می‌کند و `addAutostart` هم صدا زده می‌شود. ویرایش peer در AmneziaWG هم وضعیت قبلی را برنمی‌گرداند.

**راه‌حل:** وقتی اینترفیس خاموش است، تغییرات را فقط در دیتابیس و فایل `.conf` اعمال کنید (بدون `wg set`) یا با پیام خطای صریح `"Interface is down"` رد کنید. هرگز به‌طور ضمنی روشن نکنید.

---

<a id="bug-12"></a>
## BUG-12 🟡 تغییرات روی اینترفیس خاموش در `.conf` ذخیره نمی‌شوند

**محل:** [WireguardConfiguration.py:1244-1246](../src/modules/WireguardConfiguration.py#L1244-L1246)

```python
def __wgSave(self) -> bool:
    if not self.getStatus():
        return True        # ← هیچ چیزی ذخیره نشده ولی «موفق»
```
اگر روشن کردن اینترفیس در BUG-11 شکست بخورد، `deletePeers` ردیف‌های دیتابیس را پاک می‌کند (خطای `wg set ... remove` بلعیده می‌شود: [WireguardConfiguration.py:1021-1025](../src/modules/WireguardConfiguration.py#L1021-L1025)) ولی peer در فایل `.conf` باقی می‌ماند. دفعه‌ی بعد که فایل parse شود، `getPeers` آن را به‌عنوان peer **جدید** وارد می‌کند ([WireguardConfiguration.py:479-511](../src/modules/WireguardConfiguration.py#L479-L511)) و با BUG-03 کلید جدید هم می‌گیرد؛ و با `wg-quick up` بعدی، peer «حذف‌شده» دوباره در kernel فعال است.

**راه‌حل:** در حالت خاموش، بخش `[Peer]` مربوطه را مستقیماً از فایل حذف کنید (parse + بازنویسی اتمیک).

---

<a id="bug-13"></a>
## BUG-13 🟡 Busy loop در thread بکاپ خودکار

**محل:** [dashboard.py:271-274](../src/dashboard.py#L271-L274)

```python
if app.config.get('MAINTENANCE_MODE', False):
    app.logger.warning("Skipping scheduled backup ...")
    continue          # ← time.sleep(60) انتهای حلقه را رد می‌کند
```
اگر بکاپ خودکار فعال و زمانش رسیده باشد و هم‌زمان ریستور در جریان باشد، thread بدون توقف می‌چرخد: ۱۰۰٪ یک هسته، میلیون‌ها خط لاگ، و کند شدن خود ریستور به‌خاطر GIL.

**راه‌حل:** به‌جای `continue`، `time.sleep(60); continue` یا ساختار `if ... else`.

---

<a id="bug-14"></a>
## BUG-14 🟡 Reconcile از endpoint اشتباه استفاده می‌کند

**محل:** [WireguardConfiguration.py:1199-1201](../src/modules/WireguardConfiguration.py#L1199-L1201)

ستون `remote_endpoint` در جدول peerها آدرس **سرور** است (برای ساخت کانفیگ کلاینت)، نه endpoint کلاینت. اگر شامل `:` باشد (IPv6 یا `host:port`) دستور `wg set ... endpoint <آدرس سرور>` اجرا می‌شود که یا شکست می‌خورد (peer بازیابی نمی‌شود) یا endpoint اشتباه ست می‌کند.

**راه‌حل:** بخش endpoint را کاملاً حذف کنید؛ کلاینت خودش وصل می‌شود.

---

<a id="bug-15"></a>
## BUG-15 🟡 Rename کانفیگ داده‌ها را از دست می‌دهد

**محل:** [WireguardConfiguration.py:1816-1861](../src/modules/WireguardConfiguration.py#L1816-L1861)، [dashboard.py:720-739](../src/dashboard.py#L720-L739)

جداول peer و snapshot و job ها منتقل می‌شوند، ولی:
- ردیف `ConfigurationsInfo` (توضیحات، **OverridePeerSettings** شامل endpoint/DNS/MTU پیش‌فرض، PeerGroups) منتقل نمی‌شود ← کانفیگ‌هایی که بعد از rename دانلود می‌شوند **endpoint و DNS متفاوتی** خواهند داشت.
- `PeerShareLinks.Configuration` و `DashboardClientsPeerAssignment` به‌روز نمی‌شوند.
- ربات `inboundid` قدیمی را دارد ← همه‌ی درخواست‌های ربات «Configuration does not exist» می‌گیرند.
- SQL با backtick نوشته شده و روی PostgreSQL کار نمی‌کند.

**راه‌حل:** همه‌ی این جداول را در همان تراکنش به‌روز کنید و در UI هشدار دهید که `inboundid` ربات باید عوض شود.

---

<a id="bug-16"></a>
## BUG-16 🟢 حذف کانفیگ داده‌های یتیم باقی می‌گذارد

**محل:** [WireguardConfiguration.py:1809-1814](../src/modules/WireguardConfiguration.py#L1809-L1814)

job ها، share linkها، ردیف `ConfigurationsInfo` و assignmentها باقی می‌مانند. job های یتیم هر ۳ دقیقه شکست می‌خورند و لاگ تولید می‌کنند. همچنین قبل از حذف هیچ بکاپی گرفته نمی‌شود.

---

<a id="bug-17"></a>
## BUG-17 🟢 بررسی «مقدار خالی» هرگز اجرا نمی‌شود

**محل:** [DashboardConfig.py:243-246](../src/modules/DashboardConfig.py#L243-L246)

شرط `section == 'Peer'` (به‌جای `'Peers'`) هیچ‌وقت درست نیست، پس مقدار خالی برای هر کلیدی (مثلاً `app_port`) پذیرفته می‌شود و پنل در اجرای بعدی بالا نمی‌آید.

---

<a id="bug-18"></a>
## BUG-18 🟢 ورودی ناقص ← خطای 500

نمونه‌ها (همه با `data['...']` بدون بررسی وجود کلید):
- [dashboard.py:1173-1182](../src/dashboard.py#L1173-L1182) `updatePeerSettings`، [1220-1221](../src/dashboard.py#L1220-L1221) `resetPeerData`، [1246](../src/dashboard.py#L1246) `deletePeers`، [1268](../src/dashboard.py#L1268)، [1338](../src/dashboard.py#L1338)، [1517](../src/dashboard.py#L1517) `downloadPeer`، [1722-1724](../src/dashboard.py#L1722-L1724) `savePeerScheduleJob`، [519](../src/dashboard.py#L519) `authenticate`.
- [dashboard.py:1292](../src/dashboard.py#L1292): `sharePeer/create` با `ExpireDate = null` ← `strptime(None)`.
- [dashboard.py:829-830](../src/dashboard.py#L829-L830): `os.path.basename(None)`.
- [dashboard.py:1774-1778](../src/dashboard.py#L1774-L1778): اگر parse IP شکست بخورد، `ip` تعریف‌نشده یا مقدار قبلی است.
- [dashboard.py:1824](../src/dashboard.py#L1824): `ResponseObject(False, exp)` — شیء Exception قابل JSON نیست.
- [dashboard.py:2142](../src/dashboard.py#L2142): `all([configurationName, id])` از تابع داخلی `id` استفاده می‌کند نه `peerID`.
- [dashboard.py:332-333](../src/dashboard.py#L332-L333): اگر `awg` نصب باشد ولی پوشه‌ی `awg_conf_path` نباشد، `os.listdir` خطا می‌دهد و لیست کانفیگ‌ها 500 می‌شود.
- `restrictPeers` اگر لیست ورودی id تکراری داشته باشد False برمی‌گرداند ([WireguardConfiguration.py:953-955](../src/modules/WireguardConfiguration.py#L953-L955)).

**راه‌حل کلی:** یک decorator برای اعتبارسنجی بدنه‌ی JSON (یا pydantic که در requirements هست) و پاسخ 400 با پیام مشخص.

---

<a id="bug-19"></a>
## BUG-19 🟢 بررسی آپدیت به ریپوی اصلی اشاره می‌کند

**محل:** [dashboard.py:1877-1892](../src/dashboard.py#L1877-L1892)

`getDashboardUpdate` آخرین release ریپوی `WGDashboard/WGDashboard` را چک می‌کند و پیام «update available» نشان می‌دهد. نصب آن روی این فورک همه‌ی تغییرات شما (Global Backup، snapshot ترافیک، ...) را پاک می‌کند. همچنین `docker/compose.yaml` ایمیج رسمی upstream را استفاده می‌کند نه این فورک، و `dashboard_version` در MANIFEST بکاپ ثابت `v4.3.3` است ([GlobalBackup.py:353](../src/modules/GlobalBackup.py#L353)).

**راه‌حل:** آدرس را به ریپوی خودتان تغییر دهید یا این قابلیت را غیرفعال کنید؛ نسخه را از `DashboardConfig.DashboardVersion` بخوانید.

---

<a id="bug-20"></a>
## BUG-20 🟢 `wgd.sh`

- [wgd.sh:406](../src/wgd.sh#L406)، [436](../src/wgd.sh#L436)، [498](../src/wgd.sh#L498): پورت `10086` ثابت است؛ اگر `app_port` را عوض کنید، status اشتباه و `fuser -k` هر پروسه‌ی دیگری روی 10086 را می‌کشد.
- [wgd.sh:430-433](../src/wgd.sh#L430-L433)، [495](../src/wgd.sh#L495): `pkill -f "gunicorn.*dashboard:app"` هر اپ دیگری با همین الگو را هم می‌کشد.
- [wgd.sh:488-490](../src/wgd.sh#L488-L490): `./wgd.sh restart` دستی، سرویس systemd را stop می‌کند و gunicorn را **خارج از systemd** اجرا می‌کند ← بعد از کرش، دیگر خودکار بالا نمی‌آید. پیشنهاد: اگر سرویس نصب است، `restart` همیشه `systemctl restart wg-dashboard` بزند.

---

<a id="bug-21"></a>
## BUG-21 🟢 موارد ریز

- `getNumberOfAvailableIP` آدرس network و broadcast را هم می‌شمارد (۲ عدد بیشتر از واقع) در حالی که `getAvailableIP` از `hosts()` استفاده می‌کند ([WireguardConfiguration.py:1889](../src/modules/WireguardConfiguration.py#L1889)).
- بررسی تکراری بودن IP در `addPeers` مقایسه‌ی رشته‌ای است؛ `10.0.0.5` و `10.0.0.5/32` متفاوت حساب می‌شوند ([WireguardConfiguration.py:672-698](../src/modules/WireguardConfiguration.py#L672-L698)). بهتر است با `ipaddress.ip_network(...).overlaps()` بررسی شود.
- `autoPruneDatabaseLogs` زمان UTC اسکیولایت (`datetime('now')`) را با زمان محلی `ExpireDate` مقایسه می‌کند ([dashboard.py:187-191](../src/dashboard.py#L187-L191)).
- فراخوانی `self.__dumpDatabase()` در سازنده یک generator است و هیچ کاری نمی‌کند ([WireguardConfiguration.py:162](../src/modules/WireguardConfiguration.py#L162)).
- نام peerهای bulk با `addedCount + 1` ساخته می‌شود (اولی `BulkPeer_2_...`) ([dashboard.py:1431](../src/dashboard.py#L1431)).
- `updateConfigurationSettings` اگر یکی از کلیدهای `allowEdit` در بدنه نباشد `KeyError` می‌دهد، در حالی که اینترفیس را قبلاً خاموش کرده ([WireguardConfiguration.py:1771-1793](../src/modules/WireguardConfiguration.py#L1771-L1793)).
