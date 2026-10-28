"""一句话是怎么说出来的：自己打的还是贴进来的；在这个窗口里是主动说起，还是跟着对方复述。

借词分析（devtools/loanwords.py）和研究版（research/）都用这里的判断，别处别再写一份。
分词不在这里：中文要 jieba、英文按空格，调用方自己切好词再喂进来。
"""
MAXLEN = 400       # 超过这么多字，多半是贴进来的文章 / 日志


def typed(text):
    """像是自己打的：短、没代码块、换行少。贴进来的东西不算这个人说的话（但算进窗口的上下文，见 Window.show）。"""
    return len(text) <= MAXLEN and '```' not in text and text.count('\n') <= 4


class Window:
    """一个窗口（一段对话）里谁说过哪些词。按时间顺序喂消息。

    say：这一方说了这些词，返回 (主动, 复述)。对方在这个窗口里先说过的算复述。
    show：这一方贴进来的内容，只算进上下文，不算谁主动说了什么。
    """

    def __init__(self):
        self._said = {'human': set(), 'assistant': set()}

    def say(self, role, words):
        other = self._said['assistant' if role == 'human' else 'human']
        words = set(words)
        echo = words & other
        self._said[role] |= words
        return words - echo, echo

    def show(self, role, words):
        self._said[role] |= set(words)
