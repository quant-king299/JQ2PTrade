"""
五合一打板策略 — MiniPTrade 日线回测简化版
基于聚宽原策略核心逻辑，适配MiniPTrade引擎(仅日线)
移除了: 集合竞价、分钟K线、资金流、概念板块、tushare
保留了: 涨停检测、连板统计、五类打板选股、评分买入、止损卖出
"""
import pandas as pd
import numpy as np


# ======================================================================
# 初始化
# ======================================================================
def initialize(context):
    g.position_limit = 2           # 最大持仓数
    g.min_score = 8                # 最低买入评分（简化后降低）
    g.priority_config = ["lb", "rzq", "yje", "dk", "fxsbdk"]
    g.is_empty = False
    g.qualified_stocks = []
    g.score_cache = {}
    g.emo_count = []
    g.trade_stats = {'market_stats': {}, 'daily_returns': []}
    g.trading_days_history = []    # 记录已过的交易日
    g.today_trades = []


# ======================================================================
# 盘前: 市场环境判断 + 策略优先级
# ======================================================================
def before_trading_start(context, data):
    # 记录交易日
    g.trading_days_history.append(context.current_dt)
    g.score_cache = {}

    # 清空当日交易
    g.today_trades = []

    # 获取大盘5日数据判断趋势
    try:
        idx_hist = get_history(5, '1d', ['close', 'volume'],
                               security_list='000300.SS', include=True)
        if idx_hist is not None and len(idx_hist) >= 2:
            closes = idx_hist['close'].values
            vols = idx_hist['volume'].values
            change = (closes[-1] / closes[-2] - 1) * 100
            vol_ratio = vols[-1] / vols[:-1].mean() if vols[:-1].mean() > 0 else 1

            if change > 1:
                trend = 'strong_up'
            elif change > 0:
                trend = 'up'
            elif change > -1:
                trend = 'flat'
            else:
                trend = 'down'

            g.trade_stats['market_stats'] = {
                'trend': trend, 'change': change, 'vol_ratio': vol_ratio
            }
            update_priority(trend)
    except Exception as e:
        log.warning(f"盘前统计失败: {e}")


def update_priority(trend):
    if trend == 'down':
        g.priority_config = ["lb", "fxsbdk", "yje", "rzq", "dk"]
    elif trend in ('strong_up', 'up'):
        g.priority_config = ["lb", "rzq", "yje", "fxsbdk", "dk"]
    else:
        g.priority_config = ["lb", "rzq", "yje", "fxsbdk", "dk"]


# ======================================================================
# 主逻辑: 选股 + 买入 + 卖出 (日线级别)
# ======================================================================
def handle_data(context, data):
    # ---------- 1. 卖出逻辑 ----------
    do_sell(context)

    # ---------- 2. 空仓判断 ----------
    if should_empty(context):
        for stock in list(get_positions().keys()):
            order_target_value(stock, 0)
            log.info(f"[空仓] 卖出 {stock}")
        g.is_empty = True
        return
    g.is_empty = False

    # ---------- 3. 周五不买 (周四晚上决策效果更好) ----------
    if context.current_dt.weekday() == 4:
        log.info("周五不执行买入")
        return

    # ---------- 4. 选股 ----------
    qualified = do_select(context)
    g.qualified_stocks = qualified
    if not qualified:
        log.info("今日无符合条件的股票")
        return

    # ---------- 5. 买入 ----------
    do_buy(context, qualified)


# ======================================================================
# 选股核心
# ======================================================================
def do_select(context):
    """五合一选股: 从前一交易日数据中识别涨停股并分类

    性能优化: 逐只股票查询避免一次性加载5000+只股票数据
    """
    codes = get_Ashares()
    if not codes:
        return []

    # 过滤: 排除科创板/北交所/代码异常
    codes = [c for c in codes if c[:3] in ('000', '001', '002', '003',
                                            '300', '301', '600', '601', '603', '605')]

    # ---- 逐只扫描涨停 (性能优化: 每只只查2天数据) ----
    hl0_list = []       # 昨日收盘涨停
    ever_hl_list = []   # 昨日曾涨停(最高=涨停价 但 收盘!=涨停价)
    ll_list = []        # 昨日跌停
    hl1_set = set()     # 前天涨停的股票
    stocks_data = {}    # 缓存数据

    # 批量查询: 分批处理, 每批200只
    batch_size = 200
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        try:
            hist = get_history(2, '1d', ['close', 'high', 'low', 'high_limit', 'volume'],
                               security_list=batch, include=True)
            if hist is None or hist.empty:
                continue
            if 'code' not in hist.columns:
                continue

            for code in hist['code'].unique():
                sub = hist[hist['code'] == code].tail(2)
                if len(sub) < 1:
                    continue
                stocks_data[code] = sub
                row = sub.iloc[-1]

                # 涨停: 收盘价 = 涨停价
                if row['close'] == row['high_limit'] and row['high_limit'] > 0:
                    hl0_list.append(code)
                # 曾涨停: 最高价 = 涨停价 但 收盘价 != 涨停价
                elif row['high'] == row['high_limit'] and row['close'] != row['high_limit'] and row['high_limit'] > 0:
                    ever_hl_list.append(code)

                # 跌停: close <= preclose * 0.9 (近似)
                if len(sub) >= 2:
                    prev_row = sub.iloc[-2]
                    if prev_row['close'] == prev_row['high_limit'] and prev_row['high_limit'] > 0:
                        hl1_set.add(code)
                    # 跌停检测 (用前一天收盘*0.9估算跌停价)
                    low_limit_est = prev_row['close'] * 0.9
                    if row['close'] <= low_limit_est * 1.005 and row['close'] > 0:
                        ll_list.append(code)
        except Exception:
            continue

    # ---- 分类 ----
    gap_up = [s for s in hl0_list if s not in hl1_set]        # 首板涨停(用于一进二)
    gap_down = [s for s in hl0_list if s not in hl1_set]      # 首板涨停(用于低开)
    reversal = ever_hl_list                                      # 弱转强

    # ---- 连板统计 (只对涨停股计算, 逐只查询但限制数量) ----
    consecutive_map = {}
    for s in hl0_list[:50]:
        try:
            h = get_history(20, '1d', ['close', 'high_limit', 'low'],
                            security_list=s, include=True)
            if h is None or len(h) < 1:
                continue
            cnt = 0
            extreme_cnt = 0
            for _, row in h.iterrows():
                if row['close'] == row['high_limit'] and row['high_limit'] > 0:
                    cnt += 1
                    if row['low'] == row['high_limit']:
                        extreme_cnt += 1
                else:
                    cnt = 0
                    extreme_cnt = 0
            consecutive_map[s] = (cnt, extreme_cnt)
        except Exception:
            continue

    # ---- 子策略选股 ----
    lblt_stocks = []
    rzq_stocks = []
    yje_stocks = []
    dk_stocks = []
    fxsbdk_stocks = []

    # 1. 连板龙头: 最高连板数
    if consecutive_map:
        max_count = max(v[0] for v in consecutive_map.values())
        if max_count >= 2:
            leaders = [s for s, (cnt, ext) in consecutive_map.items()
                       if cnt == max_count and ext < 10]
            lblt_stocks = leaders[:3]

    # 2. 弱转强
    for s in reversal[:30]:
        if s in stocks_data:
            row = stocks_data[s].iloc[-1]
            if row['volume'] * row['close'] > 3e8:
                rzq_stocks.append(s)

    # 3. 一进二
    for s in gap_up[:50]:
        if s in stocks_data:
            row = stocks_data[s].iloc[-1]
            turnover = row['volume'] * row['close']
            if 5.5e8 < turnover < 20e8:
                yje_stocks.append(s)

    # 4. 首板低开(简化为低价首板)
    for s in gap_down[:30]:
        if s in stocks_data:
            row = stocks_data[s].iloc[-1]
            if 3 < row['close'] < 47:
                dk_stocks.append(s)

    # 5. 反向首板低开
    fxsbdk_stocks = ll_list[:5]

    # ---- 按优先级合并 ----
    priority_lists = {
        "lb": lblt_stocks,
        "rzq": rzq_stocks,
        "yje": yje_stocks,
        "dk": dk_stocks,
        "fxsbdk": fxsbdk_stocks,
    }
    seen = set()
    merged = []
    for ptype in g.priority_config:
        for s in priority_lists.get(ptype, []):
            if s not in seen:
                seen.add(s)
                merged.append(s)

    # ---- 评分筛选 ----
    scored = []
    for s in merged:
        score = calc_score(s, context, stocks_data)
        g.score_cache[s] = score
        if score >= g.min_score:
            scored.append((s, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    result = [s for s, _ in scored]
    log.info(f"选股: 连板{len(lblt_stocks)} 弱转强{len(rzq_stocks)} "
             f"一进二{len(yje_stocks)} 低开{len(dk_stocks)} 反向{len(fxsbdk_stocks)} "
             f"-> 最终{len(result)}只")
    return result


# ======================================================================
# 评分
# ======================================================================
def calc_score(stock, context, stocks_data):
    """简化评分: 涨停分 + 技术分 + 量能分 (满分30)"""
    score = 0

    if stock not in stocks_data:
        return 0
    sub = stocks_data[stock]
    if len(sub) < 1:
        return 0
    row = sub.iloc[-1]

    # --- 1. 涨停分 (0-10) ---
    if row['close'] == row['high_limit'] and row['high_limit'] > 0:
        score += 8
        # 连板加分
        if len(sub) >= 2:
            prev = sub.iloc[-2]
            if prev['close'] == prev['high_limit'] and prev['high_limit'] > 0:
                score += 5  # 连板额外加分

    # --- 2. 量能分 (0-8) ---
    if len(sub) >= 2 and row['volume'] > 0:
        prev_vol = sub.iloc[-2]['volume']
        if prev_vol > 0:
            vol_ratio = row['volume'] / prev_vol
            if 1.0 <= vol_ratio <= 3.0:
                score += 4
            elif 0.5 <= vol_ratio < 1.0:
                score += 2
            elif vol_ratio > 3.0:
                score += 1

    # --- 3. 价格/市值分 (0-6) ---
    price = row['close']
    if 5 < price < 30:
        score += 4
    elif 3 < price < 50:
        score += 2

    # --- 4. 技术趋势分 (0-6) ---
    try:
        hist = get_history(10, '1d', ['close'], security_list=stock, include=True)
        if hist is not None and len(hist) >= 5:
            closes = hist['close'].values
            ma5 = closes[-5:].mean()
            if closes[-1] > ma5:
                score += 3
            if len(closes) >= 3 and closes[-1] > closes[-2] > closes[-3]:
                score += 3
    except Exception:
        pass

    return min(score, 30)


# ======================================================================
# 买入
# ======================================================================
def do_buy(context, qualified):
    positions = get_positions()
    n_hold = len(positions)
    available = g.position_limit - n_hold
    if available <= 0:
        log.info(f"已达最大持仓 {g.position_limit}")
        return

    buy_list = qualified[:available]
    n_buy = len(buy_list)
    if n_buy == 0:
        return

    # 等权分配
    total_value = context.portfolio.portfolio_value
    per_value = total_value / g.position_limit

    for s in buy_list:
        if context.portfolio.cash < per_value * 0.5:
            log.info(f"资金不足，跳过 {s}")
            continue
        tid = order_target_value(s, per_value)
        if tid:
            pos = get_position(s)
            price = pos.cost_basis if pos.amount > 0 else 0
            log.info(f"买入 {s} 目标金额:{per_value:.0f}")
            g.today_trades.append({'stock': s, 'action': '买入', 'price': price})
        else:
            log.warning(f"买入 {s} 失败")


# ======================================================================
# 卖出
# ======================================================================
def do_sell(context):
    """日线级别卖出逻辑"""
    positions = get_positions()
    for stock, pos in list(positions.items()):
        if pos.amount <= 0:
            continue

        try:
            # 获取近5日数据
            hist = get_history(5, '1d', ['close', 'high', 'low', 'high_limit', 'volume', 'open'],
                               security_list=stock, include=True)
            if hist is None or len(hist) < 2:
                continue

            yesterday = hist.iloc[-1]
            today = hist.iloc[-1]  # 日线模式下就是当日收盘

            # 1. 跌停卖出
            if yesterday['close'] <= yesterday['high_limit'] * 0.82:
                order_target_value(stock, 0)
                log.info(f"[止损-跌停] 卖出 {stock}")
                g.today_trades.append({'stock': stock, 'action': '卖出', 'reason': '跌停'})
                continue

            # 2. 止损: 亏损超过8%
            cost = pos.cost_basis
            if cost > 0:
                loss_pct = (yesterday['close'] - cost) / cost
                if loss_pct < -0.08:
                    order_target_value(stock, 0)
                    log.info(f"[止损] {stock} 亏损 {loss_pct:.1%}")
                    g.today_trades.append({'stock': stock, 'action': '卖出', 'reason': f'止损{loss_pct:.1%}'})
                    continue

            # 3. 放量长上影卖出
            if len(hist) >= 2:
                avg_vol = hist['volume'].iloc[:-1].mean()
                upper_shadow = yesterday['high'] - max(yesterday['open'], yesterday['close'])
                lower_shadow = min(yesterday['open'], yesterday['close']) - yesterday['low']
                total_range = yesterday['high'] - yesterday['low']

                if (total_range > 0 and avg_vol > 0 and
                    upper_shadow > lower_shadow * 1.5 and
                    upper_shadow > 0.3 * total_range and
                    yesterday['volume'] > 1.5 * avg_vol):
                    order_target_value(stock, 0)
                    log.info(f"[卖出-放量上影] {stock}")
                    g.today_trades.append({'stock': stock, 'action': '卖出', 'reason': '放量上影'})
                    continue

            # 4. 持仓3天以上未涨停 -> 卖出
            # 简化: 获取持仓天数(通过cost_basis和最近价格比较)
            # 如果3天前买入且至今未涨停，卖出
            try:
                hist10 = get_history(10, '1d', ['close', 'high_limit'],
                                     security_list=stock, include=True)
                if hist10 is not None and len(hist10) >= 3:
                    # 最近3天是否有涨停
                    recent_3 = hist10.tail(3)
                    has_limit = any(recent_3['close'] == recent_3['high_limit'])
                    if not has_limit and cost > 0 and yesterday['close'] > cost:
                        # 有盈利且3天无涨停，获利了结
                        profit = (yesterday['close'] - cost) / cost
                        if profit > 0.03:
                            order_target_value(stock, 0)
                            log.info(f"[止盈] {stock} 盈利 {profit:.1%}")
                            g.today_trades.append({'stock': stock, 'action': '卖出', 'reason': f'止盈{profit:.1%}'})
            except Exception:
                pass

        except Exception as e:
            log.warning(f"卖出检查 {stock} 异常: {e}")


# ======================================================================
# 空仓判断
# ======================================================================
def should_empty(context):
    """大盘量能异常时空仓"""
    try:
        idx = get_history(5, '1d', ['volume'], security_list='000300.SS', include=True)
        if idx is None or len(idx) < 3:
            return False
        vols = idx['volume'].values
        avg = vols[:-1].mean()
        cur = vols[-1]
        if avg > 0 and (cur > 2 * avg or cur < 0.5 * avg):
            return True
    except Exception:
        pass
    return False


# ======================================================================
# 盘后记录
# ======================================================================
def after_trading_end(context, data):
    positions = get_positions()
    n_pos = len(positions)
    pv = context.portfolio.portfolio_value
    cash = context.portfolio.cash
    log.info(f"盘后: 净值={pv:,.0f} 持仓={n_pos}只 现金={cash:,.0f}")
