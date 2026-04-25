import importlib.util
import os
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import text

from altcoin_trend.config import load_settings
from altcoin_trend.db import build_engine


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_ultra_signal_production.py"
_SPEC = importlib.util.spec_from_file_location("validate_ultra_signal_production", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


@pytest.mark.skipif(
    os.environ.get("ACTS_RUN_DB_SMOKE") != "1",
    reason="set ACTS_RUN_DB_SMOKE=1 to run real DB validation smoke",
)
def test_real_db_validation_smoke_generates_summary():
    try:
        settings = load_settings()
        engine = build_engine(settings)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"db unavailable: {exc}")

    try:
        with engine.begin() as connection:
            latest_ts = connection.execute(
                text("SELECT max(ts) FROM alt_core.market_1m WHERE exchange = 'binance'")
            ).scalar()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"db unavailable: {exc}")
    if latest_ts is None:
        pytest.skip("no binance market_1m data available")

    end = _MODULE.hour_bucket_start(latest_ts).to_pydatetime()
    start = (pd.Timestamp(end) - pd.Timedelta(days=1)).to_pydatetime()

    summary, rows = _MODULE.evaluate_signal_family(
        engine,
        "binance",
        start,
        end,
        signal_family="ultra_high_conviction",
    )

    assert summary["signal_family"] == "ultra_high_conviction"
    assert "precision_before_dd8" in summary
    assert isinstance(rows, list)
