"""注册档案首版测试：幂等键、版本快照、标签补齐、断点续转、并发裁决、入口切换。

只用标准库 unittest，通过临时 SQLite 文件隔离，不污染开发档案库。
运行：cd backend && .venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.archive import SOURCE_CONVERSION, SOURCE_RUNTIME, ArchiveConflict, ArchiveStore
from app.services import archive_admin
from app.services.registry import RegistryService


class ArchiveTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "archive.sqlite3"
        self.archive = ArchiveStore(self.db_path)
        self.archive.init_db()
        self.payload = {
            "实例编号": "REGI-0002",
            "服务名称": "订单查询服务",
            "服务地址": "10.12.3.4:8080",
            "服务版本": "v2.3.1",
            "注册时间": "2026-09-02",
            "心跳间隔": "15s",
            "健康状态": "UP",
            "实例标签": "原有标签保持原义",
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_snapshot_keeps_original_fields_and_adds_version_and_tags(self) -> None:
        snap = self.archive.snapshot(
            instance_code="REGI-0002",
            payload=dict(self.payload),
            source=SOURCE_CONVERSION,
            status="已注册",
            pending=True,
            abnormal=True,
        )
        self.assertEqual(snap["archive_version"], 1)
        self.assertEqual(snap["archive_source"], SOURCE_CONVERSION)
        self.assertEqual(snap["original_payload"]["实例标签"], "原有标签保持原义")
        self.assertEqual(snap["original_payload"]["注册时间"], "2026-09-02")
        self.assertTrue(snap["tag_supplement"])
        self.assertNotIn("档案版本", snap["original_payload"])

    def test_instance_code_is_idempotent_key_first_commit_wins(self) -> None:
        first = self.archive.snapshot(
            instance_code="REGI-0002", payload=dict(self.payload),
            source=SOURCE_CONVERSION, status="草稿", pending=True, abnormal=False,
        )
        with self.assertRaises(ArchiveConflict) as ctx:
            self.archive.snapshot(
                instance_code="REGI-0002",
                payload={**self.payload, "服务地址": "mutated:9000"},
                source=SOURCE_RUNTIME, status="草稿", pending=True, abnormal=False,
            )
        self.assertEqual(ctx.exception.winner["id"], first["id"])
        # 版本号未因失败提交而增加
        self.assertEqual(self.archive.archive_version(), 1)
        self.assertEqual(self.archive.get_snapshot("REGI-0002")["original_payload"]["服务地址"], "10.12.3.4:8080")

    def test_concurrent_runtime_registration_single_winner(self) -> None:
        barrier = threading.Barrier(4)
        outcomes: list[str] = []

        def worker(idx: int) -> None:
            barrier.wait()
            try:
                self.archive.snapshot(
                    instance_code="REGI-RACE-1",
                    payload={"实例编号": "REGI-RACE-1", "服务地址": f"10.0.0.{idx}"},
                    source=SOURCE_RUNTIME, status="草稿", pending=True, abnormal=False,
                )
                outcomes.append("winner")
            except ArchiveConflict:
                outcomes.append("conflict")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, 5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count("winner"), 1)
        self.assertEqual(outcomes.count("conflict"), 3)
        self.assertEqual(self.archive.archive_version(), 1)

    def test_update_state_creates_new_version_without_touching_payload(self) -> None:
        self.archive.snapshot(
            instance_code="REGI-0002", payload=dict(self.payload),
            source=SOURCE_CONVERSION, status="已注册", pending=True, abnormal=True,
        )
        updated = self.archive.update_state(
            instance_code="REGI-0002", status="已下线", pending=False, abnormal=False,
        )
        self.assertEqual(updated["archive_version"], 2)
        self.assertEqual(updated["original_payload"]["服务地址"], "10.12.3.4:8080")
        self.assertEqual(updated["original_payload"]["注册时间"], "2026-09-02")


class ConversionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "archive.sqlite3"
        self.archive = ArchiveStore(self.db_path)
        self.archive.init_db()
        self.patcher = mock.patch("app.services.archive_admin.archive", self.archive)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    def test_interrupt_after_second_entry_then_resume_only_remaining(self) -> None:
        # 第一次：成功 1 条后在第 2 条中断
        with self.assertRaises(archive_admin.ConversionInterrupted) as ctx:
            archive_admin.convert_memory_registry(fail_after=1)
        self.assertEqual(ctx.exception.converted, ["REGI-0001"])
        self.assertEqual(ctx.exception.next_code, "REGI-0002")
        self.assertEqual(self.archive.known_instance_codes(), {"REGI-0001"})
        failures = self.archive.list_failures()
        self.assertEqual([f["instance_code"] for f in failures], ["REGI-0002"])
        self.assertTrue(failures[0]["reason"])

        # 第二次续转：只继续第 2、3 条
        report = archive_admin.convert_memory_registry()
        self.assertEqual(report["converted"], ["REGI-0002", "REGI-0003"])
        self.assertEqual(self.archive.archive_version(), 3)
        # 成功落档后失败清单不再包含该编号
        self.assertEqual(
            {f["instance_code"] for f in self.archive.list_failures()} - self.archive.known_instance_codes(),
            set(),
        )

    def test_second_conversion_reuses_first_archive(self) -> None:
        first = archive_admin.convert_memory_registry()
        self.assertFalse(first["reused"])
        version = self.archive.archive_version()
        second = archive_admin.convert_memory_registry()
        self.assertTrue(second["reused"])
        self.assertEqual(second["converted"], [])
        self.assertEqual(self.archive.archive_version(), version)

    def test_supplemented_tags_never_overwrite_original_label(self) -> None:
        archive_admin.convert_memory_registry()
        snap = self.archive.get_snapshot("REGI-0002")
        self.assertEqual(snap["original_payload"]["实例标签"], "服务注册发现样例2")
        self.assertTrue(any("档案来源" in tag for tag in snap["tag_supplement"]))


class ReadSourceSwitchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "archive.sqlite3"
        self.archive = ArchiveStore(self.db_path)
        self.archive.init_db()
        self.patcher = mock.patch("app.services.registry.archive", self.archive)
        self.patcher.start()
        self.service = RegistryService()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    def test_switch_between_memory_and_archive_with_same_regi_0002(self) -> None:
        archive_admin_archive = mock.patch("app.services.archive_admin.archive", self.archive)
        archive_admin_archive.start()
        self.addCleanup(archive_admin_archive.stop)
        archive_admin.convert_memory_registry()

        with mock.patch("app.services.registry.settings", mock.Mock(registry_read_source="memory")):
            mem_rows, total = self.service.list_entries(keyword="REGI-0002")
        self.assertEqual(total, 1)
        mem = mem_rows[0]

        with mock.patch("app.services.registry.settings", mock.Mock(registry_read_source="archive")):
            arc_rows, arc_total = self.service.list_entries(keyword="REGI-0002")
        self.assertEqual(arc_total, 1)
        arc = arc_rows[0]
        self.assertEqual(arc["注册时间"], mem["注册时间"])
        self.assertEqual(arc["服务地址"], mem["服务地址"])
        self.assertEqual(arc["健康状态"], mem["健康状态"])
        self.assertEqual(arc["档案来源"], "历史条目转换")

    def test_runtime_create_conflict_returns_winner_and_action_bumps_version(self) -> None:
        archive_admin_archive = mock.patch("app.services.archive_admin.archive", self.archive)
        archive_admin_archive.start()
        self.addCleanup(archive_admin_archive.stop)
        archive_admin.convert_memory_registry()

        with mock.patch("app.services.registry.settings", mock.Mock(registry_read_source="archive")):
            winner, missing = self.service.create_entry(
                {"实例编号": "REGI-0002", "服务名称": "重复提交", "服务地址": "changed:9000"}
            )
            self.assertEqual(missing, ["__conflict__"])
            self.assertEqual(winner["实例编号"], "REGI-0002")
            self.assertEqual(winner["服务地址"], "服务注册发现样例2")

            entry, message = self.service.run_action(2, "下线服务")
            self.assertIn("下线服务", message)
            # 动作口径沿用既有 ACTION_RULES：下线服务 → 已注册、abnormal=True
            self.assertEqual(entry["status"], "已注册")
            self.assertEqual(entry["abnormal"], True)
            self.assertEqual(entry["档案版本"], "v4")
            self.assertEqual(entry["服务地址"], "服务注册发现样例2")


if __name__ == "__main__":
    unittest.main()
