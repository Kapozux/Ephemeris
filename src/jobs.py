"""后台任务：同一个名字同一时间只跑一个；状态谁都能读（前端轮询）。

    start(name, fn, *a, **fresh) → 开线程跑，True；已经在跑就不开，False
    run(name, fn, *a, **fresh)   → 在当前线程跑，返回 fn 的结果；已经在跑返回 None；出错照样往上抛
    running(name)                → bool
    names(prefix)                → 跑过的、名字以 prefix 开头的那些（「问」每段对话一个：qa:<对话>）
    get(name, **idle)            → 状态快照（给 jsonify）；从没跑过就是 running/error/started/finished + idle 那些字段
    state(name)                  → 活的状态 dict（要从外面改它时用，比如「停下」）

fn(job, *a)：job 就是状态 dict，往里写 stage / done / total / current，前端就能看到。
开跑：running=True、error=None、started=现在，再盖上 fresh（这次要清零的字段）；别的字段（上次的结果）留着。
fn 抛异常 → error='类型: 信息' 并打印；结束 → running=False、finished=现在、stage / current 清空。

为什么要有它（2026-10-02 体检）：以前 5 个任务（补日记、claude.ai 同步、Notion 同步、备份、窗口回顾）
各手写一个状态字典和一套「先看在不在跑、再开线程」—— 看和开之间没有锁，点得快就会开出两个。
"""
import threading
from datetime import datetime

_lock = threading.Lock()
_jobs = {}


def _now():
    return datetime.now().isoformat(timespec='seconds')


def state(name):
    with _lock:
        return _jobs.setdefault(name, {'running': False})


_IDLE = {'running': False, 'error': None, 'started': None, 'finished': None}


def get(name, **idle):
    with _lock:
        return {**_IDLE, **idle, **_jobs.get(name, {})}


def names(prefix):
    with _lock:
        return [n for n in _jobs if n.startswith(prefix)]


def running(name):
    with _lock:
        return bool(_jobs.get(name, {}).get('running'))


def _claim(name, fresh):
    with _lock:
        st = _jobs.setdefault(name, {'running': False})
        if st['running']:
            return None
        st.update(running=True, error=None, started=_now(), finished=None, **fresh)
        return st


def _body(st, name, fn, a, reraise):
    try:
        return fn(st, *a)
    except Exception as e:                                   # noqa: BLE001
        st['error'] = f'{type(e).__name__}: {e}'[:300]
        print(f'[{name}] 失败：{st["error"]}', flush=True)
        if reraise:
            raise
        return None
    finally:
        st.update(running=False, finished=_now(), stage=None, current=None)


def run(name, fn, *a, **fresh):
    st = _claim(name, fresh)
    return None if st is None else _body(st, name, fn, a, True)


def start(name, fn, *a, **fresh):
    st = _claim(name, fresh)
    if st is None:
        return False
    threading.Thread(target=_body, args=(st, name, fn, a, False), daemon=True, name=name).start()
    return True
