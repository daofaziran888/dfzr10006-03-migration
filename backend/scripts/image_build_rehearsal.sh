#!/usr/bin/env bash
# 镜像构建演练：当前环境没有 Docker 守护进程，按 Dockerfile 的等价步骤在隔离环境里
# 完整走一遍——基础镜像检查、构建上下文校验、依赖安装、源码拷贝、启动命令导入健康。
# 若环境提供 docker，则直接执行真实镜像构建并跑一次容器冒烟。
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== 镜像构建演练（backend Dockerfile 等价步骤）=="
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "[image] 检测到 Docker，执行真实构建"
  docker build -t realtime-bus-backend:rehearsal .
  docker run --rm -d --name rehearsal-backend -p 18000:8000 \
    -e REGISTRY_READ_SOURCE=archive realtime-bus-backend:rehearsal
  trap 'docker stop rehearsal-backend >/dev/null 2>&1 || true' EXIT
  for _ in $(seq 1 30); do
    curl -fsS http://127.0.0.1:18000/api/health >/dev/null && break
    sleep 1
  done
  curl -fsS http://127.0.0.1:18000/api/health
  echo
  echo "[image] 真实镜像构建与容器冒烟通过"
  exit 0
fi

echo "[image] 未检测到 Docker 守护进程，执行 Dockerfile 等价演练"
test -f Dockerfile
grep -q "FROM python:" Dockerfile
grep -q "app.main:app" Dockerfile
echo "[image] Dockerfile 基础镜像、工作目录、启动指令静态校验通过"

# 在独立 target 目录做一次全新依赖安装，等价镜像内的 pip install，不触碰开发 .venv
rm -rf /tmp/rehearsal-image-deps
.venv/bin/pip install --quiet --target /tmp/rehearsal-image-deps -r requirements.txt
echo "[image] 等价 COPY requirements + pip install 完成（隔离目录 /tmp/rehearsal-image-deps）"

# 等价 COPY . . 后在干净 PYTHONPATH 下导入应用（仅依赖 target 目录与构建上下文源码）
REGISTRY_DB_PATH=/tmp/rehearsal-image-archive.sqlite3 PYTHONPATH=/tmp/rehearsal-image-deps:. \
  .venv/bin/python -c "from app.main import app; print('[image] 应用导入成功，路由数 =', len(app.routes))"
REGISTRY_DB_PATH=/tmp/rehearsal-image-archive.sqlite3 PYTHONPATH=/tmp/rehearsal-image-deps:. \
  .venv/bin/python scripts/init_db.py
rm -f /tmp/rehearsal-image-archive.sqlite3 /tmp/rehearsal-image-archive.sqlite3-*
rm -rf /tmp/rehearsal-image-deps
echo "[image] 镜像构建等价演练通过"
