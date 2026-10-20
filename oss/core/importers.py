"""知网、万方的导出题录 → 语料记录。

中文库没有任何公开 API（知网自己是 DOI 注册机构，DOI 不进 Crossref；万方、维普、
国家哲社中心都没有），抓取器脆且是法律灰区。唯一诚实又合法的路是**用户用自己机构的权限
检索、点导出，工具吃导出文件**。按知网、万方的官方导出样式解析（RIS、EndNote、RefWorks、
NoteExpress）；**真实导出文件只用合成样本核过**——各家版本的标签集、大小写、换行都有出入，
遇到解析不全的，把文件脱敏后放进 `tests/` 当夹具，照着补标签表。

导入的记录：`via="import"`、`source="import:<格式>"`、`language` 按标题四分（见 `_language`）。
**解析不做取舍**：日文、韩文的记录照样产出，要不要它们由 `oss/api/lit.py::import_file` 决定
（用户只要中英文，那两种在导入时被挡下并计数）。解析器一旦替下游做主，下游就再也数不清
自己漏了什么。
没有 OpenAlex 编号，滚雪球对它们无效，也拿不到全文——只有题录和摘要。
"""

from __future__ import annotations

import hashlib
import re

from oss.core.contracts import PaperRecord

#: 汉字。只收基本区（U+4E00–U+9FFF）——**不含扩展 A（U+3400–）与兼容表意文字（U+F900–）**：
#: 那两段里是生僻字与异体字，现代刊名、论文标题里基本不出现，多收进来只会把判定面变宽。
_HAN = re.compile(r"[\u4e00-\u9fff]")
#: 平假名 + 片假名，**挖掉 U+30FB「・」**。那个中点 Unicode 上归片假名块，但在中文标题里
#: 它是外国人名的间隔号（`马克斯・韦伯的社会学思想`）——算成假名的话，凡是提到外国学者的
#: 中文题录都会判 ja，被导入那一层挡掉。
#: **不含片假名扩展（U+31F0–31FF）与半角片假名（U+FF66–FF9D）**：前者是阿伊努语的小写假名，
#: 后者是半角写法，知网、万方的导出里都不出现，收进来只会把判定面变宽。
_KANA = re.compile(r"[\u3040-\u30fa\u30fc-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af]")    # 谚文音节


def _language(title: str) -> str:
    """标题 → `ja` / `ko` / `zh` / `en`。四分，**顺序不能换**。

    假名、谚文先判：日文标题里的汉字跟中文的汉字是同一批码位（`日本の社会保障` 里的
    `日本`、`社会保障`），先看汉字就把每一条日文都判成中文了。反过来，假名、谚文是各自
    独有的字，见到就能定。

    **纯汉字写的日文标题分不出来，会当中文进来**——这是这套判法认下的代价：
    码位上它跟中文一模一样，除非上词典，没有别的分法。知网、万方的导出里这种极少。
    """
    if _KANA.search(title or ""):
        return "ja"
    if _HANGUL.search(title or ""):
        return "ko"
    return "zh" if _HAN.search(title or "") else "en"


#: 续行拼接的边界判定，比 `_HAN` 宽：中文标点（，。；）与全角符号也算，因为摘要常折在标点后。
#: 除全角标点外只收破折号一族（‐-―）、省略号（…）与间隔号（·）——中文标题常折在
#: 「主标题——副标题」的破折号处。**不收弯引号**：`a ‘new’` 接 `approach` 是西文折行，
#: 那个空格不能省。只认汉字与中文标点（不收假名、谚文）：日韩文的记录进不了语料，
#: 为它们放宽接缝判定只会在中英文上多出错。只用来决定接缝要不要空格，不参与 `_language`。
_CJK_EDGE = re.compile(
    r"[\u00b7\u2010-\u2015\u2026\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


class UnknownFormat(ValueError):
    """认不出是哪种导出格式。"""


def detect_format(text: str) -> str:
    head = text.lstrip("\ufeff")[:2000]
    if re.search(r"^TY  - ", head, re.M):
        return "ris"
    if re.search(r"^%0 ", head, re.M):
        return "endnote"
    if re.search(r"^RT ", head, re.M):
        return "refworks"
    if re.search(r"^\{Reference Type\}:", head, re.M):
        return "noteexpress"
    raise UnknownFormat("认不出导出格式：要 RIS（TY  - ）、EndNote（%0）、RefWorks（RT）或 NoteExpress（{Reference Type}）")


#: 各格式的标签 → 统一字段。多值字段（作者、关键词）在 `_records` 里累加。
_TAGS = {
    "ris": {"AU": "author", "A1": "author", "TI": "title", "T1": "title", "JO": "venue_abbr", "JF": "venue", "T2": "venue",
            "PY": "year", "Y1": "year", "VL": "volume", "IS": "issue", "SP": "sp", "EP": "ep", "AB": "abstract",
            "N2": "abstract", "DO": "doi", "KW": "keyword", "UR": "link", "UL": "link", "LK": "link"},
    "endnote": {"A": "author", "T": "title", "J": "venue", "B": "venue", "D": "year", "V": "volume", "N": "issue",
                "P": "pages", "X": "abstract", "R": "doi", "K": "keyword"},
    "refworks": {"A1": "author", "T1": "title", "JF": "venue", "YR": "year", "VO": "volume", "IS": "issue",
                 "SP": "sp", "OP": "ep", "AB": "abstract", "DO": "doi", "K1": "keyword", "LK": "link"},
    "noteexpress": {"Author": "author", "Title": "title", "Journal": "venue", "Year": "year", "Volume": "volume",
                    "Issue": "issue", "Pages": "pages", "Abstract": "abstract", "DOI": "doi", "Keywords": "keyword"},
}

#: RefWorks 里**认得但不要**的标签。知网真会出这些（`SR Print(0)`、`ID`、`AD` 作者单位、
#: `FD` 入库日期、`DS`/`DB` 库名）。「不要」不等于「不认得」——只有在这张表里，
#: 它们才会被当成标签把值丢掉；不在表里就会被当成续行，值粘进上一个字段。
_KNOWN_REFWORKS = frozenset({"SR", "ID", "AD", "FD", "DS", "DB", "CL", "OL", "LA", "NO", "U1", "U2"})

_SPLIT = {
    "ris": (re.compile(r"^TY  - ", re.M), re.compile(r"^([A-Z][A-Z0-9])  - ?(.*)$")),
    "endnote": (re.compile(r"^%0 ", re.M), re.compile(r"^%(\S) ?(.*)$")),
    # 知网的 RefWorks 导出里卷号标签是小写 `vo`——大小写敏感就把卷号整个丢了。查表前统一 `.upper()`。
    "refworks": (re.compile(r"^RT ", re.M), re.compile(r"^([A-Za-z][A-Za-z0-9])(?: (.*))?$")),
    # `{ISBN/ISSN}: 1002-5502`——标签名里有斜杠。原来的 `[A-Za-z0-9 ]+` 不认，整行当续行
    # 粘进摘要，刊号跟着进 `imp_` 编号的哈希。
    "noteexpress": (re.compile(r"^\{Reference Type\}:", re.M), re.compile(r"^\{([^}]+)\}: ?(.*)$")),
}


def _join(a: str, b: str) -> str:
    """续行接缝：**任一侧**是中文字（或中文标点）就直接连，否则补一个空格。

    中文不靠空格断词，多塞一个就改了字面——而字面进 `imp_` 编号的哈希，去重会漏。
    要求两侧都是中文就漏了中英混排：`本文利用2019` 接 `年数据`、`GDP` 接 `，增长`，
    接缝一侧是数字或拉丁字母，照样不该有空格。只有两侧都不沾中文才是真的西文折行。
    """
    if not a:
        return b
    if _CJK_EDGE.search(a[-1]) or _CJK_EDGE.search(b[0]):
        return a + b
    return a + " " + b


def _records(text: str, fmt: str) -> list[dict]:
    splitter, line_re = _SPLIT[fmt]
    tags = _TAGS[fmt]
    chunks = [c for c in splitter.split(text) if c.strip()]
    out = []
    for chunk in chunks:
        rec: dict = {"author": [], "keyword": []}
        last = None
        for raw in chunk.splitlines():
            line = raw.rstrip("\r")
            m = line_re.match(line)
            if m and fmt == "refworks":
                # 摘要折行后的 `US policy matters` 正好长得像标签（两个大写字母加空格）。
                # 只有表里有的才算标签，其余按续行——否则整句被当成一个不认得的标签吃掉。
                up = m.group(1).strip().upper()
                if up not in tags and up not in _KNOWN_REFWORKS:
                    m = None
            if not m:
                # 续行：摘要常折行
                if last in ("abstract", "title") and line.strip():
                    rec[last] = _join(rec.get(last, ""), line.strip())
                continue
            raw_key = m.group(1).strip()
            key = tags.get(raw_key.upper() if fmt == "refworks" else raw_key)
            val = (m.group(2) or "").strip()
            last = key
            if not key or not val:
                continue
            if key in ("author", "keyword"):
                rec[key].extend(a.strip() for a in re.split(r"[;；]", val) if a.strip())
            else:
                rec.setdefault(key, val)
        if rec.get("title"):
            out.append(rec)
    return out


def _pages(rec: dict) -> str:
    if rec.get("pages"):
        return re.sub(r"\s*-\s*", "–", rec["pages"].strip())
    sp, ep = rec.get("sp", "").strip(), rec.get("ep", "").strip()
    # 有的导出把整段页码塞进起页（`SP 3-15`）、止页又单给一遍（`OP 15`）：再拼就成了 `3-15–15`。
    if sp and ep and sp != ep and not sp.endswith(ep):
        return f"{sp}–{ep}"
    return re.sub(r"\s*-\s*", "–", sp or ep)


#: 链接里的 DOI。知网的 RefWorks 导出常常不给 `DO`，只在
#: `LK https://link.cnki.net/doi/10.19604/j.cnki.rkyj.2012.04.001` 里带一次——
#: 而 DOI 是去重的第一把钥匙，丢了就只能靠标题比对。
_DOI_IN_URL = re.compile(r"(?:/doi/|doi\.org/)(10\.\d{4,9}/[^\s?#]+)", re.I)


def _doi_from_link(link: str) -> str:
    """`LK`/`UR`/`UL` 的值里抽 DOI。**只在没有 `DO` 时用**：链接可能指向别的东西
    （知网的详情页、出版社的落地页），`DO` 才是作者本人填的那个。"""
    m = _DOI_IN_URL.search(link or "")
    return m.group(1).rstrip(".").lower() if m else ""


def _year(v: str) -> int | None:
    m = re.search(r"(19|20)\d{2}", v or "")
    return int(m.group(0)) if m else None


#: 只在首页范围里找 DOI。正文末尾的参考文献每条都有 DOI——在全文里搜，抓到的会是
#: **别人的**文章，而且错得看不出来（编号格式完全正常）。学术 PDF 的 DOI 印在首页页眉、
#: 页脚或摘要旁，这个窗口够用。
_FIRST_PAGE_CHARS = 2500
#: DOI 的通行形状（Crossref 的建议正则，收紧了尾部标点）。前缀 `doi:` / `https://doi.org/`
#: 由 `_clean` 在匹配前剥掉。
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]*[A-Za-z0-9]")


#: 中文期刊网页上的「引用本文」块：`作者[, 作者]. 年. 题名[J]. 刊名, 卷(期): 页.`
#: 这是**期刊自己给的机读题录**，比从版面上猜作者行可靠得多——
#: 版面启发式实测只中 2/9，还把页眉的「移动版」当成了作者名。
#: 三个作者之间的分隔符各站不同（逗号、顿号、全角逗号、空格），一并认。
_CITE_NAME = r"[\u4e00-\u9fff]{2,4}"
_CITE_AUTHORS = rf"{_CITE_NAME}(?:\s*[,，、]\s*{_CITE_NAME})*"
#: **两种写法都要认。**国标题录的年份位置各刊不同，实测两种都在用：
#:   · `翟学伟. 2014. 信任的本质及其文化[J]. 社会, 34(1): 1-26.`   ← 年在题名前
#:   · `刘力, 阮荣平. 信仰与捐赠…[J]. 南方经济, 2018, 37(11): 103-120.` ← 年在题名后
#: 只认第一种时，第二种那一篇的作者（阮荣平）就一直是空的。
_CITE_BLOCKS = (
    re.compile(rf"({_CITE_AUTHORS})\s*[.．]\s*((?:19|20)\d\d)\s*[.．]\s*([^\n\[]{{4,80}})\[J\]"),
    re.compile(rf"({_CITE_AUTHORS})\s*[.．]\s*([^\n\[]{{4,80}})\[J\][^\n]*?((?:19|20)\d\d)"),
)


def cited_as(text: str, title: str) -> tuple[list[str], int]:
    """从「引用本文」块里取作者与年。**取到的题名跟已知篇名对不上就不要。**

    返回 `(作者列表, 年)`；拿不到返回 `([], 0)`。

    **为什么要用篇名自核**：一页上可能有好几条题录（「上一篇」「下一篇」、
    参考文献表），随便取一条就会把**别人的**作者安到这份文献上，
    而且错得看不出来（作者名、年份、刊名全都是合法的）。
    篇名对得上，这一条才确定是本文自己的题录——这是个**算得出来的事实**，
    不是「看起来像」。

    篇名比对去掉空白、标点与 `[UNVERIFIED]` 前缀：网页题录里的题名常带
    破折号副标题，正文抽出来的那份可能只到主标题，所以**一方包含另一方**即算对上。
    """
    want = _norm_title(title)
    if not want or len(want) < 4:
        return [], 0
    for i, pat in enumerate(_CITE_BLOCKS):
        # 第一种写法里 年=2、题名=3；第二种反过来
        gt, gy = (3, 2) if i == 0 else (2, 3)
        for m in pat.finditer(text or ""):
            got = _norm_title(m.group(gt))
            if not got:
                continue
            if want in got or got in want:
                names = [n for n in re.split(r"[,，、\s]+", m.group(1)) if n]
                return names, int(m.group(gy))
    return [], 0


def _norm_title(s: str) -> str:
    """比对用的题名：去掉 `[UNVERIFIED]` 前缀、空白与标点，只留字。"""
    s = re.sub(r"^\s*\[UNVERIFIED\]\s*", "", s or "")
    return re.sub(r"[\s\u3000\-—–‐－_:：,，.。、;；!！?？()（）\[\]【】《》「」\"'']+", "", s)


#: 「题名 → 作者行 → 摘要」——中文期刊版面的通例。**锚在「摘要」上，不是扫前几十行。**
#: 早先试过「扫首页、取最后一个像名字的行」，实测只中 2/9，还把页眉的「移动版」
#: 当成了作者名。**会误报的抽取器比没有更糟**，所以这一版把两头都钉住：
#: 上界是已抽出的篇名出现的那一行，下界是「摘要」，作者只能在这两行之间。
#: 摘要那一行的各种印法。**三处都踩过**：英文刊名页用 `Abstract`；
#: 有的 PDF 把「【摘要】」断成「【摘」「要】」两行；全角空格夹在「摘」「要」之间。
_AU_ABSTRACT = re.compile(r"^\s*[\[［【]?\s*(摘\s*要|摘\s*$|内容提要|提\s*要|Abstract\b)", re.I)
_AU_NAME = re.compile(r"^[\u4e00-\u9fff]{2,4}$")
#: 夹在中间但不是作者的：单位、栏目、卷期、稿件信息。逐类排除，不靠词表猜。
_AU_BAD = re.compile(r"学报|评论|专栏|大学|学院|研究所|研究院|中心|编辑|基金|项目"
                     r"|摘要|关键词|收稿|通讯|作者|年第|期刊|卷")
_AU_DIGIT = re.compile(r"[\d０-９]")
#: 栏目标签的记法（`·劳动就业·`、`◆专栏`）。**剥掉这些符号会造出假名字**——
#: 「·劳动就业·」剥成「劳动就业」正好是四个汉字，过得了姓名那条判据。
_AU_LABEL = re.compile(r"[·◆●■□▲△※]")
#: 篇名往前找不过 20 行、摘要往后找不过 40 行。放宽只会把别人的题录圈进来。
_AU_TITLE_WINDOW, _AU_ABST_WINDOW = 20, 40


def authors_by_layout(text: str, title: str) -> list[str]:
    """按版面位置推作者：夹在篇名与「摘要」之间、长得像姓名的那几行。

    **这一层只推作者，不核篇名**——所以用它的记录要继续带 `[UNVERIFIED]`。
    「引用本文」块那一层（`cited_as`）不同：块里带篇名，对得上才采用，
    那是期刊自己给的题录，可以摘掉存疑标记。两层的可信度不一样，标记也不该一样。

    名字被断成两行的要拼回去（实测「周洁」印成「周」「洁*」两行）。
    """
    lines = [l.strip() for l in (text or "").splitlines()]
    want = _norm_title(title)
    if len(want) < 6:                       # 篇名太短，位置匹配不可靠
        return []
    ti = next((i for i, l in enumerate(lines[:_AU_TITLE_WINDOW])
               if (n := _norm_title(l)) and len(n) >= 6 and (n in want or want in n)), None)
    if ti is None:
        return []
    ai = next((i for i, l in enumerate(lines[ti + 1:ti + _AU_ABST_WINDOW], ti + 1)
               if _AU_ABSTRACT.match(l)), None)
    if ai is None:
        return []
    out: list[str] = []
    buf = ""
    for line in (x for x in lines[ti + 1:ai] if x):
        if _AU_DIGIT.search(line) or _AU_LABEL.search(line):
            buf = ""                        # 页眉、卷期、栏目标签：跳过，也打断拼接
            continue
        # **按空格切，不是剥掉空格。**一行印几个作者是通例（`李　丁　卢云峰`、
        # `吕炜 姬明曦 杨沫`），剥掉空格会拼成「李丁卢云峰」这样五到七个字的一坨，
        # 过不了姓名那条判据——四份真文件都是这么漏掉的。
        parts = [p for p in re.split(r"[*\s\u3000]+", line) if p]
        if len(parts) == 1 and len(parts[0]) == 1:   # 整行只有一个字：跨行断开的名，攒着
            buf += parts[0]
            continue
        if buf:
            out.append(buf)
            buf = ""
        # **一行里连续的单字要并成一个名。**两字名的通行印法是中间加空格，
        # 为了跟三字名对齐：`李　丁　卢云峰` 是两位作者（李丁、卢云峰），
        # `刘　　　能` 是一位（刘能）。逐个 token 取会把它们全按单字滤掉。
        run = ""
        for p in parts:
            if len(p) == 1:
                run += p
                continue
            if run:
                out.append(run)
                run = ""
            out.append(p)
        if run:
            out.append(run)
    if buf:
        out.append(buf)
    return [s for s in out if _AU_NAME.match(s) and not _AU_BAD.search(s)][:6]


def doi_in(text: str) -> str:
    """PDF 首页里印着的 DOI，找不到返回空串。

    **只看前 `_FIRST_PAGE_CHARS` 个字符**，理由见该常量。返回小写、剥过前缀的裸编号。
    """
    m = _DOI_RE.search((text or "")[:_FIRST_PAGE_CHARS])
    return m.group(0).rstrip(".").lower() if m else ""


#: 单个汉字。判「这份文档是不是中文的」用，决定要不要拒绝整行没有汉字的刊名英译页眉。
_CJK_CHARS = re.compile(r"[一-鿿]")

#: 刊眉：年份、期号、卷号、Vol/No、卷(期)：页码、刊号。
#: 实测抽到过 `保险研究　２０２３年第８期　ＩＮＳＵＲＡＮＣＥ ＳＴＵＤＩＥＳ`、`2004 年第2 期`。
_T_HEADER = re.compile(
    r"[\d０-９]{4}\s*年|第\s*[\d０-９]+\s*[期卷辑]|\bVol\b|\bNo\b\.?\s*[\d０-９]"
    r"|\d+\s*\(\s*\d+\s*\)\s*[：:]|ISSN|\bCN\s*\d{2}[-—]", re.I)

#: 栏目标记。实测抽到过 `◆专栏: 跨学科视野: 行为公共管理学`。
_T_BULLET = re.compile(r"^[◆●■□▲△※·*]+")

#: 摘要 / 关键词的起头。它们排在标题后面，抽文顺序一乱就会被当成第一条候选。
_T_ABSTRACT = re.compile(
    r"^[\[［【]?\s*(摘\s*要|内容提要|提\s*要|关\s*键\s*词|Abstract|Key\s*words)", re.I)

#: 刊名。**长度限制是关键**：`社会学研究`（5 字）、`山东大学学报（哲学社会科学版）`（6+ 字）
#: 是刊名，而 `企业制度安排与员工组织系统信任研究`（17 字）是篇名——都以「研究」收尾。
#: 12 字这条线把两者分开；实测 18 篇校准集里没有误伤。
#: **长度这条线是关键，而且要收得很紧。**刊名在「研究 / 学报」前面只有三五个字
#: （社会学研究 3、山东大学学报 4、心理与行为研究 5、公共行政评论 4），
#: 而以「研究」收尾的真篇名前面有十几个字
#: （`宗教信仰与捐赠行为的实证研究` 12、`企业制度安排与员工组织系统信任研究` 15）。
#: 第一版写 12，**把 14 字的真篇名当刊名砍了**——缝在 6 和 12 之间，取 6。
#:
#: 括号要认全角也认半角：`经济学(季刊)` 用的是半角，而且刊别词在括号**里面**。
_T_JOURNAL = re.compile(
    r"^.{2,6}(学报|评论|研究|科学|论坛|月刊|季刊|杂志|通讯)([（(][^）)]{0,12}[）)])?$"
    r"|^.{2,8}[（(](季刊|月刊|双月刊|旬刊|周刊)[）)]$")

#: 章节号独占一行（`一、`、`1.`）。**下一行就是栏目标题，不是篇名**——
#: 《社会学研究》的 PDF 抽出来顺序是乱的，`研究背景与问题的提出` 会排在篇名前面。
#: 这是结构信号，不是词表：认的是「前一行是编号」，不是认「研究背景」这几个字。
_T_SECNUM = re.compile(r"^[一二三四五六七八九十\d０-９]+\s*[、.．]\s*$")

#: 作者单位行：`(1  西南大学心理学部，重庆    400715)`。
_T_AFFIL = re.compile(r"^[(（]\s*[\d０-９]")

#: 国标 GB/T 3179 规定的那几个固定标签。**封闭集合，不是语义词表**——
#: 中文期刊首页固定印这几行，它们永远不是篇名。
_T_META = re.compile(r"^(中图)?分类号|^文献标[识志]码|^文章编号|^基金项目|^收稿日期")

#: 篇名的长度上限，**中文与西文两档**。
#:
#: 中文：连副标题也极少超过 45 字，而摘要的换行段动辄五十字往上（实测最长真篇名 28 字）。
#: 西文：一个汉字顶一个词，英文篇名按字符数轻易过百——
#: `Religion and Charitable Giving in Contemporary China` 就有 52 个字符。
#: **拿中文那档去卡英文，英文文献的篇名会被整片砍掉。**
_T_MAX = 45
_T_MAX_LATIN = 140
_T_CJK_SHARE = 0.3            # 汉字占比过线就按中文那档卡


def _title_authorish(t: str) -> bool:
    """像作者行吗。`胡 荣  胡 康  温莹莹` 这种——**字间还带空格**，所以按词长占比判。"""
    toks = [x for x in re.split(r"[\s　]+", t) if x]
    if len(toks) < 2:
        return False
    return (sum(1 for x in toks if len(x) <= 2) / len(toks) >= 0.6
            and len(t.replace(" ", "")) <= 24)


def title_guess(text: str) -> str:
    """从首页正文猜标题：第一条既不是刊眉、也不是作者、单位、栏目、摘要的行。

    **这是猜的**，所以只在第三层用，而且结果要标 `[UNVERIFIED]` 交用户确认。
    真办法是让用户同时给题录导出文件。

    **2026-09-25 重写。**原先是「第一条够长又不全是符号的行」，在 18 篇真的中文期刊
    PDF 上只对 8 篇（44%）——抽到的是刊眉（`保险研究　２０２３年第８期`）、
    作者行（`胡 荣  胡 康  温莹莹`）、期号（`201 3 年第6 期`）、
    栏目名（`◆专栏: 跨学科视野`），甚至正文中间一句
    （`机制，加大公共产品、特别是社会保障的供给力度。`）。

    重写后 **17/18（94%）**。每一条拒绝规则都对着一类真实的失败，
    而且都挂在**结构**上不挂在词表上：

    | 规则 | 认的是什么 |
    |---|---|
    | 刊眉 | 年 / 期 / 卷 / Vol / No / 卷(期)：页 |
    | 刊名 | 短（≤12 字）且以「学报 / 评论 / 研究…」收尾 |
    | 作者行 | 多数词只有一两个字 |
    | 单位行 | 以 `(数字` 开头 |
    | 栏目标题 | **前一行是「一、」这类编号** |
    | 摘要 / 关键词 | 起头那几个标准词 |
    | 正文残句 | **以句号收尾**——篇名不会 |

    唯一没救回来的那篇，篇名在抽出来的文本里排在正文后面（PDF 抽文顺序错乱），
    再追就是过拟合了。
    """
    head = (text or "")[:_FIRST_PAGE_CHARS]
    zh_doc = len(_CJK_CHARS.findall(head)) > 100
    nonempty = [ln for ln in (x.strip() for x in head.splitlines()) if ln]
    for pos, t in enumerate(nonempty):
        cjk_heavy = len(_CJK_CHARS.findall(t)) >= len(t) * _T_CJK_SHARE
        if len(t) < 6 or len(t) > (_T_MAX if cjk_heavy else _T_MAX_LATIN):
            continue
        if t.lower().startswith(("doi", "http", "issn", "isbn")):
            continue
        if sum(c.isalnum() for c in t) < len(t) * 0.5:
            continue
        if _T_HEADER.search(t) or _T_BULLET.match(t) or _T_ABSTRACT.match(t):
            continue
        if _T_AFFIL.match(t) or _T_META.match(t) or _T_JOURNAL.match(t):
            continue
        if _title_authorish(t) or t.endswith(("。", "．")):
            continue
        # 中文文档里整行不含汉字的，多半是刊名的英译页眉
        if zh_doc and not _CJK_CHARS.search(t):
            continue
        # 前一非空行是章节编号 → 这是栏目标题
        if pos and _T_SECNUM.match(nonempty[pos - 1]):
            continue
        # 以冒号收尾 = 主标题，副标题在下一非空行，接上才完整
        if t.endswith(("：", ":")) and pos + 1 < len(nonempty):
            nxt = nonempty[pos + 1]
            if len(nxt) <= _T_MAX and not _T_ABSTRACT.match(nxt) and not _title_authorish(nxt):
                t += nxt
        return t[:300]
    return ""


def parse_export(text: str) -> list[PaperRecord]:
    """整个导出文件 → 记录列表。认不出格式抛 `UnknownFormat`。"""
    # 用户常把知网 500 条一批的几份导出拼起来，接缝处还留着 BOM：只 lstrip 清不掉，
    # 带 BOM 的 `%0` 不在行首就切不开，整个第二份会并进最后一条记录。
    text = text.replace("\ufeff", "").replace("\r\n", "\n")
    fmt = detect_format(text)
    out: list[PaperRecord] = []
    for rec in _records(text, fmt):
        title = rec["title"].strip()
        doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "",
                     (rec.get("doi") or "").strip(), flags=re.I).rstrip(".").lower()
        doi = doi or _doi_from_link(rec.get("link", ""))
        year = _year(rec.get("year", ""))
        venue = (rec.get("venue") or rec.get("venue_abbr") or "").strip()
        # 刊名进哈希：`title|year` 两条同题同年的不同刊文章会撞成同一个编号，
        # 后来那条被当成重复丢掉。**仍可能碰撞**（同题同年同刊），见计划的停车场。
        pid = doi or "imp_" + hashlib.sha1(f"{title}|{year}|{venue}".encode()).hexdigest()[:10]
        out.append(PaperRecord(
            paper_id=pid, title=title, year=year, authors=rec["author"][:10], doi=doi,
            venue=venue, volume=(rec.get("volume") or "").strip(),
            issue=(rec.get("issue") or "").strip(), pages=_pages(rec),
            abstract=(rec.get("abstract") or "").strip(), source=f"import:{fmt}", via="import",
            language=_language(title),
        ))
    return out
