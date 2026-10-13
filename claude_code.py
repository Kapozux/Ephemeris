"""Claude Code 的对话也进 Ephemeris（2026-10-13 用户）：来源 cc，一个会话一个窗口。

    sync()      读 ~/.claude/projects/ 下改动过的会话，新消息进 messages（只增不删，规矩 6）

为什么、怎么放：用户在 Claude Code 里做项目（Ephemeris / Verbatim / 墨页……），里面也有他的决定、想法、骂人。
但它不是生活主线：日记的材料按来源分预算，AI 聊天 80%、Claude Code 10%、问 10%（slice.SOURCE_SHARE，用户 10-13 定的）。
存的：他打的字、Claude 回的文字（搜索、问 Claude 用得上）。不存：工具调用和输出（读文件、跑命令几万次）。
不进来的：
  · 子代理 / workflow 的对话（在 <项目>/<会话>/subagents/ 下，或标着 isSidechain）；
  · Ephemeris / Verbatim / 墨页自己调 `claude -p` 留下的会话（~/.ephemeris-claude 之类的目录）和临时目录里跑的；
  · 系统塞进来的「用户消息」：任务通知、isMeta、斜杠命令和它的输出、别的窗口发来的消息、续上一段的摘要、技能说明、
    「[Request interrupted]」、图片占位说明。
做证据卡只看他说的（cards 里跳过 cc 的 assistant）；日记只看他说的（跟别的来源一样）。
"""
import glob
import json
import os
import re
from datetime import datetime

import days
import index as idx
import store

SOURCE = 'cc'
ROOT = os.path.expanduser(os.environ.get('EPHEMERIS_CC_DIR') or '~/.claude/projects')
_SKIP_PROJECTS = ('-ephemeris-claude', '-verbatim-claude', '-moye-claude', '-private-tmp', '-tmp')
_INJECTED = ('<task-notification>', '<local-command-caveat>', '<command-name>', '<command-message>', '<local-command-stdout>',
             '<local-command-stderr>', '<system-reminder>', '<user-prompt-submit-hook>', '[Request interrupted',
             'Another Claude session sent a message', 'This session is being continued', 'Base directory for this skill',
             '# Workflow authoring reference', 'Continue from where you left off', 'Caveat: The messages below')
_IMAGE_NOTE = re.compile(r'^\[Image[^\]]*\]\s*$', re.M)


def _user_text(content):
    if isinstance(content, list):
        content = '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
    if not isinstance(content, str):
        return ''
    t = content.strip()
    if not t or t.startswith(_INJECTED):
        return ''
    return _IMAGE_NOTE.sub('', t).strip()


def _assistant_text(content):
    if not isinstance(content, list):
        return ''
    return '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text').strip()


def _files():
    for f in glob.glob(os.path.join(ROOT, '*', '*.jsonl')):           # 只到第二层：subagents/ 在更深一层
        if os.path.basename(os.path.dirname(f)).endswith(_SKIP_PROJECTS):
            continue
        yield f


def _parse(path):
    """→ (会话 id, 项目名, [(消息 id, 时间, role, 文字)])"""
    sid, cwd, out = os.path.splitext(os.path.basename(path))[0], '', []
    with open(path, encoding='utf-8', errors='ignore') as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get('isSidechain') or d.get('isMeta') or d.get('type') not in ('user', 'assistant'):
                continue
            cwd = cwd or d.get('cwd') or ''
            msg = d.get('message') or {}
            text = _user_text(msg.get('content')) if d['type'] == 'user' else _assistant_text(msg.get('content'))
            ts = d.get('timestamp')
            if text and ts and d.get('uuid'):
                out.append((f"{SOURCE}-{d['uuid']}", ts, 'human' if d['type'] == 'user' else 'assistant', text))
    return sid, os.path.basename(cwd.rstrip('/')) or '?', out


def sync(say=print):
    """读改动过的会话。返回 {'files': 读了几个, 'new': 新消息条数, 'days': 涉及哪些天}。"""
    seen = {r['path']: (r['size'], r['mtime']) for r in store.q('SELECT path, size, mtime FROM cc_files')}
    new, touched_days, n_files = 0, set(), 0
    for path in _files():
        st = os.stat(path)
        if seen.get(path) == (st.st_size, int(st.st_mtime)):
            continue
        n_files += 1
        sid, project, msgs = _parse(path)
        cid = f'{SOURCE}-{sid}'
        first = next((t for _, _, role, t in msgs if role == 'human'), '')
        with store.tx() as db:
            if msgs:
                db.execute("""INSERT INTO conversations (id, source, title, title_fallback, first_import, last_import)
                              VALUES (?,?,?,0,'claude-code','claude-code') ON CONFLICT(id) DO NOTHING""",
                           (cid, SOURCE, f"CC·{project}：{' '.join(first.split())[:40] or '（没有文字）'}"))
            for mid, ts, role, text in msgs:
                when = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                date, hhmm = days.bucket(when)
                # 不用 INSERT OR REPLACE：rowid 会变，全文索引对不上
                cur = db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING',
                                 (mid, cid, SOURCE, when.isoformat(), date, hhmm, role, text, len(text)))
                if cur.rowcount:
                    new += 1
                    if role == 'human':
                        touched_days.add(date)
            db.execute('INSERT OR REPLACE INTO cc_files (path, size, mtime, synced_at) VALUES (?,?,?,?)',
                       (path, st.st_size, int(st.st_mtime), datetime.now().isoformat(timespec='seconds')))
    if new:
        with store.tx() as db:
            idx._recount(db)
    if n_files and say:
        say(f'[claude-code] 读了 {n_files} 个会话，新消息 {new} 条，涉及 {len(touched_days)} 天')
    return {'files': n_files, 'new': new, 'days': sorted(touched_days)}


if __name__ == '__main__':
    print(sync())
