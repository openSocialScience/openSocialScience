"""因果骨架的计划模板与门（设计稿第三节）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from oss.core.contracts import AnalysisPlan, AnalysisResults, Estimand, OlsFit


def test_老计划没有新字段照样读得进(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"plan_id": "p", "idea_id": "i", "outcome": "y", "treatment": "x"}),
                 encoding="utf-8")
    plan = AnalysisPlan.load(p)
    assert plan.estimand is None and plan.control_roles == {}


def test_估计量三步缺哪栏说哪栏() -> None:
    e = Estimand(unit_quantity="u", target_population="", assumptions=[" "], link="l")
    assert e.missing() == ["目标总体", "识别假设"]


def test_控制变量角色只认五种() -> None:
    with pytest.raises(ValidationError):
        AnalysisPlan(control_roles={"w": "mechanism"})


def test_拟合信息不落盘(tmp_path: Path) -> None:
    r = AnalysisResults(plan_id="p")
    r._fit = OlsFit(terms=["x"], beta=[1.0], vcov=[[0.1]], vcov_iid=[[0.1]], dof=10, r2=0.5, n=12)
    r.save(tmp_path / "results.json")
    assert "_fit" not in (tmp_path / "results.json").read_text(encoding="utf-8")
    assert AnalysisResults.load(tmp_path / "results.json")._fit is None


def test_样例计划写了估计量三步与控制变量角色() -> None:
    from oss.core import fake

    p = fake.plan("idea_001")
    assert p.estimand is not None and not p.estimand.missing()
    assert set(p.controls) <= set(p.control_roles)


from oss.core.analysis import validate_plan
from oss.core.profiler import profile_file

FIX = Path(__file__).parent / "fixtures"
EST = Estimand(unit_quantity="同一人受处理与否时收入之差", target_population="样本代表的就业人口",
               assumptions=["给定受教育年限与双向固定效应后无未观测混淆"],
               link="双向固定效应下 treat 的系数对上平均处理效应")


def _tiny_plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p1", idea_id="i1", estimator="ols_fe", outcome="income", treatment="treat",
                controls=["edu_years"], fixed_effects=["unit_id", "year"], cluster_level="region",
                research_type="causal", estimand=EST, control_roles={"edu_years": "confounder"})
    return AnalysisPlan(**{**base, **kw})


@pytest.fixture(scope="module")
def prof():
    return profile_file(FIX / "tiny.csv")


def _gate_msgs(problems: list[str]) -> list[str]:
    keys = ("估计量三步", "没标角色", "中介或碰撞", "结果变量")
    return [p for p in problems if any(k in p for k in keys)]


def test_写全了就不拦(prof) -> None:
    problems, _ = validate_plan(_tiny_plan(), prof)
    assert _gate_msgs(problems) == [], problems


def test_因果计划没写估计量三步就拒(prof) -> None:
    problems, _ = validate_plan(_tiny_plan(estimand=None), prof)
    assert any("估计量三步" in p for p in problems), problems


def test_估计量三步没写全点名缺哪栏(prof) -> None:
    problems, _ = validate_plan(_tiny_plan(estimand=Estimand(unit_quantity="u")), prof)
    assert any("没写全" in p and "目标总体" in p for p in problems), problems


def test_控制变量没标角色就拒(prof) -> None:
    problems, _ = validate_plan(_tiny_plan(control_roles={}), prof)
    assert any("没标角色" in p and "edu_years" in p for p in problems), problems


@pytest.mark.parametrize("role", ["mediator", "collider"])
def test_控制中介或碰撞就拒(prof, role: str) -> None:
    problems, _ = validate_plan(_tiny_plan(control_roles={"edu_years": role}), prof)
    assert any("中介或碰撞" in p for p in problems), problems


def test_样本限制用结果变量就拒(prof) -> None:
    problems, _ = validate_plan(_tiny_plan(sample_filter="income > 0"), prof)
    assert any("结果变量" in p for p in problems), problems


def test_只是名字相近不算用结果变量(prof) -> None:
    problems, _ = validate_plan(_tiny_plan(sample_filter="income_base > 0"), prof)
    assert not any("结果变量" in p for p in problems), problems


def test_描述类不要求估计量三步(prof) -> None:
    problems, _ = validate_plan(
        _tiny_plan(research_type="descriptive", estimand=None, control_roles={}), prof)
    assert _gate_msgs(problems) == [], problems


def test_老计划只警告不拒(prof) -> None:
    problems, warnings = validate_plan(
        _tiny_plan(research_type="", estimand=None, control_roles={}, treat_time="year"), prof)
    assert _gate_msgs(problems) == [], problems
    assert any("估计量三步" in w for w in warnings), warnings


from oss.core import causal_checks as cc


def test_弱工具时正文不引AR区间就拒() -> None:
    probs = cc.paper_problems({"有效 F": "4.20"}, "效应 {{result:main.coef}}")
    assert probs and "AR 区间" in probs[0]
    assert cc.paper_problems({"有效 F": "4.20"}, "区间 {{diag:AR 区间}}") == []
    assert cc.paper_problems({"有效 F": "40"}, "x") == []
    assert cc.paper_problems({"有效 F": "没算"}, "x") == []


def test_因果ols没引敏感性数字只警告() -> None:
    d = {"稳健值": "0.1"}
    assert cc.paper_warnings(d, "x", causal_ols=True)
    assert cc.paper_warnings(d, "{{diag:Oster δ}}", causal_ols=True) == []
    assert cc.paper_warnings(d, "x", causal_ols=False) == []


def test_识别审稿的提示词问了因果骨架的几件事() -> None:
    from oss.host.reviewer import _PROMPTS

    p = _PROMPTS["identification"]
    for k in ("估计量", "目标总体", "中介", "碰撞", "排他性", "稳健值", "有效 F", "预检功效"):
        assert k in p, k
    assert "不说明没有混淆" in p


def test_有效F不是有限数时不开AR硬门() -> None:
    """`-inf`／`nan` 那类不是「测出来弱」，`float()` 转得过去但不该开门。"""
    assert cc.paper_problems({"有效 F": "-inf"}, "x") == []
    assert cc.paper_problems({"有效 F": "inf"}, "x") == []
    assert cc.paper_problems({"有效 F": "nan"}, "x") == []


def test_兜底路径判AR区间引没引_查的是值不是占位符() -> None:
    """`paper_rendered.md` 里占位符早换成了实际值，`{{diag:AR 区间}}` 这个占位符本身
    已经不存在——查的是 AR 区间的值字符串在不在渲染稿里。"""
    diag = {"有效 F": "4.20", "AR 区间": "[0.01, 0.20]"}
    probs = cc.paper_problems_rendered(diag, "正文没提这段区间")
    assert probs and "AR 区间" in probs[0]
    assert cc.paper_problems_rendered(diag, "区间见 [0.01, 0.20]。") == []
    assert cc.paper_problems_rendered({"有效 F": "40", "AR 区间": "[0.01, 0.20]"}, "x") == []
    assert cc.paper_problems_rendered({"有效 F": "4.20", "AR 区间": ""}, "随便什么文本") != []
    assert cc.paper_problems_rendered({"有效 F": "-inf", "AR 区间": "[0.01, 0.20]"}, "x") == []
