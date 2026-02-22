# Options Badger

Options Badger is a desktop options trading terminal for Indian markets, built with **Python** and **PySide6 (Qt)**. It integrates with the **Zerodha Kite Connect API** for live market data and order execution, and includes a built-in paper trading mode for simulation.

> ⚠️ **Risk Disclosure:** Trading in options is high risk and may result in substantial losses. This software is provided for educational and personal-use purposes. Use at your own risk.

## Key Features

- **Live market connectivity** via Kite Connect.
- **Strike Ladder interface** for fast option-chain navigation around spot.
- **Quick buy/sell and exit controls** for scalping workflows.
- **Open positions and P&L tracking** in real time.
- **Paper trading mode** for strategy testing without live capital.
- **CVD and market monitor views** for market context.
- **Session and credential persistence** with encrypted local storage.
- **Execution stack v1** with routing abstraction, TWAP/VWAP/POV-style slicing, and fill-quality telemetry.

## Tech Stack

- Python
- PySide6 (Qt)
- Zerodha `kiteconnect`
- NumPy / Pandas / Matplotlib / PyQtGraph

## Project Structure

```text
Options_Badger/
├── core/        # Trading logic, login/session, workers, app window
├── widgets/     # Reusable UI components
├── dialogs/     # Dialog windows (settings, watchlist, history, etc.)
├── utils/       # Helpers for config, logging, calculations, sounds, etc.
├── assets/      # Icons, textures, and audio assets
├── main.py      # Application entry point
└── requirements.txt
```

## Prerequisites

- Python **3.10+** (recommended)
- A Zerodha Kite Connect app (API key/secret)
- Active internet connection during live usage

## Installation

1. Clone the repository:

   ```bash
   git clone <your-repo-url>
   cd Options_Badger
   ```

2. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate      # Linux/macOS
   # .venv\Scripts\activate       # Windows PowerShell
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Running the Application

```bash
python main.py
```

On startup, the login dialog allows you to:

- Enter Kite API credentials.
- Select **Live Trading** or **Paper Trading** mode.
- Complete Kite authentication in the browser.

## Configuration & Data Storage

Options Badger stores local encrypted session and credential data in:

```text
~/.options_badger/
```

This directory contains encrypted files for credentials/tokens and a local key used for decryption.

## Notes

- This project is currently optimized for desktop usage.
- Some features rely on market session timings and API availability.
- Keep your API credentials private and rotate them periodically.

## Institutional-Grade Auto-Trader Gaps (Current State)

The current stack is strong for discretionary and semi-systematic execution, but a true institutional-grade auto trader would additionally need:

- **Adaptive signal engines** (e.g., KAMA, volatility-scaled EMAs, or regime-switching trend filters) instead of fixed EMA windows such as 10/51 tuned to one instrument.
- **Regime detection and strategy orchestration** so strategy logic changes automatically across trend/range/high-volatility sessions.
- **Portfolio-level risk controls** (cross-symbol exposure caps, factor/greek concentration controls, and kill-switches at book level).
- **Robust transaction-cost and slippage models** continuously calibrated with live fill telemetry.
- **Walk-forward validation and drift monitoring** to detect alpha decay and disable stale models before losses compound.
- **Production resiliency controls** (redundant market data paths, deterministic replay, incident playbooks, and automated failover).

## Contributing

Contributions are welcome. If you submit a change, please:

1. Open an issue describing the enhancement or bug.
2. Keep changes focused and well-documented.
3. Ensure the app launches and core workflows still function.

## License

No license file is currently included in this repository. Add an explicit license before public redistribution.
