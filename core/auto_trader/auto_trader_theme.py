"""
Auto Trader Dialog — Premium Visual Redesign
=============================================

HOW TO APPLY
────────────
In auto_trader_dialog.py, inside _setup_ui(), at the very top add:

    from core.auto_trader.dialog_theme import apply_dialog_theme, COMPACT_COMBO_STYLE, COMPACT_SPINBOX_STYLE
    apply_dialog_theme(self)
    compact_combo_style    = COMPACT_COMBO_STYLE
    compact_spinbox_style  = COMPACT_SPINBOX_STYLE
    compact_toggle_style   = COMPACT_TOGGLE_STYLE

Then replace existing root layout setup with the new toolbar builder from build_toolbar().
The chart backgrounds and signal feed sidebar are separate helpers below.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QSizePolicy,
    QVBoxLayout, QWidget, QScrollArea, QPushButton
)


# ═══════════════════════════════════════════════════════════════════
#  COLOUR TOKENS  (single source of truth)
# ═══════════════════════════════════════════════════════════════════
C = {
    "bg_deep":      "#080C12",
    "bg_base":      "#0D1117",
    "bg_panel":     "#111722",
    "bg_card":      "#151D2B",
    "bg_hover":     "#1C2638",
    "bg_active":    "#1E2D40",
    "border":       "#1E2D40",
    "border_b":     "#2A3F58",
    "teal":         "#00BFA5",
    "blue":         "#4D9FFF",
    "amber":        "#FFB300",
    "red":          "#FF4D4D",
    "green":        "#00E676",
    "text_1":       "#E8EDF5",
    "text_2":       "#8A99B3",
    "text_dim":     "#4A5568",
    "chart_bg":     "#0D1117",
    "grid":         "#1E2D40",
}


# ═══════════════════════════════════════════════════════════════════
#  MAIN STYLESHEET — paste this into setStyleSheet(...)
# ═══════════════════════════════════════════════════════════════════
DIALOG_STYLESHEET = f"""
/* ── WINDOW ─────────────────────────────────────────────────────── */
QDialog#autoTraderWindow {{
    background: {C["bg_base"]};
    color: {C["text_1"]};
    border: 1px solid {C["border"]};
}}

/* ── ALL LABELS ──────────────────────────────────────────────────── */
QLabel {{
    color: {C["text_2"]};
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-size: 11px;
    font-weight: 500;
    background: transparent;
}}

/* ── CONTROL BAR CONTAINER ───────────────────────────────────────── */
QWidget#controlBar {{
    background: {C["bg_deep"]};
    border-bottom: 1px solid {C["border"]};
    min-height: 46px;
    max-height: 46px;
}}

/* ── COMBOS ──────────────────────────────────────────────────────── */
QComboBox {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-weight: 600;
    font-size: 11px;
    padding: 3px 10px;
    border: 1px solid {C["border_b"]};
    border-radius: 6px;
    min-height: 28px;
    selection-background-color: {C["bg_active"]};
}}
QComboBox:hover {{
    border: 1px solid {C["blue"]};
    background: {C["bg_hover"]};
}}
QComboBox:focus {{
    border: 1px solid {C["blue"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {C["text_dim"]};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    border: 1px solid {C["border_b"]};
    border-radius: 6px;
    padding: 4px;
    selection-background-color: {C["bg_active"]};
    selection-color: {C["teal"]};
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    padding: 5px 10px;
    border-radius: 4px;
    min-height: 24px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: {C["bg_hover"]};
}}

/* ── SPINBOXES ───────────────────────────────────────────────────── */
QSpinBox, QDoubleSpinBox {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-weight: 600;
    font-size: 11px;
    border: 1px solid {C["border_b"]};
    border-radius: 6px;
    padding: 2px 6px;
    min-height: 26px;
    selection-background-color: {C["bg_active"]};
}}
QSpinBox:hover, QDoubleSpinBox:hover {{
    border: 1px solid {C["blue"]};
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {C["blue"]};
    background: {C["bg_hover"]};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 16px;
    border: none;
    background: transparent;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {C["text_dim"]};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {C["text_dim"]};
}}

/* ── CHECKBOXES ──────────────────────────────────────────────────── */
QCheckBox {{
    color: {C["text_2"]};
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-weight: 600;
    font-size: 11px;
    spacing: 5px;
}}
QCheckBox:hover {{
    color: {C["text_1"]};
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {C["border_b"]};
    border-radius: 4px;
    background: {C["bg_card"]};
}}
QCheckBox::indicator:hover {{
    border: 1px solid {C["blue"]};
}}
QCheckBox::indicator:checked {{
    background: {C["teal"]};
    border: 1px solid {C["teal"]};
    image: none;
}}
QCheckBox::indicator:checked:hover {{
    background: #00D4B8;
    border: 1px solid #00D4B8;
}}

/* ── PUSH BUTTONS ────────────────────────────────────────────────── */
QPushButton {{
    background: {C["bg_card"]};
    color: {C["text_2"]};
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    border: 1px solid {C["border_b"]};
    border-radius: 6px;
    padding: 4px 12px;
    min-height: 28px;
}}
QPushButton:hover {{
    background: {C["bg_hover"]};
    color: {C["text_1"]};
    border: 1px solid {C["blue"]};
}}
QPushButton:pressed {{
    background: {C["bg_active"]};
}}
QPushButton:disabled {{
    color: {C["text_dim"]};
    border-color: {C["border"]};
}}

/* ── AUTOMATE BUTTON (active state) ──────────────────────────────── */
QPushButton#automateActive {{
    background: rgba(0, 191, 165, 0.12);
    color: {C["teal"]};
    border: 1px solid {C["teal"]};
}}
QPushButton#automateActive:hover {{
    background: rgba(0, 191, 165, 0.2);
}}

/* ── SIMULATOR RUN BUTTON ────────────────────────────────────────── */
QPushButton#simRunBtn {{
    background: rgba(77, 159, 255, 0.1);
    color: {C["blue"]};
    border: 1px solid rgba(77, 159, 255, 0.4);
    min-width: 100px;
}}
QPushButton#simRunBtn:hover {{
    background: rgba(77, 159, 255, 0.18);
    border-color: {C["blue"]};
}}

/* ── SETUP BUTTON ────────────────────────────────────────────────── */
QPushButton#setupBtn {{
    color: {C["text_2"]};
    min-width: 70px;
}}

/* ── NAV BUTTONS (back/forward) ──────────────────────────────────── */
QPushButton#navBtn {{
    background: {C["bg_card"]};
    border: 1px solid {C["border_b"]};
    border-radius: 6px;
    min-width: 30px;
    max-width: 30px;
    font-size: 14px;
    padding: 0;
}}
QPushButton#navBtn:hover {{
    border-color: {C["blue"]};
    color: {C["text_1"]};
}}

/* ── FOCUS / LIVE TOGGLE ─────────────────────────────────────────── */
QPushButton[checkable="true"]:checked {{
    background: rgba(0, 191, 165, 0.12);
    color: {C["teal"]};
    border: 1px solid {C["teal"]};
}}

/* ── GROUP BOXES (setup dialog) ──────────────────────────────────── */
QGroupBox {{
    border: 1px solid {C["border"]};
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 12px;
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.06em;
    color: {C["text_2"]};
    text-transform: uppercase;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    background: {C["bg_deep"]};
}}
QGroupBox:hover {{
    border-color: {C["border_b"]};
}}

/* ── SCROLL BARS ─────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {C["bg_deep"]};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {C["border_b"]};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {C["text_dim"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

/* ── TOOLTIPS ────────────────────────────────────────────────────── */
QToolTip {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    border: 1px solid {C["border_b"]};
    border-radius: 6px;
    padding: 5px 10px;
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-size: 11px;
}}
"""


# ─────────────────────────────────────────────────────────────
#  Compact widget styles (pass as variables in _setup_ui)
# ─────────────────────────────────────────────────────────────
COMPACT_COMBO_STYLE = f"""
QComboBox {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-weight: 600;
    font-size: 11px;
    padding: 2px 10px;
    border: 1px solid {C["border_b"]};
    border-radius: 6px;
    min-height: 28px;
}}
QComboBox:hover {{ border: 1px solid {C["blue"]}; background: {C["bg_hover"]}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {C["text_dim"]};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    border: 1px solid {C["border_b"]};
    selection-background-color: {C["bg_active"]};
    selection-color: {C["teal"]};
    padding: 4px;
    outline: none;
}}
"""

COMPACT_SPINBOX_STYLE = f"""
QSpinBox, QDoubleSpinBox {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-weight: 600;
    font-size: 11px;
    border: 1px solid {C["border_b"]};
    border-radius: 6px;
    padding: 2px 6px;
    min-height: 26px;
}}
QSpinBox:hover, QDoubleSpinBox:hover {{ border: 1px solid {C["blue"]}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
"""

COMPACT_TOGGLE_STYLE = f"""
QCheckBox {{
    color: {C["text_2"]};
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-weight: 600;
    font-size: 11px;
    spacing: 5px;
}}
QCheckBox:hover {{ color: {C["text_1"]}; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {C["border_b"]};
    border-radius: 4px;
    background: {C["bg_card"]};
}}
QCheckBox::indicator:checked {{
    background: {C["teal"]};
    border-color: {C["teal"]};
}}
"""


# ═══════════════════════════════════════════════════════════════════
#  PYQTGRAPH CHART CONFIG  — call after plot widget creation
# ═══════════════════════════════════════════════════════════════════
def style_plot_widget(plot_widget, show_x_axis: bool = False):
    """Apply premium dark styling to a pyqtgraph PlotWidget."""
    import pyqtgraph as pg

    plot_widget.setBackground(C["bg_base"])

    # Grid — very subtle
    plot_widget.showGrid(x=True, y=True, alpha=0.08)

    # Y axis
    y_axis = plot_widget.getAxis("left")
    y_axis.setWidth(72)
    y_axis.setTextPen(pg.mkPen(C["text_dim"]))
    y_axis.setPen(pg.mkPen(C["border"]))
    y_axis.enableAutoSIPrefix(False)
    y_axis.setStyle(tickLength=-6, stopAxisAtTick=(True, True))

    # Bottom axis
    b_axis = plot_widget.getAxis("bottom")
    b_axis.setTextPen(pg.mkPen(C["text_dim"]))
    b_axis.setPen(pg.mkPen(C["border"]))
    b_axis.setHeight(28 if show_x_axis else 0)
    b_axis.setStyle(showValues=show_x_axis, tickLength=-4)

    # Remove right axis
    plot_widget.showAxis("right", False)

    # Tight margins between charts
    plot_widget.setContentsMargins(0, 0, 0, 0)


# ═══════════════════════════════════════════════════════════════════
#  SIGNAL FEED SIDEBAR
# ═══════════════════════════════════════════════════════════════════
SIDEBAR_STYLE = f"""
QWidget#signalSidebar {{
    background: {C["bg_deep"]};
    border-left: 1px solid {C["border"]};
}}

QLabel#sidebarTitle {{
    color: {C["text_dim"]};
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    background: {C["bg_deep"]};
    border-bottom: 1px solid {C["border"]};
    padding: 8px 14px;
}}

QFrame#signalCard {{
    background: {C["bg_card"]};
    border: 1px solid {C["border"]};
    border-radius: 8px;
}}
QFrame#signalCard:hover {{
    background: {C["bg_hover"]};
    border-color: {C["border_b"]};
}}
"""


class SignalFeedSidebar(QWidget):
    """
    Collapsible right-side signal feed panel.
    Attach to the auto trader dialog layout for a live signal log.

    Usage in _setup_ui():
        from core.auto_trader.dialog_theme import SignalFeedSidebar
        self._signal_sidebar = SignalFeedSidebar(self)
        # Add to the horizontal body layout beside charts
        body_layout.addWidget(self._signal_sidebar)

    When a signal fires, call:
        self._signal_sidebar.add_signal(payload)
    """

    STRATEGY_META = {
        "atr_reversal":   ("ATR REVERSAL",   C["teal"]),
        "ema_cross":      ("EMA CROSS",       C["blue"]),
        "range_breakout": ("BREAKOUT",        C["amber"]),
        "cvd_range_breakout": ("CVD BREAKOUT", "#FFC107"),
        "atr_divergence": ("ATR DIVERGE",     "#FF9800"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("signalSidebar")
        self.setFixedWidth(210)
        self.setStyleSheet(SIDEBAR_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        self._header = QLabel("Signal Feed")
        self._header.setObjectName("sidebarTitle")
        self._header.setFixedHeight(34)
        layout.addWidget(self._header)

        # Scroll area for cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                background: {C["bg_deep"]}; width: 5px; border-radius: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {C["border_b"]}; border-radius: 2px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        self._cards_widget = QWidget()
        self._cards_widget.setStyleSheet(f"background: {C['bg_deep']};")
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(8, 8, 8, 8)
        self._cards_layout.setSpacing(6)
        self._cards_layout.addStretch()

        scroll.setWidget(self._cards_widget)
        layout.addWidget(scroll)

        self._signal_count = 0
        self._update_header()

    def _update_header(self):
        count = self._signal_count
        label = "1 signal" if count == 1 else f"{count} signals"
        self._header.setText(f"Signal Feed  ·  {label}" if count else "Signal Feed")

    def add_signal(self, payload: dict):
        """Add a new signal card to the top of the feed."""
        strategy = payload.get("strategy_type", "atr_reversal")
        side = (payload.get("signal_side") or "long").lower()
        price = payload.get("price", 0.0)
        timestamp = payload.get("timestamp", "")

        try:
            from datetime import datetime
            dt = datetime.fromisoformat(timestamp)
            ts_str = dt.strftime("%H:%M")
        except Exception:
            ts_str = timestamp[-5:] if len(timestamp) >= 5 else timestamp

        strat_name, strat_color = self.STRATEGY_META.get(
            strategy, ("SIGNAL", C["text_2"])
        )
        dir_color = C["teal"] if side == "long" else C["red"]
        dir_label = "▲ LONG" if side == "long" else "▼ SHORT"
        accent = C["teal"] if side == "long" else C["red"]

        card = QFrame()
        card.setObjectName("signalCard")
        card.setStyleSheet(f"""
            QFrame {{
                background: {C["bg_card"]};
                border: 1px solid {C["border"]};
                border-radius: 8px;
                border-left: 3px solid {accent};
            }}
            QFrame:hover {{
                background: {C["bg_hover"]};
                border-color: {C["border_b"]};
                border-left: 3px solid {accent};
            }}
        """)

        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 7, 10, 7)
        cl.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(0)
        strat_lbl = QLabel(strat_name)
        strat_lbl.setStyleSheet(f"""
            font-family: "JetBrains Mono", monospace;
            font-size: 9px; font-weight: 700;
            letter-spacing: 0.06em; color: {strat_color};
        """)
        dir_lbl = QLabel(dir_label)
        dir_lbl.setStyleSheet(f"""
            font-family: "JetBrains Mono", monospace;
            font-size: 10px; font-weight: 700; color: {dir_color};
        """)
        top.addWidget(strat_lbl)
        top.addStretch()
        top.addWidget(dir_lbl)

        bottom = QHBoxLayout()
        bottom.setSpacing(0)
        price_lbl = QLabel(f"{price:,.2f}")
        price_lbl.setStyleSheet(f"""
            font-family: "JetBrains Mono", monospace;
            font-size: 13px; font-weight: 700; color: {C["text_1"]};
        """)
        time_lbl = QLabel(ts_str)
        time_lbl.setStyleSheet(f"""
            font-family: "JetBrains Mono", monospace;
            font-size: 10px; color: {C["text_dim"]};
        """)
        bottom.addWidget(price_lbl)
        bottom.addStretch()
        bottom.addWidget(time_lbl)

        cl.addLayout(top)
        cl.addLayout(bottom)

        # Insert before the stretch
        self._cards_layout.insertWidget(
            self._cards_layout.count() - 1, card
        )
        self._signal_count += 1
        self._update_header()

    def clear_signals(self):
        """Remove all signal cards."""
        while self._cards_layout.count() > 1:  # keep stretch
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._signal_count = 0
        self._update_header()


# ═══════════════════════════════════════════════════════════════════
#  STATUS BAR
# ═══════════════════════════════════════════════════════════════════
STATUS_BAR_STYLE = f"""
QWidget#statusBar {{
    background: {C["bg_deep"]};
    border-top: 1px solid {C["border"]};
    min-height: 26px;
    max-height: 26px;
}}
"""


class StatusBar(QWidget):
    """
    Slim status bar to attach at the bottom of the dialog.
    Replaces the simulator_summary_label position.

    Usage in _setup_ui(), after chart widgets:
        self._status_bar = StatusBar(self)
        root.addWidget(self._status_bar)

    Update fields:
        self._status_bar.set("sim_pnl", "+247 pts", "green")
        self._status_bar.set("mode", "LIVE", "teal")
    """

    VALUE_COLORS = {
        "teal":  C["teal"],
        "green": "#66BB6A",
        "amber": C["amber"],
        "red":   C["red"],
        "dim":   C["text_dim"],
        "white": C["text_1"],
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusBar")
        self.setStyleSheet(STATUS_BAR_STYLE)
        self.setFixedHeight(26)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(14, 0, 14, 0)
        self._layout.setSpacing(20)

        self._items: dict[str, QLabel] = {}

        # Default items
        self._add_item("mode",    "Mode",    "—",     "dim")
        self._add_sep()
        self._add_item("signals", "Signals", "0",     "dim")
        self._add_sep()
        self._add_item("sim_pnl", "Sim P&L", "—",     "dim")
        self._add_sep()
        self._add_item("chop",    "Chop",    "ON",    "amber")

        self._layout.addStretch()

        self._add_item("refresh", "Refresh", "3s", "dim")
        self._add_sep()
        self._conn_dot = QLabel("●")
        self._conn_dot.setStyleSheet(f"color: {C['teal']}; font-size: 10px;")
        self._layout.addWidget(self._conn_dot)
        self._add_item("conn", "", "Connected", "teal")

    def _add_item(self, key: str, label: str, value: str, color: str):
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        h = QHBoxLayout(wrapper)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)

        base_style = f"""
            font-family: "JetBrains Mono", "Consolas", monospace;
            font-size: 10px;
            background: transparent;
        """
        if label:
            lbl = QLabel(label.upper())
            lbl.setStyleSheet(base_style + f"""
                color: {C["text_dim"]};
                font-weight: 600;
                letter-spacing: 0.06em;
                font-size: 9px;
            """)
            h.addWidget(lbl)

        val = QLabel(value)
        hex_color = self.VALUE_COLORS.get(color, color)
        val.setStyleSheet(base_style + f"color: {hex_color}; font-weight: 700;")
        h.addWidget(val)

        self._items[key] = val
        self._layout.addWidget(wrapper)

    def _add_sep(self):
        sep = QLabel("·")
        sep.setStyleSheet(f"color: {C['border_b']}; font-size: 12px; background: transparent;")
        self._layout.addWidget(sep)

    def set(self, key: str, value: str, color: str = "white"):
        if key in self._items:
            lbl = self._items[key]
            hex_color = self.VALUE_COLORS.get(color, color)
            lbl.setText(value)
            lbl.setStyleSheet(
                lbl.styleSheet().split("color:")[0]
                + f"color: {hex_color}; font-weight: 700;"
            )

    def set_connected(self, connected: bool):
        color = C["teal"] if connected else C["red"]
        self._conn_dot.setStyleSheet(f"color: {color}; font-size: 10px;")
        self.set("conn", "Connected" if connected else "Disconnected",
                 "teal" if connected else "red")


# ═══════════════════════════════════════════════════════════════════
#  MAIN APPLY FUNCTION — call this at the top of _setup_ui()
# ═══════════════════════════════════════════════════════════════════
def apply_dialog_theme(dialog: QDialog):
    """
    One-shot: apply the premium stylesheet to the AutoTraderDialog.

    Call as the FIRST line of _setup_ui():
        apply_dialog_theme(self)
    """
    dialog.setStyleSheet(DIALOG_STYLESHEET)

    # Font: JetBrains Mono is free / open-source. Fallback to Consolas.
    font = QFont("JetBrains Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    if not font.exactMatch():
        font = QFont("Consolas")
    dialog.setFont(font)