# 论文的跨模型审稿 · 契约

## 输入
- `<运行目录>/paper_rendered.md` ← **审的是渲染后的成稿，不是带占位符的源稿**
- `<运行目录>/plan.json`（对着它看稳健性是不是事后补的）
- `<运行目录>/results.json`、`power.json`、`review_plan.json`
- `<运行目录>/literature.json` ← 对着 notes 看局限写全没有
- `--- round: <N>`，缺省 1

## 产出
- `review_desk.json`（桌审，**不算审稿轮次里的一位**）
- 三份（测量类四份）`review_paper_rendered_*.json`（识别 / 替代解释 / 局限；测量类另加测量与数据），第 N 轮存成 `_rN` **不覆盖上一轮**
- `review_editor.json`（编辑综合，**不算审稿人**）
- 改过的 `paper.md` 与重新渲染的 `paper_rendered.md`

## 完成条件（全部满足才算做完）
- 先桌审；FAIL 就先改再送三位审稿人
- 三位互盲审稿人各自**全新线程**、只拿同一份不可变材料 + 自己的侧重；三份都落盘冻结之后才做编辑综合
- 每份都用 `--paper`（不是只传 `paper_rendered.md`）
- 每条意见逐条核过三遍：回原文对、回产出物对、判"错"还是"口味"
- 改稿改 `paper.md` 再 `oss paper check` + `oss paper render`
- 跑过 `oss loop status`，并**照第一行的决定做**
- 收尾告诉用户四件事：判词原文照抄、逐条意见与你的处置、你不同意的那几条、退出码是 1 或 2 时明说本次没有得到任何裁决

## 禁止
- 把别人的报告、共享的问题清单、草稿综合给任何一位审稿人
- 把侧重写成审稿人身份 / 专业 / 履历（那是演戏，不是审稿）
- 先比较再定稿（让后写的迁就先写的）
- 为了"显得多样"编辑掉重复或分歧——**重复和分歧都是独立性的证据**
- 做不到隔离却仍称之为独立评议
- 把退出码 1（后端不可用）或 2（严格档下的同家族）当成通过
- 自审（`cross_family: false`）却不在局限里写明——能审不等于独立验证
- 只改渲染稿；删 `review_*.json`
- 判 PASS 就说"这篇可以投"
- **第 2 轮起拿新看到的问题判 FAIL**——新问题写在「新发现（不作为 FAIL 依据）」段里，每条以 `[新发现]` 开头

## 命令
```bash
oss review --desk --run <运行目录>
oss review --paper --level deep --run <运行目录> --round <N> --emphasis "识别策略与因果表述：结论有没有超出设计支持的范围、话跟数对不对"
oss review --alternatives --level deep --run <运行目录> --round <N>
oss review --paper --level deep --run <运行目录> --round <N> --emphasis "局限与稳健性：该写的写全没有（含因果审计 review_plan.json 的每一条）、稳健性是不是事后补的"
# research_type 为 measurement、或 also 含 measurement 时加第四位，之后每轮都带上
oss review --paper --level deep --run <运行目录> --round <N> --emphasis "测量与数据：构念、代理变量、缺失机制、样本构成"
oss review --editor --round <N> --run <运行目录>
oss paper check  --run <运行目录>
oss paper render --run <运行目录>
oss loop status  --run <运行目录>
```

## 卡住时读
- 机械规则的硬边界在哪 → [reference/why.md](reference/why.md)
- 三位怎么分工、互盲纪律六条 → [reference/blind-discipline.md](reference/blind-discipline.md)
- 退出码 0 / 1 / 2 / 4 分别怎么办 → [reference/exit-codes.md](reference/exit-codes.md)
- 判词回来之后怎么办、轮次怎么走 → [reference/after-verdict.md](reference/after-verdict.md)
