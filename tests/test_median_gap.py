"""按组的中位数差（设计稿 docs/design/2026-09-23-median-gap.md）。

收入、财富、工时右偏，均值被尾部拉走——社科描述统计里中位数是标配，而这套工具箱
**以前写不出「男女中位数财富差距」这句话**：`gap` 只算均值差，`distribution` 只对全样本报分位数。
2026-09-23 在 DiscoveryBench 上撞出来（问「哪一年男女中位数财富差距最大」，按均值算出的是
1996 年，标准答案是 1985 年——算得越认真错得越确定）。

中位数的区间没有解析公式，用自助法；**抽的是方差单位**（初级抽样单位 / 簇 / 行），
跟标准误那三条路同一个口径。离线，不叫模型。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from oss.core.analysis import PlanRejected, run_describe
from oss.core.contracts import AnalysisPlan, DescriptionSpec


def _plan(**kw) -> AnalysisPlan:
    desc = {"kind": "gap", "groups": ["sex"], "statistic": "median"}
    desc.update(kw.pop("description", {}))
    return AnalysisPlan(plan_id="p", outcome="income", estimator="describe",
                        description=DescriptionSpec(**desc), **kw)


def _skewed(n: int = 600, seed: int = 1) -> pd.DataFrame:
    """右偏的收入：男性中位数明显更高（真值 e^10.5 对 e^10.0）。"""
    rng = np.random.default_rng(seed)
    sex = np.where(rng.random(n) < 0.5, "male", "female")
    return pd.DataFrame({"sex": sex,
                         "income": rng.lognormal(np.where(sex == "male", 10.5, 10.0), 1.0)})


def test_中位数不是均值_右偏数据上差得很远():
    """这是要这个功能的理由：均值被尾部拉走。"""
    f = _skewed()
    med = run_describe(f, _plan()).groups
    mean = run_describe(f, _plan(description={"statistic": "mean"})).groups
    for g in med:
        assert g.statistic == "median"
        same = next(x for x in mean if x.label == g.label)
        assert g.mean < same.mean, "右偏数据上中位数应当明显低于均值"


def test_点估计与区间_盖得住真值():
    f = _skewed()
    cells = {g.label: g for g in run_describe(f, _plan()).groups}
    for label, truth in (("sex=female", np.exp(10.0)), ("sex=male", np.exp(10.5))):
        g = cells[label]
        assert g.ci_lo < truth < g.ci_hi, f"{label} 的区间没盖住真值：[{g.ci_lo}, {g.ci_hi}] vs {truth}"


def test_整群抽样的区间更宽_说明自助法抽的是簇不是行():
    """**这条是这套实现存在的理由。**按行重抽会把整群数据的区间算窄——不报错的那种错。"""
    rng = np.random.default_rng(5)
    village = np.repeat(np.arange(40), 30)
    y = np.exp(10 + rng.normal(0, 0.8, 40)[village] + rng.normal(0, 0.6, 1200))
    f = pd.DataFrame({"village": village, "income": y})

    def width(cluster: str) -> float:
        plan = AnalysisPlan(plan_id="p", outcome="income", estimator="describe",
                            cluster_level=cluster,
                            description=DescriptionSpec(kind="level", statistic="median"))
        g = run_describe(f, plan).groups[0]
        return g.ci_hi - g.ci_lo

    assert width("village") > 2 * width(""), "按村聚类的区间没有明显变宽，自助法多半在按行抽"


def test_同一份数据同一份计划跑两次_区间一字不差():
    """不可复现的区间等于没有区间。种子由计划与数据定死。"""
    f = _skewed()
    a, b = run_describe(f, _plan()).groups[1], run_describe(f, _plan()).groups[1]
    assert (a.mean, a.se, a.ci_lo, a.ci_hi, a.gap, a.gap_p) == (b.mean, b.se, b.ci_lo, b.ci_hi, b.gap, b.gap_p)


def test_加权改变中位数():
    """给高收入那一半大权重，加权中位数要跟着上去。"""
    rng = np.random.default_rng(3)
    income = rng.lognormal(10.0, 1.0, 800)
    f = pd.DataFrame({"sex": ["male", "female"] * 400, "income": income,
                      "w": np.where(income > np.median(income), 5.0, 1.0)})
    plain = run_describe(f, _plan(description={"kind": "level"})).groups[0].mean
    weighted = run_describe(f, _plan(weights="w", description={"kind": "level"})).groups[0].mean
    assert weighted > plain, "加权没起作用"


def test_0和1的结局拒绝用中位数():
    """二值变量的中位数不是 0 就是 1，说明不了任何事。"""
    f = pd.DataFrame({"sex": ["male", "female"] * 100, "income": [0.0, 1.0] * 100})
    with pytest.raises(PlanRejected) as e:
        run_describe(f, _plan())
    assert "0/1 结局不许用中位数" in str(e.value)


def test_趋势不支持中位数():
    f = _skewed()
    f["year"] = np.tile([2010, 2015], len(f) // 2)
    with pytest.raises(PlanRejected) as e:
        run_describe(f, _plan(description={"kind": "trend", "over": "year", "groups": []}))
    assert "只支持 level 与 gap" in str(e.value)


def test_诊断说清是中位数与自助法():
    """读结果的人要知道这个数是怎么来的。"""
    r = run_describe(_skewed(), _plan())
    assert "中位数" in r.diagnostics["估计量"] and "自助法" in r.diagnostics["估计量"]


# ─────────────────────────────── 事前精度也要按中位数算

def test_功效那一步的半宽来自自助法_不是均值公式(tmp_path):
    """计划写中位数、精度却按均值的解析公式算，就是把判词挂在一个算错的数上——
    跟 2026-09-23 一整天修的那类洞同形（门挂在声明上，不挂在算得出的事实上）。"""
    import json

    from oss.api import power as power_api
    from oss.api import profile as profile_api

    run = tmp_path / "r"
    run.mkdir()
    f = _skewed(800, seed=11)
    f.to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    (run / "ideas.json").write_text(json.dumps({"direction": "d", "ideas": [
        {"idea_id": "idea_001", "title": "t", "research_question": "男女中位数收入差多少",
         "lens": "user-idea", "source": "user", "research_type": "descriptive"}]},
        ensure_ascii=False), encoding="utf-8")
    plan = {"plan_id": "p1", "idea_id": "idea_001", "research_type": "descriptive",
            "estimator": "describe", "outcome": "income",
            "estimand": {"unit_quantity": "中位数收入的性别差", "target_population": "全部样本",
                         "assumptions": ["样本即总体"]},
            "description": {"kind": "gap", "groups": ["sex"], "statistic": "median"}}
    (run / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    d = power_api.power(str(run), idea="idea_001", effect=0.3, effect_source="测试假定值")["data"]
    assert d["mode"] == "precision"
    assert any("自助法" in line for line in d["reason_lines"]), d["reason_lines"]
    assert any("中位数的标准误没有解析式" in line for line in d["reason_lines"])
