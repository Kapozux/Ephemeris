"""「问」的技能（2026-10-22）：skills/<名字>.md 第一行是什么时候用（进提示词），后面是做法（对上了 look skill 读）。"""
import _env  # noqa: F401
import unittest

import qa
from test_look_sql import run


class Skills(unittest.TestCase):
    def test_pattern_listed_and_readable(self):
        self.assertIn('pattern', dict(qa.skills()))
        text = run('skill', 'pattern')
        self.assertIn('反例', text)
        self.assertNotIn(dict(qa.skills())['pattern'], text)          # 第一行是目录用的，不重复给

    def test_unknown_lists(self):
        out = run('skill', '../qa')
        self.assertIn('没有「../qa」', out)
        self.assertIn('pattern：', out)
        self.assertIsNone(qa.skill_text('../qa'))

    def test_prompt_has_only_descriptions(self):
        p = qa.DEEP_PROMPT.format(name='他', today='2026-10-22', roots='~/Documents', skills=qa._skills_text(), look='look.py',
                                  history='', question='我一直在做什么')
        self.assertIn('look.py skill <名字>', p)
        self.assertIn('· pattern：', p)
        self.assertNotIn('用前一半预测后一半', p)                     # 全文不进提示词
        self.assertIn('skill', qa.LOOK_CMDS)


if __name__ == '__main__':
    unittest.main()
