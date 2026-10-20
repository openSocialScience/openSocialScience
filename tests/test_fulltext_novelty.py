"""文献：全文的更多来源、按阵营出检索式、新颖性门。

评审第 7 条：「22 篇入选精读只拿到 1 篇全文……在这个基础上写 'nobody has …' 是不成立的——
你没有能力知道有没有人做过。这一条单独就足以让编辑退稿。」
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from oss.core import literature as lit
from oss.core import reading
from oss.core.contracts import AnalysisResults, Estimate, PaperRecord, SpecSummary
from oss.core.manuscript import check, novelty_claims


# ══════════════════════════════ 一、阵营


def test_阵营前缀能拆开_没有就原样() -> None:
    assert lit.split_camp("威权回应性: selectorate theory responsiveness") == ("威权回应性", "selectorate theory responsiveness")
    assert lit.split_camp("digital authoritarianism：censorship internet") == ("digital authoritarianism", "censorship internet")
    assert lit.split_camp("democracy AND health") == ("", "democracy AND health")
    assert lit.split_camp("https://x.org/a: b") == ("", "https://x.org/a: b")


def test_检索按阵营记命中并在阵营为零时提醒(monkeypatch) -> None:
    def fake_search(self, q, limit=20, *, year_from=None):
        recs = [] if "authoritarian" in q else [
            PaperRecord(paper_id=f"W_{q[:5]}", title=f"{q} paper", abstract="a", source=self.name)]
        return lit.SourceResult(records=recs, relaxed=False)

    for cls in (lit.OpenAlexSource, lit.SemanticScholarSource, lit.CrossrefSource):
        monkeypatch.setattr(cls, "search", fake_search)
    corpus = lit.search(["民主与健康: democracy health", "威权回应性: authoritarian responsiveness"], limit=5)
    assert corpus.camps == {"democracy health": "民主与健康", "authoritarian responsiveness": "威权回应性"}
    assert corpus.queries == ["democracy health", "authoritarian responsiveness"]
    assert corpus.hits_by_query["authoritarian responsiveness"] == 0
    assert all(r.camp == "民主与健康" for r in corpus.records)
    joined = " ".join(corpus.notes)
    assert "「威权回应性」一条都没有" in joined and "先换词重搜" in joined, corpus.notes


# ══════════════════════════════ 二、全文：落地页跟进、多个来源按顺序试


def _pdf_bytes(chars: int = 4000) -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    text = ("Democracy and health. " * (chars // 22))
    lines = [text[i:i + 80] for i in range(0, len(text), 80)]
    for start in range(0, len(lines), 60):
        page = doc.new_page()
        for j, line in enumerate(lines[start:start + 60]):
            page.insert_text((40, 40 + 12 * j), line, fontsize=8)
    return doc.tobytes()


def _client(routes: dict[str, tuple[int, str, bytes]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        status, ctype, body = routes.get(str(request.url), (404, "text/plain", b""))
        return httpx.Response(status, headers={"content-type": ctype}, content=body)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_落地页里的citation_pdf_url会被跟进() -> None:
    pdf = _pdf_bytes()
    html = b'<html><head><meta name="citation_pdf_url" content="https://x.org/paper.pdf"></head></html>'
    client = _client({"https://x.org/landing": (200, "text/html", html),
                      "https://x.org/paper.pdf": (200, "application/pdf", pdf)})
    text, why = reading._try_url("https://x.org/landing", client)
    assert text and not why and "Democracy and health" in text


def test_按顺序试所有来源_第一个能用的就停(monkeypatch) -> None:
    pdf = _pdf_bytes()
    monkeypatch.setattr(lit, "oa_locations", lambda doi, client: ["https://oa.org/2.pdf"])
    monkeypatch.setattr(lit, "unpaywall_pdf", lambda doi, client: ["https://up.org/3.pdf"])
    monkeypatch.setattr(lit, "core_pdf", lambda doi, client: [])
    rec = PaperRecord(paper_id="W1", title="t", doi="10.1/x", oa_url="https://own.org/1.pdf", abstract="abs")
    client = _client({"https://own.org/1.pdf": (403, "text/html", b""),
                      "https://oa.org/2.pdf": (200, "application/pdf", pdf)})
    corpus = lit.LiteratureCorpus(records=[rec])
    items = reading.fetch(corpus, Path(pytest.importorskip("tempfile").mkdtemp()), records=[rec], client=client)
    assert items[0].level == "fulltext", items[0]
    assert reading._candidates(rec, client) == ["https://own.org/1.pdf", "https://oa.org/2.pdf", "https://up.org/3.pdf"]


def test_全都拿不到时每个失败原因都记下来(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(lit, "oa_locations", lambda doi, client: ["https://oa.org/2.pdf"])
    monkeypatch.setattr(lit, "unpaywall_pdf", lambda doi, client: [])
    monkeypatch.setattr(lit, "core_pdf", lambda doi, client: [])
    rec = PaperRecord(paper_id="W1", title="t", doi="10.1/x", oa_url="https://own.org/1.pdf", abstract="abs")
    client = _client({"https://own.org/1.pdf": (403, "text/html", b""),
                      "https://oa.org/2.pdf": (200, "text/html", b"<html>no pdf here</html>")})
    items = reading.fetch(lit.LiteratureCorpus(records=[rec]), tmp_path, records=[rec], client=client)
    assert items[0].level == "abstract"
    assert "试了 2 个链接" in items[0].reason and "HTTP 403" in items[0].reason and "不是 PDF" in items[0].reason


def test_没配的全文源不去打它(monkeypatch) -> None:
    """没配就直接返回空，别拿一个空邮箱、空密钥去打——对面只会回 400。"""
    monkeypatch.delenv("UNPAYWALL_EMAIL", raising=False)
    monkeypatch.delenv("CORE_API_KEY", raising=False)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: pytest.fail(f"没配就不该打：{req.url}")))
    assert lit.unpaywall_pdf("10.1/x", client) == [] and lit.core_pdf("10.1/x", client) == []


# ══════════════════════════════ 三、新颖性门


def _paper(sentence: str) -> str:
    return f"""# T

## Abstract
Effect {{{{result:main.coef}}}} (n = {{{{result:main.n}}}}).

## Introduction
{sentence}

## Data and Methods
x

## Results
*Table 1. Main estimates.*
{{{{table:main}}}}

## Discussion
x
"""


def _res() -> AnalysisResults:
    return AnalysisResults(plan_id="p", n=100, estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                           spec=SpecSummary(estimator="ols_fe"))


def test_新颖性主张被识别() -> None:
    hits = novelty_claims(_paper("Nobody has put informational cost in the moderating position. "
                                 "To our knowledge, no study estimates this. This fills a gap."))
    assert len(hits) >= 1 and "obody has" in " ".join(h for _, h in hits)
    assert novelty_claims("We extend prior work on democracy and health.") == []
    assert novelty_claims("本文首次检验了这一机制。")


def test_没有精读记录却说首次要拒绝() -> None:
    problems, _ = check(_paper("This is the first study to test the interaction."), _res(), fulltext=None)
    assert any("新颖性主张站不住" in p and "没有精读记录" in p for p in problems), problems


def test_全文太少时说首次要拒绝_并给改法() -> None:
    problems, _ = check(_paper("No prior study has examined this moderation."), _res(), fulltext=(1, 22))
    assert any("只读到 1 篇全文" in p and "among the 1 studies" in p for p in problems), problems
    problems, _ = check(_paper("No prior study has examined this moderation."), _res(), fulltext=(4, 12))
    assert any("新颖性主张站不住" in p for p in problems), "4/12 不到一半也不行"


def test_全文够时只警告要说范围() -> None:
    problems, warnings = check(_paper("No prior study has examined this moderation."), _res(), fulltext=(12, 20))
    assert not any("新颖性" in p for p in problems)
    assert any("12/20 篇全文" in w for w in warnings), warnings


def test_不说首次就不管全文多少() -> None:
    problems, warnings = check(_paper("We extend prior work."), _res(), fulltext=(0, 22))
    assert not any("新颖性" in p for p in problems + warnings)


def test_不说首次也是新颖性主张() -> None:
    """**「首次」「填补空白」只是这一族里最直白的几个说法。**

    十一轮 CGSS 那次真跑，摘要写「中国是否也走了这条路，此前**缺少**覆盖足够长时段的
    经验回答」，引言写「**尚未**用长时段数据回答」——而那篇一条文献都没检索，
    参考文献是「（没有语料，生成不了）」。**这门当时判的是 pass。**

    漏掉的是同一件事换了个说法：不说「没人做过」，改说「缺少回答」。
    跨模型审稿抓到了，机械的门没抓到。
    """
    assert novelty_claims("中国是否也走了这条路，此前缺少覆盖足够长时段的经验回答。")
    assert novelty_claims("这一问题尚未用长时段数据回答。")
    assert novelty_claims("两支文献之间未见直接对话的文献。")
    assert novelty_claims("国内鲜有研究检验过这一机制。")
    assert novelty_claims("目前仍没有文献处理这一情形。")


def test_新颖性判据不许跨行拼() -> None:
    """不禁跨行的话，「尚未稳定，\\n其斜率估计」会被接成一条命中。

    十六篇历史稿子校准时真撞上过：那句话说的是估计不稳，不是没人做过。
    """
    assert novelty_claims("该趋势尚未稳定，\n其斜率估计仍在变动。") == []


def test_谈自己数据的缺乏不算新颖性主张() -> None:
    """局限里说「本文缺乏行业变量」是诚实交代，不是在声称空白。"""
    assert novelty_claims("本文缺乏行业变量，无法区分部门差异。") == []
    assert novelty_claims("样本量不足，功效偏低。") == []
