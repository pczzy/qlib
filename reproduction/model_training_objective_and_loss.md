# 模型训练目标与损失函数说明

当前五类模型的预测目标相同，但具体训练器不同。核心结论是：它们训练时优化的是回归误差，不是直接优化 IC 或 Rank IC。

## 统一预测目标

原始 Label 定义为：

```text
Ref($close, -2) / Ref($close, -1) - 1
```

即对预测日 `t`：

```text
原始收益 = close(t+2) / close(t+1) - 1
```

不过，Alpha158 在训练前会对每天的股票 Label 做横截面标准化：

```text
y(t,i) = [return(t,i) - 当日股票平均收益] / 当日收益标准差
```

对应 Qlib 的 `CSZScoreNorm`。所以模型实际拟合的是“该股票未来收益相对当日股票池的标准分”，不是未经处理的绝对收益率。

这也是为什么模型输出的 `0.04` 不能解释为预期上涨 `4%`，它主要是横截面排序分数。

## 各模型损失函数

| 模型 | 目标函数/损失 | 当前配置 |
|---|---|---|
| XGBoost | 平方误差回归 | `reg:squarederror` |
| LightGBM | 均方误差 | `objective=mse` |
| CatBoost | 均方根误差 | `loss_function=RMSE` |
| DoubleEnsemble | 均方误差 | `loss=mse` |
| Linear | Ridge 平方误差加 L2 正则 | `alpha=0.05` |

### XGBoost

实际保存模型确认：

```text
objective = reg:squarederror
eval_metric = rmse
```

优化目标近似为：

```text
MSE = mean((预测分 - 标准化Label)²)
```

`eval_metric=rmse` 主要用于验证集监控和早停。配置位于 `qlibAssistant/roll/myconfig.py`。

### LightGBM

配置为：

```python
loss = "mse"
```

即最小化：

```text
mean((预测分 - 标准化Label)²)
```

同时带有较强的树模型正则：

```text
lambda_l1 = 205.6999
lambda_l2 = 580.9768
```

### CatBoost

配置为：

```python
loss = "RMSE"
```

即：

```text
sqrt(mean((预测分 - 标准化Label)²))
```

RMSE 与 MSE 的最优解方向一致，只是展示尺度不同。

### DoubleEnsemble

每个内部 LightGBM 子模型使用：

```text
objective = mse
```

总共训练 6 个子模型。它还会根据前序模型的样本损失重新分配样本权重，并选择特征，但基础损失仍然是：

```text
(预测分 - 标准化Label)²
```

### Linear

使用 Ridge 回归：

```text
min ||y - Xw||² + 0.05 × ||w||²
```

前半部分是平方误差，后半部分是 L2 正则，用来抑制因子系数过大。

## IC 在哪里使用

IC、Rank IC 目前不是训练损失，而是在模型完成训练后用于：

1. 测试期表现评估。
2. 筛掉 IC、ICIR、Rank IC、Rank ICIR 不为正的模型。
3. 使用 Rank ICIR 给入选模型分配组合权重。

因此当前流程是：

```text
MSE/RMSE 训练
    ↓
得到预测分数
    ↓
计算测试期 IC / Rank IC
    ↓
筛选正指标模型
    ↓
按 Rank ICIR 加权组合
```

这也解释了为什么“损失下降”不保证 Rank IC 一定上升：MSE 关注预测值误差，而 Rank IC 只关注排序。当前系统最终用于 Top-N 选股，业务目标其实更接近 Rank IC，但训练器优化的是 MSE/RMSE，两者并不完全一致。
