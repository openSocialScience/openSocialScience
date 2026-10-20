"""想题目之前先读透：挑、拿、钉。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from oss.core.contracts import LiteratureCorpus, PaperRecord, ReadingCards
from oss.core.reading import LEVEL_ABSTRACT, LEVEL_FULL, LEVEL_META, check_cards, coverage_note, fetch, rank


def _corpus(records):
    return LiteratureCorpus(corpus_id="c", queries=["education expansion intergenerational mobility"],
                            records=records)


def _rec(pid, title, cites=10, abstract="", oa="", tldr=""):
    return PaperRecord(paper_id=pid, title=title, citation_count=cites, abstract=abstract, oa_url=oa, tldr=tldr)


# ─────────────────────────── 挑：主题匹配是门槛


def test_跟检索式一个词都不沾的被引再高也不进():
    c = _corpus([
        _rec("a", "Education expansion and mobility", cites=5),
        _rec("b", "Deep learning for protein folding", cites=5000),
    ])
    picked = [r.paper_id for r in rank(c, top=5)]
    assert picked == ["a"], "顶刊上的无关文章正是这样混进来的"


def test_同样相关时被引多的靠前():
    c = _corpus([
        _rec("low", "Education expansion and intergenerational mobility", cites=1),
        _rec("high", "Education expansion and intergenerational mobility revisited", cites=500),
    ])
    assert [r.paper_id for r in rank(c, top=2)][0] == "high"


# ─────────────────────────── 拿：来源层级要标清


def _pdf_bytes(text: str) -> bytes:
    from io import BytesIO

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=300, height=300)
    buf = BytesIO(); w.write(buf)
    return buf.getvalue()


class _Client:
    """假的 HTTP：按链接决定给什么。"""

    def __init__(self, table): self.table = table
    def get(self, url):
        kind = self.table.get(url, "404")
        if kind == "404":
            return httpx.Response(404, request=httpx.Request("GET", url))
        if kind == "html":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>",
                                  request=httpx.Request("GET", url))
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=kind,
                              request=httpx.Request("GET", url))
    def close(self): pass


def test_拿不到全文就退到摘要再退到元数据(tmp_path):
    c = _corpus([
        _rec("p1", "Education mobility A", oa="http://x/a.pdf", abstract="Abstract A"),
        _rec("p2", "Education mobility B", oa="http://x/b", abstract="Abstract B"),
        _rec("p3", "Education mobility C"),
    ])
    client = _Client({"http://x/a.pdf": _pdf_bytes("x"), "http://x/b": "html"})
    items = fetch(c, tmp_path / "reading", top=3, client=client)
    by = {i.paper_id: i for i in items}
    # 空白页 PDF 抽不出 3000 字 → 不算全文，退到摘要
    assert by["p1"].level == LEVEL_ABSTRACT and "不算全文" in by["p1"].reason
    assert by["p2"].level == LEVEL_ABSTRACT and "不是 PDF" in by["p2"].reason
    assert by["p3"].level == LEVEL_META
    assert (tmp_path / "reading" / "index.json").exists()
    note = coverage_note(items)
    assert "全文 0" in note and "全文不到一半" in note


# ─────────────────────────── 钉：局限引文必须在原文里


def _prepare(tmp_path: Path, text: str, level: str = LEVEL_FULL) -> Path:
    d = tmp_path / "reading"; d.mkdir()
    (d / "p1.txt").write_text("# T\n\n" + text, encoding="utf-8")
    (d / "index.json").write_text(json.dumps([{"paper_id": "p1", "level": level}]), encoding="utf-8")
    return d


def test_引文逐字在原文里才放行(tmp_path):
    d = _prepare(tmp_path, "We acknowledge that our data cannot distinguish selection from causation.")
    ok = ReadingCards(cards=[{"paper_id": "p1", "source_level": "fulltext",
                              "limitations": [{"quote": "our data cannot distinguish selection from causation"}]}])
    problems, _ = check_cards(ok, d)
    assert not problems

    bad = ReadingCards(cards=[{"paper_id": "p1", "source_level": "fulltext",
                               "limitations": [{"quote": "the authors admit the sample is too small to generalize"}]}])
    problems, _ = check_cards(bad, d)
    assert any("找不到" in p for p in problems), "凭印象写的局限必须拦"


def test_读了摘要不能自称读了全文(tmp_path):
    d = _prepare(tmp_path, "Short abstract only.", level=LEVEL_ABSTRACT)
    cards = ReadingCards(cards=[{"paper_id": "p1", "source_level": "fulltext"}])
    problems, _ = check_cards(cards, d)
    assert any("读了摘要不能说读了全文" in p for p in problems)


def test_本地没文本就先让去拿(tmp_path):
    d = tmp_path / "reading"; d.mkdir()
    cards = ReadingCards(cards=[{"paper_id": "nope", "source_level": "fulltext"}])
    problems, _ = check_cards(cards, d)
    assert any("先跑 oss lit fetch" in p for p in problems)


# ─────────────────────────── 挑：专词才算分，泛词不算；两万字倾倒不占便宜


def test_沾泛词的高被引文章排不过沾专词的():
    """验证跑：COVID 治理、希腊债务危机各沾「government」「health」这种到处都是的词，
    被引一乘压过了只沾「democratization」「mortality」的 Kudamatsu——前 20 篇只有 8 篇沾题。"""
    generic = "government public health policy response"
    c = LiteratureCorpus(corpus_id="c", queries=[
        "democratization infant mortality government accountability",
        "public health policy government response"], records=[
        _rec("kudamatsu", "Has democratization reduced infant mortality?", cites=8,
             abstract="democratization infant mortality accountability"),
        *[_rec(f"covid{i}", f"COVID {i} government public health policy", cites=500, abstract=generic)
          for i in range(6)],
    ])
    assert [r.paper_id for r in rank(c, top=1)] == ["kudamatsu"]


def test_两万字的摘要倾倒不因为什么都沾而排前():
    """语料里有些记录的「摘要」是全文倾倒，什么词都沾——只看前 2000 字。"""
    dump = " ".join(["constitutional law review"] * 300) + " democratization infant mortality accountability"
    c = LiteratureCorpus(corpus_id="c", queries=["democratization infant mortality government accountability"],
                         records=[
        _rec("real", "Democratization and infant mortality", cites=1, abstract="democratization infant mortality"),
        _rec("dump", "Comparative constitutional studies", cites=100, abstract=dump),
    ])
    assert [r.paper_id for r in rank(c, top=1)] == ["real"]


# ─────────────────────────── 点名：读哪几篇是判断，不是排名


def test_点名拿的按id或DOI找且报没找到的():
    from oss.core.reading import pick

    c = _corpus([_rec("W1", "A"),
                 PaperRecord(paper_id="10.1/x", title="B", citation_count=0, doi="10.1/X")])
    chosen, missing = pick(c, ["w1", "10.1/x", "nope"])
    assert [r.paper_id for r in chosen] == ["W1", "10.1/x"] and missing == ["nope"]


def test_点名的记录直接拿不经排名(tmp_path):
    c = _corpus([_rec("a", "Deep learning for protein folding"), _rec("b", "Education expansion")])
    items = fetch(c, tmp_path / "reading", records=[c.records[0]], client=_Client({}))
    assert [i.paper_id for i in items] == ["a"], "点名的跟检索式不沾也拿——判断是 skill 的"


def test_中文标题也能排进候选():
    """中文记录靠子串沾词，否则 `rank()` 里 `hit` 永远是空集、一条都排不进候选——
    导入与 `--zh` 拉回来的中文文献就永远读不到。"""
    c = LiteratureCorpus(corpus_id="c", queries=["农民工 市民化"], records=[
        _rec("zh", "农民工市民化的路径选择", cites=3),
        _rec("en", "Deep learning for protein folding", cites=5000),
    ])
    assert [r.paper_id for r in rank(c, top=5)] == ["zh"]


def test_两字中文检索词也能排进候选():
    """两字中文实词被 `len(w) > 2` 丢掉的话，`qwords` 是空集，`rank()` 直接返回空表。"""
    c = LiteratureCorpus(corpus_id="c", queries=["教育 生育"], records=[
        _rec("zh", "教育扩张与生育意愿", cites=3),
        _rec("en", "Deep learning for protein folding", cites=5000),
    ])
    assert [r.paper_id for r in rank(c, top=5)] == ["zh"]
