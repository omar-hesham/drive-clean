"""
drive-analyzer / panel.py — the live action panel.

    python drive_analyzer.py serve --db <out>/inventory.sqlite [--open] [--port N]

Serves the report on http://127.0.0.1:<port>/?t=<token> with working buttons:
  recycle   → Windows Recycle Bin (refused when the drive has no bin, the bin is set to
              "delete immediately", or the item is bigger than the bin, because Windows would then
              silently delete it for good)
  delete    → permanent delete (the page asks for explicit confirmation)
  quarantine→ move into <root>/_to_delete/<stamp>/ (reversible; for drives without a Recycle Bin)
  open      → show the item in Explorer / the file manager
  win       → open the right Windows tool for locked system items (empty bin, Disk Cleanup, Apps)

Safety: binds to 127.0.0.1 only, every API call needs the random token, Host header is checked
(DNS-rebinding), only items from the inventory can be acted on (never an arbitrary path),
locked system rules are refused server-side, and every action is logged in the inventory DB.
"""
import json, os, secrets, shutil, sqlite3, stat, subprocess, sys, threading, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import drive_analyzer as DA
import rules as R

IS_WIN = os.name == 'nt'


# ------------------------------------------------------------------ helpers
def _lp(p):
    """Long-path form for Windows file APIs."""
    if IS_WIN:
        p = os.path.abspath(p)
        if not p.startswith('\\\\?\\'):
            return '\\\\?\\' + p
    return p


def _native(root, rel):
    return os.path.join(root, *rel.split('/')) if rel else root


def _inside(root, path):
    r = os.path.normcase(os.path.realpath(root)).rstrip('\\/')
    p = os.path.normcase(os.path.realpath(path))
    return p != r and p.startswith(r + os.sep)


def _exists(p):
    return os.path.lexists(_lp(p))


def _db(path):
    db = sqlite3.connect(path, timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS actions(item TEXT, op TEXT, at REAL, ok INTEGER, bytes INTEGER, msg TEXT)')
    return db


def _gb(b):
    return '%.1f GB' % (b / 1024 ** 3) if b >= 1024 ** 3 else '%.0f MB' % (b / 1024 ** 2)


# ------------------------------------------------------------------ Recycle Bin (Windows)
def recycle_info(root):
    info = {'supported': False, 'cap': 0, 'why': ''}
    if not IS_WIN:
        info['why'] = 'سلة المحذوفات متاحة على ويندوز بس — استخدم النقل لـ _to_delete'
        return info
    import ctypes
    from ctypes import wintypes
    drive = os.path.splitdrive(os.path.abspath(root))[0] + '\\'
    k32 = ctypes.windll.kernel32
    dtype = k32.GetDriveTypeW(drive)
    if dtype != 3:   # DRIVE_FIXED
        info['why'] = 'الدرايف ده (USB/شبكة) مالوش سلة محذوفات — أي مسح هيبقى نهائي؛ استخدم النقل لـ _to_delete'
        return info
    total = shutil.disk_usage(root).total
    cap, nuke = int(total * 0.05), 0
    try:
        buf = ctypes.create_unicode_buffer(64)
        if k32.GetVolumeNameForVolumeMountPointW(drive, buf, 64):
            guid = buf.value[buf.value.index('{'):buf.value.index('}') + 1]
            import winreg
            key = r'Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\%s' % guid
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                try:
                    cap = int(winreg.QueryValueEx(k, 'MaxCapacity')[0]) * 1024 * 1024
                except OSError:
                    pass
                try:
                    nuke = int(winreg.QueryValueEx(k, 'NukeOnDelete')[0])
                except OSError:
                    pass
    except Exception:
        pass
    if nuke:
        info['why'] = 'سلة المحذوفات للدرايف ده متظبطة "امسح على طول" — المسح هيبقى نهائي'
        return info
    info.update(supported=True, cap=cap)
    return info


def _shfileop_recycle(paths):
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT), ('pFrom', wintypes.LPCWSTR),
                    ('pTo', wintypes.LPCWSTR), ('fFlags', ctypes.c_ushort), ('fAnyOperationsAborted', wintypes.BOOL),
                    ('hNameMappings', ctypes.c_void_p), ('lpszProgressTitle', wintypes.LPCWSTR)]
    FO_DELETE = 3
    flags = 0x40 | 0x10 | 0x4 | 0x400 | 0x200   # ALLOWUNDO|NOCONFIRMATION|SILENT|NOERRORUI|NOCONFIRMMKDIR
    buf = ctypes.create_unicode_buffer('\0'.join(os.path.abspath(p) for p in paths) + '\0')
    op = SHFILEOPSTRUCTW(None, FO_DELETE, ctypes.cast(buf, wintypes.LPCWSTR), None, flags, False, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))


# ------------------------------------------------------------------ the operations
class Panel:
    def __init__(self, db_path):
        self.db_path = os.path.abspath(db_path)
        db = _db(self.db_path)
        self.root = DA.meta_get(db, 'root')
        self.source = DA.meta_get(db, 'source')
        db.close()
        if self.source != 'fs' or not os.path.isdir(self.root):
            sys.exit('the live panel needs a filesystem inventory whose root is reachable from here: %s' % self.root)
        self.token = secrets.token_urlsafe(24)
        self.last = time.time()
        self.lock = threading.Lock()
        self.recycle = recycle_info(self.root)

    # -- targets of one item, with every safety check
    def targets(self, db, item):
        row = db.execute('SELECT bucket, rule, action, size FROM items WHERE item=?', (item,)).fetchone()
        if not row:
            raise ValueError('عنصر مش موجود في التحليل')
        bucket, rule, action, size = row
        if R.locked(rule, action):
            raise ValueError('ده عنصر نظام مقفول — استخدم زرار أداة ويندوز بتاعته')
        kind, _, key = item.partition(':')
        if kind == 'd':
            out = [(_native(self.root, key), size, True)]
        else:
            out = [(_native(self.root, rel), sz, False) for rel, sz in
                   db.execute('SELECT rel, size FROM files WHERE item=?', (item,))]
        for p, _, _ in out:
            if not _inside(self.root, p):
                raise ValueError('مسار برة الدرايف: %s' % p)
        return out

    def act(self, op, items):
        results, db = [], _db(self.db_path)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        for item in items:
            try:
                tg = self.targets(db, item)
                present = [t for t in tg if _exists(t[0])]
                if not present:
                    res = {'ok': True, 'bytes': 0, 'msg': 'مش موجود أصلًا (اتشال قبل كده)'}
                elif op == 'recycle':
                    res = self._recycle(present)
                elif op == 'delete':
                    res = self._delete(present)
                elif op == 'quarantine':
                    res = self._quarantine(present, stamp)
                else:
                    raise ValueError('عملية مش معروفة')
            except Exception as e:
                res = {'ok': False, 'bytes': 0, 'msg': str(e)[:300]}
            res['item'] = item
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?)',
                       (item, op, time.time(), 1 if res['ok'] else 0, res['bytes'], res['msg']))
            db.commit()
            results.append(res)
        db.close()
        return results

    def _finish(self, present, label):
        gone = [t for t in present if not _exists(t[0])]
        left = [t for t in present if _exists(t[0])]
        b = sum(t[1] for t in gone)
        if not left:
            return {'ok': True, 'bytes': b, 'msg': label}
        return {'ok': False, 'bytes': b, 'msg': '%s جزئيًا — فاضل %d (غالبًا مفتوح في برنامج أو مسار طويل): %s'
                % (label, len(left), os.path.basename(left[0][0]))}

    def _recycle(self, present):
        if not self.recycle['supported']:
            raise ValueError(self.recycle['why'])
        cap = self.recycle['cap']
        big = [t for t in present if cap and t[1] > cap]
        if big:
            raise ValueError('أكبر من سعة سلة المحذوفات (%s) — ويندوز كان هيمسحه نهائي من غير ما يقول. '
                             'استخدم "مسح نهائي" أو "_to_delete"' % _gb(cap))
        paths = [t[0] for t in present]
        for i in range(0, len(paths), 200):
            _shfileop_recycle(paths[i:i + 200])
        return self._finish(present, 'اتنقل لسلة المحذوفات')

    def _delete(self, present):
        def onerr(func, path, exc):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass
        for p, _, is_dir in present:
            try:
                if is_dir and not os.path.islink(p):
                    shutil.rmtree(_lp(p), onerror=onerr)
                else:
                    try:
                        os.remove(_lp(p))
                    except PermissionError:
                        os.chmod(_lp(p), stat.S_IWRITE)
                        os.remove(_lp(p))
            except Exception:
                pass
        return self._finish(present, 'اتمسح نهائي')

    def _quarantine(self, present, stamp):
        qdir = os.path.join(self.root, DA.QUARANTINE, stamp)
        man = os.path.join(qdir, 'manifest.csv')
        os.makedirs(qdir, exist_ok=True)
        new = not os.path.exists(man)
        import csv
        with open(man, 'a', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            if new:
                w.writerow(['src', 'dst', 'size', 'status'])
            for p, size, _ in present:
                rel = os.path.relpath(p, self.root)
                dst = os.path.join(qdir, rel)
                try:
                    os.makedirs(os.path.dirname(_lp(dst)), exist_ok=True)
                    shutil.move(_lp(p), _lp(dst))
                    w.writerow([p, dst, size, 'moved'])
                except Exception as e:
                    w.writerow([p, dst, size, 'skipped: %s' % str(e)[:150]])
        return self._finish(present, 'اتنقل لـ %s' % os.path.join(DA.QUARANTINE, stamp))

    def open_item(self, item):
        db = _db(self.db_path)
        kind, _, key = item.partition(':')
        if kind == 'g':
            rel = db.execute('SELECT min(rel) FROM files WHERE item=?', (item,)).fetchone()[0] or ''
            cnt = db.execute('SELECT count(*) FROM files WHERE item=?', (item,)).fetchone()[0]
            path, select = (_native(self.root, rel), True) if cnt == 1 else (_native(self.root, rel.rpartition('/')[0]), False)
        elif kind == 'f':
            path, select = _native(self.root, key), True
        else:
            path, select = _native(self.root, key), False
        db.close()
        if not _exists(path):
            raise ValueError('المكان ده مابقاش موجود')
        if IS_WIN:
            subprocess.Popen(['explorer', '/select,', path] if select else ['explorer', path])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', '-R', path] if select else ['open', path])
        else:
            subprocess.Popen(['xdg-open', os.path.dirname(path) if select else path])
        return {'ok': True, 'msg': 'اتفتح'}

    def win_action(self, kind):
        if not IS_WIN:
            raise ValueError('متاح على ويندوز بس')
        drive = os.path.splitdrive(os.path.abspath(self.root))[0]
        if kind == 'empty_bin':
            import ctypes
            r = ctypes.windll.shell32.SHEmptyRecycleBinW(None, drive + '\\', 0x1 | 0x2 | 0x4)
            ok = r in (0, -2147418113)  # S_OK or E_UNEXPECTED (already empty)
            return {'ok': ok, 'msg': 'سلة المحذوفات اتفضّت' if ok else 'ماقدرتش أفضّي السلة (كود %s)' % r}
        if kind == 'cleanmgr':
            subprocess.Popen(['cleanmgr', '/d', drive])
            return {'ok': True, 'msg': 'اتفتح Disk Cleanup — دوس "Clean up system files"'}
        if kind == 'apps':
            os.startfile('ms-settings:appsfeatures')
            return {'ok': True, 'msg': 'اتفتحت شاشة Installed apps'}
        if kind == 'power':
            return {'ok': True, 'msg': 'لإلغاء الـ Hibernate: افتح PowerShell كـ Admin واكتب powercfg -h off'}
        raise ValueError('أداة مش معروفة')

    def state(self):
        db = _db(self.db_path)
        done = {}
        for item, op, at, ok, b, msg in db.execute('SELECT item, op, at, ok, bytes, msg FROM actions ORDER BY at'):
            done[item] = {'op': op, 'ok': bool(ok), 'bytes': b, 'msg': msg}
        db.close()
        du = shutil.disk_usage(self.root)
        return {'done': done, 'disk': {'total': du.total, 'used': du.used, 'free': du.free},
                'recycle': self.recycle, 'os': 'windows' if IS_WIN else sys.platform}

    def page(self):
        db = _db(self.db_path)
        data = DA.build_data(db, per_bucket=5000)
        db.close()
        data['live'] = {'token': self.token, **self.state()}
        return DA.render_html(data)


# ------------------------------------------------------------------ http
def serve(db_path, port=0, open_browser=False, idle_hours=3.0):
    P = Panel(db_path)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _host_ok(self):
            host = (self.headers.get('Host') or '').split(':')[0]
            return host in ('127.0.0.1', 'localhost')

        def _send(self, code, body, ctype='application/json; charset=utf-8'):
            data = body if isinstance(body, bytes) else body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            P.last = time.time()
            u = urlparse(self.path)
            if not self._host_ok():
                return self._send(403, '{"error":"host"}')
            if u.path == '/' and parse_qs(u.query).get('t', [''])[0] == P.token:
                return self._send(200, P.page(), 'text/html; charset=utf-8')
            if u.path == '/api/state' and self.headers.get('X-Token') == P.token:
                return self._send(200, json.dumps(P.state(), ensure_ascii=False))
            return self._send(403, '{"error":"token"}')

        def do_POST(self):
            P.last = time.time()
            if not self._host_ok() or self.headers.get('X-Token') != P.token:
                return self._send(403, '{"error":"token"}')
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n) or b'{}')
                path = urlparse(self.path).path
                if path == '/api/act':
                    with P.lock:
                        out = {'results': P.act(body['op'], list(body['items'])[:5000])}
                elif path == '/api/open':
                    out = P.open_item(body['item'])
                elif path == '/api/win':
                    out = P.win_action(body['kind'])
                elif path == '/api/shutdown':
                    threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0)), daemon=True).start()
                    out = {'ok': True, 'msg': 'اللوحة اتقفلت'}
                else:
                    return self._send(404, '{"error":"path"}')
                out['state'] = P.state() if path == '/api/act' or path == '/api/win' else None
                self._send(200, json.dumps(out, ensure_ascii=False))
            except Exception as e:
                self._send(400, json.dumps({'ok': False, 'msg': str(e)[:300]}, ensure_ascii=False))

    srv = ThreadingHTTPServer(('127.0.0.1', port), H)
    url = 'http://127.0.0.1:%d/?t=%s' % (srv.server_address[1], P.token)
    out_dir = os.path.dirname(P.db_path)
    with open(os.path.join(out_dir, 'panel_url.txt'), 'w', encoding='utf-8') as f:
        f.write(url)
    print('PANEL ' + url, flush=True)

    def idle():
        while True:
            time.sleep(60)
            if time.time() - P.last > idle_hours * 3600:
                os._exit(0)
    threading.Thread(target=idle, daemon=True).start()
    if open_browser:
        try:
            os.startfile(url) if IS_WIN else __import__('webbrowser').open(url)
        except Exception:
            pass
    srv.serve_forever()
