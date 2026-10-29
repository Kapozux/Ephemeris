"""覆盖报告：每个人每一期的数据覆盖了什么、缺了什么。不是指标，是给研究者判断「指标变了是人变了还是数据变了」用的。

换平台、换模型都会让指标跟着变（同一个人在 Claude 和 Gemini 上说话方式不一样，GPT-4o 换到 5 回复长度就变了）；
一段时间没数据不等于没聊（可能在别的账号、手机 App、删掉了）。所以每期报：
    各平台消息占比、有数据的天数、最长连续没数据的天数、回复里知道是哪个模型写的占比、各模型的回复数。
「不知道」照实报，不猜（Gemini、Cursor、claude.ai 官方导出不记模型）。参与者自报还用了哪些没导进来的账号 / App，
在 consent 里（提交前审阅页填），导出时进 manifest。
"""
from collections import Counter
from datetime import timedelta

COLS = ['chat_cov_days_with_data', 'chat_cov_max_gap_days', 'chat_cov_main_platform', 'chat_cov_main_platform_share',
        'chat_cov_model_known_share']
LONG_COLS = ['pid', 'period_number', 'platform', 'messages', 'share', 'replies', 'replies_model_known', 'models']


def _gap(days, start, end):
    """这期里最长连续几天一条消息都没有（期初、期末的空白也算）。"""
    longest, prev = 0, start - timedelta(days=1)
    for d in sorted(days) + [end]:
        longest = max(longest, (d - prev).days - 1)
        prev = d
    return longest


def period(p):
    """一期的覆盖摘要（并进长表的 chat_cov_* 列）。"""
    by = Counter(m.source for m in p.msgs)
    replies = [m for m in p.msgs if m.role == 'assistant']
    main, n = by.most_common(1)[0] if by else ('', 0)
    days = {m.at.date() for m in p.msgs}
    return {'chat_cov_days_with_data': len(days),
            'chat_cov_max_gap_days': _gap(days, p.start.date(), p.end.date()),
            'chat_cov_main_platform': main,
            'chat_cov_main_platform_share': n / len(p.msgs) if p.msgs else None,
            'chat_cov_model_known_share': sum(1 for m in replies if m.model) / len(replies) if replies else None}


def long_rows(pid, p):
    """一期 × 一个平台一行：消息数、占比、回复里知道模型的、各模型几条。"""
    total = len(p.msgs)
    out = []
    for platform, n in sorted(Counter(m.source for m in p.msgs).items()):
        replies = [m for m in p.msgs if m.source == platform and m.role == 'assistant']
        models = Counter(m.model for m in replies if m.model)
        out.append({'pid': pid, 'period_number': p.number, 'platform': platform, 'messages': n, 'share': n / total,
                    'replies': len(replies), 'replies_model_known': sum(models.values()),
                    'models': ';'.join(f'{k}:{v}' for k, v in models.most_common())})
    return out
