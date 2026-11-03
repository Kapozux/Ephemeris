"""Chrome 的浏览记录也存进 Ephemeris（2026-10-20）：只存，不进日记。

    sync()      把 Chrome 各个 profile 的新访问记进 browse_visits（只增不删，规矩 6）

为什么每天拷：Chrome 自己只留大约 90 天，更早的它会删掉；这里攒着，备份也按行只增不减（backup.CORE）。
在哪：~/Library/Application Support/Google/Chrome/<Default | Profile N>/History，Chrome 开着时锁着，先整个文件拷到临时目录再读。
时间是 1601-01-01（UTC）起的微秒数。只要 http(s) 的网址。
不进日记、不进搜索、不做卡（10-20，日记已经太臃肿）：别的模块都不读这张表。
"""
import glob
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import days
import store


def _default_dir():
    if os.name == 'nt':
        return os.path.join(os.environ.get('LOCALAPPDATA') or '', 'Google', 'Chrome', 'User Data')
    if os.path.isdir(os.path.expanduser('~/Library')):
        return '~/Library/Application Support/Google/Chrome'
    return '~/.config/google-chrome'                                    # Linux


DIR = os.path.expanduser(os.environ.get('EPHEMERIS_CHROME_DIR') or _default_dir())
EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _profiles():
    """→ [(profile 名, History 路径)]"""
    out = []
    for path in sorted(glob.glob(os.path.join(DIR, '*', 'History'))):
        name = os.path.basename(os.path.dirname(path))
        if name == 'Default' or name.startswith('Profile '):
            out.append((name, path))
    return out


def _read(path, after):
    """拷一份再读 visit_time > after 的访问 → [(visit_time, url, title, 停留微秒)]"""
    tmp = tempfile.mkdtemp(prefix='ephemeris-history-')
    try:
        copy = os.path.join(tmp, 'History')
        shutil.copyfile(path, copy)
        db = sqlite3.connect(copy)
        try:
            return db.execute("""SELECT v.visit_time, u.url, u.title, v.visit_duration FROM visits v JOIN urls u ON u.id = v.url
                                 WHERE v.visit_time > ? AND (u.url LIKE 'http://%' OR u.url LIKE 'https://%')
                                 ORDER BY v.visit_time""", (after,)).fetchall()
        finally:
            db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def sync(say=print):
    """返回 {'profiles': 读了几个, 'new': 新记下几次访问}。"""
    new, n = 0, 0
    for name, path in _profiles():
        after = store.one('SELECT MAX(vt) AS m FROM browse_visits WHERE profile=?', name)['m'] or 0
        rows = _read(path, after)
        n += 1
        with store.tx() as db:
            for vt, url, title, dur in rows:
                date, hhmm = days.bucket(EPOCH + timedelta(microseconds=vt))
                cur = db.execute("""INSERT INTO browse_visits (id, profile, vt, date, hhmm, url, title, secs)
                                    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
                                 (f'{name}:{vt}', name, vt, date, hhmm, url, title or '', round((dur or 0) / 1e6)))
                new += cur.rowcount
    if say:
        say(f'[browsing] {n} 个 Chrome profile，新记下 {new} 次访问')
    return {'profiles': n, 'new': new}


if __name__ == '__main__':
    print(sync())
