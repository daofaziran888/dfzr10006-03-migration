"""注册档案管理接口：建库、转换、状态查询与失败清单。

只负责编排，业务规则在 app.services.archive_admin，持久化在 app.archive。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.archive import archive
from app.services import archive_admin

router = APIRouter(prefix="/api/archive", tags=["注册档案"])


@router.post("/schema/init")
def init_schema() -> dict[str, object]:
    """建立数据库结构（幂等），对应发布流程第二步。"""
    archive.init_db()
    return {"ok": True, "message": "注册档案数据库结构已就绪", **archive_admin.archive_status()}


@router.get("/status")
def status() -> dict[str, object]:
    """档案版本、已落档编号、失败清单与当前读取入口。"""
    return archive_admin.archive_status()


@router.post("/convert")
def convert(fail_after: int = 0) -> dict[str, object]:
    """把内存注册清单逐条转成首版注册档案；fail_after>0 时注入中断演练。"""
    archive.init_db()
    try:
        report = archive_admin.convert_memory_registry(fail_after=fail_after or None)
    except archive_admin.ConversionInterrupted as interrupted:
        return {
            "ok": False,
            "interrupted": True,
            "message": str(interrupted),
            "converted": interrupted.converted,
            "next_code": interrupted.next_code,
            "archive_version": archive.archive_version(),
            "failures": archive.list_failures(),
        }
    return {"ok": True, "interrupted": False, **report}


@router.get("/failures")
def failures() -> dict[str, object]:
    """失败清单：保留每个未转换编号与原因。"""
    return {"total": len(archive.list_failures()), "items": archive.list_failures()}
