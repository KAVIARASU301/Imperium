"""
auto_trader_theme.py
────────────────────
Centralized design tokens for the Auto Trader institutional desk terminal.

Usage:
    from core.auto_trader.auto_trader_theme import THEME, Styles
"""

# ═══════════════════════════════════════════════════════════════════════════
# PALETTE — Bloomberg-style dark terminal with crisp accents
# ═══════════════════════════════════════════════════════════════════════════
THEME = {
    # Backgrounds
    "bg_base":       "#0D1117",   # deepest — window background
    "bg_surface":    "#131820",   # chart background
    "bg_panel":      "#161B26",   # toolbar / panel background
    "bg_input":      "#1A2030",   # input field background
    "bg_card":       "#1E2535",   # card / group background
    "bg_hover":      "#242D40",   # hover state
    "bg_active":     "#2A3448",   # pressed / active state

    # Borders
    "border":        "#2A3550",   # default border
    "border_focus":  "#3D5A8A",   # focused border (blue)
    "border_active": "#4A7CC7",   # activated accent border

    # Text
    "text_primary":  "#E8EDF5",   # primary labels
    "text_secondary":"#8A96B0",   # muted / secondary
    "text_muted":    "#505870",   # very muted, section headers
    "text_accent":   "#9CCAF4",   # blue accent labels

    # Brand accents (Bloomberg-inspired)
    "accent_blue":   "#4A9EFF",   # primary interactive
    "accent_teal":   "#26C6DA",   # CVD line / teal
    "accent_gold":   "#FFD54F",   # price line / gold
    "accent_green":  "#00E676",   # long signals / profit
    "accent_red":    "#FF5252",   # short signals / loss
    "accent_orange": "#FF9800",   # warnings

    # Signal colors
    "sig_long":      "#00E676",
    "sig_short":     "#FF5252",
    "sig_neutral":   "#90A4AE",

    # Status indicators
    "status_live":   "#00E676",
    "status_warn":   "#FFB74D",
    "status_error":  "#EF5350",
    "status_idle":   "#546E7A",

    # EMA line colors
    "ema_10":        "#00D9FF",
    "ema_21":        "#FFD700",
    "ema_51":        "#FF6B6B",
    "vwap":          "#00E676",

    # Separator
    "separator":     "#1E2840",
}

# ═══════════════════════════════════════════════════════════════════════════
# DIMENSIONS
# ═══════════════════════════════════════════════════════════════════════════
DIMS = {
    "toolbar_h":     26,    # standard control row height
    "btn_h":         24,    # button height
    "input_h":       22,    # spinbox height
    "label_font":    10,    # section label font size
    "control_font":  11,    # input / button font size
    "group_font":    10,    # group box title
    "input_w_s":     64,    # small spinbox width
    "input_w_m":     84,    # medium spinbox width
    "input_w_l":     104,   # large spinbox width
    "combo_w_s":     100,   # small combo
    "combo_w_m":     140,   # medium combo
    "combo_w_l":     180,   # large combo
    "pill_h":        18,    # status pill height
    "pill_r":        9,     # status pill border radius
}

# ═══════════════════════════════════════════════════════════════════════════
# REUSABLE QSS STYLESHEET FRAGMENTS
# ═══════════════════════════════════════════════════════════════════════════

class Styles:
    """Pre-built QSS string constants. Compose these in _setup_ui."""

    # ── Base spinbox ──────────────────────────────────────────────────────
    SPINBOX = f"""
        QSpinBox, QDoubleSpinBox {{
            background: {THEME['bg_input']};
            color: {THEME['text_primary']};
            font-weight: 600;
            font-size: {DIMS['control_font']}px;
            border: 1px solid {THEME['border']};
            border-radius: 2px;
            padding: 1px 4px;
            min-height: {DIMS['input_h']}px;
            selection-background-color: {THEME['accent_blue']};
        }}
        QSpinBox:hover, QDoubleSpinBox:hover {{
            border: 1px solid {THEME['border_focus']};
        }}
        QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 1px solid {THEME['border_active']};
            background: {THEME['bg_card']};
        }}
        QSpinBox::up-button, QDoubleSpinBox::up-button {{
            width: 14px;
            border: none;
            background: {THEME['bg_card']};
            border-left: 1px solid {THEME['border']};
        }}
        QSpinBox::down-button, QDoubleSpinBox::down-button {{
            width: 14px;
            border: none;
            background: {THEME['bg_card']};
            border-left: 1px solid {THEME['border']};
        }}
        QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
        QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
            background: {THEME['bg_hover']};
        }}
    """

    # ── Base combo ────────────────────────────────────────────────────────
    COMBO = f"""
        QComboBox {{
            background: {THEME['bg_input']};
            color: {THEME['text_primary']};
            font-weight: 600;
            font-size: {DIMS['control_font']}px;
            padding: 1px 6px;
            border: 1px solid {THEME['border']};
            border-radius: 2px;
            min-height: {DIMS['input_h']}px;
        }}
        QComboBox:hover {{
            border: 1px solid {THEME['border_focus']};
        }}
        QComboBox:focus {{
            border: 1px solid {THEME['border_active']};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 18px;
            background: {THEME['bg_card']};
            border-left: 1px solid {THEME['border']};
        }}
        QComboBox::down-arrow {{
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid {THEME['text_secondary']};
            margin-right: 4px;
        }}
        QComboBox QAbstractItemView {{
            background: {THEME['bg_panel']};
            color: {THEME['text_primary']};
            selection-background-color: {THEME['accent_blue']};
            selection-color: #000;
            border: 1px solid {THEME['border_focus']};
            border-radius: 0px;
            outline: none;
            padding: 2px;
        }}
        QComboBox QAbstractItemView::item {{
            min-height: 22px;
            padding: 2px 8px;
        }}
        QComboBox QAbstractItemView::item:hover {{
            background: {THEME['bg_hover']};
        }}
    """

    # ── Checkbox ──────────────────────────────────────────────────────────
    CHECKBOX = f"""
        QCheckBox {{
            color: {THEME['text_accent']};
            font-weight: 600;
            font-size: {DIMS['control_font']}px;
            spacing: 5px;
        }}
        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid {THEME['border_focus']};
            border-radius: 2px;
            background: {THEME['bg_input']};
        }}
        QCheckBox::indicator:checked {{
            background: {THEME['accent_blue']};
            border-color: {THEME['accent_blue']};
        }}
        QCheckBox::indicator:hover {{
            border-color: {THEME['border_active']};
        }}
    """

    # ── Muted label ───────────────────────────────────────────────────────
    LABEL_MUTED = f"color: {THEME['text_secondary']}; font-size: {DIMS['label_font']}px; font-weight: 600;"
    LABEL_DIM   = f"color: {THEME['text_muted']}; font-size: {DIMS['label_font']}px; font-weight: 500;"
    LABEL_VALUE = f"color: {THEME['text_primary']}; font-size: {DIMS['control_font']}px; font-weight: 600;"

    # ── Standard button ───────────────────────────────────────────────────
    BTN = f"""
        QPushButton {{
            background: {THEME['bg_card']};
            color: {THEME['text_accent']};
            font-weight: 600;
            font-size: {DIMS['control_font']}px;
            border: 1px solid {THEME['border']};
            border-radius: 2px;
            padding: 2px 10px;
            min-height: {DIMS['btn_h']}px;
        }}
        QPushButton:hover {{
            border: 1px solid {THEME['border_active']};
            background: {THEME['bg_hover']};
        }}
        QPushButton:pressed {{
            background: {THEME['bg_active']};
        }}
        QPushButton:disabled {{
            color: {THEME['text_muted']};
            border-color: {THEME['separator']};
        }}
    """

    BTN_ICON = f"""
        QPushButton {{
            background: {THEME['bg_card']};
            color: {THEME['text_primary']};
            border: 1px solid {THEME['border']};
            border-radius: 2px;
            font-size: 14px;
            padding: 0px;
        }}
        QPushButton:hover {{
            background: {THEME['bg_hover']};
            border: 1px solid {THEME['border_active']};
        }}
        QPushButton:pressed {{
            background: {THEME['bg_active']};
        }}
    """

    BTN_TOGGLE = f"""
        QPushButton {{
            background: {THEME['bg_card']};
            color: {THEME['text_secondary']};
            font-weight: 700;
            font-size: {DIMS['control_font']}px;
            border: 1px solid {THEME['border']};
            border-radius: 2px;
            padding: 2px 10px;
            min-height: {DIMS['btn_h']}px;
        }}
        QPushButton:checked {{
            background: {THEME['accent_teal']};
            color: #000;
            border-color: {THEME['accent_teal']};
        }}
        QPushButton:hover {{
            border: 1px solid {THEME['border_active']};
        }}
    """

    # ── Automate button (prominent) ───────────────────────────────────────
    BTN_AUTOMATE_OFF = f"""
        QPushButton {{
            background: {THEME['bg_card']};
            color: {THEME['text_secondary']};
            font-weight: 700;
            font-size: 11px;
            border: 1px solid {THEME['border']};
            border-radius: 2px;
            padding: 2px 12px;
            min-height: {DIMS['btn_h']}px;
            letter-spacing: 0.5px;
        }}
        QPushButton:hover {{
            border: 1px solid {THEME['border_active']};
            color: {THEME['text_primary']};
        }}
    """

    BTN_AUTOMATE_ON = f"""
        QPushButton {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #004D27, stop:1 #003D20);
            color: {THEME['accent_green']};
            font-weight: 700;
            font-size: 11px;
            border: 1px solid {THEME['accent_green']};
            border-radius: 2px;
            padding: 2px 12px;
            min-height: {DIMS['btn_h']}px;
            letter-spacing: 0.5px;
        }}
    """

    # ── Group box ─────────────────────────────────────────────────────────
    GROUPBOX = f"""
        QGroupBox {{
            border: 1px solid {THEME['border']};
            border-radius: 3px;
            margin-top: 14px;
            padding-top: 4px;
            font-weight: 700;
            color: {THEME['text_accent']};
            font-size: {DIMS['group_font']}px;
            background: {THEME['bg_card']};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 6px;
            background: {THEME['bg_panel']};
        }}
    """

    # ── Inline control panel (the new dense toolbar section) ──────────────
    CONTROL_PANEL = f"""
        QWidget#controlPanel {{
            background: {THEME['bg_panel']};
            border-bottom: 1px solid {THEME['border']};
        }}
    """

    # ── Section divider label ─────────────────────────────────────────────
    DIVIDER_LABEL = (
        f"color: {THEME['text_muted']}; font-size: 9px; font-weight: 700; "
        f"letter-spacing: 1px; text-transform: uppercase;"
    )

    # ── Quick-value label (shows live value beside a spinner) ─────────────
    LIVE_VALUE = f"color: {THEME['accent_gold']}; font-size: 10px; font-weight: 700;"

    # ── Deploy mode pill ──────────────────────────────────────────────────
    @staticmethod
    def deploy_pill(mode: str) -> str:
        colors = {
            "live":   (THEME['accent_green'], "#001A10"),
            "canary": (THEME['accent_orange'], "#1A0D00"),
            "shadow": (THEME['text_secondary'], THEME['bg_card']),
        }
        fg, bg = colors.get(mode, (THEME['text_secondary'], THEME['bg_card']))
        return (
            f"background:{bg}; color:{fg}; border:1px solid {fg}; "
            f"border-radius:8px; padding:1px 8px; font-size:10px; font-weight:700;"
        )

    # ── Confidence meter bar ──────────────────────────────────────────────
    @staticmethod
    def confidence_bar_color(value: float) -> str:
        if value >= 0.65:
            return THEME['accent_green']
        if value >= 0.45:
            return THEME['accent_orange']
        return THEME['accent_red']