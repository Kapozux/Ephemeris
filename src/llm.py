"""Ephemeris 调模型的唯一入口。

    ask(prompt)            → 文本或 None
    ask_json(prompt, want) → 解析好的 dict / list，或 None（会重试）
    parse_json(text, want) → 纯函数：从模型输出里抠 JSON（去代码块、修 LaTeX 反斜杠）
    status()               → 现在用哪个后端、能不能用、上次失败原因
    stray(text, source)    → 输出里冒出来、材料里没有的别的文字（ask 自动重来一次）
    claude(prompt, effort) → (文本, 会话 id)：直接调 Claude（ask 默认就先走它）；Session(材料) 同一份材料做好几件事
    claude_agent(prompt, allow) → Claude 自己跑命令查资料（只放行 allow 开头的命令；「问 Claude」用）
    ask_stream(prompt, on_text) → 边写边交字（Claude）；用不了就走原来的路线一次性交
    ask_strong(prompt)     → (文本, 实际美元)：只给「交接包」用的大模型，Claude Opus 4.6（走 OpenRouter）。失败抛异常

为什么要有它（2026-10-02 体检）：
- 以前 3 个模块各自 `sys.path` 伸进 getAudio 借 `reflect._call_model`，8 个地方各写一遍「抠 JSON + 重试」。
- 借来的东西会被别人改掉：10-01 Verbatim 为了省钱，把 `reflect._call_model` 改成按内容分流到阿里云 DeepSeek，
  Ephemeris 的日记、打卡回话也就跟着悄悄换了服务商 —— 没人替 Ephemeris 做过这个决定。
- 调用失败只返回 None，上层不报错：哪天借的函数改名了，日记会一篇都写不出来却没人知道。

首选 Claude（2026-10-13 起，见下面「Claude」那一节）：本机的 Claude Code，用订阅额度；用不了才走下面的后端。

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
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

import claude_gate
import index as idx
import providers
import store

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 仓库根目录（代码在 src/，数据在根目录）
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
    """先直连 Gemini（AI Studio）；不行再走 OpenRouter 上同一个 Google 模型（2026-10-13：AI Studio 月度上限满了，
    涉政的段只能给 Gemini、全卡住了）。直连报的是到上限 / 欠费这种一直会失败的，30 分钟内不再试直连。"""
    err = None
    if _paused.get('gemini', 0) <= time.time():
        try:
            client = _client()
        except Exception as e:                               # noqa: BLE001  没 key：直接走 OpenRouter
            client, err = None, e
        for m in ((model,) if model and client else MODELS if client else ()):
            try:
                return _gemini_direct(prompt, m, client)
            except Exception as e:                           # noqa: BLE001
                err = e
                if any(k in str(e) for k in _GEMINI_HARD):
                    _paused['gemini'] = time.time() + PAUSE_S
                    print(f'[llm] gemini 直连停 30 分钟，改走 OpenRouter：{str(e)[:160]}', flush=True)
                    break
    if openrouter_key():
        for m in ((model,) if model else MODELS):
            try:
                return _gemini_openrouter(prompt, m)
            except Exception as e:                           # noqa: BLE001
                err = e
    raise err or RuntimeError('没有可用的模型')


def _gemini_direct(prompt, m, client=None):
    """直连 Gemini（AI Studio）一次，失败抛异常，不转 OpenRouter。"""
    client = client or _client()          # 要先存住：临时的 client 一被回收就关了，请求发不出去
    resp = client.models.generate_content(model=m, contents=prompt)
    _record('record_gemini', resp, m, 'ephemeris')
    text = (resp.text or '').strip()
    if not text:
        raise RuntimeError(f'{m} 返回空（多半是安全过滤）')
    return text


_GEMINI_HARD = ('spending cap', 'spend cap', 'API key not valid', 'API_KEY_INVALID', 'PERMISSION_DENIED', 'billing')


def _gemini_openrouter(prompt, model):
    """OpenRouter 上的 google/<model>，只让 Google 自己接（provider.only），不让别家托管。"""
    import requests
    r = requests.post(OPENROUTER, timeout=600, headers={'Authorization': f'Bearer {openrouter_key()}'},
                      json={'model': f'google/{model}', 'usage': {'include': True},
                            'provider': {'only': ['google-ai-studio', 'google-vertex'], 'allow_fallbacks': False},
                            'messages': [{'role': 'user', 'content': prompt}]})
    if r.status_code != 200:
        raise RuntimeError(f'OpenRouter google/{model} {r.status_code}: {r.text[:200]}')
    body = r.json()
    _record('record_openai', body, 'openrouter', f'google/{model}', 'ephemeris')
    text = (((body.get('choices') or [{}])[0].get('message') or {}).get('content') or '').strip()
    if not text:
        raise RuntimeError(f'OpenRouter google/{model} 返回空（多半是安全过滤）')
    return text


# 向量（证据卡按意思找，2026-10-13，照 Verbatim ask.py 的 _embed_texts）：只给 Google 的模型，私密内容不给别家。
# 有 OpenRouter key 就走它上面的 google/gemini-embedding-2（10-13：AI Studio 那个月度上限满了，走 OR；
# 实测比 001 分得开：「我好累」对「状态很差」0.68、对「MIT 文书」0.39，001 是 0.70 / 0.53）；没有才直连 Gemini 的 001。
# 两个模型的向量不能混着比：存的时候记模型名（embed_model()），查的时候只用同一个模型的。
EMBED_DIM = 768
OR_EMBED = 'google/gemini-embedding-2'
GEMINI_EMBED = 'gemini-embedding-001'


def embed_ready():
    """能不能算向量：指定了向量模型（EPHEMERIS_MODEL_EMBED），或者有 OpenRouter / Gemini 的 key（DeepSeek 没有 embedding）。
    都没有的话证据卡只按字面找，补向量那步跳过。"""
    return bool(_embed_override or providers.pick('EMBED') or openrouter_key() or key('GEMINI_API_KEY', 'gemini_key'))


def embed_model():
    """存向量时记的模型名：换了模型，旧向量不跟新的比（cards 按这个名字分开），补向量会按新模型重算。"""
    own = providers.pick('EMBED')
    if own:
        return ':'.join(own)
    return OR_EMBED if openrouter_key() else GEMINI_EMBED


def _embed_compat(texts):
    """指定的向量模型（11-03，providers 的 EMBED）：OpenAI 兼容的 /embeddings。不传 dimensions（不少开源模型不认），维数按它自己的。"""
    import requests
    name, model = providers.pick('EMBED')
    last = None
    for attempt in range(3):
        try:
            r = requests.post(providers.base(name) + '/embeddings', timeout=120,
                              headers={'Authorization': f'Bearer {providers.key_for(name)}'},
                              json={'model': model, 'input': list(texts)})
            if r.status_code != 200:
                raise RuntimeError(f'{r.status_code}: {r.text[:200]}')
            data = sorted(r.json()['data'], key=lambda d: d['index'])
            if len(data) != len(texts):
                raise RuntimeError(f'回来 {len(data)} 条，送了 {len(texts)} 条')
            return [d['embedding'] for d in data]
        except Exception as e:                           # noqa: BLE001
            last = e
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f'embedding 失败：{last}')


def _embed_openrouter(texts):
    import requests
    last = None
    for attempt in range(3):
        try:
            r = requests.post('https://openrouter.ai/api/v1/embeddings', timeout=120,
                              headers={'Authorization': f'Bearer {openrouter_key()}'},
                              json={'model': OR_EMBED, 'input': list(texts), 'dimensions': EMBED_DIM})
            if r.status_code != 200:
                raise RuntimeError(f'{r.status_code}: {r.text[:200]}')
            data = sorted(r.json()['data'], key=lambda d: d['index'])
            if len(data) != len(texts):
                raise RuntimeError(f'回来 {len(data)} 条，送了 {len(texts)} 条')
            return [d['embedding'] for d in data]
        except Exception as e:                           # noqa: BLE001  限流 / 断线：等等再试
            last = e
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f'embedding 失败：{last}')


def _embed_gemini(texts, task):
    from google.genai import types
    client, last = _client(), None
    for attempt in range(3):
        try:
            r = client.models.embed_content(model=GEMINI_EMBED, contents=list(texts),
                                            config=types.EmbedContentConfig(task_type=task, output_dimensionality=EMBED_DIM))
            return [e.values for e in r.embeddings]
        except Exception as e:                           # noqa: BLE001
            last = e
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f'embedding 失败：{last}')


def embed(texts, task='RETRIEVAL_DOCUMENT'):
    """→ 每条一个行归一化的 float32 向量（numpy 矩阵），模型是 embed_model()。一次最多 100 条；三次不行抛异常。
    task（存卡 RETRIEVAL_DOCUMENT / 问的时候 RETRIEVAL_QUERY）只有直连 Gemini 时用得上。"""
    import numpy as np
    if _embed_override:
        rows = [_embed_override(t) for t in texts]
    elif _override:                                  # 测试换了假模型、没给假向量：别真去调
        raise RuntimeError('测试里没给假向量（llm.use_embed）')
    elif providers.pick('EMBED'):
        rows = _embed_compat(texts)
    elif embed_model() == OR_EMBED:
        rows = _embed_openrouter(texts)
    else:
        rows = _embed_gemini(texts, task)
    mat = np.array(rows, dtype=np.float32)
    return mat / np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9)   # 截到 768 维后要自己归一化


def use_embed(fn):
    """测试用：换成假的向量（fn(text) -> list[float]）。use_embed(None) 换回来。"""
    global _embed_override
    _embed_override = fn


_embed_override = None


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
_cheap_state = {'calls': 0, 'guarded': 0, 'fallback': 0, 'political_sent': 0, 'last_error': None}
_paused = {}                                     # 路线名 → 停到什么时候
_turn = [0]                                      # 轮到哪条路先上（几条路分着扛并发，2026-10-13）


DEEPSEEK_MODEL = 'deepseek-v4-flash'
# 只有 DeepSeek 的电脑（2026-10-14：用户同学的 Windows）聊天页的模型菜单。第一个是默认；.env 的 EPHEMERIS_DEEPSEEK_MODEL 能换默认
DEEPSEEK_CHOICES = (('deepseek-v4-flash', 'DeepSeek V4 Flash'), ('deepseek-chat', 'DeepSeek Chat'),
                    ('deepseek-reasoner', 'DeepSeek Reasoner（先想再答）'))
CHEAP_SYSTEM = '你是 Ephemeris（一个人的私人日记应用）里的文本处理程序。只按用户消息里的要求输出，不寒暄，不加额外说明。'
# 演示版（10-25，英文假数据给 Reddit 截图）：EPHEMERIS_OUTPUT_LANG=English 让日记 / 账本 / 卡 / 「问」的正文都用这门语言写。
# 只管 DeepSeek 这两条路（_cheap、deepseek_agent），演示版就只开着它们。
if idx.env('EPHEMERIS_OUTPUT_LANG'):
    CHEAP_SYSTEM += (f" Write every piece of free text you output (diaries, summaries, titles, notes, answers) in "
                     f"{idx.env('EPHEMERIS_OUTPUT_LANG')}. Keep JSON keys, fixed labels and enum values exactly as the "
                     "instructions spell them (even if they are Chinese), and quote source text verbatim.")
CHEAP_PARALLEL = int(idx.env('EPHEMERIS_CHEAP_PARALLEL') or 16)     # 同时在路上的 DeepSeek 请求最多几个（做卡 12 个窗口 × 4 段会冲到 48）
CHEAP_RETRIES = 4                                # 没有 Gemini 可退时，同一条路限流 / 断线 / 5xx 再试几次
RETRY_WAIT = 3.0                                 # 第 n 次重试前等 RETRY_WAIT × 2^(n-1) 秒（测试里设 0）
_cheap_sem = threading.BoundedSemaphore(CHEAP_PARALLEL)
# DeepSeek 价目（美元 / 百万 token：缓存没命中的输入、命中的输入、输出）。估价、记账都按它
DEEPSEEK_PRICE = {'deepseek-reasoner': (0.55, 0.14, 2.19), 'deepseek-chat': (0.27, 0.07, 1.10)}
DEEPSEEK_PRICE_DEFAULT = (0.14, 0.014, 0.28)    # V4 Flash


def deepseek_model():
    return (idx.env('EPHEMERIS_DEEPSEEK_MODEL') or DEEPSEEK_MODEL).strip()


def gemini_ready():
    """有没有 Gemini 可退（直连 key 或 OpenRouter）。只有 DeepSeek 的电脑上没有：涉政的也只能给 DeepSeek、失败了多重试几次。"""
    return bool(key('GEMINI_API_KEY', 'gemini_key') or openrouter_key())


def political(text):
    """命中的时政词（列表）。出现 GUARD_MIN 次以上就不给国内模型。"""
    return _POLITICAL.findall(text or '')


def cheap_on():
    """配了 DeepSeek（有 key、没关掉）。跟「现在有没有没被停的路」是两回事：路全被停了也算配了，
    这时 fallback=False 的调用直接当失败，不退到 Gemini。"""
    return (idx.env('EPHEMERIS_CHEAP') or 'on').strip().lower() not in ('off', '0', 'false', 'no') and bool(_cheap_routes(all_routes=True))


def _cheap_routes(all_routes=False):
    """写东西走的路：[(名字, 地址, key, 模型, 多带的参数)]。哪几家、什么顺序、指定的模型都在 providers（11-03 起）。"""
    now = time.time()
    out = providers.routes()
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


def _retryable(e):
    t = str(e)
    return any(k in t for k in ('429', '500:', '502:', '503:', '504:', 'Timeout', 'timed out', 'Connection', 'SSL', '返回空'))


def _picked_deepseek():
    """聊天页选了 DeepSeek 的某个模型（llm.using）：只走 DeepSeek 官方那条、用这个模型。"""
    m = getattr(_pick, 'model', None)
    return m if m and m.startswith('deepseek') else None


def _picked_route():
    """聊天页选了「接口名:模型」（choices() 里 providers 指定的那种）：只走那一条。"""
    m = getattr(_pick, 'model', None) or ''
    name, _, model = m.partition(':')
    return providers.route(name, model) if model and name in providers.PRESETS and providers.key_for(name) else None


def _cheap_post(route, prompt, json_mode=False, purpose=''):
    """在 _cheap_routes 的一条路上发一次请求，拿到文字返回，失败抛异常（不重试、不换路）。"""
    import requests
    name, base, k, model, extra = route
    body_json = {'model': model, 'messages': [{'role': 'system', 'content': CHEAP_SYSTEM}, {'role': 'user', 'content': prompt}],
                 **extra}
    if json_mode and name == 'deepseek' and 'reasoner' not in model and 'json' in prompt.lower():
        body_json['response_format'] = {'type': 'json_object'}
    t = time.time()
    with _cheap_sem:
        r = requests.post(base + '/chat/completions', timeout=300, headers={'Authorization': f'Bearer {k}'}, json=body_json)
    if r.status_code != 200:
        raise RuntimeError(f'{r.status_code}: {r.text[:200]}')
    body = r.json()
    text = (((body.get('choices') or [{}])[0].get('message') or {}).get('content') or '').strip()
    if not text:
        raise RuntimeError('返回空')
    _record('record_openai', body, name, model, 'ephemeris')
    _api_record(name, model, purpose, body.get('usage') or {}, time.time() - t, True)
    return text


def _cheap(prompt, why=None, force=False, json_mode=False, purpose=''):
    """走 DeepSeek，拿到文字就返回；涉政、没有路线、全失败返回 None（调用方接着用 Gemini）。
    why：传一个 dict 进来，会记下是不是被内容审核拦了（why['moderated']=True）—— 词表没认出来、审核却拦了的，
    也算涉政，该走 Gemini；跟限流不一样，重试多少次 DeepSeek 都不会收。
    force：涉政也照样给 DeepSeek（没有 Gemini 可退时，_call 传）。
    json_mode：要的是 JSON 对象（ask_json(want=dict)）—— DeepSeek 官方那条开 response_format=json_object，回来的一定是合法 JSON。
    DeepSeek 的上下文缓存按前缀命中：系统提示固定（CHEAP_SYSTEM），提示词模板也都是「固定的要求在前、材料在后」，
    同一批活（做卡、打分）的前缀能读缓存，按 1/10 价算。"""
    if not force and len(political(prompt)) >= GUARD_MIN:
        with _lock:
            _cheap_state['guarded'] += 1
        return None
    routes = _cheap_routes()
    picked, chosen = _picked_deepseek(), _picked_route()
    if chosen:
        routes = [chosen]
    elif picked:
        routes = [(n, b, k, picked, x) for n, b, k, m, x in routes if n == 'deepseek'] or routes
    tries = 1 if gemini_ready() else CHEAP_RETRIES      # 有 Gemini 就失败一次换它；只有 DeepSeek 就多扛几下
    with _lock:
        i = _turn[0] % max(len(routes), 1)
        _turn[0] += 1
    if providers.pick('TEXT') and not chosen:
        i = 0                                                # 指定了写东西用哪个（11-03）：每次先走它，别的只当后备，不轮流
    for name, base, k, model, extra in routes[i:] + routes[:i]:     # 轮流先上，失败了再试别的
        for attempt in range(tries):
            try:
                text = _cheap_post((name, base, k, model, extra), prompt, json_mode, purpose)
                with _lock:
                    _cheap_state['calls'] += 1
                    if force:
                        _cheap_state['political_sent'] += 1
                return text
            except Exception as e:                           # noqa: BLE001
                with _lock:
                    _cheap_state['last_error'] = f'{name}: {e}'[:200]
                if why is not None and any(k in str(e) for k in _MODERATED):
                    why['moderated'] = True
                if picked and 'model' in str(e).lower():
                    break                                    # 选的模型这条路没有：别停整条路
                _pause(name, e)
                if attempt + 1 < tries and _retryable(e) and _paused.get(name, 0) <= time.time():
                    time.sleep(RETRY_WAIT * 2 ** attempt)
                    continue
                break
    with _lock:
        _cheap_state['fallback'] += 1
    return None


def deepseek_agent(prompt, tool, run_tool, on_event=None, max_steps=16, purpose='', notes=None, review=None):
    """没有 Claude 时的「深挖」：让 DeepSeek 自己调工具查资料（OpenAI 那种 function calling）。
    tool：一个工具的 JSON 描述（name / description / parameters）；run_tool(参数 dict) → 工具输出的文字。
    on_event(kind, text) 跟 claude_agent 一样：'tool'（要跑的命令，给界面报进度）、'message' / 'delta'（写好的回答）。
    最多 max_steps 轮；到了还在查就逼它用已经查到的回答。返回回答全文；失败抛异常，点了停止抛 Cancelled。
    走第一条能用的 DeepSeek 路（聊天页选了模型就只走官方那条）；思考过程（reasoning_content）不回传，回传了 DeepSeek 会报 400。
    notes=(每几步, 提示词)：查了这么多步就让它把有用的记成笔记，之前的工具输出整段换成笔记（上下文不越堆越长，弱模型后半程不糊涂）。
    review=提示词：第一次想交的时候先让它照这张单子过一遍，要补就接着查；回「交」就交刚才那份。"""
    import requests
    picked, chosen = _picked_deepseek(), _picked_route()
    if chosen:
        first = chosen
    elif picked:
        first = next(((n, b, k, picked, x) for n, b, k, m, x in _cheap_routes() if n == 'deepseek'), None) or providers.ask_route()
    else:
        first = providers.ask_route()                           # 指定了「问」的模型（EPHEMERIS_MODEL_ASK）就它，没指定跟写东西同一条
    if not first:
        raise RuntimeError('没有能用的模型（.env 里填 DEEPSEEK_API_KEY，或者在设置里接一家）')
    name, base, k, model, extra = first
    max_steps = int(idx.env('EPHEMERIS_AGENT_STEPS') or max_steps)
    msgs = [{'role': 'system', 'content': CHEAP_SYSTEM}, {'role': 'user', 'content': prompt}]
    tools = [{'type': 'function', 'function': tool}]
    draft, kept = None, []                                       # 交过一次的草稿；记下的笔记（每次一段）
    since = 0                                                    # 上次记笔记之后又查了几步
    for step in range(max_steps + 1):
        if cancelled():
            raise Cancelled('停下了')
        last = step == max_steps
        if notes and draft is None and not last and since >= notes[0]:
            got = _agent_say(name, base, k, model, extra, msgs + [{'role': 'user', 'content': notes[1]}], purpose)
            since = 0
            if got:                                              # 没记成：工具输出照留着，过几步再试
                kept.append(got)
                msgs = msgs[:2] + [{'role': 'assistant', 'content': '到现在查到的（笔记）：\n\n' + '\n\n'.join(kept)},
                                   {'role': 'user', 'content': '好，接着查；之前的工具输出已经收进笔记了，要原话再 read。'}]
                if on_event:
                    on_event('notes', got)
        if last:
            msgs.append({'role': 'user', 'content': '查的次数到上限了。不要再调工具，用已经查到的内容回答（照前面的要求）。'})
        body_json = {'model': model, 'messages': msgs, **extra, **({} if last else {'tools': tools})}
        t = time.time()
        for attempt in range(CHEAP_RETRIES):
            try:
                with _cheap_sem:
                    r = requests.post(base + '/chat/completions', timeout=300, headers={'Authorization': f'Bearer {k}'},
                                      json=body_json)
                if r.status_code != 200:
                    raise RuntimeError(f'{r.status_code}: {r.text[:200]}')
                break
            except Exception as e:                           # noqa: BLE001
                if attempt + 1 < CHEAP_RETRIES and _retryable(e):
                    time.sleep(RETRY_WAIT * 2 ** attempt)
                    continue
                _pause(name, e)
                raise RuntimeError(f'DeepSeek：{e}') from e
        body = r.json()
        _api_record(name, model, purpose, body.get('usage') or {}, time.time() - t, True)
        msg = (body.get('choices') or [{}])[0].get('message') or {}
        calls = msg.get('tool_calls') or []
        if not calls:
            text = (msg.get('content') or '').strip()
            if draft and (not text or text.strip('「」。 ') == '交'):
                text = draft                                     # 过完单子说没问题：交刚才那份
            if not text:
                raise RuntimeError('DeepSeek 返回空')
            if review and draft is None and not last:
                draft = text
                msgs += [{'role': 'assistant', 'content': text}, {'role': 'user', 'content': review}]
                if on_event:
                    on_event('review', '')
                continue
            if on_event:
                on_event('message', '')
                on_event('delta', text)
            return text
        msgs.append({'role': 'assistant', 'content': msg.get('content') or '', 'tool_calls': calls})
        for c in calls:
            fn = c.get('function') or {}
            try:
                args = json.loads(fn.get('arguments') or '{}')
            except ValueError:
                args = {}
            if on_event:
                on_event('tool', str(args.get('command') or ''))
            if cancelled():
                raise Cancelled('停下了')
            try:
                out = run_tool(args)
            except Exception as e:                           # noqa: BLE001  工具出错也告诉它，让它换个查法
                out = f'出错了：{e}'
            if on_event:
                on_event('result', str(out))
            msgs.append({'role': 'tool', 'tool_call_id': c.get('id'), 'content': str(out)[:12000]})
        since += 1
    raise RuntimeError('DeepSeek 查了太多步还没回答')


def _agent_say(name, base, k, model, extra, msgs, purpose):
    """查到一半让它不调工具说一段话（记笔记）。失败就当没记，不打断查。"""
    import requests
    t = time.time()
    try:
        with _cheap_sem:
            r = requests.post(base + '/chat/completions', timeout=300, headers={'Authorization': f'Bearer {k}'},
                              json={'model': model, 'messages': msgs, **extra})
        if r.status_code != 200:
            raise RuntimeError(f'{r.status_code}: {r.text[:200]}')
        body = r.json()
    except Exception:                                            # noqa: BLE001
        return ''
    _api_record(name, model, purpose, body.get('usage') or {}, time.time() - t, True)
    return (((body.get('choices') or [{}])[0].get('message') or {}).get('content') or '').strip()


def deepseek_price(model):
    """记账、估价用的单价：DeepSeek 自己的表 → providers 的模型资料 → 按 V4 Flash 估。"""
    return DEEPSEEK_PRICE.get(model) or providers.price(model) or DEEPSEEK_PRICE_DEFAULT


def _api_record(route, model, purpose, u, secs, ok):
    """记进 api_usage（设置页「这周花了多少」、只有 DeepSeek 的电脑也看得到）。命中缓存的输入按便宜那档算。"""
    hit = u.get('prompt_cache_hit_tokens') or ((u.get('prompt_tokens_details') or {}).get('cached_tokens')) or 0
    inp, out = u.get('prompt_tokens') or 0, u.get('completion_tokens') or 0
    miss_p, hit_p, out_p = deepseek_price(model)
    usd = u.get('cost') if route == 'openrouter' and u.get('cost') is not None else \
        ((inp - hit) * miss_p + hit * hit_p + out * out_p) / 1e6
    try:
        store.run('INSERT INTO api_usage (ts, route, model, purpose, input, output, cache_hit, usd, secs, ok) VALUES (?,?,?,?,?,?,?,?,?,?)',
                  datetime.now().isoformat(timespec='seconds'), route, model, purpose, inp, out, hit, float(usd), round(secs, 1), int(ok))
    except Exception:                                        # noqa: BLE001  记账失败不影响结果
        pass


def api_week():
    """这周（周一起）走 API 的调用：次数、估的美元、缓存命中的输入占比。"""
    monday = (datetime.now() - timedelta(days=datetime.now().weekday())).date().isoformat()
    r = store.one('SELECT COUNT(*) calls, COALESCE(SUM(usd), 0) usd, COALESCE(SUM(input), 0) input, COALESCE(SUM(cache_hit), 0) hit '
                  'FROM api_usage WHERE ts >= ?', monday)
    return {**r, 'hit_rate': round(r['hit'] / r['input'], 2) if r['input'] else None}


def prices():
    """(每 token 进, 每 token 出) 美元：估价用。能走 DeepSeek 就按它算（涉政的那一小部分按 Gemini，忽略）。"""
    if cheap_on():
        miss, _, out = deepseek_price(deepseek_model())
        return miss / 1e6, out / 1e6
    return 0.25 / 1e6, 1.50 / 1e6


# ------------------------------------------------------------------ Claude（2026-10-13 起首选）
# 走本机的 Claude Code（`claude -p`），用的是用户的订阅额度，不是 API key。
# 精简模式：自己的系统提示、不给工具、不读设置和 MCP、输出 JSON（带 token 用量和等值美元）—— 每次只多带约 550 token。
#   · 同时最多 CLAUDE_PARALLEL 个进程：10-13 全量做卡一个小时开了几千次，撞了 5 小时上限（额度按「贵的 token」算，缓存写入最贵）
#   · 撞上限 / 没登录：Claude 停一阵（能读到重置时间就停到那时），期间自动走原来的 DeepSeek / Gemini
#   · 每次调用记进 claude_usage（设置页「本周 Claude 用了多少」）
#   · Session：同一份材料做好几件事 —— 第一次写入缓存，之后 --resume 只送任务，材料按读取算（便宜 25～40 倍）
#   · 跑测试时不会真的调（EPHEMERIS_NO_USAGE；要测就 use_claude(假函数)）
#   · 关掉：.env 里 EPHEMERIS_CLAUDE=off
CLAUDE_MODEL = 'claude-opus-5-5'
# 聊天页能自己挑的模型（2026-10-14：照 claude.ai 的菜单，Fable 太贵不放；Opus 3 本机已经调不到）。第一个是默认。
CLAUDE_CHOICES = (('claude-opus-5-5', 'Opus 5.5'), ('claude-sonnet-5-5', 'Sonnet 5.5'), ('claude-haiku-5-5', 'Haiku 5.5'),
                  ('claude-opus-5', 'Opus 5'), ('claude-opus-4-8', 'Opus 4.8'), ('claude-opus-4-7', 'Opus 4.7'),
                  ('claude-opus-4-6', 'Opus 4.6'), ('claude-sonnet-5', 'Sonnet 5'), ('claude-sonnet-4-6', 'Sonnet 4.6'),
                  ('claude-haiku-4-5', 'Haiku 4.5'))
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
_pick = threading.local()        # 这个线程临时换的模型（using）；别的线程照旧用 CLAUDE_MODEL


@contextmanager
def using(model=None):
    """这一段里本线程的 Claude 调用都用 model（None = 默认）。聊天页选的模型靠它传到 qa 的每一次调用。"""
    old = getattr(_pick, 'model', None)
    _pick.model = model or old
    try:
        yield
    finally:
        _pick.model = old


def current_model():
    return getattr(_pick, 'model', None) or CLAUDE_MODEL


CLAUDE_PARALLEL = int(idx.env('EPHEMERIS_CLAUDE_PARALLEL') or 3)
CLAUDE_TIMEOUT = 900
# 流式调用（问、问 Claude）：多久一个事件都没有才算卡住；总时长另有上限（timeout）。
# 10-17：「把十几个活动从 8 月到现在完整挖一遍」查了 10 分钟还在查，被总时长掐掉、查到的全丢了 —— 一直在干活的不该掐。
CLAUDE_IDLE = 300
AGENT_TIMEOUT = 3600
CLAUDE_CWD = os.path.expanduser('~/.ephemeris-claude')     # 会话存在这个目录名下，不混进用户自己的 Claude Code 项目
CLAUDE_SYSTEM = '你是 Ephemeris（一个人的私人日记应用）里的文本处理程序。只按用户消息里的要求输出，不寒暄，不加额外说明。'
_claude_sem = threading.BoundedSemaphore(CLAUDE_PARALLEL)
# 「问」另算（10-30 用户：同时开 5 个问，后两个一直不动——排在做卡那 3 个位子后面）：问是人在等着的，单独给 QA_PARALLEL 个位子；
# 三个应用合计的并发和留给问的位子还是 claude_gate 管
QA_PARALLEL = int(idx.env('EPHEMERIS_QA_PARALLEL') or 8)
_qa_sem = threading.BoundedSemaphore(QA_PARALLEL)


def _local_slot(purpose):
    return _qa_sem if _prio(purpose) == 'live' else _claude_sem
_claude_runner = None            # 测试用：use_claude(fn)，fn(args, stdin) -> claude -p 的 JSON（dict）
_LIMIT = re.compile(r'usage limit|limit reached|rate limit|(?<![A-Za-z])resets?(?![A-Za-z])|Not logged in|/login|credit balance', re.I)


# 三个应用（Verbatim / Ephemeris / 墨页）共用一份额度时的分配器：claude_gate（CLAUDE_GATE 没设就不起作用）。
# 问 = live（留着位子，批量跑满了也不排队），做卡 = batch，其余 normal；撞上限写进共用的 pause，别的应用也停。
GATE_APP = 'ephemeris'
for _k in ('CLAUDE_GATE', 'CLAUDE_GATE_CAP', 'CLAUDE_GATE_RESERVE', 'CLAUDE_GATE_WEEK_KEEP', 'CLAUDE_GATE_WEEK_KEEP_LIVE',
           'CLAUDE_GATE_LIMIT_5H', 'CLAUDE_GATE_KEEP', 'CLAUDE_GATE_CAP_CODEX', 'CLAUDE_GATE_RESERVE_CODEX'):  # claude_gate 只看 os.environ，.env 里的要搬过去
    if idx.env(_k):
        os.environ.setdefault(_k, idx.env(_k))


def _prio(purpose):
    p = str(purpose or '')
    return 'live' if p.startswith('qa') or p == 'ask' else 'batch' if p == 'cards' else 'normal'


def _claude_bin():
    """本机 claude 的完整路径（Windows 上 npm 装的是 claude.cmd，subprocess 要完整路径才找得到）。没装是 None。"""
    return shutil.which('claude')


def claude_on():
    if (idx.env('EPHEMERIS_CLAUDE') or 'on').strip().lower() in ('off', '0', 'false', 'no'):
        return False
    if (idx.env('EPHEMERIS_PROVIDER') or 'claude').strip().lower() != 'claude':
        return False                             # 「开始用」页选了别家：装着 Claude Code 也不碰它
    if claude_gate.paused_until():
        return False
    if _claude_runner:
        return _paused.get('claude', 0) <= time.time()
    if os.environ.get('EPHEMERIS_NO_USAGE'):     # 测试环境：没给假的就当没有
        return False
    return bool(_claude_bin()) and _paused.get('claude', 0) <= time.time()


def _env(args):
    """一次性的调用（不留会话、不接着问）关掉缓存写入：Claude Code 默认把输入写进 1 小时缓存，写的价钱是普通输入的 2 倍，
    之后又没人读，等于白付一倍（10-13 实测同样的输入 $0.063 → $0.032）。要接着用的（Session、问 Claude、追问）照常写。"""
    if '--no-session-persistence' in args and '--resume' not in args:
        return {**os.environ, 'DISABLE_PROMPT_CACHING': '1'}
    return None


def _claude_exec(args, stdin):
    if _claude_runner:
        return _claude_runner(args, stdin)
    os.makedirs(CLAUDE_CWD, exist_ok=True)
    r = subprocess.run([_claude_bin() or 'claude'] + args, input=stdin, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', cwd=CLAUDE_CWD, timeout=CLAUDE_TIMEOUT, env=_env(args))
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {'is_error': True, 'result': (r.stdout or r.stderr or f'claude 退出码 {r.returncode}')[:500]}


def _claude_pause(text):
    """撞上限：结果里带了重置时间（「…|1760400000」）就停到那时，不然停 30 分钟。"""
    m = re.search(r'\|(\d{10})', text or '')
    until = int(m.group(1)) if m else time.time() + PAUSE_S
    _paused['claude'] = max(until, time.time() + 60)
    claude_gate.pause(_paused['claude'], text, GATE_APP)
    print(f'[llm] Claude 停到 {datetime.fromtimestamp(_paused["claude"]):%H:%M}，期间走 DeepSeek / Gemini：{(text or "")[:120]}', flush=True)


def claude(prompt, effort='medium', session=None, keep=False, purpose=''):
    """调一次 Claude，返回 (文本, 会话 id)。失败抛异常。
    session：接着那个会话问（--resume，前面的内容走缓存）；keep：留下会话，之后能接着问（Session 用）。"""
    args = ['-p', '--model', current_model(), '--effort', effort, '--system-prompt', CLAUDE_SYSTEM, '--tools', '',
            '--disable-slash-commands', '--strict-mcp-config', '--setting-sources', '', '--output-format', 'json']
    if session:
        args += ['--resume', session]
    elif not keep:
        args += ['--no-session-persistence']
    t = time.time()
    with _local_slot(purpose), claude_gate.slot(GATE_APP, purpose, _prio(purpose)):
        d = _claude_exec(args, prompt)
    text = (d.get('result') or '').strip()
    _claude_record(d, effort, purpose, time.time() - t)
    if d.get('is_error') or not text:
        if _LIMIT.search(text):
            _claude_pause(text)
        raise RuntimeError(f'Claude：{text[:200] or "返回空"}')
    return text, d.get('session_id')


# 停下（聊天页的停止按钮，2026-10-13）：cancel(线程) 把那个线程正在跑的 claude 进程当场关掉，之后它抛 Cancelled——
# 调用方（qa）留下已经写出来的那段；ask_stream 不会把它当成「Claude 失败了」再换别的模型重写一遍。
class Cancelled(RuntimeError):
    pass


_procs = {}                 # 线程 id → 正在跑的 claude 进程
_cancelled = set()


def cancel(tid):
    _cancelled.add(tid)
    p = _procs.get(tid)
    if p and p.poll() is None:
        p.kill()


def cancelled():
    return threading.get_ident() in _cancelled


def uncancel():
    _cancelled.discard(threading.get_ident())


def _claude_events(args, prompt, on_event, timeout):
    """跑 claude -p --output-format stream-json，边读边报：on_event(kind, text)
      kind = 'message'（新的一段回复开始）| 'delta'（一小段字）| 'tool'（它要跑的命令）| 'result'（命令吐回来的东西）| 'text'（一整段话写完了）
           | 'session'（一开始就报会话 id：中途断了也知道接哪个会话）
    返回最后那个 result 事件（dict）。测试时 _claude_runner 返回 {'events': [...], **result}。"""
    def seen(ev):
        if not on_event:
            return
        t = ev.get('type')
        if t == 'system' and ev.get('subtype') == 'init' and ev.get('session_id'):
            on_event('session', ev['session_id'])
        elif t == 'stream_event':
            e = ev.get('event') or {}
            if e.get('type') == 'message_start':
                on_event('message', '')
            elif e.get('type') == 'content_block_delta' and (e.get('delta') or {}).get('type') == 'text_delta':
                on_event('delta', e['delta'].get('text', ''))
        elif t == 'assistant':
            for b in (ev.get('message') or {}).get('content') or []:
                if b.get('type') == 'tool_use':
                    on_event('tool', (b.get('input') or {}).get('command', ''))
                elif b.get('type') == 'text' and b.get('text', '').strip():
                    on_event('text', b['text'].strip())
        elif t == 'user':                                    # 工具跑完吐回来的东西（「问」靠它记这轮真看过哪些出处，11-03）
            for b in (ev.get('message') or {}).get('content') or []:
                if isinstance(b, dict) and b.get('type') == 'tool_result':
                    c = b.get('content')
                    on_event('result', c if isinstance(c, str) else ''.join(x.get('text', '') for x in c or [] if isinstance(x, dict)))

    if _claude_runner:
        d = _claude_runner(args, prompt)
        for ev in d.get('events', []):
            if cancelled():
                raise Cancelled('停下了')
            seen(ev)
        if cancelled():
            raise Cancelled('停下了')
        return d
    os.makedirs(CLAUDE_CWD, exist_ok=True)
    t = time.time()
    p = subprocess.Popen([_claude_bin() or 'claude'] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding='utf-8', errors='replace', cwd=CLAUDE_CWD, env=_env(args))
    _procs[threading.get_ident()] = p
    try:
        p.stdin.write(prompt)
        p.stdin.close()
        return _claude_read(p, seen, t, timeout)
    finally:
        _procs.pop(threading.get_ident(), None)


def _claude_read(p, seen, t, timeout, idle=None):
    """读到 result 为止。旁边一条看门的线：超过 idle 秒没有一行输出、或者总共超过 timeout 秒，就掐掉进程。"""
    idle = idle or CLAUDE_IDLE
    d = {'is_error': True, 'result': ''}
    last, why = [time.time()], []

    def watch():
        while p.poll() is None:
            now = time.time()
            if now - last[0] > idle:
                why.append(f'超过 {idle} 秒没有动静')
            elif now - t > timeout:
                why.append(f'查了 {timeout // 60} 分钟还没写完')
            if why:
                p.kill()
                return
            time.sleep(1)
    threading.Thread(target=watch, daemon=True).start()
    for line in p.stdout:
        last[0] = time.time()
        if cancelled():
            break
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get('type') == 'result':
            d = ev
        else:
            seen(ev)
    if why:
        d = {'is_error': True, 'result': why[0]}
    if cancelled():
        p.kill()
        p.wait()
        raise Cancelled('停下了')
    p.wait()
    if not d.get('result') and d.get('is_error'):
        d['result'] = (p.stderr.read() or '')[:300]
    return d


def _claude_finish(d, effort, purpose, t):
    text = (d.get('result') or '').strip()
    _claude_record(d, effort, purpose, time.time() - t)
    if d.get('is_error') or not text:
        if _LIMIT.search(text):
            _claude_pause(text)
        raise RuntimeError(f'Claude：{text[:200] or "返回空"}')
    return text, d.get('session_id')


def _base_args(effort):
    return ['-p', '--model', current_model(), '--effort', effort, '--system-prompt', CLAUDE_SYSTEM,
            '--disable-slash-commands', '--strict-mcp-config', '--setting-sources', '',
            '--output-format', 'stream-json', '--verbose', '--include-partial-messages']


def claude_stream(prompt, on_text, effort='medium', purpose=''):
    """跟 claude() 一样，但边写边把字交给 on_text(一小段)。返回 (全文, 会话 id)。失败抛异常。"""
    t = time.time()
    with _local_slot(purpose), claude_gate.slot(GATE_APP, purpose, _prio(purpose)):
        d = _claude_events(_base_args(effort) + ['--tools', '', '--no-session-persistence'], prompt,
                           lambda kind, text: kind == 'delta' and on_text(text), CLAUDE_TIMEOUT)
    return _claude_finish(d, effort, purpose, t)


def claude_agent(prompt, allow, effort='high', session=None, on_event=None, purpose='', timeout=AGENT_TIMEOUT):
    """让 Claude 自己用工具干活（「问 Claude」）：只给 Bash，且只放行 allow 开头的命令（别的命令一律被拒）。
    on_event(kind, text) 见 _claude_events：'tool' 给界面报进度，'message' / 'delta' 是正在写的字。
    返回 (最后的回答, 会话 id)。会话留着，追问时传 session 接着问（前面查到的材料走缓存）。失败抛异常。"""
    args = _base_args(effort) + ['--tools', 'Bash', '--allowedTools', f'Bash({allow}:*)']
    if session:
        args += ['--resume', session]
    t = time.time()
    with _local_slot(purpose), claude_gate.slot(GATE_APP, purpose, _prio(purpose)):
        d = _claude_events(args, prompt, on_event, timeout)
    return _claude_finish(d, effort, purpose, t)


def ask_stream(prompt, on_text, effort='medium', purpose=''):
    """流式的 ask：Claude 能用就边写边交字；不能用（或中途失败）就走原来的路线，写完一次性交出。返回全文。"""
    if claude_on():
        sent = []
        try:
            return claude_stream(prompt, lambda x: (sent.append(x), on_text(x)), effort, purpose)[0]
        except Cancelled:
            raise                                            # 用户点了停止：不换模型重写
        except Exception as e:                               # noqa: BLE001
            with _lock:
                _state['last_error'] = f'{type(e).__name__}: {e}'[:300]
            if sent:
                on_text('\n\n（Claude 中途断了，换个模型重写）\n\n')
    text = ask(prompt, route='cheap', effort=effort, purpose=purpose)
    if text:
        on_text(text)
    return text


def _claude_record(d, effort, purpose, secs):
    claude_gate.record(GATE_APP, purpose, current_model(), d, secs, _prio(purpose))
    u = d.get('usage') or {}
    try:
        store.run('INSERT INTO claude_usage (ts, purpose, model, effort, input, output, cache_read, cache_write, usd, secs, ok) '
                  'VALUES (?,?,?,?,?,?,?,?,?,?,?)', datetime.now().isoformat(timespec='seconds'), purpose, current_model(), effort,
                  u.get('input_tokens', 0), u.get('output_tokens', 0), u.get('cache_read_input_tokens', 0),
                  u.get('cache_creation_input_tokens', 0), float(d.get('total_cost_usd') or 0), round(secs, 1),
                  0 if d.get('is_error') else 1)
    except Exception:                                        # noqa: BLE001  记账失败不影响结果
        pass


def claude_week():
    """这周（周一起）Claude 用了多少：次数、等值美元、缓存读 / 写。"""
    monday = (datetime.now() - timedelta(days=datetime.now().weekday())).date().isoformat()
    return store.one('SELECT COUNT(*) calls, COALESCE(SUM(usd), 0) usd, COALESCE(SUM(cache_read), 0) cache_read, '
                     'COALESCE(SUM(cache_write), 0) cache_write FROM claude_usage WHERE ts >= ?', monday)


def use_claude(fn):
    """测试用：fn(args, stdin) -> dict（claude -p --output-format json 的样子）。use_claude(None) 换回来。"""
    global _claude_runner
    _claude_runner = fn
    _paused.pop('claude', None)


class Session:
    """同一份材料做好几件事（早上那一串、「问」的追问）。第一次把材料 + 任务送去并留下会话；之后只送任务（--resume），
    材料在缓存里按读取算。Claude 用不了就退回普通 ask：每次把材料 + 任务整段送。"""

    def __init__(self, material, effort='medium', purpose=''):
        self.material, self.effort, self.purpose, self.sid = material, effort, purpose, None

    def ask(self, task):
        if claude_on():
            try:
                if self.sid:
                    text, sid = claude(task, self.effort, session=self.sid, purpose=self.purpose)
                else:
                    text, sid = claude(self.material + '\n\n' + task, self.effort, keep=True, purpose=self.purpose)
                self.sid = sid or self.sid
                return text
            except Exception as e:                           # noqa: BLE001
                with _lock:
                    _state['last_error'] = f'{type(e).__name__}: {e}'[:300]
        text = _codex_try(self.material + '\n\n' + task, self.effort, self.purpose)
        if text:
            return text
        return ask(self.material + '\n\n' + task, effort=self.effort, route='cheap', purpose=self.purpose)


# ------------------------------------------------------------------ GPT（Codex CLI，用户的 ChatGPT 订阅）
# 2026-11-03 用户：GPT 也有订阅，像 claude -p 那样接进来，跟 Verbatim / 墨页一起归 claude_gate 管（provider='codex'）。
#   · 只在 Claude 用不了的时候顶上（_call、Session 的后备），排在 DeepSeek / Gemini 前面；route='cheap' 的大批量活不走它（Plus 额度小）。
#   · 要自己打开：.env 里 EPHEMERIS_CODEX=on（别人装了 Codex 也不会悄悄用他的 ChatGPT 额度、把日记发给 OpenAI）。
#   · 只做纯文字的活。「问」不交给它：codex 自带 shell 关不掉，只读沙盒照样能读整个硬盘，绕过屏蔽和文件白名单。
#   · codex exec --ephemeral：不留会话记录（不然会被 codex.sync 当成用户自己的 Codex 对话收进日记）；
#     --ignore-user-config：不带用户自己配的插件 / MCP / 记忆；模型照用户 config.toml 里那个（EPHEMERIS_CODEX_MODEL 可以换）。
CODEX_CWD = os.path.expanduser('~/.ephemeris-codex')
CODEX_TIMEOUT = 900
CODEX_EFFORT = {'low': 'low', 'medium': 'medium', 'high': 'high', 'xhigh': 'xhigh', 'max': 'xhigh'}
_codex_runner = None             # 测试用：use_codex(fn)，fn(args, stdin) -> (事件列表, stderr)
_codex_sem = threading.BoundedSemaphore(int(idx.env('EPHEMERIS_CODEX_PARALLEL') or 2))


def _codex_bin():
    return shutil.which('codex')


def codex_model():
    m = (idx.env('EPHEMERIS_CODEX_MODEL') or '').strip()
    if m:
        return m
    try:
        with open(os.path.expanduser('~/.codex/config.toml'), encoding='utf-8') as f:
            for line in f:
                hit = re.match(r'\s*model\s*=\s*"([^"]+)"', line)
                if hit:
                    return hit.group(1)
                if line.lstrip().startswith('['):
                    break
    except OSError:
        pass
    return ''


def codex_on():
    if (idx.env('EPHEMERIS_CODEX') or 'off').strip().lower() not in ('on', '1', 'true', 'yes'):
        return False
    if claude_gate.paused_until('codex') or _paused.get('codex', 0) > time.time():
        return False
    if _codex_runner:
        return True
    if os.environ.get('EPHEMERIS_NO_USAGE'):
        return False
    return bool(_codex_bin())


def _codex_exec(args, stdin):
    if _codex_runner:
        return _codex_runner(args, stdin)
    os.makedirs(CODEX_CWD, exist_ok=True)
    r = subprocess.run([_codex_bin() or 'codex'] + args, input=stdin, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', cwd=CODEX_CWD, timeout=CODEX_TIMEOUT)
    events = []
    for line in r.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return events, r.stderr


def codex(prompt, effort='medium', purpose=''):
    """调一次 GPT（codex exec），返回文本。失败抛异常；撞上限就停这一家（写进共用的 pauses，三边都停）。"""
    model = codex_model()
    args = ['exec', '--json', '--ephemeral', '--skip-git-repo-check', '--ignore-user-config', '--ignore-rules',
            '-s', 'read-only', '-C', CODEX_CWD, '-c', f'model_reasoning_effort="{CODEX_EFFORT.get(effort, "medium")}"',
            '--disable', 'apps', '--disable', 'browser_use', '--disable', 'computer_use']
    if model:
        args += ['-m', model]
    args.append('-')
    stdin = CLAUDE_SYSTEM + '不要运行任何命令、不要读写文件，材料都在下面。\n\n' + prompt
    t = time.time()
    with _codex_sem, claude_gate.slot(GATE_APP, purpose, _prio(purpose), provider='codex'):
        events, err = _codex_exec(args, stdin)
    texts, usage, errors = [], None, []
    for e in events:
        item = e.get('item') or {}
        if e.get('type') == 'item.completed' and item.get('type') == 'agent_message':
            texts.append(item.get('text') or '')
        elif e.get('type') == 'turn.completed':
            usage = e.get('usage')
        elif e.get('type') in ('error', 'turn.failed'):
            errors.append(str(e.get('message') or (e.get('error') or {}).get('message') or e))
    text = (texts[-1] if texts else '').strip()
    bad = bool(errors) or not text
    claude_gate.record(GATE_APP, purpose, 'codex:' + (model or 'default'), {'usage': usage or {}, 'is_error': bad},
                       time.time() - t, _prio(purpose), provider='codex')
    if bad:
        why = ' '.join(errors) or (err or '').strip()[-300:] or '返回空'
        if _LIMIT.search(why):
            m = re.search(r'resets?[^0-9]*(\d{10})', why)
            until = max(int(m.group(1)) if m else time.time() + PAUSE_S, time.time() + 60)
            _paused['codex'] = until
            claude_gate.pause(until, why, GATE_APP, provider='codex')
        raise RuntimeError(f'GPT（Codex）：{why[:200]}')
    return text


def _codex_try(prompt, effort, purpose):
    """Claude 用不了时先让 GPT 顶上；没开 / 失败返回 None，调用方接着走 DeepSeek / Gemini。"""
    if not codex_on():
        return None
    try:
        return codex(prompt, effort, purpose)
    except Exception as e:                                   # noqa: BLE001
        with _lock:
            _state['last_error'] = f'{type(e).__name__}: {e}'[:300]
        return None


def use_codex(fn):
    """测试用：fn(args, stdin) -> (codex exec --json 的事件列表, stderr)。use_codex(None) 换回来。"""
    global _codex_runner
    _codex_runner = fn
    _paused.pop('codex', None)


def _call(prompt, model=None, fallback=True, effort='medium', route='auto', purpose='', json_mode=False):
    """先 Claude（route='auto' 且能用时）；Claude 失败 / 撞上限 / route='cheap'（一次性的大批量活）走下面原来的路线。
    fallback=False：不涉政、又有 DeepSeek 的路时，DeepSeek 失败就当失败，不回 Gemini。
    给证据卡这种大批量的用：并发高的时候限流一来，悄悄全回 Gemini 会贵 3～5 倍；失败的块下次再做就是了。
    例外：被内容审核拦了的照样回 Gemini（那是词表漏掉的涉政内容，重试也没用；10-12 全量做卡时遇到）。
    没有 Gemini 可退（只有 DeepSeek 的电脑）：涉政的也给 DeepSeek（审核拦了就是这次没拿到，status() 里记着），不会因为「没有 Gemini key」整段失败。"""
    if _override:
        return _override(prompt)
    if route == 'auto' and claude_on():
        try:
            return claude(prompt, effort, purpose=purpose)[0]
        except Exception as e:                               # noqa: BLE001  接着走原来的路线
            with _lock:
                _state['last_error'] = f'{type(e).__name__}: {e}'[:300]
    if route == 'auto':
        text = _codex_try(prompt, effort, purpose)
        if text:
            return text
    b = backend()
    if b == 'verbatim':
        return _verbatim(prompt, model)
    if b != 'gemini':
        raise RuntimeError(f'不认识的 EPHEMERIS_LLM：{b}（只有 gemini / verbatim）')
    if cheap_on():
        alone = not gemini_ready()
        guarded = len(political(prompt)) >= GUARD_MIN and not alone
        why = {}
        text = _cheap(prompt, why, force=alone, json_mode=json_mode, purpose=purpose)
        if text:
            return text
        if alone:
            raise RuntimeError('DeepSeek 这次没拿到' + ('（内容审核拦了，这段没有别的模型可换）' if why.get('moderated') else
                                                     f'：{_cheap_state["last_error"] or "没有能用的路"}'))
        if not guarded and not fallback and not why.get('moderated'):
            raise RuntimeError('DeepSeek 这次没拿到（限流 / 断线），按要求不回 Gemini')
    return _gemini(prompt, model)


# 只走指定的一家（研究版，2026-10-29）：IRB 和同意书写明了数据经过哪些厂商，悄悄换路由等于违反研究方案。
# 上面的 _call 会在 Claude / 百炼 / DeepSeek 官方 / OpenRouter / Gemini 之间自动换，研究版一律不用它，只用 ask_only。
# 厂商 → 数据去哪：claude = Anthropic（本机 claude -p）；deepseek = DeepSeek 官方；aliyun = 阿里云百炼；
# openrouter = OpenRouter（再转给模型的厂商）；gemini = Google AI Studio 直连。local（本机模型）还没接。
PROVIDERS = ('claude', 'deepseek', 'aliyun', 'openrouter', 'gemini', 'local')


def ask_only(provider, model, prompt, effort='medium', json_mode=False, purpose=''):
    """用 provider 家的 model 调一次，失败抛异常：不重试、不换厂商、不换模型、不看涉政词表。"""
    if provider not in PROVIDERS:
        raise ValueError(f'不认识的厂商：{provider}（只有 {"、".join(PROVIDERS)}）')
    if _override:
        return _override(prompt)
    if provider == 'claude':
        with using(model):
            return claude(prompt, effort, purpose=purpose)[0]
    if provider == 'gemini':
        return _gemini_direct(prompt, model)
    if provider == 'local':
        raise RuntimeError('本机模型还没接：「只用本机模型」的研究现在跑不了')
    route = next((r for r in _cheap_routes(all_routes=True) if r[0] == provider), None)
    if not route:
        raise RuntimeError(f'没有 {provider} 的 key')
    return _cheap_post(route[:3] + (model,) + route[4:], prompt, json_mode, purpose)


# ------------------------------------------------------------------ 大模型（只有交接包用）
# 2026-10-07 用户要求：交接包（export.handoff）点一下请「类似 Claude Opus 4.6」的来写，一年一次，贵一点没关系。
# 这是唯一一处把日记发给 Gemini 以外的服务（CLAUDE.md 规矩 4 的例外）。key 跟 Verbatim 共用一把 OpenRouter 的。
STRONG_MODEL = 'anthropic/claude-opus-4.6'
OPENROUTER = 'https://openrouter.ai/api/v1/chat/completions'


STRONG_CLAUDE = 'claude-opus-5-5[1m]'      # 交接包一次读一年的日记，要 100 万 token 的窗口


def strong_model():
    """交接包用哪个模型（界面上显示的名字）：.env 指定了就用它（走 OpenRouter），Claude 能用就是本机 Opus 5.5 [1m]。"""
    own = (idx.env('EPHEMERIS_STRONG_MODEL') or '').strip()
    return own or (STRONG_CLAUDE if claude_on() else STRONG_MODEL)


def openrouter_key():
    return key('OPENROUTER_API_KEY', 'openrouter_key')


def ask_strong(prompt, max_tokens=16000):
    """调一次大模型，返回 (文本, 这一笔实际多少美元 / None)。没 key、报错、返回空都抛异常（上层要让用户看到原因）。
    先走本机 Claude Opus 5.5 [1m]（订阅额度，不另花钱，所以金额是 None；2026-10-14 用户点头），用不了才走 OpenRouter。"""
    if _override:
        return _override(prompt), None
    if claude_on() and not (idx.env('EPHEMERIS_STRONG_MODEL') or '').strip():
        try:
            with using(STRONG_CLAUDE):
                return claude(prompt, 'high', purpose='handoff')[0], None
        except Exception as e:                               # noqa: BLE001
            with _lock:
                _state['last_error'] = f'{type(e).__name__}: {e}'[:300]
    return _strong_openrouter(prompt, max_tokens)


def _strong_openrouter(prompt, max_tokens):
    import requests
    key = openrouter_key()
    if not key:
        raise RuntimeError('没有 OpenRouter key：在 ephemeris/.env 里填 OPENROUTER_API_KEY（只有交接包要用）')
    model = (idx.env('EPHEMERIS_STRONG_MODEL') or '').strip() or STRONG_MODEL
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


def ask(prompt, model=None, fallback=True, effort='medium', route='auto', purpose='', json_mode=False):
    """调一次模型，返回文本；失败返回 None，原因记在 status() 里（界面能看到）。
    输出里冒出材料里没有的外文词：重来一次；还这样就照收（总比没有强），次数记在 status()['stray']。"""
    with _lock:
        _state['calls'] += 1
    try:
        text = _call(prompt, model, fallback, effort, route, purpose, json_mode)
    except Exception as e:                                   # noqa: BLE001
        with _lock:
            _state['failures'] += 1
            _state['last_error'] = f'{type(e).__name__}: {e}'[:300]
        return None
    if stray(text, prompt):
        with _lock:
            _state['stray'] += 1
        try:
            again = _call(prompt, model, fallback, effort, route, purpose, json_mode)
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


def ask_json(prompt, want=dict, tries=2, model=None, ok=None, fallback=True, effort='medium', route='auto', purpose=''):
    """调模型并解析 JSON；解析失败或 ok(结果) 不满意就重来，最多 tries 次。"""
    for _ in range(tries):
        out = parse_json(ask(prompt, model, fallback, effort, route, purpose, json_mode=want is dict), want)
        if out is not None and (ok is None or ok(out)):
            return out
    return None


def use(fn):
    """测试用：换成一个假的后端（fn(prompt) -> str）。use(None) 换回来。"""
    global _override
    _override = fn


def chat_backend():
    """聊天页（问）现在用谁写：claude | deepseek | gemini。菜单里列哪家的模型跟着它。"""
    return 'claude' if claude_on() else 'deepseek' if cheap_on() else 'gemini'


def choices():
    """聊天页能挑的模型 [(id, 名字)]，第一个是默认。Gemini 那条没得挑（空）。"""
    b = chat_backend()
    if b == 'claude':
        return CLAUDE_CHOICES
    if b == 'deepseek':
        ask = providers.pick('ASK')                           # 指定了「问」的模型：排第一（默认）
        if ask and ask[0] != 'deepseek':
            return ((':'.join(ask), f"{providers.PRESETS[ask[0]]['label']} · {ask[1]}"),) + DEEPSEEK_CHOICES
        own = ask[1] if ask else deepseek_model()
        if own in dict(DEEPSEEK_CHOICES):
            return tuple(sorted(DEEPSEEK_CHOICES, key=lambda x: x[0] != own))
        return ((own, own),) + DEEPSEEK_CHOICES
    return ()


def status():
    return {'backend': backend(), **_state, 'chat': chat_backend(), 'gemini_ready': gemini_ready(), 'api_week': api_week(),
            'claude': {'on': claude_on(), 'model': CLAUDE_MODEL, 'paused_until': _paused.get('claude'), 'week': claude_week()},
            'codex': {'on': codex_on(), 'model': codex_model(), 'paused_until': _paused.get('codex')},
            'cheap': {'on': cheap_on(), **_cheap_state,
            'routes': [r[0] for r in _cheap_routes()], 'paused': [n for n, t in _paused.items() if t > time.time()],
        'usable': bool(_cheap_routes())}}


def check():
    """启动时调一下：后端能不能建起来（不真的调模型）。返回错误说明或 None。
    有 Claude 或 DeepSeek 就算能用（只有 DeepSeek 的电脑没有 Gemini key，不该报「不可用」）。"""
    if backend() != 'verbatim' and (claude_on() or cheap_on()):
        return None
    try:
        if backend() == 'verbatim':
            _getaudio()
            from reflect import _call_model      # noqa: F401
        else:
            _client()
    except Exception as e:                                   # noqa: BLE001
        return f'{type(e).__name__}: {e}'
    return None
