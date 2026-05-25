"""
MiniPTrade 绩效报告生成
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data_loader import DataLoader


def generate_report(
    daily_values: list[tuple],       # [(date, portfolio_value), ...]
    starting_cash: float,
    trades: list,
    benchmark_code: str = '000300.SS',
    data_loader: DataLoader | None = None,
    trading_days: list[pd.Timestamp] | None = None,
) -> str:
    if not daily_values:
        return "无回测数据"

    dates = [d for d, _ in daily_values]
    values = [v for _, v in daily_values]
    final_value = values[-1]

    # 总收益率
    total_return = (final_value - starting_cash) / starting_cash

    # 年化收益率
    n_days = len(daily_values)
    if n_days > 1:
        annual_return = (1 + total_return) ** (252 / n_days) - 1
    else:
        annual_return = 0.0

    # 最大回撤
    peak = starting_cash
    max_drawdown = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_drawdown:
            max_drawdown = dd

    # 日收益率序列
    daily_returns = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            daily_returns.append(values[i] / values[i - 1] - 1)
        else:
            daily_returns.append(0.0)

    # Sharpe ratio (无风险利率取 0)
    if daily_returns and np.std(daily_returns) > 0:
        sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252)
    else:
        sharpe = 0.0

    # 交易统计
    n_trades = len(trades)
    n_buy = sum(1 for t in trades if t.amount > 0)
    n_sell = sum(1 for t in trades if t.amount < 0)
    total_commission = sum(t.commission for t in trades)

    # 胜率
    # 简化: 看每次卖出的盈亏
    sell_trades = [t for t in trades if t.amount < 0]
    if sell_trades:
        wins = sum(1 for t in sell_trades if t.price > 0)
        win_rate = wins / len(sell_trades) * 100
    else:
        win_rate = 0.0

    # 基准收益 (如果有数据)
    bench_return = None
    if data_loader and trading_days:
        bench_start = data_loader.get_price(benchmark_code, trading_days[0])
        bench_end = data_loader.get_price(benchmark_code, trading_days[-1])
        if bench_start > 0 and bench_end > 0:
            bench_return = (bench_end - bench_start) / bench_start

    # 格式化输出
    lines = [
        f"初始资金: {starting_cash:>14,.2f}",
        f"最终净值: {final_value:>14,.2f}",
        f"总收益:   {total_return:>13.2%}",
        f"年化收益: {annual_return:>13.2%}",
        f"最大回撤: {max_drawdown:>13.2%}",
        f"Sharpe:   {sharpe:>13.2f}",
        "",
        f"交易次数: {n_trades} (买{n_buy} / 卖{n_sell})",
        f"总佣金:   {total_commission:>14,.2f}",
        f"胜率:     {win_rate:>13.1f}%",
    ]
    if bench_return is not None:
        lines.append(f"基准收益: {bench_return:>13.2%}")
        excess = total_return - bench_return
        lines.append(f"超额收益: {excess:>13.2%}")

    return "\n".join(lines)
