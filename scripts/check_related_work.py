#!/usr/bin/env python3
"""相关工作地图的回验：**模型说的话，程序查一遍**。

阶段 1（docs/design/2026-09-11-related-work.md）让六路子代理各自上网查、交回条目。
子代理最常见的毛病是编——编一个不存在的项目、编一句原文里没有的话。这里机械地核三层：

1. **格式**：该填的都填了；每一栏的说法都有引句撑着；编号认得
2. **标识**：arXiv 编号、DOI 查得到，标题年份对得上；项目网址不是 404
3. **引句**：每句都能在原文里逐字找到——规则跟读书卡同一把（`oss.core.reading._norm`：
   小写、空白归一），另外抹平 PDF 断行连字符、HTML 与 Markdown 记号、弯引号与连字

每条三种结果：通过 / 未核（原文拿不到——不作清单依据）/ 不过（退回重查）。
**它不判断条目重不重要、说得对不对**——那是主会话和跨模型审稿的事。

    python scripts/check_related_work.py runs/related-work/raw/A.json [更多文件] [--report 报告.json] [--offline]

退出码：0 = 没有「不过」；1 = 有「不过」，或输入文件格式不对。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oss.core import literature, reading  # noqa: E402

CATEGORIES = ("A", "B", "C", "D", "E1", "E2")
MECHANISMS = frozenset(f"M{i}" for i in range(1, 19))
WEAKNESSES = frozenset(f"W{i}" for i in range(1, 10))
LEVELS = ("强", "中", "弱")
OVERLAP_LEVELS = ("有", "部分")
DISCIPLINES = frozenset({"economics", "sociology", "political-science", "psychology", "communication", "通用"})
RESEARCH_TYPES = frozenset({"因果", "描述", "测量", "调查", "仿真", "通用"})
LANDINGS = ("透镜", "门", "审稿侧重", "计划模板", "写作", "学科档案")
SOURCE_LEVELS = ("fulltext", "abstract", "readme", "metadata")
STAGES = ("文献", "选题", "数据", "分析", "替代解释", "写作", "审稿", "执行", "评测")
COSTS = ("小", "中", "大")
#: 省略号的几种写法——引句里出现哪一种都算拼接
_ELLIPSES = ("…", "...", "⋯", "。。。")
CLAIM_FIELDS = ("what", "claims", "evaluation")
NOT_FOUND = "查不到"
#: 引句的最短长度。太短的一句（「we propose」）在哪篇里都找得到，核不牢
MIN_QUOTE = 20
#: arXiv 接口要求两次调用之间隔三秒
ARXIV_DELAY = 3.0
ARXIV_API = "https://export.arxiv.org/api/query"
_ATOM = {"a": "http://www.w3.org/2005/Atom"}


# ------------------------------------------------------------------ 一、格式


def _blank(v) -> bool:
    """空：None、空列表、只有空白的字符串。"""
    return v is None or v == [] or (isinstance(v, str) and not v.strip())


def _as_list(tag: str, name: str, v, p: list[str]) -> list:
    """该是列表的栏。写成了别的（最常见是字符串）报一句就算——**不然字符串会被按字拆开逐个报**。"""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    p.append(f"{tag}：{name} 要写成列表，现在是 {type(v).__name__}「{str(v)[:30]}」")
    return []


def _quote_problems(tag: str, item: dict) -> list[str]:
    """一句引文本身合不合格：够不够长、有没有拼接、写没写出处。它在不在原文里是另一件事（quote_found）。"""
    raw = item.get("quote")
    if raw is not None and not isinstance(raw, str):
        return [f"{tag}：quote 要写成一个字符串，现在是 {type(raw).__name__}——抄一段连续的原文，不许拆成几段拼"]
    q = str(item.get("quote") or "").strip()
    p: list[str] = []
    n = len(canon(q))
    if n < MIN_QUOTE:
        p.append(f"{tag}：引句太短（规范化后 {n} 个字符），核不牢——至少 {MIN_QUOTE} 个字符")
    if any(mark in q for mark in _ELLIPSES):
        p.append(f"{tag}：引句里有省略号——要抄连续的一段原文，不许拼接")
    if _blank(str(item.get("where") or "")):
        p.append(f"{tag}：没写出处位置（where）")
    return p


def _technique_problems(eid: str, i: int, t: dict, category: str) -> list[str]:
    tag = f"{eid} 本事 #{i}"
    p = [f"{tag}：{k} 是空的" for k in ("name", "what", "measured_on", "evidence_level", "cost") if _blank(t.get(k))]
    for k, allowed in (("evidence_level", LEVELS), ("cost", COSTS), ("stage", STAGES)):
        if not _blank(t.get(k)) and t.get(k) not in allowed:
            p.append(f"{tag}：{k}「{t.get(k)}」只认 {'/'.join(allowed)}")
    if t.get("evidence") is not None and not isinstance(t.get("evidence"), dict):
        p.append(f"{tag}：evidence 要写成对象 {{\"quote\": …, \"where\": …}}，现在是 {type(t.get('evidence')).__name__}")
    level = t.get("evidence_level")
    if level in ("强", "中"):
        ev = t.get("evidence")
        if isinstance(ev, dict) and not _blank(ev.get("quote")):
            p += _quote_problems(tag, ev)
        else:
            p.append(f"{tag}：判了「{level}」却没引撑得住的那句原文——evidence 要写成 {{\"quote\": …, \"where\": …}}")
    fits = _as_list(tag, "fits", t.get("fits"), p)
    p += [f"{tag}：fits 里的「{w}」不是 W1–W9" for w in fits if not isinstance(w, str) or w not in WEAKNESSES]
    if category in ("E1", "E2"):
        scope = t.get("applies_to") if isinstance(t.get("applies_to"), dict) else {}
        ds = _as_list(tag, "applies_to.disciplines", scope.get("disciplines"), p)
        rs = _as_list(tag, "applies_to.research_types", scope.get("research_types"), p)
        if not ds or not rs:
            p.append(f"{tag}：方法论要写 applies_to（适用哪些学科、哪类研究）")
        p += [f"{tag}：学科「{d}」不认识，只认 {'/'.join(sorted(DISCIPLINES))}"
              for d in ds if not isinstance(d, str) or d not in DISCIPLINES]
        p += [f"{tag}：研究类型「{r}」不认识，只认 {'/'.join(sorted(RESEARCH_TYPES))}"
              for r in rs if not isinstance(r, str) or r not in RESEARCH_TYPES]
        if t.get("landing") not in LANDINGS:
            p.append(f"{tag}：landing「{t.get('landing')}」只认 {'/'.join(LANDINGS)}")
    elif not fits:
        p.append(f"{tag}：没对上任何一块短板（fits 是空的）——对不上的本事不收")
    return p


def check_structure(e: dict) -> list[str]:
    """格式：形状对、该填的填了、每栏说法有引句撑着、编号认得。**不联网，也不会因为形状写坏而崩。**"""
    if not isinstance(e, dict):
        return [f"条目要写成一个对象，现在是 {type(e).__name__}「{str(e)[:30]}」"]
    eid = e.get("id") or "?"
    p = [f"{eid}：{k} 是空的" for k in ("id", "category", "title") if _blank(e.get(k))]
    p += [f"{eid}：{k} 是空的——查不到就写「查不到」，不留空" for k in (*CLAIM_FIELDS, "borrow") if _blank(e.get(k))]
    year = e.get("year")
    if not isinstance(year, int) or isinstance(year, bool):
        p.append(f"{eid}：year 要写成整数年份（如 2024），现在是「{year}」")
    if e.get("category") not in CATEGORIES:
        p.append(f"{eid}：category「{e.get('category')}」不认识，只认 {'/'.join(CATEGORIES)}")
    if e.get("source_level") not in SOURCE_LEVELS:
        p.append(f"{eid}：source_level「{e.get('source_level')}」只认 {'/'.join(SOURCE_LEVELS)}")
    ids = e.get("ids")
    if not isinstance(ids, dict):
        p.append(f"{eid}：ids 要写成对象，如 {{\"arxiv\": \"2408.06292\"}}")
        ids = {}
    if not any(ids.get(k) for k in ("doi", "arxiv", "url", "local")):
        p.append(f"{eid}：ids 里 doi / arxiv / url / local 一个都没有，没法核")

    evidence: list[tuple[int, dict]] = []
    for i, x in enumerate(_as_list(eid, "evidence", e.get("evidence"), p), 1):
        if isinstance(x, dict):
            evidence.append((i, x))
        else:
            p.append(f"{eid} 引句 #{i}：要写成对象 {{\"supports\": …, \"quote\": …, \"where\": …}}")
    overlap = _as_list(eid, "overlap", e.get("overlap"), p)
    listed = {str(o.get("m")) for o in overlap if isinstance(o, dict)}
    allowed = {*CLAIM_FIELDS, *(f"overlap:{m}" for m in listed)}
    supports: set[str] = set()
    for i, x in evidence:
        s = x.get("supports")
        if isinstance(s, str) and s in allowed:
            supports.add(s)
        else:
            p.append(f"{eid} 引句 #{i}：supports「{s}」对不上——只认 what / claims / evaluation，"
                     f"或 overlap 里列了的 overlap:M几")
    for f in CLAIM_FIELDS:
        v = e.get(f)
        if not _blank(v) and str(v).strip() != NOT_FOUND and f not in supports:
            p.append(f"{eid}：{f} 这一栏没有引句撑着——要么加一条 supports={f} 的引句，要么整栏只写「查不到」")
    seen: set[str] = set()
    for o in overlap:
        if not isinstance(o, dict):
            p.append(f"{eid}：overlap 的每一项要写成 {{\"m\": \"M1\", \"level\": \"有\"}}，不是「{o}」")
            continue
        m = o.get("m", "")
        if not isinstance(m, str) or m not in MECHANISMS:
            p.append(f"{eid}：overlap 里的「{m}」不是 M1–M18")
            continue
        if m in seen:
            p.append(f"{eid}：overlap 里 {m} 列了两次")
        seen.add(m)
        if o.get("level") not in OVERLAP_LEVELS:
            p.append(f"{eid}：overlap {m} 的 level 只认 有/部分（没看到的不用列）")
        if o.get("level") == "部分" and _blank(o.get("note")):
            p.append(f"{eid}：overlap {m} 判了「部分」，要写差在哪（note）")
        if f"overlap:{m}" not in supports:
            p.append(f"{eid}：overlap 说它有 {m}，没有引句撑着（evidence 里要有 supports=overlap:{m}）")
    for i, x in evidence:
        p += _quote_problems(f"{eid} 引句 #{i}", x)
    for i, t in enumerate(_as_list(eid, "techniques", e.get("techniques"), p), 1):
        if isinstance(t, dict):
            p += _technique_problems(eid, i, t, e.get("category") or "")
        else:
            p.append(f"{eid} 本事 #{i}：要写成对象")
    return p


# ------------------------------------------------------------------ 二、引句比对

#: 会出现在 README 与网页正文里的 HTML 标签：行内的删掉（不留空格——「<b>实证</b>研究」要连上），块级的换成空格。
#: **只认白名单里的标签名、不跨行、不超过 500 字符**——泛泛的 `<字母…>` 会从 PDF 公式里的「y<t」
#: 一路吞到下一个「>」，真跑时一篇论文丢了 27% 的正文
_INLINE_TAGS = "a|abbr|b|big|code|del|em|font|i|img|ins|kbd|mark|s|small|span|strong|sub|sup|u"
_BLOCK_TAGS = ("blockquote|br|center|details|div|h[1-6]|hr|li|ol|p|picture|pre|source|summary"
               "|table|tbody|td|th|thead|tr|ul|video")
_TAG_INLINE = re.compile(rf"</?(?:{_INLINE_TAGS})\b[^<>\n]{{0,500}}>", re.I)
_TAG_BLOCK = re.compile(rf"</?(?:{_BLOCK_TAGS})\b[^<>\n]{{0,500}}>", re.I)
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
#: Markdown 记号：行首的标题井号（后面要有空格）与引用号、强调与代码记号、词边上的下划线。**删掉，不换空格**。
#: 行中间的 > 与 #（「>10%」「#1」）有意义，留着；行首的 > 后面紧跟数字的也留着——那是 PDF 折到行首的「> 0.05」
_MD_MARKS = re.compile(r"(?m)^[ \t]*(?:(?:>(?![ \t]*[\d.])[ \t]*)+|#{1,6}[ \t]+)|[*`]|(?<!\w)_|_(?!\w)")
#: 软连字符（PDF 断词处）连同后面的空白一起删
_SOFT_HYPHEN = re.compile(r"\u00ad\s*")
#: 零宽字符与变体选择符：看不见，却会让逐字比对失败
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\ufe0e\ufe0f"), None)
#: 弯引号拉直；各种连字符与减号（U+2010–2012、U+2212）统一成「-」
_FOLD = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u02bc": "'",
                       "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
                       "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-"})
_CJK = "\u2e80-\u9fff\u3000-\u303f\uff00-\uffef"
#: 汉字旁边的空白：中文里的换行、中英之间的空格都不是原文的一部分
_CJK_SPACE = re.compile(rf"(?<=[{_CJK}])\s+|\s+(?=[{_CJK}])")
_HAS_CJK = re.compile(rf"[{_CJK}]")
#: PDF 断行处的连字符：「multi-\nagent」规范化之后是「multi- agent」。只认字母-字母，**数字之间的不动**（「1-\n5」不是 15）
_BROKEN_HYPHEN = re.compile(r"([^\W\d_])- ([^\W\d_])")
#: 字母之间的连字符（带不带断行空格）——最后一道比对时两边都去掉
_LETTER_HYPHEN = re.compile(r"(?<=[^\W\d_])- ?(?=[^\W\d_])")
#: 中文兜底比对时去掉的空白：**夹在两个英文字母或数字之间的不去**——不然「1 5」会跟「15」粘成一样
_SQUASH = re.compile(r"(?<![A-Za-z0-9])\s+|\s+(?![A-Za-z0-9])")


def canon(t: str) -> str:
    """比对用的规范形。规则只有一条：**只抹掉从不承载意思的东西，不跨行删**——
    实体解码、软连字符与零宽字符、白名单里的 HTML 标签、Markdown 记号、汉字旁的空白，
    再 NFKC（连字 ﬁ→fi、全角转半角）、弯引号与连字符归一，最后走读书卡那把 `_norm`（小写、空白归一）。
    代价：NFKC 把「10²」折成「102」、「①」折成「1」，删 `*` 会丢显著性星号——两边一起丢，可以接受。
    字母间连字符的兜底会把「re-sign」和「resign」当成一样——也接受。"""
    t = _SOFT_HYPHEN.sub("", html.unescape(t or "")).translate(_INVISIBLE)
    t = _TAG_BLOCK.sub(" ", _TAG_INLINE.sub("", t))
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_MARKS.sub("", t).replace("|", " ")
    t = _CJK_SPACE.sub("", t)  # 在 NFKC 之前做：NFKC 会把全角标点变成半角，那时就认不出是中文了
    return reading._norm(unicodedata.normalize("NFKC", t).translate(_FOLD))


def _is_alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _edge_ok(q: str, t: str, i: int) -> bool:
    """在 t 的位置 i 找到的 q，两头有没有粘着别的字母数字——「3.2」不能算在「3.25」里找到，
    「significant」不能算在「insignificant」里找到。汉字不看边界。"""
    j = i + len(q)
    if _is_alnum(q[0]) and i > 0 and _is_alnum(t[i - 1]):
        return False
    if _is_alnum(q[-1]) and j < len(t) and _is_alnum(t[j]):
        return False
    if q[-1].isdigit() and re.match(r"[.,]\d", t[j:j + 2]):
        return False
    if q[0].isdigit() and re.search(r"\d[.,]$", t[max(0, i - 2):i]):
        return False
    if q[0].isascii() and q[0].isalpha() and re.search(r"[^\W\d_]- ?$", t[max(0, i - 3):i]):
        return False  # 「non-significant」里找到的「significant」不算
    if q[-1].isascii() and q[-1].isalpha() and re.match(r"- ?[^\W\d_]", t[j:j + 3]):
        return False  # 引句停在连字符复合词的半截
    if q[0].isdigit() and re.search(r"(?<![\w-])-\s?$", t[max(0, i - 3):i]):
        return False  # 「-0.35」里找到的「0.35」不算；表格分隔线「--」里的连字符不是负号
    return True


def _find(q: str, t: str) -> bool:
    i = t.find(q)
    while i >= 0:
        if _edge_ok(q, t, i):
            return True
        i = t.find(q, i + 1)
    return False


@lru_cache(maxsize=8)
def _text_forms(text: str) -> tuple[str, str, str, str, str]:
    """一份原文的五种比对形：规范形、断行连字符连上、断行连字符留着、字母间连字符全去掉、再去掉空白（中文兜底用）。
    同一份原文要核好几句引文，缓存起来只算一次。"""
    t = canon(text)
    no_hyphen = _LETTER_HYPHEN.sub("", t)
    return (t, _BROKEN_HYPHEN.sub(r"\1\2", t), _BROKEN_HYPHEN.sub(r"\1-\2", t),
            no_hyphen, _SQUASH.sub("", no_hyphen))


def quote_found(quote: str, text: str) -> bool:
    """这句引文在不在原文里——逐字，但不计较大小写、空白、排版记号；断行连字符带不带都认；
    **找到的地方两头不能粘着别的字母数字**（数字被截了一截、词被截了一截都不算找到）。"""
    q = canon(quote)
    if not q:
        return False
    t, joined, kept, no_hyphen, squashed = _text_forms(text)
    if any(_find(q, v) for v in (t, joined, kept)):
        return True
    qh = _LETTER_HYPHEN.sub("", q)
    if _find(qh, no_hyphen):
        return True
    # 中文：原文换行、中英之间的空格都会让逐字比对失败——引文里有汉字时，两边去掉空白再比
    return bool(_HAS_CJK.search(q)) and _find(_SQUASH.sub("", qh), squashed)


# ------------------------------------------------------------------ 三、取原文

_DROP = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.S | re.I)
#: 网页里的标签。网页正文里真正的小于号都写成了 &lt;，所以这里可以放心泛泛地去——
#: 跟引句比对用的白名单（`_TAG_INLINE` / `_TAG_BLOCK`）不是一回事，那边的原文可能是 PDF
_HTML_TAG = re.compile(r"<[^>]+>")
#: 浏览器里会换行的块级标签，换成换行；别的（行内）标签直接删——全换成空格会把「multi<span>agent</span>」拆开、
#: 在逗号前多出空格，审查时 arXiv 网页版的引句有一到两成因此找不到
_HTML_BLOCK = re.compile(r"</?(?:p|div|li|ul|ol|h[1-6]|tr|td|th|table|thead|tbody|br|pre|blockquote|details|summary"
                         r"|hr|article|section|figure|figcaption)\b[^>]*>", re.I)
#: 网页抽出的字不到这么多，多半是要跑脚本才显示内容的页面（「Loading...」）——不算原文，也不缓存
MIN_HTML_CHARS = 200
#: 本地副本只认这个目录下的文件：仓库自带的参考快照。绝对路径、「..」跳出去、运行目录里的文件
#: 都可能是子代理自己写的「副本」——拿它当原文，就能给编造的引句作证
LOCAL_ROOT = "skill reference"
_ARXIV_ID = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|huggingface\.co/papers/|10\.48550/arxiv\.|arxiv:?\s*|^)"
                       r"(\d{4}\.\d{4,5}(?:v\d+)?)(?!\d)", re.I)
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
_GH = re.compile(r"https?://github\.com/([^/\s]+)/([^/\s#?]+)(?:/blob/([^/\s]+)/(\S+?))?/?$")


def html_text(page: str) -> str:
    """网页 → 文字，照浏览器显示的样子：扔掉脚本与样式，块级标签换行，其余标签删掉，再解实体。"""
    page = _HTML_BLOCK.sub("\n", _DROP.sub(" ", page))
    return html.unescape(_HTML_TAG.sub("", page))


def pdf_text(raw: bytes) -> str:
    """PDF → 文字，**只留横排的行**。arXiv PDF 左边竖排的「arXiv:xxxx [cs.AI] 日期」水印，
    按 `reading._pdf_text` 的抽法会插进摘要里，把真引句拆坏（审查时第 1 页十句坏了两句）。
    PyMuPDF 不在、或这份 PDF 它打不开，就退回 `reading._pdf_text`。"""
    try:
        import pymupdf
    except ImportError:
        return reading._pdf_text(raw)
    try:
        lines: list[str] = []
        with pymupdf.open(stream=raw, filetype="pdf") as doc:
            for page in doc:
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        if tuple(round(x) for x in line.get("dir", (1, 0))) != (1, 0):
                            continue
                        lines.append("".join(span.get("text", "") for span in line.get("spans", [])))
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 —— 坏 PDF 是常态；打不开就让 reading 那一套再试一次
        return reading._pdf_text(raw)


@dataclass
class Fetched:
    text: str
    why: str = ""
    status: int = 0


def _local_path(loc: str) -> Path | None:
    """本地副本的真实路径；不在 `skill reference/` 里的（含绝对路径、用「..」跳出去的）一律不认。"""
    p = Path(loc)
    if p.is_absolute():
        return None
    full = (ROOT / p).resolve()
    return full if full.is_relative_to((ROOT / LOCAL_ROOT).resolve()) else None


def fetch_text(loc: str, client: httpx.Client, cache_dir: Path) -> Fetched:
    """拿一处原文。本地副本只认 `skill reference/` 下的相对路径；网址抓下来按类型抽文字，缓存在 cache_dir。
    缓存先写临时文件再改名——几路子代理并行时，读的一方不会读到写了一半的文件；空的缓存文件当没有。"""
    if not loc.startswith(("http://", "https://")):
        p = _local_path(loc)
        if p is None:
            return Fetched("", f"本地路径不认：{loc}——只认 {LOCAL_ROOT}/ 下的相对路径")
        if p.is_file():
            suffix = p.suffix.lower()
            if suffix == ".pdf":
                text = pdf_text(p.read_bytes())
            elif suffix in (".html", ".htm"):
                text = html_text(p.read_text(encoding="utf-8", errors="replace"))
            else:
                text = p.read_text(encoding="utf-8", errors="replace")
            return Fetched(text) if text.strip() else Fetched("", f"{loc}：抽不出文字")
        return Fetched("", f"本地没有 {loc}")
    cached = cache_dir / f"{hashlib.sha1(loc.encode()).hexdigest()[:16]}.txt"
    if cached.is_file() and cached.stat().st_size > 0:
        return Fetched(cached.read_text(encoding="utf-8"), status=200)
    if "arxiv.org" in loc:
        time.sleep(ARXIV_DELAY)  # arXiv 的 robots.txt 要求放慢，而几路子代理会同时来
    try:
        r = client.get(loc)
    except httpx.RequestError as exc:
        return Fetched("", f"{loc}：{type(exc).__name__}")
    if r.status_code != 200:
        return Fetched("", f"{loc}：HTTP {r.status_code}", r.status_code)
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or r.content[:5] == b"%PDF-":
        text = pdf_text(r.content)
    elif "html" in ctype:
        text = html_text(r.text)
        if len(text.strip()) < MIN_HTML_CHARS:
            return Fetched("", f"{loc}：网页只抽出 {len(text.strip())} 个字符，多半要跑脚本才显示内容，不算原文", 200)
    else:
        text = r.text
    if not text.strip():
        return Fetched("", f"{loc}：抽不出文字", 200)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_name(f"{cached.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, cached)
    return Fetched(text, status=200)


def arxiv_id(s: str) -> str:
    """「2408.06292」「arXiv:2408.06292v2」「arXiv 2408.06292」「10.48550/arXiv.2408.06292」、arXiv 与 Hugging Face 的论文网址都认；
    别的 DOI 返回空。"""
    m = _ARXIV_ID.search((s or "").strip())
    return m.group(1) if m else ""


#: 网址里只有这几类地址的 arXiv 编号才算数——GitHub 上叫「arxiv2408.06292-reproduction」的仓库不是那篇论文
_ARXIV_HOSTS = ("arxiv.org/", "huggingface.co/papers/", "10.48550/")


def _arxiv_of(ids: dict) -> str:
    """一条的 arXiv 编号：`ids.arxiv`、`ids.doi` 里写了就认；`ids.url` 只认 arXiv、Hugging Face 论文页、arXiv DOI 这三类地址——
    子代理常只给一个 arXiv 或 Hugging Face 网址，而别处网址里碰巧带着像编号的字样不算。"""
    url = ids.get("url") or ""
    cands = (ids.get("arxiv") or "", ids.get("doi") or "",
             url if any(h in url.lower() for h in _ARXIV_HOSTS) else "")
    return next((a for a in map(arxiv_id, cands) if a), "")


def _doi(s: str) -> str:
    """DOI 去掉常见前缀（网址、「doi:」）——子代理常把整条链接写进来。"""
    return _DOI_PREFIX.sub("", (s or "").strip())


def github_raw(url: str) -> list[str]:
    """GitHub 仓库首页 → raw README 的两个常见名字；blob 链接 → 对应的 raw 文件；别的网址返回空。
    **核对读的是原始文本**，子代理也被要求从 raw README 抄引句。"""
    m = _GH.match(url or "")
    if not m:
        return []
    owner, repo, branch, path = m.groups()
    base = f"https://raw.githubusercontent.com/{owner}/{repo.removesuffix('.git')}"
    if branch:
        return [f"{base}/{branch}/{path}"]
    return [f"{base}/HEAD/README.md", f"{base}/HEAD/readme.md"]


def locations(e: dict) -> list[str]:
    """一条的原文从哪些地方拿，按顺序：本地副本 → arXiv 的 PDF、摘要页、网页版 → GitHub raw → 网址本身 → DOI 落地页。
    摘要页与网页版要带上：子代理常从这两处抄，而它们的措辞、排版可能跟 PDF 不一样。"""
    ids = e.get("ids") or {}
    local = ids.get("local") or []
    locs = [local] if isinstance(local, str) else list(local)
    ax = _arxiv_of(ids)
    if ax:
        locs += [f"https://arxiv.org/pdf/{ax}", f"https://arxiv.org/abs/{ax}", f"https://arxiv.org/html/{ax}"]
    url = ids.get("url") or ""
    locs += github_raw(url)
    if url:
        locs.append(url)
    doi = _doi(ids.get("doi") or "")
    if doi and not ax:
        locs.append(f"https://doi.org/{doi}")
    return list(dict.fromkeys(locs))


# ------------------------------------------------------------------ 四、标识

#: 标题比对时不算数的虚词——不然「The Logic of the Survey」能靠 the、of 跟「The Logic of Collective Action」凑够六成
_STOPWORDS = frozenset("a an the of in on for to and or with by from at as is are its into via s t".split())


def _tokens(t: str) -> set[str]:
    """标题比对用的词：拉丁字母与数字按词切，去掉虚词、复数折成单数；汉字按字切（中文没有空格）。"""
    c = canon(t)
    words = {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
             for w in re.findall(r"[a-z0-9]+", c) if w not in _STOPWORDS}
    return words | set(_HAS_CJK.findall(c))


def _share(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a) if a else 0.0


def title_matches(claimed: str, found: str) -> bool:
    """是不是同一部作品的标题。三种情形都算对上：自报标题的实词有六成在查到的标题里；
    自报的主标题（冒号、破折号之前）跟查到的标题两头都对得上——登记处常不存副标题；
    查到的标题（至少三个实词）整个落在自报标题里——arXiv 常把第一版的长标题改短。"""
    a, b = _tokens(claimed), _tokens(found)
    if not a or not b:
        return False
    if _share(a, b) >= 0.6:
        return True
    main = _tokens(re.split(r":|—|--", canon(claimed), maxsplit=1)[0])
    if main and _share(main, b) >= 0.8 and _share(b, main) >= 0.8:
        return True
    return len(b) >= 3 and b <= a


def _compare(eid: str, title: str, year, got_title: str, got_year) -> tuple[list[str], list[str]]:
    """(不过的理由, 只记一笔的)。标题对不上算不过；**年份差得多只记一笔**——arXiv 记的是第一版的日子，
    期刊版常晚两三年（三篇分批双重差分的方法论文都是 2018 对 2020–2021），重印的书更晚，拿年份判不过会冤枉真条目。"""
    bad = [] if title_matches(title, got_title) else [f"{eid}：标题对不上——自报「{title}」，查到「{got_title}」"]
    notes = []
    if isinstance(year, int) and isinstance(got_year, int) and abs(year - got_year) > 1:
        notes.append(f"{eid}：年份差得多——自报 {year}，登记的是 {got_year}（预印本与正式版、重印版常差几年）")
    return bad, notes


def arxiv_meta(ax: str, client: httpx.Client) -> tuple[str, int | None, str] | None:
    """(标题, 年份, 摘要)；查不到返回 None。接口本身不通、或返回的不是 Atom（限流页、错误页），
    抛 `literature.SourceUnavailable`——**源不可用不能变成「查不到」**。"""
    time.sleep(ARXIV_DELAY)
    try:
        r = client.get(ARXIV_API, params={"id_list": ax})
    except httpx.RequestError as exc:
        raise literature.SourceUnavailable("arxiv", type(exc).__name__) from exc
    if r.status_code != 200:
        raise literature.SourceUnavailable("arxiv", f"HTTP {r.status_code}")
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as exc:
        raise literature.SourceUnavailable("arxiv", "返回的不是 XML，多半是限流页") from exc
    if root.tag != f"{{{_ATOM['a']}}}feed":
        raise literature.SourceUnavailable("arxiv", f"返回的不是 Atom（根元素是 {root.tag}）")
    for entry in root.findall("a:entry", _ATOM):
        title = " ".join((entry.findtext("a:title", "", _ATOM) or "").split())
        if not title or title.lower() == "error":
            return None
        pub = entry.findtext("a:published", "", _ATOM) or ""
        summary = (entry.findtext("a:summary", "", _ATOM) or "").strip()
        return title, (int(pub[:4]) if pub[:4].isdigit() else None), summary
    return None


#: arXiv 给每篇论文都在 DataCite 登记了 DOI（10.48550/arXiv.编号，老论文也补登了）——arXiv 接口限流时的备用门
DATACITE_API = "https://api.datacite.org/dois"


def arxiv_doi(ax: str) -> str:
    """arXiv 编号 → 它在 DataCite 登记的 DOI。DOI 不分版本，版本号（v2）去掉。"""
    return "10.48550/arXiv." + re.sub(r"v\d+$", "", ax)


def datacite_meta(ax: str, client: httpx.Client) -> tuple[str, int | None, str] | None:
    """(标题, 年份, 摘要)，跟 `arxiv_meta` 同一个形状：DataCite 上那条 arXiv DOI 记录里的标题、
    首版年份（publicationYear）与摘要。DataCite 上没有这条返回 None；接口不通、返回的不是那条记录，
    抛 `literature.SourceUnavailable`——跟 arXiv 那一路同一条规矩。"""
    try:
        r = client.get(f"{DATACITE_API}/{arxiv_doi(ax)}")
    except httpx.RequestError as exc:
        raise literature.SourceUnavailable("datacite", type(exc).__name__) from exc
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise literature.SourceUnavailable("datacite", f"HTTP {r.status_code}")
    try:
        attrs = r.json()["data"]["attributes"]
        title = " ".join(str(attrs["titles"][0]["title"]).split())
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise literature.SourceUnavailable("datacite", "返回的不是 DOI 记录（没有 data.attributes.titles）") from exc
    if not title:
        raise literature.SourceUnavailable("datacite", "记录里的标题是空的")
    year = attrs.get("publicationYear")
    abstract = next((str(d.get("description") or "") for d in attrs.get("descriptions") or []
                     if isinstance(d, dict) and d.get("descriptionType") == "Abstract"), "")
    return title, (int(year) if str(year).isdigit() else None), abstract.strip()


def _datacite_fallback(ax: str, client: httpx.Client, arxiv_down: literature.SourceUnavailable
                       ) -> tuple[tuple[str, int | None, str], str]:
    """arXiv 接口不可用时改问 DataCite：(记录, 记一笔是谁核的)。DataCite 也不可用、或上面没有这条，
    都只算源不可用——**DataCite 没登记不能当「arXiv 查不到」**：它是备用的门，不是 arXiv 本身。"""
    doi = arxiv_doi(ax)
    try:
        got = datacite_meta(ax, client)
    except literature.SourceUnavailable as exc:
        raise literature.SourceUnavailable(
            "arxiv+datacite", f"arXiv：{arxiv_down.reason}；DataCite：{exc.reason}") from exc
    if got is None:
        raise literature.SourceUnavailable("arxiv+datacite", f"arXiv：{arxiv_down.reason}；DataCite 上没有 {doi}")
    return got, f"arXiv 接口不可用（{arxiv_down.reason}），标识改用 DataCite 登记的 arXiv DOI {doi} 核"


def check_ids(e: dict, client: httpx.Client) -> tuple[list[str], list[str], list[str], list[str]]:
    """(不过的理由, 未核的理由, 只记一笔的, 查标识时顺手拿到的摘要)。arXiv 编号优先，arXiv 接口不可用时改查
    DataCite 登记的 arXiv DOI（记一笔是它核的）；不是 arXiv 的 DOI 走 OpenAlex → Crossref。
    **源不可用不等于查不到**——前者只算未核。摘要交给 `check_entry` 当一处原文：期刊论文的落地页多半挡机器人，
    有了它，摘要里的引句就不至于全是未核。"""
    eid, ids = e.get("id") or "?", e.get("ids") or {}
    title, year = e.get("title") or "", e.get("year")
    bad: list[str] = []
    unk: list[str] = []
    notes: list[str] = []
    texts: list[str] = []
    ax = _arxiv_of(ids)
    doi = _doi(ids.get("doi") or "")
    try:
        if ax:
            try:
                got = arxiv_meta(ax, client)
            except literature.SourceUnavailable as exc:
                got, via = _datacite_fallback(ax, client, exc)
                notes.append(f"{eid}：{via}")
            if got is None:
                bad.append(f"{eid}：arXiv {ax} 查不到")
            else:
                got_title, got_year, summary = got
                b, n = _compare(eid, title, year, got_title, got_year)
                bad, notes = bad + b, notes + n
                texts += [summary] if summary else []
        elif doi:
            rec = literature.fetch_by_doi(doi, client=client)
            if rec is None:
                bad.append(f"{eid}：DOI {doi} 查不到")
            else:
                b, n = _compare(eid, title, year, rec.title, rec.year)
                bad, notes = bad + b, notes + n
                texts += [rec.abstract] if rec.abstract else []
    except literature.SourceUnavailable as exc:
        unk.append(f"{eid}：查标识的源不可用（{exc.reason}）——没核上，不等于没有")
    return bad, unk, notes, texts


# ------------------------------------------------------------------ 五、整条与命令行


@dataclass
class EntryResult:
    id: str
    status: str                                   # 通过 / 未核 / 不过
    problems: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # 只记一笔、不影响结果的（比如年份差得多）


def _quotes(e: dict):
    """一条里所有要核的引句：各栏的 evidence，加上每个本事的证据句。"""
    eid = e.get("id") or "?"
    for i, x in enumerate(e.get("evidence") or [], 1):
        yield f"{eid} 引句 #{i}", x
    for i, t in enumerate(e.get("techniques") or [], 1):
        ev = t.get("evidence") if isinstance(t, dict) and isinstance(t.get("evidence"), dict) else {}
        if ev.get("quote"):
            yield f"{eid} 本事 #{i}", ev


def check_entry(e: dict, client: httpx.Client, cache_dir: Path) -> EntryResult:
    """一条的三层全核一遍。原文拿不到的只算未核；**原文拿到了、引句找不到的算不过**——
    但只拿到接口给的摘要时，找不到的那句多半在正文里，只算未核。"""
    eid = e.get("id") or "?"
    problems = check_structure(e)
    bad, unk, notes, extra = check_ids(e, client)
    problems += bad
    parts: list[str] = []
    reasons: list[str] = []
    url = (e.get("ids") or {}).get("url") or ""
    for loc in locations(e):
        got = fetch_text(loc, client, cache_dir)
        if got.text:
            parts.append(got.text)
            continue
        reasons.append(got.why)
        if loc == url and got.status in (404, 410):
            problems.append(f"{eid}：网址打不开（HTTP {got.status}）——这个东西真的存在吗")
    corpus = "\n".join(parts + extra)  # 查标识时拿到的摘要也算一处原文
    if not corpus:
        unk.append(f"{eid}：原文一处都拿不到（{'；'.join(reasons) or '没有可抓的位置'}）")
    for tag, item in _quotes(e):
        quote = item.get("quote") or ""
        text = corpus
        if item.get("url"):
            # 另给了出处的，那一页和这条的主来源一起核——子代理「保险起见」加的 url 常指向摘要页，而这句在正文里
            got = fetch_text(item["url"], client, cache_dir)
            if got.text:
                text = got.text + "\n" + corpus
            elif quote_found(quote, corpus):
                continue  # 那一页拿不到，但主来源里就有这句
            else:
                unk.append(f"{tag}：这句的出处拿不到（{got.why}）")
                continue
        if text and not quote_found(quote, text):
            if parts or item.get("url"):
                problems.append(f"{tag}：引句在原文里找不到——「{quote[:40]}」；"
                                f"要是这句出自别处（比如正式版的 PDF），在这条引句上加 url")
            else:
                unk.append(f"{tag}：只拿到接口给的摘要，这句不在摘要里——正文没拿到，核不了")
    status = "不过" if problems else ("未核" if unk else "通过")
    return EntryResult(eid, status, problems, unk, notes)


def _safe_id(e) -> str:
    return str(e.get("id") or "?") if isinstance(e, dict) else "?"


def check_all(entries: list[dict], client: httpx.Client | None, cache_dir: Path, *,
              offline: bool = False, on_result=None) -> list[EntryResult]:
    """全部条目。`offline` 只核格式——格式没问题的也只能算未核，不能算通过。
    **一条形状写坏了只让那一条不过**，不许崩掉整批、连报告都没有。
    `on_result` 每核完一条就回调一次——命令行靠它边核边打印。"""
    count: dict[str, int] = {}
    for e in entries:
        count[_safe_id(e)] = count.get(_safe_id(e), 0) + 1
    out: list[EntryResult] = []
    for e in entries:
        eid = _safe_id(e)
        try:
            if offline or client is None:
                p = check_structure(e)
                r = EntryResult(eid, "不过" if p else "未核", p, [] if p else ["离线：只核了格式"])
            else:
                r = check_entry(e, client, cache_dir)
        except Exception as exc:  # noqa: BLE001 —— 子代理写坏的形状千奇百怪，这一条判不过，其余照核
            try:
                p = check_structure(e)
            except Exception:  # noqa: BLE001
                p = []
            r = EntryResult(eid, "不过", p + [f"{eid}：检查时出错（{type(exc).__name__}: {exc}）——多半是哪一栏的类型写错了"])
        if count[eid] > 1:
            r.problems.append(f"{eid}：id 重复了 {count[eid]} 次")
            r.status = "不过"
        if on_result is not None:
            on_result(r)
        out.append(r)
    return out


_MARK = {"通过": "✓", "未核": "·", "不过": "✗"}


def _print_result(r: EntryResult) -> None:
    """核完一条就打一条——一批要跑好几分钟，全核完才打印的话，子代理的命令超时时什么都看不到。"""
    print(f"{_MARK[r.status]} {r.id}", flush=True)
    for line in r.problems + r.unverified:
        print(f"    {line}", flush=True)
    for line in r.notes:
        print(f"    注：{line}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="相关工作地图的回验：格式、标识、引句")
    ap.add_argument("files", nargs="+", help="条目 JSON 文件（顶层是一个列表），可给多个")
    ap.add_argument("--sources", default=str(ROOT / "runs" / "related-work" / "sources"),
                    help="原文缓存目录（不进仓库）")
    ap.add_argument("--report", default="", help="逐条结果写成 JSON")
    ap.add_argument("--offline", action="store_true", help="只核格式，不联网")
    a = ap.parse_args(argv)
    entries: list[dict] = []
    for f in a.files:
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"✗ {f}：读不了或不是合法的 JSON（{exc}）", file=sys.stderr)
            return 1
        if not isinstance(data, list):
            print(f"✗ {f}：顶层要是一个列表", file=sys.stderr)
            return 1
        entries += data
    client = None if a.offline else httpx.Client(
        timeout=40, follow_redirects=True, headers={"User-Agent": "oss-related-work-check/0.1"})
    try:
        results = check_all(entries, client, Path(a.sources), offline=a.offline, on_result=_print_result)
    finally:
        if client is not None:
            client.close()
    n = {s: sum(r.status == s for r in results) for s in _MARK}
    print(f"通过 {n['通过']} / 未核 {n['未核']} / 不过 {n['不过']}（共 {len(results)} 条）")
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    return 1 if n["不过"] else 0


if __name__ == "__main__":
    sys.exit(main())
