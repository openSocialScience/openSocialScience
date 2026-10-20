"""公开数据源：搜目录、拉数据、剔汇总、记出处。全部用假的 HTTP，不碰网络。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pandas as pd

from oss.core.datasources import Catalog, fetch_oecd, fetch_owid, fetch_wb


class _Client:
    def __init__(self, routes): self.routes = routes
    def get(self, url, params=None, headers=None):
        for key, (status, body, ctype) in self.routes.items():
            if key in url:
                content = body if isinstance(body, bytes) else (json.dumps(body) if not isinstance(body, str) else body).encode()
                return httpx.Response(status, content=content, headers={"content-type": ctype},
                                      request=httpx.Request("GET", url))
        return httpx.Response(404, request=httpx.Request("GET", url))
    def close(self): pass


# ─────────────────────────── 搜目录：词元重合，只给候选


def test_搜索按词元重合排序且不认定(tmp_path):
    cat = Catalog(tmp_path, client=_Client({
        "/indicator": (200, [{"pages": 1}, [
            {"id": "IT.NET.USER.ZS", "name": "Individuals using the Internet (% of population)", "sourceNote": ""},
            {"id": "NY.GDP.PCAP.KD", "name": "GDP per capita (constant 2015 US$)", "sourceNote": ""},
        ]], "application/json"),
    }))
    hits = cat.search("internet users share", sources=("wb",))
    assert hits and hits[0].code == "IT.NET.USER.ZS"
    assert all(h.code != "NY.GDP.PCAP.KD" for h in hits), "一个词都不沾的不进候选"


def test_目录有缓存不重复打网络(tmp_path):
    calls = []
    class C(_Client):
        def get(self, url, params=None, headers=None):
            calls.append(url); return super().get(url, params, headers)
    cat = Catalog(tmp_path, client=C({"/indicator": (200, [{"pages": 1}, []], "application/json")}))
    cat.wb(); cat.wb()
    assert len(calls) == 1


# ─────────────────────────── 世界银行：汇总口径必须剔掉


def test_世界银行去掉地区与收入组汇总(tmp_path):
    client = _Client({
        "/indicator/NY": (200, [{"total": 3}, [
            {"country": {"value": "Albania"}, "countryiso3code": "ALB", "date": "2020", "value": 5000.0},
            {"country": {"value": "World"}, "countryiso3code": "WLD", "date": "2020", "value": 11000.0},
            {"country": {"value": "Albania"}, "countryiso3code": "ALB", "date": "2019", "value": None},
        ]], "application/json"),
        "/country?": (200, [{}, [
            {"id": "ALB", "region": {"value": "Europe & Central Asia"}},
            {"id": "WLD", "region": {"value": "Aggregates"}},
        ]], "application/json"),
        "/country": (200, [{}, [
            {"id": "ALB", "region": {"value": "Europe & Central Asia"}},
            {"id": "WLD", "region": {"value": "Aggregates"}},
        ]], "application/json"),
    })
    out = tmp_path / "gdp.csv"
    res = fetch_wb("NY.GDP.PCAP.KD", out, client=client)
    df = pd.read_csv(out)
    assert list(df.iso3) == ["ALB"], "World 是汇总口径，混进国家面板是静默失败"
    assert res.countries == 1 and "汇总口径" in " ".join(res.notes)
    meta = json.loads(Path(str(out) + ".meta.json").read_text())
    assert meta["url"] and meta["fetched_at"], "没有出处的数据跟没有出处的数字一样"


# ─────────────────────────── OWID：World / 大洲的 code 是 OWID_ 开头


def test_OWID去掉世界与大洲(tmp_path):
    csv = "Entity,Code,Year,it_net_user_zs\nChina,CHN,2020,70\nWorld,OWID_WRL,2020,60\nAsia,,2020,55\n"
    res = fetch_owid("share-of-individuals-using-the-internet", tmp_path / "o.csv",
                     client=_Client({"grapher": (200, csv, "text/csv")}))
    df = pd.read_csv(tmp_path / "o.csv")
    assert list(df.iso3) == ["CHN"] and list(df.columns[:3]) == ["country", "iso3", "year"]
    assert res.rows == 1


def test_OWID返回网页要报不是CSV(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="网页"):
        fetch_owid("nope", tmp_path / "o.csv", client=_Client({"grapher": (200, "<html>", "text/html")}))


# ─────────────────────────── OECD：维度多，要说出来


def test_OECD多维度要提醒一个国家年可能多行(tmp_path):
    csv = ("REF_AREA,Reference area,AGE,Age,TIME_PERIOD,OBS_VALUE\n"
           "AUS,Australia,Y_LT18,Less than 18,2020,13.3\nAUS,Australia,Y18T65,18 to 65,2020,9.1\n")
    res = fetch_oecd("OECD.X,DF_Y,1.0", tmp_path / "e.csv", client=_Client({"/data/": (200, csv, "text/csv")}))
    df = pd.read_csv(tmp_path / "e.csv")
    assert {"iso3", "country", "year", "value"} <= set(df.columns)
    assert any("多行" in n and "AGE" in n for n in res.notes)
