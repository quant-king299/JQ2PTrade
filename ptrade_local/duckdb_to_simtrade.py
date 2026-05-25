#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DuckDB → SimTradeLab 数据桥

从 D:/StockData/stock_data.ddb 读取数据，生成 SimTradeLab 回测所需的 parquet 文件。

用法:
    from duckdb_to_simtrade import DuckDBToSimTradeBridge
    bridge = DuckDBToSimTradeBridge()
    bridge.generate_all(output_dir='./data')

    # 命令行
    python duckdb_to_simtrade.py --output ./data --start 2023-01-01 --end 2024-12-31
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import duckdb
import numpy as np
import pandas as pd


class DuckDBToSimTradeBridge:
    """从 DuckDB 读取数据并生成 SimTradeLab 兼容的 parquet 目录"""

    def __init__(self, duckdb_path: str = 'D:/StockData/stock_data.ddb'):
        self.duckdb_path = duckdb_path
        if not os.path.exists(duckdb_path):
            raise FileNotFoundError(f"DuckDB 文件不存在: {duckdb_path}")

    @staticmethod
    def convert_code(ts_code: str) -> str:
        """Tushare 格式 (.SH) → SimTradeLab 格式 (.SS)"""
        if ts_code.endswith('.SH'):
            return ts_code[:-3] + '.SS'
        return ts_code

    def generate_all(
        self,
        output_dir: str,
        stock_codes: List[str] = None,
        start_date: str = None,
        end_date: str = None,
        skip_if_cached: bool = True,
    ) -> Dict:
        """
        生成完整的 SimTradeLab 数据目录

        Returns:
            统计信息 dict
        """
        output_path = Path(output_dir)
        cache_file = output_path / '.duckdb_cache_info.json'

        # 检查缓存
        if skip_if_cached and self._is_cache_valid(cache_file, start_date, end_date):
            print(f"数据已是最新，跳过生成: {output_dir}")
            return {'cached': True}

        start_time = time.time()
        print(f"从 DuckDB 生成 SimTradeLab 数据...")
        print(f"  DuckDB: {self.duckdb_path}")
        print(f"  输出: {output_dir}")

        # 创建目录
        stocks_dir = output_path / 'stocks'
        exrights_dir = output_path / 'exrights'
        metadata_dir = output_path / 'metadata'
        for d in [stocks_dir, exrights_dir, metadata_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # 1. 生成 stock parquet
        stock_stats = self._generate_stock_parquets(stocks_dir, stock_codes, start_date, end_date)

        # 2. 生成 exrights parquet
        exrights_stats = self._generate_exrights_parquets(exrights_dir)

        # 3. 生成 metadata
        self._generate_metadata(metadata_dir, start_date, end_date, stock_codes=stock_codes)

        # 4. 生成 manifest
        self._generate_manifest(output_path, stock_stats)

        # 写入缓存信息
        elapsed = time.time() - start_time
        cache_info = {
            'duckdb_mtime': os.path.getmtime(self.duckdb_path),
            'generated_at': datetime.now().isoformat(),
            'start_date': start_date or '',
            'end_date': end_date or '',
            'stocks_count': stock_stats.get('total', 0),
            'elapsed_seconds': round(elapsed, 1),
        }
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_info, f, ensure_ascii=False, indent=2)

        print(f"\n生成完成! {stock_stats.get('total', 0)} 只股票, 耗时 {elapsed:.1f}s")
        return {'cached': False, 'stocks': stock_stats, 'exrights': exrights_stats, 'elapsed': elapsed}

    def _is_cache_valid(self, cache_file: Path, start_date: str, end_date: str) -> bool:
        """检查缓存是否有效"""
        if not cache_file.exists():
            return False
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                info = json.load(f)
            # DuckDB 文件是否有变化
            current_mtime = os.path.getmtime(self.duckdb_path)
            if abs(current_mtime - info.get('duckdb_mtime', 0)) > 1:
                return False
            # 日期范围是否覆盖
            if start_date and info.get('start_date') and start_date < info['start_date']:
                return False
            if end_date and info.get('end_date') and end_date > info['end_date']:
                return False
            # 检查 parquet 文件是否存在
            stocks_dir = cache_file.parent / 'stocks'
            if not stocks_dir.exists() or not list(stocks_dir.glob('*.parquet')):
                return False
            return True
        except Exception:
            return False

    def _generate_stock_parquets(
        self,
        stocks_dir: Path,
        stock_codes: List[str] = None,
        start_date: str = None,
        end_date: str = None,
    ) -> Dict:
        """生成 stocks/*.parquet 文件"""
        print("\n[1/4] 生成 stock parquet...")

        con = duckdb.connect(self.duckdb_path, read_only=True)
        try:
            # 用 LAG 窗口函数计算 preclose
            sql = """
                SELECT
                    stock_code,
                    date,
                    open,
                    high,
                    low,
                    close,
                    volume,
                    amount,
                    LAG(close) OVER (PARTITION BY stock_code ORDER BY date) as preclose
                FROM stock_daily
                WHERE period = '1d' AND symbol_type = 'stock'
            """
            conditions = []
            if stock_codes:
                codes_str = ','.join(f"'{c}'" for c in stock_codes)
                conditions.append(f"stock_code IN ({codes_str})")
            if start_date:
                conditions.append(f"date >= '{start_date}'")
            if end_date:
                conditions.append(f"date <= '{end_date}'")
            if conditions:
                sql += " AND " + " AND ".join(conditions)
            sql += " ORDER BY stock_code, date"

            df = con.execute(sql).fetchdf()
            print(f"  查询到 {len(df)} 条日线记录")
        finally:
            con.close()

        if df.empty:
            print("  无数据!")
            return {'total': 0}

        # preclose 为空时（每个股票第一行）用 close 填充
        df['preclose'] = df['preclose'].fillna(df['close'])

        # 计算涨跌停价
        df['high_limit'] = (df['preclose'] * 1.1).round(2)
        df['low_limit'] = (df['preclose'] * 0.9).round(2)

        # 列名转换: amount → money
        df = df.rename(columns={'amount': 'money'})

        # 按 stock_code 分组写 parquet
        stock_stats = {}
        grouped = df.groupby('stock_code')
        total = len(grouped)
        for i, (ts_code, group) in enumerate(grouped):
            simtrade_code = self.convert_code(ts_code)
            out_df = group[['date', 'open', 'high', 'low', 'close',
                            'preclose', 'volume', 'money', 'high_limit', 'low_limit']].copy()
            out_df['volume'] = out_df['volume'].astype('int64')
            out_df.to_parquet(stocks_dir / f"{simtrade_code}.parquet", index=False)
            stock_stats[simtrade_code] = {
                'start': str(group['date'].min().date()),
                'end': str(group['date'].max().date()),
                'count': len(group),
            }
            if (i + 1) % 500 == 0 or (i + 1) == total:
                print(f"  进度: {i+1}/{total}")

        print(f"  生成 {total} 只股票 parquet 文件")
        return {'total': total, 'stocks': stock_stats}

    def _generate_exrights_parquets(self, exrights_dir: Path) -> Dict:
        """生成 exrights/*.parquet 文件

        SimTradeLab 需要的列:
          - date: 除权日
          - dividend: 每股派息
          - exer_forward_a / exer_forward_b: 前复权因子
          - allotted_ps: 每股送股
          - bonus_ps: 每股红利
          - rationed_ps: 每股配股
          - rationed_px: 配股价
        """
        print("\n[2/4] 生成 exrights parquet...")

        con = duckdb.connect(self.duckdb_path, read_only=True)
        try:
            sql = """
                SELECT
                    d.ts_code,
                    d.ex_date as date,
                    d.cash_div,
                    d.stk_div,
                    d.stk_bo_rate,
                    d.stk_co_rate,
                    s.close as preclose
                FROM dividend_data d
                LEFT JOIN stock_daily s
                    ON d.ts_code = s.stock_code AND d.ex_date = s.date
                WHERE d.ex_date IS NOT NULL
                    AND (d.cash_div > 0 OR d.stk_bo_rate > 0 OR d.stk_co_rate > 0)
                ORDER BY d.ts_code, d.ex_date
            """
            df = con.execute(sql).fetchdf()
        finally:
            con.close()

        if df.empty:
            print("  无除权除息数据，跳过")
            return {'total': 0}

        # 填充空值
        df['preclose'] = df['preclose'].fillna(0)
        for col in ['cash_div', 'stk_div', 'stk_bo_rate', 'stk_co_rate']:
            df[col] = df[col].fillna(0)

        # 每股送股 (Tushare stk_bo_rate 是每10股送X股)
        df['allotted_ps'] = df['stk_bo_rate'] / 10.0
        # 每股红利
        df['bonus_ps'] = df['cash_div']
        # 每股配股 (Tushare stk_co_rate 是每10股配X股)
        df['rationed_ps'] = df['stk_co_rate'] / 10.0
        # 配股价（DuckDB无此数据，设为0）
        df['rationed_px'] = 0.0

        # 前复权因子: exer_forward_a = 1 - (cash_div - rationed_ps*rationed_px) / preclose
        # 简化版（无配股价数据）: exer_forward_a = 1 - cash_div / preclose
        df['exer_forward_a'] = np.where(
            df['preclose'] > 0,
            (1.0 - df['cash_div'] / df['preclose']).round(6),
            1.0
        )
        df['exer_forward_b'] = df['cash_div']

        # 派息（用于 SimTradeLab 的 dividend 缓存）
        df['dividend'] = df['cash_div']

        # 按 ts_code 分组写 parquet
        out_cols = ['date', 'dividend', 'exer_forward_a', 'exer_forward_b',
                     'allotted_ps', 'bonus_ps', 'rationed_ps', 'rationed_px']
        count = 0
        for ts_code, group in df.groupby('ts_code'):
            simtrade_code = self.convert_code(ts_code)
            out_df = group[out_cols].copy()
            out_df.to_parquet(exrights_dir / f"{simtrade_code}.parquet", index=False)
            count += 1

        print(f"  生成 {count} 只股票的 exrights 文件")
        return {'total': count}

    def _generate_metadata(self, metadata_dir: Path, start_date: str, end_date: str,
                           stock_codes: List[str] = None):
        """生成 metadata parquet 文件"""
        print("\n[3/4] 生成 metadata...")

        con = duckdb.connect(self.duckdb_path, read_only=True)
        try:
            # 交易日历
            trade_days = con.execute("""
                SELECT DISTINCT date FROM stock_daily
                WHERE period = '1d'
                ORDER BY date
            """).fetchdf()
            trade_days['date'] = trade_days['date'].dt.strftime('%Y-%m-%d')
            trade_days.to_parquet(metadata_dir / 'trade_days.parquet', index=False)
            print(f"  交易日: {len(trade_days)} 天")

            # 股票列表 - 只包含实际生成的股票
            if stock_codes:
                codes_str = ','.join(f"'{c}'" for c in stock_codes)
                stocks = con.execute(f"""
                    SELECT DISTINCT stock_code as symbol
                    FROM stock_daily
                    WHERE period = '1d' AND symbol_type = 'stock'
                      AND stock_code IN ({codes_str})
                    ORDER BY stock_code
                """).fetchdf()
            else:
                stocks = con.execute("""
                    SELECT DISTINCT stock_code as symbol
                    FROM stock_daily
                    WHERE period = '1d' AND symbol_type = 'stock'
                    ORDER BY stock_code
                """).fetchdf()
            stocks['symbol'] = stocks['symbol'].apply(self.convert_code)
            stocks['code'] = stocks['symbol'].str.split('.').str[0]
            stocks['market'] = stocks['symbol'].str.split('.').str[1]
            stocks.to_parquet(metadata_dir / 'stocks.parquet', index=False)
            print(f"  股票: {len(stocks)} 只")

            # benchmark (沪深300) - 尝试从 Tushare 下载
            self._download_benchmark(metadata_dir, start_date, end_date, con)

        finally:
            con.close()

    def _download_benchmark(self, metadata_dir: Path, start_date: str, end_date: str, con):
        """下载沪深300基准指数数据"""
        # 尝试用 Tushare 下载
        try:
            import tushare as ts
            # 动态添加项目根目录到 sys.path
            project_root = Path(__file__).resolve().parent.parent.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))
            from tushare_manager.tushare_config import TushareConfig
            config = TushareConfig()
            if not config.token:
                raise ValueError("无 Tushare token")
            ts.set_token(config.token)
            pro = ts.pro_api()

            start = (start_date or '20200101').replace('-', '')
            end = (end_date or datetime.now().strftime('%Y%m%d')).replace('-', '')
            df = pro.index_daily(ts_code='000300.SH', start_date=start, end_date=end)

            if df is not None and not df.empty:
                bench = df[['trade_date', 'open', 'high', 'low', 'close', 'vol']].copy()
                bench = bench.rename(columns={'trade_date': 'date', 'vol': 'volume'})
                bench['date'] = pd.to_datetime(bench['date'])
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    bench[col] = pd.to_numeric(bench[col], errors='coerce')
                bench = bench.sort_values('date')
                bench.to_parquet(metadata_dir / 'benchmark.parquet', index=False)
                print(f"  基准指数: {len(bench)} 天 (Tushare)")
                return
        except Exception as e:
            print(f"  Tushare 基准指数下载失败: {e}")

        # 备选：用 stock_daily 中的一只大盘股作为替代
        print("  使用替代基准（平安银行 000001.SZ 的 close 数据）...")
        try:
            bench = con.execute("""
                SELECT date, close FROM stock_daily
                WHERE stock_code = '000001.SZ' AND period = '1d'
                ORDER BY date
            """).fetchdf()
            bench = bench.rename(columns={'close': 'close'})
            bench['open'] = bench['close']
            bench['high'] = bench['close']
            bench['low'] = bench['close']
            bench['volume'] = 0
            bench.to_parquet(metadata_dir / 'benchmark.parquet', index=False)
            print(f"  替代基准: {len(bench)} 天")
        except Exception as e:
            print(f"  基准数据生成失败: {e}")

    def _generate_manifest(self, output_path: Path, stock_stats: Dict):
        """生成 manifest.json"""
        stocks_info = stock_stats.get('stocks', {})
        manifest = {
            'version': 2.0,
            'market': 'CN',
            'created': datetime.now().isoformat(),
            'data_source': 'duckdb',
            'duckdb_path': self.duckdb_path,
            'stocks': stocks_info,
            'total_stocks': stock_stats.get('total', 0),
        }
        with open(output_path / 'manifest.json', 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="DuckDB → SimTradeLab 数据桥")
    parser.add_argument('--output', '-o', default=None, help='输出目录')
    parser.add_argument('--duckdb-path', default='D:/StockData/stock_data.ddb', help='DuckDB 路径')
    parser.add_argument('--start', default=None, help='起始日期')
    parser.add_argument('--end', default=None, help='结束日期')
    parser.add_argument('--force', action='store_true', help='强制重新生成（忽略缓存）')
    args = parser.parse_args()

    output = args.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

    bridge = DuckDBToSimTradeBridge(duckdb_path=args.duckdb_path)
    result = bridge.generate_all(
        output_dir=output,
        start_date=args.start,
        end_date=args.end,
        skip_if_cached=not args.force,
    )

    if not result.get('cached'):
        print(f"\n数据已生成到: {output}")
        print("可以运行回测: python run_backtest.py strategies/你的策略.py --duckdb")


if __name__ == '__main__':
    main()
