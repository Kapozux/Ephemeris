"""参与者自己的决定：哪些窗口不交、哪条证据删掉、退不退出、交过没有。只存在参与者这边（data/<pid>.consent.json）。

算指标（participant.load 跳过不交的窗口）、导出（跳过退出的人、删掉的证据）都只认这里。
研究者那边只拿到计数（删了几个窗口、几条证据，Port 的做法），拿不到删的是什么。
"""
import json
import os
from datetime import datetime

from research import DATA

EMPTY = {'excluded_convs': [], 'hidden_evidence': [], 'withdrawn': None, 'submitted': None}


def _path(pid):
    return os.path.join(DATA, f'{pid}.consent.json')


def get(pid):
    path = _path(pid)
    return dict(EMPTY, **json.load(open(path, encoding='utf-8'))) if os.path.exists(path) else dict(EMPTY)


def _save(pid, d):
    os.makedirs(DATA, exist_ok=True)
    tmp = _path(pid) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(pid))


def _toggle(pid, key, item, on):
    d = get(pid)
    items = set(d[key])
    (items.add if on else items.discard)(item)
    d[key] = sorted(items)
    d['submitted'] = None                       # 改了就得重新确认
    _save(pid, d)
    return d


def exclude(pid, conv, on=True):
    """这个窗口不交：整个窗口从所有指标里拿掉（不只是不给看原话）。"""
    return _toggle(pid, 'excluded_convs', conv, on)


def hide(pid, msg, on=True):
    """这条原话不当证据交出去（指标照算）。"""
    return _toggle(pid, 'hidden_evidence', msg, on)


def withdraw(pid):
    d = dict(get(pid), withdrawn=datetime.now().isoformat(timespec='seconds'), submitted=None)
    _save(pid, d)
    return d


def submitted(pid, receipt):
    d = dict(get(pid), submitted=receipt)
    _save(pid, d)
    return d


def counts(pid):
    """给研究者的：只有数，没有内容。"""
    d = get(pid)
    return {'excluded_windows': len(d['excluded_convs']), 'hidden_evidence': len(d['hidden_evidence']),
            'withdrawn': bool(d['withdrawn'])}
