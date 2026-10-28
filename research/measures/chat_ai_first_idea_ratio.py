"""想法由 AI 先提的比例：一期里「有决定」的窗口中，最后采用的那个方案是 AI 先提出来的占几成。（试做）

每个窗口让模型判一次：有没有决定、谁先提的、提出它的那句原话。原话必须逐字出现在那个窗口里才算数，
对不上的不计入（verified=False，页面上标出来）。判过的存在 data/<pid>.judge.json，重跑不再花钱；
改了提示词就改 VERSION，旧的判断自动作废。一次性的批量活走省钱路线（规矩 4）。
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import research

DESCRIPTION = ('Among chat windows that contain a decision the participant goes with, the share where that option '
               'was first proposed by the AI. Judged per window by an LLM; counted only if its supporting quote '
               'matches the transcript verbatim. Experimental.')
KIND = 'interaction'
VERSION = 'v1'
BATCH = 8

PROMPT = """Below are {n} chat windows between a person ("H") and an AI assistant ("A").
For each window decide: does it contain a decision or plan that the person ends up going with?
If yes, who FIRST proposed that option: "user" or "ai"? Count the AI as the proposer if the AI suggested it first and
the person then agreed, even if the person later restates it. If there is no such decision, answer "none".
Also give "msg": the number of the message where the option was first proposed, and "quote": one sentence copied
EXACTLY, character for character, from that message. For "none", use msg -1 and an empty quote.

{windows}

Output only JSON: {{"judgments": [{{"window": "W1", "idea_from": "user|ai|none", "msg": 3, "quote": "..."}}]}}"""


def _transcript(msgs):
    return '\n'.join(f'  [{i}] {"H" if m.role == "human" else "A"}: {m.text[:600]}' for i, m in enumerate(msgs))


def _judge(batch):
    import llm
    text = '\n\n'.join(f'W{i + 1}:\n{_transcript(msgs)}' for i, (_, msgs) in enumerate(batch))
    d = llm.parse_json(llm.ask(PROMPT.format(n=len(batch), windows=text), route='cheap', json_mode=True,
                               purpose='research-idea'), dict) or {}
    got = {j.get('window'): j for j in d.get('judgments') or [] if isinstance(j, dict)}
    out = {}
    for i, (conv, msgs) in enumerate(batch):
        j = got.get(f'W{i + 1}')
        if not j:
            continue
        frm, k, quote = j.get('idea_from'), j.get('msg', -1), (j.get('quote') or '').strip()
        hit = msgs[k] if isinstance(k, int) and 0 <= k < len(msgs) and quote and quote in msgs[k].text else \
            next((m for m in msgs if quote and quote in m.text), None)
        out[conv] = {'version': VERSION, 'idea_from': frm if frm in ('user', 'ai', 'none') else 'none',
                     'msg': hit.id if hit else None, 'quote': quote,
                     'verified': frm == 'none' or hit is not None}
    return out


def judgments(person):
    path = os.path.join(research.DATA, f'{person.pid}.judge.json')
    cache = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
    todo = [(c, ms) for c, ms in person.convs.items() if cache.get(c, {}).get('version') != VERSION]
    if todo:
        research.ledger()
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        with ThreadPoolExecutor(6) as pool:
            for got in pool.map(_judge, batches):
                cache.update(got)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    return cache


def compute(person, study):
    judged = judgments(person)
    out = {}
    for p in person.periods:
        convs = {m.conv for m in p.said()}
        js = [judged[c] for c in convs if c in judged and judged[c]['idea_from'] != 'none']
        ok = [j for j in js if j['verified']]
        ai = [j for j in ok if j['idea_from'] == 'ai']
        ev = [{'msg': j['msg'], 'why': 'AI first proposed the option the participant went with'} for j in ai[:3]]
        ev += [{'msg': None, 'why': f'{len(js) - len(ok)} judgment(s) dropped: quote not found verbatim'}] if len(ok) < len(js) else []
        out[p.number] = (len(ai) / len(ok) if ok else None, ev)
    return out
