"""仿真三查（V2 任务 7）：`sim_state.json` 在场时 `paper check` 多查三条。

门控是第一原则：**没有 `sim_state.json` 就一个字都不查**——既有路径的
problems/warnings 一字节不许变（散文冻结那边盯着）。三条的宽严已决：

  一查 方法节按配料单报全：**核得动的七项**（模拟器、模拟器版本、模型、模型网关、
       温度、轮数、每条件重复次数）都要在文中出现；数字要独立成数，
       `n_reps` 另有加严口径——要跟「重复/rep」写在同一段        → problem
       （2026-09-19 从两项扩到七项：漏掉的恰恰是要紧的四项——模型名、模型版本、
        走哪个网关、什么温度，而合成数据的结果正是随这几样漂移的）
  二查 降级措辞：`downgrade=true` 时全文至少一次「初步/探索性」，
       且局限含降级固定句的头几个字「单次模拟无法区分」        → problem
  三查 现实断言警戒（窄词表，宁漏勿滥）：句子把模拟结果说成
       现实/政策断言、又没限定「在本模型内」                  → warning 逐句

`sim_state.json` 的结构照抄写它的人：`oss/api/sim.py` 的 `import_box`——
顶层 box/downgrade/single_condition/warnings/meta，配料单在 meta 里。
"""

from __future__ import annotations

import json
from pathlib import Path

from oss.api import errors
from oss.api import paper as paper_api
from oss.core.contracts import AnalysisResults, Estimate, SpecSummary


#: 报全七项的方法节。单考某一项时从这份里拿掉那一项（`_but`），别的照报——
#: 这样一条测试红了就只可能是那一项的事
FULL_METHODS = ("用 oasis 模拟器（版本 0.3.1）跑的，模型 gpt-4o-2024-08-06 走 openai 网关，"
                "温度 0.7，每条件 5 轮、重复 3 次。")


def _but(*drop: str) -> str:
    """报全七项，只把点名的那几项从方法节里拿掉。"""
    out = FULL_METHODS
    for piece in drop:
        out = out.replace(piece, "（见附录）")
    return out


def paper(methods: str = "方法。",
          body: str = "在本模型内，主效应为 {{result:main.coef}}。",
          limits: str = "识别策略有边界。") -> str:
    """能过既有校验的最小骨架（占位符、主结果表都齐），三处内容各自可换。"""
    return ("# 标题\n\n## 摘要\n效应 {{result:main.coef}}（n = {{result:main.n}}）。\n\n"
            "## 引言\n背景。\n\n## 数据与方法\n" + methods + "\n\n"
            "## 结果\n*表 1. 主结果。*\n{{table:main}}\n" + body + "\n\n"
            "## 讨论\n### 局限\n" + limits + "\n")


def _sim(simulator: str = "oasis", n_reps: int = 3, downgrade: bool = False) -> dict:
    """照 `oss/api/sim.py::import_box` 落盘的档案结构造一份 sim_state。"""
    return {"box": "/tmp/box", "downgrade": downgrade, "single_condition": False,
            "warnings": [],
            "meta": {"simulator": simulator, "simulator_version": "0.3.1",
                     "model": "gpt-4o-2024-08-06", "model_provider": "openai",
                     "temperature": 0.7, "prompts": "见附录 A", "population": "五十个智能体",
                     "conditions": {"a": "x", "b": "y"},
                     "n_reps": n_reps, "rounds": 5, "interview_plan": "每五轮一次",
                     "contamination_note": "不确定"}}


def _run(tmp_path: Path, text: str, sim: dict | None = None) -> Path:
    run = tmp_path / "r"
    run.mkdir(parents=True)
    AnalysisResults(plan_id="p", n=100,
                    estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe")).save(run / "results.json")
    (run / "paper.md").write_text(text, encoding="utf-8")
    if sim is not None:
        (run / "sim_state.json").write_text(
            json.dumps(sim, ensure_ascii=False), encoding="utf-8")
    return run


def _check(run: Path) -> tuple[list[str], list[str]]:
    """过与不过都要拿到 problems/warnings：拒绝时它们跟着 Refused 走。"""
    try:
        res = paper_api.check(str(run))
        return [], list(res["data"]["warnings"])
    except errors.Refused as e:
        return list(e.problems), list(e.data["warnings"])


# ══════════════════════════════════ 门控：没有 sim_state 就全不触发


def test_无sim_state时三查全不触发(tmp_path) -> None:
    """普通论文写「现实中将」也不关仿真三查的事——门控是第一原则。"""
    run = _run(tmp_path, paper(body="主效应为 {{result:main.coef}}。现实中将出现同样的迁移潮。"))
    problems, warnings = _check(run)
    assert not [p for p in problems if "仿真" in p or "降级" in p or "模拟器" in p], problems
    assert not [w for w in warnings if "模型边界" in w or "在本模型内" in w], warnings


# ══════════════════════════════════ 一查：方法节实名模拟器与重复次数


def test_报了模拟器与重复次数就放行(tmp_path) -> None:
    run = _run(tmp_path, paper(methods=FULL_METHODS), _sim())
    problems, warnings = _check(run)
    assert not problems, problems
    assert not [w for w in warnings if "模型边界" in w], warnings


def test_没报模拟器名要拒(tmp_path) -> None:
    run = _run(tmp_path, paper(methods=_but("oasis ")), _sim())
    problems, _ = _check(run)
    assert any("仿真论文的方法节没按配料单报全" in p and "oasis" in p for p in problems), problems


def test_重复次数不与重复同段要拒(tmp_path) -> None:
    run = _run(tmp_path, paper(methods=_but("重复 3 次")), _sim())
    problems, _ = _check(run)
    assert any("仿真论文的方法节没按配料单报全" in p and "重复次数 3" in p for p in problems), problems


def test_模拟器名不区分大小写(tmp_path) -> None:
    run = _run(tmp_path, paper(methods=FULL_METHODS.replace("oasis", "OASIS")), _sim())
    problems, _ = _check(run)
    assert not problems, problems


# ══════════════════════════════════ 二查：downgrade=true 的降级措辞


def test_降级缺初步或探索性要拒(tmp_path) -> None:
    run = _run(tmp_path, paper(
        methods=FULL_METHODS.replace("重复 3 次", "重复 2 次"),
        limits="单次模拟无法区分随机波动与真实规律。"), _sim(n_reps=2, downgrade=True))
    problems, _ = _check(run)
    assert any("初步" in p and "探索性" in p for p in problems), problems


def test_降级缺降级固定句要拒(tmp_path) -> None:
    run = _run(tmp_path, paper(
        methods=FULL_METHODS.replace("重复 3 次", "重复 2 次"),
        body="在本模型内，初步结果显示主效应为 {{result:main.coef}}。"), _sim(n_reps=2, downgrade=True))
    problems, _ = _check(run)
    assert any("单次模拟无法区分" in p for p in problems), problems


def test_降级措辞齐全放行(tmp_path) -> None:
    run = _run(tmp_path, paper(
        methods=FULL_METHODS.replace("重复 3 次", "重复 2 次"),
        body="在本模型内，初步结果显示主效应为 {{result:main.coef}}。",
        limits="单次模拟无法区分随机波动与真实规律。"), _sim(n_reps=2, downgrade=True))
    problems, _ = _check(run)
    assert not problems, problems


def test_降级句只查头几个字(tmp_path) -> None:
    """查的是固定句的头「单次模拟无法区分」——后半句换个写法不该误拒。"""
    run = _run(tmp_path, paper(
        methods=FULL_METHODS.replace("重复 3 次", "重复 2 次"),
        body="在本模型内，探索性结果显示主效应为 {{result:main.coef}}。",
        limits="单次模拟无法区分偶然波动与真实规律。"), _sim(n_reps=2, downgrade=True))
    problems, _ = _check(run)
    assert not problems, problems


# ══════════════════════════════════ 三查：现实断言警戒（警告不挡路）


def test_现实断言逐句警告(tmp_path) -> None:
    run = _run(tmp_path, paper(
        methods=FULL_METHODS,
        body="在本模型内，主效应为 {{result:main.coef}}。现实中将出现同样的迁移潮。"
             "政策应当立即推广该机制。"), _sim())
    problems, warnings = _check(run)
    assert not problems, problems
    assert any("现实中将出现同样的迁移潮" in w for w in warnings), warnings
    assert any("政策应当立即推广该机制" in w for w in warnings), warnings


def test_限定在本模型内的句子豁免(tmp_path) -> None:
    run = _run(tmp_path, paper(
        methods=FULL_METHODS,
        body="主效应为 {{result:main.coef}}。"
             "这一效应仅在本模型内成立，不宜外推为现实世界会同样迁移。"), _sim())
    problems, warnings = _check(run)
    assert not problems, problems
    assert not [w for w in warnings if "模型边界" in w], warnings


# ══════════════════════════════════ 盔甲：坏 sim_state 给人话，不崩


def test_sim_state坏JSON给人话不崩(tmp_path) -> None:
    """入库档案被人手改坏了：读不出来要说人话，不许把 traceback 甩给用户。"""
    run = _run(tmp_path, paper(methods=FULL_METHODS))
    (run / "sim_state.json").write_text("{坏的", encoding="utf-8")
    problems, _ = _check(run)
    assert any("sim_state.json" in p and "oss sim import" in p for p in problems), problems


def test_sim_state顶层或配料单不是对象也给人话(tmp_path) -> None:
    """顶层是列表、meta 是列表——都算档案坏了，一条人话，不崩。"""
    for sub, raw in (("a", "[1, 2]"), ("b", '{"meta": [1]}')):
        run = _run(tmp_path / sub, paper(methods=FULL_METHODS))
        (run / "sim_state.json").write_text(raw, encoding="utf-8")
        problems, _ = _check(run)
        assert any("oss sim import" in p for p in problems), (sub, problems)
        assert not [p for p in problems if "None" in p], (sub, problems)


def test_n_reps带正则符号不崩也不写None(tmp_path) -> None:
    """n_reps 是从磁盘上的文件读来的——正则元字符要按字面转义，不许注入。"""
    run = _run(tmp_path, paper(methods=FULL_METHODS),
               _sim(n_reps="3["))
    problems, _ = _check(run)          # 不抛 traceback 就已经赢了一半
    assert any("仿真论文的方法节没按配料单报全" in p for p in problems), problems
    assert not [p for p in problems if "None" in p], problems


def test_缺n_reps给人话而不是None(tmp_path) -> None:
    sim = _sim()
    del sim["meta"]["n_reps"]
    run = _run(tmp_path, paper(methods=FULL_METHODS), sim)
    problems, _ = _check(run)
    assert any("sim_state.json" in p for p in problems), problems
    assert not [p for p in problems if "None" in p], problems


# ══════════════════════════════════ 一查扩容（2026-09-19）：从两项到七项
#
# 原先只查模拟器名与重复次数，漏掉的恰恰是要紧的四项——**模型名、模型版本、走哪个网关、
# 什么温度**。而合成数据最大的已知毛病正是「结果随模型版本与措辞漂移」（bisbee2024）：
# 不记这几样，这篇论文就是不可复现的，而没有任何一步会说一句。


import pytest                                                        # noqa: E402

from oss.api.paper import SIM_NO_TEETH, _SIM_REPORTABLE               # noqa: E402


@pytest.mark.parametrize("piece,说的是", [
    ("版本 0.3.1", "模拟器版本"),
    ("gpt-4o-2024-08-06", "模型"),
    ("openai", "模型网关"),
    ("温度 0.7", "温度"),
    ("5 轮", "轮数"),
])
def test_七项里少报一项就拒(piece: str, 说的是: str, tmp_path) -> None:
    """报全其余六项，只拿掉这一项——红了就只可能是这一项的事。"""
    run = _run(tmp_path, paper(methods=_but(piece)), _sim())
    problems, _ = _check(run)
    hit = [p for p in problems if "没按配料单报全" in p]
    assert hit and 说的是 in hit[0], (piece, problems)


def test_报全七项就放行(tmp_path) -> None:
    run = _run(tmp_path, paper(methods=FULL_METHODS), _sim())
    problems, _ = _check(run)
    assert not problems, problems


def test_数字要独立成数_不许被更长的数顶掉(tmp_path) -> None:
    """轮数 5 不许被「25 个智能体」里的 5 顶掉；温度 0.7 不许被 10.7 顶掉。"""
    run = _run(tmp_path, paper(methods=(
        "用 oasis 模拟器（版本 0.3.1）跑的，模型 gpt-4o-2024-08-06 走 openai 网关，"
        "温度 10.7，共 25 个智能体，每条件重复 3 次。")), _sim())
    problems, _ = _check(run)
    hit = [p for p in problems if "没按配料单报全" in p]
    assert hit and "温度 0.7" in hit[0] and "轮数 5" in hit[0], problems


def test_温度为零也查得动(tmp_path) -> None:
    """0 是合法温度（`_empty` 就这么定的）——不许因为它是零就当没填。"""
    run = _run(tmp_path, paper(methods=FULL_METHODS.replace("温度 0.7", "温度设置见附录")),
               _sim())
    sim = _sim(); sim["meta"]["temperature"] = 0
    run2 = _run(tmp_path / "b", paper(methods=FULL_METHODS.replace("温度 0.7", "温度 0")), sim)
    assert [p for p in _check(run)[0] if "温度" in p], "漏报温度没拦住"
    assert not [p for p in _check(run2)[0] if "温度" in p], "报了温度 0 却被拦"


def test_配料单里干脆没这一项_也报(tmp_path) -> None:
    """sim_state.json 是运行目录里一个普通 JSON，把一项删掉就核不了——

    **「核不了」不等于「过了」**。这是测量那一批九个洞反复出现的同一条。
    """
    sim = _sim()
    del sim["meta"]["model"]
    run = _run(tmp_path, paper(methods=FULL_METHODS), sim)
    problems, _ = _check(run)
    hit = [p for p in problems if "没按配料单报全" in p]
    assert hit and "核不了，不等于过了" in hit[0], problems


def test_名字短到核不动_照实说核不动(tmp_path) -> None:
    """把 model 填成一个字母，「这个名字在文中出现过没有」就成了空问：

    随便哪篇论文里都有 `m`。**填一个字母就永远过**是零代价的绕行路。
    """
    sim = _sim()
    sim["meta"]["model"] = "m"
    run = _run(tmp_path, paper(methods=FULL_METHODS), sim)
    problems, _ = _check(run)
    hit = [p for p in problems if "没按配料单报全" in p]
    assert hit and "核不动" in hit[0] and "gpt-4o-2024-08-06" in hit[0], problems


def test_四项散文与条件名不查_这件事要写死(tmp_path) -> None:
    """**没有牙齿的那几项要在代码里点名**——说明书据此照实写，不许含糊说「都查了」。

    提示词、人群、访谈计划、污染声明是自由散文，查它就得读懂意思；
    conditions 的键是机器标识（`threshold_low`），论文里本来就该用人话描述条件。
    """
    assert set(SIM_NO_TEETH) == {"prompts", "population", "interview_plan",
                                 "contamination_note", "conditions"}
    assert not (set(SIM_NO_TEETH) & set(_SIM_REPORTABLE)), "一项不能既有牙齿又没牙齿"
    # 配料单十二项 = 有牙齿的七项 + 没牙齿的五项，一项不多一项不少
    from oss.core.simbox import META_REQUIRED
    assert set(META_REQUIRED) == set(_SIM_REPORTABLE) | set(SIM_NO_TEETH), META_REQUIRED


def test_配料单里的小数不算裸小数(tmp_path) -> None:
    """两道门原先互相打架：说明书要求方法节报全配料单，裸小数铁律又把温度 0.7 拦下来。

    修在根上——`sim_state.json` 本来就是本次运行自己的产出，照抄它不算编造。
    """
    run = _run(tmp_path, paper(methods=FULL_METHODS), _sim())
    problems, _ = _check(run)
    assert not [p for p in problems if "裸小数" in p], problems
    # 没有货箱时，同一个 0.7 照样是裸小数——例外只对本次运行自己的产出开
    run2 = _run(tmp_path / "b", paper(methods="温度 0.7。"))
    assert [p for p in _check(run2)[0] if "裸小数" in p]


# ══════════════════════════════════ 结论要限定在模型内（2026-09-19 新门）
#
# 这一条是 SIM_REALITY_LEXICON 那张五个词的清单的**对价**。两张清单方向相反：
#
#   · 违规清单：换个说法（「落到现实中」「对政策的含义是」）就漏放，**而漏放一声不响**
#     ——2026-09-16 评测量到的 6% 就是这个形状
#   · 「必须出现」清单：写了个清单上没有的等价写法，结果是**误报**，不是漏放。
#     误报会被投诉、会被修
#
# 所以不把违规词表做长（做长也还是 6%），换一个问法。


def test_通篇没限定在模型内_拒跑(tmp_path) -> None:
    run = _run(tmp_path, paper(methods=FULL_METHODS,
                               body="主效应为 {{result:main.coef}}，机制清晰。"), _sim())
    problems, _ = _check(run)
    hit = [p for p in problems if "一次都没有把结论限定在模型内" in p]
    assert hit, problems
    assert "within this model" in hit[0], "英文写法也要在处方里给出来"


@pytest.mark.parametrize("说法", [
    "在本模型内，主效应为 {{result:main.coef}}。",
    "在模型内，主效应为 {{result:main.coef}}。",
    "在本模型的设定下，主效应为 {{result:main.coef}}。",
    "在本模型框架内，主效应为 {{result:main.coef}}。",
    "Within this model, the effect is {{result:main.coef}}.",
    "Within the model's dynamics, the effect is {{result:main.coef}}.",
    "The effect is {{result:main.coef}} within the simulated model.",
])
def test_换个等价写法照样认(说法: str, tmp_path) -> None:
    """**「必须出现」型的清单收得宽些**：少收一种写法只会误报作者，不会漏放越界的稿子。"""
    run = _run(tmp_path, paper(methods=FULL_METHODS, body=说法), _sim())
    problems, _ = _check(run)
    assert not [p for p in problems if "限定在模型内" in p], (说法, problems)


def test_没有货箱就不查这一条(tmp_path) -> None:
    """门控是第一原则：普通论文不会因为没写「在本模型内」被拦。"""
    run = _run(tmp_path, paper(body="主效应为 {{result:main.coef}}。"))
    problems, _ = _check(run)
    assert not [p for p in problems if "限定在模型内" in p], problems


def test_限定语写在代码块或引号里不算(tmp_path) -> None:
    """受访者原话里的「在本模型内」不是作者自己的限定。"""
    run = _run(tmp_path, paper(
        methods=FULL_METHODS,
        body='主效应为 {{result:main.coef}}。有小人说“在本模型内一切正常”。'), _sim())
    problems, _ = _check(run)
    assert [p for p in problems if "限定在模型内" in p], problems


def test_两处限定语用的是同一把尺子(tmp_path) -> None:
    """原先两处各写各的：`scan` 认「在模型内」，仿真三查第三条只认「在本模型内」。

    作者写「在模型内」，因果措辞那边放行、仿真三查那边照样警告——
    **一个判据不会同时在这两个方向上错；只有两张各写各的清单会。**
    """
    run = _run(tmp_path, paper(
        methods=FULL_METHODS,
        body="在模型内，主效应为 {{result:main.coef}}，现实中将出现同样的迁移潮。"), _sim())
    problems, warnings = _check(run)
    assert not [p for p in problems if "限定在模型内" in p], problems
    assert not [w for w in warnings if "模型边界" in w], warnings


# ══════════════════════════════════ 五道门改挂两处（2026-09-19）
#
# 原先门控只有一处：盘上有 sim_state.json。**声明为仿真却没入库的论文，一道门都不响**，
# 正文就能拿模型里的数字去说现实。现在挂两处——档案在，**或者**计划声明为仿真。
# 要读配料单的那几条没档案跑不了，如实跳过；读得动文本的两条照跑。


def _with_plan(run: Path, **kw):
    from oss.core.contracts import AnalysisPlan
    AnalysisPlan(plan_id="p", idea_id="i1", estimator="describe", outcome="y", **kw
                 ).save(run / "plan.json")
    return run


def test_声明为仿真却没入库_结论限定那条照样查(tmp_path) -> None:
    run = _with_plan(_run(tmp_path, paper(body="主效应为 {{result:main.coef}}。")),
                     research_type="simulation")
    problems, _ = _check(run)
    assert [p for p in problems if "限定在模型内" in p], problems


def test_把仿真搁进also_也照样查(tmp_path) -> None:
    run = _with_plan(_run(tmp_path, paper(body="主效应为 {{result:main.coef}}。")),
                     research_type="causal", also=["simulation"])
    problems, _ = _check(run)
    assert [p for p in problems if "限定在模型内" in p], problems


def test_声明为仿真却没入库_现实断言照样警告(tmp_path) -> None:
    run = _with_plan(_run(tmp_path, paper(
        body="在本模型内，主效应为 {{result:main.coef}}。现实中将出现同样的迁移潮。")),
        research_type="simulation")
    _, warnings = _check(run)
    assert [w for w in warnings if "模型边界" in w], warnings


def test_没档案时要读配料单的那几条跑不了_如实跳过(tmp_path) -> None:
    """没有入库档案就没有配料单——报「没按配料单报全」是无中生有。

    这一条在 `oss run` 那边报 plan.simulation.box_missing，不在这里重复。
    """
    run = _with_plan(_run(tmp_path, paper(
        body="在本模型内，主效应为 {{result:main.coef}}。")), research_type="simulation")
    problems, _ = _check(run)
    assert not [p for p in problems if "没按配料单报全" in p], problems


def test_既没档案也没声明_一个字都不查(tmp_path) -> None:
    """门控仍是第一原则：普通论文不受任何影响。"""
    run = _with_plan(_run(tmp_path, paper(
        body="主效应为 {{result:main.coef}}。现实中将出现同样的迁移潮。")),
        research_type="causal")
    problems, warnings = _check(run)
    assert not [p for p in problems if "限定在模型内" in p], problems
    assert not [w for w in warnings if "模型边界" in w], warnings
