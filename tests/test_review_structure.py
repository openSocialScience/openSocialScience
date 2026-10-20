"""审稿的结构：桌审 → 三位互盲审稿人（识别 / 替代解释 / 局限）→ 编辑综合。

结构借自 AERS 的 referee-sim；执行仍是我们的契约——每一位都是别家模型的全新线程。
这里测：三份提示词各盯各的事；桌审和综合不算审稿轮次里的一位；命令行把文件送对、存对。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oss.host.reviewer import _PROMPTS, ReviewVerdict

runner = CliRunner()


# ══════════════════════════════ 一、三份提示词各盯各的事


def test_桌审提示词是编辑十分钟的清单() -> None:
    p = _PROMPTS["desk"]
    for must in ("桌审", "十分钟", "有表吗", "研究过程", "摘要有具体数字", "贡献一句话"):
        assert must in p, must
    assert "VERDICT:" in p and "[设计]" in p


def test_替代解释提示词要求四段并对着成员名单看() -> None:
    p = _PROMPTS["alternatives"]
    for must in ("还能被什么解释", "成员名单", "同期发生了什么", "测量在变吗", "反过来行不行",
                 "故事", "形状", "怎么排除", "现有结果排除了没有", "sample_filter"):
        assert must in p, must
    assert "不要为了显得认真而编一个" in p, "想不出替代解释要判 PASS，不许编"


def test_编辑综合提示词不另起炉灶() -> None:
    p = _PROMPTS["editor"]
    for must in ("不是第四位审稿人", "去重", "排序", "裁分歧", "核对稿子", "必须改", "应该改"):
        assert must in p, must


def test_陪审团提示词要判研究类型标得对不对() -> None:
    """设计稿 4.1：模型出题时给每个候选标类型，陪审团判标得对不对（Type-B）。"""
    p = _PROMPTS["ideas"]
    assert "六件事" in p and "research_type" in p          # 描述骨架 2026-09-15 加第 6 条（描述类新在哪）后由五变六
    assert "想下什么结论" in p and "躲识别审计" in p


# ══════════════════════════════ 二、桌审和综合不算审稿人


def _review_file(d: Path, name: str, verdict: str, findings: list[str], round_: int = 1) -> None:
    (d / name).write_text(json.dumps({"verdict": verdict, "findings": findings, "round": round_},
                                     ensure_ascii=False), encoding="utf-8")


def test_桌审与编辑综合不进轮次统计(tmp_path: Path) -> None:
    from oss.core import loop as loop_mod

    _review_file(tmp_path, "review_desk.json", "FAIL", ["[文字] 没有表"])
    _review_file(tmp_path, "review_editor.json", "FAIL", ["[设计] 必须改"])
    assert loop_mod.loop_status(tmp_path).status == "no_reviews", "桌审、综合都不是审稿人"
    _review_file(tmp_path, "review_paper_rendered_识别.json", "WARN", ["[文字] 措辞"])
    _review_file(tmp_path, "review_paper_rendered_替代解释.json", "WARN", ["[设计] 剔除某批"])
    st = loop_mod.loop_status(tmp_path)
    assert st.rounds[-1].n_reviews == 2 and st.status == "converged"


# ══════════════════════════════ 三、命令行：送对文件、存对名字


class _Fake:
    """假审稿后端：记下收到什么，返回一个固定判词。"""

    calls: list[dict] = []

    def __init__(self, *a, **k) -> None:
        pass

    def review(self, files, level="normal", kind="generic", emphasis=""):
        _Fake.calls.append({"files": [Path(f).name for f in files], "level": level,
                            "kind": kind, "emphasis": emphasis})
        v = ReviewVerdict(verdict="WARN", reason="ok", findings=["[文字] x"])
        v.reviewer_model, v.executor_model = "deepseek-v4-flash", "claude-opus-4"
        v.reviewer_family, v.executor_family = "deepseek", "anthropic"
        return v


@pytest.fixture
def rundir(tmp_path: Path, monkeypatch) -> Path:
    import oss.cli.main as cli

    monkeypatch.setattr(cli, "Reviewer", _Fake)
    _Fake.calls.clear()
    (tmp_path / "paper_rendered.md").write_text("# T\n\n## Abstract\nx", encoding="utf-8")
    (tmp_path / "plan.json").write_text(json.dumps({"plan_id": "p", "idea_id": "i", "estimator": "ols_fe",
                                                   "outcome": "y", "treatment": "x"}), encoding="utf-8")
    (tmp_path / "results.json").write_text(json.dumps({"plan_id": "p", "n": 10, "estimates": {}}), encoding="utf-8")
    return tmp_path


def test_桌审只送成稿_quick档_存成review_desk(rundir: Path) -> None:
    from oss.cli.main import app

    r = runner.invoke(app, ["review", "--desk", "--run", str(rundir)])
    assert r.exit_code == 0, r.output
    call = _Fake.calls[-1]
    assert call["kind"] == "desk" and call["level"] == "quick"
    assert call["files"] == ["paper_rendered.md"]
    assert (rundir / "review_desk.json").exists()


def test_替代解释审稿人带计划与结果_有缺省侧重(rundir: Path) -> None:
    from oss.cli.main import app

    r = runner.invoke(app, ["review", "--alternatives", "--run", str(rundir), "--round", "1"])
    assert r.exit_code == 0, r.output
    call = _Fake.calls[-1]
    assert call["kind"] == "alternatives"
    assert "paper_rendered.md" in call["files"] and "plan.json" in call["files"]
    assert "替代解释" in call["emphasis"]
    out = [p.name for p in rundir.glob("review_paper_rendered_*.json")]
    assert out and "替代解释" in out[0], out


def _set_research_type(rundir: Path, rtype: str) -> None:
    p = json.loads((rundir / "plan.json").read_text(encoding="utf-8"))
    p["research_type"] = rtype
    (rundir / "plan.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")


def test_非因果论文的措辞清单三位审稿人都拿到(rundir: Path) -> None:
    """只给识别审稿人就破了「同一份不可变材料 + 自己的侧重」——三位都拿同一份。"""
    from oss.cli.main import app

    _set_research_type(rundir, "descriptive")
    (rundir / "paper.md").write_text("教育的影响在城市更明显。", encoding="utf-8")
    for argv in (["review", "--paper", "--emphasis", "识别策略", "--run", str(rundir), "--round", "1"],
                 ["review", "--alternatives", "--run", str(rundir), "--round", "1"]):
        r = runner.invoke(app, argv)
        assert r.exit_code == 0, r.output
        assert "causal_flags.json" in _Fake.calls[-1]["files"], _Fake.calls[-1]


def test_审计穷尽却仍标因果的论文_措辞清单也送审稿人(rundir: Path) -> None:
    """Task 11c、用户 2026-09-14 决定 4A：`plan.json` 没跟着从 causal 改成 descriptive，
    但审计判 FAIL、替代设计也穷尽了——`review --paper` 一样按已降级附上措辞清单，
    判据跟 `oss paper check` / `export` 同一处（`wording_scope`）。"""
    from oss.cli.main import app

    _set_research_type(rundir, "causal")
    with (rundir / "trace.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"seq": 1, "outcome": "ok", "step": "review",
                            "command": "oss review --identification",
                            "artifacts": ["review_plan.json"], "note": "FAIL by x"}) + "\n")
        f.write(json.dumps({"seq": 2, "outcome": "ok", "step": "design_alternatives_exhausted",
                            "command": "oss trace save",
                            "note": "换窗口、换对照组都试过，都不可行"}) + "\n")
    (rundir / "paper.md").write_text("互联网普及导致收入差距扩大。", encoding="utf-8")
    r = runner.invoke(app, ["review", "--paper", "--run", str(rundir), "--round", "1"])
    assert r.exit_code == 0, r.output
    assert "causal_flags.json" in _Fake.calls[-1]["files"], _Fake.calls[-1]


def test_研究类型换回因果后陈旧的措辞清单不再送审(rundir: Path) -> None:
    """类型从描述换回因果之后，盘上残留的旧 causal_flags.json 不许再交给审稿人——
    那是上一个类型标出的，现在这篇稿子按因果判，不查措辞。"""
    from oss.cli.main import app

    (rundir / "causal_flags.json").write_text(
        json.dumps({"flags": [], "research_type": "descriptive", "n": 0, "how": "x"}), encoding="utf-8")
    _set_research_type(rundir, "causal")
    r = runner.invoke(app, ["review", "--paper", "--run", str(rundir), "--round", "1"])
    assert r.exit_code == 0, r.output
    assert "causal_flags.json" not in _Fake.calls[-1]["files"], _Fake.calls[-1]


def test_审稿材料里的措辞清单按当前paper_md重建(rundir: Path) -> None:
    """审稿材料不读盘上可能过期的 causal_flags.json——每次都按当前 paper.md 重新扫。"""
    from oss.cli.main import app

    _set_research_type(rundir, "descriptive")
    (rundir / "paper.md").write_text("教育的影响很大。", encoding="utf-8")
    r = runner.invoke(app, ["review", "--paper", "--run", str(rundir), "--round", "1"])
    assert r.exit_code == 0, r.output
    n1 = json.loads((rundir / "causal_flags.json").read_text(encoding="utf-8"))["n"]

    (rundir / "paper.md").write_text("教育的影响和作用都很大。", encoding="utf-8")
    r = runner.invoke(app, ["review", "--paper", "--run", str(rundir), "--round", "1"])
    assert r.exit_code == 0, r.output
    n2 = json.loads((rundir / "causal_flags.json").read_text(encoding="utf-8"))["n"]
    assert (n1, n2) == (1, 2), (n1, n2)
    assert "causal_flags.json" in _Fake.calls[-1]["files"]


def test_编辑综合送本轮全部报告_不含桌审与审计(rundir: Path) -> None:
    from oss.cli.main import app

    _review_file(rundir, "review_desk.json", "WARN", ["[文字] a"])
    _review_file(rundir, "review_plan.json", "FAIL", ["[设计] b"])
    _review_file(rundir, "review_paper_rendered_识别.json", "WARN", ["[文字] c"])
    _review_file(rundir, "review_paper_rendered_替代解释.json", "FAIL", ["[设计] d"])
    _review_file(rundir, "review_paper_rendered_识别_r2.json", "WARN", ["[文字] e"], round_=2)
    r = runner.invoke(app, ["review", "--editor", "--round", "1", "--run", str(rundir)])
    assert r.exit_code == 0, r.output
    call = _Fake.calls[-1]
    assert call["kind"] == "editor"
    assert call["files"][0] == "paper_rendered.md"
    assert set(call["files"][1:]) == {"review_paper_rendered_识别.json", "review_paper_rendered_替代解释.json"}
    assert (rundir / "review_editor.json").exists()


def test_没有报告时编辑综合拒绝(rundir: Path) -> None:
    from oss.cli.main import app

    r = runner.invoke(app, ["review", "--editor", "--run", str(rundir)])
    assert r.exit_code == 1 and "没有任何审稿报告" in r.output
