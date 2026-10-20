"""中文论文模式（第 1 步：语言开关贯通）。

换的只是语言，不换任何门：占位符、表、图注、黑名单照旧。这里测三件事——
中文起稿模板长对了（编号、关键词、双语摘要）、图的语言门对称（中文稿嵌英文图也拦）、
中文稿从校验到渲染全链通（表头、参考文献标题自动跟语言走）。
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from oss.core.contracts import AnalysisResults, Estimate, SpecSummary
from oss.core.manuscript import check, process_language, render
from oss.core.style import skeleton, template_markdown

runner = CliRunner()


# ══════════════════════════════ 一、中文起稿模板


def test_中文模板_编号_关键词_双语摘要_参考文献() -> None:
    md = template_markdown(skeleton(None, lang="zh"), lang="zh")
    assert "# <标题：不超过 20 字>" in md
    assert "## 摘要" in md and "**关键词**" in md
    assert "## 一、引言" in md and "## 三、数据与方法" in md and "## 六、结论" in md
    assert "## Abstract" in md and "**Keywords:**" in md, "多数中文期刊要求文末英文摘要"
    assert md.index("## Abstract") > md.index("## 六、结论"), "英文摘要在正文之后"
    assert "## 参考文献" in md and "{{references}}" in md
    # 《社会学研究》：参考文献「列于全文之后，英文标题、摘要、关键词之前」。
    # 原先英文摘要夹在结论和参考文献中间，位置是错的
    assert md.index("## Abstract") > md.index("## 参考文献"), "英文摘要排在参考文献之后"
    assert "English title" in md, "文末英文摘要要带英文题名"
    assert "## References" not in md


def test_中文模板_砍掉一节编号不断号() -> None:
    profile = {"section_order": ["abstract", "introduction", "data_methods", "results", "discussion"],
               "papers": []}
    md = template_markdown(skeleton(profile, lang="zh"), lang="zh")
    assert "## 一、引言" in md and "## 二、数据与方法" in md and "## 四、讨论" in md
    assert "三、结果" in md and "文献与理论" not in md


def test_中文模板_目标字数用范文中位数() -> None:
    profile = {"section_order": ["abstract", "introduction"],
               "papers": [{"words_body": 9000, "section_words": {"introduction": 1200}}]}
    md = template_markdown(skeleton(profile, lang="zh"), pack="某刊", lang="zh")
    assert "目标约 1200 字（某刊 范文中位数）" in md


def test_英文模板不受影响() -> None:
    md = template_markdown(skeleton(None), pack="")
    assert "## Abstract" in md and "## References" in md and "关键词" not in md


def _skel_profile() -> dict:
    """摘要 + 两节都带目标字数——覆盖模板里两种注释形状。"""
    return {"section_order": ["abstract", "introduction", "data_methods"],
            "papers": [{"words_body": 8000,
                        "section_words": {"introduction": 1000, "data_methods": 1500}}]}


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_模板自己写的注释过不过自己的过程词检查(lang: str) -> None:
    """`oss style template` 生成的模板，作者原样保留里面的 HTML 注释交稿，
    `oss paper check` 不该因为模板自己写的话就拒——那两句注释曾经含
    「占位符」，正好撞进 `manuscript._PROCESS_ZH` 的黑名单。"""
    md = template_markdown(skeleton(_skel_profile(), lang=lang), pack="某刊", lang=lang)
    assert process_language(md) == []


def test_CLI能出中文模板() -> None:
    from oss.cli.main import app

    r = runner.invoke(app, ["style", "template", "--lang", "zh"])
    assert r.exit_code == 0, r.output
    assert "## 摘要" in r.output and "## 一、引言" in r.output


# ══════════════════════════════ 二、图的语言门对称


def _zh_paper(body: str = "") -> str:
    return f"""# 教育扩张与生育率

## 摘要
效应为 {{{{result:main.coef}}}}（标准误 {{{{result:main.se}}}}），样本量 {{{{result:main.n}}}}。

## 一、引言
背景。

## 二、数据与方法
样本量 {{{{result:main.n}}}}。

## 三、结果
*表 1. 主要估计结果。*
{{{{table:main}}}}

{body}

## 四、讨论
局限。
"""


def _res() -> AnalysisResults:
    return AnalysisResults(plan_id="p", n=360,
                           estimates={"main": Estimate(term="处理", coef=1.2496, se=0.1538, p=0.0004)},
                           spec=SpecSummary(estimator="ols_fe", fixed_effects=["省", "年"], cluster_level="省"))


def test_中文稿嵌英文图要被拦() -> None:
    paper = _zh_paper("{{fig:coef}}\n\n*图 1. 主要估计。*")
    problems, _ = check(paper, _res(), figures={"coef": "en"})
    assert any("必须中文" in p and "--lang zh" in p for p in problems), problems


def test_中文稿嵌中文图放行_语言标记为空的老图不拦() -> None:
    paper = _zh_paper("{{fig:coef}}\n\n*图 1. 主要估计。*")
    problems, _ = check(paper, _res(), figures={"coef": "zh"})
    assert not any("必须中文" in p for p in problems), problems
    problems, _ = check(paper, _res(), figures={"coef": ""})
    assert not any("必须中文" in p for p in problems), "拦「明确是英文」，不拦「不知道」"


# ══════════════════════════════ 三、中文稿全链：校验 → 渲染


def test_中文稿校验通过并渲染出中文表头与参考文献标题() -> None:
    paper = _zh_paper() + "\n{{references}}\n"
    problems, _ = check(paper, _res())
    assert not problems, problems
    out = render(paper, _res())
    assert "| 变量 | 估计值 | 标准误 | p |" in out, out[:800]
    assert "| 处理 | 1.250" in out
    assert "OLS + 固定效应" in out and "标准误按 省 聚类" in out
    assert "## 参考文献" in out and "References" not in out
    assert "{{" not in out


BODY_ZH = "\n\n## 一、引言\n\n背景。\n\n## 五、结论\n\n结论。"


def test_英文摘要只在夹在正文和参考文献之间时报警告() -> None:
    """**英文摘要不是必需的，位置也不止一种。**只判在任何体例下都不对的那一处：
    正文之后、参考文献之前（十一轮 CGSS 那篇成稿就卡在这儿）。"""
    from oss.core.manuscript import _en_abstract_position

    head = "# 题\n\n## 摘要\n\n本文。"
    夹在中间 = head + BODY_ZH + "\n\n## Abstract\n\nThis paper.\n\n## 参考文献\n\n条目\n"
    排在文末 = head + BODY_ZH + "\n\n## 参考文献\n\n条目\n\n## Abstract\n\nThis paper.\n"
    排在文首 = head + "\n\n## Abstract\n\nThis paper." + BODY_ZH + "\n\n## 参考文献\n\n条目\n"
    没有英文摘要 = head + BODY_ZH + "\n\n## 参考文献\n\n条目\n"

    assert _en_abstract_position(夹在中间), "这一处在任何体例下都不对"
    assert not _en_abstract_position(排在文末), "文后英文摘要的通行顺序"
    assert not _en_abstract_position(排在文首), "GB/T 7713.1 的学位论文就要求放前面，部分期刊也印首页"
    assert not _en_abstract_position(没有英文摘要), "很多刊压根不要英文摘要"
    assert not _en_abstract_position("# T\n\n## Abstract\n\nx\n\n## References\n\ny\n"), "英文稿不管"


def test_只有中文摘要也算摘要齐全() -> None:
    """`REQUIRED_SECTIONS` 里中英摘要是**命中任一即可**——没有英文摘要不该判缺节。"""
    from oss.core.manuscript import REQUIRED_SECTIONS, _section_body

    src = ("# 题\n\n## 摘要\n\n本文。\n\n## 一、引言\n\n背景。\n\n## 二、数据与方法\n\n样本。"
           "\n\n## 三、结果\n\n结果。\n\n## 四、讨论\n\n讨论。\n")
    missing = [g[0] for g in REQUIRED_SECTIONS if not any(_section_body(src, k).strip() for k in g)]
    assert missing == [], missing
