import pandas as pd

from altcoin_trend.features.indicators import add_ema, atr, adx, true_range


def test_add_ema_adds_output_and_increases_for_rising_close():
    frame = pd.DataFrame({"close": [1.0, 2.0, 3.0]})

    result = add_ema(frame, "close", span=2, output="ema_2")

    assert "ema_2" in result
    assert result["ema_2"].iloc[0] == 1.0
    assert result["ema_2"].iloc[-1] > result["ema_2"].iloc[0]


def test_true_range_uses_previous_close():
    frame = pd.DataFrame(
        {
            "high": [11.0, 12.0],
            "low": [9.0, 9.0],
            "close": [10.0, 10.0],
        }
    )

    assert list(true_range(frame)) == [2.0, 3.0]


def test_atr_uses_rolling_mean_with_min_periods_one():
    frame = pd.DataFrame(
        {
            "high": [11.0, 12.0],
            "low": [9.0, 8.0],
            "close": [10.0, 9.0],
        }
    )

    result = atr(frame, window=2)

    assert list(result) == [2.0, 3.0]
    assert result.iloc[-1] == 3.0


def test_adx_returns_same_length_and_non_null_values():
    frame = pd.DataFrame(
        {
            "high": [10.0, 11.0, 12.0],
            "low": [9.0, 9.5, 10.0],
            "close": [9.5, 10.5, 11.0],
        }
    )

    result = adx(frame, window=3)

    assert len(result) == len(frame)
    assert result.notna().all()


def test_true_range_uses_first_row_high_low_when_previous_close_missing():
    frame = pd.DataFrame(
        {
            "high": [5.0],
            "low": [2.0],
            "close": [4.0],
        }
    )

    assert list(true_range(frame)) == [3.0]


def test_indicator_functions_return_empty_series_for_empty_input():
    frame = pd.DataFrame(columns=["high", "low", "close"])

    tr = true_range(frame)
    atr_result = atr(frame, window=3)
    adx_result = adx(frame, window=3)

    assert tr.empty
    assert atr_result.empty
    assert adx_result.empty
    assert tr.dtype == float
    assert atr_result.dtype == float
    assert adx_result.dtype == float
