"""指标插件：一个指标一个文件，文件名就是导出的列名。研究方案的 measures 里写哪些就算哪些。

每个插件定义：
    DESCRIPTION   一句话，说清怎么算（会写进导出的说明和页面）
    KIND          行为 / 语言 / 互动 / 内容
    compute(person, study) -> {期号: (值, 证据)}
        值：这一期的数，算不出来给 None（导出成空）
        证据：支撑这个数的原话 [{'msg': 消息 id, 'why': '为什么挑它'}]，页面上点数据点就展开这些
加新指标 = 在这个目录加一个文件。
"""
import importlib


def get(name):
    mod = importlib.import_module(f'research.measures.{name}')
    for attr in ('DESCRIPTION', 'KIND', 'compute'):
        if not hasattr(mod, attr):
            raise AttributeError(f'指标 {name} 少了 {attr}')
    return mod


def run(person, study):
    """研究方案里所有指标：{列名: {期号: (值, 证据)}}。"""
    return {name: get(name).compute(person, study) for name in study['measures']}
