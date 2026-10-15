# ۰۲ — آسیب‌پذیری‌های امنیتی کد

> برای نشت اطلاعات در ریپو به [سند ۰۱](01-URGENT-Leaked-Secrets.md) مراجعه کنید. این سند درباره‌ی ضعف‌های **خود کد** است.
> معماری اجرا: Gunicorn با `workers = 1` و `threads = 8` ([gunicorn.conf.py:33-34](../src/gunicorn.conf.py#L33-L34)) — یعنی ۸ درخواست به‌طور هم‌زمان در **یک پروسه** اجرا می‌شوند. چند یافته‌ی زیر به همین دلیل واقعی هستند.

## جدول خلاصه

| ID | شدت | عنوان |
|----|------|-------|
| [SEC-01](#sec-01) | 🟠 | Race در احراز هویت: ورود ادمین بدون پسورد هم‌زمان با درخواست‌های ربات |
| [SEC-02](#sec-02) | 🟠 | `getDashboardConfiguration` کلید `flask_secret_key` و پسوردها را برمی‌گرداند |
| [SEC-03](#sec-03) | 🔴* | API Key = ادمین کامل = اجرای فرمان root (مهم با توجه به افشای کلید) |
| [SEC-04](#sec-04) | 🟡 | API Keyهای منقضی تا ری‌استارت معتبر می‌مانند |
| [SEC-05](#sec-05) | 🟠 | کلید خصوصی اینترفیس در خروجی API به ربات‌ها داده می‌شود |
| [SEC-06](#sec-06) | 🟡 | CSRF با endpointهای GET که وضعیت را تغییر می‌دهند (مثلاً خاموش کردن اینترفیس) |
| [SEC-07](#sec-07) | 🟡 | Rate limiter پشت nginx همه را یک IP می‌بیند ← قفل شدن ورود ادمین |
| [SEC-08](#sec-08) | 🟡 | Session ادمین ۳۱ روزه و غیرقابل ابطال |
| [SEC-09](#sec-09) | 🟡 | نوشتن غیراتمی `wg-dashboard.ini` ← احتمال برگشت پسورد به `admin` |
| [SEC-10](#sec-10) | 🟡 | فایل‌های حساس قابل خواندن برای همه‌ی کاربران لوکال (`chmod 755`، PSK در `/tmp`) |
| [SEC-11](#sec-11) | 🟡 | پرتال Client با ثبت‌نام عمومی به‌طور پیش‌فرض فعال است |
| [SEC-12](#sec-12) | 🟡 | ارسال IP کاربران به سرویس خارجی از طریق HTTP رمزنشده |
| [SEC-13](#sec-13) | 🟢 | TOTP بدون پنجره‌ی زمانی و بدون جلوگیری از replay |
| [SEC-14](#sec-14) | 🟢 | Path traversal در `locale/update` و ساخت کانفیگ از Backup |
| [SEC-15](#sec-15) | 🟢 | پیوست‌های ایمیل (حاوی کلید خصوصی) هرگز پاک نمی‌شوند |
| [SEC-16](#sec-16) | 🟢 | CORS با `origins: "*"` |

\* شدت SEC-03 در حالت عادی «طراحی» محسوب می‌شود، ولی چون API Key شما عمومی شده، الان بحرانی است.

---

<a id="sec-01"></a>
## SEC-01 🟠 Race در احراز هویت با `DashboardConfig.APIAccessed`

**محل:** [dashboard.py:422-449](../src/dashboard.py#L422-L449) و [dashboard.py:511-518](../src/dashboard.py#L511-L518)

**مشکل:** نتیجه‌ی بررسی API Key در یک **متغیر سراسری مشترک** (`DashboardConfig.APIAccessed`) نوشته می‌شود و `API_AuthenticateLogin` همان را می‌خواند:
```python
# before_request (هر درخواست):
DashboardConfig.APIAccessed = False
...
DashboardConfig.APIAccessed = True      # اگر هدر API Key معتبر بود
# /api/authenticate:
if DashboardConfig.APIAccessed:          # ← مقدار مال درخواست دیگری می‌تواند باشد
    session['role'] = 'admin'            # بدون بررسی پسورد!
```
**سناریو:** ربات شما مدام با API Key معتبر درخواست می‌فرستد. مهاجم هم‌زمان `POST /api/authenticate` با پسورد غلط می‌زند. اگر بین `before_request` درخواست مهاجم و خط ۵۱۱، thread درخواست ربات `APIAccessed = True` را ست کند، مهاجم **session ادمین** می‌گیرد. پنجره کوچک است ولی با ۸ thread و ترافیک دائمی ربات، با تکرار کافی (و چند IP برای دور زدن rate limit) شدنی است. نتیجه: ادمین کامل ← root (SEC-03).

**راه‌حل:** وضعیت هر درخواست را در `flask.g` نگه دارید:
```python
from flask import g

@app.before_request
def auth_req():
    g.api_accessed = False
    ...
    if apiKeyExist:
        g.api_accessed = True

@app.post(f'{APP_PREFIX}/api/authenticate')
def API_AuthenticateLogin():
    if g.get("api_accessed"):
        ...
```

---

<a id="sec-02"></a>
## SEC-02 🟠 افشای `flask_secret_key` و پسوردها در `/api/getDashboardConfiguration`

**محل:** [DashboardConfig.py:25](../src/modules/DashboardConfig.py#L25)، [DashboardConfig.py:356-365](../src/modules/DashboardConfig.py#L356-L365)، [dashboard.py:1109-1111](../src/dashboard.py#L1109-L1111)

**مشکل:** فقط `totp_key` و `auth_req` مخفی می‌شوند. خروجی شامل این‌هاست:
- `Server.flask_secret_key` ← با آن می‌توان **کوکی session ادمین جعل کرد** (Flask sessionها امضاشده‌اند، نه سمت سرور).
- `Account.password` (هش bcrypt)، `Database.password`، `Email.email_password`.

هر دارنده‌ی API Key (ربات خودتان و **ربات دوستتان**) و هر XSS احتمالی در فرانت، این‌ها را می‌خواند.

**راه‌حل:**
```python
self.hiddenAttribute = ["totp_key", "auth_req", "flask_secret_key", "password", "email_password"]
```
و برای `password`ها در `toJson` فقط یک پرچم `"***"`/`has_password` برگردانید. توجه: `SetConfig` هم از `hiddenAttribute` برای جلوگیری از نوشتن استفاده می‌کند؛ برای `Account.password` و `Email.email_password` باید مسیر جداگانه‌ی نوشتن را حفظ کنید (مثلاً دو لیست `hiddenFromRead` و `readOnly`).

---

<a id="sec-03"></a>
## SEC-03 🔴 API Key = ادمین کامل = اجرای فرمان root

**محل:**
- تغییر `PreUp/PostUp/PreDown/PostDown` و روشن کردن اینترفیس: [dashboard.py:651-664](../src/dashboard.py#L651-L664) ← [WireguardConfiguration.py:1777-1801](../src/modules/WireguardConfiguration.py#L1777-L1801)
- بازنویسی کامل فایل کانفیگ: [dashboard.py:693-706](../src/dashboard.py#L693-L706)
- آپلود بکاپ که `gunicorn.conf.py` و `plugins/` را بازنویسی می‌کند: [GlobalBackup.py:510-517](../src/modules/GlobalBackup.py#L510-L517)
- مدیریت API Keyها (لیست/ساخت/حذف کلید بقیه): [dashboard.py:1138-1168](../src/dashboard.py#L1138-L1168)
- تغییر پسورد ادمین بدون دانستن پسورد فعلی: `updateDashboardConfigurationItem` با `Other.welcome_session = true` ([dashboard.py:1122-1136](../src/dashboard.py#L1122-L1136)) و بعد `Welcome_Finish` ([dashboard.py:1923-1947](../src/dashboard.py#L1923-L1947)) یا خود `Account.password` ([DashboardConfig.py:260-268](../src/modules/DashboardConfig.py#L260-L268)).
- غیرفعال کردن TOTP ادمین: `Account.enable_totp = false` از همان endpoint.

**پیامد:** هر کس API Key را داشته باشد، root سرور است. API Key شما در ریپو افشا شده (سند ۰۱). همچنین **ربات دوستتان** که با API Key خودش به همین پنل وصل است، همین قدرت را روی اینترفیس‌های شما و کل سرور دارد (و می‌تواند API Key ربات شما را هم بخواند).

**راه‌حل (کوتاه‌مدت):** در `auth_req` برای درخواست‌هایی که با API Key می‌آیند، یک allowlist از endpointهای لازم ربات بگذارید و بقیه را رد کنید:
```python
BOT_ALLOWED = {
    "/api/getWireguardConfigurationInfo", "/api/getAvailableIPs/", "/api/addPeers/",
    "/api/downloadPeer/", "/api/updatePeerSettings/", "/api/deletePeers/",
    "/api/restrictPeers/", "/api/allowAccessPeers/", "/api/resetPeerData/",
    "/api/savePeerScheduleJob", "/api/deletePeerScheduleJob", "/api/handshake",
}
if g.api_accessed and not any(request.path.startswith(APP_PREFIX + p) for p in BOT_ALLOWED):
    return ResponseObject(False, "API key not permitted for this endpoint", status_code=403)
```
توجه: قابلیت «Cross-Server» خود پنل (مدیریت چند سرور از یک UI) هم با API Key کار می‌کند؛ اگر از آن استفاده می‌کنید، برای آن یک کلید جدا با دسترسی کامل نگه دارید (که نیاز به scope دارد).

**راه‌حل (بلندمدت):** API Key با scope (لیست اینترفیس‌های مجاز + سطح دسترسی) — به [FEAT-05](09-Feature-Proposals.md#feat-05) مراجعه کنید.

---

<a id="sec-04"></a>
## SEC-04 🟡 API Keyهای منقضی تا ری‌استارت معتبر می‌مانند

**محل:** [DashboardConfig.py:105](../src/modules/DashboardConfig.py#L105)، [DashboardConfig.py:192-218](../src/modules/DashboardConfig.py#L192-L218)، [dashboard.py:429](../src/dashboard.py#L429)

**مشکل:** لیست کلیدها فقط در زمان شروع و هنگام ساخت/حذف کلید خوانده می‌شود و فیلتر انقضا فقط در همان کوئری است. بررسی در `auth_req` فقط `x.Key == apiKey` است. کلیدی که `ExpiredAt` آن فردا است، **تا ری‌استارت بعدی** معتبر می‌ماند. علاوه بر این:
- مقایسه با `==` (نه `hmac.compare_digest`)،
- کلیدها به‌صورت متن ساده در دیتابیس ذخیره می‌شوند (و در Global Backup هم هستند)،
- شرط `if DashboardConfig.GetConfig('Server', 'dashboard_api_key'):` در [dashboard.py:1140](../src/dashboard.py#L1140)، [1147](../src/dashboard.py#L1147)، [1162](../src/dashboard.py#L1162) همیشه True است (tuple است) ← بررسی «فعال بودن قابلیت» عملاً کار نمی‌کند.

**راه‌حل:**
```python
import hmac
from datetime import datetime
now = datetime.now()
apiKeyExist = any(
    hmac.compare_digest(x.Key, apiKey) and
    (x.ExpiredAt is None or datetime.strptime(x.ExpiredAt, "%Y-%m-%d %H:%M:%S") > now)
    for x in DashboardConfig.DashboardAPIKeys
)
```
و `GetConfig(...)[1]` به‌جای `GetConfig(...)`.

---

<a id="sec-05"></a>
## SEC-05 🟠 کلید خصوصی اینترفیس در خروجی API

**محل:** [WireguardConfiguration.py:1620-1641](../src/modules/WireguardConfiguration.py#L1620-L1641)، [AmneziaConfiguration.py:44-50](../src/modules/AmneziaConfiguration.py#L44-L50)

`toJson()` فیلد `PrivateKey` اینترفیس را در `getWireguardConfigurations` و `getWireguardConfigurationInfo` برمی‌گرداند. ربات‌ها فقط `Address` و `PublicKey` و `ListenPort` لازم دارند. همین رفتار باعث شد کلید سرور در دیتابیس ربات ذخیره و بعد افشا شود (سند ۰۱).

**راه‌حل:** `PrivateKey` را از `toJson` حذف کنید و فقط در صفحه‌ی ویرایش کانفیگ (که الان از `getWireguardConfigurationRawFile` استفاده می‌کند) نمایش دهید. اگر فرانت جایی به آن نیاز دارد، یک endpoint جدا فقط برای session ادمین (نه API Key) بسازید.

---

<a id="sec-06"></a>
## SEC-06 🟡 CSRF با endpointهای GET

**محل:**
- [dashboard.py:642](../src/dashboard.py#L642) `GET /api/toggleWireguardConfiguration` — خاموش/روشن کردن اینترفیس
- [dashboard.py:2065](../src/dashboard.py#L2065) `GET /api/oidc/toggle`
- [dashboard.py:2097](../src/dashboard.py#L2097) `GET /api/clients/toggleStatus`
- [dashboard.py:1904](../src/dashboard.py#L1904) `GET /api/Welcome_GetTotpLink` (کلید TOTP را بازتولید می‌کند)
- [dashboard.py:801](../src/dashboard.py#L801) `GET /api/createWireguardConfigurationBackup`

**مشکل:** کوکی session هیچ `SameSite`ی ندارد (پیش‌فرض Flask). یک لینک یا redirect در یک سایت/کانال تلگرام کافی است تا مرورگر ادمین لاگین‌شده `.../api/toggleWireguardConfiguration?configurationName=wg0` را باز کند و **همه‌ی کاربران VPN قطع شوند**.

**راه‌حل:**
```python
app.config.update(
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=True,      # اگر پشت HTTPS هستید
    SESSION_COOKIE_HTTPONLY=True,
)
```
و این endpointها را به `POST` تبدیل کنید (فرانت هم باید عوض شود).

---

<a id="sec-07"></a>
## SEC-07 🟡 Rate limiter پشت reverse proxy

**محل:** [dashboard.py:502-505](../src/dashboard.py#L502-L505)، [client.py:60](../src/client.py#L60)، [client.py:100](../src/client.py#L100)، [client.py:113](../src/client.py#L113) و ...؛ پیاده‌سازی در [Utilities.py:266-293](../src/modules/Utilities.py#L266-L293)

**مشکل:**
1. کلید limiter `request.remote_addr` است و `ProxyFix` استفاده نشده. پشت nginx (که در اسکریپت‌های شما استفاده شده) **همه‌ی کاربران `127.0.0.1` هستند** ← هر کسی با ۵ تلاش در دقیقه، ورود ادمین را برای همه قفل می‌کند.
2. تلاش‌های **موفق** هم شمرده می‌شوند.
3. حد در هر IP است نه هر username.

**راه‌حل:**
```python
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # فقط اگر واقعاً پشت یک proxy مطمئن هستید
```
و فقط تلاش‌های ناموفق را بشمارید (`check` قبل از ورود، `hit` فقط در شکست).

---

<a id="sec-08"></a>
## SEC-08 🟡 Session ادمین ۳۱ روزه و غیرقابل ابطال

**محل:** [dashboard.py:517](../src/dashboard.py#L517)، [dashboard.py:535](../src/dashboard.py#L535)، [dashboard.py:544-549](../src/dashboard.py#L544-L549)

`session.permanent = True` با `PERMANENT_SESSION_LIFETIME` پیش‌فرض (۳۱ روز). چون session در کوکی امضاشده است، `signout` فقط کوکی مرورگر خودتان را پاک می‌کند؛ کوکی دزدیده‌شده تا ۳۱ روز معتبر است، حتی بعد از تغییر پسورد. کوکی `authToken` هم بدون `HttpOnly`/`Secure` ست می‌شود.

**راه‌حل:** `PERMANENT_SESSION_LIFETIME = timedelta(hours=12)` و یک `session_epoch` در ini که با تغییر پسورد/خروج از همه‌جا افزایش یابد و در `auth_req` با مقدار session مقایسه شود.

---

<a id="sec-09"></a>
## SEC-09 🟡 نوشتن غیراتمی `wg-dashboard.ini`

**محل:** [DashboardConfig.py:323-330](../src/modules/DashboardConfig.py#L323-L330) و [DashboardConfig.py:21-24](../src/modules/DashboardConfig.py#L21-L24)، [DashboardConfig.py:96-100](../src/modules/DashboardConfig.py#L96-L100)

فایل با `open(..., "w+")` اول **خالی** و بعد نوشته می‌شود. اگر پروسه وسط نوشتن کشته شود (OOM، ری‌استارت، پر شدن دیسک)، ini خالی می‌ماند و در اجرای بعدی **مقادیر پیش‌فرض** ساخته می‌شوند: `username=admin`، `password=admin`، `welcome_session=true` و یک `flask_secret_key` جدید — پنل با رمز پیش‌فرض روی اینترنت!

**راه‌حل:**
```python
def SaveConfig(self) -> bool:
    with self.lock:
        tmp = DashboardConfig.ConfigurationFilePath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            self.__config.write(f)
            f.flush(); os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, DashboardConfig.ConfigurationFilePath)
        return True
```
و اگر ini وجود دارد ولی بخش `Account` ندارد، به‌جای ساختن پیش‌فرض، با خطا متوقف شوید.

---

<a id="sec-10"></a>
## SEC-10 🟡 فایل‌های حساس برای کاربران لوکال

- [wgd.sh:367](../src/wgd.sh#L367): `sudo chmod -R 755 /etc/wireguard/` ← کلید خصوصی سرور و PSKها برای همه‌ی کاربران سیستم قابل خواندن است. باید `chmod 700 /etc/wireguard && chmod 600 /etc/wireguard/*.conf` باشد.
- PSK در فایل موقت `/tmp/wgd_psk_<uuid>` با مجوز پیش‌فرض (معمولاً 644) نوشته می‌شود: [WireguardConfiguration.py:705-710](../src/modules/WireguardConfiguration.py#L705-L710)، [WireguardConfiguration.py:835-845](../src/modules/WireguardConfiguration.py#L835-L845)، [WireguardConfiguration.py:1182-1187](../src/modules/WireguardConfiguration.py#L1182-L1187)، [Peer.py:131-138](../src/modules/Peer.py#L131-L138)، [Peer.py:221-226](../src/modules/Peer.py#L221-L226)، [AmneziaPeer.py:74-85](../src/modules/AmneziaPeer.py#L74-L85). نام فایل با `random.Random()` (غیررمزنگارانه) ساخته می‌شود و `open(..., "w")` لینک نمادین را دنبال می‌کند.
- فایل قفل در `/tmp/wgdashboard_<name>.lock` با `open("a+")` به‌عنوان root ([WireguardConfiguration.py:111](../src/modules/WireguardConfiguration.py#L111)، [Utilities.py:211](../src/modules/Utilities.py#L211)) ← حمله‌ی symlink توسط کاربر لوکال.

**راه‌حل:** یک helper مشترک:
```python
import tempfile, os
def write_psk_tmp(psk: str) -> str:
    fd, path = tempfile.mkstemp(prefix="wgd_psk_", dir="/run")  # 0600 به‌طور پیش‌فرض
    with os.fdopen(fd, "w") as f:
        f.write(psk)
    return path
```
و فایل‌های قفل را در `/run/wgdashboard/` (با مالکیت root و 0700) بسازید.

---

<a id="sec-11"></a>
## SEC-11 🟡 پرتال Client با ثبت‌نام عمومی

**محل:** [DashboardConfig.py:86-89](../src/modules/DashboardConfig.py#L86-L89)، [client.py:58-67](../src/client.py#L58-L67)

`Clients.enable = true` و `sign_up = true` پیش‌فرض‌اند. OxBot از این پرتال استفاده نمی‌کند، ولی هر کسی می‌تواند در `/client` ثبت‌نام کند (پر شدن دیتابیس، ارسال ایمیل ریست پسورد با سرور SMTP شما، سطح حمله‌ی اضافه). کد ریست پسورد ۶ رقمی است و rate limit آن هم مشکل SEC-07 را دارد.

**راه‌حل:** در Settings، Clients را غیرفعال کنید (یا در ini: `[Clients] enable = false`).

---

<a id="sec-12"></a>
## SEC-12 🟡 ارسال IP کاربران به سرویس خارجی (حریم خصوصی)

**محل:** [dashboard.py:1581-1582](../src/dashboard.py#L1581-L1582)، [dashboard.py:1817](../src/dashboard.py#L1817)، [dashboard.py:1859-1860](../src/dashboard.py#L1859-L1860)

IP واقعی کاربران VPN (endpointها) برای geolocation به `http://ip-api.com` و با **HTTP رمزنشده** فرستاده می‌شود. برای سرویس VPN در شرایط ایران این نشت حریم خصوصی کاربران است. پیشنهاد: استفاده از دیتابیس آفلاین (MaxMind GeoLite2) یا دست‌کم قابل غیرفعال‌سازی کردن.

---

<a id="sec-13"></a>
## SEC-13 🟢 TOTP

**محل:** [dashboard.py:524](../src/dashboard.py#L524)، [dashboard.py:1917-1921](../src/dashboard.py#L1917-L1921)

`pyotp.TOTP(key).now() == data['totp']` — بدون `valid_window` (اختلاف ساعت چند ثانیه‌ای ورود را خراب می‌کند)، مقایسه‌ی غیر constant-time و بدون جلوگیری از استفاده‌ی مجدد کد. پیشنهاد: `TOTP(key).verify(code, valid_window=1)` و ذخیره‌ی آخرین کد مصرف‌شده.

---

<a id="sec-14"></a>
## SEC-14 🟢 Path traversal (فقط با دسترسی ادمین)

- [dashboard.py:1956-1970](../src/dashboard.py#L1956-L1970): `lang_id` بدون اعتبارسنجی در مسیر فایل استفاده می‌شود؛ مقدار `../../...` باعث خواندن هر فایل `.json` می‌شود و `/api/locale` **بدون احراز هویت** آن را برمی‌گرداند ([dashboard.py:460](../src/dashboard.py#L460)). راه‌حل: فقط مقادیر موجود در `supported_locales.json`.
- [dashboard.py:614-635](../src/dashboard.py#L614-L635): `data["Backup"]` و `ConfigurationName` اعتبارسنجی نمی‌شوند و `shutil.copy` به‌عنوان root به مسیر دلخواه کپی می‌کند. نام کانفیگ جدید هم (برخلاف rename) با regex بررسی نمی‌شود. راه‌حل: همان regex [WireguardConfiguration.py:1819](../src/modules/WireguardConfiguration.py#L1819) و `os.path.basename` برای Backup.

---

<a id="sec-15"></a>
## SEC-15 🟢 پیوست‌های ایمیل پاک نمی‌شوند

**محل:** [dashboard.py:2018-2026](../src/dashboard.py#L2018-L2026)

فایل `.conf` (حاوی private key کاربر) در `./attachments` نوشته و بعد از ارسال حذف نمی‌شود. راه‌حل: بعد از `EmailSender.send` با `try/finally` حذف شود.

---

<a id="sec-16"></a>
## SEC-16 🟢 CORS باز

**محل:** [dashboard.py:399-403](../src/dashboard.py#L399-L403)

`origins: "*"` برای همه‌ی `/api/*`. چون `supports_credentials` خاموش است، کوکی‌ها در درخواست cross-origin خوانده نمی‌شوند، پس خطر مستقیم پایین است؛ ولی بهتر است فقط originهای لازم (قابلیت Cross-Server خود پنل) مجاز باشند.

---

## نکات تکمیلی (Hardening)
- پنل را روی `127.0.0.1` bind کنید و فقط از طریق nginx با TLS در دسترس بگذارید ([DashboardConfig.py:38-39](../src/modules/DashboardConfig.py#L38-L39) پیش‌فرض `0.0.0.0:10086` است).
- مسیر پنل را با `app_prefix` تصادفی کنید (کاهش اسکن خودکار).
- `python-jose` برای OIDC نگهداری فعالی ندارد؛ در صورت استفاده از OIDC به `PyJWT` یا `authlib` مهاجرت کنید.
