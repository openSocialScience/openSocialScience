"""风格包：有模板按模板写，没有按我们自己的写法写。

一个风格包是一个文件夹：

    styles/<刊名>/
      exemplars/*.pdf      3–10 篇该刊近年高引**研究论文**（不要书评）
      guide.md             作者须知原文（用户从期刊网站复制）
      template.docx        出版社 Word 模板（可选；牛津系多数不给）
      ── 以下由本模块生成 ──
      exemplars/*.txt      抽出的全文（本机读，不进仓库）
      style_profile.json   骨架：各节长度、图表数、句长、引用密度（工具量出来的）
      rules.json           硬规矩：字数、图表上限、匿名、摘要…（skill 读须知填，工具核对）

**分工照旧。**量得出的（骨架）归工具；须知是自然语言，读懂它填数字归 skill——
但填进去的每个数字必须能在须知原文里找到（跟读书卡引文钉回原文是同一个机制）；
学修辞动作归 skill 读范文；**抄句子这条路由查重堵死**。

**门是通用的，数字是每本刊自己的。**换刊 = 换文件夹，代码一行不动。
须知里没提的规矩，那一项留空，对应的门不生效。
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path

from oss.core.contracts import StyleRules
# 取节全仓库只此一份（节名表、编号前缀一并在那边）——风格包不再自持一份
from oss.core.textspan import section_spans as _section_spans

# ------------------------------------------------------------------ 抽文本


def pdf_to_text(pdf: Path) -> str:
    # 跟精读同一条抽字路径：优先 PyMuPDF（中文与双栏的阅读顺序对得多），退回 pypdf
    from oss.core.reading import _pdf_text

    try:
        t = _pdf_text(Path(pdf).read_bytes())
    except OSError:
        return ""
    t = re.sub(r"-\n(?=[a-z])", "", t)          # 行尾连字符接回去
    t = re.sub(r"[ \t]+", " ", t)
    return t


#: 中文的作者-年份引用：（张三，2020）（张三等，2020）、张三（2020）
_CITE_ZH = re.compile(
    r"[（(][\u4e00-\u9fff]{2,4}(?:等)?[，,]\s*(?:19|20)\d{2}[a-z]?[）)]"
    r"|[\u4e00-\u9fff]{2,4}(?:等)?[（(](?:19|20)\d{2}[a-z]?[）)]")

_CJK = re.compile(r"[\u4e00-\u9fff]")

_NAME = r"[A-Z][A-Za-zÀ-ɏ\-’']+"
_CITE = re.compile(
    r"\(\s*" + _NAME + r"(?:(?:,| and| &) " + _NAME + r")*(?: et al\.)?,? (?:19|20)\d{2}[a-z]?"   # (Smith and Jones 2020)
    r"|\b" + _NAME + r"(?:(?: and| &) " + _NAME + r")?(?: et al\.)? \((?:19|20)\d{2}[a-z]?\)"    # Smith et al. (2020)
)
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")


def _words(s: str) -> int:
    """英文按词数，中文按字数，混排相加。

    中文没有空格，按空白切词会把一整段数成一个「词」——8000 字的范文量出来 300 词，
    字数上限、各节目标全部失真。一个汉字记一个单位、一个拉丁词记一个单位：
    纯英文文本结果与原来完全一致，中文文本得到的就是期刊说的「字数」。
    """
    return len(_WORD.findall(s)) + len(_CJK.findall(s))


#: 公开名。摘要长度检查（`manuscript._abstract_length`）要跟风格包的字数上限用同一把尺子，
#: 否则同一篇稿子在两处量出两个字数。**跨模块用别人的下划线名迟早断**。
count_words = _words


def _sentences(body: str) -> list[str]:
    """分句：英文按 .!? 后接空白与大写，中文直接按 。！？切。"""
    parts: list[str] = []
    for chunk in re.split(r"(?<=[。！？])", body):
        parts.extend(_SENT.split(chunk))
    return [s for s in parts if 4 <= _words(s) <= 150]


def _table_nums(body: str) -> list[int]:
    return [int(m.group(1)) for m in re.finditer(r"(?:\bTable\s+|(?<!图)表\s*)(\d+)", body)]


def _figure_nums(body: str) -> list[int]:
    return [int(m.group(1)) for m in re.finditer(r"(?:\bFig(?:ure|\.)\s+|图\s*)(\d+)", body)]


def _cites(body: str) -> int:
    return len(_CITE.findall(body)) + len(_CITE_ZH.findall(body))


def _first_person(body: str) -> int:
    return len(re.findall(r"\b(?:we|our|us)\b", body, re.I)) + body.count("我们") + body.count("本文认为")


_META_LINE = re.compile(
    r"^(received:|revised:|accepted:|©|doi|https?://|advance access|original article|"
    r"\*?direct all correspondence|e-mail|phone:|university|department|\d+ [A-Z][a-z]+ (?:University|Institute))",
    re.I)


def _infer_abstract(text: str, intro_start: int) -> tuple[int, int] | None:
    """牛津系 PDF 的摘要没有「Abstract」标题——就是 Introduction 之前最后那段正文。

    判据：Introduction 之前、去掉元数据行（收稿日期、版权、DOI、单位、邮箱）之后，
    最后一个 ≥ 80 词的连续块。认错的话 profile 里会看出来（摘要词数离谱），
    所以宁可认不出留空，也不硬猜。
    """
    head = text[:intro_start]
    lines = head.split("\n")
    blocks: list[tuple[int, int]] = []
    pos, cur_start, cur_words = 0, None, 0
    for line in lines:
        s_ = line.strip()
        is_meta = bool(_META_LINE.match(s_)) or "@" in s_
        if s_ and not is_meta and len(s_) > 30:
            if cur_start is None:
                cur_start = pos
            cur_words += _words(s_)
        else:
            if cur_start is not None and cur_words >= 80:
                blocks.append((cur_start, pos))
            cur_start, cur_words = None, 0
        pos += len(line) + 1
    if cur_start is not None and cur_words >= 80:
        blocks.append((cur_start, pos))
    if not blocks:
        return None
    a, b = blocks[-1]
    return (a, b) if _words(text[a:b]) <= 400 else None


@dataclass
class PaperStats:
    file: str
    title: str = ""
    words_total: int = 0
    words_body: int = 0            # 正文：参考文献之前
    words_abstract: int = 0
    sections_found: list[str] = field(default_factory=list)
    section_words: dict[str, int] = field(default_factory=dict)
    sentence_mean: float = 0.0
    n_tables: int = 0
    n_figures: int = 0
    cites_per_1000: float = 0.0
    first_person_per_1000: float = 0.0
    citation_form: str = ""        # author-year / numeric
    has_limitations_heading: bool = False
    intro_opening: str = ""        # 引言前 120 词——skill 学「怎么开头」用
    discussion_opening: str = ""   # 讨论前 120 词——学「怎么过渡」用


def profile_paper(text: str, file: str = "") -> PaperStats:
    st = PaperStats(file=file)
    if not text.strip():
        return st
    spans = _section_spans(text)
    if "abstract" not in spans and "introduction" in spans:
        inferred = _infer_abstract(text, spans["introduction"][0])
        if inferred:
            spans["abstract"] = inferred
    ref_start = spans.get("references", (len(text), len(text)))[0]
    body = text[:ref_start]
    st.title = text.strip().split("\n", 1)[0][:120]
    st.words_total = _words(text)
    st.words_body = _words(body)
    if "abstract" in spans:
        a, b = spans["abstract"]
        st.words_abstract = _words(text[a:b])
    st.sections_found = [k for k, _ in sorted(spans.items(), key=lambda kv: kv[1][0])]
    st.section_words = {k: _words(text[a:b]) for k, (a, b) in spans.items() if k != "references"}
    sents = _sentences(body)
    st.sentence_mean = round(statistics.mean(_words(s) for s in sents), 1) if sents else 0.0
    st.n_tables = len(set(_table_nums(body)))
    st.n_figures = len(set(_figure_nums(body)))
    st.cites_per_1000 = round(_cites(body) / max(st.words_body, 1) * 1000, 1)
    st.first_person_per_1000 = round(_first_person(body) / max(st.words_body, 1) * 1000, 1)
    st.citation_form = citation_form(body)
    st.has_limitations_heading = "limitations" in spans
    for key, attr in (("introduction", "intro_opening"), ("discussion", "discussion_opening")):
        if key in spans:
            a, b = spans[key]
            seg = text[a:b].split("\n", 1)[-1]           # 去掉标题行
            setattr(st, attr, " ".join(seg.split()[:120]))
    return st


_NUMERIC_CITE = re.compile(r"[\[［]\d+(?:\s*[,，–\-]\s*\d+)*[\]］]")


def citation_form(body: str) -> str:
    """引用是作者-年份（社科主流）还是数字编号（GB/T 7714 顺序编码是后者）。只认两种，按哪种多算。"""
    numeric = len(_NUMERIC_CITE.findall(body))
    author_year = _cites(body)
    if not numeric and not author_year:
        return ""
    return "numeric" if numeric > author_year else "author-year"


def _rng(vals: list[float]) -> dict[str, float]:
    vals = [v for v in vals if v]
    if not vals:
        return {}
    return {"min": min(vals), "median": round(statistics.median(vals), 1), "max": max(vals)}


def build_profile(pack: Path) -> dict:
    """从 exemplars/ 抽骨架。同时把每篇的全文存成 .txt（本机读，不进仓库）。"""
    ex = pack / "exemplars"
    papers: list[PaperStats] = []
    for pdf in sorted(ex.glob("*.pdf")):
        txt_path = pdf.with_suffix(".txt")
        text = txt_path.read_text(encoding="utf-8") if txt_path.exists() else pdf_to_text(pdf)
        if not txt_path.exists() and text:
            txt_path.write_text(text, encoding="utf-8")
        papers.append(profile_paper(text, pdf.name))
    usable = [p for p in papers if p.words_body > 2000]
    prof = {
        "pack": pack.name,
        "n_exemplars": len(papers),
        "n_usable": len(usable),
        "ranges": {
            "words_body": _rng([p.words_body for p in usable]),
            "words_abstract": _rng([p.words_abstract for p in usable]),
            "sentence_mean": _rng([p.sentence_mean for p in usable]),
            "n_tables": _rng([p.n_tables for p in usable]),
            "n_figures": _rng([p.n_figures for p in usable]),
            "tables_plus_figures": _rng([p.n_tables + p.n_figures for p in usable]),
            "cites_per_1000": _rng([p.cites_per_1000 for p in usable]),
            "first_person_per_1000": _rng([p.first_person_per_1000 for p in usable]),
        },
        "section_order": _majority_order([p.sections_found for p in usable]),
        "citation_form": _majority([p.citation_form for p in usable]),
        "limitations_as_heading_share": round(
            sum(p.has_limitations_heading for p in usable) / max(len(usable), 1), 2),
        "papers": [asdict(p) for p in papers],
        "notes": [],
    }
    if len(usable) < 3:
        prof["notes"].append(
            f"只有 {len(usable)} 篇能用（抽出的正文 > 2000 词），**骨架不可靠**——至少要 3 篇")
    if any(not p.words_abstract for p in usable):
        prof["notes"].append("有范文没认出摘要（PDF 排版），摘要长度的区间只按认出的那几篇算")
    (pack / "style_profile.json").write_text(
        json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    return prof


def _majority(vals: list[str]) -> str:
    vals = [v for v in vals if v]
    if not vals:
        return ""
    return max(set(vals), key=vals.count)


def _majority_order(seqs: list[list[str]]) -> list[str]:
    """各篇节顺序的多数派：按每个节名的平均位次排。"""
    pos: dict[str, list[float]] = {}
    for seq in seqs:
        body = [s for s in seq if s != "references"]
        for i, k in enumerate(body):
            pos.setdefault(k, []).append(i / max(len(body) - 1, 1))
    return [k for k, _ in sorted(pos.items(), key=lambda kv: statistics.mean(kv[1]))]


# ------------------------------------------------------------------ 规则核对


#: 中文的数量单位。**中文期刊的须知几乎不写阿拉伯数字的整数**——
#: 写的是「不超 2 万字」「1.2 万—2 万字为宜」。只抓阿拉伯数字的话，
#: `word_limit: 20000` 这个**填对了**的值会被判成「须知里没有这个数」，
#: 于是每一个正确的中文风格包都过不了这道门（建第一个中文包时当场撞上）。
_CJK = re.compile(r"[\u4e00-\u9fff]")

#: 中文的数量单位
_CN_UNITS = {"亿": 100_000_000, "万": 10_000, "千": 1_000, "百": 100}


def _numbers_in(text: str) -> set[str]:
    """须知里出现过的数，含中文单位展开后的值。

    「2万」既记 `2` 也记 `20000`；「1.2万」既记 `1.2` 也记 `12000`。
    展开只加不减——原样的数仍然认，免得英文须知反而变严。
    """
    out = {n.replace(",", "") for n in re.findall(r"\d[\d,]*", text)}
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([亿万千百])", text):
        v = float(num) * _CN_UNITS[unit]
        out.add(str(int(v)) if v.is_integer() else str(v))
    return out


def _style_word_in(reference_style: str, guide_text: str) -> bool:
    """引用格式的说法，在须知原文里找得到吗。

    原先取 `split()[0]`——那是英文假设（「Chicago Manual of Style」取 Chicago）。
    中文串里没有空格，整串被当成一个词去找，于是「页下注（每页单独编号）……」
    这种照须知写的值反而找不到。改成：拆成拉丁词与汉字段，**任一段出现即算落回**。
    """
    toks = [w for w in re.findall(r"[A-Za-z]{2,}|[\u4e00-\u9fff]{2,}", reference_style)]
    low = guide_text.lower()
    return any(tok.lower() in low for tok in toks)


def check_rules_grounded(rules: StyleRules, guide_text: str) -> list[str]:
    """填进 rules.json 的每个数字，须知原文里必须有。**编一个上限跟编一个系数一样。**"""
    nums = _numbers_in(guide_text)
    problems = []
    for name in ("word_limit", "word_min", "max_tables_figures",
                 "abstract_max_words", "supplementary_max_pages"):
        v = getattr(rules, name)
        if v is not None and str(int(v)) not in nums:
            problems.append(f"rules.{name} = {v}，但须知原文里没有这个数——要么抄错了，要么须知没说，没说就留空")
    if rules.reference_style and not _style_word_in(rules.reference_style, guide_text):
        problems.append(f"rules.reference_style = {rules.reference_style!r}，须知原文里找不到其中任何一个词")
    problems += _layout_grounded(rules, guide_text, nums)
    return problems


#: 版式里**带数字**的那几格。页边距是小数（2.5），须知里也写作 2.5，直接按字面找。
_LAYOUT_NUMS = ("margin_top_cm", "margin_bottom_cm", "margin_left_cm", "margin_right_cm",
                "column_gap_cm", "page_number_start")


def _layout_grounded(rules: StyleRules, guide_text: str, nums: set[str]) -> list[str]:
    """版式里的数字同样要在须知原文里。

    **这一层原先不查。**于是「页边距上下各 2.5 厘米」可以凭印象填进去而没人拦——
    跟编一个字数上限是同一件事，而且更隐蔽：读者打开 Word 看到的就是这个版式，
    会以为它来自该刊的要求。分栏数与单双面同理：须知没说就别声明。
    """
    page = getattr(rules, "layout", None)
    if page is None:
        return []
    out = []
    for name in _LAYOUT_NUMS:
        v = getattr(page, name)
        if v is None:
            continue
        # 2.5 与 2.50 在须知里可能任一种写法；整数格（起始页）按整数找
        forms = {str(v), str(v).rstrip("0").rstrip("."), str(int(v)) if float(v).is_integer() else ""}
        if not ({f for f in forms if f} & _numbers_in(guide_text) or
                any(f and f in guide_text for f in forms)):
            out.append(f"rules.layout.{name} = {v}，但须知原文里没有这个数——"
                       "版式也是须知的一部分，没说就留 None，别按印象填")
    # 注文的字体字号同样要落回须知：这两格会真的改成品的排版，
    # 按印象填一个「小五仿宋」，读者打开 Word 看到的就是它，会以为那是该刊的要求。
    for name, val in (("paper_size", page.paper_size),
                      ("body_font", page.body_font), ("body_size", page.body_size),
                      ("footnote_font", page.footnote_font), ("footnote_size", page.footnote_size)):
        if val and val not in guide_text:
            out.append(f"rules.layout.{name} = {val!r}，须知原文里没有这个说法——"
                       "照抄须知里的字体名与字号名，须知没说就留空")
    if page.columns > 1 and str(page.columns) not in nums:
        out.append(f"rules.layout.columns = {page.columns}，须知原文里没有这个数——"
                   "投稿稿多为一栏，分栏是排印阶段的事，须知明说了才写")
    return out


# ------------------------------------------------------------------ 七道门


_BLIND_HITS = re.compile(
    r"\b(acknowledg(?:e)?ments?|funding|grant (?:no|number)|supported by (?:the )?[A-Z]|"
    r"we thank|the authors? thanks?|corresponding author)\b", re.I)


def check_paper(
    rendered: str,
    rules: StyleRules | None,
    profile: dict | None,
    exemplar_texts: dict[str, str] | None = None,
    *,
    ngram: int = 8,
) -> tuple[list[str], list[str]]:
    """对着风格包查成稿。返回 `(拒绝理由, 警告)`。

    硬规矩（rules）违反 → 拒绝；骨架（profile）偏离 → 警告；
    跟范文连续 `ngram` 个词相同 → 拒绝（**学结构，不抄句子**）。
    """
    problems: list[str] = []
    warnings: list[str] = []
    text = rendered
    spans = _section_spans(text)
    ref_start = spans.get("references", (len(text), len(text)))[0]
    body = text[:ref_start]
    body_words = _words(body)
    total_words = _words(text)

    if rules is not None:
        # **「含不含参考文献」原先只写在提示文字里，没用在计算上**——
        # 不管须知怎么说，算的都是全文，于是不含参考文献的刊被判得过严。
        counted = total_words if rules.word_limit_includes_references else body_words
        scope = "含" if rules.word_limit_includes_references else "不含"
        if rules.word_limit and counted > rules.word_limit:
            problems.append(
                f"全文 {counted} 词，超过 {rules.journal or '该刊'} 的上限 {rules.word_limit}"
                f"（{scope}参考文献）")
        # 下限只警告不拦：须知那句是「以 X—Y 字为宜」，是建议不是硬杠。
        # 但**漏掉它等于只管了一半**——写得太短一样退稿。
        if rules.word_min and counted < rules.word_min:
            warnings.append(
                f"全文 {counted} 词，低于 {rules.journal or '该刊'} 的下限 {rules.word_min}"
                f"（{scope}参考文献）——须知给的是区间，短了一样不合体例")
        n_tf = len(set(_table_nums(body))) + len(set(_figure_nums(body)))
        if rules.max_tables_figures and n_tf > rules.max_tables_figures:
            problems.append(f"图表共 {n_tf} 个，超过上限 {rules.max_tables_figures}")
        if rules.double_blind:
            for m in _BLIND_HITS.finditer(body):
                line = body[: m.start()].count("\n") + 1
                problems.append(
                    f"第 {line} 行有「{m.group(0)}」——双盲评审的正文里不能有致谢/基金/作者信息，"
                    f"这些放单独的标题页")
                break
        if "abstract" in spans:
            # **取摘要正文，不是整节。**原先按节切，于是「## 摘要」这行标题与
            # 「**关键词**：……」那一行都算进字数里，一篇 298 字的摘要量出来 334 字——
            # 而 `oss paper check` 那边用的是 `_abstract_body`，两个数不一样。
            # 数法（`count_words`）本来就共用一份，取法也共用，才不会两头对不上。
            from oss.core.manuscript import _abstract_body

            a, b = spans["abstract"]
            abs_text = _abstract_body(text).strip() or text[a:b]
            if rules.abstract_no_citations and (_CITE.search(abs_text) or _CITE_ZH.search(abs_text) or "{{cite" in abs_text):
                problems.append("摘要里有引用——该刊要求摘要不含引用")
            if rules.abstract_max_words and _words(abs_text) > rules.abstract_max_words:
                problems.append(f"摘要 {_words(abs_text)} 词，超过上限 {rules.abstract_max_words}")
        # 图表编号必须按出现顺序（Table/表、Figure/图 两种写法都认）
        for kind, nums in (("Figure/图", _figure_nums(body)), ("Table/表", _table_nums(body))):
            seen: list[int] = []
            for n in nums:
                if n not in seen:
                    seen.append(n)
            if seen != sorted(seen) or (seen and seen[0] != 1):
                problems.append(f"{kind} 的编号不按出现顺序：{seen}——须知要求按首次出现的顺序连续编号")

    if profile and profile.get("ranges"):
        r = profile["ranges"]

        def out_of(name: str, val: float, label: str) -> None:
            rg = r.get(name) or {}
            if rg and (val < rg["min"] or val > rg["max"]):
                warnings.append(
                    f"{label} = {val}，范文里是 {rg['min']}–{rg['max']}（中位 {rg['median']}）——"
                    f"不是错，但跟该刊的常态差得远，看一眼")

        out_of("words_body", body_words, "正文词数")
        if "abstract" in spans:
            a, b = spans["abstract"]
            out_of("words_abstract", _words(text[a:b]), "摘要词数")
        sents = _sentences(body)
        if sents:
            out_of("sentence_mean", round(statistics.mean(_words(s) for s in sents), 1), "平均句长")
        n_tf = len(set(_table_nums(body))) + len(set(_figure_nums(body)))
        out_of("tables_plus_figures", n_tf, "图表数")
        out_of("cites_per_1000", round(_cites(body) / max(body_words, 1) * 1000, 1), "每千词引用数")
        out_of("first_person_per_1000",
               round(_first_person(body) / max(body_words, 1) * 1000, 1),
               "每千词第一人称（we/our/us/我们）")
        want = profile.get("citation_form")
        got = citation_form(body)
        if want and got and want != got:
            warnings.append(f"引用形式是 {got}，范文全是 {want}——导出时 citeproc 会按该刊样式排，正文里别混用")

    if exemplar_texts:
        for name, hit, unit in overlaps(body, exemplar_texts, ngram=ngram):
            problems.append(
                f"跟范文《{name[:40]}》有连续 {unit}相同：「{hit}」——"
                f"**学结构，不抄句子**。换个说法")
    return problems, warnings


def _is_cjk_heavy(text: str) -> bool:
    sample = text[:4000]
    return len(_CJK.findall(sample)) > 0.2 * max(len(sample), 1)


def _shingles(text: str, n: int) -> dict[str, str]:
    toks = [t.lower() for t in _WORD.findall(text)]
    out: dict[str, str] = {}
    for i in range(len(toks) - n + 1):
        out[" ".join(toks[i:i + n])] = " ".join(toks[i:i + n])
    return out


def _shingles_zh(text: str, n: int) -> dict[str, str]:
    """中文按**字**滚窗。只留汉字与拉丁字母数字（标点、空白不算——换个逗号不算换了说法）。"""
    chars = "".join(c for c in text if _CJK.match(c) or c.isalnum()).lower()
    return {chars[i:i + n]: chars[i:i + n] for i in range(len(chars) - n + 1)}


def overlaps(paper: str, exemplar_texts: dict[str, str], *, ngram: int = 8,
             ngram_zh: int = 13, limit: int = 5) -> list[tuple[str, str, str]]:
    """成稿与每篇范文的重合。英文连续 `ngram` 词，中文连续 `ngram_zh` 字（知网查重的经验口径）。

    返回 `(范文名, 重合片段, 单位说明)`，每篇最多几条。按**范文**的语言选口径——
    查的是「抄没抄范文」，尺子跟着被抄的一方走。
    """
    mine_en = _shingles(paper, ngram)
    mine_zh = _shingles_zh(paper, ngram_zh)
    hits: list[tuple[str, str, str]] = []
    for name, text in exemplar_texts.items():
        if _is_cjk_heavy(text):
            theirs = _shingles_zh(text, ngram_zh)
            common = [k for k in mine_zh if k in theirs]
            unit = f"{ngram_zh} 个字"
        else:
            theirs = _shingles(text, ngram)
            common = [k for k in mine_en if k in theirs]
            unit = f"{ngram} 个词"
        for k in common[:limit]:
            hits.append((name, k, unit))
    return hits


# ------------------------------------------------------------------ 骨架 → 模板
#
# 风格包原来只是门（写完拦一下）；这里把它变成模具（写之前决定长什么样）。
# 范文的多数派节顺序 + 各节的中位词数 → paper.md 的模板；校验按同一个骨架查节。
# 没有范文就用标准论文格式——规范即可，不做特殊要求。

SECTION_TITLES = {
    "abstract": "Abstract", "introduction": "Introduction", "background": "Background",
    "data_methods": "Data and Methods", "results": "Results", "discussion": "Discussion",
    "limitations": "Limitations", "conclusion": "Conclusion", "references": "References",
}

#: 中文节名（不带编号——编号在出模板时按实际节数现编，砍掉一节不会留下断号）
SECTION_TITLES_ZH = {
    "abstract": "摘要", "introduction": "引言", "background": "文献与理论",
    "data_methods": "数据与方法", "results": "结果", "discussion": "讨论",
    "limitations": "局限", "conclusion": "结论", "references": "参考文献",
}

_ZH_NUMERALS = "一二三四五六七八九十"

#: 标准论文格式（没有风格包时）
DEFAULT_SKELETON = ("abstract", "introduction", "background", "data_methods", "results",
                    "discussion", "conclusion")


def _section_medians(profile: dict) -> dict[str, int]:
    vals: dict[str, list[int]] = {}
    for p in profile.get("papers", []):
        if (p.get("words_body") or 0) <= 2000:
            continue
        for k, w in (p.get("section_words") or {}).items():
            if w:
                vals.setdefault(k, []).append(int(w))
    return {k: int(statistics.median(v)) for k, v in vals.items() if v}


def skeleton(profile: dict | None, *, lang: str = "en") -> list[dict]:
    """写作模板的骨架：节、顺序、目标词数。有范文按范文的多数派，没有按标准格式。

    `limitations` 只在**过半**范文把它单列成节时才单列，否则并进讨论。
    `lang="zh"` 用中文节名（编号在出模板时现编）。
    """
    titles = SECTION_TITLES_ZH if lang == "zh" else SECTION_TITLES
    if not profile or not profile.get("section_order"):
        return [{"key": k, "title": titles[k], "words": None} for k in DEFAULT_SKELETON]
    order = [k for k in profile["section_order"] if k != "references"]
    if "limitations" in order and (profile.get("limitations_as_heading_share") or 0) < 0.5:
        order.remove("limitations")
    words = _section_medians(profile)
    return [{"key": k, "title": titles.get(k, k.replace("_", " ").title()),
             "words": words.get(k)} for k in order]


def template_markdown(skel: list[dict], *, pack: str = "", lang: str = "en") -> str:
    """骨架 → paper.md 的起稿模板。目标词数写在注释里，References 和 {{references}} 放好。

    中文模板照国内社科期刊的通例：正文节编「一、二、……」；摘要下一行放**关键词**；
    参考文献由 `{{references}}` 生成，**英文题名、摘要、关键词排在参考文献之后**
    （《社会学研究》：参考文献「列于全文之后，英文标题、摘要、关键词之前」）。
    """
    if lang == "zh":
        return _template_markdown_zh(skel, pack=pack)
    lines = ["# <Title: no more than 15 words>", ""]
    for s in skel:
        lines.append(f"## {s['title']}")
        lines.append("")
        if s["key"] == "abstract":
            # 跟中文模板对齐：原先英文每节下面只有一个空注释，模型拿不到任何指引
            lines += ["<!-- One paragraph, four moves: question -> what data -> main findings -> "
                      "what it means. Give a concrete magnitude a reader understands without the "
                      "model (percentage points, years, dollars); numbers come from the results, "
                      "never typed. Keep standard errors, p-values, MDE and sample size out of the "
                      "abstract - they belong in the text and tables. Robustness checks belong in "
                      "the text too. End on what it means, not on a number -->", "",
                      "**Keywords:** <3-5, semicolon-separated>", ""]
            continue
        if s.get("words"):
            src = f" in {pack} exemplars" if pack else ""
            lines.append(f"<!-- target ~{s['words']} words (median{src}) -->")
        else:
            lines.append("<!-- -->")
        lines.append("")
    lines += ["## References", "", "{{references}}", ""]
    return "\n".join(lines)


def _template_markdown_zh(skel: list[dict], *, pack: str = "") -> str:
    lines = ["# <标题：不超过 20 字>", ""]
    n = 0
    for s in skel:
        if s["key"] == "abstract":
            lines += ["## 摘要", "",
                      "<!-- 一段，**300 字以内**。四句：问题 → 用了什么数据 → 主要发现 → 这意味着什么。",
                      "     发现要给**具体的量**，而且是读者不看模型就懂的量（「概率低 X 个百分点」「收入高 X 元」），",
                      "     数字从结果引、不手写。",
                      "     **不要写**标准误、p 值、最小可探测效应、样本量——它们在正文和表里报（实测 59 篇",
                      "     已发表的中文摘要，这四样一篇都没有）。**不要写**稳健性检验，那是正文的事。",
                      "     「本文」在开头出现一次就够。末句给含义，别停在数字上 -->", "",
                      "**关键词**：<3–5 个；分号隔开>", ""]
            continue
        n += 1
        num = _ZH_NUMERALS[n - 1] if n <= len(_ZH_NUMERALS) else str(n)
        lines.append(f"## {num}、{s['title']}")
        lines.append("")
        if s.get("words"):
            src = f"（{pack} 范文中位数）" if pack else ""
            lines.append(f"<!-- 目标约 {s['words']} 字{src} -->")
        else:
            lines.append("<!-- -->")
        lines.append("")
    # 英文题名、摘要、关键词排在**参考文献之后**——《社会学研究》的规定是
    # 参考文献「列于全文之后，英文标题、摘要、关键词之前」。原先排在参考文献前面，位置是错的
    lines += ["## 参考文献", "", "{{references}}", "",
              "## Abstract", "",
              "<!-- 文后英文摘要。目标刊不要就整段删掉（不影响校验）；要印在首页的刊，"
              "把这一整段移到中文摘要下面 -->", "",
              "**<English title: 与中文标题对应>**", "",
              "<!-- 与中文摘要对应的英文摘要，数字跟中文摘要引同一批结果 -->", "",
              "**Keywords:** <与中文关键词对应>", ""]
    return "\n".join(lines)


def csl_for(reference_style: str) -> str:
    """须知里的引用格式说法 → 包里带的 CSL 样式名。认不出就 Chicago 作者-年份（社科通用）。

    中文期刊多数写「按 GB/T 7714」或「参考文献采用顺序编码制」——两种国标样式都带：
    顺序编码（[1][2]，缺省）和著者-出版年。
    """
    raw = reference_style or ""
    s = raw.lower()
    # **页下注是第三种体例，原先没有出口。**《中国社会科学》要「注释请用页下注」，
    # 而这里只有顺序编码与著者-出版年两条路，于是脚注体例的刊只能落到夹注上——
    # 跟须知直接冲突，而且成稿看起来「挺像」，不细看发现不了。
    if "页下注" in raw or "脚注" in raw or "note" in s:
        return "china-national-standard-gb-t-7714-2015-note"
    # **中文须知一律先按国标判。**原先只认「著者」「顺序编码」这几个术语，
    # 而须知常写成一句白话（「在引文后加括号注明作者、出版年份及页码」），
    # 于是落到最后的 Chicago 兜底上——**中文刊拿到英文体例**。
    # 更坏的是：不带风格包时 `default_csl_name` 按语言判、给的是国标，
    # 于是「用了风格包」反而比「不用」更不对。
    if _CJK.search(raw):
        if "顺序编码" in raw or "连续编号" in raw or "序号" in raw:
            return "china-national-standard-gb-t-7714-2015-numeric"
        return "china-national-standard-gb-t-7714-2015-author-date"
    if "7714" in s or "国标" in s or "gb" in s or "顺序编码" in s or "著者" in s:
        if "著者" in s or "出版年" in s or "author" in s:
            return "china-national-standard-gb-t-7714-2015-author-date"
        return "china-national-standard-gb-t-7714-2015-numeric"
    if "apa" in s or "american psychological" in s:
        return "apa"
    if "asa" in s or "american sociological" in s:
        return "american-sociological-association"
    return "chicago-author-date"


def load_pack(pack: Path) -> tuple[StyleRules | None, dict | None, dict[str, str]]:
    """读一个风格包：规则、骨架、范文全文。缺什么返回 None / 空。"""
    rules = None
    if (pack / "rules.json").exists():
        rules = StyleRules.load(pack / "rules.json")
    profile = None
    if (pack / "style_profile.json").exists():
        profile = json.loads((pack / "style_profile.json").read_text(encoding="utf-8"))
    texts = {p.stem: p.read_text(encoding="utf-8") for p in (pack / "exemplars").glob("*.txt")}
    return rules, profile, texts
