"""`oss schema`：某个产出物该长什么样。

skill 写 ideas.json / plan.json / paper.md 时靠这个知道格式——
**不该让它去读 Python 源码。**

不碰运行目录，所以没有 `run` 参数：查格式是纯查询。
"""

from __future__ import annotations

import json

from oss.api.errors import BadInput
from oss.api.result import result
from oss.core import fake
from oss.core.contracts import (
    ARTIFACTS,
    AnalysisPlan,
    AnalysisResults,
    BindingReport,
    DataProfile,
    IdeaSet,
    LiteratureCorpus,
    MeasurementSpec,
    PowerReport,
    ReadingCards,
    RealBenchmark,
    Screening,
    SimulationSpec,
    StyleRules,
)

#: paper.md 不是 JSON，没有 schema 可给——给的是规矩。
PAPER_RULES = (
    "paper.md 是 Markdown，不是 JSON。要求：\n"
    "  · 至少五节：摘要 / 引言 / 数据与方法 / 结果 / 讨论\n"
    "  · 正文引用结果一律用占位符，不许手打数字：\n"
    "      {{result:main.coef}}  {{result:main.se}}\n"
    "      {{result:main.p}}     {{result:main.n}}"
)

#: `oss stack` 的映射文件也不是 JSON 产出物，是 skill 写给工具的一份声明。
#: 给规矩 + 一份照着 CGSS 真形状写的样例——**这三个坑都在样例里出现一次**：
#: 逐轮改名、编码翻转、某一轮整个模块没问。
STACK_SPEC = (
    "oss stack 的映射文件是 JSON。**判断在这里，机械动作归工具。**\n"
    "\n"
    "  · sources：一份来源一个对象，按轮次排\n"
    "  · path：数据文件；wave：这一轮的标识，会写进轮次列\n"
    "  · columns：{概念名: 这一轮的列名}\n"
    "  · 编码跨轮翻转的（同一个说法挂到了另一个码上），写成\n"
    "    {\"from\": 列名, \"recode\": {原值: 新值}}——工具查出翻转会**拒绝执行**，\n"
    "    直到你显式声明怎么对齐。只是措辞变了（码没搬家）不拦，只警告\n"
    "  · 某一轮没有的概念，就别写进那一轮的 columns；工具会报「整轮缺席」\n"
    "\n"
    "{\n"
    '  "wave_column": "year",\n'
    '  "sources": [\n'
    "    {\n"
    '      "path": "data/raw/survey_2012.dta", "wave": 2012,\n'
    '      "columns": {\n'
    '        "hours": "q31",\n'
    '        "educ": "q08",\n'
    '        "weight": "weight",\n'
    '        "employed": {"from": "q30", "recode": {"1": 1, "2": 0}}\n'
    "      }\n"
    "    },\n"
    "    {\n"
    '      "path": "data/raw/survey_2020.dta", "wave": 2020,\n'
    '      "columns": {\n'
    '        "hours": "q31b",\n'
    '        "educ": "q08",\n'
    '        "weight": "wt_ind",\n'
    '        "employed": {"from": "q30", "recode": {"4": 1, "1": 0, "2": 0, "3": 0}}\n'
    "      }\n"
    "    }\n"
    "  ]\n"
    "}\n"
    "\n"
    "上面那两个 recode 不是装饰：同一道题 2012 年 1=是、2020 年 4=是。\n"
    "**不对齐就摞，不会报错，只会把有工作的人算成没工作。**"
)

#: 测量类计划的 `measurement` 块怎么填。一份样例只装得下一种测量研究，
#: 而造尺子与用尺子填法不同（测量骨架设计稿第三节），所以两块跟在样例后面单给。
#: **由 MeasurementSpec 自己写出来**：例子违规就在导入这一刻炸，不会静静地教错。
MEASUREMENT_EXAMPLES = (
    "measurement 块（测量类计划要填；上面那份样例是因果类，这一栏是 null）：\n"
    "\n"
    "· 造尺子——新编量表、新建指数。效度证据按类给，"
    "「效度良好」这种整体判断不收，它是结论不是证据；"
    "声明了哪几类，evidence_outputs 就要登记那几类的产出叫什么名字：\n"
    + MeasurementSpec(
        kind="instrument", construct="对陌生人的一般信任",
        indicators=["q12a", "q12b", "q12c", "q12d"],
        validity_evidence=["structural", "criterion"],
        evidence_outputs={"structural": "因子载荷", "criterion": "与捐款行为的相关",
                          "reliability": "内部一致性系数"},
        source_quality="self_report").model_dump_json(indent=2)
    + "\n\n"
    "· 用尺子——拿现成量表做实质研究。跨组比较的分组变量写进 groups_compared，"
    "**跨组就要给测量等价检验的产出**（做不出来就别比组间差，各组各报各的）：\n"
    + MeasurementSpec(
        kind="application", construct="对陌生人的一般信任",
        indicators=["trust_general"], groups_compared=["urban"],
        evidence_outputs={"equivalence": "城乡测量等价检验"},
        source_quality="self_report").model_dump_json(indent=2)
)

#: 仿真类计划的 `simulation` 块怎么填。两块的分界不是「用什么模拟器」，
#: 而是**主张不主张这批小人代表真人**——两种走的门不一样（仿真骨架设计稿 4.3）。
#: 同样**由 SimulationSpec 自己写出来**：例子违规就在导入这一刻炸。
SIMULATION_EXAMPLES = (
    "simulation 块（仿真类计划要填；上面那份样例是因果类，这一栏是 null）：\n"
    "\n"
    "· 不主张代表真人——做的是「这套机制能不能产生这个已知模式」"
    "（Grimm 等 2020 的面向模式验收）。mechanism 说模型里什么导致什么，"
    "pattern 说模型跑对了该出现哪个现实里已经观察到的规律：\n"
    + SimulationSpec(
        mechanism="邻里同质化偏好使住户迁出异质社区，聚集成同质街区",
        pattern="隔离指数随偏好阈值单调上升，且在阈值 0.3 附近出现拐点",
        stands_in_for_humans=False,
        conditions_compared=["threshold_low", "threshold_high"]).model_dump_json(indent=2)
    + "\n\n"
    "· 主张这批合成样本代表真人——**就得给出真实对照数据**："
    "合成样本的方差会被压扁（Bisbee 2024），拿它当调查数据用，"
    "标准误会假小、显著性会假强；pairs 是列的配对，合成列在前、真实列在后：\n"
    + SimulationSpec(
        mechanism="议题接触顺序影响态度极化程度",
        pattern="极化程度随异质信息比例下降，与真实面板调查同向",
        conditions_compared=["homogeneous_feed", "mixed_feed"],
        stands_in_for_humans=True,
        real_benchmark=RealBenchmark(
            data="cgss2017",
            pairs=[("attitude_score", "a33"), ("media_hours", "a301")]),
    ).model_dump_json(indent=2)
    + "\n\n"
    "**货箱路径不写进计划**：它由 `oss sim import` 写进 sim_state.json，"
    "计划里再抄一遍就是两处真相，检测一律以盘上那份为准。"
)

#: skill 自己写的那几份。**顺序就是 `--help` 与报错里列出的顺序**，别动。
WRITABLE = {"ideas": IdeaSet, "plan": AnalysisPlan, "reading": ReadingCards, "rules": StyleRules,
            "screening": Screening}
#: 工具写、skill 只读的那几份。
READABLE = {"profile": DataProfile, "literature": LiteratureCorpus,
            "binding": BindingReport, "power": PowerReport, "results": AnalysisResults}
ALL_MODELS = {**WRITABLE, **READABLE}

#: 产出物 → 文件名。`rules` 不在 `ARTIFACTS` 里（它是风格包内部的 rules.json，
#: 不是运行目录的产出物），补在这里——原先 `oss schema rules` 在这一步 KeyError。
FILENAMES = {**ARTIFACTS, "rules": "rules.json"}


def describe(artifact: str, *, example: bool = False) -> dict:
    """schema、或者一份填好的样例。`paper` 不是 JSON，给的是规矩。

    `example_json` 是 **pydantic 自己写的那个字符串**，CLI 直接打它。
    `json.dumps(model_dump(), indent=2)` 跟 `model_dump_json(indent=2)`
    不保证同一个字节（float、datetime 的写法可能不同），而散文一个字节都不许变——
    所以字符串由这里给出，机器要读的那一份另放在 `example` 里。
    """
    if artifact == "paper":
        return result("paper.md 的写法（不是 JSON）",
                      {"artifact": FILENAMES["paper"], "kind": "prose", "text": PAPER_RULES,
                       "written_by": "skill"})
    if artifact == "stack":
        return result("oss stack 映射文件的写法（不是产出物）",
                      {"artifact": "stack_spec.json", "kind": "prose", "text": STACK_SPEC,
                       "written_by": "skill"})
    cls = ALL_MODELS.get(artifact)
    if cls is None:
        # 退出码 1（不是 BadInput 缺省的 2）：散文那一侧一直是 1，退出码是契约
        #
        # `paper` 与 `stack` 也要列出来。它们查得到、只是没有 JSON schema，
        # 而这行报错**正是照着名字来问的人唯一能看到的清单**——
        # 漏掉能查的选项，等于告诉他那条路不存在。
        raise BadInput(
            f"没有叫 {artifact} 的产出物。可选：{' / '.join([*ALL_MODELS, 'paper', 'stack'])}",
            code=1)
    written_by = "skill" if artifact in WRITABLE else "tool"
    if example:
        text = _example(artifact).model_dump_json(indent=2)
        data = {"artifact": FILENAMES[artifact], "kind": "example",
                "example": json.loads(text), "example_json": text,
                "written_by": written_by}
        if artifact == "plan":
            data["measurement_examples"] = MEASUREMENT_EXAMPLES
            data["simulation_examples"] = SIMULATION_EXAMPLES
        return result(f"{FILENAMES[artifact]} 的样例", data)
    return result(f"{FILENAMES[artifact]} 的 schema",
                  {"artifact": FILENAMES[artifact], "kind": "schema",
                   "schema": cls.model_json_schema(), "written_by": written_by})


def _example(artifact: str):
    """样例仍用假数据：查格式不该要求手边有数据文件。"""
    makers = {
        "profile": lambda: fake.profile("your_data.csv"),
        "literature": lambda: fake.literature(["your query"]),
        "ideas": lambda: fake.ideas("你的研究方向"),
        "binding": lambda: fake.binding("idea_001"),
        "power": lambda: fake.power("idea_001", 0.2, 1000),
        "plan": lambda: fake.plan("idea_001"),
        "rules": lambda: StyleRules(
            journal="Social Forces", word_limit=10000, word_limit_includes_references=True,
            max_tables_figures=10, double_blind=True, abstract_no_citations=True,
            reference_style="Chicago Manual of Style, 17th edition", supplementary_max_pages=10,
            other_requirements=["Abstracts should summarize the article's quest with or without conclusions."]),
        "reading": lambda: ReadingCards(direction="你的研究方向", cards=[{
            "paper_id": "W2079007828", "source_level": "fulltext",
            "question": "教育扩张是否削弱了出身对地位获得的影响",
            "data": "GSS 1972–2010 累积截面，n≈30,000",
            "design": "OLS + 队列固定效应，出身 × 学历交互",
            "finding": "大学学历显著削弱出身效应，系数约 −0.8（SEI 单位）",
            "limitations": [{"quote": "Our data cannot distinguish selection into college from the causal effect of college itself.",
                             "where": "Discussion"}],
            "relevance": "直接",
            "notes": "只报了整体样本，没分性别",
        }]),
        "screening": lambda: Screening(direction="你的研究方向", verdicts=[
            {"paper_id": "W2079007828", "verdict": "纳入", "reason": "教育扩张与代际流动，正是本题"},
            {"paper_id": "10.1234/off-topic", "verdict": "排除", "reason": "流行病学队列研究，只是沾了 cohort 这个词"},
        ]),
        "results": lambda: fake.results("plan_001"),
    }
    return makers[artifact]()
