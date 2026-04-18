from pathlib import Path


def test_altcoin_trend_service_contains_expected_runtime_lines():
    service_path = Path(__file__).resolve().parents[1] / "systemd/user/altcoin-trend.service"
    contents = service_path.read_text(encoding="utf-8")

    assert "WorkingDirectory=/home/tfisher/altcoin-trend-system" in contents
    assert "EnvironmentFile=-%h/.config/acts/acts.env" in contents
    assert "Environment=PYTHONPATH=/home/tfisher/altcoin-trend-system/src" in contents
    assert "ExecStart=/home/tfisher/altcoin-trend-system/.venv/bin/python -m altcoin_trend.daemon" in contents
