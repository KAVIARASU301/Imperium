from PySide6.QtWidgets import QWidget, QLabel, QGridLayout, QFrame
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from core.cvd.cvd_engine import CVDEngine


class CVDCell(QFrame):
    """
    Single CVD display cell (one symbol).
    """

    def __init__(self, display_symbol: str):
        super().__init__()
        self.display_symbol = display_symbol

        self.setObjectName("cvdCell")
        self.setStyleSheet("""
            QFrame#cvdCell {
                background-color: #161A25;
                border: 1px solid #2F3447;
                border-radius: 10px;
            }
        """)

        layout = QGridLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self.symbol_label = QLabel(display_symbol)
        self.symbol_label.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.symbol_label.setAlignment(Qt.AlignCenter)

        self.cvd_label = QLabel("—")
        self.cvd_label.setFont(QFont("Segoe UI", 18))
        self.cvd_label.setAlignment(Qt.AlignCenter)
        self.cvd_label.setStyleSheet("color: #A9B1C3;")

        layout.addWidget(self.symbol_label, 0, 0)
        layout.addWidget(self.cvd_label, 1, 0)

    def update_value(self, cvd: float | None):
        """
        Update displayed CVD value with proper coloring.
        """
        if cvd is None:
            self.cvd_label.setText("—")
            self.cvd_label.setStyleSheet("color: #A9B1C3;")
            return

        self.cvd_label.setText(f"{int(cvd):,}")

        if cvd > 0:
            self.cvd_label.setStyleSheet("color: #26A69A;")  # green
        elif cvd < 0:
            self.cvd_label.setStyleSheet("color: #EF5350;")  # red
        else:
            self.cvd_label.setStyleSheet("color: #A9B1C3;")  # neutral


class CVDGridWidget(QWidget):
    """
    2x2 grid-style CVD monitor for index futures.
    """

    def __init__(self, cvd_engine: CVDEngine, display_symbols: list[str], parent=None):
        super().__init__(parent)

        self.cvd_engine = cvd_engine

        # display_symbol -> { cell, engine_symbol }
        self.cells: dict[str, dict] = {}

        grid = QGridLayout(self)
        grid.setSpacing(8)
        grid.setContentsMargins(6, 6, 6, 6)

        for idx, display_symbol in enumerate(display_symbols):
            cell = CVDCell(display_symbol)

            self.cells[display_symbol] = {
                "cell": cell,
                "engine_symbol": None  # resolved dynamically (e.g. BANKNIFTY25JANFUT)
            }

            grid.addWidget(cell, idx // 2, idx % 2)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(500)

    def refresh(self):
        """
        Refresh grid values from CVDEngine snapshot.
        Automatically maps display symbol -> FUT tradingsymbol.
        """
        snapshot = self.cvd_engine.snapshot()

        for display_symbol, info in self.cells.items():
            engine_symbol = info["engine_symbol"]

            # Resolve FUT symbol once (lazy binding)
            if engine_symbol is None:
                for sym in snapshot.keys():
                    # Example: BANKNIFTY25JANFUT startswith BANKNIFTY
                    if sym.startswith(display_symbol) and sym.endswith("FUT"):
                        info["engine_symbol"] = sym
                        engine_symbol = sym
                        break

            cvd_value = snapshot.get(engine_symbol) if engine_symbol else None
            info["cell"].update_value(cvd_value)
