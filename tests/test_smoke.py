"""End-to-end smoke test: build a fake Windows-like drive, analyze it, check the classification,
exercise plan/apply/undo and the live panel API.  Run:  python -m unittest discover tests"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, unittest, urllib.request, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), 'scripts')
DA = os.path.join(SCRIPTS, 'drive_analyzer.py')
OLD = time.time() - 400 * 86400


def mk(root, rel, size=1000, mtime=None):
    p = os.path.join(root, *rel.split('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as f:
        f.write(os.urandom(size))
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


class Smoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        r = cls.root = os.path.join(cls.tmp, 'C')
        for rel in ('Windows/System32/k.dll', 'Windows/Temp/x.tmp', 'Program Files/App/a.exe', 'Program Files/App/a.dll'):
            mk(r, rel)
        mk(r, '$Recycle.Bin/S-1/a.bin')
        u = 'Users/me'
        mk(r, u + '/AppData/Local/Temp/t.log', mtime=OLD)
        mk(r, u + '/Downloads/old-setup.exe', 5000, OLD)
        mk(r, u + '/Downloads/movie.mkv.crdownload')
        mk(r, u + '/Documents/cv.docx', mtime=OLD)
        mk(r, u + '/Videos/Some.Movie.2024.1080p.WEB-DL.x264.mkv')
        mk(r, u + '/dev/web/package.json', 50, OLD)
        mk(r, u + '/dev/web/node_modules/react/index.js', 3000, OLD)
        big = mk(r, u + '/Pictures/a.jpg', 1_200_000)
        shutil.copy(big, os.path.join(r, 'Users', 'me', 'copy of a.jpg'))
        cls.out = os.path.join(cls.tmp, 'out')
        subprocess.run([sys.executable, DA, 'analyze', r, '--out', cls.out, '--display-root', 'C:\\'], check=True,
                       capture_output=True)
        cls.db = sqlite3.connect(os.path.join(cls.out, 'inventory.sqlite'))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def verdict(self, rel):
        return self.db.execute('SELECT bucket, rule FROM files WHERE rel=?', (rel,)).fetchone()

    def test_classification(self):
        u = 'Users/me/'
        self.assertEqual(self.verdict('Windows/System32/k.dll')[0], 'keep')
        self.assertEqual(self.verdict(u + 'AppData/Local/Temp/t.log'), ('delete', 'user_temp'))
        self.assertEqual(self.verdict(u + 'Downloads/old-setup.exe'), ('delete', 'old_installer'))
        self.assertEqual(self.verdict(u + 'Downloads/movie.mkv.crdownload'), ('delete', 'partial_download'))
        self.assertEqual(self.verdict(u + 'Documents/cv.docx')[0], 'keep')
        self.assertEqual(self.verdict(u + 'Videos/Some.Movie.2024.1080p.WEB-DL.x264.mkv'), ('maybe', 'downloaded_movie'))
        self.assertEqual(self.verdict(u + 'dev/web/node_modules/react/index.js'), ('delete', 'node_modules'))
        self.assertEqual(self.verdict(u + 'copy of a.jpg'), ('delete', 'duplicate'))
        self.assertEqual(self.verdict(u + 'Pictures/a.jpg')[0], 'keep')
        for f in ('report.html', 'summary.json', 'items.csv', 'done.flag'):
            self.assertTrue(os.path.exists(os.path.join(self.out, f)), f)

    def test_plan_apply_undo(self):
        plan = os.path.join(self.tmp, 'plan.json')
        ids = os.path.join(self.tmp, 'ids.txt')
        with open(ids, 'w') as f:
            f.write('d:Users/me/dev/web/node_modules\n')
        subprocess.run([sys.executable, DA, 'plan', '--db', os.path.join(self.out, 'inventory.sqlite'),
                        '--out', plan, '--items', ids], check=True, capture_output=True)
        nm = os.path.join(self.root, 'Users', 'me', 'dev', 'web', 'node_modules')
        subprocess.run([sys.executable, DA, 'apply', plan, '--confirm'], check=True, capture_output=True)
        self.assertFalse(os.path.exists(nm))
        q = os.path.join(self.root, '_to_delete')
        man = [os.path.join(q, d, 'manifest.csv') for d in os.listdir(q)][0]
        subprocess.run([sys.executable, DA, 'undo', man, '--confirm'], check=True, capture_output=True)
        self.assertTrue(os.path.exists(nm))

    def test_panel_api(self):
        p = subprocess.Popen([sys.executable, DA, 'serve', '--db', os.path.join(self.out, 'inventory.sqlite')],
                             stdout=subprocess.PIPE, text=True)
        try:
            url = p.stdout.readline().split()[-1]
            base, tok = url.split('/?t=')

            def post(path, body, token=tok):
                req = urllib.request.Request(base + path, json.dumps(body).encode(), method='POST',
                                             headers={'X-Token': token, 'Content-Type': 'application/json'})
                try:
                    return json.load(urllib.request.urlopen(req))
                except urllib.error.HTTPError as e:
                    return {'http': e.code}
            self.assertEqual(urllib.request.urlopen(url).status, 200)
            self.assertEqual(post('/api/act', {'op': 'delete', 'items': []}, 'bad'), {'http': 403})
            r = post('/api/act', {'op': 'delete', 'items': ['d:Windows/Temp']})['results'][0]
            self.assertFalse(r['ok'])                                   # locked system item
            r = post('/api/act', {'op': 'quarantine', 'items': ['g:Users/me/Downloads|delete|partial_download']})['results'][0]
            self.assertTrue(r['ok'], r)
            r = post('/api/act', {'op': 'delete', 'items': ['d:../../etc']})['results'][0]
            self.assertFalse(r['ok'])                                   # not in inventory
        finally:
            p.terminate()


if __name__ == '__main__':
    unittest.main()
