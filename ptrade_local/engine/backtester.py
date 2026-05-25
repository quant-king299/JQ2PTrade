"""
MiniPTrade 回测引擎 — 主事件循环
"""
from __future__ import annotations

import os
import sys
import types
import traceback
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .data_loader import DataLoader
from .context import Portfolio, Blotter, Context
from .api import create_api_namespace
from .report import generate_report


class BacktestEngine:
    def __init__(
        self,
        duckdb_path: str = 'D:/StockData/stock_data.ddb',
        start_date: str = '2024-01-01',
        end_date: str = '2024-12-31',
        initial_capital: float = 100000,
        frequency: str = '1d',
        benchmark: str = '000300.SS',
    ):
        self.duckdb_path = duckdb_path
        self.start_date = pd.Timestamp(start_date)
        self.end_date = pd.Timestamp(end_date)
        self.initial_capital = initial_capital
        self.frequency = frequency
        self.benchmark = benchmark

    def run(self, strategy_path: str) -> str | None:
        strategy_path = os.path.abspath(strategy_path)
        if not os.path.exists(strategy_path):
            print(f"错误: 策略文件不存在: {strategy_path}")
            return None

        strategy_name = Path(strategy_path).stem

        # 1. 加载数据
        data_loader = DataLoader(self.duckdb_path)
        data_bundle = data_loader.load(self.start_date, self.end_date)
        trading_days = data_bundle.trading_days
        all_codes = data_bundle.all_stock_codes

        if not trading_days:
            print("错误: 无交易日数据")
            return None

        # 2. 创建 context
        portfolio = Portfolio(self.initial_capital, data_loader)
        blotter = Blotter()
        context = Context(portfolio, blotter)

        # 3. 创建 API 命名空间
        api_ns, trades = create_api_namespace(
            context, portfolio, data_loader, trading_days, all_codes,
        )

        # 4. 读取并执行策略
        strategy_code = Path(strategy_path).read_text(encoding='utf-8')

        # 全局对象 g
        g = types.SimpleNamespace()

        # 构建策略执行命名空间
        ns = {
            '__builtins__': __builtins__,
            '__name__': '__main__',
            '__file__': strategy_path,
            'g': g,
            'pd': pd,
            'np': np,
            'os': os,
            'sys': sys,
            'datetime': datetime,
            'timedelta': timedelta,
            'traceback': traceback,
            'types': types,
        }
        # 注入 API 函数
        for k, v in api_ns.items():
            if not k.startswith('_'):
                ns[k] = v

        # 编译并执行策略（定义函数）
        try:
            compiled = compile(strategy_code, strategy_path, 'exec')
            exec(compiled, ns)
        except Exception as e:
            print(f"策略加载失败: {type(e).__name__}: {e}")
            traceback.print_exc()
            return None

        init_fn = ns.get('initialize')
        bts_fn = ns.get('before_trading_start')
        hd_fn = ns.get('handle_data')
        ate_fn = ns.get('after_trading_end')
        data_proxy = api_ns['_data_proxy']

        # 5. 执行 initialize
        print(f"\n策略: {strategy_name}")
        print(f"回测区间: {self.start_date.date()} ~ {self.end_date.date()}")
        print(f"初始资金: {self.initial_capital:,.0f}")
        print("-" * 50)

        try:
            if init_fn:
                init_fn(context)
        except Exception as e:
            print(f"initialize 失败: {type(e).__name__}: {e}")
            traceback.print_exc()
            return None

        # 6. 主循环
        daily_values = []   # (date, portfolio_value)
        total_days = len(trading_days)

        for i, day in enumerate(trading_days):
            context.current_dt = day

            try:
                if bts_fn:
                    bts_fn(context, data_proxy)
            except Exception as e:
                print(f"[{day.date()}] before_trading_start 异常: {e}")

            try:
                if hd_fn:
                    hd_fn(context, data_proxy)
            except Exception as e:
                print(f"[{day.date()}] handle_data 异常: {e}")

            try:
                if ate_fn:
                    ate_fn(context, data_proxy)
            except Exception as e:
                print(f"[{day.date()}] after_trading_end 异常: {e}")

            daily_values.append((day, portfolio.portfolio_value))

            # 进度
            if (i + 1) % 50 == 0 or (i + 1) == total_days:
                pct = (i + 1) / total_days * 100
                print(f"  进度: {i+1}/{total_days} ({pct:.0f}%) "
                      f"净值: {portfolio.portfolio_value:,.0f}")

        # 7. 生成报告
        report = generate_report(
            daily_values=daily_values,
            starting_cash=self.initial_capital,
            trades=trades,
            benchmark_code=self.benchmark,
            data_loader=data_loader,
            trading_days=trading_days,
        )

        print("\n" + "=" * 50)
        print("回测完成!")
        print(report)
        return report
