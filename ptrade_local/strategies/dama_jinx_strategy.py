"""
菜场大妈选股策略行业冥灯版V2 - PTrade本地回测版
原策略来源: 公众号【量化君也】(QMT版本)
转换说明: QMT API → PTrade API (SimTradeLab)

核心逻辑:
1. 每周第N个交易日调仓
2. 选股: 高股息(前25%) + PEG范围 + 价格范围 + 最小市值
3. 行业冥灯: 最热行业在冥灯列表中则空仓
4. 1/4月清仓

数据要求:
  先运行 python export_duckdb_data.py 从DuckDB导出数据
  数据保存在 data/tushare_strategy/ 目录下
"""
import os
import traceback
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# ============ 参数设置 ============
SELECT_NUM = 10          # 选股数目
PRICE_DN = 2.0           # 价格下限
PRICE_UP = 9.0           # 价格上限
PEG_DN = -3.0            # PEG下限
PEG_UP = 3.0             # PEG上限
WEEK_DAY = 1             # 每周第几个交易日交易
MA_LEN = 20              # 均线长度
FILTER_MONTH_LIST = [1, 4]  # 清仓月份
# 行业冥灯列表: 银行, 有色金属, 钢铁, 煤炭
JINX_INDU_LIST = ['801780.SI', '801050.SI', '801040.SI', '801950.SI']

# ============ 数据目录(硬编码绝对路径，因为run_backtest会拷贝到临时目录) ============
_PTRADE_LOCAL_DIR = r'C:\Users\wukun\Desktop\miniqmt扩展\code_converter\ptrade_local'
_STRATEGY_DATA_DIR = os.path.join(_PTRADE_LOCAL_DIR, 'data', 'tushare_strategy')
_LOCAL_DATA_DIR = os.path.join(_PTRADE_LOCAL_DIR, 'data')

# ============ 代码格式转换 ============
def ts_to_ptrade(code):
    """tushare代码 → PTrade代码: 600000.SH → 600000.SS"""
    if code is None:
        return code
    return code.replace('.SH', '.SS')

def ptrade_to_ts(code):
    """PTrade代码 → tushare代码: 600000.SS → 600000.SH"""
    if code is None:
        return code
    return code.replace('.SS', '.SH')


# ============ 本地数据加载 ============
def _load_local_parquet(filename):
    path = os.path.join(_STRATEGY_DATA_DIR, filename)
    if os.path.exists(path):
        return pd.read_parquet(path)
    return None


def get_local_daily_basic(dt):
    """从本地缓存获取daily_basic（按trade_date过滤）"""
    if g._daily_basic_cache is not None:
        df = g._daily_basic_cache
        return df[df['trade_date'] == dt] if 'trade_date' in df.columns else df
    return None


def get_local_stk_limit(dt):
    """从本地缓存获取涨跌停数据"""
    if g._stk_limit_cache is not None:
        df = g._stk_limit_cache
        return df[df['trade_date'] == dt] if 'trade_date' in df.columns else df
    return None


# ============ 交易日历 ============
def get_week_nth_trading_date_local(start_date, end_date, n=1):
    """从本地交易日历计算每周第n个交易日"""
    # 尝试从SimTradeLab的manifest/trade_days获取
    td_path = os.path.join(_LOCAL_DATA_DIR, 'metadata', 'trade_days.parquet')
    if os.path.exists(td_path):
        td_df = pd.read_parquet(td_path)
        dates = td_df['date'].astype(str).str.replace('-', '').tolist()
    else:
        # 从stock数据推断交易日
        sample_path = os.path.join(_LOCAL_DATA_DIR, 'stocks', '000001.SZ.parquet')
        if os.path.exists(sample_path):
            sdf = pd.read_parquet(sample_path)
            dates = pd.to_datetime(sdf['date']).dt.strftime('%Y%m%d').tolist()
        else:
            print('[ERROR] 无法获取交易日历')
            return []

    # 过滤日期范围
    dates = [d for d in dates if start_date <= d <= end_date]
    if not dates:
        return []

    date_df = pd.DataFrame({'date_str': dates})
    date_df['date_obj'] = pd.to_datetime(date_df['date_str'], format='%Y%m%d')
    date_df['week_start'] = date_df['date_obj'].apply(lambda x: x - pd.Timedelta(days=x.weekday()))

    result = []
    for _, group in date_df.groupby('week_start'):
        group = group.sort_values('date_obj')
        if len(group) >= n:
            result.append(group.iloc[n - 1]['date_str'])
        else:
            result.append(group.iloc[-1]['date_str'])
    return result


def get_previous_trading_date_local(date_str, n=1):
    """获取往前第n个交易日"""
    td_path = os.path.join(_LOCAL_DATA_DIR, 'metadata', 'trade_days.parquet')
    if os.path.exists(td_path):
        td_df = pd.read_parquet(td_path)
        dates = td_df['date'].astype(str).str.replace('-', '').tolist()
    else:
        sample_path = os.path.join(_LOCAL_DATA_DIR, 'stocks', '000001.SZ.parquet')
        if os.path.exists(sample_path):
            sdf = pd.read_parquet(sample_path)
            dates = pd.to_datetime(sdf['date']).dt.strftime('%Y%m%d').tolist()
        else:
            return date_str

    if date_str in dates:
        idx = dates.index(date_str)
        if idx >= n:
            return dates[idx - n]
    # date_str不在列表中
    earlier = [d for d in dates if d < date_str]
    if len(earlier) >= n:
        return earlier[-n]
    return dates[0] if dates else date_str


# ============ 均线/行业宽度计算 ============
def calc_all_stock_above_ma(start_date, end_date, ma_len=20):
    """从本地parquet文件读取所有股票收盘价，计算MA"""
    stocks_dir = os.path.join(_LOCAL_DATA_DIR, 'stocks')
    if not os.path.exists(stocks_dir):
        print(f'[ERROR] 股票数据目录不存在: {stocks_dir}')
        return pd.DataFrame()

    print(f'[INFO] 正在加载全部股票数据并计算MA{ma_len}，请稍候...')
    stime = datetime.now()

    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)

    all_closes = {}
    files = [f for f in os.listdir(stocks_dir) if f.endswith('.parquet')]
    for i, f in enumerate(files):
        code = f.replace('.parquet', '')
        try:
            df = pd.read_parquet(os.path.join(stocks_dir, f))
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            df = df[(df.index >= start_dt) & (df.index <= end_dt)]
            if len(df) > ma_len:
                all_closes[code] = df['close']
        except Exception:
            continue

    if not all_closes:
        return pd.DataFrame()

    price_df = pd.DataFrame(all_closes)
    price_df = price_df.sort_index().ffill()
    ma_df = price_df.rolling(ma_len).mean()
    above_df = price_df > ma_df

    etime = datetime.now()
    print(f'[INFO] MA计算完成，{len(all_closes)}只股票，耗时{(etime - stime).seconds}秒')
    return above_df


# ============ 策略主体 ============
def initialize(context):
    """策略初始化"""
    set_benchmark('000300.SS')

    # 全局状态
    g.select_list = []
    g.trading_date_list = None
    g.sw_code_name = None
    g.sw_cons_df = None
    g.above_df = None
    g.last_trade_date = None

    # ---- 加载本地基本面数据 ----
    g._daily_basic_cache = _load_local_parquet('daily_basic.parquet')
    g._stock_basic = _load_local_parquet('stock_basic_lite.parquet')
    g._stk_limit_cache = _load_local_parquet('stk_limit.parquet')
    g._dv_ttm = _load_local_parquet('dv_ttm.parquet')

    if g._daily_basic_cache is not None:
        print(f'[INIT] 加载daily_basic: {len(g._daily_basic_cache)}条')
    else:
        print('[WARN] 未找到daily_basic数据，请先运行 export_duckdb_data.py')

    if g._stock_basic is not None:
        print(f'[INIT] 加载stock_basic: {len(g._stock_basic)}只股票')
    else:
        print('[WARN] 未找到stock_basic数据')

    if g._stk_limit_cache is not None:
        print(f'[INIT] 加载stk_limit: {len(g._stk_limit_cache)}条')

    if g._dv_ttm is not None:
        print(f'[INIT] 加载dv_ttm: {len(g._dv_ttm)}只股票')

    # ---- 加载申万行业数据 ----
    sw_list_df = _load_local_parquet('sw_industry_list.parquet')
    sw_cons_df = _load_local_parquet('sw_constituents.parquet')
    # 回退到旧路径
    if sw_cons_df is None:
        sw_cons_df = _load_local_parquet('stock_industry.parquet')

    if sw_list_df is not None:
        code_col = 'industry_code' if 'industry_code' in sw_list_df.columns else 'index_code'
        name_col = 'industry_name' if 'industry_name' in sw_list_df.columns else 'industry_name'
        g.sw_code_name = dict(zip(sw_list_df[code_col], sw_list_df[name_col]))
        print(f'[INIT] 加载申万行业: {len(g.sw_code_name)}个')

    if sw_cons_df is not None:
        # 统一列名
        rename_map = {}
        if 'con_code' in sw_cons_df.columns:
            rename_map['con_code'] = 'code'
        elif 'ts_code' in sw_cons_df.columns:
            rename_map['ts_code'] = 'code'
        if 'index_code' in sw_cons_df.columns:
            rename_map['index_code'] = 'sw_code'
        elif 'l1_code' in sw_cons_df.columns:
            rename_map['l1_code'] = 'sw_code'
        sw_cons_df = sw_cons_df.rename(columns=rename_map)

        for col in ['in_date', 'out_date']:
            if col not in sw_cons_df.columns:
                sw_cons_df[col] = ''
        g.sw_cons_df = sw_cons_df[['code', 'sw_code', 'in_date', 'out_date']].copy()
        print(f'[INIT] 加载行业成分股: {len(g.sw_cons_df)}条')

    # ---- 计算均线数据 ----
    if g.sw_cons_df is not None and len(g.sw_cons_df) > 0:
        bt_start = context.current_dt.strftime('%Y%m%d')
        true_start = get_previous_trading_date_local(bt_start, n=MA_LEN + 5)
        end_date = (context.current_dt + timedelta(days=365)).strftime('%Y%m%d')
        g.above_df = calc_all_stock_above_ma(true_start, end_date, MA_LEN)

    # ---- 计算交易日列表 ----
    start = (context.current_dt - timedelta(days=30)).strftime('%Y%m%d')
    end = (context.current_dt + timedelta(days=365)).strftime('%Y%m%d')
    g.trading_date_list = get_week_nth_trading_date_local(start, end, WEEK_DAY)
    print(f'[INIT] 调仓交易日: {len(g.trading_date_list)}天')

    print('[INIT] 策略初始化完成')


def before_trading_start(context, data):
    pass


def handle_data(context, data):
    try:
        _handle_data_impl(context, data)
    except Exception:
        print(f'[策略异常] {traceback.format_exc()}')


def _handle_data_impl(context, data):
    today = context.current_dt.strftime('%Y%m%d')

    if g.last_trade_date == today:
        return

    # 清仓月份
    month = context.current_dt.month
    if month in FILTER_MONTH_LIST:
        print(f'[{today}] 在清仓月份({month}月)中，执行清仓')
        sell_all_positions(context)
        g.last_trade_date = today
        return

    # 检查调仓日
    if g.trading_date_list is None or today not in g.trading_date_list:
        return

    print(f'[{today}] 当周第{WEEK_DAY}个交易日，执行选股策略')

    # 获取昨日日期
    yestoday = get_yesterday_str(context)

    # 行业冥灯过滤
    indu_filter_flag = False
    indu_code = None
    indu_width = 0
    if JINX_INDU_LIST and g.sw_cons_df is not None and g.above_df is not None:
        try:
            indu_width_df = calc_industry_width_by_date(yestoday)
            if indu_width_df is not None and len(indu_width_df) > 0:
                indu_code = indu_width_df['width'].idxmax()
                indu_width = indu_width_df['width'].max()
                indu_filter_flag = indu_code in JINX_INDU_LIST
        except Exception as e:
            print(f'[WARN] 行业宽度计算失败: {e}')

    if indu_filter_flag:
        indu_name = g.sw_code_name.get(indu_code, indu_code) if g.sw_code_name else indu_code
        print(f'[{today}] 行业冥灯[{indu_name}({indu_code})]，行业宽度{indu_width:.2f}，空仓')
        g.select_list = []
    else:
        if indu_code and g.sw_code_name:
            indu_name = g.sw_code_name.get(indu_code, indu_code)
            print(f'[{today}] [{indu_name}({indu_code})] 行业宽度最高: {indu_width:.2f}')
        select_stocks(context, today, yestoday)

    execute_trade(context)
    g.last_trade_date = today


def get_yesterday_str(context):
    """获取前一个交易日的日期字符串(YYYYMMDD)"""
    today_str = context.current_dt.strftime('%Y%m%d')
    return get_previous_trading_date_local(today_str, 1)


def select_stocks(context, today, yestoday):
    """选股逻辑 - 从本地DuckDB导出数据读取"""
    # 获取daily_basic（市值、PE、收盘价）
    daily_df = get_local_daily_basic(yestoday)
    if daily_df is None or len(daily_df) == 0:
        print(f'[{today}] yestoday={yestoday} 无daily_basic, 尝试today={today}')
        daily_df = get_local_daily_basic(today)
    if daily_df is None or len(daily_df) == 0:
        prev_dt = get_previous_trading_date_local(yestoday, 1)
        daily_df = get_local_daily_basic(prev_dt)

    # 获取涨跌停数据
    limit_df = get_local_stk_limit(today)
    if limit_df is None or len(limit_df) == 0:
        prev_dt = get_previous_trading_date_local(today, 1)
        limit_df = get_local_stk_limit(prev_dt)

    if daily_df is None or len(daily_df) == 0:
        print(f'[{today}] 无daily_basic数据，跳过选股')
        g.select_list = []
        return

    # 从stock_basic获取name和list_date
    if g._stock_basic is not None:
        basic = g._stock_basic[['ts_code', 'name', 'list_date']].copy()
        df = pd.merge(daily_df, basic, on='ts_code', how='left')
    else:
        df = daily_df.copy()
        df['name'] = ''
        df['list_date'] = '19900101'

    # 合并dv_ttm
    if g._dv_ttm is not None:
        df = pd.merge(df, g._dv_ttm, on='ts_code', how='left')

    # 合并涨跌停
    if limit_df is not None and len(limit_df) > 0:
        df = pd.merge(df, limit_df[['ts_code', 'up_limit', 'down_limit']], on='ts_code', how='left')

    # 过滤异常list_date
    df = df[df['list_date'].astype(str) >= '19900101']

    # 上市天数
    yestoday_obj = pd.to_datetime(yestoday, format='%Y%m%d')
    df['list_date'] = pd.to_datetime(df['list_date'], format='%Y%m%d')
    df['list_days'] = df['list_date'].apply(lambda x: (yestoday_obj - x).days + 1)

    # 价格列
    if 'close' in df.columns:
        df = df.rename(columns={'close': 'price'})

    # 没有涨跌停数据时近似计算
    if 'up_limit' not in df.columns and 'price' in df.columns:
        # 从SimTradeLab获取high_limit/low_limit
        df['up_limit'] = df['price'] * 1.1
        df['down_limit'] = df['price'] * 0.9

    if 'price' not in df.columns:
        g.select_list = []
        return

    # ---- 过滤 ----
    df = df[~df['ts_code'].str.startswith('688')]
    df = df[~df['ts_code'].str.endswith('BJ')]
    if 'name' in df.columns:
        df = df[~df['name'].str.contains(r'S|ST|\*|退', regex=True, na=False, case=False)]
    if 'up_limit' in df.columns:
        df = df[df['price'] < df['up_limit']]
        df = df[df['price'] > df['down_limit']]

    if df.empty:
        print(f'[{today}] 过滤后无股票')
        g.select_list = []
        return

    # ---- 选股 ----
    # 高股息(前25%) - 使用dv_ttm
    if 'dv_ttm' in df.columns:
        top_n = max(1, round(0.25 * len(df)))
        df = df.sort_values('dv_ttm', ascending=False).head(top_n)

    # PEG过滤 - 本地无profit_yoy数据，用pe范围近似替代
    if 'pe' in df.columns:
        df = df[df['pe'] > 0]  # 只取盈利的股票
        df = df[df['pe'] < 100]  # 排除PE异常高的

    # 价格范围
    if 'price' in df.columns:
        df = df[df['price'] < PRICE_UP]
        df = df[df['price'] > PRICE_DN]

    # 市值最小的N个
    if 'total_mv' in df.columns:
        select_df = df.sort_values('total_mv', ascending=True).head(SELECT_NUM)
    else:
        select_df = df.head(SELECT_NUM)

    g.select_list = [ts_to_ptrade(c) for c in select_df['ts_code'].tolist()]
    print(f'[{today}] 选中的股票: {g.select_list}')


def calc_industry_width_by_date(dt):
    """计算指定日期的行业宽度"""
    if g.sw_cons_df is None or g.above_df is None:
        return None

    # 查找日期
    dt_str = dt
    above_idx = None
    for idx in g.above_df.index:
        idx_str = idx.strftime('%Y%m%d') if hasattr(idx, 'strftime') else str(idx)[:10].replace('-', '')
        if idx_str == dt_str:
            above_idx = idx
            break
    if above_idx is None:
        return None

    above_srs = g.above_df.loc[above_idx]
    sw_cons_df = g.sw_cons_df.copy()

    # 筛选有效成分股
    sw_cons_df['in_date'] = sw_cons_df['in_date'].fillna('').astype(str)
    sw_cons_df['out_date'] = sw_cons_df['out_date'].fillna('').astype(str)
    mask = (sw_cons_df['in_date'] <= dt_str) & (
        (sw_cons_df['out_date'] > dt_str) | (sw_cons_df['out_date'] == '') | (sw_cons_df['out_date'] == 'nan')
    )
    df = sw_cons_df[mask].copy()
    df = df.sort_values(by=['code', 'in_date']).drop_duplicates(subset=['code'], keep='last')

    # 代码转换
    df['ptrade_code'] = df['code'].apply(ts_to_ptrade)

    # join above数据
    above_col = pd.DataFrame(above_srs).rename(columns={above_idx: 'above'})
    merged = pd.merge(df, above_col, left_on='ptrade_code', right_index=True, how='inner')

    indu_df = merged[['sw_code', 'above']].dropna(subset=['above'])
    if indu_df.empty:
        return None
    indu_width_df = 100 * indu_df.groupby('sw_code').sum() / indu_df.groupby('sw_code').count()
    indu_width_df = indu_width_df.fillna(0).rename(columns={'above': 'width'})
    return indu_width_df


def sell_all_positions(context):
    """清空所有持仓"""
    positions = get_positions()
    if not positions:
        return
    for stock, pos in positions.items():
        if pos.amount > 0:
            order_target(stock, 0)
            print(f'清仓卖出: {stock} {pos.amount}股')


def execute_trade(context):
    """执行交易"""
    positions = get_positions()
    hold_list = [s for s, p in positions.items() if p.amount > 0] if positions else []

    print(f'当前持仓: {hold_list}')
    print(f'目标持仓: {g.select_list}')

    if not hold_list and not g.select_list:
        return

    # 卖出
    sell_num = 0
    for code in hold_list:
        if code not in g.select_list:
            pos = positions.get(code)
            if pos and pos.amount > 0:
                sell_num += 1
                order_target(code, 0)
                print(f'卖出: {code} {pos.amount}股')
        else:
            print(f'继续持有: {code}')

    # 买入
    buy_list = [c for c in g.select_list if c not in hold_list]
    hold_num = len(hold_list) - sell_num
    buy_num = SELECT_NUM - hold_num

    if buy_num > 0 and buy_list:
        cash = context.portfolio.cash if hasattr(context, 'portfolio') and hasattr(context.portfolio, 'cash') else 100000
        cash = 0.999 * cash
        cash_per_code = cash / buy_num if buy_num > 0 else 0

        for code in buy_list[:buy_num]:
            if cash_per_code > 0:
                order_value(code, cash_per_code)
                print(f'买入: {code} 金额={cash_per_code:.0f}')
