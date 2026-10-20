"""诊断图。**不是装饰，是最快的错误检测器。**

回归表回答不了「这个模型对不对」，图能。GSS 真跑时的现成例子：
论文假定「均衡作用随队列**线性**衰减」，写成一个三重交互项，
**却从未验证过它真的是线性的**。一张按队列分箱的图一眼就能看出
是平滑下滑、断崖、还是非单调。（PLAN.md 九之二）

同理：`masei10` 缺失六成是靠手写脚本才发现的；`region` 只有 4 个簇也是。
这些都该是画像跑完就能看见的东西。

**这是工具箱不是 skill**——从产出物确定性地画图，属于「算」。
判断「该看哪张图、图说明了什么」才是 skill 的活。

matplotlib 是可选依赖：装不上不影响任何分析，只是画不出图。
"""

from __future__ import annotations

import json
import re
import textwrap
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

from oss.core.contracts import DataProfile

# ────────────────────────────────────────────── 发表样式（进论文的四种图）
#
# 验证跑的英文 Word 里嵌了四张图，标题、坐标轴全是中文，没人拦。
# 进论文的图（系数、逐年展开、稳健性、分箱斜率）从这里起分两种语言画，
# 英文版用期刊的样子：衬线字体、去上右边框、300 dpi、另存矢量 PDF、
# 色盲也分得开的颜色（Okabe–Ito）、**图里不放标题和解说**——那些是图注的事。
# 每张图旁边写一份 fig_<name>.json：语言、建议图注。英文稿嵌中文图，校验按它拦。
# 诊断图（结构、缺失、簇、VIF）是给执行者看的，照旧中文、不进论文。

_CJK = re.compile(r"[一-鿿]")
_SERIF = ("Times New Roman", "Times", "Nimbus Roman", "Liberation Serif", "STIXGeneral", "DejaVu Serif")
_MAIN, _OTHER, _PRE, _POST = "#D55E00", "#0072B2", "#D55E00", "#0072B2"
_PALETTE = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9")

# ────────────────────────────────────── 两套体例（2026-09-24 照实物定）
#
# 拿真发表的论文量出来的，不是照指南猜的：
#
# **中文**（《管理世界》2015、《公共行政评论》2021、《华南师大学报（社科版）》2023，
# 四张图）：**一张彩色都没有**；系列靠黑／灰阶／线型分；图四周有完整边框；
# 图内不放标题，图题在图下方居中排成「图 N　题目」；误差棒是虚线。
#
# **英文**（Social Forces 六篇）：彩色**可以**用（六篇里一篇用了 Stata s2color），
# 但三篇是灰阶的；图题在图下方左对齐排成「**Figure N.** Title.」；只留左、下两条轴线。
#
# 所以两边分开走。**但有一条是两边共同的**：图要经得起黑白印刷。
# 我们原来的橙 `#D55E00` 与蓝 `#0072B2` 是按色盲可分选的（Okabe–Ito），
# 转成灰度只差 119 vs 87——**12% 的明度差，印在纸上等于没有区别**。
# 色盲友好和黑白友好是两件事，从前只解决了前一件。所以英文这边保留颜色，
# **另外再加点形与线型**：在线看是彩色，印出来或复印照样分得开。
_GRAY_MAIN, _GRAY_OTHER = "#000000", "#7F7F7F"
_GRAY_SERIES = ("#000000", "#707070", "#A9A9A9", "#404040", "#8C8C8C", "#C0C0C0")
#: 点形与线型：中英文都用，**这是不靠颜色也分得开的那一半**
_MARKERS = ("o", "s", "^", "D", "v", "P")
_LINESTYLES = ("-", "--", "-.", ":", (0, (3, 1, 1, 1)), (0, (5, 2)))


def _is_zh(lang: str) -> bool:
    return lang != "en"


def _series_style(lang: str, i: int) -> dict:
    """第 i 条系列的画法。中文走灰阶，英文走颜色——两边都带点形与线型。"""
    colors = _GRAY_SERIES if _is_zh(lang) else _PALETTE
    return {"color": colors[i % len(colors)],
            "marker": _MARKERS[i % len(_MARKERS)],
            "linestyle": _LINESTYLES[i % len(_LINESTYLES)]}


def _pair_colors(lang: str) -> tuple[str, str]:
    """「高亮的」与「其余的」两色。中文是黑与灰，英文是橙与蓝。"""
    return (_GRAY_MAIN, _GRAY_OTHER) if _is_zh(lang) else (_MAIN, _OTHER)

_T: dict[str, dict[str, str]] = {
    "zh": {
        "coef_x": "系数（点估计与 95% 置信区间）",
        # **中文期刊的图里不放标题**，图题排在图下方（《管理世界》《公共行政评论》
        # 《华南师大学报》四张图都是这样）。图题照旧写进旁注 JSON——导出时 pandoc
        # 把图片替代文字排成 Word 题注，正好落在图下方（见 `manuscript.fold_captions`）。
        "coef_title": "",
        # 高亮的说明原先只写在图注里。**图会被单独拿去用**（贴进 PPT、发给同事），
        # 那时图注不在身边，高亮就成了一个没人解释得了的深浅——跟 `mg_y` 那次同一类。
        "coef_focal": "主效应", "coef_other": "其他系数",
        "main": "主规格", "nores": "  跑不动，无结果",
        "rob_x": "“{key}”的系数（点估计与 95% 置信区间）", "rob_title": "",
        "ev_x": "相对政策实施的期数（第 −1 期为参照）", "ev_y": "效应",
        "ev_title": "", "ev_none": "没有逐年展开的结果",
        # 口径写进**纵轴标签**，不写进标题。两件事都要顾：期刊体例要求图内无标题，
        # 而「图被单独拿去用时身上只剩那行字」这条理由一点没变——
        # 二元斜率和带控制变量的斜率**是两个不同的量**，认错了整张图就读反了。
        # 轴标签既合体例、又跟着图走，比标题更该放这个信息。
        "mg_y": "“{treatment}”对“{outcome}”的斜率（二元，不含控制变量）",
        "mg_y_adj": "“{treatment}”对“{outcome}”的斜率（含控制变量与固定效应）",
        "mg_title": "", "mg_title_adj": "",
        "mg_none": "样本不足，画不出分箱斜率", "全样本": "全样本",
        "cap_coef": "主规格各系数的点估计与 95% 置信区间（n = {n}），处理变量的系数高亮。",
        "cap_rob": "“{key}”的系数在主规格与预先登记的各稳健性规格下的点估计与 95% 置信区间。",
        "cap_ev": "相对处理时点各期的效应估计与 95% 置信区间，第 −1 期为参照；处理前各期检验平行趋势。",
        "cap_mg": "按 {by} 分箱，各箱内“{outcome}”对“{treatment}”的二元斜率与 95% 置信区间（不含控制变量与固定效应）。",
        "cap_mg_adj": "按 {by} 分箱，各箱内“{outcome}”对“{treatment}”的斜率与 95% 置信区间；各箱内含主规格的控制变量与固定效应，与主规格是同一个量。",
    },
    "en": {
        "coef_x": "Coefficient (point estimate and 95% CI)", "coef_title": "",
        "coef_focal": "Focal estimate", "coef_other": "Other coefficients",
        "main": "Main specification", "nores": "  not estimable",
        "rob_x": "Coefficient on {key} (point estimate and 95% CI)", "rob_title": "",
        "ev_x": "Periods relative to treatment (period −1 omitted)", "ev_y": "Estimated effect",
        "ev_title": "", "ev_none": "No event-study estimates",
        "mg_y": "Slope of {outcome} on {treatment} (bivariate)",
        "mg_y_adj": "Slope of {outcome} on {treatment} (with controls and fixed effects)",
        "mg_title": "", "mg_title_adj": "",
        "mg_none": "Too few observations for binned slopes", "全样本": "Full sample",
        "cap_coef": "Coefficient estimates from the main specification with 95% confidence intervals "
                    "(n = {n}). The treatment coefficient is highlighted.",
        "cap_rob": "Estimated coefficient on {key} across the main specification and the pre-registered "
                   "robustness specifications, with 95% confidence intervals.",
        "cap_ev": "Event-study estimates by period relative to treatment, with 95% confidence intervals; "
                  "period −1 is the omitted reference. Estimates for pre-treatment periods test for "
                  "differential pre-trends.",
        "cap_mg": "Within-bin bivariate slopes of {outcome} on {treatment} across levels of {by}, "
                  "with 95% confidence intervals (no controls or fixed effects).",
        "cap_mg_adj": "Within-bin slopes of {outcome} on {treatment} across levels of {by}, "
                      "with 95% confidence intervals. Each bin is estimated with the main specification's "
                      "controls and fixed effects, so the slopes are comparable to the main estimate.",
    },
}


def _t(lang: str, name: str, **kw) -> str:
    return _T["en" if lang == "en" else "zh"][name].format(**kw)


# ────────────────────────── 画布尺寸（2026-09-23 那批，09-25 移植过来）
#
# 依据：仓库里 6 篇 Social Forces 的 19 张真图，用 PyMuPDF 量版面尺寸与印刷字号。
# **已发表的 19 张没有一张超过 4.71 英寸，我们真跑的 7 张里有 3 张超了，最宽 9.41 英寸。**
# 字号在代码里定死，图一超宽就被整体缩放——印出来的字号成了缩放比的副产品，
# 同一篇论文里从 5.7pt 到 9.4pt 都有。已发表的能稳定在 7–8pt，是因为作者**按最终尺寸画**。

FIGURE_MAX_WIDTH = 4.33

#: `bbox_inches="tight"` 每边加的留白（英寸）。matplotlib 缺省 0.1，一张图就多 0.2 英寸——
#: 量宽度时不把它算进去，钳出来的图会**稳定地超上限 0.2**（实测 4.33 钳完存出来 4.51）。
_PAD = 0.02


@contextmanager
def max_width(inches: float | None):
    """临时改画布宽度上限——风格包的 `figure_max_width`（版心不同的刊自己写）。

    跟 `abstract_max_words`、`reference_style` 同一条路：**门是通用的，数字是每本刊自己的**。
    """
    global FIGURE_MAX_WIDTH
    old = FIGURE_MAX_WIDTH
    if inches and float(inches) > 0:
        FIGURE_MAX_WIDTH = float(inches)
    try:
        yield
    finally:
        FIGURE_MAX_WIDTH = old


#: 等比缩最多把高度缩到原来的几成。**高度塌了的图一个数都读不出**——
#: 纵轴标签会叠在一起，点估计与区间全被压没。
_MIN_HEIGHT_SHARE = 0.85
#: 高度到底之后还是太宽时，纵轴标签的字号最小能缩到几磅。再小就看不清了。
_MIN_TICK_FONT = 5.5


def _fit(fig, *, max_width: float | None = None, rounds: int = 8) -> None:
    """把**存出来的**宽度压进上限。

    只钳 `figsize` 不够：`bbox_inches="tight"` 连画到画布之外的东西一起算
    （长的 y 轴标签、图例、题注），成品比画布宽——实测 `fig_robustness` 画布 4.33 英寸、
    存出来 5.57 英寸。这里按真实的 tight 边框等比缩回去。

    **减不是乘**：溢出的是 y 轴标签、图例这些按磅走的东西，它们占的英寸数基本与画布宽度无关，
    所以「画布 = 上限 − 溢出」一步就到位；按比例缩会越缩溢出占比越大，收敛不了
    （实测按比例缩三轮，`fig_structure` 停在 5.79 英寸下不来）。
    溢出本身就超过上限时退回按比例缩——那种图只能整体缩小。
    """
    cap = (max_width or FIGURE_MAX_WIDTH) - 2 * _PAD      # 留白也占宽度
    h0 = fig.get_size_inches()[1]
    for _ in range(rounds):
        fig.canvas.draw()
        bb = fig.get_tightbbox(fig.canvas.get_renderer())
        if bb.width <= cap + 0.01:
            return
        w, h = fig.get_size_inches()
        # **高度不许塌。**溢出的是按磅走的文字时，等比缩越缩越不够，
        # 八轮下来画布只剩原高的零头——纵轴标签叠在一起，图就废了。
        # 宽一点的图总还读得出数；高度塌了的图一个数都读不出。**宁可超宽。**
        # 钳在缩**之前**：放在缩之后检查，最后一轮照样冲过头（实测 6.36 → 3.50 英寸）。
        #
        # **但也不能拿宽度换高度。**超宽的图排进 Word 会被整体缩放、字跟着变小，
        # 那是这个上限存在的理由。高度到底之后还太宽，说明溢出的是纵轴标签本身——
        # 那就缩标签的字号。这是 docstring 里那句「真正的解法是把标签写短」的自动版：
        # 字号小一点，标签占的英寸数就少，高度和宽度两头都保得住。
        if h <= h0 * _MIN_HEIGHT_SHARE + 1e-9:
            if not _shrink_ticks(fig):
                return
            continue
        over = bb.width - w
        target = cap - over
        if target < cap * 0.4:
            # 光是标签就快把宽度占满：只能整体缩，收敛慢，所以轮数给够。
            # 这种图的绘图区会很窄——**真正的解法是把标签写短**（plan.labels），不是缩图
            target = w * cap / bb.width
        # 等比缩会连高度一起带下去——钳住，宁可超宽也不让高度塌
        new_h = max(h * target / w, h0 * _MIN_HEIGHT_SHARE)
        fig.set_size_inches(min(target, w * new_h / h), new_h)


def _title(ax, text: str, fontsize: float = 10) -> None:
    """设子图标题，**按这个子图实际有多宽折行**。

    并排两栏时每个子图只有画布的一半宽，而中文标题动辄二十几个字——
    「设计效应 = 1+(m−1)·ρx·ρu（m=91, ρu=0.05）」在 2 英寸宽的子图上要占 3.9 英寸，
    于是两个标题横着撞在一起（`fig_clusters` 三份 demo 都这样）。
    折行之后标题变高不变宽，撞不上了；高度由调用方留足。
    """
    import textwrap

    fig = ax.get_figure()
    w_in = fig.get_size_inches()[0] * ax.get_position().width
    width = max(8, int(w_in * 72 / fontsize))
    ax.set_title("\n".join(_wrap_cjk(text, width)), fontsize=fontsize)


def _shrink_ticks(fig, step: float = 0.5) -> bool:
    """把纵轴刻度标签的字号调小一档。已经到底了返回 False。"""
    changed = False
    for ax in fig.axes:
        for lab in ax.get_yticklabels():
            size = lab.get_fontsize()
            if size - step >= _MIN_TICK_FONT:
                lab.set_fontsize(size - step)
                changed = True
    return changed


def _wrap_chars(fig, fontsize: float) -> int:
    """这块画布一行放得下几个汉字。**折行宽度要跟着画布算，不能写死。**

    结构图的判定依据原先写死 `textwrap.wrap(plain, 62)`——那是照 11 英寸画布调的，
    画布缩到 4.33 英寸之后，这一行字本身就有 7.7 英寸宽，把图整个撑开
    （`_fit` 也救不回来：文字按磅走，缩画布不缩字）。
    汉字宽约等于字号，所以「画布磅数 ÷ 字号」就是一行的字数。
    """
    return max(16, int(fig.get_size_inches()[0] * 72 / fontsize))


#: 纵轴标签最多占画布宽度的几成。剩下的留给绘图区——
#: 标签占满了，图就只剩一条缝，`_fit` 缩多少都救不回来。
_LABEL_SHARE = 0.38
#: 森林图另给一档：它的纵轴标签就是内容本身（每一行是一个规格），
#: 而 x 轴范围窄，绘图区让一点读得起。
_ROB_LABEL_SHARE = 0.50


def _thin_xticks(ax, fig, fontsize: float = 7) -> None:
    """横轴刻度按画布宽度稀释。

    matplotlib 缺省给到 ~9 档，那是照宽画布来的；画布压到 4.33 英寸之后
    「−0.008−0.006−0.004」会连成一片，读不出来。按「一个刻度标签占多少英寸」算得下几档。
    """
    from matplotlib.ticker import MaxNLocator

    # 标定：已发表的图在 4.3 英寸上放 6 档（soad064 那张横轴 6 个刻度）。
    # 一个刻度标签约 7 个字符宽，字符宽约半个字号——一路除下来 4.3 英寸给到 5–6 档。
    # 先前多除了个 1.6，出来只剩 2 档（「-0.15」「0.00」），比不稀释还难读
    per_label = 7 * fontsize / 2 / 72
    ax.xaxis.set_major_locator(MaxNLocator(nbins=max(3, int(fig.get_size_inches()[0] / per_label / 2.2))))


def _wrap_labels(labels: list[str], fig_width: float, fontsize: float = 7,
                 share: float | None = None) -> list[str]:
    """把过长的纵轴标签折行，**而不是把整张图缩小**。

    `_fit` 对长标签救不回来：轴区按画布比例缩，标签按磅走，**越缩溢出占比越大**——
    实测中文稳健性图缩到 2.44 英寸画布、标签还占 1.86 英寸，绘图区只剩一条缝，
    而且收敛到 4.30 就不动了。已发表的图标签都很短（「US」「Denmark」），
    长标签该折行。汉字宽约等于字号，西文约半个。
    """
    budget = fig_width * (share or _LABEL_SHARE) * 72 / fontsize   # 能放几个汉字
    out = []
    for s in labels:
        cjk = len(_CJK.findall(s))
        width = int(budget if cjk > len(s) / 3 else budget * 2)   # 西文一行能多放一倍
        out.append("\n".join(_wrap_cjk(s, max(8, width))) if len(s) > width else s)
    return out


def _canvas(w: float, h: float, *, max_width: float | None = None) -> tuple[float, float]:
    """画布尺寸：超宽就**等比缩**，宽高比不动。

    等比缩而不是只截宽度：只截宽度会把宽图压成方图，长的 y 轴标签挤不下。
    等比缩之后画布变小而字号（磅）不变，相对字号自然变大——这正是「按最终尺寸画」的效果，
    也是已发表的图能稳定在 7–8pt 的原因。
    """
    cap = max_width or FIGURE_MAX_WIDTH
    if w <= cap:
        return w, h
    return cap, h * cap / w


def _pub(plt, lang: str):
    """发表样式的 rc 上下文。英文用衬线字体；中文用 CJK 无衬线（衬线中文字体机器上多半没有）。

    **边框两边不一样，这是照实物定的**：中文期刊的图四周有完整边框
    （《公共行政评论》的平行趋势图、《华南师大学报》的柱状图都是），
    Social Forces 只留左、下两条。两种都是对的，对的是各自那一边。
    """
    from matplotlib import font_manager

    have = {f.name for f in font_manager.fontManager.ttflist}
    zh = _is_zh(lang)
    rc = {
        # 字号照实测定：三篇 Social Forces 量出来是 7.0 / 8.0 / 8.0pt，很集中。
        # 画布锚定到 4.33 英寸之后 10pt 偏大——**字号要跟着最终尺寸定**，不是反过来
        "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 8.5,
        "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
        "axes.spines.top": zh, "axes.spines.right": zh,
        "axes.unicode_minus": False, "mathtext.fontset": "stix",
        "legend.frameon": False,
    }
    if zh:
        rc["font.family"] = "sans-serif"
        rc["font.sans-serif"] = [f for f in _CJK_FONTS if f in have] + ["DejaVu Sans"]
        rc["axes.edgecolor"] = "#000000"
        rc["axes.linewidth"] = 0.8
    else:
        rc["font.family"] = "serif"
        rc["font.serif"] = [f for f in _SERIF if f in have] + ["DejaVu Serif"]
    return plt.rc_context(rc)


def _legend(ax, lang: str, *, handles=None, ncol: int = 2) -> None:
    """图例按体例放。

    中文期刊把图例排在**图的下方、横向一行**（《华南师大学报》那张柱状图、
    《管理世界》那三个子图都是）；Social Forces 放在图内。
    两边都不画边框——`legend.frameon` 已经关掉了。
    """
    kw = {"handles": handles} if handles is not None else {}
    if _is_zh(lang):
        ax.legend(**kw, loc="upper center", bbox_to_anchor=(0.5, -0.16),
                  ncol=ncol, frameon=False, fontsize="small")
    else:
        ax.legend(**kw, loc="best", frameon=False, fontsize="small")


def _ascii_label(label: str, fallback: str) -> str:
    """英文图上的标签不能有中文。稳健性的 label 来自计划（`R1 去掉控制`），
    留它的前导 ASCII 部分（`R1`）；一个 ASCII 字符都没有就用兜底名。"""
    m = re.match(r"[^一-鿿]+", label)
    head = (m.group(0) if m else "").strip(" ：:（(")
    return head or fallback


def _save_pub(fig, out: Path, *, kind: str, lang: str, caption: str,
              extra: dict | None = None) -> Path:
    """存 PNG（300 dpi）+ PDF（矢量）+ 旁注 JSON（语言、建议图注）。"""
    # 图注是一段话，不能带换行：折行过的标签会把 \n 原样带进来
    # （真跑上见过「Overwork threshold 60\nhours per week」）
    caption = " ".join(caption.split())
    out.parent.mkdir(parents=True, exist_ok=True)
    _fit(fig)
    fig.savefig(out, dpi=300, bbox_inches="tight", pad_inches=_PAD)
    pdf: Path | None = out.with_suffix(".pdf")
    try:
        fig.savefig(pdf, bbox_inches="tight", pad_inches=_PAD)
    except Exception:  # noqa: BLE001 —— 矢量版存不了不该让位图版也没了
        pdf = None
    import matplotlib.pyplot as plt

    plt.close(fig)
    meta = {"kind": kind, "lang": lang, "caption": caption, "png": out.name,
            "pdf": pdf.name if pdf else "", "dpi": 300}
    if extra:
        meta.update(extra)
    out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return out

#: 中文标签需要 CJK 字体，否则全是方框。**方框比没有图更糟**——
#: 它看起来像图能用，实际读不出来。按平台常见字体依次尝试。
_CJK_FONTS = (
    "PingFang SC", "Hiragino Sans GB", "Heiti SC", "STHeiti",           # macOS
    "Noto Sans CJK SC", "WenQuanYi Zen Hei", "Source Han Sans SC",      # Linux
    "Microsoft YaHei", "SimHei",                                        # Windows
    "Arial Unicode MS",
)


class FiguresUnavailable(RuntimeError):
    """没装 matplotlib。**这不是错误，是能力缺失**——照实说，别静默跳过。"""


def _plt():
    try:
        import matplotlib
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise FiguresUnavailable(
            "画诊断图需要 matplotlib，当前没装。它是可选依赖，装不上不影响任何分析。"
        ) from exc
    matplotlib.use("Agg")
    from matplotlib import font_manager, pyplot as plt

    have = {f.name for f in font_manager.fontManager.ttflist}
    picked = [f for f in _CJK_FONTS if f in have]
    if picked:
        plt.rcParams["font.sans-serif"] = picked + ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


#: 标注一律加白底。**被柱子压住的警告等于没有警告**——
#: 而这几张图存在的全部理由就是让人一眼看见问题。
_BOX = {"boxstyle": "round,pad=0.3", "facecolor": "white", "alpha": 0.85, "edgecolor": "none"}


def _note(ax, text: str, color: str = "#333", *, plot_share: float | None = None) -> None:
    """在左上角写一条标注，并把 y 轴顶端抬高，保证它不被数据压住。

    去掉 markdown 强调标记——**图不是 markdown**，`**` 会原样画上去。

    **必须折行。**不折行的话，一条中文长注就是一条极宽的单行；它按磅走、
    不随画布缩，于是 `_fit` 为了把宽度压进上限只能一轮轮等比缩整个画布，
    每缩一轮溢出占比反而更大——八轮之后高度塌成一条缝，
    纵轴标签全叠在一起，点估计与区间根本看不见。
    三份 demo 的稳健性森林图**全都是这样出来的，而且直接嵌进了成品 Word**。
    """
    import textwrap

    text = text.replace("**", "")
    fig = ax.get_figure()
    # 注释占的是**这个子图**的绘图区宽度，不是整张画布。两处都踩过：
    # · 按整张画布算 → 森林图（标签占 50%）折出来的注释比绘图区还宽，撑爆 tight 边框，
    #   `_fit` 为了压回宽度把整张图缩掉四成半（实测 6.36 → 3.50 英寸），标签就全叠了；
    # · 并排两栏时子图只有半张画布宽 → 注释整条溢出到子图外面（`fig_clusters` 的右栏）。
    # `plot_share` 再扣掉纵轴标签那一块，缺省按通用的标签占比。
    ax_in = fig.get_size_inches()[0] * ax.get_position().width
    width = max(10, int(ax_in * 72 / 8 * (plot_share or (1 - _LABEL_SHARE))))
    body = "\n".join(textwrap.wrap(text, width)) or text
    # **按注释真实占几行让位，不是拍一个比例。**拍比例时四行的注释照样压住第一行数据
    # （demo 的森林图就是这样：主规格那个点整个藏在注释框底下）。
    # 注释按磅走、绘图区按英寸走，两者的比才是要让出的那一份。
    # 用 `get_position()` 而不是 `get_window_extent()`：后者要先 draw，
    # 而且拿到的是当时的像素数，`_fit` 之后就不作数了。
    # **量出来，不要估。**估算（按行数 × 字号 ÷ 绘图区高）在行数多、画布又被 `_fit`
    # 缩过的图上会差那么一点，而差一点就是注释框压住最上面一两行数据——
    # 测量那篇的森林图六行注释、十六行数据，估出来差了半行，主规格与第一个效标都被盖住。
    #
    # 办法：先画上去，再量它在**数据坐标**里的下沿，抬 y 轴顶端直到下沿高过最上面那个点。
    # 数据坐标是稳的：`_fit` 缩画布不改 ylim，抬完就一直有效。
    lines = body.count("\n") + 1
    ax_in = max(fig.get_size_inches()[1] * ax.get_position().height, 0.5)
    frac = min(0.6, ((lines + 0.5) * 1.45 + 0.6) * 8 / 72 / ax_in + 0.03)
    lo, hi = ax.get_ylim()
    ax.set_ylim(lo, hi + (hi - lo) * frac / max(1 - frac, 0.2))
    txt = ax.text(0.02, 0.98, body, transform=ax.transAxes, fontsize=8,
                  color=color, va="top", bbox=_BOX)
    top_data = max((max(l.get_ydata(orig=False), default=lo) for l in ax.lines), default=lo)
    for _ in range(4):
        fig.canvas.draw()
        bb = txt.get_window_extent(fig.canvas.get_renderer())
        bottom = ax.transData.inverted().transform((0, bb.y0))[1]
        gap = top_data - bottom
        if gap < -0.25:                      # 下沿已经高过最上面那个点，且留了四分之一行
            break
        lo, hi = ax.get_ylim()
        ax.set_ylim(lo, hi + (gap + 0.5) * 1.15)


def _save(fig, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    _fit(fig)
    fig.savefig(out, dpi=140, bbox_inches="tight", pad_inches=_PAD)
    import matplotlib.pyplot as plt

    plt.close(fig)
    return out


# ────────────────────────────────────────────────────── ⑫ 结构证据


def structure(frame: pd.DataFrame, profile: DataProfile, out: Path) -> Path:
    """结构判定的证据图。**判错结构是后面一切的地基塌了。**

    左：每期行数。时间序列每期约 1 行；每期几十上百行的是重复截面。
    右：每个「个体」出现在几期。全都出现在所有期、而又没有任何一列在个体内稳定，
        就是编号被回收的特征（GSS 的 `id` 每轮从 1 重编）。
    """
    plt = _plt()
    time = profile.structure.time_id
    unit = profile.structure.unit_id or _guess_unit(frame, profile)

    fig, axes = plt.subplots(1, 2, figsize=_canvas(11, 4))
    ax = axes[0]
    if time and time in frame.columns:
        counts = frame.groupby(time).size()
        ax.bar(range(len(counts)), counts.values, color="#4C72B0")
        ax.set_xticks(range(len(counts)))
        ax.set_xticklabels([str(x) for x in counts.index], rotation=45, ha="right", fontsize=7)
        ax.axhline(1, color="#C44E52", ls="--", lw=1)
        _note(ax, "红线 = 时间序列的每期行数（约 1）", "#C44E52")
        ax.set_title(f"每期行数（时间列「{time}」）", fontsize=10)
        ax.set_ylabel("行数")
    else:
        ax.text(0.5, 0.5, "没有时间列", ha="center", va="center")
        ax.set_axis_off()

    ax = axes[1]
    if unit and time and unit in frame.columns and time in frame.columns:
        per_unit = frame.groupby(unit)[time].nunique()
        ax.hist(per_unit.values, bins=range(1, int(per_unit.max()) + 2),
                align="left", rwidth=0.8, color="#55A868")
        ax.set_title(f"每个「{unit}」出现在几期", fontsize=10)
        ax.set_xlabel("期数")
        ax.set_ylabel("个数")
        share = float((per_unit > 1).mean())
        _note(ax, f"跨期出现的占 {share:.0%}")
    else:
        ax.text(0.5, 0.5, "没有个体标识", ha="center", va="center")
        ax.set_axis_off()

    # **判定依据要完整写出来，不能截断。**这张图的价值一半在这句话上。
    head = f"结构判定：{profile.structure.kind}"
    # 判定依据里带 markdown 强调标记，画到图上要去掉——图不是 markdown
    plain = profile.structure.detection_note.replace("**", "")
    # 62 是照 11 英寸画布调的——画布压到 4.33 之后这一行字本身就有 7.7 英寸宽，
    # 把图整个撑开，`_fit` 也救不回来（文字按磅走，缩画布不缩字）。跟着画布算
    body = "\n".join(textwrap.wrap(plain, _wrap_chars(fig, 8)))
    fig.suptitle(f"{head}\n{body}", fontsize=9, y=1.02, va="bottom")
    return _save(fig, out)


def _guess_unit(frame: pd.DataFrame, profile: DataProfile) -> str | None:
    """结构判成重复截面时 `unit_id` 是 None，但**被否掉的那个候选正是要画的东西**。"""
    for v in profile.variables:
        if v.semantic_type == "identifier" and v.name in frame.columns:
            return v.name
    return None


# ────────────────────────────────────────────────────── ⑬ 缺失模式


#: 一张图最多画几行。再多就既看不清也印不下——**画不下的图不是图**。
_MAX_ROWS = 25


def _top_rows(series):
    """按值取前 `_MAX_ROWS` 行，返回 `(留下的, 没画的个数)`。

    留下的按升序排（`barh` 从下往上画，升序才让最高的在最上面）。
    """
    if len(series) <= _MAX_ROWS:
        return series.sort_values(), 0
    top = series.sort_values(ascending=False).head(_MAX_ROWS)
    return top.sort_values(), len(series) - _MAX_ROWS


def missing(frame: pd.DataFrame, profile: DataProfile, out: Path) -> Path:
    """缺失率 × 时间的热力图。**块状缺失和随机缺失的含义完全不同。**

    「整体缺三成」和「前两轮一条都没有」是两回事：后者意味着这个变量
    在早期队列上根本不可用，任何跨时期的比较都要重新划窗口。
    只看整体缺失率永远看不出这个区别。
    """
    plt = _plt()
    time = profile.structure.time_id
    cols = [c for c in frame.columns if c != time]

    if not time or time not in frame.columns:
        rates = frame[cols].isna().mean().sort_values()
        rates, left = _top_rows(rates)
        fig, ax = plt.subplots(figsize=_canvas(7, max(3, 0.28 * len(rates))))
        ax.barh(range(len(rates)), rates.values, color="#C44E52")
        ax.set_yticks(range(len(rates)))
        ax.set_yticklabels(rates.index, fontsize=8)
        ax.set_xlabel("缺失率")
        _title(ax, "各变量缺失率（无时间列，画不了块状缺失）")
        if left:
            _note(ax, f"另有 {left} 个变量缺失率更低，未画在这里——按缺失率取了最高的 {_MAX_ROWS} 个")
        return _save(fig, out)

    periods = sorted(frame[time].dropna().unique())
    # 按整体缺失率取最高的若干个：**画不下的图不是图**。
    # 三百多个变量画出来是九十多英寸高、标签糊成一片（实测 575 × 13574 像素），
    # 排进论文完全不能看，而「哪些变量缺得多」恰恰是这张图唯一要回答的问题。
    order = frame[cols].isna().mean().sort_values(ascending=False)
    kept, left = _top_rows(order)
    cols = list(kept.index)
    grid = np.array([[frame.loc[frame[time] == p, c].isna().mean() for p in periods]
                     for c in cols])

    fig, ax = plt.subplots(figsize=_canvas(max(6, 0.42 * len(periods)), max(3, 0.3 * len(cols))))
    im = ax.imshow(grid, aspect="auto", cmap="Reds", vmin=0, vmax=1)
    ax.set_xticks(range(len(periods)))
    ax.set_xticklabels([str(p) for p in periods], rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(cols)))
    ax.set_yticklabels(cols, fontsize=8)
    fig.colorbar(im, ax=ax, label="缺失率")
    _title(ax, f"缺失率 × {time}（整块深色 = 该变量在那几轮没问）")
    if left:
        _note(ax, f"另有 {left} 个变量缺失率更低，未画在这里——按缺失率取了最高的 {_MAX_ROWS} 个")
    return _save(fig, out)


# ────────────────────────────────────────────────────── ⑭ 簇结构


def clusters(frame: pd.DataFrame, cluster_col: str, out: Path, *, icc: float = 0.05) -> Path:
    """簇大小分布 + 设计效应曲线。

    两件事一眼可见：
    **① 簇数够不够**——低于约 30 时聚类稳健标准误本身是有偏的，
    小样本校正只能缓解不能消除。GSS 的 `region` 只有 4 个。
    **② 簇有多大**——设计效应是 `1+(m−1)·ρx·ρu`，簇越大惩罚越重。
    """
    plt = _plt()
    sizes = frame.groupby(cluster_col).size()
    g, m = len(sizes), float(sizes.mean())

    # 并排两栏在 4.33 英寸的版心里各只剩不到两英寸——中文标题比子图还宽。
    # 高度给足（折行之后的标题要占两三行），横向间距拉开（右栏的 y 轴标签原先
    # 顶进了左栏的绘图区）。
    fig, axes = plt.subplots(1, 2, figsize=_canvas(11, 5.6))
    fig.subplots_adjust(wspace=0.45, top=0.80)
    ax = axes[0]
    ax.hist(sizes.values, bins=min(30, max(5, g // 2)), color="#4C72B0")
    _title(ax, f"「{cluster_col}」的簇大小分布：{g} 个簇，均 {m:.0f}")
    ax.set_xlabel("每簇观测数")
    ax.set_ylabel("簇数")
    warn = "簇数低于 30，聚类稳健标准误本身有偏" if g < 30 else f"簇数 {g}，够用"
    _note(ax, warn, "#C44E52" if g < 30 else "#55A868")

    ax = axes[1]
    rho_x = np.linspace(0, 1, 101)
    deff = 1 + (m - 1) * icc * rho_x
    ax.plot(rho_x, deff, color="#C44E52")
    ax.set_xlabel("处理变量的组内相关 ρx")
    ax.set_ylabel("设计效应")
    _title(ax, f"设计效应 = 1+(m−1)·ρx·ρu（m={m:.0f}, ρu={icc}）")
    ax.axvline(0, color="#999", ls=":", lw=1)
    _note(ax, "ρx→0：个体层处理变量，几乎不打折\nρx→1：簇层处理变量，满额打折")
    return _save(fig, out)


# ────────────────────────────────────────────────────── ⑱ 稳健性森林图


def _main_outcome(results) -> str:
    return str(getattr(getattr(results, "spec", None), "outcome", "") or "").strip()


def _other_outcome(run, main: str = "") -> bool:
    """这一项换过结局变量吗。

    空串 = 没换（`_run_robustness` 只在换了时才填，老结果文件里这一格也是空的）。
    填了的话仍跟主结局比一次：别的写入路径可能把结局原样记上，
    那种「填了但跟主结果一样」不该被当成换过。
    """
    o = str(getattr(run, "outcome", "") or "").strip()
    return bool(o) and o != main


def _robustness_note(results, lang: str = "zh") -> tuple[str, int]:
    """森林图上那句「还有几项没画在这儿、为什么」。返回 (文字, 被排除的项数)。

    **换了结局就不是同一个估计量。**验收跑 r20260907_edufert_zh 的五项稳健性里
    三项换了结局（15–19 岁组生育率、30–34 岁组生育率、中学毛入学率），
    单位分别是每千名女性的生育数与入学率百分点，跟主结果的总和生育率不可比。
    它们被画在同一根轴上，把范围撑到 ±10，**真正可比的那两项被压成看不见的点**——
    读者看到的是「主估计精确、其它规格乱飞」，而事实恰恰相反。

    排除不等于藏起来：排除了就要在图上说出来，否则读者以为清单只有这几项。
    """
    main = _main_outcome(results)
    others = [lb for lb, r in results.robustness.items() if _other_outcome(r, main)]
    if not others:
        return "", 0
    if lang == "en":
        # 英文图也得说——排除掉却什么都不说，是把「少画了三项」变成读者看不见的事。
        # **但不点名**：稳健性 label 来自计划文件，可能是中文的，点名会把中文漏进英文图
        # （英文图不许有中文，见 test_发布图英文版没有中文且带旁注）。数量和理由才是要害。
        text = (f"{len(others)} further check(s) replace the outcome variable; their units "
                f"differ from the main estimate and are not comparable on this axis "
                f"— see the robustness table.")
        return "\n".join(textwrap.wrap(text, width=72)), len(others)
    names = "、".join(f"「{lb}」" for lb in others[:3])
    tail = "等" if len(others) > 3 else ""
    text = (f"另有 {len(others)} 项换了结局变量（{names}{tail}），单位与主结果不同，"
            f"不能同轴比较，未画在这里——见稳健性表")
    return "\n".join(_wrap_cjk(text, 40)), len(others)


def _crit(results) -> float:
    """区间临界值：按抽样设计时用 t（自由度 = 抽样单位数 − 层数，`spec.t_crit`），否则 1.96。"""
    spec = getattr(results, "spec", None)
    return spec.t_crit if spec is not None and spec.t_crit else 1.96


def robustness(results, out: Path, *, key: str = "main", lang: str = "en",
               labels: dict[str, str] | None = None) -> Path:
    """主结果与各项稳健性并排，带 95% 置信区间。

    **一句「同号同显著」藏得住太多东西**：量级掉了一半算不算稳健？
    某一项的置信区间跨过零算不算？并排画出来，读者自己就能判断，
    而不是只能相信作者的形容词。

    跑不动的那几项也画进去（标成灰色）——**它们没有结果本身就是信息**，
    省掉的话读者分不出「没做」和「做了但不好看」。

    横轴印的是那个系数的**标签**，不是估计量的键。原先直接印 `key`，于是真跑出来的图上
    写着「Coefficient on main」——**读者无从知道 main 是哪个变量**，而图会被单独拿去用，
    那时连图注都不在身边。跟 `coef` 同一个 `labels`、同一个 `term_label`：图和表叫同一个名字。
    """
    from oss.core.manuscript import term_label, usable_labels

    labels = usable_labels(labels, lang == "en")
    plt = _plt()
    main_label = _t(lang, "main")
    est_for_key = results.estimates.get(key)
    key_shown = term_label(est_for_key.term, labels) if est_for_key is not None else key
    # 每一行带自己的区间临界值：主规格取 `spec.t_crit`，每一项稳健性取它自己的 `t_crit`——
    # 换了样本的那项抽样单位少、自由度小，去掉设计的那项按正态。主规格的临界值套不到每一行
    rows: list[tuple[str, float, float, bool, float]] = []
    m = results.estimates.get(key)
    if m is not None:
        rows.append((main_label, m.coef, m.se, True, _crit(results)))
    main_outcome = _main_outcome(results)
    for fallback, run in results.robustness.items():
        # 契约里有 `label`，就用它；键只是兜底。**判据挂在算得出来的那一格上**——
        # 现在唯一的写入方把键和 label 写成同一个串，将来多一个写入方就未必
        label = run.label or fallback
        if _other_outcome(run, main_outcome):
            continue                      # 换了结局 = 换了估计量，单位都不同，不同轴
        est = run.estimates.get(key)
        if run.error or est is None:
            rows.append((label, float("nan"), float("nan"), False, 1.96))
        else:
            rows.append((label, est.coef, est.se, True, run.t_crit or 1.96))

    # 英文图上的标签不能有中文：稳健性 label 来自计划文件，可能是中文的
    shown: list[str] = []
    mapping: dict[str, str] = {}
    for i, (label, *_rest) in enumerate(rows):
        if lang == "en" and _CJK.search(label):
            s = _ascii_label(label, f"R{i}")
            mapping[s] = label
            shown.append(s)
        else:
            shown.append(label)

    with _pub(plt, lang):
        # **标签折行了，行距要跟着加。**否则「只看 1966—1978 年出生者」折成两行之后，
        # 溢出的那半行正好压在邻行的标签上（demo 的三张森林图都这样）。
        # **森林图的纵轴就是内容**，标签该分到更多宽度：缺省的 38% 只放得下十六个汉字，
        # 「只看 1950—1965 年出生者」多一个字就折两行，第二行跨在两行数据之间。
        # 这类图的 x 轴范围本来就窄，让出去的绘图区读得起。
        wrapped = _wrap_labels(shown, _canvas(6.5, 3)[0], share=_ROB_LABEL_SHARE)
        n_lines = sum(s.count("\n") for s in wrapped)
        # **注释占的高度要算进画布，不能从数据区挤。**挤的话注释框正好盖住最上面那一行
        # （主规格那个菱形整个藏在框底下，而那是全图最该看的一个点）。
        note_text, _ = _robustness_note(results, lang)
        n_note = 0 if not note_text else len(
            textwrap.wrap(note_text.replace("**", ""),
                          max(12, int(4.33 * 72 / 8 * (1 - _ROB_LABEL_SHARE)))))
        fig, ax = plt.subplots(
            figsize=_canvas(6.5, max(2.2, 0.38 * (len(rows) + 1.5 * n_lines) + 0.30 * n_note + 0.9)))
        c_main, c_other = _pair_colors(lang)
        ys = list(range(len(rows)))[::-1]
        for y, (label, coef, se, ok, crit) in zip(ys, rows):
            if not ok:
                ax.text(0, y, _t(lang, "nores"), va="center", fontsize=8, color="#999999")
                continue
            lo, hi = coef - crit * se, coef + crit * se
            is_main = label == main_label
            color = c_main if is_main else c_other
            ax.plot([lo, hi], [y, y], color=color, lw=1.5)
            ax.plot([coef], [y], marker="D" if is_main else "o", color=color,
                    ms=7.0 if is_main else 4.5)
        ax.axvline(0, color="#555555", ls="--", lw=0.9)
        ax.set_yticks(ys)
        ax.set_yticklabels(wrapped)
        ax.set_xlabel(_t(lang, "rob_x", key=key_shown))
        _thin_xticks(ax, fig)
        if (title := _t(lang, "rob_title")):
            ax.set_title(title)
        note, n_excluded = _robustness_note(results, lang)
        caption = _t(lang, "cap_rob", key=key_shown)
        if note:
            # **图注里也要有**：已发表的图一律把说明放图注，排进论文时读者看的是那儿。
            # 图上**照样留**——这条注解只在「有几项没画进来」时才出现，
            # 而那正是单独拿图去用会被误导的情形（跟分箱斜率漏组同一条理）
            caption = f"{caption} {' '.join(note.split())}"
            _note(ax, note, plot_share=1 - _ROB_LABEL_SHARE)
        extra: dict = {"shown": [r[0] for r in rows]}
        if mapping:
            extra["labels"] = mapping
        if n_excluded:
            extra["excluded_other_outcome"] = n_excluded
        return _save_pub(fig, out, kind="robustness", lang=lang,
                         caption=caption, extra=extra)


# ────────────────────────────────────────────────── ⑮ 分箱边际（验函数形式）


def marginal(frame, outcome: str, treatment: str, by: str, out: Path,
             *, split: str = "", bins: int = 6, lang: str = "en",
             series=None, adjusted: bool = False, results=None,
             labels: dict[str, str] | None = None, weights: str = "") -> Path:
    """分箱斜率图。**这张是用来验证交互项的函数形式的。**

    写一个 `背景 × 队列` 的交互项，等于假定「斜率随队列**线性**变化」——
    而这个假定几乎从来没人验证过。**回归表看不出来，图一眼就看得出。**

    GSS 那篇论文就是现成的教训：假定线性衰减写成一个三重交互项，
    画出来才发现大学组根本不是线性的（早期队列平甚至为负，中间有跳跃），
    而且最晚队列的斜率已经跟非大学组一样——
    「均衡作用所剩无几」这个说法其实低估了，它是**完全消失**。

    `results` 是 `series` 所出自的那份结果：区间临界值跟着它的抽样设计走（`_crit`）；
    现算的二元形状没有结果对象，照旧 1.96。
    """
    plt = _plt()
    from oss.core.analysis import binned_slopes
    from oss.core.manuscript import term_label, usable_labels

    # 轴标签走跟 coef / robustness 同一把筛子。**原先这里直接印原始列名**：
    # 中文列名 + 英文衬线字体 = 一排方框，而「方框比没有图更糟，它看起来像图能用」
    # 正是本模块开头写着的那条。coef 和 robustness 都滤过，只有这张漏了。
    labels = usable_labels(labels, lang == "en")
    outcome_s = term_label(outcome, labels)
    treatment_s = term_label(treatment, labels)
    by_s = term_label(by, labels)

    # `series` 给了就画它（来自 results.json 的 binned——图和文件里的数必须是同一批）；
    # 没给才现算，而且现算的是二元的原始形状。
    if series is None:
        # **现算也要加权。**「二元的原始形状」说的是不带控制变量与固定效应，
        # 不是不带权重——去掉控制是换了一个量，**去掉权重是换了一个总体**。
        # 调查数据上不加权的分箱斜率能跟加权的主结果反号（真跑时撞过一次，
        # 差点据此写成「线性假定不成立」）。
        series = binned_slopes(frame, outcome, treatment, by, split=split, bins=bins,
                               weights=weights if weights in frame.columns else "")
        adjusted = False

    # 某个分组一个点都没画出来 → 多半是处理变量在那组里恒为常数。
    # **静默少一条线是最坏的**：图看起来正常，读者不会知道少了一半。
    dropped: list[str] = []
    if split and split in frame.columns:
        shown = {name.split("=", 1)[1] for name, _ in series if "=" in name}
        for lv in sorted(frame[split].dropna().unique(), key=str):
            if str(lv) not in shown:
                dropped.append(str(lv))

    caption = _t(lang, "cap_mg_adj" if adjusted else "cap_mg",
                 by=by_s, outcome=outcome_s, treatment=treatment_s)
    if dropped:
        caption += (f" Groups with no variation in {treatment} are omitted ({split} = {', '.join(dropped)})."
                    if lang == "en" else
                    f" {split}={'、'.join(dropped)} 这几组没画出来——「{treatment}」在组内没有变异。")
    with _pub(plt, lang):
        fig, ax = plt.subplots(figsize=_canvas(6.5, 4.0))
        if not series:
            ax.text(0.5, 0.5, _t(lang, "mg_none"), ha="center", va="center")
            ax.set_axis_off()
            return _save_pub(fig, out, kind="marginal", lang=lang, caption=caption)

        crit = _crit(results)
        for i, (name, pts) in enumerate(series):
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            es = [crit * p[2] for p in pts]
            shown = _t(lang, "全样本") if name == "全样本" else name
            st = _series_style(lang, i)
            ax.errorbar(xs, ys, yerr=es, ms=4.5, capsize=3, label=shown, lw=1.5,
                        elinewidth=1.0, **({"linestyle": st["linestyle"]} if len(series) > 1 else {}),
                        color=st["color"], marker=st["marker"])
        ax.axhline(0, color="#555555", ls="--", lw=0.9)
        ax.set_xlabel(by_s)
        ylab = _t(lang, "mg_y_adj" if adjusted else "mg_y",
                  treatment=treatment_s, outcome=outcome_s)
        # 中文纵轴是**竖排**的，长标签会把图挤扁——真实的中文图纵轴都很短
        # （《公共行政评论》那张是「事故死亡率」五个字）。折行，不缩写：
        # 口径那句话是这张图的要害，删不得
        if _is_zh(lang) and len(ylab) > 14:
            ylab = "\n".join(_wrap_cjk(ylab, 14))
        ax.set_ylabel(ylab)
        if (title := _t(lang, "mg_title_adj" if adjusted else "mg_title", by=by)):
            ax.set_title(title)
        if len(series) > 1:
            _legend(ax, lang, ncol=min(len(series), 3))
        # 「交互项假定这几条线是直的」那句教学话去掉了——三十八张真发表的图里一张都没有，
        # 而且图注已经说了。**但「哪几组没画出来」留着，两种语言都印**：
        # 那不是教学话，是这次数据的事实，而**静默少一条线是最坏的**——
        # 图看起来正常，读者不会知道少了一半。英文图从前连这条也没有
        if dropped:
            msg = (f"Omitted: {split} = {', '.join(dropped)} — no variation in {treatment_s} "
                   f"within these groups" if lang == "en" else
                   f"{split}={'、'.join(dropped)} 这几组没画出来——"
                   f"「{treatment_s}」在组内没有变异")
            _note(ax, msg, "#C44E52")
        extra: dict = {"omitted_groups": dropped} if dropped else {}
        # 滤过之后还留着中文 = 这几个名字没有英文标签。**说出来**，别让方框静悄悄进论文
        if cjk := [t for t in (outcome_s, treatment_s, by_s) if _CJK.search(t)] if lang == "en" else []:
            extra["cjk_labels"] = cjk
        extra = extra or None
        return _save_pub(fig, out, kind="marginal", lang=lang, caption=caption, extra=extra)


# ────────────────────────────────────────────────────── ⑯ 主系数


def _coef_order(items: list) -> list:
    """系数图的行序：主效应 → 其他交互项 → 其余，组内保持原顺序。

    **原先照 `results.estimates` 的插入序画**，于是真跑出来的图上四个交互项
    一个在最顶、三个在最底（ossrun-a 的 fig_coef），中间隔着一串控制变量——
    读图的人得自己把它们认出来再拼到一起。

    只按「是不是主效应」「项里有没有 `*`」分组，**不需要计划、不需要额外输入**：
    换个数据、换个估计量都成立。要按处理/控制分得更细得读 plan，那是另一回事。
    """
    def key(i: tuple[int, tuple]) -> tuple[int, int]:
        idx, (k, e) = i
        return (0 if k == "main" else 1 if "*" in (e.term or "") else 2, idx)

    return [it for _, it in sorted(enumerate(items), key=key)]


def coef(results, out: Path, *, drop: tuple[str, ...] = ("_const",), lang: str = "en",
         labels: dict[str, str] | None = None) -> Path:
    """主回归的所有系数与 95% 置信区间。

    纵轴印的是**标签**不是列名（`labels` 由 `paper.labels_of` 从 binding.json + plan.json 装，
    解析规则跟表里同一个函数 `manuscript.term_label`）——图和表必须叫同一个名字。
    """
    from oss.core.manuscript import term_label, usable_labels

    labels = usable_labels(labels, lang == "en")     # 英文图里不混中文标签，跟表同一把筛子
    plt = _plt()
    items = [(k, e) for k, e in results.estimates.items() if e.term not in drop]
    items = _coef_order(items)
    with _pub(plt, lang):
        fig, ax = plt.subplots(figsize=_canvas(6.5, max(2.2, 0.36 * len(items) + 0.9)))
        ys = list(range(len(items)))[::-1]
        crit = _crit(results)
        has_focal = any(k == "main" for k, _ in items)
        c_main, c_other = _pair_colors(lang)
        for y, (k, e) in zip(ys, items):
            lo, hi = e.coef - crit * e.se, e.coef + crit * e.se
            focal = k == "main"
            color = c_main if focal else c_other
            ax.plot([lo, hi], [y, y], color=color, lw=1.5)
            # **主效应用菱形，其余用圆点。**只靠颜色分在黑白印刷下就没了
            # （橙 119 vs 蓝 87，12% 的明度差），而森林图里菱形表示焦点估计
            # 本来就是通行做法——形状是不靠颜色的那一半
            ax.plot([e.coef], [y], marker="D" if focal else "o", color=color,
                    ms=7.0 if focal else 4.5)
        ax.axvline(0, color="#555555", ls="--", lw=0.9)
        ax.set_yticks(ys)
        shown = [term_label(e.term, labels) for _, e in items]
        ax.set_yticklabels(_wrap_labels(shown, fig.get_size_inches()[0]))
        ax.set_xlabel(_t(lang, "coef_x"))
        _thin_xticks(ax, fig)
        if has_focal and len(items) > 1:     # 只有一行时没有「其他」可对比，图例是废话
            from matplotlib.lines import Line2D
            handles = [Line2D([], [], color=c_main, marker="D", ms=7.0, lw=1.5,
                              label=_t(lang, "coef_focal")),
                       Line2D([], [], color=c_other, marker="o", ms=4.5, lw=1.5,
                              label=_t(lang, "coef_other"))]
            _legend(ax, lang, handles=handles, ncol=2)
        if (title := _t(lang, "coef_title", n=results.n)):
            ax.set_title(title)
        cjk_terms = [s for s in shown if _CJK.search(s)]
        # 行序照 `shown` 记下来，跟森林图一个规矩：**排好了要能被检查**，
        # 不能只靠「我排了」这句话——图片本身是检查不了顺序的
        extra: dict = {"shown": list(shown)}
        if lang == "en" and cjk_terms:
            extra["cjk_labels"] = cjk_terms
        return _save_pub(fig, out, kind="coef", lang=lang,
                         caption=_t(lang, "cap_coef", n=results.n), extra=extra)


# ────────────────────────────────────────────────── ⑰ 共线诊断


def collinearity(frame, plan, out: Path) -> Path:
    """每个回归元的 VIF。

    秩检验只挡精确共线；**「差一点点」的共线会安静地穿过去**，
    留下一张全是星号、但有一半系数读不得的表。
    """
    plt = _plt()
    from oss.core.analysis import vif_table

    rows = sorted(vif_table(frame, plan), key=lambda r: -r[1])
    fig, ax = plt.subplots(figsize=_canvas(7.5, max(2.5, 0.4 * len(rows) + 1)))
    if not rows:
        ax.text(0.5, 0.5, "回归元不足两个，算不了 VIF", ha="center", va="center")
        ax.set_axis_off()
        return _save(fig, out)

    ys = list(range(len(rows)))[::-1]
    colors = ["#C44E52" if v >= 100 else "#DD8452" if v >= 10 else "#4C72B0"
              for _, v in rows]
    ax.barh(ys, [v for _, v in rows], color=colors)
    ax.set_yticks(ys)
    ax.set_yticklabels([n for n, _ in rows], fontsize=8)
    ax.set_xscale("log")
    ax.axvline(10, color="#DD8452", ls="--", lw=1)
    ax.axvline(100, color="#C44E52", ls="--", lw=1)
    ax.set_xlabel("VIF（对数刻度）")
    ax.set_title("共线诊断：橙线 VIF=10，红线 VIF=100", fontsize=10)
    return _save(fig, out)


# ────────────────────────────────────────────── 逐年展开图


def _wrap_cjk(s: str, width: int) -> list[str]:
    """按字数折行，但不在西文词或数字中间断开。

    `textwrap` 以空格为断点，遇到「中文长句 + 少量西文」会折出参差不齐的短行
    （`105）p =` 单独占一行）。图上的旁注要的是齐整的等宽行。
    """
    out, s = [], s.strip()
    while len(s) > width:
        cut = width
        while (cut > width // 2 and s[cut - 1].isascii() and s[cut - 1].isalnum()
               and s[cut].isascii() and s[cut].isalnum()):
            cut -= 1
        # 行尾不要留一个孤零零的左括号（「Alternative class scheme (」）
        while cut > width // 2 and s[cut - 1] in "([“（":
            cut -= 1
        out.append(s[:cut].strip())
        s = s[cut:].strip()
    if s:
        out.append(s)
    return _no_orphan(out, width)


#: 末行短于这个字数就算「孤字」，要把行匀开。
_ORPHAN = 3


def _no_orphan(lines: list[str], width: int) -> list[str]:
    """末行只剩一两个字时，把各行匀开重折。

    **孤字行在纵轴标签上是实打实的坏图**：matplotlib 把多行标签按整体垂直居中，
    于是那一个字落到两行数据之间，看着像下一行的标签。
    demo 的森林图里「只看 1966—1978 年出生者」正好比预算多一个字，
    末行就剩一个「者」掉在行间。

    匀的办法是把宽度一点点收窄再折，取**末行最长**的那个宽度——
    收窄不会增加行数（原来就折得下），只会让各行更齐。
    """
    if len(lines) < 2 or len(lines[-1]) >= _ORPHAN:
        return lines
    whole = "".join(lines)
    best = lines
    for w in range(width - 1, max(width // 2, _ORPHAN), -1):
        trial = [whole[i:i + w] for i in range(0, len(whole), w)]
        if len(trial) != len(lines):          # 收窄到要多折一行就停
            break
        if len(trial[-1]) > len(best[-1]):
            best = trial
        if len(best[-1]) >= _ORPHAN:
            break
    return best


def _pretrend_verdict(results) -> str:
    """平行趋势的判词标记：`violated` / `not_refuted` / `unknown`。

    **取自 `diagnostics.平行趋势`，不在这里重算。**那条诊断在分批估计里
    用的是各批 × 期的联合检验，在其它估计量里退回逐期规则——
    哪把尺子由估计量决定，图跟着它走，才不会跟正文说两样话。
    """
    v = str((getattr(results, "diagnostics", None) or {}).get("平行趋势", "") or "")
    if not v.strip() or "检验不了" in v:
        return "unknown"
    return "violated" if "不成立" in v else "not_refuted"


def _pretrend_note(results, lang: str = "zh") -> tuple[str, str]:
    """逐年展开图左上角那条旁注：**只转述诊断的判词**。

    2026-09-25 去掉了原先那句「竖虚线左边是政策实施之前，那几期本该贴着零线」——
    翻了中英两边三十八张真发表的图，**没有一张有这种教学式注记**，而且那句话
    图注里已经说了。判词留下，而且**两种语言都印**：英文图从前一条都没有。

    **图自己不判平行趋势。**图上能看见的是各期的汇总系数，逐点看它们显不显著
    是一把尺子；正式判词是另一把——各批 × 期的联合检验。两把尺子会给出相反的
    结论：验收跑 `r20260907_edufert_zh` 里逐期最大 |t| = 0.74（一期都不显著），
    而联合检验（自由度 105）p = 0.000 拒绝平行。当时图上印着「平行趋势未被推翻」，
    同一份 Word 的摘要里写着「联合检验拒绝了平行」——**一份文件自己打自己脸**。

    所以这里只转述 `diagnostics.平行趋势` 的头一句；诊断给不出判词就不下判词。
    """
    verdict = str((getattr(results, "diagnostics", None) or {}).get("平行趋势", "") or "")
    bad = "不成立" in verdict
    if lang == "en":
        # 英文图上不能印中文诊断（方框）。判词有归一化的标记，照它出英文短语
        tag = _pretrend_verdict(results)
        text = {"violated": "Pre-trend test: parallel trends rejected",
                "not_refuted": "Pre-trend test: not refuted"}.get(tag, "")
        return text, ("#C44E52" if bad else "#333")
    first = verdict.split("。")[0].replace("**", "").strip()
    if not first:
        return "", "#333"
    return "\n".join(_wrap_cjk(first + "。", 34)), ("#C44E52" if bad else "#333")


def event(results, out: Path, *, lang: str = "en") -> Path:
    """逐年展开图。**这是判断一个政策冲击研究能不能信的那张图。**

    横轴是相对政策实施的期数，纵轴是效应。**要看的是虚线左边**：
    政策落地**之前**两组本该没有差异，如果那几期已经偏离零，
    说明两组在政策之前就在分化——**算出来的「效应」里混着本来就有的趋势**。

    这个问题在结果表格上看不出来（表里只有一个汇总的系数），图上一眼可见。
    """
    plt = _plt()
    pts = sorted(results.event_study, key=lambda p: p.k)
    caption = _t(lang, "cap_ev")
    with _pub(plt, lang):
        fig, ax = plt.subplots(figsize=_canvas(6.5, 4.0))
        if not pts:
            ax.text(0.5, 0.5, _t(lang, "ev_none"), ha="center", va="center")
            ax.set_axis_off()
            return _save_pub(fig, out, kind="event", lang=lang, caption=caption)

        # 逐期效应出自伴随的 Callaway–Sant'Anna 时照它自己的口径（StatsPAI 按正态，companion_spec.t_crit 为 0 → 1.96），
        # 不借主规格的 t 临界值
        comp = getattr(results, "companion_spec", None)
        crit = (comp.t_crit or 1.96) if comp is not None else _crit(results)
        # 处理前用空心点、处理后用实心点：**处理前那几期是这张图的要害**，
        # 而它原先只靠颜色区分——黑白印出来就没了。中文体例连线也改虚线误差棒
        # （《公共行政评论》那张平行趋势图就是虚线误差棒 + 实心黑点）
        c_pre, c_post = (_GRAY_MAIN, _GRAY_MAIN) if _is_zh(lang) else (_PRE, _POST)
        ebar = {"elinewidth": 1.0, "capsize": 3}
        if _is_zh(lang):
            ebar["capthick"] = 1.0
        for p in pts:
            pre = p.k < 0
            bar = ax.errorbar([p.k], [p.coef], yerr=[crit * p.se], ms=5.0, lw=1.5,
                              marker="o", color=c_pre if pre else c_post,
                              markerfacecolor="white" if pre else c_post,
                              markeredgewidth=1.2, **ebar)
            if _is_zh(lang):
                # 误差棒画成虚线，照《公共行政评论》2021 那张平行趋势图。
                # **横杠是 LineCollection 不是 Line2D**——照 `ax.lines` 去找是找不着的
                for coll in bar.lines[2]:
                    coll.set_linestyle((0, (3, 2)))
        # 中文期刊的连线明显更粗（《湖北经济学院学报》《公共行政评论》三张都是）
        ax.plot([p.k for p in pts], [p.coef for p in pts],
                color="#333333" if _is_zh(lang) else "#999999",
                lw=1.6 if _is_zh(lang) else 0.9, zorder=0)
        ax.axhline(0, color="#555555", ls="--", lw=0.9)
        ax.axvline(-0.5, color="#333333", ls=":", lw=1.1)
        ax.set_xlabel(_t(lang, "ev_x"))
        ax.set_ylabel(_t(lang, "ev_y"))
        if (title := _t(lang, "ev_title")):
            ax.set_title(title)
        bad = [p for p in pts if p.k < 0 and p.se and abs(p.coef / p.se) > crit]
        # **判词两种语言都印。**它不是教学话，是这次分析的事实——
        # 验收跑里图上写「平行趋势未被推翻」、同一份 Word 的摘要写「联合检验拒绝了平行」，
        # 一份文件自己打自己脸。英文图从前一条判词都没有，是同一个洞的另一半
        note_text, note_color = _pretrend_note(results, lang)
        if note_text:
            _note(ax, note_text, note_color)
        return _save_pub(fig, out, kind="event", lang=lang, caption=caption,
                         extra={"pre_trend_violations": len(bad),
                                "pre_trend_verdict": _pretrend_verdict(results)})


KINDS = {"structure": "结构判定的证据", "missing": "缺失率 × 时间",
         "clusters": "簇大小与设计效应", "robustness": "稳健性森林图",
         "marginal": "分箱斜率（验交互项的函数形式）", "coef": "主回归系数",
         "collinearity": "共线诊断 VIF", "event": "逐年展开图"}
