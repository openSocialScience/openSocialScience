"""行为指标：这一次是**怎么跑的**。留痕是手写的，期望值也是手数的——对不上就是算错了。"""

from __future__ import annotations

import json
from pathlib import Path

from oss.core import behavior as B
from oss.core import loop as loop_mod
from oss.core.skillcheck import FAIL, NA, PASS, RunView


def _trace(rundir: Path, rows: list[dict]) -> None:
    for i, r in enumerate(rows, start=1):
        r.setdefault("seq", i)
        r.setdefault("ts", f"2026-08-29T01:{i:02d}:00Z")   # 默认时间戳夹在首末两行之间
        r.setdefault("step", "x")
        r.setdefault("command", "")
        r.setdefault("outcome", "ok")
    (rundir / "trace.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _rows(rundir: Path) -> list[dict]:
    return [json.loads(x) for x in (rundir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]


def _json(rundir: Path, name: str, payload) -> None:
    (rundir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _good(rundir: Path) -> Path:
    """一次规矩的运行：2 轮审稿、0 换题、3 次探索、1 次 paper check 被拒、4 次 oss run、89 分钟。"""
    rows = [
        {"ts": "2026-08-29T01:00:00Z", "step": "profile", "command": "oss profile d.csv"},
        {"step": "results", "command": "oss run"},
        {"step": "route", "command": "oss route"},
        {"step": "explore", "command": "oss run --explore --for pretrend_fail", "args": {"for": "pretrend_fail"}},
        {"step": "explore", "command": "oss run --explore --for pretrend_fail", "args": {"for": "pretrend_fail"}},
        {"step": "explore", "command": "oss run --explore --for review-r1", "args": {"for": "review-r1"}},
        {"step": "results", "command": "oss run"},
        {"step": "results", "command": "oss run"},
        {"step": "results", "command": "oss run"},
        {"step": "paper", "command": "oss paper check", "outcome": "refused"},
        {"step": "paper", "command": "oss paper check"},
        {"ts": "2026-08-29T02:29:00Z", "step": "paper", "command": "oss paper export"},
    ]
    _trace(rundir, rows)
    _json(rundir, "loop_state.json", {"round": 2, "max_rounds": 2, "decision": {"decision": "stop"}})
    _json(rundir, "run_state.json", {"stage": "done", "stage_status": "done", "anchor": {"idea_id": "edu_001"}})
    _json(rundir, "route.json", {"plan_sha": "s", "items": [
        {"id": "pretrend_fail", "priority": "must", "trigger": "t", "kind": "disclose"},
        {"id": "imbalance", "priority": "should", "trigger": "t", "kind": "disclose"},
        {"id": "attrition", "priority": "should", "trigger": "t", "kind": "add_robustness"}]})
    return rundir


def _switch(seq: int, ts: str, **args) -> dict:
    return {"seq": seq, "ts": ts, "step": "topic_switch", "command": "oss anchor switch",
            "outcome": args.pop("outcome", "ok"), "args": args}


def test_七个过程指标一次算齐(tmp_path):
    m = B.metrics(RunView(_good(tmp_path)))
    assert [x.value for x in (m["review_rounds"], m["topic_switch"])] == [2.0, 0.0]
    assert m["explore"].value == 3.0 and m["explore"].limit == 15.0      # 3 条处方 × 5
    assert m["paper_check_refused"].value == 1.0
    assert m["runs"].value == 4.0, "explore 的那三次不许算进 oss run"
    assert m["minutes"].value == 89.0 and m["minutes"].limit == 120.0
    assert m["user_questions"].value is None
    # 自带想法那两条不在这条测试的范围里：这个夹具的锚点没有 source，它们判 n/a
    # （正反两面在 tests/test_byo_idea_behavior.py）
    later = ("user_questions", "idea_kept", "jury_recorded")
    assert all(x.status == PASS for x in m.values() if x.key not in later)
    assert m["user_questions"].status == NA
    assert m["idea_kept"].status == NA and m["jury_recorded"].status == NA


def test_空目录全部判不适用而不是通过(tmp_path):
    m = B.metrics(RunView(tmp_path))
    assert all(x.status == NA for x in m.values()), [(k, v.status) for k, v in m.items()]


def test_换题按原始次数算_授权的也算(tmp_path):
    """换题一次就是一次。**授权与否写在备注里，不从头上的数字里扣掉**——
    扣掉之后「这次跑动过几次题」就再也读不出来了。"""
    _good(tmp_path)
    rows = _rows(tmp_path)
    rows.append(_switch(99, "2026-08-29T02:00:00Z", to="edu_007", authorized_by="user"))
    _trace(tmp_path, rows)
    m = B.metrics(RunView(tmp_path))["topic_switch"]
    assert m.value == 1.0 and m.status == FAIL
    assert "1 次有用户授权" in m.note


def test_没有授权的换题不许被授权的盖住(tmp_path):
    """事故的形状：模型自己换题、被拒——**被拒本身就是要报的那件事**。"""
    _good(tmp_path)
    rows = _rows(tmp_path)
    rows.append(_switch(99, "2026-08-29T02:00:00Z", to="edu_007", authorized_by="user"))
    rows.append(_switch(100, "2026-08-29T02:10:00Z", to="edu_009", outcome="refused"))
    _trace(tmp_path, rows)
    m = B.metrics(RunView(tmp_path))["topic_switch"]
    assert m.value == 2.0 and m.status == FAIL
    assert "1 次有用户授权" in m.note and "1 次被拒或没有授权留痕" in m.note


def test_问用户次数只有宿主给了才有数(tmp_path):
    _good(tmp_path)
    assert B.metrics(RunView(tmp_path))["user_questions"].status == NA
    _json(tmp_path, "run_state.json", {"stage_status": "done", "host_metrics": {"user_questions": 0}})
    assert B.metrics(RunView(tmp_path))["user_questions"].status == PASS
    _json(tmp_path, "run_state.json", {"stage_status": "done", "host_metrics": {"user_questions": 5}})
    assert B.metrics(RunView(tmp_path))["user_questions"].status == FAIL


def test_探索上限按处方数放大_没有处方时按一条算(tmp_path):
    _good(tmp_path)
    (tmp_path / "route.json").unlink()
    m = B.metrics(RunView(tmp_path))["explore"]
    assert m.limit == 5.0 and m.status == PASS


def test_被拒的探索不算次数但要写进备注(tmp_path):
    _good(tmp_path)
    rows = _rows(tmp_path)
    rows.append({"seq": 99, "ts": "2026-08-29T02:00:00Z", "step": "explore",
                 "command": "oss run --explore", "outcome": "refused", "args": {"for": "pretrend_fail"}})
    _trace(tmp_path, rows)
    m = B.metrics(RunView(tmp_path))["explore"]
    assert m.value == 3.0 and "被拒 1 次" in m.note


def test_被拒的正式分析照样算一次(tmp_path):
    """拒绝也是一次尝试，一样烧时间。"""
    _good(tmp_path)
    rows = _rows(tmp_path)
    rows.append({"seq": 99, "ts": "2026-08-29T02:00:00Z", "step": "results",
                 "command": "oss run", "outcome": "refused"})
    _trace(tmp_path, rows)
    m = B.metrics(RunView(tmp_path))["runs"]
    assert m.value == 5.0 and "1 次被拒" in m.note


def test_没跑完时耗时只报数不判定(tmp_path):
    _good(tmp_path)
    _trace(tmp_path, _rows(tmp_path)[:-1])
    _json(tmp_path, "run_state.json", {"stage": "7-paper", "stage_status": "in_progress"})
    m = B.metrics(RunView(tmp_path))["minutes"]
    assert m.value is not None and m.limit is None and m.status == NA


def test_没跑完但已经超过上限判fail(tmp_path):
    """**半截的运行时间短不说明任何事，时间长却说明了一件事**：它已经超了。

    事故那次跑到 266 分钟被按停，盘上就是这样一个没跑完的目录。
    「还没跑完所以不判定」会把它读成一条没有判词的记录——最该报的那次反而没判词。
    """
    _good(tmp_path)
    rows = _rows(tmp_path)[:-1]                       # 去掉 oss paper export：这次没跑完
    rows.append({"seq": 99, "ts": "2026-08-29T04:00:00Z", "step": "x", "command": ""})
    _trace(tmp_path, rows)
    _json(tmp_path, "run_state.json", {"stage": "7-paper", "stage_status": "in_progress"})
    m = B.metrics(RunView(tmp_path))["minutes"]
    assert m.value == 180.0 and m.limit == 120.0 and m.status == FAIL
    assert "还没跑完就已经超过 120 分钟" in m.note


def test_max_rounds写0就是一轮都不许审(tmp_path):
    """`or` 会把 0 也读成「没写」，兜回默认的 2——那是把一次违规读成合格。"""
    _good(tmp_path)
    _json(tmp_path, "loop_state.json", {"round": 2, "max_rounds": 0})
    m = B.metrics(RunView(tmp_path))["review_rounds"]
    assert m.limit == 0.0 and m.status == FAIL, m.note
    # 键不在、或者值根本不是个数时，才回落到默认
    for loop in ({"round": 2}, {"round": 2, "max_rounds": None}, {"round": 2, "max_rounds": "两轮"}):
        _json(tmp_path, "loop_state.json", loop)
        assert B.metrics(RunView(tmp_path))["review_rounds"].limit == float(loop_mod.MAX_ROUNDS), loop


def test_一次oss_run都没有时不许说全部跑通(tmp_path):
    """0 次「全部跑通」是拿一个空集合的真命题冒充一句好消息。"""
    _good(tmp_path)
    _trace(tmp_path, [r for r in _rows(tmp_path) if r.get("command") != "oss run"])
    m = B.metrics(RunView(tmp_path))["runs"]
    assert m.value == 0.0 and "全部跑通" not in m.note
    assert "留痕里没有 oss run" in m.note
    assert "下限" in m.note, "留痕看不见裸 python 跑的回归——这个数是下限，表上得说出来"


def test_探索上限与门的常数一致():
    """**表上那条线跟门上那条线必须是同一个数。**

    改了 `gates/explore.MAX_PER_TARGET` 不改这边，`oss eval --behavior`
    会拿一条已经作废的合格线判分——门放行的次数，表上判成超线（或者反过来）。
    """
    from oss.gates.explore import MAX_PER_TARGET      # noqa: PLC0415 —— 只为这一条断言而 import

    assert B.MAX_EXPLORE_PER_ITEM == MAX_PER_TARGET


def test_轮数超上限判fail并且是must级(tmp_path):
    _good(tmp_path)
    _json(tmp_path, "loop_state.json", {"round": 5, "max_rounds": 2})
    rep, m = B.evaluate(tmp_path)
    assert m["review_rounds"].status == FAIL
    assert [i.id for i in rep.must_failed] == ["behavior-review-rounds"]


def test_每轮都是BLOCKED时不算干净通过(tmp_path):
    """五轮全 BLOCKED 也「没超轮数上限」——但那不是跑得规矩，是根本没人裁决过。"""
    _good(tmp_path)
    _json(tmp_path, "review_paper_identification.json", {"round": 1, "verdict": "BLOCKED", "findings": []})
    _json(tmp_path, "review_paper_alternatives.json", {"round": 2, "verdict": "ERROR", "findings": []})
    rep, m = B.evaluate(tmp_path)
    r = m["review_rounds"]
    assert r.status == NA, "没人裁决过不能读成干净通过"
    assert "没有任何一轮得到真实裁决" in r.note
    assert not rep.must_failed, "判不了不等于违规——不进 must_failed，但也不许报通过"
    # 有一轮拿到真判词就照常判通过
    _json(tmp_path, "review_paper_alternatives.json", {"round": 2, "verdict": "PASS", "findings": []})
    assert B.metrics(RunView(tmp_path))["review_rounds"].status == PASS


def test_锚点冻了却读不到计划时不许报0次漂移(tmp_path):
    _good(tmp_path)
    _json(tmp_path, "run_state.json", {"stage_status": "done", "anchor": {
        "direction": "教育扩张与生育", "idea_id": "edu_001", "frozen_at": "2026-08-29T01:00:00Z"}})
    m = B.metrics(RunView(tmp_path))["topic_switch"]
    assert m.value == 0.0 and m.status == NA, "没有 plan.json 就测不出偏没偏，不是「0 次漂移」"
    assert "plan.json" in m.note and "ideas.json" in m.note


def test_计划悄悄换了题判fail(tmp_path):
    """没走 oss anchor switch，直接改 plan.json 的 idea_id——留痕里一次换题都没有，
    但盘上的题已经不是冻结的那个。"""
    _good(tmp_path)
    _json(tmp_path, "run_state.json", {"stage_status": "done", "anchor": {
        "direction": "教育扩张与生育", "idea_id": "edu_001", "frozen_at": "2026-08-29T01:00:00Z"}})
    _json(tmp_path, "ideas.json", {"direction": "教育扩张与生育"})
    _json(tmp_path, "plan.json", {"idea_id": "edu_001"})
    assert B.metrics(RunView(tmp_path))["topic_switch"].status == PASS
    _json(tmp_path, "plan.json", {"idea_id": "edu_009"})
    m = B.metrics(RunView(tmp_path))["topic_switch"]
    assert m.value == 0.0 and m.status == FAIL
    assert "edu_009" in m.note and "edu_001" in m.note


def test_状态文件坏过要写进换题那条的备注(tmp_path):
    _good(tmp_path)
    (tmp_path / "run_state.corrupt.json").write_text("{坏的", encoding="utf-8")
    m = B.metrics(RunView(tmp_path))["topic_switch"]
    assert m.status == NA and "run_state.corrupt.json" in m.note
    # 授权重建之后：证据改成带时间戳的一份，留痕里那次换题照数，备注说清它是重建
    (tmp_path / "run_state.corrupt.json").rename(tmp_path / "run_state.corrupt.2026-08-29T02-00-00Z.json")
    rows = _rows(tmp_path)
    rows.append(_switch(99, "2026-08-29T02:00:00Z", to="edu_001",
                        authorized_by="user", rebuild_after_corrupt=True))
    _trace(tmp_path, rows)
    m = B.metrics(RunView(tmp_path))["topic_switch"]
    assert m.value == 1.0 and m.status == FAIL and "重建" in m.note


def test_被拒的重建不算一次授权重建(tmp_path):
    """被拒的那次什么也没重建成——记成「授权重建」等于替它把没发生的事说成发生了。"""
    _good(tmp_path)
    (tmp_path / "run_state.corrupt.2026-08-29T02-00-00Z.json").write_text("{坏的", encoding="utf-8")
    rows = _rows(tmp_path)
    rows.append(_switch(99, "2026-08-29T02:00:00Z", to="edu_001",
                        authorized_by="user", rebuild_after_corrupt=True, outcome="refused"))
    _trace(tmp_path, rows)
    m = B.metrics(RunView(tmp_path))["topic_switch"]
    assert m.value == 1.0 and m.status == FAIL
    assert "重建" not in m.note, m.note
    assert "run_state.corrupt.2026-08-29T02-00-00Z.json" in m.note


def test_盘上是一团乱码也不许崩(tmp_path):
    """行为指标是事后核账的，什么烂目录都可能递进来——崩掉等于没有账。"""
    _good(tmp_path)
    (tmp_path / "loop_state.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "run_state.json").write_text("不是 JSON", encoding="utf-8")
    (tmp_path / "route.json").write_text("null", encoding="utf-8")
    (tmp_path / "trace.jsonl").write_text('{"seq": 1}\n["不是对象"]\n坏行\n', encoding="utf-8")
    rep, m = B.evaluate(tmp_path)
    assert len(rep.items) == 9 and len(m) == 9
    assert (tmp_path / "run_state.json").exists(), "读状态不许把坏文件挪走——行为指标是只读的"


def test_报告的形状跟说明书的规则一样(tmp_path):
    rep, m = B.evaluate(_good(tmp_path))
    d = rep.to_dict()
    assert set(d) == {"items", "must_failed", "summary"}
    assert {i["id"] for i in d["items"]} == {
        "behavior-review-rounds", "behavior-topic-switch", "behavior-user-questions",
        "behavior-explore", "behavior-paper-check", "behavior-runs", "behavior-wall-clock",
        "behavior-idea-kept", "behavior-jury-recorded"}
    assert all(i["skill"] == "behavior" for i in d["items"])
    assert {i["id"] for i in d["items"] if i["priority"] == "must"} == {
        "behavior-review-rounds", "behavior-topic-switch", "behavior-idea-kept"}
    assert "审稿轮数" in B.table(m) and "换题" in B.table(m)


def test_宿主给的不是个数就不算数(tmp_path):
    """宿主写了个读不懂的值，不许被 int() 兜成一条「问用户 0 次」的通过。"""
    _good(tmp_path)
    for junk in ("three", None, [], True, {"n": 1}):
        _json(tmp_path, "run_state.json", {"stage_status": "done", "host_metrics": {"user_questions": junk}})
        m = B.metrics(RunView(tmp_path))["user_questions"]
        assert m.value is None and m.status == NA, junk
        assert "不是个数" in m.note, junk
    # 真是个数就照常判
    _json(tmp_path, "run_state.json", {"stage_status": "done", "host_metrics": {"user_questions": 2}})
    assert B.metrics(RunView(tmp_path))["user_questions"].status == FAIL


def test_烂值也盖不住留痕里的ask_user(tmp_path):
    _good(tmp_path)
    rows = _rows(tmp_path)
    rows.append({"seq": 99, "ts": "2026-08-29T02:00:00Z", "step": "ask_user", "command": ""})
    _trace(tmp_path, rows)
    _json(tmp_path, "run_state.json", {"stage_status": "done", "host_metrics": {"user_questions": "three"}})
    m = B.metrics(RunView(tmp_path))["user_questions"]
    assert m.value == 1.0 and m.status == FAIL and "不是个数" in m.note


def test_时间戳混着写也算得出耗时(tmp_path):
    """宿主补的留痕常常不带时区。带 Z 的和不带的减一下会抛 TypeError——核账不许被这个弄崩。"""
    _good(tmp_path)
    rows = _rows(tmp_path)
    rows[5]["ts"] = "2026-08-29T02:00:00"          # 不带时区，按 UTC 读
    rows.append({"seq": 99, "ts": "坏时间", "step": "x", "command": "", "outcome": "ok"})
    _trace(tmp_path, rows)
    m = B.metrics(RunView(tmp_path))["minutes"]
    assert m.value == 89.0 and m.status == PASS


def test_只有一行留痕时耗时判不适用(tmp_path):
    _good(tmp_path)
    _trace(tmp_path, _rows(tmp_path)[:1])
    m = B.metrics(RunView(tmp_path))["minutes"]
    assert m.value is None and m.status == NA, "一个时间戳量不出一段时间"


def test_审稿报告读不出来要写进备注(tmp_path):
    """「三份报告两份读不出来」不许跟「只写了一份报告」在表上长得一样。"""
    _good(tmp_path)
    _json(tmp_path, "review_paper_identification.json", {"round": 1, "verdict": "PASS", "findings": []})
    (tmp_path / "review_paper_alternatives.json").write_text("{半截", encoding="utf-8")
    (tmp_path / "review_desk.json").write_text("{也半截", encoding="utf-8")     # 桌审不算论文轮次
    m = B.metrics(RunView(tmp_path))["review_rounds"]
    assert m.status == PASS, "读不出来只进备注，不改判词"
    assert "1 份报告读不出来" in m.note and "review_paper_alternatives.json" in m.note
    assert "review_desk.json" not in m.note


# ─────────────────────────── CLI：`oss eval --behavior`

from typer.testing import CliRunner        # noqa: E402

from oss.cli.main import app               # noqa: E402

runner = CliRunner()


def test_eval_behavior打一屏表并落盘(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _good(run)
    r = runner.invoke(app, ["eval", "--behavior", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert "审稿轮数" in r.output and "换题" in r.output and "总耗时" in r.output
    assert "89" in r.output and "120" in r.output
    d = json.loads((run / "eval_behavior.json").read_text(encoding="utf-8"))
    assert d["must_failed"] == []
    assert {i["id"] for i in d["items"]} >= {"behavior-review-rounds", "behavior-topic-switch"}
    assert d["metrics"]["runs"]["value"] == 4.0
    assert not (run / "eval_report.json").exists(), "行为指标不许覆盖说明书那份报告"


def test_eval_behavior有must级不过时退出7(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _good(run)
    _json(run, "loop_state.json", {"round": 4, "max_rounds": 2})
    r = runner.invoke(app, ["eval", "--behavior", "--run", str(run)])
    assert r.exit_code == 7, r.output
    assert "behavior-review-rounds" in r.output


def test_eval_behavior换了三次题也退出7(tmp_path):
    """换题是 must 级：表上那一行必须是 ✗，不是备注里的一句话。"""
    run = tmp_path / "r"; run.mkdir()
    _good(run)
    rows = _rows(run)
    rows += [_switch(90 + i, f"2026-08-29T01:5{i}:00Z", to=f"edu_00{i}") for i in range(3)]
    _trace(run, rows)
    r = runner.invoke(app, ["eval", "--behavior", "--run", str(run)])
    assert r.exit_code == 7, r.output
    assert "behavior-topic-switch" in r.output
    assert [x for x in r.output.splitlines() if "换题" in x and "✗" in x], r.output
    d = json.loads((run / "eval_behavior.json").read_text(encoding="utf-8"))
    assert d["must_failed"] == ["behavior-topic-switch"]
    assert d["metrics"]["topic_switch"]["value"] == 3.0


def test_eval_behavior核不了时打点不打勾(tmp_path):
    """状态坏过一次 = 锚点丢了。此时「0 次换题」是没根据的，表上得是 `·`，不许是 ✓。"""
    run = tmp_path / "r"; run.mkdir()
    _good(run)
    (run / "run_state.corrupt.json").write_text("{坏的", encoding="utf-8")
    r = runner.invoke(app, ["eval", "--behavior", "--run", str(run)])
    assert r.exit_code == 0, r.output
    line = [x for x in r.output.splitlines() if "换题" in x and "合格线" not in x]
    assert line and line[0].rstrip().endswith("·"), r.output
    d = json.loads((run / "eval_behavior.json").read_text(encoding="utf-8"))
    assert d["metrics"]["topic_switch"]["status"] == NA
    assert [i for i in d["items"] if i["id"] == "behavior-topic-switch"][0]["status"] == NA


def test_eval_behavior目录不存在时退1且不建目录(tmp_path):
    """打错路径原先会顺手把目录建出来，再对着它报「must 级行为指标全过」——
    **一个凭空造出来的通过**。判分器早就这么拦了，这里跟上。"""
    missing = tmp_path / "没这个目录"
    r = runner.invoke(app, ["eval", "--behavior", "--run", str(missing)])
    assert r.exit_code == 1, r.output
    assert "运行目录不存在" in r.output
    assert not missing.exists(), "目录不存在时不许顺手建目录 / 写报告"


def test_eval_behavior全是na时退1(tmp_path):
    """**一条留痕都没有 = 什么也没核成。**「不过 0 条」于是成了一句没有内容的好消息。

    照判分器（`check_exam.Scorecard.judged`）同一条规矩：没交卷不是合格。
    """
    run = tmp_path / "r"; run.mkdir()
    r = runner.invoke(app, ["eval", "--behavior", "--run", str(run)])
    assert r.exit_code == 1, r.output
    assert "判不了" in r.output and "must 级行为指标全过" not in r.output
    d = json.loads((run / "eval_behavior.json").read_text(encoding="utf-8"))
    assert d["summary"] == {"pass": 0, "fail": 0, "n/a": 9}, d["summary"]


def test_eval_behavior刚建好还没动手的目录照旧退0(tmp_path):
    """**「还没动手」跟「没交卷」不是一回事，这条把两者钉开。**

    小题刚建好的目录（`oss profile` 跑过、模型还没接手）有 trace.jsonl，
    换题 / 探索 / 被拒 / oss run 四个计数器是**真的 0**，四条 ✓ 五条 ·——
    这是合法的起跑状态，不许被上面那条 1 误伤。
    （五条 · = 问用户、总耗时、自带想法那两条，外加还没审稿的审稿轮数。）
    """
    run = tmp_path / "r"; run.mkdir()
    _trace(run, [{"step": "profile", "command": "oss profile panel.csv"}])
    r = runner.invoke(app, ["eval", "--behavior", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert "must 级行为指标全过" in r.output
    d = json.loads((run / "eval_behavior.json").read_text(encoding="utf-8"))
    assert d["summary"] == {"pass": 4, "fail": 0, "n/a": 5}, d["summary"]


def test_eval_behavior可以只列规则(tmp_path):
    r = runner.invoke(app, ["eval", "--behavior", "--list"])
    assert r.exit_code == 0 and "behavior-wall-clock" in r.output
    assert sum(1 for x in r.output.splitlines() if "behavior-" in x) == 9


def test_不加behavior时行为照旧(tmp_path):
    run = tmp_path / "r"; run.mkdir()
    _good(run)
    r = runner.invoke(app, ["eval", "--run", str(run)])
    assert (run / "eval_report.json").exists()
    assert "behavior-" not in r.output
    assert not (run / "eval_behavior.json").exists()
