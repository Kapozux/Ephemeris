"""「问」也能看他电脑上的文件（2026-10-21：聊天记录里到处是日志、文书、代码，「问」只看得到聊过，看不到文件本身）。

只读，只在白名单目录里（默认 ~/Documents、~/Downloads；EPHEMERIS_FILE_ROOTS 用 os.pathsep 隔开可以改），
看不了的：隐藏的文件和目录（.env、.ssh、.git……）、密钥 / 证书 / 凭证样子的文件、数据库（chats.db 有 look 管着）、
依赖和缓存目录（node_modules、venv……，全是噪音）。符号链接按真实位置算，链到白名单外面也不行。
look.py 的 files / grep / file 三个命令用这里；回答里引文件是 [[f:路径:行号]] 或 [[f:路径:起-止]]，qa._snapshot 也用这里读那几行。

不是纯文字的（10-21）：.docx / .pptx 直接从里面的 XML 取字，有文字层的 PDF 用 pdftotext 取字，都是一下就出。
扫描件不现转（墨页转一份要排队、几分钟）：墨页（旁边的 PDF→Markdown 应用，MOYE_URL）已经转好的，用 moye_search 搜、
路径写 moye:<id> 按行读，跟文件一样；没转过的就让他先在墨页里转。墨页没开着也不影响别的。
"""
import fnmatch
import html
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime

DEFAULT_ROOTS = ('~/Documents', '~/Downloads')
_DENY_DIRS = {'node_modules', 'venv', '__pycache__', 'site-packages', 'Library'}
_DENY_FILES = ('*.pem', '*.key', '*.p12', '*.pfx', '*.keychain*', 'id_rsa*', 'id_ed25519*', '*credential*', '*secret*',
               '*.db', '*.db-*', '*.sqlite*', '*.kdbx')
BIG = 20 * 1024 * 1024       # 比这大的文本文件 grep 跳过（file 照样能按行读）
MAX_LINES = 400              # file 一次最多给这么多行
LINE_CHARS = 500             # 一行最多给这么多字


EXTRACT = ('.docx', '.pptx', '.pdf')
MOYE = 'moye:'


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
            if os.path.getsize(p) > BIG or not (is_text(p) or p.lower().endswith(EXTRACT)):
                continue
            for i, line in enumerate(_read(p), 1):
                low = line.lower()
                if all(w in low for w in words):
                    hits.append((p, i, line[:LINE_CHARS]))
                    if len(hits) >= n:
                        return hits, True
        except (OSError, Denied):
            continue
    return hits, cut


def lines(path, start=1, count=200):
    """第 start 行起的 count 行（最多 MAX_LINES）。→ ([(行号, 那行)], 一共几行)"""
    start, count = max(1, int(start)), max(1, min(int(count), MAX_LINES))
    out, total = [], 0
    for total, line in enumerate(_source(path), 1):
        if start <= total < start + count:
            out.append((total, line[:LINE_CHARS]))
    return out, total


def find_line(path, word, start=1):
    """从 start 行往后，word 第一次出现在第几行；没有 → None。"""
    w = word.lower()
    for i, line in enumerate(_source(path), 1):
        if i >= start and w in line.lower():
            return i
    return None


def where(path):
    """file / 出处上显示的：(名字, 修改时间)。"""
    if str(path).startswith(MOYE):
        job = moye_job(path[len(MOYE):])
        return f"墨页 · {job.get('filename') or ''}", _local(job.get('finished_at') or job.get('created_at'))
    real = resolve(path)
    return show(real), mdate(real)


def _source(path):
    if str(path).startswith(MOYE):
        return moye_text(path[len(MOYE):]).split('\n')
    real = resolve(path)
    if os.path.isdir(real):
        raise Denied(f'这是文件夹，用 files 看：{path}')
    return _read(real)


def _read(real):
    """一行一行（不带换行符）。.docx / .pptx / PDF 先取字；别的不是文字的（图片之类）→ Denied。"""
    if real.lower().endswith(EXTRACT):
        return extract(real).split('\n')
    if not is_text(real):
        raise Denied(f'不是文字文件（图片之类看不了）：{show(real)}')
    with open(real, encoding='utf-8', errors='replace') as fh:
        return [line.rstrip('\n') for line in fh]


def _xml_text(xml, para):
    """Office XML 里一段一行：para 是段落标签（w:p / a:p），取 <*:t> 里的字，tab / 换行照留。"""
    out = []
    for p in re.findall(rf'<{para}[ >].*?</{para}>|<{para}/>', xml, re.S):
        p = re.sub(r'<w:tab/>', '\t', p)
        p = re.sub(r'<(?:w|a):br/>', '\n', p)
        out.append(html.unescape(''.join(re.findall(r'<(?:w|a):t(?: [^>]*)?>([^<]*)</(?:w|a):t>', p))) if '<' in p else '')
    return '\n'.join(out)


def extract(real):
    """.docx / .pptx / PDF → 文字。读不出来（损坏、加密、扫描件、Word 的 ~$ 临时文件）→ Denied，写清楚为什么。"""
    ext = os.path.splitext(real)[1].lower()
    name = show(real)
    try:
        if ext == '.docx':
            with zipfile.ZipFile(real) as z:
                return _xml_text(z.read('word/document.xml').decode('utf-8', 'replace'), 'w:p')
        if ext == '.pptx':
            with zipfile.ZipFile(real) as z:
                slides = sorted((n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)),
                                key=lambda n: int(re.search(r'(\d+)', n.rsplit('/', 1)[1]).group(1)))
                return '\n'.join(f'—— 第 {i} 页 ——\n' + _xml_text(z.read(n).decode('utf-8', 'replace'), 'a:p')
                                 for i, n in enumerate(slides, 1))
    except (zipfile.BadZipFile, KeyError):
        raise Denied(f'读不出来（文件坏了，或者是 Word 开着时留的 ~$ 临时文件）：{name}')
    exe = shutil.which('pdftotext') or next((p for p in ('/opt/homebrew/bin/pdftotext', '/usr/local/bin/pdftotext') if os.path.exists(p)), None)
    if not exe:
        raise Denied('读 PDF 要装 poppler（brew install poppler）')
    try:
        r = subprocess.run([exe, '-layout', '-enc', 'UTF-8', real, '-'], capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise Denied(f'PDF 太大，一分钟没取完字：{name}')
    if r.returncode:
        raise Denied(f'PDF 读不出来（可能加了密码）：{name}')
    pages = r.stdout.decode('utf-8', 'replace').rstrip('\f').split('\f')
    if sum(len(p.strip()) for p in pages) < 30 * len(pages):
        raise Denied(f'这份 PDF 是扫描件（没有文字层）：先用 moye 搜墨页转好的；没转过就跟他说先在墨页里转一下。{name}')
    return '\n'.join(f'—— 第 {i} 页 ——\n' + p.rstrip() for i, p in enumerate(pages, 1))


# ---------- 墨页：只读它已经转好的（全文搜索、按 id 取 Markdown），不提交新任务 ----------
def _moye(path):
    base = os.environ.get('MOYE_URL') or 'http://127.0.0.1:8765'
    try:
        with urllib.request.urlopen(base + path, timeout=20) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise Denied(f'墨页里没有这份（{e.code}）')
    except (OSError, ValueError):
        raise Denied('墨页没在跑（它开着才搜得了它转好的 PDF）')


def moye_search(q, n=10):
    """→ [{id, filename, title, date, count, hits:[{page, text}]}]，命中多的在前。"""
    found = _moye('/api/search?q=' + urllib.parse.quote(q[:200])).get('results') or []
    meta = {j['id']: j for j in _moye('/api/library').get('items') or []}
    out = []
    for r in found[:n]:
        j = meta.get(r['id'], {})
        out.append({'id': r['id'], 'filename': j.get('filename') or '', 'title': j.get('ai_title') or '',
                    'date': _local(j.get('created_at'))[:10], 'count': r.get('count') or 0,
                    'hits': [{'page': h.get('page'), 'text': f"{h.get('before', '')}{h.get('match', '')}{h.get('after', '')}"}
                             for h in (r.get('hits') or [])[:3]]})
    return out


def _local(iso):
    """墨页存的是 UTC（…Z）→ 本地时间「YYYY-MM-DD HH:MM」。"""
    try:
        return datetime.fromisoformat(str(iso).replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return str(iso or '')[:16]


def moye_job(job_id):
    if not re.fullmatch(r'[\w-]+', job_id or ''):
        raise Denied(f'不是墨页的 id：{job_id}')
    return _moye(f'/api/library/{job_id}').get('job') or {}


def moye_text(job_id):
    if not re.fullmatch(r'[\w-]+', job_id or ''):
        raise Denied(f'不是墨页的 id：{job_id}')
    return (_moye(f'/api/library/{job_id}').get('result') or {}).get('markdown') or ''
