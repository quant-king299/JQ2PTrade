"""
MiniPTrade 数据加载器 — 从 DuckDB 直接加载到内存
"""

import os
from collections import namedtuple

import duckdb
import numpy as np
import pandas as pd


DataBundle = namedtuple('DataBundle', [
    'trading_days',       # list[pd.Timestamp] 回测区间内的交易日
    'all_stock_codes',    # list[str] 全部股票代码 (.SS/.SZ)
])


class DataLoader:
    def __init__(self, duckdb_path: str = 'D:/StockData/stock_data.ddb'):
        self.duckdb_path = duckdb_path
        self._stock_data: dict[str, pd.DataFrame] = {}   # {code: df(date-indexed)}
        self._trading_days: list[pd.Timestamp] = []
        self._all_codes: list[str] = []
        self._loaded = False

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------
    def load(self, start_date: str | pd.Timestamp,
             end_date: str | pd.Timestamp) -> DataBundle:
        if not os.path.exists(self.duckdb_path):
            raise FileNotFoundError(f"DuckDB 不存在: {self.duckdb_path}")

        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        # 向前延伸 400 天，为 get_history(count=120) 提供历史缓冲
        extended_start = start - pd.Timedelta(days=400)

        print(f"从 DuckDB 加载数据 ({extended_start.date()} ~ {end.date()})...")
        con = duckdb.connect(self.duckdb_path, read_only=True)
        try:
            sql = f"""
                SELECT stock_code, symbol_type, date, open, high, low, close,
                       volume, amount,
                       LAG(close) OVER (PARTITION BY stock_code ORDER BY date) as preclose
                FROM stock_daily
                WHERE period = '1d' AND symbol_type IN ('stock', 'index')
                  AND date >= '{extended_start.strftime('%Y-%m-%d')}'
                  AND date <= '{end.strftime('%Y-%m-%d')}'
                ORDER BY stock_code, date
            """
            df = con.execute(sql).fetchdf()
        finally:
            con.close()

        if df.empty:
            raise ValueError("DuckDB 中无日线数据")

        stock_count = (df['symbol_type'] == 'stock').sum()
        index_count = (df['symbol_type'] == 'index').sum()
        print(f"  原始记录: {len(df)} 条 "
              f"(股票记录 {stock_count}, 指数记录 {index_count})")
        print(f"  代码分布: {df['stock_code'].nunique()} 只 "
              f"({df[df['symbol_type']=='stock']['stock_code'].nunique()} 股票 / "
              f"{df[df['symbol_type']=='index']['stock_code'].nunique()} 指数)")

        # preclose: 首行用 close 填充
        df['preclose'] = df['preclose'].fillna(df['close'])
        # 涨跌停价（指数不受涨跌停限制，用 ±∞ 表示）
        is_stock = df['symbol_type'] == 'stock'
        df['high_limit'] = np.where(is_stock,
                                     (df['preclose'] * 1.1).round(2),
                                     np.inf)
        df['low_limit'] = np.where(is_stock,
                                    (df['preclose'] * 0.9).round(2),
                                    -np.inf)
        # .SH → .SS (PTrade 格式)
        df['stock_code'] = df['stock_code'].str.replace('.SH', '.SS', regex=False)

        # 按 stock_code 预分组，存入 dict
        data_cols = ['symbol_type', 'open', 'high', 'low', 'close', 'volume',
                     'amount', 'preclose', 'high_limit', 'low_limit']
        for code, group in df.groupby('stock_code'):
            sub = group.set_index('date').sort_index()
            self._stock_data[code] = sub[[c for c in data_cols if c in sub.columns]]

        self._all_codes = sorted(
            code for code, df in self._stock_data.items()
            if not df.empty and (df.get('symbol_type', 'stock').iloc[0] == 'stock'
                                  if 'symbol_type' in df.columns else True)
        )
        # 指数代码清单（供策略/调试用）
        self._index_codes = sorted(
            code for code, df in self._stock_data.items()
            if not df.empty and 'symbol_type' in df.columns
            and df['symbol_type'].iloc[0] == 'index'
        )

        # 交易日: 从全部数据中取 [start, end] 范围内的去重日期
        all_dates = sorted(df['date'].unique())
        self._trading_days = [d for d in all_dates if start <= d <= end]

        self._loaded = True
        print(f"  可用股票: {len(self._all_codes)} 只")
        print(f"  回测交易日: {len(self._trading_days)} 天")

        return DataBundle(
            trading_days=self._trading_days,
            all_stock_codes=list(self._all_codes),
        )

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def get_stock_history(self, code: str, date_range: list[pd.Timestamp],
                          fields: list[str]) -> pd.DataFrame:
        """返回 DataFrame, 日期索引, 含指定列"""
        sdf = self._stock_data.get(code)
        if sdf is None:
            return pd.DataFrame(columns=fields)
        mask = sdf.index.isin(date_range)
        result = sdf.loc[mask, [c for c in fields if c in sdf.columns]].copy()
        return result

    def get_price(self, code: str, dt: pd.Timestamp) -> float:
        sdf = self._stock_data.get(code)
        if sdf is None:
            return 0.0
        ts = pd.Timestamp(dt)
        if ts not in sdf.index:
            return 0.0
        return float(sdf.loc[ts, 'close'])

    def get_bar(self, code: str, dt) -> dict:
        sdf = self._stock_data.get(code)
        ts = pd.Timestamp(dt)
        if sdf is None or ts not in sdf.index:
            return {'close': 0.0, 'high_limit': 999999.0,
                    'low_limit': 0.0, 'volume': 0, 'open': 0.0}
        row = sdf.loc[ts]
        # 跳过非数值字段（如 symbol_type），只返回行情字段
        result = {}
        for col in row.index:
            if col == 'symbol_type':
                continue
            val = row[col]
            try:
                result[col] = int(val) if col == 'volume' else float(val)
            except (TypeError, ValueError):
                result[col] = val
        return result

    @property
    def index_codes(self) -> list[str]:
        return self._index_codes

    @property
    def all_codes(self) -> list[str]:
        return self._all_codes

    @property
    def stock_data(self) -> dict[str, pd.DataFrame]:
        return self._stock_data
