# 找空白、生成 idea · 契约

## 输入
- `<运行目录>/literature.json`（主链环节 2 的语料）
- `<运行目录>/reading_cards.json`（读书卡，**空白地图从这里的原句建**）
- `<运行目录>/profile.json`——**只看形状，不看变量清单**（`oss profile <数据文件> --shape`）
- `--- discipline: <学科>`，缺省 `economics`

## 产出
- `<运行目录>/ideas.json`——顶层的 `generated_n`（发散段产出数）与 `unreachable`（清单乙）都要写满
- 每条候选的 `prescreen` 栏与 `prescreen_<idea_id>.json`（⑤ 经验预检的证据）
- `<运行目录>/review_ideas.json`（陪审团判词）
- 一条 `oss trace save --step ideas` 留痕

## 完成条件（全部满足才算做完）
- 六个默认透镜顺序过了一遍；产出不足三个候选时才开学科额外的四个（**输入带 `idea:` 时整套不跑**，改走 [reference/tidy-mode.md](reference/tidy-mode.md) 的四步：整理 → 陪审 → 确认 → 冻结）
- 合并去重只按**机械相似度**做，没有因为"这个弱"丢掉任何候选
- 客观筛选只按四条机械可核实的事实淘汰，每条淘汰记了理由
- 每个 idea 有 `lens` 与 `research_type`；`theory_hook` / `value_if_null` 为空的照写不删，标出来让陪审团判；`context-transfer` / `temporal-shift` 出来的写 `transfer_rationale`（说不出就 `null` 并标高风险）
- **对存活候选跑过经验预检并据此重排**（最多前 8 个，细则见 [reference/prescreen.md](reference/prescreen.md)）：
  读 `mde` 不看 `verdict`，描述类读 `precision_halfwidth`；做不了的**不扣分**，进 `unreachable` 并写清缺什么
- **候选数没变**：`generated_n` 与文件里的条数相等。多出来是收敛段拿变量清单回头造题，少下去是把砍掉的候选删了
- **拿具体候选做过定向查重**：结论原文写进 `novelty_check`，`theory_hook` 用检索结果校正过；
  自检——连用户指定的文献都没召回，就是检索策略有问题，**不是「没人做过」**
- 跑过 `oss review --kind ideas`，**退出码 0**；判词与逐条意见落回每个 idea 的 `jury_verdict` / `jury_reason` 两个字段
- 被砍掉的候选**保留在文件里**并标注砍掉的理由
- 收尾告诉用户四件事：用了哪几个透镜各产出多少、淘汰了几个分别因为什么、陪审团的判词与逐条意见（原文照抄）、语料 `notes` 里的每一条警告（比如「摘要覆盖太低，方法缺陷类空白检测不到」——那意味着**某一类 idea 这次系统性地找不到**）

## 禁止
- 在生成阶段按质量淘汰（那是陪审团的活）
- **在 ⑤ 看见真数据之后新加或改写候选**——那一段只许重排，不许生成
- 把「这份数据做不了」当成扣分项（做不了不扣分，分流到清单乙）
- 只把能做的那份清单交给用户（**两份清单都要交**）
- 看具体列名去凑问题（**那是数据在决定问题**）
- 自己发明 `ideas.json` 的顶层键（schema 禁止未知字段，加了下一步 `oss bind` 会被拒——两次真跑都踩过）
- 改评议的判词（可以不同意，写进收尾说明）
- 让评议单独决定候选去留——它是**一个信号，不是裁决**，跟预检证据、查重结果并列摆给用户
- 把「检索没召回」写成「没人做过」
- 把退出码 1（后端不可用）或 2（严格档下的同家族）当成通过
- 对本质上是质性的方向硬产出假设形状的东西
- 不许写死宿主：叫审稿模型走 `oss review`，留痕走 `oss trace save`
- **淘汰或改写 `source = user` 的那条记录**：它是用户自己交上来的题，陪审团的判词只能记录进 `jury_verdict` / `jury_reason` 与论文局限，删它、改它的题、在它旁边再加一条候选，都是换题——换题只能由用户发起

## 命令
```bash
oss schema ideas --example          # 格式先查，不要猜
oss schema ideas
oss profile <数据文件> --shape --run <运行目录>
oss review <运行目录>/ideas.json --kind ideas --level quick --run <运行目录>
oss trace save --step ideas --note "N 个候选，M 个透镜" --run <运行目录>
```

## 卡住时读
- 为什么不能照搬 ML 那套 → [reference/why-social-science.md](reference/why-social-science.md)
- 三条铁律 → [reference/iron-laws.md](reference/iron-laws.md)
- 这个学科开哪几个透镜、识别门多硬 → [reference/discipline-profiles.md](reference/discipline-profiles.md)
- 十个透镜各问什么、四步流程 → [reference/lenses.md](reference/lenses.md)
- 陪审团怎么调、三种退出情况 → [reference/jury.md](reference/jury.md)
