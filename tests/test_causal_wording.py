"""因果措辞两档检查（设计稿第 4.4 节、附录 A）。

研究类型不是因果的论文不下因果结论：明确的说法（硬拦档）拒绝，模糊的（标出档）交识别审稿人判。
**宁漏勿滥**——每条豁免都是一类真会出现的误拦，这里一条一条钉住。
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from oss.core.causal_wording import FLAGS_FILE, hard_problems, scan


def _hard(text: str) -> list[str]:
    return [h.word for h in scan(text) if h.tier == "hard"]


def _tiers(text: str, word: str, **kw: str) -> list[tuple[str, str]]:
    return [(h.tier, h.note) for h in scan(text, **kw) if h.word.startswith(word)]


# ═══════════ 一、扫描器


def test_明确的因果说法进硬拦档() -> None:
    text = ("互联网普及导致了收入差距扩大。\n"
            "The reform causes higher output.\n"
            "We estimate the effect of internet use on income.\n")
    hard = {w.lower() for w in _hard(text)}
    assert "导致" in hard and "causes" in hard
    assert any(w.startswith("the effect of") for w in hard), hard


def test_否定语境不拦() -> None:
    assert not _hard("本文不能说明互联网导致收入差距扩大。")
    assert not _hard("These data cannot show that the reform causes growth.")


def test_转述别人的发现降为标出档() -> None:
    hits = [h for h in scan("已有研究发现，教育扩张导致生育率下降 {{cite:W1}}。") if h.word == "导致"]
    assert [(h.tier, h.note) for h in hits] == [("soft", "转述")]


def test_模糊说法只标出不拦() -> None:
    hits = scan("收入较高的群体生育意愿也较低，教育的影响在城市更明显。")
    assert "影响" in {h.word for h in hits if h.tier == "soft"}
    assert not [h for h in hits if h.tier == "hard"]


def test_引号代码表格与占位符里的不算() -> None:
    text = ("所谓「导致」只是受访者的原话。\n\n```\nx causes y\n```\n\n"
            "| 变量 | 导致 |\n|---|---|\n\n效应为 {{result:main.coef}}。\n")
    assert not _hard(text)


def test_统计术语不算() -> None:
    assert not scan("模型控制了省份固定效应与年份固定效应。We include unit fixed effects.")
    assert not scan("We include unit fixed-effects and random-effects.")


# 重放验收 2026-09-14：真稿里标出档中位数每篇 30 处，真描述类论文里一半是统计术语——
# 审稿人要逐条判，这一半白白稀释了真正要看的那几条
@pytest.mark.parametrize("term", ["最小可探测效应", "效应量", "设计效应", "主效应", "交互效应", "边际效应",
                                  "时期效应", "队列效应", "年龄效应", "固定效应", "随机效应", "民主主效应"])
def test_中文统计术语的效应不标出(term: str) -> None:
    assert not scan(f"表 2 报告了{term}。"), scan(f"表 2 报告了{term}。")


@pytest.mark.parametrize("term", ["minimum detectable effect", "minimum detectable effects", "smallest detectable effect",
                                  "minimum-detectable effect", "effect size", "effect sizes", "effect-size",
                                  "design effect", "design effects", "main effect", "main effects",
                                  "interaction effect", "interaction effects", "marginal effect", "marginal effects",
                                  "period effects", "cohort effects", "birth-cohort effects", "age effects",
                                  "fixed effects", "random effects"])
def test_英文统计术语的effect不标出(term: str) -> None:
    assert not scan(f"Table 2 reports the {term}."), scan(f"Table 2 reports the {term}.")


@pytest.mark.parametrize("text,word", [
    ("教育的效应在城市更大。", "效应"),
    ("The effect was larger in cities.", "effect"),
    # 排除只认整词：average 的尾巴不是 age；民主效应、雇主效应不是主效应
    ("The average effect was larger in cities.", "effect"),
    ("民主效应在该区间转负。", "效应"),
    ("雇主效应在城市更大。", "效应"),
])
def test_不是统计术语的效应照样标出(text: str, word: str) -> None:
    assert _tiers(text, word) == [("soft", "")], scan(text)


def test_统计术语的排除不碰硬拦档() -> None:
    assert _hard("主效应之外，教育导致了收入提高。") == ["导致"]
    assert [w.lower() for w in _hard("We estimate the effect of education on income; the effect size is 0.2.")] \
        == ["the effect of education on"]


def test_仿真限定在模型内只对仿真论文降为标出() -> None:
    """「在本模型内」是仿真论文的写法；回归论文写这几个字不算数。降为标出，不是放行。"""
    text = "在本模型内，信息扩散导致了极化。"
    assert _tiers(text, "导致", research_type="simulation") == [("soft", "模型内")]
    assert _hard(text)


@pytest.mark.parametrize("text", [
    "这一现象引起了学界的广泛关注。",
    "这一现象引起了学界和政策制定者的广泛关注。",
    "使用互联网的人收入也提高了；即使收入提高，差距仍在。",
    "教育不是收入差距扩大的原因。",
])
def test_长得像的非因果搭配不拦(text: str) -> None:
    assert not _hard(text), scan(text)


def test_豁免不能太宽() -> None:
    """否定词要整词（enormous 里的 nor 不算否定）；「in the model」回归论文里太常见，不当仿真限定。"""
    assert _hard("The enormous reform causes higher output.")
    assert _hard("The coefficient in the model shows that the reform causes growth.")


def test_拒绝理由点出行号与改法() -> None:
    msgs = hard_problems(scan("# 标题\n\n互联网普及导致收入差距扩大。\n"), "descriptive")
    assert msgs and msgs[0].startswith("第 3 行") and "描述" in msgs[0] and "相关" in msgs[0]
    down = hard_problems(scan("互联网普及导致收入差距扩大。\n"), "descriptive", downgraded=True)
    assert "从因果降级" in down[0]


def test_标出档写盘交审稿人(tmp_path: Path) -> None:
    from oss.core.causal_wording import write_flags

    write_flags(tmp_path, scan("教育的影响在城市更明显。互联网普及导致差距扩大。"), "descriptive")
    d = json.loads((tmp_path / FLAGS_FILE).read_text(encoding="utf-8"))
    assert d["research_type"] == "descriptive" and d["n"] == len(d["flags"]) >= 1
    assert all(f["tier"] == "soft" for f in d["flags"]), "硬拦档不进这份清单——它们已经拦下了"


# ═══════════ 二、否定认全：降级声明句本身不能被拦
#
# 15 篇真稿校准：23 条硬拦只有 2 条是真因果结论；最糟的是降级声明句本身
# （「results are reported as associations rather than causal effects」）被拦——
# 正确降级的论文反而导不出去。


@pytest.mark.parametrize("text", [
    "相关关系不等于因果关系。",
    "本文报告的是相关关系而非因果关系。",
    "本文不讨论因果关系。",
    "这一相关不意味着互联网使用导致收入提高。",
    "这些关联不应被理解为因果关系。",
    "本文不主张所报告的交互效应为因果效应。",
    "Results are reported as associations rather than causal effects.",
    "We describe patterns rather than causal effects.",
    "This does not necessarily imply that internet use at home causes higher income.",
    "Because the parallel-trends condition fails, we do not read the post-treatment path as a causal effect.",
    "This isn't evidence that the reform causes growth.",
])
def test_降级声明与否定句不拦(text: str) -> None:
    assert not _hard(text), scan(text)


@pytest.mark.parametrize("text", [
    "教育扩张导致了生育率下降。",
    "不仅如此，教育扩张还导致了生育率下降。",
    "教育扩张不断导致生育率下降。",
    "教育不光导致收入提高。",
    "收入不平等导致健康差距扩大。",
    "地区发展不平衡导致收入差距扩大。",
    "信息不对称导致逆向选择。",
    "The reform causes higher output.",
    "Education not only raises income but causes better health.",
    "There is no doubt that internet use causes higher income.",
    "Relative to never-treated countries democratization leads to higher growth.",
])
def test_长得像否定的因果说法照拦(text: str) -> None:
    """「不仅 / 不断 / 不平等 / 不平衡」里的「不」不是否定；not only、no doubt 不是；
    never-treated、not-yet-treated 也不是（DiD 术语；真稿里 `\\b(?:never|not)-\\w`，re.I，39 处、33 行）。"""
    assert _hard(text), scan(text)


@pytest.mark.parametrize("text", [
    "本文不能说明{{fig:fig1}}中互联网导致收入提高。",
    "We cannot claim that {{result:did.coef}} reflects that the reform causes growth.",
])
def test_占位符抹成的空白不占否定窗口(text: str) -> None:
    assert not _hard(text), scan(text)


def test_括注式引用也算转述() -> None:
    hits = [h for h in scan("Smith and Jones {{citep:sj2020}} show that education causes lower fertility.")
            if h.word == "causes"]
    assert [(h.tier, h.note) for h in hits] == [("soft", "转述")]


def test_占位符语法里每一种引用都算转述() -> None:
    """`manuscript.PLACEHOLDER` 以后多一种引用（citet 之类），扫描器不跟上就会把转述当成自己的结论拦下。"""
    from oss.core.manuscript import PLACEHOLDER

    kinds = [k for k in re.search(r"\(([a-z|]+)\)", PLACEHOLDER.pattern).group(1).split("|")
             if k.startswith("cit")]
    assert {"cite", "citep"} <= set(kinds)
    for k in kinds:
        hits = [h for h in scan("Education expansion led to lower fertility {{%s:W1}}." % k)
                if h.word == "led to"]
        assert [(h.tier, h.note) for h in hits] == [("soft", "转述")], k


# ═══════════ 三、拿不准的上下文：降为标出、带 note，不是放行
#
# 宁漏勿滥是对硬拦档说的——拿不准的降为标出，审稿人照样看得见。只有真否定整条不报。


@pytest.mark.parametrize("text, word", [
    ("删除缺失值导致样本量减少。", "导致"),
    ("合并会造成静默损失。", "造成"),
    ("剔除缺失值使得样本量减少。", "使得"),
    ("Listwise deletion results in a final sample of respondents.", "results in"),
    ("Measurement error may cause attenuation bias.", "cause"),
    ("This leads to biased estimates.", "leads to"),
    ("To assess the effect of sample weights on the estimates, we re-estimate the model.", "the effect of"),
])
def test_宾语是方法或数据处理的降为标出(text: str, word: str) -> None:
    assert _tiers(text, word) == [("soft", "方法")], scan(text)


def test_宾语是研究对象的照拦() -> None:
    assert _hard("教育导致收入差距扩大。")
    assert _hard("互联网使用导致收入提高，这一关系在城市样本中更明显。"), "逗号后面的「样本」不是它的宾语"


@pytest.mark.parametrize("text", [
    "There are plausible common causes of democratization and growth.",
    "The event-study plot runs from the leads to the lags.",
    "The results in Table 2 are robust.",
    "Results in column 3 show a gap.",
    "Deaths are coded as cause-specific mortality.",
    "There is cause for concern.",
])
def test_英文名词用法不拦(text: str) -> None:
    assert not _hard(text), scan(text)


def test_名词的cause还在谈因果_降为标出() -> None:
    """common causes of X and Y 说的是混淆，交审稿人看；the results in Table 2 一看就不是因果，整条不报。"""
    assert _tiers("There are plausible common causes of democratization and growth.", "causes") == [("soft", "名词")]
    assert not scan("The results in Table 2 are robust.")


@pytest.mark.parametrize("text", ["Democratization causes growth.", "The reform led to higher output."])
def test_动词用法照拦(text: str) -> None:
    assert _hard(text), scan(text)


@pytest.mark.parametrize("text, word", [
    ("Whether democratization causes economic growth is contested.", "causes"),
    ("Whether or not democratization causes economic growth is contested.", "causes"),
    ("If the effect of education on fertility is proportional, the estimate scales.", "the effect of"),
    ("教育是否导致生育率下降仍有争议。", "导致"),
    ("教育扩张导致生育率下降与否仍有争议。", "导致"),
    ("如果教育导致生育率下降，队列之间应当看到梯度。", "导致"),
])
def test_设问与假设降为标出(text: str, word: str) -> None:
    """研究问题里的设问要不要当因果说法算，是调用方（`oss run` 的门）的事；这里只管把 note 标对。
    whether or not 里的 not 不是否定。"""
    assert _tiers(text, word) == [("soft", "设问")], scan(text)


@pytest.mark.parametrize("text", [
    "与文献一致，本文发现互联网使用导致收入提高。",
    "We find that internet use causes higher income {{cite:x}}.",
    "互联网普及导致青年学者收入提高。",
    "本文发现互联网使用导致收入提高，这与已有研究一致。",
])
def test_自己的结论挂上文献也照拦(text: str) -> None:
    """文字记号（文献 / 学者）只在命中词之前才算转述；同一句里有本文 / 我们 / we，就是自己的结论。"""
    assert _hard(text), scan(text)


@pytest.mark.parametrize("text, word", [
    ("已有研究发现，教育扩张导致生育率下降 {{cite:W1}}。", "导致"),
    ("Education expansion led to lower fertility {{cite:W1}}.", "led to"),
    ("Smith et al. (2020) find that education causes lower fertility.", "causes"),
])
def test_转述别人的发现照旧标出(text: str, word: str) -> None:
    """引用占位符在句里任何位置都算；「et al.」的句点不是句末。"""
    assert _tiers(text, word) == [("soft", "转述")], scan(text)


def test_引号里的硬说法降为标出() -> None:
    assert _tiers("所谓「导致」只是受访者的原话。", "导致") == [("soft", "引号")]
    assert _tiers("受访者说“互联网导致收入提高”。", "导致") == [("soft", "引号")]
    assert not [h for h in scan("本文不能说明「互联网导致收入提高」。") if h.word == "导致"], "引号外的否定照样管用"


@pytest.mark.parametrize("text, word", [("教育是导致收入差距扩大的原因。", "导致"), ("政策致使收入降低。", "致使")])
def test_同一处只报一次(text: str, word: str) -> None:
    """重叠的式子只报先认领的那一处；被并掉的那一段（使收入降低）里的模糊词也不另报。"""
    assert [(h.tier, h.word) for h in scan(text)] == [("hard", word)], scan(text)


@pytest.mark.parametrize("text, word", [
    ("在控制了年龄、性别、户籍、受教育年限、婚姻状况与省份之后" * 3
     + "互联网使用导致收入提高，这一关系在城市和东部地区的样本中都更强，而且各个年龄段之间差异很大。", "导致"),
    ("Across all specifications, every subsample, each cohort and every region that we examined in the analysis, "
     "internet use causes higher income in the long run and in the short run alike.", "causes"),
])
def test_片段以命中词为中心(text: str, word: str) -> None:
    """长句里命中词在 80 字之后——旧的「整句截 60 字」把命中词本身截掉了。"""
    h = next(h for h in scan(text) if h.tier == "hard")
    assert h.word == word and word in h.snippet, h.snippet
    assert h.snippet.startswith("…") and h.snippet.endswith("…"), h.snippet
    assert len(h.snippet) <= 25 + len(word) + 35 + 2, h.snippet


@pytest.mark.parametrize("text", [
    "系数为 {{result:main.coef}}，互联网使用导致收入提高。",
    "The estimate of {{result:main.coef}} implies that the reform causes growth.",
])
def test_片段里占位符的空白并成一个(text: str) -> None:
    h = next(h for h in scan(text) if h.tier == "hard")
    assert h.word in h.snippet and "  " not in h.snippet, h.snippet


def test_命中处带列号() -> None:
    hits = [(h.line, h.start, h.tier) for h in scan("# 标题\n\n互联网普及导致收入差距扩大。所谓「导致」只是原话。")
            if h.word == "导致"]
    assert sorted(hits) == [(3, 5, "hard"), (3, 17, "soft")]
    text = "Smith and Jones {{citep:sj2020}} show that education causes lower fertility."
    assert [h.start for h in scan(text) if h.word == "causes"] == [text.index("causes")], "引用记号补到原长，列号不错位"


# ═══════════ 四、复查修补：放宽之后漏掉的明说，转述收紧带出来的误拦
#
# 规矩不变：只有否定「这个说法本身」才整条不报；上下文拿不准的降为标出、带 note。


@pytest.mark.parametrize("text, word", [
    ("{{cite:x}} 发现教育导致生育率下降，本文在此基础上进一步考察城乡差异。", "导致"),
    ("已有研究发现教育导致生育率下降 {{cite:x}}，但本文关注的是描述性差异。", "导致"),
    ("Smith et al. {{cite:s}} find that education causes lower fertility, which motivates our analysis.", "causes"),
    ("Building on {{cite:x}}, who show that education causes lower fertility, we examine the gradient.", "causes"),
])
def test_本文在后一个分句不抵消转述(text: str, word: str) -> None:
    """「本文 / we」只看命中词之前和命中所在的分句——后面另起一个分句说自己要做什么，前面仍是转述。"""
    assert _tiers(text, word) == [("soft", "转述")], scan(text)


@pytest.mark.parametrize("text", [
    "收入不足导致健康恶化。", "监管不力导致环境恶化。", "技能不匹配导致失业率上升。", "经营不善导致企业倒闭。",
    "健康状况不佳导致收入下降。", "信息不透明导致腐败蔓延。", "制度不完善导致收入差距扩大。",
    "发展不充分导致区域差距扩大。", "教育水平不高导致收入偏低。",
])
def test_不加形容词作主语照拦(text: str) -> None:
    assert _hard(text), scan(text)


def test_拿不准的不降为标出() -> None:
    """「不」后面既不是认定、推断一类的动词，也不是形容词：拿不准它否定的是不是这个说法，交审稿人。"""
    assert _tiers("不控制收入时教育导致生育率下降。", "导致") == [("soft", "否定存疑")]
    assert not _tiers("教育不导致生育率下降。", "导致"), "「不」直接否定命中词：真否定"
    assert not _tiers("本文不认为教育导致生育率下降。", "导致")


@pytest.mark.parametrize("text", [
    "教育而非收入导致了生育率下降。",
    "Education rather than income causes lower fertility.",
    "不是教育而是收入导致了生育率下降。",
    "It is not education but income that causes lower fertility.",
    "Not education but income causes lower fertility.",
    "教育而非收入是生育率下降的主要原因。",
    "Education rather than income is the main cause of lower fertility.",
])
def test_对比句里的而非与不是照拦(text: str) -> None:
    """「而非 / rather than」只直接否定名词（相关关系而非因果关系），或紧挨着的动词（而非导致）；
    「不是 A 而是 B」「not A but B」是把因果归给 B。「是……的原因」「is the cause of」是判断句，不是名词。"""
    assert _hard(text), scan(text)


@pytest.mark.parametrize("text, word", [
    ("This reflects composition rather than any causal effect of ageing.", "causal effect"),
    ("Rather than causing growth, democratization merely coincides with it.", "causing"),
    ("互联网使用与收入同步变化而非导致收入变化。", "导致"),
])
def test_而非直接否定名词或紧挨着的动词才放(text: str, word: str) -> None:
    assert not _tiers(text, word), scan(text)


@pytest.mark.parametrize("text", [
    "不能否认互联网导致了收入差距扩大。", "不可否认互联网导致了收入差距扩大。", "无法否认互联网导致了收入差距扩大。",
    "We cannot deny that internet use causes higher income.",
    "It cannot be denied that internet use causes higher income.",
    "这不会改变教育导致生育率下降的结论。",
    "This does not change the conclusion that education causes lower fertility.",
])
def test_双重否定与否定别的谓词照拦(text: str) -> None:
    assert _hard(text), scan(text)


@pytest.mark.parametrize("text, word", [
    ("难以排除「有些人本来就又富裕又快乐」这一稳定禀赋造成的混淆。", "造成"),
    ("不能排除互联网使用导致收入提高的可能。", "导致"),
    ("We cannot rule out that internet use causes higher income.", "causes"),
])
def test_不能排除降为标出(text: str, word: str) -> None:
    """引号在否定窗口里（真稿 relinc 第 8 行的写法）也照样认得出「难以排除」。"""
    assert _tiers(text, word) == [("soft", "可能")], scan(text)


def test_抵消词要紧跟否定词() -> None:
    """change 也是名词：「We do not claim that the change in policy causes growth」仍是真否定。"""
    assert not _tiers("We do not claim that the change in policy causes growth.", "causes")


@pytest.mark.parametrize("text", [
    "Even if we control for income, education causes lower fertility.",
    "如果控制收入，教育仍然导致生育率下降。",
    "无论控制与否，教育都导致生育率下降。",
    "Whether or not we include controls, education causes lower fertility.",
    "If anything, education causes lower fertility.",
])
def test_设问只看命中所在分句(text: str) -> None:
    assert _hard(text), scan(text)


@pytest.mark.parametrize("text", [
    "Even with no controls education causes higher income.",
    "In a model with no covariates the reform causes higher output.",
    "It is no accident that the reform causes higher output.",
    "Not surprisingly the reform causes higher output.",
])
def test_no作限定词不算否定(text: str) -> None:
    assert _hard(text), scan(text)


@pytest.mark.parametrize("text", [
    "We find no evidence that education causes higher income.",
    "We make no claim that internet use causes higher income.",
    "We find no direct evidence that education causes higher income.",
])
def test_no否定证据与主张才放(text: str) -> None:
    assert not _tiers(text, "causes"), scan(text)


@pytest.mark.parametrize("text", [
    "疫情导致经济损失扩大。", "教育扩张导致人才流失。", "政策导致医保覆盖率提高。",
    "互联网使用导致了观测到的收入差距。", "教育导致样本中女性的收入下降。",
])
def test_方法词有实义时照拦(text: str) -> None:
    assert _hard(text), scan(text)


@pytest.mark.parametrize("text, word", [
    ("第三，观测窗口造成的处理定义偏移。", "造成"),
    ("加权使估计值降低。", "使"),
    ("合并口径不同造成的样本差异。", "造成"),
    # 真跑（ossrun-d）抓出的漏拦：「跳问」是「跳答」的常见同义词，同样是数据处理
    ("其余受访者在文件中被记为 0，这些 0 是跳问造成的，本文一律视为缺失。", "造成"),
])
def test_主语是数据处理的降为标出(text: str, word: str) -> None:
    assert _tiers(text, word) == [("soft", "方法")], scan(text)


@pytest.mark.parametrize("text", [
    "Education is the main cause of the fertility decline.",
    "Education is a cause of lower fertility.",
    "Internet use is one of the leading causes of rising income.",
    "教育是生育率下降的主要原因。",
    "生育率下降的主要原因在于教育扩张。",
])
def test_是原因的判断句照拦(text: str) -> None:
    assert _hard(text), scan(text)


def test_共同原因仍是名词() -> None:
    """common causes 是混淆的说法，还在谈因果但不是在下结论——降为标出。"""
    assert _tiers("Age structure and urbanization are common causes of democratization and mortality.",
                  "causes") == [("soft", "名词")]


@pytest.mark.parametrize("text, word", [
    ("Time-use accounts hold that enrolment crowds out the activities that lead to early births.", "lead to"),
    ("On this view, the effect of democratization on population health should be larger.", "the effect of"),
    ("人力资本理论认为，教育导致生育率下降。", "导致"),
])
def test_复述理论算转述(text: str, word: str) -> None:
    assert _tiers(text, word) == [("soft", "转述")], scan(text)


def test_自己的模型不算转述() -> None:
    assert _hard("Our model predicts that the reform causes growth.")


def test_本文发现加引号仍是自己的结论() -> None:
    assert _hard("本文发现“互联网使用导致收入提高”。")
    assert _hard('We find that "internet use causes higher income".')
    assert _tiers("本文检验“教育导致生育率下降”这一假说。", "导致") == [("soft", "引号")], "检验一个假说不是下结论"


def test_研究问题问的是不是因果() -> None:
    """`oss run` 的门拿它判研究问题：硬拦的，加上设问（「是否导致」）与名义（「……的因果效应」——
    研究问题本来不下结论，名词类因果词在题目里必定降成名义，不算进来就等于把因果题放过去了）。"""
    from oss.core.causal_wording import CAUSAL_QUESTION_NOTES, asks_causal

    assert CAUSAL_QUESTION_NOTES == frozenset({"设问", "名义"})
    for q in ("教育扩张是否导致生育率下降", "Whether education expansion causes lower fertility",
              "Does education expansion cause lower fertility?", "The causal effect of internet use on income",
              "互联网使用对收入的因果效应"):
        assert asks_causal(scan(q)), scan(q)
    assert _tiers("The causal effect of internet use on income", "causal effect") == [("soft", "名义")]
    assert not asks_causal(scan("收入较高的群体生育意愿是否也较低"))


# ═══════════ 五、否定词补全（第三轮复查）
#
# 真稿 ossrun-a 第 85 行「…so nothing here identifies a causal effect of class position on hours.」
# 在 527ade1 是硬拦——免责声明本身挡住了导出。

_OSSRUN_A_85 = ("The design is associational. Province fixed effects absorb stable differences between local labour "
                "markets but not influences that vary within a province over time, and the rounds are independent "
                "samples, so nothing here identifies a causal effect of class position on hours.")


@pytest.mark.parametrize("text, word", [
    ("None of these estimates is a causal effect.", "causal effect"),
    ("Neither design identifies causal effects.", "causal effect"),
    ("We describe the gradient without implying that education causes lower fertility.", "causes"),
    ("There is little evidence that education causes lower fertility.", "causes"),
    ("Nobody should read these as causal effects.", "causal effect"),
    ("本文未识别因果效应。", "因果效应"),
    ("本文未对因果关系作出判断。", "因果关系"),
    ("这些系数毫无因果效应的含义。", "因果效应"),
    ("我们从未声称教育导致生育率下降。", "导致"),
    ("本文的估计非因果效应。", "因果效应"),
    ("本文的估计决非因果效应。", "因果效应"),
    ("本文未充分识别因果效应。", "因果效应"),
    (_OSSRUN_A_85, "causal effect"),
])
def test_补全的否定词让免责声明不拦(text: str, word: str) -> None:
    """「决非」跟「绝非」一样；「未」总是否定动词——不套「不」的形容词例外（未充分识别 ≠ 不充分）。"""
    assert not _tiers(text, word), scan(text)


@pytest.mark.parametrize("text", [
    "未婚女性比例上升导致生育率下降。",
    "未来研究发现教育导致生育率下降。",
    "这一政策非常显著地导致了收入提高。",
    "非农就业对收入存在因果效应。",
    "No one doubts that education causes lower fertility.",
    "Without controls the reform causes higher output.",
])
def test_长得像新否定词的照拦(text: str) -> None:
    """这几句在 527ade1 都是硬拦，补全否定词之后仍是：未婚、未来不是否定；「非」只在紧挨着命中词时才算
    （非常、非农不算）；No one doubts 是双重否定；without 只在 without implying 这类里算。"""
    assert _hard(text), scan(text)


def test_非不当对比词() -> None:
    """「非」要是跟「而非」一样否定名词，「大学与非大学两组的出身效应」就被当成否定、整条不报了。"""
    assert _tiers("大学与非大学两组的出身效应差距很大。", "效应") == [("soft", "")]


@pytest.mark.parametrize("text", [
    "Nothing suggests otherwise: education causes higher income.",
    "本文不讨论机制：教育导致生育率下降。",
])
def test_冒号截断否定窗口(text: str) -> None:
    """冒号前的否定管不到冒号后的断言。不截断，「Nothing suggests otherwise: …causes…」就整条漏掉。"""
    assert _hard(text), scan(text)


@pytest.mark.parametrize("text, word", [
    ("但本文的分箱诊断给出了一个不能忽略的限定：调节作用集中发生在互联网普及率极低的区间，此后基本平坦，"
     "与线性交互项所刻画的匀速衰减不符。", "作用"),                                          # 真稿 demint 第 99 行
    ("Two distinct adjustments carry that name and should not be conflated: the standard errors reported here "
     "are affected by the dispersion of the sampling weights and by clustering on province.", "affected"),  # ossrun-a 第 35 行
])
def test_冒号后的模糊词照样标出(text: str, word: str) -> None:
    """冒号前的「不能忽略 / should not be conflated」原来把冒号后的模糊词也吞了——真稿里这样少报了 4 条。"""
    assert _tiers(text, word) == [("soft", "")], scan(text)


# ═══════════ 六、名词类因果词只在「下结论」的句式里硬拦（名义；用户 2026-09-14 定 3A）
#
# 18 篇真稿里「因果关系 / 因果效应 / 因果影响 / causal effect(s) / causal impact(s)」出现 14 次，0 次是真结论：
# 描述论文里不带否定的用法都是局限、展望句。真稿 edufert 第 15 行
# 「Readers looking for the causal effect of schooling on fertility should…」原是硬拦，挡了导出。
# 动词（导致、causes……）与「the effect of … on」不走这条；否定照旧整条不报。


@pytest.mark.parametrize("text, word", [
    ("识别因果关系需要更强的研究设计。", "因果关系"),
    ("Future work should estimate the causal effect of schooling on fertility.", "causal effect"),
    ("Readers looking for the causal effect of schooling on fertility should consult designs with exogenous "
     "variation.", "causal effect"),                                                     # 真稿 edufert 第 15 行
    ("因果效应的识别有待更好的工具变量。", "因果效应"),
    ("有关因果关系的讨论见第五节。", "因果关系"),                    # 有关、只有、有必要里的「有」不是断言
    ("只有外生冲击才能识别因果效应。", "因果效应"),
    # 助动词 has / had 不算句式（「Few studies had estimated…」这类说文献的句子见第七节，记转述）
    ("This design has been used to estimate the causal effect of schooling on fertility.", "causal effect"),
    ("The design had been used to estimate the causal effect of schooling on fertility.", "causal effect"),
])
def test_名词类因果词不在下结论的句式里_降为标出(text: str, word: str) -> None:
    assert _tiers(text, word) == [("soft", "名义")], scan(text)


@pytest.mark.parametrize("text, word", [
    ("本文发现教育与生育率之间存在因果关系。", "因果关系"),
    ("Education has a causal effect on fertility.", "causal effect"),
    ("We find a causal effect of schooling on fertility.", "causal effect"),
    ("结果表明互联网使用对收入具有因果影响。", "因果影响"),
    ("互联网使用对收入有因果影响。", "因果影响"),
    ("本文发现互联网使用对收入存在因果效应与溢出效应。", "因果效应"),        # 「效应」里的「应」不是情态词
    ("非农就业对收入存在因果效应。", "因果效应"),
    # 过去时是结果一节的常规写法
    ("The results showed a causal effect of schooling on fertility.", "causal effect"),
    ("The analysis revealed a causal impact of internet use on income.", "causal impact"),
    ("Education had a causal effect on fertility in these cohorts.", "causal effect"),
    ("Education exerted a causal effect on fertility.", "causal effect"),
])
def test_名词类因果词在下结论的句式里照拦(text: str, word: str) -> None:
    assert _tiers(text, word) == [("hard", "")], scan(text)


@pytest.mark.parametrize("text, word", [
    ("本文认为二者可能存在因果关系。", "因果关系"),
    ("未来研究可以借助面板数据证实两者之间的因果关系。", "因果关系"),
    ("We might find a causal effect of schooling on fertility with better data.", "causal effect"),
])
def test_情态词让下结论的句式不算数(text: str, word: str) -> None:
    """「可能」在这里是情态词，不是 note「可能」——那条只认否定后面紧跟「排除 / rule out」（不能排除）；
    这几句前面几条都沾不上，走到最后一步，记名义。"""
    assert _tiers(text, word) == [("soft", "名义")], scan(text)


@pytest.mark.parametrize("text, word, note", [
    ("已有研究发现，教育与生育率之间存在因果关系 {{cite:W1}}。", "因果关系", "转述"),
    ("教育与生育率之间是否存在因果关系仍有争议。", "因果关系", "设问"),
    ("测量误差使因果效应的估计值偏小。", "因果效应", "方法"),
    ("所谓「因果关系」只是受访者的说法。", "因果关系", "引号"),
    ("不能排除二者之间存在因果关系。", "因果关系", "可能"),
    ("不控制收入时二者之间存在因果关系。", "因果关系", "否定存疑"),
])
def test_别的规则先降的保留原note(text: str, word: str, note: str) -> None:
    """名义是最后一步：别的 note 说出了句子是什么（谁的话、问句、方法、引号），比「没找到下结论的句式」多告诉审稿人一些。"""
    assert _tiers(text, word) == [("soft", note)], scan(text)


def test_仿真论文里模型内先于名义() -> None:
    text = "在本模型内，信息扩散对极化的因果效应更大。"
    assert _tiers(text, "因果效应", research_type="simulation") == [("soft", "模型内")]
    assert _tiers(text, "因果效应") == [("soft", "名义")]


def test_名义不碰否定_动词与the_effect_of() -> None:
    assert not _tiers("Results are reported as associations rather than causal effects.", "causal effect")
    assert not _tiers("本文报告的是相关关系而非因果关系。", "因果关系")
    assert _hard("We re-estimate the effect of democratization on log real GDP per capita.")  # 真稿 dem_prior 第 5 行
    # 「the effect of … on」与动词按决定不走名义：局限、展望句里也照拦
    assert _tiers("Estimating the effect of internet use on income would require an instrument.",
                  "the effect of") == [("hard", "")]
    assert _tiers("未来研究应解释互联网普及导致收入差距扩大的机制。", "导致") == [("hard", "")]
    assert _tiers("Future work should ask why the reform causes growth.", "causes") == [("hard", "")]


def test_已接受的漏拦_没有下结论句式的真主张降为标出() -> None:
    """已知、接受的损失（用户 2026-09-14 定 3A）：「……的因果影响在城市更大」是在下结论，但同一分句、
    命中词之前既没有本文 / 我们，也没有断言动词——降为名义，交审稿人判，不硬拦。改这条之前先回去看 18 篇真稿的校准。"""
    assert _tiers("互联网使用对收入的因果影响在城市更大。", "因果影响") == [("soft", "名义")]


# ═══════════ 七、英文的转述文献（2026-09-14）
#
# 中文的「文献 / 已有研究」早就算转述，英文只认 et al.、prior work 几个——「Studies show that education causes
# lower fertility」一直是硬拦。补上的文字记号跟中文同一条规矩：只在命中词之前算；我们 / our 在前就是自己的结论。


@pytest.mark.parametrize("text, word", [
    ("No study has identified the causal effect of schooling on fertility.", "causal effect"),
    ("Few studies have identified the causal effect of schooling on fertility.", "causal effect"),
    ("The literature has established a causal effect of schooling on fertility.", "causal effect"),
    ("Studies show that education causes lower fertility.", "causes"),
    ("A growing body of research finds that internet use leads to higher income.", "leads to"),
    ("Few studies have estimated the causal effect of schooling on fertility.", "causal effect"),
    ("Few studies had estimated the causal effect of schooling on fertility.", "causal effect"),
])
def test_英文的文献说法算转述(text: str, word: str) -> None:
    """转述排在名义前面：后两句原来记名义（助动词不算句式），现在记转述——说出了是谁的话，给审稿人的更多。"""
    assert _tiers(text, word) == [("soft", "转述")], scan(text)


@pytest.mark.parametrize("text, word", [
    ("The analysis has revealed a causal effect of schooling on fertility.", "causal effect"),
    ("We extend the literature and find that education causes lower fertility.", "causes"),
    ("Our results show that education causes lower fertility.", "causes"),
    ("Education causes lower fertility, as prior studies suggest.", "causes"),
])
def test_英文文献说法挡不住自己的结论(text: str, word: str) -> None:
    """we / our 在命中词之前，挂上文献也是自己的结论；文字记号在命中词之后（as prior studies suggest）不算——
    跟「本文发现互联网使用导致收入提高，这与已有研究一致」同一条：拿文献撑自己的结论，结论还是自己的。
    引用占位符不一样，在句里任何位置都算（加上 {{cite:x}} 就是转述）。"""
    assert _tiers(text, word) == [("hard", "")], scan(text)


# ═══════════ 八、主张框收紧（第四轮复查，2026-09-14）
#
# 光有本文 / 我们不算下结论——「本文对因果关系不作判断」「We remain agnostic about the causal effect…」是免责声明。
# 要有断言或估计动词；「有」只认紧挨着名词的（有 / 有着……的）；to identify 这类不定式不算；can / 才能 / 很难也是情态词；
# 名词后面跟着「尚不清楚 / 难以确定 / cannot be identified」这类存疑说法的也降为名义——故意不收单独的「不 / not」。
# 与文献唱反调（Unlike most studies, / 与已有研究不同，）后面的是作者自己的结论，不算转述。

_R4_LIMITS = [                                                    # 第四轮复查的 23 条探针：原来全是硬拦
    # 句式在前、否定或存疑在后
    "本文对因果关系不作判断。",
    "本文对因果关系持保留态度。",
    "本文将因果效应的识别留给后续研究。",
    "We remain agnostic about the causal effect of schooling on fertility.",
    "We take no position on causal effects.",
    "本文发现互联网使用与收入之间的因果关系尚不清楚。",
    "本文的结果表明教育与生育率之间的因果关系仍难以确定。",
    "Our results show that the causal effect of schooling on fertility cannot be identified with these data.",
    "We show that the causal effect of schooling on fertility is not identified in these data.",
    "存在的遗漏变量使因果效应无法识别。",
    # 「有」的复合词
    "工具变量法能够有效识别因果效应。",
    "只有随机实验才能有效识别因果效应。",
    "面板数据更有利于识别因果效应。",
    "这一设计有望识别因果效应。",
    # 不定式、can、很难
    "These data are insufficient to identify the causal effect of schooling on fertility.",
    "It is difficult to identify causal effects with cross-sectional data.",
    "Cross-sectional designs fail to identify causal effects.",
    "Only an instrument or a natural experiment can identify the causal effect of schooling on fertility.",
    "Randomized experiments can establish causal effects.",
    "A natural experiment is required to identify the causal effect.",
    "We have to be careful in interpreting causal effects.",
    "很难证明两者之间存在因果关系。",
    "横截面数据较难证实因果关系。",
]


def _nominal(text: str) -> list[tuple[str, str]]:
    return [(h.tier, h.note) for h in scan(text) if h.word.lower().startswith(("因果", "causal "))]


def test_复查探针是23条() -> None:
    assert len(_R4_LIMITS) == 23


@pytest.mark.parametrize("text", _R4_LIMITS)
def test_免责与局限句降为名义(text: str) -> None:
    assert _nominal(text) == [("soft", "名义")], scan(text)


@pytest.mark.parametrize("text", [
    "We estimate the causal effect of internet use on income.",
    "本文估计了互联网使用对收入的因果效应。",
    "本文估计的因果效应为正。",
    "This paper identifies a causal effect of schooling on fertility.",
    "本文发现教育与生育率之间存在因果关系。",
    "互联网使用对收入有显著的因果影响。",
    "互联网使用对收入有着显著而稳健的因果影响。",
    "The results show a causal effect that does not vary across cohorts.",
    "本文发现存在因果效应且不随时间变化。",
])
def test_断言或估计动词在前照拦(text: str) -> None:
    """「不随时间变化」「does not vary」否定的是别的谓词——存疑词表故意不收单独的「不 / not」。"""
    assert _nominal(text) == [("hard", "")], scan(text)


@pytest.mark.parametrize("text", [
    "Unlike most studies, this analysis finds that education causes lower fertility.",
    "Contrary to the literature, education causes lower fertility.",
    "In contrast to prior studies, the present study shows that education causes lower fertility.",
    "与已有研究不同，教育导致了生育率下降。",
    "不同于以往研究，教育导致了生育率下降。",
])
def test_与文献唱反调的是自己的结论(text: str) -> None:
    """this analysis / the present study 也是作者自己；对比词断了句之后的，是拿文献反衬自己的结论。"""
    assert _hard(text), scan(text)


@pytest.mark.parametrize("text, word", [
    ("Unlike Smith et al., who find that education causes lower fertility, we focus on levels.", "causes"),
    ("Unlike prior studies that find that education causes lower fertility, we focus on levels.", "causes"),
    ("In contrast to earlier work, which found that education causes lower fertility, we examine levels.",
     "causes"),
])
def test_唱反调里的别人发现仍是转述(text: str, word: str) -> None:
    """who / which 引出的、对比词后面还没断句的，说的是别人的发现；句里有引用占位符的也不抵消
    （Building on {{cite:x}}, who show… 见第四节）。"""
    assert _tiers(text, word) == [("soft", "转述")], scan(text)


# ═══════════ 二、接进 paper check / export


from oss.api import errors                                            # noqa: E402
from oss.api import paper as paper_api                                # noqa: E402
from oss.core.contracts import AnalysisPlan, AnalysisResults, Estimate, SpecSummary  # noqa: E402


def _paper(body: str) -> str:
    """能过既有校验的最小骨架（照 tests/test_sim_paper_checks.py），结果一节可换。"""
    return ("# 标题\n\n## 摘要\n效应 {{result:main.coef}}（n = {{result:main.n}}）。\n\n"
            "## 引言\n背景。\n\n## 数据与方法\n方法。\n\n"
            "## 结果\n*表 1. 主结果。*\n{{table:main}}\n" + body + "\n\n"
            "## 讨论\n### 局限\n识别策略有边界。\n")


def _run(tmp_path: Path, body: str, research_type: str = "descriptive") -> Path:
    run = tmp_path / "r"
    run.mkdir(parents=True)
    AnalysisResults(plan_id="p", n=100,
                    estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="ols_fe")).save(run / "results.json")
    AnalysisPlan(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="x",
                 research_type=research_type).save(run / "plan.json")
    (run / "paper.md").write_text(_paper(body), encoding="utf-8")
    return run


def _check(run: Path) -> tuple[list[str], list[str]]:
    try:
        res = paper_api.check(str(run))
        return [], list(res["data"]["warnings"])
    except errors.Refused as e:
        return list(e.problems), list(e.data["warnings"])


def test_描述类论文写导致_paper_check拒绝(tmp_path: Path) -> None:
    problems, _ = _check(_run(tmp_path, "互联网普及导致收入差距扩大。"))
    assert any("明确的因果说法" in p and "导致" in p for p in problems), problems


@pytest.mark.parametrize("rtype", ["causal", ""])
def test_因果类与没声明类型的论文不查措辞(tmp_path: Path, rtype: str) -> None:
    run = _run(tmp_path, "互联网普及导致收入差距扩大。", research_type=rtype)
    problems, _ = _check(run)
    assert not any("明确的因果说法" in p for p in problems), problems
    assert not (run / FLAGS_FILE).exists()


def test_模糊说法进清单且只警告(tmp_path: Path) -> None:
    run = _run(tmp_path, "教育的影响在城市更明显。")
    problems, warnings = _check(run)
    assert not any("明确的因果说法" in p for p in problems), problems
    assert any(FLAGS_FILE in w for w in warnings), warnings
    # 真跑（ossrun-d）抓出的坑：警告原话推着执行者把「效应」「经济冲击」这类标准术语也改掉——
    # 标出档是给审稿人判的，警告只该让执行者改真正因果口气的句子，术语照写
    assert any("只有确实是因果口气的句子才改" in w for w in warnings), warnings
    d = json.loads((run / FLAGS_FILE).read_text(encoding="utf-8"))
    assert any(f["word"] == "影响" for f in d["flags"])


def test_仿真论文限定在模型内的因果说法不拦(tmp_path: Path) -> None:
    run = _run(tmp_path, "在本模型内，信息扩散导致了极化。", research_type="simulation")
    problems, _ = _check(run)
    assert not any("明确的因果说法" in p for p in problems), problems


def test_描述类论文写导致_导出退出码八(tmp_path: Path) -> None:
    run = _run(tmp_path, "互联网普及导致收入差距扩大。")
    (run / "paper_rendered.md").write_text("# T\n", encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        paper_api.export(str(run))
    assert e.value.code == 8 and "导致" in e.value.summary


def _reviewed(run: Path) -> None:
    """三份 PASS 判词，凑够 `oss paper export` 的审稿门（5/3）——只为走到措辞门。"""
    for who in ("a", "b", "c"):
        (run / f"review_paper_rendered_{who}.json").write_text(
            json.dumps({"verdict": "PASS", "findings": [], "round": 1}), encoding="utf-8")


def test_导出兜底路径按渲染稿查因果措辞(tmp_path: Path) -> None:
    """没有 literature.json 时导出走文本兜底路径，转的是 paper_rendered.md——
    只改 paper.md 不重渲染，退出码 8 那道门也得查渲染稿，不能只查 paper.md。"""
    run = _run(tmp_path, "x")
    (run / "paper_rendered.md").write_text(_paper("互联网普及导致收入差距扩大。"), encoding="utf-8")
    _reviewed(run)
    with pytest.raises(errors.Refused) as e:
        paper_api.export(str(run))
    assert e.value.code == 8 and "导致" in e.value.summary and "重跑 oss paper render" in e.value.summary


def _hide_pandoc(monkeypatch: pytest.MonkeyPatch) -> None:
    """照 CI 的样子：只让 pandoc 找不到，别的命令照常找得到。"""
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **kw: None if name == "pandoc" else real_which(name, *a, **kw))
    assert shutil.which("pandoc") is None


def test_导出兜底路径按渲染稿查因果措辞_没装pandoc也先报措辞(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CI 不装 pandoc。该拒的稿子先按措辞拒（退出码 8），轮不到「没装 pandoc」（退出码 1）——
    兜底路径这道门也在查工具之前判完，装没装 pandoc 结果都一样。"""
    _hide_pandoc(monkeypatch)
    test_导出兜底路径按渲染稿查因果措辞(tmp_path)


# ═══════════ 三、Task 11c：识别审计穷尽却仍标因果——按已降级查措辞（用户 2026-09-14 决定 4A）


def _trace_row(run: Path, seq: int, **row) -> None:
    """往留痕里补一行（照 `identification_gate` 认的形状）。这个模块的 `_run` 不建
    `trace.jsonl`——这里手写、不经 `oss run`，测的是 `wording_scope` 本身，不是全链。"""
    with (run / "trace.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"seq": seq, "outcome": "ok", **row}, ensure_ascii=False) + "\n")


def test_审计穷尽却仍标因果_paper_check按已降级查措辞并警告(tmp_path: Path) -> None:
    """计划没跟着从 causal 改成 descriptive，但审计判 FAIL、替代设计也穷尽了——
    `paper check` 不该再放行硬因果说法，还要提醒把 plan.json 一并改掉。"""
    run = _run(tmp_path, "互联网普及导致收入差距扩大。", research_type="causal")
    _trace_row(run, 1, step="review", command="oss review --identification",
               artifacts=["review_plan.json"], note="FAIL by x")
    _trace_row(run, 2, step="design_alternatives_exhausted", command="oss trace save",
               note="换窗口、换对照组都试过，都不可行")
    problems, warnings = _check(run)
    assert any("明确的因果说法" in p and "导致" in p for p in problems), problems
    assert any("识别审计判 FAIL" in w and "已穷尽" in w and "descriptive" in w for w in warnings), warnings


def test_审计穷尽却仍标因果_导出退出码八(tmp_path: Path) -> None:
    run = _run(tmp_path, "互联网普及导致收入差距扩大。", research_type="causal")
    _trace_row(run, 1, step="review", command="oss review --identification",
               artifacts=["review_plan.json"], note="FAIL by x")
    _trace_row(run, 2, step="design_alternatives_exhausted", command="oss trace save",
               note="换窗口、换对照组都试过，都不可行")
    (run / "paper_rendered.md").write_text("# T\n", encoding="utf-8")
    with pytest.raises(errors.Refused) as e:
        paper_api.export(str(run))
    assert e.value.code == 8 and "导致" in e.value.summary


def test_平行趋势不成立不足以强制降级查措辞(tmp_path: Path) -> None:
    """用户 4A 明说：平行趋势不成立只*允许*改计划降级，单独一条不强制——
    没有审计 FAIL、替代设计穷尽这两行留痕，计划还是 causal，措辞检查不查。"""
    run = tmp_path / "r"
    run.mkdir(parents=True)
    AnalysisResults(plan_id="p", n=100,
                    estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.001)},
                    spec=SpecSummary(estimator="did"),
                    diagnostics={"平行趋势": "**不成立**：预趋势不平行"}).save(run / "results.json")
    AnalysisPlan(plan_id="p", idea_id="i", estimator="did", outcome="y", treatment="x",
                 research_type="causal").save(run / "plan.json")
    (run / "paper.md").write_text(_paper("互联网普及导致收入差距扩大。"), encoding="utf-8")
    problems, _ = _check(run)
    assert not any("明确的因果说法" in p for p in problems), problems
    assert not (run / FLAGS_FILE).exists()


def test_因果计划没有审计留痕_仍不查措辞(tmp_path: Path) -> None:
    """没有 FAIL、没有穷尽留痕——`forced_downgrade` 该是 False，回归既有基线。"""
    run = _run(tmp_path, "互联网普及导致收入差距扩大。", research_type="causal")
    problems, _ = _check(run)
    assert not any("明确的因果说法" in p for p in problems), problems
    assert not (run / FLAGS_FILE).exists()


# ═══════════ 九、公开的纯函数（补门的洞·Task 3）


from oss.core.causal_wording import negated, sim_reality_warnings, soft_warning  # noqa: E402
from oss.core.findings import code_of                                            # noqa: E402


@pytest.mark.parametrize("head, word", [
    ("本文不能说明互联网", "导致"),
    ("These data cannot show that the reform ", "causes"),
])
def test_否定薄壳认得出否定语境(head: str, word: str) -> None:
    assert negated(head, word)


def test_否定薄壳不把不是A而是B当否定() -> None:
    """「不是收入而是教育导致……」把因果归给了教育——`_negation` 的老判据，薄壳照传。"""
    assert not negated("不是收入而是教育", "导致")
    assert not negated("互联网普及", "导致")


def test_否定薄壳把nounish原样传下去() -> None:
    """「而非」只直接否定名词：同一段话，命中词是名词性的才算否定。"""
    assert not negated("教育而非收入", "导致")
    assert negated("教育而非收入", "导致", nounish=True)


def test_软措辞一处都没有时不汇报() -> None:
    assert soft_warning(scan("受教育年限较高的群体收入也较高。")) is None


def test_软措辞汇成一条带条数与编号() -> None:
    out = soft_warning(scan("受教育年限对收入有显著影响。"))
    assert out is not None and out.startswith("1 处模糊的因果说法"), out
    assert code_of(out) == "wording.causal_soft" and FLAGS_FILE in out


def test_仿真现实断言逐句报() -> None:
    out = sim_reality_warnings("主效应为正。现实中将出现同样的迁移潮。政策应当立即推广该机制。")
    assert len(out) == 2, out
    assert all(code_of(w) == "sim.reality" for w in out)
    assert any("现实中将出现同样的迁移潮" in w for w in out), out


def test_仿真现实断言限定在本模型内就豁免() -> None:
    assert not sim_reality_warnings("这一效应仅在本模型内成立，不宜外推为现实世界会同样迁移。")
    assert not sim_reality_warnings("主效应为正，稳健性一致。")


@pytest.mark.parametrize("paper", [
    "在本模型内，参数上升；现实中将出现同样的迁移潮。",
    "在本模型内 the agents migrate. 现实中将出现同样的迁移潮。",
    "在本模型内，政策应当立即推广；现实中会有同样的效果。",
])
def test_仿真现实断言的豁免窗口比句子宽(paper: str) -> None:
    """在句首写了「在本模型内」、后面用分号或英文句点接着说，整段都算限定过。

    这条的窗口只按中文句末与换行切。换成 `textspan.sentences()` 会多切分号与
    英文句点，把豁免切掉——作者正当的写法就成了误报。
    """
    assert not sim_reality_warnings(paper), paper


def test_仿真现实断言引的那一句不含句末标点() -> None:
    """窗口按 `re.split` 切，分隔符丢掉，引出来的句子**不带句号**。

    这是用户看得见的字，逐字节钉死：并切句时曾静默多出一个句号。
    """
    out = sim_reality_warnings("主效应为正。现实中将出现同样的迁移潮。")
    assert [str(w) for w in out] == [
        "仿真结论跨出模型边界：「现实中将出现同样的迁移潮」——"
        "没限定「在本模型内」就断言现实/政策，措辞收回模型内"]


# ═══════════ 十、词表外的八种写法（注入测试 injection-v2 逐条核出来的）

#: 评测里 15 个变体中词表外的 8 句，原样搬过来——改词表就是改考卷答案，两边必须对得上
_v2_漏拦 = [
    ("带来了", "延长受教育年限带来了完成生育子女数的下降。"),
    ("让", "多受几年教育让女性少生了孩子。"),
    ("源于", "完成生育子女数较少，源于受教育年限的延长。"),
    ("是因为", "受教育年限较长的女性子女较少，是因为教育推迟了她们的初育。"),
    ("由于", "由于受教育年限较长，女性的子女数较少。"),
    ("引发", "教育年限的延长引发了生育的下降。"),
    ("压低", "受教育年限的延长压低了女性的完成生育。"),
    ("归因于", "女性子女数较少可以归因于受教育年限的延长。"),
]


@pytest.mark.parametrize("word,text", _v2_漏拦)
def test_词表外的八种写法进硬拦档(word: str, text: str) -> None:
    assert any(word in h.word for h in scan(text) if h.tier == "hard"), scan(text)


@pytest.mark.parametrize("text", [
    # 「让」：单字「让」是常用动词，只有「让 + 对象 + 升降」才是结论句式
    "让我们先看两组的均值差。",
    "这一差距会让估计向零偏，因而本文的估计应读作效应的下界。",
    "分组的口径让读者更容易对照表 2。",
    # 「由于」：方法与局限里的常规说法，绝不能拦
    "由于篇幅所限，这里不再展开两组的分布。",
    "由于数据限制，本文无法区分这两类国家。",
    "由于各国上调法定年限的时间不同，本文采用分批双重差分估计量。",
    "由于处理前走势平行这一假设被拒绝，点估计本身混有处理前的分化。",
    "由于 1998 年之前的年限记录在可及的数据源里不存在，本文所说的「从未上调」只是一个窗口内的说法。",
])
def test_让与由于的反向例子不拦(text: str) -> None:
    assert not _hard(text), scan(text)


@pytest.mark.parametrize("text", [
    # 归因类三个词单用都是方法、转述、局限里的常规说法——真稿里逐条抄回来的
    "之所以采用轮次哑变量而非线性轮次，是因为后者会强加一个线性的函数形式。",
    "前者把民主的健康红利归因于选举问责，后者则报告互联网普及能改善治理。",
    "在集体性的经济收缩中，收入下降被广泛地归因于外部环境而非个人失败。",
    "这一反转约有一半可归因于父母一代平均受教育水平的上升。",
    "其对幸福感的弱关联不宜简单归因于测量失败。",
    # 施动类：没有「了」的一律不算下结论——政策意图、理论复述、条件句
    "各国政府把延长义务教育年限当作压低生育率的政策抓手。",
    "教育抬高女性时间的机会成本，两者都压低子女数量。",
    "这一设计引发的争议在文献里讨论了很久。",
    "民主是否带来民生改善，已有文献给出了相对稳定的正向结论。",
])
def test_归因类与施动类的反向例子不拦(text: str) -> None:
    assert not _hard(text), scan(text)


@pytest.mark.parametrize("text,word", [
    ("女性子女数较少并非由于受教育年限的延长。", "由于"),
    ("子女数的下降不能归因于受教育年限的延长。", "归因于"),
    ("子女数较少难以归因于受教育年限的延长。", "归因于"),
    ("子女较少不是因为教育推迟了初育。", "是因为"),
])
def test_八种新写法照旧吃否定豁免(text: str, word: str) -> None:
    assert not _hard(text), (word, scan(text))
