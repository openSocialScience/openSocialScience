"""数据加载：编码探测。

**变量标签是免费的语义信息**，读错了不会报错，只会让后面所有靠标签认变量的活失效。
"""

from __future__ import annotations

import pytest




# ────────────────────────── GBK 编码的 .dta（2026-09-25，CGSS2005 上撞到的）

def test_乱码判据_不看抛不抛异常() -> None:
    """**latin-1 能把任何字节解开，一个异常都不抛。**

    CGSS2005 的 `.dta` 是 GBK 存的。原先的编码探测只试 UTF-8 与 latin-1，
    靠「抛不抛 UnicodeDecodeError」选——latin-1 从不抛，于是拿到的是
    「问卷编号」→「ÎÊ¾í±àºÅ」这种**不报错的乱码**。

    乱码的后果不是报错，是**后面所有靠标签认变量的活全部失效**，
    而画像看起来一切正常。所以判据换成「解出来像不像话」。
    """
    from oss.core.loaders import _mojibake

    bad = "问卷编号".encode("gb18030").decode("latin-1")
    assert _mojibake([bad]), f"没认出乱码：{bad!r}"
    assert not _mojibake(["问卷编号", "省/自治区/直辖市名称"])
    assert not _mojibake(["sequential serial number", "year of survey"])
    assert not _mojibake([]), "空标签不算乱码"


def test_乱码判据_不误伤带重音的西文() -> None:
    """法语、德语的标签里有 é / ü，**但不会整段都是**——判据按占比，不按有没有。"""
    from oss.core.loaders import _mojibake

    assert not _mojibake(["Âge du répondant", "Niveau d'éducation", "Région"])


def test_GBK的dta读出来是中文标签() -> None:
    """端到端。**夹具用真文件**：pandas 写不出中文标签的 .dta
    （它的 `to_stata` 要求标签能用 latin-1 编码），所以造不出合成夹具——
    而这个 bug 恰恰只在真的 GBK 文件上出现。

    CGSS2005 不在仓库里（数据不进版本控制），本机没有就跳过。
    """
    pytest.importorskip("pyreadstat")
    from pathlib import Path

    from oss.core.loaders import load_any

    p = Path.home() / "Desktop/OSS 测试数据文件/raw/cgss2005.dta"
    if not p.exists():
        pytest.skip("本机没有 CGSS2005（数据不进仓库）")
    labels = load_any(p).variable_labels
    assert labels.get("serial") == "问卷编号", labels.get("serial")
    joined = "".join(list(labels.values())[:20])
    assert not _mojibake_of(joined), f"读出来还是乱码：{joined[:60]}"


def _mojibake_of(text: str) -> bool:
    from oss.core.loaders import _mojibake
    return _mojibake([text])
