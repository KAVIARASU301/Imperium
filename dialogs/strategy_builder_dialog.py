import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from utils.data_models import Contract

logger = logging.getLogger(__name__)


@dataclass
class StrategyLeg:
    side: str
    option_type: str
    strike: float
    lots: int
    contract: Optional[Contract]


class StrategyBuilderDialog(QDialog):
    def __init__(
        self,
        instrument_data: Dict,
        strike_ladder,
        symbol: str,
        expiry: str,
        default_lots: int,
        product: str,
        on_execute: Callable[[List[dict]], None],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.instrument_data = instrument_data
        self.strike_ladder = strike_ladder
        self.symbol = symbol
        self.expiry = expiry
        self.default_lots = max(1, int(default_lots))
        self.product = product
        self.on_execute = on_execute

        self.legs: List[StrategyLeg] = []

        self.setWindowTitle("Strategy Builder")
        self.setMinimumSize(980, 620)

        self._setup_ui()
        self._refresh_strike_inputs()
        self._apply_styles()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QLabel("Strategy Builder")
        header.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        layout.addWidget(header)

        meta_layout = QHBoxLayout()
        self.symbol_label = QLabel(f"Symbol: {self.symbol}")
        self.expiry_label = QLabel(f"Expiry: {self.expiry}")
        self.atm_label = QLabel("ATM: —")
        self.interval_label = QLabel("Step: —")
        for label in (self.symbol_label, self.expiry_label, self.atm_label, self.interval_label):
            label.setStyleSheet("color: #C3CAD8; font-weight: 600;")
            meta_layout.addWidget(label)
        meta_layout.addStretch()
        layout.addLayout(meta_layout)

        top_row = QHBoxLayout()

        template_group = QGroupBox("Strategy Templates")
        template_layout = QGridLayout(template_group)
        template_layout.setHorizontalSpacing(10)
        template_layout.setVerticalSpacing(8)

        self.template_combo = QComboBox()
        self.template_combo.addItems([
            "Long Call",
            "Long Put",
            "Bull Call Spread",
            "Bear Put Spread",
            "Long Straddle",
            "Short Straddle",
            "Long Strangle",
            "Short Strangle",
            "Iron Condor",
        ])

        self.call_offset_spin = QSpinBox()
        self.call_offset_spin.setRange(0, 20)
        self.call_offset_spin.setValue(0)
        self.put_offset_spin = QSpinBox()
        self.put_offset_spin.setRange(0, 20)
        self.put_offset_spin.setValue(0)
        self.wing_width_spin = QSpinBox()
        self.wing_width_spin.setRange(1, 20)
        self.wing_width_spin.setValue(1)

        template_layout.addWidget(QLabel("Template"), 0, 0)
        template_layout.addWidget(self.template_combo, 0, 1, 1, 2)
        template_layout.addWidget(QLabel("Call Offset (steps)"), 1, 0)
        template_layout.addWidget(self.call_offset_spin, 1, 1)
        template_layout.addWidget(QLabel("Put Offset (steps)"), 1, 2)
        template_layout.addWidget(self.put_offset_spin, 1, 3)
        template_layout.addWidget(QLabel("Wing Width (steps)"), 2, 0)
        template_layout.addWidget(self.wing_width_spin, 2, 1)

        self.apply_template_btn = QPushButton("Load Template")
        self.apply_template_btn.clicked.connect(self._apply_template)
        template_layout.addWidget(self.apply_template_btn, 2, 2, 1, 2)

        top_row.addWidget(template_group, 2)

        manual_group = QGroupBox("Manual Leg Builder")
        manual_layout = QGridLayout(manual_group)
        manual_layout.setHorizontalSpacing(10)
        manual_layout.setVerticalSpacing(8)

        self.side_combo = QComboBox()
        self.side_combo.addItems(["BUY", "SELL"])
        self.option_type_combo = QComboBox()
        self.option_type_combo.addItems(["CE", "PE"])
        self.strike_combo = QComboBox()
        self.lots_spin = QSpinBox()
        self.lots_spin.setRange(1, 100)
        self.lots_spin.setValue(self.default_lots)

        manual_layout.addWidget(QLabel("Side"), 0, 0)
        manual_layout.addWidget(self.side_combo, 0, 1)
        manual_layout.addWidget(QLabel("Type"), 0, 2)
        manual_layout.addWidget(self.option_type_combo, 0, 3)
        manual_layout.addWidget(QLabel("Strike"), 1, 0)
        manual_layout.addWidget(self.strike_combo, 1, 1)
        manual_layout.addWidget(QLabel("Lots"), 1, 2)
        manual_layout.addWidget(self.lots_spin, 1, 3)

        self.add_leg_btn = QPushButton("Add Leg")
        self.add_leg_btn.clicked.connect(self._add_manual_leg)
        manual_layout.addWidget(self.add_leg_btn, 2, 0, 1, 2)

        self.clear_btn = QPushButton("Clear Legs")
        self.clear_btn.clicked.connect(self._clear_legs)
        manual_layout.addWidget(self.clear_btn, 2, 2, 1, 2)

        top_row.addWidget(manual_group, 3)

        layout.addLayout(top_row)

        table_frame = QFrame()
        table_layout = QVBoxLayout(table_frame)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.legs_table = QTableWidget(0, 6)
        self.legs_table.setHorizontalHeaderLabels(["Side", "Type", "Strike", "Lots", "LTP", "Action"])
        self.legs_table.horizontalHeader().setStretchLastSection(True)
        self.legs_table.verticalHeader().setVisible(False)
        self.legs_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.legs_table.setSelectionBehavior(QTableWidget.SelectRows)
        table_layout.addWidget(self.legs_table)
        layout.addWidget(table_frame)

        footer_layout = QHBoxLayout()
        self.summary_label = QLabel("Net Premium: — | Total Lots: 0")
        self.summary_label.setStyleSheet("color: #C3CAD8; font-weight: 600;")
        footer_layout.addWidget(self.summary_label)
        footer_layout.addStretch()

        self.refresh_prices_btn = QPushButton("Refresh Prices")
        self.refresh_prices_btn.clicked.connect(self._refresh_leg_prices)
        footer_layout.addWidget(self.refresh_prices_btn)

        self.execute_btn = QPushButton("Execute Strategy")
        self.execute_btn.clicked.connect(self._execute_strategy)
        self.execute_btn.setObjectName("executeStrategyButton")
        footer_layout.addWidget(self.execute_btn)
        layout.addLayout(footer_layout)

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #161A25;
                color: #E0E0E0;
            }
            QGroupBox {
                border: 1px solid #2A3140;
                border-radius: 8px;
                margin-top: 12px;
                padding: 8px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #C3CAD8;
            }
            QComboBox, QSpinBox {
                background-color: #1F2432;
                border: 1px solid #2A3140;
                border-radius: 6px;
                padding: 4px 8px;
            }
            QTableWidget {
                background-color: #111622;
                border: 1px solid #2A3140;
                border-radius: 8px;
                gridline-color: #2A3140;
            }
            QHeaderView::section {
                background-color: #1F2432;
                padding: 6px;
                border: 1px solid #2A3140;
                color: #C3CAD8;
                font-weight: 600;
            }
            QPushButton {
                background-color: #212635;
                border: 1px solid #3A4458;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #29C7C9;
                color: #161A25;
            }
            #executeStrategyButton {
                background-color: #29C7C9;
                color: #161A25;
                border: none;
                padding: 8px 16px;
                font-weight: 700;
            }
            #executeStrategyButton:hover {
                background-color: #35D6D8;
            }
        """)

    def _refresh_strike_inputs(self):
        contracts = self.strike_ladder.get_current_contracts()
        strikes = sorted(contracts.keys())
        self.strike_combo.clear()
        for strike in strikes:
            self.strike_combo.addItem(str(strike), strike)

        atm_strike = self.strike_ladder.atm_strike
        strike_interval = self.strike_ladder.get_strike_interval()
        self.atm_label.setText(f"ATM: {atm_strike:.2f}" if atm_strike else "ATM: —")
        self.interval_label.setText(f"Step: {strike_interval:.2f}" if strike_interval else "Step: —")

    def _get_contract(self, strike: float, option_type: str) -> Optional[Contract]:
        contracts = self.strike_ladder.get_current_contracts()
        if strike in contracts:
            return contracts[strike].get(option_type)
        return None

    def _add_leg(self, leg: StrategyLeg):
        self.legs.append(leg)
        self._render_legs()

    def _add_manual_leg(self):
        if self.strike_combo.count() == 0:
            QMessageBox.warning(self, "No Strikes", "Strike ladder data is not ready yet.")
            return
        strike = self.strike_combo.currentData()
        option_type = self.option_type_combo.currentText()
        contract = self._get_contract(strike, option_type)
        leg = StrategyLeg(
            side=self.side_combo.currentText(),
            option_type=option_type,
            strike=strike,
            lots=int(self.lots_spin.value()),
            contract=contract,
        )
        self._add_leg(leg)

    def _clear_legs(self):
        self.legs.clear()
        self._render_legs()

    def _apply_template(self):
        if self.strike_combo.count() == 0:
            QMessageBox.warning(self, "No Strikes", "Strike ladder data is not ready yet.")
            return
        template = self.template_combo.currentText()
        call_offset = self.call_offset_spin.value()
        put_offset = self.put_offset_spin.value()
        wing_width = self.wing_width_spin.value()

        atm = self.strike_ladder.atm_strike
        step = self.strike_ladder.get_strike_interval()

        if not atm or not step:
            QMessageBox.warning(self, "ATM Unavailable", "ATM strike data is not ready yet.")
            return

        def strike_at(offset: int, direction: int) -> float:
            return atm + (offset * step * direction)

        self._clear_legs()

        def add_leg(side: str, option_type: str, strike: float):
            contract = self._get_contract(strike, option_type)
            self._add_leg(StrategyLeg(side, option_type, strike, self.default_lots, contract))

        if template == "Long Call":
            add_leg("BUY", "CE", strike_at(call_offset, 1))
        elif template == "Long Put":
            add_leg("BUY", "PE", strike_at(put_offset, -1))
        elif template == "Bull Call Spread":
            lower_strike = strike_at(call_offset, 1)
            higher_strike = strike_at(call_offset + wing_width, 1)
            add_leg("BUY", "CE", lower_strike)
            add_leg("SELL", "CE", higher_strike)
        elif template == "Bear Put Spread":
            higher_strike = strike_at(put_offset, -1)
            lower_strike = strike_at(put_offset + wing_width, -1)
            add_leg("BUY", "PE", higher_strike)
            add_leg("SELL", "PE", lower_strike)
        elif template == "Long Straddle":
            add_leg("BUY", "CE", atm)
            add_leg("BUY", "PE", atm)
        elif template == "Short Straddle":
            add_leg("SELL", "CE", atm)
            add_leg("SELL", "PE", atm)
        elif template == "Long Strangle":
            add_leg("BUY", "CE", strike_at(max(call_offset, 1), 1))
            add_leg("BUY", "PE", strike_at(max(put_offset, 1), -1))
        elif template == "Short Strangle":
            add_leg("SELL", "CE", strike_at(max(call_offset, 1), 1))
            add_leg("SELL", "PE", strike_at(max(put_offset, 1), -1))
        elif template == "Iron Condor":
            sell_call = strike_at(max(call_offset, 1), 1)
            buy_call = strike_at(max(call_offset, 1) + wing_width, 1)
            sell_put = strike_at(max(put_offset, 1), -1)
            buy_put = strike_at(max(put_offset, 1) + wing_width, -1)
            add_leg("SELL", "CE", sell_call)
            add_leg("BUY", "CE", buy_call)
            add_leg("SELL", "PE", sell_put)
            add_leg("BUY", "PE", buy_put)
        else:
            logger.warning("Unknown template selected.")

        self._render_legs()

    def _render_legs(self):
        self.legs_table.setRowCount(0)
        total_lots = 0
        net_premium = 0.0
        for idx, leg in enumerate(self.legs):
            self.legs_table.insertRow(idx)
            self.legs_table.setItem(idx, 0, QTableWidgetItem(leg.side))
            self.legs_table.setItem(idx, 1, QTableWidgetItem(leg.option_type))
            self.legs_table.setItem(idx, 2, QTableWidgetItem(f"{leg.strike:.2f}"))
            self.legs_table.setItem(idx, 3, QTableWidgetItem(str(leg.lots)))

            ltp = getattr(leg.contract, "ltp", 0.0) if leg.contract else 0.0
            self.legs_table.setItem(idx, 4, QTableWidgetItem(f"{ltp:.2f}" if ltp else "—"))

            remove_btn = QPushButton("Remove")
            remove_btn.clicked.connect(lambda _, row=idx: self._remove_leg(row))
            self.legs_table.setCellWidget(idx, 5, remove_btn)

            total_lots += leg.lots
            if ltp:
                leg_sign = 1 if leg.side == "BUY" else -1
                net_premium += leg_sign * ltp * leg.lots

        if total_lots == 0:
            self.summary_label.setText("Net Premium: — | Total Lots: 0")
        else:
            premium_label = f"₹{net_premium:,.2f}" if net_premium else "—"
            self.summary_label.setText(f"Net Premium: {premium_label} | Total Lots: {total_lots}")

    def _remove_leg(self, row_index: int):
        if 0 <= row_index < len(self.legs):
            self.legs.pop(row_index)
            self._render_legs()

    def _refresh_leg_prices(self):
        self._render_legs()

    def _execute_strategy(self):
        if not self.legs:
            QMessageBox.warning(self, "No Legs", "Add at least one leg before executing.")
            return

        lot_size = self.instrument_data.get(self.symbol, {}).get("lot_size", 1)
        if lot_size <= 0:
            lot_size = 1

        order_params_list: List[dict] = []
        for leg in self.legs:
            if not leg.contract:
                QMessageBox.warning(
                    self,
                    "Missing Contract",
                    f"Contract data missing for {leg.option_type} {leg.strike:.2f}.",
                )
                return
            quantity = leg.lots * lot_size
            order_params_list.append({
                "contract": leg.contract,
                "quantity": quantity,
                "price": None,
                "order_type": "MARKET",
                "product": self.product,
                "side": leg.side,
            })

        self.on_execute(order_params_list)
        self.accept()
