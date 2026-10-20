"""检查发现带编号：文字跟普通字符串一模一样，多带一个稳定编号，给评测归因用。

**不改任何给用户看的字**：Finding 是 str 的子类，不重写 __str__ / __repr__ / __format__——打印、比较、哈希、json.dumps
都跟原来的字符串一样，冻结的基准不会变。编号只有直接读返回列表的人（评测判分器）看得见；
拼进留痕备注、导出摘要、切片、pydantic 的 list[str] 字段时会丢，这是有意的。

**编号写字面量**：`Finding(f"…", "paper.bare_decimal")`，第二个参数不许是变量——
`tests/test_findings.py` 按语法树核源码里用到的编号都登记了、登记的编号源码里都用到了，写成变量就核不到。
"""
from __future__ import annotations

HARD, WARN = "hard", "warn"


class Finding(str):
    code: str

    def __new__(cls, text: str, code: str) -> "Finding":
        obj = super().__new__(cls, text)
        obj.code = code
        return obj

    def __getnewargs__(self) -> tuple[str, str]:
        # copy.deepcopy 与 pickle 按这两个参数重建；不写的话 str 自己的那份只给文字，重建时缺编号就报错
        return str(self), self.code


def code_of(x: object) -> str:
    """一条发现的编号；没挂编号的返回空串（判分时算「没法归因」）。"""
    return getattr(x, "code", "")


_PAPER = "oss paper check"
_EXPORT = "oss paper export"
_RUN = "oss run"
_POWER = "oss power"

# 编号 →（哪条命令报的，硬拦还是只提示，管什么）。新加一处发现就在这里登记——测试会核源码里用到的编号都在这张表里
FINDING_CODES: dict[str, tuple[str, str, str]] = {
    # ── 论文检查：manuscript.check
    "paper.explore_results": (_PAPER, HARD, "结果来自探索性分析"),
    "paper.section_missing": (_PAPER, HARD, "缺必需的节"),
    "paper.bare_decimal": (_PAPER, HARD, "手打的小数不在产出里"),
    "paper.bare_integer": (_PAPER, WARN, "手打的整数跟 N 或系数撞上"),
    "paper.no_placeholders": (_PAPER, WARN, "全文没有占位符"),
    "paper.placeholder_result": (_PAPER, HARD, "估计占位符解析不了"),
    "paper.placeholder_cite": (_PAPER, HARD, "引文占位符不在语料里"),
    "paper.placeholder_fig": (_PAPER, HARD, "图占位符解析不了"),
    "paper.placeholder_table": (_PAPER, HARD, "表占位符解析不了"),
    "paper.placeholder_diag": (_PAPER, HARD, "诊断占位符解析不了"),
    "paper.placeholder_robust": (_PAPER, HARD, "稳健性占位符解析不了（含引了报错的稳健性）"),
    "paper.placeholder_other": (_PAPER, HARD, "其余解析不了的占位符（功效、分箱、未知命名空间）"),
    "paper.binned_no_record": (_PAPER, HARD, "拿分箱形状当证据却没有分箱结果"),
    "paper.process_language": (_PAPER, HARD, "正文留下研究过程的话"),
    "paper.caption_missing": (_PAPER, HARD, "图表没有图注表注"),
    "paper.novelty": (_PAPER, HARD, "新颖性主张没有足够的全文撑着"),
    "paper.novelty_scope": (_PAPER, WARN, "新颖性主张有全文撑着，但正文要说出读过的范围"),
    "paper.main_table_missing": (_PAPER, WARN, "没有主表 / 分组表"),
    "paper.heading_run_in": (_PAPER, WARN, "标题前没有空行，pandoc 认不出"),
    "paper.en_abstract_position": (_PAPER, WARN, "中文稿的英文摘要排在参考文献之前"),
    "paper.bare_column_names": (_PAPER, WARN, "表里印的是原始列名，不是人话"),
    "paper.spec_code_in_table": (_PAPER, WARN, "稳健性表里印着 R1 这种规格代号"),
    "paper.cjk_reference": (_PAPER, WARN, "英文稿的参考文献表里有汉字条目"),
    "paper.abstract_no_numbers": (_PAPER, WARN, "摘要没有数字"),
    "paper.abstract_statistics": (_PAPER, WARN, "摘要里写了标准误 / p / MDE / 样本量"),
    "paper.abstract_too_long": (_PAPER, WARN, "中文摘要超过 300 字"),
    "paper.corner_quotes": (_PAPER, WARN, "中文稿用了直角引号「」——横排简体是 “”（GB/T 15834）"),
    "paper.mixed_spec_contrast": (_PAPER, WARN, "一句话比较带与不带交互项的规格"),
    # ── 论文检查：manuscript.check_claims（主张跟结果对不对得上）
    "claims.significance": (_PAPER, HARD, "显著与否跟 p 值矛盾"),
    "claims.direction": (_PAPER, WARN, "方向词跟系数符号相反"),
    "claims.prove": (_PAPER, WARN, "「证明」类措辞"),
    "claims.mde_exclusion": (_PAPER, WARN, "最小可探测效应当成排除下界"),
    "claims.limits_power": (_PAPER, HARD, "功效 / 精度判灰或不足却没写进局限"),
    "claims.limits_power_contra": (_PAPER, HARD, "功效 / 精度判灰或不足，局限里却说充足"),
    "claims.power_fragile": (_PAPER, WARN, "功效判词对假定敏感却没报敏感性"),
    "claims.limits_collinearity": (_PAPER, HARD, "共线严重却全文没提"),
    "claims.limits_collinearity_contra": (_PAPER, HARD, "共线诊断报严重，正文却说没有共线"),
    "claims.paraphrase": (_PAPER, WARN, "转述有疑"),
    "claims.references_missing": (_PAPER, WARN, "没有参考文献占位符"),
    "claims.power_n": (_PAPER, WARN, "功效的 N 跟结果的 N 对不上"),
    "cite.bare_surname": (_PAPER, HARD, "直呼语料作者的姓"),
    "cite.bare_surname_folded": (_PAPER, WARN, "去掉变音记号才跟语料作者的姓对得上"),
    "cite.no_author": (_PAPER, WARN, "正文引了没有作者的语料记录——导出会印成占位"),
    "cite.unknown_name": (_PAPER, WARN, "中文句里出现语料外的人名"),
    # ── 论文检查：oss.api.paper.check 自己的几条
    "paper.construct_gap": (_PAPER, WARN, "代理 / 构造变量的三栏没填全"),
    "paper.measurement_construct_gap": (_PAPER, WARN, "测量类的主构念没填代理差距三栏"),
    "paper.derived_unmentioned": (_PAPER, WARN, "prep.py 构造、分析用到的列论文里没提"),
    "paper.datapack_uncited": (_PAPER, WARN, "用了数据包却没写数据集名"),
    "sim.state_broken": (_PAPER, HARD, "仿真入库档案读不出来或缺关键项"),
    "sim.unreported": (_PAPER, HARD, "仿真论文没报模拟器 / 重复次数"),
    "sim.downgrade_hedge": (_PAPER, HARD, "降级仿真全文没有「初步」或「探索性」"),
    "sim.downgrade_sentence": (_PAPER, HARD, "降级仿真缺降级固定句"),
    "sim.reality": (_PAPER, WARN, "仿真结论跨出模型边界"),
    "sim.no_scope_limit": (_PAPER, HARD, "仿真论文通篇一次都没把结论限定在模型内"),
    # ── 论文检查拉进来的门（导出、oss run 也走其中几道）
    "gate.route_pending": ("oss paper check、oss paper export", HARD, "must 级处方没处置"),
    "gate.anchor": ("oss paper check、oss paper export", HARD, "锚点对不上"),
    "gate.research_type": ("oss run、oss paper check、oss paper export", HARD, "研究类型检测"),
    "gate.research_type_unlocked": ("oss paper check、oss paper export", HARD, "研究类型补了却没重跑锁定"),
    "gate.causal_question": (_RUN, HARD, "标成非因果，选中的题自己在问因果"),
    "wording.causal_hard": ("oss paper check、oss paper export", HARD, "非因果论文下明确的因果结论"),
    "wording.causal_soft": (_PAPER, WARN, "模糊的因果词"),
    "wording.forced_downgrade": (_PAPER, WARN, "识别审计判 FAIL、替代设计穷尽，全文按关联查措辞"),
    "iv.ar_missing": ("oss paper check、oss paper export", HARD, "弱工具却没引 AR 区间"),
    "paper.measurement_reliability_only": ("oss paper check、oss paper export", HARD,
                                           "论文引了信度那份产出，而产出里一类效度证据都没有"),
    "paper.measurement_equivalence_missing": ("oss paper check、oss paper export", HARD,
                                              "登记了跨组测量等价检验，那份产出却不在结果里"),
    "causal.sensitivity_uncited": (_PAPER, WARN, "因果类 ols_fe 没引敏感性数字"),
    # ── 导出的门：按退出码记（problems 与 summary 都挂；退出码 4、5 那两道没有 problems，只挂 summary）
    "export.anchor_or_type": (_EXPORT, HARD, "退出码 7"),
    "export.causal_family": (_EXPORT, HARD, "退出码 8"),
    "export.main_table": (_EXPORT, HARD, "退出码 4"),
    "export.review": (_EXPORT, HARD, "退出码 5 / 3"),
    "export.route": (_EXPORT, HARD, "退出码 6"),
    "export.rendered_stale": (_EXPORT, HARD, "退出码 9：渲染稿不是 render 写下的那一份"),
    "export.delivered_check": (_EXPORT, HARD, "退出码 10：交付文本的四项复核"),
    # ── 计划的门（oss run）：plan.<门>.<哪一条>
    "plan.causal.estimand": (_RUN, HARD, "因果计划没写估计量三步"),
    "plan.causal.estimand_incomplete": (_RUN, HARD, "估计量三步没写全"),
    "plan.causal.control_roles": (_RUN, HARD, "控制变量没标角色"),
    "plan.causal.mediator_control": (_RUN, HARD, "控制了中介 / 碰撞变量"),
    "plan.causal.outcome_filter": (_RUN, HARD, "按结局筛样本"),
    "plan.causal.legacy_no_estimand": (_RUN, WARN, "老计划没写估计量三步与控制变量角色"),
    "plan.survey.design_missing": (_RUN, HARD, "有调查信号却没声明抽样设计"),
    "plan.survey.design_missing_legacy": (_RUN, WARN, "有调查信号却没声明抽样设计（老计划没写研究类型，只警告）"),
    "plan.survey.reason_and_design": (_RUN, HARD, "写了不按调查处理的理由又写了抽样设计"),
    "plan.survey.column_missing": (_RUN, HARD, "权重 / 分层 / 初级抽样单位列不在数据里或整列缺失"),
    "plan.survey.cluster_vs_psu": (_RUN, WARN, "聚类层级跟初级抽样单位不同"),
    "plan.survey.strata_without_psu": (_RUN, WARN, "只写了分层没写初级抽样单位"),
    "plan.descriptive.describe_causal": (_RUN, HARD, "describe 估计量却声明为因果"),
    "plan.descriptive.describe_extras": (_RUN, HARD, "describe 计划写了用不上的回归项"),
    "plan.descriptive.estimand": (_RUN, HARD, "描述类计划没写描述量与目标总体"),
    "plan.descriptive.kind_missing": (_RUN, HARD, "describe 没写 description.kind"),
    "plan.descriptive.kind_association": (_RUN, HARD, "条件关联走了 describe"),
    "plan.descriptive.gap_groups": (_RUN, HARD, "组间差没写比较哪几组"),
    "plan.descriptive.trend_over": (_RUN, HARD, "趋势没写看哪段时间"),
    "plan.descriptive.groups_too_many": (_RUN, HARD, "分组超过两列"),
    "plan.descriptive.column_missing": (_RUN, HARD, "描述块写的列不在数据里"),
    "plan.descriptive.individual_change": (_RUN, HARD, "问个体变化却不是面板"),
    "plan.measurement.spec_missing": (_RUN, HARD, "声明为测量的计划没写测量块"),
    # 造尺子的三道门（测量骨架设计稿 5.1）：只核「声明了什么 + 产出在不在」，不读论文里的论证
    "plan.measurement.reliability_only": (_RUN, HARD, "造尺子的产出登记里只有信度，一类效度证据都没有"),
    "plan.measurement.evidence_missing": (_RUN, HARD, "声明了效度证据，产出登记里那几类一类都没有"),
    "plan.measurement.kind_conflict": (_RUN, HARD, "产出里有造尺子才会有的东西，计划却标成用现成量表"),
    # 用尺子的两道门（测量骨架设计稿 5.2）：都只警告——M4 另开一条 must 级处方给出路，不堵死
    "plan.measurement.no_equivalence": (_RUN, WARN, "跨组比较，产出登记里却没有测量等价检验"),
    "plan.measurement.groups_undeclared": (_RUN, WARN, "别处有分组的迹象，跨组比较那一栏却空着"),
    "plan.measurement.source_undeclared": (_RUN, WARN, "source_quality 留空，而别处看得出是自报数据"),
    "plan.measurement.self_report_proxy": (_RUN, WARN, "自报的量没按代理处理"),
    # 环节 4「判可行」的测量类分路（测量骨架设计稿第四节）：不算功效，判「证据做不做得出来」
    "plan.measurement.too_few_indicators": (_POWER, HARD, "造尺子的题目少于三道，给不出结构效度"),
    "plan.measurement.group_missing": (_POWER, HARD, "跨组比较的分组变量不在数据里或只有一组"),
    "plan.measurement.no_criterion_var": (_POWER, WARN, "声明要报效标关联效度，数据里却没有别的变量可对账"),
    # 环节 4「判可行」的仿真类分路（仿真骨架设计稿第三节）：不算功效，判「跑得出可判的东西吗」。
    # **三条都只警告**——单条件与重复不足都是正当研究，正确答案是把话说软，不是不许做
    "plan.simulation.single_condition": (_POWER, WARN, "货箱只有一个条件，做不了条件间比较"),
    "plan.simulation.low_reps": (_POWER, WARN, "重复次数不足，分不开随机波动与真实规律"),
    "plan.simulation.no_benchmark": (_POWER, WARN, "主张合成样本代表真人，声明的真实对照数据却不在"),
    "plan.simulation.state_stale": (_POWER, WARN, "入库档案跟货箱现在的配料单对不上——判的是入库那一刻的货箱"),
    # 仿真骨架的接轴门（仿真骨架设计稿 4.1）：**判据优先看盘上有没有货箱，那是事实，不是声明**
    "plan.simulation.spec_missing": (_RUN, HARD, "声明为仿真的计划没写仿真块"),
    "plan.simulation.box_missing": (_RUN, HARD, "声明为仿真却没有入库的货箱——论文期那五项仿真检测一项都不会触发"),
    "plan.simulation.undeclared": (_RUN, HARD, "盘上有货箱，计划却没声明这是仿真研究"),
    "plan.simulation.condition_unknown": (_RUN, HARD, "要比较的条件不在货箱的配料单里"),
    "plan.simulation.conditions_undeclared": (_RUN, WARN, "货箱里不止一个条件，要比较哪几条那一栏却空着"),
    "plan.simulation.degenerate_column": (_RUN, WARN, "合成数据某一列众数占比高到不像人答的——方差被压扁"),
    # 跟真实调查对账（4.3 乙）：只在作者自己配了对照列时才查——不主张代表真人的 ABM 不受打扰
    "plan.simulation.benchmark_column_missing": (_RUN, WARN, "对照列配对里有一边不在数据里"),
    "plan.simulation.variance_mismatch": (_RUN, WARN, "合成数据跟真实数据的方差对不上——压扁或夸大"),
    "plan.simulation.relation_flipped": (_RUN, WARN, "合成数据里两列的关系方向跟真实数据相反"),
    "plan.simulation.output_undeclared": (_RUN, HARD, "数据里货箱的四根钥匙列齐了，却既没声明也没入库——整个骨架唯一的入口漏洞"),
    "plan.estimator.unsupported": (_RUN, HARD, "估计量还不支持"),
    "plan.iptw.no_controls": (_RUN, HARD, "iptw 没有控制变量——倾向得分无从谈起"),
    "plan.iptw.treatment_not_binary": (_RUN, HARD, "iptw 的处理变量不是 0/1"),
    "plan.iptw.separated": (_RUN, HARD, "倾向得分的 logit 完全可分：有人根本没有可比的对照"),
    "plan.iptw.no_overlap": (_RUN, HARD, "两组倾向得分没有重叠——没有共同支撑域"),
    "plan.estimator.statspai_missing": (_RUN, HARD, "did_cs 要 StatsPAI，没装"),
    "plan.columns.required_missing": (_RUN, HARD, "必填的列没填（结果变量、ols_fe 的处理变量）"),
    "plan.columns.not_in_data": (_RUN, HARD, "计划写的列不在数据里"),
    "plan.columns.missing_code_labeled": (_RUN, HARD, "进回归的列里还留着取值标签写明是缺失的码"),
    "plan.columns.missing_code_suspected": (_RUN, WARN, "进回归的列里有分布上像缺失码的取值"),
    "plan.interaction.component_missing": (_RUN, HARD, "交互项的分量不在数据里"),
    "plan.interaction.treatment_unlisted": (_RUN, HARD, "处理变量是交互项却没列进 interactions"),
    "plan.interaction.lower_order": (_RUN, WARN, "交互项缺低阶项"),
    "plan.interaction.iv_unsupported": (_RUN, HARD, "iv 下写了交互项——原先被静默吞掉，现在明拒"),
    "plan.robustness.too_many": (_RUN, WARN, "稳健性超过 12 项"),
    "plan.did.field_missing": (_RUN, HARD, "双重差分一族没指定个体、时间或处理起始时间列"),
    "plan.did.column_missing": (_RUN, HARD, "双重差分一族的个体、时间或处理起始时间列不在数据里"),
    "plan.did.staggered": (_RUN, WARN, "分批实施却不用 did_cs"),
    "plan.variation.within_zero": (_RUN, HARD, "加了固定效应后核心变量组内无变异"),
    "plan.variation.constant": (_RUN, HARD, "核心变量取值恒定"),
    "plan.cluster.missing": (_RUN, WARN, "没指定聚类层级"),
    "plan.cluster.few": (_RUN, WARN, "聚类数少于 30"),
    "plan.research_type.missing": (_RUN, WARN, "计划没声明研究类型"),
    # ── logit / ologit（2026-09-25）。设计稿 docs/design/2026-09-25-logit-multilevel.md
    "plan.logit.outcome": (_RUN, HARD, "logit 的结局不是 0/1"),
    "plan.logit.fixed_effects": (_RUN, HARD, "logit 的固定效应每组观测太少（incidental parameters）"),
    "plan.logit.separated": (_RUN, HARD, "完全分离：某一列把结局完全分开了"),
    "plan.logit.not_converged": (_RUN, HARD, "logit 没收敛"),
    "plan.ologit.outcome": (_RUN, HARD, "有序 logit 的结局不足三个取值"),
    "plan.ologit.fixed_effects": (_RUN, HARD, "有序 logit 带固定效应（切点与组内截距不可识别）"),
    "plan.ologit.fit": (_RUN, HARD, "有序 logit 拟合失败"),
    # ── 多层模型（2026-09-25）。M1 挡的不是技术错误，是用错工具
    "plan.mixed.spec_missing": (_RUN, HARD, "estimator: mixed 没写 plan.mixed"),
    "plan.mixed.rationale_missing": (_RUN, HARD, "多层模型没说为什么用它"),
    "plan.mixed.rationale_se": (_RUN, HARD, "多层模型只为处理聚类——该用 ols_fe + cluster"),
    "plan.mixed.few_groups": (_RUN, HARD, "二层单位少于 10 个"),
    "plan.mixed.unavailable": (_RUN, HARD, "没装 statsmodels"),
}
