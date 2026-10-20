"""研究类型主轴·地基（docs/design/2026-09-13-research-types.md）。

研究类型按「想下什么结论」分（causal / descriptive / measurement / simulation），不按方法分；
类型随锚点冻结，冻结后只许在识别站不住时从因果降为描述。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from oss.cli.main import app
from oss.core import state as state_mod
from oss.core.contracts import (
    RESEARCH_TYPE_LABELS,
    RESEARCH_TYPES,
    AnalysisPlan,
    Idea,
    IdeaSet,
    ResearchPart,
)

FIX = Path(__file__).parent / "fixtures"
SK = Path(__file__).parent.parent / ".claude" / "skills"
runner = CliRunner()


# ═══════════ 一、契约


def test_计划与候选都有研究类型_缺省为空() -> None:
    assert AnalysisPlan().research_type == "" and AnalysisPlan().also == []
    assert Idea(idea_id="i", title="t", research_question="q").research_type == ""
    assert RESEARCH_TYPES == ("causal", "descriptive", "measurement", "simulation")
    assert RESEARCH_TYPE_LABELS["descriptive"] == "描述"


def test_老计划没有这一栏照样读得进(tmp_path: Path) -> None:
    """`AnalysisPlan` 禁未知键、但缺省字段可以缺席——老运行目录的计划一个字不改就能读。"""
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"plan_id": "p", "idea_id": "i", "estimator": "did"}), encoding="utf-8")
    assert AnalysisPlan.load(p).research_type == ""


@pytest.mark.parametrize("bad", ["因果", "causality", "Causal"])
def test_研究类型只认四个英文值(bad: str) -> None:
    """中文写法由说明书换成英文值再写进文件；拼错一个字要当场报，不能静默当成没声明。"""
    with pytest.raises(ValidationError):
        AnalysisPlan(research_type=bad)
    with pytest.raises(ValidationError):
        Idea(idea_id="i", title="t", research_question="q", research_type=bad)


def test_其它部分的类型写进also() -> None:
    assert AnalysisPlan(research_type="causal", also=["measurement"]).also == ["measurement"]
    with pytest.raises(ValidationError):
        AnalysisPlan(research_type="causal", also=["因果"])
    with pytest.raises(ValidationError):
        AnalysisPlan(research_type="descriptive", also=["causal"])


def test_研究类型的取值只写一处() -> None:
    """标签表、schema 枚举都跟 RESEARCH_TYPES 对得上——几处各写一遍，漂开了锁定会静默失效。"""
    assert tuple(RESEARCH_TYPE_LABELS) == RESEARCH_TYPES
    enum = AnalysisPlan.model_json_schema()["properties"]["research_type"]["enum"]
    assert enum == ["", *RESEARCH_TYPES]
    assert get_args(ResearchPart) == tuple(t for t in RESEARCH_TYPES if t != "causal")


def test_老的候选清单没有这一栏照样读得进(tmp_path: Path) -> None:
    """`IdeaSet` 禁未知键、`Idea` 不禁——老 ideas.json 一个字不改就能读，类型读成空串。"""
    p = tmp_path / "ideas.json"
    p.write_text(json.dumps({"direction": "d", "ideas": [
        {"idea_id": "i", "title": "t", "research_question": "q"}]}), encoding="utf-8")
    assert IdeaSet.load(p).ideas[0].research_type == ""


def test_schema示例里写了研究类型() -> None:
    from oss.core import fake

    assert fake.plan("idea_001").research_type == "causal"
    assert all(i.research_type == "causal" for i in fake.ideas("d").ideas)


# ═══════════ 二、锚点


def _seed(run: Path, plan: dict, *, research_question: str = "Q",
          extra_ideas: tuple[dict, ...] = (), data: str = "did_clean.csv") -> Path:
    """到「写好计划」为止的运行目录：数据画像 → ideas.json → plan.json。**一次网都不连。**"""
    run.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIX / data, run / "data.csv")
    r = runner.invoke(app, ["profile", str(run / "data.csv"), "--run", str(run)])
    assert r.exit_code == 0, r.output
    (run / "ideas.json").write_text(json.dumps({"direction": "政策冲击与产出", "ideas": [
        {"idea_id": "edu_001", "title": "T", "research_question": research_question}, *extra_ideas]},
        ensure_ascii=False), encoding="utf-8")
    _write_plan(run, plan)
    return run


def _write_plan(run: Path, plan: dict) -> None:
    (run / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


def _plan_patch(run: Path, **kw) -> None:
    p = json.loads((run / "plan.json").read_text(encoding="utf-8"))
    p.update(kw)
    _write_plan(run, p)


def _run(run: Path):
    return runner.invoke(app, ["run", "--run", str(run)])


EST = {"unit_quantity": "同一单位受处理与否时 y 之差", "target_population": "夹具面板里的全部单位",
       "assumptions": ["给定双向固定效应后无未观测混淆"], "link": "双向固定效应下 x 的系数对上平均效应"}

#: 回归、不带处理时点：结果里没有「平行趋势」这条诊断——**降级没有机械理由**
OLS = {"plan_id": "p001", "idea_id": "edu_001", "estimator": "ols_fe", "outcome": "y", "treatment": "x",
       "fixed_effects": ["unit", "year"], "cluster_level": "unit", "estimand": EST}

#: 测量块。**声明为测量的计划必填**（测量骨架实施计划「零之二」第 ① 条），列名要在数据里
MEAS = {"kind": "application", "construct": "产出水平", "indicators": ["y"]}

#: 跟 OLS 一样、但带 treat_time——`is_causal()` 的估计量兜底规则认它是因果形状的设计，
#: 又不必装 StatsPAI（estimator 仍是 ols_fe，不落 DID_FAMILY 那一支，不会触发分批实施警告）
OLS_TREAT = {**OLS, "treat_time": "treat_time"}


def test_锚点冻结时记下研究类型(tmp_path: Path) -> None:
    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"})
    r = _run(run)
    assert r.exit_code == 0, r.output
    a = state_mod.load(run).anchor
    assert a["research_type"] == "causal" and a["frozen_at"]


def test_没声明类型的计划冻结成空格(tmp_path: Path) -> None:
    """空格与缺席要分得开：缺席 = 这一栏出现之前就冻结的老锚点，研究类型门对它不比。"""
    run = _seed(tmp_path / "r", OLS)
    assert _run(run).exit_code == 0
    a = state_mod.load(run).anchor
    assert "research_type" in a and a["research_type"] == ""


def test_换题时研究类型按新计划重算(tmp_path: Path) -> None:
    from oss.api import anchor as anchor_api

    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"},
                extra_ideas=({"idea_id": "edu_002", "title": "T2", "research_question": "Q2"},))
    assert _run(run).exit_code == 0
    _plan_patch(run, idea_id="edu_002", research_type="descriptive")
    anchor_api.switch(str(run), to="edu_002", why="用户要换", authorized_by="user")
    assert state_mod.load(run).anchor["research_type"] == "descriptive"


def test_先换题后写计划_类型等新题第一次跑出结果时补上(tmp_path: Path) -> None:
    """换题那一刻盘上还是旧题的计划：旧题的类型不许冒充新题的——先留空，新计划跑出结果时补上。

    这一次补不算「看过结果才补」——是新题自己的第一次跑，不留 research_type_declared_late。
    """
    from oss.api import anchor as anchor_api

    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"},
                extra_ideas=({"idea_id": "edu_002", "title": "T2", "research_question": "Q2"},))
    assert _run(run).exit_code == 0
    anchor_api.switch(str(run), to="edu_002", why="用户要换", authorized_by="user")
    a = state_mod.load(run).anchor
    assert a["idea_id"] == "edu_002" and a["research_type"] == "" and a["plan_sha"] == ""
    _plan_patch(run, idea_id="edu_002", research_type="measurement", measurement=MEAS)
    assert _run(run).exit_code == 0
    a = state_mod.load(run).anchor
    assert a["research_type"] == "measurement" and a["plan_sha"]
    assert "research_type_declared_late" not in _steps(run)


def test_老锚点没有这格_声明也不用来锁定_不留痕(tmp_path: Path) -> None:
    """legacy 锚点（这一格压根不存在，冻结时研究类型这条轴线还没上线）不算「新式锚点」——
    之后计划才声明类型，不补这一格，也不留「看过结果才补」的证据。"""
    run = _seed(tmp_path / "r", OLS)
    assert _run(run).exit_code == 0
    st = state_mod.load(run)
    st.anchor.pop("research_type")
    state_mod.save(run, st)
    _plan_patch(run, research_type="descriptive")
    assert _run(run).exit_code == 0
    a = state_mod.load(run).anchor
    assert "research_type" not in a
    assert "research_type_declared_late" not in _steps(run)


def test_伪造的看过结果才补留痕不认(tmp_path: Path) -> None:
    """`oss trace save` 写的行 `command` 是空串、`args` 是空字典——
    伪造不出 `declared_late` 认的那种形状。"""
    from oss.gates import research_type as rt_gate

    run = _seed(tmp_path / "r", OLS)
    assert _run(run).exit_code == 0
    forged = runner.invoke(app, ["trace", "save", "--step", "research_type_declared_late",
                                 "--note", "x", "--run", str(run)])
    assert forged.exit_code == 0, forged.output
    assert rt_gate.declared_late(run) is False


@pytest.mark.parametrize("bad", ["Causal", "因果", " causal"])
def test_锚点只记四类里的值_别的一律空串(tmp_path: Path, bad: str) -> None:
    """跟 source 同一条口径：读不准就宁可不比。current_anchor 读的是生 JSON，不过 pydantic。"""
    st = state_mod.reset_anchor(tmp_path, direction="d", idea_id="i", plan_sha="", research_type=bad)
    assert st.anchor["research_type"] == ""
    (tmp_path / "plan.json").write_text(json.dumps({"idea_id": "i", "research_type": bad}), encoding="utf-8")
    assert state_mod.current_anchor(tmp_path)["research_type"] == ""


def test_老锚点没有这一格_再跑也不补(tmp_path: Path) -> None:
    """缺席 = 这一栏出现之前就冻结的老锚点；再跑 oss run 也不许悄悄补上——合规规则靠它判「看不出来」。"""
    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"})
    assert _run(run).exit_code == 0
    st = state_mod.load(run)
    st.anchor.pop("research_type")
    state_mod.save(run, st)
    assert _run(run).exit_code == 0
    assert "research_type" not in state_mod.load(run).anchor


# ═══════════ 三、研究类型门：锁定、只许降级


def _trace_row(run: Path, **row) -> None:
    """往留痕里补一行（审计判词与穷尽留痕，照真跑的形状）。"""
    lines = [x for x in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    seq = max(int(json.loads(x).get("seq", 0)) for x in lines) + 1
    with (run / "trace.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"seq": seq, "outcome": "ok", **row}, ensure_ascii=False) + "\n")


def _steps(run: Path) -> list[str]:
    return [json.loads(x).get("step") for x in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()]


def test_没有理由从因果降为描述_拒跑(tmp_path: Path) -> None:
    """回归没有平行趋势诊断、也没有审计 FAIL——降级没有任何机械理由。"""
    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"})
    assert _run(run).exit_code == 0
    _plan_patch(run, research_type="descriptive")
    r = _run(run)
    assert r.exit_code == 6, r.output
    assert "结果不显著不是降级的理由" in r.output


def test_审计FAIL且穷尽后可以降为描述_留痕只记一次(tmp_path: Path) -> None:
    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"})
    assert _run(run).exit_code == 0
    _trace_row(run, step="review", command="oss review --identification",
               artifacts=["review_plan.json"], note="FAIL by deepseek-flash")
    _trace_row(run, step="design_alternatives_exhausted", command="oss trace save",
               note="换窗口、换对照组都试过，都不可行")
    _plan_patch(run, research_type="descriptive")
    assert _run(run).exit_code == 0
    assert _run(run).exit_code == 0
    assert _steps(run).count("research_type_downgrade") == 1


def test_平行趋势不成立时可以降为描述(tmp_path: Path) -> None:
    """夹具 did_pretrend 上 `did` 的诊断判「平行趋势：**不成立**」——那份数据处理前真的在分化。

    **原先用的是 did_clean。**那份是干净的，判「不成立」是逐（批 × 期）格子联合检验的误报
    （2026-09-17 修）。拿误报当降级的入场券，修好之后这条就没得考了——换成真有前趋势的那份。
    """
    from oss.core import staggered

    if not staggered.available():
        pytest.skip("没装 StatsPAI：这条夹具上的平行趋势判词只在装了它的环境里核过")
    did = {"plan_id": "p001", "idea_id": "edu_001", "estimator": "did", "outcome": "y",
           "unit": "unit", "time": "year", "treat_time": "treat_time", "cluster_level": "unit",
           "estimand": EST}
    run = _seed(tmp_path / "r", {**did, "research_type": "causal"}, data="did_pretrend.csv")
    assert _run(run).exit_code == 0
    _plan_patch(run, research_type="descriptive")
    r = _run(run)
    assert r.exit_code == 0, r.output
    assert "research_type_downgrade" in _steps(run)


def test_合规降级之后换设计照样能跑(tmp_path: Path) -> None:
    """降级那一刻的理由已经留痕：之后换成回归（结果里没有平行趋势诊断了）不许再被当成「无理由降级」拦下。"""
    from oss.core import staggered

    if not staggered.available():
        pytest.skip("没装 StatsPAI：这条夹具上的平行趋势判词只在装了它的环境里核过")
    did = {"plan_id": "p001", "idea_id": "edu_001", "estimator": "did", "outcome": "y",
           "unit": "unit", "time": "year", "treat_time": "treat_time", "cluster_level": "unit",
           "estimand": EST}
    run = _seed(tmp_path / "r", {**did, "research_type": "causal"}, data="did_pretrend.csv")
    assert _run(run).exit_code == 0
    _plan_patch(run, research_type="descriptive")
    assert _run(run).exit_code == 0                     # 平行趋势不成立 → 合规降级，留痕
    _write_plan(run, {**OLS, "research_type": "descriptive"})
    assert _run(run).exit_code == 0                     # 这一跑之后 results.json 里没有平行趋势了
    r = _run(run)
    assert r.exit_code == 0, r.output                   # 靠的是已经记下的降级留痕
    assert _steps(run).count("research_type_downgrade") == 1


def test_描述不许往大改成因果(tmp_path: Path) -> None:
    run = _seed(tmp_path / "r", {**OLS, "research_type": "descriptive"})
    assert _run(run).exit_code == 0
    _plan_patch(run, research_type="causal")
    r = _run(run)
    assert r.exit_code == 6, r.output
    assert "authorized-by user" in r.output


def test_老锚点没有研究类型这一格_不比(tmp_path: Path) -> None:
    run = _seed(tmp_path / "r", OLS)
    assert _run(run).exit_code == 0
    st = state_mod.load(run)
    st.anchor.pop("research_type")
    state_mod.save(run, st)
    _plan_patch(run, research_type="descriptive")
    assert _run(run).exit_code == 0


def test_标成描述却在问因果_计划阶段就拒(tmp_path: Path) -> None:
    run = _seed(tmp_path / "r", {**OLS, "research_type": "descriptive"},
                research_question="互联网普及导致收入差距扩大了吗")
    r = _run(run)
    assert r.exit_code == 1, r.output
    assert "自己就在问因果" in r.output


@pytest.mark.parametrize("q", ["互联网普及是否导致收入差距扩大", "Whether internet diffusion causes wider income gaps"])
def test_标成描述却在问因果_问句也拦(tmp_path: Path, q: str) -> None:
    run = _seed(tmp_path / "r", {**OLS, "research_type": "descriptive"}, research_question=q)
    r = _run(run)
    assert r.exit_code == 1, r.output
    assert "自己就在问因果" in r.output


# ═══════════ 四、研究类型门：三个真旁路（代码审查抓出，2026-09-13）


def test_伪造的降级留痕不认(tmp_path: Path) -> None:
    """C1：`oss trace save` 写的行 `command` 是空串、`args` 是空字典——
    伪造不出门自己那种「合规降级」的形状，不能拿来顶替真理由。"""
    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"})
    assert _run(run).exit_code == 0
    _plan_patch(run, research_type="descriptive")
    r = _run(run)
    assert r.exit_code == 6, r.output                     # 没理由，先确认真的被拦
    forged = runner.invoke(app, ["trace", "save", "--step", "research_type_downgrade",
                                 "--note", "x", "--run", str(run)])
    assert forged.exit_code == 0, forged.output
    r = _run(run)
    assert r.exit_code == 6, r.output                      # 伪造的那一行不算数，照样拦


def test_授权换题之后旧题的降级理由不能借给新题(tmp_path: Path) -> None:
    """I1：`oss anchor switch` 授权换到另一道题之后，旧题的审计 FAIL、穷尽留痕、
    已经记过的降级，都只在旧题名下算数——新题要降级得自己攒出机械理由。"""
    from oss.api import anchor as anchor_api

    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"},
                extra_ideas=({"idea_id": "edu_002", "title": "T2", "research_question": "Q2"},))
    assert _run(run).exit_code == 0
    _trace_row(run, step="review", command="oss review --identification",
               artifacts=["review_plan.json"], note="FAIL by x")
    _trace_row(run, step="design_alternatives_exhausted", command="oss trace save",
               note="换窗口、换对照组都试过，都不可行")
    _plan_patch(run, research_type="descriptive")
    assert _run(run).exit_code == 0                        # edu_001：合规降级，留痕
    _plan_patch(run, idea_id="edu_002", research_type="causal")
    anchor_api.switch(str(run), to="edu_002", why="用户要换", authorized_by="user")
    assert _run(run).exit_code == 0                        # edu_002 第一次跑出结果，类型补回 causal
    _plan_patch(run, research_type="descriptive")
    r = _run(run)
    assert r.exit_code == 6, r.output                       # edu_002 自己从没审计过 FAIL，不许借用 edu_001 的理由
    assert "结果不显著不是降级的理由" in r.output


def test_冻结的类型不许删成空_描述(tmp_path: Path) -> None:
    """I2：冻结之后把 research_type 删掉（或写回空串）不算「没声明」，算「想撤」——一样拒绝。"""
    run = _seed(tmp_path / "r", {**OLS, "research_type": "descriptive"})
    assert _run(run).exit_code == 0
    _plan_patch(run, research_type="")
    r = _run(run)
    assert r.exit_code == 6, r.output
    assert "冻结后不许撤回类型" in r.output and "descriptive" in r.output


def test_冻结的类型不许删成空_因果(tmp_path: Path) -> None:
    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"})
    assert _run(run).exit_code == 0
    _plan_patch(run, research_type="")
    r = _run(run)
    assert r.exit_code == 6, r.output
    assert "冻结后不许撤回类型" in r.output and "causal" in r.output


def test_平移不许_测量到描述(tmp_path: Path) -> None:
    """M7：往大改（描述 → 因果）之外，平移（其它两类互换）也不许——同一族拒绝。"""
    run = _seed(tmp_path / "r", {**OLS, "research_type": "measurement", "measurement": MEAS})
    assert _run(run).exit_code == 0
    _plan_patch(run, research_type="descriptive")
    r = _run(run)
    assert r.exit_code == 6, r.output
    assert "authorized-by user" in r.output


def test_第一次写明类型那次就锁定_并记为看过结果才补(tmp_path: Path) -> None:
    """用户 2026-09-14 决定 2A：锚点冻结那一刻计划没声明类型（这里是 OLS 没写 research_type），
    冻的就是空串；第一次有计划声明了类型的那次跑，就把这一格锁死——同时留痕
    `research_type_declared_late`：类型是在看过结果之后才定的，`oss eval` 的
    research-type-declared 规则见到这一行就判不过。锁定之后就走「只许因果降描述」的老规矩。"""
    run = _seed(tmp_path / "r", OLS)
    assert _run(run).exit_code == 0
    assert state_mod.load(run).anchor["research_type"] == ""
    _plan_patch(run, research_type="descriptive")
    assert _run(run).exit_code == 0
    assert state_mod.load(run).anchor["research_type"] == "descriptive"
    late = [r for r in _steps(run) if r == "research_type_declared_late"]
    assert len(late) == 1
    rows = [json.loads(x) for x in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    late_rows = [r for r in rows if r["step"] == "research_type_declared_late"]
    assert late_rows[0]["command"] == "oss run"
    assert late_rows[0]["args"].get("research_type") == "descriptive"
    _plan_patch(run, research_type="causal")
    r = _run(run)
    assert r.exit_code == 6, r.output


# ═══════════ 五、研究类型门：C1 的近亲、序号读不懂、复审补测（2026-09-13 二次复审）


def test_换题后伪造results行不能复活旧题的平行趋势(tmp_path: Path) -> None:
    """R2：`oss trace save --step results` 写得出同名的 `step`，但 `command` 是空串、
    `artifacts` 是空列表——不是 `emit()` 落盘 `results.json` 时写的那种行，
    不能拿来冒充「新题真跑过一次」，复活盘上残留的（这里是手改的）平行趋势诊断。"""
    from oss.api import anchor as anchor_api

    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"},
                extra_ideas=({"idea_id": "edu_002", "title": "T2", "research_question": "Q2"},))
    assert _run(run).exit_code == 0
    res = json.loads((run / "results.json").read_text(encoding="utf-8"))
    res.setdefault("diagnostics", {})["平行趋势"] = "**不成立**：手改的，冒充旧题的诊断"
    (run / "results.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    _write_plan(run, {**OLS, "idea_id": "edu_002", "research_type": "causal"})
    anchor_api.switch(str(run), to="edu_002", why="用户要换", authorized_by="user")
    _plan_patch(run, research_type="descriptive")
    r = _run(run)
    assert r.exit_code == 6, r.output                      # edu_002 还没真跑过一次，没有 grounds
    forged = runner.invoke(app, ["trace", "save", "--step", "results", "--note", "x", "--run", str(run)])
    assert forged.exit_code == 0, forged.output
    r = _run(run)
    assert r.exit_code == 6, r.output                       # 伪造的 results 行不算「新题真跑过」
    assert "结果不显著不是降级的理由" in r.output


def test_留痕序号读不懂不崩(tmp_path: Path) -> None:
    """R7：手改坏的 `seq`（写成字符串）不该让门直接崩掉——读不懂就当这一行不存在，
    门照样按「没有理由」拒绝，而不是抛异常。"""
    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"})
    assert _run(run).exit_code == 0
    with (run / "trace.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"seq": "x", "step": "note", "outcome": "ok"}) + "\n")
    _plan_patch(run, research_type="descriptive")
    r = _run(run)
    assert r.exit_code == 6, r.output


def test_审计FAIL那一行的seq是字符串也不崩(tmp_path: Path) -> None:
    """R9（跟进复审抓出）：`seq` 读得懂（是个数字），但类型是字符串——
    `_seq()` 能把它解析成整数，`_since_last_switch` 却原样把字符串 `seq` 传了下去，
    `identification_gate` 拿它跟别的整数 `seq` 一比就是 `TypeError`（`int` 和 `str` 不能比大小）。
    `_since_last_switch` 现在把留下来的每一行的 `seq` 换回解析出来的整数，两边就总是同一种类型。
    """
    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"})
    assert _run(run).exit_code == 0
    lines = [x for x in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    seq = max(int(json.loads(x).get("seq", 0)) for x in lines) + 1
    with (run / "trace.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"seq": str(seq), "outcome": "ok", "step": "review",
                            "command": "oss review --identification",
                            "artifacts": ["review_plan.json"], "note": "FAIL by x"}) + "\n")
        f.write(json.dumps({"seq": seq + 1, "outcome": "ok", "step": "design_alternatives_exhausted",
                            "command": "oss trace save",
                            "note": "换窗口、换对照组都试过，都不可行"}) + "\n")
    _plan_patch(run, research_type="descriptive")
    r = _run(run)
    assert r.exit_code == 0, r.output
    assert "research_type_downgrade" in _steps(run)
    from oss.core import skillcheck as sc

    rep = sc.evaluate(run)
    crashed = [(i.id, i.detail) for i in rep.items if "规则出错" in i.detail]
    assert not crashed, crashed


def test_未授权换题的拒绝行不当边界(tmp_path: Path) -> None:
    """R1（复审复核：现状本来就对，补测钉住）：未授权的 `anchor switch` 尝试写的是
    `outcome=refused` 的 `topic_switch` 行——不是真的翻篇，不能当边界；
    之前记的审计 FAIL / 穷尽理由照样算数，降级照样放行。"""
    from oss.api import anchor as anchor_api
    from oss.api.errors import Refused

    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"},
                extra_ideas=({"idea_id": "edu_002", "title": "T2", "research_question": "Q2"},))
    assert _run(run).exit_code == 0
    _trace_row(run, step="review", command="oss review --identification",
               artifacts=["review_plan.json"], note="FAIL by x")
    _trace_row(run, step="design_alternatives_exhausted", command="oss trace save",
               note="换窗口、换对照组都试过，都不可行")
    try:
        anchor_api.switch(str(run), to="edu_002", why="model wants", authorized_by="")
        raise AssertionError("未授权换题应当被拒绝")
    except Refused:
        pass
    _plan_patch(run, research_type="descriptive")
    r = _run(run)
    assert r.exit_code == 0, r.output


# ═══════════ 四、老计划：警告，不拦


def test_没声明研究类型要警告_声明了不警告() -> None:
    from oss.core.analysis import validate_plan
    from oss.core.profiler import profile_file

    prof = profile_file(FIX / "did_clean.csv")
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="x")
    _, w = validate_plan(plan, prof)
    assert any("没声明研究类型" in x for x in w), w
    _, w2 = validate_plan(plan.model_copy(update={"research_type": "descriptive"}), prof)
    assert not any("研究类型" in x for x in w2), w2


# ═══════════ 六、`oss paper check` / `oss paper export` 也用这道门（Task 6 复审补测）


def test_paper_export的研究类型门退出码七(tmp_path: Path) -> None:
    """`oss paper export` 接的是它自己那道研究类型门——冻结之后只改 plan.json、
    不再跑 `oss run`（那道门会先拦成 6），退出码该是 7，两侧一致。"""
    run = _seed(tmp_path / "r", {**OLS, "research_type": "descriptive"})
    assert _run(run).exit_code == 0
    (run / "paper.md").write_text(
        "# T\n\n## 摘要\n效应 {{result:main.coef}}（n = {{result:main.n}}）。\n\n"
        "## 结果\n*表 1. 主结果。*\n{{table:main}}\n", encoding="utf-8")
    (run / "paper_rendered.md").write_text("# T\n", encoding="utf-8")
    _plan_patch(run, research_type="measurement")
    prose = runner.invoke(app, ["paper", "export", "--run", str(run)])
    js = runner.invoke(app, ["paper", "export", "--run", str(run), "--json"])
    assert prose.exit_code == js.exit_code == 7, prose.output
    data = json.loads([ln for ln in js.stdout.splitlines() if ln.strip()][0])
    assert data["problems"][0].startswith("研究类型检测")


def test_paper_check记降级只记一次(tmp_path: Path) -> None:
    """`oss run` 与 `oss paper check` 谁先看见这次合规降级都只记一次——两处共用
    `already_recorded` 的判据（`oss/gates/research_type.py`）。"""
    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"})
    assert _run(run).exit_code == 0
    _trace_row(run, step="review", command="oss review --identification",
               artifacts=["review_plan.json"], note="FAIL by deepseek-flash")
    _trace_row(run, step="design_alternatives_exhausted", command="oss trace save",
               note="换窗口、换对照组都试过，都不可行")
    _plan_patch(run, research_type="descriptive")
    (run / "paper.md").write_text(
        "# T\n\n## 摘要\n效应 {{result:main.coef}}（n = {{result:main.n}}）。\n\n"
        "## 引言\n背景。\n\n## 数据与方法\n方法。\n\n## 结果\n*表 1. 主结果。*\n{{table:main}}\n\n"
        "## 讨论\n### 局限\n识别策略有边界。\n", encoding="utf-8")
    runner.invoke(app, ["paper", "check", "--run", str(run)])
    runner.invoke(app, ["paper", "check", "--run", str(run)])
    rows = [json.loads(x) for x in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    downgrade_rows = [r for r in rows if r["step"] == "research_type_downgrade"]
    assert len(downgrade_rows) == 1 and downgrade_rows[0]["command"] == "oss paper check"


# ═══════════ 七、写了类型不重跑锁不上（复审 2026-09-14 追加，用户 2A 的后续修复）


def test_写了类型不重跑照样躲不过_合规检查审计论文检查与导出(tmp_path: Path) -> None:
    """复审抓出的关键漏洞：光在 `plan.json` 上补研究类型、从不重跑 `oss run`，锚点这一格
    还留着空——`research_type.check()` 见「不比」直接放行，看起来跟真声明过一样，
    `oss eval` 会把 research-type-declared 判过、把因果设计的 identification-audit
    判成不适用。`declared_unlocked` 抓的就是「写了没锁」这个中间状态：`oss eval`、
    识别审计（经 `RunView.is_causal`）、`oss paper check` / `export` 都不该被它骗过；
    重跑一次 `oss run` 才是唯一能真的锁定的路。
    """
    from oss.api import paper as paper_api
    from oss.api.errors import Refused
    from oss.core import skillcheck as sc
    from oss.gates import research_type as rt_gate

    run = _seed(tmp_path / "r", OLS_TREAT)            # 未声明类型，估计量长得像因果设计
    assert _run(run).exit_code == 0
    _plan_patch(run, research_type="descriptive")      # 只改计划，不重跑

    a = state_mod.load(run).anchor
    assert a["research_type"] == "" and a["plan_sha"]           # 锚点这一格还空着
    assert rt_gate.check(run, "descriptive") == ([], "")        # check() 见「不比」，什么都不拦

    rep = sc.evaluate(run)
    decl = next(i for i in rep.items if i.id == "research-type-declared")
    assert decl.status == sc.FAIL, decl.detail
    assert "重跑" in decl.detail
    # 照着说的重跑一次，这一条从此永远判不过——三处都要先说清（重放验收 2026-09-14）
    assert "合规检查会一直判不过" in decl.detail, decl.detail
    audit = next(i for i in rep.items if i.id == "identification-audit")
    assert audit.status == sc.FAIL, audit.detail   # 估计量长得像因果，没锁定的「描述」标签压不住

    (run / "paper.md").write_text(
        "# T\n\n## 摘要\n效应 {{result:main.coef}}（n = {{result:main.n}}）。\n\n"
        "## 引言\n背景。\n\n## 数据与方法\n方法。\n\n## 结果\n*表 1. 主结果。*\n{{table:main}}\n\n"
        "## 讨论\n### 局限\n识别策略有边界。\n", encoding="utf-8")
    with pytest.raises(Refused) as exc:
        paper_api.check(str(run))
    assert any("先重跑一次 oss run" in p and "合规检查会一直判不过" in p for p in exc.value.problems), \
        exc.value.problems

    (run / "paper_rendered.md").write_text("# T\n", encoding="utf-8")
    with pytest.raises(Refused) as exc:
        paper_api.export(str(run))
    assert exc.value.code == 7
    assert any("先重跑一次 oss run" in p and "合规检查会一直判不过" in p for p in exc.value.problems), \
        exc.value.problems

    # 重跑一次：这才是真的锁定，并留一行「看过结果才补」
    assert _run(run).exit_code == 0
    a = state_mod.load(run).anchor
    assert a["research_type"] == "descriptive"
    assert _steps(run).count("research_type_declared_late") == 1


def test_idea不匹配时不补也不留痕(tmp_path: Path) -> None:
    """`refill_plan_sha` 的 idea_id 匹配门槛跟「late」判据现在是同一次计算——idea 对不上、
    什么都没补时不该留一行「从这一次起锁定」（复审抓出：曾经在调用方另算一遍，
    两处判据漂开后，idea 不匹配也照样写、还一次跑一行地累加）。"""
    run = _seed(tmp_path / "r", OLS,
                extra_ideas=({"idea_id": "edu_002", "title": "T2", "research_question": "Q2"},))
    assert _run(run).exit_code == 0
    _plan_patch(run, idea_id="edu_002", research_type="descriptive")
    assert _run(run).exit_code == 0
    assert _run(run).exit_code == 0
    a = state_mod.load(run).anchor
    assert a["idea_id"] == "edu_001" and a["research_type"] == ""
    assert _steps(run).count("research_type_declared_late") == 0
    _plan_patch(run, idea_id="edu_001")                 # 改回匹配的 idea，这才该真的补上
    assert _run(run).exit_code == 0
    a = state_mod.load(run).anchor
    assert a["research_type"] == "descriptive"
    assert _steps(run).count("research_type_declared_late") == 1


def test_同题换题不抹掉看过结果才补的记录(tmp_path: Path) -> None:
    """`oss anchor switch --to <当前 idea_id>` 是说明书教的重定类型的正规做法——题目
    没换，不该被 `declared_late` 当成「翻篇」而把之前记的「看过结果才补」洗掉。"""
    from oss.api import anchor as anchor_api
    from oss.gates import research_type as rt_gate

    run = _seed(tmp_path / "r", OLS)
    assert _run(run).exit_code == 0
    _plan_patch(run, research_type="descriptive")
    assert _run(run).exit_code == 0
    assert rt_gate.declared_late(run) is True
    anchor_api.switch(str(run), to="edu_001", why="重定类型", authorized_by="user")
    assert rt_gate.declared_late(run) is True


# ═══════════ 五、说明书


def _t(*parts: str) -> str:
    return SK.joinpath(*parts).read_text(encoding="utf-8")


def test_说明书写了研究类型的来龙去脉() -> None:
    router = _t("social-research", "SKILL.md")
    assert "[--- type: 因果|描述|测量|仿真]" in router
    assert "checkpoint: idea,ideas,design,none" in router, "旧钉子不许碰"
    assert "- **type** = 可选，研究类型" in _t("social-research", "rules", "input-and-rundir.md")
    plan5 = _t("social-research", "stages", "5-plan.md")
    assert "`plan.json` 写了 `research_type`" in plan5
    assert "**因果不进 `also`**" in plan5
    assert "先改 `plan.json` 的 `research_type`" in plan5
    id75 = _t("social-research", "stages", "7.5-identification.md")
    assert "`research_type` 是 `causal`" in id75
    assert "sim_state.json" in id75
    stop = _t("social-research", "rules", "stop-conditions.md")
    assert "降级为关联" in stop and "把 `research_type` 从 `causal` 改成 `descriptive`" in stop
    assert "**`research_type`**" in _t("social-idea", "reference", "lenses.md")
    assert "| `research_type` |" in _t("social-idea", "reference", "tidy-mode.md")
    assert "## 学科 × 研究类型的门槛" in _t("social-idea", "reference", "discipline-profiles.md")
    assert "causal_flags.json" in _t("social-review", "reference", "blind-discipline.md")
    assert "`oss paper export` 退出码 8" in _t("social-paper", "contract.md")
    assert "七项检测" in (SK.parent.parent / "docs" / "user" / "mcp.md").read_text(encoding="utf-8")
    # 真跑抓出的坑：陪审团点名了两道没选中去做的候选，把停下来问用户的规矩用错了地方——
    # 停不停要看被点名的题是不是选中去做的那道
    assert "被点名的题要是选中去做的那道" in _t("social-idea", "reference", "jury.md")


# ═══════════ 九、Task 11c：识别审计穷尽却仍标因果——按已降级查措辞（用户 2026-09-14 决定 4A）


def test_forced_downgrade_审计FAIL且穷尽后为真(tmp_path: Path) -> None:
    from oss.gates import research_type as rt_gate

    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"})
    assert _run(run).exit_code == 0
    assert rt_gate.forced_downgrade(run) is False           # 还没审计过
    _trace_row(run, step="review", command="oss review --identification",
               artifacts=["review_plan.json"], note="FAIL by x")
    _trace_row(run, step="design_alternatives_exhausted", command="oss trace save",
               note="换窗口、换对照组都试过，都不可行")
    assert rt_gate.forced_downgrade(run) is True             # plan.json 没跟着改，照样为真


def test_forced_downgrade_只有平行趋势不成立不算(tmp_path: Path) -> None:
    """4A 明说：平行趋势不成立不是这里要拦的停止条件，只有「审计 FAIL 且替代设计穷尽」才算。"""
    from oss.gates import research_type as rt_gate

    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"})
    assert _run(run).exit_code == 0
    (run / "results.json").write_text(json.dumps(
        {**json.loads((run / "results.json").read_text(encoding="utf-8")),
         "diagnostics": {"平行趋势": "**不成立**：预趋势不平行"}}, ensure_ascii=False), encoding="utf-8")
    assert rt_gate.forced_downgrade(run) is False


def test_forced_downgrade_换题之后不借旧题的(tmp_path: Path) -> None:
    """跟 `downgrade_grounds` 同一个口径（`_since_last_switch`）：授权换题之后，
    旧题的审计 FAIL、穷尽留痕不该被新题借用。"""
    from oss.api import anchor as anchor_api
    from oss.gates import research_type as rt_gate

    run = _seed(tmp_path / "r", {**OLS, "research_type": "causal"},
                extra_ideas=({"idea_id": "edu_002", "title": "T2", "research_question": "Q2"},))
    assert _run(run).exit_code == 0
    _trace_row(run, step="review", command="oss review --identification",
               artifacts=["review_plan.json"], note="FAIL by x")
    _trace_row(run, step="design_alternatives_exhausted", command="oss trace save",
               note="换窗口、换对照组都试过，都不可行")
    assert rt_gate.forced_downgrade(run) is True
    _plan_patch(run, idea_id="edu_002")                     # 题目仍标 causal，只换 idea
    anchor_api.switch(str(run), to="edu_002", why="用户要换", authorized_by="user")
    assert rt_gate.forced_downgrade(run) is False            # 新题自己从没审计过 FAIL
