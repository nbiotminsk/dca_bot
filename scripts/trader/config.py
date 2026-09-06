from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import yaml
from rich.console import Console

root_dir = Path(__file__).resolve().parent.parent.parent
console = Console()


@dataclass
class TradeConfig:
    total_risk_usd: float = 2.0
    minor_risk_usd: float = 2.0
    major_risk_usd: float = 2.0
    manipulation_risk_usd: float = 2.0
    grid_weights: list[float] = field(default_factory=lambda: [0.50, 0.30, 0.20])
    entry_buffer_pct: float = 0.10
    entry_buffer_0500_pct: float = 0.10
    entry_buffer_0618_pct: float = 0.15
    entry_buffer_0786_pct: float = 0.15
    entry_buffer_1414_pct: float = 0.10
    entry_buffer_1618_pct: float = 0.10
    tp_buffer_pct: float = 0.10
    reclaim_tp_buffer_pct: float = 2.0
    reclaim_be_trigger_fib: float = 0.786
    reclaim_be_offset_pct: float = 0.05
    reclaim_max_sweep_pct: float = 0.5
    reclaim_allow_close_below: bool = False
    preferred_side: Literal["long", "short"] = "long"
    min_impulse_pct: float = 2.0
    atr_multiplier: float = 2.5
    timeout_hours: int = 24
    minor_timeout_hours: int = 24
    major_timeout_hours: int = 96
    lookback_bars: int = 120
    max_impulse_bars: int = 24
    minor_max_impulse_bars: int = 24
    major_max_impulse_bars: int = 96
    timeframe: str = "1h"
    scale: Literal["log", "linear"] = "log"
    symbols: list[str] = field(default_factory=list)
    mutual_exclusion: bool = True
    config_path: Optional[str] = None


def load_trade_config(config_path: Optional[str | Path] = None) -> TradeConfig:
    """Загружает параметры стратегии и риск-менеджмента из файла YAML."""
    default_path = root_dir / "config" / "trade_config.yaml"
    path = Path(config_path) if config_path else default_path

    cfg = TradeConfig()
    if not path.exists():
        return cfg

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        cfg.config_path = str(path)

        risk_data = data.get("risk", {})
        if "total_risk_usd" in risk_data:
            cfg.total_risk_usd = float(risk_data["total_risk_usd"])
            cfg.minor_risk_usd = float(risk_data["total_risk_usd"])
        if "minor_risk_usd" in risk_data:
            cfg.minor_risk_usd = float(risk_data["minor_risk_usd"])
            cfg.total_risk_usd = float(risk_data["minor_risk_usd"])
        if "major_risk_usd" in risk_data:
            cfg.major_risk_usd = float(risk_data["major_risk_usd"])
        if "manipulation_risk_usd" in risk_data:
            cfg.manipulation_risk_usd = float(risk_data["manipulation_risk_usd"])
        if "grid_weights" in risk_data:
            raw_w = risk_data["grid_weights"]
            if isinstance(raw_w, list) and len(raw_w) == 3:
                cfg.grid_weights = [float(x) for x in raw_w]

        buffer_data = data.get("buffers", {})
        if "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_pct = float(buffer_data["entry_buffer_pct"])
        if "entry_buffer_0500_pct" in buffer_data:
            cfg.entry_buffer_0500_pct = float(buffer_data["entry_buffer_0500_pct"])
        elif "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_0500_pct = cfg.entry_buffer_pct
        if "entry_buffer_0618_pct" in buffer_data:
            cfg.entry_buffer_0618_pct = float(buffer_data["entry_buffer_0618_pct"])
        elif "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_0618_pct = cfg.entry_buffer_pct
        if "entry_buffer_0786_pct" in buffer_data:
            cfg.entry_buffer_0786_pct = float(buffer_data["entry_buffer_0786_pct"])
        elif "entry_buffer_0718_pct" in buffer_data:
            cfg.entry_buffer_0786_pct = float(buffer_data["entry_buffer_0718_pct"])
        elif "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_0786_pct = cfg.entry_buffer_pct

        if "entry_buffer_1414_pct" in buffer_data:
            cfg.entry_buffer_1414_pct = float(buffer_data["entry_buffer_1414_pct"])
        elif "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_1414_pct = cfg.entry_buffer_pct
        if "entry_buffer_1618_pct" in buffer_data:
            cfg.entry_buffer_1618_pct = float(buffer_data["entry_buffer_1618_pct"])
        elif "entry_buffer_pct" in buffer_data:
            cfg.entry_buffer_1618_pct = cfg.entry_buffer_pct

        if "tp_buffer_pct" in buffer_data:
            cfg.tp_buffer_pct = float(buffer_data["tp_buffer_pct"])
        if "reclaim_tp_buffer_pct" in buffer_data:
            cfg.reclaim_tp_buffer_pct = float(buffer_data["reclaim_tp_buffer_pct"])
        if "reclaim_be_trigger_fib" in buffer_data:
            cfg.reclaim_be_trigger_fib = float(buffer_data["reclaim_be_trigger_fib"])
        if "reclaim_be_offset_pct" in buffer_data:
            cfg.reclaim_be_offset_pct = float(buffer_data["reclaim_be_offset_pct"])
        if "reclaim_max_sweep_pct" in buffer_data:
            cfg.reclaim_max_sweep_pct = float(buffer_data["reclaim_max_sweep_pct"])
        if "reclaim_allow_close_below" in buffer_data:
            cfg.reclaim_allow_close_below = bool(buffer_data["reclaim_allow_close_below"])

        strat_data = data.get("strategy", {})
        if "preferred_side" in strat_data:
            s_side = str(strat_data["preferred_side"]).lower()
            if s_side in ("long", "short"):
                cfg.preferred_side = s_side  # type: ignore[assignment]
        if "min_impulse_pct" in strat_data:
            cfg.min_impulse_pct = float(strat_data["min_impulse_pct"])
        if "atr_multiplier" in strat_data:
            val_atr = strat_data["atr_multiplier"]
            cfg.atr_multiplier = float(val_atr) if val_atr is not None else 0.0
        if "timeout_hours" in strat_data:
            val_to = strat_data["timeout_hours"]
            cfg.timeout_hours = int(val_to) if val_to is not None else 0
            cfg.minor_timeout_hours = cfg.timeout_hours
        if "minor_timeout_hours" in strat_data:
            val_mto = strat_data["minor_timeout_hours"]
            cfg.minor_timeout_hours = int(val_mto) if val_mto is not None else 0
            cfg.timeout_hours = cfg.minor_timeout_hours
        if "major_timeout_hours" in strat_data:
            val_majto = strat_data["major_timeout_hours"]
            cfg.major_timeout_hours = int(val_majto) if val_majto is not None else 0
        if "lookback_bars" in strat_data:
            cfg.lookback_bars = int(strat_data["lookback_bars"])
        if "max_impulse_bars" in strat_data:
            cfg.max_impulse_bars = int(strat_data["max_impulse_bars"])
            cfg.minor_max_impulse_bars = int(strat_data["max_impulse_bars"])
        if "minor_max_impulse_bars" in strat_data:
            cfg.minor_max_impulse_bars = int(strat_data["minor_max_impulse_bars"])
            cfg.max_impulse_bars = cfg.minor_max_impulse_bars
        if "major_max_impulse_bars" in strat_data:
            cfg.major_max_impulse_bars = int(strat_data["major_max_impulse_bars"])
        if "timeframe" in strat_data:
            cfg.timeframe = str(strat_data["timeframe"])
        if "scale" in strat_data:
            s_scale = str(strat_data["scale"]).lower()
            if s_scale in ("log", "linear"):
                cfg.scale = s_scale  # type: ignore[assignment]
        if "symbols" in strat_data:
            raw_syms = strat_data["symbols"]
            if isinstance(raw_syms, list):
                cfg.symbols = [str(s).strip() for s in raw_syms if str(s).strip()]
            elif isinstance(raw_syms, str):
                cfg.symbols = [s.strip() for s in raw_syms.split(",") if s.strip()]
        elif "symbol" in strat_data:
            raw_sym = str(strat_data["symbol"]).strip()
            if raw_sym:
                cfg.symbols = [raw_sym]
        if "mutual_exclusion" in strat_data:
            cfg.mutual_exclusion = bool(strat_data["mutual_exclusion"])

    except Exception as e:
        console.print(f"[yellow]⚠️ Ошибка при загрузке конфига {path}: {e}. Используются значения по умолчанию.[/yellow]")

    return cfg
