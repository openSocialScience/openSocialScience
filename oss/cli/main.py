"""oss 命令行入口。

阶段 0：所有计算类命令走 core/fake.py，返回格式正确但数值是假的产出。
每个阶段换掉一个假实现，其余不动（PLAN.md 四、七个阶段）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import typer

from oss.core import fake
from oss.core import analysis as analysis_mod
from oss.core import envfile
from oss.core import literature as lit_module
from oss.core import manuscript
from oss.core import power as power_mod
from oss.core import style as style_mod
from oss.core import route as route_mod
from oss.core.profiler import profile_file
from oss.core.contracts import (
    ARTIFACTS,
    AnalysisPlan,
    AnalysisResults,
    BindingReport,
    DataProfile,
    IdeaSet,
    LiteratureCorpus,
    PowerReport,
    ReadingCards,
    Screening,
    StyleRules,
    TraceEvent,
)
from oss.api import analysis as analysis_api
from oss.api import anchor as anchor_api
from oss.api import binding as binding_api
from oss.api import data as data_api
from oss.api import evals as evals_api
from oss.api import figure as figure_api
from oss.api import results_import as results_import_api
from oss.api import lit as lit_api
from oss.api import loop as loop_api
from oss.api import paper as paper_api
from oss.api import power as power_api
from oss.api import profile as profile_api
from oss.api import review as review_api
from oss.api import route as route_api
from oss.api import schema as schema_api
from oss.api import sim as sim_api
from oss.api import style as style_api
from oss.api import tables as tables_api
from oss.api.errors import ApiError
from oss.api.rundir import (
    emit as _api_emit,
    open_run as _api_open_run,
    require_run as _api_require_run,
)
from oss.cli import output as jout
from oss import hosts
from oss.hosts import checks as host_checks
from oss.hosts import doc as host_doc
from oss.hosts import project as host_project
from oss.host.base import RunDir
from oss.host.reviewer import (
    Reviewer,
    SameFamilyRefused,
)

def _load_env() -> None:
    """读 `.env`：cwd 项目本地 / 用户级 `~/.config/oss/.env` / 源码检出，
    按这个优先级（见 oss/core/envfile.py）。key 只在这里进环境，
    不出现在任何输出里。"""
    envfile.load_env()


_load_env()

app = typer.Typer(no_args_is_help=True, add_completion=False, help="社科定量研究工具箱")
lit_app = typer.Typer(no_args_is_help=True, help="文献检索与核验")
trace_app = typer.Typer(no_args_is_help=True, help="留痕")
app.add_typer(lit_app, name="lit")
app.add_typer(trace_app, name="trace")

DEFAULT_RUN = "runs/current"

#: 每条命令 `--json` 的帮助文字。**一句话说清形状**，模型不必去翻文档。
JSON_HELP = "只输出一行 JSON：{ok, summary, data, decision?, problems?}；退出码不变"

#: 工具侧已经换成真实现的环节。每个阶段完成时往里加一项——
#: 这样 `oss smoke` 的输出就是可执行的进度表，不靠人记。
REAL_STEPS = {"profile", "literature", "review", "binding", "power", "results", "route"}

#: 论文的校验与渲染是工具侧的（`oss paper check` / `render`），
#: 但正文本身由 skill 写——所以 paper 同时属于两侧。

#: skill 侧的产出。**它们在 `oss smoke` 里永远是假的**——
#: smoke 是纯 CLI，没有模型，造不出真的判断。
#: 要验证它们只能真跑一次 skill，见 docs/design/PLAN.md。
SKILL_WRITTEN = {"screening", "reading", "ideas", "plan", "paper"}


def _install_cmd() -> str:
    """给一条**在这个环境里真能跑**的安装命令。

    原先无脑打印 `pip install ...`——而本项目的 venv 里没有 pip（用 uv 建的），
    照着抄第一步就卡住，而 `.dta` 是社科数据的标准格式。
    （PLAN.md 九之一 ⑩，GSS 真跑时第一步就踩到）

    探测顺序：venv 自带 pip → uv → pipx → 兜底给 python -m pip。
    """
    venv = Path(sys.executable).parent
    if (venv / "pip").exists() or importlib.util.find_spec("pip"):
        return f"{sys.executable} -m pip install"
    if shutil.which("uv"):
        return f"uv pip install --python {sys.executable}"
    if shutil.which("pipx"):
        return "pipx inject opensocialscience"
    return f"{sys.executable} -m pip install"


def _code_provenance(pkg: Path | None = None, repo: Path | None = None) -> tuple[bool, str]:
    """**此刻跑的是哪份代码。**`doctor` 的第一条，因为它错了后面每一条都不算数。

    真跑一次时发现：PATH 上的 `oss` 加载的是几个月前一份非可编辑安装，
    分析模块 294 行（仓库里 1299 行）、一个因果估计量都没有。
    链条照跑、产出照出、`doctor` 全绿——**近半年的工作从命令行完全够不着**。

    更要命的是它**验不出来**：用 `python -c "import oss"` 去查，
    当前目录排在搜索路径最前面，于是先看见仓库、给出一个假的通过；
    而真正的命令行脚本不把当前目录放进搜索路径，它看见的是 site-packages。
    **查的地方跟跑的地方不是同一个，等于没查。**

    所以这里看的是 `oss` 这个包**此刻真正加载自哪里**——
    不重新 import，直接读已经在内存里的那个模块的文件路径。

    两个参数只为让「装错时会不会真报警」这件事本身可测：
    只测得到通过的那一支，等于没测这道门。
    """
    if pkg is None:
        import oss as _oss

        pkg = Path(_oss.__file__ or "").resolve().parent
    if repo is None:
        repo = Path(__file__).resolve().parents[1]
    if pkg == repo:
        return True, str(pkg)
    return False, (
        f"**装的和仓库不是同一份** —— 正在跑 {pkg}，仓库在 {repo}。"
        f"改仓库不会生效。修：{_install_cmd()} -e {repo.parent}"
    )


def _skills_src() -> Path:
    """说明书的**源头**。`install-skills`、`doctor` 与过程话的系统专名表必须问同一个地方，
    所以判据只此一份，在 `oss/core/manuscript.py`。"""
    return manuscript.skills_src()


def _skills_drift(src: Path | None = None, dst: Path | None = None,
                  profile: "hosts.HostProfile | None" = None) -> tuple[bool, str]:
    """全局铺的那份说明书，跟源头是不是同一个内容。

    **跟「装的代码是旧的」是同一类陷阱**，而且更隐蔽：
    模型读的永远是全局那份，改了源头忘了重铺，**改动一行都不生效**，
    而所有检查都会通过——因为源头确实改对了。

    这个项目的说明书改过 22 次。只要有一次忘了重铺，
    后面每一次真跑验的都是旧说明书，**而且验出来的问题会被当成新问题去修**。

    **阶段 4 起按宿主比**：dsh 的安装树里 `allowed-tools` 是注释，跟源头字节不同——
    拿源头直接比会让「刚装完」立刻显示不一致。所以比的是**这个宿主的投影**，
    投影函数跟 `install-skills` 用的是同一个。
    """
    profile = profile or hosts.load(hosts.DEFAULT_HOST)
    src = src or _skills_src()
    dst = dst or profile.user_dir()
    if not src.exists():
        return True, "源头不在这个安装里（装的是发行版），跳过"

    stale, missing = [], []
    for skill in sorted(p for p in src.iterdir() if p.is_dir()):
        out = dst / skill.name
        if not out.exists():
            missing.append(skill.name)
            continue
        for f in sorted(skill.rglob("*")):
            if not f.is_file() or host_project.ignored(f.name):
                continue
            rel = f.relative_to(skill)
            try:
                want = host_project.projected_bytes(profile, rel, f.read_bytes())
            except host_project.BuildError as exc:
                return False, f"**说明书投影不到 {profile.name}**：{exc}"
            mirror = out / rel
            if not mirror.exists() or mirror.read_bytes() != want:
                stale.append(skill.name)
                break
    if not stale and not missing:
        return True, f"{len(list(src.iterdir()))} 份，与源头一致"
    bits = []
    if stale:
        bits.append("已过期 " + "、".join(stale))
    if missing:
        bits.append("没铺 " + "、".join(missing))
    return False, (
        f"**全局的说明书跟源头不一样**（{'；'.join(bits)}）——"
        f"模型读的是全局那份，**改了源头不重铺等于没改**。"
        f"修：oss install-skills --host {profile.name} --force"
    )



#: `tests/test_whole_output.py` 直接 import 这个名字，别名保留
_finished_run = profile_api.finished_run



#: 审稿投影搬进了 oss/api/paper.py；`tests/test_whole_output.py` 直接 import 这个名字
_results_digest = paper_api.results_digest


def _run(run: str) -> RunDir:
    """还没搬进 api 的命令从这里拿运行目录。**写的命令：不存在就建。**

    只读的命令别用它——那一侧走 `oss/api/rundir.py::require_run`，
    打错路径要拒绝，不许顺手建一个空目录出来。
    """
    return _api_open_run(run)


def _emit(
    rundir: RunDir,
    step: str,
    command: str,
    artifact: str,
    payload,
    *,
    note: str = "",
    args: dict | None = None,
) -> None:
    """落盘 + 留痕 + 回显。三件事总是一起发生，所以合并成一个入口。

    **`note` 必须由调用方给。**这里原先写死 `"FAKE：阶段 0 假实现"`，
    而且没有参数可以传对的进来——于是工具侧全部换真之后，
    `trace.jsonl` 仍然对每一次运行都说「这是假的」。

    后果不是「说得难听了点」：`trace.jsonl` 是这个项目的审计留痕，
    它存在的意义就是让人事后回答「这个数字从哪来、可不可信」。
    对真跑和 smoke 说同一句话，等于**留痕分不出这两者**，价值归零。
    （PLAN.md 九之一 ⑧，GSS 真跑时 24 条留痕里 19 条是那句写死的话。）

    `args` 用来记可核验的溯源信息——最要紧的是**输入数据的 sha256**：
    没有它，留痕连「跑的是哪一版数据」都回答不了。
    """
    path = _api_emit(rundir, step, command, artifact, payload, note=note, args=args)
    typer.echo(f"✓ {ARTIFACTS[artifact]}  →  {path}")


# --------------------------------------------------------------- ① 数据画像


@app.command()
def profile(
    data: str = typer.Argument(..., help="数据文件路径"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    shape: bool = typer.Option(False, "--shape", help="只输出形状，不输出变量清单"),
    resume: bool = typer.Option(False, "--resume", help="明确要接着一个已完成的运行继续"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """数据画像：结构、变量、缺失、层次。

    --shape 是 DECISIONS.md 5.1 的落点：合成 idea 的阶段只该看见形状，
    看见变量名模型就会顺着现有列凑问题。
    """
    try:
        res = profile_api.profile(data, run=run, shape=shape, resume=resume)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    if shape:
        typer.echo(d["shape_summary"])
        return
    typer.echo(f"✓ {d['artifact']}  →  {d['path']}")
    for w in d["warnings"]:
        typer.echo(f"  ⚠ {w}")


# ----------------------------------------------------------------- ② 文献


@lit_app.command("verify")
def lit_verify(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    out: str = typer.Option("", "--out", help="配对文件写到哪，缺省写进运行目录"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """核对论文里每一处引用的**转述**对不对。

    上一步（`{{cite:...}}`）保证了「这篇文献真实存在、作者年份没写错」，
    **保证不了「转述得对」**——编号是真的、年份是对的，内容可以是歪的。

    这一步分两层：

    1. **机器比对**（这里做）：把转述里的概念经中英词典映射后，
       去被引文献的标题+摘要里找。只能抓明显的张冠李戴。
       **没有摘要、或转述里没有可映射的概念 → 判「核不了」，不判「存疑」。**
    2. **跨模型判**：产出的配对文件交给别家模型，问「这句话是这篇文献支持的吗」。
       细微的过度引申只有这一层抓得到。
    """
    try:
        res = lit_api.verify(run, out=out)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    if not d["n_pairs"]:
        typer.echo(f"· {res['summary']}")
        return
    target = Path(d["path"])
    typer.echo(f"✓ {target.name}  →  {target}")
    for k, v in d["tally"].items():
        if v:
            typer.echo(f"  {k} {v} 处")
    for p_ in d["pairs"]:
        if p_["机器判定"] != "支持":
            typer.echo(f"  · [{p_['机器判定']}] {p_['cite']}：{p_['论文原话'][:38]}")
    typer.echo(
        f"\n  **机器只能抓明显的张冠李戴。**细微的过度引申要交给别家模型判：\n"
        f"    {d['next']}"
    )


@lit_app.command("add")
def lit_add(
    doi: str = typer.Argument(..., help="要补进语料的文献 DOI"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """按 DOI 把一篇文献补进语料。

    **为什么需要这条命令**：规则「引用必须来自语料」有个副作用——
    检索没召回的经典（比如一本 1967 年的书）会逼人要么不引、要么造假。
    这条命令给出正路，而且它是**留痕的、可核的**，
    不是在正文里偷偷写一个名字。
    """
    try:
        res = lit_api.add(doi, run=run)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    if d["already"]:
        typer.echo(f"· {res['summary']}")
        return
    typer.echo(f"✓ {res['summary']}")
    typer.echo(f"  引用时写：{d['cite']}")


@lit_app.command("search")
def lit_search(
    query: list[str] = typer.Argument(None, help="检索式，可给多条，写法 阵营: (概念块)"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    limit: int = typer.Option(20, "--limit", help="每条检索式每个源取多少"),
    year_from: int = typer.Option(None, "--year-from", help="只要这一年之后的"),
    zh: list[str] = typer.Option([], "--zh",
                                 help="中文检索式，可给多条，写法 阵营: 词 词；只查 OpenAlex 里 2018 年前的核心刊白名单"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """检索文献。OpenAlex（要一把免费密钥）+ Semantic Scholar；Crossref 只兜底。"""
    try:
        res = lit_api.search(list(query or []), run=run, limit=limit, year_from=year_from, zh=list(zh))
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    typer.echo(f"✓ {d['artifact']}  →  {d['path']}")
    typer.echo(
        f"  {d['n_records']} 条｜命中 {d['hits_by_source']}"
        f"｜摘要 {d['abstract_coverage']:.0%}｜引文 {d['citation_coverage']:.0%}"
    )
    if d["camps"]:
        typer.echo("  阵营：" + "；".join(f"「{c}」← {q[:40]}（{d['hits_by_query'].get(q, 0)} 条）"
                                       for q, c in d["camps"].items()))
    for note in d["notes"]:
        typer.echo(f"  {note}")


# ------------------------------------------------------------- ③· 宽表转长表


@app.command()
def reshape(
    data: str = typer.Argument(..., help="宽表数据文件"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    out: str = typer.Option("long.csv", "--out", help="输出文件名，落在运行目录里"),
    unit: str = typer.Option("", "--unit", help="个体编号列，缺省自动认"),
    wave_name: str = typer.Option("wave", "--wave-name", help="轮次列叫什么"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """宽表面板转长表：每人每轮一行。

    画像认得出宽表面板，却只会提示「请重塑成长表」然后没有下文——
    真跑一次三轮面板时，模型只好**自己手写重塑脚本**。那是模型在干算的活，
    而**没有任何检查会去核它写对没有**：轮次拼错了回归照跑，结果照出。

    只做摞行。量表反向、取对数、转哑变量一概不做——那些是研究者的决定。
    """
    try:
        res = tables_api.reshape(data, run=run, out=out, unit=unit, wave_name=wave_name)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    typer.echo(f"✓ {d['out']}  →  {d['path']}")
    for note in d["notes"]:
        typer.echo(f"  · {note}")
    typer.echo(f"  ↳ 接着跑：{d['next_command']}")


# --------------------------------------------------------- ③之二· 多轮摞成一张


@app.command()
def stack(
    spec: str = typer.Argument(..., help="映射文件（JSON）：每轮取哪个文件、哪些列"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    out: str = typer.Option("stacked.csv", "--out", help="输出文件名，落在运行目录里"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """多轮调查摞成一张表：每轮一段，列名按声明对齐。

    `merge` 是横着拼两份，这是**竖着摞多份**。11 轮 CGSS 每轮列名都不一样，
    而这一段之前只能落在模型手写的 `prep.py` 里——**这条链上唯一没人核的计算**，
    偏偏最危险的东西全在这儿：编码翻转（`1=是` 变成 `4=是`）、某一轮整个模块
    没问、缺失码逐轮不同。**三样错了都不报错，只是把一类人算成另一类人。**

    映射文件的样子跑 `oss schema stack --example`。
    """
    try:
        res = tables_api.stack(spec, run=run, out=out)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        for p in exc.problems:
            typer.echo(f"  · {p}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    typer.echo(f"✓ {d['out']}  →  {d['path']}")
    for note in d["notes"]:
        typer.echo(f"  · {note}")
    for w in d["warnings"]:
        typer.echo(f"  ⚠ {w}")
    for m in d["missing_codes"][:6]:
        typer.echo(f"  ○ {m}")
    if len(d["missing_codes"]) > 6:
        typer.echo(f"  ○ 还有 {len(d['missing_codes']) - 6} 条缺失码提示，见 --json")
    typer.echo(f"  ↳ 接着跑：{d['next_command']}")


@app.command()
def merge(
    left: str = typer.Argument(..., help="左表（主数据，行数以它为准）"),
    right: str = typer.Argument(..., help="右表（要拼进来的数据）"),
    on: str = typer.Option("", "--on", help="两边同名的键，逗号分隔，如 country,year"),
    left_on: str = typer.Option("", "--left-on", help="左表的键，两边列名不同时用"),
    right_on: str = typer.Option("", "--right-on", help="右表的键"),
    how: str = typer.Option("inner", "--how", help="inner（只留配上的）/ left（左表全留）"),
    allow_loss: float = typer.Option(-1.0, "--allow-loss", help="接受多大的损失，如 0.25"),
    map_file: str = typer.Option("", "--map", help="键对照表 CSV，两列：左表写法,右表写法"),
    out: str = typer.Option("merged.csv", "--out"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """拼两份数据，并**把丢掉的东西摊开**。

    真数据上量过：两份「国家-年」数据按国家名直接拼，丢 25.3%、30 个国家整个消失，
    **没有任何报错**——一边写 `Korea (the Republic of)`，一边写 `South Korea`。
    而丢掉的那批里民主国家比例低 10 个百分点，**那份数据研究的正是民主化**。

    对不上的键只列出来、只给待确认的候选，**绝不自动套用**。
    """
    try:
        res = tables_api.merge(left, right, run=run, on=on, left_on=left_on,
                               right_on=right_on, how=how, allow_loss=allow_loss,
                               map_file=map_file, out=out)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    typer.echo(f"✓ {d['out']}  →  {d['path']}")
    for note in d["notes"]:
        typer.echo(f"  · {note}")
    typer.echo(f"  ↳ 接着跑：{d['next_command']}")




@lit_app.command("fetch")
def lit_fetch(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    top: int = typer.Option(20, "--top", help="精读前几篇（按主题重合排，被引只在打平时说话）"),
    ids: str = typer.Option("", "--ids", help="点名要读的 id / DOI，逗号分隔。给了就不按排名挑"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """把最值得精读的文献拿到本地：有开放获取链接的抽全文，没有的只留摘要。

    **来源层级会标清**：全文 / 仅摘要 / 仅元数据。一张只读了摘要的读书卡，
    它的「局限」栏几乎必然是空的——那不是文献没局限，是没读到。

    **`--top` 排出来的是候选，读哪几篇是判断。**验证跑里按排名拿的前 20 篇
    只有 8 篇跟题目沾边，执行者最后是自己在语料里按关键词挑的——
    那就让它直接点名：`--ids`。
    """
    try:
        res = lit_api.fetch(run, top=top, ids=ids)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        # 缺的那几个 id 原先打在 ✗ 前面，位置不许挪
        for w in exc.data.get("warnings", []):
            typer.echo(f"  ⚠ {w}", err=True)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    for w in d["warnings"]:
        typer.echo(f"  ⚠ {w}", err=True)
    typer.echo(f"✓ reading/  →  {d['path']}")
    typer.echo(f"  {d['note']}")
    for it in d["items"]:
        mark = {"fulltext": "全文", "abstract": "摘要", "metadata": "元数据"}[it["level"]]
        typer.echo(f"  [{mark:3s}] {it['title'][:60]}"
                   + (f"  （{it['reason']}）" if it["reason"] else ""))
    typer.echo("  ↳ 读 reading/<id>.txt 写读书卡：oss schema reading --example；写完 oss lit cards --check")


@lit_app.command("cards")
def lit_cards(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    check: bool = typer.Option(True, "--check/--no-check"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """校验读书卡：**局限引文必须能在拿到的文本里逐字找到**。

    借自 nature-reader 的源锚点纪律。找不到的一律拒绝——
    「大概是这个意思」正是空白地图最不能建在上面的东西。
    """
    try:
        res = lit_api.cards(run, check=check)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        # 警告原先打在 **stdout**，且在 ✗ 之前——两样都不动
        for w in exc.data.get("warnings", []):
            typer.echo(f"  ⚠ {w}")
        typer.echo(f"✗ {exc.summary}", err=True)
        for x in exc.problems:
            typer.echo(f"  · {x}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    for w in d["warnings"]:
        typer.echo(f"  ⚠ {w}")
    typer.echo(f"✓ {res['summary']}")
    typer.echo(f"  来源层级：{d['levels']}")


@lit_app.command("screen")
def lit_screen(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """把模型写的筛选判定（screening.json）盖到语料上：每篇都要判，排除要写理由。"""
    try:
        res = lit_api.screen(run)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        for w in exc.data.get("warnings", []):
            typer.echo(f"  ⚠ {w}")
        typer.echo(f"✗ {exc.summary}", err=True)
        for x in exc.problems:
            typer.echo(f"  · {x}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    for w in res["data"]["warnings"]:
        typer.echo(f"  ⚠ {w}")
    typer.echo(f"✓ {res['summary']}")


@lit_app.command("snowball")
def lit_snowball(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    seeds: str = typer.Option("", "--seeds", help="点名种子 id/DOI，逗号分隔；缺省按排序取前 --top 篇"),
    top: int = typer.Option(8, "--top", help="缺省种子数"),
    backward: int = typer.Option(50, "--backward", help="每个种子往后拉几篇参考文献（按被引取，最多 200）"),
    forward: int = typer.Option(15, "--forward", help="每个种子往前拉几篇引用它的（按被引取最有分量的，最多 200）"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """从种子往后拉参考文献、往前拉引用，扩进语料。滚完再跑 oss lit screen 只判新增的。"""
    try:
        res = lit_api.snowball(run, seeds=seeds, top=top, backward=backward, forward=forward)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        for w in exc.data.get("warnings", []):
            typer.echo(f"  ⚠ {w}")
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    for w in d["warnings"]:
        typer.echo(f"  ⚠ {w}")
    typer.echo(f"✓ {res['summary']}")
    typer.echo(f"  种子：{'、'.join(d['seeds'])}")
    if d["next"]:
        typer.echo(f"  ↳ 接着跑：{d['next']}")


@lit_app.command("import")
def lit_import(
    file: str = typer.Argument(..., help="题录导出文件（RIS、EndNote、RefWorks、NoteExpress），"
                                         "或者一个 PDF、一整个 PDF 目录"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    camp: str = typer.Option("", "--camp", help="这批文献属于哪个理论阵营"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """把你自己找好的文献并进语料：知网/万方的题录导出文件，或者你手里的 PDF。

    **PDF 给目录就整批导**，元数据从正文找（DOI 反查 → 标题反查 → 标 [UNVERIFIED]），
    全文同时落进精读目录——开放获取拿不到全文的那些，这是唯一的补法。

    导入不等于筛过，接着跑 oss lit screen 只判新增的。
    """
    try:
        res = lit_api.import_file(file, run=run, camp=camp)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        for w in exc.data.get("warnings", []):
            typer.echo(f"  ⚠ {w}")
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    # 跟别的命令一样用下标取，不用 `.get`。**少一个键就当场崩，别静默吞掉**——
    # 这儿原先是 `.get("warnings", [])`，PDF 分支的 [UNVERIFIED] 警告被它整条吃了，
    # 而紧接着的 `d["next"]` 照样崩。盖住一半的兜底比不兜底更坏。
    for w in d["warnings"]:
        typer.echo(f"  ⚠ {w}")
    typer.echo(f"✓ {res['summary']}")
    if d["next"]:
        typer.echo(f"  ↳ 接着跑：{d['next']}")


data_app = typer.Typer(no_args_is_help=True, help="公开数据源：题目需要什么数据，就去拿")
app.add_typer(data_app, name="data")

@data_app.command("search")
def data_search(
    query: str = typer.Argument(..., help="关键词，英文，如 'internet users share'"),
    source: str = typer.Option("", "--source", help="wb / oecd / owid，不给就三个都搜"),
    top: int = typer.Option(12, "--top"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """在世界银行、OECD、Our World in Data 的目录里搜指标。**给的是候选，不是答案。**"""
    try:
        res = data_api.search(query, source=source, top=top)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    if not d["candidates"]:
        typer.echo(f"· {res['summary']}")
        return
    for c in d["candidates"]:
        typer.echo(f"  [{c['source']:4s}] {c['code']:40s} {c['name'][:60]}")
    typer.echo("  ↳ 拉：oss data fetch <源> <代码> --run <运行目录>")


@data_app.command("fetch")
def data_fetch(
    source: str = typer.Argument(..., help="wb / oecd / owid"),
    code: str = typer.Argument(..., help="指标代码 / 数据流 / 图表 slug"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    out: str = typer.Option("", "--out", help="文件名，缺省按代码起"),
    year_from: int = typer.Option(0, "--year-from"),
    year_to: int = typer.Option(0, "--year-to"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """把一个指标拉成「国家-年」长表，带 ISO3 代码和出处（.meta.json）。"""
    try:
        res = data_api.fetch(source, code, run=run, out=out,
                             year_from=year_from, year_to=year_to)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    typer.echo(f"✓ {d['name']}  →  {d['path']}")
    typer.echo(f"  {d['rows']} 行｜{d['countries']} 个国家｜{d['years']}｜列：{', '.join(d['columns'][:8])}")
    for n in d["notes"]:
        typer.echo(f"  · {n}")
    typer.echo(f"  ↳ 拼进主数据：oss merge <主数据> {d['path']} --left-on <国家列>,<年份列> --right-on iso3,year --run {run}")


_STATUS_ZH = {"local": "本地已有", "download": "可直接下载", "incoming": "已放进来，待转", "manual": "需申请，还没放"}


@data_app.command("list")
def data_list(
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """现成数据集清单：哪些能直接下、哪些要申请、哪些已经在本机。

    **清单只列候选，不替你选。**题目需要额外数据时用哪一套是判断，写进计划、留痕。
    """
    res = data_api.list_packs()
    if json_out:
        jout.ok(res)
    for r in res["data"]["packs"]:
        keys = "、".join(f"{k}={v}" for k, v in r["keys"].items())
        typer.echo(f"  {r['name']:6s} {_STATUS_ZH[r['status']]:10s} {r['level']:36s} 钥匙列 {keys}")
        typer.echo(f"         {r['title']}")
    typer.echo("  ↳ 拿：oss data get <名>；详情：oss data info <名>；要申请的放进 data/<名>/ 再 get")


@data_app.command("needs")
def data_needs(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """这一轮候选题目缺哪些数据：合并同类项、按解锁几个候选排序、标可信度。

    信息本来就在 `ideas.json` 的 `needs_extra_data` 里，只是散在十个候选的字段里没人读。
    **三档的差别是这张表的要害**：一档一条命令就拿到；二档你精读的文献用过，
    存在性有出处；三档只有模型说过，名字可能不对甚至根本不存在。
    """
    try:
        res = data_api.needs(run)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    typer.echo(res["summary"])
    for line in res["data"]["lines"]:
        typer.echo(f"  · {line}")
    if res["data"]["lines"]:
        typer.echo("  ↳ ① 一条命令就拿到　② 你精读的文献用过，存在性有出处，优先考虑")
        typer.echo("     ③ 未核实＝模型点了名但没人证实过（名字可能不对甚至不存在，"
                   "去找之前先搜一下）；没点名＝只说了缺什么，还得自己找哪套数据有")


@data_app.command("info")
def data_info(
    name: str = typer.Argument(..., help="数据集名，见 oss data list"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """一套数据的码本、钥匙列、引用格式、许可、怎么拿。"""
    try:
        res = data_api.info(name)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    typer.echo(f"{d['title']}\n  层级 {d['level']}｜钥匙列 {d['unit']} / {d['time']}"
               + (f" / {d['iso3']}" if d["iso3"] else ""))
    typer.echo(f"  状态 {_STATUS_ZH[d['status']]}｜许可 {d['license']}"
               + (f"｜权重 {d['weights']}" if d["weights"] else ""))
    typer.echo(f"  主页 {d['homepage']}\n  码本 {d['codebook']}")
    typer.echo(f"  引用 {d['citation']}")
    if d["note"]:
        typer.echo(f"  · {d['note']}")
    if d["manual"]:
        typer.echo("  怎么拿：\n" + "\n".join("    " + ln for ln in d["how_to"].split("\n")))
    else:
        typer.echo(f"  下载 {d['url']}（约 {d['size_mb']} MB，第一次用时才下，之后用缓存）")


@data_app.command("columns")
def data_columns(
    name: str = typer.Argument(..., help="数据集名，见 oss data list"),
    search: str = typer.Option("", "--search", help="关键词，逗号分隔；在列名、变量标签、值标签里找"),
    limit: int = typer.Option(40, "--limit"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """按构念找列：一套数据里哪些列可能测你要的东西。

    评审第四条致命问题：理论要「未审查的政治信息」，测的是「上网人口比例」；
    V-Dem 的数字社会模块是公开的，论文说「需要这些数据」却没去找。**先搜列，再说没有。**
    找到的是候选，不是答案——口径要看码本。
    """
    try:
        res = data_api.columns(name, query=search, limit=limit)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    typer.echo(f"{d['title']}：{d['n_index']} 列"
               + (f"，命中 {d['n_rows']} 列（{'、'.join(d['terms'])}）" if d["terms"] else ""))
    for r in d["rows"]:
        vals = "；".join(f"{k}={v}" for k, v in list(r["values"].items())[:4])
        typer.echo(f"  {r['name']:<28} {r['label'][:60]}" + (f"  [{vals}{'…' if r['n_values'] > 4 else ''}]" if vals else ""))
    if d["n_rows"] > limit:
        typer.echo(f"  … 还有 {d['n_rows'] - limit} 列，--limit 调大或换更具体的词")
    if not d["has_labels"]:
        typer.echo(f"  ↳ 这套数据的文件里没有变量标签，只能按列名找；口径看码本：{d['codebook']}")
    typer.echo(f"  ↳ 选定了就：oss data get {name} --columns <列名,列名,...>")


@data_app.command("get")
def data_get(
    name: str = typer.Argument(..., help="数据集名，见 oss data list"),
    columns: str = typer.Option("", "--columns", help="只抽这些列，逗号分隔（六千列的调查文件必须这样）"),
    out: str = typer.Option("", "--out", help="抽出来的表写到哪（比如运行目录）；缺省写在 data/<名>/"),
    file: str = typer.Option("", "--file", help="用这个本地文件（要申请的数据集自己下好的）"),
    refresh: bool = typer.Option(False, "--refresh", help="重新下载，不用缓存"),
    run: str = typer.Option("", "--run", help="给了就把出处记进这个运行目录的留痕"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """拿一套数据：第一次下载（或用你放进来的文件）→ 转 CSV → 记出处。之后直接用缓存。

    要申请的数据集系统不能替你下：它会停下来告诉你怎么申请、放哪。
    """
    try:
        res = data_api.get(name, columns=columns, out=out, file=file,
                           refresh=refresh, run=run)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    typer.echo(f"✓ {d['title']}")
    typer.echo(f"  {'转好的表' if d['converted'] else '原件'} {d['path']}")
    if d["converted"]:
        typer.echo(f"  {d['rows']:,} 行 × {d['n_columns']} 列｜{d['years'] or '年份列没选'}｜钥匙列 "
                   + "、".join(f"{k}={v}" for k, v in d["keys"].items()))
    else:
        typer.echo(f"  {d['n_columns']} 列（前几列：{', '.join(d['columns'][:8])}…）")
    for n in d["notes"]:
        typer.echo(f"  · {n}")
    typer.echo(f"  引用（论文数据节必须出现「{d['cite_name']}」并引它）：{d['citation']}")
    if d["converted"] and d["keys"].get("iso3"):
        typer.echo(f"  ↳ 跟别的国家-年数据拼：oss merge <主数据> {d['path']} --left-on <代码列>,<年份列> "
                   f"--right-on {d['keys']['iso3']},{d['keys']['time']}")


sim_app = typer.Typer(no_args_is_help=True, help="仿真货箱：验收、入库外部模拟器的输出")
app.add_typer(sim_app, name="sim")


@sim_app.command("init")
def sim_init(box: str = typer.Argument(..., help="要新建的货箱目录"),
             json_out: bool = typer.Option(False, "--json", help=JSON_HELP)) -> None:
    """生成空白配料单与运行清单，必填项都列好等着填。"""
    try:
        res = sim_api.init(box)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    typer.echo(f"✓ {res['summary']}")


@sim_app.command("check")
def sim_check(box: str = typer.Argument(..., help="货箱目录"),
              json_out: bool = typer.Option(False, "--json", help=JSON_HELP)) -> None:
    """验货：拒收逐条点名；通过时把降级/单条件警告如实打出来。"""
    try:
        res = sim_api.check(box)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        for p in exc.problems:
            typer.echo(f"  · {p}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    typer.echo(f"✓ {res['summary']}")
    for w in res["data"]["warnings"]:
        typer.echo(f"  ⚠ {w}")


@sim_app.command("pack")
def sim_pack(simulator: str = typer.Option(
                 ..., "--simulator", "--from",
                 help="模拟器名，见插头注册表（--from 是别名，留作向后兼容）"),
             box: str = typer.Argument(..., help="含 raw/ 与 runs.csv 的货箱目录"),
             json_out: bool = typer.Option(False, "--json", help=JSON_HELP)) -> None:
    """走插头装箱：原始输出 → states/events 两张表，配料单后半自动补。"""
    try:
        res = sim_api.pack(simulator, box=box)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    typer.echo(f"✓ {res['summary']}")


@sim_app.command("import")
def sim_import(box: str = typer.Argument(..., help="验过货的箱子"),
               run: str = typer.Option(..., "--run", help="运行目录（必须已存在）"),
               json_out: bool = typer.Option(False, "--json", help=JSON_HELP)) -> None:
    """入库：写 sim_state.json、留痕。拒收的箱子一个字节都不进来。"""
    try:
        res = sim_api.import_box(box, run=run)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        for p in exc.problems:
            typer.echo(f"  · {p}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    typer.echo(f"✓ {res['summary']}")
    for w in res["data"]["warnings"]:
        typer.echo(f"  ⚠ {w}")   # 入库是落章的一刻——降级必须此刻可见（2026-08-31 拍板的前提）


@sim_app.command("doctor")
def sim_doctor(simulator: str = typer.Option("oasis", "--simulator", help="体检哪个模拟器"),
               json_out: bool = typer.Option(False, "--json", help=JSON_HELP)) -> None:
    """体检：版本锁、样本数据、网关变量。只报事实。"""
    try:
        res = sim_api.doctor(simulator)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    typer.echo(res["summary"])   # 「体检 N 条没过」先露面——退出码永远是 0，这行是唯一的肉眼信号
    for c in res["data"]["checks"]:
        typer.echo(f"{'✓' if c['ok'] else '✗'} {c['name']}  {c['note']}")


# ------------------------------------------------------------- ④ 绑定与功效


@app.command()
def bind(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    idea: str = typer.Option("", "--idea", help="idea_id，缺省取 ideas.json 的第一个"),
    map: list[str] = typer.Option(None, "--map", help="构念=列名:角色，可给多个"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """把构念绑到数据的真实列上。绑不上就说清楚缺什么。

    两种用法：
      不带 --map  只列候选（算），让 skill 去选（判）
      带 --map    回验 skill 选的列（回验），产出 binding.json
    """
    try:
        res = (binding_api.bind(run, idea=idea, mappings=list(map))
               if map else binding_api.candidates(run, idea=idea))
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    if not map:
        typer.echo(f"{d['idea_id']} 需要绑定这些构念，各自的候选列如下：\n")
        for c in d["constructs"]:
            typer.echo(f"  {c['construct']}")
            if c["rule_failed"]:
                typer.echo(f"    ⚠ {c['note']}")
                continue
            for cand in c["matches"]:
                typer.echo(f"    {cand['column']:<20} {cand['score']:>6.3f}  {cand['why']}")
        if d["rule_failed"]:
            # 规则给不出来时，把全表交出去——**这才是「把问题交出去」**，
            # 而不是按缺失率补几列充数。
            typer.echo("\n  有构念规则匹配不上。全表的列如下，请自己判断：")
            for v in d["all_variables"]:
                tag = f"  {v['label']}" if v["label"] else ""
                typer.echo(f"    {v['name']:<20} {v['semantic_type']:<12} "
                           f"缺失 {v['missing_rate']:>5.1%}  取值 {v['n_unique']}{tag}")
        typer.echo(d["next_command"])
        return
    typer.echo(f"✓ {ARTIFACTS['binding']}  →  {d['path']}")
    for b in d["bindings"]:
        mark = "✓" if b["is_usable"] else "✗"
        typer.echo(f"  {mark} {b['construct_name']} ({b['role']}) → "
                   f"{b['column'] or b['derivation'] or '—'}"
                   f"  [{b['status']}] {b['note']}")
    if d["needs_gap_columns"]:
        typer.echo("  ↳ 代理 / 构造变量要填代理差距三栏：在 binding.json 里给每一条写 construct_def（理论要什么）、"
                   "measures（这列测的是什么）、gap（差距，偏向哪边）。paper check 会查填没填")
    if d["blocking"]:
        typer.echo("\n  这个 idea 做不了：")
        for x in d["blocking"]:
            typer.echo(f"    · {x}")


@app.command()
def figure(
    kind: str = typer.Argument(..., help="structure | missing | clusters"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    data: str = typer.Option("", "--data", help="数据文件，缺省从 profile.json 读"),
    cluster_col: str = typer.Option("", "--cluster-col", help="clusters 图用"),
    by: str = typer.Option("", "--by", help="marginal 图：斜率随哪一列变"),
    split: str = typer.Option("", "--split", help="marginal 图：按哪一列分组画多条线"),
    bins: int = typer.Option(6, "--bins", help="marginal 图的箱数"),
    treatment: str = typer.Option(
        "", "--treatment",
        help="marginal 图：对哪个变量算斜率。缺省用 plan.treatment，"
             "但那是交互项时必须显式给——交互列在参照组里恒为 0，画出来是空的",
    ),
    icc: float = typer.Option(0.05, "--icc", help="clusters 图的 ρu"),
    out: str = typer.Option("", "--out", help="输出路径，缺省写进运行目录"),
    lang: str = typer.Option(
        "zh", "--lang",
        help="进论文的图（coef / event / robustness / marginal）的语言："
             "zh 缺省（中文稿、中文期刊体例），en 英文稿。"
             "**两个方向都拦**：英文稿嵌中文图、中文稿嵌英文图，paper check 都不放行",
    ),
    style: str = typer.Option(
        "", "--style",
        help="风格包名：按该刊的版心定图的宽度（rules.json 的 figure_max_width）。"
             "不给用缺省 4.33 英寸——图的物理宽度就是画布宽度，超了印出来字号会变小",
    ),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """诊断图。**不是装饰，是最快的错误检测器。**

    回归表回答不了「这个模型对不对」，图能。
    结构判错、块状缺失、簇数太少——这三样在表格里要靠人逐行核，在图上一眼可见。
    """
    try:
        res = figure_api.figure(kind, run=run, data=data, cluster_col=cluster_col, by=by,
                                split=split, bins=bins, treatment=treatment, icc=icc, style=style,
                                out=out, lang=lang)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        # log 先打：「分箱斜率取自 results.json」原先是**边走边打**的——
        # 画图依赖缺席时它那时候已经在屏幕上了，排到 ✗ 后面就是顺序漂移
        for stream, text in exc.data.get("log", []):
            typer.echo(text, err=(stream == "err"))
        extra = exc.data.get("install_extra")
        tail = f"\n  装：{_install_cmd()} '{extra}'" if extra else ""
        typer.echo(f"✗ {exc.summary}{tail}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    # log 先打：「分箱斜率取自 results.json」原先打在画图之前，位置不许挪
    for stream, text in d["log"]:
        typer.echo(text, err=(stream == "err"))
    if d["has_meta"]:
        typer.echo(f"  语言 {d['lang']}｜矢量版 {d['pdf'] or '无'}")
        typer.echo(f"  图注（建议，照它改写）：{d['caption']}")
        if d["labels"]:
            typer.echo("  ⚠ 稳健性标签是中文的，英文图上只留了编号："
                       + "；".join(f"{k} = {v}" for k, v in d["labels"].items())
                       + "。计划里的 label 用英文写就不用这样")
        if d["cjk_labels"]:
            typer.echo(f"  ⚠ 这些变量名是中文的，英文图上会原样出现：{'、'.join(d['cjk_labels'])}")
    typer.echo(f"✓ {d['name']}  →  {d['path']}    （{d['kind_label']}）")


@app.command()
def power(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    idea: str = typer.Option("", "--idea"),
    effect: float = typer.Option(
        0.2, "--effect",
        help="文献报告的效应量（未打折）。描述类题目按精度判时是有意义的最小差别："
             "标准差为单位；加 --effect-metric raw 则按结局原单位",
    ),
    effect_source: str = typer.Option(
        "", "--effect-source",
        help="这个效应量从哪儿来：哪条语料记录、哪一页、原话。"
             "**不给就判不了 pass**——判词整个建立在这个数上，"
             "没有出处等于没人选过它，论文里也不许写「按文献报告的效应量」",
    ),
    effect_metric: str = typer.Option(
        "d", "--effect-metric",
        help="效应量的度量：d（Cohen's d）/ r（相关系数）/ beta（标准化系数）。"
             "**换度量就要换公式**——传 r 却按 d 算会安静地给出错的功效。"
             "描述类按精度判时还可以是 raw：--effect 按结局原单位给，按结局的加权标准差折成标准差单位"
             "（按功效判时不认）",
    ),
    n: int = typer.Option(0, "--n", help="样本量，0 表示从 profile.json 读"),
    discipline: str = typer.Option("", "--discipline", help="决定灰区的上下界"),
    clusters: int = typer.Option(0, "--clusters", help="聚类单位数，0 表示从 profile 读"),
    icc: float = typer.Option(0.05, "--icc", help="残差的组内相关系数 ρu"),
    out: str = typer.Option("", "--out", help="另存为运行目录里的这个文件名。"
        "一次研究常要算两个尺度（主效应/交互项）——不另存的话后一次会覆盖前一次，"
        "而两份判词都要进论文"),
    cluster_col: str = typer.Option(
        "", "--cluster-col",
        help="聚类列名。给了就从数据算 ρx；描述类按精度判时按它数簇、组差的 ρx 也从它算"
             "（不给就按本题计划的 survey.psu，没写取 cluster_level；都没有就不算整群）",
    ),
    treatment_col: str = typer.Option(
        "", "--treatment-col", help="处理变量列名。不给则从 binding.json 读"
    ),
    treatment_icc: float = typer.Option(
        -1.0, "--treatment-icc",
        help="处理变量组内相关 ρx。不给、也算不出时按 1 算（保守，会低估功效）",
    ),
    alpha: float = typer.Option(0.05, "--alpha"),
    weight_col: str | None = typer.Option(
        None, "--weight-col",
        help="算权重设计效应（Kish）用的权重列：有效样本量 = n ÷（整群设计效应 × 权重设计效应）。"
             "不给 = 用画像检出的权重列（描述类有本题的计划时用计划的 weights）；传空串 = 不按权重折",
    ),
    group_col: str = typer.Option(
        "", "--group-col",
        help="描述类题目按精度判时的分组列：逗号分隔，至多两列，按这几列交叉数各格，最小分组单独报。"
             "组间差（kind: gap）的研究主要比较是排序第二类对参照组（排序第一类）之差，跟 describe 的主结果同一对比；"
             "不给、本题有 describe 计划时按计划的分组（description.groups，趋势再加 over）；"
             "不能跟 --n 一起用（按子样本算就在 prep.py 里筛好，或写进计划的 sample_filter）",
    ),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """统计功效。效应量先按发表偏倚打折再算。

    判词三档：低于下界判死、高于上界通过、**中间交给跨模型判**。
    上下界按学科变——用一个值走天下就等于没有学科档案。

    **描述类题目改按精度判**（power.json 的 mode 是 precision）：--effect 读作有意义的最小差别，
    判词比的是主要比较的 95% 区间半宽；fail 是精度不足、**不停**——环节 6 的 oss route 开 precision_low 处置。
    """
    try:
        res = power_api.power(run, idea=idea, effect=effect, effect_metric=effect_metric,
                              effect_source=effect_source, n=n,
                              discipline=discipline, clusters=clusters, icc=icc, out=out,
                              cluster_col=cluster_col, treatment_col=treatment_col,
                              treatment_icc=treatment_icc, alpha=alpha, weight_col=weight_col,
                              group_col=group_col)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    # ρx 用了假定值的警告打在 ✓ 之前——它发生在计算里，位置跟从前一样
    for w in d["warnings"]:
        typer.echo(f"  ⚠ {w}", err=True)
    typer.echo(f"✓ {ARTIFACTS['power']}  →  {d['path']}")
    if d["extra_path"]:
        typer.echo(f"  另存 → {d['extra_path']}")
    prec = d["mode"] == "precision"      # 描述类按精度判：没有功效值，打「功效 0.00」会误导
    # 不按功效判的那几路，判词那一行说它自己判的是什么——**「功效 0.00」比不打还糟**：
    # 读的人会以为算过、算出来是零（描述类踩过一次，测量类与仿真类各配了一条测试盯着）
    NOT_POWER = {"evidence": "证据做不做得出来", "readiness": "模型跑得出可判的东西吗"}
    if prec:
        typer.echo(f"  {d['verdict'].upper()}  精度 ±{d['precision_halfwidth']:.3f} 个标准差")
    elif (said := NOT_POWER.get(d["mode"])):
        typer.echo(f"  {d['verdict'].upper()}  {said}")
    else:
        typer.echo(f"  {d['verdict'].upper()}  功效 {d['power']:.2f}")
    for bit in d["reason_lines"]:
        typer.echo(f"    · {bit}")
    if d["gray"] and prec:
        typer.echo("\n  → 落在灰区：精度够不够分辨有意义的差别，这一段没有标准答案。"
                   "**写进局限**（一句话：这份数据分辨得出多小的差别），不用再叫人裁决")
    elif d["gray"]:
        typer.echo("\n  → 落在灰区：功效值在上下界之间，这一段没有标准答案。"
                   "**写进局限**（一句话：功效落在灰区、对多大的效应才够），不用再叫人裁决")


# ------------------------------------------------------------------ ⑤ 分析


@app.command(
    "run",
    # **这一行不许手写。**原先写死成「只支持 OLS + 固定效应」，
    # 而工具箱早就支持五种估计量了。帮助文字过期比没有帮助文字更糟：
    # 下一个模型读到「只支持 OLS」，就不会去用那四种因果方法——
    # **能力装好了却因为一句旧文案而用不上。**
    help="按 plan.json 跑分析。支持的估计量：" + "、".join(analysis_mod.SUPPORTED_ESTIMATORS),
)
def run_analysis(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    data: str = typer.Option("", "--data", help="数据文件，缺省从 profile.json 读路径"),
    explore: bool = typer.Option(False, "--explore", help="探索性回归：跑、留痕，结果进 explore/，不进论文"),
    for_: str = typer.Option("", "--for", help="探索回应的处方 id，或 review-rN"),
    explore_plan: str = typer.Option("", "--plan", help="探索用的计划文件（完整 plan 格式）"),
    why: str = typer.Option("", "--why", help="同一条意见探索超过上限时的理由"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """**跑之前先校验计划里每个列名都在数据里存在。**

    列名写错不会报错，回归照样跑，只是量错了东西——这是最贵的静默失败。
    """
    try:
        res = analysis_api.run(run, data=data, explore=explore, for_id=for_,
                               explore_plan=explore_plan, why=why)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        # ⚠ 打在 ✗ 之前：警告发生在校验里，位置跟从前一样
        for w in exc.data.get("warnings", []):
            typer.echo(f"  ⚠ {w}")
        typer.echo(f"✗ {exc.summary}", err=True)
        for x in exc.problems:
            typer.echo(f"  · {x}", err=True)
        if exc.data.get("stale_profile_hint"):
            # 真跑时踩过：列明明加进数据了，报「不在数据里」——
            # 因为校验对着的是 profile.json，而那是**加列之前**画的
            typer.echo("  ↳ 如果这些列是你刚加进数据的：校验看的是 profile.json，"
                       "它可能还是加列之前画的——先重跑 oss profile 再试", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    for w in d["warnings"]:
        typer.echo(f"  ⚠ {w}")
    if d["explore"]:
        typer.echo(f"  [探索结果，不进论文，不可被占位符引用] → {d['path']}")
        for e in d["estimates"]:
            typer.echo(f"  {e['key']:12s} {e['term']:16s} {e['coef']:>10.4f} "
                       f"({e['se']:.4f}) p={e['p']:.4f}")
        return
    typer.echo(f"✓ {ARTIFACTS['results']}  →  {d['path']}")
    for e in d["estimates"]:
        star = "***" if e["p"] < 0.01 else "**" if e["p"] < 0.05 else "*" if e["p"] < 0.1 else ""
        typer.echo(f"  {e['key']:12s} {e['term']:16s} {e['coef']:>10.4f} "
                   f"({e['se']:.4f}) p={e['p']:.4f} {star}")
    typer.echo(f"  n = {d['n']}")
    for k, v in d["diagnostics"].items():
        typer.echo(f"  {k}：{v}")

    if d["event_study"]:
        typer.echo("\n  逐年展开（第 −1 期为参照）：")
        for pt in d["event_study"]:
            t = pt["coef"] / pt["se"] if pt["se"] else 0.0
            mark = "  ←" if pt["k"] < 0 and abs(t) > 1.96 else ""
            typer.echo(f"    k={pt['k']:>3}  {pt['coef']:>8.4f} ({pt['se']:.4f})  t={t:>6.2f}{mark}")
        typer.echo(f"    → 画成图：oss figure event --run {run}")

    if d["robustness"]:
        typer.echo(f"\n  稳健性（{len(d['robustness'])} 项，与主规格同一次跑完）：")
        base = next((e for e in d["estimates"] if e["key"] == "main"), None)
        for rb in d["robustness"]:
            if rb["error"]:
                typer.echo(f"    ✗ {rb['label']}：{rb['error']}")
                continue
            e = rb["main"]
            if e is None:
                typer.echo(f"    ? {rb['label']}：没有 main 估计")
                continue
            # 与主结果比：同号才算「方向一致」
            same = base is not None and (e["coef"] * base["coef"] > 0)
            mark = "✓" if same else "⚠"
            typer.echo(
                f"    {mark} {rb['label']}：{e['coef']:>9.4f} ({e['se']:.4f}) "
                f"p={e['p']:.4f} n={rb['n']}"
            )
        typer.echo("    → 画成森林图：oss figure robustness --run " + run)


# ------------------------------------------------ ⑥′ 结果已经在别处跑完了


results_app = typer.Typer(no_args_is_help=True, help="外部结果：已经在别处跑完的，接进来写论文")
app.add_typer(results_app, name="results")


@results_app.command("import")
def results_import_cmd(
    payload: str = typer.Argument(..., help="照 oss schema results 填好的结果文件"),
    raw: str = typer.Option(..., "--raw", help="**用户粘的那张回归表原文**，原样存档"),
    software: str = typer.Option(
        "", "--software",
        help="用户说用什么处理的。**原话，说多少填多少**——他说「Stata」就填 Stata，"
             "不要补成「Stata 18」；他没说就别填，论文里也不写"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """结果已经在别处跑完了，直接接进来写论文。

    **不问原始数据，也不问为什么带不出来。**只有两条硬要求：原文要存一份
    （中间隔着一道把表转成数据的手工活，小数点看错一位整篇论文就全错且不报错），
    软件名照用户原话（补全就是编造）。
    """
    try:
        res = results_import_api.import_results(run, payload=payload, raw=raw, software=software)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    typer.echo(f"✓ {res['summary']}")
    typer.echo(f"  原文存档 → {d['raw_path']}")
    if d["software"]:
        typer.echo(f"  表注会写「估计在 {d['software']} 中完成」")
    for p_ in res.get("problems") or []:
        typer.echo(f"  · {p_}")


# ------------------------------------------------------------------ ⑦ 论文


paper_app = typer.Typer(no_args_is_help=True, help="论文校验与渲染")
app.add_typer(paper_app, name="paper")


@paper_app.command("check")
def paper_check(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    style: str = typer.Option("", "--style", help="风格包名。给了就按它的骨架查节，而不是通用五节"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """校验稿件：节齐不齐、有没有裸数字、占位符解析得了吗。

    **正文里的统计结果必须是占位符。**手打的数字看起来跟真的一模一样——
    这是整套系统最危险的失败模式，只能在渲染前硬拦。
    """
    try:
        res = paper_api.check(run, style=style)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        # 警告先打：过不了校验时它们原先也打在 ✗ 前面，位置不许挪
        for w in exc.data.get("warnings", []):
            typer.echo(f"  ⚠ {w}")
        typer.echo(f"✗ {exc.summary}", err=True)
        for x in exc.problems:
            typer.echo(f"  · {x}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    for w in d["warnings"]:
        typer.echo(f"  ⚠ {w}")
    typer.echo(f"✓ 校验通过：{d['n_sections']} 节齐全，正文零裸数字")
    typer.echo(f"  引用了 {len(d['placeholders'])} 个结果：{'、'.join(d['placeholders'])}")


@paper_app.command("render")
def paper_render(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    out_file: str = typer.Option("", "--out", help="缺省写到 paper_rendered.md"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """把占位符换成真实数字。**校验不过就不给渲染。**"""
    try:
        res = paper_api.render(run, out=out_file)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        for x in exc.problems:
            typer.echo(f"  · {x}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    typer.echo(f"✓ 已渲染 → {res['data']['path']}")


@paper_app.command("export")
def paper_export(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    out_name: str = typer.Option("paper.docx", "--out", help="输出文件名，落在运行目录里"),
    style: str = typer.Option("", "--style", help="风格包名。给了就先通过它的检测，并套它的 Word 模板"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """把渲染好的论文导出成 Word（.docx），图片嵌入正文。

    **markdown 不是成品。**社科期刊投稿收的是 Word；
    审稿人、导师、合作者打开的也是 Word。链条的终点必须是一份
    能直接发给人看的文件，而不是一个文本文件加一个图片文件夹。

    七道门的退出码（7 锚点与研究类型 / 8 正文的因果说法一族——非因果论文下了因果结论，
    或因果论文弱工具没引 AR 区间 / 4 缺主结果表 / 5 没审过 / 3 还要复审 / 6 处方没处置 /
    1 风格包与 pandoc）在 `oss/api/paper.py`，这里只负责把它们原样交出去。
    """
    try:
        res = paper_api.export(run, out=out_name, style=style)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        # log 先打：这几行原先是**边走边打**的——被后面某道门拒绝时，
        # 它们那时候已经在屏幕上了。攒到最后一起打会把顺序改掉
        for stream, text in exc.data.get("log", []):
            typer.echo(text, err=(stream == "err"))
        if exc.code == 7:
            # 锚点门一次报全：idea 和方向一起漂时两条都要打
            for b in exc.problems:
                typer.echo(f"✗ 不给导出：{b}", err=True)
        elif exc.code == 6:
            # 处方门原先在循环里 raise，**只报第一条**——summary 就是那一条，
            # 其余几条在 `problems` 里给 --json 和 MCP 用
            typer.echo(f"✗ {exc.summary}", err=True)
        else:
            typer.echo(f"✗ {exc.summary}", err=True)
            for x in exc.problems:
                typer.echo(f"  · {x}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    for stream, text in d["log"]:
        typer.echo(text, err=(stream == "err"))
    if d["figures_missing"]:
        typer.echo(f"  ⚠ {d['fig_note']}——**有图没嵌进去**，检查文件名或路径", err=True)
    typer.echo(f"✓ {d['out']}  →  {d['path']}")
    typer.echo(f"  {d['fig_note']}")



#: 降档重试搬进了 oss/api/review.py；`tests/test_whole_output.py` 直接 import 这个名字，
#: 五个位置参数、返回判词，**签名与返回值都不许换**
_review_with_downgrade = review_api.with_downgrade



loop_app = typer.Typer(no_args_is_help=True, help="审稿—修改—复审循环")
app.add_typer(loop_app, name="loop")


@loop_app.command("status")
def loop_status_cmd(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """循环走到哪了：第几轮、设计级意见清零没有、因果审计 FAIL 后试过替代设计没有。

    退出码就是答案：0 收敛，3 还在循环里，4 轮次耗尽。
    **判断归审稿人，计数与放行归这里。**
    """
    try:
        res = loop_api.status(run)
    except ApiError as exc:
        # 只读的命令不许把打错的路径建成一个空运行目录（api/rundir.require_run）
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code)
    if json_out:
        jout.ok(res)
    d, dec = res["data"], res["decision"]
    typer.echo(f"决定 {dec['decision']}｜{dec['reason']}")
    typer.echo(f"轮次 {d['round']}/{d['max_rounds']}｜状态 {d['status']}｜因果审计检测 {d['identification_gate']}")
    for r in d["rounds"]:
        typer.echo(f"  第 {r['round']} 轮：{r['n_reviews']} 份审稿 {r['verdicts']}；"
                   f"设计 {r['design_open']} / 数据 {r['data_open']} / 文字 {r['text_open']}"
                   f" / 新发现 {r['new_open']} / 已处置 {r['resolved']}")
        for it in r["design_items"][:6]:
            typer.echo(f"      · {it}")
    if d["anchor"].get("direction"):
        typer.echo(f"  锚点：「{d['anchor']['direction'][:50]}」｜{d['anchor'].get('idea_id')}｜计划指纹 {d['anchor'].get('plan_sha')}")
    # 盘上的状态也打出来：模型每轮都会跑这条命令，让 run_state.json 在这里自证，
    # 省得它另去 cat 一个文件——**看不见的状态等于没有状态**。
    if d["state"]["stage"]:
        typer.echo(f"  阶段 {d['state']['stage']}（{d['state']['stage_status']}）｜冻结锚点 {d['state']['frozen_idea_id'] or '—'}")
    # 偏离在这里只是提醒（退出码留给循环状态）——但要每轮都看得见，
    # 别等到 paper export 被 7 号退出码拦下来才发现题已经换了
    for b in d["anchor_drift"]:
        typer.echo(f"  ⚠ 计划已偏离冻结锚点：{b}")
    for b in d["blockers"]:
        typer.echo(f"  ✗ {b}")
    if d["identification_note"] and d["identification_gate"] != "none":
        typer.echo(f"  {d['identification_note']}")
    typer.echo(f"→ {d['loop_state_path']}")
    if d["identification_gate"] == "fail_untried":
        # 因果审计 FAIL 不再拦导出（合格 = 写进局限），这里只提醒
        typer.echo("  ⚠ 因果审计判 FAIL、没换设计——允许，但每条审计意见都要在局限里交代")
    if d["exit_code"]:
        raise typer.Exit(d["exit_code"])


anchor_app = typer.Typer(no_args_is_help=True, help="原始问题的锚点：换题（需用户授权）")
app.add_typer(anchor_app, name="anchor")


@anchor_app.command("switch")
def anchor_switch(
    to: str = typer.Option(..., "--to", help="切换到的 idea_id"),
    why: str = typer.Option(..., "--why", help="为什么换：数据回答不了 / 功效 FAIL 换题 …"),
    authorized_by: str = typer.Option("", "--authorized-by", help="必须是 user：换题是用户的决定"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """换题。**模型不能授权自己换题**——没有 --authorized-by user 就拒绝。"""
    try:
        res = anchor_api.switch(run, to=to, why=why, authorized_by=authorized_by)
    except ApiError as exc:
        # 拒绝那一行留痕在 api 里写（MCP 调一次和这里跑一次，盘上一模一样）
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    typer.echo(f"✓ {res['summary']}")
    if d["rebuild_after_corrupt"]:
        typer.echo(f"  （run_state.json 曾损坏，本次为重建锚点；证据存为 {d['corrupt_evidence']}）")
    if d["direction_changed"]:
        typer.echo(f"  方向也变了：「{d['direction_changed']['from']}」"
                   f"→「{d['direction_changed']['to']}」")


@app.command()
def route(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    apply: str = typer.Option("", "--apply", help="应用这条处方的补丁到 plan.json（patch_plan / add_robustness）"),
    dismiss: str = typer.Option("", "--dismiss", help="驳回这条处方，必须配 --why"),
    disclose: str = typer.Option("", "--disclose", help="标记这条处方已写进局限，配 --why 说写了什么"),
    why: str = typer.Option("", "--why", help="驳回 / 写进局限的理由"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """诊断 → 处置。读 results.json / power.json / 审计意见，开出处方；一次一处置。

    退出码：0 没有待处置的 must 级处方；6 有。
    """
    try:
        # 三选一的判断在 api 里（`route_api.dispatch`），MCP 的 oss_route 共用同一份：
        # 同时给两个开关是参数错，退 2，不再静默只做第一个
        res = route_api.dispatch(run, apply=apply, dismiss=dismiss,
                                 disclose=disclose, why=why)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    # 打「做了什么」看的是 data.action（api 说哪一步真做了），不是命令行参数
    if d["action"] == "apply":
        typer.echo(f"✓ {d['applied_note']}")
    elif d["action"] == "dismiss":
        typer.echo(f"✓ 驳回 {d['action_id']}：{d['action_why']}")
    elif d["action"] == "disclose":
        typer.echo(f"✓ {d['action_id']} 标记为写进局限")
    if not d["items"]:
        typer.echo("没有处方：诊断里没有触发任何处置（或还没跑 oss run）")
        return
    mark = {"pending": "○", "applied": "✓", "dismissed": "✗", "disclosed": "≈", "resolved": "·"}
    for it in d["items"]:
        typer.echo(f"{mark[it['status']]} [{it['priority']}] {it['id']}  ← {it['trigger']}")
        if it["status"] == "pending":
            if it["evidence"]:
                typer.echo(f"      依据：{it['evidence'][:200]}")
            typer.echo(f"      处置：{it['advice']}")
        elif it["why"]:
            typer.echo(f"      {it['status']}：{it['why'][:160]}")
    typer.echo(f"→ {d['route_path']}")
    if d["pending_must"]:
        typer.echo(f"✗ {len(d['pending_must'])} 条 must 级处方待处置——paper check / export 会拦",
                   err=True)
        raise typer.Exit(6)
    typer.echo("✓ 没有待处置的 must 级处方")


@app.command("eval")
def eval_run(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    list_rules: bool = typer.Option(False, "--list", help="只列规则，不评"),
    behavior: bool = typer.Option(
        False, "--behavior",
        help="换一把尺子：不查产物合不合说明书，查**这次是怎么跑的**——"
             "审稿轮数、换题、问用户、探索、被拒、耗时",
    ),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """说明书的测试：对着运行目录核执行者有没有照说明书做。

    工具有五百多个测试，说明书一个都没有——听不听话只能事后翻对话记录。这里核的是
    留在目录里的产物：命令先后、估计量、处方状态、论文里有没有表、几位审稿人。

    `--behavior` 换一把尺子：产物看不出「换了 4 次题、审了 5 轮、被拒 25 次」，
    留痕看得出。指标全部从 trace.jsonl + run_state.json + loop_state.json 算，
    落 eval_behavior.json（**不覆盖 eval_report.json**，两把尺子分开量：
    往那一份里塞新条目会让「must 级全过」的意思悄悄改掉）。
    退出码：0 must 级全过；7 有 must 级没过。`--behavior` 另有两条 1：
    运行目录不存在（不建目录、不写报告——那样的「全过」是核账自己造出来的），
    以及全部指标都是 n/a（一条留痕都没有，那不是合格，是没交卷）。
    """
    try:
        if list_rules:
            res = evals_api.list_rules(behavior=behavior)
        elif behavior:
            res = evals_api.behavior(run)
        else:
            res = evals_api.run(run)
    except ApiError as exc:
        # 目录不存在就在这里结束：读的命令不许把打错的路径建成一个空运行目录
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    # 行为指标那张表的 id 更长，两侧的列宽本来就不一样——照旧
    width = 24 if behavior else 22
    if list_rules:
        for r in d["rules"]:
            typer.echo(f"  [{r['priority']:6s}] {r['id']:{width}s} {r['skill']:22s} {r['what']}")
        return
    mark = {"pass": "✓", "fail": "✗", "n/a": "·"}
    if behavior:
        typer.echo("行为指标（从 trace.jsonl + run_state.json + loop_state.json 算）")
        typer.echo(d["table"])
        typer.echo("")
    for it in d["items"]:
        typer.echo(f"{mark[it['status']]} [{it['priority']:6s}] {it['id']:{width}s} "
                   f"{it['detail'][:110]}")
    s = d["summary_counts"]
    typer.echo(f"→ {d['report_path']}｜过 {s['pass']} / 不过 {s['fail']} / 不适用 {s['n/a']}")
    what = "行为指标" if behavior else "规则"
    if d["must_failed"]:
        typer.echo(f"✗ {len(d['must_failed'])} 条 must 级{what}没过："
                   f"{'、'.join(d['must_failed'])}", err=True)
        raise typer.Exit(7)
    if behavior and d["undecidable"]:
        # 全部指标都是 n/a = 这个目录里一条留痕都没有，什么也没核成。
        # 「不过 0 条」于是成了一句没有内容的好消息——照判分器
        # （check_exam.Scorecard.judged）同一条规矩：没交卷不是合格。
        # **不写死条数**：自带想法把表从七条加到了九条，写死的数字迟早跟表对不上。
        typer.echo("✗ 判不了：全部指标都是 n/a——这个目录里没有可核的留痕，不是「全过」", err=True)
        raise typer.Exit(1)
    typer.echo(f"✓ must 级{what}全过")


style_app = typer.Typer(no_args_is_help=True, help="风格包：有模板按模板写")
app.add_typer(style_app, name="style")

mcp_app = typer.Typer(no_args_is_help=True, help="MCP 服务器：把 api 当工具交给别的宿主")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def mcp_serve() -> None:
    """在 stdin/stdout 上跑 MCP（stdio 传输）。

    **这条命令不打任何散文**——stdout 是协议通道，掺一个字就把消息流搅坏了，
    而表现是「客户端偶尔连不上」，最难查的那一类。
    宿主怎么接见 docs/user/mcp.md；工具与命令行调的是同一批 api 函数，
    所以「MCP 跑一次」和「命令行跑一次」盘上一模一样。
    """
    from oss.mcp.server import serve      # 延迟 import：mcp 坏了不该拖垮整个 CLI

    serve()



def _pack(name: str) -> Path:
    """`oss style build / template / check` 三条命令不在阶段 3 的子集里，退出码在这里翻译。"""
    try:
        return style_api.pack_dir(name)
    except ApiError as exc:
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc


@style_app.command("build")
def style_build(pack: str = typer.Argument(..., help="风格包名或路径，如 social-forces")) -> None:
    """从范文抽骨架，核对 rules.json。

    骨架（各节长度、图表数、句长、引用密度）是量出来的，归工具；
    rules.json 由 skill 读作者须知填——**填进去的每个数字必须能在须知原文里找到**。
    """
    pk = _pack(pack)
    prof = style_mod.build_profile(pk)
    typer.echo(f"✓ style_profile.json  →  {pk / 'style_profile.json'}")
    typer.echo(f"  范文 {prof['n_exemplars']} 篇，能用 {prof['n_usable']} 篇；节顺序 {prof['section_order']}")
    for k, v in prof["ranges"].items():
        if v:
            typer.echo(f"  {k:22s} {v['min']}–{v['max']}（中位 {v['median']}）")
    for n in prof["notes"]:
        typer.echo(f"  ⚠ {n}")
    if prof.get("citation_form"):
        typer.echo(f"  引用形式 {prof['citation_form']}")
    # 行文说明是判断，工具量不出来——写作 skill 读范文写。这里只报在不在、该读哪两篇
    notes = pk / "style_notes.md"
    if notes.exists():
        typer.echo("  ✓ style_notes.md（这本刊怎么讲故事）在")
    else:
        usable = sorted((p for p in prof["papers"] if p.get("words_body", 0) > 2000),
                        key=lambda p: -p["words_body"])[:2]
        names = "、".join(Path(p["file"]).with_suffix(".txt").name for p in usable) or "（没有能用的范文）"
        typer.echo(f"  ↳ 还没有 style_notes.md：写作前先读 exemplars/ 里这两篇的引言和讨论写一份：{names}")
    typer.echo(f"  ↳ 起稿模板：oss style template {pk.name} --out <运行目录>/paper.md")
    guide = pk / "guide.md"
    rules = pk / "rules.json"
    if not guide.exists():
        typer.echo("  · 没有 guide.md（作者须知）——硬规矩那几项检测不会生效，只有骨架警告")
    elif not rules.exists():
        typer.echo("  ↳ 有 guide.md 但还没有 rules.json：读须知填一份（oss schema rules --example），再跑一次 build 核对")
    else:
        problems = style_mod.check_rules_grounded(StyleRules.load(rules), guide.read_text(encoding="utf-8"))
        if problems:
            typer.echo("✗ rules.json 里有数字在须知原文里找不到：", err=True)
            for x in problems:
                typer.echo(f"  · {x}", err=True)
            raise typer.Exit(1)
        typer.echo("  ✓ rules.json 每个数字都能在须知原文里找到")


@style_app.command("template")
def style_template(
    pack: str = typer.Argument("", help="风格包名；留空按标准论文格式"),
    out: str = typer.Option("", "--out", help="写到文件（通常是运行目录的 paper.md）；缺省打印"),
    lang: str = typer.Option("zh", "--lang", help="模板语言：zh 缺省（编号节名、关键词、双语摘要）；en 英文"),
) -> None:
    """起稿模板：节的顺序和每节的目标词数按范文的多数派；没有范文按标准论文格式。

    风格包不只是门，是模具——**写之前**就决定长什么样。`oss paper check --style` 按同一个骨架查节。
    """
    profile = None
    if pack:
        pp = _pack(pack) / "style_profile.json"
        if not pp.exists():
            typer.echo(f"✗ {pack} 还没建包：oss style build {pack}", err=True)
            raise typer.Exit(1)
        profile = json.loads(pp.read_text(encoding="utf-8"))
    md = style_mod.template_markdown(style_mod.skeleton(profile, lang=lang), pack=pack, lang=lang)
    if out:
        Path(out).write_text(md, encoding="utf-8")
        typer.echo(f"✓ 模板 → {out}（{'按 ' + pack + ' 范文' if pack else '标准论文格式'}）")
    else:
        typer.echo(md)


@style_app.command("check")
def style_check(
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    style: str = typer.Option(..., "--style", help="风格包名"),
) -> None:
    """对着风格包查成稿：硬规矩违反拒绝、骨架偏离警告、跟范文连续 8 词相同拒绝。"""
    rundir = _run(run)
    src = rundir.file("paper_rendered.md")
    if not src.exists():
        typer.echo("✗ 缺 paper_rendered.md——先 oss paper render", err=True)
        raise typer.Exit(1)
    rules, profile, texts = style_mod.load_pack(_pack(style))
    problems, warnings = style_mod.check_paper(src.read_text(encoding="utf-8"), rules, profile, texts)
    for w in warnings:
        typer.echo(f"  ⚠ {w}")
    if problems:
        typer.echo(f"✗ 不合 {style} 的规矩：", err=True)
        for x in problems:
            typer.echo(f"  · {x}", err=True)
        rundir.append_trace(TraceEvent(seq=rundir.next_seq(), step="paper",
                                       command=f"oss style check --style {style}",
                                       outcome="refused", note="；".join(problems)[:300]))
        raise typer.Exit(1)
    rundir.append_trace(TraceEvent(seq=rundir.next_seq(), step="paper",
                                   command=f"oss style check --style {style}", outcome="ok",
                                   note=f"合 {style} 规矩；{len(warnings)} 条骨架警告"))
    typer.echo(f"✓ 合 {style} 的硬规矩，跟范文无连续 8 词重合；{len(warnings)} 条骨架提示")


# ------------------------------------------------------------- 宿主层命令


@app.command()
def review(
    file: list[str] = typer.Argument(None, help="要审的文件，可给多个"),
    level: str = typer.Option("normal", "--level", help="quick / normal / deep"),
    kind: str = typer.Option("generic", "--kind", help="审什么：ideas / paper / generic"),
    emphasis: str = typer.Option(
        "", "--emphasis",
        help="本轮侧重。多审稿人互盲时每人一条，**是工作透镜不是审稿人身份**",
    ),
    round_: int = typer.Option(1, "--round", min=1, help="第几轮审稿。改稿重审时递增，报告分文件存"),
    identification: bool = typer.Option(
        False, "--identification",
        help="审因果设计。自动带齐计划与结果（含机器已算的识别诊断）",
    ),
    paper: bool = typer.Option(
        False, "--paper",
        help="审论文。自动带齐要比对的产出物（稿子+计划+结果+功效+语料），"
             "等价于 --kind paper 加一串文件",
    ),
    desk: bool = typer.Option(
        False, "--desk",
        help="桌审：编辑十分钟决定退回还是送审。只看成稿；FAIL 就先改再送三位审稿人",
    ),
    alternatives: bool = typer.Option(
        False, "--alternatives",
        help="替代解释审稿人：主要结果还能被什么解释，每条附一个手头数据能做的检查。"
             "带成稿 + 计划 + 结果（含各批效应与成员名单）",
    ),
    editor: bool = typer.Option(
        False, "--editor",
        help="编辑综合：读本轮所有冻结的审稿报告，去重、排序、裁分歧，出一封决定信。"
             "在几位审稿人都审完之后跑",
    ),
    extra_round: bool = typer.Option(
        False, "--extra-round",
        help="轮次耗尽后再审一轮。必须配 --why，留痕。默认不允许——耗尽了就导出",
    ),
    why: str = typer.Option("", "--why", help="--extra-round 的理由"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """叫一个别家的模型来审。

    契约：只传路径不传摘要、每次开新线程、executor 与 reviewer **优先不同家族**。
    同家族或判不出家族时照审，判词记为「自审」、论文必须写进局限；
    `.env` 设 `REQUIRE_CROSS_MODEL=1` 才拒绝执行（正式验收跑用）。

    **审论文要用 `--paper`。**只把 `paper.md` 交出去审不了要紧的问题：
    「稳健性是不是事后补的」得对着 `plan.json` 里事前锁定的清单看，
    「局限写全没有」得对着 `power.json` 与 `literature.json` 的 notes 看。
    多文件是机械拼接原文，**不做摘要**——一总结就带上了调用方的口径。
    """
    try:
        # 运行目录要传：执行者身份的第三顺位是这个目录里 run_state.json 记下的
        rundir = _api_require_run(run)
        res = review_api.review(
            run, files=list(file or []), level=level, kind=kind, emphasis=emphasis,
            round_=round_, identification=identification, paper=paper, desk=desk,
            alternatives=alternatives, editor=editor, extra_round=extra_round, why=why,
            # **`Reviewer()` 留在这里、用模块级的这个名字**：审稿后端是 api 层唯一的
            # 网络接缝，而拦住它的办法是 `monkeypatch.setattr(cli, "Reviewer", _Fake)`
            # （`tests/test_review_structure.py` 等五处）。改成 api 内部去造，
            # 那几条测试就当场变成真连网。
            reviewer=Reviewer(rundir=rundir.path))
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        # log 先打：这几行原先是**边走边打**的——被后面某一步拒绝时，
        # 它们那时候已经在屏幕上了。攒到最后一起打会把顺序改掉
        for stream, text in exc.data.get("log", []):
            typer.echo(text, err=(stream == "err"))
        typer.echo(f"✗ {exc.summary}", err=True)
        # 审稿后端不可用的第二行原先就是**两个空格顶头、没有 ·**——照旧
        for x in exc.problems:
            typer.echo(f"  {x}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    for stream, text in d["log"]:
        typer.echo(text, err=(stream == "err"))
    typer.echo(f"{d['verdict']}  {d['reason']}")
    typer.echo(f"  第 {d['round']} 轮｜意见分类：设计 {d['kinds']['设计']}、"
               f"数据 {d['kinds']['数据']}、文字 {d['kinds']['文字']}、"
               f"新发现 {d['kinds']['新发现']}（不作为 FAIL 依据）"
               + ("  ← **设计级意见必须回到 plan.json 重跑，不能只改字**"
                  if d["kinds"]["设计"] else ""))
    typer.echo(f"  reviewer {d['reviewer_model']}（{d['reviewer_family']}）"
               f" × executor {d['executor_model']}（{d['executor_family']}）")
    for f in d["findings"]:
        typer.echo(f"  · {f}")
    typer.echo(f"→ {d['path']}")


@trace_app.command("save")
def trace_save(
    step: str = typer.Option(..., "--step"),
    note: str = typer.Option("", "--note"),
    run: str = typer.Option(DEFAULT_RUN, "--run"),
) -> None:
    """记一条留痕。"""
    rundir = _run(run)
    rundir.append_trace(TraceEvent(seq=rundir.next_seq(), step=step, note=note))
    typer.echo(f"✓ 已记录：{step}")


# --------------------------------------------------------------- 契约查询


@app.command()
def schema(
    artifact: str = typer.Argument(..., help=f"产出物名：{' / '.join(ARTIFACTS)}"),
    example: bool = typer.Option(False, "--example", help="给一份填好的样例，而不是 schema"),
    json_out: bool = typer.Option(False, "--json", help=JSON_HELP),
) -> None:
    """查某个产出物该长什么样。

    skill 写 ideas.json / plan.json / paper.md 时靠这个知道格式——
    **不该让它去读 Python 源码**。
    """
    try:
        res = schema_api.describe(artifact, example=example)
    except ApiError as exc:
        if json_out:
            jout.fail(exc)
        typer.echo(f"✗ {exc.summary}", err=True)
        raise typer.Exit(exc.code) from exc
    if json_out:
        jout.ok(res)
    d = res["data"]
    if d["kind"] == "prose":
        typer.echo(d["text"])
        return
    if d["kind"] == "example":
        # 打的是 pydantic 自己写的那个字符串（api 的 example_json），不是重新 dumps 一遍：
        # 两者不保证同一个字节，而散文一个字节都不许变
        typer.echo(d["example_json"])
        # 一份样例只装得下一种研究，而测量（造尺子 / 用尺子）与仿真（主张不主张代表真人）
        # 各有两种填法，所以另起段落给。**都走 stderr**：样例那一路的 stdout 必须是合法 JSON
        # （tests/test_smoke.py 按这个核）
        for key in ("measurement_examples", "simulation_examples"):
            if d.get(key):
                typer.echo(f"\n{d[key]}", err=True)
        return
    typer.echo(json.dumps(d["schema"], ensure_ascii=False, indent=2))
    if d["written_by"] == "skill":
        typer.echo(f"\n（{d['artifact']} 由 skill 写。`--example` 看填好的样例）", err=True)


# ------------------------------------------------------------- 安装与自检


@app.command("install-skills")
def install_skills(
    host: str = typer.Option("", "--host",
                             help="claude-code / dsh / codex / workbuddy / openclaw；不给就自动探测"),
    target: str = typer.Option("", "--target", help="覆盖宿主档案里的技能目录"),
    project: bool = typer.Option(False, "--project", help="铺到项目级技能目录，不铺全局"),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的同名 skill"),
    no_doc: bool = typer.Option(False, "--no-doc", help="不动 CLAUDE.md / AGENTS.md 的托管块"),
    clear_doc: bool = typer.Option(False, "--clear-doc", help="只删托管块，不铺技能"),
) -> None:
    """把自带的 skill **投影**成宿主的形状，铺到它能发现的位置。

    开发时 skill 在项目的 .claude/skills/ 跟 git 走；用户装完之后要能在**任意目录**使用，
    所以铺到全局（DECISIONS.md 1.7 的补充）。

    阶段 4 起铺之前先投影：dsh 不认 `allowed-tools`，那一行降级成注释；
    不替换 `$ARGUMENTS` 的宿主，说明书里必须写着这件事，没写就拒绝铺
    （投影规则见 oss/hosts/project.py，宿主事实见 oss/hosts/<名>.json）。
    """
    name, why = (host, "命令行 --host") if host else hosts.detect_reason()
    try:
        prof = hosts.load(name)
    except hosts.UnknownHost as exc:
        typer.echo(f"✗ {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"宿主：{prof.label}（{prof.name}）｜依据：{why}")

    doc_path = Path.cwd() / prof.managed_doc if prof.managed_doc else None

    if clear_doc:
        if doc_path is None:
            typer.echo(f"· {prof.label} 没有指令文件，没有托管块可删")
        else:
            try:
                typer.echo(host_doc.apply_to_file(doc_path, ""))
            except host_doc.DocConflict as exc:
                typer.echo(f"· 托管块没动：{exc}")
                raise typer.Exit(1) from exc
        return

    if target and project:
        typer.echo("✗ --target 与 --project 只能给一个", err=True)
        raise typer.Exit(2)

    if target:
        dst = Path(target).expanduser()
    elif project:
        p = prof.project_dir()
        if p is None:
            typer.echo(f"✗ {prof.label} 没有项目级技能目录，只能装到 {prof.user_dir()}", err=True)
            raise typer.Exit(2)
        dst = p
    else:
        dst = prof.user_dir()

    try:
        rep = host_project.build(prof, _skills_src(), dst, force=force)
    except host_project.BuildError as exc:
        typer.echo(f"✗ 投影失败：{exc}", err=True)
        raise typer.Exit(1) from exc

    for n in rep.skipped:
        typer.echo(f"· 跳过 {n}（已存在，--force 覆盖）")
    for n in rep.skills:
        typer.echo(f"✓ {n} → {dst / n}")
    if not rep.skills:
        # **不 return**：托管块还得刷。「先装了技能，后来才建 AGENTS.md，再跑一次」
        # 是最常见的顺序，早退会让这个项目永远没有托管块——而两次都显示成功了。
        typer.echo("没有新装任何 skill")
    elif rep.rewritten:
        typer.echo(f"  （{rep.rewritten} 个文件按 {prof.name} 的规则改写过："
                   f"allowed-tools 降级为注释）")

    if doc_path is not None and not no_doc:
        # 名单**整体排序**再给块：部分安装时是「新装的 + 跳过的」，全量重装时全在 skills 里，
        # 两次拼出来的顺序不同，块就会来回改一个「没变过」的项目的指令文件。
        names = sorted(rep.skills + rep.skipped)
        try:
            typer.echo(host_doc.apply_to_file(
                doc_path, host_doc.block_body(prof, names, dst)))
        except host_doc.DocConflict as exc:
            typer.echo(f"· 托管块没动：{exc}")


@app.command()
def doctor(
    host: str = typer.Option("", "--host", help="按哪个宿主自检；不给就自动探测"),
) -> None:
    """自检。装完第一件事该跑这个。

    **每一条红线都必须是这个宿主修得了的。**在 dsh 里查 `claude` 在不在 PATH，
    给出的是一条修不了、也不该修的红线；长期挂着假红线，真红线就没人看了。
    宿主该查什么写在 oss/hosts/<名>.json 的 doctor_checks 里。
    """
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        typer.echo(f"{'✓' if passed else '✗'} {label}{'  ' + detail if detail else ''}")

    check("Python 版本", sys.version_info >= (3, 11), f"{sys.version.split()[0]}")

    check("加载的是哪份代码", *_code_provenance())

    name, why = (host, "命令行 --host") if host else hosts.detect_reason()
    try:
        prof = hosts.load(name)
    except hosts.UnknownHost as exc:
        typer.echo(f"✗ 宿主  {exc}", err=True)
        raise typer.Exit(2) from exc
    check("宿主", True, f"{prof.label}（{prof.name}）——依据 {why}；不对就加 --host")

    for mod in ("pydantic", "typer", "pandas", "numpy"):
        try:
            __import__(mod)
            check(f"依赖 {mod}", True)
        except ImportError:
            check(f"依赖 {mod}", False, "未安装")

    try:
        __import__("pyreadstat")
        check("pyreadstat（.dta/.sav 支持）", True)
    except ImportError:
        typer.echo(
            f"· pyreadstat 未装——只能读 CSV/Excel（.dta/.sav 是社科数据的标准格式）。"
            f"装：{_install_cmd()} 'opensocialscience[stats-formats]'"
        )

    try:
        import pymupdf  # noqa: F401
        check("PyMuPDF（学术 PDF 抽全文）", True)
    except ImportError:
        typer.echo(f"· PyMuPDF 未装——全文抽取退回 pypdf，双栏 PDF 阅读顺序会乱。装：{_install_cmd()} 'opensocialscience[reading]'")
    if os.getenv("OPENALEX_API_KEY"):
        check("OpenAlex 密钥（文献主源）", True)
    else:
        typer.echo("· OPENALEX_API_KEY 未配——OpenAlex 自 2026-02-13 起要密钥，匿名基本不可用，"
                   "语料会退化成 Crossref 独撑（没摘要、没引文）。免费注册：https://openalex.org/settings/api，"
                   ".env 里填 OPENALEX_API_KEY=…")
    if os.getenv("S2_API_KEY"):
        check("Semantic Scholar 密钥（补摘要与一句话总结）", True)
    else:
        typer.echo("· S2_API_KEY 未配——Semantic Scholar 不会问（没密钥共用全局配额，实测每次 429）。"
                   "免费申请：https://www.semanticscholar.org/product/api#api-key-form，.env 里填 S2_API_KEY=…")
    for env, what in (("UNPAYWALL_EMAIL", "Unpaywall 全文源"), ("CORE_API_KEY", "CORE 全文源")):
        if not os.getenv(env):
            typer.echo(f"· {env} 未配——{what}不会用。.env 里填上通常能多拿到三成全文")
    # 三个数分开报：清单里 25 本、OpenAlex 里真有中文版源记录的只有 20 本。只报前一个数
    # 会让人以为那五本（经济研究、中国社会科学、中国行政管理、教育研究、开放时代）也查过了。
    if lit_module.ZH_SOURCES:
        typer.echo(f"· 中文文献：知网、万方无开放 API；OpenAlex 里 2018 年前的核心刊走 oss lit search --zh"
                   f"（白名单 {lit_module.ZH_JOURNALS} 本，有记录 {lit_module.ZH_COVERED} 本、"
                   f"{len(lit_module.ZH_SOURCES)} 条源记录），"
                   f"之后的用 oss lit import 导入知网/万方导出文件；只收中英文")
    else:
        typer.echo("· 中文文献：知网、万方无开放 API；中文核心刊白名单未构建——跑 scripts/build_zh_whitelist.py，"
                   "在那之前 oss lit search --zh 一条都查不到；2018 年后的用 oss lit import 导入知网/万方导出文件；只收中英文")

    try:
        import statspai
        check("StatsPAI（分批双重差分 did_cs）", True, getattr(statspai, "__version__", ""))
    except ImportError:
        typer.echo(
            "· StatsPAI 未装——政策分批实施时算不了 Callaway–Sant'Anna，只能用会出偏的 TWFE。"
            f"装：{_install_cmd()} 'opensocialscience[causal]'"
        )

    check("pandoc（论文导出 Word）", shutil.which("pandoc") is not None,
          "" if shutil.which("pandoc") else "brew install pandoc——没有它链条终点只是文本文件")

    skills_dir = prof.user_dir()
    installed = (skills_dir / "social-research").exists()
    check("skill 已铺到宿主目录", installed,
          str(skills_dir) if installed
          else f"{skills_dir} 里没有——跑 oss install-skills --host {prof.name}")
    # 「铺过了」不等于「铺的是当前这一版、当前这个宿主的形状」。**这一条查的是后者。**
    check("skill 跟源头一致", *_skills_drift(dst=skills_dir, profile=prof))

    rev = Reviewer(host=prof.name)
    if rev.is_configured():
        try:
            exe_fam, rev_fam = rev.check_family()
            who = (f"{rev.model}（{rev_fam}）× {rev.executor_model or '未知'}（{exe_fam}，"
                   f"来源 {rev.executor_source}）")
            if rev.cross_family(exe_fam, rev_fam):
                check("审稿", True, f"跨模型：{who}")
            else:
                # **不打红叉**：自审能用，只是不算独立验证。红叉会让人以为坏了、
                # 停下来去找配置问题——而这正是我们想拿掉的那道门槛。
                typer.echo(f"· 审稿（自审）  {who}")
                typer.echo("    审稿人与执行者同家族（或判不出家族）——能审，但不构成独立验证。")
                typer.echo("    论文局限里会自动写明这一点。想要更硬的验证：把 REVIEWER_MODEL "
                           "换成别家的模型。")
                typer.echo("    做正式验收跑时可在 .env 设 REQUIRE_CROSS_MODEL=1，"
                           "凑不齐两家就直接不让跑。")
        except SameFamilyRefused as exc:
            check("审稿", False, str(exc).splitlines()[0])
            # 只打提示那几行。切 str(exc)[1:] 的话，「同家族」那支没有提示，
            # 却会把消息第二行（改配哪个变量）当成提示多打一行。
            for line in exc.hint.splitlines():
                typer.echo(f"    {line}")
    else:
        looked = "、".join(envfile.search_paths_for_message())
        check("审稿", False,
              f"未配置 REVIEWER_BASE_URL / REVIEWER_MODEL / REVIEWER_API_KEY——"
              f".env 找过这些地方：{looked}")

    # 只跑这个宿主该跑的。别人家的红线不显示——不是藏起来，是它对这台机器没有意义。
    for cid in prof.doctor_checks:
        label, fn = host_checks.CHECKS[cid]
        check(label, *fn(prof))

    typer.echo("")
    typer.echo(f"工具侧已换真：{'、'.join(sorted(REAL_STEPS))}")
    typer.echo(f"工具侧还是假的：{'、'.join(_still_fake())}")
    typer.echo(
        f"skill 侧（由模型写，oss smoke 造不出真的）：{'、'.join(sorted(SKILL_WRITTEN))}"
    )
    typer.echo(f"  → 要验证 skill 侧，在 {prof.label} 里真跑一次 /social-research")
    if not ok:
        raise typer.Exit(1)


# ------------------------------------------------------------------ 冒烟


@app.command()
def smoke(
    run: str = typer.Option("runs/smoke", "--run"),
    keep: bool = typer.Option(False, "--keep", help="跑完不删产出"),
    online: bool = typer.Option(False, "--online", help="真调文献 API（默认用假语料，离线零成本）"),
) -> None:
    """跑完整条链并检查每个产出物都在。

    这就是「能跑通」的定义——只检查链条通了，不检查产出好不好。
    """
    # 跑完 `shutil.rmtree` 整个 --run 目录（除非 --keep）——**只删自己建的那个**。
    # 打错一个字（`--run runs/r20260826_edufert`）原先就是把一次真跑的全部产出
    # 连同 trace.jsonl 一起删掉，而留痕正是这个项目事后唯一能查的东西。
    # 目录已经在了就拒绝跑，不赌用户记得加 --keep：拒绝的代价是重敲一条命令，
    # 删错的代价是那次运行没了。
    if Path(run).exists():
        typer.echo(
            f"✗ {run} 已经存在——smoke 跑完要删掉整个运行目录，只删自己建的那个。"
            "换一个还不存在的路径，或者先自己把它删掉",
            err=True,
        )
        raise typer.Exit(1)
    rundir = _run(run)
    direction = "数字化对城乡收入不平等的影响"
    data = str(Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "tiny.csv")

    typer.echo(f"run: {rundir.path}\n")

    # ① 画像 —— 阶段 1 起是真的
    _emit(rundir, "profile", "oss profile", "profile", profile_file(data),
          note="smoke：数据是真的，产出是真的")
    # ② 文献
    corpus = lit_module.search([direction], limit=5) if online else fake.literature([direction])
    _emit(rundir, "literature", "oss lit search", "literature", corpus)
    # ②b 筛选 —— 逐篇「纳入/排除」是判断，归 skill；smoke 里没有模型，一律纳入充数
    _emit(rundir, "screening", "(skill)", "screening",
          Screening(direction=direction,
                    verdicts=[{"paper_id": r.paper_id, "verdict": "纳入"} for r in corpus.records]),
          note="FAKE：smoke 造的假筛选判定，没有模型参与")
    # 真跑一遍工具侧的校验与盖章：smoke 验的是「这条路走得通」，
    # 只把文件摆在那儿验不出 `oss lit screen` 是不是坏的
    # 筛选被拒（比如漏判了某篇）不能只在 stdout 上飘过去——smoke 的结论是
    # 「链条通没通」，这一步走不通就得跟 `_check` 一样非零退出，否则 CI 是绿的、链是断的
    try:
        for w in lit_api.screen(str(rundir.path))["data"]["warnings"]:
            typer.echo(f"  ⚠ {w}")
    except ApiError as exc:
        typer.echo(f"✗ 筛选：{exc.summary}", err=True)
        raise typer.Exit(1)
    # ③ idea —— 真实链路由 skill 写，smoke 里用假的顶替
    # 读书卡是 skill 写的，smoke 只造一张假的占位——跟 ideas/plan/paper 同理
    _emit(rundir, "reading", "(skill)", "reading",
          ReadingCards(direction=direction, cards=[{
              "paper_id": "FAKE_W0", "source_level": "metadata",
              "question": "FAKE_假问题", "relevance": "FAKE_直接"}]),
          note="FAKE：smoke 造的假读书卡，没有模型参与")
    ideaset = fake.ideas(direction)
    _emit(rundir, "ideas", "(skill)", "ideas", ideaset, note="FAKE：smoke 造的假 idea，没有模型参与")
    idea_id = ideaset.ideas[0].idea_id
    # ④ 绑定 + 功效
    _emit(rundir, "binding", "oss bind", "binding", fake.binding(idea_id), note="FAKE：smoke 造的假绑定")
    _emit(rundir, "power", "oss power", "power",
          power_mod.analyze(idea_id, 0.2, 100, discipline="economics", n_clusters=25))
    # ⑤ 计划（skill 写）+ 分析
    plan = fake.plan(idea_id)
    _emit(rundir, "plan", "(skill)", "plan", plan, note="FAKE：smoke 造的假计划，没有模型参与")
    _emit(rundir, "results", "oss run", "results", fake.results(plan.plan_id), note="FAKE：smoke 造的假结果")
    rt = route_mod.build(rundir.path)
    typer.echo(f"✓ {ARTIFACTS['route']}  →  {rundir.file(ARTIFACTS['route'])}（{len(rt.items)} 条处方）")
    # ⑥ 论文（skill 写）
    paper_path = rundir.file(ARTIFACTS["paper"])
    paper_path.write_text(fake.paper(direction, ideaset.ideas[0].title), encoding="utf-8")
    typer.echo(f"✓ {ARTIFACTS['paper']}  →  {paper_path}")

    # 检查
    typer.echo("")
    problems = _check(rundir)
    if problems:
        for p in problems:
            typer.echo(f"✗ {p}", err=True)
        raise typer.Exit(1)

    typer.echo(f"✓ 冒烟通过：链条从头走到尾，{len(ARTIFACTS) - 1} 个产出物齐全")
    typer.echo(f"  工具侧已换真：{'、'.join(sorted(REAL_STEPS))}")
    typer.echo(f"  工具侧还是假的：{'、'.join(_still_fake())}")
    typer.echo(f"  skill 侧（smoke 里恒为假）：{'、'.join(sorted(SKILL_WRITTEN))}")
    if not keep:
        shutil.rmtree(rundir.path, ignore_errors=True)


def _check(rundir: RunDir) -> list[str]:
    problems: list[str] = []
    for key, name in ARTIFACTS.items():
        if key == "trace":
            continue
        if not rundir.file(name).exists():
            problems.append(f"缺 {name}")

    if not problems:
        loaders = {
            "profile": DataProfile, "literature": LiteratureCorpus, "ideas": IdeaSet,
            "binding": BindingReport, "power": PowerReport, "plan": AnalysisPlan,
            "results": AnalysisResults,
        }
        for key, cls in loaders.items():
            try:
                cls.load(rundir.file(ARTIFACTS[key]))
            except Exception as e:  # noqa: BLE001 — 冒烟要报出所有问题，不能中断
                problems.append(f"{ARTIFACTS[key]} 格式不合契约：{e}")

        paper = rundir.file(ARTIFACTS["paper"]).read_text(encoding="utf-8")
        n_sections = paper.count("\n## ")
        if n_sections < 5:
            problems.append(f"paper.md 只有 {n_sections} 节，少于 5")

    return problems


def _still_fake() -> list[str]:
    return [k for k in ARTIFACTS if k not in REAL_STEPS | SKILL_WRITTEN and k != "trace"]


if __name__ == "__main__":
    app()
