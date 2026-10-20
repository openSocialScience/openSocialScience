"""`oss data search / fetch / list / info / columns / get`：题目需要什么数据，就去拿。

两条来路，分得很清：

* **指标**（`search` / `fetch`）：世界银行、OECD、Our World in Data 的「国家-年」序列。
  目录能搜、指标能直接拉，出处写进 `.meta.json`。
* **数据包**（`list` / `info` / `columns` / `get`）：整套的调查与国别数据（GSS、QoG、V-Dem、
  CGSS、CFPS、WVS）。能直接下的就下，要申请的**系统不替你下**——它停下来告诉你怎么申请、放哪。

退出码在这一层写死，说明书按它们分支：

    3  要申请、还没放进来（`NeedsManualDownload`）——**不是参数错，也不是网断了**
    2  名字不对 / 还没拿过（`KeyError` / `FileNotFoundError`）
    1  别的都算「拿不下来」

`columns` 是环节 3 那条纪律的工具：评审第四条致命问题是「理论要『未审查的政治信息』，
测的是『上网人口比例』；V-Dem 的数字社会模块是公开的，论文说『需要这些数据』却没去找」。
**先搜列，再说没有。**所以它跟 MCP 那一侧一起搬过来，不留在命令行里。

中文与排版全部归门面：`_STATUS_ZH`（本地已有 / 可直接下载 / …）留在 `oss/cli/main.py`，
这里只给 `status` 的机器名。

运行目录：`fetch` 把数据写进运行目录，且按环节 1 的命令表它可能是**第一条**碰这个目录的
命令（`run_state.json` 还不存在），所以走 `open_run`；`get` 只往留痕里加一行，
给了 `--run` 就必须是个已经存在的目录——而且**查在下载之前**，
打错路径不该在下完几百 MB 之后才发现。
"""

from __future__ import annotations

import re
from pathlib import Path

from oss.api.errors import BadInput, Unavailable
from oss.api.result import result
from oss.api.rundir import open_run, read_json_file, require_run
from oss.core import datapacks as packs_mod
from oss.core import datasources as data_mod
from oss.core.contracts import ARTIFACTS, TraceEvent

#: 三个指标源的目录缓存（一天）。不进运行目录——它跟哪一次研究无关。
DATA_CACHE = Path("~/.cache/oss-data").expanduser()


# ─────────────────────────────────────────────── 指标：搜目录、拉序列


def search(query: str, *, source: str = "", top: int = 12,
           cache: Path | None = None) -> dict:
    """在世界银行、OECD、Our World in Data 的目录里搜指标。**给的是候选，不是答案。**"""
    srcs = (source,) if source else data_mod.SOURCES
    try:
        cands = data_mod.Catalog(cache or DATA_CACHE).search(query, srcs, top=top)
    except Exception as exc:  # noqa: BLE001 —— 三个源各有各的抛法，一律算「拉不下来」
        raise Unavailable(f"目录拉不下来：{exc}", code=1) from exc
    rows = [{"source": c.source, "code": c.code, "name": c.name,
             "extra": c.extra, "score": c.score} for c in cands]
    if not rows:
        # **搜不到不等于没有。**这句必须留在 summary 里：0 命中最常见的原因是
        # 目录按名字匹配、而你用的是构念的说法
        return result("一个都没搜到——换几个词试试；**搜不到不等于没有**，目录是按名字匹配的",
                      {"query": query, "sources": list(srcs), "candidates": []})
    return result(f"{len(rows)} 个候选",
                  {"query": query, "sources": list(srcs), "candidates": rows})


def fetch(source: str, code: str, *, run: str, out: str = "",
          year_from: int = 0, year_to: int = 0) -> dict:
    """把一个指标拉成「国家-年」长表，带 ISO3 代码和出处（`.meta.json`）。"""
    rundir = open_run(run)
    name = out or (re.sub(r"[^A-Za-z0-9]+", "_", f"{source}_{code}").strip("_").lower() + ".csv")
    target = rundir.file(name)
    kw = {}
    if source in ("wb", "oecd"):
        if year_from:
            kw["year_from"] = year_from
        if year_to:
            kw["year_to"] = year_to
    try:
        res = data_mod.fetch(source, code, target, **kw)
    except Exception as exc:  # noqa: BLE001
        raise Unavailable(f"拉不下来：{str(exc)[:300]}", code=1) from exc
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="data", command=f"oss data fetch {source} {code}",
        outcome="ok", artifacts=[target.name],
        note=f"{res.rows} 行，{res.countries} 国，{res.years}",
        args={"source": source, "code": code, "url": res.url, "fetched_at": res.fetched_at}))
    return result(f"{name}  →  {target}", {
        "run": str(rundir.path), "source": source, "code": code,
        "name": name, "path": str(target),
        "rows": res.rows, "countries": res.countries, "years": res.years,
        "columns": list(res.columns), "url": res.url, "fetched_at": res.fetched_at,
        "notes": list(res.notes),
    })


# ─────────────────────────────────────────────── 数据包：清单、详情、列、拿


def list_packs() -> dict:
    """现成数据集清单：哪些能直接下、哪些要申请、哪些已经在本机。

    **清单只列候选，不替你选。**题目需要额外数据时用哪一套是判断，写进计划、留痕。
    """
    rows = packs_mod.list_packs()
    return result(f"{len(rows)} 套数据", {"packs": rows})


def _packs_in_hand(rundir) -> set[str]:
    """这一轮已经在用哪些数据包。**用它们的文件路径认，不看措辞。**

    三次自由跑都撞上同一件事：候选说「CGSS 2006 与 2008 两轮没有可比的单位所有制变量」，
    工具回「数据包 cgss——`oss data get cgss` 直接拿」。**他手上就是 CGSS**，
    缺的正好是 CGSS 里没有的东西——把已经在用的那套再推荐一遍，是纯粹的噪音。
    """
    text = ""
    for name in (ARTIFACTS["profile"], "stack_spec.json", "stack_map.json"):
        try:
            text += (rundir.file(name)).read_text(encoding="utf-8")
        except OSError:
            continue
    low = text.lower()
    return {p for p in packs_mod.PACKS
            if re.search(rf"(?<![0-9a-z]){re.escape(p.lower())}(?![a-z])", low)}


def needs(run: str) -> dict:
    """这一轮候选题目缺哪些数据，合并同类项、按解锁几个候选排序、标可信度。

    信息本来就在 `ideas.json` 的 `needs_extra_data` 里，只是散着、没人读。
    详见 `oss.core.datawants`。
    """
    from oss.core import datawants

    rundir = require_run(run)
    ideas = read_json_file(rundir.file(ARTIFACTS["ideas"])) or {}
    cards = read_json_file(rundir.file(ARTIFACTS["reading"])) or {}
    wants = datawants.collect_wants(ideas, cards, list(packs_mod.PACKS),
                                    in_hand=_packs_in_hand(rundir))
    if not wants:
        return result("候选题目都不缺额外数据", {"wants": [], "lines": []})
    tiers = {1: 0, 2: 0, 3: 0, 4: 0}
    for w in wants:
        tiers[w.tier] = tiers.get(w.tier, 0) + 1
    # 三档与「没点名」都归到「还得自己找」那一栏里报数——对用户是同一件事：
    # 拿不到现成的，得先去把这份数据找出来
    return result(
        f"{len(wants)} 份数据（① 目录里有 {tiers[1]} / ② 文献用过 {tiers[2]} / "
        f"③ 还得自己找 {tiers[3] + tiers[4]}）",
        {"wants": [w.__dict__ for w in wants], "lines": [w.line() for w in wants]})


def info(name: str) -> dict:
    """一套数据的码本、钥匙列、引用格式、许可、怎么拿，以及抽样设计登记。

    抽样设计五栏（`design` / `strata` / `psu` / `weights_by_wave` / `design_source`）
    是调查层声明 `plan.survey` 的底稿：查到的每一格在 `design_source` 里有出处，
    查不到的写「待核」——照抄「待核」进计划等于没声明。
    """
    if name not in packs_mod.PACKS:
        raise BadInput(f"没有 {name!r}。可选：{'、'.join(packs_mod.PACKS)}", code=2)
    p = packs_mod.PACKS[name]
    return result(p.title, {
        "name": name, "title": p.title, "cite_name": p.cite_name, "level": p.level,
        "unit": p.unit, "time": p.time, "iso3": p.iso3,
        "keys": {k: v for k, v in (("unit", p.unit), ("time", p.time), ("iso3", p.iso3)) if v},
        "status": packs_mod.status(name), "license": p.license, "weights": p.weights,
        "homepage": p.homepage, "codebook": p.codebook, "citation": p.citation,
        "note": p.note, "manual": p.manual, "how_to": p.how_to,
        "url": p.url, "size_mb": p.size_mb,
        "design": p.design, "strata": p.strata, "psu": p.psu,
        "weights_by_wave": dict(p.weights_by_wave), "design_source": p.design_source,
    })


def columns(name: str, *, query: str = "", limit: int = 40) -> dict:
    """按构念找列：一套数据里哪些列可能测你要的东西。

    评审第四条致命问题：理论要「未审查的政治信息」，测的是「上网人口比例」；
    V-Dem 的数字社会模块是公开的，论文说「需要这些数据」却没去找。**先搜列，再说没有。**
    找到的是候选，不是答案——口径要看码本。

    `limit` **在这一层截**，不留给门面：GSS 累积文件六千多列，不截就等于
    让每个调用方各自处理一份六千行的返回。`n_rows` 给的是截断前的条数，
    「还有 N 列」那一行照样打得出来。
    """
    try:
        src = packs_mod.source_file(name)
    except packs_mod.NeedsManualDownload as exc:
        raise Unavailable(str(exc), code=3) from exc
    except (KeyError, FileNotFoundError) as exc:
        raise BadInput(str(exc.args[0]) if exc.args else str(exc), code=2) from exc
    index = packs_mod.index_columns(src)
    terms = [t.strip() for t in query.split(",") if t.strip()]
    rows = packs_mod.search_columns(index, terms)
    p = packs_mod.PACKS[name]
    return result(
        f"{p.title}：{len(index)} 列" + (f"，命中 {len(rows)} 列" if terms else ""), {
            "name": name, "title": p.title, "source_file": str(src),
            "terms": terms, "n_index": len(index), "n_rows": len(rows),
            "limit": limit, "rows": rows[:limit],
            "has_labels": any(r["label"] for r in index), "codebook": p.codebook,
        })


def get(name: str, *, columns: str = "", out: str = "", file: str = "",
        refresh: bool = False, run: str = "") -> dict:
    """拿一套数据：第一次下载（或用你放进来的文件）→ 转 CSV → 记出处。之后直接用缓存。

    要申请的数据集系统不能替你下：它会停下来告诉你怎么申请、放哪（退出码 3，
    **不是 1**——「要去申请」和「网断了」是两回事，说明书按这个数分支）。
    """
    # 查目录排在下载**前面**：打错路径不该在下完几百 MB 之后才发现
    rundir = require_run(run) if run else None
    cols = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
    try:
        res = packs_mod.get(name, columns=cols, out=Path(out) if out else None,
                            file=Path(file) if file else None, refresh=refresh)
    except packs_mod.NeedsManualDownload as exc:
        raise Unavailable(str(exc), code=3) from exc
    except KeyError as exc:
        raise BadInput(str(exc.args[0]) if exc.args else str(exc), code=2) from exc
    except Exception as exc:  # noqa: BLE001
        raise Unavailable(f"拿不下来：{str(exc)[:300]}", code=1) from exc
    if rundir is not None:
        # 留痕是业务：这套数据的 sha256 与拿到的时间，事后回答「跑的是哪一版数据」全靠它
        rundir.append_trace(TraceEvent(
            seq=rundir.next_seq(), step="data", command=f"oss data get {name}",
            outcome="ok", artifacts=[Path(res.path).name],
            note=f"{res.title}：{res.rows:,} 行 × {res.n_columns} 列",
            args={"pack": name, "source": res.source, "sha256": res.sha256,
                  "fetched_at": res.fetched_at}))
    return result(res.title, {
        "run": str(rundir.path) if rundir is not None else "",
        "name": name, "title": res.title, "converted": res.converted,
        "path": res.path, "raw": res.raw,
        "rows": res.rows, "n_columns": res.n_columns, "columns": list(res.columns),
        "years": res.years, "keys": dict(res.keys),
        "source": res.source, "sha256": res.sha256, "fetched_at": res.fetched_at,
        "citation": res.citation, "cite_name": packs_mod.PACKS[name].cite_name,
        "codebook": res.codebook, "license": res.license, "weights": res.weights,
        "notes": list(res.notes),
    })
