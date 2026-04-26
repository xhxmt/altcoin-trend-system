from pathlib import Path


def test_altcoin_trend_service_contains_expected_runtime_lines():
    service_path = Path(__file__).resolve().parents[1] / "systemd/user/altcoin-trend.service"
    contents = service_path.read_text(encoding="utf-8")

    assert "/home/tfisher" not in contents
    assert "WorkingDirectory=%h/altcoin-trend-system" in contents
    assert "EnvironmentFile=-%h/.config/acts/acts.env" in contents
    assert "Environment=PYTHONPATH=%h/altcoin-trend-system/src" in contents
    assert "ExecStart=%h/altcoin-trend-system/.venv/bin/python -m altcoin_trend.daemon" in contents
