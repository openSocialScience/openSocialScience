"""测量骨架 Task 3 / Task 4 / Task 5：造尺子的三道门（设计稿 5.1）、用尺子的两道门（5.2），
外加第二、第三个绕行洞（实施计划「零之二」第 ②③ 条），以及 Task 5 的两件——
代理差距三栏对测量类必填（5.3）、M4 的论文期核实（登记了等价检验，那份产出得真的在）。

这一批的第一原则（设计稿第二节）：**门只核「声明了什么 + 产出在不在」，不读论文里的论证**。
2026-09-16 的评测量出来的东西直接决定这里怎么写——靠认关键词的检查在没见过的写法上只有 6%，
而问「有没有做过某一步」这类结构判据是 100%。

所以五道门一个字都不读论文的论证：

- M1 / M2（`validate_plan`）核的是计划里的两栏对不对得上：声明了哪几类效度证据（`validity_evidence`）、
  哪一类的产出真的登记了（`evidence_outputs`）
- M3（`manuscript` 的纯函数）核的是两件结构上的事：① 产出里有没有效度证据；
  ② 论文里有没有引用**信度那份产出**的占位符。**论文把信度写成「内部一致性」还是
  「Cronbach's α」还是「题目间一致」，这道门一个字都不看**——下面三条「换说法照样拦」就是这条的验收
- M4（`validate_plan` + `route`）核的是登记里有没有测量等价检验那一行；**不停**，开 must 级处方给两条出路
- M5（`validate_plan`）核的是「这一列按代理处理了没有」——判据全从画像里那一列的**形状**来，
  一个字的词表都没有：名字叫 `hours_week`、`q12a` 还是 `v301` 是同一件事
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oss.api import errors
from oss.api import paper as paper_api
from oss.core import binding as binding_mod
from oss.core import manuscript
from oss.core import route as route_mod
from oss.core import skillcheck
from oss.core.analysis import validate_plan
from oss.core.contracts import (
    AnalysisPlan,
    AnalysisResults,
    BindingReport,
    DescriptionSpec,
    Estimate,
    GroupCell,
    MeasurementSpec,
    PowerReport,
    SpecSummary,
    VariableBinding,
)
from oss.core.findings import code_of
from oss.core.profiler import profile_file

FIX = Path(__file__).parent / "fixtures"


# ─────────────────────────── 夹具


def _profile():
    return profile_file(FIX / "tiny.csv")


def _plan(**measurement) -> AnalysisPlan:
    """一份测量类计划：只写测量块要用的那几栏，别的留缺省（列名取 tiny.csv 里的）。"""
    base = dict(construct="对陌生人的一般信任", indicators=["income", "edu_years", "urban"])
    return AnalysisPlan(plan_id="p", idea_id="i1", estimator="describe", outcome="income",
                        research_type="measurement",
                        measurement=MeasurementSpec(**{**base, **measurement}))


def _codes(plan: AnalysisPlan) -> set[str]:
    problems, _ = validate_plan(plan, _profile())
    return {code_of(p) for p in problems}


def _warn_codes(plan: AnalysisPlan, profile=None, binding=None, frame=None) -> set[str]:
    _, warnings = validate_plan(plan, profile or _profile(), frame, binding=binding)
    return {code_of(w) for w in warnings}


# ═══════════════════════ M1：效度证据不能只有信度


def test_只声明信度类的证据_拒跑() -> None:
    """造尺子的产出登记里只有信度系数——**信度不是效度**，一类效度证据都没有就不给跑。

    判的是 `evidence_outputs` 这一栏里有没有非信度的键，不看论文、也不看那个产出长什么样。
    """
    codes = _codes(_plan(kind="instrument", validity_evidence=["structural"],
                         evidence_outputs={"reliability": "alpha_trust"}))
    assert "plan.measurement.reliability_only" in codes, codes


def test_只有信度那一条要说清该怎么改() -> None:
    problems, _ = validate_plan(
        _plan(kind="instrument", validity_evidence=["structural"],
              evidence_outputs={"reliability": "alpha_trust"}), _profile())
    hit = [p for p in problems if code_of(p) == "plan.measurement.reliability_only"]
    assert hit and "structural" in hit[0], hit


# ═══════════════════════ M2：声明了却一个产出都没有


def test_声明了效度证据但产出一个都没有_拒跑() -> None:
    """`validity_evidence` 非空、`evidence_outputs` 一个都没登记——声明走过场，拒跑。"""
    codes = _codes(_plan(kind="instrument", validity_evidence=["structural", "criterion"]))
    assert "plan.measurement.evidence_missing" in codes, codes


def test_产出登记的是别的类别_声明的那几类一条都没有_拒跑() -> None:
    """登记了产出，但登记的类别跟声明的对不上——照样是「声明的那几类一个产出都没有」。"""
    codes = _codes(_plan(kind="instrument", validity_evidence=["structural"],
                         evidence_outputs={"content": "专家评定表"}))
    assert "plan.measurement.evidence_missing" in codes, codes


def test_用尺子的计划声明了效度证据也要有产出() -> None:
    """M2 不只管造尺子：`validity_evidence` 是作者自己声明的，声明了就得对得上产出。"""
    codes = _codes(_plan(kind="application", validity_evidence=["criterion"]))
    assert "plan.measurement.evidence_missing" in codes, codes


def test_没声明效度证据的用尺子计划不受M2影响() -> None:
    """`validity_evidence` 空着（用尺子不必填）：没有声明就没有「对不对得上」可言。"""
    codes = _codes(_plan(kind="application"))
    assert "plan.measurement.evidence_missing" not in codes, codes
    assert "plan.measurement.reliability_only" not in codes, codes


# ═══════════════════════ 第二个绕行洞：往轻里声明——新编量表标成 application


def test_按最轻的说法声明照样拦_标成application但产出有因子结构() -> None:
    """**这一条是「按最轻的说法声明」的验收**（实施计划「零之二」第 ② 条）。

    新编量表标成 `application`，M1/M2 与「题目数 < 3 就停」全部不触发，代价为零。
    修法跟上一轮修分批实施那个洞一样：判据从「计划说什么」改成「产出里有什么」——
    产出里出现因子结构这种**造尺子才会有**的东西，而计划标的是用尺子，就报冲突。
    """
    codes = _codes(_plan(kind="application", evidence_outputs={"structural": "cfa_loadings"}))
    assert "plan.measurement.kind_conflict" in codes, codes


def test_标成application但产出有信度系数_照样拦() -> None:
    """信度系数也是造尺子才会有的东西——两道题的新量表标成 application 躲不掉。"""
    codes = _codes(_plan(kind="application", evidence_outputs={"reliability": "alpha_trust"}))
    assert "plan.measurement.kind_conflict" in codes, codes


def test_用尺子研究登记效标关联与区分的产出_不拦() -> None:
    """拿现成量表做实质研究，报效标关联或区分证据是常事——不许误拦（设计稿第十二节）。"""
    codes = _codes(_plan(kind="application", validity_evidence=["criterion", "discriminant"],
                         evidence_outputs={"criterion": "corr_with_donation",
                                           "discriminant": "corr_with_risk"}))
    assert "plan.measurement.kind_conflict" not in codes, codes
    assert "plan.measurement.evidence_missing" not in codes, codes


def test_冲突那一条要点出是哪个产出() -> None:
    problems, _ = validate_plan(
        _plan(kind="application", evidence_outputs={"structural": "cfa_loadings"}), _profile())
    hit = [p for p in problems if code_of(p) == "plan.measurement.kind_conflict"]
    assert hit and "structural" in hit[0] and "instrument" in hit[0], hit


# ═══════════════════════ 反面：正当的造尺子研究不被拦


def test_正当的造尺子研究不被拦() -> None:
    """声明了两类效度证据、两类的产出都登记了，另外还报了信度——一条都不该响。"""
    codes = _codes(_plan(kind="instrument", validity_evidence=["structural", "criterion"],
                         evidence_outputs={"structural": "cfa_loadings",
                                           "criterion": "corr_with_donation",
                                           "reliability": "alpha_trust"}))
    assert not {c for c in codes if c.startswith("plan.measurement.")}, codes


def test_不做测量研究的计划一条都不响() -> None:
    plan = AnalysisPlan(plan_id="p", idea_id="i1", estimator="ols_fe", outcome="income",
                        treatment="treat", research_type="descriptive")
    problems, _ = validate_plan(plan, _profile())
    assert not [p for p in problems if code_of(p).startswith("plan.measurement.")], problems


# ═══════════════════════ M3：信度不得冒充效度（纯函数）


def _results(**names) -> AnalysisResults:
    """一份结果：`names` 里每个名字造一个估计，当作「那份产出真的在」。"""
    est = {"main": Estimate(term="q", coef=0.2, se=0.05, p=0.001)}
    est |= {k: Estimate(term=k, coef=v, se=0.01, p=0.001) for k, v in names.items()}
    return AnalysisResults(plan_id="p", n=500, estimates=est, spec=SpecSummary(estimator="describe"))


def _spec(**kw) -> MeasurementSpec:
    base = dict(kind="instrument", construct="对陌生人的一般信任",
                indicators=["q1", "q2", "q3"], validity_evidence=["structural"])
    return MeasurementSpec(**{**base, **kw})


#: 论文里引用信度那份产出的那一句。**三条「换说法」用的是同一个占位符、同一份产出**——
#: 变的只有措辞，而这道门一个措辞都不读。
_CITE_ALPHA = "{{result:alpha_trust.coef}}"


def _m3(body: str, results: AnalysisResults, spec: MeasurementSpec | None):
    return manuscript.measurement_reliability_problems(body, results, spec)


def test_论文报信度而产出无效度证据_硬拦导出() -> None:
    """论文引了信度那份产出的占位符，而产出里一类效度证据都没有——拦。"""
    out = _m3(f"## 结果\n量表的信度为 {_CITE_ALPHA}。\n",
              _results(alpha_trust=0.87), _spec(evidence_outputs={"reliability": "alpha_trust"}))
    assert out and code_of(out[0]) == "paper.measurement_reliability_only", out


def test_换说法照样拦_内部一致性() -> None:
    out = _m3(f"## 结果\n三道题的内部一致性为 {_CITE_ALPHA}，量表可用。\n",
              _results(alpha_trust=0.87), _spec(evidence_outputs={"reliability": "alpha_trust"}))
    assert out and code_of(out[0]) == "paper.measurement_reliability_only", out


def test_换说法照样拦_Cronbach_alpha() -> None:
    out = _m3(f"## 结果\nCronbach's α 为 {_CITE_ALPHA}，达到常用标准。\n",
              _results(alpha_trust=0.87), _spec(evidence_outputs={"reliability": "alpha_trust"}))
    assert out and code_of(out[0]) == "paper.measurement_reliability_only", out


def test_换说法照样拦_题目间一致() -> None:
    out = _m3(f"## 结果\n三道题之间高度一致（题目间一致程度 {_CITE_ALPHA}）。\n",
              _results(alpha_trust=0.87), _spec(evidence_outputs={"reliability": "alpha_trust"}))
    assert out and code_of(out[0]) == "paper.measurement_reliability_only", out


def test_三种写法的判词一模一样() -> None:
    """同一份产出、同一个占位符，措辞不同——**判词必须一字不差**，因为门读的根本不是措辞。"""
    spec = _spec(evidence_outputs={"reliability": "alpha_trust"})
    res = _results(alpha_trust=0.87)
    said = {str(_m3(f"## 结果\n{w} {_CITE_ALPHA}。\n", res, spec)[0])
            for w in ("内部一致性为", "Cronbach's α 为", "题目间一致程度为")}
    assert len(said) == 1, said


def test_产出里有效度证据就不拦() -> None:
    """报了信度，同时结构效度的产出也真的在——这才是正当的造尺子论文。"""
    assert not _m3(
        f"## 结果\n量表的内部一致性为 {_CITE_ALPHA}，因子结构见 {{{{result:cfa_loadings.coef}}}}。\n",
        _results(alpha_trust=0.87, cfa_loadings=0.72),
        _spec(evidence_outputs={"reliability": "alpha_trust", "structural": "cfa_loadings"}))


def test_登记了效度证据但产出不在结果里_照样拦() -> None:
    """**「登记了」不算「产出在」**：登记的名字在结果里找不到，等于这一类证据没做出来。"""
    out = _m3(f"## 结果\n量表的内部一致性为 {_CITE_ALPHA}。\n",
              _results(alpha_trust=0.87),
              _spec(evidence_outputs={"reliability": "alpha_trust", "structural": "cfa_loadings"}))
    assert out and code_of(out[0]) == "paper.measurement_reliability_only", out


def test_效度证据是一张图也算产出() -> None:
    """因子结构常常只有一张图——图也是产出，传了图名就不该拦。"""
    assert not manuscript.measurement_reliability_problems(
        f"## 结果\n量表的内部一致性为 {_CITE_ALPHA}，因子结构见 {{{{fig:loadings}}}}。\n",
        _results(alpha_trust=0.87),
        _spec(evidence_outputs={"reliability": "alpha_trust", "structural": "loadings"}),
        figures={"loadings": "zh"})


def test_论文没引信度占位符就不拦() -> None:
    """没把信度报进论文，这道门就没有「冒充」可言——不响。"""
    assert not _m3("## 结果\n三道题的分布见表 1。\n",
                   _results(alpha_trust=0.87), _spec(evidence_outputs={"reliability": "alpha_trust"}))


def test_没有测量块的论文不受这道门影响() -> None:
    assert not _m3(f"## 结果\n系数为 {_CITE_ALPHA}。\n", _results(alpha_trust=0.87), None)


def test_没登记信度产出就没有可核的信度() -> None:
    """一份产出是不是信度系数，由作者在 `evidence_outputs` 里登记——没登记就无从判起，不猜。"""
    assert not _m3(f"## 结果\n量表的内部一致性为 {_CITE_ALPHA}。\n",
                   _results(alpha_trust=0.87), _spec(evidence_outputs={"content": "专家评定表"}))


def test_代码块里的占位符不算引用() -> None:
    """说明书示例里的占位符不是论文在引用它——跟别的检查同一口径（`_strip_code`）。"""
    assert not _m3("## 结果\n写法如下：\n\n```\n" + _CITE_ALPHA + "\n```\n",
                   _results(alpha_trust=0.87), _spec(evidence_outputs={"reliability": "alpha_trust"}))


# ═══════════════════════ M3 接进论文检查与导出


def _paper(body: str) -> str:
    return ("# 标题\n\n## 摘要\n均值 {{result:main.coef}}（n = {{result:main.n}}）。\n\n"
            "## 引言\n背景。\n\n## 数据与方法\n三道题测一般信任。\n\n"
            "## 结果\n*表 1. 主结果。*\n{{table:main}}\n" + body + "\n\n"
            "## 讨论\n### 局限\n样本有边界。\n")


def _rundir(tmp_path: Path, body: str, *, plan: AnalysisPlan | None = None,
            bindings: tuple[VariableBinding, ...] = (), **spec_kw) -> Path:
    run = tmp_path / "r"
    run.mkdir(parents=True)
    _results(alpha_trust=0.87).save(run / "results.json")
    (plan or AnalysisPlan(plan_id="p", idea_id="i1", estimator="describe", outcome="q1",
                          research_type="measurement",
                          measurement=_spec(**spec_kw))).save(run / "plan.json")
    if bindings:
        BindingReport(idea_id="i1", bindings=list(bindings), core_ok=True).save(run / "binding.json")
    (run / "paper.md").write_text(_paper(body), encoding="utf-8")
    for who in ("a", "b", "c"):                      # 凑够导出的审稿门（退出码 5/3）
        (run / f"review_paper_rendered_{who}.json").write_text(
            json.dumps({"verdict": "PASS", "findings": [], "round": 1}), encoding="utf-8")
    return run


def test_oss_paper_check拦得住(tmp_path: Path) -> None:
    run = _rundir(tmp_path, f"三道题的内部一致性为 {_CITE_ALPHA}。",
                  evidence_outputs={"reliability": "alpha_trust"})
    with pytest.raises(errors.Refused) as ei:
        paper_api.check(str(run))
    assert "paper.measurement_reliability_only" in {code_of(p) for p in ei.value.problems}, ei.value.problems


def test_oss_paper_export硬拦(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """导出那一侧也拦：`.docx` 是真发出去的那一份，不能只在校验里挡一下。"""
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **kw: None if name == "pandoc" else real_which(name, *a, **kw))
    run = _rundir(tmp_path, f"三道题的内部一致性为 {_CITE_ALPHA}。",
                  evidence_outputs={"reliability": "alpha_trust"})
    paper_api.render(str(run))
    with pytest.raises(errors.Refused) as ei:
        paper_api.export(str(run))
    assert code_of(ei.value.summary) == "paper.measurement_reliability_only", ei.value.summary


def test_正当的造尺子论文导出不被拦(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """反面：结构效度的产出真的在，导出就不该被这道门拦（拦不拦得过 pandoc 是另一回事）。"""
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **kw: None if name == "pandoc" else real_which(name, *a, **kw))
    run = _rundir(tmp_path,
                  f"内部一致性为 {_CITE_ALPHA}，因子结构见 {{{{result:cfa_loadings.coef}}}}。",
                  evidence_outputs={"reliability": "alpha_trust", "structural": "cfa_loadings"})
    _results(alpha_trust=0.87, cfa_loadings=0.72).save(run / "results.json")
    paper_api.render(str(run))
    with pytest.raises(errors.Unavailable):          # 走到查 pandoc 那一步 = 这道门放行了
        paper_api.export(str(run))


# ═══════════════════════ 顺带：局限里的功效判词对 evidence 显式不适用


def test_evidence模式不报功效的假警报() -> None:
    """测量类既不算功效也不算精度（设计稿第四节），局限里当然也没有功效可交代。

    今天 evidence 的判词恒为 pass，这条走不到；写死是为了将来 evidence 出现非 pass 判词时，
    不会冒出一句「功效判词是 X，局限里一个字没提功效」那样的假警报。
    """
    pw = PowerReport(idea_id="i", mode="evidence", verdict="gray", power=0.0)
    assert not manuscript.limits_power_problems("## 讨论\n### 局限\n样本有边界。\n", pw)
    # 别的模式一行不动
    assert manuscript.limits_power_problems(
        "## 讨论\n### 局限\n样本有边界。\n", PowerReport(idea_id="i", verdict="gray", power=0.4))


# ═══════════════════════ Task 4 · M4：跨组比较前先验测量等价（设计稿 5.2）


def _equiv_plan(**measurement) -> AnalysisPlan:
    """一份「用尺子」的计划：跨组比较写在 groups_compared 里。"""
    return _plan(kind="application", **measurement)


def test_跨组比较没有等价检验_警告加处方(tmp_path: Path) -> None:
    """声明了跨组比较，产出登记里没有等价检验那一行——**警告 + must 级处方，不直接停**。

    不停的理由（设计稿 5.2）：等价检验做不出来时正确答案是把跨组比较降级为「组内描述」，
    不是不做研究——堵死了作者只能造假或换题。
    """
    plan = _equiv_plan(groups_compared=["urban"])
    assert "plan.measurement.no_equivalence" in _warn_codes(plan), _warn_codes(plan)

    item = _route_item(tmp_path, plan)
    assert item is not None and item.priority == "must" and item.kind == "disclose", item
    # 两条出路都要写在处方里：做检验，或降级为组内描述并写进局限
    assert "组内描述" in item.advice and "局限" in item.advice, item.advice


def test_做了等价检验_不响(tmp_path: Path) -> None:
    """等价检验的产出登记上了——门不响，处方也不开。"""
    plan = _equiv_plan(groups_compared=["urban"],
                       evidence_outputs={"equivalence": "跨组测量等价检验"})
    assert "plan.measurement.no_equivalence" not in _warn_codes(plan), _warn_codes(plan)
    assert _route_item(tmp_path, plan) is None


def test_没有跨组比较的测量计划_M4不响() -> None:
    """`groups_compared` 空着、别处也没有分组的迹象——没有跨组比较可言。"""
    assert "plan.measurement.no_equivalence" not in _warn_codes(_equiv_plan()), _warn_codes(_equiv_plan())


def test_等价检验登记的名字空着不算登记() -> None:
    """契约不收空名字（`evidence_outputs` 的校验器），所以「登记了个空的」进不来。"""
    with pytest.raises(Exception):
        MeasurementSpec(kind="application", construct="X", indicators=["income"],
                        groups_compared=["urban"], evidence_outputs={"equivalence": "  "})


def test_等价检验不算造尺子才有的产出() -> None:
    """用现成量表做跨组比较、报测量等价，是最正当的做法——不许被 kind_conflict 误拦。"""
    codes = _codes(_equiv_plan(groups_compared=["urban"],
                               evidence_outputs={"equivalence": "跨组测量等价检验"}))
    assert "plan.measurement.kind_conflict" not in codes, codes


# ═══════════════════════ Task 4 · 第三个绕行洞：跨组比较不声明


def test_按最轻的说法声明照样拦_分组变量不声明() -> None:
    """**「按最轻的说法声明」的验收**（实施计划「零之二」第 ③ 条）。

    `groups_compared` 空着，M4 整道门绕过，而论文照样跨组比较，代价为零。
    修法照前两批：判据从「计划说什么」改成**从别处对账**——描述块里有分组，就得报。
    """
    plan = _plan(kind="application")
    plan.description = DescriptionSpec(kind="gap", groups=["urban"])
    assert "plan.measurement.groups_undeclared" in _warn_codes(plan), _warn_codes(plan)


def test_不声明照样开处方_洞堵不堵得住看这条(tmp_path: Path) -> None:
    """光警告堵不住洞：声明了开 must 级处方、不声明只警告，绕行的代价还是零。

    所以处方那一侧也认「别处有分组的迹象」——这一条才是洞真正堵上的地方。
    """
    plan = _plan(kind="application")
    plan.description = DescriptionSpec(kind="gap", groups=["urban"])
    item = _route_item(tmp_path, plan)
    assert item is not None and item.priority == "must", item
    assert "urban" in item.evidence, item.evidence


def test_交互项也是分组的迹象() -> None:
    plan = _plan(kind="application")
    plan.interactions = ["income*urban"]
    assert "plan.measurement.groups_undeclared" in _warn_codes(plan), _warn_codes(plan)


def test_结果里的分组表也是分组的迹象(tmp_path: Path) -> None:
    """计划里一个字没写，结果里却真排出了分组表——照样开处方。"""
    plan = _plan(kind="application")
    results = _results()
    results.groups = [GroupCell(label="城镇", n=50, mean=1.0, se=0.1, ci_lo=0.8, ci_hi=1.2),
                      GroupCell(label="乡村", n=50, mean=0.8, se=0.1, ci_lo=0.6, ci_hi=1.0)]
    item = _route_item(tmp_path, plan, results=results)
    assert item is not None and item.priority == "must", item


def test_确实没有跨组比较_不误报(tmp_path: Path) -> None:
    """反面、防误拦：整篇就一个总体，描述块没有分组、没有交互项、结果里也没有分组表。"""
    plan = _plan(kind="application")
    plan.description = DescriptionSpec(kind="level")
    assert "plan.measurement.groups_undeclared" not in _warn_codes(plan), _warn_codes(plan)
    assert _route_item(tmp_path, plan) is None


def test_声明了分组就不再报没声明() -> None:
    """`groups_compared` 写了，这一条就没有「绕行」可言——该响的是 M4，不是这一条。"""
    plan = _plan(kind="application", groups_compared=["urban"])
    plan.description = DescriptionSpec(kind="gap", groups=["urban"])
    codes = _warn_codes(plan)
    assert "plan.measurement.groups_undeclared" not in codes, codes
    assert "plan.measurement.no_equivalence" in codes, codes


def test_不做测量研究的计划没有这一条() -> None:
    """描述类计划本来就该有分组——没有测量块就没有 groups_compared 可对账。"""
    plan = AnalysisPlan(plan_id="p", idea_id="i1", estimator="describe", outcome="income",
                        research_type="descriptive",
                        description=DescriptionSpec(kind="gap", groups=["urban"]))
    codes = _warn_codes(plan)
    assert not {c for c in codes if c.startswith("plan.measurement.")}, codes


# ═══════════════════════ 第四个绕行洞：分组变量直接当处理变量放进回归


def _scale_frame() -> pd.DataFrame:
    """六道五点题 + 它们的均分（`scale_all`）+ 城乡二值列 + 一列跟量表无关的收入。"""
    rng = np.random.default_rng(20260917)
    n = 300
    urban = (rng.random(n) < 0.5).astype(int)
    theta = 0.4 * urban + rng.normal(0, 1, n)
    d = pd.DataFrame({f"item{k + 1}": np.clip(np.rint(3 + 0.8 * theta + rng.normal(0, 0.9, n)), 1, 5).astype(int)
                      for k in range(6)})
    d["scale_all"] = d.mean(axis=1).round(4)
    d["urban"] = urban
    d["income"] = np.round(np.exp(rng.normal(9, 0.5, n)), 2)
    return d


def _scale_plan(tmp_path: Path, outcome: str, treatment: str, **measurement):
    """把上面那份数据落盘，返回（计划、画像、帧）。计划一律不声明 `groups_compared`。"""
    path = tmp_path / "scale.csv"
    if not path.exists():
        _scale_frame().to_csv(path, index=False)
    frame = pd.read_csv(path)
    base = dict(kind="application", construct="对陌生人的一般信任",
                indicators=[f"item{k + 1}" for k in range(6)])
    plan = AnalysisPlan(plan_id="p", idea_id="i1", estimator="ols_fe", research_type="measurement",
                        outcome=outcome, treatment=treatment,
                        measurement=MeasurementSpec(**{**base, **measurement}))
    return plan, profile_file(path), frame


def test_按最轻的说法声明照样拦_分组变量当处理变量(tmp_path: Path) -> None:
    """**第四个绕行洞**：结局是量表分、处理变量就是分组变量、`groups_compared` 留空。

    算的就是跨组比较，而 M4 与「跨组比较不声明」原先一道门都不响，代价为零。
    修法跟前三个洞一样——判据从「计划说什么」改成**从计划与数据算出来的事实**：
    结局是不是这套题的量表分从数据算，处理变量是不是分组变量从画像判。
    """
    plan, profile, frame = _scale_plan(tmp_path, outcome="scale_all", treatment="urban")
    codes = _warn_codes(plan, profile, frame=frame)
    assert "plan.measurement.groups_undeclared" in codes, codes


def test_结局就是量表里的一道题_也认(tmp_path: Path) -> None:
    """结局直接写成 `indicators` 里的一道题——连数据都不用算，名单上就有。"""
    plan, profile, _ = _scale_plan(tmp_path, outcome="item1", treatment="urban")
    assert "plan.measurement.groups_undeclared" in _warn_codes(plan, profile), _warn_codes(plan, profile)


def test_分组变量当处理变量_处方那一侧也开(tmp_path: Path) -> None:
    """洞真正堵上的地方在处方那一侧：光警告的话，绕行的代价还是接近零。

    处方那一步手上没有数据，只有画像与绑定——所以走的是绑定那一路：
    `binding.json` 把结局那一列绑给了测量块的构念。
    """
    plan, profile, _ = _scale_plan(tmp_path, outcome="scale_all", treatment="urban")
    binding = BindingReport(bindings=[VariableBinding(
        construct_name="对陌生人的一般信任", role="outcome", column="scale_all", status="bound")])
    item = _route_item(tmp_path, plan, profile=profile, binding=binding)
    assert item is not None and item.priority == "must", item
    assert "urban" in item.evidence, item.evidence


def test_处理变量是连续的_不报(tmp_path: Path) -> None:
    """**防误拦**：量表分对收入的回归不是跨组比较——收入在画像里是 continuous。"""
    plan, profile, frame = _scale_plan(tmp_path, outcome="scale_all", treatment="income")
    assert "plan.measurement.groups_undeclared" not in _warn_codes(plan, profile, frame=frame)


def test_结局不落在这个测量块上_不报(tmp_path: Path) -> None:
    """**防误拦**：城乡对收入的回归里，量表只是别处的一块——那是另一个研究，跟测量无关。"""
    plan, profile, frame = _scale_plan(tmp_path, outcome="income", treatment="urban")
    assert "plan.measurement.groups_undeclared" not in _warn_codes(plan, profile, frame=frame)


def test_判不出来就不报_没有数据也没有绑定(tmp_path: Path) -> None:
    """**边界照实写**：结局不在 `indicators` 里，而手上既没有帧也没有绑定——
    「它是不是这套题的量表分」无从判起，这时一律不报。"""
    plan, profile, _ = _scale_plan(tmp_path, outcome="scale_all", treatment="urban")
    assert "plan.measurement.groups_undeclared" not in _warn_codes(plan, profile)


def test_声明了分组就不再报没声明_分组变量当处理变量那一路(tmp_path: Path) -> None:
    """写进了 `groups_compared`，这一条就没有「绕行」可言——该响的是 M4。"""
    plan, profile, frame = _scale_plan(tmp_path, outcome="scale_all", treatment="urban",
                                       groups_compared=["urban"])
    codes = _warn_codes(plan, profile, frame=frame)
    assert "plan.measurement.groups_undeclared" not in codes, codes
    assert "plan.measurement.no_equivalence" in codes, codes


# ═══════════════════════ Task 4 · M5：自报数据按高误差代理（设计稿 5.2）


def _shaped(tmp_path: Path, **cols) -> tuple[AnalysisPlan, object]:
    """造一份带指定形状的列的数据，返回拿这些列当指标的计划与画像。

    **列名一律取无意义的编号**（`v301`、`q7`）：M5 判的是画像里那一列的形状，
    名字里没有任何线索也照样判得出来——这一条就是「不做词表」的验收。
    """
    path = tmp_path / "survey.csv"
    pd.DataFrame(cols).to_csv(path, index=False)
    profile = profile_file(path)
    plan = AnalysisPlan(plan_id="p", idea_id="i1", estimator="describe", outcome=list(cols)[0],
                        research_type="measurement",
                        measurement=MeasurementSpec(kind="application", construct="媒介使用",
                                                    indicators=list(cols),
                                                    source_quality="self_report"))
    return plan, profile


#: 一列自报的「量」：非负、取值有空档、上尾拖到中位数的两倍以上——时长、频次、金额就是这个形状。
_QUANTITY = [i % 8 for i in range(90)] + [35, 40, 42, 45, 48, 50, 52, 55, 58, 60]
#: 一道五点李克特题：画像判成 ordinal，M5 一律不碰——态度没有客观对应物，自报的就是它本身。
_LIKERT = [1, 2, 3, 4, 5] * 20


def test_自报的时长变量没标代理_警告(tmp_path: Path) -> None:
    """自报的时长 / 频次 / 金额跟后台日志有**系统性**差异（Parry 等 2021），不是随机噪声。

    判据只认「有没有按代理处理」，不判论证；「那一列是不是量」全从画像里的形状判。
    """
    plan, profile = _shaped(tmp_path, v301=_QUANTITY)
    codes = _warn_codes(plan, profile)
    assert "plan.measurement.self_report_proxy" in codes, codes


def test_标了代理_不响(tmp_path: Path) -> None:
    """按代理处理了——用的是现成的机制（`binding.py` 的 `status="proxy"`），不另造一套。"""
    plan, profile = _shaped(tmp_path, v301=_QUANTITY)
    binding = BindingReport(bindings=[VariableBinding(
        construct_name="媒介使用", role="treatment", column="v301", status="proxy")])
    codes = _warn_codes(plan, profile, binding=binding)
    assert "plan.measurement.self_report_proxy" not in codes, codes


def test_填齐了代理差距三栏也算按代理处理(tmp_path: Path) -> None:
    """三栏填齐 = 已经把「理论要什么 / 实际测什么 / 差距」交代过了，不必再提醒一遍。"""
    plan, profile = _shaped(tmp_path, v301=_QUANTITY)
    binding = BindingReport(bindings=[VariableBinding(
        construct_name="媒介使用", role="treatment", column="v301", status="bound",
        construct_def="每天真正花在社交媒体上的时间", measures="自报的上周日均使用小时数",
        gap="自报普遍高估且高估程度跟使用量相关，系数偏大")])
    codes = _warn_codes(plan, profile, binding=binding)
    assert "plan.measurement.self_report_proxy" not in codes, codes


def test_李克特题不报_态度没有客观对应物(tmp_path: Path) -> None:
    """**防误拦**：五点量表在画像里是 ordinal，M5 连看都不看它。"""
    plan, profile = _shaped(tmp_path, q7=_LIKERT)
    codes = _warn_codes(plan, profile)
    assert "plan.measurement.self_report_proxy" not in codes, codes


def test_零到十的评分题不报_那是量表不是量(tmp_path: Path) -> None:
    """**防误拦**：0–10 的单题评分取值多于十个、画像判成 continuous，但它把整个区间占满了——
    是一把有界的尺子，不是一个量。"""
    plan, profile = _shaped(tmp_path, v88=[i % 11 for i in range(99)])
    codes = _warn_codes(plan, profile)
    assert "plan.measurement.self_report_proxy" not in codes, codes


def test_不是自报的就不报(tmp_path: Path) -> None:
    """同一列数据、同一个形状，来源标成后台日志——M5 不响。"""
    plan, profile = _shaped(tmp_path, v301=_QUANTITY)
    plan.measurement.source_quality = "logged"
    codes = _warn_codes(plan, profile)
    assert "plan.measurement.self_report_proxy" not in codes, codes


def test_M5那一条要点出是哪一列(tmp_path: Path) -> None:
    plan, profile = _shaped(tmp_path, v301=_QUANTITY, q7=_LIKERT)
    _, warnings = validate_plan(plan, profile)
    hit = [w for w in warnings if code_of(w) == "plan.measurement.self_report_proxy"]
    assert len(hit) == 1 and "v301" in hit[0], hit


# ─────────────────────────── 处方那一侧的小工具


def _route_item(tmp_path: Path, plan: AnalysisPlan, results: AnalysisResults | None = None,
                profile=None, binding=None):
    """在一个干净的运行目录上生成处方，把 `measurement_equivalence` 那一条取出来（没有就 None）。"""
    run = tmp_path / f"route_{len(list(tmp_path.iterdir()))}"
    run.mkdir(parents=True)
    plan.save(run / "plan.json")
    (results or _results()).save(run / "results.json")
    if profile is not None:
        profile.save(run / "profile.json")
    if binding is not None:
        binding.save(run / "binding.json")
    route = route_mod.build(run)
    return next((i for i in route.items if i.id == "measurement_equivalence"), None)


# ═══════════════════════ Task 5 甲 · 代理差距三栏对测量类必填（设计稿 5.3）


def _bound(construct: str, column: str, **gap) -> VariableBinding:
    """一条**绑得很准**的绑定：置信度 0.95 → `status="bound"`，老口径下三栏根本不要求填。"""
    b = binding_mod.verify(construct, "outcome", column, _profile(), confidence=0.95)
    assert b.status == "bound", b          # 夹具自检：这条不是代理，老口径管不着它
    return b.model_copy(update=gap) if gap else b


def _causal_plan() -> AnalysisPlan:
    return AnalysisPlan(plan_id="p", idea_id="i1", estimator="ols_fe", outcome="income",
                        treatment="urban", research_type="causal")


def _descriptive_plan() -> AnalysisPlan:
    return AnalysisPlan(plan_id="p", idea_id="i1", estimator="describe", outcome="income",
                        research_type="descriptive",
                        description=DescriptionSpec(kind="gap", groups=["urban"]))


def _gap_rule(tmp_path: Path, plan: AnalysisPlan, *bindings: VariableBinding):
    """把计划与绑定落到一个运行目录上，跑合规规则 `proxy-gap`。"""
    run = tmp_path / f"gap_{len(list(tmp_path.iterdir()))}"
    run.mkdir(parents=True)
    plan.save(run / "plan.json")
    BindingReport(idea_id="i1", bindings=list(bindings), core_ok=True).save(run / "binding.json")
    return skillcheck.r_proxy_gap(skillcheck.RunView(run))


def test_测量类主构念置信度再高也要填三栏(tmp_path: Path) -> None:
    """**这一件改的只是触发条件**（设计稿 5.3）：三栏原先只在绑定判成代理 / 构造时才要求填，
    而 `oss bind` 走下来置信度缺省是 1.0——**绑得准就不用交代**。

    测量研究不是这样：「理论要什么 / 实际测什么 / 差距」正是这类研究的核心交代，
    **绑得再准也省不掉**。还有一层：`measurement.construct` 是一句纯自由文本，
    写得对不对程序永远核不了，**三栏是它唯一的抓手**。
    """
    status, detail = _gap_rule(tmp_path, _plan(kind="application"),
                               _bound("对陌生人的一般信任", "income"))
    assert status == skillcheck.FAIL, (status, detail)
    assert "对陌生人的一般信任" in detail, detail


def test_指标列对上也算主构念(tmp_path: Path) -> None:
    """构念名是自由文本、跟 `measurement.construct` 一字不差的时候不多；
    `indicators` 里的列名是硬的，绑定的 `column` 对上就算。"""
    status, detail = _gap_rule(tmp_path, _plan(kind="application"),
                               _bound("受访者的一般信任水平", "edu_years"))
    assert status == skillcheck.FAIL, (status, detail)


def test_三栏填齐了不报(tmp_path: Path) -> None:
    status, detail = _gap_rule(
        tmp_path, _plan(kind="application"),
        _bound("对陌生人的一般信任", "income",
               construct_def="理论要的是对陌生人的一般信任",
               measures="一道十点的信任评分题",
               gap="单题比多题量表噪声大，相关会被压平"))
    assert status == skillcheck.PASS, (status, detail)


def test_别的研究类型不受影响(tmp_path: Path) -> None:
    """**因果 / 描述的既有行为一个字都不该变**：同一条绑得很准的绑定，没有测量块就不报。"""
    for plan in (_causal_plan(), _descriptive_plan()):
        status, detail = _gap_rule(tmp_path, plan, _bound("对陌生人的一般信任", "income"))
        assert status == skillcheck.PASS, (plan.research_type, status, detail)


def test_代理变量那一条的判词一字不动(tmp_path: Path) -> None:
    """老那一路的文案是用户可见的，**一个字都不许改**。"""
    proxy = binding_mod.verify("政治信息", "treatment", "income", _profile(), confidence=0.3)
    assert proxy.status == "proxy", proxy
    status, detail = _gap_rule(tmp_path, _causal_plan(), proxy)
    assert status == skillcheck.FAIL
    assert detail == "代理 / 构造变量没填代理差距三栏：政治信息", detail


def test_既是代理又是主构念只报一次(tmp_path: Path) -> None:
    """两条判据同时命中时走老那一路——不重做机制，也不把同一件事说两遍。"""
    proxy = binding_mod.verify("对陌生人的一般信任", "treatment", "income", _profile(), confidence=0.3)
    status, detail = _gap_rule(tmp_path, _plan(kind="application"), proxy)
    assert status == skillcheck.FAIL
    assert detail == "代理 / 构造变量没填代理差距三栏：对陌生人的一般信任", detail


def test_论文检查也报测量类主构念没填三栏(tmp_path: Path) -> None:
    """`oss paper check` 那一侧同一条触发条件——作者真正读到那句「方法节该怎么写」的地方。"""
    run = _rundir(tmp_path, "三道题的分布见表 1。", kind="application", validity_evidence=[],
                  bindings=(VariableBinding(construct_name="对陌生人的一般信任", role="outcome",
                                            column="q1", status="bound"),))
    out = paper_api.check(str(run))
    assert "paper.measurement_construct_gap" in {code_of(w) for w in out["data"]["warnings"]}, \
        out["data"]["warnings"]


def test_论文检查_三栏填齐了不报(tmp_path: Path) -> None:
    run = _rundir(tmp_path, "三道题的分布见表 1。", kind="application", validity_evidence=[],
                  bindings=(VariableBinding(
                      construct_name="对陌生人的一般信任", role="outcome", column="q1",
                      status="bound", construct_def="理论要的是对陌生人的一般信任",
                      measures="一道十点的信任评分题", gap="单题噪声大，相关会被压平"),))
    out = paper_api.check(str(run))
    assert "paper.measurement_construct_gap" not in {code_of(w) for w in out["data"]["warnings"]}, \
        out["data"]["warnings"]


def test_论文检查_别的研究类型不受影响(tmp_path: Path) -> None:
    """同一份稿子、同一条绑定，计划改成描述类——这条警告不许冒出来。"""
    plan = AnalysisPlan(plan_id="p", idea_id="i1", estimator="describe", outcome="q1",
                        research_type="descriptive")
    run = _rundir(tmp_path, "三道题的分布见表 1。", plan=plan,
                  bindings=(VariableBinding(construct_name="对陌生人的一般信任", role="outcome",
                                            column="q1", status="bound"),))
    out = paper_api.check(str(run))
    assert "paper.measurement_construct_gap" not in {code_of(w) for w in out["data"]["warnings"]}, \
        out["data"]["warnings"]


# ═══════════════════════ Task 5 乙 · M4 的论文期核实：登记了等价检验，产出得真的在


def _m4(results: AnalysisResults, spec: MeasurementSpec | None, figures=None):
    return manuscript.measurement_equivalence_problems(results, spec, figures)


def _app_spec(**kw) -> MeasurementSpec:
    base = dict(kind="application", validity_evidence=[], groups_compared=["urban"])
    return _spec(**{**base, **kw})


def test_登记了等价产出但产出不存在_硬拦导出() -> None:
    """**T4 查出来的洞**：M4 在计划那一侧只核「登记了没有」，
    于是登记一个空壳——写个名字、产出根本不存在——警告与 must 级处方整套不响，代价为零。

    补法照 M3 已有的形状：拿登记的名字去 `produced_outputs` 里找。
    """
    out = _m4(_results(), _app_spec(evidence_outputs={"equivalence": "随便一个名字"}))
    assert out and code_of(out[0]) == "paper.measurement_equivalence_missing", out


def test_等价产出真的在_不拦() -> None:
    assert not _m4(_results(mi_test=0.01),
                   _app_spec(evidence_outputs={"equivalence": "mi_test"}))


def test_等价检验是一张图也算产出() -> None:
    """等价检验常常只留一张图——图名表在论文期这一侧手上，所以核得了、不会误报。"""
    assert not _m4(_results(), _app_spec(evidence_outputs={"equivalence": "mi_plot"}),
                   figures={"mi_plot": "zh"})


def test_等价检验是一条诊断也算产出() -> None:
    res = _results()
    res.diagnostics["跨组测量等价"] = "标量等价成立"
    assert not _m4(res, _app_spec(evidence_outputs={"equivalence": "跨组测量等价"}))


def test_没登记等价检验的不归这道门管() -> None:
    """一个字都没登记是 M4 计划那一侧的事（警告 + must 级处方），这里不重复报。"""
    assert not _m4(_results(), _app_spec())


def test_没有测量块的等价门不响() -> None:
    assert not _m4(_results(), None)


def test_空壳登记在oss_paper_check里拦得住(tmp_path: Path) -> None:
    run = _rundir(tmp_path, "三道题的分布见表 1。", kind="application", validity_evidence=[],
                  groups_compared=["urban"], evidence_outputs={"equivalence": "空壳产出"})
    with pytest.raises(errors.Refused) as ei:
        paper_api.check(str(run))
    assert "paper.measurement_equivalence_missing" in {code_of(p) for p in ei.value.problems}, \
        ei.value.problems


def test_空壳登记硬拦导出(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`.docx` 是真发出去的那一份，不能只在校验里挡一下。"""
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **kw: None if name == "pandoc" else real_which(name, *a, **kw))
    run = _rundir(tmp_path, "三道题的分布见表 1。", kind="application", validity_evidence=[],
                  groups_compared=["urban"], evidence_outputs={"equivalence": "空壳产出"})
    (run / "paper_rendered.md").write_text("# 标题\n", encoding="utf-8")
    with pytest.raises(errors.Refused) as ei:
        paper_api.export(str(run))
    assert code_of(ei.value.summary) == "paper.measurement_equivalence_missing", ei.value.summary


def test_等价产出真的在_导出不被这道门拦(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """反面：那份产出真的落进了结果里——走到查 pandoc 那一步就说明这道门放行了。"""
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **kw: None if name == "pandoc" else real_which(name, *a, **kw))
    run = _rundir(tmp_path, "三道题的分布见表 1。", kind="application", validity_evidence=[],
                  groups_compared=["urban"], evidence_outputs={"equivalence": "mi_test"})
    _results(alpha_trust=0.87, mi_test=0.01).save(run / "results.json")
    paper_api.render(str(run))
    with pytest.raises(errors.Unavailable):
        paper_api.export(str(run))
