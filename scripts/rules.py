"""
drive-analyzer / rules.py — the classification knowledge.

Every file ends up in one of four buckets:
    keep     احتفظ      — personal, unique, or needed by the system/apps
    maybe    نص نص      — probably removable, but a human should glance at it
    delete   امسح       — regenerable junk: caches, temp, partial downloads, exact copies
    unknown  مش عارف    — we can't tell what it is; ask the owner

A verdict is (bucket, rule_id, reason_ar, action).
action:  move      -> safe to move into _to_delete/ (quarantine)
         system    -> don't move by hand; use the Windows tool named in the reason
         uninstall -> remove through Apps & Features / the app itself, not by deleting
         review    -> human decision
         none      -> nothing to do

Edit this file to teach the analyzer new patterns. Keep reasons short, in Egyptian Arabic,
and always say *why* (and how it comes back, for regenerable things).
"""

DAY = 86400.0

# ---- thresholds (seconds / bytes) -------------------------------------------------
INACTIVE_PROJECT = 30 * DAY        # node_modules/venv of a project untouched this long -> delete
OLD_INSTALLER = 30 * DAY           # installers in Downloads older than this -> delete
OLD_LOG = 30 * DAY
OLD_DOWNLOAD = 365 * DAY           # anything in Downloads older than this -> maybe
OLD_ARCHIVE = 2 * 365 * DAY
OLD_BIG_MEDIA = 2 * 365 * DAY
BIG_MEDIA = 1024 ** 3              # 1 GB
BIG_UNKNOWN = 10 * 1024 ** 2       # unknown-type files above this are worth asking about
BIG_FILE_ITEM = 100 * 1024 ** 2    # files above this are listed individually in the report

V = lambda b, r, why, act: (b, r, why, act)

import re
# downloaded movies / episodes / YouTube rips — watched once, usually safe to let go
RX_RIP = re.compile(r'(\b(2160p|1080p|720p|480p|web-?dl|webrip|bluray|blu-ray|brrip|bdrip|hdrip|dvdrip|camrip|hdcam|hdtv|'
                    r'x264|x265|h\.?264|h\.?265|hevc|yts|rarbg|psa|aac2?\.0)\b|\bs\d{1,2}e\d{1,2}\b|\[tt\d{7,}\])', re.I)
RX_YT = re.compile(r'\[[A-Za-z0-9_-]{11}\](\.f\d{2,4})?\.(mp4|webm|mkv|m4a|mp3|opus)$', re.I)
# cracked software / activators — a top malware vector
CRACK_WORDS = ('crack', 'keygen', 'activator', 'kmspico', 'pre-activated', 'activated', 'cracks', 'patch-only', '+ fix')
SECURITY_WHY = 'برنامج متكرك/أكتيفيتور — من أشهر مصادر الفيروسات، والنسخة دي غالبًا قديمة'
# yt-dlp / youtube-dl leftovers: unmerged format streams and temp files
RX_YTDLP_PART = re.compile(r'(\.f\d{2,4}\.(webm|mp4|m4a|mkv)|\.temp\.(mp4|mkv|webm|m4a)|\.part-frag\d+|\.ytdl)$', re.I)
INSTALLER_MARKERS = ('setup.exe', 'install.exe', 'installer.exe', 'autorun.inf')


# ---- extension families -----------------------------------------------------------
FAMILIES = {
    'documents': 'pdf doc docx docm xls xlsx xlsm xlsb ppt pptx pptm odt ods odp txt rtf md csv tsv epub mobi pages numbers key one onetoc2 vsdx pub tex bib',
    'images': 'jpg jpeg png gif heic heif webp bmp tif tiff raw cr2 cr3 nef arw dng orf rw2 svg ico avif jfif',
    'video': 'mp4 mov mkv avi wmv m4v webm mts m2ts 3gp flv mpg mpeg vob ts',
    'audio': 'mp3 wav flac m4a aac ogg wma opus aiff aif mid midi amr',
    'design': 'psd psb ai indd idml aep aet prproj mogrt blend fig sketch xd c4d fbx obj stl max ma mb drp ae afphoto afdesign afpub kra clip xcf cdr',
    'code': 'py pyw ipynb js mjs cjs ts tsx jsx java kt c cc cpp h hpp cs go rs php rb swift dart lua r m html htm css scss sass less vue svelte json jsonc yaml yml toml ini cfg xml sql sh bat cmd ps1 psm1 gradle lock env properties wasm map',
    'archives': 'zip rar 7z tar gz tgz bz2 xz zst cab lz lzma br',
    'installers': 'exe msi msix msixbundle appx appxbundle',
    'foreign_installers': 'apk aab xapk dmg pkg deb rpm appimage',
    'disk_images': 'iso img vhd vhdx vmdk vdi qcow2 ova ovf wim esd ext4 ext2 ext3 sparse',
    'ai_models': 'gguf safetensors ckpt pt pth onnx ggml',
    'fonts': 'ttf otf woff woff2 fon',
    'binaries': 'dll sys so dylib ocx drv mui cpl efi node pyd lib a o obj pdb jar class',
    'databases': 'db sqlite sqlite3 mdb accdb ldb frm ibd kdbx',
    'temp': 'tmp temp crdownload part partial download !ut opdownload dmp mdmp hdmp',
}
EXT_FAMILY = {e: fam for fam, exts in FAMILIES.items() for e in exts.split()}

FAMILY_AR = {
    'documents': 'مستندات', 'images': 'صور', 'video': 'فيديو', 'audio': 'صوت',
    'design': 'ملفات تصميم/مونتاج', 'code': 'كود ومشاريع', 'archives': 'أرشيفات مضغوطة',
    'installers': 'برامج تسطيب', 'foreign_installers': 'installers لأنظمة تانية',
    'disk_images': 'صور ديسكات/أجهزة افتراضية', 'ai_models': 'موديلات AI', 'fonts': 'خطوط',
    'binaries': 'ملفات برامج (dll/sys)', 'databases': 'قواعد بيانات', 'temp': 'مؤقت/ناقص',
    'other': 'أنواع تانية',
}

KEEP_REASON = {
    'documents': 'مستند شخصي — احتفظ بيه',
    'images': 'صورة — احتفظ بيها',
    'video': 'فيديو — احتفظ بيه',
    'audio': 'ملف صوت — احتفظ بيه',
    'design': 'ملف مشروع تصميم/مونتاج — احتفظ بيه',
    'code': 'كود/ملف مشروع — احتفظ بيه',
    'fonts': 'خط — احتفظ بيه',
    'binaries': 'ملف تابع لبرنامج — ماتلمسوش',
    'databases': 'قاعدة بيانات — غالبًا تبع برنامج أو مشروع',
    'archives': 'أرشيف حديث — احتفظ بيه',
    'installers': 'ملف تشغيل برنامج',
}


def ends(parts, *seq):
    n = len(seq)
    return len(parts) >= n and tuple(parts[-n:]) == seq


def contains_seq(parts, *seq):
    n = len(seq)
    return any(tuple(parts[i:i + n]) == seq for i in range(len(parts) - n + 1))


# ---- directory rules ----------------------------------------------------------------
# Each returns None or (verdict, terminal, protect).
#   terminal=True : whole subtree gets this verdict (one report item = this folder)
#   terminal=False: a "zone" default; deeper rules can still refine it
#   protect=True  : inside this zone, file-level rules are not applied (system folders)

CACHE_NAMES = {'cache', 'caches', 'code cache', 'gpucache', 'cache_data', 'cachedata', 'cacheddata',
               'shadercache', 'grshadercache', 'graphitedawncache', 'dawncache', 'dawngraphitecache',
               'cachedextensionvsixs', 'cachedprofilesdata', 'cache2', 'service worker',
               'blob_storage', 'crashpad', 'crash reports', 'squirreltemp', 'pending'}
ROOT_SYSTEM_KEEP = {'system volume information', 'recovery', 'boot', 'efi', 'config.msi',
                    'documents and settings', 'perflogs', 'msocache'}
ROOT_DISK_CLEANUP = {'windows.old', '$windows.~bt', '$windows.~ws', '$winreagent', '$sysreset',
                     '$getcurrent', 'esd'}
TOOL_DIRS = {'appdata', 'program files', 'program files (x86)', 'programdata', 'scoop', 'nodejs',
             'pipx', 'site-packages', 'lib', 'extensions', 'resources'}
SYNC_ROOT_PREFIXES = ('onedrive', 'google drive', 'my drive', 'dropbox', 'icloud', 'mega', 'pcloud')


def dir_rules(name, parts, rel, ctx):
    """name/parts are lower-case; rel is the original-case relative path of this directory."""
    d = len(parts) - 1  # depth (0 = top level of the scanned root)
    sysdrive = ctx.is_system_drive
    # inside a protected zone only the Windows-internal rules below may refine the verdict
    if ctx.in_protected and not (sysdrive and parts[0] == 'windows'):
        return None

    # --- root level of a Windows system drive
    if d == 0 and sysdrive:
        if name == '$recycle.bin':
            return V('delete', 'recycle_bin', 'سلة المحذوفات — فضّيها (Empty Recycle Bin)', 'system'), True, True
        if name in ROOT_DISK_CLEANUP:
            return V('delete', 'windows_upgrade_leftovers',
                     'بقايا تحديث/ترقية ويندوز — امسحها من Disk Cleanup › Clean up system files', 'system'), True, True
        if name in ROOT_SYSTEM_KEEP:
            return V('keep', 'system', 'ملفات نظام — ماتلمسهاش', 'none'), True, True
        if name in ('amd', 'nvidia'):
            return V('delete', 'driver_extract', 'ملفات فك درايفر قديمة — الدرايفر متسطب خلاص', 'move'), True, False
        if name == 'intel':
            return V('maybe', 'driver_extract', 'غالبًا ملفات فك درايفر Intel — راجعها', 'review'), True, False
        if name == 'windows':
            return V('keep', 'system', 'ويندوز نفسه — ماتلمسوش', 'none'), False, True
        if name in ('program files', 'program files (x86)', 'programdata'):
            return V('keep', 'installed_apps', 'برامج متسطبة — اللي مش عايزه شيله من Apps & Features', 'uninstall'), False, True
    if d == 0 and name == '$recycle.bin':
        return V('delete', 'recycle_bin', 'سلة المحذوفات للدرايف ده — فضّيها', 'system'), True, True
    if d == 0 and name == 'system volume information':
        return V('keep', 'system', 'ملفات نظام (System Restore) — ماتلمسهاش', 'none'), True, True

    # --- inside Windows
    if sysdrive and parts[0] == 'windows':
        if parts == ['windows', 'temp']:
            return V('delete', 'windows_temp', 'ملفات مؤقتة للويندوز — امسحها من Disk Cleanup (محتاجة Admin)', 'system'), True, True
        if parts == ['windows', 'softwaredistribution', 'download']:
            return V('delete', 'windows_update_cache', 'كاش تحديثات ويندوز — Disk Cleanup › Windows Update Cleanup', 'system'), True, True
        if parts in (['windows', 'minidump'], ['windows', 'livekernelreports']):
            return V('delete', 'crash_dumps', 'تقارير أعطال قديمة — مش محتاجها', 'system'), True, True
        if parts in (['windows', 'installer'], ['windows', 'winsxs']):
            return V('keep', 'system_critical',
                     'ماتمسحش منها يدوي أبدًا — لتصغيرها: DISM /Online /Cleanup-Image /StartComponentCleanup', 'none'), True, True
        return None

    # --- project / developer folders (anywhere)
    parent = rel.rsplit('/', 1)[0] if '/' in rel else ''
    in_tool = any(p.startswith('.') or p in TOOL_DIRS for p in parts[:-1])
    if name == 'node_modules':
        if in_tool or not ctx.has_file((parent + '/' if parent else '') + 'package.json'):
            return V('keep', 'tool_modules', 'مكتبات تابعة لبرنامج/أداة متسطبة — ماتمسحهاش', 'none'), True, False
        return ctx.project_cache(rel, 'node_modules', 'مكتبات npm — بترجع بـ npm install'), True, False
    if name in ('.venv', 'venv', 'env', '.env', 'virtualenv') and ctx.has_file(rel + '/pyvenv.cfg'):
        if in_tool:
            return V('keep', 'tool_modules', 'بيئة بايثون تابعة لأداة متسطبة — ماتمسحهاش', 'none'), True, False
        return ctx.project_cache(rel, 'python_venv', 'بيئة بايثون — بترجع بـ pip install -r requirements.txt'), True, False
    if name in ('__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox', '.ipynb_checkpoints'):
        return V('delete', 'python_cache', 'كاش بايثون — بيتعمل تاني لوحده', 'move'), True, False
    if name in ('.next', '.nuxt', '.parcel-cache', '.turbo', '.svelte-kit', '.angular', '.vite', '.expo', '.docusaurus'):
        return V('delete', 'build_cache', 'كاش build — بيتعمل تاني مع أول build', 'move'), True, False
    if name == 'target' and ctx.has_file((parent + '/' if parent else '') + 'Cargo.toml'):
        return V('delete', 'build_output', 'ناتج build لـ Rust — يرجع بـ cargo build', 'move'), True, False
    if name in ('bin', 'obj') and ctx.dir_has_ext(parent, ('csproj', 'vbproj', 'fsproj', 'sln')):
        return V('delete', 'build_output', 'ناتج build لـ .NET — يرجع مع أول build', 'move'), True, False
    if name in ('dist', 'build', 'out') and ctx.has_file((parent + '/' if parent else '') + 'package.json'):
        return V('maybe', 'build_output', 'ناتج build — غالبًا بيتعمل تاني، اتأكد إنه مش نسخة منشورة', 'review'), True, False
    if name == '.git':
        return V('keep', 'git_history', 'تاريخ المشروع (git) — جزء من المشروع', 'none'), True, False
    if name == '.gradle' or ends(parts, '.nuget', 'packages') or ends(parts, '.m2', 'repository') or name == '.pnpm-store':
        return V('delete', 'package_cache', 'كاش مكتبات (gradle/nuget/maven/pnpm) — بيتنزل تاني', 'move'), True, False
    if name in ('npm-cache', '_cacache') or ends(parts, 'pip', 'cache') or ends(parts, 'yarn', 'cache') \
            or ends(parts, 'npm', 'cache') or ends(parts, 'go', 'pkg', 'mod') or ends(parts, '.cargo', 'registry'):
        return V('delete', 'package_cache', 'كاش مدير الحزم — بيتنزل تاني لما تحتاجه', 'move'), True, False

    # --- AI models
    if name in ('.ollama',) or ends(parts, '.cache', 'huggingface') or ends(parts, '.lmstudio', 'models') \
            or (name == 'models' and 'stable-diffusion-webui' in parts) or ends(parts, '.cache', 'torch'):
        return V('maybe', 'ai_models', 'موديلات AI — كبيرة وبتتنزل تاني؛ امسح اللي مابقيتش تستخدمه بس', 'review'), True, False

    # --- media apps
    if name in ('media cache files', 'media cache', 'peak files', 'disk cache') and ('adobe' in parts or 'common' in parts):
        return V('delete', 'adobe_cache', 'كاش Adobe (Premiere/AE) — بيتعمل تاني لوحده', 'move'), True, False
    if 'auto-save' in name and 'adobe' in ' '.join(parts):
        return V('maybe', 'autosave', 'نسخ حفظ تلقائي قديمة — احتفظ بآخر نسخة بس', 'review'), True, False

    # --- games
    if ends(parts, 'steamapps', 'shadercache') or ends(parts, 'steamapps', 'downloading') or ends(parts, 'steamapps', 'temp'):
        return V('delete', 'game_cache', 'كاش/تحميلات Steam مؤقتة', 'move'), True, False
    if name == 'steamapps':
        return V('keep', 'games', 'ألعاب Steam — اللي مش عايزها شيلها من Steam نفسه', 'uninstall'), False, True

    # --- user profile app data
    if 'appdata' in parts:
        if ends(parts, 'appdata', 'local', 'temp') or ends(parts, 'appdata', 'local', 'crashdumps'):
            return V('delete', 'user_temp', 'ملفات مؤقتة — الملفات اللي شغالة هتتساب لوحدها', 'move'), True, False
        if ends(parts, 'microsoft', 'windows', 'inetcache') or ends(parts, 'microsoft', 'windows', 'webcache') \
                or name in ('d3dscache', 'dxcache', 'glcache', 'nv_cache', 'vkcache', 'fontcache') \
                or ends(parts, 'nvidia', 'computecache'):
            return V('delete', 'system_cache', 'كاش (إنترنت/كارت شاشة) — بيتعمل تاني لوحده', 'move'), True, False
        if name in CACHE_NAMES:
            return V('delete', 'app_cache', 'كاش برنامج — بيتعمل تاني لوحده (اقفل البرنامج قبل المسح)', 'move'), True, False
        if ends(parts, 'appdata', 'local', 'packages') or name in ('lxss',) or ends(parts, 'docker', 'wsl'):
            return V('keep', 'app_data', 'بيانات برامج/WSL — ماتمسحش يدوي', 'none'), False, True
        if name == 'appdata':
            return V('keep', 'app_data', 'بيانات وإعدادات البرامج', 'none'), False, False

    if name == '.cache' or name == '.thumbnails':
        return V('maybe', 'user_cache', 'كاش برامج في البروفايل — غالبًا يتمسح، بس فيه أحيانًا موديلات', 'review'), True, False

    # --- messengers / downloads subfolders
    if name in ('telegram desktop', 'whatsapp') and 'downloads' in parts:
        return V('maybe', 'messenger_downloads', 'ملفات نزلت من تيليجرام/واتساب — غالبًا اتشافت وخلصت', 'review'), True, False

    # --- backups
    if any(k in name for k in ('backup', 'نسخة احتياطية', '.bak', 'old_backup')) and d >= 0:
        return V('maybe', 'backup_folder', 'نسخة احتياطية — اتأكد إن عندك الأحدث وبعدين قرر', 'review'), True, False

    # --- cracked software and installer packages
    if any(w in name for w in CRACK_WORDS):
        return V('maybe', 'cracked_software', SECURITY_WHY, 'review'), True, False
    if ctx.is_installer_dir(rel):
        return V('maybe', 'installer_folder', 'فولدر تسطيب برنامج (فيه setup) — لو البرنامج متسطب أو مش محتاجه شيله', 'review'), True, False

    # --- portable apps / games installed outside Program Files
    if ctx.is_app_dir(rel) and not ctx.has_project_marker(rel):
        return V('unknown', 'portable_app', 'برنامج أو لعبة متسطبة هنا — لسه بتستخدمها؟ لو لأ شيلها من Uninstall', 'review'), True, False

    return None


def zone_flags(name, parts):
    """Flags that annotate a subtree without deciding it."""
    f = set()
    if name == 'downloads':
        f.add('downloads')
    if any(name.startswith(p) for p in SYNC_ROOT_PREFIXES) and len(parts) <= 4:
        f.add('sync')
    if name == 'desktop' and len(parts) <= 3:
        f.add('desktop')
    return f


# ---- file rules ------------------------------------------------------------------------
def file_rules(name, ext, fam, size, mtime, rel, flags, ctx):
    age = ctx.now - mtime if mtime else 0
    top = '/' not in rel
    parent = rel.rsplit('/', 1)[0] if '/' in rel else ''
    if ctx.is_download_dir(parent):
        flags = set(flags) | {'downloads'}

    if name in ('thumbs.db', 'ehthumbs.db', 'ehthumbs_vista.db', '.ds_store') or (name.startswith('._') and size <= 8192):
        return V('delete', 'os_junk', 'ملف زبالة من ويندوز/ماك (thumbnails) — بيتعمل تاني', 'move')
    if name.startswith('~$') and size < 1024 ** 2:
        return V('delete', 'office_lock', 'ملف قفل أوفيس فاضل من ملف اتقفل', 'move')
    if name.startswith(('thumbcache_', 'iconcache_')) and ext == 'db':
        return V('delete', 'thumb_cache', 'كاش الصور المصغرة — Disk Cleanup › Thumbnails', 'system')
    if top and ctx.is_system_drive and name in ('pagefile.sys', 'swapfile.sys'):
        return V('keep', 'pagefile', 'ذاكرة افتراضية للويندوز — حجمها يتظبط من System › Advanced', 'none')
    if top and ctx.is_system_drive and name == 'hiberfil.sys':
        return V('maybe', 'hiberfil', 'ملف الـ Hibernate — لو مش بتستخدمه: powercfg -h off (Admin)', 'system')
    if ext in ('tmp', 'temp'):
        return V('delete', 'temp_file', 'ملف مؤقت', 'move')
    if ext in ('crdownload', 'part', 'partial', 'download', '!ut', 'opdownload'):
        return V('delete', 'partial_download', 'تحميل ناقص ما كملش', 'move')
    if RX_YTDLP_PART.search(name):
        return V('delete', 'partial_download', 'بقايا تحميل yt-dlp ما اتدمجتش (format/temp) — مش ملف كامل', 'move')
    if ext in ('dmp', 'mdmp', 'hdmp'):
        return V('delete', 'crash_dump', 'تقرير عطل (crash dump) — مش محتاجه', 'move')
    if ext == 'log' and age >= OLD_LOG:
        return V('delete', 'old_log', 'لوج قديم', 'move')
    if ext in ('bak', 'old', 'orig') or name.endswith('~'):
        return V('maybe', 'backup_file', 'نسخة احتياطية لملف — اتأكد إن الأصل سليم', 'review')

    if any(w in name for w in CRACK_WORDS) and fam in ('installers', 'archives', 'disk_images', 'other'):
        return V('maybe', 'cracked_software', SECURITY_WHY, 'review')

    if fam == 'installers':
        looks_installer = (ext != 'exe' or size >= 50 * 1024 ** 2 or re.search(r'\d+\.\d+', name)
                           or any(k in name for k in ('setup', 'install', 'x64', 'x86', 'win64', 'win32', 'portable')))
        if 'downloads' in flags or 'desktop' in flags:
            if age >= OLD_INSTALLER:
                return V('delete', 'old_installer', 'ملف تسطيب قديم — لو احتجته تنزله تاني', 'move')
            return V('maybe', 'installer', 'ملف تسطيب حديث — لو سطّبته خلاص امسحه', 'review')
        if looks_installer and not ctx.is_app_dir(rel.rsplit('/', 1)[0] if '/' in rel else ''):
            return V('maybe', 'installer', 'شكله ملف تسطيب — لو البرنامج متسطب خلاص مش محتاجه', 'review')
        return None
    if fam == 'foreign_installers':
        return V('maybe', 'foreign_installer', 'installer لنظام تاني (أندرويد/ماك/لينكس)', 'review')
    if fam == 'disk_images':
        if name in ('ext4.vhdx', 'docker_data.vhdx') or 'wsl' in rel.lower() or 'docker' in rel.lower():
            return V('keep', 'wsl_disk', 'ديسك WSL/Docker — ماتمسحوش؛ لو كبير اعمله compact', 'none')
        return V('maybe', 'disk_image', 'صورة ديسك/جهاز افتراضي — كبيرة؛ امسحها لو مش محتاجها', 'review')
    if fam == 'ai_models' and size >= 100 * 1024 ** 2:
        return V('maybe', 'ai_model', 'موديل AI — بيتنزل تاني لو احتجته', 'review')
    if fam == 'archives':
        stem = name.rsplit('.', 1)[0]
        if stem.endswith('.tar'):
            stem = stem[:-4]
        parent = rel.rsplit('/', 1)[0] if '/' in rel else ''
        if ctx.is_dir_ci((parent + '/' if parent else '') + stem):
            return V('maybe', 'extracted_archive', 'اتفك جنبه في فولدر بنفس الاسم — غالبًا الأرشيف مش محتاجه', 'review')
        if 'downloads' in flags and age >= 90 * 86400:
            return V('maybe', 'old_download', 'أرشيف في Downloads من فترة', 'review')
        if age >= OLD_ARCHIVE and size >= 100 * 1024 ** 2:
            return V('maybe', 'old_archive', 'أرشيف كبير ما اتلمسش من سنتين', 'review')
        return None
    if fam == 'video' and RX_YT.search(name):
        return V('maybe', 'youtube_rip', 'فيديو متحمّل من يوتيوب — تقدر تتفرج عليه أونلاين تاني', 'review')
    if fam == 'video' and RX_RIP.search(name):
        return V('maybe', 'downloaded_movie', 'شكله فيلم/حلقة متحمّلة — لو اتفرجت عليها امسحها', 'review')
    if 'downloads' in flags and age >= OLD_DOWNLOAD:
        return V('maybe', 'old_download', 'في Downloads من أكتر من سنة — انقله لمكانه أو امسحه', 'review')
    if fam == 'video' and size >= BIG_MEDIA and age >= OLD_BIG_MEDIA:
        return V('maybe', 'old_big_video', 'فيديو كبير قديم — لو مش بتفتحه انقله لهارد خارجي/كلاود', 'review')
    return None


def default_verdict(fam, size, ext):
    if fam in KEEP_REASON:
        return V('keep', 'type_' + fam, KEEP_REASON[fam], 'none')
    if fam in ('ai_models',):
        return V('keep', 'type_' + fam, 'ملف موديل صغير', 'none')
    if size >= BIG_UNKNOWN:
        return V('unknown', 'unknown_type', 'نوع ملف مش معروف (.%s) وحجمه كبير — ده إيه؟' % (ext or 'بدون امتداد'), 'review')
    return V('unknown', 'unknown_small', 'ملفات صغيرة نوعها مش معروف', 'review')


# ---- Google Drive specifics ---------------------------------------------------------------
def gdrive_rules(extra, size, mtime, ctx):
    if extra.get('trashed'):
        return V('delete', 'gdrive_trash', 'في الـ Trash ولسه بياخد من مساحتك — فضّي الـ Trash', 'system')
    if extra.get('owned') is False:
        return V('keep', 'gdrive_not_mine', 'مش ملكك — مش محسوب من مساحتك', 'none')
    mime = extra.get('mime') or ''
    if mime.startswith('application/vnd.google-apps'):
        return V('keep', 'gdrive_native', 'ملف Google Docs/Sheets — مساحته تقريبًا صفر', 'none')
    viewed = extra.get('viewed')
    if viewed and size >= 100 * 1024 ** 2 and ctx.now - viewed >= 2 * 365 * DAY:
        return V('maybe', 'gdrive_stale_big', 'ملف كبير ما اتفتحش من سنتين', 'review')
    return None


# ---- presentation & action policy (used by the report and the live panel) ------------------
RULE_TITLES = {
    'recycle_bin': 'سلة المحذوفات', 'windows_upgrade_leftovers': 'بقايا ترقية/تحديث ويندوز',
    'system': 'ملفات النظام', 'system_critical': 'مكونات ويندوز الحساسة', 'driver_extract': 'ملفات فك الدرايفرات',
    'installed_apps': 'البرامج المتسطبة', 'windows_temp': 'مؤقت ويندوز', 'windows_update_cache': 'كاش تحديثات ويندوز',
    'crash_dumps': 'تقارير الأعطال', 'crash_dump': 'تقارير الأعطال', 'node_modules': 'مكتبات npm (node_modules)',
    'python_venv': 'بيئات بايثون (venv)', 'tool_modules': 'مكتبات تابعة لأدوات متسطبة', 'python_cache': 'كاش بايثون',
    'build_cache': 'كاش البناء', 'build_output': 'ناتج البناء (build)', 'git_history': 'تاريخ git',
    'package_cache': 'كاش مديري الحزم (npm/pip/gradle…)', 'ai_models': 'موديلات AI', 'ai_model': 'موديلات AI',
    'adobe_cache': 'كاش Adobe', 'autosave': 'نسخ الحفظ التلقائي', 'game_cache': 'كاش الألعاب', 'games': 'الألعاب',
    'user_temp': 'الملفات المؤقتة', 'system_cache': 'كاش الإنترنت وكارت الشاشة', 'app_cache': 'كاش البرامج',
    'app_data': 'بيانات البرامج', 'user_cache': 'كاش البروفايل', 'messenger_downloads': 'تحميلات تيليجرام/واتساب',
    'backup_folder': 'النسخ الاحتياطية', 'backup_file': 'ملفات backup', 'cracked_software': 'برامج متكركة (خطر أمني)',
    'installer_folder': 'فولدرات تسطيب برامج', 'portable_app': 'برامج/ألعاب portable', 'os_junk': 'زبالة النظام (thumbs/DS_Store)',
    'office_lock': 'ملفات قفل أوفيس', 'thumb_cache': 'كاش الصور المصغرة', 'pagefile': 'الذاكرة الافتراضية',
    'hiberfil': 'ملف الـ Hibernate', 'temp_file': 'ملفات مؤقتة', 'partial_download': 'تحميلات ناقصة',
    'old_log': 'لوجات قديمة', 'old_installer': 'ملفات تسطيب قديمة', 'installer': 'ملفات تسطيب',
    'foreign_installer': 'installers لأنظمة تانية', 'wsl_disk': 'ديسكات WSL/Docker', 'disk_image': 'صور ديسكات وISO وماشينات',
    'extracted_archive': 'أرشيفات اتفكت خلاص', 'old_download': 'تحميلات قديمة', 'old_archive': 'أرشيفات قديمة كبيرة',
    'youtube_rip': 'فيديوهات يوتيوب متحمّلة', 'downloaded_movie': 'أفلام ومسلسلات متحمّلة', 'old_big_video': 'فيديوهات كبيرة قديمة',
    'duplicate': 'نسخ مكررة 100%', 'probable_duplicate': 'غالبًا مكرر', 'unknown_type': 'أنواع مش معروفة (كبيرة)',
    'unknown_small': 'ملفات صغيرة مش معروفة', 'gdrive_trash': 'سلة Drive', 'gdrive_not_mine': 'مش ملكك',
    'gdrive_native': 'ملفات Google Docs', 'gdrive_stale_big': 'ملفات كبيرة ما اتفتحتش', 'override': 'قراراتك',
}
for _fam, _lab in FAMILY_AR.items():
    RULE_TITLES.setdefault('type_' + _fam, _lab)

# never selectable for delete from the panel — Windows must handle these itself
LOCKED_RULES = {'system', 'system_critical', 'installed_apps', 'pagefile', 'app_data', 'wsl_disk', 'tool_modules',
                'games', 'recycle_bin', 'windows_upgrade_leftovers', 'windows_temp', 'windows_update_cache',
                'thumb_cache', 'hiberfil', 'gdrive_not_mine'}
# the Windows tool the panel can open instead
WIN_ACTION = {'recycle_bin': 'empty_bin', 'windows_upgrade_leftovers': 'cleanmgr', 'windows_temp': 'cleanmgr',
              'windows_update_cache': 'cleanmgr', 'thumb_cache': 'cleanmgr', 'installed_apps': 'apps', 'games': 'apps',
              'hiberfil': 'power'}


def locked(rule, action):
    return rule in LOCKED_RULES or (rule in ('crash_dumps', 'crash_dump') and action == 'system')
