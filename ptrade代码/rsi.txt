import time
import traceback
import numpy as np
import pandas as pd

def initialize(context):
    print('策略初始化')
    g.ob_stock = ['601020.SS', '000032.SZ', '603688.SS']
    set_universe(g.ob_stock)
    g.total_amt_map = {}
    g.stock_minute_data = {}
    g.buy_money = 10e4          # 每次买入多少价值的股票
    g.total_money = 20e4        # 每只股票总共要买入多少价值
    g.N = 5
    g.M = 3
    g.history_k_line_num = 120  # 获取多少根历史K线
    g.position_ratio = 0.5      # 第二次减仓比例
    g.stop_loss_ratio = 0.1     # 止损率
    g.stop_earned_ratio = 0.3   # 止盈率
    g.position_status = {       # 状态字典
        code: {
            'first_buy': False,    # 是否首次买入
            'second_buy': False,   # 是否二次加仓
            'cost_amt': 0,
            'wrsi_cross_down_count': 0,  # WRSI下穿80计数
            'wrsi_cross_up_count': 0, # 上穿20计数
        } for code in g.ob_stock
    }


def before_trading_start(context, data):
    pass


def handle_data(context, data):
    try:
        _handle_data_impl(context, data)
    except Exception as e:
        error_msg = traceback.format_exc()
        log.error(f"[策略异常] {error_msg}")
        print(f"[策略异常] {error_msg}")

def _handle_data_impl(context, data):
    for stock_code in g.ob_stock:
        current_position = get_position(stock_code)
        if current_position is None:
            current_position = type('obj', (object,), {'amount': 0, 'cost_basis': 0})()

        hist_data = get_history(g.history_k_line_num, '1d', ['close', 'volume'], stock_code)
        if hist_data is None or len(hist_data) == 0:
            continue
        cur_date = context.current_dt.strftime('%Y%m%d')
        closes = hist_data['close']
        volumes = hist_data['volume']

        # 1. 计算WRSI指标（标准RSI公式，0~100范围）
        changes = closes.diff(1)
        gains = changes.copy()
        gains[gains < 0] = 0
        losses = changes.copy()
        losses[losses > 0] = 0
        losses = losses.abs()

        avg_gain = gains.rolling(window=6).mean()
        avg_loss = losses.rolling(window=6).mean()
        rs = avg_gain / avg_loss
        wrsi = 100 - (100 / (1 + rs))

        # 2. 计算MACD条件
        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        diff = ema12 - ema26

        dea = diff.ewm(span=9, adjust=False).mean()
        macd_cond = dea.diff() > 0

        # 3. 计算均线条件
        ma_m = closes.rolling(window=g.M).mean()
        close_cond = closes > ma_m

        # 4. 基础信号和复合信号
        cross_up = (wrsi.shift(1) <= 20) & (wrsi > 20)
        cross_down = (wrsi.shift(1) >= 80) & (wrsi < 80)
        base_signal = cross_up & close_cond
        composite_signal = base_signal & macd_cond

        not_suspended = volumes.iloc[-1] > 0
        # 5. 检查最近N日是否有信号
        exist_signal = composite_signal.rolling(window=g.N, min_periods=1).max().astype(bool)

        # 计算连续亏损
        last_season_loss_flag = False
        try:
            table_last_season = get_fundamentals([stock_code], 'income_statement', ['net_profit'],
                                                  date=cur_date)
            table_last_year = get_fundamentals([stock_code], 'income_statement', ['net_profit'],
                                               date=cur_date, report_types='4')
            if (table_last_year is not None and len(table_last_year) > 0 and
                    table_last_season is not None and len(table_last_season) > 0):
                last_season_loss_flag = (table_last_year['net_profit'].iloc[-1] < 0 and
                                         table_last_season['net_profit'].iloc[-1] < 0)
        except Exception as e:
            log.info(f"获取财报数据失败 {stock_code}: {e}")

        # 综合买入信号
        buy_signal = exist_signal.iloc[-1] and not_suspended and not last_season_loss_flag

        # 交易逻辑
        if buy_signal:
            if current_position.amount == 0 and not g.position_status[stock_code]['first_buy']:
                order_value(stock_code, g.buy_money)
                g.position_status[stock_code].update({'first_buy': True})
                log.info(f"首次买入 {stock_code} 10万元")
                continue

        if cross_up.iloc[-1]:
            g.position_status[stock_code]['wrsi_cross_up_count'] += 1
            if (not g.position_status[stock_code]['second_buy'] and
                    g.position_status[stock_code]['wrsi_cross_up_count'] >= 2):
                order_value(stock_code, g.buy_money)
                log.info(f"二次加仓 {stock_code} 10万元")
                g.position_status[stock_code]['second_buy'] = True

        if current_position.amount > 0:
            cost_price = current_position.cost_basis
            current_price = closes.iloc[-1]
            if current_price <= cost_price * (1-g.stop_loss_ratio):
                order_target(stock_code, 0)
                log.info(f"触发止损，清仓 {stock_code}")
                reset_position_status(stock_code)
                continue

            if current_price >= cost_price * (1+g.stop_earned_ratio):
                order_target(stock_code, 0)
                log.info(f"触发止盈，清仓 {stock_code}")
                reset_position_status(stock_code)
                continue

            if cross_down.iloc[-1]:
                g.position_status[stock_code]['wrsi_cross_down_count'] += 1
                if (g.position_status[stock_code]['first_buy'] and
                        not g.position_status[stock_code]['second_buy']):
                    order_target(stock_code, 0)
                    log.info(f"第一次买入，WRSI下穿80，没有第二次买入，清仓 {stock_code}")
                    reset_position_status(stock_code)
                elif g.position_status[stock_code]['second_buy']:
                    if g.position_status[stock_code]['wrsi_cross_down_count'] == 1:
                        sell_amount = current_position.amount * g.position_ratio
                        order(stock_code, -sell_amount)
                        log.info(f"WRSI下穿80，卖出50%仓位 {stock_code}")
                    elif g.position_status[stock_code]['wrsi_cross_down_count'] >= 2:
                        order_target(stock_code, 0)
                        log.info(f"WRSI二次下穿80，清仓剩余 {stock_code}")
                        reset_position_status(stock_code)


def reset_position_status(stock_code):
    g.position_status[stock_code] = {
        'first_buy': False,
        'second_buy': False,
        'cost_amt': 0,
        'wrsi_cross_down_count': 0,
        'wrsi_cross_up_count': 0,
    }
