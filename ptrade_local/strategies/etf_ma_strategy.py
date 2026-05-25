def initialize(context):
    # 初始化5个ETF标的
    g.etfs = ['510300.SS', '510500.SS', '159915.SZ', '510050.SS', '159919.SZ']
    set_universe(g.etfs)
    
    # 参数设置
    g.base_position = 1000  # 每只ETF底仓1000股
    g.trade_amount = 200    # 每次交易200股
    g.initial_cash_per_etf = 100000  # 每个ETF分配的初始资金
    
    # 回测模式自动初始化持仓（无需环境检测）
    # 构建初始持仓参数
    pos_list = []
    for etf in g.etfs:
        pos = {
            "sid": etf,
            "amount": g.base_position,
            "cost_basis": 0,  # 回测会自动计算成本价
            "enable_amount": g.base_position
        }
        pos_list.append(pos)
    
    # 设置昨日持仓（回测/实盘通用逻辑）
    set_yesterday_position(pos_list)
    
    # 检查资金是否足够（回测会自动忽略此检查）
    if context.portfolio.starting_cash < g.initial_cash_per_etf * len(g.etfs):
        raise ValueError("初始资金不足，请增加资金配置")

def before_trading_start(context, data):
    # 记录需要建立底仓的ETF（回测已自动处理）
    g.need_build_position = []
    for etf in g.etfs:
        position = get_position(etf)
        if position is None or position.amount < g.base_position:
            g.need_build_position.append(etf)

def handle_data(context, data):
    current_time = context.blotter.current_dt.time()
    
    # 交易时间段判断(09:30-11:30,13:00-15:00)
    is_trading_time = ((9 <= current_time.hour < 11 and 30 <= current_time.minute <= 59) or 
                      (current_time.hour == 11 and current_time.minute <= 30) or
                      (13 <= current_time.hour < 15))
    
    if not is_trading_time:
        return
    
    # 处理底仓建立（回测已自动完成）
    if g.need_build_position and current_time.hour == 9 and current_time.minute == 30:
        for etf in g.need_build_position[:]:  # 使用副本遍历
            position = get_position(etf)
            current_amount = position.amount if position else 0
            if current_amount < g.base_position:
                order_target(etf, g.base_position)
                log.info("建立底仓 %s 目标:%s股 当前价:%s" % (etf, g.base_position, data[etf].last))
                g.need_build_position.remove(etf)
    
    # 交易信号检查（每小时执行一次）
    if current_time.minute not in [0, 30]:
        return
    
    for etf in g.etfs:
        # 确保底仓已建立（回测已自动满足）
        position = get_position(etf)
        if position is None or position.amount < g.base_position:
            continue
            
        # 获取历史数据
        try:
            df = get_history(count=20, frequency='15m', field='close', security_list=etf, fq=None, include=False)
            if len(df) < 10:
                continue
                
            # 计算均线
            ma5 = df['close'][-5:].mean()
            ma10 = df['close'][-10:].mean()
            current_price = data[etf].last
            
            # 交易逻辑
            if ma5 > ma10 * 1.001:  # 金叉信号
                order(etf, g.trade_amount)
                log.info("买入 %s %s股 价格:%s MA5:%.3f>MA10:%.3f" % (etf, g.trade_amount, current_price, ma5, ma10))
            elif ma5 < ma10 * 0.999:  # 死叉信号
                sell_amount = min(g.trade_amount, position.amount - g.base_position)
                if sell_amount > 0:
                    order(etf, -sell_amount)
                    log.info("卖出 %s %s股 价格:%s MA5:%.3f<MA10:%.3f" % (etf, sell_amount, current_price, ma5, ma10))
        except Exception as e:
            log.error("处理 %s 时出错: %s" % (etf, str(e)))  # 统一日志级别

def after_trading_end(context, data):
    # 盘后持仓检查
    for etf in g.etfs:
        position = get_position(etf)
        if position:
            log.info("收盘持仓 %s: %s股" % (etf, position.amount))
            if position.amount < g.base_position:
                log.warning("持仓不足 %s 需要补充" % etf)  # 修正日志方法
        else:
            log.warning("无持仓 %s 需要建立底仓" % etf)  # 修正日志方法