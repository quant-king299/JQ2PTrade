"""
聚宽到Ptrade统一转换器 v5.2 (全面JQ→PTrade兼容)
更新说明:
- ✅ v5.2: 反向集成四大搅屎棍策略调试发现的所有问题
  - get_industry(): 改用tushare申万行业数据，PTrade代码格式(.SZ/.SS)
  - get_stock_info(): 返回嵌套dict，start_date→listed_date+日期解析
  - Position对象: 无.security属性，改用dict key
  - Order对象: 返回字符串ID，不是Order对象
  - get_fundamentals(): 无code列，股票代码是index；无market_cap字段
  - get_market_value(): 回测不可用
  - get_current_data_compat(): 使用真实涨跌停价格
  - get_price(): 移除skip_paused/fq参数
- ✅ v5.1: 修复query() ORM转换
  - 使用括号计数法替代正则，正确匹配含嵌套括号的完整表达式
  - 检测原始赋值变量，不再使用_query_result临时变量
  - 先初始化结果变量，避免else块吞没后续代码
  - 正保证留行缩进
- ✅ v5.0: 反向集成PTrade运行调试发现的所有问题
  - set_slippage(FixedSlippage(N)) → set_slippage(N)
  - set_order_cost(OrderCost(...)) → 移除
  - enable_profile() → 移除
  - run_daily/run_weekly 位置参数格式修正
  - get_price 移除 panel/fill_paused 参数
  - get_price/get_index_stocks 日期 datetime.date → 字符串
  - get_history JQ参数名转PTrade格式
  - query() ORM自动转PTrade get_fundamentals
  - indicator表映射到profit_ability
  - .has_key() → in 操作符
  - context.subportfolios[0] → context.portfolio
  - get_current_data_compat() 无参调用修复
  - h.time → h.index (get_price返回格式)
- ✅ v4.0: 自动检测缺失的JQ函数，注入Tushare替代实现
- ✅ v3.x: 基础API映射、datetime导入、辅助函数
"""
import re
import ast
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
from enum import Enum


class StrategyType(Enum):
    """策略类型枚举"""
    BACKTEST = "backtest"
    LIVE = "live"
    HYBRID = "hybrid"


class APIUsage(Enum):
    """API使用情况"""
    FUNDAMENTALS = "fundamentals"
    FACTORS = "factors"
    TECHNICAL = "technical"
    BASIC = "basic"
    REALTIME = "realtime"


class JQToPtradeUnifiedConverter:
    """聚宽到Ptrade统一转换器 v3.10

    基于实际Ptrade运行验证和Demo策略分析
    修复: get_Ashares返回值处理、get_factor_values兼容、完善get_fundamentals转换
    """

    def __init__(self, verbose: bool = True, convert_security_codes: bool = False,
                 tushare_token: str = ''):
        """
        初始化转换器

        Args:
            verbose: 是否显示详细转换信息
            convert_security_codes: 是否转换证券代码后缀（.XSHG->.SS, .XSHE->.SZ）
                                     默认False，因为Ptrade向后兼容.XSHG/.XSHE格式
            tushare_token: Tushare API token，用于注入数据函数
        """
        self.verbose = verbose
        self.convert_security_codes = convert_security_codes
        self.tushare_token = tushare_token
        self.conversion_report = {
            'warnings': [],
            'errors': [],
            'changes': [],
            'api_mappings': [],
            'added_functions': []
        }

        # API映射规则 (基于实际Ptrade运行验证)
        self.api_mapping = {
            'get_price': 'get_price',
            'get_history': 'get_history',
            'get_bars': 'get_history',
            'history': 'get_history',  # 聚宽的history()函数
            'get_current_data': 'get_snapshot',
            'get_all_securities': 'get_Ashares',
            'get_security_info': 'get_stock_info',
            'get_index_stocks': 'get_index_stocks',
            'get_industry_stocks': 'get_industry_stocks',
            'get_portfolio': 'get_portfolio',
            'get_positions': 'get_positions',
            'get_orders': 'get_orders',
            'order': 'order',
            'order_value': 'order_value',
            'order_target': 'order_target',
            'order_target_value': 'order_target_value',
            'cancel_order': 'cancel_order',
            'log': 'log',
            'record': 'record',
            'set_benchmark': 'set_benchmark',
            # 注意：定时函数不添加context前缀，保持全局函数调用
            # run_daily, run_weekly, run_monthly 在_convert_timing_functions中单独处理
        }

        # 特殊处理函数
        self.special_apis = {
            'get_current_data': self._handle_get_current_data,
            'get_factor_values': self._handle_get_factor_values,
            'get_fundamentals': self._handle_get_fundamentals,
            'query': self._handle_query,
            'get_Ashares': self._handle_get_Ashares,
        }

        # 不支持的API（直接移除的）
        self.unsupported_apis = {
            'log.set_level',
            'set_commission',
            'set_price_limit',
            'set_order_cost',
            'set_option',
            'enable_profile',
        }

    def convert(self, jq_code: str, strategy_type: Optional[StrategyType] = None) -> str:
        """转换聚宽代码为Ptrade代码"""
        self.conversion_report = {
            'warnings': [],
            'errors': [],
            'changes': [],
            'api_mappings': [],
            'added_functions': []
        }

        if self.verbose:
            print("=" * 60)
            print("聚宽到Ptrade统一转换器 v5.1 (全面JQ→PTrade兼容)")
            print("反向集成PTrade运行调试发现的所有问题")
            print("=" * 60)

        # 1. 分析代码
        code_analysis = self._analyze_code(jq_code)

        # 2. 确定策略类型
        if strategy_type is None:
            strategy_type = self._detect_strategy_type(code_analysis)

        if self.verbose:
            print(f"\n[OK] 检测到策略类型: {strategy_type.value}")
            print(f"[OK] 使用的API类型: {', '.join([api.value for api in code_analysis['api_usage']])}")

        # 3. 执行转换
        converted_code = self._perform_conversion(jq_code, code_analysis, strategy_type)

        # 4. 后处理
        converted_code = self._post_process(converted_code, strategy_type)

        # 5. 添加辅助函数
        converted_code = self._add_helper_functions(converted_code, code_analysis)

        # 6. 生成报告
        if self.verbose:
            self._print_report()

        return converted_code

    def _analyze_code(self, code: str) -> Dict:
        """分析代码特征"""
        analysis = {
            'api_usage': set(),
            'functions': [],
            'uses_realtime_data': False,
            'uses_historical_data': False,
            'uses_fundamentals': False,
            'uses_factors': False,
            'uses_monthly_timing': False,
            'uses_current_data': False,
            'needs_tushare_inject': [],  # 需要注入的 Tushare 函数
        }

        # 检测API使用
        api_patterns = {
            APIUsage.FUNDAMENTALS: [
                r'get_fundamentals\s*\(',
                r'query\s*\(',
            ],
            APIUsage.FACTORS: [
                r'get_factor_values\s*\(',
                r'from\s+jqfactor\s+import',
            ],
            APIUsage.TECHNICAL: [
                r'MACD\s*\(',
                r'RSI\s*\(',
            ],
            APIUsage.BASIC: [
                r'get_price\s*\(',
                r'get_history\s*\(',
                r'attribute_history\s*\(',
            ],
            APIUsage.REALTIME: [
                r'get_current_data\s*\(',
            ],
        }

        for api_type, patterns in api_patterns.items():
            for pattern in patterns:
                if re.search(pattern, code):
                    analysis['api_usage'].add(api_type)
                    if api_type == APIUsage.FUNDAMENTALS:
                        analysis['uses_fundamentals'] = True
                    elif api_type == APIUsage.FACTORS:
                        analysis['uses_factors'] = True
                    elif api_type == APIUsage.REALTIME:
                        analysis['uses_realtime_data'] = True
                        analysis['uses_current_data'] = True
                    elif api_type == APIUsage.BASIC:
                        analysis['uses_historical_data'] = True

        # 检测 run_monthly 使用
        if re.search(r'run_monthly\s*\(', code):
            analysis['uses_monthly_timing'] = True

        # 提取函数定义
        function_pattern = r'def\s+(\w+)\s*\([^)]*\)\s*:'
        analysis['functions'] = re.findall(function_pattern, code)

        # ===== v4.0: 检测需要 Tushare 替代的 JQ 函数 =====
        tushare_detect_patterns = {
            'get_extras': r'\bget_extras\s*\(',
            'get_concept_stocks': r'\bget_concept_stocks\s*\(',
            'get_concepts': r'\bget_concepts\s*\(',
            'get_industry': r'\bget_industry\s*\(',
            'get_fundamentals_continuously': r'\bget_fundamentals_continuously\s*\(',
            'get_factor_values': r'\bget_factor_values\s*\(',
            'order_target_percent': r'\border_target_percent\s*\(',
            'get_current_data': r'\bget_current_data\s*\(',
            'check_limit_up': r'\bcheck_limit_up\s*\(',
            'normalize_code': r'\bnormalize_code\s*\(',
            'get_all_securities': r'\bget_all_securities\s*\(',
        }
        for func_name, pattern in tushare_detect_patterns.items():
            # 排除策略文件中已自行定义的函数
            func_defined = re.search(rf'^def\s+{func_name}\s*\(', code, re.MULTILINE)
            func_called = re.search(pattern, code)
            if func_called and not func_defined:
                analysis['needs_tushare_inject'].append(func_name)

        return analysis

    def _detect_strategy_type(self, code_analysis: Dict) -> StrategyType:
        """检测策略类型"""
        if code_analysis['uses_realtime_data']:
            if code_analysis['uses_historical_data']:
                return StrategyType.HYBRID
            return StrategyType.LIVE

        function_names = ' '.join(code_analysis['functions'])
        if any(keyword in function_names for keyword in ['check_limit_up', 'realtime', 'live']):
            return StrategyType.LIVE

        return StrategyType.BACKTEST

    def _perform_conversion(self, code: str, analysis: Dict, strategy_type: StrategyType) -> str:
        """执行转换"""
        result = code

        # 1. 处理定时任务（必须在常规API映射之前，因为run_monthly需要特殊处理）
        result = self._convert_timing_functions(result, analysis)

        # 2. 处理特殊API（注意：get_Ashares处理在API映射之后进行）
        for api_name, handler in self.special_apis.items():
            if api_name == 'get_Ashares':
                continue
            if re.search(rf'{api_name}\s*\(', result):
                result = handler(result, strategy_type)

        # 3. 常规API映射
        for jq_api, ptrade_api in self.api_mapping.items():
            if jq_api in ['run_daily', 'run_weekly', 'run_monthly']:
                continue
            pattern = rf'\b{re.escape(jq_api)}\b'
            if re.search(pattern, result):
                result = re.sub(pattern, ptrade_api, result)
                self.conversion_report['api_mappings'].append(f'{jq_api} → {ptrade_api}')

        # 3.5. 处理get_Ashares的日期格式
        result = self._handle_get_Ashares(result, strategy_type)

        # 4. 处理全局变量
        result = self._convert_global_variable(result)

        # 5. 标准化证券代码
        result = self._standardize_security_codes(result)

        # 6. 移除/转换不支持的API (v5.0: 增强处理)
        result = self._remove_unsupported_apis(result)

        # 7. v5.0: 转换set_slippage(FixedSlippage(N)) → set_slippage(N)
        result = self._convert_set_slippage(result)

        # 8. v5.0: 转换JQ get_history参数名到PTrade格式
        result = self._convert_get_history(result)

        # 9. v5.0: 移除get_price的panel/fill_paused参数 + 修复日期格式
        result = self._convert_get_price(result)

        # 10. v5.0: 修复日期参数 (datetime.date → 字符串)
        result = self._fix_date_args(result)

        # 11. v5.0: 转换context.subportfolios[0] → context.portfolio
        result = self._convert_subportfolios(result)

        # 12. v5.0: 转换.has_key() → in
        result = self._convert_has_key(result)

        # 13. v5.0: 修复get_current_data_compat()无参调用
        result = self._fix_get_current_data_calls(result)

        # 14. v5.0: 转换query() ORM到PTrade get_fundamentals
        result = self._convert_query_orm(result)

        # 15. v5.0: 表名映射 indicator → profit_ability
        result = self._map_table_names(result)

        # 16. 转换技术指标
        result = self._convert_technical_indicators(result)

        # 17. 转换其他杂项
        result = self._convert_misc_issues(result)

        # 18. v5.1: get_stock_info()返回dict，属性访问→字典访问
        result = self._convert_stock_info_attr(result)

        # 19. v5.2: Position对象没有.security属性
        result = self._convert_position_access(result)

        # 20. v5.2: Order返回字符串ID，不是对象
        result = self._convert_order_checks(result)

        # 21. v5.2: get_fundamentals返回DataFrame无code列
        result = self._convert_get_fundamentals_code_field(result)

        # 22. v5.2: get_market_value回测不可用
        result = self._convert_market_value(result)

        # 23. v5.2: 移除get_price不支持的参数
        result = self._convert_get_price_params(result)

        # 24. v5.2: close_position(position) → close_position(stock)
        result = self._convert_close_position(result)

        return result

    def _handle_get_current_data(self, code: str, strategy_type: StrategyType) -> str:
        """
        智能处理get_current_data调用
        聚宽的get_current_data()返回当前快照数据，Ptrade用get_snapshot()
        但需要根据使用方式提供不同的替代方案
        """
        # 检测get_current_data的使用模式
        usage_patterns = [
            (r'get_current_data\(\)\[([^\]]+)\]\.last_price', 'last_price'),
            (r'get_current_data\(\)\[([^\]]+)\]\.paused', 'paused'),
            (r'get_current_data\(\)\[([^\]]+)\]\.is_st', 'is_st'),
            (r'get_current_data\(\)', 'general'),
        ]

        has_get_current_data = False
        for pattern, usage_type in usage_patterns:
            if re.search(pattern, code):
                has_get_current_data = True
                break

        if not has_get_current_data:
            return code

        self.conversion_report['warnings'].append(
            "检测到get_current_data使用。已转换为get_snapshot并添加兼容函数。"
        )

        # 替换get_current_data为get_snapshot
        code = re.sub(r'\bget_current_data\s*\(\)', 'get_snapshot()', code)

        # 添加get_current_data兼容函数（如果不存在）
        if 'def get_current_data_compat' not in code:
            compat_function = '''
# get_current_data兼容函数 - 替代聚宽的get_current_data()
def get_current_data_compat(security_list=None):
    """模拟聚宽get_current_data()功能，使用get_history批量获取（PTrade兼容）"""
    import pandas as pd

    if security_list is None or not security_list:
        return {}

    result = {}

    # 批量获取收盘价和是否开盘
    try:
        df = get_history(1, '1d', ['close', 'is_open', 'high_limit', 'low_limit'], security_list=security_list, include=True, fill='nan')

        if df is not None and not df.empty and 'code' in df.columns:
            for code, group in df.groupby('code'):
                try:
                    close_price = float(group['close'].iloc[-1])
                    is_open = float(group['is_open'].iloc[-1]) if 'is_open' in group.columns else 1
                    high_limit = float(group['high_limit'].iloc[-1]) if 'high_limit' in group.columns else 0
                    low_limit = float(group['low_limit'].iloc[-1]) if 'low_limit' in group.columns else 0
                except Exception:
                    close_price = 0
                    is_open = 1
                    high_limit = 0
                    low_limit = 0

                result[code] = type('obj', (), {
                    'last_price': close_price,
                    'high_limit': high_limit if high_limit > 0 else round(close_price * 1.1, 2),
                    'low_limit': low_limit if low_limit > 0 else round(close_price * 0.9, 2),
                    'paused': (is_open == 0),
                    'is_st': False,
                    'name': code,
                })()
    except Exception:
        pass

    # 对没获取到数据的股票，补充默认值（确保不返回空dict）
    for stock in security_list:
        if stock not in result:
            result[stock] = type('obj', (), {
                'last_price': 0,
                'high_limit': 0,
                'low_limit': 0,
                'paused': False,
                'is_st': False,
                'name': stock,
            })()

    return result

# ========================================

'''
            # 在文件开头添加
            if compat_function not in code:
                code = compat_function + code

            # 替换get_snapshot()调用为get_current_data_compat()
            # 只替换用户代码区域，不替换compat_function内部
            lines = code.split('\n')
            compat_function_end = -1

            # 找到compat_function的结束位置
            for i, line in enumerate(lines):
                if line.strip() == '# ========================================' and i > 10:
                    compat_function_end = i
                    break

            if compat_function_end > 0:
                # 只替换用户代码部分
                user_code = '\n'.join(lines[compat_function_end+1:])
                user_code = re.sub(r'\bget_snapshot\s*\(\)', 'get_current_data_compat()', user_code)
                code = '\n'.join(lines[:compat_function_end+1]) + '\n' + user_code
            else:
                # 没有找到，直接替换
                code = re.sub(r'\bget_snapshot\s*\(\)', 'get_current_data_compat()', code)

        return code

    def _handle_get_factor_values(self, code: str, strategy_type: StrategyType) -> str:
        """处理get_factor_values - 添加Ptrade兼容实现

        聚宽: from jqfactor import get_factor_values (内置函数)
        Ptrade: 需要自己实现（基于get_fundamentals封装）
        """
        if not re.search(r'get_factor_values\s*\(', code):
            return code

        # 检测是否导入了jqfactor
        has_jqfactor_import = 'from jqfactor import' in code or 'import jqfactor' in code

        if has_jqfactor_import:
            # 添加Ptrade版本的get_factor_values实现
            compat_function = '''
# ======================================================================
# get_factor_values 兼容函数（Ptrade版本）
# ======================================================================
# 聚宽的get_factor_values是内置函数，Ptrade需要自己实现
# 这里基于get_fundamentals封装，提供基本的因子数据获取功能
#
# 使用方法:
#   df = get_factor_values(stocks, 'sales_growth', end_date=date, count=1)
#
# 注意: 因子名称需要映射到Ptrade的表和字段
# ======================================================================

def get_factor_values(stock_list, factor, end_date=None, count=1, **kwargs):
    """
    获取因子值（Ptrade兼容版本）

    参数:
        stock_list: 股票列表
        factor: 因子名称或因子列表
        end_date: 结束日期
        count: 数据条数（暂未实现，默认为1）
        **kwargs: 其他参数

    返回:
        DataFrame格式的因子数据
    """
    import pandas as pd

    # 因子名称映射：聚宽因子名 -> (Ptrade表名, Ptrade字段名)
    factor_mapping = {
        # 估值因子
        'market_cap': ('valuation', 'total_value'),
        'circulating_market_cap': ('valuation', 'a_floats'),
        'pe_ratio': ('valuation', 'pe_ratio'),
        'pb_ratio': ('valuation', 'pb_ratio'),
        'ps_ratio': ('valuation', 'ps_ratio'),

        # 财务指标因子
        'roe': ('indicator', 'roe'),
        'roa': ('indicator', 'roa'),
        'eps': ('indicator', 'eps'),
        'inc_revenue_year_on_year': ('indicator', 'operating_revenue_grow_rate'),
        'inc_net_profit_year_on_year': ('indicator', 'np_parent_company_cut_yoy'),
        'operating_revenue_grow_rate': ('indicator', 'operating_revenue_grow_rate'),

        # 增长因子
        'sales_growth': ('indicator', 'operating_revenue_grow_rate'),
        'net_profit_growth_rate': ('indicator', 'net_profit_growth_rate'),
        'total_profit_growth_rate': ('indicator', 'total_profit_growth_rate'),

        # 质量因子
        'total_shareholder_equity': ('balance_statement', 'total_shareholder_equity'),
    }

    # 处理单个因子
    if isinstance(factor, str):
        if factor not in factor_mapping:
            # 未映射的因子，尝试直接使用
            table = 'valuation'
            field = factor
        else:
            table, field = factor_mapping[factor]

        # 获取数据
        try:
            df = get_fundamentals(
                stock_list,
                table,
                fields=field,
                date=end_date,
                is_dataframe=True
            )

            if df is not None and not df.empty:
                # 重命名列
                df = df.rename(columns={field: factor})
                return df
            else:
                return pd.DataFrame()

        except Exception as e:
            print(f"获取因子{factor}失败: {e}")
            return pd.DataFrame()

    # 处理多个因子
    elif isinstance(factor, list):
        dfs = []
        for f in factor:
            df_f = get_factor_values(stock_list, f, end_date=end_date, count=count, **kwargs)
            if not df_f.empty:
                dfs.append(df_f)

        if dfs:
            # 合并所有因子的DataFrame
            result = pd.concat(dfs, axis=1)
            return result
        else:
            return pd.DataFrame()

    return pd.DataFrame()

'''

            # 移除jqfactor导入
            code = re.sub(r'from jqfactor import.*\n?', '', code)
            code = re.sub(r'import jqfactor.*\n?', '', code)

            # 添加兼容函数
            code = compat_function + '\n' + code

            self.conversion_report['changes'].append(
                'get_factor_values: 已添加Ptrade兼容实现（基于get_fundamentals封装）'
            )

            self.conversion_report['warnings'].append(
                "get_factor_values已添加兼容实现，但因子名称可能需要手动调整映射关系"
            )
        else:
            # 没有导入jqfactor，可能只是调用
            self.conversion_report['warnings'].append(
                "检测到get_factor_values使用，但未发现jqfactor导入。请确认是否需要添加兼容实现。"
            )

        return code

    def _handle_get_fundamentals(self, code: str, strategy_type: StrategyType) -> str:
        """处理get_fundamentals - 智能转换或添加转换指南

        聚宽: get_fundamentals(query_object)
        Ptrade: get_fundamentals(stock_list, "table", fields=[...], date=..., is_dataframe=False)
        """
        if not re.search(r'get_fundamentals\s*\(', code):
            return code

        conversion_guide = '''
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

'''

        lines = code.split('\n')
        result = []
        converted_count = 0

        i = 0
        while i < len(lines):
            line = lines[i]

            # 尝试自动转换get_fundamentals调用
            # 模式1: get_fundamentals(query_var)
            match = re.search(r'get_fundamentals\s*\(\s*(\w+)\s*\)', line)
            if match:
                query_var = match.group(1)

                # 向上查找query定义
                query_info = self._find_and_analyze_query(lines, i, query_var)

                if query_info and self._is_simple_query(query_info):
                    # 可以自动转换
                    ptrade_call = self._build_ptrade_fundamentals_call(query_info)
                    # 替换原调用
                    new_line = re.sub(
                        r'get_fundamentals\s*\([^)]*\)',
                        ptrade_call,
                        line
                    )
                    result.append(new_line + '  # [已转换为Ptrade格式]')
                    converted_count += 1
                    self.conversion_report['changes'].append(
                        f"自动转换get_fundamentals: table={query_info['table']}, fields={query_info['fields']}"
                    )
                else:
                    # 复杂query，无法自动转换
                    result.append(line)
                    if query_info:
                        result.append(f'    # ⚠️ 复杂query无法自动转换，需要手动调整')
                        result.append(f'    # 检测到表: {query_info.get("table", "?")}, 字段: {query_info.get("fields", "?")}')
                    else:
                        result.append(f'    # ⚠️ 无法分析query对象，请手动转换为Ptrade格式')
            else:
                result.append(line)

            i += 1

        # 添加转换信息
        if converted_count > 0:
            self.conversion_report['changes'].append(
                f"get_fundamentals: 已自动转换{converted_count}处简单调用"
            )
            code = conversion_guide + '\n'.join(result)
        else:
            # 没有自动转换，添加警告
            self.conversion_report['warnings'].append(
                "检测到get_fundamentals使用，但无法自动转换复杂query，请手动调整"
            )
            code = conversion_guide + code

        return code

    def _find_and_analyze_query(self, lines: list, current_idx: int, query_var: str) -> dict:
        """查找并分析query对象定义"""
        # 向上查找query定义
        for i in range(current_idx - 1, max(0, current_idx - 50), -1):
            line = lines[i]

            # 匹配 query_var = query(
            match = re.match(rf'{query_var}\s*=\s*query\s*\(', line)
            if match:
                # 收集完整query定义
                query_lines = [line]
                j = i + 1
                bracket_count = line.count('(') - line.count(')')

                while j < len(lines) and bracket_count > 0:
                    query_lines.append(lines[j])
                    bracket_count += lines[j].count('(') - lines[j].count(')')
                    j += 1

                query_code = '\n'.join(query_lines)
                return self._parse_simple_query(query_code)

        return None

    def _parse_simple_query(self, query_code: str) -> dict:
        """解析简单query对象

        返回: {
            'table': 'valuation',
            'fields': ['code', 'market_cap'],
            'stock_list_var': 'stock_list',
            'is_simple': bool
        }
        """
        info = {
            'table': None,
            'fields': [],
            'stock_list_var': None,
            'filters': [],
            'order_by': None,
            'limit': None,
            'is_simple': False
        }

        # 提取表和字段
        # 模式: query(valuation.code, valuation.field)
        table_field_pattern = r'query\s*\(\s*([a-z_]+)\.([a-z_]+)(?:\s*,\s*([a-z_]+)\.([a-z_]+))*'
        match = re.search(table_field_pattern, query_code)
        if match:
            info['table'] = match.group(1)
            info['fields'].append(match.group(2))
            # 检查是否有更多字段
            for m in re.finditer(r'([a-z_]+)\.([a-z_]+)', query_code):
                if m.group(1) == info['table']:
                    field = m.group(2)
                    if field not in info['fields']:
                        info['fields'].append(field)

        # 提取filter中的stock_list
        # 模式: valuation.code.in_(stock_list)
        stock_pattern = r'valuation\.code\.in_\s*\(\s*(\w+)'
        stock_match = re.search(stock_pattern, query_code)
        if stock_match:
            info['stock_list_var'] = stock_match.group(1)
        else:
            # 尝试其他模式
            stock_pattern2 = r'\.in_\s*\(\s*(\w+)'
            stock_match2 = re.search(stock_pattern2, query_code)
            if stock_match2:
                info['stock_list_var'] = stock_match2.group(1)

        # 检测复杂特性
        if '.order_by(' in query_code:
            info['order_by'] = True
        if '.limit(' in query_code:
            info['limit'] = True
        if '>.<' in query_code or '<.>' in query_code or '==' in query_code:
            info['filters'].append('complex')

        # 判断是否简单（只有基本字段查询和stock_list过滤）
        info['is_simple'] = (
            info['table'] is not None and
            len(info['fields']) > 0 and
            info['order_by'] is None and
            info['limit'] is None and
            len(info['filters']) == 0
        )

        return info

    def _is_simple_query(self, query_info: dict) -> bool:
        """判断是否是简单query（可以自动转换）"""
        return query_info and query_info.get('is_simple', False)

    def _build_ptrade_fundamentals_call(self, query_info: dict) -> str:
        """构建Ptrade格式的get_fundamentals调用

        Ptrade格式: get_fundamentals(stock_list, "table", fields=..., date=..., is_dataframe=False)
        """
        table = query_info.get('table', 'valuation')
        fields = query_info.get('fields', ['code'])
        stock_list_var = query_info.get('stock_list_var', 'stock_list')

        # 如果没有找到stock_list，使用context.portfolio.positions或提示
        if not stock_list_var:
            stock_list_var = 'context.portfolio.positions.keys()'

        # 构建fields参数
        # 单字段用字符串，多字段用列表
        if len(fields) == 1:
            fields_str = f"'{fields[0]}'"
        else:
            fields_str = str(fields)

        # 构建完整调用
        # 默认添加is_dataframe=True（因为大多数情况下需要DataFrame格式）
        return f'get_fundamentals(list({stock_list_var}), "{table}", fields={fields_str}, date=context.previous_date, is_dataframe=True)'


    def _handle_query(self, code: str, strategy_type: StrategyType) -> str:
        """处理query对象"""
        if not re.search(r'\bquery\s*\(', code):
            return code
        return code

    def _handle_get_Ashares(self, code: str, strategy_type: StrategyType) -> str:
        """处理get_all_securities/get_Ashares - 移除date参数和.index.tolist()

        聚宽: get_all_securities(date=xxx).index.tolist()
        Ptrade: get_Ashares() 或 list(get_Ashares())
        """
        # 注意：此函数在API映射之前被调用，所以代码中还是get_all_securities
        # 需要同时处理get_all_securities和get_Ashares两种情况

        # Ptrade的get_Ashares()不接受任何参数，直接返回所有A股列表
        # 返回值可能是列表或DataFrame，需要处理.index.tolist()

        # 第一步：移除date参数
        # 处理get_all_securities(date=xxx) → get_all_securities()
        pattern_all = r'get_all_securities\s*\(\s*date\s*=\s*[^)]*\)'
        code = re.sub(pattern_all, 'get_all_securities()', code)

        # 处理get_Ashares(date=xxx) → get_Ashares()
        pattern_ashares = r'get_Ashares\s*\(\s*date\s*=\s*[^)]*\)'
        code = re.sub(pattern_ashares, 'get_Ashares()', code)

        # 处理get_Ashares(_format_date(xxx)) → get_Ashares()
        pattern_ashares_format = r'get_Ashares\s*\(\s*_format_date\s*\([^)]*\)\s*\)'
        code = re.sub(pattern_ashares_format, 'get_Ashares()', code)

        # 第二步：处理.index.tolist()
        # Ptrade的get_Ashares()可能返回列表，不需要.index.tolist()
        # 模式1: get_all_securities().index.tolist() → list(get_all_securities())
        pattern_all_index = r'get_all_securities\(\)\.index\.tolist\(\)'
        code = re.sub(pattern_all_index, 'list(get_all_securities())', code)

        # 模式2: get_Ashares().index.tolist() → list(get_Ashares())
        pattern_ashares_index = r'get_Ashares\(\)\.index\.tolist\(\)'
        code = re.sub(pattern_ashares_index, 'list(get_Ashares())', code)

        # 模式3: xxx = get_Ashares().index → xxx = list(get_Ashares())
        pattern_index = r'(\w+)\s*=\s*get_(?:all_securities|Ashares)\(\)\.index'
        code = re.sub(pattern_index, r'\1 = list(get_Ashares())', code)

        if re.search(r'get_all_securities\(\)|get_Ashares\(\)', code):
            self.conversion_report['changes'].append(
                "get_all_securities()已转换为get_Ashares()，移除date参数，处理.index.tolist()为list()包装"
            )

        return code

    def _convert_timing_functions(self, code: str, analysis: Dict) -> str:
        """转换定时任务函数

        基于实际Ptrade运行验证(2026-03-02 12:00):
        - Ptrade只支持 run_daily(context, func, time='...')
        - 不支持 run_weekly() 和 run_monthly()
        - 必须将所有定时函数转换为run_daily()
        - 在函数内部添加日期检查逻辑
        - JQ支持位置参数: run_weekly(func, 1, '9:30') 和关键字参数: run_weekly(func, weekday=1, time='9:30')
        """

        # 收集需要特殊处理的函数（用于添加日期检查）
        weekly_functions = []
        monthly_functions = []

        # 处理run_weekly - 转换为run_daily
        def replace_run_weekly(match):
            func_name = match.group(1).strip()
            weekday = match.group(2) if match.group(2) else '1'
            time_str = match.group(3).strip() if match.group(3) else "'09:30'"

            # 如果time_str没有引号，加上引号
            if time_str and not time_str.startswith("'") and not time_str.startswith('"'):
                time_str = f"'{time_str}'"

            weekly_functions.append({'name': func_name, 'weekday': weekday})
            return f'run_daily(context, {func_name}, time={time_str})'

        # 同时匹配关键字参数和位置参数格式
        run_weekly_patterns = [
            # 关键字参数: run_weekly(func, weekday=1, time='9:30')
            r'run_weekly\s*\(\s*([^,]+),\s*weekday\s*=\s*(\d+)(?:,\s*time\s*=\s*([^,\)]+))?(?:,\s*reference_security\s*=\s*[^)]+)?\s*\)',
            # 位置参数: run_weekly(func, 1, '9:30')
            r"run_weekly\s*\(\s*([^,]+)\s*,\s*(\d+)\s*(?:,\s*(['\"][^'\"]*['\"]|[^\)]+?))?\s*\)",
        ]

        for pattern in run_weekly_patterns:
            if re.search(pattern, code):
                code = re.sub(pattern, replace_run_weekly, code)
                self.conversion_report['changes'].append(
                    "run_weekly → run_daily (需要在函数内检查weekday)"
                )
                break

        # 处理run_monthly - 转换为run_daily
        def replace_run_monthly(match):
            func_name = match.group(1).strip() if match.group(1) else ""
            monthday = match.group(2) if match.group(2) else "1"
            time_str = match.group(3).strip() if match.group(3) else "'09:30'"

            if time_str and not time_str.startswith("'") and not time_str.startswith('"'):
                time_str = f"'{time_str}'"

            if func_name:
                monthly_functions.append({'name': func_name, 'day': monthday})
            return f'run_daily(context, {func_name}, time={time_str})'

        run_monthly_patterns = [
            # 关键字参数: run_monthly(func, monthday=1, time='9:30')
            r'run_monthly\s*\(\s*([^,\s]+)\s*(?:,\s*monthday\s*=\s*(\d+))?(?:,\s*time\s*=\s*([^,\)]+))?(?:,\s*reference_security\s*=\s*[^)]+)?\s*\)',
            # 位置参数: run_monthly(func, 1, '9:30')
            r"run_monthly\s*\(\s*([^,\s]+)\s*(?:,\s*(\d+)\s*)?(?:,\s*(['\"][^'\"]*['\"]|[^\)]+?))?\s*\)",
        ]

        for pattern in run_monthly_patterns:
            if re.search(pattern, code):
                code = re.sub(pattern, replace_run_monthly, code)
                self.conversion_report['changes'].append(
                    "run_monthly → run_daily (需要在函数内检查day of month)"
                )
                break

        # 处理run_daily - 添加context参数
        def replace_run_daily(match):
            func_name = match.group(1).strip()
            time_str = match.group(2).strip() if match.group(2) else "'9:30'"

            if time_str and not time_str.startswith("'") and not time_str.startswith('"'):
                time_str = f"'{time_str}'"

            return f'run_daily(context, {func_name}, time={time_str})'

        run_daily_patterns = [
            # 关键字参数: run_daily(func, time='9:05') — 跳过已有context的已转换调用
            r"run_daily\s*\(\s*(?!context\b)([^,\s]+)\s*(?:,\s*time\s*=\s*([^,\)]+))?(?:,\s*reference_security\s*=\s*[^)]+)?\s*\)",
            # 位置参数: run_daily(func, '9:05') — 跳过已有context的已转换调用
            r"run_daily\s*\(\s*(?!context\b)([^,\s]+)\s*,\s*(['\"][^'\"]*['\"]|[^\)]+?)\s*\)",
        ]

        for pattern in run_daily_patterns:
            if re.search(pattern, code):
                code = re.sub(pattern, replace_run_daily, code)
                self.conversion_report['changes'].append(
                    "run_daily - 添加context参数，移除reference_security"
                )
                break

        # 为需要星期/月份检查的函数添加检查逻辑
        code = self._add_timing_checks(code, weekly_functions, monthly_functions)

        return code

    def _add_timing_checks(self, code: str, weekly_functions: list, monthly_functions: list) -> str:
        """为函数添加日期检查逻辑"""
        if not weekly_functions and not monthly_functions:
            return code

        lines = code.split('\n')
        result_lines = []
        i = 0

        while i < len(lines):
            line = lines[i]
            result_lines.append(line)

            # 检查是否是需要添加检查的函数定义
            for func_info in weekly_functions:
                func_name = func_info['name']
                weekday = func_info['weekday']

                # 匹配函数定义行
                if re.match(rf'\s*def\s+{re.escape(func_name)}\s*\(', line):
                    # 在函数定义后添加星期检查
                    result_lines.append('')
                    result_lines.append('    # ========== run_weekly转换 ==========')
                    result_lines.append(f'    # 只在星期{weekday}执行')
                    result_lines.append(f"    if context.current_dt.weekday() + 1 != {weekday}:")
                    result_lines.append('        return  # 今天不是目标星期，直接返回')
                    result_lines.append('    # =====================================')
                    result_lines.append('')
                    break

            for func_info in monthly_functions:
                func_name = func_info['name']
                day = func_info['day']

                # 匹配函数定义行
                if re.match(rf'\s*def\s+{re.escape(func_name)}\s*\(', line):
                    # 在函数定义后添加月份检查
                    result_lines.append('')
                    result_lines.append('    # ========== run_monthly转换 ==========')
                    result_lines.append(f'    # 只在每月第{day}日执行')
                    result_lines.append(f'    if context.current_dt.day != {day}:')
                    result_lines.append('        return  # 今天不是目标日期，直接返回')
                    result_lines.append('    # =====================================')
                    result_lines.append('')
                    break

            i += 1

        return '\n'.join(result_lines)


    def _convert_global_variable(self, code: str) -> str:
        """转换全局变量"""
        code = re.sub(r'\bg\.', 'context.', code)
        code = re.sub(r'context\.(info|debug|warn|error)\s*\(', r'log.\1(', code)
        return code

    def _standardize_security_codes(self, code: str) -> str:
        """标准化证券代码（可选转换）

        基于Ptrade Demo分析：
        - Ptrade同时支持.XSHG/.XSHE（聚宽格式）和.SS/.SZ（新版格式）
        - 默认不转换，保持聚宽格式
        - 用户可以通过convert_security_codes=True参数启用转换
        """
        if self.convert_security_codes:
            # 转换为.SS/.SZ格式
            code = re.sub(r'\.XSHG\b', '.SS', code)
            code = re.sub(r'\.XSHE\b', '.SZ', code)
            if 'security_codes' not in self.conversion_report['changes']:
                self.conversion_report['changes'].append('证券代码后缀: .XSHG->.SS, .XSHE->.SZ')
        else:
            # 不转换，保持聚宽格式
            if 'security_codes' not in self.conversion_report['changes']:
                self.conversion_report['changes'].append('证券代码后缀: 保持原样（.XSHG/.XSHE）')
        return code

    def _remove_unsupported_apis(self, code: str) -> str:
        """移除不支持的API（支持多行参数）"""
        for api in self.unsupported_apis:
            # 匹配单行和多行的API调用
            pattern = rf'{re.escape(api)}\s*\(.*?\)\s*(?=\n|$)'
            if re.search(pattern, code, re.DOTALL):
                code = re.sub(
                    pattern,
                    f'# [已移除] {api}()  # PTrade不支持此API',
                    code,
                    flags=re.DOTALL
                )
                self.conversion_report['warnings'].append(f'已移除不支持的API: {api}')

        return code

    def _convert_technical_indicators(self, code: str) -> str:
        """转换技术指标，并记录使用了哪些指标"""
        macd_converted = False
        rsi_converted = False

        # MACD
        def convert_macd_call(match):
            nonlocal macd_converted
            stock = match.group(1)
            macd_converted = True
            return f'get_macd_value(context, {stock})'

        if re.search(r'MACD\s*\(', code):
            code = re.sub(
                r'MACD\s*\(\s*([^,)]+)\s*,[^)]*\)',
                convert_macd_call,
                code
            )

        # RSI
        def convert_rsi_call(match):
            nonlocal rsi_converted
            stock = match.group(1)
            rsi_converted = True
            return f'get_rsi_value(context, {stock})'

        if re.search(r'RSI\s*\(', code):
            code = re.sub(
                r'RSI\s*\(\s*([^,)]+)\s*(?:,[^)]*)?\)',
                convert_rsi_call,
                code
            )

        # 记录到分析结果
        if macd_converted:
            self.conversion_report['changes'].append("MACD指标已转换为get_macd_value()")
        if rsi_converted:
            self.conversion_report['changes'].append("RSI指标已转换为get_rsi_value()")

        # 将使用信息存储在conversion_report中
        if macd_converted:
            self.conversion_report['uses_macd'] = True
        if rsi_converted:
            self.conversion_report['uses_rsi'] = True

        return code

    # ========================================================================
    # v5.0 新增转换方法
    # ========================================================================

    def _convert_set_slippage(self, code: str) -> str:
        """转换 set_slippage(FixedSlippage(N)) → set_slippage(N)"""
        # set_slippage(FixedSlippage(0)) → set_slippage(0)
        new_code = re.sub(
            r'set_slippage\s*\(\s*FixedSlippage\s*\(\s*([^)]+)\s*\)\s*\)',
            r'set_slippage(\1)',
            code
        )
        if new_code != code:
            self.conversion_report['changes'].append(
                "set_slippage(FixedSlippage(N)) → set_slippage(N) (PTrade期望数字)"
            )
        return new_code

    def _convert_get_history(self, code: str) -> str:
        """转换JQ get_history/history参数名到PTrade格式

        只做关键字参数名替换，不重构调用结构：
        - unit → frequency
        - end_dt → end_date
        - include_now → include
        - df → 移除
        """
        changes = []

        # unit= → frequency= (只替换get_history调用内的)
        if re.search(r'get_history\s*\(', code) and 'unit=' in code:
            new = re.sub(r'(get_history\s*\([^)]*?)unit=', r'\1frequency=', code)
            if new != code:
                code = new
                changes.append('unit → frequency')

        # end_dt= → end_date=
        if re.search(r'get_history\s*\(', code) and 'end_dt=' in code:
            new = re.sub(r'(get_history\s*\([^)]*?)end_dt=', r'\1end_date=', code)
            if new != code:
                code = new
                changes.append('end_dt → end_date')

        # include_now= → include=
        if re.search(r'get_history\s*\(', code) and 'include_now=' in code:
            new = re.sub(r'(get_history\s*\([^)]*?)include_now=', r'\1include=', code)
            if new != code:
                code = new
                changes.append('include_now → include')

        # 移除 df=True/False 参数
        if re.search(r'get_history\s*\(', code) and 'df=' in code:
            new = re.sub(r',\s*df\s*=\s*(True|False)', '', code)
            if new != code:
                code = new
                changes.append('移除df参数')

        if changes:
            self.conversion_report['changes'].append(
                f"get_history JQ参数名转PTrade格式 ({', '.join(changes)})"
            )
        return code

    def _convert_get_price(self, code: str) -> str:
        """移除get_price的panel/fill_paused参数，修复日期格式"""
        # 移除 panel=False/True
        new_code = re.sub(r',\s*panel\s*=\s*(?:False|True)', '', code)
        # 移除 fill_paused=False/True
        new_code = re.sub(r',\s*fill_paused\s*=\s*(?:False|True)', '', new_code)

        # 修复 get_price 返回格式: h['date'] = pd.DatetimeIndex(h.time).date → h['date'] = h.index.date
        new_code = re.sub(
            r"h\['date'\]\s*=\s*pd\.DatetimeIndex\(h\.time\)\.date",
            "if 'time' in h.columns:\n        h['date'] = pd.DatetimeIndex(h.time).date\n    else:\n        h['date'] = h.index.date",
            new_code
        )

        if new_code != code:
            self.conversion_report['changes'].append(
                "get_price: 移除panel/fill_paused参数，兼容返回格式"
            )
        return new_code

    def _fix_date_args(self, code: str) -> str:
        """修复日期参数: datetime.date对象 → 字符串格式

        PTrade的get_price/get_index_stocks等要求字符串日期，不接受datetime.date
        """
        changes = 0

        # get_index_stocks(code, date_obj) → get_index_stocks(code, date_obj.strftime('%Y%m%d'))
        # 匹配第二个参数是变量名(非字符串)的情况
        new_code = re.sub(
            r"(get_index_stocks\s*\(\s*['\"][^'\"]+['\"](?:\.(?:XSHG|XSHE|SS|SZ))?\s*,\s*)(\w+(?:\.\w+)*)\s*\)",
            lambda m: f"{m.group(1)}{m.group(2)}.strftime('%Y%m%d'))" if '.strftime' not in m.group(2) else m.group(0),
            code
        )
        if new_code != code:
            changes += 1

        # get_price(..., end_date=context.previous_date, ...) → .strftime('%Y%m%d')
        # 只对 context.previous_date 和 yesterday 变量添加转换
        for date_var in ['context.previous_date', 'yesterday']:
            pattern = rf"(end_date\s*=\s*{re.escape(date_var)})(\s*[,)])"
            replacement = rf"end_date={date_var}.strftime('%Y%m%d')\2"
            new_code2 = re.sub(pattern, replacement, new_code)
            if new_code2 != new_code:
                new_code = new_code2
                changes += 1

        if changes > 0:
            self.conversion_report['changes'].append(
                "日期参数: datetime.date → .strftime('%Y%m%d') (PTrade要求字符串)"
            )
        return new_code

    def _convert_subportfolios(self, code: str) -> str:
        """转换 context.subportfolios[0] → context.portfolio"""
        new_code = code.replace('context.subportfolios[0].long_positions', 'context.portfolio.positions')
        new_code = new_code.replace('context.subportfolios[0].available_cash', 'context.portfolio.available_cash')
        new_code = new_code.replace('context.subportfolios[0]', 'context.portfolio')

        if new_code != code:
            self.conversion_report['changes'].append(
                "context.subportfolios[0] → context.portfolio (PTrade无subportfolios)"
            )
        return new_code

    def _convert_has_key(self, code: str) -> str:
        """转换 Python 2 的 .has_key() → in 操作符"""
        # dict.has_key(key) → key in dict
        new_code = re.sub(
            r'(\w+)\.has_key\s*\(\s*([^)]+)\s*\)',
            r'\2 in \1',
            code
        )
        if new_code != code:
            self.conversion_report['changes'].append(
                ".has_key() → in 操作符 (Python 3兼容)"
            )
        return new_code

    def _fix_get_current_data_calls(self, code: str) -> str:
        """修复 get_current_data_compat() 无参调用

        在过滤函数中: current_data = get_current_data_compat()
        需要: current_data = get_current_data_compat(stock_list)
        """
        # 在函数定义中找到模式: def filter_xxx(stock_list): ... get_current_data_compat()
        # 替换为: get_current_data_compat(stock_list)
        new_code = re.sub(
            r'get_current_data_compat\s*\(\s*\)',
            'get_current_data_compat(stock_list)',
            code
        )

        if new_code != code:
            self.conversion_report['changes'].append(
                "get_current_data_compat() → get_current_data_compat(stock_list) (修复无参调用)"
            )
        return new_code

    def _find_matching_paren(self, text, open_pos):
        """从open_pos位置的(开始，用括号计数找到匹配的)的位置"""
        depth = 0
        for i in range(open_pos, len(text)):
            if text[i] == '(':
                depth += 1
            elif text[i] == ')':
                depth -= 1
                if depth == 0:
                    return i
        return -1

    def _convert_query_orm(self, code: str) -> str:
        """转换JQ query() ORM到PTrade get_fundamentals

        使用括号计数法正确匹配含嵌套括号的完整表达式:
        get_fundamentals(query(...).filter(...).order_by(...).limit(N)).set_index('code').index.tolist()
        """
        changed = False
        max_iterations = 10  # 防止无限循环

        for _ in range(max_iterations):
            start_match = re.search(r'get_fundamentals\s*\(\s*query\s*\(', code)
            if not start_match:
                break

            start_idx = start_match.start()
            gf_open = code.index('(', start_idx)

            # 括号计数找 get_fundamentals( 的匹配 )
            gf_close = self._find_matching_paren(code, gf_open)
            if gf_close == -1:
                break

            # 检查尾部 .set_index('code').index.tolist() 等
            rest = code[gf_close + 1:]
            trail_len = 0
            for tp in [
                r"\.set_index\([^)]*\)\s*\.\s*index\s*\.\s*tolist\s*\(\s*\)",
                r"\.set_index\([^)]*\)\s*\.\s*index\b",
            ]:
                tm = re.match(tp, rest)
                if tm:
                    trail_len = tm.end()
                    break

            full_end = gf_close + 1 + trail_len

            # 检测缩进和赋值变量
            line_start = code.rfind('\n', 0, start_idx) + 1
            # 只提取行首空白，不含变量名
            ind = ''
            for c in code[line_start:]:
                if c in ' \t':
                    ind += c
                else:
                    break
            if not ind:
                ind = '    '

            # 向前找 "var = " 模式
            prefix = code[:start_idx]
            assign_match = re.search(r'(\w[\w.]*)\s*=\s*$', prefix)
            if assign_match:
                result_var = assign_match.group(1)
            else:
                result_var = '_query_result'
            replace_start = line_start

            # 提取 get_fundamentals(...) 内部文本
            inner = code[gf_open + 1:gf_close]

            # 解析 query(...) 内容（括号计数）
            q_match = re.search(r'query\s*\(', inner)
            if not q_match:
                break
            q_open = inner.index('(', q_match.start())
            q_close = self._find_matching_paren(inner, q_open)
            if q_close == -1:
                break

            query_fields = inner[q_open + 1:q_close].strip()
            fields = re.findall(r'(\w+)\.(\w+)', query_fields)
            if not fields:
                break

            main_table = fields[0][0]
            field_names = [f[1] for f in fields]

            # 解析 .filter(...)（括号计数）
            remaining = inner[q_close + 1:]
            filter_content = ''
            fm = re.search(r'\.filter\s*\(', remaining)
            if fm:
                f_open_abs = q_close + 1 + fm.end() - 1
                f_close = self._find_matching_paren(inner, f_open_abs)
                if f_close != -1:
                    filter_content = inner[f_open_abs + 1:f_close].strip()
                    remaining = inner[f_close + 1:]

            # 解析 .order_by(...)
            order_content = ''
            om = re.search(r'\.order_by\s*\(', remaining)
            if om:
                o_open = om.end() - 1
                o_close = self._find_matching_paren(remaining, o_open)
                if o_close != -1:
                    order_content = remaining[o_open + 1:o_close].strip()
                    remaining = remaining[o_close + 1:]

            # 解析 .limit(...)
            limit_val = 'None'
            lm = re.search(r'\.limit\s*\(', remaining)
            if lm:
                l_open = lm.end() - 1
                l_close = self._find_matching_paren(remaining, l_open)
                if l_close != -1:
                    limit_val = remaining[l_open + 1:l_close].strip()

            # 解析 filter 条件
            stock_var = None
            filter_conditions = []
            for cond in re.finditer(r'(\w+)\.(\w+)\s*>\s*([\d.]+)', filter_content):
                filter_conditions.append((cond.group(1), cond.group(2), '>', cond.group(3)))
            for cond in re.finditer(r'(\w+)\.(\w+)\s*<\s*([\d.]+)', filter_content):
                filter_conditions.append((cond.group(1), cond.group(2), '<', cond.group(3)))
            in_match = re.search(r'(\w+)\.code\.in_\s*\(\s*([\w.]+)\s*\)', filter_content)
            if in_match:
                stock_var = in_match.group(2)
            if not stock_var:
                stock_var = 'security'

            # 解析 order_by
            is_asc = True
            order_field = None
            if order_content:
                om2 = re.search(r'(\w+)\.(\w+)\.(asc|desc)', order_content)
                if om2:
                    order_field = om2.group(2)
                    is_asc = om2.group(3) == 'asc'

            # 生成替换代码（先初始化变量，只用if不用else，避免吞没后续代码）
            date_arg = "yesterday.strftime('%Y%m%d') if 'yesterday' in dir() else context.previous_date.strftime('%Y%m%d')"

            lines = []
            lines.append(f"{ind}# JQ query自动转换")

            # 跨表filter先查
            main_conditions = []
            cross_conditions = []
            if filter_conditions:
                main_conditions = [(t, f, o, v) for t, f, o, v in filter_conditions if t == main_table]
                cross_conditions = [(t, f, o, v) for t, f, o, v in filter_conditions if t != main_table]

                for tbl, fld, op, val in cross_conditions:
                    ptbl = 'profit_ability' if tbl == 'indicator' else tbl
                    lines.append(f"{ind}df_{tbl} = get_fundamentals({stock_var}, '{ptbl}', fields=['code', '{fld}'],")
                    lines.append(f"{ind}              date={date_arg}, is_dataframe=True)")
                    lines.append(f"{ind}if df_{tbl} is not None and not df_{tbl}.empty and 'code' in df_{tbl}.columns:")
                    lines.append(f"{ind}    {stock_var} = df_{tbl}[df_{tbl}['{fld}'] {op} {val}]['code'].tolist()")
                    lines.append(f"{ind}else:")
                    lines.append(f"{ind}    {stock_var} = []")

            # 主表查询
            lines.append(f"{ind}{result_var} = []")
            all_fields = ['code'] + field_names
            if order_field and order_field not in all_fields:
                all_fields.append(order_field)
            fields_str = str(all_fields)
            lines.append(f"{ind}df_query = get_fundamentals({stock_var}, '{main_table}', fields={fields_str},")
            lines.append(f"{ind}              date={date_arg}, is_dataframe=True)")
            lines.append(f"{ind}if df_query is not None and not df_query.empty:")

            for tbl, fld, op, val in main_conditions:
                lines.append(f"{ind}    df_query = df_query[df_query['{fld}'] {op} {val}]")

            if order_field:
                direction = 'True' if is_asc else 'False'
                lines.append(f"{ind}    df_query = df_query.sort_values('{order_field}', ascending={direction})")
            if limit_val and limit_val != 'None':
                lines.append(f"{ind}    df_query = df_query.head({limit_val})")

            lines.append(f"{ind}    {result_var} = df_query.set_index('code').index.tolist() if 'code' in df_query.columns else []")

            replacement = '\n'.join(lines)
            new_code = code[:replace_start] + replacement + code[full_end:]

            if new_code != code:
                changed = True
                code = new_code
            else:
                break

        if changed:
            self.conversion_report['changes'].append(
                f"query() ORM自动转PTrade get_fundamentals (table={main_table})"
            )
        return code

    def _map_table_names(self, code: str) -> str:
        """映射JQ表名到PTrade表名: indicator → profit_ability"""
        mappings = {
            "'indicator'": "'profit_ability'",
            '"indicator"': "'profit_ability'",
        }
        new_code = code
        for old, new in mappings.items():
            new_code = new_code.replace(old, new)
        if new_code != code:
            self.conversion_report['changes'].append(
                "表名映射: indicator → profit_ability"
            )
        return new_code

    def _convert_misc_issues(self, code: str) -> str:
        """转换其他杂项问题"""
        # 处理OrderStatus
        code = re.sub(
            r'\bOrderStatus\.held\b',
            "'held'",
            code
        )
        # 移除 from jqdata import *
        code = re.sub(r'from\s+jqdata\s+import\s+\*\s*\n?', '', code)
        # 移除 import jqdata
        code = re.sub(r'import\s+jqdata\s*\n?', '', code)
        # 移除 from jqfactor import *
        code = re.sub(r'from\s+jqfactor\s+import\s+\*\s*\n?', '', code)

        return code

    def _convert_stock_info_attr(self, code: str) -> str:
        """v5.1: get_stock_info()返回嵌套dict，属性访问→字典访问+日期解析"""
        # get_stock_info(x).start_date → 解析listed_date
        pattern = r'get_stock_info\(([^)]+)\)\s*\.\s*start_date'
        if re.search(pattern, code):
            def replace_stock_info(m):
                var = m.group(1).strip()
                return f"datetime.datetime.strptime(get_stock_info({var}).get({var}, {{}}).get('listed_date', '2000-01-01'), '%Y-%m-%d').date()"
            code = re.sub(pattern, replace_stock_info, code)
            self.conversion_report['api_mappings'].append('get_stock_info(x).start_date → get_stock_info(x)[x]["listed_date"]')
        return code

    def _convert_position_access(self, code: str) -> str:
        """v5.2: position.security不存在，改用dict key"""
        # position.security → 直接在调用处改为用stock变量
        # 处理: for position in ...: stock = position.security
        code = re.sub(
            r'for\s+position\s+in\s+list\(context\.portfolio\.positions\.values\(\)\):\s*\n\s*stock\s*=\s*position\.security\s*\n\s*context\.hold_list\.append\(stock\)',
            'context.hold_list = list(context.portfolio.positions.keys())',
            code
        )
        # 处理: stock = position.security
        code = re.sub(r'stock\s*=\s*position\.security', '# stock already from dict key', code)
        return code

    def _convert_order_checks(self, code: str) -> str:
        """v5.2: PTrade order返回字符串ID，不是Order对象"""
        # order.filled > 0 → order is not None and order != ''
        code = re.sub(
            r'order\s*!=\s*None\s+and\s+order\.filled\s*>\s*0',
            "order is not None and order != ''",
            code
        )
        # order.status == 'held' and order.filled == order.amount → True
        code = re.sub(
            r"order\.status\s*==\s*'held'\s+and\s+order\.filled\s*==\s*order\.amount",
            "True",
            code
        )
        # order != None (general)
        code = re.sub(r'order\s*!=\s*None\b', "order is not None and order != ''", code)
        return code

    def _convert_get_fundamentals_code_field(self, code: str) -> str:
        """v5.2: PTrade get_fundamentals返回的DataFrame无code列，股票代码是index"""
        # 移除fields参数中的'code'
        code = re.sub(
            r"fields\s*=\s*\['code',\s*",
            "fields=['",
            code
        )
        code = re.sub(
            r",\s*'code'\]",
            "]",
            code
        )
        code = re.sub(
            r"\['code',\s*'code',\s*",
            "['",
            code
        )
        # df['code'].tolist() → df.index.tolist()
        code = re.sub(r"df_\w+\['code'\]\.tolist\(\)", lambda m: m.group(0).replace("['code']", ".index"), code)
        # df.set_index('code').index.tolist() → df.index.tolist()
        code = re.sub(r"\.set_index\('code'\)\.index\.tolist\(\)", ".index.tolist()", code)
        # 'code' in df.columns → 永远False，删除这个条件
        code = re.sub(r"and\s+'code'\s+in\s+\w+\.columns", "", code)
        return code

    def _convert_market_value(self, code: str) -> str:
        """v5.2: get_market_value回测不可用，替换为tushare daily_basic"""
        if 'get_market_value' not in code:
            return code
        # 添加tushare市值获取辅助函数（如果不存在）
        if 'def _get_market_cap_ts' not in code:
            helper = '''

def _get_market_cap_ts(stock_list, date_str):
    """通过tushare获取市值数据（PTrade回测模式兼容）"""
    try:
        ts_codes = []
        for s in stock_list:
            code, suffix = s.split('.')
            ts_codes.append(code + ('.SH' if suffix == '.SS' else '.' + suffix))
        df = _ts_pro.daily_basic(trade_date=date_str, fields='ts_code,total_mv')
        if df is not None and not df.empty:
            ts_set = set(ts_codes)
            df = df[df['ts_code'].isin(ts_set)]
            result = {}
            for _, row in df.iterrows():
                ts_c = row['ts_code']
                code, suffix = ts_c.split('.')
                ptrade_code = code + ('.SS' if suffix == 'SH' else '.' + suffix)
                result[ptrade_code] = row['total_mv']
            return result
    except Exception:
        pass
    return {}

'''
            lines = code.split('\n')
            insert_idx = 0
            for i, line in enumerate(lines):
                if line.startswith('def '):
                    insert_idx = i
                    break
            lines.insert(insert_idx, helper)
            code = '\n'.join(lines)
        return code

    def _convert_get_price_params(self, code: str) -> str:
        """v5.2: 移除get_price不支持的skip_paused/fq参数"""
        # skip_paused=False
        code = re.sub(r',\s*skip_paused\s*=\s*\w+', '', code)
        # fq='pre' or fq='daily'
        code = re.sub(r",\s*fq\s*=\s*['\"][^'\"]*['\"]", '', code)
        return code

    def _convert_close_position(self, code: str) -> str:
        """v5.2: close_position(position) → close_position(stock)"""
        # close_position(position) where position = context.portfolio.positions[stock]
        code = re.sub(
            r'position\s*=\s*context\.portfolio\.positions\[stock\]\s*\n(\s*)close_position\(position\)',
            r'\1close_position(stock)',
            code
        )
        return code

    def _add_helper_functions(self, code: str, analysis: Dict) -> str:
        """添加辅助函数 - 只在代码中实际使用时才添加"""
        # 检查转换过程中是否实际使用了MACD/RSI
        uses_macd = self.conversion_report.get('uses_macd', False)
        uses_rsi = self.conversion_report.get('uses_rsi', False)
        has_get_current_data = 'get_current_data_compat' in code

        # 检查是否已经有函数定义（避免重复添加）
        has_macd_func = 'def get_macd_value' in code
        has_rsi_func = 'def get_rsi_value' in code
        has_compat_func = 'def get_current_data_compat' in code

        helper_functions = []
        added_funcs = []

        # MACD函数
        if uses_macd and not has_macd_func:
            helper_functions.append('''
def get_macd_value(context, security, short_period=12, long_period=26, signal_period=9):
    """
    获取MACD指标值（兼容聚宽的MACD函数）

    Args:
        context: 上下文
        security: 证券代码
        short_period: 短周期
        long_period: 长周期
        signal_period: 信号周期

    Returns:
        float: MACD柱状图值
    """
    try:
        # 获取历史数据
        h = get_history(50, '1d', ['close'], stock_list=[security], end_date=context.current_dt)
        close_data = h['close'].values

        # 计算MACD
        import pandas as pd
        import numpy as np
        ema_short = pd.Series(close_data).ewm(span=short_period).mean()
        ema_long = pd.Series(close_data).ewm(span=long_period).mean()
        dif = ema_short - ema_long
        dea = dif.ewm(span=signal_period).mean()
        macd_bar = (dif - dea) * 2

        return macd_bar[-1]  # 返回最新的MACD柱状图值
    except Exception as e:
        log.warning(f"计算MACD失败: {e}")
        return 0.0
''')
            added_funcs.append('get_macd_value')

        # RSI函数
        if uses_rsi and not has_rsi_func:
            helper_functions.append('''
def get_rsi_value(context, security, period=14):
    """
    获取RSI指标值（兼容聚宽的RSI函数）

    Args:
        context: 上下文
        security: 证券代码
        period: 周期

    Returns:
        float: RSI值
    """
    try:
        # 获取历史数据
        h = get_history(period + 10, '1d', ['close'], stock_list=[security], end_date=context.current_dt)
        close_data = h['close'].values

        # 计算RSI
        import pandas as pd
        import numpy as np
        delta = pd.Series(close_data).diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        return rsi.values[-1]  # 返回最新的RSI值
    except Exception as e:
        log.warning(f"计算RSI失败: {e}")
        return 50.0
''')
            added_funcs.append('get_rsi_value')

        if helper_functions:
            # 合并所有辅助函数
            all_helpers = ''.join(helper_functions)

            # 在第一个def之前插入
            lines = code.split('\n')
            insert_index = 0
            for i, line in enumerate(lines):
                if line.startswith('def '):
                    insert_index = i
                    break
                elif not line.strip().startswith('#') and line.strip():
                    insert_index = i
                    break

            lines.insert(insert_index, all_helpers)
            code = '\n'.join(lines)

            self.conversion_report['added_functions'].extend(added_funcs)

        # ===== v4.0: 注入 Tushare 数据函数 =====
        tushare_funcs = analysis.get('needs_tushare_inject', [])
        if tushare_funcs:
            code = self._inject_tushare_functions(code, tushare_funcs)

        return code

    def _inject_tushare_functions(self, code: str, required_functions: list) -> str:
        """注入 Tushare 数据函数到策略代码中"""
        try:
            from .tushare_data_functions import get_injection_code
        except ImportError:
            from tushare_data_functions import get_injection_code

        # 获取 Tushare token
        token = self.tushare_token
        if not token:
            try:
                import sys
                from pathlib import Path
                project_root = Path(__file__).resolve().parent.parent.parent
                sys.path.insert(0, str(project_root))
                from config import get_env_config
                env = get_env_config()
                token = env.tushare_token or ''
            except Exception:
                pass

        injection_code = get_injection_code(required_functions, tushare_token=token)
        if not injection_code:
            return code

        # 找到第一个 def 或 initialize 函数，在其前面插入
        lines = code.split('\n')
        insert_index = 0
        for i, line in enumerate(lines):
            if line.startswith('def ') or (line.strip() and not line.startswith('#') and not line.startswith('import')):
                insert_index = i
                break

        lines.insert(insert_index, injection_code + '\n')
        code = '\n'.join(lines)

        self.conversion_report['added_functions'].extend(required_functions)
        if self.verbose:
            print(f"\n[Tushare] 注入数据函数: {', '.join(required_functions)}")

        return code

    def _post_process(self, code: str, strategy_type: StrategyType) -> str:
        """后处理"""
        # 清理导入语句
        lines = code.split('\n')
        cleaned_lines = []

        for line in lines:
            if line.startswith('import jqdata') or line.startswith('from jqdata import'):
                cleaned_lines.append(f'# {line}  # 聚宽数据模块已移除')
            elif line.startswith('from jqfactor import') or line.startswith('import jqfactor'):
                cleaned_lines.append(f'# {line}  # 聚宽因子库需要手动处理')
            else:
                cleaned_lines.append(line)

        code = '\n'.join(cleaned_lines)

        # 检查代码中是否使用了datetime，确保导入
        uses_datetime = 'datetime.' in code or 'context.current_dt' in code or 'context.previous_date' in code
        has_datetime_import = 'import datetime' in code

        if uses_datetime and not has_datetime_import:
            # 在第一个import之前添加datetime导入
            lines = code.split('\n')
            import_index = -1
            for i, line in enumerate(lines):
                if line.startswith('import ') and 'import datetime' not in line:
                    import_index = i
                    break
                elif line.strip() and not line.startswith('#'):
                    import_index = i
                    break

            if import_index >= 0:
                lines.insert(import_index, 'import datetime  # 添加datetime导入\n')
                code = '\n'.join(lines)

        # 添加头部说明
        header = f'''# 聚宽策略转Ptrade - {strategy_type.value.upper()}版本
# 转换时间: {self._get_timestamp()}
# 转换器版本: v5.2 - 反向集成PTrade回测调试修复

'''

        if not code.startswith('# 聚宽策略转Ptrade'):
            code = header + code

        if not code.endswith('\n'):
            code += '\n'

        return code

    def _get_timestamp(self) -> str:
        """获取时间戳"""
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def _print_report(self):
        """打印转换报告"""
        print("\n" + "=" * 60)
        print("转换报告")
        print("=" * 60)

        if self.conversion_report['api_mappings']:
            print("\n[OK] API映射:")
            for mapping in self.conversion_report['api_mappings']:
                print(f"  - {mapping}")

        if self.conversion_report['changes']:
            print("\n[OK] 代码改动:")
            for change in self.conversion_report['changes']:
                print(f"  - {change}")

        if self.conversion_report['added_functions']:
            print("\n[OK] 添加的辅助函数:")
            for func in self.conversion_report['added_functions']:
                print(f"  - {func}")

        if self.conversion_report['warnings']:
            print("\n[WARNING] 警告:")
            for warning in self.conversion_report['warnings']:
                print(f"  - {warning}")

        if self.conversion_report['errors']:
            print("\n[ERROR] 错误:")
            for error in self.conversion_report['errors']:
                print(f"  - {error}")

        print("\n" + "=" * 60)
        print("转换完成！请检查生成的代码并根据警告信息手动调整。")
        print("=" * 60 + "\n")

    def get_conversion_report(self) -> Dict:
        """获取转换报告"""
        return self.conversion_report


# 使用示例
if __name__ == "__main__":
    # 测试代码
    sample_jq_code = '''
import jqdata
from jqfactor import MACD

def initialize(context):
    g.stock_num = 10
    g.hold_list = []
    set_benchmark('000300.XSHG')

    # 测试定时函数
    run_monthly(monthly_adjustment, monthday=1, time='9:30', reference_security='000300.XSHG')
    run_weekly(weekly_rebalance, weekday=1, time='10:00', reference_security='000300.XSHG')
    run_daily(daily_check, time='14:00', reference_security='000300.XSHG')

def monthly_adjustment(context):
    """月度调整"""
    # 获取当前数据
    current_data = get_current_data()
    for stock in context.hold_list:
        if current_data[stock].last_price > current_data[stock].high_limit:
            log.info(f'{stock} 涨停')

    # 使用MACD指标
    macd_value = MACD('000001.XSHE', check_date=context.previous_date)
    if macd_value > 0:
        order_target_value('000001.XSHE', 100000)

def weekly_rebalance(context):
    """周度调整 - 使用Ptrade原生run_weekly"""
    pass

def daily_check(context):
    """每日检查"""
    pass
'''

    converter = JQToPtradeUnifiedConverter(verbose=True)
    converted_code = converter.convert(sample_jq_code)

    # 保存
    with open('ptrade_strategy_v3_test.py', 'w', encoding='utf-8') as f:
        f.write(converted_code)

    print("[OK] 测试代码已保存到: ptrade_strategy_v3_test.py")
    print("\nv3.4改进内容:")
    print("  - 修复datetime模块导入缺失问题")
    print("  - 智能添加辅助函数(MACD/RSI只在需要时)")
    print("  - 精简注释，减少冗余说明")
    print("  - run_weekly/monthly转换为run_daily+日期检查")
    print("  - 基于实际Ptrade运行验证")
