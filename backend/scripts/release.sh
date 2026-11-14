#!/usr/bin/env bash
# 发布流程（严格按序）：
#   1. 依赖锁文件校验
#   2. 数据库结构建立
#   3. 应用启动 + 读取入口切换到注册档案
# 用法：bash scripts/release.sh [--no-serve]
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x .venv/bin/python ]; then
    PYTHON=.venv/bin/python
  else
    PYTHON=python3
  fi
fi
export REGISTRY_READ_SOURCE="${REGISTRY_READ_SOURCE:-archive}"

echo "== 发布步骤 1/3：依赖锁文件校验 =="
"$PYTHON" scripts/verify_lock.py

echo "== 发布步骤 2/3：数据库结构建立 =="
"$PYTHON" scripts/init_db.py

if [ "${1:-}" = "--no-serve" ]; then
  echo "== 发布步骤 3/3：跳过应用启动（--no-serve），读取入口预设为 ${REGISTRY_READ_SOURCE} =="
  exit 0
fi

echo "== 发布步骤 3/3：应用启动，读取入口=${REGISTRY_READ_SOURCE} =="
exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "${APP_PORT:-8000}"
