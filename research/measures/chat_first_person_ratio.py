"""第一人称单数占比（I-talk）：这个人自己打的话里，第一人称单数占全部词的比例。

英文：I / me / my / mine / myself（含 I'm、I've 这类缩写）占英文词数；
中文：「我」（不算「我们」）占汉字数——中文没切词，汉字数大致当词数，跨语言的人只在本人内部比。
"""
import re

DESCRIPTION = ('Share of first-person singular pronouns among the participant\'s typed words. English: I, me, my, '
               'mine, myself (incl. contractions) over English words; Chinese: 我 (not 我们) over Han characters. '
               'Compare within person only.')
KIND = 'language'

EN_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
EN_FP = {'i', 'me', 'my', 'mine', 'myself'}
HAN = re.compile(r'[一-鿿]')
ZH_FP = re.compile(r'我(?!们)')


def counts(text):
    words = EN_WORD.findall(text)
    fp = sum(1 for w in words if w.lower().split("'")[0] in EN_FP)
    return fp + len(ZH_FP.findall(text)), len(words) + len(HAN.findall(text))


def compute(person, study):
    out = {}
    for p in person.periods:
        rows = [(m, *counts(m.text)) for m in p.said()]
        total = sum(n for _, _, n in rows)
        if not total:
            out[p.number] = (None, [])
            continue
        top = sorted((r for r in rows if r[2] >= 5), key=lambda r: -r[1] / r[2])[:3]
        out[p.number] = (sum(f for _, f, _ in rows) / total,
                         [{'msg': m.id, 'why': f'{f} first-person of {n} words'} for m, f, n in top])
    return out
