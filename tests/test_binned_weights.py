"""分箱斜率要加权。**「二元的原始形状」说的是不带控制变量，不是不带权重。**

去掉控制变量换的是一个量（条件关联 → 边际关联）；
**去掉权重换的是一个总体**——样本里各类人的比例与目标总体不同，
加权正是为了把它调回去。

危险在于这张图是拿去跟主结果对照的：主结果加权、图不加权，
读者看到两条形状不同的线，会读成「线性假定不成立」，而真因只是口径不同。
真跑时撞过一次（CGSS 过劳那一轮），差点据此改写结论。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from oss.core.analysis import binned_slopes


def _frame() -> pd.DataFrame:
    """造一份「加权与不加权反号」的数据。

    两个亚群：多数群（权重小）斜率为负，少数群（权重大）斜率为正。
    不加权时多数群说了算 → 负；加权之后少数群的权重把它拉成正。
    **这不是极端构造**：抽样设计里对小群体过抽是常态，权重正是用来纠正它的。
    """
    rng = np.random.default_rng(0)
    rows = []
    for g, n, slope, w in ((0, 900, -1.0, 0.2), (1, 100, +3.0, 9.0)):
        x = rng.uniform(0, 10, n)
        rows.append(pd.DataFrame({
            "x": x, "y": slope * x + rng.normal(0, 0.5, n),
            "by": rng.uniform(0, 10, n), "w": w, "g": g}))
    return pd.concat(rows, ignore_index=True)


def _mean_slope(series) -> float:
    pts = series[0][1]
    return float(np.mean([b for _c, b, _se, _n in pts]))


def test_不加权与加权会反号() -> None:
    """先证明这个风险是真的——**判据本身要先站得住**。"""
    df = _frame()
    plain = _mean_slope(binned_slopes(df, "y", "x", "by", bins=4))
    weighted = _mean_slope(binned_slopes(df, "y", "x", "by", bins=4, weights="w"))
    assert plain < 0 < weighted, f"不加权 {plain:.2f}，加权 {weighted:.2f}——这份夹具没造出反号"


def test_图现算时也要加权(tmp_path) -> None:
    """`marginal` 在计划里没有匹配的分箱规格时会现算。**那条路原先不加权**，
    于是同一张图上的形状跟加权的主结果可能反号。"""
    pytest.importorskip("matplotlib")
    from oss.core import figures

    df = _frame()
    # `marginal` 在函数体内 `from oss.core.analysis import binned_slopes`，
    # 所以要打桩在 analysis 上
    from oss.core import analysis

    grabbed: list = []
    real = analysis.binned_slopes
    analysis.binned_slopes = lambda *a, **k: (grabbed.append(k.get("weights")), real(*a, **k))[1]
    try:
        figures.marginal(df, "y", "x", "by", tmp_path / "m.png", bins=4, weights="w")
    finally:
        analysis.binned_slopes = real
    assert grabbed == ["w"], f"现算时没把权重传下去：{grabbed}"


def test_权重列不在数据里就不传(tmp_path) -> None:
    """计划声明了权重但这张图的数据里没有那一列时，**不能硬传**——
    传了会在 `binned_slopes` 里抛 KeyError，一张图就此画不出来。"""
    pytest.importorskip("matplotlib")
    from oss.core import figures

    df = _frame().drop(columns=["w"])
    from oss.core import analysis

    grabbed: list = []
    real = analysis.binned_slopes
    analysis.binned_slopes = lambda *a, **k: (grabbed.append(k.get("weights")), real(*a, **k))[1]
    try:
        figures.marginal(df, "y", "x", "by", tmp_path / "m.png", bins=4, weights="w")
    finally:
        analysis.binned_slopes = real
    assert grabbed == [""]


def test_主链那一处一直是加权的() -> None:
    """`_run_binned` 调 `binned_slopes` 时传了 `plan.weights`——这一处早就是对的，
    钉住它，免得以后改回去。"""
    import inspect

    from oss.core import analysis

    src = inspect.getsource(analysis._run_binned)
    assert "weights=plan.weights" in src
