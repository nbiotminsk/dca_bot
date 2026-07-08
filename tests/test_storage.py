from datetime import date
from decimal import Decimal

import pytest

from trade_tracker.models import Trade, TradeEntry
from trade_tracker.storage import (
    csv_columns, save_trade, load_trades, write_csv, write_json,
    trade_to_csv_row, csv_row_to_trade, DuplicateTradeError,
    rebuild_json_from_csv, csv_template,
)
from tests.helpers import make_bot, make_trade


def test_csv_columns_count():
    assert len(csv_columns()) == 22
    assert csv_columns()[0] == "date"
    assert csv_columns()[-1] == "notes"


def test_trade_to_csv_row_keys():
    row = trade_to_csv_row(make_trade())
    assert set(row.keys()) == set(csv_columns())
    assert row["entry_count"] == "2"
    assert row["bot_long_orders"] == "5"
    assert row["bot_base_qty_long"] == "0.04"
    assert row["notes"] == ""


def test_save_and_load_roundtrip(tmp_path):
    csv_p = tmp_path / "journal.csv"
    json_p = tmp_path / "journal.json"
    t = make_trade(notes="тест")
    save_trade(t, str(csv_p), str(json_p))
    loaded = load_trades(str(csv_p), str(json_p))
    assert len(loaded) == 1
    t2 = loaded[0]
    assert t2.key == t.key
    assert len(t2.entries) == 2  # из JSON-зеркала
    assert t2.notes == "тест"


def test_save_duplicate_raises(tmp_path):
    csv_p = tmp_path / "journal.csv"
    json_p = tmp_path / "journal.json"
    t = make_trade()
    save_trade(t, str(csv_p), str(json_p))
    with pytest.raises(DuplicateTradeError):
        save_trade(t, str(csv_p), str(json_p))


def test_save_different_side_ok(tmp_path):
    csv_p = tmp_path / "journal.csv"
    json_p = tmp_path / "journal.json"
    save_trade(make_trade(side="long"), str(csv_p), str(json_p))
    save_trade(make_trade(side="short"), str(csv_p), str(json_p))
    assert len(load_trades(str(csv_p), str(json_p))) == 2


def test_csv_headers_match(tmp_path):
    csv_p = tmp_path / "journal.csv"
    json_p = tmp_path / "journal.json"
    save_trade(make_trade(), str(csv_p), str(json_p))
    import csv as _csv

    with open(csv_p) as fh:
        reader = _csv.reader(fh)
        header = next(reader)
    assert header == csv_columns()


def test_csv_to_trade_roundtrip_synthetic_entries(tmp_path):
    csv_p = tmp_path / "journal.csv"
    json_p = tmp_path / "journal.json"
    t = make_trade()
    write_csv([t], str(csv_p))
    rebuild_json_from_csv(str(csv_p), str(json_p))
    loaded = load_trades(str(csv_p), str(json_p))
    assert len(loaded) == 1
    # Synthetic entries: одна точка с avg_entry
    assert len(loaded[0].entries) == 1
    # средневзвешенная: (2300*0.025 + 2200*0.050) / 0.075 ≈ 2233.333
    assert loaded[0].entries[0].price == pytest.approx(Decimal("2233.3333"), abs=Decimal("0.01"))


def test_csv_template_has_header():
    tmpl = csv_template()
    lines = tmpl.strip().splitlines()
    assert lines[0] == ",".join(csv_columns())
    assert len(lines) == 2  # header + empty row