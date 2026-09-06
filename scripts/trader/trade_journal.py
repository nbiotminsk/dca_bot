import json
import math
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from rich.console import Console

console = Console()

COMPLETED_IMPULSES_FILE = "data/trades/completed_impulses.json"


def parse_timestamp_utc(val: Any) -> pd.Timestamp:
    """Парсит временную метку и приводит ее к UTC."""
    ts = pd.to_datetime(val)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def load_completed_impulses(file_path: str = COMPLETED_IMPULSES_FILE) -> list[dict[str, Any]]:
    """Загружает список завершенных сделок и отработанных импульсов из JSON файла."""
    p = Path(file_path)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as e:
        console.print(f"[yellow]⚠️ Ошибка чтения {file_path}: {e}[/yellow]")
    return []


def save_completed_impulse(record: dict[str, Any], file_path: str = COMPLETED_IMPULSES_FILE) -> None:
    """Сохраняет отработанную сделку/импульс в JSON файл с защитой от дубликатов."""
    if float(record.get("peak_price", 0.0)) <= 0:
        return

    p = Path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = load_completed_impulses(file_path)

    # Проверка на дубликат по символу, вершине и времени вершины
    for r in existing:
        sym_match = (r.get("symbol") == record.get("symbol"))
        peak_match = False
        try:
            peak_match = math.isclose(float(r.get("peak_price", 0.0)), float(record.get("peak_price", 0.0)), rel_tol=1e-3)
        except Exception:
            pass
        time_match = (str(r.get("imp_end_time")) == str(record.get("imp_end_time")))
        if sym_match and peak_match and time_match:
            return  # Уже сохранен

    existing.append(record)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        console.print(f"[dim][💾 Записан отработанный импульс {record.get('symbol')} (вершина ${record.get('peak_price')}) в {file_path}][/dim]")
    except Exception as e:
        console.print(f"[red]❌ Ошибка записи в {file_path}: {e}[/red]")


def is_impulse_disqualified(
    imp_peak: float,
    imp_start_time: Any,
    imp_end_time: Any,
    symbol: str,
    completed_records: list[dict[str, Any]],
    layer: Optional[str] = None,
) -> bool:
    """
    Проверяет, не относится ли кандидат-импульс к уже отработанному импульсу (или его части/середине).
    Правило:
    - Запрещено входить в отработанную вершину (совпадение цены вершины).
    - Запрещено входить в подволны, завершившиеся до или на свече вершины отработанного импульса.
    - Новый вход рассматривается:
      если start_time строго позже imp_end_time отработанного импульса (imp_start_ts > rec_end_ts).
    - Слой (Minor / Major): отработанная сделка в Minor не блокирует независимый сетап в Major, и наоборот (если указан layer).
    """
    if not completed_records:
        return False

    imp_start_ts = parse_timestamp_utc(imp_start_time)

    for rec in completed_records:
        rec_sym = rec.get("symbol", "")
        if rec_sym:
            if not symbol or rec_sym != symbol:
                continue

        # Изоляция по слою: если слой передан и в записи указан слой, они должны совпадать
        rec_layer = rec.get("layer")
        if layer and rec_layer and layer != rec_layer:
            continue

        rec_peak = float(rec.get("peak_price", 0.0))
        rec_end_time = rec.get("imp_end_time")
        peak_matches = (rec_peak > 0 and math.isclose(imp_peak, rec_peak, rel_tol=1e-3))

        if rec_end_time:
            rec_end_ts = parse_timestamp_utc(rec_end_time)

            # Если импульс начался во времени строго ПОСЛЕ вершины отработанного:
            # это новый самостоятельный импульс (даже если цена вершины совпадает в боковике).
            if imp_start_ts > rec_end_ts:
                continue

            # Новый вход рассматривается ТОЛЬКО от следующей свечи после отработанного импульса:
            # запрещено входить в старый импульс, его часть или середину (imp_start_ts <= rec_end_ts).
            if imp_start_ts <= rec_end_ts:
                return True
        else:
            # Если время вершины не указано, проверяем совпадение цены вершины
            if peak_matches:
                return True

    return False

