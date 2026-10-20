"""绑定门 DID 例外（设计稿第五节、V0 发现 7/10）：

识别策略是 did / did_cs / event_study 时，组间处理（组内变异 0）是这类设计的
本来形状——不进 blocking，降为一条 note。ols_fe 照旧拦。"""
from __future__ import annotations

from oss.core import binding as binding_mod
from oss.core.contracts import DataProfile, DataStructure, FileInfo, VariableProfile


def _profile() -> DataProfile:
    return DataProfile(
        file=FileInfo(path="sim.csv"),  # FileInfo.path 必填，fence 未提供——按 contracts 补齐
        structure=DataStructure(kind="panel_balanced"),
        variables=[
            VariableProfile(name="stance", semantic_type="ordinal", within_variation=0.9),
            VariableProfile(name="personalized", semantic_type="binary", within_variation=0.0),
        ])


def _bindings(prof):
    return [binding_mod.verify("立场分", "outcome", "stance", prof),
            binding_mod.verify("是否热度推荐", "treatment", "personalized", prof)]


def test_did策略下组间处理不进blocking():
    prof = _profile()
    report = binding_mod.build_report("i1", _bindings(prof), prof, strategy="did")
    assert report.blocking == [] and report.core_ok is True


def test_olsfe策略下照旧拦():
    prof = _profile()
    report = binding_mod.build_report("i1", _bindings(prof), prof, strategy="ols_fe")
    assert any("组内变异" in b for b in report.blocking)


def test_缺省策略等于olsfe向后兼容():
    prof = _profile()
    report = binding_mod.build_report("i1", _bindings(prof), prof)
    assert any("组内变异" in b for b in report.blocking)


def test_api层真的把策略递进去了(tmp_path):
    """突变测试抓出的洞：删掉 bind() 的 strategy= 传参，全套曾照样全绿。

    这条钉「送达」，前三条钉「规则」——两头都得钉（test_stage3_json 名单的同一条哲学）。
    """
    import json

    from oss.api import binding as binding_api

    run = tmp_path / "r"
    run.mkdir()
    _profile().save(run / "profile.json")
    for strategy, should_pass in (("did", True), ("ols_fe", False)):
        ideas = {"direction": "d", "ideas": [{
            "idea_id": "idea_001", "title": "t", "research_question": "q",
            "hypothesis": "h", "mechanism": "m",
            "identification_strategy": strategy,
            "constructs_needed": ["立场分", "是否热度推荐"],
            "contribution_type": "new-mechanism", "theory_hook": "th",
            "value_if_null": "v", "needs_extra_data": "", "lens": "l"}]}
        (run / "ideas.json").write_text(
            json.dumps(ideas, ensure_ascii=False), encoding="utf-8")
        res = binding_api.bind(str(run), idea="idea_001",
                               mappings=["立场分=stance:outcome",
                                         "是否热度推荐=personalized:treatment"])
        assert (res["data"]["blocking"] == []) is should_pass, strategy
