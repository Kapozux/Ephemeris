"""前端 static/js/md.js：手写日记的 Markdown → HTML。用本机的 node 直接跑（没装 node 就跳过）。
重点是安全：原文里的尖括号永远不变成 HTML，javascript: 链接不变成链接，图片只认本地 /journal-files/。"""
import _env  # noqa: F401
import json
import os
import shutil
import subprocess
import unittest

MD = os.path.join(_env.ROOT, 'static', 'js', 'md.js')
CASES = [
    ('- ', ''), ('    1.', ''), ('    ☐ ', ''),                       # Notion 模板留的空位：不画
    ('1. **粗**：说明', '<div class="j-li j-num" data-n="1."><b>粗</b>：说明</div>'),
    ('☑ 做完了', '<div class="j-todo done">做完了</div>'),
    ('> 引一句', '<blockquote>引一句</blockquote>'),
    ('  - 第二层', '<div class="j-li j-in1">第二层</div>'),
    ('———', '<hr>'),
    ('<script>alert(1)</script>', '<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>'),
    ('[链接](javascript:alert(1))', '<p>[链接](javascript:alert(1))</p>'),
    ('[文书](https://a.com/x?a=1&b=2)', '<p><a href="https://a.com/x?a=1&amp;b=2" target="_blank" rel="noopener">文书</a></p>'),
    ('![晚饭](/journal-files/p/a.jpg)', '<figure class="j-fig"><a href="/journal-files/p/a.jpg" target="_blank" rel="noopener" '
                                      'title="看原图"><img src="/journal-files/p/a.jpg" alt="晚饭" loading="lazy"></a>'
                                      '<figcaption>晚饭</figcaption></figure>'),
    ('![x](https://evil.com/a.png)', '<p>![x](https://evil.com/a.png)</p>'),
]


@unittest.skipUnless(shutil.which('node'), '没装 node')
class MdRender(unittest.TestCase):
    def test_cases(self):
        script = (f"import {{ journalHtml }} from {json.dumps(MD)};"
                  f"const cases = {json.dumps([c[0] for c in CASES], ensure_ascii=False)};"
                  "console.log(JSON.stringify(cases.map(journalHtml)));")
        out = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, check=True)
        for (src, want), got in zip(CASES, json.loads(out.stdout)):
            with self.subTest(src=src):
                self.assertEqual(got, want)


if __name__ == '__main__':
    unittest.main()
