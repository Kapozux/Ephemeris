"""研究版（research/）：只许引用 research.CORE 里的 Ephemeris 模块；共用的 speech 判断对。"""
import _env  # noqa: F401
import ast
import os
import unittest

import research
import speech

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _imports(path):
    tree = ast.parse(open(path, encoding='utf-8').read(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split('.')[0], node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module.split('.')[0], node.lineno


class Boundary(unittest.TestCase):
    def test_only_core_modules(self):
        ephemeris = {f[:-3] for f in os.listdir(ROOT) if f.endswith('.py')}
        bad = []
        for d, _, files in os.walk(os.path.join(ROOT, 'research')):
            for f in files:
                if f.endswith('.py'):
                    path = os.path.join(d, f)
                    for mod, line in _imports(path):
                        if mod in ephemeris and mod not in research.CORE:
                            bad.append(f'{os.path.relpath(path, ROOT)}:{line} import {mod}')
        self.assertEqual(bad, [], '研究版只许引用 research.CORE；要用就先加进 CORE')


class Speech(unittest.TestCase):
    def test_typed(self):
        self.assertTrue(speech.typed('明天几点出发'))
        self.assertFalse(speech.typed('字' * (speech.MAXLEN + 1)))
        self.assertFalse(speech.typed('看这段\n```\nprint(1)\n```'))
        self.assertFalse(speech.typed('\n'.join('第一行 第二行 第三行 第四行 第五行 第六行'.split())))

    def test_window_spontaneous_and_echo(self):
        w = speech.Window()
        self.assertEqual(w.say('assistant', {'兜底', '方案'}), ({'兜底', '方案'}, set()))
        spont, echo = w.say('human', {'兜底', '晚饭'})
        self.assertEqual((spont, echo), ({'晚饭'}, {'兜底'}))

    def test_pasted_counts_as_context(self):
        w = speech.Window()
        w.show('human', {'颗粒度'})                    # 你贴进来的文章里有
        self.assertEqual(w.say('assistant', {'颗粒度'}), (set(), {'颗粒度'}))   # AI 跟着说不算主动


if __name__ == '__main__':
    unittest.main()
