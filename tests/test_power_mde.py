"""最小可测效应（MDE）：筛候选用的那个数。

idea 重构第 ⑤ 段（经验预检与重排）要对**多个候选**判「这题在这份数据上测不测得出来」。
判不了的做法是拿命令行缺省的 0.2 去算功效——那正是 `effect_size_source` 那一栏
反复在拦的事（验收跑 r20260907_1938 拿缺省值算出「功效 0.83 / pass」，论文却写
「按文献报告的效应量」）。

**MDE 不需要先有效应量估计**：它只取决于有效样本量，回答的是「这份数据最小能测出
多大的效应」。所以它是候选分诊唯一诚实的那个数，也是 ARIS 原版 Pilot Ladder 里
「先读 mde_at_target_power」那一条的落点。

公式跟本模块的功效公式同源：功效 = Φ(d·√n_eff/2 − z_{α/2})，
令功效 = 0.8 反解，d_MDE = 2·(z_{α/2} + z_{0.8}) / √n_eff。

**离线，不叫模型。**
"""

from __future__ import annotations

import math

import pytest

from oss.core import power as pw
from oss.core.contracts import PowerReport


def _mde_by_hand(n_eff: float, alpha: float = 0.05) -> float:
    return 2 * (pw._z(1 - alpha / 2) + pw._z(pw.MDE_TARGET_POWER)) / math.sqrt(n_eff)


def test_报告里有mde_按有效样本量算() -> None:
    rep = pw.analyze(effect_size=0.2, n=4000, idea_id="i")
    assert rep.mde == pytest.approx(_mde_by_hand(rep.n_effective), rel=1e-3)


def test_mde不随效应量变() -> None:
    """筛查要的就是这一点：不用先知道效应量多大，也答得出「测不测得出来」。"""
    a = pw.analyze(effect_size=0.1, n=4000, idea_id="i")
    b = pw.analyze(effect_size=0.8, n=4000, idea_id="i")
    assert a.power != b.power, "两条的功效本该不同，否则这个用例什么都没测"
    assert a.mde == pytest.approx(b.mde, rel=1e-9)


def test_样本越大mde越小() -> None:
    small = pw.analyze(effect_size=0.2, n=400, idea_id="i")
    big = pw.analyze(effect_size=0.2, n=40000, idea_id="i")
    assert big.mde < small.mde


def test_聚类把mde抬上去() -> None:
    """整群设计效应压低有效样本量，MDE 必须跟着变大——否则聚类被悄悄忽略了。"""
    flat = pw.analyze(effect_size=0.2, n=12000, idea_id="i")
    clustered = pw.analyze(effect_size=0.2, n=12000, n_clusters=480, icc=0.05, idea_id="i")
    assert clustered.n_effective < flat.n_effective
    assert clustered.mde > flat.mde


def test_老报告没有这一栏也读得出来() -> None:
    """只增不改：老运行目录的 power.json 仍要能加载。"""
    rep = PowerReport.model_validate({"idea_id": "i", "power": 0.9, "verdict": "pass"})
    assert rep.mde == 0.0


def test_有效样本量为零时不炸() -> None:
    """筛查会在各种残缺的候选上跑，除零不能把整段预检打断。"""
    rep = pw.analyze(effect_size=0.2, n=0, idea_id="i")
    assert rep.mde == 0.0


# ══════════════════════════════════════════════════════ 只数分析真会用到的行

def _rundir(tmp_path, n=400, missing_share=0.5):
    """一个刚做完绑定、还没写计划的运行目录：处理变量有一半缺失。"""
    import csv
    from oss.api import profile as profile_api
    from oss.api.rundir import require_run
    from oss.core.contracts import BindingReport, Idea, IdeaSet, VariableBinding

    run = tmp_path / "r"; run.mkdir()
    data = run / "d.csv"
    with data.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["unit", "year", "y", "x"])
        for i in range(n):
            x = "" if i < int(n * missing_share) else f"{i % 17}"
            w.writerow([f"c{i % 20}", 1990 + i % 20, f"{(i % 13) / 13:.3f}", x])
    profile_api.profile(str(data), run=str(run))
    IdeaSet(direction="d", generated_n=1, ideas=[Idea(
        idea_id="i1", title="t", research_question="q", research_type="causal")]).save(run / "ideas.json")
    BindingReport(idea_id="i1", core_ok=True, bindings=[
        VariableBinding(construct_name="结局", role="outcome", column="y", status="bound"),
        VariableBinding(construct_name="处理", role="treatment", column="x", status="bound"),
    ]).save(run / "binding.json")
    return run


def test_功效不按全文件行数算_按绑上的结局与处理都非缺失的行(tmp_path) -> None:
    """真跑 r20260916_netdem 撞出来的：wdi_internet 缺 54%，`oss power` 却按全量
    11136 行算，MDE 报 0.074，按可用的 5148 行算是 0.122——**偏乐观六成半**。

    分诊靠的就是这个数：偏乐观会把做不了的题排到前面去。精度模式本来就「只数分析
    真会用到的行」，功效模式一直没跟上。
    """
    from oss.api import power as power_api

    run = _rundir(tmp_path, n=400, missing_share=0.5)
    res = power_api.power(str(run), idea="i1", effect=0.3,
                          effect_source="测试：只看行数怎么数")
    import json
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    assert rep["n_raw"] == 200, f"该按处理非缺失的 200 行算，实际 {rep['n_raw']}"
    warns = res["data"]["warnings"]
    assert any("分析样本" in w for w in warns), warns
    assert any("显式给 --n" in w for w in warns), "得告诉用户怎么覆盖这个数"


def test_显式给了n就照给的算(tmp_path) -> None:
    """`--n` 是用户显式声明的，不许被悄悄改掉。"""
    from oss.api import power as power_api

    run = _rundir(tmp_path, n=400, missing_share=0.5)
    power_api.power(str(run), idea="i1", effect=0.3, n=999,
                    effect_source="测试：显式 n")
    import json
    assert json.loads((run / "power.json").read_text(encoding="utf-8"))["n_raw"] == 999
