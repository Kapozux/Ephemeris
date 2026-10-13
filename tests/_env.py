"""每个测试文件第一行 import 它：把数据库指到临时文件，测试碰不到真实的 chats.db。"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if 'DIARY_DB' not in os.environ:
    os.environ['DIARY_DB'] = os.path.join(tempfile.mkdtemp(prefix='ephemeris-test-'), 'test.db')
os.environ['EPHEMERIS_NO_USAGE'] = '1'          # 别往旁边 Verbatim 的真账本（usage.db）里记测试的假调用
os.environ['CLAUDE_GATE'] = ''                 # 三个应用共用的 Claude 分配器：测试里不碰真的 ~/.claude-gate
os.environ['EPHEMERIS_CC_DIR'] = tempfile.mkdtemp(prefix='ephemeris-cc-')   # Claude Code 的对话：测试里是空目录，不读真的 ~/.claude
