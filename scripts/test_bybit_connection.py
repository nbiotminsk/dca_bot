"""Test script for verifying Bybit V5 API credentials via pybit."""

import os
import sys
from dotenv import load_dotenv
from pybit.unified_trading import HTTP
from pybit.exceptions import FailedRequestError, InvalidRequestError


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return key[:4] + "..." + key[-4:]


def main() -> int:
    load_dotenv()

    api_key = os.getenv("BYBIT_API_KEY", "").strip()
    api_secret = os.getenv("BYBIT_API_SECRET", "").strip()
    testnet_str = os.getenv("BYBIT_TESTNET", "false").strip().lower()
    demo_str = os.getenv("BYBIT_DEMO", "false").strip().lower()
    testnet = testnet_str in ("true", "1", "yes")
    demo = demo_str in ("true", "1", "yes")

    if not api_key or not api_secret:
        print("❌ ОШИБКА: API ключи Bybit не найдены в файле .env!")
        print("\nПожалуйста, заполните .env в корне проекта:")
        print("BYBIT_API_KEY=ваш_api_ключ")
        print("BYBIT_API_SECRET=ваш_api_секрет")
        print("BYBIT_DEMO=true     # для Demo Trading на bybit.com ($50,000 демо)")
        print("BYBIT_TESTNET=false # для отдельного testnet.bybit.com")
        return 1

    masked = mask_key(api_key)
    if demo:
        mode_str = "DEMO TRADING (api-demo.bybit.com)"
    elif testnet:
        mode_str = "TESTNET (api-testnet.bybit.com)"
    else:
        mode_str = "MAINNET (боевой аккаунт)"

    print(f"🔄 Подключение к Bybit V5: {mode_str}")
    print(f"🔑 API Key: {masked}")

    try:
        session = HTTP(
            testnet=testnet,
            demo=demo,
            api_key=api_key,
            api_secret=api_secret,
        )

        # 1. Проверяем баланс кошелька (пробуем UNIFIED, затем CONTRACT)
        account_type = "UNIFIED"
        wallet_resp = None
        try:
            wallet_resp = session.get_wallet_balance(accountType="UNIFIED")
        except Exception as e:
            # Если классический аккаунт
            try:
                wallet_resp = session.get_wallet_balance(accountType="CONTRACT")
                account_type = "CONTRACT"
            except Exception:
                raise e

        ret_code = wallet_resp.get("retCode", 0)
        if ret_code != 0:
            print(f"❌ Bybit API Error (code {ret_code}): {wallet_resp.get('retMsg')}")
            return 1

        print(f"✅ Успешное подключение! Тип аккаунта: {account_type}")

        # Выводим информацию по балансу
        list_data = wallet_resp.get("result", {}).get("list", [])
        if list_data:
            acc = list_data[0]
            total_equity = float(acc.get("totalEquity", 0.0) or 0.0)
            total_avail = float(acc.get("totalAvailableBalance", 0.0) or 0.0)
            unrealised_pnl = float(acc.get("totalPerpUPL", 0.0) or 0.0)

            print(f"\n📊 Состояние счета ({account_type}):")
            print(f"   Общий капитал (Equity):  ${total_equity:,.2f} USD")
            print(f"   Доступный баланс:        ${total_avail:,.2f} USD")
            print(f"   Нереализованный PnL:     ${unrealised_pnl:+,.2f} USD")

            # Список монет с ненулевым балансом
            coins = acc.get("coin", [])
            active_coins = [c for c in coins if float(c.get("walletBalance", 0.0) or 0.0) > 0.0001]
            if active_coins:
                print("   Активы на балансе:")
                for c in active_coins[:5]:
                    c_name = c.get("coin")
                    c_bal = float(c.get("walletBalance", 0.0))
                    c_val = float(c.get("usdValue", 0.0) or 0.0)
                    print(f"     • {c_name}: {c_bal:,.4f} (${c_val:,.2f})")

        # 2. Проверяем открытые позиции (Linear USDT перпетуалы)
        pos_resp = session.get_positions(category="linear", settleCoin="USDT")
        if pos_resp.get("retCode") == 0:
            positions = [
                p for p in pos_resp.get("result", {}).get("list", [])
                if float(p.get("size", 0.0) or 0.0) > 0
            ]
            print(f"\n📈 Открытые позиции USDT Linear: {len(positions)}")
            for p in positions:
                sym = p.get("symbol")
                side = p.get("side")
                size = float(p.get("size", 0.0))
                entry = float(p.get("avgPrice", 0.0))
                mark = float(p.get("markPrice", 0.0))
                upl = float(p.get("unrealisedPnl", 0.0))
                print(f"   • {sym} {side} {size} @ {entry} (Mark: {mark}, PnL: ${upl:+,.2f})")

        print("\n🎉 Аккаунт полностью готов к работе с ботом!")
        return 0

    except (FailedRequestError, InvalidRequestError) as e:
        print(f"\n❌ Ошибка Bybit API: {e}")
        status = getattr(e, "status_code", None)
        msg = str(e)
        if "10003" in msg or "10004" in msg:
            print("💡 Подсказка: Проверьте правильность API Key и API Secret.")
        elif "10005" in msg:
            print("💡 Подсказка: Недостаточно прав у ключа (проверьте галочки Contract/Unified в кабинете Bybit).")
        elif "10009" in msg:
            print("💡 Подсказка: Ограничение по IP-адресу (ваш текущий IP не добавлен в белый список ключа).")
        return 1
    except Exception as e:
        print(f"\n❌ Непредвиденная ошибка подключения: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
