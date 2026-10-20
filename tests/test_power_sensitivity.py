"""敏感性分析：结论到底是关于数据的，还是关于我拍的那个参数的。

**跨模型审稿的原话**（`docs/runs/gss20260824_v2-review-power.json`）：

> `haircut=0.6` 与 `gray_band=[0.4,0.8]` 均无出处或敏感性分析；
> **改动这些任意参数即可改变 verdict。**

这条指控只能用**一张可核对的表**回应，辩解没有用。

跟稳健性检验的区别：

============  ==========================  ==============================
稳健性检验     换模型/样本/测量（数据里有的）  这个发现是不是只在某个规格下成立
敏感性分析     换一个拍脑袋定的参数（数据里没有）  这个结论是关于世界还是关于我的假定
============  ==========================  ==============================

**只报会翻盘的参数。**不敏感的参数刷出来就是噪声——
跟 VIF 对交互项误报是同一个道理：警报天天响就等于没有警报。
"""

from __future__ import annotations

import pytest

from oss.core.power import analyze


def rep(**kw):
    #: 夹具一律带效应量出处。这一组测的是**判词对假定参数敏不敏感**，
    #: 而无出处会把 pass 直接压成 gray（见 test_power_binding 的出处那一组），
    #: 不带出处的话每条断言都被那条规则盖住，测不到本来要测的东西。
    base = dict(
        idea_id="i1", effect_size=0.05, n=49891, discipline="sociology",
        n_clusters=140, icc=0.02, treatment_icc=0.077,
        effect_size_source="夹具：假想文献 X (2020) 表 3",
    )
    return analyze(**{**base, **kw})


def test_会翻盘的参数要报出来并给出翻盘点() -> None:
    """本次运行的实测：折价 0.6 判 gray，0.7 就翻成 pass，0.3 翻成 fail。
    **离翻盘只差 0.1**——这一点必须让读者看见。
    """
    r = rep()
    assert r.verdict == "gray"
    assert r.verdict_fragile, "判词对某个假定参数敏感，就该标出来"
    assert "haircut" in r.sensitivity_table
    text = r.sensitivity
    assert "折价" in text or "haircut" in text, text
    assert "pass" in text.lower() or "通过" in text, "要说清翻成什么"


def test_不敏感的参数不该刷屏() -> None:
    """五个学科的灰区上下界全都判 gray——这个结论对上下界不敏感。

    **不敏感就说不敏感，别把它也报成警告。**
    """
    r = rep()
    assert "灰区" in r.sensitivity or "band" in r.sensitivity
    # 但它不该出现在「会翻盘」的清单里
    fragile = {p for p, rows in r.sensitivity_table.items()
               if len({v for _, _, v in rows}) > 1}
    assert "gray_band" not in fragile


def test_敏感性表可以逐格核对() -> None:
    """表里每一格是 (参数取值, 功效, 判词)，**读者能自己复算**。"""
    rows = rep().sensitivity_table["haircut"]
    assert len(rows) >= 5
    vals = [v for v, _, _ in rows]
    assert vals == sorted(vals)
    got = {round(v, 1): verd for v, _, verd in rows}
    assert got[0.3] == "fail" and got[0.6] == "gray" and got[0.8] == "pass"


def test_稳健的判词不该被标成脆弱() -> None:
    """效应量很大时，怎么调参数都是 pass——那就不该报警。"""
    r = rep(effect_size=0.6)
    assert r.verdict == "pass"
    assert not r.verdict_fragile, f"判词很稳还报脆弱：{r.sensitivity}"


def test_敏感性结论能被论文引用() -> None:
    """写不进论文的诊断等于没做。占位符要能解析到它。"""
    from oss.core.contracts import AnalysisResults, Estimate
    from oss.core.manuscript import check, render

    res = AnalysisResults(
        plan_id="p", n=100,
        estimates={"main": Estimate(term="x", coef=1.0, se=0.1, p=0.001)},
    )
    # 讨论里必须提功效——判词落在灰区时这是硬要求（见 test_claim_check.py）。
    # 这条测试原先没写，被新加的主张一致性核验当场拦下了。
    text = ("# T\n## 摘要\na\n## 引言\nb\n## 数据与方法\nc\n## 结果\n"
            "{{result:main.coef}}\n## 讨论\n事前功效落在灰区。"
            "敏感性：{{power:sensitivity}}")
    problems, _ = check(text, res, rep())
    assert not problems, problems
    assert "折价" in render(text, res, rep())


def test_折价系数要有文献依据() -> None:
    """**「无出处」这条指控要靠给出处来回应，不是靠删掉这个参数。**"""
    from oss.core import power

    doc = power.__doc__ or ""
    src = doc + (power.EFFECT_SIZE_HAIRCUT.__doc__ or "") + power.HAIRCUT_BASIS
    assert "复制" in src or "replication" in src.lower(), "折价系数要说清依据哪来的"
    assert "选择" in power.HAIRCUT_BASIS, "同时要明说它仍然是一个选择而非事实"
