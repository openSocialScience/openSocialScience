# README 用图

手写的 SVG 图都有**深色和浅色两份**，README 用 `<picture>` 按读者的 GitHub 主题自动切换：

```html
<picture>
  <source media="(prefers-color-scheme: dark)"  srcset="docs/images/workflow-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/images/workflow-light.svg">
  <img src="docs/images/workflow-light.svg" width="960" alt="...">
</picture>
```

| 图 | 用在哪 | 尺寸 | 源文件 |
|---|---|---|---|
| `title{,-dark}.png` | README 首屏的标志和字标 | 1827 &times; 216 | 位图 |
| `logo.svg` | 标志本身：五个人站成一条钟形曲线 | 矢量 | SVG |
| `hero-{light,dark}.jpg` | README 首屏大图，三篇实例论文的真实页面 | 2400 &times; 1000 | 位图，改了 `examples/` 的论文要重新出图 |
| `hero.png` | GitHub 仓库设置里的 Social preview | 1024 &times; 506 | AI 生成的位图，**没有源文件**，要改得重新出图 |
| `workflow-{dark,light}.svg` / `.png` | 目前没有页面引用 | 960 &times; 808 | 手写 `-dark.svg` |
| `architecture-{dark,light}.svg` / `.png` | ARCHITECTURE.md 开头 | 960 &times; 898 | 手写 `-dark.svg` |
| `no-fabricated-numbers.png` | 中文 README（`docs/i18n/README_zh.md`）「数字溯源」一节的对比图 | 1666 &times; 722 | 位图，只有深色版，**没有可编辑的源文件**；回显可复现，见下 |
| [`gallery/`](gallery/) | 八种图的样张（**数据是造的**，只看版面） | —— | 由 [`图样张.py`](图样张.py) 生成 |

## 改图的规矩

> **只改 `-dark.svg`。** `-light.svg` 是机械生成的，手改了下次生成就被覆盖。

深色版用的底色 `#0d1117` **就是 GitHub 深色（默认）主题的画布色**，所以深色主题下图和页面无缝；
浅色版由 [`生成浅色版.py`](生成浅色版.py) 按 Primer 的对应色替换，浅色主题下同样无缝。
色板里没覆盖的颜色会**直接报错**，不会静默漏掉——加了新颜色就去 `PALETTE` 里补一行。

```bash
python3 docs/images/生成浅色版.py          # 只生成 *-light.svg
python3 docs/images/生成浅色版.py --png    # 顺带渲染全部位图（需要 rsvg-convert）
```

装渲染器：`brew install librsvg`。

字体列表把 `PingFang SC` 和 `Microsoft YaHei` 都写进去了，Mac 和 Windows 上中文都认得出来。

> **GitHub 的 Social preview 只收位图**（PNG / JPG，&le; 1 MB），而且**不分主题**——只能传一张。
> 传 `hero.png`（GitHub 建议 1280×640，这张 1024×506 会被略微放大，可接受）。
> 传的地方：仓库 → Settings → General → Social preview → Upload an image。

**画箭头的一个坑**：方框如果画在箭头之后，会把箭头盖住。
早先手写的那版 Hero 里，箭头因此收在方框之间的空隙里，不伸进方框。
`marker-end` 也换成了显式三角形——部分渲染器和图床对 SVG `<marker>` 的支持不稳。

## `no-fabricated-numbers.png`

「模型编不出数字」那一节的对比图。**图里的每一行回显都是真跑出来的**，不是照着想象画的——
所以下面留一份复现步骤，好让「未经修饰」这句话可核。

```bash
RUN=/tmp/oss-demo/run
mkdir -p "$RUN"
cp runs/gss20260824_v2/results.json "$RUN"/        # 任何带 main 估计的结果都行

# 左半：手打一个数字
cat > "$RUN"/paper.md <<'EOF'
# 父母教育对子女教育获得的影响

## 摘要
本文用 GSS 累积文件考察父母教育对子女教育获得的影响。

## 引言
地位获得研究长期关注家庭背景如何传递。

## 数据与方法
数据来自 GSS 1972–2024 年累积文件，估计量为固定效应模型。

## 结果
出身效应的估计值为 0.188。

## 讨论
结果与地位获得理论的预期一致。
EOF

oss paper check --run "$RUN"      # → 退出码 1，点名第 13 行的裸小数

# 右半：换成占位符
#   把「出身效应的估计值为 0.188。」改成
#   「出身效应的估计值为 {{result:main.coef}}（标准误 {{result:main.se}}，p {{result:main.p}}）。」

oss paper check  --run "$RUN"     # → 退出码 0，「正文零裸数字」
oss paper render --run "$RUN"     # → paper_rendered.md 里是 0.188（标准误 0.026，p < 0.001）
```

**这个例子挑得有讲究**：手打的 `0.188` 跟真实估计值**完全相同**，照样被拒。
检测查的不是「这个数对不对」，而是「这个数找不找得到出处」——
把两者混为一谈，正是「反正我算对了」这类说法能蒙混过关的原因。

这张是位图，要改图得重新出一张，换掉 `no-fabricated-numbers.png`。
