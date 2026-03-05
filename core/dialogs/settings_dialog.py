import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QWidget, QGroupBox, QGridLayout, QLabel, QLineEdit,
    QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox, QPushButton,
    QMessageBox, QListWidget, QListWidgetItem, QAbstractItemView,
)
from PySide6.QtCore import Qt, Signal

from core.utils.config_manager import ConfigManager
from core.account.token_manager import TokenManager
from core.ui_kit.close_button import CloseButton
logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """Premium settings dialog with modern UX improvements."""

    settings_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(580)
        self.setMinimumHeight(560)
        self.setMaximumHeight(640)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._drag_pos = None
        self._has_changes = False  # Track unsaved changes

        self.token_manager = TokenManager()
        self.config_manager = ConfigManager()

        self._setup_ui()
        self._load_settings()
        self._apply_styles()
        self._track_changes()

    def _setup_ui(self):
        container = QWidget(self)
        container.setObjectName("mainContainer")

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header with close button
        header = self._create_header()
        layout.addLayout(header)

        # Content area
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 15, 20, 20)
        content_layout.setSpacing(15)

        tabs = QTabWidget()
        tabs.setObjectName("mainTabs")
        tabs.addTab(self._create_trading_tab(), "TRADING")
        tabs.addTab(self._create_risk_tab(), "RISK")
        tabs.addTab(self._create_display_tab(), "DISPLAY")
        tabs.addTab(self._create_api_tab(), "API")
        tabs.addTab(self._create_instruments_tab(), "INSTRUMENTS")
        content_layout.addWidget(tabs)

        content_layout.addLayout(self._create_action_buttons())

        layout.addWidget(content)

        dialog_layout = QVBoxLayout(self)
        dialog_layout.setContentsMargins(0, 0, 0, 0)
        dialog_layout.addWidget(container)

    def _create_header(self) -> QHBoxLayout:
        """Premium header with drag handle and close button."""
        header = QWidget()
        header.setObjectName("dialogHeader")
        header.setFixedHeight(48)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 0, 12, 0)
        layout.setSpacing(0)

        # Title
        title = QLabel("⚙️ Settings")
        title.setObjectName("dialogTitle")

        # Close button
        # Replace your old close button code with:
        close_btn = CloseButton(style="minimal", size=20)
        close_btn.setObjectName("closeButton")
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self._on_close_requested)

        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(close_btn)

        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(header)

        # Separator line
        separator = QWidget()
        separator.setObjectName("headerSeparator")
        separator.setFixedHeight(1)
        container_layout.addWidget(separator)

        return container_layout

    def _create_action_buttons(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 15, 0, 0)
        layout.setSpacing(10)

        reset_btn = QPushButton("⟲ Reset to Defaults")
        reset_btn.setObjectName("secondaryButton")
        reset_btn.setToolTip("Restore factory settings")
        reset_btn.clicked.connect(self._reset_to_defaults)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self._on_close_requested)

        save_btn = QPushButton("💾 Save Settings")
        save_btn.setObjectName("primaryButton")
        save_btn.setFixedWidth(140)
        save_btn.clicked.connect(self._save_settings)

        layout.addWidget(reset_btn)
        layout.addStretch()
        layout.addWidget(cancel_btn)
        layout.addWidget(save_btn)
        return layout

    def _create_trading_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 20, 15, 15)
        layout.setSpacing(10)

        # ── Existing: Default Trading Values ──────────────────────────────────
        group = QGroupBox("Default Trading Values")
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 10, 12, 10)
        group_layout.setSpacing(8)

        top_row = QWidget()
        top_row_layout = QGridLayout(top_row)
        top_row_layout.setContentsMargins(0, 0, 0, 0)
        top_row_layout.setHorizontalSpacing(10)
        top_row_layout.setVerticalSpacing(8)

        top_row_layout.addWidget(QLabel("Default Symbol:"), 0, 0)
        self.default_symbol = QComboBox()
        self.default_symbol.addItems(["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"])
        top_row_layout.addWidget(self.default_symbol, 0, 1)

        top_row_layout.addWidget(QLabel("Default Product:"), 0, 2)
        self.default_product = QComboBox()
        self.default_product.addItems(["MIS", "NRML"])
        top_row_layout.addWidget(self.default_product, 0, 3)

        top_row_layout.addWidget(QLabel("Default Lots:"), 0, 4)
        self.default_lots = QSpinBox()
        self.default_lots.setRange(1, 100)
        self.default_lots.setSuffix(" lots")
        self.default_lots.setMinimumWidth(110)
        top_row_layout.addWidget(self.default_lots, 0, 5)

        top_row_layout.setColumnStretch(1, 2)
        top_row_layout.setColumnStretch(3, 2)
        top_row_layout.setColumnStretch(5, 1)
        group_layout.addWidget(top_row)

        layout.addWidget(group)

        # ── NEW: Quick Order Risk Defaults ────────────────────────────────────
        risk_group = QGroupBox("Quick Order Risk Defaults")
        risk_grid = QGridLayout(risk_group)
        risk_grid.setContentsMargins(12, 10, 12, 10)
        risk_grid.setHorizontalSpacing(10)
        risk_grid.setVerticalSpacing(8)

        risk_grid.addWidget(QLabel("SL per Lot (₹):"), 0, 0)
        self.order_sl_per_lot = QSpinBox()
        self.order_sl_per_lot.setRange(100, 100000)
        self.order_sl_per_lot.setSingleStep(100)
        self.order_sl_per_lot.setValue(1000)
        self.order_sl_per_lot.setToolTip(
            "Default stop-loss in ₹ per lot. "
            "Dialog will set SL = this × number of lots."
        )
        self.order_sl_per_lot.setMinimumWidth(120)
        risk_grid.addWidget(self.order_sl_per_lot, 0, 1)

        risk_grid.addWidget(QLabel("Risk : Reward Ratio:"), 0, 2)
        self.order_rr_ratio = QDoubleSpinBox()
        self.order_rr_ratio.setRange(0.5, 10.0)
        self.order_rr_ratio.setSingleStep(0.25)
        self.order_rr_ratio.setDecimals(2)
        self.order_rr_ratio.setValue(1.5)
        self.order_rr_ratio.setToolTip(
            "TP = SL × this ratio.  1.5 means TP is 1.5× the SL amount."
        )
        self.order_rr_ratio.setMinimumWidth(100)
        risk_grid.addWidget(self.order_rr_ratio, 0, 3)

        risk_grid.setColumnStretch(1, 1)
        risk_grid.setColumnStretch(3, 1)

        self.order_trailing_enabled = QCheckBox("Enable Trailing Stop-Loss in Quick Order")
        self.order_trailing_enabled.setChecked(True)
        self.order_trailing_enabled.setToolTip(
            "When unchecked the Trailing SL row is hidden in the Quick Order dialog."
        )
        risk_grid.addWidget(self.order_trailing_enabled, 1, 0, 1, 4)

        layout.addWidget(risk_group)

        info = QLabel(
            "💡 These values pre-fill the Quick Order dialog. "
            "SL and TP will auto-update when you change the lot count."
        )
        info.setObjectName("infoLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addStretch()
        return tab

    def _create_display_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 20, 15, 15)

        group = QGroupBox("Display Settings")
        grid = QGridLayout(group)
        grid.setVerticalSpacing(14)

        self.auto_refresh = QCheckBox("Auto-refresh UI components")
        grid.addWidget(self.auto_refresh, 0, 0, 1, 2)

        grid.addWidget(QLabel("Refresh Interval:"), 1, 0)
        self.refresh_interval = QSpinBox()
        self.refresh_interval.setRange(1, 60)
        self.refresh_interval.setSuffix(" sec")
        grid.addWidget(self.refresh_interval, 1, 1)

        self.auto_adjust_ladder = QCheckBox("Auto-adjust strike ladder on price movement")
        grid.addWidget(self.auto_adjust_ladder, 2, 0, 1, 2)


        layout.addWidget(group)
        layout.addStretch()
        return tab

    def _create_risk_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 20, 15, 15)

        group = QGroupBox("Risk Hard Limits")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(15)
        grid.setVerticalSpacing(12)

        grid.addWidget(QLabel("Max Portfolio Loss:"), 0, 0)
        self.risk_max_portfolio_loss = QSpinBox()
        self.risk_max_portfolio_loss.setRange(0, 10_000_000)
        self.risk_max_portfolio_loss.setSingleStep(1000)
        self.risk_max_portfolio_loss.setPrefix("₹")
        self.risk_max_portfolio_loss.setToolTip("Trigger global kill switch when intraday P&L drops below -limit. 0 = disabled.")
        grid.addWidget(self.risk_max_portfolio_loss, 0, 1)

        grid.addWidget(QLabel("Intraday Drawdown Limit:"), 1, 0)
        self.risk_intraday_drawdown_limit = QSpinBox()
        self.risk_intraday_drawdown_limit.setRange(0, 10_000_000)
        self.risk_intraday_drawdown_limit.setSingleStep(1000)
        self.risk_intraday_drawdown_limit.setPrefix("₹")
        self.risk_intraday_drawdown_limit.setToolTip(
            "Lock trading if drawdown from intraday peak P&L crosses this value. 0 = disabled."
        )
        grid.addWidget(self.risk_intraday_drawdown_limit, 1, 1)

        grid.addWidget(QLabel("Max Open Positions:"), 2, 0)
        self.risk_max_open_positions = QSpinBox()
        self.risk_max_open_positions.setRange(0, 500)
        self.risk_max_open_positions.setToolTip("Block new symbols if active positions reach this count. 0 = disabled.")
        grid.addWidget(self.risk_max_open_positions, 2, 1)

        grid.addWidget(QLabel("Max Gross Open Quantity:"), 3, 0)
        self.risk_max_gross_open_quantity = QSpinBox()
        self.risk_max_gross_open_quantity.setRange(0, 5_000_000)
        self.risk_max_gross_open_quantity.setSingleStep(100)
        self.risk_max_gross_open_quantity.setToolTip(
            "Block new entries when sum(abs(open quantities)) exceeds this cap. 0 = disabled."
        )
        grid.addWidget(self.risk_max_gross_open_quantity, 3, 1)

        layout.addWidget(group)

        info = QLabel(
            "💡 Limits apply to entry orders only. Exits remain allowed.\n"
            "Global kill-switch and drawdown lock reset at next trading-day reset."
        )
        info.setObjectName("infoLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addStretch()
        return tab

    def _create_api_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 20, 15, 15)

        group = QGroupBox("API Configuration")
        grid = QGridLayout(group)
        grid.setVerticalSpacing(14)

        grid.addWidget(QLabel("API Key:"), 0, 0)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("Enter Kite API Key")
        grid.addWidget(self.api_key, 0, 1)

        grid.addWidget(QLabel("API Secret:"), 1, 0)
        self.api_secret = QLineEdit()
        self.api_secret.setEchoMode(QLineEdit.Password)
        self.api_secret.setPlaceholderText("Enter Kite API Secret")
        grid.addWidget(self.api_secret, 1, 1)

        self.save_credentials = QCheckBox("Save credentials securely on this machine")
        grid.addWidget(self.save_credentials, 2, 0, 1, 2)

        layout.addWidget(group)

        # Warning
        warning = QLabel("⚠️ Credentials are encrypted and stored locally")
        warning.setObjectName("warningLabel")
        layout.addWidget(warning)

        layout.addStretch()
        return tab

    def _create_instruments_tab(self) -> QWidget:
        """Instrument loading configuration tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 20, 15, 15)
        layout.setSpacing(12)

        exch_group = QGroupBox("Exchange")
        exch_layout = QGridLayout(exch_group)
        exch_layout.setContentsMargins(12, 14, 12, 12)
        exch_layout.setSpacing(8)

        exch_layout.addWidget(QLabel("Load from:"), 0, 0)
        self.inst_exchange_mode = QComboBox()
        self.inst_exchange_mode.addItems([
            "NFO Only",
            "NFO + BFO (BSE F&O)",
        ])
        self.inst_exchange_mode.setToolTip(
            "NFO = NSE derivatives (NIFTY/stocks)\n"
            "BFO = BSE derivatives (SENSEX/BANKEX) — adds load"
        )
        exch_layout.addWidget(self.inst_exchange_mode, 0, 1)
        layout.addWidget(exch_group)

        sym_group = QGroupBox("Symbol Universe")
        sym_layout = QVBoxLayout(sym_group)
        sym_layout.setContentsMargins(12, 14, 12, 12)
        sym_layout.setSpacing(8)

        mode_row = QWidget()
        mode_row_layout = QGridLayout(mode_row)
        mode_row_layout.setContentsMargins(0, 0, 0, 0)
        mode_row_layout.setSpacing(8)

        mode_row_layout.addWidget(QLabel("Symbol mode:"), 0, 0)
        self.inst_symbol_mode = QComboBox()
        self.inst_symbol_mode.addItems([
            "Indices Only (lightest)",
            "Preferred List (custom)",
            "All NFO (heaviest)",
        ])
        self.inst_symbol_mode.setToolTip(
            "Indices Only  → 4 index symbols only\n"
            "Preferred List → your custom symbol list\n"
            "All NFO        → every tradeable symbol (150 + symbols, slow)"
        )
        self.inst_symbol_mode.currentIndexChanged.connect(self._on_inst_symbol_mode_changed)
        mode_row_layout.addWidget(self.inst_symbol_mode, 0, 1)
        sym_layout.addWidget(mode_row)

        self.inst_symbols_editor = QWidget()
        editor_layout = QVBoxLayout(self.inst_symbols_editor)
        editor_layout.setContentsMargins(0, 4, 0, 0)
        editor_layout.setSpacing(4)

        editor_layout.addWidget(QLabel("Preferred symbols (one per row):"))

        self.inst_symbols_list = QListWidget()
        self.inst_symbols_list.setFixedHeight(90)
        self.inst_symbols_list.setSelectionMode(QAbstractItemView.SingleSelection)
        editor_layout.addWidget(self.inst_symbols_list)

        btn_row = QHBoxLayout()
        self.inst_sym_input = QLineEdit()
        self.inst_sym_input.setPlaceholderText("e.g. SBIN")
        self.inst_sym_input.setMaximumWidth(100)
        add_btn = QPushButton("＋ Add")
        add_btn.setObjectName("secondaryButton")
        add_btn.setFixedWidth(70)
        add_btn.clicked.connect(self._add_preferred_symbol)
        rem_btn = QPushButton("－ Remove")
        rem_btn.setObjectName("secondaryButton")
        rem_btn.setFixedWidth(80)
        rem_btn.clicked.connect(self._remove_preferred_symbol)

        btn_row.addWidget(self.inst_sym_input)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rem_btn)
        btn_row.addStretch()
        editor_layout.addLayout(btn_row)

        sym_layout.addWidget(self.inst_symbols_editor)
        layout.addWidget(sym_group)

        exp_group = QGroupBox("Expiry Depth")
        exp_layout = QGridLayout(exp_group)
        exp_layout.setContentsMargins(12, 14, 12, 12)
        exp_layout.setSpacing(8)

        exp_layout.addWidget(QLabel("Load expiries:"), 0, 0)
        self.inst_expiry_depth = QComboBox()
        self.inst_expiry_depth.addItems([
            "Current expiry only  (fastest)",
            "Current + 1 expiry",
            "Current + 2 expiries",
            "All expiries  (slowest)",
        ])
        self.inst_expiry_depth.setToolTip(
            "For hedging strategies you typically only need Current + 1.\n"
            "'All expiries' can add thousands of extra strikes."
        )
        exp_layout.addWidget(self.inst_expiry_depth, 0, 1)

        self.inst_load_hint = QLabel("")
        self.inst_load_hint.setObjectName("infoLabel")
        self.inst_load_hint.setWordWrap(True)
        exp_layout.addWidget(self.inst_load_hint, 1, 0, 1, 2)

        self.inst_expiry_depth.currentIndexChanged.connect(self._update_load_hint)
        self.inst_symbol_mode.currentIndexChanged.connect(self._update_load_hint)
        self.inst_exchange_mode.currentIndexChanged.connect(self._update_load_hint)

        layout.addWidget(exp_group)
        layout.addStretch()

        return tab

    def _on_inst_symbol_mode_changed(self, index: int) -> None:
        """Show/hide the custom symbol editor based on mode."""
        is_custom = index == 1
        self.inst_symbols_editor.setVisible(is_custom)
        self._update_load_hint()

    def _add_preferred_symbol(self) -> None:
        sym = self.inst_sym_input.text().strip().upper()
        if not sym:
            return

        existing = [
            self.inst_symbols_list.item(i).text()
            for i in range(self.inst_symbols_list.count())
        ]
        if sym not in existing:
            self.inst_symbols_list.addItem(QListWidgetItem(sym))
        self.inst_sym_input.clear()
        self._has_changes = True
        self._update_load_hint()

    def _remove_preferred_symbol(self) -> None:
        row = self.inst_symbols_list.currentRow()
        if row >= 0:
            self.inst_symbols_list.takeItem(row)
            self._has_changes = True
            self._update_load_hint()

    def _update_load_hint(self) -> None:
        """Show a rough load-size description so the user understands the tradeoff."""
        exch_idx = self.inst_exchange_mode.currentIndex()
        sym_idx = self.inst_symbol_mode.currentIndex()
        exp_idx = self.inst_expiry_depth.currentIndex()

        base_counts = {0: "~8,000", 1: "~14,000", 2: "~22,000+"}
        exp_mult = ["1×", "2×", "3×", "up to 8×"]
        exch_note = "" if exch_idx == 0 else " + BSE F&O contracts"

        if sym_idx == 2:
            hint = (
                f"⚠ All NFO loads 150 + symbols{exch_note}. "
                f"With expiry depth {exp_mult[exp_idx]} this can be 100,000 + contracts. "
                "Only use if you need full market coverage."
            )
        elif sym_idx == 0:
            approx = base_counts.get(exp_idx, "varies")
            hint = (
                f"✓ Indices only{exch_note}: ~{approx} contracts "
                f"(expiry depth {exp_mult[exp_idx]}). Recommended for most strategies."
            )
        else:
            count = self.inst_symbols_list.count() if hasattr(self, "inst_symbols_list") else "?"
            hint = (
                f"Custom list: {count} symbol(s){exch_note}. "
                f"Expiry depth {exp_mult[min(exp_idx, 3)]}."
            )

        if hasattr(self, "inst_load_hint"):
            self.inst_load_hint.setText(hint)

    def _load_inst_settings(self, settings: dict) -> None:
        """Load instrument settings into the INSTRUMENTS tab controls."""
        exch_map = {"NFO_ONLY": 0, "NFO_AND_BFO": 1}
        self.inst_exchange_mode.setCurrentIndex(
            exch_map.get(settings.get("inst_exchange_mode", "NFO_ONLY"), 0)
        )

        sym_map = {"INDICES_ONLY": 0, "CUSTOM": 1, "ALL_NFO": 2}
        sym_idx = sym_map.get(settings.get("inst_symbol_mode", "INDICES_ONLY"), 0)
        self.inst_symbol_mode.setCurrentIndex(sym_idx)

        self.inst_symbols_list.clear()
        for sym in settings.get(
            "inst_preferred_symbols",
            ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
        ):
            self.inst_symbols_list.addItem(QListWidgetItem(sym))

        depth_map = {0: 0, 1: 1, 2: 2, -1: 3}
        depth_val = settings.get("inst_expiry_depth", 1)
        self.inst_expiry_depth.setCurrentIndex(depth_map.get(depth_val, 1))

        self._on_inst_symbol_mode_changed(sym_idx)
        self._update_load_hint()

    def _get_inst_settings_dict(self) -> dict:
        """Extract instrument settings from UI controls."""
        exch_vals = ["NFO_ONLY", "NFO_AND_BFO"]
        sym_vals = ["INDICES_ONLY", "CUSTOM", "ALL_NFO"]
        depth_vals = [0, 1, 2, -1]

        preferred = [
            self.inst_symbols_list.item(i).text()
            for i in range(self.inst_symbols_list.count())
        ]

        return {
            "inst_exchange_mode": exch_vals[self.inst_exchange_mode.currentIndex()],
            "inst_symbol_mode": sym_vals[self.inst_symbol_mode.currentIndex()],
            "inst_preferred_symbols": preferred,
            "inst_expiry_depth": depth_vals[self.inst_expiry_depth.currentIndex()],
        }

    def _track_changes(self):
        """Track changes to enable unsaved changes warning."""
        self.default_symbol.currentTextChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.default_product.currentTextChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.default_lots.valueChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.order_sl_per_lot.valueChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.order_rr_ratio.valueChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.order_trailing_enabled.stateChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.auto_refresh.stateChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.refresh_interval.valueChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.auto_adjust_ladder.stateChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.risk_intraday_drawdown_limit.valueChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.risk_max_portfolio_loss.valueChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.risk_max_open_positions.valueChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.risk_max_gross_open_quantity.valueChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.inst_exchange_mode.currentTextChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.inst_symbol_mode.currentTextChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.inst_expiry_depth.currentTextChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.inst_sym_input.textChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.inst_symbols_list.itemSelectionChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.api_key.textChanged.connect(lambda: setattr(self, '_has_changes', True))
        self.api_secret.textChanged.connect(lambda: setattr(self, '_has_changes', True))

    def _on_close_requested(self):
        """Handle close with unsaved changes warning."""
        if self._has_changes:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Close without saving?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.No:
                return

        self.reject()

    def _apply_styles(self):
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
            }

            #dialogHeader {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1F2533,
                    stop:1 #1A1F2E
                );
                border-radius: 12px 12px 0 0;
            }

            #headerSeparator {
                background: #3A4458;
            }

            #dialogTitle {
                color: #FFFFFF;
                font-size: 16px;
                font-weight: 700;
            }

            #closeButton {
                background: transparent;
                border: none;
                color: #A9B1C3;
                font-family: "Segoe UI Symbol";
                font-size: 16px;
                border-radius: 6px;
                padding: 0;
            }
            
            #closeButton:hover {
                color: #FFFFFF;
            }
            
            #closeButton:pressed {
                color: #F85149;
            }


            QTabWidget::pane { 
                border: 1px solid #2A3140;
                border-radius: 8px;
                background: #1A1F2E;
            }
            QTabBar::tab {
                background: transparent;
                color: #8A9BA8;
                font-weight: 700;
                font-size: 11px;
                letter-spacing: 0.5px;
                padding: 12px 24px;
                border-bottom: 2px solid transparent;
            }
            QTabBar::tab:selected {
                color: #29C7C9;
                border-bottom: 2px solid #29C7C9;
            }
            QTabBar::tab:hover {
                color: #FFFFFF;
                background: rgba(41, 199, 201, 0.05);
            }

            QGroupBox {
                color: #E0E0E0;
                border: 1px solid #2A3140;
                border-radius: 8px;
                font-size: 12px;
                font-weight: 700;
                margin-top: 12px;
                padding-top: 18px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                background: #1A1F2E;
            }

            QLabel {
                color: #A9B1C3;
                font-size: 13px;
            }

            #infoLabel {
                color: #7892A8;
                font-size: 11.5px;
                padding: 8px 12px;
                background: rgba(41, 199, 201, 0.05);
                border-left: 3px solid #29C7C9;
                border-radius: 4px;
            }

            #warningLabel {
                color: #FBBF24;
                font-size: 11px;
                padding: 8px;
            }

            QLineEdit, QSpinBox, QComboBox {
                background: #212635;
                border: 1px solid #3A4458;
                color: #E0E0E0;
                padding: 9px 12px;
                border-radius: 6px;
                font-size: 13px;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 1px solid #29C7C9;
                background: #1F2533;
            }
            QLineEdit:hover, QSpinBox:hover, QComboBox:hover {
                border-color: #4A5568;
            }

            QCheckBox {
                color: #E0E0E0;
                spacing: 10px;
                font-size: 13px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 2px solid #3A4458;
                border-radius: 4px;
                background: #212635;
            }
            QCheckBox::indicator:hover {
                border-color: #29C7C9;
            }
            QCheckBox::indicator:checked {
                background: #29C7C9;
                border-color: #29C7C9;
                image: url(none);
            }

            QPushButton {
                font-weight: 600;
                border-radius: 6px;
                padding: 11px 20px;
                border: none;
                font-size: 13px;
            }
            #secondaryButton {
                background: #2A3140;
                color: #E0E0E0;
                border: 1px solid #3A4458;
            }
            #secondaryButton:hover {
                background: #3A4458;
                border-color: #4A5568;
            }
            #secondaryButton:pressed {
                background: #1F2533;
            }

            #primaryButton {
                background: #29C7C9;
                color: #161A25;
            }
            #primaryButton:hover {
                background: #32E0E3;
            }
            #primaryButton:pressed {
                background: #1f8a8c;
            }
        """)

    def _load_settings(self):
        settings = self.config_manager.load_settings()

        self.default_symbol.setCurrentText(settings.get("default_symbol", "NIFTY"))
        self.default_product.setCurrentText(settings.get("default_product", "MIS"))
        self.default_lots.setValue(settings.get("default_lots", 1))
        self.order_sl_per_lot.setValue(int(settings.get("order_sl_per_lot", 1000)))
        self.order_rr_ratio.setValue(float(settings.get("order_rr_ratio", 1.5)))
        self.order_trailing_enabled.setChecked(bool(settings.get("order_trailing_enabled", True)))
        self.auto_refresh.setChecked(settings.get("auto_refresh", True))
        self.refresh_interval.setValue(settings.get("refresh_interval", 2))
        self.auto_adjust_ladder.setChecked(settings.get("auto_adjust_ladder", True))
        self.risk_intraday_drawdown_limit.setValue(int(settings.get("risk_intraday_drawdown_limit", 0) or 0))
        self.risk_max_portfolio_loss.setValue(int(settings.get("risk_max_portfolio_loss", 0) or 0))
        self.risk_max_open_positions.setValue(int(settings.get("risk_max_open_positions", 0) or 0))
        self.risk_max_gross_open_quantity.setValue(int(settings.get("risk_max_gross_open_quantity", 0) or 0))
        self._load_inst_settings(settings)

        creds = self.token_manager.load_credentials()
        if creds:
            self.api_key.setText(creds.get("api_key", ""))
            self.api_secret.setText(creds.get("api_secret", ""))
            self.save_credentials.setChecked(True)

        # Reset change tracking after load
        self._has_changes = False

    def _save_settings(self):
        settings = self.config_manager.load_settings()
        settings.update({
            "default_symbol": self.default_symbol.currentText(),
            "default_product": self.default_product.currentText(),
            "default_lots": self.default_lots.value(),
            "order_sl_per_lot": self.order_sl_per_lot.value(),
            "order_rr_ratio": self.order_rr_ratio.value(),
            "order_trailing_enabled": self.order_trailing_enabled.isChecked(),
            "auto_refresh": self.auto_refresh.isChecked(),
            "refresh_interval": self.refresh_interval.value(),
            "auto_adjust_ladder": self.auto_adjust_ladder.isChecked(),
            "risk_intraday_drawdown_limit": self.risk_intraday_drawdown_limit.value(),
            "risk_max_portfolio_loss": self.risk_max_portfolio_loss.value(),
            "risk_max_open_positions": self.risk_max_open_positions.value(),
            "risk_max_gross_open_quantity": self.risk_max_gross_open_quantity.value(),
            **self._get_inst_settings_dict(),
        })

        self.config_manager.save_settings(settings)

        if self.save_credentials.isChecked() and self.api_key.text() and self.api_secret.text():
            self.token_manager.save_credentials(self.api_key.text(), self.api_secret.text())

        self._has_changes = False
        self.settings_changed.emit(settings)
        self.accept()


    def _load_inst_settings(self, settings):
        """Load instrument-universe settings when controls are available."""
        if hasattr(self, "inst_exchange_mode"):
            self.inst_exchange_mode.setCurrentText(settings.get("inst_exchange_mode", "NFO_ONLY"))
        if hasattr(self, "inst_symbol_mode"):
            self.inst_symbol_mode.setCurrentText(settings.get("inst_symbol_mode", "INDICES_ONLY"))
        if hasattr(self, "inst_preferred_symbols"):
            symbols = settings.get("inst_preferred_symbols", ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"])
            self.inst_preferred_symbols.setText(",".join(symbols) if isinstance(symbols, list) else str(symbols))
        if hasattr(self, "inst_expiry_depth"):
            self.inst_expiry_depth.setValue(int(settings.get("inst_expiry_depth", 1) or 1))

    def _get_inst_settings_dict(self):
        """Return instrument-universe settings from UI controls, with safe fallbacks."""
        defaults = {
            "inst_exchange_mode": "NFO_ONLY",
            "inst_symbol_mode": "INDICES_ONLY",
            "inst_preferred_symbols": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
            "inst_expiry_depth": 1,
        }

        if hasattr(self, "inst_exchange_mode"):
            defaults["inst_exchange_mode"] = self.inst_exchange_mode.currentText()
        if hasattr(self, "inst_symbol_mode"):
            defaults["inst_symbol_mode"] = self.inst_symbol_mode.currentText()
        if hasattr(self, "inst_preferred_symbols"):
            raw = self.inst_preferred_symbols.text().strip()
            defaults["inst_preferred_symbols"] = [s.strip().upper() for s in raw.split(",") if s.strip()]
        if hasattr(self, "inst_expiry_depth"):
            defaults["inst_expiry_depth"] = self.inst_expiry_depth.value()

        return defaults

    def _reset_to_defaults(self):
        reply = QMessageBox.question(
            self,
            "Confirm Reset",
            "Reset all settings to factory defaults?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            defaults = self.config_manager.default_settings

            self.default_symbol.setCurrentText(defaults["default_symbol"])
            self.default_product.setCurrentText(defaults["default_product"])
            self.default_lots.setValue(defaults["default_lots"])
            self.order_sl_per_lot.setValue(int(defaults.get("order_sl_per_lot", 1000)))
            self.order_rr_ratio.setValue(float(defaults.get("order_rr_ratio", 1.5)))
            self.order_trailing_enabled.setChecked(bool(defaults.get("order_trailing_enabled", True)))
            self.auto_refresh.setChecked(defaults.get("auto_refresh", True))
            self.refresh_interval.setValue(defaults.get("refresh_interval", 2))
            self.auto_adjust_ladder.setChecked(defaults.get("auto_adjust_ladder", True))
            self.risk_intraday_drawdown_limit.setValue(int(defaults.get("risk_intraday_drawdown_limit", 0) or 0))
            self.risk_max_portfolio_loss.setValue(int(defaults.get("risk_max_portfolio_loss", 0) or 0))
            self.risk_max_open_positions.setValue(int(defaults.get("risk_max_open_positions", 0) or 0))
            self.risk_max_gross_open_quantity.setValue(int(defaults.get("risk_max_gross_open_quantity", 0) or 0))

            self.api_key.clear()
            self.api_secret.clear()
            self.save_credentials.setChecked(False)

            self._has_changes = True

    def keyPressEvent(self, event):
        """ESC to close."""
        if event.key() == Qt.Key_Escape:
            self._on_close_requested()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()
