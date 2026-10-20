"""公开数据源：题目需要什么数据，就去拿。

**为什么要这一步。**七篇论文结论弱的根子之二是数据旧：想题目环节找到的最好题目
几乎都标着「需要额外数据」，然后被手上能跑的那个顶替。系统知道好题目在哪，但不会去拿数据。

第一批只接**不用注册**的三个源：

| 源 | 目录 | 数据 |
|---|---|---|
| 世界银行 WDI | 1,498 个指标 | 国家-年，自带 ISO3 |
| OECD | 1,546 个数据流（SDMX） | 维度多，原样保留，让分析计划去筛 |
| Our World in Data | 4,371 张图表（sitemap） | 国家-年，自带 ISO3 |

这里做的全是机械的那一半：**搜目录、下数据、整成「国家-年」长表、记出处**。
「这个指标是不是我要的构念」是判断，归 skill——搜出来的是候选，不是答案。

**每份数据都带 ISO3 代码。**QoG × OWID 那次按国家名拼丢了 25%，
就因为一边写 `Korea (the Republic of)` 一边写 `South Korea`；有代码就按代码拼。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

WB = "https://api.worldbank.org/v2"
OECD = "https://sdmx.oecd.org/public/rest"
OWID_SITEMAP = "https://ourworldindata.org/sitemap.xml"
OWID_GRAPHER = "https://ourworldindata.org/grapher"

SOURCES = ("wb", "oecd", "owid")

_STOP = frozenset("of the and in on for to a an by per with as at from or total share rate".split())


def _tokens(s: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(w) > 2 and w not in _STOP}


@dataclass
class Candidate:
    source: str
    code: str
    name: str
    extra: str = ""
    score: int = 0


# ------------------------------------------------------------------ 目录（缓存）


class Catalog:
    """三个源的目录，本地缓存一天。搜索是本地词元匹配——目录不大，不值得每次打网络。"""

    def __init__(self, cache_dir: Path, client: httpx.Client | None = None, ttl_hours: int = 24) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._ttl = ttl_hours * 3600

    def _get_cached(self, name: str, fetch) -> list[dict]:
        p = self.cache_dir / f"{name}.json"
        if p.exists() and time.time() - p.stat().st_mtime < self._ttl:
            return json.loads(p.read_text(encoding="utf-8"))
        data = fetch()
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def _client_or_new(self) -> httpx.Client:
        return self._client or httpx.Client(timeout=60, follow_redirects=True,
                                            headers={"User-Agent": "oss-data/0.3"})

    # --- 各源的目录
    def wb(self) -> list[dict]:
        def fetch():
            c = self._client_or_new()
            out = []
            page = 1
            while True:
                r = c.get(f"{WB}/indicator", params={"source": 2, "format": "json", "per_page": 500, "page": page})
                r.raise_for_status()
                meta, rows = r.json()
                out += [{"code": i["id"], "name": i["name"], "extra": (i.get("sourceNote") or "")[:200]} for i in rows]
                if page >= int(meta.get("pages", 1)):
                    break
                page += 1
            return out
        return self._get_cached("wb", fetch)

    def oecd(self) -> list[dict]:
        def fetch():
            c = self._client_or_new()
            r = c.get(f"{OECD}/dataflow/all", params={"detail": "allstubs"},
                      headers={"Accept": "application/vnd.sdmx.structure+json;version=1.0"})
            r.raise_for_status()
            flows = r.json().get("data", {}).get("dataflows", [])
            return [{"code": f"{f['agencyID']},{f['id']},{f.get('version', '')}",
                     "name": f.get("name") or "", "extra": ""} for f in flows]
        return self._get_cached("oecd", fetch)

    def owid(self) -> list[dict]:
        def fetch():
            c = self._client_or_new()
            r = c.get(OWID_SITEMAP)
            r.raise_for_status()
            slugs = sorted(set(re.findall(r"ourworldindata\.org/grapher/([a-z0-9\-]+)", r.text)))
            return [{"code": s, "name": s.replace("-", " "), "extra": ""} for s in slugs]
        return self._get_cached("owid", fetch)

    def search(self, query: str, sources: tuple[str, ...] = SOURCES, top: int = 12) -> list[Candidate]:
        """词元重合数排序。**只给候选，不认定。**"""
        q = _tokens(query)
        if not q:
            return []
        out: list[Candidate] = []
        for src in sources:
            items = {"wb": self.wb, "oecd": self.oecd, "owid": self.owid}[src]()
            for it in items:
                hay = _tokens(f"{it['name']} {it.get('extra', '')}")
                score = len(q & hay)
                if score:
                    out.append(Candidate(source=src, code=it["code"], name=it["name"],
                                         extra=it.get("extra", "")[:100], score=score))
        out.sort(key=lambda c: (-c.score, c.source, c.name))
        return out[:top]


# ------------------------------------------------------------------ 拉数据


@dataclass
class FetchResult:
    source: str
    code: str
    path: str
    rows: int
    countries: int
    years: str
    columns: list[str]
    url: str
    fetched_at: str
    notes: list[str]


def _wb_countries(c: httpx.Client) -> set[str]:
    """真正的国家（去掉「东亚与太平洋」这类汇总口径）。汇总行混进国家面板是静默失败。"""
    r = c.get(f"{WB}/country", params={"format": "json", "per_page": 400})
    r.raise_for_status()
    return {x["id"] for x in r.json()[1] if (x.get("region") or {}).get("value") != "Aggregates"}


def fetch_wb(code: str, out: Path, *, client: httpx.Client | None = None,
             year_from: int = 1960, year_to: int = 2025) -> FetchResult:
    c = client or httpx.Client(timeout=90, headers={"User-Agent": "oss-data/0.3"})
    notes: list[str] = []
    url = f"{WB}/country/all/indicator/{code}"
    r = c.get(url, params={"format": "json", "per_page": 20000, "date": f"{year_from}:{year_to}"})
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        raise ValueError(f"世界银行没有返回数据：{str(payload)[:200]}")
    countries = _wb_countries(c)
    rows = [{"country": x["country"]["value"], "iso3": x["countryiso3code"], "year": int(x["date"]),
             "value": x["value"]} for x in payload[1] if x.get("countryiso3code")]
    df = pd.DataFrame(rows)
    n_all = df.iso3.nunique()
    df = df[df.iso3.isin(countries)]
    notes.append(f"去掉 {n_all - df.iso3.nunique()} 个汇总口径（地区、收入组），只留国家")
    df = df.dropna(subset=["value"]).sort_values(["iso3", "year"])
    name = re.sub(r"[^A-Za-z0-9]+", "_", code).strip("_").lower()
    df = df.rename(columns={"value": name})
    df.to_csv(out, index=False)
    return _result("wb", code, out, df, url, notes)


def fetch_owid(slug: str, out: Path, *, client: httpx.Client | None = None) -> FetchResult:
    c = client or httpx.Client(timeout=90, follow_redirects=True, headers={"User-Agent": "oss-data/0.3"})
    url = f"{OWID_GRAPHER}/{slug}.csv"
    r = c.get(url, params={"csvType": "full", "useColumnShortNames": "true"})
    r.raise_for_status()
    if r.text.lstrip().startswith("<"):
        raise ValueError(f"OWID 返回的是网页不是 CSV——这个图表可能不存在：{slug}")
    from io import StringIO

    df = pd.read_csv(StringIO(r.text))
    notes: list[str] = []
    ren = {"Entity": "country", "entity": "country", "Code": "iso3", "code": "iso3", "Year": "year"}
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    if "iso3" in df.columns:
        n_all = len(df)
        # OWID 里汇总口径的 code 是空的、OWID_ 开头（World、大洲）或 WB_ 开头（世界银行地区）；
        # 真国家的 ISO3 恰好三个字母——按这条一刀切，不靠前缀清单
        code = df.iso3.astype(str)
        df = df[df.iso3.notna() & code.str.fullmatch(r"[A-Z]{3}")]
        notes.append(f"去掉 {n_all - len(df)} 行汇总口径（World、大洲、收入组）")
    df.to_csv(out, index=False)
    return _result("owid", slug, out, df, url, notes)


def fetch_oecd(flow: str, out: Path, *, client: httpx.Client | None = None,
               year_from: int | None = None, year_to: int | None = None) -> FetchResult:
    """OECD 的 SDMX 数据流维度多（性别、年龄、口径……），**原样保留所有维度列**，
    只把国家代码和年份改成统一名字。挑哪一层是分析计划的事（sample_filter）。"""
    c = client or httpx.Client(timeout=180, headers={"User-Agent": "oss-data/0.3"})
    url = f"{OECD}/data/{flow}/all"
    params = {"format": "csvfilewithlabels"}
    if year_from:
        params["startPeriod"] = str(year_from)
    if year_to:
        params["endPeriod"] = str(year_to)
    r = c.get(url, params=params, headers={"Accept": "text/csv"})
    r.raise_for_status()
    from io import StringIO

    df = pd.read_csv(StringIO(r.text))
    notes: list[str] = []
    ren = {"REF_AREA": "iso3", "Reference area": "country", "TIME_PERIOD": "year", "OBS_VALUE": "value"}
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    # SDMX 的结构性废列（STRUCTURE / STRUCTURE_ID / ACTION…）整列同一个值，只占地方
    df = df.drop(columns=[c for c in ("STRUCTURE", "STRUCTURE_ID", "STRUCTURE_NAME", "ACTION") if c in df.columns])
    if "year" in df.columns:
        df["year"] = pd.to_numeric(df["year"].astype(str).str[:4], errors="coerce")
    dims = [col for col in df.columns if col not in ("iso3", "country", "year", "value")
            and df[col].nunique() > 1]
    if dims:
        notes.append(f"这个数据流还有 {len(dims)} 个维度（{'、'.join(dims[:6])}）——"
                     f"**一个国家-年可能有多行**，分析前要在 sample_filter 里选定一层")
    df.to_csv(out, index=False)
    return _result("oecd", flow, out, df, url, notes)


def _result(source: str, code: str, out: Path, df: pd.DataFrame, url: str, notes: list[str]) -> FetchResult:
    years = ""
    if "year" in df.columns and df["year"].notna().any():
        years = f"{int(df['year'].min())}–{int(df['year'].max())}"
    res = FetchResult(
        source=source, code=code, path=str(out), rows=int(len(df)),
        countries=int(df["iso3"].nunique()) if "iso3" in df.columns else 0,
        years=years, columns=list(map(str, df.columns)), url=url,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), notes=notes,
    )
    # 出处跟数据放一起。**没有出处的数据跟没有出处的数字一样**——复现时答不出「哪来的、哪天拿的」。
    Path(str(out) + ".meta.json").write_text(json.dumps(asdict(res), ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def fetch(source: str, code: str, out: Path, **kw) -> FetchResult:
    if source == "wb":
        return fetch_wb(code, out, **kw)
    if source == "owid":
        return fetch_owid(code, out, **{k: v for k, v in kw.items() if k == "client"})
    if source == "oecd":
        return fetch_oecd(code, out, **kw)
    raise ValueError(f"不认识的源 {source!r}，可选：{'、'.join(SOURCES)}")
