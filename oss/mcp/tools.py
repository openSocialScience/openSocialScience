"""工具表：名字 → api 函数 + 参数 schema。

名字按提案 §七：`oss profile` → `oss_profile`，`oss paper check` → `oss_paper_check`。
参数与 CLI 一一对应，只有**七处**改名（Python 关键字与内建名冲突、或 CLI 那边的
旗标名跟 api 的参数名本来就不同）——把 typer 注册表与下面的 `properties` 求了一次
差集核出来的，只差大小写或连字符的不算（`--cluster-col` → `cluster_col` 那种）：

    oss run --for            → for_id        oss review 的位置参数 [file]... → files
    oss run --plan           → explore_plan  oss review --round             → round_
    oss bind --map           → mappings      oss merge --map                → map_file
    oss data columns --search → query

能给多次的参数在这边是数组：`bind --map` → `mappings`、`review` 的多个文件 → `files`、
`lit search` 的多条检索式 → `query`（名字没变，变的是形状）。
`--json` 不暴露（MCP 本来就是 JSON）。**这份清单与 `docs/user/mcp.md` 是同一份，
改了参数名两处都要改。**

**参数名以 api 函数的签名为准，不以计划里的猜测为准。**手写的 schema 对着会变的签名，
只能机械地核：`tests/test_stage3_mcp.py` 用 `inspect` 逐个比对——schema 里的每个参数
都得是函数真收的，函数每个没有缺省值的参数都得在 `required` 里。对不上时的表现是
「模型递了一个参数，服务器回 TypeError」，而模型看不出是谁的错，只会换个参数再试。

**审稿 key 不是参数。**`oss_review` 的审稿后端从服务器进程的环境里来
（`REVIEWER_BASE_URL` / `REVIEWER_MODEL` / `REVIEWER_API_KEY`，或按 `oss mcp serve` 进程的
工作目录向上找到的 `.env`——与 `run` 参数无关，`.env` 的查找不受任何一次工具调用的
`run` 影响）——提案 §十一 把「MCP 要不要暴露 oss review」列为悬案，这里定成暴露、但 key 只走环境：
让模型把 key 当参数递进来，就等于让它出现在对话历史和留痕里。
`review()` 的 `reviewer=` 是给测试注入用的接缝，同样不暴露。

计划点名 25 个工具，这里是 32 个：`oss_lit_verify` / `oss_data_info` /
`oss_data_columns` / `oss_eval_behavior` 在计划里被划到「留在 CLI」，可它们后来都落进了
`oss/api/`——落了就该暴露，不然模型只能靠 shell 去调命令行，而 MCP 宿主里往往没有 shell。
仍然没暴露的是 `doctor` / `smoke` / `install-skills` / `trace save` / `style build|template|check`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from oss.api import analysis, anchor, binding, data, evals, figure, lit, loop, paper, power
from oss.api import profile as profile_api
from oss.api import review as review_api
from oss.api import route as route_api
from oss.api import schema as schema_api
from oss.api import tables

S: dict[str, Any] = {"type": "string"}
B: dict[str, Any] = {"type": "boolean"}
N: dict[str, Any] = {"type": "number"}
I: dict[str, Any] = {"type": "integer"}  # noqa: E741 —— 跟 S/B/N 一组，改名反而难认


def _s(t: dict, desc: str) -> dict:
    return dict(t, description=desc)


def _arr(desc: str) -> dict:
    return {"type": "array", "items": S, "description": desc}


RUN = _s(S, "运行目录，跟命令行的 --run 一样（相对服务器的工作目录，或绝对路径）")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict
    required: tuple[str, ...]
    fn: Callable[..., dict]

    def describe(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": {"type": "object", "properties": self.properties,
                                "required": list(self.required),
                                "additionalProperties": False}}


# ───────────────────────────────────────────── 一个分派：一个工具、两条 api

def _bind(run: str, idea: str = "", mappings: list[str] | None = None) -> dict:
    """不给 mappings 只列候选，给了就回验并落 binding.json。

    合成一个工具而不是两个：「先看候选、再定绑定」是一件事的两半，
    拆成两个名字只会让模型在该看候选的时候去猜绑定。
    """
    if mappings:
        return binding.bind(run, idea=idea, mappings=list(mappings))
    return binding.candidates(run, idea=idea)

# `oss_route` 的分派不在这里：`route_api.dispatch` 自己判「一次一处置」，
# CLI 与 MCP 共用同一份判据（在这里再写一遍 if/elif 就等于两份会漂的规矩）


_LIST: list[Tool] = [
    Tool("oss_loop_status",
         "审稿循环走到哪了。**第一行的 decision 就是下一步**："
         "review_first / continue / stop / export_with_open_items。"
         "退出码在 data.exit_code：0 收敛 / 3 还在循环里 / 4 轮次耗尽",
         {"run": RUN}, ("run",), loop.status),
    Tool("oss_schema",
         "某个产出物该长什么样。artifact 取 profile/literature/screening/reading/ideas/binding/"
         "power/plan/results/rules/paper；example=true 给一份填好的样例",
         {"artifact": _s(S, "产出物名"), "example": _s(B, "给样例而不是 schema")},
         ("artifact",), schema_api.describe),
    Tool("oss_profile",
         "数据画像：结构、变量、缺失、层次。shape=true 只给形状不落盘",
         {"data": _s(S, "数据文件路径"), "run": RUN, "shape": _s(B, "只要形状"),
          "resume": _s(B, "明确要接着一个已完成的运行继续")},
         ("data", "run"), profile_api.profile),
    Tool("oss_bind",
         "把构念绑到数据的真实列上。不给 mappings 只列候选（选哪个是你的判断）；"
         "给了就回验并产出 binding.json",
         {"run": RUN, "idea": _s(S, "idea_id，缺省取第一个"),
          "mappings": _arr("构念=列名:角色，可给多条")},
         ("run",), _bind),
    Tool("oss_power",
         "统计功效。判词三档：pass / gray（写进局限）/ fail（停，回环节 4）。"
         "**描述类题目改按精度判**（mode=precision）：effect 读作有意义的最小差别，判词比的是主要比较的 95% 区间半宽；"
         "fail 是精度不足、**不停**——环节 6 的 oss_route 开 precision_low 处置",
         {"run": RUN, "idea": _s(S, "idea_id；不给先取锚点当前的题，再取计划写的题，都没有取 ideas.json 第一条"),
          "effect": _s(N, "文献报告的效应量；描述类是有意义的最小差别"),
          "effect_source": _s(S, "这个数从哪儿来：哪条语料记录、哪一页、原话；描述类写这个差别凭什么算有意义。"
                                 "**不给就判不了 pass**（封顶灰区）"),
          "effect_metric": _s(S, "d / r / beta；描述类还可以是 raw（effect 按结局原单位给，按结局的加权标准差折算）"),
          "weight_col": _s(S, "算权重设计效应（Kish）用的权重列；不给用画像检出的权重列"
                              "（描述类有本题的计划时用计划的 weights），给空串就不按权重折"),
          "n": _s(I, "样本量，0 表示从 profile 读；描述类按精度判时不能跟 group_col 一起给"),
          "discipline": _s(S, "学科，决定灰区上下界"), "clusters": _s(I, "聚类单位数"),
          "icc": _s(N, "残差组内相关"), "out": _s(S, "另存为运行目录里的这个文件名"),
          "cluster_col": _s(S, "聚类列名；描述类按它数簇、组差的 ρx 也从它算"
                               "（不给就按本题计划的 survey.psu，没写取 cluster_level；都没有就不算整群）"),
          "treatment_col": _s(S, "处理变量列名"),
          "treatment_icc": _s(N, "处理变量组内相关"), "alpha": _s(N, "显著性水平"),
          "group_col": _s(S, "描述类按精度判时的分组列，逗号分隔，至多两列；不给、本题有 describe 计划时按计划的分组")},
         ("run",), power.power),
    Tool("oss_run",
         "按 plan.json 跑分析。explore=true 走探索通道：必须给 for_id 与 explore_plan，"
         "结果进 explore/，**不进论文、不进稳健性表、占位符引用不到**",
         {"run": RUN, "data": _s(S, "数据文件，缺省从 profile.json 读"),
          "explore": _s(B, "探索性回归"), "for_id": _s(S, "探索回应的处方 id 或 review-rN"),
          "explore_plan": _s(S, "探索用的计划文件"), "why": _s(S, "超上限时的理由")},
         ("run",), analysis.run),
    Tool("oss_figure",
         "诊断图。kind 取 structure/missing/clusters/coef/collinearity/event/"
         "robustness/marginal。**不是装饰，是最快的错误检测器**",
         {"kind": _s(S, "图名"), "run": RUN, "data": _s(S, "数据文件"),
          "cluster_col": _s(S, "clusters 图用"), "by": _s(S, "marginal：斜率随哪一列变"),
          "split": _s(S, "marginal：按哪一列分组"), "bins": _s(I, "marginal 的箱数"),
          "treatment": _s(S, "marginal：对哪个变量算斜率"), "icc": _s(N, "clusters 图的 ρu"),
          "out": _s(S, "输出路径"), "lang": _s(S, "en / zh")},
         ("kind", "run"), figure.figure),
    Tool("oss_route",
         "诊断 → 处置。只给 run 是查看；给 apply/dismiss/disclose 之一是处置"
         "（一次一处置）。退出码 6 表示还有 must 级没处置",
         {"run": RUN, "apply": _s(S, "应用这条处方"), "dismiss": _s(S, "驳回，必须配 why"),
          "disclose": _s(S, "标记已写进局限，配 why"), "why": _s(S, "理由")},
         ("run",), route_api.dispatch),
    Tool("oss_review",
         "叫一个别家的模型来审。审论文用 paper=true（自动带齐稿子+计划+结果+功效+语料）。"
         "**审稿 key 只从服务器环境来，不是参数。**"
         "第 3 轮会被轮次检测拒绝（退出码 4）",
         {"run": RUN, "files": _arr("要审的文件"),
          "level": _s(S, "quick / normal / deep"), "kind": _s(S, "ideas / paper / generic"),
          "emphasis": _s(S, "本轮侧重"), "round_": _s(I, "第几轮"),
          "identification": _s(B, "审因果设计"), "paper": _s(B, "审论文"),
          "desk": _s(B, "桌审"), "alternatives": _s(B, "替代解释审稿人"),
          "editor": _s(B, "编辑综合"), "extra_round": _s(B, "超上限加审，必须配 why"),
          "why": _s(S, "加审的理由")},
         ("run",), review_api.review),
    Tool("oss_paper_check",
         "校验稿件：节齐不齐、有没有裸数字、占位符解析得了吗、锚点还是原来那个题吗、"
         "must 级处方处置了没有",
         {"run": RUN, "style": _s(S, "风格包名")}, ("run",), paper.check),
    Tool("oss_paper_render",
         "把占位符换成真实数字。**校验不过就不给渲染**",
         {"run": RUN, "out": _s(S, "缺省写到 paper_rendered.md")}, ("run",), paper.render),
    Tool("oss_paper_export",
         "导出 Word。七项检测各有退出码：7 锚点与研究类型 / 8 正文的因果说法一族——非因果论文"
         "下了因果结论，或因果论文弱工具没引 AR 区间 / 4 缺主结果表 / 5 没审过 / "
         "3 有 FAIL 未复审 / 6 处方没处置 / 1 风格包或 pandoc",
         {"run": RUN, "out": _s(S, "输出文件名"), "style": _s(S, "风格包名")},
         ("run",), paper.export),
    Tool("oss_anchor_switch",
         "换题。**模型不能授权自己换题**——authorized_by 必须是 user，否则拒绝（退出码 2）",
         {"run": RUN, "to": _s(S, "切换到的 idea_id"), "why": _s(S, "为什么换"),
          "authorized_by": _s(S, "必须是 user")},
         ("run", "to", "why"), anchor.switch),
    Tool("oss_eval",
         "说明书的测试：对着运行目录核有没有照说明书做。退出码 7 表示有 must 级没过",
         {"run": RUN}, ("run",), evals.run),
    Tool("oss_eval_behavior",
         "换一把尺子：不查产物合不合说明书，查**这次是怎么跑的**——"
         "审稿轮数、换题、问用户、探索、被拒、耗时。指标全部从留痕算",
         {"run": RUN}, ("run",), evals.behavior),
    Tool("oss_eval_rules", "只列规则，不评。behavior=true 列行为指标那一套",
         {"behavior": _s(B, "列行为指标而不是产物规则")}, (), evals.list_rules),
    Tool("oss_merge",
         "拼两份数据，并**把丢掉的东西摊开**。对不上的键只列候选，绝不自动套用",
         {"left": _s(S, "左表（行数以它为准）"), "right": _s(S, "右表"), "run": RUN,
          "on": _s(S, "两边同名的键，逗号分隔"), "left_on": _s(S, "左表的键"),
          "right_on": _s(S, "右表的键"), "how": _s(S, "inner / left"),
          "allow_loss": _s(N, "接受多大的损失，如 0.25"), "map_file": _s(S, "键对照表 CSV"),
          "out": _s(S, "输出文件名")},
         ("left", "right", "run"), tables.merge),
    Tool("oss_reshape",
         "宽表面板转长表：每人每轮一行。**只做摞行**，量表反向/取对数/转哑变量一概不做",
         {"data": _s(S, "宽表数据文件"), "run": RUN, "out": _s(S, "输出文件名"),
          "unit": _s(S, "个体编号列"), "wave_name": _s(S, "轮次列叫什么")},
         ("data", "run"), tables.reshape),
    Tool("oss_lit_search", "检索文献。OpenAlex（要一把免费密钥）+ Semantic Scholar，Crossref 只在 OpenAlex 不可用时兜底。检索式按理论阵营写，用概念块",
         {"query": _arr("检索式，可给多条"), "run": RUN,
          "limit": _s(I, "每条检索式每个源取多少"),
          "year_from": _s(I, "只要这一年之后的"),
          "zh": _arr("中文检索式（阵营: 检索式，可给多条）；只查 OpenAlex 里 2018 年前的中文核心刊白名单；与 query 至少给一边")},
         ("run",), lit.search),
    Tool("oss_lit_fetch",
         "把最值得精读的文献拿到本地。**来源层级会标清**：全文 / 仅摘要 / 仅元数据",
         {"run": RUN, "top": _s(I, "精读前几篇"), "ids": _s(S, "点名要读的 id/DOI，逗号分隔")},
         ("run",), lit.fetch),
    Tool("oss_lit_cards",
         "校验读书卡：**局限引文必须能在拿到的文本里逐字找到**，找不到一律拒绝",
         {"run": RUN, "check": _s(B, "false 只汇总不校验")}, ("run",), lit.cards),
    Tool("oss_lit_screen",
         "把 skill 写的筛选判定 screening.json 盖到语料上：每篇都要判「纳入/排除」，排除要写理由；"
         "之后精读排序只看纳入的",
         {"run": RUN}, ("run",), lit.screen),
    Tool("oss_lit_snowball",
         "从种子往后拉参考文献、往前拉引用扩进语料（主题检索够不着的经典在这；往前拉按被引取最有分量的后续工作）。"
         "滚完新增的没筛过，接着 oss_lit_screen 只判新增的",
         {"run": RUN, "seeds": _s(S, "点名种子 id/DOI，逗号分隔；缺省按排序取前 top 篇"),
          "top": _s(I, "缺省种子数"), "backward": _s(I, "每个种子往后拉几篇"),
          "forward": _s(I, "每个种子往前拉几篇")},
         ("run",), lit.snowball),
    Tool("oss_lit_import",
         "把知网/万方导出的题录文件并进语料（中文库没有 API，用户自己检索导出）；"
         "导入的记录没筛过，接着 oss_lit_screen",
         {"path": _s(S, "导出文件路径"), "run": RUN, "camp": _s(S, "理论阵营")},
         ("path", "run"), lit.import_file),
    Tool("oss_lit_verify",
         "核对稿子里每一处引用的**转述**对不对。上一步只保证「这篇文献真实存在」，"
         "保证不了「转述得对」。机器只抓明显的张冠李戴，剩下的配对文件交给别家模型判；"
         "没有摘要就判「核不了」，不判「存疑」",
         {"run": RUN, "out": _s(S, "配对文件另存为运行目录里的这个文件名")},
         ("run",), lit.verify),
    Tool("oss_lit_add", "按 DOI 把一篇文献补进语料（检索没召回的经典走这条路，留痕可核）",
         {"doi": _s(S, "DOI"), "run": RUN}, ("doi", "run"), lit.add),
    Tool("oss_data_list", "现成数据集清单：哪些能直接下、哪些要申请、哪些已经在本机",
         {}, (), data.list_packs),
    Tool("oss_data_info", "一套数据的码本、钥匙列、引用格式、许可、怎么拿",
         {"name": _s(S, "数据集名，见 oss_data_list")}, ("name",), data.info),
    Tool("oss_data_columns",
         "按构念找列：一套数据里哪些列可能测你要的东西。**先搜列，再说没有**——"
         "找到的是候选不是答案，口径要看码本",
         {"name": _s(S, "数据集名"), "query": _s(S, "关键词，逗号分隔；在列名、变量标签、值标签里找"),
          "limit": _s(I, "最多列几条")},
         ("name",), data.columns),
    Tool("oss_data_search", "在世界银行 / OECD / Our World in Data 的目录里搜指标。"
         "**给的是候选，不是答案**",
         {"query": _s(S, "关键词，英文"), "source": _s(S, "wb / oecd / owid"),
          "top": _s(I, "取几条")}, ("query",), data.search),
    Tool("oss_data_fetch", "把一个指标拉成「国家-年」长表，带 ISO3 代码和出处",
         {"source": _s(S, "wb / oecd / owid"), "code": _s(S, "指标代码"), "run": RUN,
          "out": _s(S, "文件名"), "year_from": _s(I, "起始年"), "year_to": _s(I, "结束年")},
         ("source", "code", "run"), data.fetch),
    Tool("oss_data_get",
         "拿一套现成数据。**要申请的数据集不能替你下**（退出码 3，会告诉你怎么申请）",
         {"name": _s(S, "数据集名"), "columns": _s(S, "只抽这些列，逗号分隔"),
          "out": _s(S, "抽出来的表写到哪"), "file": _s(S, "用这个本地文件"),
          "refresh": _s(B, "重新下载"), "run": _s(S, "给了就把出处记进这个运行目录的留痕")},
         ("name",), data.get),
]

TOOLS: dict[str, Tool] = {t.name: t for t in _LIST}
