from __future__ import annotations

import numpy as np
import pandas as pd


def add_ema(frame: pd.DataFrame, column: str, span: int, output: str) -> pd.DataFrame:
    result = frame.copy()
    result[output] = result[column].ewm(span=span, adjust=False, min_periods=1).mean()
    return result


def true_range(frame: pd.DataFrame) -> pd.Series:
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)

    previous_close = close.shift(1)
    first_range = high - low
    high_gap = (high - previous_close).abs()
    low_gap = (low - previous_close).abs()

    tr = pd.concat([first_range, high_gap, low_gap], axis=1).max(axis=1)
    tr.iloc[0] = first_range.iloc[0]
    return tr


def atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    return true_range(frame).rolling(window=window, min_periods=1).mean()


def adx(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)

    up_move = high.diff()
    down_move = low.shift(1) - low

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=frame.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=frame.index,
    )

    tr = true_range(frame)
    atr_series = tr.rolling(window=window, min_periods=1).mean()

    plus_di = 100 * plus_dm.rolling(window=window, min_periods=1).mean() / atr_series.replace(0, np.nan)
    minus_di = 100 * minus_dm.rolling(window=window, min_periods=1).mean() / atr_series.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_series = dx.rolling(window=window, min_periods=1).mean()
    return adx_series.fillna(0.0)
