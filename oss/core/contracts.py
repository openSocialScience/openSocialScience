"""所有产出物的格式定义。

**这是阶段 0 的核心产出。**每个环节吃什么、吐什么，全在这里定死。
换真实现的时候只换实现，不换格式——所以假数据和真数据必须是同一套 schema。

产出物与归属（见 DECISIONS.md 第八章「算 / 判 / 回验」）：

    profile.json      工具箱写   算
    literature.json   工具箱写   算
    ideas.json        skill 写   判        ← 判断的产物
    binding.json      工具箱写   算 + 回验
    power.json        工具箱写   算
    plan.json         skill 写   判        ← 判断的产物
    results.json      工具箱写   算
    paper.md          skill 写   判        ← 判断的产物
    trace.jsonl       宿主层写
"""

from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

# --------------------------------------------------------------------- 基类


def now() -> datetime:
    return datetime.now(timezone.utc)


class Artifact(BaseModel):
    """所有落盘产出的基类。

    统一负责读写，这样「谁写谁读」的约定只有一个实现，
    不会出现某个环节自己拼 json.dump 然后格式漂掉。
    """

    model_config = {"extra": "forbid"}

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写：先写临时文件再 replace，避免中断留下半个文件
        # （DECISIONS.md 4.10 失败语义第 4 条）
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, path: Path):
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------- ① 数据画像

SemanticType = Literal[
    "continuous", "binary", "categorical", "ordinal",
    "identifier", "date", "text", "weight",
]

StructureKind = Literal[
    "cross_section",
    "panel_balanced",
    "panel_unbalanced",
    "repeated_cross_section",
    "time_series",
    "unknown",
]


class VariableProfile(BaseModel):
    name: str
    label: str = Field(default="", description="来自 .dta/.sav 的变量标签——免费的语义信息")
    dtype: str = ""
    semantic_type: SemanticType = "continuous"
    n_missing: int = 0
    missing_rate: float = 0.0
    missing_pattern: str = ""
    n_unique: int = 0
    distribution: dict[str, float | int | str] = Field(default_factory=dict)
    value_labels: dict[str, str] = Field(
        default_factory=dict, description="取值到含义的映射，来自 .dta/.sav"
    )
    within_variation: float | None = Field(
        default=None,
        description="面板的组内变异。为 0 意味着固定效应会把这一列吃掉——回归里它是常数",
    )

    @property
    def coverage(self) -> float:
        """非缺失比例。绑定时用它判断这一列撑不撑得起一个变量。"""
        return round(max(0.0, 1.0 - self.missing_rate), 4)


class DataStructure(BaseModel):
    kind: StructureKind = "unknown"
    unit_id: str | None = None
    time_id: str | None = None
    n_units: int | None = None
    n_periods: int | None = None
    balance_ratio: float | None = None
    nesting_levels: list[str] = Field(
        default_factory=list,
        description="嵌套层级，如 ['个人', '家庭', '社区']。多层模型与层次匹配检查的依据",
    )
    detection_note: str = ""


class FileInfo(BaseModel):
    path: str
    format: str = ""
    n_rows: int = 0
    n_cols: int = 0
    size_bytes: int = 0
    sha256: str = Field(default="", description="内容哈希。复现时用它确认「用的是同一份数据」")


class SurveySignals(BaseModel):
    """画像认出的调查信号（调查层设计稿 3.1）。**一律是疑似**——确认在计划的 `survey` 里做。

    数据级宁严勿松：任何一样有信号，`likely` 就为真，后面每一环都按调查数据处理；
    列级谨慎：哪列是权重、哪列是层，不确认就不当真（地基设计稿第 9 条）。
    """

    likely: bool = Field(default=False, description="权重、分层、抽样单位、插补标记，有任何一样就为真")
    weight_candidates: list[str] = Field(default_factory=list, description="疑似权重列（横截面与面板权重常同时在）")
    strata_candidates: list[str] = Field(default_factory=list, description="疑似分层列")
    psu_candidates: list[str] = Field(default_factory=list, description="疑似初级抽样单位列")
    imputation_flags: dict[str, str] = Field(
        default_factory=dict, description="疑似插补标记列 → 它标的那一列（认不出标的是哪列就空串）")


class DataProfile(Artifact):
    profile_id: str = ""
    file: FileInfo = Field(default_factory=FileInfo)
    variables: list[VariableProfile] = Field(default_factory=list)
    structure: DataStructure = Field(default_factory=DataStructure)
    all_missing_columns: list[str] = Field(default_factory=list)
    constant_columns: list[str] = Field(
        default_factory=list, description="整列同一个值。放进回归会被吸收掉"
    )
    weight_column: str | None = Field(default=None, description="检出的抽样权重列")
    survey: SurveySignals = Field(default_factory=SurveySignals, description="调查信号（都是疑似，见 SurveySignals）")
    sensitive_columns: list[str] = Field(
        default_factory=list, description="疑似个人敏感信息，伦理审查要看"
    )
    suspected_missing_codes: dict[str, list[tuple[float, int]]] = Field(
        default_factory=dict,
        description="疑似缺失值代码：列名 → [(取值, 出现次数)]。调查数据里 99=拒答、"
        "-99=缺失这类约定，**在文件里就是普通数字**，算进平均数就全错且不报错。"
        "只报不改——把一列抹成缺失是用户的判断，不是工具的",
    )
    labeled_missing_codes: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description="上面那些码里，**文件自己写明含义**的那部分：列名 → {取值: 标签}。"
        "`.sav`/`.dta` 的取值标签直接写着「不适用」「拒绝回答」——那不是推断，是原文。"
        "写 prep.py 时照这个改成缺失即可；不在这里的才需要问用户",
    )
    warnings: list[str] = Field(default_factory=list)

    def var(self, name: str) -> VariableProfile | None:
        return next((v for v in self.variables if v.name == name), None)

    def shape_summary(self) -> str:
        """只给「形状」，不给**实质变量**的清单。

        这是 DECISIONS.md 5.1 的落点：阶段 B 合成 idea 时只让模型看见形状，
        看见变量名它就会顺着 `internet_use` 和 `income` 凑问题——那是数据在决定问题。

        **边界在哪**：结构列（个体标识、时间、层级）可以出现，实质变量不行。
        知道时间列叫 `year` 不会让模型去凑问题；知道有一列叫 `income` 会。
        结构列反而必须给——不然模型判断不了层次匹配和期数够不够。
        """
        s = self.structure
        lines = [f"结构：{s.kind}"]
        if s.n_units and s.n_periods:
            lines.append(f"规模：{s.n_units} 个单位 × {s.n_periods} 期")
        elif s.n_units:
            lines.append(f"规模：{s.n_units} 个观测")
        lines.append(f"变量数：{len(self.variables)}（清单在此阶段不提供）")
        if s.nesting_levels:
            lines.append(f"嵌套层级：{' ← '.join(s.nesting_levels)}")
        if s.detection_note:
            lines.append(f"判定依据：{s.detection_note}")
        return "\n".join(lines)


# ----------------------------------------------------------------- ② 文献

class PaperRecord(BaseModel):
    paper_id: str
    title: str
    year: int | None = None
    authors: list[str] = Field(default_factory=list)
    doi: str = ""
    venue: str = Field(default="", description="期刊 / 会议名。参考文献条目没有它就是半截的")
    volume: str = ""
    issue: str = ""
    pages: str = Field(default="", description="起止页，如 1–25。没有卷期页，Chicago 样式会排成 ahead of print")
    abstract: str = ""
    citation_count: int = 0
    source: str = Field(default="", description="哪个源返回的：openalex / crossref / semanticscholar")
    oa_url: str = Field(default="", description="开放获取的 PDF 链接。**有它才拿得到全文**")
    tldr: str = Field(default="", description="Semantic Scholar 的一句话总结，没有就空")
    camp: str = Field(default="", description="哪个理论阵营的检索式找到的（`阵营: 检索式` 写法）。空白地图按阵营分组用")
    screen: str = Field(default="", description="模型筛选判定：纳入 / 排除；空 = 还没筛")
    screen_reason: str = Field(default="", description="排除的理由，一句话")
    via: str = Field(default="", description="怎么进语料的：空 = 检索命中；snowball:backward:W… = 某种子的参考文献；"
                                            "snowball:forward:W… = 引用了某种子；add = oss lit add 补入")
    language: str = Field(default="", description="zh / en / 空=没判。导入与中文历史层写 zh；论文阶段据此放开「中文未覆盖」规则")
    # ── 非拉丁文字的文献进英文稿时用。Social Forces 的范文里一个汉字都没有；
    #    非英文文献的写法是「原题（拉丁字母）[英译]」——德文那条就是这样排的。
    #    中文题名要先罗马化（拼音）再给英译，**原题留在 `title` 里不动，任何人都能对着核**。
    title_romanized: str = Field(default="", description="原题的罗马化（中文即拼音）。英文稿排参考文献时放在前面")
    title_en: str = Field(default="", description="题名的英译，排在方括号里。**原题仍留在 title**，好核")
    venue_en: str = Field(default="", description="刊名的英文：有官方英文名就用官方的（中国人口科学 → Chinese Journal of Population Science）")
    authors_romanized: list[str] = Field(default_factory=list, description="罗马化的作者名，顺序与 authors 一致")


class SnowballStats(BaseModel):
    """滚雪球的总账：从哪几篇出发、各拉进多少。每条记录自己的来路在 `PaperRecord.via`。"""

    seeds: list[str] = Field(default_factory=list, description="出发的 OpenAlex W 号，累计")
    added: int = Field(default=0, description="累计新增")
    backward: int = Field(default=0, description="往后拉（种子的参考文献）进来的，累计")
    forward: int = Field(default=0, description="往前拉（引用种子的）进来的，累计")
    rounds: int = Field(default=0, description="滚了几轮；一个种子都没换到 W 号、一次引文调用都没打的空轮不计")


class LiteratureCorpus(Artifact):
    corpus_id: str = ""
    queries: list[str] = Field(default_factory=list)
    camps: dict[str, str] = Field(
        default_factory=dict,
        description="检索式 → 理论阵营。评审第 7 条：威权回应性、数字威权主义、民主与健康三群文献整群缺失——"
        "检索式按阵营各出一条，哪个阵营 0 命中一眼看得见")
    hits_by_query: dict[str, int] = Field(default_factory=dict, description="每条检索式（去重前）命中多少")
    records: list[PaperRecord] = Field(default_factory=list)
    hits_by_source: dict[str, int] = Field(default_factory=dict)
    abstract_coverage: float = Field(
        default=0.0, description="带摘要的比例。低于阈值时抽不出作者自陈的局限"
    )
    citation_coverage: float = Field(
        default=0.0, description="带引文的比例。为 0 则召回率探针不可执行"
    )
    relaxed: bool = Field(
        default=False,
        description="是否因命中不足 MIN_HITS 而自动降档过（原式、去引号、丢块）",
    )
    topical_relevance: float = Field(
        default=0.0,
        description="主题相关度中位数（检索式的概念块在标题+摘要里的命中比例）。"
        "**覆盖率和相关性是两回事**——一个语料可以 100% 覆盖、100% 跑题",
    )
    off_topic_count: int = Field(
        default=0, description="与检索式零主题重合的条数"
    )
    screened: bool = Field(default=False, description="模型是否逐篇筛过（oss lit screen 通过后为真）")
    n_excluded: int = Field(default=0, description="筛掉的篇数。精读排序只看纳入的")
    snowball: SnowballStats = Field(default_factory=SnowballStats,
                                    description="滚雪球的总账；rounds 为 0 表示没滚过")
    notes: list[str] = Field(default_factory=list)



class ScreenVerdict(BaseModel):
    """一篇文献的筛选判定。**每篇都要判**，排除的必须写理由——
    「沾了个词」的无关文献不筛掉，空白地图就建在它们上面。"""

    paper_id: str
    verdict: str = Field(description="纳入 / 排除")
    reason: str = Field(default="", description="一句话。排除的必须写")


class Screening(Artifact):
    direction: str = ""
    verdicts: list[ScreenVerdict] = Field(default_factory=list)


class LimitationQuote(BaseModel):
    """作者**自己承认**的一条局限——必须是原句，能在拿到的文本里找到。"""

    quote: str = Field(description="原文原句，逐字。校验会去全文/摘要里找它")
    where: str = Field(default="", description="在哪：Discussion / Limitations / Abstract …")


class ReadingCard(BaseModel):
    """一篇文献的读书卡。**局限那一栏必须引原句**——这是空白地图的原料。

    借自 nature-reader 的「源锚点」纪律：任何结论都得能指回原文某一段；
    借自 nature-literature-pipeline 的「来源层级」：全文 / 仅摘要 / 仅元数据要标清，
    因为一张只读了摘要的卡，它的「局限」栏几乎必然是空的——那不是文献没局限，是没读到。
    """

    paper_id: str
    source_level: str = Field(description="fulltext / abstract / metadata——这张卡是读什么写的")
    question: str = Field(default="", description="这篇文献回答什么问题")
    data: str = Field(
        default="",
        description="用的什么数据（**有名字就写名字**、年份、样本）。"
        "「26 国人口与健康调查（DHS）」「CFPS 2010–2020 四轮」，不是「多国生育调查」。"
        "**这一栏是缺数据时唯一有出处的线索**——一篇真发表的论文真用过它，"
        "说明它确实存在、且本领域真在用；写虚了，开数据清单时就只剩模型凭记忆猜",
    )
    design: str = Field(default="", description="识别策略 / 估计方法")
    finding: str = Field(default="", description="主要发现，带方向和量级")
    limitations: list[LimitationQuote] = Field(
        default_factory=list, description="作者自陈的局限，**逐字引原句**")
    relevance: str = Field(default="", description="跟本研究方向的关系：直接 / 边缘 / 方法借鉴")
    notes: str = Field(default="", description="读的时候拿不准的地方")


class ReadingCards(Artifact):
    direction: str = ""
    cards: list[ReadingCard] = Field(default_factory=list)


# ----------------------------------------------------------- 风格包的硬规矩


class PageLayout(BaseModel):
    """页面级版式：页边距、页眉页脚、页码、单双面、分栏、行号。

    **这一层原先整个不存在。**段落级的档位（字体、字号、行距、缩进）有 `_ZH_LAYOUT`
    那张表，而页面级的东西全在 docx 的 `sectPr` 里，风格包只能手工塞一个
    `template.docx` 进去——于是「这本刊要求奇偶页不同页眉」「要求双面印刷」
    既没法声明，也没法校验，跟这个项目「声明出来、工具去建、还能核对」的做法相反。

    **须知没提的一律留空 / None，对应的设置就不动。**猜一个页边距写进去，
    比不写更糟：读者会以为这是该刊的要求。
    """

    margin_top_cm: float | None = None
    margin_bottom_cm: float | None = None
    margin_left_cm: float | None = None
    margin_right_cm: float | None = None
    two_sided: bool = Field(
        default=False,
        description="双面印刷：页边距镜像（内侧/外侧而不是左/右），且奇偶页可用不同页眉。"
        "单面（缺省）时 margin_left/right 就是字面的左右")
    header_odd: str = Field(default="", description="奇数页页眉的文字。支持 {journal}、{title}、{year}")
    header_even: str = Field(default="", description="偶数页页眉的文字；只在 two_sided 为真时生效")
    footer_odd: str = ""
    footer_even: str = ""
    page_number: str = Field(
        default="",
        description="页码放哪：空串＝不加；否则 header/footer 加 -left/-center/-right，"
        "如 footer-center。**页码只走这一格**，别写进 header_/footer_ 的文字里——"
        "那样出来的是死字符，不随页数变")
    page_number_format: str = Field(default="arabic", description="arabic / roman-lower / roman-upper")
    page_number_start: int | None = Field(default=None, description="起始页号；None 用 Word 缺省的 1")
    columns: int = Field(default=1, description="分栏数。中文期刊排印常用两栏，投稿稿多为一栏")
    column_gap_cm: float | None = None
    line_numbers: bool = Field(
        default=False, description="连续行号（有些刊要求便于审稿人逐行指出）")
    paper_size: str = Field(
        default="",
        description="纸张，如「A4」「Letter」。留空不动（pandoc 模板缺省是 Letter）")
    body_font: str = Field(
        default="", description="正文的中文字体，如「宋体」「仿宋」。留空用缺省")
    body_size: str = Field(
        default="",
        description="正文字号，按须知的说法写：「小四」「小4号」「12pt」都认。"
        "跟 `footnote_size` 同一条规矩——**照抄须知的字号名，不要自己折成磅**")
    footnote_font: str = Field(
        default="",
        description="注文的中文字体，如「仿宋」「宋体」。留空用缺省（中文稿缺省仿宋）")
    footnote_size: str = Field(
        default="",
        description="注文的字号，按须知的说法写：「五号」「小五」「9pt」都认。"
        "**照抄须知的字号名，不要自己折成磅**——五号＝10.5 磅是国标里的固定对应，"
        "工具查表换算；而「36 字 × 35 行」那种要靠字体度量推算的，不属于这一格")
    footnote_restart_each_page: bool = Field(
        default=False,
        description="脚注每页重新编号。《中国社会科学》的须知原句是「注释请用页下注（每页单独编号）」——"
        "**Word 缺省是全文连续编号**，不显式设置就跟须知不符，而这一条肉眼很难发现")


class StyleRules(Artifact):
    """一本期刊的硬规矩。**skill 读作者须知填，工具核对每个数字在须知原文里有。**

    检测是通用的，数字是每本刊自己的。须知没提的留 None，对应的检测不生效。
    """

    journal: str = ""
    word_limit: int | None = Field(default=None, description="全文字数上限")
    word_min: int | None = Field(
        default=None,
        description="全文字数下限。**中文期刊的字数要求常常是区间**"
        "（「学术论文以 10000-20000 字为宜」），只填上限会漏掉「写得太短」这一半。"
        "「为宜」是建议不是硬杠，所以低于下限只出警告，不拦导出；"
        "「不超 X 字」那种硬上限仍然拦")
    word_limit_includes_references: bool = Field(
        default=True, description="上限含不含参考文献。Social Forces 含（连注释一起）")
    max_tables_figures: int | None = Field(default=None, description="表 + 图面板的总数上限")
    double_blind: bool = Field(default=False, description="双盲：正文不许出现致谢、基金、作者信息")
    abstract_max_words: int | None = None
    abstract_no_citations: bool = Field(default=False, description="摘要不含引用")
    reference_style: str = Field(default="", description="录用后要求的引用格式，如 Chicago 17")
    supplementary_max_pages: int | None = None
    figures_inline: bool = Field(default=True, description="图表内嵌正文（投稿时）")
    figure_max_width: float | None = Field(
        default=None,
        description="图的最大宽度（英寸），一般就是该刊的版心宽度。**Word / PDF 里图的物理宽度就是画布宽度**，"
        "超了会被整体缩放、印出来的字号跟着变小。留 None 用缺省 4.33 英寸"
        "（Social Forces 19 张真图的中位宽度）")
    layout: PageLayout | None = Field(
        default=None,
        description="页面级版式。给了就**自动生成** Word 模板，不必手工塞 template.docx；"
        "包里同时有 template.docx 时以手工那份为准（手工的能表达的东西更多）")
    other_requirements: list[str] = Field(
        default_factory=list, description="须知里有、但上面几类检测不覆盖的规矩，原句抄进来，写作时照做")

# ------------------------------------------------------------- ③ idea（skill 写）

#: 研究类型（docs/design/2026-09-13-research-types.md）：**按「想下什么结论」分，不按「用了什么方法」分**——
#: 想说「X 影响 Y」就是 causal，哪怕只用普通回归。空串 = 没声明（老文件都是它，识别审计按估计量兜底）。
ResearchKind = Literal["causal", "descriptive", "measurement", "simulation"]
ResearchType = Literal["", ResearchKind]
#: `also` 的取值：不收 causal——非因果类型的正文不许有因果说法，论文只要有一部分下因果结论，主类型就是 causal
ResearchPart = Literal["descriptive", "measurement", "simulation"]
#: 取值只写在上面的 Literal 里一处；元组由它推出——几处各写一遍，日后漂开会让锚点锁定静默失效
RESEARCH_TYPES: tuple[str, ...] = get_args(ResearchKind)
RESEARCH_TYPE_LABELS: dict[str, str] = {"causal": "因果", "descriptive": "描述",
                                        "measurement": "测量", "simulation": "仿真"}


class Idea(BaseModel):
    idea_id: str
    title: str
    research_question: str
    hypothesis: str = ""
    mechanism: str = ""
    identification_strategy: str = ""
    constructs_needed: list[str] = Field(default_factory=list)
    contribution_type: str = ""
    theory_hook: str = Field(default="", description="进入哪个理论对话。空值是强负面信号")
    value_if_null: str = Field(
        default="", description="结果为空时这研究还成立吗。答不上说明价值绑在结果方向上"
    )
    needs_extra_data: str = Field(
        default="", description="非空表示这份数据做不了，但值得做——缺什么写在这里"
    )
    lens: str = Field(
        default="",
        description="哪个分析透镜产出的。丢了这个就没法回答「哪个角度产出高」，"
        "也没法在某个透镜系统性产出垃圾时把它关掉",
    )
    jury_verdict: str = Field(
        default="",
        description="跨模型陪审团的判词。**被砍掉的候选也要留在文件里**——"
        "它是下次探索时的反重复记忆，删掉就白挖了一次",
    )
    jury_reason: str = Field(default="", description="陪审团给的理由，原文照抄不要转述")
    transfer_rationale: str | None = Field(
        default=None,
        description="context-transfer / temporal-shift 透镜的**强制检测**："
        "为什么结果应当不同的理论论证。"
        "「没人在 Z 国做过」不是贡献；「理论假设了 X，而 Z 不成立，所以符号该反转」才是。"
        "这两个透镜产出的候选，此字段为 None 即标为高风险（但不淘汰——淘汰是陪审团的事）",
    )
    source: Literal["model", "user"] = Field(
        default="model",
        description="这条 idea 是谁出的。`model` = 链条自己找空白找出来的（缺省，老文件都是它）；"
        "`user` = 用户在输入里用 `--- idea:` 自带的想法，链条只整理、不替换。"
        "**两条行为指标（behavior-idea-kept / behavior-jury-recorded）只在 user 时判**，"
        "所以取值是 Literal 不是自由字符串：拼错一个字，那两条会静默变成 n/a，"
        "而 n/a 看起来跟「这次不是自带想法」一模一样",
    )
    research_type: ResearchType = Field(
        default="",
        description="这道题想下什么结论：causal（X 影响 Y）/ descriptive（分布与趋势）/ "
        "measurement（新量表、新指标）/ simulation（跑仿真）。**按结论定，不按方法定**；"
        "陪审团判标得对不对。空串 = 老文件，没声明",
    )
    source_lens: str = Field(
        default="",
        description="发散段 ③ 的四个起点之一：anomaly（异常现象）/ identification（识别机会）/ "
        "theory（理论张力）/ literature（文献局限）；整理模式为 user-idea。"
        "跟 `lens` 并存不是重复——`lens` 答「怎么问出来的」，这一栏答「从哪儿起的」，"
        "丢掉任一个都没法回答「哪条路产出高」，也没法在某条路系统性产出垃圾时把它关掉。"
        "空串 = 老文件",
    )
    prescreen: dict = Field(
        default_factory=dict,
        description="⑤ 经验预检的证据：{bound, mde, coverage, note}。**这是排序依据，"
        "不是淘汰依据**——绑不上、功效不够的进清单乙，不降权（设计稿不变量三）",
    )
    novelty_check: str = Field(
        default="",
        description="⑥ 定向查重的结论原文。空 = 这一步没做；"
        "**检索没召回不等于没人做过**，判词照抄不要转述",
    )


class IdeaSet(Artifact):
    direction: str = ""
    ideas: list[Idea] = Field(default_factory=list)
    generated_n: int = Field(
        default=0,
        description="发散段 ③ 产出的候选数。收敛段之后条数应当**一直等于它**："
        "多出来说明 ④ 拿着完整变量清单回头造题（数据出题），"
        "少下去说明被砍的候选被删出了文件（那是下次探索的反重复记忆）。"
        "0 = 老文件没声明，判据不适用",
    )
    unreachable: list[str] = Field(
        default_factory=list,
        description="清单乙：更值得做、但这份数据做不了的候选编号。**仍留在 `ideas` 里且不降权**，"
        "每条要在 `needs_extra_data` 写清缺什么、去哪拿（设计稿不变量三）。"
        "空 = 本次没有这类候选，或这一步没做——两者分不出来，判据不适用",
    )


# ------------------------------------------------------------- ④ 绑定与功效

BindingStatus = Literal["bound", "proxy", "derived", "missing"]
VariableRole = Literal[
    "treatment", "outcome", "control", "unit", "time", "instrument", "mediator", "moderator"
]


class VariableBinding(BaseModel):
    # 字段名用 construct_name 而不是 construct——后者会遮蔽 BaseModel 的属性
    construct_name: str
    role: VariableRole
    column: str | None = None
    status: BindingStatus = "missing"
    coverage: float = 0.0
    derivation: str = Field(default="", description="派生变量的构造式，如 urban_inc / rural_inc")
    note: str = ""
    # 代理差距三栏。评审第 4 条致命问题：理论要的是「未审查的政治信息」，测的是「上网人口比例」，
    # 而且偏差与政体类型相关——「这不是 limitation，是理论和测量对不上」。
    # 三栏由执行者填（判断），工具只查填没填；论文的方法节按这三栏写那句话。
    construct_def: str = Field(default="", description="理论要的是什么：这个构念在假设里指的是什么")
    measures: str = Field(default="", description="这一列实际测的是什么（口径、来源、单位）")
    gap: str = Field(default="", description="两者的差距，以及它会让估计偏向哪边（跟处理 / 调节变量相关吗）")

    @property
    def is_usable(self) -> bool:
        return self.status in ("bound", "proxy", "derived")


class BindingReport(Artifact):
    idea_id: str = ""
    bindings: list[VariableBinding] = Field(default_factory=list)
    core_ok: bool = Field(default=False, description="treatment 与 outcome 是否都绑上了")
    blocking: list[str] = Field(default_factory=list, description="挡住执行的具体原因")


Verdict = Literal["pass", "gray", "fail"]
"""三态判词。

`gray` 是 DECISIONS.md 6.2 的落点——两头定死、中间跨模型：
落进灰区的不由工具箱下结论，交给跨模型判，且必须给理由。
"""


class PowerReport(Artifact):
    idea_id: str = ""
    effect_size_raw: float = 0.0
    effect_size_adjusted: float = Field(
        default=0.0, description="打过 EFFECT_SIZE_HAIRCUT 折之后的值"
    )
    effect_metric: str = Field(
        default="d",
        description="效应量的度量：d / r / beta（描述类按精度判时还可以是 raw：有意义的差别按结局原单位给，"
        "按结局的加权标准差折成标准差单位）。**换度量就要换公式**——"
        "不声明而硬套两样本均值差公式，传进来一个 r 就会安静地给出错的功效",
    )
    effect_size_source: str = Field(
        default="",
        description="效应量是从哪儿来的（哪条语料记录、哪一页、原话）。**判词整个建立在这个数上，"
        "所以它比 ρx 更需要出处**——空着就说明没人选过这个数，判词不许给 pass，降级为灰区。"
        "验收跑 r20260907_1938 与 r20260907_edufert_zh 都没传 --effect，"
        "拿命令行缺省的 0.2 算出「功效 0.83 / pass」，论文却写「按文献报告的效应量」——那句话是假的",
    )
    haircut: float = 0.6
    n_raw: int = Field(default=0, description="原始观测数")
    n_effective: int = Field(
        default=0, description="按设计效应打折后的有效样本量。聚类会让它明显小于 n_raw"
    )
    n_clusters: int | None = Field(default=None, description="聚类单位数")
    icc: float = Field(default=0.0, description="组内相关系数。设计效应靠它算，不给就无法复核")
    treatment_icc: float = Field(
        default=1.0,
        description="**处理变量**的组内相关 ρx。设计效应是 1+(m-1)·ρx·ρu（Moulton），"
        "取 1 相当于假定处理变量在簇内完全不变——个体层面的自变量远小于 1，"
        "取 1 会系统性低估功效",
    )
    treatment_icc_source: str = Field(
        default="", description="ρx 是算出来的还是假定的。判词要写进论文局限，来源必须可核"
    )
    design_effect: float = Field(
        default=1.0, description="**整群**设计效应：`1 + (每簇均值 − 1) × ρx × ρu`。字段名沿用，下游读 JSON 的都用它")
    weight_design_effect: float = Field(
        default=1.0, description="权重设计效应（Kish）：n ÷ ((Σw)² / Σw²)。有效样本量 = n ÷（整群 × 权重）")
    weight_column: str = Field(default="", description="算权重设计效应用的权重列；空串 = 没按权重折")
    mde: float = Field(
        default=0.0,
        description="最小可测效应（Cohen's d，目标功效 0.8）：这份数据最小能测出多大的效应。"
        "**只取决于有效样本量，不随 effect_size 变**——所以它是 ⑤ 经验预检给多个候选分诊时"
        "唯一不需要先有效应量估计的数，判的是「这题在这份数据上测不测得出来」。"
        "0 = 老报告没有这一栏，或有效样本量为 0",
    )
    mode: Literal["power", "precision", "evidence", "readiness"] = Field(
        default="power", description="power 按功效判（估一个效应）；precision 按精度判（描述研究，描述骨架设计稿第四节）——精度不足不停；"
        "evidence 判「证据做不做得出来」（测量研究，测量骨架设计稿第四节）——不算效应量、不出功效判词，"
        "做不出来的当场停，所以落了盘的 evidence 报告判词恒为 pass；"
        "readiness 判「这个模型跑得出可判的东西吗」（仿真研究，仿真骨架设计稿第三节）——"
        "同样不算功效，三条都只警告不停（单条件与重复不足都是正当研究，把话说软即可），"
        "所以 readiness 报告判词也恒为 pass")
    precision_halfwidth: float = Field(default=0.0, description="precision：主要比较的 95% 区间半宽，以标准差为单位")
    min_cell_n_eff: float = Field(default=0.0, description="precision：最小分组的有效样本量；没给分组列时为 0")
    min_cell_label: str = Field(default="", description="precision：最小分组是哪一格")
    alpha: float = 0.05
    power: float = 0.0
    verdict: Verdict = "gray"
    gray_band: tuple[float, float] = (0.5, 0.9)
    discipline: str = ""
    sensitivity: str = Field(
        default="",
        description="判词对假定参数（折价、ρu、灰区上下界）有多敏感。**这一段要写进论文**——"
        "「改这个参数就能改判词」这条指控只能用可核对的表回应，辩解没有用",
    )
    sensitivity_table: dict[str, list[tuple[float | str, float, str]]] = Field(
        default_factory=dict,
        description="参数取值 → (取值, 功效, 判词)，读者能逐格复算。"
        "**第一列允许是字符串**：扫学科灰区时那一列是学科名（sociology…），"
        "不是刻度。原先限死成数字，代码只好存序号，"
        "表上就打出「取 3 时翻为 fail」——读者会把那个 3 当成参数取值，"
        "而它其实是 psychology",
    )
    verdict_fragile: bool = Field(
        default=False, description="是否存在某个假定参数，改动它就会翻盘"
    )
    reason: str = Field(default="", description="灰区时由跨模型给出的理由")


# ------------------------------------------------------- ⑤ 分析计划（skill 写）与结果

class RobustnessSpec(BaseModel):
    """一项稳健性检验：**主计划之上的一个覆盖补丁**。

    这个字段原本是一串散文（"R1 换结果变量：以职业声望替代 SEI"）。
    但 `robustness` 存在的全部理由是**事前锁定、事后不许改**——
    锁一份没人能执行、没人能核对的散文，等于没锁：
    真跑的时候照样想跑什么跑什么，事后再写一段话说「我跑了这些」。

    改成补丁之后，`oss run` 会**跟主规格同一次**把它们全跑掉，
    结果一并写进 `results.json`，论文可以直接引用具体数值。
    （PLAN.md 九之一 ⑤）

    `None` 表示这一项不覆盖，沿用主计划。
    """

    label: str
    rationale: str = Field(default="", description="为什么要做这项检验")
    outcome: str | None = None
    treatment: str | None = None
    controls: list[str] | None = None
    fixed_effects: list[str] | None = None
    cluster_level: str | None = None
    weights: str | None = Field(
        default=None,
        description="覆盖抽样权重列。**空串是「这一项不加权」**（调查数据最常见的一项"
        "稳健性就是不加权复核），`None` 才是「沿用主计划」——两者不能混",
    )
    sample_filter: str | None = None
    interactions: list[str] | None = None
    executable: bool = Field(
        default=True,
        description="散文式的旧条目跑不了。**静默接受等于假装锁上了**，所以要标出来",
    )


class DesignLock(BaseModel):
    """用户用 `--- design:` 锁死的设计键，**原样抄一份进 `plan.json`**。

    只有五个键，都可缺省；键名跟 `AnalysisPlan` 本体**一一对应**——用户那边写
    `fe=firm+year` / `cluster=firm`，抄进来是 `fixed_effects` / `cluster_level`。
    映射表写在 `social-research/rules/input-and-rundir.md`，模型照它抄。

    `extra` 是 `forbid`（`BaseModel` 缺省的 `ignore` 在这里是灾难）：
    模型把用户的写法原样照抄成 `{"fe": [...], "cluster": "firm"}` 是这条路上
    **最可能犯的一个错**，而 `ignore` 会让它变成一个五个键全空的锁——
    锁上了、什么也没锁住、没有任何人会发现。

    **空串 / 空列表 = 这个键没锁**，不参与比对：`design:` 五个键都可缺省，
    缺省的由模型定，那正是这个字段存在的意义——分清「用户定死的」和「模型可以定的」。

    **这不是防篡改。**锁和被锁的是同一份文件，模型两处一起改照样过。
    它防的是**两处写在不同时刻而漂开**：环节 5 抄下用户的键，之后
    `oss route --apply` 打补丁、审稿意见换设计，一路改下来把用户锁的键改掉了，
    而没有人再回头看一眼。
    """

    model_config = {"extra": "forbid"}

    outcome: str = Field(default="", description="用户写的 `outcome=`")
    treatment: str = Field(default="", description="用户写的 `treatment=`")
    fixed_effects: list[str] = Field(
        default_factory=list,
        description="用户写的 `fe=firm+year`，按 `+` 拆成列表。**顺序不算数**——"
        "固定效应是个集合，`firm+year` 与 `year+firm` 是同一个设计")
    cluster_level: str = Field(default="", description="用户写的 `cluster=`")
    estimator: str = Field(
        default="",
        description="用户写的 `estimator=`。只认工具箱清单里的那十一个"
        "（ols_fe、did、did_cs、event_study、iv、iptw、rdd、describe、logit、ologit、mixed）；"
        "不在清单里**停下来告诉用户**，不替换（设计稿第三节）")


#: DID 系估计量：组间处理正是这类设计的本来形状，多处判断都要问「是不是这一族」
#: （绑定门例外见 oss/core/binding.py；识别策略稳健性、聚类层级默认值同一把尺子）
DID_FAMILY: tuple[str, ...] = ("did", "did_cs", "event_study")

#: 真正用上抽样权重的估计量。2026-09-13 查出 did / event_study / rdd 转交 run_ols_fe 时新建的计划
#: 不带权重、iv / did_cs 根本不读，照跑是按不加权算的，诊断写「未加权」而权重门看计划填了就放行，
#: 两处打架、不报错。同日用户定：did、event_study 转交时带上权重（与 statsmodels 的 WLS 对过数），其余拒跑。
#: 2026-09-15 调查层：iv、rdd 按 √w 缩放，did_cs 走 StatsPAI 的单位层面权重——六种都真加权；
#: 同日描述骨架：describe 的各格均值同样按 √w 缩放（跟 run_ols_fe 同一个精确等价）
WEIGHTED_ESTIMATORS: tuple[str, ...] = ("ols_fe", "did", "did_cs", "event_study", "iv", "rdd",
                                        "describe", "logit", "ologit")
#: **`mixed` 故意不在上面那张表里**：多层模型的加权要 level-1 与 level-2 两层权重，
#: 调查数据通常只给一层，硬按一层跑出来的方差分量是错的。写了权重也降级不加权，
#: 并在诊断里说明——见 `analysis.run_mixed` 的门 M3


ControlRole = Literal["confounder", "precision", "mediator", "collider", "other"]


class Estimand(BaseModel):
    """估计量三步（Lundberg 等 2021）：先说要问的量，再说靠什么假设对得上，最后才挑方法。

    **估计方法的选择一律回到这里说理由**——计划里只有方法和变量、没有「问的是什么」，
    审稿人就无从判断方法选得对不对（因果骨架设计稿 3.1）。
    """

    model_config = {"extra": "forbid"}

    unit_quantity: str = Field(default="", description="单位层面的量：例「同一个人，受过与没受过高等教育时的年收入之差」")
    target_population: str = Field(
        default="", description="目标总体：例「2017 年中国 18–60 岁城镇就业人口」。用了抽样调查的，写明权重怎么用")
    assumptions: list[str] = Field(default_factory=list, description="识别假设，逐条列（平行趋势、排他性、无未观测混淆……）")
    link: str = Field(default="", description="经验估计量怎么对上理论估计量：一两句")

    def missing(self) -> list[str]:
        """没写的栏，按固定顺序。空白串算没写。"""
        out = []
        if not self.unit_quantity.strip():
            out.append("单位层面的量")
        if not self.target_population.strip():
            out.append("目标总体")
        if not [a for a in self.assumptions if a.strip()]:
            out.append("识别假设")
        if not self.link.strip():
            out.append("经验估计量怎么对上")
        return out


DescriptionKind = Literal["", "level", "distribution", "gap", "trend", "association"]


class Benchmark(BaseModel):
    """现实核查用的外部已知数（Firebaugh「现实核查」；描述骨架设计稿第六节）。**出处必填**。"""

    model_config = {"extra": "forbid"}

    quantity: str = Field(description="哪个量：例「2017 年城镇就业人员周平均工作时间」")
    value: float
    unit: str = ""
    source: str = Field(description="出处：哪份统计、哪张表或哪一页")

    @field_validator("quantity", "source")
    @classmethod
    def _not_blank(cls, v: str, info) -> str:
        """空串与只有空白的都拒：出处空着的基准核不了，量空着的不知道拿什么比。"""
        if not v.strip():
            raise ValueError(f"{info.field_name} 不许空：现实核查的基准要写清是哪个量、出自哪份统计的哪张表或哪一页")
        return v


#: 多层模型「为什么用它」。**这一栏是门 M1 的全部依据。**
#: 多层模型常被拿来「把聚类处理掉」，但只为修标准误的话聚类稳健更稳
#: （不假设随机效应服从正态、不假设它与自变量不相关）。所以必须说清用它干什么。
MixedRationale = Literal["", "variance", "shrinkage", "se"]


class MixedSpec(BaseModel):
    """多层模型块（设计稿 `docs/design/2026-09-25-logit-multilevel.md` 第三节）。

    **`estimator: mixed` 时必填**，而且 `rationale` 不能留空也不能写 `se`。
    """

    model_config = {"extra": "forbid"}

    group: str = Field(
        default="", description="二层单位那一列（省份、学校、社区……）。**必填**；"
        "少于 30 个警告、少于 10 个拒跑——方差分量在那么少的组上基本是噪声")
    rationale: MixedRationale = Field(
        default="", description="**为什么要用多层模型，必填**："
        "variance 方差分解本身是研究问题（「省际差异占几成」）／"
        "shrinkage 要各组的经验贝叶斯估计／"
        "se 只是想把聚类处理掉——**写 se 直接拒**，那种情形用 "
        "estimator: ols_fe + cluster_level 更稳，而且不多假设随机效应的分布")


class DescriptionSpec(BaseModel):
    """描述研究「问的是什么」的另一半（描述骨架设计稿第三节）；描述量与目标总体写在 `estimand`。

    **估计量是 `describe` 时必填 `kind`**；走 `ols_fe` 的描述类计划（条件关联、从因果降级来的）可以不写这一块。
    """

    model_config = {"extra": "forbid"}

    kind: DescriptionKind = Field(
        default="", description="level 水平 / 比例；distribution 分布；gap 组间差；trend 趋势；association 条件关联（走 ols_fe）")
    groups: list[str] = Field(default_factory=list, description="比较的组（列名，至多两列）；组间差的参照组是排序后的第一类")
    over: str = Field(default="", description="看哪段时间（时间列），趋势用")
    statistic: Literal["mean", "median"] = Field(
        default="mean",
        description="用哪个统计量：mean 均值 / 比例；median 中位数（只支持 level 与 gap）。"
        "**收入、财富、工时这类右偏的量该用 median**——均值被尾部拉走。"
        "中位数的区间没有解析公式，按抽样设计做自助法（设计稿 docs/design/2026-09-23-median-gap.md）")
    change: Literal["", "social", "individual", "none"] = Field(
        default="", description="social 总体随时间变（重复截面合适）/ individual 个人随时间变（要面板）/ none")
    meaningful_difference: str = Field(
        default="", description="有意义的最小差别，带单位与出处或理由——给论文与审稿看；环节 4 的精度按 oss power --effect 算")
    benchmarks: list[Benchmark] = Field(default_factory=list, description="外部已知数，现实核查用")


class SurveyDesign(BaseModel):
    """计划里声明的抽样设计（调查层设计稿第四节）。权重列仍写在 `AnalysisPlan.weights`。

    **画像有调查信号的计划，这一块要么写设计、要么写不按调查处理的理由**——两样都空，
    声明了研究类型的计划 `oss run` 拒绝，老计划只警告（设计稿第六节）。
    """

    model_config = {"extra": "forbid"}

    strata: str = Field(default="", description="分层列；公开版没给分层变量就空着，并在 design_source 写明")
    psu: str = Field(default="", description="初级抽样单位列。写了它，主规格的标准误按抽样设计算，不再按 cluster_level")
    weight_use: Literal["", "descriptive", "model"] = Field(
        default="", description="权重怎么用：descriptive（均值、比例、分布——必须加权）/ model（行为、因果模型——"
        "加权与不加权两版都报，差得多要解释）。Deaton 1997 第 2 章 2.1")
    design_source: str = Field(default="", description="设计从哪来：用户说的原话 / 登记（码本页）/ 画像疑似未确认")
    not_survey_reason: str = Field(default="", description="有调查信号却不按调查处理时的理由（留痕，合规检查看）")

    def declared(self) -> bool:
        """写了设计（设计来源 + 权重用法），或写了不按调查处理的理由。"""
        return bool(self.not_survey_reason.strip()) or bool(self.design_source.strip() and self.weight_use)


MeasurementKind = Literal["", "instrument", "application"]
#: 效度证据按类给，不许给整体判断——「效度良好」不是证据，是结论。
#: 分类照 Adcock & Collier（2001）、Flake 等（2017）。
ValidityEvidence = Literal["content", "structural", "criterion", "discriminant"]
#: 产出登记表的键：四类效度证据，**外加 reliability（信度系数）与 equivalence（测量等价检验）**。
#: 这两样进得来、`ValidityEvidence` 进不去，各有各的道理：
#:
#: - 信度是一份真产出，但它不是效度证据（信度说的是这几道题彼此一致，不是这几道题量的
#:   就是那个构念；Flake 等 2017）——这正是造尺子第一道门要拦的那件事
#: - 测量等价检验答的是「这几道题在两组里量的是不是同一个东西」，**是跨组可比性，不是效度的一类**
#:   （Adcock & Collier 的四类里没有它的位置）。硬塞进 structural 会错两头：它会让一个
#:   单组的因子分析冒充等价检验（跨组那道门就白设了），而用尺子的研究登记 structural
#:   还会撞上「造尺子才会有的产出」那道门。所以照 reliability 的先例单给一个键
EvidenceOutputKind = Literal[
    "content", "structural", "criterion", "discriminant", "reliability", "equivalence"]
SourceQuality = Literal["", "self_report", "administrative", "logged", "mixed"]

#: 四类效度证据的报错菜单。**只说「取值非法」，作者不知道该改成什么**。
VALIDITY_MENU = "content（内容）/ structural（结构）/ criterion（效标关联）/ discriminant（区分）"


with warnings.catch_warnings():
    # pydantic 的 BaseModel 上还留着 v1 的 `construct()`（已废弃，等同 `model_construct`），
    # 于是「构念」这一栏的名字撞上它，每导入一次就警告一次——用户跑任何一条命令都会看见。
    # **键名是契约，不为一个废弃别名改名**；只按掉这一声，别的警告照旧（离开这个块就复原）。
    warnings.filterwarnings(
        "ignore", message='Field name "construct"', category=UserWarning)

    class MeasurementSpec(BaseModel):
        """测量研究「凭什么说这几道题量的是那个东西」（测量骨架设计稿第六节）。

        对应因果的「估计量三步」、描述的「描述量 + 目标总体」。

        **这一块是给程序核的声明，不是给人读的论证**（设计稿第二节）：效度、信度、构念
        全是语义判断，做成词表就是一项换个说法就失灵的检测。所以作者在这里自己声明，
        程序只核声明的合法性与完整性——**声明了什么、产出在不在**，论文里怎么论证不读。

        不做测量研究就别写这一块（`AnalysisPlan.measurement` 留 `None`）：
        写了，就得说清测的是哪个构念、拿哪几道题测。
        """

        model_config = {"extra": "forbid"}

        kind: MeasurementKind = Field(
            default="", description="instrument 造尺子（新编量表、新建指数、新造指标）"
            "/ application 用尺子（拿现成量表做实质研究，但测量本身是主张的一部分）")
        construct: str = Field(
            default="", description="要测的那个看不见的东西，一句话：例「对陌生人的一般信任」")
        indicators: list[str] = Field(
            default_factory=list, description="题目或变量的列名")
        validity_evidence: list[ValidityEvidence] = Field(
            default_factory=list,
            description=f"要给哪几类效度证据：{VALIDITY_MENU}。**造尺子必填**——"
            "「效度良好」这种整体判断不收，它是结论不是证据")
        evidence_outputs: dict[EvidenceOutputKind, str] = Field(
            default_factory=dict,
            description=f"**哪一类证据的产出叫什么名字**：键是类别（{VALIDITY_MENU}，"
            "外加 reliability 信度系数、equivalence 跨组测量等价检验），"
            "值是这份产出在结果里的名字——`{{result:<名>.coef}}` 的名、`{{diag:<名>}}` 的名、稳健性的 label、或图名。"
            "oss 不替作者做因子分析（设计稿第十一节），所以产出在别处算，算完在这里登记一行。"
            "**检测核的是这一栏，不是论文里的论证**：声明了哪几类效度证据，就得有对应的产出登记；"
            "只登记了 reliability 等于只报了信度")
        groups_compared: list[str] = Field(
            default_factory=list, description="跨组比较的分组变量（列名），没有就留空")
        source_quality: SourceQuality = Field(
            default="", description="指标数据从哪来：self_report 自报 / administrative 行政记录 "
            "/ logged 后台日志 / mixed 混合")

        @field_validator("validity_evidence", mode="before")
        @classmethod
        def _known_category(cls, v):
            """取值不在四类里时，Literal 只会说「不在枚举里」；作者要的是「那我该写哪个」。"""
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and item not in get_args(ValidityEvidence):
                        raise ValueError(
                            f"效度证据只认四类：{VALIDITY_MENU}；「{item}」不是类别。"
                            "整体判断（例「效度良好」「量表成熟可靠」）是结论不是证据，按类给")
            return v

        @field_validator("evidence_outputs", mode="before")
        @classmethod
        def _named_output(cls, v):
            """登记一类证据，就得给出那份产出**叫什么名字**——门要拿它去结果里找。

            值留空等于没登记，而「登记了一个空名字」读起来像是登记过了：**比不登记更糟**。
            """
            if isinstance(v, dict):
                for k, name in v.items():
                    if isinstance(name, str) and not name.strip():
                        raise ValueError(
                            f"证据产出登记「{k}」的名字是空的：要写这份产出在结果里叫什么"
                            "（`{{result:<名>.coef}}` 的名、`{{diag:<名>}}` 的名、稳健性的 label、或图名）——"
                            "检测要拿这个名字去结果里找它在不在；没做出来就别登记这一类")
            return v

        @model_validator(mode="after")
        def _declared(self) -> "MeasurementSpec":
            """三栏必填；造尺子还要说清给哪一类效度证据。

            `kind` 单报一条、不并进下面那张缺栏清单：造尺子那一路的门（M1、kind 对账、
            「题目数至少三道」、「有没有外部指标」）**全都要求 `kind == "instrument"`**，
            留空比标错还轻——标成 `application` 至少还有 `kind_conflict` 兜着，
            留空则一道门都不响。所以它先答，答完才轮到别的栏。
            """
            if not self.kind.strip():
                raise ValueError(
                    "测量块缺 kind：写了这一块，就得说清是**造尺子**"
                    "（instrument：新编量表、新建指数、新造指标）还是**用尺子**"
                    "（application：拿现成量表做实质研究，但测量本身是主张的一部分）——"
                    "两种走的不是同一套检测，留空等于一项都不过；不做测量研究就别写这一块")
            miss = []
            if not self.construct.strip():
                miss.append("construct（要测的那个看不见的东西，一句话）")
            if not [i for i in self.indicators if i.strip()]:
                miss.append("indicators（题目或变量的列名）")
            if miss:
                raise ValueError(
                    "测量块缺：" + "、".join(miss) + "——「凭什么说这几道题量的是那个东西」"
                    "先从这两栏说起；不做测量研究就别写这一块")
            if self.kind == "instrument" and not self.validity_evidence:
                raise ValueError(
                    f"造尺子（kind=instrument）要声明效度证据类别：{VALIDITY_MENU}。"
                    "**只报信度不算给了效度证据**——信度说的是这几道题彼此一致，"
                    "不是这几道题量的就是那个构念")
            return self


class RealBenchmark(BaseModel):
    """合成样本要跟哪份真实数据对账（仿真骨架设计稿 4.3 乙）。

    只在主张「这批小人代表真人」时要填。`pairs` 是**列的配对**，不是变量名清单：
    合成数据的列名跟真实调查的列名几乎不会一样，对账要知道哪一列对哪一列。
    """

    model_config = {"extra": "forbid"}

    data: str = Field(
        default="", description="真实对照数据：数据包名（data/<名>）或运行目录里那份数据的文件名")
    pairs: list[tuple[str, str]] = Field(
        default_factory=list, description="[[合成列, 真实列], …]——方差比与关系方向都按这几对算")

    @field_validator("pairs", mode="before")
    @classmethod
    def _both_sides(cls, v):
        """一对里少一边，对账就无从下手——而「配了一半」读起来像是配过了。"""
        if isinstance(v, list):
            for pair in v:
                if isinstance(pair, (list, tuple)) and len(pair) == 2 and not all(
                        isinstance(c, str) and c.strip() for c in pair):
                    raise ValueError(
                        f"对照列配对「{list(pair)}」有一边是空的：要写成 [合成列, 真实列] 两边都给——"
                        "合成数据与真实调查的列名几乎不会一样，只给一边对不了账")
        return v


class SimulationSpec(BaseModel):
    """仿真研究「这个模型里，这套机制会不会产生这个模式」（仿真骨架设计稿第七节）。

    对应因果的「估计量三步」、描述的「描述量 + 目标总体」、测量的「构念 + 指标 + 效度证据」。

    **不写 `box`**：货箱路径由 `oss sim import` 写进 `sim_state.json`，计划里再抄一遍
    就是两处真相——检测一律以盘上那份为准（设计稿第七节）。

    不做仿真研究就别写这一块（`AnalysisPlan.simulation` 留 `None`）：
    写了，就得说清检验的是哪套机制、该重现哪个模式。
    """

    model_config = {"extra": "forbid"}

    mechanism: str = Field(
        default="", description="要检验的机制，一句话：模型里什么导致什么。"
        "例「邻里同质化偏好导致居住隔离」")
    pattern: str = Field(
        default="", description="该重现的**已知模式**（Grimm 等 2020 的面向模式验收）："
        "模型跑对了的话，应当出现哪个现实里已经观察到的规律。"
        "例「隔离指数随偏好阈值单调上升，且在阈值 0.3 附近出现拐点」")
    conditions_compared: list[str] = Field(
        default_factory=list, description="要比较的条件名（对应配料单 conditions 的键）；"
        "单条件研究留空——单条件只能做模式展示，不能做条件间比较")
    stands_in_for_humans: bool | None = Field(
        default=None,
        description="**这批合成样本是不是用来代表真人**。为真就要跟真实调查对账"
        "（方差比、关系方向；Argyle 等 2023 的算法保真度、Bisbee 2024 的方差压扁）。"
        "很多 ABM 本来就不主张小人等于真人，它做的是「机制能不能产生这个模式」——那种填 false。"
        "**这一栏必须表态，不许留空。**留空与 false 原先是同一个值，于是"
        "「声明了不代表真人」与「压根没想过」分不出来，而后者是零代价地绕过对账那项检测。"
        "**这不是一栏猜不出答案的字段**——小人代不代表真人，是作者对自己主张的表态。"
        "填 false 也是一个positive 的主张：论文里就只能说「在本模型内」的话")
    real_benchmark: RealBenchmark | None = Field(
        default=None, description="跟哪份真实数据对账、哪一列对哪一列。"
        "`stands_in_for_humans` 为真时必填")

    @model_validator(mode="after")
    def _declared(self) -> "SimulationSpec":
        """两栏必填；主张代表真人的还要给出对照物。"""
        miss = []
        if not self.mechanism.strip():
            miss.append("mechanism（要检验的机制，一句话：模型里什么导致什么）")
        if not self.pattern.strip():
            miss.append("pattern（该重现的已知模式——模型跑对了应当出现的那个规律）")
        if miss:
            raise ValueError(
                "仿真块缺：" + "、".join(miss) + "——仿真研究答的是「这套机制会不会产生这个模式」，"
                "先从这两栏说起；不做仿真研究就别写这一块")
        if self.stands_in_for_humans is None:
            raise ValueError(
                "仿真块的 stands_in_for_humans 没表态——**这一栏不许留空**。"
                "留空与 false 原先是同一个值，于是「声明了不代表真人」与「压根没想过」"
                "分不出来，而后者零代价地绕过了跟真实调查对账那项检测。"
                "小人代不代表真人是你对自己主张的表态：代表就填 true 并给出对照物，"
                "只做「机制能不能产生这个模式」就填 false，论文里相应只说「在本模型内」的话")
        if self.stands_in_for_humans and not (
                self.real_benchmark and self.real_benchmark.data.strip()
                and self.real_benchmark.pairs):
            raise ValueError(
                "声明这批合成样本代表真人（stands_in_for_humans=true），就得给出对照物"
                "（real_benchmark：data 真实数据 + pairs 列配对）——**合成样本的方差会被压扁**"
                "（Bisbee 2024），拿它当调查数据用，标准误会假小、显著性会假强；"
                "没有对照就核不出这件事。找不到对照数据就把这一栏改成 false，"
                "论文里也只说「在本模型内」的话")
        return self


class AnalysisPlan(Artifact):
    plan_id: str = ""
    idea_id: str = ""
    research_type: ResearchType = Field(
        default="",
        description="主类型：causal / descriptive / measurement / simulation。**按「想下什么结论」定**——"
        "想说「X 影响 Y」就是 causal，哪怕只用普通回归（那样也要过识别审计）。"
        "随锚点冻结；冻结后只许在识别站不住时从 causal 降为 descriptive（oss/gates/research_type.py）。"
        "空串 = 老计划没声明：识别审计按估计量兜底判，`oss run` 会警告",
    )
    also: list[ResearchPart] = Field(
        default_factory=list,
        description="论文里其它部分的类型（例：先自建指数再估影响——主类型 causal，also 写 measurement）。"
        "主类型走整套流程，这里列的只加它那类的检查（measurement 会多请一位测量审稿人）。"
        "因果不进 also：论文只要有一部分下因果结论，主类型就是 causal（非因果类型的正文不许有因果说法）",
    )
    estimand: Estimand | None = Field(
        default=None,
        description="估计量三步。**声明为 causal 的计划必填**（因果骨架设计稿 3.1），`oss run` 前核；老计划没写只警告",
    )
    control_roles: dict[str, ControlRole] = Field(
        default_factory=dict,
        description="每个控制变量的角色：confounder（混杂）/ precision（提高精度）/ other。"
        "**标 mediator（中介）或 collider（碰撞）的，oss run 拒绝**——那两类不该控制，控了会把估计推离因果效应"
        "（Rohrer 2018）。声明为 causal 的计划每个控制变量都要标",
    )
    estimator: str = Field(
        default="ols_fe",
        description="ols_fe（关联）/ did（政策冲击对比，双向固定效应）"
        "/ did_cs（分批双重差分，Callaway–Sant'Anna——**政策分批实施时用这个作主规格**，"
        "did 只作对比）/ event_study（逐年展开）/ iv（借力变量）/ rdd（断点）"
        "/ describe（分组描述：水平 / 分布 / 组差 / 趋势，不带控制变量；描述类研究用）"
        "/ logit（0/1 结局，**报的是平均边际效应不是系数**）"
        "/ ologit（三个以上有序取值的态度量表；**必验平行线假定**，判词进诊断）。"
        "**0/1 结局先考虑 ols_fe**：它跑出来就是线性概率模型，系数直接读成"
        "「概率高 X 个百分点」，固定效应也干净——logit 是多一个选择，不是更高级"
        "/ mixed（多层模型·随机截距，**主结果是 ICC**）：**只有「方差分解本身是研究问题」时才用**，"
        "想处理聚类用 ols_fe + cluster_level，那个更稳",
    )
    instruments: list[str] = Field(
        default_factory=list,
        description="iv：借力变量。**它的核心假设（只通过处理影响结果）"
        "在数学上无法用数据检验**——工具能做的只有每次都把这句话说出来",
    )
    running: str = Field(default="", description="rdd：决定过没过线的那个变量")
    cutoff: float = Field(default=0.0, description="rdd：分数线")
    bandwidth: float = Field(
        default=0.0, description="rdd：只看线两侧多宽的范围。**这是人选的**，所以必须报敏感性"
    )
    unit: str = Field(default="", description="did/event_study：个体或地区标识")
    time: str = Field(default="", description="did/event_study：时间列")
    treat_time: str = Field(
        default="",
        description="did/event_study：每个单位**开始被处理的时间**所在的列；"
        "缺失表示从未处理。用这一列而不是 treated×post，是因为现实里的政策"
        "几乎都是分批铺开的，而分批正是双向固定效应最容易出偏的场合",
    )
    leads: int = Field(default=4, description="event_study：往前看几期")
    lags: int = Field(default=4, description="event_study：往后看几期")
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="表和图里印的**短标签**：列名 → 名字（{\"cls_mgr\": \"管理与专业阶级\"}）。"
        "不填就退回 binding.json 里该列的构念名，再没有就印原列名——"
        "发表用的表不该出现 cls_mgr、age2 这种列名。"
        "**哑变量展开（class4 → cls_mgr / cls_clerk / cls_service）与构造项（age2）只能从这里来**，"
        "binding 里没有它们的行。交互项不用填，`a*b` 会拼成「A × B」。"
        "英文稿填英文——英文稿的表出现中文会被拒",
    )
    outcome: str = ""
    treatment: str = ""
    controls: list[str] = Field(default_factory=list)
    fixed_effects: list[str] = Field(default_factory=list)
    cluster_level: str = ""
    weights: str = Field(
        default="",
        description="抽样权重列。**调查数据不加权的估计通常是有偏的**——"
        "CGSS/CFPS/GSS 这类数据按复杂抽样设计采集，各人入样概率不同，"
        "不加权算出来的是「样本里的中国」，不是「中国」。"
        "画像检出了权重列而这里留空时，`oss route` 会开一条处方要求交代"
        "（`weights_unused`）。**留空是一个需要理由的选择，不是默认**。"
        "**七种估计量都真加权**（含 describe）；did_cs 要求权重在单位内不变",
    )
    survey: SurveyDesign | None = Field(
        default=None,
        description="抽样设计（调查层设计稿第四节）。**画像标了调查信号就要写**：分层、初级抽样单位、"
        "权重怎么用、设计从哪来；不按调查处理就只写 not_survey_reason。`None` 是老计划或非调查数据",
    )
    description: DescriptionSpec | None = Field(
        default=None,
        description="描述研究的描述块（描述骨架设计稿第三节）：kind / groups / over / change / meaningful_difference / benchmarks。"
        "估计量是 describe 时必填 kind；描述量与目标总体写在 estimand",
    )
    measurement: MeasurementSpec | None = Field(
        default=None,
        description="测量研究的测量块（测量骨架设计稿第六节）：kind / construct / indicators / validity_evidence / "
        "groups_compared / source_quality。**构念与指标必填**；造尺子（kind=instrument）还要声明"
        "给哪一类效度证据——「效度良好」这种整体判断不收，它是结论不是证据。"
        "`None` 是不做测量研究的计划",
    )
    mixed: MixedSpec | None = Field(
        default=None,
        description="多层模型块：group（二层单位那一列）与 rationale（为什么用它）。"
        "**estimator: mixed 时必填**——`rationale: se`（只想处理聚类）直接拒，"
        "那种情形 ols_fe + cluster_level 更稳。`None` 是不跑多层模型的计划",
    )
    simulation: SimulationSpec | None = Field(
        default=None,
        description="仿真研究的仿真块（仿真骨架设计稿第七节）：mechanism / pattern / conditions_compared / "
        "stands_in_for_humans / real_benchmark。**机制与该重现的模式必填**；"
        "主张合成样本代表真人的还要给出真实对照数据——合成样本的方差会被压扁（Bisbee 2024）。"
        "**不写货箱路径**：那是 `oss sim import` 写进 sim_state.json 的，抄一遍就是两处真相。"
        "`None` 是不做仿真研究的计划",
    )
    sample_filter: str = ""
    binned: BinnedSpec | None = Field(
        default=None,
        description="分箱估计的声明。写了交互项就该声明它——"
        "交互项假定斜率线性变化，而这个假定几乎从来没人验证过",
    )
    interactions: list[str] = Field(
        default_factory=list,
        description="交互项，写成 `a*b` 或 `a*b*c`，由 `run_ols_fe` 现场构造。"
        "**不要在数据里预先乘好**——那样没有任何校验能确认乘对了，"
        "`paeduc*eduq`（打错一个字母）会静静地产出一列 NaN",
    )
    robustness: list[RobustnessSpec] = Field(
        default_factory=list, description="稳健性清单。**锁定后只能跑这些**，跟主规格同一次跑完"
    )
    design_locked: DesignLock | None = Field(
        default=None,
        description="用户用 `--- design:` 锁死的那几个键的副本；没有 `--- design:` 就**不写这个字段**。"
        "`None`（没锁）与「五个键全空」（锁了但每个键都留空）都不比对，"
        "但只有前者该出现在没有自带想法的运行里。写了就由 `oss run` 在跑之前逐键跟计划本体比，"
        "不等即拒绝（退出码 6，见 oss/api/analysis.py）",
    )

    @field_validator("robustness", mode="before")
    @classmethod
    def _accept_prose(cls, v):
        """兼容旧格式：一串字符串。**但要标成不可执行**，不能静默当成锁上了。"""
        if isinstance(v, list):
            return [
                {"label": x, "executable": False} if isinstance(x, str) else x for x in v
            ]
        return v


class Estimate(BaseModel):
    term: str
    coef: float
    se: float
    p: float
    mde: float = Field(
        default=0.0,
        description="最小可探测效应：80% 功效、5% 双侧下 ≈ 2.80 × 标准误；按抽样设计（`dof` > 0）时用 t 分布，"
        "≈（t(0.975, dof) + t(0.80, dof)）× 标准误（自由度 30 约 2.90，自由度 9 约 3.15）。"
        "**一个不显著的系数只有跟它比过才知道是「没有效应」还是「测不出来」**——"
        "验证跑里执行者手算了交互项的 MDE（2.2–2.8 年，比整个基础效应还大），"
        "那是论文最要紧的一句话，不该靠手算。由标准误算出，不接受手填",
    )
    dof: int = Field(
        default=0,
        description="按抽样设计时 t 分布的自由度（抽样单位数 − 层数）：p 值与最小可探测效应都按它算；"
        "0 表示按正态。存进结果文件，读回来时最小可探测效应照样按 t 算",
    )

    @model_validator(mode="after")
    def _fill_mde(self) -> "Estimate":
        from oss.core.causal_stats import mde_multiplier

        self.mde = round(mde_multiplier(self.dof) * self.se, 6)
        return self


class BinnedSlope(BaseModel):
    """分箱估计的一格：某一组、某一箱里的斜率。

    **图上的数字必须能被复核。**一张图是给人看形状的；
    但论文引用它做出事实断言时（「最晚队列两组已无实质差别」），
    读者要能查到每一箱的点估计、标准误和样本量。
    图里有、文件里没有，就等于「相信我」——
    而这一条是三轮互盲审稿里**两位审稿人独立指出**的。
    """

    group: str
    bin_index: int = Field(description="第几箱，从 1 开始。论文用 first / last 引最两端")
    center: float
    slope: float
    se: float
    n: int


class EventStudyPoint(BaseModel):
    """逐年展开图上的一个点：相对处理时点第 k 期的效应。

    **k < 0 的那几期是这套方法的命根子。**政策实施之前两组本该没有差异；
    如果处理前就已经在分化，双重差分会把那个趋势当成效应，
    **算出来的「效应」是假的，而表格上看不出来**。
    """

    k: int = Field(description="相对处理时点的期数。k=0 是处理当期，k=-1 是参照期")
    coef: float
    se: float
    n: int


class BinnedSpec(BaseModel):
    """分箱分析的声明。**没在计划里声明就不跑**——事前锁定的意义就在这。"""

    by: str = Field(description="斜率随哪一列变（通常是队列或年份）")
    treatment: str = Field(default="", description="对哪个变量算斜率。留空则用主处理变量")
    split: str = Field(default="", description="按哪一列分组画多条线")
    bins: int = 6
    bivariate: bool = Field(
        default=False,
        description="True = 各箱内只跑二元回归（看原始形状）。缺省 False：各箱内带主规格的控制变量与"
        "固定效应——评审：「拿一个双变量截面相关去质疑一个双向固定效应加控制变量的估计，"
        "两者根本不是同一个量」。分箱要跟主规格比，就得用同一个量",
    )


class RobustnessRun(BaseModel):
    """一项稳健性检验跑出来的结果。

    **跑不动也要留下来**：一项稳健性跑不出来是信息，不是可以忽略的意外。
    静默跳过的话，读者看到的是「这项没做」还是「做了但结果不好看」，分不出来。
    """

    label: str = ""
    estimates: dict[str, Estimate] = Field(default_factory=dict)
    n: int = 0
    outcome: str = Field(
        default="",
        description="这一项实际用的结局变量。**换了结局就不是同一个估计量**——"
        "它回答的是另一个问题（换结局的多半是机制检验或第一阶段），"
        "系数的单位也不同，不能跟主结果画在同一根轴上。空串表示没换（老结果文件也是空）",
    )
    groups: list[GroupCell] = Field(
        default_factory=list,
        description="这一项自己的分组格（describe 估计量）。**它本来就是按主计划的分组重跑的**，"
        "格早就算出来了，只是以前没存——于是论文只能引一个「所有组合并」的数，跟主表的口径对不上",
    )
    diagnostics: dict[str, str] = Field(default_factory=dict)
    error: str = Field(default="", description="跑不动的原因。非空表示这一项没有结果")
    t_crit: float = Field(
        default=0.0,
        description="这一项按抽样设计时区间的 t 临界值（自由度 = 这一项自己的抽样单位数 − 层数）；0 表示按正态 1.96。"
        "森林图逐行用它：换了样本的那项抽样单位少、自由度小，去掉设计的那项按正态——主规格的临界值套不到每一行",
    )


class Descriptive(BaseModel):
    """描述统计表的一行：估计样本里某个变量的 N / 均值 / 标准差 / 最小 / 最大。

    评审：「没有描述性统计表，没有回归表——任何量化论文这都是硬伤」。
    表里的数字跟正文的数字一样，只能来自结果文件，所以描述统计在 `oss run` 时
    按**估计样本**（筛选后、去缺失后）算好存进来，渲染期 `{{table:descriptives}}` 直接排。
    """

    name: str
    n: int
    mean: float
    sd: float
    min: float
    max: float
    weighted: bool = Field(default=False, description="均值与标准差按计划的权重算的；N 仍是未加权的行数")


class GroupCell(BaseModel):
    """分组描述表的一格（`describe` 估计量；描述骨架设计稿第五节）。区间按计划的权重与抽样设计算。

    整格只落在一个初级抽样单位里（只聚类时是一个簇；不按设计也不聚类时是只有一个观测），方差是假的 0：
    `se`、`ci_lo`、`ci_hi` 与组间差三栏都是 None，`n_eff` 记为这一格所跨的单位数。
    """

    label: str
    n: int
    n_eff: float = Field(default=0.0, description=(
        "这一格的有效样本量。按抽样设计或聚类时是 样本量 ÷ 设计效应（`min(这一格的 Kish, s²_w / se²)`，"
        "封顶在 Kish：抽样单位少时 se 可能偶然很小；s² 或 se 为 0 时退回 Kish）；不按设计也不聚类时，加权是 Kish 的 (Σw)²/Σw²、不加权是行数；"
        "区间留空的格记为所跨的单位数"))
    mean: float = Field(description="这一格的点估计。`statistic` 说它是均值 / 比例还是中位数")
    statistic: Literal["mean", "median"] = Field(
        default="mean", description="`mean` 那一栏装的是哪个量——渲染与正文据此写「均值」还是「中位数」")
    se: float | None = Field(description="None：整格落在一个抽样单位里，方差算不出来。中位数时是自助法重抽分布的标准差")
    ci_lo: float | None = Field(description="95% 区间下限；0/1 结局按 logit 尺度算（见 proportion）")
    ci_hi: float | None = Field(description="95% 区间上限")
    proportion: bool = Field(default=False, description=(
        "结局是 0/1：均值是比例，区间按 logit 尺度算（同 Stata svy: proportion），不会出 [0, 1]"))
    reference: bool = Field(default=False, description="组间差的参照组")
    gap: float | None = Field(default=None, description="对参照组的差；参照组自己是 None")
    gap_se: float | None = None
    gap_p: float | None = None


class SampleStep(BaseModel):
    """样本构建日志的一行：这一步之后还剩多少行、多少单位，整个掉出去的单位是谁。

    评审第 10 条：「107 国 = 41 转型 + 66 从未转型，但回归里是 99 个 cluster，
    中间 8 个国家去哪了没有交代」。答案在去缺失那一步——哪些单位因为哪个变量整段缺失。
    AERS 叫它「脚注 4」：每篇顶刊都有的那条脚注。
    """

    step: str
    rows: int
    units: int = 0
    dropped_units: list[str] = Field(default_factory=list, description="这一步整个掉出去的单位（最多列 20 个）")
    note: str = ""


class SpecSummary(BaseModel):
    """主规格的「表注」信息：估计量、固定效应、聚类、样本筛选。

    回归表的注释（"Country and year fixed effects; standard errors clustered by country"）
    不该靠执行者手写——它来自计划，渲染期从这里生成。
    """

    estimator: str = ""
    outcome: str = ""
    treatment: str = ""
    controls: list[str] = Field(default_factory=list)
    fixed_effects: list[str] = Field(default_factory=list)
    cluster_level: str = ""
    sample_filter: str = ""
    unit: str = ""
    time: str = ""
    treat_time: str = ""
    weights: str = ""
    strata: str = ""
    psu: str = ""
    t_crit: float = Field(default=0.0, description="按抽样设计时区间的 t 临界值（自由度 = 抽样单位数 − 层数）；0 表示按正态 1.96")


class ExternalImport(BaseModel):
    """这份结果是**外部导入**的，不是本链跑出来的。

    最常见的真实用户：实验做完了、Stata 也跑完了，就差写论文；原始数据带不出来
    （单位的、签了协议的、在保密机器上、或者他就是不想再跑一遍）。
    这种人不缺想法不缺数据不缺结果，缺的正是这套东西最强的那一段。

    **这个标记只在文件里，不进论文。**论文里出现关于本系统的任何字都是过程泄漏，
    `no-process-language` 那项检测会拦。留它是为了两件事：
    ① 那几道查「有没有真跑过」的规则能明说「不适用——结果是外部导入的」，
      而不是判「还没跑分析」然后显示绿色；
    ② 将来对比两份运行记录时，分得清哪份是真跑过的。
    """

    raw_file: str = Field(
        description="用户粘进来的原文存在哪个文件。**必须存**——中间隔着一道"
        "「把表转成结构化数据」的手工活，小数点看错一位整篇论文的数字就全错且不报错。"
        "存了原文，任何一个数都能翻回去对",
    )
    software: str = Field(
        default="",
        description="用户说用什么处理的。**原话，说多少记多少**——他说「Stata」就是 Stata，"
        "不许自己补成「Stata 18」；没说就空着，论文里一个字都不写。"
        "补全就是编造，跟拿缺省效应量冒充文献值是同一类错",
    )
    imported_at: str = ""


class PretrendCov(BaseModel):
    """事件研究处理前各期系数的协方差。**联合检验与预检功效都要它**——只存对角线上的标准误算不了。"""

    ks: list[int] = Field(default_factory=list, description="处理前的期（k < 0，不含参照期 −1）")
    vcov: list[list[float]] = Field(default_factory=list, description="与 ks 同序的协方差矩阵")
    n_clusters: int = Field(
        default=0,
        description="这个协方差是拿多少个聚类估出来的。**联合检验的小样本校正要它**："
        "Wald 按 F(m, G−m) 判而不是 χ²(m)。0 = 不知道（老结果文件），退回卡方",
    )


class OlsFit(BaseModel):
    """`run_ols_fe` 的拟合信息。**不落盘**（挂在 `AnalysisResults._fit` 上），给同一次运行里的诊断用。"""

    terms: list[str]
    beta: list[float]
    vcov: list[list[float]]
    vcov_iid: list[list[float]] = Field(
        description="同方差经典协方差 σ̂²·(X'X)⁻¹，原刻度（跟 `vcov` 同一套标准化换算）。"
        "敏感性分析的稳健值、偏 R² 靠 R²_{Y~D|X} = t²/(t²+自由度) 这条恒等式，"
        "**只在经典 t 下成立**——`vcov` 是 HC1／聚类稳健的，代进去会跟 sensemakr 对不上，概念上也不对"
    )
    dof: int
    r2: float
    n: int
    n_clusters: int = Field(
        default=0,
        description="`vcov` 是拿多少个聚类估出来的（按设计时是初级抽样单位数，未聚类时是观测数）。"
        "处理前联合检验的小样本校正靠它定分母自由度",
    )


class AnalysisResults(Artifact):
    plan_id: str = ""
    estimates: dict[str, Estimate] = Field(default_factory=dict)
    n: int = 0
    imported: ExternalImport | None = Field(
        default=None,
        description="非空 = 结果是外部导入的，不是 oss run 跑出来的。见 ExternalImport",
    )
    spec: SpecSummary | None = Field(default=None, description="主规格摘要，回归表的表注从这里生成")
    companion_spec: SpecSummary | None = Field(
        default=None,
        description="伴随主规格的分批事件研究（Callaway–Sant'Anna）的规格摘要。它跟主规格不是同一个估计量，"
        "标准误口径也不同（StatsPAI 只按抽样单位聚类、分层不进，区间按正态）：各批效应表、逐期表与逐期图"
        "照它写表注、取临界值。空 = 逐期效应出自主规格自己（或老结果文件）",
    )
    descriptives: list[Descriptive] = Field(
        default_factory=list,
        description="估计样本的描述统计，`{{table:descriptives}}` 从这里排。空表示老结果文件，重跑 oss run",
    )
    groups: list[GroupCell] = Field(default_factory=list, description="分组描述表（describe 估计量），{{table:groups}} 从这里排")
    quantiles: dict[str, float] = Field(default_factory=dict, description="分布（describe 的 kind=distribution）：加权分位数")
    sample_log: list[SampleStep] = Field(
        default_factory=list,
        description="样本构建日志：原始 → 样本筛选 → 去缺失 → 估计样本，每步剩多少、谁掉了。`{{table:sample}}`",
    )
    diagnostics: dict[str, str] = Field(default_factory=dict)
    event_study: list[EventStudyPoint] = Field(
        default_factory=list,
        description="逐年展开的结果。**处理前那几期是判断这套方法能不能信的依据**",
    )
    pretrend_cov: PretrendCov | None = Field(
        default=None,
        description="事件研究处理前各期系数的协方差。老结果文件没有：联合检验退回逐期判，预检功效写「没算」",
    )
    _fit: OlsFit | None = PrivateAttr(default=None)
    binned: dict[str, list[BinnedSlope]] = Field(
        default_factory=dict,
        description="分箱估计，键是分组名。**交互项假定斜率线性变化，这里是它的实际形状**——"
        "论文引用图上的数字时必须能查到出处",
    )
    robustness: dict[str, RobustnessRun] = Field(
        default_factory=dict,
        description="稳健性检验的结果，键是 label。**跟主结果存在同一个文件里**——"
        "分开存的话后一次会覆盖前一次，论文就报不出具体数值了",
    )
    log: str = ""


# ------------------------------------------------------------- 诊断 → 处置


RouteKind = Literal["stop", "patch_plan", "add_robustness", "disclose"]
RouteStatus = Literal["pending", "applied", "dismissed", "disclosed", "resolved"]


class RouteItem(BaseModel):
    """一条处方：某个诊断触发了什么处置。

    评审对第十次验收跑的判词：「它把自己所有的病症都准确地描述了一遍，然后没有治任何一个。」
    诊断（`results.json` / `power.json` / 审计）本来只是文字，执行者读了、写进局限、交付。
    处方把每条诊断接上一个**动作**：换估计量（补丁）、加稳健性（补丁）、停（换题）、或写进局限。
    每条都要有**处置**——应用了、驳回了（写理由）、或写进局限了——`must` 级的没处置不许交付。
    """

    id: str
    priority: Literal["must", "should"] = "should"
    trigger: str = Field(description="哪条诊断触发的")
    evidence: str = Field(default="", description="诊断原文（截断）")
    kind: RouteKind = "disclose"
    patch: dict = Field(default_factory=dict, description="patch_plan：盖到 plan.json 上的字段")
    robustness: RobustnessSpec | None = Field(default=None, description="add_robustness：要加的那一项")
    advice: str = Field(default="", description="处置说明：怎么做、为什么")
    status: RouteStatus = "pending"
    why: str = Field(default="", description="驳回 / 写进局限的理由")


class Route(Artifact):
    plan_sha: str = Field(default="", description="生成处方时 plan.json 的指纹")
    items: list[RouteItem] = Field(default_factory=list)

    def pending_must(self) -> list[RouteItem]:
        return [i for i in self.items if i.priority == "must" and i.status == "pending"]


# ------------------------------------------------------------- 计划体检

class PairedColumns(BaseModel):
    """一对配对列：同一词干、一对标记（原始 / 复制、前测 / 后测……）。"""

    a: str
    b: str
    marker: str = Field(description="标记对的写法，如 `.o/.r`；不带分隔符的写成 `o/r`")


class NestedColumns(BaseModel):
    """计划的分组列 `column` 跟另一列 `other`：嵌套（粗细不同的两种分法），或有一类恰好是同一批行。

    一一对应的不算——那是同一列换了个编码，分出来的组一模一样。
    """

    column: str
    other: str
    relation: Literal["finer", "coarser", "same_rows"]
    mapping: list[str] = Field(default_factory=list, description="对应关系，一类一句；只有类别名与行数")


class PlanCheck(Artifact):
    """`oss run` 正式跑之前的计划体检（docs/design/2026-09-23-plan-check.md）。

    **只有列名、类别名与行数，没有结局的取值。**route 读它开 `plan_*` 处方。
    """

    pairs: list[PairedColumns] = Field(default_factory=list, description="整份数据里的配对列，跟计划无关")
    nested: list[NestedColumns] = Field(default_factory=list, description="按体检时计划的分组列查的")


# ------------------------------------------------------------------ 留痕

class TraceEvent(BaseModel):
    seq: int
    ts: datetime = Field(default_factory=now)
    step: str
    command: str = ""
    args: dict = Field(default_factory=dict)
    outcome: Literal["ok", "refused", "error"] = "ok"
    artifacts: list[str] = Field(default_factory=list)
    note: str = ""

    def to_line(self) -> str:
        return json.dumps(json.loads(self.model_dump_json()), ensure_ascii=False)


# ------------------------------------------------------------- 产出物清单

ARTIFACTS: dict[str, str] = {
    "profile": "profile.json",
    "literature": "literature.json",
    "screening": "screening.json",
    "reading": "reading_cards.json",
    "ideas": "ideas.json",
    "binding": "binding.json",
    "power": "power.json",
    "plan": "plan.json",
    "results": "results.json",
    "paper": "paper.md",
    "trace": "trace.jsonl",
    "route": "route.json",
}
"""谁写谁读的唯一权威表。skill 和工具箱都从这里取文件名，不许各写各的字面量。"""
