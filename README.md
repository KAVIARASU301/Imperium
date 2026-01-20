# Options Scalper

Hey there! Welcome to the Options Scalper project. This is a trading application I've been working on, built to make scalping options on the Indian stock market a bit easier and more intuitive. It connects to Zerodha's Kite API to handle live data, place orders, and manage positions.

This started as a personal project to build a tool that fit my own trading style, but I'm sharing it here in case it's useful to anyone else. It's built with Python and the PySide6 (Qt) library for the user interface.

## What's Inside?
* **Live Market Data:** Hooks directly into the Kite API to get real-time ticks.
* **Strike Ladder:** A central part of the UI that shows a ladder of option strikes with live prices, making it easy to see what's happening around the current price.
* **Quick Trading:** Panels for quickly buying and selling calls/puts, and for exiting positions with a single click.
* **Position Tracking:** Keeps track of your open positions, showing your real-time profit and loss.
* **Market Monitor:** A separate window to monitor price charts for different indices.
* **CVD Monitor:** A separate window to monitor price charts for different indices.
* **Paper Trading Mode:** A built-in paper trading feature to test out strategies without risking real money.

Happy trading!
