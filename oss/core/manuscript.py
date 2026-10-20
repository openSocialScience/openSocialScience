"""论文的占位符解析与完整性校验。

**这一段防的是整套系统最危险的失败模式：产出一篇排版漂亮、引用齐全、
数字是手打的论文。**

机制：正文只允许写占位符，数字在渲染期由程序从 `results.json` 填。
**写得再差也不会编造数字。**

这条规则写在 skill 里没用——模型手打一个 `β = 0.032` 太容易了。
必须是渲染前的硬校验。

## 「裸数字」怎么判

太松防不住编造，太严连年份都写不了。可判据的规则是：

**禁止小数。**统计结果几乎总是小数（系数、标准误、p 值），
而年份、样本量、节号都是整数。

整数不拦，但**如果它恰好等于 results.json 里的某个值就警告**——
那多半是手打的样本量。
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from oss.core import textspan
from oss.core.contracts import VALIDITY_MENU, AnalysisResults
from oss.core.findings import FINDING_CODES, Finding, code_of

#: 占位符：`{{result:main.coef}}` / `{{power:power}}` / `{{diag:组内 R²}}`
#:
#: **三个命名空间，因为诚实的论文要引用三处的数字：**
#:   result  估计结果       —— 主效应、标准误、p 值、样本量
#:   power   功效报告       —— 功效、设计效应、有效样本量（**局限那一节要用**）
#:   diag    回归诊断       —— 组内 R²、吸收的自由度、聚类方式
#:
#: 只做 result 的话，skill 写局限时会被逼着手打功效和设计效应——
#: 而那正是这套机制要防的事。这个缺口是**真写一篇论文才发现的**。
#:   robust  稳健性检验    —— `{{robust:<label>/main.coef}}`，label 与后半用 `/` 隔开
#:
#: 加 robust 是因为：稳健性结果如果引用不了，那一节就只能写
#: 「同号同显著」——**诚实的写法被机制堵死**，跟当初只做 result 命名空间时
#: 局限那节写不了功效是同一个毛病。
#:   bin     分箱估计      —— `{{bin:<组名>/<first|last|序号>.slope}}`
#:
#: 加 bin 是因为：论文最强的事实断言常常来自一张图（「最晚一箱两组已无实质差别」），
#: 而图上的数字如果不落盘，这句话就**没有占位符可用**，只能用散文写——
#: 而散文正是主张核验管不住的地方。三轮互盲审稿里两位审稿人独立指出了这一条。
#:   cite    文献引用      —— `{{cite:<文献编号或 DOI>}}`
#:
#: 加 cite 是因为：核实过的现状是论文里提到 11 个人名，**没有参考文献列表、
#: 没有年份、没有 DOI**，也没有任何记录说明「正文这句话对应语料里的哪一篇」。
#: 既验证不了转述对不对，也发现不了引了一篇不存在的文献。
#: **核对的前提是有东西可核。**
#:   fig     诊断图        —— `{{fig:robustness}}`，渲染成嵌入正文的图片
#:
#: 加 fig 是因为：六张诊断图一直躺在运行目录里，**正文一张都没嵌**——
#: 一篇没有图的定量论文不是成品。跟数字同一套占位机制：
#: 校验期核「这张图存在吗」，渲染期换成图片引用——引一张不存在的图会被拦，
#: 跟引一个不存在的估计一样。
#: `cite` 是叙述式引用「Kudamatsu (2007) found…」，`citep` 是括注式「(Kudamatsu 2007)」。
#: 两种都从语料填，导出时都交给 citeproc 按目标期刊的格式排。
#: `table` 是三张表：`descriptives`（描述统计）、`main`（主结果）、`robustness`（稳健性），
#: 外加 `cohorts`（分批各批效应）、`event`（逐期效应）。评审：「所有系数用散文报出来，
#: 任何量化论文这都是硬伤」。表跟数字同一套机制——渲染期从结果文件排出来，不手打。
PLACEHOLDER = re.compile(r"\{\{(result|power|diag|robust|bin|cite|citep|fig|table):([^}]+)\}\}")

#: 图注在图占位符**下一行**、表注在表占位符**上一行**（期刊惯例：图注在下、表注在上）。
_CAPTION_FIG = re.compile(r"^\*?\s*(?:Figure|Fig\.|图)\s*\d+", re.I)
_CAPTION_TAB = re.compile(r"^\*?\s*(?:Table|表)\s*\d+", re.I)

#: **研究过程不属于正文。**验收跑的论文里写了 "the automated control-group diagnostic"、
#: "an independent design audit"、"a verdict of failure"、"the R3 specification"，
#: Limitations 里还有一段「审计报告有两处错误，备案」。评审：「没有任何期刊接受这个——
#: 审稿 / 审计过程属于附录或回应信，不属于正文」；编辑 30 秒内就会形成负面印象。
#: 这些词一出现就是硬伤，校验拒绝。表格行（`|` 开头）和代码不查。
_PROCESS_EN: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\baudit(?:or|ors|ed|ing|s)?\b", re.I), "audit"),
    (re.compile(r"\bautomated\b", re.I), "automated"),
    (re.compile(r"\btool(?:ing|box|chain)\b", re.I), "tooling"),
    (re.compile(r"\bpipeline\b", re.I), "pipeline"),
    (re.compile(r"\bverdicts?\b", re.I), "verdict"),
    (re.compile(r"\bgr[ae]y band\b", re.I), "gray band"),
    (re.compile(r"\b[\w-]+\.(?:json|py|csv|md|jsonl)\b", re.I), "file name"),
    # `oss <子命令>` 原来手抄成一张十来个词的表，现在从说明书里现取，见 `system_names`
    (re.compile(r"\bR\d{1,2}\b"), "specification label (R1/R3…)"),
    (re.compile(r"\b(?:the|an?|our) (?:analysis|tool|toolbox|system|software|program|script) "
                r"(?:attach|report|return|flag|produce|generat|declar)\w*", re.I), "the tool did X"),
    (re.compile(r"\bdiagnostics? (?:report|flag|return|indicate)s?\b", re.I), "the diagnostic reports"),
    (re.compile(r"\b(?:design|causal|independent|external) audit\b", re.I), "design audit"),
    (re.compile(r"\bfor the record\b", re.I), "for the record"),
    (re.compile(r"\bcross-model\b", re.I), "cross-model"),
    (re.compile(r"\bexecutor\b", re.I), "executor"),
    (re.compile(r"\b(?:review|reviewer|referee) (?:round|report|rounds|reports)\b", re.I), "review round"),
    (re.compile(r"\bskill (?:file|instruction)s?\b", re.I), "skill file"),
]
#: 修改史句式。**「先试了 A，再改成 B」是漏拦那几句的共同形状**（注入测试里七句漏拦有两句是它）。两支：
#:
#: - `先 / 起初 / 最初` + `试了 / 跑了`——「试」「跑」本身说的就是研究者在试，不会是在讲世界
#: - `先 / 起初 / 最初` + `定了 / 声明的……` + `再 / 后来` + `改成 / 换为`——这一支**要配方法名**：
#:   「各国最初把义务教育定为六年，后来改为九年」讲的是政策史，是正当的正文内容
_REVISION = re.compile(
    r"(?:先|起初|最初)(?:[^。；]{0,16}?(?:试|跑)了"
    r"|(?=[^。；]{0,70}(?:估计|回归|模型|规格|设定|固定效应|双重差分|工具变量|断点|倾向得分"
    r"|分箱|分组|聚类|加权|控制变量|标准误|口径|样本|检验))"
    r"[^。；]{0,16}?(?:用|定|设|写|声明|选)[了的][^。；]{0,20}?(?:再|后来|随后|之后)[^。；]{0,8}?(?:改|换)[成为用])")

#: 中文这一路。**系统专名不在这里**——环节名、`oss` 子命令、技能名从真名现取（`system_names`）。
#: 手抄的名字表正是「说明书」那次误报的来源：本系统的「说明书」指技能文件，而**问卷说明书是正当的正文内容**
#: （注入测试丁组唯一的误报就是它）。生成的表里只有真名（`1-profile`、`oss run`、`social-paper`），
#: 撞不上普通名词——所以这一条**不是靠加豁免解开的，是靠不再手抄名字**。
#: 剩在这里的是**过程词汇**，不是名字，生成不出来（真稿里「跨模型」「审计」一抓一大把）。
_PROCESS_ZH: list[tuple[re.Pattern, str]] = [
    (_REVISION, "修改史"),
    # 「工具做了什么」——英文表里 "the tool did X" / "the diagnostic reports" 的中文对面。
    # 「工具」要排掉「工具变量」、「程序」要排掉「程序性」；**「系统 / 软件」不收**：
    # 「医疗系统的数据显示」「统计软件」都是正当的正文说法
    (re.compile(r"(?:工具(?!变量)|程序(?!性)|脚本|结果文件|运行日志)"
                r"[^。；]{0,10}?(?:给出|报出|返回|标出|构造|生成|算出|显示|报错|拦下|放行)"), "工具做了什么"),
    # 「评审 / 审稿 / 复审 / 裁决 + 指出」——英文表里 review round / cross-model 的中文对面
    (re.compile(r"(?:评审|审稿|复审|裁决)[^。；]{0,12}?(?:指出|抓到|提出|要求|翻转)"), "评审指出"),
] + [
    (re.compile(w), w) for w in
    # 「报错」跟英文表里的 verdict、diagnostic reports 同一口径；「说明书」退场，理由见上。
    # **「判词」只收「灰区判词」，不收单字**：英文表里 `verdict` 是裸词，中文这边不能照搬——
    # `social-paper/reference/read-first.md` 明写着功效不敏感时要在局限里补一句
    # 「判词对折价系数与灰区上下界均不敏感」，裸收就把说明书自己教的话拦了
    # （`social-research/stages/7-paper.md` 的禁止清单里却列着「判词」，两份说明书本身就对不上，记进待办）
    ("审计", "工具箱", "执行者", "占位符", "留痕", "跨模型", "陪审团", "灰区判词", "报错")
]


def skills_src() -> Path:
    """说明书的**源头**：装好的包里在 `oss/skills/`，开发时在仓库的 `.claude/skills/`。

    `oss install-skills`、`oss doctor` 与下面的系统专名表问的是同一个地方——**只此一份**。
    """
    pkg = Path(__file__).resolve().parents[1] / "skills"
    return pkg if pkg.exists() else Path(__file__).resolve().parents[2] / ".claude" / "skills"


@lru_cache(maxsize=1)
def system_names() -> tuple[str, ...]:
    """系统专名：环节名、`oss` 子命令、技能名，**从真名现取，不手抄**。

    三处来源都是真的，表因此自动跟着代码走——以后加一个环节不用回来补词：

    - `FINDING_CODES` 里每条发现登记的命令串（`oss run`、`oss paper check`）。
      装好的包里没有说明书时只剩这一路，所以它排在最前
    - `<技能>/stages/*.md` 的文件名（`1-profile`、`7.5-identification`）与首行标题里的「环节 7.5」
    - 技能目录名 = 技能名 = 角色名（`scripts/validate_skills.py` 核过 `name` 跟目录名一致）
    - 说明书里出现的 `oss <子命令>`（同一份校验核过这些命令在 typer 的命令树里真的存在）

    **只收真名**：生成不出「说明书」这种普通名词，撞不上正文。读盘一次，`lru_cache` 缓存。
    """
    names: set[str] = set()
    for cmd, _hard, _what in FINDING_CODES.values():
        names.update(one.strip() for one in cmd.split("、") if one.strip())
    root = skills_src()
    if root.is_dir():
        for skill in sorted(p for p in root.iterdir() if p.is_dir()):
            names.add(skill.name)
            for stage in sorted((skill / "stages").glob("*.md")):
                if stage.name.endswith(".notes.md"):
                    continue                            # 散文不是契约，文件名不是环节名
                names.add(stage.stem)
                head = stage.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
                m = re.match(r"#\s*(环节\s*[0-9.]+)", head)
                if m:
                    names.add(m.group(1))
            for md in sorted(skill.rglob("*.md")):
                text = md.read_text(encoding="utf-8", errors="replace")
                names.update(f"oss {m.group(1)}" for m in re.finditer(r"\boss ([a-z][a-z0-9-]*)", text))
    return tuple(sorted(names))


@lru_cache(maxsize=1)
def _system_name_rule() -> tuple[re.Pattern, str]:
    # 名字里的空格按空白类匹配——「环节 7.5」排版折过来也还是它
    return re.compile("|".join(re.escape(n).replace(" ", r"\s+") for n in system_names())), "系统专名"


#: 参考文献列表的占位。**只列被引用过的**——
#: 引了一定在列表里、没引的不许出现。后者是很常见的凑数手法。
REFERENCES = re.compile(r"\{\{references\}\}")

#: 归属动词。判断一个大写拉丁词是不是在被当作「某人说过什么」用。
#: 没有归属动词的大写词多半是术语（`SEI 指数`），不是引用。
_ATTRIB = ("发现", "指出", "提出", "报告", "认为", "主张", "论证", "强调",
           "建立", "证明", "表明", "的研究", "的比较", "的结论", "所说", "一脉",
           # 英文归属动词。**小写形式**——匹配前先把句子 lower 的调用方负责
           " found", " argue", " argues", " argued", " show", " shows", " showed",
           " propose", " reports", " reported", " claims", " demonstrates",
           " suggests", " according to", " et al")

#: 统计学与数据的专名，不是引用。**误报会让人直接忽略这个检查。**
#: 最后一行是**两个字母**的缩写与专名：姓的索引放宽到两个字母之后，这一类最容易撞上
#: （`US 的数据`、`AI 技术`）。只按原样大小写比，`Ai`、`Se` 这类真姓照旧进得了索引。
_NOT_CITATIONS = frozenset("""
GSS CGSS CFPS CHARLS CHFS ISSP ESS WVS IPUMS NBER SSRN OpenAlex Crossref PubMed
OLS VIF ICC APC SEI ISEI EGP HC1 DOI MMI EMI SES FWL DID IV RDD GMM MLE OLS2SLS
Cohen Moulton Frisch Waugh Lovell Bonferroni Wald Huber White Newey West
Stata Python Excel CSV JSON Nature Science
Table Tables Figure Figures Fig Panel Appendix Notes Cohort Cohorts Period Sample Column Row
SE SD CI OR RR HR AR DV ID AI ML IT US UK EU UN
""".split())

#: NFKD 拆不开的几个拉丁字母：它们不带组合记号，规范化之后原样返回。
#: `Kırdar` → `Kirdar`、`Løken` → `Loken` 靠这张表，不靠 NFKD。
_FOLD_EXTRA = str.maketrans({"ı": "i", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L",
                             "đ": "d", "Đ": "D", "ß": "ss",
                             "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE"})


def _fold(s: str) -> str:
    """去掉变音记号：`Götmark` → `Gotmark`、`Kırdar` → `Kirdar`。

    **语料侧的姓与正文各折一次，用的必须是同一个函数**——两边折法不一致就等于没折。
    """
    plain = "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))
    return plain.translate(_FOLD_EXTRA)


def _surname(author: str) -> str:
    """从「Richard Breen」里取「Breen」；带逗号的按「姓, 名」读（`Yin, Haijie` → `Yin`）。"""
    author = (author or "").strip()
    if "," in author:
        return author.split(",", 1)[0].strip()
    parts = [t.strip(".,;") for t in author.split() if t.strip(".,;")]
    return parts[-1] if parts else ""


_CJK_CHARS = re.compile(r"[一-鿿]")


def _is_english(paper: str) -> bool:
    """这篇论文是不是英文写的。判据：中日韩字符占比不到 5%。

    引用标注（`et al.` vs 「等」）和参考文献格式都跟着这个走——
    **让作者声明语言是多余的一步**，文本自己就能回答。"""
    if not paper:
        return False
    return len(_CJK_CHARS.findall(paper)) < 0.05 * len(paper)


def _cite_who(rec, en: bool = False) -> str:
    """引用里作者那一半：「Breen」「Raftery 与 Hout」「Camerer 等」。

    单独拆出来，是因为**编号体例下也要用它**：GB/T 顺序编码的叙述式引用是
    「卡德威尔[1] 认为」——作者名写在正文里，编号只作上标。而 pandoc 的 `@key`
    在编号体例下只排出数字，作者名整个丢掉，正文就成了「[1] 认为」。
    """
    # 英文稿里用罗马化的作者名——不然正文会出现「尹海洁 and 王翌佳 (2015) find…」。
    # 参考文献表那条路（`csl_json`）用的是同一份 `authors_romanized`
    src = _en_authors(rec) if en else (rec.authors or [])
    names = [_surname(a) for a in src if _surname(a)]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}" if en else f"{names[0]} 与 {names[1]}"
    return f"{names[0]} et al." if en else f"{names[0]} 等"


def _cite_label(rec, en: bool = False, parenthetical: bool = False) -> str:
    """渲染成「Breen (2010)」「Raftery 与 Hout (1993)」「Camerer 等 (2018)」；
    英文论文里是 (and / et al.)。括注式是「(Breen 2010)」「（Breen，2010）」。"""
    year = rec.year or "n.d."
    who = _cite_who(rec, en)
    if not who:
        return f"(Anon., {year})" if en else f"（佚名，{year}）"
    if parenthetical:
        return f"({who} {year})" if en else f"（{who}，{year}）"
    return f"{who} ({year})"


def cite_key(paper_id: str) -> str:
    """pandoc 的引用键：只留字母数字，前面加 ref_——DOI 里的 / 和 . 在键里会出事。"""
    return "ref_" + re.sub(r"[^A-Za-z0-9]+", "_", paper_id or "").strip("_")


#: 作者抽不出来时的占位。**原先写「Anonymous」/「佚名」——那不是占位，那是一个像样的作者名。**
#: 真跑时一条中文 PDF 记录的作者没抽出来，正文里印出来是「(Anonymous; Delhey 等, 2011)」，
#: 读者与作者都会以为那是一篇佚名著作。题名那一侧的约定是 `[UNVERIFIED]`——方括号加英文大写，
#: 中英文稿里都一眼看得出是占位而不是内容。作者这一侧照同一形状。
NO_AUTHOR = "[NO AUTHOR]"


def _split_name(name: str) -> dict[str, str]:
    """「Masayuki Kudamatsu」→ family/given。中文名整个当姓（CSL 会原样排）。

    **带逗号的按「姓, 名」读**（`Yin, Haijie`）。罗马化的中文名是姓在前，按西文顺序
    取最后一个词当姓会排成「Haijie, Yin」——姓名整个倒过来。逗号是这件事的通行记法，
    西文条目写成 `Kudamatsu, Masayuki` 也一样能读对（原先那样写会把 Masayuki 当姓）。
    """
    name = (name or "").strip()
    if not name:
        return {"family": NO_AUTHOR}
    if "," in name:
        fam, _, given = name.partition(",")
        return {"family": fam.strip(), "given": given.strip()} if given.strip() else {"family": fam.strip()}
    parts = name.split()
    if len(parts) == 1 or _CJK_CHARS.search(name):
        return {"family": name}
    return {"family": parts[-1], "given": " ".join(parts[:-1])}


def _en_title(r, *, nocase: bool = False) -> str:
    """英文稿里这条文献的题名：`罗马化 [英译]`。

    两样都有就照 Social Forces 范文的写法拼；只有一样就用那一样；都没有退回原题
    （随后由 `cjk_references` 报出来）。**原题始终留在 `r.title` 里**，好对着核。
    """
    rom = (getattr(r, "title_romanized", "") or "").strip()
    tr = (getattr(r, "title_en", "") or "").strip()
    # `nocase` 只在交给 citeproc 时加：Chicago 会把题名转成标题式大写，拼音被那样处理
    # 会变成「Xinli Yali Yu Shenghuo Manyidu」——`Yu`、`de` 这种虚词不该大写。
    # `<span class="nocase">` 是 CSL 的记法，**纯文本参考文献里加上去就是一串裸标签**。
    if rom and nocase:
        rom = f'<span class="nocase">{rom}</span>'
    if rom and tr:
        return f"{rom} [{tr}]"
    return rom or tr or (r.title or "")


def _en_authors(r) -> list[str]:
    return [a for a in (getattr(r, "authors_romanized", None) or []) if a.strip()] or (r.authors or [])


def csl_json(paper: str, corpus) -> list[dict]:
    """被引文献的 CSL-JSON 书目——交给 pandoc citeproc 按目标期刊的样式排。

    **只列被引用过的**，跟文本版的参考文献列表一个规矩。
    """
    if corpus is None:
        return []
    cited = _cited_ids(paper)
    out = []
    for r in corpus.records:
        if (r.paper_id or "").lower() not in cited and (r.doi or "").lower() not in cited:
            continue
        # 英文稿里非拉丁文字的条目换成罗马化 + 英译；中文稿原样排
        en = _is_english(paper)
        item: dict = {
            "id": cite_key(r.paper_id or r.doi),
            "type": "article-journal",
            "title": (_en_title(r, nocase=True) if en else (r.title or "")),
            "author": [_split_name(a) for a in (_en_authors(r) if en else (r.authors or []))]
                      or [{"family": NO_AUTHOR}],
        }
        if r.year:
            item["issued"] = {"date-parts": [[int(r.year)]]}
        if r.doi:
            item["DOI"] = r.doi
        venue = (getattr(r, "venue_en", "") or r.venue) if en else r.venue
        if venue:
            item["container-title"] = venue
        # 卷期页都没有的话，Chicago 样式会排成 "ahead of print"——验收跑的成品里 2018 年的文章就这样
        for src_attr, csl_key in (("volume", "volume"), ("issue", "issue"), ("pages", "page")):
            if getattr(r, src_attr, ""):
                item[csl_key] = getattr(r, src_attr)
        out.append(item)
    return out


def cjk_references(paper: str, corpus) -> list[str]:
    """英文稿的参考文献表里**排出来之后**仍带汉字的条目（返回题名片段）。

    查的是 `csl_json` 真正交给 citeproc 的那份，不是语料原始字段——
    填了罗马化和英译的就不该再报。英文期刊的参考文献表里出现汉字是格式硬伤：
    `styles/social-forces/exemplars/` 的 6 篇范文里一个汉字都没有。
    """
    if corpus is None or not _is_english(paper):
        return []
    out: list[str] = []
    for item in csl_json(paper, corpus):
        blob = " ".join([str(item.get("title", "")), str(item.get("container-title", "")),
                         *(f"{a.get('family', '')} {a.get('given', '')}"
                           for a in item.get("author", []))])
        if _CJK_CHARS.search(blob):
            out.append(str(item.get("title", ""))[:34])
    return out


def find_record(corpus, key: str):
    """按文献编号或 DOI 找。两个都在检索结果里现成有，**复制就行，不用打字**。

    **公开名**：`oss/api/lit.py::verify` 从模块外面调它。原先那段逻辑写在 CLI 里、
    伸手去拿 `manuscript._find_record`——搬进 api 时顺手把接缝摆正，
    不再让调用方去够一个下划线名字。`sentences_with` 同理。
    """
    if corpus is None:
        return None
    k = key.strip().lower()
    for r in corpus.records:
        if (r.paper_id or "").lower() == k:
            return r
        if (r.doi or "").lower() == k or (r.doi or "").lower().endswith("/" + k):
            return r
    return None

#: 裸小数。整数不在此列——年份、样本量、节号都是整数。
#:
#: 三处边界都踩过坑（注入测试量出来 12 个变体只拦下 6 个），改动前先读这段：
#: · 小数点前可以省 0——`p = .004`、`p < .001` 是期刊里极常见的写法，
#:   原先的 `\d+\.\d+` 要求小数点前至少有一位数字，整类漏过。
#: · 前置断言只排拉丁字母、数字与点：**汉字在 Python 里算 \w**，
#:   原先用 \w 会把「标准误0.123」豁免掉，而「标准误 0.123」（中间有空格）反倒拦得住。
#: · 后置同理，只排拉丁字母与数字，好让「0.123。」「0.123，」拦得住；
#:   多排一个斜杠是为了放过 DOI 的前缀（`10.1086/123456`）。
#: 版本号 `v1.2.3` 与文件名 `figure2.png` 靠两边断言里的字母挡住，占位符与代码块
#: 在 `_strip_code_and_quotes` 那一层就抹掉了——**判据只管这一关，不要在这里加第三层豁免**。
BARE_DECIMAL = re.compile(r"(?<![0-9A-Za-z.])(-?(?:\d+\.\d+|\.\d+))(?![0-9A-Za-z/])")

#: 正文里的整数，用来跟结果比对
BARE_INT = re.compile(r"(?<![\w.])(-?\d{2,})(?![\w.])")

#: 论文至少要有的节。少一节就不是一篇论文。每一项是同一节的中英名字——**命中任意一个就算有**。
#: 英文的「数据与方法」各刊写法不一（Data and Methods / Methods / Data），
#: 认 Data 或 Methods 任意其一。
REQUIRED_SECTIONS = (
    ("摘要", "Abstract"),
    ("引言", "Introduction"),
    ("数据与方法", "Data", "Methods"),
    ("结果", "Results"),
    ("讨论", "Discussion"),
)

#: 每个估计可以被引用的字段
ESTIMATE_FIELDS = ("coef", "se", "p", "term", "mde")


class ManuscriptRejected(RuntimeError):
    """稿件过不了校验。**这是拒绝渲染，不是警告。**"""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("稿件过不了校验：\n  · " + "\n  · ".join(problems))
        self.problems = problems


def _strip_code_and_quotes(text: str) -> str:
    """校验时忽略代码块、行内代码，以及**占位符内部的文字**。

    正文里贴一段代码示例、或者写 `p < 0.05` 这种阈值约定，
    都不该被当成编造的结果。

    **占位符内部同理**：`{{bin:female=0.0/last.slope}}` 里的 `0.0` 是组名的一部分，
    不是手打的统计结果。这一条直到分箱占位符出现才暴露——
    在那之前占位符路径里从没出现过小数。

    占位符用等长空格替换而不是删掉，**否则报出来的行号会错位**。

    注意跟 `_strip_code` 的分工：主张核验需要占位符**留在原地**
    （它要定位每个占位符周围说了什么），所以那边用 `_strip_code`。
    """
    return PLACEHOLDER.sub(lambda m: " " * len(m.group(0)), _strip_code(text))


#: 公开名。判分器（`evals/benchmark/check_exam.py`）要跟校验用同一把剪刀去掉代码和占位符，
#: 否则占位符里的路径会被当成正文查。**跨模块用别人的下划线名迟早断**，所以这里给个正式名字。
strip_code_and_quotes = _strip_code_and_quotes


def _strip_code(text: str) -> str:
    """只去代码，**保留占位符**。主张核验要靠占位符定位上下文。"""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


# ------------------------------------------------------------------ 校验


#: 诊断文本里**结构固定**的几种中文写法，英文稿渲染时翻过来。
#: 只翻结构固定的；自由文本（共线诊断的长句、功效敏感性那一段）翻不了，
#: 那种在英文稿里直接拒绝，让作者用英文散文转述。
_EN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(\d[\d,]*) 行（([\d.]+%)）"), r"\1 rows (\2)"),
    (re.compile(r"聚类到 (\S+?)（(\d+) 个簇）"), r"clustered by \1 (\2 clusters)"),
    (re.compile(r"HC1 异方差稳健（未聚类）"), "HC1 heteroskedasticity-robust (unclustered)"),
    (re.compile(r"最大 VIF ([\d.]+)（([^）]+)），无共线问题"),
     r"max VIF \1 (\2); no collinearity concern"),
    (re.compile(r"OLS \+ 固定效应（去均值吸收）"), "OLS with fixed effects (absorbed by demeaning)"),
    (re.compile(r"逐年展开（相对处理时点，第 −1 期为参照）"),
     "event study (relative to treatment year; period −1 as reference)"),
    (re.compile(r"双重差分（单位与时间双向固定效应）"),
     "difference-in-differences (two-way unit and time fixed effects)"),
    (re.compile(r"^无$"), "none"),
    (re.compile(r"^不可算$"), "not computable"),
    (re.compile(r"\*\*流失明显\*\*——"), "**substantial attrition** — "),
    (re.compile(r"流失偏多——"), "elevated attrition — "),
    (re.compile(r"(\d+) 个「(\S+?)」单位进入回归，其中 (\d+) 个（(\d+%)）在全部 (\d+) 个「(\S+?)」期都有观测"),
     r"\1 \2 units enter the regression, of which \3 (\4) are observed in all \5 \6 periods"),
    (re.compile(r"。\*\*流失往往不是随机的\*\*（状况变差的单位更容易退出），若与研究对象相关会造成选择性偏误"),
     ". **Attrition is rarely random** (units whose condition worsens are more likely to drop out); "
     "if it correlates with the object of study it induces selection bias"),
    (re.compile(r"80% 功效、5% 双侧下（≈2\.8×标准误）："), "at 80% power and two-sided 5% (≈2.8 × SE): "),
    (re.compile(r"80% 功效、5% 双侧下（按 t 分布、自由度 (\d+)，≈([\d.]+)×标准误）："),
     r"at 80% power and two-sided 5% (t distribution, \1 df; ≈\2 × SE): "),
    (re.compile(r"。\*\*不显著的系数只有跟它比过才知道是「没有效应」还是「测不出来」\*\*"),
     ". **A null coefficient is only interpretable against this number: "
     "it separates \"no effect\" from \"could not have been detected\"**"),
    (re.compile(r"、"), ", "),
]


def _englishize(val: str) -> str:
    """把结构固定的中文诊断片段翻成英文。翻不了的原样返回，由调用方拒绝。"""
    for pat, rep in _EN_PATTERNS:
        val = pat.sub(rep, val)
    return val


def _resolve_one(source: str, path: str, results, power, corpus=None,
                 figures: set[str] | None = None, en: bool = False,
                 labels: dict[str, str] | None = None) -> tuple[str | None, str]:
    """解析一个占位符。返回 `(值, 出错原因)`，值为 None 表示解析不了。

    英文稿里，`diag` / `power` 这类会渲染出**中文诊断文本**的占位符，
    结构固定的翻成英文；翻不了的判「解析不了」——
    **验收跑的成稿里就漏进了四处中文**（「513 行（9.0%）」「聚类到 country（105 个簇）」
    和整段功效敏感性），一个英文期刊的编辑翻开第一页就会看见。
    """
    val, why = _resolve_raw(source, path, results, power, corpus, figures, en, labels)
    if val is None or not en or source in ("fig", "cite", "citep", "table"):
        return val, why
    val2 = _englishize(val)
    if _CJK_CHARS.search(val2):
        return None, (
            f"在英文稿里会渲染出中文诊断文本：「{val[:60]}…」。"
            f"**用英文散文转述它**（数值可以照抄，它们来自结果文件），不要直接嵌这个占位符"
        )
    return val2, why


def _resolve_raw(source: str, path: str, results, power, corpus=None,
                 figures: set[str] | None = None, en: bool = False,
                 labels: dict[str, str] | None = None) -> tuple[str | None, str]:
    if source == "table":
        return table_markdown(path.strip(), results, en=en, labels=labels)
    if source == "fig":
        name = path.strip()
        if figures is None:
            return None, (
                f"{{{{fig:{name}}}}} 核验不了——调用方没有提供运行目录里的图片清单"
            )
        if name not in figures:
            avail = "、".join(sorted(figures)) or "（一张都没有）"
            return None, (
                f"图「{name}」不存在。可用的：{avail}。"
                f"**引一张不存在的图跟引一个不存在的估计一样**——"
                f"先用 oss figure {name} 把它画出来"
            )
        # 英文稿里的图必须是英文版。验证跑的英文 Word 里嵌的四张图标题、坐标轴全是中文，
        # 没人拦——图的语言记在旁边的 fig_<name>.json 里（oss figure 写的）。
        # 传进来的是 set（老调用方、测试）就没有语言信息，不拦。
        if en and isinstance(figures, dict) and figures.get(name) != "en":
            tag = figures.get(name) or "无"
            return None, (
                f"图「{name}」不是英文版（语言标记：{tag}）——英文稿里的图必须英文。"
                f"重画：oss figure {name} --lang en"
            )
        # 对称的另一半：中文稿里嵌英文版的图也拦。语言标记为空（老图）不拦——
        # 拦「明确是另一种语言」，不拦「不知道」。
        if not en and isinstance(figures, dict) and figures.get(name) == "en":
            return None, (
                f"图「{name}」是英文版——中文稿里的图必须中文。"
                f"重画：oss figure {name} --lang zh"
            )
        return f"![{name}](fig_{name}.png)", ""

    if source in ("cite", "citep"):
        if corpus is None:
            return None, "没有语料（literature.json），引用解析不了"
        # 括注式可以一次引几篇：{{citep:a;b}} → (A 2019; B 2012)。
        # 验收跑里执行者写了 {{citep:a,b}} 解析不了，只好连写两个括注「(A 2019) (B 2012)」
        ids = _split_ids(path) if source == "citep" else [path]
        recs = []
        for one in ids:
            rec = find_record(corpus, one)
            if rec is None:
                return None, (
                    f"语料里没有「{one}」这篇文献。"
                    f"**引一篇不存在的文献跟手打一个数字一样**——"
                    f"要么用 `oss lit add <DOI>` 把它抓进语料，要么改写这句话"
                )
            recs.append(rec)
        if source == "cite":
            return _cite_label(recs[0], en), ""
        inner = "; ".join(_cite_label(r, en, parenthetical=True).strip("()（）") for r in recs)
        return (f"({inner})" if en else f"（{inner}）"), ""

    if source == "result":
        if "." not in path:
            return None, f"{{{{result:{path}}}}} 少了字段名，形如 result:main.coef"
        key, field = path.rsplit(".", 1)
        if key == "main" and field == "n":
            return str(results.n), ""
        est = results.estimates.get(key)
        if est is None:
            avail = "、".join(results.estimates) or "（一个都没有）"
            return None, f"估计「{key}」不存在。可用的：{avail}"
        if field not in ESTIMATE_FIELDS:
            return None, f"字段「{field}」不存在。可用的：{'、'.join(ESTIMATE_FIELDS)}、n"
        if field in ("p", "mde") and key == "main" and _mean_only(results):
            return None, _mean_only_why(field, "主结果", "{{result:main.coef}}",
                                        "全样本的 95% 区间在 {{table:main}}，各组的在 {{table:groups}}")
        val = getattr(est, field)
        if field == "p" and isinstance(val, float) and val < 0.001:
            return "< 0.001", ""
        return (f"{val:.3f}" if isinstance(val, float) else str(val)), ""

    if source == "power":
        if power is None:
            return None, "没有 power.json，引用不了功效相关的数字"
        if not hasattr(power, path):
            fields = "、".join(f for f in power.model_dump() if not f.startswith("_"))
            return None, f"功效报告里没有「{path}」。可用的：{fields}"
        val = getattr(power, path)
        return (f"{val:.3f}" if isinstance(val, float) else str(val)), ""

    if source == "robust":
        if "/" not in path:
            return None, (
                f"{{{{robust:{path}}}}} 少了分隔符，形如 robust:<label>/main.coef"
            )
        label, rest = path.split("/", 1)
        run = results.robustness.get(label)
        if run is None:
            avail = "、".join(results.robustness) or "（一项都没有）"
            return None, f"稳健性「{label}」不存在。可用的：{avail}"
        if run.error:
            return None, (
                f"稳健性「{label}」**没有结果**（{run.error}）——引用它就是编数字"
            )
        # **稳健性项的诊断原先引不出来。**AR 区间、有效 F、2SLS/OLS 这些逐项都算了、
        # 也存进了 `RobustnessRun.diagnostics`，但占位符只认 estimates 里的几个字段，
        # 于是正文只能写「近四倍」这种约数，而精确值就在结果文件里——
        # **能算出精确数却在正文写约数，是这个项目最不该有的形状。**
        # 前缀写成 `diag.`，不走下面的 rsplit：诊断的键带空格（「有效 F」）也带斜杠（「2SLS/OLS」）。
        if rest.startswith("diag."):
            dkey = rest[len("diag."):]
            if dkey not in run.diagnostics:
                avail = "、".join(run.diagnostics) or "（空）"
                return None, f"稳健性「{label}」的诊断里没有「{dkey}」。可用的：{avail}"
            return run.diagnostics[dkey], ""
        if "." not in rest:
            return None, (f"{{{{robust:{path}}}}} 少了字段名，形如 robust:{label}/main.coef，"
                          f"或 robust:{label}/diag.<诊断名> 引这一项的诊断")
        key, field = rest.rsplit(".", 1)
        if key == "main" and field == "n":
            return str(run.n), ""
        est = run.estimates.get(key)
        if est is None:
            avail = "、".join(run.estimates) or "（一个都没有）"
            return None, f"稳健性「{label}」里没有估计「{key}」。可用的：{avail}"
        if field not in ESTIMATE_FIELDS:
            return None, f"字段「{field}」不存在。可用的：{'、'.join(ESTIMATE_FIELDS)}、n"
        if field in ("p", "mde") and key == "main" and _mean_only(results):
            return None, _mean_only_why(field, f"稳健性「{label}」也", "{{robust:" + label + "/main.coef}}",
                                        "各项的 95% 区间在 {{table:robustness}}")
        val = getattr(est, field)
        if field == "p" and isinstance(val, float) and val < 0.001:
            return "< 0.001", ""
        return (f"{val:.3f}" if isinstance(val, float) else str(val)), ""

    if source == "bin":
        if "/" not in path:
            return None, f"{{{{bin:{path}}}}} 少了分隔符，形如 bin:<组名>/last.slope"
        group, rest = path.split("/", 1)
        rows = results.binned.get(group)
        if not rows:
            avail = "、".join(results.binned) or "（一组都没有）"
            return None, f"分箱里没有「{group}」。可用的：{avail}"
        if "." not in rest:
            return None, f"{{{{bin:{path}}}}} 少了字段名，形如 bin:{group}/last.slope"
        which, field = rest.rsplit(".", 1)
        if which == "first":
            row = rows[0]
        elif which == "last":
            row = rows[-1]
        else:
            try:
                row = next(r for r in rows if r.bin_index == int(which))
            except (ValueError, StopIteration):
                idx = "、".join(str(r.bin_index) for r in rows)
                return None, f"分箱「{group}」里没有第 {which} 箱。可用：first、last、{idx}"
        if not hasattr(row, field):
            fields = "、".join(f for f in row.model_dump())
            return None, f"分箱没有字段「{field}」。可用的：{fields}"
        val = getattr(row, field)
        return (f"{val:.3f}" if isinstance(val, float) else str(val)), ""

    if source == "diag":
        if path not in results.diagnostics:
            avail = "、".join(results.diagnostics) or "（空）"
            return None, f"诊断里没有「{path}」。可用的：{avail}"
        return results.diagnostics[path], ""

    return None, f"未知的命名空间「{source}」"


# ══════════════════════════════════════════════ 主张与结果一致吗

#: 分句边界。**上下文要切到分句，不能停在整句。**
#:
#: 一句话里常常有两个结果：「主效应为 X（p 0.001），对照项不显著（p 0.42）」——
#: 按整句取上下文的话，`X` 会被拿去跟「不显著」比对，报一个假警报。
#: 而假警报的代价很高：**警报天天响就等于没有警报**。
#:
#: 代价是切窄了会漏掉跨分句的错（「该效应在各规格中均下降，主系数为 +0.2」）。
#: 两害相权取精确——漏掉的那类还有跨模型审稿兜底，而假警报会让人直接忽略这个检查。
#: 英文句号不能直接当边界字符——`et al. (2015)`、`Fig. 3`、`A. Smith` 里全是句点。
#: 判据：句点/问叹号 + 空白 + 大写字母，且句点前不是单个大写字母（人名缩写）。
#: `vs. Smith` 这类仍会误切，代价可接受：误切只是把上下文切窄了一点，
#: 而漏切（当没有边界）只是把上下文放宽——两个方向都不会让检查失明。
_EN_SENT = r"(?<![A-Z])(?<=[.!?])[ \t]+(?=[A-Z])"

#: 分句边界。**上下文要切到分句，不能停在整句。**
#:
#: 一句话里常常有两个结果：「主效应为 X（p 0.001），对照项不显著（p 0.42）」——
#: 按整句取上下文的话，`X` 会被拿去跟「不显著」比对，报一个假警报。
#: 而假警报的代价很高：**警报天天响就等于没有警报**。
#:
#: 代价是切窄了会漏掉跨分句的错（「该效应在各规格中均下降，主系数为 +0.2」）。
#: 两害相权取精确——漏掉的那类还有跨模型审稿兜底，而假警报会让人直接忽略这个检查。
_CLAUSE_BOUND = re.compile(r"[。！？；，、;:,]|" + _EN_SENT)

#: 方向词。判「句子说的方向」跟「系数的符号」对不对得上。
#: 中文按子串匹配；英文必须带词边界——`comprise` 里就藏着一个 `rise`。
_UP = ("上升", "增加", "提高", "增强", "扩大", "加剧", "更高", "更强", "正向", "促进")
_DOWN = ("下降", "减少", "降低", "削弱", "减弱", "缩小", "缓解", "更低", "更弱", "负向", "抑制")
_UP_EN = re.compile(
    r"\b(increas\w*|ris(e|es|ing)|rose|higher|greater|larger|strengthen\w*"
    r"|widen\w*|amplif\w*|positive\w*|improv\w*|promot\w*|boost\w*)\b", re.I)
_DOWN_EN = re.compile(
    r"\b(decreas\w*|declin\w*|reduc\w*|lower\w*|weaken\w*|narrow\w*|dampen\w*"
    r"|negative\w*|attenuat\w*|shrink\w*|smaller|fell|fall(s|ing)?|erod\w*)\b", re.I)

#: 显著性判据。**这一条没有歧义，必须硬拦。**
#: 英文的否定式必须先查——`insignificant` 里包含着 `significant`。
_SIG = ("显著",)
_NOT_SIG = ("不显著", "不显着", "未达显著", "未显著")
#: 否定词与「显著」隔着别的字的情形：「未达**常规**显著水平」「不具有统计显著性」。
#: 真跑时这句被当成「说显著」硬拦，模型只好改写句子绕开「显著」两个字——
#: **工具的盲区在扭曲论文的语言，比漏报更糟。**
#: 间隔里排除「仅/但」：「不仅显著」「不但显著」是在**强调**显著，不是否定。
#: 「不再显著」「不复显著」：否定词与「显著」隔着一个字，两条分支都够不着——
#: 第一支的词表里没有光杆的「不」，第二支要求紧贴。真跑时这句被判成「在说显著」。
_NOT_SIG_NEAR = re.compile(r"(未达|未能|没有|并不|不具|不再|不复|无)[^。；，！？仅但]{0,8}显著|(未|不|非)显著")
#: 不含「显著」二字的等价说法。**真实论文讲 p 值的句子多半是这么写的**——
#: 真实论文底板上讲 p 值的 8 句里，一句带「显著」的都没有。
#: 这三类是评测 injection-v2 逐条核过的写法，不是猜的。
#: **三条各自带着否定的挡板**：否则「不能拒绝原假设」「与零区分不开」这些
#: 说的是不显著，却会被读成在说显著（良性改动里就有一句「不显著」改写成
#: 「在 5% 水平上不能拒绝为零的原假设」，认错了就是误报）。
#: · 「拒绝」这一路用定长后顾一个个挡掉否定词——Python 的后顾断言不许变长，
#:   所以只能罗列；挡板必须紧贴「拒绝」，放在可选的「可以 / 能够」之前挡不住。
#: · 「区分」这一路把否定字从中间那两段里排除，且后面紧跟「不」「无」的不算。
#: 「在 5% 水平上」这类前缀**故意不写进正则**：它是可选的，写了也不改变匹配与否。
_SIG_ALT = re.compile(
    r"(?<!不能)(?<!不可)(?<!不足以)(?<!无法)(?<!未能)(?<!没能)(?<!没有)(?<!难以)(?<!不)(?<!未)"
    r"拒绝[^。；]{0,12}(?:原假设|零假设|系数为零)"
    r"|统计上[^。；不未无难非]{0,8}(?:与零|与\s*0)[^。；不未无]{0,4}区分(?![不无])"
    r"|(?:明确|确实)[^。；]{0,4}不为\s*(?:零|0)")
_SIG_EN = re.compile(r"\bsignificant(ly)?\b", re.I)
_NOT_SIG_EN = re.compile(
    r"\b(not\s+(statistically\s+)?significant|in\s?significant"
    r"|non-?significant|no\s+significant|fail(s|ed)?\s+to\s+reach\s+significance)\b", re.I)

#: 「证明」类措辞。统计显著不等于证明。
#: 英文只拦 prove 一族——demonstrate/show 是常规学术用语，拦了就是天天响的警报。
_PROOF = ("证明", "证实", "确证", "表明了因果", "说明了因果")
_PROOF_EN = re.compile(r"\b(prove[sdn]?|proving|definitive\s+evidence)\b", re.I)


#: 方向词**自己**被否定的情形。跟 `_NEG_ZH`/`_NEG_EN` 分开写：那两条挡的是
#: 「不能排除」这类情态否定，判据是「排除」前面有没有情态词；这里判的是
#: 「没有出现同向的正向关联」「no positive association is detected」——
#: 方向词还是那个词，句子说的却是它的反面。两者的窗口与词表都不同，合成一条会互相误伤。
#: 窗口靠标点兜住（中文的逗号顿号、英文的句号分号都不许跨），只看紧挨着的那一小段。
#: 「不仅 / 不但 / 不只 / 不光」是**强调**，不是否定，排掉。
_DIR_NEG_ZH = re.compile(r"(?:不|未|没|无|非)(?!仅|但|只|光)[^。；，、！？]{0,6}$")
_DIR_NEG_EN = re.compile(r"\b(?:no|not|never|nor|neither|without)\b[\w\s'-]{0,24}$", re.I)


def _dir_negated(sent: str, start: int) -> bool:
    """方向词出现在 `start`，紧挨它前面是不是有否定。"""
    head = sent[:start]
    return bool(_DIR_NEG_ZH.search(head) or _DIR_NEG_EN.search(head))


def _has_direction(sent: str, words: tuple[str, ...], pat) -> bool:
    """句子里有没有**没被否定的**方向词。"""
    for w in words:
        i = sent.find(w)
        while i >= 0:
            if not _dir_negated(sent, i):
                return True
            i = sent.find(w, i + 1)
    return any(not _dir_negated(sent, m.start()) for m in pat.finditer(sent))


def _says_up(sent: str) -> bool:
    return _has_direction(sent, _UP, _UP_EN)


def _says_down(sent: str) -> bool:
    return _has_direction(sent, _DOWN, _DOWN_EN)


def _says_not_sig(sent: str) -> bool:
    return (any(w in sent for w in _NOT_SIG) or bool(_NOT_SIG_NEAR.search(sent))
            or bool(_NOT_SIG_EN.search(sent)))


def _says_sig(sent: str) -> bool:
    return (any(w in sent for w in _SIG) or bool(_SIG_EN.search(sent))
            or bool(_SIG_ALT.search(sent)))


def _says_proof(sent: str) -> bool:
    return any(w in sent for w in _PROOF) or bool(_PROOF_EN.search(sent))


#: 句子边界。**单个换行不算**——正文是手工折行的，一句话经常跨两三行。
#: 把 `\n` 当句号会把上下文截断在半句上：
#: 「{{cite:X}} 对三国的比较发现，\n教育扩张本身是流动机会改善的一个机制。」
#: 截到换行处就只剩「对三国的比较发现」，真正的内容全在下一行——
#: **所有跨行的检查都会因此失效**。段落之间的空行才是真边界。
_SENT_BOUND = re.compile(r"[。！？]|" + _EN_SENT)
_PARA = re.compile(r"\n[ \t]*\n")


def _span(paper: str, at: int, end: int, bound: re.Pattern) -> tuple[int, int]:
    lo = 0
    for m in bound.finditer(paper, 0, at):
        lo = m.end()
    lo = max(lo, max((m.end() for m in _PARA.finditer(paper, 0, at)), default=0))
    hi = len(paper)
    if (m := bound.search(paper, end)) is not None:
        # 中文标点：切在标点前（跟原行为一致）。英文边界匹配的是句号后的空白，
        # m.start() 已经在句号之后——两种情形同一个式子。
        hi = m.start()
    if (m := _PARA.search(paper, end)) is not None:
        hi = min(hi, m.start())
    return lo, hi


def sentences_with(paper: str) -> list[tuple[str, re.Match]]:
    """把每个占位符连同它的上下文取出来。

    **上下文该多宽，取决于这句话里有没有别的同类结果。**

    - 一句话里只有这一个结果 → 用**整句**。
      「两者均不显著（p {{...}}），这证明了性别没有差异」——
      切到分句就看不见「证明」，而那正是要抓的东西。
    - 一句话里有多个同类结果 → 用**分句**。
      「主效应为 X（p 0.001），对照项不显著（p 0.42）」——
      用整句的话 X 会被拿去跟「不显著」比对，报一个假警报。

    判「同类」看的是字段名（`.p` 跟 `.p`、`.coef` 跟 `.coef`），
    因为歧义只在同一种量之间产生。
    """
    out = []
    for m in PLACEHOLDER.finditer(paper):
        field = m.group(2).rsplit(".", 1)[-1]
        s_lo, s_hi = _span(paper, m.start(), m.end(), _SENT_BOUND)
        siblings = [
            x for x in PLACEHOLDER.finditer(paper[s_lo:s_hi])
            if x.group(2).rsplit(".", 1)[-1] == field
        ]
        if len(siblings) > 1:
            lo, hi = _span(paper, m.start(), m.end(), _CLAUSE_BOUND)
        else:
            lo, hi = s_lo, s_hi
        out.append((paper[lo:hi], m))
    return out


def _section_body(paper: str, keyword: str) -> str:
    """取某一节的正文。用来核「该写的写了没有」。"""
    m = re.search(rf"^#{{1,4}}\s*.*{re.escape(keyword)}.*$", paper, re.M | re.I)
    if not m:
        return ""
    nxt = re.search(r"^#{1,4}\s", paper[m.end():], re.M)
    return paper[m.end(): m.end() + (nxt.start() if nxt else len(paper))]


#: 转述核对判不了的两种情形。**「核不了」和「存疑」必须分开**——
#: 混在一起会让这个指标自己变成噪声，跟「判不了 ≠ 跑题」是同一个道理。
UNCHECKABLE = "核不了"


def _claim_fragments(sentence: str) -> set[str]:
    """把一句中文转述映射成可以去英文摘要里找的词根。

    **这是转述核对最大的障碍。**论文是中文、语料是英文摘要，
    纯词面比对必然全零——「Breen 发现教育扩张促进了社会流动」里没有一个英文词。

    复用绑定阶段那张社科构念中英对照表，并为转述核对补了论证性词汇
    （「不平等」「扩张」「持续不变」这类，绑变量时用不到，转述核对必须有）。

    句子里本来就有的英文词也直接算——引英文文献时常常会带上原词。
    """
    from oss.core.binding import lexicon_fragments

    out = lexicon_fragments(sentence)
    out |= {
        w.lower() for w in re.findall(r"[A-Za-z]{4,}", sentence)
        if w.lower() not in {"cite", "result", "power", "diag", "robust", "references"}
    }
    return out


def citation_support(sentence: str, record) -> tuple[str, str]:
    """这句转述，被它引的那篇文献支持吗？

    **这一层很弱，而且必须承认它弱。**它只能抓明显的张冠李戴，
    抓不到细微的歪曲——后者要交给跨模型判。

    三种结果：

    ==========  ================================================
    支持         转述里的关键概念能在标题+摘要里找到
    存疑         有可映射的概念，但**一个都没命中**
    核不了       没有摘要，或转述里没有任何可映射的概念
    ==========  ================================================

    **「核不了」不能报成「存疑」。**语料里只有约一半的文献带摘要，
    把「没摘要」算成「可疑」会立刻变成满屏假警报。
    """
    hay = f"{record.title or ''} {getattr(record, 'abstract', '') or ''}".lower()
    if not (getattr(record, "abstract", "") or ""):
        return UNCHECKABLE, "这篇文献没有摘要，只有标题——文本太少，机器没有判据"

    frags = _claim_fragments(sentence)
    if not frags:
        return UNCHECKABLE, "这句转述里没有词典覆盖得到的概念，机器没有判据"

    hits = sorted({f for f in frags if f in hay})
    if hits:
        return "支持", f"转述里的概念在摘要里找到了：{'、'.join(hits[:5])}"
    return "存疑", (
        f"转述里的概念（{'、'.join(sorted(frags)[:6])}）在这篇的标题与摘要里**一个都没命中**——"
        f"可能是张冠李戴，也可能只是说法不同。**机器判不了这一层，请人核或交跨模型判**"
    )


def _corpus_surnames(corpus) -> dict[str, list[tuple[str, object]]]:
    """语料里的姓 → [(全名, 文献), ...]，按引用数降序。

    **返回全部同姓的人，不只返回一个。**姓能匹配，人不一定是同一个——
    实测踩到过：论文里的「Duncan」是 Otis Dudley Duncan（1967 年那本经典），
    而语料里的 Duncan 是 Greg J. Duncan。工具当时提示「请写成 W2097355889」，
    **照着改就会引错文献——这是工具制造出来的引用错误。**

    所以工具只负责指出「这里有个裸引用」，**不替人认定是哪一篇**。

    **两个字母的姓也进索引**（Xu、Li、He、Wu——中文作者的拉丁姓大量是两个字母，这是一个大类）。
    进不了索引，后面再怎么查都不可能命中。代价是这一类极容易撞上缩写与专名，
    所以两头各加一道：这里按 `_NOT_CITATIONS` 挡掉 `US`、`AI` 那种，
    匹配那边（`_surname_hit`）给它更严的边界。
    """
    out: dict[str, list[tuple[str, object]]] = {}
    for rec in sorted(corpus.records if corpus else [], key=lambda r: -(r.citation_count or 0)):
        for a in rec.authors or []:
            sn = _surname(a)
            if len(sn) < 2 or not sn.isalpha():
                continue
            if len(sn) == 2 and sn in _NOT_CITATIONS:
                continue
            full = " ".join((a or "").split())
            bucket = out.setdefault(sn, [])
            if not any(f == full for f, _ in bucket):
                bucket.append((full, rec))
    return out


def _split_ids(path: str) -> list[str]:
    """`a;b;c` → 三个 id。只认分号——DOI 里有逗号和斜杠，分号没有。"""
    return [p.strip() for p in path.split(";") if p.strip()]


#: 公开名。`oss/api/lit.py` 的转述核对要用同一条分号规则拆 `{{citep:a;b}}`，
#: 理由跟 `strip_code_and_quotes` 一样——跨模块用别人的下划线名迟早断。
split_ids = _split_ids


def _cited_ids(paper: str) -> set[str]:
    out: set[str] = set()
    for m in PLACEHOLDER.finditer(paper):
        if m.group(1) in ("cite", "citep"):
            out |= {p.lower() for p in _split_ids(m.group(2))}
    return out


def _surname_hit(sn: str, body: str, zh_body: str) -> bool:
    """这个姓在正文里被当成一个词写着。

    **两个字母的姓另走两道更严的关**，三个字母以上的一律沿用原来的判据
    （汉字紧贴着也算命中：中文里「Breen发现」是常见写法）：

    ① 边界卡死：前后必须是行首行尾、空白或句读（`\\w` 连汉字也算词内，所以「见附录Sg表」不算命中）。
       真实底板上抽到过 `Sg`、`Dj` 这类作者信息里的碎片，而两个字母又极易撞上缩写与专名。
    ② **只在非英文的行里查**（`zh_body`）。理由跟 `_bare_citation_warnings` 记着的那条一样：
       英文里句首全是大写词，两个字母的大写词不携带任何信号。
       真实稿子上量到过——`runs/r20260825_qog_internet/paper.md` 是英文稿，语料里有位 Lian An，
       正文的「An I-squared of…」「An instrumental-variables design…」两处句首就被当成裸引用报了。
       代价是英文稿里两个字母的姓查不着，记在这里：要在英文里做同一件事得上真的命名实体识别。
    """
    if len(sn) == 2:
        return re.search(rf"(?<!\w){re.escape(sn)}(?!\w)", zh_body) is not None
    return re.search(rf"(?<![0-9A-Za-z]){re.escape(sn)}(?![0-9A-Za-z])", body) is not None


def _no_author_warnings(paper: str, corpus) -> list[Finding]:
    """引了作者抽不出来的记录。**在导出之前说，不要等人打开 Word 才看见。**

    真跑时一条中文 PDF 的作者没抽出来，成稿正文里印的是「([NO AUTHOR]; Delhey 等, 2011)」。
    占位本身是对的（不能编一个作者），但它出现在**正文的引用里**，而这件事在
    `paper.md` 上完全看不出来——占位符 `{{citep:...}}` 是好的，语料记录里才缺。
    所以在这一层点名，让人在导出前补上或改引别的文献。
    """
    if corpus is None:
        return []
    cited = _cited_ids(paper)
    bad = [r for r in corpus.records
           if ((r.paper_id or "").lower() in cited or (r.doi or "").lower() in cited)
           and not (r.authors or [])]
    if not bad:
        return []
    listed = "；".join(f"{r.paper_id}（{(r.title or '')[:34]}）" for r in bad[:4])
    return [Finding(
        f"正文引了 {len(bad)} 条**没有作者**的记录：{listed}。"
        f"导出时它们的作者会印成 `{NO_AUTHOR}`——占位是对的（编一个作者更糟），"
        f"但正文的引用里出现占位就不能投。"
        f"补作者（`oss lit add <DOI>` 重抓，或直接改语料记录），或改引别的文献",
        "cite.no_author")]


def _unverified_title_warnings(paper: str, corpus) -> list[Finding]:
    """引了题名还带 `[UNVERIFIED]` 的记录。跟 `_no_author_warnings` 同一个理由：在导出之前说。

    题名是从 PDF 版面猜的，猜完带着 `[UNVERIFIED]` 进语料（见 `oss/api/lit.py`），这一步是对的。
    但三篇 demo 照样带着它导出了：参考文献里印着 `[UNVERIFIED]`，没有刊名、年份和页码，
    原先导出前一句都不说。
    """
    if corpus is None:
        return []
    cited = _cited_ids(paper)
    bad = [r for r in corpus.records
           if ((r.paper_id or "").lower() in cited or (r.doi or "").lower() in cited)
           and (r.title or "").lstrip().startswith("[UNVERIFIED]")]
    if not bad:
        return []
    listed = "；".join(f"{r.paper_id}（{(r.title or '')[:34]}）" for r in bad[:4])
    return [Finding(
        f"正文引了 {len(bad)} 条**题名没核实**的记录：{listed}。"
        f"导出时参考文献里会印出 `[UNVERIFIED]`，刊名、年份和页码多半也是空的。"
        f"去期刊或知网的页面核对，补全题名、作者、刊名、年份、卷期页（直接改语料记录），或改引别的文献",
        "cite.unverified_title")]


def _bare_citation_problems(body: str, paper: str, corpus) -> list[str]:
    """**语料里有这个人，却用裸名字引** → 拒绝。

    理由跟手打数字完全一样：**手打的引用看起来跟真的一模一样**。
    而且这一类是可以精确判定的——那个姓就在语料里，编号也现成。

    **去掉变音记号才对得上的降一档，只给提示**（`cite.bare_surname_folded`）：
    语料写 `Götmark`、正文写 `Gotmark`，两边都是字母，卡住的不是索引那一关，是字面比对这一行。
    折叠能把它们接上，但折叠也会把**不同的姓**并到一起——同形不同人的风险跟着上升，
    硬拦就会制造工具自己造出来的引用错误（`_corpus_surnames` 里 Duncan 那次教训）。
    """
    out: list[str] = []
    known = _corpus_surnames(corpus)
    if not known:
        return out
    cited = _cited_ids(paper)
    zh_body = "\n".join(ln for ln in body.split("\n") if not _is_english(ln))   # 见 `_surname_hit` 的 ②
    plain, folded = (body, zh_body), (_fold(body), _fold(zh_body))
    for sn, people in known.items():
        shown, soft = sn, False
        if not _surname_hit(sn, *plain):
            shown = _fold(sn)
            if not _surname_hit(shown, *folded):
                continue
            soft = True             # 折叠之后才对上：降一档
        if any((r.paper_id or "").lower() in cited or (r.doi or "").lower() in cited
               for _, r in people):
            continue
        if soft:
            listed = "；".join(f"**{full}**（{rec.paper_id}：{(rec.title or '')[:30]}）"
                              for full, rec in people[:4])
            out.append(
                Finding(f"正文里的「{shown}」去掉变音记号才跟语料里的姓「{sn}」对得上：{listed}。"
                f"**同形不见得是同一个人**，所以这里只提示不拦——"
                f"确认是同一位就写成对应的 {{{{cite:...}}}}，不是就照原样保留", "cite.bare_surname_folded")
            )
            continue
        if len(people) == 1:
            full, rec = people[0]
            out.append(
                Finding(f"正文里有裸引用「{sn}」，语料里有 **{full}** 的文献"
                f"（{rec.paper_id}：{(rec.title or '')[:40]}）。"
                f"**手打的引用看起来跟真的一模一样**——"
                f"确认是同一个人的话写成 {{{{cite:{rec.paper_id}}}}}；"
                f"**如果不是同一个人，用 `oss lit add <DOI>` 把正确的那篇抓进来**", "cite.bare_surname")
            )
        else:
            listed = "；".join(
                f"**{full}**（{rec.paper_id}：{(rec.title or '')[:30]}）"
                for full, rec in people[:4]
            )
            out.append(
                Finding(f"正文里有裸引用「{sn}」，而语料里**同姓的有多位**：{listed}。"
                f"**工具不替你认定是哪一位**——自己核准之后写成对应的 {{{{cite:...}}}}", "cite.bare_surname")
            )
    return out


def _bare_citation_warnings(body: str, corpus) -> list[str]:
    """**语料里没有这个人** → 警告，这处引用无据。

    实测中 `Blau` 就是这样：论文写了「Blau 与 Duncan 建立的地位获得模型」，
    而这次检索一篇 Blau 都没召回。它大概率是对的（社会学常识），
    但**论文里没有任何东西能证明它**。

    只在**带归属动词**的分句里判，否则「本文的 SEI 指数」会被当成引用一个叫
    SEI 的人。**误报会让人直接忽略这个检查。**

    **英文分句整个不查**（不是整篇——中文稿里那段英文摘要会犯同一个错）。
    这条判据的全部依据是「中文句子里冒出一个大写拉丁词，多半是个姓」——
    英文里每句话开头都是大写词，依据就没了。
    真跑一次英文稿量到 **42 条误报**：`For`、`That`、`American`、`Using`……
    全是句首单词，而这一篇一处裸引用都没有。要在英文里做同一件事得上真的命名实体识别，
    不是大写字母。**42 条噪音会把这个通道整个废掉**，而它在中文稿上是有效的。

    上面那条 `_bare_citation_problems`（姓在语料里）不受影响：它认的是语料里的真姓，
    不是「看着像不像」，中英文都成立。
    """
    out: list[str] = []
    known = set(_corpus_surnames(corpus))
    seen: set[str] = set()
    for clause in re.split(r"[。！？；]|\n[ \t]*\n", body):
        if not any(v in clause for v in _ATTRIB):
            continue
        if _is_english(clause):
            continue                    # 见 docstring：英文分句里大写不携带任何信号
        for tok in re.findall(r"(?<![0-9A-Za-z])([A-Z][a-zA-Z]{2,})(?![0-9A-Za-z])", clause):
            if tok in known or tok in _NOT_CITATIONS or tok in seen:
                continue
            seen.add(tok)
            out.append(
                Finding(f"「{tok}」看着像一处引用，但语料里没有这个作者的任何文献——"
                f"**这处引用无据**。要么用 `oss lit add <DOI>` 把它抓进语料，"
                f"要么改写这句话", "cite.unknown_name")
            )
    return out


#: 最小可探测效应出现的两种写法：占位符，或渲染后的那句话。
_MDE_MENTION = re.compile(r"\{\{[a-z]+:[^}]*\.mde\}\}|最小可探测效应|minimum detectable effect|\bMDE\b")
#: 「排除」要指向效应类的东西才算那个谬误——「排除 2021 年那一轮」是正当的样本限制。
_RULES_OUT_ZH = re.compile(
    r"排除[^。；\n]{0,20}(?:(?<!可探测)效应|收敛|影响|差异|变化|移动|趋势|关联|作用)"
    r"|(?:快于|大于|超过|高于|以上|强于)[^。；\n]{0,12}排除")
_RULES_OUT_EN = re.compile(r"rules?\s+out|ruled\s+out|excludes?\s+(?:any|an?|effects?)", re.I)
#: 「不能排除」是**正确**用法，不许拦；「只能排除很小的效应」是收敛的表述，也放行。
#: 回溯窗口要够宽：`cannot be ruled out` 的否定词离「ruled out」隔着两个词，
#: 窗口窄了就把 `nnot be ` 当成没有否定——三篇英文稿子上真误报过。
# 「也**不是**排除了全部正向关联」——这是正确的否定用法，原先挡板里没有「不是」一族，
# 于是一句自我限定的话被判成犯了这个谬误（真跑时命中过）。
_NEG_ZH = re.compile(r"(?:不能|无法|不足以|难以|未能|不可能|没能|只能|不是|并非|并不是|而非)[^。；]{0,10}$")
_NEG_EN = re.compile(
    r"(?:cannot|can't|could not|couldn't|unable to|do(?:es)? not|don't|\bnot\b|\bno\b|\bonly\b|never)"
    r"[^.;]{0,24}$", re.I)


def mde_misuse(sent: str) -> str:
    """这句话有没有把最小可探测效应当成排除下界。命中返回那一小段，否则空串。

    **这是一个有固定形状的谬误：**

        MDE 是 0.008 → 我没测出来 → 所以能排除 0.008 以上的效应

    错在最后一步。MDE 的意思是「若真值有 0.008，我有八成把握测出来」；
    没测出来只说明「要么没有，要么这次运气不好」——**0.008 本身还在置信区间里，
    排除不掉**。要主张「排除」得做等效性检验（TOST），不是报 MDE。

    十一轮 CGSS 那次真跑，这句话在摘要、结果、结论里各出现一次，
    而 `oss paper check` 一路放行——它只有「p 值说反」「符号说反」「用了证明」三条规则。
    跨模型审稿抓到了，并且**独立走到了原论文作者内部评审的同一个结论**。

    `不能排除` 是正确用法，必须放行；`排除 2021 年那一轮` 是样本限制，也必须放行。
    十六篇历史稿子校准过：只在那一篇上命中两处，都是真的。
    """
    if not _MDE_MENTION.search(sent):
        return ""
    # **占位符先压成一个短记号再匹配。**`{{result:cls_mgrprof*year_c.mde}}` 有三十几个字符，
    # 夹在「排除快于每年 … 的收敛」中间就把窗口撑爆了，规则于是漏掉最典型的那一句。
    # 放宽窗口是另一条路，但窗口越宽误报越多——压短它，窗口才能保持紧。
    sent = re.sub(r"\{\{[^}]*\}\}", "〔值〕", sent)
    for pat, neg in ((_RULES_OUT_ZH, _NEG_ZH), (_RULES_OUT_EN, _NEG_EN)):
        for m in pat.finditer(sent):
            head = sent[max(0, m.start() - 40):m.start()]
            if neg.search(head.strip()):
                continue                       # 「不能排除」——正确
            return sent[max(0, m.start() - 8):m.end() + 8].strip()
    return ""


def significance_problems(sent: str, path: str, val: str) -> list[Finding]:
    """这句话说的显著与否，跟它引的那个 p 值对不对得上。**没有歧义，硬拦。**

    只管 `.p` 的占位符——别的字段轮不到这一条。
    否定先判（英文的 `insignificant` 里包含着 `significant`）；
    渲染成 `< 0.001` 的按 0.0005 算，那是渲染层的写法不是真值。
    """
    if not path.endswith(".p"):
        return []
    said_not = _says_not_sig(sent)
    said_sig = (not said_not) and _says_sig(sent)
    pval = 0.0005 if val.startswith("<") else float(val)
    out: list[Finding] = []
    if said_sig and pval > 0.05:
        out.append(
            Finding(f"「{path}」渲染出来是 {val}，句子里却说**显著**："
            f"「{sent.strip()[:48]}」——p 值大于常用阈值时不能称显著", "claims.significance")
        )
    if said_not and pval <= 0.05:
        out.append(
            Finding(f"「{path}」渲染出来是 {val}，句子里却说**不显著**："
            f"「{sent.strip()[:48]}」", "claims.significance")
        )
    return out


def _written_as(val: str, sent: str) -> bool:
    """这个 p **渲染出来的写法**在句子里有没有原样写出来（`< 0.001` 的空白可多可少）。

    只认渲染出来的写法：`{{result:did.p}}` 渲染成 `0.420`，正文手写的 `0.42`
    **不算对上**。放宽到「数值相等」就会把一个 0.42 的系数也当成 p，
    而这一路本来就是兜底的那一条——**宁可漏，不可误报**。
    """
    num = rf"(?<![\d.]){re.escape(val.lstrip('< '))}(?!\d)"
    return bool(re.search(rf"<\s*{num}" if val.startswith("<") else num, sent))


def significance_scan(paper: str, results: AnalysisResults) -> list[Finding]:
    """句子里说了显著与否、**却没引 p 的占位符**。

    `check_claims` 的显著性一路挂在 `.p` 的占位符上：一句话里没有
    `{{result:x.p}}` 就永远不查。而**摘要与结论最常下显著性断言，
    那两处又最不带 p 占位符**——评测里说反的变体有一多半漏在这。

    **只在能唯一定位到本文某个估计的 p 时才判**，两条路：

    · 同句里有指向某个估计的别的占位符（`{{result:did.coef}}`、`{{robust:X/main.se}}`）
    · 同句里的字面数字恰好等于某个已知 p 的渲染写法（含 `< 0.001` 这类）

    两条都对不上就**不判**。理由：文献综述里的「既往研究发现该效应显著」
    讲的是别人的结果，跟本文的 p 无关，拦下来就是误报；而**会误报的门，
    人会学会绕过去**（真跑里已经量到过一次——模型改写句子躲开「显著」两个字）。
    指到两个不同估计的也算对不上。

    判词复用 `significance_problems`，这里只负责认出「这句说的是哪个 p」。
    """
    body = _strip_code(paper)               # 占位符留在原地：要靠它认这句指着哪个估计
    clean = _strip_code_and_quotes(paper)   # 判词与字面数字都在抹掉占位符之后看（等长替换，偏移不变）
    known: dict[str, str] = {}
    for src, path in ([("result", f"{k}.p") for k in results.estimates]
                      + [("robust", f"{lab}/{k}.p")
                         for lab, run in results.robustness.items() for k in run.estimates]):
        if (val := _resolve_one(src, path, results, None)[0]) is not None:
            known[path] = val
    if not known:
        return []
    # 老那一路（`sentences_with`）判过的那几段整段跳开。**两边的窗口不一样宽**：
    # 那边单个换行不算句末，这边算——只跳「同句里有 .p 占位符」会把同一处报两遍。
    judged = [_span(body, m.start(), m.end(), _SENT_BOUND)
              for m in PLACEHOLDER.finditer(body) if m.group(2).endswith(".p")]
    out: list[Finding] = []
    for lo, hi, raw in textspan.sentences(body):
        if any(lo < b and a < hi for a, b in judged):
            continue
        sent = clean[lo:hi]
        if not _says_sig(sent) and not _says_not_sig(sent):
            continue
        paths = {m.group(2).rsplit(".", 1)[0] + ".p"
                 for m in PLACEHOLDER.finditer(raw)
                 if m.group(1) in ("result", "robust")} & set(known)
        if len(paths) > 1:
            continue                        # 一句话指着两个估计——定位不唯一，不判
        if not paths:
            paths = {p for p, v in known.items() if _written_as(v, sent)}
            if len({known[p] for p in paths}) != 1:
                continue                    # 一个数都没对上，或对上了两个不同的 p
        path = sorted(paths)[0]
        out.extend(significance_problems(sent, path, known[path]))
    return out


def direction_warnings(sent: str, path: str, val: str) -> list[Finding]:
    """句子里的方向词跟系数的符号对不对得上。**中文用法有歧义，只警告。**

    **交互项整个不查**（路径里带 `*` 的）。交互项的符号说的是「这个关系怎么变」，
    而句子里几乎总要同时点出那个关系本身的方向——两者是两码事，凑在一起必然误判。
    三次自由跑一次量到 5 条误报，**全部是交互项**：

      ·「方向不是向上转移，而是差距进一步扩大」——系数为负，句里有「向上」
      ·「受教育年限与过劳的负向关联随时间显著变平（+0.010）」——系数为正，句里有「负向」

    后一句还是对的：负向关联变平，交互项就该是正的。**判据看不出这层，那就别判。**
    """
    if not path.endswith(".coef") or "*" in path:
        return []
    try:
        coef = float(val)
    except ValueError:
        coef = 0.0
    up, down = _says_up(sent), _says_down(sent)
    if up == down:      # 一句话里两个方向都有，多半是在做比较，不判
        return []
    if coef > 0 and down:
        return [
            Finding(f"「{path}」是正的（{val}），句子里的方向词却是向下的："
            f"「{sent.strip()[:48]}」——**符号与措辞对不上，核一遍**", "claims.direction")
        ]
    if coef < 0 and up:
        return [
            Finding(f"「{path}」是负的（{val}），句子里的方向词却是向上的："
            f"「{sent.strip()[:48]}」——**符号与措辞对不上，核一遍**", "claims.direction")
        ]
    return []


def proof_warnings(sent: str, ns: str) -> list[Finding]:
    """结果句里出现「证明」类措辞。**统计显著不等于证明**，观察性设计更谈不上。

    `ns` 是占位符的命名空间：只有结果与稳健性（`result` / `robust`）所在的句子算，
    引文句里的「证明」说的是别人做过什么，不归这条管。
    """
    if ns not in ("result", "robust") or not _says_proof(sent):
        return []
    return [
        Finding(f"「{sent.strip()[:48]}」里出现了「证明」类措辞。"
        f"**统计显著不等于证明**，观察性设计更谈不上", "claims.prove")
    ]


def mde_warnings(paper: str) -> list[Finding]:
    """最小可探测效应被当成排除下界。**扫全文的句子，不只扫带占位符的那些。**

    `check_claims` 的其余规则都挂在占位符上（找到 {{result:…}}，看它所在的那句话）。
    但这个谬误常常写在摘要和结论里，而那两处最不带数字——十一轮 CGSS 那次
    三处误用里有一处整句没有占位符。所以这一条单独扫一遍。

    **警告不拒绝。**错是同一个错，但写法有宽窄：`能排除每年超过这一幅度的收敛`
    是硬错，`can rule out only a large decline` 是同一个推理的克制版本。
    十六篇校准下来三处命中，其中一处属于后者——**2/3 的准确率不够格当拒绝**
    （今天两次教训：会误报的门，人会学会绕过去）。
    """
    out: list[Finding] = []
    # **这条检查要的窗口比句子宽。**「最小可探测效应是 X。因此可以排除更大的效应」——
    # 病症天生跨句，切到句子就抓不到（Task 1 并切句时踩过这个坑，考卷没测出来）。
    # 按行与中文句末切，跟这条检查校准十六篇时用的窗口一致，不要「顺手」换成 textspan.sentences()。
    for sent in re.split(r"(?<=[。！？])|\n", _strip_code(paper)):
        if seg := mde_misuse(sent):
            out.append(
                Finding(f"把**最小可探测效应当成了排除下界**：「{seg[:56]}」。"
                f"MDE 的意思是「若真值有这么大，本样本有八成把握测出来」；"
                f"**没测出来不等于排除了它**——那个值本身还在置信区间里。"
                f"要主张「排除」得报置信区间上界或做等效性检验（TOST）；"
                f"否则把话改成「不能排除」", "claims.mde_exclusion")
            )
    return out


#: 「提到了功效」算数的说法。只认「功效」二字的时候，作者用标准同义说法反倒被判成没交代——
#: 评测 injection-v2 丁组 5 个误报里 3 个是这么来的（「统计检验力」「检验效能」）。
#: **「把握」不进表**：因果底板的局限里「平行趋势检验对线性违背把握不足」说的是另一件事，
#: 进了表「删句」那三个变体就漏拦了；「把握度」是完整的术语，可以进。
#: 英文侧沿用原来的口径：整段小写之后找 `power`（`statistical power` 一并覆盖）。
_POWER_WORDS = re.compile(r"功效|检验力|检验效能|把握度|power", re.I)

#: 「提到了精度」算数的说法（精度那一路照旧也认功效的说法，见 `limits_power_problems`）。
#: 「区间半宽」「更小的差别分辨不出来」是说明书叫作者写的原话，不该判成没交代。
_PRECISION_WORDS = re.compile(r"精度|精确度|区间(?:宽度|半宽|长度)|分辨|precision", re.I)

#: 「提到了共线」算数的说法。`collinear` 把 `multicollinearity` 一并覆盖。
#: **「高度相关」不进表**：只说两个变量相关、不说系数不可单独解读，考卷上算漏写。
#: **VIF 只认大写**（`(?-i:VIF)`）：这三个字母小写起来太容易撞上——注入考卷的挂靠记号
#: `<!--anchor:vif_note-->` 就带着一个 `vif`，不区分大小写的话删掉那句话也算交代过了。
#: `variance inflation` 是真跑稿子上核出来的（`runs/r20260825_dem_prior/paper.md` 第 47 行）：
#: 那一句的「共线」二字只在占位符 `{{diag:共线诊断（VIF）}}` 的路径里，抹掉占位符之后
#: 只剩英文的这两个词——不进表就成了新的误报。
_COLLIN_WORDS = re.compile(r"共线|方差膨胀|(?-i:VIF)|collinear|variance[ -]inflation", re.I)

#: 与诊断相反的断言。判词是 fail / gray 还写这些，**比不写更糟**——
#: 评测 injection-v2：说反 0/4 全漏，因为这几道检查只看关键词在不在。
#: **说反优先于「提到了」**：放宽同义说法之后「统计检验力充足」两边都命中，按硬问题报。
#: 功效与共线分两张表，不合成一张：两者的取节范围不同（见下），合在一起会让
#: 方法一节里的一句「功效充足」报成共线说反。
_POWER_CONTRA = re.compile(r"(?:统计)?(?:功效|检验力|检验效能)(?:充足|充分|足够|没有问题)"
                           r"|精度(?:足够|充分|没有问题)")
_COLLIN_CONTRA = re.compile(r"(?:不存在|没有(?:明显|严重)?|未(?:发现|见))[^。；]{0,6}共线"
                            r"|共线(?:问题)?(?:并?不(?:严重|明显)|可以忽略)")

#: 功效 / 精度看哪几节：局限，没有就退到讨论（`section_body` 命中第一个就返回）。
_POWER_SECTIONS = ("limitations", "discussion")
#: 共线看哪几节：诊断报在方法一节，交代多半也写在那里；真跑稿子里还有写在结果一节的
#: （`runs/gss20260824/paper.md` 第 116 行）。**引言不算**——在引言里带过一句「共线」
#: 不是对「这几个系数不可单独解读」的交代。
_COLLIN_SECTIONS = ("data_methods", "results", "discussion", "limitations")


def _section_clean(paper: str, *keys: str) -> str:
    """取节正文，**占位符先抹空再查**。命中第一个给定的节名就返回（`textspan.section_body`）。

    占位符不算「提到了」：`{{power:power}}` 的名字里自带 `power`，不抹掉的话
    局限里只剩这一个占位符也算交代过功效了——评测 injection-v2「只剩数值」3 个全漏在这。
    """
    return _strip_code_and_quotes(textspan.section_body(paper, *keys))


def limits_power_problems(paper: str, power) -> list[Finding]:
    """功效（或精度）判灰、判死，局限里得写。**这是审稿人最先看的。**

    取局限一节，没有就退到讨论（中英文与「一、」编号前缀都认）。
    描述类按精度判（描述骨架设计稿第四节）：说明书叫局限写「这份数据分不出小于多少的差别」，
    写「精度」就算交代；写「功效」也照旧认。

    三档，**先判说反**：写了与判词相反的话（「统计功效充足」）比不写更糟，按另一个编号硬拦。
    """
    if power is None or getattr(power, "verdict", "") not in ("fail", "gray"):
        return []
    if getattr(power, "mode", "power") == "evidence":
        # 测量类判「证据做不做得出来」，既不算功效也不算精度（测量骨架设计稿第四节）——
        # 局限里当然也没有功效可交代。今天 evidence 的判词恒为 pass，这一支走不到；
        # **写死是为了将来**：evidence 一旦出现非 pass 的判词，下面两条会冒出一句
        # 「功效判词是 X，局限里一个字没提功效」的假警报，而这篇论文压根没有功效这回事
        return []
    limits = _section_clean(paper, *_POWER_SECTIONS)
    precision = getattr(power, "mode", "power") == "precision"
    if m := _POWER_CONTRA.search(limits):
        what = "精度" if precision else "功效"
        return [
            Finding(f"{what}判词是 **{power.verdict}**，局限里却写「{m.group(0)}」——"
            f"**跟判词正相反，比不写更糟**。判死或落灰区就照实写：这份数据测不出多小的效应",
            "claims.limits_power_contra")
        ]
    if precision:
        if not (_PRECISION_WORDS.search(limits) or _POWER_WORDS.search(limits)):
            return [
                Finding(f"精度判词是 **{power.verdict}**，但局限里一个字没提精度。"
                f"**精度落灰区或不足都必须写进局限**——写明这份数据分不出小于多少的差别", "claims.limits_power")
            ]
    elif not _POWER_WORDS.search(limits):
        return [
            Finding(f"功效判词是 **{power.verdict}**，但局限里一个字没提功效。"
            f"**判死或落灰区都必须写进局限**——这是审稿人最先看的", "claims.limits_power")
        ]
    return []


def limits_collinearity_problems(paper: str, results: AnalysisResults) -> list[Finding]:
    """共线诊断报了「严重」，而方法、结果、讨论、局限四节一个字没提共线。

    主规格和**每一项稳健性各自的**诊断都要看。只扫顶层的话，补充检验里的严重共线
    会整个漏掉——三轮互盲审稿里就是这么被抓到的（R3 的诊断写着「严重」，正文没提）。

    从前是全文子串，在引言里带过一句「共线」就算交代过了；现在收回到上面四节。
    跟功效那条一样先判说反：「没有明显的共线问题」跟诊断对着干，按另一个编号硬拦。
    """
    said = "".join(_section_clean(paper, k) for k in _COLLIN_SECTIONS)
    contra = _COLLIN_CONTRA.search(said)
    if _COLLIN_WORDS.search(said) and contra is None:
        return []                                   # 交代过了就不管写在这四节的哪一节
    specs: list[tuple[str, str]] = [
        ("主规格", results.diagnostics.get("共线诊断（VIF）", ""))
    ]
    specs += [
        (label, run.diagnostics.get("共线诊断（VIF）", ""))
        for label, run in results.robustness.items()
    ]
    out: list[Finding] = []
    for name, txt in specs:
        if "严重" not in txt:
            continue
        if contra is not None:
            out.append(
                Finding(f"**{name}**的共线诊断报了**严重**（{txt[:40]}），正文却写「{contra.group(0)}」——"
                f"**跟诊断正相反，比不写更糟**。有系数不可单独解读就照实说", "claims.limits_collinearity_contra")
            )
            continue
        out.append(
            Finding(f"**{name}**的共线诊断报了**严重**（{txt[:40]}），但全文没提共线。"
            f"**有系数不可单独解读时，必须在方法或局限里说**", "claims.limits_collinearity")
        )
    return out


def placeholder_targets(paper: str) -> set[str]:
    """正文引用了哪几份**产出**：每个占位符指向的那个名字。

    `{{result:alpha.coef}}` → `alpha`；`{{robust:R1/main.coef}}` → `R1`；
    `{{bin:女性/last.slope}}` → `女性`；`{{diag:AR 区间}}` / `{{fig:coef}}` / `{{table:main}}` → 路径本身。
    引文（`cite` / `citep`）指的是文献不是产出，不算。

    代码块里的占位符不算引用（`_strip_code`）：说明书示例里贴一行 `{{result:…}}`
    不是论文在引用那份产出——跟别的检查同一口径。
    """
    out: set[str] = set()
    for m in PLACEHOLDER.finditer(_strip_code(paper)):
        source, path = m.group(1), m.group(2).strip()
        if source in ("cite", "citep"):
            continue
        if source == "result":
            out.add(path.rsplit(".", 1)[0] if "." in path else path)
        elif source in ("robust", "bin"):
            out.add(path.split("/", 1)[0])
        else:                                   # diag / fig / table / power
            out.add(path)
    return out


def produced_outputs(results: AnalysisResults, figures=None) -> set[str]:
    """这次跑出来的产出都叫什么名字：估计、诊断、稳健性、分箱，外加运行目录里的图。

    **「登记了」不等于「做出来了」**——测量类的证据登记（`measurement.evidence_outputs`）
    写的是名字，名字在不在这个集合里才是「产出在不在」。
    """
    names = (set(results.estimates) | set(results.diagnostics)
             | set(results.robustness) | set(results.binned))
    return names | set(figures or ())


def measurement_reliability_problems(paper: str, results: AnalysisResults, measurement,
                                     figures=None) -> list[Finding]:
    """**信度不得冒充效度**（测量骨架设计稿 5.1 的 M3）：论文报了信度系数，而产出里一类效度证据都没有。

    **这道门一个字都不读论文里的论证**（设计稿第二节）。只核两件结构上的事：

    ① **论文引没引信度那份产出**——拿计划里登记的信度产出名字（`measurement.evidence_outputs`
       的 `reliability` 那一行）去比正文里占位符指向的名字。正文把它写成「内部一致性」
       「Cronbach's α」还是「题目间一致」，这里一律不看：**判的是引了哪份产出，不是怎么措辞的**。
       靠认关键词的检查在没见过的写法上只有 6%，而这类结构判据是 100%（评测结果第二版第二节）
    ② **效度证据的产出在不在**——四类效度证据里，凡登记了名字、而那个名字在 `produced_outputs`
       里真找得到的，就算这一类做出来了。**只要有一类做出来了就不拦**：报了哪一类就说哪一类的话，
       这是审稿人的活，不是门的活

    两样都成立才报：论文报了信度、而一类效度证据的产出都没有。没登记信度产出的不判——
    一份产出是不是信度系数由作者登记，**这里不猜、不认词**。
    """
    if measurement is None:
        return []
    rel = (measurement.evidence_outputs.get("reliability") or "").strip()
    if not rel or rel not in placeholder_targets(paper):
        return []
    have = produced_outputs(results, figures)
    if any(k != "reliability" and name.strip() in have
           for k, name in measurement.evidence_outputs.items()):
        return []
    return [Finding(
        f"论文引用了信度那份产出（{rel}），而产出里**一类效度证据都没有**——"
        "**信度不是效度**：信度说的是这几道题彼此一致，不是这几道题量的就是那个构念（Flake 等 2017）。"
        f"把至少一类效度证据做出来、登记进 plan.measurement.evidence_outputs（{VALIDITY_MENU}），"
        "并让那份产出真的落进结果里；做不出来就把正文里「这套测量站得住」那一路说法收回去，"
        "只说信度本身支持的话。**这项检测不读论文怎么写的**：判的是产出在不在、正文引没引信度那份产出",
        "paper.measurement_reliability_only")]


def measurement_equivalence_problems(results: AnalysisResults, measurement,
                                     figures=None) -> list[Finding]:
    """**登记了跨组测量等价检验，那份产出就得真的在**（测量骨架设计稿 5.2 的 M4，论文期那一半）。

    M4 在计划那一侧只核「登记了没有」：`evidence_outputs` 里有 `equivalence` 这一行就放行。
    于是**登记一个空壳——写个名字、那份产出根本不存在——警告与 must 级处方整套不响**，代价为零。
    这跟 M1「声明了却一个产出都没有」是同一类洞，修法也一样：**把判据从「登记说什么」
    改成「产出在不在」**（`produced_outputs`），照 M3 已有的形状，不另造一套。

    为什么补在论文期、不补在处方那一侧：`route.build` 手上没有图名表，在那里核「产出在不在」
    会把「作者其实画了一张等价检验的图」误报成没做；**论文期这一侧图名表在手上**，核得了。

    **一个字都不读论文里的论证**（设计稿第二节）：只核登记的那个名字在不在这次的产出里。
    没登记 `equivalence` 的不归这道门管——那是计划那一侧 M4 的事，这里不重复报。
    """
    if measurement is None:
        return []
    name = (measurement.evidence_outputs.get("equivalence") or "").strip()
    if not name or name in produced_outputs(results, figures):
        return []
    return [Finding(
        f"计划里登记了跨组测量等价检验的产出（equivalence: {name}），"
        f"而这次的结果里没有任何叫这个名字的产出——**登记不等于做过**："
        "跨组比较那项检测只核登记了没有，登记一个空壳就能让警告与 must 级处方整套不响，"
        "而论文照样在跨组比较。两条出路选一条：① 把等价检验真的做出来，让那份产出落进结果里"
        "（`{{result:<名>.coef}}` 的名、`{{diag:<名>}}` 的名、稳健性的 label、或图名，"
        "跟登记的名字对上）；② 把这一行从 evidence_outputs 里去掉，并把跨组比较**降级为组内描述**"
        "——每组各报各的、不比大小，在局限里写明为什么不比。"
        "**这项检测不读论文怎么写的**：判的是登记的那个名字在不在这次的产出里",
        "paper.measurement_equivalence_missing")]


def paraphrase_warnings(paper: str, corpus) -> list[Finding]:
    """转述对不对：**上一件保证文献存在，这一件保证我没说错他说了什么。**

    只报「存疑」。`citation_support` 判「核不了」（没摘要、或句子里没有能映射的词）的不报——
    「核不了」混进「存疑」，这个指标自己就成了噪声。
    """
    out: list[Finding] = []
    for sent, m in sentences_with(_strip_code(paper)):
        if m.group(1) != "cite":
            continue
        rec = find_record(corpus, m.group(2))
        if rec is None:
            continue
        status, why = citation_support(sent, rec)
        if status == "存疑":
            out.append(
                Finding(f"转述**存疑**：「{sent.strip()[:44]}」引的是 {m.group(2)}"
                f"（{(rec.title or '')[:44]}）。{why}", "claims.paraphrase")
            )
    return out


def check_claims(
    paper: str, results: AnalysisResults, power=None, corpus=None
) -> tuple[list[str], list[str]]:
    """**占位符保证数字是真的，这一段保证句子没说反。**

    占位符机制堵住了「编一个 0.032」，但堵不住「在真的 0.188 旁边写『效应在减弱』」——
    校验照样会一次通过。这是整条链上最难看出来的一种假：
    **数字全部可追溯，结论却是反的。**

    判据一律机械可判（Type-A），不需要跨模型。

    **拒绝与警告分开。**中文方向词有歧义（「减弱 −0.785」是通顺的），
    一律拒绝会逼得人没法写；而「p=0.42 却说显著」没有任何歧义，必须硬拦。

    **每一条检查都是一个模块级的纯函数**（`significance_problems`、`mde_warnings`……），
    这里只负责解析占位符、把它们串起来。别处要单独跑其中一条（交付稿上只核局限、
    评测只单测一条判据），直接调那个函数，不必整篇走一遍。
    """
    problems: list[str] = []
    warnings: list[str] = []
    body = _strip_code(paper)   # **保留占位符**——要靠它定位上下文

    for sent, m in sentences_with(body):
        val, _ = _resolve_one(m.group(1), m.group(2), results, power, corpus)
        if val is None:
            continue
        path = m.group(2)
        problems.extend(significance_problems(sent, path, val))
        warnings.extend(direction_warnings(sent, path, val))
        warnings.extend(proof_warnings(sent, m.group(1)))

    problems.extend(significance_scan(paper, results))
    warnings.extend(mde_warnings(paper))

    # ── 该写而没写的
    problems.extend(limits_power_problems(paper, power))
    if power is not None and getattr(power, "verdict_fragile", False):
        if "敏感" not in paper and "sensitiv" not in paper.lower():
            warnings.append(
                Finding("功效判词**对假定参数敏感**（换个折价系数就翻盘），"
                "正文却没有报敏感性分析。用 {{power:sensitivity}} 引进来", "claims.power_fragile")
            )

    problems.extend(limits_collinearity_problems(paper, results))
    warnings.extend(paraphrase_warnings(paper, corpus))

    for f in _bare_citation_problems(body, paper, corpus):
        # 去掉变音记号才对得上的那一档只提示，理由见 `_bare_citation_problems` 的 docstring
        (warnings if code_of(f) == "cite.bare_surname_folded" else problems).append(f)
    warnings.extend(_bare_citation_warnings(body, corpus))
    warnings.extend(_no_author_warnings(paper, corpus))
    warnings.extend(_unverified_title_warnings(paper, corpus))
    if PLACEHOLDER.search(paper) and "cite:" in paper and not REFERENCES.search(paper):
        warnings.append(
            Finding("引用了文献但正文里没有 {{references}}——"
            "**参考文献列表应当自动生成**，手写的会漏也会多", "claims.references_missing")
        )

    # 功效用的人数与实际分析人数对不对得上。**纯机械可判，之前没人核。**
    if power is not None and getattr(power, "n_raw", 0) and results.n:
        if int(power.n_raw) != int(results.n):
            warnings.append(
                Finding(f"功效计算用的样本量是 {power.n_raw}，而实际分析样本量是 {results.n}"
                f"（差 {abs(int(power.n_raw) - int(results.n))}）。"
                f"事前功效用估计值是正常的，**但正文若同时报告这两个数，必须说明差异**——"
                f"否则读者会以为其中一个写错了", "claims.power_n")
            )

    return problems, warnings


#: 「这一句在拿分箱当证据」的信号词。**光提到「分箱」不算**——
#: 方法一节写「本文未做分箱」也会命中「分箱」两个字。
_SHAPE_CLAIM = ("显示", "表明", "呈", "形态", "非单调", "单调",
                "最高", "最低", "回升", "收敛", "趋近", "为准", "说明")
_SHAPE_DENIED = ("未做", "没有做", "不适用", "无法", "未报告", "未纳入")
_SHAPE_CLAIM_EN = re.compile(
    r"\b(shows?|indicat\w*|reveal\w*|suggests?|display\w*|non-?monoton\w*"
    r"|monoton\w*|highest|lowest|converg\w*|recover\w*|pattern|shape)\b", re.I)
_SHAPE_DENIED_EN = re.compile(
    r"\b(did\s+not|does\s+not|no\s+binned|not\s+(performed|reported|available|applicable)"
    r"|cannot|unavailable|was\s+not)\b", re.I)
_BINNED_KW = re.compile(r"分箱|\bbinned\b|\bbin-level\b", re.I)


def _binned_without_record(paper: str, results: AnalysisResults) -> list[str]:
    """论文拿分箱当形状依据，而 `results.json` 里根本没有分箱。

    **这是被跨模型审稿抓到两次的同一个问题。**第一次的原话：

    > 「分箱估计被指定为『读者应以分箱图为准』的形状依据，
    >   但 results.json 中没有分箱估计的输出，读者无法核实。」

    第二次是在完全独立的一次运行里——不同的题目、不同的数据、
    不同的执行模型。**同一个坑踩两次，说明缺的是一道门，不是一次疏忽。**

    成因很具体：`plan.binned` 留空时 `oss run` 就不算，
    而 `oss figure --kind marginal` 会自己另算一遍去画图。
    于是那几个斜率**只存在于图片里**，谁也复核不了。

    **不自动补算。**「没在计划里声明就不跑」是事前锁定的规矩——
    工具悄悄加一项没声明的分析，这条规矩就废了。所以这里只拦，
    并且把两条出路都写清楚。

    只在**拿它当证据**时拦。光提到「分箱」不拦，否则
    「本文未做分箱估计」这种正当写法会被误伤——
    **警报天天响就等于没有警报。**
    """
    if results.binned:
        return []
    body = _strip_code_and_quotes(paper)
    hits = []
    for m in _BINNED_KW.finditer(body):
        lo, hi = _span(body, m.start(), m.end(), _SENT_BOUND)
        sent = body[lo:hi]
        if any(w in sent for w in _SHAPE_DENIED) or _SHAPE_DENIED_EN.search(sent):
            continue
        if any(w in sent for w in _SHAPE_CLAIM) or _SHAPE_CLAIM_EN.search(sent):
            hits.append((body[:m.start()].count("\n") + 1, sent.strip()))
    if not hits:
        return []
    where = "；".join(f"第 {ln} 行「{s[:40]}…」" for ln, s in hits[:3])
    return [
        Finding(f"正文拿分箱估计当形状依据（{where}），"
        f"但 results.json 的 `binned` 是空的——**那几个数字只存在于图片里，读者复核不了**。"
        f"两条出路，选一条：① 在 plan.json 里声明 `binned`（`by` 写斜率随哪一列变）"
        f"后重跑 `oss run`；② 删掉这个断言。"
        f"**不要保留断言而不留出处**——这个问题已经被跨模型审稿抓到过两次", "paper.binned_no_record")
    ]


#: prep.py 里给数据加列的写法：d['x'] = … / df["x"] = … / d.x = …
_ASSIGN = re.compile(
    r"""\b(?:d|df|data|frame)\s*(?:\[\s*['"]([A-Za-z_]\w*)['"]\s*\]|\.([A-Za-z_]\w*))\s*=(?!=)"""
)


def derived_columns(script: str) -> list[str]:
    """从构造变量的脚本里抽出它造了哪些列（按出现顺序，去重）。

    这是「构造变量进计划」的轻版：不造小语言，只登记。
    脚本里 `d['post_dem'] = …` 这种赋值就是派生列；`paper check` 拿它对着论文看，
    分析用到的派生列在论文里一个字没提，就警告——方法节必须交代构造式。
    """
    out: list[str] = []
    for m in _ASSIGN.finditer(script):
        name = m.group(1) or m.group(2)
        if name and name not in out:
            out.append(name)
    return out


def unmentioned_derived(paper: str, used: set[str], derived: list[str]) -> list[str]:
    """分析用到、脚本里构造、论文里没提的列名。"""
    body = _strip_code_and_quotes(paper)
    return [c for c in derived
            if c in used and not re.search(rf"(?<!\w){re.escape(c)}(?!\w)", body)]


def _mixed_spec_contrast(paper: str, results: AnalysisResults) -> list[str]:
    """一句话里对比两个规格的主效应，而一个含交互项、一个不含——**比的不是同一个量**。

    含交互项时，处理变量的系数是参照组的条件效应；去掉交互项才是平均效应。
    验证跑的摘要写「限制样本后平均效应 0.740；放回左删失国家升到 1.522」——
    前者来自不含交互项的 R11，后者来自含交互项的 R0，是两种量。
    审稿人抓到了前一半（0.127 是条件效应），执行者补了 R11，却拿它去跟 R0 比。
    机械能判的是「两个规格的设定不同」；对比合不合理是判断，所以只警告。
    """
    def _has_inter(ests) -> bool:
        return any("*" in e.term for e in ests.values())

    # 换行也算边界：表格的每一行是一条记录，不是「一句话里的对比」——
    # 真论文的表 1 把 12 个规格列在一起，按段落切会把整张表当成一句话报警
    bound = re.compile(r"[。！？\n]|" + _EN_SENT)
    groups: dict[tuple[int, int], dict[str, bool]] = {}
    for m in PLACEHOLDER.finditer(paper):
        src, path = m.group(1), m.group(2)
        if src == "result" and path == "main.coef":
            label, inter = "主规格", _has_inter(results.estimates)
        elif src == "robust" and "/" in path and path.split("/", 1)[1] == "main.coef":
            label = path.split("/", 1)[0]
            run = results.robustness.get(label)
            if run is None or run.error:
                continue
            inter = _has_inter(run.estimates)
        else:
            continue
        span = _span(paper, m.start(), m.end(), bound)
        groups.setdefault(span, {})[label] = inter
    out = []
    for (lo, _hi), specs in groups.items():
        if len(specs) >= 2 and len(set(specs.values())) > 1:
            line = paper[:lo].count("\n") + 1
            with_i = "、".join(k for k, v in specs.items() if v)
            without = "、".join(k for k, v in specs.items() if not v)
            out.append(
                Finding(f"第 {line} 行把「{with_i}」和「{without}」的主效应放在一句里对比，"
                f"但前者含交互项（主效应是参照组的条件效应）、后者不含（平均效应）——"
                f"**不是同一个量**。要比就比同一设定下的两个样本", "paper.mixed_spec_contrast")
            )
    return out


def _placeholder_finding(text: str, source: str) -> Finding:
    """占位符解析不了：按命名空间挂编号——评测归因要分清坏的是估计、引文、图、表、诊断还是稳健性。"""
    if source == "result":
        return Finding(text, "paper.placeholder_result")
    if source in ("cite", "citep"):
        return Finding(text, "paper.placeholder_cite")
    if source == "fig":
        return Finding(text, "paper.placeholder_fig")
    if source == "table":
        return Finding(text, "paper.placeholder_table")
    if source == "diag":
        return Finding(text, "paper.placeholder_diag")
    if source == "robust":
        return Finding(text, "paper.placeholder_robust")
    return Finding(text, "paper.placeholder_other")


def check(paper: str, results: AnalysisResults, power=None, corpus=None,
          figures: set[str] | None = None,
          known_numbers: set[str] | None = None,
          required_sections: tuple[tuple[str, ...], ...] | None = None,
          fulltext: tuple[int, int] | None = None,
          labels: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """校验稿件。返回 `(拒绝理由, 警告)`。

    拒绝：少节、裸小数、占位符解析不了。
    警告：整数疑似手打的结果。

    `required_sections` 给了就按它查节（风格包的骨架），没给按通用的五节。
    """
    problems: list[str] = []
    warnings: list[str] = []
    body = _strip_code_and_quotes(paper)

    # ── 探索结果冒充正式结果：oss run --explore 在 plan_id 上打了戳，拷成 results.json 也擦不掉。
    # **这一条要长在 check 里而不是 CLI 里**——render 也走这里，
    # 而 render 才是把数字真正写进稿子的那一步。
    if str(getattr(results, "plan_id", "")).startswith("explore:"):
        problems.append(Finding("results.json 是探索结果冒充的（plan_id 以 explore: 开头）——探索不进论文", "paper.explore_results"))

    # ── 节齐不齐
    for names in (required_sections or REQUIRED_SECTIONS):
        if not any(
            re.search(rf"^#{{1,4}}\s*.*{re.escape(n)}", paper, re.M | re.I)
            for n in names
        ):
            problems.append(Finding(f"缺「{names[0]}」（{'/'.join(names[1:])}）这一节", "paper.section_missing"))

    # ── 裸小数：**这是核心那条**
    #
    # 例外只有一条：**这个数原样出现在本次运行自己的产出文件里**（结果、功效、
    # 画像、留痕）。裸小数禁令防的是编造，而一个能在产出里找到出处的数不是编造。
    # 没有这条例外，规则会互相打架：英文稿要求把中文诊断转述成散文，
    # 转述里照抄「0.3–0.5 时翻为 fail」立刻被裸小数拦下；上次真跑里
    # 合并损失 48.6% 被迫改写成 "nearly half"——**规则在逼人把精确的写成模糊的**。
    known = known_numbers or set()
    for m in BARE_DECIMAL.finditer(body):
        if m.group(1).lstrip("-") in known:
            continue
        line = body[: m.start()].count("\n") + 1
        problems.append(
            Finding(f"第 {line} 行有裸小数 {m.group(1)}——"
            f"统计结果必须写成占位符 {{{{result:xxx.yyy}}}}，不能手打", "paper.bare_decimal")
        )

    # ── 占位符指向的东西存不存在
    for m in PLACEHOLDER.finditer(paper):
        val, why = _resolve_one(m.group(1), m.group(2), results, power, corpus,
                                figures=figures, en=_is_english(paper))
        if val is None:
            problems.append(_placeholder_finding(f"占位符 {m.group(0)} 解析不了：{why}", m.group(1)))

    # ── 整数里有没有疑似手打的结果
    numeric = {results.n}
    for e in results.estimates.values():
        numeric |= {round(abs(e.coef)), round(abs(e.se))}
    for m in BARE_INT.finditer(body):
        try:
            val = int(m.group(1))
        except ValueError:
            continue
        if 1900 <= val <= 2100:
            continue  # 年份
        if val in numeric:
            line = body[: m.start()].count("\n") + 1
            warnings.append(
                Finding(f"第 {line} 行的 {val} 跟结果里的某个数值相同——"
                f"如果那是统计结果，应当写成占位符而不是手打", "paper.bare_integer")
            )

    if not PLACEHOLDER.search(paper):
        warnings.append(
            Finding("全文没有任何占位符。**一篇实证论文不引用自己的估计结果是可疑的**", "paper.no_placeholders")
        )

    problems.extend(_binned_without_record(paper, results))
    warnings.extend(_mixed_spec_contrast(paper, results))

    # ── 桌拒自查里能机械查的几条。评审：编辑 30 秒内就会因为这些形成负面印象。
    problems.extend(process_language(paper))
    problems.extend(_caption_problems(paper))
    warnings.extend(_abstract_numbers(paper))
    warnings.extend(_abstract_statistics(paper))
    warnings.extend(_abstract_length(paper))
    warnings.extend(_corner_quotes(paper))
    warnings.extend(_en_abstract_position(paper))
    warnings.extend(_spec_codes_in_tables(paper, results))
    if (cjk := cjk_references(paper, corpus)):
        warnings.append(
            Finding(f"英文稿的参考文献表里有 {len(cjk)} 条还带着汉字："
            f"{'；'.join(cjk[:3])}{'…' if len(cjk) > 3 else ''}——英文期刊不收这种条目。"
            f"非英文文献的写法是「罗马化原题 [英译]」：在 literature.json 里给这几条填 "
            f"title_romanized / title_en / venue_en / authors_romanized，**原题留在 title 不要动**",
            "paper.cjk_reference")
        )
    if (bare := unlabeled_terms(results, labels)) and "{{table:" in paper:
        shown = "、".join(bare[:8]) + ("…" if len(bare) > 8 else "")
        warnings.append(
            Finding(f"表里会印出原始列名：{shown}——发表用的表这一列该是人话。"
            f"在 plan.json 的 labels 里给它们填短名（例：{bare[0]} → 某某）；"
            f"binding.json 里已有构念名的列会自动取，不用重复填", "paper.bare_column_names")
        )
    for ln, text in heading_run_ins(paper):
        warnings.append(
            Finding(f"第 {ln} 行的标题「{text[:40]}」上面没有空行——pandoc 认不出它是标题，"
            f"Word 里会变成上一段末尾一串裸露的「{text[:20]}」。导出时会自动补，源稿里也补上",
            "paper.heading_run_in")
        )
    np_, nw = _novelty_problems(paper, fulltext)
    problems.extend(np_)
    warnings.extend(nw)
    # 描述论文（结果里有分组表）引了 {{table:groups}} 就算有主表，理由是 `GROUPS_MAIN_TABLE` 那一句。
    # 跟导出（`oss/api/paper.py`）、合规规则 tables（`skillcheck.r_tables`）同一口径、同一句话
    if results.groups and "{{table:main}}" not in paper and "{{table:groups}}" not in paper:
        warnings.append(
            Finding("全文没有分组描述表 {{table:groups}}——" + GROUPS_MAIN_TABLE + "。"
            "导出 Word 时会拒绝。表注在占位符上一行：*Table 2. Group means.*", "paper.main_table_missing")
        )
    elif not results.groups and results.estimates and "{{table:main}}" not in paper:
        warnings.append(
            Finding("全文没有主结果表 {{table:main}}——**量化论文没有回归表是硬伤**，"
            "导出 Word 时会拒绝。表注在占位符上一行：*Table 2. Main estimates.*", "paper.main_table_missing")
        )

    # ── 主张跟结果对不对得上。**数字真不等于话对。**
    if not problems:  # 占位符都解析不了时，谈主张一致性没有意义
        cp, cw = check_claims(paper, results, power, corpus)
        problems.extend(cp)
        warnings.extend(cw)

    return problems, warnings


# ------------------------------------------------------------------ 渲染


def process_language(paper: str) -> list[str]:
    """正文里的研究过程痕迹。返回拒绝理由（每行一条，列出命中的词）。"""
    body = _strip_code_and_quotes(paper)
    en = _is_english(paper)
    # 系统专名两种语言都查——`oss run`、`1-profile`、`social-paper` 不分中英
    rules = _PROCESS_EN + [_system_name_rule()] + ([] if en else _PROCESS_ZH)
    out: list[str] = []
    for i, line in enumerate(body.split("\n"), start=1):
        if line.lstrip().startswith("|"):
            continue                                    # 表格行：稳健性标签 R1 之类可以在表里
        hits: list[str] = []
        for pat, label in rules:
            m = pat.search(line)
            if m:
                hits.append(m.group(0))
        if hits:
            out.append(
                Finding(f"第 {i} 行有研究过程的痕迹「{'、'.join(dict.fromkeys(hits))}」——"
                f"审计 / 审稿 / 工具 / 规格代号不属于正文：写结论本身，不写它是怎么来的"
                f"（\"the audit found X\" → 直接写 X；\"the R3 specification\" → 说那个规格换了什么）", "paper.process_language")
            )
    return out


#: 新颖性主张。评审第 7 条：「22 篇入选精读只拿到 1 篇全文……在这个基础上写
#: 'nobody has put informational cost in the moderating position' 是不成立的——
#: 你没有能力知道有没有人做过。这一条单独就足以让编辑退稿。」
_NOVELTY = re.compile(
    r"\b(?:the first (?:study|paper|analysis|attempt|to)|first to (?:test|estimate|examine|study|show|document)|"
    r"no (?:prior|previous|existing|earlier) (?:study|work|research|paper|literature)|"
    r"has not (?:yet |previously )?been (?:studied|examined|tested|estimated|explored|addressed|asked)|"
    r"(?:nobody|no one|no study|no paper) has|to (?:our|the best of our) knowledge,? (?:no|none|this is the first)|"
    r"remains? (?:unexplored|unexamined|unstudied|untested)|fills? (?:a|this|the|an important) gap|"
    r"neither literature has|has never been)\b", re.I)
#: 中文的新颖性主张。**"首次""填补空白"只是这一族里最直白的几个说法。**
#:
#: 十一轮 CGSS 那次跑，摘要写「中国是否也走了这条路，**此前缺少**覆盖足够长时段的
#: 经验回答」，引言写「**尚未**用长时段数据回答」——而那篇论文一条文献都没检索，
#: 参考文献是「（没有语料，生成不了）」。**这门判的是 pass。**
#: 漏掉的是同一件事换了个说法：不说"没人做过"，改说"缺少回答"。
#:
#: `[^。；\n]` 里的换行不能省：不禁跨行的话，"尚未稳定，\n其斜率估计" 会被接成
#: 一条命中（真跑的稿子上量到过）。全部十六篇历史稿子校准过：加了这三族之后
#: 只多出两篇的四条命中，全部核过是真主张，零误报。
_NOVELTY_ZH = re.compile(
    r"首次|尚无(?:研究|文献)|填补.{0,6}空白|前人(?:尚)?未|据我们所知.{0,6}(?:没有|无)"
    r"|(?:缺少|缺乏|少有|鲜有|未见|尚缺)[^。；\n]{0,14}(?:研究|文献|证据|回答|分析|讨论|检验)"
    r"|尚未[^。；\n]{0,12}(?:回答|检验|考察|研究|讨论|估计)"
    r"|(?:还|仍)(?:没有|未见|缺)[^。；\n]{0,12}(?:研究|文献)")


def novelty_claims(paper: str) -> list[tuple[int, str]]:
    """正文里的新颖性主张：(行号, 命中的话)。"""
    body = _strip_code_and_quotes(paper)
    out = []
    for i, line in enumerate(body.split("\n"), start=1):
        if line.lstrip().startswith("|"):
            continue
        m = _NOVELTY.search(line) or _NOVELTY_ZH.search(line)
        if m:
            out.append((i, m.group(0)))
    return out


def _novelty_problems(paper: str, fulltext: tuple[int, int] | None) -> tuple[list[str], list[str]]:
    """新颖性门：说「首次」「尚无研究」得先读过全文。返回 (拒绝, 警告)。

    `fulltext = (读到全文的篇数, 精读集篇数)`；None 表示根本没有精读记录。
    门槛：全文至少 3 篇、且不少于精读集的一半。不够就不许说「首次」——
    改成「在我们能读到全文的 N 篇里……」或去掉。够了也只是警告：提醒把范围说出来。
    """
    claims = novelty_claims(paper)
    if not claims:
        return [], []
    where = "；".join(f"第 {i} 行「{t}」" for i, t in claims[:5])
    if fulltext is None:
        return [Finding(f"新颖性主张站不住：{where}——运行目录里没有精读记录（reading/index.json），"
                f"没读过就不能说「首次」「尚无研究」。先 oss lit fetch 精读，或把这些话改成有范围的说法", "paper.novelty")], []
    full, total = fulltext
    if full < 3 or full * 2 < total:
        return [Finding(f"新颖性主张站不住：{where}——精读集 {total} 篇只读到 {full} 篇全文，"
                f"**你没有能力知道有没有人做过**。要么补全文（配 UNPAYWALL_EMAIL / CORE_API_KEY 后重跑 "
                f"oss lit fetch），要么改写成「among the {full} studies we could read in full, …」或去掉", "paper.novelty")], []
    return [], [Finding(f"新颖性主张（{where}）建立在 {full}/{total} 篇全文上——正文把这个范围说出来"
                f"（\"among the studies we reviewed in full\"），不要写成绝对的「没有人」", "paper.novelty_scope")]


def _caption_problems(paper: str) -> list[str]:
    """每张图下一行要有图注、每张表上一行要有表注。

    验收跑的 Word 里每张图下面是一个裸露的 "coef" / "marginal"——pandoc 把图片的替代文字
    当成了图注。渲染时替代文字改成图注本身（见 `render`），前提是图注得在那儿。
    """
    lines = paper.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        for m in re.finditer(r"\{\{fig:([^}]+)\}\}", line):
            name = m.group(1).strip()
            nxt = next((l for l in lines[i + 1:i + 4] if l.strip()), "")
            if not _CAPTION_FIG.match(nxt.strip()):
                out.append(Finding(f"图「{name}」（第 {i + 1} 行）下面没有图注——下一行要以 *Figure N. …* 开头", "paper.caption_missing"))
        for m in re.finditer(r"\{\{table:([^}]+)\}\}", line):
            name = m.group(1).strip()
            prev = next((l for l in reversed(lines[max(0, i - 3):i]) if l.strip()), "")
            if not _CAPTION_TAB.match(prev.strip()):
                out.append(Finding(f"表「{name}」（第 {i + 1} 行）上面没有表注——上一行要以 *Table N. …* 开头", "paper.caption_missing"))
    return out


def _en_abstract_position(paper: str) -> list[str]:
    """中文稿里**当作文后附件**的英文摘要，位置对不对。

    只判一种排法：英文摘要落在正文**之后**、参考文献**之前**——那一处在任何体例下都不对，
    十一轮 CGSS 那篇成稿就卡在这儿（结论下面直接冒出一段英文）。

    **不判的两种都是正当排法，别误伤**：
    · 压根没有英文摘要——很多刊不要，`REQUIRED_SECTIONS` 里中英摘要命中任一即可
    · 英文摘要排在文首（紧跟中文摘要）——GB/T 7713.1 的学位论文就要求放前面，部分期刊也印在首页

    文后这一种的通行顺序是正文 → 参考文献 → 英文题名 / 摘要 / 关键词
    （《社会学研究》：参考文献「列于全文之后，英文标题、摘要、关键词之前」），
    起稿模板按它出。**目标刊另有说法时以风格包为准**，所以这里是警告不是拒绝。
    """
    if _is_english(paper):
        return []
    en = re.search(r"^#{1,4}\s*Abstract\s*$", paper, re.M | re.I)
    refs = re.search(r"^#{1,4}\s*参考文献", paper, re.M)
    if en is None or refs is None or en.start() > refs.start():
        return []
    # 在正文开头之前 = 排在文首，那是正当排法
    body = re.search(r"^#{1,4}\s*(?:[一二三四五六七八九十]+、\s*)?(?:引言|导论|绪论|问题的提出|Introduction)",
                     paper, re.M | re.I)
    if body is not None and en.start() < body.start():
        return []
    return [Finding("英文摘要夹在正文和参考文献之间——文后英文摘要的通行顺序是"
                    "正文 → 参考文献 → 英文题名 / 摘要 / 关键词。"
                    "把 ## Abstract 整段移到 ## 参考文献 之后（排在文首也可以，看目标刊要哪种）",
                    "paper.en_abstract_position")]


#: 规格代号：R1、R3、M2 之类。**正文里是硬拦的**（`_PROCESS_EN` / `_PROCESS_ZH`），
#: 但表格行被显式放行（见 `process_language`），于是代号能从稳健性 label 一路进表。
#: 范文的表里不出现代号——写它换了什么，不写它叫什么。
_SPEC_CODE = re.compile(r"^[A-Z]\d+\b")


def _spec_codes_in_tables(paper: str, results: AnalysisResults) -> list[str]:
    """稳健性表里会印出来的规格代号。"""
    if "{{table:robustness}}" not in paper:
        return []
    bad = [lb for lb in results.robustness if _SPEC_CODE.match(str(lb).strip())]
    if not bad:
        return []
    return [Finding(f"稳健性表里会印出规格代号：{'、'.join(bad[:6])}"
                    f"{'…' if len(bad) > 6 else ''}——期刊的表里不写代号，写这一项**换了什么**"
                    f"（R1 Outcome: under-5 mortality → Outcome: under-5 mortality）。"
                    f"改 plan.json 的 robustness label，重跑 oss run", "paper.spec_code_in_table")]


#: 摘要里不该出现的占位符：标准误、p 值、最小可探测效应、样本量，以及功效。
#: **59 篇已发表的中文摘要里一篇都没有这些**（2026-09-23 实测，见设计稿）——
#: 期刊摘要报的是读者能理解的效应量（「小时工资增加 1.88 元」），不是模型的统计量。
#: 它们在正文和表里照旧完整报，只是不进摘要。
#:
#: **`{{diag:}}` 不在此列**：诊断占位符里什么都有，而且有正当的摘要用法——
#: 弱工具那篇的「第一阶段有效 F 只有 X，常规区间不能用来推断」就是它的核心告诫，
#: 不是多余的统计量。只禁这四个量，跟用户 2026-09-23 定的「甲」一致。
_ABSTRACT_STATS = re.compile(r"\{\{(?:result|robust)[^}]*\.(se|p|mde|n)\}\}|\{\{(power):")

#: 中文摘要的字数上限。《中国社会科学》发文格式：「300 字以内的中文摘要」；
#: GB/T 7713.2—2022 说报道性摘要 400 字左右。取严的那个。
#: **实测我们生成的 8 篇全部超 300 字、6 篇超 400 字**，中位 480 字，是已发表中位数（230）的两倍。
ABSTRACT_MAX_ZH = 300


def _abstract_body(paper: str) -> str:
    """摘要正文（去掉关键词那一行）。三条摘要检查共用。

    **按稿子的语言挑那一节**：中文稿多半在文末还有一段英文摘要，
    先找 `Abstract` 会量到英文那一段——`ossrun-c` 的中文摘要 477 字，
    照旧写法量出来是英文摘要的长度，长度门整个失灵。
    """
    order = ("Abstract", "摘要") if _is_english(paper) else ("摘要", "Abstract")
    body = next((b for k in order if (b := _section_body(paper, k)).strip()), "")
    return re.sub(r"^\s*\*{0,2}\s*(?:关\s*键\s*词|Key\s*words?)\s*\*{0,2}\s*[：:].*$",
                  "", body, flags=re.M | re.I)


def _abstract_statistics(paper: str) -> list[str]:
    """摘要里的统计量。**留效应量、去统计量**——判据的两半，另一半是 `_abstract_numbers`。"""
    hits = [m.group(0) for m in _ABSTRACT_STATS.finditer(_abstract_body(paper))]
    if not hits:
        return []
    return [Finding(f"摘要里有 {len(hits)} 处统计量（{'、'.join(dict.fromkeys(hits))[:60]}）——"
                    f"期刊摘要不写标准误、p 值、最小可探测效应和样本量（实测 59 篇已发表的中文摘要，"
                    f"一篇都没有）。**留效应量、去统计量**：把系数说成读者能懂的量"
                    f"（「概率低 X 个百分点」），这些留给正文和表",
                    "paper.abstract_statistics")]


#: 直角引号。横排简体的引号是 `“”`，`「」` 是竖排与港台的用法（GB/T 15834）。
_CORNER = re.compile(r"[「」『』]")


def _corner_quotes(paper: str) -> list[Finding]:
    """中文稿里用了直角引号。**这不是口味问题。**

    GB/T 15834《标点符号用法》规定横排简体的引号是 `“”`（嵌套 `‘’`）；
    `「」` 是竖排与港台的用法。语料里 28 篇真发表的中文期刊论文，
    `“”` 用了 3355 处，`「」` **一处都没有**。

    三份 demo 的中文稿通篇是 `「」`（合计 340 处），一路走完九个环节、
    两轮跨模型审稿、导出 Word，**没有任何一道门看这个**——
    是用户打开成品翻到正文才发现的。
    """
    if _is_english(paper):
        return []
    hits = _CORNER.findall(_strip_code_and_quotes(paper))
    if not hits:
        return []
    return [Finding(
        f"正文用了 {len(hits)} 处直角引号（「」）——横排简体的引号是 “”，嵌套用 ‘’"
        f"（GB/T 15834）。「」是竖排与港台的用法：语料里 28 篇真发表的中文期刊论文，"
        f"“” 用了三千多处，「」一处都没有",
        "paper.corner_quotes")]


def _abstract_length(paper: str) -> list[str]:
    """中文摘要的字数。英文不判——没有等价的硬规范，风格包的 `abstract_max_words` 那条路在管。"""
    if _is_english(paper):
        return []
    from oss.core.style import count_words

    body = _abstract_body(paper).strip()
    if not body:
        return []
    n = count_words(_strip_code_and_quotes(body))
    if n <= ABSTRACT_MAX_ZH:
        return []
    return [Finding(f"中文摘要 {n} 字，超过 {ABSTRACT_MAX_ZH} 字（《中国社会科学》的上限；"
                    f"GB/T 7713.2 说报道性摘要 400 字左右）。已发表的中文摘要中位数是 230 字——"
                    f"先砍稳健性与局限那几句，它们是正文的事",
                    "paper.abstract_too_long")]


def _abstract_numbers(paper: str) -> list[str]:
    """摘要必须带具体数字。评审：「摘要没有任何具体数字（系数、MDE、样本量都没有）」。"""
    body = _abstract_body(paper)
    if not body.strip():
        return []                                       # 缺节由节检查报
    if re.search(r"\{\{(result|power|robust|bin):", body):
        return []
    return [Finding("摘要没有任何具体数字——至少引主效应的占位符（{{result:main.coef}}、{{result:main.n}}）；"
            "只说「显著」「有影响」的摘要会被桌拒", "paper.abstract_no_numbers")]


# ------------------------------------------------------------------ 表


#: 显著性档位（***, **, * 的上界）。**中英不同，这是体例差异不是口味**。
#: 英文按社会学通例 .05/.01/.001——`styles/social-forces/exemplars/` 里
#: Laurison & Friedman 2024 的表注原文是「∗ p < .05, ∗∗ p < .01, ∗∗∗ p < .001」，**没有 0.10 这一档**。
#: 中文沿用 0.10/0.05/0.01：国内期刊两种写法都常见，没有同等强度的证据支持改，
#: 不拿没证据的改动去动统计呈现。
_STAR_CUTS_EN = (0.001, 0.01, 0.05)
_STAR_CUTS_ZH = (0.01, 0.05, 0.10)


def _stars(p: float, en: bool = False) -> str:
    hi, mid, lo = _STAR_CUTS_EN if en else _STAR_CUTS_ZH
    return "***" if p < hi else "**" if p < mid else "*" if p < lo else ""


def _pfmt(p: float) -> str:
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _logit_note(en: bool) -> str:
    """表注里 0/1 结局那一句：比例区间按 logit 尺度（`analysis.mean_ci`）。分组表与水平、分布的主结果表共用。"""
    return (" Intervals for proportions of 0/1 outcomes are computed on the logit scale "
            "(as in Stata svy: proportion)." if en
            else " 0/1 结局的比例区间按 logit 尺度算（同 Stata svy: proportion）。")


def _mean_only(results: AnalysisResults) -> bool:
    """主结果是全样本均值（`describe` 的 level、distribution）：它的星号与 p 检验的是「均值 = 0」，没有意义，不打不报。

    认法照 `analysis.run_describe` 写主结果的约定：这两种的 `term` 就是结局本身；gap 是「列=值」，trend 是时间列。
    只看 describe——别的估计量一律 False，输出一个字不变。
    """
    sp, m = results.spec, results.estimates.get("main")
    return sp is not None and sp.estimator == "describe" and m is not None and m.term == sp.outcome


def _mean_ci(results: AnalysisResults, e) -> str:
    """水平、分布的主结果（全样本均值）的 95% 区间。分组表只有一格（没分组，或分组列只剩一个取值）时那一格就是全样本：
    照抄它的区间，两张表一个数不差。分了组时全样本不是一格，照分组表的口径现算（`analysis.mean_ci`）：
    临界值取 `spec.t_crit`（没按设计为 1.96），0/1 结局按 logit 尺度。"""
    cells = results.groups
    if len(cells) == 1 and cells[0].ci_lo is not None and cells[0].ci_hi is not None:
        lo, hi = cells[0].ci_lo, cells[0].ci_hi
    else:
        from oss.core.analysis import mean_ci
        lo, hi = mean_ci(e.coef, e.se, results.spec.t_crit or 1.96, proportion=any(c.proportion for c in cells))
    return f"[{lo:.3f}, {hi:.3f}]"


def _robust_mean_ci(rb) -> str:
    """稳健性那一项（水平、分布）的均值区间：临界值取这一项自己的 `t_crit`（没按设计为 1.96），
    0/1 结局按 logit 尺度（看这一项的诊断 `analysis.PROPORTION_DIAG`——换了结局的那一项不一定还是 0/1）。"""
    from oss.core.analysis import PROPORTION_DIAG, mean_ci
    e = rb.estimates["main"]
    lo, hi = mean_ci(e.coef, e.se, rb.t_crit or 1.96, proportion=PROPORTION_DIAG in rb.diagnostics)
    return f"[{lo:.3f}, {hi:.3f}]"


def _mean_note(en: bool) -> str:
    """水平、分布的主表与稳健性表（`_mean_only`）的表注：估计值是全样本均值，列区间，不做「均值 = 0」的检验。"""
    return (" Estimates are full-sample means with their 95% confidence intervals; no test against zero is reported."
            if en else " 估计值是全样本均值，列 95% 区间，不做「均值 = 0」的检验。")


def _mean_only_why(field: str, what: str, cite: str, where: str) -> str:
    """水平、分布（`_mean_only`）的均值不报 p 与 MDE：占位符解析不了的理由。`cite` 是该引的均值，`where` 是区间在哪。"""
    test = "p 检验的是「均值 = 0」" if field == "p" else "MDE 是跟 0 比能分辨多小"
    return f"水平、分布的{what}是全样本均值，它的 {test}，没有意义，不报——写均值 {cite}；{where}"


#: 描述论文为什么拿分组描述表当主表。论文检查的警告（`check`）、合规规则 tables（`skillcheck.r_tables`）、
#: 导出（`oss/api/paper.py`）三处拦「没有分组表」时都说这一句，不各写一套
GROUPS_MAIN_TABLE = ("描述论文的主表是分组描述表 {{table:groups}}：{{table:main}} 只有主结果那一行"
                     "（水平、分布是全样本均值与区间，组差、趋势是那一个差或斜率），各组的均值与区间只在分组表里")


def _num(x: float, d: int = 3) -> str:
    if x == int(x) and abs(x) < 1e12:
        return f"{int(x)}"
    return f"{x:.{d}f}"


#: 交互项在表里的连接符。印「管理与专业阶级 × 调查年份」，不是「cls_mgr*decade」。
TIMES = " × "


def usable_labels(labels: dict[str, str] | None, en: bool) -> dict[str, str]:
    """英文稿里把**中文标签**筛掉，退回原列名。

    绑定记录里的构念名跟着数据的语言走：CGSS 那种跑法，binding.json 是中文的，
    而论文可能是英文的。不筛的话中文会顺着标签进英文表，撞上「英文稿的表不能有中文」
    那道硬门——**整篇稿子导不出来**（真跑上出过：`ossrun-a` 是英文稿、中文绑定）。
    筛掉不是把问题藏起来：这些列会落回 `unlabeled_terms`，`oss paper check` 照样报
    「表里会印出原始列名」，作者补一份英文的 `plan.labels` 就好。
    """
    if not labels or not en:
        return labels or {}
    return {k: v for k, v in labels.items() if not _CJK_CHARS.search(v)}


def term_label(term: str, labels: dict[str, str] | None) -> str:
    """列名 → 表和图里印的名字。

    三件事：交互项 `a*b` 拆开分别解析、拼成「A × B」；类别哑变量 `col=值` 只换列名那一半，
    取值留着（`edu_long=1` → 「受教育年限=1」）；**查不到就原样返回**——
    猜错了比印列名更糟（`cls_mgr` 猜「管理」还是「经理」？）。

    标签哪来的见 `binding.display_labels`：plan.labels 压过 binding 的构念名。
    """
    if not labels:
        return term
    if (whole := labels.get(term)):
        return whole            # 整个交互项自己起了名字，就用它，不再拆
    return TIMES.join(_one_label(p, labels) for p in _term_parts(term))


def _one_label(part: str, labels: dict[str, str]) -> str:
    if (hit := labels.get(part)):
        return hit
    col, sep, value = part.partition("=")
    if sep and (m := _CODED_VALUE.fullmatch(value)):
        return m.group(1).strip()      # `urban_hukou3=2 城市非农户口` → 「城市非农户口」，见 `group_label`
    if sep and (hit := labels.get(col)):
        return f"{hit}={value}"
    return part


#: 分组取值「编码 + 空格 + 文字」，如 `1 农村居民`：数据里带着取值标签
_CODED_VALUE = re.compile(r"\s*-?\d+(?:\.\d+)?\s+(\S.*)")
_NUMBER = re.compile(r"\s*-?\d+(?:\.\d+)?\s*")


def group_label(label: str, labels: dict[str, str] | None) -> str:
    """分组描述表里一格的名字。describe 把格名拼成 `列=取值`（多个分组列用 × 连），原样印就是
    `urban_hukou3=1 农村居民`。

    取值自己带文字的（`1 农村居民`、`female`），只印文字；纯编码的（`cohort=2`）换列名那一半，
    跟 `term_label` 一样；查不到标签就原样返回。
    """
    labels = labels or {}
    out = []
    for part in label.split(TIMES):
        col, sep, value = part.partition("=")
        if not sep:
            out.append(part)
        elif (m := _CODED_VALUE.fullmatch(value)):
            out.append(m.group(1).strip())
        elif value.strip() and not _NUMBER.fullmatch(value):
            out.append(value.strip())
        else:
            out.append(f"{labels[col]}={value}" if labels.get(col) else part)
    return TIMES.join(out)


def _term_parts(term: str) -> list[str]:
    from oss.core.analysis import parts_of

    return parts_of(term)


def unlabeled_terms(results, labels: dict[str, str] | None) -> list[str]:
    """表里要印、却一个标签都查不到的列名（估计项与描述统计行）。`oss paper check` 拿它报警告。"""
    want: list[str] = []
    for e in results.estimates.values():
        want.extend(_term_parts(e.term))
    want.extend(d.name for d in results.descriptives)
    # **固定效应那一行不计**：它是元数据不是变量行，而且 country / year / province
    # 本来就是人话。有标签照样会套上（见 table_markdown），只是不为它报警告
    out: list[str] = []
    for c in want:
        # `col=值` 的哑变量：列名那一半有标签就算有——取值不需要单独填
        if c and c not in out and _one_label(c, labels or {}) == c:
            out.append(c)
    return out


def _cell(text: str) -> str:
    """表格单元格：管道符和星号会破坏 Markdown 表，转义掉。"""
    return str(text).replace("|", "/").replace("*", "\\*")


_ESTIMATOR_EN = {
    "ols_fe": "OLS with fixed effects",
    "did": "Two-way fixed-effects difference-in-differences",
    "did_cs": "Callaway–Sant'Anna (2021) staggered difference-in-differences",
    "event_study": "Event study (period −1 omitted)",
    "iv": "Two-stage least squares",
    "rdd": "Local linear regression discontinuity",
    "describe": "Group means and proportions",
}
_ESTIMATOR_ZH = {
    "ols_fe": "OLS + 固定效应", "did": "双向固定效应双重差分", "did_cs": "Callaway–Sant'Anna 分批双重差分",
    "event_study": "逐年展开（第 −1 期为参照）", "iv": "两阶段最小二乘", "rdd": "局部线性断点回归",
    "describe": "分组均值与比例",
}


def _notes(results: AnalysisResults, en: bool, spec=None, stars: bool = True) -> str:
    """表注：估计量、固定效应、聚类、样本筛选、星号约定。**从规格摘要生成，不手写。**

    `spec` 缺省用主规格；各批效应表与逐期表传伴随估计的（`results.companion_spec`）——
    那两张表的数出自伴随的 Callaway–Sant'Anna，标准误口径跟主规格不同。
    `stars=False`：表里没打星号（水平、分布的主结果表，`_mean_only`），不写星号约定。
    """
    sp = spec if spec is not None else results.spec
    bits: list[str] = []
    if sp is not None:
        if en:
            bits.append(_ESTIMATOR_EN.get(sp.estimator, sp.estimator) + ".")
            if sp.fixed_effects:
                bits.append("Fixed effects: " + ", ".join(sp.fixed_effects) + ".")
            if sp.psu and sp.estimator == "did_cs":
                # 分批双重差分走 StatsPAI：只按初级抽样单位聚类，分层进不了方差——照实说，不说「按抽样设计」
                bits.append(f"Standard errors clustered by PSU {sp.psu}"
                            + (f"; strata ({sp.strata}) not reflected." if sp.strata else "."))
            elif sp.psu:
                bits.append(f"Standard errors account for the sampling design (strata: {sp.strata or 'none'}; "
                            f"PSUs: {sp.psu}; Taylor linearization).")
            else:
                bits.append(f"Standard errors clustered by {sp.cluster_level}." if sp.cluster_level
                            else "Heteroskedasticity-robust standard errors.")
            if sp.weights:
                bits.append(f"Estimates are weighted by {sp.weights}.")
            if sp.sample_filter:
                bits.append(f"Sample restriction: `{sp.sample_filter}`.")
        else:
            bits.append(_ESTIMATOR_ZH.get(sp.estimator, sp.estimator) + "。")
            if sp.fixed_effects:
                bits.append("固定效应：" + "、".join(sp.fixed_effects) + "。")
            if sp.psu and sp.estimator == "did_cs":
                bits.append(f"标准误按初级抽样单位 {sp.psu} 聚类"
                            + (f"（分层 {sp.strata} 没进方差）。" if sp.strata else "。"))
            elif sp.psu:
                bits.append(f"标准误按抽样设计算（{'分层 ' + sp.strata if sp.strata else '未分层'}，"
                            f"初级抽样单位 {sp.psu}，泰勒线性化）。")
            else:
                bits.append(f"标准误按 {sp.cluster_level} 聚类。" if sp.cluster_level else "异方差稳健标准误。")
            if sp.weights:
                bits.append(f"按 {sp.weights} 加权。")
            if sp.sample_filter:
                bits.append(f"样本筛选：`{sp.sample_filter}`。")
    # 用什么处理的：**照用户原话，说多少写多少**。他说「Stata」就是 Stata，
    # 不许补成「Stata 18」——补全就是编造。没说就一个字不写。
    # 这是正常的方法交代（任何论文都写），**不是**关于本系统的话——
    # 后者是过程泄漏，`no-process-language` 会拦。
    soft = (getattr(results.imported, "software", "") or "").strip()
    if soft:
        bits.append(f"Estimates produced in {soft}." if en else f"估计在 {soft} 中完成。")
    if stars:
        # 英文图例照范文原文：不带前导零、没有 0.10 那一档。**正文里的 p 值仍带前导零**
        # （`_pfmt`）——范文实测是带零的更多（10 比 3），图例和正文本来就不是一套写法
        bits.append("\\* p < .05, \\*\\* p < .01, \\*\\*\\* p < .001." if en
                    else "\\* p < 0.10，\\*\\* p < 0.05，\\*\\*\\* p < 0.01。")
    # 范文的表注不论几句都是单数 Note:（6 篇里 14 处 Note:、0 处 Notes:）
    return ("*Note:* " if en else "*注：*") + " ".join(bits)


def _table(header: list[str], rows: list[list[str]], align_first_left: bool = True,
           left_cols: tuple[int, ...] = ()) -> str:
    """数字列右对齐、文字列左对齐。`left_cols` 是额外要左对齐的列号（0 起）——
    稳健性表的「结局」那一列装的是变量名，跟着数字右对齐会很别扭。"""
    sep = ["---"] + ["---:"] * (len(header) - 1) if align_first_left else ["---:"] * len(header)
    for i in left_cols:
        if 0 <= i < len(sep):
            sep[i] = "---"
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(sep) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _se_below(head: list[str], rows: list[list[str]], apply: bool) -> tuple[list[str], list[list[str]]]:
    """英文系数表的通例：系数带星号占一列，**标准误在下一行同列的括号里**，不设 p 列。

    Social Forces 的范文就是这个样子（`styles/social-forces/exemplars/`，
    Laurison & Friedman 2024 表 3）：

        Routine/Manual    -19717.84 ***
                             (4605.38)

    做成**对已建好的行的变换**，而不是改每个分支的行构造——主表、稳健性表各有
    「换了结局」「跑不动」「只报均值」几个分支，列数都不一样，逐个改必然漏一个。
    估计值那一列固定在 `SE` 的前一列（四张系数表都是这个次序）。
    """
    if not apply or "SE" not in head:
        return head, rows
    se = head.index("SE")
    drop = {se} | ({head.index("p")} if "p" in head else set())
    keep = [i for i in range(len(head)) if i not in drop]
    at = keep.index(se - 1)
    out: list[list[str]] = []
    for r in rows:
        out.append([r[i] if i < len(r) else "" for i in keep])
        val = r[se] if se < len(r) else ""
        if val.strip():                     # 跑不动的那一行没有标准误，不补空括号
            below = [""] * len(keep)
            below[at] = f"({val})"
            out.append(below)
    return [head[i] for i in keep], out


def table_markdown(name: str, results: AnalysisResults, *, en: bool = True,
                   labels: dict[str, str] | None = None) -> tuple[str | None, str]:
    """`{{table:<name>}}` → Markdown 表（pandoc 转成 Word 表）。返回 `(表, 出错原因)`。

    数字只来自结果文件；表注从规格摘要生成。英文稿里表内出现中文就拒绝——
    多半是稳健性标签用中文写了（plan.json 的 label 要英文）。
    """
    labels = usable_labels(labels, en)
    if name == "main":
        if not results.estimates:
            return None, "结果里没有任何估计，主结果表排不出来"
        # 水平、分布的主结果是全样本均值：星号与 p 检验的是「均值 = 0」，没有意义——最后一列换成 95% 区间
        mean_only = _mean_only(results)
        head = (["Variable", "Estimate", "SE", "p"] if en else ["变量", "估计值", "标准误", "p"])
        if mean_only:
            head[-1] = "95% CI" if en else "95% 区间"
        rows = []
        # 各批效应（cohort_<年>）有自己的表 {{table:cohorts}}，不混进主结果表
        order = ["main"] + [k for k in results.estimates if k != "main" and not k.startswith("cohort_")]
        for k in order:
            e = results.estimates[k]
            term = term_label(e.term, labels)
            term = _englishize(term) if en else term
            if mean_only:                   # describe 只有 main 一个估计
                rows.append([_cell(term), f"{e.coef:.3f}", f"{e.se:.3f}", _mean_ci(results, e)])
            else:
                rows.append([_cell(term), f"{e.coef:.3f}{_stars(e.p, en)}", f"{e.se:.3f}", _pfmt(e.p)])
        rows.append([("N" if en else "样本量"), f"{results.n}", "", ""])
        if results.spec and results.spec.fixed_effects:
            rows.append([("Fixed effects" if en else "固定效应"),
                         _cell(", ".join(term_label(f, labels)
                                         for f in results.spec.fixed_effects)), "", ""])
        head, rows = _se_below(head, rows, en and not mean_only)
        body = _table(head, rows) + "\n\n" + _notes(results, en, stars=not mean_only)
        if mean_only:
            body += _mean_note(en)
            if any(c.proportion for c in results.groups):
                body += _logit_note(en)
    elif name == "robustness":
        if not results.robustness:
            return None, "结果里没有稳健性检验（plan.json 的 robustness 为空），稳健性表排不出来"
        # 换了结局的那几项跟主结果**不是同一个量**，单位都不同（生育率 vs 入学率百分点）。
        # 同一列里放不同单位的数，表本身就在误导：主规格 0.020 与「中学毛入学率 1.171」
        # 并排，读者会以为估计爆了。有这种项才多一列「结局」——没有就一列都不多。
        main_out = str(getattr(results.spec, "outcome", "") or "").strip()
        changed = {lb: str(rb.outcome or "").strip() for lb, rb in results.robustness.items()
                   if str(rb.outcome or "").strip() and str(rb.outcome or "").strip() != main_out}
        # 「结局」那一列印的也是列名，一并换成标签
        shown_out = {c: term_label(c, labels) for c in [main_out, *changed.values()] if c}
        # **每一项都要报自己的 MDE。**三位审稿人独立抓到同一件事：主结果反复交代
        # 「能分辨多小的差异」，稳健性只给系数和 p，读者无从判断那个「零」有多少
        # 只是分辨力不够。验收跑里主规格能分辨 0.196，三项换结局的分别是 4.44、
        # 8.08、7.79——宽了几十倍。不报的话，「五项都不显著」读起来像五重确认。
        # 水平、分布（`_mean_only`）：每一行都是全样本均值，星号、p 与 MDE 都是对「均值 = 0」说的——
        # p 与 MDE 两栏换成 95% 区间（各行用自己的临界值），表注不写星号约定，也不要 MDE 那一段
        mean_only = _mean_only(results)
        head = (["Specification", "Estimate", "SE", "p", "MDE", "N"] if en
                else ["规格", "估计值", "标准误", "p", "MDE", "样本量"])
        if mean_only:
            head[3:5] = ["95% CI" if en else "95% 区间"]
        if changed:
            head.insert(1, "Outcome" if en else "结局")
        rows = []
        m = results.estimates.get("main")
        if m is not None:
            row = [("Main specification" if en else "主规格"),
                   f"{m.coef:.3f}{_stars(m.p, en)}", f"{m.se:.3f}", _pfmt(m.p),
                   f"{m.mde:.3f}", f"{results.n}"]
            if mean_only:
                row[1:5] = [f"{m.coef:.3f}", f"{m.se:.3f}", _mean_ci(results, m)]
            if changed:
                row.insert(1, _cell(shown_out.get(main_out, main_out)) or "—")
            rows.append(row)
        for label, rb in results.robustness.items():
            e = rb.estimates.get("main")
            if rb.error or e is None:
                row = [_cell(label), ("— (not estimated)" if en else "—（没跑出来）"), "", "", "", ""]
                if mean_only:
                    row.pop()
            else:
                row = [_cell(label), f"{e.coef:.3f}{_stars(e.p, en)}", f"{e.se:.3f}", _pfmt(e.p),
                       f"{e.mde:.3f}", f"{rb.n}"]
                if mean_only:
                    row[1:5] = [f"{e.coef:.3f}", f"{e.se:.3f}", _robust_mean_ci(rb)]
            if changed:
                out_col = changed.get(label, main_out)
                row.insert(1, _cell(shown_out.get(out_col, out_col)) or "—")
            rows.append(row)
        head, rows = _se_below(head, rows, en and not mean_only)
        body = (_table(head, rows, left_cols=(1,) if changed else ()) + "\n\n"
                + _notes(results, en, stars=not mean_only))
        if mean_only:
            from oss.core.analysis import PROPORTION_DIAG
            body += _mean_note(en)
            if any(c.proportion for c in results.groups) or any(
                    PROPORTION_DIAG in rb.diagnostics for rb in results.robustness.values()):
                body += _logit_note(en)
        else:
            body += ("\n\nMDE = the smallest difference this specification could detect "
                     "(two-sided, 80% power). A coefficient near zero with a wide MDE means "
                     "the check could not resolve an effect, not that none exists."
                     if en else
                     "\n\nMDE = 该规格**能分辨的最小差异**（双侧、80% 功效）。"
                     "系数贴近零而 MDE 很宽，说明这一项**测不出**效应，不等于效应不存在。")
        if changed:
            body += ("\n\n" + (
                f"Note: {'One check replaces' if len(changed) == 1 else str(len(changed)) + ' checks replace'} "
                f"the outcome variable; their units differ from the main estimate and the "
                f"coefficients are not directly comparable."
                if en else
                f"注：其中 {len(changed)} 项换了结局变量，**单位与主结果不同，系数不可直接比较**。"))
    elif name == "descriptives":
        if not results.descriptives:
            return None, "结果文件里没有描述统计——重跑 oss run（老版本的结果文件没有这一项）"
        head = (["Variable", "N", "Mean", "SD", "Min", "Max"] if en else ["变量", "N", "均值", "标准差", "最小", "最大"])
        rows = [[_cell(term_label(d.name, labels)), f"{d.n}",
                 _num(d.mean), _num(d.sd), _num(d.min), _num(d.max)]
                for d in results.descriptives]
        note = ("*Note:* Estimation sample (after sample restrictions and listwise deletion of missing values)."
                if en else "*注：*估计样本（样本筛选并去掉缺失之后）。")
        if any(d.weighted for d in results.descriptives):
            # 表 1 跟主规格同一口径加权（`analysis._descriptives`）；N 照报未加权的行数
            wcol = results.spec.weights if results.spec is not None else ""
            if en:
                note += f" Means and SDs are weighted{' by ' + wcol if wcol else ''}; N is the unweighted count."
            else:
                note += f"均值与标准差{'按 ' + wcol + ' ' if wcol else ''}加权；N 为未加权的行数。"
        body = _table(head, rows) + "\n\n" + note
    elif name == "sample":
        if not results.sample_log:
            return None, "结果文件里没有样本构建日志——重跑 oss run（老版本的结果文件没有这一项）"
        head = (["Step", "Rows", "Units", "Units dropped"] if en else ["步骤", "行数", "单位数", "掉出去的单位"])
        names = {"原始": "Raw data", "估计样本": "Estimation sample",
                 "去掉回归变量缺失的行": "Drop rows with missing regression variables"}
        rows = []
        for st in results.sample_log:
            step = st.step
            if en:
                step = names.get(step, step.replace("样本筛选", "Sample restriction"))
            gone = ", ".join(st.dropped_units[:12]) + ("…" if len(st.dropped_units) > 12 else "")
            rows.append([_cell(step), f"{st.rows:,}", f"{st.units}" if st.units else "", _cell(gone)])
        note = ("*Note:* Units listed were dropped entirely at that step."
                if en else "*注：*列出的是在这一步整个掉出去的单位。")
        body = _table(head, rows) + "\n\n" + note
    elif name == "groups":
        # 分组描述（describe 估计量）：各组均值与区间、对参照组的差；分布另给分位数。区间与 p 按计划的权重与抽样设计算
        if not results.groups and not results.quantiles:
            return None, "结果里没有分组描述——估计量是 describe 才有"
        parts: list[str] = []
        if results.groups:
            # 表头按各格实际装的量写：计划写了 statistic: median 就是中位数，不能叫「均值」
            med = all(c.statistic == "median" for c in results.groups)
            head = (["Group", "N", "Median" if med else "Mean", "95% CI", "Difference from ref.", "p"] if en
                    else ["组", "N", "中位数" if med else "均值", "95% 区间", "与参照组之差", "p"])
            rows = []
            multi = len(results.groups) > 1
            for c in results.groups:
                label = "Full sample" if en and c.label == "全样本" else group_label(c.label, labels)
                if c.reference:
                    diff = p = "ref." if en else "参照"
                elif c.gap is None:
                    diff = p = "—" if multi else ""      # 多格时空着是算不出来（整格落在一个抽样单位里）
                else:
                    diff = f"{c.gap:.3f}{_stars(c.gap_p, en) if c.gap_p is not None else ''}"
                    p = _pfmt(c.gap_p) if c.gap_p is not None else ""
                ci = f"[{c.ci_lo:.3f}, {c.ci_hi:.3f}]" if c.ci_lo is not None and c.ci_hi is not None else "—"
                rows.append([_cell(label), f"{c.n}", f"{c.mean:.3f}", ci, diff, p])
            parts.append(_table(head, rows))
        if results.quantiles:
            parts.append(_table(["Quantile", "Value"] if en else ["分位", "值"],
                                [[k, f"{v:.3f}"] for k, v in results.quantiles.items()]))
        note = _notes(results, en, results.spec)
        if any(c.reference for c in results.groups):       # 只有一格「全样本」时没有参照组，不写这句
            note += (" Differences are relative to the first group in sorted order." if en
                     else " 组间差以排序后的第一组为参照。")
        if any(c.proportion for c in results.groups):
            note += _logit_note(en)
        if any(c.se is None for c in results.groups):
            # 「—」是算不出来：整格只落在一个方差单位里（`analysis._variance_units`）。单位按主规格的标准误口径说
            sp = results.spec
            where_zh, where_en = (("整格落在一个初级抽样单位里", "lies within a single PSU") if sp is not None and sp.psu
                                  else ("整格落在一个聚类单位里", "lies within a single cluster")
                                  if sp is not None and sp.cluster_level
                                  else ("整格只有一个观测", "has a single observation"))
            ref_blank = any(c.reference and c.se is None for c in results.groups)
            note += (f" —: the group {where_en}, so no interval or difference can be computed"
                     + ("; the reference group is among them, so all differences are blank." if ref_blank else ".")
                     if en else
                     f" 「—」：该组{where_zh}，区间与组间差算不出来"
                     + ("；参照组也在其中，各组对它的差都留空。" if ref_blank else "。"))
        body = "\n\n".join(parts) + "\n\n" + note
    elif name == "cohorts":
        keys = [k for k in results.estimates if k.startswith("cohort_")]
        if not keys:
            return None, "结果里没有按批的效应（只有 did_cs 估计量产出 cohort_<年>）"
        head = (["Cohort", "ATT", "SE", "p"] if en else ["批次", "ATT", "标准误", "p"])
        rows = [[k[len("cohort_"):], f"{results.estimates[k].coef:.3f}{_stars(results.estimates[k].p, en)}",
                 f"{results.estimates[k].se:.3f}", _pfmt(results.estimates[k].p)] for k in keys]
        head, rows = _se_below(head, rows, en)
        body = _table(head, rows) + "\n\n" + _notes(results, en, results.companion_spec)
    elif name == "event":
        if not results.event_study:
            return None, "结果里没有逐期效应，事件研究表排不出来"
        head = (["Period", "Estimate", "SE", "N treated"] if en else ["相对期", "估计值", "标准误", "处理观测数"])
        rows = [[f"{pt.k:+d}", f"{pt.coef:.3f}{_stars(_p_from_t(pt.coef, pt.se), en)}", f"{pt.se:.3f}", f"{pt.n}"]
                for pt in sorted(results.event_study, key=lambda x: x.k)]
        head, rows = _se_below(head, rows, en)
        body = _table(head, rows) + "\n\n" + _notes(results, en, results.companion_spec)
    else:
        return None, f"没有叫「{name}」的表。可用：descriptives、main、robustness、sample、groups、cohorts、event"

    if en and (m := re.search(r"[^|\n]*[\u4e00-\u9fff][^|\n]*", body)):
        if name == "groups":
            # 分组表的组名是数据里分组列的取值（`列=值`）：中文取值要在数据里重编码，改 plan.json 没用
            return None, (f"表「groups」里有中文单元格「{m.group(0).strip()[:40]}」——英文稿的表不能有中文。"
                          f"组名来自数据里分组列的取值（或列名）：在 prep.py 里把分组列重编码成英文"
                          f"（例如 城镇 → urban、农村 → rural），重跑 oss run")
        return None, (f"表「{name}」里有中文单元格「{m.group(0).strip()[:40]}」——英文稿的表不能有中文。"
                      f"多半是稳健性标签或变量名：plan.json 的 label 用英文写，重跑 oss run")
    return body, ""


_ATX = re.compile(r"#{1,6}\s")

#: 关键词那一行。中英、有没有星号、全角半角冒号都认。
_KEYWORDS_LINE = re.compile(r"^\s*\*{0,2}\s*(?:关\s*键\s*词|Key\s*words?)\s*\*{0,2}\s*[：:]", re.I)


def _run_in_headings(lines: list[str]) -> list[int]:
    """上一行还是正文的标题行号（0 起）。**pandoc 认不出这种标题。**

    pandoc 的 markdown 默认开 `blank_before_header`：紧贴在段落下面的 `## X` 会被并进
    上一段，Word 里就出现一行裸露的「## Abstract」——十一轮 CGSS 那篇成稿上出过。
    而门这边（`_section_body` 等）用 `re.M` 按行首认标题，**照样认得出**，
    于是稿子带着这个毛病一路走到 Word 也没人拦。
    """
    out: list[int] = []
    fenced = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or i == 0 or not _ATX.match(line):
            continue
        prev = lines[i - 1]
        if prev.strip() and not _ATX.match(prev):
            out.append(i)
    return out


def normalize_headings(md: str) -> str:
    """给紧贴在正文后面的标题补一个空行。空白是机械可补的，**补掉比拒掉对**。"""
    lines = md.split("\n")
    for i in reversed(_run_in_headings(lines)):
        lines.insert(i, "")
    return "\n".join(lines)


def heading_run_ins(paper: str) -> list[tuple[int, str]]:
    """pandoc 认不出的标题：(行号, 标题行)。导出时会自动补，源稿里也该补。"""
    lines = paper.split("\n")
    return [(i + 1, lines[i].strip()) for i in _run_in_headings(lines)]


def _trim(lines: list[str]) -> list[str]:
    """掐掉首尾空行，**中间的留着**——摘要可能不止一段。"""
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _wrap(lines: list[str], style: str) -> list[str]:
    """包进 pandoc 的 custom-style 围栏 div——docx 专用的样式挂靠方式，
    里面的段落在 Word 里挂到同名样式上。模板里的档位（`oss/api/paper.py`）靠它生效。"""
    return ['::: {custom-style="' + style + '"}', *lines, ":::"]


def style_abstract(md: str) -> str:
    """给摘要与关键词挂上各自的 Word 样式档。中文稿有两处摘要（文首中文、文末英文），两处都挂。"""
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        if not (_ATX.match(line) and re.search(r"摘\s*要|Abstract", line, re.I)):
            continue
        body: list[str] = []
        while i < len(lines) and not _ATX.match(lines[i]):
            body.append(lines[i])
            i += 1
        kw = next((j for j, l in enumerate(body) if _KEYWORDS_LINE.match(l)), None)
        head, rest = (body, []) if kw is None else (body[:kw], body[kw:])
        for part, style in ((head, "Abstract"), (rest, "Keywords")):
            if any(l.strip() for l in part):
                out += ["", *_wrap(_trim(part), style), ""]
    return "\n".join(out)


def _pull_table_captions(paper: str) -> tuple[str, dict[str, str]]:
    """把 `{{table:x}}` 上面那行表题摘下来，交给 `_caption_into_table` 换个位置放回去。

    表题原先是一行斜体正文（`*表 1. 描述统计。*`），Word 里落成带首行缩进的普通段落，
    不居中、不进题注样式。pandoc 有表题语法，见 `_caption_into_table`。
    """
    lines = paper.split("\n")
    caps: dict[str, str] = {}
    drop: set[int] = set()
    for i, line in enumerate(lines):
        m = re.search(r"\{\{table:([^}]+)\}\}", line)
        if not m:
            continue
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j >= 0 and j not in drop and _CAPTION_TAB.match(lines[j].strip()):
            caps[m.group(1).strip()] = lines[j].strip().strip("*").strip()
            drop.add(j)
    return "\n".join(l for k, l in enumerate(lines) if k not in drop), caps


def _caption_into_table(md: str, caption: str) -> str:
    """表题插到表格块之后（pandoc 的 `: 表 1. …` 语法，docx 里排到表格**上方**），
    表注包进自己的档。

    插入点是**第一个表格块的末行**，不是整段末尾——分组表有两张表（组均值 + 分位数），
    插到末尾会挂到第二张上。
    """
    lines = md.split("\n")
    end = 0
    for i, line in enumerate(lines):
        if line.startswith("|"):
            end = i
        elif end:
            break
    head, tail = lines[:end + 1], lines[end + 1:]
    # 前缀写 "*Note" 不写 "*Note:"——单复数都认。表注起头改过一次（Notes: → Note:），
    # 这里没跟上就会让英文表注**静默**丢掉样式档，没有任何报错
    note = next((i for i, l in enumerate(tail) if l.lstrip().startswith(("*注：", "*Note"))), None)
    if note is not None:
        tail = tail[:note] + _wrap(_trim(tail[note:]), "Table Note")
    return "\n".join(head + (["", f": {caption}"] if caption else []) + tail)


def fold_captions(rendered: str) -> str:
    """给人看的渲染稿里，图片替代文字 = 图注，图注行也留着。导出 Word 前把图注行并掉——
    pandoc 会把替代文字排成题注，再留着图注行 Word 里就出现两遍。"""
    return re.sub(r"(!\[[^\]]+\]\([^)]+\))[ \t]*\n+\*?\s*(?:Figure|Fig\.|图)\s*\d+[^\n]*",
                  r"\1", rendered)


def _p_from_t(coef: float, se: float) -> float:
    if se <= 0:
        return 1.0
    from math import erf, sqrt
    z = abs(coef / se)
    return float(2 * (1 - 0.5 * (1 + erf(z / sqrt(2)))))


def render(paper: str, results: AnalysisResults, power=None, corpus=None,
           figures: set[str] | None = None, cite_mode: str = "text",
           fig_captions: str = "keep", numeric_citations: bool = False,
           labels: dict[str, str] | None = None) -> str:
    """把占位符替换成真实数字。

    **解析不了就抛，绝不静默留空。**留空的话正文会出现「效应为  （标准误 ）」
    这种半截句子，而它看起来像排版问题，不像数据缺失。

    `cite_mode="pandoc"`：引用不渲染成文字，渲染成 pandoc 的引用键（`@ref_x` / `[@ref_x]`），
    参考文献留一个 `{#refs}` 容器——导出 Word 时交给 citeproc 按目标期刊的样式排。
    给人看、给审稿人看的仍是 text 版。

    图片的替代文字 = 图注。pandoc 把「单独成段、有替代文字的图片」排成带题注的图，
    题注就是替代文字——验收跑的 Word 里每张图下面因此是一个裸露的 "coef"。
    `fig_captions="alt"`（导出 Word 用）把图注行并进替代文字，Word 里只出现一次；
    `"keep"`（给人看的 markdown）图注行留在原处。
    """
    missing: list[str] = []

    en = _is_english(paper)
    pandoc = cite_mode == "pandoc"
    captions: dict[str, str] = {}
    for m in re.finditer(r"\{\{fig:([^}]+)\}\}[ \t]*\n+([^\n]+)", paper):
        line = m.group(2).strip()
        if _CAPTION_FIG.match(line):
            captions[m.group(1).strip()] = line.strip("*").strip()
    if fig_captions == "alt":
        paper = re.sub(r"(\{\{fig:[^}]+\}\})[ \t]*\n+(\*?\s*(?:Figure|Fig\.|图)\s*\d+[^\n]*)",
                       r"\1", paper)
    # 表题换个位置放回去（见 `_caption_into_table`）。给人看的 md 不动：那边表题在表格上方读起来更顺
    table_caps: dict[str, str] = {}
    if pandoc:
        paper, table_caps = _pull_table_captions(paper)

    def sub(m: re.Match) -> str:
        source, path = m.group(1), m.group(2)
        if source == "fig":
            val, _ = _resolve_one(source, path, results, power, corpus, figures=figures,
                                  en=en, labels=labels)
            if val is None:
                missing.append(m.group(0))
                return m.group(0)
            name = path.strip()
            alt = captions.get(name, "").replace("]", ")").replace("[", "(")
            return f"![{alt}](fig_{name}.png)" if alt else val
        if source == "table":
            val, _ = _resolve_one(source, path, results, power, corpus, figures=figures,
                                  en=en, labels=labels)
            if val is None:
                missing.append(m.group(0))
                return m.group(0)
            # 表前后空行，pandoc 才认它是表
            return "\n" + _caption_into_table(val, table_caps.get(path.strip(), "")) + "\n"
        if pandoc and source in ("cite", "citep"):
            ids = _split_ids(path) if source == "citep" else [path]
            recs = [find_record(corpus, one) for one in ids]
            if any(r is None for r in recs) or not recs:
                missing.append(m.group(0))
                return m.group(0)
            keys = [cite_key(r.paper_id or r.doi) for r in recs]
            if source == "citep":
                return f"[{'; '.join('@' + k for k in keys)}]"
            # 叙述式。作者-年份体例下 `@key` 排成「Kudamatsu (2007)」，能当主语；
            # 编号体例（GB/T 顺序编码、Vancouver…）只排出数字，作者名整个丢掉，
            # 正文就成了「[1] 认为」——一个编号当主语，中文里不成句。
            # 所以编号体例下把作者名写进正文，编号用抑制作者的形式：`作者[-@key]`。
            if numeric_citations and (who := _cite_who(recs[0], en)):
                return f"{who}[-@{keys[0]}]"
            return f"@{keys[0]}"
        val, _ = _resolve_one(source, path, results, power, corpus, figures=figures,
                                  en=en, labels=labels)
        if val is None:
            missing.append(m.group(0))
            return m.group(0)
        return val

    out = PLACEHOLDER.sub(sub, paper)
    # 参考文献列表自带一个标题。验收跑的成稿里 {{references}} 紧跟在
    # 「Next steps」正文后面，条目直接接在段落下——成品里看不出这是参考文献。
    # 作者自己写了标题就不重复加。
    def _refs(m: re.Match) -> str:
        before = out[:m.start()].rstrip()
        last_line = before.rsplit("\n", 1)[-1] if before else ""
        has_heading = bool(re.match(r"^#{1,4}\s*.*(References|参考文献|Bibliography)", last_line, re.I))
        title = "" if has_heading else ("## References\n\n" if _is_english(paper) else "## 参考文献\n\n")
        if pandoc:
            return title + "::: {#refs}\n:::"
        return title + _reference_list(paper, corpus)

    out = REFERENCES.sub(_refs, out)
    if pandoc:
        out = style_abstract(out)
    # 两版都过：紧贴在正文后面的标题 pandoc 认不出来，会在 Word 里裸露成「## Abstract」
    out = normalize_headings(out)
    if missing:
        raise ManuscriptRejected([f"解析不了的占位符：{'、'.join(sorted(set(missing)))}"])
    return out


def _reference_list(paper: str, corpus) -> str:
    """**只列被引用过的**——引了一定在里面，没引的不许出现。

    没引却列进去是很常见的凑数手法；引了却漏掉则让读者查不到出处。
    自动生成把两个方向一起堵死。
    """
    if corpus is None:
        return "（没有语料，参考文献列表生成不了）"
    cited = _cited_ids(paper)
    recs = [r for r in corpus.records
            if (r.paper_id or "").lower() in cited or (r.doi or "").lower() in cited]
    en = _is_english(paper)
    if not recs:
        return "(No references cited.)" if en else "（本文没有引用任何文献）"
    lines = []
    for r in sorted(recs, key=lambda x: (_surname((x.authors or [""])[0]), x.year or 0)):
        doi = f" DOI: {r.doi}" if r.doi else ""
        if en:
            # 英文条目：作者姓在前、逗号分隔，接近 ASA/APA 的通用形态。
            # 期刊各有细则，那是阶段二润色的事——**阶段一先保证格式一致且可核**。
            # 非拉丁文字的条目跟 citeproc 那条路同一规矩：罗马化 + 英译
            who = ", ".join(_en_authors(r)) or NO_AUTHOR
            lines.append(f"- {who} ({r.year or 'n.d.'}). {_en_title(r)}.{doi}")
        else:
            who = "、".join(r.authors or []) or NO_AUTHOR
            lines.append(f"- {who}（{r.year or 'n.d.'}）。{r.title}.{doi}")
    return "\n".join(lines)


def placeholders_in(paper: str) -> list[str]:
    """列出全文用到的占位符。写作时用来自查。"""
    return sorted({m.group(0) for m in PLACEHOLDER.finditer(paper)})
