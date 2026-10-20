"""风格包认中文（第 2 步）。

中文期刊数「字数」不数「词数」、节名是「一、引言」不是 Introduction、
引用是（张三，2020）或 GB/T 7714 的 [1]、查重口径是连续 13 字不是 8 词。
这些原来全按英文写死——8000 字的中文范文按空格切出来只有几百「词」，
每一道按词数的门都失真。夹具全是自己编的句子，不含任何真实期刊内容。
"""

from __future__ import annotations

from oss.core.style import (
    _words, check_paper, citation_form, csl_for, overlaps, profile_paper,
)
from oss.core.contracts import StyleRules


def _zh_paper(*, tables: str = "表 1 报告了描述统计。图 1 展示了趋势。表 2 是主结果。",
              cite: str = "已有研究指出教育扩张改变了地位获得的机制（张三，2020；李四等，2019）。王五（2018）持不同看法。",
              filler_reps: int = 60) -> str:
    filler = "本文利用面板数据考察教育扩张对地位获得的影响，并对不同队列分别进行估计。" * filler_reps
    return f"""教育扩张与地位获得：基于面板数据的考察

摘要
本文考察教育扩张的作用。研究发现效应集中在早期队列。

一、引言
{cite}我们据此提出研究问题。{filler}

二、文献与理论
相关研究可分为两派。{filler}

三、数据与方法
数据来自某调查。{tables}{filler}

四、结果
估计结果表明效应为正。{filler}

五、讨论
本文的局限在于测量。{filler}

参考文献
张三，2020。教育扩张研究。某刊物。
"""


# ══════════════════════════════ 一、数字数：中文按字


def test_中文按字数英文按词数混排相加() -> None:
    assert _words("hello world") == 2
    assert _words("教育扩张改变了地位获得") == 11
    assert _words("我们用 OLS 估计") == 5 + 1


def test_中文范文的画像_节都认得出_字数是真字数() -> None:
    st = profile_paper(_zh_paper(), file="fake.pdf")
    assert st.words_body > 2000, "8000 字的中文范文不该被数成几百词"
    for k in ("abstract", "introduction", "background", "data_methods", "results", "discussion"):
        assert k in st.sections_found, (k, st.sections_found)
    assert st.n_tables == 2 and st.n_figures == 1
    assert st.citation_form == "author-year"
    assert st.cites_per_1000 > 0, "（张三，2020）这类中文引用要数得到"
    assert st.words_abstract < 100


def test_图表不把图表二字错数成表() -> None:
    st = profile_paper(_zh_paper(tables="图表的编排见附录。表 1 是描述统计。"))
    assert st.n_tables == 1


# ══════════════════════════════ 二、引用形式：GB T 7714 顺序编码


def test_数字编号引用认半角与全角括号() -> None:
    assert citation_form("已有研究表明存在正效应[1]，也有研究持相反看法[2,3]。") == "numeric"
    assert citation_form("已有研究表明存在正效应［1］，另见［2－3］。") == "numeric"
    assert citation_form("已有研究（张三，2020)与王五（2018）意见不一。") == "author-year"


def test_csl认国标() -> None:
    assert csl_for("参考文献采用 GB/T 7714 顺序编码制") == "china-national-standard-gb-t-7714-2015-numeric"
    assert csl_for("按国标著者-出版年制") == "china-national-standard-gb-t-7714-2015-author-date"
    assert csl_for("Chicago author-date") == "chicago-author-date"


# ══════════════════════════════ 三、查重：中文连续 13 字


def test_中文抄范文连续13字要被抓_标点变了也算() -> None:
    exemplar = {"某文": "教育扩张显著削弱了家庭背景对子代教育获得的影响，这一效应在扩招之后的队列中尤为明显。"}
    paper = "正如既有研究所言，教育扩张显著削弱了家庭背景，对子代教育获得的影响也随之变化。"
    hits = overlaps(paper, exemplar)
    assert hits and hits[0][2] == "13 个字", hits
    assert "教育扩张显著削弱了家庭背景" in hits[0][1]


def test_中文不足13字的重合不算抄() -> None:
    exemplar = {"某文": "教育扩张显著削弱了家庭背景这个说法流传已久。"}
    paper = "教育扩张显著削弱了家庭条件，其余论述完全不同。"      # 只重合 11 字
    assert overlaps(paper, exemplar) == []


def test_英文范文照旧按8词() -> None:
    exemplar = {"ex": "the class ceiling reflects accumulated advantage across generations of families"}
    paper = "We argue the class ceiling reflects accumulated advantage across generations of workers."
    hits = overlaps(paper, exemplar)
    assert hits and hits[0][2] == "8 个词"


# ══════════════════════════════ 四、check_paper 对中文稿


def test_中文稿超字数上限按字数拦() -> None:
    rules = StyleRules(journal="某刊", word_limit=1000)
    problems, _ = check_paper(_zh_paper(), rules, None)
    assert any("超过" in p and "上限 1000" in p for p in problems), problems


def test_中文稿表编号乱序要拦() -> None:
    problems, _ = check_paper(_zh_paper(tables="表 2 在前。表 1 在后。"), StyleRules(journal="某刊"), None)
    assert any("Table/表" in p and "[2, 1]" in p for p in problems), problems


def test_中文摘要引用与字数上限() -> None:
    rules = StyleRules(journal="某刊", abstract_no_citations=True, abstract_max_words=10)
    text = _zh_paper().replace("本文考察教育扩张的作用。", "本文考察教育扩张的作用（张三，2020）。")
    problems, _ = check_paper(text, rules, None)
    assert any("摘要里有引用" in p for p in problems), problems
    assert any("摘要" in p and "超过上限 10" in p for p in problems), problems
