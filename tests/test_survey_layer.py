"""调查层（设计稿 docs/design/2026-09-14-survey-layer.md）。按实施计划的任务分节。"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oss.core import causal_stats as cs

FIX = Path(__file__).parent / "fixtures"


# ══════════════════════════════ Task 1：t 分布


@pytest.mark.parametrize("t,df", [(0.0, 5), (1.0, 3), (2.0, 10), (-1.5, 7), (3.2, 111), (0.3, 1)])
def test_t分布上尾跟scipy一致(t: float, df: int) -> None:
    stats = pytest.importorskip("scipy.stats")
    assert cs.t_sf(t, df) == pytest.approx(stats.t.sf(t, df), rel=1e-9, abs=1e-12)


@pytest.mark.parametrize("q,df", [(0.975, 5), (0.975, 30), (0.975, 111), (0.9, 2), (0.025, 12)])
def test_t分布分位跟scipy一致(q: float, df: int) -> None:
    stats = pytest.importorskip("scipy.stats")
    assert cs.t_ppf(q, df) == pytest.approx(stats.t.ppf(q, df), rel=1e-7)


def test_自由度很大时t分布趋近正态() -> None:
    from statistics import NormalDist
    assert cs.t_sf(1.96, 100_000) == pytest.approx(1 - NormalDist().cdf(1.96), rel=1e-4)


def test_不完全贝塔的端点() -> None:
    assert cs.betainc(2.0, 3.0, 0.0) == 0.0 and cs.betainc(2.0, 3.0, 1.0) == 1.0
    assert cs.betainc(1.0, 1.0, 0.37) == pytest.approx(0.37)          # Beta(1,1) 是均匀分布


# ══════════════════════════════ Task 2：契约

from oss.core.contracts import (  # noqa: E402
    AnalysisPlan, DataProfile, Descriptive, PowerReport, SpecSummary, SurveyDesign, SurveySignals,
)


def test_老计划没有survey照样读得进(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"plan_id": "p", "idea_id": "i", "outcome": "y", "treatment": "x"}), encoding="utf-8")
    assert AnalysisPlan.load(p).survey is None


def test_抽样设计不认得的键直接拒() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SurveyDesign(stratum="s41")                       # 少了一个 a：照抄错的键不许悄悄变成空设计


@pytest.mark.parametrize("kw,ok", [
    (dict(design_source="登记：CGSS 2017 码本", weight_use="descriptive", psu="county"), True),
    (dict(not_survey_reason="这份数据是全国普查的完整抽取，不是抽样"), True),
    (dict(design_source="登记", weight_use=""), False),          # 没说权重怎么用
    (dict(psu="county"), False),                                  # 没说设计从哪来
    (dict(), False),
])
def test_算不算声明了抽样设计(kw: dict, ok: bool) -> None:
    assert SurveyDesign(**kw).declared() is ok


def test_画像默认没有调查信号() -> None:
    prof = DataProfile(file={"path": "d.csv"})            # FileInfo 的 path 必填：DataProfile() 空着构造不了
    assert prof.survey == SurveySignals() and prof.survey.likely is False


def test_新栏位的默认值() -> None:
    assert Descriptive(name="x", n=1, mean=0, sd=0, min=0, max=0).weighted is False
    s = SpecSummary()
    assert (s.weights, s.strata, s.psu, s.t_crit) == ("", "", "", 0.0)
    assert PowerReport.model_fields["weight_design_effect"].default == 1.0


# ══════════════════════════════ Task 3：画像认调查信号

from oss.core.loaders import LoadedTable  # noqa: E402
from oss.core.profiler import profile_table  # noqa: E402


def _prof(df: pd.DataFrame):
    return profile_table(LoadedTable(frame=df, path=FIX / "tiny.csv", fmt="csv"))


def _cgss_like(n: int = 600, seed: int = 1) -> pd.DataFrame:
    """照 CGSS 2017 画像里的几列造：权重 weight、省份码 s41、区县 county、一道有序题。"""
    rng = np.random.default_rng(seed)
    county = np.repeat(np.arange(30), n // 30)
    return pd.DataFrame({
        "id": np.arange(n), "s41": county // 3, "county": county,
        "weight": rng.uniform(0.3, 3.0, n), "a53": rng.integers(1, 5, n),
        "y": rng.normal(size=n),
    })


def test_照CGSS那几列认出权重与区县() -> None:
    s = _prof(_cgss_like()).survey
    assert s.likely and s.weight_candidates == ["weight"] and s.psu_candidates == ["county"]
    assert s.strata_candidates == []          # s41 靠名字认不出（计划第零节第 1 条），要登记或用户说


def test_照GSS那几列认出分层与抽样单位() -> None:
    rng = np.random.default_rng(2)
    n = 400
    df = pd.DataFrame({"vstrat": np.repeat(np.arange(20), n // 20), "vpsu": np.tile([1, 2], n // 2),
                       "wtssall": rng.uniform(0.4, 4.0, n), "y": rng.normal(size=n)})
    s = _prof(df).survey
    assert s.strata_candidates == ["vstrat"] and s.psu_candidates == ["vpsu"] and "wtssall" in s.weight_candidates


def test_插补标记认出来并对上它标的那一列() -> None:
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"income": rng.normal(size=200), "income_imputed": rng.integers(0, 2, 200),
                       "import_share": rng.uniform(size=200)})
    s = _prof(df).survey
    assert s.imputation_flags == {"income_imputed": "income"} and s.likely


def test_社会阶层不是分层() -> None:
    rng = np.random.default_rng(4)
    df = pd.DataFrame({"主观阶层": rng.integers(1, 11, 300), "y": rng.normal(size=300)})
    s = _prof(df).survey
    assert s.strata_candidates == [] and not s.likely


def test_抽样单位的编码形态_整数值的浮点列算_非整数的不算() -> None:
    from oss.core.profiler import _design_like
    codes = pd.Series(np.repeat(np.arange(30), 10).astype(float))   # 带过缺失的整数编码读进来是浮点
    assert _design_like(codes, len(codes))
    assert not _design_like(codes + 0.5, len(codes))                # 取值个数、每个取值的行数都一样，只差是不是整数


def test_有信号时画像警告说是疑似要确认() -> None:
    w = " ".join(_prof(_cgss_like()).warnings)
    assert "疑似抽样调查" in w and "plan.survey" in w and "not_survey_reason" in w


def test_没有信号的数据不警告() -> None:
    from oss.core.profiler import profile_file
    p = profile_file(FIX / "did_clean.csv")
    assert not p.survey.likely and not any("疑似抽样调查" in x for x in p.warnings)


# ══════════════════════════════ Task 4：计划的两道门

from oss.core.analysis import validate_plan  # noqa: E402


@pytest.fixture
def cgss_prof(tmp_path: Path):
    from oss.core.profiler import profile_file
    p = tmp_path / "cgss.csv"
    _cgss_like().to_csv(p, index=False)
    return profile_file(p)


def _survey_plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="a53",
                research_type="descriptive", weights="weight")
    return AnalysisPlan(**{**base, **kw})


def test_有调查信号却没声明设计就拒(cgss_prof) -> None:
    problems, _ = validate_plan(_survey_plan(), cgss_prof)
    assert any("调查信号" in p and "plan.survey" in p for p in problems), problems


@pytest.mark.parametrize("survey", [
    SurveyDesign(psu="county", weight_use="descriptive", design_source="用户说：区县是初级抽样单位"),
    SurveyDesign(not_survey_reason="只用来试流程，不推到总体"),
])
def test_声明了设计或写了理由就放行(cgss_prof, survey) -> None:
    problems, _ = validate_plan(_survey_plan(survey=survey), cgss_prof)
    assert not any("调查信号" in p for p in problems), problems


def test_老计划没写研究类型只警告(cgss_prof) -> None:
    problems, warnings = validate_plan(_survey_plan(research_type=""), cgss_prof)
    assert not any("调查信号" in p for p in problems)
    assert any("调查信号" in w and "老计划" in w for w in warnings), warnings


def test_声明的抽样单位列不在数据里就拒(cgss_prof) -> None:
    plan = _survey_plan(survey=SurveyDesign(psu="village", weight_use="model", design_source="x"))
    problems, _ = validate_plan(plan, cgss_prof)
    assert any("village" in p and "不在数据里" in p for p in problems), problems


def test_抽样单位与聚类层不同时警告主规格按设计(cgss_prof) -> None:
    plan = _survey_plan(cluster_level="s41",
                        survey=SurveyDesign(psu="county", weight_use="model", design_source="x"))
    _, warnings = validate_plan(plan, cgss_prof)
    assert any("按抽样设计" in w and "s41" in w for w in warnings), warnings


# ══════════════════════════════ Task 5：按抽样设计的方差

from oss.core.analysis import _cluster_robust_vcov, _design_vcov, run_ols_fe, run_plan  # noqa: E402


def _ols_pieces(seed: int = 5, n: int = 480):
    rng = np.random.default_rng(seed)
    X = np.column_stack([np.ones(n), rng.normal(size=n), rng.normal(size=n)])
    y = X @ np.array([1.0, 0.5, -0.3]) + rng.normal(size=n)
    xtx_inv = np.linalg.inv(X.T @ X)
    resid = y - X @ (xtx_inv @ X.T @ y)
    psu = np.repeat(np.arange(48), n // 48)
    strata = psu // 4                                        # 12 层，每层 4 个抽样单位
    return X, resid, xtx_inv, psu, strata


def test_不写分层时跟聚类稳健方差逐位一致() -> None:
    X, resid, xtx_inv, psu, _ = _ols_pieces()
    V, info = _design_vcov(X, resid, None, psu, xtx_inv)
    assert np.array_equal(V, _cluster_robust_vcov(X, resid, psu, xtx_inv))
    assert info == {"psu": 48, "strata": 1, "singletons": 0, "dof": 47}


def test_只有一层时跟聚类稳健方差一致() -> None:
    X, resid, xtx_inv, psu, _ = _ols_pieces()
    V, _ = _design_vcov(X, resid, np.zeros(len(psu)), psu, xtx_inv)
    assert V == pytest.approx(_cluster_robust_vcov(X, resid, psu, xtx_inv), rel=1e-10)


def test_分层的方差跟手算一致() -> None:
    X, resid, xtx_inv, psu, strata = _ols_pieces()
    n, k = X.shape
    meat = np.zeros((k, k))
    for h in np.unique(strata):
        zs = [X[(strata == h) & (psu == i)].T @ resid[(strata == h) & (psu == i)]
              for i in np.unique(psu[strata == h])]
        zbar = np.mean(zs, axis=0)
        meat += len(zs) / (len(zs) - 1) * sum(np.outer(z - zbar, z - zbar) for z in zs)
    want = (n - 1) / (n - k) * (xtx_inv @ meat @ xtx_inv)
    V, info = _design_vcov(X, resid, strata, psu, xtx_inv)
    assert V == pytest.approx(want, rel=1e-10) and info["dof"] == 48 - 12


def test_只有一个抽样单位的层按全体均值中心化() -> None:
    X, resid, xtx_inv, psu, strata = _ols_pieces()
    strata = strata.copy()
    strata[psu == 47] = 99                                    # 第 47 号单独成一层
    _, info = _design_vcov(X, resid, strata, psu, xtx_inv)
    assert info["singletons"] == 1


def _survey_frame(seed: int = 6, n: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    county = np.repeat(np.arange(40), n // 40)
    df = pd.DataFrame({"county": county, "prov": county // 4, "w": rng.uniform(0.5, 2.5, n),
                       "x": rng.normal(size=n) + 0.3 * (county % 5)})
    df["y"] = 0.4 * df.x + 0.2 * (county % 7) + rng.normal(size=n)
    return df


def test_声明了设计的回归用t分布的p值并报抽样单位数() -> None:
    plan = AnalysisPlan(outcome="y", treatment="x", weights="w",
                        survey=SurveyDesign(strata="prov", psu="county", weight_use="model", design_source="测试"))
    res = run_ols_fe(_survey_frame(), plan)
    m, d = res.estimates["main"], res.diagnostics
    assert "按抽样设计" in d["标准误"] and d["抽样单位数"] == "40" and d["抽样设计自由度"] == "30"
    assert m.p == pytest.approx(2 * cs.t_sf(abs(m.coef / m.se), 30), abs=2e-6)


def test_没声明设计的回归照旧按正态() -> None:
    from statistics import NormalDist
    res = run_ols_fe(_survey_frame(), AnalysisPlan(outcome="y", treatment="x", weights="w", cluster_level="county"))
    m = res.estimates["main"]
    assert "抽样单位数" not in res.diagnostics
    assert m.p == pytest.approx(2 * (1 - NormalDist().cdf(abs(m.coef / m.se))), abs=2e-6)


def test_表注与区间临界值跟着设计走() -> None:
    plan = AnalysisPlan(plan_id="p", idea_id="i", outcome="y", treatment="x", weights="w",
                        survey=SurveyDesign(strata="prov", psu="county", weight_use="model", design_source="测试"))
    res = run_plan(_survey_frame(), plan)
    assert (res.spec.weights, res.spec.strata, res.spec.psu) == ("w", "prov", "county")
    assert res.spec.t_crit == pytest.approx(cs.t_ppf(0.975, 30), rel=1e-6)


def test_设计列缺失的行去掉并算进因缺失丢弃() -> None:
    df = _survey_frame()
    df.loc[:9, "county"] = np.nan
    plan = AnalysisPlan(outcome="y", treatment="x", weights="w",
                        survey=SurveyDesign(psu="county", weight_use="model", design_source="测试"))
    assert run_ols_fe(df, plan).n == len(df) - 10


def test_声明了设计的结果表注写明抽样设计与加权() -> None:
    from oss.core.manuscript import table_markdown
    plan = AnalysisPlan(plan_id="p", idea_id="i", outcome="y", treatment="x", weights="w", cluster_level="prov",
                        survey=SurveyDesign(strata="prov", psu="county", weight_use="model", design_source="测试"))
    res = run_plan(_survey_frame(), plan)
    zh, why = table_markdown("main", res, en=False)
    assert zh is not None, why
    assert "抽样设计" in zh and "按 w 加权" in zh and "聚类" not in zh, zh
    en, why = table_markdown("main", res, en=True)
    assert en is not None, why
    assert "sampling design" in en and "weighted by w" in en and "clustered" not in en, en


def test_稳健性只改聚类层时按那一层聚类_不再按抽样设计() -> None:
    """主计划的 survey 会被 `_patched` 抄进每一项稳健性；只写了 cluster_level 的那一项
    若照旧按设计算，「按 cluster_level 聚类的那版」就悄悄不是按它聚类的。"""
    from oss.core.analysis import _patched
    from oss.core.contracts import RobustnessSpec
    df = _survey_frame()
    design = SurveyDesign(psu="county", weight_use="model", design_source="x")
    by_prov = RobustnessSpec(label="按省聚类", cluster_level="prov", executable=True)
    unweighted = RobustnessSpec(label="不加权", weights="", executable=True)
    plan = AnalysisPlan(plan_id="p", idea_id="i", outcome="y", treatment="x", weights="w", survey=design,
                        robustness=[by_prov, unweighted])
    assert _patched(plan, by_prov).survey is None and _patched(plan, by_prov).weights == "w"
    assert _patched(plan, unweighted).survey == design

    res = run_plan(df, plan)
    got = res.robustness["按省聚类"].estimates["main"].se
    direct = run_ols_fe(df, AnalysisPlan(outcome="y", treatment="x", weights="w", cluster_level="prov"))
    assert got == pytest.approx(direct.estimates["main"].se, rel=1e-12)
    assert got != pytest.approx(res.estimates["main"].se, rel=1e-6)

    kept = res.robustness["不加权"]
    assert "按抽样设计" in kept.diagnostics["标准误"]
    same = run_ols_fe(df, AnalysisPlan(outcome="y", treatment="x", survey=design))
    assert kept.estimates["main"].se == pytest.approx(same.estimates["main"].se, rel=1e-12)


# ══════════════════════════════ 审查第 1、2 批的修补


@pytest.mark.parametrize("df", [0, -1, float("nan"), float("inf")])
def test_t分位对非法自由度报错(df: float) -> None:
    with pytest.raises(ValueError):
        cs.t_ppf(0.975, df)


def test_连分式到迭代上限还没收敛就报错(monkeypatch) -> None:
    # 自然的输入到不了上限（a = b = 1e12 也只要两千来步），所以把上限调低来走这条路
    monkeypatch.setattr(cs, "_BETACF_MAX_ITER", 2, raising=False)
    with pytest.raises(RuntimeError):
        cs._betacf(2.0, 3.0, 0.3)


def test_区县乘年份的面板没有权重不算疑似调查_区县照样列为候选() -> None:
    rng = np.random.default_rng(11)
    df = pd.DataFrame({"county": np.repeat(np.arange(30), 10), "year": np.tile(np.arange(2010, 2020), 30),
                       "y": rng.normal(size=300)})
    s = _prof(df).survey
    assert s.psu_candidates == ["county"] and not s.likely


def test_超重的0_1与带零的权重列都不算权重候选() -> None:
    rng = np.random.default_rng(12)
    w = rng.uniform(0.3, 3.0, 300)
    w[:5] = 0.0
    df = pd.DataFrame({"overweight": rng.integers(0, 2, 300), "weight": w, "y": rng.normal(size=300)})
    s = _prof(df).survey
    assert s.weight_candidates == [] and not s.likely


def test_CFPS的权重名认得出() -> None:
    rng = np.random.default_rng(13)
    df = pd.DataFrame({"rswt_natcs16": rng.uniform(0.2, 5.0, 300), "fswt_nat": rng.uniform(0.2, 5.0, 300),
                       "y": rng.normal(size=300)})
    s = _prof(df).survey
    assert sorted(s.weight_candidates) == ["fswt_nat", "rswt_natcs16"] and s.likely


def test_CFPS的分层变量名只算弱信号() -> None:
    rng = np.random.default_rng(14)
    df = pd.DataFrame({"subpopulation": np.repeat(np.arange(6), 50), "y": rng.normal(size=300)})
    s = _prof(df).survey
    assert s.strata_candidates == ["subpopulation"] and not s.likely


def test_插补标记只带imp或取值恒定只算弱信号() -> None:
    rng = np.random.default_rng(15)
    df = pd.DataFrame({"income": rng.normal(size=200), "income_imp": rng.integers(0, 2, 200),
                       "edu": rng.normal(size=200), "edu_imputed": np.zeros(200, dtype=int)})
    s = _prof(df).survey
    assert s.imputation_flags == {"income_imp": "income", "edu_imputed": "edu"} and not s.likely


def test_写了抽样单位就不再催聚类层_少于30个按抽样单位数(cgss_prof) -> None:
    design = SurveyDesign(psu="county", weight_use="model", design_source="x")
    _, warnings = validate_plan(_survey_plan(survey=design), cgss_prof)
    assert not any("没指定聚类层级" in w for w in warnings), warnings
    few = SurveyDesign(psu="s41", weight_use="model", design_source="x")
    _, warnings = validate_plan(_survey_plan(survey=few, cluster_level="county"), cgss_prof)
    assert any("只有 10 个初级抽样单位" in w for w in warnings), warnings


@pytest.mark.parametrize("field,value", [("strata", "s41"), ("psu", "county"), ("weight_use", "model"),
                                         ("design_source", "登记")])
def test_理由与抽样设计二选一(cgss_prof, field: str, value: str) -> None:
    both = SurveyDesign(not_survey_reason="其实是普查的完整抽取", **{field: value})
    problems, _ = validate_plan(_survey_plan(survey=both), cgss_prof)
    assert any("二选一" in p for p in problems), problems


def test_老画像没有survey这一栏时权重列也算信号(cgss_prof) -> None:
    raw = json.loads(cgss_prof.model_dump_json())
    del raw["survey"]                                  # 调查层之前的 profile.json 就是这样（如 runs/gss20260824_v2）
    old = DataProfile.model_validate(raw)
    problems, _ = validate_plan(_survey_plan(), old)
    assert any("调查信号" in p and "权重候选 weight" in p for p in problems), problems
    rng = np.random.default_rng(16)
    fresh = _prof(pd.DataFrame({"overweight": rng.integers(0, 2, 300), "y": rng.normal(size=300)}))
    problems, _ = validate_plan(_survey_plan(weights=""), fresh)
    assert not any("调查信号" in p for p in problems), "新画像有 survey 这一栏，不走老画像那条路"


def test_调查信号的拒绝写明权重用法的取值并给例子(cgss_prof) -> None:
    problems, _ = validate_plan(_survey_plan(), cgss_prof)
    msg = next(p for p in problems if "调查信号" in p)
    assert "descriptive" in msg and "model" in msg and '"survey": {"strata": ""' in msg, msg


def test_权重列不在数据里就拒_说是计划写的列(cgss_prof) -> None:
    design = SurveyDesign(psu="county", weight_use="model", design_source="x")
    problems, _ = validate_plan(_survey_plan(weights="wt_nope", survey=design), cgss_prof)
    assert any("wt_nope" in p and "不在数据里" in p and "计划写的列" in p for p in problems), problems


def test_权重列整列缺失就拒(tmp_path: Path) -> None:
    from oss.core.profiler import profile_file
    df = _cgss_like()
    df["wt_empty"] = np.nan
    path = tmp_path / "d.csv"
    df.to_csv(path, index=False)
    design = SurveyDesign(psu="county", weight_use="model", design_source="x")
    problems, _ = validate_plan(_survey_plan(weights="wt_empty", survey=design), profile_file(path))
    assert any("wt_empty" in p and "整列缺失" in p and "计划写的列" in p for p in problems), problems


def test_分层列不在数据里就拒(cgss_prof) -> None:
    design = SurveyDesign(strata="stratum_nope", psu="county", weight_use="model", design_source="x")
    problems, _ = validate_plan(_survey_plan(survey=design), cgss_prof)
    assert any("stratum_nope" in p and "不在数据里" in p and "抽样设计写的列" in p for p in problems), problems


# ══════════════════════════════ Task 6：六种估计量与分箱都能加权

from oss.core.analysis import PlanRejected  # noqa: E402


def _iv_frame(seed: int = 7, n: int = 800) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    z, c, u = rng.normal(size=n), rng.normal(size=n), rng.normal(size=n)
    x = 0.8 * z + 0.3 * c + 0.5 * u + rng.normal(size=n)
    y = 2.0 * x + 0.4 * c + u + rng.normal(size=n)
    return pd.DataFrame({"y": y, "x": x, "z": z, "c": c, "wt": rng.uniform(0.3, 3.0, n)})


def test_加权两阶段最小二乘跟linearmodels一致() -> None:
    lm = pytest.importorskip("linearmodels.iv")
    df = _iv_frame()
    got = run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", estimator="iv", outcome="y", treatment="x",
                                    instruments=["z"], controls=["c"], weights="wt"))
    want = lm.IV2SLS(df.y, pd.DataFrame({"const": 1.0, "c": df.c}), df[["x"]], df[["z"]], weights=df.wt).fit()
    assert got.estimates["main"].coef == pytest.approx(want.params["x"], rel=1e-6)
    assert "wt" in got.diagnostics["抽样权重"]


def test_加权断点跟窗口内的WLS一致() -> None:
    sm = pytest.importorskip("statsmodels.api")
    rng = np.random.default_rng(8)
    n = 1500
    score = rng.uniform(40, 80, n)
    d = (score >= 60).astype(float)
    df = pd.DataFrame({"score": score, "w": rng.uniform(0.5, 2.0, n)})
    df["y"] = 4.0 * d + 0.1 * (score - 60) + rng.normal(size=n)
    got = run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", estimator="rdd", outcome="y", running="score",
                                    cutoff=60.0, bandwidth=10.0, weights="w"))
    win = df[(df.score - 60).abs() <= 10].assign(r=lambda t: t.score - 60, p=lambda t: (t.score >= 60) * 1.0)
    X = sm.add_constant(pd.DataFrame({"p": win.p, "r": win.r, "rx": win.r * win.p}))
    want = sm.WLS(win.y, X, weights=win.w).fit()
    assert got.estimates["main"].coef == pytest.approx(want.params["p"], rel=1e-6)


def test_分箱的每一箱跟那一箱的WLS一致() -> None:
    sm = pytest.importorskip("statsmodels.api")
    from oss.core.analysis import binned_slopes
    rng = np.random.default_rng(9)
    n = 600
    df = pd.DataFrame({"x": rng.normal(size=n), "b": np.repeat([1, 2], n // 2), "w": rng.uniform(0.2, 5.0, n)})
    df["y"] = np.where(df.b == 1, 1.0, -1.0) * df.x + rng.normal(size=n)
    (_, pts), = binned_slopes(df, "y", "x", "b", bins=2, weights="w")
    one = df[df.b == 1]
    want = sm.WLS(one.y, sm.add_constant(one.x), weights=one.w).fit().params["x"]
    assert pts[0][1] == pytest.approx(want, rel=1e-6)


def test_分批双重差分的权重在单位内会变就拒() -> None:
    pytest.importorskip("statspai")
    df = pd.read_csv(FIX / "did_staggered.csv")
    df["w"] = np.random.default_rng(10).uniform(0.5, 2.0, len(df))          # 每行不同
    with pytest.raises(PlanRejected, match="单位内"):
        run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", estimator="did_cs", outcome="y", unit="unit",
                                  time="year", treat_time="treat_time", weights="w"))


def test_分批双重差分吃单位层面的权重() -> None:
    pytest.importorskip("statspai")
    df = pd.read_csv(FIX / "did_staggered.csv")
    per_unit = {u: 0.5 + (i % 4) for i, u in enumerate(sorted(df.unit.unique()))}
    df["w"] = df.unit.map(per_unit)
    plan = dict(plan_id="p", idea_id="i", estimator="did_cs", outcome="y", unit="unit", time="year",
                treat_time="treat_time")
    weighted = run_plan(df, AnalysisPlan(**plan, weights="w"))
    plain = run_plan(df, AnalysisPlan(**plan))
    assert "w" in weighted.diagnostics["抽样权重"]
    assert weighted.estimates["main"].coef != pytest.approx(plain.estimates["main"].coef, rel=1e-9)


@pytest.mark.parametrize("est", ["ols_fe", "did", "did_cs", "event_study", "iv", "rdd"])
def test_计划校验不再因为估计量拦权重(est: str) -> None:
    from oss.core.profiler import profile_file
    prof = profile_file(FIX / "did_clean.csv")
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator=est, outcome="y", treatment="post",
                        unit="unit", time="year", treat_time="treat_time", weights="x")
    problems, _ = validate_plan(plan, prof)
    assert not any("不支持抽样权重" in p for p in problems), problems


# ── 第 3 批留下的两处空缺，与审查第 3 批查出的四条（都并进任务 6）

_DESIGN = SurveyDesign(strata="prov", psu="county", weight_use="model", design_source="测试")


def _staggered_design_frame() -> pd.DataFrame:
    """分批面板加上抽样设计：每 5 个单位一个初级抽样单位（在单位内不变），每 2 个抽样单位一层。"""
    df = pd.read_csv(FIX / "did_staggered.csv")
    df["county"] = df.unit // 5
    df["prov"] = df.county // 2
    return df


def test_分批双重差分声明了设计时按抽样单位聚类_表注照实说分层没进() -> None:
    """StatsPAI 只能按抽样单位聚类、不认分层：表注不许说「按抽样设计、泰勒线性化」。"""
    pytest.importorskip("statspai")
    from oss.core.manuscript import table_markdown
    df = _staggered_design_frame()
    base = dict(plan_id="p", idea_id="i", estimator="did_cs", outcome="y", unit="unit", time="year",
                treat_time="treat_time")
    res = run_plan(df, AnalysisPlan(**base, survey=_DESIGN))
    zh, why = table_markdown("main", res, en=False)
    assert zh is not None, why
    assert "按初级抽样单位 county 聚类" in zh and "分层 prov 没进方差" in zh and "泰勒线性化" not in zh, zh
    en, why = table_markdown("main", res, en=True)
    assert en is not None, why
    assert "clustered by PSU county" in en and "strata (prov) not reflected" in en and "Taylor" not in en, en
    assert "按初级抽样单位 county 聚类" in res.diagnostics["标准误"] and "分层" in res.diagnostics["标准误"]
    # 真按抽样单位聚类了：跟「按 county 聚类」同一条路、同一个数；跟不聚类的不同
    by_county = run_plan(df, AnalysisPlan(**base, cluster_level="county"))
    plain = run_plan(df, AnalysisPlan(**base))
    assert res.estimates["main"].se == pytest.approx(by_county.estimates["main"].se, rel=1e-12)
    assert res.estimates["main"].se != pytest.approx(plain.estimates["main"].se, rel=1e-6)


@pytest.mark.parametrize("est", ["did", "event_study"])
def test_声明了设计时双重差分不替用户补unit聚类(est: str) -> None:
    """转交 run_ols_fe 的内层计划原先缺省按 unit 聚类：声明了设计时，run_ols_fe 就报
    「cluster_level（unit）没用上」——可用户根本没写。只有用户自己写了别的聚类层才该这么报。"""
    df = pd.read_csv(FIX / "did_clean.csv")
    df["county"] = df.unit // 3
    df["region"] = df.unit // 10
    base = dict(plan_id="p", idea_id="i", estimator=est, outcome="y", unit="unit", time="year",
                treat_time="treat_time", survey=SurveyDesign(psu="county", weight_use="model", design_source="测试"))
    quiet = run_plan(df, AnalysisPlan(**base)).diagnostics["标准误"]
    assert "按抽样设计" in quiet and "没用上" not in quiet, quiet
    loud = run_plan(df, AnalysisPlan(**base, cluster_level="region")).diagnostics["标准误"]
    assert "cluster_level（region）没用上" in loud, loud


def _rdd_design_frame(seed: int = 18, n: int = 1600) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    score = rng.uniform(40, 80, n)
    df = pd.DataFrame({"score": score, "w": rng.uniform(0.5, 2.0, n), "county": rng.integers(0, 40, n)})
    df["prov"] = df.county // 4
    df["y"] = 4.0 * (score >= 60) + 0.1 * (score - 60) + 0.3 * (df.county % 5) + rng.normal(size=n)
    return df


def test_声明了设计却没按设计算的路径_表注不写抽样设计(monkeypatch) -> None:
    """表注那句「按抽样设计」只在真按设计算了（诊断里有抽样设计自由度）时才写。第 3 批时 iv、rdd 就是这样：
    标准误是聚类或 HC1，表注却说按抽样设计。这里把 rdd 换成一条丢掉设计、退回聚类的路径来模拟。"""
    from oss.core import analysis as A
    from oss.core.manuscript import table_markdown
    real = A.run_rdd
    monkeypatch.setattr(A, "run_rdd", lambda frame, plan: real(frame, plan.model_copy(update={"survey": None})))
    res = run_plan(_rdd_design_frame(), AnalysisPlan(plan_id="p", idea_id="i", estimator="rdd", outcome="y",
                                                     running="score", cutoff=60.0, bandwidth=10.0,
                                                     cluster_level="county", survey=_DESIGN))
    assert "抽样设计自由度" not in res.diagnostics
    assert (res.spec.psu, res.spec.strata) == ("", "")
    zh, why = table_markdown("main", res, en=False)
    assert zh is not None, why
    assert "抽样设计" not in zh and "按 county 聚类" in zh, zh


def test_工具变量按抽样设计算标准误_表注写明() -> None:
    from oss.core.manuscript import table_markdown
    df = _iv_frame(seed=17)
    df["county"] = np.repeat(np.arange(40), len(df) // 40)
    df["prov"] = df.county // 4
    base = dict(plan_id="p", idea_id="i", estimator="iv", outcome="y", treatment="x", instruments=["z"],
                controls=["c"], weights="wt")
    res = run_plan(df, AnalysisPlan(**base, survey=_DESIGN))
    d, m = res.diagnostics, res.estimates["main"]
    assert "按抽样设计" in d["标准误"] and d["抽样设计自由度"] == "30"
    assert m.p == pytest.approx(2 * cs.t_sf(abs(m.coef / m.se), 30), abs=2e-6)
    assert "按初级抽样单位 county 聚类" in d["有效 F 与 AR 的聚类"] and "分层 prov 没进" in d["有效 F 与 AR 的聚类"]
    zh, why = table_markdown("main", res, en=False)
    assert zh is not None, why
    assert "标准误按抽样设计算" in zh and "按 wt 加权" in zh, zh
    # 有效 F 与 AR 按抽样单位聚类（分层不进），跟「按 county 聚类」那次同一个数
    by_county = run_plan(df, AnalysisPlan(**base, cluster_level="county"))
    assert d["有效 F"] == by_county.diagnostics["有效 F"]
    # 不写分层时按设计的标准误跟按抽样单位聚类逐位一致（没有固定效应，吸收的自由度为 0）
    psu_only = run_plan(df, AnalysisPlan(**base, survey=SurveyDesign(psu="county", weight_use="model",
                                                                     design_source="测试")))
    assert psu_only.estimates["main"].se == pytest.approx(by_county.estimates["main"].se, rel=1e-12)


def test_断点按抽样设计算标准误_表注写明() -> None:
    from oss.core.manuscript import table_markdown
    res = run_plan(_rdd_design_frame(), AnalysisPlan(plan_id="p", idea_id="i", estimator="rdd", outcome="y",
                                                     running="score", cutoff=60.0, bandwidth=10.0, weights="w",
                                                     survey=_DESIGN))
    assert "按抽样设计" in res.diagnostics["标准误"] and res.diagnostics["抽样设计自由度"] == "30"
    assert "不加权" in res.diagnostics["卡线检验"]
    zh, why = table_markdown("main", res, en=False)
    assert zh is not None, why
    assert "标准误按抽样设计算" in zh and "按 w 加权" in zh, zh


def test_分层与抽样单位写成同一列就拒() -> None:
    """每层只有一个抽样单位时自由度 = 抽样单位数 − 层数 = 0，原先被悄悄抬成 1：t 临界值 12.7，z≈12 也不显著。"""
    plan = AnalysisPlan(outcome="y", treatment="x",
                        survey=SurveyDesign(strata="county", psu="county", weight_use="model", design_source="测试"))
    # 审查第 4 批：原先的「每层至少要两个」说过头了——单抽样单位的层是容忍的，只拒 G ≤ H
    with pytest.raises(PlanRejected, match="G ≤ H）：每层都只剩一个抽样单位"):
        run_ols_fe(_survey_frame(), plan)


def test_分箱按主规格跑时带上权重与抽样设计() -> None:
    """各箱内按主规格跑，就得跟主规格同样加权、同样按设计算标准误——边际效应图拿设计的 t 临界值乘它。"""
    from oss.core.contracts import BinnedSpec
    df = _survey_frame()
    df["c"] = np.random.default_rng(19).normal(size=len(df))
    df["b"] = np.tile([1, 2], len(df) // 2)
    plan = AnalysisPlan(plan_id="p", idea_id="i", outcome="y", treatment="x", controls=["c"], weights="w",
                        survey=_DESIGN, binned=BinnedSpec(by="b", bins=2))
    res = run_plan(df, plan)
    got = res.binned["全样本"][0]
    want = run_ols_fe(df[df.b == 1], AnalysisPlan(outcome="y", treatment="x", controls=["c"], weights="w",
                                                   survey=_DESIGN)).estimates["main"]
    assert got.slope == pytest.approx(want.coef, rel=1e-9) and got.se == pytest.approx(want.se, rel=1e-9)
    assert "按抽样设计" in res.diagnostics["分箱口径"] and "按 w 加权" in res.diagnostics["分箱口径"]


def test_只写分层没写抽样单位时警告分层不进方差(cgss_prof) -> None:
    plan = _survey_plan(survey=SurveyDesign(strata="s41", weight_use="model", design_source="x"))
    _, warnings = validate_plan(plan, cgss_prof)
    assert any("只写了分层没写初级抽样单位" in w for w in warnings), warnings


# ══════════════════════════════ Task 7：表 1 加权


def test_计划加权时表1按权重算均值与标准差() -> None:
    df = _survey_frame()
    res = run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", outcome="y", treatment="x", weights="w",
                                    survey=SurveyDesign(psu="county", weight_use="descriptive", design_source="测试")))
    d = next(x for x in res.descriptives if x.name == "y")
    m = np.average(df.y, weights=df.w)
    assert d.weighted and d.n == len(df)
    assert d.mean == pytest.approx(m) and d.sd == pytest.approx(math.sqrt(np.average((df.y - m) ** 2, weights=df.w)))


def test_不加权的计划表1照旧() -> None:
    df = _survey_frame()
    d = next(x for x in run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", outcome="y", treatment="x")).descriptives
             if x.name == "y")
    assert not d.weighted and d.mean == pytest.approx(df.y.mean())


def test_加权的表1表注写明() -> None:
    # 计划里写的是 manuscript.render("{{table:descriptives}}", …)：那一串没有中文，render 判它是英文稿，
    # 中文表注出不来——照现有测试的写法直接排表，中英各查一遍
    from oss.core.manuscript import table_markdown
    df = _survey_frame()
    res = run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", outcome="y", treatment="x", weights="w"))
    zh, why = table_markdown("descriptives", res, en=False)
    assert zh is not None, why
    assert "均值与标准差按 w 加权" in zh and "N 为未加权的行数" in zh, zh
    en, why = table_markdown("descriptives", res, en=True)
    assert en is not None, why
    assert "Means and SDs are weighted by w" in en and "N is the unweighted count" in en, en
    plain, _ = table_markdown("descriptives", run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", outcome="y",
                                                                        treatment="x")), en=False)
    assert plain is not None and "加权" not in plain, plain


@pytest.mark.parametrize("col", ["county", "w"])
def test_抽样单位或权重缺失的行_表1与样本日志也去掉_N跟回归一致(col: str) -> None:
    """主回归去掉了抽样单位、权重缺失的行（`_ols_rows`）；表 1 与样本构建日志不去的话，表 1 的 N 跟回归的 n 对不上。"""
    df = _survey_frame()
    df.loc[:9, col] = np.nan
    res = run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", outcome="y", treatment="x", weights="w",
                                    survey=SurveyDesign(psu="county", weight_use="model", design_source="测试")))
    assert res.n == len(df) - 10
    assert {d.n for d in res.descriptives} == {res.n}
    assert res.sample_log[-1].rows == res.n


# ── 审查第 3 批查出的三条（并进任务 7）


def test_稳健性森林图每一行按自己的设计自由度画区间_不按设计的用1_96(monkeypatch, tmp_path: Path) -> None:
    """主规格的 t 临界值不能套到每一行：换了样本的那项抽样单位少、自由度小，去掉设计的那项按正态。"""
    pytest.importorskip("matplotlib")
    import matplotlib.axes

    from oss.core import figures
    from oss.core.contracts import RobustnessSpec
    plan = AnalysisPlan(plan_id="p", idea_id="i", outcome="y", treatment="x", weights="w", survey=_DESIGN,
                        robustness=[RobustnessSpec(label="half", sample_filter="prov < 5", executable=True),
                                    RobustnessSpec(label="unweighted", weights="", executable=True),
                                    RobustnessSpec(label="by_prov", cluster_level="prov", executable=True)])
    res = run_plan(_survey_frame(), plan)
    rb = res.robustness
    assert rb["half"].t_crit == pytest.approx(cs.t_ppf(0.975, 15), rel=1e-6)          # 20 个抽样单位、5 层
    assert rb["unweighted"].t_crit == pytest.approx(cs.t_ppf(0.975, 30), rel=1e-6)    # 40 个抽样单位、10 层
    assert rb["by_prov"].t_crit == 0.0                                                # 只改聚类层的那项不按设计

    half_widths: list[float] = []
    real = matplotlib.axes.Axes.plot

    def spy(self, *args, **kw):
        if len(args) >= 2 and len(args[0]) == 2 and args[1][0] == args[1][1]:      # 区间那一横
            half_widths.append((args[0][1] - args[0][0]) / 2)
        return real(self, *args, **kw)

    monkeypatch.setattr(matplotlib.axes.Axes, "plot", spy)
    figures.robustness(res, tmp_path / "f.png", lang="en")
    se = [res.estimates["main"].se] + [rb[k].estimates["main"].se for k in ("half", "unweighted", "by_prov")]
    crit = [res.spec.t_crit, rb["half"].t_crit, rb["unweighted"].t_crit, 1.96]
    assert half_widths == pytest.approx([c * s for c, s in zip(crit, se)], rel=1e-9)


def test_声明了设计时最小可探测效应按t分布算_没声明照旧2_80() -> None:
    from oss.core.contracts import Estimate
    from oss.core.manuscript import _englishize
    df = _survey_frame()
    res = run_ols_fe(df, AnalysisPlan(outcome="y", treatment="x", weights="w", survey=_DESIGN))
    m = res.estimates["main"]
    assert m.mde == pytest.approx((cs.t_ppf(0.975, 30) + cs.t_ppf(0.80, 30)) * m.se, rel=1e-5)
    assert Estimate.model_validate_json(m.model_dump_json()).mde == m.mde        # 存盘再读回来不退回正态
    note = res.diagnostics["最小可探测效应"]
    assert "自由度 30" in note and "2.90" in note, note
    en = _englishize(note)
    assert "30 df" in en and "自由度" not in en and "标准误" not in en, en
    plain = run_ols_fe(df, AnalysisPlan(outcome="y", treatment="x", weights="w", cluster_level="county"))
    assert plain.estimates["main"].mde == round(2.80 * plain.estimates["main"].se, 6)
    assert "≈2.8×标准误" in plain.diagnostics["最小可探测效应"]
    assert Estimate(term="x", coef=1.0, se=1.0, p=0.5, dof=30).mde == pytest.approx(2.90, abs=0.01)
    assert Estimate(term="x", coef=1.0, se=1.0, p=0.5, dof=9).mde == pytest.approx(3.15, abs=0.01)


def test_逐期平行趋势按给定的临界值判() -> None:
    from oss.core.analysis import _parallel_trends
    from oss.core.contracts import EventStudyPoint
    pts = [EventStudyPoint(k=-3, coef=0.1, se=1.0, n=10), EventStudyPoint(k=-2, coef=2.1, se=1.0, n=10)]
    assert "不成立" in _parallel_trends(pts)                                   # 正态：2.1 > 1.96
    assert "不成立" not in _parallel_trends(pts, crit=cs.t_ppf(0.975, 9))      # t(9)：2.1 < 2.26


def test_事件研究声明了设计时平行趋势用设计的t临界值(monkeypatch) -> None:
    """跟 figures.event 同一把尺子：图上标红的处理前各期，跟诊断里判「不成立」的是同一批。"""
    from oss.core import analysis as A
    seen: list[float] = []
    monkeypatch.setattr(A, "_parallel_trends_joint", lambda pts, cov, crit=1.96: seen.append(crit) or "x")
    df = pd.read_csv(FIX / "did_clean.csv")
    df["county"] = df.unit // 3
    base = dict(plan_id="p", idea_id="i", estimator="event_study", outcome="y", unit="unit", time="year",
                treat_time="treat_time")
    res = A.run_event_study(df, AnalysisPlan(**base, survey=SurveyDesign(psu="county", weight_use="model",
                                                                         design_source="测试")))
    A.run_event_study(df, AnalysisPlan(**base))
    dof = int(res.diagnostics["抽样设计自由度"])
    assert seen == [pytest.approx(cs.t_ppf(0.975, dof), rel=1e-6), 1.96]


# ══════════════════════════════ Task 8：功效用有效样本量

from oss.core import power as power_mod  # noqa: E402


def test_权重设计效应让有效样本量按比例变小() -> None:
    _, n1, _ = power_mod.compute_power(0.2, 2000)
    _, n2, _ = power_mod.compute_power(0.2, 2000, weight_deff=2.0)
    assert n2 == pytest.approx(n1 / 2)


def test_整群与权重两个设计效应相乘() -> None:
    _, n_c, _ = power_mod.compute_power(0.2, 2000, n_clusters=100, icc=0.05)
    _, n_cw, _ = power_mod.compute_power(0.2, 2000, n_clusters=100, icc=0.05, weight_deff=1.5)
    # compute_power 返回的有效样本量取到一位小数（1025.6 与 683.8），两边各差不到 0.05，所以按绝对差比
    assert n_cw == pytest.approx(n_c / 1.5, abs=0.1)


def test_功效报告分开报两个设计效应() -> None:
    rep = power_mod.analyze("i", 0.2, 2000, n_clusters=100, weight_design_effect=1.5, weight_column="weight")
    assert rep.weight_design_effect == 1.5 and rep.weight_column == "weight"
    assert "整群设计效应" in rep.reason and "权重设计效应" in rep.reason      # 计划写的 rationale，字段实名 reason


def test_没有权重时判词照旧只说设计效应() -> None:
    """冻结原文 tests/golden/power.txt 的数据没有权重列：那一句不许变。"""
    rep = power_mod.analyze("i", 0.2, 2000, n_clusters=100)
    assert "设计效应 = 1+(m−1)·ρx·ρu" in rep.reason and "整群设计效应" not in rep.reason
    assert rep.weight_design_effect == 1.0 and rep.weight_column == ""


def _power_run(tmp_path: Path) -> tuple[Path, pd.DataFrame]:
    from oss.api import profile as profile_api
    run = tmp_path / "r"
    run.mkdir()
    df = _cgss_like()
    df.to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    return run, df


def test_oss_power按画像的权重列算权重设计效应(tmp_path: Path) -> None:
    from oss.api import power as power_api
    run, df = _power_run(tmp_path)
    out = power_api.power(str(run), idea="i", effect=0.2, effect_source="测试")
    w = df.weight
    assert out["data"]["weight_design_effect"] == pytest.approx(len(w) * (w ** 2).sum() / w.sum() ** 2, rel=1e-4)
    assert out["data"]["weight_column"] == "weight"


def test_oss_power传空串就不按权重折_列不在数据里警告并按1算(tmp_path: Path) -> None:
    from oss.api import power as power_api
    run, _ = _power_run(tmp_path)
    off = power_api.power(str(run), idea="i", effect=0.2, effect_source="测试", weight_col="")
    assert off["data"]["weight_design_effect"] == 1.0 and off["data"]["weight_column"] == ""
    nope = power_api.power(str(run), idea="i", effect=0.2, effect_source="测试", weight_col="wt_nope")
    assert nope["data"]["weight_design_effect"] == 1.0 and nope["data"]["weight_column"] == ""
    assert any("wt_nope" in w for w in nope["data"]["warnings"]), nope["data"]["warnings"]


def test_oss_power命令行的weight_col(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from oss.cli.main import app
    run, _ = _power_run(tmp_path)
    base = ["power", "--run", str(run), "--idea", "i", "--effect-source", "测试"]
    r = CliRunner().invoke(app, [*base, "--weight-col", ""])
    assert r.exit_code == 0, r.output
    assert json.loads((run / "power.json").read_text(encoding="utf-8"))["weight_design_effect"] == 1.0
    r = CliRunner().invoke(app, base)
    assert r.exit_code == 0, r.output
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    assert rep["weight_design_effect"] > 1.0 and rep["weight_column"] == "weight"


def test_回归诊断里的设计效应改叫权重设计效应() -> None:
    res = run_ols_fe(_survey_frame(), AnalysisPlan(outcome="y", treatment="x", weights="w"))
    assert "权重设计效应（Kish）" in res.diagnostics["抽样权重"], res.diagnostics["抽样权重"]


# ══════════════════════════════ 审查第 4 批（1a941c3）的修补


def test_分批伴随估计的表与图照它自己的标准误口径_不写泰勒线性化(monkeypatch, tmp_path: Path) -> None:
    """主规格 did 声明了分层 + 抽样单位，主结果按设计算；伴随的 Callaway–Sant'Anna 只按抽样单位聚类
    （StatsPAI 的自助法，分层不进）。各批效应表、逐期表、逐期图照伴随估计自己的口径，不借主规格的表注与 t 临界值。"""
    pytest.importorskip("statspai")
    pytest.importorskip("matplotlib")
    import matplotlib.axes

    from oss.core import figures
    from oss.core.manuscript import table_markdown
    res = run_plan(_staggered_design_frame(), AnalysisPlan(plan_id="p", idea_id="i", estimator="did", outcome="y",
                                                           unit="unit", time="year", treat_time="treat_time",
                                                           survey=_DESIGN))
    assert any(k.startswith("cohort_") for k in res.estimates) and res.event_study
    main_zh, why = table_markdown("main", res, en=False)
    assert main_zh is not None and "泰勒线性化" in main_zh and res.spec.t_crit > 1.96, why   # 主规格真按设计算了
    for name in ("cohorts", "event"):
        zh, why = table_markdown(name, res, en=False)
        assert zh is not None, why
        assert "泰勒线性化" not in zh and "按初级抽样单位 county 聚类" in zh and "分层 prov 没进方差" in zh, zh
        en, why = table_markdown(name, res, en=True)
        assert en is not None, why
        assert "Taylor" not in en and "clustered by PSU county" in en, en

    yerr: list[float] = []
    real = matplotlib.axes.Axes.errorbar

    def spy(self, x, y, *args, **kw):
        yerr.append(kw["yerr"][0])
        return real(self, x, y, *args, **kw)

    monkeypatch.setattr(matplotlib.axes.Axes, "errorbar", spy)
    figures.event(res, tmp_path / "ev.png", lang="zh")
    assert yerr == pytest.approx([1.96 * p.se for p in sorted(res.event_study, key=lambda p: p.k)], rel=1e-9)


def test_逐期权重在单位内会变时_伴随估计不加权照跑CS_不退回TWFE() -> None:
    """CFPS 那种逐轮权重在单位内会变，StatsPAI 吃不了；退回 TWFE 事件研究正是分批实施下最该避免的。"""
    pytest.importorskip("statspai")
    df = pd.read_csv(FIX / "did_staggered.csv")
    df["w"] = np.random.default_rng(22).uniform(0.5, 2.0, len(df))            # 每行不同
    base = dict(plan_id="p", idea_id="i", outcome="y", unit="unit", time="year", treat_time="treat_time")
    res = run_plan(df, AnalysisPlan(**base, estimator="did", weights="w"))
    note = res.diagnostics["逐年展开"]
    assert "Callaway–Sant'Anna" in note and "退回 TWFE" not in note, note
    assert "未加权" in note and "单位内" in note and "prep.py" in note, note
    assert "w" in res.diagnostics["抽样权重"]                                   # 主规格照样加权
    plain = run_plan(df, AnalysisPlan(**base, estimator="did_cs"))
    for k in ("cohort_2006", "cohort_2012"):
        assert res.estimates[k].coef == pytest.approx(plain.estimates[k].coef, rel=1e-12)


def test_伴随估计退回TWFE时理由只写问题本身_不贴拒绝的原文() -> None:
    pytest.importorskip("statspai")
    df = pd.read_csv(FIX / "did_staggered.csv")
    df["county"] = np.arange(len(df)) % 30                                    # 抽样单位在单位内会变，StatsPAI 聚类不了
    res = run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", estimator="did", outcome="y", unit="unit",
                                    time="year", treat_time="treat_time",
                                    survey=SurveyDesign(psu="county", weight_use="model", design_source="测试")))
    note = res.diagnostics["逐年展开"]
    assert "退回 TWFE" in note and "只落在一个簇里" in note, note
    assert "分析计划过不了校验" not in note and "PlanRejected" not in note, note


def test_工具变量带固定效应时按抽样单位算与按它聚类同一个标准误() -> None:
    df = _iv_frame(seed=21, n=1200)
    df["fe"] = np.repeat(np.arange(300), 4)
    df["county"] = np.repeat(np.arange(60), 20)
    base = dict(plan_id="p", idea_id="i", estimator="iv", outcome="y", treatment="x", instruments=["z"],
                controls=["c"], fixed_effects=["fe"])
    design = run_plan(df, AnalysisPlan(**base, survey=SurveyDesign(psu="county", weight_use="model",
                                                                   design_source="测试")))
    cluster = run_plan(df, AnalysisPlan(**base, cluster_level="county"))
    assert design.estimates["main"].se == pytest.approx(cluster.estimates["main"].se, rel=1e-12)


@pytest.mark.parametrize("fe", [False, True])
def test_加权工具变量的稳健与聚类标准误跟linearmodels一致(fe: bool) -> None:
    """linearmodels 的 debiased 口径：稳健乘 n/(n−k)，聚类乘 G/(G−1)·(n−1)/(n−k)；k 算上固定效应的哑变量与截距。"""
    lm = pytest.importorskip("linearmodels.iv")
    df = _iv_frame()
    df["g"] = np.repeat(np.arange(40), len(df) // 40)
    df["f"] = np.tile(np.arange(5), len(df) // 5)
    base = dict(plan_id="p", idea_id="i", estimator="iv", outcome="y", treatment="x", instruments=["z"],
                controls=["c"], weights="wt", fixed_effects=["f"] if fe else [])
    exog = pd.DataFrame({"const": 1.0, "c": df.c})
    if fe:
        exog = pd.concat([exog, pd.get_dummies(df.f, prefix="f", drop_first=True, dtype=float)], axis=1)
    mod = lm.IV2SLS(df.y, exog, df[["x"]], df[["z"]], weights=df.wt)
    robust = run_plan(df, AnalysisPlan(**base)).estimates["main"].se
    clustered = run_plan(df, AnalysisPlan(**base, cluster_level="g")).estimates["main"].se
    assert robust == pytest.approx(mod.fit(cov_type="robust", debiased=True).std_errors["x"], rel=1e-6)
    assert clustered == pytest.approx(
        mod.fit(cov_type="clustered", clusters=df.g, debiased=True).std_errors["x"], rel=1e-6)


def _rdd_coarse_design_frame(seed: int = 23, n: int = 1600) -> pd.DataFrame:
    """抽样单位是分数上每 5 分一段、两段一层：带宽砍到一半时窗口里每层只剩一个抽样单位（G = H）。"""
    rng = np.random.default_rng(seed)
    score = rng.uniform(40, 80, n)
    df = pd.DataFrame({"score": score, "w": rng.uniform(0.5, 2.0, n), "c": rng.normal(size=n)})
    df["county"] = ((score - 40) // 5).astype(int)
    df["prov"] = df.county // 2
    df["y"] = 4.0 * (score >= 60) + 0.1 * (score - 60) + 0.2 * df.c + rng.normal(size=n)
    return df


def test_带宽敏感性只带权重不带抽样设计_窄窗口不会因为自由度不够被悄悄丢掉() -> None:
    res = run_plan(_rdd_coarse_design_frame(), AnalysisPlan(plan_id="p", idea_id="i", estimator="rdd", outcome="y",
                                                            running="score", cutoff=60.0, bandwidth=10.0,
                                                            weights="w", survey=_DESIGN))
    text = res.diagnostics["带宽敏感性"]
    assert all(f"带宽 {h:g} →" in text for h in (5, 7.5, 10, 15, 20)), text


def test_断点的协变量连续性与带宽敏感性都按权重算_跟窗口内的WLS一致() -> None:
    sm = pytest.importorskip("statsmodels.api")
    df = _rdd_coarse_design_frame()
    res = run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", estimator="rdd", outcome="y", running="score",
                                    cutoff=60.0, bandwidth=10.0, controls=["c"], weights="w"))

    def wls(outcome: str, h: float, controls: list[str]) -> float:
        win = df[(df.score - 60).abs() <= h]
        r, p = win.score - 60, (win.score >= 60) * 1.0
        X = sm.add_constant(pd.DataFrame({"p": p, "r": r, "rx": r * p, **{c: win[c] for c in controls}}))
        return float(sm.WLS(win[outcome], X, weights=win.w).fit().params["p"])

    jump = res.diagnostics["协变量连续性"]
    assert f"c 跳跃 {wls('c', 10.0, []):+.3f}" in jump, jump
    text = res.diagnostics["带宽敏感性"]
    for h in (5, 7.5, 10, 15, 20):
        assert f"带宽 {h:g} → {wls('y', h, ['c']):+.3f}" in text, text


def test_分批双重差分的分层在单位内会变就拒_点名分层那一列() -> None:
    pytest.importorskip("statspai")
    df = _staggered_design_frame()
    df["prov"] = df.county // 2 + (df.year >= 2010) * 100                  # 抽样单位在单位内不变，分层会变
    with pytest.raises(PlanRejected, match="`prov` 在"):
        run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", estimator="did_cs", outcome="y", unit="unit",
                                  time="year", treat_time="treat_time", survey=_DESIGN))


def test_分批双重差分加权跟直接调StatsPAI一致() -> None:
    sp = pytest.importorskip("statspai")
    df = pd.read_csv(FIX / "did_staggered.csv")
    per_unit = {u: 0.5 + (i % 4) for i, u in enumerate(sorted(df.unit.unique()))}
    df["w"] = df.unit.map(per_unit)
    res = run_plan(df, AnalysisPlan(plan_id="p", idea_id="i", estimator="did_cs", outcome="y", unit="unit",
                                    time="year", treat_time="treat_time", weights="w"))
    work = df.assign(_g=pd.to_numeric(df.treat_time, errors="coerce").fillna(0), _t=df.year)
    r = sp.callaway_santanna(work, y="y", g="_g", t="_t", i="unit", x=None, weights="w",
                             control_group="nevertreated", clustervars=None, bstrap=False,
                             allow_unbalanced_panel=True, base_period="universal", pretest="joint",
                             pretest_periods=4)
    want = sp.aggte(r, type="simple", cband=False, bstrap=False)
    m = res.estimates["main"]
    assert m.coef == pytest.approx(float(want.estimate), rel=1e-12) and m.se == pytest.approx(float(want.se), rel=1e-12)
    assert m.coef == pytest.approx(3.4230650203, rel=1e-9) and m.se == pytest.approx(0.2303804365, rel=1e-9)
