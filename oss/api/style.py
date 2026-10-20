"""风格包：paper 用得到的那一半。

`oss style build / template / check` 三条命令不在阶段 3 的子集里，仍留在 CLI；
它们要的目录定位跟 `paper check --style` / `paper export --style` 是同一件事，
所以先把这一个函数搬出来共用，其余等阶段 4 再说。
"""

from __future__ import annotations

from pathlib import Path

from oss.api.errors import BadInput


def pack_dir(name: str) -> Path:
    """风格包名或路径 → 目录。找不到抛 BadInput（退出码 1，与原先一致）。"""
    p = Path(name)
    if not p.exists():
        p = Path("styles") / name
    if not p.exists():
        raise BadInput(f"找不到风格包 {name}（styles/{name}/ 或一个路径）", code=1)
    return p
