"""转述核对：我说他说过的话，他真的说过吗。

上一件保证了「这篇文献真实存在、作者年份没写错」。
**保证不了「转述得对」**——编号是真的、年份是对的，内容可以是歪的。

**最大的障碍是语言。**论文是中文，语料是英文摘要，
纯词面比对必然全零——「Breen 发现教育扩张促进了流动」里没有一个英文词。
所以要经中英构念词典把中文说法映射成英文词根，再去摘要里找。

**这一层很弱，而且必须承认它弱：**

  没有摘要        → **核不了**（不是「存疑」）
  中文里没有可映射的词 → **核不了**
  能映射且命中     → 支持
  能映射但一个不中 → 存疑，交给跨模型判

「核不了」和「存疑」必须分开——跟之前「判不了 ≠ 跑题」是同一个道理。
混在一起会让这个指标自己变成噪声。
"""

from __future__ import annotations

import pytest

from oss.core.contracts import PaperRecord

BREEN = PaperRecord(
    paper_id="W2079007828",
    title="Educational Expansion and Social Mobility in the 20th Century",
    abstract=("Sociologists consider inequality in educational attainment to be a major "
              "cause of inequality between people. I measure the effect of educational "
              "expansion on social mobility in Britain, Sweden and Germany."),
    year=2010, authors=["Richard Breen"], doi="10.1353/sof.2010.0076", source="openalex")

NO_ABSTRACT = PaperRecord(
    paper_id="W9", title="Some Title Without Abstract",
    year=1999, authors=["A. Author"], source="openalex")


def support(sentence: str, rec=BREEN):
    from oss.core.manuscript import citation_support

    return citation_support(sentence, rec)


# ══════════════════════════════════ 能判的时候要判对


def test_转述与摘要一致判支持() -> None:
    st, why = support("{{cite:W2079007828}} 发现教育扩张促进了社会流动。")
    assert st == "支持", why


def test_张冠李戴判存疑() -> None:
    """摘要通篇在说教育扩张与流动，而我写成了「城乡户口差异」。"""
    st, why = support("{{cite:W2079007828}} 发现城乡户口差异决定了健康水平。")
    assert st == "存疑", why
    assert "命中" in why or "一个都没" in why, why


def test_中文能经词典映射到英文摘要() -> None:
    """**这是这一步的核心难点。**中文论文引英文文献，纯词面比对必然全零。"""
    from oss.core.manuscript import _claim_fragments

    frags = _claim_fragments("教育扩张促进了社会流动")
    assert any(f in "educational expansion" for f in frags), frags
    assert any(f in "social mobility" for f in frags), frags


# ══════════════════════════════════ 判不了的时候要说判不了


def test_没有摘要判核不了() -> None:
    """**不是「存疑」。**只有一个标题，文本太少，机器没有判据。"""
    st, why = support("{{cite:W9}} 发现了什么。", NO_ABSTRACT)
    assert st == "核不了"
    assert "摘要" in why


def test_中文里没有可映射的词也判核不了() -> None:
    """句子里全是词典覆盖不到的说法，机器同样没有判据——**别报成存疑**。"""
    st, why = support("{{cite:W2079007828}} 的论证方式值得商榷。")
    assert st == "核不了", why


def test_核不了不能被当成存疑() -> None:
    """混在一起会让这个指标自己变成噪声，跟「判不了 ≠ 跑题」是同一个道理。"""
    assert support("{{cite:W9}} 发现了什么。", NO_ABSTRACT)[0] != "存疑"


# ══════════════════════════════════ 接进论文校验


def test_存疑的转述要在校验里报出来() -> None:
    from oss.core.contracts import AnalysisResults, Estimate, LiteratureCorpus
    from oss.core.manuscript import check

    corpus = LiteratureCorpus(corpus_id="c", records=[BREEN])
    res = AnalysisResults(plan_id="p", n=10,
                          estimates={"main": Estimate(term="x", coef=1.0, se=0.1, p=0.001)})
    text = ("# T\n## 摘要\na\n## 引言\n"
            "{{cite:W2079007828}} 发现城乡户口差异决定了健康水平。\n"
            "## 数据与方法\nb\n## 结果\n{{result:main.coef}}\n## 讨论\nc\n"
            "## 参考文献\n{{references}}")
    _, warns = check(text, res, corpus=corpus)
    assert any("存疑" in w and "W2079007828" in w for w in warns), warns


def test_一致的转述不报警() -> None:
    from oss.core.contracts import AnalysisResults, Estimate, LiteratureCorpus
    from oss.core.manuscript import check

    corpus = LiteratureCorpus(corpus_id="c", records=[BREEN])
    res = AnalysisResults(plan_id="p", n=10,
                          estimates={"main": Estimate(term="x", coef=1.0, se=0.1, p=0.001)})
    text = ("# T\n## 摘要\na\n## 引言\n"
            "{{cite:W2079007828}} 发现教育扩张促进了社会流动。\n"
            "## 数据与方法\nb\n## 结果\n{{result:main.coef}}\n## 讨论\nc\n"
            "## 参考文献\n{{references}}")
    _, warns = check(text, res, corpus=corpus)
    assert not [w for w in warns if "存疑" in w], warns
