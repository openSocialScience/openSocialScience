"""自带想法模式的程序侧三处：契约字段、锚点带 source、design_locked 门。

事故形状（还没发生，但形状是现成的）：用户把成熟想法写进 `--- idea:`，
模型「优化」成另一个题；或者用户用 `--- design:` 锁死了 `estimator=did_cs`，
审稿意见来了一轮，模型顺手把主规格换成 `did`。**两种都不会报错**——
产出物照样是一篇论文，锚点照样冻着，`oss eval` 照样一片绿。

看得见这件事的只有两样：`ideas.json` 里那条记录是不是还写着 `source: "user"`，
以及 `plan.json` 里那份 `design_locked` 副本跟计划本体还合不合得上。
这个文件把这两样变成门。

**离线，不叫模型。**用的数据是 `tests/fixtures/did_clean.csv`。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from oss.api import analysis as analysis_api
from oss.api import anchor as anchor_api
from oss.api import errors
from oss.api import profile as profile_api
from oss.cli.main import app
from oss.core import route as route_mod
from oss.core import state as state_mod
from oss.core.contracts import AnalysisPlan, Idea, IdeaSet, Route, RouteItem

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"

#: 自带想法整理出来的那一条的编号。说明书写死 idea_001，指标也认这个编号。
BYO = "idea_001"


def _rows(rundir: Path) -> list[dict]:
    p = rundir / "trace.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _ideas(rundir: Path, *, source: str = "user", direction: str = "自带想法原文",
           n: int = 1) -> None:
    """`ideas.json`：自带想法模式下只有一条，`source = "user"`。"""
    (rundir / "ideas.json").write_text(json.dumps({
        "direction": direction,
        "ideas": [{"idea_id": BYO if i == 0 else f"idea_{i + 1:03d}",
                   "title": "错误定价与供应链风险", "research_question": "Q",
                   "lens": "user-idea", "source": source} for i in range(n)],
    }, ensure_ascii=False), encoding="utf-8")


def _plan(rundir: Path, *, lock: dict | None = None, **over) -> None:
    """一份能真跑起来的 ols_fe 计划；`lock` 非 None 就写进 `design_locked`。"""
    d = {"plan_id": "p001", "idea_id": BYO, "estimator": "ols_fe",
         "outcome": "y", "treatment": "x",
         "fixed_effects": ["unit", "year"], "cluster_level": "unit"}
    d.update(over)
    if lock is not None:
        d["design_locked"] = lock
    (rundir / "plan.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def _run(tmp_path: Path, *, lock: dict | None = None, source: str = "user", **over) -> Path:
    """一个画过像、有 ideas.json 与 plan.json 的运行目录。"""
    import shutil
    run = tmp_path / "r"
    run.mkdir()
    shutil.copy(FIXTURES / "did_clean.csv", run / "data.csv")
    profile_api.profile(str(run / "data.csv"), run=str(run))
    _ideas(run, source=source)
    _plan(run, lock=lock, **over)
    return run


# ─────────────────────────────── a. 契约：谁出的题

def test_idea缺省是模型出的题():
    """老 `ideas.json` 一个字不改就该读得出来，且**不许被读成 user**。"""
    i = Idea(idea_id=BYO, title="T", research_question="Q")
    assert i.source == "model"


def test_idea的source只认两个取值():
    """`source` 不是自由文本：写错一个字就是「这条到底谁出的」读不出来，
    而两条 must / should 级指标全靠它分支。**拼错不静默。**"""
    with pytest.raises(ValidationError) as exc:
        Idea(idea_id=BYO, title="T", research_question="Q", source="human")
    # 断到具体那一条：光看 "source" 在不在报错文本里，换成任何一种别的错
    # （拼错字段名、类型不对）都照样过——那时这条测试就不再测它说要测的东西了
    errs = exc.value.errors()
    assert [(e["type"], e["loc"]) for e in errs] == [("literal_error", ("source",))]
    assert errs[0]["ctx"]["expected"] == "'model' or 'user'"


def test_ideas_json带source能原样读回来(tmp_path):
    """从前 `Idea` 是 `BaseModel`（extra 缺省 ignore），写进去的 `source` 会被**静默丢掉**。"""
    _ideas(tmp_path)
    got = IdeaSet.load(tmp_path / "ideas.json")
    assert [x.source for x in got.ideas] == ["user"]
    assert got.direction == "自带想法原文"


def test_没有source的老文件照样读得出来(tmp_path):
    (tmp_path / "ideas.json").write_text(json.dumps({
        "direction": "教育扩张与生育",
        "ideas": [{"idea_id": "edu_001", "title": "T", "research_question": "Q"}]},
        ensure_ascii=False), encoding="utf-8")
    assert IdeaSet.load(tmp_path / "ideas.json").ideas[0].source == "model"


# ─────────────────────────────── b. 锚点：记得住谁出的题

def test_冻结锚点带上source(tmp_path):
    run = _run(tmp_path)
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
    assert state_mod.load(run).anchor["source"] == "user"


def test_老目录冻出来的锚点是model(tmp_path):
    run = _run(tmp_path, source="model")
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
    assert state_mod.load(run).anchor["source"] == "model"


def _mixed_ideas(rundir: Path) -> None:
    """两条候选，第一条是用户出的、第二条是模型出的。**顺序是故意的**：
    读第一条的写法在这份清单上永远答 user。"""
    (rundir / "ideas.json").write_text(json.dumps({
        "direction": "自带想法原文",
        "ideas": [{"idea_id": BYO, "title": "T", "research_question": "Q",
                   "lens": "user-idea", "source": "user"},
                  {"idea_id": "idea_002", "title": "T2", "research_question": "Q2",
                   "lens": "mechanism", "source": "model"}]}, ensure_ascii=False),
        encoding="utf-8")


def test_source读的是计划指向的那条不是第一条(tmp_path):
    """锚点上那一格问的是「**这次要跑的这道题**是谁出的」，不是「清单里排第一的是谁出的」。

    读第一条的写法在混合清单上一律答第一条的 source：计划指着模型那条时报 `user`
    （凭空多两条只在 user 下判的指标），计划指着用户那条却排在后面时报 `model`
    （两条指标从此一律 n/a，而那正是最该判的时候）。两个方向都错，两个方向都要钉。
    """
    for idea_id, want in (("idea_002", "model"), (BYO, "user")):
        run = tmp_path / idea_id
        run.mkdir()
        _mixed_ideas(run)
        _plan(run, idea_id=idea_id)
        assert state_mod.current_anchor(run)["source"] == want, idea_id
        state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
        a = state_mod.load(run).anchor
        assert a["source"] == want and a["idea_id"] == idea_id


def test_找不到计划指的那条就退回第一条(tmp_path):
    """环节 3 刚整理完、`plan.json` 还没写的时候只有那一条，那时它就是答案。
    计划指了个清单里没有的编号，也退回第一条——**不许因为找不到就判成 model**：
    那样「计划的编号打错一个字」会顺手把「这是用户出的题」抹掉。"""
    _mixed_ideas(tmp_path)
    assert state_mod.current_anchor(tmp_path)["source"] == "user"    # 没有 plan.json
    _plan(tmp_path, idea_id="idea_999")
    assert state_mod.current_anchor(tmp_path)["source"] == "user"


def test_ideas读不出来时算model不算user(tmp_path):
    """**不许把「看不出来是谁出的题」当成 user**：那样老运行会凭空多两条判词。"""
    run = _run(tmp_path)
    for junk in ('{"direction": "x", "ideas": "不是列表"}', '{"direction": "x"}', "{坏的"):
        (run / "ideas.json").write_text(junk, encoding="utf-8")
        assert state_mod.current_anchor(run)["source"] == "model", junk


def test_写盘前把认不得的source收敛成model(tmp_path):
    """签名标的是 `Literal["model", "user"]`，但标注只挡看代码的人，挡不住运行时。

    `"users"` 这种拼错落到盘上，两条只在 `user` 下判的指标既不判也不报错——
    **宁可少判**：认不得的一律按 `model`，跟 `current_anchor()` 读不出来时同一条口径。"""
    state_mod.freeze_anchor(tmp_path, direction="D", idea_id=BYO, plan_sha="s",
                            source="users")
    assert state_mod.load(tmp_path).anchor["source"] == "model"
    # reset_anchor（换题那条路）走的是同一个构造，同样收敛
    state_mod.reset_anchor(tmp_path, direction="D", idea_id="idea_002", plan_sha="",
                           source="USER")
    assert state_mod.load(tmp_path).anchor["source"] == "model"


def test_oss_run真跑一次锚点上就有source(tmp_path):
    run = _run(tmp_path)
    analysis_api.run(str(run))
    a = state_mod.load(run).anchor
    assert a["source"] == "user" and a["idea_id"] == BYO and a["frozen_at"]


def test_用户授权换题之后source跟着盘上的ideas走(tmp_path):
    """换题不该把「这是用户出的题」抹掉——抹掉之后两条指标从此永远 n/a，
    而那正是换过题之后最该继续量的时候。"""
    run = _run(tmp_path)
    analysis_api.run(str(run))
    anchor_api.switch(str(run), to="idea_002", why="用户自己要换", authorized_by="user")
    assert state_mod.load(run).anchor["source"] == "user"
    # 盘上换成模型出的题，再换一次：这时就该跟着变成 model
    _ideas(run, source="model")
    anchor_api.switch(str(run), to="edu_007", why="改用模型出的题", authorized_by="user")
    assert state_mod.load(run).anchor["source"] == "model"


# ─────────────────────────────── c. design_locked：用户锁的设计不许被静默改掉

def test_plan可以带design_locked(tmp_path):
    _plan(tmp_path, lock={"estimator": "ols_fe", "outcome": "y",
                          "treatment": "x", "fixed_effects": ["unit", "year"],
                          "cluster_level": "unit"})
    p = AnalysisPlan.load(tmp_path / "plan.json")
    assert p.design_locked is not None
    assert p.design_locked.fixed_effects == ["unit", "year"]


def test_没有design_locked时是None(tmp_path):
    _plan(tmp_path)
    assert AnalysisPlan.load(tmp_path / "plan.json").design_locked is None


def test_design_locked里写用户那套键名会被拒(tmp_path):
    """用户输入写的是 `fe=` / `cluster=`，`plan.json` 里的键名是 `fixed_effects` /
    `cluster_level`。**原样照抄用户的写法是这条路上最可能犯的错**——
    静默接受等于锁了个空，所以 `DesignLock` 禁未知键。"""
    _plan(tmp_path, lock={"fe": ["unit", "year"], "cluster": "unit"})
    with pytest.raises(ValidationError) as exc:
        AnalysisPlan.load(tmp_path / "plan.json")
    # `"fe" in str(exc.value)` 太松：`fixed_effects` 里也有 "fe"，
    # 连一条「fixed_effects 类型不对」都能让它过。断 extra_forbidden 与 loc 里的键名。
    errs = exc.value.errors()
    assert {e["type"] for e in errs} == {"extra_forbidden"}
    assert {e["loc"] for e in errs} == {("design_locked", "fe"), ("design_locked", "cluster")}
    assert all(e["msg"] == "Extra inputs are not permitted" for e in errs)


def test_锁跟计划一致时照常跑(tmp_path):
    run = _run(tmp_path, lock={"estimator": "ols_fe", "outcome": "y", "treatment": "x",
                               "fixed_effects": ["unit", "year"], "cluster_level": "unit"})
    res = analysis_api.run(str(run))
    assert res["data"]["explore"] is False and (run / "results.json").exists()
    assert not [r for r in _rows(run) if r.get("step") == "design_lock"]


def test_模型改了estimator时退6并留痕(tmp_path):
    """设计稿第八节点名的那条：模型改了 `estimator` → 退出码 6，`trace` 留痕。"""
    run = _run(tmp_path, estimator="ols_fe",
               lock={"estimator": "did_cs", "outcome": "y"})
    with pytest.raises(errors.Refused) as exc:
        analysis_api.run(str(run))
    assert exc.value.code == 6
    assert any("estimator" in p for p in exc.value.problems), exc.value.problems
    assert not (run / "results.json").exists(), "被这道门拒的跑不许落结果"
    assert not (run / state_mod.FILE).exists(), "被拒的跑不许冻锚点、不许推阶段"
    row = _rows(run)[-1]
    assert row["step"] == "design_lock" and row["outcome"] == "refused"
    assert row["args"]["keys"] == ["estimator"]


def test_五个键一次报全(tmp_path):
    """改完一个再被拦一次，是把一次修改拆成五次往返。"""
    run = _run(tmp_path, lock={"estimator": "did_cs", "outcome": "fertility",
                               "treatment": "policy", "fixed_effects": ["firm"],
                               "cluster_level": "firm"})
    with pytest.raises(errors.Refused) as exc:
        analysis_api.run(str(run))
    assert exc.value.code == 6
    assert len([p for p in exc.value.problems if "plan.json 写的是" in p]) == 5


def test_固定效应换个顺序不算改(tmp_path):
    """固定效应是个**集合**：`firm+year` 与 `year+firm` 是同一个设计。
    拿一次无关紧要的差别去拦用户，只会教人把这道门关掉。"""
    run = _run(tmp_path, lock={"fixed_effects": ["year", "unit"]})
    assert analysis_api.run(str(run))["data"]["explore"] is False


def test_固定效应写重了也不算改(tmp_path):
    """集合就是集合：用户把 `fe=unit+unit+year` 写重一项，跟 `unit+year` 是同一个设计。

    `DesignLock.fixed_effects` 的文档串说的是「顺序不算数、是个集合」——比对时
    重复项还参与，就成了文档说一套、门判另一套。"""
    run = _run(tmp_path, lock={"fixed_effects": ["unit", "unit", "year"]})
    assert analysis_api.run(str(run))["data"]["explore"] is False
    assert not [r for r in _rows(run) if r.get("step") == "design_lock"]


def test_空值的键不比对(tmp_path):
    """`design:` 五个键都可缺省，缺省的由模型定——空串 / 空列表 = 没锁。"""
    run = _run(tmp_path, lock={"estimator": "", "outcome": "", "treatment": "",
                               "fixed_effects": [], "cluster_level": ""})
    assert analysis_api.run(str(run))["data"]["explore"] is False
    assert not [r for r in _rows(run) if r.get("step") == "design_lock"]


def test_探索也拦(tmp_path):
    """探索路径上也跑这道门——**拦的是 `plan.json` 本体**，不是探针。

    本体已经不是用户那个设计时，探索也只是在为一份错规格找证据；留痕里的
    `command` 要写全（含 `--for`），事后才看得出是哪条路上被拦的。"""
    run = _run(tmp_path, estimator="ols_fe", lock={"estimator": "did_cs"})
    (run / "route.json").write_text(json.dumps({"plan_sha": "s", "items": [
        {"id": "pretrend_fail", "priority": "must", "trigger": "t", "kind": "disclose"}]}),
        encoding="utf-8")
    with pytest.raises(errors.Refused) as exc:
        analysis_api.run(str(run), explore=True, for_id="pretrend_fail",
                         explore_plan=str(run / "plan.json"))
    assert exc.value.code == 6
    assert _rows(run)[-1]["command"] == "oss run --explore --for pretrend_fail"


def test_route_apply不会把锁弄丢(tmp_path):
    """`oss route --apply` 走的是「读成对象 → model_dump → 打补丁 → 写回去」
    （`oss.core.route.apply`）。**未声明的顶层键会在这一趟里被静默抹掉**，
    而 `--apply` 恰恰是最可能改动 `fixed_effects` / `cluster_level` 的那条路径——
    锁被它要防的动作抹掉，比没有锁更糟。

    **调的是真的 `route_mod.apply()`**：手搓一遍 load → model_dump → save 只能证明
    「我抄的这几行不丢字段」，证明不了盘上那条路径不丢——往返写回哪天挪进别的函数、
    多一道 `exclude_unset`，手搓的复制品照样全绿。"""
    p = tmp_path / "plan.json"
    _plan(tmp_path, lock={"estimator": "ols_fe", "fixed_effects": ["unit", "year"]})
    route = Route(plan_sha="s", items=[RouteItem(
        id="cluster_too_few", priority="should", trigger="聚类层只有 8 个",
        kind="patch_plan", patch={"cluster_level": "year"})])   # 补丁改了一个没锁的键
    route_mod.apply(tmp_path, route, "cluster_too_few")
    back = AnalysisPlan.load(p)
    assert back.design_locked is not None
    assert back.design_locked.estimator == "ols_fe"
    assert back.design_locked.fixed_effects == ["unit", "year"]
    assert back.cluster_level == "year"


# ─────────────────────────────── d. plan.json 读不进：报人话，不甩 traceback

def _cli(rundir: Path, *args: str) -> subprocess.CompletedProcess:
    """真的从命令行跑一次。

    `CliRunner` 会把没接住的异常收进 `result.exception`、**不打印**——
    「屏幕上有没有 traceback」这件事它根本量不了。要钉住这一条，只能起一个真进程。
    """
    return subprocess.run([sys.executable, "-m", "oss.cli.main", "run", "--run", str(rundir),
                           *args], capture_output=True, text=True, cwd=REPO)


def test_design_locked抄错键名时报人话不打traceback(tmp_path):
    """说明书三处点名的那个最常见的错：把用户写的 `fe=` / `cluster=` 原样抄进
    `design_locked`。从前这里打的是一整屏 pydantic traceback——模型读不懂自己
    错在哪，只会再猜一次；而报错里连「改成什么」都没有。"""
    run = _run(tmp_path, lock={"fe": ["unit", "year"], "cluster": "unit"})
    out = _cli(run)
    text = out.stdout + out.stderr
    assert out.returncode == 2, text
    assert "Traceback" not in text and "pydantic" not in text, text
    assert "design_locked" in text, text
    # 光说「extra_forbidden」等于把映射表藏起来：得说出该改成哪个键名
    assert "fixed_effects" in text and "cluster_level" in text, text
    assert "input-and-rundir.md" in text, text


def test_未知顶层键也报人话不打traceback(tmp_path):
    """不只是 `design_locked` 那一种：任何读不进的 `plan.json` 都走这条路。"""
    run = _run(tmp_path, bogus_key="随便写的一个键")
    out = _cli(run)
    text = out.stdout + out.stderr
    assert out.returncode == 2, text
    assert "Traceback" not in text, text
    assert "bogus_key" in text and "extra_forbidden" in text, text
    # 这一条跟 design_locked 无关，不许把那句映射表提示到处贴
    assert "input-and-rundir.md" not in text, text


def test_读不进计划时退出码是2(tmp_path):
    """`BadInput` 的缺省退出码——「参数不对」，不是门拒绝（6）也不是缺前置（1）。"""
    run = _run(tmp_path, lock={"fe": ["unit"]})
    with pytest.raises(errors.BadInput) as exc:
        analysis_api.run(str(run))
    assert exc.value.code == 2
    assert exc.value.summary.startswith("plan.json 读不进：")
    assert exc.value.problems[:1] == ["design_locked.fe —— extra_forbidden"]
    assert not (run / "results.json").exists()
    assert not (run / state_mod.FILE).exists(), "读不进计划的跑不许冻锚点、不许推阶段"


def test_读不进计划时json只有一行(tmp_path):
    """`--json` 那一侧从前**根本不出 JSON**：异常在 `jout.fail` 之前就抛出去了，
    调用方拿到的是一屏 traceback 和 exit 1。"""
    run = _run(tmp_path, lock={"fe": ["unit", "year"], "cluster": "unit"})
    r = runner.invoke(app, ["run", "--run", str(run), "--json"])
    assert r.exit_code == 2, r.output
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, lines
    d = json.loads(lines[0])
    assert d["ok"] is False and d["data"]["exit_code"] == 2
    assert d["data"]["kind"] == "bad_input"
    assert "design_locked" in d["summary"] and "fixed_effects" in d["summary"]
    assert r.stderr == "", "--json 下别处什么都不许打"


def test_读不进计划时留一条痕(tmp_path):
    """被拒也要留痕：事后核账看得见「这次跑因为计划读不进被挡回去了」。"""
    run = _run(tmp_path, lock={"fe": ["unit"]})
    with pytest.raises(errors.BadInput):
        analysis_api.run(str(run))
    row = _rows(run)[-1]
    assert row["step"] == "run" and row["outcome"] == "refused"
    assert row["command"] == "oss run"
    assert row["args"]["fields"] == ["design_locked.fe"]
    assert row["note"].startswith("plan.json 读不进：")


# ─────────────────────────────── CLI：退出码要真的到得了命令行

def test_cli上退出码是6(tmp_path):
    run = _run(tmp_path, estimator="ols_fe", lock={"estimator": "did_cs"})
    r = runner.invoke(app, ["run", "--run", str(run)])
    assert r.exit_code == 6, r.output
    assert "用户锁定的设计" in r.output
    assert "estimator" in r.output


def test_cli上没锁的老目录照旧退0(tmp_path):
    """**回归护栏**：没有 `design_locked` 的运行目录，这条命令一个字都不该变。

    只认设计锁门自己的措辞（`design_lock`、「用户锁定的设计」）——研究类型警告里
    「第一次写上类型的那次跑就锁定」也含「锁定」两个字，是另一件事，不该被这条守卫误判。
    """
    run = _run(tmp_path, source="model")
    r = runner.invoke(app, ["run", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert "design_lock" not in r.output and "用户锁定的设计" not in r.output
