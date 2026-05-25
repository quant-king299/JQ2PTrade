"""
PTrade兼容补丁 - 修复SimTradeLab与真实PTrade的API差异

打补丁方式：在run_backtest.py中调用 apply_ptrade_patches()
不修改pip安装的SimTradeLab源码

修复内容:
1. get_history 多股票返回纵向堆叠DataFrame（带code列）
2. get_position 未持仓时返回空Position对象而非None
3. get_fundamentals 不支持的表名返回空DataFrame而非抛异常
"""

import pandas as pd
import numpy as np


def apply_ptrade_patches():
    """在SimTradeLab的PtradeAPI类上打猴子补丁"""
    from simtradelab.ptrade.api import PtradeAPI
    from simtradelab.ptrade.object import Position

    _orig_get_history = PtradeAPI.get_history
    _orig_get_position = PtradeAPI.get_position
    _orig_get_fundamentals = PtradeAPI.get_fundamentals

    def patched_get_history(self, count, frequency='1d', field=None,
                            security_list=None, fq=None, include=False,
                            fill='nan', is_dict=False):
        result = _orig_get_history(
            self, count, frequency, field, security_list,
            fq, include, fill, is_dict
        )

        # 只对多股票非dict返回值做转换
        is_multi_stock = (
            security_list is not None
            and not isinstance(security_list, str)
            and len(security_list) > 1
            and not is_dict
        )
        if not is_multi_stock:
            return result

        # PanelLike → 纵向堆叠DataFrame（带code列）
        # PanelLike 是 dict{field_name: DataFrame{stock_code: ndarray}}
        if isinstance(result, dict) and not isinstance(result, pd.DataFrame):
            all_rows = []
            fields_in_result = list(result.keys())

            # 获取股票列表（从第一个field的DataFrame列名）
            stock_list = []
            if fields_in_result:
                first_df = result[fields_in_result[0]]
                if isinstance(first_df, pd.DataFrame):
                    stock_list = list(first_df.columns)

            for stock in stock_list:
                row_dict = {'code': stock}
                for field_name in fields_in_result:
                    field_df = result[field_name]
                    if isinstance(field_df, pd.DataFrame) and stock in field_df.columns:
                        values = field_df[stock]
                        if isinstance(values, pd.Series):
                            row_dict[field_name] = values.values
                        else:
                            row_dict[field_name] = values
                    elif isinstance(field_df, dict) and stock in field_df:
                        row_dict[field_name] = field_df[stock]
                all_rows.append(pd.DataFrame(row_dict))

            if all_rows:
                return pd.concat(all_rows, ignore_index=True)
            return pd.DataFrame()

        return result

    def patched_get_position(self, security):
        pos = _orig_get_position(self, security)
        if pos is None:
            empty = Position(stock=security, amount=0, cost_basis=0)
            return empty
        return pos

    def patched_get_fundamentals(self, security, table, fields, date=None,
                                 start_year=None, end_year=None,
                                 report_types=None, date_type=None,
                                 merge_type=None, is_dataframe=None):
        try:
            return _orig_get_fundamentals(
                self, security, table, fields, date,
                start_year, end_year, report_types,
                date_type, merge_type, is_dataframe
            )
        except ValueError:
            # 不支持的表名（如income_statement），返回空DataFrame
            if isinstance(security, str):
                stocks = [security]
            else:
                stocks = security
            return pd.DataFrame({
                'code': stocks,
                **{f: [np.nan] * len(stocks) for f in fields}
            })

    PtradeAPI.get_history = patched_get_history
    PtradeAPI.get_position = patched_get_position
    PtradeAPI.get_fundamentals = patched_get_fundamentals

    print("[PTrade兼容] 补丁已应用: get_history多股票格式 + get_position空持仓 + get_fundamentals容错")
