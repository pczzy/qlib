# qlibAssistant 复现任务运维说明

本目录保存 `todo.md` 对应的完整复现、模型训练、选股和自动化部署。
用途仅限研究与技术验证，不连接券商、不自动下单，也不会自动上传或
Git push 数据和结果。

完整的执行证据和限制说明见：
[reproduction/FINAL_REPORT.md](reproduction/FINAL_REPORT.md)。

## 日常控制

所有命令均从工作区根目录执行：

```bash
cd /data0/zhangpeng6/qlib
```

查看当前任务状态和最近一次成功状态：

```bash
reproduction/reproctl.sh status
```

查看最近 100 条结构化流水线日志：

```bash
reproduction/reproctl.sh logs 100
```

查看最新结果目录：

```bash
reproduction/reproctl.sh results
```

查看模型成果仪表盘：

```bash
reproduction/dashboard/start.sh
```

默认监听 `0.0.0.0:8765`，页面实时读取 `evidence/results` 和流水线状态，
包含历史预测复盘、Top 10 胜率和收益统计，每分钟自动刷新；也可通过
`PORT=9000 reproduction/dashboard/start.sh`
指定其他端口。仪表盘使用 HTTP Basic 认证，凭据摘要保存在不纳入 Git 的
`reproduction/dashboard/.dashboard.env`。

手工启动一次完整检查：

```bash
reproduction/reproctl.sh start
```

该命令先探测 GitHub 最新数据包，再直连新浪探测是否有晚于当前日历的交易
日。GitHub 不可用或新资产尚未发布时，已有本地基线仍会继续走新浪增量。
新浪无新交易日且 GitHub SHA256 未变化时会验证现有模型，并跳过数据换代、
昂贵训练和重复选股。出现新交易日后会刷新预测和选股；只有模型审计失败，
或数据最新交易日晚于现有模型任务的 `test_end` 时才启动训练。

新浪更新在正式切换前自动执行以下闸门：5 只成分股多数表决发现新交易日、
300 只 CSI300 全量抓取、最近 GitHub 基线交易日回放、十字段误差检查、复权
事件处理、Qlib 二进制读取及 300 股票池检查。更新先写入硬链接 staging，验证
通过后原子切换；失败时保留当前数据。请求显式禁用代理。每次探测和更新报告
写入 `reproduction/evidence/sina-auto-*.json`。

这里的 `test_end` 是模型测试窗口截止日，不是训练数据截止日。状态文件和
日报分别记录 `model_train_end`、`model_test_end`、
`model_data_archive_sha256` 与当前数据包 SHA256，不能用 `test_end` 判断
模型参数是否使用了最新交易日数据。

停止正在运行的流水线：

```bash
reproduction/reproctl.sh stop
```

扫描并恢复失败或缺失训练项：

```bash
reproduction/reproctl.sh retry
```

该命令会让五种算法重新扫描 recorder；已成功窗口会被跳过，只训练失败
或缺失窗口。

启用或禁用定时任务：

```bash
reproduction/reproctl.sh enable
reproduction/reproctl.sh disable
```

当前计划为每 10 分钟探测一次。`start` 启动的是检查流水线，不代表每次都
重新训练；锁会阻止并发重复运行。检查实际安装状态：

```bash
crontab -l | grep qlib-reproduction-managed
systemctl is-active crond
```

## 主要产物

| 内容 | 路径 |
|---|---|
| 完整证据报告 | `/data0/zhangpeng6/qlib/reproduction/FINAL_REPORT.md` |
| 隔离 Qlib 数据 | `/data0/zhangpeng6/qlib/reproduction/data` |
| 从零训练模型 | `/data0/zhangpeng6/qlib/reproduction/mlruns` |
| 最新选股稳定链接 | `/data0/zhangpeng6/qlib/reproduction/analysis/latest` |
| 25 个模型指标 | `/data0/zhangpeng6/qlib/reproduction/evidence/results/all_25_model_metrics.csv` |
| 筛选模型和权重 | `/data0/zhangpeng6/qlib/reproduction/evidence/results/selected_models_and_weights.csv` |
| 未过滤 Top 10 | `/data0/zhangpeng6/qlib/reproduction/evidence/results/top10_unfiltered.csv` |
| 过滤后 Top 10 | `/data0/zhangpeng6/qlib/reproduction/evidence/results/top10_filtered.csv` |
| 模型完整性审计 | `/data0/zhangpeng6/qlib/reproduction/evidence/final-audit/model_audit.json` |
| 训练日志 | `/data0/zhangpeng6/qlib/reproduction/logs/training` |
| 流水线事件日志 | `/data0/zhangpeng6/qlib/reproduction/logs/pipeline` |
| 流水线状态 | `/data0/zhangpeng6/qlib/reproduction/state/pipeline-state.json` |

最新选股目录包含：

- `total.csv`：每个入选模型的逐股票原始预测；
- `total.md`：参数、模型指标和权重；
- `YYYY-MM-DD_ret.csv`：完整 CSI300 加权结果；
- `YYYY-MM-DD_filter_ret.csv`：稳健性过滤后的结果。

## 日常报告

定时或手工流水线成功后会自动刷新：

```text
reproduction/evidence/results/summary.json
reproduction/evidence/results/selected_models_and_weights.csv
reproduction/evidence/results/top10_unfiltered.csv
reproduction/evidence/results/top10_filtered.csv
reproduction/evidence/results/all_25_model_metrics.csv
```

快速查看当日报告摘要：

```bash
cat reproduction/evidence/results/summary.json
```

查看未过滤和过滤后 Top 10：

```bash
column -s, -t < reproduction/evidence/results/top10_unfiltered.csv
column -s, -t < reproduction/evidence/results/top10_filtered.csv
```

若系统没有 `column`，可使用：

```bash
reproduction/venv/bin/python -c \
  'import pandas as p; print(p.read_csv("reproduction/evidence/results/top10_filtered.csv").to_string(index=False))'
```

需要手工刷新日报证据时：

```bash
reproduction/venv/bin/python reproduction/build_result_evidence.py
```

日报应至少关注：

1. `prediction_date` 是否等于数据最新交易日；
2. `selected_model_count` 和 `selected_weight_sum` 是否合理；
3. 未过滤及过滤后股票数量；
4. Top 10 的股票代码、名称、`avg_score`、`pos_ratio` 和过滤字段；
5. `real_label_non_null`。预测日尚无未来收益时为 0 属于正常情况；
6. `model_audit.json` 中 `success` 是否为 `true`。
7. `model_train_end`、`model_test_end` 和两个数据包 SHA256 是否符合预期。

选股集成会先把每个模型的当日分数转换为横截面百分位排名，消除不同算法
输出尺度不一致造成的隐性权重；仅保留满足配置指标门槛的模型，再以
Rank IC 权重与等权各占一半进行收缩集成。每次结果的实际方法、门槛和权重
记录在选股目录的 `ensemble_metadata.json`。

本自动化只生成本地日报文件，不发送邮件、消息或外部通知。如需对外发送，
应先单独确认接收方、凭据管理和数据合规要求。

## 故障定位

先执行：

```bash
reproduction/reproctl.sh status
reproduction/reproctl.sh logs 200
```

再检查对应算法日志：

```bash
tail -n 100 reproduction/logs/training/DoubleEnsemble.log
tail -n 100 reproduction/logs/training/LightGBM.log
tail -n 100 reproduction/logs/training/CatBoost.log
```

流水线锁冲突的约定退出码为 75，表示已有实例运行，不应再次启动。
