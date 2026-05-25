# JQ2PTrade - 聚宽策略转 PTrade 代码转换器

将聚宽（JoinQuant）策略代码自动转换为 PTrade 平台格式，帮助快速迁移策略。内置 **MiniPTrade** 本地回测引擎，无需上传云端即可在本地验证策略。

## 快速开始

```bash
pip install -r requirements.txt

# 转换单个文件
python cli.py your_strategy.py

# 指定输出文件
python cli.py your_strategy.py -o output.py

# GUI 模式
python run_converter_gui.py
```

## 项目结构

```
JQ2PTrade/
├── cli.py                  # 命令行入口
├── run_converter_gui.py    # GUI 入口
├── run_converter.bat       # Windows 一键启动
├── api_mapping.json        # API 映射规则
├── converters/             # 核心转换逻辑
│   ├── jq_to_ptrade_unified_v3.py   # 聚宽 → PTrade 转换器
│   ├── jq_to_easyxt.py              # 聚宽 → EasyXT 转换器
│   └── ptrade_strategy_unified.py   # PTrade 策略模板
├── utils/                  # 工具模块
│   ├── code_parser.py      # 代码解析
│   └── code_generator.py   # 代码生成
├── samples/                # 聚宽示例策略
├── ptrade_local/           # MiniPTrade 本地回测引擎
│   ├── engine/             # 核心引擎代码
│   │   ├── __init__.py     # 公开接口
│   │   ├── data_loader.py  # DuckDB 数据加载
│   │   ├── context.py      # Position/Portfolio/Context
│   │   ├── api.py          # PTrade API 兼容层
│   │   ├── backtester.py   # 回测主循环
│   │   └── report.py       # 绩效报告
│   ├── strategies/         # 示例策略
│   └── run_backtest.py     # 回测 CLI 入口
└── ptrade代码/             # 转换后的 PTrade 策略示例
    ├── MACD.txt
    ├── rsi.txt
    └── ETF均线_241217.txt
```

## 支持的转换

| 源平台 | 目标平台 | 状态 |
|--------|----------|------|
| 聚宽 JoinQuant | PTrade | ✅ |
| 聚宽 JoinQuant | EasyXT (miniQMT) | ✅ |

## 使用说明

### 1. 准备聚宽策略代码

从聚宽平台导出你的策略 `.py` 文件，或参考 `samples/` 目录下的示例。

### 2. 运行转换

```bash
python cli.py samples/jq_sample_strategy.py -o my_ptrade_strategy.py
```

### 3. 在 PTrade 中使用

将生成的 `.py` 文件内容复制到 PTrade 策略编辑器中运行。

## MiniPTrade 本地回测

转换后的 PTrade 策略可以用 MiniPTrade 在本地直接回测验证，零三方回测框架依赖，直接从 DuckDB 加载数据。

```bash
cd ptrade_local

# 回测指定策略
python run_backtest.py strategies/ma_cross_demo.py

# 指定参数
python run_backtest.py strategies/ma_cross_demo.py --start 2024-01-01 --end 2024-06-30 --capital 200000

# 指定 DuckDB 路径
python run_backtest.py strategies/my_strategy.py --duckdb-path D:/StockData/stock_data.ddb
```

### 支持的 PTrade API

| 类别 | API |
|------|-----|
| 数据 | `get_history`, `get_price`, `get_fundamentals`, `get_Ashares`, `get_stock_status` |
| 交易 | `order`, `order_target`, `order_value`, `order_target_value` |
| 持仓 | `get_position`, `get_positions`, `set_yesterday_position` |
| 配置 | `set_benchmark`, `set_slippage`, `set_commission`, `set_universe` |
| 工具 | `log.info/warning/error`, `g` 全局对象 |

### 依赖

仅需 `duckdb`、`pandas`、`numpy`（项目已有，无需额外安装）。

## 许可证

MIT License
