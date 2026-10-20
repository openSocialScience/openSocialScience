"""仿真货箱：契约、读箱、验货。**纯确定性，不打印、不留痕。**

设计稿 docs/design/2026-08-31-sim-intake.md 第三节的落地。
验货三态：problems（拒收，must）/ warnings（收但降级或记录）/ 全空（通过）。
rep < 3 不拒收——2026-08-31 用户拍板「收但降级」：诚实的方式不是拒收而是把话说软。

结构性缺失（目录、必需文件、非法 JSON）抛异常，内容缺陷进 problems：
这条缝对应 api 层的退出码——异常归 2 与 1，problems 归 7。
缺陷**一次报全**：各段检查互不吞并，用户不该修一处、再跑、再被下一处拒。
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

#: 配料单必填项（跑前人填的那一半，设计稿第三节）
META_REQUIRED = (
    "simulator", "simulator_version", "model", "model_provider", "temperature",
    "prompts", "population", "conditions", "n_reps", "rounds",
    "interview_plan", "contamination_note",
)

#: states.csv 的钥匙列；events.csv 把 wave 换成 step，另带 seq
STATE_KEYS = ("condition", "rep", "wave", "agent_id")
#: events.csv 的**去重**钥匙（对账、唯一性判断只看这 5 列）
EVENT_KEYS = ("condition", "rep", "step", "seq", "agent_id")
#: events.csv 的**必须齐全**的列——设计稿第三节钉的 8 列。比 EVENT_KEYS 宽：
#: sim_time / action / target 不参与去重，但列必须在，缺一样照样拒收。
#: 插头输出会再多一列 target_kind（第 9 列），这里不强求它在——
#: 那是插头本次终审加的信息列，不是货箱契约本身要求的最低限度
EVENT_REQUIRED = ("condition", "rep", "step", "seq", "sim_time", "agent_id",
                  "action", "target")
#: runs.csv 的必需列（complete 可缺，整列缺时全部当已完整）
RUNS_KEYS = ("file", "condition", "rep")

#: rep 降级线（可配是 V2 的事：V1 写死，misconfiguration 面还不存在）
REP_FLOOR = 3


@dataclass
class BoxVerdict:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    downgrade: bool = False          # rep 不足：写作须降级为「初步/探索性」
    single_condition: bool = False   # 单条件：限制论文类型
    meta: dict = field(default_factory=dict)


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    # utf-8-sig：手填 CSV 常出自 Excel、带 BOM；不带 BOM 的照读
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _empty(val) -> bool:
    """必填项的「空」：None、空串、空容器。0 与 False 是合法值（温度可以是 0）。"""
    return val is None or (isinstance(val, (str, dict, list)) and not val)


def _fmt_waves(waves) -> str:
    """波次集合转人话短语：数字按数值排序，非数字（罕见）弃在后面按字面排序。"""
    def key(w):
        return (0, int(w)) if w.isdigit() else (1, w)
    return "、".join(sorted(waves, key=key))


def _check_wave_coverage(v: BoxVerdict, rows: list[dict],
                          complete_pairs: set[tuple[str, str]]) -> None:
    """states.csv 已读入的行按 (condition, rep) 分组比 wave 覆盖——F1。

    2026-08-31 真跑验证：一个条件中转站断粮、访谈全死在 wave 0，runs.csv 却仍标
    complete，check 照样放行。这条补上「标 complete 的跑，波次集合必须一致」。

    参照集取 complete 组里的**众数集**（Counter 数出现次数最多的那个 frozenset），
    不取「波次最多」的最大集——2026-08-31 质量审给的反例：A 多跑出一波是异常，
    B、C 才是健康多数；若参照取最大集，A 会被追认成「标准」，反过来把健康的
    B、C 点名要求整改（措辞会变成「B 只有……」），事故跑倒告了正常跑的状。
    平票（出现次数并列）先比集合大小（大的优先），再比集合本身的排序键
    （数字按数值），确保结果确定可复现。

    消息里怎么称呼参照集的持有者，看它占 complete 组的份额：持有组占**严格多数**
    （>1/2）才许说「多数跑」——平票胜出（比如 2-2-1 里靠比大小赢的那个集合）算不上
    多数，说「多数」是谎报确信度（2026-08-31 规格复核注记）；不够严格多数时，把持有
    参照集的组**逐个点名**（单独一组持有时自然就是点名它一个，读者能看见具体是谁）。
    点名而不笼统称「多数」，也免得「点名参照」这个动作把健康的多数拖进指责里——
    这正是 5a 的反转案要防的事。不相交的两个集合谁都不是谁的子集，措辞一律用
    「有」，不用「只有」（「只有」暗示残缺，disjoint 只是不同，不是残）。

    没有 complete 组可比时（全部跑都未完成）这条检查故意保持沉默——没有一组
    诚实完整的基线可供对照，勉强点名不如不点；那些未完成的跑本来就已经被
    「未完整结束」那条警告单独覆盖了，不会被这里的沉默漏过。
    """
    wave_groups: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        w = (r.get("wave") or "").strip()
        if w:
            wave_groups.setdefault((r["condition"], r["rep"]), set()).add(w)
    complete_wave_groups = {k: s for k, s in wave_groups.items() if k in complete_pairs}
    # 单组：没有对照对象；没有 complete 组：没有诚实基线可对照——两种都不发声
    if len(wave_groups) <= 1 or not complete_wave_groups:
        return

    def wkey(w):
        return (0, int(w)) if w.isdigit() else (1, w)

    counts = Counter(frozenset(s) for s in complete_wave_groups.values())
    ref_frozen = max(counts, key=lambda s: (counts[s], len(s), tuple(sorted(s, key=wkey))))
    ref_set = set(ref_frozen)
    ref_count = counts[ref_frozen]
    mismatched = {k: s for k, s in complete_wave_groups.items() if s != ref_set}
    if mismatched:
        if ref_count * 2 > len(complete_wave_groups):
            parts = [f"多数跑有 wave {_fmt_waves(ref_set)}"]
        else:
            holders = sorted(k for k, s in complete_wave_groups.items() if s == ref_set)
            named = "、".join(f"{k[0]}/{k[1]}" for k in holders)
            parts = [f"{named} 有 wave {_fmt_waves(ref_set)}"]
        for k in sorted(mismatched):
            s = mismatched[k]
            verb = "只有" if s < ref_set else "有"
            parts.append(f"{k[0]}/{k[1]} {verb} wave {_fmt_waves(s)}")
        v.problems.append(
            "各次跑的访谈波次对不齐：" + "，".join(parts) +
            "——断粮或中途崩的跑要么补跑、要么在 runs.csv 标 complete=no")

    for k in sorted(wave_groups):
        if k in complete_pairs:
            continue  # complete 组已在上面比过，这里只看未完成组
        missing = ref_set - wave_groups[k]
        if missing:
            v.warnings.append(
                f"未完成的跑 {k[0]}/{k[1]} 缺 wave {_fmt_waves(missing)}——"
                "其数据默认不进主分析")


def _check_agent_coverage(v: BoxVerdict, rows: list[dict]) -> None:
    """states.csv 已读入的行按 (condition, rep, wave) 分组数 agent 集大小——T2b。

    起因：T2 给跑单的 perform_interview 包了兜底网，单个小人的模型调用出错时
    返回哨兵、不再拖垮整波——但 camel-oasis 的落库发生在模型调用**之后**，
    被兜底网接住的小人那一步根本没写 trace 行。结果是一整波「看起来齐全」
    （wave 号还在，_check_wave_coverage 的 wave 集合对账认不出任何异常），
    实际只剩少数几个小人的数据——2026-08-31 如果 B 组昨天的断粮跑接了这张
    还没打的兜底网，产出的大概率就是这种「wave 1 只有 2 个小人」却验货照样
    通过的半波，比整波全灭更隐蔽。

    参照值取**众数**大小（出现次数最多的 cell 人数），不取最大值——但众数要
    在**每个 (condition, rep) 组内部**分别算，不是全箱子混在一起算：
    2026-08-31 质量审抓到一个反例——A 组老老实实用 50 人的样本池、B 组用
    100 人的样本池，两组内部各自整整齐齐、毫无缺人，但混算全箱众数时
    3 个 cell 报 50、3 个 cell 报 100，出现次数打平，「更大的赢」的打平规则
    把 100 立成了全箱参照，于是 A 组三个健康波全部被冤枉成「缺人」。这跟
    T1「异常最大集反过来指责健康多数」是同一类事故的镜像：那次是错把
    「最大」当参照，这次是错把「全局」当分组范围——参照必须只在真正可比的
    同一组里算，跨组混算等于拿两个不同的度量单位比大小。组内比较则不会
    有这问题：B 组自己的众数就是 100（或者本例中的 5），不会被 A 组的数字
    污染，B/1 wave 1 缺人（2/5）这种真实的组内异常照样抓得到。
    人数是标量，众数打平只需再比大小（更大的赢），不需要 T1 那种「集合排序
    键」第三层——两个不同的整数不可能相等，打平只会发生在「出现次数」这一层。

    只警告，不拒收：**合法的未完成跑本来就会少人**（人没跑完，数据自然缺），
    这里没法从行数直接分辨「正常未完成」与「兜底网救场的隐形 miss」——
    证据链在 run_summary.json / packed 的 n_runs_with_summary（T2 的产物），
    这里只负责让海关看得见「哪些 wave 的人数不对劲」，不越权替用户下判断。

    每组内 cell 少于 2 个，或该组所有 cell 人数相等：这一组没有可比对象，
    不发声（同 T1 的沉默条款，逐组独立判断，不因为别的组吵就跟着吵）。
    空 wave 值不入 cell（同 states.csv 各处的规矩，不重复处理已由 n_unplaced 通道管）。
    """
    cell_agents: dict[tuple[str, str, str], set[str]] = {}
    for r in rows:
        w = (r.get("wave") or "").strip()
        if w:
            cell_agents.setdefault((r["condition"], r["rep"], w), set()).add(r["agent_id"])
    sizes: dict[tuple[str, str, str], int] = {k: len(agents) for k, agents in cell_agents.items()}

    by_group: dict[tuple[str, str], dict[str, int]] = {}
    for (c, r, w), n in sizes.items():
        by_group.setdefault((c, r), {})[w] = n

    for (c, r) in sorted(by_group):
        group_sizes = by_group[(c, r)]
        if len(group_sizes) < 2:
            continue  # 这一组只有一个 cell，没有可比对象
        counts = Counter(group_sizes.values())
        if len(counts) == 1:
            continue  # 这一组所有 cell 人数相等，没有短缺
        modal = max(counts, key=lambda sz: (counts[sz], sz))
        short = [(w, n) for w, n in group_sizes.items() if n < modal]
        if not short:
            continue
        waves = sorted(short, key=lambda item: (0, int(item[0])) if item[0].isdigit()
                        else (1, item[0]))
        phrase = "、".join(f"wave {w} 只有 {n}/{modal} 个小人" for w, n in waves)
        v.warnings.append(
            f"{c}/{r} 的 {phrase}——兜底记过 miss 的场次要在局限里说明")


def _check_table(v: BoxVerdict, path: Path, required: tuple[str, ...],
                  unique: tuple[str, ...] | None = None) -> tuple[bool, list[dict]]:
    """一张表的两条规矩：钥匙列齐、钥匙组合唯一。states 与 events 同一把尺子——

    但「齐」与「唯一」可以是两把不同宽窄的尺子：events.csv 要求 8 列全在
    （`required`），去重只看其中 5 列（`unique`，缺省与 `required` 相同，
    states.csv 就是这种缺省情形）。

    钥匙列的值去首尾空格再比（手填 CSV 的空格是常态）。
    返回 (这张表可用吗, 规范化后的行)，可用才参与后面的对账。
    """
    unique = unique or required
    header, rows = _read_csv(path)
    missing = [k for k in required if k not in header]
    if missing:
        v.problems.append(f"{path.name} 钥匙列不齐，缺 {'、'.join(missing)}")
        return False, rows
    for r in rows:
        for k in unique:
            r[k] = (r.get(k) or "").strip()
    seen = [tuple(r[k] for k in unique) for r in rows]
    if len(seen) != len(set(seen)):
        v.problems.append(f"{path.name} 的 {'×'.join(unique)} 不唯一——"
                          "同一处出现两行，翻译大概率出了错")
        return False, rows
    return True, rows


def check_box(box: Path | str) -> BoxVerdict:
    """验货。

    目录或必需文件不存在抛 FileNotFoundError（api 层翻成 code=2）；
    sim.meta.json 读不进抛 ValueError（api 层翻成 code=1）——
    手填 JSON 的尾逗号、全角引号是最常见的坏输入，报人话不报 traceback。
    """
    box = Path(box)
    if not box.is_dir():
        raise FileNotFoundError(f"货箱目录不存在：{box}")
    for name in ("sim.meta.json", "runs.csv"):
        if not (box / name).exists():
            raise FileNotFoundError(f"货箱缺 {name}：{box}")
    # 跑脚本两种合法布局：根目录一份通用 runner.py，或 raw/ 下逐条件另存的
    # *.py（模板另存出来的样子）——有一份就算数，不强求哪一种
    if not (box / "runner.py").exists() and not list((box / "raw").glob("*.py")):
        raise FileNotFoundError(
            f"货箱缺跑脚本：根目录 runner.py 或 raw/ 下的 *.py 至少要有一份：{box}")
    if not (box / "states.csv").exists() and not (box / "events.csv").exists():
        raise FileNotFoundError(f"states.csv 与 events.csv 至少要有一张：{box}")

    v = BoxVerdict()
    try:
        v.meta = json.loads((box / "sim.meta.json").read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"sim.meta.json 不是合法 JSON：{exc}") from exc
    if not isinstance(v.meta, dict):
        raise ValueError("sim.meta.json 顶层必须是一个对象 {…}，不是列表或字符串")
    for key in META_REQUIRED:
        if key not in v.meta or _empty(v.meta[key]):
            v.problems.append(f"配料单缺必填项「{key}」——论文方法节报不出它就是不诚实")

    # 两张数据表先查（不依赖 runs.csv，缺陷要一次报全）
    tables: dict[str, list[dict]] = {}
    for name, required, unique in (
        ("states.csv", STATE_KEYS, None),
        ("events.csv", EVENT_REQUIRED, EVENT_KEYS),
    ):
        if (box / name).exists():
            ok, rows = _check_table(v, box / name, required, unique)
            if ok:
                tables[name] = rows

    header, runs = _read_csv(box / "runs.csv")
    if not header:
        v.problems.append("runs.csv 是空文件——连表头都没有")
        return v
    missing = [k for k in RUNS_KEYS if k not in header]
    if missing:
        v.problems.append(f"runs.csv 列不齐，缺 {'、'.join(missing)}")
        return v
    if not runs:
        v.problems.append("runs.csv 里一条跑都没有——只有表头的清单不是运行记录")
        return v

    for r in runs:
        for k in RUNS_KEYS:
            r[k] = (r.get(k) or "").strip()
    bad = [i for i, r in enumerate(runs, 2) if any(not r[k] for k in RUNS_KEYS)]
    if bad:
        v.problems.append(f"runs.csv 第 {'、'.join(map(str, bad))} 条必填列有空——行截断或没填全")
    pairs = [(r["condition"], r["rep"]) for r in runs]
    if len(pairs) != len(set(pairs)):
        v.problems.append("runs.csv 里同一 condition×rep 登记了两遍")
    outside, not_in_box = [], []
    for r in runs:
        f = r["file"]
        if not f:
            continue  # 空列已按条号报过，这里只管指向
        if Path(f).is_absolute() or ".." in Path(f).parts:
            outside.append(f)
        elif not (box / f).exists():
            not_in_box.append(f)
    if outside:
        v.problems.append(f"runs.csv 里的 {'、'.join(outside)} 指到箱子外面——箱子必须自含")
    if not_in_box:
        v.problems.append(f"runs.csv 里的 {'、'.join(not_in_box)} 不在箱子里")

    conditions = sorted({r["condition"] for r in runs if r["condition"]})
    reps_per_cond = {c: {r["rep"] for r in runs if r["condition"] == c}
                     for c in conditions}
    n = v.meta.get("n_reps")
    if isinstance(n, int) and not isinstance(n, bool) and n >= 1:
        if reps_per_cond:
            m = max(len(r) for r in reps_per_cond.values())
            if m != n:
                v.problems.append(
                    f"runs.csv 与配料单对不上：配料单说每条件 {n} 次，清单里最多 {m} 次")
    elif "n_reps" in v.meta and not _empty(n):
        # 缺失或空已由必填项检查报过，这里只报「有但类型不对」，一件缺陷一条问题
        v.problems.append(f"配料单 n_reps 必须是正整数，现在是 {n!r}（引号里的数字不算数）")

    # complete 列缺失时整列当已完整（docstring 的规矩）；absent 方向只数 complete
    # 为真的跑——complete=no 的跑本来就该没有数据行，不该再报一遍「没数据」
    if "complete" in header:
        complete_pairs = {(r["condition"], r["rep"]) for r in runs
                          if (r.get("complete") or "").strip().lower() in ("yes", "true", "1")}
    else:
        complete_pairs = set(pairs)

    # 对账：数据表 ↔ 运行清单，两个方向都查
    registered = set(pairs)
    for name, rows in tables.items():
        got = {(r["condition"], r["rep"]) for r in rows}
        extra = sorted(got - registered)
        if extra:
            v.problems.append(f"{name} 里出现了 runs.csv 没登记的 condition×rep："
                              + "、".join(f"{c}/{r}" for c, r in extra))
        absent = sorted(complete_pairs - got)
        if absent:
            if not got:
                v.problems.append(
                    f"{name} 一行数据都没有，而 runs.csv 登记了 {len(registered)} 次跑")
            else:
                v.warnings.append(f"这些登记的跑在 {name} 里一行数据都没有："
                                  + "、".join(f"{c}/{r}" for c, r in absent))

    if (not complete_pairs and registered and tables
            and all(not rows for rows in tables.values())):
        # complete_pairs 空时上面的 absent 恒空——「全未完成」不该白白吞掉「数据表也是空的」
        # 要 ALL 张收集到的表都空才算：两张表一张有数据、一张没有，
        # 那是后面「登记的跑缺数据行」该管的警告，不该被这条更硬的规则误伤
        v.problems.append(
            "所有跑都标了未完成，数据表又是空的——这个箱子没有可用数据")

    # 波次覆盖对账（F1）：complete 组之间访谈波次必须一致——2026-08-31 真跑验证，
    # 一个条件中转站断粮、访谈全死在 wave 0，runs.csv 却仍标 complete，check 放行了。
    # 只看 states.csv（events.csv 用 step 不用 wave，不参与）；空 wave 值不入集合，
    # 那是 n_unplaced 通道的地盘。算法细节（众数参照、点名规则）见 _check_wave_coverage。
    if "states.csv" in tables:
        _check_wave_coverage(v, tables["states.csv"], complete_pairs)
        # 波内人数覆盖（T2b）：兜底网救活的小人不写 trace 行，wave 集合看不见
        # 半波——算法细节见 _check_agent_coverage。
        _check_agent_coverage(v, tables["states.csv"])

    if "complete" in header:
        # 有 complete 列时，空格子按「没跑完」算：既然作者建了这列，空白更像没填完
        incomplete = [r for r in runs
                      if (r.get("complete") or "").strip().lower() not in ("yes", "true", "1")]
        if incomplete:
            v.warnings.append(
                f"{len(incomplete)} 次跑未完整结束——其数据默认不进主分析，写进局限")
    short = sorted(c for c, reps in reps_per_cond.items() if len(reps) < REP_FLOOR)
    if short:
        v.downgrade = True
        v.warnings.append(
            f"条件 {'、'.join(short)} 重复不足 {REP_FLOOR} 次——收，但结果只能作"
            f"「初步/探索性」表述，局限必写「单次模拟无法区分随机波动与真实规律」"
            f"（2026-08-31 用户拍板）")
    if len(conditions) < 2:
        v.single_condition = True
        v.warnings.append("只有一个条件——做不了机制型对比，论文类型与措辞受限（记入 run_state）")
    return v
