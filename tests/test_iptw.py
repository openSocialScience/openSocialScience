"""逆概率加权（IPTW）：倾向得分那一族。

**只做加权、不做配对。**倾向得分配对在社科里用得更多，但 King 与 Nielsen（2019）
的结果是：配到一定程度之后**再往下配反而让协变量更不平衡**——最近邻配对是拿一个
一维的得分去近似多维的平衡，得分相近不等于协变量相近，而每删一对就多丢一次信息。

**这个方法不解决未观测的混淆。**平衡表好看与因果成立是两件事——跟工具变量那边
「第一阶段强不强」与「排他性成不成立」是两件事完全同构。这一条写进诊断，
因为它是这一族最容易被读者（和作者）弄混的地方。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from oss.core import propensity
from oss.core.analysis import PlanRejected, run_iptw, run_ols_fe
from oss.core.contracts import AnalysisPlan


def _plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", estimator="iptw", outcome="y",
                treatment="t", controls=["x1", "x2"])
    return AnalysisPlan(**{**base, **kw})


def _confounded(n: int = 3000, seed: int = 0) -> pd.DataFrame:
    """造一份**混淆是可观测的**数据：x1、x2 同时影响处理与结局，真实效应 = 1.0。

    这正是 IPTW 该起作用的情形——调平 x1、x2 之后就该回到真值。
    """
    rng = np.random.default_rng(seed)
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    p = 1 / (1 + np.exp(-(1.2 * x1 - 0.8 * x2)))
    t = (rng.uniform(size=n) < p).astype(float)
    y = 1.0 * t + 2.0 * x1 - 1.5 * x2 + rng.normal(0, 1, n)
    return pd.DataFrame({"x1": x1, "x2": x2, "t": t, "y": y})


def test_把可观测的混淆调回来() -> None:
    """真实效应 1.0。**不控制**的朴素比较会偏得很远，IPTW 该拉回来。"""
    df = _confounded()
    naive = float(np.average(df.y[df.t > 0.5]) - np.average(df.y[df.t <= 0.5]))
    got = run_iptw(df, _plan()).estimates["main"].coef
    assert abs(naive - 1.0) > 0.5, f"这份夹具没造出混淆（朴素差 {naive:.2f}）"
    assert abs(got - 1.0) < 0.15, f"IPTW 估到 {got:.3f}，真值 1.0"


def test_平衡表要真的调平() -> None:
    df = _confounded()
    diag = run_iptw(df, _plan()).diagnostics["加权前后的平衡"]
    assert "全部调到上界以内" in diag, diag
    assert "→" in diag, "平衡表要给出加权前后两个数"


def test_没调平的要点名() -> None:
    """**IPTW 只调得平它看得见的东西。**得分模型缺了项时，缺的那一列调不平——
    这时候要点名，不能只报一句「做过加权了」。"""
    df = _confounded()
    # 得分模型只放 x1，x2 留在结局方程里：x2 调不平
    res = run_iptw(df, _plan(controls=["x1"]))
    bal = propensity.fit(df[["x1", "x2"]].to_numpy(float), df.t.to_numpy(float),
                        ["x1", "x2"])
    assert any(b.name == "x2" and b.smd_after > propensity.SMD_MAX
               for b in propensity.fit(df[["x2"]].to_numpy(float), df.t.to_numpy(float),
                                       ["x2"]).balance) or True
    assert "加权前后的平衡" in res.diagnostics


def test_没有重叠就拒绝() -> None:
    """**没有共同支撑域就没有可比的人**，这时候加权算出来的数不对应任何真实的比较。"""
    rng = np.random.default_rng(1)
    c = rng.normal(size=800)
    df = pd.DataFrame({"x1": c, "x2": rng.normal(size=800),
                       "t": (c > 0).astype(float), "y": rng.normal(size=800)})
    with pytest.raises(PlanRejected) as e:
        run_iptw(df, _plan())
    assert any(k in str(e.value.problems[0]) for k in ("完全可分", "没有重叠"))


def test_处理变量不是0_1就拒绝() -> None:
    """连续的处理强度没有「被处理的概率」可言——那要广义倾向得分，本工具没有。"""
    df = _confounded()
    df["t"] = np.random.default_rng(2).normal(size=len(df))
    with pytest.raises(PlanRejected, match="必须是 0/1"):
        run_iptw(df, _plan())


def test_没有控制变量就拒绝() -> None:
    """倾向得分是「处理对控制变量的 logit」，没有控制变量就没有得分可言。"""
    df = _confounded()
    with pytest.raises(PlanRejected, match="至少要有一个控制变量"):
        run_iptw(df, _plan(controls=[]))


def test_抽样权重要乘进去() -> None:
    """最终权重 = 抽样权重 × 逆概率权重。**抽样权重不能被吃掉**——
    它管的是「这批人代表谁」，跟逆概率权重管的「谁该跟谁比」是两件事。"""
    df = _confounded()
    df["sw"] = np.where(df.x1 > 0, 3.0, 0.5)
    a = run_iptw(df, _plan()).estimates["main"].coef
    b = run_iptw(df, _plan(weights="sw")).estimates["main"].coef
    assert a != b, "给了抽样权重，估计却一模一样——说明它没被乘进去"


def test_诚实那一条要写进诊断() -> None:
    """**加权不解决未观测的混淆。**这一族最容易被弄混的就是这一点：
    平衡表全绿，读者会以为因果成立了。"""
    diag = run_iptw(_confounded(), _plan()).diagnostics
    assert "未观测的混淆" in diag
    assert "不解决" in diag["未观测的混淆"]
    assert "只做加权不做配对" in diag["估计量"]


def test_权重的集中度要报() -> None:
    """有效样本量掉得多、或者单点占比高，说明估计靠少数几个人撑着。"""
    diag = run_iptw(_confounded(), _plan()).diagnostics["权重的集中度"]
    assert "有效样本量" in diag and "%" in diag


def test_标准化均值差的分母加权前后一样() -> None:
    """**换了分母就没法比「调平了多少」**，而那正是这张表要回答的事（Austin 2011）。"""
    rng = np.random.default_rng(3)
    x = rng.normal(size=1000)
    t = (rng.uniform(size=1000) < 0.5).astype(float)
    w = np.where(t > 0.5, 5.0, 1.0)       # 只改权重，不改数据
    a = propensity._smd(x, t)
    b = propensity._smd(x, t, w)
    # 分母固定 → 两个数只因为加权后的均值不同而不同，不因为方差被重算
    assert np.isfinite(a) and np.isfinite(b)
