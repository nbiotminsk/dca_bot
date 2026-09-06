from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd


@dataclass
class SetupSignal:
    setup_type: Literal[
        "TRIPLE_GRID_TRAILING",
        "TRIPLE_GRID_CORRECTION",
        "DUAL_GRID_TRAILING",
        "DUAL_GRID_CORRECTION",
        "SWEEP_RECLAIM",
        "MANIPULATION",
        "AWAITING_BREAK_BELOW",
        "NONE",
    ]
    side: Literal["long", "short"]
    imp_start_time: pd.Timestamp
    imp_end_time: pd.Timestamp
    imp_start_price: float
    imp_peak_price: float
    imp_pct: float
    # Уровни
    entry_1: float
    tp_1: float
    entry_2: Optional[float] = None
    tp_2: Optional[float] = None
    entry_3: Optional[float] = None
    tp_3: Optional[float] = None
    stop_loss: float = 0.0
    # Безубыток (Sweep Reclaim)
    be_trigger: Optional[float] = None
    be_price: Optional[float] = None
    # Детали свипа / дивергенции
    sweep_price: Optional[float] = None
    sweep_pct: Optional[float] = None
    macd_divergent: bool = False
    description: str = ""
    # Двухуровневая сетка (minor до 24 свечей / major до 96 свечей)
    layer: Literal["minor", "major"] = "minor"
    p_0382: Optional[float] = None
    touched_0382: bool = True
    o1_filled: bool = False
    o2_filled: bool = False


@dataclass
class ActiveTradeMonitor:
    symbol: str
    setup_type: str = "IDLE"
    state: str = "TRAILING"  # "TRAILING", "O1_FILLED", "O2_FILLED", "O3_FILLED", "AWAITING_SWEEP_CLOSE", "SWEEP_RECLAIM_ACTIVE", "MANIPULATION_ACTIVE", "IDLE"
    o1_id: Optional[str] = None
    o2_id: Optional[str] = None
    o3_id: Optional[str] = None
    cur_peak: float = 0.0
    cur_e1: float = 0.0
    cur_tp1: float = 0.0
    cur_e2: float = 0.0
    cur_tp2: float = 0.0
    cur_e3: float = 0.0
    cur_tp3: float = 0.0
    imp_start_price: float = 0.0
    sl: float = 0.0
    q1: float = 0.0
    q2: float = 0.0
    q3: float = 0.0
    has_o2: bool = False
    has_o3: bool = False
    be_trigger: Optional[float] = None
    be_price: Optional[float] = None
    be_applied: bool = False
    tp_basket_applied: bool = False
    position_was_open: bool = False
    stop_bar_time: Optional[pd.Timestamp] = None
    stop_sweep_low: float = 0.0
    last_candle_time: Optional[pd.Timestamp] = None
    imp_start_time: Optional[pd.Timestamp] = None
    imp_end_time: Optional[pd.Timestamp] = None
    close_only: bool = False
    done: bool = False
    layer: Literal["minor", "major"] = "minor"
    side: Literal["long", "short"] = "long"
    p_0382: Optional[float] = None
    touched_0382: bool = True
    timeout_hours: Optional[int] = None
    last_skipped_imp_time: Optional[pd.Timestamp] = None

    @property
    def is_active(self) -> bool:
        """Определяет, занят ли данный монитор активной сеткой ордеров или открытой позицией."""
        if self.done:
            return False
        if self.state in (
            "TRAILING",
            "O1_FILLED",
            "O2_FILLED",
            "BOTH_FILLED",
            "O3_FILLED",
            "AWAITING_SWEEP_CLOSE",
            "SWEEP_RECLAIM_ACTIVE",
            "MANIPULATION_ACTIVE",
        ):
            return True
        if any((self.o1_id, self.o2_id, self.o3_id)):
            return True
        return False
