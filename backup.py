"""把 Ephemeris 的全部数据备份到 Google Drive（本机同步目录，Drive 客户端负责传上云）。

**全都留、只传新的**（2026-10-02 用户要求；之前是每周 139MB 整库快照、只留 8 周）：
  聊天记录/YYYY-MM/YYYY-MM-DD.jsonl   每天一个文件，消息原样（含 AI 回复）。过去的日子不变就不再写。
  数据/<表名>.jsonl                   其余每张表一个文件（日记、打卡、手写日记、主题账本、情绪分数……）。
  数据/_schema.sql                    表结构，还原时用。
  每周/YYYY-Www.md                    每周一份能直接读的：日记、手写日记、打卡。
  导入原件/                           imports/ 里导入过的原始文件。
**内容没变的文件一律不写** —— 不写就不会被 Drive 重传。按表自动导出，以后新加的表不用记得来这里登记。
**只增不删**：备份目录里的东西从不删除（包括以前那份整库快照）。
不备：.env（key 和 cookie）、.chrome/（登录态）、全文索引（*_fts*，能从消息重建）。

`restore(src, out)` 能从备份目录把库原样拼回来 —— 拼不回来的不叫备份，改了这里要重新跑一遍还原核对。
不挂 launchd，挂在应用的调度循环里每天一次，状态在「窗口与导入」面板里看得见。
"""
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import threading
from collections import defaultdict
from datetime import date as _date, datetime, timedelta

import days
import store
import index as idx

HERE = os.path.dirname(os.path.abspath(__file__))
EVERY_DAYS = 1
SKIP_TABLES = ('sqlite_sequence', 'backups', 'backup_files')   # 再加上名字里带 _fts 的（全文索引，能重建）；备份自己的记账不备

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


def last():
    r = store.q('SELECT * FROM backups ORDER BY id DESC LIMIT 1')
    return r[0] if r else None


def last_ok():
    r = store.q('SELECT * FROM backups WHERE ok=1 ORDER BY id DESC LIMIT 1')
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


# ------------------------------------------------------------------ 写文件：没变就不写

class _Writer:
    def __init__(self, root):
        self.root = root
        self.known = {r['path']: r['sha1'] for r in store.q('SELECT path, sha1 FROM backup_files')}
        self.written, self.total, self.bytes = 0, 0, 0
        self._rows = []

    def put(self, rel, data):
        if isinstance(data, str):
            data = data.encode('utf-8')
        self.total += 1
        h = hashlib.sha1(data).hexdigest()
        path = os.path.join(self.root, rel)
        if self.known.get(rel) == h and os.path.exists(path):
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + '.part', 'wb') as f:
            f.write(data)
        os.replace(path + '.part', path)
        self.written += 1
        self.bytes += len(data)
        self._rows.append((rel, h, len(data), datetime.now().isoformat(timespec='seconds')))
        return True

    def commit(self):
        with store.LOCK:
            db = store.connect()
            db.executemany('INSERT OR REPLACE INTO backup_files VALUES (?,?,?,?)', self._rows)
            db.commit()
        self._rows = []


def _jsonl(rows):
    return ''.join(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n' for r in rows)


def _tables():
    """要备份的表：全部普通表，除了全文索引和 sqlite 自己的。"""
    rows = store.q("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r for r in rows if r['name'] not in SKIP_TABLES and '_fts' not in r['name']]


def _schema():
    """建表语句：普通表 + 索引 + 两张全文索引的虚表定义（还原时建好再重建内容）。"""
    out = []
    for r in store.q("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type DESC, name"):
        n = r['name']
        if n in SKIP_TABLES:
            continue
        if '_fts' in n and not (r['sql'] or '').upper().startswith('CREATE VIRTUAL'):
            continue                                  # 全文索引的影子表，建虚表时会自动带出来
        out.append(r['sql'].rstrip(';') + ';')
    return '\n\n'.join(out) + '\n'


def _export(w):
    """每张表导出一遍；messages 按天分文件。"""
    w.put('数据/_schema.sql', _schema())
    for t in _tables():
        name = t['name']
        if name == 'messages':
            continue
        w.put(f'数据/{name}.jsonl', _jsonl(store.q(f'SELECT * FROM "{name}" ORDER BY rowid')))
    by_day = defaultdict(list)
    for r in store.q('SELECT * FROM messages ORDER BY date, ts, id'):
        by_day[r['date']].append(r)
    for d, rows in by_day.items():
        w.put(f'聊天记录/{d[:7]}/{d}.jsonl', _jsonl(rows))
    return len(by_day)


def _week_md(monday):
    """一周能直接读的版本：每天的日记、手写、打卡。"""
    days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    lo, hi = days[0], days[-1]
    diaries = {r['date']: r for r in store.q('SELECT * FROM diaries WHERE date BETWEEN ? AND ?', lo, hi)}
    hand = {}
    for r in store.q('SELECT date, title, text FROM journal WHERE date BETWEEN ? AND ? ORDER BY title', lo, hi):
        hand.setdefault(r['date'], []).append(r)
    cks = {}
    for r in store.q('SELECT * FROM checkins WHERE date BETWEEN ? AND ? ORDER BY created_at', lo, hi):
        cks.setdefault(r['date'], []).append(r)
    if not (diaries or hand or cks):
        return None
    out = [f'# {_week(monday)}　{lo} → {hi}', '']
    for d in days:
        if d not in diaries and d not in hand and d not in cks:
            continue
        wd = '一二三四五六日'[_date.fromisoformat(d).weekday()]
        out += [f'## {d} 周{wd}', '']
        for c in cks.get(d, []):
            out.append(f"- 打卡 {c['created_at'][11:16]} **{c['word']}**" + (f"：{c['note']}" if c['note'] else ''))
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


def _weeks_md(w):
    first = store.q("""SELECT MIN(d) d FROM (SELECT MIN(date) d FROM diaries
                  UNION ALL SELECT MIN(date) FROM journal UNION ALL SELECT MIN(date) FROM checkins)""")[0]['d']
    if not first:
        return 0
    start = _date.fromisoformat(first)
    monday, today, n = start - timedelta(days=start.weekday()), _date.fromisoformat(days.today()), 0
    while monday <= today:
        md = _week_md(monday)
        if md:
            w.put(f'每周/{_week(monday)}.md', md)
            n += 1
        monday += timedelta(days=7)
    return n


def _imports(root):
    """导入原件：只增不删，已经在的（同名同大小）跳过。"""
    src, out = os.path.join(HERE, 'imports'), os.path.join(root, '导入原件')
    if not os.path.isdir(src):
        return 0
    os.makedirs(out, exist_ok=True)
    n = 0
    for name in os.listdir(src):
        a, b = os.path.join(src, name), os.path.join(out, name)
        if os.path.isfile(a) and not (os.path.exists(b) and os.path.getsize(b) == os.path.getsize(a)):
            shutil.copy2(a, b)
            n += 1
    return n


# ------------------------------------------------------------------ 跑

def run():
    if not _lock.acquire(blocking=False):
        return None
    started = datetime.now().isoformat(timespec='seconds')
    root = dest()
    week = _week(_date.fromisoformat(days.today()))
    _job.update(running=True, stage='准备', error=None)
    try:
        if not root or not os.path.isdir(os.path.dirname(root)):
            raise RuntimeError('找不到 Google Drive 的同步目录 —— Drive 客户端没开？（也可以在 .env 设 EPHEMERIS_BACKUP_DIR）')
        w = _Writer(root)
        _job['stage'] = '导出聊天记录和各张表'
        ndays = _export(w)
        _job['stage'] = '写每周的 Markdown'
        nmd = _weeks_md(w)
        w.commit()
        _job['stage'] = '同步导入原件'
        nimp = _imports(root)
        with store.LOCK:
            db = store.connect()
            db.execute("""INSERT INTO backups(started_at, finished_at, ok, week, dest, weeks_md, imports_new,
                                              files_written, files_total, bytes_written, note)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (started, datetime.now().isoformat(timespec='seconds'), 1, week, root, nmd, nimp,
                        w.written, w.total, w.bytes, f'聊天 {ndays} 天'))
            db.commit()
        print(f'[backup] 共 {w.total} 个文件，这次写了 {w.written} 个（{w.bytes // 1024} KB），新导入原件 {nimp}', flush=True)
        return last_ok()
    except Exception as e:                                   # noqa: BLE001
        _job['error'] = f'{type(e).__name__}: {e}'[:300]
        with store.LOCK:
            db = store.connect()
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
    nxt = (datetime.fromisoformat(ok['finished_at']) + timedelta(days=EVERY_DAYS)).isoformat(timespec='minutes') if ok else None
    return {'job': dict(_job), 'last': last(), 'last_ok': ok, 'dest': dest(),
            'every_days': EVERY_DAYS, 'next': nxt}


# ------------------------------------------------------------------ 还原

def restore(src, out):
    """从备份目录拼出一个新库 out（不会碰正在用的 chats.db）。返回 {表名: 行数}。"""
    if os.path.exists(out):
        raise FileExistsError(out)
    db = sqlite3.connect(out)
    with open(os.path.join(src, '数据', '_schema.sql'), encoding='utf-8') as f:
        db.executescript(f.read())

    def load(table, path):
        n = 0
        with open(path, encoding='utf-8') as f:
            rows = [json.loads(line) for line in f if line.strip()]
        if rows:
            cols = list(rows[0])
            db.executemany(f'INSERT INTO "{table}" ({",".join(chr(34) + c + chr(34) for c in cols)}) '
                           f'VALUES ({",".join("?" * len(cols))})', [[r.get(c) for c in cols] for r in rows])
            n = len(rows)
        return n

    counts = {}
    known = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for p in sorted(glob.glob(os.path.join(src, '数据', '*.jsonl'))):
        t = os.path.basename(p)[:-6]
        if t in known and t != 'messages':        # 只增不删：备份目录里可能有以前导出过、现在不再备份的表
            counts[t] = load(t, p)
    counts['messages'] = sum(load('messages', p) for p in sorted(glob.glob(os.path.join(src, '聊天记录', '*', '*.jsonl'))))
    # 全文索引从数据重建
    idx.rebuild_fts(db)
    db.execute("INSERT INTO journal_fts(text, date, page_id) SELECT text, date, page_id FROM journal WHERE text <> ''")
    db.commit()
    db.close()
    return counts


if __name__ == '__main__':
    import sys
    if sys.argv[1:2] == ['--restore']:
        print(json.dumps(restore(sys.argv[2], sys.argv[3]), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(run(), ensure_ascii=False, indent=1))
