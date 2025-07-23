# Options Scalper

Hey there! Welcome to the Options Scalper project. This is a trading application I've been working on, built to make scalping options on the Indian stock market a bit easier and more intuitive. It connects to Zerodha's Kite API to handle live data, place orders, and manage positions.

This started as a personal project to build a tool that fit my own trading style, but I'm sharing it here in case it's useful to anyone else. It's built with Python and the PySide6 (Qt) library for the user interface.

## What's Inside?
g
* **Live Market Data:** Hooks directly into the Kite API to get real-time ticks.
* **Strike Ladder:** A central part of the UI that shows a ladder of option strikes with live prices, making it easy to see what's happening around the current price.
* **Quick Trading:** Panels for quickly buying and selling calls/puts, and for exiting positions with a single click.
* **Position Tracking:** Keeps track of your open positions, showing your real-time profit and loss.
* **Market Monitor:** A separate window to monitor price charts for different indices.
* **Paper Trading Mode:** A built-in paper trading feature to test out strategies without risking real money.

## A Quick Look
![Screenshot](https://github.com/user-attachments/assets/b0085bd3-21d9-4bcf-adee-8ae887dbdf9b)



## Getting Started

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/KAVIARASU301/Options_Trading.git]
    cd options_scalper
    ```
2.  **Install the necessary libraries:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Set up your API keys:**
    You'll need to add your Zerodha Kite API key and secret in the configuration files.
4.  **Run the application:**
    ```bash
    python main.py
    ```

## Heads Up! (Disclaimer)

Please remember that this is a personal project. While I'm using it myself, it's not a professionally audited piece of software. Trading in the stock market carries significant risk, and you should be fully aware of what you're doing. Use this tool at your own risk, and I'd highly recommend running it in paper trading mode first to get comfortable with how it works.

Happy trading!
