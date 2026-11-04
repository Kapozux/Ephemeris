"""第一次用（新 clone 下来的电脑）：「开始用」页（2026-10-10，给不写代码的人装；10-14 改成能挑用哪家模型）。

    status()       → 每一步做没做、还要不要显示这一页、哪几家模型配好了
    save(fields)   → 把页面上填的 key 写进 ephemeris/.env（只认下面 FIELDS 里的几个），key 存之前先验
    verify(provider, key) → 这把 key 能不能用（只读模型列表，不花钱）。能用 None，不能用给一句原因

模型挑一家就够（2026-10-14：同学的 Windows 电脑只有 DeepSeek，「新手引导弄多个选项」）：
  deepseek    DeepSeek 官方 API（推荐给只有 DeepSeek 的人；写日记、做卡、问都走它，深挖它自己调工具查）
  claude      本机装好、登录了的 Claude Code（用订阅额度，不用 key）
  gemini      Google AI Studio 的 key
  openrouter  OpenRouter 的 key（上面的 DeepSeek / Gemini 都能走）
  dashscope   阿里云百炼（通义）的 key（走它上面的 DeepSeek）
选的那家记在 .env 的 EPHEMERIS_PROVIDER：选了别的，本机就算装着 Claude Code 也不去碰它（llm.claude_on）。

什么时候出现：一家能用的模型都没有，或者库里一条聊天都还没有。两样都有了就不再出现 —— 作者自己的电脑从来看不到它。
填的值只写进本机的 .env（.gitignore 挡着），接口只回「填了没有」，不把值传回页面。
"""
import os
import re

import chrome
from sources import claude_sync
import daily
import index as idx
import llm
import providers as prov                 # 「模型」那栏（11-03）；本模块自己有个 providers() 函数
import store

ENV = os.path.join(idx.HERE, '.env')
PROVIDERS = ('deepseek', 'claude', 'gemini', 'openrouter', 'dashscope')
KEY_OF = {'deepseek': 'DEEPSEEK_API_KEY', 'gemini': 'GEMINI_API_KEY', 'openrouter': 'OPENROUTER_API_KEY',
          'dashscope': 'DASHSCOPE_API_KEY'}
FIELDS = tuple(dict.fromkeys(tuple(KEY_OF.values()) + ('CLAUDE_SESSION_KEY', 'DIARY_NAME', 'EPHEMERIS_PROVIDER', 'EPHEMERIS_LANG')
                             + prov.env_names()))     # 11-03：设置里「模型」那栏（各家的 key、自定义地址、三个角色）


def _chrome():
    """自动下载要起一个真浏览器：Chrome、Edge、Brave 都行（chrome.CHROME_CANDIDATES）。"""
    try:
        chrome.find_chrome()
        return True
    except RuntimeError:
        return False


def providers():
    """每家配没配好：有 key（存之前验过）/ 本机有 claude。"""
    return {'deepseek': bool(llm.key('DEEPSEEK_API_KEY', 'deepseek_key')),
            'claude': bool(llm._claude_bin()),
            'gemini': bool(llm.key('GEMINI_API_KEY', 'gemini_key')),
            'openrouter': bool(llm.openrouter_key()),
            'dashscope': bool(llm.key('DASHSCOPE_API_KEY', 'dashscope_key'))}


def chosen():
    """选的是哪家：.env 里记着的；没记就猜一个配好了的（都没有就空）。"""
    own = (idx.env('EPHEMERIS_PROVIDER') or '').strip().lower()
    if own in PROVIDERS:
        return own
    have = providers()
    return next((p for p in PROVIDERS if have[p]), '')


def status():
    msgs = store.one('SELECT COUNT(*) n FROM messages')['n']
    ready = llm.check() is None
    sync = daily.status()
    return {
        'need': not (ready and msgs),
        'model': ready,                       # 有一家能写日记了
        'gemini': ready,                      # 老名字（设置栏以前看它）
        'provider': chosen(),
        'providers': providers(),
        'chat': llm.chat_backend(),
        'claude': bool(idx.env('CLAUDE_SESSION_KEY')),     # claude.ai 的 sessionKey（自动下载用），不是 Claude Code
        'chrome': _chrome(),
        'name': idx.who() if store.DEMO else idx.env('DIARY_NAME'),
        'lang': idx.lang(),
        'msgs': msgs,
        'sync': sync['job'],
        'last_sync': claude_sync.last_run(),
        'models': prov.summary(),
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


# 读模型列表 / key 信息的地址：带上 key 请求一次，200 就是能用（不花钱）
_CHECK = {'deepseek': ('https://api.deepseek.com/models', 'DeepSeek'),
          'openrouter': ('https://openrouter.ai/api/v1/key', 'OpenRouter'),
          'dashscope': ('https://dashscope.aliyuncs.com/compatible-mode/v1/models', '阿里云百炼')}


def verify(provider, api_key):
    if provider == 'gemini':
        return verify_gemini(api_key)
    url, name = _CHECK[provider]
    import requests
    try:
        r = requests.get(url, timeout=20, headers={'Authorization': f'Bearer {api_key}'})
    except Exception as e:                                   # noqa: BLE001
        return f'连不上 {name}（{type(e).__name__}）。看看网络，或者过一会儿再试'
    if r.status_code == 200:
        return None
    if r.status_code in (401, 403):
        return f'这个 key 不对，{name} 说它无效。回去重新复制一遍（整串，不要带空格）'
    if r.status_code == 402:
        return f'{name} 说余额不足：先去充值，再回来保存'
    return f'{name} 返回 {r.status_code}，过一会儿再试'


def verify_preset(name, api_key, base=None):
    """providers 里那几家（11-03）的 key：带着它读一次 /models（不花钱）。能用 None，不能用给一句原因。"""
    import requests
    label = prov.PRESETS[name]['label']
    url = (base or prov.base(name)).rstrip('/')
    if not url:
        return '先填自定义的接口地址（写到 /v1 为止）'
    try:
        r = requests.get(url + '/models', timeout=20, headers={'Authorization': f'Bearer {api_key}'})
    except Exception as e:                                   # noqa: BLE001
        return f'连不上 {label}（{type(e).__name__}）。看看地址和网络'
    if r.status_code == 200:
        return None
    if r.status_code in (401, 403):
        return f'这个 key 不对，{label} 说它无效'
    if r.status_code == 404:
        return f'{label} 那个地址下面没有 /models：地址要写到 /v1 为止（多了少了都会这样）'
    return f'{label} 返回 {r.status_code}，过一会儿再试'


def _bad_pick(raw):
    name, _, model = (raw or '').partition(':')
    if not raw:
        return None                                          # 清空 = 不指定
    if name not in prov.PRESETS or not model.strip():
        return '模型要写成「接口名:模型」'
    return None


def save(fields):
    """只改给的这几行，.env 里别的行原样留着；没有 .env 就新建一个（只有自己能读）。"""
    updates = {k: re.sub(r'[\r\n]', '', str(v)).strip() for k, v in (fields or {}).items() if k in FIELDS and v is not None}
    if 'EPHEMERIS_PROVIDER' in updates and updates['EPHEMERIS_PROVIDER'] not in PROVIDERS:
        return {**status(), 'key_error': '不认识这家模型'}
    if 'EPHEMERIS_LANG' in updates and updates['EPHEMERIS_LANG'] not in idx.LANGS:
        return {**status(), 'key_error': '不认识这个语言'}
    if not updates:
        return status()
    # key 先验再存：错的 key 存进去，第一步就显示成「好了」，可日记一篇都写不出来
    for provider, name in KEY_OF.items():
        if updates.get(name):
            bad = verify(provider, updates[name])
            if bad:
                return {**status(), 'key_error': bad, 'gemini_error': bad}
    for pname, p in prov.PRESETS.items():                  # 其余几家：读一次它的 /models
        if p['env'] in KEY_OF.values() or not updates.get(p['env']):
            continue
        bad = verify_preset(pname, updates[p['env']], updates.get('EPHEMERIS_CUSTOM_BASE_URL') if pname == 'custom' else None)
        if bad:
            return {**status(), 'key_error': bad}
    for r in prov.ROLES:
        bad = _bad_pick(updates.get(f'EPHEMERIS_MODEL_{r}'))
        if bad:
            return {**status(), 'key_error': bad}
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
    for k, v in updates.items():                             # Flask 的 app.run 启动时把 .env 读进了环境变量，idx.env 先看那里：一起改，不用重启
        if k in os.environ:
            os.environ[k] = v
    try:
        os.chmod(ENV, 0o600)                                 # Windows 上只是去掉只读位，不报错也没坏处
    except OSError:
        pass
    return status()
