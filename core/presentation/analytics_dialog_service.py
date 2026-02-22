from dialogs.performance_dialog import PerformanceDialog
from dialogs.pnl_history_dialog import PnlHistoryDialog


class AnalyticsDialogService:
    """Presentation orchestration for analytics dialogs."""

    def __init__(self, main_window):
        self.main_window = main_window

    def show_pnl_history_dialog(self):
        w = self.main_window
        if not hasattr(w, "pnl_history_dialog") or w.pnl_history_dialog is None:
            w.pnl_history_dialog = PnlHistoryDialog(trade_ledger=w.trade_ledger, parent=w)

        w.pnl_history_dialog.show()
        w.pnl_history_dialog.activateWindow()
        w.pnl_history_dialog.raise_()

    def show_performance_dialog(self):
        w = self.main_window
        if w.performance_dialog is None:
            w.performance_dialog = PerformanceDialog(trade_ledger=w.trade_ledger, parent=w)
            w.performance_dialog.finished.connect(lambda: setattr(w, "performance_dialog", None))

        w.performance_dialog.refresh()
        w.performance_dialog.show()
        w.performance_dialog.raise_()
        w.performance_dialog.activateWindow()
