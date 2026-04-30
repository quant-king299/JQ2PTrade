# 聚宽策略转Ptrade - HYBRID版本
# 转换时间: 2026-04-09 22:38:05
# 转换器版本: v3.4 - 修复datetime导入，智能添加辅助函数


# ======================================================================
# ⚠️ get_fundamentals API转换说明 ⚠️
# ======================================================================
# Ptrade的get_fundamentals API与聚宽完全不同：
#
# 【聚宽】: get_fundamentals(query_object) - 使用query对象
# 【Ptrade】: get_fundamentals(stock_list, "table", fields=[...], date=..., is_dataframe=False)
#
# 【Ptrade支持的表名】:
#   - "valuation": 估值数据（市值、PE、PB等）
#   - "indicator": 财务指标（EPS、ROE、营收增长率等）
#   - "balance_statement": 资产负债表
#   - "profit_ability": 利润表数据
#   - "growth_ability": 增长能力数据
#   - "cash_flow_statement": 现金流量表
#
# 【fields参数】:
#   - 单字段: fields='total_value'
#   - 多字段: fields=['code', 'total_value']
#
# 【is_dataframe参数】:
#   - True: 返回DataFrame格式
#   - False或省略: 返回dict格式
#
# 转换器已尝试自动转换，请检查结果是否正确！
# ======================================================================


# get_current_data兼容函数 - 替代聚宽的get_current_data()
def get_current_data_compat(security_list=None):
    """模拟聚宽get_current_data()功能，使用get_price()实现"""
    import pandas as pd

    try:
        if security_list is None:
            # 返回空字典，需要用户传入具体的security_list
            # 或者可以从context.portfolio.positions获取
            return {}

        # 使用get_price获取最近一天的数据
        df = get_price(security_list, count=1, fields=['close', 'high', 'low', 'volume', 'paused', 'name'])

        # 构造返回数据，模拟聚宽的get_current_data()返回格式
        result = {}
        for stock in security_list:
            if stock in df.index:
                row = df.loc[stock]
                # 估算涨跌停价格
                close_price = row['close'] if 'close' in row else 0
                result[stock] = type('obj', (), {
                    'last_price': close_price,
                    'high_limit': close_price * 1.1 if close_price > 0 else 0,  # 估算涨停价
                    'low_limit': close_price * 0.9 if close_price > 0 else 0,   # 估算跌停价
                    'paused': row.get('paused', False),                        # 停牌状态
                    'is_st': 'ST' in stock or '*' in stock,                     # ST状态
                    'name': row.get('name', stock),                             # 股票名称
                })()

        return result
    except Exception as e:
        # 出错时返回空字典
        return {}

# ========================================

# 克隆自聚宽文章：https://www.joinquant.com/post/19619
# 标题：【策略】夏普超过2.0，后市靠定增一步步往上爬
# 作者：安藤忠雄

# 导入函数库
# import jqdata  # 聚宽数据模块已移除
import pandas as pd
import numpy as np
from math import log as lg

MY_EXCLUDE_STOCKS = ["600656.XSHG","000594.XSHE"]
# 初始化函数，设定要操作的股票、基准等等
def initialize(context):
    # [已移除] set_option()  # Ptrade不支持此API
# 开启动态复权模式(真实价格)
    # [已移除] set_option()  # Ptrade不支持此API

    context.s1 = '000001.XSHG'
    context.small_cap_num = 50
    context.stop_index_drop = -0.03
    context.win_length = 126
    context.rel_return = -0.5 
    context.max_weight = 0.3 
    
    # schedule rebalance function/定时器
    run_daily(context, rebalance, time='14:21')
    
def before_trading_start(context): 
    fundamental_df = get_fundamentals(
        query(
            valuation.code
        ).order_by(
            valuation.market_cap.asc()
        ).limit(
            context.small_cap_num 
        )
    )
    context.stocks=np.array([i[0] for i in fundamental_df.values])
  
    
    
    context.stocks = remove_st(context.stocks) 
    
    
def remove_st(stocks):
    result = []
    for s in stocks:
        if ( not get_current_data_compat()[s].is_st) and (s not in MY_EXCLUDE_STOCKS):
            result.append(s)
    return np.array(result)

# 交易日志
def trade_log(stock, weight):
    log.info("[ %s(%s) -> %.2f]" % (stock_name(stock), stock, weight))

# 股票名字
def stock_name(stock):
    return get_stock_info(stock).display_name
    
def rebalance(context):
    bar_dict = get_current_data_compat()
    
    for stock in context.portfolio.positions.keys():
        if stock not in context.stocks:
            order_target_value(stock, 0); trade_log(stock, 0)#打印交易日志
    
    index_hist = attribute_history(context.s1,2, "1d", "close")
    index_return_1d = lg(index_hist['close'][1]/index_hist['close'][0]) 
    if index_return_1d < context.stop_index_drop: 
        for stock in context.stocks:
            order_target_value(stock, 0); trade_log(stock, 0)
        return
    
    a = context.stocks.tolist()
    a.append(context.s1)
    set_universe(a)
    stock_hist = get_history(context.win_length, "1d", "close")
    stock_return = (stock_hist.ix[context.win_length-1]-stock_hist.ix[0])/stock_hist.ix[0] 
    index_return = stock_return[context.s1] 
    rel_return = stock_return - index_return
    context.stocks = [stock for stock in context.stocks if not bar_dict[stock].paused 
    and get_history(stock, 1, '1m', 'volume', include_now=True)['volume']!=0
    and bar_dict[stock].day_open<1.095*stock_hist[stock].iloc[-1] 
    and bar_dict[stock].day_open>0.905*stock_hist[stock].iloc[-1] 
    and rel_return[stock]<abs(index_return)*context.rel_return]
    
    if len(context.stocks) == 0:
        return
    
    weight = {}
    sum_weight = 0 
    for stock in context.stocks:
        weight[stock] = abs((rel_return[stock]-abs(index_return)*context.rel_return)/(index_return) ) * context.max_weight # 超跌的相对情况 |(个股上证相对调幅-|上证调幅|*(-0.5))/(上证调幅)|*0.3
        if weight[stock] > context.max_weight: 
            weight[stock] = context.max_weight
        sum_weight += weight[stock]
        
    for stock in context.stocks:
        weight[stock] /= sum_weight # 归一化
        if weight[stock] > context.max_weight: # 单个股票仓位控制
            weight[stock] = context.max_weight
        trade_log(stock, weight[stock])
        value = context.portfolio.total_value * weight[stock]
        order_target_value(stock, value) 



