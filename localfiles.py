"""「问」也能看他电脑上的文件（2026-10-21：聊天记录里到处是日志、文书、代码，「问」只看得到聊过，看不到文件本身）。

只读，只在白名单目录里（默认 ~/Documents、~/Downloads；EPHEMERIS_FILE_ROOTS 用 os.pathsep 隔开可以改），
看不了的：隐藏的文件和目录（.env、.ssh、.git……）、密钥 / 证书 / 凭证样子的文件、数据库（chats.db 有 look 管着）、
依赖和缓存目录（node_modules、venv……，全是噪音）。符号链接按真实位置算，链到白名单外面也不行。
look.py 的 files / grep / file 三个命令用这里；回答里引文件是 [[f:路径:行号]] 或 [[f:路径:起-止]]，qa._snapshot 也用这里读那几行。
"""
import fnmatch
import os
import time
from datetime import datetime

DEFAULT_ROOTS = ('~/Documents', '~/Downloads')
_DENY_DIRS = {'node_modules', 'venv', '__pycache__', 'site-packages', 'Library'}
_DENY_FILES = ('*.pem', '*.key', '*.p12', '*.pfx', '*.keychain*', 'id_rsa*', 'id_ed25519*', '*credential*', '*secret*',
               '*.db', '*.db-*', '*.sqlite*', '*.kdbx')
BIG = 20 * 1024 * 1024       # 比这大的文本文件 grep 跳过（file 照样能按行读）
MAX_LINES = 400              # file 一次最多给这么多行
LINE_CHARS = 500             # 一行最多给这么多字


class Denied(ValueError):
    pass


def roots():
    raw = os.environ.get('EPHEMERIS_FILE_ROOTS')
    return [os.path.realpath(os.path.expanduser(r)) for r in (raw.split(os.pathsep) if raw else DEFAULT_ROOTS) if r.strip()]


def _blocked_name(name, is_dir):
    if name.startswith('.'):
        return True
    if is_dir:
        return name in _DENY_DIRS
    return any(fnmatch.fnmatch(name.lower(), p) for p in _DENY_FILES)


def resolve(path):
    """→ 真实的绝对路径；不在白名单里 / 碰到挡着的名字 → Denied。"""
    p = os.path.expanduser(str(path or '').strip())
    if not os.path.isabs(p):
        raise Denied(f'要写完整路径（~/… 或 /…）：{path}')
    real = os.path.realpath(p)
    for r in roots():
        if real == r or real.startswith(r + os.sep):
            parts = os.path.relpath(real, r).split(os.sep) if real != r else []
            for i, name in enumerate(parts):
                if _blocked_name(name, is_dir=i < len(parts) - 1 or os.path.isdir(real)):
                    raise Denied(f'这个看不了（隐藏文件、密钥、数据库之类）：{path}')
            return real
    raise Denied('只能看这些目录里的：' + '、'.join(show(r) for r in roots()))


def show(real):
    """给人 / 模型看的路径：家目录写成 ~。"""
    home = os.path.expanduser('~')
    return '~' + real[len(home):] if real == home or real.startswith(home + os.sep) else real


def is_text(real):
    try:
        with open(real, 'rb') as f:
            return b'\0' not in f.read(8192)
    except OSError:
        return False


def mdate(real):
    return datetime.fromtimestamp(os.path.getmtime(real)).strftime('%Y-%m-%d %H:%M')


def listing(path, pattern=None, n=50):
    """目录里有什么（新改的在前）；给了 pattern 就往下一层层找文件名对得上的。→ [(路径, 是目录, 大小, 修改时间)]"""
    real = resolve(path)
    if not os.path.isdir(real):
        raise Denied(f'不是文件夹：{path}')
    out = []
    if pattern:
        t0 = time.time()
        for d, dirs, files in os.walk(real):
            dirs[:] = [x for x in dirs if not _blocked_name(x, True)]
            for f in files:
                if not _blocked_name(f, False) and fnmatch.fnmatch(f.lower(), pattern.lower()):
                    out.append(os.path.join(d, f))
            if len(out) >= n * 4 or time.time() - t0 > 30:
                break
    else:
        out = [os.path.join(real, x) for x in os.listdir(real) if not _blocked_name(x, os.path.isdir(os.path.join(real, x)))]
    rows = []
    for p in out:
        try:
            st = os.stat(p)
        except OSError:
            continue
        rows.append((p, os.path.isdir(p), st.st_size, st.st_mtime))
    rows.sort(key=lambda r: -r[3])
    return rows[:n]


def grep(words, path, n=30, budget=40):
    """在文件 / 文件夹（往下一层层）里找同一行里这几个词都出现的（不分大小写）。→ ([(路径, 行号, 那行)], 是不是没搜完)"""
    real = resolve(path)
    words = [w.lower() for w in words if w]
    files = [real] if os.path.isfile(real) else None
    hits, t0, cut = [], time.time(), False

    def walk():
        for d, dirs, fs in os.walk(real):
            dirs[:] = sorted(x for x in dirs if not _blocked_name(x, True))
            for f in sorted(fs):
                if not _blocked_name(f, False):
                    yield os.path.join(d, f)
    for p in files or walk():
        if time.time() - t0 > budget:
            cut = True
            break
        try:
            if os.path.getsize(p) > BIG or not is_text(p):
                continue
            with open(p, encoding='utf-8', errors='replace') as fh:
                for i, line in enumerate(fh, 1):
                    low = line.lower()
                    if all(w in low for w in words):
                        hits.append((p, i, line.rstrip('\n')[:LINE_CHARS]))
                        if len(hits) >= n:
                            return hits, True
        except OSError:
            continue
    return hits, cut


def lines(path, start=1, count=200):
    """第 start 行起的 count 行（最多 MAX_LINES）。→ ([(行号, 那行)], 一共几行)"""
    real = resolve(path)
    if os.path.isdir(real):
        raise Denied(f'这是文件夹，用 files 看：{path}')
    if not is_text(real):
        raise Denied(f'不是文字文件（PDF、图片之类看不了）：{path}')
    start, count = max(1, int(start)), max(1, min(int(count), MAX_LINES))
    out, total = [], 0
    with open(real, encoding='utf-8', errors='replace') as fh:
        for total, line in enumerate(fh, 1):
            if start <= total < start + count:
                out.append((total, line.rstrip('\n')[:LINE_CHARS]))
    return out, total


def find_line(path, word, start=1):
    """从 start 行往后，word 第一次出现在第几行；没有 → None。"""
    real = resolve(path)
    w = word.lower()
    with open(real, encoding='utf-8', errors='replace') as fh:
        for i, line in enumerate(fh, 1):
            if i >= start and w in line.lower():
                return i
    return None
