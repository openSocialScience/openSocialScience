"""描述骨架（设计稿 docs/design/2026-09-15-descriptive-skeleton.md）。按实施计划的任务分节。"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

FIX = Path(__file__).parent / "fixtures"


# ══════════════════════════════ Task 1：契约

from oss.core.contracts import (  # noqa: E402
    AnalysisPlan, AnalysisResults, Benchmark, DescriptionSpec, GroupCell, PowerReport,
)


def test_老计划没有description照样读得进(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"plan_id": "p", "idea_id": "i", "outcome": "y", "treatment": "x"}), encoding="utf-8")
    assert AnalysisPlan.load(p).description is None


def test_描述块不认得的键直接拒() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DescriptionSpec(group=["urban"])                  # 少了一个 s：照抄错的键不许悄悄变成空描述块
    with pytest.raises(ValidationError):
        DescriptionSpec(kind="ranking")                   # 只认五类


def test_外部基准要写出处() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Benchmark(quantity="城镇就业人员周平均工作时间", value=46.3)
    b = Benchmark(quantity="城镇就业人员周平均工作时间", value=46.3, unit="小时", source="国家统计局《中国劳动统计年鉴 2018》表 1-50")
    assert b.source


def test_功效报告缺省是功效模式_分组表缺省是空() -> None:
    assert PowerReport.model_fields["mode"].default == "power"
    r = AnalysisResults(plan_id="p")
    assert r.groups == [] and r.quantiles == {}


def test_分组表能存能读(tmp_path: Path) -> None:
    r = AnalysisResults(plan_id="p", groups=[GroupCell(label="urban=0", n=10, mean=0.2, se=0.05, ci_lo=0.1, ci_hi=0.3,
                                                        reference=True)])
    r.save(tmp_path / "results.json")
    assert AnalysisResults.load(tmp_path / "results.json").groups[0].label == "urban=0"


# ══════════════════════════════ Task 2：精度代替功效

from oss.core import power as power_mod  # noqa: E402


def test_没给分组时精度是总体水平的区间半宽() -> None:
    half, eff, cell = power_mod.precision(400)
    assert half == pytest.approx(1.959964 / 20, rel=1e-5) and eff == pytest.approx(400) and cell == 0.0


def test_给了分组时精度是排序第二类对参照组之差_最小分组单独报() -> None:
    half, eff, cell = power_mod.precision(500, group_sizes=[300, 100, 100])
    assert half == pytest.approx(1.959964 * math.sqrt(1 / 300 + 1 / 100), rel=1e-5)
    assert cell == pytest.approx(100)


def test_权重设计效应让精度变宽() -> None:
    assert power_mod.precision(400, weight_deff=2.0)[0] == pytest.approx(power_mod.precision(400)[0] * math.sqrt(2))


@pytest.mark.parametrize("d,verdict", [(0.30, "pass"), (0.12, "gray"), (0.08, "fail")])
def test_精度判词按区间半宽跟有意义差别之比(d: float, verdict: str) -> None:
    rep = power_mod.analyze_precision("i", d, 400, effect_size_source="测试")          # 半宽 0.098
    assert rep.mode == "precision" and rep.verdict == verdict
    assert rep.precision_halfwidth == pytest.approx(0.098, abs=1e-3)


def test_有意义差别没出处_判词封顶灰区() -> None:
    assert power_mod.analyze_precision("i", 0.30, 400).verdict == "gray"


def test_描述类题目的oss_power走精度(tmp_path: Path) -> None:
    from oss.api import power as power_api
    from oss.api import profile as profile_api
    from oss.core.contracts import Idea, IdeaSet
    run = tmp_path / "r"
    run.mkdir()
    df = pd.DataFrame({"y": np.random.default_rng(1).normal(size=300), "urban": [0] * 200 + [1] * 100})
    df.to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    IdeaSet(ideas=[Idea(idea_id="i1", title="T", research_question="Q",
                        research_type="descriptive")]).save(run / "ideas.json")
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试", group_col="urban")
    assert out["data"]["mode"] == "precision" and out["data"]["min_cell_label"] == "urban=1"


def _desc_run(tmp_path: Path, research_type: str = "descriptive") -> Path:
    from oss.api import profile as profile_api
    from oss.core.contracts import Idea, IdeaSet
    run = tmp_path / "r"
    run.mkdir()
    df = pd.DataFrame({"y": np.random.default_rng(1).normal(size=300), "urban": [0] * 200 + [1] * 100,
                       "east": [0, 1] * 150})
    df.to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    IdeaSet(ideas=[Idea(idea_id="i1", title="T", research_question="Q",
                        research_type=research_type)]).save(run / "ideas.json")
    return run


def test_计划写了研究类型就按计划_不是描述类照旧算功效(tmp_path: Path) -> None:
    from oss.api import power as power_api
    run = _desc_run(tmp_path, research_type="causal")
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试", group_col="urban")
    assert out["data"]["mode"] == "power" and out["data"]["power"] > 0
    assert any("--group-col" in w for w in out["data"]["warnings"])     # 因果题给了分组列：说一声没用上
    (run / "plan.json").write_text(json.dumps({"plan_id": "p", "idea_id": "i1", "research_type": "descriptive"}),
                                   encoding="utf-8")                     # 因果降为描述只改计划
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    assert out["data"]["mode"] == "precision" and out["data"]["min_cell_label"] == ""


def test_分组列交叉数格_至多两列(tmp_path: Path) -> None:
    from oss.api import errors
    from oss.api import power as power_api
    run = _desc_run(tmp_path)
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试", group_col="urban, east")
    assert out["data"]["min_cell_label"] == "urban=1 × east=0" and out["data"]["min_cell_n_eff"] == pytest.approx(50)
    with pytest.raises(errors.BadInput):
        power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试", group_col="urban,east,y")


def test_oss_power命令行的group_col(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from oss.cli.main import app
    run = _desc_run(tmp_path)
    r = CliRunner().invoke(app, ["power", "--run", str(run), "--idea", "i1", "--effect", "0.3",
                                 "--effect-source", "测试", "--group-col", "urban"])
    assert r.exit_code == 0, r.output
    assert "精度 ±" in r.output and "功效 0.00" not in r.output and "urban=1" in r.output


# ══════════════════════════════ Task 3：两道门

from oss.core.analysis import validate_plan  # noqa: E402
from oss.core.contracts import Estimand  # noqa: E402
from oss.core.profiler import profile_file  # noqa: E402

EST = Estimand(unit_quantity="每周工作 55 小时以上的比例，城乡之差", target_population="2017 年中国 18–60 岁在业人口",
               assumptions=["抽样框覆盖目标总体"], link="按权重加权、按区县整群")


def _desc_frame(n: int = 600, seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"year": np.repeat([2010, 2012, 2014], n // 3), "rid": np.arange(n),
                         "urban": rng.integers(0, 2, n), "y": rng.normal(size=n)})


@pytest.fixture
def rcs_prof(tmp_path: Path):
    p = tmp_path / "rcs.csv"
    _desc_frame().to_csv(p, index=False)
    prof = profile_file(p)
    assert prof.structure.kind == "repeated_cross_section", prof.structure.kind   # 夹具本身先要判成重复截面
    return prof


def _dplan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", research_type="descriptive", estimator="describe", outcome="y",
                estimand=EST, description=DescriptionSpec(kind="gap", groups=["urban"]))
    return AnalysisPlan(**{**base, **kw})


def _dprobs(plan: AnalysisPlan, prof) -> list[str]:
    problems, _ = validate_plan(plan, prof)
    return [p for p in problems if "描述" in p or "description" in p or "重复截面" in p]


def test_描述类计划没写描述量与目标总体就拒(rcs_prof) -> None:
    assert any("描述量" in p for p in _dprobs(_dplan(estimand=None), rcs_prof))


def test_describe要写description的kind(rcs_prof) -> None:
    assert any("description.kind" in p for p in _dprobs(_dplan(description=None), rcs_prof))


@pytest.mark.parametrize("desc,needle", [
    (DescriptionSpec(kind="gap"), "groups"),
    (DescriptionSpec(kind="trend"), "over"),
    (DescriptionSpec(kind="association"), "ols_fe"),
    (DescriptionSpec(kind="gap", groups=["rural"]), "不在数据里"),
])
def test_描述块写得不对就拒(rcs_prof, desc, needle: str) -> None:
    assert any(needle in p for p in _dprobs(_dplan(description=desc), rcs_prof)), needle


def test_个体变化配重复截面就拒(rcs_prof) -> None:
    desc = DescriptionSpec(kind="trend", over="year", change="individual")
    assert any("重复截面" in p for p in _dprobs(_dplan(description=desc), rcs_prof))


def test_因果降级来的描述计划照旧能过(rcs_prof) -> None:
    plan = _dplan(estimator="ols_fe", treatment="urban", description=None)
    assert _dprobs(plan, rcs_prof) == []


def test_没写研究类型的老计划不查(rcs_prof) -> None:
    assert _dprobs(_dplan(research_type="", estimand=None, description=None), rcs_prof) == []


def test_describe不下因果结论(rcs_prof) -> None:
    assert any("describe" in p for p in validate_plan(_dplan(research_type="causal"), rcs_prof)[0])


# ══════════════════════════════ Task 4：describe 估计量

from oss.core import causal_stats as cs  # noqa: E402
from oss.core.analysis import run_plan  # noqa: E402
from oss.core.contracts import SurveyDesign  # noqa: E402


def _gap_frame(n: int = 800, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    county = np.repeat(np.arange(40), n // 40)
    urban = (rng.random(n) < 0.4).astype(int)
    return pd.DataFrame({"y": 0.3 + 0.1 * urban + rng.normal(scale=0.5, size=n), "urban": urban,
                         "year": np.tile([2010, 2012, 2014, 2016], n // 4), "county": county, "prov": county // 4,
                         "w": rng.uniform(0.3, 3.0, n)})


def _describe(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", research_type="descriptive", estimator="describe", outcome="y",
                description=DescriptionSpec(kind="gap", groups=["urban"]))
    return AnalysisPlan(**{**base, **kw})


def test_不加权的分组均值跟pandas一致() -> None:
    df = _gap_frame()
    res = run_plan(df, _describe())
    g = {c.label: c for c in res.groups}
    want = df.groupby("urban").y.mean()
    assert g["urban=0"].reference and g["urban=0"].mean == pytest.approx(want[0])
    assert g["urban=1"].mean == pytest.approx(want[1]) and g["urban=1"].gap == pytest.approx(want[1] - want[0])
    assert res.estimates["main"].coef == pytest.approx(want[1] - want[0])


def test_加权的均值与组间差跟加权最小二乘一致() -> None:
    sm = pytest.importorskip("statsmodels.api")
    df = _gap_frame()
    res = run_plan(df, _describe(weights="w"))
    g = {c.label: c for c in res.groups}
    assert g["urban=1"].mean == pytest.approx(np.average(df.y[df.urban == 1], weights=df.w[df.urban == 1]))
    fit = sm.WLS(df.y, sm.add_constant(df.urban.astype(float)), weights=df.w).fit(cov_type="HC1")
    assert g["urban=1"].gap == pytest.approx(fit.params["urban"], rel=1e-8)
    assert g["urban=1"].gap_se == pytest.approx(fit.bse["urban"], rel=1e-6)


def test_按设计的分组区间用t分布() -> None:
    df = _gap_frame()
    res = run_plan(df, _describe(weights="w", survey=SurveyDesign(strata="prov", psu="county", weight_use="descriptive",
                                                                  design_source="测试")))
    c = next(x for x in res.groups if x.label == "urban=1")
    dof = int(res.diagnostics["抽样设计自由度"])
    assert c.ci_hi - c.mean == pytest.approx(cs.t_ppf(0.975, dof) * c.se, rel=1e-6)


def test_水平只有一格() -> None:
    df = _gap_frame()
    res = run_plan(df, _describe(description=DescriptionSpec(kind="level")))
    assert [c.label for c in res.groups] == ["全样本"] and res.estimates["main"].coef == pytest.approx(df.y.mean())


def test_趋势逐期加一条斜率() -> None:
    df = _gap_frame()
    res = run_plan(df, _describe(description=DescriptionSpec(kind="trend", over="year")))
    assert [c.label for c in res.groups] == ["year=2010", "year=2012", "year=2014", "year=2016"]
    assert res.estimates["main"].coef == pytest.approx(np.polyfit(df.year, df.y, 1)[0], rel=1e-6)


def test_分布给加权分位数() -> None:
    df = _gap_frame()
    res = run_plan(df, _describe(description=DescriptionSpec(kind="distribution")))
    assert res.quantiles["p50"] == pytest.approx(np.quantile(df.y, 0.5, method="inverted_cdf"))


def test_describe拿到权重照样跑() -> None:
    from oss.core.contracts import WEIGHTED_ESTIMATORS
    assert "describe" in WEIGHTED_ESTIMATORS


# ══════════════════════════════ Task 5：分组描述表


def test_分组描述表中英都排得出来() -> None:
    from oss.core.manuscript import table_markdown
    res = run_plan(_gap_frame(), _describe(weights="w"))
    zh, err = table_markdown("groups", res, en=False)
    assert not err and "参照" in zh and "urban=1" in zh and "加权" in zh
    en, err = table_markdown("groups", res, en=True)
    assert not err and "ref." in en and "95% CI" in en


def test_分布只有分位数时也排得出来() -> None:
    from oss.core.manuscript import table_markdown
    res = run_plan(_gap_frame(), _describe(description=DescriptionSpec(kind="distribution")))
    zh, err = table_markdown("groups", res, en=False)
    assert not err and "p50" in zh


# ══════════════════════════════ Task 5b：论文检查——描述类精度判灰区或不足，局限写「精度」就算交代


def _check_limits(limits: str, **power_kw) -> list[str]:
    """跟 tests/test_claim_check.py 同一副骨架：五节齐、结果句只用占位符，只看「局限」写没写。"""
    from oss.core.contracts import Estimate
    from oss.core.manuscript import check
    paper = ("# 标题\n## 摘要\n概要。\n## 引言\n背景。\n## 数据与方法\n方法。\n"
             "## 结果\n城乡之差为 {{result:main.coef}}（p {{result:main.p}}）。\n"
             f"## 讨论\n### 局限\n{limits}\n")
    res = AnalysisResults(plan_id="p", n=800, estimates={"main": Estimate(term="urban=1", coef=0.084, se=0.044, p=0.068)})
    problems, _ = check(paper, res, PowerReport(**{"idea_id": "i", "gray_band": (0.7, 1.0), **power_kw}))
    return problems


@pytest.mark.parametrize("limits", [
    "精度落在灰区：这份数据分不出小于十分之一个标准差的城乡之差。",
    "Precision is limited: differences below a tenth of a standard deviation cannot be resolved.",
    "功效与精度都有限。",
])
def test_精度模式灰区_局限写了精度就算交代(limits: str) -> None:
    # 断言整张清单为空，不只是「没有这一条」：别的拒绝（比如裸小数）会让 check 跳过主张核验，这一条就空过了
    problems = _check_limits(limits, mode="precision", verdict="gray", precision_halfwidth=0.1)
    assert problems == [], problems


def test_精度模式不足_局限一个字没提就拒_话说的是精度() -> None:
    problems = _check_limits("识别策略有边界。", mode="precision", verdict="fail", precision_halfwidth=0.2)
    assert len(problems) == 1, problems
    assert "精度" in problems[0] and "局限里一个字没提精度" in problems[0] and "功效" not in problems[0]


def test_功效模式照旧只认功效_话一个字不变() -> None:
    problems = _check_limits("精度落在灰区：分不出小的差别。", verdict="gray", power=0.6)
    assert problems == ["功效判词是 **gray**，但局限里一个字没提功效。**判死或落灰区都必须写进局限**——这是审稿人最先看的"]


# ══════════════════════════════ 批 2 审查修补

from oss.core.analysis import PlanRejected  # noqa: E402
from oss.core.contracts import BinnedSpec, RobustnessSpec  # noqa: E402

# ── ① describe 只做不带控制的分组描述：处理、控制、固定效应、交互、借力变量、驱动变量、分箱一律拒。
#    原先门不拦、run_describe 一个都不读——稳健性加了控制，原样照抄主结果，论文读起来就是「稳健性过了」


@pytest.mark.parametrize("kw", [
    dict(treatment="urban"), dict(controls=["year"]), dict(fixed_effects=["year"]),
    dict(interactions=["urban*year"]), dict(instruments=["year"]), dict(running="year"),
    dict(binned=BinnedSpec(by="year")),
])
def test_describe带控制或固定效应就拒(rcs_prof, kw) -> None:
    probs = validate_plan(_dplan(**kw), rcs_prof)[0]
    assert any("不带控制的分组描述" in p and "ols_fe" in p for p in probs), probs


def test_测量类的describe带控制也拒(rcs_prof) -> None:
    probs = validate_plan(_dplan(research_type="measurement", controls=["year"]), rcs_prof)[0]
    assert any("不带控制的分组描述" in p for p in probs), probs


def test_describe运行时带控制也拒() -> None:
    with pytest.raises(PlanRejected, match="不带控制的分组描述"):
        run_plan(_gap_frame(), _describe(controls=["year"], fixed_effects=["prov"]))


def test_稳健性加控制记为出错不照抄主结果_换结局换权重换筛选照跑() -> None:
    df = _gap_frame().assign(y2=lambda d: d.y * 2)
    res = run_plan(df, _describe(robustness=[
        RobustnessSpec(label="C", controls=["year"]), RobustnessSpec(label="FE", fixed_effects=["prov"]),
        RobustnessSpec(label="O", outcome="y2"), RobustnessSpec(label="W", weights="w"),
        RobustnessSpec(label="F", sample_filter="year >= 2012")]))
    for lb in ("C", "FE"):
        assert "不带控制的分组描述" in res.robustness[lb].error and not res.robustness[lb].estimates, lb
    for lb in ("O", "W", "F"):
        rb = res.robustness[lb]
        assert not rb.error and rb.estimates["main"].coef != res.estimates["main"].coef, lb
    assert res.robustness["O"].estimates["main"].coef == pytest.approx(2 * res.estimates["main"].coef)


# ── ② 文字分组列（east / west）：样本记录、样本构建诊断、表 1 的 N、分组表的合计四处对得上。
#    原先按数值转换判缺失，文字列整列变缺失：样本记录报估计样本 0 行，表 1 不按分组列去缺失


@pytest.mark.parametrize("with_missing", [False, True])
def test_文字分组列_样本记录与表1与分组表的N对得上(with_missing: bool) -> None:
    df = _gap_frame()
    df["region"] = pd.Series(np.where(df.urban == 1, "east", "west"), dtype=object)
    if with_missing:
        df.loc[:30, "region"] = None                      # 31 行分组列缺失
    res = run_plan(df, _describe(description=DescriptionSpec(kind="gap", groups=["region"])))
    n = sum(c.n for c in res.groups)
    assert n == res.n == (len(df) - 31 if with_missing else len(df))
    assert res.sample_log[-1].rows == n, res.sample_log
    assert f"估计样本：{n:,} 行" in res.diagnostics["样本构建"], res.diagnostics["样本构建"]
    assert {d.name: d.n for d in res.descriptives}["y"] == n
    assert "region" not in {d.name for d in res.descriptives}          # 文字列照旧不进表 1


# ── ⑦ HC1 不建 n×n 矩阵：原先 np.diag(resid²) 在 2 万行时一次要 3 GB，不聚类的 ols_fe 同样中招


def _big_frame(n: int = 20_000) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    return pd.DataFrame({"y": rng.normal(size=n), "x": rng.normal(size=n), "c": rng.normal(size=n),
                         "g": rng.integers(0, 3, n)})


@pytest.mark.parametrize("plan", [
    _describe(description=DescriptionSpec(kind="gap", groups=["g"])),
    AnalysisPlan(plan_id="p", idea_id="i", outcome="y", treatment="x", controls=["c"]),
], ids=["describe", "ols_fe"])
def test_HC1不建n乘n矩阵(plan: AnalysisPlan) -> None:
    import tracemalloc
    df = _big_frame()
    tracemalloc.start()
    try:
        run_plan(df, plan)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 100 * 2 ** 20, f"峰值 {peak / 2 ** 20:.0f} MiB"


# ── ③ 整格落在一个抽样单位里：按设计的方差是假的 0（原先 se≈6e-17、区间缩成均值那一点）。
#    不给区间与组间差、有效样本量记所跨的抽样单位数、诊断点名；主结果要用它就拒

DES = SurveyDesign(strata="prov", psu="county", weight_use="descriptive", design_source="测试")


def _one_psu_frame(value: float = 2) -> pd.DataFrame:
    df = _gap_frame().astype({"urban": float})
    df.loc[df.county == 7, "urban"] = value            # 这一格只出现在抽样单位 7 里（20 行）
    return df


def test_整格落在一个抽样单位里_不给区间_有效样本量记抽样单位数() -> None:
    from oss.core.manuscript import table_markdown
    df = _one_psu_frame()
    res = run_plan(df, _describe(weights="w", survey=DES))
    c = next(x for x in res.groups if x.label == "urban=2")
    m = df.urban == 2
    assert c.mean == pytest.approx(np.average(df.y[m], weights=df.w[m]))              # 均值照报
    assert (c.se, c.ci_lo, c.ci_hi, c.gap, c.gap_se, c.gap_p) == (None,) * 6
    assert c.n == 20 and c.n_eff == 1
    assert "urban=2" in res.diagnostics["区间留空的分组"]
    ok = next(x for x in res.groups if x.label == "urban=1")
    assert ok.se > 0 and ok.gap is not None                                            # 别的格照常
    zh, err = table_markdown("groups", res, en=False)
    row = next(line for line in zh.splitlines() if line.startswith("| urban=2 "))
    assert not err and row.count("—") == 3 and "None" not in zh, row


@pytest.mark.parametrize("value,label", [(-1, "urban=-1"), (0.5, "urban=0.5")])
def test_主结果要用的格落在一个抽样单位里就拒(value: float, label: str) -> None:
    # -1：排序后第一格（参照）；0.5：排序后第二格——组间差的主结果两格都要用
    with pytest.raises(PlanRejected, match="整格落在一个初级抽样单位里") as e:
        run_plan(_one_psu_frame(value), _describe(weights="w", survey=DES))
    assert label in str(e.value) and "prep.py" in str(e.value)


def test_参照格落在一个抽样单位里_各组的差全空() -> None:
    res = run_plan(_one_psu_frame(-1), _describe(weights="w", survey=DES,
                                                 description=DescriptionSpec(kind="level", groups=["urban"])))
    ref = res.groups[0]
    assert ref.reference and ref.label == "urban=-1" and ref.se is None
    assert all(c.gap is None and c.gap_se is None and c.gap_p is None for c in res.groups)
    assert all(c.se is not None for c in res.groups[1:])
    assert res.estimates["main"].se > 0                            # 水平的主结果是全样本均值，照算


def test_只写聚类层时按簇数判() -> None:
    res = run_plan(_one_psu_frame(), _describe(cluster_level="county"))
    c = next(x for x in res.groups if x.label == "urban=2")
    assert c.se is None and c.ci_lo is None and c.n_eff == 1
    assert "聚类单位" in res.diagnostics["区间留空的分组"]


def test_不按设计不聚类时_一格只有一行也不给区间() -> None:
    df = _gap_frame()
    df.loc[0, "urban"] = 2                                  # HC1 下一行的格残差是 0，标准误同样是假的 0
    res = run_plan(df, _describe(description=DescriptionSpec(kind="level", groups=["urban"])))
    c = next(x for x in res.groups if x.label == "urban=2")
    assert c.n == 1 and c.se is None and c.n_eff == 1


# ── ④ 0/1 结局的比例区间按 logit 尺度（同 Stata svy: proportion）：Wald 区间会出 [0, 1]


def _expit(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def test_0_1结局的比例区间按logit尺度_不出0到1() -> None:
    from oss.core.manuscript import table_markdown
    df = _gap_frame().assign(rare=(np.random.default_rng(11).random(800) < 0.01).astype(int))
    res = run_plan(df, _describe(outcome="rare", weights="w", survey=DES))
    crit = cs.t_ppf(0.975, int(res.diagnostics["抽样设计自由度"]))
    inner = [c for c in res.groups if 0 < c.mean < 1]
    assert inner and any(c.mean - crit * c.se < 0 for c in inner)          # 夹具本身要让 Wald 区间出界
    for c in inner:                                                          # 参照算法：logit 尺度上 ± t·se/(p(1−p))
        lg, sl = math.log(c.mean / (1 - c.mean)), c.se / (c.mean * (1 - c.mean))
        assert c.ci_lo == pytest.approx(_expit(lg - crit * sl), rel=1e-9)
        assert c.ci_hi == pytest.approx(_expit(lg + crit * sl), rel=1e-9)
        assert 0 < c.ci_lo < c.mean < c.ci_hi < 1 and c.proportion
    zh, _ = table_markdown("groups", res, en=False)
    assert "0/1 结局的比例区间按 logit 尺度算（同 Stata svy: proportion）" in zh
    en, err = table_markdown("groups", res, en=True)
    assert not err and "logit scale" in en
    cont = run_plan(df, _describe(weights="w", survey=DES))                  # 连续结局照旧 均值 ± t·se
    assert not any(c.proportion for c in cont.groups)
    assert "logit" not in table_markdown("groups", cont, en=False)[0]


def test_比例为0或1时区间是那一点_有效样本量退回Kish() -> None:
    df = _gap_frame()
    df["z"] = np.where(df.urban == 1, 0, (np.random.default_rng(4).random(800) < 0.3).astype(int))
    res = run_plan(df, _describe(outcome="z", weights="w", survey=DES))
    c = next(x for x in res.groups if x.label == "urban=1")
    m = df.urban == 1
    assert c.mean == pytest.approx(0, abs=1e-12) and (c.ci_lo, c.ci_hi) == (c.mean, c.mean)
    assert c.n_eff == pytest.approx(np.sum(df.w[m]) ** 2 / np.sum(df.w[m] ** 2))      # s² = 0：退回 Kish


# ── ⑨ 按设计或聚类时，分组的有效样本量 = 样本量 ÷ 设计效应（Kish 的 deff；NCHS 比例标准同一口径）。
#    原先只算权重那一截（Kish），环节 4 的最小分组却连整群一起算，两边拿同一个 50 比


def _clustered_frame() -> pd.DataFrame:
    df = _gap_frame()
    df["y"] = df.y + np.random.default_rng(8).normal(scale=0.6, size=40)[df.county.to_numpy()]   # 县内相关
    return df


def test_按设计时分组的有效样本量是样本量除以设计效应() -> None:
    df = _clustered_frame()
    res = run_plan(df, _describe(weights="w", survey=DES))
    for c, lab in zip(res.groups, (0, 1)):
        m = (df.urban == lab).to_numpy()
        yb = np.average(df.y[m], weights=df.w[m])
        s2 = np.average((df.y[m] - yb) ** 2, weights=df.w[m])
        assert c.n_eff == pytest.approx(min(c.n, s2 / c.se ** 2), rel=1e-9)
        assert c.n_eff < 0.5 * np.sum(df.w[m]) ** 2 / np.sum(df.w[m] ** 2)            # 比 Kish 小得多
    assert "分组的有效样本量按抽样设计算（样本量 ÷ 设计效应）" in res.diagnostics["分组有效样本量"]


def test_只聚类时有效样本量同样按设计效应() -> None:
    df = _clustered_frame()
    res = run_plan(df, _describe(cluster_level="county"))
    c, m = res.groups[1], (df.urban == 1).to_numpy()
    s2 = np.mean((df.y[m] - df.y[m].mean()) ** 2)
    assert c.n_eff == pytest.approx(min(c.n, s2 / c.se ** 2), rel=1e-9) and c.n_eff < 0.5 * c.n
    assert "聚类" in res.diagnostics["分组有效样本量"]


def test_不按设计不聚类时有效样本量照旧是Kish() -> None:
    df = _clustered_frame()
    res = run_plan(df, _describe(weights="w"))
    m = (df.urban == 1).to_numpy()
    assert res.groups[1].n_eff == pytest.approx(np.sum(df.w[m]) ** 2 / np.sum(df.w[m] ** 2))
    assert run_plan(df, _describe()).groups[1].n_eff == m.sum()
    assert "分组有效样本量" not in res.diagnostics


# ── ⑤ 英文稿的分组表有中文：多半是分组值（城镇 / 农村），叫在 prep.py 里重编码成英文——原先报成稳健性标签


def test_英文稿分组表有中文_说的是分组值要重编码() -> None:
    from oss.core.contracts import RobustnessRun
    from oss.core.manuscript import table_markdown
    res = AnalysisResults(plan_id="p", n=20, groups=[
        GroupCell(label="hukou=城镇", n=10, mean=0.2, se=0.05, ci_lo=0.1, ci_hi=0.3, reference=True),
        GroupCell(label="hukou=农村", n=10, mean=0.3, se=0.05, ci_lo=0.2, ci_hi=0.4, gap=0.1, gap_se=0.07, gap_p=0.15)])
    body, err = table_markdown("groups", res, en=True)
    assert body is None and "prep.py" in err and "分组" in err and "稳健性标签" not in err, err
    rob = AnalysisResults(plan_id="p", n=1, robustness={"换样本": RobustnessRun(label="换样本", error="x")})
    assert "稳健性标签" in table_markdown("robustness", rob, en=True)[1]          # 别的表照旧


# ── ⑥ 个体变化（change: individual）只认面板：画像判出结构、又不是面板就拒（原先只拦重复截面，截面照样放行）；
#    没判出结构（空或 unknown）不拒


@pytest.fixture
def cs_prof(tmp_path: Path):
    p = tmp_path / "cs.csv"
    rng = np.random.default_rng(1)
    pd.DataFrame({"rid": np.arange(300), "urban": rng.integers(0, 2, 300), "y": rng.normal(size=300)}).to_csv(p, index=False)
    prof = profile_file(p)
    assert prof.structure.kind == "cross_section", prof.structure.kind    # 夹具本身先要判成截面
    return prof


def test_个体变化配截面也拒(cs_prof) -> None:
    desc = DescriptionSpec(kind="gap", groups=["urban"], change="individual")
    probs = _dprobs(_dplan(description=desc), cs_prof)
    assert any("description.change: individual" in p and "数据却是截面" in p for p in probs), probs


def _with_kind(prof, kind: str):
    from oss.core.contracts import DataStructure
    return prof.model_copy(update={"structure": DataStructure().model_copy(update={"kind": kind})})


def test_个体变化配时间序列也拒(rcs_prof) -> None:
    desc = DescriptionSpec(kind="trend", over="year", change="individual")
    probs = _dprobs(_dplan(description=desc), _with_kind(rcs_prof, "time_series"))
    assert any("description.change: individual" in p and "时间序列" in p for p in probs), probs


@pytest.mark.parametrize("kind", ["", "unknown", "panel_balanced", "panel_unbalanced"])
def test_个体变化_面板或没判出结构时不拒(rcs_prof, kind: str) -> None:
    desc = DescriptionSpec(kind="trend", over="year", change="individual")
    assert not any("individual" in p for p in _dprobs(_dplan(description=desc), _with_kind(rcs_prof, kind)))


# ── ⑧ 描述论文（结果里有分组表）引了 {{table:groups}} 就算有主表：{{table:main}} 在描述论文里只有一行、
#    星号检验的是「均值 = 0」，是误导。导出、校验警告、合规规则三处一个口径；别的论文照旧要 {{table:main}}

_GROUPS_PAPER = ("# 标题\n## 摘要\n城乡之差为 {{result:main.coef}}。\n## 引言\n背景。\n## 数据与方法\n方法。\n"
                 "## 结果\n*表 1. 分组均值。*\n{{table:groups}}\n## 讨论\n### 局限\n精度有限。\n")


def test_描述论文引分组表_校验不再催主表() -> None:
    from oss.core.contracts import Estimate
    from oss.core.manuscript import check
    res = run_plan(_gap_frame(), _describe())
    assert not any("{{table:main}}" in w for w in check(_GROUPS_PAPER, res)[1])
    no_table = _GROUPS_PAPER.replace("{{table:groups}}", "见正文")
    assert any("{{table:groups}}" in w for w in check(no_table, res)[1])            # 两张都没有：催的是分组表
    other = AnalysisResults(plan_id="p", n=100, estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)})
    assert any("{{table:main}}" in w for w in check(_GROUPS_PAPER, other)[1])       # 非描述论文照旧要主表


def test_描述论文引分组表_合规规则的三张表算齐(tmp_path: Path) -> None:
    from oss.core import skillcheck as sc
    from oss.core.contracts import Estimate
    run_plan(_gap_frame(), _describe()).save(tmp_path / "results.json")
    body = "{{table:descriptives}}\n{{table:groups}}\n{{table:robustness}}\n"
    (tmp_path / "paper.md").write_text(body, encoding="utf-8")
    assert sc.r_tables(sc.RunView(tmp_path))[0] == sc.PASS
    (tmp_path / "paper.md").write_text(body.replace("{{table:groups}}", ""), encoding="utf-8")
    st, detail = sc.r_tables(sc.RunView(tmp_path))
    assert st == sc.FAIL and "{{table:groups}}" in detail, detail
    AnalysisResults(plan_id="p", n=1, estimates={"main": Estimate(term="x", coef=1, se=1, p=0.5)}).save(
        tmp_path / "results.json")
    (tmp_path / "paper.md").write_text(body, encoding="utf-8")
    st, detail = sc.r_tables(sc.RunView(tmp_path))
    assert st == sc.FAIL and "{{table:main}}" in detail, detail                     # 非描述论文照旧要主表


def _describe_run(tmp_path: Path) -> Path:
    from oss.api import analysis as analysis_api
    from oss.api import profile as profile_api
    run = tmp_path / "r"
    run.mkdir()
    _gap_frame()[["y", "urban"]].to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    (run / "ideas.json").write_text(json.dumps({"direction": "城乡之差", "ideas": []}, ensure_ascii=False),
                                    encoding="utf-8")
    AnalysisPlan(plan_id="p001", idea_id="i1", research_type="descriptive", estimator="describe", outcome="y",
                 estimand=EST, description=DescriptionSpec(kind="gap", groups=["urban"])).save(run / "plan.json")
    analysis_api.run(str(run))
    return run


def test_描述论文引分组表_导出不再要主表(tmp_path: Path) -> None:
    from oss.api import errors
    from oss.api import paper as paper_api
    run = _describe_run(tmp_path)
    (run / "paper_rendered.md").write_text("# T", encoding="utf-8")
    (run / "paper.md").write_text("# T\n没有表", encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        paper_api.export(str(run))
    assert e.value.code == 4 and "{{table:groups}}" in e.value.summary, e.value.summary
    (run / "paper.md").write_text("# T\n{{table:groups}}", encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        paper_api.export(str(run))
    assert e.value.code == 5, e.value.summary                  # 过了表那道门，停在「没审过」


# ══════════════════════════════ 批 F2：终审修补（M6 主要比较一个定义、M9 均值不打星号、M11 类别分组列）

# ── M6 主要比较那一对只有一个定义：run_describe 跟环节 4 的精度都用 `oss.core.power.first_two`（头两个有行的格）。
#    原先 describe 自己写 cells[0]、cells[1]，精度那边用 first_two，两处各写一套


def _three_frame(n: int = 900, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    edu = rng.choice([1, 2, 3], size=n, p=[0.5, 0.3, 0.2])
    return pd.DataFrame({"y": 0.2 * edu + rng.normal(scale=0.5, size=n), "edu": edu})


def test_主要比较那一对由first_two定_三格(monkeypatch) -> None:
    from oss.core import analysis as an
    real, seen = power_mod.first_two, []

    def spy(sizes):
        seen.append(real(sizes))
        return seen[-1]

    monkeypatch.setattr(an, "first_two", spy, raising=False)
    plan = _describe(description=DescriptionSpec(kind="gap", groups=["edu"]))
    res = run_plan(_three_frame(), plan)
    assert seen, "run_describe 没用 first_two 定主要比较那一对"
    ref, other = (res.groups[i] for i in seen[-1])
    assert [c.label for c in res.groups] == ["edu=1", "edu=2", "edu=3"] and ref.reference
    main = res.estimates["main"]
    assert main.term == other.label and main.coef == pytest.approx(other.mean - ref.mean)
    assert f"（{other.label}）" in res.diagnostics["主结果"] and f"（{ref.label}）" in res.diagnostics["主结果"]
    # first_two 换一对，主结果跟着换：两处只有一个定义
    monkeypatch.setattr(an, "first_two", lambda sizes: [0, 2])
    res2 = run_plan(_three_frame(), plan)
    assert res2.estimates["main"].term == "edu=3" and res2.groups[0].reference


# ── M9 水平与分布的主结果是全样本均值：{{table:main}} 原先照回归表打星号、报 p（「0.362***」），检验的是「均值 = 0」，
#    没有意义。改成报估计值、标准误与 95% 区间，不打星号、不报 p；{{result:main.p}} 解析不了。组差、趋势与别的估计量一个字不变

_P_PAPER = "# 标题\n## 结果\n均值为 {{result:main.coef}}（p {{result:main.p}}）。\n"


def _placeholder_problems(res) -> list[str]:
    from oss.core.manuscript import check
    return [p for p in check(_P_PAPER, res)[0] if p.startswith("占位符")]


@pytest.mark.parametrize("kind", ["level", "distribution"])
def test_水平与分布的均值_主表不打星号不报p_报区间(kind: str) -> None:
    from oss.core.manuscript import table_markdown
    res = run_plan(_gap_frame(), _describe(description=DescriptionSpec(kind=kind)))
    m, whole = res.estimates["main"], res.groups[0]                  # 没分组：分组表「全样本」那一格就是主结果
    row = f"| y | {m.coef:.3f} | {m.se:.3f} | [{whole.ci_lo:.3f}, {whole.ci_hi:.3f}] |"
    for en, head in ((False, "| 变量 | 估计值 | 标准误 | 95% 区间 |"), (True, "| Variable | Estimate | SE | 95% CI |")):
        body, err = table_markdown("main", res, en=en)
        assert not err and body.splitlines()[0] == head and body.splitlines()[2] == row, body
        assert "p < 0.10" not in body                                   # 没有星号，表注也不写星号约定
    probs = _placeholder_problems(res)
    assert len(probs) == 1 and "{{result:main.p}}" in probs[0] and "均值 = 0" in probs[0], probs


def test_水平分了组_主表区间按分组表的口径现算() -> None:
    from oss.core.manuscript import table_markdown
    df = _gap_frame()
    res = run_plan(df, _describe(description=DescriptionSpec(kind="level", groups=["urban"])))
    m = res.estimates["main"]
    assert f"| y | {m.coef:.3f} | {m.se:.3f} | [{m.coef - 1.96 * m.se:.3f}, {m.coef + 1.96 * m.se:.3f}] |" in (
        table_markdown("main", res, en=False)[0])
    # 0/1 结局、按设计：比例区间按 logit 尺度，临界值用设计自由度的 t（跟分组表同一口径）
    design = SurveyDesign(strata="prov", psu="county", weight_use="descriptive", design_source="测试")
    res = run_plan(df.assign(y=(df.y > 0.35).astype(int)),
                   _describe(description=DescriptionSpec(kind="level", groups=["urban"]), weights="w", survey=design))
    m, crit = res.estimates["main"], res.spec.t_crit
    lg, sl = math.log(m.coef / (1 - m.coef)), m.se / (m.coef * (1 - m.coef))
    body = table_markdown("main", res, en=False)[0]
    assert crit > 1.96 and f"[{_expit(lg - crit * sl):.3f}, {_expit(lg + crit * sl):.3f}]" in body, body
    assert "logit" in body and "p < 0.10" not in body


@pytest.mark.parametrize("desc, row", [
    (DescriptionSpec(kind="gap", groups=["urban"]), "| urban=1 | 0.087** | 0.035 | 0.012 |"),
    (DescriptionSpec(kind="trend", over="year"), "| year | 0.008 | 0.007 | 0.271 |"),
])
def test_组差与趋势照旧打星号报p(desc: DescriptionSpec, row: str) -> None:
    from oss.core.manuscript import table_markdown
    res = run_plan(_gap_frame(), _describe(description=desc))
    body = table_markdown("main", res, en=False)[0]
    assert body.splitlines()[0] == "| 变量 | 估计值 | 标准误 | p |" and body.splitlines()[2] == row, body
    assert "p < 0.10" in body and _placeholder_problems(res) == []


def test_ols_fe的主表与p一个字不变() -> None:
    from oss.core.manuscript import table_markdown
    res = run_plan(_gap_frame(), AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y",
                                              treatment="urban", cluster_level="county"))
    assert table_markdown("main", res, en=False)[0] == (
        "| 变量 | 估计值 | 标准误 | p |\n|---|---:|---:|---:|\n| urban | 0.087** | 0.038 | 0.021 |\n| 样本量 | 800 |  |  |\n\n"
        "*注：*OLS + 固定效应。 标准误按 county 聚类。 \\* p < 0.10，\\*\\* p < 0.05，\\*\\*\\* p < 0.01。")
    # 英文按社会学通例：标准误在下一行括号里、不设 p 列、档位 .05/.01/.001
    # （p = 0.021 在中文档位下是两颗星，在英文档位下是一颗）
    assert table_markdown("main", res, en=True)[0] == (
        "| Variable | Estimate |\n|---|---:|\n| urban | 0.087* |\n|  | (0.038) |\n| N | 800 |\n\n"
        "*Note:* OLS with fixed effects. Standard errors clustered by county. "
        "\\* p < .05, \\*\\* p < .01, \\*\\*\\* p < .001.")
    assert _placeholder_problems(res) == []


# ── M11 类别分组列：pandas 3 以前 groupby 缺省 observed=False，没用到的类也出一格，空格让矩阵奇异（LinAlgError）。
#    依赖写的是 pandas>=2.2，这里照 2.2 的缺省跑，免得测试只在新版 pandas 上绿


def test_类别分组列有没用到的类_只取出现过的类(monkeypatch) -> None:
    df = _gap_frame()
    df["urban"] = pd.Categorical(df["urban"].map({0: "rural", 1: "urban"}), categories=["rural", "urban", "abroad"])
    orig = pd.DataFrame.groupby
    monkeypatch.setattr(pd.DataFrame, "groupby", lambda self, *a, **kw: orig(self, *a, **{"observed": False, **kw}))
    res = run_plan(df, _describe())
    assert [c.label for c in res.groups] == ["urban=rural", "urban=urban"] and sum(c.n for c in res.groups) == res.n
    assert res.estimates["main"].term == "urban=urban"


# ── 批 2b 复审：按设计的分组有效样本量封顶在这一格自己的 Kish，不是行数。只落在两三个抽样单位里的格，标准误只靠
#    一两个自由度，碰巧很小时 s²/se² 冲到行数、比 Kish 还大，small_cells 就不开了（复审探针：2 个抽样单位、60 行、
#    组内相关 0.3，300 次里 28% 不开，2b 之前 100% 开）


def test_只落在两个抽样单位里的格_有效样本量封顶在这一格的Kish() -> None:
    from oss.core.analysis import _effective_n
    rng = np.random.default_rng(7)
    st, psu = np.repeat(np.arange(8), 30), np.repeat(np.arange(16), 15)       # 8 层 × 2 个抽样单位 × 15 行
    g = np.where(rng.random(240) < 0.5, "a", "b").astype(object)
    y, w = rng.normal(size=240), rng.lognormal(0, 0.7, 240)
    v, wv = rng.normal(size=15), rng.lognormal(0, 0.7, 15)
    for j, d in ((0, 0.001), (2, -0.001)):     # 格 c 落在第 0、2 个抽样单位（不同层），两处只差 ±0.001：标准误碰巧极小
        k = psu == j
        g[k], y[k], w[k] = "c", v + d, wv
    df = pd.DataFrame({"y": y, "g": g, "w": w, "st": st, "psu": psu})
    design = SurveyDesign(strata="st", psu="psu", weight_use="descriptive", design_source="测试")
    res = run_plan(df, _describe(description=DescriptionSpec(kind="level", groups=["g"]), weights="w", survey=design))
    c = next(x for x in res.groups if x.label == "g=c")
    kish = _effective_n(w[g == "c"])
    s2 = np.average((y[g == "c"] - c.mean) ** 2, weights=w[g == "c"])
    assert s2 / c.se ** 2 > c.n > kish, (s2 / c.se ** 2, c.n, kish)           # 前提：s²/se² 比行数还大，行数比 Kish 大
    assert c.n_eff == pytest.approx(kish)                                        # 封顶在 Kish，不是行数
    diag = res.diagnostics["分组有效样本量"]
    assert "封顶在这一格的 Kish" in diag and "两者可以不同" in diag and "封顶样本量" not in diag, diag


# ── 终审追加（a）：水平、分布的稳健性表每一行也是全样本均值——不打星号、不报 p 与 MDE，这两栏换成 95% 区间；
#    {{robust:<项>/main.p}} 与 .mde 解析不了。组差与别的估计量的稳健性表一个字不变

_ROB = [RobustnessSpec(label="F", sample_filter="year >= 2012"), RobustnessSpec(label="O", outcome="y")]
_ROB_PAPER = "# 标题\n## 结果\n筛选后为 {{robust:F/main.coef}}（p {{robust:F/main.p}}，MDE {{robust:F/main.mde}}）。\n"


def _rob_frame() -> pd.DataFrame:
    return _gap_frame().assign(z=lambda d: (d.y > 0.35).astype(int))


def _ph_problems(paper: str, res) -> list[str]:
    from oss.core.manuscript import check
    return [p for p in check(paper, res)[0] if p.startswith("占位符")]


def test_水平的稳健性表_不打星号不报p与MDE_各行报区间() -> None:
    from oss.core.analysis import mean_ci
    from oss.core.manuscript import table_markdown
    res = run_plan(_rob_frame(), _describe(outcome="z", description=DescriptionSpec(kind="level"), weights="w",
                                           survey=DES, robustness=_ROB))
    m, whole = res.estimates["main"], res.groups[0]

    def row(label: str, out: str, prop: bool) -> str:
        rb = res.robustness[label]
        e = rb.estimates["main"]
        lo, hi = mean_ci(e.coef, e.se, rb.t_crit or 1.96, proportion=prop)
        return f"| {label} | {out} | {e.coef:.3f} | {e.se:.3f} | [{lo:.3f}, {hi:.3f}] | {rb.n} |"

    body = table_markdown("robustness", res, en=False)[0]
    assert body.splitlines()[0] == "| 规格 | 结局 | 估计值 | 标准误 | 95% 区间 | 样本量 |", body
    assert body.splitlines()[2:5] == [
        f"| 主规格 | z | {m.coef:.3f} | {m.se:.3f} | [{whole.ci_lo:.3f}, {whole.ci_hi:.3f}] | {res.n} |",
        row("F", "z", True),            # 同一个 0/1 结局：logit 尺度，临界值用这一项自己的 t
        row("O", "y", False)], body     # 换成连续结局：均值 ± 临界值 × 标准误
    assert "p < 0.10" not in body and "MDE" not in body and "logit" in body and "不做「均值 = 0」的检验" in body
    en = table_markdown("robustness", res, en=True)[0]
    assert en.splitlines()[0] == "| Specification | Outcome | Estimate | SE | 95% CI | N |" and "MDE" not in en, en
    probs = _ph_problems(_ROB_PAPER, res)
    assert len(probs) == 2 and "{{robust:F/main.p}}" in probs[0] and "均值 = 0" in probs[0], probs
    assert "{{robust:F/main.mde}}" in probs[1] and "跟 0 比" in probs[1], probs


_ROB_TAIL_ZH = (" \\* p < 0.10，\\*\\* p < 0.05，\\*\\*\\* p < 0.01。\n\nMDE = 该规格**能分辨的最小差异**（双侧、80% 功效）。"
                "系数贴近零而 MDE 很宽，说明这一项**测不出**效应，不等于效应不存在。\n\n"
                "注：其中 1 项换了结局变量，**单位与主结果不同，系数不可直接比较**。")
_ROB_TAIL_EN = (" \\* p < .05, \\*\\* p < .01, \\*\\*\\* p < .001.\n\nMDE = the smallest difference this specification "
                "could detect (two-sided, 80% power). A coefficient near zero with a wide MDE means the check could not "
                "resolve an effect, not that none exists.\n\nNote: One check replaces the outcome variable; their units "
                "differ from the main estimate and the coefficients are not directly comparable.")


@pytest.mark.parametrize("estimator, rows, rows_en, note_zh, note_en", [
    ("ols_fe", "| 主规格 | y | 0.087** | 0.038 | 0.021 | 0.106 | 800 |\n| F | y | 0.098** | 0.046 | 0.032 | 0.129 | 600 |\n"
               "| O | z | 0.055 | 0.037 | 0.143 | 0.105 | 800 |",
               "| Main specification | y | 0.087* | 0.106 | 800 |\n|  |  | (0.038) |  |  |\n"
               "| F | y | 0.098* | 0.129 | 600 |\n|  |  | (0.046) |  |  |\n"
               "| O | z | 0.055 | 0.105 | 800 |\n|  |  | (0.037) |  |  |",
     "*注：*OLS + 固定效应。 标准误按 county 聚类。", "*Note:* OLS with fixed effects. Standard errors clustered by county."),
    ("describe", "| 主规格 | y | 0.087** | 0.035 | 0.012 | 0.097 | 800 |\n| F | y | 0.098** | 0.040 | 0.014 | 0.112 | 600 |\n"
                 "| O | z | 0.055 | 0.036 | 0.126 | 0.100 | 800 |",
                 "| Main specification | y | 0.087* | 0.097 | 800 |\n|  |  | (0.035) |  |  |\n"
                 "| F | y | 0.098* | 0.112 | 600 |\n|  |  | (0.040) |  |  |\n"
                 "| O | z | 0.055 | 0.100 | 800 |\n|  |  | (0.036) |  |  |",
     "*注：*分组均值与比例。 异方差稳健标准误。",
     "*Note:* Group means and proportions. Heteroskedasticity-robust standard errors."),
])
def test_组差与别的估计量的稳健性表一个字不变(estimator: str, rows: str, rows_en: str,
                                 note_zh: str, note_en: str) -> None:
    from oss.core.manuscript import table_markdown
    rob = [_ROB[0], RobustnessSpec(label="O", outcome="z")]
    plan = (AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="urban",
                         cluster_level="county", robustness=rob) if estimator == "ols_fe" else _describe(robustness=rob))
    res = run_plan(_rob_frame(), plan)
    head_zh = "| 规格 | 结局 | 估计值 | 标准误 | p | MDE | 样本量 |\n|---|---|---:|---:|---:|---:|---:|\n"
    head_en = "| Specification | Outcome | Estimate | MDE | N |\n|---|---|---:|---:|---:|\n"
    assert table_markdown("robustness", res, en=False)[0] == head_zh + rows + "\n\n" + note_zh + _ROB_TAIL_ZH
    assert table_markdown("robustness", res, en=True)[0] == (
        head_en + rows_en + "\n\n" + note_en + _ROB_TAIL_EN)
    assert _ph_problems(_ROB_PAPER, res) == []


# ── 终审追加（b）：{{result:main.mde}} 对水平、分布的均值也解析不了——MDE 是跟 0 比能分辨多小，对均值没有意义

_MDE_PAPER = "# 标题\n## 结果\n能分辨的最小差为 {{result:main.mde}}。\n"


@pytest.mark.parametrize("desc, refused", [
    (DescriptionSpec(kind="level"), True), (DescriptionSpec(kind="distribution"), True),
    (DescriptionSpec(kind="gap", groups=["urban"]), False), (DescriptionSpec(kind="trend", over="year"), False),
])
def test_水平与分布的主结果不报MDE_组差与趋势照报(desc: DescriptionSpec, refused: bool) -> None:
    probs = _ph_problems(_MDE_PAPER, run_plan(_gap_frame(), _describe(description=desc)))
    assert bool(probs) == refused, probs
    assert not refused or ("{{result:main.mde}}" in probs[0] and "跟 0 比" in probs[0]), probs


def test_ols_fe的main_mde照报() -> None:
    res = run_plan(_gap_frame(), AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y",
                                              treatment="urban", cluster_level="county"))
    assert _ph_problems(_MDE_PAPER, res) == []


# ── 终审追加（c）：拦「没有分组表」的三处（论文检查的警告、合规规则 tables、导出）说同一句话，不再说 {{table:main}}
#    那一行检验「均值 = 0」：水平、分布的主表现在只列均值与区间，组差、趋势那一行本来就是那个差或斜率


def test_三处拦分组表说同一句话_不再说主表检验均值为0(tmp_path: Path) -> None:
    from oss.api import errors
    from oss.api import paper as paper_api
    from oss.core import skillcheck as sc
    from oss.core.manuscript import check
    res = run_plan(_gap_frame(), _describe())
    warn = [w for w in check(_GROUPS_PAPER.replace("{{table:groups}}", "见正文"), res)[1] if "{{table:groups}}" in w]
    sc_dir = tmp_path / "sc"
    sc_dir.mkdir()
    res.save(sc_dir / "results.json")
    (sc_dir / "paper.md").write_text("{{table:descriptives}}\n{{table:robustness}}\n", encoding="utf-8")
    st, detail = sc.r_tables(sc.RunView(sc_dir))
    run = _describe_run(tmp_path)
    (run / "paper_rendered.md").write_text("# T", encoding="utf-8")
    (run / "paper.md").write_text("# T\n没有表", encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        paper_api.export(str(run))
    texts = (warn[0], detail, e.value.summary)
    assert len(warn) == 1 and st == sc.FAIL and e.value.code == 4
    assert not [t for t in texts if "均值 = 0" in t], texts
    from oss.core.manuscript import GROUPS_MAIN_TABLE
    assert all(GROUPS_MAIN_TABLE in t for t in texts), texts
