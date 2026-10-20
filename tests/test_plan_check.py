"""计划体检（docs/design/2026-09-23-plan-check.md）。离线，不叫模型。"""

from __future__ import annotations

import pandas as pd

from oss.core import profiler


def test_nests_in是模块级的_两列都有值的行上判():
    f = pd.DataFrame({"d": ["E", "E", "S", "C", None], "p": ["ee", "ee", "rpp", "rpp", "ee"]})
    assert profiler.nests_in(f, "d", "p") is True       # 每个 d 只落在一个 p 里
    assert profiler.nests_in(f, "p", "d") is False      # rpp 里有 S 和 C
    assert profiler.nests_in(f, "d", "d") is False      # 自己不嵌套于自己


# ─────────────────────────────── 契约

from oss.core.contracts import NestedColumns, PairedColumns, PlanCheck  # noqa: E402


def test_PlanCheck能落盘读回(tmp_path):
    pc = PlanCheck(pairs=[PairedColumns(a="es.o", b="es.r", marker=".o/.r")],
                   nested=[NestedColumns(column="d", other="p", relation="finer",
                                         mapping=["E → ee（2 行）"])])
    assert PlanCheck.load(pc.save(tmp_path / "plan_check.json")) == pc


# ─────────────────────────────── 配对列

from oss.core import plan_checks  # noqa: E402


def _pairs(cols: dict) -> set[tuple[str, str]]:
    return {(p.a, p.b) for p in plan_checks.paired_columns(pd.DataFrame(cols))}


def test_带分隔符的配对_各种标记与大小写都认():
    got = _pairs({"es.o": [1.0], "es.r": [2.0], "wage_PRE": [1], "wage_post": [2],
                  "n_orig": [3], "n_rep": [4], "score_t0": [1], "score_t1": [2], "age": [30]})
    assert got == {("es.o", "es.r"), ("wage_PRE", "wage_post"), ("n_orig", "n_rep"),
                   ("score_t0", "score_t1")}


def test_不带分隔符的配对_成批才认():
    """元科学那份数据：ro/rr、fiso/fisr、po/pr……一组可能是巧合，成批出现才是命名习惯。"""
    three = {"ro": [.1], "rr": [.2], "fiso": [.1], "fisr": [.2], "po": [.01], "pr": [.2]}
    assert _pairs(three) == {("ro", "rr"), ("fiso", "fisr"), ("po", "pr")}
    assert _pairs({"ro": [.1], "rr": [.2], "age": [30]}) == set()


def test_一边数值一边文本不算配对():
    assert _pairs({"x.o": [1.0], "x.r": ["a"]}) == set()


# ─────────────────────────────── 分组列跟另一列的对应

from oss.core.contracts import AnalysisPlan, DescriptionSpec  # noqa: E402
from oss.core.profiler import profile_file  # noqa: E402


def _meta_frame() -> pd.DataFrame:
    """元科学那份数据的形状：discipline 三类，每一类只落在一个 project 里。"""
    rows = [dict(discipline="Economics", project="ee", pm_belief=0.5 + i / 100) for i in range(18)]
    rows += [dict(discipline="Social" if i % 2 else "Cognitive", project="rpp", pm_belief=None)
             for i in range(68)]
    f = pd.DataFrame(rows)
    f["effect_size.o"] = [0.6113 + i / 10000 for i in range(len(f))]
    f["effect_size.r"] = f["effect_size.o"] / 2
    return f


def _profile(frame: pd.DataFrame, tmp_path):
    p = tmp_path / "d.csv"
    frame.to_csv(p, index=False)
    return profile_file(p)


def _table(frame: pd.DataFrame, tmp_path):
    """帧不经 CSV 读回：CSV 读回来空串就成了缺失，测不到 .sav 里空串原样留着的那种情形。
    （文件只给画像算指纹用。）"""
    from oss.core.loaders import LoadedTable
    p = tmp_path / "t.csv"
    frame.to_csv(p, index=False)
    return LoadedTable(frame=frame, path=p, fmt="csv")


def _desc_plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", research_type="descriptive", estimator="describe",
                outcome="effect_size.o",
                description=DescriptionSpec(kind="level", groups=["discipline"]))
    return AnalysisPlan(**{**base, **kw})


def test_分组列嵌套在另一列里_列出对应关系(tmp_path):
    f = _meta_frame()
    got = plan_checks.nested_columns(_desc_plan(), f, _profile(f, tmp_path))
    assert [(n.column, n.other, n.relation) for n in got] == [("discipline", "project", "finer")]
    assert "Economics → ee（18 行）" in got[0].mapping
    assert "Cognitive、Social → rpp（68 行）" in got[0].mapping


def test_筛选式里的列也查(tmp_path):
    f = _meta_frame()
    plan = _desc_plan(description=DescriptionSpec(kind="level"), sample_filter="project == 'rpp'")
    got = plan_checks.nested_columns(plan, f, _profile(f, tmp_path))
    assert [(n.column, n.other, n.relation) for n in got] == [("project", "discipline", "coarser")]


def test_连续数与跳问造成的巧合不算(tmp_path):
    """pm_belief 只在经济学那 18 行有值：不加两道防误报就被判成嵌套。"""
    f = _meta_frame()
    got = plan_checks.nested_columns(_desc_plan(), f, _profile(f, tmp_path))
    assert "pm_belief" not in {n.other for n in got}


def test_结局与处理不拿来比(tmp_path):
    f = _meta_frame()
    got = plan_checks.nested_columns(_desc_plan(outcome="project"), f, _profile(f, tmp_path))
    assert got == []


def test_有一类恰好是同一批行(tmp_path):
    f = pd.DataFrame({"g": ["a"] * 6 + ["b"] * 6 + ["c"] * 6,
                      "h": ["x"] * 6 + ["y", "z"] * 6})
    plan = _desc_plan(outcome="", description=DescriptionSpec(kind="level", groups=["g"]))
    got = plan_checks.nested_columns(plan, f, _profile(f, tmp_path))
    assert [(n.relation, n.mapping) for n in got] == [("same_rows", ["a = x（6 行）"])]


def test_分组列算出来的哑变量_不开(tmp_path):
    """GSS 真数据上剩下的噪声几乎全是这一类：black = race==2、college = degree>=3。
    它跟「discipline 嵌套在 project 里」结构一样，区别只在这是拿分组列算出来的重编码。"""
    race = [1, 2, 3] * 20
    f = pd.DataFrame({"race": [float(r) for r in race],
                      "black": [1.0 if r == 2 else 0.0 for r in race],
                      "us_lab": [1.0 if r == 1 else 0.0 for r in race],
                      "country": ["美国" if r == 1 else ("英国" if r == 2 else "法国") for r in race]})
    prof = _profile(f, tmp_path)
    plan = _desc_plan(outcome="", description=DescriptionSpec(kind="level", groups=["race"]))
    assert [n.other for n in plan_checks.nested_columns(plan, f, prof)] == []
    # 反过来也一样：分组列自己是哑变量、国家嵌在它里面
    plan = _desc_plan(outcome="", description=DescriptionSpec(kind="level", groups=["us_lab"]))
    assert plan_checks.nested_columns(plan, f, prof) == []


def test_一一对应的是同一列换了编码_不开(tmp_path):
    """urban 0/1 与「城市 / 农村」：分出来的组一模一样，没有要定的事（描述骨架 F1 夹具上撞出来的误报）。"""
    f = pd.DataFrame({"urban": [0.0, 1.0] * 20, "area": ["农村", "城市"] * 20, "k": ["a"] * 20 + ["b"] * 20})
    plan = _desc_plan(outcome="", description=DescriptionSpec(kind="level", groups=["urban"]))
    assert plan_checks.nested_columns(plan, f, _profile(f, tmp_path)) == []


def test_空串算没填(tmp_path):
    """CGSS2017 真数据上撞出来的：a7aa「请注明」几乎全是空串，当成一类就判出 a7a 嵌套在它里面。"""
    f = pd.DataFrame({"edu": [str(i % 5) for i in range(90)] + [None] * 10,
                      "edu_other": [""] * 90 + ["夜校", "私塾", "扫盲班", "夜校", "私塾"] * 2},
                     dtype="string")
    plan = _desc_plan(outcome="", description=DescriptionSpec(kind="level", groups=["edu"]))
    assert plan_checks.nested_columns(plan, f, profiler.profile_table(_table(f, tmp_path))) == []


def test_超过30类不拿来比(tmp_path):
    f = pd.DataFrame({"g": ["a", "b"] * 40, "h": [f"k{i}" for i in range(80)]})
    plan = _desc_plan(outcome="", description=DescriptionSpec(kind="level", groups=["g"]))
    assert plan_checks.nested_columns(plan, f, _profile(f, tmp_path)) == []


# ─────────────────────────────── 计划用没用、整份体检、不泄答案

from oss.core.contracts import RobustnessSpec  # noqa: E402


def test_uses只认分析字段_说明文字不算():
    plan = _desc_plan(robustness=[RobustnessSpec(label="x", rationale="对照 effect_size.r")])
    assert not plan_checks.uses(plan, "effect_size.r")
    plan = _desc_plan(robustness=[RobustnessSpec(label="x", outcome="effect_size.r")])
    assert plan_checks.uses(plan, "effect_size.r")
    assert plan_checks.uses(_desc_plan(sample_filter="`effect_size.r` > 0"), "effect_size.r")
    assert plan_checks.uses(_desc_plan(controls=["a*effect_size.r"]), "effect_size.r")


def test_体检结果里没有结局的取值(tmp_path):
    f = _meta_frame()
    text = plan_checks.check(_desc_plan(), f, _profile(f, tmp_path)).model_dump_json()
    assert "0.6113" not in text and "0.61" not in text
    assert "effect_size.r" in text and "project" in text


# ─────────────────────────────── 处方

from oss.core import route as route_mod  # noqa: E402


def _route_dir(tmp_path, plan: AnalysisPlan, pc: PlanCheck):
    plan.save(tmp_path / "plan.json")
    pc.save(tmp_path / plan_checks.ARTIFACT)
    return tmp_path


_PAIR = PlanCheck(pairs=[PairedColumns(a="effect_size.o", b="effect_size.r", marker=".o/.r")])


def test_配对列开must级加稳健性的处方_应用后复查即解决(tmp_path):
    rd = _route_dir(tmp_path, _desc_plan(), _PAIR)
    it = {i.id: i for i in route_mod.build(rd).items}["plan_paired"]
    assert (it.priority, it.kind, it.status) == ("must", "add_robustness", "pending")
    assert it.robustness.outcome == "effect_size.r"
    route_mod.apply(rd, route_mod.build(rd), "plan_paired")
    assert {i.id: i for i in route_mod.build(rd).items}["plan_paired"].status == "resolved"


def test_应用配对列补丁时说清覆盖了什么_不打None(tmp_path):
    rd = _route_dir(tmp_path, _desc_plan(), _PAIR)
    note = route_mod.apply(rd, route_mod.build(rd), "plan_paired")
    assert "outcome=effect_size.r" in note and "None" not in note


def test_配对列驳回后重建状态照留(tmp_path):
    rd = _route_dir(tmp_path, _desc_plan(), _PAIR)
    route_mod.dismiss(rd, route_mod.build(rd), "plan_paired", "题目只问原始研究")
    it = {i.id: i for i in route_mod.build(rd).items}["plan_paired"]
    assert (it.status, it.why) == ("dismissed", "题目只问原始研究")


def test_另一边只写在说明里照样开(tmp_path):
    plan = _desc_plan(robustness=[RobustnessSpec(label="x", rationale="对照 effect_size.r")])
    assert "plan_paired" in {i.id for i in route_mod.build(_route_dir(tmp_path, plan, _PAIR)).items}


def test_嵌套开must级写进局限的处方_计划不再用那列就解决(tmp_path):
    pc = PlanCheck(nested=[NestedColumns(column="discipline", other="project", relation="finer",
                                         mapping=["Economics → ee（18 行）"])])
    it = {i.id: i for i in route_mod.build(_route_dir(tmp_path, _desc_plan(), pc)).items}["plan_nested"]
    assert (it.priority, it.kind) == ("must", "disclose")
    assert it.evidence.startswith("计划按 `discipline` 分组；"), "没先说清计划现在按哪一列分，方向会被读反"
    assert "`discipline` 嵌套在 `project` 里" in it.evidence and "Economics → ee" in it.evidence
    plan = _desc_plan(description=DescriptionSpec(kind="level", groups=["project"]))
    it = {i.id: i for i in route_mod.build(_route_dir(tmp_path, plan, pc)).items}["plan_nested"]
    assert it.status == "resolved"


def test_没有体检文件就不开(tmp_path):
    _desc_plan().save(tmp_path / "plan.json")
    assert not [i for i in route_mod.build(tmp_path).items if i.id.startswith(plan_checks.PREFIX)]


# ─────────────────────────────── oss run 正式跑之前拦

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from oss.api import analysis as analysis_api  # noqa: E402
from oss.api import errors  # noqa: E402
from oss.api import profile as profile_api  # noqa: E402
from oss.api import route as route_api  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _run_dir(tmp_path, **plan_over) -> Path:
    """did_clean 改一份带配对结局的：y 改名 y_pre，另造一列 y_post。"""
    run = tmp_path / "r"
    run.mkdir()
    f = pd.read_csv(FIXTURES / "did_clean.csv").rename(columns={"y": "y_pre"})
    f["y_post"] = f["y_pre"].sample(frac=1, random_state=0).to_numpy()   # 跟 y_pre 不相干，当控制也跑得动
    f.to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    (run / "ideas.json").write_text(json.dumps({"direction": "d", "ideas": [
        {"idea_id": "idea_001", "title": "t", "research_question": "Q", "lens": "user-idea",
         "source": "user"}]}, ensure_ascii=False), encoding="utf-8")
    plan = {"plan_id": "p001", "idea_id": "idea_001", "estimator": "ols_fe", "outcome": "y_pre",
            "treatment": "x", "fixed_effects": ["unit", "year"], "cluster_level": "unit", **plan_over}
    (run / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return run


def test_oss_run有待处置的plan处方就拒跑_退出码6_不写结果(tmp_path):
    run = _run_dir(tmp_path)
    with pytest.raises(errors.Refused) as e:
        analysis_api.run(str(run))
    assert e.value.code == 6 and any("plan_paired" in p for p in e.value.problems)
    assert (run / plan_checks.ARTIFACT).exists() and not (run / "results.json").exists()


def test_应用之后放行_稳健性里多了配对那一项(tmp_path):
    run = _run_dir(tmp_path)
    with pytest.raises(errors.Refused):
        analysis_api.run(str(run))
    route_api.apply(str(run), "plan_paired")
    analysis_api.run(str(run))
    res = json.loads((run / "results.json").read_text(encoding="utf-8"))
    assert "配对列 y_post" in res["robustness"]


def test_驳回之后放行(tmp_path):
    run = _run_dir(tmp_path)
    with pytest.raises(errors.Refused):
        analysis_api.run(str(run))
    route_api.dismiss(str(run), "plan_paired", why="题目只问前测")
    analysis_api.run(str(run))
    assert (run / "results.json").exists()


def test_计划已经用了另一边就不拦(tmp_path):
    run = _run_dir(tmp_path, controls=["y_post"])
    analysis_api.run(str(run))
    assert (run / "results.json").exists()


def test_拒跑时不替模型写route_json(tmp_path):
    """「盘上有没有 route.json」是别的规则判「开没开过方」的依据：oss run 只算不写。"""
    run = _run_dir(tmp_path)
    with pytest.raises(errors.Refused):
        analysis_api.run(str(run))
    assert not (run / "route.json").exists()


def test_枚举字段读不进时把合法取值一起报出来():
    """只说「description.kind —— literal_error」，看的人只能猜。

    2026-09-23 DiscoveryBench 一片 12 道题里 3 道死在这儿：模型猜成 `proportions`，
    改三次、三次都不合法，一道题整个作废。
    """
    from pydantic import ValidationError

    from oss.api.analysis import plan_load_problems

    with pytest.raises(ValidationError) as e:
        AnalysisPlan(plan_id="p", outcome="y", estimator="describe",
                     description={"kind": "proportions", "groups": ["g"]})
    line = plan_load_problems(e.value)[0]
    assert line.startswith("description.kind —— literal_error")
    assert "'level'" in line and "'gap'" in line, f"没把合法取值带上：{line}"


def test_稳健性带着自己的分组格(tmp_path):
    """稳健性本来就是按主计划的分组重跑的，格早算出来了——以前没存，
    论文只能引一个「所有组合并」的数，跟主表的口径对不上（DiscoveryBench 2026-09-23 撞到）。"""
    import pandas as pd

    from oss.api import analysis as analysis_api
    from oss.api import profile as profile_api

    run = tmp_path / "r"
    run.mkdir()
    rng = __import__("numpy").random.default_rng(3)
    f = pd.DataFrame({"sex": ["male", "female"] * 60,
                      "y_a": rng.normal(10, 2, 120), "y_b": rng.normal(12, 2, 120)})
    f.to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    (run / "plan.json").write_text(json.dumps({
        "plan_id": "p", "idea_id": "i", "research_type": "descriptive", "estimator": "describe",
        "outcome": "y_a", "description": {"kind": "level", "groups": ["sex"]},
        "estimand": {"unit_quantity": "y 的水平", "target_population": "全部样本",
                     "assumptions": ["样本即总体"]},
        "robustness": [{"label": "candidate: y_b", "rationale": "另一个候选量", "outcome": "y_b"}],
    }, ensure_ascii=False), encoding="utf-8")

    analysis_api.run(str(run))
    res = json.loads((run / "results.json").read_text(encoding="utf-8"))
    cells = res["robustness"]["candidate: y_b"]["groups"]
    assert {c["label"] for c in cells} == {"sex=female", "sex=male"}, cells
    assert all(c["mean"] is not None for c in cells)
