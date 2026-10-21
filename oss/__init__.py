"""OpenSocialScience 工具箱。

分层（DECISIONS.md 1.4）：
    core/   确定性计算 —— 宿主无关，永远不变
    host/   宿主适配   —— 运行期：运行目录、跨模型审稿后端
    hosts/  宿主档案   —— 静态事实表：谁在跑我们、技能铺哪儿、投影怎么做
    cli/    命令行入口

**core/ 绝不 import host/ 与 hosts/。**（hosts/ 可以 import core/：执行者身份要读 run_state）
"""

__version__ = "0.1.0"
