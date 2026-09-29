"""每周把 Ephemeris 的数据备份到 Google Drive（本机的同步目录，Drive 客户端负责传上云）。

备什么：
  快照/   chats.db 的完整快照（sqlite 在线备份，应用跑着也一致），gzip 压缩，保留最近 KEEP 周。
          聊天、日记、主题账本、手写日记、打卡、情绪分数……全在这一个库里。
  每周/   每周一份能直接读的 Markdown：那周的日记、手写日记、打卡。哪天应用跑不起来也能翻。
  导入原件/ imports/ 里导入过的原始文件，只增不删。
不备：.env（key 和 cookie）、.chrome/（登录态）、.cache/（能重算）。

跟 Verbatim 的备份一个思路：不挂 launchd，应用自己跑（08:30 那个调度循环里每分钟看一眼，
满 7 天就跑），状态在「窗口与导入」面板里看得见 —— 挂在外面的定时任务坏了没人知道（Verbatim 踩过）。
"""
import glob
import gzip
import os
import shutil
import sqlite3
import threading
from datetime import date as _date, datetime, timedelta

import index as idx

HERE = os.path.dirname(os.path.abspath(__file__))
EVERY_DAYS = 7
KEEP = 8                     # 快照留几周（一份约 140MB）

_job = {'running': False, 'stage': None, 'error': None}
_lock = threading.Lock()


def dest():
    """EPHEMERIS_BACKUP_DIR 优先；否则本机 Google Drive 的 My Drive/Ephemeris_备份。"""
    env = idx.env('EPHEMERIS_BACKUP_DIR')
    if env:
        return os.path.expanduser(env)
    for base in sorted(glob.glob(os.path.expanduser('~/Library/CloudStorage/GoogleDrive-*/My Drive'))):
        return os.path.join(base, 'Ephemeris_备份')
    return ''


def ensure_table():
    with idx.LOCK:
        db = idx.connect()
        db.execute("""CREATE TABLE IF NOT EXISTS backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, ok INTEGER,
            week TEXT, dest TEXT, snapshot TEXT, size INTEGER, weeks_md INTEGER, imports_new INTEGER, note TEXT)""")
        db.commit()


def _q(sql, *a):
    with idx.LOCK:
        return [dict(r) for r in idx.connect().execute(sql, a).fetchall()]


def last():
    ensure_table()
    r = _q('SELECT * FROM backups ORDER BY id DESC LIMIT 1')
    return r[0] if r else None


def last_ok():
    ensure_table()
    r = _q('SELECT * FROM backups WHERE ok=1 ORDER BY id DESC LIMIT 1')
    return r[0] if r else None


def due(now=None):
    now = now or datetime.now()
    ok = last_ok()
    if ok and now - datetime.fromisoformat(ok['finished_at']) < timedelta(days=EVERY_DAYS):
        return False
    l = last()
    if l and not l['ok'] and now - datetime.fromisoformat(l['started_at']) < timedelta(hours=6):
        return False                      # 刚失败过（比如 Drive 没开），隔 6 小时再试
    return True


def _week(d):
    y, w, _ = d.isocalendar()
    return f'{y}-W{w:02d}'


# ------------------------------------------------------------------ 三样东西

def _snapshot(out_dir, week):
    """sqlite 在线备份到临时文件 → gzip → 改名。中途断了不会留下半个文件冒充完整快照。"""
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d')
    final = os.path.join(out_dir, f'chats_{week}_{stamp}.db.gz')
    raw = final + '.part.db'
    gz = final + '.part'
    try:
        src = sqlite3.connect(os.path.join(HERE, 'chats.db'))
        dst = sqlite3.connect(raw)
        with src:
            src.backup(dst)
        dst.close()
        src.close()
        with open(raw, 'rb') as f, gzip.open(gz, 'wb', compresslevel=6) as g:
            shutil.copyfileobj(f, g, 1 << 20)
        os.replace(gz, final)
    finally:
        for p in (raw, gz):
            if os.path.exists(p):
                os.remove(p)
    # 只留最近 KEEP 份
    snaps = sorted(glob.glob(os.path.join(out_dir, 'chats_*.db.gz')))
    for old in snaps[:-KEEP]:
        os.remove(old)
    return final, os.path.getsize(final)


def _week_md(monday):
    """一周能直接读的版本：每天的日记、手写、打卡。"""
    days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    lo, hi = days[0], days[-1]
    diaries = {r['date']: r for r in _q('SELECT * FROM diaries WHERE date BETWEEN ? AND ?', lo, hi)}
    hand = {}
    try:
        for r in _q('SELECT date, title, text FROM journal WHERE date BETWEEN ? AND ? ORDER BY title', lo, hi):
            hand.setdefault(r['date'], []).append(r)
    except sqlite3.OperationalError:
        pass
    cks = {}
    try:
        for r in _q('SELECT * FROM checkins WHERE date BETWEEN ? AND ? ORDER BY created_at', lo, hi):
            cks.setdefault(r['date'], []).append(r)
    except sqlite3.OperationalError:
        pass
    if not (diaries or hand or cks):
        return None
    import json
    out = [f'# {_week(monday)}　{lo} → {hi}', '']
    for d in days:
        if d not in diaries and d not in hand and d not in cks:
            continue
        wd = '一二三四五六日'[_date.fromisoformat(d).weekday()]
        out += [f'## {d} 周{wd}', '']
        for c in cks.get(d, []):
            line = f"- 打卡 {c['created_at'][11:16]} **{c['word']}**" + (f"：{c['note']}" if c['note'] else '')
            out.append(line)
            if c.get('reply'):
                out.append(f"  > {c['reply']}")
        if d in cks:
            out.append('')
        for h in hand.get(d, []):
            out += ['**我手写的**', '', h['text'] or '', '']
        dy = diaries.get(d)
        if dy:
            out += [f"**{dy['headline']}**", '', dy['narrative'] or '', '']
            for k, lab in (('artifacts', '做出来的'), ('open_loops', '没做完的')):
                try:
                    arr = json.loads(dy[k] or '[]')
                except ValueError:
                    arr = []
                if arr:
                    out.append(f'{lab}：' + '；'.join(arr))
            out.append('')
    return '\n'.join(out)


def _weeks_md(out_dir, full):
    """全量第一次把所有周都写出来；之后只重写最近 3 周（日记会晚一天写、打卡能补）。"""
    os.makedirs(out_dir, exist_ok=True)
    first = _q("""SELECT MIN(d) d FROM (SELECT MIN(date) d FROM diaries
                  UNION ALL SELECT MIN(date) FROM journal UNION ALL SELECT MIN(date) FROM checkins)""")[0]['d']
    if not first:
        return 0
    today = _date.today()
    start = _date.fromisoformat(first) if full else today - timedelta(days=21)
    monday = start - timedelta(days=start.weekday())
    n = 0
    while monday <= today:
        md = _week_md(monday)
        if md:
            p = os.path.join(out_dir, f'{_week(monday)}.md')
            with open(p + '.part', 'w', encoding='utf-8') as f:
                f.write(md)
            os.replace(p + '.part', p)
            n += 1
        monday += timedelta(days=7)
    return n


def _imports(out_dir):
    """导入原件：只增不删，已经在的（同名同大小）跳过。"""
    src = os.path.join(HERE, 'imports')
    if not os.path.isdir(src):
        return 0
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    for name in os.listdir(src):
        a, b = os.path.join(src, name), os.path.join(out_dir, name)
        if os.path.isfile(a) and not (os.path.exists(b) and os.path.getsize(b) == os.path.getsize(a)):
            shutil.copy2(a, b)
            n += 1
    return n


# ------------------------------------------------------------------ 跑

def run():
    if not _lock.acquire(blocking=False):
        return None
    ensure_table()
    started = datetime.now().isoformat(timespec='seconds')
    root = dest()
    week = _week(_date.today())
    _job.update(running=True, stage='准备', error=None)
    try:
        if not root or not os.path.isdir(os.path.dirname(root)):
            raise RuntimeError('找不到 Google Drive 的同步目录 —— Drive 客户端没开？（也可以在 .env 设 EPHEMERIS_BACKUP_DIR）')
        full = not last_ok()
        _job['stage'] = '拷数据库快照'
        snap, size = _snapshot(os.path.join(root, '快照'), week)
        _job['stage'] = '写每周的 Markdown'
        nmd = _weeks_md(os.path.join(root, '每周'), full)
        _job['stage'] = '同步导入原件'
        nimp = _imports(os.path.join(root, '导入原件'))
        with idx.LOCK:
            db = idx.connect()
            db.execute('INSERT INTO backups(started_at, finished_at, ok, week, dest, snapshot, size, weeks_md, imports_new) '
                       'VALUES (?,?,?,?,?,?,?,?,?)',
                       (started, datetime.now().isoformat(timespec='seconds'), 1, week, root,
                        os.path.basename(snap), size, nmd, nimp))
            db.commit()
        print(f'[backup] {week} 快照 {size // 1024 // 1024}MB · 每周 {nmd} 份 · 新导入原件 {nimp}', flush=True)
        return last_ok()
    except Exception as e:                                   # noqa: BLE001
        _job['error'] = f'{type(e).__name__}: {e}'[:300]
        with idx.LOCK:
            db = idx.connect()
            db.execute('INSERT INTO backups(started_at, finished_at, ok, week, dest, note) VALUES (?,?,?,?,?,?)',
                       (started, datetime.now().isoformat(timespec='seconds'), 0, week, root, _job['error']))
            db.commit()
        print(f'[backup] 失败：{_job["error"]}', flush=True)
        return None
    finally:
        _job.update(running=False, stage=None)
        _lock.release()


def run_bg():
    if _job['running']:
        return
    threading.Thread(target=run, daemon=True).start()


def status():
    ok = last_ok()
    root = dest()
    snaps = sorted(glob.glob(os.path.join(root, '快照', 'chats_*.db.gz'))) if root else []
    nxt = (datetime.fromisoformat(ok['finished_at']) + timedelta(days=EVERY_DAYS)).isoformat(timespec='minutes') if ok else None
    return {'job': dict(_job), 'last': last(), 'last_ok': ok, 'dest': root,
            'snapshots': len(snaps), 'keep': KEEP, 'every_days': EVERY_DAYS, 'next': nxt}


if __name__ == '__main__':
    import json
    print(json.dumps(run(), ensure_ascii=False, indent=1))
