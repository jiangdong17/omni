#!/bin/sh
# omni 命令入口
# 优先用 OMNI_PYTHON 指定的解释器，其次 python3。建议 Python 3.9+（需要新版 SQLite / FTS5）。
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY="${OMNI_PYTHON:-python3}"
exec "$PY" -u "$DIR/omni.py" "$@"
