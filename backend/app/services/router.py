"""查询路由分发业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.archive import archive
from app.archive.db import ArchiveStore
from app.config import settings
from app.store import store

MODULE = "router"
REGISTRY_MODULE = "registry"
REQUIRED_FIELDS = ["规则编号", "匹配条件", "目标服务"]
STATUS_ORDER = ["草稿", "生效中", "已降级", "已关闭"]
ACTION_RULES = {"启用规则": "草稿", "降级路由": "生效中", "关闭规则": "已降级"}
NEGATIVE_ACTIONS = []


class RouterService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"路由规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于查询路由分发可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"路由规则已{action}"

    def referenced_instances(self) -> list[dict[str, Any]]:
        """路由服务引用的服务实例清单：档案入口下带来源与档案版本，不再是无来源旧清单。"""
        if settings.registry_read_source == "archive":
            return [ArchiveStore.to_api_row(item) for item in archive.list_snapshots()]
        rows = store.rows(REGISTRY_MODULE)
        return [dict(row, 档案来源="内存清单") for row in rows]
