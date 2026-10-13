"""一篇日记：读、写、补。

    读  parse(行) / get(日期) / as_text / loose_ends
        「日记长什么样」的唯一定义：库里 topics / artifacts / open_loops 存的是 JSON 字符串，拿出来先 parse；
        要拼成纯文本（喂模型、写备份）用 as_text / loose_ends。以前这几步在 5 个地方各写一份。
    写  write(日期)：切好的一天 → 模型 → 存库 → 记进主题账本。失败的原因记在补全任务的 errors 里
    补  missing() 该有还没有的天；start_batch / run_batch 从早往晚一天天写（后台任务 diary-batch，见 jobs.py）

模型调用走 llm.py（默认 gemini-3.5-flash-lite），叙事口径沿用「回顾面板约定」：
直白，先说主要在弄什么，不要比喻和抒情。
"""
import json
import os
import time
from datetime import datetime

import bundle
import index as idx
import jobs
import llm
import store
from slice import render_day

LISTS = ('topics', 'artifacts', 'open_loops')
LOOSE_ENDS = (('artifacts', '做出来的'), ('open_loops', '没做完的'))


def parse(row):
    """库里的一行 → dict，列表字段解开；坏了 / 没有的当空列表。已经解开的原样留着。"""
    d = dict(row)
    for k in LISTS:
        v = d.get(k)
        if not isinstance(v, list):
            try:
                v = json.loads(v or '[]')
            except (TypeError, ValueError):
                v = []
        d[k] = v if isinstance(v, list) else []
    return d


def get(date):
    r = store.one('SELECT * FROM diaries WHERE date=?', date)
    return parse(r) if r else None


def loose_ends(d):
    """['做出来的：a；b', '没做完的：c']，空的不出。"""
    d = parse(d)
    return [lab + '：' + '；'.join(d[k]) for k, lab in LOOSE_ENDS if d[k]]


def as_text(d):
    """纯文本：标题、正文、做出来的 / 没做完的，一行一段。没有日记给空串。"""
    if not d:
        return ''
    d = parse(d)
    return '\n'.join(x for x in [d.get('headline') or '', d.get('narrative') or '', *loose_ends(d)] if x)


CACHE_DIR = os.environ.get('DIARY_CACHE') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '.cache')

PROMPT = """下面是 __NAME__ 在 {date} 这一天，跨 {n_threads} 个不同对话窗口里发给 AI 的全部消息。
你的任务是据此写一篇当天的日记。

写作要求（严格遵守）：
- 直白。第一句说清这天主要在弄什么，然后第二、第三块。不要比喻，不要抒情，不要"仿佛""沉浸""思绪万千"这类修辞。
- 用第一人称「我」写，像他自己记的，不是旁观者报告。
- 只写材料里有的。不要评价他，不要给建议，不要安慰。没聊到的不要补。
- 材料要是分成【AI 聊天】【Claude Code】【问】几组：日记主要写【AI 聊天】里的事和想法；【Claude Code】只用一两句说今天在做哪个项目、卡在哪、有什么决定；【问】最多一句。
- **做了什么，也写他当时感觉怎样**：他自己说出来的感受（烦、累、焦虑、开心、得意、崩溃、纠结……）要写进去，尽量用他的原话或接近原话，放在对应的那件事旁边。他没说出来的不要猜。
- 有些窗口带【前情】：那是这条线今天之前聊过什么，**只用来理解今天这段在说什么**。
  绝对不要把前情里的事当成今天发生的写进日记。今天只是接着往下聊，就写今天推进到哪一步；
  必要时可以带一句「继续上周的 X」这种交代，但主语始终是今天做的事。
- 同一件事如果在几个窗口里都出现，合成一件事写，不要重复。
- 具体的东西要留下来：科目名、项目名、报错、决定、数字、人名。日记的价值在细节。
- 下面还给了【上一篇日记】和【最近在进行的事】，同样**只是背景**，用来让这篇跟之前接得上：
  · 今天在接着做之前的事，可以交代一句，比如「接着昨天把第三稿改完」「隔了一个多月又捡起 BERT」。
  · **不要写「第几天」**，天数页面上另有标注。只在真有意义时提连续性，不要每件事都交代。
  · 上一篇日记里没做完的事今天做完了，可以点一句。今天没碰的事不要写。

【上一篇日记】
{prev}

【最近在进行的事】
{ledger}

只输出 JSON，不要 markdown 代码块，结构：
{{
  "headline": "一句话概括这天（不超过 24 字，不要用句号结尾）。绝对不要写日期、不要以「日记」「记录」开头——日期已经在页面上了，直接说这天在干什么",
  "narrative": "正文。2-4 段，段间用 \\n\\n 分隔。总共 300-600 字。",
  "topics": ["当天的主要话题，3-6 个，每个 2-6 字"],
  "artifacts": ["当天做出来/改动了的具体东西，如「改完经济IA第三稿」「跑通BERT baseline」。没有就空数组"],
  "open_loops": ["当天提到但没完成、明确说要做的事。没有就空数组"],
  "mood": "一个词：开心/满足/兴奋/平稳/疲惫/焦虑/烦躁/低落/崩溃。只根据他自己说的话，看整天占主导的那一面；看得出倾向就别写平稳",
  "threads": [
    {{"name": "今天花了功夫的一件事，2-10 字，具体到这件事本身", "note": "今天在这件事上推进到哪，一句话，不超过 40 字"}}
  ]
}}
threads 通常 2-5 条，只顺口提了一句的不算。一条只说一件事：「CS IA」和「CS 期末复习」是两条，不要合成「CS 复习与 IA」。
不是科目或领域（「经济」「学习」不行），是这门课里具体在弄的那件事（「经济 IA 改稿」「经济 Paper1 备考」）。

--- 当天记录 ---
{body}
"""

PROMPT = PROMPT.replace('__NAME__', idx.who())


def _cache_path(date):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f'{date}.json')


def _render_prev(prev, date):
    if not prev:
        return '（没有——这是第一篇。）'
    from datetime import date as D
    gap = (D.fromisoformat(date) - D.fromisoformat(prev['date'])).days
    when = '昨天' if gap == 1 else f'{gap} 天前'
    loops = parse(prev)['open_loops']
    out = [f"{prev['date']}（{when}）　{prev.get('headline') or ''}", prev.get('narrative') or '']
    if loops:
        out.append('当时没做完的：' + '；'.join(loops))
    return '\n'.join(out)


# ------------------------------------------------------------------ 当天记录只写入一次（2026-10-13）
# 写日记、打「卡得多狠」（mood.score_friction）喂的是同一段 render_day。走 Claude 时把它放进一个会话：
# 第一次写入缓存，第二件事接着这个会话问（--resume），这段按读取算。缓存 1 小时，会话 50 分钟内复用。
_sessions = {}
SESSION_TTL = 50 * 60


def session_for(date, day=None):
    """这一天的 llm.Session（材料 = 当天记录）。Claude 用不了时返回 None，调用方照原来整段送。"""
    if not llm.claude_on():
        return None
    body = render_day(day or bundle.day_bundle(date))
    hit = _sessions.get(date)
    if hit and hit[1] == body and time.time() - hit[0] < SESSION_TTL:
        return hit[2]
    s = llm.Session(f'--- {date} 当天记录：{idx.who()} 这一天发给 AI 的全部消息 ---\n{body}', effort='high',   # 日记用 Opus 的 high 档（10-13 用户：日记要给力点）
                    purpose=f'day {date}')
    _sessions[date] = (time.time(), body, s)
    return s


def generate(day, refresh=False, char_budget=60000, prev=None, ledger=''):
    """生成一天的日记。命中缓存就直接返回。

    prev 是上一篇日记（diaries 表的一行），ledger 是 ledger.render_for_prompt 的输出。
    """
    path = _cache_path(day['date'])
    if not refresh and os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    fields = dict(date=day['date'], n_threads=day['n_threads'], prev=_render_prev(prev, day['date']),
                  ledger=ledger or '（账本还是空的。）')
    sess = session_for(day['date'], day) if char_budget == 60000 else None
    if sess:                                         # 当天记录已经在会话里：只送写法要求
        parsed = llm.parse_json(sess.ask(PROMPT.replace('下面是', '前面那段是', 1).format(body='（就是前面那段，不再重复。）',
                                                                                    **fields)), dict)
    else:
        parsed = llm.ask_json(PROMPT.format(body=render_day(day, char_budget=char_budget), **fields), dict, tries=1)
    if not parsed:
        return None

    parsed['date'] = day['date']
    parsed['stats'] = {
        'threads': day['n_threads'],
        'messages': day['n_user_msgs'],
        'chars': day['user_chars'],
        'conversations': [t['conversation'] for t in day['threads']],
        'span': f"{day['threads'][0]['first']}–{day['threads'][-1]['last']}" if day['threads'] else '',
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(parsed, f, ensure_ascii=False, indent=1)
    return parsed


# ------------------------------------------------------------------ 写：一天

MIN_CHARS = 800          # 少于这个字数的天不值得写日记
COST_PER_DAY = 0.01      # flash-lite 一天约 6 万字进去，按「回顾面板约定」记的 $0.006/次 放宽一点算
RETRY_AFTER = 3          # 秒。模型偶尔抽风，隔一会儿原样再跑就过了


def write(date):
    """写这一天的日记并存下，接着记进主题账本。写不出来返回 False。"""
    import ledger            # 账本读日记文本要用本模块，放这里免得两边互相导入
    entry = generate(bundle.day_bundle(date), refresh=True,
                     prev=ledger.previous_diary(date), ledger=ledger.render_for_prompt(date))
    if not entry:
        # generate() 返回 None = 模型输出解析不成 JSON。不记下来的话批量跑完只剩一个数字。
        _fail(date, '模型返回解析不出 JSON')
        return False
    store.run('INSERT OR REPLACE INTO diaries (date, headline, narrative, topics, artifacts, open_loops, mood, '
              'model, generated_at) VALUES (?,?,?,?,?,?,?,?,?)',
              date, entry.get('headline', ''), entry.get('narrative', ''),
              json.dumps(entry.get('topics') or [], ensure_ascii=False),
              json.dumps(entry.get('artifacts') or [], ensure_ascii=False),
              json.dumps(entry.get('open_loops') or [], ensure_ascii=False),
              entry.get('mood', ''), os.environ.get('REFLECT_MODEL', ''),
              datetime.now().isoformat(timespec='seconds'))
    ledger.record(date, ledger.match(date, entry.get('threads'), llm.ask))
    return True


def start_one(date):
    """日记页上的「重新生成」。这天已经在写就不重复开。"""
    jobs.start(f'diary:{date}', lambda job: write(date))


def writing(date):
    return jobs.running(f'diary:{date}')


# ------------------------------------------------------------------ 补：缺的那些天

BATCH = 'diary-batch'
BATCH_IDLE = {'total': 0, 'done': 0, 'failed': 0, 'current': None, 'stop': False, 'errors': []}


def missing(force=False):
    """聊够 MIN_CHARS 字、还没有日记的天，新的在前。force=True 连已经有的也算（改了 prompt 之后重写用）。"""
    have = set() if force else {r['date'] for r in store.q('SELECT date FROM diaries')}
    # 别名不能叫 chars：SQLite 在 HAVING 里会把它解析成原始列，不是聚合值（实测 35 vs 271 天）
    rows = store.q("""SELECT date, SUM(chars) total FROM messages WHERE role='human'
                       GROUP BY date HAVING total >= ? ORDER BY date DESC""", MIN_CHARS)
    return [r['date'] for r in rows if r['date'] not in have]


def _fail(date, why):
    """某天失败的原因记在补全任务上。以前是 except: pass，跑完只剩个计数，没法查。"""
    print(f'[diary] {date} 失败：{why}', flush=True)
    errs = jobs.state(BATCH).setdefault('errors', [])
    errs.append({'date': date, 'error': why[:300]})
    del errs[:-30]


def _write_or_note(date):
    try:
        return write(date)
    except Exception as e:                                  # noqa: BLE001
        _fail(date, f'{type(e).__name__}: {e}')
        return False


def _write_many(job, dates, progress=None):
    # 日记是一天接一天的（上一篇 + 账本），必须从早往晚写
    for d in sorted(dates):
        if job['stop']:
            break
        job['current'] = d

        def one(_job):
            # 2026-09-20 实测：73 天里 1 天解析不成 JSON 或超时，隔一会儿原样再跑就过了
            if _write_or_note(d):
                return True
            time.sleep(RETRY_AFTER)
            return _write_or_note(d)
        ok = jobs.run(f'diary:{d}', one)             # 这天要是正被单独写着，跳过（记失败）
        job['done' if ok else 'failed'] += 1
        if progress:
            progress(job['done'] + job['failed'], job['total'])


def _fresh(dates):
    return {**BATCH_IDLE, 'total': len(dates), 'errors': []}


def batch_status():
    return jobs.get(BATCH, **BATCH_IDLE)


def start_batch(force=False):
    """「补全」按钮：后台把缺的写掉。已经在跑就只返回状态。"""
    if jobs.running(BATCH):
        return batch_status()
    dates = missing(force)
    if not dates:
        return {**batch_status(), 'total': 0}
    jobs.start(BATCH, _write_many, dates, **_fresh(dates))
    return batch_status()


def run_batch(dates, progress=None):
    """在当前线程写这些天（早上的流程用）。补全正在跑就什么都不做，返回 False。"""
    if jobs.running(BATCH):
        return False
    jobs.run(BATCH, _write_many, dates, progress, **_fresh(dates))
    return True


def stop_batch():
    if jobs.running(BATCH):
        jobs.state(BATCH)['stop'] = True
    return batch_status()


def to_markdown(entry):
    s = entry['stats']
    out = [f"# {entry['date']}　{entry.get('headline', '')}", '']
    out.append(entry.get('narrative', ''))
    out.append('')
    if entry.get('artifacts'):
        out.append('**做出来的：**')
        out += [f'- {a}' for a in entry['artifacts']]
        out.append('')
    if entry.get('open_loops'):
        out.append('**没做完的：**')
        out += [f'- {a}' for a in entry['open_loops']]
        out.append('')
    out.append(f"*{s['threads']} 个窗口 · {s['messages']} 条 · {s['chars']:,} 字 · "
               f"{s['span']} · {entry.get('mood', '')}*")
    return '\n'.join(out)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('days_json')
    ap.add_argument('--date', help='只生成某一天 YYYY-MM-DD')
    ap.add_argument('--limit', type=int, default=0, help='只处理字数最多的 N 天')
    ap.add_argument('--min-chars', type=int, default=2000, help='低于这个字数的天跳过')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--out', help='把 markdown 写到这个目录')
    args = ap.parse_args()

    with open(args.days_json, encoding='utf-8') as f:
        days = json.load(f)

    if args.date:
        targets = [days[args.date]] if args.date in days else []
    else:
        targets = [d for d in days.values() if d['user_chars'] >= args.min_chars]
        targets.sort(key=lambda d: -d['user_chars'])
        if args.limit:
            targets = targets[:args.limit]
        targets.sort(key=lambda d: d['date'])

    print(f'待生成 {len(targets)} 天')
    ok = 0
    for d in targets:
        entry = generate(d, refresh=args.refresh)
        if not entry:
            print(f"  {d['date']}  失败")
            continue
        ok += 1
        print(f"  {d['date']}  {entry.get('headline', '')}")
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            with open(os.path.join(args.out, f"{d['date']}.md"), 'w', encoding='utf-8') as f:
                f.write(to_markdown(entry))
    print(f'\n完成 {ok}/{len(targets)}')
