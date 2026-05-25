"""
MiniPTrade 核心对象: Position, Portfolio, Blotter, Context
"""
from __future__ import annotations

import pandas as pd


class Position:
    __slots__ = ('stock', 'amount', 'cost_basis', 'enable_amount')

    def __init__(self, stock: str = '', amount: int = 0,
                 cost_basis: float = 0.0, enable_amount: int = 0):
        self.stock = stock
        self.amount = amount
        self.cost_basis = cost_basis
        self.enable_amount = enable_amount

    def __repr__(self):
        return (f"Position({self.stock}, amount={self.amount}, "
                f"cost={self.cost_basis:.2f})")


class Portfolio:
    def __init__(self, starting_cash: float, data_loader=None):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self._positions: dict[str, Position] = {}
        self._data_loader = data_loader
        self._current_dt: pd.Timestamp | None = None

    def set_current_dt(self, dt: pd.Timestamp):
        self._current_dt = dt

    @property
    def portfolio_value(self) -> float:
        total = self.cash
        for code, pos in self._positions.items():
            if pos.amount > 0 and self._data_loader and self._current_dt:
                price = self._data_loader.get_price(code, self._current_dt)
                total += pos.amount * price
        return total

    def get_position(self, security: str) -> Position:
        if security not in self._positions:
            self._positions[security] = Position(
                stock=security, amount=0, cost_basis=0.0, enable_amount=0
            )
        return self._positions[security]

    def get_positions(self) -> dict[str, Position]:
        return {k: v for k, v in self._positions.items() if v.amount > 0}

    def set_position(self, code: str, amount: int, cost_basis: float = 0.0):
        pos = self.get_position(code)
        pos.amount = amount
        pos.cost_basis = cost_basis
        pos.enable_amount = amount


class Blotter:
    def __init__(self):
        self.current_dt: pd.Timestamp = pd.Timestamp.now()

    def set_dt(self, dt: pd.Timestamp):
        self.current_dt = dt


class Context:
    def __init__(self, portfolio: Portfolio, blotter: Blotter):
        self.portfolio = portfolio
        self.blotter = blotter

    @property
    def current_dt(self) -> pd.Timestamp:
        return self.blotter.current_dt

    @current_dt.setter
    def current_dt(self, value: pd.Timestamp):
        value = pd.Timestamp(value)
        self.blotter.current_dt = value
        self.portfolio.set_current_dt(value)
