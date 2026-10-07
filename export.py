"""导出（2026-10-07）。「设置 → 导出」两个按钮，外加每个窗口自己的「导出 .md」。

    one(conv_id)        单个窗口 → (文件名, Markdown)；跟完整导出里的是同一个格式（conv_md）
    full(dir)           完整导出，不调模型：
                          聊天原文/   每个窗口一个 .md，按「来源/年-月」分文件夹，带目录
                          日记/       每周一份：AI 写的日记、手写日记、打卡（跟备份的「每周/」同一份，backup.weeks_md）
                          日记图片/   手写日记里的图片
                          主题.md     主题账本：每条线哪天做到哪
    handoff(dir, skip)  给新账号的交接包：请 Claude Opus 4.6（llm.ask_strong）读一年的日记和主题账本，一次写出
                          关于我.md / 过去一年.md / 正在进行的事.md，再附 怎么用.md。约 $1，按实际收，结果里记着花了多少。
                        skip：要跳过的话题（比如「感情」）—— 标题或正文提到它的日记、提到它的主题整篇不发给模型，也叫模型别写

为什么不直接把聊天全倒进新账号：全部约 7500 万字，任何 AI 一个窗口只读得下几十万字；
新账号也不「记得」旧聊天，只知道塞给它的东西。能带过去的是浓缩过的：日记 + 主题账本。

后台任务（jobs）：export-full / export-handoff；导出到 ~/Downloads/Ephemeris导出-日期/，同一天再导另起一个文件夹，不覆盖。
"""
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta

import backup
import days
import jobs
import journal
import ledger
import llm
import store

NAMES = {'claude': 'Claude', 'gemini': 'Gemini'}
KINDS = ('full', 'handoff')


def out_dir():
    return os.path.join(os.path.expanduser('~/Downloads'), f'Ephemeris导出-{days.today()}')


def _safe(s, n=40):
    s = re.sub(r'[\\/:*?"<>|\s]+', ' ', (s or '').strip())[:n].strip(' .')
    return s or '无标题'


def _fresh(root, name):
    """今天已经导出过同名文件夹：新的一份带上时分，旧的不碰（标题改过的话，覆盖会留下两个文件）。"""
    path = os.path.join(root, name)
    if os.path.exists(path):
        path = os.path.join(root, f'{name}-{datetime.now():%H%M%S}')
    os.makedirs(path)
    return path


def _write(root, name, lines):
    with open(os.path.join(root, name), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


# ------------------------------------------------------------------ 单个窗口

def conv_md(c, msgs=None):
    """一个窗口 → (文件名, Markdown)。c 至少有 id / source / title；msgs 不给就从库里读。
    这里是唯一的格式：单个窗口的「导出 .md」和完整导出的「聊天原文」都用它。"""
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


# ------------------------------------------------------------------ 完整导出

def archive(job, root):
    """聊天原文：每个对话一个 .md，按「来源/年-月」分文件夹，加一个目录。root 要已经存在。返回对话数。"""
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
    _write(root, '目录.md', lines)
    return len(convs)


def themes_md():
    """主题账本整本：每条线一节，哪天做到哪。"""
    out = ['# 主题', '', '一件事聊了多少天、每天做到哪（每写一篇日记更新一次）。最近还在动的在前面。', '']
    for t in ledger.all_themes():
        out += [f"## {t['name']}", '', f"{t['first']} → {t['last']} · {t['n_days']} 天", '']
        out += [f"- {d['date']}　{d['note'] or ''}" for d in t['days']] + ['']
    return out


def full(job, root):
    base = _fresh(root, '完整导出')
    job['stage'] = '聊天原文'
    os.makedirs(os.path.join(base, '聊天原文'))
    n_convs = archive(job, os.path.join(base, '聊天原文'))
    job['stage'], job['done'], job['total'] = '日记', 0, 0
    weeks = backup.weeks_md()
    os.makedirs(os.path.join(base, '日记'))
    for name, md in weeks:
        with open(os.path.join(base, '日记', name), 'w', encoding='utf-8') as f:
            f.write(md)
    job['stage'] = '日记图片'
    n_img = backup.copy_new(journal.FILES_DIR, os.path.join(base, '日记图片'))
    job['stage'] = '主题'
    _write(base, '主题.md', themes_md())
    _write(base, '说明.md', [
        '# Ephemeris 完整导出', '', f'导出于 {datetime.now():%Y-%m-%d %H:%M}。全是 Markdown，任何编辑器都能打开。', '',
        f'- **聊天原文/**：{n_convs} 个对话，每个一个文件，先看「目录.md」。要接着某段旧对话聊，把那个文件拖给 AI。',
        f'- **日记/**：{len(weeks)} 周，每周一个文件：AI 写的日记、我手写的、打卡。',
        f'- **日记图片/**：手写日记里的 {n_img} 张图。',
        '- **主题.md**：每件长期在做的事，哪天做到哪。'])
    job['path'] = base
    return base


# ------------------------------------------------------------------ 交接包

HANDOFF_PROMPT = """下面是一个人过去一年的材料：每天的日记（AI 根据这个人当天跟 AI 的聊天写的，第一人称），
和「主题账本」（长期在做的事：哪天开始、最近一次、前后多少天、最近几次做到哪）。

这个人要换一个新的 AI 账号，新 AI 对这个人一无所知。请写三份文件，这个人看过、删改后交给新 AI：

1.「关于我」—— 贴进新 AI 的个人偏好，每次对话都会带着。第一人称「我」，1500–2500 字。
   小标题：我是谁 / 最近在做的事 / 我做事和说话的习惯 / 希望你怎么配合我。
   「希望你怎么配合我」从这个人跟 AI 打交道的方式里归纳（日记里常写到对 AI 的回答满意或不满意），看不出来就少写。
2.「过去一年」—— 作为项目知识上传，新 AI 要背景时去翻。按月，从早到晚，每月一个小标题，3–6 条要点：
   这个月主要在做什么、有什么转折、做成了什么。
3.「正在进行的事」—— 最近两个月还在推进的事，按重要程度排，最多 25 件，每件一个小标题：
   是什么、从什么时候开始、现在做到哪、下一步或卡在哪。

要求：
- 只写材料里有的，别编，别夸，别下判断。具体的东西要写出来：科目、项目名、工具、做到第几稿、分数。
- 直白，不用比喻，不抒情。
- 很私人的事（身体、心理、感情、家庭）：只在跟「怎么配合我」有关时简短提一句，不写细节。这个人上传前会自己删改。
{skip}
- 输出格式：严格按下面三段，每段以单独一行的分隔符开头；分隔符以外不要任何前言后语。段内用 Markdown，标题从 ## 开始。
=====关于我=====
=====过去一年=====
=====正在进行的事=====

【主题账本（最近还在动的在前）】
{themes}

【日记（从早到晚；最近三个月是全文，更早的只有开头）】
{diaries}
"""
SECTIONS = ('关于我', '过去一年', '正在进行的事')
FULL_DAYS = 90            # 最近这么多天的日记给全文，更早的只给标题 + 开头
OLD_CHARS = 150
PRICE_IN, PRICE_OUT, OUT_TOKENS = 5 / 1e6, 25 / 1e6, 10000      # Opus 4.6：每百万 token $5 / $25；输出约一万 token


def _hit(text, skip):
    return any(k and k in (text or '') for k in skip)


def _pick(skip=()):
    """要发给模型的日记和主题。跳过的话题按全文过滤：标题或正文里提到它的日记、名字或最近进展里提到它的主题，
    整篇不发（2026-10-07 走查：以前只看标题，正文里顺带提到的照样发出去了，只在提示词里叫模型别写）。"""
    skip = [k.strip() for k in skip if k and k.strip()]
    since = (datetime.fromisoformat(days.today()) - timedelta(days=60)).date().isoformat()
    diaries = store.q('SELECT date, headline, narrative FROM diaries ORDER BY date')
    keep_d = [d for d in diaries if not _hit((d['headline'] or '') + (d['narrative'] or ''), skip)]
    themes = [t for t in ledger.all_themes() if t['n_days'] >= 3 or t['last'] >= since][:200]
    keep_t = [t for t in themes
              if not _hit(t['name'] + ''.join(x['note'] or '' for x in t['days'][-3:]), skip)]
    return skip, keep_d, keep_t, {'diaries': len(keep_d), 'themes': len(keep_t),
                                  'dropped_diaries': len(diaries) - len(keep_d), 'dropped_themes': len(themes) - len(keep_t)}


def material(skip=()):
    """给模型的整段提示词。中文一个字大约一个 token，估价就按字数算。"""
    skip, diaries, themes, _ = _pick(skip)
    recent = (datetime.fromisoformat(days.today()) - timedelta(days=FULL_DAYS)).date().isoformat()
    lines = [f"{d['date']}　{d['headline']}\n" + ((d['narrative'] or '') if d['date'] >= recent else (d['narrative'] or '')[:OLD_CHARS])
             for d in diaries]
    th = [f"- {t['name']}：{t['first']} → {t['last']}，{t['n_days']} 天。最近："
          + '；'.join(f"{x['date'][5:]} {x['note']}" for x in t['days'][-3:] if x['note']) for t in themes]
    return HANDOFF_PROMPT.format(
        skip=f"- 不要写任何跟这些有关的内容：{'、'.join(skip)}。" if skip else '',
        themes='\n'.join(th) or '（无）', diaries='\n\n'.join(lines) or '（无）')


def estimate(skip=()):
    return round(len(material(skip)) * PRICE_IN + OUT_TOKENS * PRICE_OUT, 2)


def split(text):
    """模型的输出 → {段名: 正文}；分隔符缺了的段不在里面。"""
    parts = re.split(r'^\s*=+\s*(' + '|'.join(SECTIONS) + r')\s*=+\s*$', text or '', flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2) if parts[i + 1].strip()}


def handoff(job, root, skip=()):
    base = _fresh(root, '交接包')
    skip = [k.strip() for k in skip if k.strip()]
    job['stage'] = f'在请 {llm.strong_model().split("/")[-1]} 读一年的日记，两三分钟'
    text, cost = llm.ask_strong(material(skip))
    job['cost'] = cost
    got = split(text)
    if len(got) < len(SECTIONS):
        _write(base, '模型原文.md', [text])
        raise RuntimeError(f'模型没按格式分成三段（只认出 {"、".join(got) or "零段"}），原文存在「模型原文.md」')
    for name in SECTIONS:
        _write(base, f'{name}.md', [f'# {name}', '', got[name]])
    _write(base, '怎么用.md', [
        '# 怎么用这个交接包', '',
        '1. **先看一遍、删改**：三份都是 AI 起草的，里面有私事，留哪些你说了算。',
        '2. **关于我**：贴进新账号的个人偏好（Claude：设置 → 个人资料 → 偏好；也可以放进某个项目的项目说明）。',
        '3. **过去一年、正在进行的事**：建一个项目，作为项目知识上传。新 AI 要背景时会去翻。',
        '4. **要接着某段旧对话聊**：去「完整导出 / 聊天原文」找那个对话的 .md，拖进新窗口。',
        '', f'导出于 {datetime.now():%Y-%m-%d %H:%M}，{llm.strong_model()} 写的'
        + (f'，花了 ${cost:.2f}' if cost is not None else '')
        + (f'；跳过了：{"、".join(skip)}' if skip else '') + '。'])
    job['path'] = base
    return base


# ------------------------------------------------------------------ 后台任务

IDLE = {'stage': None, 'done': 0, 'total': 0, 'path': None, 'cost': None}


def start(kind, skip=()):
    if kind == 'full':
        jobs.start('export-full', full, out_dir(), stage='准备', done=0, total=0, path=None)
    elif kind == 'handoff':
        jobs.start('export-handoff', handoff, out_dir(), tuple(skip), stage='准备', path=None, cost=None)
    return status()


def status(skip=()):
    """两种导出的状态；交接包按现在填的「跳过」算：发几篇日记几个主题、去掉了几个、估多少钱。"""
    return {**{k: jobs.get(f'export-{k}', **IDLE) for k in KINDS},
            'model': llm.strong_model(), 'estimate': estimate(skip), 'sends': _pick(skip)[3]}


def reveal(kind):
    """在 Finder 里打开上次导出的文件夹（路径只用后台任务自己记的，不收前端传的）。"""
    path = jobs.get(f'export-{kind}', path=None)['path']
    if path and os.path.isdir(path):
        subprocess.run(['open', path], check=False)
        return True
    return False
