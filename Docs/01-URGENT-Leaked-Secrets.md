# ۰۱ — 🔴 اطلاعات محرمانه‌ی افشاشده در ریپوی عمومی (فوری)

> در این سند **هیچ** مقدار محرمانه‌ای تکرار نشده؛ فقط نام فایل و نوع داده آمده است.

## وضعیت
- ریپو: `github.com/MasterALiReza/WgDashboard` — **Public** (با `gh repo view` بررسی شد).
- پوشه‌ی `scratch/` در `.gitignore` هست ولی **قبل از آن commit شده** و هنوز track می‌شود (`git ls-files scratch` = ۵۷ فایل).
- اولین commit این فایل‌ها: `624bb09` در **2026-07-18**. یعنی حدود **۳ ماه** است که عمومی‌اند. فرض را بر این بگذارید که دیده شده‌اند.

## وضعیت اقدامات (به‌روزرسانی 2026-10-15)

| کار | وضعیت |
|-----|--------|
| Private کردن ریپو | ✅ انجام شد (تأییدشده: `visibility: PRIVATE`، `forkCount: 0`) |
| نسخه‌ی پشتیبان خصوصی از فایل‌ها، **بیرون** از ریپو | ✅ `_PRIVATE_BACKUP_leaked_files_2026-10-14\` و `EXTRA_BACKUP_before_fix_2026-10-15.bundle` (کل تاریخچه‌ی قبلی و فایل‌ها، verify‌شده) |
| حذف از نسخه‌ی فعلی + سخت‌سازی `.gitignore` | ✅ انجام شد (اعمال روی `main`، مسدودسازی کامل دیتابیس، زیپ‌ها، پلاگین‌ها و اسکرچ) |
| اسکن کل تاریخچه برای اسرار دیگر | ✅ انجام شد و پاکسازی گردید |
| **پاک کردن از تاریخچه‌ی git و force push** | ✅ **انجام شد.** (`scratch`, `GlobalBackups`, `db/wg-dashboard.sqlite3`, `ssh-mcp-server` از تمام تاریخچه‌ی ریپازیتوری حذف شدند و روی `origin/main` اعمال گردید؛ `git log` خروجی 0 تأیید شد) |
| رفع نشت کلید خصوصی در API (SEC-05) | ✅ **انجام شد.** فیلد `PrivateKey` از متدهای `toJson()` در `WireguardConfiguration.py` و `AmneziaConfiguration.py` حذف شد و فرانت‌اند ایمن گردید. |
| چرخش اسرار (Rotation) | ⏳ باید توسط شما انجام شود (مرحله ۲) |

> ⚠️ پوشه‌های پشتیبان خارج از ریپو حاوی اسرار قبلی هستند. بعد از تمام شدن چرخش اسرار، اگر به آن نیازی ندارید پاکش کنید، یا آن را رمزنگاری‌شده (مثلاً با 7-Zip و AES-256) نگه دارید.

## فهرست چیزهایی که افشا شده

| شدت | فایل | محتوا | پیامد |
|------|------|--------|--------|
| 🔴 | `scratch/*.py` (حدود ۴۰ اسکریپت paramiko، مثل `check_panel_live.py`, `direct_server_migration.py`, ...) | IP و **پسورد root** دست‌کم ۳ سرور (سرور فعلی پنل، سرور قدیمی، سرور ربات) | کنترل کامل سرورها |
| 🔴 | `scratch/remote_global_config.php` | **توکن ربات تلگرام**، یوزر/پسورد/نام دیتابیس MySQL ربات، `backup_secure_token`، آیدی ادمین | کنترل کامل ربات، پیام دادن به همه‌ی کاربران، دستکاری پرداخت‌ها و دیتابیس فروش |
| 🔴 | `scratch/bot_marzban_panel_dump.txt` | آدرس پنل‌ها و ستون `password_panel` = **API Key پنل WGDashboard** | دسترسی ادمین کامل به پنل ← با `PostUp` اجرای فرمان به‌عنوان **root** (به SEC-03 مراجعه کنید) |
| 🔴 | `scratch/bot_user_info_dump.txt` (۴.۹ مگابایت) | رکورد کامل کاربران؛ شامل `private_key` و `preshared_key` هر peer و **`configuration.PrivateKey` = کلید خصوصی سرور WireGuard** (۱۶۱۶ بار) | جعل هویت سرور VPN (حمله‌ی MITM در سطح شبکه)، استفاده‌ی رایگان از اکانت هر کاربر |
| 🔴 | `scratch/bot_peers_mapping.json` (۳۲۲ کیلوبایت) | نگاشت public key ← private key همه‌ی peerها | همان بالا |
| 🟠 | `scratch/bot_admin_dump.txt` | آیدی تلگرام ادمین‌ها + هش bcrypt پسوردشان | حمله‌ی آفلاین به پسورد ادمین‌های پنل وب ربات |
| 🟠 | `scratch/remote_restore_dry_run.py` | یوزرنیم و **پسورد ادمین پنل WGDashboard** به‌صورت متن ساده | ورود مستقیم به پنل |
| 🟠 | `scratch/test_remote_time.py` | IP سرور + مسیر کلید SSH | شناسایی زیرساخت |
| 🟡 | `scratch/remote_*.php` | کد منبع ربات (پنل وب، منطق فروش) | کمک به یافتن آسیب‌پذیری‌های ربات |
| 🟡 | `GlobalBackups/*.zip` (۳ فایل) | بکاپ کامل یک محیط تست (ini با هش پسورد، `flask_secret_key`، `totp_key`، API Keyها و کلید خصوصی اینترفیس تست) | اگر همین مقادیر جایی دوباره استفاده شده باشند، خطرناک است |
| 🟢 | `db/wg-dashboard.sqlite3` | فایل خالی | بی‌خطر، ولی نباید در ریپو باشد |

> چرا کلید خصوصی سرور در دیتابیس ربات بود؟ در نسخه‌های قبلی این فورک، `Peer.toJson()` کل شیء `configuration` (شامل `PrivateKey` اینترفیس) را برمی‌گرداند و ربات همان را در `invoice.user_info` ذخیره کرد. در کد فعلی این مشکل در `Peer.toJson` رفع شده ([Peer.py:61-67](../src/modules/Peer.py#L61-L67))، ولی **`WireguardConfiguration.toJson()` هنوز `PrivateKey` را برمی‌گرداند** ([WireguardConfiguration.py:1626](../src/modules/WireguardConfiguration.py#L1626)) و `getWireguardConfigurationInfo` آن را به ربات (و ربات دوستتان) می‌دهد. ← SEC-05

---

## چک‌لیست اقدام (به همین ترتیب)

### مرحله ۱ — جلوی دیده‌شدن بیشتر را بگیرید (۵ دقیقه)
- [ ] ریپو را در GitHub → Settings → **Change visibility → Private** کنید.
- [ ] در GitHub → Security بخش **Secret scanning alerts** را ببینید (احتمالاً توکن تلگرام را قبلاً شناسایی کرده).
- [ ] لیست Forkهای ریپو را نگاه کنید؛ forkها با private شدن ریپوی شما پاک نمی‌شوند.

### مرحله ۲ — چرخش (Rotation) همه‌ی اسرار — مهم‌تر از پاک کردن تاریخچه
پاک کردن فایل‌ها کافی نیست؛ هر چیزی که منتشر شده باید **باطل** شود.

- [ ] **توکن ربات تلگرام:** در @BotFather دستور `/revoke` ← توکن جدید را در `config.php` ربات بگذارید و webhook را دوباره ست کنید.
- [ ] **پسورد MySQL ربات** را عوض کنید. مطمئن شوید پورت MySQL فقط روی `127.0.0.1` باز است.
- [ ] **`backup_secure_token`** ربات را عوض کنید.
- [ ] **API Key پنل WGDashboard:** از Settings → API Keys کلید قدیمی را حذف و کلید جدید بسازید؛ مقدار جدید را در ستون `marzban_panel.password_panel` ربات قرار دهید. (حذف کلید فوراً اعمال می‌شود چون لیست کلیدها بعد از حذف دوباره خوانده می‌شود.)
- [ ] **پسورد ادمین پنل** را عوض کنید و TOTP را فعال کنید.
- [ ] **`flask_secret_key`** را در `src/wg-dashboard.ini` با یک مقدار تصادفی جدید جایگزین و پنل را ری‌استارت کنید (همه‌ی sessionهای قبلی باطل می‌شوند).
- [ ] **پسورد root همه‌ی سرورها** را عوض کنید؛ بهتر: ورود با کلید SSH و `PasswordAuthentication no` و `PermitRootLogin prohibit-password`.
- [ ] پسورد ادمین‌های پنل وب ربات را عوض کنید (هش‌هایشان افشا شده).

### مرحله ۳ — بررسی نفوذ (چون ۳ ماه عمومی بوده)
روی هر سرور:
```bash
last -a | head -50                       # ورودهای اخیر
grep -i "accepted" /var/log/auth.log* | tail -100
cat /root/.ssh/authorized_keys           # کلید ناشناس؟
crontab -l; ls -la /etc/cron.*           # کرون ناشناس؟
systemctl list-unit-files --state=enabled | head -80
grep -nE "^(Pre|Post)(Up|Down)" /etc/wireguard/*.conf /etc/amnezia/amneziawg/*.conf
ls -la <WGDashboard>/src/plugins/        # پلاگین ناشناس؟ (پلاگین‌ها به‌عنوان root اجرا می‌شوند)
```
در پنل: Settings → API Keys (کلید ناشناس؟)، و جدول `DashboardLog` در `src/db/wgdashboard_log.db` برای `API Action` های مشکوک.

### مرحله ۴ — کلیدهای WireGuard
- [ ] **کلید خصوصی اینترفیس(ها)** افشا شده. مهاجم با آن می‌تواند در صورت داشتن موقعیت MITM (مثلاً در سطح ISP) خودش را سرور جا بزند. چون WireGuard forward secrecy دارد، ترافیک گذشته رمزگشایی نمی‌شود، ولی جعل سرور در آینده ممکن است. راه درست: تولید کلید جدید برای اینترفیس و ارسال کانفیگ جدید به کاربران (از طریق ربات). اگر فعلاً ممکن نیست، دست‌کم یک اینترفیس جدید برای فروش‌های جدید بسازید.
- [ ] **کلید خصوصی کاربران** هم افشا شده؛ هر کسی می‌تواند با اکانت هر کاربر وصل شود. در چرخش کلید اینترفیس، کلید peerها را هم عوض کنید.

<a id="purge"></a>
### مرحله ۵ — پاک کردن از تاریخچه‌ی git
> این کار تاریخچه را بازنویسی و `main` را **force push** می‌کند. نسخه‌ی پشتیبان کامل تاریخچه‌ی قبلی از قبل در `_PRIVATE_BACKUP...\full-history-BEFORE-purge.bundle` ساخته شده است. ریپو فقط یک شاخه (`main`) و هیچ fork و tagی ندارد، پس عملیات ساده است.

در **Git Bash**، به ترتیب (از ابزار داخلی `git filter-branch` استفاده می‌شود؛ نیازی به نصب چیزی نیست):

**الف) بازنویسی روی یک کلون mirror موقت** (مسیر کوتاه لازم است؛ در مسیرهای طولانی ویندوز git خطای `'$GIT_DIR' too big` می‌دهد)
```bash
mkdir -p /c/wgdpurge && cd /c/wgdpurge && rm -rf m.git
git clone --mirror https://github.com/MasterALiReza/WgDashboard.git m.git && cd m.git
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch --force \
  --index-filter 'git rm -r -q --cached --ignore-unmatch -- scratch GlobalBackups db/wg-dashboard.sqlite3 ssh-mcp-server' \
  --prune-empty -- --all
git for-each-ref --format='%(refname)' refs/original | xargs -r -n1 git update-ref -d
git reflog expire --expire=now --all && git gc --prune=now --aggressive
```

**ب) بررسی قبل از push** (باید `0` چاپ کند)
```bash
git log --all --oneline -- scratch GlobalBackups | wc -l
```

**ج) ارسال به گیت‌هاب**
```bash
git push --force origin main
```

**د) همگام‌سازی کپی‌های محلی** (فایل‌های ignoreشده مثل `src/wg-dashboard.ini`، دیتابیس‌ها و `venv` دست نمی‌خورند؛ فقط فایل‌های حذف‌شده از working tree پاک می‌شوند که پشتیبانشان گرفته شده)
```bash
cd /c/Users/iWexort/Documents/Github/WGDashboard-main/WGDashboard-main
git fetch origin
git reset --hard origin/main
git -C .claude/worktrees/project-oxbot-deep-review-0b5ff0 rebase --onto origin/main 9e27712
git reflog expire --expire=now --all && git gc --prune=now
rm -rf /c/wgdpurge
```
(خط `rebase` شاخه‌ی بازبینی را روی تاریخچه‌ی جدید منتقل می‌کند؛ از commit حذف فقط تغییر `.gitignore` باقی می‌ماند.)

**ه) بعد از آن:** commitهای قدیمی ممکن است تا مدتی با SHA مستقیم در گیت‌هاب قابل دسترس بمانند. چون ریپو private و بدون fork است ریسک پایین است؛ برای حذف قطعی از GitHub Support درخواست «garbage collection / remove cached views» کنید. اگر پروژه را با `git clone` روی سرورها هم گذاشته‌اید، آن کلون‌ها را دوباره کلون یا مثل بند «د» reset کنید؛ وگرنه push بعدی از آن‌ها تاریخچه‌ی قدیمی را برمی‌گرداند.

### مرحله ۶ — جلوگیری از تکرار
`.gitignore` الان `scratch/` دارد ولی این‌ها را ندارد:
```gitignore
GlobalBackups/
src/GlobalBackups/
src/download/
src/attachments/
src/plugins/
db/
*.sqlite3
*.zip
!src/static/dist/**/*.zip
```
و پیشنهاد می‌شود یک pre-commit hook مثل `gitleaks` یا `detect-secrets` اضافه کنید:
```bash
pip install pre-commit
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.4
    hooks: [{id: gitleaks}]
```
اسکریپت‌های عملیاتی (paramiko) را هم طوری بنویسید که پسورد را از متغیر محیطی یا `~/.ssh/config` بخوانند، نه از متن کد.
