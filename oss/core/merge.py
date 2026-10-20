"""把两份数据拼在一起，并且**把丢掉的东西摊开**。

拼数据本身 pandas 一行就够了。这个模块存在的理由不是「能拼」，是**拼完之后**。

真数据上量过一次：QoG 的各国民主化数据拼 Our World in Data 的互联网普及率，
两边都是「国家-年」，按国家名直接拼——

    5,726 行 / 190 国  →  4,276 行 / 160 国
    丢了 1,450 行（25.3%），30 个国家整个消失，**没有任何报错**

丢的是 `Korea (the Republic of)`、`Iran (Islamic Republic of)`、
`Congo (the Democratic Republic of the)` 这一批——名字带正式后缀的。
另一份写的是 `South Korea`、`Iran`、`Democratic Republic of Congo`。

**而且丢得不随机**：那 29 个国家里民主国家的比例比留下的低 10 个百分点，
而这份数据要研究的正是民主化。**等于挑着跟研究主题相关的那批扔掉。**

所以这里做三件 pandas 不做的事：

1. **算清楚两边各丢了什么**，并把对不上的键**列出来**
2. **查丢掉的那批跟留下的是不是系统性地不同**——这一条才是社科真正需要的。
   「丢了 25%」是个数字，「丢掉的那批民主国家少 10 个百分点」才是结论
3. **丢太多就拦住**，要接受得显式说出来

**永远不猜。**「Korea」到底是韩国还是朝鲜，猜错了比不拼更糟——
这是引用核验那次学到的：一个「帮你改正错误」的工具，
如果替人做了它不该做的认定，**它制造的错误比它拦下的更难被发现**。
所以对不上的名字只**列出来**、只给「待你确认的候选」，绝不自动套用。
"""

from __future__ import annotations

import difflib

import pandas as pd

#: 丢掉多少就拦住。不是随手定的：社科的二手数据合并，丢一成以上
#: 基本都意味着键的口径不一致（ISO 名 vs 通用名、行政区划改过），
#: 而不是「那些单位真的没数据」。要接受得用 `allow_loss` 显式说出来。
DEFAULT_MAX_LOSS = 0.10

#: 判「丢掉的那批是不是不一样」的门槛：标准化均值差。
#: 跟处理前平衡检验用的是同一个 0.25——**同一件事就该用同一把尺**。
SMD_ALERT = 0.25


class MergeRefused(ValueError):
    """丢得太多，拦下来。**说不该拼就说不该拼。**"""


def _norm(s: pd.Series) -> pd.Series:
    """键的安全归一化：去首尾空白、折叠内部连续空白。

    **只做机械上无争议的。**大小写也折叠——`USA` 和 `usa` 是同一个国家，
    这一条没有歧义。再往下（去掉 `(the)`、把 `Korea, Rep.` 映射成 `South Korea`）
    就是判断了，那必须由人显式给对照表。

    这里原先写的是 `if s.dtype == object:`。**在 pandas 3.0 上那一行永远为假**——
    字符串列的 dtype 从 `object` 改成了 `str`，于是整个归一化是死代码，
    `" USA "` 和 `"usa"` 拼不上，而且**不报错、不警告**，只是安静地少拼一批。

    是测试抓到的。改成按数值/时间反向判断：**不认识的一律当文本处理**，
    这样再出新的字符串类型也不会重演。
    """
    if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
        return s
    return s.astype(str).str.strip().str.replace(r"\s+", " ", regex=True).str.casefold()


def _candidates(missing: list[str], pool: list[str], k: int = 3) -> dict[str, list[str]]:
    """给对不上的键找几个长得像的。**这是待确认的候选，不是答案。**

    工具给候选、人做认定——这两件事必须分开。自动套用的话，
    `Korea (the Democratic People's Republic of)` 会被安静地接到 `South Korea` 上，
    而那种错误没有任何后续检查抓得到。
    """
    out: dict[str, list[str]] = {}
    for m in missing:
        hit = difflib.get_close_matches(str(m), [str(p) for p in pool], n=k, cutoff=0.6)
        if hit:
            out[m] = hit
    return out


def _attribute_loss(
    L: pd.DataFrame, R: pd.DataFrame, lk: list[str], rk: list[str],
    names: list[str], matched: pd.Series,
) -> list[str]:
    """没配上的行，**分别是因为哪个键**。

    第一版只报一个总数。真数据上那个数是 56.4%，看着像灾难，
    而它其实是两件完全不同的事叠在一起：

        1,450 行  国家名对不上   ← 真问题，要做对照表
        4,073 行  年份不重叠     ← 右表只从 1990 年开始，本来就没有

    **混成一个数，就既吓人又没法行动。**这跟秩亏归因是同一个道理：
    「矩阵不满秩」没有用，「是这两列完全共线」才有用。

    判据是机械的：对没配上的行，逐个键看它的取值**在对面整列里出不出现**。
    出不出现是事实，不是推断。
    """
    if matched.all():
        return []
    lost = L[~matched.to_numpy()]
    out: list[str] = []
    for a, b, shown_name in zip(lk, rk, names):
        theirs = set(R[b].dropna())
        n = int((~lost[a].isin(theirs)).sum())
        if not n:
            continue
        vals = sorted({str(v) for v in lost.loc[~lost[a].isin(theirs), a].dropna()})
        rng = ""
        nums = pd.to_numeric(pd.Series(vals), errors="coerce").dropna()
        if len(nums) == len(vals) and len(nums) > 1:
            rng = f"（{nums.min():g}–{nums.max():g}）"
        out.append(f"{n:,} 行是因为 `{shown_name}` 的取值在对面整列里都没出现{rng}，共 {len(vals)} 个不同取值")
    if not out:
        # 每个键单独看都对得上，是**组合**对不上——`country` 在、`year` 也在，
        # 缺的是「这个国家的这一年」。
        #
        # **这跟口径不一致是两回事，而且出路完全相反**：
        # 口径问题做张对照表就能救回来；组合缺失是那一格真的没数据，
        # 做多少对照表都没用，只能如实写进局限。
        # 分不清这两者的话，人会一直去改对照表，改到放弃为止。
        return [
            f"{len(lost):,} 行没配上，但**每个键单独看都在对面出现过**——"
            f"缺的是键的**组合**（比如某国某一年）。"
            f"**这不是口径问题，对照表救不回来**：那几格是真的没数据，只能写进局限"
        ]
    if len(out) > 1:
        return ["**没配上的行分别是因为**：" + "；".join(out)]
    return out


def _systematic_loss(
    left: pd.DataFrame, matched: pd.Series, keys: list[str]
) -> list[str]:
    """丢掉的那批，跟留下的是不是系统性地不同。

    **这一条是这个模块真正的价值。**「丢了 25%」只是个数字；
    「丢掉的那批里民主国家比例低 10 个百分点，而本研究研究的正是民主化」
    才是能改变结论的事实。

    逐个数值列比较标准化均值差。差得大的列要点名——
    **尤其当它就是处理变量或结果变量时。**
    """
    if matched.all() or not matched.any():
        return []
    kept, lost = left[matched], left[~matched]
    hits: list[tuple[float, str]] = []
    for col in left.columns:
        if col in keys:
            continue
        s = pd.to_numeric(left[col], errors="coerce")
        if s.notna().sum() < 10 or s.nunique(dropna=True) < 2:
            continue
        a, b = s[matched.to_numpy()], s[(~matched).to_numpy()]
        if a.notna().sum() < 5 or b.notna().sum() < 5:
            continue
        pooled = ((a.var(ddof=1) + b.var(ddof=1)) / 2) ** 0.5
        if not pooled or pooled != pooled:
            continue
        smd = abs(a.mean() - b.mean()) / pooled
        if smd >= SMD_ALERT:
            hits.append((smd, f"`{col}`（留下 {a.mean():.3g}，丢掉 {b.mean():.3g}，标准化差 {smd:.2f}）"))
    if not hits:
        return []
    hits.sort(reverse=True)
    return [
        "**丢掉的那批跟留下的系统性地不同**：" + "；".join(h for _, h in hits[:5])
        + "。这不是「少了一些数据」，是**样本被按某种特征筛过一遍**——"
        "如果其中有处理变量或结果变量，估计出来的效应带着这道筛子的偏。"
        "写进局限，或者先把键对齐再拼"
    ]


def merge_tables(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    left_on: list[str],
    right_on: list[str],
    how: str = "inner",
    allow_loss: float | None = None,
    crosswalk: dict[str, str] | None = None,
    left_name: str = "左表",
    right_name: str = "右表",
) -> tuple[pd.DataFrame, list[str]]:
    """按键拼两份数据。返回 `(拼好的表, 结论)`。

    `crosswalk` 是**显式的**键对照：`{左表的写法: 右表的写法}`。
    只作用在第一个键上（通常是单位名）。没有它就不做任何名字变换。
    """
    if len(left_on) != len(right_on):
        raise MergeRefused(f"两边的键数目对不上：{left_on} vs {right_on}")
    for cols, frame, who in ((left_on, left, left_name), (right_on, right, right_name)):
        miss = [c for c in cols if c not in frame.columns]
        if miss:
            raise MergeRefused(f"{who}里没有这些列：{'、'.join(miss)}")

    notes: list[str] = []
    L, R = left.copy(), right.copy()

    # 归一化后的临时键。**不改原列**——拼好的表里保留双方原来的写法，
    # 否则事后没人能回答「这一行到底是从哪两行拼出来的」。
    lk, rk = [], []
    for i, (a, b) in enumerate(zip(left_on, right_on)):
        ka, kb = f"__k{i}_l", f"__k{i}_r"
        L[ka], R[kb] = _norm(L[a]), _norm(R[b])
        lk.append(ka)
        rk.append(kb)

    if crosswalk:
        cw = {_norm(pd.Series([k]))[0]: _norm(pd.Series([v]))[0] for k, v in crosswalk.items()}
        before = L[lk[0]].isin(set(R[rk[0]])).sum()
        L[lk[0]] = L[lk[0]].map(lambda v: cw.get(v, v))
        after = L[lk[0]].isin(set(R[rk[0]])).sum()
        notes.append(f"用了 {len(crosswalk)} 条显式对照，能对上的行从 {before:,} 增到 {after:,}")

    # 一对多会让左表的行**变多**，那是另一种静默失败：
    # 「国家-年」拼「国家-年」本该一对一，多出来说明右表里有重复键。
    dup = int(R.duplicated(rk).sum())
    if dup:
        notes.append(
            f"⚠ {right_name}里有 {dup:,} 行的键是重复的——拼出来的行数会**多于**{left_name}。"
            f"确认那是不是本该唯一的键"
        )

    merged = L.merge(R, left_on=lk, right_on=rk, how=how, suffixes=("", f"_{right_name}"))
    matched_keys = set(map(tuple, R[rk].dropna().itertuples(index=False, name=None)))
    matched = pd.Series(
        [tuple(t) in matched_keys for t in L[lk].itertuples(index=False, name=None)],
        index=L.index,
    )

    kept_rows, total_rows = int(matched.sum()), len(L)
    lost_rows = total_rows - kept_rows
    rate = lost_rows / max(total_rows, 1)
    notes.append(
        f"{left_name} {total_rows:,} 行 × {right_name} {len(R):,} 行 "
        f"→ 拼出 {len(merged):,} 行；{left_name}里有 {lost_rows:,} 行没配上（{rate:.1%}）"
    )

    notes.extend(_attribute_loss(L, R, lk, rk, left_on, matched))

    # 对不上的键，两边都要报。**只报左边等于默认右表是全集**，而它通常不是。
    #
    # 报的是**数据里原本的写法**，不是归一化之后的。归一化会折叠大小写，
    # 打出 `bahamas (the)` 而文件里写的是 `Bahamas (the)`——
    # 而用户拿着这份清单是要去写对照表的，**给他看一个文件里搜不到的字符串，
    # 等于让他自己再猜一次**。
    def _orig(frame: pd.DataFrame, key: str, raw: str) -> dict[str, str]:
        return dict(zip(frame[key].astype(str), frame[raw].astype(str)))

    for who, frame, keycol, rawcol, othercol, other in (
        (left_name, L, lk[0], left_on[0], rk[0], R),
        (right_name, R, rk[0], right_on[0], lk[0], L),
    ):
        theirs = set(other[othercol].dropna())
        back = _orig(frame, keycol, rawcol)
        missing_norm = sorted({str(v) for v in frame[keycol].dropna()} - {str(v) for v in theirs})
        missing = [back.get(v, v) for v in missing_norm]
        if not missing:
            continue
        shown = "、".join(missing[:10])
        more = f" 等 {len(missing)} 个" if len(missing) > 10 else ""
        notes.append(f"{who}有 {len(missing)} 个键在对面找不到：{shown}{more}")
        back_other = _orig(other, othercol, right_on[0] if who == left_name else left_on[0])
        theirs = {back_other.get(str(v), str(v)) for v in theirs}
        cands = _candidates(missing[:20], [str(v) for v in theirs])
        if cands:
            pairs = "；".join(f"{k} → {'/'.join(v)}" for k, v in list(cands.items())[:6])
            notes.append(
                f"  长得像的（**这是待你确认的候选，不是答案**）：{pairs}。"
                f"确认之后写成对照表用 --map 传进来——**不要让工具替你认定**"
            )

    notes.extend(_systematic_loss(L.drop(columns=lk), matched, []))

    limit = DEFAULT_MAX_LOSS if allow_loss is None else allow_loss
    if how == "inner" and rate > limit:
        raise MergeRefused(
            f"{left_name}有 {rate:.1%} 的行没配上（超过 {limit:.0%}），**拒绝拼**。\n"
            + "\n".join(f"  · {n}" for n in notes)
            + f"\n\n丢一成以上通常不是「那些单位真的没数据」，"
            f"而是**键的口径不一致**——一边写 ISO 正式名、一边写通用名。\n"
            f"两条出路：① 按上面的候选做一张对照表，用 --map 传进来；"
            f"② 确实要接受这个损失，用 --allow-loss {min(rate + 0.01, 1.0):.2f} 显式说出来。\n"
            f"**不要绕过去**——丢掉的那批往往不是随机的"
        )

    merged = merged.drop(columns=[c for c in (*lk, *rk) if c in merged.columns])
    # 右表的键列跟左表同名时，pandas 会把右表那份带上后缀留下来
    # （`country_wb_it_net_user_zs`、`year_wb_…`）。它们跟左表的键完全冗余，
    # 却只在配上的行里有值——验证跑里画像把它们认成了个体/时间键，
    # 把 190 国 × 61 年的面板判成 189 × 31，执行者只好手写脚本删列。
    dup_keys = [f"{b}_{right_name}" for b in right_on if f"{b}_{right_name}" in merged.columns]
    if dup_keys:
        merged = merged.drop(columns=dup_keys)
        same = [k[: -len(right_name) - 1] for k in dup_keys]
        notes.append(f"右表的键列 {'、'.join(same)} 与左表同名，只保留左表那份")
    return merged, notes
