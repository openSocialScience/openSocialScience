#!/usr/bin/env python3
"""手工刊名清单 → OpenAlex 源记录白名单（`oss/assets/zh_core_sources.json`）。

**为什么要一张表而不是现查**：`oss lit search --zh` 每次都要把「哪些源算中文核心刊」
告诉 OpenAlex。现查要 25 本刊 × 三路调用，一次检索多打七八十次 API（免费密钥每天
约 1000 次），而这张表一年也变不了几次——所以离线建一次、入库，检索时只读文件。

**三路匹配**（同一本刊在 OpenAlex 里常有两条源记录，缺一条就漏掉一半作品）：
`filter=issn:<issn>`、`search=<刊名>`、`search=<每个别名>`。2026-09-05 实测：
`管理世界` 有中文名的 S4306556525（无 ISSN），`财经研究` 在表里有 S2764941960 与
S4306558568 两条；只按 ISSN 查会漏掉中文名那条，只按中文名查会漏掉拼音名那条。`GET /sources?search=Guanli shijie` 能搜到中文名那条（别名匹配）。

**创刊年过滤**：OpenAlex 里有同名的低端新刊——`教育研究` S4210209493 是新加坡 2018 年
起的 OA 刊，`counts_by_year` 首年就是 2018。真核心刊在 OpenAlex 里的首年 ≤ 2011
（闸设在 2011 而不是 2010：《社会》在 OpenAlex 里首年 2011，2010 会误伤）。
所以刊名匹配的候选还要过这道闸；**ISSN 命中的直接接受**——ISSN 是刊的身份证，
不需要再拿创刊年去猜。

只 import 标准库和 httpx（`oss` 包不进来：这是一次性的建表脚本，不该把包的导入链
拖进来）。要 `OPENALEX_API_KEY`，脚本自己读 `.env`。

    python scripts/build_zh_whitelist.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
JOURNALS = ROOT / "oss" / "assets" / "zh_core_journals.json"
OUT = ROOT / "oss" / "assets" / "zh_core_sources.json"
OPENALEX = "https://api.openalex.org"

#: OpenAlex 源记录里够用的字段。不 select 的话每条回来几十 KB，25 本刊要拉几兆。
#: `issn` 是列表字段（一本刊常有纸本、电子两个号），`issn_l` 是它的规范号。
#: 字段名以 OpenAlex 的 select 白名单为准——写 `issns` 会被 400 顶回来。
SELECT = "id,display_name,works_count,issn_l,issn,counts_by_year,country_code,alternate_titles"

#: 真核心刊在 OpenAlex 里的首年上限。同名新刊（`教育研究` S4210209493 首年 2018）过不了这道闸。
#: **2011 不是 2010**：《社会》（`Chinese Journal of Sociology` S2764961298）在 OpenAlex 里
#: 首年就是 2011——它是 1981 年创刊的真核心刊，OpenAlex 只是收录得晚，2010 会把它误伤掉。
#: 放到 2011 仍然挡得住同名新刊：那批是 2018 年起的 OA 刊，隔着七年。
FIRST_YEAR_MAX = 2011


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _norm(s: str) -> str:
    """刊名归一化：全角括号 → 半角、去所有空白、小写。

    `经济学（季刊）` 在 OpenAlex 里写成 `经济学(季刊)`，全角括号不归一就永远不等。
    """
    t = str(s or "").replace("（", "(").replace("）", ")").replace("〔", "(").replace("〕", ")")
    return "".join(t.split()).lower()


def _first_year(counts: list[dict]) -> int | None:
    """`counts_by_year` 里**有作品**的最早年份。零作品的年份不算创刊。"""
    years = [int(c["year"]) for c in (counts or [])
             if c.get("year") is not None and int(c.get("works_count") or 0) > 0]
    return min(years) if years else None


def _get(client: httpx.Client, params: dict) -> list[dict]:
    params = {**params, "select": SELECT, "per-page": 5}
    if (k := os.getenv("OPENALEX_API_KEY", "")):
        params["api_key"] = k
    if (m := os.getenv("OPENALEX_MAILTO", "")):
        params["mailto"] = m
    for attempt in range(3):
        try:
            r = client.get(f"{OPENALEX}/sources", params=params, timeout=30)
        except httpx.RequestError as exc:
            print(f"    ! {type(exc).__name__}: {exc}", file=sys.stderr)
        else:
            if r.status_code == 200:
                return list(r.json().get("results") or [])
            print(f"    ! HTTP {r.status_code}: {r.text[:160]}", file=sys.stderr)
            if r.status_code in (401, 403, 409):
                raise SystemExit("OpenAlex 拒了：核对 .env 里的 OPENALEX_API_KEY（免费注册见 "
                                 "https://openalex.org/settings/api）")
            if r.status_code == 400:
                # 参数写坏了，重试三次只是把同一个错打三遍——直接停，把原话摆出来
                raise SystemExit(f"OpenAlex 说参数不对：{r.text[:400]}")
        time.sleep(2 ** attempt)
    return []


def _accept(item: dict, issn: str, names: set[str]) -> tuple[bool, str]:
    """这条源记录算不算这本刊。返回 `(接受吗, 为什么)`。"""
    issns = {str(x).strip() for x in (item.get("issn") or [])}
    if item.get("issn_l"):
        issns.add(str(item["issn_l"]).strip())
    if issn and issn in issns:
        return True, "issn"
    titles = {_norm(item.get("display_name"))} | {_norm(t) for t in (item.get("alternate_titles") or [])}
    if not (titles & names):
        return False, "名字对不上"
    fy = _first_year(item.get("counts_by_year") or [])
    if fy is None or fy > FIRST_YEAR_MAX:
        return False, f"首年 {fy}（> {FIRST_YEAR_MAX}），像同名新刊"
    return True, f"名字命中，首年 {fy}"


def main() -> int:
    _load_env()
    if not os.getenv("OPENALEX_API_KEY"):
        print("缺 OPENALEX_API_KEY（.env 里填）——OpenAlex 自 2026-02-13 起要求密钥", file=sys.stderr)
        return 2
    journals = json.loads(JOURNALS.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    empty: list[str] = []
    with httpx.Client(follow_redirects=True) as client:
        for j in journals:
            name, issn = j["name"], (j.get("issn") or "").strip()
            aliases = list(j.get("aliases") or [])
            names = {_norm(name)} | {_norm(a) for a in aliases}
            # 三路：ISSN 一路，刊名一路，每个别名一路。同一条源记录会被多路捞到，按 id 去重。
            probes: list[dict] = ([{"filter": f"issn:{issn}"}] if issn else [])
            probes += [{"search": name}] + [{"search": a} for a in aliases]
            found: dict[str, dict] = {}
            rejected: list[str] = []
            for p in probes:
                for item in _get(client, p):
                    sid = (item.get("id") or "").rsplit("/", 1)[-1]
                    if not sid or sid in found:
                        continue
                    ok, why = _accept(item, issn, names)
                    if ok:
                        found[sid] = item
                    elif why.startswith("首年"):
                        rejected.append(f"{sid} {item.get('display_name')} — {why}")
            works = sum(int(i.get("works_count") or 0) for i in found.values())
            firsts = [y for i in found.values() if (y := _first_year(i.get("counts_by_year") or []))]
            out[name] = {"sources": sorted(found), "works": works,
                         "first_year": min(firsts) if firsts else None}
            tag = "、".join(f"{s}({found[s].get('display_name')})" for s in sorted(found)) or "—"
            print(f"{name:<12} {len(found)} 条源  {works:>7} 篇  首年 {out[name]['first_year']}  {tag}")
            for r in rejected:
                print(f"    ✗ 丢弃 {r}")
            if not found:
                empty.append(name)

    ids = sorted({s for v in out.values() for s in v["sources"]})
    OUT.write_text(json.dumps(
        {"built": date.today().isoformat(), "journals": out, "source_ids": ids},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(out)} 本刊 → {len(ids)} 条源记录，写入 {OUT.relative_to(ROOT)}")
    if empty:
        print(f"一条都没命中的 {len(empty)} 本：" + "、".join(empty))
    return 0


if __name__ == "__main__":
    sys.exit(main())
