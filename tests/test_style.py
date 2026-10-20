"""风格包：有模板按模板写。门是通用的，数字是每本刊自己的。"""

from __future__ import annotations

import json
from pathlib import Path

from oss.core.contracts import StyleRules
from oss.core.style import (
    _section_spans,
    check_paper,
    check_rules_grounded,
    overlaps,
    profile_paper,
)

GUIDE = ("Articles submitted to Social Forces may not exceed 10,000 words (including text, endnotes, "
         "and references), and may not contain more than 10 tables and figure panels. The journal "
         "operates double-anonymized peer review. Abstracts should not include references. "
         "Chicago Manual of Style, 17th edition. Supplementary material cannot exceed 10 pages.")


def _paper(words_intro=300, n_fig=2, extra=""):
    intro = " ".join(["The debate over democratization and growth remains unsettled."] * (words_intro // 8))
    return f"""# A Title

## Abstract

We ask whether democratization raises income. Using a panel of 190 countries we find no effect.

## Introduction

{intro}

## Data and Methods

We use the Quality of Government panel (Smith 2020). Figure 1 shows the structure.

## Results

The estimate is small. Figure {n_fig} shows robustness. Table 1 reports estimates.

## Discussion

{extra}

## References

- Smith, J. (2020). A paper.
"""


# ─────────────────────────── 骨架抽取


def test_按关键词认节不按排版猜():
    spans = _section_spans(_paper())
    assert list(spans) == ["abstract", "introduction", "data_methods", "results", "discussion", "references"]


def test_骨架里的数是量出来的():
    st = profile_paper(_paper(), "x.pdf")
    assert st.words_abstract > 10 and st.words_body > 0
    assert st.n_figures == 2 and st.n_tables == 1
    assert st.cites_per_1000 > 0
    assert "democratization" in st.intro_opening.lower()


# ─────────────────────────── 规则核对：数字必须在须知原文里


def test_规则里的数字须知里没有就拦():
    ok = StyleRules(journal="Social Forces", word_limit=10000, max_tables_figures=10,
                    supplementary_max_pages=10, reference_style="Chicago Manual of Style, 17th edition")
    assert check_rules_grounded(ok, GUIDE) == []
    bad = StyleRules(journal="Social Forces", word_limit=12000)
    problems = check_rules_grounded(bad, GUIDE)
    assert any("12000" in p for p in problems), "编一个上限跟编一个系数一样"


def test_须知没说的留空门就不生效():
    rules = StyleRules(journal="X")            # 全是 None / False
    problems, _ = check_paper(_paper(), rules, None)
    assert not problems


# ─────────────────────────── 七道门


def _sf_rules():
    return StyleRules(journal="Social Forces", word_limit=10000, max_tables_figures=10,
                      double_blind=True, abstract_no_citations=True)


def test_字数超上限拒绝():
    rules = StyleRules(journal="X", word_limit=50)
    problems, _ = check_paper(_paper(), rules, None)
    assert any("超过" in p and "上限" in p for p in problems)


def test_双盲正文里不许有致谢():
    problems, _ = check_paper(_paper(extra="We thank the reviewers. Funding from grant no. 123."), _sf_rules(), None)
    assert any("双盲" in p for p in problems)


def test_摘要里有引用拒绝():
    text = _paper().replace("we find no effect.", "we find no effect (Acemoglu et al. 2019).")
    problems, _ = check_paper(text, _sf_rules(), None)
    assert any("摘要里有引用" in p for p in problems)


def test_图编号不按出现顺序拒绝():
    """验收跑的成稿里 Figure 2、3 出现在 Figure 1 前面——正好撞这条。"""
    text = _paper().replace("Figure 1 shows the structure", "Figure 3 shows the structure")
    problems, _ = check_paper(text, _sf_rules(), None)
    assert any("编号不按出现顺序" in p for p in problems)


def test_骨架偏离只警告不拒绝():
    profile = {"ranges": {"words_abstract": {"min": 150, "median": 180, "max": 220}}}
    problems, warnings = check_paper(_paper(), None, profile)
    assert not problems
    assert any("摘要词数" in w and "范文里是" in w for w in warnings)


# ─────────────────────────── 查重：学结构，不抄句子


def test_跟范文连续8词相同拒绝():
    exemplar = {"ex1": "In this paper we argue that the class ceiling reflects accumulated advantage across generations."}
    text = _paper(extra="As prior work notes, the class ceiling reflects accumulated advantage across generations in the labor market.")
    problems, _ = check_paper(text, None, None, exemplar)
    assert any("连续 8 个词相同" in p for p in problems)


def test_正常改写不触发查重():
    exemplar = {"ex1": "In this paper we argue that the class ceiling reflects accumulated advantage across generations."}
    text = _paper(extra="We interpret the pay gap as advantage that accumulates over the life course rather than a ceiling.")
    assert overlaps(text, exemplar, ngram=8) == []


# ─────────────────────────── 模具：骨架 → 模板 → 按骨架查节


def test_骨架按范文多数派给出节和词数():
    from oss.core.style import DEFAULT_SKELETON, skeleton, template_markdown

    profile = {
        "pack": "x",
        "section_order": ["abstract", "introduction", "background", "data_methods",
                          "results", "discussion", "limitations", "references"],
        "limitations_as_heading_share": 0.33,
        "papers": [
            {"words_body": 8000, "section_words": {"abstract": 160, "introduction": 900, "results": 2500}},
            {"words_body": 9000, "section_words": {"abstract": 200, "introduction": 1100, "results": 3000}},
            {"words_body": 500, "section_words": {"introduction": 5}},      # 抽不出正文的不算
        ],
    }
    skel = skeleton(profile)
    assert [s["key"] for s in skel] == ["abstract", "introduction", "background", "data_methods",
                                        "results", "discussion"], "limitations 没过半不单列；references 不在骨架里"
    assert next(s for s in skel if s["key"] == "introduction")["words"] == 1000
    md = template_markdown(skel, pack="x")
    assert "## Data and Methods" in md and "{{references}}" in md and "~1000 words" in md
    assert [s["key"] for s in skeleton(None)] == list(DEFAULT_SKELETON), "没有范文按标准格式"


def test_引用格式说法映射到CSL():
    from oss.core.style import csl_for

    assert csl_for("Chicago Manual of Style, 17th edition") == "chicago-author-date"
    assert csl_for("APA 7th") == "apa"
    assert csl_for("ASA style guide") == "american-sociological-association"
    assert csl_for("") == "chicago-author-date"


def test_引用形式认得出数字编号与作者年份():
    from oss.core.style import citation_form

    assert citation_form("Prior work [1], [2, 3] shows this.") == "numeric"
    assert citation_form("Prior work (Smith 2020) shows this. Jones (2019) agrees.") == "author-year"
    assert citation_form("No citations here.") == ""


def test_包里带的三个CSL样式文件都在():
    from pathlib import Path

    d = Path(__file__).resolve().parent.parent / "oss" / "assets" / "csl"
    for n in ("chicago-author-date", "apa", "american-sociological-association"):
        assert (d / f"{n}.csl").stat().st_size > 5000, n
