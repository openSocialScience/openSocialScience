"""引用的可查性：把「我说」变成「可查」。

**核对的前提是有东西可核。**核实过的现状（`docs/runs/gss20260824_v2-paper.md`）：
论文里提到 11 个人名，**没有参考文献列表、没有年份、没有 DOI**，
也没有任何记录说明「正文这句话对应语料里的哪一篇」。
所以既验证不了转述对不对，也发现不了引了一篇不存在的文献。

照搬数字那一套：**不许手打，只能引编号**。

  不许写：Breen 发现教育扩张促进了流动
  只能写：{{cite:W2079007828}} 发现教育扩张促进了流动
  渲染成：Breen (2010) 发现教育扩张促进了流动
  引不存在的：直接拒绝

两种「裸引用」分开处理：

  语料里**有**这个人却没用编号引  → 拒绝（跟手打数字同理）
  语料里**没有**这个人            → 警告（这处引用无据）

后者正是实测中 Blau 的情况：论文写了「Blau 与 Duncan 建立的地位获得模型」，
而这次检索一篇 Blau 都没召回。
"""

from __future__ import annotations

import pytest

from oss.core.contracts import AnalysisResults, Estimate, LiteratureCorpus, PaperRecord
from oss.core.findings import code_of
from oss.core.manuscript import _bare_citation_problems, check, render


def corpus() -> LiteratureCorpus:
    return LiteratureCorpus(
        corpus_id="c",
        records=[
            PaperRecord(paper_id="W2079007828", title="Educational Expansion and Social Mobility",
                        year=2010, authors=["Richard Breen"],
                        doi="10.1353/sof.2010.0076", source="openalex"),
            PaperRecord(paper_id="W2100", title="Maximally Maintained Inequality",
                        year=1993, authors=["Adrian E. Raftery", "Michael Hout"],
                        doi="10.2307/2112784", source="openalex"),
            PaperRecord(paper_id="W2200", title="Replicating Social Science Experiments",
                        year=2018, authors=["Colin Camerer", "Anna Dreber", "Felix Holzmeister"],
                        doi="10.1038/s41562-018-0399-z", source="openalex"),
        ],
    )


def results() -> AnalysisResults:
    return AnalysisResults(
        plan_id="p", n=100,
        estimates={"main": Estimate(term="x", coef=1.0, se=0.1, p=0.001)})


def paper(body: str, tail: str = "") -> str:
    return ("# 标题\n## 摘要\n概要。\n## 引言\n" + body +
            "\n## 数据与方法\n方法。\n## 结果\n{{result:main.coef}}\n"
            "## 讨论\n讨论。\n" + tail)


# ══════════════════════════════════ 引用能解析、能渲染


def test_按编号引用能渲染成人名年份() -> None:
    text = paper("{{cite:W2079007828}} 发现教育扩张促进了流动。")
    problems, _ = check(text, results(), corpus=corpus())
    assert not problems, problems
    assert "Breen (2010)" in render(text, results(), corpus=corpus())


def test_按DOI引用也认() -> None:
    """DOI 是论文的国际唯一编号，从检索结果里复制就行。"""
    text = paper("{{cite:10.2307/2112784}} 提出了最大限度维持的不平等。")
    assert "Raftery 与 Hout (1993)" in render(text, results(), corpus=corpus())


def test_三位以上作者用等() -> None:
    text = paper("{{cite:W2200}} 的复制研究给出了同一区间。")
    assert "Camerer 等 (2018)" in render(text, results(), corpus=corpus())


def test_引用不存在的文献要被拒绝() -> None:
    """**跟引用不存在的统计结果同理。**"""
    text = paper("{{cite:W9999}} 发现了什么。")
    problems, _ = check(text, results(), corpus=corpus())
    assert any("W9999" in p for p in problems), problems


def test_没有语料时引用要被拒绝() -> None:
    text = paper("{{cite:W2079007828}} 发现了什么。")
    problems, _ = check(text, results(), corpus=None)
    assert any("语料" in p for p in problems), problems


# ══════════════════════════════════ 参考文献列表自动生成


def test_参考文献列表只列被引用过的() -> None:
    """**两个方向都要堵**：引了一定在列表里，没引的不许出现。

    没引却列进去是很常见的凑数手法。
    """
    text = paper("{{cite:W2079007828}} 与 {{cite:W2100}} 的结论不同。", "\n## 参考文献\n{{references}}")
    out = render(text, results(), corpus=corpus())
    assert "Breen" in out and "Raftery" in out
    assert "Camerer" not in out, "没引用的文献不该出现在参考文献里"
    assert "10.1353/sof.2010.0076" in out, "参考文献要带 DOI，否则读者查不到"


def test_没写参考文献标记但引用了要警告() -> None:
    text = paper("{{cite:W2079007828}} 发现了什么。")
    _, warns = check(text, results(), corpus=corpus())
    assert any("参考文献" in w for w in warns), warns


# ══════════════════════════════════ 裸引用：语料里有他


def test_语料里有这个人却没用编号引要拒绝() -> None:
    """**手打的引用看起来跟真的一模一样**，跟手打数字是同一类问题。"""
    text = paper("Breen 对三国的比较发现教育扩张促进了流动。")
    problems, _ = check(text, results(), corpus=corpus())
    assert any("Breen" in p for p in problems), problems
    assert any("W2079007828" in p or "cite" in p for p in problems), "要告诉他该用哪个编号"


def test_已经用编号引了就不再报() -> None:
    text = paper("{{cite:W2079007828}} 发现教育扩张促进了流动。",
                 "\n## 参考文献\n{{references}}")
    problems, _ = check(text, results(), corpus=corpus())
    assert not any("Breen" in p for p in problems), problems


# ══════════════════════════════════ 裸引用：语料里没有他


def test_语料里没有的人名要警告() -> None:
    """**这正是实测中 Blau 的情况**：论文写了他，而检索一篇都没召回。"""
    text = paper("Blau 与 Duncan 建立的地位获得模型指出了两条路径。")
    _, warns = check(text, results(), corpus=corpus())
    assert any("Blau" in w for w in warns), warns
    assert any("无据" in w or "没有" in w for w in warns), warns


def test_统计学名词不要误伤() -> None:
    """`Cohen's d`、`Moulton 因子`、`GSS` 都不是引用。

    **误报会让人直接忽略这个检查**——跟共线警报天天响是同一个道理。
    """
    text = paper("本文用 Cohen's d 度量效应量，设计效应按 Moulton 因子计算，"
                 "数据来自 GSS，标准误用 HC1。")
    _, warns = check(text, results(), corpus=corpus())
    assert not [w for w in warns if "引用" in w], warns


def test_没有归属动词的大写词不算引用() -> None:
    """「本文的 SEI 指数」不是在引用一个叫 SEI 的人。"""
    text = paper("结果变量是 SEI 指数，数据结构是 repeated cross-section。")
    _, warns = check(text, results(), corpus=corpus())
    assert not [w for w in warns if "引用" in w], warns


# ══════════════════════════════════ 同名不同人：工具不能替人认定


def test_提示里要给出作者全名() -> None:
    """**实测踩到的真问题。**

    论文里的「Duncan」指的是 Otis Dudley Duncan（Blau & Duncan 1967 那本经典），
    而语料里的 Duncan 是 Greg J. Duncan——**不同的人**。
    工具当时提示「请写成 {{cite:W2097355889}}」，
    照着改就会引错文献——**这是工具制造出来的引用错误**。

    姓能匹配，人不一定是同一个。所以提示里必须给出**全名**，让人自己核。
    """
    c = corpus()
    c.records.append(PaperRecord(
        paper_id="W3300", title="Some Other Work", year=2005,
        authors=["Greg J. Duncan"], doi="10.1/x", source="openalex"))
    problems, _ = check(paper("Duncan 提出了地位获得模型。"), results(), corpus=c)
    assert any("Greg J. Duncan" in p for p in problems), (
        f"提示里必须写全名，否则人无法判断是不是同一个人：{problems}")


def test_同姓多人时要明说有歧义() -> None:
    """语料里同一个姓有好几个人时，**工具更不能替人认定**。"""
    c = corpus()
    c.records += [
        PaperRecord(paper_id="W4400", title="A", year=2001,
                    authors=["Greg J. Duncan"], doi="10.1/a", source="openalex"),
        PaperRecord(paper_id="W4401", title="B", year=1967,
                    authors=["Otis Dudley Duncan"], doi="10.1/b", source="openalex"),
    ]
    problems, _ = check(paper("Duncan 提出了地位获得模型。"), results(), corpus=c)
    hit = [p for p in problems if "Duncan" in p]
    assert hit, problems
    assert "同姓" in hit[0] or "歧义" in hit[0] or "多位" in hit[0], hit
    assert "Greg J. Duncan" in hit[0] and "Otis Dudley Duncan" in hit[0], \
        "两个人都要列出来，让人自己选"


# ══════════════════════════════════ 两个字母的姓、去掉变音记号的姓
#
# 评测（injection-v2）量出来的两个洞：「直呼作者的姓」22 个变体只拦下 18 个，漏的两类根因都在判据行上——
# 索引门槛把两个字母的姓整个挡在外面（中文作者的拉丁姓大量是两个字母），
# 匹配那一行是字面精确比对，语料写 Götmark、正文写 Gotmark 就完全对不上。


def one_author(*rows: tuple[str, str]) -> LiteratureCorpus:
    """（作者名、编号）→ 一人一篇的语料。"""
    return LiteratureCorpus(corpus_id="c", records=[
        PaperRecord(paper_id=pid, title=f"标题 {pid}", year=2020, authors=[a],
                    doi=f"10.1/{pid}", source="openalex")
        for a, pid in rows])


def test_两个字母的姓也查() -> None:
    """Xu、Li、He、Wu：中文作者的拉丁姓大量是两个字母，这是一个大类，不是边角。"""
    out = _bare_citation_problems("Xu 发现教育扩张促进了流动。", "", one_author(("Ming Xu", "W1")))
    assert out and code_of(out[0]) == "cite.bare_surname", out
    assert "Ming Xu" in out[0] and "W1" in out[0], "照旧要给全名与编号，让人自己核"


def test_两个字母的姓不吃碎片() -> None:
    """放宽到两个字母最容易造误报，边界要卡死。

    真实底板上抽到过 `Sg`、`Dj` 这类作者信息里的碎片（不是真姓），贴着汉字的不算命中；
    `US`、`AI`、`SE`、`ID` 这类缩写与专名进不了姓的索引。
    """
    assert not _bare_citation_problems("见附录Sg表。", "", one_author(("A Sg", "W1")))
    assert not _bare_citation_problems("US 的数据也支持这一点。", "", one_author(("B US", "W2")))
    assert _bare_citation_problems("Sg 发现了这一点。", "", one_author(("A Sg", "W1"))), \
        "前后是空白就照旧要报——判不了这个姓是真是假，只判边界"


def test_英文行里不查两个字母的姓() -> None:
    """英文里句首全是大写词，两个字母的大写词不携带任何信号。

    真实稿子上量到的误报（`runs/r20260825_qog_internet/paper.md`，英文稿，语料里有位 Lian An）：
    「An I-squared of ninety-three percent means…」被当成裸引用报了。
    三个字母以上的照旧中英文都查——那一条认的是语料里的真姓，不是「看着像不像」。
    """
    en = "An I-squared of ninety-three percent means that the variation is large."
    assert not _bare_citation_problems(en, "", one_author(("Lian An", "W1")))
    assert _bare_citation_problems("An 发现这一差异很大。", "", one_author(("Lian An", "W1"))), \
        "中文行里照旧要查"
    assert _bare_citation_problems(en.replace("An I-squared", "Breen I-squared"), "",
                                   one_author(("Richard Breen", "W2"))), "三个字母以上的不受这一条影响"


def test_去掉变音记号才对得上的姓降一档给提示() -> None:
    """折叠会把不同的姓并到一起（同形不同人的风险上升），硬拦就会制造工具自己造出来的引用错误——
    `_corpus_surnames` 的 docstring 记着的 Duncan 那次教训。"""
    out = _bare_citation_problems("Gotmark 指出过这一点。", "", one_author(("F Götmark", "W1")))
    assert out and code_of(out[0]) == "cite.bare_surname_folded", out
    assert "Gotmark" in out[0] and "Götmark" in out[0], "两种写法都要写出来，人才判得了是不是同一位"


def test_正文带变音记号语料不带也认得出() -> None:
    """反过来写也是同一件事：折叠在两边各做一次，不分谁带记号。"""
    out = _bare_citation_problems("Götmark 指出过这一点。", "", one_author(("F Gotmark", "W1")))
    assert out and code_of(out[0]) == "cite.bare_surname_folded", out


def test_原样命中仍是硬拦() -> None:
    """老行为不许退：一个字一个字对得上的，照旧硬拦。"""
    out = _bare_citation_problems("Götmark 指出过这一点。", "", one_author(("F Götmark", "W1")))
    assert out and code_of(out[0]) == "cite.bare_surname", out


def test_引过了就不再报_折叠这一路也一样() -> None:
    out = _bare_citation_problems("Gotmark 指出过这一点。", "{{cite:W1}}", one_author(("F Götmark", "W1")))
    assert not out, out


def test_同姓多位仍不指认() -> None:
    """踩过坑写下的行为，一字不动：列出全部同姓的人，不替人认定是哪一位。"""
    c = one_author(("Greg J. Duncan", "W1"), ("Otis Dudley Duncan", "W2"))
    out = _bare_citation_problems("Duncan 提出了地位获得模型。", "", c)
    assert out and code_of(out[0]) == "cite.bare_surname", out
    assert "Greg J. Duncan" in out[0] and "Otis Dudley Duncan" in out[0], out
    assert "多位" in out[0] and "不替你认定" in out[0], out


# ══════════════════════════════ 非英文文献进英文稿：罗马化 + 英译
#
# 依据 styles/social-forces/exemplars/：6 篇范文里**一个汉字都没有**，
# 非英文文献的写法是「原题（拉丁字母）[英译]」（范文里那条德文文献就是这样排的）。


def _zh_corpus():
    from oss.core.contracts import LiteratureCorpus, PaperRecord

    return LiteratureCorpus(records=[PaperRecord(
        paper_id="W9001", title="东北地区城市工作贫困群体就业质量研究", year=2015,
        authors=["尹海洁", "王翌佳"], venue="中国人口科学", doi="10.0000/zh.1",
        language="zh",
        title_romanized="Dongbei diqu chengshi gongzuo pinkun qunti jiuye zhiliang yanjiu",
        title_en="Employment quality of the urban working poor in Northeast China",
        venue_en="Chinese Journal of Population Science",
        authors_romanized=["Yin, Haijie", "Wang, Yijia"])])


def _bare_zh_corpus():
    from oss.core.contracts import LiteratureCorpus, PaperRecord

    return LiteratureCorpus(records=[PaperRecord(
        paper_id="W9001", title="东北地区城市工作贫困群体就业质量研究", year=2015,
        authors=["尹海洁"], venue="中国人口科学", doi="10.0000/zh.1", language="zh")])


EN_PAPER = ("# Title\n\n## Abstract\nWe cite {{cite:W9001}} here.\n\n## Introduction\nText.\n\n"
            "## Data and Methods\nText.\n\n## Results\nText.\n\n## Discussion\nText.\n\n"
            "## References\n\n{{references}}\n")


def test_姓名带逗号时按姓在前读() -> None:
    """罗马化的中文名是姓在前，按西文顺序取末词当姓会排成「Haijie, Yin」——整个倒过来。"""
    from oss.core.manuscript import _split_name

    assert _split_name("Yin, Haijie") == {"family": "Yin", "given": "Haijie"}
    assert _split_name("Kudamatsu, Masayuki") == {"family": "Kudamatsu", "given": "Masayuki"}
    assert _split_name("Masayuki Kudamatsu") == {"family": "Kudamatsu", "given": "Masayuki"}, "老写法照旧"
    assert _split_name("尹海洁") == {"family": "尹海洁"}, "中文名整个当姓"


def test_英文稿的中文文献排成罗马化加英译() -> None:
    from oss.core.manuscript import csl_json

    item = csl_json(EN_PAPER, _zh_corpus())[0]
    assert "Dongbei diqu chengshi" in item["title"] and "[Employment quality" in item["title"], item["title"]
    assert 'class="nocase"' in item["title"], "拼音要挡住 Chicago 的标题式大写"
    assert item["container-title"] == "Chinese Journal of Population Science"
    assert item["author"][0] == {"family": "Yin", "given": "Haijie"}


def test_中文稿里同一条原样排() -> None:
    from oss.core.manuscript import csl_json

    zh_paper = EN_PAPER.replace("We cite", "本文引用").replace("Text.", "正文。")
    item = csl_json(zh_paper, _zh_corpus())[0]
    assert item["title"] == "东北地区城市工作贫困群体就业质量研究", "中文稿不动"
    assert item["container-title"] == "中国人口科学"


def test_没填罗马化时报警告而不是拒绝() -> None:
    from oss.core.manuscript import cjk_references

    assert cjk_references(EN_PAPER, _bare_zh_corpus()), "英文稿里带汉字的条目要报出来"
    assert not cjk_references(EN_PAPER, _zh_corpus()), "填好了就不该再报"
    probs, warns = check(EN_PAPER, results(), corpus=_bare_zh_corpus())
    assert not [p for p in probs if "汉字" in str(p)], "警告不是拒绝——老运行目录还得能导出"
    assert [w for w in warns if "汉字" in str(w)], warns


def test_文本版参考文献也照同一规矩() -> None:
    out = render(EN_PAPER, results(), corpus=_zh_corpus())
    assert "Yin, Haijie" in out and "Dongbei diqu" in out, out[-400:]
    assert "尹海洁" not in out and "中国人口科学" not in out, out[-400:]
