import logging
from typing import Callable, List, Optional

from utils.data_models import Position

logger = logging.getLogger(__name__)


class PositionSyncAdapter:
    """Bridges PositionManager events to UI widgets/dialog state."""

    def __init__(
        self,
        *,
        get_positions_dialog: Callable[[], Optional[object]],
        get_inline_positions_table: Callable[[], Optional[object]],
        get_all_positions: Callable[[], List[Position]],
        position_to_dict: Callable[[Position], dict],
        update_performance: Callable[[], None],
        update_market_subscriptions: Callable[[], None],
        reconcile_cvd_automation_positions: Callable[[], None],
        publish_status: Callable[[str, int, str], None],
    ):
        self._get_positions_dialog = get_positions_dialog
        self._get_inline_positions_table = get_inline_positions_table
        self._get_all_positions = get_all_positions
        self._position_to_dict = position_to_dict
        self._update_performance = update_performance
        self._update_market_subscriptions = update_market_subscriptions
        self._reconcile_cvd_automation_positions = reconcile_cvd_automation_positions
        self._publish_status = publish_status

    def on_positions_updated(self, positions: List[Position]):
        logger.debug(f"Received {len(positions)} positions from PositionManager for UI update.")

        positions_dialog = self._get_positions_dialog()
        if positions_dialog and positions_dialog.isVisible():
            positions_dialog.update_positions(positions)

        inline_positions_table = self._get_inline_positions_table()
        if inline_positions_table:
            positions_as_dicts = [self._position_to_dict(position) for position in positions]
            inline_positions_table.update_positions(positions_as_dicts)

        self._update_performance()
        self._update_market_subscriptions()

    def on_position_added(self, position: Position):
        logger.debug(f"Position added: {position.tradingsymbol}, forwarding to UI.")

        positions_dialog = self._get_positions_dialog()
        if positions_dialog and positions_dialog.isVisible():
            if hasattr(positions_dialog, 'positions_table') and hasattr(positions_dialog.positions_table, 'add_position'):
                positions_dialog.positions_table.add_position(position)
            else:
                self.sync_positions_to_dialog()

        self._update_performance()

    def on_position_removed(self, symbol: str):
        logger.debug(f"Position removed: {symbol}, forwarding to UI.")

        positions_dialog = self._get_positions_dialog()
        if positions_dialog and positions_dialog.isVisible():
            if hasattr(positions_dialog, 'positions_table') and hasattr(positions_dialog.positions_table, 'remove_position'):
                positions_dialog.positions_table.remove_position(symbol)
            else:
                self.sync_positions_to_dialog()

        self._update_performance()

    def sync_positions_to_dialog(self):
        positions_dialog = self._get_positions_dialog()
        if not positions_dialog or not positions_dialog.isVisible():
            return

        positions_list = self._get_all_positions()
        if hasattr(positions_dialog, 'positions_table'):
            table_widget = positions_dialog.positions_table
            if hasattr(table_widget, 'update_positions'):
                table_widget.update_positions(positions_list)
            elif hasattr(table_widget, 'clear_all_positions') and hasattr(table_widget, 'add_position'):
                table_widget.clear_all_positions()
                for position in positions_list:
                    table_widget.add_position(position)
            else:
                logger.warning("OpenPositionsDialog's table does not have suitable methods for syncing.")
        else:
            logger.warning("OpenPositionsDialog does not have 'positions_table' attribute for syncing.")

    def on_refresh_completed(self, success: bool):
        if success:
            self._reconcile_cvd_automation_positions()
            self._publish_status("Positions refreshed successfully.", 2500, "success")
            logger.info("Position refresh completed successfully via PositionManager.")
        else:
            self._publish_status("Position refresh failed. Check logs.", 3500, "warning")
            logger.warning("Position refresh failed via PositionManager.")
