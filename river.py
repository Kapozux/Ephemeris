"""话题河流：一年里精力是怎么在几件大事之间流动的。

主题账本里有 800 多个具体主题（「经济 IA 改稿」「USACO 冲刺」），直接画会糊成一片。
先让模型把每个主题归到下面 8 个大类之一（外加「其他」），归一次存下来，之后只归新主题。
每周算一次：各大类占了多少「主题·天」（同一主题同一天只算一次，AI 和手写两边都算）。

颜色 = 类别的身份，固定顺序；类别顺序也是河流从下往上的叠放顺序。
"""
import json
import re
from collections import defaultdict
from datetime import date as _date, timedelta

import index as idx

CATS = ['IB 课业', '标化与竞赛', '升学申请', '编程与项目', '感情', '游戏娱乐', '家庭与人际', '身心健康', '其他']

CLASSIFY_PROMPT = """把下面每个主题归到一个大类。主题来自一个人的日记。

大类（只能用这些名字）：
- IB 课业：学校的课、作业、考试、复习、IA / EE / TOK / CAS、各科知识
- 标化与竞赛：SAT、托福、USACO 等标化考试和竞赛
- 升学申请：文书、选校、申请规划、活动列表、签证、留学政策
- 编程与项目：自己做的软件和项目、编程学习、AI 工具折腾、电脑和软件配置
- 游戏娱乐：玩游戏、AI 推演游戏、看视频、爱好消遣
- 感情：喜欢的人、表白、恋爱、感情复盘
- 家庭与人际：父母家人、同学朋友、室友、社团、学生会、校园生活
- 身心健康：身体、看病、睡眠、运动、情绪和心理状态、ADHD
- 其他：时间管理、生活杂事、购物、做饭、以上都不像的

只输出 JSON 对象，键是编号，值是大类名，不要 markdown：
{{"12": "IB 课业", ...}}

{items}
"""


def ensure_table():
    with idx.LOCK:
        db = idx.connect()
        db.execute('CREATE TABLE IF NOT EXISTS theme_cats (theme_id INTEGER PRIMARY KEY, cat TEXT NOT NULL)')
        db.commit()


def _q(sql, *a):
    with idx.LOCK:
        return [dict(r) for r in idx.connect().execute(sql, a).fetchall()]


def classify(call_model, batch=80, say=print):
    """给还没归类的主题归类。名字不够判断时带上它第一次出现那天的 note。"""
    ensure_table()
    todo = _q("""SELECT t.id, t.name, t.desc,
                        (SELECT note FROM theme_days d WHERE d.theme_id=t.id ORDER BY date LIMIT 1) note
                   FROM themes t
                  WHERE t.id IN (SELECT theme_id FROM theme_days)
                    AND t.id NOT IN (SELECT theme_id FROM theme_cats)""")
    say(f'要归类 {len(todo)} 个主题')
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        lines = '\n'.join(f"{t['id']}. {t['name']}：{(t['desc'] or t['note'] or '')[:50]}" for t in chunk)
        got = {}
        for _ in range(2):
            raw = call_model(CLASSIFY_PROMPT.format(items=lines))
            m = re.search(r'\{.*\}', raw or '', re.S)
            try:
                got = {int(k): v for k, v in json.loads(m.group(0)).items()}
                break
            except (AttributeError, ValueError, TypeError):
                continue
        with idx.LOCK:
            db = idx.connect()
            for t in chunk:
                c = got.get(t['id'])
                if c in CATS:
                    db.execute('INSERT OR REPLACE INTO theme_cats VALUES (?,?)', (t['id'], c))
            db.commit()
        say(f'  {min(i + batch, len(todo))}/{len(todo)}')


def _week(d):
    x = _date.fromisoformat(d)
    return (x - timedelta(days=x.weekday())).isoformat()           # 周一


def series(start='2025-08-18', smooth=3):
    """按周：每个大类的「主题·天」数，外加每周每类最多的几个主题（悬停时看）。

    smooth = 滑动平均的周数，一周一周抖得太厉害，看不出流向。
    """
    ensure_table()
    cat = {r['theme_id']: r['cat'] for r in _q('SELECT theme_id, cat FROM theme_cats')}
    name = {r['id']: r['name'] for r in _q('SELECT id, name FROM themes')}
    rows = _q('SELECT DISTINCT theme_id, date FROM theme_days WHERE date >= ?', start)
    if not rows:
        return {'cats': CATS, 'weeks': [], 'raw': [], 'smooth': [], 'top': []}
    counts = defaultdict(lambda: defaultdict(int))
    top = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for r in rows:
        w, c = _week(r['date']), cat.get(r['theme_id'], '其他')
        counts[w][c] += 1
        top[w][c][name.get(r['theme_id'], '?')] += 1
    first, last = _date.fromisoformat(min(counts)), _date.fromisoformat(max(counts))
    weeks = []
    while first <= last:
        weeks.append(first.isoformat())
        first += timedelta(days=7)
    raw = [[counts[w][c] for c in CATS] for w in weeks]
    sm = []
    for i in range(len(weeks)):
        win = raw[max(0, i - smooth // 2): i + smooth // 2 + 1]
        sm.append([round(sum(x[k] for x in win) / len(win), 2) for k in range(len(CATS))])
    tops = [[[n for n, _ in sorted(top[w][c].items(), key=lambda kv: -kv[1])[:3]] for c in CATS] for w in weeks]
    return {'cats': CATS, 'weeks': weeks, 'raw': raw, 'smooth': sm, 'top': tops}


if __name__ == '__main__':
    import sys
    sys.path.insert(0, '.')
    import diary
    classify(diary._call_model, say=lambda m: print(m, flush=True))
    from collections import Counter
    print(Counter(r['cat'] for r in _q('SELECT cat FROM theme_cats')).most_common())
