"""现成数据集清单：需要时才下、下过就存、要申请的只说怎么申请。全部假 HTTP，不碰网络。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pandas as pd
import pytest

from oss.core import datapacks as dp


class _Client:
    def __init__(self, table): self.table = table; self.calls = 0

    def stream(self, method, url):
        self.calls += 1
        body = self.table.get(url)
        status = 200 if body is not None else 404
        return httpx.Response(status, content=body or b"", request=httpx.Request(method, url)).__enter__() \
            if False else _Resp(status, body or b"")


class _Resp:
    def __init__(self, status, body): self.status_code = status; self._body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def iter_bytes(self):
        yield self._body


# ─────────────────────────── 清单本身要说得清

def test_清单每一套都说得清怎么拿怎么引怎么拼():
    for name, p in dp.PACKS.items():
        assert p.title and p.cite_name and p.level and p.unit and p.time, name
        assert p.citation and p.codebook and p.homepage, name
        assert bool(p.url) != bool(p.how_to), f"{name}：要么能直接下，要么说怎么申请，二选一"
        if p.level == "country-year":
            assert p.iso3, f"{name}：国家-年数据必须标 ISO3 列，不然拼不上"


def test_状态_没下是download_要申请是manual_放进来是incoming(tmp_path):
    assert dp.status("qog", tmp_path) == "download"
    assert dp.status("cgss", tmp_path) == "manual"
    (tmp_path / "cgss").mkdir()
    (tmp_path / "cgss" / "cgss2021.dta").write_bytes(b"x")
    assert dp.status("cgss", tmp_path) == "incoming"


# ─────────────────────────── 直接下载的：第一次下、之后用缓存


def _csv_pack(monkeypatch):
    p = dp.Pack(name="toy", title="Toy", cite_name="Toy Data", level="country-year",
                unit="cname", time="year", iso3="ccodealp", url="https://x/toy.csv",
                codebook="c", homepage="h", citation="cite me")
    monkeypatch.setitem(dp.PACKS, "toy", p)
    return p


def test_第一次下载转CSV记出处_第二次用缓存(tmp_path, monkeypatch):
    _csv_pack(monkeypatch)
    body = b"cname,ccodealp,year,x\nAlbania,ALB,2000,1\nAlbania,ALB,2001,2\n"
    client = _Client({"https://x/toy.csv": body})
    res = dp.get("toy", cache=tmp_path, client=client)
    assert res.rows == 2 and res.years == "2000–2001" and res.converted
    assert res.keys == {"unit": "cname", "time": "year", "iso3": "ccodealp"}
    meta = json.loads((tmp_path / "toy" / "toy.meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "https://x/toy.csv" and meta["sha256"] and meta["fetched_at"]
    assert dp.status("toy", tmp_path) == "local"
    dp.get("toy", cache=tmp_path, client=client)
    assert client.calls == 1, "第二次不该再下"
    dp.get("toy", cache=tmp_path, client=client, refresh=True)
    assert client.calls == 2


def test_压缩包里挑最大的匹配文件(tmp_path, monkeypatch):
    p = dp.Pack(name="zipped", title="Z", cite_name="Z", level="country-year", unit="country_name",
                time="year", iso3="country_text_id", url="https://x/z.zip", inner="*.csv",
                codebook="c", homepage="h", citation="c")
    monkeypatch.setitem(dp.PACKS, "zipped", p)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hello")
        z.writestr("small.csv", "country_name,country_text_id,year,v\n")
        z.writestr("V-Dem-CY-Core.csv", "country_name,country_text_id,year,v\nSweden,SWE,1990,0.9\n")
    res = dp.get("zipped", cache=tmp_path, client=_Client({"https://x/z.zip": buf.getvalue()}))
    assert res.rows == 1 and Path(res.path).name == "zipped.csv"


# ─────────────────────────── 要申请的：本地没有就说怎么拿，放进来就转


def test_要申请的本地没文件就说清怎么申请(tmp_path):
    with pytest.raises(dp.NeedsManualDownload, match="注册"):
        dp.get("cgss", cache=tmp_path)


def test_放进来的Stata文件只抽要的列并带值标签(tmp_path):
    d = tmp_path / "cgss"; d.mkdir()
    df = pd.DataFrame({"id": [1, 2, 3], "year": [2021] * 3, "a7a": [1, 2, 9], "b1": [0, 1, 1], "junk": [5, 5, 5]})
    df.to_stata(d / "cgss2021.dta", write_index=False,
                value_labels={"a7a": {1: "yes", 2: "no", 9: "n/a"}})
    res = dp.get("cgss", cache=tmp_path, columns=["id", "year", "a7a"], out=tmp_path / "sub.csv")
    got = pd.read_csv(tmp_path / "sub.csv")
    assert list(got.columns) == ["id", "year", "a7a"] and len(got) == 3
    labels = json.loads((tmp_path / "sub.csv._labels.json").read_text(encoding="utf-8"))
    assert labels["a7a"] == {"1.0": "yes", "2.0": "no", "9.0": "n/a"} or labels["a7a"] == {"1": "yes", "2": "no", "9": "n/a"}
    assert (tmp_path / "sub.csv.meta.json").exists(), "抽出来的表旁边也要有出处"


def test_列名不存在要报并指向码本(tmp_path):
    d = tmp_path / "cgss"; d.mkdir()
    pd.DataFrame({"id": [1], "year": [2021]}).to_stata(d / "c.dta", write_index=False)
    with pytest.raises(KeyError, match="码本"):
        dp.get("cgss", cache=tmp_path, columns=["id", "nope"])


# ─────────────────────────── 宽表不整个转


def test_几千列的文件不整个转只报列数(tmp_path, monkeypatch):
    p = dp.Pack(name="wide", title="W", cite_name="W", level="individual-repeated-cross-section",
                unit="id", time="year", url="https://x/w.csv", codebook="c", homepage="h", citation="c")
    monkeypatch.setitem(dp.PACKS, "wide", p)
    header = ",".join(f"v{i}" for i in range(dp.WIDE + 5))
    res = dp.get("wide", cache=tmp_path, client=_Client({"https://x/w.csv": (header + "\n" + ",".join(["1"] * (dp.WIDE + 5)) + "\n").encode()}))
    assert not res.converted and res.n_columns == dp.WIDE + 5
    assert any("没整个转" in n for n in res.notes)


# ─────────────────────────── 论文得引它


def test_按路径认出数据包(tmp_path):
    (tmp_path / "qog").mkdir()
    f = tmp_path / "qog" / "qog.csv"; f.write_text("a\n")
    assert dp.pack_of(f, tmp_path).name == "qog"
    other = tmp_path / "x.csv"; other.write_text("a\n")
    (tmp_path / "x.csv.meta.json").write_text(json.dumps({"name": "vdem"}))
    assert dp.pack_of(other, tmp_path).name == "vdem"
    assert dp.pack_of(tmp_path / "nothing.csv", tmp_path) is None


# ─────────────────────────── 常用调查的抽样设计登记：每一格要么有出处，要么写待核


def test_调查登记每一格要么有出处要么写待核():
    for name in ("gss", "cgss", "cfps"):
        p = dp.PACKS[name]
        for field in ("design", "strata", "psu"):
            v = getattr(p, field)
            assert v, f"{name}.{field} 空着——查不到就写「待核」"
        claims = [getattr(p, f) for f in ("design", "strata", "psu") if getattr(p, f) != "待核"]
        claims += [v for v in p.weights_by_wave.values() if v != "待核"]
        if claims:
            assert p.design_source.strip(), f"{name} 写了设计信息却没有出处"


def test_data_info报出抽样设计():
    from oss.api import data as data_api
    got = data_api.info("cgss")["data"]
    for k in ("design", "strata", "psu", "weights_by_wave", "design_source"):
        assert k in got
