"""每天那一串：读 Claude Code 的新对话 → 拉 claude.ai → 补写缺的日记 → 证据卡（最近一周有新消息的窗口）→ 整理。13:15 自己跑（10-25 从 08:30 挪过来：同步要开窗口），手动点「立刻同步」也走这一条。

    morning(job, force)  整串（后台任务 claude-sync，进度在 job 里：stage / done / total）
    start(force)         手动点按钮
    scheduler()          常驻线程：每分钟看一眼该不该跑早上这串、该不该备份
    status()             「窗口与导入」面板上那一块要的

不挂 cron/launchd —— 这个进程本来就随「启动服务.command」常驻，自己管就行。
那一刻 Mac 在睡觉就错过了：醒来（或应用启动）后发现今天的 13:15 还没跑过，马上补；失败了隔 30 分钟再试。
全量第一次同步要 25 分钟，之后每天只拉动过的那几个对话，一两分钟。

整理（tidy）一步接一步，每步独立：前一步失败不拦后一步，结果记在 daily_runs。
  1. 手写日记    从 Notion 拉新写/改过的页（只读）；Notion 里没了的本地照样留着
  2. 手写进账本  正文真的变了的那几天，记进主题账本（src='hand'）
  3. 两个指标    新聊天天打「卡得多狠」，新手写天打「过得怎么样」
  4. 主题归类    新冒出来的主题归到河流图的大类里
  5. 窗口回顾    做过回顾的窗口，每周补一次新聊的天（见 conv_review.update_weekly）
  6. 回顾面板    那段话有新东西才重写，每周最多一次（见 reflect_chat.weekly）
"""
import json
import time
from datetime import datetime, timedelta

import backup
import browsing
import cards
import claude_code
import chatgpt_sync
import claude_sync
import codex
import cursor
import days
import diary
import jobs
import llm
import memory
import store

SYNC_AT = (13, 15)
RETRY_MIN = 30           # 失败了隔多久再试
AUTO_DAYS = 7            # 早上自动补日记只补这么多天以内的
JOB = 'claude-sync'
IDLE = {'stage': None, 'done': 0, 'total': 0, 'last': None}
FRESH = {'stage': '起浏览器', 'done': 0, 'total': 0}


# ------------------------------------------------------------------ 早上那一串

def morning(job, force=False):
    """拉 claude.ai → 写缺的日记 → 整理。同步失败也照样整理（手写日记那些不靠它）。"""
    def prog(stage, a, b):
        job.update(stage=stage, done=a, total=b)

    for name, mod in (('claude-code', claude_code), ('cursor', cursor), ('codex', codex), ('browsing', browsing)):   # 写代码的对话（本机文件，不联网）：先进来，后面写日记才看得到；浏览记录只存不进日记
        try:
            mod.sync()
        except Exception as e:                               # noqa: BLE001
            print(f'[{name}] 失败：{type(e).__name__}: {e}', flush=True)
    if chatgpt_sync.configured():                            # ChatGPT（.env 里填了登录 cookie 才跑，10-14）：失败不挡后面
        try:
            g = chatgpt_sync.sync(progress=prog)
            print(f"[chatgpt-sync] 拉了 {g['fetched']} 个，新消息 {g['new_msgs']}，涉及 {len(g['days'])} 天", flush=True)
        except Exception as e:                               # noqa: BLE001
            print(f'[chatgpt-sync] 失败：{type(e).__name__}: {e}', flush=True)
    try:
        r = claude_sync.sync(force=force, progress=prog)
    except Exception as e:                                   # noqa: BLE001
        job['error'] = f'{type(e).__name__}: {e}'[:300]
        print(f'[claude-sync] 失败：{job["error"]}', flush=True)
        r = None
    if r is not None:
        job['last'] = {**r, 'at': datetime.now().isoformat(timespec='seconds')}
        if r.get('days'):
            print(f"[claude-sync] 新消息 {r.get('new_msgs', 0)}，涉及 {len(r['days'])} 天", flush=True)
        # 最近一周缺的都补（顺手把漏网的也收了）；今天还没过完，不写。
        # 更早的缺口（多半是导入了旧聊天）不自动写 —— 可能一下几百天，留给侧栏「补全」按钮，那里先报价
        today = days.today()
        since = (datetime.fromisoformat(today) - timedelta(days=AUTO_DAYS)).date().isoformat()
        dates = [d for d in diary.missing() if since <= d < today]
        if dates:
            print(f'[claude-sync] 接着写日记：{len(dates)} 天', flush=True)
            job.update(stage='写日记', done=0, total=len(dates))
            diary.run_batch(dates, progress=lambda a, b: job.update(done=a, total=b))
    # 证据卡：最近一周有新消息的窗口（只做新来的那些消息），做完补向量。DeepSeek，一天约 $0.03
    since = (datetime.fromisoformat(days.today()) - timedelta(days=AUTO_DAYS)).date().isoformat()
    todo = cards.due_since(since)
    if todo:
        job.update(stage='证据卡', done=0, total=len(todo))
        try:
            cards.make(job, todo, cards.route_for(todo))
        except Exception as e:                               # noqa: BLE001  做卡没成不耽误后面的整理
            print(f'[cards] 早上做卡没做完：{type(e).__name__}: {e}', flush=True)
    if memory.due():                                         # 记忆（「问」先看的档案）：一周整理一次，接在新卡后面
        try:
            memory.refresh(job)
        except Exception as e:                               # noqa: BLE001
            print(f'[memory] 没整理完：{type(e).__name__}: {e}', flush=True)
    job.update(stage='整理', done=0, total=0)
    tidy(llm.ask)


def start(force=False):
    jobs.start(JOB, morning, force, **FRESH)
    return jobs.get(JOB, **IDLE)


def _last_slot(now):
    """最近一个已经过去的 13:15。"""
    slot = now.replace(hour=SYNC_AT[0], minute=SYNC_AT[1], second=0, microsecond=0)
    return slot if now >= slot else slot - timedelta(days=1)


def due(now):
    slot = _last_slot(now)
    ok = claude_sync.last_ok_run()
    if ok and datetime.fromisoformat(ok['started_at']) >= slot:
        return False                                 # 这一轮已经成功过了
    last = claude_sync.last_run()
    if last and not last.get('ok') and last.get('started_at'):
        # 刚失败过：别每分钟都撞一次，隔 RETRY_MIN 再来
        if now - datetime.fromisoformat(last['started_at']) < timedelta(minutes=RETRY_MIN):
            return False
    return True


def scheduler():
    time.sleep(90)                       # 开机先让应用把自己的事做完
    while True:
        # 备份：到点就跑，跟同步互不等待（各自一个线程）
        try:
            if backup.due():
                backup.run_bg()
        except Exception as e:                               # noqa: BLE001
            print(f'[backup] 调度出错：{e}', flush=True)
        try:
            if not jobs.running(JOB) and due(datetime.now()):
                jobs.run(JOB, morning, False, **FRESH)
        except Exception as e:                               # noqa: BLE001
            print(f'[claude-sync] 调度器出错：{e}', flush=True)
        time.sleep(60)


def status():
    return {'job': jobs.get(JOB, **IDLE), 'last_run': claude_sync.last_run(), 'daily': last(),
            'llm': {**llm.status(), 'check': llm.check()},
            'configured': bool(claude_sync.env('CLAUDE_SESSION_KEY')),
            'at': '%02d:%02d' % SYNC_AT}


# ------------------------------------------------------------------ 整理

def _step(name, fn, out):
    t0 = time.time()
    try:
        res = fn()
        out.append({'step': name, 'ok': True, 'result': res, 'secs': round(time.time() - t0, 1)})
    except Exception as e:                                   # noqa: BLE001
        out.append({'step': name, 'ok': False, 'error': f'{type(e).__name__}: {e}'[:300],
                    'secs': round(time.time() - t0, 1)})
        print(f'[daily] {name} 失败：{e}', flush=True)


def tidy(call_model):
    import conv_review
    import journal
    import ledger
    import mood
    import reflect_chat
    import river
    started = datetime.now().isoformat(timespec='seconds')
    out = []

    def s_journal():
        r = journal.sync(say=lambda m: None)
        if r['warning']:
            raise RuntimeError(r['warning'])                # 记成这一步失败，面板上看得见
        return {'fetched': r['fetched'], 'gone': r['gone'], 'failed': len(r['failed'])}

    def s_hand():
        # 正文真的变了的才重新抽主题（白天打开 Ephemeris 时也可能已经把新写的拉下来了，所以不看这次同步拉了哪些）
        dates = journal.dirty_dates()
        done = ledger.record_hand_for(dates, call_model)
        journal.mark_digested(done)                     # 抽不出来的不记，明天早上再试
        return {'days': len(done), 'dates': done}

    def s_mood():
        a, b = mood.fill(call_model, say=lambda m: None)
        return {'friction': a, 'hand': b}

    def s_cats():
        before = store.one('SELECT COUNT(*) n FROM theme_cats')['n']
        river.classify(call_model, say=lambda m: None)
        return {'new': store.one('SELECT COUNT(*) n FROM theme_cats')['n'] - before}

    def s_windows():
        return {'windows': conv_review.update_weekly()}

    def s_reflect():
        return {'rewrote': reflect_chat.weekly()}

    for name, fn in (('手写日记', s_journal), ('手写进账本', s_hand), ('两个指标', s_mood),
                     ('主题归类', s_cats), ('窗口回顾', s_windows), ('回顾面板', s_reflect)):
        _step(name, fn, out)

    with store.LOCK:
        db = store.connect()
        db.execute('INSERT INTO daily_runs(started_at, finished_at, steps) VALUES (?,?,?)',
                   (started, datetime.now().isoformat(timespec='seconds'), json.dumps(out, ensure_ascii=False)))
        db.commit()
    brief = ' · '.join(f"{o['step']}{'' if o['ok'] else ' ✗'}" for o in out)
    print(f'[daily] {brief}', flush=True)
    return out


def last():
    with store.LOCK:
        r = store.connect().execute('SELECT * FROM daily_runs ORDER BY id DESC LIMIT 1').fetchone()
    if not r:
        return None
    r = dict(r)
    r['steps'] = json.loads(r['steps'] or '[]')
    return r
