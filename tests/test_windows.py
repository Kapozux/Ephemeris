"""Windows 适配（2026-10-14，用户同学的 Windows 电脑）：在 Mac 上能测的那部分——
浏览器装在哪、清残留进程不用 pkill、Cursor 的库在 %APPDATA%、claude 用完整路径、run.bat 在仓库里且是 CRLF、
所有文本文件都按 UTF-8 打开（中文 Windows 默认 GBK）。"""
import _env  # noqa: F401
import ast
import os
import subprocess
import unittest
from unittest import mock

import chrome
import llm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Windows(unittest.TestCase):
    def test_browser_paths(self):
        with mock.patch.dict(os.environ, {'PROGRAMFILES': r'C:\Program Files', 'LOCALAPPDATA': r'C:\Users\a\AppData\Local'}):
            paths = chrome._win_paths()
        self.assertTrue(any(p.endswith('chrome.exe') and 'Program Files' in p for p in paths))
        self.assertTrue(any(p.endswith('msedge.exe') for p in paths))

    def test_kill_stale_without_pkill(self):
        seen = []
        with mock.patch.object(chrome, 'WINDOWS', True), \
                mock.patch.object(subprocess, 'run', lambda cmd, **k: seen.append(cmd)), \
                mock.patch.object(chrome.time, 'sleep', lambda s: None):
            chrome.Chrome(profile=r"C:\eph\.chrome").kill_stale()
        self.assertEqual(seen[0][0], 'powershell')
        self.assertIn(r"--user-data-dir=C:\eph\.chrome", seen[0][-1])

    def test_cursor_db_on_windows(self):
        import cursor
        with mock.patch.object(os, 'name', 'nt'), mock.patch.dict(os.environ, {'APPDATA': 'APPDATA'}):
            p = cursor._default_db()
        self.assertEqual(p.split(os.sep)[:2], ['APPDATA', 'Cursor'])
        self.assertTrue(p.endswith('state.vscdb'))

    def test_claude_full_path(self):
        with mock.patch.object(llm.shutil, 'which', lambda n: r'C:\npm\claude.cmd'):
            self.assertEqual(llm._claude_bin(), r'C:\npm\claude.cmd')

    def test_run_bat_is_tracked_and_crlf(self):
        with open(os.path.join(ROOT, 'run.bat'), 'rb') as f:
            raw = f.read()
        self.assertIn(b'\r\n', raw)
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        self.assertIn(b'PYTHONUTF8=1', raw)
        r = subprocess.run(['git', 'check-ignore', '-q', 'run.bat'], cwd=ROOT)
        self.assertEqual(r.returncode, 1)                  # 白名单放行了

    def test_text_files_opened_as_utf8(self):
        """open(…) 读写文本都要写 encoding（二进制模式不算）：中文 Windows 上不写就按 GBK，读到中文就崩。"""
        bad = []
        for name in sorted(os.listdir(ROOT)):
            if not name.endswith('.py'):
                continue
            with open(os.path.join(ROOT, name), encoding='utf-8') as f:
                tree = ast.parse(f.read())
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'open':
                    mode = n.args[1].value if len(n.args) > 1 and isinstance(n.args[1], ast.Constant) else \
                        next((k.value.value for k in n.keywords if k.arg == 'mode' and isinstance(k.value, ast.Constant)), 'r')
                    if 'b' not in mode and not any(k.arg == 'encoding' for k in n.keywords):
                        bad.append(f'{name}:{n.lineno}')
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
