"""研究版调模型的唯一入口：只用研究方案里写明的厂商和模型，每次调用都记进数据流账本。

研究方案（study JSON）里写：
    "models": ["deepseek:deepseek-v4-flash"]          按顺序：前一个失败才用下一个；都在方案里，所以换也是方案允许的
    "models": ["local:任意"]                           只用本机模型（还没接，调用会报错，不会退到云上）
没写 models 的方案一律拒绝调用。名单外的厂商、模型永远不会被用到：这里不调 llm.ask / ask_json（它们会自动换），只调 llm.ask_only。

账本 data/flow.jsonl：每次尝试一行（时间、研究、参与者编号、厂商、模型、用途、送出去多少字、成没成）。不记内容。
flow() 汇总成「数据经过了哪些厂商」，导出时写进 manifest，给 IRB 的数据流说明照它写。
"""
import json
import os
import threading
from collections import Counter, defaultdict
from datetime import datetime

import research

FLOW = os.path.join(research.DATA, 'flow.jsonl')
_lock = threading.Lock()


def allowed(study):
    """研究方案里允许的 [(厂商, 模型)]。没写或写错了直接报错，不猜。"""
    import llm
    out = []
    for item in study.get('models') or []:
        provider, _, model = str(item).partition(':')
        if provider not in llm.PROVIDERS or not model:
            raise ValueError(f'研究方案 models 里的「{item}」不对：要写成 厂商:模型，厂商只有 {"、".join(llm.PROVIDERS)}')
        out.append((provider, model))
    if not out:
        raise ValueError(f'研究方案 {study.get("study")} 没写 models：不知道数据能给哪家，不调模型')
    return out


def _log(row):
    os.makedirs(os.path.dirname(FLOW), exist_ok=True)
    with _lock, open(FLOW, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')


def call(study, prompt, purpose, pid=None, effort='medium', json_mode=False):
    """按研究方案的名单调模型，返回文字。名单里的都失败了就抛异常（最后那个的原因）。"""
    import llm
    err = None
    for provider, model in allowed(study):
        row = {'ts': datetime.now().isoformat(timespec='seconds'), 'study': study.get('study'), 'pid': pid,
               'provider': provider, 'model': model, 'purpose': purpose, 'chars': len(prompt)}
        try:
            text = llm.ask_only(provider, model, prompt, effort=effort, json_mode=json_mode, purpose=purpose)
        except Exception as e:                               # noqa: BLE001  记下，试名单里的下一个
            _log(dict(row, ok=False, error=f'{type(e).__name__}: {e}'[:200]))
            err = e
            continue
        _log(dict(row, ok=True))
        return text
    raise RuntimeError(f'研究方案允许的模型都没成功：{err}')


def call_json(study, prompt, purpose, pid=None, want=dict, effort='medium'):
    import llm
    return llm.parse_json(call(study, prompt, purpose, pid, effort, json_mode=True), want)


def flow(study_name=None):
    """账本汇总：{厂商: {模型: {calls, ok, chars, participants}}}。只汇总成功送出去的字数和失败次数，不含内容。"""
    out = defaultdict(lambda: defaultdict(Counter))
    pids = defaultdict(set)
    if not os.path.exists(FLOW):
        return {}
    for line in open(FLOW, encoding='utf-8'):
        r = json.loads(line)
        if study_name and r.get('study') != study_name:
            continue
        c = out[r['provider']][r['model']]
        c['calls'] += 1
        c['ok'] += bool(r.get('ok'))
        c['chars'] += r['chars']
        if r.get('pid'):
            pids[r['provider'], r['model']].add(r['pid'])
    return {p: {m: dict(c, participants=len(pids[p, m])) for m, c in ms.items()} for p, ms in out.items()}
