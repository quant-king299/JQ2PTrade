# MiniPTrade 示例策略库

本目录收录可在 [MiniPTrade](../) 本地回测引擎上直接运行的策略示例。
所有策略 100% 兼容 PTrade 平台 API，**同一份代码可在 PTrade 云端回测/实盘零修改运行**。

## 📚 策略一览

| 策略 | 文件 | 难度 | 类型 | 推荐场景 |
|---|---|---|---|---|
| 双均线 Demo | [ma_cross_demo.py](./ma_cross_demo.py) | ⭐ 入门 | 趋势跟随 | 第一次接触 PTrade API |
| ETF 均线轮动 | [etf_ma_strategy.py](./etf_ma_strategy.py) | ⭐ 入门 | 资产配置 | 学习多标的轮动 |
| RSI 技术指标 | [rsi_strategy.py](./rsi_strategy.py) | ⭐⭐ 进阶 | 震荡策略 | 学习自定义指标封装 |
| 行业动量（四大搅屎棍） | [sw_momentum_strategy.py](./sw_momentum_strategy.py) | ⭐⭐⭐ 中阶 | 行业轮动 | 学习申万行业数据 |
| 五合一打板 | [五合一打板策略.py](./五合一打板策略.py) | ⭐⭐⭐⭐⭐ 高阶 | 短线打板 | 学习复杂选股 + 评分体系 |

## 🚀 快速开始

```bash
cd code_converter/ptrade_local

# 最简单的双均线 demo
python run_backtest.py strategies/ma_cross_demo.py \
    --start 2024-01-01 --end 2024-12-31 --capital 100000

# 五合一打板（推荐，已有完整[介绍文档](./五合一打板策略_介绍.md)）
python run_backtest.py strategies/五合一打板策略.py \
    --start 2024-10-01 --end 2024-12-31 --capital 200000
```

## 🧭 学习路径建议

```
ma_cross_demo.py        ← 理解 PTrade 三钩子 (initialize / handle_data / after_trading_end)
        ↓
etf_ma_strategy.py      ← 学会 get_history + order_target_value 标准范式
        ↓
rsi_strategy.py         ← 学会自定义指标 + 信号生成
        ↓
sw_momentum_strategy.py ← 学会批量股票 + 行业数据
        ↓
五合一打板策略.py        ← 完整工程化策略（多模式 + 评分 + 风控 + 大盘判断）
```

## 📖 策略来源与版权

| 策略 | 原作者 | 来源 |
|---|---|---|
| 双均线 / ETF均线 / RSI | 通用教学策略 | 本项目原创 |
| 行业动量（四大搅屎棍） | 聚宽社区 | https://www.joinquant.com/post/49085 |
| 五合一打板 | aric_zq81 | 聚宽社区 |

> 所有源自社区的策略仅用于学习交流，原作者署名已保留。
> **本项目不构成投资建议，回测结果不代表实盘收益。**

## 🔒 不在本目录的策略

以下策略因依赖外部数据源或仍在迭代，**仅本地保留，不上传 GitHub**：

- `dama_jinx_strategy.py` — 菜场大妈行业冥灯 V2，依赖 Tushare 高频数据接口
- `dama_jinx_strategy_ptrade.py` — 同上的 PTrade 实盘部署版

如需交流这两条策略，欢迎在 Issues 区讨论。

## 🤝 贡献新策略

欢迎 PR 提交新策略，请确保：

1. 文件头注明原作者与来源链接（如有）
2. `initialize` 中不写死任何 token / 密码（用占位符）
3. 至少跑通一次完整回测，附 1~2 行结果摘要到文件头注释
4. 风险提示不可少："仅供学习，不构成投资建议"
