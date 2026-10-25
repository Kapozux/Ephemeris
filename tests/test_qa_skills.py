"""「问」的技能（2026-10-22）：skills/<名字>.md 第一行是什么时候用（进提示词），后面是做法（对上了 look skill 读）。"""
import _env  # noqa: F401
import unittest

import llm
import qa
from test_cards_qa import deltas, seed
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
        p = qa.DEEP_PROMPT.format(name='他', today='2026-10-22', roots='~/Documents', skills=qa._skills_text(), memory='', look='look.py',
                                  history='', question='我一直在做什么')
        self.assertIn('look.py skill <名字>', p)
        self.assertIn('· pattern：', p)
        self.assertNotIn('用前一半预测后一半', p)                     # 全文不进提示词
        self.assertIn('skill', qa.LOOK_CMDS)


class Notes(unittest.TestCase):
    """查的时候说的话进步骤栏（检验结果写在这里）；吐出来的孤零零一个标签不算。"""
    def setUp(self):
        llm.uncancel()
        seed()

    def tearDown(self):
        llm.use_claude(None)

    def test_stray_tag_dropped_long_note_kept(self):
        note = '【抽样】按季度抽 64 条：符合 31、不符合 9、无关 24。' + '反例：……' * 80
        say = lambda t: {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': t}]}}
        tool = {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'input': {'command': 'look.py stats'}}]}}
        llm.use_claude(lambda args, stdin: {'result': '答。', 'session_id': 's', 'events': [
            say('</reasoning_effort>'), tool, say(note), tool] + deltas('答。')})
        r = qa.answer_deep('我这一年变了什么', thread=qa.new_thread())
        said = [s['t'] for s in r['trace']['steps'] if s['k'] == 'say']
        self.assertEqual(said, [note[:600]])
        self.assertGreater(len(said[0]), 300)

    def test_written_thinking_removed(self):
        """10-25：它把思考写进正文（text 块里的 <thinking>…</thinking>）：步骤栏、正在写的、最终回答里都不显示；真正的 thinking 块本来就不收。"""
        say = lambda t: {'type': 'assistant', 'message': {'content': [{'type': 'thinking', 'thinking': '内部'}, {'type': 'redacted_thinking', 'data': 'x'},
                                                                      {'type': 'text', 'text': t}]}}
        tool = {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'input': {'command': 'look.py stats'}}]}}
        llm.use_claude(lambda args, stdin: {'result': '<thinking>草稿</thinking>答。', 'session_id': 's', 'events': [
            say('<thinking>先想想</thinking>'), tool, say('<thinking>想\n半天</thinking>我先查统计'), tool] + deltas('<thinking>草稿</thinking>', '答。')})
        live = []
        r = qa.answer_deep('我这一年变了什么', thread=qa.new_thread(), on_text=lambda x: live.append(x))
        self.assertEqual([s['t'] for s in r['trace']['steps'] if s['k'] == 'say'], ['我先查统计'])
        self.assertEqual(r['answer'], '答。')
        self.assertEqual(qa.no_thinking(''.join(x for x in live if x)), '答。')
        self.assertEqual(qa.no_thinking('写了一半<thinking>没收尾'), '写了一半')


if __name__ == '__main__':
    unittest.main()
