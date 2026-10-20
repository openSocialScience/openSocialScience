"""二值与有序 Logit 的数值核对。

**这一层只管「算得对不对」**，门在 `analysis.py`、判断在说明书。

核对的办法是拿已知答案对：模拟数据的真值、statsmodels 的结果、数值差分。
**不跟自己对**——跟自己对只能证明没改坏，证明不了一开始就是对的。
"""

from __future__ import annotations

from math import erf, sqrt

import numpy as np
import pytest

from oss.core.discrete import (
    NotConverged, ame, binary_logit, ordered_logit, parallel_lines, score_matrix,
)


def _chi2_p(stat: float, df: int) -> float:
    """Wilson–Hilferty 近似的卡方上尾概率。只用来判量级，不进产品。"""
    if df <= 0:
        return 1.0
    z = ((stat / df) ** (1 / 3) - (1 - 2 / (9 * df))) / sqrt(2 / (9 * df))
    return 1 - 0.5 * (1 + erf(z / sqrt(2)))


@pytest.fixture(scope="module")
def binary():
    rng = np.random.default_rng(20260925)
    n = 20000
    x1, x2 = rng.normal(size=n), rng.binomial(1, 0.4, n)
    X = np.column_stack([np.ones(n), x1, x2])
    true = np.array([-0.5, 0.8, -0.6])
    y = (rng.random(n) < 1 / (1 + np.exp(-(X @ true)))).astype(float)
    return X, y, true


def test_二值logit估回真值(binary) -> None:
    X, y, true = binary
    fit = binary_logit(X, y)
    for b, s, t in zip(fit.beta, fit.se, true):
        assert abs(b - t) < 3 * s, f"{b:.4f} 离真值 {t} 超过三个标准误"


def test_二值logit跟statsmodels一致(binary) -> None:
    """**跟一个独立实现对**——自己跟自己对证明不了对。"""
    sm = pytest.importorskip("statsmodels.api")
    X, y, _ = binary
    fit = binary_logit(X, y)
    ref = sm.Logit(y, X).fit(disp=0)
    assert np.abs(fit.beta - ref.params).max() < 1e-8
    assert np.abs(fit.se - ref.bse).max() < 1e-8


def test_边际效应跟数值差分一致(binary) -> None:
    """AME 是 logit 的主结果（系数是对数几率，读者判断不了量级），所以它要对。"""
    from oss.core.discrete import _p

    X, y, _ = binary
    fit = binary_logit(X, y)
    eff, _se = ame(X, fit.beta, fit.vcov)
    h = 1e-5
    Xp = X.copy()
    Xp[:, 1] += h
    num = float((_p(Xp, fit.beta) - _p(X, fit.beta)).mean() / h)
    assert abs(eff[1] - num) < 1e-6, f"解析 {eff[1]} vs 数值 {num}"


def test_边际效应跟statsmodels一致(binary) -> None:
    sm = pytest.importorskip("statsmodels.api")
    X, y, _ = binary
    fit = binary_logit(X, y)
    eff, se = ame(X, fit.beta, fit.vcov)
    ref = sm.Logit(y, X).fit(disp=0).get_margeff(at="overall")
    assert abs(eff[1] - ref.margeff[0]) < 1e-8
    assert abs(se[1] - ref.margeff_se[0]) < 1e-8


def test_权重进似然不是乘根号w() -> None:
    """**抽样权重在最大似然里直接进似然**，跟 WLS 把数据乘 √w 不是一回事。
    把每一行复制 w 次，加权拟合该跟复制后的未加权拟合一致。"""
    rng = np.random.default_rng(3)
    n = 600
    X = np.column_stack([np.ones(n), rng.normal(size=n)])
    y = (rng.random(n) < 1 / (1 + np.exp(-(X @ [0.2, 0.9])))).astype(float)
    w = rng.integers(1, 4, n).astype(float)
    a = binary_logit(X, y, w).beta
    b = binary_logit(np.repeat(X, w.astype(int), axis=0),
                     np.repeat(y, w.astype(int))).beta
    assert np.abs(a - b).max() < 1e-7, f"加权 {a} vs 复制 {b}"


def test_得分矩阵按行加起来就是梯度(binary) -> None:
    """夹心方差的「肉」按得分算。收敛处梯度为零，所以得分的列和该是零。"""
    X, y, _ = binary
    fit = binary_logit(X, y)
    g = score_matrix(X, y, fit.beta).sum(0)
    assert np.abs(g).max() < 1e-6, f"收敛处梯度不为零：{g}"


def test_完全分离要报出来() -> None:
    """完全分离的列系数理论上无穷大，估出来是个大数——**看起来像个了不起的强效应**。
    不报出来，读者会把「这一列完美预测了结局」当成发现。"""
    rng = np.random.default_rng(5)
    n = 400
    x = rng.normal(size=n)
    y = (x > 0).astype(float)                    # x 完美预测 y
    from oss.core.discrete import Separated

    with pytest.raises(Separated) as e:
        binary_logit(np.column_stack([np.ones(n), x]), y)
    assert 1 in e.value.cols, f"没认出是哪一列：{e.value.cols}"
    assert "完全分离" in str(e.value) and "无穷大" in str(e.value)


def test_完全分离不报成不收敛() -> None:
    """**两者的处置完全不同**：分离要查那一列是不是混进了结局本身，
    不收敛要查共线。报成同一种，人会照着错的方向查一整天。"""
    from oss.core.discrete import NotConverged, Separated

    assert issubclass(Separated, RuntimeError)
    assert not issubclass(Separated, NotConverged)


# ───────────────────────────────────────────── 有序 logit


@pytest.fixture(scope="module")
def ordered():
    rng = np.random.default_rng(7)
    n = 20000
    x1, x2 = rng.normal(size=n), rng.binomial(1, 0.4, n)
    z = 0.7 * x1 - 0.5 * x2 + rng.logistic(size=n)
    y = np.digitize(z, [-1.0, 0.0, 1.0]).astype(float)
    return np.column_stack([x1, x2]), y, np.array([0.7, -0.5])


def test_有序logit估回真值与切点(ordered) -> None:
    X, y, true = ordered
    fit = ordered_logit(X, y)
    for b, s, t in zip(fit.beta, fit.se, true):
        assert abs(b - t) < 3 * s, f"{b:.4f} 离真值 {t} 超过三个标准误"
    assert np.abs(fit.cuts - np.array([-1.0, 0.0, 1.0])).max() < 0.1, fit.cuts
    assert np.all(np.diff(fit.cuts) > 0), f"切点必须递增：{fit.cuts}"


def test_有序logit跟statsmodels一致(ordered) -> None:
    om = pytest.importorskip("statsmodels.miscmodels.ordinal_model")
    X, y, _ = ordered
    fit = ordered_logit(X, y)
    ref = om.OrderedModel(y, X, distr="logit").fit(method="bfgs", disp=0)
    assert np.abs(fit.beta - ref.params[:X.shape[1]]).max() < 1e-4


def test_两个取值的结局要拒(ordered) -> None:
    """两个取值用二值 logit；有序 logit 至少要三个有序取值。"""
    X, _y, _ = ordered
    with pytest.raises(ValueError, match="三个"):
        ordered_logit(X, np.zeros(len(X)) + (np.arange(len(X)) % 2))


def test_数值不溢出(ordered) -> None:
    """尾部观测的 |切点 − Xβ| 轻易到几十，直接写 1/(1+exp(−z)) 会溢出。
    **结果碰巧是对的（inf → 概率 0），但那是警告**——
    「跑出来有警告但结果碰巧对」正是最容易被放过去的毛病。"""
    X, y, _ = ordered
    with np.errstate(over="raise", invalid="raise"):
        ordered_logit(X, y)


# ───────────────────────────────────────────── 平行线假定（Brant）


def _panels(seed: int = 11, n: int = 12000):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    ok = np.digitize(0.8 * x + rng.logistic(size=n), [-1.0, 0.0, 1.0]).astype(float)
    u = rng.logistic(size=n)
    bad = np.where(0.1 * x + u < -1, 0,
                   np.where(1.5 * x + u < 0, 1,
                            np.where(-0.5 * x + u < 1, 2, 3))).astype(float)
    return x[:, None], ok, bad


def test_平行线成立时不响() -> None:
    """**这一条是这个文件里最要紧的。**

    第一版把各切点的二值 logit 当独立处理算协方差，在平行线**明明成立**的
    模拟数据上（n=12000）给出 p≈0——**一道会误报的门**。
    这个项目的立场是「会误报的门比没有门更糟」：人会学会绕过去。
    修法是把统计量算对（Brant 原式的协方差 + 各分项 logit 自己的截距），
    **不是把阈值调松**。
    """
    X, ok, _bad = _panels()
    stat, df, betas = parallel_lines(X, ok)
    assert stat > 0, f"卡方不可能为负：{stat}"
    assert _chi2_p(stat, df) > 0.01, f"平行线成立却响了：卡方 {stat:.2f}，系数 {betas}"


def test_平行线不成立时要响() -> None:
    X, _ok, bad = _panels()
    stat, df, _ = parallel_lines(X, bad)
    assert _chi2_p(stat, df) < 0.001, f"平行线不成立却不响：卡方 {stat:.2f}"


def test_两种数据的区分度要大() -> None:
    """成立与不成立之间差两个数量级以上——门才不会踩在边上。"""
    X, ok, bad = _panels()
    s_ok, _, _ = parallel_lines(X, ok)
    s_bad, _, _ = parallel_lines(X, bad)
    assert s_bad > 100 * s_ok, f"区分度不够：成立 {s_ok:.1f} vs 不成立 {s_bad:.1f}"


def test_各切点的系数一起交出去() -> None:
    """判词说「不成立」时，人要能看见是哪几段不一样。只给一个卡方值，没法处置。"""
    X, _ok, bad = _panels()
    _stat, _df, betas = parallel_lines(X, bad)
    assert len(betas) == 3, betas
    vals = [float(b[0]) for b in betas]
    assert max(vals) - min(vals) > 0.5, f"这份数据的各段系数本该差很远：{vals}"


def test_多个自变量时协方差仍然半正定() -> None:
    """**一元时碰巧是正定的，二元一跑就崩。**

    第二个坑：对角块从含截距的矩阵切出来（`[1:,1:]`），非对角块的中间那层
    却用了不含截距的 X——**两边不在同一个空间**。协方差矩阵出负特征值，
    检验报「算不了」，而单变量的测试**全都是过的**。

    所以这一条专门用两个自变量，而且拿平行线成立的数据：既要不误报，
    也要能算得出来（算不出来时旧代码返回的是「检验不了」，那也是一种静默失效）。
    """
    rng = np.random.default_rng(4)
    n = 6000
    x = rng.normal(size=n)
    edu = rng.integers(6, 20, n).astype(float)
    y = np.digitize(0.7 * x - 0.02 * edu + rng.logistic(size=n), [-1, 0, 1]).astype(float)
    stat, df, _ = parallel_lines(np.column_stack([x, edu]), y)
    assert stat > 0, f"算出负卡方：{stat}"
    assert df == 4, f"两个自变量、三个切点，自由度该是 (3−1)×2=4，得到 {df}"
    assert _chi2_p(stat, df) > 0.01, f"平行线成立却响了：χ²={stat:.2f}"


def test_多个自变量时不成立也要响() -> None:
    rng = np.random.default_rng(4)
    n = 6000
    x = rng.normal(size=n)
    edu = rng.integers(6, 20, n).astype(float)
    u = rng.logistic(size=n)
    y = np.where(0.1 * x + u < -1, 0,
                 np.where(1.6 * x + u < 0, 1,
                          np.where(-0.6 * x + u < 1, 2, 3))).astype(float)
    stat, df, _ = parallel_lines(np.column_stack([x, edu]), y)
    assert _chi2_p(stat, df) < 0.001, f"不成立却不响：χ²={stat:.2f}"
