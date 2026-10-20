"""英文论文与成品导出。

阶段一的方向转变（用户拍板：先英文、出 Word）暴露的问题是同一类的：
**校验链的每一条判据原先只认中文**——英文论文一来，「显著/证明/方向词/节名」
全部失明，校验照样全绿。**全绿的失明比报错更危险。**
"""

from __future__ import annotations

import shutil

import pytest

from oss.core.contracts import AnalysisResults, Estimate, PowerReport
from oss.core.manuscript import (
    REQUIRED_SECTIONS,
    _binned_without_record,
    _cite_label,
    _is_english,
    check,
    render,
)


def _results(p=0.4, coef=0.2):
    return AnalysisResults(
        plan_id="p", n=1000,
        estimates={"main": Estimate(term="x", coef=coef, se=0.1, p=p)},
    )


def _en_paper(body: str) -> str:
    secs = ["Abstract", "Introduction", "Data and Methods", "Results", "Discussion"]
    return "\n\n".join(f"## {s}\n\nPlaceholder text." for s in secs) + "\n\n" + body


# ─────────────────────────────────── 显著性：英文也要硬拦


def test_英文论文p大却说显著要被拦():
    paper = _en_paper("The interaction is significant (p {{result:main.p}}).")
    problems, _ = check(paper, _results(p=0.4))
    assert any("显著" in p or "significant" in p.lower() for p in problems)


def test_insignificant里藏着significant不能误判():
    """`insignificant` 包含 `significant` 作子串——否定式必须先查。"""
    paper = _en_paper("The interaction is insignificant (p {{result:main.p}}).")
    problems, _ = check(paper, _results(p=0.4))
    assert not problems, problems


def test_英文论文p小却说不显著要被拦():
    paper = _en_paper("The effect is not statistically significant (p {{result:main.p}}).")
    problems, _ = check(paper, _results(p=0.001))
    assert problems


# ─────────────────────────────────── 方向与「证明」


def test_英文方向词与符号对不上要警告():
    paper = _en_paper("The coefficient {{result:main.coef}} shows the gap declines sharply.")
    _, warnings = check(paper, _results(coef=+0.2))
    assert any("方向" in w for w in warnings)


def test_comprise里藏着rise不能误报方向():
    """英文方向词必须带词边界——`comprise` 里就藏着一个 `rise`。"""
    paper = _en_paper("The samples comprise {{result:main.coef}} of the population share.")
    _, warnings = check(paper, _results(coef=-0.2))
    assert not any("方向" in w for w in warnings), warnings


def test_英文prove要警告_demonstrate不警告():
    p1 = _en_paper("This proves the effect is real ({{result:main.coef}}).")
    _, w1 = check(p1, _results(p=0.01))
    assert any("证明" in w for w in w1)

    # demonstrate/show 是常规学术用语——拦了就是天天响的警报
    p2 = _en_paper("This demonstrates a robust association ({{result:main.coef}}).")
    _, w2 = check(p2, _results(p=0.01))
    assert not any("证明" in w for w in w2)


# ─────────────────────────────────── 该写没写：英文关键词也认


def test_功效fail时英文局限里写了power就放行():
    pw = PowerReport(idea_id="i", effect_size_raw=0.1, effect_size_adjusted=0.06,
                     n_raw=1000, power=0.2, verdict="fail")
    ok = _en_paper("") .replace(
        "## Discussion\n\nPlaceholder text.",
        "## Discussion\n\nA key limitation: statistical power is only {{power:power}}.")
    problems, _ = check(ok, _results(p=0.01), pw)
    assert not any("功效" in p for p in problems), problems

    bad = _en_paper("Nothing about it.")
    problems, _ = check(bad, _results(p=0.01), pw)
    assert any("功效" in p for p in problems)


def test_英文分箱形状断言而文件里没有要拦():
    hits = _binned_without_record(
        "The binned estimates show a clearly non-monotonic pattern.", _results())
    assert hits
    assert not _binned_without_record(
        "Binned estimation was not performed in this study.", _results())


# ─────────────────────────────────── 图占位符


def test_图存在就解析_不存在就拦():
    paper = _en_paper("{{fig:robustness}}\n\n*Figure 1. Robustness.*")
    problems, _ = check(paper, _results(p=0.01), figures={"robustness"})
    assert not any("fig" in p for p in problems), problems

    problems, _ = check(paper, _results(p=0.01), figures=set())
    assert any("robustness" in p for p in problems)


def test_渲染把图占位符换成图片引用():
    paper = _en_paper("{{fig:robustness}}")
    out = render(paper, _results(p=0.01), figures={"robustness"})
    assert "![robustness](fig_robustness.png)" in out


def test_没传图片清单时引图要报核验不了():
    """调用方没给清单 ≠ 图存在。**核不了就说核不了，不能当通过。**"""
    paper = _en_paper("{{fig:robustness}}")
    problems, _ = check(paper, _results(p=0.01), figures=None)
    assert any("核验不了" in p for p in problems)


# ─────────────────────────────────── 引用与参考文献的英文形态


class _Rec:
    paper_id = "W1"; doi = "10.1/x"; year = 2018; title = "T"
    authors = ["Colin Camerer", "Anna Dreber", "Felix Holzmeister"]


def test_引用标注英文用et_al中文用等():
    assert _cite_label(_Rec(), en=True) == "Camerer et al. (2018)"
    assert _cite_label(_Rec(), en=False) == "Camerer 等 (2018)"


def test_语言探测():
    assert _is_english(_en_paper("The effect is small."))
    assert not _is_english("## 摘要\n\n本文考察教育扩张的作用。")


# ─────────────────────────────────── 成品导出


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="没装 pandoc")
def test_导出的Word里图真的嵌进去了(tmp_path):
    """**转换成功不等于图在。**docx 是 zip，图在 word/media/ 下，数得出来。"""
    import subprocess, zipfile

    import numpy as np
    from PIL import Image  # noqa: F401  (matplotlib 依赖里有)

    # 造一张真 png
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(); ax.plot([1, 2]); fig.savefig(tmp_path / "fig_x.png"); plt.close(fig)

    (tmp_path / "r.md").write_text("# T\n\n![x](fig_x.png)\n", encoding="utf-8")
    out = tmp_path / "o.docx"
    subprocess.run(["pandoc", "r.md", "-f", "gfm", "-o", str(out.resolve())],
                   cwd=tmp_path, check=True)
    with zipfile.ZipFile(out) as z:
        assert sum(1 for n in z.namelist() if n.startswith("word/media/")) == 1


# ─────────────────────────────────── 审计跨窗口真跑记录后补的：中文否定式近邻


def _zh_paper(body: str) -> str:
    secs = ["摘要", "引言", "数据与方法", "结果", "讨论"]
    return "\n\n".join(f"## {s}\n\n占位。" for s in secs) + "\n\n" + body


def test_未达常规显著水平不能被当成说显著():
    """真跑记录里的原样案例：这句被硬拦，模型只好**改写句子绕开「显著」两个字**。

    工具的盲区在扭曲论文的语言——比漏报更糟。
    """
    paper = _zh_paper("该交互项未达常规显著水平（p {{result:main.p}}）。")
    problems, _ = check(paper, _results(p=0.4))
    assert not problems, problems


def test_不仅显著是在强调不是否定():
    paper = _zh_paper("该效应不仅显著（p {{result:main.p}}），而且量级可观。")
    problems, _ = check(paper, _results(p=0.4))
    assert problems, "「不仅显著」是在说显著，p=0.4 必须拦"


def test_不具有统计显著性算否定():
    paper = _zh_paper("该系数不具有统计显著性（p {{result:main.p}}）。")
    problems, _ = check(paper, _results(p=0.001))
    assert problems, "说不显著而 p 极小，必须拦"


# ─────────────────────────────────── 诊断文本：不说「人」，能译成英文


def test_面板流失诊断不说人且能译成英文():
    """验证跑的诊断原话：「107 人进入回归，其中 8 人（7%）答满了全部 61 轮」——那是国家面板。"""
    from oss.core.manuscript import _CJK_CHARS, _englishize

    zh = ("**流失明显**——107 个「country」单位进入回归，其中 8 个（7%）在全部 61 个「year」期都有观测。"
          "**流失往往不是随机的**（状况变差的单位更容易退出），若与研究对象相关会造成选择性偏误")
    en = _englishize(zh)
    assert "107 country units" in en and "61 year periods" in en and "attrition" in en.lower(), en
    assert not _CJK_CHARS.search(en), en


def test_最小可探测效应诊断能译成英文():
    from oss.core.manuscript import _CJK_CHARS, _englishize

    zh = ("80% 功效、5% 双侧下（≈2.8×标准误）：main 2.099；post_dem*net_low 2.286。"
          "**不显著的系数只有跟它比过才知道是「没有效应」还是「测不出来」**")
    en = _englishize(zh)
    assert "80% power" in en and "2.099" in en and not _CJK_CHARS.search(en), en


# ─────────────────────────────────── 图：英文稿里的图必须是英文版


def test_英文稿嵌中文版的图要被拦():
    """验证跑：英文 Word 里四张图全是中文标题。图的语言记在 fig_<name>.json 里，按它拦。"""
    paper = _en_paper("{{fig:robustness}}")
    problems, _ = check(paper, _results(p=0.01), figures={"robustness": "zh"})
    assert any("不是英文版" in p for p in problems), problems
    problems, _ = check(paper, _results(p=0.01), figures={"robustness": ""})
    assert any("不是英文版" in p for p in problems), "没有语言标记的老图也拦——重画一次就有了"
    problems, _ = check(paper, _results(p=0.01), figures={"robustness": "en"})
    assert not any("不是英文版" in p for p in problems), problems


# ─────────────────────────────────── 导出：参考文献按 CSL 样式排


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="没装 pandoc")
def test_citeproc按Chicago排出作者年份与参考文献(tmp_path):
    import json
    import subprocess
    from pathlib import Path

    csl = Path(__file__).resolve().parent.parent / "oss" / "assets" / "csl" / "chicago-author-date.csl"
    (tmp_path / "p.md").write_text(
        "---\ntitle: \"T\"\n---\n\n## Introduction\n\nAs @ref_a shows [@ref_a].\n\n## References\n\n::: {#refs}\n:::\n",
        encoding="utf-8")
    (tmp_path / "refs.json").write_text(json.dumps([{
        "id": "ref_a", "type": "article-journal", "title": "Has Democratization Reduced Infant Mortality?",
        "author": [{"family": "Kudamatsu", "given": "Masayuki"}],
        "issued": {"date-parts": [[2012]]}, "container-title": "Journal of the European Economic Association",
    }]), encoding="utf-8")
    out = tmp_path / "p.docx"
    r = subprocess.run(["pandoc", "p.md", "-f", "markdown", "--citeproc", "--csl", str(csl),
                        "--bibliography", "refs.json", "-o", str(out)], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    txt = subprocess.run(["pandoc", str(out), "-t", "plain"], capture_output=True, text=True).stdout
    assert "Kudamatsu (2012)" in txt and "(Kudamatsu 2012)" in txt, txt
    assert "Journal of the European Economic Association" in txt, "参考文献条目要带期刊名"
