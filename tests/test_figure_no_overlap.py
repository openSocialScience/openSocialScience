"""图不许坏：注释不许压数据、标签不许互相重叠、画布不许塌也不许长到印不下。

**这是打开成品 Word 第一眼就看见的那种坏。**三份 demo 的稳健性森林图全都是坏的，
而且已经嵌进了导出的 .docx——用户直接指出来的。

根因是一条链：`_note` 画的注释不折行 → 一条中文长注就是一条极宽的单行 →
它按磅走、不随画布缩 → `_fit` 为了把宽度压进上限只能一轮轮等比缩整张画布 →
每缩一轮溢出占比反而更大 → 八轮之后高度塌成一条缝 → 标签全叠、点和区间看不见。

判据不看「好不好看」，看**算得出来的事实**：两个文字包围盒有没有相交。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss.core.contracts import AnalysisResults, Estimate, RobustnessRun

pytest.importorskip("matplotlib")


def _results(n: int, *, label_len: int = 8) -> AnalysisResults:
    """n 项稳健性，标签长度可调（长标签是会折行的那一类）。"""
    rob = {}
    for i in range(n):
        lab = f"稳健性第{i}项" + "很长的说明文字" * (label_len // 7)
        rob[lab] = RobustnessRun(
            label=lab, n=1000,
            estimates={"main": Estimate(term="x", coef=0.1 + i * 0.01, se=0.02, p=0.01)})
    # 换了结局的项会触发那条「另有 N 项未画在这里」的长注释
    for j in range(3):
        lab = f"结局：换一个很长名字的结局变量之{j}"
        rob[lab] = RobustnessRun(label=lab, n=1000, outcome=f"y{j}",
                                 estimates={"main": Estimate(term="x", coef=0.5, se=0.1, p=0.01)})
    return AnalysisResults(
        plan_id="p", n=1000,
        estimates={"main": Estimate(term="x", coef=0.1, se=0.02, p=0.01)},
        robustness=rob)


def _boxes(fig, ax):
    """纵轴标签与注释文字的像素包围盒。"""
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    ticks = [t.get_window_extent(r) for t in ax.get_yticklabels() if t.get_text().strip()]
    notes = [t.get_window_extent(r) for t in ax.texts if t.get_text().strip()]
    return ticks, notes


def _draw(tmp_path: Path, res: AnalysisResults):
    """画一张，并把 figure 截下来——`robustness` 存完就 `plt.close`，事后拿不到。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from oss.core import figures

    grabbed: list = []
    real_close = plt.close
    plt.close = lambda *a, **k: grabbed.append(a[0]) if a else None  # noqa: E731
    try:
        figures.robustness(res, tmp_path / "f.png", lang="zh")
    finally:
        plt.close = real_close
    assert grabbed, "没抓到 figure"
    fig = grabbed[0]
    return fig, fig.axes[0]


@pytest.mark.parametrize("n,label_len", [(8, 8), (16, 30), (22, 14)])
def test_纵轴标签不许互相重叠(tmp_path: Path, n: int, label_len: int) -> None:
    """标签折行之后第二行会落在这一行下面；行距不够就贴上邻行的标签。
    **一条折行的标签要吃掉一整行多的位置**，这是算得出来的量。"""
    fig, ax = _draw(tmp_path, _results(n, label_len=label_len))
    ticks, _ = _boxes(fig, ax)
    for a, b in zip(ticks, ticks[1:]):
        lo, hi = sorted((a, b), key=lambda x: x.y0)
        assert lo.y1 <= hi.y0 + 1.0, f"两条纵轴标签叠在一起：{lo} / {hi}"


@pytest.mark.parametrize("n", [8, 16, 22])
def test_注释不许压住数据(tmp_path: Path, n: int) -> None:
    """注释框盖住最上面那一行时，主规格那个点整个看不见——而那是全图最该看的一个点。"""
    fig, ax = _draw(tmp_path, _results(n))
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    notes = [t for t in ax.texts if t.get_text().strip()]
    if not notes:
        pytest.skip("这一组没有触发注释")
    bottom = min(ax.transData.inverted().transform(
        (0, t.get_window_extent(r).y0))[1] for t in notes)
    top_point = max(max(l.get_ydata(orig=False)) for l in ax.lines
                    if len(l.get_ydata(orig=False)) and max(l.get_ydata(orig=False)) < 1e3)
    assert bottom > top_point, f"注释下沿 {bottom:.2f} 压住了最上面的数据 {top_point:.2f}"


@pytest.mark.parametrize("n", [8, 16, 22])
def test_画布高度不许塌(tmp_path: Path, n: int) -> None:
    """等比缩会把高度一起带下去。**宽一点的图总还读得出数；高度塌了的图一个数都读不出。**
    实测坏的那一版把 6.36 英寸高的画布缩到 3.50 英寸。"""
    from oss.core import figures

    fig, _ = _draw(tmp_path, _results(n))
    h = fig.get_size_inches()[1]
    # 基准是 `_canvas` 之后的高度：为了把宽度压进上限而等比缩是**设计好的**，
    # 要拦的是 `_fit` 在那之后又把高度带下去。
    want = figures._canvas(6.5, 0.38 * n + 0.9)[1]
    assert h >= want * figures._MIN_HEIGHT_SHARE, (
        f"{n} 行的图只剩 {h:.2f} 英寸高，基准 {want:.2f}")


def test_注释要折行(tmp_path: Path) -> None:
    """不折行的一条中文长注就是撑爆整张图的那根杠杆。"""
    fig, ax = _draw(tmp_path, _results(8))
    notes = [t.get_text() for t in ax.texts if len(t.get_text()) > 30]
    assert notes, "这一组该有一条长注释"
    assert "\n" in notes[0], f"注释没折行：{notes[0][:40]}"


# ══════════════════════════════════════════════ 别的图：同一套判据
#
# 森林图那一轮只盯了它自己。**同样的坏在别的图上照样有**，用户指出之后逐张看，
# 又抓到两张：`fig_missing` 三百多个变量全画上去、575 × 13574 像素（九十多英寸高，
# 标签糊成一片，排进论文完全不能看）；`fig_clusters` 并排两栏，中文标题比子图还宽，
# 两个标题横着撞在一起、右栏的 y 轴标签顶进左栏的绘图区。

import numpy as np
import pandas as pd

from oss.core.contracts import DataProfile


def _frame(n_cols: int, n_rows: int = 200, *, time: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    d = {}
    for i in range(n_cols):
        col = rng.normal(size=n_rows)
        col[rng.random(n_rows) < (i / max(n_cols, 1)) * 0.8] = np.nan   # 缺失率逐列升高
        d[f"很长的变量名字第{i}列"] = col
    if time:
        d["year"] = rng.integers(2000, 2010, n_rows)
    return pd.DataFrame(d)


def _profile(time: str = "") -> DataProfile:
    p = DataProfile(file={"path": "t.csv"})      # FileInfo 的 path 必填
    p.structure.time_id = time
    return p


def _png(path: Path):
    from PIL import Image

    return Image.open(path).size


#: 一张图最高印得下多少像素。A4 版心约 9 英寸，按 140 dpi 算 1260——
#: 给到两倍富余，超了就是排不进论文的那一类。
_MAX_PX = 2600


@pytest.mark.parametrize("n_cols,has_time", [(8, False), (60, False), (350, False),
                                             (8, True), (350, True)])
def test_缺失图不许长到印不下(tmp_path: Path, n_cols: int, has_time: bool) -> None:
    """**画不下的图不是图。**三百多个变量画出来九十多英寸高，而「哪些变量缺得多」
    恰恰是这张图唯一要回答的问题——取缺失率最高的若干个，并说清漏了多少。"""
    pytest.importorskip("PIL")
    from oss.core import figures

    out = tmp_path / "m.png"
    figures.missing(_frame(n_cols, time=has_time), _profile("year" if has_time else ""), out)
    w, h = _png(out)
    assert h <= _MAX_PX, f"{n_cols} 列画出来 {h} 像素高，印不进论文"
    assert w <= _MAX_PX


def test_缺失图漏掉的变量要说出来(tmp_path: Path) -> None:
    """取了子集就必须说，否则读者以为这就是全部——跟森林图那条「另有 N 项未画在这里」同理。"""
    from oss.core import figures

    fig, ax = _capture(lambda: figures.missing(_frame(350), _profile(), tmp_path / "m.png"))
    # 去掉换行再判：注释是折过行的，断点可能正好落在这几个字中间
    notes = [t.get_text().replace("\n", "") for t in ax.texts]
    assert any("未画在这里" in s for s in notes), f"没说漏了多少：{notes}"
    assert any("325" in s for s in notes), f"没说漏了几个：{notes}"


def _capture(call):
    """跑一次画图，把 figure 截下来（画完就被 `plt.close` 关掉了）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    grabbed: list = []
    real = plt.close
    plt.close = lambda *a, **k: grabbed.append(a[0]) if a else None    # noqa: E731
    try:
        call()
    finally:
        plt.close = real
    assert grabbed, "没抓到 figure"
    return grabbed[0], grabbed[0].axes[0]


def test_并排两栏的标题不许撞在一起(tmp_path: Path) -> None:
    """4.33 英寸的版心里并排两栏，每栏不到两英寸，而中文标题动辄二十几个字。
    标题该按**子图**宽度折行，不是按整张画布。"""
    from oss.core import figures

    df = pd.DataFrame({"g": np.repeat(np.arange(40), 5), "y": np.arange(200.0)})
    fig, _ = _capture(lambda: figures.clusters(df, "g", tmp_path / "c.png"))
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    boxes = [a.title.get_window_extent(r) for a in fig.axes if a.get_title().strip()]
    for a, b in zip(boxes, boxes[1:]):
        lo, hi = sorted((a, b), key=lambda x: x.x0)
        assert lo.x1 <= hi.x0 + 1.0, "两个子图标题横着撞在一起"


def test_并排两栏的注释不许溢出子图(tmp_path: Path) -> None:
    """注释折行用整张画布的宽度时，并排两栏的注释会整条甩到子图外面。"""
    from oss.core import figures

    df = pd.DataFrame({"g": np.repeat(np.arange(40), 5), "y": np.arange(200.0)})
    fig, _ = _capture(lambda: figures.clusters(df, "g", tmp_path / "c.png"))
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    for ax in fig.axes:
        for txt in ax.texts:
            if not txt.get_text().strip():
                continue
            bb = txt.get_window_extent(r)
            ab = ax.get_window_extent(r)
            assert bb.x1 <= ab.x1 + 2.0, f"注释溢出子图右边：{txt.get_text()[:24]}"
