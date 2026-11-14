# 实时数据服务总线平台

面向业务系统的一站式实时数据服务总线平台。覆盖数据服务注册与发现、查询路由分发、结果缓存加速、调用限流降级、服务编排组合、调用链追踪与服务健康诊断的完整数据服务链路。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 注册档案（进程外持久化）

服务实例档案默认在进程内存里，进程停止即丢失。首版注册档案基于标准库 SQLite
落地到 `backend/data/registry_archive.sqlite3`，重启后 REGI-0001 至 REGI-0003 的
注册时间、服务地址与健康状态可以延续。

关键约定：

- **幂等键**：`实例编号` 为 `UNIQUE` 键。历史条目转换与运行时注册都以它去重，
  重复提交不会覆盖；并发时以**先成功提交的档案版本**裁决，后到方收到冲突结果。
- **版本快照**：每次成功提交在 `archive_meta` 产生单调递增的档案版本号
  （`registry_snapshot.archive_version`）；状态流转保留原字段原义并生成新版本。
- **标签补齐**：原始业务字段整体存入 `original_payload`（含原 `实例标签` 原义），
  结构化标签写入独立的 `tag_supplement`，不覆盖任何已有字段。
- **断点续转与失败清单**：转换逐条独立提交，中断后重跑只处理未落档编号；
  未转换编号与原因写入 `conversion_failure`，转换成功后即从失败清单移除
  （历史尝试行保留可审计）。
- **读取入口切换**：环境变量 `REGISTRY_READ_SOURCE=memory|archive`。
  `memory` 是回滚兜底入口，原内存清单始终只读、随时可读；`archive` 走 SQLite 档案。

管理接口：

| 接口 | 说明 |
| --- | --- |
| `POST /api/archive/schema/init` | 建立数据库结构（幂等） |
| `POST /api/archive/convert?fail_after=N` | 内存清单转首版档案；`fail_after` 注入中断演练 |
| `GET  /api/archive/status` | 档案版本、已落档编号、失败清单、读取入口 |
| `GET  /api/archive/failures` | 未转换编号与原因 |
| `GET  /api/registry/read-source` | 当前读取入口探针 |
| `GET  /api/router/referenced-instances` | 路由服务引用的实例（档案入口下带来源与版本） |

### 发布流程（严格按序）

```bash
make release        # = cd backend && bash scripts/release.sh
```

1. **依赖锁文件校验**：`scripts/verify_lock.py` 逐行比对 `requirements.lock`
   与当前环境版本，缺包或漂移即失败；
2. **数据库结构建立**：`scripts/init_db.py` 幂等建表；
3. **应用启动与读取入口切换**：以 `REGISTRY_READ_SOURCE=archive` 启动 uvicorn。

### 演练

```bash
make rehearsal        # 本地开发环境全流程演练
make image-rehearsal  # 镜像构建演练（无 Docker 时执行 Dockerfile 等价步骤）
make test             # 单元测试（unittest，9 个用例）
```

本地演练覆盖：内存入口基线采集 → 转换到第 2 条中断 → 仅续转第 2、3 条 →
二次转换复用首版档案 → 档案入口重启并对照 REGI-0002 注册时间与服务地址 →
回滚内存入口验证原清单可读 → 同编号并发注册的先提交裁决。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 服务注册发现 | `registry` | 服务实例 | 实例编号、服务名称、服务地址 |
| 查询路由分发 | `router` | 路由规则 | 规则编号、匹配条件、目标服务 |
| 结果缓存加速 | `cache` | 缓存配置 | 配置编号、缓存键名、过期秒数 |
| 调用限流降级 | `limiter` | 限流规则 | 规则编号、限流对象、限流算法 |
| 服务编排组合 | `orchestra` | 编排流程 | 流程编号、流程名称、编排步骤 |
| 调用链追踪 | `trace` | 追踪记录 | 记录编号、调用链ID、服务名称 |
| 服务健康诊断 | `diagnose` | 诊断报告 | 报告编号、诊断对象、健康分值 |
| 数据接口契约 | `schema` | 接口定义 | 接口编号、接口名称、请求方法 |
| 数据订阅推送 | `subscribe` | 订阅任务 | 任务编号、数据源名称、推送目标 |
| 数据加工转换 | `transform` | 加工规则 | 规则编号、输入格式、输出格式 |
| 数据模拟服务 | `mock` | 模拟配置 | 配置编号、模拟对象、数据模板 |
| 接入网关管理 | `gateway` | 网关配置 | 配置编号、网关名称、监听端口 |
| 服务指标采集 | `metric` | 指标采集器 | 采集器编号、监控对象、指标名称 |
| 调用来源管理 | `whitelist` | 来源登记 | 登记编号、应用名称、调用标识 |
| 负载均衡策略 | `balance` | 均衡策略 | 策略编号、策略名称、均衡算法 |
| 服务版本回退 | `rollback` | 版本快照 | 快照编号、服务名称、版本号 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
