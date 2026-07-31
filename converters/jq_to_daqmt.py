# -*- coding: utf-8 -*-
"""
聚宽转大QMT（内置Python）智能转换器 V1.0
将聚宽策略自动转换为大QMT内置策略引擎可直接运行的代码

大QMT 内置 Python 策略引擎特点：
- init(ContextInfo) + handlebar(ContextInfo) 框架
- ContextInfo.run_time() 定时任务
- passorder() 下单
- get_trade_detail_data() 查持仓/资产/委托
- ContextInfo.get_market_data_ex() 获取行情
- Python 3.6+，支持 f-string

作者：王者Quant
版本：v1.0.0
"""

import re
import textwrap
import os
from typing import Dict, List, Optional
from datetime import datetime

# 尝试导入 tushare 注入模块（从同目录加载）
try:
    from .tushare_data_functions import get_injection_code as _get_tushare_injection
except ImportError:
    try:
        from tushare_data_functions import get_injection_code as _get_tushare_injection
    except ImportError:
        _get_tushare_injection = None


class JQToDaQmtConverter:
    """聚宽到 大QMT（内置Python）转换器 V1.0"""

    def __init__(self, verbose: bool = True, account_id: str = "xxxxxxxxxxxx"):
        self.verbose = verbose
        self.account_id = account_id
        self.conversion_report = {
            'api_mappings': [],
            'warnings': [],
            'errors': [],
            'changes': [],
            'added_functions': [],
        }

        # API 名称映射（聚宽 → 大QMT）
        self.api_mapping = {
            'get_price': 'ContextInfo.get_market_data_ex',
            'get_bars': 'ContextInfo.get_market_data_ex',
            'history': 'ContextInfo.get_market_data_ex',
            'attribute_history': 'ContextInfo.get_market_data_ex',
            'get_trade_days': '_get_trading_dates',
            'get_trading_dates': '_get_trading_dates',
            'get_all_securities': '_get_all_securities',
            'get_security_info': '_get_security_info',
            'get_current_data': '_get_current_data_compat',
            'get_index_stocks': 'ContextInfo.get_stock_list_in_sector',
            'get_fundamentals': '_get_fundamentals_continuously',  # tushare 替代
        }

        # log 映射
        self.log_mapping = {
            'log.info': 'print', 'log.warn': 'print',
            'log.error': 'print', 'log.debug': 'print',
        }

        # 不支持的 API → 直接移除
        self.unsupported_apis = [
            'set_option', 'set_order_cost', 'set_commission',
            'set_price_limit', 'enable_profile', 'log.set_level',
        ]

        # 注释掉的 API
        self.comment_apis = ['set_benchmark', 'set_universe']

        # run_daily 时间映射
        self.time_mapping = {
            'before_open': '09:00:00',
            'open': '09:30:00',
            'close': '14:50:00',
            'after_close': '15:30:00',
        }

    # ================================================================
    #  主转换入口
    # ================================================================

    def convert(self, jq_code: str, output_file: Optional[str] = None) -> str:
        self._reset_report()

        if self.verbose:
            print("=" * 70)
            print("聚宽 → 大QMT（内置Python）智能转换器 V1.0")
            print("=" * 70)

        # Step 1: 分析
        analysis = self._analyze_code(jq_code)

        # Step 2: 移除不支持API & 清理导入
        code = self._remove_unsupported(jq_code)

        # Step 3: 提取函数体和全局变量
        extracted = self._extract_code_blocks(code)

        # 从 initialize() 提取 g.xxx 全局变量
        if 'initialize' in extracted['functions']:
            init_body = extracted['functions']['initialize']['body']
            for line in init_body.split('\n'):
                g_match = re.match(r'\s*g\.(\w+)\s*=\s*(.+)', line)
                if g_match:
                    var_name = g_match.group(1)
                    var_value = g_match.group(2).strip()
                    if not any(v.startswith(f'gvar.{var_name} =') for v in extracted['global_vars']):
                        extracted['global_vars'].append(self._standardize_codes(f'gvar.{var_name} = {var_value}'))
                        self._add_mapping(f'g.{var_name} → gvar.{var_name}')
            del extracted['functions']['initialize']

        # 对全局变量也做代码标准化
        extracted['global_vars'] = [self._standardize_codes(v) for v in extracted['global_vars']]

        # Step 4: 对每个函数体应用转换管道
        all_func_names = set(extracted['functions'].keys())
        converted_functions = {}
        for func_name, func_info in extracted['functions'].items():
            body = func_info['body']
            body = self._convert_function_body(body)
            body = self._convert_trading_apis(body)
            body = self._convert_context_access(body)
            body = self._convert_bare_context(body)
            body = self._standardize_codes(body)
            body = self._fix_get_price_params(body)
            body = self._fix_date_formats(body)
            converted_functions[func_name] = {
                'body': body,
                'params': func_info['params'],
            }

        # 修正函数调用参数
        for func_name in converted_functions:
            converted_functions[func_name]['body'] = self._fix_func_calls(
                converted_functions[func_name]['body'], all_func_names)

        # Step 5: 生成最终脚本
        final_code = self._generate_script(
            global_vars=extracted['global_vars'],
            functions=converted_functions,
            analysis=analysis,
        )

        if output_file:
            with open(output_file, 'w', encoding='gbk') as f:
                f.write(final_code)

        if self.verbose:
            self._print_report()

        return final_code

    # ================================================================
    #  Step 1: 代码分析
    # ================================================================

    def _analyze_code(self, code: str) -> Dict:
        analysis = {
            'has_initialize': bool(re.search(r'def\s+initialize\s*\(', code)),
            'has_handle_data': bool(re.search(r'def\s+handle_data\s*\(', code)),
            'has_trading': False,
            'uses_get_current_data': False,
            'uses_fundamentals': False,
            'uses_get_factor_values': False,
            'uses_index_stocks': False,
            'timing_functions': [],
            'strategy_name': 'JQ转大QMT策略',
            'tushare_functions': [],  # 需要注入的 tushare 函数列表
        }

        if re.search(r'\b(order|order_value|order_target|order_target_value|order_target_percent)\s*\(', code):
            analysis['has_trading'] = True

        if re.search(r'\bget_current_data\s*\(', code):
            analysis['uses_get_current_data'] = True
            analysis['tushare_functions'].append('get_current_data')

        if re.search(r'\bget_fundamentals\s*\(', code):
            analysis['uses_fundamentals'] = True
            analysis['tushare_functions'].append('get_fundamentals_continuously')

        if re.search(r'\bget_factor_values\s*\(', code):
            analysis['uses_get_factor_values'] = True
            analysis['tushare_functions'].append('get_factor_values')

        if re.search(r'\bget_index_stocks\s*\(', code):
            analysis['uses_index_stocks'] = True

        if re.search(r'\bget_all_securities\s*\(', code):
            analysis['tushare_functions'].append('get_all_securities')

        if re.search(r'\bget_extras\s*\(', code):
            analysis['tushare_functions'].append('get_extras')

        if re.search(r'\bget_industry\s*\(', code):
            analysis['tushare_functions'].append('get_industry')

        # 去重
        analysis['tushare_functions'] = list(set(analysis['tushare_functions']))

        # 提取 timing_functions
        timing_patterns = [
            (r'^[^#]*\brun_daily\s*\(\s*(\w+)\s*,\s*([^)]+)\)', 'run_daily'),
            (r'^[^#]*\brun_weekly\s*\(\s*(\w+)\s*,\s*([^)]+)\)', 'run_weekly'),
            (r'^[^#]*\brun_monthly\s*\(\s*(\w+)\s*,\s*([^)]+)\)', 'run_monthly'),
        ]
        for pattern, ttype in timing_patterns:
            for m in re.finditer(pattern, code):
                analysis['timing_functions'].append(
                    (ttype, m.group(1), m.group(2).strip()))

        return analysis

    # ================================================================
    #  Step 2: 移除不支持API
    # ================================================================

    def _remove_unsupported(self, code: str) -> str:
        for pattern in [r'^import\s+jqdata.*\n?', r'^from\s+jqdata\s+import.*\n?',
                         r'^from\s+jqfactor\s+import.*\n?']:
            if re.search(pattern, code, re.MULTILINE):
                code = re.sub(pattern, '', code, flags=re.MULTILINE)
                self._add_change('移除导入: jqdata/jqfactor')

        for api in self.unsupported_apis:
            pattern = rf'^[ \t]*{re.escape(api)}\s*\([^)]*\).*\n?'
            if re.search(pattern, code, re.MULTILINE):
                code = re.sub(pattern, '', code, flags=re.MULTILINE)
                self._add_change(f'移除不支持API: {api}()')

        for api in self.comment_apis:
            pattern = rf'^([ \t]*)({re.escape(api)}\s*\([^)]*\))'
            if re.search(pattern, code, re.MULTILINE):
                code = re.sub(pattern, r'\1# \2  # 大QMT在回测界面配置',
                              code, flags=re.MULTILINE)
                self._add_change(f'注释: {api}()')

        return code

    # ================================================================
    #  Step 3: 提取代码块
    # ================================================================

    def _extract_code_blocks(self, code: str) -> Dict:
        result = {'global_vars': [], 'functions': {}}
        lines = code.split('\n')
        i = 0
        global_lines = []

        while i < len(lines):
            line = lines[i]
            func_match = re.match(r'def\s+(\w+)\s*\(([^)]*)\)\s*:', line)

            if func_match:
                for gl in global_lines:
                    stripped = gl.strip()
                    if not stripped or stripped.startswith('#'):
                        continue
                    if stripped.startswith('import ') or stripped.startswith('from '):
                        continue
                    result['global_vars'].append(gl)
                global_lines = []

                func_name = func_match.group(1)
                func_params_str = func_match.group(2).strip()
                params = [p.strip() for p in func_params_str.split(',') if p.strip()]
                kept_params = [p for p in params
                               if p not in ('context',) and not p.startswith('context:')]
                new_params = ['ContextInfo'] + kept_params

                body_lines = []
                i += 1
                while i < len(lines):
                    stripped_inner = lines[i].strip()
                    if stripped_inner and re.match(r'def\s+\w+\s*\(', lines[i]):
                        break
                    if stripped_inner and not lines[i].startswith(' ') and not lines[i].startswith('\t'):
                        if not stripped_inner.startswith('@'):
                            break
                    body_lines.append(lines[i])
                    i += 1

                result['functions'][func_name] = {
                    'body': '\n'.join(body_lines),
                    'params': ', '.join(new_params),
                }
                continue
            else:
                global_lines.append(line)
            i += 1

        return result

    # ================================================================
    #  Step 4a: 函数体基础转换
    # ================================================================

    def _convert_function_body(self, body: str) -> str:
        # log 映射
        for jq_log, daqmt_log in self.log_mapping.items():
            body = re.sub(rf'\b{re.escape(jq_log)}\s*\(', f'{daqmt_log}(', body)

        # g.xxx → gvar.xxx
        body = re.sub(r'\bg\.(\w+)', r'gvar.\1', body)
        # 但保留 gvar.gvar. → gvar.
        body = body.replace('gvar.gvar.', 'gvar.')

        # API 名称映射（最长优先）
        sorted_apis = sorted(self.api_mapping.items(), key=lambda x: -len(x[0]))
        for jq_api, daqmt_api in sorted_apis:
            body = re.sub(rf'(?<![.\w]){re.escape(jq_api)}\s*\(', f'{daqmt_api}(', body)

        # 清理：移除 context 参数引用
        body = re.sub(r'\bcontext\.', '', body)

        return body

    # ================================================================
    #  Step 4b: 交易API转换
    # ================================================================

    def _convert_trading_apis(self, body: str) -> str:
        result_lines = []
        for line in body.split('\n'):
            stripped = line.strip()
            indent = line[:len(line) - len(line.lstrip())]

            if 'order_target_percent(' in stripped:
                result_lines.append(self._conv_order_target_percent(stripped, indent))
            elif 'order_target_value(' in stripped:
                result_lines.append(self._conv_order_target_value(stripped, indent))
            elif 'order_target(' in stripped:
                result_lines.append(self._conv_order_target(stripped, indent))
            elif 'order_value(' in stripped:
                result_lines.append(self._conv_order_value(stripped, indent))
            elif re.search(r'\border\s*\(', stripped) and 'cancel_order' not in stripped:
                result_lines.append(self._conv_order(stripped, indent))
            elif 'cancel_order(' in stripped:
                result_lines.append(self._conv_cancel_order(stripped, indent))
            else:
                result_lines.append(line)

        return '\n'.join(result_lines)

    def _conv_order(self, stripped: str, indent: str) -> str:
        m = re.search(r'\border\s*\(\s*([^,]+)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)', stripped)
        if m:
            sec, amt = m.group(1), m.group(2)
            if float(amt) > 0:
                self._add_mapping(f'order({sec}, {amt}) → passorder(买入)')
                return (
                    f'{indent}_price = ContextInfo.get_market_data_ex('
                    f'fields=["close"], stock_code=[{sec}], period="1d", count=1, '
                    f'dividend_type="front", subscribe=True)\n'
                    f'{indent}_price = _price[{sec}]["close"].iloc[-1] if {sec} in _price else 0\n'
                    f'{indent}if _price > 0:\n'
                    f"{indent}    _uoi = f\"{{STRATEGY_NAME}}_{{datetime.now().strftime('%Y%m%d%H%M%S')}}\"\n"
                    f'{indent}    passorder(OPTYPE_BUY, ORDER_TYPE_VOLUME, ACCOUNT_ID, {sec}, '
                    f'PRTYPE_OPPOSITEBEST, -1, {int(float(amt))}, STRATEGY_NAME, 0, _uoi, ContextInfo)'
                )
            elif float(amt) < 0:
                self._add_mapping(f'order({sec}, {amt}) → passorder(卖出)')
                return (
                    f'{indent}_price = ContextInfo.get_market_data_ex('
                    f'fields=["close"], stock_code=[{sec}], period="1d", count=1, '
                    f'dividend_type="front", subscribe=True)\n'
                    f'{indent}_price = _price[{sec}]["close"].iloc[-1] if {sec} in _price else 0\n'
                    f'{indent}if _price > 0:\n'
                    f"{indent}    _uoi = f\"{{STRATEGY_NAME}}_{{datetime.now().strftime('%Y%m%d%H%M%S')}}\"\n"
                    f'{indent}    passorder(OPTYPE_SELL, ORDER_TYPE_VOLUME, ACCOUNT_ID, {sec}, '
                    f'PRTYPE_OPPOSITEBEST, -1, {int(abs(float(amt)))}, STRATEGY_NAME, 0, _uoi, ContextInfo)'
                )
            else:
                return f'{indent}# order({sec}, 0) — 无需操作'
        self._add_warning(f'order() 格式无法识别: {stripped[:60]}')
        return f'{indent}{stripped}'

    def _conv_order_value(self, stripped: str, indent: str) -> str:
        m = re.search(r'order_value\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', stripped)
        if m:
            sec, val = m.group(1), m.group(2)
            self._add_mapping(f'order_value({sec}, {val}) → 按金额买入')
            return (
                f'{indent}# order_value({sec}, {val}) → 按金额买入\n'
                f'{indent}_price = ContextInfo.get_market_data_ex('
                f'fields=["close"], stock_code=[{sec}], period="1d", count=1, '
                f'dividend_type="front", subscribe=True)\n'
                f'{indent}_price = _price[{sec}]["close"].iloc[-1] if {sec} in _price else 0\n'
                f'{indent}if _price > 0:\n'
                f'{indent}    _volume = int(({val}) / _price / 100) * 100\n'
                f'{indent}    if _volume > 0:\n'
                f"{indent}        _uoi = f\"{{STRATEGY_NAME}}_{{datetime.now().strftime('%Y%m%d%H%M%S')}}\"\n"
                f'{indent}        passorder(OPTYPE_BUY, ORDER_TYPE_VOLUME, ACCOUNT_ID, {sec}, '
                f'PRTYPE_OPPOSITEBEST, -1, _volume, STRATEGY_NAME, 0, _uoi, ContextInfo)'
            )
        return f'{indent}{stripped}'

    def _conv_order_target(self, stripped: str, indent: str) -> str:
        m = re.search(r'order_target\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', stripped)
        if m:
            sec, target = m.group(1), m.group(2)
            self._add_mapping(f'order_target({sec}, {target}) → 调仓')
            return (
                f'{indent}# order_target({sec}, {target}) → 调整持仓\n'
                f'{indent}_pos_list = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "POSITION")\n'
                f'{indent}_current = 0\n'
                f'{indent}for _p in _pos_list:\n'
                f'{indent}    if _p.m_strInstrumentID + "." + _p.m_strExchangeID == {sec}:\n'
                f'{indent}        _current = _p.m_nCanUseVolume\n'
                f'{indent}        break\n'
                f'{indent}_diff = ({target}) - _current\n'
                f'{indent}if _diff > 0:\n'
                f"{indent}    _uoi = f\"{{STRATEGY_NAME}}_{{datetime.now().strftime('%Y%m%d%H%M%S')}}\"\n"
                f'{indent}    passorder(OPTYPE_BUY, ORDER_TYPE_VOLUME, ACCOUNT_ID, {sec}, '
                f'PRTYPE_OPPOSITEBEST, -1, _diff, STRATEGY_NAME, 0, _uoi, ContextInfo)\n'
                f'{indent}elif _diff < 0:\n'
                f"{indent}    _uoi = f\"{{STRATEGY_NAME}}_{{datetime.now().strftime('%Y%m%d%H%M%S')}}\"\n"
                f'{indent}    passorder(OPTYPE_SELL, ORDER_TYPE_VOLUME, ACCOUNT_ID, {sec}, '
                f'PRTYPE_OPPOSITEBEST, -1, abs(_diff), STRATEGY_NAME, 0, _uoi, ContextInfo)'
            )
        return f'{indent}{stripped}'

    def _conv_order_target_value(self, stripped: str, indent: str) -> str:
        m = re.search(r'order_target_value\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', stripped)
        if m:
            sec, tval = m.group(1), m.group(2)
            if tval.strip() == '0':
                return self._conv_order_target(
                    stripped.replace('order_target_value', 'order_target'), indent)
            self._add_mapping(f'order_target_value({sec}, {tval}) → 按金额调仓')
            return (
                f'{indent}# order_target_value({sec}, {tval}) → 按金额调仓\n'
                f'{indent}_price = ContextInfo.get_market_data_ex('
                f'fields=["close"], stock_code=[{sec}], period="1d", count=1, '
                f'dividend_type="front", subscribe=True)\n'
                f'{indent}_price = _price[{sec}]["close"].iloc[-1] if {sec} in _price else 0\n'
                f'{indent}if _price > 0:\n'
                f'{indent}    _target_vol = int(({tval}) / _price / 100) * 100\n'
                f'{indent}    _pos_list = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "POSITION")\n'
                f'{indent}    _current = 0\n'
                f'{indent}    for _p in _pos_list:\n'
                f'{indent}        if _p.m_strInstrumentID + "." + _p.m_strExchangeID == {sec}:\n'
                f'{indent}            _current = _p.m_nCanUseVolume\n'
                f'{indent}            break\n'
                f'{indent}    _diff = _target_vol - _current\n'
                f'{indent}    if _diff > 0:\n'
                f"{indent}        _uoi = f\"{{STRATEGY_NAME}}_{{datetime.now().strftime('%Y%m%d%H%M%S')}}\"\n"
                f'{indent}        passorder(OPTYPE_BUY, ORDER_TYPE_VOLUME, ACCOUNT_ID, {sec}, '
                f'PRTYPE_OPPOSITEBEST, -1, _diff, STRATEGY_NAME, 0, _uoi, ContextInfo)\n'
                f'{indent}    elif _diff < 0:\n'
                f"{indent}        _uoi = f\"{{STRATEGY_NAME}}_{{datetime.now().strftime('%Y%m%d%H%M%S')}}\"\n"
                f'{indent}        passorder(OPTYPE_SELL, ORDER_TYPE_VOLUME, ACCOUNT_ID, {sec}, '
                f'PRTYPE_OPPOSITEBEST, -1, abs(_diff), STRATEGY_NAME, 0, _uoi, ContextInfo)'
            )
        return f'{indent}{stripped}'

    def _conv_order_target_percent(self, stripped: str, indent: str) -> str:
        m = re.search(r'order_target_percent\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', stripped)
        if m:
            sec, pct = m.group(1), m.group(2)
            self._add_mapping(f'order_target_percent({sec}, {pct}) → 百分比调仓')
            return (
                f'{indent}# order_target_percent({sec}, {pct}) → 百分比调仓\n'
                f'{indent}_acct = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "ACCOUNT")[0]\n'
                f'{indent}_total = _acct.m_dBalance\n'
                f'{indent}_target_value = _total * ({pct})\n'
                f'{indent}_price = ContextInfo.get_market_data_ex('
                f'fields=["close"], stock_code=[{sec}], period="1d", count=1, '
                f'dividend_type="front", subscribe=True)\n'
                f'{indent}_price = _price[{sec}]["close"].iloc[-1] if {sec} in _price else 0\n'
                f'{indent}if _price > 0:\n'
                f'{indent}    _target_vol = int(_target_value / _price / 100) * 100\n'
                f'{indent}    _pos_list = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "POSITION")\n'
                f'{indent}    _current = 0\n'
                f'{indent}    for _p in _pos_list:\n'
                f'{indent}        if _p.m_strInstrumentID + "." + _p.m_strExchangeID == {sec}:\n'
                f'{indent}            _current = _p.m_nCanUseVolume\n'
                f'{indent}            break\n'
                f'{indent}    _diff = _target_vol - _current\n'
                f'{indent}    if _diff > 0:\n'
                f"{indent}        _uoi = f\"{{STRATEGY_NAME}}_{{datetime.now().strftime('%Y%m%d%H%M%S')}}\"\n"
                f'{indent}        passorder(OPTYPE_BUY, ORDER_TYPE_VOLUME, ACCOUNT_ID, {sec}, '
                f'PRTYPE_OPPOSITEBEST, -1, _diff, STRATEGY_NAME, 0, _uoi, ContextInfo)\n'
                f'{indent}    elif _diff < 0:\n'
                f"{indent}        _uoi = f\"{{STRATEGY_NAME}}_{{datetime.now().strftime('%Y%m%d%H%M%S')}}\"\n"
                f'{indent}        passorder(OPTYPE_SELL, ORDER_TYPE_VOLUME, ACCOUNT_ID, {sec}, '
                f'PRTYPE_OPPOSITEBEST, -1, abs(_diff), STRATEGY_NAME, 0, _uoi, ContextInfo)'
            )
        return f'{indent}{stripped}'

    def _conv_cancel_order(self, stripped: str, indent: str) -> str:
        m = re.search(r'cancel_order\s*\(\s*([^)]+)\s*\)', stripped)
        if m:
            self._add_mapping(f'cancel_order({m.group(1)}) → cancel()')
            return (
                f'{indent}# cancel_order({m.group(1)}) → 大QMT 撤单\n'
                f'{indent}cancel({m.group(1)}, ACCOUNT_ID, ACCOUNT_TYPE, ContextInfo)'
            )
        return f'{indent}{stripped}'

    # ================================================================
    #  Step 4c: context 访问转换
    # ================================================================

    def _convert_context_access(self, body: str) -> str:
        # context.portfolio.total_value
        body = re.sub(
            r'context\.portfolio\.total_value',
            'get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "ACCOUNT")[0].m_dBalance',
            body)

        # context.portfolio.available_cash
        body = re.sub(
            r'context\.portfolio\.available_cash',
            'get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "ACCOUNT")[0].m_dAvailable',
            body)

        # context.portfolio.positions[code].total_amount
        body = re.sub(
            r"context\.portfolio\.positions\[([^\]]+)\]\.total_amount",
            r"_get_position_amount(\1)",
            body)

        # context.portfolio.positions[code].value
        body = re.sub(
            r"context\.portfolio\.positions\[([^\]]+)\]\.value",
            r"_get_position_value(\1)",
            body)

        # context.portfolio.positions[code].closeable_amount
        body = re.sub(
            r"context\.portfolio\.positions\[([^\]]+)\]\.closeable_amount",
            r"_get_position_available(\1)",
            body)

        # context.run_info → ContextInfo
        body = re.sub(r'\bcontext\.run_info\b', 'ContextInfo', body)

        return body

    def _convert_bare_context(self, body: str) -> str:
        """处理单独出现的 context 变量（不是 context.xxx 的）"""
        # context 作为独立变量出现（函数参数已改名，这里处理赋值等）
        body = re.sub(r'\bcontext\b(?=\s*[=,])', 'ContextInfo', body)
        return body

    # ================================================================
    #  Step 4d: 代码标准化
    # ================================================================

    def _standardize_codes(self, body: str) -> str:
        body = body.replace('.XSHG', '.SH')
        body = body.replace('.XSHE', '.SZ')
        return body

    def _fix_get_price_params(self, body: str) -> str:
        """修正 get_price → ContextInfo.get_market_data_ex 参数"""
        # get_price(codes, count=N, fields=[...])
        # → ContextInfo.get_market_data_ex(fields=[...], stock_code=codes, period='1d', count=N)
        # 这个转换比较复杂，简单的处理是加注释
        return body

    def _fix_date_formats(self, body: str) -> str:
        """日期格式修正"""
        # datetime.date(2024,1,1) → '20240101'
        body = re.sub(
            r'datetime\.date\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)',
            r"f'{\1}{\2:02d}{\3:02d}'", body)
        # datetime.datetime(...) → 保留，大QMT 支持
        return body

    def _fix_func_calls(self, body: str, all_func_names: set) -> str:
        """将用户自定义函数的 context 参数替换为 ContextInfo"""
        for fname in all_func_names:
            if fname in ('initialize', 'handle_data'):
                continue
            # func(context, ...) → func(ContextInfo, ...)
            body = re.sub(
                rf'\b{fname}\s*\(\s*context\b',
                f'{fname}(ContextInfo', body)
        return body

    # ================================================================
    #  Step 5: 生成脚本
    # ================================================================

    def _generate_script(self, global_vars: List[str],
                         functions: Dict[str, Dict], analysis: Dict) -> str:
        parts = []

        # ---- 文件头 ----
        parts.extend([
            '#encoding:gbk',
            '"""',
            f'聚宽策略 → 大QMT 自动转换 (V1.0) — 回测实盘一体版',
            '"""',
            '',
            'import os',
            'import json',
            'import numpy as np',
            'import pandas as pd',
            'from datetime import datetime, time, timedelta',
            '',
            '# ========================================',
            '# 参数配置（请修改为实际值）',
            '# ========================================',
            f"ACCOUNT_ID = '{self.account_id}'",
            "ACCOUNT_TYPE = 'STOCK'",
            "ACCOUNT_MODE = 'MONEY'      # MONEY=固定金额, RATIO=按账户比例",
            "ACCOUNT_MONEY = 50000       # ACCOUNT_MODE为MONEY时有效",
            "ACCOUNT_RATIO = 0.3         # ACCOUNT_MODE为RATIO时有效",
            "STRATEGY_TRADETIME = '09:45:00'  # 实盘交易时间 HH:MM:SS",
            "STRATEGY_PATH = r'D:\\量化策略'  # 策略文件存储路径",
            "STRATEGY_NAME = 'JQ转大QMT策略'",
            "TOKEN = '请填入自己Tushare的token'  # 使用tushare数据函数时必填",
            '',
            '# ========================================',
            '# passorder 下单常量（勿修改）',
            '# ========================================',
            'OPTYPE_BUY = 23',
            'OPTYPE_SELL = 24',
            'ORDER_TYPE_VOLUME = 1101',
            'ORDER_TYPE_MONEY = 1102',
            'PRTYPE_FIXED = 11',
            'PRTYPE_OPPOSITEBEST = 14',
            '',
            '# ========================================',
            '# 全局变量',
            '# ========================================',
            'class GlobalVariable:',
            '    pass',
            'gvar = GlobalVariable()',
            'gvar.is_backtest = True    # init()中根据ContextInfo.do_back_test自动设置',
            'gvar.quick_trade = 0       # 回测=0, 实盘=1',
            'gvar.stg_start_dt = datetime.now()',
        ])

        if global_vars:
            parts.extend(global_vars)
        else:
            parts.append('# 无全局变量')
        parts.append('')

        # ---- 辅助函数 ----
        parts.append('# ========================================')
        parts.append('# 辅助函数')
        parts.append('# ========================================')
        parts.append('')

        # timetag_to_datetime（大QMT 内置，但显式声明）
        parts.append('# 注: timetag_to_datetime 是大QMT内置函数，无需定义')
        parts.append('')

        # _get_position_amount 辅助
        if analysis.get('has_trading'):
            parts.extend([
                'def _get_position_amount(code):',
                '    """获取持仓数量"""',
                '    _pos_list = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "POSITION")',
                '    for _p in _pos_list:',
                '        if _p.m_strInstrumentID + "." + _p.m_strExchangeID == code:',
                '            return _p.m_nVolume',
                '    return 0',
                '',
                'def _get_position_available(code):',
                '    """获取可用持仓"""',
                '    _pos_list = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "POSITION")',
                '    for _p in _pos_list:',
                '        if _p.m_strInstrumentID + "." + _p.m_strExchangeID == code:',
                '            return _p.m_nCanUseVolume',
                '    return 0',
                '',
                'def _get_position_value(code):',
                '    """获取持仓市值"""',
                '    _pos_list = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "POSITION")',
                '    for _p in _pos_list:',
                '        if _p.m_strInstrumentID + "." + _p.m_strExchangeID == code:',
                '            return _p.m_dMarketValue',
                '    return 0',
                '',
            ])

        # _get_trading_dates 辅助
        parts.extend([
            'def _get_trading_dates(start_date, end_date):',
            '    """获取交易日期列表（通过 000300.SH 日线 index 获取）"""',
            '    _df = ContextInfo.get_market_data_ex(',
            '        fields=["close"], stock_code=["000300.SH"], period="1d",',
            '        start_time=start_date, end_time=end_date, dividend_type="none")',
            '    if "000300.SH" in _df:',
            '        return list(_df["000300.SH"].index)',
            '    return []',
            '',
        ])

        if analysis.get('uses_get_current_data'):
            parts.extend([
                'def _get_current_data_compat(codes):',
                '    """兼容聚宽 get_current_data 的数据结构"""',
                '    _result = {}',
                '    _ticks = ContextInfo.get_full_tick(codes)',
                '    for _code, _tick in _ticks.items():',
                '        if _tick:',
                '            class _DataObj:',
                '                pass',
                '            _obj = _DataObj()',
                '            _obj.close = _tick.get("lastPrice", 0)',
                '            _obj.high = _tick.get("high", 0)',
                '            _obj.low = _tick.get("low", 0)',
                '            _obj.open = _tick.get("open", 0)',
                '            _obj.volume = _tick.get("volume", 0)',
                '            _obj.last_price = _tick.get("lastPrice", 0)',
                '            _result[_code] = _obj',
                '    return _result',
                '',
            ])

        if analysis.get('uses_index_stocks'):
            parts.extend([
                'def _get_all_securities():',
                '    """获取所有A股"""',
                '    return ContextInfo.get_stock_list_in_sector("沪深A股")',
                '',
                'def _get_security_info(code):',
                '    """获取标的信息"""',
                '    return ContextInfo.get_instrument_detail(code)',
                '',
            ])

        # ---- Tushare 数据函数注入 ----
        tushare_funcs = analysis.get('tushare_functions', [])
        if tushare_funcs and _get_tushare_injection:
            try:
                tushare_code = _get_tushare_injection(tushare_funcs, tushare_token='')
                if tushare_code:
                    parts.append(tushare_code)
                    parts.append('')
                    self._add_function('tushare 数据函数注入')
            except Exception:
                pass

        # ---- 策略函数 ----
        parts.append('# ========================================')
        parts.append('# 策略函数')
        parts.append('# ========================================')
        parts.append('')

        for func_name, func_info in functions.items():
            if func_name in ('initialize', 'handle_data'):
                continue
            body = func_info['body']
            params = func_info['params']
            parts.append(f'def {func_name}({params}):')
            if body.strip():
                dedented = textwrap.dedent(body)
                parts.append(textwrap.indent(dedented, '    '))
            else:
                parts.append('    pass')
            parts.append('')
            parts.append('')

        # ---- init 函数 ----
        parts.append('# ========================================')
        parts.append('# 策略入口')
        parts.append('# ========================================')
        parts.append('')
        parts.append('def init(ContextInfo):')
        parts.append('    """策略初始化"""')
        parts.append('    ContextInfo.set_account(ACCOUNT_ID)')
        parts.append('')
        parts.append('    # 判断回测/实盘模式')
        parts.append('    gvar.is_backtest = ContextInfo.do_back_test')
        parts.append('    gvar.quick_trade = 0 if gvar.is_backtest else 1')
        parts.append(f"    print(f'{{STRATEGY_NAME}}: {{\"回测\" if gvar.is_backtest else \"实盘\"}}模式')")
        parts.append('')
        parts.append('    if gvar.is_backtest:')
        parts.append('        print(\'回测模式 — 通过 handlebar K线驱动\')')
        parts.append('    else:')
        parts.append('        print(\'实盘模式 — 通过 run_time 定时任务驱动\')')

        # 生成 run_time 调用（实盘模式）
        if analysis['timing_functions']:
            parts.append('')
            parts.append('        # ===== 实盘定时任务 =====')
            # Trade time
            for ttype, fname, params in analysis['timing_functions']:
                if ttype == 'run_daily':
                    time_str = self._parse_run_daily_time(params)
                    parts.append(f"        # 原: {ttype}({fname}, {params})")
                    parts.append(f"        _run_time = gvar.stg_start_dt.strftime('%Y-%m-%d') + ' ' + STRATEGY_TRADETIME")
                    parts.append(f"        ContextInfo.run_time('{fname}', '1nDay',"
                                 f" gvar.stg_start_dt.strftime('%Y-%m-%d') + ' {time_str}')")
                elif ttype == 'run_weekly':
                    parts.append(f"        # 原: {ttype}({fname}, {params})")
                    parts.append(f"        # 注: 大QMT 不直接支持指定星期，在 {fname} 内判断 weekday")
                    parts.append(f"        ContextInfo.run_time('{fname}', '1nDay',"
                                 f" gvar.stg_start_dt.strftime('%Y-%m-%d') + ' 09:30:00')")
                elif ttype == 'run_monthly':
                    parts.append(f"        # 原: {ttype}({fname}, {params})")
                    parts.append(f"        ContextInfo.run_time('{fname}', '1nDay',"
                                 f" gvar.stg_start_dt.strftime('%Y-%m-%d') + ' 09:30:00')")
            self._add_change(f'添加实盘 run_time 定时任务 ({len(analysis["timing_functions"])}个)')

        parts.append('')
        parts.append('')
        parts.append('def handlebar(ContextInfo):')
        parts.append('    """K线驱动回调（仅回测模式执行）"""')
        parts.append('    if not gvar.is_backtest:')
        parts.append('        return')
        parts.append('    today = timetag_to_datetime('
                      "ContextInfo.get_bar_timetag(ContextInfo.barpos), '%Y%m%d')")
        parts.append(f"    print(f'---{{today}}---')")

        # 如果有 handle_data，调用它
        if analysis['has_handle_data'] and 'handle_data' in functions:
            parts.append('    handle_data(ContextInfo)')

        parts.append('')

        # ---- 回测实盘一体说明 ----
        parts.append('# ========================================')
        parts.append('# 回测实盘一体说明')
        parts.append('# ========================================')
        parts.append('# 回测模式: handlebar() 按K线周期驱动策略逻辑')
        parts.append('# 实盘模式: init() 中的 ContextInfo.run_time() 定时任务驱动')
        parts.append('# 切换方式: 由 ContextInfo.do_back_test 自动判断')
        parts.append('# 回测模式 quick_trade=0，实盘模式 quick_trade=1')
        if analysis['timing_functions']:
            parts.append('# 实盘定时任务已在 init() 中注册:')
            for ttype, fname, params in analysis['timing_functions']:
                parts.append(f'#   {ttype}({fname}, {params})')
        parts.append('')

        # ---- 转换报告注释 ----
        parts.append('# ========================================')
        parts.append('# 转换信息')
        parts.append('# ========================================')
        parts.append(f"# 转换时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        parts.append(f'# 转换器版本: JQToDaQmtConverter V1.0')
        parts.append('# 原始平台: 聚宽 (JoinQuant)')
        parts.append('# 目标平台: 大QMT (内置 Python 策略引擎)')
        if analysis['timing_functions']:
            parts.append('# 定时任务:')
            for ttype, fname, params in analysis['timing_functions']:
                parts.append(f'#   {ttype}({fname}, {params})')
        parts.append('')

        return '\n'.join(parts)

    # ================================================================
    #  辅助：解析 run_daily 时间参数
    # ================================================================

    def _parse_run_daily_time(self, params: str) -> str:
        """解析 run_daily 的时间参数，返回 HH:MM:SS"""
        params = params.strip()

        # run_daily(func, time='09:30')
        time_match = re.search(r"time\s*=\s*['\"](\d{2}:\d{2})['\"]", params)
        if time_match:
            return time_match.group(1) + ':00'

        # run_daily(func, 'open') / run_daily(func, 'close') etc.
        for key, val in self.time_mapping.items():
            if key in params:
                return val

        # run_daily(func, time='every_bar')
        if 'every_bar' in params:
            return '09:30:00'

        return '09:30:00'

    def _parse_run_weekly_params(self, params: str):
        """解析 run_weekly 参数，返回 (weekday, time_str)"""
        parts = [p.strip() for p in params.split(',')]
        weekday = 1  # 默认周一
        time_str = '09:30:00'

        for p in parts:
            if 'weekday' in p:
                wm = re.search(r'weekday\s*=\s*(\d+)', p)
                if wm:
                    weekday = int(wm.group(1))
            elif 'time' in p:
                tm = re.search(r"time\s*=\s*['\"](\d{2}:\d{2})['\"]", p)
                if tm:
                    time_str = tm.group(1) + ':00'

        return weekday, time_str

    # ================================================================
    #  报告相关
    # ================================================================

    def _reset_report(self):
        self.conversion_report = {
            'api_mappings': [], 'warnings': [], 'errors': [],
            'changes': [], 'added_functions': [],
        }

    def _add_mapping(self, msg: str):
        if msg not in self.conversion_report['api_mappings']:
            self.conversion_report['api_mappings'].append(msg)

    def _add_warning(self, msg: str):
        if msg not in self.conversion_report['warnings']:
            self.conversion_report['warnings'].append(msg)

    def _add_change(self, msg: str):
        if msg not in self.conversion_report['changes']:
            self.conversion_report['changes'].append(msg)

    def get_conversion_report(self) -> Dict:
        return self.conversion_report

    def _print_report(self):
        report = self.conversion_report
        if report['api_mappings']:
            print('\n📋 API 映射:')
            for m in report['api_mappings']:
                print(f'  ✓ {m}')
        if report['changes']:
            print('\n🔧 变更:')
            for c in report['changes']:
                print(f'  • {c}')
        if report['warnings']:
            print('\n⚠️ 警告:')
            for w in report['warnings']:
                print(f'  ! {w}')


# ================================================================
#  便捷函数
# ================================================================

def convert_jq_to_daqmt(jq_code: str, account_id: str = 'xxxxxxxxxxxx') -> str:
    """快捷转换函数"""
    converter = JQToDaQmtConverter(verbose=False, account_id=account_id)
    return converter.convert(jq_code)


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('用法: python jq_to_daqmt.py <聚宽策略文件> [-o 输出文件]')
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = None
    if '-o' in sys.argv:
        idx = sys.argv.index('-o')
        if idx + 1 < len(sys.argv):
            output_file = sys.argv[idx + 1]

    with open(input_file, 'r', encoding='utf-8') as f:
        jq_code = f.read()

    converter = JQToDaQmtConverter(verbose=True)
    result = converter.convert(jq_code, output_file=output_file)

    if not output_file:
        print('\n' + '=' * 70)
        print(result)
