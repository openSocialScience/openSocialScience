"""自带想法的两条行为指标：想法没被换、陪审团确实审过。

产出物看不出「用户的想法被优化成了另一个题」——`ideas.json` 里照样是一条像样的记录，
`plan.json` 照样跑得通，锚点照样冻着（冻的是模型改完之后那个）。
看得出来的只有三样：锚点的 `source`、锚点的 `direction` 与盘上 `ideas.json` 的差、
以及 `ideas.json` 里还剩几条候选。这个文件把这三样变成两条可核的判词。

**两条都只在 `run_state.anchor.source == "user"` 时判。**模型自己出的题根本没有
「被换掉」这件事，给它判「通过」等于凭空多两条好消息；老运行目录压根没有这个字段，
更不能因为「看不出来」就当成 user。**不许把看不见说成没有**（`core/behavior.py` 的老规矩）。

留痕与状态文件都是手写的，期望值也是手数的——对不上就是算错了。
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from oss.cli.main import app
from oss.core import behavior as B
from oss.core.skillcheck import FAIL, NA, PASS, RunView

runner = CliRunner()

BYO = "idea_001"
IDEA_TEXT = "资本市场错误定价越严重，企业的供应链风险越高；机制是管理层短视与过度自信"


def _json(rundir: Path, name: str, payload) -> None:
    (rundir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _byo_run(rundir: Path, *, source: str = "user", direction: str = IDEA_TEXT,
             anchor_direction: str | None = None, idea_id: str = BYO,
             anchor_idea_id: str | None = None, n_ideas: int = 1,
             jury: bool = True) -> Path:
    """一次规矩的自带想法运行：锚点冻着、盘上只有那一条、陪审团报告在。

    每个参数都是一种「被换掉」的形状，缺省全给对的那一份。
    """
    rundir.mkdir(parents=True, exist_ok=True)
    (rundir / "trace.jsonl").write_text(json.dumps({
        "seq": 1, "ts": "2026-08-29T01:00:00Z", "step": "results",
        "command": "oss run", "outcome": "ok"}, ensure_ascii=False) + "\n", encoding="utf-8")
    _json(rundir, "run_state.json", {"stage": "6-run", "stage_status": "in_progress", "anchor": {
        "direction": anchor_direction if anchor_direction is not None else direction,
        "idea_id": anchor_idea_id if anchor_idea_id is not None else idea_id,
        "plan_sha": "abc", "source": source, "frozen_at": "2026-08-29T01:00:00Z"}})
    _json(rundir, "ideas.json", {"direction": direction, "ideas": [
        {"idea_id": BYO if i == 0 else f"idea_{i + 1:03d}", "title": "T",
         "research_question": "Q", "lens": "user-idea", "source": source}
        for i in range(n_ideas)]})
    _json(rundir, "plan.json", {"plan_id": "p001", "idea_id": idea_id, "estimator": "ols_fe"})
    if jury:
        _json(rundir, "review_ideas.json", {"verdict": "PASS", "findings": ["理论钩子偏弱"]})
    return rundir


def _m(rundir: Path) -> dict:
    return B.metrics(RunView(rundir))


# ─────────────────────────── 正：规矩的一次自带想法运行

def test_两条都通过(tmp_path):
    m = _m(_byo_run(tmp_path))
    assert m["idea_kept"].status == PASS, m["idea_kept"].note
    assert m["jury_recorded"].status == PASS, m["jury_recorded"].note
    assert m["idea_kept"].value == 0.0 and m["idea_kept"].limit == 0.0


def test_方向只差空白排布不算换题(tmp_path):
    """跟锚点门同一条判据（`state.norm_direction`）：空白怎么排不算换题。"""
    run = _byo_run(tmp_path, direction=IDEA_TEXT,
                   anchor_direction="  资本市场错误定价越严重，企业的供应链风险越高；"
                                    "机制是管理层短视与过度自信 ")
    assert _m(run)["idea_kept"].status == PASS


def test_两条的优先级是must与should(tmp_path):
    rep, _ms = B.evaluate(_byo_run(tmp_path))
    by_id = {i.id: i for i in rep.items}
    assert by_id["behavior-idea-kept"].priority == "must"
    assert by_id["behavior-jury-recorded"].priority == "should"
    assert by_id["behavior-idea-kept"].skill == "behavior"


# ─────────────────────────── 反：被换掉的三种形状

def test_方向被改写判fail(tmp_path):
    """事故的形状：`ideas.json` 的 direction 被重写成模型自己那句话。"""
    run = _byo_run(tmp_path, direction="错误定价与企业风险的再检验",
                   anchor_direction=IDEA_TEXT)
    m = _m(run)["idea_kept"]
    assert m.value == 1.0 and m.status == FAIL
    assert "错误定价与企业风险的再检验" in m.note and "供应链风险" in m.note


def test_计划挂到别的idea上判fail(tmp_path):
    run = _byo_run(tmp_path, idea_id="idea_004", anchor_idea_id=BYO)
    m = _m(run)["idea_kept"]
    # 数字也要钉：plan 与锚点对不上算一条、plan 的编号不是 idea_001 又算一条。
    # 只断言 FAIL 的话，删掉「plan ≠ 锚点」那条判据这里照样绿——那条判据就没有门了。
    assert m.value == 2.0 and m.status == FAIL, m.note
    assert "idea_004" in m.note and BYO in m.note


def test_编号不是idea_001判fail(tmp_path):
    """自带想法整理出来的那一条说明书写死是 `idea_001`。编号漂了，
    说明整理那一步没照说明书做——**而它漂了以后，上面两条比对全都比在错的东西上**。"""
    run = _byo_run(tmp_path, idea_id="edu_007", anchor_idea_id="edu_007")
    m = _m(run)["idea_kept"]
    assert m.value == 2.0 and m.status == FAIL, m.note      # plan 与锚点各算一条
    assert BYO in m.note and "edu_007" in m.note


def test_多出一条候选判fail(tmp_path):
    """自带想法一次一个（设计稿第九节）。多一条是替用户加题。"""
    run = _byo_run(tmp_path, n_ideas=2)
    m = _m(run)["jury_recorded"]
    assert m.value == 1.0 and m.status == FAIL
    assert "2 条候选" in m.note


def test_带轮次或侧重的判词文件也算审过(tmp_path):
    """`oss review --kind ideas` 带 `--emphasis` 写的是 `review_ideas_<意见>.json`、
    带 `--round 2` 写的是 `review_ideas_r2.json`。只认精确的 `review_ideas.json`
    一个名字，等于**审得更细致反而判成没审过**——而那正是说明书鼓励模型去做的事。

    判据跟 `skillcheck.r_ideas_jury` 的 `startswith("review_ideas")` 是同一条：
    两处不一样时，同一个目录会在 `oss skillcheck` 里判 ✓、在 `oss behavior` 里判 ✗。
    """
    for name in ("review_ideas_r2.json", "review_ideas_theory.json"):
        run = _byo_run(tmp_path / name.removesuffix(".json"), jury=False)
        _json(run, name, {"verdict": "PASS", "findings": []})
        m = _m(run)["jury_recorded"]
        assert m.status == PASS, (name, m.note)
        assert name in m.note, (name, m.note)   # 备注里得写清凭据是哪一份


def test_备注里列出找到的每一份判词(tmp_path):
    """一次跑了两轮陪审时，表上要看得见是哪两份——否则「审过」这句话跟盘上对不起来。"""
    run = _byo_run(tmp_path)                    # 已经有 review_ideas.json
    _json(run, "review_ideas_r2.json", {"verdict": "PASS", "findings": []})
    m = _m(run)["jury_recorded"]
    assert m.status == PASS, m.note
    assert "review_ideas.json、review_ideas_r2.json" in m.note, m.note


def test_陪审团报告缺失判fail(tmp_path):
    run = _byo_run(tmp_path, jury=False)
    m = _m(run)["jury_recorded"]
    assert m.value == 1.0 and m.status == FAIL
    assert "review_ideas.json" in m.note
    assert _m(run)["idea_kept"].status == PASS, "陪审团缺席不牵连「想法有没有被换」"


def test_候选被砍到零条也判fail(tmp_path):
    """**判词只能记录，不能淘汰**（设计稿第四节）：把用户那条砍掉是最严重的一种换题。"""
    run = _byo_run(tmp_path, n_ideas=0)
    assert _m(run)["jury_recorded"].status == FAIL
    assert "0 条候选" in _m(run)["jury_recorded"].note


# ─────────────────────────── 老运行目录：两条都 n/a，一条也不许判

def test_source是model时两条都不适用(tmp_path):
    run = _byo_run(tmp_path, source="model")
    for k in ("idea_kept", "jury_recorded"):
        assert _m(run)[k].status == NA, k
        assert _m(run)[k].value is None, k
        assert "自带想法" in _m(run)[k].note


def test_老状态文件没有source字段时两条都不适用(tmp_path):
    run = _byo_run(tmp_path)
    _json(run, "run_state.json", {"stage": "6-run", "anchor": {
        "direction": IDEA_TEXT, "idea_id": BYO, "plan_sha": "abc",
        "frozen_at": "2026-08-29T01:00:00Z"}})
    assert _m(run)["idea_kept"].status == NA
    assert _m(run)["jury_recorded"].status == NA


def test_锚点没冻时两条都不适用(tmp_path):
    run = _byo_run(tmp_path)
    _json(run, "run_state.json", {"stage": "3-ideas", "anchor": {}})
    assert _m(run)["idea_kept"].status == NA and _m(run)["jury_recorded"].status == NA


def test_状态文件读不出来时两条都不适用(tmp_path):
    """**读不出来 ≠ user**：看不出来是谁出的题，就什么也不判。"""
    run = _byo_run(tmp_path)
    (run / "run_state.json").write_text("不是 JSON", encoding="utf-8")
    assert _m(run)["idea_kept"].status == NA and _m(run)["jury_recorded"].status == NA
    assert (run / "run_state.json").exists(), "核账是只读的，不许把坏文件挪走"


def test_锚点隔离标记在场时两条都不判(tmp_path):
    """`run_state.corrupt.json` 在场 = 锚点未经授权重建过。

    **盘上自洽不算数**：现在这份 `run_state.json` 是重建出来的，里面写着 `source: user`
    也只是重建的人写的。老规则 `_anchor_note` 遇到这个标记明确拒判
    （「锚点未经授权重建——测不出偏没偏」），这两条走同一条规矩。
    """
    run = _byo_run(tmp_path)                    # 一切都对：方向一致、编号是 idea_001、陪审报告在
    _json(run, "run_state.corrupt.json", {"stage": "3-ideas", "anchor": {}})
    for k in ("idea_kept", "jury_recorded"):
        m = _m(run)[k]
        assert m.status == NA and m.value is None, (k, m.value, m.note)
        assert "重建" in m.note and "corrupt" in m.note, (k, m.note)


def test_锚点还没冻时就算source是user也不判(tmp_path):
    """半截运行：题还没定下来，「有没有被换掉」这件事还没开始。"""
    run = _byo_run(tmp_path)
    _json(run, "run_state.json", {"stage": "3-ideas", "anchor": {
        "direction": IDEA_TEXT, "idea_id": BYO, "source": "user"}})   # 没有 frozen_at
    for k in ("idea_kept", "jury_recorded"):
        assert _m(run)[k].status == NA and _m(run)[k].value is None, k
    assert "frozen_at" in _m(run)["idea_kept"].note


def test_空目录两条也是不适用(tmp_path):
    m = _m(tmp_path)
    assert all(x.status == NA for x in m.values()), [(k, v.status) for k, v in m.items()]


# ─────────────────────────── 自带想法但文件读不到：n/a，不报「0 次偏移」

def test_ideas读不到时不许报没被换(tmp_path):
    """跟 `topic_switch` 同一条规矩：删掉文件不等于没换题。"""
    run = _byo_run(tmp_path)
    (run / "ideas.json").unlink()
    m = _m(run)["idea_kept"]
    assert m.status == NA and "ideas.json" in m.note
    j = _m(run)["jury_recorded"]
    assert j.status == NA and "数不出还剩几条候选" in j.note


def test_plan读不到时不许报没被换(tmp_path):
    run = _byo_run(tmp_path)
    (run / "plan.json").write_text("{半截", encoding="utf-8")
    m = _m(run)["idea_kept"]
    assert m.status == NA and "plan.json" in m.note


def test_ideas读不出候选清单时陪审那条走na(tmp_path):
    """文件是合法 JSON 却数不出候选：**不许按字符数计，也不许兜成 0 条判 FAIL。**

    `{"ideas": "一条"}` 按 `len()` 是 3 条，写成列表的 `[1, 2]` 被 `_dict` 兜回 `{}` 是 0 条——
    前者报「有 3 条候选」，后者报「用户的题被砍光了」，两句都是拿一份读不懂的文件换来的。
    """
    for bad in ({"direction": IDEA_TEXT, "ideas": "一条"}, [1, 2], {"direction": IDEA_TEXT}):
        run = _byo_run(tmp_path / f"r{abs(hash(str(bad)))}")
        _json(run, "ideas.json", bad)
        m = _m(run)["jury_recorded"]
        assert m.status == NA and m.value is None, (bad, m.value, m.note)
        assert "数不出还剩几条候选" in m.note, (bad, m.note)


def test_盘上一团乱码也不许崩(tmp_path):
    """行为指标是事后核账的，什么烂目录都可能递进来——崩掉等于没有账。"""
    run = _byo_run(tmp_path)
    (run / "ideas.json").write_text("[1, 2]", encoding="utf-8")
    (run / "plan.json").write_text("null", encoding="utf-8")
    rep, m = B.evaluate(run)
    assert len(rep.items) == 9 and len(m) == 9


# ─────────────────────────── 七条老规则一个字都不许变

_OLD_SEVEN = [
    ("behavior-review-rounds", "must", PASS, "2（合格线 <= 2）；loop_state.round = 2"),
    ("behavior-topic-switch", "must", PASS, "0（合格线 == 0）；没有换题留痕"),
    ("behavior-user-questions", "should", NA,
     "—（合格线 == 0）；宿主替模型问用户时 oss 不在场，留痕里看不见——"
     "宿主不提供就判 n/a，不编一个 0"),
    ("behavior-explore", "should", PASS, "3（合格线 <= 15）；处方 3 条 × 5"),
    ("behavior-paper-check", "should", PASS, "1（合格线 <= 5）；共校验 2 次"),
    ("behavior-runs", "should", PASS,
     "4（合格线 <= 15）；全部跑通；留痕只看得见走 oss 的动作，"
     "裸 python 跑的回归看不见——这个数是下限"),
    ("behavior-wall-clock", "should", PASS,
     "89 分钟（合格线 <= 120 分钟）；跑完了（有 oss paper export 或 stage_status=done）"),
]


def _good(rundir: Path) -> Path:
    """跟 `tests/test_behavior.py::_good` 逐字节相同的那份夹具。

    **故意重抄一遍而不是 import**：这条测试要钉的正是「老规则的输出没变」，
    从那边 import 夹具的话，那边改了这边跟着改，钉子就松了。
    """
    rows = [
        {"ts": "2026-08-29T01:00:00Z", "step": "profile", "command": "oss profile d.csv"},
        {"step": "results", "command": "oss run"},
        {"step": "route", "command": "oss route"},
        {"step": "explore", "command": "oss run --explore --for pretrend_fail",
         "args": {"for": "pretrend_fail"}},
        {"step": "explore", "command": "oss run --explore --for pretrend_fail",
         "args": {"for": "pretrend_fail"}},
        {"step": "explore", "command": "oss run --explore --for review-r1",
         "args": {"for": "review-r1"}},
        {"step": "results", "command": "oss run"},
        {"step": "results", "command": "oss run"},
        {"step": "results", "command": "oss run"},
        {"step": "paper", "command": "oss paper check", "outcome": "refused"},
        {"step": "paper", "command": "oss paper check"},
        {"ts": "2026-08-29T02:29:00Z", "step": "paper", "command": "oss paper export"},
    ]
    for i, r in enumerate(rows, start=1):
        r.setdefault("seq", i)
        r.setdefault("ts", f"2026-08-29T01:{i:02d}:00Z")
        r.setdefault("step", "x")
        r.setdefault("command", "")
        r.setdefault("outcome", "ok")
    (rundir / "trace.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    _json(rundir, "loop_state.json",
          {"round": 2, "max_rounds": 2, "decision": {"decision": "stop"}})
    _json(rundir, "run_state.json",
          {"stage": "done", "stage_status": "done", "anchor": {"idea_id": "edu_001"}})
    _json(rundir, "route.json", {"plan_sha": "s", "items": [
        {"id": "pretrend_fail", "priority": "must", "trigger": "t", "kind": "disclose"},
        {"id": "imbalance", "priority": "should", "trigger": "t", "kind": "disclose"},
        {"id": "attrition", "priority": "should", "trigger": "t", "kind": "add_robustness"}]})
    return rundir


def test_七条老规则的判词与备注逐字未变(tmp_path):
    """**加两条不许碰七条。**这条测试逐字比 id / 优先级 / 判词 / detail 那一整串。"""
    rep, _ms = B.evaluate(_good(tmp_path))
    got = [(i.id, i.priority, i.status, i.detail) for i in rep.items[:7]]
    assert got == _OLD_SEVEN, [x for x, y in zip(got, _OLD_SEVEN) if x != y]


def test_新两条排在七条后面且老目录判不适用(tmp_path):
    rep, ms = B.evaluate(_good(tmp_path))
    assert [i.id for i in rep.items[7:]] == ["behavior-idea-kept", "behavior-jury-recorded"]
    assert ms["idea_kept"].status == NA and ms["jury_recorded"].status == NA
    assert rep.to_dict()["summary"] == {PASS: 6, FAIL: 0, NA: 3}


def test_表上多两行且老七行的文字没动(tmp_path):
    _rep, ms = B.evaluate(_good(tmp_path))
    lines = B.table(ms).splitlines()
    assert len(lines) == 2 + 9, lines               # 表头 + 分隔线 + 九行
    assert "审稿轮数" in lines[2] and "总耗时" in lines[8]
    assert "自带想法" in lines[9] and "陪审团" in lines[10]
    assert lines[9].rstrip().endswith("·") and lines[10].rstrip().endswith("·")


# ─────────────────────────── CLI：`oss eval --behavior`

def test_list多两行一共九行(tmp_path):
    """**不为这两行新建 golden**：`eval --behavior --list` 从来没有基线，
    列表内容由 `_RULES` 直接渲染，这条 CliRunner 断言就是它的门。"""
    r = runner.invoke(app, ["eval", "--behavior", "--list"])
    assert r.exit_code == 0, r.output
    # `[must  ]` 里有空格、`[should]` 里没有，两种行按 split() 切出来的列号不一样——
    # 认前缀而不是认列号，才是「一行一个 id」这件事本身
    ids = [t for x in r.output.splitlines() for t in x.split() if t.startswith("behavior-")]
    assert len(ids) == 9
    assert ids[:7] == ["behavior-review-rounds", "behavior-topic-switch",
                       "behavior-user-questions", "behavior-explore",
                       "behavior-paper-check", "behavior-runs", "behavior-wall-clock"]
    assert ids[7:] == ["behavior-idea-kept", "behavior-jury-recorded"]
    assert "[must  ] behavior-idea-kept" in r.output
    assert "[should] behavior-jury-recorded" in r.output


def test_不带behavior的eval_list一条都没多(tmp_path):
    """两把尺子分开量：说明书那 36 条规则一条都不许动。

    26 是基线条数（2026-09-07 加 power-effect-source、state-not-hand-written、self-review-disclosed、byo-idea-declared、open-items-accounted 后由 23 变 28；2026-09-13 加 research-type-declared、causal-wording 后由 28 变 30；2026-09-14 加 causal-estimand、causal-diagnostics-cited 后由 30 变 32；2026-09-15 加 survey-design-declared、survey-descriptive-weighted 后由 32 变 34；2026-09-15 加 descriptive-estimand、descriptive-magnitude 后由 34 变 36；2026-09-16 加 ideas-candidates-monotone、ideas-two-lists 后由 36 变 38）。
    真正要守的是上一行——**行为规则不许混进默认名单**；条数是顺带的哨兵，
    有意加规则时跟着改，别不明不白地变。
    """
    r = runner.invoke(app, ["eval", "--list"])
    assert r.exit_code == 0
    assert "behavior-" not in r.output
    assert len([x for x in r.output.splitlines() if x.strip().startswith("[")]) == 38


def test_想法被换时eval_behavior退7(tmp_path):
    run = tmp_path / "r"
    _byo_run(run, direction="模型自己改的方向", anchor_direction=IDEA_TEXT)
    r = runner.invoke(app, ["eval", "--behavior", "--run", str(run)])
    assert r.exit_code == 7, r.output
    assert "behavior-idea-kept" in r.output
    assert [x for x in r.output.splitlines() if "自带想法" in x and "✗" in x], r.output
    d = json.loads((run / "eval_behavior.json").read_text(encoding="utf-8"))
    # 方向被重写，老那条锚点比对（behavior-topic-switch）照样抓得到——**七条老规则一个字没改**，
    # 所以两条 must 一起不过：老那条报「计划偏离了冻结的锚点」，新那条报「用户的题被换了」。
    assert d["must_failed"] == ["behavior-topic-switch", "behavior-idea-kept"]
    assert d["metrics"]["idea_kept"]["value"] == 1.0


def test_陪审团缺失只是should不退7(tmp_path):
    run = tmp_path / "r"
    _byo_run(run, jury=False)
    r = runner.invoke(app, ["eval", "--behavior", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert "must 级行为指标全过" in r.output
    d = json.loads((run / "eval_behavior.json").read_text(encoding="utf-8"))
    assert d["must_failed"] == []
    assert d["metrics"]["jury_recorded"]["status"] == FAIL


def test_一条留痕都没有时九条全na退1(tmp_path):
    """条数从七变九，这条「没交卷」的规矩一个字不松。"""
    run = tmp_path / "r"
    run.mkdir()
    r = runner.invoke(app, ["eval", "--behavior", "--run", str(run)])
    assert r.exit_code == 1, r.output
    assert "判不了" in r.output and "must 级行为指标全过" not in r.output
    assert "七条" not in r.output, "条数已经不是七了，这句话不许再写死数字"
    d = json.loads((run / "eval_behavior.json").read_text(encoding="utf-8"))
    assert d["summary"] == {PASS: 0, FAIL: 0, NA: 9}, d["summary"]
