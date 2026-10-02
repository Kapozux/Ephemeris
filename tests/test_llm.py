import _env  # noqa: F401  必须第一个
import unittest

import llm


class ParseJson(unittest.TestCase):
    def test_code_fence(self):
        self.assertEqual(llm.parse_json('```json\n{"a": 1}\n```', dict), {'a': 1})

    def test_latex_backslash(self):
        # 2026-09-21：数学多的日子模型吐 LaTeX，\s 不是合法 JSON 转义
        out = llm.parse_json('{"narrative": "解 $\\sqrt{3}$ 和 $\\frac{1}{2}$"}', dict)
        self.assertIn('sqrt', out['narrative'])

    def test_array_inside_chatter(self):
        self.assertEqual(llm.parse_json('好的：\n[{"name": "x"}]\n以上', list), [{'name': 'x'}])

    def test_wrong_type_is_none(self):
        self.assertIsNone(llm.parse_json('{"a": 1}', list))
        self.assertIsNone(llm.parse_json('[1]', dict))

    def test_garbage(self):
        self.assertIsNone(llm.parse_json('', dict))
        self.assertIsNone(llm.parse_json('没有 JSON', dict))


class AskJson(unittest.TestCase):
    def tearDown(self):
        llm.use(None)

    def test_retries_until_parsable(self):
        replies = iter(['乱说一通', '{"ok": true}'])
        llm.use(lambda p: next(replies))
        self.assertEqual(llm.ask_json('x', dict, tries=2), {'ok': True})

    def test_ok_predicate_rejects(self):
        llm.use(lambda p: '{"headline": ""}')
        self.assertIsNone(llm.ask_json('x', dict, tries=2, ok=lambda d: d.get('headline')))

    def test_backend_error_is_recorded_not_raised(self):
        def boom(p):
            raise RuntimeError('key 失效')
        llm.use(boom)
        self.assertIsNone(llm.ask('x'))
        self.assertIn('key 失效', llm.status()['last_error'])


class Stray(unittest.TestCase):
    def tearDown(self):
        llm.use(None)

    def test_detects_only_words_missing_from_source(self):
        self.assertEqual(llm.stray('确定了 SVM 분류기 的阈值', '材料'), ['분류기'])
        self.assertEqual(llm.stray('在听「アイドル」', '今天循环アイドル'), [])     # 材料里本来就有
        self.assertEqual(llm.stray('纯中文 and English', ''), [])

    def test_ask_retries_once_and_keeps_clean_answer(self):
        answers = iter(['情绪剧烈拉扯，खासकर 那天', '情绪剧烈拉扯，尤其那天'])
        llm.use(lambda p: next(answers))
        self.assertEqual(llm.ask('材料'), '情绪剧烈拉扯，尤其那天')

    def test_ask_keeps_first_if_retry_also_strays(self):
        answers = iter(['A 분류기', 'B 분류기'])
        llm.use(lambda p: next(answers))
        self.assertEqual(llm.ask('材料'), 'A 분류기')


if __name__ == '__main__':
    unittest.main()
