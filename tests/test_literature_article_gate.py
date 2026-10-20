"""「这是不是一篇研究论文」这道门。

背景：2026-09-25 实测发现三个免费学术源都不收录中文 CSSCI 社科刊
（OpenAlex 中文历史层 0 条、`language:zh` 全网 86 条且不沾题、Crossref 返回的
不是真 CSSCI 刊），而网页搜索找得到也下得动。于是文献要走「skill 搜、工具验」
这条路——**这道门就是「验」那一半**。

门的判据来自一次真实校准（六篇中文研究论文 + 四篇通讯/法条/院系动态）：
**参考文献列表几乎完美地区分两类**，正例 28–98 条，反例全是 0。

这个文件盯的是**两类错误的不对称**：放进一条通讯后面还有 `lit screen`，
拒掉一篇真论文就彻底没了。所以误伤的测试比漏放的测试更多。
"""

from __future__ import annotations

from oss.core.literature import _MIN_REFS, _MIN_TEXT, looks_like_article


def _refs(n: int, head: str = "参考文献") -> str:
    """造一段像参考文献的尾巴。每行都够长、都带年份——判据认的就是这两样。"""
    items = "\n".join(
        f"作者{i}、合作者{i}，{1990 + i % 30}，《一个足够长的中文篇名{i}》，《某刊》第{i}期。"
        for i in range(n)
    )
    return f"\n{head}\n{items}\n"


def _body(chars: int = 6000) -> str:
    return "这是一段正文。" * (chars // 7)


# ───────────────────────────────── 认得出真论文


def test_带参考文献的中文论文放行() -> None:
    v = looks_like_article(_body() + _refs(30))
    assert v.ok and not v.undecidable, v.reason
    assert v.n_refs >= 30, v.n_refs


def test_参考文献后面带括号也认得出() -> None:
    """**这一条是校准时真栽过的。**

    第一版的正则要求 `参考文献` 独占一行（行尾锚点），而《社会》的全文里写的是
    `参考文献（Reference）`——六篇正例里有两篇因此被拒。
    锚点是那种「测试全过、真文件一跑就漏」的写法。
    """
    v = looks_like_article(_body() + _refs(30, head="参考文献（Reference）"))
    assert v.ok and not v.undecidable, v.reason


def test_英文References也认() -> None:
    for head in ("References", "REFERENCES", "Reference"):
        v = looks_like_article(_body() + _refs(20, head=head))
        assert v.ok and not v.undecidable, f"{head}: {v.reason}"


def test_参考文献不在末尾也认得出() -> None:
    """正文里先出现一次「参考文献」（比如「参考文献计量法」），真的那一段在后面。
    **每处出现都数一遍取最大**，不能只看最后一处，也不能只看第一处。"""
    t = _body(2000) + "\n本文的参考文献计量方法见下节。\n" + _body(2000) + _refs(40)
    v = looks_like_article(t)
    assert v.ok and v.n_refs >= 40, (v.n_refs, v.reason)


# ───────────────────────────────── 拦得住不是论文的


def test_活动通讯拒收() -> None:
    """「十万大米送乡亲 道德风尚洒人间」这种——OpenAlex 真的会把它当文献返回。"""
    v = looks_like_article(
        "十万大米送乡亲 道德风尚洒人间——南昌万寿宫捐赠大米活动散记。" * 200)
    assert not v.ok and not v.undecidable
    assert "参考文献" in v.reason


def test_长篇媒体文章也拒收() -> None:
    """**长度不是判据。**校准集里最长的反例是一篇三万字的媒体长文，0 条参考文献；
    而能判的正例最短只有一万七千字。拿长度当判据两边都会错。"""
    v = looks_like_article("行业观察：哪些因素会影响慈善行为。" * 2000)
    assert not v.ok and not v.undecidable, v.reason


def test_法规原文拒收() -> None:
    v = looks_like_article("第一条 为了保障适龄儿童、少年接受义务教育的权利。" * 200)
    assert not v.ok


# ───────────────────────────────── 判不了的一律放行（最要紧的一组）


def test_抽不出正文时放行而不是拒收() -> None:
    """**这一条是这道门的立场所在。**

    扫描件、下载截断、PDF 解析失败——这时候是「我看不出来」，不是「我看出来它不是」。
    本项目的规矩：会误报的门比没有门更糟，人会学会绕过去。
    """
    v = looks_like_article("标题页" * 10)
    assert v.ok, "抽不出正文时必须放行"
    assert v.undecidable, "而且要标成判不了，让调用方记警告"
    assert "判不了不等于判它不是" in v.reason


def test_判不了与判它不是要分得开() -> None:
    """两者的 `ok` 都可能是 True/False，**唯一可靠的区分是 `undecidable`**。
    调用方拿 `ok` 当唯一开关就会把「没判」当成「判过了」。"""
    short = looks_like_article("短" * 10)
    bad = looks_like_article("通讯正文。" * 2000)
    assert short.undecidable and not bad.undecidable
    assert short.ok and not bad.ok


def test_空文本不炸() -> None:
    v = looks_like_article("")
    assert v.ok and v.undecidable and v.n_chars == 0


# ───────────────────────────────── 阈值本身


def test_两个定数是量出来的不是拍的() -> None:
    """校准集：正例 28–98 条，反例 0 条。阈值必须落在这个缝里，
    而且**要靠近 0 那一边**——两类错误的代价不对称。"""
    assert 0 < _MIN_REFS < 28, f"{_MIN_REFS} 不在校准出来的缝里"
    assert _MIN_TEXT <= 17685, "能判的正例最短 17,685 字，门槛不能高过它"


def test_刚好够和差一条() -> None:
    assert looks_like_article(_body() + _refs(_MIN_REFS)).ok
    v = looks_like_article(_body() + _refs(_MIN_REFS - 1))
    assert not v.ok and not v.undecidable


# ───────────────────────── 真跑逮到的三条（2026-09-25，12 篇真论文的第一次导入）


def test_脚注体例的刊没有参考文献列表也要过() -> None:
    """**第一版在这里拒掉了一篇《山东大学学报》。**

    它整篇没有「参考文献」四个字，引用全在脚注里：
    `⑤张海波：《中国转型期公共危机治理》，北京：社会科学文献出版社，2012 年，第66 页。`
    《中国社会科学》这一系的人文社科刊大多如此。

    「研究论文必然带参考文献列表」这句话，**对中国社科的一整类刊是错的**——
    判据得能认第二种引用体例。
    """
    notes = "".join(
        f"{i}张海波：《中国转型期公共危机治理：理论框架与现实路径{i}》，"
        f"北京：社会科学文献出版社，{2000 + i % 20} 年，第{i}页。\n"
        for i in range(30)
    )
    v = looks_like_article(_body() + notes)
    assert v.ok and not v.undecidable, v.reason
    assert v.n_refs == 0, "本来就没有参考文献标题"
    assert v.n_cites >= 20, v.n_cites


def test_全角数字的年份要认() -> None:
    """中文期刊 PDF 大量用全角排版：
    `［１］　陈秉正．网络互助［Ｊ］．中国保险，２０２０，（８）．`
    只认半角的话，这一整类 PDF 一篇都过不了。
    """
    items = "\n".join(
        f"［{i}］　作者{i}．一个足够长的中文篇名{i}［Ｊ］．某刊，２０{i:02d}，（{i}）．"
        for i in range(10)
    )
    v = looks_like_article(_body() + "\n参考文献］\n" + items)
    assert v.ok and v.n_refs >= 5, (v.n_refs, v.reason)


def test_抽文抽坏的判不了而不是拒收() -> None:
    """PDF 用嵌入字体时，抽出来可能是私用区码位而不是真字符。
    校准集里那篇双栏综述 7.34% 的字是 `\\ue5d2` 这类，年份被抽成 `2 0\\n1 2`。

    **它不是不像论文，是读不出来。**靠调阈值去硬判一份读不出来的文档，
    正是本项目反复栽的那种跟头。
    """
    broken = ((chr(0xE5D2) + chr(0xE5CF)) * 300) + ("正文。" * 500)
    v = looks_like_article(broken)
    assert v.ok and v.undecidable, v.reason
    assert "私用区" in v.reason and "读不出来" in v.reason


def test_干净正文里偶有私用区字符不算抽坏() -> None:
    """占比判据，不是有无判据。真论文里偶尔混进一两个私用区码位很常见。"""
    v = looks_like_article(_body() + chr(0xE5D2) * 3 + _refs(30))
    assert v.ok and not v.undecidable, v.reason


def test_扫描件OCR坏了的判不了而不是拒收() -> None:
    """**第二批真论文又逮到一个。**《社会科学》2013 年那篇社会信任，
    PDF 是扫描件、OCR 质量很差：

        改革开放以来的中国社会转型/型塑0 了……   （书名号成了 / 和 0）
        soeial tru st ... dife re nees             （social trust / differences）

    私用区字符是 **0**，上一条判据抓不到；而它 7515 个中文字里
    **一个中文标点都没有**——中文文章不可能这样。

    校准集里其余 27 份的中文标点密度是 0.026–0.133，这一篇是 0.000。
    """
    broken = "改革开放以来的中国社会转型/型塑0 了具有转型嵌入性0 的社会信任的时代特征" * 120
    v = looks_like_article(broken)
    assert v.ok and v.undecidable, v.reason
    assert "中文标点" in v.reason and "OCR" in v.reason


def test_英文文献不因为没有中文标点被判成抽坏() -> None:
    """判据只在**中文字够多**时才查——英文文献本来就没有中文标点。"""
    en = "This paper examines the relationship between religion and giving. " * 200
    v = looks_like_article(en + "\nReferences\n" + "\n".join(
        f"Smith, J. ({1990 + i}). A sufficiently long English article title {i}. Journal of Things, {i}(2)."
        for i in range(20)))
    assert v.ok and not v.undecidable, v.reason


def test_正常中文论文的标点密度远在门槛之上() -> None:
    """门槛 0.005，而真论文实测 0.026–0.133——**留了五倍以上的余量**。"""
    from oss.core.literature import _MIN_ZH_PUNCT_RATE

    assert _MIN_ZH_PUNCT_RATE < 0.026 / 5, "余量不足五倍，扫描质量稍差的真论文会被误判"
