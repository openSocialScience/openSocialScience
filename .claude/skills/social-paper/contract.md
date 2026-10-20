# 写论文初稿 · 契约

## 输入
- `<运行目录>/ideas.json`（研究问题、假设、机制、`theory_hook`）
- `<运行目录>/results.json` ← **正文的数字只能来自这里**
- `<运行目录>/literature.json`——语料，写引言和文献部分用
- `<运行目录>/plan.json`（分析计划：估计量、固定效应、聚类层级）、`binding.json`（构念绑到了哪些列）、`power.json`（功效判定）、`route.json`
- 可选：`--- style: <刊名>`（`styles/<刊名>/` 要存在）
- 可选：`--- lang: zh`——写**中文论文**（细则读 [reference/chinese.md](reference/chinese.md)）

## 产出
- `<运行目录>/paper.md`——至少五节：Abstract / Introduction / Data and Methods / Results / Discussion（`lang: zh` 时为 摘要 / 引言 / 数据与方法 / 结果 / 讨论，另加文末英文 Abstract）；正文语言跟 `lang` 走，**缺省中文**（用户明说写英文才 `en`）；这是占位符源稿，**改稿改它**
- `<运行目录>/paper_rendered.md`——中间产物，不要改
- `<运行目录>/paper.docx` ← **这才是交付物**

## 完成条件（全部满足才算写完）
- `oss paper check` 通过（节齐、零裸数字、占位符可解析、口径一致）；带 `--- style:` 的还要 `oss style check` 通过
- 三张表都在：`{{table:descriptives}}`、`{{table:main}}`、`{{table:robustness}}`，表注在占位符**上一行**
- 至少嵌两张图，每张图注在占位符**下一行**；有交互项必须有 `{{fig:marginal}}`，因果设计必须有 `{{fig:event}}`
- 因果研究引机器算的因果诊断：工具变量报有效 F 与 AR 区间——**有效 F 低于 10 时主结果的区间引 `{{diag:AR 区间}}`**（`oss paper check` 拒绝不引的）；
  双重差分报预检功效或 breakdown 值；用 `ols_fe` 下因果结论的报稳健值、Oster δ 或 E 值（不引会被警告）
- 局限一节写全五类：`power.json` 判 `gray` 的功效（描述类写精度，判 `fail` 的也写）、`route.json` 里 `disclosed` 的每一条、语料 `notes` 的覆盖缺口、`binding.json` 的代理变量、`review_plan.json` 的每一条意见（一条一句）
- `oss paper export` 报的「引用 N 张图、嵌入 M 张」两个数**相等**——对不上就是有图丢了，多半是文件名或路径的问题，不能当成功交差
- 收尾告诉用户四件事：稿子在哪、引用了哪几个结果、局限里写了什么、`route.json` 哪些写进局限哪些驳回

## 禁止
- 正文出现裸小数（统计结果一律占位符；年份、整数样本量、节号放行）
- 手打引用（一律 `{{cite:}}` / `{{citep:}}`）；语料里没有的作者要么 `oss lit add`，要么改写成不具名归属
- 把研究过程写进正文：audit / automated / tooling / pipeline / verdict / gray band / R1、R3 规格代号 / 文件名 / `oss` 命令
- 功效判 `fail` 还往下写（描述类按精度判的不在此列，见 `reference/read-first.md`）
- `plan.json` 没声明 `binned` 却用分箱图做形状断言
- 把统计显著说成"证明""证实"
- 研究类型不是因果（`plan.json` 的 `research_type` 是描述 / 测量 / 仿真，或从因果降级成描述的）却在正文下因果结论（导致、造成、下结论的「存在因果关系 / 因果效应」、causes、leads to……）——`oss paper check` 拦，`oss paper export` 退出码 8；模糊的说法（影响、提高……）会列进 `causal_flags.json` 交审稿人判——只在确实是因果口气时改，标准术语照写
- `oss paper export` 退出码 8**不止管上面这条因果措辞**：因果论文用了弱工具（有效 F 低于 10）却没在主结果的区间引 `{{diag:AR 区间}}`，同一个退出码——收到退出码 8 先看拒绝理由说的是哪一条，**这条要补的是引用，不是把因果说法改软**
- 标题超过 15 个词
- 图的语言跟稿子拧着来（英文稿嵌中文图、中文稿嵌英文图，校验都会拦）；稳健性 `label` 语言跟稿子拧着来
- 校验没过就渲染、渲染没过就导出；改渲染稿

## 命令
```bash
oss schema paper
oss style template [<刊名>] --out <运行目录>/paper.md
oss paper check  --run <运行目录> [--style <刊名>]
oss style check  --run <运行目录> --style <刊名>
oss paper render --run <运行目录>
oss paper export --run <运行目录> [--style <刊名>]
oss lit add <DOI> --run <运行目录>
oss lit verify --run <运行目录>
oss figure coef --run <运行目录> --lang en    # 中文稿一律 --lang zh；起稿模板 oss style template --lang zh
```

## 卡住时读
- 英文与风格包 → [reference/english-and-style.md](reference/english-and-style.md)
- 占位符 / 表 / 图 / 引用 / 校验拦什么 → [reference/placeholders.md](reference/placeholders.md)
- 过程词怎么改 → [reference/no-process-language.md](reference/no-process-language.md)
- 动笔前先读的三样 → [reference/read-first.md](reference/read-first.md)
- 五节各写什么 → [reference/sections.md](reference/sections.md)
