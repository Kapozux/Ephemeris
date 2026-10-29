"""生成演示用的虚构聊天记录（只用 personas.py 里编的人设，不给模型任何真实聊天）。

    ../venv/bin/python -m research.synth gen [P01 ...]   每人每期调一次 DeepSeek，存 data/raw/<pid>/pNN.json（有了就跳过）
    ../venv/bin/python -m research.synth load            把 raw 装进 data/<pid>.db，写 data/cesd.csv、data/<pid>.truth.json

用哪家模型照研究方案的 models（research.route），不自动换。装库每个人单开一个进程：store 一个进程只认一个库。
"""
import csv
import hashlib
import json
import os
import random
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import research
from research import DATA, db_path, route
from research import personas as P

RAW = os.path.join(DATA, 'raw')

PROMPT = """You are generating FICTIONAL chat logs between one person and AI assistants, for a research software demo.
Nothing here is real. Write {n} separate chat windows (conversations) that this person had during the two weeks below.

Person: {name}, {age}. {bio}
Language of the person's messages and the AI's replies: {lang}.
Platforms they use (pick one per window): {platforms}.
Period: {start} to {end} (local time, {tz}).

What is going on in their life this period: {events}
How they are doing this period: {mood}
Sleep this period: {sleep}
Decisions: {reliance}

Rules:
- Most windows are about everyday things (work, school, cooking, errands, hobbies, code, travel, money, family).
  How they are doing shows only indirectly, the way it would in real chats; never name a diagnosis or a questionnaire.
- Each window has 4 to 10 messages, alternating, the person ("h") first. The person writes casually, like texting.
  The AI replies naturally in 1 to 5 sentences.
- "when" is the local start time "YYYY-MM-DD HH:MM", inside the period. Spread windows over different days.
- "idea_from": if the window contains a decision or plan the person ends up going with, who first proposed that option:
  "user" or "ai". If there is no decision, "none". "idea_quote": the exact sentence (copied verbatim from one message)
  where that option was first proposed; empty string if "none".
- Use made-up names for any other people. No real public figures, no politics.

Output only JSON:
{{"windows": [{{"when": "YYYY-MM-DD HH:MM", "platform": "...", "title": "short title",
  "idea_from": "user|ai|none", "idea_quote": "...", "msgs": [["h", "..."], ["a", "..."]]}}]}}"""


def mood_text(s):
    if s < 0.25:
        return 'Doing fine overall: normal energy, interested in things.'
    if s < 0.4:
        return 'A bit low and stressed: more tired than usual, some worry, still functioning.'
    if s < 0.55:
        return 'Clearly low: tired, less interest in usual things, self-critical, harder to concentrate.'
    return 'Low most days: hopeless at times, withdrawing from people, little energy, ruminating.'


SLEEP = {'normal': 'Normal. Chats start between 07:30 and 23:30; at most one window starts after midnight.',
         'poor': 'Poor. They are often awake late: at least 3 windows start between 00:00 and 05:30 local time.'}
RELIANCE = {'low': 'They make their own decisions; the AI mostly supplies information. When there is a decision, '
                   'it is usually the person who proposes the option.',
            'medium': 'About half the time they ask the AI what to do and go with its suggestion.',
            'high': 'In most windows with a decision, they ask the AI to decide and go with what the AI proposes.'}


def period_dates(k):
    """第 k 期（1 起）：[上一次 CES-D, 这一次 CES-D)，返回 (开始, 结束) 两个 date。"""
    s = date.fromisoformat(P.START) + timedelta(days=P.PERIOD_DAYS * (k - 1))
    return s, s + timedelta(days=P.PERIOD_DAYS)


def prompt(person, k):
    s, e = period_dates(k)
    events = person['events'].get(k) or 'Nothing special.'
    if k > 1:
        before = [f'period {i}: {person["events"][i]}' for i in range(1, k) if i in person['events']]
        if before:
            events += ' (Earlier: ' + ' '.join(before) + ')'
    return PROMPT.format(
        n=random.Random(f"{person['pid']}{k}").randint(6, 9), name=person['name'], age=person['age'],
        bio=person['bio'], lang=person['lang'], platforms=', '.join(person['platforms']),
        start=s.isoformat(), end=(e - timedelta(days=1)).isoformat(), tz=person['tz'], events=events,
        mood=mood_text(person['severity'][k - 1]), sleep=SLEEP[person['sleep'][k - 1]],
        reliance=RELIANCE[person['reliance'][k - 1]])


def check(d, person, k):
    s, e = period_dates(k)
    errs = []
    for i, w in enumerate(d.get('windows') or []):
        try:
            day = datetime.strptime(w['when'], '%Y-%m-%d %H:%M').date()
        except (KeyError, ValueError):
            errs.append(f'{i}: when')
            continue
        if not s <= day < e:
            errs.append(f'{i}: {w["when"]} outside period')
        msgs = w.get('msgs') or []
        if not msgs or not all(isinstance(m, list) and len(m) == 2 and m[0] in ('h', 'a') and isinstance(m[1], str)
                               for m in msgs) or msgs[0][0] != 'h':
            errs.append(f'{i}: msgs')
        if w.get('platform') not in person['platforms']:
            w['platform'] = person['platforms'][0]
    return errs if d.get('windows') else ['no windows']


def gen_one(person, k):
    path = os.path.join(RAW, person['pid'], f'p{k:02d}.json')
    if os.path.exists(path):
        return path, 'cached'
    for attempt in range(3):
        d = route.call_json(research.study(), prompt(person, k), 'research-synth', person['pid'])
        errs = check(d or {}, person, k)
        if d and not errs:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            return path, f'{len(d["windows"])} windows'
    return path, f'FAILED: {errs[:3]}'


def gen(pids):
    research.ledger()
    jobs = [(p, k) for p in P.PEOPLE if not pids or p['pid'] in pids for k in range(1, P.PERIODS + 1)]
    with ThreadPoolExecutor(8) as pool:
        for (p, k), (path, r) in zip(jobs, pool.map(lambda j: gen_one(*j), jobs)):
            print(p['pid'], k, r, flush=True)


def _id(*parts):
    return hashlib.sha1('|'.join(map(str, parts)).encode()).hexdigest()[:20]


def load_one(pid):
    """在单独的进程里跑（DIARY_DB 已经指到 data/<pid>.db）。用固定的 id，重复装不会重复。"""
    import store
    assert os.path.abspath(store.DB_PATH) == os.path.abspath(db_path(pid)), store.DB_PATH
    person = P.by_pid(pid)
    tz = ZoneInfo(person['tz'])
    truth = {}
    with store.tx() as db:
        for k in range(1, P.PERIODS + 1):
            path = os.path.join(RAW, pid, f'p{k:02d}.json')
            if not os.path.exists(path):
                print(pid, k, 'no raw, skipped')
                continue
            for i, w in enumerate(json.load(open(path, encoding='utf-8'))['windows']):
                cid = str(uuid.uuid5(uuid.NAMESPACE_URL, f'ephemeris-research/{pid}/{k}/{i}'))
                t = datetime.strptime(w['when'], '%Y-%m-%d %H:%M').replace(tzinfo=tz)
                rng = random.Random(cid)
                stamps, chars = [], 0
                for j, (role, text) in enumerate(w['msgs']):
                    if j:
                        t += timedelta(seconds=rng.randint(40, 240))
                    utc = t.astimezone(timezone.utc).isoformat()
                    db.execute('INSERT OR IGNORE INTO messages (id, conv_id, source, ts, date, hhmm, role, text, chars) '
                               'VALUES (?,?,?,?,?,?,?,?,?)',
                               (_id(cid, j), cid, w['platform'], utc, t.date().isoformat(), t.strftime('%H:%M'),
                                'human' if role == 'h' else 'assistant', text, len(text)))
                    stamps.append(utc)
                    chars += len(text)
                db.execute('INSERT OR IGNORE INTO conversations (id, source, title, first_ts, last_ts, n_msgs, chars, '
                           'title_fallback, n_days) VALUES (?,?,?,?,?,?,?,0,1)',
                           (cid, w['platform'], w.get('title') or '', stamps[0], stamps[-1], len(w['msgs']), chars))
                truth[cid] = {'period': k, 'idea_from': w.get('idea_from', 'none'), 'idea_quote': w.get('idea_quote', '')}
    with open(os.path.join(DATA, f'{pid}.truth.json'), 'w', encoding='utf-8') as f:
        json.dump(truth, f, ensure_ascii=False, indent=1)
    n = store.one('SELECT COUNT(*) n FROM messages')['n']
    print(pid, f'{len(truth)} windows, {n} messages')


def write_cesd():
    path = os.path.join(DATA, 'cesd.csv')
    with open(path, 'w', newline='') as f:
        out = csv.writer(f)
        out.writerow(['pid', 'survey_number', 'survey_date', 'cesd_score'])
        for p in P.PEOPLE:
            for n, score in enumerate(P.cesd(p)):
                day = date.fromisoformat(P.START) + timedelta(days=P.PERIOD_DAYS * n)
                out.writerow([p['pid'], n, day.isoformat(), score])
    print('->', path)


def load():
    os.makedirs(DATA, exist_ok=True)
    for p in P.PEOPLE:
        env = dict(os.environ, DIARY_DB=db_path(p['pid']), EPHEMERIS_NO_USAGE='1')
        subprocess.run([sys.executable, '-m', 'research.synth', '_load', p['pid']], env=env, check=True,
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    write_cesd()


if __name__ == '__main__':
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == 'gen':
        gen(args)
    elif cmd == 'load':
        load()
    elif cmd == '_load':
        load_one(args[0])
