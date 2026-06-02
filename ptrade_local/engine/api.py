"""
MiniPTrade PTrade API 兼容层 — 注入策略命名空间
"""
from __future__ import annotations

import types

import numpy as np
import pandas as pd

from .context import Context, Portfolio
from .data_loader import DataLoader


class _Logger:
    def info(self, msg):
        print(f"[INFO] {msg}")

    def warning(self, msg):
        print(f"[WARN] {msg}")

    def error(self, msg):
        print(f"[ERROR] {msg}")


class _StockData:
    def __init__(self, code: str, data_loader: DataLoader, dt: pd.Timestamp):
        self._code = code
        self._dl = data_loader
        self._dt = dt

    @property
    def last(self) -> float:
        return self._dl.get_price(self._code, self._dt)


class _DataProxy:
    """data[security] 返回 _StockData 对象, 支持 .last 获取当前价"""
    def __init__(self, data_loader: DataLoader, get_dt):
        self._dl = data_loader
        self._get_dt = get_dt

    def __getitem__(self, key: str) -> _StockData:
        return _StockData(key, self._dl, self._get_dt())


class _TradeRecord:
    __slots__ = ('id', 'stock', 'amount', 'price', 'commission', 'dt')

    def __init__(self, tid, stock, amount, price, commission, dt):
        self.id = tid
        self.stock = stock
        self.amount = amount
        self.price = price
        self.commission = commission
        self.dt = dt


# ======================================================================
# 工厂函数
# ======================================================================
def create_api_namespace(
    context: Context,
    portfolio: Portfolio,
    data_loader: DataLoader,
    trading_days: list[pd.Timestamp],
    all_stock_codes: list[str],
) -> tuple[dict, list[_TradeRecord]]:
    """
    返回 (api_dict, trades_list)
    api_dict: {name: callable} 注入到策略命名空间
    trades_list: 可变列表，记录所有成交
    """
    trades: list[_TradeRecord] = []
    _trade_id_counter = [0]
    _benchmark_code = ['000300.SS']
    _slippage = [0.0]
    # 佣金组件（与 PTrade set_commission 关键字参数对齐）
    _commission_rate = [0.0003]       # 佣金费率（万3 默认）
    _min_commission = [5.0]           # 最低佣金（元）
    _stamp_duty_rate = [0.001]        # 印花税（仅卖出，千1）
    _transfer_fee_rate = [0.00001]    # 过户费（万0.1）

    def _current_dt() -> pd.Timestamp:
        return context.current_dt

    # ------------------------------------------------------------------
    # 内部: 计算手续费（佣金+印花税+过户费），与 PTrade 规则一致
    # ------------------------------------------------------------------
    def _calc_fees(turnover: float, direction: str) -> tuple[float, float, float, float]:
        """
        返回 (total_fee, commission, stamp_duty, transfer_fee)
        - turnover: 成交金额
        - direction: 'BUY' / 'SELL'
        - 佣金: max(min_commission, turnover * commission_rate)
        - 印花税: 卖出千1，买入0
        - 过户费: 双向万0.1
        """
        turnover = max(0.0, float(turnover))
        commission = max(_min_commission[0], turnover * _commission_rate[0])
        stamp_duty = turnover * _stamp_duty_rate[0] if direction == 'SELL' else 0.0
        transfer_fee = turnover * _transfer_fee_rate[0]
        return commission + stamp_duty + transfer_fee, commission, stamp_duty, transfer_fee

    # ------------------------------------------------------------------
    # 内部: 执行一笔交易（按 PTrade 费用规则计费）
    # ------------------------------------------------------------------
    def _execute_trade(security: str, amount: int) -> int | None:
        if amount == 0:
            return None

        dt = _current_dt()
        bar = data_loader.get_bar(security, dt)
        close_price = bar.get('close', 0.0)
        if close_price <= 0:
            return None

        if amount > 0:
            # 买入
            exec_price = close_price * (1 + _slippage[0])
            if exec_price > bar.get('high_limit', 999999):
                return None
            shares = amount
            cost = exec_price * shares
            total_fee, commission, _, transfer_fee = _calc_fees(cost, 'BUY')
            total_cost = cost + total_fee
            if total_cost > portfolio.cash:
                return None
            portfolio.cash -= total_cost
            pos = portfolio.get_position(security)
            old_total = pos.amount * pos.cost_basis
            pos.amount += shares
            # 成本价 = (旧成本 + 买入成交额) / 总股数（不含费用，与 PTrade 一致）
            pos.cost_basis = (old_total + cost) / pos.amount if pos.amount > 0 else 0.0
            pos.enable_amount = pos.amount
        else:
            # 卖出
            sell_shares = -amount
            pos = portfolio.get_position(security)
            if pos.amount < sell_shares:
                return None
            exec_price = close_price * (1 - _slippage[0])
            if exec_price < bar.get('low_limit', 0):
                return None
            revenue = exec_price * sell_shares
            total_fee, commission, stamp_duty, transfer_fee = _calc_fees(revenue, 'SELL')
            portfolio.cash += revenue - total_fee
            pos.amount -= sell_shares
            pos.enable_amount = pos.amount
            if pos.amount == 0:
                pos.cost_basis = 0.0

        _trade_id_counter[0] += 1
        tid = _trade_id_counter[0]
        trades.append(_TradeRecord(tid, security, amount, exec_price, commission, dt))
        return tid

    # ------------------------------------------------------------------
    # 数据 API
    # ------------------------------------------------------------------
    def get_history(count, frequency='1d', field=None, security_list=None,
                    fq=None, include=False, fill='nan', is_dict=False):
        if frequency != '1d':
            return pd.DataFrame()

        # 规范化 field
        if field is None:
            fields = ['open', 'high', 'low', 'close', 'volume', 'amount']
        elif isinstance(field, str):
            fields = [field]
        else:
            fields = list(field)

        # 规范化 security_list
        is_single = isinstance(security_list, str) or security_list is None
        if is_single:
            codes = [security_list] if security_list else all_stock_codes
        else:
            codes = list(security_list)

        # 计算日期范围
        dt = _current_dt()
        if dt not in trading_days:
            # 找最近的前一个交易日
            earlier = [d for d in trading_days if d <= dt]
            if not earlier:
                return pd.DataFrame()
            dt = earlier[-1]
        dt_idx = trading_days.index(dt)
        end_idx = dt_idx if include else dt_idx - 1
        start_idx = max(0, end_idx - count + 1)
        date_range = trading_days[start_idx:end_idx + 1]

        if not date_range:
            return pd.DataFrame()

        if is_single:
            # 字符串: 返回 DataFrame, 日期索引
            return data_loader.get_stock_history(codes[0], date_range, fields)
        elif len(codes) == 1:
            # list-of-1: 返回 dict {code: DataFrame}, 兼容 hist[stock] 访问
            sdf = data_loader.get_stock_history(codes[0], date_range, fields)
            return {codes[0]: sdf}
        else:
            # 多股票: 纵向堆叠 DataFrame, 带 'code' 列
            all_rows = []
            for code in codes:
                sdf = data_loader.get_stock_history(code, date_range, fields)
                if sdf.empty:
                    continue
                row_dict = {'code': code}
                for f in fields:
                    if f in sdf.columns:
                        row_dict[f] = sdf[f].values
                all_rows.append(pd.DataFrame(row_dict))
            if all_rows:
                return pd.concat(all_rows, ignore_index=True)
            return pd.DataFrame()

    def get_fundamentals(security_list, table, fields, date=None,
                         start_year=None, end_year=None,
                         report_types=None, date_type=None,
                         merge_type=None, is_dataframe=None):
        codes = [security_list] if isinstance(security_list, str) else list(security_list)
        return pd.DataFrame({
            'code': codes,
            **{f: [np.nan] * len(codes) for f in fields}
        })

    def get_Ashares(date=None):
        return list(all_stock_codes)

    def get_stock_status(security_list, status_type):
        codes = security_list if isinstance(security_list, list) else [security_list]
        return {c: False for c in codes}

    def get_industry_for_stocks(stock_list):
        return {}

    def get_price(security, start_date=None, end_date=None, frequency='1d',
                  fields=None, skip_paused=True, fq='pre', count=None):
        dt = _current_dt()
        bar = data_loader.get_bar(security, dt)
        if not fields:
            return bar.get('close', 0.0)
        if isinstance(fields, str):
            return bar.get(fields, 0.0)
        return {f: bar.get(f, 0.0) for f in fields}

    # ------------------------------------------------------------------
    # 交易 API
    # ------------------------------------------------------------------
    def order(security, amount):
        return _execute_trade(security, int(amount))

    def order_target(security, amount):
        pos = portfolio.get_position(security)
        delta = int(amount) - pos.amount
        if delta != 0:
            return _execute_trade(security, delta)
        return None

    def order_value(security, value):
        if value == 0:
            return None
        dt = _current_dt()
        close = data_loader.get_price(security, dt)
        if close <= 0:
            return None
        if value > 0:
            exec_price = close * (1 + _slippage[0])
            shares = int(value / exec_price / 100) * 100
            if shares <= 0:
                return None
            return _execute_trade(security, shares)
        else:
            exec_price = close * (1 - _slippage[0])
            shares = int(abs(value) / exec_price / 100) * 100
            if shares <= 0:
                return None
            pos = portfolio.get_position(security)
            shares = min(shares, pos.amount)
            if shares <= 0:
                return None
            return _execute_trade(security, -shares)

    def order_target_value(security, value):
        dt = _current_dt()
        close = data_loader.get_price(security, dt)
        if close <= 0:
            return None
        pos = portfolio.get_position(security)
        current_value = pos.amount * close
        delta_value = value - current_value
        if abs(delta_value) < close * 100:
            return None
        if delta_value > 0:
            return order_value(security, delta_value)
        else:
            return order_value(security, delta_value)

    def set_yesterday_position(pos_list):
        for pos_dict in pos_list:
            code = pos_dict.get('sid', pos_dict.get('code', ''))
            amount = int(pos_dict.get('amount', 0))
            cost = float(pos_dict.get('cost_basis', 0))
            enable = int(pos_dict.get('enable_amount', amount))
            pos = portfolio.get_position(code)
            pos.amount = amount
            pos.cost_basis = cost
            pos.enable_amount = enable

    def cancel_order(order_id):
        pass

    def get_open_orders():
        return []

    def get_orders():
        return {t.id: t for t in trades}

    def get_trades():
        return {t.id: t for t in trades}

    # ------------------------------------------------------------------
    # 配置 API
    # ------------------------------------------------------------------
    def set_benchmark(code):
        _benchmark_code[0] = code

    def set_universe(securities):
        pass

    def set_slippage(value):
        _slippage[0] = float(value)

    def set_commission(commission_ratio=None, min_commission=None,
                       stamp_duty_ratio=None, transfer_fee_ratio=None,
                       value=None):
        """与 PTrade set_commission 完全对齐（关键字参数）

        PTrade 签名: set_commission(commission_ratio, min_commission,
                                    stamp_duty_ratio, transfer_fee_ratio)
        旧版 MiniPTrade 兼容: set_commission(value) - 单值当作 commission_ratio

        任一参数传 None 表示保持原值不变。
        """
        # 兼容旧式 set_commission(0.0003) 调用
        if value is not None and commission_ratio is None:
            commission_ratio = value
        if commission_ratio is not None:
            _commission_rate[0] = float(commission_ratio)
        if min_commission is not None:
            _min_commission[0] = float(min_commission)
        if stamp_duty_ratio is not None:
            _stamp_duty_rate[0] = float(stamp_duty_ratio)
        if transfer_fee_ratio is not None:
            _transfer_fee_rate[0] = float(transfer_fee_ratio)

    def set_stamp_duty(value):
        """单独设置印花税（PTrade 部分版本支持）"""
        _stamp_duty_rate[0] = float(value)

    def set_transfer_fee(value):
        """单独设置过户费"""
        _transfer_fee_rate[0] = float(value)

    def set_option(key, value):
        pass

    def set_price_limit(enabled=True):
        pass

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def record(**kwargs):
        pass

    def plot(**kwargs):
        pass

    # Data proxy
    data_proxy = _DataProxy(data_loader, _current_dt)

    # 组装命名空间
    ns = {
        # 数据
        'get_history': get_history,
        'get_fundamentals': get_fundamentals,
        'get_Ashares': get_Ashares,
        'get_stock_status': get_stock_status,
        'get_industry_for_stocks': get_industry_for_stocks,
        'get_price': get_price,
        'get_current_data': lambda code, fields=None: get_price(code, fields=fields),
        # 交易
        'order': order,
        'order_target': order_target,
        'order_value': order_value,
        'order_target_value': order_target_value,
        'set_yesterday_position': set_yesterday_position,
        'cancel_order': cancel_order,
        'get_open_orders': get_open_orders,
        'get_orders': get_orders,
        'get_trades': get_trades,
        # 持仓
        'get_position': portfolio.get_position,
        'get_positions': portfolio.get_positions,
        # 配置
        'set_benchmark': set_benchmark,
        'set_universe': set_universe,
        'set_slippage': set_slippage,
        'set_commission': set_commission,
        'set_stamp_duty': set_stamp_duty,
        'set_transfer_fee': set_transfer_fee,
        'set_option': set_option,
        'set_price_limit': set_price_limit,
        # 工具
        'log': _Logger(),
        'record': record,
        'plot': plot,
        # data proxy
        '_data_proxy': data_proxy,
    }

    return ns, trades
