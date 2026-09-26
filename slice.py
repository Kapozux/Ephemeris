"""把跨窗口的聊天记录按天切开。

一个对话窗口可能横跨几个月（A 窗口 1-4 月、B 窗口 2-5 月），
而日记要的是「3 月 3 日这天我在所有窗口里说了什么」。
这里做的就是这个转置：conversation-major -> date-major。

吃两种输入：
  - JSON  export_claude.js 导出的（推荐，时间戳没被破坏）
  - MD    浏览器脚本旧版导出的 markdown（凑合能用，时间是本地时区字符串）
"""
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

MONTHS = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}

# 本地时区：Claude API 给的是 UTC，按天分桶必须先落到你所在时区，
# 否则晚上 8 点以后的消息会被算进第二天。
LOCAL_TZ = timezone(timedelta(hours=8))          # Asia/Shanghai

# 一天的分界。设成 4 表示凌晨 4 点前算前一天——熬夜聊的东西归到前一天的日记里。
DAY_CUTOFF_HOUR = int(os.environ.get('DIARY_DAY_CUTOFF', '4'))


def _bucket_date(dt):
    """带 cutoff 的日期归属。"""
    local = dt.astimezone(LOCAL_TZ)
    if local.hour < DAY_CUTOFF_HOUR:
        local -= timedelta(days=1)
    return local.date().isoformat()


# ---------------------------------------------------------------- 读 JSON

def load_json(path):
    """读 export_claude.js 的输出。返回 [conversation]。"""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    convs = data.get('conversations', data) if isinstance(data, dict) else data
    out = []
    for c in convs:
        msgs = []
        for m in c.get('messages', []):
            ts = m.get('created_at')
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            text = (m.get('text') or '').strip()
            if not text:
                continue
            msgs.append({'dt': dt, 'role': m.get('role', 'human'), 'text': text})
        if msgs:
            out.append({'id': c.get('uuid') or c.get('id') or '',
                        'title': c.get('name') or c.get('title') or 'Untitled',
                        'messages': msgs})
    return out


# ------------------------------------------------------------------ 读 MD

_H_RE = re.compile(r'\n## (Human|Claude) \(([^)]*)\):\n')


def _parse_md_ts(s):
    """'Jun 27, 2026, 9:13 PM' -> aware datetime（按本地时区解释）。"""
    m = re.match(r'(\w+) (\d+), (\d+),\s*(\d+):(\d+)\s*(AM|PM)', s.strip())
    if not m:
        return None
    mon = MONTHS.get(m.group(1))
    if not mon:
        return None
    hour = int(m.group(4)) % 12
    if m.group(6) == 'PM':
        hour += 12
    return datetime(int(m.group(3)), mon, int(m.group(2)),
                    hour, int(m.group(5)), tzinfo=LOCAL_TZ)


def load_markdown(path):
    """读浏览器脚本导出的合并 markdown。

    对话之间用 '# 标题' 分隔，但正文里用户自己写的 markdown 也可能有 '# '，
    所以只认后面紧跟 '## Human (' 的那种。
    """
    with open(path, encoding='utf-8', errors='replace') as f:
        raw = f.read()
    lines = raw.split('\n')
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1

    starts = []
    for i, ln in enumerate(lines):
        if ln.startswith('# ') and not ln.startswith('## '):
            for j in range(i + 1, min(i + 8, len(lines))):
                if lines[j].startswith('## Human ('):
                    starts.append((i, ln[2:].strip()))
                    break

    out = []
    for k, (li, title) in enumerate(starts):
        s = offsets[li]
        e = offsets[starts[k + 1][0]] if k + 1 < len(starts) else len(raw)
        parts = _H_RE.split(raw[s:e])
        msgs = []
        for idx in range(1, len(parts) - 1, 3):
            role, ts, body = parts[idx], parts[idx + 1], parts[idx + 2]
            dt = _parse_md_ts(ts)
            body = body.strip().rstrip('-').strip()
            if dt and body:
                msgs.append({'dt': dt,
                             'role': 'human' if role == 'Human' else 'assistant',
                             'text': body})
        if msgs:
            out.append({'id': '', 'title': title, 'messages': msgs})
    return out


def load(path):
    return load_json(path) if path.lower().endswith('.json') else load_markdown(path)


# ------------------------------------------------------------- 按天转置

def slice_by_day(conversations, include_assistant=False):
    """conversation-major -> date-major。

    返回 {'YYYY-MM-DD': {'date', 'threads': [...], 'user_chars', 'n_user_msgs'}}
    threads 里每条是当天在某个窗口里的片段，按当天第一条消息的时间排序。
    """
    days = defaultdict(lambda: defaultdict(list))
    meta = {}
    for c in conversations:
        key = c['id'] or c['title']
        meta[key] = c['title']
        for m in c['messages']:
            if m['role'] != 'human' and not include_assistant:
                continue
            days[_bucket_date(m['dt'])][key].append(m)

    out = {}
    for date, threads in sorted(days.items()):
        items = []
        for key, msgs in threads.items():
            msgs.sort(key=lambda m: m['dt'])
            items.append({
                'conversation': meta[key],
                'conversation_id': key if len(key) > 20 else '',
                'first': msgs[0]['dt'].astimezone(LOCAL_TZ).strftime('%H:%M'),
                'last': msgs[-1]['dt'].astimezone(LOCAL_TZ).strftime('%H:%M'),
                'n_msgs': len(msgs),
                'chars': sum(len(m['text']) for m in msgs),
                'messages': [{'time': m['dt'].astimezone(LOCAL_TZ).strftime('%H:%M'),
                              'role': m['role'], 'text': m['text']} for m in msgs],
            })
        items.sort(key=lambda t: t['first'])
        out[date] = {
            'date': date,
            'n_threads': len(items),
            'n_user_msgs': sum(t['n_msgs'] for t in items),
            'user_chars': sum(t['chars'] for t in items),
            'threads': items,
        }
    return out


def render_day(day, char_budget=60000):
    """把一天渲染成喂给模型的纯文本。按窗口分块，超预算时截长消息。

    接着前面聊的窗口会先出一段【前情】——那是背景，prompt 里明确要求不能当成今天发生的事写。
    """
    head = (f"日期：{day['date']}\n"
            f"当天在 {day['n_threads']} 个对话窗口里发了 {day['n_user_msgs']} 条消息"
            f"（共 {day['user_chars']:,} 字）\n")
    budget = max(char_budget - len(head), 1000)
    per = max(budget // max(day['n_threads'], 1), 400)
    blocks = []
    for t in day['threads']:
        lines = [f"\n【窗口】{t['conversation']}  {t['first']}–{t['last']}  {t['n_msgs']}条"]
        p = t.get('prior')
        if p:
            lines.append(f"  【前情｜背景，不是今天发生的】这条线从 {p['since']} 开始，"
                         f"今天之前已聊 {p['days']} 天 {p['n']} 条。")
            lines.append(f"    最初问的是：{p['opening']}")
            if p.get('recent'):
                lines.append("    今天之前最后聊到：")
                for r in p['recent']:
                    lines.append(f"      [{r['date']} {r['hhmm']}] {r['text']}")
            lines.append("  ——以上是背景，以下才是今天——")
        used = 0
        for m in t['messages']:
            txt = m['text']
            if used + len(txt) > per:
                txt = txt[:max(per - used, 0)]
            if not txt:
                lines.append('  …（本窗口后续省略）')
                break
            lines.append(f"  [{m['time']}] {txt}")
            used += len(txt)
        blocks.append('\n'.join(lines))
    return head + '\n'.join(blocks)


if __name__ == '__main__':
    import sys
    src = sys.argv[1]
    convs = load(src)
    days = slice_by_day(convs)
    tot = sum(d['user_chars'] for d in days.values())
    print(f'{len(convs)} 个对话 -> {len(days)} 天，{tot:,} 字')
    multi = [d for d in days.values() if d['n_threads'] > 1]
    print(f'跨窗口的天数：{len(multi)} / {len(days)} '
          f'({len(multi) / max(len(days), 1) * 100:.0f}%)')
    busiest = sorted(days.values(), key=lambda d: -d['user_chars'])[:10]
    print('\n字数最多的 10 天：')
    for d in busiest:
        print(f"  {d['date']}  {d['user_chars']:>8,} 字  "
              f"{d['n_threads']:>2} 个窗口  {d['n_user_msgs']:>3} 条")
    out = sys.argv[2] if len(sys.argv) > 2 else 'days.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(days, f, ensure_ascii=False)
    print(f'\n-> {out}')
