"""阶段 1 的三道门：轮次、锚点、探索。**判断归模型，放行归程序。**"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from oss.cli.main import app

runner = CliRunner()


def _trace_rows(rundir: Path) -> list[dict]:
    p = rundir / "trace.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _review(rundir: Path, name: str, verdict: str, findings: list[str], round_: int) -> None:
    (rundir / name).write_text(json.dumps(
        {"verdict": verdict, "findings": findings, "round": round_}, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────── 轮次门


def test_review超过上限被拒绝并留痕(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    r = runner.invoke(app, ["review", "--paper", "--round", "3", "--run", str(run)])
    assert r.exit_code == 4, r.output
    assert "超过上限" in r.output and "当前决定：" in r.output
    rows = _trace_rows(run)
    assert rows and rows[-1]["outcome"] == "refused" and rows[-1]["step"] == "review"
    assert rows[-1]["args"]["round"] == 3


def test_review加审必须带why(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    r = runner.invoke(app, ["review", "--paper", "--round", "3", "--extra-round", "--run", str(run)])
    assert r.exit_code == 4, r.output
    assert "确需再审" in r.output


def test_正好等于上限不拦(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    r = runner.invoke(app, ["review", "--round", "2", "--run", str(run)])
    assert r.exit_code == 2, r.output          # 过了门，停在「没给文件」
    assert not any(x["outcome"] == "refused" for x in _trace_rows(run))


def test_加审带why放行并留痕(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    r = runner.invoke(app, ["review", "--round", "3", "--extra-round", "--why", "审稿人换人重审",
                            "--run", str(run)])
    assert r.exit_code == 2, r.output          # 过了门，停在「没给文件」
    rows = _trace_rows(run)
    assert rows[-1]["step"] == "review" and rows[-1]["outcome"] == "ok"
    assert rows[-1]["args"]["why"] == "审稿人换人重审"


def test_循环已耗尽时重跑第2轮也被拒(tmp_path):
    """绕法：耗尽后重跑 --round 2 覆盖报告，让判词变 PASS。**按状态拦，不只按轮号。**"""
    run = tmp_path / "r"; run.mkdir()
    (run / "plan.json").write_text(json.dumps({"plan_id": "p", "idea_id": "i", "estimator": "ols_fe",
                                               "outcome": "y", "treatment": "x"}), encoding="utf-8")
    for n in "abc":                                    # 两轮都审全了，才是真的没事可做
        _review(run, f"review_paper_rendered_{n}.json", "FAIL", ["[设计] x"], 1)
        _review(run, f"review_paper_rendered_{n}_r2.json", "FAIL", ["[设计] y"], 2)
    r = runner.invoke(app, ["review", "--paper", "--round", "2", "--run", str(run)])
    assert r.exit_code == 4, r.output
    assert "循环已耗尽" in r.output and "export_with_open_items" in r.output


def test_最后一轮没审全时后面的审稿人不被拦(tmp_path):
    """三位互盲：A 的复审一落盘状态就是 exhausted，B、C 还没回来——**不许拦他们**。"""
    run = tmp_path / "r"; run.mkdir()
    (run / "plan.json").write_text(json.dumps({"plan_id": "p", "idea_id": "i", "estimator": "ols_fe",
                                               "outcome": "y", "treatment": "x"}), encoding="utf-8")
    for n in "abc":
        _review(run, f"review_paper_rendered_{n}.json", "FAIL", ["[设计] x"], 1)
    _review(run, "review_paper_rendered_a_r2.json", "PASS", [], 2)      # 只有 A 回来了
    r = runner.invoke(app, ["review", "--round", "2", "--run", str(run)])
    assert r.exit_code == 2, r.output          # 过了门，停在「没给文件」
    assert not any(x["outcome"] == "refused" for x in _trace_rows(run))


def test_桌审和编辑综合不受轮次门约束(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    for flag in ("--desk", "--editor", "--identification"):
        r = runner.invoke(app, ["review", flag, "--round", "3", "--run", str(run)])
        assert r.exit_code == 1, (flag, r.output)      # 过了门，停在「缺 paper_rendered.md」
        assert not any(x["outcome"] == "refused" for x in _trace_rows(run))


def test_loop_status第一行是决定(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    (run / "plan.json").write_text(json.dumps({"plan_id": "p", "idea_id": "i", "estimator": "ols_fe",
                                               "outcome": "y", "treatment": "x"}), encoding="utf-8")
    _review(run, "review_paper_rendered_a.json", "FAIL", ["[设计] x"], 1)
    _review(run, "review_paper_rendered_a_r2.json", "FAIL", ["[设计] y"], 2)
    r = runner.invoke(app, ["loop", "status", "--run", str(run)])
    first = r.output.splitlines()[0]
    assert first.startswith("决定 export_with_open_items"), r.output
    assert r.exit_code == 4


# ─────────────────────────── 锚点门

from oss.core import state as state_mod
from oss.gates import anchor as anchor_gate


def _ideas(rundir: Path, direction: str) -> None:
    (rundir / "ideas.json").write_text(json.dumps({"direction": direction, "ideas": []},
                                                  ensure_ascii=False), encoding="utf-8")


def _plan(rundir: Path, idea_id: str) -> None:
    (rundir / "plan.json").write_text(json.dumps({"plan_id": "p", "idea_id": idea_id, "estimator": "ols_fe",
                                                 "outcome": "y", "treatment": "x"}), encoding="utf-8")


def test_没冻结锚点时门不拦(tmp_path):
    _ideas(tmp_path, "A"); _plan(tmp_path, "edu_001")
    assert anchor_gate.check(tmp_path) == []


def test_半冻结的锚点判为被手改(tmp_path):
    """有方向/题号却没有冻结时间——**工具箱写不出这个形状**。

    `_anchor()` 六格一起写、`frozen_at` 必是时间戳，没冻过的运行 anchor 是空字典。
    所以这个组合只能是有人手写了 run_state.json，而说明书里模型只写
    ideas.json / plan.json / paper.md。

    真实来源：WorkBuddy 验收跑 r20260907_1938 的 run_state.json 就是这个形状
    （direction 有值、frozen_at 为 null），同时 ideas.json 的 direction 被写成了
    一串列名——两处的「研究方向」对不上，而当时没有任何一道门看得见。
    """
    _ideas(tmp_path, "A"); _plan(tmp_path, "edu_001")
    state_mod.update(tmp_path, anchor={"direction": "手写的方向", "idea_id": None,
                                       "plan_sha": None, "source": "model", "frozen_at": None})
    problems = anchor_gate.check(tmp_path)
    assert len(problems) == 1 and "半冻结" in problems[0], problems
    assert "手工改过" in problems[0] and "anchor switch" in problems[0]


def test_只有题号没有冻结时间也算被手改(tmp_path):
    _ideas(tmp_path, "A"); _plan(tmp_path, "edu_001")
    state_mod.update(tmp_path, anchor={"idea_id": "edu_001", "frozen_at": None})
    assert any("半冻结" in p for p in anchor_gate.check(tmp_path))


def test_真冻结过的锚点不会被误判(tmp_path):
    """走正规入口冻的锚点带时间戳，一条都不许报——误报会把这道门变成噪声。"""
    _ideas(tmp_path, "A"); _plan(tmp_path, "edu_001")
    state_mod.freeze_anchor(tmp_path, **state_mod.current_anchor(tmp_path))
    assert anchor_gate.check(tmp_path) == []


def test_idea或方向变了就拦(tmp_path):
    _ideas(tmp_path, "A"); _plan(tmp_path, "edu_001")
    state_mod.freeze_anchor(tmp_path, **state_mod.current_anchor(tmp_path))
    _plan(tmp_path, "edu_007")
    problems = anchor_gate.check(tmp_path)
    assert len(problems) == 1 and "edu_007" in problems[0] and "anchor switch" in problems[0]
    _ideas(tmp_path, "B")
    assert len(anchor_gate.check(tmp_path)) == 2


def test_anchor_switch必须用户授权(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _ideas(run, "A"); _plan(run, "edu_001")
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
    r = runner.invoke(app, ["anchor", "switch", "--to", "edu_007", "--why", "数据回答不了", "--run", str(run)])
    assert r.exit_code == 2 and "用户授权" in r.output
    r = runner.invoke(app, ["anchor", "switch", "--to", "edu_007", "--why", "数据回答不了",
                            "--authorized-by", "user", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert state_mod.load(run).anchor["idea_id"] == "edu_007"
    rows = _trace_rows(run)
    assert rows[-1]["step"] == "topic_switch" and rows[-1]["args"]["authorized_by"] == "user"
    _plan(run, "edu_007")
    assert anchor_gate.check(run) == []


def test_paper_export被锚点门拦下(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _ideas(run, "A"); _plan(run, "edu_001")
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
    _plan(run, "edu_007"); _ideas(run, "B")          # idea 和方向一起漂
    (run / "paper_rendered.md").write_text("# x\n", encoding="utf-8")
    r = runner.invoke(app, ["paper", "export", "--run", str(run)])
    assert r.exit_code == 7, r.output
    # 两条一次报全，别改一条被拦一次
    assert "edu_007" in r.output and "ideas.json 的方向" in r.output, r.output


def test_export的遗留条数跟decide一个口径(tmp_path):
    """复审缩水时「最近一轮」的设计级意见凭空归零——导出提醒报「遗留 0 条」，
    模型据此认为没什么可写。**遗留几条只有一个出处：decide()。**"""
    run = tmp_path / "r"; run.mkdir()
    _ideas(run, "A"); _plan(run, "edu_001")
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
    for n, items in zip("abc", (["[设计] x1", "[设计] x2"], ["[设计] y"], ["[设计] z"])):
        _review(run, f"review_paper_rendered_{n}.json", "WARN", items, 1)
    _review(run, "review_paper_rendered_a_r2.json", "FAIL", ["[文字] 措辞"], 2)
    (run / "paper_rendered.md").write_text("# T\n\nx\n", encoding="utf-8")
    r = runner.invoke(app, ["paper", "export", "--run", str(run)])
    assert "遗留设计级意见 4 条" in r.output, (r.exit_code, r.output)
    assert "遗留设计级意见 0 条" not in r.output, r.output


def test_loop_status标出偏离(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _ideas(run, "A"); _plan(run, "edu_001")
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
    _plan(run, "edu_007")
    r = runner.invoke(app, ["loop", "status", "--run", str(run)])
    assert "偏离冻结锚点" in r.output, r.output
    assert r.exit_code == 0, r.output          # 只是提醒：退出码归循环状态管


# ─────────────────────────── 锚点门堵的三条绕路


def test_没冻结时anchor_switch拒绝(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _ideas(run, "A"); _plan(run, "edu_001")
    r = runner.invoke(app, ["anchor", "switch", "--to", "edu_007", "--why", "x",
                            "--authorized-by", "user", "--run", str(run)])
    assert r.exit_code == 2 and "没有题可换" in r.output
    assert not (run / state_mod.FILE).exists()


def test_删掉plan或ideas也被拦(tmp_path):
    _ideas(tmp_path, "A"); _plan(tmp_path, "edu_001")
    state_mod.freeze_anchor(tmp_path, **state_mod.current_anchor(tmp_path))
    (tmp_path / "plan.json").unlink()
    assert any("找不到 plan.json" in p for p in anchor_gate.check(tmp_path))
    _plan(tmp_path, "edu_001"); (tmp_path / "ideas.json").unlink()
    assert any("找不到 ideas.json" in p for p in anchor_gate.check(tmp_path))


def test_状态文件损坏后门不静默放行(tmp_path):
    _ideas(tmp_path, "A"); _plan(tmp_path, "edu_001")
    state_mod.freeze_anchor(tmp_path, **state_mod.current_anchor(tmp_path))
    (tmp_path / state_mod.FILE).write_text("{坏了", encoding="utf-8")
    problems = anchor_gate.check(tmp_path)
    assert problems and "曾损坏" in problems[0]


def test_换题留痕记录从哪换来(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _ideas(run, "A"); _plan(run, "edu_001")
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
    _ideas(run, "B")
    r = runner.invoke(app, ["anchor", "switch", "--to", "edu_007", "--why", "x",
                            "--authorized-by", "user", "--run", str(run)])
    assert r.exit_code == 0, r.output
    row = _trace_rows(run)[-1]
    assert row["args"]["from"] == "edu_001" and row["args"]["to"] == "edu_007"
    assert row["args"]["from_direction"] == "A" and row["args"]["to_direction"] == "B"
    assert "方向也变了" in r.output
    assert state_mod.load(run).anchor["plan_sha"] == ""


def test_方向只差空白不拦(tmp_path):
    _ideas(tmp_path, "教育 与 生育"); _plan(tmp_path, "edu_001")
    state_mod.freeze_anchor(tmp_path, **state_mod.current_anchor(tmp_path))
    _ideas(tmp_path, "教育  与  生育 ")
    assert anchor_gate.check(tmp_path) == []


def test_paper_check也报锚点门(tmp_path):
    """两个调用点都要有测试——导出那边已有，这里补校验。"""
    run = tmp_path / "r"; run.mkdir()
    _ideas(run, "A"); _plan(run, "edu_001")
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
    _plan(run, "edu_007")
    (run / "paper.md").write_text("# T\n\n## Abstract\n\nx\n", encoding="utf-8")
    (run / "results.json").write_text("{}", encoding="utf-8")
    r = runner.invoke(app, ["paper", "check", "--run", str(run)])
    assert r.exit_code == 1 and "锚点检测" in r.output, r.output


def test_状态损坏后可以重建锚点(tmp_path):
    """损坏 → 门报警 → 用户授权重建。**这条路不通的话，门就是死路。**"""
    run = tmp_path / "r"; run.mkdir()
    _ideas(run, "A"); _plan(run, "edu_001")
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
    (run / state_mod.FILE).write_text("{坏了", encoding="utf-8")
    assert anchor_gate.check(run)                       # 先报损坏
    r = runner.invoke(app, ["anchor", "switch", "--to", "edu_001", "--why", "状态损坏重建",
                            "--authorized-by", "user", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert "重建" in r.output
    assert _trace_rows(run)[-1]["args"]["rebuild_after_corrupt"] is True
    assert not (run / state_mod.CORRUPT).exists()       # 警报摘掉了
    archived = list(run.glob("run_state.corrupt.*.json"))
    assert len(archived) == 1, archived                 # 证据改名留着
    assert anchor_gate.check(run) == []                 # 门重新安静


def test_损坏后重跑run重新冻结也不放行(tmp_path):
    """事故形态：状态文件坏了，oss run 一重跑就把当前题目冻上——门必须还在响。"""
    _ideas(tmp_path, "A"); _plan(tmp_path, "edu_001")
    state_mod.freeze_anchor(tmp_path, **state_mod.current_anchor(tmp_path))
    (tmp_path / state_mod.FILE).write_text("{坏了", encoding="utf-8")
    _plan(tmp_path, "edu_009"); _ideas(tmp_path, "改过的方向")
    assert anchor_gate.check(tmp_path)                          # 触发隔离
    state_mod.freeze_anchor(tmp_path, **state_mod.current_anchor(tmp_path))   # 模拟 oss run 重新冻结
    assert any("曾损坏" in p for p in anchor_gate.check(tmp_path))


# ─────────────────────────── 探索门

from oss.core.contracts import AnalysisPlan
from oss.gates import explore as explore_gate

FIXTURES = Path(__file__).parent / "fixtures"


def _did_plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", estimator="did", outcome="y", unit="unit", time="year",
                treat_time="treat_time", cluster_level="unit")
    return AnalysisPlan(**{**base, **kw})


def _route(rundir: Path, *ids: str) -> None:
    (rundir / "route.json").write_text(json.dumps({"plan_sha": "s", "items": [
        {"id": i, "priority": "should", "trigger": "t", "kind": "disclose"} for i in ids]}), encoding="utf-8")


def test_探索必须挂在处方或审稿轮次上(tmp_path):
    _route(tmp_path, "pretrend_fail")
    base, probe = _did_plan(), _did_plan(sample_filter="year >= 2002")
    assert explore_gate.validate(tmp_path, base, probe, "", "")
    assert any("找不到" in p for p in explore_gate.validate(tmp_path, base, probe, "nope", ""))
    assert explore_gate.validate(tmp_path, base, probe, "pretrend_fail", "") == []
    _review(tmp_path, "review_paper_rendered_a_r2.json", "FAIL", ["[设计] x"], 2)
    assert explore_gate.validate(tmp_path, base, probe, "review-r2", "") == []
    assert explore_gate.validate(tmp_path, base, probe, "review-r3", "")


def test_探索不许改结局处理估计量(tmp_path):
    _route(tmp_path, "pretrend_fail")
    base = _did_plan()
    for change in ({"outcome": "y2"}, {"treat_time": "t2"}, {"estimator": "ols_fe"}):
        problems = explore_gate.validate(tmp_path, base, _did_plan(**change), "pretrend_fail", "")
        assert problems and "换题" in problems[0], change
    ok = explore_gate.validate(tmp_path, base, _did_plan(controls=["x"], sample_filter="year>1"), "pretrend_fail", "")
    assert ok == []


def test_探索改抽样设计与权重跟改聚类层一样处理(tmp_path):
    """抽样设计、权重跟 cluster_level 同属设计层：改了要跟改聚类层一样被门放行或拦下，并留痕。"""
    from oss.core.contracts import SurveyDesign
    _route(tmp_path, "pretrend_fail")
    base = _did_plan()
    like = _did_plan(cluster_level="year")
    want = explore_gate.validate(tmp_path, base, like, "pretrend_fail", "")
    assert explore_gate.diff(base, like) == ["cluster_level"]
    for key, value in (("survey", SurveyDesign(psu="unit", weight_use="model", design_source="x")),
                       ("weights", "x")):
        probe = _did_plan(**{key: value})
        assert explore_gate.validate(tmp_path, base, probe, "pretrend_fail", "") == want, key
        assert explore_gate.diff(base, probe) == [key], key


def test_同一处方探索超过上限要理由(tmp_path):
    _route(tmp_path, "pretrend_fail")
    rows = [{"seq": i, "step": "explore", "args": {"for": "pretrend_fail"}} for i in range(1, 6)]
    (tmp_path / "trace.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    base, probe = _did_plan(), _did_plan(sample_filter="year >= 2002")
    assert any("--why" in p for p in explore_gate.validate(tmp_path, base, probe, "pretrend_fail", ""))
    assert explore_gate.validate(tmp_path, base, probe, "pretrend_fail", "审稿人要求") == []


def test_cli探索结果不进results并留痕(tmp_path):
    run = tmp_path / "r"
    r = runner.invoke(app, ["profile", str(FIXTURES / "did_clean.csv"), "--run", str(run)])
    assert r.exit_code == 0, r.output
    (run / "plan.json").write_text(_did_plan().model_dump_json(), encoding="utf-8")
    _route(run, "pretrend_fail")
    probe = run / "probe.json"
    probe.write_text(_did_plan(sample_filter="year >= 2002").model_dump_json(), encoding="utf-8")
    r = runner.invoke(app, ["run", "--explore", "--for", "pretrend_fail", "--plan", str(probe), "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert "不进论文" in r.output
    assert (run / "explore" / "pretrend_fail_1.results.json").exists()
    assert not (run / "results.json").exists(), "探索结果绝不能冒充正式结果"
    assert not (run / state_mod.FILE).exists(), "探索不冻结锚点、不推进阶段"
    rows = _trace_rows(run)
    assert rows[-1]["step"] == "explore" and rows[-1]["args"]["for"] == "pretrend_fail"
    bad = run / "bad.json"
    bad.write_text(_did_plan(outcome="treat_time").model_dump_json(), encoding="utf-8")
    r = runner.invoke(app, ["run", "--explore", "--for", "pretrend_fail", "--plan", str(bad), "--run", str(run)])
    assert r.exit_code == 1 and "换题" in r.output
    assert _trace_rows(run)[-1]["outcome"] == "refused"


# ─────────────────────────── 探索门：堵洗白的路

import re
import shutil

from oss.core.contracts import AnalysisResults, Estimate, SpecSummary

#: 能过 paper check 的最小骨架，正文里插一句话
_PAPER = ("# T\n\n## Abstract\nEffect {{result:main.coef}}.\n\n## Introduction\nx\n\n"
          "## Data and Methods\nx\n\n## Results\n{0}\n\n## Discussion\nx\n")


def _explorable_run(tmp_path: Path) -> Path:
    """一个能跑探索的运行目录：画像 + plan.json + route.json + probe.json。"""
    run = tmp_path / "r"
    r = runner.invoke(app, ["profile", str(FIXTURES / "did_clean.csv"), "--run", str(run)])
    assert r.exit_code == 0, r.output
    (run / "plan.json").write_text(_did_plan().model_dump_json(), encoding="utf-8")
    _route(run, "pretrend_fail")
    (run / "probe.json").write_text(
        _did_plan(sample_filter="year >= 2002").model_dump_json(), encoding="utf-8")
    return run


def _real_results(run: Path) -> None:
    """一份最小的正式结果，用来单独考 paper check。"""
    AnalysisResults(plan_id="p", n=100, estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe")).save(run / "results.json")


def test_plan不带explore被拒(tmp_path):
    """忘了 --explore 不能变成「拿 plan.json 跑出一份正式结果」——那正是模型以为它在探索的时候。"""
    run = _explorable_run(tmp_path)
    r = runner.invoke(app, ["run", "--plan", str(run / "probe.json"), "--run", str(run)])
    assert r.exit_code == 1, r.output
    assert "只在 --explore 下有效" in r.output
    assert not (run / "results.json").exists()
    r = runner.invoke(app, ["run", "--for", "pretrend_fail", "--run", str(run)])
    assert r.exit_code == 1 and "只在 --explore 下有效" in r.output
    assert not (run / "results.json").exists()


def test_explore不带plan被拒(tmp_path):
    run = _explorable_run(tmp_path)
    r = runner.invoke(app, ["run", "--explore", "--for", "pretrend_fail", "--run", str(run)])
    assert r.exit_code == 1 and "要配 --plan" in r.output
    assert not (run / "results.json").exists()


def test_第二次探索编号2且拒绝不占额度(tmp_path):
    run = _explorable_run(tmp_path)
    probe, bad = str(run / "probe.json"), run / "bad.json"
    bad.write_text(_did_plan(outcome="treat_time").model_dump_json(), encoding="utf-8")
    def _run_probe(f: str):
        return runner.invoke(app, ["run", "--explore", "--for", "pretrend_fail",
                                   "--plan", f, "--run", str(run)])

    assert _run_probe(probe).exit_code == 0
    assert _run_probe(str(bad)).exit_code == 1
    assert _run_probe(probe).exit_code == 0
    names = sorted(p.name for p in (run / "explore").iterdir())
    assert names == ["pretrend_fail_1.results.json", "pretrend_fail_2.results.json"], names
    # 被拒的那几次一个数字都没产出，不该吃掉额度
    for _ in range(5):
        assert _run_probe(str(bad)).exit_code == 1
    r = _run_probe(probe)
    assert r.exit_code == 0 and "--why" not in r.output, r.output
    assert (run / "explore" / "pretrend_fail_3.results.json").exists()


def test_探索换数据被拒(tmp_path):
    """换规格是探索，换数据是另一件事——两次的数字不可比，留痕却只会说「探索了一次」。"""
    run = _explorable_run(tmp_path)
    other = tmp_path / "别的数据.csv"
    shutil.copy(FIXTURES / "did_clean.csv", other)
    r = runner.invoke(app, ["run", "--explore", "--for", "pretrend_fail",
                            "--plan", str(run / "probe.json"), "--data", str(other), "--run", str(run)])
    assert r.exit_code == 1 and "不能换数据" in r.output, r.output
    assert not (run / "explore").exists()


def test_探索数字不可被引用(tmp_path):
    """探索出来的数字**不是出处**：既不能靠 explore/ 文件洗白，也不能靠写进 --why 洗白。"""
    run = _explorable_run(tmp_path)
    r = runner.invoke(app, ["run", "--explore", "--for", "pretrend_fail",
                            "--plan", str(run / "probe.json"), "--why", "阈值 0.98765", "--run", str(run)])
    assert r.exit_code == 0, r.output
    _real_results(run)
    seen = "".join((run / n).read_text(encoding="utf-8")
                   for n in ("results.json", "profile.json") if (run / n).exists())
    cands = [d for d in re.findall(r"\d+\.\d+",
                                   (run / "explore" / "pretrend_fail_1.results.json").read_text(encoding="utf-8"))
             if d not in seen]
    assert cands, "探索结果里应有别处没有的数字"
    for num in (cands[0], "0.98765"):
        (run / "paper.md").write_text(_PAPER.format(f"The effect is {num}."), encoding="utf-8")
        r = runner.invoke(app, ["paper", "check", "--run", str(run)])
        assert r.exit_code != 0 and "裸小数" in r.output and num in r.output, (num, r.output)


def test_加审理由里的数字也不算出处(tmp_path):
    """`--why` 是模型自己写的自由文本，落进 args/note 也不能变成可引用的出处。"""
    run = tmp_path / "r"; run.mkdir()
    runner.invoke(app, ["review", "--round", "3", "--extra-round", "--why", "阈值 0.67891", "--run", str(run)])
    assert "0.67891" in (run / "trace.jsonl").read_text(encoding="utf-8")
    _real_results(run)
    (run / "paper.md").write_text(_PAPER.format("The effect is 0.67891."), encoding="utf-8")
    r = runner.invoke(app, ["paper", "check", "--run", str(run)])
    assert r.exit_code != 0 and "裸小数" in r.output and "0.67891" in r.output, r.output


def test_探索不动已有正式结果(tmp_path):
    run = _explorable_run(tmp_path)
    _real_results(run)
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
    before = [(run / n).read_bytes() for n in ("results.json", state_mod.FILE)]
    r = runner.invoke(app, ["run", "--explore", "--for", "pretrend_fail",
                            "--plan", str(run / "probe.json"), "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert [(run / n).read_bytes() for n in ("results.json", state_mod.FILE)] == before


def test_route不存在时提示先跑route(tmp_path):
    base, probe = _did_plan(), _did_plan(sample_filter="year >= 2002")
    problems = explore_gate.validate(tmp_path, base, probe, "pretrend_fail", "")
    assert problems and "先跑 oss route" in problems[0], problems


def test_冒充的results被paper_check拒绝(tmp_path):
    _real_results(tmp_path)
    res = AnalysisResults.load(tmp_path / "results.json")
    res.plan_id = "explore:pretrend_fail#1"
    res.save(tmp_path / "results.json")
    (tmp_path / "paper.md").write_text(_PAPER.format("x"), encoding="utf-8")
    r = runner.invoke(app, ["paper", "check", "--run", str(tmp_path)])
    assert r.exit_code != 0 and "探索结果冒充" in r.output, r.output


def test_冒充的results被paper_render拒绝(tmp_path):
    """render 才是把数字写进稿子的那一步——戳要在 check() 里查，两条路才都拦得住。"""
    _real_results(tmp_path)
    res = AnalysisResults.load(tmp_path / "results.json")
    res.plan_id = "explore:pretrend_fail#1"
    res.save(tmp_path / "results.json")
    (tmp_path / "paper.md").write_text(_PAPER.format("x"), encoding="utf-8")
    r = runner.invoke(app, ["paper", "render", "--run", str(tmp_path)])
    assert r.exit_code != 0 and "探索结果冒充" in r.output, r.output
    assert not (tmp_path / "paper_rendered.md").exists(), "拦住了就不该落盘"


def test_冒充的results不给画图(tmp_path):
    """图也是数字的出口：拷一份探索结果过去就能画出一张「正式」的系数图。"""
    run = _explorable_run(tmp_path)
    _real_results(run)
    res = AnalysisResults.load(run / "results.json")
    res.plan_id = "explore:pretrend_fail#1"
    res.save(run / "results.json")
    r = runner.invoke(app, ["figure", "coef", "--run", str(run)])
    assert r.exit_code == 1 and "探索结果冒充" in r.output, r.output


from oss.core.contracts import LiteratureCorpus

#: 能过 paper check 的完整骨架：**带主结果表**，否则 export 在 {{table:main}} 那道门就先被拦下
_PAPER_OK = ("# T\n\n## Abstract\nEffect {{result:main.coef}} (n = {{result:main.n}}).\n\n"
             "## Introduction\nx\n\n## Data and Methods\nx\n\n"
             "## Results\n*Table 1. Main estimates.*\n{{table:main}}\n\n## Discussion\nx\n")


def test_冒充的results被paper_export拒绝(tmp_path):
    """交付物那一步也要查戳：稿子渲染完、审稿也过了，再把 results.json 换成探索的那份——
    `.docx` 是最终发出去的一份，它比正文更不该带探索的数字。"""
    run = tmp_path / "r"; run.mkdir()
    _plan(run, "edu_001"); _ideas(run, "A")
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
    _real_results(run)
    (run / "paper.md").write_text(_PAPER_OK, encoding="utf-8")
    # 语料在，export 才走 citeproc 那条路——文本版那条路根本不读 results.json
    LiteratureCorpus(corpus_id="c").save(run / "literature.json")
    for cmd in (["paper", "check"], ["paper", "render"]):
        r = runner.invoke(app, cmd + ["--run", str(run)])
        assert r.exit_code == 0, (cmd, r.output)
    assert (run / "paper_rendered.md").exists()
    _review(run, "review_paper_rendered_a.json", "PASS", [], 1)      # 审过一轮，轮次门放行

    res = AnalysisResults.load(run / "results.json")
    res.plan_id = "explore:pt#1"                                     # 探索结果拷过来冒充
    res.save(run / "results.json")
    r = runner.invoke(app, ["paper", "export", "--run", str(run)])
    assert r.exit_code == 1, r.output
    assert "探索结果冒充" in r.output, r.output
    assert not (run / "paper.docx").exists(), "拦住了就不该有交付物"

    # 没有语料时 export 走纯文本那条渲染路径——**戳照查**。
    # 门要是长在 citeproc 分支里，删掉 literature.json 就能把冒充的结果导出成 .docx
    (run / "literature.json").unlink()
    r = runner.invoke(app, ["paper", "export", "--run", str(run)])
    assert r.exit_code == 1, r.output
    assert "探索结果冒充" in r.output, r.output
    assert not (run / "paper.docx").exists(), "拦住了就不该有交付物"
