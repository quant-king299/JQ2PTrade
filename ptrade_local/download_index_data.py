"""
下载指数日线数据 → 写入 DuckDB（symbol_type='index'）

用途：
    MiniPTrade 引擎已扩展为同时加载 stock+index 数据，
    本脚本把沪深300/上证指数/深证成指等指数日线写入 stock_daily 表，
    使策略的"大盘趋势判断"、"空仓机制"在 MiniPTrade 上能正常工作。

数据源：Tushare pro.index_daily 接口
入库：D:/StockData/stock_data.ddb（默认路径，可参数覆盖）

用法：
    # 已设置 TUSHARE_TOKEN 环境变量
    python download_index_data.py

    # 直接传 token
    python download_index_data.py --token YOUR_TUSHARE_TOKEN

    # 指定起止日期 + 多个指数
    python download_index_data.py --start 20180101 --end 20241231 \\
        --indices 000300.SH,000001.SH,399001.SZ

权限要求：
    - Tushare 积分 ≥ 120（基础指数日线接口）
"""
import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

try:
    import tushare as ts
except ImportError:
    print("请先安装 tushare: pip install tushare")
    sys.exit(1)


# 默认下载的指数（覆盖策略常用的"大盘趋势判断"）
DEFAULT_INDICES = [
    '000300.SH',   # 沪深300
    '000001.SH',   # 上证指数
    '399001.SZ',   # 深证成指
    '399006.SZ',   # 创业板指
    '000905.SH',   # 中证500
    '000852.SH',   # 中证1000
]


def parse_args():
    p = argparse.ArgumentParser(description="下载指数日线 → DuckDB")
    p.add_argument('--token', default=os.environ.get('TUSHARE_TOKEN', ''),
                   help='Tushare token（默认读 TUSHARE_TOKEN 环境变量）')
    p.add_argument('--duckdb-path',
                   default='D:/StockData/stock_data.ddb',
                   help='DuckDB 文件路径')
    p.add_argument('--start', default='20180101',
                   help='起始日期 YYYYMMDD')
    p.add_argument('--end',
                   default=pd.Timestamp.now().strftime('%Y%m%d'),
                   help='结束日期 YYYYMMDD')
    p.add_argument('--indices',
                   default=','.join(DEFAULT_INDICES),
                   help='逗号分隔的指数代码（tushare 格式，如 000300.SH）')
    p.add_argument('--replace', action='store_true',
                   help='覆盖写入（先删后插）；默认追加')
    return p.parse_args()


def download_one_index(pro, ts_code: str, start: str, end: str) -> pd.DataFrame:
    """下载单个指数日线（自动按年切分，避免单次返回超限）"""
    rows = []
    # 按年切分
    cur = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    while cur <= end_ts:
        y_start = cur.strftime('%Y%m%d')
        next_year = cur.replace(year=cur.year + 1, month=1, day=1)
        y_end = min(next_year - pd.Timedelta(days=1), end_ts).strftime('%Y%m%d')
        try:
            df = pro.index_daily(ts_code=ts_code,
                                 start_date=y_start, end_date=y_end)
            if df is not None and not df.empty:
                rows.append(df)
                print(f"  {ts_code} {y_start[:4]}: {len(df)} 条")
            time.sleep(0.3)
        except Exception as e:
            print(f"  {ts_code} {y_start[:4]} 失败: {e}")
        cur = next_year

    if not rows:
        return pd.DataFrame()
    df_all = pd.concat(rows, ignore_index=True)
    return df_all


def to_duckdb_format(df: pd.DataFrame, ts_code: str) -> pd.DataFrame:
    """tushare df → stock_daily 表结构"""
    if df.empty:
        return df
    out = pd.DataFrame({
        'stock_code': ts_code,                 # 直接用 .SH/.SZ 格式入库
        'symbol_type': 'index',
        'date': pd.to_datetime(df['trade_date']).dt.date,
        'period': '1d',
        'open': df['open'].astype(float),
        'high': df['high'].astype(float),
        'low': df['low'].astype(float),
        'close': df['close'].astype(float),
        'volume': df['vol'].astype('int64'),
        'amount': df['amount'].astype(float),
    })
    return out


def write_to_duckdb(df: pd.DataFrame, duckdb_path: str, replace: bool):
    """写入 DuckDB（INSERT OR REPLACE）"""
    import duckdb
    if df.empty:
        print("  空数据，跳过写入")
        return

    con = duckdb.connect(duckdb_path)
    try:
        if replace:
            # 删除该 code 的旧指数记录
            codes = tuple(df['stock_code'].unique())
            if len(codes) == 1:
                con.execute(
                    "DELETE FROM stock_daily WHERE stock_code = ? "
                    "AND symbol_type = 'index'",
                    [codes[0]]
                )
            else:
                placeholders = ','.join(['?'] * len(codes))
                con.execute(
                    f"DELETE FROM stock_daily WHERE stock_code IN ({placeholders}) "
                    "AND symbol_type = 'index'",
                    list(codes)
                )
            print(f"  已删除旧指数记录: {codes}")

        # 注册临时视图 + INSERT
        con.register('_idx_df', df)
        con.execute("""
            INSERT INTO stock_daily
                (stock_code, symbol_type, date, period,
                 open, high, low, close, volume, amount,
                 created_at, updated_at)
            SELECT stock_code, symbol_type, date, period,
                   open, high, low, close, volume, amount,
                   CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM _idx_df
        """)
        con.unregister('_idx_df')
        print(f"  写入 {len(df)} 条")
    finally:
        con.close()


def main():
    args = parse_args()
    if not args.token:
        print("错误：未提供 Tushare token。请用 --token 参数或设置 TUSHARE_TOKEN 环境变量。")
        sys.exit(1)

    if not Path(args.duckdb_path).exists():
        print(f"错误：DuckDB 文件不存在: {args.duckdb_path}")
        sys.exit(1)

    ts.set_token(args.token)
    pro = ts.pro_api()

    indices = [x.strip() for x in args.indices.split(',') if x.strip()]
    print(f"=" * 60)
    print(f"开始下载指数日线数据")
    print(f"  DuckDB:  {args.duckdb_path}")
    print(f"  区间:    {args.start} ~ {args.end}")
    print(f"  指数:    {indices}")
    print(f"  模式:    {'覆盖' if args.replace else '追加'}")
    print(f"=" * 60)

    total_written = 0
    for ts_code in indices:
        print(f"\n[{ts_code}] 下载中...")
        try:
            df_raw = download_one_index(pro, ts_code, args.start, args.end)
            if df_raw.empty:
                print(f"  {ts_code} 无数据")
                continue
            df_db = to_duckdb_format(df_raw, ts_code)
            write_to_duckdb(df_db, args.duckdb_path, args.replace)
            total_written += len(df_db)
        except Exception as e:
            print(f"  {ts_code} 失败: {e}")

    print(f"\n" + "=" * 60)
    print(f"完成！共写入 {total_written} 条指数日线")
    print(f"=" * 60)


if __name__ == '__main__':
    main()
