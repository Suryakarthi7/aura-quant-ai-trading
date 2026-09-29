# ⚡ AURA QUANT — AI Intraday & Options Trading Station

[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Market: NSE / NIFTY](https://img.shields.io/badge/Market-NSE%20India-emerald.svg)](https://www.nseindia.com/)

A powerful, autonomous intraday algorithmic trading terminal and decision-support bot engineered specifically for Indian financial markets (NSE). Features an interactive real-time dashboard, institutional multi-factor confirmation filters, dynamic strategy models (Scalper, Trend Rider, Safe Sniper), and an automated Risk Management System (RMS).

---

## 🌟 Key Highlights

### 1. 📊 89+ Liquid NSE Stocks Live Radar
* Live second-by-second micro-tick streaming across 89+ top Indian equities and indices (NIFTY 50, Bank Nifty, Fin Nifty, Auto, IT, Banking, Metals, Energy, Defence, Railways, Green Energy, FMCG).
* Sector filtering tabs (`All`, `Indices`, `Banking`, `IT`, `Auto`, `Metals`, `Energy`, `Defence/PSU/FMCG`).
* Real-time technical indicators per stock: VWAP, 9/20 EMA ribbon, 15m Multi-Timeframe (MTF) trend, RVOL (Relative Volume), and composite AI Confidence Score.

### 2. 🎯 3 Institutional Strategy Models
* **⚡ Quick Scalper:**
  * Fast momentum breakouts with dynamic trailing stops.
  * Trigger: Score $\ge 70\%$ (or $\le 30\%$ for Bearish Breakdown).
  * Risk/Reward: 1:1.5 | RVOL $\ge 1.1\times$ | VWAP Pullback $\le 2.0\%$.
* **🏄 Trend Rider:**
  * Balanced trend continuation following the institutional order flow.
  * Trigger: Score $\ge 75\%$ (or $\le 25\%$ for Short).
  * Risk/Reward: 1:2.0 | RVOL $\ge 1.3\times$ | 9/20 EMA Ribbon Aligned | 15m MTF Bullish.
* **🎯 Safe Sniper (Ultra-High Precision):**
  * Maximum conviction setups designed for capital preservation.
  * Trigger: Score $\ge 80\%$ (or $\le 20\%$ for Short).
  * Risk/Reward: 1:2.5+ | RVOL $\ge 1.5\times$ Surge | VWAP Pullback $\le 1.2\%$ | Strict NIFTY Macro Alignment.

### 3. 🛡️ 6 Institutional Confirmation Filters (Accuracy Shield)
1. **NIFTY Trend Alignment:** Prevents taking long positions when the broader market index is in a downtrend (don't swim against the macro tide).
2. **Midday Chop Guard:** Automatically suppresses false breakouts during noon liquidity lulls (11:30 AM – 01:15 PM IST).
3. **15m MTF Alignment:** Ensures higher timeframe trend confirmation before 5m intraday entries.
4. **9/20 EMA Dynamic Ribbon:** Dynamic trend ribbon confirmation.
5. **VWAP Pullback Zone:** Filters out over-extended stocks to avoid getting trapped at the daily top.
6. **Institutional RVOL Surge:** Validates smart money volume participation ($\text{RVOL} \ge 1.5\times$).

### 4. 💰 Brokerage Shield & Risk Management (RMS)
* **Custom Demo/Live Capital & Loss Limits:** Set initial capital (e.g. ₹5,000) and max daily loss budget (e.g. ₹1,000). Settings persist automatically on disk (`user_settings.json`).
* **Max Daily Trades Cap:** Protects against overtrading and brokerage erosion (Default: 10 trades per day).
* **Automatic Kill-Switch:** Squares off all open positions immediately if the daily loss budget is reached.
* **Accurate Two-Way P&L Engine:** Native support for both Long (BUY) and Short (SELL) intraday positions.

---

## 🚀 Quick Start (Just Clone & Run)

### Prerequisites
* Python 3.8 or higher installed on your computer.

### Step 1: Clone the Repository
```bash
git clone https://github.com/Suryakarthi7/aura-quant-ai-trading.git
cd aura-quant-ai-trading
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```
*(Note: If you don't install external packages, the application will automatically fall back to Python's zero-dependency standard library mode!)*

### Step 3: Launch the Application

#### Option A: One-Click Launcher (Windows)
Double-click **`start_bot.bat`**. It will automatically connect to feeds and launch your default browser.

#### Option B: Terminal / Command Prompt
```bash
python app.py
```

### Step 4: Open in Your Browser
Visit:
```
http://localhost:5000
```

---

## 📖 Daily Intraday Workflow

1. **Configure Capital & Risk:** In the top Settings Card, adjust your starting capital (e.g., ₹5,000), Max Loss Limit (e.g., ₹1,000), and Risk per trade (e.g., 2%). Click **Save Settings** (persists automatically in `user_settings.json`).
2. **Select AI Strategy Style:** Choose between `⚡ Quick Scalper`, `🏄 Trend Rider`, or `🎯 Safe Sniper`.
3. **Toggle Model Filter:** Click `[ 🔍 Filter Model Stocks ]` to instantly filter the 89+ stocks down to only the candidates that meet your chosen strategy's strict criteria.
4. **Start Autonomous Bot:** Click **START AI BOT**. The engine continuously ranks candidates and enters the highest-conviction setup within your exact risk budget.
5. **Square Off:** Stop at any time using **EMERGENCY STOP**, or let the engine auto square-off at 15:15 IST before market close.

---

## 📁 Repository Structure

```
├── app.py                  # Flask / Standalone HTTP server & REST APIs
├── market_data.py          # Real NSE live feed gateway, 89-stock tick engine & indicators
├── trading_engine.py       # Autonomous AI trading engine, 6 institutional filters & RMS
├── trading_station.html    # Full-featured trading dashboard UI (Tailwind CSS, Vanilla JS)
├── templates/
│   └── index.html          # Web template entrypoint (mirrors trading_station.html)
├── user_settings.json      # Persistent disk storage for user risk & capital settings
├── start_bot.bat           # 1-Click Windows launch script
├── requirements.txt        # Python dependency list
├── .gitignore              # Standard git ignore definitions
└── README.md               # Complete project documentation
```

---

## ⚖️ License
This project is licensed under the MIT License.
For educational and paper trading purposes.
