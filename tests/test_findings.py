"""检查发现带编号（评测第 1 步 Task 1）：文字一个字不改，多带一个稳定编号给评测归因用。

三条要紧的路——`manuscript.check`、`oss.api.paper.check`（`Refused.problems` 与
`data["warnings"]`）、`analysis.validate_plan`——返回的列表里必须还是 `Finding`，
判分器直接读它们；拼接、切片、`"；".join` 之后编号会丢，那是有意的。
"""
from __future__ import annotations

import ast
import copy
import json
import pickle
from pathlib import Path

import pytest

from oss.core.findings import FINDING_CODES, HARD, WARN, Finding, code_of

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures"


def test_文字跟普通字符串一模一样():
    f = Finding("缺了「局限」一节", "paper.section_missing")
    assert f == "缺了「局限」一节" and hash(f) == hash("缺了「局限」一节")
    assert json.dumps([f], ensure_ascii=False) == json.dumps(["缺了「局限」一节"], ensure_ascii=False)
    assert f"{f}" == "缺了「局限」一节" and str(f) == "缺了「局限」一节" and repr(f) == repr("缺了「局限」一节")
    assert code_of(f) == "paper.section_missing"
    assert code_of(copy.deepcopy(f)) == "paper.section_missing"
    assert code_of(copy.copy(f)) == "paper.section_missing"
    assert code_of(pickle.loads(pickle.dumps(f))) == "paper.section_missing"
    assert code_of("普通字符串") == ""
    # 拼接、切片之后是普通字符串：编号丢了，这是有意的
    assert code_of(f + "。") == "" and code_of(f[:2]) == ""


def _finding_calls() -> list[tuple[Path, ast.Call]]:
    """oss/ 底下每一处 `Finding(...)` 调用。用语法树找，不怕文字里带括号、跨行。"""
    out = []
    for p in sorted((ROOT / "oss").rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if name == "Finding":
                out.append((p, node))
    return out


def _code_arg(node: ast.Call) -> ast.expr | None:
    if len(node.args) >= 2:
        return node.args[1]
    return next((k.value for k in node.keywords if k.arg == "code"), None)


def test_源码里用到的编号都登记了():
    calls = _finding_calls()
    assert calls, "一处都没挂"
    used, dynamic = set(), []
    for p, node in calls:
        arg = _code_arg(node)
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            used.add(arg.value)
        else:
            dynamic.append(f"{p.relative_to(ROOT)}:{node.lineno}")
    # 编号必须是字面量：写成变量的话，这条测试就核不到它登没登记
    assert not dynamic, dynamic
    assert used <= set(FINDING_CODES), sorted(used - set(FINDING_CODES))


def test_登记的编号在源码里都用到了():
    """反过来：表里登记了、源码里一处都没挂的编号，是写错了名字或删了检查忘了删表。"""
    used = {a.value for _, n in _finding_calls()
            if isinstance(a := _code_arg(n), ast.Constant)}
    assert set(FINDING_CODES) <= used, sorted(set(FINDING_CODES) - used)


def test_编号清单每一条都写全了():
    for code, entry in FINDING_CODES.items():
        assert len(entry) == 3, code
        cmd, level, what = entry
        assert cmd.startswith("oss "), code
        assert level in (HARD, WARN), code
        assert what.strip(), code
        assert code == code.strip() and "." in code, code


# ─────────────────────────── 三条路：返回的列表里还是 Finding


def _results(**kw):
    from oss.core.contracts import AnalysisResults, Estimate, SpecSummary
    return AnalysisResults(plan_id="p", n=100,
                           estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                           spec=SpecSummary(estimator="ols_fe"), **kw)


def _levels_match(problems, warnings) -> None:
    """硬拦的那一栏只许出现 HARD 编号，警告那一栏只许出现 WARN 编号——登记的级别不许跟实际走的栏对不上。"""
    for p in problems:
        assert FINDING_CODES[code_of(p)][1] == HARD, (code_of(p), p)
    for w in warnings:
        assert FINDING_CODES[code_of(w)][1] == WARN, (code_of(w), w)


def test_论文检查的问题带编号():
    from oss.core import manuscript

    problems, warnings = manuscript.check("# T\n\n## 摘要\n效应是 0.47391。\n", _results())
    assert problems and warnings
    assert code_of(problems[0]) == "paper.section_missing"
    assert "paper.bare_decimal" in {code_of(p) for p in problems}
    assert "paper.no_placeholders" in {code_of(w) for w in warnings}
    assert all(isinstance(x, Finding) for x in [*problems, *warnings])
    _levels_match(problems, warnings)


def test_论文检查的主张一致性也带编号():
    """`check_claims` 挂在 check 的末尾（前面没有拒绝时才跑）——走 check 的路也得带编号。"""
    from oss.core import manuscript

    paper = ("# T\n\n## 摘要\n效应 {{result:main.coef}}（n = {{result:main.n}}）。\n\n"
             "## 引言\nx\n\n## 数据与方法\nx\n\n"
             "## 结果\n*Table 1. Main estimates.*\n{{table:main}}\n\n"
             "处理效应为 {{result:main.coef}}，p 值为 {{result:main.p}}，结果不显著。\n\n## 讨论\nx\n")
    problems, warnings = manuscript.check(paper, _results())
    assert [code_of(p) for p in problems] == ["claims.significance"], problems
    _levels_match(problems, warnings)


def test_计划的门的问题带编号():
    from oss.core.analysis import validate_plan
    from oss.core.contracts import AnalysisPlan, Estimand
    from oss.core.profiler import profile_file

    est = Estimand(unit_quantity="同一人受处理与否时收入之差", target_population="样本代表的就业人口",
                   assumptions=["给定受教育年限与双向固定效应后无未观测混淆"],
                   link="双向固定效应下 treat 的系数对上平均处理效应")
    plan = AnalysisPlan(plan_id="p1", idea_id="i1", estimator="ols_fe", outcome="income", treatment="treat",
                        controls=["edu_years"], fixed_effects=["unit_id", "year"], cluster_level="region",
                        research_type="causal", estimand=est, control_roles={"edu_years": "mediator"})
    problems, warnings = validate_plan(plan, profile_file(FIX / "tiny.csv"))
    assert problems, "控制了中介却没拦"
    assert code_of(problems[0]) == "plan.causal.mediator_control", problems
    assert all(isinstance(x, Finding) for x in [*problems, *warnings])
    _levels_match(problems, warnings)


def test_api论文检查拒绝时问题与警告都带编号(tmp_path):
    from oss.api import paper as paper_api
    from oss.api.errors import Refused

    run = tmp_path / "r"
    run.mkdir()
    _results().save(run / "results.json")
    (run / "paper.md").write_text("# T\n\n## 摘要\n效应是 0.47391。\n", encoding="utf-8")
    with pytest.raises(Refused) as ei:
        paper_api.check(str(run))
    exc = ei.value
    assert code_of(exc.problems[0]) == "paper.section_missing"
    assert exc.data["warnings"] and all(code_of(w) for w in exc.data["warnings"])
    assert "paper.no_placeholders" in {code_of(w) for w in exc.data["warnings"]}
    _levels_match(exc.problems, exc.data["warnings"])
    # 给人看的那一份照旧：to_result 出来的 JSON 跟普通字符串一样
    js = json.loads(json.dumps(exc.to_result(), ensure_ascii=False))
    assert js["problems"] == [str(p) for p in exc.problems]


_PAPER_OK = ("# T\n\n## Abstract\nEffect {{result:main.coef}} (n = {{result:main.n}}).\n\n"
             "## Introduction\nx\n\n## Data and Methods\nx\n\n"
             "## Results\n*Table 1. Main estimates.*\n{{table:main}}\n\n## Discussion\nx\n")


def test_导出的门带export编号(tmp_path):
    """导出的门按退出码记编号：8 是正文的因果说法一族，4 是缺主结果表（这一道没有 problems，编号挂在 summary 上）。"""
    from oss.api import paper as paper_api
    from oss.api.errors import Refused
    from oss.core.contracts import AnalysisPlan

    run = tmp_path / "r8"
    run.mkdir()
    _results().save(run / "results.json")
    AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="x",
                 research_type="descriptive").save(run / "plan.json")
    (run / "paper.md").write_text(
        _PAPER_OK.replace("## Discussion\nx", "## Discussion\nThe reform causes higher output."),
        encoding="utf-8")
    (run / "paper_rendered.md").write_text("# T\n", encoding="utf-8")
    with pytest.raises(Refused) as ei:
        paper_api.export(str(run))
    assert ei.value.code == 8
    assert code_of(ei.value.summary) == "export.causal_family"
    assert ei.value.problems and {code_of(p) for p in ei.value.problems} == {"export.causal_family"}

    run4 = tmp_path / "r4"
    run4.mkdir()
    _results().save(run4 / "results.json")
    (run4 / "paper.md").write_text(_PAPER_OK.replace("{{table:main}}", "x"), encoding="utf-8")
    (run4 / "paper_rendered.md").write_text("# T\n", encoding="utf-8")
    with pytest.raises(Refused) as ei:
        paper_api.export(str(run4))
    assert ei.value.code == 4
    assert code_of(ei.value.summary) == "export.main_table"
