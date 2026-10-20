"""交付前的两道门（补洞批 Task 8、设计稿甲 5）：审稿人看的和交出去的必须是同一份。

盘点查实的洞：正常路径下 `.docx` **不是**从 `paper_rendered.md` 转的——`export` 从 `paper.md`
重渲第二份 `paper_pandoc.md` 再交给 pandoc。于是最终交付的那份文字上，`manuscript.check` /
`check_claims` 从头到尾没跑过；改了审稿人看的那一份也没人知道。

这里钉两件事：
- 渲染稿一致性（退出码 9）：`paper_rendered.md` 不是 `oss paper render` 写下的那一份就拒绝导出
- 交付文本复核（退出码 10）：数字回账、因果措辞、过程话、局限的功效与共线

两道门都排在**查 pandoc 之前**，所以这里一律把 pandoc 藏起来跑（CI 就是这样的环境）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from oss.api import errors
from oss.api import paper as paper_api
from oss.api.rundir import require_run
from oss.core import manuscript
from oss.core.contracts import AnalysisPlan, AnalysisResults, Estimate, SpecSummary
from oss.core.findings import code_of


def _paper(body: str = "组间差异见表 1。") -> str:
    """能过既有校验的最小骨架（照 tests/test_causal_wording.py）。"""
    return ("# 标题\n\n## 摘要\n效应 {{result:main.coef}}（n = {{result:main.n}}）。\n\n"
            "## 引言\n背景。\n\n## 数据与方法\n方法。\n\n"
            "## 结果\n*表 1. 主结果。*\n{{table:main}}\n" + body + "\n\n"
            "## 讨论\n### 局限\n识别策略有边界。\n")


def _run(tmp_path: Path, body: str = "组间差异见表 1。") -> Path:
    """一个能一路走到交付前那两道门的运行目录：结果、计划、稿子、三份 PASS 判词。"""
    run = tmp_path / "r"
    run.mkdir(parents=True)
    AnalysisResults(plan_id="p", n=100,
                    estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe")).save(run / "results.json")
    AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="x",
                 research_type="descriptive").save(run / "plan.json")
    (run / "paper.md").write_text(_paper(body), encoding="utf-8")
    for who in ("a", "b", "c"):                      # 凑够审稿门（退出码 5/3）
        (run / f"review_paper_rendered_{who}.json").write_text(
            json.dumps({"verdict": "PASS", "findings": [], "round": 1}), encoding="utf-8")
    return run


def _fresh(run: Path) -> str:
    """此刻从 paper.md 重渲的结果（不写盘、不留痕）——手写渲染稿的测试拿它当底稿。"""
    return manuscript.render((run / "paper.md").read_text(encoding="utf-8"),
                             AnalysisResults.load(run / "results.json"), None, None, figures=set())


@pytest.fixture
def no_pandoc(monkeypatch: pytest.MonkeyPatch) -> None:
    """照 CI 的样子：只让 pandoc 找不到，别的命令照常找得到。"""
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **kw: None if name == "pandoc" else real_which(name, *a, **kw))
    assert shutil.which("pandoc") is None


# ═══════════════════════════════ 门一：渲染稿一致性（退出码 9）


def test_改过渲染稿就拒绝导出(tmp_path: Path, no_pandoc: None) -> None:
    """正常渲染一次 → 手改 paper_rendered.md 一个数 → 再导出：拒绝，并指出差异的行号与两边原文。"""
    run = _run(tmp_path)
    paper_api.render(str(run))
    src = run / "paper_rendered.md"
    text = src.read_text(encoding="utf-8")
    assert "0.100" in text, text
    src.write_text(text.replace("0.100", "0.999", 1), encoding="utf-8")

    with pytest.raises(errors.Refused) as e:
        paper_api.export(str(run))
    assert e.value.code == 9, e.value.summary
    assert code_of(e.value.summary) == "export.rendered_stale"
    assert e.value.problems and all(code_of(p) == "export.rendered_stale" for p in e.value.problems)
    first = str(e.value.problems[0])
    assert "行" in first and "0.999" in first and "0.100" in first, first


def test_渲染稿没动照常导出(tmp_path: Path, no_pandoc: None) -> None:
    """回归：正常的稿子不许被这两道门拦下——门全过就停在「没装 pandoc」。"""
    run = _run(tmp_path)
    paper_api.render(str(run))
    with pytest.raises(errors.Unavailable) as e:
        paper_api.export(str(run))
    assert e.value.code == 1 and "pandoc" in e.value.summary


def test_源稿改了没重渲只提醒不拦(tmp_path: Path, no_pandoc: None) -> None:
    """渲染稿本身没被动过、是源稿在渲染之后改了：那是渲染稿过期，提醒一句，不按门拦。

    **这两件事的处置不一样**，不能并成一条：前者要撤销对渲染稿的改动，后者重跑一次 render 就好。
    """
    run = _run(tmp_path)
    paper_api.render(str(run))
    p = run / "paper.md"
    p.write_text(p.read_text(encoding="utf-8").replace("背景。", "背景与动机。"), encoding="utf-8")
    with pytest.raises(errors.Unavailable) as e:      # 停在「没装 pandoc」，不是被门拒
        paper_api.export(str(run))
    assert "pandoc" in e.value.summary
    assert any("重跑" in line[1] and "render" in line[1] for line in e.value.data["log"]), e.value.data["log"]


def test_没记过指纹的老运行目录不硬拦(tmp_path: Path, no_pandoc: None) -> None:
    """留痕里没有渲染稿的指纹（这一批之前的运行目录）：判不出是哪一头动的，就不硬拦。"""
    run = _run(tmp_path)
    (run / "paper_rendered.md").write_text(_fresh(run).replace("0.100", "0.999", 1), encoding="utf-8")
    assert not (run / "trace.jsonl").exists()
    with pytest.raises(errors.Refused) as e:          # 数字回账那道门照样拦（门二）
        paper_api.export(str(run))
    assert e.value.code == 10, e.value.summary


def test_渲染稿的指纹记在留痕里(tmp_path: Path) -> None:
    """门一靠它分清「渲染稿被人手改了」与「源稿改完没重渲」——`oss paper render` 要把指纹记下来。"""
    run = _run(tmp_path)
    paper_api.render(str(run))
    want = hashlib.sha256((run / "paper_rendered.md").read_bytes()).hexdigest()
    assert paper_api.rendered_fingerprint(require_run(run)) == want


# ═══════════════════════════════ 门二：交付文本复核（退出码 10）


def test_交付文本里的裸数字被抓到(tmp_path: Path, no_pandoc: None) -> None:
    """源稿占位符指向 0.100，渲染稿被改成 0.999：交付出去的那份文字里，这个数没有出处。"""
    run = _run(tmp_path)
    (run / "paper_rendered.md").write_text(_fresh(run).replace("0.100", "0.999", 1), encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        paper_api.export(str(run))
    assert e.value.code == 10, e.value.summary
    assert code_of(e.value.summary) == "export.delivered_check"
    assert any("0.999" in str(p) for p in e.value.problems), e.value.problems


def test_交付文本里渲染器写下的数不算裸数字(tmp_path: Path, no_pandoc: None) -> None:
    """负对照：结果文件里存的是全精度，正文里是三位小数——不收渲染器解析出来的那一份，
    一篇干净的稿子会被自己的数拦下几十条。"""
    run = _run(tmp_path)
    (run / "paper_rendered.md").write_text(_fresh(run), encoding="utf-8")
    with pytest.raises(errors.Unavailable) as e:
        paper_api.export(str(run))
    assert "pandoc" in e.value.summary


def test_交付文本里的因果措辞被抓到() -> None:
    """描述类论文的交付文本里有一句明确的因果结论：复核拦下。

    这里直接考纯函数：走整条 `export` 的话，没有语料的兜底路径上、退出码 8 那道老门先判
    （交付的就是渲染稿），轮不到这一条；而有语料时交付文本是从源稿重渲的，源稿那道老门
    也先判。这一条守的是**渲染之后才成形的那种**，与老门同一个判据、不同的文本。
    """
    out = paper_api.delivered_problems("互联网普及导致收入差距扩大。", known=set(),
                                       results=None, power=None, wtype="descriptive", downgraded=False)
    assert out and all(code_of(p) == "export.delivered_check" for p in out)
    assert any("导致" in str(p) for p in out), out


def test_交付文本里的过程话与局限都在复核里() -> None:
    """过程话、局限的功效：同样是在渲染之后才看得见的那一份文字上再跑一遍。"""
    out = paper_api.delivered_problems("## 讨论\n我们先试了固定效应，后来换成工具变量。\n", known=set(),
                                       results=None, power=None, wtype="", downgraded=False)
    assert out and any("过程" in str(p) or "先试" in str(p) for p in out), out

    limits = paper_api.delivered_problems("## 讨论\n### 局限\n样本不大。\n", known=set(), results=None,
                                          power=SimpleNamespace(verdict="fail", mode="power"),
                                          wtype="", downgraded=False)
    assert limits and any("功效" in str(p) for p in limits), limits


def test_显著性说反不在交付文本上重跑() -> None:
    """**定下来的边界**：渲染后 p 值已是字面数字，要判得把占位符的解析映射一路传下来；
    源稿那一遍（`check_claims`）已经覆盖。这里钉住它不在交付复核里，免得下一个人「顺手」补上。"""
    said = "这一效应显著（p = 0.420）。"
    assert paper_api.delivered_problems(said, known={"0.420"}, results=None, power=None,
                                        wtype="", downgraded=False) == []
