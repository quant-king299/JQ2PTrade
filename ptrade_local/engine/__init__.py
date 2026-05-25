"""
MiniPTrade — 轻量级 PTrade 本地回测引擎
零三方回测框架依赖，直接从 DuckDB 加载数据
"""
from .backtester import BacktestEngine


def run_backtest(
    strategy_path: str,
    start_date: str = '2024-01-01',
    end_date: str = '2024-12-31',
    capital: float = 100000,
    duckdb_path: str = 'D:/StockData/stock_data.ddb',
    frequency: str = '1d',
    benchmark: str = '000300.SS',
):
    engine = BacktestEngine(
        duckdb_path=duckdb_path,
        start_date=start_date,
        end_date=end_date,
        initial_capital=capital,
        frequency=frequency,
        benchmark=benchmark,
    )
    return engine.run(strategy_path)
