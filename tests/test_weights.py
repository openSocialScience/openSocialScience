"""抽样权重。**调查数据不加权的估计通常是有偏的。**

CGSS / CFPS / GSS 都按复杂抽样设计采集，各人入样概率不同。不加权算出来的是
「样本里的中国」而不是「中国」——而这个错误在系数上看不出来，没有任何一个
统计量会告诉你算错了对象。

这一批来自要复现一篇 11 轮 CGSS 的论文：那篇每一个数都是加权的，而工具箱
当时连放权重的字段都没有。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oss.core.analysis import PlanRejected, _effective_n, run_ols_fe
from oss.core.contracts import AnalysisPlan


def _plan(**kw) -> AnalysisPlan:
    base = dict(outcome="y", treatment="x", estimator="ols_fe")
    return AnalysisPlan(**{**base, **kw})


def test_加权结果跟statsmodels的WLS一致() -> None:
    """判据不是「跑得动」，是**跟一个独立实现算出同一个数**。"""
    sm = pytest.importorskip("statsmodels.api")
    rng = np.random.default_rng(3)
    n = 400
    df = pd.DataFrame({
        "x": rng.normal(size=n), "c": rng.normal(size=n),
        "w": rng.uniform(0.2, 5.0, n),
    })
    df["y"] = 1.5 + 2.0 * df.x - 0.7 * df.c + rng.normal(scale=0.5, size=n)

    got = run_ols_fe(df, _plan(controls=["c"], weights="w"))
    want = sm.WLS(df.y, sm.add_constant(df[["x", "c"]]), weights=df.w).fit()

    assert got.estimates["main"].coef == pytest.approx(want.params["x"], abs=1e-6)
    assert got.estimates["c"].coef == pytest.approx(want.params["c"], abs=1e-6)


def test_固定效应下减的是加权组均值() -> None:
    """**这是最容易错、最不会报错的一处。**

    带权重时组内去均值减的必须是 `Σwy/Σw`，不是算术均值。减错了固定效应
    照样被吸收掉、系数照样算得出来——只是算的是另一个东西。

    构造一份组内权重极不均的数据，两种减法的答案差得开，才检验得出来。
    """
    sm = pytest.importorskip("statsmodels.api")
    rng = np.random.default_rng(4)
    n = 600
    g = rng.integers(0, 6, n)
    df = pd.DataFrame({"x": rng.normal(size=n), "g": g,
                       "w": np.where(rng.random(n) < 0.3, 40.0, 0.5)})
    df["y"] = 3.0 * df.x + g * 2.0 + rng.normal(scale=0.4, size=n)

    got = run_ols_fe(df, _plan(fixed_effects=["g"], weights="w"))
    dummies = pd.get_dummies(df.g, prefix="g", drop_first=True).astype(float)
    want = sm.WLS(df.y, sm.add_constant(pd.concat([df[["x"]], dummies], axis=1)),
                  weights=df.w).fit()
    assert got.estimates["main"].coef == pytest.approx(want.params["x"], abs=1e-6)


def test_加权的聚类标准误也对得上() -> None:
    sm = pytest.importorskip("statsmodels.api")
    rng = np.random.default_rng(5)
    n = 900
    cl = rng.integers(0, 30, n)
    df = pd.DataFrame({"x": rng.normal(size=n), "cl": cl, "w": rng.uniform(0.5, 3, n)})
    df["y"] = 1.0 * df.x + cl * 0.1 + rng.normal(scale=0.6, size=n)

    got = run_ols_fe(df, _plan(cluster_level="cl", weights="w"))
    want = sm.WLS(df.y, sm.add_constant(df[["x"]]), weights=df.w).fit(
        cov_type="cluster", cov_kwds={"groups": df.cl, "use_correction": True})
    assert got.estimates["main"].se == pytest.approx(want.bse["x"], rel=0.02)


def test_不加权时结果跟从前一模一样() -> None:
    """加权是新增的一条路，**不许顺手改掉原来那条**。"""
    rng = np.random.default_rng(6)
    df = pd.DataFrame({"x": rng.normal(size=300), "w": rng.uniform(1, 2, 300)})
    df["y"] = 2 * df.x + rng.normal(scale=0.3, size=300)
    assert run_ols_fe(df, _plan()).estimates["main"].coef == pytest.approx(
        run_ols_fe(df, _plan(weights="")).estimates["main"].coef)


def test_权重有非正数就拒绝跑() -> None:
    """0 和负数多半是缺失码没处理干净。**按 1 兜底等于凭空发权重。**"""
    rng = np.random.default_rng(7)
    df = pd.DataFrame({"x": rng.normal(size=200), "w": rng.uniform(1, 2, 200)})
    df["y"] = df.x + rng.normal(size=200)
    df.loc[:4, "w"] = 0.0
    with pytest.raises(PlanRejected, match="不是正数"):
        run_ols_fe(df, _plan(weights="w"))


def test_权重缺失的行整行丢掉而不是当成1() -> None:
    rng = np.random.default_rng(8)
    df = pd.DataFrame({"x": rng.normal(size=200), "w": rng.uniform(1, 2, 200)})
    df["y"] = df.x + rng.normal(size=200)
    df.loc[:9, "w"] = np.nan
    r = run_ols_fe(df, _plan(weights="w"))
    assert "10 行" in r.diagnostics["因缺失丢弃"]


def test_有效样本量与设计效应报出来() -> None:
    """3 万人的加权样本可能只值 2 万人的信息量。**置信区间照 3 万算就是假的精确。**"""
    assert _effective_n(np.ones(100)) == pytest.approx(100.0)
    assert _effective_n(np.array([1.0] * 50 + [9.0] * 50)) == pytest.approx(
        500.0 ** 2 / (50 + 50 * 81))

    rng = np.random.default_rng(9)
    df = pd.DataFrame({"x": rng.normal(size=400), "w": rng.uniform(0.2, 8, 400)})
    df["y"] = df.x + rng.normal(size=400)
    note = run_ols_fe(df, _plan(weights="w")).diagnostics["抽样权重"]
    assert "有效样本量" in note and "设计效应" in note


def test_没加权也要留一句话() -> None:
    """**只在加权时才出现的诊断项，看不见它的人不会想到去找它。**"""
    rng = np.random.default_rng(10)
    df = pd.DataFrame({"x": rng.normal(size=200)})
    df["y"] = df.x + rng.normal(size=200)
    assert "未加权" in run_ols_fe(df, _plan()).diagnostics["抽样权重"]


def test_稳健性可以覆盖成不加权() -> None:
    """「不加权复核」是调查数据最常见的一项稳健性。

    空串是「这一项不加权」，`None` 才是「沿用主计划」——**两者不能混**。
    """
    from oss.core.analysis import _patched
    from oss.core.contracts import RobustnessSpec

    main = _plan(weights="w", robustness=[])
    assert _patched(main, RobustnessSpec(label="不加权", weights="")).weights == ""
    assert _patched(main, RobustnessSpec(label="换聚类", cluster_level="prov")).weights == "w"


def test_检出权重列却没用会开处方(tmp_path) -> None:
    """**不加权是正当选择，沉默不是。**"""
    from oss.core.route import _rules

    items = _rules(_plan(weights=""), None, None, [], weight_column="wtssall")
    assert [i.id for i in items] == ["weights_unused"]
    assert items[0].priority == "should"
    assert "wtssall" in items[0].evidence

    assert _rules(_plan(weights="wtssall"), None, None, [], "wtssall") == []
    assert _rules(_plan(weights=""), None, None, [], "") == []


# ═══════════ 聚类标准误：吸收掉的自由度要算进小样本校正

def test_聚类标准误把吸收的自由度算进去() -> None:
    """**同一个函数里两套账。**

    `run_ols_fe` 的 HC1 那条路一直用 `n - k - k_absorbed` 做小样本校正，
    聚类那条路却用 `n - k`——去均值把固定效应的参数从矩阵里拿掉了，
    但它们仍然是估出来的参数。不算进去，标准误偏小，**而且是往显著里偏**。

    两条路不会同时跑，所以谁也不会发现。发现于给 CGSS 做加权回归时
    跟 statsmodels 对不上最后那位小数：省份聚类只差 0.1%（31 个省、一万多行），
    项目自己的夹具上差 3.9%，**个体固定效应那种设定能差出十几个百分点**——
    而那正是社科最常见的设定。
    """
    sm = pytest.importorskip("statsmodels.api")
    rng = np.random.default_rng(21)
    n, n_unit = 900, 60
    unit = rng.integers(0, n_unit, n)
    cl = unit % 20
    df = pd.DataFrame({"x": rng.normal(size=n), "unit": unit, "cl": cl})
    df["y"] = 2.0 * df.x + unit * 0.3 + rng.normal(scale=0.5, size=n)

    got = run_ols_fe(df, _plan(fixed_effects=["unit"], cluster_level="cl"))
    dummies = pd.get_dummies(df.unit, prefix="u", drop_first=True).astype(float)
    want = sm.OLS(df.y, sm.add_constant(pd.concat([df[["x"]], dummies], axis=1))).fit(
        cov_type="cluster", cov_kwds={"groups": df.cl, "use_correction": True})

    # 系数在 Estimate 里按六位有效数字存，容差不能比它还紧
    assert got.estimates["main"].coef == pytest.approx(want.params["x"], abs=1e-6)
    assert got.estimates["main"].se == pytest.approx(want.bse["x"], rel=1e-4), (
        "吸收掉的自由度没算进小样本校正，标准误就偏小")


# ═══════════ 加权：六种估计量都真加权（2026-09-15 调查层：iv、rdd、did_cs 补上）；did_cs 要单位层面的权重

FIXTURES = Path(__file__).parent / "fixtures"


def _did_plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", estimator="did", outcome="y",
                unit="unit", time="year", treat_time="treat_time", cluster_level="unit")
    return AnalysisPlan(**{**base, **kw})


def _weighted_panel() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "did_clean.csv")
    rng = np.random.default_rng(12)
    return df.assign(w=rng.uniform(0.5, 3.0, len(df)))


def _fe_dummies(df: pd.DataFrame) -> pd.DataFrame:
    return pd.concat([pd.get_dummies(df.unit, prefix="u", drop_first=True).astype(float),
                      pd.get_dummies(df.year, prefix="t", drop_first=True).astype(float)], axis=1)


def test_DID加权跟statsmodels带双向固定效应的WLS一致() -> None:
    """**2026-09-13 查出来的真漏洞，用户定补上真加权。**

    原先 `run_did` 转交 `run_ols_fe` 时新建的计划不带 `weights`，计划里写了权重也照样按不加权跑。
    判据不是「跑得动」，是**跟一个独立实现算出同一个数**：处理指示 + 单位与年份哑变量的 WLS。
    """
    sm = pytest.importorskip("statsmodels.api")
    from oss.core.analysis import run_plan

    df = _weighted_panel()
    res = run_plan(df, _did_plan(weights="w"))
    treated = (df.treat_time.notna() & (df.year >= df.treat_time)).astype(float).rename("treated")
    want = sm.WLS(df.y, sm.add_constant(pd.concat([treated, _fe_dummies(df)], axis=1)), weights=df.w).fit()
    assert res.estimates["main"].coef == pytest.approx(want.params["treated"], rel=1e-5)
    assert "按 `w` 加权" in res.diagnostics["抽样权重"]
    unweighted = run_plan(df, _did_plan()).estimates["main"].coef
    assert res.estimates["main"].coef != pytest.approx(unweighted, rel=1e-6), "权重没起作用"


def test_事件研究加权跟statsmodels带同样哑变量的WLS一致() -> None:
    """逐期系数要吃权重——逐期与合并那两次拟合当初都没带。"""
    sm = pytest.importorskip("statsmodels.api")
    from oss.core.analysis import run_plan

    df = _weighted_panel()
    leads, lags = 3, 3
    res = run_plan(df, _did_plan(estimator="event_study", leads=leads, lags=lags, weights="w"))
    k = df.year - df.treat_time
    ks = [j for j in range(-leads, lags + 1) if j != -1]
    X = pd.DataFrame({f"k{j}": (k == j).astype(float) for j in ks})
    X["far"] = (k.notna() & ~k.isin(ks) & (k != -1)).astype(float)
    want = sm.WLS(df.y, sm.add_constant(pd.concat([X, _fe_dummies(df)], axis=1)), weights=df.w).fit().params
    got = {p.k: p.coef for p in res.event_study}
    for j in (-3, 0, 2):
        assert got[j] == pytest.approx(want[f"k{j}"], rel=1e-4), j
    assert "按 `w` 加权" in res.diagnostics["抽样权重"]


@pytest.mark.parametrize("est", ["ols_fe", "did", "did_cs", "event_study", "iv", "rdd"])
def test_支持加权的估计量与不加权复核照常放行(est: str) -> None:
    """六种估计量都真加权了（2026-09-15 调查层）。**加权的主规格与「不加权复核」这两条路都不许被误伤。**

    原先拦 did_cs、iv、rdd 的三条测试（拿到权重就拒跑、计划校验就拦、稳健性单独加权也拦）随拒跑一起删了，
    被 `tests/test_survey_layer.py` 里跟独立实现对数的正面测试取代。
    """
    from oss.core.analysis import validate_plan
    from oss.core.contracts import RobustnessSpec
    from oss.core.profiler import profile_file

    prof = profile_file(FIXTURES / "did_clean.csv")
    plan = _did_plan(estimator=est, treatment="post", weights="w",
                     robustness=[RobustnessSpec(label="不加权", weights="", executable=True)])
    problems, _ = validate_plan(plan, prof)
    assert not any("抽样权重" in p for p in problems), problems


def _fe_plan_weighted(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="post",
                controls=["x"], fixed_effects=["unit", "year"], cluster_level="unit",
                unit="unit", time="year", treat_time="treat_time", leads=3, lags=3, weights="w")
    return AnalysisPlan(**{**base, **kw})


def test_单批时加权主规格附带的逐年展开也加权() -> None:
    """不分批时伴随的逐年展开走双向固定效应——事件研究现在吃权重，伴随的这一份要跟主规格同一口径。"""
    from oss.core.analysis import run_plan

    df = _weighted_panel()
    one = df[df.treat_time.isna() | (df.treat_time == 2010)].reset_index(drop=True)
    res = run_plan(one, _fe_plan_weighted())
    assert "未加权" not in res.diagnostics["逐年展开"], res.diagnostics["逐年展开"]
    direct = run_plan(one, _did_plan(estimator="event_study", controls=["x"], leads=3, lags=3, weights="w"))
    assert [(p.k, p.coef) for p in res.event_study] == [(p.k, p.coef) for p in direct.event_study]


def test_分批时伴随的逐年展开也加权() -> None:
    """分批实施时伴随的逐年展开换成 Callaway–Sant'Anna——它现在吃单位层面的权重，跟主规格同一口径。

    **口径不同的两个量放在一起，至少要写明**（分箱图与主设定反号那次，就是口径不同又没写明，
    初稿据此写了「线性假定不成立」）。现在口径相同，不再写「未加权」。
    did_cs 要权重在单位内不变，所以先把逐行的权重改成单位层面的。
    """
    from oss.core import staggered
    from oss.core.analysis import run_plan

    if not staggered.available():
        pytest.skip("没装 StatsPAI：伴随的逐年展开退回双向固定效应，不走 Callaway–Sant'Anna")
    df = _weighted_panel()
    df["w"] = df.groupby("unit")["w"].transform("first")
    res = run_plan(df, _fe_plan_weighted())
    note = res.diagnostics["逐年展开"]
    assert "未加权" not in note and "Callaway" in note and "退回 TWFE" not in note, note
    direct = run_plan(df, _did_plan(estimator="did_cs", controls=["x"], leads=3, lags=3, weights="w"))
    assert [(p.k, p.coef) for p in res.event_study] == [(p.k, p.coef) for p in direct.event_study]


def test_iv检出权重列没用也叫你把权重填上() -> None:
    """六种估计量都真加权了：处方统一成「填上权重、在 plan.survey 里声明抽样设计」那一条路。"""
    from oss.core.route import _rules

    items = _rules(_plan(estimator="iv", weights=""), None, None, [], weight_column="wtssall")
    assert [i.id for i in items] == ["weights_unused"]
    assert "填成 `wtssall`" in items[0].advice and "plan.survey" in items[0].advice
    assert "不支持抽样权重" not in items[0].advice


def test_DID检出权重列却没用照常叫你加权() -> None:
    """DID 现在吃权重——处方回到「把权重填上重跑」那条路。"""
    from oss.core.route import _rules

    items = _rules(_plan(estimator="did", weights=""), None, None, [], weight_column="wtssall")
    assert [i.id for i in items] == ["weights_unused"]
    assert "填成" in items[0].advice
