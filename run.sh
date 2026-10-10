#!/usr/bin/env bash
# Ephemeris -> http://localhost:5055
# 第一次跑：在这个文件夹里建一个 venv，把 requirements.txt 装进去（要联网，一两分钟）。以后直接开。
# 作者自己的电脑：上一层有跟 Verbatim 共用的 venv，就用那个。
set -e
cd "$(dirname "$0")"
if [ -x ../venv/bin/python ] && [ -d ../getAudio ]; then
  PY=../venv/bin/python
else
  if [ ! -x venv/bin/python ]; then
    echo "第一次运行：在装需要的东西，一两分钟……"
    python3 -m venv venv
    venv/bin/pip install --quiet --upgrade pip
    venv/bin/pip install --quiet -r requirements.txt
  fi
  PY=venv/bin/python
fi
PORT="${PORT:-5055}"
# 已经在跑了：直接打开页面
if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/setup" 2>/dev/null; then
  open "http://localhost:$PORT" 2>/dev/null || echo "打开 http://localhost:$PORT"
  exit 0
fi
( sleep 3; open "http://localhost:$PORT" 2>/dev/null || true ) &
exec "$PY" app.py
