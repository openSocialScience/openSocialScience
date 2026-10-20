"""主张-结果一致性核验：堵住「数字真、话是编的」。

**这是回归验证之后发现的最大的洞。**

占位符机制保证了正文里的数字来自 `results.json`，**但一个字都没管句子说了什么**。
v2 论文里我写了「在最晚队列上它不是减弱，而是已经消失」——
这句话来自诊断图，不来自任何占位符；我要是写成相反的意思，
`oss paper check` 照样一次通过。

**判据必须是机械可判的（Type-A）**，不需要跨模型：

  {{...p}} 渲染成 0.42，句子里却说「显著」        → 拒绝
  {{...coef}} 是正的，句子里却说「下降」          → 警告（中文里「减弱 -0.8」也说得通）
  功效判 fail/gray，局限里一个字没提功效          → 拒绝
  共线诊断报「严重」，全文没提共线                → 拒绝
  判词脆弱，正文没报敏感性分析                    → 警告
  结果句里出现「证明」                            → 警告（显著不等于证明）

**拒绝与警告要分开。**中文的方向词用法有歧义，一律拒绝会逼得人没法写；
而「p=0.42 说显著」没有任何歧义，必须硬拦。
"""

from __future__ import annotations

import pytest

from oss.core.contracts import AnalysisResults, Estimate, PowerReport, RobustnessRun
from oss.core.manuscript import check


def results(**kw) -> AnalysisResults:
    base = dict(
        plan_id="p", n=1000,
        estimates={
            "main": Estimate(term="x", coef=0.188, se=0.027, p=0.0000001),
            "weak": Estimate(term="z", coef=-0.05, se=0.06, p=0.42),
            "neg": Estimate(term="w", coef=-0.785, se=0.053, p=0.0000001),
        },
        diagnostics={"组内 R²": "0.36"},
    )
    return AnalysisResults(**{**base, **kw})


def power(**kw) -> PowerReport:
    base = dict(idea_id="i", power=0.77, verdict="gray", gray_band=(0.4, 0.8),
                discipline="sociology", sensitivity="折价：会翻盘", verdict_fragile=True)
    return PowerReport(**{**base, **kw})


def paper(body: str, limits: str = "功效判词见上，样本量撑得住。") -> str:
    return (f"# 标题\n## 摘要\n概要。\n## 引言\n背景。\n## 数据与方法\n方法。\n"
            f"## 结果\n{body}\n## 讨论\n### 局限\n{limits}\n")


# ══════════════════════════════════ 硬拦：显著性说反了


def test_p值不显著却说显著要拒绝() -> None:
    """**这条没有任何歧义，必须硬拦。**"""
    problems, _ = check(paper("该效应显著为负（p {{result:weak.p}}）。"), results())
    assert any("显著" in p and "weak.p" in p for p in problems), problems


def test_p值极小说不显著也要拒绝() -> None:
    problems, _ = check(paper("该效应并不显著（p {{result:main.p}}）。"), results())
    assert any("不显著" in p for p in problems), problems


def test_p值不显著且如实说不显著要放行() -> None:
    problems, _ = check(paper("该效应不显著（p {{result:weak.p}}）。"), results())
    assert not problems, problems


def test_稳健性的p值也要核() -> None:
    res = results()
    res.robustness = {"R1": RobustnessRun(
        label="R1", n=900,
        estimates={"main": Estimate(term="x", coef=0.01, se=0.2, p=0.96)})}
    problems, _ = check(
        paper("稳健性同样显著（p {{robust:R1/main.p}}）。"), res)
    assert any("显著" in p for p in problems), problems


# ══════════════════════════════════ 硬拦：该写没写


def test_功效判gray而局限没提功效要拒绝() -> None:
    problems, _ = check(
        paper("主效应为 {{result:main.coef}}。", limits="识别策略有边界。"),
        results(), power())
    assert any("功效" in p for p in problems), problems


def test_功效判pass就不强制写() -> None:
    problems, _ = check(
        paper("主效应为 {{result:main.coef}}。", limits="识别策略有边界。"),
        results(), power(verdict="pass", verdict_fragile=False))
    assert not any("功效" in p for p in problems), problems


def test_共线报严重而全文没提要拒绝() -> None:
    res = results(diagnostics={"共线诊断（VIF）": "**严重**：age=4535、cohort=4535"})
    problems, _ = check(paper("主效应为 {{result:main.coef}}。"), res)
    assert any("共线" in p for p in problems), problems


def test_共线正常就不强制提() -> None:
    res = results(diagnostics={"共线诊断（VIF）": "最大 VIF 2.1（age），无共线问题"})
    problems, _ = check(paper("主效应为 {{result:main.coef}}。"), res)
    assert not any("共线" in p for p in problems), problems


# ══════════════════════════════════ 警告：有歧义的


def test_系数为正却说下降要警告() -> None:
    _, warns = check(paper("出身效应随之下降 {{result:main.coef}}。"), results())
    assert any("符号" in w or "方向" in w for w in warns), warns


def test_系数为负说减弱不该报警() -> None:
    """**中文里「减弱 -0.785」是通顺的**——一律拒绝会逼得人没法写。"""
    _, warns = check(paper("获得学历使出身效应减弱 {{result:neg.coef}} 个单位。"), results())
    assert not any("符号" in w or "方向" in w for w in warns), warns


def test_判词脆弱却没报敏感性要警告() -> None:
    _, warns = check(paper("主效应 {{result:main.coef}}。"), results(), power())
    assert any("敏感" in w for w in warns), warns


def test_结果句说证明要警告() -> None:
    _, warns = check(paper("这证明了教育扩张的作用 {{result:main.coef}}。"), results())
    assert any("证明" in w for w in warns), warns


# ══════════════════════════════════ 别误伤


def test_合规的稿子不该被拦() -> None:
    text = paper(
        "主效应为 {{result:main.coef}}（p {{result:main.p}}），"
        "对照项不显著（p {{result:weak.p}}）。",
        limits="事前功效落在灰区，敏感性分析显示判词对折价系数敏感。识别策略有边界。",
    )
    problems, warns = check(text, results(), power())
    assert not problems, problems
    assert not [w for w in warns if "符号" in w or "敏感" in w], warns


# ══════════════════════════════ 已知边界：这个检查器抓不到什么

def test_已知漏洞_方向词描述派生量时抓不到() -> None:
    """**把边界写成测试，免得有人以为这个检查是完备的。**

    「三重交互项为 +0.188，说明均衡作用在持续增强」——这句话是**错的**：
    三重交互为正意味着均衡作用在**减弱**，因为它抵消的是一个负的二阶项。

    但机械规则看不出这一层：方向词「增强」描述的是「均衡作用」这个**派生量**，
    不是系数本身；而系数确实是正的，符号与方向词「一致」。

    **要抓这一类只能靠跨模型审稿**（第十章第二件）。
    这条测试断言的是「现在抓不到」——等审稿那一步做完，
    这里应当改成断言「抓到了」。
    """
    text = paper(
        "三重交互项为 {{result:main.coef}}，说明均衡作用在持续增强。",
        limits="事前功效落在灰区，敏感性分析已报。",
    )
    problems, warns = check(text, results(), power())
    sign = [x for x in problems + warns if "符号" in x or "方向" in x]
    assert not sign, (
        "如果这条开始失败，说明有人扩展了规则、能抓派生量了——"
        f"那是好事，把这条测试改成断言抓到：{sign}"
    )


def test_已知边界_一句话里同类结果多于一个时只看分句() -> None:
    """切窄的代价：跨分句的错会漏。

    「该效应在各规格中均下降，主系数为 +0.188，对照为 +0.02」——
    因为这句话里有两个 `.coef`，上下文切到分句，「下降」就看不见了。

    **两害相权取精确**：假警报会让人直接忽略这个检查，
    而漏掉的这类还有跨模型审稿兜底。
    """
    text = paper("该效应在各规格中均下降，主系数为 {{result:main.coef}}，"
                 "对照为 {{result:neg.coef}}。")
    _, warns = check(text, results())
    assert not [w for w in warns if "符号" in w], warns


# ═══════════ 最小可探测效应不许当排除下界

def test_MDE当成排除下界要警告() -> None:
    """**有固定形状的谬误：**MDE 是 0.008 → 没测出来 → 所以能排除 0.008 以上。

    错在最后一步。MDE 的意思是「若真值有 0.008，有八成把握测出来」；
    没测出来只说明「要么没有，要么运气不好」——那个值本身还在置信区间里。

    十一轮 CGSS 那次真跑，这句话在摘要、结果、结论里各出现一次，
    `oss paper check` 一路放行（它当时只有「p 说反」「符号说反」「用了证明」三条）。
    跨模型审稿抓到了，还独立走到了原论文作者内部评审的同一个结论。
    """
    from oss.core.manuscript import mde_misuse

    assert mde_misuse("最小可探测效应为 0.008，即本样本能排除每年超过这一幅度的收敛。")
    assert mde_misuse("本样本能排除快于每年 {{result:main.mde}} 的收敛。")
    assert mde_misuse("Given the MDE, the sample rules out convergence faster than that.")


def test_不能排除是正确用法_不许拦() -> None:
    """把「不能排除」也拦下来，等于逼人把对的话改成错的。"""
    from oss.core.manuscript import mde_misuse

    assert not mde_misuse("最小可探测效应为 0.008，因此不能排除一个更慢的移动。")
    assert not mde_misuse("A larger effect cannot be ruled out given the MDE of 0.08.")
    assert not mde_misuse("最小可探测效应为 0.008，只能排除更大的效应。")


def test_排除某一轮样本不算这个谬误() -> None:
    """「排除 2021 年那一轮」是正当的样本限制，跟排除效应是两回事。"""
    from oss.core.manuscript import mde_misuse

    assert not mde_misuse("稳健性中剔除了 2021 年那一轮，最小可探测效应为 0.008。")
    assert not mde_misuse("排除 2021 年之后，最小可探测效应升到 0.010。")


def test_没提MDE的句子不查这条() -> None:
    from oss.core.manuscript import mde_misuse

    assert not mde_misuse("本文的设计能排除随时间变化的遗漏变量带来的影响。")


def test_否定词隔着两个词也认得出() -> None:
    """`cannot be ruled out` 的否定词离「ruled out」隔着两个词。
    回溯窗口窄了就把 `nnot be ` 当成没有否定——三篇英文稿子上真误报过。"""
    from oss.core.manuscript import mde_misuse

    assert not mde_misuse("Effects below the MDE cannot be fully ruled out by this design.")
    # 「can rule out only …」是同一个推理的克制版本：**照样警告**（所以这条规则只警告不拒绝）
    assert mde_misuse("The design can rule out only a large decline (larger than the MDE).")


# ═══════════ 三次自由跑抓到的两处误报

def test_英文分句不查裸引用() -> None:
    """这条判据的依据是「中文句子里冒出一个大写拉丁词，多半是个姓」。
    英文里每句话开头都是大写词，依据就没了。

    真跑一次英文稿量到 **42 条误报**：`For`、`That`、`American`、`Using`……
    全是句首单词，而那一篇一处裸引用都没有。中文稿里那段**英文摘要**犯同一个错
    （`Using`、`Chinese`、`General`、`Survey` 各报一条），所以判据下沉到分句。
    """
    from oss.core.manuscript import _bare_citation_warnings

    en = "Using the Chinese General Social Survey, we show that hours rose."
    assert _bare_citation_warnings(en, None) == []
    zh = "Blau 与 Duncan 提出的地位获得模型至今仍是基准。"
    assert len(_bare_citation_warnings(zh, None)) >= 1, "中文句里照旧要查"


def test_交互项不查符号与措辞() -> None:
    """交互项的符号说的是「这个关系怎么变」，而句子里几乎总要同时点出
    那个关系本身的方向——两者是两码事，凑在一起必然误判。

    三次自由跑一次量到 5 条误报，**全部是交互项**：

      ·「方向不是向上转移，而是差距进一步扩大」——系数为负，句里有「向上」
      ·「受教育年限与过劳的负向关联随时间显著变平（+0.010）」——系数为正，句里有「负向」

    后一句还是对的：负向关联变平，交互项就该是正的。**判据看不出这层，那就别判。**
    """
    from oss.core.contracts import AnalysisResults, Estimate
    from oss.core.manuscript import check_claims

    res = AnalysisResults(plan_id="p", n=100, estimates={
        "main": Estimate(term="x", coef=-0.5, se=0.1, p=0.001),
        "a*b": Estimate(term="a*b", coef=-0.069, se=0.027, p=0.010)})

    _, w = check_claims("交互项为 {{result:a*b.coef}}：方向不是向上转移，而是差距扩大。", res)
    assert not [x for x in w if "符号与措辞" in x], w

    _, w = check_claims("主效应为 {{result:main.coef}}，说明它在上升。", res)
    assert [x for x in w if "符号与措辞" in x], "非交互项照旧要查"
