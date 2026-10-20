"""主链 `social-research` 认得 `sim:` 货箱——锚点测试。

Task 4/5（sim-intake-v2）：`/social-sim` 是独立技能（见 `tests/test_social_sim_skill.py`），
这份测试点名的是**主链**吃下 sim 数据这件事本身——三个文件：
`rules/input-and-rundir.md`（`sim:` 拆法 + `/social-sim` 参数拆法指路）、
`rules/ask-user.md`（三行白名单）、`stages/1-profile.md`（check→import→读章
三步的 sim 分支、完成条件、禁止区）。

另立一份文件而不是并进 `tests/test_social_sim_skill.py`：那份测试的 `SKILL_DIR`
锁死在 `.claude/skills/social-sim/`，`_all()` 只拼那三个文件；这里点的是
`social-research` 底下三份不同的文件，混进去会让那份测试的文档字符串
（"这份技能只有三个文件"）说谎。

Task 6（sim-intake-v2）在这份文件里续了四份 `social-research` 阶段文件的仿真
分支（`5-plan.md`、`7-paper.md`、`7.5-identification.md`、`8-review.md`）以及
`social-paper`/`social-review` 两个技能各自新增的 `reference/simulation.md`
（连同各自 `manifest.yaml` 的挂载）——体裁的说话规矩、方法节十项、审稿成见。

keyless，只读文件，不跑仿真、不碰网络。逐句点名到具体文件，不用"整份技能里
找找看"的写法——跟 `tests/test_stage1_skill_rules.py` 同一套办法。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / ".claude" / "skills" / "social-research"

INPUT_RUNDIR = RESEARCH / "rules" / "input-and-rundir.md"
ASK_USER = RESEARCH / "rules" / "ask-user.md"
PROFILE = RESEARCH / "stages" / "1-profile.md"

# Task 6：仿真体裁的说话规矩
PLAN = RESEARCH / "stages" / "5-plan.md"
PAPER = RESEARCH / "stages" / "7-paper.md"
IDENTIFICATION = RESEARCH / "stages" / "7.5-identification.md"
REVIEW = RESEARCH / "stages" / "8-review.md"
SOCIAL_PAPER_DIR = ROOT / ".claude" / "skills" / "social-paper"
SOCIAL_REVIEW_DIR = ROOT / ".claude" / "skills" / "social-review"
SOCIAL_PAPER_SIM = SOCIAL_PAPER_DIR / "reference" / "simulation.md"
SOCIAL_REVIEW_SIM = SOCIAL_REVIEW_DIR / "reference" / "simulation.md"
SOCIAL_PAPER_MANIFEST = SOCIAL_PAPER_DIR / "manifest.yaml"
SOCIAL_REVIEW_MANIFEST = SOCIAL_REVIEW_DIR / "manifest.yaml"


def _t(p: Path) -> str:
    assert p.exists(), f"缺 {p}"
    return p.read_text(encoding="utf-8")


# ══════════════════════ input-and-rundir.md：`sim:` 拆法 ══════════════════════

def test_data拆法认得sim前缀():
    """`--- data: sim:<货箱目录>` 这条拆法句子要在——点名它来自 `/social-sim`
    或手工装（`docs/user/sim-box.md`），并指向环节 1 的 sim 分支。"""
    t = _t(INPUT_RUNDIR)
    assert "`--- data: sim:<货箱目录>` 表示这份数据来自仿真货箱，不是普通数据文件" in t
    assert "要么是 `/social-sim` 一段话跑出来的，要么照 `docs/user/sim-box.md` 手工装的" in t
    assert "环节 1 认出这个前缀会走专门的 sim 分支（先验货、再入库、再取数据）" in t


def test_socialsim参数拆法一节在input_rundir里():
    """`/social-sim` 的 simulator/design/budget/checkpoint 拆法要点在这里出现，
    但完整规矩不重复——指向 social-sim 自己的 SKILL.md 与 run-order.md。"""
    t = _t(INPUT_RUNDIR)
    assert "## 仿真来源：`/social-sim` 的参数拆法" in t
    assert "simulator: oasis --- design: <一段话>" in t
    assert "**simulator** 必给，模拟器插头名（`oasis` 或 `agentsociety`" in t
    assert "**design** 必给" in t
    assert "**budget** 可选" in t
    assert "**checkpoint** 可选" in t
    # 权威出处指路，不是复制规矩
    assert "`.claude/skills/social-sim/SKILL.md`" in t
    assert "`.claude/skills/social-sim/reference/run-order.md`" in t
    assert "**不在这里重复**" in t


# ══════════════════════ ask-user.md：白名单三行 ══════════════════════

def test_ask_user白名单加三行():
    t = _t(ASK_USER)
    assert "| **仿真整理稿确认**（`/social-sim` 缺省停一次） |  |" in t
    assert "| **`oss sim doctor` ✗ 项处理不了** |  |" in t
    assert "| **402／余额类断粮，全停告知用户去充值** |  |" in t
    # 三行没有把原有白名单挤掉——原来那两条自带想法行仍然在
    assert "**整理稿确认**（带 `idea:` 时的 checkpoint `idea`，缺省开）" in t
    assert "**自带想法时功效 FAIL 走到「换题目」那条出路**" in t


# ══════════════════════ 1-profile.md：sim 分支三步 ══════════════════════

def test_1profile有sim分支小节():
    t = _t(PROFILE)
    assert "## 数据来自仿真货箱（`sim:`）时" in t


def test_sim分支先check拒收即停不许绕过():
    """check-先行禁令：拒收就停，problems 原样给用户，不许绕过 check 直接拿 CSV。
    这条禁令要落在两处——sim 分支小节里讲清楚，`## 禁止` 区再钉一遍死规矩。"""
    t = _t(PROFILE)
    assert "**先验货**：`oss sim check <箱目录>`" in t
    assert "不许绕过 check 直接翻箱子拿 CSV 当数据用" in t
    assert "- 绕过 `oss sim check` 直接拿箱内 CSV 当普通数据用" in t


def test_sim分支import与数据文件():
    t = _t(PROFILE)
    assert "验货通过（含只带警告的通过）才能入库：`oss sim import <箱目录>" in t
    assert "--run <运行目录>`" in t
    assert "数据文件就是 `<箱目录>/states.csv`" in t
    assert "events.csv" in t


def test_sim分支步2按来源分叉沿用运行目录():
    """质量审修订②：箱子来自 `/social-sim`、收尾报告给了运行目录时——沿用
    那个运行目录、不重复 import；手工装的箱子照旧自建目录 + import。这是
    `--run` 交接的接收端；发送端锚句见 `test_social_sim_skill.py`。"""
    t = _t(PROFILE)
    assert "**入库分两条路，按箱子怎么来的分叉**" in t
    assert "`/social-sim` 跑出来的、收尾报告已经给了运行目录" in t
    assert "**沿用那个" in t and "运行目录**" in t
    assert "**不用再 `import` 一次**" in t
    assert "`check` 这一步仍然要跑，验货不因为已经交接过而豁免" in t
    assert "箱子是手工装的（或没收到收尾报告给的运行目录）" in t


def test_sim_state三字段必须复述():
    """downgrade / single_condition / warnings 三个字段的内容要原文逐字念给
    用户、不许改写——质量审修订③把这句话升到跟发送端（`oss sim check`/
    `oss sim import` 的警告）同一个强度，不是「复述」这种弱说法。"""
    t = _t(PROFILE)
    assert "`<运行目录>/sim_state.json`" in t
    assert "`downgrade`、`single_condition`、" in t
    assert "三个字段的内容**原文逐字念给用户，不许改写**" in t
    assert "**必须复述给用户**" not in t, "旧的弱措辞不该还在"
    assert "`downgrade=true`" in t
    assert "意味着论文只能作「初步/探索性」表述" in t


def test_完成条件加sim来路一行():
    t = _t(PROFILE)
    assert ("- 数据来自仿真货箱时，`sim_state.json` 已在运行目录里，且 "
            "`downgrade` 字段已经读过并原文逐字念给用户") in t
    # 原有推进行没被挤掉、顺序也没乱——sim 那行在它前面
    idx_sim = t.index("数据来自仿真货箱时，`sim_state.json` 已在运行目录里")
    idx_advance = t.index("`run_state.stage` 推进为 `2-literature`")
    assert idx_sim < idx_advance


def test_states_csv个体是复合体不是数据坏():
    """质量审修订①：`agent_id` 跨场重复编号，不是同一个体跨场追踪——
    `repeated_cross_section`/`unit_id` 为空是预期现象，不是要去修的坏数据；
    面板/DID 前要在 `prep.py` 里造复合 `unit` 列，供环节 5 的 `plan.unit` 用。"""
    t = _t(PROFILE)
    assert "**condition×rep×agent_id 复合体**" in t
    assert "A/1 的 0 号小人与 B/1 的 0 号小人是两个不同的模拟人" in t
    assert '`structure.kind == "repeated_cross_section"`' in t
    assert "**预期现象，不是数据坏**" in t
    assert "「没有个体标识」" in t
    assert "**别信以为真去修数据**" in t
    assert "在 `prep.py` 里拼一列复合 `unit`" in t
    assert "环节 5 的 `plan.unit` 用这一列" in t


# ══════════════════════ docs/user 烂链哨——盲区同类延伸（T3 的检查搬到这三个文件） ══════════════════════

def test_docs用户引用全部指得到真文件():
    """T3 给 `social-sim` 自己的三份文件堵过这个盲区（`test_social_sim_skill.py::
    test_docs用户引用全部指得到真文件`）：正文散文里裸写的 `docs/user/*.md`
    （不是 Markdown 链接语法，`validate_skills` 的裸链检查认不出这种写法）没有
    烂链哨——文档改名/挪走，说明书里的引用会悄悄指空，没人报。

    这份测试把同一招搬到 Task 4/5 改动的这三个 `social-research` 文件：
    `input-and-rundir.md` 新增了一处 `docs/user/sim-box.md` 引用，正是这条哨兵
    要盯住的那个新盲区。"""
    pattern = re.compile(r"docs/user/[\w.\-]+\.md")
    matches = (pattern.findall(_t(INPUT_RUNDIR))
               + pattern.findall(_t(ASK_USER))
               + pattern.findall(_t(PROFILE)))
    assert len(matches) >= 1, f"三份文件里一处 docs/user/*.md 引用都没找到：{matches}"
    for rel in matches:
        assert (ROOT / rel).exists(), f"引用 {rel} 指不到真文件"


# ══════════════════════ Task 6：仿真体裁的说话规矩 ══════════════════════
# 5-plan / 7-paper / 7.5-identification / 8-review 各自的「数据来自仿真货箱时」
# 小节，加 social-paper / social-review 两份新 reference/simulation.md。

# ---- 5-plan.md：主处理形状、复合 unit 列、did 族、稳健性、leads/lags ----

def test_5plan有sim分支小节():
    t = _t(PLAN)
    assert "## 数据来自仿真货箱时" in t


def test_5plan_unit必须用复合列不是agent_id():
    """`plan.unit` 不能直接填 `agent_id`——环节 1 造的复合 `unit` 列
    （`condition×rep×agent_id`）才是环节 5 该用的那一列；写 `plan.json` 的
    这一刻是「个体标识错了」变成「估计能不能信」的分界点，这里再申一遍。"""
    t = _t(PLAN)
    assert "**`plan.unit` 必须用 `prep.py` 造的复合 `unit` 列（`condition×rep×agent_id`），" in t
    assert "不是 `agent_id`**——环节 1 已经交代过" in t


def test_5plan_treatment走did族且绑定门例外看identification_strategy():
    """画像报「condition 在个体内不变」是组间处理的机器自证，不是数据坏；
    绑定门对组间处理的一般拦法在这里有 did 族例外，例外按整理稿的
    `identification_strategy` 判，留空退回旧拦法（策略空等于从严，有意的缺省）。"""
    t = _t(PLAN)
    assert "那不是数据坏，是**组间" in t
    assert "处理的机器自证**——每个复合 `unit` 从建组起就只属于一个 `condition`，这正" in t
    assert "`treatment` 走 **did 族**识别（`did` / `did_cs` /" in t
    assert "族来判——**留空会退回旧拦法**，策略空等于从严，这是有意的缺省，不是漏填" in t


def test_5plan稳健性清单含跨rep留一():
    t = _t(PLAN)
    assert "`robustness` 清单**必含跨 rep 留一**（`rep ≥ 2` 时）：留一去一重跑主设计，" in t


def test_5plan_leads_lags不超过波次数减一():
    """V0 发现 11：leads/lags 超过货箱实际访谈波次数，在仿真数据里天然全缺失。"""
    t = _t(PLAN)
    assert "**`leads/lags` ≤ 波次数 − 1**：波次数以货箱 `interview_plan` 实际访谈的次" in t


# ---- 7-paper.md：薄路由，细则甩给 social-paper 的 reference/simulation.md ----

def test_7paper有sim分支且指向social_paper_reference():
    t = _t(PAPER)
    assert "调 `/social-paper` 时**明说这是一篇仿真论文**——运行目录里有 `sim_state.json`" in t
    assert "`.claude/skills/social-paper/reference/simulation.md`" in t
    assert "这里不重复" in t


def test_7paper仿真三查已上线且不写死实现细节():
    """T7 那道门已经落地（3120216）——说明书要用现在时说它开着、说清宽严
    （前两件拒收、第三件警告），但仍不写死消息文本或退出码。"""
    t = _t(PAPER)
    assert "导出前的 `oss paper check` 对仿真论文会额外拦三件事：模拟器有没有被引用、" in t
    assert "这道门就开着：前两件缺了按拒收拦，第三件逐句警告不拦死" in t
    assert "在实现中" not in t
    assert "以 `oss paper check` 的实际输出为准，不在这份" in t
    assert "说明书里写死。" in t


# ---- 7.5-identification.md：审计清单换套、威胁模型=模型伪影非混淆 ----

def test_75identification有sim分支且换审计清单():
    """rep 够不够 / 条件平衡 / 敏感性跑没跑 / 污染声明核过没——四条换掉传统
    混淆问句；机器已算的双重差分族诊断仍要照读，只是不再驱动判词。
    （因果骨架加了预检功效与 breakdown 值，这一句跟着列全。）"""
    t = _t(IDENTIFICATION)
    assert ("机器已算的双重差分族诊断（平行趋势 / 预检功效 / breakdown 值 / 对照组 / 处理前平衡 / "
            "批次异质性）仍要照读——") in t
    assert "- **rep 够不够**：`sim_state.json` 的 `downgrade` 为 `true`（rep < 3）时，不必" in t
    assert "- **条件平衡**：仿真各条件用**同一个人群池按条件复制**出来（同一批 agent" in t
    assert "- **敏感性跑没跑**：核对环节 5 `robustness` 清单里的跨 rep 留一有没有真跑过、" in t
    assert "- **污染声明核过没**：`sim.meta.json` 的 `contamination_note` 写了没有，写的" in t


def test_75identification伪影非混淆句():
    """威胁模型换了：仿真论文的头号威胁是模型伪影，不是观察性混淆——传统混淆
    问句对仿真数据没有意义，不许机械套用。"""
    t = _t(IDENTIFICATION)
    assert "**威胁模型也换了**：仿真论文的头号威胁**是模型伪影，不是观察性混淆**——" in t
    assert "对仿真数据没有意义——**不要" in t
    assert "机械套用**这一套，套上了也审不出真问题" in t


# ---- 8-review.md：薄路由，细则甩给 social-review 的 reference/simulation.md ----

def test_8review有sim分支指向social_review_reference():
    t = _t(REVIEW)
    assert "三位互盲审稿人的**成见**在这里换一套问法——不再是「识别 / 替代解释 / 局限」" in t
    assert "`.claude/skills/social-review/reference/simulation.md`" in t
    assert "这里不重复" in t


# ---- social-paper/reference/simulation.md：仿真体裁的实质写作规矩 ----

def test_socialpaper_simulation参考文件存在且被manifest挂上():
    assert SOCIAL_PAPER_SIM.exists(), "social-paper 缺 reference/simulation.md"
    m = SOCIAL_PAPER_MANIFEST.read_text(encoding="utf-8")
    assert "path: reference/simulation.md" in m


def test_socialpaper_simulation结论句限定在本模型内():
    """说明书列的每一种限定写法，**代码都得真的认**。

    原先这里钉的是一句散文，而散文里写的 `within this simulated model` 跟代码认的
    `within this/the model` 并不是一回事——**说明书教了一种代码不认的写法，没人发现**。
    改成从说明书里把写法抓出来，逐个喂给 `SIM_SCOPE`：教的和认的对不上就红。
    """
    from oss.core.causal_wording import SIM_SCOPE

    t = _t(SOCIAL_PAPER_SIM)
    assert "结论句一律限定在**「在本模型内」**" in t
    taught = ["在本模型内", "在模型内", "在本模型的设定下", "在本模型框架内", "在本模型的边界内",
              "within this model", "within the model", "within the simulated model",
              "within the model's dynamics"]
    missing = [w for w in taught if w not in t]
    assert not missing, f"说明书没列这几种写法：{missing}"
    unknown = [w for w in taught if not SIM_SCOPE.search(w)]
    assert not unknown, f"说明书教了代码不认的写法：{unknown}"


def test_socialpaper_simulation配料单十二项一项不漏():
    """配料单每一项都要在说明书里点到名——**漏掉一项，作者就不会报它**。

    原先钉的是一行十项的清单（漏了 `model_provider` 与 `contamination_note`）。
    改成拿 `simbox.META_REQUIRED` 逐项核：配料单以后加一项，这条会红。
    """
    from oss.core.simbox import META_REQUIRED

    t = _t(SOCIAL_PAPER_SIM)
    assert "`sim_state.json` 的 `meta` 字段" in t
    missing = [k for k in META_REQUIRED if f"`{k}`" not in t]
    assert not missing, f"配料单这几项说明书里没点名：{missing}"


def test_socialpaper_simulation说的有牙齿的项跟代码查的一模一样():
    """**说明书说哪几项有检查，就得真的只有那几项有检查。**

    原先是两项有牙齿、八项没有，说明书照实写着「别指望检查器接住」。
    2026-09-19 扩到七项，那句话作废——但作废之后更危险：
    **说明书说「这几项会查」而代码不查，作者就不会报它，还以为有人接着。**
    所以这条不钉散文，钉两张表跟代码对得上。
    """
    from oss.api.paper import SIM_NO_TEETH, _SIM_REPORTABLE

    t = _t(SOCIAL_PAPER_SIM)
    assert "别指望检查器接住" not in t, "这句话已经作废——七项有牙齿了"
    for key in _SIM_REPORTABLE:
        assert f"| `{key}` |" in t, f"有牙齿的 {key} 没进「七项有代码牙齿」那张表"
    for key in SIM_NO_TEETH:
        assert f"| `{key}` |" in t, f"没牙齿的 {key} 没进「五项没有任何机器兜底」那张表"
    assert "五项没有任何机器兜底" in t and "七项有代码牙齿" in t
    # ODD 七节的对照表：第四节没有机器兜底这件事要写明
    assert "④ 设计概念" in t and "全靠你写，审稿人来看" in t


def test_socialpaper_simulation降级句原文引simbox():
    """降级句必须逐字引 `oss/core/simbox.py` 验货打出来的那句，不转述。"""
    t = _t(SOCIAL_PAPER_SIM)
    assert "「单次模拟无法区分随机波动与真实规律」" in t
    simbox_src = (ROOT / "oss" / "core" / "simbox.py").read_text(encoding="utf-8")
    assert "单次模拟无法区分随机波动与真实规律" in simbox_src


def test_socialpaper_simulation局限五件套():
    t = _t(SOCIAL_PAPER_SIM)
    for item in ("小人效度", "版本漂移", "提示词敏感", "污染声明", "单一模型"):
        assert item in t, f"局限五件套缺了「{item}」"


def test_socialpaper_simulation版本漂移含网关model_provider():
    """质量审 Fix 2：`model_provider`（所走网关）落在版本漂移这一条局限里，
    不进十项方法节清单——同名模型走不同网关，路由与量化可能不同。"""
    t = _t(SOCIAL_PAPER_SIM)
    assert "`simulator_version`、模型版本与所走网关（`model_provider`）都要写实" in t
    assert "同名模型走不同网关，路由与量化可能不同" in t


def test_socialpaper_simulation_wave对应真实轮次以interview_plan为权威():
    t = _t(SOCIAL_PAPER_SIM)
    assert "轮次号——真实对应第几轮，权威出处是配料单 `interview_plan`。正文提到某一波" in t


# ---- social-review/reference/simulation.md：三位成见换法、桌审多一条 ----

def test_socialreview_simulation参考文件存在且被manifest挂上():
    assert SOCIAL_REVIEW_SIM.exists(), "social-review 缺 reference/simulation.md"
    m = SOCIAL_REVIEW_MANIFEST.read_text(encoding="utf-8")
    assert "path: reference/simulation.md" in m


def test_socialreview_simulation三位成见换法():
    t = _t(SOCIAL_REVIEW_SIM)
    assert ("**「这是涌现还是模型先验」**——条件间的差异是模拟机制真的跑出来的，"
            "还是这个 LLM 对这类议题本来就有固定立场，条件怎么设都会收敛到同一个答案") in t
    assert ("**「提示词换个说法结论还在吗」**——访谈问题、角色设定换一种问法，"
            "结论会不会跟着变；没跑过提示词变体的，这条问不出答案，得写进局限，不能假装问过") in t
    assert "**「五条局限写全没」**——小人效度、版本漂移、提示词敏感、污染声明、单一模型，一条不能少" in t


def test_socialreview_simulation桌审看的正是机器兜不住的那几项():
    """审稿人要看的**恰好是没有代码牙齿的那五项**——复查机器已经查过的东西是浪费。

    这条跟 `test_socialpaper_simulation说的有牙齿的项跟代码查的一模一样` 是一对：
    代码查七项、审稿人看五项，两边加起来正好是配料单十二项，不重不漏。
    """
    from oss.api.paper import SIM_NO_TEETH, _SIM_REPORTABLE
    from oss.core.simbox import META_REQUIRED

    t = _t(SOCIAL_REVIEW_SIM)
    assert "桌审（`oss review --desk`）原有十条之外，仿真论文再加两条" in t
    for key in SIM_NO_TEETH:
        assert f"`{key}`" in t, f"没牙齿的 {key} 没交代给审稿人"
    assert set(META_REQUIRED) == set(_SIM_REPORTABLE) | set(SIM_NO_TEETH), "两边加起来不是十二项"
    # ODD 第四节与面向模式的验收也归审稿人
    assert "设计概念" in t and "事后挑模式就等于没验收" in t


# ---- Task 6 新文件的 docs/user 烂链哨——同一个盲区延伸一次 ----

def test_task6文件的docs用户引用全部指得到真文件():
    """同一招搬到 Task 6 新写/改的六份文件——`social-paper/reference/simulation.md`
    引了一处 `docs/user/sim-box.md`，堵住这个新盲区。"""
    pattern = re.compile(r"docs/user/[\w.\-]+\.md")
    files = [PLAN, PAPER, IDENTIFICATION, REVIEW, SOCIAL_PAPER_SIM, SOCIAL_REVIEW_SIM]
    matches: list[str] = []
    for f in files:
        matches += pattern.findall(_t(f))
    assert len(matches) >= 1, f"Task 6 六份文件里一处 docs/user/*.md 引用都没找到：{matches}"
    for rel in matches:
        assert (ROOT / rel).exists(), f"引用 {rel} 指不到真文件"
