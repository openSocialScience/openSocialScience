"""验收跑之后按「整篇像不像论文」补的门。全部来自成品 Word 里肉眼可见的问题。"""

from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest

from oss.core.contracts import AnalysisResults, Estimate
from oss.core.manuscript import _englishize, check, render
from tests.test_english_output import _en_paper, _results


# ─────────────────────────── A. 英文稿里不许漏中文诊断


def test_结构固定的中文诊断翻成英文():
    assert _englishize("513 行（9.0%）") == "513 rows (9.0%)"
    assert _englishize("聚类到 country（105 个簇）") == "clustered by country (105 clusters)"
    assert _englishize("country、year") == "country, year"


def test_翻不了的中文诊断在英文稿里被拦():
    res = _results(p=0.01)
    res.diagnostics["共线诊断（VIF）"] = "偏高：_k_far=11——解读这些系数时要谨慎"
    paper = _en_paper("VIF: {{diag:共线诊断（VIF）}}.")
    problems, _ = check(paper, res)
    assert any("中文诊断文本" in p and "英文散文" in p for p in problems)


def test_同样的占位符在中文稿里照常放行():
    res = _results(p=0.01)
    res.diagnostics["共线诊断（VIF）"] = "偏高：_k_far=11——解读这些系数时要谨慎"
    zh = "\n\n".join(f"## {s}\n\n占位。" for s in ("摘要", "引言", "数据与方法", "结果", "讨论"))
    problems, _ = check(zh + "\n\n共线：{{diag:共线诊断（VIF）}}。", res)
    assert not any("中文诊断" in p for p in problems)


def test_英文稿渲染出来不含中文():
    res = _results(p=0.01)
    res.diagnostics["因缺失丢弃"] = "513 行（9.0%）"
    res.diagnostics["标准误"] = "聚类到 country（105 个簇）"
    out = render(_en_paper("Dropped {{diag:因缺失丢弃}}; SE {{diag:标准误}}."), res)
    import re
    assert not re.search(r"[一-鿿]", out), out


# ─────────────────────────── B. 参考文献自带标题


class _Corpus:
    class _R:
        paper_id = "W1"; doi = "10.1/x"; year = 2020; title = "A paper"; authors = ["Ann Lee"]
    records = [_R()]


def test_参考文献列表自带标题_英文():
    out = render(_en_paper("As {{cite:W1}} shows.\n\n{{references}}"), _results(p=0.01), corpus=_Corpus())
    assert "## References" in out


def test_作者已写标题就不重复():
    out = render(_en_paper("As {{cite:W1}} shows.\n\n## References\n\n{{references}}"),
                 _results(p=0.01), corpus=_Corpus())
    assert out.count("## References") == 1


# ─────────────────────────── C. 稳健性按主计划的估计量跑


def test_事件研究的稳健性项也用事件研究跑():
    """验收跑的七项稳健性全跑在了 OLS 下，论文只能承认
    「事件研究路径没有自己的稳健性检验」。"""
    from pathlib import Path
    from oss.core.analysis import run_plan
    from oss.core.contracts import AnalysisPlan

    df = pd.read_csv(Path(__file__).parent / "fixtures" / "did_clean.csv")
    unit, time = "unit", "year"
    plan = AnalysisPlan(
        plan_id="p", idea_id="i", estimator="event_study", outcome="y",
        unit=unit, time=time, treat_time="treat_time", leads=2, lags=2,
        cluster_level=unit, fixed_effects=[unit, time],
        robustness=[{"label": "R1 换聚类", "rationale": "x", "cluster_level": time}],
    )
    res = run_plan(df, plan)
    r1 = res.robustness["R1 换聚类"]
    assert not r1.error, r1.error
    assert "逐年展开" in r1.diagnostics.get("估计量", ""), r1.diagnostics


# ─────────────────────────── E. 产出里有的数不算编造


def test_产出文件里出现过的小数可以照抄():
    paper = _en_paper("Merging dropped 48.6% of rows; the haircut flips the verdict at 0.3.")
    problems, _ = check(paper, _results(p=0.01), known_numbers={"48.6", "0.3"})
    assert not any("裸小数" in p for p in problems), problems


def test_产出里没有的小数仍然拦():
    paper = _en_paper("The effect is 0.032.")
    problems, _ = check(paper, _results(p=0.01), known_numbers={"48.6"})
    assert any("裸小数" in p for p in problems)


# ─────────────────────────── D. 网关超时自动降一档重试一次


def test_网关超时降一档重试_真不可用仍然失败():
    """验收跑 12 个失败点里 9 个是 HTTP 524，执行者每次手动降档重跑。

    降档重试**不是**把「审不了」变成「通过」的通道：只降一档、只试一次，
    降到底还超时就照旧 fail closed。
    """
    from oss.cli.main import _review_with_downgrade
    from oss.host.reviewer import ReviewerUnavailable

    calls: list[str] = []

    class _Rev:
        def review(self, files, level, kind, emphasis):
            calls.append(level)
            if len(calls) == 1:
                raise ReviewerUnavailable("HTTP 524: <html>")
            return {"verdict": "WARN", "level": level}

    v = _review_with_downgrade(_Rev(), [], "deep", "paper", "")
    assert calls == ["deep", "normal"] and v["level"] == "normal"

    class _Dead:
        def review(self, files, level, kind, emphasis):
            raise ReviewerUnavailable("HTTP 524: <html>")

    with pytest.raises(ReviewerUnavailable):
        _review_with_downgrade(_Dead(), [], "deep", "paper", "")

    class _NotGateway:
        def review(self, files, level, kind, emphasis):
            raise ReviewerUnavailable("要审的文件不存在")

    with pytest.raises(ReviewerUnavailable):        # 不是网关问题就不重试
        _review_with_downgrade(_NotGateway(), [], "deep", "paper", "")


# ─────────────────────────── 事件研究的 main 必须是效应，不是第 −5 期


def test_事件研究的main是处理后合并效应():
    """真跑时执行者自己抓到的：`estimates["main"]` 存的是 k=−leads 那一期的系数，
    `{{robust:…/main.coef}}` 和森林图引的全是处理前趋势。"""
    from pathlib import Path
    from oss.core.analysis import run_plan
    from oss.core.contracts import AnalysisPlan

    df = pd.read_csv(Path(__file__).parent / "fixtures" / "did_clean.csv")
    plan = AnalysisPlan(plan_id="p", idea_id="i", estimator="event_study", outcome="y",
                        unit="unit", time="year", treat_time="treat_time", leads=3, lags=3,
                        cluster_level="unit", fixed_effects=["unit", "year"])
    res = run_plan(df, plan)
    m = res.estimates["main"]
    assert "post" in m.term and "合并" in m.term
    post = [p for p in res.event_study if p.k >= 0]
    assert post, "要有处理后各期"
    avg = np.mean([p.coef for p in post])
    assert abs(m.coef - avg) < 0.5 * max(abs(avg), 0.05) or abs(m.coef - avg) < 0.1, (m.coef, avg)
    assert "_km3" in res.estimates, "第 −3 期系数保留在自己名下"


def test_已完成的运行目录不许再当新研究用(tmp_path):
    from oss.cli.main import _finished_run
    assert not _finished_run(tmp_path)
    (tmp_path / "paper.md").write_text("x", encoding="utf-8")
    assert _finished_run(tmp_path)


def test_审稿投影只挑字段不改数值():
    from oss.cli.main import _results_digest
    raw = {"plan_id": "p", "n": 10, "estimates": {"main": {"coef": 1}},
           "robustness": {"R1": {"estimates": {"main": {"coef": 2}, "_kp1": {"coef": 9}},
                                 "n": 5, "error": None, "diagnostics": {"a": "b"}}}}
    d = _results_digest(raw)
    assert d["estimates"] == raw["estimates"]
    assert d["robustness"]["R1"]["estimates"] == {"main": {"coef": 2}}
    assert "_kp1" not in json.dumps(d["robustness"])
