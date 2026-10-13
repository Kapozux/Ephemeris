"""Ephemeris 调模型的唯一入口。

    ask(prompt)            → 文本或 None
    ask_json(prompt, want) → 解析好的 dict / list，或 None（会重试）
    parse_json(text, want) → 纯函数：从模型输出里抠 JSON（去代码块、修 LaTeX 反斜杠）
    status()               → 现在用哪个后端、能不能用、上次失败原因
    stray(text, source)    → 输出里冒出来、材料里没有的别的文字（ask 自动重来一次）
    ask_strong(prompt)     → (文本, 实际美元)：只给「交接包」用的大模型，Claude Opus 4.6（走 OpenRouter）。失败抛异常

为什么要有它（2026-10-02 体检）：
- 以前 3 个模块各自 `sys.path` 伸进 getAudio 借 `reflect._call_model`，8 个地方各写一遍「抠 JSON + 重试」。
- 借来的东西会被别人改掉：10-01 Verbatim 为了省钱，把 `reflect._call_model` 改成按内容分流到阿里云 DeepSeek，
  Ephemeris 的日记、打卡回话也就跟着悄悄换了服务商 —— 没人替 Ephemeris 做过这个决定。
- 调用失败只返回 None，上层不报错：哪天借的函数改名了，日记会一篇都写不出来却没人知道。

后端（.env 的 EPHEMERIS_LLM 选）：
  gemini   默认。Gemini flash-lite，不行退一档 2.5-flash。直接用 google-genai，key 是 .env 的 GEMINI_API_KEY。
  verbatim 跟着 Verbatim 的 reflect._call_model 走（含它的省钱分流）。旁边要有 Verbatim 的 getAudio/。

单独装的 Ephemeris（2026-10-10 起）不需要 Verbatim。旁边放着 Verbatim 的话（作者自己的电脑），
key 没填会去读它存的（只读它的 .env / settings.local.json，不 import 它的代码），花费也顺带记进它的账本。
测试可以 `use(假函数)` 换掉后端。
"""
import json
import os
import re
import sys
import threading
import time

import index as idx

HERE = os.path.dirname(os.path.abspath(__file__))
GETAUDIO = os.path.join(HERE, '..', 'getAudio')
MODELS = ('gemini-3.5-flash-lite', 'gemini-2.5-flash')

_override = None                 # 测试用：use(fn)
_state = {'last_error': None, 'calls': 0, 'failures': 0, 'stray': 0}
_lock = threading.Lock()


def _getaudio():
    if GETAUDIO not in sys.path:
        sys.path.append(GETAUDIO)        # 放在最后：别让 getAudio 的同名模块（app.py）盖掉自己的


def _borrowed(env_name, setting):
    """旁边 Verbatim 存的值：先看它的 .env，再看网页设置 settings.local.json。没有 Verbatim 就是空串。"""
    try:
        with open(os.path.join(GETAUDIO, '.env'), encoding='utf-8') as f:
            for line in f:
                if line.startswith(env_name + '='):
                    v = line.split('=', 1)[1].strip().strip('"').strip("'")
                    if v:
                        return v
    except OSError:
        pass
    try:
        with open(os.path.join(GETAUDIO, 'settings.local.json'), encoding='utf-8') as f:
            return (json.load(f).get(setting) or '').strip()
    except (OSError, ValueError):
        return ''


def key(env_name, setting):
    """一个凭证：Ephemeris 自己的（环境变量 / ephemeris/.env）优先，没有再借 Verbatim 的。"""
    return idx.env(env_name) or _borrowed(env_name, setting)


def _record(fn_name, *a):
    """旁边有 Verbatim：这一笔花费记进它的账本（usage.db）。没有就算了，记账失败也不影响结果。
    跑测试时不记（EPHEMERIS_NO_USAGE，tests/_env 设的）：10-12 测试里的假路线往真账本写过 29 条 fake。"""
    if not os.path.isdir(GETAUDIO) or os.environ.get('EPHEMERIS_NO_USAGE'):
        return
    try:
        _getaudio()
        import usage
        getattr(usage, fn_name)(*a)
    except Exception:                                        # noqa: BLE001
        pass


def backend():
    return (idx.env('EPHEMERIS_LLM') or 'gemini').strip().lower()


def _client(api_key=None):
    """api_key 不给就用存着的（「开始用」页验证新 key 时才传）。"""
    from google import genai
    from google.genai import types
    k = api_key or key('GEMINI_API_KEY', 'gemini_key')
    if not k:
        raise RuntimeError('没有 Gemini key：在 ephemeris/.env 里填 GEMINI_API_KEY（打开 Ephemeris 的「开始用」页有步骤）')
    base = key('GEMINI_BASE_URL', 'gemini_base_url') or None          # 要走代理的才填
    return genai.Client(api_key=k, http_options=types.HttpOptions(timeout=600_000, base_url=base))


def _gemini(prompt, model=None):
    client = _client()
    err = None
    for m in ((model,) if model else MODELS):
        try:
            resp = client.models.generate_content(model=m, contents=prompt)
        except Exception as e:                               # noqa: BLE001
            err = e
            continue
        _record('record_gemini', resp, m, 'ephemeris')
        text = (resp.text or '').strip()
        if text:
            return text
        err = RuntimeError(f'{m} 返回空（多半是安全过滤）')
    raise err or RuntimeError('没有可用的模型')


def _verbatim(prompt, model=None):
    _getaudio()
    from reflect import _call_model          # 借 Verbatim 的；它改了接口这里会当场报错，不会悄悄返回空
    return _call_model(prompt)


# ------------------------------------------------------------------ 省钱路由：不涉政的都给 DeepSeek V4 Flash
# 2026-10-12。照 Verbatim 的 llmroute.py：
# DeepSeek V4 Flash 每百万 token 约 $0.14 进 / $0.28 出，Gemini 3.1-flash-lite 是 $0.25 / $1.50、3.5-flash-lite $0.30 / $2.50。
#   · 时政词（繁简 + 英文，抄自 Verbatim）在整段提示词里出现 GUARD_MIN 次以上 → 只走 Gemini，不给国内服务商
#     （泛中国词「中国 / 上海 / 高考」不算：聊天里天天有，Verbatim 10-04 吃过这个亏）。
#     **提示词模板里别写词表里的词**，不然每次都被判成涉政（tests/test_cheap_route 会扫一遍）。
#     博主名（立党、马督工、夸克说、王局、王志安、睡前消息）和「体制内」不算：用户聊 Verbatim 开发、升学时天天提这几个名字，
#     9 月一个月「立党」出现 272 次，算进去的话几乎每块都被判涉政（10-12 实测 09-15 六块全走了 Gemini）。Verbatim 的 deepseek_guard 也是这么剔的。
#     「防火墙」「翻墙」「清零」也剔了：在这里多半是服务器端口、Clash 代理、代码里计数清零。词表漏掉的涉政内容有内容审核兜底（_MODERATED）。
#   · 路线按顺序：阿里云百炼 → DeepSeek 官方（.env 填了 DEEPSEEK_API_KEY 才有）→ OpenRouter 上的 DeepSeek；
#     一条报错就试下一条，全不行才回 Gemini。欠费 / key 错这种会连着失败的，那条停 30 分钟。
#   · 关掉：.env 里 EPHEMERIS_CHEAP=off。交接包（ask_strong）不归这里管。
GUARD_MIN = 2
PAUSE_S = 30 * 60
_POL_ZH = """中共 共产党 共產黨 习近平 習近平 习主席 習主席 总书记 總書記 毛泽东 毛澤東 邓小平 鄧小平 江泽民 江澤民
胡锦涛 胡錦濤 温家宝 溫家寶 李克强 李克強 王沪宁 王滬寧 党中央 黨中央 政治局 中南海 六四 天安门 天安門 八九民运
法轮功 法輪功 新疆 维吾尔 維吾爾 西藏 达赖 達賴 台独 台獨 臺獨 港独 港獨 反送中 民主化 极权 極權 独裁 獨裁 专制 專制
维权 維權 上访 上訪 言论审查 言論審查 内容审查 內容審查 网络审查 網絡審查 審查制度 审查制度 电影审查 電影審查 新闻审查 新聞審查
敏感词 敏感詞 文革 文化大革命 大跃进 大躍進 大饥荒 大饑荒
刘晓波 劉曉波 艾未未 李文亮 白纸运动 白紙運動 封控 人权 人權 言论自由 言論自由 政权 政權 颠覆国家 顛覆國家 颠覆政权 顛覆政權 维稳 維穩
国安 國安 统战 統戰 两岸 兩岸 解放军 解放軍 武统 武統 台海 臺海 战狼 戰狼 小粉红 小粉紅 五毛 润学 潤學 党媒 黨媒
官媒 新闻联播 新聞聯播 人民日报 人民日報 环球时报 環球時報 胡锡进 胡錫進 司马南 司馬南
墙国 牆國 赵家人 趙家人 中宣部 网信办 網信辦 宣传部 宣傳部
共匪 中华人民共和国 中華人民共和國 国务院 國務院 政协 政協""".split()
_POL_EN = [r'CCP', r'CPC', r'Chinese Communist', r'Communist Party', r'Xi Jinping', r'Tiananmen', r'Uy?gh?urs?',
           r'Xinjiang', r'Tibet(?:an)?', r'Dalai Lama', r'Falun Gong', r'Taiwan independence', r'Hong Kong protests?',
           r'Great Firewall', r'PRC', r'Chinese government', r'Beijing regime']
_POLITICAL = re.compile('|'.join(map(re.escape, _POL_ZH)) + r'|\b(?:' + '|'.join(_POL_EN) + r')\b')
_cheap_state = {'calls': 0, 'guarded': 0, 'fallback': 0, 'last_error': None}
_paused = {}                                     # 路线名 → 停到什么时候
_turn = [0]                                      # 轮到哪条路先上（几条路分着扛并发，2026-10-13）


def political(text):
    """命中的时政词（列表）。出现 GUARD_MIN 次以上就不给国内模型。"""
    return _POLITICAL.findall(text or '')


def cheap_on():
    """配了 DeepSeek（有 key、没关掉）。跟「现在有没有没被停的路」是两回事：路全被停了也算配了，
    这时 fallback=False 的调用直接当失败，不退到 Gemini。"""
    return (idx.env('EPHEMERIS_CHEAP') or 'on').strip().lower() not in ('off', '0', 'false', 'no') and bool(_cheap_routes(all_routes=True))


def _cheap_routes(all_routes=False):
    now = time.time()
    out = []
    ds = key('DASHSCOPE_API_KEY', 'dashscope_key')
    if ds:
        out.append(('aliyun', 'https://dashscope.aliyuncs.com/compatible-mode/v1', ds,
                    idx.env('EPHEMERIS_CHEAP_MODEL') or 'deepseek-v4-flash-0731', {'enable_thinking': False}))
    dk = key('DEEPSEEK_API_KEY', 'deepseek_key')
    if dk:
        out.append(('deepseek', 'https://api.deepseek.com/v1', dk, 'deepseek-v4-flash', {}))
    ok = openrouter_key()
    if ok:
        out.append(('openrouter', 'https://openrouter.ai/api/v1', ok, 'deepseek/deepseek-v4-flash',
                    {'reasoning': {'enabled': False}, 'usage': {'include': True}}))
    return out if all_routes else [r for r in out if _paused.get(r[0], 0) <= now]


# 只有这些才停这条路 30 分钟：会一直失败的（欠费、key 错、没这个模型）。
# 别的（SSL 断线、回复被截断、JSON 坏了、429、5xx、超时）都只是这一次 —— 10-12 全量做卡时把这些也当成要停，
# 两条路都被停掉，又因为「没路可走」直接退到 Gemini，多花了好几美元。
_HARD = ('Access denied', 'good standing', 'Arrearage', 'arrearage', 'InvalidApiKey', 'Invalid API', 'invalid_api_key',
         'Incorrect API key', 'model_not_found', 'does not exist', 'Insufficient', 'insufficient', '401:', '402:', '403:', '404:')


def _pause(name, err):
    s = str(err)
    if not any(k in s for k in _HARD):
        return
    _paused[name] = time.time() + PAUSE_S
    print(f'[llm] {name} 停 30 分钟：{s[:160]}', flush=True)


_MODERATED = ('data_inspection', 'DataInspection', 'inappropriate')


def _cheap(prompt, why=None):
    """走 DeepSeek，拿到文字就返回；涉政、没有路线、全失败返回 None（调用方接着用 Gemini）。
    why：传一个 dict 进来，会记下是不是被内容审核拦了（why['moderated']=True）—— 词表没认出来、审核却拦了的，
    也算涉政，该走 Gemini；跟限流不一样，重试多少次 DeepSeek 都不会收。"""
    if len(political(prompt)) >= GUARD_MIN:
        with _lock:
            _cheap_state['guarded'] += 1
        return None
    import requests
    routes = _cheap_routes()
    with _lock:
        i = _turn[0] % max(len(routes), 1)
        _turn[0] += 1
    for name, base, k, model, extra in routes[i:] + routes[:i]:     # 轮流先上，失败了再试别的
        try:
            r = requests.post(base + '/chat/completions', timeout=300, headers={'Authorization': f'Bearer {k}'},
                              json={'model': model, 'messages': [{'role': 'user', 'content': prompt}], **extra})
            if r.status_code != 200:
                raise RuntimeError(f'{r.status_code}: {r.text[:200]}')
            body = r.json()
            text = (((body.get('choices') or [{}])[0].get('message') or {}).get('content') or '').strip()
            if not text:
                raise RuntimeError('返回空')
            _record('record_openai', body, name, model, 'ephemeris')
            with _lock:
                _cheap_state['calls'] += 1
            return text
        except Exception as e:                               # noqa: BLE001
            with _lock:
                _cheap_state['last_error'] = f'{name}: {e}'[:200]
            if why is not None and any(k in str(e) for k in _MODERATED):
                why['moderated'] = True
            _pause(name, e)
    with _lock:
        _cheap_state['fallback'] += 1
    return None


def prices():
    """(每 token 进, 每 token 出) 美元：估价用。能走 DeepSeek 就按它算（涉政的那一小部分按 Gemini，忽略）。"""
    return (0.14 / 1e6, 0.28 / 1e6) if cheap_on() else (0.25 / 1e6, 1.50 / 1e6)


def _call(prompt, model=None, fallback=True):
    """fallback=False：不涉政、又有 DeepSeek 的路时，DeepSeek 失败就当失败，不回 Gemini。
    给证据卡这种大批量的用：并发高的时候限流一来，悄悄全回 Gemini 会贵 3～5 倍；失败的块下次再做就是了。
    例外：被内容审核拦了的照样回 Gemini（那是词表漏掉的涉政内容，重试也没用；10-12 全量做卡时遇到）。"""
    if _override:
        return _override(prompt)
    b = backend()
    if b == 'verbatim':
        return _verbatim(prompt, model)
    if b != 'gemini':
        raise RuntimeError(f'不认识的 EPHEMERIS_LLM：{b}（只有 gemini / verbatim）')
    if cheap_on():
        guarded = len(political(prompt)) >= GUARD_MIN
        why = {}
        text = _cheap(prompt, why)
        if text:
            return text
        if not guarded and not fallback and not why.get('moderated'):
            raise RuntimeError('DeepSeek 这次没拿到（限流 / 断线），按要求不回 Gemini')
    return _gemini(prompt, model)


# ------------------------------------------------------------------ 大模型（只有交接包用）
# 2026-10-07 用户要求：交接包（export.handoff）点一下请「类似 Claude Opus 4.6」的来写，一年一次，贵一点没关系。
# 这是唯一一处把日记发给 Gemini 以外的服务（CLAUDE.md 规矩 4 的例外）。key 跟 Verbatim 共用一把 OpenRouter 的。
STRONG_MODEL = 'anthropic/claude-opus-4.6'
OPENROUTER = 'https://openrouter.ai/api/v1/chat/completions'


def strong_model():
    return (idx.env('EPHEMERIS_STRONG_MODEL') or '').strip() or STRONG_MODEL


def openrouter_key():
    return key('OPENROUTER_API_KEY', 'openrouter_key')


def ask_strong(prompt, max_tokens=16000):
    """调一次大模型，返回 (文本, 这一笔实际多少美元 / None)。没 key、报错、返回空都抛异常（上层要让用户看到原因）。"""
    if _override:
        return _override(prompt), None
    import requests
    key = openrouter_key()
    if not key:
        raise RuntimeError('没有 OpenRouter key：在 ephemeris/.env 里填 OPENROUTER_API_KEY（只有交接包要用）')
    model = strong_model()
    r = requests.post(OPENROUTER, timeout=900, headers={'Authorization': f'Bearer {key}'},
                      json={'model': model, 'max_tokens': max_tokens, 'usage': {'include': True},
                            'messages': [{'role': 'user', 'content': prompt}]})
    if r.status_code != 200:
        raise RuntimeError(f'{model} 返回 {r.status_code}：{r.text[:200]}')
    body = r.json()
    _record('record_openai', body, 'openrouter', model, 'ephemeris')
    text = ((body.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
    if not text.strip():
        raise RuntimeError(f'{model} 返回空')
    cost = (body.get('usage') or {}).get('cost')
    return text.strip(), (float(cost) if cost is not None else None)


# 天城文、阿拉伯文、西里尔、泰文、希伯来、韩文、日文假名。中文日记里出现它们，多半是模型串了语言：
# 2026-10 查到两处 ——「SVM 분류기」（韩文「分类器」）、「खासकर 9月11日」（印地语「尤其」），材料里都没有。
_STRAY = re.compile(r'[\u0900-\u097F\u0600-\u06FF\u0400-\u04FF\u0E00-\u0E7F\u0590-\u05FF\uAC00-\uD7AF\u3040-\u30FF]+')


def stray(text, source=''):
    """输出里有、材料里却没有的「别的文字」片段。材料里本来就有的（日文歌名之类）不算。"""
    return [w for w in _STRAY.findall(text or '') if w not in (source or '')]


def ask(prompt, model=None, fallback=True):
    """调一次模型，返回文本；失败返回 None，原因记在 status() 里（界面能看到）。
    输出里冒出材料里没有的外文词：重来一次；还这样就照收（总比没有强），次数记在 status()['stray']。"""
    with _lock:
        _state['calls'] += 1
    try:
        text = _call(prompt, model, fallback)
    except Exception as e:                                   # noqa: BLE001
        with _lock:
            _state['failures'] += 1
            _state['last_error'] = f'{type(e).__name__}: {e}'[:300]
        return None
    if stray(text, prompt):
        with _lock:
            _state['stray'] += 1
        try:
            again = _call(prompt, model, fallback)
        except Exception:                                    # noqa: BLE001  重来失败就用第一次的
            again = None
        if again and not stray(again, prompt):
            text = again
    return (text or '').strip() or None


def _fix_backslashes(s):
    """把不是合法 JSON 转义的反斜杠补成 \\\\。

    模型写数学时会直接吐 LaTeX：`$\\sqrt{3}$` 里的 `\\s` 不是合法 JSON 转义，整串就解析不了 ——
    内容本身是好的，只卡在这一个字符（2026-09-21：199 天里 3 天栽在这，全是数学重的日子）。
    """
    return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)


def parse_json(text, want=None):
    """从模型输出里抠出 JSON。want=dict / list 时类型不对也算失败。抠不出来返回 None。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r'^```(?:json)?\s*', '', t)
    t = re.sub(r'\s*```$', '', t)
    cands = [t]
    for pat in ((r'\{.*\}',) if want is dict else (r'\[.*\]',) if want is list else (r'\{.*\}', r'\[.*\]')):
        m = re.search(pat, t, re.S)
        if m:
            cands.append(m.group(0))
    for c in cands:
        for attempt in (c, _fix_backslashes(c)):        # 先原样，不行再修反斜杠
            try:
                out = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if want is None or isinstance(out, want):
                return out
    return None


def ask_json(prompt, want=dict, tries=2, model=None, ok=None, fallback=True):
    """调模型并解析 JSON；解析失败或 ok(结果) 不满意就重来，最多 tries 次。"""
    for _ in range(tries):
        out = parse_json(ask(prompt, model, fallback), want)
        if out is not None and (ok is None or ok(out)):
            return out
    return None


def use(fn):
    """测试用：换成一个假的后端（fn(prompt) -> str）。use(None) 换回来。"""
    global _override
    _override = fn


def status():
    return {'backend': backend(), **_state, 'cheap': {'on': cheap_on(), **_cheap_state,
            'routes': [r[0] for r in _cheap_routes()], 'paused': [n for n, t in _paused.items() if t > time.time()],
        'usable': bool(_cheap_routes())}}


def check():
    """启动时调一下：后端能不能建起来（不真的调模型）。返回错误说明或 None。"""
    try:
        if backend() == 'verbatim':
            _getaudio()
            from reflect import _call_model      # noqa: F401
        else:
            _client()
    except Exception as e:                                   # noqa: BLE001
        return f'{type(e).__name__}: {e}'
    return None
