"""第一次用（新 clone 下来的电脑）：「开始用」页（2026-10-10，给不写代码的人装）。

    status()       → 每一步做没做、还要不要显示这一页
    save(fields)   → 把页面上填的 key 写进 ephemeris/.env（只认下面 FIELDS 里的几个）

什么时候出现：没有 Gemini key，或者库里一条聊天都还没有。两样都有了就不再出现 —— 作者自己的电脑从来看不到它。
填的值只写进本机的 .env（.gitignore 挡着），接口只回「填了没有」，不把值传回页面。
"""
import os
import re

import chrome
import claude_sync
import daily
import index as idx
import llm
import store

ENV = os.path.join(idx.HERE, '.env')
FIELDS = ('GEMINI_API_KEY', 'CLAUDE_SESSION_KEY', 'DIARY_NAME')


def _chrome():
    try:
        chrome.find_chrome()
        return True
    except RuntimeError:
        return False


def status():
    msgs = store.one('SELECT COUNT(*) n FROM messages')['n']
    gemini = llm.check() is None
    sync = daily.status()
    return {
        'need': not (gemini and msgs),
        'gemini': gemini,
        'claude': bool(idx.env('CLAUDE_SESSION_KEY')),
        'chrome': _chrome(),
        'name': idx.env('DIARY_NAME'),
        'msgs': msgs,
        'sync': sync['job'],
        'last_sync': claude_sync.last_run(),
    }


def verify_gemini(api_key=None):
    """Gemini key 能不能用：读一下模型信息（不花钱）。能用 None，不能用给一句原因。不给 key 就验存着的那个。"""
    try:
        client = llm._client(api_key)        # 先拿住：链式调用的话 client 会被回收、连接被关
        client.models.get(model=llm.MODELS[0])
        return None
    except Exception as e:                                   # noqa: BLE001
        msg = str(e)
        if 'API_KEY_INVALID' in msg or 'API key not valid' in msg:
            return '这个 key 不对，Google 说它无效。回去重新复制一遍（整串，不要带空格）'
        return f'连不上 Google（{type(e).__name__}）。网络要能打开 Google 才行'


def save(fields):
    """只改给的这几行，.env 里别的行原样留着；没有 .env 就新建一个（只有自己能读）。"""
    updates = {k: re.sub(r'[\r\n]', '', str(v)).strip() for k, v in (fields or {}).items() if k in FIELDS and v is not None}
    if not updates:
        return status()
    # Gemini key 先验再存：错的 key 存进去，第一步就显示成「好了」，可日记一篇都写不出来
    bad = verify_gemini(updates['GEMINI_API_KEY']) if updates.get('GEMINI_API_KEY') else None
    if bad:
        return {**status(), 'gemini_error': bad}
    lines = []
    if os.path.exists(ENV):
        with open(ENV, encoding='utf-8') as f:
            lines = f.read().splitlines()
    done = set()
    for i, line in enumerate(lines):
        k = line.split('=', 1)[0].strip()
        if k in updates:
            lines[i] = f'{k}={updates[k]}'
            done.add(k)
    lines += [f'{k}={v}' for k, v in updates.items() if k not in done]
    with open(ENV, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    os.chmod(ENV, 0o600)
    return status()
