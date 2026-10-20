"""api 层：吃路径与值，吐字典；不 import typer、不打印、不退出。

阶段 3 的全部意义是**一份逻辑三个门面**（CLI 散文 / CLI --json / MCP）。
只要逻辑还长在 typer 命令函数里，MCP 那一侧就只能重写一遍——
重写的那一份会慢慢漂，然后「命令行能过、MCP 过不了」变成一类没人能查的 bug。

这个文件测的是 api 自己：形状对不对、该拒绝的抛不抛、抛的退出码对不对。
**退出码是契约**（说明书按 0/3/4/5/6/7 分支），所以每条拒绝路径都点名退出码。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss.api import errors
from oss.api import loop as loop_api
from oss.api import rundir as rundir_api


def _review(rundir: Path, name: str, verdict: str, findings: list[str], round_: int) -> None:
    (rundir / name).write_text(json.dumps(
        {"verdict": verdict, "findings": findings, "round": round_}, ensure_ascii=False),
        encoding="utf-8")


def _plan(rundir: Path, idea_id: str = "edu_001") -> None:
    (rundir / "plan.json").write_text(json.dumps(
        {"plan_id": "p", "idea_id": idea_id, "estimator": "ols_fe",
         "outcome": "y", "treatment": "x"}), encoding="utf-8")


# ═════════════════════════ 返回形状：五个键，一处定义


def test_结果形状是提案第七节那五个键(tmp_path):
    res = loop_api.status(str(tmp_path))
    assert set(res) <= {"ok", "summary", "data", "decision", "problems"}
    assert isinstance(res["ok"], bool) and isinstance(res["summary"], str)
    assert isinstance(res["data"], dict) and "exit_code" in res["data"]
    # 整份结果必须能原样 json.dumps——MCP 与 --json 都直接序列化它
    json.dumps(res, ensure_ascii=False)


def test_ok就是退出码为零(tmp_path):
    _plan(tmp_path)
    _review(tmp_path, "review_paper_rendered_a.json", "FAIL", ["[设计] x"], 1)
    res = loop_api.status(str(tmp_path))
    assert res["data"]["exit_code"] == 3 and res["ok"] is False
    assert res["decision"]["decision"] == "continue"


def test_loop_status给决定与遗留条数(tmp_path):
    _plan(tmp_path)
    _review(tmp_path, "review_paper_rendered_a.json", "FAIL", ["[设计] x"], 1)
    _review(tmp_path, "review_paper_rendered_a_r2.json", "FAIL", ["[设计] y"], 2)
    res = loop_api.status(str(tmp_path))
    assert res["decision"]["decision"] == "export_with_open_items"
    assert res["data"]["exit_code"] == 4
    assert res["data"]["status"] == "exhausted"
    assert res["data"]["rounds"][-1]["design_open"] == 1
    assert (tmp_path / "loop_state.json").exists(), "api 也要落盘，MCP 调一次盘上要一样"


def test_没审过时退出码零(tmp_path):
    res = loop_api.status(str(tmp_path))
    assert res["ok"] is True and res["data"]["exit_code"] == 0
    assert res["decision"]["decision"] == "review_first"


def test_每轮的新发现与已处置也进data(tmp_path):
    """散文里印着这两个数（阶段 2 加的），data 里没有的话 `--json` 就比散文少两个数。"""
    _review(tmp_path, "review_paper_rendered_a.json", "FAIL", ["[设计] x"], 1)
    _review(tmp_path, "review_paper_rendered_a_r2.json", "PASS",
            ["[设计] 1 处置了 —— 换了估计量", "[新发现] z"], 2)
    r2 = loop_api.status(str(tmp_path))["data"]["rounds"][-1]
    assert r2["new_open"] == 1 and r2["resolved"] == 1 and r2["design_open"] == 0


# ═════════════════════════ 拒绝路径：读的命令不许把目录建出来


def test_运行目录不存在就抛Missing而不是建一个(tmp_path):
    """打错路径是最常见的一次调用错误。`RunDir` 会顺手建目录，
    于是 `loop status runs/currnet` 报「还没有任何审稿」，看着像一次干净的新运行。"""
    missing = tmp_path / "没有这个目录"
    with pytest.raises(errors.Missing) as e:
        loop_api.status(str(missing))
    assert e.value.code == 1 and e.value.artifact == "run"
    assert not missing.exists(), "读的命令不许把目录建出来"
    assert e.value.summary.startswith("运行目录不存在：")


def test_require_run认已存在的目录(tmp_path):
    assert rundir_api.require_run(str(tmp_path)).path == tmp_path


def test_缺ideas时first_idea_id抛Missing(tmp_path):
    with pytest.raises(errors.Missing) as e:
        rundir_api.first_idea_id(rundir_api.open_run(str(tmp_path)))
    assert e.value.artifact == "ideas" and e.value.code == 1


def test_探索结果冒充正式结果被拒(tmp_path):
    (tmp_path / "results.json").write_text(json.dumps(
        {"plan_id": "explore:pretrend#1", "n": 10}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        rundir_api.official_results(rundir_api.open_run(str(tmp_path)))
    assert e.value.code == 1


# ═════════════════════════ 异常：带着退出码走


def test_异常自己带退出码和逐条理由():
    exc = errors.Refused("稿件过不了校验：", code=1, problems=["a", "b"])
    r = exc.to_result()
    assert r["ok"] is False and r["problems"] == ["a", "b"]
    assert r["data"]["exit_code"] == 1 and r["data"]["kind"] == "refused"
    assert r["summary"] == "稿件过不了校验："


def test_退出码零的异常不允许():
    """`ok = 退出码为零` 是唯一的规则。一个「失败但退出码 0」的异常会把它撕开。"""
    with pytest.raises(ValueError):
        errors.Refused("x", code=0)


def test_四种异常各有各的退出码缺省():
    assert errors.BadInput("x").code == 2
    assert errors.Missing("plan", "x").code == 1
    assert errors.Unavailable("x").code == 1
    assert {errors.Refused("x").kind, errors.Missing("p", "x").kind,
            errors.BadInput("x").kind, errors.Unavailable("x").kind} == {
        "refused", "missing", "bad_input", "unavailable"}


def test_api不许import_typer():
    """搬家最容易带过来的就是 typer。它一旦进 api，MCP 那一侧就会试图 Exit。

    整个包一起查，不是只查一个模块——**下一个搬进来的模块自动也在这条规矩里**。
    """
    import oss.api as pkg
    mods = sorted(Path(pkg.__file__).parent.glob("*.py"))
    assert len(mods) >= 6, [m.name for m in mods]
    for m in mods:
        lines = m.read_text(encoding="utf-8").splitlines()
        bad = [l for l in lines if l.strip().startswith(("import typer", "from typer"))]
        assert not bad, f"{m.name}: {bad}"


# ═════════════════════════ schema / eval

from oss.api import evals as evals_api          # noqa: E402
from oss.api import schema as schema_api        # noqa: E402


def test_schema给的是schema本身():
    res = schema_api.describe("plan")
    assert res["data"]["kind"] == "schema"
    assert res["data"]["schema"]["title"] == "AnalysisPlan"
    assert res["data"]["written_by"] == "skill"


def test_schema的example能直接落盘():
    res = schema_api.describe("plan", example=True)
    assert res["data"]["kind"] == "example"
    assert res["data"]["example"]["estimator"]
    # 打印用的那一份是 pydantic 自己写的字符串：散文不许因为换了序列化器而变
    assert json.loads(res["data"]["example_json"]) == res["data"]["example"]


def test_paper不是JSON时给的是规矩():
    res = schema_api.describe("paper")
    assert res["data"]["kind"] == "prose"
    assert "占位符" in res["data"]["text"]


def test_认不出的产出物报BadInput():
    with pytest.raises(errors.BadInput) as e:
        schema_api.describe("nope")
    assert e.value.code == 1 and "没有叫 nope 的产出物" in e.value.summary


def test_eval的must没过退出码是七(tmp_path):
    res = evals_api.run(str(tmp_path))
    assert res["data"]["exit_code"] == 7
    assert res["data"]["must_failed"]
    assert (tmp_path / "eval_report.json").exists()


def test_eval列规则不碰运行目录():
    res = evals_api.list_rules()
    assert res["data"]["exit_code"] == 0
    assert len(res["data"]["rules"]) > 20
    assert {"id", "skill", "priority", "what"} <= set(res["data"]["rules"][0])


def test_eval列行为规则也是九条():
    res = evals_api.list_rules(behavior=True)
    assert [r["skill"] for r in res["data"]["rules"]] == ["behavior"] * 9
    assert res["data"]["rules"][0]["id"].startswith("behavior-")


# ─────────────── --behavior：阶段 5 已经实现，不是挂点


def test_behavior目录不存在时报Missing而不是建一个(tmp_path):
    """**凭空造出来的通过是最坏的一种**：核账不许自己把目录建出来再判全过。"""
    missing = tmp_path / "没这个目录"
    with pytest.raises(errors.Missing) as e:
        evals_api.behavior(str(missing))
    assert e.value.code == 1 and "运行目录不存在" in e.value.summary
    assert not missing.exists()


def test_behavior全是na时退一且报告照样落盘(tmp_path):
    """一条留痕都没有 = 没交卷，不是合格。报告仍要写出来（证据），只是退出码是 1。"""
    res = evals_api.behavior(str(tmp_path))
    assert res["data"]["exit_code"] == 1 and res["ok"] is False
    assert res["data"]["undecidable"] is True
    assert "判不了" in res["summary"]
    d = json.loads((tmp_path / "eval_behavior.json").read_text(encoding="utf-8"))
    assert d["summary"] == {"pass": 0, "fail": 0, "n/a": 9}


def test_behavior的表与指标都在data里(tmp_path):
    (tmp_path / "trace.jsonl").write_text(json.dumps(
        {"seq": 1, "step": "profile", "command": "oss profile panel.csv", "args": {},
         "outcome": "ok"}, ensure_ascii=False) + "\n", encoding="utf-8")
    res = evals_api.behavior(str(tmp_path))
    assert res["data"]["exit_code"] == 0
    assert "指标" in res["data"]["table"] and "合格线" in res["data"]["table"]
    assert res["data"]["metrics"]["topic_switch"]["value"] == 0.0
    assert res["data"]["report_path"].endswith("eval_behavior.json")
    assert not (tmp_path / "eval_report.json").exists(), "两把尺子分开量"


# ═════════════════════════ anchor / route

from oss.api import anchor as anchor_api        # noqa: E402
from oss.api import route as route_api          # noqa: E402
from oss.core import state as state_mod         # noqa: E402


def _ideas(rundir: Path, direction: str = "A") -> None:
    (rundir / "ideas.json").write_text(json.dumps({"direction": direction, "ideas": []},
                                                  ensure_ascii=False), encoding="utf-8")


def _rows(rundir: Path) -> list[dict]:
    p = rundir / "trace.jsonl"
    return ([json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
            if p.exists() else [])


def test_换题没有用户授权就拒绝(tmp_path):
    _ideas(tmp_path); _plan(tmp_path)
    state_mod.freeze_anchor(tmp_path, direction="A", idea_id="edu_001", plan_sha="s")
    with pytest.raises(errors.Refused) as e:
        anchor_api.switch(str(tmp_path), to="edu_007", why="试试", authorized_by="")
    assert e.value.code == 2 and "用户授权" in e.value.summary


def test_没冻结时换题也拒绝(tmp_path):
    _ideas(tmp_path); _plan(tmp_path)
    with pytest.raises(errors.Refused) as e:
        anchor_api.switch(str(tmp_path), to="edu_007", why="试试", authorized_by="user")
    assert e.value.code == 2 and "还没冻结锚点" in e.value.summary


def test_被拒的换题在api这一侧就留痕(tmp_path):
    """留痕搬进 api：MCP 调一次和命令行跑一次，盘上必须一模一样。

    行为指标 `topic_switch` 数的是 trace.jsonl 的原始条数——少写一行，
    「模型自己授权自己换题被拦下来」这件事事后就完全看不见。
    """
    _ideas(tmp_path); _plan(tmp_path)
    state_mod.freeze_anchor(tmp_path, direction="A", idea_id="edu_001", plan_sha="s")
    with pytest.raises(errors.Refused):
        anchor_api.switch(str(tmp_path), to="edu_007", why="试试", authorized_by="")
    row = _rows(tmp_path)[-1]
    assert row["step"] == "topic_switch" and row["outcome"] == "refused"
    assert row["args"]["authorized_by"] == "" and row["args"]["reason"] == "unauthorized"
    assert state_mod.load(tmp_path).anchor["idea_id"] == "edu_001"      # 锚点没动


def test_没冻结那条拒绝的理由键不一样(tmp_path):
    _ideas(tmp_path); _plan(tmp_path)
    with pytest.raises(errors.Refused):
        anchor_api.switch(str(tmp_path), to="edu_007", why="试试", authorized_by="user")
    assert _rows(tmp_path)[-1]["args"]["reason"] == "not_frozen"
    assert not (tmp_path / state_mod.FILE).exists()      # 拒绝路径不凭空造状态文件


def test_授权换题落盘并留痕(tmp_path):
    _ideas(tmp_path); _plan(tmp_path)
    state_mod.freeze_anchor(tmp_path, direction="A", idea_id="edu_001", plan_sha="s")
    res = anchor_api.switch(str(tmp_path), to="edu_007", why="数据回答不了",
                            authorized_by="user")
    assert res["ok"] and res["data"]["from"] == "edu_001" and res["data"]["to"] == "edu_007"
    assert state_mod.load(tmp_path).anchor["idea_id"] == "edu_007"
    row = _rows(tmp_path)[-1]
    assert row["step"] == "topic_switch" and row["args"]["authorized_by"] == "user"
    assert row["outcome"] == "ok"


def test_换题目录不存在时不建目录(tmp_path):
    missing = tmp_path / "没这个目录"
    with pytest.raises(errors.Missing) as e:
        anchor_api.switch(str(missing), to="edu_007", why="x", authorized_by="user")
    assert e.value.code == 1 and not missing.exists()


def test_route没有处方时也给结构(tmp_path):
    res = route_api.status(str(tmp_path))
    assert res["data"]["items"] == [] and res["data"]["exit_code"] == 0
    assert "没有处方" in res["summary"]


def _pretrend_fail(rundir: Path) -> None:
    """一个会开出 must 级处方的运行目录：平行趋势不成立（core/route.py 规则 ③）。

    直接手写 route.json 是不行的——`route_mod.build()` 每次都从诊断重新开方，
    触发已经消失的老处方会被标成「已解决」。**处方跟着诊断走，不跟着文件走。**
    """
    (rundir / "plan.json").write_text(json.dumps(
        {"plan_id": "p", "idea_id": "i", "estimator": "did", "outcome": "y", "unit": "unit",
         "time": "year", "treat_time": "treat_time", "cluster_level": "unit"}), encoding="utf-8")
    (rundir / "results.json").write_text(json.dumps(
        {"plan_id": "p", "n": 100,
         "estimates": {"main": {"term": "t", "coef": 1.0, "se": 0.2, "p": 0.001}},
         "diagnostics": {"平行趋势": "**不成立**：处理前联合检验 p = 0.005"}},
        ensure_ascii=False), encoding="utf-8")


def test_route的must没处置退出码是六(tmp_path):
    _pretrend_fail(tmp_path)
    res = route_api.status(str(tmp_path))
    assert res["data"]["exit_code"] == 6 and res["ok"] is False
    assert res["data"]["pending_must"] == ["pretrend_fail"]
    assert res["problems"] == ["pretrend_fail"]


def test_route处置认不出的id报BadInput(tmp_path):
    _pretrend_fail(tmp_path)
    with pytest.raises(errors.BadInput) as e:
        route_api.dismiss(str(tmp_path), "nope", why="x")
    assert e.value.code == 1


def test_route写进局限之后那条不再待处置(tmp_path):
    _pretrend_fail(tmp_path)
    res = route_api.disclose(str(tmp_path), "pretrend_fail", why="局限第三段写了")
    assert res["data"]["pending_must"] == [] and res["data"]["exit_code"] == 0
    assert res["data"]["action"] == "disclose" and res["data"]["action_id"] == "pretrend_fail"
    it = [i for i in res["data"]["items"] if i["id"] == "pretrend_fail"][0]
    assert it["status"] == "disclosed" and it["why"] == "局限第三段写了"


def test_route目录不存在时不建目录(tmp_path):
    missing = tmp_path / "没这个目录"
    with pytest.raises(errors.Missing):
        route_api.status(str(missing))
    assert not missing.exists()


# ═════════════════════════ profile / merge / reshape

from oss.api import profile as profile_api      # noqa: E402
from oss.api import tables as tables_api        # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def test_画像落盘并把警告放进data(tmp_path):
    res = profile_api.profile(str(FIXTURES / "gss_like.csv"), run=str(tmp_path))
    assert res["ok"] and res["data"]["artifact"] == "profile.json"
    assert Path(res["data"]["path"]).exists()
    assert isinstance(res["data"]["warnings"], list)
    assert res["data"]["n_rows"] > 0 and res["data"]["structure"]


def test_shape只给形状不落盘(tmp_path):
    res = profile_api.profile(str(FIXTURES / "gss_like.csv"), run=str(tmp_path), shape=True)
    assert res["data"]["shape_summary"]
    assert not (tmp_path / "profile.json").exists(), "--shape 不该落盘"


def test_往一个跑完的目录里再画像被拒绝(tmp_path):
    (tmp_path / "results.json").write_text("{}", encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        profile_api.profile(str(FIXTURES / "gss_like.csv"), run=str(tmp_path))
    assert e.value.code == 1 and "新研究开新目录" in e.value.summary
    profile_api.profile(str(FIXTURES / "gss_like.csv"), run=str(tmp_path), resume=True)


def test_文件不存在报Missing(tmp_path):
    with pytest.raises(errors.Missing):
        profile_api.profile(str(tmp_path / "nope.csv"), run=str(tmp_path))


def test_merge没给键报BadInput(tmp_path):
    (tmp_path / "a.csv").write_text("k,v\n1,2\n", encoding="utf-8")
    with pytest.raises(errors.BadInput) as e:
        tables_api.merge(str(tmp_path / "a.csv"), str(tmp_path / "a.csv"), run=str(tmp_path))
    assert e.value.code == 1 and "要给键" in e.value.summary


def _two_tables(tmp_path: Path) -> tuple[str, str]:
    (tmp_path / "l.csv").write_text("country,year,y\nCHN,2000,1\nUSA,2000,2\n", encoding="utf-8")
    (tmp_path / "r.csv").write_text("country,year,z\nCHN,2000,9\nJPN,2000,8\n", encoding="utf-8")
    return str(tmp_path / "l.csv"), str(tmp_path / "r.csv")


def test_merge把丢掉的东西放进notes(tmp_path):
    left, right = _two_tables(tmp_path)
    res = tables_api.merge(left, right, on="country,year", run=str(tmp_path), allow_loss=0.6)
    assert res["data"]["rows_out"] == 1 and res["data"]["rows_in"] == 2
    assert res["data"]["loss_rate"] == 0.5
    assert res["data"]["notes"] and Path(res["data"]["path"]).exists()
    assert res["data"]["next_command"].startswith("oss profile ")


def test_merge丢太多时拒绝且候选不自动套用(tmp_path):
    """散文那一侧打的就是这条异常的整段话——候选列在里面，**但没有被应用**。"""
    left, right = _two_tables(tmp_path)
    with pytest.raises(errors.Refused) as e:
        tables_api.merge(left, right, on="country,year", run=str(tmp_path))
    assert e.value.code == 1 and "拒绝拼" in e.value.summary
    # 摊开的那几条「谁没配上」原样在拒绝理由里，而**没有被工具替你认定**
    assert "USA" in e.value.summary and "JPN" in e.value.summary
    assert "--allow-loss" in e.value.summary
    assert not (tmp_path / "merged.csv").exists(), "被拒的拼接不许留下产物"


def test_merge把对照表条数与损失率写进留痕(tmp_path):
    """「用了哪张对照表、接受了多大损失」是这一步全部的判断，事后要审。"""
    left, right = _two_tables(tmp_path)
    (tmp_path / "cw.csv").write_text("l,r\nUSA,JPN\n", encoding="utf-8")
    tables_api.merge(left, right, on="country,year", run=str(tmp_path),
                     map_file=str(tmp_path / "cw.csv"), allow_loss=0.6)
    rows = [json.loads(l) for l in
            (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    args = rows[-1]["args"]
    assert args["map_entries"] == 1 and args["allow_loss"] == 0.6
    assert args["rows_in"] == 2 and "loss_rate" in args


def test_reshape不是宽表报Refused(tmp_path):
    (tmp_path / "flat.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(errors.Refused):
        tables_api.reshape(str(tmp_path / "flat.csv"), run=str(tmp_path))


def test_reshape的行数与结论都在data里(tmp_path):
    (tmp_path / "wide.csv").write_text(
        "pid,y_w1,y_w2,x_w1,x_w2,z_w1,z_w2\n1,1,2,3,4,5,6\n2,7,8,9,1,2,3\n", encoding="utf-8")
    res = tables_api.reshape(str(tmp_path / "wide.csv"), run=str(tmp_path))
    assert res["data"]["rows_in"] == 2 and res["data"]["rows_out"] == 4
    assert Path(res["data"]["path"]).exists() and res["data"]["out"] == "long.csv"
    assert isinstance(res["data"]["notes"], list)


def test_写的命令目录不存在就建出来(tmp_path):
    """`profile` / `merge` / `reshape` 是写的命令：目录不存在正是常态，建。"""
    fresh = tmp_path / "还没有的目录"
    profile_api.profile(str(FIXTURES / "gss_like.csv"), run=str(fresh))
    assert (fresh / "profile.json").exists()


# ═════════════════════════ bind / power

from oss.api import binding as binding_api      # noqa: E402
from oss.api import power as power_api          # noqa: E402


def _profiled(tmp_path: Path) -> Path:
    run = tmp_path / "r"
    profile_api.profile(str(FIXTURES / "gss_like.csv"), run=str(run))
    (run / "ideas.json").write_text(json.dumps({
        "direction": "教育与地位", "ideas": [{
            "idea_id": "edu_001", "title": "T", "research_question": "Q",
            "constructs_needed": ["教育年限", "社会经济地位"]}]},
        ensure_ascii=False), encoding="utf-8")
    return run


def test_不带map时只给候选(tmp_path):
    run = _profiled(tmp_path)
    res = binding_api.candidates(str(run), idea="edu_001")
    assert res["data"]["idea_id"] == "edu_001"
    assert res["data"]["constructs"] and "column" in res["data"]["constructs"][0]["matches"][0]
    assert not (run / "binding.json").exists(), "只列候选不该落盘"


def test_规则给不出候选时把全表交出去(tmp_path):
    """**噪声比空白更糟**：给不出就说给不出，同时把全表的列摆出来让人自己判断。"""
    run = tmp_path / "r"
    profile_api.profile(str(FIXTURES / "did_clean.csv"), run=str(run))
    (run / "ideas.json").write_text(json.dumps({
        "direction": "政策冲击", "ideas": [{
            "idea_id": "edu_001", "title": "T", "research_question": "Q",
            "constructs_needed": ["处理时点", "结局"]}]}, ensure_ascii=False), encoding="utf-8")
    res = binding_api.candidates(str(run), idea="edu_001")
    assert res["data"]["rule_failed"] is True
    assert all(c["matches"] == [] and c["note"] for c in res["data"]["constructs"])
    assert {"name", "semantic_type", "missing_rate", "n_unique", "label"} <= set(
        res["data"]["all_variables"][0])


def test_带map时回验并落盘(tmp_path):
    run = _profiled(tmp_path)
    res = binding_api.bind(str(run), idea="edu_001",
                           mappings=["教育年限=educ:treatment", "社会经济地位=sei:outcome"])
    assert (run / "binding.json").exists()
    assert res["data"]["core_ok"] is True
    assert {"construct_name", "role", "status", "is_usable"} <= set(res["data"]["bindings"][0])
    assert res["data"]["needs_gap_columns"] is False


def test_map格式看不懂报BadInput(tmp_path):
    run = _profiled(tmp_path)
    with pytest.raises(errors.BadInput) as e:
        binding_api.bind(str(run), idea="edu_001", mappings=["乱写"])
    assert e.value.code == 1 and "看不懂" in e.value.summary
    assert not (run / "binding.json").exists()


def test_ideas里没有这个id报Missing(tmp_path):
    run = _profiled(tmp_path)
    with pytest.raises(errors.Missing) as e:
        binding_api.candidates(str(run), idea="nope")
    assert e.value.code == 1


def test_bind目录不存在时不建目录(tmp_path):
    """`bind` 读 profile.json / ideas.json，是只读前置的命令：打错路径要拒绝。

    顺手把目录建出来的话，报的会是「缺 ideas.json」——看着像「这一步还没做」，
    而真相是「这个目录根本不是你要的那个」。
    """
    missing = tmp_path / "没这个目录"
    with pytest.raises(errors.Missing) as e:
        binding_api.candidates(str(missing))
    assert e.value.code == 1 and "运行目录不存在" in e.value.summary
    assert not missing.exists()


def test_功效落盘且判词进summary(tmp_path):
    run = _profiled(tmp_path)
    res = power_api.power(str(run), idea="edu_001", effect=0.2,
                          effect_source="夹具：假想文献 X (2020) 表 3")
    assert (run / "power.json").exists()
    assert res["data"]["verdict"] in ("pass", "gray", "fail")
    assert res["data"]["reason_lines"] and isinstance(res["data"]["reason_lines"], list)
    assert res["data"]["verdict"] in res["summary"]
    assert res["data"]["warnings"] == [] and res["data"]["extra_path"] == ""


def test_效应量不给出处就警告且判不了pass(tmp_path):
    """判词整个建立在效应量上。没出处 = 没人选过这个数，那就不许发通过证。

    验收跑 r20260907_1938 与 r20260907_edufert_zh 都没传 --effect，
    拿命令行缺省的 0.2 算出 pass，论文却写「按文献报告的效应量」——那句是假的。
    """
    run = _profiled(tmp_path)
    res = power_api.power(str(run), idea="edu_001", effect=0.9)   # 大效应，本该 pass
    assert res["data"]["verdict"] != "pass", "无出处不许判 pass"
    assert any("出处" in w for w in res["data"]["warnings"]), res["data"]["warnings"]
    assert any("不许写「按文献报告的效应量」" in b for b in res["data"]["reason_lines"])

    ok = power_api.power(str(run), idea="edu_001", effect=0.9,
                         effect_source="Smith (2019) 表 2，d = 0.9")
    assert ok["data"]["verdict"] == "pass", "补上出处就该恢复"
    assert ok["data"]["warnings"] == []


def test_功效另存不覆盖(tmp_path):
    run = _profiled(tmp_path)
    res = power_api.power(str(run), idea="edu_001", effect=0.2, out="power_interaction.json")
    assert (run / "power_interaction.json").exists()
    assert res["data"]["extra_path"].endswith("power_interaction.json")


def test_算不出ρx时把警告放进data而不是吞掉(tmp_path):
    """ρx 用了假定值这件事必须说出来——它会一路传进论文局限。"""
    run = _profiled(tmp_path)
    res = power_api.power(str(run), idea="edu_001", effect=0.2,
                          cluster_col="没这列", treatment_col="也没这列")
    assert res["data"]["warnings"] and "ρx 用假定值" in res["data"]["warnings"][0]


def test_power目录不存在时不建目录(tmp_path):
    missing = tmp_path / "没这个目录"
    with pytest.raises(errors.Missing) as e:
        power_api.power(str(missing))
    assert e.value.code == 1 and not missing.exists()


# ═════════════════════════ run（正式 + 探索）

from oss.api import analysis as analysis_api      # noqa: E402


def _did_run(tmp_path: Path) -> Path:
    """一个能跑 did 的运行目录：数据 + 画像 + ideas.json + plan.json。"""
    import shutil
    run = tmp_path / "r"
    run.mkdir()
    shutil.copy(FIXTURES / "did_clean.csv", run / "data.csv")
    profile_api.profile(str(run / "data.csv"), run=str(run))
    (run / "ideas.json").write_text(json.dumps({"direction": "政策与产出", "ideas": []},
                                               ensure_ascii=False), encoding="utf-8")
    (run / "plan.json").write_text(json.dumps({
        "plan_id": "p001", "idea_id": "edu_001", "estimator": "did", "outcome": "y",
        "unit": "unit", "time": "year", "treat_time": "treat_time",
        "cluster_level": "unit"}), encoding="utf-8")
    return run


def test_正式跑落盘冻锚点推阶段(tmp_path):
    run = _did_run(tmp_path)
    res = analysis_api.run(str(run))
    assert (run / "results.json").exists()
    assert res["data"]["n"] > 0 and res["data"]["estimates"]
    assert res["data"]["artifact"] == "results.json"
    assert res["data"]["explore"] is False
    assert state_mod.load(run).stage == "6-run"
    assert state_mod.load(run).anchor["idea_id"] == "edu_001"
    # 散文打的那几段，data 里各有出处
    assert res["data"]["diagnostics"] and res["data"]["event_study"]
    assert isinstance(res["data"]["warnings"], list) and res["data"]["warnings"]


def test_缺计划报Missing(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    with pytest.raises(errors.Missing) as e:
        analysis_api.run(str(run))
    assert e.value.artifact == "plan" and e.value.code == 1


def test_跑分析目录不存在时不建目录(tmp_path):
    """`run` 是写的命令，但它要的是一个**已经画过像、有计划**的目录——
    打错路径时报「缺 plan.json」等于把「目录错了」说成「这一步还没做」。"""
    missing = tmp_path / "没这个目录"
    with pytest.raises(errors.Missing) as e:
        analysis_api.run(str(missing))
    assert e.value.code == 1 and e.value.artifact == "run"
    assert not missing.exists()


def test_没explore却给plan被拒(tmp_path):
    run = _did_run(tmp_path)
    with pytest.raises(errors.BadInput) as e:
        analysis_api.run(str(run), explore_plan=str(run / "plan.json"))
    assert e.value.code == 1 and "只在 --explore 下有效" in e.value.summary
    assert not (run / "results.json").exists()


def test_explore不带plan被拒(tmp_path):
    run = _did_run(tmp_path)
    with pytest.raises(errors.BadInput) as e:
        analysis_api.run(str(run), explore=True, for_id="pretrend_fail")
    assert e.value.code == 1 and "要配 --plan" in e.value.summary


def test_探索门拒绝时留痕且不落results(tmp_path):
    run = _did_run(tmp_path)
    with pytest.raises(errors.Refused) as e:
        analysis_api.run(str(run), explore=True, for_id="pretrend_fail",
                         explore_plan=str(run / "plan.json"))
    assert e.value.code == 1 and e.value.problems
    assert not (run / "results.json").exists()
    rows = _rows(run)
    assert rows[-1]["step"] == "explore" and rows[-1]["outcome"] == "refused"


def _probe(run: Path) -> Path:
    (run / "route.json").write_text(json.dumps({"plan_sha": "s", "items": [
        {"id": "pretrend_fail", "priority": "must", "trigger": "t", "kind": "disclose"}]}),
        encoding="utf-8")
    probe = run / "probe.json"
    probe.write_text(json.dumps({
        "plan_id": "p001", "idea_id": "edu_001", "estimator": "did", "outcome": "y",
        "unit": "unit", "time": "year", "treat_time": "treat_time",
        "cluster_level": "unit", "sample_filter": "year >= 2005"}), encoding="utf-8")
    return probe


def test_探索通过时结果进explore不进results(tmp_path):
    run = _did_run(tmp_path)
    probe = _probe(run)
    res = analysis_api.run(str(run), explore=True, for_id="pretrend_fail",
                           explore_plan=str(probe))
    assert res["data"]["explore"] is True and res["data"]["n_used"] == 1
    assert Path(res["data"]["path"]).name == "pretrend_fail_1.results.json"
    assert not (run / "results.json").exists()
    assert not (run / state_mod.FILE).exists(), "探索不冻结锚点、不推进阶段"
    stamped = json.loads(Path(res["data"]["path"]).read_text(encoding="utf-8"))
    assert stamped["plan_id"] == "explore:pretrend_fail#1"


def test_探索换数据被拒(tmp_path):
    """换规格是探索，换数据是另一件事——两次的数字不可比，留痕却只会说「探索了一次」。"""
    import shutil
    run = _did_run(tmp_path)
    probe = _probe(run)
    other = tmp_path / "别的数据.csv"
    shutil.copy(FIXTURES / "did_clean.csv", other)
    with pytest.raises(errors.Refused) as e:
        analysis_api.run(str(run), explore=True, for_id="pretrend_fail",
                         explore_plan=str(probe), data=str(other))
    assert e.value.code == 1 and any("不能换数据" in p for p in e.value.problems)
    assert not (run / "explore").exists()


def test_计划过不了校验时把警告也交出去(tmp_path):
    """散文在 ✗ 之前先打 ⚠——警告要跟着异常走，不然拒绝路径上那几行就没了。"""
    run = _did_run(tmp_path)
    (run / "plan.json").write_text(json.dumps({
        "plan_id": "p001", "idea_id": "edu_001", "estimator": "did", "outcome": "没这列",
        "unit": "unit", "time": "year", "treat_time": "treat_time",
        "cluster_level": "unit"}), encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        analysis_api.run(str(run))
    assert e.value.code == 1 and e.value.problems
    assert e.value.data["stale_profile_hint"] is True
    assert e.value.data["warnings"]
    assert not (run / "results.json").exists()
    assert not (run / state_mod.FILE).exists(), "被拒的跑不许冻锚点"
    assert _rows(run)[-1]["outcome"] == "refused"


# ═════════════════════════ figure

from oss.api import figure as figure_api          # noqa: E402


def test_认不出的图名报BadInput(tmp_path):
    with pytest.raises(errors.BadInput) as e:
        figure_api.figure("nope", run=str(tmp_path))
    assert e.value.code == 2 and "不认识的图" in e.value.summary


def test_缺results时coef报Missing(tmp_path):
    run = _did_run(tmp_path)
    with pytest.raises(errors.Missing) as e:
        figure_api.figure("coef", run=str(run))
    assert e.value.code == 2 and e.value.artifact == "results"


def test_画图目录不存在时不建目录(tmp_path):
    missing = tmp_path / "没这个目录"
    with pytest.raises(errors.Missing) as e:
        figure_api.figure("coef", run=str(missing))
    assert e.value.code == 1 and e.value.artifact == "run"
    assert not missing.exists()


def test_画出来的图带图注与语言(tmp_path):
    pytest.importorskip("matplotlib")
    run = _did_run(tmp_path)
    analysis_api.run(str(run))
    # **缺省是中文**（2026-09-25 改）：写中文论文是缺省路径，用户明说写英文才走 en。
    # 从前缺省 en，中文稿要模型自己记得传 --lang zh，忘了就得在 paper check 那步返工
    res = figure_api.figure("coef", run=str(run))
    assert Path(res["data"]["path"]).exists()
    assert res["data"]["lang"] == "zh" and res["data"]["caption"]
    res_en = figure_api.figure("coef", run=str(run), lang="en")
    assert res_en["data"]["lang"] == "en" and res_en["data"]["caption"]
    assert res["data"]["kind_label"] == "主回归系数"
    assert res["data"]["name"] == "fig_coef.png"
    assert _rows(run)[-1]["step"] == "figure"


def test_冒充的results不给画图(tmp_path):
    run = _did_run(tmp_path)
    analysis_api.run(str(run))
    raw = json.loads((run / "results.json").read_text(encoding="utf-8"))
    raw["plan_id"] = "explore:pretrend_fail#1"
    (run / "results.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        figure_api.figure("coef", run=str(run))
    assert e.value.code == 1 and "探索不进论文" in e.value.summary


def test_分箱那行仍在画图之前(tmp_path):
    """老代码把「分箱斜率取自 results.json」打在**画图之前**（marginal 分支里）。

    抽出之后 api 不回显，这一行只能靠 `data["log"]` 保住位置——
    门面先按序打 log，再打图注与 ✓，顺序跟从前一模一样。
    """
    pytest.importorskip("matplotlib")
    run = _did_run(tmp_path)
    (run / "plan.json").write_text(json.dumps({
        "plan_id": "p001", "idea_id": "edu_001", "estimator": "ols_fe", "outcome": "y",
        "treatment": "x", "fixed_effects": ["unit"], "cluster_level": "unit",
        "binned": {"by": "year", "bins": 3}}), encoding="utf-8")
    analysis_api.run(str(run))
    res = figure_api.figure("marginal", run=str(run), by="year")
    assert res["data"]["binned_from_results"] is True
    assert res["data"]["log"][0] == [
        "out", "  分箱斜率取自 results.json（各箱内含主规格的控制变量与固定效应）"]


def test_画图依赖缺席时那行回显跟着异常一起交出来(tmp_path, monkeypatch):
    """**被拒的那条路也得把 log 带上。**

    老代码那一行是当场 `typer.echo` 的：`figures_mod.marginal` 抛 FiguresUnavailable
    的时候，它已经在屏幕上了。异常里不带 log，散文就少一行——而 golden 盖不到
    （要 plan.binned 与 results.binned 同时命中，还要画图依赖恰好缺席）。
    """
    from oss.core import figures as figures_mod
    run = _did_run(tmp_path)
    (run / "plan.json").write_text(json.dumps({
        "plan_id": "p001", "idea_id": "edu_001", "estimator": "ols_fe", "outcome": "y",
        "treatment": "x", "fixed_effects": ["unit"], "cluster_level": "unit",
        "binned": {"by": "year", "bins": 3}}), encoding="utf-8")
    analysis_api.run(str(run))

    def _boom(*a, **k):
        raise figures_mod.FiguresUnavailable("画图要 matplotlib")
    monkeypatch.setattr(figures_mod, "marginal", _boom)

    with pytest.raises(errors.Unavailable) as e:
        figure_api.figure("marginal", run=str(run), by="year")
    assert e.value.code == 1
    assert e.value.data["install_extra"] == "opensocialscience[figures]", "原有的键不许丢"
    assert e.value.data["log"] == [
        ["out", "  分箱斜率取自 results.json（各箱内含主规格的控制变量与固定效应）"]]


def test_marginal缺by报BadInput(tmp_path):
    run = _did_run(tmp_path)
    with pytest.raises(errors.BadInput) as e:
        figure_api.figure("marginal", run=str(run))
    assert e.value.code == 2 and "--by" in e.value.summary


# ═════════════════════════ paper check / render / export

from oss.api import paper as paper_api            # noqa: E402
from oss.core import manuscript                   # noqa: E402
from oss.core.contracts import (                  # noqa: E402
    AnalysisResults, Estimate, SpecSummary,
)

#: 能过校验、也过得了导出那道「有没有主结果表」的最小骨架
_PAPER_OK = ("# T\n\n## Abstract\nEffect {{result:main.coef}} (n = {{result:main.n}}).\n\n"
             "## Introduction\nx\n\n## Data and Methods\nx\n\n"
             "## Results\n*Table 1. Main estimates.*\n{{table:main}}\n\n## Discussion\nx\n")


def _min_results(run: Path) -> None:
    """一份最小的正式结果：够 paper check 走完，又不带任何 must 级处方。"""
    AnalysisResults(plan_id="p", n=100,
                    estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe")).save(run / "results.json")


def test_缺paper报Missing(tmp_path):
    run = _did_run(tmp_path)
    with pytest.raises(errors.Missing) as e:
        paper_api.check(str(run))
    assert e.value.artifact == "paper" and e.value.code == 1


def test_缺results也报Missing(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    (run / "paper.md").write_text("# T", encoding="utf-8")
    with pytest.raises(errors.Missing) as e:
        paper_api.check(str(run))
    assert e.value.artifact == "results"


def test_paper三条命令都不许把目录建出来(tmp_path):
    """打错路径时凭空造一个空运行目录，报的就成了「缺 paper.md」——那是另一回事。"""
    missing = tmp_path / "没这个目录"
    for fn in (paper_api.check, paper_api.render, paper_api.export):
        with pytest.raises(errors.Missing) as e:
            fn(str(missing))
        assert e.value.code == 1 and e.value.artifact == "run"
        assert not missing.exists()


def test_校验不过时警告也带出来且留痕(tmp_path):
    run = _did_run(tmp_path)
    analysis_api.run(str(run))
    (run / "paper.md").write_text("# T\n\n## 摘要\n效应是 0.123。\n", encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        paper_api.check(str(run))
    assert e.value.code == 1 and e.value.summary == "稿件过不了校验："
    # 光断言「是个 list」的话，警告**全丢**也照样绿：门面在 ✗ 之前打的那几行
    # 会一声不响地消失。所以点名要非空、且认得出是哪几条
    warns = e.value.data["warnings"]
    assert warns, "警告丢了：散文那一侧 ✗ 之前的 ⚠ 行会跟着一起消失"
    assert any("全文没有任何占位符" in w for w in warns), warns
    assert any("摘要没有任何具体数字" in w for w in warns), warns
    assert any("全文没有主结果表" in w for w in warns), warns
    rows = [json.loads(l) for l in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    assert rows[-1]["step"] == "paper" and rows[-1]["outcome"] == "refused"
    assert state_mod.load(run).stage == "7-paper"


def test_校验通过时那两行的数都在data里(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _min_results(run)
    (run / "paper.md").write_text(_PAPER_OK, encoding="utf-8")
    # 代理构念的三栏没填：这是**警告不是问题**——校验照样过，但那一行必须还在。
    # 不给它一条真警告的话，`warnings` 恒为 []，断言「是个 list」等于什么都没查
    (run / "binding.json").write_text(json.dumps({
        "idea_id": "edu_001", "core_ok": True,
        "bindings": [{"construct_name": "城乡收入差距", "role": "treatment",
                      "column": "gap_ratio", "status": "proxy", "coverage": 1.0}]},
        ensure_ascii=False), encoding="utf-8")
    res = paper_api.check(str(run))
    assert res["ok"] is True and res["data"]["exit_code"] == 0
    assert res["data"]["n_sections"] == len(manuscript.REQUIRED_SECTIONS)
    assert "{{result:main.coef}}" in res["data"]["placeholders"]
    warns = res["data"]["warnings"]
    assert warns, "通过那条路也要把警告交出来——门面按它打 ⚠ 行"
    assert any("城乡收入差距" in w and "三栏没填全" in w for w in warns), warns
    st = state_mod.load(run)
    assert st.stage == "7-paper" and st.stage_status == "done"


def test_render校验不过时不写渲染稿(tmp_path):
    run = _did_run(tmp_path)
    analysis_api.run(str(run))
    (run / "paper.md").write_text("# T\n\n## 摘要\n效应是 0.123。\n", encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        paper_api.render(str(run))
    assert e.value.summary == "校验没过，不给渲染："
    assert not (run / "paper_rendered.md").exists()


def test_render把路径放进data(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _min_results(run)
    (run / "paper.md").write_text(_PAPER_OK, encoding="utf-8")
    res = paper_api.render(str(run))
    assert Path(res["data"]["path"]).exists()
    assert (run / "paper_rendered.md").exists()


def test_导出前锚点门排在轮次门前面(tmp_path):
    run = _did_run(tmp_path)
    analysis_api.run(str(run))
    (run / "paper_rendered.md").write_text("# T", encoding="utf-8")
    raw = json.loads((run / "plan.json").read_text(encoding="utf-8"))
    raw["idea_id"] = "edu_999"
    (run / "plan.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        paper_api.export(str(run))
    assert e.value.code == 7, "锚点门是 7，不是没审过的 5"
    assert e.value.problems, "两条一起报全"


def test_缺主结果表时导出退出码四(tmp_path):
    run = _did_run(tmp_path)
    analysis_api.run(str(run))
    (run / "paper_rendered.md").write_text("# T", encoding="utf-8")
    (run / "paper.md").write_text("# T\n没有表", encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        paper_api.export(str(run))
    assert e.value.code == 4 and "{{table:main}}" in e.value.summary


def test_没审过时导出退出码五(tmp_path):
    run = _did_run(tmp_path)
    analysis_api.run(str(run))
    (run / "paper_rendered.md").write_text("# T", encoding="utf-8")
    (run / "paper.md").write_text("# T\n{{table:main}}", encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        paper_api.export(str(run))
    assert e.value.code == 5 and "没经过任何跨模型审稿" in e.value.summary


def test_轮次耗尽那条警告留在它那道门的位置(tmp_path):
    """「⚠ 轮次耗尽」原先是**边走边打**的：打完继续往下走，被处方门拦下时它已经在屏幕上了。

    攒进一个 `warnings` 一起打在最前会把它挪到 ✗ 前面——所以走 `data["log"]`
    （跟 figure 同一套办法），门面按序打，位置一个都不动。
    """
    run = _did_run(tmp_path)
    analysis_api.run(str(run))
    (run / "paper_rendered.md").write_text("# T", encoding="utf-8")
    (run / "paper.md").write_text(_PAPER_OK, encoding="utf-8")
    for name, rnd in (("review_paper_rendered_a.json", 1),
                      ("review_paper_rendered_a_r2.json", 2)):
        _review(run, name, "FAIL", ["[设计] 平行趋势不成立"], rnd)
    with pytest.raises(errors.Refused) as e:
        paper_api.export(str(run))
    assert e.value.code == 6, "轮次耗尽是提醒不是拒绝，往下走才被处方门拦住"
    log = e.value.data["log"]
    assert any(s == "err" and "轮次耗尽" in t for s, t in log), log
    assert e.value.summary.startswith("不给导出：")


def test_没装pandoc时是Unavailable不是Refused(tmp_path, monkeypatch):
    """审稿后端、pandoc 这类外部依赖用不了**不是「通过」**，也不是「拒绝」。"""
    run = tmp_path / "r"; run.mkdir()
    _min_results(run)
    (run / "paper.md").write_text(_PAPER_OK, encoding="utf-8")
    (run / "paper_rendered.md").write_text("# T\n", encoding="utf-8")
    _review(run, "review_paper_rendered_a.json", "PASS", [], 1)
    monkeypatch.setattr(paper_api.shutil, "which", lambda name: None)
    with pytest.raises(errors.Unavailable) as e:
        paper_api.export(str(run))
    assert e.value.code == 1 and "没装 pandoc" in e.value.summary


# ═════════════════════════ lit / data（网络那几条一律 monkeypatch）
#
# 这两组命令是链条上**唯一会打网络**的：文献检索、按 DOI 补录、抓全文、拉指标、下数据包。
# 测试一条都不许真连——不是为了快，是为了「测试结果不取决于今天 OpenAlex 通不通」。
# 所以每条网络路径都在 api 模块的命名空间上 monkeypatch（`lit_api.lit_module.search`
# 而不是 `oss.core.literature.search`）：**打的补丁必须是被调用的那个名字。**

from oss.api import data as data_api                   # noqa: E402
from oss.api import lit as lit_api                     # noqa: E402
from oss.core.contracts import LiteratureCorpus, PaperRecord   # noqa: E402


def _corpus(run: Path, records: list[dict] | None = None) -> None:
    LiteratureCorpus(records=[PaperRecord(**r) for r in (records or [])]).save(
        run / "literature.json")


# ─────────────────────────── lit search


def test_lit_search落盘并给阵营(tmp_path, monkeypatch):
    fake = LiteratureCorpus(records=[], hits_by_source={"openalex": 0},
                            hits_by_query={"q": 3}, camps={"q": "阵营A"}, notes=["一条提醒"])
    monkeypatch.setattr(lit_api.lit_module, "search", lambda *a, **k: fake)
    res = lit_api.search(["q"], run=str(tmp_path))
    assert (tmp_path / "literature.json").exists()
    d = res["data"]
    assert d["camps"] == {"q": "阵营A"} and d["notes"] == ["一条提醒"]
    assert d["hits_by_query"] == {"q": 3} and d["n_records"] == 0
    assert d["artifact"] == "literature.json" and Path(d["path"]).exists()


def test_lit_search目录不存在时不建目录(tmp_path, monkeypatch):
    """环节 2 跑在一个**已经画过像**的目录上（SKILL.md 第二步就 mkdir 了）。
    打错路径时把语料写进一个凭空建出来的空目录，下一条命令会说
    「缺 literature.json——先跑 oss lit search」——把「路径错了」说成「这步没做」。"""
    monkeypatch.setattr(lit_api.lit_module, "search",
                        lambda *a, **k: pytest.fail("目录都不存在，不该去连网"))
    missing = tmp_path / "没有这个目录"
    with pytest.raises(errors.Missing) as e:
        lit_api.search(["q"], run=str(missing))
    assert e.value.artifact == "run" and not missing.exists()


# ─────────────────────────── lit add


def test_lit_add缺语料报Missing(tmp_path):
    with pytest.raises(errors.Missing) as e:
        lit_api.add("10.1/x", run=str(tmp_path))
    assert "先跑 oss lit search" in e.value.summary and e.value.code == 1


def test_lit_add重复的DOI不再抓一次(tmp_path, monkeypatch):
    _corpus(tmp_path, [{"paper_id": "W1", "title": "T", "doi": "10.1/X"}])
    monkeypatch.setattr(lit_api.lit_module, "fetch_by_doi",
                        lambda *a, **k: pytest.fail("已经有了就不该再抓"))
    res = lit_api.add("10.1/x", run=str(tmp_path))
    assert res["ok"] is True and res["data"]["already"] is True
    assert res["summary"] == "语料里已经有 10.1/x 了"


def test_lit_add源不可用是Unavailable不是没查到(tmp_path, monkeypatch):
    """**源坏了不等于这篇文献不存在。**两者混起来会教模型把「抓不到」当成「没有」。"""
    _corpus(tmp_path)

    def _boom(*a, **k):
        raise lit_api.lit_module.SourceUnavailable("crossref", "502")

    monkeypatch.setattr(lit_api.lit_module, "fetch_by_doi", _boom)
    with pytest.raises(errors.Unavailable) as e:
        lit_api.add("10.1/x", run=str(tmp_path))
    assert e.value.code == 1 and "抓不到" in e.value.summary


def test_lit_add查不到这个DOI报Missing(tmp_path, monkeypatch):
    _corpus(tmp_path)
    monkeypatch.setattr(lit_api.lit_module, "fetch_by_doi", lambda *a, **k: None)
    with pytest.raises(errors.Missing) as e:
        lit_api.add("10.1/x", run=str(tmp_path))
    assert e.value.artifact == "record" and "核对一下 DOI" in e.value.summary


def test_lit_add补进去之后语料里多一条(tmp_path, monkeypatch):
    _corpus(tmp_path)
    monkeypatch.setattr(lit_api.lit_module, "fetch_by_doi",
                        lambda *a, **k: PaperRecord(paper_id="W9", title="新的一篇",
                                                    year=1967, doi="10.1/x"))
    res = lit_api.add("10.1/x", run=str(tmp_path))
    d = res["data"]
    assert d["paper_id"] == "W9" and d["already"] is False
    assert d["cite"] == "{{cite:W9}}" and d["year"] == 1967
    assert len(LiteratureCorpus.load(tmp_path / "literature.json").records) == 1


# ─────────────────────────── lit fetch


def test_lit_fetch缺语料报Missing(tmp_path):
    with pytest.raises(errors.Missing) as e:
        lit_api.fetch(str(tmp_path))
    assert e.value.artifact == "literature" and e.value.code == 1


def test_lit_fetch点名的id有几个不在语料里就警告(tmp_path, monkeypatch):
    _corpus(tmp_path, [{"paper_id": "W1", "title": "T"}])
    monkeypatch.setattr(lit_api.reading_mod, "fetch", lambda *a, **k: [
        lit_api.reading_mod.FetchItem(paper_id="W1", title="T", level="abstract", chars=10)])
    res = lit_api.fetch(str(tmp_path), ids="W1,W404")
    assert res["ok"] is True
    assert res["data"]["warnings"] and "W404" in res["data"]["warnings"][0]
    assert res["data"]["items"][0]["level"] == "abstract"


def test_lit_fetch点名的id一个都不在语料里报Missing(tmp_path, monkeypatch):
    """警告跟着拒绝一起走——门面在 ✗ 之前把它打出来，位置跟从前一样。"""
    _corpus(tmp_path, [{"paper_id": "W1", "title": "T"}])
    monkeypatch.setattr(lit_api.reading_mod, "fetch",
                        lambda *a, **k: pytest.fail("一篇都没挑中就别去抓"))
    with pytest.raises(errors.Missing) as e:
        lit_api.fetch(str(tmp_path), ids="W404,W405")
    assert e.value.code == 1 and e.value.artifact == "records"
    assert e.value.data["warnings"], "缺的那几个 id 要报出来"


# ─────────────────────────── lit cards


def test_lit_cards缺文件报Missing(tmp_path):
    with pytest.raises(errors.Missing) as e:
        lit_api.cards(str(tmp_path))
    assert e.value.artifact == "reading"


def test_lit_cards格式不对报BadInput(tmp_path):
    (tmp_path / "reading_cards.json").write_text("{ 不是 JSON", encoding="utf-8")
    with pytest.raises(errors.BadInput) as e:
        lit_api.cards(str(tmp_path))
    assert e.value.code == 1 and "格式不对" in e.value.summary


def test_lit_cards过不了校验是Refused且警告跟着走(tmp_path):
    (tmp_path / "reading").mkdir()
    (tmp_path / "reading" / "W1.txt").write_text("太短", encoding="utf-8")
    (tmp_path / "reading_cards.json").write_text(json.dumps({
        "direction": "d", "cards": [
            {"paper_id": "W1", "source_level": "fulltext",
             "limitations": [{"quote": "太短"}]},
            {"paper_id": "W2", "source_level": "fulltext"}]},
        ensure_ascii=False), encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        lit_api.cards(str(tmp_path))
    assert e.value.code == 1 and e.value.summary == "读书卡过不了校验："
    assert any("本地没有这篇的文本" in p for p in e.value.problems)
    assert any("引文太短" in w for w in e.value.data["warnings"])
    trace = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert '"outcome":"refused"' in trace.replace(" ", "")


def test_lit_cards过了校验给张数与层级(tmp_path):
    (tmp_path / "reading").mkdir()
    (tmp_path / "reading" / "W1.txt").write_text("x" * 100, encoding="utf-8")
    (tmp_path / "reading_cards.json").write_text(json.dumps({
        "direction": "d",
        "cards": [{"paper_id": "W1", "source_level": "fulltext"}]},
        ensure_ascii=False), encoding="utf-8")
    res = lit_api.cards(str(tmp_path))
    d = res["data"]
    assert d["n_cards"] == 1 and d["n_quotes"] == 0 and d["levels"] == {"fulltext": 1}


# ─────────────────────────── lit verify

#: 三条引用刚好压住三种判词：有摘要且命中 → 支持、有摘要但一个都没命中 → 存疑、
#: 没有摘要 → 核不了。第四条指向语料里没有的编号，走「找不到就跳过」那一支。
_CITED_PAPER = (
    "# 引言\n\n"
    "已有研究指出，教育扩张提高了社会流动 {{cite:W001}}。\n\n"
    "也有人认为，最低工资上调并不减少就业 {{cite:W002}}。\n\n"
    "另有综述给出相反的证据 {{cite:W003}}。\n\n"
    "还有一条指向语料里没有的编号 {{cite:W404}}。\n"
)
_CITED_RECORDS = [
    {"paper_id": "W001", "title": "Educational expansion and social mobility",
     "year": 1997, "authors": ["Breen, R."], "doi": "10.1000/w001",
     "abstract": "We ask whether educational expansion raises social mobility "
                 "across cohorts.", "source": "openalex"},
    {"paper_id": "W002", "title": "A field experiment on commuting",
     "year": 1994, "authors": ["Card, D."], "doi": "10.1000/w002",
     "abstract": "A case study of coastal weather patterns.", "source": "openalex"},
    {"paper_id": "W003", "title": "A review without an abstract",
     "year": 2001, "authors": ["Duncan, O. D."], "doi": "10.1000/w003",
     "source": "crossref"},
]


def _cited(run: Path) -> None:
    (run / "paper.md").write_text(_CITED_PAPER, encoding="utf-8")
    _corpus(run, _CITED_RECORDS)


def test_lit_verify目录不存在时不建目录(tmp_path):
    """跟 lit search 同一条理由，反过来的一面：`verify` **只读**两份产出，
    两份都不该由它自己造，所以打错路径只能是拒绝。"""
    missing = tmp_path / "没有这个目录"
    with pytest.raises(errors.Missing) as e:
        lit_api.verify(str(missing))
    assert e.value.artifact == "run" and e.value.code == 1 and not missing.exists()


def test_lit_verify缺稿子报Missing(tmp_path):
    _corpus(tmp_path, _CITED_RECORDS)
    with pytest.raises(errors.Missing) as e:
        lit_api.verify(str(tmp_path))
    assert e.value.artifact == "paper" and e.value.code == 1
    assert e.value.summary == "缺 paper.md"


def test_lit_verify缺语料报Missing(tmp_path):
    (tmp_path / "paper.md").write_text(_CITED_PAPER, encoding="utf-8")
    with pytest.raises(errors.Missing) as e:
        lit_api.verify(str(tmp_path))
    assert e.value.artifact == "literature" and e.value.code == 1
    assert e.value.summary == "缺 literature.json"


def test_lit_verify一处引用都没有照样退零(tmp_path):
    """稿子里不写引用不是错误，只是没什么可核的——**不许因此判失败**，
    也不许落一个空的配对文件出来。"""
    (tmp_path / "paper.md").write_text("# 引言\n\n没有任何引用。\n", encoding="utf-8")
    _corpus(tmp_path, _CITED_RECORDS)
    res = lit_api.verify(str(tmp_path))
    assert res["ok"] is True and res["data"]["exit_code"] == 0
    assert res["data"]["n_pairs"] == 0 and res["data"]["pairs"] == []
    assert res["data"]["path"] == ""
    assert not (tmp_path / "citation_check.json").exists()
    assert res["summary"] == "论文里没有 {{cite:...}} / {{citep:...}} 引用，没什么可核的"


def test_lit_verify的citep一次引几篇会拆成几条pair(tmp_path):
    """`{{citep:a;b}}` 是一处引用、两篇文献——**每篇各判一次**，
    不能因为写在一个括注里就只核到其中一篇。"""
    (tmp_path / "paper.md").write_text(
        "# 引言\n\n括注式引用同时指向两篇教育与劳动的研究 {{citep:W001;W002}}。\n",
        encoding="utf-8")
    _corpus(tmp_path, _CITED_RECORDS)
    res = lit_api.verify(str(tmp_path))
    d = res["data"]
    assert d["n_pairs"] == 2
    assert [p["cite"] for p in d["pairs"]] == ["W001", "W002"]
    assert all(p["paper_id"] for p in d["pairs"])
    assert all("括注式引用" in p["论文原话"] for p in d["pairs"]), \
        "citep 拆开的每一条都用同一句原话——转述是针对整句写的，不是针对某一个 id"


def test_lit_verify只带一个id的citep也照常核(tmp_path):
    (tmp_path / "paper.md").write_text(
        "# 引言\n\n括注式只指向一篇 {{citep:W001}}。\n", encoding="utf-8")
    _corpus(tmp_path, _CITED_RECORDS)
    res = lit_api.verify(str(tmp_path))
    d = res["data"]
    assert d["n_pairs"] == 1 and d["pairs"][0]["cite"] == "W001"


def test_lit_verify叙述式与括注式混用两处都核(tmp_path):
    (tmp_path / "paper.md").write_text(
        "# 引言\n\n"
        "叙述式指向一篇 {{cite:W001}}。\n\n"
        "括注式指向另一篇 {{citep:W002}}。\n",
        encoding="utf-8")
    _corpus(tmp_path, _CITED_RECORDS)
    res = lit_api.verify(str(tmp_path))
    d = res["data"]
    assert d["n_pairs"] == 2
    assert {p["cite"] for p in d["pairs"]} == {"W001", "W002"}


def test_lit_verify的citep里有编号不在语料就跳过那一个其余照判(tmp_path):
    (tmp_path / "paper.md").write_text(
        "# 引言\n\n括注式引用一篇在语料里、一篇不在 {{citep:W001;W404}}。\n",
        encoding="utf-8")
    _corpus(tmp_path, _CITED_RECORDS)
    res = lit_api.verify(str(tmp_path))
    d = res["data"]
    assert d["n_pairs"] == 1 and d["pairs"][0]["cite"] == "W001"


def test_lit_verify三种判词各一条且找不到的编号跳过(tmp_path):
    _cited(tmp_path)
    res = lit_api.verify(str(tmp_path))
    d = res["data"]
    assert d["exit_code"] == 0 and d["n_pairs"] == 3, "W404 不在语料里，跳过不算一处"
    assert d["tally"] == {"支持": 1, "存疑": 1, "核不了": 1}
    assert [p["cite"] for p in d["pairs"]] == ["W001", "W002", "W003"]
    assert {p["机器判定"] for p in d["pairs"]} == {"支持", "存疑", "核不了"}
    assert all(p["理由"] for p in d["pairs"]), "每条都要给理由，跨模型那一层要读它"
    assert Path(d["path"]) == tmp_path / "citation_check.json"
    assert json.loads(Path(d["path"]).read_text(encoding="utf-8")) == d["pairs"]
    trace = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "oss lit verify" in trace and "citation_check.json" in trace


def test_lit_verify的next把run原样拼回去(tmp_path):
    """这一行是要照抄去跑的。规整成绝对路径就未必还指向同一个目录——
    调用方给的是什么，回显里就该是什么。"""
    _cited(tmp_path)
    given = f"{tmp_path}/"
    res = lit_api.verify(given)
    assert res["data"]["next"].endswith(f"--run {given}")
    assert "--kind citations --level deep" in res["data"]["next"]


def test_lit_verify的out改落盘位置(tmp_path):
    _cited(tmp_path)
    target = tmp_path / "别处.json"
    res = lit_api.verify(str(tmp_path), out=str(target))
    assert Path(res["data"]["path"]) == target and target.exists()
    assert not (tmp_path / "citation_check.json").exists()


def test_lit_verify的返回能原样序列化(tmp_path):
    """MCP 那一侧直接 json.dumps 整份结果——`re.Match` 之类漏进 data 就在这里炸。"""
    _cited(tmp_path)
    json.dumps(lit_api.verify(str(tmp_path)), ensure_ascii=False)


# ─────────────────────────── data list / info / columns


def test_data_list给的是候选不是答案():
    res = data_api.list_packs()
    packs = res["data"]["packs"]
    assert packs and {"name", "status", "level", "keys", "title"} <= set(packs[0])


def test_data_info认不出的名字退出码二():
    with pytest.raises(errors.BadInput) as e:
        data_api.info("nope")
    assert e.value.code == 2 and "可选" in e.value.summary


def test_data_info给码本引用与怎么拿():
    d = data_api.info("cgss")["data"]
    assert d["manual"] is True and d["how_to"] and d["citation"]
    assert d["status"] in ("manual", "incoming", "local")
    assert d["keys"] == {"unit": "id", "time": "year"}


def test_data_columns按词找列(tmp_path, monkeypatch):
    src = tmp_path / "x.csv"
    src.write_text("educ,paeduc,wtssall\n1,2,3\n", encoding="utf-8")
    monkeypatch.setattr(data_api.packs_mod, "source_file", lambda *a, **k: src)
    d = data_api.columns("gss", query="educ")["data"]
    assert d["n_index"] == 3 and d["n_rows"] == 2
    assert {r["name"] for r in d["rows"]} == {"educ", "paeduc"}
    assert d["has_labels"] is False and d["terms"] == ["educ"]


def test_data_columns要申请的退出码三(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise data_api.packs_mod.NeedsManualDownload("要先去申请")

    monkeypatch.setattr(data_api.packs_mod, "source_file", _boom)
    with pytest.raises(errors.Unavailable) as e:
        data_api.columns("cgss")
    assert e.value.code == 3


def test_data_columns没拿过的退出码二(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("还没拿过：先 oss data get gss")

    monkeypatch.setattr(data_api.packs_mod, "source_file", _boom)
    with pytest.raises(errors.BadInput) as e:
        data_api.columns("gss")
    assert e.value.code == 2 and "还没拿过" in e.value.summary


# ─────────────────────────── data get


def test_data_get要申请的退出码三(monkeypatch, tmp_path):
    def _boom(*a, **k):
        raise data_api.packs_mod.NeedsManualDownload("要先去申请")

    monkeypatch.setattr(data_api.packs_mod, "get", _boom)
    with pytest.raises(errors.Unavailable) as e:
        data_api.get("cgss", run=str(tmp_path))
    assert e.value.code == 3, "要申请**不是**参数错，也不是拿不下来"


def test_data_get名字不对退出码二(monkeypatch, tmp_path):
    monkeypatch.setattr(data_api.packs_mod, "get",
                        lambda *a, **k: (_ for _ in ()).throw(KeyError("没有 nope")))
    with pytest.raises(errors.BadInput) as e:
        data_api.get("nope", run=str(tmp_path))
    assert e.value.code == 2


def test_data_get别的错都是拿不下来退出码一(monkeypatch, tmp_path):
    monkeypatch.setattr(data_api.packs_mod, "get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("网断了")))
    with pytest.raises(errors.Unavailable) as e:
        data_api.get("gss", run=str(tmp_path))
    assert e.value.code == 1 and "拿不下来" in e.value.summary


def test_data_get给了run就先查目录再下载(monkeypatch, tmp_path):
    """打错路径不该在下完几百 MB 之后才发现。"""
    monkeypatch.setattr(data_api.packs_mod, "get",
                        lambda *a, **k: pytest.fail("目录都不存在，不该先下载"))
    missing = tmp_path / "没有这个目录"
    with pytest.raises(errors.Missing) as e:
        data_api.get("gss", run=str(missing))
    assert e.value.artifact == "run" and not missing.exists()


def test_data_get不给run就不碰运行目录(monkeypatch, tmp_path):
    monkeypatch.setattr(data_api.packs_mod, "get", lambda *a, **k: _pack_result(tmp_path))
    res = data_api.get("gss")
    assert res["ok"] is True and res["data"]["run"] == ""
    assert res["data"]["cite_name"] and res["data"]["converted"] is True


def _pack_result(tmp_path: Path):
    from oss.core.datapacks import PackResult
    return PackResult(
        name="gss", title="General Social Survey", path=str(tmp_path / "gss.csv"),
        raw=str(tmp_path / "gss.dta"), rows=1000, n_columns=5, columns=["a", "b"],
        years="1972–2022", keys={"unit": "id", "time": "year"}, source="local",
        sha256="ab", fetched_at="2026-08-29", citation="GSS 2022", codebook="c",
        license="l", weights="wtssall", converted=True, notes=["一条提醒"])


def test_data_get留痕记进给的运行目录(monkeypatch, tmp_path):
    monkeypatch.setattr(data_api.packs_mod, "get", lambda *a, **k: _pack_result(tmp_path))
    data_api.get("gss", run=str(tmp_path))
    trace = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "oss data get gss" in trace and '"sha256"' in trace.replace(" ", "")


# ─────────────────────────── data search / fetch


def test_data_search搜不到时候选是空列表(monkeypatch):
    monkeypatch.setattr(data_api.data_mod.Catalog, "search", lambda *a, **k: [])
    res = data_api.search("没有这种指标")
    assert res["ok"] is True and res["data"]["candidates"] == []
    assert "搜不到不等于没有" in res["summary"]


def test_data_search给的是候选不是答案(monkeypatch):
    monkeypatch.setattr(data_api.data_mod.Catalog, "search", lambda *a, **k: [
        data_api.data_mod.Candidate(source="wb", code="IT.NET.USER.ZS", name="Internet users")])
    d = data_api.search("internet")["data"]
    assert d["candidates"][0]["code"] == "IT.NET.USER.ZS"


def test_data_search目录拉不下来是Unavailable(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("连不上")

    monkeypatch.setattr(data_api.data_mod.Catalog, "search", _boom)
    with pytest.raises(errors.Unavailable) as e:
        data_api.search("x")
    assert e.value.code == 1 and "目录拉不下来" in e.value.summary


def test_data_fetch拉不下来是Unavailable(monkeypatch, tmp_path):
    def _boom(*a, **k):
        raise RuntimeError("404")

    monkeypatch.setattr(data_api.data_mod, "fetch", _boom)
    with pytest.raises(errors.Unavailable) as e:
        data_api.fetch("wb", "NOPE", run=str(tmp_path))
    assert e.value.code == 1 and "拉不下来" in e.value.summary


def test_data_fetch拉成了就落盘留痕(monkeypatch, tmp_path):
    def _fake(source, code, out, **kw):
        Path(out).write_text("iso3,year,v\nCHN,2000,1\n", encoding="utf-8")
        return data_api.data_mod.FetchResult(
            source=source, code=code, path=str(out), rows=1, countries=1, years="2000–2000",
            columns=["iso3", "year", "v"], url="https://x", fetched_at="2026-08-29",
            notes=["一条提醒"])

    monkeypatch.setattr(data_api.data_mod, "fetch", _fake)
    d = data_api.fetch("wb", "SP.POP.TOTL", run=str(tmp_path))["data"]
    assert d["name"] == "wb_sp_pop_totl.csv" and Path(d["path"]).exists()
    assert d["rows"] == 1 and d["notes"] == ["一条提醒"]
    assert "oss data fetch wb SP.POP.TOTL" in (tmp_path / "trace.jsonl").read_text(encoding="utf-8")


# ═════════════════════════ review：审稿后端由参数注入，测试永远不连网

from oss.api import review as review_api                # noqa: E402
from oss.host.reviewer import ReviewVerdict, ReviewerUnavailable   # noqa: E402


class _FakeRev:
    """假审稿后端。**签名跟老的一样**（没有 round_ / previous_findings）——
    第 1 轮不许多传参数，那是 `_review_with_downgrade` 的老规矩。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def review(self, files, level="normal", kind="generic", emphasis="", **kw):
        self.calls.append({"files": [Path(f).name for f in files], "level": level,
                           "kind": kind, "emphasis": emphasis, **kw})
        v = ReviewVerdict(verdict="WARN", reason="ok", findings=["[文字] x"])
        v.reviewer_model, v.executor_model = "deepseek-v4-flash", "claude-opus-4"
        v.reviewer_family, v.executor_family = "deepseek", "anthropic"
        return v


def _reviewable(tmp_path: Path) -> Path:
    run = tmp_path / "r"; run.mkdir()
    (run / "paper_rendered.md").write_text("# T", encoding="utf-8")
    (run / "plan.json").write_text(json.dumps({"plan_id": "p", "idea_id": "i",
                                               "estimator": "ols_fe", "outcome": "y",
                                               "treatment": "x"}), encoding="utf-8")
    (run / "results.json").write_text(json.dumps({"plan_id": "p", "n": 10, "estimates": {}}),
                                      encoding="utf-8")
    (run / "power.json").write_text("{}", encoding="utf-8")
    return run


def _prev(run: Path, name: str, findings: list[str], round_: int = 1,
          verdict: str = "FAIL") -> None:
    (run / name).write_text(json.dumps(
        {"verdict": verdict, "round": round_, "findings": findings}, ensure_ascii=False),
        encoding="utf-8")


def test_审稿后端从参数进来一次网都不连(tmp_path):
    run = _reviewable(tmp_path)
    rev = _FakeRev()
    res = review_api.review(str(run), paper=True, reviewer=rev)
    assert rev.calls and rev.calls[0]["kind"] == "paper"
    assert res["data"]["verdict"] == "WARN"
    assert Path(res["data"]["path"]).name.startswith("review_paper_rendered")
    assert res["data"]["kinds"]["文字"] == 1
    assert res["data"]["reviewer_model"] == "deepseek-v4-flash"
    assert res["data"]["reviewer_family"] == "deepseek"
    assert res["data"]["executor_model"] == "claude-opus-4"
    assert res["data"]["executor_family"] == "anthropic"
    assert res["data"]["round"] == 1 and res["data"]["findings"] == ["[文字] x"]
    # 缺件不拦，但**必须说出来**——这一行原先是边走边打的，现在进 log
    assert any("这几项审稿人核不了" in t for _, t in res["data"]["log"])


def test_第一轮不多传参数(tmp_path):
    run = _reviewable(tmp_path)
    rev = _FakeRev()
    review_api.review(str(run), paper=True, round_=1, reviewer=rev)
    assert "round_" not in rev.calls[0] and "previous_findings" not in rev.calls[0]


def test_第二轮带上一轮的意见但不带新发现(tmp_path):
    run = _reviewable(tmp_path)
    _prev(run, "review_paper_rendered_识别.json",
          ["[设计] 平行趋势不成立", "[新发现] 旧的新发现"])
    rev = _FakeRev()
    res = review_api.review(str(run), paper=True, round_=2, reviewer=rev)
    assert rev.calls[0]["round_"] == 2
    assert rev.calls[0]["previous_findings"] == ["[设计] 平行趋势不成立"]
    assert any("只判处置了没有" in t for _, t in res["data"]["log"])


def test_有编辑决定信时api只带决定信(tmp_path):
    run = _reviewable(tmp_path)
    _prev(run, "review_paper_rendered_甲.json", ["[设计] 甲说的"])
    _prev(run, "review_editor.json", ["[设计] 必须改 ｜ 平行趋势"])
    rev = _FakeRev()
    res = review_api.review(str(run), paper=True, round_=2, reviewer=rev)
    assert rev.calls[0]["previous_findings"] == ["[设计] 必须改 ｜ 平行趋势"]
    assert any("取自编辑综合的决定信" in t for _, t in res["data"]["log"])


def test_空决定信要回落到原始报告(tmp_path):
    """空的决定信不能把审稿人的意见全吃掉——回落的判据是这一档供出意见没有。"""
    run = _reviewable(tmp_path)
    _prev(run, "review_paper_rendered_a.json", ["[设计] 平行趋势不成立"])
    _prev(run, "review_paper_rendered_b.json", ["[设计]  平行趋势不成立"])
    _prev(run, "review_paper_rendered_c.json", ["[文字] 只有 c 提了"])
    _prev(run, "review_editor.json", [])
    rev = _FakeRev()
    res = review_api.review(str(run), paper=True, round_=2, reviewer=rev)
    assert rev.calls[0]["previous_findings"] == ["[设计] 平行趋势不成立", "[文字] 只有 c 提了"], \
        "空决定信要回落到原始报告，且几位提同一条只带一次"
    assert not any("取自编辑综合的决定信" in t for _, t in res["data"]["log"])


def test_轮次门在api里就拦住并留痕(tmp_path):
    run = _reviewable(tmp_path)
    with pytest.raises(errors.Refused) as e:
        review_api.review(str(run), paper=True, round_=3, reviewer=_FakeRev())
    assert e.value.code == 4 and "超过上限" in e.value.summary
    assert "确需再审" in e.value.summary
    rows = [json.loads(l) for l in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    assert rows[-1]["outcome"] == "refused" and rows[-1]["args"]["round"] == 3


def test_后端不可用不是通过(tmp_path):
    run = _reviewable(tmp_path)

    class _Dead:
        def review(self, *a, **k):
            raise ReviewerUnavailable("HTTP 500")

    with pytest.raises(errors.Unavailable) as e:
        review_api.review(str(run), paper=True, reviewer=_Dead())
    assert e.value.code == 1 and "不是「通过」" in "".join(e.value.problems)
    rows = [json.loads(l) for l in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    assert rows[-1]["outcome"] == "error" and rows[-1]["step"] == "review"


def test_没给文件也没给paper退出码二(tmp_path):
    run = _reviewable(tmp_path)
    with pytest.raises(errors.BadInput) as e:
        review_api.review(str(run), reviewer=_FakeRev())
    assert e.value.code == 2


def test_桌审缺成稿是Missing(tmp_path):
    run = tmp_path / "empty"; run.mkdir()
    with pytest.raises(errors.Missing) as e:
        review_api.review(str(run), desk=True, reviewer=_FakeRev())
    assert e.value.code == 1 and "先跑 oss paper render" in e.value.summary


def test_review打错路径不许顺手建目录(tmp_path):
    with pytest.raises(errors.Missing) as e:
        review_api.review(str(tmp_path / "nope"), paper=True, reviewer=_FakeRev())
    assert e.value.code == 1 and not (tmp_path / "nope").exists()


def test_降档重试的老签名一个字没变():
    """`tests/test_whole_output.py` 直接调 `oss.cli.main._review_with_downgrade`，
    五个位置参数、返回判词。**别名换了地方，签名不许换。**"""
    from oss.cli.main import _review_with_downgrade
    from oss.api.review import with_downgrade
    assert _review_with_downgrade is with_downgrade


def test_降档那一行进log不再直接打():
    """降档提示原先 `typer.echo` 在这里打，现在塞进 log 由门面打——位置不变。"""
    from oss.api.review import with_downgrade

    calls: list[str] = []

    class _Rev:
        def review(self, files, level, kind, emphasis):
            calls.append(level)
            if len(calls) == 1:
                raise ReviewerUnavailable("HTTP 524: <html>")
            return {"level": level}

    log: list[list[str]] = []
    v = with_downgrade(_Rev(), [], "deep", "paper", "", log=log)
    assert calls == ["deep", "normal"] and v["level"] == "normal"
    assert log == [["err", "  ⚠ 网关超时（HTTP 524: <h），自动从 deep 降到 normal 重试一次"]]


def test_lit_search_只给zh也能跑(tmp_path, monkeypatch):
    """只做中文综述是正当的做法——逼人先编一条用不上的英文检索式，只会换来一条乱写的式子。"""
    fake = LiteratureCorpus(records=[], hits_by_source={"openalex_zh": 0},
                            hits_by_query={"农民工 市民化": 0})
    monkeypatch.setattr(lit_api.lit_module, "search", lambda *a, **k: fake)
    res = lit_api.search([], run=str(tmp_path), zh=["农民工 市民化"])
    assert res["data"]["zh"] == ["农民工 市民化"] and res["data"]["queries"] == []
    assert (tmp_path / "literature.json").exists()


def test_lit_search_留痕里带得上zh检索式(tmp_path, monkeypatch):
    """留痕是「这一步做了什么」的唯一底稿。只做中文综述时留下的是 `oss lit search ''`——
    一条空检索式，看留痕的人复不出这一步，也看不出中文那半跑过。"""
    fake = LiteratureCorpus(records=[], hits_by_source={"openalex_zh": 0},
                            hits_by_query={"农民工 市民化": 0})
    monkeypatch.setattr(lit_api.lit_module, "search", lambda *a, **k: fake)
    lit_api.search([], run=str(tmp_path), zh=["本土: 农民工 市民化"])
    trace = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "--zh" in trace and "农民工 市民化" in trace
    assert "search ''" not in trace


def test_lit_search_两边都空报BadInput(tmp_path, monkeypatch):
    """一条检索式都没有就没有「检索」这回事，打出去只会换回全库前 N 篇。"""
    monkeypatch.setattr(lit_api.lit_module, "search",
                        lambda *a, **k: pytest.fail("一条检索式都没有，不该去连网"))
    with pytest.raises(errors.BadInput) as e:
        lit_api.search([], run=str(tmp_path))
    assert "至少给一条" in e.value.summary
