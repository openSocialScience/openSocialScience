"""说明书的测试。规则对着运行目录核产物；**故意改坏一条，对应规则必须变红**——那是规则的测试。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from oss.core import skillcheck as sc
from oss.core.contracts import (
    AnalysisPlan, AnalysisResults, Estimand, Estimate, PowerReport, RobustnessSpec, Route, RouteItem, SurveyDesign,
    TraceEvent,
)

ARCHIVE = Path(__file__).parent.parent / "docs" / "runs"


def _step(cmd: str) -> str:
    """留痕里的 step 跟真跑一致：正式分析是 results、探索是 explore。

    「按命令前缀数分析次数」正是被修掉的那个 bug——helper 要是继续写 step="x"，
    规则改成按 step 认之后这些用例就变成空转了。
    """
    if cmd.startswith("oss run"):
        return "explore" if "--explore" in cmd else "results"
    return "x"


def _trace(d: Path, cmds: list[str]) -> None:
    with (d / "trace.jsonl").open("w", encoding="utf-8") as f:
        for i, c in enumerate(cmds, start=1):
            arts = ["plan.json"] if c == "(skill) plan" else []
            f.write(TraceEvent(seq=i, step=_step(c), command=c, outcome="ok",
                               artifacts=arts).to_line() + "\n")


def _review(d: Path, name: str, verdict: str = "WARN", round_: int = 1,
            cross_family: bool = True) -> None:
    (d / name).write_text(json.dumps(
        {"verdict": verdict, "findings": ["[文字] x"], "round": round_,
         "cross_family": cross_family}), encoding="utf-8")


def good_run(d: Path) -> Path:
    """一个照说明书做完的运行目录。"""
    AnalysisPlan(plan_id="p", idea_id="i", research_type="causal", estimator="did_cs", outcome="y", unit="unit", time="year",
                 treat_time="treat_time", cluster_level="unit",
                 estimand=Estimand(unit_quantity="u", target_population="t", assumptions=["平行趋势"], link="l"),
                 robustness=[RobustnessSpec(label="R1", controls=[]), RobustnessSpec(label="R2", cluster_level="year")]
                 ).save(d / "plan.json")
    AnalysisResults(plan_id="p", n=100, estimates={"main": Estimate(term="ATT", coef=1, se=0.2, p=0.001)},
                    diagnostics={"实施批次": "3 批"}).save(d / "results.json")
    PowerReport(idea_id="i", verdict="pass", power=0.9,
                effect_size_source="夹具：假想文献 X (2020) 表 3，d = 0.3").save(d / "power.json")
    Route(items=[RouteItem(id="imbalance", trigger="x", kind="disclose", status="disclosed", why="写了"),
                 RouteItem(id="audit_1", trigger="x", kind="disclose", status="disclosed", why="写了")]).save(d / "route.json")
    (d / "literature.json").write_text(json.dumps({"camps": {"q1": "A", "q2": "B"}}), encoding="utf-8")
    (d / "reading").mkdir()
    (d / "reading" / "index.json").write_text(json.dumps([{"level": "fulltext"}] * 6 + [{"level": "abstract"}] * 4), encoding="utf-8")
    (d / "reading_cards.json").write_text(json.dumps({"cards": [{"paper_id": "W1"}]}), encoding="utf-8")
    (d / "ideas.json").write_text(json.dumps({"ideas": [{"idea_id": "i", "lens": "mechanism-gap"}]}), encoding="utf-8")
    (d / "binding.json").write_text(json.dumps({"bindings": [
        {"construct_name": "info", "status": "proxy", "measures": "internet share", "gap": "access ≠ content"}]}), encoding="utf-8")
    (d / "paper.md").write_text(
        "# T\n\n## Abstract\nEffect {{result:main.coef}} (n = {{result:main.n}}).\n\n## Introduction\nx\n\n"
        "## Data and Methods\n*Table 1. Descriptives.*\n{{table:descriptives}}\n\n## Results\n"
        "*Table 2. Main.*\n{{table:main}}\n\n{{fig:coef}}\n\n*Figure 1. Main.*\n\n{{fig:event}}\n\n*Figure 2. Event.*\n\n"
        "## Robustness\n*Table 3. Robustness.*\n{{table:robustness}}\n\n## Discussion\nx\n", encoding="utf-8")
    _review(d, "review_ideas.json"); _review(d, "review_plan.json", "FAIL")
    _review(d, "review_desk.json"); _review(d, "review_paper_rendered_识别.json")
    _review(d, "review_paper_rendered_替代解释.json"); _review(d, "review_paper_rendered_局限.json")
    _review(d, "review_editor.json")
    _trace(d, ["oss profile d.csv", "oss lit search q", "oss lit fetch", "(skill) plan", "oss power",
               "oss run", "oss route", "oss review", "oss paper check", "oss paper export"])
    return d


ZH_PAPER = (
    "# 教育扩张与生育率\n\n## 摘要\n效应为 {{result:main.coef}}（标准误 {{result:main.se}}），"
    "样本量 {{result:main.n}}。\n\n**关键词**：教育；生育\n\n## 一、引言\n背景。\n\n"
    "## 二、数据与方法\n*表 1. 描述统计。*\n{{table:descriptives}}\n\n## 三、结果\n"
    "*表 2. 主要结果。*\n{{table:main}}\n\n{{fig:coef}}\n\n*图 1. 主要估计。*\n\n"
    "{{fig:event}}\n\n*图 2. 逐年展开。*\n\n## 四、稳健性\n*表 3. 稳健性。*\n{{table:robustness}}\n\n"
    "## 五、讨论\n局限。\n"
)


def test_中文论文的运行目录must级同样全过(tmp_path: Path) -> None:
    """语言换成中文，说明书的规则一条都不该松：表、图注、摘要数字、过程词、新颖性全按中文认。"""
    d = good_run(tmp_path)
    (d / "paper.md").write_text(ZH_PAPER, encoding="utf-8")
    rep = sc.evaluate(d)
    assert rep.must_failed == [], [(i.id, i.detail) for i in rep.must_failed]
    assert _status(rep, "abstract-numbers")[0] == sc.PASS
    # 改坏两处，对应规则要红：去掉表注（图表注规则连表注一起管）、没读全文说「首次」
    (d / "paper.md").write_text(ZH_PAPER.replace("*表 2. 主要结果。*\n", ""), encoding="utf-8")
    assert _status(sc.evaluate(d), "figures-captioned")[0] == sc.FAIL
    (d / "paper.md").write_text(ZH_PAPER.replace("背景。", "本文首次考察这一机制。"), encoding="utf-8")
    import shutil as _sh; _sh.rmtree(d / "reading")
    assert _status(sc.evaluate(d), "novelty-gate")[0] == sc.FAIL


def _break_novelty(d: Path) -> None:
    """说了「首次」，又把精读记录删掉——新颖性门必须响。"""
    (d / "paper.md").write_text((d / "paper.md").read_text(encoding="utf-8").replace(
        "## Introduction\nx", "## Introduction\nNo prior study has examined this."), encoding="utf-8")
    shutil.rmtree(d / "reading")


def _status(rep: sc.Report, rule_id: str) -> tuple[str, str]:
    it = next(i for i in rep.items if i.id == rule_id)
    return it.status, it.detail


def test_照说明书做的目录must级全过(tmp_path: Path) -> None:
    rep = sc.evaluate(good_run(tmp_path))
    assert rep.must_failed == [], [(i.id, i.detail) for i in rep.must_failed]
    should = [i for i in rep.items if i.priority == "should"]
    assert not [i for i in should if i.status == sc.FAIL], \
        [(i.id, i.detail) for i in should if i.status == sc.FAIL]
    # should 级跟 must 级同一口径：n/a 是「这条不适用」，不是没过。但 n/a 不能来者不拒——
    # 只认两条：`causal-diagnostics-cited`——`good_run` 本来就是老结果文件，没有因果骨架的诊断键，
    # 判 n/a 是对的（专门测在 test_因果论文引没引诊断的数）；`descriptive-magnitude`——`good_run`
    # 是因果计划，「describe 的论文引分组描述表」本来就不适用（专门测在 test_describe的论文要引分组描述表）。
    # 别的 should 规则 n/a 说明这条规则本身判错了「适不适用」，不该悄悄放过
    na = {i.id for i in should if i.status == sc.NA}
    assert na <= {"causal-diagnostics-cited", "descriptive-magnitude"}, na


@pytest.mark.parametrize("breaker,rule", [
    (lambda d: AnalysisPlan.load(d / "plan.json").model_copy(update={"estimator": "did"}).save(d / "plan.json"), "staggered-did-cs"),
    (lambda d: PowerReport(idea_id="i", verdict="fail", power=0.3,
                           effect_size_source="夹具出处").save(d / "power.json"), "power-fail-stop"),
    # 效应量抹掉出处：判词整个建立在这个数上，没出处就是没人选过它
    (lambda d: PowerReport(idea_id="i", verdict="pass", power=0.9).save(d / "power.json"), "power-effect-source"),
    (lambda d: (d / "route.json").unlink() or _trace(d, ["oss profile d.csv", "oss power", "oss run"]), "route-after-run"),
    (lambda d: Route(items=[RouteItem(id="power_fail", priority="must", trigger="x", kind="stop")]).save(d / "route.json"), "route-disposed"),
    (lambda d: (d / "paper.md").write_text((d / "paper.md").read_text().replace("{{table:main}}", "prose"), encoding="utf-8"), "tables"),
    (lambda d: (d / "paper.md").write_text((d / "paper.md").read_text().replace("*Figure 1. Main.*", ""), encoding="utf-8"), "figures-captioned"),
    (lambda d: (d / "paper.md").write_text((d / "paper.md").read_text().replace("## Introduction\nx", "## Introduction\nThe audit found X."), encoding="utf-8"), "no-process-language"),
    (lambda d: _break_novelty(d), "novelty-gate"),
    (lambda d: (d / "review_paper_rendered_替代解释.json").unlink(), "three-referees"),
    (lambda d: (d / "review_desk.json").unlink(), "desk-before-referees"),
    (lambda d: (d / "review_editor.json").unlink(), "editor-synthesis"),
    (lambda d: _review(d, "review_paper_rendered_识别.json", "FAIL"), "fail-rereviewed"),
    (lambda d: (d / "review_plan.json").unlink(), "identification-audit"),
    (lambda d: _trace(d, ["oss profile d.csv", "oss run", "oss power", "oss route"]), "power-before-run"),
    (lambda d: (d / "literature.json").write_text(json.dumps({"camps": {"q1": "A"}}), encoding="utf-8"), "lit-camps"),
    (lambda d: (d / "binding.json").write_text(json.dumps({"bindings": [{"construct_name": "info", "status": "proxy"}]}), encoding="utf-8"), "proxy-gap"),
    (lambda d: (d / "reading_cards.json").unlink(), "lit-fetch"),
    (lambda d: (d / "ideas.json").write_text(json.dumps({"ideas": [{"idea_id": "i"}]}), encoding="utf-8"), "ideas-jury"),
    # 锚点冻结时研究类型这一格**在**、但是空串——不是「这一栏还没出现」（那样才不适用，见
    # test_老运行目录锚点没有这一格_声明规则不适用）；good_run 本身没有 results.json 之外的
    # 锚点线索，配上冻结的锚点就不再是「锚点出现之前的老运行目录」，得判不过
    (lambda d: (AnalysisPlan.load(d / "plan.json").model_copy(update={"research_type": ""}).save(d / "plan.json"),
                (d / "run_state.json").write_text(json.dumps({"anchor": {
                    "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
                    "research_type": "", "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")),
     "research-type-declared"),
    (lambda d: (AnalysisPlan.load(d / "plan.json").model_copy(update={"research_type": "descriptive"}).save(d / "plan.json"),
                (d / "paper.md").write_text((d / "paper.md").read_text().replace(
                    "## Discussion\nx", "## Discussion\nThe reform causes higher output."), encoding="utf-8")),
     "causal-wording"),
])
def test_故意改坏一条_对应规则变红(tmp_path: Path, breaker, rule: str) -> None:
    d = good_run(tmp_path)
    breaker(d)
    status, detail = _status(sc.evaluate(d), rule)
    assert status == sc.FAIL, (rule, status, detail)


def _plan(d: Path, **update) -> None:
    AnalysisPlan.load(d / "plan.json").model_copy(update=update).save(d / "plan.json")


def _lock(d: Path) -> None:
    """跟真跑一样把锚点冻上：`oss run` 第一次跑时 `freeze_anchor(**current_anchor(...))`，
    计划上写的研究类型一并冻进锚点——这一格有合法值，类型就锁定了。

    `good_run` 有 results.json、没有 run_state.json，读出来是「锚点出现之前就跑过分析」的
    老运行目录：那种目录里计划上的类型是事后补写的，锁不住，压不过估计量（重放验收
    2026-09-14）。要测「类型压过估计量」，得先像新运行那样有一个锁定了类型的锚点。
    """
    from oss.core import state as state_mod

    (d / "run_state.json").unlink(missing_ok=True)
    state_mod.freeze_anchor(d, **state_mod.current_anchor(d))


#: 锚点冻结时研究类型这一栏还没上线的老运行目录：锚点里**没有** research_type 这一格
_LEGACY_ANCHOR = {"direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
                  "frozen_at": "2026-09-01T00:00:00Z"}


def test_研究类型决定要不要审计_ols_fe下因果结论也要审(tmp_path: Path) -> None:
    """设计稿 4.3：按结论触发，不按方法触发。老计划没写类型的，照旧按估计量兜底。

    类型要**锁定**了才压得过估计量，所以前两步都像新运行那样冻一次锚点（`_lock`）；
    第三步是没写类型、也没有锚点的老计划。
    """
    d = good_run(tmp_path)
    (d / "review_plan.json").unlink()
    _plan(d, research_type="descriptive")
    _lock(d)
    assert _status(sc.evaluate(d), "identification-audit")[0] == sc.NA
    _plan(d, research_type="causal", estimator="ols_fe", treat_time="")
    _lock(d)
    assert _status(sc.evaluate(d), "identification-audit")[0] == sc.FAIL, "ols_fe 下因果结论也要审计"
    _plan(d, research_type="", estimator="did_cs", treat_time="treat_time")
    (d / "run_state.json").unlink()
    assert _status(sc.evaluate(d), "identification-audit")[0] == sc.FAIL, "老计划按估计量兜底"


def test_锚点出现之前的老目录_事后补写描述压不过估计量(tmp_path: Path) -> None:
    """重放验收 2026-09-14：锚点出现之前就跑完的老目录（有 results.json、没有 run_state.json），
    事后在 plan.json 上补一个 descriptive、不重跑——11 个真目录里识别审计 9 个 pass→n/a、
    2 个 FAIL→n/a（一条本该判不过的 must 就这么没了），声明规则反倒判过。
    这种目录里类型锁不住：压不过估计量，声明规则也不给判过。"""
    d = good_run(tmp_path)
    (d / "review_plan.json").unlink()
    _plan(d, research_type="descriptive")               # did_cs + treat_time
    assert not (d / "run_state.json").exists() and (d / "results.json").exists()
    rep = sc.evaluate(d)
    assert _status(rep, "identification-audit") == (sc.FAIL, "因果研究没经过识别审计（没有 review_plan.json）")
    status, detail = _status(rep, "research-type-declared")
    assert status == sc.NA and "锁不住" in detail, detail


def test_legacy锚点的老目录_事后补写描述压不过估计量(tmp_path: Path) -> None:
    """锚点冻结时还没有研究类型这一格（legacy）——`refill_plan_sha` 永远不补这一格，
    事后写上的类型永远锁不上：跟锚点出现之前的老目录同一条口径。"""
    d = good_run(tmp_path)
    (d / "review_plan.json").unlink()
    _plan(d, research_type="descriptive", estimator="event_study")
    (d / "run_state.json").write_text(json.dumps({"anchor": _LEGACY_ANCHOR}), encoding="utf-8")
    rep = sc.evaluate(d)
    assert _status(rep, "identification-audit")[0] == sc.FAIL
    status, detail = _status(rep, "research-type-declared")
    assert status == sc.NA and "锁不住" in detail, detail


def test_外部导入结果的目录_补写的类型同样锁不住(tmp_path: Path) -> None:
    """`oss results import` 不冻锚点：有 results.json、没有冻结的锚点，跟锚点出现之前的老目录
    一个形状——类型同样锁不住，只是说法换成「外部导入」，不说它是老运行目录。"""
    d = good_run(tmp_path)
    res = json.loads((d / "results.json").read_text(encoding="utf-8"))
    res["imported"] = {"raw_file": "results_raw.txt", "software": "", "imported_at": "2026-09-14T00:00:00+00:00"}
    (d / "results.json").write_text(json.dumps(res), encoding="utf-8")
    _plan(d, research_type="descriptive")
    status, detail = _status(sc.evaluate(d), "research-type-declared")
    assert status == sc.NA and "外部导入" in detail and "锁不住" in detail, detail


def test_锁定的描述照旧压过估计量(tmp_path: Path) -> None:
    """新运行：冻锚点那一刻计划就写着 descriptive——锁定了，did_cs 也不用审（这一条不变）。"""
    d = good_run(tmp_path)
    (d / "review_plan.json").unlink()
    _plan(d, research_type="descriptive")
    _lock(d)
    rep = sc.evaluate(d)
    assert _status(rep, "identification-audit") == (sc.NA, "研究类型不是因果")
    assert _status(rep, "research-type-declared")[0] == sc.PASS


def test_还没跑过的新目录_声明的描述压过估计量(tmp_path: Path) -> None:
    """没有冻结的锚点、也没有 results.json：一次都还没跑，计划上的类型就是事先定的。"""
    d = good_run(tmp_path)
    (d / "review_plan.json").unlink()
    (d / "results.json").unlink()
    _plan(d, research_type="descriptive")
    rep = sc.evaluate(d)
    assert _status(rep, "identification-audit") == (sc.NA, "研究类型不是因果")
    assert _status(rep, "research-type-declared")[0] == sc.PASS


def test_锚点这一格空着_换题后还没跑_描述压不过估计量(tmp_path: Path) -> None:
    """新式锚点、这一格空、plan_sha 也空（授权换题之后新计划还没跑过）——`declared_unlocked`
    不认这个状态（下一次 oss run 会正常锁上，不算「看过结果才补」），但这一刻类型还没锁：
    压不过估计量。"""
    d = good_run(tmp_path)
    (d / "review_plan.json").unlink()
    _plan(d, research_type="descriptive")
    (d / "run_state.json").write_text(json.dumps({"anchor": {
        **_LEGACY_ANCHOR, "plan_sha": "", "research_type": ""}}), encoding="utf-8")
    assert _status(sc.evaluate(d), "identification-audit")[0] == sc.FAIL


def test_老计划没写类型_估计量不是因果设计时的说法(tmp_path: Path) -> None:
    d = good_run(tmp_path)
    _plan(d, research_type="", estimator="ols_fe", treat_time="")
    assert _status(sc.evaluate(d), "identification-audit") == (sc.NA, "老计划没写研究类型：估计量不是因果设计")


def test_锚点冻结时是因果的_降级后仍要审计(tmp_path: Path) -> None:
    """审计是降级的前提：锚点冻的是因果、计划已降成描述，识别审计照样要有。

    估计量换成 `ols_fe` 且 `treat_time` 留空——单看估计量兜底会判「不是因果」，
    这里要证明是**锚点那一格**压过了估计量，不是估计量恰好也说因果凑巧过了。
    """
    d = good_run(tmp_path)
    (d / "review_plan.json").unlink()
    AnalysisPlan.load(d / "plan.json").model_copy(
        update={"research_type": "descriptive", "estimator": "ols_fe", "treat_time": ""}).save(d / "plan.json")
    (d / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
        "research_type": "causal", "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")
    assert _status(sc.evaluate(d), "identification-audit")[0] == sc.FAIL, \
        "锚点写着因果，估计量兜底判不出因果也要审——是锚点那一格压过了估计量"
    # 反过来：锚点与计划都写着描述，估计量却长得像因果设计（did_cs + treat_time）——
    # 类型照样压过估计量，不该被误判成因果去审计。
    AnalysisPlan.load(d / "plan.json").model_copy(
        update={"research_type": "descriptive", "estimator": "did_cs", "treat_time": "treat_time"}).save(d / "plan.json")
    (d / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
        "research_type": "descriptive", "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")
    assert _status(sc.evaluate(d), "identification-audit")[0] == sc.NA, \
        "类型写着描述，估计量像因果也不该判要审——类型压过估计量"


def test_老运行目录锚点没有这一格_声明规则不适用(tmp_path: Path) -> None:
    d = good_run(tmp_path)
    AnalysisPlan.load(d / "plan.json").model_copy(update={"research_type": ""}).save(d / "plan.json")
    (d / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
        "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")
    assert _status(sc.evaluate(d), "research-type-declared")[0] == sc.NA


def test_老运行目录没有锚点却跑过分析_声明规则不适用(tmp_path: Path) -> None:
    """在锚点功能出现之前就跑完的运行目录：**没有** run_state.json，但 results.json 在——
    跟「老锚点冻结时没有这一格」是同一条口径：看不出来不等于违规。"""
    d = good_run(tmp_path)
    AnalysisPlan.load(d / "plan.json").model_copy(update={"research_type": ""}).save(d / "plan.json")
    status, detail = _status(sc.evaluate(d), "research-type-declared")
    assert status == sc.NA, detail


def test_新运行还没跑分析_没声明类型照样判不过(tmp_path: Path) -> None:
    """没有 results.json 说明这确实是个新运行——「看不出来」的例外不该盖住「新运行没写」。"""
    d = good_run(tmp_path)
    AnalysisPlan.load(d / "plan.json").model_copy(update={"research_type": ""}).save(d / "plan.json")
    (d / "results.json").unlink()
    status, detail = _status(sc.evaluate(d), "research-type-declared")
    assert status == sc.FAIL, detail


def test_锚点写着这一格却是空串_算声明过又撤回_判不过(tmp_path: Path) -> None:
    """锚点冻结时研究类型这一格**在**、但是空串——是新运行没写，不是「这一栏还没出现」；
    跟「anchor 里根本没有这一格」（老运行目录）不是一回事，判不过。"""
    d = good_run(tmp_path)
    AnalysisPlan.load(d / "plan.json").model_copy(update={"research_type": ""}).save(d / "plan.json")
    (d / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
        "research_type": "", "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")
    status, detail = _status(sc.evaluate(d), "research-type-declared")
    assert status == sc.FAIL, detail


def test_类型是看过结果才补上的_声明规则判不过(tmp_path: Path) -> None:
    """用户 2026-09-14 决定 2A：锚点里有一行门自己写的 `research_type_declared_late`——
    类型是在看过结果之后才补的，即使计划与锚点这一格都写着合法值，也要判不过。"""
    d = good_run(tmp_path)
    AnalysisPlan.load(d / "plan.json").model_copy(update={"research_type": "descriptive"}).save(d / "plan.json")
    (d / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
        "research_type": "descriptive", "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")
    with (d / "trace.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"seq": 99, "step": "research_type_declared_late", "command": "oss run",
                            "outcome": "ok", "args": {"research_type": "descriptive"}}) + "\n")
    status, detail = _status(sc.evaluate(d), "research-type-declared")
    assert status == sc.FAIL, detail


def test_没有看过结果才补的留痕_声明规则照旧判过(tmp_path: Path) -> None:
    """同样的计划与锚点，没有 `research_type_declared_late` 这一行——判过。"""
    d = good_run(tmp_path)
    AnalysisPlan.load(d / "plan.json").model_copy(update={"research_type": "descriptive"}).save(d / "plan.json")
    (d / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
        "research_type": "descriptive", "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")
    status, detail = _status(sc.evaluate(d), "research-type-declared")
    assert status == sc.PASS, detail


def test_并集取合法值_非法或非因果的锚点值压不过计划里的因果(tmp_path: Path) -> None:
    """`is_causal` 取锚点与计划两处**合法**类型的并集，任一处是 causal 就要审计——
    不能让一个无效、或者只是不同的锚点值，把计划里明明白白的 causal 声明压没。"""
    # A：锚点合法但类型不同（descriptive），计划才是 causal——并集里两个都在，占一个 causal 就该审。
    a_dir = good_run(tmp_path / "a")
    (a_dir / "review_plan.json").unlink()
    (a_dir / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
        "research_type": "descriptive", "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")
    status, detail = _status(sc.evaluate(a_dir), "identification-audit")
    assert status == sc.FAIL, ("计划的 causal 不该被锚点的合法值压没", detail)

    # B：锚点是手改坏的非法值（大小写不对），计划才是权威的合法值。
    b_dir = good_run(tmp_path / "b")
    (b_dir / "review_plan.json").unlink()
    (b_dir / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
        "research_type": "Causal", "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")
    status, detail = _status(sc.evaluate(b_dir), "identification-audit")
    assert status == sc.FAIL, ("锚点手改坏的非法值不该盖掉计划里合法的 causal", detail)

    # B2：两处都不合法（锚点 none、计划空串），并集为空，退回估计量兜底——good_run 的估计量是 did_cs。
    b2_dir = good_run(tmp_path / "b2")
    (b2_dir / "review_plan.json").unlink()
    AnalysisPlan.load(b2_dir / "plan.json").model_copy(update={"research_type": ""}).save(b2_dir / "plan.json")
    (b2_dir / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
        "research_type": "none", "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")
    status, detail = _status(sc.evaluate(b2_dir), "identification-audit")
    assert status == sc.FAIL, ("两处都不是合法值，要退回估计量兜底", detail)


def test_eval不许动坏掉的状态文件(tmp_path: Path) -> None:
    """`run_state.json` 读不出来时，`causal-wording` 不许替它按 `wording_scope` 走一遍——
    那条链路会经过 `state.load()`，读不出来的文件会被就地改名成 `run_state.corrupt.json`。
    `oss eval` 只读不改，判一条规则不能顺手把运行目录写脏。"""
    d = good_run(tmp_path)
    AnalysisPlan.load(d / "plan.json").model_copy(update={"research_type": "descriptive"}).save(d / "plan.json")
    (d / "run_state.json").write_text("{not json", encoding="utf-8")
    before = sorted(p.name for p in d.iterdir())
    status, detail = _status(sc.evaluate(d), "causal-wording")
    after = sorted(p.name for p in d.iterdir())
    assert status == sc.FAIL and "run_state.json" in detail, detail
    assert after == before, (before, after)
    assert (d / "run_state.json").exists()
    assert not (d / "run_state.corrupt.json").exists()


def test_合规降级过的论文照样不许有硬因果说法(tmp_path: Path) -> None:
    """降级站得住不等于正文可以硬因果——`causal-wording` 只看当前类型，不看降级站不站得住。"""
    d = good_run(tmp_path)
    AnalysisPlan.load(d / "plan.json").model_copy(update={"research_type": "descriptive"}).save(d / "plan.json")
    (d / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
        "research_type": "causal", "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")
    with (d / "trace.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"seq": 100, "outcome": "ok", "step": "review",
                            "command": "oss review --identification",
                            "artifacts": ["review_plan.json"], "note": "FAIL by x"}) + "\n")
        f.write(json.dumps({"seq": 101, "outcome": "ok", "step": "design_alternatives_exhausted",
                            "command": "oss trace save",
                            "note": "换窗口、换对照组都试过，都不可行"}) + "\n")
    (d / "paper.md").write_text((d / "paper.md").read_text(encoding="utf-8").replace(
        "## Discussion\nx", "## Discussion\nThe reform causes higher output."), encoding="utf-8")
    status, detail = _status(sc.evaluate(d), "causal-wording")
    assert status == sc.FAIL, detail


def test_因果计划的论文写因果说法不归这条规则管(tmp_path: Path) -> None:
    """`good_run` 本来就是 causal 计划——正文里有因果说法是识别审计与替代解释审稿人的事，
    不该被 `causal-wording`（只管非因果类型）拦。"""
    d = good_run(tmp_path)
    (d / "paper.md").write_text((d / "paper.md").read_text(encoding="utf-8").replace(
        "## Discussion\nx", "## Discussion\nThe reform causes higher output."), encoding="utf-8")
    status, detail = _status(sc.evaluate(d), "causal-wording")
    assert status == sc.NA, detail


def test_审计穷尽却仍标因果的论文_causal_wording改判FAIL(tmp_path: Path) -> None:
    """Task 11c、用户 2026-09-14 决定 4A：`plan.json` 没跟着从 causal 改成 descriptive，
    但识别审计判 FAIL、替代设计也穷尽了——`causal-wording` 不该再放行（以前判 n/a）。"""
    d = good_run(tmp_path)
    with (d / "trace.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"seq": 100, "outcome": "ok", "step": "review",
                            "command": "oss review --identification",
                            "artifacts": ["review_plan.json"], "note": "FAIL by x"}) + "\n")
        f.write(json.dumps({"seq": 101, "outcome": "ok", "step": "design_alternatives_exhausted",
                            "command": "oss trace save",
                            "note": "换窗口、换对照组都试过，都不可行"}) + "\n")
    (d / "paper.md").write_text((d / "paper.md").read_text(encoding="utf-8").replace(
        "## Discussion\nx", "## Discussion\nThe reform causes higher output."), encoding="utf-8")
    status, detail = _status(sc.evaluate(d), "causal-wording")
    assert status == sc.FAIL, detail


def test_只有审计FAIL没穷尽_causal_wording仍是na(tmp_path: Path) -> None:
    """只留一条 FAIL 的留痕，没有 `design_alternatives_exhausted`——`identification_gate`
    停在 `fail_untried`，不满足 4A「穷尽」这一条，仍旧不归这条规则管。"""
    d = good_run(tmp_path)
    with (d / "trace.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"seq": 100, "outcome": "ok", "step": "review",
                            "command": "oss review --identification",
                            "artifacts": ["review_plan.json"], "note": "FAIL by x"}) + "\n")
    (d / "paper.md").write_text((d / "paper.md").read_text(encoding="utf-8").replace(
        "## Discussion\nx", "## Discussion\nThe reform causes higher output."), encoding="utf-8")
    status, detail = _status(sc.evaluate(d), "causal-wording")
    assert status == sc.NA, detail


def test_开方之后再探索不算又跑了一次正式分析(tmp_path: Path) -> None:
    """`oss run --explore --for …` 的命令也以「oss run」开头。按前缀数，
    一次合法的探索会被当成新一次正式分析，route-after-run 在探索之后误报。"""
    d = good_run(tmp_path)
    _trace(d, ["oss profile d.csv", "oss power", "oss run", "oss route",
               "oss run --explore --for pretrend_fail"])
    status, detail = _status(sc.evaluate(d), "route-after-run")
    assert status == sc.PASS, detail


def test_功效之前的探索不算分析跑在功效之前(tmp_path: Path) -> None:
    """探索是开方之后的诊断动作，可以发生在算功效之前——它不是「事前功效」的违规。"""
    d = good_run(tmp_path)
    _trace(d, ["oss run --explore --for pretrend_fail", "oss power", "oss run", "oss route"])
    status, detail = _status(sc.evaluate(d), "power-before-run")
    assert status == sc.PASS, detail


def test_没有产物的规则判不适用而不是通过(tmp_path: Path) -> None:
    rep = sc.evaluate(tmp_path)
    assert all(i.status in (sc.NA, sc.FAIL) for i in rep.items)
    assert rep.must_failed == [] or all(i.id in ("lit-fetch",) for i in rep.must_failed)


def test_验收跑的归档被规则抓到评审点名的问题() -> None:
    src = ARCHIVE / "r20260825_qog_internet-paper.md"
    if not src.exists():
        pytest.skip("归档不在")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for f in ("plan", "results", "power", "review_plan", "ideas", "binding", "reading_cards", "review_ideas"):
            p = ARCHIVE / f"r20260825_qog_internet-{f}.json"
            if p.exists():
                shutil.copy(p, d / f"{f}.json")
        shutil.copy(src, d / "paper.md")
        shutil.copy(ARCHIVE / "r20260825_qog_internet-trace.jsonl", d / "trace.jsonl")
        for name in ARCHIVE.glob("r20260825_qog_internet-review_paper_rendered_*.json"):
            shutil.copy(name, d / name.name.replace("r20260825_qog_internet-", ""))
        rep = sc.evaluate(d)
        failed = {i.id for i in rep.must_failed}
        for expected in ("power-fail-stop", "route-after-run", "tables", "no-process-language", "novelty-gate", "three-referees"):
            assert expected in failed, (expected, [(i.id, i.detail) for i in rep.items])


# ══════════════════════════════ 自审要在论文里说出来


def test_自审但论文没交代要判不过(tmp_path: Path) -> None:
    """2026-09-07 起缺省不再拦「审稿人与执行者同家族」——门槛压在跑起来之前，
    新用户凑不齐两家模型就看不见这套东西好在哪。**但放松拒绝不等于放松披露。**"""
    d = good_run(tmp_path)
    _review(d, "review_desk.json", cross_family=False)
    rep = sc.evaluate(d)
    bad = [i for i in rep.items if i.id == "self-review-disclosed"]
    assert bad and bad[0].status == sc.FAIL, [(i.id, i.detail) for i in rep.items]
    assert "不构成独立验证" in bad[0].detail


def test_自审且论文交代了就过(tmp_path: Path) -> None:
    d = good_run(tmp_path)
    _review(d, "review_desk.json", cross_family=False)
    p = d / "paper.md"
    p.write_text(p.read_text(encoding="utf-8")
                 + "\n\n本文的审稿由同一模型完成，不构成独立验证。\n", encoding="utf-8")
    got = [i for i in sc.evaluate(d).items if i.id == "self-review-disclosed"]
    assert got and got[0].status == sc.PASS, got[0].detail


def test_跨模型时这条不要求写(tmp_path: Path) -> None:
    """跨模型审过的稿子不该被逼着写一句「本文是自审」——那是假话。"""
    got = [i for i in sc.evaluate(good_run(tmp_path)).items if i.id == "self-review-disclosed"]
    assert got and got[0].status == sc.PASS and "跨模型" in got[0].detail


# ══════════════════════════════ 自带想法模式不许被自己进


def _byo(d: Path, source: str = "user", n: int = 1) -> None:
    """自带想法模式的形状：候选只有一条、透镜是 user-idea。"""
    (d / "ideas.json").write_text(json.dumps({"ideas": [
        {"idea_id": f"idea_{i:03d}", "lens": "user-idea", "source": source}
        for i in range(1, n + 1)]}, ensure_ascii=False), encoding="utf-8")


def _input_note(d: Path, note: str) -> None:
    rows = (d / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    rows.insert(0, TraceEvent(seq=0, step="input", command="/social-research",
                              outcome="ok", note=note).to_line())
    (d / "trace.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_没带idea却进了自带想法模式要判不过(tmp_path: Path) -> None:
    """WorkBuddy 验收跑 r20260907_1938 的形状：用户只给了一句方向，
    模型自己认定「这算你带了题」，于是**跳过十个透镜、去重、可行性筛这一整套**，
    抓着那一句直接送审——陪审团一砍就一个不剩。

    而管这事的规则只问「送审没有、记透镜没有」：1 个候选、记了透镜、审过了，**全过**。
    """
    d = good_run(tmp_path)
    _byo(d)
    _input_note(d, "教育扩张是否削弱了家庭背景对地位获得的影响 --- data: pack:gss")
    got = [i for i in sc.evaluate(d).items if i.id == "byo-idea-declared"]
    assert got and got[0].status == sc.FAIL, got and got[0].detail
    assert "--- idea:" in got[0].detail


def test_真带了idea就放行(tmp_path: Path) -> None:
    d = good_run(tmp_path)
    _byo(d)
    _input_note(d, "错误定价与供应链风险 --- data: panel.csv --- idea: 错误定价越严重，供应链风险越高")
    got = [i for i in sc.evaluate(d).items if i.id == "byo-idea-declared"]
    assert got and got[0].status == sc.PASS, got[0].detail


def test_模型自己出题的运行不受这条约束(tmp_path: Path) -> None:
    """透镜是十个里的正常那几个，说明出题流程走过了——这条不适用。"""
    d = good_run(tmp_path)
    _input_note(d, "教育扩张与生育率 --- data: pack:qog")
    got = [i for i in sc.evaluate(d).items if i.id == "byo-idea-declared"]
    assert got and got[0].status == sc.NA, got[0].detail


def test_没记原始输入时不判(tmp_path: Path) -> None:
    """**看不出来 ≠ 违规。**老运行目录没有 input 留痕，不能因此判它不过。"""
    d = good_run(tmp_path)
    _byo(d)
    got = [i for i in sc.evaluate(d).items if i.id == "byo-idea-declared"]
    assert got and got[0].status == sc.NA and "没记" in got[0].detail


# ══════════════════════════════ 未决意见要逐条交代，不是写段展望


def _loop(d: Path, **counts) -> None:
    base = {"design_open": 0, "data_open": 0, "text_open": 0, "new_open": 0}
    (d / "loop_state.json").write_text(json.dumps(
        {"round": 2, "max_rounds": 2, "rounds": [{"round": 2, **base, **counts}]},
        ensure_ascii=False), encoding="utf-8")


OPEN_SEC = ("\n\n## 尚未解决的问题\n\n以下几条用现有数据无法排除。\n\n"
            "第一，共期的宏观冲击。……\n\n第二，人口结构的惯性。……\n")


def test_有未决意见却没那一节要判不过(tmp_path: Path) -> None:
    """r20260830_mode1 的真实形状：9 条未决，论文里根本没有这一节，
    只在讨论里有一句「Open questions remain」，接着聊了两个未来方向。"""
    d = good_run(tmp_path)
    _loop(d, new_open=9)
    got = [i for i in sc.evaluate(d).items if i.id == "open-items-accounted"]
    assert got and got[0].status == sc.FAIL, got and got[0].detail
    assert "9" in got[0].detail


def test_有节但没逐条也判不过(tmp_path: Path) -> None:
    """small / 2030 / edufert 三次的形状：节在，写的是一段泛泛的「未来研究方向」，
    **一条具体的未决意见都没点**。5 到 15 条就这么过去了。"""
    d = good_run(tmp_path)
    _loop(d, text_open=8)
    p = d / "paper.md"
    p.write_text(p.read_text(encoding="utf-8")
                 + "\n\n## Open questions\n\nTwo extensions would sharpen the identification.\n",
                 encoding="utf-8")
    got = [i for i in sc.evaluate(d).items if i.id == "open-items-accounted"]
    assert got and got[0].status == sc.FAIL
    assert "逐条" in got[0].detail


def test_逐条写了就过并报出两个数(tmp_path: Path) -> None:
    d = good_run(tmp_path)
    _loop(d, new_open=1)
    p = d / "paper.md"
    p.write_text(p.read_text(encoding="utf-8") + OPEN_SEC, encoding="utf-8")
    got = [i for i in sc.evaluate(d).items if i.id == "open-items-accounted"]
    assert got and got[0].status == sc.PASS, got[0].detail
    assert "1" in got[0].detail and "2" in got[0].detail


def test_没有未决意见时不要求写(tmp_path: Path) -> None:
    """没剩事就别逼人写一节「尚未解决」——那是凑数。"""
    d = good_run(tmp_path)
    _loop(d)
    got = [i for i in sc.evaluate(d).items if i.id == "open-items-accounted"]
    assert got and got[0].status == sc.PASS and "没有未决" in got[0].detail


def test_稳健性上限放到12_原来在惩罚好做法(tmp_path) -> None:
    """三次互不相通的自由跑各自锁了 8、9、10 项，**全被判不合格**；
    作为参照的那篇已发表论文锁了 13 项。

    更要紧的是那 9 项正是价值所在：其中一次拿到了显著的主结果，
    靠自己的稳健性套件发现「加上教育控制就不显著、只留受雇者符号还翻」，
    于是在摘要里放弃了那个发现。**清单短一点，那次发现就不会发生。**

    这条要挡的从来不是「项数多」，是堆一墙没人读的检验。
    """
    import json as _json
    from oss.core import skillcheck as sc

    def plan_with(n: int):
        (tmp_path / "plan.json").write_text(_json.dumps({
            "plan_id": "p", "idea_id": "i", "estimator": "ols_fe", "outcome": "y",
            "treatment": "x", "robustness": [
                {"label": f"R{i}", "executable": True} for i in range(n)]}), encoding="utf-8")
        return sc.r_robustness_locked(sc.RunView(tmp_path))

    assert plan_with(9)[0] == sc.PASS, "真跑里 8–10 项是常态"
    assert plan_with(12)[0] == sc.PASS
    assert plan_with(13)[0] == sc.FAIL, "超过 12 才拦"
    assert plan_with(3)[0] == sc.PASS


def test_因果计划没写估计量三步_合规判不过(tmp_path: Path) -> None:
    d = good_run(tmp_path)
    assert _status(sc.evaluate(d), "causal-estimand")[0] == sc.PASS
    _plan(d, estimand=None)
    assert _status(sc.evaluate(d), "causal-estimand")[0] == sc.FAIL


def test_因果论文引没引诊断的数(tmp_path: Path) -> None:
    d = good_run(tmp_path)
    assert _status(sc.evaluate(d), "causal-diagnostics-cited")[0] == sc.NA   # 老结果文件没有这些键
    r = json.loads((d / "results.json").read_text(encoding="utf-8"))
    r["diagnostics"]["预检功效"] = "0.62"
    (d / "results.json").write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    assert _status(sc.evaluate(d), "causal-diagnostics-cited")[0] == sc.FAIL
    (d / "paper.md").write_text((d / "paper.md").read_text(encoding="utf-8")
                                + "\n预检功效 {{diag:预检功效}}。\n", encoding="utf-8")
    assert _status(sc.evaluate(d), "causal-diagnostics-cited")[0] == sc.PASS


# ══════════════════════════════ 调查层两条合规规则


def _survey_profile(d: Path, likely: bool = True) -> None:
    (d / "profile.json").write_text(json.dumps({"survey": {"likely": likely, "weight_candidates": ["w"]}}),
                                    encoding="utf-8")


def test_有调查信号没声明抽样设计_合规判不过(tmp_path: Path) -> None:
    d = good_run(tmp_path)
    assert _status(sc.evaluate(d), "survey-design-declared")[0] == sc.NA        # good_run 没有画像
    _survey_profile(d)
    assert _status(sc.evaluate(d), "survey-design-declared")[0] == sc.FAIL
    # `_plan` 走 `model_copy(update=...)`，不校验：塞字典能落盘但 pydantic 会警告，所以传模型本身
    _plan(d, survey=SurveyDesign(design_source="登记：CGSS 2017", weight_use="model", psu="county"))
    assert _status(sc.evaluate(d), "survey-design-declared")[0] == sc.PASS


def test_描述性估计没加权_合规判不过(tmp_path: Path) -> None:
    d = good_run(tmp_path)
    _survey_profile(d)
    _plan(d, survey=SurveyDesign(design_source="x", weight_use="descriptive"), weights="")
    assert _status(sc.evaluate(d), "survey-descriptive-weighted")[0] == sc.FAIL
    _plan(d, weights="w")
    assert _status(sc.evaluate(d), "survey-descriptive-weighted")[0] == sc.PASS


# ══════════════════════════════ 描述骨架两条合规规则（实施计划 2026-09-15 Task 7）


def _power_mode(d: Path, mode: str, verdict: str) -> None:
    pw = json.loads((d / "power.json").read_text(encoding="utf-8"))
    pw.update(verdict=verdict, mode=mode)
    (d / "power.json").write_text(json.dumps(pw, ensure_ascii=False), encoding="utf-8")


def test_精度模式的fail_功效停的规则判不适用(tmp_path: Path) -> None:
    """描述类计划（或老计划没写类型）按精度判：精度不足不停，这条判不适用。
    `good_run` 本身是因果计划——因果计划配精度报告是另一回事，要判不过（见下一个测试）。"""
    d = good_run(tmp_path)
    _power_mode(d, "precision", "fail")
    _plan(d, research_type="descriptive")
    assert _status(sc.evaluate(d), "power-fail-stop")[0] == sc.NA
    _plan(d, research_type="")
    assert _status(sc.evaluate(d), "power-fail-stop")[0] == sc.NA


@pytest.mark.parametrize("verdict", ["fail", "gray", "pass"])
def test_计划是因果_功效报告按精度算_功效停的规则判不过(tmp_path: Path, verdict: str) -> None:
    """复审 2026-09-15：环节 4 的 oss power 在计划之前跑、按 idea 的类型分路，描述类 idea 得到精度报告；
    环节 5 计划写成因果，就再没有东西要功效了。精度判什么都一样判不过；重跑成功效模式后回到平常的功效分支。"""
    d = good_run(tmp_path)                                        # 因果计划（did_cs）
    _power_mode(d, "precision", verdict)
    status, detail = _status(sc.evaluate(d), "power-fail-stop")
    assert status == sc.FAIL and "causal" in detail and "重跑 oss power" in detail, detail
    _power_mode(d, "power", "pass")
    assert _status(sc.evaluate(d), "power-fail-stop")[0] == sc.PASS


def test_因果降为描述_锚点留着causal_精度报告判不适用(tmp_path: Path) -> None:
    """只看计划上写的类型、不看锚点：从因果降为描述的计划，锚点里还留着 causal，
    重跑 oss power 得到精度报告之后不许判不过。"""
    d = good_run(tmp_path)
    _plan(d, research_type="descriptive")
    (d / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i", "plan_sha": "x", "source": "model",
        "research_type": "causal", "frozen_at": "2026-09-13T00:00:00Z"}}), encoding="utf-8")
    _power_mode(d, "precision", "fail")
    assert "causal" in sc.RunView(d).declared_types()              # 锚点里确实还留着 causal
    assert _status(sc.evaluate(d), "power-fail-stop")[0] == sc.NA


def test_描述类计划没写描述量_合规判不过(tmp_path: Path) -> None:
    d = good_run(tmp_path)
    _plan(d, research_type="descriptive", estimand=None)
    assert _status(sc.evaluate(d), "descriptive-estimand")[0] == sc.FAIL


def test_describe的论文要引分组描述表(tmp_path: Path) -> None:
    from oss.core.contracts import DescriptionSpec, Estimand
    d = good_run(tmp_path)
    _plan(d, research_type="descriptive", estimator="describe",
          estimand=Estimand(unit_quantity="u", target_population="t", assumptions=["a"], link="l"),
          description=DescriptionSpec(kind="gap", groups=["unit"]))
    assert _status(sc.evaluate(d), "descriptive-magnitude")[0] == sc.FAIL
    (d / "paper.md").write_text((d / "paper.md").read_text(encoding="utf-8") + "\n{{table:groups}}\n", encoding="utf-8")
    assert _status(sc.evaluate(d), "descriptive-magnitude")[0] == sc.PASS
