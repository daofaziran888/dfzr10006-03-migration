"""服务注册发现业务规则：状态流转、字段校验与筛选口径都收在这里。

读取入口由 settings.registry_read_source 决定：
- memory  ：进程内内存清单（回滚兜底，原内存清单始终可读取）
- archive ：SQLite 注册档案，重启后注册时间、服务地址、健康状态延续；
            运行时注册以实例编号为幂等键，先成功提交的档案版本裁决生效。
"""
from __future__ import annotations

from typing import Any

from app.archive import SOURCE_RUNTIME, ArchiveConflict, archive
from app.archive.db import ArchiveStore
from app.config import settings
from app.store import store

MODULE = "registry"
REQUIRED_FIELDS = ["实例编号", "服务名称", "服务地址"]
STATUS_ORDER = ["草稿", "已注册", "已下线", "已注销"]
ACTION_RULES = {"上线服务": "草稿", "下线服务": "已注册", "注销服务": "已下线"}
NEGATIVE_ACTIONS = ["下线服务"]


class RegistryService:
    def _use_archive(self) -> bool:
        return settings.registry_read_source == "archive"

    # ---- 读取：随读取入口切换 --------------------------------------
    def _all_rows(self) -> list[dict[str, Any]]:
        if self._use_archive():
            return [ArchiveStore.to_api_row(item) for item in archive.list_snapshots()]
        return store.rows(MODULE)

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._all_rows()
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("实例编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        if self._use_archive():
            for row in self._all_rows():
                if int(row.get("id", 0)) == entry_id:
                    return row
            return None
        return store.find(MODULE, entry_id)

    # ---- 登记：档案模式以实例编号为幂等键 --------------------------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing

        if self._use_archive():
            payload = {field: values.get(field) for field in REQUIRED_FIELDS}
            for field, value in values.items():
                if field not in payload and field not in {"id", "status", "pending", "abnormal"}:
                    payload[field] = value
            try:
                snapshot = archive.snapshot(
                    instance_code=str(values["实例编号"]).strip(),
                    payload=payload,
                    source=SOURCE_RUNTIME,
                    status=STATUS_ORDER[0],
                    pending=True,
                    abnormal=False,
                )
            except ArchiveConflict as conflict:
                return ArchiveStore.to_api_row(conflict.winner), ["__conflict__"]
            return ArchiveStore.to_api_row(snapshot), []

        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    # ---- 动作：档案模式生成新版本，内存模式原地流转 ----------------
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于服务注册发现可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        if self._use_archive():
            current = self.get_entry(entry_id)
            if current is None:
                return None, f"服务实例 {entry_id} 不存在或已归档"
            snapshot = archive.update_state(
                instance_code=str(current["实例编号"]),
                status=target,
                pending=target != STATUS_ORDER[-1],
                abnormal=action in NEGATIVE_ACTIONS,
            )
            return ArchiveStore.to_api_row(snapshot), f"服务实例已{action}"

        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"服务实例 {entry_id} 不存在或已归档"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"服务实例已{action}"
