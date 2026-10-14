"""Codex（OpenAI 的编程助手）的对话也进 Ephemeris（2026-10-14）：来源 codex，一个会话一个窗口。

    sync()      读 ~/.codex/sessions/ 下改动过的会话文件（rollout-*.jsonl），新消息进 messages（只增不删，规矩 6）

一个会话一个 jsonl，每行一条带时间的记录。他说的取 event_msg / user_message（干净的原话；同一句在 response_item 里还有一份，
前面塞着环境说明，不用）；Codex 回的取 response_item / message / assistant（event_msg / agent_message 是同一段的副本）。
他附了文件时，Codex 会在原话前面拼一段「# Files mentioned by the user …## My request (for Codex):」，只留 My request 后面那段；
图片标签去掉；只剩一个「?」的（贴图没打字）不进。工具调用、推理、命令输出不进。
不是他的会话整份不进：thread_source = subagent（Codex 自动审批的 guardian，把他的对话历史整段贴进去再审）、
realtime_voice（语音转交的包装，内容是系统拼的）。
跟 Claude Code / Cursor 一样算「写代码」：日记材料里写代码的加起来只占 10%（slice.CODING），做证据卡只看他说的。
"""
import glob
import json
import os
import re
from datetime import datetime

import days
import index as idx
import store

SOURCE = 'codex'
ROOT = os.path.expanduser(os.environ.get('EPHEMERIS_CODEX_DIR') or '~/.codex')
_REQUEST = re.compile(r'^##\s*My request(?: for Codex)?:\s*$', re.M)
_NOT_HIS = ('subagent', 'realtime_voice')
_IMAGE = re.compile(r'<image\b[^>]*>(?:\s*</image>)?')


def _user_text(text):
    t = text or ''
    m = None
    for m in _REQUEST.finditer(t):
        pass
    if m:                                                # 附了文件：只留他自己打的那段
        t = t[m.end():]
    t = _IMAGE.sub('', t).strip()
    return '' if t in ('', '?', '？') else t


def _assistant_text(content):
    if not isinstance(content, list):
        return ''
    return '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'output_text').strip()


def _files():
    for sub in ('sessions', 'archived_sessions'):
        yield from glob.glob(os.path.join(ROOT, sub, '**', 'rollout-*.jsonl'), recursive=True)


def _parse(path):
    """→ (会话 id, 项目名, [(消息 id, 时间, role, 文字)])。消息 id 用行号：jsonl 只往后加，行号不会变。"""
    sid, cwd, out, meta = '', '', [], None
    with open(path, encoding='utf-8', errors='ignore') as fh:
        for n, line in enumerate(fh):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            p = d.get('payload') or {}
            if d.get('type') == 'session_meta':
                if meta is None:
                    meta = p
                    if p.get('thread_source') in _NOT_HIS or isinstance(p.get('source'), dict):
                        return p.get('id') or '', '', []          # 子代理 / 语音包装：整份不要
                sid, cwd = sid or p.get('id') or '', cwd or p.get('cwd') or ''
                continue
            if d.get('type') == 'event_msg' and p.get('type') == 'user_message':
                role, text = 'human', _user_text(p.get('message'))
            elif d.get('type') == 'response_item' and p.get('type') == 'message' and p.get('role') == 'assistant':
                role, text = 'assistant', _assistant_text(p.get('content'))
            else:
                continue
            if text and d.get('timestamp'):
                out.append((n, d['timestamp'], role, text))
    sid = sid or os.path.splitext(os.path.basename(path))[0]
    return sid, os.path.basename(cwd.rstrip('/')) or '?', [(f'{SOURCE}-{sid}-{n}', ts, role, t) for n, ts, role, t in out]


def sync(say=print):
    """读改动过的会话。返回 {'files': 读了几个, 'new': 新消息条数, 'days': 涉及哪些天}。"""
    seen = {r['path']: (r['size'], r['mtime']) for r in store.q('SELECT path, size, mtime FROM cc_files')}   # 跟 Claude Code 共用
    new, touched, n_files = 0, set(), 0
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
                              VALUES (?,?,?,0,'codex','codex') ON CONFLICT(id) DO NOTHING""",
                           (cid, SOURCE, f"Codex·{project}：{' '.join(first.split())[:40] or '（没有文字）'}"))
            for mid, ts, role, text in msgs:
                when = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                date, hhmm = days.bucket(when)
                # 不用 INSERT OR REPLACE：rowid 会变，全文索引对不上
                cur = db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING',
                                 (mid, cid, SOURCE, when.isoformat(), date, hhmm, role, text, len(text)))
                if cur.rowcount:
                    new += 1
                    if role == 'human':
                        touched.add(date)
            db.execute('INSERT OR REPLACE INTO cc_files (path, size, mtime, synced_at) VALUES (?,?,?,?)',
                       (path, st.st_size, int(st.st_mtime), datetime.now().isoformat(timespec='seconds')))
    if new:
        with store.tx() as db:
            idx._recount(db)
    if n_files and say:
        say(f'[codex] 读了 {n_files} 个会话，新消息 {new} 条，涉及 {len(touched)} 天')
    return {'files': n_files, 'new': new, 'days': sorted(touched)}


if __name__ == '__main__':
    print(sync())
