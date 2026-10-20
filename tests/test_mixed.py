"""多层模型：数值核对与三道门。

**方差分解是它存在的理由，不是「处理聚类」**——门 M1 挡的正是后者，
而那不是技术错误，是用错工具。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from oss.core.analysis import PlanRejected, run_plan
from oss.core.contracts import AnalysisPlan, MixedSpec

pytest.importorskip("statsmodels.regression.mixed_linear_model")

VAR_U, VAR_E = 0.6, 1.4
TRUE_ICC = VAR_U / (VAR_U + VAR_E)


def _panel(groups: int = 60, per: int = 120, seed: int = 0) -> pd.DataFrame:
    """方差分量已知的两层数据：ICC 真值 0.30。"""
    rng = np.random.default_rng(seed)
    u = rng.normal(0, np.sqrt(VAR_U), groups)
    g = np.repeat(np.arange(groups), per)
    x = rng.normal(size=groups * per)
    y = 1.0 + 0.5 * x + u[g] + rng.normal(0, np.sqrt(VAR_E), groups * per)
    return pd.DataFrame({"y": y, "x": x, "prov": g,
                         "wt": rng.uniform(0.5, 2, groups * per)})


def _plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="t", idea_id="i", estimator="mixed", outcome="y", treatment="x")
    return AnalysisPlan(**{**base, **kw})


def test_方差分量与ICC估回真值() -> None:
    from oss.core.multilevel import fit_random_intercept

    f = fit_random_intercept(_panel(groups=200), "y", ["x"], "prov")
    assert abs(f.var_resid - VAR_E) < 0.06, f"组内方差 {f.var_resid}"
    assert abs(f.var_group - VAR_U) < 0.12, f"组间方差 {f.var_group}"
    assert abs(f.icc - TRUE_ICC) < 0.05, f"ICC {f.icc}"


def test_组数加大时偏差要缩小() -> None:
    """**这一条抓的是刻度错，不是噪声。**

    第一版把 `cov_re` 又乘了一次 `scale`（它本来就是绝对刻度），
    ICC 从 0.30 偏到 0.36。单看一次跑分辨不出是偏差还是抽样波动——
    **组数加大时噪声会缩小、系统偏差不会**，这就是判据。
    """
    from oss.core.multilevel import fit_random_intercept

    bias = {}
    for g in (60, 300):
        vals = [fit_random_intercept(_panel(groups=g, per=60, seed=s), "y", ["x"], "prov").icc
                for s in range(6)]
        bias[g] = abs(float(np.mean(vals)) - TRUE_ICC)
    assert bias[300] < 0.03, f"300 组时偏差还有 {bias[300]:.4f}——多半是刻度错了"
    assert bias[300] <= bias[60] + 0.01, f"组数加大偏差没缩小：{bias}"


def test_ICC标准误跟重复抽样的离散度对得上() -> None:
    from oss.core.multilevel import fit_random_intercept

    fits = [fit_random_intercept(_panel(groups=80, per=100, seed=100 + s), "y", ["x"], "prov")
            for s in range(12)]
    spread = float(np.std([f.icc for f in fits]))
    analytic = float(np.nanmean([f.icc_se for f in fits]))
    assert 0.6 < analytic / spread < 1.6, f"解析 {analytic:.4f} vs 实际离散 {spread:.4f}"


def test_端到端主结果是ICC() -> None:
    r = run_plan(_panel(), _plan(mixed=MixedSpec(group="prov", rationale="variance")))
    assert r.estimates["main"].term == "ICC", "主结果必须是 ICC，不是某个回归系数"
    assert abs(r.estimates["main"].coef - TRUE_ICC) < 0.06
    assert abs(r.estimates["x"].coef - 0.5) < 0.05
    assert "ICC" in r.diagnostics["估计量"]
    assert "方差分量" in r.diagnostics


def test_门M1_没写理由就拒() -> None:
    """**多层模型不是「处理聚类」的工具。**没说清用它干什么就不给跑。"""
    with pytest.raises(PlanRejected) as e:
        run_plan(_panel(), _plan(mixed=MixedSpec(group="prov")))
    msg = str(e.value.problems[0])
    assert "ols_fe" in msg and "cluster_level" in msg, "要给出替代去处"


def test_门M1_只为处理聚类的直接拒() -> None:
    """这道门挡的不是技术错误，是**用错工具**——审稿人最常问、作者最答不上来的那种。"""
    with pytest.raises(PlanRejected) as e:
        run_plan(_panel(), _plan(mixed=MixedSpec(group="prov", rationale="se")))
    msg = str(e.value.problems[0])
    assert "更稳" in msg and "ols_fe" in msg


def test_门M1_整块没写也拒() -> None:
    with pytest.raises(PlanRejected, match="plan.mixed"):
        run_plan(_panel(), _plan())


def test_门M2_组太少拒_并给去处() -> None:
    with pytest.raises(PlanRejected) as e:
        run_plan(_panel(groups=8, per=200),
                 _plan(mixed=MixedSpec(group="prov", rationale="variance")))
    msg = str(e.value.problems[0])
    assert "噪声" in msg and "fixed_effects" in msg, "要说清为什么，并给去处"


def test_门M2_组偏少只警告不拦() -> None:
    """**够用就别拦。**25 组估得出来，只是区间宽——那是提醒，不是错误。"""
    r = run_plan(_panel(groups=25, per=200),
                 _plan(mixed=MixedSpec(group="prov", rationale="variance")))
    assert "二层单位数偏少" in r.diagnostics
    assert "区间" in r.diagnostics["二层单位数偏少"]


def test_门M3_写了权重要降级并说清楚() -> None:
    """**不假装能做。**多层模型的加权要两层权重，调查数据通常只给一层；
    硬按一层跑出来的方差分量是错的，**而它看起来是对的**。"""
    r = run_plan(_panel(), _plan(weights="wt",
                                 mixed=MixedSpec(group="prov", rationale="variance")))
    t = r.diagnostics["抽样权重"]
    assert "未加权" in t and "局限" in t, t
    assert "不外推到总体" in t


def test_mixed不在受权重支持的清单里() -> None:
    """它根本不加权——**列进去就是骗人**。"""
    from oss.core.contracts import WEIGHTED_ESTIMATORS
    assert "mixed" not in WEIGHTED_ESTIMATORS


def test_没装statsmodels时如实说(monkeypatch) -> None:
    """**能力缺失要明说**，还要把安装命令报出来——照 did_cs 对 statspai 那套。"""
    from oss.core import multilevel

    monkeypatch.setattr(multilevel, "available", lambda: False)
    with pytest.raises(PlanRejected) as e:
        run_plan(_panel(), _plan(mixed=MixedSpec(group="prov", rationale="variance")))
    assert "statsmodels" in str(e.value.problems[0])
    assert "pip install" in str(e.value.problems[0])
