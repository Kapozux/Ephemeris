"""接哪些模型（11-03，用户：「参考市面比较好的开源的，怎么接不同模型」）：照 Khoj / LobeChat / Open WebUI 的「接口 + 模型清单」，
加 Aider 那样按角色分模型。不引 LiteLLM：各家都兼容 OpenAI 的 /chat/completions，自己这一层够薄。

    PRESETS              几家常见的接口（地址、.env 里 key 叫什么、国内不国内、默认用哪个模型），外加「自定义」
    configured()         哪几家填了 key：[(名字, 地址, key)]
    models(name)         问这家 /models 有哪些模型（设置页拉列表；不花钱）
    pick(role)           某个角色指定的 (接口名, 模型)：.env 的 EPHEMERIS_MODEL_<角色>=接口名:模型；没指定 None
    info(model)          模型资料：能不能调工具、上下文、价格（不认识的给 None，让人自己判断）
    routes()             写东西 / 轻活走的路（llm._cheap_routes 用）：指定了的排最前，后面是填了 key 的各家默认模型

角色（Claude 订阅永远先上，这里只管它用不了之后）：
    TEXT   写东西、做卡、打分、核出处这些一次性的活（原来的「省钱路由」）
    ASK    「问」：要能调工具（自己连着查几十步）
    EMBED  证据卡「按意思搜」的向量

key 只放 .env（不进仓库、不回传页面）。时政词那条规矩照旧在 llm._cheap 里对整条路生效，不按模型分。
"""
import index as idx

# 名字 → 显示名、地址、key 的环境变量（再加 Verbatim 那边的配置名，llm.key 会去借）、国内服务商、默认模型、每次请求多带的参数
PRESETS = {
    'aliyun': {'label': '阿里云百炼', 'base': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'env': 'DASHSCOPE_API_KEY',
               'alt': 'dashscope_key', 'cn': True, 'model': 'deepseek-v4-flash-0731', 'extra': {'enable_thinking': False},
               'model_env': 'EPHEMERIS_CHEAP_MODEL'},
    'deepseek': {'label': 'DeepSeek 官方', 'base': 'https://api.deepseek.com/v1', 'env': 'DEEPSEEK_API_KEY', 'alt': 'deepseek_key',
                 'cn': True, 'model': 'deepseek-v4-flash', 'extra': {}, 'model_env': 'EPHEMERIS_DEEPSEEK_MODEL'},
    'openrouter': {'label': 'OpenRouter', 'base': 'https://openrouter.ai/api/v1', 'env': 'OPENROUTER_API_KEY', 'alt': 'openrouter_key',
                   'cn': False, 'model': 'deepseek/deepseek-v4-flash',
                   'extra': {'reasoning': {'enabled': False}, 'usage': {'include': True}}},
    'siliconflow': {'label': '硅基流动', 'base': 'https://api.siliconflow.cn/v1', 'env': 'SILICONFLOW_API_KEY', 'cn': True,
                    'model': 'deepseek-ai/DeepSeek-V3', 'extra': {}},
    'moonshot': {'label': 'Kimi（月之暗面）', 'base': 'https://api.moonshot.cn/v1', 'env': 'MOONSHOT_API_KEY', 'cn': True,
                 'model': 'kimi-k2-0905-preview', 'extra': {}},
    'zhipu': {'label': '智谱 GLM', 'base': 'https://open.bigmodel.cn/api/paas/v4', 'env': 'ZHIPU_API_KEY', 'cn': True,
              'model': 'glm-4.6', 'extra': {}},
    'together': {'label': 'Together AI', 'base': 'https://api.together.xyz/v1', 'env': 'TOGETHER_API_KEY', 'cn': False,
                 'model': 'Qwen/Qwen3-235B-A22B-Instruct-2507-tput', 'extra': {}},
    # 自己填地址的：自己租的 vLLM、别的转发站……地址写到 /v1 为止（Open WebUI 的坑：少了 /v1 列表是空的，多了变 /v1/v1）
    'custom': {'label': '自定义（OpenAI 兼容）', 'base': None, 'base_env': 'EPHEMERIS_CUSTOM_BASE_URL', 'env': 'EPHEMERIS_CUSTOM_API_KEY',
               'cn': None, 'model': None, 'model_env': 'EPHEMERIS_CUSTOM_MODEL', 'extra': {}},
}
ORDER = tuple(PRESETS)                       # 默认轮流先上的顺序：前三家跟以前一样
ROLES = ('TEXT', 'ASK', 'EMBED')
ROLE_LABEL = {'TEXT': '写东西、做卡、打分', 'ASK': '「问」（要能调工具）', 'EMBED': '证据卡的向量'}

# 模型资料（照 Aider 的 model metadata）：tools 能不能调工具，ctx 上下文（千 token），price（美元 / 百万 token：没命中缓存的输入、命中的、输出）。
# 按模型名里的一段认（不分家）；不在表里的 info() 给 None：设置页显示「不知道」，不替人判断。
MODEL_INFO = {
    'deepseek-v4-flash': {'tools': True, 'ctx': 128, 'price': (0.14, 0.014, 0.28)},
    'deepseek-chat': {'tools': True, 'ctx': 128, 'price': (0.27, 0.07, 1.10)},
    'deepseek-reasoner': {'tools': False, 'ctx': 128, 'price': (0.55, 0.14, 2.19)},
    'deepseek-v3': {'tools': True, 'ctx': 128, 'price': None},
    'deepseek-v4': {'tools': True, 'ctx': 128, 'price': None},     # v4-pro、v4.1 这些：上面认不出具体型号时落到这条
    'kimi-k2': {'tools': True, 'ctx': 256, 'price': (0.60, 0.15, 2.50)},
    'qwen3-235b': {'tools': True, 'ctx': 256, 'price': None},
    'qwen3-coder': {'tools': True, 'ctx': 256, 'price': None},
    'qwen3-max': {'tools': True, 'ctx': 256, 'price': None, 'open': False},
    'qwen3': {'tools': True, 'ctx': 128, 'price': None},
    'glm-4.6': {'tools': True, 'ctx': 200, 'price': (0.60, 0.11, 2.20)},
    'glm-4.5': {'tools': True, 'ctx': 128, 'price': None},
    'gpt-oss': {'tools': True, 'ctx': 128, 'price': None},
    'llama-4': {'tools': True, 'ctx': 1000, 'price': None},
    'embedding': {'tools': False, 'embed': True},
    'bge-': {'tools': False, 'embed': True},
}


def base(name):
    p = PRESETS[name]
    b = p['base'] or idx.env(p.get('base_env', '')) or ''
    return b.rstrip('/')


def key_for(name):
    p = PRESETS[name]
    if p.get('alt'):
        import llm                                           # 前三家照旧能借 Verbatim 的配置
        return llm.key(p['env'], p['alt'])
    return idx.env(p['env'])


def default_model(name):
    p = PRESETS[name]
    return (idx.env(p['model_env']) if p.get('model_env') else '') or p['model']


def configured():
    """填了 key（自定义还要有地址）的那几家：[(名字, 地址, key)]，按 ORDER。"""
    return [(n, base(n), k) for n in ORDER for k in [key_for(n)] if k and base(n)]


def pick(role):
    """.env 的 EPHEMERIS_MODEL_<角色>=接口名:模型 → (接口名, 模型)；没写、写错、那家没 key → None。"""
    raw = (idx.env(f'EPHEMERIS_MODEL_{role}') or '').strip()
    name, _, model = raw.partition(':')
    if name not in PRESETS or not model.strip() or not key_for(name) or not base(name):
        return None
    return name, model.strip()


def info(model):
    m = (model or '').lower()
    hit = next((v for k, v in MODEL_INFO.items() if k in m), None)
    return dict(hit) if hit else None


def price(model):
    return ((info(model) or {}).get('price')) or None


def route(name, model=None):
    """一条路：(名字, 地址, key, 模型, 多带的参数) —— 跟 llm 里原来的写法一样。"""
    return (name, base(name), key_for(name), model or default_model(name), dict(PRESETS[name]['extra']))


def routes():
    """写东西走的路。指定了 TEXT 的排最前（用指定的模型），后面是填了 key 的各家默认模型（没默认模型的自定义要指定了才用）。"""
    picked = pick('TEXT')
    out = [route(*picked)] if picked else []
    for name, _, _ in configured():
        if picked and name == picked[0]:
            continue
        if default_model(name):
            out.append(route(name))
    return out


def ask_route():
    """「问」在没有 Claude 时走哪条：指定了 ASK 就它；没指定就跟写东西同一条（以前就是这样）。"""
    picked = pick('ASK')
    if picked:
        return route(*picked)
    r = routes()
    return r[0] if r else None


def models(name, timeout=20):
    """这家 /models 列出来的模型 id（排好序）。连不上 / key 不对抛 RuntimeError。"""
    import requests
    b, k = base(name), key_for(name)
    if not b or not k:
        raise RuntimeError('没填地址或 key')
    r = requests.get(b + '/models', timeout=timeout, headers={'Authorization': f'Bearer {k}'})
    if r.status_code != 200:
        raise RuntimeError(f'{PRESETS[name]["label"]} 返回 {r.status_code}：{r.text[:120]}')
    return sorted({m.get('id') for m in (r.json().get('data') or []) if m.get('id')})


def summary():
    """设置页看的：每家填没填、各角色指定了什么（不含 key）。"""
    have = {n for n, _, _ in configured()}
    return {'providers': [{'name': n, 'label': p['label'], 'ready': n in have, 'cn': p['cn'], 'env': p['env'],
                           'model': default_model(n), 'custom': p['base'] is None, 'base': base(n) if p['base'] is None else p['base']}
                          for n, p in PRESETS.items()],
            'roles': {r: {'label': ROLE_LABEL[r], 'pick': ':'.join(pick(r)) if pick(r) else '',
                          'info': info(pick(r)[1]) if pick(r) else None} for r in ROLES}}


def env_names():
    """设置页能写进 .env 的名字：各家的 key、自定义地址 / 模型、三个角色。"""
    out = [p['env'] for p in PRESETS.values()] + ['EPHEMERIS_CUSTOM_BASE_URL', 'EPHEMERIS_CUSTOM_MODEL']
    return tuple(dict.fromkeys(out + [f'EPHEMERIS_MODEL_{r}' for r in ROLES]))

