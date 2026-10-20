"""知网、万方的导出文件 → 语料记录。四种格式各一份最小样本，字段对齐到 PaperRecord。"""

from __future__ import annotations

import pytest

from oss.core import importers

RIS = """TY  - JOUR
AU  - 张三
AU  - 李四
TI  - 数字经济对城乡收入差距的影响
JO  - 经济研究
PY  - 2021
VL  - 56
IS  - 3
SP  - 45
EP  - 60
AB  - 本文基于省级面板数据……
KW  - 数字经济
DO  - 10.19744/j.cnki.11-1235/f.2021.0033
ER  -

TY  - JOUR
AU  - Wang, Wu
TI  - Rural migrants and citizenization
JO  - Chinese Sociological Review
PY  - 2019
ER  -
"""

ENDNOTE = """%0 Journal Article
%A 王五
%A 赵六
%T 农民工市民化的路径选择
%J 社会学研究
%D 2014
%V 29
%N 2
%P 118-140
%X 本文讨论……
%K 农民工; 市民化
%R 10.19934/j.cnki.shxyj.2014.02.006

%0 Journal Article
%A 钱七
%T 另一篇
%J 社会
%D 2016
"""

REFWORKS = """RT Journal Article
A1 孙八
A1 周九
T1 义务教育与生育决策
JF 人口研究
YR 2012
VO 36
IS 4
SP 3
OP 15
AB 利用普查数据……
DO 10.19604/j.cnki.rkyj.2012.04.001

RT Journal Article
A1 吴十
T1 第二篇
JF 中国人口科学
YR 2010
"""

NOTEEXPRESS = """{Reference Type}: Journal Article
{Author}: 郑一
{Author}: 冯二
{Title}: 社会资本与信任
{Journal}: 管理世界
{Year}: 2008
{Volume}: 24
{Issue}: 7
{Pages}: 10-22
{Abstract}: 本文……

{Reference Type}: Journal Article
{Author}: 陈三
{Title}: 再一篇
{Journal}: 开放时代
{Year}: 2005
"""


@pytest.mark.parametrize("text,fmt", [(RIS, "ris"), (ENDNOTE, "endnote"), (REFWORKS, "refworks"), (NOTEEXPRESS, "noteexpress")])
def test_四种格式都认得出并解析出两条(text, fmt) -> None:
    assert importers.detect_format(text) == fmt
    recs = importers.parse_export(text)
    assert len(recs) == 2
    first = recs[0]
    assert first.title and first.venue and first.year
    assert first.via == "import" and first.source == f"import:{fmt}"
    assert first.language == "zh"


def test_RIS字段对齐() -> None:
    r = importers.parse_export(RIS)[0]
    assert r.authors == ["张三", "李四"] and r.venue == "经济研究" and r.year == 2021
    assert r.volume == "56" and r.issue == "3" and r.pages == "45–60"
    assert r.doi == "10.19744/j.cnki.11-1235/f.2021.0033" and r.paper_id == r.doi
    assert r.abstract.startswith("本文基于")


def test_没有DOI的记录编号稳定() -> None:
    a = importers.parse_export(ENDNOTE)[1]
    b = importers.parse_export(ENDNOTE)[1]
    assert a.paper_id == b.paper_id and a.paper_id.startswith("imp_")


def test_英文记录语言判en() -> None:
    assert importers.parse_export(RIS)[1].language == "en"


def test_EndNote与RefWorks的页码合并() -> None:
    assert importers.parse_export(ENDNOTE)[0].pages == "118–140"
    assert importers.parse_export(REFWORKS)[0].pages == "3–15"


def test_认不出的文本报错() -> None:
    with pytest.raises(importers.UnknownFormat):
        importers.parse_export("这不是任何导出格式\n第二行")


def test_BOM与CRLF都能读() -> None:
    text = "\ufeff" + RIS.replace("\n", "\r\n")
    assert len(importers.parse_export(text)) == 2


def test_EndNote的标点标签不粘进摘要() -> None:
    """知网 EndNote 导出的 ISSN 是 `%@`，还有 `%!`、`%#` 等标点标签；
    行正则只认字母数字，它们就被当成续行粘进上一条摘要。"""
    text = "%0 Journal Article\n%T 网络首发的一篇\n%D 2023\n%X 这是摘要。\n%@ 0577-9154\n"
    assert importers.parse_export(text)[0].abstract == "这是摘要。"


def test_拼接的导出文件内部BOM也能切开() -> None:
    """知网一次最多导 500 条，用户把几份拼起来；中间那几个 BOM 只 lstrip 是清不掉的，
    带 BOM 的 `%0` 不在行首就切不开，整个第二份会并进最后一条记录。"""
    assert len(importers.parse_export("\ufeff" + ENDNOTE + "\ufeff" + ENDNOTE)) == 4


def test_RefWorks续行以大写开头也不当标签() -> None:
    """RefWorks 的标签后面必须有空格；`AI治理` 这种续行不是标签，别把标题截断。"""
    text = "RT Journal Article\nT1 增长与\nAI治理\nJF 经济研究\nYR 2020\n"
    assert importers.parse_export(text)[0].title == "增长与AI治理"


def test_NoteExpress自定义字段不粘进摘要() -> None:
    """NoteExpress 的自定义字段带数字（`{Custom 1}`），标签名只认字母就会漏认成续行。"""
    text = ("{Reference Type}: Journal Article\n{Title}: 一篇\n{Journal}: 经济研究\n"
            "{Year}: 2019\n{Abstract}: 这是摘要。\n{Custom 1}: 北大核心\n")
    assert importers.parse_export(text)[0].abstract == "这是摘要。"


def test_只有起页时页码里的横线也归一() -> None:
    """有的导出把起止页整段塞进 SP，没有 EP。"""
    text = "TY  - JOUR\nTI  - 只有起页\nJO  - 经济研究\nPY  - 2020\nSP  - 45-60\nER  -\n"
    assert importers.parse_export(text)[0].pages == "45–60"


def test_DOI去掉前缀与尾点() -> None:
    """导出里的 DOI 常带 `http://dx.doi.org/` 前缀和句末的点，不去掉就去不了重。"""
    text = "%0 Journal Article\n%T 带前缀的 DOI\n%D 2012\n%R http://dx.doi.org/10.1234/abc.2012.01.\n"
    r = importers.parse_export(text)[0]
    assert r.doi == "10.1234/abc.2012.01" and r.paper_id == r.doi


def test_RIS的JO是刊名缩写JF才是全名() -> None:
    """RIS 里 JO 是缩写、JF 是全名；两个都在时要全名。"""
    text = "TY  - JOUR\nTI  - 缩写与全名并存\nJO  - J. Econ. Res.\nJF  - 经济研究\nPY  - 2020\nER  -\n"
    assert importers.parse_export(text)[0].venue == "经济研究"


def test_中文摘要折行不塞空格() -> None:
    """中文不靠空格断词，折行处补空格会改字面——而字面进 `imp_` 编号的哈希。"""
    text = "%0 Journal Article\n%T 折行的摘要\n%D 2021\n%X 本文利用数据，\n考察……\n"
    assert importers.parse_export(text)[0].abstract == "本文利用数据，考察……"


def test_中英相接不加空格() -> None:
    """折行接缝只要**有一边**是中日韩字，就不补空格：中文里夹的数字与外文词
    （`本文利用2019` 接 `年数据`、`GDP` 接 `，增长`）两边不同类，要求「都是中文」就补出了
    原文没有的空格——而字面进 `imp_` 编号的哈希，去重会漏。"""
    assert importers._join("本文利用2019", "年数据") == "本文利用2019年数据"
    assert importers._join("GDP", "，增长") == "GDP，增长"
    assert importers._join("the first", "half") == "the first half"


def test_破折号引号处续行不加空格() -> None:
    """标题常折在破折号处（`主标题——副标题`），引号、省略号同理：这些是中文排版的一部分，
    接缝补空格就改了字面。"""
    text = "%0 Journal Article\n%T 主标题\n——副标题\n%D 2021\n"
    assert importers.parse_export(text)[0].title == "主标题——副标题"


def test_页码起页已含止页不重复() -> None:
    """有的 RefWorks 导出把整段页码塞进 SP，OP 又只给止页——直接拼会得到 `3-15–15`。"""
    text = "RT Journal Article\nT1 页码重复\nJF 人口研究\nYR 2012\nSP 3-15\nOP 15\n"
    assert importers.parse_export(text)[0].pages == "3–15"


def test_英文弯引号处续行仍加空格() -> None:
    """弯引号两边是西文时照旧断词：`a ‘new’` 接 `approach` 中间的空格不能省。
    接缝上的标点只在中文排版里才意味着「不该有空格」。"""
    assert importers._join("a ‘new’", "approach") == "a ‘new’ approach"


def test_日文韩文标题各判ja_ko() -> None:
    """四分：假名 → ja，谚文 → ko，汉字 → zh，其余 en。**解析器不做取舍**，
    产出照旧四种语言都在，要不要它们是 `import_file` 的事。

    假名先判：日文标题里的汉字跟中文的是同一批码位（`日本の社会保障` 里的 `日本`），
    先看汉字就把日文全判成 zh 了。纯汉字写的日文标题分不出来，仍会当中文进来。
    """
    text = ("TY  - JOUR\nTI  - 日本の社会保障\nPY  - 2020\nER  -\n\n"
            "TY  - JOUR\nTI  - 한국 사회\nPY  - 2020\nER  -\n")
    assert [r.language for r in importers.parse_export(text)] == ["ja", "ko"]


def test_人名间隔号不算日文() -> None:
    """U+30FB「・」在 Unicode 里归片假名块，但中文标题里它是**外国人名的间隔号**
    （马克斯・韦伯）。把它算成假名，凡是提到外国学者的中文题录都判 ja、被导入挡掉。"""
    assert importers._language("马克斯・韦伯的社会学思想") == "zh"
    assert importers._language("马克斯·韦伯") == "zh"       # U+00B7 那个中点
    assert importers._language("カタカナの研究") == "ja"      # 真片假名照旧判 ja


def test_NoteExpress斜杠标签不粘进摘要() -> None:
    """知网 NoteExpress 导出里有 `{ISBN/ISSN}: 1002-5502`。标签正则不认 `/`，整行就当成
    续行粘进上一个字段——摘要末尾多出一串刊号，而刊号进 `imp_` 编号的哈希。"""
    text = ("{Reference Type}: Journal Article\n{Title}: 斜杠标签\n{Year}: 2021\n"
            "{Abstract}: 本文考察……\n{ISBN/ISSN}: 1002-5502\n")
    r = importers.parse_export(text)[0]
    assert r.abstract == "本文考察……"


def test_RefWorks小写标签与链接里的DOI() -> None:
    """知网 RefWorks 导出的真实样式：卷号标签是小写 `vo`，DOI 不给 `DO`、只藏在
    `LK https://link.cnki.net/doi/10.xxxx/...` 里。大小写敏感 + 只认 `DO` 的话，
    卷号丢了、DOI 也丢了——而 DOI 是去重的第一把钥匙。"""
    text = ("RT Journal Article\nT1 小写标签\nJF 人口研究\nYR 2012\nvo 36\n"
            "LK https://link.cnki.net/doi/10.19604/j.cnki.rkyj.2012.04.001\n")
    r = importers.parse_export(text)[0]
    assert r.volume == "36"
    assert r.doi == "10.19604/j.cnki.rkyj.2012.04.001"
    assert r.paper_id == r.doi


def test_RIS链接里的DOI也认_但DO优先() -> None:
    """链接可能指向别的东西（知网详情页、出版社落地页），`DO` 才是作者填的那个，所以它优先。"""
    text = ("TY  - JOUR\nTI  - 有DO也有链接\nPY  - 2020\nDO  - 10.1234/real\n"
            "UR  - https://doi.org/10.5678/fromlink\nER  -\n")
    assert importers.parse_export(text)[0].doi == "10.1234/real"
    text2 = "TY  - JOUR\nTI  - 只有链接\nPY  - 2020\nUR  - https://doi.org/10.5678/fromlink\nER  -\n"
    assert importers.parse_export(text2)[0].doi == "10.5678/fromlink"
    # 不是 DOI 的链接不抽（知网的详情页 URL 里没有 `10.xxxx/`）
    text3 = "TY  - JOUR\nTI  - 详情页链接\nPY  - 2020\nUR  - https://kns.cnki.net/kcms/detail/x.htm\nER  -\n"
    assert importers.parse_export(text3)[0].doi == ""


def test_RefWorks未知两字母开头的续行不当标签() -> None:
    """摘要折行后下一行是 `US policy matters`——两个大写字母加空格，正好长得像标签。
    表里没有的两字母一律按续行走，否则整句被吃掉，摘要断在半截。"""
    text = "RT Journal Article\nT1 续行像标签\nYR 2020\nAB The first line\nUS policy matters\n"
    assert "US policy matters" in importers.parse_export(text)[0].abstract


def test_RefWorks认得但丢弃的标签不粘进上一字段() -> None:
    """`SR`、`ID`、`AD`、`FD`、`DS`、`DB` 是知网真会出的标签，我们不要它们的值——
    但「不要」不等于「不认得」：不认得就会被当成续行，值粘进上一个字段。"""
    text = ("RT Journal Article\nT1 已知但不要\nYR 2020\nAB 摘要正文\n"
            "SR Print(0)\nID 12345\nAD 北京大学\nDS CNKI\nDB CJFD\nFD 2020-01-01\n")
    assert importers.parse_export(text)[0].abstract == "摘要正文"


def test_编号哈希带刊名_同题同年不同刊不撞() -> None:
    """`title|year` 两条同题同年的不同刊文章会撞成同一个 `imp_` 编号，
    后来那条被当成重复丢掉。刊名进哈希把这一类分开。"""
    a = "RT Journal Article\nT1 同一个标题\nJF 人口研究\nYR 2012\n"
    b = "RT Journal Article\nT1 同一个标题\nJF 社会学研究\nYR 2012\n"
    assert importers.parse_export(a)[0].paper_id != importers.parse_export(b)[0].paper_id


# ─────────────────── 从中文期刊 PDF 里猜篇名（2026-09-25 重写）

#: 背景：原先的实现是「第一条够长又不全是符号的行」，在 18 篇真的中文期刊 PDF 上
#: **只对 8 篇（44%）**。下面每一条测试对着一类真实的失败——夹具用的是实测抽出来的原样。
#: 重写后 17/18。唯一没救回来的那篇，篇名在抽出来的文本里排在正文后面（抽文顺序错乱）。

from oss.core.importers import title_guess


def _page(*lines: str) -> str:
    return "\n\n".join(lines)


def test_刊眉不当篇名() -> None:
    """实测抽到过 `保险研究　２０２３年第８期　ＩＮＳＵＲＡＮＣＥ ＳＴＵＤＩＥＳ　Ｎｏ. ８`。
    全角数字也要认——中文期刊 PDF 大量用全角排版。"""
    t = _page("保险研究　２０２３年第８期　ＩＮＳＵＲＡＮＣＥＳＴＵＤＩＥＳ　Ｎｏ. ８",
              "慈善捐赠的同伴效应：", "来自互联网募捐平台的田野实验证据")
    assert title_guess(t).startswith("慈善捐赠的同伴效应")


def test_期号行不当篇名() -> None:
    for bad in ("2004 年第2 期", "201 3 年第6 期", "第13卷第2期"):
        got = title_guess(_page(bad, "中国都市地区普通公众参加社会捐助活动"))
        assert got == "中国都市地区普通公众参加社会捐助活动", f"{bad} → {got}"


def test_刊名不当篇名_但以研究收尾的长篇名要留() -> None:
    """**这一条最容易做错。**`社会学研究`（5 字）是刊名，
    `企业制度安排与员工组织系统信任研究`（17 字）是篇名——两者都以「研究」收尾，
    只能靠长度分开。"""
    assert title_guess(_page("社会学研究", "社会资本、政府绩效与城市居民对政府的信任")) \
        .startswith("社会资本")
    assert title_guess(_page("山东大学学报（哲学社会科学版）", "民生风险感知如何影响捐赠行为？")) \
        == "民生风险感知如何影响捐赠行为？"
    keep = "企业制度安排与员工组织系统信任研究"
    assert title_guess(_page(keep, "刘爱玉  田志鹏")) == keep, "以「研究」收尾的真篇名被当刊名砍了"


def test_作者行不当篇名() -> None:
    """`胡 荣  胡 康  温莹莹`——**字间还带空格**，所以按词长占比判，不按字数判。"""
    got = title_guess(_page("胡 荣  胡 康  温莹莹", "社会资本、政府绩效与城市居民对政府的信任"))
    assert got.startswith("社会资本")


def test_单位行不当篇名() -> None:
    got = title_guess(_page("(1  西南大学心理学部，重庆    400715)    (2  江苏师范大学教育科学学院)",
                            "城市社区居民获得感量表的编制"))
    assert got == "城市社区居民获得感量表的编制"


def test_栏目标题不当篇名_靠前一行是编号认出来() -> None:
    """《社会学研究》的 PDF 抽出来顺序是乱的，`研究背景与问题的提出` 会排在篇名前面。

    **认的是「前一行是章节编号」这个结构，不是「研究背景」这几个字**——
    换个栏目名照样拦得住，而词表判据换个写法就漏。
    """
    t = _page("一、", "研究背景与问题的提出", "我国居民收入与幸福感关系的研究")
    assert title_guess(t).startswith("我国居民收入与幸福感")
    # 换个栏目名，规则照样成立
    t2 = _page("二、", "数据来源与变量说明", "老年人微信使用与获得感的关系")
    assert title_guess(t2).startswith("老年人微信使用")


def test_正文残句不当篇名_篇名不以句号收尾() -> None:
    """实测抽到过 `机制，加大公共产品、特别是社会保障的供给力度。`——
    它是正文中间断下来的一句。**篇名不会以句号收尾。**"""
    t = _page("机制，加大公共产品、特别是社会保障的供给力度。", "我国居民收入与幸福感关系的研究")
    assert title_guess(t).startswith("我国居民收入与幸福感")


def test_摘要关键词不当篇名() -> None:
    for bad in ("摘　要　在文献分析和深度访谈的基础上编制初始量表",
                "关键词    城市社区居民，获得感，量表编制",
                "内容提要 依据差序原则建立的人际信任模式"):
        got = title_guess(_page(bad, "青年人获得感现状及其影响因素"))
        assert got == "青年人获得感现状及其影响因素", f"{bad[:12]} → {got}"


def test_国标元数据行不当篇名() -> None:
    """GB/T 3179 规定中文期刊首页固定印这几行。**封闭集合，不是语义词表。**"""
    for bad in ("分类号    B849", "中图分类号：C912", "文献标识码：A", "文章编号：1000-4149"):
        got = title_guess(_page(bad, "青年人获得感现状及其影响因素"))
        assert got == "青年人获得感现状及其影响因素", f"{bad} → {got}"


def test_主副标题跨行要接上() -> None:
    """`亲而信到利相关:` 与 `人际信任的转向` 是同一个篇名的两行。
    只取第一行，关键词就丢了。"""
    got = title_guess(_page("亲而信到利相关:", "人际信任的转向", "朱 虹"))
    assert "人际信任" in got, got


def test_英文刊名页眉不当中文篇名() -> None:
    # 夹具要有足够的中文，才算得上「这是一份中文文档」——真文件本来就有
    got = title_guess(_page("心理与行为研究 2021，19(5)：665~670",
                            "Studies  of  Psychology  and  Behavior",
                            "老年人微信使用与获得感的关系",
                            "摘要　本研究考察老年人微信使用与获得感的关系。" * 8))
    assert got.startswith("老年人微信使用")


def test_英文文献不受中文规则影响() -> None:
    """中文文档才拒「整行没有汉字」。英文文献整篇没有汉字，不能一刀切。"""
    t = _page("Religion and Charitable Giving in Contemporary China",
              "Jane Smith and John Doe", "Abstract  This paper examines ...")
    assert title_guess(t).startswith("Religion and Charitable Giving")


def test_长度上限要分中西文两档() -> None:
    """**这是补测试时逮到的真 bug。**45 字的上限是按中文校准的：
    中文一个字顶一个词，而英文篇名按字符数轻易过百——
    `Religion and Charitable Giving in Contemporary China` 就有 52 个字符。

    拿中文那档去卡英文，**英文文献的篇名会被整片砍掉**，
    然后作者行、摘要行顺位顶上来。
    """
    long_en = "Religion, Charitable Giving, and Prosocial Behavior in Contemporary Chinese Society"
    assert len(long_en) > 45, "夹具本身要超过中文那档才测得出来"
    assert title_guess(_page(long_en, "Jane Smith", "Abstract  We examine ...")) == long_en

    # 中文那档不能跟着放宽：五十多字的摘要换行段仍要拦住
    zh_abstract = "和验证性因素分析修订初始量表，最终形成的正式量表包括认知获得感和情感体验获得感两个分量表，前者包"
    assert len(zh_abstract) > 45
    assert title_guess(_page(zh_abstract, "青年人获得感现状及其影响因素")) \
        == "青年人获得感现状及其影响因素"


def test_刊名与以研究收尾的篇名_缝要卡准() -> None:
    """**补测试时逮到的回归。**第一版把「刊名」的长度上限写成 12，
    结果 `宗教信仰与捐赠行为的实证研究`（「研究」前面 12 个字）被当成刊名砍了。

    真实的缝：刊名在「研究 / 学报」前面只有三五个字，
    而以「研究」收尾的真篇名前面有十几个字。取 6。
    """
    real = "宗教信仰与捐赠行为的实证研究"
    for journal in ("社会学研究", "山东大学学报（哲学社会科学版）", "经济学(季刊)",
                    "心理与行为研究", "公共行政评论", "中国青年研究"):
        assert title_guess(_page(journal, real)) == real, f"{journal} 没拒掉"
    for t in ("企业制度安排与员工组织系统信任研究", "社会资本与居民幸福感研究",
              "转型期社会信任感的阶层与区域特征"):
        assert title_guess(_page(t, "张三  李四")) == t, f"真篇名 {t} 被当刊名砍了"
