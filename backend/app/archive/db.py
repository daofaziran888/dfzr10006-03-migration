"""注册档案持久化层：基于标准库 sqlite3，把服务实例档案落到应用进程之外。

设计要点：
- 实例编号（实例编号）是 UNIQUE 幂等键，重复提交只会得到先成功提交的那一版；
- archive_meta 维护单调递增的档案版本号，任何一次成功提交都会产生新版本；
- 原始字段以 JSON 整体存盘（original_payload），补齐的标签单独存放，不改原义；
- 写操作通过 BEGIN IMMEDIATE + 进程内锁串行化，先提交者胜，冲突方拿到 ArchiveConflict。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

META_VERSION_KEY = "archive_version"
SOURCE_CONVERSION = "conversion"
SOURCE_RUNTIME = "runtime"


class ArchiveConflict(RuntimeError):
    """实例编号已被先成功提交的档案版本占用时抛出。"""

    def __init__(self, instance_code: str, winner: dict[str, Any]) -> None:
        super().__init__(f"实例编号 {instance_code} 已存在，档案版本 v{winner['archive_version']} 先提交生效")
        self.instance_code = instance_code
        self.winner = winner


class ArchiveStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._write_lock = threading.Lock()

    # ---- 连接与结构 -------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self) -> None:
        """建立数据库结构（幂等，重复执行不报错）。"""
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS archive_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS registry_snapshot (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_code   TEXT NOT NULL UNIQUE,
                    archive_version INTEGER NOT NULL,
                    archive_source  TEXT NOT NULL,
                    original_payload TEXT NOT NULL,
                    status          TEXT NOT NULL,
                    pending         INTEGER NOT NULL,
                    abnormal        INTEGER NOT NULL,
                    tag_supplement  TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversion_failure (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_code TEXT NOT NULL,
                    reason        TEXT NOT NULL,
                    attempt       INTEGER NOT NULL DEFAULT 1,
                    created_at    TEXT NOT NULL,
                    UNIQUE(instance_code, attempt)
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO archive_meta(key, value) VALUES (?, '0')",
                (META_VERSION_KEY,),
            )

    def is_ready(self) -> bool:
        return self.db_path.is_file()

    # ---- 版本与时间 -------------------------------------------------
    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def archive_version(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM archive_meta WHERE key = ?", (META_VERSION_KEY,)
            ).fetchone()
            return int(row["value"]) if row else 0

    def _bump_version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT value FROM archive_meta WHERE key = ?", (META_VERSION_KEY,)
        ).fetchone()
        next_version = int(row["value"]) + 1
        conn.execute(
            "UPDATE archive_meta SET value = ? WHERE key = ?",
            (str(next_version), META_VERSION_KEY),
        )
        return next_version

    # ---- 写入：先成功提交者胜 --------------------------------------
    def snapshot(
        self,
        *,
        instance_code: str,
        payload: dict[str, Any],
        source: str,
        status: str,
        pending: bool,
        abnormal: bool,
        tag_supplement: list[str] | None = None,
    ) -> dict[str, Any]:
        """按实例编号幂等提交一份注册版本快照；编号已存在则抛 ArchiveConflict。"""
        now = self._now()
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM registry_snapshot WHERE instance_code = ?",
                (instance_code,),
            ).fetchone()
            if existing is not None:
                raise ArchiveConflict(instance_code, self._row_to_dict(existing))
            version = self._bump_version(conn)
            tags = tag_supplement or self.default_tags(payload, source, version)
            conn.execute(
                """
                INSERT INTO registry_snapshot(
                    instance_code, archive_version, archive_source, original_payload,
                    status, pending, abnormal, tag_supplement, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_code,
                    version,
                    source,
                    json.dumps(payload, ensure_ascii=False),
                    status,
                    int(pending),
                    int(abnormal),
                    json.dumps(tags, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.commit()
        row = self.get_snapshot(instance_code)
        assert row is not None
        return row

    def update_state(
        self,
        *,
        instance_code: str,
        status: str,
        pending: bool,
        abnormal: bool,
        payload_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """状态流转：保留原字段原义，仅更新状态并生成新档案版本。"""
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM registry_snapshot WHERE instance_code = ?",
                (instance_code,),
            ).fetchone()
            if row is None:
                raise KeyError(instance_code)
            payload = json.loads(row["original_payload"])
            if payload_patch:
                payload.update(payload_patch)
            version = self._bump_version(conn)
            conn.execute(
                """
                UPDATE registry_snapshot
                   SET status = ?, pending = ?, abnormal = ?,
                       original_payload = ?, archive_version = ?, updated_at = ?
                 WHERE instance_code = ?
                """,
                (
                    status,
                    int(pending),
                    int(abnormal),
                    json.dumps(payload, ensure_ascii=False),
                    version,
                    self._now(),
                    instance_code,
                ),
            )
            conn.commit()
        updated = self.get_snapshot(instance_code)
        assert updated is not None
        return updated

    # ---- 读取 -------------------------------------------------------
    def get_snapshot(self, instance_code: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM registry_snapshot WHERE instance_code = ?",
                (instance_code,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_snapshots(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM registry_snapshot ORDER BY archive_version ASC"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def known_instance_codes(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT instance_code FROM registry_snapshot").fetchall()
        return {row["instance_code"] for row in rows}

    # ---- 转换失败清单 ----------------------------------------------
    def add_failure(self, instance_code: str, reason: str) -> dict[str, Any]:
        with self._write_lock, self._connect() as conn:
            attempt = (
                conn.execute(
                    "SELECT COALESCE(MAX(attempt), 0) AS n FROM conversion_failure WHERE instance_code = ?",
                    (instance_code,),
                ).fetchone()["n"]
                + 1
            )
            conn.execute(
                "INSERT INTO conversion_failure(instance_code, reason, attempt, created_at) VALUES (?, ?, ?, ?)",
                (instance_code, reason, attempt, self._now()),
            )
        return {"instance_code": instance_code, "reason": reason, "attempt": attempt}

    def list_failures(self) -> list[dict[str, Any]]:
        """失败清单：仅保留尚未转换成功的编号及其最近一次原因，历史尝试次数一并返回。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT f.instance_code, f.reason, f.created_at, f.attempt, agg.total_attempts
                  FROM conversion_failure f
                  JOIN (
                      SELECT instance_code, MAX(attempt) AS latest, COUNT(*) AS total_attempts
                        FROM conversion_failure GROUP BY instance_code
                  ) agg ON agg.instance_code = f.instance_code AND agg.latest = f.attempt
                 WHERE f.instance_code NOT IN (SELECT instance_code FROM registry_snapshot)
                 ORDER BY f.instance_code
                """
            ).fetchall()
        return [dict(row) for row in rows]

    # ---- 辅助 -------------------------------------------------------
    @staticmethod
    def default_tags(payload: dict[str, Any], source: str, version: int) -> list[str]:
        """补齐结构化标签：只新增，不覆盖 payload 里已有的「实例标签」。"""
        source_label = "历史条目转换" if source == SOURCE_CONVERSION else "运行时注册"
        return [
            f"档案来源:{source_label}",
            f"档案版本:v{version}",
            f"服务名称:{payload.get('服务名称', '未登记')}",
        ]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["original_payload"] = json.loads(data["original_payload"])
        data["tag_supplement"] = json.loads(data["tag_supplement"]) if data["tag_supplement"] else []
        data["pending"] = bool(data["pending"])
        data["abnormal"] = bool(data["abnormal"])
        return data

    @staticmethod
    def to_api_row(snapshot: dict[str, Any]) -> dict[str, Any]:
        """把档案快照还原成与内存清单同构的 API 行，原字段原义保留并补充档案元数据。"""
        row = dict(snapshot["original_payload"])
        row["id"] = snapshot["id"]
        row["status"] = snapshot["status"]
        row["pending"] = snapshot["pending"]
        row["abnormal"] = snapshot["abnormal"]
        row["档案版本"] = f"v{snapshot['archive_version']}"
        row["档案来源"] = "历史条目转换" if snapshot["archive_source"] == SOURCE_CONVERSION else "运行时注册"
        row["实例标签补充"] = snapshot["tag_supplement"]
        return row
