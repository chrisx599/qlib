from qlib.moomoo import MoomooExecutor
from moomoo import TrdMarket, TrdEnv, OpenQuoteContext, RET_OK
import time

def test_hk_simulation():
    print("=== Testing MoomooExecutor for Hong Kong (HK) Market ===")
    
    # 0. Setup Quote Context to get real price
    quote_ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    symbol = "00700"
    full_code = "HK.00700"
    current_price = 300.0 # Fallback
    
    try:
        print(f"Fetching live price for {full_code}...")
        ret, data = quote_ctx.get_market_snapshot([full_code])
        if ret == RET_OK:
            current_price = data["last_price"].iloc[0]
            print(f"  -> Current Price: {current_price}")
        else:
            print(f"  -> Failed to get quote: {data}. Using fallback: {current_price}")
    except Exception as e:
        print(f"  -> Error fetching quote: {e}")
    finally:
        quote_ctx.close()

    try:
        # Initialize for HK market, Simulation environment
        executor = MoomooExecutor(
            market=TrdMarket.HK, 
            trd_env=TrdEnv.SIMULATE
        )
        
        # 1. Check Account Info
        assets, cash, positions = executor.get_portfolio_info()
        print(f"\n[Account Info]")
        print(f"Total Assets: {assets}")
        print(f"Cash: {cash}")
        print(f"Positions: {positions}")
        
        # 2. Prepare Order
        orders = {symbol: 100} 
        prices = {symbol: current_price} 
        
        # 3. Real Execution
        print(f"\n[Real] Placing order for {symbol} at ref price {current_price}...")
        placed_orders = executor.execute_orders(orders, prices, dry_run=False)
        
        # 4. Check Status
        if placed_orders:
            order_ids = list(placed_orders.values())
            print(f"Placed Orders: {placed_orders}")
            updates = executor.wait_and_check_orders(order_ids, timeout=5)
            if not updates.empty:
                print("\n[Order Update]")
                print(updates[["order_id", "code", "trd_side", "order_status", "dealt_avg_price", "dealt_qty"]])
            else:
                print("\nNo updates received yet (might be queued).")
        
        executor.close()
        print("\nTest finished.")
        
    except Exception as e:
        print(f"\nTest failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_hk_simulation()
