# 数字溯源的复现步骤

「数字溯源」一节的对比图，每一行回显都可以照下面的步骤复现。

```bash
RUN=/tmp/oss-demo/run
mkdir -p "$RUN"
cp runs/<任意一次运行>/results.json "$RUN"/   # 带 main 估计的结果文件

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

手打的 `0.188` 跟真实估计值完全相同，照样被拒：检查看的是这个数字找不找得到出处。
