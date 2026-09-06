def format_symbol(user_input: str) -> str:
    """Приводит пользовательский ввод монеты к формату Bybit USDT Linear (например, ZEC -> ZECUSDT, SUIUSDT.P -> SUIUSDT)."""
    clean = user_input.strip().upper().replace("/", "").replace("-", "")
    # Стрипаем суффиксы TradingView для фьючерсов (.P, .PERP)
    for suffix in (".PERP", ".P"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    if not clean.endswith("USDT") and not clean.endswith("PERP"):
        clean += "USDT"
    return clean
