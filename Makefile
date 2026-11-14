.PHONY: install backend frontend lock-check db-init release rehearsal image-rehearsal test

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

# ---- 注册档案发布与演练 ----
# 依赖锁文件校验（发布第 1 步）
lock-check:
	cd backend && .venv/bin/python scripts/verify_lock.py

# 数据库结构建立（发布第 2 步）
db-init:
	cd backend && .venv/bin/python scripts/init_db.py

# 完整发布：锁校验 → 建库 → 应用启动并把读取入口切到注册档案
release:
	cd backend && bash scripts/release.sh

# 本地开发环境全流程演练（中断/续转/复用/重启对照/回滚/并发裁决）
rehearsal:
	cd backend && bash scripts/local_dev_rehearsal.sh

# 镜像构建演练（有 Docker 走真实构建，否则执行 Dockerfile 等价步骤）
image-rehearsal:
	cd backend && bash scripts/image_build_rehearsal.sh

test:
	cd backend && .venv/bin/python -m unittest discover -s tests -v
