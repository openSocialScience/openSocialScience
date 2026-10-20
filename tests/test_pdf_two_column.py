"""双栏学术 PDF 的阅读顺序。

真跑 r20260916_netdem 撞出来的：抓到的那篇全文（Rød & Weidmann 2015，JPR）
抽出来是**左右两栏逐行交错**的——

    Conclusionvideos online will in the future have to get a government
    license containing the terms and conditions of produc-  While previous research has established that expansion of

后果比「读不顺」严重得多：读书卡的「局限」栏要求**逐字引原句**，而 `oss lit cards --check`
查的是「这句话在不在 reading/<id>.txt 里」。跨栏拼接出来的句子**确实在文件里**，
于是——

- 真正的原句引不出来（被右栏截断），**误拒**；
- 跨栏拼出来的假句子引得出来，**误收**，而且校验器一声不响。

误收比误拒危险：产出物上一个字都看不出来。

`page.get_text("text", sort=True)` 是按 (y, x) 整页排序的，双栏页上必然交错。
要按**栏**读：先把文本块按 x 分栏，再在栏内按 y 排。

**离线，不联网**：夹具用 PyMuPDF 现造。
"""

from __future__ import annotations

import pytest

pymupdf = pytest.importorskip("pymupdf", reason="没装 PyMuPDF，这条检查本来就不适用")

from oss.core.reading import _pdf_text

LEFT = [f"LEFT{i} this is the left column sentence number {i} written out in full." for i in range(6)]
RIGHT = [f"RIGHT{i} this is the right column sentence number {i} written out in full." for i in range(6)]


def _pdf(lines_left: list[str], lines_right: list[str] | None = None) -> bytes:
    """造一页 A4：左栏 x=55，右栏 x=320（单栏时右栏不写）。"""
    doc = pymupdf.open()
    page = doc.new_page()
    for i, t in enumerate(lines_left):
        page.insert_text((55, 90 + i * 34), t, fontsize=9)
    for i, t in enumerate(lines_right or []):
        page.insert_text((320, 90 + i * 34), t, fontsize=9)
    return doc.tobytes()


def test_双栏页先读完左栏再读右栏() -> None:
    text = _pdf_text(_pdf(LEFT, RIGHT))
    assert "LEFT5" in text and "RIGHT0" in text, text[:400]
    assert text.index("LEFT5") < text.index("RIGHT0"), (
        "右栏插到了左栏中间——跨栏拼接的假句子就是这么来的\n" + text[:600])


def test_栏内保持自上而下() -> None:
    text = _pdf_text(_pdf(LEFT, RIGHT))
    pos = [text.index(f"LEFT{i}") for i in range(6)]
    assert pos == sorted(pos), f"左栏内部乱序：{pos}"
    pos = [text.index(f"RIGHT{i}") for i in range(6)]
    assert pos == sorted(pos), f"右栏内部乱序：{pos}"


def test_整句连续_引得出来() -> None:
    """读书卡要逐字引原句。整句在文本里必须是连续的一段，中间不许插进对面栏。"""
    text = _pdf_text(_pdf(LEFT, RIGHT))
    assert LEFT[3] in text, "左栏第 3 句引不出来（被右栏截断了）"
    assert RIGHT[3] in text, "右栏第 3 句引不出来"


def test_单栏页没被改坏() -> None:
    text = _pdf_text(_pdf(LEFT))
    pos = [text.index(f"LEFT{i}") for i in range(6)]
    assert pos == sorted(pos), f"单栏页顺序被分栏逻辑搞乱了：{pos}"


def test_空页与坏文件不炸() -> None:
    assert _pdf_text(_pdf([])) .strip() == ""
    assert _pdf_text(b"not a pdf at all") == ""
