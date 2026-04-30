"""
聚宽转EasyXT转换器测试脚本
测试转换器的各项功能
"""

import sys
import os

# 添加父目录到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

from converters.jq_to_easyxt import JQToEasyXTConverter


def test_simple_strategy():
    """测试简单策略转换"""
    print("\n" + "="*80)
    print("测试1: 简单策略转换")
    print("="*80)

    jq_code = """
import jqdata

def initialize(context):
    g.security = '000001.XSHE'
    g.ma_short = 5
    g.ma_long = 20

def handle_data(context, data):
    # 获取历史数据
    hist = attribute_history(g.security, g.ma_long, '1d', ['close'], df=True)
    ma5 = hist['close'].tail(5).mean()
    ma20 = hist['close'].mean()

    # 获取当前价格
    current = get_current_data()
    price = current[g.security].last_price

    # 金叉买入
    if ma5 > ma20 and context.portfolio.available_cash > 0:
        order_value(g.security, context.portfolio.available_cash)
        log.info("金叉买入")

    # 死叉卖出
    elif ma5 < ma20:
        order_target(g.security, 0)
        log.info("死叉卖出")
"""

    converter = JQToEasyXTConverter(verbose=True)
    result = converter.convert(jq_code, output_file='test_output_simple.py')

    print("\n✅ 测试1完成！查看 test_output_simple.py")


def test_scheduled_functions():
    """测试定时任务函数转换"""
    print("\n" + "="*80)
    print("测试2: 定时任务函数转换")
    print("="*80)

    jq_code = """
import jqdata

def initialize(context):
    g.stock_pool = ['000001.XSHE', '000002.XSHE', '600000.XSHG']
    run_daily(check_entry, time='9:30')
    run_daily(check_exit, time='14:50')

def check_entry(context):
    for stock in g.stock_pool:
        hist = attribute_history(stock, 10, '1d', ['close'], df=True)
        if hist['close'].iloc[-1] > hist['close'].iloc[-2]:
            if context.portfolio.available_cash > 10000:
                order_value(stock, 10000)
                log.info(f"买入 {stock}")

def check_exit(context):
    positions = context.portfolio.positions
    for stock in positions:
        hist = attribute_history(stock, 5, '1d', ['close'], df=True)
        if hist['close'].iloc[-1] < hist['close'].mean():
            order_target(stock, 0)
            log.info(f"卖出 {stock}")

def handle_data(context, data):
    pass
"""

    converter = JQToEasyXTConverter(verbose=True)
    result = converter.convert(jq_code, output_file='test_output_scheduled.py')

    print("\n✅ 测试2完成！查看 test_output_scheduled.py")


def test_complex_strategy():
    """测试复杂策略转换"""
    print("\n" + "="*80)
    print("测试3: 复杂策略转换")
    print("="*80)

    jq_code = """
import jqdata
import pandas as pd
import numpy as np

def initialize(context):
    # 策略参数
    g.stock_num = 10
    g.rebalance_days = 5
    g.last_rebalance = None

    # 获取股票池
    g.stock_pool = get_stock_pool(context)

    # 设置基准
    set_benchmark('000300.XSHG')

    # 定时调仓
    run_weekly(rebalance, weekday=1, time='9:30')

def get_stock_pool(context):
    """获取股票池"""
    # 获取所有A股
    all_stocks = list(get_all_securities(['stock'], date=context.current_date).index)

    # 过滤条件
    pool = []
    for stock in all_stocks:
        hist = attribute_history(stock, 20, '1d', ['close', 'volume'], df=True)
        if len(hist) < 20:
            continue

        # 计算换手率
        turnover = hist['volume'].iloc[-1] / hist['volume'].mean()

        # 筛选换手率在合理范围
        if 0.5 < turnover < 3.0:
            pool.append(stock)

    return pool[:50]

def rebalance(context):
    """调仓函数"""
    # 检查是否需要调仓
    if g.last_rebalance and (context.current_date - g.last_rebalance).days < g.rebalance_days:
        return

    # 获取当前持仓
    positions = context.portfolio.positions
    current_stocks = [stock for stock in positions if positions[stock].total_amount > 0]

    # 选股
    target_stocks = select_stocks(context)

    # 卖出不在目标池的股票
    for stock in current_stocks:
        if stock not in target_stocks:
            order_target(stock, 0)
            log.info(f"卖出 {stock}")

    # 买入新股票
    if target_stocks:
        cash = context.portfolio.available_cash
        cash_per_stock = cash / len(target_stocks)

        for stock in target_stocks:
            if stock not in current_stocks:
                order_value(stock, cash_per_stock)
                log.info(f"买入 {stock}")

    g.last_rebalance = context.current_date

def select_stocks(context):
    """选股函数"""
    scores = []
    for stock in g.stock_pool:
        hist = attribute_history(stock, 20, '1d', ['close'], df=True)
        if len(hist) < 20:
            continue

        # 计算收益率
        ret = (hist['close'].iloc[-1] - hist['close'].iloc[0]) / hist['close'].iloc[0]
        scores.append((stock, ret))

    # 按收益率排序，选择前N只
    scores.sort(key=lambda x: x[1], reverse=True)
    return [stock for stock, _ in scores[:g.stock_num]]

def handle_data(context, data):
    pass
"""

    converter = JQToEasyXTConverter(verbose=True, account_id="test_account")
    result = converter.convert(jq_code, output_file='test_output_complex.py')

    print("\n✅ 测试3完成！查看 test_output_complex.py")


def run_all_tests():
    """运行所有测试"""
    print("\n" + "="*80)
    print("🚀 聚宽转EasyXT转换器 - 测试套件")
    print("="*80)

    tests = [
        test_simple_strategy,
        test_scheduled_functions,
        test_complex_strategy,
    ]

    for i, test_func in enumerate(tests, 1):
        try:
            test_func()
        except Exception as e:
            print(f"\n❌ 测试{i}失败: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "="*80)
    print("✅ 所有测试完成！")
    print("="*80)
    print("\n生成的测试文件：")
    print("  - test_output_simple.py")
    print("  - test_output_scheduled.py")
    print("  - test_output_complex.py")
    print("\n请检查生成的代码并根据TODO注释进行手动完善。")
    print("="*80)


if __name__ == "__main__":
    run_all_tests()
