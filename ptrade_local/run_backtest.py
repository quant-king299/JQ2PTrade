"""
MiniPTrade 本地回测运行器
直接从 DuckDB 加载数据，零三方回测框架依赖

用法:
    python run_backtest.py strategies/my_strategy.py
    python run_backtest.py strategies/my_strategy.py --start 2024-01-01 --end 2024-12-31 --capital 200000
    python run_backtest.py strategies/my_strategy.py --duckdb-path D:/StockData/stock_data.ddb
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="MiniPTrade 本地回测引擎")
    parser.add_argument("strategy", help="策略文件路径")
    parser.add_argument("--start", default="2024-01-01", help="回测开始日期")
    parser.add_argument("--end", default="2024-12-31", help="回测结束日期")
    parser.add_argument("--capital", type=float, default=100000, help="初始资金")
    parser.add_argument("--duckdb-path", default="D:/StockData/stock_data.ddb",
                        help="DuckDB 数据库路径")
    parser.add_argument("--frequency", default="1d", choices=["1d"], help="K线频率")
    parser.add_argument("--benchmark", default="000300.SS", help="基准指数代码")
    args = parser.parse_args()

    # 将 engine 所在目录加入 path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    from engine import run_backtest

    run_backtest(
        strategy_path=os.path.abspath(args.strategy),
        start_date=args.start,
        end_date=args.end,
        capital=args.capital,
        duckdb_path=args.duckdb_path,
        frequency=args.frequency,
        benchmark=args.benchmark,
    )


if __name__ == "__main__":
    main()
