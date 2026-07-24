# qlibAssistant 端到端复现证据报告

生成日期：2026-07-17  
用途：研究与技术验证；未配置券商连接、自动下单、实盘交易、Git push 或结果上传。

## 1. 结论

完成。指定本地归档经完整性校验后解压到隔离目录；当前代码规定的
5 种算法 × 5 个训练窗口共 25 个 recorder 已从空实验目录真实训练。
独立审计确认 25/25 模型、任务、预测和信号指标均有效。模型筛选最终
选中 6 个 recorder，生成 2026-07-16 的 300 条未过滤结果和 74 条稳健性
过滤结果。自动任务已安装、手工触发通过，并证明数据未变化时不会重训。

## 2. 服务器环境与仓库

- Linux `3.10.0-1160.el7.x86_64`，x86_64，16 CPU，62 GiB RAM。
- Python 3.12.13；Git 2.29.2。
- 仓库：`https://github.com/touhoufan2024/qlibAssistant.git`
- 分支：`main`
- commit：`77547728eb0857008be0783223d0d2c027dd5fab`
- 实际依赖版本：`/data0/zhangpeng6/qlib/reproduction/evidence/pip-freeze.txt`
- 仓库证据：`/data0/zhangpeng6/qlib/reproduction/evidence/repository.txt`

## 3. 数据归档与数据验证

- 固定输入：`/data0/zhangpeng6/rsync/qlib_bin.tar.gz`
- 大小：558,958,571 bytes。
- mtime：`2026-07-17 14:23:33.000000000 +0800`
- SHA-256：`36d94a64b7f3f688914ca1a8ea6b8f53114680fd521b1c33462dfd88fd1760a7`
- `tar -tzf` 退出状态 0：
  `/data0/zhangpeng6/qlib/reproduction/evidence/archive_integrity.txt`
- 隔离数据目录：`/data0/zhangpeng6/qlib/reproduction/data`
- calendar：2000-01-04 至 2026-07-16，共 6,429 日。
- CSI300 历史成分 949，只在最新日有效的成分 300。
- 5 只抽样股票的 open/high/low/close/volume 共 50×5，NaN 均为 0。
- Alpha158 抽样为 2,400×158，有限值 378,779。
- Qlib 初始化与完整数据证据：
  `/data0/zhangpeng6/qlib/reproduction/evidence/data-validation.txt`

没有下载替代数据、运行上游数据生成器、覆盖用户 Qlib 数据或修改原归档。

## 4. 实际训练矩阵与状态

当前代码的真实矩阵为 XGBoost、Linear、DoubleEnsemble、LightGBM、
CatBoost × 1/2/3/4/5 年窗口，数据集 Alpha158，股票池 CSI300，共 25 项。
README 中部分“20 models”文字是过时描述；执行以
`script/run.py` 和 `roll/traincli.py::start_custom` 为准。

| 算法 | recorder | 最终批次 UTC | 状态 |
|---|---:|---|---|
| XGBoost | 5 | 07:25:11–07:28:47 | 成功 |
| Linear | 5 | 07:28:47–07:32:19 | 成功 |
| DoubleEnsemble | 5 | 恢复批次 09:17:33–09:45:57 | 成功 |
| LightGBM | 5 | 09:45:57–09:49:28 | 成功 |
| CatBoost | 5 | 09:49:28–09:53:18 | 成功 |

训练状态：`/data0/zhangpeng6/qlib/reproduction/status`  
算法日志：`/data0/zhangpeng6/qlib/reproduction/logs/training`  
训练前空目录证明：
`/data0/zhangpeng6/qlib/reproduction/evidence/mlruns-empty-before-training.txt`

## 5. 失败、恢复与修复

- 默认 Python 包索引提供了需要本机编译的未来版本，旧 GCC 无法构建；
  改用官方 PyPI 的固定 wheel 兼容版本，最终安装成功。
- 首次克隆包装命令留下空仓库；使用同一目标仓库显式 shallow fetch 修复，
  最终 commit 已固定。
- 最初的普通 `nohup` 被命令会话回收，改用持久会话/`setsid` 入口。
- DoubleEnsemble 最后窗口随用户退出会话中断。恢复启动器跳过 XGBoost、
  Linear 和 DoubleEnsemble 已完成的前四窗口，只重新执行缺失窗口，随后
  自动继续 LightGBM、CatBoost。
- 原训练代码捕获子进程错误后可能仍返回批次成功；已改为收集失败窗口并
  抛出非零错误，排除隐藏失败。
- GPU driver 警告和未安装 PyTorch 提示不影响本矩阵；五种目标模型均为
  CPU 模型且逐项审计通过。

## 6. 模型指标、筛选与权重

独立审计结果：5 个实验、25 个 recorder、25 个有效、0 个问题。
审计会验证 `params.pkl` 可反序列化、`task`、`sig_analysis`、非空预测、
非全 NaN 预测，以及有限的 IC、ICIR、Rank IC、Rank ICIR。

- 审计摘要：
  `/data0/zhangpeng6/qlib/reproduction/evidence/final-audit/model_audit.json`
- 25 个模型逐项指标：
  `/data0/zhangpeng6/qlib/reproduction/evidence/results/all_25_model_metrics.csv`
- 筛选模型与权重：
  `/data0/zhangpeng6/qlib/reproduction/evidence/results/selected_models_and_weights.csv`

配置要求四项指标均大于 0.001，最终选中 6 个模型：

| 算法/训练起点 | IC | ICIR | Rank IC | Rank ICIR | 权重 |
|---|---:|---:|---:|---:|---:|
| LightGBM / 2021-07-17 | 0.010264 | 0.065600 | 0.034111 | 0.209185 | 0.159 |
| LightGBM / 2022-07-17 | 0.001616 | 0.014493 | 0.019378 | 0.149728 | 0.114 |
| LightGBM / 2023-07-17 | 0.023886 | 0.221279 | 0.027997 | 0.238617 | 0.181 |
| DoubleEnsemble / 2021-07-17 | 0.015691 | 0.095834 | 0.043778 | 0.268205 | 0.203 |
| DoubleEnsemble / 2022-07-17 | 0.013994 | 0.096550 | 0.034062 | 0.229229 | 0.174 |
| DoubleEnsemble / 2023-07-17 | 0.005290 | 0.037098 | 0.027979 | 0.222392 | 0.169 |

舍入权重合计 1.000。

## 7. Top 10 选股结果

预测日期与数据最新交易日均为 2026-07-16。该日期之后的收益尚不可用，
所以 `real_label` 为 NaN；没有填造未来标签。

未过滤 Top 10：
`/data0/zhangpeng6/qlib/reproduction/evidence/results/top10_unfiltered.csv`

稳健性过滤后 Top 10：
`/data0/zhangpeng6/qlib/reproduction/evidence/results/top10_filtered.csv`

过滤条件为 STD5/20/60 上限、STD5 相对 STD60 上限，以及
ROC10/20/60 下限和 ROC20 上限；300 个候选中 74 个通过。
两份 Top 10 均含股票代码、名称、`avg_score`、`pos_ratio`、
STD5/20/60、ROC10/20/60 和 `real_label`。

## 8. 模型、结果与日志绝对路径

- 模型实验：`/data0/zhangpeng6/qlib/reproduction/mlruns`
- 最新结果：
  `/data0/zhangpeng6/qlib/reproduction/analysis/selection_20260717_18_48_23`
- 稳定链接：`/data0/zhangpeng6/qlib/reproduction/analysis/latest`
- `total.csv`、`total.md`、`2026-07-16_ret.csv`、
  `2026-07-16_filter_ret.csv` 均在上述最新结果目录。
- 训练日志：`/data0/zhangpeng6/qlib/reproduction/logs/training`
- 筛选日志：`/data0/zhangpeng6/qlib/reproduction/logs/model-selection.log`
- 自动任务日志：`/data0/zhangpeng6/qlib/reproduction/logs/pipeline`
- 当前状态：
  `/data0/zhangpeng6/qlib/reproduction/state/pipeline-state.json`

## 9. 本地代码修改

仓库内仅有两处功能修改：

1. `roll/myconfig.py`：四种树模型的线程数 20 改为 16，与服务器 CPU
   数一致，避免超额并发；没有减少模型、样本、特征或窗口。
2. `roll/traincli.py`：记录失败子进程的窗口，批次结束时抛出
   `RuntimeError`，使隐藏失败成为非零退出状态。

任务专用新增文件位于仓库外的 `reproduction/`：
训练启动器、独立模型审计器、结果证据生成器、自动流水线、状态辅助器和
控制入口。未执行 `model decompress_mlruns`，没有预训练模型。

## 10. 一键运行、状态与恢复

```bash
cd /data0/zhangpeng6/qlib
reproduction/reproctl.sh start
reproduction/reproctl.sh status
reproduction/reproctl.sh logs 100
reproduction/reproctl.sh results
reproduction/reproctl.sh stop
reproduction/reproctl.sh retry
```

直接前台运行：`reproduction/run_pipeline.sh`。
`retry` 会使五种算法重新扫描；`traincli` 根据 recorder 中的训练窗口跳过
所有有效项，只训练失败或缺失项。

## 11. 定时自动化状态

- 调度器：用户 crontab；`crond` 已确认 `active`。
- 服务器时区：CST / UTC+8。
- 表达式：`30 18 * * 1-5`
- 实际触发：工作日 18:30 CST，即 10:30 UTC。
- 启用：`reproduction/reproctl.sh enable`
- 禁用：`reproduction/reproctl.sh disable`

首次手工触发在 10:45:25–10:48:38 UTC 完成数据校验、模型审计和选股，
明确记录 `train_required=false`。第二次数据未变化触发在
10:49:19–10:49:24 UTC 完成，训练和 selection 均被跳过，结构化 JSONL
逐行解析通过。锁竞争测试返回约定退出码 75。

## 12. 尚存限制

- 服务器 glibc 版本较旧，当前固定 XGBoost wheel 可运行，但未来升级
  XGBoost 可能需要升级操作系统或继续固定兼容版本。
- 股票名称由仓库现有 AkShare 多源逻辑获取；训练数据、特征、模型和预测
  全部来自指定本地 Qlib 归档。若名称服务暂时不可用，核心预测仍可生成，
  但名称列可能为空。
- 最新预测日尚无未来真实收益，所以本次无法进行该日事后收益验证。
- 本部署只生成研究结果，不连接券商或执行交易。
