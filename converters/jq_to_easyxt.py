"""
聚宽转EasyXT智能转换器 V2.0
将聚宽策略自动转换为可以在MiniQMT上运行的EasyXT策略

V2.0 更新:
- ✅ 交易API真实转换：order_* → api.buy/sell，不再是TODO
- ✅ 参数修正：移除JQ独有参数，日期格式转换
- ✅ 股票代码标准化：.XSHG→.SH, .XSHE→.SZ
- ✅ 兼容函数注入：get_current_data_compat()
- ✅ context访问转换：portfolio→api.get_account_asset/get_positions
- ✅ 不支持API清理：set_option/set_order_cost/enable_profile等
- ✅ 完善框架生成：可运行的EasyXT脚本

作者：王者Quant
版本：v2.0.0
"""

import re
import textwrap
from typing import Dict, List, Optional
from datetime import datetime


class JQToEasyXTConverter:
    """聚宽到EasyXT转换器 V2.0"""

    def __init__(self, verbose: bool = True, account_id: str = "YOUR_ACCOUNT_ID"):
        self.verbose = verbose
        self.account_id = account_id
        self.conversion_report = {
            'api_mappings': [],
            'warnings': [],
            'errors': [],
            'changes': [],
            'added_functions': [],
            'manual_fixes': []
        }

        # API映射表
        self.api_mapping = {
            'get_price': 'api.get_price',
            'get_bars': 'api.get_price',
            'history': 'api.get_price',
            'attribute_history': 'api.get_price',
            'get_trade_days': 'api.get_trading_dates',
            'get_trading_dates': 'api.get_trading_dates',
            'get_all_securities': 'api.get_stock_list',
            'get_security_info': 'api.get_stock_info',
        }

        # 不支持API — 直接移除
        self.unsupported_apis = [
            'set_option', 'set_order_cost', 'set_commission',
            'set_price_limit', 'enable_profile', 'log.set_level',
        ]

        # 注释掉的API
        self.comment_apis = ['set_benchmark', 'set_universe']

        # log映射
        self.log_mapping = {
            'log.info': 'print', 'log.warn': 'print',
            'log.error': 'print', 'log.debug': 'print',
        }

    # ================================================================
    #  主转换入口
    # ================================================================

    def convert(self, jq_code: str, output_file: Optional[str] = None) -> str:
        self._reset_report()

        if self.verbose:
            print("=" * 70)
            print("聚宽转EasyXT智能转换器 V2.0")
            print("=" * 70)

        # Step 1: 分析
        analysis = self._analyze_code(jq_code)

        # Step 2: 移除不支持API & 清理导入
        code = self._remove_unsupported(jq_code)

        # Step 3: 提取函数体（纯文本）和全局变量
        extracted = self._extract_code_blocks(code)

        # Step 4: 对每个函数体应用转换管道
        all_func_names = set(extracted['functions'].keys())
        converted_functions = {}
        for func_name, func_info in extracted['functions'].items():
            body = func_info['body']
            body = self._convert_function_body(body)
            body = self._convert_trading_apis(body)
            body = self._convert_context_access(body)
            # 独立 context → api（函数调用参数中）
            body = self._convert_bare_context(body)
            body = self._standardize_codes(body)
            body = self._fix_get_price_params(body)
            body = self._fix_date_formats(body)
            # 修正 history→get_price 的参数顺序
            body = self._fix_history_params(body)
            converted_functions[func_name] = {
                'body': body,
                'params': func_info['params'],
            }

        # Step 4.5: 修正函数调用中缺失的 api 参数
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
            with open(output_file, 'w', encoding='utf-8') as f:
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
            'timing_functions': [],  # [(type, func_name, params_str)]
        }

        if re.search(r'\b(order|order_value|order_target|order_target_value|order_target_percent)\s*\(', code):
            analysis['has_trading'] = True

        if re.search(r'\bget_current_data\s*\(', code):
            analysis['uses_get_current_data'] = True

        if re.search(r'\bget_fundamentals\s*\(|\bquery\s*\(', code):
            analysis['uses_fundamentals'] = True

        timing_patterns = [
            (r'run_daily\s*\(\s*(\w+)\s*,\s*([^)]+)\)', 'run_daily'),
            (r'run_weekly\s*\(\s*(\w+)\s*,\s*([^)]+)\)', 'run_weekly'),
            (r'run_monthly\s*\(\s*(\w+)\s*,\s*([^)]+)\)', 'run_monthly'),
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
        # 移除 jqdata/jqfactor 导入
        for pattern in [r'^import\s+jqdata.*\n?', r'^from\s+jqdata\s+import.*\n?',
                         r'^from\s+jqfactor\s+import.*\n?']:
            if re.search(pattern, code, re.MULTILINE):
                code = re.sub(pattern, '', code, flags=re.MULTILINE)
                self._add_change('移除导入: jqdata/jqfactor')

        # 移除不支持的API
        for api in self.unsupported_apis:
            pattern = rf'^[ \t]*{re.escape(api)}\s*\([^)]*\).*\n?'
            if re.search(pattern, code, re.MULTILINE):
                code = re.sub(pattern, '', code, flags=re.MULTILINE)
                self._add_change(f'移除不支持API: {api}()')

        # 注释掉不需要的API
        for api in self.comment_apis:
            pattern = rf'^([ \t]*)({re.escape(api)}\s*\([^)]*\))'
            if re.search(pattern, code, re.MULTILINE):
                code = re.sub(pattern, r'\1# \2  # EasyXT不需要',
                              code, flags=re.MULTILINE)
                self._add_change(f'注释: {api}()')

        return code

    # ================================================================
    #  Step 3: 提取代码块
    # ================================================================

    def _extract_code_blocks(self, code: str) -> Dict:
        """提取函数体（纯文本，保留原始缩进）和全局变量"""
        result = {'global_vars': [], 'functions': {}}

        lines = code.split('\n')
        i = 0
        global_lines = []

        while i < len(lines):
            line = lines[i]
            # 匹配函数定义，同时捕获完整参数列表
            func_match = re.match(r'def\s+(\w+)\s*\(([^)]*)\)\s*:', line)

            if func_match:
                # 保存函数前的全局变量（排除 import/from/注释/空行）
                for gl in global_lines:
                    stripped = gl.strip()
                    if not stripped:
                        continue
                    if stripped.startswith('#'):
                        continue
                    if stripped.startswith('import ') or stripped.startswith('from '):
                        continue
                    result['global_vars'].append(gl)
                global_lines = []

                func_name = func_match.group(1)
                func_params_str = func_match.group(2).strip()

                # 解析参数：移除 context，保留其余
                params = [p.strip() for p in func_params_str.split(',') if p.strip()]
                kept_params = [p for p in params
                               if p not in ('context',) and not p.startswith('context:')]
                # 始终在第一位插入 api
                new_params = ['api'] + kept_params

                body_lines = []
                i += 1
                while i < len(lines):
                    stripped = lines[i].strip()
                    if stripped == '' or stripped.startswith('#'):
                        body_lines.append(lines[i])
                        i += 1
                        continue
                    if re.match(r'^(def\s+|class\s+|@)', lines[i]):
                        break
                    # 检查缩进：函数体内容必须有缩进（或特殊关键字）
                    if lines[i] and (lines[i][0] in (' ', '\t') or
                                     stripped.startswith(('return ', 'if ', 'for ', 'while ',
                                                          'try:', 'except', 'else:', 'elif ',
                                                          'break', 'continue', 'pass'))):
                        body_lines.append(lines[i])
                        i += 1
                    else:
                        break

                # 清理 body 尾部：移除尾随的空行和纯注释
                body_stripped = body_lines[:]
                while body_stripped and (body_stripped[-1].strip() == '' or
                         body_stripped[-1].strip().startswith('#')):
                    # 只移除明显是"分隔符"的注释（非函数体内注释）
                    last = body_stripped[-1].strip()
                    if last == '' or re.match(r'^#\d+-\d+|^#={3,}|^#-{3,}', last):
                        body_stripped.pop()
                    else:
                        break

                result['functions'][func_name] = {
                    'body': '\n'.join(body_stripped),
                    'params': ', '.join(new_params),
                }
            else:
                global_lines.append(line)
                i += 1

        # 处理文件末尾的全局变量
        for gl in global_lines:
            stripped = gl.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.startswith('import ') or stripped.startswith('from '):
                continue
            result['global_vars'].append(gl)

        # 转换 g.xxx = yyy 为全局变量
        filtered_vars = []
        for gl in result['global_vars']:
            stripped = gl.strip()
            # 跳过 import/from（二次过滤）
            if stripped.startswith('import ') or stripped.startswith('from '):
                continue
            g_match = re.match(r'g\.(\w+)\s*=\s*(.+)', gl)
            if g_match:
                filtered_vars.append(f'{g_match.group(1)} = {g_match.group(2)}')
                self._add_mapping(f'g.{g_match.group(1)} → 全局变量')
            else:
                filtered_vars.append(gl)
        result['global_vars'] = filtered_vars

        return result

    # ================================================================
    #  Step 4: 转换函数体（文本进，文本出）
    # ================================================================

    def _convert_function_body(self, body: str) -> str:
        """API名称映射 + g.xxx清理 + log转换"""
        result_lines = []
        for line in body.split('\n'):
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                result_lines.append(line)
                continue

            new_line = line

            # g.xxx → xxx（排除 log.xxx）
            if 'g.' in stripped and not any(
                    kw in stripped for kw in ['log.', 'str.', '.g.']):
                new_line = re.sub(r'\bg\.(\w+)', r'\1', new_line)

            # log.xxx → print
            for jq_log, py_log in self.log_mapping.items():
                if f'{jq_log}(' in new_line:
                    new_line = new_line.replace(f'{jq_log}(', f'{py_log}(')
                    self._add_mapping(f'{jq_log}() → {py_log}()')

            # API 映射（最长优先 + 防子串误匹配：前面不能有 .）
            sorted_apis = sorted(self.api_mapping.items(),
                                 key=lambda x: len(x[0]), reverse=True)
            for jq_api, easyxt_api in sorted_apis:
                # 只匹配非方法调用：前面不能有 . 或字母
                pattern = rf'(?<![.\w]){re.escape(jq_api)}\s*\('
                if re.search(pattern, new_line):
                    new_line = re.sub(pattern, f'{easyxt_api}(', new_line)
                    self._add_mapping(f'{jq_api}() → {easyxt_api}()')

            # get_current_data() → get_current_data_compat(api, ...)
            if 'get_current_data()' in new_line:
                new_line = new_line.replace(
                    'get_current_data()', 'get_current_data_compat(api)')
                self._add_mapping('get_current_data() → get_current_data_compat(api)')

            result_lines.append(new_line)

        return '\n'.join(result_lines)

    # ================================================================
    #  Step 4b: 交易API转换
    # ================================================================

    def _convert_trading_apis(self, body: str) -> str:
        """智能转换所有JQ交易API → EasyXT api.buy/sell"""
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
        """order(security, amount) → api.buy/sell"""
        m = re.search(r'\border\s*\(\s*([^,]+)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)', stripped)
        if m:
            sec, amt = m.group(1), m.group(2)
            if float(amt) > 0:
                self._add_mapping(f'order({sec}, {amt}) → api.buy()')
                return f'{indent}api.buy(ACCOUNT_ID, {sec}, {int(float(amt))})'
            elif float(amt) < 0:
                self._add_mapping(f'order({sec}, {amt}) → api.sell()')
                return f'{indent}api.sell(ACCOUNT_ID, {sec}, {int(abs(float(amt)))})'
            else:
                return f'{indent}# order({sec}, 0) — 无需操作'
        self._add_warning(f'order() 格式无法识别: {stripped[:60]}')
        return f'{indent}{stripped}'

    def _conv_order_value(self, stripped: str, indent: str) -> str:
        """order_value(security, value) → 按金额计算股数买入"""
        m = re.search(r'order_value\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', stripped)
        if m:
            sec, val = m.group(1), m.group(2)
            self._add_mapping(f'order_value({sec}, {val}) → 按金额买入')
            return f"""{indent}# order_value({sec}, {val}) → 按金额买入
{indent}_price = api.get_current_price({sec})
{indent}if _price is not None and not _price.empty:
{indent}    _latest = float(_price.iloc[-1]) if hasattr(_price, "iloc") else float(_price)
{indent}    _volume = int(({val}) / _latest / 100) * 100
{indent}    if _volume > 0:
{indent}        api.buy(ACCOUNT_ID, {sec}, _volume)"""
        return f'{indent}{stripped}'

    def _conv_order_target(self, stripped: str, indent: str) -> str:
        """order_target(security, target) → 调整持仓"""
        m = re.search(r'order_target\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', stripped)
        if m:
            sec, target = m.group(1), m.group(2)
            if target.strip() == '0':
                self._add_mapping(f'order_target({sec}, 0) → 清仓卖出')
                return f"""{indent}# order_target({sec}, 0) → 清仓
{indent}_pos = api.get_positions(ACCOUNT_ID, {sec})
{indent}if _pos is not None and not _pos.empty:
{indent}    _vol = int(_pos.iloc[0]["volume"])
{indent}    if _vol > 0:
{indent}        api.sell(ACCOUNT_ID, {sec}, _vol)"""
            self._add_mapping(f'order_target({sec}, {target}) → 调仓')
            return f"""{indent}# order_target({sec}, {target}) → 调整持仓
{indent}_pos = api.get_positions(ACCOUNT_ID, {sec})
{indent}_current = int(_pos.iloc[0]["volume"]) if _pos is not None and not _pos.empty else 0
{indent}_diff = ({target}) - _current
{indent}if _diff > 0:
{indent}    api.buy(ACCOUNT_ID, {sec}, _diff)
{indent}elif _diff < 0:
{indent}    api.sell(ACCOUNT_ID, {sec}, abs(_diff))"""
        return f'{indent}{stripped}'

    def _conv_order_target_value(self, stripped: str, indent: str) -> str:
        """order_target_value(security, value) → 按金额调仓"""
        m = re.search(r'order_target_value\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', stripped)
        if m:
            sec, tval = m.group(1), m.group(2)
            if tval.strip() == '0':
                return self._conv_order_target(
                    stripped.replace('order_target_value', 'order_target'), indent)
            self._add_mapping(f'order_target_value({sec}, {tval}) → 按金额调仓')
            return f"""{indent}# order_target_value({sec}, {tval}) → 按金额调仓
{indent}_price = api.get_current_price({sec})
{indent}if _price is not None and not _price.empty:
{indent}    _latest = float(_price.iloc[-1]) if hasattr(_price, "iloc") else float(_price)
{indent}    _target_vol = int(({tval}) / _latest / 100) * 100
{indent}    _pos = api.get_positions(ACCOUNT_ID, {sec})
{indent}    _cur_vol = int(_pos.iloc[0]["volume"]) if _pos is not None and not _pos.empty else 0
{indent}    _diff = _target_vol - _cur_vol
{indent}    if _diff > 0:
{indent}        api.buy(ACCOUNT_ID, {sec}, _diff)
{indent}    elif _diff < 0:
{indent}        api.sell(ACCOUNT_ID, {sec}, abs(_diff))"""
        return f'{indent}{stripped}'

    def _conv_order_target_percent(self, stripped: str, indent: str) -> str:
        """order_target_percent(security, percent) → 百分比调仓"""
        m = re.search(r'order_target_percent\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)', stripped)
        if m:
            sec, pct = m.group(1), m.group(2)
            self._add_mapping(f'order_target_percent({sec}, {pct}) → 百分比调仓')
            return f"""{indent}# order_target_percent({sec}, {pct}) → 百分比调仓
{indent}_asset = api.get_account_asset(ACCOUNT_ID)
{indent}if _asset:
{indent}    _total = _asset.get("total_value", 0)
{indent}    _target_value = _total * ({pct})
{indent}    _price = api.get_current_price({sec})
{indent}    if _price is not None and not _price.empty:
{indent}        _latest = float(_price.iloc[-1]) if hasattr(_price, "iloc") else float(_price)
{indent}        _target_vol = int(_target_value / _latest / 100) * 100
{indent}        _pos = api.get_positions(ACCOUNT_ID, {sec})
{indent}        _cur_vol = int(_pos.iloc[0]["volume"]) if _pos is not None and not _pos.empty else 0
{indent}        _diff = _target_vol - _cur_vol
{indent}        if _diff > 0:
{indent}            api.buy(ACCOUNT_ID, {sec}, _diff)
{indent}        elif _diff < 0:
{indent}            api.sell(ACCOUNT_ID, {sec}, abs(_diff))"""
        return f'{indent}{stripped}'

    def _conv_cancel_order(self, stripped: str, indent: str) -> str:
        m = re.search(r'cancel_order\s*\(\s*([^)]+)\s*\)', stripped)
        if m:
            self._add_mapping(f'cancel_order({m.group(1)}) → api.cancel_order()')
            return f'{indent}api.cancel_order(ACCOUNT_ID, {m.group(1)})'
        return f'{indent}{stripped}'

    # ================================================================
    #  Step 4c: context 访问转换
    # ================================================================

    def _convert_context_access(self, body: str) -> str:
        """context.xxx → EasyXT API 调用"""
        context_map = [
            ('context.portfolio.available_cash',
             'api.get_account_asset(ACCOUNT_ID).get("available_cash", 0)'),
            ('context.portfolio.cash',
             'api.get_account_asset(ACCOUNT_ID).get("available_cash", 0)'),
            ('context.portfolio.total_value',
             'api.get_account_asset(ACCOUNT_ID).get("total_value", 0)'),
            ('context.portfolio.positions.keys()',
             'api.get_positions(ACCOUNT_ID).index'),
            ('context.portfolio.positions',
             'api.get_positions(ACCOUNT_ID)'),
            ('context.current_dt', 'datetime.now()'),
            ('context.current_date', 'datetime.now().date()'),
            ('context.previous_date', '(datetime.now() - timedelta(days=1)).date()'),
        ]

        for old, new in context_map:
            if old in body:
                body = body.replace(old, new)
                self._add_mapping(f'{old} → EasyXT API')

        return body

    # ================================================================
    #  Step 4c2: 独立 context → api
    # ================================================================

    def _convert_bare_context(self, body: str) -> str:
        """将函数调用中的 context 参数替换为 api（不触碰 context.xxx 和字符串）"""
        # 匹配作为函数参数的 context（前面是逗号/括号，后面是逗号/括号）
        body = re.sub(r'(?<!\w)(context)(?!\s*\.)(?=\s*[,)])', 'api', body)
        return body

    # ================================================================
    #  Step 4g: history/get_price 参数顺序修正
    # ================================================================

    def _fix_history_params(self, body: str) -> str:
        """JQ history(count, unit, field, security_list) → EasyXT get_price(codes, count, period, fields)"""
        # 匹配 api.get_price(N, unit='1m', field='close', security_list=xxx)
        # 或 api.get_price(stock, N, '1d', ['close'], ...)
        # JQ风格: history/get_price(count, unit=..., field=..., security_list=...)
        # EasyXT风格: get_price(codes, count=N, period='1d', fields=[...])
        pattern = r'api\.get_price\s*\(\s*(\d+)\s*,\s*unit\s*=\s*([^,)]+)\s*,\s*field\s*=\s*([^,)]+)\s*,\s*security_list\s*=\s*([^)]+)\)'
        if re.search(pattern, body):
            body = re.sub(pattern,
                          r'api.get_price(\4, count=\1, period=\2, fields=[\3])',
                          body)
            self._add_change('history参数重排: (count,unit,field,security_list) → (codes,count,period,fields)')
        return body

    # ================================================================
    #  Step 4h: 修正函数调用中缺失的 api 参数
    # ================================================================

    def _fix_func_calls(self, body: str, all_func_names: set) -> str:
        """对已转换为首参数 api 的函数，在调用处自动补上 api"""
        # 需要跳过的：内建函数、api.xxx 方法、已有 api 作为首参的调用
        skip_names = {'initialize', 'main', 'get_price', 'get_current_data',
                      'get_current_data_compat', 'get_trades', 'get_fundamentals',
                      'query', 'get_ols', 'get_zscore',
                      'get_factor_values', 'attribute_history', 'history',
                      'get_bars', 'get_all_securities', 'get_security_info',
                      'get_index_stocks', 'get_trade_days', 'get_trading_dates',
                      'get_snapshot', 'get_extras', 'get_industry',
                      'order', 'order_value', 'order_target', 'cancel_order',
                      'log'}

        for fname in sorted(all_func_names, key=len, reverse=True):
            if fname in skip_names:
                continue
            # 匹配非方法调用（前面没有 .）且首参不是 api
            pattern = rf'(?<![.\w])({re.escape(fname)})\s*\(\s*(?!api\b)'
            if re.search(pattern, body):
                body = re.sub(pattern, rf'\1(api, ', body)
                self._add_mapping(f'调用 {fname}() 补上 api 首参')

        return body

    # ================================================================
    #  Step 4d: 股票代码标准化
    # ================================================================

    def _standardize_codes(self, body: str) -> str:
        if '.XSHG' in body or '.XSHE' in body:
            body = body.replace('.XSHG', '.SH')
            body = body.replace('.XSHE', '.SZ')
            self._add_change('股票代码: .XSHG→.SH / .XSHE→.SZ')
        return body

    # ================================================================
    #  Step 4e: get_price 参数修正
    # ================================================================

    def _fix_get_price_params(self, body: str) -> str:
        for param in ['skip_paused', 'fq', 'panel', 'fill_paused']:
            if f'{param}=' in body:
                body = re.sub(rf',\s*{param}\s*=\s*[^,)\]]+', '', body)
                body = re.sub(rf'{param}\s*=\s*[^,)\]]+,\s*', '', body)
                self._add_change(f'移除参数: {param}')
        return body

    # ================================================================
    #  Step 4f: 日期格式修正
    # ================================================================

    def _fix_date_formats(self, body: str) -> str:
        pattern = r'(?:datetime\.)?date\s*\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)'
        if re.search(pattern, body):
            body = re.sub(pattern,
                          lambda m: f"'{m.group(1)}{m.group(2).zfill(2)}{m.group(3).zfill(2)}'",
                          body)
            self._add_change('datetime.date → 字符串日期')
        return body

    # ================================================================
    #  Step 5: 生成最终脚本
    # ================================================================

    def _generate_script(self, global_vars: List[str],
                         functions: Dict[str, str], analysis: Dict) -> str:
        parts = []

        # ---- 文件头 ----
        parts.extend([
            '# -*- coding: utf-8 -*-',
            '"""聚宽策略 → EasyXT 自动转换 (V2.0)"""',
            '',
            'import time',
            'import pandas as pd',
            'import numpy as np',
            'from datetime import datetime, timedelta',
            '',
            'from easy_xt import EasyXT',
            '',
            '# ========================================',
            '# 配置（请修改为实际值）',
            '# ========================================',
            f'ACCOUNT_ID = "{self.account_id}"  # TODO: 改为实际账户ID',
            '',
        ])

        # ---- 全局变量 ----
        parts.append('# ========================================')
        parts.append('# 全局变量（从 g.xxx 转换而来）')
        parts.append('# ========================================')
        if global_vars:
            parts.extend(global_vars)
        else:
            parts.append('# 无全局变量')
        parts.append('')

        # ---- 兼容函数 ----
        if analysis['uses_get_current_data']:
            parts.append(self._gen_current_data_compat())
            parts.append('')

        # ---- 策略函数 ----
        parts.append('# ========================================')
        parts.append('# 策略函数')
        parts.append('# ========================================')
        parts.append('')

        for func_name, func_info in functions.items():
            if func_name in ('initialize',):
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

        # ---- 主程序 ----
        parts.extend([
            '# ========================================',
            '# 主程序入口',
            '# ========================================',
            'def main():',
            '    """主策略"""',
            '    print("=" * 50)',
            '    print("聚宽→EasyXT 转换策略启动")',
            '    print("=" * 50)',
            '',
            '    # 初始化 EasyXT',
            '    api = EasyXT(ACCOUNT_ID)',
            '',
            '    # 初始化数据服务',
            '    if not api.init_data():',
            '        print("[ERROR] 数据服务初始化失败")',
            '        return',
            '',
        ])

        if analysis['has_trading']:
            parts.extend([
                '    # 初始化交易服务',
                '    if not api.init_trade():',
                '        print("[ERROR] 交易服务初始化失败")',
                '        return',
                '    api.add_account(ACCOUNT_ID)',
                '',
            ])

        # 定时任务说明
        if analysis['timing_functions']:
            parts.append('    # 原JQ定时任务:')
            for ttype, fname, params in analysis['timing_functions']:
                parts.append(f'    #   {ttype}({fname}, {params})')
            parts.append('')

        # 主循环
        parts.extend([
            '    print("开始主循环...")',
            '    while True:',
            '        try:',
        ])

        called = set()
        if analysis['timing_functions']:
            for _, fname, _ in analysis['timing_functions']:
                if fname not in called:
                    parts.append(f'            {fname}(api)')
                    called.add(fname)
        elif analysis['has_handle_data']:
            parts.append('            # handle_data 逻辑')
            parts.append('            handle_data(api)')
        else:
            for fname in functions:
                if fname not in ('initialize',) and fname not in called:
                    parts.append(f'            {fname}(api)')
                    called.add(fname)
                    break
            if not called:
                parts.append('            pass')

        parts.extend([
            '            time.sleep(60)',
            '        except KeyboardInterrupt:',
            '            print("\\n用户中断")',
            '            break',
            '        except Exception as e:',
            '            print(f"[ERROR] {e}")',
            '            time.sleep(60)',
            '',
            '',
            'if __name__ == "__main__":',
            '    main()',
            '',
        ])

        return '\n'.join(parts)

    # ================================================================
    #  兼容函数生成
    # ================================================================

    def _gen_current_data_compat(self) -> str:
        self._add_function('get_current_data_compat()')
        return '''# ========================================
# get_current_data 兼容函数
# ========================================
def get_current_data_compat(api, security_list=None):
    """模拟聚宽 get_current_data()，使用 EasyXT API"""
    if security_list is None:
        security_list = []
    if isinstance(security_list, str):
        security_list = [security_list]

    result = {}
    for code in security_list:
        try:
            df = api.get_current_price(code)
            if df is not None and not df.empty:
                row = df.iloc[-1]
                result[code] = {
                    'last_price': float(row.get('lastPrice', 0)),
                    'high_limit': float(row.get('highLimit', 0)) or float(row.get('lastPrice', 0)) * 1.1,
                    'low_limit': float(row.get('lowLimit', 0)) or float(row.get('lastPrice', 0)) * 0.9,
                    'paused': False,
                    'is_st': False,
                    'name': code,
                    'day_open': float(row.get('open', 0)),
                }
        except Exception:
            result[code] = {
                'last_price': 0, 'high_limit': 0, 'low_limit': 0,
                'paused': False, 'is_st': False, 'name': code, 'day_open': 0
            }
    return result'''

    # ================================================================
    #  报告系统
    # ================================================================

    def _reset_report(self):
        self.conversion_report = {
            'api_mappings': [], 'warnings': [], 'errors': [],
            'changes': [], 'added_functions': [], 'manual_fixes': []
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

    def _add_function(self, msg: str):
        if msg not in self.conversion_report['added_functions']:
            self.conversion_report['added_functions'].append(msg)

    def _print_report(self):
        report = self.conversion_report
        print("\n" + "=" * 70)
        print("📊 转换报告 V2.0")
        print("=" * 70)
        for title, key in [
            ('✅ API映射', 'api_mappings'),
            ('✏️ 代码变更', 'changes'),
            ('📦 注入函数', 'added_functions'),
            ('⚠️ 警告', 'warnings'),
        ]:
            items = report.get(key, [])
            if items:
                print(f"\n{title} ({len(items)}):")
                for item in items[:30]:
                    print(f"   - {item}")
                if len(items) > 30:
                    print(f"   ... 还有 {len(items) - 30} 项")
        print("\n" + "=" * 70)

    def get_conversion_report(self) -> Dict:
        return self.conversion_report
