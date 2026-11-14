#!/usr/bin/env bash
# 本地开发环境演练：跑通首版注册档案的完整故事线。
#
# 1. 启动内存入口应用，记录 REGI-0002 切换前的注册时间与服务地址
# 2. 建库 → 转换到第 2 条中断（fail_after=1）→ 校验只落档 REGI-0001
# 3. 续转：只处理第 2、3 条 → 失败清单与复用校验
# 4. 再执行一次转换：直接复用首版档案，不产生新版本
# 5. 重启为档案入口：REGI-0002 注册时间/服务地址前后对照
# 6. 回滚到内存入口：原内存清单仍可读取
# 7. 并发裁决演练：同编号并发注册，先成功提交的档案版本生效
set -euo pipefail

cd "$(dirname "$0")/.."
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
BASE=http://127.0.0.1
WORK=/tmp/registry-rehearsal
mkdir -p "$WORK"
export REGISTRY_DB_PATH="$WORK/registry_archive.sqlite3"
rm -f "$WORK"/*.sqlite3*

PID_MEM=""
PID_ARC=""
serve() {
  local port="$1" source="$2" log="$3"
  REGISTRY_READ_SOURCE="$source" "$PY" -m uvicorn app.main:app \
    --host 127.0.0.1 --port "$port" >"$log" 2>&1 &
  echo $!
}

wait_health() {
  local url="$1"
  for _ in $(seq 1 50); do
    curl -fsS "$url/api/health" >/dev/null 2>&1 && return 0
    sleep 0.3
  done
  echo "服务未就绪：$url" >&2
  return 1
}

stop_pid() { [ -n "$1" ] && kill "$1" >/dev/null 2>&1 || true; wait "$1" 2>/dev/null || true; }

echo "== 演练 1：内存入口启动，采集切换前 REGI-0002 基线 =="
PID_MEM=$(serve 18001 memory "$WORK/mem.log")
trap 'stop_pid "$PID_MEM" 2>/dev/null || true; stop_pid "$PID_ARC" 2>/dev/null || true' EXIT
wait_health "$BASE:18001"
BEFORE=$(curl -fsS "$BASE:18001/api/registry?keyword=REGI-0002" | "$PY" -c "import json,sys; row=json.load(sys.stdin)['items'][0]; print(row['注册时间'], row['服务地址'])")
echo "切换前 REGI-0002：$BEFORE"
stop_pid "$PID_MEM"

echo "== 演练 2：建库 + 转换到第 2 条中断 =="
"$PY" scripts/verify_lock.py
"$PY" scripts/init_db.py
"$PY" - <<'PYEOF'
from app.services import archive_admin
from app.archive import archive

archive.init_db()
try:
    archive_admin.convert_memory_registry(fail_after=1)
except archive_admin.ConversionInterrupted as exc:
    print("收到预期中断：", exc)
codes = archive.known_instance_codes()
assert codes == {"REGI-0001"}, codes
failures = archive.list_failures()
assert {f["instance_code"] for f in failures} == {"REGI-0002"}, failures
print("中断后已落档：", sorted(codes), "；失败清单：", [(f["instance_code"], f["reason"][:12]) for f in failures])
PYEOF

echo "== 演练 3：断点续转，仅继续第 2、3 条 =="
"$PY" - <<'PYEOF'
from app.services import archive_admin
from app.archive import archive

report = archive_admin.convert_memory_registry()
assert report["converted"] == ["REGI-0002", "REGI-0003"], report
# 已成功转换后，失败清单里不再残留该编号的未转换记录
remaining_failures = {f["instance_code"] for f in report["failures"]}
assert "REGI-0002" not in remaining_failures and "REGI-0003" not in remaining_failures, remaining_failures
assert archive.archive_version() == 3
snap = archive.get_snapshot("REGI-0002")
assert snap["original_payload"]["实例标签"] == "服务注册发现样例2"
assert snap["tag_supplement"], "结构化标签应补齐"
print("续转完成：", report["converted"], "；补齐标签：", snap["tag_supplement"])
PYEOF

echo "== 演练 4：第二次执行转换，直接复用首版档案 =="
"$PY" - <<'PYEOF'
from app.services import archive_admin
from app.archive import archive

version_before = archive.archive_version()
report = archive_admin.convert_memory_registry()
assert report["reused"] is True and archive.archive_version() == version_before
print(report["message"])
PYEOF

echo "== 演练 5：档案入口重启，REGI-0002 前后对照 =="
PID_ARC=$(serve 18002 archive "$WORK/arc.log")
wait_health "$BASE:18002"
curl -fsS "$BASE:18002/api/registry/read-source" | "$PY" -m json.tool
AFTER=$(curl -fsS "$BASE:18002/api/registry?keyword=REGI-0002" | "$PY" -c "import json,sys; row=json.load(sys.stdin)['items'][0]; print(row['注册时间'], row['服务地址'])")
echo "切换后 REGI-0002：$AFTER"
[ "$BEFORE" = "$AFTER" ] || { echo "REGI-0002 前后不一致！" >&2; exit 1; }
echo "REGI-0002 注册时间与服务地址前后一致 ✓"
curl -fsS "$BASE:18002/api/router/referenced-instances" | "$PY" -c "
import json,sys
items = json.load(sys.stdin)['items']
assert all(item.get('档案来源') for item in items)
print('路由服务引用实例带来源：', [(i['实例编号'], i['档案来源'], i['档案版本']) for i in items])
"

echo "== 演练 6：回滚内存入口，原内存清单仍可读取 =="
stop_pid "$PID_ARC"
PID_MEM=$(serve 18001 memory "$WORK/mem2.log")
wait_health "$BASE:18001"
curl -fsS "$BASE:18001/api/registry?keyword=REGI-0002" | "$PY" -c "
import json,sys
row = json.load(sys.stdin)['items'][0]
assert row['注册时间'] and row['服务地址']
print('回滚后内存清单 REGI-0002 仍可读：', row['注册时间'], row['服务地址'])
"
stop_pid "$PID_MEM"

echo "== 演练 7：并发注册裁决，先成功提交者胜 =="
"$PY" - <<'PYEOF'
import threading
from app.archive import ArchiveConflict, archive
from app.config import settings

settings.__class__  # noqa: B018 - 仅确认模块可导入
archive.init_db()
results: dict[str, str] = {}
barrier = threading.Barrier(2)

def register(worker: str, address: str) -> None:
    barrier.wait()
    try:
        snap = archive.snapshot(
            instance_code="REGI-CONC-1",
            payload={"实例编号": "REGI-CONC-1", "服务名称": "并发演练", "服务地址": address},
            source="runtime",
            status="草稿",
            pending=True,
            abnormal=False,
        )
        results[worker] = f"winner=v{snap['archive_version']}"
    except ArchiveConflict as conflict:
        results[worker] = f"conflict->v{conflict.winner['archive_version']}"

threads = [threading.Thread(target=register, args=(f"w{i}", f"10.0.0.{i}")) for i in (1, 2)]
for t in threads: t.start()
for t in threads: t.join()
winners = [v for v in results.values() if v.startswith("winner=")]
conflicts = [v for v in results.values() if v.startswith("conflict->")]
assert len(winners) == 1 and len(conflicts) == 1, results
winner = archive.get_snapshot("REGI-CONC-1")
assert winner["original_payload"]["服务地址"] in {"10.0.0.1", "10.0.0.2"}
winner_version = f"v{winner['archive_version']}"
assert set(results.values()) == {"winner=" + winner_version, "conflict->" + winner_version}, results
print("并发裁决结果：", results, "；生效地址：", winner["original_payload"]["服务地址"])
PYEOF

echo
echo "全部本地开发环境演练通过 ✓（档案库：$REGISTRY_DB_PATH）"
