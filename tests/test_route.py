"""诊断 → 处置。评审：「它把自己所有的病症都准确地描述了一遍，然后没有治任何一个。」

处方是机械的：每条诊断接一个动作。这里测三件事——
该开的方开了、开的方能吃（补丁真的改了 plan.json）、吃了要复查（触发消失就标已解决）。
外加两道门：功效 FAIL 停、must 级没处置不给写论文。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from oss.core import route as route_mod
from oss.core.contracts import (
    AnalysisPlan, AnalysisResults, Estimate, PowerReport, RobustnessRun, RobustnessSpec,
)

FIXTURES = Path(__file__).parent / "fixtures"
ARCHIVE = Path(__file__).parent.parent / "docs" / "runs"


def _power(verdict: str) -> PowerReport:
    return PowerReport(idea_id="i", effect_size_raw=0.15, effect_size_adjusted=0.09, n_raw=2545,
                       n_effective=1283, n_clusters=99, icc=0.05, design_effect=1.98,
                       power=0.36 if verdict == "fail" else 0.85, verdict=verdict,
                       gray_band=(0.4, 0.8), discipline="sociology")


def _rundir(tmp_path: Path, *, plan: AnalysisPlan, results: AnalysisResults,
            power: PowerReport | None = None, findings: list[str] | None = None) -> Path:
    plan.save(tmp_path / "plan.json")
    results.save(tmp_path / "results.json")
    if power is not None:
        power.save(tmp_path / "power.json")
    if findings:
        (tmp_path / "review_plan.json").write_text(
            json.dumps({"verdict": "FAIL", "findings": findings}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _did_plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", estimator="did", outcome="y", unit="unit", time="year",
                treat_time="treat_time", cluster_level="unit")
    return AnalysisPlan(**{**base, **kw})


def _results(**diag) -> AnalysisResults:
    return AnalysisResults(plan_id="p", n=100,
                           estimates={"main": Estimate(term="t", coef=1.0, se=0.2, p=0.001)},
                           diagnostics=diag)


# ══════════════════════════════ 一、该开的方


def test_功效FAIL开停的处方_驳回不了(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(), results=_results(), power=_power("fail"))
    rt = route_mod.build(rd)
    it = next(i for i in rt.items if i.id == "power_fail")
    assert it.priority == "must" and it.kind == "stop"
    assert "换结局" in it.advice and "换题目" in it.advice
    # 换题目是**用户的决定**：处方里不写清楚，模型会自己回 ideas.json 挑一个接着跑
    assert "用户点头" in it.advice and "--authorized-by user" in it.advice
    with pytest.raises(ValueError, match="驳回不了"):
        route_mod.dismiss(rd, rt, "power_fail", "我觉得没事")
    with pytest.raises(ValueError):
        route_mod.disclose(rd, rt, "power_fail", "写进局限")
    with pytest.raises(ValueError):
        route_mod.apply(rd, rt, "power_fail")
    assert any("power_fail" in b for b in route_mod.blockers(rt))


def test_功效通过或灰区不开停(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(), results=_results(), power=_power("gray"))
    assert not any(i.id == "power_fail" for i in route_mod.build(rd).items)


def test_分批用TWFE开换估计量的补丁(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(),
                 results=_results(实施批次="17 批", 批次异质性="**各批效应差异大**（I² = 93%）"))
    rt = route_mod.build(rd)
    it = next(i for i in rt.items if i.id == "staggered_twfe")
    assert it.priority == "must" and it.kind == "patch_plan" and it.patch == {"estimator": "did_cs"}
    assert "17 批" in it.evidence and "93%" in it.evidence


def test_已经是did_cs就不开换估计量(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(estimator="did_cs"),
                 results=_results(实施批次="17 批", 批次异质性="**各批效应差异大**（I² = 93%）"))
    ids = {i.id for i in route_mod.build(rd).items}
    assert "staggered_twfe" not in ids and "cohort_hetero" in ids


def test_平行趋势不成立是must级(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(estimator="did_cs"), results=_results(平行趋势="**不成立**：处理前……"))
    it = next(i for i in route_mod.build(rd).items if i.id == "pretrend_fail")
    assert it.priority == "must" and "关联" in it.advice


def test_反转与流失开加稳健性的补丁(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(),
                 results=_results(处理反转="「dem」在 5 个单位里从 1 变回 0", 面板流失="**流失明显**——"))
    by_id = {i.id: i for i in route_mod.build(rd).items}
    assert by_id["reversal"].robustness.sample_filter == "_reversal == 0"
    assert by_id["attrition"].robustness.sample_filter == "_balanced == 1"


def test_计划里已有那项稳健性就不再开(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(robustness=[RobustnessSpec(label="x", sample_filter="_reversal == 0")]),
                 results=_results(处理反转="「dem」在 5 个单位里从 1 变回 0"))
    assert "reversal" not in {i.id for i in route_mod.build(rd).items}


def test_稳健性翻转要开处方(tmp_path) -> None:
    res = _results()
    res.robustness = {
        "R1": RobustnessRun(label="R1", n=90, estimates={"main": Estimate(term="t", coef=-0.5, se=0.2, p=0.01)}),
        "R2": RobustnessRun(label="R2", n=90, estimates={"main": Estimate(term="t", coef=0.9, se=0.6, p=0.14)}),
        "R3": RobustnessRun(label="R3", n=90, estimates={"main": Estimate(term="t", coef=1.1, se=0.2, p=0.001)}),
    }
    rd = _rundir(tmp_path, plan=_did_plan(), results=res)
    it = next(i for i in route_mod.build(rd).items if i.id == "robustness_flip")
    assert "R1" in it.evidence and "方向反转" in it.evidence
    assert "R2" in it.evidence and "显著性翻转" in it.evidence
    assert "R3" not in it.evidence


def test_审计的设计级意见逐条进处方(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(), results=_results(),
                 findings=["[设计] 处理时点内生", "[文字] 措辞", "[设计] 溢出未处理"])
    ids = [i.id for i in route_mod.build(rd).items if i.id.startswith("audit_")]
    assert ids == ["audit_1", "audit_2"]


# ══════════════════════════════ 二、开的方能吃、吃了要复查


def test_应用换估计量补丁真的改了plan并留痕(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(), results=_results(实施批次="3 批"))
    rt = route_mod.build(rd)
    msg = route_mod.apply(rd, rt, "staggered_twfe")
    assert "estimator → did_cs" in msg
    assert AnalysisPlan.load(rd / "plan.json").estimator == "did_cs"
    assert "oss route" in (rd / "trace.jsonl").read_text(encoding="utf-8")
    # 补丁一应用，计划就不再是 TWFE，触发随之消失——复查时标「已解决」
    assert next(i for i in route_mod.build(rd).items if i.id == "staggered_twfe").status == "resolved"


def test_应用加稳健性补丁追加到计划(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(), results=_results(处理反转="「dem」在 5 个单位里从 1 变回 0"))
    rt = route_mod.build(rd)
    route_mod.apply(rd, rt, "reversal")
    plan = AnalysisPlan.load(rd / "plan.json")
    assert any(r.sample_filter == "_reversal == 0" for r in plan.robustness)
    assert "reversal" not in {i.id for i in route_mod.build(rd).items if i.status == "pending"}


def test_触发消失后处方标已解决_状态带过来(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(), results=_results(处理前平衡="**不平衡**（x 超过 0.25）"))
    rt = route_mod.build(rd)
    route_mod.disclose(rd, rt, "imbalance", "局限第二段写了")
    _results().save(rd / "results.json")            # 重跑后不平衡消失
    rt2 = route_mod.build(rd)
    it = next(i for i in rt2.items if i.id == "imbalance")
    assert it.status == "resolved"


def test_驳回要写理由(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(), results=_results(处理前平衡="**不平衡**"))
    rt = route_mod.build(rd)
    with pytest.raises(ValueError, match="理由"):
        route_mod.dismiss(rd, rt, "imbalance", "")
    route_mod.dismiss(rd, rt, "imbalance", "两组水平差但走势平行，主规格已控制")
    assert next(i for i in route_mod.build(rd).items if i.id == "imbalance").status == "dismissed"


def test_没有must级待处置时没有阻塞(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(), results=_results(处理前平衡="**不平衡**"))
    assert route_mod.blockers(route_mod.build(rd)) == []


# ══════════════════════════════ 三、用真的验收跑回验


def test_验收跑的文件开出评审点名的处方() -> None:
    src = ARCHIVE / "r20260825_qog_internet-results.json"
    if not src.exists():
        pytest.skip("归档不在")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        rd = Path(d)
        for f in ("plan", "results", "power", "review_plan"):
            shutil.copy(ARCHIVE / f"r20260825_qog_internet-{f}.json", rd / f"{f}.json")
        rt = route_mod.build(rd)
        ids = {i.id: i for i in rt.items}
        assert "power_fail" in ids, "评审第 6 条：功效不足是设计失败"
        assert "cohort_hetero" in ids, "评审第 1 条：17 批 I²=93%"
        assert "imbalance" in ids and "audit_1" in ids
        assert "robustness_flip" in ids, "R5 换聚类后 p 从 0.12 变 0.0004"
        assert [i.id for i in rt.pending_must()] == ["power_fail"]


# ══════════════════════════════ 四、工具侧的配套：反转 / 平衡标志列，伴随事件研究用 CS，分箱带控制


def test_oss_run给数据加反转与平衡标志列() -> None:
    from oss.core.analysis import run_plan

    df = pd.read_csv(FIXTURES / "did_clean.csv")
    # 造几个反转：单位 1、4 在 2016 年之后 post 变回 0
    df.loc[(df.unit.isin([1, 4])) & (df.year >= 2016), "post"] = 0.0
    df = df.drop(index=df[(df.unit == 7) & (df.year == 2010)].index)   # 单位 7 少一年
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="post",
                        controls=["x"], fixed_effects=["unit", "year"], cluster_level="unit",
                        robustness=[RobustnessSpec(label="No reversals", sample_filter="_reversal == 0"),
                                    RobustnessSpec(label="Balanced", sample_filter="_balanced == 1")])
    res = run_plan(df, plan)
    rev = res.diagnostics["处理反转"]
    assert "「post」在 2 个单位里从 1 变回 0" in rev and "1、4" in rev, rev
    assert "_reversal == 0" in rev
    assert not res.robustness["No reversals"].error and res.robustness["No reversals"].n < res.n
    assert not res.robustness["Balanced"].error and res.robustness["Balanced"].n == res.n - 16, \
        "单位 7 少一年，平衡面板去掉它的 16 行"


def test_没有反转时不报处理反转() -> None:
    from oss.core.analysis import run_plan

    df = pd.read_csv(FIXTURES / "did_clean.csv")
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="post",
                        fixed_effects=["unit", "year"], cluster_level="unit")
    assert "处理反转" not in run_plan(df, plan).diagnostics


def test_重复截面不算单位与时间_不开剔除反转的处方() -> None:
    """`_unit_time` 原先拿**前两个固定效应**当单位与时间。gss_like 的固定效应写的是
    `["year", "region"]`——于是「单位」= 年份、「时间」= 地区，每格几百人，压根不是面板，
    却照样分组算反转：这两个夹具上满屏报「反转」，而照处方的补丁 `_reversal == 0` 走
    会把样本删空。
    """
    from oss.core.analysis import run_plan

    df = pd.read_csv(FIXTURES / "gss_like.csv")
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="sei",
                        treatment="paeduc", controls=["educ", "age", "cohort", "female"],
                        fixed_effects=["year", "region"], cluster_level="region_year")
    res = run_plan(df, plan)
    assert "处理反转" not in res.diagnostics, res.diagnostics.get("处理反转")
    assert "reversal" not in {i.id for i in route_mod._rules(plan, res, None, [])}


def test_二值控制变量翻转不算处理反转() -> None:
    """反转说的是**处理**从 1 变回 0；`_binary_treatments`（原名 `_binary_regressors`）
    原先把控制变量也算进去，一个在单位内变一下的 0/1 控制变量就能把整份面板报成「有反转」。
    """
    from oss.core.analysis import run_plan

    df = pd.read_csv(FIXTURES / "did_clean.csv")
    df["urban"] = 1.0
    df.loc[(df.unit.isin([2, 5, 8])) & (df.year >= 2012), "urban"] = 0.0
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="post",
                        controls=["x", "urban"], fixed_effects=["unit", "year"], cluster_level="unit")
    assert "处理反转" not in run_plan(df, plan).diagnostics


def test_分批写成ols_fe加post也开换估计量的处方() -> None:
    """处方跟 `did` 那一路走**同一条**（`staggered_twfe`）——门认的是数据，不是估计量的名字。"""
    from oss.core.analysis import run_plan

    df = pd.read_csv(FIXTURES / "did_staggered.csv")
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="post",
                        controls=["x"], fixed_effects=["unit", "year"], cluster_level="unit")
    res = run_plan(df, plan)
    item = next((i for i in route_mod._rules(plan, res, None, []) if i.id == "staggered_twfe"), None)
    assert item is not None and item.priority == "must" and item.patch == {"estimator": "did_cs"}


def test_分批时伴随的事件研究用CS并带各批效应() -> None:
    pytest.importorskip("statspai")
    from oss.core.analysis import run_plan

    df = pd.read_csv(FIXTURES / "did_staggered.csv")
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="post",
                        controls=["x"], fixed_effects=["unit", "year"], cluster_level="unit",
                        unit="unit", time="year", treat_time="treat_time")
    res = run_plan(df, plan)
    assert "Callaway" in res.diagnostics["逐年展开"], res.diagnostics["逐年展开"]
    assert "cohort_2006" in res.estimates and "cohort_2012" in res.estimates
    assert abs(res.estimates["cohort_2006"].coef - 4.5) < 0.4
    assert res.estimates["main"].term == "post", "main 仍是主规格的"
    assert "批次成员" in res.diagnostics and "识别假设" in res.diagnostics


def test_分箱各箱内带控制与固定效应_结果里说明() -> None:
    from oss.core.analysis import run_plan
    from oss.core.contracts import BinnedSpec

    df = pd.read_csv(FIXTURES / "did_clean.csv")
    outcome, treatment, by, ctrl = "y", "x", "year", "post"
    base = dict(plan_id="p", idea_id="i", estimator="ols_fe", outcome=outcome, treatment=treatment,
                controls=[ctrl, by], interactions=[f"{treatment}*{by}"], fixed_effects=["unit"],
                cluster_level="unit")
    adj = run_plan(df, AnalysisPlan(**base, binned=BinnedSpec(by=by, treatment=treatment, bins=3)))
    raw = run_plan(df, AnalysisPlan(**base, binned=BinnedSpec(by=by, treatment=treatment, bins=3, bivariate=True)))
    assert adj.binned and raw.binned
    assert "按主规格跑" in adj.diagnostics["分箱口径"] and ctrl in adj.diagnostics["分箱口径"]
    assert "二元" in raw.diagnostics["分箱口径"]
    a = [b.slope for b in adj.binned["全样本"]]
    r = [b.slope for b in raw.binned["全样本"]]
    assert a != r, "带控制变量的分箱斜率不该跟二元的一模一样"
    assert all(b.se > 0 for b in adj.binned["全样本"])


# ══════════════════════════════ 五、留痕：空复查也要留下证据

def test_每次route都留痕_包括一条处方都没有那次(tmp_path) -> None:
    """**这个模块原先一条留痕都不写。**

    而 `oss eval` 的 `route-after-run`（must 级）恰恰是去留痕里找 `oss route`。
    于是这条规则测的不是有没有复查，**是模型有没有手工补一行记录**——
    以前几次跑能过，是因为模型碰巧补了。

    11 轮 CGSS 那次真跑坐实了：执行者跑了三次 route、每次都是空的，
    还专门用 `oss trace save` 写了段说明，规则照样判它不合格。

    空复查尤其要留痕：「复查过、没有处方」与「压根没复查」在 `route.json`
    上长得一模一样（都是 `items: []`），**只有留痕分得开**。
    """
    from oss.api import route as route_api

    run = _rundir(tmp_path, plan=_did_plan(estimator="ols_fe", treat_time=""),
                  results=_results())
    res = route_api.status(str(run))
    assert res["data"]["items"] == [], "这个夹具本来就不该触发任何处方"

    events = [json.loads(l) for l in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    route_events = [e for e in events if e.get("command") == "oss route"]
    assert len(route_events) == 1, events
    assert "没有处方" in route_events[0]["note"]
    assert route_events[0]["args"]["n_items"] == 0


def test_处置不重复记账(tmp_path) -> None:
    """处置那条路 `core.route` 本来就记；**别记两遍**。

    复查一次 + 处置一次 = 两条留痕，不是三条。
    """
    from oss.api import route as route_api

    run = _rundir(tmp_path, plan=_did_plan(estimator="did_cs"),
                  results=_results(平行趋势="**不成立**：处理前……"), power=_power("pass"))
    route_api.status(str(run))
    route_api.disclose(str(run), "pretrend_fail", why="窗口内没有干净的对照")

    events = [json.loads(l) for l in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    notes = [e["note"] for e in events if e.get("command") == "oss route"]
    assert len(notes) == 2, notes
    assert "1 条处方" in notes[0], "第一条是复查"
    assert "写进局限" in notes[1] and "窗口内没有干净的对照" in notes[1], "第二条是处置"


def test_空复查之后eval的route规则能过(tmp_path) -> None:
    """修之前：跑了三次 route 也过不了。修之后：跑过就是跑过。"""
    from oss.api import route as route_api
    from oss.core.contracts import TraceEvent
    from oss.core.skillcheck import RunView, r_route_after_run

    run = _rundir(tmp_path, plan=_did_plan(estimator="ols_fe", treat_time=""),
                  results=_results())
    ev = TraceEvent(seq=1, step="results", command="oss run", outcome="ok")
    (run / "trace.jsonl").write_text(ev.model_dump_json() + "\n", encoding="utf-8")

    status, _ = r_route_after_run(RunView(run))
    assert status == "fail", "还没复查就该判不过"

    route_api.status(str(run))
    status, why = r_route_after_run(RunView(run))
    assert status == "pass", why


# ══════════════════════════════ 三、因果骨架的七条处方


def _ids(rd: Path) -> dict:
    return {i.id: i for i in route_mod.build(rd).items}


def test_弱工具开must级处方(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(estimator="iv", treatment="x", instruments=["z"]),
                 results=_results(**{"有效 F": "4.20", "第一阶段强度": "**太弱，不可用常规推断**"}))
    it = _ids(rd)["weak_iv"]
    assert it.priority == "must" and "AR 区间" in it.advice


def test_2SLS远大于OLS开写进局限(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(estimator="iv", treatment="x", instruments=["z"]),
                 results=_results(**{"有效 F": "40.0", "第一阶段强度": "**够强**", "2SLS/OLS": "3.40"}))
    assert _ids(rd)["iv_ols_gap"].priority == "should"


@pytest.mark.parametrize("pw,grade", [("0.30", "不足"), ("0.65", "灰区")])
def test_预检功效不够开处方(tmp_path, pw: str, grade: str) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(), results=_results(
        平行趋势="平行趋势未被推翻", 预检功效=pw, 预检功效说明="x"))
    assert grade in _ids(rd)["pretrend_underpowered"].trigger


def test_平行趋势已判不成立时不再开功效处方(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(), results=_results(平行趋势="**不成立**", 预检功效="0.30"))
    assert "pretrend_underpowered" not in _ids(rd)


def test_breakdown小开处方(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(), results=_results(**{"breakdown 值": "0.40"}))
    assert "low_breakdown" in _ids(rd)


def test_敏感性偏弱_经济学升为must(tmp_path) -> None:
    diag = {"稳健值（显著性）": "0.010", "敏感性基准值": "0.050", "敏感性分析": "x"}
    plan = _did_plan(estimator="ols_fe", treatment="x", treat_time="", research_type="causal")
    rd = _rundir(tmp_path, plan=plan, results=_results(**diag), power=_power("pass"))
    assert _ids(rd)["weak_sensitivity"].priority == "should"
    _power("pass").model_copy(update={"discipline": "economics"}).save(tmp_path / "power.json")
    assert _ids(rd)["weak_sensitivity"].priority == "must"


def test_主结果在阈值附近_只对IV与双重差分开(tmp_path) -> None:
    # 两条走独立目录：同一目录复跑会把上一条的触发按「已消失」标成 resolved 但留痕，
    # 这里问的是两个各自独立的计划分别该不该开，不是同一研究复跑后的处置。
    res = AnalysisResults(plan_id="p", n=100, estimates={"main": Estimate(term="t", coef=1.0, se=0.5, p=0.04)})
    assert "near_threshold" in _ids(_rundir(tmp_path / "a", plan=_did_plan(), results=res))
    assert "near_threshold" not in _ids(_rundir(tmp_path / "b", plan=_did_plan(estimator="ols_fe", treatment="x"), results=res))


def test_主结果不显著时不开breakdown与敏感性处方(tmp_path) -> None:
    """不显著的主结果，breakdown 值与「削到不显著的稳健值」按定义就是 0——没有结论可翻，开了就是误拦
    （任务 13 校准：样本 4/14、留档 4/5 是这种；用户 2026-09-14 定只对显著的主结果开）。"""
    diag = {"breakdown 值": "0.00", "稳健值（显著性）": "0.000", "敏感性基准值": "0.050", "敏感性分析": "x"}
    res = AnalysisResults(plan_id="p", n=100, estimates={"main": Estimate(term="t", coef=0.1, se=0.2, p=0.62)},
                          diagnostics=diag)
    plan = _did_plan(estimator="ols_fe", treatment="x", treat_time="", research_type="causal")
    assert not {"low_breakdown", "weak_sensitivity"} & set(_ids(_rundir(tmp_path, plan=plan, results=res)))


def test_没算的数不开处方也不报错(tmp_path) -> None:
    rd = _rundir(tmp_path, plan=_did_plan(), results=_results(
        **{"有效 F": "没算", "预检功效": "没算", "breakdown 值": "∞", "2SLS/OLS": "inf"}))
    got = _ids(rd)
    assert not {"weak_iv", "pretrend_underpowered", "low_breakdown"} & set(got)
