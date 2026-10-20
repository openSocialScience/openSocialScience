"""切句与取节：**为准的那一份**在这里，判据只在这里改。

**为什么有这个模块。**同一件事从前散在好几处，而它们早就不一致了：有的认
`et al.` 的句点是句末，有的不认；有的只认 markdown 标题，有的连 PDF 抽出的
纯文本也认。补门的洞之前先立一份为准的，后面每补一条判据才只改这一处。

**取哪一份为准**：切句沿用 `causal_wording` 的句末表（`et al.` / `e.g.` /
`vs.` 的句点不算句末）；取节沿用 `style` 的那份——中英双语、吃 PDF 纯文本、
会剥「一、」这类编号前缀，是四份里能力最强的。

**收到哪一步了（2026-09-16 盘点）。不是「全仓库只剩这一份」**——合并只做在
必经之路上（设计稿第二节）。真正改接的是两处：`style` 的取节、`manuscript`
判显著性那处切句。**取节四份仍是四份**，只是 `style` 那份换成了本模块这一份。

**切句里故意没并的**：

- `manuscript.sentences_with` / `_span`：占位符上下文专用。用整句还是分句，
  取决于同句里有没有同类占位符——那套取舍是三次真跑校准出来的。
- `causal_wording._sentence`：按行做的，`scan` 报的列号绑着它。
  它跟本模块共用同一张句末表（`_DELIM`），但切的粒度不同。
- `style._sentences`：把「4 到 150 词才算一句」的过滤跟切句揉在一起，英文按
  「句点后接空白与大写」切、不认这里的句末表。并进来风格包的句长句数会变。
- `manuscript.mde_warnings`、`causal_wording.sim_reality_warnings`：这两条
  **天生要一个比句子宽的窗口**，各自按行与中文句末切。MDE 的病症跨句
  （「MDE 是 X。因此可以排除更大的效应」），仿真的豁免也跨句（「在本模型内，
  参数上升；现实中将……」）——切到句子一个漏拦、一个误报。两处函数里各写了
  为什么，不要「顺手」统一。

**取节里故意没并的**：

- `manuscript._section_body`：单关键词、**子串**匹配，摘要那一处还在用它。
  局限那几条检查已经换到本模块（实施计划 Task 7）。换之前担心的是
  「## 讨论与局限」这类合并标题：节名表是 `^` 锚定的，`limitations` 匹配不上。
  实测**不必为此放宽节名表**——这种标题归到 `discussion`，
  `section_body(text, "limitations", "discussion")` 退一步就取到了正文
  （`tests/test_textspan.py` 三条钉住）。反过来放宽 `discussion` 还会把
  「研究局限与讨论」从 `limitations` 抢走，是退步。
- `skillcheck` 的「尚未解决」那一节：不在这里的八个节名里，判据又刻意是
  「认标题、不认散文里的同一句话」，而 `section_spans` 连 PDF 纯文本行也认。
  并进来要么给节名表加一个键（`style` 多切一刀、各节字数跟着变），要么削弱它
  自己的规则——见实施计划 Task 1 的执行注记。
- `evals/benchmark/check_exam.py`：评测侧的尺子。尺子一变就得升版本号，
  不跟着生产代码走。

调用方：`section_spans` 由 `style` 用着，`section_body` 由 `manuscript` 的
局限两条（功效 / 精度、共线）用着。
"""

from __future__ import annotations

import re

# ------------------------------------------------------------------ 切句

#: 句末。「et al.」「e.g.」「vs.」的句点不是——否则「Smith et al. (2020) find that…」的出处落在上一句
_DELIM = re.compile(r"[。！？!?；;]|(?<!\bet al)(?<!\be\.g)(?<!\bi\.e)(?<!\bvs)(?<!\bcf)\.(?=\s|$)")

#: 全文切句的边界：句末标点，**外加换行**。正文是手工折行的，一行常常就是一句；
#: 切在换行处最多把上下文放窄一点，不会让检查失明。
#: （占位符上下文反过来，单个换行故意不算边界——见 `manuscript._SENT_BOUND`：
#: 那里把 `\n` 当句末会把跨行的引用上下文截在半句上，所有跨行的检查都失效。）
_SENT_BOUND = re.compile(_DELIM.pattern + r"|\n")


def sentences(text: str) -> list[tuple[int, int, str]]:
    """全文切句，返回每句的 (起, 止, 原文)。

    止在句末标点之后，原文含标点——调用方要报「哪一句」时连标点一起引，
    要回到原文时 `text[起:止]` 就是那一句。只有空白的片段不成句，不返回。
    """
    out: list[tuple[int, int, str]] = []
    lo = 0
    for m in _SENT_BOUND.finditer(text):
        if text[lo:m.end()].strip():
            out.append((lo, m.end(), text[lo:m.end()]))
        lo = m.end()
    if text[lo:].strip():
        out.append((lo, len(text), text[lo:]))
    return out


# ------------------------------------------------------------------ 取节

#: 社科论文的常见节名。**按关键词定位，不按排版猜**——PDF 抽出来的排版不可靠。
#: 中文节名并在同一张表里：一行只会命中一种语言的写法，两种混在正则里没有副作用。
#: 「一、」「1.」这类编号前缀在 `section_spans` 里先剥掉，这里不用管；
#: 「（一）」是小节记号，**故意不剥**——剥了它，小节标题会把大节切碎。
_SECTION_KEYS: list[tuple[str, str]] = [
    ("abstract", r"^\s*(?:abstract\b|(?:内容)?(?:摘要|提要))"),
    ("introduction", r"^\s*(?:introduction\b|引言|导言|绪论|问题的提出|研究背景)"),
    ("background", r"^\s*(?:background|theor(?:y|etical)|literature|prior|previous research|conceptual"
     r"|文献|理论|研究假设|相关研究|分析框架)"),
    ("data_methods", r"^\s*(?:data|methods?|research design|analytic(?:al)? strategy|measures|empirical strategy"
     r"|数据|方法|研究设计|变量|模型设定|实证策略)"),
    ("results", r"^\s*(?:results|findings|empirical results|(?:实证|回归|分析)?结果|实证分析|研究发现)"),
    ("discussion", r"^\s*(?:discussion|conclusions?|discussion and conclusion|讨论|结论|结语|总结)"),
    ("limitations", r"^\s*(?:limitations?\b|(?:研究)?局限|不足)"),
    ("references", r"^\s*(?:references|bibliography|works cited|参考文献)\s*$"),
]

#: 中文正文的编号前缀：「一、」「（1）」不算——只剥大节的「一、」「1.」「1、」
_ZH_NUM_PREFIX = re.compile(r"^(?:[一二三四五六七八九十]+|\d{1,2})[、\.．]\s*")


def section_spans(text: str) -> dict[str, tuple[int, int]]:
    """各节在文本里的起止位置（起点是标题行行首）。找不到的节不出现在结果里。"""
    lines = text.split("\n")
    marks: list[tuple[int, str]] = []
    pos = 0
    for line in lines:
        # 成稿是 markdown（`## Results`），范文是 PDF 抽出的纯文本（`Results`）——
        # 两种都要认。先把标题记号剥掉再比。
        s = re.sub(r"^[#*\s]+", "", line.strip()).strip("* ")
        s = _ZH_NUM_PREFIX.sub("", s)
        if 0 < len(s) <= 60:
            for key, pat in _SECTION_KEYS:
                if re.match(pat, s, re.I) and not s.endswith((".", "。")):
                    marks.append((pos, key))
                    break
        pos += len(line) + 1
    # 同一节名可能出现多次（目录、页眉）——取**首次**出现且顺序合理的
    seen: dict[str, int] = {}
    for p, k in marks:
        if k not in seen:
            seen[k] = p
    order = sorted(seen.items(), key=lambda kv: kv[1])
    spans: dict[str, tuple[int, int]] = {}
    for i, (k, start) in enumerate(order):
        end = order[i + 1][1] if i + 1 < len(order) else len(text)
        spans[k] = (start, end)
    return spans


def section_body(text: str, *keys: str) -> str:
    """按节名取正文（**不含标题行**）。

    `keys` 是 `_SECTION_KEYS` 的键（`"limitations"`、`"discussion"`……），
    命中第一个就返回；一个都没有返回空串。
    """
    spans = section_spans(text)
    for key in keys:
        if (span := spans.get(key)) is None:
            continue
        head_end = text.find("\n", span[0])
        return text[head_end + 1:span[1]] if 0 <= head_end < span[1] else ""
    return ""
