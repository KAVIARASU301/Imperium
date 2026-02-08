from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
    QDateEdit, QLineEdit, QLabel, QPushButton, QMessageBox
)
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from datetime import date

from utils.fii_dii_store import FIIDIIStore


class FIIDIIDataDialog(QDialog):
    """
    Manual entry dialog for FII / DII cash market data
    User enters Gross Buy & Gross Sell
    Net is auto-calculated and color-coded
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FII / DII Data Entry")
        self.setMinimumWidth(420)

        self.store = FIIDIIStore()

        self._build_ui()
        self._load_for_date()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)

        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.dateChanged.connect(self._load_for_date)

        # ---- FII ----
        self.fii_buy = self._money_edit()
        self.fii_sell = self._money_edit()
        self.fii_net = self._net_label()

        # ---- DII ----
        self.dii_buy = self._money_edit()
        self.dii_sell = self._money_edit()
        self.dii_net = self._net_label()

        for w in (self.fii_buy, self.fii_sell, self.dii_buy, self.dii_sell):
            w.textChanged.connect(self._recalc)

        form.addRow("Date", self.date_edit)

        form.addRow(self._section("FII"))
        form.addRow("Gross Purchase (Buy)", self.fii_buy)
        form.addRow("Gross Sales (Sell)", self.fii_sell)
        form.addRow("Net", self.fii_net)

        form.addRow(self._section("DII"))
        form.addRow("Gross Purchase (Buy)", self.dii_buy)
        form.addRow("Gross Sales (Sell)", self.dii_sell)
        form.addRow("Net", self.dii_net)

        layout.addLayout(form)

        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._save)
        layout.addWidget(self.save_btn)

    # ------------------------------------------------------------------
    # Widgets
    # ------------------------------------------------------------------

    def _money_edit(self) -> QLineEdit:
        le = QLineEdit()
        le.setPlaceholderText("Enter value (₹ Cr)")
        le.setAlignment(Qt.AlignRight)
        le.setStyleSheet("color: black; background: white;")
        return le

    def _net_label(self) -> QLabel:
        lbl = QLabel("0.00")
        lbl.setAlignment(Qt.AlignRight)
        lbl.setStyleSheet("font-weight: bold;")
        return lbl

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight:bold; margin-top:8px;")
        return lbl

    # ------------------------------------------------------------------
    # Logic
    # ------------------------------------------------------------------

    def _parse(self, txt: str) -> float:
        if not txt.strip():
            return 0.0
        return float(txt.replace(",", ""))

    def _recalc(self):
        fii_net = self._parse(self.fii_buy.text()) - self._parse(self.fii_sell.text())
        dii_net = self._parse(self.dii_buy.text()) - self._parse(self.dii_sell.text())

        self._set_net(self.fii_net, fii_net)
        self._set_net(self.dii_net, dii_net)

    def _set_net(self, label: QLabel, value: float):
        label.setText(f"{value:,.2f}")
        if value > 0:
            label.setStyleSheet("color:#4caf50; font-weight:bold;")
        elif value < 0:
            label.setStyleSheet("color:#f44336; font-weight:bold;")
        else:
            label.setStyleSheet("color:#9e9e9e; font-weight:bold;")

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def _load_for_date(self):
        day = self.date_edit.date().toPython()
        data = self.store.get_day_data(day)

        if not data:
            self.fii_buy.clear()
            self.fii_sell.clear()
            self.dii_buy.clear()
            self.dii_sell.clear()
            self._recalc()
            return

        self.fii_buy.setText(str(data["fii"]["buy"]))
        self.fii_sell.setText(str(data["fii"]["sell"]))
        self.dii_buy.setText(str(data["dii"]["buy"]))
        self.dii_sell.setText(str(data["dii"]["sell"]))
        self._recalc()

    def _save(self):
        try:
            day = self.date_edit.date().toPython()

            fii_buy = self._parse(self.fii_buy.text())
            fii_sell = self._parse(self.fii_sell.text())
            dii_buy = self._parse(self.dii_buy.text())
            dii_sell = self._parse(self.dii_sell.text())

            self.store.set_day_data(
                day=day,
                fii={
                    "buy": fii_buy,
                    "sell": fii_sell,
                    "net": fii_buy - fii_sell
                },
                dii={
                    "buy": dii_buy,
                    "sell": dii_sell,
                    "net": dii_buy - dii_sell
                }
            )

            QMessageBox.information(self, "Saved", "FII/DII data saved successfully.")
            self.accept()

        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Please enter valid numeric values.")
