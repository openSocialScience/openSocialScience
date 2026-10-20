"""第二轮审稿只判「上一轮处置了没有」，不准提新意见当 FAIL 依据。

事故（2026-08-28）：五轮审稿全 FAIL，而每一轮 FAIL 的理由都跟上一轮不同——
作者改一条，审稿人换一条新的提。**这不是收敛，是移动的球门。**
第二轮起把任务钉死：逐条判处置状态；新看到的问题写进「新发现」，不作为 FAIL 依据。
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from oss.cli import main as cli_main
from oss.core import loop as loop_mod
from oss.host.reviewer import FOLLOWUP_HEADING, Reviewer, _PROMPTS, _parse, followup_brief

runner = CliRunner()


def test_第一轮不加任何简报():
    assert followup_brief("paper", 1, ["[设计] x"]) == ""


def test_第二轮逐条判处置(tmp_path):
    b = followup_brief("paper", 2, ["[设计] 平行趋势不成立", "[文字] 措辞过头"])
    assert "第 2 轮" in b
    assert "处置了" in b and "写进局限了" in b and "没交代" in b
    assert "只有「没交代」" in b
    assert "平行趋势不成立" in b and "措辞过头" in b
    assert FOLLOWUP_HEADING in b and "[新发现]" in b


def test_编辑综合第二轮不许拿新发现判FAIL():
    b = followup_brief("editor", 2, ["[设计] x"])
    assert "不作为 FAIL 依据" in b
    assert "只能依据" in b and "没交代" in b


def test_没有上一轮意见时说清楚():
    b = followup_brief("paper", 2, [])
    assert "上一轮没有留下任何意见" in b


def test_桌审审计陪审团不收复审简报():
    """**只有论文类审稿有「上一轮意见」可判。**桌审是送审前的门，
    因果审计与选题陪审是别的环节——给它们一份「逐条判处置」的任务是错的任务。"""
    for k in ("desk", "identification", "ideas", "generic"):
        assert followup_brief(k, 2, ["[设计] x"]) == "", k


def test_简报接在提示词后面而不是替换它(tmp_path):
    f = tmp_path / "paper_rendered.md"
    f.write_text("正文", encoding="utf-8")
    seen = {}

    class Fake(Reviewer):
        def _post(self, system, path, content, budget):
            seen["system"] = system
            return {"choices": [{"message": {"content": "VERDICT: PASS\nREASON: r"}}],
                    "model": "deepseek-v4"}

    r = Fake(base_url="http://x", model="deepseek-v4", api_key="k", executor_model="claude-opus-4")
    r.review(f, kind="paper", round_=2, previous_findings=["[设计] 平行趋势不成立"])
    assert seen["system"].startswith(_PROMPTS["paper"])
    assert "平行趋势不成立" in seen["system"]


def test_第一轮的提示词一个字都没变(tmp_path):
    """老用法不能被这个功能改坏——test_reviewer.py 里那条断言还得成立。"""
    f = tmp_path / "p.md"
    f.write_text("x", encoding="utf-8")
    seen = {}

    class Fake(Reviewer):
        def _post(self, system, path, content, budget):
            seen["system"] = system
            return {"choices": [{"message": {"content": "VERDICT: PASS\nREASON: r"}}],
                    "model": "deepseek-v4"}

    r = Fake(base_url="http://x", model="deepseek-v4", api_key="k", executor_model="claude-opus-4")
    r.review(f, kind="paper")
    assert seen["system"] == _PROMPTS["paper"]


def test_新发现段里漏打标签也认得出来():
    """模型忘了打 `[新发现]` 是常态。**标题之后的都算新发现**，fail closed 到宽的一边。"""
    v = _parse(
        "VERDICT: WARN\nREASON: r\nFINDINGS:\n"
        "- [设计] 1: 没交代——平行趋势那条一个字没提\n"
        f"\n{FOLLOWUP_HEADING}\n"
        "- [新发现] 表 3 的标签是中文\n"
        "- 摘要没有最小可探测效应\n")
    assert v.findings[0].startswith("[设计]")
    assert v.findings[1].startswith("[新发现]")
    assert v.findings[2].startswith("[新发现]"), "标题之后没打标签的也要补上"


# ─────────────────────────── 新发现不进 design_open


def _rev(d: Path, name: str, findings: list[str], round_: int, verdict: str = "WARN") -> None:
    (d / name).write_text(json.dumps({"verdict": verdict, "findings": findings, "round": round_},
                                     ensure_ascii=False), encoding="utf-8")


def test_新发现是第四类():
    assert loop_mod.classify_finding("[新发现] 表 3 的标签是中文") == loop_mod.KIND_NEW
    assert loop_mod.classify_finding("[new] whatever") == loop_mod.KIND_NEW
    assert loop_mod.classify_finding("没打标签的") == loop_mod.KIND_DESIGN


def test_新发现不计入设计级遗留(tmp_path):
    (tmp_path / "plan.json").write_text(json.dumps(
        {"plan_id": "p", "idea_id": "i", "estimator": "ols_fe", "outcome": "y", "treatment": "x"}),
        encoding="utf-8")
    _rev(tmp_path, "review_paper_rendered_a.json", ["[设计] 平行趋势"], 1, "FAIL")
    _rev(tmp_path, "review_paper_rendered_a_r2.json",
         ["[设计] 1: 没交代——平行趋势那条没提", "[新发现] 表 3 标签是中文", "[新发现] 摘要缺 MDE"], 2)
    st = loop_mod.loop_status(tmp_path)
    last = st.rounds[-1]
    assert last.design_open == 1, "新发现不该算进设计级遗留"
    assert last.new_open == 2
    assert st.decision["open_design_items"] == 1


def test_全是新发现时第二轮算收敛(tmp_path):
    (tmp_path / "plan.json").write_text(json.dumps(
        {"plan_id": "p", "idea_id": "i", "estimator": "ols_fe", "outcome": "y", "treatment": "x"}),
        encoding="utf-8")
    _rev(tmp_path, "review_paper_rendered_a.json", ["[设计] x"], 1, "FAIL")
    _rev(tmp_path, "review_paper_rendered_a_r2.json", ["[新发现] 新看到的问题"], 2, "PASS")
    st = loop_mod.loop_status(tmp_path)
    assert st.converged and st.decision["decision"] == "stop"
    assert st.rounds[-1].design_open == 0


def test_cli第二轮把上一轮意见带进提示词(monkeypatch, tmp_path):
    run = tmp_path / "r"; run.mkdir()
    (run / "paper_rendered.md").write_text("正文", encoding="utf-8")
    (run / "plan.json").write_text("{}", encoding="utf-8")
    (run / "results.json").write_text("{}", encoding="utf-8")
    (run / "power.json").write_text("{}", encoding="utf-8")
    (run / "literature.json").write_text('{"records": []}', encoding="utf-8")
    _rev(run, "review_paper_rendered_识别.json", ["[设计] 平行趋势不成立", "[新发现] 旧的新发现"], 1, "FAIL")
    seen = {}

    class FakeReviewer:
        def __init__(self, *a, **k): pass
        def review(self, files, level="normal", kind="generic", emphasis="",
                   round_=1, previous_findings=None):
            from oss.host.reviewer import ReviewVerdict
            seen["round"] = round_
            seen["prev"] = list(previous_findings or [])
            return ReviewVerdict(verdict="PASS", reason="ok", findings=["[设计] 1: 处置了"])

    monkeypatch.setattr(cli_main, "Reviewer", FakeReviewer)
    r = runner.invoke(cli_main.app, ["review", "--paper", "--round", "2", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert seen["round"] == 2
    assert seen["prev"] == ["[设计] 平行趋势不成立"], "上一轮的新发现不算义务，不该带进去"


def test_cli桌审第二轮不带复审简报(monkeypatch, tmp_path):
    """桌审的 --round 只是选择器，不是审稿轮次。**不该有「判处置了没有」这回事。**"""
    run = tmp_path / "r"; run.mkdir()
    (run / "paper_rendered.md").write_text("# T\n\n## Abstract\nx", encoding="utf-8")
    (run / "plan.json").write_text(json.dumps(
        {"plan_id": "p", "idea_id": "i", "estimator": "ols_fe", "outcome": "y", "treatment": "x"}),
        encoding="utf-8")
    _rev(run, "review_paper_rendered_识别.json", ["[设计] 平行趋势不成立"], 1, "FAIL")
    seen = {}

    class FakeReviewer:
        """**老签名**：不认 round_ / previous_findings。
        桌审带上这两个参数就会 TypeError——这正是「非论文类不带新参数」要保证的事。"""

        def __init__(self, *a, **k): pass
        def review(self, files, level="normal", kind="generic", emphasis=""):
            from oss.host.reviewer import ReviewVerdict
            seen["kind"] = kind
            return ReviewVerdict(verdict="WARN", reason="ok", findings=["[文字] x"])

    monkeypatch.setattr(cli_main, "Reviewer", FakeReviewer)
    r = runner.invoke(cli_main.app, ["review", "--desk", "--round", "2", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert seen["kind"] == "desk"
    assert "只判处置了没有" not in r.output, r.output


# ─────────────────────────── 「新发现」段的边界：认得出，也要结束得掉


def test_标题写成小标题或半角括号都认得出():
    """模型不会一字不差地照抄标题。`## ` 前缀、半角括号、没空格，都是同一个段。"""
    for heading in ("## 新发现（不作为 FAIL 依据）",
                    "新发现(不作为FAIL依据)",
                    "**新发现（不作为 FAIL 依据）**",
                    "新发现（不作为 FAIL 依据）："):
        v = _parse("VERDICT: WARN\nREASON: r\nFINDINGS:\n"
                   "- [设计] 1 没交代\n"
                   f"\n{heading}\n"
                   "- 表 3 的标签是中文\n")
        assert v.findings[0].startswith("[设计]"), heading
        assert v.findings[1].startswith("[新发现]"), heading


def test_REASON里提一句不会把整份意见变成新发现():
    """**C1 的事故形态**：`FOLLOWUP_HEADING in line` 会让 REASON 里顺口提一句
    「新发现（不作为 FAIL 依据）」的报告，从那行往下**全部**意见被打成新发现——
    一整份审稿意见凭空作废，循环判收敛。标题必须锚在行首整行匹配。"""
    v = _parse("VERDICT: FAIL\n"
               "REASON: 上一轮三条都没交代，另有新发现（不作为 FAIL 依据）两条\n"
               "FINDINGS:\n"
               "- [设计] 1 没交代——平行趋势那条一个字没提\n"
               "- [设计] 2 没交代\n")
    assert [f[:6] for f in v.findings] == ["[设计] 1", "[设计] 2"], v.findings
    assert not any(f.startswith("[新发现]") for f in v.findings), v.findings


def test_新发现段被下一个标题结束():
    """模型在新发现之后又补一段「本轮必须改」是常态。
    **不结束这一段的话，那几条真义务会被当成新发现放过去。**
    四种结束形态都要认：冒号收尾的引导句、Markdown 标题、整行加粗的小标题、
    `FINDINGS:` 这类全大写字段。"""
    for brk in ("本轮必须改：", "## 其他", "**必须改**", "FINDINGS:"):
        v = _parse("VERDICT: FAIL\nREASON: r\nFINDINGS:\n"
                   f"{FOLLOWUP_HEADING}\n"
                   "- 表 3 的标签是中文\n"
                   f"\n{brk}\n"
                   "- [设计] 1 没交代\n")
        assert v.findings[0].startswith("[新发现]"), brk
        assert v.findings[1].startswith("[设计]"), f"{brk}：段结束后的意见不该被打成新发现"


def test_新发现标题带正文时不开段(tmp_path):
    """`新发现（不作为 FAIL 依据）：无` 是一句陈述，不是段落标题。

    正则少了**行尾锚**的话它照样开段：后面那几条真义务全被补成 `[新发现]`，
    design_open 凭空归零，循环拿着「没有遗留」判收敛——跟 C1 那个事故同一个形态，
    只是触发的写法换了一种。（`## 新发现（…）：` 这种带尾缀的标题仍要认得出来，
    靠的是 `_parse` 先把行尾的 `*：: ` 剥掉。）"""
    v = _parse("VERDICT: FAIL\nREASON: r\nFINDINGS:\n"
               f"{FOLLOWUP_HEADING}：无\n"
               "- [设计] 1 没交代\n"
               "- [设计] 2 没交代——同期政策那条没提\n")
    assert [f[:6] for f in v.findings] == ["[设计] 1", "[设计] 2"], v.findings
    assert not any(f.startswith("[新发现]") for f in v.findings), v.findings

    _plan(tmp_path)
    _rev(tmp_path, "review_paper_rendered_a.json", ["[设计] 平行趋势", "[设计] 同期政策"], 1, "FAIL")
    _rev(tmp_path, "review_paper_rendered_a_r2.json", v.findings, 2, "FAIL")
    last = loop_mod.loop_status(tmp_path).rounds[-1]
    assert last.design_open == 2, "没开段就该照常计入设计级遗留"
    assert last.new_open == 0, last.design_items


def test_条目折行续写不算段结束():
    """**「任何非条目行」当结束是错的**：条目换行续写的第二行也是非条目行。
    按那条规则，一条写长了的新发现就会把整段关掉，后面的新发现全部回落成设计级意见——
    凭空多出几条「遗留」，正是这个功能要防的事。缩进的和顶格的续行都要认。"""
    v = _parse("VERDICT: WARN\nREASON: r\nFINDINGS:\n"
               f"{FOLLOWUP_HEADING}\n"
               "- 表 3 的标签是中文，投出去之前\n"
               "  得换成英文，图 2 同理\n"
               "- 摘要缺最小可探测效应，审稿人\n"
               "多半会问这一条\n"
               "- 附录 A 的页码错了\n")
    assert len(v.findings) == 3, v.findings
    assert all(f.startswith("[新发现]") for f in v.findings), v.findings


def test_分隔线不会变成一条幽灵意见():
    """`---` 也以 `-` 开头。lstrip 完是空串，附上去就是一条没有内容的意见——
    它会被 classify_finding 当成没打标签，于是算作一条设计级遗留。"""
    v = _parse("VERDICT: PASS\nREASON: r\nFINDINGS:\n"
               "- [文字] 真意见\n"
               "---\n"
               "***\n")
    assert v.findings == ["[文字] 真意见"], v.findings


def test_第四种处置和输出格式都钉死了():
    b = followup_brief("paper", 2, ["[设计] x"])
    assert "不同意" in b and "判理由成不成立" in b
    assert "这四种之一" in b
    assert "保留原意见的方括号标签" in b and "[设计] 1 处置了" in b
    assert "不同意" in followup_brief("editor", 2, ["[设计] x"])


# ─────────────────────────── BLOCKED 不是收敛


def _plan(d: Path) -> None:
    (d / "plan.json").write_text(json.dumps(
        {"plan_id": "p", "idea_id": "i", "estimator": "ols_fe", "outcome": "y", "treatment": "x"}),
        encoding="utf-8")


def test_BLOCKED不算收敛(tmp_path):
    """**BLOCKED 是「没法核」，不是「核过了没问题」。**
    当成收敛等于让后端不可用替审稿人签字放行。"""
    _plan(tmp_path)
    _rev(tmp_path, "review_paper_rendered_a.json", ["[文字] x"], 1, "BLOCKED")
    st = loop_mod.loop_status(tmp_path)
    assert not st.converged
    assert st.decision["decision"] == "continue" and st.decision["next_round"] == 2


def test_第二轮BLOCKED不收敛但轮次已耗尽(tmp_path):
    """轮次上限是 2：第 2 轮 BLOCKED 不会变成「再审一轮」，而是带着遗留导出。
    **不收敛**这一点跟第 1 轮一样成立。"""
    _plan(tmp_path)
    _rev(tmp_path, "review_paper_rendered_a.json", ["[文字] x"], 1, "WARN")
    _rev(tmp_path, "review_paper_rendered_a_r2.json", ["[文字] x"], 2, "BLOCKED")
    st = loop_mod.loop_status(tmp_path)
    assert not st.converged and st.exhausted
    assert st.decision["decision"] == "export_with_open_items"


# ─────────────────────────── 上一轮意见：决定信优先、去重、不带新发现


def _paper_run(tmp_path: Path) -> Path:
    run = tmp_path / "r"; run.mkdir()
    (run / "paper_rendered.md").write_text("正文", encoding="utf-8")
    (run / "plan.json").write_text("{}", encoding="utf-8")
    (run / "results.json").write_text("{}", encoding="utf-8")
    (run / "power.json").write_text("{}", encoding="utf-8")
    (run / "literature.json").write_text('{"records": []}', encoding="utf-8")
    return run


def _fake_reviewer(seen: dict):
    class FakeReviewer:
        def __init__(self, *a, **k): pass
        def review(self, files, level="normal", kind="generic", emphasis="",
                   round_=1, previous_findings=None):
            from oss.host.reviewer import ReviewVerdict
            seen["round"] = round_
            seen["prev"] = list(previous_findings or [])
            return ReviewVerdict(verdict="PASS", reason="ok", findings=["[设计] 1 处置了"])
    return FakeReviewer


def test_有编辑决定信时只带决定信(monkeypatch, tmp_path):
    """决定信按构造就是几位意见去重、排序、裁分歧之后的版本。
    再把原始报告并进来，等于把编辑已经裁掉的分歧搬回来。"""
    run = _paper_run(tmp_path)
    _rev(run, "review_paper_rendered_甲.json", ["[设计] 甲说的", "[文字] 甲的小意见"], 1, "FAIL")
    _rev(run, "review_paper_rendered_乙.json", ["[设计] 乙说的"], 1, "FAIL")
    _rev(run, "review_editor.json", ["[设计] 必须改 ｜ 平行趋势（两位提到）"], 1, "FAIL")
    seen = {}
    monkeypatch.setattr(cli_main, "Reviewer", _fake_reviewer(seen))
    r = runner.invoke(cli_main.app, ["review", "--paper", "--round", "2", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert seen["prev"] == ["[设计] 必须改 ｜ 平行趋势（两位提到）"], seen["prev"]
    assert "取自编辑综合的决定信" in r.output


def test_没有决定信时几位提同一条只带一次(monkeypatch, tmp_path):
    """三位互盲审稿人提同一件事是常态。带三遍只会让复审把一条意见判三次。"""
    run = _paper_run(tmp_path)
    _rev(run, "review_paper_rendered_甲.json", ["[设计] 平行趋势不成立"], 1, "FAIL")
    _rev(run, "review_paper_rendered_乙.json", ["[设计]  平行趋势不成立"], 1, "FAIL")
    _rev(run, "review_paper_rendered_丙.json", ["[设计] 平行趋势不成立", "[文字] 只有丙提了"], 1, "FAIL")
    seen = {}
    monkeypatch.setattr(cli_main, "Reviewer", _fake_reviewer(seen))
    r = runner.invoke(cli_main.app, ["review", "--paper", "--round", "2", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert seen["prev"] == ["[设计] 平行趋势不成立", "[文字] 只有丙提了"], seen["prev"]


def test_上一轮全是新发现时等于没有意见(monkeypatch, tmp_path):
    """上一轮标了 [新发现] 的当时就声明了不构成义务。全是新发现 → 这一轮没有义务可判。"""
    run = _paper_run(tmp_path)
    _rev(run, "review_paper_rendered_甲.json", ["[新发现] 表 3 标签", "[新发现] 摘要缺 MDE"], 1, "PASS")
    seen = {}
    monkeypatch.setattr(cli_main, "Reviewer", _fake_reviewer(seen))
    r = runner.invoke(cli_main.app, ["review", "--paper", "--round", "2", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert seen["prev"] == []
    assert "上一轮没有留下任何意见" in followup_brief("paper", 2, seen["prev"])


def test_空决定信要回落到原始报告(monkeypatch, tmp_path):
    """**空的决定信不能把三位审稿人的意见全吃掉。**后端半路挂了、或编辑判 PASS
    什么都没列，都会留下一封 findings 为空的决定信；只看文件在不在就回落不了，
    复审于是拿着一张白纸去判「处置了没有」。"""
    run = _paper_run(tmp_path)
    _rev(run, "review_paper_rendered_a.json", ["[设计] 平行趋势不成立"], 1, "FAIL")
    _rev(run, "review_paper_rendered_b.json", ["[设计]  平行趋势不成立"], 1, "FAIL")
    _rev(run, "review_paper_rendered_c.json", ["[文字] 只有 c 提了"], 1, "FAIL")
    _rev(run, "review_editor.json", [], 1, "FAIL")
    seen = {}
    monkeypatch.setattr(cli_main, "Reviewer", _fake_reviewer(seen))
    r = runner.invoke(cli_main.app, ["review", "--paper", "--round", "2", "--run", str(run)])
    assert r.exit_code == 0, r.output
    assert seen["prev"] == ["[设计] 平行趋势不成立", "[文字] 只有 c 提了"], seen["prev"]
    assert "取自编辑综合的决定信" not in r.output, r.output


# ─────────────────────────── 已判处置的不算遗留


def test_复审判了处置的不算遗留(tmp_path):
    """**已判处置的不是遗留。**照原样计数的话，全部解决的一轮也会报「遗留 3 条」，
    模型照着这个数把已经改好的条目写进 Open questions。"""
    _plan(tmp_path)
    _rev(tmp_path, "review_paper_rendered_a.json",
         ["[设计] 平行趋势", "[设计] 同期政策", "[设计] 对照组"], 1, "FAIL")
    _rev(tmp_path, "review_paper_rendered_a_r2.json",
         ["[设计] 1 处置了 —— 改在 4.2 节", "[设计] 2 写进局限了 —— 局限第三段引了那句",
          "[设计] 3 没交代", "[设计] 4 没交代——作者说处置了但正文找不到"], 2, "FAIL")
    last = loop_mod.loop_status(tmp_path).rounds[-1]
    assert last.resolved == 2
    assert last.design_open == 2, "「没交代——作者说处置了」不许被当成处置了"
    assert not any("处置了 ——" in it for it in last.design_items), last.design_items


def test_不同意的意见留在遗留里(tmp_path):
    """**`不同意` 不是处置词。**这里是 `followup_brief` 与记账之间有意留的一道缝。

    简报给复审四个桶：`处置了` / `写进局限了` / `不同意` / `没交代`，并说明
    「不同意 —— 理由成立就按写进局限了对待」。可是**理由成不成立**是审稿人写在
    破折号后面的散文，工具核不了；`_DISPOSED` 只认前两个词。
    于是 `[设计] 1 不同意 —— 理由成立` 落在 design_open 里，跟没打标签同一条规矩：
    **判不了就按最严重的算。**

    代价是偶尔多报一条遗留——那条会被写进 Open questions，不拦导出。
    反过来放行的代价是：作者只要回一句「不同意」，义务就消失了。
    所以这是有意选的 fail-closed，不是漏判。"""
    _plan(tmp_path)
    _rev(tmp_path, "review_paper_rendered_a.json", ["[设计] 平行趋势", "[设计] 同期政策"], 1, "FAIL")
    _rev(tmp_path, "review_paper_rendered_a_r2.json",
         ["[设计] 1 不同意 —— 理由成立，样本期内没有同期政策",
          "[设计] 2 写进局限了 —— 局限第三段引了那句"], 2, "WARN")
    last = loop_mod.loop_status(tmp_path).rounds[-1]
    assert last.resolved == 1
    assert last.design_open == 1, "「不同意」不是处置——留在遗留里"
    assert any("不同意" in it for it in last.design_items), last.design_items


def test_首轮的意见不按处置词算(tmp_path):
    """首轮的意见还没有人判过处置状态。**第 1 轮长得像判词的行不算处置了**——
    否则作者只要把意见写成 `[设计] 1 处置了` 就能让遗留归零。"""
    _plan(tmp_path)
    _rev(tmp_path, "review_paper_rendered_a.json",
         ["[设计] 1 处置了 —— 看着像判词其实是首轮意见"], 1, "FAIL")
    last = loop_mod.loop_status(tmp_path).rounds[-1]
    assert last.resolved == 0
    assert last.design_open == 1


def test_已处置计数落进loop_state(tmp_path):
    _plan(tmp_path)
    _rev(tmp_path, "review_paper_rendered_a.json", ["[设计] x"], 1, "FAIL")
    _rev(tmp_path, "review_paper_rendered_a_r2.json", ["[设计] 1 处置了 —— 改了"], 2, "PASS")
    loop_mod.loop_status(tmp_path)
    d = json.loads((tmp_path / "loop_state.json").read_text(encoding="utf-8"))
    assert d["rounds"][-1]["resolved"] == 1
    assert d["rounds"][-1]["design_open"] == 0


# ══════════════════════════════ 同一份判词不许同时跑两个


def test_同一份审稿在跑时再起一个要被拒(tmp_path, monkeypatch) -> None:
    """验收跑 r20260907_edufert_zh 的实录：一次 --level deep 的审稿被放进后台，
    执行者分不清它是死了还是还在跑，12 分钟里把同一份审稿起了 4 次——
    **每一次都真调 API、真花钱**，而只有最后一次落了盘。

    这道锁是给钱包的：认的是判词文件名，不是命令行。
    """
    import json as _json
    import os

    from oss.api.errors import Refused
    from oss.api.review import _inflight

    out = tmp_path / "review_paper_rendered_识别.json"
    with _inflight(out, []):
        assert out.with_suffix(".lock").exists(), "跑的时候锁要在"
        # 假装另一个进程也来跑同一份：PID 记成一个确实活着的（当前进程的父进程之外，
        # 用 1 号进程——它在任何 Unix 上都活着，且不是我们自己）
        out.with_suffix(".lock").write_text(
            _json.dumps({"pid": 1, "started": 0}), encoding="utf-8")
        try:
            with _inflight(out, []):
                raise AssertionError("第二个应当被拒")
        except Refused as exc:
            assert exc.code == 3, exc.code
            assert "已经有一个同样的审稿在跑" in str(exc)
            assert any("别再起一个" in p for p in exc.problems)
            assert any("不要放后台" in p for p in exc.problems)
    assert not out.with_suffix(".lock").exists(), "跑完锁要清掉"
    assert os.getpid() > 0


def test_陈旧的锁直接接管不许卡住(tmp_path) -> None:
    """上一次被 Ctrl-C 或被杀，锁留在那儿。**卡住比重复更糟**——重复只是费钱，
    卡住是这条链再也走不下去，而且用户看不出为什么。"""
    import json as _json

    from oss.api.review import _inflight

    out = tmp_path / "review_x.json"
    # 一个几乎不可能存在的 PID
    out.with_suffix(".lock").write_text(
        _json.dumps({"pid": 999_999, "started": 0}), encoding="utf-8")
    log: list = []
    with _inflight(out, log):
        pass
    assert any("陈旧的锁" in row[1] for row in log), log
