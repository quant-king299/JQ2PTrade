"""
PTrade 双均线策略 - 直接用 PTrade 语法编写

策略逻辑:
  - 5日均线上穿20日均线买入
  - 5日均线下穿20日均线卖出
  - 最多持仓5只
"""


def initialize(context):
    set_benchmark('000300.SS')
    g.max_hold = 5
    g.short_window = 5
    g.long_window = 20
    g.positions_list = []


def before_trading_start(context, data):
    current_date = context.blotter.current_dt.strftime("%Y%m%d")
    g.all_stocks = get_Ashares(current_date)
    status_dict = get_stock_status(g.all_stocks, 'ST')
    g.tradeable = [s for s in g.all_stocks if not status_dict.get(s)]


def handle_data(context, data):
    # 卖出逻辑
    current_positions = get_positions()
    for stock in list(g.positions_list):
        if stock not in current_positions:
            g.positions_list.remove(stock)
            continue
        try:
            hist = get_history(g.long_window + 1, '1d', ['close'], [stock])
            if stock in hist and len(hist[stock]) >= g.long_window:
                closes = hist[stock]['close']
                ma_short = closes.rolling(g.short_window).mean()
                ma_long = closes.rolling(g.long_window).mean()
                if ma_short.iloc[-1] < ma_long.iloc[-1] and ma_short.iloc[-2] >= ma_long.iloc[-2]:
                    order_target(stock, 0)
                    g.positions_list.remove(stock)
        except Exception:
            continue

    # 买入逻辑
    if len(g.positions_list) >= g.max_hold:
        return

    need = g.max_hold - len(g.positions_list)
    candidates = []
    check_list = [s for s in g.tradeable[:200] if s not in g.positions_list]

    for stock in check_list:
        if len(candidates) >= need:
            break
        try:
            hist = get_history(g.long_window + 1, '1d', ['close'], [stock])
            if stock in hist and len(hist[stock]) >= g.long_window:
                closes = hist[stock]['close']
                ma_short = closes.rolling(g.short_window).mean()
                ma_long = closes.rolling(g.long_window).mean()
                if ma_short.iloc[-1] > ma_long.iloc[-1] and ma_short.iloc[-2] <= ma_long.iloc[-2]:
                    candidates.append(stock)
        except Exception:
            continue

    for stock in candidates:
        order_target_value(stock, context.portfolio.portfolio_value / g.max_hold)
        g.positions_list.append(stock)
