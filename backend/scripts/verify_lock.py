#!/usr/bin/env python3
"""发布流程第一步：依赖锁文件校验。

逐行比对 requirements.lock 与当前 Python 环境已安装发行包（importlib.metadata，
只依赖标准库，不访问网络），任何缺包、版本漂移或环境多出的托管包都会让校验失败。

用法：
    python3 scripts/verify_lock.py [--lock requirements.lock]
"""
from __future__ import annotations

import argparse
import sys
from importlib import metadata
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# 虚拟环境引导件，不属于应用依赖，不参与锁文件比对。
BOOTSTRAP_PACKAGES = {"pip", "setuptools", "wheel", "distribute", "pkg-resources"}


def parse_lock(path: Path) -> dict[str, str]:
    locked: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            raise SystemExit(f"锁文件存在非精确钉版条目：{line}")
        name, version = line.split("==", 1)
        locked[name.strip().lower()] = version.strip()
    return locked


def installed_packages() -> dict[str, str]:
    return {dist.metadata["Name"].lower(): dist.version for dist in metadata.distributions()}


def main() -> int:
    parser = argparse.ArgumentParser(description="依赖锁文件校验")
    parser.add_argument("--lock", default=str(BACKEND_DIR / "requirements.lock"))
    args = parser.parse_args()

    lock_path = Path(args.lock)
    if not lock_path.is_file():
        print(f"[lock] 锁文件不存在：{lock_path}", file=sys.stderr)
        return 2

    locked = parse_lock(lock_path)
    installed = installed_packages()

    errors: list[str] = []
    for name, version in sorted(locked.items()):
        actual = installed.get(name)
        if actual is None:
            errors.append(f"缺少依赖：{name}=={version}（环境未安装）")
        elif actual != version:
            errors.append(f"版本漂移：{name} 锁={version} 环境={actual}")

    extra = sorted(set(installed) - set(locked) - BOOTSTRAP_PACKAGES)
    if extra:
        errors.append("环境包含锁文件未登记的包：" + "、".join(extra))

    if errors:
        print("[lock] 依赖锁校验失败：", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"[lock] 依赖锁校验通过：{len(locked)} 个发行包版本与 {lock_path.name} 完全一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
