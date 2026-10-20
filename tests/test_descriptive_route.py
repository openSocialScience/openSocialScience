"""描述骨架的处方、合规规则与审稿（实施计划 Task 6–8）。单放一个文件：主工作区同时在改 tests/test_descriptive.py。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss.core import route as route_mod
from oss.core.contracts import (
    AnalysisPlan, AnalysisResults, Benchmark, DescriptionSpec, Estimand, Estimate, GroupCell, PowerReport,
)

EST = Estimand(unit_quantity="比例之差", target_population="在业人口", assumptions=["覆盖"], link="加权")


def _power(mode: str, verdict: str) -> PowerReport:
    return PowerReport(idea_id="i", effect_size_raw=0.1, effect_size_adjusted=0.1, n_raw=400, n_effective=400,
                       power=0.0, verdict=verdict, mode=mode, precision_halfwidth=0.15, gray_band=(0.7, 1.0))


def _plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", research_type="descriptive", estimator="describe", outcome="y", estimand=EST,
                description=DescriptionSpec(kind="gap", groups=["urban"],
                                            benchmarks=[Benchmark(quantity="q", value=1.0, source="官方统计")]))
    return AnalysisPlan(**{**base, **kw})


def _res(**kw) -> AnalysisResults:
    cells = kw.pop("groups", [GroupCell(label="urban=0", n=300, n_eff=280, mean=0.2, se=0.02, ci_lo=0.16, ci_hi=0.24,
                                        reference=True)])
    return AnalysisResults(plan_id="p", n=500, estimates={"main": Estimate(term="gap", coef=0.1, se=0.03, p=0.001)},
                           groups=cells, **kw)


def _ids(items) -> set[str]:
    return {i.id for i in items}


def test_精度模式的fail不开停的处方_开precision_low() -> None:
    items = route_mod._rules(_plan(), _res(), _power("precision", "fail"), [])
    assert "power_fail" not in _ids(items)
    it = next(i for i in items if i.id == "precision_low")
    assert it.priority == "must" and it.kind == "disclose"


def test_功效模式的fail照旧开停的处方() -> None:
    assert "power_fail" in _ids(route_mod._rules(_plan(research_type="causal", estimator="ols_fe", treatment="x"),
                                                 _res(), _power("power", "fail"), []))


def test_分组太小开处方() -> None:
    tiny = GroupCell(label="urban=1", n=40, n_eff=30, mean=0.3, se=0.08, ci_lo=0.14, ci_hi=0.46, gap=0.1)
    items = route_mod._rules(_plan(), _res(groups=[_res().groups[0], tiny]), None, [])
    assert "small_cells" in _ids(items) and "urban=1" in next(i for i in items if i.id == "small_cells").evidence


def test_用面板描述总体变化开处方() -> None:
    plan = _plan(description=DescriptionSpec(kind="trend", over="year", change="social",
                                             benchmarks=[Benchmark(quantity="q", value=1.0, source="s")]))
    assert "panel_for_social_change" in _ids(route_mod._rules(plan, _res(), None, [], structure_kind="panel_balanced"))
    assert "panel_for_social_change" not in _ids(route_mod._rules(plan, _res(), None, [],
                                                                  structure_kind="repeated_cross_section"))


def test_没写外部基准开现实核查的处方() -> None:
    plan = _plan(description=DescriptionSpec(kind="gap", groups=["urban"]))
    assert "reality_check_missing" in _ids(route_mod._rules(plan, _res(), None, []))
    assert "reality_check_missing" not in _ids(route_mod._rules(_plan(), _res(), None, []))


def test_因果计划不开描述处方() -> None:
    plan = _plan(research_type="causal", estimator="ols_fe", treatment="x", description=None)
    assert not {"precision_low", "small_cells", "panel_for_social_change", "reality_check_missing"} & _ids(
        route_mod._rules(plan, _res(), None, []))


def test_计划写成因果的_精度模式的fail不开precision_low() -> None:
    """计划 Task 6 第 3 步：精度不足那条要「计划没写成别的研究类型」——环节 4 按描述算了精度，计划却写成因果，不开。"""
    plan = _plan(research_type="causal", estimator="ols_fe", treatment="x", description=None)
    assert "precision_low" not in _ids(route_mod._rules(plan, _res(), _power("precision", "fail"), []))
    # 老计划没写研究类型：照开（没写成「别的」类型）
    assert "precision_low" in _ids(route_mod._rules(_plan(research_type=""), _res(), _power("precision", "fail"), []))


def test_有效样本量为0时_处方不拿半宽说事() -> None:
    """power.json 在有效样本量为 0 时把半宽存成 0.0、判 fail：处方照开，但不许写「半宽 0.000 比有意义的差别还宽」。"""
    pw = _power("precision", "fail").model_copy(update={"n_effective": 0, "precision_halfwidth": 0.0})
    it = next(i for i in route_mod._rules(_plan(), _res(), pw, []) if i.id == "precision_low")
    assert "有效样本量是 0" in it.trigger and "0.000" not in it.trigger, it.trigger


def test_没给分组时_precision_low不写最小分组() -> None:
    """批 1 复审：没给分组列时 min_cell_label 是空串，证据里不许打出「最小分组 —（有效样本量 0）」。
    最小分组那半句按 min_cell_label 判：给了分组却没标签的也不写；有标签才写。"""
    def evidence(**upd) -> str:
        pw = _power("precision", "fail").model_copy(update=upd)
        return next(i for i in route_mod._rules(_plan(), _res(), pw, []) if i.id == "precision_low").evidence

    assert "最小分组" not in evidence()                                        # 没给分组
    assert "最小分组" not in evidence(min_cell_n_eff=30.0)                     # 给了分组却没标签
    ev = evidence(min_cell_n_eff=30.0, min_cell_label="urban=1")
    assert "最小分组 urban=1" in ev and "30" in ev, ev


# ══════════════════════════════ 功效报告跟计划的研究类型对不上（复审 2026-09-15 补）


def _causal_plan(**kw) -> AnalysisPlan:
    return _plan(**{"research_type": "causal", "estimator": "ols_fe", "treatment": "x", "description": None, **kw})


def _anchor_causal(d: Path) -> None:
    """从因果降为描述：锚点冻结时写的是 causal。"""
    (d / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
        "research_type": "causal", "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")


@pytest.mark.parametrize("verdict", ["fail", "gray", "pass"])
def test_因果计划配精度报告_开驳回不了的mismatch_功效与精度两条都不开(verdict: str) -> None:
    items = route_mod._rules(_causal_plan(), None, _power("precision", verdict), [])
    it = next(i for i in items if i.id == "power_mode_mismatch")
    assert it.priority == "must" and it.kind == "stop", it
    assert "causal" in it.trigger and "重跑 oss power" in it.trigger, it.trigger
    assert not {"power_fail", "precision_low"} & _ids(items)
    assert "power_mode_mismatch" in route_mod.NOT_DISMISSABLE


@pytest.mark.parametrize("verdict", ["fail", "gray", "pass"])
def test_描述计划配功效报告_开mismatch(verdict: str) -> None:
    items = route_mod._rules(_plan(), None, _power("power", verdict), [])
    it = next(i for i in items if i.id == "power_mode_mismatch")
    assert it.priority == "must" and it.kind == "stop", it
    assert "描述类" in it.trigger and "重跑 oss power" in it.trigger, it.trigger
    assert "power_fail" not in _ids(items)


def test_老power_json没有mode键_按功效算(tmp_path: Path) -> None:
    pj = json.loads(_power("power", "fail").model_dump_json())
    for k in ("mode", "precision_halfwidth", "min_cell_n_eff", "min_cell_label"):
        pj.pop(k)
    for sub, plan, want, not_want in (("desc", _plan(), "power_mode_mismatch", "power_fail"),
                                      ("causal", _causal_plan(), "power_fail", "power_mode_mismatch")):
        d = tmp_path / sub
        d.mkdir()
        plan.save(d / "plan.json")
        (d / "power.json").write_text(json.dumps(pj, ensure_ascii=False), encoding="utf-8")
        ids = _ids(route_mod.build(d).items)
        assert want in ids and not_want not in ids, (sub, ids)


def test_因果降为描述_锚点留着causal_精度报告不开mismatch(tmp_path: Path) -> None:
    """只看计划上写的类型、不看锚点：降为描述后重跑了 oss power（精度报告），不许再响。"""
    _plan().save(tmp_path / "plan.json")
    _power("precision", "pass").save(tmp_path / "power.json")
    _anchor_causal(tmp_path)
    assert "power_mode_mismatch" not in _ids(route_mod.build(tmp_path).items)


@pytest.mark.parametrize("mode", ["precision", "power"])
def test_老计划没写研究类型_不开mismatch_照旧那条路(mode: str) -> None:
    items = route_mod._rules(_plan(research_type=""), None, _power(mode, "fail"), [])
    assert "power_mode_mismatch" not in _ids(items)
    assert ("precision_low" if mode == "precision" else "power_fail") in _ids(items)
    assert "power_mode_mismatch" not in _ids(route_mod._rules(None, None, _power(mode, "fail"), []))


def test_mismatch驳回不了_写进局限也不行_拦截说明写重跑(tmp_path: Path) -> None:
    _causal_plan().save(tmp_path / "plan.json")
    _power("precision", "pass").save(tmp_path / "power.json")
    rt = route_mod.build(tmp_path)
    with pytest.raises(ValueError, match="驳回不了"):
        route_mod.dismiss(tmp_path, rt, "power_mode_mismatch", "我觉得没事")
    with pytest.raises(ValueError):
        route_mod.disclose(tmp_path, rt, "power_mode_mismatch", "写进局限")
    with pytest.raises(ValueError):
        route_mod.apply(tmp_path, rt, "power_mode_mismatch")
    how = next(b for b in route_mod.blockers(rt) if "power_mode_mismatch" in b).rsplit("——", 1)[1]
    assert "重跑 oss power" in how and "换结局" not in how, how


def test_描述类idea先按精度算_计划写成因果_开mismatch_重跑后回到功效(tmp_path: Path) -> None:
    """复审的探针（_probe_reach）：真走 oss power。环节 4 还没有计划，按 idea 的类型（描述）算精度；
    环节 5 计划写成因果——route 开 power_mode_mismatch、合规 power-fail-stop 判不过；
    重跑 oss power（有了计划，按计划的类型分路）得到功效报告，这条标已解决，平常的功效路径接上。"""
    import numpy as np
    import pandas as pd

    from oss.api import power as power_api
    from oss.api import profile as profile_api
    from oss.core import skillcheck as sc
    from oss.core.contracts import Idea, IdeaSet

    run = tmp_path / "r"
    run.mkdir()
    rng = np.random.default_rng(1)
    pd.DataFrame({"y": rng.normal(size=300), "urban": [0] * 200 + [1] * 100, "x": rng.normal(size=300)}
                 ).to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    IdeaSet(ideas=[Idea(idea_id="i1", title="T", research_question="Q", research_type="descriptive")]
            ).save(run / "ideas.json")
    assert power_api.power(str(run), idea="i1", effect=0.05, effect_source="夹具")["data"]["mode"] == "precision"
    _causal_plan(idea_id="i1").save(run / "plan.json")
    ids = _ids(route_mod.build(run).items)
    assert "power_mode_mismatch" in ids and not {"power_fail", "precision_low"} & ids, ids
    assert next(i for i in sc.evaluate(run).items if i.id == "power-fail-stop").status == sc.FAIL

    again = power_api.power(str(run), idea="i1", effect=0.05, effect_source="夹具")["data"]
    assert again["mode"] == "power" and again["verdict"] == "fail", again
    by_id = {i.id: i for i in route_mod.build(run).items}
    assert by_id["power_mode_mismatch"].status == "resolved"
    assert by_id["power_fail"].status == "pending"
    assert next(i for i in sc.evaluate(run).items if i.id == "power-fail-stop").status != sc.FAIL  # 没写论文


# ══════════════════════════════ Task 8：陪审团一问、论文审稿人三问


def test_陪审团问描述类题目新在哪() -> None:
    from oss.host.reviewer import _PROMPTS
    p = _PROMPTS["ideas"]
    assert "六件事" in p and "新数据" in p and "新事实" in p and "新测量" in p and "经济学" in p


def test_论文审稿人对描述类多问三件() -> None:
    from oss.host.reviewer import _PROMPTS
    p = _PROMPTS["paper"]
    for k in ("descriptive", "意外的可能", "现象", "推导链", "函数形式", "边界条件"):
        assert k in p, k


# ══════════════════════════════ 功效报告算的是另一道题（终审 M2）


@pytest.mark.parametrize("mode", ["power", "precision"])
def test_题号对不上_开mismatch_处方给出idea(mode: str) -> None:
    """换题后 power.json 还是旧题的：类型对得上也要停——报告答的是另一道题。处方要写出带 --idea 的命令，
    不然「重跑 oss power」重跑的还是旧题，这条永远消不掉。"""
    pw = _power(mode, "pass").model_copy(update={"idea_id": "i1"})
    plan = _causal_plan(idea_id="i2") if mode == "power" else _plan(idea_id="i2")
    items = route_mod._rules(plan, None, pw, [])
    it = next(i for i in items if i.id == "power_mode_mismatch")
    assert it.priority == "must" and it.kind == "stop", it
    assert "i1" in it.trigger and "i2" in it.trigger, it.trigger
    assert "oss power --idea i2" in it.advice and "oss power --idea i2" in it.trigger, (it.trigger, it.advice)
    assert not {"power_fail", "precision_low"} & _ids(items)


def test_类型对不上_处方也给出idea() -> None:
    it = next(i for i in route_mod._rules(_causal_plan(), None, _power("precision", "pass"), [])
              if i.id == "power_mode_mismatch")
    assert "oss power --idea i" in it.advice, it.advice
    assert "重跑 oss power" in it.trigger and "causal" in it.trigger, it.trigger


def test_题号与类型都对得上_不开mismatch() -> None:
    assert "power_mode_mismatch" not in _ids(route_mod._rules(_causal_plan(), None, _power("power", "pass"), []))
    assert "power_mode_mismatch" not in _ids(route_mod._rules(_plan(), None, _power("precision", "pass"), []))
    # 计划没写 idea_id（老计划）：题号无从比，照旧只看类型
    assert "power_mode_mismatch" not in _ids(route_mod._rules(_causal_plan(idea_id=""), None,
                                                              _power("power", "pass"), []))


def test_说明书写了power_mode_mismatch() -> None:
    stages = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "social-research" / "stages"
    notes = (stages / "6-run.notes.md").read_text(encoding="utf-8")
    row = next(x for x in notes.splitlines() if x.startswith("| `power_mode_mismatch`"))
    assert "驳回不了" in row and "oss power --idea" in row and "停" in row, row
    feas = (stages / "4-feasibility.md").read_text(encoding="utf-8")
    assert "power_mode_mismatch" in feas and "oss anchor switch" in feas


# ══════════════════════════════ 照处方重跑按计划算；换题后计划还是旧题的（F1 复审 S1、M2-5）


_SURVEY = {"strata": "stratum", "psu": "county", "weight_use": "descriptive", "design_source": "码本"}
_EST = {"unit_quantity": "上网比例", "target_population": "成年人", "assumptions": ["a"], "link": "l"}


def _review_run(tmp_path: Path, ideas: dict[str, str]) -> Path:
    """F1 复审的夹具（f1_frame.make）：40 个区县分 4 层，城乡在区县内不全同，权重对数正态。"""
    import numpy as np
    import pandas as pd

    from oss.api import profile as profile_api
    from oss.core.contracts import Idea, IdeaSet

    rng = np.random.default_rng(7)
    county = np.repeat(np.arange(1, 41), 30).astype(float)
    urban = (rng.random(1200) < 0.3 + 0.4 * (county % 2)).astype(float)
    u = rng.normal(0, 0.6, 40)[(county - 1).astype(int)]
    y = (rng.random(1200) < 1 / (1 + np.exp(-(-0.3 + 0.5 * urban + u)))).astype(float)
    run = tmp_path / "r"
    run.mkdir()
    pd.DataFrame({"online": y, "urban": urban, "county": county, "stratum": (county - 1) // 10 + 1,
                  "weight": rng.lognormal(0, 0.5, 1200), "age": rng.integers(18, 61, 1200).astype(float),
                  "year": rng.choice([2010.0, 2012.0, 2014.0], 1200),
                  "area": np.where(urban == 1, "城市", "农村")}).to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    IdeaSet(ideas=[Idea(idea_id=i, title=f"T{i}", research_question=f"q{i}", research_type=t)
                   for i, t in ideas.items()]).save(run / "ideas.json")
    (run / "binding.json").write_text(json.dumps({"idea_id": next(iter(ideas)), "core_ok": True, "bindings": [
        {"construct_name": "上网", "role": "outcome", "column": "online", "status": "bound"},
        {"construct_name": "城镇", "role": "treatment", "column": "urban", "status": "bound"}]},
        ensure_ascii=False), encoding="utf-8")
    return run


def _gap_plan(idea_id: str) -> str:
    return json.dumps({"plan_id": "p1", "idea_id": idea_id, "research_type": "descriptive", "estimator": "describe",
                       "outcome": "online", "weights": "weight", "survey": _SURVEY, "estimand": _EST,
                       "description": {"kind": "gap", "groups": ["urban"],
                                       "benchmarks": [{"quantity": "q", "value": 0.7, "source": "s"}]}},
                      ensure_ascii=False)


def test_照处方重跑oss_power_按计划的分组整群权重算_精度不足开precision_low(tmp_path: Path) -> None:
    """F1 复审 S1：因果题在环节 4 按功效算过；环节 5 计划写成组差描述（urban，区县整群，加权）。处方说重跑
    `oss power --idea i1`——原先照做（不带 --group-col / --cluster-col）落回总体水平、不算整群：pass 0.064；
    按计划该是组差、区县整群：fail 0.144，precision_low 永远不开。"""
    from oss.api import power as power_api
    from oss.api.analysis import run as run_api

    run = _review_run(tmp_path, {"i1": "causal"})
    first = power_api.power(str(run), idea="i1", effect=0.12, effect_source="测试", cluster_col="county")
    assert first["data"]["mode"] == "power"
    (run / "plan.json").write_text(_gap_plan("i1"), encoding="utf-8")
    run_api(str(run))
    it = next(i for i in route_mod.build(run).items if i.id == "power_mode_mismatch")
    assert it.status == "pending" and "`oss power --idea i1`" in it.advice, it.advice
    assert "会按计划的分组、整群与权重算" in it.advice, it.advice

    d = power_api.power(str(run), idea="i1", effect=0.12, effect_source="测试")["data"]   # 照处方，一个参数不多给
    assert d["mode"] == "precision" and d["verdict"] == "fail", d
    assert d["precision_halfwidth"] == pytest.approx(0.1439, abs=1e-4), d["reason_lines"]
    assert any("按本题计划的分组列 urban、整群列 county、权重 weight 算" in w for w in d["warnings"]), d["warnings"]
    by_id = {i.id: i for i in route_mod.build(run).items}
    assert by_id["power_mode_mismatch"].status == "resolved"
    assert by_id["precision_low"].status == "pending"


def test_处方说按计划的分组整群与权重算_只对描述计划说() -> None:
    desc = next(i for i in route_mod._rules(_plan(), None, _power("power", "pass"), [])
                if i.id == "power_mode_mismatch")
    assert "`oss power --idea i`" in desc.advice and "会按计划的分组、整群与权重算" in desc.advice, desc.advice
    causal = next(i for i in route_mod._rules(_causal_plan(), None, _power("precision", "pass"), [])
                  if i.id == "power_mode_mismatch")
    assert "按计划的分组" not in causal.advice, causal.advice          # 因果计划重跑走功效，不用计划的分组


def test_换题后报告已按新题算_计划还是旧题的_处方说先给新题写计划(tmp_path: Path) -> None:
    """F1 复审 M2-5：i1 真跑过、换到 i2、不写 --idea 的 oss power 正确地算了 i2——route 却说
    「power.json 是 i2 的，计划是 i1 的：重跑 oss power --idea i1」，照做就算回旧题。"""
    from oss.api import anchor as anchor_api
    from oss.api import power as power_api
    from oss.api.analysis import run as run_api

    run = _review_run(tmp_path, {"i1": "descriptive", "i2": "causal"})
    power_api.power(str(run), idea="i1", effect=0.2, effect_source="测试", group_col="urban", cluster_col="county")
    (run / "plan.json").write_text(_gap_plan("i1"), encoding="utf-8")
    run_api(str(run))
    anchor_api.switch(str(run), to="i2", why="用户换题", authorized_by="user")
    assert power_api.power(str(run), effect=0.2, effect_source="测试")["data"]["idea_id"] == "i2"
    rt = route_mod.build(run)
    it = next(i for i in rt.items if i.id == "power_mode_mismatch")
    assert it.priority == "must" and it.kind == "stop" and it.status == "pending", it
    assert it.trigger == "计划是换题前 i1 的；锚点现在是 i2：先给 i2 写计划", it.trigger
    assert "--idea i1" not in it.trigger + it.advice, it.advice
    how = next(b for b in route_mod.blockers(rt) if "power_mode_mismatch" in b).rsplit("——", 1)[1]
    assert "写计划" in how and "重跑 oss power" not in how, how
    assert "power_mode_mismatch" in route_mod.NOT_DISMISSABLE


def test_题号对不上_锚点是计划的题_照旧说重跑() -> None:
    """报告算的是别的题、锚点跟计划是同一题（M2-7：写了 --idea i2 另算一份）：照旧说重跑 --idea 计划的题。"""
    pw = _power("precision", "pass").model_copy(update={"idea_id": "i2"})
    it = next(i for i in route_mod._rules(_plan(idea_id="i1"), None, pw, [], anchor_idea="i1")
              if i.id == "power_mode_mismatch")
    assert "重跑 oss power --idea i1" in it.trigger, it.trigger


def test_说明书写了照处方重跑按计划算_换题后先写计划_关联也不跟n一起用() -> None:
    stages = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "social-research" / "stages"
    notes = (stages / "6-run.notes.md").read_text(encoding="utf-8")
    row = next(x for x in notes.splitlines() if x.startswith("| `power_mode_mismatch`"))
    assert "会按计划的分组、整群与权重算" in row and "先给新题写计划" in row, row
    feas = (stages / "4-feasibility.md").read_text(encoding="utf-8")
    assert "会按计划的分组、整群与权重算" in feas and "先给新题写计划" in feas
    assert "同样不能跟 `--n` 一起用" in feas and "标准化系数" in feas
