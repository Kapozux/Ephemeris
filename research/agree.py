"""一致率：两个人（或人和模型、模型和自己）标同一批东西，对上了多少。研究版里算一致率只在这里。

    pair(pairs)    两个评分者：一致率、Cohen's κ、Gwet's AC1（名义）
    alpha(units)   任意多个评分者、允许缺：Krippendorff α（名义）
"""
from collections import Counter
from itertools import permutations


def kappa(pairs):
    """Cohen's κ 和 Gwet's AC1（两个评分者、名义）。pairs：[(a, b)]。返回 (一致率, κ, AC1)。"""
    n = len(pairs)
    if not n:
        return None, None, None
    cats = sorted({x for p in pairs for x in p})
    po = sum(a == b for a, b in pairs) / n
    pa = Counter(a for a, _ in pairs)
    pb = Counter(b for _, b in pairs)
    pe = sum(pa[c] * pb[c] for c in cats) / n / n
    k = (po - pe) / (1 - pe) if pe < 1 else 1.0
    q = len(cats)
    pi = {c: (pa[c] + pb[c]) / (2 * n) for c in cats}
    pe_g = sum(p * (1 - p) for p in pi.values()) / (q - 1) if q > 1 else 0
    ac1 = (po - pe_g) / (1 - pe_g) if pe_g < 1 else 1.0
    return po, k, ac1


def pair(pairs):
    po, k, ac1 = kappa(pairs)
    return {'n': len(pairs), 'agree': po, 'kappa': k, 'ac1': ac1}


def alpha(units):
    """Krippendorff α（名义）。units：每个单位一组标签（缺的不放），少于 2 个标签的单位不计。"""
    pairs = Counter()
    for labels in units:
        m = len(labels)
        if m < 2:
            continue
        for a, b in permutations(range(m), 2):
            pairs[labels[a], labels[b]] += 1 / (m - 1)
    n = sum(pairs.values())
    if not n:
        return None
    marg = Counter()
    for (a, _), w in pairs.items():
        marg[a] += w
    d_obs = sum(w for (a, b), w in pairs.items() if a != b) / n
    d_exp = sum(marg[a] * marg[b] for a in marg for b in marg if a != b) / (n * (n - 1))
    return 1 - d_obs / d_exp if d_exp else 1.0
