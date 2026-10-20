"""想题目之前先读透：挑最相关的文献、拿全文、把读书卡的引文钉回原文。

**为什么要这一步。**七篇论文的结论都弱，根子之一是题目浅：
检索 100 篇（一半没摘要）→ 五分钟建空白地图 → 出题。
好题目藏在文献的**局限章节**里——作者自己承认没做到的事——而我们连摘要都缺一半。

这里做的全是机械的那一半：

1. **挑**：按「跟检索式说的是不是同一件事」× 被引数排，取前 N 篇
2. **拿**：有开放获取链接就下 PDF 抽文本；没有就只有摘要；摘要也没有就只有元数据。
   **来源层级要标清**（借自 nature-literature-pipeline）——一张只读了摘要的卡，
   它的「局限」栏几乎必然是空的，那不是文献没局限，是没读到
3. **钉**：读书卡里每一条「局限」必须是原句，**校验会去拿到的文本里找它**
   （借自 nature-reader 的源锚点纪律）。找不到的一律判「无据」，不判「大概对」

读懂、抽取、判断哪里是空白——那是判断，归 skill。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from oss.core.contracts import LiteratureCorpus, PaperRecord, ReadingCards
from oss.core.literature import _cjk_hits, _content_words

LEVEL_FULL, LEVEL_ABSTRACT, LEVEL_META = "fulltext", "abstract", "metadata"


def fname(paper_id: str) -> str:
    """文献编号转成能当文件名的样子。Crossref 的编号是 DOI，带 `/`——
    直接当文件名会往子目录里写，真跑第一次就撞上。"""
    return re.sub(r"[^0-9A-Za-z._-]+", "_", paper_id or "unknown")[:120]

#: 全文最少要抽出多少字才算「拿到了」。PDF 里只有版权页或扫描件时抽出来是空的，
#: 那不叫全文。
_MIN_FULLTEXT_CHARS = 3000


@dataclass
class FetchItem:
    paper_id: str
    title: str
    level: str
    chars: int
    path: str = ""
    reason: str = ""


#: 摘要只看前这么多字符。语料里有些记录的「摘要」是两万字的全文倾倒，
#: 什么词都沾——不截断的话它们把每条检索式都命中，排到最前面。
_ABSTRACT_CAP = 2000


def rank(corpus: LiteratureCorpus, top: int = 20) -> list[PaperRecord]:
    """挑最值得精读的。分 = (专词加权的主题重合, log 被引)，**主题在前，被引在后**。

    借六维打分里**主题匹配是门槛**这一条：跟检索式一个内容词都不沾的，
    被引再高也不进——顶刊上的无关文章正是这样混进来的。
    不借「期刊声望」「人脉相关」两维：社科找空白靠理论对话在哪，不靠刊物名气。

    原先是「重合词数 × log 被引」相乘。验证跑里它把 COVID 治理、希腊债务危机、
    埃克森石油这类高被引文章排到了前 20——它们各沾了「government」「public」「health」
    这种泛词，被引一乘就压过了只沾三个专词的 Kudamatsu。前 20 篇只有 8 篇沾题。

    现在每个检索词按**它在语料里多稀有**加权（IDF）：「democratization」「mortality」
    在几篇里出现、分量重；「government」「public」到处都是、几乎不算分。
    标题里命中再加一倍。被引只在主题分打平时说话。同一份语料上前 20 篇沾题 18 篇。
    **这仍只是候选**：真正要读哪几篇是判断，`pick()` 让 skill 自己点名。
    """
    qwords: set[str] = set()
    for q in corpus.queries:
        qwords |= _content_words(q)
    if not qwords:
        return []

    def _words(r: PaperRecord) -> set[str]:
        return _content_words(f"{r.title} {(r.abstract or '')[:_ABSTRACT_CAP]} {r.tldr}")

    def _hit(r: PaperRecord) -> set[str]:
        """这条记录沾到了哪几个检索词。中文那半按子串算——中文不分词，整词比对下
        「农民工」跟标题「农民工市民化的路径选择」永远不等，中文记录一条都排不进候选。"""
        text = f"{r.title} {(r.abstract or '')[:_ABSTRACT_CAP]} {r.tldr}"
        return (_words(r) & qwords) | _cjk_hits(text, qwords)

    # 筛过的语料只排纳入的：被模型判「排除」的沾词文献，被引再高也不进精读候选
    pool = [r for r in corpus.records if r.screen != "排除"]
    n = max(len(pool), 1)
    df: dict[str, int] = {w: 0 for w in qwords}
    per_record = [(r, _hit(r)) for r in pool]
    for _, hit in per_record:
        for w in hit:
            df[w] += 1
    # 每篇都沾的词分量为零（不能为负——负分会把沾了泛词的文章倒扣到榜尾）
    idf = {w: max(0.0, math.log(n / (1 + df[w]))) for w in qwords}

    scored = []
    for r, hit in per_record:
        if not hit:
            continue
        title_words = _content_words(r.title or "") | _cjk_hits(r.title or "", qwords)
        topic = sum(idf[w] for w in hit) + sum(idf[w] for w in hit & title_words)
        scored.append(((round(topic, 6), math.log1p(max(r.citation_count, 0) + 1)), r))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in scored[:top]]


def pick(corpus: LiteratureCorpus, ids: list[str]) -> tuple[list[PaperRecord], list[str]]:
    """按 skill 点名的 id / DOI 挑。返回 `(挑到的, 没找到的)`——没找到的要说出来，不能静默跳过。"""
    by_key: dict[str, PaperRecord] = {}
    for r in corpus.records:
        for k in (getattr(r, "paper_id", ""), getattr(r, "doi", "")):
            if k:
                by_key[str(k).strip().lower()] = r
    chosen, missing = [], []
    for i in ids:
        r = by_key.get(i.strip().lower())
        (chosen if r is not None else missing).append(r if r is not None else i)
    return chosen, missing


#: 块宽超过页宽的这个比例就当「通栏」（标题、摘要、跨栏的图表），不参与分栏。
_WIDE_SHARE = 0.55
#: 左右两侧各至少要有这么多窄块，才认定这一页是双栏。少于它多半是单栏页上的零星偏移。
_MIN_COL_BLOCKS = 2


def _reading_order(page) -> list[str]:
    """一页 PDF 的文本块，按**人读的顺序**排：通栏页眉 → 左栏 → 右栏 → 通栏页脚。

    `page.get_text("text", sort=True)` 是按 (y, x) 整页排的，双栏页上必然逐行交错。
    交错的后果不只是读不顺——读书卡要逐字引原句，而 `oss lit cards --check` 查的是
    「这句话在不在文件里」：跨栏拼出来的假句子**确实在**，于是真原句被误拒、假句子被误收，
    而且校验器一声不响。**误收比误拒危险，因为产出物上一个字都看不出来。**

    分栏认得保守：左右两侧各至少 `_MIN_COL_BLOCKS` 个窄块才当双栏，否则整页按 (y, x) 排——
    **认错栏比不分栏更坏**，宁可不分。

    **没管、也不打算管的一件事**：有些两端对齐的 PDF 里空格字形是缺的，抽出来会连写
    （`theInternetfostersdemocratization`）。那是字体编码的问题，跟分栏无关。
    **不要自动补空格**——「局限」栏引的是逐字原文，自动改写等于偷偷篡改被引的原句，
    比留着连写更危险。连写不影响 `oss lit cards --check`（引文照样在文件里），
    人读也还认得出来。
    """
    blocks = [b for b in page.get_text("blocks")
              if len(b) > 6 and b[6] == 0 and (b[4] or "").strip()]
    if not blocks:
        return []
    ykey = lambda b: (round(b[1], 1), b[0])          # noqa: E731
    rect = page.rect
    w = rect.width or 1.0
    mid = rect.x0 + w / 2
    wide = [b for b in blocks if (b[2] - b[0]) > _WIDE_SHARE * w]
    narrow = [b for b in blocks if b not in wide]
    left = [b for b in narrow if (b[0] + b[2]) / 2 < mid]
    right = [b for b in narrow if (b[0] + b[2]) / 2 >= mid]
    if len(left) < _MIN_COL_BLOCKS or len(right) < _MIN_COL_BLOCKS:
        return [b[4] for b in sorted(blocks, key=ykey)]
    top, bot = min(b[1] for b in narrow), max(b[3] for b in narrow)
    head = [b for b in wide if b[3] <= top]
    foot = [b for b in wide if b[1] >= bot]
    # 夹在两栏中间的通栏块（跨栏的图表）跟着左栏走：留在上下文附近，比丢到页尾强
    mids = [b for b in wide if b not in head and b not in foot]
    ordered = (sorted(head, key=ykey) + sorted(left + mids, key=ykey)
               + sorted(right, key=ykey) + sorted(foot, key=ykey))
    return [b[4] for b in ordered]


def _pdf_text(raw: bytes) -> str:
    """PDF → 文本。有 PyMuPDF 就按栏读（见 `_reading_order`），没有退回 pypdf。"""
    from io import BytesIO

    try:
        import pymupdf

        with pymupdf.open(stream=raw, filetype="pdf") as doc:
            return "\n".join("\n".join(_reading_order(page)) for page in doc)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 —— 坏 PDF 是常态；PyMuPDF 打不开就让 pypdf 再试一次
        pass
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(raw))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:  # noqa: BLE001 —— 抽不出来就是抽不出来
        return ""


_META_PDF = re.compile(r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_META_PDF2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']', re.I)
_MAX_URLS = 6


def _pdf_from_html(html: str) -> str:
    """落地页（不是 PDF）里常有 `citation_pdf_url`——那才是 PDF。"""
    m = _META_PDF.search(html) or _META_PDF2.search(html)
    return m.group(1) if m else ""


def _candidates(r: PaperRecord, client: httpx.Client) -> list[str]:
    """按顺序试的 PDF 链接：记录自带的 → OpenAlex 全部开放位置 → Unpaywall → CORE。后两个要配置。"""
    from oss.core import literature as lit

    urls = [r.oa_url] if r.oa_url else []
    if r.doi:
        urls += lit.oa_locations(r.doi, client)
        urls += lit.unpaywall_pdf(r.doi, client)
        urls += lit.core_pdf(r.doi, client)
    return list(dict.fromkeys(u for u in urls if u))[:_MAX_URLS]


def _try_url(url: str, client: httpx.Client, *, follow_html: bool = True) -> tuple[str, str]:
    """拿一个链接：返回 (文本, 失败原因)。落地页里有 citation_pdf_url 就跟进一层。"""
    try:
        resp = client.get(url)
    except httpx.RequestError as exc:
        return "", type(exc).__name__
    if resp.status_code != 200:
        return "", f"HTTP {resp.status_code}"
    ctype = resp.headers.get("content-type", "")
    if "pdf" in ctype or resp.content[:5] == b"%PDF-":
        text = _pdf_text(resp.content)
        if len(text) >= _MIN_FULLTEXT_CHARS:
            return text, ""
        return "", f"PDF 只抽出 {len(text)} 字，不算全文"
    if follow_html and "html" in ctype:
        nxt = _pdf_from_html(resp.text[:200_000])
        if nxt:
            return _try_url(nxt, client, follow_html=False)
    return "", f"链接返回的不是 PDF（{ctype[:30]}）"


def fetch(
    corpus: LiteratureCorpus,
    out_dir: Path,
    *,
    top: int = 20,
    records: list[PaperRecord] | None = None,
    client: httpx.Client | None = None,
) -> list[FetchItem]:
    """把要读的文本拿到本地，每篇一个 txt，外加一份 index.json。

    `records` 给了就读这些（skill 点名的），没给才按 `rank` 取前 N 篇。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    own = client is None
    client = client or httpx.Client(timeout=40, follow_redirects=True,
                                    headers={"User-Agent": "oss-reading/0.3"})
    items: list[FetchItem] = []
    try:
        for r in (records if records is not None else rank(corpus, top)):
            path = out_dir / f"{fname(r.paper_id)}.txt"
            text, level, reason = "", LEVEL_META, ""
            # 验收跑 22 篇精读只拿到 1 篇全文：只试了记录自带的那一个链接。
            # 现在按顺序试所有开放位置，落地页里有 PDF 链接也跟进；每个失败原因都记下来。
            tried: list[str] = []
            for url in _candidates(r, client):
                text, why = _try_url(url, client)
                if text:
                    level = LEVEL_FULL
                    break
                tried.append(why)
            if not text:
                reason = ("没有任何开放获取链接" if not tried else
                          f"试了 {len(tried)} 个链接：{'；'.join(dict.fromkeys(tried))}")
            if not text and r.abstract:
                text, level = r.abstract, LEVEL_ABSTRACT
            header = (f"# {r.title}\n\n{'; '.join(r.authors)} ({r.year or 'n.d.'})"
                      f"  DOI: {r.doi or '—'}\n来源层级: {level}\n\n")
            path.write_text(header + (text or "（没有拿到任何文本）"), encoding="utf-8")
            items.append(FetchItem(paper_id=r.paper_id, title=r.title, level=level,
                                   chars=len(text), path=str(path), reason=reason))
    finally:
        if own:
            client.close()
    # **合并，不覆盖。**这一行原先是整批 `write_text`，于是
    # `oss lit import` 先导入的全文、再跑 `oss lit fetch` 就被整段冲掉——
    # 真跑时 18 篇全文只剩索引里的 6 篇，12 篇中文全文等于白下了。
    #
    # 讽刺的是 `merge_index` 的注释当初就写着「fetch 是整批重写……不合并就互相冲掉」，
    # **但只在导入那一侧调了它，抓取这一侧没调**。修一半跟没修一样。
    merge_index(out_dir, items)
    return items


def merge_index(out_dir: Path, items: list[FetchItem]) -> list[dict]:
    """把新拿到的并进 `index.json`，**不覆盖**已有条目（同 `paper_id` 以新的为准）。

    `fetch` 是整批重写，用户自己导入的 PDF 是增量补进来的——两边写同一个索引，
    不合并就互相冲掉。而冲掉之后 `check_cards` 会按缺失的索引判「自报全文、实际没拿到」，
    把好卡判成坏卡。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.json"
    old: list[dict] = []
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8")) or []
        except (json.JSONDecodeError, OSError):
            old = []           # 索引坏了就当没有：宁可重建，不要把导入整批打断
    merged = {str(i.get("paper_id")): i for i in old if isinstance(i, dict)}
    merged.update({i.paper_id: i.__dict__ for i in items})
    rows = list(merged.values())
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def coverage_note(items: list[FetchItem]) -> str:
    n = len(items)
    full = sum(1 for i in items if i.level == LEVEL_FULL)
    abs_ = sum(1 for i in items if i.level == LEVEL_ABSTRACT)
    meta = n - full - abs_
    note = f"{n} 篇：全文 {full}、仅摘要 {abs_}、仅元数据 {meta}"
    if full < n / 2:
        note += ("。**全文不到一半**——只读摘要写不出「作者自陈的局限」那一栏，"
                 "空白地图里「方法缺陷」类空白会系统性地缺失，要写进局限")
    return note


# ------------------------------------------------------------------ 读书卡校验


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").lower()).strip()


def check_cards(cards: ReadingCards, reading_dir: Path) -> tuple[list[str], list[str]]:
    """读书卡过不过：**局限引文必须能在拿到的文本里找到**。

    返回 `(拒绝理由, 警告)`。找不到的引文一律拒绝——
    「大概是这个意思」正是空白地图最不能建在上面的东西。
    """
    problems: list[str] = []
    warnings: list[str] = []
    index_path = reading_dir / "index.json"
    levels: dict[str, str] = {}
    if index_path.exists():
        for it in json.loads(index_path.read_text(encoding="utf-8")):
            levels[it["paper_id"]] = it["level"]

    for c in cards.cards:
        path = reading_dir / f"{fname(c.paper_id)}.txt"
        if not path.exists():
            problems.append(f"卡 {c.paper_id}：本地没有这篇的文本——先跑 oss lit fetch")
            continue
        text = _norm(path.read_text(encoding="utf-8"))
        actual = levels.get(c.paper_id, "")
        if actual and c.source_level != actual:
            problems.append(
                f"卡 {c.paper_id}：自报来源层级 {c.source_level}，实际拿到的是 {actual}——"
                f"**读了摘要不能说读了全文**")
        for i, q in enumerate(c.limitations, 1):
            probe = _norm(q.quote)
            if len(probe) < 20:
                warnings.append(f"卡 {c.paper_id} 局限 #{i}：引文太短（{len(probe)} 字），核不牢")
            if probe not in text:
                problems.append(
                    f"卡 {c.paper_id} 局限 #{i}：引文在拿到的文本里**找不到**——"
                    f"「{q.quote[:50]}…」。要么逐字抄原句，要么删掉；"
                    f"**不许凭印象写局限**")
        if actual == LEVEL_META and (c.finding or c.design):
            warnings.append(
                f"卡 {c.paper_id}：只有元数据却写了发现/设计——那是从标题猜的，标成猜的")
        if actual == LEVEL_ABSTRACT and c.limitations:
            warnings.append(
                f"卡 {c.paper_id}：只读了摘要却填了局限栏——摘要里很少有作者自陈的局限，核一下")
    return problems, warnings
