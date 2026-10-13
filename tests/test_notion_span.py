"""to_notion.find_our_span 会决定删掉用户 Notion 页面里的哪几块 —— 认错就会删掉他自己写的东西。"""
import _env  # noqa: F401
import unittest

import to_notion as tn


def blk(t, text='', emoji=None):
    body = {'rich_text': [{'plain_text': text}]} if t != 'divider' else {}
    if emoji:
        body['icon'] = {'emoji': emoji}
    return {'type': t, t: body}


FOOT = '7 个对话窗口 · 120 条 · 3,000 字'


class FindOurSpan(unittest.TestCase):
    def test_finds_appended_section_after_users_own_divider(self):
        page = [blk('paragraph', '我自己写的'), blk('divider'), blk('paragraph', '模板里我自己的分隔线后面'),
                blk('divider'), blk('paragraph', 'AI 日记正文'), blk('heading_3', '做出来的'),
                blk('bulleted_list_item', 'x'), blk('callout', FOOT, emoji='💬')]
        # 必须从 callout 往回找最近的 divider（下标 3），不能从头找第一条（下标 1 是他自己的）
        self.assertEqual(tn.find_our_span(page), [(3, 7)])

    def test_page_without_our_section(self):
        page = [blk('paragraph', '今天'), blk('divider'), blk('paragraph', '晚上')]
        self.assertIsNone(tn.find_our_span(page))

    def test_foreign_block_inside_span_means_unsure(self):
        # 中间混进我们不会产出的块型（比如他插了一张图）→ 认不准，宁可不动
        page = [blk('divider'), blk('paragraph', 'AI 日记'), blk('image'), blk('callout', FOOT, emoji='💬')]
        self.assertIsNone(tn.find_our_span(page))

    def test_half_written_section_fallback(self):
        # 上次写到一半断了：没有 callout，但最后一条 divider 之后全是我们的块，并带「没做完的」
        page = [blk('paragraph', '他写的'), blk('divider'), blk('paragraph', 'AI 日记'),
                blk('heading_3', '没做完的'), blk('bulleted_list_item', 'y')]
        self.assertEqual(tn.find_our_span(page), [(1, 4)])

    def test_fallback_needs_our_heading(self):
        # 最后一段只是普通段落，没有我们的小标题 → 可能是他自己写的，不能认
        page = [blk('paragraph', '他写的'), blk('divider'), blk('paragraph', '他接着写的')]
        self.assertIsNone(tn.find_our_span(page))


class StatusDates(unittest.TestCase):
    """面板的一览除了每类几天，还要给出是哪几天（同步前的确认里点开看），已同步的不列。"""
    def test_dates_per_action(self):
        from unittest import mock
        acts = {'2026-01-02': 'append', '2026-01-01': 'create', '2026-01-03': 'skip', '2026-01-04': 'create'}
        with mock.patch.object(tn, 'token', return_value='x'), mock.patch.object(tn, 'whoami', return_value='E'), \
                mock.patch.object(tn, 'plan', return_value=(acts, {'total': 9}, {})):
            s = tn.status()
        self.assertEqual(s['create'], 2)
        self.assertEqual(s['dates'], {'create': ['2026-01-01', '2026-01-04'], 'append': ['2026-01-02']})


if __name__ == '__main__':
    unittest.main()
