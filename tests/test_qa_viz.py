"""回答里的图（2026-10-17，照 claude.ai 的内嵌图表）：深挖的回答里可以有 ```html 代码块，聊天页放进沙盒 iframe 画。
代码原样存在 qa_chat（翻回来图还在）；进 messages（日记、搜索）和给模型看的前文时换成「（图）」。"""
import _env  # noqa: F401
import unittest

import llm
import qa
import store
from test_cards_qa import deltas, seed

CHART = '```html\n<canvas id="a"></canvas><script>new Chart(a, {type: "bar", data: {labels: ["9月"], datasets: [{data: [[1, 2]]}]}})</script>\n```'
ANSWER = f'九月聊得最多 [[c:m1·1]]。\n\n{CHART}\n\n十月少了一半。'


class Viz(unittest.TestCase):
    def setUp(self):
        llm.uncancel()
        seed()

        def run(args, stdin):
            self.prompt = stdin
            return {'result': ANSWER, 'session_id': 's1', 'events': deltas(ANSWER)}
        llm.use_claude(run)

    def tearDown(self):
        llm.use_claude(None)

    def test_prompt_explains_charts(self):
        qa.answer_deep('我哪个月聊得最多')
        self.assertIn('```html', self.prompt)
        self.assertIn('EPH.colors', self.prompt)

    def test_chart_kept_for_page_but_not_for_diary(self):
        r = qa.answer_deep('我哪个月聊得最多')
        self.assertIn('new Chart', r['answer'])
        self.assertIn('[[1, 2]]', qa.history()[-1]['text'])                  # 数组不被当成出处记号删掉
        mirrored = store.one("SELECT text FROM messages WHERE source='ask' AND role='assistant' ORDER BY ts DESC LIMIT 1")['text']
        self.assertEqual(mirrored, '九月聊得最多 。\n\n（图）\n\n十月少了一半。')

    def test_history_for_model_has_no_code(self):
        qa.answer_deep('我哪个月聊得最多')
        text = qa._history_text(qa.history())
        self.assertNotIn('Chart', text)
        self.assertIn('（图）', text)

    def test_unfinished_block_stripped(self):
        self.assertEqual(qa.no_viz('看这张：\n```html\n<svg>'), '看这张：\n（图）')


if __name__ == '__main__':
    unittest.main()
