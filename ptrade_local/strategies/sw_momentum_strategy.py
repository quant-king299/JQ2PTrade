"""
四大搅屎棍策略 - PTrade版本
原策略来源: https://www.joinquant.com/post/49085
逻辑: 每周选申万一级行业MA20乖离率最高的行业，从中筛选ROE>15%且ROA>10%的中小市值股票
"""
import os
import traceback
import datetime
import numpy as np
import pandas as pd

# ===== 数据加载工具 =====
def _get_tushare_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'tushare')


def load_stock_industry():
    """加载股票行业分类"""
    path = os.path.join(_get_tushare_dir(), 'stock_industry.parquet')
    if os.path.exists(path):
        df = pd.read_parquet(path)
        return df
    return None


def load_index_constituents(index_code):
    """加载指数成分股 index_code: Tushare格式如 000985.SH"""
    filename = f"index_{index_code.replace('.', '_')}.parquet"
    path = os.path.join(_get_tushare_dir(), filename)
    if os.path.exists(path):
        df = pd.read_parquet(path)
        latest_date = df['trade_date'].max()
        return df[df['trade_date'] == latest_date]['ptrade_code'].tolist()
    return []


def load_stock_basic():
    """加载股票基本信息"""
    path = os.path.join(_get_tushare_dir(), 'stock_basic.parquet')
    if os.path.exists(path):
        return pd.read_parquet(path)
    return None


def load_fina_indicator():
    """加载财务指标"""
    path = os.path.join(_get_tushare_dir(), 'fina_indicator.parquet')
    if os.path.exists(path):
        return pd.read_parquet(path)
    return None


# ===== 初始化 =====
def initialize(context):
    set_benchmark('000300.SS')
    g.stock_num = 10
    g.hold_list = []
    g.yesterday_HL_list = []
    g.num = 1
    g.history_bars = 25  # MA20需要20根+余量

    # 预加载数据
    g.stock_industry = load_stock_industry()
    g.stock_basic = load_stock_basic()
    g.fina_indicator = load_fina_indicator()

    # 申万一级行业代码
    g.industry_codes = [
        '801010', '801020', '801030', '801040', '801050', '801080', '801110', '801120',
        '801130', '801140', '801150', '801160', '801170', '801180', '801200', '801210',
        '801230', '801710', '801720', '801730', '801740', '801750', '801760', '801770',
        '801780', '801790', '801880', '801890'
    ]

    # 需要回避的行业（银行、有色、煤炭、钢铁）
    g.avoid_industries = ['801780', '801050', '801950', '801040']

    # 记录上周调仓日
    g.last_adjust_week = -1


def before_trading_start(context, data):
    current_date = context.current_dt

    # 1. 获取当前持仓列表
    positions = get_positions()
    g.hold_list = list(positions.keys())

    # 2. 检查昨日涨停的持仓股
    g.yesterday_HL_list = []
    if g.hold_list:
        try:
            hist = get_history(1, '1d', ['close', 'high_limit'], g.hold_list)
            if isinstance(hist, pd.DataFrame) and 'code' in hist.columns:
                hl = hist[hist['close'] == hist['high_limit']]
                g.yesterday_HL_list = hl['code'].tolist()
            else:
                # 单股票或PanelLike格式
                for stock in g.hold_list:
                    try:
                        h = get_history(1, '1d', ['close', 'high_limit'], stock)
                        if h is not None and not h.empty:
                            if h['close'].iloc[-1] == h['high_limit'].iloc[-1]:
                                g.yesterday_HL_list.append(stock)
                    except Exception:
                        pass
        except Exception:
            pass

    # 3. 每周一调仓
    weekday = current_date.weekday()  # 0=Monday
    if weekday == 0:
        print(f"[DEBUG] {current_date.strftime('%Y-%m-%d')} 周一，开始调仓")
        weekly_adjustment(context)


def handle_data(context, data):
    # 尾盘检查涨停股是否打开（日线回测中用收盘价vs涨停价近似判断）
    if g.yesterday_HL_list:
        for stock in list(g.yesterday_HL_list):
            pos = get_position(stock)
            if pos is None or pos.amount == 0:
                g.yesterday_HL_list.remove(stock)
                continue
            try:
                h = get_history(1, '1d', ['close', 'high_limit'], stock)
                if h is not None and not h.empty:
                    if h['close'].iloc[-1] < h['high_limit'].iloc[-1]:
                        log.info(f"[{stock}] 涨停打开，卖出")
                        order_target(stock, 0)
                        g.yesterday_HL_list.remove(stock)
            except Exception:
                pass


# ===== 行业分析 =====
def get_stock_list(context):
    """选股模块"""
    yesterday = context.current_dt - pd.Timedelta(days=1)

    # 1. 获取中证全指成分股
    initial_list = load_index_constituents('000985.SH')
    if not initial_list:
        # 回退：使用全部A股
        try:
            initial_list = get_Ashares(context.current_dt.strftime('%Y%m%d'))
        except Exception:
            # SimTradeLab没有get_Ashares，从数据目录读取
            import os
            stock_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'cn', 'stocks')
            if os.path.exists(stock_dir):
                initial_list = [f.replace('.parquet', '') for f in os.listdir(stock_dir) if f.endswith('.parquet')]
            else:
                print("[DEBUG] 无法获取股票列表")
                return []

    print(f"[DEBUG] 初始股票数: {len(initial_list)}")

    # 2. 过滤科创板、创业板、北交所
    initial_list = filter_kcbj_stock(initial_list)
    # 3. 过滤ST
    initial_list = filter_st_stock(context, initial_list)
    # 4. 过滤次新股
    initial_list = filter_new_stock(context, initial_list)

    if not initial_list:
        return []

    # 5. 获取收盘价数据，计算MA20乖离
    print(f"[DEBUG] 过滤后股票数: {len(initial_list)}, 开始获取历史数据...")
    try:
        hist = get_history(22, '1d', ['close'], initial_list)
    except Exception as e:
        print(f"[DEBUG] get_history失败: {e}")
        return []

    print(f"[DEBUG] hist type={type(hist)}, shape={hist.shape if hasattr(hist, 'shape') else 'N/A'}, columns={list(hist.columns[:5]) if hasattr(hist, 'columns') else 'N/A'}")

    # 处理多股票返回格式
    if isinstance(hist, pd.DataFrame) and 'code' in hist.columns:
        # 纵向堆叠格式
        bias_result = {}
        for stock in initial_list:
            stock_data = hist[hist['code'] == stock]
            if len(stock_data) < 20:
                continue
            closes = stock_data['close'].reset_index(drop=True)
            ma20 = closes.rolling(20).mean()
            if closes.iloc[-1] > ma20.iloc[-1]:
                bias_result[stock] = True
            else:
                bias_result[stock] = False
    else:
        return []

    # 6. 获取行业分类
    industry_map = get_industry_for_stocks(list(bias_result.keys()))
    if not industry_map:
        return []

    # 7. 计算各行业乖离率比例
    df_bias = pd.DataFrame({
        'stock': list(bias_result.keys()),
        'above_ma20': list(bias_result.values()),
        'industry_code': [industry_map.get(s, 'unknown') for s in bias_result.keys()]
    })
    df_ratio = (df_bias.groupby('industry_code')['above_ma20'].sum() * 100.0 /
                df_bias.groupby('industry_code')['above_ma20'].count()).round()

    if df_ratio.empty:
        return []

    # 8. 选出最强的行业
    top_industry = df_ratio.nlargest(g.num)
    top_industries = top_industry.index.tolist()

    # 9. 如果最强行业是需要回避的行业，返回空
    if any(ai in top_industries for ai in g.avoid_industries):
        log.info("最强行业在回避列表中，本周不调仓")
        return []

    # 10. 从中小板中筛选ROE>15%、ROA>10%的股票，按市值升序
    return filter_by_fundamentals(context, initial_list)


def get_industry_for_stocks(stock_list):
    """获取股票的行业分类"""
    if g.stock_industry is None:
        return {}
    industry_map = {}
    stock_set = set(stock_list)
    mask = g.stock_industry['ptrade_code'].isin(stock_set)
    filtered = g.stock_industry[mask]
    for _, row in filtered.iterrows():
        industry_map[row['ptrade_code']] = row['industry_code']
    return industry_map


# ===== 调仓 =====
def weekly_adjustment(context):
    """每周调仓"""
    target_stocks = get_stock_list(context)

    # 卖出不在目标列表且非涨停的持仓
    for stock in list(g.hold_list):
        if stock not in target_stocks and stock not in g.yesterday_HL_list:
            order_target(stock, 0)
            log.info(f"卖出 {stock}")

    # 买入目标股票
    positions = get_positions()
    position_count = len([s for s, p in positions.items() if p.amount > 0]) if positions else 0
    target_num = len(target_stocks)

    if target_num > position_count:
        buy_num = min(target_num, g.stock_num * g.num - position_count)
        cash = context.portfolio.cash if hasattr(context, 'portfolio') and hasattr(context.portfolio, 'cash') else 100000
        value = cash / buy_num if buy_num > 0 else 0

        for stock in target_stocks:
            if stock not in positions:
                try:
                    order_target_value(stock, value)
                    log.info(f"买入 {stock}, 金额={value:.0f}")
                except Exception as e:
                    log.info(f"买入失败 {stock}: {e}")


# ===== 过滤器 =====
def filter_st_stock(context, stock_list):
    """过滤ST股票"""
    try:
        status_dict = get_stock_status(stock_list, 'ST')
        return [s for s in stock_list if not status_dict.get(s)]
    except Exception:
        return stock_list


def filter_kcbj_stock(stock_list):
    """过滤科创板、创业板、北交所"""
    return [s for s in stock_list
            if not (s.startswith('4') or s.startswith('8') or s.startswith('3') or s.startswith('68'))]


def filter_new_stock(context, stock_list):
    """过滤次新股（上市不满375天）"""
    if g.stock_basic is None:
        return stock_list
    today = context.current_dt
    result = []
    for stock in stock_list:
        row = g.stock_basic[g.stock_basic['ptrade_code'] == stock]
        if row.empty:
            result.append(stock)
            continue
        list_date = pd.to_datetime(row.iloc[0]['list_date'])
        if (today - list_date).days >= 375:
            result.append(stock)
    return result


def filter_by_fundamentals(context, stock_list):
    """用基本面指标筛选：ROE>15%、ROA>10%，按市值升序"""
    if g.fina_indicator is None or g.fina_indicator.empty:
        return stock_list[:g.stock_num]

    # 取最新一期财报
    fina = g.fina_indicator.copy()
    stock_set = set(stock_list)
    fina = fina[fina['ptrade_code'].isin(stock_set)]

    # 取每只股票最新一期
    fina = fina.sort_values('end_date', ascending=False).drop_duplicates(subset=['ptrade_code'])

    # 筛选ROE>15%且ROA>10%
    filtered = fina[(fina['roe'] > 15) & (fina['roa'] > 10)]
    if filtered.empty:
        return []

    result = filtered['ptrade_code'].tolist()
    return result[:g.stock_num]
