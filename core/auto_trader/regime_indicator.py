"""
Regime Indicator Widget
=======================
Compact status pill shown in the AutoTraderDialog top bar.
Shows TREND · VOL · SESSION and ADX value at a glance.

Usage in auto_trader_dialog.py _setup_ui():
    from core.auto_trader.regime_indicator import RegimeIndicator
    self.regime_indicator = RegimeIndicator()
    top_bar.addWidget(self.regime_indicator)

Then call from _refresh_plot_only() / signal computation path:
    self.regime_indicator.update_regime(regime)   # MarketRegime instance
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


_TREND_COLORS = {
    "STRONG_TREND": "#00E676",
    "WEAK_TREND":   "#FFB300",
    "CHOP":         "#FF4D4D",
}
_VOL_COLORS = {
    "HIGH_VOL":   "#FF6B6B",
    "NORMAL_VOL": "#4D9FFF",
    "LOW_VOL":    "#8A99B3",
}
_TREND_ICONS = {
    "STRONG_TREND": "▲▲",
    "WEAK_TREND":   "▲",
    "CHOP":         "↔",
}
_VOL_ICONS = {
    "HIGH_VOL":   "🔥",
    "NORMAL_VOL": "◆",
    "LOW_VOL":    "❄",
}
_SESSION_COLORS = {
    "OPEN_DRIVE": "#FFD700",
    "MORNING":    "#9CCAF4",
    "MIDDAY":     "#8A99B3",
    "AFTERNOON":  "#8A99B3",
    "PRE_CLOSE":  "#FF9800",
}


class RegimeIndicator(QWidget):
    """
    Compact pill widget:
      [▲▲ STRONG TREND] [🔥 HIGH VOL] [MORNING] [ADX 31.4]
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)

        self._trend_lbl = self._make_pill("— —", "#3A4458", "#8A99B3")
        self._vol_lbl   = self._make_pill("— VOL", "#3A4458", "#8A99B3")
        self._sess_lbl  = self._make_pill("—", "#3A4458", "#8A99B3")
        self._adx_lbl   = self._make_pill("ADX —", "#3A4458", "#8A99B3")

        for w in (self._trend_lbl, self._vol_lbl, self._sess_lbl, self._adx_lbl):
            layout.addWidget(w)

    @staticmethod
    def _make_pill(text: str, bg: str, fg: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"color:{fg}; background:{bg}; border:1px solid #2A3650; "
            f"border-radius:3px; font-size:10px; font-weight:700; "
            f"padding:1px 6px; margin:0px 1px;"
        )
        lbl.setFixedHeight(20)
        return lbl

    def _set_pill(self, lbl: QLabel, text: str, bg: str, fg: str):
        lbl.setText(text)
        lbl.setStyleSheet(
            f"color:{fg}; background:{bg}40; border:1px solid {fg}55; "
            f"border-radius:3px; font-size:10px; font-weight:700; "
            f"padding:1px 6px; margin:0px 1px;"
        )

    def update_regime(self, regime):
        """Accept a MarketRegime dataclass and refresh all pills."""
        if regime is None:
            for lbl in (self._trend_lbl, self._vol_lbl, self._sess_lbl, self._adx_lbl):
                self._set_pill(lbl, "—", "#3A4458", "#8A99B3")
            return

        trend_c = _TREND_COLORS.get(regime.trend, "#8A99B3")
        vol_c   = _VOL_COLORS.get(regime.volatility, "#8A99B3")
        sess_c  = _SESSION_COLORS.get(regime.session, "#8A99B3")

        trend_text = f"{_TREND_ICONS.get(regime.trend,'')} {regime.trend.replace('_',' ')}"
        vol_text   = f"{_VOL_ICONS.get(regime.volatility,'')} {regime.volatility.replace('_',' ')}"
        sess_text  = regime.session.replace("_", " ")
        adx_text   = f"ADX {regime.adx_value:.1f}"

        self._set_pill(self._trend_lbl, trend_text, "#0D1117", trend_c)
        self._set_pill(self._vol_lbl,   vol_text,   "#0D1117", vol_c)
        self._set_pill(self._sess_lbl,  sess_text,  "#0D1117", sess_c)
        self._set_pill(self._adx_lbl,   adx_text,   "#0D1117", trend_c)

    def clear(self):
        self.update_regime(None)