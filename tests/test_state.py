"""run_state.json：一次运行的状态指针。状态在盘上，不在对话里——压缩、中断、换模型都从这里续。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from oss.cli.main import app
from oss.core import state as state_mod
from oss.core.contracts import AnalysisResults, Estimate, SpecSummary, TraceEvent

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"

PAPER = ("# T\n\n## Abstract\nEffect {{result:main.coef}} (n = {{result:main.n}}).\n\n"
         "## Introduction\nx\n\n## Data and Methods\nx\n\n"
         "## Results\n*Table 1. Main estimates.*\n{{table:main}}\n\n## Discussion\nx\n")


def _results(run: Path) -> None:
    AnalysisResults(plan_id="p", n=100, estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe")).save(run / "results.json")


def test_不存在时给空状态且run_id是目录名(tmp_path):
    st = state_mod.load(tmp_path)
    assert st.run_id == tmp_path.name and st.stage == "" and st.anchor == {}


def test_update合并字典字段并落盘(tmp_path):
    state_mod.update(tmp_path, stage="6-run", review={"round": 1})
    state_mod.update(tmp_path, review={"last_decision": "continue"})
    d = json.loads((tmp_path / state_mod.FILE).read_text(encoding="utf-8"))
    assert d["stage"] == "6-run"
    assert d["review"] == {"round": 1, "last_decision": "continue"}
    assert d["updated_at"]


def test_锚点只冻结一次(tmp_path):
    a = state_mod.freeze_anchor(tmp_path, direction="教育与生育", idea_id="edu_001", plan_sha="abc")
    b = state_mod.freeze_anchor(tmp_path, direction="换了", idea_id="edu_007", plan_sha="def")
    assert a.anchor["idea_id"] == "edu_001" and b.anchor["idea_id"] == "edu_001"
    assert a.anchor["frozen_at"]


def test_reset_anchor才能换(tmp_path):
    state_mod.freeze_anchor(tmp_path, direction="教育与生育", idea_id="edu_001", plan_sha="abc")
    st = state_mod.reset_anchor(tmp_path, direction="教育与生育", idea_id="edu_007", plan_sha="def")
    assert st.anchor["idea_id"] == "edu_007" and st.anchor["frozen_at"]


def test_paper_check缺稿时不推进阶段(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    r = runner.invoke(app, ["paper", "check", "--run", str(run)])
    assert r.exit_code == 1
    assert not (run / state_mod.FILE).exists()


def test_review被轮次门拒绝时不写状态(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    r = runner.invoke(app, ["review", "--paper", "--round", "3", "--run", str(run)])
    assert r.exit_code == 4
    assert state_mod.load(run).stage == ""


# ────────────────────────────────── 坏文件不该把状态弄丢，也不该把命令搞挂


def test_文件被截断时当没有状态(tmp_path):
    state_mod.update(tmp_path, stage="6-run")
    p = tmp_path / state_mod.FILE
    p.write_text(p.read_text(encoding="utf-8")[:20], encoding="utf-8")
    assert state_mod.load(tmp_path).stage == ""


def test_字段类型不对时掰回缺省(tmp_path):
    (tmp_path / state_mod.FILE).write_text(
        json.dumps({"anchor": None, "review": [], "stage": 7, "stage_status": None}), encoding="utf-8")
    st = state_mod.load(tmp_path)
    assert st.anchor == {} and st.review == {} and st.stage == "" and st.stage_status == "in_progress"
    # 掰回来之后照常能冻锚点——坏文件不该让锚点永久冻不上
    st = state_mod.freeze_anchor(tmp_path, direction="教育与生育", idea_id="edu_001", plan_sha="abc")
    assert st.anchor["idea_id"] == "edu_001"


def test_盘上是列表时当没有状态(tmp_path):
    (tmp_path / state_mod.FILE).write_text("[1, 2, 3]", encoding="utf-8")
    st = state_mod.load(tmp_path)
    assert st.run_id == tmp_path.name and st.stage == "" and st.anchor == {}


def test_坏文件挪到一边留证据(tmp_path):
    """静默吞掉坏文件 = 锚点静默解冻，下一次 run 会把当前题目重新冻上。证据得留住。"""
    (tmp_path / state_mod.FILE).write_text("{不是 json", encoding="utf-8")
    assert state_mod.load(tmp_path).stage == ""
    assert (tmp_path / state_mod.CORRUPT).exists()
    assert not (tmp_path / state_mod.FILE).exists()


def test_修好之前再坏一次不覆盖第一份证据(tmp_path):
    """第二次损坏把第一次的证据盖掉，等于**最早那份坏文件永远查不到**——
    而查的正是「锚点是被谁写坏的」。平名留给警报，后来的另存时间戳。"""
    (tmp_path / state_mod.FILE).write_text("{第一次坏", encoding="utf-8")
    state_mod.load(tmp_path)
    (tmp_path / state_mod.FILE).write_text("{第二次坏", encoding="utf-8")
    state_mod.load(tmp_path)
    assert (tmp_path / state_mod.CORRUPT).read_text(encoding="utf-8") == "{第一次坏"
    stamped = list(tmp_path.glob("run_state.corrupt.*.json"))
    assert len(stamped) == 1, stamped
    assert stamped[0].read_text(encoding="utf-8") == "{第二次坏"


def test_字典字段塞标量要报错(tmp_path):
    import pytest

    with pytest.raises(TypeError):
        state_mod.update(tmp_path, review="x")


def test_set_stage两个字段一起写(tmp_path):
    st = state_mod.set_stage(tmp_path, "done", "done")
    assert st.stage == "done" and st.stage_status == "done"
    assert state_mod.set_stage(tmp_path, "7-paper").stage_status == "in_progress"


def test_空锚点不冻结(tmp_path):
    st = state_mod.freeze_anchor(tmp_path, direction="", idea_id="", plan_sha="")
    assert not st.anchor.get("frozen_at")
    # 等下一次真有题目时再冻
    st = state_mod.freeze_anchor(tmp_path, direction="教育与生育", idea_id="edu_001", plan_sha="abc")
    assert st.anchor["frozen_at"]


def test_时间戳跟留痕同一种写法(tmp_path):
    """两个文件的时间戳要能直接比大小，不该一个带 Z 一个带 +00:00。"""
    ts = json.loads(TraceEvent(seq=1, step="x").to_line())["ts"]
    # pydantic 在 microsecond == 0 时把小数部分整个省掉——留痕这边容忍有无小数，
    # 状态那边照旧严格。要的是「两边都是 …Z、能直接比大小」，不是位数一样。
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?Z", ts), ts
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z",
                        state_mod.now_stamp()), state_mod.now_stamp()


# ────────────────────────────────── 命令走到底才落状态


def test_paper_check通过时推进到7(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _results(run)
    (run / "paper.md").write_text(PAPER, encoding="utf-8")
    r = runner.invoke(app, ["paper", "check", "--run", str(run)])
    assert r.exit_code == 0, r.output
    st = state_mod.load(run)
    assert st.stage == "7-paper" and st.stage_status == "done"


def test_paper_check没过时阶段仍是7但未done(tmp_path):
    """稿子在就进第 7 环；过没过校验看 stage_status——两件事别挤在一个字段里。"""
    run = tmp_path / "r"; run.mkdir()
    _results(run)
    (run / "paper.md").write_text(PAPER + "\nThe coefficient is 0.032.\n", encoding="utf-8")
    r = runner.invoke(app, ["paper", "check", "--run", str(run)])
    assert r.exit_code == 1, r.output
    st = state_mod.load(run)
    assert st.stage == "7-paper" and st.stage_status == "in_progress"


def test_paper_check缺结果时说去跑run(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    (run / "paper.md").write_text(PAPER, encoding="utf-8")
    r = runner.invoke(app, ["paper", "check", "--run", str(run)])
    assert r.exit_code == 1
    assert "oss run" in r.output
    assert not (run / state_mod.FILE).exists()


def _profiled(run: Path) -> None:
    run.mkdir(parents=True, exist_ok=True)
    r = runner.invoke(app, ["profile", str(FIXTURES / "did_clean.csv"), "--run", str(run)])
    assert r.exit_code == 0, r.output


def _plan(run: Path, idea_id: str = "edu_001", outcome: str = "y") -> None:
    (run / "plan.json").write_text(json.dumps(
        {"plan_id": "p", "idea_id": idea_id, "estimator": "did", "outcome": outcome,
         "unit": "unit", "time": "year", "treat_time": "treat_time", "cluster_level": "unit"}),
        encoding="utf-8")
    (run / "ideas.json").write_text(json.dumps({"direction": "教育与生育", "ideas": []},
                                               ensure_ascii=False), encoding="utf-8")


def test_run冻结锚点并推进到6(tmp_path):
    run = tmp_path / "r"
    _profiled(run)
    _plan(run)
    r = runner.invoke(app, ["run", "--run", str(run)])
    assert r.exit_code == 0, r.output
    st = state_mod.load(run)
    assert st.stage == "6-run" and st.anchor["idea_id"] == "edu_001"
    assert st.anchor["direction"] == "教育与生育" and st.anchor["frozen_at"]

    # 换了计划里的 idea_id 再跑一次，锚点纹丝不动——**换题只能走 oss anchor switch**
    _plan(run, idea_id="edu_999")
    r = runner.invoke(app, ["run", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert state_mod.load(run).anchor["idea_id"] == "edu_001"


def test_run被拒绝时不冻结锚点(tmp_path):
    run = tmp_path / "r"
    _profiled(run)
    _plan(run, outcome="nope")
    r = runner.invoke(app, ["run", "--run", str(run)])
    assert r.exit_code == 1
    assert not (run / state_mod.FILE).exists()


def test_换题后首跑补回计划指纹(tmp_path):
    """switch 时新计划还没写，plan_sha 只能留空——跑出新结果时得补回来，否则一辈子是空的。"""
    run = tmp_path / "r"
    _profiled(run)
    _plan(run)                                          # edu_001
    assert runner.invoke(app, ["run", "--run", str(run)]).exit_code == 0
    r = runner.invoke(app, ["anchor", "switch", "--to", "edu_002", "--why", "数据回答不了",
                            "--authorized-by", "user", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert state_mod.load(run).anchor["plan_sha"] == ""

    _plan(run, idea_id="edu_002")
    r = runner.invoke(app, ["run", "--run", str(run)])
    assert r.exit_code == 0, r.output
    st = state_mod.load(run)
    assert st.anchor["idea_id"] == "edu_002" and st.anchor["plan_sha"]
