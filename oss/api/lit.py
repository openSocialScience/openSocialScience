"""`oss lit search / add / import / snowball / screen / fetch / cards / verify`：检索、补录、
导入中文题录、滚雪球扩语料、筛选、精读、校验读书卡、核转述。

**`search` / `add` / `snowball` / `fetch` 四条是链条上唯一会打网络的文献侧命令**（OpenAlex / Semantic Scholar / Crossref 兜底与按 DOI 解析 / OpenAlex 引文链 / 开放获取全文），
所以它们的测试一条都不真连：网络那一层由 `lit_module` / `reading_mod` 两个名字
进来，测试在这个模块的命名空间上换掉它们。换句话说，**这里 import 的名字就是接缝**——
`from oss.core import literature as lit_module` 而不是 `from oss.core.literature import search`。

八条命令都走 `require_run`（`import` 只读本地文件，不打网络）。搬家之前它们走的是「不存在就建」，改这一条是有意的：
环节 2 跑在一个**已经画过像**的运行目录上（SKILL.md 第二步就 `mkdir -p`，
环节 1 的 `oss profile` 又往里写了 profile.json），所以目录不存在只有一种解释——
路径打错了。而顺手建一个空目录出来的后果很难查：语料落进 `runs/currnet/`，
下一条 `oss lit fetch --run runs/current` 说「缺 literature.json——先跑 oss lit search」，
把「路径错了」说成「这一步没做」。

`verify` 是最后一条从「不存在就建」上挪下来的：它只读 paper.md 和 literature.json，
两个都不该由它自己造，所以打错路径要拒绝。

`fetch` 里那条「语料里没有这几个 id」的警告**跟着拒绝一起走**（`data["warnings"]`）：
它原先打在 ✗ 之前，位置不许挪。`cards` 的警告同理，而且它原先打在 **stdout**，
不是 stderr——门面照旧。
"""

from __future__ import annotations

import hashlib
import html as html_mod
import json
import re
from pathlib import Path

from oss.api.errors import BadInput, Missing, Refused, Unavailable
from oss.api.result import result
from oss.api.rundir import emit, require_run
from oss.core import importers
from oss.core import literature as lit_module
from oss.core import manuscript
from oss.core import reading as reading_mod
from oss.core.contracts import ARTIFACTS, LiteratureCorpus, PaperRecord, ReadingCards, Screening, TraceEvent


def search(query: list[str] | None = None, *, run: str, limit: int = 20,
           year_from: int | None = None, zh: list[str] | None = None) -> dict:
    """检索文献。OpenAlex（要一把免费密钥）+ Semantic Scholar；Crossref 只在 OpenAlex 不可用时兜底。

    阵营（`camps`）与提醒（`notes`）**原样交出去**：哪个理论阵营 0 命中，
    是环节 2 的完成条件之一，判断归读的人，工具只负责把数摆出来。

    `zh` 是中文历史层的检索式：只查 OpenAlex 里的中文核心刊白名单（`--zh`）。
    **`query` 与 `zh` 至少给一条，给哪边都行**：只做中文综述是正当的做法，逼人先编一条
    用不上的英文检索式，换来的只是一条乱写的式子。两边都空则拒绝——一条检索式都没有就
    没有「检索」这回事，打出去只会换回全库前 N 篇。
    """
    if not query and not zh:
        raise BadInput("query 与 --zh 至少给一条", code=1)
    query = list(query or [])
    rundir = require_run(run)
    # 换词重搜会整份覆盖 literature.json。检索源能再拉一遍，导入的记录不能——它们是用户
    # 在知网点导出、手工搬进来的，没有任何检索源能把它们找回来，连筛选判定一起冲掉就是把
    # 人的活儿丢了。所以重搜前先把 `via="import"` 的挑出来，搜完再并回去。
    lit_path = rundir.file(ARTIFACTS["literature"])
    kept = [r for r in LiteratureCorpus.load(lit_path).records if r.via == "import"] if lit_path.exists() else []
    corpus = lit_module.search(list(query), limit=limit, year_from=year_from, zh=list(zh or []))
    if kept:  # 新语料里已经有的（同 DOI 或同标题）就不留了，留下就是重复
        fresh = {r.doi.lower() for r in corpus.records if r.doi} | {r.title.lower().strip() for r in corpus.records}
        kept = [r for r in kept
                if not ((r.doi and r.doi.lower() in fresh) or r.title.lower().strip() in fresh)]
    if kept:
        corpus.records.extend(kept)
        corpus.abstract_coverage = lit_module._ratio(corpus.records, lambda x: bool(x.abstract))
        corpus.citation_coverage = lit_module._ratio(corpus.records, lambda x: x.citation_count > 0)
        # 保留的导入记录带着自己的筛选判定进来，`n_excluded` 却是新语料的（0）——落盘的语料
        # 会说「一篇没排除」，而 `oss lit screen` 的增量判定按记录上的 `screen` 走，两边对不上。
        corpus.n_excluded = sum(1 for r in corpus.records if r.screen == "排除")
        corpus.notes.append(f"重搜保留了 {len(kept)} 条导入记录（不来自任何检索源）及其筛选判定")
    note = (f"{len(corpus.records)} 条，摘要覆盖 {corpus.abstract_coverage:.0%}，"
            f"引文覆盖 {corpus.citation_coverage:.0%}")
    # 留痕的命令串要能照着复跑。只给 `--zh` 时按 `{' '.join(query)!r}` 拼出来的是
    # `oss lit search ''`——一条空检索式，看留痕的人既复不出这一步，也看不出中文那半跑过。
    cmd = ("oss lit search"
           + "".join(f" {q!r}" for q in query)
           + "".join(f" --zh {z!r}" for z in (zh or [])))
    path = emit(
        rundir, "literature", cmd, "literature", corpus,
        note=note,
        args={"queries": list(query), "zh": list(zh or []), "hits_by_source": corpus.hits_by_source},
    )
    return result(note, {
        "run": str(rundir.path), "artifact": ARTIFACTS["literature"], "path": str(path),
        "queries": list(query),
        "zh": list(zh or []),
        "n_records": len(corpus.records),
        "hits_by_source": dict(corpus.hits_by_source),
        "hits_by_query": dict(corpus.hits_by_query),
        "abstract_coverage": corpus.abstract_coverage,
        "citation_coverage": corpus.citation_coverage,
        "camps": dict(corpus.camps),
        "notes": list(corpus.notes),
    })


def add(doi: str, *, run: str) -> dict:
    """按 DOI 把一篇文献补进语料。

    **为什么需要这条命令**：规则「引用必须来自语料」有个副作用——
    检索没召回的经典（比如一本 1967 年的书）会逼人要么不引、要么造假。
    这条命令给出正路，而且它是**留痕的、可核的**，
    不是在正文里偷偷写一个名字。

    抓不到是 `Unavailable`，查不到是 `Missing`：**源坏了不等于这篇文献不存在**，
    两者混起来会教模型把「抓不到」当成「没有」。
    """
    rundir = require_run(run)
    path = rundir.file(ARTIFACTS["literature"])
    if not path.exists():
        raise Missing("literature", "还没有语料——先跑 oss lit search", code=1)
    corpus = LiteratureCorpus.load(path)
    if any((r.doi or "").lower() == doi.strip().lower() for r in corpus.records):
        return result(f"语料里已经有 {doi} 了",
                      {"run": str(rundir.path), "doi": doi, "already": True})
    try:
        rec = lit_module.fetch_by_doi(doi)
    except lit_module.SourceUnavailable as exc:
        raise Unavailable(f"抓不到，{exc.source} 不可用：{exc.reason}", code=1) from exc
    if rec is None:
        raise Missing("record", f"按 DOI {doi} 没查到文献——核对一下 DOI 是否正确", code=1)
    if corpus.screened:
        # 筛过的语料里补一篇进来，不标判定它就是空的——既不算纳入也不算排除，
        # 而 `rank()` 只跳过「排除」，于是它照样进候选，`oss lit screen` 却再也不会问起它。
        # 补它进来这个动作本身就是「要它」，所以标纳入，理由写清是谁标的。
        rec.screen, rec.screen_reason = "纳入", "oss lit add 补入"
    # 来路记在记录上：检索命中的是空，滚雪球进来的写 `snowball:方向:种子`，
    # 手工补入的写 `add`——语料里一篇文献是怎么来的，事后只有这一个字段说得清。
    rec.via = "add"
    corpus.records.append(rec)
    corpus.save(path)
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="literature", command=f"oss lit add {doi}",
        outcome="ok", artifacts=[path.name],
        note=f"补入 {rec.paper_id}：{(rec.title or '')[:50]}",
        args={"doi": doi, "paper_id": rec.paper_id}))
    return result(f"补入 {rec.paper_id}  {(rec.year or '?')}  {rec.title[:60]}", {
        "run": str(rundir.path), "path": str(path), "doi": doi, "already": False,
        "paper_id": rec.paper_id, "year": rec.year, "title": rec.title,
        "cite": f"{{{{cite:{rec.paper_id}}}}}",
    })


#: 能当全文收的格式。HTML 是 2026-09-25 加的：中文 CSSCI 刊里有相当一批
#: （《社会》《南方经济》《公共行政评论》）把全文发成网页而不是 PDF，
#: 只认 `.pdf` 就等于把它们全挡在外面。
_DOC_SUFFIXES = (".pdf", ".htm", ".html")

#: 小于这个字节数的，**根本不是一份文献**，是抓取失败落下的残骸——
#: HTTP 403/404 的错误页（实测 162–2516 字节）、断在开头的下载。
#:
#: **这一条跟「判不了」要分开。**扫描件也抽不出字，但它是**真文件**（几百 KB 到几 MB），
#: 那种放行；几百字节的错误页放行就是往语料里塞垃圾。
#: 判据是「文件大小 + 抽出多少字」两个都算得出来的事实：
#: 真文章实测 HTML 17–124 KB、PDF 233–797 KB，错误页 ≤2.5 KB，中间空得很宽。
_MIN_DOC_BYTES = 8192

#: 网页里不该进正文的块。抽文只求够判、够做读书卡，不求还原排版。
_HTML_DROP = re.compile(r"(?is)<(script|style|nav|footer|header|aside)\b.*?</\1>")
_HTML_TAG = re.compile(r"<[^>]+>")


#: 线性化 PDF 在文件头里自报全长：`<</Linearized 1/L 1052261/O 332/...>>`。
#: 跟实际大小一比就知道下载截没截断——**这比「可能是扫描件、下载截断或解析失败」
#: 三选一有用得多**，因为三者的处置完全不同：截断了就重下，扫描件重下也没用。
_PDF_TOTAL_LEN = re.compile(rb"/Linearized\s+\d+\s*/L\s+(\d+)")


def truncated_pdf(f: Path) -> int:
    """这份 PDF 声称自己该有多大。返回 0 表示没自报，或者已经下全了。

    只看文件头的前 2 KB——线性化字典按规范就在那儿。
    """
    if f.suffix.lower() != ".pdf":
        return 0
    with f.open("rb") as fh:
        head = fh.read(2048)
    m = _PDF_TOTAL_LEN.search(head)
    if not m:
        return 0
    want = int(m.group(1))
    return want if f.stat().st_size < want * 0.98 else 0


def _doc_text(f: Path) -> str:
    """把一份文献抽成纯文本。PDF 走 PyMuPDF，网页走标签剥离。"""
    raw = f.read_bytes()
    if f.suffix.lower() == ".pdf":
        return reading_mod._pdf_text(raw)
    s = raw.decode("utf-8", errors="replace")
    s = _HTML_DROP.sub(" ", s)
    s = _HTML_TAG.sub("\n", s)
    return re.sub(r"\n{2,}", "\n", html_mod.unescape(s))


def _import_documents(src: Path, *, run: str, camp: str = "") -> dict:
    """用户自己下载的全文进语料：**先验是不是研究论文**，再三层退元数据，全文落进精读目录。

    **为什么值得单开一条路**：用户手里的文件是他检索过、筛过、亲手下载的，
    **确定相关**——精读它们比按排名抓前 20 篇划算得多（验收跑里前 20 篇只有 8 篇沾边），
    而且绕开了开放获取拿不到全文那个老问题。

    代价是文件里没有结构化题录，元数据只能从正文找。所以三层往下退，
    跟 `oss data` 查构念同构，也守同一条纪律：**查不到不等于不存在**——
    第三层照样进语料，只标 `[UNVERIFIED]` 交用户确认，绝不因为查不到就把用户的文献丢掉。

    **2026-09-25 加了一道门**（`looks_like_article`）。起因是中文文献只能走
    「skill 网页搜、工具验」这条路——而网页搜索必然会捞回活动通讯、院系动态、
    法规原文。实测 OpenAlex 按「宗教 捐赠」返回的 19 条里，17 条是这种东西。
    门只看**算得出的事实**（有没有参考文献列表），不看标题、不看刊名、不看词表。

    门判不了的（扫描件、下载截断、解析失败）**一律放行并记警告**：
    会误报的门比没有门更糟，而且拒掉一篇真论文它就彻底没了。
    """
    rundir = require_run(run)
    lit_path = rundir.file(ARTIFACTS["literature"])
    if not lit_path.exists():
        raise Missing("literature", "缺 literature.json——先跑 oss lit search", code=1)
    if src.is_dir():
        pdfs = sorted(f for f in src.iterdir() if f.suffix.lower() in _DOC_SUFFIXES)
    else:
        pdfs = [src]
    if not pdfs:
        raise BadInput(f"{src} 里没有 {'、'.join(_DOC_SUFFIXES)}", code=1)

    corpus = LiteratureCorpus.load(lit_path)
    have = {r.doi.lower() for r in corpus.records if r.doi} | {r.title.lower().strip() for r in corpus.records}
    out_dir = rundir.file("reading")
    added, items, skipped = [], [], 0
    tiers = {"doi": 0, "title": 0, "cited": 0, "layout": 0, "unverified": 0}
    rejected: list[str] = []          # 门判「不是研究论文」的，逐条留理由
    undecided: list[str] = []         # 门判不了的：照收，但要说出来
    broken: list[str] = []            # 压根没下下来的：既不是拒收也不是判不了
    for f in pdfs:
        size = f.stat().st_size
        text = _doc_text(f)
        if size < _MIN_DOC_BYTES and len(text) < lit_module._MIN_TEXT:
            # **抓取失败，不是判断问题。**这一类不进语料、也不算被门拒，
            # 报出来让人去重下——混进「拒收」里，人会以为是门判错了。
            broken.append(f"{f.name}：只有 {size} 字节，抽出 {len(text)} 字——"
                          f"多半是 HTTP 错误页或下载断在开头，**重下这一份**")
            continue
        verdict = lit_module.looks_like_article(text)
        if not verdict.ok:
            rejected.append(f"{f.name}：{verdict.reason}")
            continue
        if verdict.undecidable:
            # PDF 自报了全长的话，就别再说「可能是三种情况之一」——**直接说是截断，重下。**
            if want := truncated_pdf(f):
                undecided.append(
                    f"{f.name}：**下载截断了**——文件自报应有 {want:,} 字节，"
                    f"实际只有 {size:,} 字节（{size / want:.0%}）。**重下这一份**，"
                    f"不是扫描件也不是解析失败")
            else:
                undecided.append(f"{f.name}：{verdict.reason}")
        doi = importers.doi_in(text)
        rec, tier = None, "unverified"
        if doi:
            rec = lit_module.fetch_by_doi(doi)          # 第一层：权威题录
            tier = "doi" if rec is not None else tier
        guess = importers.title_guess(text) or f.stem
        if rec is None and not doi:
            rec = lit_module.search_by_title(guess)     # 第二层：标题反查
            tier = "title" if rec is not None else tier
        if rec is None:
            # 第三层：**刊物自己给的机读题录**（中文期刊网页上的「引用本文」块）。
            # 中文核心刊在 OpenAlex / Crossref 里基本查不到，前两层对它们几乎全废；
            # 而网页版正文里往往印着 `作者. 年. 题名[J]. 刊, 卷(期): 页.`。
            # `cited_as` 要求块里的题名跟已抽出的篇名对得上才采用——一页上常有
            # 「上一篇」「下一篇」和参考文献表的题录，随便取一条就会把**别人的**
            # 作者安到这份文献上，而且作者名、年份、刊名全都合法，错得看不出来。
            authors, year = importers.cited_as(text, guess)
            if authors:
                rec = PaperRecord(
                    paper_id="pdf_" + hashlib.sha1(f.name.encode()).hexdigest()[:10],
                    title=guess, authors=authors, year=year or 0)
                tier = "cited"
        if rec is None:
            # 第四层：按版面位置推作者（夹在篇名与「摘要」之间的那几行）。
            # **只推作者、不核篇名**，所以篇名继续带 `[UNVERIFIED]`——
            # 可信度跟第三层不一样，标记也不该一样。
            by_layout = importers.authors_by_layout(text, guess)
            if by_layout:
                rec = PaperRecord(
                    paper_id="pdf_" + hashlib.sha1(f.name.encode()).hexdigest()[:10],
                    title=f"[UNVERIFIED] {guess}", authors=by_layout)
                tier = "layout"
        if rec is None:                                  # 第五层：照样进，标存疑
            rec = PaperRecord(paper_id="pdf_" + hashlib.sha1(f.name.encode()).hexdigest()[:10],
                              title=f"[UNVERIFIED] {guess}")
            tier = "unverified"
        rec.source, rec.via, rec.camp = f"import:pdf:{tier}", "import", camp
        rec.language = importers._language(rec.title)
        keys = {rec.title.lower().strip()} | ({rec.doi.lower()} if rec.doi else set())
        if keys & have:
            skipped += 1
            continue
        have |= keys
        tiers[tier] += 1
        added.append(rec)
        # 全文落盘：用户 PDF 的价值就在这儿，落了才进得了读书卡
        out_dir.mkdir(parents=True, exist_ok=True)
        header = (f"# {rec.title}\n\n{'; '.join(rec.authors)} ({rec.year or 'n.d.'})"
                  f"  DOI: {rec.doi or '—'}\n来源层级: {reading_mod.LEVEL_FULL}\n\n")
        (out_dir / f"{reading_mod.fname(rec.paper_id)}.txt").write_text(header + text, encoding="utf-8")
        items.append(reading_mod.FetchItem(
            paper_id=rec.paper_id, title=rec.title,
            level=reading_mod.LEVEL_FULL if len(text) >= reading_mod._MIN_FULLTEXT_CHARS else reading_mod.LEVEL_META,
            chars=len(text), path=str(out_dir / f"{reading_mod.fname(rec.paper_id)}.txt"),
            reason="" if len(text) >= reading_mod._MIN_FULLTEXT_CHARS
                   else "抽出来的文本太短，可能是扫描件或只有版权页"))
    if items:
        reading_mod.merge_index(out_dir, items)
    corpus.records.extend(added)
    if added:
        corpus.screened = False      # 导入不等于筛过
        corpus.abstract_coverage = lit_module._ratio(corpus.records, lambda x: bool(x.abstract))
        corpus.citation_coverage = lit_module._ratio(corpus.records, lambda x: x.citation_count > 0)
    warnings = []
    # **两层都带 `[UNVERIFIED]`，警告就得把两层都算进来。**版面层只推出了作者，
    # 篇名仍是从正文猜的、没有任何权威源核对过——漏掉它等于让一批标着存疑的记录
    # 在「导入成功」里静默通过。
    unverified = tiers["unverified"] + tiers["layout"]
    if unverified:
        detail = (f"（其中 {tiers['layout']} 篇按版面位置推出了作者，但篇名仍未核）"
                  if tiers["layout"] else "")
        warnings.append(f"{unverified} 篇没查到权威题录，标了 [UNVERIFIED]{detail}——"
                        "元数据是从正文猜的，**引用之前要用户确认，或者补一份题录导出文件**")
    if broken:
        warnings.append(f"{len(broken)} 份根本没下下来（HTTP 错误页或下载断在开头），"
                        f"**这不是判断问题，是抓取问题，重下**。见 data.broken")
    if rejected:
        warnings.append(f"{len(rejected)} 份没收：正文里找不到参考文献列表，"
                        f"不像研究论文。逐条理由见 data.rejected——"
                        f"**要是其中有真论文，那是抽文没抽好，把文件发上来看**")
    if undecided:
        warnings.append(f"{len(undecided)} 份判不了是不是研究论文（正文抽得太少），"
                        f"**照收了**，但它们的读书卡多半做不出来。见 data.undecided")
    if skipped:
        warnings.append(f"跳过语料里已有的 {skipped} 篇")
    corpus.notes.append(
        f"导入全文 {len(pdfs)} 份：{len(added)} 条进语料"
        f"（DOI 反查 {tiers['doi']}、标题反查 {tiers['title']}、刊物自述题录 {tiers['cited']}、"
        f"版面推作者 {tiers['layout']}、存疑 {tiers['unverified']}），"
        f"未通过检测 {len(rejected)}、判不了 {len(undecided)}，跳过已有 {skipped}。"
        f"全文已落进 reading/，可直接写读书卡")
    if undecided:
        # **这一条要进语料的 notes，不只是命令行的警告。**
        # 写论文那一步读的是 notes，抽不出正文的那几篇做不出读书卡，
        # 局限那节得照实说，不能等到审稿人问起来。
        corpus.notes.append(
            f"⚠ 导入的 {len(undecided)} 份正文抽得太少（扫描件、下载截断或解析失败），"
            f"是不是研究论文判不了，已照收——**这几篇的「局限」栏会是空的**")
    corpus.save(lit_path)
    note = (f"导入全文 {len(added)} 篇：DOI 反查 {tiers['doi']}、标题反查 {tiers['title']}、"
            f"刊物自述题录 {tiers['cited']}、版面推作者 {tiers['layout']}、"
            f"存疑 {tiers['unverified']}，拒收 {len(rejected)}、判不了 {len(undecided)}，"
            f"跳过已有 {skipped}")
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="literature",
        command=f"oss lit import {src.name}" + (f" --camp {camp}" if camp else ""),
        outcome="ok", artifacts=[lit_path.name], note=note,
        args={"file": str(src), "format": "doc", "camp": camp, "tiers": tiers,
              "n_rejected": len(rejected), "n_undecided": len(undecided)}))
    # `warnings` 与 `next` 跟题录那条分支同名同位。**两条分支是同一条命令**，
    # 形状不一样，命令行就得分情况取——而它没分，于是 PDF 这条路一用就 KeyError。
    # `problems` 是被拒时的逐条理由，这里没拒任何东西，不该借用。
    return result(note, {
        "run": str(rundir.path), "path": str(lit_path), "format": "doc", "camp": camp,
        "n_added": len(added), "n_skipped": skipped, "n_records": len(corpus.records),
        "tiers": tiers, "n_fulltext": sum(1 for i in items if i.level == reading_mod.LEVEL_FULL),
        "n_rejected": len(rejected), "rejected": rejected,
        "n_undecided": len(undecided), "undecided": undecided,
        "n_broken": len(broken), "broken": broken,
        "warnings": warnings,
        "next": f"oss lit screen --run {run}（只需判新增的 {len(added)} 篇）" if added else "",
    })


def import_file(path: str, *, run: str, camp: str = "") -> dict:
    """把用户自己找好的文献并进语料：题录导出文件，**或者全文**（见 `_import_documents`）。

    给的是目录、`.pdf` 或网页（`.htm`/`.html`）就走全文那条路——
    两条路的返回形状一样，调用方不必分情况取。

    **网页是 2026-09-25 加的。**中文 CSSCI 刊里有相当一批（《社会》《南方经济》
    《公共行政评论》）把全文发成网页而不是 PDF，只认 `.pdf` 等于把它们全挡在外面。

    中文库没有公开 API，用户用自己机构的权限检索、导出，工具吃文件——这是唯一诚实又合法的路。
    导入的记录 `via="import"`、`language` 按标题判、`screen` 为空：**导入不等于筛过**，
    知网一次导出几百条里照样有沾词的无关文献，接着跑 `oss lit screen` 只判新增的。
    没有 OpenAlex 编号：滚雪球对它们无效，也拿不到全文。

    **只收中英文**（用户的决定）：日文、韩文的记录解析得出来但不进语料，跳过几条会说出来
    （`n_foreign` 与一条警告）。挡在这一层而不是解析层，是因为解析器一旦替下游做主，
    下游就再也数不清自己漏了什么。纯汉字写的日文标题分不出来，仍会当中文进来。
    """
    rundir = require_run(run)
    lit_path = rundir.file(ARTIFACTS["literature"])
    if not lit_path.exists():
        raise Missing("literature", "缺 literature.json——先跑 oss lit search", code=1)
    src = Path(path)
    if not src.exists():
        raise Missing("file", f"文件不存在：{path}", code=1)
    if src.is_dir() or src.suffix.lower() in _DOC_SUFFIXES:
        return _import_documents(src, run=run, camp=camp)
    raw = src.read_bytes()
    text = None
    for enc in ("utf-8-sig", "gb18030"):  # utf-8-sig 连带认没有 BOM 的 UTF-8，不必再列一遍
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise BadInput(f"{path} 不是 UTF-8 也不是 GBK 文本", code=1)
    try:
        recs = importers.parse_export(text)
    except importers.UnknownFormat as exc:
        raise BadInput(str(exc), code=1) from exc
    corpus = LiteratureCorpus.load(lit_path)
    have = {r.doi.lower() for r in corpus.records if r.doi} | {r.title.lower().strip() for r in corpus.records}
    # 「文件内重复」与「语料已有」分开数：两种跳过的处置完全不同——前者是用户导出时勾重了
    # （改导出），后者是这条本来就在（该跳）。合成一个数字报出去，用户不知道该动哪一头。
    seen_here: set[str] = set()
    added, skipped, skipped_titles, dup_titles = [], 0, [], []
    foreign = [r for r in recs if r.language not in ("zh", "en")]
    for r in [x for x in recs if x.language in ("zh", "en")]:
        keys = {r.title.lower().strip()} | ({r.doi} if r.doi else set())
        if keys & have:
            skipped += 1
            skipped_titles.append(r.title)
            continue
        if keys & seen_here:
            dup_titles.append(r.title)
            continue
        r.camp = camp
        seen_here |= keys
        added.append(r)
    corpus.records.extend(added)
    if added:
        corpus.screened = False
        corpus.abstract_coverage = lit_module._ratio(corpus.records, lambda x: bool(x.abstract))
        corpus.citation_coverage = lit_module._ratio(corpus.records, lambda x: x.citation_count > 0)
    warnings: list[str] = []
    if added and not camp:
        warnings.append("没给 --camp：这批记录不归任何阵营，滚雪球时也不会把阵营传给后代")
    if skipped:
        warnings.append(f"跳过已有 {skipped} 条：{'、'.join(t[:30] for t in skipped_titles[:5])}"
                        + ("…" if skipped > 5 else ""))
    if dup_titles:
        warnings.append(f"文件内重复 {len(dup_titles)} 条（同一份导出里勾重了）："
                        + "、".join(t[:30] for t in dup_titles[:5])
                        + ("…" if len(dup_titles) > 5 else ""))
    if foreign:
        warnings.append(f"跳过 {len(foreign)} 条日文/韩文记录："
                        + "、".join(r.title[:30] for r in foreign[:3])
                        + ("…" if len(foreign) > 3 else ""))
    fmt = importers.detect_format(text)
    # 中文条数单列：论文阶段按「语料里有没有 `language` 为 zh 的记录」放行新颖性主张，
    # 一份全是英文题录的导出文件解不开那道闸——只报总数看不出来。
    n_zh = sum(1 for r in added if r.language == "zh")
    corpus.notes.append(f"导入 {src.name}（{fmt}）：{len(added)} 条进语料（中文 {n_zh} 条），{skipped} 条已有"
                        + (f"，文件内重复 {len(dup_titles)} 条" if dup_titles else "")
                        + (f"，跳过 {len(foreign)} 条日文/韩文" if foreign else "")
                        + (f"，阵营「{camp}」" if camp else "") + "。导入的记录没有 OpenAlex 编号：滚雪球无效、拿不到全文")
    corpus.save(lit_path)
    note = f"导入 {len(added)} 条（{fmt}），跳过已有 {skipped} 条（{len(corpus.records) - len(added)}→{len(corpus.records)}）"
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="literature", command=f"oss lit import {src.name}" + (f" --camp {camp}" if camp else ""),
        outcome="ok", artifacts=[lit_path.name], note=note,
        args={"file": str(src), "format": fmt, "camp": camp,
              "n_foreign": len(foreign), "n_dup_in_file": len(dup_titles)}))
    return result(note, {
        "run": str(rundir.path), "path": str(lit_path), "format": fmt, "camp": camp,
        "n_added": len(added), "n_skipped": skipped, "n_dup_in_file": len(dup_titles),
        "n_foreign": len(foreign), "n_records": len(corpus.records),
        "warnings": warnings,
        "next": f"oss lit screen --run {run}（只需判新增的 {len(added)} 篇）" if added else "",
    })


def snowball(run: str, *, seeds: str = "", top: int = 8, backward: int = 50, forward: int = 15) -> dict:
    """从种子往后拉参考文献、往前拉引用，扩进语料。

    第一期实测：容量翻 2.5 倍召回不动——必找集里的文献靠主题检索够不着，只有引文链能到。
    种子缺省按 `rank()` 取前 `top` 篇（筛过的语料只在纳入里取）；点名用 `seeds`。
    滚完新增的记录没筛过，语料 `screened` 翻假，**接着跑 `oss lit screen` 只判新增的**。
    OpenAlex 不可用是 `Unavailable`：滚雪球没有别的源可退。
    """
    rundir = require_run(run)
    lit_path = rundir.file(ARTIFACTS["literature"])
    if not lit_path.exists():
        raise Missing("literature", "缺 literature.json——先跑 oss lit search", code=1)
    corpus = LiteratureCorpus.load(lit_path)
    warnings: list[str] = []
    traced = ""  # 除了这一轮的账之外还要写进留痕的话
    wanted = [s for s in seeds.split(",") if s.strip()] if seeds else []
    if wanted:
        chosen, missing = reading_mod.pick(corpus, wanted)
        if missing:
            warnings.append(f"语料里没有这 {len(missing)} 个 id：{'、'.join(missing)}")
        if not chosen:
            raise Missing("records", "点名的种子一个都不在语料里", code=1, data={"warnings": warnings})
        # 点名一篇筛选时排除的当种子：**照滚**——点名就是判断，工具不替它翻案
        # （`oss lit fetch --ids` 一个门面）。但从一篇筛掉的文献往外滚，
        # 它引的和引它的多半也跑题，而这件事只活在一次终端输出里就等于没发生。
        dropped = [r.paper_id for r in chosen if r.screen == "排除"]
        if dropped:
            warnings.append(f"点名的 {len(dropped)} 篇是筛选时排除的（{'、'.join(dropped)}）——"
                            f"照滚，但它引的和引它的多半也跑题，留痕里看得见")
            traced = f"；种子里有筛选时排除的 {len(dropped)} 篇：{'、'.join(dropped)}"
        cmd = f"oss lit snowball --seeds <{len(chosen)} 篇>"
    else:
        if not corpus.screened:
            warnings.append("语料还没筛就滚雪球——种子按词重合排，可能带着沾词的无关文献往外滚；"
                            "先 oss lit screen 更稳")
        chosen = reading_mod.rank(corpus, top)
        if not chosen:
            raise Missing("records", "语料里排不出种子（没有跟检索式沾边的记录）", code=1,
                          data={"warnings": warnings})
        cmd = f"oss lit snowball --top {top}"
    before = len(corpus.records)
    try:
        this = lit_module.snowball(corpus, chosen, backward=backward, forward=forward)
    except lit_module.SourceUnavailable as exc:
        raise Unavailable(f"滚雪球要 OpenAlex，它不可用：{exc.reason}", code=1) from exc
    corpus.save(lit_path)
    # 核心把换不到 W 号（没编号也没 DOI）的种子悄悄跳过了。不说出来的话，
    # 调用方只看见「新增 0 篇」，会当成引文链到头了——其实是一篇都没出发。
    if len(this.seeds) < len(chosen):
        warnings.append(f"{len(chosen) - len(this.seeds)} 个种子没有 OpenAlex 编号也没有 DOI，没滚")
    note = (f"从 {len(this.seeds)} 篇出发，往后 {this.backward} 条、往前 {this.forward} 条，"
            f"新增 {this.added} 篇（{before}→{len(corpus.records)}）")
    if not this.added:
        note += "——换种子（--seeds）或加大 --backward/--forward"
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="literature", command=cmd, outcome="ok",
        artifacts=[lit_path.name], note=note + traced))
    return result(note, {
        "run": str(rundir.path), "path": str(lit_path), "warnings": warnings,
        "seeds": [r.paper_id for r in chosen], "n_added": this.added,
        "backward": this.backward, "forward": this.forward, "n_records": len(corpus.records),
        "next": f"oss lit screen --run {run}（只需判新增的 {this.added} 篇）" if this.added else "",
    })


def fetch(run: str, *, top: int = 20, ids: str = "") -> dict:
    """把最值得精读的文献拿到本地：有开放获取链接的抽全文，没有的只留摘要。

    **来源层级会标清**（`data["items"]` 每条的 `level`）：全文 / 仅摘要 / 仅元数据。
    一张只读了摘要的读书卡，它的「局限」栏几乎必然是空的——那不是文献没局限，是没读到。

    **`--top` 排出来的是候选，读哪几篇是判断。**验证跑里按排名拿的前 20 篇
    只有 8 篇跟题目沾边，执行者最后是自己在语料里按关键词挑的——
    那就让它直接点名：`ids`。
    """
    rundir = require_run(run)
    lit_path = rundir.file(ARTIFACTS["literature"])
    if not lit_path.exists():
        raise Missing("literature", "缺 literature.json——先跑 oss lit search", code=1)
    corpus = LiteratureCorpus.load(lit_path)
    out_dir = rundir.file("reading")
    wanted = [s for s in ids.split(",") if s.strip()] if ids else []
    warnings: list[str] = []
    traced = ""  # 除了覆盖率之外还要写进留痕的话
    if wanted:
        chosen, missing = reading_mod.pick(corpus, wanted)
        if missing:
            warnings.append(f"语料里没有这 {len(missing)} 个 id：{'、'.join(missing)}——"
                            f"用 oss lit add <DOI> 先抓进来")
        if not chosen:
            # 警告跟着拒绝一起走：它原先打在 ✗ 前面，门面照旧
            raise Missing("records", "点名的 id 一个都不在语料里", code=1,
                          data={"warnings": warnings})
        # 点名要读一篇筛选时排除的：**照拿**——点名就是判断，工具不替它翻案。
        # 但这件事只活在一次终端输出里就等于没发生，所以也写进留痕。
        dropped = [r.paper_id for r in chosen if r.screen == "排除"]
        if dropped:
            warnings.append(f"点名的 {len(dropped)} 篇是筛选时排除的（{'、'.join(dropped)}）——"
                            f"照拿，但留痕里看得见")
            traced = f"；点名了筛选时排除的 {len(dropped)} 篇：{'、'.join(dropped)}"
        items = reading_mod.fetch(corpus, out_dir, records=chosen)
        cmd = f"oss lit fetch --ids <{len(chosen)} 篇>"
    else:
        if not corpus.screened:
            warnings.append("语料还没筛（oss lit screen）——--top 排的是全部记录，"
                            "沾词的无关文献也在里面")
        items = reading_mod.fetch(corpus, out_dir, top=top)
        cmd = f"oss lit fetch --top {top}"
    note = reading_mod.coverage_note(items)
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="literature", command=cmd,
        outcome="ok", artifacts=["reading/index.json"], note=note + traced))
    return result(note, {
        "run": str(rundir.path), "path": str(out_dir), "note": note,
        "warnings": warnings,
        "items": [{"paper_id": it.paper_id, "title": it.title, "level": it.level,
                   "chars": it.chars, "path": it.path, "reason": it.reason}
                  for it in items],
    })


def screen(run: str) -> dict:
    """把模型写的筛选判定（screening.json）盖到语料上。

    **每篇都要判，排除要写理由。**工具的相关度判据只有「沾没沾词」，判不了「是不是同一件事」；
    六次真跑 `off_topic_count` 全是 0，而语料里明明有「世界科学家警告」这种东西。
    通过后 `oss lit fetch --top` 只排纳入的。过不了校验时**语料一个字段都不动**，
    警告跟着拒绝一起走（`data["warnings"]`），跟 `cards` 一个门面。
    """
    rundir = require_run(run)
    lit_path = rundir.file(ARTIFACTS["literature"])
    scr_path = rundir.file(ARTIFACTS["screening"])
    if not lit_path.exists():
        raise Missing("literature", "缺 literature.json——先跑 oss lit search", code=1)
    if not scr_path.exists():
        raise Missing("screening",
                      f"缺 {ARTIFACTS['screening']}——筛选判定是 skill 写的，格式见 oss schema screening --example",
                      code=1)
    try:
        screening = Screening.load(scr_path)
    except Exception as exc:  # noqa: BLE001 —— pydantic 什么都可能抛，一律算「格式不对」
        raise BadInput(f"{ARTIFACTS['screening']} 格式不对：{str(exc)[:300]}", code=1) from exc
    corpus = LiteratureCorpus.load(lit_path)
    problems, warnings = lit_module.apply_screening(corpus, screening)
    # `n_excluded` 说的是**这次盖上了几篇**。拒绝时一篇都没盖上，所以是 0——
    # 照搬盘上的旧数字（上一轮筛过的语料里它可能是 37），调用方会以为这次筛成功了。
    data = {"run": str(rundir.path), "path": str(lit_path), "warnings": warnings,
            "n_records": len(corpus.records),
            "n_excluded": 0 if problems else corpus.n_excluded}
    if problems:
        rundir.append_trace(TraceEvent(
            seq=rundir.next_seq(), step="literature", command="oss lit screen",
            outcome="refused", note="；".join(problems)[:300]))
        raise Refused("筛选判定过不了校验：", code=1, problems=problems, data=data)
    corpus.save(lit_path)
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="literature", command="oss lit screen", outcome="ok",
        artifacts=[lit_path.name], note=f"{len(corpus.records)} 篇判完，排除 {corpus.n_excluded}"))
    return result(f"{len(corpus.records)} 篇判完，排除 {corpus.n_excluded}，精读排序只看纳入的", data)


def cards(run: str, *, check: bool = True) -> dict:
    """校验读书卡：**局限引文必须能在拿到的文本里逐字找到**。

    借自 nature-reader 的源锚点纪律。找不到的一律拒绝——
    「大概是这个意思」正是空白地图最不能建在上面的东西。

    `check` 是命令行上 `--check/--no-check` 的落点。**它现在不改变任何行为**
    （校验本来就是这条命令的全部内容），保留是为了不动命令行签名。

    警告与拒绝分开：警告不挡路，但**都要打**，过不了校验时也一样——
    所以它跟着 `Refused` 走（`data["warnings"]`）。
    """
    rundir = require_run(run)
    path = rundir.file(ARTIFACTS["reading"])
    if not path.exists():
        raise Missing(
            "reading",
            f"缺 {ARTIFACTS['reading']}——读书卡是 skill 写的，格式见 oss schema reading --example",
            code=1)
    try:
        cards_obj = ReadingCards.load(path)
    except Exception as exc:  # noqa: BLE001 —— pydantic 什么都可能抛，一律算「格式不对」
        raise BadInput(f"{ARTIFACTS['reading']} 格式不对：{str(exc)[:300]}", code=1) from exc
    problems, warnings = reading_mod.check_cards(cards_obj, rundir.file("reading"))
    levels: dict[str, int] = {}
    for c in cards_obj.cards:
        levels[c.source_level] = levels.get(c.source_level, 0) + 1
    n_quotes = sum(len(c.limitations) for c in cards_obj.cards)
    data = {"run": str(rundir.path), "path": str(path), "warnings": warnings,
            "n_cards": len(cards_obj.cards), "n_quotes": n_quotes, "levels": levels}
    if problems:
        rundir.append_trace(TraceEvent(
            seq=rundir.next_seq(), step="reading", command="oss lit cards",
            outcome="refused", note="；".join(problems)[:300]))
        raise Refused("读书卡过不了校验：", code=1, problems=problems, data=data)
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="reading", command="oss lit cards", outcome="ok",
        artifacts=[path.name],
        note=f"{len(cards_obj.cards)} 张卡（{levels}），{n_quotes} 条局限引文全部钉回原文"))
    return result(
        f"{len(cards_obj.cards)} 张读书卡，{n_quotes} 条局限引文全部能在原文里找到", data)


def verify(run: str, *, out: str = "") -> dict:
    """核对论文里每一处引用的**转述**对不对。

    上一步（`{{cite:...}}`）保证了「这篇文献真实存在、作者年份没写错」，
    **保证不了「转述得对」**——编号是真的、年份是对的，内容可以是歪的。

    这一步分两层：

    1. **机器比对**（这里做）：把转述里的概念经中英词典映射后，
       去被引文献的标题+摘要里找。只能抓明显的张冠李戴。
       **没有摘要、或转述里没有可映射的概念 → 判「核不了」，不判「存疑」。**
    2. **跨模型判**：产出的配对文件交给别家模型，问「这句话是这篇文献支持的吗」。
       细微的过度引申只有这一层抓得到。

    叙述式 `{{cite:...}}` 与括注式 `{{citep:...}}` 都核——后者可以一次
    引几篇（`{{citep:a;b}}`），**每篇各判一次**，都配同一句「论文原话」
    （转述是针对整句写的，不是针对某一个 id）；不在语料里的 id 跳过，
    不算一处。

    一处引用都没有时**照样退 0**：稿子里不写引用不是错误，只是没什么可核的。
    """
    rundir = require_run(run)
    paper_path = rundir.file(ARTIFACTS["paper"])
    lit_path = rundir.file(ARTIFACTS["literature"])
    for key, pth, what in (("paper", paper_path, "paper.md"),
                           ("literature", lit_path, ARTIFACTS["literature"])):
        if not pth.exists():
            raise Missing(key, f"缺 {what}", code=1)
    corpus = LiteratureCorpus.load(lit_path)
    text = paper_path.read_text(encoding="utf-8")

    pairs, tally = [], {"支持": 0, "存疑": 0, manuscript.UNCHECKABLE: 0}
    for sent, m in manuscript.sentences_with(text):
        if m.group(1) not in ("cite", "citep"):
            continue
        # citep 一次可以引几篇（`{{citep:a;b}}`）——拆开后每篇各自成一条 pair，
        # 都配同一句「论文原话」；cite 只有一个 id，形状跟从前一样。
        ids = manuscript.split_ids(m.group(2)) if m.group(1) == "citep" else [m.group(2)]
        for one in ids:
            rec = manuscript.find_record(corpus, one)
            if rec is None:
                continue
            status, why = manuscript.citation_support(sent, rec)
            tally[status] = tally.get(status, 0) + 1
            pairs.append({
                "cite": one, "paper_id": rec.paper_id, "doi": rec.doi,
                "论文原话": sent.strip(), "被引文献标题": rec.title,
                "被引文献摘要": (getattr(rec, "abstract", "") or "")[:1200],
                "机器判定": status, "理由": why,
            })

    if not pairs:
        return result("论文里没有 {{cite:...}} / {{citep:...}} 引用，没什么可核的",
                      {"run": str(rundir.path), "path": "", "n_pairs": 0,
                       "tally": tally, "pairs": []})
    target = Path(out) if out else rundir.file("citation_check.json")
    target.write_text(json.dumps(pairs, ensure_ascii=False, indent=2), encoding="utf-8")
    note = f"{len(pairs)} 处引用：" + "、".join(f"{k} {v}" for k, v in tally.items() if v)
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="literature", command="oss lit verify",
        outcome="ok", artifacts=[target.name], note=note))
    return result(note, {
        "run": str(rundir.path), "path": str(target), "n_pairs": len(pairs),
        "tally": tally, "pairs": pairs,
        # `--run` 用调用方给的那个字符串原样拼回去：这一行是要照抄去跑的，
        # 换成规整过的路径就未必还指向同一个目录。
        "next": f"oss review {target} --kind citations --level deep --run {run}",
    })
