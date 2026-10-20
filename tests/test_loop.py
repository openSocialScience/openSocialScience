"""审稿—修改—复审循环的记账。**七次真跑没有一次闭合过这个循环。**"""

from __future__ import annotations

import json
from pathlib import Path

from oss.core.loop import (
    KIND_DATA,
    KIND_DESIGN,
    KIND_TEXT,
    classify_finding,
    loop_status,
    plan_sha,
)


def _trace(rundir: Path, rows: list[dict]) -> None:
    (rundir / "trace.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _review(rundir: Path, name: str, verdict: str, findings: list[str], round_: int = 1) -> None:
    (rundir / name).write_text(json.dumps(
        {"verdict": verdict, "findings": findings, "round": round_}, ensure_ascii=False), encoding="utf-8")


def _plan(rundir: Path, **over) -> None:
    base = {"plan_id": "p", "idea_id": "idea_1", "estimator": "ols_fe", "outcome": "y",
            "treatment": "x", "controls": [], "fixed_effects": ["u"], "rationale": "r"}
    base.update(over)
    (rundir / "plan.json").write_text(json.dumps(base), encoding="utf-8")


# ─────────────────────────── 分类：没打标按最严重的算


def test_标签识别与不打标就算设计():
    assert classify_finding("[设计] 该换估计量") == KIND_DESIGN
    assert classify_finding("【数据】要补人口") == KIND_DATA
    assert classify_finding("[文字] 措辞过强") == KIND_TEXT
    assert classify_finding("(design) switch estimator") == KIND_DESIGN
    assert classify_finding("没打标的意见") == KIND_DESIGN, "判不了就按最严重的算"


# ─────────────────────────── 计划指纹：改措辞不算换设计


def test_计划指纹只看会改变估计的字段(tmp_path):
    _plan(tmp_path); a = plan_sha(tmp_path / "plan.json")
    _plan(tmp_path, rationale="换个说法"); b = plan_sha(tmp_path / "plan.json")
    _plan(tmp_path, estimator="event_study"); c = plan_sha(tmp_path / "plan.json")
    assert a == b, "改 rationale 不算换设计"
    assert a != c, "换估计量算"


# ─────────────────────────── 收敛条件：「与」不是「或」


def test_无FAIL且覆盖不缩就可交付_设计级意见不要求清零(tmp_path):
    """目标是合格，不是完美：设计级意见写进局限就算处理，不再要求清零。
    原来要求清零、三轮，一次跑 77 分钟花在审稿上。"""
    _plan(tmp_path)
    _review(tmp_path, "review_paper_rendered_a.json", "WARN", ["[文字] 措辞", "[设计] 换聚类"])
    st = loop_status(tmp_path)
    assert st.converged and st.status == "converged"
    assert st.rounds[-1].design_open == 1, "设计级意见照样计数、照样要写进局限"

    # 第 2 轮：有一份 FAIL → 不可交付，要复审
    _review(tmp_path, "review_paper_rendered_a_r2.json", "FAIL", ["[文字] 还是措辞"], round_=2)
    st = loop_status(tmp_path)
    assert st.round == 2 and not st.converged
    assert any("FAIL" in b for b in st.blockers)


def test_两轮都FAIL就是耗尽(tmp_path):
    _plan(tmp_path)
    for r in (1, 2):
        _review(tmp_path, f"review_paper_x_r{r}.json", "FAIL", ["[文字] 说过头"], round_=r)
    st = loop_status(tmp_path)
    assert st.status == "exhausted" and st.exhausted and st.round == 2


def test_没有任何审稿就是no_reviews(tmp_path):
    _plan(tmp_path)
    assert loop_status(tmp_path).status == "no_reviews"


def test_ideas与power的审稿不算论文轮次(tmp_path):
    _plan(tmp_path)
    _review(tmp_path, "review_ideas.json", "WARN", ["[设计] 砍掉"])
    _review(tmp_path, "review_power.json", "FAIL", ["x"])
    assert loop_status(tmp_path).status == "no_reviews"


# ─────────────────────────── 因果审计 FAIL 之后必须试过替代设计


def _fail_then(tmp_path: Path, after: list[dict]) -> None:
    _plan(tmp_path)
    rows = [
        {"seq": 1, "step": "results", "args": {"plan_sha": "aaa"}},
        {"seq": 2, "step": "review", "artifacts": ["review_plan.json"], "note": "FAIL by deepseek"},
        *after,
    ]
    _trace(tmp_path, rows)
    (tmp_path / "review_plan.json").write_text(json.dumps({"verdict": "FAIL", "findings": []}))


def test_FAIL后没换设计就不放行(tmp_path):
    _fail_then(tmp_path, [{"seq": 3, "step": "results", "args": {"plan_sha": "aaa"}}])
    st = loop_status(tmp_path)
    assert st.identification_gate == "fail_untried"
    assert any("替代设计" in b for b in st.blockers)


def test_FAIL后换了设计重跑就放行(tmp_path):
    _fail_then(tmp_path, [{"seq": 3, "step": "results", "args": {"plan_sha": "bbb"}}])
    assert loop_status(tmp_path).identification_gate == "ok"


def test_显式留痕穷尽也放行但记录在案(tmp_path):
    _fail_then(tmp_path, [{"seq": 3, "step": "design_alternatives_exhausted",
                          "note": "数据只有一批处理，无从换对照"}])
    st = loop_status(tmp_path)
    assert st.identification_gate == "exhausted"
    assert "一批处理" in st.identification_note


def test_老运行没留痕但审计文件是FAIL也拦(tmp_path):
    """验收跑就是这种：审计 FAIL 写进了局限，留痕里没有之后的重跑。"""
    _plan(tmp_path)
    (tmp_path / "review_plan.json").write_text(json.dumps({"verdict": "FAIL", "findings": []}))
    assert loop_status(tmp_path).identification_gate == "fail_untried"


def test_状态落盘(tmp_path):
    _plan(tmp_path)
    loop_status(tmp_path)
    d = json.loads((tmp_path / "loop_state.json").read_text(encoding="utf-8"))
    assert d["anchor"]["idea_id"] == "idea_1" and "status" in d


# ─────────────────────────── 第一次真走这个循环之后补的


def test_复审覆盖不全不算收敛(tmp_path):
    """首轮三位审稿人，第二轮只回来一位（其余超时）、设计级 0 → 不能算收敛。
    一场超时风暴就能让收敛变得毫不费力——那不是收敛，是没人在场。"""
    _plan(tmp_path)
    for i in range(3):
        _review(tmp_path, f"review_paper_x{i}.json", "WARN", ["[设计] 换估计量"])
    _review(tmp_path, "review_paper_x0_r2.json", "WARN", ["[文字] 措辞"], round_=2)
    st = loop_status(tmp_path)
    assert not st.converged, "只回来一位不算收敛"
    assert any("1/3" in b for b in st.blockers)

    for i in (1, 2):
        _review(tmp_path, f"review_paper_x{i}_r2.json", "WARN", ["[文字] 措辞"], round_=2)
    assert loop_status(tmp_path).converged


def test_审计报告改名成review_identification也认(tmp_path):
    """验证跑：执行者把另一个目录的 review_plan.json 复制过来、改名 review_identification.json，
    门只认 review_plan* → 判了「none」，论文照常导出。两种名字都认。"""
    _plan(tmp_path)
    _trace(tmp_path, [{"seq": 1, "step": "results", "args": {"plan_sha": "aaa"}}])
    (tmp_path / "review_identification.json").write_text(
        json.dumps({"verdict": "FAIL", "findings": []}), encoding="utf-8")
    assert loop_status(tmp_path).identification_gate == "fail_untried"


# ─────────────────────────── 决定：由程序给，不由散文猜


def test_轮次耗尽时决定是导出而不是再审(tmp_path):
    """事故：状态写着 exhausted，阻塞信息却写"用 --round 3 复审"，模型照做审到第 5 轮。"""
    _plan(tmp_path)
    _review(tmp_path, "review_paper_rendered_a.json", "FAIL", ["[设计] x"], round_=1)
    _review(tmp_path, "review_paper_rendered_a_r2.json", "FAIL", ["[设计] y"], round_=2)
    st = loop_status(tmp_path)
    assert st.status == "exhausted"
    assert st.decision["decision"] == "export_with_open_items"
    assert st.decision["next_round"] is None
    assert st.decision["open_design_items"] == 1
    assert not any("--round 3" in b for b in st.blockers), st.blockers
    assert any("Open questions" in b for b in st.blockers), st.blockers
    saved = json.loads((tmp_path / "loop_state.json").read_text(encoding="utf-8"))
    assert saved["decision"]["decision"] == "export_with_open_items"


def test_未耗尽有FAIL时决定是继续并指明下一轮(tmp_path):
    _plan(tmp_path)
    _review(tmp_path, "review_paper_rendered_a.json", "FAIL", ["[设计] x"], round_=1)
    st = loop_status(tmp_path)
    assert st.status == "in_progress"
    assert st.decision["decision"] == "continue"
    assert st.decision["next_round"] == 2


def test_收敛时决定是停(tmp_path):
    _plan(tmp_path)
    _review(tmp_path, "review_paper_rendered_a.json", "WARN", ["[文字] 措辞"], round_=1)
    st = loop_status(tmp_path)
    assert st.decision["decision"] == "stop"
    assert st.decision["next_round"] is None


def test_没审过时决定是先审(tmp_path):
    _plan(tmp_path)
    st = loop_status(tmp_path)
    assert st.decision["decision"] == "review_first"
    assert st.decision["next_round"] == 1


def test_耗尽且复审缩水时遗留数不能归零也不能叫人重试(tmp_path):
    """三位提了设计级意见，第二轮只回来一位 → 报「遗留 0 条」是假的全清。"""
    _plan(tmp_path)
    for n in "abc":
        _review(tmp_path, f"review_paper_rendered_{n}.json", "WARN", ["[设计] 换估计量"], round_=1)
    _review(tmp_path, "review_paper_rendered_a_r2.json", "WARN", ["[文字] 措辞"], round_=2)
    st = loop_status(tmp_path)
    assert st.status == "exhausted"
    assert st.decision["decision"] == "export_with_open_items"
    assert st.decision["open_design_items"] == 3
    assert "2 位审稿人未复审" in st.decision["reason"]
    assert not any("重试" in b for b in st.blockers), st.blockers
    assert any("不再复审" in b for b in st.blockers), st.blockers


def test_耗尽的阻塞话术里遗留数与decide一致(tmp_path):
    """阻塞信息自己数「最近一轮」，decide() 数「历史最大」——复审缩水时两个数不一样，
    而模型照着阻塞信息写论文。**遗留几条只有一个出处：decide()。**"""
    _plan(tmp_path)
    for n, items in zip("abc", (["[设计] x1", "[设计] x2"], ["[设计] y"], ["[设计] z"])):
        _review(tmp_path, f"review_paper_rendered_{n}.json", "WARN", items, round_=1)
    _review(tmp_path, "review_paper_rendered_a_r2.json", "FAIL", ["[文字] 措辞"], round_=2)
    st = loop_status(tmp_path)
    assert st.status == "exhausted"
    assert st.decision["open_design_items"] == 4
    assert any("遗留的 4 条" in b for b in st.blockers), st.blockers
    assert not any("遗留的 0 条" in b for b in st.blockers), st.blockers


def test_审计FAIL未换设计时决定里带上局限义务(tmp_path):
    _plan(tmp_path)
    _trace(tmp_path, [{"seq": 1, "step": "results", "args": {"plan_sha": "s0"}},
                      {"seq": 2, "step": "review", "artifacts": ["review_plan.json"], "note": "FAIL 审计"}])
    _review(tmp_path, "review_paper_rendered_a.json", "WARN", ["[文字] 措辞"], round_=1)
    st = loop_status(tmp_path)
    assert st.identification_gate == "fail_untried"
    assert st.decision["decision"] == "stop"
    assert "审计" in st.decision["reason"] and "局限" in st.decision["reason"]


# ─────────────────────────── 编辑综合：不算一位审稿人，但不许静音


def test_编辑判FAIL不拦导出但必须出现在决定里(tmp_path):
    """**原先编辑的判词一个字都进不来。**

    十一轮 CGSS 那次真跑：编辑综合判 FAIL、列了「必须改」三条，
    而 `oss loop status` 打出来的是「决定 stop：可导出」——**两句话之间没有任何东西**。

    编辑不算审稿轮次里的一位（它是对三份报告的整理，不是独立意见），这一条是对的；
    但「不算一位」被写成了「排除出全部下游」。编辑是全流程唯一一处给意见排轻重、
    裁审稿人分歧的角色，丢掉它等于丢掉整份综合的价值。

    按本模块的原则——耗尽也可以交付，但**必须大声说出遗留了什么**——
    编辑的判词不拦门，但要跟着决定一起说出来。
    """
    _plan(tmp_path)
    _review(tmp_path, "review_paper_rendered_a.json", "WARN", ["[设计] 加交互项"])
    _review(tmp_path, "review_paper_rendered_b.json", "WARN", ["[文字] 措辞"])
    _review(tmp_path, "review_editor.json", "FAIL",
            ["[设计] 必须改 ｜ 控制变量要与年份交互", "[设计] 必须改 ｜ 阈值要扫一遍",
             "[文字] 应该改 ｜ 摘要口径"])

    st = loop_status(tmp_path)
    assert st.converged, "编辑不是第四位审稿人，不改变收敛判定"
    assert st.decision["decision"] == "stop"
    assert st.editor == {"round": 1, "verdict": "FAIL", "file": "review_editor.json",
                         "n_findings": 3, "must_fix": 2}
    reason = st.decision["reason"]
    assert "编辑综合判 FAIL" in reason
    assert "2 条标了「必须改」" in reason
    assert "尚未解决的问题" in reason, "要说清这些条目的去处"


def test_编辑判PASS就不啰嗦(tmp_path):
    _plan(tmp_path)
    _review(tmp_path, "review_paper_rendered_a.json", "WARN", ["[文字] 措辞"])
    _review(tmp_path, "review_editor.json", "PASS", [])
    st = loop_status(tmp_path)
    assert "编辑综合判" not in st.decision["reason"]


def test_编辑综合不计入审稿轮次(tmp_path):
    """它跟桌审一样，是门和整理，不是一份独立意见。"""
    _plan(tmp_path)
    _review(tmp_path, "review_paper_rendered_a.json", "WARN", ["[文字] 措辞"])
    _review(tmp_path, "review_editor.json", "FAIL", ["[设计] 必须改 ｜ x"])
    _review(tmp_path, "review_desk.json", "FAIL", ["[文字] y"])
    st = loop_status(tmp_path)
    assert st.rounds[-1].n_reviews == 1, "只有一位真审稿人"
    assert st.rounds[-1].verdicts == ["WARN"], "桌审与编辑的判词不进轮次"


def test_多轮时取最近一轮的编辑判词(tmp_path):
    _plan(tmp_path)
    for r in (1, 2):
        _review(tmp_path, f"review_paper_rendered_a{'_r2' if r == 2 else ''}.json",
                "WARN", ["[文字] 措辞"], round_=r)
    _review(tmp_path, "review_editor.json", "FAIL", ["[设计] 必须改 ｜ x"], round_=1)
    _review(tmp_path, "review_editor_r2.json", "WARN", ["[文字] 应该改 ｜ y"], round_=2)
    st = loop_status(tmp_path)
    assert st.editor["round"] == 2 and st.editor["verdict"] == "WARN"
    assert st.editor["must_fix"] == 0
