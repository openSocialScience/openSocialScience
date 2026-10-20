"""计量执行的测试。

**上一阶段的教训**：手抄的数值算法错了不报错，只安静地给出错的数——
功效被高估三倍，是跨模型审稿按参数重算才发现的。

所以这里每个公式都配一个**能独立复核**的测试：
要么有解析解，要么有等价的另一种算法可以对照。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oss.core.analysis import (
    PlanRejected,
    _cluster_robust_vcov,
    _demean,
    run_ols_fe,
    validate_plan,
)
from oss.core.contracts import AnalysisPlan
from oss.core.profiler import profile_file

FIXTURES = Path(__file__).parent / "fixtures"


def plan(**kw) -> AnalysisPlan:
    base = dict(
        plan_id="p1", idea_id="i1", estimator="ols_fe",
        outcome="income", treatment="treat",
        controls=["edu_years"], fixed_effects=["unit_id", "year"],
        cluster_level="region",
    )
    return AnalysisPlan(**{**base, **kw})


@pytest.fixture(scope="module")
def econ():
    return profile_file(FIXTURES / "tiny.csv")


@pytest.fixture(scope="module")
def frame():
    return pd.read_csv(FIXTURES / "tiny.csv")


# ══════════════════════════════════════════ 数值正确性：能被独立复核


def test_单维固定效应等价于加虚拟变量() -> None:
    """去均值吸收和加虚拟变量必须给出**同一个系数**。

    这是 Frisch-Waugh-Lovell 定理。两种算法对不上就说明去均值写错了——
    而这类错误不报错，只会让系数悄悄偏掉。
    """
    rng = np.random.default_rng(0)
    n, g = 200, 10
    grp = np.repeat(np.arange(g), n // g)
    x = rng.normal(size=n)
    y = 2.5 * x + np.repeat(rng.normal(size=g), n // g) + rng.normal(scale=0.3, size=n)

    # 去均值
    yd = _demean(y.reshape(-1, 1), [grp]).ravel()
    xd = _demean(x.reshape(-1, 1), [grp]).ravel()
    beta_demean = float(np.linalg.lstsq(xd.reshape(-1, 1), yd, rcond=None)[0][0])

    # 虚拟变量
    D = pd.get_dummies(pd.Series(grp), drop_first=True).to_numpy(dtype=float)
    X = np.column_stack([np.ones(n), x, D])
    beta_dummy = float(np.linalg.lstsq(X, y, rcond=None)[0][1])

    assert abs(beta_demean - beta_dummy) < 1e-8, "去均值与虚拟变量给出了不同的系数"
    assert abs(beta_demean - 2.5) < 0.15, "系数偏离真值太远"


def test_双维固定效应要迭代到收敛() -> None:
    """两维以上，一次去均值不够——必须交替投影到收敛。

    只做一遍的话残留的组间差异会污染系数，而且**看不出来**。
    """
    rng = np.random.default_rng(1)
    unit = np.repeat(np.arange(20), 5)
    time = np.tile(np.arange(5), 20)
    n = len(unit)
    x = rng.normal(size=n)
    y = (1.8 * x + np.repeat(rng.normal(size=20), 5)
         + np.tile(rng.normal(size=5), 20) + rng.normal(scale=0.2, size=n))

    xd = _demean(x.reshape(-1, 1), [unit, time]).ravel()
    yd = _demean(y.reshape(-1, 1), [unit, time]).ravel()
    beta = float(np.linalg.lstsq(xd.reshape(-1, 1), yd, rcond=None)[0][0])

    D = np.column_stack([
        pd.get_dummies(pd.Series(unit), drop_first=True).to_numpy(dtype=float),
        pd.get_dummies(pd.Series(time), drop_first=True).to_numpy(dtype=float),
    ])
    beta_dummy = float(np.linalg.lstsq(np.column_stack([np.ones(n), x, D]), y, rcond=None)[0][1])
    assert abs(beta - beta_dummy) < 1e-6


def test_去均值之后组内均值为零() -> None:
    grp = np.repeat(np.arange(5), 20)
    x = np.random.default_rng(2).normal(size=100)
    xd = _demean(x.reshape(-1, 1), [grp]).ravel()
    for g in range(5):
        assert abs(xd[grp == g].mean()) < 1e-12


def test_聚类稳健方差可以手算复核() -> None:
    """按定义式手算一遍，跟函数对照。"""
    rng = np.random.default_rng(3)
    n, k = 60, 2
    X = rng.normal(size=(n, k))
    u = rng.normal(size=n)
    clusters = np.repeat(np.arange(6), 10)
    xtx_inv = np.linalg.inv(X.T @ X)

    got = _cluster_robust_vcov(X, u, clusters, xtx_inv)

    meat = np.zeros((k, k))
    for g in range(6):
        m = clusters == g
        s = X[m].T @ u[m]
        meat += np.outer(s, s)
    corr = (6 / 5) * ((n - 1) / (n - k))
    assert np.allclose(got, corr * xtx_inv @ meat @ xtx_inv)


def test_聚类会让标准误变大(frame) -> None:
    """簇内相关是正的时候，不聚类的标准误偏小——**p 值虚假显著**。"""
    clustered = run_ols_fe(frame, plan(cluster_level="region"))
    plain = run_ols_fe(frame, plan(cluster_level=""))
    assert clustered.estimates["main"].coef == pytest.approx(plain.estimates["main"].coef)
    assert clustered.estimates["main"].se != plain.estimates["main"].se


def test_系数与pandas的ols对得上(frame) -> None:
    """跟一个完全独立的实现对照。"""
    df = frame.dropna(subset=["income", "treat", "edu_years"])
    got = run_ols_fe(df, plan(fixed_effects=[], cluster_level="", controls=["edu_years"]))
    X = np.column_stack([np.ones(len(df)), df["treat"], df["edu_years"]])
    expected = np.linalg.lstsq(X, df["income"].to_numpy(float), rcond=None)[0]
    assert got.estimates["main"].coef == pytest.approx(expected[1], abs=1e-6)


# ══════════════════════════════════════════════ 校验：拒绝 vs 警告


def test_列名写错就拒绝执行并给出最接近的(econ) -> None:
    """**这是 PLAN.md §8 ⑤ 记下的那条验收。**

    列名写错不会报错，回归照样跑，只是量错了东西。
    """
    problems, _ = validate_plan(plan(outcome="incom"), econ)
    assert problems
    assert "最接近的是 income" in problems[0]


def test_组内变异为零的处理变量被拒绝(econ) -> None:
    """加了固定效应之后它是常数，系数估不出来。"""
    problems, _ = validate_plan(plan(treatment="const_col"), econ)
    assert any("组内变异为 0" in p for p in problems)


def test_不支持的估计量被拒绝(econ) -> None:
    problems, _ = validate_plan(plan(estimator="did_staggered"), econ)
    assert any("还不支持" in p for p in problems)


def test_没填结果或处理变量被拒绝(econ) -> None:
    assert validate_plan(plan(outcome=""), econ)[0]
    assert validate_plan(plan(treatment=""), econ)[0]


def test_不聚类是警告不是拒绝(econ) -> None:
    """**拒绝和警告必须分开。**截面数据本来就可能没有聚类结构，
    硬拦住等于逼使用者编一个出来。"""
    problems, warnings = validate_plan(plan(cluster_level=""), econ)
    assert not problems, "不聚类不该拦住执行"
    assert any("虚假显著" in w for w in warnings)


def test_聚类数太少要警告(econ) -> None:
    """聚类数少于约 30 时，聚类稳健标准误本身是有偏的——
    小样本校正只能缓解，不能消除。"""
    _, warnings = validate_plan(plan(cluster_level="region"), econ)
    assert any("聚类单位" in w for w in warnings)


def test_合法计划无拒绝理由(econ) -> None:
    problems, _ = validate_plan(plan(), econ)
    assert not problems


# ══════════════════════════════════════════════════════ 产出


def test_结果是结构化的可被占位符引用(frame) -> None:
    """正文里的 `{{result:main.coef}}` 靠它解析——
    自由文本的结果在写作阶段引用不了。"""
    res = run_ols_fe(frame, plan())
    assert "main" in res.estimates
    e = res.estimates["main"]
    assert e.term == "treat"
    assert isinstance(e.coef, float) and isinstance(e.se, float) and 0 <= e.p <= 1


def test_诊断里说清标准误是怎么算的(frame) -> None:
    res = run_ols_fe(frame, plan())
    assert "聚类到 region" in res.diagnostics["标准误"]
    assert res.diagnostics["吸收的自由度"] != "0"


def test_缺失丢弃要报出来(frame) -> None:
    df = frame.copy()
    df.loc[:9, "income"] = np.nan
    res = run_ols_fe(df, plan())
    assert "因缺失丢弃" in res.diagnostics
    assert res.n == len(frame) - 10


def test_样本筛选被记录(frame) -> None:
    res = run_ols_fe(frame, plan(sample_filter="age >= 30"))
    assert res.diagnostics["样本筛选"] == "age >= 30"
    assert res.n < len(frame)


def test_样本筛选把处理变量吃光时点名(frame) -> None:
    """筛掉 2016 年之后，treat 在每个个体内变成常数——被个体固定效应吸收。

    **拒绝是对的，但「不满秩」这个说法没法行动。**
    使用者需要知道的是「你的处理变量在这个筛选之后不再变化」。
    """
    with pytest.raises(PlanRejected) as exc:
        run_ols_fe(frame, plan(sample_filter="year >= 2018"))
    msg = str(exc.value)
    assert "treat" in msg, "要点名是哪个变量"
    assert "样本筛选" in msg, "要说清是筛选导致的"


def test_完全共线时点名两个变量(frame) -> None:
    df = frame.copy()
    df["treat_copy"] = df["treat"]
    with pytest.raises(PlanRejected) as exc:
        run_ols_fe(df, plan(controls=["treat_copy"], fixed_effects=[], cluster_level=""))
    assert "完全共线" in str(exc.value)


# ══════════════════════════════════════════════ 社会学场景


def test_重复截面也能跑() -> None:
    """社会学常用重复截面。没有个体固定效应，但有省份和年份。"""
    df = pd.read_csv(FIXTURES / "cgss_like.csv")
    res = run_ols_fe(df, AnalysisPlan(
        plan_id="p", idea_id="i", estimator="ols_fe",
        outcome="occupation_isei", treatment="own_edu_years",
        controls=["hukou_urban"], fixed_effects=["province", "survey_year"],
        cluster_level="province",
    ))
    assert res.estimates["main"].coef > 0, "教育年限对职业地位该是正向的（数据是这么造的）"
    assert res.n == len(df)


def test_稳健性超过十二项才警告(econ) -> None:
    """每一项都要跑、都要写进论文，所以有上限；**但上限原来定在 5，是在惩罚好做法。**

    三次互不相通的自由跑各自锁了 8、9、10 项，全被判不合格；作为参照的那篇
    已发表论文锁了 13 项。而那 9 项正是价值所在——其中一次靠自己的稳健性套件
    发现主结果加上教育控制就不显著、只留受雇者符号还翻，于是放弃了那个发现。
    """
    from oss.core.contracts import RobustnessSpec

    many = [RobustnessSpec(label=f"R{i}", controls=[], executable=True) for i in range(13)]
    _, warnings = validate_plan(plan(robustness=many), econ)
    assert any("12 项" in w for w in warnings), warnings
    _, warnings = validate_plan(plan(robustness=many[:10]), econ)
    assert not any("12 项" in w for w in warnings), "8–10 项是真跑里的常态"
