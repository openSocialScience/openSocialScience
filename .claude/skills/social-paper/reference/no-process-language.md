# 铁律二：研究过程不进正文

评审第十次验收跑，规范性问题一整节：「把研究过程当成研究内容写进论文——
'the automated control-group diagnostic'、'an independent design audit'、'a verdict of failure'、
'the R3 specification'，Limitations 里还专门有一段指出审计报告有两处错误。
**没有任何期刊接受这个**——审稿 / 审计过程属于附录或回应信，不属于正文。」

校验会**拒绝**这些词：audit / automated / tooling / pipeline / verdict / gray band /
R1、R3 这类规格代号 / "the analysis attaches…" / "the diagnostic reports…" / 文件名 / oss 命令。

怎么改：**写结论本身，不写它是怎么来的。**

| 不许写 | 改成 |
|---|---|
| The independent design audit found that treatment timing is endogenous. | Treatment timing is plausibly endogenous: … |
| The power analysis returns a verdict of failure against a gray band of 0.4–0.8. | The design has power of {{power:power}} for the target effect, below conventional thresholds. |
| The R3 specification restores pre-1990 democracies. | The specification that restores countries democratizing before 1990 gives … |
| The automated control-group diagnostic reports 159 never-treated units. | The estimation sample contains 66 never-treated countries. |
| Two errors in the audit report should be noted for the record. | **不写。**跟审计的分歧写进收尾说明和留痕，不进论文 |

审计的每条意见仍然要在局限里交代（见下），但交代的是**那个局限**，不是「审计说了什么」。
