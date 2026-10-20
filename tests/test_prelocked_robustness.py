"""「哪些稳健性项是事前锁定的」——判据挂在留痕上，不挂在计划里的声明字段上。

**为什么要有这件事**：事前锁定的稳健性项是一句承诺（分析开始之前就写下要查什么），
看过结果才加的是一次回应。两者可信度完全不同，论文必须分开报。

原先分不出来，于是「稳健性清单不许超过 12 项」这条判据会把审稿人指名要求补做的
诊断也算进去——**罚的是照要求补做，这个方向是反的**。因果那篇按编辑给的「改完标准」
补了 23 项，合规检查就此判不过。

判据不能挂在声明上：谁都能在计划里写一句「这项是事前锁定的」，但没人能改写
第一次成功跑分析那一刻盘上是什么。所以每次成功跑分析时把清单的项名记进留痕，
「事前锁定」= 第一次成功跑时就在清单里的那些。
"""

from __future__ import annotations

import json
from pathlib import Path

from oss.core.loop import prelocked_robustness


def _trace(run: Path, rows: list[dict]) -> None:
    run.mkdir(parents=True, exist_ok=True)
    (run / "trace.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _run_row(seq: int, labels: list[str] | None, outcome: str = "ok") -> dict:
    args: dict = {"plan_sha": "abc123"}
    if labels is not None:
        args["robustness"] = labels
    return {"seq": seq, "step": "results", "command": "oss run", "outcome": outcome, "args": args}


def test_取第一次成功跑时的清单(tmp_path: Path) -> None:
    run = tmp_path / "r"
    _trace(run, [
        _run_row(1, ["甲", "乙"]),
        _run_row(2, ["甲", "乙", "丙", "丁"]),      # 后面加的不算
    ])
    assert prelocked_robustness(run) == {"甲", "乙"}


def test_被拒的那次不算(tmp_path: Path) -> None:
    """计划过不了校验的那次没产出任何结果，**不能当成「分析跑过了」**。"""
    run = tmp_path / "r"
    _trace(run, [
        _run_row(1, ["只是手滑"], outcome="refused"),
        _run_row(2, ["甲", "乙"]),
    ])
    assert prelocked_robustness(run) == {"甲", "乙"}


def test_留痕里没这一格就不判(tmp_path: Path) -> None:
    """老运行目录的留痕没有这一格。**返回 None 表示「查不到」，不是「一项都没锁定」**——
    把查不到当成零会让老目录里每一项都被判成事后补的。
    """
    run = tmp_path / "r"
    _trace(run, [_run_row(1, None), _run_row(2, None)])
    assert prelocked_robustness(run) is None


def test_还没跑过分析就不判(tmp_path: Path) -> None:
    run = tmp_path / "r"
    _trace(run, [{"seq": 1, "step": "profile", "command": "oss profile", "outcome": "ok"}])
    assert prelocked_robustness(run) is None


def test_一项都没锁定与查不到要分开(tmp_path: Path) -> None:
    """第一次跑时清单确实是空的 → 空集合（**能判**，判出来是零项）；
    留痕没记 → None（判不了）。两者不是一回事。"""
    run = tmp_path / "r"
    _trace(run, [_run_row(1, [])])
    assert prelocked_robustness(run) == set()


def test_上限只管事前锁定的那些(tmp_path: Path) -> None:
    """13 项里 8 项是事前锁定的 → 不该报警；13 项全是事前锁定的 → 该报警。"""
    import numpy as np
    import pandas as pd

    from oss.api import profile as profile_api
    from oss.core.analysis import validate_plan
    from oss.core.contracts import AnalysisPlan, DataProfile

    rng = np.random.default_rng(0)
    df = pd.DataFrame({"y": rng.normal(size=80), "x": rng.normal(size=80),
                       "c": rng.normal(size=80)})
    d = tmp_path / "p"
    d.mkdir()
    df.to_csv(d / "data.csv", index=False)
    profile_api.profile(str(d / "data.csv"), run=str(d))
    prof = DataProfile.load(d / "profile.json")
    items = [{"label": f"第{i}项", "rationale": "试", "executable": True} for i in range(13)]
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y",
                        treatment="x", controls=["c"], robustness=items)

    def warns(prelocked):
        _, w = validate_plan(plan, prof, df, prelocked=prelocked)
        return [x for x in w if "稳健性清单" in str(x)]

    assert warns({f"第{i}项" for i in range(8)}) == []          # 只有 8 项是锁定的
    assert warns({f"第{i}项" for i in range(13)})               # 13 项全是锁定的 → 报
    assert warns(None)                                          # 判不了时退回按总数判 → 报
