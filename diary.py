"""把切好的一天变成一篇日记。

模型调用复用 getAudio/reflect.py 的那一套（默认 gemini-3.5-flash-lite，
配了 REFLECT_OPENROUTER_MODEL 就走 OpenRouter），叙事口径也沿用「回顾面板约定」：
直白，先说主要在弄什么，不要比喻和抒情。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'getAudio'))

from slice import render_day  # noqa: E402
import index as idx  # noqa: E402

CACHE_DIR = os.environ.get('DIARY_CACHE') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '.cache')

PROMPT = """下面是 __NAME__ 在 {date} 这一天，跨 {n_threads} 个不同对话窗口里发给 AI 的全部消息。
你的任务是据此写一篇当天的日记。

写作要求（严格遵守）：
- 直白。第一句说清这天主要在弄什么，然后第二、第三块。不要比喻，不要抒情，不要"仿佛""沉浸""思绪万千"这类修辞。
- 用第一人称「我」写，像他自己记的，不是旁观者报告。
- 只写材料里有的。不要评价他，不要给建议，不要安慰。没聊到的不要补。
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


def _call_model(prompt):
    from reflect import _call_model as reflect_call
    return reflect_call(prompt)


def _fix_backslashes(s):
    """把不是合法 JSON 转义的反斜杠补成 \\。

    模型写数学时会直接吐 LaTeX：narrative 里出现 `$\\sqrt{3}$`，而 `\\s` 不是合法的
    JSON 转义，整串就解析不了 —— 返回的内容本身是好的、完整的，只卡在这一个字符。
    2026-09-21 实测：199 天里 3 天栽在这，全是数学重的日子。
    """
    return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)


def _extract_json(text):
    if not text:
        return None
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    m = re.search(r'\{.*\}', text, re.S)
    for cand in (text, m.group(0) if m else None):
        if not cand:
            continue
        for attempt in (cand, _fix_backslashes(cand)):      # 先原样，不行再修反斜杠
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    return None


def _cache_path(date):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f'{date}.json')


def _render_prev(prev, date):
    if not prev:
        return '（没有——这是第一篇。）'
    from datetime import date as D
    gap = (D.fromisoformat(date) - D.fromisoformat(prev['date'])).days
    when = '昨天' if gap == 1 else f'{gap} 天前'
    loops = prev.get('open_loops') or '[]'
    try:
        loops = json.loads(loops) if isinstance(loops, str) else loops
    except ValueError:
        loops = []
    out = [f"{prev['date']}（{when}）　{prev.get('headline') or ''}", prev.get('narrative') or '']
    if loops:
        out.append('当时没做完的：' + '；'.join(loops))
    return '\n'.join(out)


def generate(day, refresh=False, char_budget=60000, prev=None, ledger=''):
    """生成一天的日记。命中缓存就直接返回。

    prev 是上一篇日记（diaries 表的一行），ledger 是 ledger.render_for_prompt 的输出。
    """
    path = _cache_path(day['date'])
    if not refresh and os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    body = render_day(day, char_budget=char_budget)
    prompt = PROMPT.format(date=day['date'], n_threads=day['n_threads'], body=body,
                           prev=_render_prev(prev, day['date']),
                           ledger=ledger or '（账本还是空的。）')
    raw = _call_model(prompt)
    parsed = _extract_json(raw)
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
