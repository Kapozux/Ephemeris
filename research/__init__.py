"""Ephemeris 研究版：把很多人的 AI 聊天记录变成研究者能直接用的数据（按研究方案算指标、和量表分数对齐、导出）。

跟 Ephemeris 同一个仓库、直接引用它的核心模块，所以核心一改这边就跟着用上。
**只许引用 CORE 里的模块**（tests/test_research_boundary.py 会查）：日记、打卡、「问」、锁、记忆这些不进研究版。
要用 Ephemeris 的新东西，就把它加进 CORE —— 这就是「选择性同步」那一步。

数据只放在 research/data/（不进仓库）：每个参与者一个库 <pid>.db（跟 chats.db 一样的表）。
仓库是公开的：这里只放代码和虚构参与者的设定，真实数据、研究者给的东西一律不放。
"""
import json
import os

CORE = ('speech', 'store', 'llm')

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get('EPHEMERIS_RESEARCH_DATA') or os.path.join(HERE, 'data')


def study(path=None):
    """读研究方案（JSON）。默认是演示用的 study.demo.json。"""
    with open(path or os.path.join(HERE, 'study.demo.json'), encoding='utf-8') as f:
        return json.load(f)


def db_path(pid):
    return os.path.join(DATA, f'{pid}.db')
