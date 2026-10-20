"""logit / ologit 接进主链：三道门与端到端。

**数值对不对在 `test_discrete.py`**，这里只管「接得对不对、门响不响」。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from oss.core.analysis import PlanRejected, run_plan
from oss.core.contracts import AnalysisPlan


def _binary(n: int = 4000, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x, edu = rng.normal(size=n), rng.integers(6, 20, n)
    p = 1 / (1 + np.exp(-(-0.4 + 0.7 * x - 0.03 * edu)))
    return pd.DataFrame({"y": (rng.random(n) < p).astype(float), "x": x, "edu": edu,
                         "prov": rng.integers(0, 31, n), "wt": rng.uniform(.5, 2, n)})


def _plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="t", idea_id="i", outcome="y", treatment="x")
    return AnalysisPlan(**{**base, **kw})


def test_logit端到端_加权与聚类都接上() -> None:
    r = run_plan(_binary(), _plan(estimator="logit", controls=["edu"],
                                  cluster_level="prov", weights="wt"))
    assert r.n == 4000
    assert set(r.estimates) == {"main", "edu"}
    assert "prov" in r.diagnostics["标准误"]
    assert "加权" in r.diagnostics["估计量"]
    assert "有效样本量" in r.diagnostics["抽样权重"]


def test_logit报的是边际效应不是系数() -> None:
    """**系数是对数几率，读者判断不了量级。**odds ratio 也不行——
    「几率高 1.8 倍」在基线概率 2% 和 40% 的组里是完全不同的两回事。"""
    r = run_plan(_binary(), _plan(estimator="logit", controls=["edu"]))
    assert "平均边际效应" in r.diagnostics["估计量"]
    ame = r.estimates["main"].coef
    raw = float(r.diagnostics["logit 系数"].split("x ")[1].split("；")[0])
    assert abs(ame) < abs(raw) / 2, f"AME {ame} 跟系数 {raw} 太接近，多半报错了东西"
    assert "logit 系数" in r.diagnostics, "系数照存，只是不当主结果"


def test_门L2_固定效应每组太少就拒() -> None:
    """incidental parameters：组内观测少时**点估计本身有偏**，不是标准误的问题。"""
    rng = np.random.default_rng(2)
    n = 600
    df = pd.DataFrame({"y": (rng.random(n) < .5).astype(float), "x": rng.normal(size=n),
                       "g": rng.integers(0, 200, n)})
    with pytest.raises(PlanRejected) as e:
        run_plan(df, _plan(estimator="logit", fixed_effects=["g"]))
    msg = str(e.value.problems[0])
    assert "incidental" in msg and "ols_fe" in msg, "要给出替代去处，不能只说不行"


def test_门L2_组够大就放行() -> None:
    """**门不能一律拦**——固定效应本身没问题，组内观测少才有问题。"""
    rng = np.random.default_rng(3)
    n = 4000
    df = pd.DataFrame({"y": (rng.random(n) < .5).astype(float), "x": rng.normal(size=n),
                       "g": rng.integers(0, 20, n)})          # 每组 200 个
    r = run_plan(df, _plan(estimator="logit", fixed_effects=["g"]))
    assert r.n == n


def test_logit拒绝非01结局() -> None:
    rng = np.random.default_rng(4)
    df = pd.DataFrame({"y": rng.integers(0, 5, 500).astype(float), "x": rng.normal(size=500)})
    with pytest.raises(PlanRejected, match="ologit"):
        run_plan(df, _plan(estimator="logit"))


def _ordinal(n: int = 6000, seed: int = 4, parallel: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x, edu = rng.normal(size=n), rng.integers(6, 20, n).astype(float)
    if parallel:
        y = np.digitize(0.7 * x - 0.02 * edu + rng.logistic(size=n), [-1, 0, 1])
    else:
        u = rng.logistic(size=n)
        y = np.where(0.1 * x + u < -1, 0,
                     np.where(1.6 * x + u < 0, 1, np.where(-0.6 * x + u < 1, 2, 3)))
    return pd.DataFrame({"y": y.astype(float), "x": x, "edu": edu,
                         "wt": rng.uniform(.5, 2, n)})


def test_ologit端到端并且切点递增() -> None:
    r = run_plan(_ordinal(), _plan(estimator="ologit", controls=["edu"], weights="wt"))
    cuts = [float(c) for c in r.diagnostics["切点"].split("、")]
    assert cuts == sorted(cuts), f"切点必须递增：{cuts}"
    assert abs(r.estimates["main"].coef - 0.7) < 0.1, r.estimates["main"].coef


def test_门L3_平行线判词永远报() -> None:
    """**永远报，不只在不成立时报。**只在出问题时才出现的诊断项，
    使用者看不到它就不会想到去找它——这条在别处已经栽过。"""
    r = run_plan(_ordinal(parallel=True), _plan(estimator="ologit", controls=["edu"]))
    assert "平行线假定" in r.diagnostics
    assert "未被推翻" in r.diagnostics["平行线假定"], r.diagnostics["平行线假定"]


def test_门L3_不成立时说清后果与去处() -> None:
    r = run_plan(_ordinal(parallel=False), _plan(estimator="ologit"))
    text = r.diagnostics["平行线假定"]
    assert "不成立" in text
    assert "对哪一段都不成立" in text, "要说清后果"
    assert "二值 logit" in text or "局限" in text, "要给去处"


def test_门L3_判词里带各切点的系数() -> None:
    """只给一个卡方值没法处置——人要看见是哪几段不一样。"""
    r = run_plan(_ordinal(parallel=False), _plan(estimator="ologit"))
    assert "各切点分别估的系数" in r.diagnostics["平行线假定"]


def test_ologit拒绝固定效应() -> None:
    """切点与组内截距不可识别，硬做出来的系数没有意义。"""
    rng = np.random.default_rng(6)
    n = 900
    df = pd.DataFrame({"y": rng.integers(0, 4, n).astype(float), "x": rng.normal(size=n),
                       "g": rng.integers(0, 9, n)})
    with pytest.raises(PlanRejected) as e:
        run_plan(df, _plan(estimator="ologit", fixed_effects=["g"]))
    assert "不可识别" in str(e.value.problems[0])


def test_两个估计量都在受权重支持的清单里() -> None:
    from oss.core.contracts import WEIGHTED_ESTIMATORS
    assert "logit" in WEIGHTED_ESTIMATORS and "ologit" in WEIGHTED_ESTIMATORS
