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
