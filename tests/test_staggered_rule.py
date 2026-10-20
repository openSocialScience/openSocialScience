"""分批实施要用 `did_cs` —— **判据是诊断里的批次数，不是计划里写的估计量名字。**

合规规则原先按名字判：不是 `did` / `event_study` 就直接 n/a。于是
**`ols_fe` + 一个 post 哑变量 + 单位与时间双向固定效应**这条路整道绕过——
它算的跟 `did` 一模一样，坑也一模一样（考卷 A 实测主系数 2.129，
真实平均处理效应 3.56，偏了四成）。

计划那一侧早就不认名字了（`_twfe_staggered_cohorts` 专判这条路，批次数也写进了
`diagnostics["实施批次"]`），**只有合规规则没跟上**——跟「ols_fe 加 post 绕过分批门」
是同一个形状：换个写法就全放行。

顺带钉住「空复查也要留痕」：「复查过、没有处方」与「压根没复查」在 `route.json` 上
长得一模一样（都是 `items: []`），只有留痕分得开。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss.core.skillcheck import NA, PASS, r_staggered_estimator


class _View:
    def __init__(self, plan: dict, diagnostics: dict | None = None) -> None:
        self.dir = Path("/tmp/does-not-exist")
        self.plan = plan
        self.results = {"diagnostics": diagnostics or {}}


@pytest.mark.parametrize("est,label", [("ols_fe", "双向固定效应"), ("did", "TWFE")])
def test_分批却没用did_cs一律拦(est: str, label: str) -> None:
    """**`ols_fe` 这一路是原先漏掉的那一半。**"""
    status, detail = r_staggered_estimator(_View({"estimator": est}, {"实施批次": "4 批"}))
    assert status == "fail"
    assert "4 批" in detail and est in detail


def test_did_cs放行() -> None:
    assert r_staggered_estimator(_View({"estimator": "did_cs"}, {"实施批次": "4 批"}))[0] == PASS


def test_单批放行() -> None:
    assert r_staggered_estimator(_View({"estimator": "ols_fe"}, {"实施批次": "1 批"}))[0] == PASS


def test_算不出批次数就不判_但不能顺着判通过() -> None:
    """**判不了就不判。**横截面的 `ols_fe` 谈不上分批 → n/a；
    而面板设计（写了 treat_time 或用的是 did 族）算不出批次数时判 pass 并说明白，
    不含糊其辞。"""
    assert r_staggered_estimator(_View({"estimator": "ols_fe"}, {}))[0] == NA
    assert r_staggered_estimator(_View({"estimator": "iv"}, {}))[0] == NA
    s, d = r_staggered_estimator(_View({"estimator": "did"}, {}))
    assert s == PASS and "没有批次数" in d


def test_空复查也要留痕(tmp_path: Path) -> None:
    """「复查过、没有处方」与「压根没复查」在 `route.json` 上长得一模一样
    （都是 `items: []`）。只有留痕分得开——否则那条 must 规则永远要靠手工补才能过，
    **测的就成了抄写员而不是研究者**。"""
    from oss.api import route as route_api

    run = tmp_path / "r"
    run.mkdir()
    (run / "plan.json").write_text(json.dumps(
        {"plan_id": "p", "idea_id": "i", "estimator": "ols_fe",
         "outcome": "y", "treatment": "x"}), encoding="utf-8")
    route_api.status(str(run))
    rows = [json.loads(l) for l in (run / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    hits = [r for r in rows if r.get("command") == "oss route"]
    assert hits, "空复查没留痕"
    assert (hits[0].get("args") or {}).get("n_items") == 0
    assert "没有处方" in (hits[0].get("note") or "")
