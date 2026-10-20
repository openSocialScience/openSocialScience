"""调查层的处方与审稿（实施计划 docs/design/plans/2026-09-14-survey-layer.md 的 Task 10、Task 12）。

计划写的是追加到 `tests/test_survey_layer.py`；那份文件同时有另一批在改，为免合并冲突单放这里。
"""
from __future__ import annotations

from oss.core import route as route_mod
from oss.core.contracts import (
    AnalysisPlan, AnalysisResults, Estimate, RobustnessRun, RobustnessSpec, SurveyDesign,
)


# ══════════════════════════════ Task 10：调查层的四条处方


def _ids(items) -> set[str]:
    return {i.id for i in items}


def _res(coef=1.0, se=0.2, p=0.001, unweighted=None, **diag) -> AnalysisResults:
    r = AnalysisResults(plan_id="p", n=500, estimates={"main": Estimate(term="x", coef=coef, se=se, p=p)},
                        diagnostics=diag)
    if unweighted is not None:
        r.robustness = {"不加权": RobustnessRun(label="不加权", n=500,
                                                estimates={"main": Estimate(term="x", coef=unweighted, se=0.2, p=0.01)})}
    return r


def _model_plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", outcome="y", treatment="x", weights="w", research_type="causal",
                survey=SurveyDesign(psu="county", weight_use="model", design_source="测试"),
                robustness=[RobustnessSpec(label="不加权", weights="", executable=True)])
    return AnalysisPlan(**{**base, **kw})


def test_加权与不加权差得多开处方() -> None:
    items = route_mod._rules(_model_plan(), _res(coef=1.0, se=0.2, unweighted=0.3), None, [])
    it = next(i for i in items if i.id == "survey_weight_gap")
    assert it.priority == "should" and "3.5" in it.trigger            # |1.0 − 0.3| / 0.2 = 3.5 个标准误


def test_差得不多或描述性估计不开() -> None:
    assert "survey_weight_gap" not in _ids(route_mod._rules(_model_plan(), _res(unweighted=0.9), None, []))
    desc = _model_plan(survey=SurveyDesign(psu="county", weight_use="descriptive", design_source="测试"))
    assert "survey_weight_gap" not in _ids(route_mod._rules(desc, _res(unweighted=0.3), None, []))


def test_did_cs有分层只能近似开处方() -> None:
    plan = _model_plan(estimator="did_cs", survey=SurveyDesign(strata="prov", psu="county", weight_use="model",
                                                              design_source="测试"))
    assert "survey_design_approx" in _ids(route_mod._rules(plan, _res(), None, []))


def test_did_cs分层近似的处方带上标准误的说明() -> None:
    """佐证取诊断里真有的键：did_cs 把「按初级抽样单位聚类（分层没进方差）」写在「标准误」里，没有「抽样设计」这个键。"""
    plan = _model_plan(estimator="did_cs", survey=SurveyDesign(strata="prov", psu="county", weight_use="model",
                                                              design_source="测试"))
    res = _res(**{"标准误": "按初级抽样单位 county 聚类（分层 prov 没进方差）", "抽样单位数": "139"})
    it = next(i for i in route_mod._rules(plan, res, None, []) if i.id == "survey_design_approx")
    assert "county" in it.evidence, it.evidence


def test_结局有插补值开处方并给建议的筛选式() -> None:
    survey = {"likely": True, "imputation_flags": {"y_imp": "y"}}
    it = next(i for i in route_mod._rules(_model_plan(), _res(), None, [], survey=survey) if i.id == "survey_imputed")
    assert it.kind == "disclose" and "y_imp" in it.advice and "码本" in it.advice


def test_抽样单位太少开处方() -> None:
    items = route_mod._rules(_model_plan(), _res(**{"抽样单位数": "12", "单抽样单位的层": "0"}), None, [])
    assert "survey_few_psu" in _ids(items)
    ok = route_mod._rules(_model_plan(), _res(**{"抽样单位数": "139", "单抽样单位的层": "0"}), None, [])
    assert "survey_few_psu" not in _ids(ok)


# ══════════════════════════════ Task 12：审稿多问三件


def test_论文审稿人问了调查层的三件事() -> None:
    from oss.host.reviewer import _PROMPTS
    p = _PROMPTS["paper"]
    for k in ("survey", "无应答", "总调查误差", "抽样框", "学历"):
        assert k in p, k
