"""演示用的 5 个虚构参与者：人设、每一期（两周）的状态、埋下的故事线。全是编的，不对应任何真人。

每一期的状态喂给生成聊天的模型（synth.py），CES-D 也从状态算出来。埋下的故事线（PLANTS）
是「流程自检」的标准答案：系统要从聊天里把它们找回来。这只能说明流程没坏，不能说明指标有效——
状态、聊天、分数都是我们自己配的。
"""
import random

START = '2026-03-02'        # 第一次 CES-D（基线）那天；之后每 14 天一次
PERIODS = 13                # 13 期 → 14 次 CES-D；Merve 的代码要求每人至少 10 期
PERIOD_DAYS = 14


def _series(base, bumps):
    """每期一个值：base 打底，bumps = {期号(1 起): 值} 覆盖。"""
    return [bumps.get(i, base) for i in range(1, PERIODS + 1)]


PEOPLE = [
    {
        'pid': 'P01', 'name': 'Maya', 'age': 24, 'tz': 'America/Los_Angeles', 'lang': 'English',
        'platforms': ['chatgpt', 'claude'],
        'bio': 'Second-year master\'s student in urban planning in Los Angeles. Uses AI for coursework, '
               'cooking, and sometimes to talk things through.',
        'severity': _series(0.15, {5: 0.3, 6: 0.6, 7: 0.65, 8: 0.55, 9: 0.4, 10: 0.3, 11: 0.25, 12: 0.2, 13: 0.2}),
        'sleep': _series('normal', {6: 'poor', 7: 'poor', 8: 'poor'}),
        'reliance': _series('low', {}),
        'events': {5: 'Breaks up with her partner of three years at the start of this period.',
                   6: 'Struggles to focus on her thesis; skips a studio class.',
                   8: 'Starts seeing a campus counselor.',
                   10: 'Thesis draft back on track; spends weekends hiking again.'},
    },
    {
        'pid': 'P02', 'name': 'Daniel', 'age': 35, 'tz': 'America/New_York', 'lang': 'English',
        'platforms': ['gemini', 'chatgpt'],
        'bio': 'Backend engineer at a logistics company in Philadelphia, married, one kid. '
               'Uses AI mostly for work code, trip planning and recipes.',
        'severity': _series(0.12, {4: 0.15, 9: 0.15}),
        'sleep': _series('normal', {}),
        'reliance': _series('low', {}),
        'events': {4: 'Family trip to the Outer Banks.', 9: 'Busy release week at work.'},
    },
    {
        'pid': 'P03', 'name': '林晓', 'age': 21, 'tz': 'Asia/Shanghai', 'lang': 'Simplified Chinese',
        'platforms': ['claude', 'gemini'],
        'bio': '杭州一所大学的大三学生，学会计，在纠结要不要转去做数据分析。什么事都爱问 AI。',
        'severity': _series(0.2, {9: 0.3, 10: 0.35, 11: 0.35, 12: 0.3, 13: 0.3}),
        'sleep': _series('normal', {}),
        'reliance': ['low', 'low', 'low', 'medium', 'medium', 'medium', 'high', 'high', 'high', 'high',
                     'high', 'high', 'high'],
        'events': {4: '开始准备暑期实习的申请。', 7: '拿到两个实习 offer，要选一个。',
                   9: '实习开始，觉得自己什么都定不下来。'},
    },
    {
        'pid': 'P04', 'name': 'Wei', 'age': 29, 'tz': 'America/Vancouver', 'lang': 'English, with some Simplified Chinese mixed in',
        'platforms': ['claude'],
        'bio': 'Product designer in Vancouver who moved from Shanghai at 18. Writes to AI in English, '
               'switching to Chinese for family matters.',
        'severity': _series(0.15, {3: 0.45, 4: 0.55, 5: 0.5, 6: 0.45, 7: 0.35, 8: 0.3, 9: 0.2, 10: 0.15}),
        'sleep': _series('normal', {3: 'poor', 4: 'poor', 5: 'poor'}),
        'reliance': _series('low', {}),
        'events': {3: 'Laid off with half of the design team.', 4: 'Applying for jobs; tense calls with parents.',
                   7: 'Two final-round interviews.', 9: 'Starts a new job at a fintech startup.'},
    },
    {
        'pid': 'P05', 'name': 'Hannah', 'age': 41, 'tz': 'America/Denver', 'lang': 'English',
        'platforms': ['chatgpt'],
        'bio': 'Night-shift nurse turned day-shift clinic manager in Denver, divorced, lives alone. '
               'Talks to ChatGPT about work, her garden, and increasingly about how she feels.',
        'severity': [0.1, 0.12, 0.15, 0.18, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.55],
        'sleep': _series('normal', {8: 'poor', 9: 'poor', 10: 'poor', 11: 'poor', 12: 'poor', 13: 'poor'}),
        'reliance': _series('low', {}),
        'events': {6: 'Her closest friend moves to another state.',
                   9: 'Stops going to her Sunday garden club.'},
    },
]

# 埋下的故事线：哪个指标、哪几期、比本人平时高还是低。流程自检看系统能不能找回来。
PLANTS = [
    {'pid': 'P01', 'measure': 'chat_mean_daily_overnight_ratio', 'periods': [6, 7, 8], 'direction': 'up',
     'story': 'After the breakup, sleep falls apart and she chats late at night.'},
    {'pid': 'P01', 'measure': 'chat_first_person_ratio', 'periods': [6, 7, 8], 'direction': 'up',
     'story': 'Her messages turn inward while she is low.'},
    {'pid': 'P03', 'measure': 'chat_ai_first_idea_ratio', 'periods': [7, 8, 9, 10, 11, 12, 13], 'direction': 'up',
     'story': 'She increasingly lets the AI propose what to do and goes along with it.'},
    {'pid': 'P04', 'measure': 'chat_mean_daily_overnight_ratio', 'periods': [3, 4, 5], 'direction': 'up',
     'story': 'After the layoff he is up late applying for jobs and worrying.'},
    {'pid': 'P05', 'measure': 'chat_mean_daily_overnight_ratio', 'periods': [8, 9, 10, 11, 12, 13], 'direction': 'up',
     'story': 'As she gets lonelier, she talks to ChatGPT late at night.'},
]

CONTROL = ['P02']   # 什么都没埋：自检看它会不会被误报


def cesd(person, seed=0):
    """14 次 CES-D（第 0 次是基线）：0–60，由每期的严重度加一点噪声。基线用第 1 期的严重度。"""
    rng = random.Random(f"{person['pid']}-{seed}")
    sev = [person['severity'][0]] + person['severity']
    return [max(0, min(60, round(4 + 44 * s + rng.gauss(0, 2.5)))) for s in sev]


def by_pid(pid):
    return next(p for p in PEOPLE if p['pid'] == pid)
