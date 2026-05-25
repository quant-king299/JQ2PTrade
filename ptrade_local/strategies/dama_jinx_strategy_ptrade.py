"""
菜场大妈选股策略行业冥灯版V2 - PTrade平台版 (Tushare预加载版)
原策略来源: 公众号【量化君也】(QMT版本)

核心逻辑:
1. 每周第N个交易日调仓
2. 选股: 高股息(前25%) + PEG范围 + 价格范围 + 最小市值
3. 行业冥灯: 最热行业在冥灯列表中则空仓
4. 1/4月清仓

数据策略: initialize中预加载全部Tushare数据，handle_data只查缓存，避免频率超限
"""
import traceback
import time as ttime
import numpy as np
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta

# ============ 参数设置 ============
SELECT_NUM = 10
PRICE_DN = 2.0
PRICE_UP = 9.0
PEG_DN = -3.0
PEG_UP = 3.0
WEEK_DAY = 1
MA_LEN = 20
FILTER_MONTH_LIST = [1, 4]
JINX_INDU_LIST = ['801780.SI', '801050.SI', '801040.SI', '801950.SI']
TOKEN = '请填入自己Tushare的token'

# ============ 代码格式转换 ============
def ts_to_ptrade(code):
    if code is None:
        return code
    return code.replace('.SH', '.SS')

def ptrade_to_ts(code):
    if code is None:
        return code
    return code.replace('.SS', '.SH')


# ============ Tushare数据函数 ============
def _ts_get(func, retry_count=3, pause_seconds=1, **kwargs):
    """通用tushare查询，带重试"""
    for i in range(retry_count):
        try:
            df = func(**kwargs)
            if df is not None and len(df) > 0:
                return df
            return df
        except Exception as e:
            if i < retry_count - 1:
                ttime.sleep(pause_seconds)
            else:
                print(f'[TS] 失败: {e}')
    return None


def get_week_nth_trading_date(pro, start_date, end_date, n=1):
    """获取每周第n个交易日"""
    df = _ts_get(pro.trade_cal, exchange='SSE', start_date=start_date, end_date=end_date, is_open='1')
    if df is None or df.empty:
        return []

    date_df = df.rename(columns={'cal_date': 'date_str'})[['date_str']]
    date_df['date_obj'] = pd.to_datetime(date_df['date_str'], format='%Y%m%d')
    date_df = date_df.sort_values('date_str')
    date_df['week_start'] = date_df['date_obj'].apply(lambda x: x - pd.Timedelta(days=x.weekday()))

    result = []
    for _, group in date_df.groupby('week_start'):
        group = group.sort_values('date_obj')
        if len(group) >= n:
            result.append(group.iloc[n - 1]['date_str'])
        else:
            result.append(group.iloc[-1]['date_str'])
    return result


def get_all_trade_dates(pro, start_date, end_date):
    """获取交易日列表"""
    df = _ts_get(pro.trade_cal, exchange='SSE', start_date=start_date, end_date=end_date, is_open='1')
    if df is None or df.empty:
        return []
    return df.sort_values('cal_date')['cal_date'].tolist()


def get_sw_cons_info(pro):
    """获取申万一级行业全部成分股信息"""
    industry_df = _ts_get(pro.index_classify, level='L1', src='SW2021')
    if industry_df is None or industry_df.empty:
        return {}, None

    sw_code_name = industry_df.set_index('index_code')['industry_name'].to_dict()
    df_list = []
    for code in industry_df['index_code'].tolist():
        df = _ts_get(pro.index_member_all, l1_code=code,
                     fields='ts_code,l1_code,in_date,out_date')
        if df is not None and len(df) > 0:
            df_list.append(df)
        ttime.sleep(0.3)

    if not df_list:
        return sw_code_name, None

    all_cons = pd.concat(df_list, ignore_index=True)
    all_cons = all_cons.rename(columns={'ts_code': 'code', 'l1_code': 'sw_code'})
    return sw_code_name, all_cons


# ============ 均线/行业宽度计算 ============
def calc_industry_width(context, yestoday):
    """用PTrade的get_history计算行业宽度"""
    if g.sw_cons_df is None:
        return None

    all_stocks = get_Ashares(context.current_dt.strftime('%Y%m%d'))
    all_stocks = [s for s in all_stocks if not (
        s.startswith('688') or s.startswith('4') or
        s.startswith('8')
    )]

    # 过滤上市天数不足的股票(需要 >= 2*MA_LEN 才有足够数据计算MA)
    bak_source = g._bak_basic_cache.get(yestoday)
    if bak_source is not None:
        bak = bak_source.copy()
        bak = bak[bak['list_date'].astype(str) >= '19900101']
        bak['list_date'] = pd.to_datetime(bak['list_date'], format='%Y%m%d')
        yestoday_obj = pd.to_datetime(yestoday, format='%Y%m%d')
        bak['list_days'] = bak['list_date'].apply(lambda x: (yestoday_obj - x).days + 1)
        valid_ts = set(bak[bak['list_days'] >= 2 * MA_LEN]['ts_code'].tolist())
        all_stocks = [s for s in all_stocks if ptrade_to_ts(s) in valid_ts]

    if not all_stocks:
        return None

    BATCH_SIZE = 200
    close_dict = {}
    for i in range(0, len(all_stocks), BATCH_SIZE):
        batch = all_stocks[i:i + BATCH_SIZE]
        try:
            hist = get_history(MA_LEN + 2, '1d', 'close', security_list=batch)
        except Exception:
            continue
        if hist is None or (isinstance(hist, pd.DataFrame) and hist.empty):
            continue
        if isinstance(hist, pd.DataFrame) and 'code' in hist.columns:
            for stock in batch:
                stock_data = hist[hist['code'] == stock]
                if len(stock_data) >= MA_LEN:
                    closes = stock_data['close'].reset_index(drop=True)
                    ma = closes.rolling(MA_LEN).mean()
                    close_dict[stock] = closes.iloc[-1] > ma.iloc[-1]

    if not close_dict:
        return None

    above_ts = {ptrade_to_ts(k): v for k, v in close_dict.items()}

    sw_cons_df = g.sw_cons_df.copy()
    sw_cons_df['in_date'] = sw_cons_df['in_date'].fillna('').astype(str)
    sw_cons_df['out_date'] = sw_cons_df['out_date'].fillna('').astype(str)
    mask = (sw_cons_df['in_date'] <= yestoday) & (
        (sw_cons_df['out_date'] > yestoday) | (sw_cons_df['out_date'] == '') |
        (sw_cons_df['out_date'] == 'nan')
    )
    df = sw_cons_df[mask].copy()
    df = df.sort_values(by=['code', 'in_date']).drop_duplicates(subset=['code'], keep='last')

    above_srs = pd.Series(above_ts)
    df = pd.merge(df, pd.DataFrame(above_srs).rename(columns={0: 'above'}),
                  left_on='code', right_index=True, how='inner')

    indu_df = df[['sw_code', 'above']].dropna(subset=['above'])
    if indu_df.empty:
        return None
    indu_width_df = 100 * indu_df.groupby('sw_code').sum() / indu_df.groupby('sw_code').count()
    indu_width_df = indu_width_df.fillna(0).rename(columns={'above': 'width'})
    return indu_width_df


# ============ 策略主体 ============
def initialize(context):
    """策略初始化 - 预加载全部Tushare数据"""
    if TOKEN == '请填入自己Tushare的token':
        raise ValueError('请先填入Tushare的token!')

    ts.set_token(TOKEN)
    pro = ts.pro_api()

    set_benchmark('000300.SS')
    set_slippage(0)

    g.select_list = []
    g.trading_date_list = None
    g.last_trade_date = None

    # ---- 1. 加载申万行业数据 ----
    g.sw_code_name, g.sw_cons_df = get_sw_cons_info(pro)
    if g.sw_cons_df is not None:
        print(f'[INIT] 申万行业: {len(g.sw_cons_df)}条成分股, {len(g.sw_code_name)}个行业')
    else:
        print('[WARN] 未加载申万行业数据')

    # ---- 2. 计算交易日和调仓日 ----
    start = (context.current_dt - timedelta(days=60)).strftime('%Y%m%d')
    end = (context.current_dt + timedelta(days=400)).strftime('%Y%m%d')
    all_trade_dates = get_all_trade_dates(pro, start, end)
    g._all_trade_dates = all_trade_dates
    g.trading_date_list = get_week_nth_trading_date(pro, start, end, WEEK_DAY)
    print(f'[INIT] 调仓交易日: {len(g.trading_date_list)}天')

    # ---- 3. 预加载全部基本面数据 ----
    print(f'[INIT] 正在预加载{len(g.trading_date_list)}个调仓日的基本面数据...')
    g._bak_basic_cache = {}
    g._daily_basic_cache = {}
    g._stk_limit_cache = {}

    for rebalance_date in g.trading_date_list:
        # 找前一个交易日
        prev_date = _get_prev_from_list(rebalance_date)
        if prev_date is None:
            continue

        # bak_basic - 前一日(用于name/list_date/profit_yoy，每个调仓日各取一份)
        if prev_date not in g._bak_basic_cache:
            df = _ts_get(pro.bak_basic, trade_date=prev_date,
                         fields='ts_code,list_date,profit_yoy,name')
            if df is not None and len(df) > 0:
                g._bak_basic_cache[prev_date] = df
                ttime.sleep(0.5)  # bak_basic限制严格，间隔大一些

        # daily_basic - 前一日(用于选股因子)
        if prev_date not in g._daily_basic_cache:
            df = _ts_get(pro.daily_basic, trade_date=prev_date,
                         fields='ts_code,total_mv,dv_ttm,pe_ttm')
            if df is not None and len(df) > 0:
                g._daily_basic_cache[prev_date] = df
                ttime.sleep(0.2)

        # daily_basic - 当日(用于收盘价)
        if rebalance_date not in g._daily_basic_cache:
            df = _ts_get(pro.daily_basic, trade_date=rebalance_date,
                         fields='ts_code,close')
            if df is not None and len(df) > 0:
                g._daily_basic_cache[rebalance_date] = df
                ttime.sleep(0.2)

        # stk_limit - 当日
        if rebalance_date not in g._stk_limit_cache:
            df = _ts_get(pro.stk_limit, trade_date=rebalance_date,
                         fields='ts_code,up_limit,down_limit')
            if df is not None and len(df) > 0:
                g._stk_limit_cache[rebalance_date] = df
                ttime.sleep(0.2)

    print(f'[INIT] 缓存: bak_basic {len(g._bak_basic_cache)}天, daily_basic {len(g._daily_basic_cache)}天, stk_limit {len(g._stk_limit_cache)}天')
    print('[INIT] 策略初始化完成')


def _get_prev_from_list(date_str, n=1):
    """从缓存的交易日列表获取前n个交易日"""
    dates = g._all_trade_dates
    if not dates:
        return None
    if date_str in dates:
        idx = dates.index(date_str)
        if idx >= n:
            return dates[idx - n]
    earlier = [d for d in dates if d < date_str]
    if len(earlier) >= n:
        return earlier[-n]
    return dates[0] if dates else None


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

    yestoday = _get_prev_from_list(today, 1)

    # 行业冥灯过滤
    indu_filter_flag = False
    indu_code = None
    indu_width = 0
    if JINX_INDU_LIST and g.sw_cons_df is not None:
        try:
            indu_width_df = calc_industry_width(context, yestoday)
            if indu_width_df is not None and len(indu_width_df) > 0:
                indu_code = indu_width_df['width'].idxmax()
                indu_width = indu_width_df['width'].max()
                indu_filter_flag = indu_code in JINX_INDU_LIST
        except Exception as e:
            print(f'[WARN] 行业宽度计算失败: {e}')

    if indu_filter_flag:
        indu_name = g.sw_code_name.get(indu_code, indu_code)
        print(f'[{today}] 行业冥灯[{indu_name}({indu_code})]，行业宽度{indu_width:.2f}，空仓')
        g.select_list = []
    else:
        if indu_code:
            indu_name = g.sw_code_name.get(indu_code, indu_code)
            print(f'[{today}] [{indu_name}({indu_code})] 行业宽度最高: {indu_width:.2f}')
        select_stocks(today, yestoday)

    execute_trade(context)
    g.last_trade_date = today


def select_stocks(today, yestoday):
    """选股逻辑 - 从缓存读取数据，零tushare调用"""
    # 从缓存读取
    daily_df = g._daily_basic_cache.get(yestoday)
    limit_df = g._stk_limit_cache.get(today)
    price_df = g._daily_basic_cache.get(today)
    bak_df = g._bak_basic_cache.get(yestoday)

    if daily_df is None or len(daily_df) == 0:
        print(f'[{today}] 缓存无daily_basic(yestoday={yestoday})')
        g.select_list = []
        return

    # 合并数据
    if bak_df is not None and len(bak_df) > 0:
        factor_df = pd.merge(bak_df[['ts_code', 'list_date', 'profit_yoy', 'name']],
                             daily_df, on='ts_code', how='inner')
    else:
        daily_df_copy = daily_df.copy()
        daily_df_copy['name'] = ''
        daily_df_copy['list_date'] = '19900101'
        daily_df_copy['profit_yoy'] = np.nan
        factor_df = daily_df_copy

    # 计算PEG
    if 'pe_ttm' in factor_df.columns and 'profit_yoy' in factor_df.columns:
        factor_df['peg'] = factor_df.apply(
            lambda x: x.pe_ttm / x.profit_yoy if x.profit_yoy != 0 else np.nan, axis=1
        )
    else:
        factor_df['peg'] = np.nan

    # 计算上市天数
    factor_df = factor_df[factor_df['list_date'].astype(str) >= '19900101']
    yestoday_obj = pd.to_datetime(yestoday, format='%Y%m%d')
    factor_df['list_date'] = pd.to_datetime(factor_df['list_date'], format='%Y%m%d')
    factor_df['list_days'] = factor_df['list_date'].apply(lambda x: (yestoday_obj - x).days + 1)

    # 获取价格和涨跌停
    if limit_df is not None and len(limit_df) > 0 and price_df is not None and len(price_df) > 0:
        price_df_r = price_df.rename(columns={'close': 'price'})[['ts_code', 'price']]
        limit_price = pd.merge(limit_df, price_df_r, on='ts_code', how='inner')
        df = pd.merge(factor_df, limit_price, on='ts_code', how='inner')
    else:
        if 'pe_ttm' in factor_df.columns:
            factor_df = factor_df.drop(columns=['pe_ttm'], errors='ignore')
        if 'close' in factor_df.columns:
            factor_df = factor_df.rename(columns={'close': 'price'})
        else:
            factor_df['price'] = 0
        df = factor_df
        df['up_limit'] = df['price'] * 1.1
        df['down_limit'] = df['price'] * 0.9

    if df.empty:
        print(f'[{today}] 合并后无数据')
        g.select_list = []
        return

    # ---- 过滤 ----
    df = df[~df['ts_code'].str.startswith('688')]
    df = df[~df['ts_code'].str.endswith('BJ')]
    if 'name' in df.columns:
        df = df[~df['name'].str.contains(r'S|ST|\*|退', regex=True, na=False, case=False)]
    if 'up_limit' in df.columns and 'price' in df.columns:
        df = df[df['price'] < df['up_limit']]
        df = df[df['price'] > df['down_limit']]

    if df.empty:
        print(f'[{today}] 过滤后无股票')
        g.select_list = []
        return

    # ---- 选股 ----
    # 高股息(前25%)
    if 'dv_ttm' in df.columns:
        top_n = max(1, round(0.25 * len(df)))
        df = df.sort_values('dv_ttm', ascending=False).head(top_n)

    # PEG过滤
    if 'peg' in df.columns:
        df = df[df['peg'] < PEG_UP]
        df = df[df['peg'] > PEG_DN]

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
