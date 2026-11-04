#!/usr/bin/env python3
"""
drive_analyzer.py — inventory a drive/folder, classify every file into
keep / maybe / delete / unknown, and build an Arabic HTML report.
Python 3.8+ standard library only (runs on Windows, Linux, macOS).

  analyze ROOT --out DIR [--display-root "D:\\"] [--budget SEC] [--no-hash]
        scan + duplicates + classify + report in one go. Resumable: if --budget runs out
        it exits with code 3; run the same command again to continue.

  scan / dupes / classify / report        the same steps one at a time (all take --db)
  import-gdrive FILE.jsonl --db DB        records from the Google Drive connector
  import-listing FILE.tsv --db DB         rel<TAB>size<TAB>mtime[<TAB>ctime] (e.g. from list_files.ps1)
  plan --db DB --out plan.json (--items FILE | --buckets delete[,maybe])
  apply plan.json [--confirm]             move targets into <root>/_to_delete/<stamp>/ (dry-run without --confirm)
  undo MANIFEST.csv [--confirm]           put moved files back

Read-only until `apply --confirm`. Nothing is ever deleted permanently by this script.
"""
import argparse, csv, hashlib, heapq, json, os, shutil, sqlite3, stat, sys, time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rules as R  # noqa: E402

QUARANTINE = '_to_delete'
OUT_MARK = '.drive-analyzer-out'
CLOUD_ATTRS = 0x400000 | 0x40000 | 0x1000   # recall-on-data-access | recall-on-open | offline
BUCKETS = ('keep', 'maybe', 'delete', 'unknown')

# Windows consoles default to cp1252/cp437 — Arabic text must not crash a run
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


# ------------------------------------------------------------------ db
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS units(name TEXT PRIMARY KEY, done INTEGER, files INTEGER, secs REAL);
CREATE TABLE IF NOT EXISTS dirs(rel TEXT PRIMARY KEY, rel_l TEXT);
CREATE TABLE IF NOT EXISTS errors(rel TEXT, err TEXT);
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY, rel TEXT, parent TEXT, name TEXT, ext TEXT, size INTEGER,
  mtime REAL, ctime REAL, atime REAL, ondisk INTEGER DEFAULT 1, xid TEXT, extra TEXT,
  dup INTEGER, dup_of TEXT, dupconf INTEGER,
  bucket TEXT, rule TEXT, reason TEXT, action TEXT, item TEXT);
CREATE INDEX IF NOT EXISTS ix_rel ON files(rel);
CREATE INDEX IF NOT EXISTS ix_parent ON files(parent);
CREATE INDEX IF NOT EXISTS ix_dirs_l ON dirs(rel_l);
"""


def connect(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=NORMAL')
    return db


def meta_get(db, k, default=None):
    r = db.execute('SELECT v FROM meta WHERE k=?', (k,)).fetchone()
    return json.loads(r[0]) if r else default


def meta_set(db, k, v):
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (k, json.dumps(v, ensure_ascii=False)))


def split_name(rel):
    parent, _, name = rel.rpartition('/')
    ext = name.rsplit('.', 1)[1].lower() if '.' in name.strip('.') else ''
    if len(ext) > 12 or ' ' in ext:
        ext = ''
    return parent, name, ext


def progress(out_dir, **kw):
    if not out_dir:
        return
    kw['at'] = datetime.now().strftime('%H:%M:%S')
    try:
        with open(os.path.join(out_dir, 'progress.json'), 'w', encoding='utf-8') as f:
            json.dump(kw, f, ensure_ascii=False)
    except OSError:
        pass


# ------------------------------------------------------------------ scan (filesystem)
def scan(db, root, display_root=None, budget=None, excludes=(), out_dir=None):
    root = os.path.abspath(root)
    t0 = time.time()
    if not meta_get(db, 'root'):
        meta_set(db, 'root', root)
        meta_set(db, 'source', 'fs')
        meta_set(db, 'display_root', display_root or (root if root.endswith(os.sep) else root + os.sep))
        meta_set(db, 'sep', '\\' if (display_root or root)[1:2] == ':' or os.name == 'nt' else '/')
        meta_set(db, 'scan_started', time.time())
        try:
            du = shutil.disk_usage(root)
            meta_set(db, 'disk', {'total': du.total, 'used': du.used, 'free': du.free})
        except OSError:
            pass
        names = set(os.listdir(root))
        meta_set(db, 'is_system_drive', 'windows' in {n.lower() for n in names}
                 and any(n.lower() == 'program files' for n in names))
        db.commit()
    excl = {os.path.normcase(os.path.abspath(e)) for e in excludes}

    try:
        top = sorted(os.scandir(root), key=lambda e: e.name.lower())
    except OSError as e:
        sys.exit('cannot read root: %s' % e)
    units = [('<root-files>', None)] + [(e.name, e) for e in top
                                       if _is_real_dir(e) and not _excluded(e, excl)]
    done = {r[0] for r in db.execute('SELECT name FROM units WHERE done=1')}
    total_files = db.execute('SELECT count(*) FROM files').fetchone()[0]

    for i, (uname, entry) in enumerate(units):
        if uname in done:
            continue
        if budget and time.time() - t0 > budget:
            meta_set(db, 'scan_complete', False)
            db.commit()
            print('BUDGET: scanned %d/%d top-level units; run again to continue' % (len(done), len(units)))
            return False
        u0 = time.time()
        # a crashed earlier attempt may have left half a unit behind
        if uname != '<root-files>':
            db.execute('DELETE FROM files WHERE rel=? OR (rel>=? AND rel<?)', (uname, uname + '/', uname + '0'))
            db.execute('DELETE FROM dirs WHERE rel=? OR (rel>=? AND rel<?)', (uname, uname + '/', uname + '0'))
        rows, drows, n = [], [], 0
        if uname == '<root-files>':
            for e in top:
                if e.is_file(follow_symlinks=False):
                    rows.append(_row(e, e.name))
        elif uname.lower() == QUARANTINE.lower() or _excluded(entry, excl):
            pass
        else:
            drows.append((uname, uname.lower()))
            stack = [(entry.path, uname)]
            while stack:
                path, rel = stack.pop()
                try:
                    it = list(os.scandir(path))
                except OSError as ex:
                    db.execute('INSERT INTO errors VALUES(?,?)', (rel, str(ex)[:200]))
                    continue
                for e in it:
                    r = rel + '/' + e.name
                    try:
                        if _is_real_dir(e):
                            if not _excluded(e, excl):
                                drows.append((r, r.lower()))
                                stack.append((e.path, r))
                        elif e.is_file(follow_symlinks=False):
                            rows.append(_row(e, r))
                    except OSError as ex:
                        db.execute('INSERT INTO errors VALUES(?,?)', (r, str(ex)[:200]))
                if len(rows) >= 20000:
                    _flush(db, rows, drows)
                    n += 20000
                    rows, drows = [], []
                    progress(out_dir, step='scan', unit=uname, unit_index=i, units=len(units),
                             files=total_files + n, secs=round(time.time() - t0))
        n += len(rows)
        _flush(db, rows, drows)
        total_files += n
        db.execute('INSERT OR REPLACE INTO units VALUES(?,?,?,?)', (uname, 1, n, time.time() - u0))
        db.commit()
        progress(out_dir, step='scan', unit=uname, unit_index=i + 1, units=len(units),
                 files=total_files, secs=round(time.time() - t0))
        print('  scanned %-40s %8d files  %5.1fs' % (uname[:40], n, time.time() - u0))
    meta_set(db, 'scan_complete', True)
    meta_set(db, 'scan_finished', time.time())
    db.commit()
    return True


def _is_real_dir(e):
    try:
        if e.is_symlink() or (hasattr(e, 'is_junction') and e.is_junction()):
            return False
        return e.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _excluded(e, excl):
    if e.name.lower() == QUARANTINE.lower():
        return True
    try:
        if os.path.exists(os.path.join(e.path, OUT_MARK)):
            return True
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(e.path)) in excl


def _row(e, rel):
    st = e.stat(follow_symlinks=False)
    birth = getattr(st, 'st_birthtime', None)
    if birth is None and os.name == 'nt':
        birth = st.st_ctime
    attrs = getattr(st, 'st_file_attributes', 0) or 0
    parent, name, ext = split_name(rel)
    return (rel, parent, name, ext, st.st_size, st.st_mtime, birth, st.st_atime,
            0 if attrs & CLOUD_ATTRS else 1)


def _flush(db, rows, drows):
    db.executemany('INSERT INTO files(rel,parent,name,ext,size,mtime,ctime,atime,ondisk) VALUES(?,?,?,?,?,?,?,?,?)', rows)
    db.executemany('INSERT OR IGNORE INTO dirs VALUES(?,?)', drows)


# ------------------------------------------------------------------ imports
def _ts(v):
    if v in (None, ''):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    s = str(v).strip().replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%m/%d/%Y %H:%M:%S'):
            try:
                return datetime.strptime(s[:19], fmt).timestamp()
            except ValueError:
                pass
    return None


def _add_dirs(db, rels):
    seen = set()
    for rel in rels:
        p = rel.rpartition('/')[0]
        while p and p not in seen:
            seen.add(p)
            p = p.rpartition('/')[0]
    db.executemany('INSERT OR IGNORE INTO dirs VALUES(?,?)', [(d, d.lower()) for d in seen])


def import_gdrive(db, path):
    meta_set(db, 'root', 'gdrive')
    meta_set(db, 'source', 'gdrive')
    meta_set(db, 'display_root', 'Google Drive/')
    meta_set(db, 'sep', '/')
    meta_set(db, 'is_system_drive', False)
    rows, rels, seen = [], [], set()
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            if o.get('id') in seen:
                continue   # the connector can repeat items across page boundaries
            seen.add(o.get('id'))
            if (o.get('mime') or '') == 'application/vnd.google-apps.folder':
                continue
            rel = (o.get('path') or o.get('title') or o.get('id')).strip('/')
            parent, name, ext = split_name(rel)
            if o.get('ext'):
                ext = o['ext'].lower()
            extra = {k: o.get(k) for k in ('mime', 'owned', 'trashed', 'url') if k in o}
            extra['viewed'] = _ts(o.get('viewed'))
            rows.append((rel, parent, name, ext, int(o.get('size') or 0), _ts(o.get('mtime')),
                         _ts(o.get('ctime')), None, 1, o.get('id'), json.dumps(extra, ensure_ascii=False)))
            rels.append(rel)
    db.executemany('INSERT INTO files(rel,parent,name,ext,size,mtime,ctime,atime,ondisk,xid,extra) '
                   'VALUES(?,?,?,?,?,?,?,?,?,?,?)', rows)
    _add_dirs(db, rels)
    meta_set(db, 'scan_complete', True)
    meta_set(db, 'scan_finished', time.time())
    db.commit()
    print('imported %d Google Drive files' % len(rows))


def import_listing(db, path, root, display_root):
    meta_set(db, 'root', root)
    meta_set(db, 'source', 'listing')
    meta_set(db, 'display_root', display_root or root)
    meta_set(db, 'sep', '\\' if (display_root or root)[1:2] == ':' else '/')
    rows, rels = [], []
    with open(path, encoding='utf-8-sig') as f:
        for line in f:
            p = line.rstrip('\r\n').split('\t')
            if len(p) < 3 or p[0] == 'rel':
                continue
            rel = p[0].replace('\\', '/').strip('/')
            parent, name, ext = split_name(rel)
            rows.append((rel, parent, name, ext, int(p[1] or 0), _ts(p[2]), _ts(p[3]) if len(p) > 3 else None, None, 1))
            rels.append(rel)
    names = {r.split('/')[0].lower() for r in rels}
    meta_set(db, 'is_system_drive', 'windows' in names and 'program files' in names)
    _flush(db, rows, [])
    _add_dirs(db, rels)
    meta_set(db, 'scan_complete', True)
    meta_set(db, 'scan_finished', time.time())
    db.commit()
    print('imported %d listed files' % len(rows))


# ------------------------------------------------------------------ duplicates
SKIP_DUP_PARTS = {'windows', 'program files', 'program files (x86)', 'programdata', 'appdata', '$recycle.bin',
                  'node_modules', 'site-packages', 'windows.old', QUARANTINE.lower(), 'steamapps'}


def _dup_eligible(rel):
    parts = rel.lower().split('/')[:-1]
    return not any(p in SKIP_DUP_PARTS or p.startswith('.') for p in parts)


def _original_key(rel, mtime):
    low = rel.lower()
    junk = any(k in low for k in ('/downloads/', 'temp', 'copy', 'نسخة', '(1)', '(2)', ' - copy'))
    return (junk, mtime or 0, len(rel), rel)


def dupes(db, budget_gb=30.0, budget_sec=None, min_size=1024 ** 2, use_hash=True, out_dir=None):
    db.execute('UPDATE files SET dup=NULL, dup_of=NULL, dupconf=NULL')
    source = meta_get(db, 'source')
    root = meta_get(db, 'root')
    gid = 0
    if source != 'fs' or not use_hash:
        # without file access: same name + same size = probable duplicate (unconfirmed)
        q = ('SELECT lower(name), size FROM files WHERE size>=? GROUP BY lower(name), size HAVING count(*)>1')
        for lname, size in db.execute(q, (min_size,)).fetchall():
            grp = [r for r in db.execute('SELECT id, rel, mtime FROM files WHERE lower(name)=? AND size=?', (lname, size))
                   if _dup_eligible(r[1])]
            if len(grp) < 2:
                continue
            gid += 1
            _mark_group(db, gid, grp, 0)
        db.commit()
        print('probable duplicate groups (name+size): %d' % gid)
        return
    t0, read = time.time(), 0
    budget = budget_gb * 1024 ** 3
    sizes = db.execute('SELECT size, count(*) c FROM files WHERE size>=? AND ondisk=1 GROUP BY size HAVING c>1 '
                       'ORDER BY size*(c-1) DESC', (min_size,)).fetchall()
    for size, _ in sizes:
        if read > budget or (budget_sec and time.time() - t0 > budget_sec):
            print('dupes: budget reached after %.1f GB' % (read / 1024 ** 3))
            break
        cands = [r for r in db.execute('SELECT id, rel, mtime FROM files WHERE size=? AND ondisk=1', (size,))
                 if _dup_eligible(r[1])]
        if len(cands) < 2:
            continue
        quick = {}
        for r in cands:
            h = _hash(os.path.join(root, *r[1].split('/')), size, quick=True)
            read += min(size, 2 * 65536)
            if h:
                quick.setdefault(h, []).append(r)
        for grp in quick.values():
            if len(grp) < 2:
                continue
            full = {}
            for r in grp:
                h = _hash(os.path.join(root, *r[1].split('/')), size, quick=False)
                read += size
                if h:
                    full.setdefault(h, []).append(r)
            for same in full.values():
                if len(same) > 1:
                    gid += 1
                    _mark_group(db, gid, same, 1)
        progress(out_dir, step='dupes', groups=gid, read_gb=round(read / 1024 ** 3, 2))
    db.commit()
    print('exact duplicate groups: %d (read %.1f GB)' % (gid, read / 1024 ** 3))


def _mark_group(db, gid, grp, conf):
    grp = sorted(grp, key=lambda r: _original_key(r[1], r[2]))
    orig = grp[0]
    db.execute('UPDATE files SET dup=?, dupconf=? WHERE id=?', (gid, conf, orig[0]))
    for r in grp[1:]:
        db.execute('UPDATE files SET dup=?, dup_of=?, dupconf=? WHERE id=?', (gid, orig[1], conf, r[0]))


def _hash(path, size, quick):
    try:
        h = hashlib.blake2b(digest_size=20)
        with open(path, 'rb') as f:
            if quick:
                h.update(f.read(65536))
                if size > 65536:
                    f.seek(max(size - 65536, 0))
                    h.update(f.read(65536))
            else:
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# ------------------------------------------------------------------ classify
class Ctx:
    def __init__(self, db):
        self.db = db
        self.now = meta_get(db, 'scan_started') or time.time()
        self.is_system_drive = bool(meta_get(db, 'is_system_drive'))
        self.source = meta_get(db, 'source')
        self.in_protected = False
        self.dirs_l = {r[0] for r in db.execute('SELECT rel_l FROM dirs')}
        exe = {r[0] for r in db.execute("SELECT DISTINCT parent FROM files WHERE ext='exe'")}
        dll = {r[0] for r in db.execute("SELECT DISTINCT parent FROM files WHERE ext='dll'")}
        self.app_dirs = exe & dll
        # a folder holding browser partial downloads is a browser download target
        self.download_dirs = {r[0] for r in db.execute(
            "SELECT DISTINCT parent FROM files WHERE ext IN ('crdownload','opdownload','part') OR name LIKE 'Unconfirmed %'")}
        self.installer_dirs = {r[0] for r in db.execute(
            "SELECT DISTINCT parent FROM files WHERE lower(name) IN (%s)" % ','.join("'%s'" % m for m in R.INSTALLER_MARKERS))}
        self.installer_dirs -= self.download_dirs
        self.installer_dirs.discard('')
        self._file_cache = {}

    def has_file(self, rel):
        if rel not in self._file_cache:
            self._file_cache[rel] = self.db.execute('SELECT 1 FROM files WHERE rel=? LIMIT 1', (rel,)).fetchone() is not None
        return self._file_cache[rel]

    def is_dir_ci(self, rel):
        return rel.lower() in self.dirs_l

    def dir_has_ext(self, rel, exts):
        q = 'SELECT 1 FROM files WHERE parent=? AND ext IN (%s) LIMIT 1' % ','.join('?' * len(exts))
        return self.db.execute(q, (rel,) + tuple(exts)).fetchone() is not None

    def is_download_dir(self, rel):
        return rel in self.download_dirs

    def is_installer_dir(self, rel):
        return rel in self.installer_dirs

    def is_app_dir(self, rel):
        return rel in self.app_dirs

    def has_project_marker(self, rel):
        p = rel + '/'
        return (self.is_dir_ci(p + '.git') or any(self.has_file(p + m) for m in
                ('package.json', 'Cargo.toml', 'pyproject.toml', 'requirements.txt', 'go.mod', 'pom.xml', 'build.gradle'))
                or self.dir_has_ext(rel, ('sln', 'csproj', 'vcxproj')))

    def project_cache(self, rel, rule, why):
        parent = rel.rpartition('/')[0]
        if parent:
            q = ('SELECT max(mtime) FROM files WHERE rel>=? AND rel<? AND NOT (rel>=? AND rel<?)')
            newest = self.db.execute(q, (parent + '/', parent + '0', rel + '/', rel + '0')).fetchone()[0]
        else:
            q = 'SELECT max(mtime) FROM files WHERE NOT (rel>=? AND rel<?)'
            newest = self.db.execute(q, (rel + '/', rel + '0')).fetchone()[0]
        if newest and self.now - newest < R.INACTIVE_PROJECT:
            return R.V('maybe', rule, why + ' — بس المشروع اتعدّل من قريب (%s)' % _d(newest), 'review')
        return R.V('delete', rule, why + (' — المشروع آخر تعديل %s' % _d(newest) if newest else ''), 'move')


def _d(ts):
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d') if ts else '—'


AGE_BINS = [(30, 'أقل من شهر'), (182, '1–6 شهور'), (365, '6–12 شهر'), (730, 'سنة–سنتين'),
            (1825, '2–5 سنين'), (10 ** 9, 'أكتر من 5 سنين')]


def classify(db, overrides=None, out_dir=None):
    ctx = Ctx(db)
    disp = Display(db)
    ovr = []
    if overrides and os.path.exists(overrides):
        with open(overrides, encoding='utf-8') as f:
            ovr = sorted(json.load(f), key=lambda o: -len(o.get('prefix', '')))
    memo = {'': (None, False, False, None, frozenset())}

    def dstate(rel):
        if rel in memo:
            return memo[rel]
        chain = []
        r = rel
        while r not in memo:
            chain.append(r)
            r = r.rpartition('/')[0]
        for d in reversed(chain):
            ps = memo[d.rpartition('/')[0]]
            if ps[1]:   # parent terminal -> inherit
                memo[d] = ps
                continue
            parts = d.lower().split('/')
            flags = ps[4] | frozenset(R.zone_flags(parts[-1], parts))
            ctx.in_protected = ps[2]
            hit = R.dir_rules(parts[-1], parts, d, ctx)
            if hit:
                v, term, prot = hit
                memo[d] = (v, term, prot or ps[2], d if term else None, flags)
            else:
                memo[d] = (ps[0], False, ps[2], None, flags)
        return memo[rel]

    tree1, tree2, fams, years, ages = {}, {}, {}, {}, {}
    updates, n = [], 0
    cur = db.execute('SELECT id, rel, parent, name, ext, size, mtime, extra, dup, dup_of, dupconf FROM files')
    for fid, rel, parent, name, ext, size, mtime, extra, dup, dup_of, dupconf in cur.fetchall():
        st = dstate(parent)
        lname = name.lower()
        fam = R.EXT_FAMILY.get(ext, 'other')
        item = None
        if st[1]:
            v = st[0]
            item = 'd:' + st[3]
        else:
            v = None
            if ctx.source == 'gdrive':
                v = R.gdrive_rules(json.loads(extra or '{}'), size, mtime, ctx)
            if not v and st[2]:
                v = R.V('delete', 'crash_dump', 'تقرير عطل (crash dump)', 'system') if ext in ('dmp', 'mdmp') else st[0]
            if not v:
                v = R.file_rules(lname, ext, fam, size, mtime, rel, st[4], ctx)
            if not v and st[0]:
                v = st[0]
            if not v:
                v = R.default_verdict(fam, size, ext)
            if dup_of and v[0] in ('keep', 'maybe', 'unknown') and not st[2] and not v[1].startswith('gdrive_'):
                if dupconf:
                    v = R.V('delete', 'duplicate', 'نسخة مطابقة 100%% من: %s' % disp(dup_of), 'move')
                else:
                    v = R.V('maybe', 'probable_duplicate', 'غالبًا مكرر (نفس الاسم والحجم) مع: %s' % disp(dup_of), 'review')
        if 'sync' in st[4] and v[0] in ('delete', 'maybe') and v[3] == 'move':
            v = (v[0], v[1], v[2] + ' ⚠️ فولدر متزامن: المسح هنا بيمسح من الكلاود كمان', v[3])
        for o in ovr:
            if rel == o['prefix'] or rel.startswith(o['prefix'].rstrip('/') + '/'):
                v = (o['bucket'], 'override', o.get('reason', 'قرارك'), o.get('action', 'move' if o['bucket'] == 'delete' else 'review'))
                item = None
                break
        if not item:
            if size >= R.BIG_FILE_ITEM or v[1] == 'duplicate' and size >= 10 * 1024 ** 2:
                item = 'f:' + rel
            else:
                grp = '/'.join(parent.split('/')[:3]) if parent else ''
                item = 'g:%s|%s|%s' % (grp, v[0], v[1])
        updates.append((v[0], v[1], v[2], v[3], item, fid))

        # aggregates
        p = rel.split('/')
        k1 = p[0] if len(p) > 1 else '(ملفات في الجذر)'
        t = tree1.setdefault(k1, [0, 0, None, None, {b: 0 for b in BUCKETS}])
        t[0] += size; t[1] += 1; t[4][v[0]] += size
        if mtime:
            t[2] = mtime if t[2] is None else max(t[2], mtime)
            t[3] = mtime if t[3] is None else min(t[3], mtime)
        if len(p) > 2:
            k2 = p[0] + '/' + p[1]
            t2 = tree2.setdefault(k2, [0, 0, None])
            t2[0] += size; t2[1] += 1
            if mtime:
                t2[2] = mtime if t2[2] is None else max(t2[2], mtime)
        f = fams.setdefault(fam, [0, 0]); f[0] += size; f[1] += 1
        if mtime:
            y = datetime.fromtimestamp(mtime).year
            yy = years.setdefault(y, [0, 0]); yy[0] += size; yy[1] += 1
            days = (ctx.now - mtime) / 86400
            for lim, lab in AGE_BINS:
                if days < lim:
                    a = ages.setdefault(lab, [0, 0]); a[0] += size; a[1] += 1
                    break
        n += 1
        if len(updates) >= 50000:
            db.executemany('UPDATE files SET bucket=?, rule=?, reason=?, action=?, item=? WHERE id=?', updates)
            updates = []
            progress(out_dir, step='classify', files=n)
    db.executemany('UPDATE files SET bucket=?, rule=?, reason=?, action=?, item=? WHERE id=?', updates)
    meta_set(db, 'agg', {'tree1': tree1, 'tree2': tree2, 'fams': fams, 'years': years, 'ages': ages})
    meta_set(db, 'classified_at', time.time())
    db.execute('DROP TABLE IF EXISTS items')
    db.execute('''CREATE TABLE items AS SELECT item, min(bucket) bucket, min(rule) rule, min(reason) reason,
                  min(action) action, sum(size) size, count(*) cnt, max(mtime) newest, min(mtime) oldest,
                  min(rel) one_rel, substr(group_concat(name, ' · '), 1, 160) sample
                  FROM files GROUP BY item''')
    db.commit()
    print('classified %d files' % n)


class Display:
    def __init__(self, db):
        self.root = meta_get(db, 'display_root') or ''
        self.sep = meta_get(db, 'sep') or '/'

    def __call__(self, rel):
        return self.root + rel.replace('/', self.sep) if rel else self.root


# ------------------------------------------------------------------ report
def build_data(db, title=None, per_bucket=400):
    disp = Display(db)
    agg = meta_get(db, 'agg') or {}
    now = meta_get(db, 'scan_started') or time.time()
    sep = disp.sep

    def item_row(r):
        item, bucket, rule, reason, action, size, cnt, newest, oldest, one_rel, sample = r
        kind, _, key = item.partition(':')
        sample_out = ''
        if kind == 'g' and cnt == 1:
            kind, label = 'f', disp(one_rel)
        elif kind == 'g':
            path, _, _ = key.partition('|')
            label = disp(path) if path else disp('')
            sample_out = sample
        else:
            label = disp(key)
        if kind == 'f':
            folder, _, name = label.rpartition(sep)
        elif kind == 'd':
            folder, _, name = label.rstrip(sep).rpartition(sep)
        else:
            folder, name = label, ''
        return {'id': item, 'kind': {'d': 'folder', 'f': 'file', 'g': 'group'}[kind], 'path': label,
                'name': name, 'folder': folder, 'sample': sample_out,
                'bucket': bucket, 'rule': rule, 'title': R.RULE_TITLES.get(rule, rule), 'reason': reason,
                'action': action, 'locked': R.locked(rule, action), 'win': R.WIN_ACTION.get(rule, ''),
                'size': size, 'count': cnt, 'newest': _d(newest), 'oldest': _d(oldest)}

    totals = {}
    for b, s_, c in db.execute('SELECT bucket, sum(size), count(*) FROM files GROUP BY bucket'):
        totals[b] = {'size': s_ or 0, 'count': c}
    items = {}
    for b in BUCKETS:
        rows = db.execute('SELECT item,bucket,rule,reason,action,size,cnt,newest,oldest,one_rel,sample FROM items '
                          'WHERE bucket=? ORDER BY size DESC LIMIT ?', (b, per_bucket)).fetchall()
        items[b] = [item_row(r) for r in rows]
        totals.setdefault(b, {'size': 0, 'count': 0})
        totals[b]['items'] = db.execute('SELECT count(*) FROM items WHERE bucket=?', (b,)).fetchone()[0]
    rules_sum = [{'bucket': b, 'rule': r, 'title': R.RULE_TITLES.get(r, r), 'reason': why, 'size': s_, 'count': c}
                 for b, r, why, s_, c in db.execute(
                     'SELECT bucket, rule, min(reason), sum(size), count(*) FROM files GROUP BY bucket, rule ORDER BY sum(size) DESC')]
    largest = [{'path': disp(r[0]), 'size': r[1], 'mtime': _d(r[2]), 'bucket': r[3], 'reason': r[4], 'id': r[5]}
               for r in db.execute('SELECT rel,size,mtime,bucket,reason,item FROM files ORDER BY size DESC LIMIT 100')]
    tot = db.execute('SELECT count(*), sum(size), min(mtime), max(mtime) FROM files').fetchone()
    return {
        'title': title or meta_get(db, 'title') or ('تحليل ' + disp('')),
        'root': disp(''), 'source': meta_get(db, 'source'),
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'scan_date': _d(now), 'disk': meta_get(db, 'disk'),
        'files': tot[0], 'bytes': tot[1] or 0, 'oldest': _d(tot[2]), 'newest': _d(tot[3]),
        'dirs': db.execute('SELECT count(*) FROM dirs').fetchone()[0],
        'errors': db.execute('SELECT count(*) FROM errors').fetchone()[0],
        'complete': bool(meta_get(db, 'scan_complete')),
        'dup_groups': db.execute('SELECT count(DISTINCT dup) FROM files WHERE dup IS NOT NULL').fetchone()[0],
        'totals': totals,
        'tree1': sorted([{'name': k, 'path': disp(k) if not k.startswith('(') else k, 'size': v[0], 'count': v[1],
                          'newest': _d(v[2]), 'oldest': _d(v[3]), 'buckets': v[4]} for k, v in agg.get('tree1', {}).items()],
                        key=lambda x: -x['size']),
        'tree2': sorted([{'name': k, 'path': disp(k), 'size': v[0], 'count': v[1], 'newest': _d(v[2])}
                         for k, v in agg.get('tree2', {}).items()], key=lambda x: -x['size'])[:300],
        'fams': sorted([{'fam': k, 'label': R.FAMILY_AR.get(k, k), 'size': v[0], 'count': v[1]}
                        for k, v in agg.get('fams', {}).items()], key=lambda x: -x['size']),
        'years': sorted([{'year': int(k), 'size': v[0], 'count': v[1]} for k, v in agg.get('years', {}).items()],
                        key=lambda x: x['year']),
        'ages': [{'label': lab, 'size': agg.get('ages', {}).get(lab, [0, 0])[0],
                  'count': agg.get('ages', {}).get(lab, [0, 0])[1]} for _, lab in AGE_BINS],
        'rules': rules_sum, 'items': items, 'largest': largest, 'live': None,
    }


def render_html(data):
    with open(os.path.join(HERE, 'report_template.html'), encoding='utf-8') as f:
        html = f.read()
    payload = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    return html.replace('__DATA_JSON__', payload).replace('__TITLE__', data['title'])


def report(db, out_dir, title=None, per_bucket=400):
    os.makedirs(out_dir, exist_ok=True)
    if title:
        meta_set(db, 'title', title); db.commit()
    data = build_data(db, title, per_bucket)
    items = data['items']
    with open(os.path.join(out_dir, 'report.html'), 'w', encoding='utf-8') as f:
        f.write(render_html(data))
    with open(os.path.join(out_dir, 'items.csv'), 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['id', 'bucket', 'category', 'size_mb', 'count', 'path', 'reason', 'action', 'newest', 'oldest', 'sample'])
        full = build_data(db, title, 10 ** 9)['items']
        for b in BUCKETS:
            for x in full[b]:
                w.writerow([x['id'], x['bucket'], x['title'], round(x['size'] / 1024 ** 2, 1), x['count'], x['path'],
                            x['reason'], x['action'], x['newest'], x['oldest'], x['sample']])
    summary = {k: data[k] for k in ('title', 'root', 'files', 'bytes', 'dirs', 'errors', 'complete', 'disk',
                                    'oldest', 'newest', 'dup_groups', 'totals', 'ages')}
    summary['top_folders'] = data['tree1'][:12]
    summary['fams'] = data['fams'][:10]
    summary['top_items'] = {b: items[b][:12] for b in BUCKETS}
    summary['top_rules'] = data['rules'][:25]
    with open(os.path.join(out_dir, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    _write_launcher(db, out_dir)
    print('report: %s' % os.path.join(out_dir, 'report.html'))


def _write_launcher(db, out_dir):
    """open-panel.cmd / open-panel.sh — double-click to reopen the live panel later, no agent needed."""
    if meta_get(db, 'source') != 'fs':
        return
    dbp = os.path.abspath(os.path.join(out_dir, 'inventory.sqlite'))
    if not os.path.exists(dbp):
        return
    script = os.path.abspath(__file__)
    if os.name == 'nt':
        with open(os.path.join(out_dir, 'open-panel.cmd'), 'w', encoding='utf-8') as f:
            f.write('@echo off\r\nchcp 65001 >nul\r\n"%s" -X utf8 "%s" serve --db "%s" --open\r\npause\r\n'
                    % (sys.executable, script, dbp))
    else:
        pth = os.path.join(out_dir, 'open-panel.sh')
        with open(pth, 'w', encoding='utf-8') as f:
            f.write('#!/bin/sh\nexec "%s" "%s" serve --db "%s" --open\n' % (sys.executable, script, dbp))
        os.chmod(pth, 0o755)


# ------------------------------------------------------------------ plan / apply / undo
def plan(db, out, item_ids=None, buckets=None, include_review=False):
    root = meta_get(db, 'root')
    disp = Display(db)
    q = 'SELECT item, bucket, action, size, cnt, reason FROM items'
    sel = []
    for item, bucket, action, size, cnt, reason in db.execute(q):
        if item_ids is not None and item not in item_ids:
            continue
        if buckets and bucket not in buckets:
            continue
        sel.append((item, bucket, action, size, cnt, reason))
    targets, manual = [], []
    for item, bucket, action, size, cnt, reason in sel:
        if action not in ('move', 'review') or (action == 'review' and not include_review and item_ids is None):
            manual.append({'item': item, 'path': disp(item.partition(':')[2].partition('|')[0]),
                           'action': action, 'size': size, 'reason': reason})
            continue
        kind, _, key = item.partition(':')
        if kind == 'd':
            targets.append({'rel': key, 'type': 'dir', 'size': size, 'count': cnt, 'item': item})
        else:
            for rel, sz, xid in db.execute('SELECT rel, size, xid FROM files WHERE item=?', (item,)):
                targets.append({'rel': rel, 'type': 'file', 'size': sz, 'item': item, 'xid': xid})
    p = {'root': root, 'source': meta_get(db, 'source'), 'display_root': disp(''), 'created': _d(time.time()),
         'total_size': sum(t['size'] for t in targets), 'targets': targets, 'manual': manual}
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(p, f, ensure_ascii=False, indent=1)
    print('plan: %d targets, %.2f GB to quarantine; %d items need a manual/system action'
          % (len(targets), p['total_size'] / 1024 ** 3, len(manual)))


def apply(plan_path, confirm=False):
    with open(plan_path, encoding='utf-8') as f:
        p = json.load(f)
    if p.get('source') == 'gdrive':
        sys.exit('Google Drive plans are executed with the connector (trash_file per xid), not here.')
    root = p['root']
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    qdir = os.path.join(root, QUARANTINE, stamp)
    if not confirm:
        print('DRY RUN — would move %d targets (%.2f GB) into %s' % (len(p['targets']), p['total_size'] / 1024 ** 3, qdir))
        for t in p['targets'][:30]:
            print('  ', t['type'], t['rel'], round(t['size'] / 1024 ** 2, 1), 'MB')
        print('re-run with --confirm to move.')
        return
    os.makedirs(qdir, exist_ok=True)
    man_path = os.path.join(qdir, 'manifest.csv')
    moved = skipped = 0
    moved_bytes = 0
    with open(man_path, 'w', encoding='utf-8-sig', newline='') as mf:
        w = csv.writer(mf)
        w.writerow(['src', 'dst', 'size', 'status'])
        for t in p['targets']:
            src = os.path.join(root, *t['rel'].split('/'))
            dst = os.path.join(qdir, *t['rel'].split('/'))
            if not os.path.lexists(src):
                w.writerow([src, dst, t['size'], 'missing']); skipped += 1
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
                w.writerow([src, dst, t['size'], 'moved']); moved += 1; moved_bytes += t['size']
            except Exception as e:  # file in use, permission, path too long...
                w.writerow([src, dst, t['size'], 'skipped: %s' % str(e)[:150]]); skipped += 1
    print('moved %d (%.2f GB), skipped %d. Quarantine: %s\nmanifest: %s'
          % (moved, moved_bytes / 1024 ** 3, skipped, qdir, man_path))


def undo(manifest, confirm=False):
    rows = list(csv.DictReader(open(manifest, encoding='utf-8-sig')))
    todo = [r for r in rows if r['status'] == 'moved']
    if not confirm:
        print('DRY RUN — would restore %d entries. Re-run with --confirm.' % len(todo))
        return
    ok = bad = 0
    for r in todo:
        try:
            if os.path.lexists(r['src']):
                raise FileExistsError('destination exists')
            os.makedirs(os.path.dirname(r['src']), exist_ok=True)
            shutil.move(r['dst'], r['src']); ok += 1
        except Exception as e:
            print('  could not restore %s: %s' % (r['src'], e)); bad += 1
    print('restored %d, failed %d' % (ok, bad))


# ------------------------------------------------------------------ cli
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('analyze', help='scan + duplicates + classify + report (resumable)')
    a.add_argument('root'); a.add_argument('--out', help='output folder (default: ~/drive-analyzer-reports/<drive>-<date>)')
    a.add_argument('--serve', action='store_true', help='open the live action panel in the browser when done')
    a.add_argument('--display-root'); a.add_argument('--budget', type=float)
    a.add_argument('--no-hash', action='store_true'); a.add_argument('--hash-gb', type=float, default=30)
    a.add_argument('--overrides'); a.add_argument('--title'); a.add_argument('--exclude', nargs='*', default=[])

    s = sub.add_parser('scan', help='inventory only'); s.add_argument('root'); s.add_argument('--db', required=True)
    s.add_argument('--display-root'); s.add_argument('--budget', type=float); s.add_argument('--exclude', nargs='*', default=[])
    d = sub.add_parser('dupes'); d.add_argument('--db', required=True); d.add_argument('--hash-gb', type=float, default=30)
    d.add_argument('--no-hash', action='store_true')
    c = sub.add_parser('classify'); c.add_argument('--db', required=True); c.add_argument('--overrides')
    r = sub.add_parser('report'); r.add_argument('--db', required=True); r.add_argument('--out', required=True)
    r.add_argument('--title')
    g = sub.add_parser('import-gdrive'); g.add_argument('file'); g.add_argument('--db', required=True)
    li = sub.add_parser('import-listing'); li.add_argument('file'); li.add_argument('--db', required=True)
    li.add_argument('--root', required=True); li.add_argument('--display-root')
    p = sub.add_parser('plan'); p.add_argument('--db', required=True); p.add_argument('--out', required=True)
    p.add_argument('--items'); p.add_argument('--buckets'); p.add_argument('--include-review', action='store_true')
    sv = sub.add_parser('serve', help='live action panel on 127.0.0.1 (recycle / delete / open)'); sv.add_argument('--db', required=True); sv.add_argument('--port', type=int, default=0)
    sv.add_argument('--open', action='store_true'); sv.add_argument('--idle-hours', type=float, default=3)
    x = sub.add_parser('apply'); x.add_argument('plan'); x.add_argument('--confirm', action='store_true')
    u = sub.add_parser('undo'); u.add_argument('manifest'); u.add_argument('--confirm', action='store_true')
    args = ap.parse_args()

    if args.cmd == 'analyze':
        if not args.out:
            label = os.path.splitdrive(os.path.abspath(args.root))[0].rstrip(':') or os.path.basename(os.path.abspath(args.root).rstrip('/\\')) or 'root'
            args.out = os.path.join(os.path.expanduser('~'), 'drive-analyzer-reports', '%s-%s' % (label, datetime.now().strftime('%Y%m%d')))
            print('output: ' + args.out)
        os.makedirs(args.out, exist_ok=True)
        open(os.path.join(args.out, OUT_MARK), 'w').close()
        db = connect(os.path.join(args.out, 'inventory.sqlite'))
        t0 = time.time()
        if not meta_get(db, 'scan_complete'):
            if not scan(db, args.root, args.display_root, args.budget, args.exclude + [args.out], args.out):
                progress(args.out, step='scan', state='incomplete')
                sys.exit(3)
        if meta_get(db, 'dupes_done') is None:
            left = (args.budget - (time.time() - t0)) if args.budget else None
            dupes(db, args.hash_gb, budget_sec=left, use_hash=not args.no_hash, out_dir=args.out)
            meta_set(db, 'dupes_done', True); db.commit()
        classify(db, args.overrides, args.out)
        report(db, args.out, args.title)
        progress(args.out, step='done', state='complete')
        open(os.path.join(args.out, 'done.flag'), 'w').close()
        if args.serve:
            import panel
            panel.serve(os.path.join(args.out, 'inventory.sqlite'), 0, True)
    elif args.cmd == 'scan':
        db = connect(args.db)
        sys.exit(0 if scan(db, args.root, args.display_root, args.budget, args.exclude) else 3)
    elif args.cmd == 'dupes':
        dupes(connect(args.db), args.hash_gb, use_hash=not args.no_hash)
    elif args.cmd == 'classify':
        classify(connect(args.db), args.overrides)
    elif args.cmd == 'report':
        report(connect(args.db), args.out, args.title)
    elif args.cmd == 'import-gdrive':
        import_gdrive(connect(args.db), args.file)
    elif args.cmd == 'import-listing':
        import_listing(connect(args.db), args.file, args.root, args.display_root)
    elif args.cmd == 'plan':
        ids = None
        if args.items:
            ids = {l.strip() for l in open(args.items, encoding='utf-8') if l.strip()}
        bks = set(args.buckets.split(',')) if args.buckets else None
        plan(connect(args.db), args.out, ids, bks, args.include_review)
    elif args.cmd == 'serve':
        import panel
        panel.serve(args.db, args.port, args.open, args.idle_hours)
    elif args.cmd == 'apply':
        apply(args.plan, args.confirm)
    elif args.cmd == 'undo':
        undo(args.manifest, args.confirm)


if __name__ == '__main__':
    main()
