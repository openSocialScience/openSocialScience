"""文献检索。多源查询 → 规范化 → 去重补齐 → 语料。

从旧实现抢救（阶段 2）。**保住的四条全是实测踩出来的**，重写一定想不到：

**① 别用 OpenAlex 的顶层 `search` 参数。**
它是跨全文的模糊匹配。配上按被引排序之后，弱匹配 + 全库排序会把**全库最高引的
论文**捞上来——实测检索「数字化与城乡收入差距」返回引力波探测、全球疾病负担、
单细胞测序。必须用 `title_and_abstract.search`。
（对照：ARIS 的 `litsearch.py` 用的就是顶层 `search`。）

**② 被引排序只能在本地做。**
即便加了字段过滤，API 层的被引排序仍会把 World Development Report 这类
「碰巧含这些词的万引大部头」顶到最前。按相关性多取一批，本地重排——
「相关文献里被引最高的」才是广度检索要的东西。

**③ 命中太少往往不是「没人做过」，是检索式把自己卡死了。**
引号短语取交会打 0（`relax()`），八个词的裸词袋要求每个词都命中、实测只有几条。
命中不足 `MIN_HITS` 就沿 `degrade_ladder()` 自动降一档，并在 notes 里说清降过——
**降档前的少命中不构成空白证据**。

**④ 源不可用 ≠ 检索到 0 条。**
0 条是证据（可能真的没有），源挂了不是证据，不能拿来支撑「这是研究空白」。

这类 bug 有个共同点：**不报错，只安静地污染整个语料**，
而下游每一个「研究空白」都建立在这批语料上。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from oss.core.contracts import LiteratureCorpus, PaperRecord, Screening, SnowballStats

OPENALEX = "https://api.openalex.org"
CROSSREF = "https://api.crossref.org"

RETRYABLE = {429, 500, 502, 503, 504}
CACHE_TTL_DAYS = 30

#: 引号短语的正则。OpenAlex 把 `"..."` 当**严格短语**匹配。
_PHRASE = re.compile(r'"([^"]+)"')

#: 中文核心刊白名单（`scripts/build_zh_whitelist.py` 建的表）。
_ZH_SOURCES_PATH = Path(__file__).resolve().parents[1] / "assets" / "zh_core_sources.json"


def _load_zh_sources() -> tuple[list[str], int, int]:
    """白名单 → `(源记录 id 表, 清单里的刊数, OpenAlex 里有记录的刊数)`。文件缺失或坏了就 `([], 0, 0)`。

    读不到**不是错误**：装了包但没跑过建表脚本是正常状态，`search()` 见到空表会提醒去跑脚本，
    而不是拿一条不限源的 `language:zh` 打出去——那回来的是满库的低端刊与学位论文，
    冒充「核心刊历史层」进语料，下游没有一步会报错。

    **两个刊数不是一回事，报数时不能混。**清单里有 25 本，OpenAlex 里搜得到源记录的只有
    20 本（经济研究、中国社会科学、中国行政管理、教育研究、开放时代五本没有中文版源记录）。
    只报「白名单 25 本」会让人以为这五本也查过了——它们在历史层里是彻底的盲区。
    """
    try:
        d = json.loads(_ZH_SOURCES_PATH.read_text(encoding="utf-8"))
        ids = [str(x) for x in (d.get("source_ids") or []) if x]
        journals = d.get("journals") or {}
        covered = sum(1 for v in journals.values() if (v or {}).get("sources"))
        return ids, len(journals), covered
    except (OSError, ValueError, AttributeError):
        return [], 0, 0


ZH_SOURCES, ZH_JOURNALS, ZH_COVERED = _load_zh_sources()

#: 一个 OpenAlex 过滤器最多塞 50 个值，超了要分批（白名单现在 23 条源记录，留着给它长）。
_ZH_BATCH = 50


class SourceUnavailable(RuntimeError):
    """这个源暂时用不了（限流、宕机、缺 key）。

    **与「检索到 0 条」必须区分。**0 条是证据，源不可用不是证据——
    拿它去支撑「这是研究空白」是错的。所以它一路传到 `LiteratureCorpus.notes`。
    """

    def __init__(self, source: str, reason: str) -> None:
        super().__init__(f"{source} 不可用：{reason}")
        self.source = source
        self.reason = reason


# --------------------------------------------------------------- 0 命中放松


def relax(q: str) -> str | None:
    """把一条打了 0 命中的检索式放松一档，放不动就返回 None。

    **现在它是 `degrade_ladder()` 的第一档**（去引号），不再单独被 `search` 调用。

    实测出来的坑，而且它安静得可怕：

        "internet use"                              → 31544 命中
        "urban-rural income gap"                    →  1233 命中
        "internet use" AND "urban-rural income gap" →     0 命中   ← 交集竟然为空

    OpenAlex 的 `title_and_abstract.search` 对引号短语做严格匹配，多个短语再
    AND 起来几乎必然落空；而 Crossref 走词袋相关性，同一条检索式在它那里能返回
    几十条。**同一个字符串在两个源里含义根本不同。**

    后果是链式的：OpenAlex 是唯一提供摘要与引文网络的源（实测带摘要 77% /
    带引文 70%，Crossref 是 30% / 0%）。它被检索式打空之后语料退化成 Crossref
    独撑，摘要覆盖掉到 29%，作者自陈的局限抽不出来，贡献先验最高的
    「方法缺陷」类空白**在信号层面**就消失了（Crossref 还在发现循环时的实测）。

    **只放松一档**——去引号，走词干化的词袋匹配（实测 0→55、0→10、0→501，
    量级正合适）。**刻意不改成 OR**：`"A" OR "B"` 实测 32777 命中，
    那不是放松，那是把检索变成噪声。
    """
    if '"' not in q:
        return None
    relaxed = _PHRASE.sub(r"\1", q)
    relaxed = re.sub(r"\b(AND|OR|NOT)\b", " ", relaxed)
    relaxed = " ".join(relaxed.split())
    return relaxed or None


# --------------------------------------------------------------- 概念块


#: OpenAlex 命中低于这个数就往下降一档。八词裸词袋实测 0–13 条，三词 67–219 条。
MIN_HITS = 10


def _normalize(q: str) -> str:
    """把检索式的毛边削掉，**整条链上只做这一次**。

    - **不支持 NOT**（OpenAlex 的 `title_and_abstract.search` 不认），遇到就当没写——
      连它否定的那一项（一个词、一个引号短语、或一个括号组）一起丢。留着 NOT 是多一个
      搜不到的词，留着被否定的词是**反着搜**；光杆的尾部 NOT 没东西可否定，也丢。
    - 丢完会留下挨着的算子（`a AND NOT b` → `a AND  AND c`），合并掉，
      否则 `AND AND` 会被当成一个同义词。
    - 首尾多出来的 AND / OR 剥掉，否则 `AND x` 会整个当成一个同义词送出去。

    **`parse_blocks` 和 `degrade_ladder` 都从这里进。**只在 `parse_blocks` 里削是不够的：
    `degrade_ladder` 还要拿字符串去 `relax()`、去数引号，拿的要是同一个字符串，
    否则 NOT 掉的词会在去引号那一档整个长回来。

    引号短语**首尾**的算子不动：`"AND gate"` 开头那个 AND 贴着引号，剥首尾算子的两条
    正则要的是词边界前后的空白，够不着它。但短语**内部**的大写 NOT 仍会被当成算子——
    上面第一条正则不看引号，`"signal NOT detected"` 会被削成 `"signal`。
    真要搜带 NOT 的短语，把 NOT 写成小写。
    """
    s = re.sub(r'\bNOT\s+("[^"]+"|\([^)]*\)|\S+)', " ", q.strip())
    s = re.sub(r"\s*\bNOT\s*$", " ", s)
    s = re.sub(r"\b(AND|OR)(?:\s+(?:AND|OR)\b)+", r"\1", s)
    s = re.sub(r"^(?:\s*(?:AND|OR)\b)+\s*", "", s)
    s = re.sub(r"(?:\s*\b(?:AND|OR))+\s*$", "", s)
    return " ".join(s.split())


def parse_blocks(q: str) -> list[list[str]]:
    """把检索式拆成概念块：`[[同义词, …], …]`，块内 OR，块间 AND。

    两种写法都认：
    - 概念块：`("a b" OR c) AND (d OR "e f")` → [["a b", "c"], ["d", "e f"]]
    - 旧写法（裸词袋，可带引号短语）：`a b "c d"` → [["c d"], ["a"], ["b"]]——每个词一块，
      这样「降档」在两种写法下是同一个动作：丢掉最后一块。
    AND / OR 只认大写，跟 OpenAlex 一样；NOT 与首尾算子见 `_normalize`。
    """
    s = _normalize(q)
    if re.search(r"\b(AND|OR)\b", s):
        blocks: list[list[str]] = []
        for part in re.split(r"\s+AND\s+", s):
            part = re.sub(r"[()]", " ", part)
            terms = [t.strip().strip('"').strip() for t in re.split(r"\s+OR\s+", part)]
            terms = [t for t in terms if t]
            if terms:
                blocks.append(terms)
        return blocks
    phrases = [p.strip() for p in _PHRASE.findall(s) if p.strip()]
    rest = re.sub(r"[()]", " ", _PHRASE.sub(" ", s))
    return [[p] for p in phrases] + [[w] for w in rest.split()]


def build_openalex(blocks: list[list[str]]) -> str:
    """概念块 → OpenAlex 检索式。**多词同义词必须加引号**：OpenAlex 的 OR 只绑相邻的单词，
    `compulsory schooling OR schooling reform` 会被读成 compulsory AND (schooling OR schooling) AND reform。
    逗号是 filter 的分隔符，去掉。"""
    def term(t: str) -> str:
        t = t.replace(",", " ").strip()
        return f'"{t}"' if " " in t else t
    parts = []
    for b in blocks:
        inner = " OR ".join(term(t) for t in b)
        parts.append(f"({inner})" if len(b) > 1 else inner)
    return " AND ".join(parts)


def bag_of_words(q: str) -> str:
    """概念块 → 词袋引擎（Crossref / Semantic Scholar）用的检索式：每块取第一个同义词。
    把 OR AND 括号原样递过去只会多出噪声词。旧写法原样回来。"""
    return " ".join(b[0] for b in parse_blocks(q))


def degrade_ladder(q: str) -> list[str]:
    """从原式到最松的一档：OpenAlex 命中不足 `MIN_HITS` 就往下走一档。

    旧写法（裸词袋带引号、没有 OR）第一档是去引号——DECISIONS 4.8 那条实测坑，0→55。
    之后丢掉**最后的块**，最少留两块。所以 skill 要把最核心的概念写在第一块。

    **最多 4 次调用（原式、去引号、三块、两块）。**块超过四个时不逐块丢：八个词逐块丢要打
    七次 API，而匿名 OpenAlex 一天只有 100 次测试额度，跑一次基准就见底。

    去重按**块结构**，不按字符串：`"education" fertility` 去引号之后拆出来的块跟原式
    一模一样，字符串却不同（引号还在），不按结构去重就会把同一次检索打两遍。
    """
    q = _normalize(q)  # 归一化只做一次：下面的 relax 与数引号要跟 parse_blocks 看同一个串
    blocks = parse_blocks(q)
    rungs = [build_openalex(blocks)]
    if '"' in q and not re.search(r"\bOR\b", q) and (loose := relax(q)):
        rungs.append(loose)
        blocks = parse_blocks(loose)
    if len(blocks) > 4:
        tails = [blocks[:3], blocks[:2]]
    else:
        tails = [blocks[:i] for i in range(len(blocks) - 1, 1, -1)]
    rungs += [build_openalex(b) for b in tails]

    out: list[str] = []
    seen: list[list[list[str]]] = []
    for rung in rungs:
        shape = parse_blocks(rung)
        if shape in seen:
            continue
        seen.append(shape)
        out.append(rung)
    return out


# ------------------------------------------------------------------- 缓存


class Cache:
    """按 URL 缓存原始响应。

    存在的理由不是省时间，是**断额度时还能继续开发**：
    OpenAlex 匿名每天只有 100 个测试额度、免费密钥每天 1 美元；
    有缓存才能反复调下游逻辑而不烧额度。
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.home() / ".cache" / "oss" / "literature"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, source: str, key: str) -> Path:
        return self.root / f"{source}_{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"

    def get(self, source: str, key: str) -> dict | None:
        p = self._path(source, key)
        if not p.exists():
            return None
        if time.time() - p.stat().st_mtime > CACHE_TTL_DAYS * 86400:
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, source: str, key: str, body: dict) -> None:
        try:
            self._path(source, key).write_text(
                json.dumps(body, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # 缓存写不进去不该让检索失败


def _client() -> httpx.Client:
    """本模块统一的 HTTP 客户端。

    三个入口（`fetch_by_doi` / `search` / `snowball`）以前各抄一份 timeout 和 User-Agent，
    改一处漏两处。OpenAlex 的礼貌池按 User-Agent 认人，这串不该各处不一样。
    """
    return httpx.Client(timeout=30, headers={"User-Agent": "oss-literature/0.3"})


# --------------------------------------------------------------- 规范化


def _clean_doi(raw: str | None) -> str:
    if not raw:
        return ""
    return raw.replace("https://doi.org/", "").replace("http://dx.doi.org/", "").strip().lower()


def reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str:
    """OpenAlex 存的是倒排索引，得还原成文本。

    没有这一步，`abstract_coverage` 会永远是 0，而它是判断
    「能不能挖出方法缺陷类空白」的依据。
    """
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        positions.extend((i, word) for i in idxs)
    positions.sort()
    return " ".join(w for _, w in positions)


#: 去重时可以从重复记录补齐的字段。
_FILLABLE = ("abstract", "doi", "year", "authors", "oa_url", "tldr")


def _fill(kept: PaperRecord, other: PaperRecord) -> bool:
    """用 `other` 单向补齐 `kept` 的空缺，被引数取大的。真改了才返回真。

    补齐是单向的：先来的源更权威，它已有的值不被覆盖，只填它的空缺。
    直接丢掉重复记录是浪费——Crossref 有权威的期刊元数据，
    OpenAlex 有摘要和引文，同一篇论文两边各有各的强项。
    去重（`dedupe`）和滚雪球撞见已有记录时是同一件事，共用这一份。
    """
    changed = False
    for f in _FILLABLE:
        if not getattr(kept, f, None) and (v := getattr(other, f, None)):
            setattr(kept, f, v)
            changed = True
    # 被引数取大的：某个源没同步到，不等于这篇论文没被引用过
    if other.citation_count > kept.citation_count:
        kept.citation_count = other.citation_count
        changed = True
    return changed


def dedupe(records: list[PaperRecord]) -> list[PaperRecord]:
    """按 DOI 去重，并用后来的重复记录**补齐**先来者缺的字段（见 `_fill`）。"""
    seen: dict[str, PaperRecord] = {}
    out: list[PaperRecord] = []
    for r in records:
        key = r.doi or f"title::{r.title.lower().strip()}"
        if not key or key == "title::":
            continue
        kept = seen.get(key)
        if kept is None:
            seen[key] = r
            out.append(r)
            continue
        _fill(kept, r)
    return out


# ------------------------------------------------------------------- 源


@dataclass
class SourceResult:
    records: list[PaperRecord]
    relaxed: bool = False
    note: str = ""
    hits_total: int = 0


class OpenAlexSource:
    """OpenAlex。唯一同时提供摘要与引文网络的免费源。"""

    name = "openalex"

    def __init__(self, client: httpx.Client, cache: Cache, *, max_retries: int = 3) -> None:
        self._client = client
        self._cache = cache
        self._max_retries = max_retries
        self._mailto = os.getenv("OPENALEX_MAILTO", "")
        # 2026-02-13 起 OpenAlex 要求所有请求带密钥；mailto 只进礼貌池，不解决 409/429。
        self._api_key = os.getenv("OPENALEX_API_KEY", "")

    def search(self, q: str, limit: int = 20, *, year_from: int | None = None) -> SourceResult:
        tail = ([f"from_publication_date:{year_from}-01-01"] if year_from else []) + ["type:article"]
        # 按相关性多取一批，本地重排。见模块 docstring ②
        params = {"per-page": min(max(limit * 3, limit), 200), "sort": "relevance_score:desc"}
        ladder = degrade_ladder(q)
        # 归一化后什么都不剩（比如整条式子只有一个 `NOT`）：空检索式送进去，
        # `title_and_abstract.search:` 后面是空的，OpenAlex 回的是**全库前 N 篇**——
        # 一次白花的额度，换回来一堆跟题目毫无关系的记录。
        if not ladder or not ladder[0].strip():
            return SourceResult(records=[], note="检索式归一化后为空（只剩算子？），OpenAlex 这条没跑",
                                hits_total=0)
        data: dict = {}
        count, step, used = 0, 0, ladder[0]
        for step, used in enumerate(ladder):
            # 见模块 docstring ①：必须用 title_and_abstract.search
            data = self._get("works", {**params, "filter": ",".join([f"title_and_abstract.search:{used}", *tail])})
            # meta.count 是全库命中数（不止这一页）；没有 meta 时退回本页条数，
            # 宁可低估——低估只会多降一档，高估会让降档在真该降的时候不降
            count = int((data.get("meta") or {}).get("count") or len(data.get("results") or []))
            if count >= MIN_HITS:
                break
        records = [r for item in data.get("results", []) if (r := self._to_record(item))]
        records.sort(key=lambda r: -r.citation_count)
        note = ""
        if step:
            note = (f"OpenAlex 对 {q!r} 命中不足 {MIN_HITS}，自动降了 {step} 档 → {used!r}（{count} 命中）。"
                    f"**降档前的少命中不构成空白证据**——OpenAlex 要求所有词都命中，长检索式几乎必然打空")
        return SourceResult(records=records[:limit], relaxed=bool(step), note=note, hits_total=count)

    def search_zh(self, q: str, limit: int, sources: list[str],
                  *, year_from: int | None = None) -> SourceResult:
        """只在白名单源里查中文作品。`oss lit search --zh` 的历史层。

        跟 `search()` 差三处，每处都是实测逼出来的：
        - **限源**。不限源的 `language:zh` 回来的是满库低端刊与学位论文；限在核心刊白名单里
          才是「中文顶刊里做过什么」。50 个 id 一批（OpenAlex 每个过滤器最多 50 个值）。
        - **不加 `type:article`**。OpenAlex 给中文刊文章标的 type 很杂（`paper`、`other`
          都有），加上这一条能把整本刊筛没。
        - **不走降档梯子**。梯子是给「所有词都命中」的长检索式救场的；限了源之后候选池本来
          就只有几百到几千篇，降档只会把不相关的中文文章灌进来。

        **含汉字的词一律加引号**（`GDP增长` 这种中英混写的也算）。2026-09-05 三发对照（同一批白名单源，各打一次）：

            title_and_abstract.search:农民工     → 254 命中
            title_and_abstract.search:"农民工"   →  81 命中
            title_and_abstract.search:工民农     → 254 命中   ← 倒序，跟正序一模一样

        倒序与正序同解，说明 **OpenAlex 对中文是按「字袋」匹配的**：不加引号的「农民工」
        等于「含农、民、工这三个字」，讲「农民」的、讲「工人」的全算命中，多出来的三分之二
        是噪声。加引号才是短语匹配。西文词不动——它本来就按词匹配，加引号反而卡死。
        """
        tail = [f"from_publication_date:{year_from}-01-01"] if year_from else []
        # 闸是「含不含汉字」，不是「全是不是汉字」：`GDP增长`、`COVID疫情` 这种中英混写的词
        # 在 OpenAlex 那边照样按字袋拆（「增」「长」各算一命中），只放纯汉字词进闸就漏了它们。
        oa_q = build_openalex([[f'"{t}"' if _CJK_RUN.search(t) else t for t in b]
                               for b in parse_blocks(q)])
        records: list[PaperRecord] = []
        total = 0
        for i in range(0, len(sources), _ZH_BATCH):
            chunk = sources[i:i + _ZH_BATCH]
            flt = ",".join([f"title_and_abstract.search:{oa_q}", "language:zh",
                            f"primary_location.source.id:{'|'.join(chunk)}", *tail])
            data = self._get("works", {"filter": flt, "sort": "relevance_score:desc",
                                       "per-page": min(max(limit * 3, limit), 200)})
            total += int((data.get("meta") or {}).get("count") or 0)
            records.extend(r for item in (data.get("results") or []) if (r := self._to_record(item)))
        for r in records:
            r.language = "zh"
        records.sort(key=lambda r: -r.citation_count)
        return SourceResult(records=records[:limit], hits_total=total)

    def by_doi(self, doi: str) -> PaperRecord | None:
        """按 DOI 直接抓一篇。

        用来**补检索没召回的经典文献**。规则「引用必须来自语料」有个副作用：
        检索漏掉的经典（比如一本 1967 年的书）会逼人要么不引、要么造假。
        所以要给一条正路——而且这条路是**留痕的、可核的**，
        不是在正文里偷偷写一个名字。
        """
        clean = doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")
        data = self._get(f"works/https://doi.org/{clean}", {})
        return self._to_record(data) if data else None

    def works(self, flt: str, *, sort: str = "cited_by_count:desc",
              per_page: int = 50) -> list[PaperRecord]:
        """按过滤器列作品，滚雪球用。

        `search` 走的是主题检索（`title_and_abstract.search` + 降档梯子）；引文链要的是
        另一类过滤器——`cited_by:W…`（W 引用的）、`cites:W…`（引用 W 的）。给它一条
        公开的路，外面就不必去碰 `_get` / `_to_record` 这些私有件。
        """
        data = self._get("works", {"filter": flt, "sort": sort,
                                   "per-page": min(max(int(per_page), 1), 200)})
        return [r for item in (data.get("results") or []) if (r := self._to_record(item))]

    def _to_record(self, item: dict) -> PaperRecord | None:
        title = (item.get("display_name") or "").strip()
        if not title:
            return None
        return PaperRecord(
            paper_id=(item.get("id") or "").rsplit("/", 1)[-1] or title[:40],
            title=title,
            year=item.get("publication_year"),
            authors=[
                a["author"]["display_name"]
                for a in (item.get("authorships") or [])
                if a.get("author", {}).get("display_name")
            ][:10],
            doi=_clean_doi(item.get("doi")),
            venue=(((item.get("primary_location") or {}).get("source") or {}).get("display_name") or ""),
            volume=str((item.get("biblio") or {}).get("volume") or ""),
            issue=str((item.get("biblio") or {}).get("issue") or ""),
            pages=_pages((item.get("biblio") or {}).get("first_page"), (item.get("biblio") or {}).get("last_page")),
            abstract=reconstruct_abstract(item.get("abstract_inverted_index")),
            citation_count=int(item.get("cited_by_count") or 0),
            source=self.name,
            oa_url=(
                ((item.get("primary_location") or {}).get("pdf_url"))
                or ((item.get("open_access") or {}).get("oa_url"))
                or ((item.get("best_oa_location") or {}).get("pdf_url"))
                or ""
            ),
        )

    def _key_advice(self, status: int) -> str:
        """被拒时该说什么。2026-02-13 起 OpenAlex 要求所有请求带密钥（匿名每天 100 个测试额度，
        之后 409/429）；免费密钥每天 1 美元额度，约 1000 次检索。**不回退匿名调用**：
        报不可用，由其它源撑起语料，notes 里说清（aipoch/open-science 同一做法）。"""
        if not self._api_key:
            return (f"HTTP {status}：OpenAlex 自 2026-02-13 起要求密钥，匿名每天只有 100 个测试额度。"
                    f"到 https://openalex.org/settings/api 免费注册一把，.env 里填 OPENALEX_API_KEY=…"
                    f"（每天 1 美元额度，约 1000 次检索，一次运行用几十次）")
        return (f"HTTP {status}：密钥被拒或今日额度耗尽（UTC 零点重置）。"
                f"核对 OPENALEX_API_KEY 是否抄全、是否激活；额度见 https://openalex.org/settings/api")

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict:
        if self._mailto:
            params = {**params, "mailto": self._mailto}
        if self._api_key:
            params = {**params, "api_key": self._api_key}

        key = f"{endpoint}?{sorted(params.items())}"
        if (cached := self._cache.get(self.name, key)) is not None:
            return cached

        last = ""
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._client.get(f"{OPENALEX}/{endpoint}", params=params)
            except httpx.RequestError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 200:
                    body = resp.json()
                    self._cache.put(self.name, key, body)
                    return body
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
                # 404 只在单篇查询上是「没这篇」；列表端点 404 是坏了，
                # 报不可用——**源不可用 ≠ 检索到 0 条**
                if resp.status_code == 404 and endpoint.startswith("works/"):
                    return {}
                if resp.status_code in (401, 403, 409) or (
                        resp.status_code == 429 and "budget" in resp.text.lower()):
                    # 密钥问题不是暂时性故障，重试只是浪费时间
                    advice = self._key_advice(resp.status_code)
                    if resp.status_code == 403:
                        # 403 也可能是检索式/过滤器写坏了，不一定是密钥——把原话带上
                        advice += f"（响应：{resp.text[:120]}）"
                    raise SourceUnavailable(self.name, advice)
                if resp.status_code not in RETRYABLE:
                    raise SourceUnavailable(self.name, last)
            time.sleep(min(2 ** (attempt - 1) + random.uniform(0, 0.4), 20.0))

        raise SourceUnavailable(self.name, f"重试 {self._max_retries} 次仍失败：{last}")


SEMANTIC_SCHOLAR = "https://api.semanticscholar.org/graph/v1"


class SemanticScholarSource:
    """Semantic Scholar。带摘要、一句话总结（tldr）和开放获取 PDF 链接。

    参考库的 `semantic-scholar` skill 说它免费无 key。**实测第一次请求就 429**：
    没 key 的调用共用一个全局配额池，撞墙是常态。所以这里
    - 退避比别的源长（首次等 3 秒起）
    - 有 `S2_API_KEY` 就带上（免费，但要填表申请）；密钥被网关拒了就报不可用，**不退回无密钥**
    - 撞到底就报「不可用」，**不是没结果**——`_diagnose` 会把它翻译成后果
    - 没 S2_API_KEY 时 search() 根本不列它为发现源（共用全局配额，实测每次 429）

    它的价值在于 OpenAlex 没摘要的那一半里，它常常有。
    """

    name = "semanticscholar"
    _FIELDS = "title,year,abstract,tldr,openAccessPdf,citationCount,externalIds,authors,venue,journal"

    def __init__(self, client: httpx.Client, cache: Cache, *, max_retries: int = 4) -> None:
        self._client = client
        self._cache = cache
        self._max_retries = max_retries
        self._api_key = os.getenv("S2_API_KEY", "")

    def search(self, q: str, limit: int = 20, *, year_from: int | None = None) -> SourceResult:
        params: dict[str, Any] = {"query": bag_of_words(q), "limit": min(max(limit, 1), 100),
                                  "fields": self._FIELDS}
        if year_from:
            params["year"] = f"{year_from}-"
        data = self._get("paper/search", params)
        records = [r for item in data.get("data", []) if (r := self._to_record(item))]
        records.sort(key=lambda r: -r.citation_count)
        return SourceResult(records=records[:limit], relaxed=False)

    def by_doi(self, doi: str) -> PaperRecord | None:
        clean = doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")
        data = self._get(f"paper/DOI:{clean}", {"fields": self._FIELDS})
        return self._to_record(data) if data else None

    def _to_record(self, item: dict) -> PaperRecord | None:
        title = (item.get("title") or "").strip()
        if not title:
            return None
        ext = item.get("externalIds") or {}
        return PaperRecord(
            paper_id=item.get("paperId") or title[:40],
            title=title,
            year=item.get("year"),
            authors=[a.get("name", "") for a in (item.get("authors") or []) if a.get("name")][:10],
            doi=_clean_doi(ext.get("DOI")),
            venue=(item.get("venue") or ((item.get("journal") or {}).get("name")) or ""),
            volume=str((item.get("journal") or {}).get("volume") or ""),
            pages=str((item.get("journal") or {}).get("pages") or ""),
            abstract=(item.get("abstract") or "").strip(),
            citation_count=int(item.get("citationCount") or 0),
            source=self.name,
            oa_url=((item.get("openAccessPdf") or {}).get("url") or ""),
            tldr=((item.get("tldr") or {}).get("text") or ""),
        )

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict:
        key = f"{endpoint}?{sorted(params.items())}"
        if (cached := self._cache.get(self.name, key)) is not None:
            return cached
        headers = {"x-api-key": self._api_key} if self._api_key else {}
        last = ""
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._client.get(f"{SEMANTIC_SCHOLAR}/{endpoint}", params=params, headers=headers)
            except httpx.RequestError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 200:
                    body = resp.json()
                    self._cache.put(self.name, key, body)
                    return body
                if resp.status_code == 404:
                    return {}
                last = f"HTTP {resp.status_code}: {resp.text[:160]}"
                if resp.status_code == 403 and headers:
                    # 网关的 ForbiddenException = 密钥没被认出来（没激活、抄错、或是别家的）。
                    # 没密钥的 S2 已经不进发现循环（每次 429），退回无密钥只是换一种失败——
                    # 跟 OpenAlex 一样：报不可用、不回退，由 notes 说清该查什么。
                    raise SourceUnavailable(
                        self.name, f"{last}。密钥被网关拒绝：检查 S2_API_KEY 是否激活、是否抄全")
                if resp.status_code not in RETRYABLE:
                    raise SourceUnavailable(self.name, last)
            # 共用配额池，退避要比别的源长
            time.sleep(min(3 * 2 ** (attempt - 1) + random.uniform(0, 1.0), 30.0))
        raise SourceUnavailable(
            self.name,
            f"重试 {self._max_retries} 次仍失败：{last}。带密钥仍被限流或服务不稳——稍后重跑；"
            f"连续如此就核对 S2_API_KEY 是否为本人有效密钥",
        )


class CrossrefSource:
    """Crossref。期刊 DOI 的权威，免费无 key。

    走**词袋相关性**，跟 OpenAlex 的严格短语匹配完全不同——
    同一条检索式在两边含义不一样，这正是 `relax()` 存在的原因。

    代价：实测带摘要 30%、带引文 0%。它补的是元数据，不是语义。
    """

    name = "crossref"

    def __init__(self, client: httpx.Client, cache: Cache) -> None:
        self._client = client
        self._cache = cache
        self._mailto = os.getenv("CROSSREF_MAILTO", "")

    def search(self, q: str, limit: int = 20, *, year_from: int | None = None) -> SourceResult:
        params: dict[str, Any] = {"query.bibliographic": bag_of_words(q), "rows": min(limit, 100)}
        if year_from:
            params["filter"] = f"from-pub-date:{year_from}-01-01"
        data = self._get("works", params)
        items = (data.get("message") or {}).get("items") or []
        return SourceResult(records=[r for i in items if (r := self._to_record(i))])

    def by_doi(self, doi: str) -> PaperRecord | None:
        """按 DOI 直接抓一篇。`fetch_by_doi` 的备胎：OpenAlex 额度断了，经典也得补得进。"""
        clean = doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")
        data = self._get(f"works/{clean}", {})
        return self._to_record(data.get("message") or {}) if data else None

    def _to_record(self, item: dict) -> PaperRecord | None:
        titles = item.get("title") or []
        title = (titles[0] if titles else "").strip()
        if not title:
            return None
        parts = (item.get("issued") or {}).get("date-parts") or [[]]
        year = parts[0][0] if parts and parts[0] else None
        doi = _clean_doi(item.get("DOI"))
        return PaperRecord(
            paper_id=doi or title[:40],
            title=title,
            year=year,
            authors=[
                " ".join(filter(None, (a.get("given"), a.get("family"))))
                for a in (item.get("author") or [])
            ][:10],
            doi=doi,
            venue=((item.get("container-title") or [""])[0] or ""),
            volume=str(item.get("volume") or ""),
            issue=str(item.get("issue") or ""),
            pages=str(item.get("page") or ""),
            abstract=re.sub(r"<[^>]+>", "", item.get("abstract") or "").strip(),
            citation_count=int(item.get("is-referenced-by-count") or 0),
            source=self.name,
        )

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict:
        if self._mailto:
            params = {**params, "mailto": self._mailto}
        key = f"{endpoint}?{sorted(params.items())}"
        if (cached := self._cache.get(self.name, key)) is not None:
            return cached
        try:
            resp = self._client.get(f"{CROSSREF}/{endpoint}", params=params)
        except httpx.RequestError as exc:
            raise SourceUnavailable(self.name, f"{type(exc).__name__}: {exc}") from exc
        # 跟 OpenAlex 对称：404 只在单篇查询上是「没这篇」，列表端点 404 是坏了
        if resp.status_code == 404 and endpoint.startswith("works/"):
            return {}
        if resp.status_code != 200:
            raise SourceUnavailable(self.name, f"HTTP {resp.status_code}: {resp.text[:120]}")
        body = resp.json()
        self._cache.put(self.name, key, body)
        return body


# ------------------------------------------------------------------ 聚合


@dataclass
class SearchOutcome:
    corpus: LiteratureCorpus
    failed: dict[str, str] = field(default_factory=dict)


def fetch_by_doi(doi: str, *, client: httpx.Client | None = None,
                 cache: Cache | None = None) -> PaperRecord | None:
    """按 DOI 抓一篇文献。给 `oss lit add` 用——**补检索没召回的经典文献**。

    先 OpenAlex（带摘要、带开放获取链接），它不可用或查不到就走 Crossref。
    两边都不可用才报不可用；两边都查不到才是 None——**源坏了不等于这篇不存在**。
    """
    own = client is None
    client = client or _client()
    cache = cache or Cache()
    try:
        oa_err: SourceUnavailable | None = None
        try:
            rec = OpenAlexSource(client, cache).by_doi(doi)
        except SourceUnavailable as exc:
            oa_err, rec = exc, None
        if rec is not None:
            return rec
        try:
            return CrossrefSource(client, cache).by_doi(doi)
        except SourceUnavailable as exc:
            if oa_err is not None:
                raise SourceUnavailable(
                    "openalex+crossref", f"OpenAlex：{oa_err.reason}；Crossref 也不可用：{exc.reason}"
                ) from exc
            raise
    finally:
        if own:
            client.close()


#: 「阵营: 检索式」——冒号前是理论阵营的名字（不超过 40 字，中英文冒号都认）
_CAMP = re.compile(r"^\s*([^:：\"]{1,40}?)\s*[:：]\s*(\S.*)$")


def split_camp(q: str) -> tuple[str, str]:
    """`"威权回应性: selectorate responsiveness"` → (阵营, 检索式)。没写阵营就 ("", 原样)。"""
    m = _CAMP.match(q)
    if not m or "://" in q:
        return "", q.strip()
    return m.group(1).strip(), m.group(2).strip()


# ------------------------------------------------------------------ 全文的更多来源


def oa_locations(doi: str, client: httpx.Client) -> list[str]:
    """OpenAlex 记录里**所有**开放获取的 PDF 链接（不只 primary）。

    要 OPENALEX_API_KEY（2026-02-13 起），没配就只能拿到记录自带的链接——
    这里把非 200 当成「没有开放获取版本」，所以匿名被 409/429 挡住时**全文会静悄悄地消失**。
    """
    clean = _clean_doi(doi)
    if not clean:
        return []
    params = {"select": "locations,best_oa_location,open_access"}
    if (m := os.getenv("OPENALEX_MAILTO", "")):
        params["mailto"] = m
    if (k := os.getenv("OPENALEX_API_KEY", "")):
        params["api_key"] = k
    try:
        r = client.get(f"https://api.openalex.org/works/https://doi.org/{clean}", params=params, timeout=20)
        if r.status_code != 200:
            return []
        d = r.json()
    except (httpx.RequestError, ValueError):
        return []
    urls: list[str] = []
    for loc in [d.get("best_oa_location") or {}, *(d.get("locations") or [])]:
        if loc.get("is_oa") and loc.get("pdf_url"):
            urls.append(loc["pdf_url"])
    if (oa := (d.get("open_access") or {}).get("oa_url")):
        urls.append(oa)
    return list(dict.fromkeys(u for u in urls if u))


def unpaywall_pdf(doi: str, client: httpx.Client) -> list[str]:
    """Unpaywall：按 DOI 找开放获取版本。**要一个邮箱**（UNPAYWALL_EMAIL），没配就跳过（`oss doctor` 会报）。"""
    email = os.getenv("UNPAYWALL_EMAIL", "")
    clean = _clean_doi(doi)
    if not email or not clean:
        return []
    try:
        r = client.get(f"https://api.unpaywall.org/v2/{clean}", params={"email": email}, timeout=20)
        if r.status_code != 200:
            return []
        d = r.json()
    except (httpx.RequestError, ValueError):
        return []
    urls = []
    for loc in [d.get("best_oa_location") or {}, *(d.get("oa_locations") or [])]:
        if loc.get("url_for_pdf"):
            urls.append(loc["url_for_pdf"])
    return list(dict.fromkeys(urls))


def core_pdf(doi: str, client: httpx.Client) -> list[str]:
    """CORE（core.ac.uk）：全球机构库的聚合，**要密钥**（CORE_API_KEY，免费申请），没配就跳过。"""
    key = os.getenv("CORE_API_KEY", "")
    clean = _clean_doi(doi)
    if not key or not clean:
        return []
    try:
        r = client.get("https://api.core.ac.uk/v3/search/works",
                       params={"q": f'doi:"{clean}"', "limit": 3},
                       headers={"Authorization": f"Bearer {key}"}, timeout=20)
        if r.status_code != 200:
            return []
        d = r.json()
    except (httpx.RequestError, ValueError):
        return []
    return list(dict.fromkeys(x.get("downloadUrl") for x in (d.get("results") or []) if x.get("downloadUrl")))


def _norm_title(t: str) -> str:
    """标题归一化：只留字母数字与汉字，全部小写。用来判两个标题是不是同一篇。"""
    return "".join(c.lower() for c in (t or "") if c.isalnum())


def search_by_title(title: str, *, client: "httpx.Client | None" = None,
                    cache: "Cache | None" = None) -> PaperRecord | None:
    """按标题找权威题录——PDF 里没印 DOI 时的第二层。

    **宁可返回 None，也不要配错。**配错的元数据在产出物上一个字都看不出来：
    题目是对的、作者年份是别人的，引用就成了假的。所以只在归一化后的标题
    **完全相等或一方包含另一方**时才采用；差一点就交给第三层标 `[UNVERIFIED]`，
    让用户确认或补题录文件。
    """
    key = _norm_title(title)
    if len(key) < 8:          # 太短的猜测（页眉、栏目名）不值得拿去检索
        return None
    try:
        corpus = search([title], limit=5, client=client, cache=cache)
    except SourceUnavailable:
        return None           # 源坏了不等于这篇不存在——交第三层，不当作「查不到」处理
    for rec in corpus.records:
        got = _norm_title(rec.title)
        if got and (got == key or got in key or key in got):
            return rec
    return None


def search(
    queries: list[str],
    *,
    limit: int = 20,
    year_from: int | None = None,
    zh: list[str] | tuple[str, ...] = (),
    client: httpx.Client | None = None,
    cache: Cache | None = None,
) -> LiteratureCorpus:
    """多源检索并汇成语料。

    发现源只有 OpenAlex（摘要、引文网络、开放获取）与 Semantic Scholar（一句话总结）。
    Crossref 是 DOI 注册处：只在 OpenAlex 不可用时兜底，平时只做按 DOI 解析（`fetch_by_doi`）。
    同一篇论文由两边返回时，保留先来的并补齐它的空缺。

    `zh` 是**中文历史层**的检索式（写法跟 `queries` 一样，认「阵营: 检索式」）：只在
    `ZH_SOURCES`（核心刊白名单）里查 `language:zh`。它跟英文检索式分开走，因为中文那半
    的覆盖是有边界的——OpenAlex 里这些刊大多断在 2018 年前，之后的要 `oss lit import`
    从知网导出文件导入。这条边界写进 notes，不写就会被当成「中文文献没人做过」。
    """
    own_client = client is None
    client = client or _client()
    cache = cache or Cache()

    # Crossref 不再进发现循环：它是 DOI 注册处不是搜索引擎，返回的是预印本、会议摘要、
    # 书章节，七成没摘要；第一期实测语料六到九成来自它，把 OpenAlex 的摘要与引文全冲淡了。
    # 它只在 OpenAlex 不可用时兜底（无密钥、额度耗尽），并把「兜底」二字写进 notes。
    # S2 没密钥时共用全局配额，实测每次 429：每条检索式白等三秒再等一次，还多一条警告。
    # 它能补的只有 tldr 和 OpenAlex 缺的那部分摘要——配了密钥才值得问。
    openalex = OpenAlexSource(client, cache)
    sources: list = [openalex]
    if os.getenv("S2_API_KEY"):
        sources.append(SemanticScholarSource(client, cache))
    crossref = CrossrefSource(client, cache)
    crossref_queries: list[str] = []
    collected: list[PaperRecord] = []
    hits: dict[str, int] = {}
    failed: dict[str, str] = {}
    relaxed_any = False
    notes: list[str] = []
    # 「阵营: 检索式」——按阵营各出一条，哪个阵营 0 命中一眼看得见
    parsed = [split_camp(q) for q in queries]
    camps = {q: c for c, q in parsed if c}
    bare = [q for _, q in parsed]
    hits_by_query: dict[str, int] = {}
    zh_parsed = [split_camp(q) for q in zh]

    def _ingest(name: str, result: SourceResult) -> None:
        """把一个源的结果并进语料：记源命中、记这条检索式的命中、补阵营标签。
        发现循环和 Crossref 兜底共用一份——两处各写一遍的话，改一处就会漏一处。"""
        nonlocal n_q
        hits[name] = hits.get(name, 0) + len(result.records)
        n_q += len(result.records)
        for rec in result.records:
            if camp and not rec.camp:
                rec.camp = camp
        collected.extend(result.records)

    try:
        for camp, q in parsed:
            n_q = 0
            # 归一化后什么都不剩（整条只有一个 NOT 之类）：一个源都别跑。
            # OpenAlex 那道闸只挡得住它自己——`bag_of_words` 同样吐空串，
            # 于是 S2 的 `query=`（兜底时还有 Crossref 的 `query.bibliographic=`）照样打出去，
            # 回来的是「最近的随便什么论文」，然后它们冒充这条检索式的结果进语料。
            if not _normalize(q):
                notes.append(f"检索式「{q}」归一化后为空（只剩算子？），发现源一个都没跑")
                hits_by_query[q] = 0
                continue
            # NOT 是被 `_normalize` 静悄悄丢掉的——连同它否定的那个词。
            # 不说的话，用户看到的是一份「比自己写的更宽」的语料，却以为排除生效了。
            if re.search(r"\bNOT\b", q):
                notes.append(f"检索式「{q}」里的 NOT 不支持，已丢掉被否定的词再检索——结果比你写的更宽")
            for src in sources:
                if src.name in failed:
                    continue  # 已经确认不可用，别再撞
                try:
                    result = src.search(q, limit, year_from=year_from)
                except SourceUnavailable as exc:
                    failed[src.name] = exc.reason
                    continue
                _ingest(src.name, result)
                relaxed_any = relaxed_any or result.relaxed
                if result.note:
                    notes.append(result.note)
            if "openalex" in failed and crossref.name not in failed:
                try:
                    result = crossref.search(q, limit, year_from=year_from)
                except SourceUnavailable as exc:
                    failed[crossref.name] = exc.reason
                else:
                    _ingest(crossref.name, result)
                    crossref_queries.append(q)
            hits_by_query[q] = n_q

        # 中文历史层。英文那批跑完再跑：它走的是另一条过滤器（限源 + `language:zh`），
        # 跟降档梯子、Crossref 兜底都不相干，混进主循环只会把两套逻辑缠在一起。
        for camp, q in zh_parsed:
            n_q = 0
            bare.append(q)          # 不进 `corpus.queries` 的话，`rank()` 与相关度都看不见它们
            if camp:
                camps[q] = camp
            if not ZH_SOURCES:
                # 空白名单 + 不限源 = 满库的 `language:zh`（低端刊、学位论文）冒充历史层。
                # 宁可 0 条，把「先跑建表脚本」说出来。同一条提醒只说一次。
                if not any("中文白名单为空" in n for n in notes):
                    notes.append("中文白名单为空——先跑 scripts/build_zh_whitelist.py")
                hits_by_query[q] = 0
                continue
            # 跟英文那半同一道闸：归一化后什么都不剩的式子送进去，
            # `title_and_abstract.search:` 后面是空的，回来的是白名单里的前 N 篇。
            if not _normalize(q):
                notes.append(f"中文检索式「{q}」归一化后为空（只剩算子？），历史层这条没跑")
                hits_by_query[q] = 0
                continue
            # 英文那半已经确认 OpenAlex 挂了的话，历史层走的是同一个源、同一把密钥，不必再撞。
            # **登记在「白名单为空」那道闸之后**：白名单空时历史层一次都没打，再报一句
            # 「源 openalex_zh 不可用」是把两个不相干的原因摆在一起，读的人不知道该去建表
            # 还是去修密钥。一条 `--zh` 都没给时循环根本不进，也就不会凭空多出一个从没打算跑的源。
            if "openalex" in failed:
                failed.setdefault("openalex_zh", failed["openalex"])
            if "openalex_zh" in failed:
                hits_by_query[q] = 0
                continue
            try:
                result = openalex.search_zh(q, limit, ZH_SOURCES, year_from=year_from)
            except SourceUnavailable as exc:
                failed["openalex_zh"] = exc.reason
                hits_by_query[q] = 0
                continue
            _ingest("openalex_zh", result)  # `language="zh"` 由 `search_zh` 标，这里只登记
            hits_by_query[q] = n_q
        if zh and ZH_SOURCES:
            notes.append(
                # 命中数写在最前面：论文阶段按「语料里有没有中文记录」放行新颖性主张，
                # 读 notes 的人要一眼看见这个数，不该再去翻 hits_by_source。
                f"中文历史层命中 {hits.get('openalex_zh', 0)} 条："
                f"只覆盖 OpenAlex 里 2018 年前的核心刊"
                f"（白名单 {ZH_JOURNALS} 本、OpenAlex 里有记录 {ZH_COVERED} 本、"
                f"{len(ZH_SOURCES)} 条源记录），"
                f"2018 年后要用 oss lit import 导入")
    finally:
        if own_client:
            client.close()

    if crossref_queries:
        which = "、".join(f"「{q}」" for q in crossref_queries)
        notes.append(f"⚠ OpenAlex 不可用，{len(crossref_queries)}/{len(parsed)} 条检索式由 Crossref 兜底"
                     f"（{which}，共 {hits.get(crossref.name, 0)} 条）：没摘要、没引文、混着预印本与会议摘要。"
                     f"配好 OPENALEX_API_KEY 后重搜")
    records = dedupe(collected)
    corpus = LiteratureCorpus(
        corpus_id=f"corpus_{hashlib.sha256('|'.join(bare).encode()).hexdigest()[:8]}",
        queries=bare,
        camps=camps,
        hits_by_query=hits_by_query,
        records=records,
        hits_by_source=hits,
        abstract_coverage=_ratio(records, lambda r: bool(r.abstract)),
        citation_coverage=_ratio(records, lambda r: r.citation_count > 0),
        relaxed=relaxed_any,
        notes=notes,
    )
    _diagnose(corpus, failed)
    return corpus


def _ratio(records: list[PaperRecord], pred) -> float:
    return round(sum(1 for r in records if pred(r)) / len(records), 4) if records else 0.0


#: 检索式里的功能词。命中它们不算主题相关，否则「什么都相关」。
_STOPWORDS = frozenset("""
a an the and or of in on for to with by from as at is are be been was were
this that these those it its their his her our your not no than then so such
we they he she you i but if into over under about between among during
study studies research paper article analysis effect effects impact role
""".split()) | frozenset("""
研究 分析 影响 问题 基于 探讨 关系 视角 路径 机制 因素 效应 对策 实证 案例
综述 思考 启示 比较 评价 构建 模式 发展 实践 理论 意义 作用 方法 现状 趋势
""".split())
#: 中文那半跟英文那半是同一类词：`study` / `analysis` / `effect` 的中文对应。中文标题里
#: 「研究」「分析」「影响」几乎无处不在，留着就是「什么都相关」——`人口研究综述` 会命中
#: `生育 研究` 的一半。**这三十个词是拍出来的，不是跑出来的**，要按真语料补（见计划停车场）。


def _pages(first, last) -> str:
    """起止页 → `12–34`；只有起页就只给起页。"""
    a, b = (str(first or "").strip(), str(last or "").strip())
    if a and b and a != b:
        return f"{a}–{b}"
    return a or b


#: 整串都是汉字的词。`_content_words` 只按非汉字切分，所以一段连着的汉字整个是一个「词」。
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


def _content_words(text: str) -> set[str]:
    """切成实词。**两字的汉字词也算实词，两字的西文词不算。**

    `len(w) > 2` 这条门槛是给西文定的：三个字母以下的英文词多半是虚词（`the`、`and`、`of`）。
    中文不一样——最常见的实词恰好就是两个字：教育、生育、户籍、收入、房价、迁移。一刀切
    下去，`教育 生育` 这种最常见的中文检索式一个词都不剩，于是 `topical_relevance` 报
    0.00 的假警报、`rank()` 一条都不给，而**没有任何一步会报错**。
    单字仍然挡：单个汉字里虚词太多（的、与、和），放进来会什么都命中。
    """
    return {
        w for w in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", (text or "").lower())
        if (len(w) > 2 or (len(w) == 2 and _CJK_RUN.fullmatch(w))) and w not in _STOPWORDS
    }


def _cjk_hits(text: str, terms: set[str]) -> set[str]:
    """中文检索词按**子串**命中：中文不分词，「农民工」在「农民工市民化研究」里是子串不是整词。

    西文靠空格断词，`_content_words` 切出来的就是一个个词，整词比对成立。中文没有空格，
    切出来的是一整段连写的汉字——「农民工市民化的路径选择」是**一个**词，检索词「农民工」
    跟它整词比对永远不等。于是导入的中文文献与 `--zh` 历史层的记录，在 `rank()` 里一个词
    都沾不上（排不进精读候选），在 `topical_relevance` 里相关度是 0（被报成全部跑题）。

    只对**纯汉字**的检索词放宽（`fullmatch`）：西文词照旧整词比对，
    否则 `art` 会在 `article` 里命中，泛词把排序冲垮。

    **子串会过匹配**，这是认下的取舍：「民工」命中「农民工」、「市民」命中「市民化」，
    多沾一点总好过一条都不沾——中文没有分词器就没有第三条路，而漏掉整批中文记录的代价
    （精读候选里一条中文都没有）比多收几条大得多。
    """
    t = text or ""
    return {w for w in terms if _CJK_RUN.fullmatch(w) and w in t}


def topical_relevance(
    records: list, queries: list[str], *, examples: int = 5
) -> tuple[float, int, list[str]]:
    """语料**跟检索式说的是不是同一件事**。

    **覆盖率和相关性是两回事。**`abstract_coverage` 说的是「有多少条带摘要」，
    它可以是 100%，而这 100% 全都跑题。第一次社会学试跑返回 60 篇，
    大半是流行病学——因为 `cohort` / `attainment` 在那边也是高频词。

    跑题的语料会**一路污染下游**：空白地图建在无关文献上，
    生成的 idea 去填一个不存在的空白，而**没有任何一步会报错**。

    判据取每条记录与「任一检索式」的最大实词命中比例，再取中位数。
    取最大而不是平均，是因为多条检索式本来就各自覆盖不同侧面。

    **没有摘要的记录，零重合不算跑题。**只有一个标题的话文本太少，
    判不出来——GSS 真语料里 `Testing MMI and EMI in the Czech...` 就是这样：
    它高度相关，但标题不含检索词、又没摘要。
    **「判不了」和「跑题」是两回事**，混在一起会让这个指标自己变成噪声。

    **按概念块算，不按整串检索式的实词比例算。**`(A OR B) AND (C OR D)` 写法下，一篇完全
    对题的论文只会命中每块里的一个同义词——按整串算它只有 0.33，健康的语料会被报成跑题
    （基线跑真的打出过「主题相关度中位数只有 0.33」）。所以：一块里**任一**同义词的实词
    全都出现，这一块就算命中；得分是命中块数 / 总块数。对不带引号短语、不重复的裸词袋，
    每个词自成一块，算出来跟原来的实词命中比例一样，GSS 那个校准点（0.4 / 25%）仍然成立。

    返回 `(相关度中位数, 跑题条数, 跑题标题举例)`。
    """
    # 每条检索式 → [[同义词的实词集, …], …]；实词为空的块（纯停用词）跳过，判不了
    per_query: list[list[list[set[str]]]] = []
    for q in queries:
        blocks: list[list[set[str]]] = []
        for b in parse_blocks(q):
            terms = [ws for t in b if (ws := _content_words(t))]
            if terms:
                blocks.append(terms)
        if blocks:
            per_query.append(blocks)
    if not records or not per_query:
        return 0.0, 0, []

    scores: list[float] = []
    off: list[str] = []
    for r in records:
        hay_text = f"{r.title} {getattr(r, 'abstract', '') or ''}"
        hay_words = _content_words(hay_text)
        # 一个同义词命中 = 它的实词**全都**在记录里。西文实词按整词比，纯汉字的实词按子串比
        # （见 `_cjk_hits`：中文不分词，整词比对永远不等）。
        def _hit(ws: set[str]) -> bool:
            return all((w in hay_words) or (_CJK_RUN.fullmatch(w) and w in hay_text) for w in ws)

        best = max(
            sum(1 for b in blocks if any(_hit(ws) for ws in b)) / len(blocks)
            for blocks in per_query
        )
        scores.append(best)
        if best == 0.0 and (getattr(r, "abstract", "") or ""):
            off.append(r.title)
    scores.sort()
    mid = scores[len(scores) // 2] if len(scores) % 2 else (
        (scores[len(scores) // 2 - 1] + scores[len(scores) // 2]) / 2
    )
    return round(mid, 4), len(off), off[:examples]


def _diagnose(corpus: LiteratureCorpus, failed: dict[str, str]) -> None:
    """把「语料够不够用」翻译成后果，而不是只报数字。

    「摘要覆盖 29%」对使用者没有信息量；
    「摘要覆盖 29%，所以挖不出方法缺陷类空白」才有。
    """
    # 按 reason 聚合：`openalex` 与 `openalex_zh` 是同一个源、同一把密钥，密钥一坏两个名字
    # 都会登记，同一句话报两遍只是把 notes 撑长。但**名字要列全**——只留第一个的话，
    # 看到「源 openalex 不可用」的人以为中文历史层还跑着，其实那半也没打出去。
    by_reason: dict[str, list[str]] = {}
    for name, reason in failed.items():
        by_reason.setdefault(reason, []).append(name)
    for reason, names in by_reason.items():
        corpus.notes.append(
            f"⚠ 源 {'、'.join(names)} 不可用：{reason}。"
            f"**源不可用不是「没人做过」的证据**——本次覆盖是残缺的"
        )

    if corpus.relaxed:
        corpus.notes.append(
            "检索式命中不足时已自动降档（哪条、降了几档见上面那条）。"
            "**降档前的 0 命中不构成空白证据**——OpenAlex 要求所有词都命中，"
            "引号短语取交、八个词的裸词袋都会把它卡死"
        )

    for name, n in corpus.hits_by_source.items():
        if n or name in failed:
            continue
        if name == "openalex_zh":
            # 历史层 0 命中是**覆盖薄**的常态，不是检索式坏了：白名单只有二十几本刊，
            # 而且它们在 OpenAlex 里大多断在 2018 年前。套用「检索式可能是坏的」
            # 会让人去改一条本来没问题的中文检索式。
            corpus.notes.append(
                "⚠ 中文历史层 0 命中——白名单覆盖薄（2018 年前），"
                "既不是检索式坏的证据，也不是空白证据；"
                "2018 年后的中文文献靠 oss lit import 从知网导出文件导入")
        else:
            corpus.notes.append(f"⚠ 源 {name} 返回 0 条但服务正常——检索式在这个源里可能是坏的")

    if corpus.camps:
        by_camp: dict[str, int] = {}
        for q, c in corpus.camps.items():
            by_camp[c] = by_camp.get(c, 0) + corpus.hits_by_query.get(q, 0)
        empty = [c for c, n in by_camp.items() if n == 0]
        corpus.notes.append("按阵营命中：" + "、".join(f"「{c}」{n} 条" for c, n in by_camp.items())
                            + (f"。⚠ {'、'.join('「' + c + '」' for c in empty)}一条都没有——"
                               f"是检索式在这个阵营坏了，还是真空白？**先换词重搜再下结论**" if empty else ""))
        if len(by_camp) < 2:
            corpus.notes.append("⚠ 只有一个阵营的检索式——理论对话要有对立面，至少两派各一条")
    if not corpus.records:
        corpus.notes.append("语料为空，下游任何「这是空白」的判断都无效")
        return

    rel, off_n, off_titles = topical_relevance(corpus.records, corpus.queries)
    corpus.topical_relevance = rel
    corpus.off_topic_count = off_n
    share = off_n / max(len(corpus.records), 1)
    # 阈值**只有一个真实校准点**：GSS 那次 194 条、人工判断约 86% 相关，
    # 算出来中位数 0.75、跑题 3%（p10=0.5, p25=0.75）。
    # 所以 0.4 与 25% 是「明显不对劲」的线，不是「刚好合格」的线。
    # 跟 GRAY_BANDS 一样，多跑几次真数据再收紧。
    if rel < 0.4 or share > 0.25:
        # 相关度低不等于有「零重合」的：概念块写法下常常是每条只沾到一块。
        # 一条都没有时原来的文案会打出「举例：。」这种空句子
        shown = "；".join(t[:60] for t in off_titles)
        n = len(corpus.records)
        corpus.notes.append(
            f"⚠ 主题相关度中位数只有 {rel:.2f}"
            + (f"，{off_n}/{n} 条（{share:.0%}）与检索式**零主题重合**。举例：{shown}。"
               if off_titles else "——多数文献只沾到检索式的一部分概念。")
            + f"**覆盖率高不等于相关**——跑题的语料会让空白地图建在无关文献上，"
              f"由它生成的 idea 会去填一个不存在的空白，而没有任何一步会报错。"
              f"先改检索式再往下走"
        )
    # 摘要警告只看摘要覆盖。它原先嵌在上面那个 if 里：覆盖 29% 但相关度正常不报，
    # 相关度差但覆盖 90% 反而报「只有 90%」。阈值 0.6：OpenAlex 撑起的语料实测 77%，
    # Crossref 独撑 29%，六次真跑落在 45%–60%——那几次确实是残缺的。
    if corpus.abstract_coverage < 0.6:
        corpus.notes.append(
            f"⚠ 只有 {corpus.abstract_coverage:.0%} 的文献带摘要。"
            f"作者自陈的局限只能从摘要里抽——**「方法缺陷」这类空白因此检测不到**"
        )
    if corpus.citation_coverage == 0:
        corpus.notes.append("⚠ 语料没有引文数据，捕获再捕获的召回率探针无法执行")


# ------------------------------------------------------- 这是不是一篇研究论文

#: 参考文献段的起头。**不要行尾锚点**——`参考文献（Reference）` 这种后面带括号的
#: 一锚就漏，2026-09-25 校准时六篇正例被这一个锚点拒掉两篇。
_REF_HEAD = re.compile(r"参\s*考\s*文\s*献|引\s*用\s*文\s*献|\bReferences?\b", re.I)

#: 一条文献长什么样：够长、且带年份。中英文条目都满足。
#:
#: **全角数字必须认。** 中文期刊的 PDF 大量用全角排版，条目长这样：
#: `［１］　陈秉正．网络互助［Ｊ］．中国保险，２０２０，（８）．`
#: 只认半角的话，这类 PDF 一篇都过不了——2026-09-25 真跑时三篇真论文被误拒，
#: 两篇栽在这里。`\d` 在 Python 里认全角数字，但 `(19|20)` 这两个字面量不认，
#: 所以要把全角的一二九零也写进去。
_REF_YEAR = re.compile(r"(19|20|１９|２０)[\d０-９]{2}")

#: 文献条目数的下限。**这个数字是量出来的，不是拍的**（2026-09-25 校准集）：
#: 能抽出正文的中文研究论文是 28 / 30 / 31 / 43 / 73 / 98 条，
#: 通讯、名录页、法条是 **0 / 0 / 0 / 0**——分离干净，阈值落在 1 到 28 之间哪儿都行。
#:
#: 取 5 而不是取中间，因为**两类错误的代价不对称**：放进一条通讯，后面还有
#: `lit screen` 那一关；拒掉一篇真论文，它就彻底没了。所以往宽了放。
_MIN_REFS = 5

#: 像一条引用的片段。**这一路是给脚注体例的刊留的。**
#:
#: 第一版只认「参考文献」标题，真跑时被一篇《山东大学学报》拒掉——
#: 它整篇没有「参考文献」四个字，引用全在脚注里：
#: `⑤张海波：《中国转型期公共危机治理》，北京：社会科学文献出版社，2012 年，第66 页。`
#: **《中国社会科学》这一系的人文社科刊大多如此。**
#: 「研究论文必然带参考文献列表」这句话，对中国社科的一整类刊是错的。
_CITE_ZH = re.compile(r"《[^》]{2,80}》")
_CITE_EN = re.compile(r"[A-Z][a-z]+,?\s?[A-Z]?\.?.{0,30}\(?(19|20)\d{2}[a-z]?\)?")

#: 引用形状的条数下限。同一次校准：通讯与名录页 3 / 8 / 12 / 14，
#: 而靠这一路才过关的研究论文是 45 / 54 / 61 / 85 / 103 / 115 / 157 / 274。
#: **缝很宽（14 到 45），取 20。**
_MIN_CITES = 20

#: 私用区字符（U+E000–U+F8FF）的占比上限。**这是「抽文坏没坏」的判据，不是「是不是论文」的。**
#:
#: PDF 用嵌入字体时，抽出来的可能是私用区码位而不是真字符。校准集里
#: **只有一个文件带私用区字符**（一篇双栏综述，3792 个、占 7.34%），其余全是 0。
#: 那一篇的年份被抽成 `2 0\n1 2`、字符里插满 ``——**它不是不像论文，是读不出来**。
#:
#: 读不出来的一律判「判不了」放行。靠调阈值去硬判一份读不出来的文档，
#: 正是本项目反复栽的那种跟头。
_PUA = re.compile(r"[-]")
_MAX_PUA_SHARE = 0.02

#: 中文标点的密度下限。**这是 OCR 坏没坏的判据**，跟私用区那条同类，都属于「判不判得了」。
#:
#: 扫描件 OCR 出来的中文，标点常常整片丢失或被认成 ASCII：
#: 一篇《社会科学》的真论文抽出来是 `改革开放以来的中国社会转型/型塑0 了` ——
#: 书名号成了 `/` 和 `0`，英文 `social trust` 成了 `soeial tru st`。
#:
#: 校准集里那一篇 **7515 个中文字、0 个中文标点**；其余 27 份全在 0.026–0.133 之间。
#: 中文文章不可能没有中文标点，所以这个数接近 0 就是抽坏了，不是「不像论文」。
_ZH_PUNCT = re.compile(r"[，。、；：？！《》「」（）]")
_MIN_ZH_PUNCT_RATE = 0.005
_ZH_ENOUGH = 500                  # 中文字少于这个数就不查这一条：英文文献本来就没有中文标点

#: 抽出来的正文不足这么多字，就是**判不了**，不是**判它不是**。
#: 扫描件、下载截断、PDF 解析失败都落在这里。
#:
#: 数字同样是量出来的：能判的正例最少 17,685 字，反例 4,535–30,448 字——
#: **正文长度本身区分不了两类**（最长的反例是一篇三万字的媒体长文，0 条参考文献），
#: 所以它只当「够不够判」的门槛，不当判据。
_MIN_TEXT = 2000


@dataclass
class ArticleVerdict:
    """`looks_like_article` 的三态判词。

    **`ok` 与 `undecidable` 不是一回事**，调用方必须分开处置：
    `undecidable` 是「我看不出来」，按本项目的立场一律放行并记警告。
    """

    ok: bool
    undecidable: bool
    n_refs: int
    n_chars: int
    n_cites: int = 0
    reason: str = ""


def looks_like_article(text: str) -> ArticleVerdict:
    """抽出来的正文像不像一篇研究论文。**判据只看算得出的事实。**

    看的是**有没有学术引用**，两条路任过其一即可：

    1. **参考文献列表**——有「参考文献 / References」标题，其后有够多的带年份条目
    2. **引用形状**——满篇 `《……》` 与 `Author (2012)` 这类片段

    第二条是给**脚注体例**的刊留的。第一版只认第一条，真跑时拒掉了一篇
    《山东大学学报》——它整篇没有「参考文献」四个字，引用全在脚注里。
    **「研究论文必然带参考文献列表」这句话，对中国人文社科的一整类刊是错的。**

    **为什么不看标题、不看刊名、不看词表。**本项目在词表判据上栽过两次
    （「说反 1/16 仍是词表」「另出的没见过的写法只拦 23/52」）：
    按「XX 捐赠仪式在 XX 举行」这种写法去认，换一种说法就漏。
    刊名也不行——《中国宗教》既发通讯也发文章。

    **判不了就不判。**正文太短（扫描件、下载截断）、或者抽出来满是私用区码位
    （嵌入字体抽坏了）时返回 `undecidable=True`，由调用方放行。
    会误报的门比没有门更糟。
    """
    n = len(text)
    if n < _MIN_TEXT:
        return ArticleVerdict(
            ok=True, undecidable=True, n_refs=0, n_chars=n,
            reason=f"只抽出 {n} 字（不足 {_MIN_TEXT}）——可能是扫描件、下载截断或解析失败。"
                   f"**判不了不等于判它不是**，照收，但这一篇的读书卡多半做不出来")

    pua = len(_PUA.findall(text))
    if pua / n > _MAX_PUA_SHARE:
        return ArticleVerdict(
            ok=True, undecidable=True, n_refs=0, n_chars=n,
            reason=f"正文里 {pua / n:.1%} 是私用区码位（{pua} 个）——**嵌入字体抽坏了，这份读不出来**。"
                   f"照收，但读书卡做不出来；要它的话把 PDF 另存或换个来源重下")

    # 中文正文却几乎没有中文标点 = 扫描件 OCR 坏了。**同样是「判不了」，不是「不是论文」。**
    n_cjk = sum(len(m) for m in _CJK_RUN.findall(text))
    if n_cjk >= _ZH_ENOUGH:
        rate = len(_ZH_PUNCT.findall(text)) / n_cjk
        if rate < _MIN_ZH_PUNCT_RATE:
            return ArticleVerdict(
                ok=True, undecidable=True, n_refs=0, n_chars=n,
                reason=f"{n_cjk} 个中文字里几乎没有中文标点（{rate:.3f}）——"
                       f"**多半是扫描件 OCR 坏了，这份读不出来**。照收，但读书卡做不出来；"
                       f"要它的话换个来源重下")

    best = 0
    for m in _REF_HEAD.finditer(text):
        lines = [ln.strip() for ln in text[m.end():].split("\n") if len(ln.strip()) > 15]
        best = max(best, sum(1 for ln in lines[:250] if _REF_YEAR.search(ln)))

    # 引用形状按**去掉全部空白**的正文数——双栏 PDF 抽出来会在字符间插空白，
    # 不去掉的话 `《中国转型期公共危机治理》` 会被拆得认不出来。
    flat = re.sub(r"[\s　]+", "", text)
    cites = len(_CITE_ZH.findall(flat)) + len(_CITE_EN.findall(text))

    if best >= _MIN_REFS or cites >= _MIN_CITES:
        return ArticleVerdict(ok=True, undecidable=False, n_refs=best, n_chars=n, n_cites=cites)
    return ArticleVerdict(
        ok=False, undecidable=False, n_refs=best, n_chars=n, n_cites=cites,
        reason=f"正文 {n} 字里几乎没有学术引用"
               f"（参考文献条目 {best}，要 {_MIN_REFS}；引用片段 {cites}，要 {_MIN_CITES}）——"
               f"活动通讯、院系动态、名录页、法规原文都是这个样子")


# ------------------------------------------------------------------ 筛选


VERDICTS = ("纳入", "排除")


def apply_screening(corpus: LiteratureCorpus, screening: Screening) -> tuple[list[str], list[str]]:
    """把模型的逐篇判定盖到语料上。返回 `(problems, warnings)`；**有 problems 时一个字段都不动**。

    工具的相关度判据只有「沾没沾词」，判不了「是不是同一件事」——那是判断，归模型。
    这里只核三件机械的事：每篇都判过、判定只有两种、排除的写了理由。
    增量：已判过的不用重判，新文件里出现的以新的为准。改口（判定翻面）与
    「这一轮判的一篇都没排除」都出警告——增量之后这两件事最容易悄悄发生。
    """
    problems: list[str] = []
    warnings: list[str] = []
    by_id = {r.paper_id: r for r in corpus.records}
    seen: set[str] = set()
    for v in screening.verdicts:
        if v.paper_id not in by_id:
            problems.append(f"{v.paper_id} 不在语料里")
            continue
        if v.paper_id in seen:
            problems.append(f"{v.paper_id} 判了两次")
            continue
        seen.add(v.paper_id)
        if v.verdict not in VERDICTS:
            problems.append(f"{v.paper_id} 的判定「{v.verdict}」不是 纳入/排除")
        elif v.verdict == "排除" and not v.reason.strip():
            problems.append(f"{v.paper_id} 排除了但没写理由")
    # 增量：已经判过的记录（滚雪球之前那批）不在新文件里不算漏；只有 `screen` 为空的才必须判。
    # 新文件里出现已判过的 id 是允许的——那是改口，以新的为准。
    missing = [pid for pid, r in by_id.items() if pid not in seen and not r.screen]
    if missing:
        problems.append(f"{len(missing)} 篇没判：{'、'.join(missing[:5])}{'…' if len(missing) > 5 else ''}")
    if problems:
        return problems, warnings

    # 改口要在盖之前算：盖完就看不出原判是什么了。**只有判定翻面才算改口**——
    # 同一个判定换个理由是补写理由，不是翻案。翻案本身合法（`oss lit screen` 就是增量的），
    # 但它悄悄发生就等于没发生：上一轮排除掉的这一轮又纳入了，只有这一条警告说得出来。
    flipped = [f"{v.paper_id}（{by_id[v.paper_id].screen}→{v.verdict}）"
               for v in screening.verdicts
               if by_id[v.paper_id].screen and by_id[v.paper_id].screen != v.verdict]
    for v in screening.verdicts:
        r = by_id[v.paper_id]
        r.screen, r.screen_reason = v.verdict, v.reason.strip()
    if flipped:
        warnings.append(f"改口 {len(flipped)} 篇：{'、'.join(flipped[:5])}"
                        f"{'…' if len(flipped) > 5 else ''}——以新判定为准")
    n_ex = sum(1 for r in corpus.records if r.screen == "排除")
    corpus.screened, corpus.n_excluded = True, n_ex
    if not corpus.records:
        # 空语料上「一篇都没排除」是假警报——没有可排除的东西。
        # （verdicts 也必然是空的：不然上面已经报「不在语料里」了）
        return problems, ["语料是空的，没什么可筛"]
    n = len(corpus.records)
    if n_ex == 0:
        warnings.append("一篇都没排除——语料里通常有沾词的无关文献，全纳入多半是没认真筛")
    elif screening.verdicts and not any(v.verdict == "排除" for v in screening.verdicts):
        # 增量之后「一篇都没排除」按全语料算就不响了：上一轮排的还挂在那儿，`n_ex` 不为零。
        # 可这一轮判的那批照样可能是全纳入——滚雪球拉进来的尤其混。所以按**这一轮**再问一遍。
        warnings.append(f"这轮判的 {len(seen)} 篇一篇都没排除——"
                        f"滚雪球拉进来的通常混着不相关的，全纳入多半是没认真筛")
    if n_ex / n > 0.7:  # 跟上面两条是两件事，各响各的
        warnings.append(f"排除了 {n_ex / n:.0%}——检索式多半跑题，剩下的 {n - n_ex} 篇撑不起空白地图，回去改检索式重搜")
    return problems, warnings


# ------------------------------------------------------------------ 滚雪球


def _work_id(rec: PaperRecord, oa: OpenAlexSource) -> str:
    """记录的 OpenAlex W 号：自带就用；否则按 DOI 去 OpenAlex 换一个。换不到返回空串。

    Crossref 兜底、`oss lit add` 进来的记录 paper_id 是 DOI，拉引文要的是 W 号。
    """
    pid = (rec.paper_id or "").strip()
    if re.fullmatch(r"W\d+", pid):
        return pid
    if rec.doi:
        got = oa.by_doi(rec.doi)
        if got is not None and re.fullmatch(r"W\d+", got.paper_id or ""):
            return got.paper_id
    return ""


def _match_keys(rec: PaperRecord) -> list[str]:
    """一条记录的身份键：W 号 / DOI / 标题。任一撞上就当同一篇。

    三个键分别加前缀，免得某条的标题恰好长得像另一条的编号而误判成同一篇。
    DOI 大小写不敏感，标题按小写去空白比。
    """
    keys = []
    if (i := (rec.paper_id or "").strip()):
        keys.append(f"id::{i}")
    if (d := (rec.doi or "").strip().lower()):
        keys.append(f"doi::{d}")
    if (t := (rec.title or "").lower().strip()):
        keys.append(f"title::{t}")
    return keys


def snowball(
    corpus: LiteratureCorpus,
    seeds: list[PaperRecord],
    *,
    backward: int = 50,
    forward: int = 15,
    client: httpx.Client | None = None,
    cache: Cache | None = None,
) -> SnowballStats:
    """从种子往后拉参考文献、往前拉引用，扩进语料。返回**这一轮**的账；语料上的 `snowball` 是累计。

    第一期实测：把 `--limit` 从 20 提到 50，记录数翻 2.5 倍，召回几乎不动——必找集里的文献
    靠主题检索就够不着。真人做综述靠的就是引文链：找到几篇种子，往后看它引了谁（经典在这），
    往前看谁引了它（最有分量的后续工作在这；真正最新的争论被引还少，这一档拿不到）。
    OpenAlex 两个方向都免费给：`cited_by:W`（W 引用的）、`cites:W`（引用 W 的）。
    每个种子两次调用，某个方向的条数给 0 就不打那一次。

    - 往后不加类型过滤：经典常常是书、章节、工作论文，正是主题检索 `type:article` 挡掉的那批
    - 往前只要 `type:article` 并按被引排，取前 `forward` 篇：引用者动辄上千，取最有分量的
      ——最新的争论要另想办法（停车场）
    - 新记录 `via` 记来路、`camp` 继承种子、`screen` 为空——**滚完要再筛一次**，所以语料 `screened` 翻假
    - 同一篇从两个阵营的种子都能到达时，记先到的那个阵营
    - 已在语料里的（按 W 号、DOI、标题）不重复进，但拿新数据**补齐**它的空缺（`_fill`）：
      Crossref 那条缺的摘要、被引数，正好由 OpenAlex 这条补上；种子自引也不重复进
    - 语料变了就把覆盖率和主题相关度一起重算——滚进来的可能整批跑题，
      相关度不重算，语料会顶着滚之前那个漂亮数字往下走
    - OpenAlex 不可用直接抛 `SourceUnavailable`：滚雪球没有别的源可退
    """
    own = client is None
    client = client or _client()
    cache = cache or Cache()
    oa = OpenAlexSource(client, cache)
    this = SnowballStats()
    known: dict[str, PaperRecord] = {}
    for r in corpus.records:
        for k in _match_keys(r):
            known.setdefault(k, r)
    new: list[PaperRecord] = []
    skipped: list[str] = []
    skipped_import: list[str] = []
    filled = False
    try:
        for seed in seeds:
            # 导入的记录没有 OpenAlex 编号，多数连 DOI 都没有——`_work_id` 会为它按 DOI
            # 查一次或按标题搜一次，回来必然是空。它们从来就滚不了，不该出发：
            # 每个种子白花一次额度，换回一条「换不到 W 号」的警告，把真正该查的种子挤掉。
            if seed.via == "import":
                skipped_import.append(seed.paper_id or seed.title[:40])
                continue
            wid = _work_id(seed, oa)
            if not wid:
                skipped.append(seed.paper_id or seed.title[:40])
                continue
            this.seeds.append(wid)
            plan = (("backward", f"cited_by:{wid}", backward),
                    ("forward", f"cites:{wid},type:article", forward))
            for direction, flt, n in plan:
                if n <= 0:      # 这个方向关掉了，一次调用都别打
                    continue
                for r in oa.works(flt, per_page=n):
                    hit = next((known[k] for k in _match_keys(r) if k in known), None)
                    if hit is not None:
                        # 同一篇两边各有各的强项：补齐已有那条，不重复进语料
                        filled = _fill(hit, r) or filled
                        continue
                    r.via = f"snowball:{direction}:{wid}"
                    r.camp = seed.camp
                    for k in _match_keys(r):
                        known.setdefault(k, r)
                    new.append(r)
                    setattr(this, direction, getattr(this, direction) + 1)
    finally:
        if own:
            client.close()

    if skipped:
        corpus.notes.append(
            f"滚雪球跳过 {len(skipped)} 篇没有 OpenAlex 编号也没有 DOI 的种子："
            f"{'、'.join(skipped[:5])}{'…' if len(skipped) > 5 else ''}")
    if skipped_import:
        corpus.notes.append(
            f"{len(skipped_import)} 个导入种子没有 OpenAlex 编号，未滚（滚雪球对导入的记录无效）："
            f"{'、'.join(skipped_import[:5])}{'…' if len(skipped_import) > 5 else ''}")
    this.added = len(new)
    # 一个种子都没换到 W 号：一次引文调用都没打，这不算滚过一轮
    this.rounds = 1 if this.seeds else 0
    corpus.records.extend(new)
    total = corpus.snowball
    corpus.snowball = SnowballStats(
        seeds=[*total.seeds, *this.seeds], added=total.added + this.added,
        backward=total.backward + this.backward, forward=total.forward + this.forward,
        rounds=total.rounds + this.rounds,
    )
    if new:
        # 新来的没筛过：语料整体回到「未筛」，`oss lit fetch --top` 会提醒，`apply_screening` 只要求判新增的
        corpus.screened = False
    if new or filled:
        corpus.abstract_coverage = _ratio(corpus.records, lambda r: bool(r.abstract))
        corpus.citation_coverage = _ratio(corpus.records, lambda r: r.citation_count > 0)
        corpus.topical_relevance, corpus.off_topic_count, _ = topical_relevance(
            corpus.records, corpus.queries)
    return this
