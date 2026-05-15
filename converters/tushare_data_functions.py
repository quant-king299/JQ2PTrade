#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JQ 数据函数的 Tushare 替代实现
这些函数会被注入到转换后的 PTrade 策略文件中

每个函数都是独立的，依赖 tushare（PTrade 已预装）
"""

# ============================================================================
# Tushare 初始化
# ============================================================================

_TUSHARE_FUNCTIONS = '''
# ======================================================================
# Tushare 数据函数（替代聚宽缺失 API）
# 注意: 请将下方 test_token 替换为你自己的 Tushare Token
# 获取地址: https://tushare.pro/register
# ======================================================================
import tushare as ts
ts.set_token('test_token')
_ts_pro = ts.pro_api()

def _ts_today():
    """获取当前日期字符串 YYYYMMDD"""
    import datetime
    return datetime.datetime.now().strftime('%%Y%%m%%d')

def _ts_code_convert(jq_code):
    """聚宽代码 -> Tushare代码: 000001.XSHE -> 000001.SZ"""
    if '.' in jq_code:
        code, suffix = jq_code.split('.')
        if suffix in ('XSHE', 'SZ'):
            return code + '.SZ'
        elif suffix in ('XSHG', 'SS', 'SH'):
            return code + '.SH'
    return jq_code

def _jq_code_convert(ts_code):
    """Tushare代码 -> PTrade代码: 000001.SZ -> 000001.SZ, 600000.SH -> 600000.SS"""
    if '.' in ts_code:
        code, suffix = ts_code.split('.')
        if suffix == 'SH':
            return code + '.SS'
    return ts_code

def _batch_ts_codes(jq_codes):
    """批量转换代码格式"""
    return [_ts_code_convert(c) for c in jq_codes]

def _batch_jq_codes(ts_codes):
    """批量转换回聚宽格式"""
    return [_jq_code_convert(c) for c in ts_codes]
'''

# ============================================================================
# get_extras 替代
# ============================================================================

_GET_EXTRAS = '''
def get_extras(field, security_list=None, end_date=None, count=1):
    """
    替代聚宽 get_extras()
    支持字段: is_st, acc_net_value, unit_net_value, futures_sett_price
    """
    import pandas as pd
    today = end_date.strftime('%Y%m%d') if end_date else _ts_today()

    if field == 'is_st':
        # 通过股票名称判断ST
        try:
            df = _ts_pro.namechange(ts_code='', fields='ts_code,name')
            st_stocks = set(df[df['name'].str.contains('ST', na=False)]['ts_code'].tolist())
            codes = _batch_ts_codes(security_list) if security_list else list(st_stocks)
            result = pd.Series({c: (_ts_code_convert(c) in st_stocks) for c in (security_list or codes)}, dtype=bool)
            return result
        except Exception:
            return pd.Series(dtype=bool)

    elif field in ('acc_net_value', 'unit_net_value'):
        # 基金净值
        try:
            codes = _batch_ts_codes(security_list) if security_list else []
            if not codes:
                return pd.DataFrame()
            all_data = []
            for code in codes:
                df = _ts_pro.fund_nav(ts_code=code, end_date=today)
                if df is not None and not df.empty:
                    all_data.append(df.head(count))
            if all_data:
                result = pd.concat(all_data, ignore_index=True)
                col = 'accum_nav' if field == 'acc_net_value' else 'unit_nav'
                return result.set_index('end_date')[col] if col in result.columns else pd.Series(dtype=float)
            return pd.Series(dtype=float)
        except Exception:
            return pd.Series(dtype=float)

    elif field == 'futures_sett_price':
        try:
            codes = _batch_ts_codes(security_list) if security_list else []
            all_data = []
            for code in codes:
                df = _ts_pro.futures_daily(ts_code=code, end_date=today, limit=count)
                if df is not None and not df.empty:
                    all_data.append(df)
            if all_data:
                result = pd.concat(all_data, ignore_index=True)
                return result.set_index('trade_date')['settle'] if 'settle' in result.columns else pd.Series(dtype=float)
            return pd.Series(dtype=float)
        except Exception:
            return pd.Series(dtype=float)

    return pd.Series(dtype=float)
'''

# ============================================================================
# get_concept_stocks / get_concepts 替代
# ============================================================================

_GET_CONCEPT_FUNCTIONS = '''
def get_concepts():
    """替代聚宽 get_concepts() - 获取所有概念板块列表"""
    try:
        df = _ts_pro.concept(fields='code,name')
        if df is not None and not df.empty:
            df['src'] = 'TS'
            return df.set_index('code')
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()

def get_concept_stocks(concept_code, date=None):
    """替代聚宽 get_concept_stocks() - 获取概念板块成分股"""
    import pandas as pd
    try:
        # concept_code 可以是代码或名称
        if not concept_code.endswith('.TI'):
            concept_code = concept_code if concept_code.startswith('TS') else 'TS' + concept_code

        df = _ts_pro.concept_detail(id=concept_code, fields='ts_code')
        if df is not None and not df.empty:
            codes = df['ts_code'].tolist()
            return _batch_jq_codes(codes)
        return []
    except Exception:
        return []
'''

# ============================================================================
# get_industry 替代
# ============================================================================

_GET_INDUSTRY = '''
# 申万行业股票映射缓存（tushare获取，回测期间只初始化一次）
_sw_ind_cache = None

def _init_sw_industry():
    """通过tushare获取申万一级行业成分股映射，缓存到全局变量"""
    global _sw_ind_cache
    if _sw_ind_cache is not None:
        return
    import pandas as pd
    _sw_ind_cache = {}
    _SW1_NAME_MAP = {
        '801010': '农林牧渔I', '801020': '采掘I', '801030': '化工I', '801040': '钢铁I',
        '801050': '有色金属I', '801060': '建筑建材I', '801070': '机械设备I', '801080': '电子I',
        '801090': '交运设备I', '801100': '信息设备I', '801110': '家用电器I', '801120': '食品饮料I',
        '801130': '纺织服装I', '801140': '轻工制造I', '801150': '医药生物I', '801160': '公用事业I',
        '801170': '交通运输I', '801180': '房地产I', '801190': '金融服务I', '801200': '商业贸易I',
        '801210': '休闲服务I', '801220': '信息服务I', '801230': '综合I',
        '801710': '建筑材料I', '801720': '建筑装饰I', '801730': '电气设备I', '801740': '国防军工I',
        '801750': '计算机I', '801760': '传媒I', '801770': '通信I', '801780': '银行I',
        '801790': '非银金融I', '801880': '汽车I', '801890': '机械设备I',
        '801950': '煤炭I', '801960': '石油石化I', '801970': '环保I', '801980': '美容护理I',
    }
    try:
        ind_df = _ts_pro.index_classify(level='L1', src='SW2021')
        for _, row in ind_df.iterrows():
            ts_ind_code = row['index_code']
            sw_code = ts_ind_code.replace('.SI', '')
            ind_name = _SW1_NAME_MAP.get(sw_code, row.get('industry_name', '') + 'I')
            try:
                members = _ts_pro.index_member_all(l1_code=ts_ind_code, fields='ts_code')
                if members is not None and not members.empty:
                    for ts_code in members['ts_code'].tolist():
                        code, suffix = ts_code.split('.')
                        ptrade_code = code + ('.SS' if suffix == 'SH' else '.' + suffix)
                        _sw_ind_cache[ptrade_code] = {'industry_code': sw_code, 'industry_name': ind_name}
            except Exception:
                pass
        log.info(f'申万行业映射初始化完成，共{len(_sw_ind_cache)}只股票')
    except Exception as e:
        log.info(f'申万行业映射初始化失败: {e}')

def get_industry(security=None, date=None):
    """替代聚宽 get_industry() - 使用tushare申万行业数据，返回PTrade代码格式"""
    _init_sw_industry()
    if security is None or _sw_ind_cache is None:
        return {}
    codes = security if isinstance(security, list) else [security]
    result = {}
    for code in codes:
        if code in _sw_ind_cache:
            result[code] = {'sw_l1': _sw_ind_cache[code]}
    return result
'''

# ============================================================================
# get_fundamentals_continuously 替代
# ============================================================================

_GET_FUNDAMENTALS_CONTINUOUSLY = '''
def get_fundamentals_continuously(security_list, table, fields=None,
                                   end_date=None, count=10):
    """替代聚宽 get_fundamentals_continuously() - 获取多日财务数据"""
    import pandas as pd
    try:
        today = end_date.strftime('%Y%m%d') if end_date else _ts_today()
        codes = _batch_ts_codes(security_list) if security_list else []
        if not codes:
            return pd.DataFrame()

        if table == 'indicator' or table == 'valuation':
            all_data = []
            for code in codes:
                df = _ts_pro.fina_indicator(ts_code=code, end_date=today,
                                             fields='ts_code,ann_date,end_date,' + (','.join(fields) if fields else ''))
                if df is not None and not df.empty:
                    all_data.append(df.head(count))
            if all_data:
                result = pd.concat(all_data, ignore_index=True)
                result.index = _batch_jq_codes(result['ts_code'].tolist())
                return result
        else:
            # 其他表使用 get_fundamentals 循环调用
            all_data = []
            trade_days = get_trade_days(end_date=end_date, count=count)
            for day in trade_days:
                df = get_fundamentals(security_list, table, fields=fields,
                                       date=day, is_dataframe=True)
                if df is not None and not df.empty:
                    df['date'] = day
                    all_data.append(df)
            if all_data:
                return pd.concat(all_data, ignore_index=True)

        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()
'''

# ============================================================================
# get_factor_values 替代
# ============================================================================

_GET_FACTOR_VALUES = '''
# 聚宽因子名 -> Tushare fina_indicator 字段映射
_FACTOR_MAP = {
    'pe_ratio': None,  # 需从 daily_basic 获取
    'pb_ratio': None,  # 需从 daily_basic 获取
    'market_cap': None,  # 从 daily_basic 获取
    'circulating_market_cap': None,  # 从 daily_basic 获取
    'roe': 'roe',
    'roa': 'roa',
    'eps': 'eps',
    'inc_revenue_year_on_year': 'or_yoy',
    'inc_net_profit_year_on_year': 'netprofit_yoy',
    'operating_revenue_grow_rate': 'or_yoy',
    'net_profit_growth_rate': 'netprofit_yoy',
    'total_profit_growth_rate': 'profit_dedt_yoy',
    'sales_growth': 'or_yoy',
    'current_ratio': 'current_ratio',
    'quick_ratio': 'quick_ratio',
    'debt_to_asset_ratio': 'debt_to_assets',
    'gross_income_ratio': 'grossprofit_margin',
    'roe_ttm': None,  # fina_indicator无权限，改用daily_basic的pb/pe_ttm反算
    'cash_rate_of_sales': 'ocf_to_or',
}

# 财务指标季度缓存（避免每个回测天重复查询）
_fina_indicator_cache = {}

def get_factor_values(security_list, factor_list, end_date=None, count=1):
    """替代聚宽 get_factor_values() - 批量获取因子数据"""
    import pandas as pd
    try:
        today = end_date.strftime('%%Y%%m%%d') if hasattr(end_date, 'strftime') else (end_date or _ts_today())
        if isinstance(factor_list, str):
            factor_list = [factor_list]

        jq_codes = set(security_list) if security_list else set()
        ts_codes = set(_batch_ts_codes(security_list)) if security_list else set()
        all_factors = {}

        # ---- 收集需要从各数据源获取的字段 ----
        indicator_factors = {}
        daily_basic_factors = {}
        _DB_FIELD_MAP = {
            'pe_ratio': 'pe', 'pb_ratio': 'pb',
            'market_cap': 'total_mv', 'circulating_market_cap': 'circ_mv',
            'ps_ratio': 'ps', 'turnover_ratio': 'turnover_rate',
        }
        for factor in factor_list:
            ts_field = _FACTOR_MAP.get(factor)
            if ts_field:
                indicator_factors[factor] = ts_field
            elif factor in _DB_FIELD_MAP:
                daily_basic_factors[factor] = _DB_FIELD_MAP[factor]

        # ---- 批量获取 fina_indicator（多策略组合 + 季度缓存） ----
        if indicator_factors:
            ind_fields = ','.join(set(indicator_factors.values()))
            try:
                today_val = int(today) if isinstance(today, str) and today.isdigit() else int(_ts_today())
                year = today_val // 10000
                cache_key = f'{year}_{ind_fields}'

                if cache_key not in _fina_indicator_cache:
                    all_ind_dfs = []
                    from datetime import datetime as _dt, timedelta as _td

                    # 方法1: period 批量查询（4个季度，不break，累积覆盖）
                    periods = [f'{year-1}1231', f'{year}0331',
                               f'{year-1}0930', f'{year-1}0630']
                    for period in periods:
                        try:
                            batch_df = _ts_pro.fina_indicator(
                                period=period,
                                fields=f'ts_code,{ind_fields}')
                            if batch_df is not None and not batch_df.empty:
                                all_ind_dfs.append(batch_df)
                        except Exception:
                            continue

                    # 方法2: start_date/end_date 范围查询（单次调用，覆盖180天公告）
                    try:
                        today_dt = _dt.strptime(str(today_val), '%Y%m%d')
                        start = (today_dt - _td(days=180)).strftime('%Y%m%d')
                        range_df = _ts_pro.fina_indicator(
                            start_date=start,
                            end_date=str(today_val),
                            fields=f'ts_code,{ind_fields}')
                        if range_df is not None and not range_df.empty:
                            all_ind_dfs.append(range_df)
                    except Exception:
                        pass

                    # 方法3: 逐只查询（batch全部失败且股票数<=600时自动降级）
                    if not all_ind_dfs and len(ts_codes) <= 600:
                        import time as _time
                        tc_list = list(ts_codes)[:600]
                        success_set = set()
                        consec_fail = 0
                        for i, tc in enumerate(tc_list):
                            # 连续失败5次 → 暂停2秒等限频窗口重置
                            if consec_fail >= 5:
                                _time.sleep(2)
                                consec_fail = 0
                            try:
                                row = _ts_pro.fina_indicator(ts_code=tc, fields=f'ts_code,{ind_fields}')
                                if row is not None and not row.empty:
                                    all_ind_dfs.append(row.head(1))
                                    success_set.add(tc)
                                    consec_fail = 0
                                else:
                                    consec_fail += 1
                            except Exception:
                                consec_fail += 1
                            _time.sleep(0.05)
                        # 第二轮：重试失败的（0.2s延时）
                        failed = [tc for tc in tc_list if tc not in success_set]
                        if failed:
                            consec_fail = 0
                            for tc in failed:
                                if consec_fail >= 3:
                                    _time.sleep(2)
                                    consec_fail = 0
                                try:
                                    row = _ts_pro.fina_indicator(ts_code=tc, fields=f'ts_code,{ind_fields}')
                                    if row is not None and not row.empty:
                                        all_ind_dfs.append(row.head(1))
                                        success_set.add(tc)
                                        consec_fail = 0
                                    else:
                                        consec_fail += 1
                                except Exception:
                                    consec_fail += 1
                                _time.sleep(0.2)

                    if all_ind_dfs:
                        merged = pd.concat(all_ind_dfs, ignore_index=True)
                        merged = merged.drop_duplicates(subset='ts_code', keep='first')
                        _fina_indicator_cache[cache_key] = merged
                    else:
                        _fina_indicator_cache[cache_key] = pd.DataFrame()

                ind_df = _fina_indicator_cache[cache_key]

                if ind_df is not None and not ind_df.empty:
                    ind_df = ind_df[ind_df['ts_code'].isin(ts_codes)]
                    jq_idx = _batch_jq_codes(ind_df['ts_code'].tolist())
                    ind_df.index = jq_idx
                    ind_df = ind_df[ind_df.index.isin(jq_codes)]
                    for factor, ts_field in indicator_factors.items():
                        if ts_field in ind_df.columns:
                            sub = ind_df[ind_df[ts_field].notna()]
                            if not sub.empty:
                                series = sub[ts_field]
                                all_factors[factor] = pd.DataFrame([series.values], columns=series.index)
                            else:
                                all_factors[factor] = pd.DataFrame()
                        else:
                            all_factors[factor] = pd.DataFrame()
                else:
                    for factor in indicator_factors:
                        all_factors[factor] = pd.DataFrame()
            except Exception:
                for factor in indicator_factors:
                    all_factors[factor] = pd.DataFrame()

        # ---- 批量获取 daily_basic（一次API调用获取所有股票） ----
        if daily_basic_factors:
            db_fields = ','.join(set(daily_basic_factors.values()))
            try:
                db_df = _ts_pro.daily_basic(trade_date=today,
                                            fields=f'ts_code,{db_fields}')
                if db_df is not None and not db_df.empty:
                    db_df = db_df[db_df['ts_code'].isin(ts_codes)]
                    jq_idx = _batch_jq_codes(db_df['ts_code'].tolist())
                    db_df.index = jq_idx
                    db_df = db_df[db_df.index.isin(jq_codes)]
                    for factor, ts_field in daily_basic_factors.items():
                        if ts_field in db_df.columns:
                            series = db_df[ts_field]
                            all_factors[factor] = pd.DataFrame([series.values], columns=series.index)
                        else:
                            all_factors[factor] = pd.DataFrame()
                else:
                    for factor in daily_basic_factors:
                        all_factors[factor] = pd.DataFrame()
            except Exception:
                for factor in daily_basic_factors:
                    all_factors[factor] = pd.DataFrame()

        # ---- roe_ttm 反算：ROE ≈ PB / PE_TTM（daily_basic有权限） ----
        if 'roe_ttm' in factor_list and 'roe_ttm' not in all_factors:
            try:
                db_roe = _ts_pro.daily_basic(trade_date=today, fields='ts_code,pe_ttm,pb')
                if db_roe is not None and not db_roe.empty:
                    db_roe = db_roe[db_roe['ts_code'].isin(ts_codes)]
                    db_roe = db_roe[(db_roe['pe_ttm'] > 0) & (db_roe['pb'] > 0)]
                    if not db_roe.empty:
                        db_roe['roe_calc'] = db_roe['pb'] / db_roe['pe_ttm']
                        jq_idx = _batch_jq_codes(db_roe['ts_code'].tolist())
                        db_roe.index = jq_idx
                        db_roe = db_roe[db_roe.index.isin(jq_codes)]
                        series = db_roe['roe_calc']
                        if not series.empty:
                            all_factors['roe_ttm'] = pd.DataFrame([series.values], columns=series.index)
            except Exception:
                pass

        # ---- 未映射的因子 ----
        for factor in factor_list:
            if factor not in all_factors:
                all_factors[factor] = pd.DataFrame()

        return all_factors
    except Exception:
        return {f: pd.DataFrame() for f in (factor_list if isinstance(factor_list, list) else [factor_list])}
'''

# ============================================================================
# order_target_percent 替代
# ============================================================================

_ORDER_TARGET_PERCENT = '''
def order_target_percent(security, percent, limit_price=None):
    """替代聚宽 order_target_percent() - 按组合比例调仓"""
    try:
        portfolio = get_portfolio()
        target_value = portfolio.portfolio_value * percent
        order_target_value(security, target_value, limit_price=limit_price)
    except Exception as e:
        log.warn(f"order_target_percent失败: {e}")
'''

# ============================================================================
# get_current_data 替代
# ============================================================================

_GET_CURRENT_DATA = '''
def get_current_data(security_list=None):
    """替代聚宽 get_current_data() - 获取实时/最近交易数据"""
    import pandas as pd
    try:
        if security_list is None:
            return {}
        codes = _batch_ts_codes(security_list) if isinstance(security_list, list) else [_ts_code_convert(security_list)]
        today = _ts_today()
        result = {}
        for jq_code, ts_code in zip(
            (security_list if isinstance(security_list, list) else [security_list]),
            codes
        ):
            df = _ts_pro.daily_basic(ts_code=ts_code, trade_date=today,
                                      fields='ts_code,close,pe,pb,turnover_rate,circ_mv,total_mv')
            if df is not None and not df.empty:
                row = df.iloc[0]
                close = row.get('close', 0)
                result[jq_code] = type('StockData', (), {
                    'last_price': close,
                    'high_limit': round(close * 1.1, 2) if close and close > 0 else 0,
                    'low_limit': round(close * 0.9, 2) if close and close > 0 else 0,
                    'paused': False,
                    'is_st': False,
                    'money': 0,
                    'name': jq_code,
                })()
        return result
    except Exception:
        return {}
'''

# ============================================================================
# check_limit_up 替代
# ============================================================================

_CHECK_LIMIT_UP = '''
def check_limit_up(security):
    """替代聚宽 check_limit_up() - 判断是否涨停"""
    try:
        df = get_history(1, '1d', ['close', 'high_limit'], stock_list=[security])
        if security in df and not df[security].empty:
            row = df[security].iloc[-1] if hasattr(df[security], 'iloc') else df[security]
            close = row['close'] if isinstance(row, dict) else getattr(row, 'close', 0)
            limit = row['high_limit'] if isinstance(row, dict) else getattr(row, 'high_limit', 0)
            return close >= limit and limit > 0
        return False
    except Exception:
        return False
'''

# ============================================================================
# normalize_code 替代
# ============================================================================

_NORMALIZE_CODE = '''
def normalize_code(code):
    """替代聚宽 normalize_code() - 标准化证券代码"""
    code = code.strip()
    if '.' in code:
        return code
    if code.startswith(('6', '9')):
        return code + '.XSHG'
    elif code.startswith(('0', '3')):
        return code + '.XSHE'
    return code + '.XSHG'
'''

# ============================================================================
# get_all_securities 替代（支持 types 参数）
# ============================================================================

_GET_ALL_SECURITIES = '''
def get_all_securities(types='stock', date=None):
    """替代聚宽 get_all_securities() - 支持多种证券类型"""
    import pandas as pd
    try:
        if types == 'stock' or types == ['stock']:
            df = _ts_pro.stock_basic(list_status='L', fields='ts_code,symbol,name,area,industry,list_date')
            if df is not None and not df.empty:
                df['src'] = 'TS'
                jq_codes = _batch_jq_codes(df['ts_code'].tolist())
                df.index = jq_codes
                return df
        elif types == 'fund' or types == ['fund']:
            df = _ts_pro.fund_basic(market='E', fields='ts_code,symbol,name,fund_type,issue_date')
            if df is not None and not df.empty:
                jq_codes = _batch_jq_codes(df['ts_code'].tolist())
                df.index = jq_codes
                return df
        elif types == 'index' or types == ['index']:
            df = _ts_pro.index_basic(market='SSE', fields='ts_code,name,publish_date')
            df2 = _ts_pro.index_basic(market='SZSE', fields='ts_code,name,publish_date')
            frames = [df, df2] if df2 is not None and not df2.empty else [df]
            if frames:
                combined = pd.concat(frames, ignore_index=True)
                combined.index = _batch_jq_codes(combined['ts_code'].tolist())
                return combined
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()
'''

# ============================================================================
# 函数注入配置：哪些 JQ 函数对应哪些代码模板
# ============================================================================

FUNCTION_MAP = {
    'get_extras': _GET_EXTRAS,
    'get_concept_stocks': _GET_CONCEPT_FUNCTIONS,
    'get_concepts': _GET_CONCEPT_FUNCTIONS,
    'get_industry': _GET_INDUSTRY,
    'get_fundamentals_continuously': _GET_FUNDAMENTALS_CONTINUOUSLY,
    'get_factor_values': _GET_FACTOR_VALUES,
    'order_target_percent': _ORDER_TARGET_PERCENT,
    'get_current_data': _GET_CURRENT_DATA,
    'check_limit_up': _CHECK_LIMIT_UP,
    'normalize_code': _NORMALIZE_CODE,
    'get_all_securities': _GET_ALL_SECURITIES,
}


def get_injection_code(required_functions, tushare_token=''):
    """
    根据需要的函数列表，生成要注入到策略中的代码

    Args:
        required_functions: 需要的 JQ 函数列表
        tushare_token: Tushare API token

    Returns:
        要注入的代码字符串
    """
    parts = []

    # 总是需要的工具函数
    needs_tushare_init = len(required_functions) > 0

    # 按组去重
    injected_templates = set()
    for func in required_functions:
        template = FUNCTION_MAP.get(func)
        if template and template not in injected_templates:
            injected_templates.add(template)
            parts.append(template)

    if not parts:
        return ''

    # 添加 tushare 初始化（token从环境变量读取，不硬编码）
    init_code = _TUSHARE_FUNCTIONS

    header = '# ======================================================================\n'
    header += '# 以下函数替代聚宽 API（基于 Tushare 实现，PTrade 已预装 tushare）\n'
    header += '# ======================================================================\n\n'

    return header + init_code + '\n'.join(parts)
