"""按构念找数据：数据契约、样本构建日志、列索引搜索、代理差距三栏。

评审第 4 条（调节变量测的不是理论要的东西）和第 10 条（8 个国家去哪了没交代）。
借自 AERS 的 data contract（五项 go/no-go）和「脚注 4」（样本构建日志）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from oss.core.analysis import PlanRejected, run_plan
from oss.core.contracts import AnalysisPlan, AnalysisResults, Estimate, SampleStep, SpecSummary
from oss.core.manuscript import table_markdown

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


def _cs_plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", estimator="did_cs", outcome="y", unit="unit", time="year",
                treat_time="treat_time", controls=["x"], cluster_level="unit")
    return AnalysisPlan(**{**base, **kw})


# ══════════════════════════════ 一、样本构建日志：谁在哪一步掉了


def test_样本日志列出整个掉出去的单位和肇事变量() -> None:
    pytest.importorskip("statspai")
    df = pd.read_csv(FIXTURES / "did_staggered.csv")
    df.loc[df.unit.isin([5, 8]), "x"] = np.nan          # 两个单位的控制变量整段缺失
    res = run_plan(df, _cs_plan(sample_filter="year >= 2002"))
    steps = {s.step: s for s in res.sample_log}
    assert steps["原始"].rows == len(df) and steps["原始"].units == 150
    assert "样本筛选 `year >= 2002`" in steps
    drop = steps["去掉回归变量缺失的行"]
    assert drop.dropped_units == ["5", "8"] and drop.units == 148
    assert "x" in drop.note and "100%" in drop.note, "要说清是哪个变量把它们弄丢的"
    assert steps["估计样本"].units == 148
    text = res.diagnostics["样本构建"]
    assert "掉 5, 8" in text and "主要因为 x" in text, text


def test_样本日志能排成表() -> None:
    res = AnalysisResults(plan_id="p", n=10, estimates={"main": Estimate(term="t", coef=1, se=1, p=0.5)},
                          spec=SpecSummary(estimator="ols_fe"),
                          sample_log=[SampleStep(step="原始", rows=3150, units=150),
                                      SampleStep(step="去掉回归变量缺失的行", rows=3100, units=148,
                                                 dropped_units=["5", "8"], note="x"),
                                      SampleStep(step="估计样本", rows=3100, units=148)])
    body, why = table_markdown("sample", res, en=True)
    assert body, why
    assert "| Raw data | 3,150 | 150 |  |" in body, body
    assert "| Drop rows with missing regression variables | 3,100 | 148 | 5, 8 |" in body
    assert table_markdown("sample", AnalysisResults(plan_id="p"), en=True)[0] is None


# ══════════════════════════════ 二、数据契约


def test_分批设计里单位时间键重复要拒绝() -> None:
    pytest.importorskip("statspai")
    df = pd.read_csv(FIXTURES / "did_staggered.csv")
    dup = pd.concat([df, df.head(3)])
    with pytest.raises(PlanRejected, match="重复键"):
        run_plan(dup, _cs_plan())


def test_关联分析不拦重复键() -> None:
    """个体层面数据上省份固定效应：一个省-年很多行，那是正常的。"""
    df = pd.read_csv(FIXTURES / "did_clean.csv")
    dup = pd.concat([df, df.head(3)])
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="post",
                        fixed_effects=["unit", "year"], cluster_level="unit")
    assert run_plan(dup, plan).n == len(dup)


def test_左删失的处理单位被点名并能用标志列剔掉() -> None:
    pytest.importorskip("statspai")
    from oss.core.contracts import RobustnessSpec

    df = pd.read_csv(FIXTURES / "did_staggered.csv")
    lc = df[~((df.treat_time == 2006) & (df.year < 2006))]      # 2006 批看不到处理前
    res = run_plan(lc, _cs_plan(robustness=[
        RobustnessSpec(label="Exclude left-censored", sample_filter="_has_pre == 1")]))
    c = res.diagnostics["数据契约"]
    assert "50 个处理单位没有处理前观测" in c and "_has_pre == 1" in c, c
    rb = res.robustness["Exclude left-censored"]
    assert not rb.error, rb.error
    assert rb.n == len(lc[lc.treat_time != 2006]), "剔掉的正好是左删失那一批"
    assert abs(rb.estimates["main"].coef - 2.0) < 0.4, "只剩 2012 批，真实效应 2.0"


def test_都有处理前观测时契约照常写明() -> None:
    pytest.importorskip("statspai")
    res = run_plan(pd.read_csv(FIXTURES / "did_staggered.csv"), _cs_plan())
    c = res.diagnostics["数据契约"]
    assert "单位-时间键唯一" in c and "处理单位都有处理前观测" in c and "50 个从未处理" in c, c


def test_左删失开出加稳健性的处方(tmp_path: Path) -> None:
    from oss.core import route as route_mod

    _cs_plan().save(tmp_path / "plan.json")
    AnalysisResults(plan_id="p", n=1, estimates={"main": Estimate(term="t", coef=1, se=1, p=0.5)},
                    diagnostics={"数据契约": "**5 个处理单位没有处理前观测（左删失）**：a、b"}).save(tmp_path / "results.json")
    it = next(i for i in route_mod.build(tmp_path).items if i.id == "left_censored")
    assert it.robustness.sample_filter == "_has_pre == 1"
    route_mod.apply(tmp_path, route_mod.build(tmp_path), "left_censored")
    assert any("_has_pre" in (r.sample_filter or "") for r in AnalysisPlan.load(tmp_path / "plan.json").robustness)


# ══════════════════════════════ 三、列索引：按构念找列


def test_列索引读变量标签与值标签并按关键词搜(tmp_path: Path) -> None:
    from oss.core import datapacks as packs

    df = pd.DataFrame({"ccode": ["A", "B"], "year": [2000, 2001],
                       "v2smgovdom": [1, 2], "wdi_internet": [3.5, 4.5], "gdp": [1.0, 2.0]})
    path = tmp_path / "t.dta"
    df.to_stata(path, write_index=False, version=118,
                variable_labels={"v2smgovdom": "Government dissemination of false information domestic",
                                 "wdi_internet": "Individuals using the Internet (% of population)"},
                value_labels={"v2smgovdom": {1: "Never", 2: "Rarely"}})
    index = packs.index_columns(path)
    by = {r["name"]: r for r in index}
    assert by["wdi_internet"]["label"].startswith("Individuals using the Internet")
    assert by["v2smgovdom"]["values"].get("1") == "Never"
    hits = packs.search_columns(index, ["internet"])
    assert [h["name"] for h in hits] == ["wdi_internet"]
    hits = packs.search_columns(index, ["false information", "censor"])
    assert [h["name"] for h in hits] == ["v2smgovdom"], "按变量标签也能搜到"
    assert packs.search_columns(index, ["never"])[0]["name"] == "v2smgovdom", "值标签也算"
    assert packs.search_columns(index, []) == index


def test_csv没有标签时索引只有列名(tmp_path: Path) -> None:
    from oss.core import datapacks as packs

    index = packs.index_columns(FIXTURES / "did_clean.csv")
    assert [r["name"] for r in index][:3] == ["unit", "year", "treat_time"]
    assert all(r["label"] == "" for r in index)


def test_没拿过的数据集搜列要说先去拿(tmp_path: Path) -> None:
    from oss.core import datapacks as packs

    with pytest.raises(FileNotFoundError, match="oss data get qog"):
        packs.source_file("qog", cache=tmp_path)


# ══════════════════════════════ 四、代理差距三栏


def test_代理变量没填三栏时校验警告(tmp_path: Path) -> None:
    from oss.cli.main import app
    from oss.core.contracts import BindingReport, VariableBinding

    AnalysisResults(plan_id="p", n=100, estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe")).save(tmp_path / "results.json")
    BindingReport(idea_id="i", core_ok=True, bindings=[
        VariableBinding(construct_name="uncensored political information", role="moderator",
                        column="internet", status="proxy"),
        VariableBinding(construct_name="health", role="outcome", column="lifexp", status="bound"),
    ]).save(tmp_path / "binding.json")
    (tmp_path / "paper.md").write_text(
        "# T\n\n## Abstract\nEffect {{result:main.coef}} (n = {{result:main.n}}).\n\n## Introduction\nx\n\n"
        "## Data and Methods\nx\n\n## Results\n*Table 1. Main estimates.*\n{{table:main}}\n\n## Discussion\nx\n",
        encoding="utf-8")
    r = runner.invoke(app, ["paper", "check", "--run", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "uncensored political information" in r.output and "gap" in r.output, r.output
    assert "health" not in r.output.split("构念")[1] if "构念" in r.output else True

    b = BindingReport.load(tmp_path / "binding.json")
    b.bindings[0].construct_def = "political information not filtered by the state"
    b.bindings[0].measures = "share of individuals using the internet (ITU)"
    b.bindings[0].gap = "access ≠ uncensored content; bias correlated with regime type"
    b.save(tmp_path / "binding.json")
    r = runner.invoke(app, ["paper", "check", "--run", str(tmp_path)])
    assert "三栏没填全" not in r.output, r.output
