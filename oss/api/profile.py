"""`oss profile`：数据画像。结构、变量、缺失、层次。

**写的命令**，所以走 `open_run`：目录不存在就建出来——第一条命令面对的
本来就是一个还不存在的运行目录。防打错路径的那道闸在只读的命令那一侧
（`require_run`），不在这里。
"""

from __future__ import annotations

from pathlib import Path

from oss.api.errors import BadInput, Missing, Refused
from oss.api.result import result
from oss.api.rundir import emit, open_run
from oss.core.contracts import ARTIFACTS
from oss.core.loaders import MissingDependency, UnsupportedFormat
from oss.core.profiler import profile_file


def finished_run(path: Path) -> bool:
    """这个运行目录是不是已经跑出过成品。有论文或结果就算。"""
    return (path / "paper.md").exists() or (path / ARTIFACTS["results"]).exists()


def profile(data: str, *, run: str, shape: bool = False, resume: bool = False) -> dict:
    """画像。`shape=True` 只给形状、不落盘。

    --shape 是 DECISIONS.md 5.1 的落点：合成 idea 的阶段只该看见形状，
    看见变量名模型就会顺着现有列凑问题。
    """
    rundir = open_run(run)
    if not shape and not resume and finished_run(rundir.path):
        # 真跑撞过车：同一天同一题，`mkdir -p` 不报错，直接往上一次的目录里写，
        # 十几个产出被盖掉，留痕两次运行混在一起，循环状态算的是两次的合集。
        # **一个目录一次研究。**接着旧的要显式说。
        raise Refused(
            f"{rundir.path} 里已经有一次完成的运行（有 paper.md / results.json）。"
            f"**新研究开新目录**——名字加个时间或序号；确实要接着这次继续就加 --resume",
            code=1)
    try:
        prof = profile_file(data)
    except FileNotFoundError as exc:
        raise Missing("data", str(exc), code=1) from exc
    except (UnsupportedFormat, MissingDependency) as exc:
        raise BadInput(str(exc), code=1) from exc

    if shape:
        return result("形状摘要", {"run": str(rundir.path),
                                   "shape_summary": prof.shape_summary()})

    note = f"{prof.file.n_rows} 行 × {prof.file.n_cols} 列，判为 {prof.structure.kind}"
    path = emit(rundir, "profile", f"oss profile {data}", "profile", prof,
                note=note, args={"data": str(data), "data_sha256": prof.file.sha256})
    return result(note, {
        "run": str(rundir.path), "artifact": ARTIFACTS["profile"], "path": str(path),
        "n_rows": prof.file.n_rows, "n_cols": prof.file.n_cols,
        "structure": prof.structure.kind, "warnings": list(prof.warnings),
        "data_sha256": prof.file.sha256,
    })
