"""Ephemeris 调模型的唯一入口。

    ask(prompt)            → 文本或 None
    ask_json(prompt, want) → 解析好的 dict / list，或 None（会重试）
    parse_json(text, want) → 纯函数：从模型输出里抠 JSON（去代码块、修 LaTeX 反斜杠）
    status()               → 现在用哪个后端、能不能用、上次失败原因
    stray(text, source)    → 输出里冒出来、材料里没有的别的文字（ask 自动重来一次）

为什么要有它（2026-10-02 体检）：
- 以前 3 个模块各自 `sys.path` 伸进 getAudio 借 `reflect._call_model`，8 个地方各写一遍「抠 JSON + 重试」。
- 借来的东西会被别人改掉：10-01 Verbatim 为了省钱，把 `reflect._call_model` 改成按内容分流到阿里云 DeepSeek，
  Ephemeris 的日记、打卡回话也就跟着悄悄换了服务商 —— 没人替 Ephemeris 做过这个决定。
- 调用失败只返回 None，上层不报错：哪天借的函数改名了，日记会一篇都写不出来却没人知道。

后端（.env 的 EPHEMERIS_LLM 选）：
  gemini   默认。Gemini flash-lite，不行退一档 2.5-flash。key 用 .env 的 GEMINI_API_KEY，没有就借 Verbatim 的。
  verbatim 跟着 Verbatim 的 reflect._call_model 走（含它的省钱分流）。
测试可以 `use(假函数)` 换掉后端。
"""
import json
import os
import re
import sys
import threading

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


def backend():
    return (idx.env('EPHEMERIS_LLM') or 'gemini').strip().lower()


def _gemini(prompt, model=None):
    _getaudio()
    import config                       # getAudio/config.py：建 client 的办法和 key 的存放处
    import usage
    key = idx.env('GEMINI_API_KEY') or config.gemini_key()
    if not key:
        raise RuntimeError('没有 Gemini key：在 ephemeris/.env 设 GEMINI_API_KEY，或在 Verbatim 的设置里填')
    client = config.make_gemini_client(key)
    err = None
    for m in ((model,) if model else MODELS):
        try:
            resp = client.models.generate_content(model=m, contents=prompt)
        except Exception as e:                               # noqa: BLE001
            err = e
            continue
        try:
            usage.record_gemini(resp, m, 'ephemeris')
        except Exception:                                    # noqa: BLE001  记账失败不影响结果
            pass
        text = (resp.text or '').strip()
        if text:
            return text
        err = RuntimeError(f'{m} 返回空（多半是安全过滤）')
    raise err or RuntimeError('没有可用的模型')


def _verbatim(prompt, model=None):
    _getaudio()
    from reflect import _call_model          # 借 Verbatim 的；它改了接口这里会当场报错，不会悄悄返回空
    return _call_model(prompt)


def _call(prompt, model=None):
    if _override:
        return _override(prompt)
    b = backend()
    if b == 'verbatim':
        return _verbatim(prompt, model)
    if b != 'gemini':
        raise RuntimeError(f'不认识的 EPHEMERIS_LLM：{b}（只有 gemini / verbatim）')
    return _gemini(prompt, model)


# 天城文、阿拉伯文、西里尔、泰文、希伯来、韩文、日文假名。中文日记里出现它们，多半是模型串了语言：
# 2026-10 查到两处 ——「SVM 분류기」（韩文「分类器」）、「खासकर 9月11日」（印地语「尤其」），材料里都没有。
_STRAY = re.compile(r'[\u0900-\u097F\u0600-\u06FF\u0400-\u04FF\u0E00-\u0E7F\u0590-\u05FF\uAC00-\uD7AF\u3040-\u30FF]+')


def stray(text, source=''):
    """输出里有、材料里却没有的「别的文字」片段。材料里本来就有的（日文歌名之类）不算。"""
    return [w for w in _STRAY.findall(text or '') if w not in (source or '')]


def ask(prompt, model=None):
    """调一次模型，返回文本；失败返回 None，原因记在 status() 里（界面能看到）。
    输出里冒出材料里没有的外文词：重来一次；还这样就照收（总比没有强），次数记在 status()['stray']。"""
    with _lock:
        _state['calls'] += 1
    try:
        text = _call(prompt, model)
    except Exception as e:                                   # noqa: BLE001
        with _lock:
            _state['failures'] += 1
            _state['last_error'] = f'{type(e).__name__}: {e}'[:300]
        return None
    if stray(text, prompt):
        with _lock:
            _state['stray'] += 1
        try:
            again = _call(prompt, model)
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


def ask_json(prompt, want=dict, tries=2, model=None, ok=None):
    """调模型并解析 JSON；解析失败或 ok(结果) 不满意就重来，最多 tries 次。"""
    for _ in range(tries):
        out = parse_json(ask(prompt, model), want)
        if out is not None and (ok is None or ok(out)):
            return out
    return None


def use(fn):
    """测试用：换成一个假的后端（fn(prompt) -> str）。use(None) 换回来。"""
    global _override
    _override = fn


def status():
    return {'backend': backend(), **_state}


def check():
    """启动时调一下：后端能不能建起来（不真的调模型）。返回错误说明或 None。"""
    try:
        _getaudio()
        if backend() == 'verbatim':
            from reflect import _call_model      # noqa: F401
        else:
            import config
            if not (idx.env('GEMINI_API_KEY') or config.gemini_key()):
                return '没有 Gemini key'
            config.make_gemini_client                     # noqa: B018  属性还在就行
    except Exception as e:                                   # noqa: BLE001
        return f'{type(e).__name__}: {e}'
    return None
