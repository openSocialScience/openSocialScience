"""假实现。按 contracts.py 的格式造数据，字段齐全但数值是编的。

**存在的理由**：先让整条链跑通，再逐段换真的（PLAN.md 纪律 ①）。
接口对不上的问题在这个阶段暴露最便宜——改的是空壳，不是已经写好的实现。

**假数据一眼要能认出来**（PLAN.md 纪律 ④）：变量名带 FAKE_ 前缀、
DOI 用 10.0000/fake.*。这样「这一步换成真的了没有」不用翻代码就知道。
"""

from __future__ import annotations

from .contracts import (
    AnalysisPlan,
    AnalysisResults,
    BindingReport,
    DataProfile,
    DataStructure,
    Estimand,
    Estimate,
    FileInfo,
    Idea,
    IdeaSet,
    LiteratureCorpus,
    PaperRecord,
    PowerReport,
    VariableBinding,
    VariableProfile,
)

FAKE_PREFIX = "FAKE_"
FAKE_DOI = "10.0000/fake"


def profile(path: str) -> DataProfile:
    variables = [
        VariableProfile(
            name=f"{FAKE_PREFIX}var_{i:02d}",
            label=f"假变量 {i}",
            dtype="float64",
            semantic_type="continuous" if i > 2 else "identifier",
            n_missing=i,
            missing_rate=round(i / 100, 3),
            n_unique=100 - i,
            within_variation=0.0 if i == 3 else 0.42,
        )
        for i in range(1, 11)
    ]
    return DataProfile(
        profile_id=f"{FAKE_PREFIX}prof_001",
        file=FileInfo(path=path, format="csv", n_rows=100, n_cols=10, sha256=f"{FAKE_PREFIX}sha"),
        variables=variables,
        structure=DataStructure(
            kind="panel_unbalanced",
            unit_id=f"{FAKE_PREFIX}var_01",
            time_id=f"{FAKE_PREFIX}var_02",
            n_units=25,
            n_periods=4,
            balance_ratio=0.92,
            nesting_levels=[f"{FAKE_PREFIX}个体", f"{FAKE_PREFIX}地区"],
            detection_note="假画像：阶段 1 换成真的",
        ),
        warnings=[f"{FAKE_PREFIX}这是假画像，没有读取真实文件"],
    )


def literature(queries: list[str]) -> LiteratureCorpus:
    records = [
        PaperRecord(
            paper_id=f"{FAKE_PREFIX}paper_{i:03d}",
            title=f"{FAKE_PREFIX}假文献标题 {i}",
            year=2015 + i % 8,
            authors=[f"{FAKE_PREFIX}Author{i}"],
            doi=f"{FAKE_DOI}.{i:03d}",
            abstract=f"{FAKE_PREFIX}假摘要 {i}。真实检索在阶段 2 接入。",
            citation_count=10 * i,
            source="fake",
        )
        for i in range(1, 6)
    ]
    return LiteratureCorpus(
        corpus_id=f"{FAKE_PREFIX}corpus_001",
        queries=queries,
        records=records,
        hits_by_source={"fake": len(records)},
        abstract_coverage=1.0,
        citation_coverage=1.0,
        relaxed=False,
        notes=[f"{FAKE_PREFIX}假语料，未调用任何文献 API"],
    )


def ideas(direction: str) -> IdeaSet:
    """注意：真实链路里 ideas.json 由 skill 写，不由工具箱写。

    这里造假只是为了让 `oss smoke` 能在不启动 Claude Code 的情况下跑通全链。
    阶段 3 之后，smoke 仍用这个假实现，真实链路走 skill。
    """
    return IdeaSet(
        direction=direction,
        ideas=[
            Idea(
                idea_id=f"{FAKE_PREFIX}idea_{i:03d}",
                title=f"{FAKE_PREFIX}假 idea {i}",
                research_question=f"{FAKE_PREFIX}假研究问题 {i}？",
                hypothesis=f"{FAKE_PREFIX}假假设 {i}（有方向性）",
                mechanism=f"{FAKE_PREFIX}假机制 {i}",
                identification_strategy="ols_fe",
                constructs_needed=[f"{FAKE_PREFIX}构念A", f"{FAKE_PREFIX}构念B"],
                contribution_type="new-mechanism",
                theory_hook=f"{FAKE_PREFIX}假理论对话 {i}",
                value_if_null=f"{FAKE_PREFIX}结果为空时仍成立，因为……",
                lens=f"{FAKE_PREFIX}lens_{i}",
                research_type="causal",
            )
            for i in range(1, 4)
        ],
    )


def binding(idea_id: str) -> BindingReport:
    return BindingReport(
        idea_id=idea_id,
        bindings=[
            VariableBinding(
                construct_name=f"{FAKE_PREFIX}构念A",
                role="treatment",
                column=f"{FAKE_PREFIX}var_04",
                status="bound",
                coverage=0.96,
                note=f"{FAKE_PREFIX}假绑定",
            ),
            VariableBinding(
                construct_name=f"{FAKE_PREFIX}构念B",
                role="outcome",
                column=f"{FAKE_PREFIX}var_05",
                status="bound",
                coverage=0.92,
                note=f"{FAKE_PREFIX}假绑定",
            ),
        ],
        core_ok=True,
        blocking=[],
    )


def power(idea_id: str, effect: float, n: int) -> PowerReport:
    """假功效。数值编的，但 verdict 必须由真的 verdict_for 推出来。"""
    from oss.core.power import analyze

    rep = analyze(idea_id, effect, n, discipline="economics", n_clusters=max(n // 4, 1))
    rep.reason = f"{FAKE_PREFIX}" + rep.reason
    return rep


def _verdict(value: float, band: tuple[float, float]) -> str:
    """两头定死、中间跨模型（DECISIONS.md 6.2）。

    真实现也要用这个函数——判词的推导规则只该有一处。
    """
    low, high = band
    if value < low:
        return "fail"
    if value > high:
        return "pass"
    return "gray"


def plan(idea_id: str) -> AnalysisPlan:
    """真实链路里 plan.json 由 skill 写。这里同 ideas()，只为 smoke。"""
    return AnalysisPlan(
        plan_id=f"{FAKE_PREFIX}plan_001",
        idea_id=idea_id,
        research_type="causal",
        estimator="ols_fe",
        outcome=f"{FAKE_PREFIX}var_05",
        treatment=f"{FAKE_PREFIX}var_04",
        controls=[f"{FAKE_PREFIX}var_06"],
        fixed_effects=[f"{FAKE_PREFIX}var_01", f"{FAKE_PREFIX}var_02"],
        cluster_level=f"{FAKE_PREFIX}var_01",
        sample_filter="",
        robustness=[f"{FAKE_PREFIX}换聚类层级"],
        estimand=Estimand(
            unit_quantity=f"{FAKE_PREFIX}同一单位处理与否时结果之差",
            target_population=f"{FAKE_PREFIX}样本所代表的总体",
            assumptions=[f"{FAKE_PREFIX}给定控制变量与固定效应后无未观测混淆"],
            link=f"{FAKE_PREFIX}双向固定效应下的系数对上平均处理效应",
        ),
        control_roles={f"{FAKE_PREFIX}var_06": "confounder"},
    )


def results(plan_id: str) -> AnalysisResults:
    return AnalysisResults(
        plan_id=plan_id,
        estimates={
            "main": Estimate(term=f"{FAKE_PREFIX}var_04", coef=0.032, se=0.011, p=0.043),
        },
        n=100,
        diagnostics={f"{FAKE_PREFIX}诊断": "假的，阶段 5 换成真的"},
        log=f"{FAKE_PREFIX}未执行任何回归",
    )


def paper(direction: str, idea_title: str) -> str:
    """真实链路里 paper.md 由 skill 写。这里同上，只为 smoke。

    注意正文里的数字用占位符而不是裸数字——这个约定从假实现就开始守，
    免得阶段 6 换真的时候才发现 skill 一直在手打数字。
    """
    return f"""# {idea_title}

## 摘要

{FAKE_PREFIX}这是一篇假论文。研究方向是「{direction}」。
本文的估计结果为 {{{{result:main.coef}}}}（标准误 {{{{result:main.se}}}}）。

## 引言

{FAKE_PREFIX}假引言。阶段 6 由 skill 写真的。

## 数据与方法

{FAKE_PREFIX}假的数据与方法。样本量 {{{{result:main.n}}}}。

## 结果

{FAKE_PREFIX}假结果。主效应 {{{{result:main.coef}}}}，p = {{{{result:main.p}}}}。

## 讨论

{FAKE_PREFIX}假讨论。

---
*由 OpenSocialScience 阶段 0 骨架生成——全部内容为假数据。*
"""
