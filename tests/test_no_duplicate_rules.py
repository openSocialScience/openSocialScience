"""同一段判据不许有第二份（补门的洞·实施计划 Task 3）。

软措辞汇总从前在 `oss/api/paper.py` 与 `evals/traps/score.py` 各有一份——后者的
注释自陈是抄来的。两处现在都调 `causal_wording.soft_warning`：这里用同一份稿子把
两条路各跑一遍，比它们报出来的条数。

**别拿对象比较来判「是不是同一份」**：`re.compile` 自带缓存，同一个式子写两遍拿到
的是同一个对象，抄回去照样绿。要测就比行为，或者扫源码。
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from oss.api import errors
from oss.api import paper as paper_api
from oss.core.contracts import AnalysisPlan, AnalysisResults, Estimate, SpecSummary

ROOT = Path(__file__).resolve().parents[1]
TRAPS = ROOT / "evals" / "traps"
#: 软措辞那条警告的头：两条路各自报的条数从这里读
_N_SOFT = re.compile(r"^(\d+) 处模糊的因果说法")


def _score():
    """按路径载入 evals/traps/score.py——注入测试的目录里也有个 score.py，裸名 import 会串。"""
    key = "evals_traps_score"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, TRAPS / "score.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(key, None)
        raise
    return mod


def _soft_n(warnings: list[str]) -> int:
    """报出来的软措辞条数；一条都没报算 0。"""
    found = [int(m.group(1)) for w in warnings if (m := _N_SOFT.match(str(w)))]
    assert len(found) <= 1, f"软措辞汇总报了不止一条：{warnings}"
    return found[0] if found else 0


def _via_traps(doc: dict, trap: dict, tmp_path: Path) -> int:
    out = _score().evaluate(doc, trap, "trap", tmp_path)
    assert not out.error and not out.skipped, (out.error, out.skipped)
    return _soft_n([text for _, text in out.warnings])


def _via_paper_check(text: str, plan: dict, tmp_path: Path) -> int:
    run = tmp_path / "r"
    run.mkdir(parents=True)
    AnalysisPlan(**plan).save(run / "plan.json")
    AnalysisResults(plan_id=plan["plan_id"], n=100,
                    estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe")).save(run / "results.json")
    (run / "paper.md").write_text(text, encoding="utf-8")
    try:
        warnings = paper_api.check(str(run))["data"]["warnings"]
    except errors.Refused as exc:                 # 拒绝时警告跟着异常走，软措辞那条照样在里面
        warnings = exc.data["warnings"]
    return _soft_n(list(warnings))


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / "evals/traps/score.py").exists(),
                    reason="evals/ 不随仓库分发，判分器那条路取不到")
@pytest.mark.parametrize("side", ["paper", "twin_paper"])
def test_陷阱集与论文检查报出的软措辞条数相等(tmp_path: Path, side: str) -> None:
    """同一份稿子：陷阱集判分器那条路，与 oss paper check 那条路，条数必须一样。

    **只比相等不够**：把 `soft_warning` 改成恒返回 `None`——等价于两个调用点都被删掉、
    用户再也收不到这条警告——两边一起变成 0，「相等」照样成立，**这条测试自己什么都没保住**
    （2026-09-16 实测：报警的是别处三条测试，不是这一条）。所以两侧的期望值也钉死：
    有坑那份必须报 1 条，双胞胎那份必须 0 条。
    """
    sc = _score()
    doc = json.loads((TRAPS / "traps.json").read_text(encoding="utf-8"))
    trap = next(t for t in doc["traps"] if t["id"] == "causal_wording_soft")
    spec = {**trap, "paper": trap[side]}
    text = sc.paper_text(doc, spec["paper"])
    n_traps = _via_traps(doc, spec, tmp_path / "traps")
    n_api = _via_paper_check(text, trap["plan"], tmp_path / "api")
    assert n_traps == n_api, (n_traps, n_api)
    assert n_traps == (1 if side == "paper" else 0), n_traps


@pytest.mark.parametrize("phrase, home", [
    ("处模糊的因果说法", "oss/core/causal_wording.py"),
    ("仿真结论跨出模型边界", "oss/core/causal_wording.py"),
])
def test_搬过的判据文案全仓库只剩一处(phrase: str, home: str) -> None:
    """搬进纯函数之后，文案在 oss/ 与 evals/ 底下只该出现在它的新家。

    `oss/core/findings.py` 不算：那是编号登记表，不是第二份判据。
    """
    where = [rel for base in ("oss", "evals")
             for p in sorted((ROOT / base).rglob("*.py"))
             if (rel := str(p.relative_to(ROOT))) != "oss/core/findings.py"
             and phrase in p.read_text(encoding="utf-8")]
    assert where == [home], where
