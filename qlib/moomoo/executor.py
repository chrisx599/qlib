import time
import pandas as pd
from typing import Dict, Optional, Tuple, List

# Try to import from the installed moomoo package
try:
    from moomoo import (
        OpenSecTradeContext, 
        TrdEnv, 
        TrdMarket, 
        SecurityFirm, 
        TrdSide, 
        OrderType, 
        RET_OK
    )
except ImportError:
    # Fallback or explicit error if the package is missing
    raise ImportError("The 'moomoo' package is not installed. Please install it to use MoomooExecutor.")

class MoomooExecutor:
    """
    Executor for trading via Moomoo OpenD (Futu API).
    """
    def __init__(
        self, 
        host: str = "127.0.0.1", 
        port: int = 11111, 
        trd_env: TrdEnv = TrdEnv.SIMULATE,
        acc_id: Optional[int] = None,
        security_firm: SecurityFirm = SecurityFirm.FUTUINC,
        market: TrdMarket = TrdMarket.US
    ):
        """
        Args:
            host: OpenD host IP.
            port: OpenD port.
            trd_env: TrdEnv.REAL or TrdEnv.SIMULATE.
            acc_id: Account ID. If None, auto-detects the first account for the given env.
            security_firm: SecurityFirm enum (default FUTUINC).
            market: TrdMarket enum (default US).
        """
        self.host = host
        self.port = port
        self.trd_env = trd_env
        self.acc_id = acc_id
        self.security_firm = security_firm
        self.market = market
        self.trd_ctx = None
        
        self._connect()

    def _connect(self):
        """Establish connection to Moomoo OpenD"""
        try:
            self.trd_ctx = OpenSecTradeContext(
                filter_trdmarket=self.market,
                host=self.host,
                port=self.port,
                security_firm=self.security_firm,
            )
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Moomoo OpenD at {self.host}:{self.port}. Error: {e}")
        
        # Auto-detect account if not provided
        if self.acc_id is None:
            self.acc_id = self._detect_account()
            
        print(f"[MoomooExecutor] Connected. Account ID: {self.acc_id} (Env: {self.trd_env})")

    def _detect_account(self) -> int:
        ret, accs = self.trd_ctx.get_acc_list()
        if ret != RET_OK:
            raise RuntimeError(f"get_acc_list failed: {accs}")
            
        # Filter by environment
        filtered_accs = accs[accs["trd_env"] == self.trd_env]
        if filtered_accs.empty:
            available_info = accs[['acc_id', 'trd_env', 'trdmarket_auth']].to_string(index=False) if not accs.empty else "None"
            raise RuntimeError(
                f"No account found for environment: {self.trd_env}.\n"
                f"Available accounts:\n{available_info}"
            )
            
        # Pick the first one
        selected_id = int(filtered_accs["acc_id"].iloc[0])
        return selected_id

    def close(self):
        """Close the connection"""
        if self.trd_ctx:
            self.trd_ctx.close()
            self.trd_ctx = None

    def get_portfolio_info(self) -> Tuple[float, float, Dict[str, int]]:
        """
        Get current account info.
        Returns:
            (total_assets, cash, positions_dict)
            positions_dict: {qlib_symbol: quantity}
        """
        if not self.trd_ctx:
            raise RuntimeError("Connection closed.")

        # 1. Assets
        ret, funds = self.trd_ctx.accinfo_query(trd_env=self.trd_env, acc_id=self.acc_id)
        if ret != RET_OK:
            raise RuntimeError(f"accinfo_query failed: {funds}")
        
        total_assets = float(funds["total_assets"].iloc[0])
        cash = float(funds["cash"].iloc[0])

        # 2. Positions
        ret, pos_df = self.trd_ctx.position_list_query(trd_env=self.trd_env, acc_id=self.acc_id)
        if ret != RET_OK:
            raise RuntimeError(f"position_list_query failed: {pos_df}")

        positions = {}
        if not pos_df.empty:
            for _, row in pos_df.iterrows():
                qty = int(row["qty"])
                # Only include non-zero positions
                if qty != 0:
                    qlib_sym = self._moomoo_to_qlib(row["code"])
                    positions[qlib_sym] = qty
                    
        return total_assets, cash, positions

    def execute_orders(self, orders: Dict[str, int], price_dict: Dict[str, float] = None, dry_run: bool = False) -> Dict[str, str]:
        """
        Execute a list of orders (deltas).
        
        Args:
            orders: Dict {qlib_symbol: delta_qty}. 
                    positive int = Buy, negative int = Sell.
            price_dict: Dict {qlib_symbol: reference_price}.
                        Used to calculate Limit Order price.
                        If None, or symbol missing, skips order (safety).
            dry_run: If True, does not send orders to OpenD, only prints them.
                        
        Returns:
             Dict {qlib_symbol: order_id} for successfully placed orders.
             (In dry_run, order_id will be "DRY_RUN_ID")
        """
        if not self.trd_ctx:
            raise RuntimeError("Connection closed.")

        placed_orders = {}
        
        for sym, qty in orders.items():
            if qty == 0:
                continue
                
            trd_side = TrdSide.BUY if qty > 0 else TrdSide.SELL
            code = self._qlib_to_moomoo(sym)
            
            # Determine Limit Price
            if price_dict and sym in price_dict:
                ref_price = float(price_dict[sym])
                if ref_price <= 0:
                    print(f"[Warn] Invalid price {ref_price} for {sym}. Skipping.")
                    continue
                
                # Logic: Buy at +2%, Sell at -2% (to ensure fill while protecting against huge slippage)
                if qty > 0:
                    limit_price = round(ref_price * 1.02, 2)
                else:
                    limit_price = round(ref_price * 0.98, 2)
            else:
                print(f"[Warn] Skipping {sym}: No reference price provided for Limit order.")
                continue

            print(f"[Order Plan] {trd_side} {sym}: {abs(qty)} @ {limit_price}")

            if dry_run:
                placed_orders[sym] = "DRY_RUN_ID"
                continue

            # Place Order
            # Note: Remark is useful for filtering later
            ret, data = self.trd_ctx.place_order(
                price=limit_price,
                qty=abs(qty),
                code=code,
                trd_side=trd_side,
                order_type=OrderType.NORMAL,
                trd_env=self.trd_env,
                acc_id=self.acc_id,
                remark="qlib_exec"
            )
            
            if ret == RET_OK:
                # order_id can be large int, convert to str
                order_id = str(data["order_id"].iloc[0])
                placed_orders[sym] = order_id
                print(f"  -> Success. ID: {order_id}")
            else:
                print(f"  -> Failed: {data}")

        return placed_orders

    def wait_and_check_orders(self, order_ids: List[str], timeout: int = 3) -> pd.DataFrame:
        """
        Wait for a few seconds and check the status of specific orders.
        """
        if not order_ids:
            return pd.DataFrame()
            
        print(f"Waiting {timeout}s for order updates...")
        time.sleep(timeout)
        
        ret, df = self.trd_ctx.order_list_query(trd_env=self.trd_env, acc_id=self.acc_id)
        if ret == RET_OK and not df.empty:
             df["order_id"] = df["order_id"].astype(str)
             # Filter
             mask = df["order_id"].isin([str(oid) for oid in order_ids])
             return df[mask]
        elif ret != RET_OK:
            print(f"Failed to query order list: {df}")
            return pd.DataFrame()
        else:
            return pd.DataFrame()

    # --- Helpers ---

    def _qlib_to_moomoo(self, sym: str) -> str:
        """
        Convert Qlib symbol to Moomoo code based on self.market.
        e.g., "AAPL" -> "US.AAPL", "D05" -> "SG.D05"
        """
        prefix_map = {
            TrdMarket.US: "US.",
            TrdMarket.HK: "HK.",
            TrdMarket.SG: "SG.",
            # Add more as needed
        }
        
        prefix = prefix_map.get(self.market, "")
        if not prefix:
            # Fallback for unknown markets or if user passed full code
            if "." in sym: 
                return sym
            print(f"[Warn] Unknown market {self.market}, assuming US or manual prefix.")
            return f"US.{sym}"
            
        return f"{prefix}{sym}"

    def _moomoo_to_qlib(self, code: str) -> str:
        """
        Convert Moomoo code to Qlib symbol.
        e.g., "US.AAPL" -> "AAPL", "SG.D05" -> "D05"
        """
        if "." in code:
            return code.split(".", 1)[1]
        return code


if __name__ == "__main__":
    # Simple test/demo
    print("=== MoomooExecutor Demo ===")
    try:
        executor = MoomooExecutor(trd_env=TrdEnv.SIMULATE)
        
        # 1. Get Info
        assets, cash, positions = executor.get_portfolio_info()
        print(f"\nAssets: {assets}, Cash: {cash}")
        print(f"Positions: {positions}")
        
        # 2. Mock Orders
        # Try to buy 1 share of AAPL if we have cash, or just dry run
        orders = {"AAPL": 1}
        prices = {"AAPL": 150.0} # Mock price
        
        print("\nPlacing Dry Run Orders:")
        executor.execute_orders(orders, prices, dry_run=True)
        
        # To run real orders in SIMULATE:
        # executor.execute_orders(orders, prices, dry_run=False)
        
        executor.close()
        print("\nDemo finished.")
        
    except Exception as e:
        print(f"Demo failed (OpenD might not be running): {e}")

