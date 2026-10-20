"""调查层的灰区初值与处方判据（调查层设计稿第七、八节）。**初值先实现、再校准、再请用户定**。

2026-09-15 校准（表在实施计划末尾的执行记录）：合成数据门槛 2 误拦 2%、检出 100%；CGSS 2017 真跑两版之差 0.95–1.01 个标准误，不开；
抽样单位 12 个开处方，36 个、139 个不开。用户回「按建议」：两个都保持。
"""
from __future__ import annotations

from oss.core.contracts import AnalysisPlan, AnalysisResults

WEIGHT_GAP_SE = 2.0     # 加权与不加权两版系数之差超过加权版标准误的这么多倍，开 survey_weight_gap（无文献出处；校准后用户 2026-09-15 定：保持）
FEW_PSU = 30            # 初级抽样单位少于这个数，开 survey_few_psu（无文献出处；校准后用户 2026-09-15 定：保持）


def unweighted_label(plan: AnalysisPlan) -> str | None:
    """计划里「不加权复核」那一项：`weights` 显式写成空串的那项（`None` 是沿用主计划，不算）。"""
    return next((s.label for s in plan.robustness if s.weights == ""), None)


def weight_gap(plan: AnalysisPlan, results: AnalysisResults) -> tuple[float, float, float] | None:
    """`(加权系数, 不加权系数, 两者之差 ÷ 加权版标准误)`；没加权、没锁不加权复核、那项没跑出来，返回 None。"""
    label, main = unweighted_label(plan), results.estimates.get("main")
    if not plan.weights or label is None or main is None or main.se <= 0:
        return None
    run = results.robustness.get(label)
    if run is None or run.error or "main" not in run.estimates:
        return None
    u = run.estimates["main"].coef
    return main.coef, u, abs(main.coef - u) / main.se
