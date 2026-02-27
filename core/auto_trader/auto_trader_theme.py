"""
Auto Trader Dialog — Institutional Visual Theme v2
===================================================

Institutional design principles applied:
  • Bloomberg Terminal-inspired density with Bloomberg-grade readability
  • Strict 4px spatial grid — every margin/padding is a multiple of 4
  • Semantic color tokens: signal green/red are distinct from UI teal/blue
  • Micro-typography: label caps, numeric tabular figures, tight tracking
  • Three-tier text hierarchy (primary / secondary / muted)
  • Edge-lit panels: top highlight border creates depth without heavy shadows
  • Status pulse animation for live connection dot

HOW TO APPLY
────────────
In auto_trader_dialog.py, inside _setup_ui(), at the very top add:

    from core.auto_trader.dialog_theme import (
        apply_dialog_theme,
        COMPACT_COMBO_STYLE,
        COMPACT_SPINBOX_STYLE,
        COMPACT_TOGGLE_STYLE,
    )
    apply_dialog_theme(self)
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QSizePolicy,
    QVBoxLayout, QWidget, QScrollArea, QPushButton, QGraphicsOpacityEffect
)


# ═══════════════════════════════════════════════════════════════════
#  COLOUR SYSTEM — semantic + primitive tokens
#  Institutional principle: never reference a hex twice.
#  If you want to change "the green for profits" you change one line.
# ═══════════════════════════════════════════════════════════════════
C = {
    # ── Backgrounds (layered depth, darkest → lightest) ──────────────
    "bg_void":      "#050810",   # modal overlay / true black
    "bg_deep":      "#080C13",   # outermost shell, toolbar, status bar
    "bg_base":      "#0C1018",   # main window
    "bg_panel":     "#101520",   # panels, sidebar
    "bg_card":      "#141C28",   # cards, inputs
    "bg_hover":     "#192333",   # hover state
    "bg_active":    "#1D2A3E",   # pressed / selected

    # ── Borders ───────────────────────────────────────────────────────
    "border_dim":   "#141C28",   # subtle dividers
    "border":       "#1E2D42",   # standard borders
    "border_hi":    "#2A3F5A",   # emphasized borders
    "border_focus": "#3A5A80",   # focus ring

    # ── Edge-lit top highlight (creates raised panel illusion) ────────
    "edge_top":     "#1F2E44",   # 1px top border on panels

    # ── Brand / Accent ────────────────────────────────────────────────
    "teal":         "#00C9AD",   # primary brand accent
    "teal_dim":     "rgba(0,201,173,0.12)",
    "teal_glow":    "rgba(0,201,173,0.08)",
    "blue":         "#4D9FFF",   # secondary accent / info
    "blue_dim":     "rgba(77,159,255,0.12)",
    "violet":       "#9D7FEA",   # tertiary accent (alerts)

    # ── Semantic: P&L ─────────────────────────────────────────────────
    "profit":       "#26D07C",   # confirmed profit / long signal
    "profit_dim":   "rgba(38,208,124,0.12)",
    "loss":         "#F05A5A",   # confirmed loss / short signal
    "loss_dim":     "rgba(240,90,90,0.12)",
    "warn":         "#FFB020",   # warnings, caution
    "warn_dim":     "rgba(255,176,32,0.12)",

    # ── Text (three tiers) ────────────────────────────────────────────
    "text_1":       "#DDE5F0",   # primary — values, important labels
    "text_2":       "#7A8FA8",   # secondary — field labels, descriptions
    "text_3":       "#3D5068",   # muted — separators, placeholders

    # ── Chart ─────────────────────────────────────────────────────────
    "chart_bg":     "#0C1018",
    "grid":         "#141C28",
    "grid_hi":      "#1E2D42",
}


# ═══════════════════════════════════════════════════════════════════
#  TYPOGRAPHY SYSTEM
#  JetBrains Mono for all numerics / code — tabular figures = aligned columns
#  Fallback chain covers Windows (Consolas), macOS (Menlo), Linux (monospace)
# ═══════════════════════════════════════════════════════════════════
FONT_MONO  = '"JetBrains Mono", "Consolas", "Menlo", monospace'
FONT_UI    = '"Inter", "Segoe UI", "SF Pro Display", sans-serif'

# Reusable text style snippets
T1 = f"color: {C['text_1']}; font-family: {FONT_MONO}; font-weight: 600;"
T2 = f"color: {C['text_2']}; font-family: {FONT_MONO}; font-weight: 500;"
T3 = f"color: {C['text_3']}; font-family: {FONT_MONO}; font-weight: 500;"
LABEL_CAPS = f"font-size: 9px; letter-spacing: 0.08em; text-transform: uppercase;"


# ═══════════════════════════════════════════════════════════════════
#  MASTER STYLESHEET
# ═══════════════════════════════════════════════════════════════════
DIALOG_STYLESHEET = f"""

/* ═══ WINDOW ════════════════════════════════════════════════════════ */
QDialog#autoTraderWindow {{
    background: {C["bg_base"]};
    color: {C["text_1"]};
    border: 1px solid {C["border"]};
}}

/* ═══ GLOBAL LABEL RESET ════════════════════════════════════════════ */
QLabel {{
    color: {C["text_2"]};
    font-family: {FONT_MONO};
    font-size: 11px;
    font-weight: 500;
    background: transparent;
}}

/* ═══ CONTROL BAR ═══════════════════════════════════════════════════ */
/* Institutional bars have a 1px highlight on top and muted bottom border */
QWidget#controlBar {{
    background: {C["bg_deep"]};
    border-top: 1px solid {C["edge_top"]};
    border-bottom: 1px solid {C["border_dim"]};
    min-height: 48px;
    max-height: 48px;
}}

/* ═══ COMBO BOXES ════════════════════════════════════════════════════ */
QComboBox {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    font-family: {FONT_MONO};
    font-weight: 600;
    font-size: 11px;
    padding: 0px 10px;
    border: 1px solid {C["border"]};
    border-radius: 4px;
    min-height: 28px;
    selection-background-color: {C["bg_active"]};
}}
QComboBox:hover {{
    border: 1px solid {C["border_hi"]};
    background: {C["bg_hover"]};
}}
QComboBox:focus {{
    border: 1px solid {C["border_focus"]};
    outline: none;
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
    border-left: 1px solid {C["border_dim"]};
    background: transparent;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {C["text_3"]};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    border: 1px solid {C["border_hi"]};
    border-radius: 4px;
    padding: 4px;
    selection-background-color: {C["bg_active"]};
    selection-color: {C["teal"]};
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 12px;
    border-radius: 3px;
    min-height: 24px;
    font-size: 11px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: {C["bg_hover"]};
}}

/* ═══ SPIN BOXES ═════════════════════════════════════════════════════ */
QSpinBox, QDoubleSpinBox {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    font-family: {FONT_MONO};
    font-weight: 600;
    font-size: 11px;
    border: 1px solid {C["border"]};
    border-radius: 4px;
    padding: 0px 6px;
    min-height: 28px;
    selection-background-color: {C["bg_active"]};
}}
QSpinBox:hover, QDoubleSpinBox:hover {{
    border: 1px solid {C["border_hi"]};
    background: {C["bg_hover"]};
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {C["border_focus"]};
    background: {C["bg_hover"]};
    outline: none;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 18px;
    border: none;
    border-left: 1px solid {C["border_dim"]};
    background: transparent;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {C["bg_hover"]};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid {C["text_3"]};
    margin: 0;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {C["text_3"]};
    margin: 0;
}}

/* ═══ CHECKBOXES ═════════════════════════════════════════════════════ */
QCheckBox {{
    color: {C["text_2"]};
    font-family: {FONT_MONO};
    font-weight: 600;
    font-size: 11px;
    spacing: 6px;
}}
QCheckBox:hover {{ color: {C["text_1"]}; }}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {C["border_hi"]};
    border-radius: 3px;
    background: {C["bg_card"]};
}}
QCheckBox::indicator:hover {{
    border: 1px solid {C["teal"]};
}}
QCheckBox::indicator:checked {{
    background: {C["teal"]};
    border: 1px solid {C["teal"]};
    image: none;
}}
QCheckBox::indicator:checked:hover {{
    background: #00DFC0;
}}

/* ═══ PUSH BUTTONS ════════════════════════════════════════════════════ */
QPushButton {{
    background: {C["bg_card"]};
    color: {C["text_2"]};
    font-family: {FONT_MONO};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    border: 1px solid {C["border"]};
    border-radius: 4px;
    padding: 0px 12px;
    min-height: 28px;
}}
QPushButton:hover {{
    background: {C["bg_hover"]};
    color: {C["text_1"]};
    border: 1px solid {C["border_hi"]};
}}
QPushButton:pressed {{
    background: {C["bg_active"]};
    border: 1px solid {C["border_focus"]};
}}
QPushButton:disabled {{
    color: {C["text_3"]};
    border-color: {C["border_dim"]};
    background: {C["bg_panel"]};
}}

/* Automate: live/active state */
QPushButton#automateActive {{
    background: {C["teal_dim"]};
    color: {C["teal"]};
    border: 1px solid {C["teal"]};
}}
QPushButton#automateActive:hover {{
    background: rgba(0,201,173,0.18);
}}

/* Simulator Run — blue accent */
QPushButton#simRunBtn {{
    background: {C["blue_dim"]};
    color: {C["blue"]};
    border: 1px solid rgba(77,159,255,0.35);
    min-width: 96px;
}}
QPushButton#simRunBtn:hover {{
    background: rgba(77,159,255,0.2);
    border-color: {C["blue"]};
}}

/* Setup / config button */
QPushButton#setupBtn {{
    color: {C["text_2"]};
    min-width: 68px;
}}

/* Nav prev/next arrows */
QPushButton#navBtn {{
    background: {C["bg_card"]};
    color: {C["text_2"]};
    border: 1px solid {C["border"]};
    border-radius: 4px;
    min-width: 28px;
    max-width: 28px;
    min-height: 28px;
    max-height: 28px;
    font-size: 13px;
    padding: 0;
}}
QPushButton#navBtn:hover {{
    border-color: {C["border_focus"]};
    color: {C["text_1"]};
}}

/* Live / Focus toggle — checked state */
QPushButton[checkable="true"]:checked {{
    background: {C["teal_dim"]};
    color: {C["teal"]};
    border: 1px solid {C["teal"]};
}}

/* ═══ GROUP BOXES ═════════════════════════════════════════════════════ */
QGroupBox {{
    border: 1px solid {C["border"]};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 14px;
    font-family: {FONT_MONO};
    font-weight: 700;
    font-size: 9px;
    letter-spacing: 0.10em;
    color: {C["text_3"]};
    text-transform: uppercase;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    background: {C["bg_base"]};
    color: {C["text_3"]};
}}
QGroupBox:hover {{
    border-color: {C["border_hi"]};
}}

/* ═══ SCROLL BARS ═════════════════════════════════════════════════════ */
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    border-radius: 3px;
    margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {C["border_hi"]};
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {C["text_3"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
    border-radius: 3px;
    margin: 0 2px;
}}
QScrollBar::handle:horizontal {{
    background: {C["border_hi"]};
    border-radius: 3px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {C["text_3"]};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ═══ TOOLTIPS ════════════════════════════════════════════════════════ */
QToolTip {{
    background: {C["bg_panel"]};
    color: {C["text_1"]};
    border: 1px solid {C["border_hi"]};
    border-radius: 4px;
    padding: 6px 10px;
    font-family: {FONT_MONO};
    font-size: 10px;
    font-weight: 500;
}}

/* ═══ SEPARATOR LINES ════════════════════════════════════════════════ */
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {C["border_dim"]};
    background: {C["border_dim"]};
}}

/* ═══ TAB BAR (if used) ══════════════════════════════════════════════ */
QTabBar::tab {{
    background: {C["bg_panel"]};
    color: {C["text_2"]};
    font-family: {FONT_MONO};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    border: 1px solid {C["border"]};
    border-bottom: none;
    padding: 6px 16px;
    border-radius: 3px 3px 0 0;
}}
QTabBar::tab:selected {{
    background: {C["bg_base"]};
    color: {C["teal"]};
    border-bottom: 2px solid {C["teal"]};
}}
QTabBar::tab:hover {{
    background: {C["bg_hover"]};
    color: {C["text_1"]};
}}

/* ═══ LINE EDITS (if used) ═══════════════════════════════════════════ */
QLineEdit {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    font-family: {FONT_MONO};
    font-size: 11px;
    font-weight: 600;
    border: 1px solid {C["border"]};
    border-radius: 4px;
    padding: 0px 8px;
    min-height: 28px;
    selection-background-color: {C["bg_active"]};
}}
QLineEdit:focus {{
    border: 1px solid {C["border_focus"]};
    background: {C["bg_hover"]};
    outline: none;
}}
QLineEdit:hover {{
    border: 1px solid {C["border_hi"]};
}}
QLineEdit::placeholder {{
    color: {C["text_3"]};
}}
"""


# ─────────────────────────────────────────────────────────────────────
#  COMPACT WIDGET STYLES — override defaults for tight toolbar controls
# ─────────────────────────────────────────────────────────────────────
COMPACT_COMBO_STYLE = f"""
QComboBox {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    font-family: {FONT_MONO};
    font-weight: 600;
    font-size: 11px;
    padding: 0px 10px;
    border: 1px solid {C["border"]};
    border-radius: 4px;
    min-height: 28px;
}}
QComboBox:hover {{ border: 1px solid {C["border_hi"]}; background: {C["bg_hover"]}; }}
QComboBox:focus {{ border: 1px solid {C["border_focus"]}; outline: none; }}
QComboBox::drop-down {{ border: none; border-left: 1px solid {C["border_dim"]}; width: 22px; background: transparent; }}
QComboBox::down-arrow {{
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {C["text_3"]};
    margin-right: 7px;
}}
QComboBox QAbstractItemView {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    border: 1px solid {C["border_hi"]};
    selection-background-color: {C["bg_active"]};
    selection-color: {C["teal"]};
    padding: 4px;
    outline: none;
    font-size: 11px;
}}
QComboBox QAbstractItemView::item {{
    padding: 5px 12px;
    border-radius: 3px;
    min-height: 22px;
}}
QComboBox QAbstractItemView::item:hover {{ background: {C["bg_hover"]}; }}
"""

COMPACT_SPINBOX_STYLE = f"""
QSpinBox, QDoubleSpinBox {{
    background: {C["bg_card"]};
    color: {C["text_1"]};
    font-family: {FONT_MONO};
    font-weight: 600;
    font-size: 11px;
    border: 1px solid {C["border"]};
    border-radius: 4px;
    padding: 0px 6px;
    min-height: 28px;
}}
QSpinBox:hover, QDoubleSpinBox:hover {{
    border: 1px solid {C["border_hi"]};
    background: {C["bg_hover"]};
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {C["border_focus"]};
    background: {C["bg_hover"]};
    outline: none;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 17px; border: none;
    border-left: 1px solid {C["border_dim"]};
    background: transparent;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {C["bg_hover"]};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid {C["text_3"]};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {C["text_3"]};
}}
"""

COMPACT_TOGGLE_STYLE = f"""
QCheckBox {{
    color: {C["text_2"]};
    font-family: {FONT_MONO};
    font-weight: 600;
    font-size: 11px;
    spacing: 6px;
}}
QCheckBox:hover {{ color: {C["text_1"]}; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {C["border_hi"]};
    border-radius: 3px;
    background: {C["bg_card"]};
}}
QCheckBox::indicator:hover {{ border: 1px solid {C["teal"]}; }}
QCheckBox::indicator:checked {{
    background: {C["teal"]};
    border-color: {C["teal"]};
}}
"""


# ═══════════════════════════════════════════════════════════════════
#  PYQTGRAPH CHART CONFIG
#  Institutional: near-zero grid alpha, tight axis labels, no clutter
# ═══════════════════════════════════════════════════════════════════
def style_plot_widget(plot_widget, show_x_axis: bool = False):
    """Apply institutional dark styling to a pyqtgraph PlotWidget."""
    import pyqtgraph as pg

    plot_widget.setBackground(C["chart_bg"])

    # Extremely subtle grid — institutions use almost-invisible grids
    plot_widget.showGrid(x=True, y=True, alpha=0.06)

    # Y axis — right-aligned labels, tabular monospace
    y_axis = plot_widget.getAxis("left")
    y_axis.setWidth(68)
    y_axis.setTextPen(pg.mkPen(color=C["text_3"], width=1))
    y_axis.setPen(pg.mkPen(color=C["border_dim"], width=1))
    y_axis.enableAutoSIPrefix(False)
    y_axis.setStyle(tickLength=-5, stopAxisAtTick=(True, True))

    # Bottom axis
    b_axis = plot_widget.getAxis("bottom")
    b_axis.setTextPen(pg.mkPen(color=C["text_3"], width=1))
    b_axis.setPen(pg.mkPen(color=C["border_dim"], width=1))
    b_axis.setHeight(26 if show_x_axis else 0)
    b_axis.setStyle(showValues=show_x_axis, tickLength=-4)

    # Remove right/top axes — clean, no-redundancy institutional look
    plot_widget.showAxis("right", False)
    plot_widget.showAxis("top", False)

    plot_widget.setContentsMargins(0, 0, 0, 0)


# ═══════════════════════════════════════════════════════════════════
#  SIGNAL FEED SIDEBAR
#  Institutional pattern: event log as a right-panel card stack.
#  Each card uses color-coded left border for immediate side recognition.
# ═══════════════════════════════════════════════════════════════════
SIDEBAR_STYLE = f"""
QWidget#signalSidebar {{
    background: {C["bg_panel"]};
    border-left: 1px solid {C["border_dim"]};
}}

QLabel#sidebarTitle {{
    color: {C["text_3"]};
    font-family: {FONT_MONO};
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    background: {C["bg_deep"]};
    border-bottom: 1px solid {C["border_dim"]};
    padding: 0px 12px;
}}

QFrame#signalCard {{
    background: {C["bg_card"]};
    border: 1px solid {C["border_dim"]};
    border-radius: 4px;
}}
QFrame#signalCard:hover {{
    background: {C["bg_hover"]};
    border-color: {C["border"]};
}}
"""


class SignalFeedSidebar(QWidget):
    """
    Right-panel signal event log.

    Institutional concept: Order Flow Tape — every signal is a timestamped
    event with direction, strategy type, and execution price. Think of it
    like a mini DOM (Depth of Market) event log.

    Usage:
        self._signal_sidebar = SignalFeedSidebar(self)
        body_layout.addWidget(self._signal_sidebar)

        # On signal fire:
        self._signal_sidebar.add_signal(payload)
    """

    # Strategy display metadata: (display_name, accent_color)
    STRATEGY_META = {
        "atr_reversal":       ("ATR REV",     C["teal"]),
        "ema_cross":          ("EMA CROSS",   C["blue"]),
        "range_breakout":     ("BREAKOUT",    C["warn"]),
        "cvd_range_breakout": ("CVD BRKOUT",  "#FFC107"),
        "atr_divergence":     ("ATR DIV",     C["violet"]),
    }

    MAX_CARDS = 80  # Memory guard — discard old cards beyond this

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("signalSidebar")
        self.setFixedWidth(204)
        self.setStyleSheet(SIDEBAR_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────
        self._header = QLabel("SIGNALS")
        self._header.setObjectName("sidebarTitle")
        self._header.setFixedHeight(32)
        self._header.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        layout.addWidget(self._header)

        # ── Count badge row ───────────────────────────────────────────
        badge_row = QWidget()
        badge_row.setStyleSheet(f"""
            background: {C["bg_deep"]};
            border-bottom: 1px solid {C["border_dim"]};
        """)
        badge_row.setFixedHeight(24)
        badge_layout = QHBoxLayout(badge_row)
        badge_layout.setContentsMargins(12, 0, 12, 0)

        self._count_label = QLabel("0 events")
        self._count_label.setStyleSheet(f"""
            color: {C["text_3"]};
            font-family: {FONT_MONO};
            font-size: 9px;
            font-weight: 600;
            letter-spacing: 0.04em;
        """)
        self._long_count = QLabel("▲ 0")
        self._long_count.setStyleSheet(f"""
            color: {C["profit"]};
            font-family: {FONT_MONO};
            font-size: 9px;
            font-weight: 700;
        """)
        self._short_count = QLabel("▼ 0")
        self._short_count.setStyleSheet(f"""
            color: {C["loss"]};
            font-family: {FONT_MONO};
            font-size: 9px;
            font-weight: 700;
        """)
        badge_layout.addWidget(self._count_label)
        badge_layout.addStretch()
        badge_layout.addWidget(self._long_count)
        badge_layout.addSpacing(8)
        badge_layout.addWidget(self._short_count)
        layout.addWidget(badge_row)

        # ── Scroll area ───────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                background: {C["bg_panel"]}; width: 4px; border-radius: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {C["border_hi"]}; border-radius: 2px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        self._cards_widget = QWidget()
        self._cards_widget.setStyleSheet(f"background: {C['bg_panel']};")
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(6, 6, 6, 6)
        self._cards_layout.setSpacing(4)
        self._cards_layout.addStretch()

        scroll.setWidget(self._cards_widget)
        layout.addWidget(scroll)

        self._signal_count = 0
        self._long_c = 0
        self._short_c = 0

    def _update_header(self):
        n = self._signal_count
        self._count_label.setText(f"{n} event{'s' if n != 1 else ''}")
        self._long_count.setText(f"▲ {self._long_c}")
        self._short_count.setText(f"▼ {self._short_c}")

    def add_signal(self, payload: dict):
        """Add a new signal card to the top of the feed."""
        strategy = payload.get("strategy_type", "atr_reversal")
        side = (payload.get("signal_side") or "long").lower()
        price = payload.get("price", 0.0)
        timestamp = payload.get("timestamp", "")

        try:
            from datetime import datetime
            dt = datetime.fromisoformat(timestamp)
            ts_str = dt.strftime("%H:%M:%S")
        except Exception:
            ts_str = timestamp[-8:] if len(timestamp) >= 8 else timestamp

        strat_name, strat_color = self.STRATEGY_META.get(
            strategy, ("SIGNAL", C["text_2"])
        )

        is_long = side == "long"
        dir_color = C["profit"] if is_long else C["loss"]
        dir_label = "▲ LONG" if is_long else "▼ SHORT"
        accent = C["profit"] if is_long else C["loss"]
        bg_tint = C["profit_dim"] if is_long else C["loss_dim"]

        # ── Build card ────────────────────────────────────────────────
        card = QFrame()
        card.setObjectName("signalCard")
        card.setStyleSheet(f"""
            QFrame#signalCard {{
                background: {C["bg_card"]};
                border: 1px solid {C["border_dim"]};
                border-left: 3px solid {accent};
                border-radius: 4px;
            }}
            QFrame#signalCard:hover {{
                background: {C["bg_hover"]};
                border-color: {C["border"]};
                border-left: 3px solid {accent};
            }}
        """)

        cl = QVBoxLayout(card)
        cl.setContentsMargins(8, 6, 8, 6)
        cl.setSpacing(2)

        # Row 1: strategy tag + direction
        top = QHBoxLayout()
        top.setSpacing(0)

        strat_lbl = QLabel(strat_name)
        strat_lbl.setStyleSheet(f"""
            font-family: {FONT_MONO};
            font-size: 8px; font-weight: 700;
            letter-spacing: 0.08em;
            color: {strat_color};
            background: transparent;
        """)
        dir_lbl = QLabel(dir_label)
        dir_lbl.setStyleSheet(f"""
            font-family: {FONT_MONO};
            font-size: 9px; font-weight: 700;
            color: {dir_color};
            background: transparent;
        """)
        top.addWidget(strat_lbl)
        top.addStretch()
        top.addWidget(dir_lbl)

        # Row 2: price + timestamp
        bottom = QHBoxLayout()
        bottom.setSpacing(0)

        price_lbl = QLabel(f"{price:,.2f}")
        price_lbl.setStyleSheet(f"""
            font-family: {FONT_MONO};
            font-size: 12px; font-weight: 700;
            color: {C["text_1"]};
            background: transparent;
        """)
        time_lbl = QLabel(ts_str)
        time_lbl.setStyleSheet(f"""
            font-family: {FONT_MONO};
            font-size: 9px; font-weight: 500;
            color: {C["text_3"]};
            background: transparent;
        """)
        bottom.addWidget(price_lbl)
        bottom.addStretch()
        bottom.addWidget(time_lbl)

        cl.addLayout(top)
        cl.addLayout(bottom)

        # Insert before stretch (newest on top)
        self._cards_layout.insertWidget(0, card)

        # Memory guard: remove old cards
        if self._cards_layout.count() - 1 > self.MAX_CARDS:
            item = self._cards_layout.takeAt(self._cards_layout.count() - 2)
            if item and item.widget():
                item.widget().deleteLater()

        self._signal_count += 1
        if is_long:
            self._long_c += 1
        else:
            self._short_c += 1
        self._update_header()

    def clear_signals(self):
        """Remove all signal cards."""
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._signal_count = 0
        self._long_c = 0
        self._short_c = 0
        self._update_header()


# ═══════════════════════════════════════════════════════════════════
#  STATUS BAR
#  Institutional concept: always-visible telemetry strip.
#  Left = strategy state. Right = connection & clock. Middle = live metrics.
# ═══════════════════════════════════════════════════════════════════
STATUS_BAR_STYLE = f"""
QWidget#statusBar {{
    background: {C["bg_deep"]};
    border-top: 1px solid {C["border_dim"]};
    min-height: 24px;
    max-height: 24px;
}}
"""


class StatusBar(QWidget):
    """
    Slim telemetry strip at the bottom of the dialog.

    Usage:
        self._status_bar = StatusBar(self)
        root.addWidget(self._status_bar)

        self._status_bar.set("sim_pnl", "+247 pts", "profit")
        self._status_bar.set("mode", "LIVE", "teal")
        self._status_bar.set_connected(True)

    Color keys: teal, profit, loss, warn, dim, white
    """

    VALUE_COLORS = {
        "teal":   C["teal"],
        "profit": C["profit"],
        "loss":   C["loss"],
        "warn":   C["warn"],
        "blue":   C["blue"],
        "dim":    C["text_3"],
        "white":  C["text_1"],
        "muted":  C["text_2"],
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusBar")
        self.setStyleSheet(STATUS_BAR_STYLE)
        self.setFixedHeight(24)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(12, 0, 12, 0)
        self._layout.setSpacing(16)

        self._items: dict[str, QLabel] = {}

        # Left cluster: operational state
        self._add_item("mode",    "MODE",    "—",    "dim")
        self._add_sep()
        self._add_item("signals", "SIGNALS", "0",    "dim")
        self._add_sep()
        self._add_item("sim_pnl", "SIM P&L", "—",   "dim")
        self._add_sep()
        self._add_item("chop",    "CHOP",    "ON",   "warn")

        self._layout.addStretch()

        # Right cluster: system telemetry
        self._add_item("latency", "LATENCY", "—",    "dim")
        self._add_sep()
        self._add_item("refresh", "REFRESH", "3s",   "dim")
        self._add_sep()

        # Pulsing connection dot + label
        self._conn_dot = QLabel("●")
        self._conn_dot.setStyleSheet(
            f"color: {C['teal']}; font-size: 8px; background: transparent;"
        )
        self._layout.addWidget(self._conn_dot)
        self._add_item("conn", "", "CONNECTED", "teal")

        # Pulse animation
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(1400)
        self._pulse_timer.timeout.connect(self._pulse_dot)
        self._pulse_on = True
        self._pulse_timer.start()

    def _pulse_dot(self):
        """Subtle alpha pulse on the connection dot — live heartbeat signal."""
        self._pulse_on = not self._pulse_on
        alpha = "1.0" if self._pulse_on else "0.35"
        color = C["teal"] if "CONNECTED" in (self._items.get("conn", QLabel()).text() or "") else C["loss"]
        self._conn_dot.setStyleSheet(
            f"color: {color}; font-size: 8px; opacity: {alpha}; background: transparent;"
        )

    def _add_item(self, key: str, label: str, value: str, color: str):
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        h = QHBoxLayout(wrapper)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        base_style = f"""
            font-family: {FONT_MONO};
            background: transparent;
        """
        if label:
            lbl = QLabel(label)
            lbl.setStyleSheet(base_style + f"""
                color: {C["text_3"]};
                font-weight: 600;
                font-size: 8px;
                letter-spacing: 0.08em;
            """)
            h.addWidget(lbl)

        val = QLabel(value)
        hex_color = self.VALUE_COLORS.get(color, color)
        val.setStyleSheet(base_style + f"""
            color: {hex_color};
            font-weight: 700;
            font-size: 10px;
        """)
        h.addWidget(val)

        self._items[key] = val
        self._layout.addWidget(wrapper)

    def _add_sep(self):
        sep = QLabel("│")
        sep.setStyleSheet(
            f"color: {C['border_hi']}; font-size: 10px; background: transparent;"
        )
        self._layout.addWidget(sep)

    def set(self, key: str, value: str, color: str = "white"):
        if key not in self._items:
            return
        lbl = self._items[key]
        hex_color = self.VALUE_COLORS.get(color, color)
        lbl.setText(value)
        # Preserve font props, only update color
        lbl.setStyleSheet(f"""
            font-family: {FONT_MONO};
            background: transparent;
            color: {hex_color};
            font-weight: 700;
            font-size: 10px;
        """)

    def set_connected(self, connected: bool):
        color = C["teal"] if connected else C["loss"]
        dot_color = color
        self._conn_dot.setStyleSheet(
            f"color: {dot_color}; font-size: 8px; background: transparent;"
        )
        self.set(
            "conn",
            "CONNECTED" if connected else "DISCONNECTED",
            "teal" if connected else "loss",
        )
        if not connected:
            self._pulse_timer.stop()
        elif not self._pulse_timer.isActive():
            self._pulse_timer.start()


# ═══════════════════════════════════════════════════════════════════
#  METRIC TILE — reusable KPI block for dashboards / summary rows
#  Institutional concept: Bloomberg-style key metric blocks.
#  Use these for PnL, win rate, drawdown, Sharpe ratio display.
# ═══════════════════════════════════════════════════════════════════
class MetricTile(QFrame):
    """
    Compact labeled metric display.

    Usage:
        tile = MetricTile("NET P&L", "+1,240", "profit", parent=self)
        tile = MetricTile("WIN RATE", "67.4%", "teal",   parent=self)
        tile = MetricTile("MAX DD",  "-320",   "loss",   parent=self)
        metrics_row.addWidget(tile)

        tile.set_value("+1,560", "profit")  # live update
    """

    COLORS = {
        "teal":   C["teal"],
        "profit": C["profit"],
        "loss":   C["loss"],
        "warn":   C["warn"],
        "blue":   C["blue"],
        "white":  C["text_1"],
        "dim":    C["text_3"],
    }

    def __init__(self, label: str, value: str, color: str = "white", parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background: {C["bg_card"]};
                border: 1px solid {C["border_dim"]};
                border-top: 2px solid {C["border_hi"]};
                border-radius: 4px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        self._label = QLabel(label)
        self._label.setStyleSheet(f"""
            color: {C["text_3"]};
            font-family: {FONT_MONO};
            font-size: 8px;
            font-weight: 700;
            letter-spacing: 0.10em;
            background: transparent;
        """)
        layout.addWidget(self._label)

        hex_color = self.COLORS.get(color, color)
        self._value = QLabel(value)
        self._value.setStyleSheet(f"""
            color: {hex_color};
            font-family: {FONT_MONO};
            font-size: 16px;
            font-weight: 700;
            background: transparent;
        """)
        layout.addWidget(self._value)

    def set_value(self, value: str, color: str = "white"):
        self._value.setText(value)
        hex_color = self.COLORS.get(color, color)
        self._value.setStyleSheet(f"""
            color: {hex_color};
            font-family: {FONT_MONO};
            font-size: 16px;
            font-weight: 700;
            background: transparent;
        """)


# ═══════════════════════════════════════════════════════════════════
#  PANEL HEADER — section dividers for dialog panels
#  Institutional style: label + optional badge, slim, uppercase
# ═══════════════════════════════════════════════════════════════════
class PanelHeader(QWidget):
    """
    Reusable panel section header.

    Usage:
        header = PanelHeader("ORDER ROUTING", badge="LIVE")
        panel_layout.addWidget(header)
    """

    def __init__(self, title: str, badge: str = "", parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setStyleSheet(f"""
            QWidget {{
                background: {C["bg_deep"]};
                border-bottom: 1px solid {C["border_dim"]};
            }}
        """)

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 12, 0)
        h.setSpacing(8)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"""
            color: {C["text_3"]};
            font-family: {FONT_MONO};
            font-size: 9px;
            font-weight: 700;
            letter-spacing: 0.10em;
            background: transparent;
        """)
        h.addWidget(title_lbl)

        if badge:
            badge_lbl = QLabel(badge)
            badge_lbl.setStyleSheet(f"""
                color: {C["teal"]};
                font-family: {FONT_MONO};
                font-size: 8px;
                font-weight: 700;
                letter-spacing: 0.08em;
                background: {C["teal_dim"]};
                border: 1px solid rgba(0,201,173,0.25);
                border-radius: 3px;
                padding: 1px 6px;
            """)
            h.addWidget(badge_lbl)

        h.addStretch()


# ═══════════════════════════════════════════════════════════════════
#  MAIN APPLY FUNCTION
# ═══════════════════════════════════════════════════════════════════
def apply_dialog_theme(dialog: QDialog):
    """
    One-shot: apply the institutional stylesheet to the AutoTraderDialog.

    Call as the FIRST line of _setup_ui():
        apply_dialog_theme(self)
    """
    dialog.setStyleSheet(DIALOG_STYLESHEET)

    # JetBrains Mono = tabular-figure monospace (numbers align in columns)
    # This is critical for trading UIs — price/volume columns must be readable at a glance
    font = QFont("JetBrains Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    if not font.exactMatch():
        font = QFont("Consolas")
        if not font.exactMatch():
            font = QFont("Menlo")
    dialog.setFont(font)