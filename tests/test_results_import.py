"""外部结果导入：实验做完了、Stata 也跑完了，就差写论文。

最常见的真实用户就是这种：不缺想法、不缺数据、不缺结果，**缺的正是把它写成
一篇规范论文**——而那是这套东西最强的一段。可现在系统只会说「把原始数据给我，
我要自己跑一遍」，他给不了就卡死。

三条纪律：

1. **原文必须存。**中间隔着一道「模型把表转成结构化数据」的手工活，
   小数点看错一位整篇论文就全错且不报错。
2. **软件名照用户原话，说多少记多少。**他说「Stata」就写 Stata，不许补成
   「Stata 18」——补全就是编造。没说就一个字不写。
3. **标记只在文件里，不进论文。**论文里出现关于本系统的字是过程泄漏，
   `no-process-language` 那道门会拦。留标记是为了让那几道查「有没有真跑过」的
   规则能明说「不适用」，而不是判绿。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss.api.errors import BadInput
from oss.api.results_import import import_results
from oss.core.contracts import AnalysisResults, ExternalImport

RAW = """                    Coef.   Std. Err.      t    P>|t|
    treat          .0321      .0114      2.82   0.004
    N = 1840
"""

PAYLOAD = {
    "plan_id": "p001",
    "n": 1840,
    "estimates": {"main": {"term": "treat", "coef": 0.0321, "se": 0.0114, "p": 0.004}},
    "spec": {"estimator": "ols_fe", "outcome": "y", "treatment": "treat",
             "fixed_effects": ["firm", "year"], "cluster_level": "firm"},
}


def _run(tmp_path: Path) -> Path:
    d = tmp_path / "r"
    d.mkdir()
    (d / "payload.json").write_text(json.dumps(PAYLOAD), encoding="utf-8")
    (d / "paste.txt").write_text(RAW, encoding="utf-8")
    return d


def test_导入之后结果文件在且带外部标记(tmp_path: Path) -> None:
    d = _run(tmp_path)
    res = import_results(str(d), payload=str(d / "payload.json"),
                         raw=str(d / "paste.txt"), software="Stata")
    got = AnalysisResults.load(d / "results.json")
    assert got.estimates["main"].coef == 0.0321 and got.n == 1840
    assert got.imported is not None
    assert got.imported.software == "Stata"
    assert res["data"]["external"] is True


def test_原文原样存进运行目录(tmp_path: Path) -> None:
    """**任何一个数都要能翻回去对。**"""
    d = _run(tmp_path)
    import_results(str(d), payload=str(d / "payload.json"), raw=str(d / "paste.txt"))
    got = AnalysisResults.load(d / "results.json")
    stored = d / got.imported.raw_file
    assert stored.exists()
    assert stored.read_text(encoding="utf-8") == RAW, "原文一个字节都不许改"


def test_没说软件就空着(tmp_path: Path) -> None:
    """**没说就不写。**模型最爱「帮你补全」，而补全就是编造。"""
    d = _run(tmp_path)
    import_results(str(d), payload=str(d / "payload.json"), raw=str(d / "paste.txt"))
    assert AnalysisResults.load(d / "results.json").imported.software == ""


def test_不给原文直接拒(tmp_path: Path) -> None:
    d = _run(tmp_path)
    with pytest.raises(BadInput) as exc:
        import_results(str(d), payload=str(d / "payload.json"), raw="")
    assert "原文" in str(exc.value)


def test_原文是空的也拒(tmp_path: Path) -> None:
    d = _run(tmp_path)
    (d / "empty.txt").write_text("   \n", encoding="utf-8")
    with pytest.raises(BadInput):
        import_results(str(d), payload=str(d / "payload.json"), raw=str(d / "empty.txt"))


def test_没有主估计要拒(tmp_path: Path) -> None:
    """`main` 是论文里占位符的锚，缺了后面全串。"""
    d = _run(tmp_path)
    bad = dict(PAYLOAD, estimates={"other": PAYLOAD["estimates"]["main"]})
    (d / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(BadInput) as exc:
        import_results(str(d), payload=str(d / "bad.json"), raw=str(d / "paste.txt"))
    assert "main" in str(exc.value)


def test_留痕不写成results这一步(tmp_path: Path) -> None:
    """**不许伪装成跑过。**留痕里的 step 是 results_import，不是 results——
    「功效要在分析之前算」那几条查的是 results，写成 results 等于骗过了它们。"""
    d = _run(tmp_path)
    import_results(str(d), payload=str(d / "payload.json"), raw=str(d / "paste.txt"))
    steps = [json.loads(l)["step"] for l in (d / "trace.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert "results_import" in steps and "results" not in steps


def test_软件名进表注_没说就不写() -> None:
    """**正常的方法交代**：任何论文都写「估计在 X 中完成」。
    但没说就不写——补一个版本号出来是编造。
    关于本系统的话一个字都不许有，那是过程泄漏。"""
    from oss.core.contracts import Estimate, SpecSummary
    from oss.core.manuscript import table_markdown

    def _res(software: str | None):
        r = AnalysisResults(
            plan_id="p", n=1840,
            spec=SpecSummary(estimator="ols_fe", outcome="y", cluster_level="firm"),
            estimates={"main": Estimate(term="treat", coef=0.032, se=0.011, p=0.004)})
        if software is not None:
            r.imported = ExternalImport(raw_file="results_raw.txt", software=software)
        return r

    body, _ = table_markdown("main", _res("Stata"), en=False)
    assert "估计在 Stata 中完成。" in body, body
    assert "Stata 18" not in body

    body, _ = table_markdown("main", _res(""), en=False)
    assert "中完成" not in body, "没说软件就一个字不写"

    body, _ = table_markdown("main", _res(None), en=False)
    assert "中完成" not in body, "本链自己跑的更不该写"

    body, _ = table_markdown("main", _res("R"), en=True)
    assert "Estimates produced in R." in body, body
    for bad in ("system", "OpenSocialScience", "imported", "external"):
        assert bad.lower() not in body.lower(), f"论文里不许出现关于本系统的字：{bad}"


def test_外部导入时那两条规则明说不适用(tmp_path: Path) -> None:
    """**不能顺着「还没跑分析」判绿。**报告绿着，其实什么都没查。"""
    from oss.core import skillcheck as sc

    d = _run(tmp_path)
    import_results(str(d), payload=str(d / "payload.json"), raw=str(d / "paste.txt"))
    rep = sc.evaluate(d)
    for rid in ("power-before-run", "route-after-run"):
        it = next(i for i in rep.items if i.id == rid)
        assert it.status == sc.NA and "外部导入" in it.detail, (rid, it.detail)
