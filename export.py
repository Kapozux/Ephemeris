"""导出（2026-10-07）：两种，用途不同。

    archive(dir)          原文归档：每个对话一个 Markdown，按「来源/年-月」分文件夹，带总目录。不调模型。
                          用来自己翻、搜；要接着某段旧对话聊，就把那一个文件拖给新 AI。
    handoff(dir, skip)    给新账号的交接包：
                            关于我.md        一页（模型写一次，约 6 万字进去，不到 $0.01）—— 贴进新账号的个人偏好 / 项目说明
                            过去一年.md      按月，每天一行（日记标题拼的，不调模型）
                            正在进行的事.md  主题账本里最近还在推进的线、做到哪（不调模型）
                            怎么用.md
                          skip：要跳过的话题关键词（比如「感情」）—— 主题名、日记标题里带它的不进，写「关于我」时也不提

为什么不直接把聊天全倒进新账号：全部约 7500 万字，任何 AI 一个窗口只读得下几十万字；
新账号也不「记得」旧聊天，只知道塞给它的东西。能带过去的是浓缩过的：日记 + 主题账本。

后台任务（jobs）：export-archive / export-handoff；导出到 ~/Downloads/Ephemeris导出-日期/。
"""
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta

import days
import jobs
import ledger
import llm
import store

NAMES = {'claude': 'Claude', 'gemini': 'Gemini'}


def out_dir():
    return os.path.join(os.path.expanduser('~/Downloads'), f'Ephemeris导出-{days.today()}')


def _safe(s, n=40):
    s = re.sub(r'[\\/:*?"<>|\s]+', ' ', (s or '').strip())[:n].strip(' .')
    return s or '无标题'


# ------------------------------------------------------------------ 原文归档

def _fresh(root, name):
    """今天已经导出过同名文件夹：新的一份带上时分，旧的不碰（标题改过的话，覆盖会留下两个文件）。"""
    path = os.path.join(root, name)
    if os.path.exists(path):
        path = os.path.join(root, f'{name}-{datetime.now():%H%M%S}')
    os.makedirs(path)
    return path


def conv_md(c, msgs=None):
    """一个窗口 → (文件名, Markdown)。c 至少有 id / source / title；msgs 不给就从库里读。
    这里是唯一的格式：单个窗口的「导出 md」和整份原文归档都用它。"""
    msgs = msgs if msgs is not None else store.q(
        'SELECT date, hhmm, role, text FROM messages WHERE conv_id=? ORDER BY ts, id', c['id'])
    src = NAMES.get(c['source'], c['source'] or '其他')
    first = f"{msgs[0]['date']} {msgs[0]['hhmm']}" if msgs else ''
    last = f"{msgs[-1]['date']} {msgs[-1]['hhmm']}" if msgs else ''
    out = [f"# {c['title'] or '无标题'}", '', f"{src} · {first} → {last} · {len(msgs)} 条 · id {c['id']}", '']
    for m in msgs:
        who = '我' if m['role'] == 'human' else src
        out += [f"**{who}**　{m['date']} {m['hhmm']}", '', m['text'] or '', '', '---', '']
    return f"{first[:10]}_{_safe(c['title'])}_{c['id'][:8]}.md", '\n'.join(out)


def one(conv_id):
    """单个窗口的导出：(文件名, Markdown)，没有这个窗口 → None。"""
    c = store.one('SELECT id, source, title FROM conversations WHERE id=?', conv_id)
    return conv_md(c) if c else None


def archive(job, root):
    """每个对话一个 .md：标题、来源、时间跨度，然后按时间一条条（我 / AI）。"""
    root = _fresh(root, '原文')
    convs = store.q("""SELECT c.id, c.source, c.title, MIN(m.ts) first, COUNT(*) n
                         FROM conversations c JOIN messages m ON m.conv_id = c.id
                        GROUP BY c.id ORDER BY first""")
    job['total'], job['done'] = len(convs), 0
    index = defaultdict(list)
    for c in convs:
        src = NAMES.get(c['source'], c['source'] or '其他')
        month = (c['first'] or '')[:7] or '未知'
        name, text = conv_md(c)
        rel = os.path.join(src, month, name)
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        index[(src, month)].append((c['first'][:10], c['title'] or '无标题', rel, c['n']))
        job['done'] += 1
    lines = ['# 全部聊天', '', f'{len(convs)} 个对话，导出于 {datetime.now():%Y-%m-%d %H:%M}。每个对话一个文件；'
             '要接着某段旧对话聊，把那个文件拖给 AI 就行。', '']
    for (src, month) in sorted(index):
        lines += [f'## {src} · {month}', '']
        lines += [f"- {d}　[{_safe(t, 60)}](<{rel}>)　{n} 条" for d, t, rel, n in index[(src, month)]]
        lines.append('')
    with open(os.path.join(root, '目录.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    job['path'] = root
    return root


# ------------------------------------------------------------------ 交接包

ABOUT_PROMPT = """下面是一个人过去一年的日记（AI 根据这个人每天跟 AI 的聊天写的）和长期在做的事。
这个人换了一个新的 AI 账号，新 AI 不认识这个人。请替这个人写一页「关于我」，会贴进新 AI 的个人偏好里。

要求：
- 用第一人称「我」，直白，像本人写的。1500–2500 字。
- 分四段，用小标题：「我是谁」「最近在做的事」「我做事和说话的习惯」「希望你怎么配合我」。
- 只写材料里看得出来的，别编；具体的东西（在读什么、做什么项目、用什么工具）要写出来，别泛泛而谈。
- 「希望你怎么配合我」从这个人跟 AI 打交道的方式里归纳（比如喜欢直接给结论、讨厌某些写法），看不出来就少写。
{skip}
- 只输出这一页的正文（Markdown），不要前言后语。

【长期在做的事】
{themes}

【日记（近的在后）】
{diaries}
"""


def _hit(text, skip):
    return any(k and k in (text or '') for k in skip)


def handoff(job, root, skip=()):
    root = _fresh(root, '交接包')
    skip = [k.strip() for k in skip if k.strip()]
    job['stage'] = '时间线'
    diaries = [d for d in store.q('SELECT date, headline, narrative FROM diaries ORDER BY date')
               if not _hit(d['headline'], skip)]
    by_month = defaultdict(list)
    for d in diaries:
        by_month[d['date'][:7]].append(d)
    tl = ['# 过去这段时间', '', '每天一行，是 AI 根据我那天跟 AI 的聊天写的日记标题。', '']
    for m in sorted(by_month):
        tl += [f'## {m}', ''] + [f"- {d['date'][5:]}　{d['headline']}" for d in by_month[m]] + ['']
    _write(root, '过去一年.md', tl)

    job['stage'] = '正在进行的事'
    since = (datetime.fromisoformat(days.today()) - timedelta(days=60)).date().isoformat()
    themes = [t for t in ledger.all_themes() if t['last'] >= since and t['n_days'] >= 2 and not _hit(t['name'], skip)]
    th = ['# 正在进行的事', '', '最近两个月还在推进、聊过两天以上的事，做到哪一步（最后一次的进展）。', '']
    for t in themes:
        th += [f"## {t['name']}", '', f"{t['first']} 开始，前后 {t['n_days']} 天，最近一次 {t['last']}。", '',
               f"做到：{t['state']}", '']
    _write(root, '正在进行的事.md', th)

    job['stage'] = '写「关于我」'
    recent = diaries[-120:]
    text = llm.ask(ABOUT_PROMPT.format(
        skip=f"- 不要写任何跟这些有关的内容：{'、'.join(skip)}。" if skip else '',
        themes='\n'.join(f"- {t['name']}（{t['n_days']} 天）：{t['state']}" for t in themes[:40]) or '（无）',
        diaries='\n\n'.join(f"{d['date']}　{d['headline']}\n{(d['narrative'] or '')[:400]}" for d in recent)))
    if not text:
        raise RuntimeError('「关于我」没写出来（模型没返回），另外三个文件已经导出了')
    _write(root, '关于我.md', [text.strip()])

    _write(root, '怎么用.md', [
        '# 怎么用这个交接包', '',
        '1. **先看一遍、删改**：「关于我」是 AI 起草的，里面有私事，留哪些你说了算。',
        '2. **「关于我」**：贴进新账号的个人偏好（Claude：设置 → 个人偏好；也可以放进某个项目的项目说明）。',
        '3. **「过去一年」「正在进行的事」**：建一个项目，作为项目知识上传。新 AI 需要背景时会去翻。',
        '4. **要接着某段旧对话聊**：去「原文」文件夹找那个对话的 .md，拖进新窗口。',
        '', f'导出于 {datetime.now():%Y-%m-%d %H:%M}' + (f'；跳过了：{"、".join(skip)}' if skip else '') + '。'])
    job['path'] = root
    return root


def _write(root, name, lines):
    with open(os.path.join(root, name), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


# ------------------------------------------------------------------ 后台任务

IDLE = {'stage': None, 'done': 0, 'total': 0, 'path': None}


def start(kind, skip=()):
    name = f'export-{kind}'
    fn = archive if kind == 'archive' else handoff
    args = (out_dir(),) if kind == 'archive' else (out_dir(), tuple(skip))
    jobs.start(name, fn, *args, stage='导出原文' if kind == 'archive' else '准备', done=0, total=0, path=None)
    return status()


def status():
    return {k: jobs.get(f'export-{k}', **IDLE) for k in ('archive', 'handoff')}


def reveal(kind):
    """在 Finder 里打开上次导出的文件夹（路径只用后台任务自己记的，不收前端传的）。"""
    path = jobs.get(f'export-{kind}', path=None)['path']
    if path and os.path.isdir(path):
        subprocess.run(['open', path], check=False)
        return True
    return False
