"""文献检索的单元测试。

核心逻辑（放松、去重补齐、摘要还原、诊断）**全部离线可测**——
它们是纯函数，不该为了测它们去调 API。

真调 API 的那几个标了 `@pytest.mark.network`，默认跳过：
`pytest -m network` 才跑。
"""

from __future__ import annotations

import httpx
import pytest

from oss.core import literature as lit
from oss.core.contracts import PaperRecord
from oss.core.literature import (
    SourceUnavailable,
    _diagnose,
    dedupe,
    reconstruct_abstract,
    relax,
    search,
)
from oss.core.contracts import LiteratureCorpus


def rec(pid: str, **kw) -> PaperRecord:
    base = dict(paper_id=pid, title=f"Paper {pid}", source="test")
    return PaperRecord(**{**base, **kw})


# ------------------------------------------------- 0 命中放松：最要命的那条


def test_多个引号短语取交会被严格匹配卡死() -> None:
    """实测：两条式子各有几万命中，AND 起来交集为空。

        "internet use"                              → 31544
        "urban-rural income gap"                    →  1233
        "internet use" AND "urban-rural income gap" →     0

    放松就是去引号，让它走词干化的词袋匹配。
    """
    q = '"internet use" AND "urban-rural income gap"'
    assert relax(q) == "internet use urban-rural income gap"


def test_没有引号就放松不动() -> None:
    """已经是词袋了，再放松只会变成噪声。"""
    assert relax("internet use income") is None
    assert relax("") is None


def test_布尔算子被去掉但词保留() -> None:
    assert relax('"A" OR "B"') == "A B"
    assert relax('"A" NOT "B"') == "A B"


def test_刻意不改成OR() -> None:
    """`"A" OR "B"` 实测 32777 命中——那不是放松，是把检索变成噪声。"""
    assert "OR" not in (relax('"digital" AND "income"') or "")


# ------------------------------------------------- 概念块：块内 OR、块间 AND、命中不足丢最后一块


def test_概念块写法拆成块() -> None:
    q = '("compulsory schooling" OR "schooling reform") AND (fertility OR childbearing)'
    assert lit.parse_blocks(q) == [["compulsory schooling", "schooling reform"], ["fertility", "childbearing"]]


def test_块可以不带括号() -> None:
    assert lit.parse_blocks('("a b" OR c) AND fertility') == [["a b", "c"], ["fertility"]]


def test_旧写法每个词一块_引号短语整块() -> None:
    """裸词袋也拆成块，这样「降级」在两种写法下是同一个动作：丢最后一块。"""
    assert lit.parse_blocks('education fertility "compulsory schooling"') == \
        [["compulsory schooling"], ["education"], ["fertility"]]


def test_首尾的AND不当成词() -> None:
    """`AND x AND y AND` 是手写检索式的常见毛边。不剥掉的话 `AND x` 会整个当成一个同义词，
    再被 `build_openalex` 加引号送出去——一条谁也搜不到的短语。"""
    assert lit.parse_blocks("AND x AND y AND") == [["x"], ["y"]]


def test_NOT不支持_直接丢掉() -> None:
    """OpenAlex 的 `title_and_abstract.search` 不认 NOT。把 NOT 留着会变成一个搜不到的词，
    把它否定的词留着更糟——那是**反着搜**。连词带被否定项一起丢，当没写过。"""
    assert lit.parse_blocks("a NOT b AND c") == [["a"], ["c"]]
    assert lit.parse_blocks('a NOT "b c" AND d') == [["a"], ["d"]]


def test_AND_NOT写法不产生垃圾短语() -> None:
    """`a AND NOT b`：丢掉 NOT 和 b 之后留下两个挨着的 AND，不合并的话
    `AND AND` 会被当成一个同义词。"""
    assert lit.parse_blocks("a AND NOT b AND c") == [["a"], ["c"]]


def test_NOT括号组整组丢掉() -> None:
    assert lit.parse_blocks("a NOT (b OR c) AND d") == [["a"], ["d"]]


def test_尾部的NOT丢掉() -> None:
    """光杆 NOT 后面没东西可否定，直接丢。"""
    assert lit.parse_blocks("a AND b NOT") == [["a"], ["b"]]


def test_引号里的AND不是算子() -> None:
    """`"AND gate"` 是电路里的与门，不是布尔算子——引号里的东西不许被剥。"""
    assert lit.parse_blocks('"AND gate" AND circuit') == [["AND gate"], ["circuit"]]


def test_降档不会把NOT否定的词搜回来() -> None:
    """**归一化只能做一次。**`parse_blocks` 丢掉的 NOT 项，如果 `degrade_ladder` 拿原串去
    `relax()`，它会在去引号那一档整个长回来——后面几档全在**搜用户明确说不要的东西**。"""
    ladder = lit.degrade_ladder('a NOT "b c" AND d')
    assert ladder[0] == "a AND d"
    for rung in ladder:
        assert "b" not in rung.split() and '"b c"' not in rung, rung


def test_构造OpenAlex检索式_多词同义词加引号() -> None:
    """OpenAlex 的 OR 只绑相邻的单词：`compulsory schooling OR schooling reform` 会被读成
    compulsory AND (schooling OR schooling) AND reform。实测不加引号 45 条、加引号 44 条，
    看着差不多，但含义完全不同——多词同义词必须加引号。"""
    assert lit.build_openalex([["compulsory schooling", "schooling reform"], ["fertility"]]) == \
        '("compulsory schooling" OR "schooling reform") AND fertility'


def test_词袋引擎只拿每块第一个同义词() -> None:
    """Crossref / S2 走词袋相关性，把 OR AND 括号原样递过去只会多出噪声词。"""
    q = '("compulsory schooling" OR "schooling reform") AND (fertility OR childbearing)'
    assert lit.bag_of_words(q) == "compulsory schooling fertility"
    assert lit.bag_of_words("education fertility") == "education fertility"


def test_降档_概念块从最后一块丢_最少留两块() -> None:
    assert lit.degrade_ladder("(a OR b) AND (c OR d) AND (e OR f)") == [
        "(a OR b) AND (c OR d) AND (e OR f)",
        "(a OR b) AND (c OR d)",
    ]


def test_降档_超过四块直接跳到三块再两块() -> None:
    """逐块丢在八块时要打七次 API，匿名额度一天才 100 次。块多就直接跳。"""
    assert lit.degrade_ladder("(a) AND (b) AND (c) AND (d) AND (e) AND (f)") == [
        "a AND b AND c AND d AND e AND f",
        "a AND b AND c",
        "a AND b",
    ]


def test_降档_结构相同的档去重() -> None:
    """`"education" fertility` 去引号之后拆出来的块跟原式一模一样——**同一次检索打两遍**。
    按字符串去重看不出来（引号还在），得按拆出来的块去重。"""
    assert lit.degrade_ladder('"education" fertility') == ["education AND fertility"]


def test_降档_旧写法先去引号再丢词() -> None:
    """DECISIONS 4.8 那条实测坑：两个引号短语取交打 0，去引号 0→55。所以旧写法的第一档是去引号。"""
    ladder = lit.degrade_ladder('"internet use" AND "urban-rural income gap"')
    assert ladder[0] == '"internet use" AND "urban-rural income gap"'
    assert ladder[1] == "internet use urban-rural income gap"
    assert ladder[-1] == "internet AND use"


def test_降档_两块以内不降() -> None:
    assert lit.degrade_ladder("(a OR b) AND c") == ["(a OR b) AND c"]


def test_OpenAlex命中不足自动降档并留话(tmp_path, monkeypatch) -> None:
    """实测：八个词 4 条、三个词 219 条。命中不足 MIN_HITS 就往下一档，并在 note 里说清
    降过——**降档前的少命中不构成空白证据**。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        flt = req.url.params.get("filter", "")
        seen.append(flt)
        n = 3 if "(e OR f)" in flt else 50
        return httpx.Response(200, json={"meta": {"count": n},
                                         "results": [_oa_item("W1", "T1"), _oa_item("W2", "T2")][:min(n, 2)]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = lit.OpenAlexSource(client, lit.Cache(tmp_path))
    res = src.search("(a OR b) AND (c OR d) AND (e OR f)", 5)
    assert len(seen) == 2 and "(e OR f)" in seen[0] and "(e OR f)" not in seen[1]
    assert res.relaxed and res.hits_total == 50 and "降了 1 档" in res.note
    assert len(res.records) == 2


def test_OpenAlex命中够就不降(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"meta": {"count": 30}, "results": [_oa_item("W1", "T1")]})))
    res = lit.OpenAlexSource(client, lit.Cache(tmp_path)).search("(a OR b) AND (c OR d) AND e", 5)
    assert not res.relaxed and res.note == "" and res.hits_total == 30


def test_检索式只剩算子时不打API(tmp_path, monkeypatch) -> None:
    """`NOT` 归一化之后什么都不剩：空检索式送进 OpenAlex 是一次白花的额度
    （匿名一天 100 次），而且它返回的是「全库前 N 篇」——跟检索式毫无关系的语料。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")

    def handler(req: httpx.Request) -> httpx.Response:
        pytest.fail(f"检索式归一化后为空，不该打 API：{req.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    res = lit.OpenAlexSource(client, lit.Cache(tmp_path)).search("NOT", 5)
    assert res.records == [] and res.hits_total == 0 and "归一化后为空" in res.note


def test_检索式只剩算子时一个源都不跑(tmp_path) -> None:
    """OpenAlex 那道闸只挡得住它自己。`bag_of_words("NOT")` 同样是空串，
    于是 S2 的 `query=`（兜底时还有 Crossref 的 `query.bibliographic=`）照样打出去，
    回来的是「最近的随便什么论文」——空语料还不如假语料，它至少不装成检索结果。
    所以空检索式要在**聚合层**就拦下来。"""
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: pytest.fail(f"检索式归一化后为空，不该打任何源：{req.url}")))
    corpus = lit.search(["NOT"], client=client, cache=lit.Cache(tmp_path))
    assert corpus.records == [] and corpus.hits_by_query == {"NOT": 0}
    assert any("发现源一个都没跑" in n for n in corpus.notes)


def test_NOT会在notes里说明已丢掉(tmp_path, monkeypatch) -> None:
    """`_normalize` 把 NOT **连同被它否定的词**一起丢掉——检索式因此变宽了。
    不写进 notes 的话，用户以为排除生效了，实际拿到的是一份更宽的语料，
    而下游的「这个没人做过」全建立在它上面。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")

    def handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        if "openalex" in host:
            return httpx.Response(200, json={"meta": {"count": 30}, "results": []})
        if "crossref" in host:
            return httpx.Response(200, json={"message": {"items": []}})
        return httpx.Response(200, json={"data": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    corpus = lit.search(["a NOT b AND c"], client=client, cache=lit.Cache(tmp_path))
    assert any("NOT 不支持" in n for n in corpus.notes), corpus.notes


# -------------------------------------------------------------- 去重补齐


def test_同一篇论文的空缺被后来的源补齐() -> None:
    """Crossref 有权威元数据，OpenAlex 有摘要和引文——各有各的强项，
    直接丢掉重复记录是浪费。"""
    merged = dedupe([
        rec("a", doi="10.1/x", abstract="", year=None, source="crossref"),
        rec("b", doi="10.1/x", abstract="有摘要", year=2020, source="openalex"),
    ])
    assert len(merged) == 1
    assert merged[0].abstract == "有摘要"
    assert merged[0].year == 2020
    assert merged[0].source == "crossref", "补齐是单向的，先来的源不该被覆盖"


def test_已有的值不被覆盖() -> None:
    merged = dedupe([
        rec("a", doi="10.1/x", abstract="先来的摘要"),
        rec("b", doi="10.1/x", abstract="后来的摘要"),
    ])
    assert merged[0].abstract == "先来的摘要"


def test_被引数取大的() -> None:
    """某个源没同步到，不等于这篇论文没被引用过。"""
    merged = dedupe([
        rec("a", doi="10.1/x", citation_count=3),
        rec("b", doi="10.1/x", citation_count=97),
    ])
    assert merged[0].citation_count == 97


def test_没有doi时退回标题去重() -> None:
    merged = dedupe([rec("a", title="Same Title"), rec("b", title="same title")])
    assert len(merged) == 1


# ------------------------------------------------------------- 摘要还原


def test_倒排索引还原成文本() -> None:
    """OpenAlex 存的是倒排索引。不还原的话 abstract_coverage 永远是 0，
    而它是判断「能不能挖出方法缺陷类空白」的依据。"""
    assert reconstruct_abstract({"digital": [0], "divide": [1], "matters": [2]}) == (
        "digital divide matters"
    )
    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract({}) == ""


def test_重复出现的词按位置还原() -> None:
    assert reconstruct_abstract({"the": [0, 2], "gap": [1]}) == "the gap the"


# ------------------------------------------ 诊断：把数字翻译成后果


def diagnosed(**kw) -> list[str]:
    corpus = LiteratureCorpus(**kw)
    _diagnose(corpus, kw.pop("_failed", {}))
    return corpus.notes


def test_摘要覆盖低时说清后果而不是只报数字() -> None:
    """「摘要覆盖 29%」没信息量，「所以挖不出方法缺陷类空白」才有。"""
    notes = "\n".join(diagnosed(records=[rec("a")], abstract_coverage=0.29, citation_coverage=0.7))
    assert "29%" in notes and "方法缺陷" in notes


def test_摘要覆盖高时不报摘要警告() -> None:
    """摘要警告只看摘要覆盖，跟相关度无关。原先它嵌在低相关度分支里：
    覆盖 29% 但相关度正常不报，相关度差但覆盖 90% 反而报「只有 90%」。"""
    notes = "\n".join(diagnosed(
        records=[rec("a", abstract="education fertility", source="openalex"),
                 rec("b", abstract="schooling childbearing", source="crossref")],
        queries=["education fertility"], abstract_coverage=0.9, citation_coverage=0.9))
    assert "方法缺陷" not in notes and "只有 90%" not in notes


def test_相关度差但摘要覆盖高时不说只有九成带摘要() -> None:
    notes = "\n".join(diagnosed(
        records=[rec("a", abstract="flail chest injuries", source="openalex"),
                 rec("b", abstract="nitrogen on dairy farms", source="crossref")],
        queries=["education fertility"], abstract_coverage=0.9, citation_coverage=0.9))
    assert "零主题重合" in notes
    assert "只有 90%" not in notes


def test_引文覆盖为零时说明召回率探针不可执行() -> None:
    notes = "\n".join(diagnosed(records=[rec("a")], abstract_coverage=0.9, citation_coverage=0.0))
    assert "召回率探针" in notes


def test_源不可用要明说它不是空白证据() -> None:
    """0 条是证据，源挂了不是证据——不能拿来支撑「这是研究空白」。"""
    corpus = LiteratureCorpus(records=[rec("a")], abstract_coverage=0.9, citation_coverage=0.9)
    _diagnose(corpus, {"openalex": "额度用尽"})
    notes = "\n".join(corpus.notes)
    assert "openalex" in notes and "不是「没人做过」的证据" in notes


def test_放松过要说明零命中不构成空白证据() -> None:
    notes = "\n".join(
        diagnosed(records=[rec("a")], relaxed=True, abstract_coverage=0.9, citation_coverage=0.9)
    )
    assert "0 命中不构成空白证据" in notes
    assert "自动降档" in notes, "得说清是「降档」，不然对不上源那条 note 的说法"


def test_相关度低但没有零重合时不打空举例() -> None:
    """三个概念块、每条只沾到一块：相关度 0.33 该报，但一条「零重合」都没有——
    这时候原来的文案会打出「举例：。」这种空句子。"""
    q = '("compulsory schooling" OR "schooling reform") AND (fertility OR childbearing) AND (china OR india)'
    notes = "\n".join(diagnosed(
        records=[rec("a", abstract="compulsory schooling laws in europe", source="openalex"),
                 rec("b", abstract="fertility decline in the north", source="openalex")],
        queries=[q], abstract_coverage=0.9, citation_coverage=0.9))
    assert "只沾到" in notes
    assert "举例：。" not in notes


def test_某个源返回零条但服务正常要单独警告() -> None:
    """这不是「没人做过」，是检索式在那个源里坏了。"""
    corpus = LiteratureCorpus(
        records=[rec("a")], hits_by_source={"openalex": 0, "crossref": 12},
        abstract_coverage=0.9, citation_coverage=0.9,
    )
    _diagnose(corpus, {})
    assert any("检索式在这个源里可能是坏的" in n for n in corpus.notes)


def test_语料为空时明说下游判断无效() -> None:
    notes = "\n".join(diagnosed(records=[]))
    assert "语料为空" in notes and "无效" in notes


# ------------------------------------------------------------ 真调 API


@pytest.mark.network
def test_真检索能拿到东西() -> None:
    corpus = search(["digital divide income inequality"], limit=5)
    assert corpus.records
    assert corpus.hits_by_source
    assert all(r.title for r in corpus.records)


@pytest.mark.network
def test_引号短语取交会触发放松() -> None:
    """复现 DECISIONS 4.8 那条实测坑。"""
    corpus = search(['"internet use" AND "urban-rural income gap"'], limit=5)
    assert corpus.relaxed, "两个引号短语取交应当打 0 命中并触发放松"
    assert corpus.records, "放松之后应当能捞回东西"


@pytest.mark.network
def test_OpenAlex正常时语料里没有Crossref() -> None:
    """Crossref 退出发现循环后，正常情况下语料只来自 OpenAlex 与 S2。"""
    corpus = search(["social capital trust"], limit=5)
    if "openalex" in corpus.hits_by_source:
        assert "crossref" not in corpus.hits_by_source


# ─────────────────────────── 参考文献条目要带期刊、卷、期、页


def _bare(cls, name):
    src = cls.__new__(cls)
    src.name = name
    return src


def test_OpenAlex记录带期刊卷期页():
    from oss.core.literature import OpenAlexSource

    item = {"id": "https://openalex.org/W1", "display_name": "T", "publication_year": 2018,
            "authorships": [{"author": {"display_name": "A B"}}], "doi": "https://doi.org/10.1/x",
            "primary_location": {"source": {"display_name": "Journal of Political Economy"}},
            "biblio": {"volume": "127", "issue": "1", "first_page": "47", "last_page": "100"}}
    r = _bare(OpenAlexSource, "openalex")._to_record(item)
    assert (r.venue, r.volume, r.issue, r.pages) == ("Journal of Political Economy", "127", "1", "47–100")


def test_Crossref记录带期刊卷期页():
    from oss.core.literature import CrossrefSource

    item = {"title": ["T"], "issued": {"date-parts": [[2011]]}, "DOI": "10.1/y",
            "author": [{"given": "Curtis", "family": "Bell"}], "container-title": ["International Studies Quarterly"],
            "volume": "55", "issue": "3", "page": "625-646"}
    r = _bare(CrossrefSource, "crossref")._to_record(item)
    assert (r.venue, r.volume, r.issue, r.pages) == ("International Studies Quarterly", "55", "3", "625-646")


def test_只有起页时不编止页():
    from oss.core.literature import _pages

    assert _pages("12", None) == "12" and _pages("12", "34") == "12–34" and _pages(None, None) == ""


# ------------------------------------------------- OpenAlex 密钥语义 & 补录备胎（离线打桩）


def _oa_item(wid: str, title: str) -> dict:
    return {"id": f"https://openalex.org/{wid}", "display_name": title, "publication_year": 2020,
            "authorships": [], "cited_by_count": 1, "doi": f"https://doi.org/10.1/{wid}"}


def _crossref_msg(doi: str, title: str) -> dict:
    return {"message": {"DOI": doi, "title": [title], "issued": {"date-parts": [[2001]]},
                        "author": [{"given": "A", "family": "B"}], "container-title": ["J"]}}


def test_OpenAlex无密钥被拒时说清要去哪注册(tmp_path, monkeypatch) -> None:
    """2026-02-13 起 OpenAlex 要密钥：匿名 100 额度后 409/429。报出来的必须是「去配密钥」，
    不是一串 HTTP 文本。"""
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(409, text='{"error":"api key required"}')))
    src = lit.OpenAlexSource(client, lit.Cache(tmp_path), max_retries=1)
    with pytest.raises(SourceUnavailable) as e:
        src.search("education fertility", 5)
    assert "OPENALEX_API_KEY" in e.value.reason and "openalex.org/settings/api" in e.value.reason


def test_有密钥仍被拒时说的是核对密钥或额度(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(429, text='{"message":"Insufficient budget"}')))
    src = lit.OpenAlexSource(client, lit.Cache(tmp_path), max_retries=1)
    with pytest.raises(SourceUnavailable) as e:
        src.search("education fertility", 5)
    assert "额度" in e.value.reason and "OPENALEX_API_KEY" in e.value.reason


def test_按DOI补录_OpenAlex不可用时走Crossref(tmp_path, monkeypatch) -> None:
    """`oss lit add` 原先只走 OpenAlex，额度一断经典就补不进（评审记的「QoG 引不进语料」）。"""
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)

    def handler(req: httpx.Request) -> httpx.Response:
        if "openalex.org" in req.url.host:
            return httpx.Response(429, text='{"message":"Insufficient budget"}')
        return httpx.Response(200, json=_crossref_msg("10.1/x", "Classic"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rec_ = lit.fetch_by_doi("10.1/x", client=client, cache=lit.Cache(tmp_path))
    assert rec_ is not None and rec_.title == "Classic" and rec_.source == "crossref"
    assert rec_.venue == "J" and rec_.authors == ["A B"]


def test_按DOI补录_两边都不可用时一起报(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(lit.time, "sleep", lambda _s: None)  # 503 可重试，真退避要等 7 秒
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(503, text="down")))
    with pytest.raises(SourceUnavailable) as e:
        lit.fetch_by_doi("10.1/x", client=client, cache=lit.Cache(tmp_path))
    assert "Crossref" in e.value.reason


def test_按DOI补录_两边都查不到是None不是异常(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(404, text="{}")))
    assert lit.fetch_by_doi("10.1/nope", client=client, cache=lit.Cache(tmp_path)) is None


def test_按DOI补录_OpenAlex查不到而Crossref坏了时报Crossref的原因(tmp_path, monkeypatch) -> None:
    """OpenAlex 说「没这篇」不等于没这篇——Crossref 又坏了，得把 Crossref 的原因报出来，
    不能悄悄回一个 None 让上游以为查过了。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")

    def handler(req: httpx.Request) -> httpx.Response:
        if "openalex.org" in req.url.host:
            return httpx.Response(404, text="{}")
        return httpx.Response(503, text="down")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SourceUnavailable) as e:
        lit.fetch_by_doi("10.1/x", client=client, cache=lit.Cache(tmp_path))
    assert "HTTP 503" in e.value.reason


def test_Crossref非200是不可用(tmp_path) -> None:
    """**源不可用 ≠ 检索到 0 条**：Crossref 挂了得报出来，不能当成「没人做过」。"""
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(503, text="down")))
    with pytest.raises(SourceUnavailable) as e:
        lit.CrossrefSource(client, lit.Cache(tmp_path)).search("x", 5)
    assert "503" in e.value.reason


def test_OpenAlex列表端点404是不可用不是零命中(tmp_path, monkeypatch) -> None:
    """404 只在单篇查询上是「没这篇」。列表端点 404 是坏了——**源不可用 ≠ 检索到 0 条**。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(404, text="{}")))
    with pytest.raises(SourceUnavailable):
        lit.OpenAlexSource(client, lit.Cache(tmp_path), max_retries=1).search("education fertility", 5)


def test_Crossref列表端点404是不可用(tmp_path) -> None:
    """跟 OpenAlex 对称：`works/<doi>` 的 404 是「没这篇」，`works` 的 404 是源坏了。"""
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(404, text="{}")))
    with pytest.raises(SourceUnavailable):
        lit.CrossrefSource(client, lit.Cache(tmp_path)).search("education fertility", 5)


def test_oa_locations带上密钥(monkeypatch) -> None:
    """全文链接发现也走 OpenAlex：2026-02-13 之后不带密钥就是 409/429，
    而这个函数把非 200 当成「没有开放获取版本」——**全文会静悄悄地消失**。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    seen: list[bool] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append("api_key" in req.url.params)
        return httpx.Response(200, json={"best_oa_location": {"is_oa": True, "pdf_url": "https://x/a.pdf"},
                                         "locations": [], "open_access": {}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert lit.oa_locations("10.1/x", client) == ["https://x/a.pdf"]
    assert seen == [True]



def test_oa_locations没密钥就不带密钥参数(monkeypatch) -> None:
    """没配密钥时不能瞎塞一个空的 api_key——OpenAlex 会当成坏密钥直接拒。"""
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)

    def handler(req: httpx.Request) -> httpx.Response:
        assert "api_key" not in req.url.params
        return httpx.Response(200, json={"best_oa_location": {"is_oa": True, "pdf_url": "https://x/a.pdf"},
                                         "locations": [], "open_access": {}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert lit.oa_locations("10.1/x", client) == ["https://x/a.pdf"]


# ------------------------------------------------- Crossref 只兜底：OpenAlex 正常时不进发现循环


def _s2_empty() -> httpx.Response:
    return httpx.Response(200, json={"data": []})


def test_OpenAlex正常时Crossref不被调用(tmp_path, monkeypatch) -> None:
    """Crossref 是 DOI 注册处不是发现源：它返回的是 SSRN、会议摘要、书章节，七成没摘要。
    第一期实测语料六到九成来自它，而 OpenAlex 一份都出力了。有 OpenAlex 就不该再问它。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")

    def handler(req: httpx.Request) -> httpx.Response:
        if "crossref.org" in req.url.host:
            pytest.fail("OpenAlex 正常时不该打 Crossref")
        if "semanticscholar" in req.url.host:
            return _s2_empty()
        return httpx.Response(200, json={"meta": {"count": 30}, "results": [_oa_item("W1", "T1")]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    corpus = lit.search(["education fertility"], limit=5, client=client, cache=lit.Cache(tmp_path))
    assert [r.source for r in corpus.records] == ["openalex"]
    assert "crossref" not in corpus.hits_by_source


def test_OpenAlex不可用时Crossref兜底并说明(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)

    def handler(req: httpx.Request) -> httpx.Response:
        if "openalex.org" in req.url.host:
            return httpx.Response(409, text='{"error":"api key required"}')
        if "semanticscholar" in req.url.host:
            return _s2_empty()
        return httpx.Response(200, json={"message": {"items": [_crossref_msg("10.1/x", "Fallback")["message"]]}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    corpus = lit.search(["education fertility"], limit=5, client=client, cache=lit.Cache(tmp_path))
    assert [r.source for r in corpus.records] == ["crossref"]
    assert corpus.hits_by_source.get("crossref") == 1
    joined = " ".join(corpus.notes)
    assert "Crossref 兜底" in joined and "OPENALEX_API_KEY" in joined


def test_没配S2密钥就不问S2(tmp_path, monkeypatch) -> None:
    """没密钥的 S2 共用全局配额，实测每次 429：每条检索式白等三秒再等一次，还多一条警告。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    monkeypatch.delenv("S2_API_KEY", raising=False)

    def handler(req: httpx.Request) -> httpx.Response:
        if "semanticscholar" in req.url.host:
            pytest.fail("没配 S2_API_KEY 不该问 S2")
        return httpx.Response(200, json={"meta": {"count": 30}, "results": [_oa_item("W1", "T1")]})

    corpus = lit.search(["education fertility"], limit=5,
                        client=httpx.Client(transport=httpx.MockTransport(handler)), cache=lit.Cache(tmp_path))
    assert "semanticscholar" not in corpus.hits_by_source
    assert not any("semanticscholar" in n for n in corpus.notes)


def test_配了S2密钥才问S2(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    monkeypatch.setenv("S2_API_KEY", "s")
    asked: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if "semanticscholar" in req.url.host:
            asked.append(req.url.host)
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"meta": {"count": 30}, "results": [_oa_item("W1", "T1")]})

    corpus = lit.search(["education fertility"], limit=5,
                        client=httpx.Client(transport=httpx.MockTransport(handler)), cache=lit.Cache(tmp_path))
    assert asked and corpus.hits_by_source.get("semanticscholar") == 0


def test_环境事实不进notes() -> None:
    """「CORE 没配」「中文未覆盖」跟本次检索无关，每次重复只让 notes 变长；由 oss doctor 说一次。"""
    notes = "\n".join(diagnosed(records=[rec("a", abstract="x", source="openalex")],
                                queries=["a"], abstract_coverage=0.9, citation_coverage=0.9))
    assert "中文文献未覆盖" not in notes and "全文源只用了" not in notes


def test_S2密钥被拒是不可用不回退(tmp_path, monkeypatch) -> None:
    """网关拒了密钥就退回无密钥，是从「配了密钥的模式」掉进「已经决定不跑的模式」：
    没密钥的 S2 每次 429，而且给配了密钥的人报一条「去配密钥」。跟 OpenAlex 一样报不可用。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    monkeypatch.setenv("S2_API_KEY", "bad")

    def handler(req: httpx.Request) -> httpx.Response:
        if "semanticscholar" in req.url.host:
            if "x-api-key" not in req.headers:
                pytest.fail("被拒后不该退回无密钥再打")
            return httpx.Response(403, json={"message": "Forbidden"})
        return httpx.Response(200, json={"meta": {"count": 30}, "results": [_oa_item("W1", "T1")]})

    corpus = lit.search(["education fertility"], limit=5,
                        client=httpx.Client(transport=httpx.MockTransport(handler)), cache=lit.Cache(tmp_path))
    assert len([n for n in corpus.notes if "密钥被网关拒绝" in n]) == 1
    assert "semanticscholar" not in corpus.hits_by_source


def test_中途OpenAlex断了_兜底说明只点名后面的检索式(tmp_path, monkeypatch) -> None:
    """额度是跑到一半耗尽的：前面几条检索式有 OpenAlex，后面几条只有 Crossref。
    一句笼统的「本次由 Crossref 兜底」会让人以为整份语料都废了。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    monkeypatch.delenv("S2_API_KEY", raising=False)
    oa_calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal oa_calls
        if "openalex.org" in req.url.host:
            oa_calls += 1
            if oa_calls == 1:
                return httpx.Response(200, json={"meta": {"count": 30}, "results": [_oa_item("W1", "T1")]})
            return httpx.Response(429, json={"message": "Insufficient budget"})
        return httpx.Response(200, json={"message": {"items": [_crossref_msg("10.1/x", "Fallback")["message"]]}})

    corpus = lit.search(["education fertility", "schooling childbearing"], limit=5,
                        client=httpx.Client(transport=httpx.MockTransport(handler)), cache=lit.Cache(tmp_path))
    note = next(n for n in corpus.notes if "Crossref 兜底" in n)
    assert "schooling childbearing" in note and "1/2" in note
    assert "education fertility" not in note


# ------------------------------------------------- 滚雪球：往后拉参考文献、往前拉引用


def _oa_full(wid: str, title: str, doi: str = "", abstract: str = "") -> dict:
    d = _oa_item(wid, title)
    d["doi"] = f"https://doi.org/{doi}" if doi else None
    if abstract:
        d["abstract_inverted_index"] = {w: [i] for i, w in enumerate(abstract.split())}
    return d


def _snowball_handler(calls: list[str]):
    def handler(req: httpx.Request) -> httpx.Response:
        assert "openalex.org" in req.url.host, req.url
        flt = req.url.params.get("filter", "")
        calls.append(flt or str(req.url.path))
        if flt.startswith("cited_by:W1"):
            return httpx.Response(200, json={"results": [
                _oa_full("W10", "Ref one", "10.1/r1", "schooling fertility"),
                _oa_full("W11", "Ref two", "10.1/r2"),
                _oa_full("W1", "Seed itself", "10.1/seed"),      # 自己引自己：已在语料里，不重复进
            ]})
        if flt.startswith("cites:W1"):
            return httpx.Response(200, json={"results": [_oa_full("W20", "Citing one", "10.1/c1")]})
        if not flt and "10.1/cr" in str(req.url):  # by_doi 换 W 号：单篇端点，不带 filter
            return httpx.Response(200, json=_oa_full("W2", "Crossref-born seed", "10.1/cr"))
        return httpx.Response(200, json={"results": []})
    return handler


def test_滚雪球_两个方向都拉_继承阵营_标来路(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    calls: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(_snowball_handler(calls)))
    seed = rec("W1", doi="10.1/seed", camp="支持派", source="openalex")
    corpus = LiteratureCorpus(corpus_id="c", queries=["schooling fertility"], records=[seed])
    stats = lit.snowball(corpus, [seed], backward=50, forward=15, client=client, cache=lit.Cache(tmp_path))
    assert stats.added == 3 and stats.backward == 2 and stats.forward == 1 and stats.seeds == ["W1"]
    assert [r.paper_id for r in corpus.records] == ["W1", "W10", "W11", "W20"]
    by = {r.paper_id: r for r in corpus.records}
    assert by["W10"].via == "snowball:backward:W1" and by["W20"].via == "snowball:forward:W1"
    assert by["W10"].camp == "支持派" and by["W10"].screen == "" and by["W10"].source == "openalex"
    assert any(c.startswith("cited_by:W1") for c in calls) and any(c.startswith("cites:W1,type:article") for c in calls)
    assert corpus.snowball.rounds == 1 and corpus.snowball.added == 3


def test_滚雪球_新增后语料要重筛_覆盖率重算(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    client = httpx.Client(transport=httpx.MockTransport(_snowball_handler([])))
    seed = rec("W1", doi="10.1/seed", source="openalex", screen="纳入", abstract="x")
    corpus = LiteratureCorpus(corpus_id="c", queries=["q"], records=[seed], screened=True, abstract_coverage=1.0)
    lit.snowball(corpus, [seed], client=client, cache=lit.Cache(tmp_path))
    assert corpus.screened is False
    assert corpus.abstract_coverage == 0.5  # W1 与 W10 有摘要，W11、W20 没有


def test_滚雪球_没有W号的种子按DOI换一个(tmp_path, monkeypatch) -> None:
    """Crossref 兜底或 `lit add` 进来的记录 paper_id 是 DOI，得先去 OpenAlex 换成 W 号才能拉引文。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    calls: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(_snowball_handler(calls)))
    seed = rec("10.1/cr", doi="10.1/cr", source="crossref")
    corpus = LiteratureCorpus(corpus_id="c", queries=["q"], records=[seed])
    stats = lit.snowball(corpus, [seed], client=client, cache=lit.Cache(tmp_path))
    assert stats.seeds == ["W2"]


def test_滚雪球_换不到W号的种子跳过并说明(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: pytest.fail("没有 DOI 也没有 W 号，不该打 API")))
    seed = rec("title::only", source="crossref")
    corpus = LiteratureCorpus(corpus_id="c", queries=["q"], records=[seed])
    stats = lit.snowball(corpus, [seed], client=client, cache=lit.Cache(tmp_path))
    assert stats.seeds == [] and stats.added == 0
    assert any("跳过" in n for n in corpus.notes)
    assert corpus.snowball.rounds == 0  # 一个种子都没换到 W 号，这不算滚过一轮


def test_滚雪球_第二轮累计总账(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    client = httpx.Client(transport=httpx.MockTransport(_snowball_handler([])))
    seed = rec("W1", doi="10.1/seed", source="openalex")
    corpus = LiteratureCorpus(corpus_id="c", queries=["q"], records=[seed])
    lit.snowball(corpus, [seed], client=client, cache=lit.Cache(tmp_path))
    second = lit.snowball(corpus, [seed], client=client, cache=lit.Cache(tmp_path))
    assert second.added == 0  # 同一批已经在语料里
    assert corpus.snowball.rounds == 2 and corpus.snowball.added == 3 and corpus.snowball.seeds == ["W1", "W1"]


def test_滚雪球_OpenAlex不可用直接抛(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(409, text='{"error":"api key required"}')))
    seed = rec("W1", doi="10.1/seed", source="openalex")
    corpus = LiteratureCorpus(corpus_id="c", queries=["q"], records=[seed])
    with pytest.raises(SourceUnavailable):
        lit.snowball(corpus, [seed], client=client, cache=lit.Cache(tmp_path))


def test_滚雪球_新增后相关度重算(tmp_path, monkeypatch) -> None:
    """滚进来的可能整批跑题——按被引排在最前的常常是别的学科的巨头。
    相关度不跟着重算，语料就顶着滚之前那个漂亮数字一路走到空白地图。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")

    def handler(req: httpx.Request) -> httpx.Response:
        flt = req.url.params.get("filter", "")
        if flt.startswith("cited_by:W1"):
            return httpx.Response(200, json={"results": [
                _oa_full("W30", "Flail chest", "10.1/f1", "flail chest injuries ventilation"),
                _oa_full("W31", "Rib fixation", "10.1/f2", "surgical rib fixation outcomes"),
            ]})
        return httpx.Response(200, json={"results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    seed = rec("W1", doi="10.1/seed", source="openalex", abstract="schooling raises fertility")
    corpus = LiteratureCorpus(corpus_id="c", queries=["schooling fertility"],
                              records=[seed], topical_relevance=1.0)
    lit.snowball(corpus, [seed], client=client, cache=lit.Cache(tmp_path))
    assert corpus.topical_relevance < 1.0
    assert corpus.off_topic_count >= 1


def test_滚雪球_已有记录被补齐不重复(tmp_path, monkeypatch) -> None:
    """同一篇 Crossref 已经进过语料（有卷期页、没摘要），滚雪球从 OpenAlex 又拉到它一次。
    直接跳过就白白丢了摘要和被引数——按 `dedupe` 的规矩单向补齐。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")

    def handler(req: httpx.Request) -> httpx.Response:
        flt = req.url.params.get("filter", "")
        if flt.startswith("cited_by:W1"):
            item = _oa_full("W10", "Ref one", "10.1/r1", "schooling fertility")
            item["cited_by_count"] = 999
            return httpx.Response(200, json={"results": [item]})
        return httpx.Response(200, json={"results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    seed = rec("W1", doi="10.1/seed", source="openalex")
    twin = rec("10.1/r1", doi="10.1/r1", source="crossref")
    corpus = LiteratureCorpus(corpus_id="c", queries=["q"], records=[seed, twin])
    stats = lit.snowball(corpus, [seed], client=client, cache=lit.Cache(tmp_path))
    assert stats.added == 0 and stats.backward == 0
    same = [r for r in corpus.records if r.doi == "10.1/r1"]
    assert len(same) == 1
    assert same[0].paper_id == "10.1/r1"      # 补齐原记录，不是换成新的
    assert same[0].abstract == "schooling fertility" and same[0].citation_count == 999


def test_滚雪球_forward为0就不往前拉(tmp_path, monkeypatch) -> None:
    """`--forward 0` 是「只往后拉经典」；这时一次多余的 API 调用都不该打。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")

    def handler(req: httpx.Request) -> httpx.Response:
        flt = req.url.params.get("filter", "")
        if flt.startswith("cites:"):
            pytest.fail(f"forward=0 还去往前拉了：{flt}")
        return httpx.Response(200, json={"results": [_oa_full("W10", "Ref one", "10.1/r1")]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    seed = rec("W1", doi="10.1/seed", source="openalex")
    corpus = LiteratureCorpus(corpus_id="c", queries=["q"], records=[seed])
    stats = lit.snowball(corpus, [seed], forward=0, client=client, cache=lit.Cache(tmp_path))
    assert stats.backward == 1 and stats.forward == 0
    assert [r.paper_id for r in corpus.records] == ["W1", "W10"]


def test_滚雪球_只凭标题也去重(tmp_path, monkeypatch) -> None:
    """预印本与正式版在 OpenAlex 是两个 W 号，DOI 常常只有一边有——只剩标题能认亲。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")

    def handler(req: httpx.Request) -> httpx.Response:
        flt = req.url.params.get("filter", "")
        if flt.startswith("cited_by:W1"):
            return httpx.Response(200, json={"results": [_oa_full("W99", "REF ONE")]})
        return httpx.Response(200, json={"results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    seed = rec("W1", doi="10.1/seed", source="openalex")
    corpus = LiteratureCorpus(corpus_id="c", queries=["q"], records=[seed, rec("P9", title="Ref One")])
    stats = lit.snowball(corpus, [seed], client=client, cache=lit.Cache(tmp_path))
    assert stats.added == 0 and [r.paper_id for r in corpus.records] == ["W1", "P9"]


def test_滚雪球_DOI大小写不同也算同一篇(tmp_path, monkeypatch) -> None:
    """DOI 不区分大小写；语料里存的是哪种写法要看当初哪个源给的。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")

    def handler(req: httpx.Request) -> httpx.Response:
        flt = req.url.params.get("filter", "")
        if flt.startswith("cited_by:W1"):
            return httpx.Response(200, json={"results": [_oa_full("W98", "Different title", "10.1/R1")]})
        return httpx.Response(200, json={"results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    seed = rec("W1", doi="10.1/seed", source="openalex")
    corpus = LiteratureCorpus(corpus_id="c", queries=["q"], records=[seed, rec("P1", doi="10.1/r1")])
    stats = lit.snowball(corpus, [seed], client=client, cache=lit.Cache(tmp_path))
    assert stats.added == 0 and len(corpus.records) == 2


# ------------------------------------------------- 中文历史层：--zh 只打白名单源


def test_zh检索只打白名单源并标语言(tmp_path, monkeypatch) -> None:
    """中文检索不能满库打：OpenAlex 里 `language:zh` 的作品绝大部分来自低端刊与学位论文，
    不限源的话回来的是一堆噪声。所以 `--zh` 只在核心刊白名单的源记录里查。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    monkeypatch.delenv("S2_API_KEY", raising=False)
    monkeypatch.setattr(lit, "ZH_SOURCES", ["S1", "S2"])
    monkeypatch.setattr(lit, "ZH_JOURNALS", 25)
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        flt = req.url.params.get("filter", "")
        seen.append(flt)
        assert "language:zh" in flt and "primary_location.source.id:S1|S2" in flt
        return httpx.Response(200, json={"meta": {"count": 7}, "results": [_oa_item("W1", "农民工市民化研究")]})

    corpus = lit.search([], zh=["本土: 农民工 市民化"], limit=5,
                        client=httpx.Client(transport=httpx.MockTransport(handler)),
                        cache=lit.Cache(tmp_path))
    assert len(seen) == 1
    assert corpus.hits_by_source == {"openalex_zh": 1}
    assert [r.language for r in corpus.records] == ["zh"]
    assert [r.camp for r in corpus.records] == ["本土"]
    assert corpus.queries == ["农民工 市民化"] and corpus.camps == {"农民工 市民化": "本土"}
    assert corpus.hits_by_query == {"农民工 市民化": 1}
    assert any("2018" in n and "历史层" in n for n in corpus.notes)
    # 命中数写在 note 的开头：模型读 notes 时要一眼看见语料里到底有没有中文记录，
    # 否则得自己去翻 hits_by_source。
    assert any(n.startswith("中文历史层命中 1 条：") for n in corpus.notes)


def test_没有zh时不打白名单(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    monkeypatch.delenv("S2_API_KEY", raising=False)
    monkeypatch.setattr(lit, "ZH_SOURCES", ["S1", "S2"])

    def handler(req: httpx.Request) -> httpx.Response:
        if "language:zh" in req.url.params.get("filter", ""):
            pytest.fail("没给 --zh 不该打白名单")
        return httpx.Response(200, json={"meta": {"count": 30}, "results": [_oa_item("W1", "T1")]})

    corpus = lit.search(["education fertility"], limit=5,
                        client=httpx.Client(transport=httpx.MockTransport(handler)),
                        cache=lit.Cache(tmp_path))
    assert "openalex_zh" not in corpus.hits_by_source
    assert not any("历史层" in n for n in corpus.notes)


def test_白名单为空时不打API并提醒(tmp_path, monkeypatch) -> None:
    """白名单没建（脚本没跑过、资产文件缺了）时打出去的是**不限源**的中文检索——
    满库的 `language:zh` 噪声冒充历史层进语料。宁可 0 条，把「先跑脚本」说出来。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    monkeypatch.delenv("S2_API_KEY", raising=False)
    monkeypatch.setattr(lit, "ZH_SOURCES", [])

    def handler(req: httpx.Request) -> httpx.Response:
        pytest.fail("白名单为空时一个 API 都不该打")

    corpus = lit.search([], zh=["农民工 市民化"], limit=5,
                        client=httpx.Client(transport=httpx.MockTransport(handler)),
                        cache=lit.Cache(tmp_path))
    assert corpus.records == [] and corpus.hits_by_query == {"农民工 市民化": 0}
    assert any("白名单为空" in n and "build_zh_whitelist" in n for n in corpus.notes)


def test_zh检索的中文词加引号强制短语(tmp_path, monkeypatch) -> None:
    """**2026-09-05 三发对照**（同一批白名单源，各一次调用）：
    `农民工` 254 命中、`"农民工"` 81 命中、**倒序 `工民农` 也是 254**。
    倒序与正序一模一样，说明 OpenAlex 对中文是按**字袋**匹配的——不加引号的「农民工」
    等于「含农、民、工三个字」，「农民」的文章、「工人」的文章全算命中。加引号才是短语。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    seen: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: (seen.append(req.url.params.get("filter", "")),
                     httpx.Response(200, json={"meta": {"count": 3}, "results": []}))[1]))
    src = lit.OpenAlexSource(client, lit.Cache(tmp_path))
    src.search_zh("农民工 市民化", 5, ["S1"])
    assert '"农民工" AND "市民化"' in seen[0]


def test_zh检索式只剩算子时不打API(tmp_path, monkeypatch) -> None:
    """归一化后什么都不剩的式子送进去，`title_and_abstract.search:` 后面是空的，
    OpenAlex 回的是**白名单里的前 N 篇**——一次白花的额度，换回一堆跟题目无关的记录。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    monkeypatch.delenv("S2_API_KEY", raising=False)
    monkeypatch.setattr(lit, "ZH_SOURCES", ["S1"])

    def handler(req: httpx.Request) -> httpx.Response:
        pytest.fail("只剩算子的中文检索式不该打出去")

    corpus = lit.search([], zh=["AND OR"], limit=5,
                        client=httpx.Client(transport=httpx.MockTransport(handler)),
                        cache=lit.Cache(tmp_path))
    assert corpus.hits_by_query == {"AND OR": 0}
    assert any("历史层这条没跑" in n for n in corpus.notes)


def test_OpenAlex已经不可用时zh层不再撞(tmp_path, monkeypatch) -> None:
    """英文那半已经确认 OpenAlex 挂了，历史层走的是同一个源、同一把密钥——再撞一次
    只是把同一条「不可用」重报一遍，还多等一轮退避。"""
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("S2_API_KEY", raising=False)
    monkeypatch.setattr(lit, "ZH_SOURCES", ["S1"])
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        if "openalex.org" in req.url.host:
            calls += 1
            return httpx.Response(409, text='{"error":"api key required"}')
        return httpx.Response(200, json={"message": {"items": []}})

    corpus = lit.search(["education fertility"], zh=["农民工 市民化"], limit=5,
                        client=httpx.Client(transport=httpx.MockTransport(handler)),
                        cache=lit.Cache(tmp_path))
    assert calls == 1
    assert corpus.hits_by_query["农民工 市民化"] == 0
    # 同一条 reason 只报一次，不因为 openalex / openalex_zh 两个名字报两遍
    assert len([n for n in corpus.notes if "源不可用不是" in n]) == 1


def test_历史层零命中的提示不说检索式坏了(tmp_path, monkeypatch) -> None:
    """白名单只覆盖 2018 年前的核心刊，0 命中是**覆盖薄**的常态，不是检索式坏了。
    套用「检索式在这个源里可能是坏的」会让人去改一条本来没问题的中文检索式。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    monkeypatch.delenv("S2_API_KEY", raising=False)
    monkeypatch.setattr(lit, "ZH_SOURCES", ["S1"])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"meta": {"count": 0}, "results": []})

    corpus = lit.search([], zh=["农民工 市民化"], limit=5,
                        client=httpx.Client(transport=httpx.MockTransport(handler)),
                        cache=lit.Cache(tmp_path))
    joined = "\n".join(corpus.notes)
    assert "中文历史层 0 命中" in joined and "oss lit import" in joined
    assert "检索式在这个源里可能是坏的" not in joined


def test_白名单超过50个源要分批(tmp_path, monkeypatch) -> None:
    """一个 OpenAlex 过滤器最多塞 50 个值。51 个 id 一次发出去会被 400 顶回来——
    整条中文检索式静悄悄地变成 0 条。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.params.get("filter", ""))
        return httpx.Response(200, json={"meta": {"count": 4}, "results": []})

    src = lit.OpenAlexSource(httpx.Client(transport=httpx.MockTransport(handler)), lit.Cache(tmp_path))
    out = src.search_zh("农民工", 5, [f"S{i}" for i in range(51)])
    assert len(seen) == 2
    assert seen[0].count("|") == 49 and seen[1].count("|") == 0
    # 第 51 个 id 真的进了第二批。只数分隔符的话，把尾巴整个丢掉也照样是 49 + 0。
    assert "S50" in seen[1] and "S50" not in seen[0]
    assert out.hits_total == 8  # 两批的 meta.count 相加


def test_zh检索混合词也加引号(tmp_path, monkeypatch) -> None:
    """加引号的闸是「含不含汉字」，不是「全是不是汉字」。`GDP增长`、`COVID疫情`
    这种中英混写的词在 OpenAlex 那边照样按字袋拆（「增」「长」各算一命中），
    只放纯汉字词进闸的话，混合词就漏在闸外，回来的还是那三分之二噪声。"""
    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    seen: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: (seen.append(req.url.params.get("filter", "")),
                     httpx.Response(200, json={"meta": {"count": 3}, "results": []}))[1]))
    src = lit.OpenAlexSource(client, lit.Cache(tmp_path))
    src.search_zh("GDP增长 fertility", 5, ["S1"])
    assert '"GDP增长"' in seen[0]
    assert '"fertility"' not in seen[0]   # 西文词不动：加引号反而把它卡死


def test_源不可用按理由聚合但列全名(tmp_path, monkeypatch) -> None:
    """同一条 reason 只说一遍（`openalex` 与 `openalex_zh` 是同一个源、同一把密钥），
    但**名字要列全**：只报一个名字的话，看到「源 openalex 不可用」的人以为中文历史层
    还跑着，其实那半也没打出去。"""
    corpus = LiteratureCorpus(corpus_id="c", queries=["q"], records=[rec("P1")])
    _diagnose(corpus, {"openalex": "缺 key", "openalex_zh": "缺 key", "s2": "HTTP 429"})
    bad = [n for n in corpus.notes if "不可用：" in n]
    assert len(bad) == 2
    assert any("源 openalex、openalex_zh 不可用：缺 key" in n for n in bad)
    assert any("源 s2 不可用：HTTP 429" in n for n in bad)


def test_没给zh时OpenAlex挂了不记zh源(tmp_path, monkeypatch) -> None:
    """一条中文检索式都没给，历史层这一层根本不存在——把 `openalex_zh` 也记成
    「不可用」，诊断里就凭空多出一个从没打算跑的源。"""
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("S2_API_KEY", raising=False)
    monkeypatch.setattr(lit, "ZH_SOURCES", ["S1"])

    def handler(req: httpx.Request) -> httpx.Response:
        if "openalex.org" in req.url.host:
            return httpx.Response(409, text='{"error":"api key required"}')
        return httpx.Response(200, json={"message": {"items": []}})

    corpus = lit.search(["education fertility"], limit=5,
                        client=httpx.Client(transport=httpx.MockTransport(handler)),
                        cache=lit.Cache(tmp_path))
    assert not any("openalex_zh" in n for n in corpus.notes)


def test_白名单为空且OpenAlex也挂了时只说白名单(tmp_path, monkeypatch) -> None:
    """白名单空的时候历史层一次都没打，再报一句「源 openalex_zh 不可用」是把两个不相干的
    原因摆在一起——读的人不知道该去建表还是去修密钥。"""
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("S2_API_KEY", raising=False)
    monkeypatch.setattr(lit, "ZH_SOURCES", [])

    def handler(req: httpx.Request) -> httpx.Response:
        if "openalex.org" in req.url.host:
            return httpx.Response(409, text='{"error":"api key required"}')
        return httpx.Response(200, json={"message": {"items": []}})

    corpus = lit.search(["education fertility"], zh=["农民工 市民化"], limit=5,
                        client=httpx.Client(transport=httpx.MockTransport(handler)),
                        cache=lit.Cache(tmp_path))
    assert any("中文白名单为空" in n for n in corpus.notes)
    assert not any("openalex_zh" in n for n in corpus.notes)


def test_滚雪球_导入种子不打API(tmp_path) -> None:
    """导入的记录没有 OpenAlex 编号，也几乎都没有 DOI——`_work_id` 会为它去打一次
    `works/https://doi.org/…` 或按标题搜一次，回来必然是空。每个导入种子白花一次额度，
    换回一条「换不到 W 号」。它们从来就滚不了，不该出发。"""
    def handler(req: httpx.Request) -> httpx.Response:
        pytest.fail("导入种子没有 OpenAlex 编号，一次都不该打")

    corpus = LiteratureCorpus(queries=["q"], records=[])
    seed = PaperRecord(paper_id="imp_1", title="农民工市民化", via="import", source="import:ris")
    stats = lit.snowball(corpus, [seed],
                         client=httpx.Client(transport=httpx.MockTransport(handler)),
                         cache=lit.Cache(tmp_path))
    assert stats.added == 0 and stats.rounds == 0
    note = "\n".join(corpus.notes)
    assert "1 个导入种子没有 OpenAlex 编号" in note
