"""因果骨架的机器诊断（设计稿第四节）。"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oss.core import causal_checks as cc
from oss.core import causal_stats
from oss.core.analysis import run_plan
from oss.core.contracts import AnalysisPlan, PretrendCov

FIX = Path(__file__).parent / "fixtures"


def _es_plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", estimator="event_study", outcome="y", unit="unit",
                time="year", treat_time="treat_time", cluster_level="unit", leads=4, lags=4)
    return AnalysisPlan(**{**base, **kw})


def test_回归挂着拟合信息且跟估计一致() -> None:
    res = run_plan(pd.read_csv(FIX / "did_clean.csv"), AnalysisPlan(
        plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="x",
        fixed_effects=["unit", "year"], cluster_level="unit"))
    fit = res._fit
    assert fit is not None and "_const" not in fit.terms
    i = fit.terms.index("x")
    assert fit.beta[i] == pytest.approx(res.estimates["main"].coef, rel=1e-5)
    assert np.sqrt(fit.vcov[i][i]) == pytest.approx(res.estimates["main"].se, rel=1e-5)
    assert fit.dof > 0 and 0.0 <= fit.r2 <= 1.0


def test_事件研究存下处理前协方差() -> None:
    res = run_plan(pd.read_csv(FIX / "did_clean.csv"), _es_plan())
    pre = sorted(p.k for p in res.event_study if p.k < 0)
    assert res.pretrend_cov is not None and res.pretrend_cov.ks == pre
    se = {p.k: p.se for p in res.event_study}
    assert np.sqrt(np.diag(np.array(res.pretrend_cov.vcov))) == pytest.approx([se[k] for k in pre], rel=1e-4)


def test_干净夹具联合检验不拒绝() -> None:
    d = run_plan(pd.read_csv(FIX / "did_clean.csv"), _es_plan()).diagnostics["平行趋势"]
    assert "联合检验" in d and "平行趋势未被推翻" in d and "不成立" not in d, d


def test_处理前分化的夹具联合检验拒绝() -> None:
    d = run_plan(pd.read_csv(FIX / "did_pretrend.csv"), _es_plan()).diagnostics["平行趋势"]
    assert "**不成立**" in d and "联合检验" in d, d


def test_联合检验跟手算一致() -> None:
    res = run_plan(pd.read_csv(FIX / "did_clean.csv"), _es_plan())
    cov = res.pretrend_cov
    b = np.array([next(p.coef for p in res.event_study if p.k == k) for k in cov.ks])
    W = float(b @ np.linalg.solve(np.array(cov.vcov), b))
    m, G = len(cov.ks), cov.n_clusters
    p = causal_stats.f_sf(W * (G - m) / (m * (G - 1)), m, G - m)
    assert f"p = {p:.3f}" in res.diagnostics["平行趋势"]


def test_协方差带上聚类数() -> None:
    """小样本校正的分母自由度是**聚类数**，不是观测数——不带上就校正不了。"""
    df = pd.read_csv(FIX / "did_clean.csv")
    res = run_plan(df, _es_plan())
    assert res.pretrend_cov.n_clusters == df["unit"].nunique()


def test_联合检验按聚类数做小样本校正() -> None:
    """**矩条件多、聚类少时卡方会大量误拒。**同一个 Wald 要按 F(m, G−m) 判，不是 χ²(m)。

    分母自由度未知（老结果文件）时退回卡方——退回不等于对，只是没有更好的可用。
    """
    from oss.core.contracts import EventStudyPoint

    pts = [EventStudyPoint(k=-3, coef=0.4, se=0.2, n=10), EventStudyPoint(k=-2, coef=0.3, se=0.22, n=10)]
    cov = PretrendCov(ks=[-3, -2], vcov=[[0.04, 0.01], [0.01, 0.05]], n_clusters=30)
    W, p, m = cc.joint_pretest(pts, cov)
    assert m == 2
    assert p == pytest.approx(causal_stats.f_sf(W * (30 - 2) / (2 * (30 - 1)), 2, 28), rel=1e-9)
    assert p > causal_stats.chi2_sf(W, 2), "校正之后只会更保守"
    bare = PretrendCov(ks=cov.ks, vcov=cov.vcov)
    assert cc.joint_pretest(pts, bare)[1] == pytest.approx(causal_stats.chi2_sf(W, 2))


def test_线性违背让处理后平均偏误等于估计效应() -> None:
    d = cc.linear_violation([-4, -3, -2], theta=3.0, lags=4)
    gamma = 3.0 / ((4 + 2) / 2)
    assert list(d) == pytest.approx([-3 * gamma, -2 * gamma, -1 * gamma])
    assert np.mean([gamma * (k + 1) for k in range(0, 5)]) == pytest.approx(3.0)


def test_预检功效跟scipy一致() -> None:
    stats = pytest.importorskip("scipy.stats")
    cov = PretrendCov(ks=[-3, -2], vcov=[[0.04, 0.01], [0.01, 0.05]])
    delta = np.array([-0.4, -0.2])
    lam = float(delta @ np.linalg.solve(np.array(cov.vcov), delta))
    want = stats.ncx2.sf(stats.chi2.ppf(0.95, 2), 2, lam)
    assert cc.pretrend_power(cov, delta) == pytest.approx(want, rel=1e-6)


def test_预检功效用的是检验自己那把尺子() -> None:
    """**功效的门槛必须是检验真正用的那个临界值。**

    联合检验按聚类数做了小样本校正，临界值比 χ² 的高；功效还拿 χ² 的算，报出来的就比
    实际能查出来的多——那是一句让人放心的假话。
    """
    stats = pytest.importorskip("scipy.stats")
    m, G = 7, 60
    rng = np.random.default_rng(0)
    A = rng.normal(size=(m, m))
    V = (A @ A.T) / 400 + np.eye(m) * 0.01
    cov = PretrendCov(ks=list(range(-m - 1, -1)), vcov=V.tolist(), n_clusters=G)
    delta = np.full(m, 0.12)
    lam = float(delta @ np.linalg.solve(V, delta))
    crit = stats.f.ppf(0.95, m, G - m) * m * (G - 1) / (G - m)
    assert cc.joint_pretest_crit(m, G) == pytest.approx(crit, rel=1e-6)
    assert cc.joint_pretest_crit(m, G) > causal_stats.chi2_ppf(0.95, m), "校正之后门槛只会更高"
    got = cc.pretrend_power(cov, delta)
    assert got == pytest.approx(stats.ncx2.sf(crit, m, lam), rel=1e-6)
    assert got < stats.ncx2.sf(stats.chi2.ppf(0.95, m), m, lam), "拿 χ² 的临界值算会把功效说高"
    # 聚类数未知（老结果文件）时照旧按 χ²
    assert cc.joint_pretest_crit(m, 0) == pytest.approx(causal_stats.chi2_ppf(0.95, m))


def test_breakdown近似的闭式() -> None:
    assert cc.breakdown_rm(2.0, 0.5, [0.1, -0.5, 0.2]) == pytest.approx((2.0 - 1.959964 * 0.5) / 0.5)
    assert cc.breakdown_rm(0.5, 0.5, [0.3]) == 0.0
    assert cc.breakdown_rm(1.0, 0.1, [0.0, 0.0]) is None


def test_事件研究报预检功效与breakdown() -> None:
    d = run_plan(pd.read_csv(FIX / "did_clean.csv"), _es_plan()).diagnostics
    assert 0.0 <= float(d["预检功效"]) <= 1.0 and "把握" in d["预检功效说明"]
    assert float(d["breakdown 值"]) >= 0.0 and "相对大小" in d["breakdown 说明"]


def test_没有协方差时预检功效写没算() -> None:
    out = cc.pretrend_texts([], None, theta=1.0, se=0.2, lags=4)
    assert out["预检功效"] == "没算" and "不等于" in out["预检功效说明"]


def test_分批路径也报breakdown与预检功效() -> None:
    pytest.importorskip("statspai")
    plan = _es_plan(estimator="did_cs")
    d = run_plan(pd.read_csv(FIX / "did_staggered.csv"), plan).diagnostics
    assert float(d["breakdown 值"]) >= 0.0
    assert 0.0 <= float(d["预检功效"]) <= 1.0, d["预检功效"]
    assert "预检功效说明" in d


def test_did计划分批时伴随的分批诊断也带上预检功效与breakdown() -> None:
    """`did` 主规格、`treat_time` 两批——走 `_attach_event_study` 的分批分支，
    copy list 曾漏掉这四条键，导致算出来的诊断到不了主结果。"""
    pytest.importorskip("statspai")
    plan = _es_plan(estimator="did")
    d = run_plan(pd.read_csv(FIX / "did_clean.csv"), plan).diagnostics
    assert "breakdown 值" in d


# ══════════════════════════════ Task 8：选择可观测——稳健值、Oster δ、E 值


def test_稳健值的闭式() -> None:
    f = 5 / math.sqrt(100)
    assert cc.robustness_value(5.0, 100) == pytest.approx(0.5 * (math.sqrt(f ** 4 + 4 * f ** 2) - f ** 2))
    assert cc.robustness_value(5.0, 100, alpha=0.05) < cc.robustness_value(5.0, 100)
    assert cc.robustness_value(0.5, 100, alpha=0.05) == 0.0


def test_E值的闭式() -> None:
    rr = math.exp(0.91 * 0.5)
    assert cc.evalue_ols(beta=0.5, se=0.01, sd=1.0)[0] == pytest.approx(rr + math.sqrt(rr * (rr - 1)))
    assert cc.evalue_ols(beta=0.02, se=0.05, sd=1.0)[1] == 1.0          # 区间跨 0：区间那一端的 E 值是 1


def _causal_ols(**kw) -> AnalysisPlan:
    from oss.core.contracts import Estimand
    base = dict(plan_id="p", idea_id="i", estimator="ols_fe", research_type="causal",
                outcome="income", treatment="treat", controls=["edu_years"],
                fixed_effects=["unit_id", "year"], cluster_level="region",
                estimand=Estimand(unit_quantity="u", target_population="t", assumptions=["a"], link="l"),
                control_roles={"edu_years": "confounder"})
    return AnalysisPlan(**{**base, **kw})


def _cgss(**kw) -> AnalysisPlan:
    """cgss_like 上的因果 ols_fe：主结果显著（p≈0），加进控制变量后系数离 0 更远（Oster δ 为负）。"""
    base = dict(outcome="occupation_isei", treatment="own_edu_years", controls=["father_edu_years", "hukou_urban"],
                control_roles={"father_edu_years": "confounder", "hukou_urban": "confounder"},
                fixed_effects=["province", "survey_year"], cluster_level="province")
    return _causal_ols(**{**base, **kw})


# ── 任务 13 校准后的改动（用户 2026-09-14 定：五个初值不改；不显著的主结果不谈「推翻」；Oster δ 为负要说方向）


def test_主结果不显著时breakdown写不适用() -> None:
    """区间本来含 0：breakdown 问的是「违背多大才推翻显著的结论」，不显著就没有可推翻的——写不适用，不写 0.00。"""
    from oss.core.contracts import EventStudyPoint
    pts = [EventStudyPoint.model_construct(k=k, coef=c) for k, c in ((-3, 0.2), (-2, -0.1))]
    out = cc.pretrend_texts(pts, None, theta=0.1, se=0.2, lags=4)
    assert out["breakdown 值"] == "不适用" and "不显著" in out["breakdown 说明"]
    sig = cc.pretrend_texts(pts, None, theta=1.0, se=0.2, lags=4)
    assert sig["breakdown 值"] == f"{(1.0 - cc._Z975 * 0.2) / 0.2:.2f}"


def test_主结果不显著时稳健值显著性写不适用() -> None:
    """tiny 的主结果 p≈0.49：「削到不显著要多强的混淆」按定义是 0，没有可削的——写不适用，也不判偏弱。"""
    d = run_plan(pd.read_csv(FIX / "tiny.csv"), _causal_ols()).diagnostics
    assert d["稳健值（显著性）"] == "不适用" and math.isfinite(float(d["稳健值"]))
    assert "偏弱" not in d["敏感性分析"] and "本来就不显著" in d["敏感性分析"]


def test_Oster为负时说明是反方向() -> None:
    """cgss_like：加进控制变量后系数从 1.26 升到 1.41、离 0 更远，δ* 为负——只印一个负数，读者会误读成「很弱」。"""
    d = run_plan(pd.read_csv(FIX / "cgss_like.csv"), _cgss()).diagnostics
    assert float(d["Oster δ"]) < 0 and "反方向" in d["敏感性分析"]


def test_因果ols报三样敏感性() -> None:
    d = run_plan(pd.read_csv(FIX / "cgss_like.csv"), _cgss()).diagnostics
    assert 0.0 <= float(d["稳健值"]) <= 1.0 and 0.0 <= float(d["稳健值（显著性）"]) <= 1.0
    assert d["Oster δ"] == "∞" or math.isfinite(float(d["Oster δ"]))
    assert float(d["E 值"]) >= 1.0 and "稳健值" in d["敏感性分析"]


def test_描述类不算敏感性() -> None:
    d = run_plan(pd.read_csv(FIX / "tiny.csv"), _causal_ols(research_type="descriptive")).diagnostics
    assert "稳健值" not in d


def test_稳健值跟StatsPAI对数() -> None:
    """交叉验证靠同方差经典 t：主回归报的是稳健/聚类标准误，跟 sensemakr 内部的经典 t 不是一个数，
    所以这条测试必须关掉固定效应与聚类——不是为了简化，是因为 sensemakr 本身就是同方差经典 OLS。"""
    sp = pytest.importorskip("statspai")
    df = pd.read_csv(FIX / "tiny.csv")
    ours = run_plan(df, _causal_ols(fixed_effects=[], cluster_level="")).diagnostics
    theirs = sp.sensemakr(df, y="income", treat="treat", controls=["edu_years"])
    assert float(ours["稳健值"]) == pytest.approx(float(theirs["rv_q"]), abs=2e-3)


def test_Oster照原文的近似式() -> None:
    """Oster 2019 §3.2：β* ≈ β̃ − δ(β̊ − β̃)(R_max − R̃)/(R̃ − R̊)。
    **不跟 StatsPAI `oster_bounds` 对**——它把比值写倒了（见 `oster()` 的文档串）。"""
    delta0, beta_star, r_max = cc.oster(beta_short=2.5, r2_short=0.15, beta_long=2.0, r2_long=0.45)
    assert r_max == pytest.approx(0.585)
    assert beta_star == pytest.approx(2.0 - 0.5 * (0.585 - 0.45) / (0.45 - 0.15))        # 1.775
    assert delta0 == pytest.approx(2.0 * (0.45 - 0.15) / (0.5 * (0.585 - 0.45)))        # 8.89


def test_Oster剩余空间越小调整越小() -> None:
    """R_max 逼近 R̃：未观测变量没有剩余的解释空间，偏误调整趋于 0、δ* 趋于无穷。倒过来的式子在这里会趋于无穷。"""
    adj = [2.0 - cc.oster(2.5, 0.15, 2.0, 0.45, r_max=rm)[1] for rm in (0.9, 0.6, 0.46, 0.4501)]
    assert adj == sorted(adj, reverse=True) and adj[-1] < 1e-3
    assert cc.oster(2.5, 0.15, 2.0, 0.45, r_max=0.45)[0] is None
    assert cc.oster(2.5, 0.15, 2.0, 0.45, r_max=0.40)[1] == 2.0          # 给的 R_max 比 R̃ 还小：不调整


def test_E值跟StatsPAI对数() -> None:
    sp = pytest.importorskip("statspai")
    e_pt, e_ci = cc.evalue_ols(beta=0.5, se=0.1, sd=2.0)
    theirs = sp.evalue(estimate=0.5, se=0.1, measure="OLS", sd=2.0)
    assert e_pt == pytest.approx(theirs["evalue_estimate"], abs=2e-3)
    assert e_ci == pytest.approx(theirs["evalue_ci"], abs=2e-3)


def test_短回归与辅助回归跟主回归用同一批行(monkeypatch) -> None:
    """控制变量、结果变量各有缺失时，短回归、辅助回归不重新按主回归的样本筛，
    就会在跟主回归不同的一批人上跑——Oster 比较的两个 R² 就成了两批不同的人。
    复现：`father_edu_years` 缺 60 行、`occupation_isei` 再缺 10 行，主回归 n=290，
    改之前短回归 n=300（只看结果与处理，不受控制变量缺失影响）、辅助回归 n=290
    （看处理与控制变量，不受结果变量缺失影响）——三个 n 应该相等但没有。
    """
    df = pd.read_csv(FIX / "cgss_like.csv")
    rng = np.random.default_rng(0)
    idx_ctrl = rng.choice(df.index, size=60, replace=False)
    df.loc[idx_ctrl, "father_edu_years"] = np.nan
    idx_y = rng.choice(df.index.difference(idx_ctrl), size=10, replace=False)
    df.loc[idx_y, "occupation_isei"] = np.nan

    plan = _causal_ols(outcome="occupation_isei", treatment="own_edu_years",
                       controls=["father_edu_years", "hukou_urban"],
                       control_roles={"father_edu_years": "confounder", "hukou_urban": "confounder"},
                       fixed_effects=["province", "survey_year"], cluster_level="province")

    from oss.core import analysis as analysis_mod

    captured: dict = {}
    real = cc.sensitivity_texts

    def spy(fit, main_coef, main_se, treatment, short_fit, aux_fit, controls, sd_y):
        captured["fit"], captured["short_fit"], captured["aux_fit"] = fit, short_fit, aux_fit
        return real(fit, main_coef, main_se, treatment, short_fit, aux_fit, controls, sd_y)

    monkeypatch.setattr(cc, "sensitivity_texts", spy)
    analysis_mod.run_plan(df, plan)
    assert captured["short_fit"].n == captured["aux_fit"].n == captured["fit"].n


def test_没有固定效应就不许说有(monkeypatch) -> None:
    """**真跑时论文审稿人逐字点出来的。**「OLS 对照」这句原先无条件写
    「同一样本、同样控制与固定效应的 OLS」，而主规格 `fixed_effects` 为空时
    这是不实的——读者会以为 OLS 对照带了固定效应而 2SLS 没带。

    判据是 `k_absorbed`（吸收的自由度），**算得出来的事实**，不是计划里的声明。
    """
    import numpy as np

    from oss.core import causal_checks as C

    monkeypatch.setattr(C, "effective_f", lambda *a, **k: 50.0)
    monkeypatch.setattr(C, "anderson_rubin", lambda *a, **k: (0.01, 0.09, "有界", 0.02))
    monkeypatch.setattr(C, "tf_interval", lambda *a, **k: None)
    args = (np.zeros(5), np.zeros(5), np.zeros((5, 1)), np.zeros((5, 1)), None)

    no_fe = C.iv_diagnostics(*args, 0, 0.04, 0.02, 0.02, 0.01)["OLS 对照"]
    assert "主规格无固定效应" in no_fe, no_fe
    assert "同样控制与固定效应" not in no_fe, f"没有固定效应却说有：{no_fe}"

    with_fe = C.iv_diagnostics(*args, 30, 0.04, 0.02, 0.02, 0.01)["OLS 对照"]
    assert "同样控制与固定效应" in with_fe, with_fe
