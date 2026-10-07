import threading
import time
import math
import os
import json
from datetime import datetime, timezone, timedelta
from market_data import real_feed, INSTRUMENT_MAP

IST = timezone(timedelta(hours=5, minutes=30))

STRATEGY_PROFILES = {
    "SCALPER": {
        "name": "⚡ Quick Scalper",
        "min_score": 72,
        "max_bear_score": 28,
        "min_rvol": 1.2,
        "max_vwap_dist": 1.8,
        "rr_ratio": 1.25,        # 1:1.25 quick realistic targets
        "opt_sl": 0.90,          # -10% SL
        "opt_tp": 1.15,          # +15% TP
        "eq_sl": 0.988,          # -1.2% SL (wide enough to survive 1m/5m noise)
        "eq_tp": 1.015,          # +1.5% TP (high probability hit)
        "breakeven_trigger": 0.005,  # +0.5% profit -> shift SL to Entry + 0.1% (Cost-to-Cost Risk Free)
        "trail_trigger": 1.010,  # +1.0% profit -> trail SL closely
        "trail_dist": 0.994,     # Keep SL 0.6% behind peak
        "tag": "SCALP"
    },
    "TREND": {
        "name": "🏄 Trend Rider",
        "min_score": 75,
        "max_bear_score": 25,
        "min_rvol": 1.3,
        "max_vwap_dist": 1.5,
        "rr_ratio": 1.6,         # 1:1.6 solid trend R:R
        "opt_sl": 0.85,          # -15% SL
        "opt_tp": 1.25,          # +25% TP
        "eq_sl": 0.986,          # -1.4% SL (survives normal intraday volatility)
        "eq_tp": 1.022,          # +2.2% TP
        "breakeven_trigger": 0.007,  # +0.7% profit -> shift SL to Entry + 0.1% (Risk Free)
        "trail_trigger": 1.014,  # +1.4% profit -> trail SL
        "trail_dist": 0.990,     # Keep SL 1.0% behind peak
        "tag": "TREND"
    },
    "SNIPER": {
        "name": "🎯 Safe Sniper",
        "min_score": 82,         # High conviction institutional pullback
        "max_bear_score": 18,
        "min_rvol": 1.4,         # Strict Institutional Volume Surge
        "max_vwap_dist": 1.0,    # Strict Pullback Zone within 1.0% of VWAP (Cheap Entry)
        "rr_ratio": 1.5,         # 1:1.5 high win-rate target (not 1:3 which fails in noise)
        "opt_sl": 0.88,          # -12% SL
        "opt_tp": 1.20,          # +20% TP
        "eq_sl": 0.988,          # -1.2% SL (support below VWAP)
        "eq_tp": 1.018,          # +1.8% TP
        "breakeven_trigger": 0.006,  # +0.6% profit -> shift SL to Entry + 0.1% (Risk Free)
        "trail_trigger": 1.012,  # +1.2% profit -> trail SL
        "trail_dist": 0.992,
        "tag": "SNIPER"
    }
}

class TradingEngine:
    def __init__(self):
        self.lock = threading.Lock()
        
        # User Configurable Settings (Default to user's desired config)
        self.initial_capital = 5000.0
        self.current_capital = 5000.0
        self.daily_loss_limit = 1000.0
        self.risk_per_trade_pct = 2.0   # 2.0% risk of capital per trade
        self.strategy_style = "TREND"   # "SCALPER", "TREND", "SNIPER"
        self.trade_direction = "BOTH"   # "BOTH", "BUY_ONLY", "SELL_ONLY"
        self.max_open_positions = 3
        self.enable_options = True
        self.enable_equities = True
        self.mode = "REAL_DATA_PAPER"   # Real live NSE prices, paper execution
        
        # Runtime State
        self.is_running = False
        self.kill_switch_triggered = False
        self.kill_switch_reason = ""
        self.worker_thread = None
        
        # Accounting & Performance
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        
        # Storage
        self.active_positions = []
        self.trade_history = []
        self.ai_logs = []
        
        # 5-Minute Re-Entry Cooldown Shield (Prevents revenge trading, top-buying & zombie re-entries)
        self.cooldown_duration = 300  # 300 seconds = 5 minutes
        self.stock_cooldowns = {}     # { symbol/underlying: expiry_timestamp }
        
        # Persistent custom SL / TP override for active & upcoming new stocks (BUY & SELL independent)
        self.custom_trade_sl = None
        self.custom_trade_tp = None
        self.custom_buy_sl = None
        self.custom_buy_tp = None
        self.custom_sell_sl = None
        self.custom_sell_tp = None

        # Price Filter Settings (Default OFF: all price ranges included in active stocks)
        self.price_filter_active = False
        self.price_filter_condition = "HIGHER"  # "HIGHER" (>=) or "LOWER" (<=)
        self.price_filter_amount = 1000.0

        # Elite Accuracy Filters (6 Institutional Confirmation Filters)
        self.nifty_filter_active = True     # 1. NIFTY Trend Alignment
        self.time_filter_active = True      # 2. No-Trade Zone / Chop Protection
        self.mtf_filter_active = True       # 3. 15m Multi-Timeframe Alignment
        self.ema_filter_active = True       # 4. 9 EMA & 20 EMA Dynamic Ribbon
        self.vwap_pullback_filter_active = True # 5. VWAP Pullback Zone (Avoid Overbought Top-Buying)
        self.rvol_filter_active = True      # 6. Institutional Volume Surge (RVOL >= 1.5x)
        self.max_vwap_dist_pct = 1.2        # Maximum 1.2% distance from VWAP for Safe Pullback
        self.min_rvol_threshold = 1.5       # Minimum 1.5x relative volume surge

        # Brokerage Shield & Frequency Limiter
        self.max_daily_trades = 10
        self.brokerage_per_trade = 20.0  # ₹20 per trade

        # ==============================================================
        # MANUAL PRO TRADER DESK (INDEPENDENT VIRTUAL DESK)
        # ==============================================================
        self.manual_initial_capital = 10000.0
        self.manual_current_capital = 10000.0
        self.manual_daily_loss_limit = 2000.0
        self.manual_risk_per_trade_pct = 1.0
        self.manual_max_daily_trades = 10
        self.manual_brokerage_per_trade = 20.0
        self.manual_realized_pnl = 0.0
        self.manual_unrealized_pnl = 0.0
        self.manual_total_trades = 0
        self.manual_winning_trades = 0
        self.manual_active_positions = []
        self.manual_trade_history = []
        self.manual_logs = []

        # Load persisted settings if available
        self._load_settings()
        
        risk_amt = self.get_normal_sl_amount()
        normal_tp = self.get_normal_tp_amount()
        self.add_log(f"System ready: REAL NSE Market Data Feed connected. Capital: ₹{self.initial_capital:,.2f} | Loss Limit: ₹{self.daily_loss_limit:,.2f} | Risk: {self.risk_per_trade_pct}% (Normal SL: -₹{risk_amt:,.0f} | Normal TP: +₹{normal_tp:,.0f})")

        # Continuous background position monitoring loop (Ensures manual & AI positions update in real-time)
        threading.Thread(target=self._continuous_monitor_loop, daemon=True).start()

    def _load_settings(self):
        settings_file = os.path.join(os.path.dirname(__file__), "user_settings.json")
        if os.path.exists(settings_file):
            try:
                with open(settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "initial_capital" in data and float(data["initial_capital"]) > 0:
                    self.initial_capital = float(data["initial_capital"])
                    self.current_capital = self.initial_capital
                if "daily_loss_limit" in data and float(data["daily_loss_limit"]) > 0:
                    self.daily_loss_limit = float(data["daily_loss_limit"])
                if "risk_per_trade_pct" in data and float(data["risk_per_trade_pct"]) > 0:
                    self.risk_per_trade_pct = float(data["risk_per_trade_pct"])
                if "strategy_style" in data and data["strategy_style"] in STRATEGY_PROFILES:
                    self.strategy_style = data["strategy_style"]
                if "trade_direction" in data:
                    self.trade_direction = data["trade_direction"]
                if "max_daily_trades" in data:
                    self.max_daily_trades = int(data["max_daily_trades"])
                if "brokerage_per_trade" in data:
                    self.brokerage_per_trade = float(data["brokerage_per_trade"])
                if "price_filter_active" in data:
                    self.price_filter_active = bool(data["price_filter_active"])
                if "price_filter_condition" in data:
                    self.price_filter_condition = str(data["price_filter_condition"])
                if "price_filter_amount" in data:
                    self.price_filter_amount = float(data["price_filter_amount"])
                if "nifty_filter_active" in data:
                    self.nifty_filter_active = bool(data["nifty_filter_active"])
                if "time_filter_active" in data:
                    self.time_filter_active = bool(data["time_filter_active"])
                if "mtf_filter_active" in data:
                    self.mtf_filter_active = bool(data["mtf_filter_active"])
                if "ema_filter_active" in data:
                    self.ema_filter_active = bool(data["ema_filter_active"])
                if "vwap_pullback_filter_active" in data:
                    self.vwap_pullback_filter_active = bool(data["vwap_pullback_filter_active"])
                if "rvol_filter_active" in data:
                    self.rvol_filter_active = bool(data["rvol_filter_active"])
                if "max_vwap_dist_pct" in data:
                    self.max_vwap_dist_pct = float(data["max_vwap_dist_pct"])
                if "min_rvol_threshold" in data:
                    self.min_rvol_threshold = float(data["min_rvol_threshold"])
                # Manual Desk Settings persistence
                if "manual_initial_capital" in data and float(data["manual_initial_capital"]) > 0:
                    self.manual_initial_capital = float(data["manual_initial_capital"])
                    self.manual_current_capital = self.manual_initial_capital
                if "manual_daily_loss_limit" in data and float(data["manual_daily_loss_limit"]) > 0:
                    self.manual_daily_loss_limit = float(data["manual_daily_loss_limit"])
                if "manual_risk_per_trade_pct" in data and float(data["manual_risk_per_trade_pct"]) > 0:
                    self.manual_risk_per_trade_pct = float(data["manual_risk_per_trade_pct"])
                if "manual_max_daily_trades" in data and int(data["manual_max_daily_trades"]) >= 0:
                    self.manual_max_daily_trades = int(data["manual_max_daily_trades"])
                if "manual_brokerage_per_trade" in data and float(data["manual_brokerage_per_trade"]) >= 0:
                    self.manual_brokerage_per_trade = float(data["manual_brokerage_per_trade"])
            except Exception:
                pass

    def _save_settings(self):
        settings_file = os.path.join(os.path.dirname(__file__), "user_settings.json")
        try:
            data = {
                "initial_capital": self.initial_capital,
                "daily_loss_limit": self.daily_loss_limit,
                "risk_per_trade_pct": self.risk_per_trade_pct,
                "strategy_style": self.strategy_style,
                "trade_direction": self.trade_direction,
                "max_daily_trades": self.max_daily_trades,
                "brokerage_per_trade": self.brokerage_per_trade,
                "price_filter_active": self.price_filter_active,
                "price_filter_condition": self.price_filter_condition,
                "price_filter_amount": self.price_filter_amount,
                "nifty_filter_active": self.nifty_filter_active,
                "time_filter_active": self.time_filter_active,
                "mtf_filter_active": self.mtf_filter_active,
                "ema_filter_active": self.ema_filter_active,
                "vwap_pullback_filter_active": self.vwap_pullback_filter_active,
                "rvol_filter_active": self.rvol_filter_active,
                "max_vwap_dist_pct": self.max_vwap_dist_pct,
                "min_rvol_threshold": self.min_rvol_threshold,
                "manual_initial_capital": self.manual_initial_capital,
                "manual_daily_loss_limit": self.manual_daily_loss_limit,
                "manual_risk_per_trade_pct": self.manual_risk_per_trade_pct,
                "manual_max_daily_trades": self.manual_max_daily_trades,
                "manual_brokerage_per_trade": self.manual_brokerage_per_trade,
            }
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _get_time_window_status(self):
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return "MARKET_CLOSED", "Market Closed (Weekend)"
        
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        
        if now < market_open or now > market_close:
            return "MARKET_CLOSED", "Market Closed (Active 09:15 - 15:30 IST)"
            
        opening_noise_end = now.replace(hour=9, minute=25, second=0, microsecond=0)
        if market_open <= now < opening_noise_end:
            return "OPENING_VOLATILITY", "Opening Volatility (09:15-09:25 IST) • Filtering Noise"
            
        midday_start = now.replace(hour=11, minute=30, second=0, microsecond=0)
        midday_end = now.replace(hour=13, minute=15, second=0, microsecond=0)
        if midday_start <= now < midday_end:
            return "MIDDAY_CHOP", "Midday Chop Zone (11:30-13:15 IST) • Sideways Decay Protection"
            
        sq_off_time = now.replace(hour=15, minute=15, second=0, microsecond=0)
        if now >= sq_off_time:
            return "SQUARE_OFF", "3:15 PM Square-Off Period • No New Entries"
            
        return "ACTIVE", "Prime Trading Window Active • High-Probability Entries"

    def get_trade_side(self, pos):
        if not pos:
            return "BUY"
        if pos.get("type") == "SELL":
            return "SELL"
        if pos.get("instrument") == "OPTION" and "PE" in pos.get("symbol", ""):
            return "SELL"
        if "pe" in pos.get("reason", "").lower():
            return "SELL"
        return "BUY"

    def get_normal_sl_amount(self):
        return round((self.initial_capital * self.risk_per_trade_pct) / 100.0, 2)

    def get_normal_tp_amount(self):
        sl = self.get_normal_sl_amount()
        rr = STRATEGY_PROFILES.get(self.strategy_style, {}).get("rr_ratio", 2.0)
        return round(sl * rr, 2)

    def get_effective_sl_amount(self, side="BUY"):
        if side == "BUY":
            if self.custom_buy_sl is not None and self.custom_buy_sl > 0:
                return round(self.custom_buy_sl, 2)
        else:
            if self.custom_sell_sl is not None and self.custom_sell_sl > 0:
                return round(self.custom_sell_sl, 2)
        if self.custom_trade_sl is not None and self.custom_trade_sl > 0:
            return round(self.custom_trade_sl, 2)
        return self.get_normal_sl_amount()

    def get_effective_tp_amount(self, side="BUY"):
        if side == "BUY":
            if self.custom_buy_tp is not None and self.custom_buy_tp > 0:
                return round(self.custom_buy_tp, 2)
            if self.custom_buy_sl is not None and self.custom_buy_sl > 0:
                rr = STRATEGY_PROFILES.get(self.strategy_style, {}).get("rr_ratio", 2.0)
                return round(self.custom_buy_sl * rr, 2)
        else:
            if self.custom_sell_tp is not None and self.custom_sell_tp > 0:
                return round(self.custom_sell_tp, 2)
            if self.custom_sell_sl is not None and self.custom_sell_sl > 0:
                rr = STRATEGY_PROFILES.get(self.strategy_style, {}).get("rr_ratio", 2.0)
                return round(self.custom_sell_sl * rr, 2)
        if self.custom_trade_tp is not None and self.custom_trade_tp > 0:
            return round(self.custom_trade_tp, 2)
        if self.custom_trade_sl is not None and self.custom_trade_sl > 0:
            rr = STRATEGY_PROFILES.get(self.strategy_style, {}).get("rr_ratio", 2.0)
            return round(self.custom_trade_sl * rr, 2)
        return self.get_normal_tp_amount()

    def update_position_sltp(self, position_id, sl_amount=None, tp_amount=None):
        with self.lock:
            for pos in self.active_positions:
                if pos["id"] == position_id:
                    qty = pos["qty"]
                    entry = pos["entry_price"]
                    if sl_amount is not None and float(sl_amount) > 0:
                        pos["sl_amount"] = float(sl_amount)
                        pos["sl"] = max(0.5, round(entry - (pos["sl_amount"] / qty), 2)) if pos["type"] == "BUY" else round(entry + (pos["sl_amount"] / qty), 2)
                        pos["is_custom_sltp"] = True
                    if tp_amount is not None and float(tp_amount) > 0:
                        pos["tp_amount"] = float(tp_amount)
                        pos["target"] = round(entry + (pos["tp_amount"] / qty), 2) if pos["type"] == "BUY" else max(0.5, round(entry - (pos["tp_amount"] / qty), 2))
                        pos["is_custom_sltp"] = True
                    self.add_log(f"UPDATED SL/TP for {pos['symbol']}: SL=-₹{pos['sl_amount']:,.0f} (@ ₹{pos['sl']:.2f}) | TP=+₹{pos['tp_amount']:,.0f} (@ ₹{pos['target']:.2f}) [Custom Edit]")
                    return True, f"SL/TP updated for {pos['symbol']}."
            return False, "Position not found."

    def reset_position_sltp(self, position_id):
        with self.lock:
            for pos in self.active_positions:
                if pos["id"] == position_id:
                    qty = pos["qty"]
                    entry = pos["entry_price"]
                    pos["sl_amount"] = self.get_normal_sl_amount()
                    pos["tp_amount"] = self.get_normal_tp_amount()
                    pos["sl"] = max(0.5, round(entry - (pos["sl_amount"] / qty), 2)) if pos["type"] == "BUY" else round(entry + (pos["sl_amount"] / qty), 2)
                    pos["target"] = round(entry + (pos["tp_amount"] / qty), 2) if pos["type"] == "BUY" else max(0.5, round(entry - (pos["tp_amount"] / qty), 2))
                    pos["is_custom_sltp"] = False
                    self.add_log(f"RESET SL/TP to NORMAL for {pos['symbol']}: SL=-₹{pos['sl_amount']:,.0f} | TP=+₹{pos['tp_amount']:,.0f}")
                    return True, f"SL/TP reset to normal for {pos['symbol']}."
            return False, "Position not found."

    def update_all_positions_sltp(self, sl_amount=None, tp_amount=None, side="BUY"):
        with self.lock:
            side = "SELL" if str(side).upper() == "SELL" else "BUY"
            
            # 1. Lock in custom override so upcoming new stocks of this side inherit this
            if side == "BUY":
                if sl_amount is not None and float(sl_amount) > 0:
                    self.custom_buy_sl = float(sl_amount)
                if tp_amount is not None and float(tp_amount) > 0:
                    self.custom_buy_tp = float(tp_amount)
            else:
                if sl_amount is not None and float(sl_amount) > 0:
                    self.custom_sell_sl = float(sl_amount)
                if tp_amount is not None and float(tp_amount) > 0:
                    self.custom_sell_tp = float(tp_amount)

            eff_sl = self.get_effective_sl_amount(side)
            eff_tp = self.get_effective_tp_amount(side)

            # 2. Update existing active positions matching this side
            count = 0
            for pos in self.active_positions:
                if self.get_trade_side(pos) == side:
                    qty = pos["qty"]
                    entry = pos["entry_price"]
                    if sl_amount is not None and float(sl_amount) > 0:
                        pos["sl_amount"] = float(sl_amount)
                        pos["sl"] = max(0.5, round(entry - (pos["sl_amount"] / qty), 2)) if pos["type"] == "BUY" else round(entry + (pos["sl_amount"] / qty), 2)
                        pos["is_custom_sltp"] = True
                    if tp_amount is not None and float(tp_amount) > 0:
                        pos["tp_amount"] = float(tp_amount)
                        pos["target"] = round(entry + (pos["tp_amount"] / qty), 2) if pos["type"] == "BUY" else max(0.5, round(entry - (pos["tp_amount"] / qty), 2))
                        pos["is_custom_sltp"] = True
                    count += 1

            self.add_log(f"LOCKED IN CUSTOM {side} SL/TP: SL=-₹{eff_sl:,.0f} | TP=+₹{eff_tp:,.0f} (Applied to {count} active positions & ALL newly entered {side} stocks)")
            return True, f"SL/TP set for {side} (SL=-₹{eff_sl:,.0f} | TP=+₹{eff_tp:,.0f}). Applied to {count} active positions and locked in for all new {side} stocks."

    def reset_all_positions_sltp(self, side=None):
        with self.lock:
            if side == "BUY":
                self.custom_buy_sl = None
                self.custom_buy_tp = None
                sides_to_reset = ["BUY"]
            elif side == "SELL":
                self.custom_sell_sl = None
                self.custom_sell_tp = None
                sides_to_reset = ["SELL"]
            else:
                self.custom_trade_sl = None
                self.custom_trade_tp = None
                self.custom_buy_sl = None
                self.custom_buy_tp = None
                self.custom_sell_sl = None
                self.custom_sell_tp = None
                sides_to_reset = ["BUY", "SELL"]

            normal_sl = self.get_normal_sl_amount()
            normal_tp = self.get_normal_tp_amount()
            count = 0
            for pos in self.active_positions:
                if self.get_trade_side(pos) in sides_to_reset:
                    qty = pos["qty"]
                    entry = pos["entry_price"]
                    pos["sl_amount"] = normal_sl
                    pos["tp_amount"] = normal_tp
                    pos["sl"] = max(0.5, round(entry - (normal_sl / qty), 2)) if pos["type"] == "BUY" else round(entry + (normal_sl / qty), 2)
                    pos["target"] = round(entry + (pos["tp_amount"] / qty), 2) if pos["type"] == "BUY" else max(0.5, round(entry - (pos["tp_amount"] / qty), 2))
                    pos["is_custom_sltp"] = False
                    count += 1
            label = side if side in ["BUY", "SELL"] else "ALL"
            self.add_log(f"RESET {label} ({count}) POSITIONS & FUTURE NEW STOCKS to NORMAL SL/TP: SL=-₹{normal_sl:,.0f} | TP=+₹{normal_tp:,.0f}")
            return True, f"Reset all {count} active {label} positions and future new stocks to normal SL/TP."

    def add_log(self, message):
        timestamp = datetime.now(IST).strftime("%H:%M:%S")
        entry = {"time": timestamp, "message": message}
        self.ai_logs.insert(0, entry)
        if len(self.ai_logs) > 100:
            self.ai_logs.pop()

    def update_settings(self, initial_capital=None, daily_loss_limit=None, risk_per_trade_pct=None, enable_options=None, enable_equities=None, strategy_style=None, trade_direction=None, trade_sl_amount=None, trade_tp_amount=None, price_filter_active=None, price_filter_condition=None, price_filter_amount=None, max_daily_trades=None, brokerage_per_trade=None, nifty_filter_active=None, time_filter_active=None, mtf_filter_active=None, ema_filter_active=None, vwap_pullback_filter_active=None, rvol_filter_active=None, max_vwap_dist_pct=None, min_rvol_threshold=None):
        with self.lock:
            if initial_capital is not None and initial_capital > 0:
                diff = initial_capital - self.initial_capital
                self.initial_capital = float(initial_capital)
                self.current_capital = max(0.0, self.current_capital + diff)
            if daily_loss_limit is not None and daily_loss_limit > 0:
                self.daily_loss_limit = float(daily_loss_limit)
            if risk_per_trade_pct is not None and 0.1 <= risk_per_trade_pct <= 10.0:
                self.risk_per_trade_pct = float(risk_per_trade_pct)
            if enable_options is not None:
                self.enable_options = bool(enable_options)
            if enable_equities is not None:
                self.enable_equities = bool(enable_equities)
            if strategy_style and strategy_style in STRATEGY_PROFILES:
                self.strategy_style = strategy_style
                profile_name = STRATEGY_PROFILES[strategy_style]["name"]
                self.add_log(f"Strategy style set to {profile_name} (BUY ≥ {STRATEGY_PROFILES[strategy_style]['min_score']}% | SELL ≤ {STRATEGY_PROFILES[strategy_style]['max_bear_score']}%)")
            if trade_direction in ["BOTH", "BUY_ONLY", "SELL_ONLY"]:
                self.trade_direction = trade_direction
                dir_label = "BOTH (BUY & SELL)" if trade_direction == "BOTH" else ("BUY ONLY (Long)" if trade_direction == "BUY_ONLY" else "SELL ONLY (Short)")
                self.add_log(f"Trade Direction set to: {dir_label}")
            if price_filter_active is not None:
                self.price_filter_active = bool(price_filter_active)
            if price_filter_condition in ["HIGHER", "LOWER"]:
                self.price_filter_condition = price_filter_condition
            if price_filter_amount is not None and float(price_filter_amount) > 0:
                self.price_filter_amount = float(price_filter_amount)
            if max_daily_trades is not None and int(max_daily_trades) >= 0:
                self.max_daily_trades = int(max_daily_trades)
            if brokerage_per_trade is not None and float(brokerage_per_trade) >= 0:
                self.brokerage_per_trade = float(brokerage_per_trade)
            if nifty_filter_active is not None:
                self.nifty_filter_active = bool(nifty_filter_active)
                self.add_log(f"Accuracy Filter: NIFTY Trend Alignment set to {'ON' if self.nifty_filter_active else 'OFF'}")
            if time_filter_active is not None:
                self.time_filter_active = bool(time_filter_active)
                self.add_log(f"Accuracy Filter: No-Trade Zone / Chop Protection set to {'ON' if self.time_filter_active else 'OFF'}")
            if mtf_filter_active is not None:
                self.mtf_filter_active = bool(mtf_filter_active)
                self.add_log(f"Accuracy Filter: 15m Multi-Timeframe Alignment set to {'ON' if self.mtf_filter_active else 'OFF'}")
            if ema_filter_active is not None:
                self.ema_filter_active = bool(ema_filter_active)
                self.add_log(f"Accuracy Filter: 9/20 EMA Dynamic Ribbon set to {'ON' if self.ema_filter_active else 'OFF'}")
            if vwap_pullback_filter_active is not None:
                self.vwap_pullback_filter_active = bool(vwap_pullback_filter_active)
                self.add_log(f"Accuracy Filter: VWAP Pullback Zone set to {'ON' if self.vwap_pullback_filter_active else 'OFF'}")
            if rvol_filter_active is not None:
                self.rvol_filter_active = bool(rvol_filter_active)
                self.add_log(f"Accuracy Filter: Institutional RVOL Surge set to {'ON' if self.rvol_filter_active else 'OFF'}")
            if max_vwap_dist_pct is not None and float(max_vwap_dist_pct) > 0:
                self.max_vwap_dist_pct = float(max_vwap_dist_pct)
            if min_rvol_threshold is not None and float(min_rvol_threshold) > 0:
                self.min_rvol_threshold = float(min_rvol_threshold)
            
            risk_amt = self.get_normal_sl_amount()
            normal_tp = self.get_normal_tp_amount()
            pf_status = f"ON ({self.price_filter_condition} ₹{self.price_filter_amount:,.0f})" if self.price_filter_active else "OFF (All Prices)"
            cap_str = f"{self.max_daily_trades} Trades" if self.max_daily_trades > 0 else "Unlimited"
            self.add_log(f"Settings saved: Capital=₹{self.initial_capital:,.2f}, Loss Limit=₹{self.daily_loss_limit:,.2f}, Risk={self.risk_per_trade_pct}% (₹{risk_amt:,.2f}), Trade Cap={cap_str}, Price Filter={pf_status}, Normal SL=-₹{risk_amt:,.0f}, Normal TP=+₹{normal_tp:,.0f}, Style={self.strategy_style}")
            self._save_settings()

    def reset_account(self):
        with self.lock:
            self.current_capital = self.initial_capital
            self.realized_pnl = 0.0
            self.unrealized_pnl = 0.0
            self.total_trades = 0
            self.winning_trades = 0
            self.active_positions.clear()
            self.trade_history.clear()
            self.stock_cooldowns.clear()
            self.custom_trade_sl = None
            self.custom_trade_tp = None
            self.custom_buy_sl = None
            self.custom_buy_tp = None
            self.custom_sell_sl = None
            self.custom_sell_tp = None
            self.price_filter_active = False
            self.price_filter_condition = "HIGHER"
            self.price_filter_amount = 1000.0
            self.max_daily_trades = 10
            self.trade_direction = "BOTH"
            self.kill_switch_triggered = False
            self.kill_switch_reason = ""
            self.add_log("Account reset to default balance and metrics cleared.")

    def start_bot(self):
        with self.lock:
            if self.kill_switch_triggered:
                self.add_log("Cannot start: Kill switch is active from today's loss limit! Reset account to trade.")
                return False, "Kill switch is active. Reset account to trade again."
            if self.is_running:
                return True, "Bot is already running."
            
            self.is_running = True
            feed_info = real_feed.get_feed_status()
            market_state_str = "OPEN" if feed_info["is_market_open"] else "CLOSED (Using latest real NSE session data)"
            self.add_log(f">>> AI TRADING BOT STARTED. NSE Market Status: {market_state_str}. Scanning real data...")
            
            self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
            self.worker_thread.start()
            return True, "Bot started successfully."

    def stop_bot(self, square_off=True):
        with self.lock:
            if not self.is_running:
                return True, "Bot is already stopped."
            
            self.is_running = False
            self.add_log("<<< STOP COMMAND RECEIVED. Bot shutting down safely.")
            
            if square_off:
                self._square_off_all_positions(reason="Manual Emergency Stop")
            return True, "Bot stopped and positions squared off."

    def _square_off_all_positions(self, reason="Square Off"):
        for pos in list(self.active_positions):
            self._close_position_internal(pos, reason=reason)
        self.unrealized_pnl = 0.0

    def close_single_position(self, position_id):
        with self.lock:
            pos_to_close = None
            for pos in self.active_positions:
                if pos["id"] == position_id or pos["symbol"] == position_id or pos.get("underlying") == position_id:
                    pos_to_close = pos
                    break
            if pos_to_close:
                self._close_position_internal(pos_to_close, reason="Manual User Exit")
                return True, f"Position {pos_to_close['symbol']} closed successfully."
            return False, "Position not found."

    # ==============================================================
    # MANUAL PRO TRADER DESK METHODS
    # ==============================================================
    def execute_manual_order(self, symbol, side="BUY", qty=None, entry_price=None, sl_price=None, tp_price=None, sl_amount=None, tp_amount=None, reason="Manual Trade"):
        with self.lock:
            clean_underlying = symbol.replace(" MIS", "").split()[0].upper()
            sym_data = real_feed.get_symbol_data(clean_underlying)
            
            spot = sym_data["current_price"] if sym_data else (float(entry_price) if entry_price else 100.0)
            entry = float(entry_price) if entry_price and float(entry_price) > 0 else spot
            
            risk_amt = float(sl_amount) if sl_amount and float(sl_amount) > 0 else ((self.manual_initial_capital * self.manual_risk_per_trade_pct) / 100.0)
            target_amt = float(tp_amount) if tp_amount and float(tp_amount) > 0 else (risk_amt * 1.6)

            if not qty or int(qty) <= 0:
                loss_per_share = max(0.5, entry * 0.012)
                shares = max(1, int(risk_amt / loss_per_share))
            else:
                shares = int(qty)

            side_upper = "SELL" if str(side).upper() == "SELL" else "BUY"
            
            if sl_price and float(sl_price) > 0:
                final_sl = round(float(sl_price), 2)
            else:
                final_sl = max(0.5, round(entry - (risk_amt / shares), 2)) if side_upper == "BUY" else round(entry + (risk_amt / shares), 2)
                
            if tp_price and float(tp_price) > 0:
                final_tp = round(float(tp_price), 2)
            else:
                final_tp = round(entry + (target_amt / shares), 2) if side_upper == "BUY" else max(0.5, round(entry - (target_amt / shares), 2))

            pos = {
                "id": f"MAN_{int(time.time()*1000)%100000}",
                "symbol": f"{clean_underlying} MIS",
                "underlying": clean_underlying,
                "instrument": "EQUITY",
                "type": side_upper,
                "qty": shares,
                "base_price": entry,
                "entry_price": entry,
                "current_price": entry,
                "sl": final_sl,
                "target": final_tp,
                "sl_amount": risk_amt,
                "tp_amount": target_amt,
                "is_custom_sltp": True,
                "trailing_active": False,
                "breakeven_locked": False,
                "breakeven_trigger": 0.005,
                "trail_trigger": 1.010,
                "trail_dist": 0.990,
                "style_tag": "MANUAL",
                "pnl": 0.0,
                "entry_time": datetime.now(IST).strftime("%H:%M:%S"),
                "reason": reason
            }
            self.manual_active_positions.append(pos)
            self.add_manual_log(f"MANUAL ORDER: {side_upper} {shares}x {pos['symbol']} @ ₹{entry:.2f} | SL: ₹{final_sl:.2f} | Target: ₹{final_tp:.2f} | {reason}")
            return True, f"Manual {side_upper} order executed for {pos['symbol']}", pos

    def close_single_manual_position(self, position_id, reason="Manual User Exit"):
        with self.lock:
            pos_to_close = None
            for pos in self.manual_active_positions:
                if pos["id"] == position_id or pos["symbol"] == position_id or pos.get("underlying") == position_id:
                    pos_to_close = pos
                    break
            if pos_to_close:
                self._close_manual_position_internal(pos_to_close, reason=reason)
                return True, f"Manual Position {pos_to_close['symbol']} closed successfully."
            return False, "Position not found."

    def _close_manual_position_internal(self, pos, reason="Manual Exit"):
        exit_price = pos["current_price"]
        pnl = (exit_price - pos["entry_price"]) * pos["qty"] if pos["type"] == "BUY" else (pos["entry_price"] - exit_price) * pos["qty"]
        
        self.manual_realized_pnl += pnl
        self.manual_total_trades += 1
        if pnl > 0:
            self.manual_winning_trades += 1
        total_brokerage = self.manual_total_trades * self.manual_brokerage_per_trade
        self.manual_current_capital = max(0.0, round(self.manual_initial_capital + self.manual_realized_pnl - total_brokerage, 2))
        
        trade_record = {
            "id": pos["id"],
            "symbol": pos["symbol"],
            "underlying": pos["underlying"],
            "instrument": pos.get("instrument", "EQUITY"),
            "type": pos["type"],
            "qty": pos["qty"],
            "entry_price": pos["entry_price"],
            "exit_price": round(exit_price, 2),
            "pnl": round(pnl, 2),
            "entry_time": pos.get("entry_time", datetime.now(IST).strftime("%H:%M:%S")),
            "exit_time": datetime.now(IST).strftime("%H:%M:%S"),
            "reason": reason,
            "style_tag": "MANUAL"
        }
        self.manual_trade_history.insert(0, trade_record)
        if pos in self.manual_active_positions:
            self.manual_active_positions.remove(pos)
            
        pnl_str = f"+₹{pnl:,.2f}" if pnl >= 0 else f"-₹{abs(pnl):,.2f}"
        self.add_manual_log(f"MANUAL CLOSED {pos['symbol']} @ ₹{exit_price:.2f} | P&L: {pnl_str} | Reason: {reason}")

    def reset_manual_account(self):
        with self.lock:
            self.manual_current_capital = self.manual_initial_capital
            self.manual_realized_pnl = 0.0
            self.manual_unrealized_pnl = 0.0
            self.manual_total_trades = 0
            self.manual_winning_trades = 0
            self.manual_active_positions.clear()
            self.manual_trade_history.clear()
            self.add_manual_log("Manual Trading Desk account reset to default balance.")

    def update_manual_position_sltp(self, position_id, sl_amount=None, tp_amount=None):
        with self.lock:
            for pos in self.manual_active_positions:
                if pos["id"] == position_id:
                    qty = pos["qty"]
                    entry = pos["entry_price"]
                    if sl_amount is not None and float(sl_amount) > 0:
                        pos["sl_amount"] = float(sl_amount)
                        pos["sl"] = max(0.5, round(entry - (pos["sl_amount"] / qty), 2)) if pos["type"] == "BUY" else round(entry + (pos["sl_amount"] / qty), 2)
                    if tp_amount is not None and float(tp_amount) > 0:
                        pos["tp_amount"] = float(tp_amount)
                        pos["target"] = round(entry + (pos["tp_amount"] / qty), 2) if pos["type"] == "BUY" else max(0.5, round(entry - (pos["tp_amount"] / qty), 2))
                    self.add_manual_log(f"UPDATED SL/TP for Manual {pos['symbol']}: SL=₹{pos['sl']:.2f} | TP=₹{pos['target']:.2f}")
                    return True, f"SL/TP updated for {pos['symbol']}."
            return False, "Position not found."

    def update_all_manual_positions_sltp(self, side, sl_amount=None, tp_amount=None):
        with self.lock:
            side_upper = str(side).upper()
            count = 0
            for pos in self.manual_active_positions:
                if pos.get("type") == side_upper:
                    qty = pos["qty"]
                    entry = pos["entry_price"]
                    if sl_amount is not None and float(sl_amount) > 0:
                        pos["sl_amount"] = float(sl_amount)
                        pos["sl"] = max(0.5, round(entry - (pos["sl_amount"] / qty), 2)) if pos["type"] == "BUY" else round(entry + (pos["sl_amount"] / qty), 2)
                    if tp_amount is not None and float(tp_amount) > 0:
                        pos["tp_amount"] = float(tp_amount)
                        pos["target"] = round(entry + (pos["tp_amount"] / qty), 2) if pos["type"] == "BUY" else max(0.5, round(entry - (pos["tp_amount"] / qty), 2))
                    pos["is_custom_sltp"] = True
                    count += 1
            self.add_manual_log(f"BULK UPDATED {count} Manual {side_upper} positions: SL=₹{sl_amount or '--'} | TP=₹{tp_amount or '--'}")
            return True, f"Bulk updated {count} manual {side_upper} positions."

    def update_manual_settings(self, initial_capital=None, daily_loss_limit=None, risk_per_trade_pct=None, max_daily_trades=None, brokerage_per_trade=None):
        with self.lock:
            if initial_capital is not None and float(initial_capital) > 0:
                diff = float(initial_capital) - self.manual_initial_capital
                self.manual_initial_capital = float(initial_capital)
                self.manual_current_capital = max(0.0, self.manual_current_capital + diff)
            if daily_loss_limit is not None and float(daily_loss_limit) > 0:
                self.manual_daily_loss_limit = float(daily_loss_limit)
            if risk_per_trade_pct is not None and 0.1 <= float(risk_per_trade_pct) <= 10.0:
                self.manual_risk_per_trade_pct = float(risk_per_trade_pct)
            if max_daily_trades is not None and int(max_daily_trades) >= 0:
                self.manual_max_daily_trades = int(max_daily_trades)
            if brokerage_per_trade is not None and float(brokerage_per_trade) >= 0:
                self.manual_brokerage_per_trade = float(brokerage_per_trade)
            self.add_manual_log(f"Manual Desk Settings: Capital=₹{self.manual_initial_capital:,.2f}, Loss Limit=₹{self.manual_daily_loss_limit:,.2f}, Risk={self.manual_risk_per_trade_pct}%")
            self._save_settings()

    def add_manual_log(self, message):
        timestamp = datetime.now(IST).strftime("%H:%M:%S")
        entry = {"time": timestamp, "message": message}
        self.manual_logs.insert(0, entry)
        if len(self.manual_logs) > 60:
            self.manual_logs.pop()

    def _manage_manual_positions(self):
        total_unrealized = 0.0
        for pos in list(self.manual_active_positions):
            underlying_key = pos["underlying"]
            underlying_data = real_feed.get_symbol_data(underlying_key)
            if underlying_data:
                pos["current_price"] = round(underlying_data["current_price"], 2)
            
            if pos["type"] == "BUY":
                pnl = (pos["current_price"] - pos["entry_price"]) * pos["qty"]
            else:
                pnl = (pos["entry_price"] - pos["current_price"]) * pos["qty"]
                
            pos["pnl"] = round(pnl, 2)
            total_unrealized += pnl

            # Auto-Breakeven Shield
            be_trig = pos.get("breakeven_trigger", 0.005)
            if pos["type"] == "BUY":
                if not pos.get("breakeven_locked", False):
                    if pos["current_price"] >= pos["entry_price"] * (1.0 + be_trig):
                        cost_plus_sl = round(pos["entry_price"] * 1.001, 2)
                        if cost_plus_sl > pos["sl"]:
                            pos["sl"] = cost_plus_sl
                            pos["breakeven_locked"] = True
                            self.add_manual_log(f"🛡️ AUTO-BREAKEVEN LOCKED for {pos['symbol']}: SL shifted to Cost (₹{cost_plus_sl:.2f})")

                # Trailing SL
                trail_trig = pos.get("trail_trigger", 1.010)
                trail_dist = pos.get("trail_dist", 0.990)
                if pos["current_price"] >= pos["entry_price"] * trail_trig:
                    new_sl = round(pos["current_price"] * trail_dist, 2)
                    if new_sl > pos["sl"]:
                        pos["sl"] = new_sl
                        pos["trailing_active"] = True

                if pos["current_price"] <= pos["sl"] or pnl <= -abs(pos.get("sl_amount", 1000.0)):
                    exit_reason = f"Breakeven Cost-to-Cost (+₹{pnl:.2f})" if pos.get("breakeven_locked") and pnl >= -5.0 else f"Stop-Loss Hit (-₹{abs(pnl):.0f})"
                    self._close_manual_position_internal(pos, reason=exit_reason)
                    continue

                if pos["current_price"] >= pos["target"] or pnl >= abs(pos.get("tp_amount", 2000.0)):
                    self._close_manual_position_internal(pos, reason=f"Target Hit (+₹{pnl:.0f})")
                    continue
            else:
                if not pos.get("breakeven_locked", False):
                    if pos["current_price"] <= pos["entry_price"] * (1.0 - be_trig):
                        cost_minus_sl = round(pos["entry_price"] * 0.999, 2)
                        if cost_minus_sl < pos["sl"]:
                            pos["sl"] = cost_minus_sl
                            pos["breakeven_locked"] = True
                            self.add_manual_log(f"🛡️ AUTO-BREAKEVEN LOCKED for {pos['symbol']}: SL shifted to Cost (₹{cost_minus_sl:.2f})")

                trail_trig = pos.get("trail_trigger", 1.010)
                trail_dist = pos.get("trail_dist", 0.990)
                if pos["current_price"] <= pos["entry_price"] * (2 - trail_trig):
                    new_sl = round(pos["current_price"] * (2 - trail_dist), 2)
                    if new_sl < pos["sl"]:
                        pos["sl"] = new_sl
                        pos["trailing_active"] = True

                if pos["current_price"] >= pos["sl"] or pnl <= -abs(pos.get("sl_amount", 1000.0)):
                    exit_reason = f"Breakeven Cost-to-Cost (+₹{pnl:.2f})" if pos.get("breakeven_locked") and pnl >= -5.0 else f"Stop-Loss Hit (-₹{abs(pnl):.0f})"
                    self._close_manual_position_internal(pos, reason=exit_reason)
                    continue

                if pos["current_price"] <= pos["target"] or pnl >= abs(pos.get("tp_amount", 2000.0)):
                    self._close_manual_position_internal(pos, reason=f"Target Hit (+₹{pnl:.0f})")
                    continue

        self.manual_unrealized_pnl = round(total_unrealized, 2)

    def _close_position_internal(self, pos, reason="Square Off"):
        exit_price = pos["current_price"]
        pnl = (exit_price - pos["entry_price"]) * pos["qty"] if pos["type"] == "BUY" else (pos["entry_price"] - exit_price) * pos["qty"]
        
        self.realized_pnl += pnl
        self.total_trades += 1
        if pnl > 0:
            self.winning_trades += 1
        total_brokerage = self.total_trades * self.brokerage_per_trade
        self.current_capital = max(0.0, round(self.initial_capital + self.realized_pnl - total_brokerage, 2))
            
        trade_record = {
            "id": pos["id"],
            "symbol": pos["symbol"],
            "instrument": pos["instrument"],
            "type": pos["type"],
            "qty": pos["qty"],
            "entry_price": pos["entry_price"],
            "exit_price": round(exit_price, 2),
            "pnl": round(pnl, 2),
            "entry_time": pos.get("entry_time", datetime.now(IST).strftime("%H:%M:%S")),
            "exit_time": datetime.now(IST).strftime("%H:%M:%S"),
            "reason": reason,
            "style_tag": pos.get("style_tag", "TREND")
        }
        self.trade_history.insert(0, trade_record)
        if pos in self.active_positions:
            self.active_positions.remove(pos)

        # 5-Minute Re-Entry Cooldown Shield (Applies to ALL exits: Manual Exit, SL Hit, Target Hit)
        underlying = pos.get("underlying") or pos.get("symbol", "").split()[0]
        cooldown_expiry = time.time() + self.cooldown_duration
        self.stock_cooldowns[underlying] = cooldown_expiry
        self.stock_cooldowns[pos["symbol"]] = cooldown_expiry
        cd_expiry_time = (datetime.now(IST) + timedelta(seconds=self.cooldown_duration)).strftime("%H:%M:%S")
            
        pnl_str = f"+₹{pnl:,.2f}" if pnl >= 0 else f"-₹{abs(pnl):,.2f}"
        self.add_log(f"CLOSED {pos['symbol']} @ ₹{exit_price:.2f} | P&L: {pnl_str} | Reason: {reason}")
        self.add_log(f"⏳ 5-MIN COOLDOWN ACTIVE for {pos['symbol']}: Re-entry locked until {cd_expiry_time} IST (Anti-Chop Shield)")

    def _continuous_monitor_loop(self):
        while True:
            try:
                with self.lock:
                    if self.manual_active_positions:
                        self._manage_manual_positions()
                    if not self.is_running and self.active_positions:
                        self._manage_active_positions()
            except Exception:
                pass
            time.sleep(1.0)

    def _run_loop(self):
        while self.is_running:
            try:
                with self.lock:
                    self._cycle_step()
            except Exception as e:
                self.add_log(f"Cycle execution notice: {str(e)}")
            time.sleep(1.0)

    def _cycle_step(self):
        # 1. Update positions with REAL quotes from real_feed
        self._manage_active_positions()
        self._manage_manual_positions()

        # 2. Check Daily Loss Limit (Kill Switch)
        total_pnl = self.realized_pnl + self.unrealized_pnl
        if total_pnl <= -abs(self.daily_loss_limit):
            self.kill_switch_triggered = True
            self.kill_switch_reason = f"Daily Loss Limit reached (Total P&L: -₹{abs(total_pnl):,.2f} >= Limit -₹{self.daily_loss_limit:,.2f})"
            self.add_log(f"CRITICAL SAFETY TRIGGER: {self.kill_switch_reason}!")
            self._square_off_all_positions(reason="Kill Switch: Daily Loss Limit Hit")
            self.is_running = False
            return

        # 3. Market Time Check: Auto square-off at 15:15 IST during live market days
        now = datetime.now(IST)
        feed_status = real_feed.get_feed_status()
        if feed_status["is_market_open"] and now.hour == 15 and now.minute >= 15:
            if self.active_positions:
                self.add_log("15:15 IST reached: Auto squaring off all intraday positions.")
                self._square_off_all_positions(reason="3:15 PM Intraday Auto Square-Off")

        # 4. Scan Real Market Data & Execute Trades
        if len(self.active_positions) < self.max_open_positions:
            self._scan_and_execute_trades()

    def _manage_active_positions(self):
        total_unrealized = 0.0
        for pos in list(self.active_positions):
            underlying_key = pos["underlying"]
            underlying_data = real_feed.get_symbol_data(underlying_key)
            
            if underlying_data:
                real_spot = underlying_data["current_price"]
                base_price = pos["base_price"]
                pct_move = (real_spot - base_price) / max(1.0, base_price)
                
                if pos["instrument"] == "OPTION":
                    delta = 0.55 if "CE" in pos["symbol"] else -0.55
                    pos["current_price"] = round(max(1.0, pos["entry_price"] * (1 + pct_move * 15 * delta)), 2)
                else:
                    pos["current_price"] = round(real_spot, 2)
            
            # P&L calculation
            if pos["type"] == "BUY":
                pnl = (pos["current_price"] - pos["entry_price"]) * pos["qty"]
            else:
                pnl = (pos["entry_price"] - pos["current_price"]) * pos["qty"]
                
            pos["pnl"] = round(pnl, 2)
            total_unrealized += pnl

            # 1. Auto-Breakeven Shield (Zero Risk Lock-in when trade is in profit)
            be_trig = pos.get("breakeven_trigger", 0.005)
            if pos["type"] == "BUY":
                if not pos.get("breakeven_locked", False):
                    if pos["current_price"] >= pos["entry_price"] * (1.0 + be_trig):
                        cost_plus_sl = round(pos["entry_price"] * 1.001, 2)
                        if cost_plus_sl > pos["sl"]:
                            pos["sl"] = cost_plus_sl
                            pos["breakeven_locked"] = True
                            self.add_log(f"🛡️ AUTO-BREAKEVEN LOCKED for {pos['symbol']}: SL shifted to Cost (₹{cost_plus_sl:.2f}) [Zero-Risk Trade]")

                # 2. Dynamic Profile-based Trailing Stop-Loss
                trail_trig = pos.get("trail_trigger", 1.010)
                trail_dist = pos.get("trail_dist", 0.990)
                if pos["current_price"] >= pos["entry_price"] * trail_trig:
                    new_sl = round(pos["current_price"] * trail_dist, 2)
                    if new_sl > pos["sl"]:
                        pos["sl"] = new_sl
                        pos["trailing_active"] = True

                if pos["current_price"] <= pos["sl"] or pnl <= -abs(pos.get("sl_amount", 1000.0)):
                    exit_reason = f"Breakeven Cost-to-Cost (+₹{pnl:.2f})" if pos.get("breakeven_locked") and pnl >= -5.0 else f"Stop-Loss Hit (-₹{abs(pnl):.0f})"
                    self._close_position_internal(pos, reason=exit_reason)
                    continue

                if pos["current_price"] >= pos["target"] or pnl >= abs(pos.get("tp_amount", 2000.0)):
                    self._close_position_internal(pos, reason=f"Target Hit (+₹{pnl:.0f})")
                    continue
            else:
                if not pos.get("breakeven_locked", False):
                    if pos["current_price"] <= pos["entry_price"] * (1.0 - be_trig):
                        cost_minus_sl = round(pos["entry_price"] * 0.999, 2)
                        if cost_minus_sl < pos["sl"]:
                            pos["sl"] = cost_minus_sl
                            pos["breakeven_locked"] = True
                            self.add_log(f"🛡️ AUTO-BREAKEVEN LOCKED for {pos['symbol']}: SL shifted to Cost (₹{cost_minus_sl:.2f}) [Zero-Risk Trade]")

                # 2. Dynamic Profile-based Trailing Stop-Loss
                trail_trig = pos.get("trail_trigger", 1.010)
                trail_dist = pos.get("trail_dist", 0.990)
                if pos["current_price"] <= pos["entry_price"] * (2 - trail_trig):
                    new_sl = round(pos["current_price"] * (2 - trail_dist), 2)
                    if new_sl < pos["sl"]:
                        pos["sl"] = new_sl
                        pos["trailing_active"] = True

                if pos["current_price"] >= pos["sl"] or pnl <= -abs(pos.get("sl_amount", 1000.0)):
                    exit_reason = f"Breakeven Cost-to-Cost (+₹{pnl:.2f})" if pos.get("breakeven_locked") and pnl >= -5.0 else f"Stop-Loss Hit (-₹{abs(pnl):.0f})"
                    self._close_position_internal(pos, reason=exit_reason)
                    continue

                if pos["current_price"] <= pos["target"] or pnl >= abs(pos.get("tp_amount", 2000.0)):
                    self._close_position_internal(pos, reason=f"Target Hit (+₹{pnl:.0f})")
                    continue

        self.unrealized_pnl = round(total_unrealized, 2)

    def _scan_and_execute_trades(self):
        # Brokerage Shield 1: Check Max Daily Trades Cap
        if self.max_daily_trades > 0 and self.total_trades >= self.max_daily_trades:
            return

        # Feature 2: Time-of-Day Filter Check
        tw_status, tw_msg = self._get_time_window_status()
        if self.time_filter_active and tw_status in ["OPENING_VOLATILITY", "SQUARE_OFF", "MARKET_CLOSED"]:
            return

        universe_data = real_feed.get_all_data()
        candidates = []
        profile = STRATEGY_PROFILES.get(self.strategy_style, STRATEGY_PROFILES["TREND"])
        min_score = profile["min_score"]
        max_bear = profile["max_bear_score"]
        nifty_trend = real_feed.get_nifty_trend()
        
        for data in universe_data:
            key = data["id"]
            if any(p["underlying"] == key for p in self.active_positions):
                continue

            # 5-Minute Re-Entry Cooldown Shield (Prevents revenge trading, top-buying & zombie re-entry)
            now_ts = time.time()
            if key in self.stock_cooldowns:
                if now_ts < self.stock_cooldowns[key]:
                    continue
                else:
                    self.stock_cooldowns.pop(key, None)
            sym = data.get("symbol", key)
            if sym in self.stock_cooldowns:
                if now_ts < self.stock_cooldowns[sym]:
                    continue
                else:
                    self.stock_cooldowns.pop(sym, None)
                
            if data["type"] == "INDEX" and not self.enable_options:
                continue
            if data["type"] == "EQUITY" and not self.enable_equities:
                continue

            # Price Filter check: if active, only trade stocks matching price condition
            if self.price_filter_active:
                spot_price = data.get("current_price", 0.0)
                if self.price_filter_condition == "HIGHER" and spot_price < self.price_filter_amount:
                    continue
                if self.price_filter_condition == "LOWER" and spot_price > self.price_filter_amount:
                    continue

            # Feature 2: Midday Chop Check - only permit if volume is institutional surge (RVOL >= 2.5x)
            if self.time_filter_active and tw_status == "MIDDAY_CHOP" and data.get("rvol", 1.0) < 2.5:
                continue

            # Confidence score threshold based on active strategy profile & trade direction filter
            bias = None
            if self.trade_direction in ["BOTH", "BUY_ONLY"] and data["score"] >= min_score:
                bias = "BULLISH"
            elif self.trade_direction in ["BOTH", "SELL_ONLY"] and data["score"] <= max_bear:
                bias = "BEARISH"

            if not bias:
                continue

            # Feature 1: NIFTY Market Trend Filter (Don't swim against the tide!)
            if self.nifty_filter_active and key != "NIFTY":
                if bias == "BULLISH" and nifty_trend != "BULLISH":
                    continue  # NIFTY is falling, reject stock long
                if bias == "BEARISH" and nifty_trend != "BEARISH":
                    continue  # NIFTY is rising, reject stock short

            # Feature 3: 15m Multi-Timeframe Alignment
            if self.mtf_filter_active:
                mtf_15m = data.get("mtf_trend_15m", "BULLISH")
                if bias == "BULLISH" and mtf_15m != "BULLISH":
                    continue
                if bias == "BEARISH" and mtf_15m != "BEARISH":
                    continue

            # Feature 4: EMA Dynamic Ribbon Filter (9 EMA & 20 EMA)
            if self.ema_filter_active:
                ema_tr = data.get("ema_trend", "BULLISH")
                spot = data.get("current_price", 0.0)
                ema20 = data.get("ema20", spot)
                if bias == "BULLISH" and (ema_tr != "BULLISH" or spot < ema20):
                    continue
                if bias == "BEARISH" and (ema_tr != "BEARISH" or spot > ema20):
                    continue

            # Feature 5: VWAP Pullback Zone Filter (Avoid overbought top buying)
            if self.vwap_pullback_filter_active:
                vwap_val = data.get("vwap", 0.0)
                spot = data.get("current_price", 0.0)
                if vwap_val > 0:
                    dist_pct = ((spot - vwap_val) / vwap_val) * 100.0
                    max_allowed_dist = profile.get("max_vwap_dist", self.max_vwap_dist_pct)
                    if bias == "BULLISH" and dist_pct > max_allowed_dist:
                        continue  # Price over-extended above VWAP (top-buying trap)
                    if bias == "BEARISH" and dist_pct < -max_allowed_dist:
                        continue  # Price over-extended below VWAP (bottom-selling trap)

            # Feature 6: Institutional Volume Surge Filter (Minimum RVOL threshold)
            if self.rvol_filter_active:
                req_rvol = profile.get("min_rvol", self.min_rvol_threshold)
                if data.get("rvol", 1.0) < req_rvol:
                    continue  # Weak volume - avoid illiquid or retail false breakout

            candidates.append((key, bias, data))

        if not candidates:
            return

        candidates.sort(key=lambda x: abs(x[2]["score"] - 50), reverse=True)
        key, bias, data = candidates[0]
        meta = INSTRUMENT_MAP[key]

        trade_side = "BUY" if bias == "BULLISH" else "SELL"
        user_sl_amount = self.get_effective_sl_amount(trade_side)
        user_tp_amount = self.get_effective_tp_amount(trade_side)
        is_custom = (trade_side == "BUY" and (self.custom_buy_sl is not None or self.custom_buy_tp is not None)) or \
                    (trade_side == "SELL" and (self.custom_sell_sl is not None or self.custom_sell_tp is not None)) or \
                    (self.custom_trade_sl is not None or self.custom_trade_tp is not None)
        mode_tag = f"[Custom {trade_side} SL/TP]" if is_custom else "[Normal SL/TP]"

        if data["type"] == "INDEX" and self.enable_options:
            spot = data["current_price"]
            step = meta["step"]
            strike = int(round(spot / step) * step)
            lot_size = meta["lot_size"]

            opt_type = "CE" if bias == "BULLISH" else "PE"
            symbol = f"{key} {strike} {opt_type}"
            
            # Sizing derived directly from user's ₹ Stop Loss amount
            entry_price = round(spot * 0.0075, 2)
            loss_per_share = max(1.0, entry_price * (1.0 - profile["opt_sl"]))
            shares_allowed = max(lot_size, int((user_sl_amount / loss_per_share) // lot_size) * lot_size)

            sl_price = max(1.0, round(entry_price - (user_sl_amount / shares_allowed), 2))
            target_price = round(entry_price + (user_tp_amount / shares_allowed), 2)
            
            pos = {
                "id": f"POS_{int(time.time()*1000)%100000}",
                "symbol": symbol,
                "underlying": key,
                "instrument": "OPTION",
                "type": "BUY",
                "qty": shares_allowed,
                "base_price": spot,
                "entry_price": entry_price,
                "current_price": entry_price,
                "sl": sl_price,
                "target": target_price,
                "sl_amount": user_sl_amount,
                "tp_amount": user_tp_amount,
                "is_custom_sltp": is_custom,
                "trailing_active": False,
                "breakeven_locked": False,
                "breakeven_trigger": profile.get("breakeven_trigger", 0.05),
                "trail_trigger": profile["trail_trigger"],
                "trail_dist": profile["trail_dist"],
                "style_tag": profile["tag"],
                "pnl": 0.0,
                "entry_time": datetime.now(IST).strftime("%H:%M:%S"),
                "reason": f"{bias} Option ({opt_type}) • Score: {data['score']}% | Pattern: {data['pattern']}"
            }
            self.active_positions.append(pos)
            self.add_log(f"EXECUTED [{profile['name']}]: BUY {pos['qty']}x {symbol} @ ₹{entry_price:.2f} | Target: +₹{user_tp_amount:,.0f} (@ ₹{target_price:.2f}) | SL: -₹{user_sl_amount:,.0f} (@ ₹{sl_price:.2f}) {mode_tag}")

        elif data["type"] == "EQUITY" and self.enable_equities:
            entry_price = data["current_price"]
            loss_pct = max(0.008, 1.0 - profile.get("eq_sl", 0.988))
            loss_per_share = max(0.5, entry_price * loss_pct)
            shares_allowed = max(1, int(user_sl_amount / loss_per_share))

            if bias == "BULLISH":
                side = "BUY"
                sl_price = max(0.5, round(entry_price - (user_sl_amount / shares_allowed), 2))
                target_price = round(entry_price + (user_tp_amount / shares_allowed), 2)
                reason_str = f"Bullish Breakout (Long) • Score: {data['score']}% | Pattern: {data['pattern']}"
            else:
                side = "SELL"
                sl_price = round(entry_price + (user_sl_amount / shares_allowed), 2)
                target_price = max(0.5, round(entry_price - (user_tp_amount / shares_allowed), 2))
                reason_str = f"Bearish Breakdown (Short) • Score: {data['score']}% | Pattern: {data['pattern']}"

            pos = {
                "id": f"POS_{int(time.time()*1000)%100000}",
                "symbol": f"{key} MIS",
                "underlying": key,
                "instrument": "EQUITY",
                "type": side,
                "qty": shares_allowed,
                "base_price": entry_price,
                "entry_price": entry_price,
                "current_price": entry_price,
                "sl": sl_price,
                "target": target_price,
                "sl_amount": user_sl_amount,
                "tp_amount": user_tp_amount,
                "is_custom_sltp": is_custom,
                "trailing_active": False,
                "breakeven_locked": False,
                "breakeven_trigger": profile.get("breakeven_trigger", 0.005),
                "trail_trigger": profile["trail_trigger"],
                "trail_dist": profile["trail_dist"],
                "style_tag": profile["tag"],
                "pnl": 0.0,
                "entry_time": datetime.now(IST).strftime("%H:%M:%S"),
                "reason": reason_str
            }
            self.active_positions.append(pos)
            self.add_log(f"EXECUTED [{profile['name']}]: {side} {pos['qty']} shares {key} @ ₹{entry_price:.2f} | Target: +₹{user_tp_amount:,.0f} (@ ₹{target_price:.2f}) | SL: -₹{user_sl_amount:,.0f} (@ ₹{sl_price:.2f}) {mode_tag}")

    def get_state(self):
        with self.lock:
            self._manage_manual_positions()
            total_pnl = round(self.realized_pnl + self.unrealized_pnl, 2)
            loss_used_pct = round(min(100.0, (abs(min(0.0, total_pnl)) / self.daily_loss_limit) * 100.0), 1)
            win_rate = round((self.winning_trades / self.total_trades * 100.0), 1) if self.total_trades > 0 else 0.0
            feed_status = real_feed.get_feed_status()
            normal_sl = self.get_normal_sl_amount()
            normal_tp = self.get_normal_tp_amount()
            eff_sl = self.get_effective_sl_amount()
            eff_tp = self.get_effective_tp_amount()
            
            tw_status, tw_msg = self._get_time_window_status()
            nifty_trend = real_feed.get_nifty_trend()
            
            now_ts = time.time()
            active_cooldowns = {}
            for sym, exp in list(self.stock_cooldowns.items()):
                remaining = int(exp - now_ts)
                if remaining > 0:
                    active_cooldowns[sym] = remaining
                else:
                    self.stock_cooldowns.pop(sym, None)

            manual_tot_pnl = round(self.manual_realized_pnl + self.manual_unrealized_pnl, 2)
            manual_loss_used = round(min(100.0, (abs(min(0.0, manual_tot_pnl)) / max(1.0, self.manual_daily_loss_limit)) * 100.0), 1) if self.manual_daily_loss_limit > 0 else 0.0
            manual_win_rate = round((self.manual_winning_trades / self.manual_total_trades * 100.0), 1) if self.manual_total_trades > 0 else 0.0
            manual_brokerage = round(self.manual_total_trades * self.manual_brokerage_per_trade, 2)
            manual_net_pnl = round(manual_tot_pnl - manual_brokerage, 2)

            return {
                "is_running": self.is_running,
                "mode": self.mode,
                "feed_status": feed_status,
                "cooldowns": active_cooldowns,
                "initial_capital": self.initial_capital,
                "current_capital": round(self.current_capital + self.unrealized_pnl, 2),
                "daily_loss_limit": self.daily_loss_limit,
                "risk_per_trade_pct": self.risk_per_trade_pct,
                "risk_per_trade_amount": normal_sl,
                "normal_sl_amount": normal_sl,
                "normal_tp_amount": normal_tp,
                "custom_trade_sl": self.custom_trade_sl,
                "custom_trade_tp": self.custom_trade_tp,
                "custom_buy_sl": self.custom_buy_sl,
                "custom_buy_tp": self.custom_buy_tp,
                "custom_sell_sl": self.custom_sell_sl,
                "custom_sell_tp": self.custom_sell_tp,
                "effective_buy_sl": self.get_effective_sl_amount("BUY"),
                "effective_buy_tp": self.get_effective_tp_amount("BUY"),
                "effective_sell_sl": self.get_effective_sl_amount("SELL"),
                "effective_sell_tp": self.get_effective_tp_amount("SELL"),
                "effective_sl_amount": eff_sl,
                "effective_tp_amount": eff_tp,
                "trade_sl_amount": eff_sl,
                "trade_tp_amount": eff_tp,
                "strategy_style": self.strategy_style,
                "trade_direction": self.trade_direction,
                "price_filter_active": self.price_filter_active,
                "price_filter_condition": self.price_filter_condition,
                "price_filter_amount": self.price_filter_amount,
                "nifty_filter_active": self.nifty_filter_active,
                "time_filter_active": self.time_filter_active,
                "mtf_filter_active": self.mtf_filter_active,
                "ema_filter_active": self.ema_filter_active,
                "vwap_pullback_filter_active": self.vwap_pullback_filter_active,
                "rvol_filter_active": self.rvol_filter_active,
                "max_vwap_dist_pct": self.max_vwap_dist_pct,
                "min_rvol_threshold": self.min_rvol_threshold,
                "nifty_trend": nifty_trend,
                "time_window_status": tw_status,
                "time_window_message": tw_msg,
                "max_daily_trades": self.max_daily_trades,
                "brokerage_per_trade": self.brokerage_per_trade,
                "total_brokerage": round(self.total_trades * self.brokerage_per_trade, 2),
                "gross_pnl": total_pnl,
                "net_pnl": round(total_pnl - (self.total_trades * self.brokerage_per_trade), 2),
                "loss_used_pct": loss_used_pct,
                "realized_pnl": round(self.realized_pnl, 2),
                "unrealized_pnl": self.unrealized_pnl,
                "total_pnl": total_pnl,
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "win_rate": win_rate,
                "kill_switch_triggered": self.kill_switch_triggered,
                "kill_switch_reason": self.kill_switch_reason,
                "active_positions": list(self.active_positions),
                "trade_history": list(self.trade_history[:20]),
                "ai_logs": list(self.ai_logs[:30]),
                "scanner": real_feed.get_all_data(),
                "enable_options": self.enable_options,
                "enable_equities": self.enable_equities,
                "manual_desk": {
                    "initial_capital": self.manual_initial_capital,
                    "current_capital": round(self.manual_current_capital + self.manual_unrealized_pnl, 2),
                    "daily_loss_limit": self.manual_daily_loss_limit,
                    "risk_per_trade_pct": self.manual_risk_per_trade_pct,
                    "max_daily_trades": self.manual_max_daily_trades,
                    "brokerage_per_trade": self.manual_brokerage_per_trade,
                    "total_brokerage": manual_brokerage,
                    "gross_pnl": manual_tot_pnl,
                    "net_pnl": manual_net_pnl,
                    "realized_pnl": round(self.manual_realized_pnl, 2),
                    "unrealized_pnl": round(self.manual_unrealized_pnl, 2),
                    "loss_used_pct": manual_loss_used,
                    "total_trades": self.manual_total_trades,
                    "winning_trades": self.manual_winning_trades,
                    "win_rate": manual_win_rate,
                    "active_positions": list(self.manual_active_positions),
                    "trade_history": list(self.manual_trade_history[:20]),
                    "logs": list(self.manual_logs[:30])
                }
            }

engine = TradingEngine()
