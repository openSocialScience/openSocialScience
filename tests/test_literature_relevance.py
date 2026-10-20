"""语料的**主题相关性**：现在完全没有度量。

**PLAN.md 九之一 ⑪。**`_diagnose` 把覆盖率翻译成了后果，做得很好——
但覆盖率说的是「有多少条带摘要 / 带引文」，**不是「有多少条真的相关」**。

**一个语料可以 100% 覆盖、100% 跑题。**
第一次社会学试跑返回 60 篇，大半是流行病学（儿童哮喘、多病共存），
因为 `cohort` / `attainment` 在那边也是高频词。
GSS 那次运气好（关键词命中 86%），但那是**检索式写得好，不是工具保证的**。

跑题的语料会一路污染下游：空白地图建在无关文献上，
生成的 idea 会去填一个不存在的空白，而且**没有任何一步会报错**。
"""

from __future__ import annotations

import pytest

from oss.core import literature as L
from oss.core.contracts import LiteratureCorpus, PaperRecord


def rec(title: str, abstract: str = "") -> PaperRecord:
    return PaperRecord(paper_id=title[:12], title=title, abstract=abstract, source="test")


def with_abs(r: PaperRecord) -> PaperRecord:
    """相关性判据要看标题+摘要；没有摘要的记录不参与「跑题」计数。"""
    return rec(r.title, r.title)


ON = [
    rec("Educational Expansion and Social Mobility in the 20th Century"),
    rec("Maximally Maintained Inequality: Expansion, Reform, and Opportunity"),
    rec("Is a College Degree Still the Great Equalizer? Intergenerational Mobility"),
    rec("Class Origins, Education and Occupational Attainment in Britain"),
]
#: 都带摘要——**没有摘要的记录按新规则不计入「跑题」**，见下面那条测试
OFF = [
    rec("The Pathophysiology, Diagnosis and Management of Flail Chest Injuries",
        "Flail chest is a serious thoracic injury requiring surgical fixation."),
    rec("Balancing nitrogen at the farm gate",
        "We measure nitrogen flows on dairy farms across three regions."),
    rec("AI in medicine: an introduction to the potential benefits",
        "Machine learning models are increasingly deployed in clinical settings."),
    rec("Home environment relationships with children's physical activity",
        "We examine sedentary behaviour and screen time among preschool children."),
]
QUERIES = ["educational expansion social mobility",
           "intergenerational occupational mobility education"]


def test_全相关的语料相关度高() -> None:
    """**阈值的校准点**：GSS 那次真语料（194 条，人工判断约 86% 相关）
    算出来中位数 0.75、跑题 3%，分位数 p10=0.5 / p25=0.75。
    所以「明显不对劲」的线定在 0.4 与 25%，不是「刚好合格」的线。
    """
    score, off, _ = L.topical_relevance(ON, QUERIES)
    assert score >= 0.5, f"相关度只有 {score:.2f}"
    assert off == 0


def test_全跑题的语料相关度低() -> None:
    score, off, examples = L.topical_relevance(OFF, QUERIES)
    assert score < 0.2, f"相关度居然有 {score:.2f}"
    assert off == len(OFF), "跑题的条数该全数报出来"
    assert examples, "要给出跑题的例子，否则使用者没法判断"


def test_跑题的语料要在notes里报出后果() -> None:
    """**只报数字没有信息量。**

    「相关度 0.08」对使用者没用；
    「一半以上的文献与检索式无主题重合，空白地图会建在无关文献上」才有用。
    """
    corpus = LiteratureCorpus(
        corpus_id="c", queries=QUERIES, records=OFF + ON[:1],
        hits_by_source={"openalex": 5}, abstract_coverage=1.0, citation_coverage=1.0,
    )
    L._diagnose(corpus, {})
    joined = " ".join(corpus.notes)
    assert "相关" in joined, corpus.notes
    assert any(k in joined for k in ("空白", "污染", "无关")), "要说清后果不是只报数字"


def test_相关的语料不该被误报() -> None:
    corpus = LiteratureCorpus(
        corpus_id="c", queries=QUERIES, records=ON,
        hits_by_source={"openalex": 4}, abstract_coverage=1.0, citation_coverage=1.0,
    )
    L._diagnose(corpus, {})
    assert not any("跑题" in n or "无主题重合" in n for n in corpus.notes), corpus.notes


def test_相关度要落进产出物() -> None:
    """写进 notes 不够——下游要能读到这个数，论文局限里也要能引用。"""
    corpus = LiteratureCorpus(
        corpus_id="c", queries=QUERIES, records=OFF,
        hits_by_source={"openalex": 4}, abstract_coverage=1.0, citation_coverage=1.0,
    )
    L._diagnose(corpus, {})
    assert corpus.topical_relevance < 0.2
    assert corpus.off_topic_count == len(OFF)


def test_没有摘要时零重合不算跑题() -> None:
    """**「判不了」和「跑题」是两回事。**

    只有一个标题的话文本太少。GSS 真语料里
    `Testing MMI and EMI in the Czech...` 高度相关，但标题不含检索词、又没摘要——
    把它算成跑题，这个指标自己就变成了噪声。
    """
    _, off, _ = L.topical_relevance([rec("Testing MMI and EMI in the Czech Republic")], QUERIES)
    assert off == 0, "没有摘要的记录不该被判成跑题"
    _, off2, _ = L.topical_relevance(
        [rec("Balancing nitrogen at the farm gate", "We measure nitrogen flows on dairy farms.")],
        QUERIES,
    )
    assert off2 == 1, "有摘要且零重合的，才算跑题"


def test_停用词不算主题重合() -> None:
    """`and` / `the` / `of` 命中不能算相关，否则什么都相关。"""
    noise = [rec("The and of in for a study", "The and of in for a study of things.")]
    score, off, _ = L.topical_relevance(noise, ["the and of education mobility"])
    assert score < 0.2 and off == 1


# ------------------------------------------------- 概念块：每块命中一个同义词就算这一块中了

BLOCKS_Q = '("compulsory schooling" OR "schooling reform") AND (fertility OR childbearing)'


def test_概念块_每块命中一个同义词就算全相关() -> None:
    """OR 写法下，一篇完全对题的论文只会命中每块里的一个同义词。按整串检索式的
    实词命中比例算，它只有 0.33——健康的语料因此被报成「跑题」。"""
    r = rec("Schooling and births", "compulsory schooling laws raised fertility")
    assert L.topical_relevance([r], [BLOCKS_Q])[0] == 1.0


def test_概念块_只沾一块算半相关() -> None:
    r = rec("Schooling and pay", "compulsory schooling and wages")
    assert L.topical_relevance([r], [BLOCKS_Q])[0] == 0.5


def test_旧写法裸词袋的相关度跟原来一样() -> None:
    """改成按块算之前是「整串检索式的实词命中比例」。裸词袋每个词自成一块，两种算法同解——
    GSS 那个校准点（0.4 / 25%）因此仍然成立。注意**没有词干化**：`educational` ≠ `education`。"""
    r = rec("Schooling and jobs", "education expansion and social mobility")
    assert L.topical_relevance([r], ["educational expansion social mobility"])[0] == 0.75


def test_中文检索词按子串命中() -> None:
    """中文不分词：`_content_words` 按非汉字切分，「农民工市民化的路径选择」整段是**一个词**，
    「农民工」在里面是子串不是整词——按整词比对的话，每一条中文记录的相关度都是 0，
    导入的中文文献与 `--zh` 历史层进了语料却被报成「全部跑题」。"""
    r = PaperRecord(paper_id="c1", title="农民工市民化的路径选择",
                    abstract="本文讨论外来农民工的市民化", source="import:ris")
    score, off, _ = L.topical_relevance([r], ["农民工 市民化"])
    assert score == 1.0 and off == 0


def test_两字中文检索词也算实词() -> None:
    """`len(w) > 2` 是给西文定的（`the`、`and` 三字以下多是虚词）。中文最常见的实词恰好
    是两个字——教育、生育、户籍、收入、房价——一刀切下去，最常见的中文检索式一个词都不剩：
    `topical_relevance` 报 0.00 的假警报，`rank()` 一条都不给。单字汉字仍然挡（「的」「与」）。"""
    r = rec("教育与生育", "教育对生育的影响")
    assert L.topical_relevance([r], ["教育 生育"])[0] == 1.0
    assert "的" not in L._content_words("教育的生育")


def test_中文泛词不算实词() -> None:
    """「研究」「分析」「影响」在中文标题里几乎无处不在——它们跟英文的 `study`、`analysis`
    是同一类功能词，留着的话「什么都相关」：`人口研究综述` 会命中 `生育 研究` 的一半。"""
    assert "研究" not in L._content_words("生育 研究")
    assert "生育" in L._content_words("生育 研究")
    assert L.topical_relevance([rec("人口研究综述")], ["生育 研究"])[0] == 0.0
