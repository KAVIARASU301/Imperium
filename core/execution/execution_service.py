from core.execution.exit_execution_methods import ExitExecutionMethods
from core.execution.order_execution_methods import OrderExecutionMethods


class ExecutionService:
    """Unified execution surface for main window order and exit flows."""

    def __init__(self, window):
        self.order_methods = OrderExecutionMethods(window)
        self.exit_methods = ExitExecutionMethods(window)

    def execute_orders(self, confirmed_order_details: dict):
        self.order_methods.execute_orders(confirmed_order_details)

    def show_order_results(self, successful_list, failed_list):
        self.order_methods.show_order_results(successful_list, failed_list)

    def execute_single_strike_order(self, order_params: dict):
        self.order_methods.execute_single_strike_order(order_params)

    def confirm_and_finalize_order(self, *args, **kwargs):
        self.order_methods.confirm_and_finalize_order(*args, **kwargs)

    def has_pending_order_for_symbol(self, tradingsymbol: str | None) -> bool:
        return self.order_methods.has_pending_order_for_symbol(tradingsymbol)

    def start_cvd_pending_retry(self, token: int):
        self.order_methods.start_cvd_pending_retry(token)

    def stop_cvd_pending_retry(self, token: int):
        self.order_methods.stop_cvd_pending_retry(token)

    def retry_cvd_pending_order(self, token: int):
        self.order_methods.retry_cvd_pending_order(token)

    def execute_strategy_orders(self, order_params_list, strategy_name=None):
        self.order_methods.execute_strategy_orders(order_params_list, strategy_name)

    def exit_all_positions(self):
        self.exit_methods.exit_all_positions()

    def execute_bulk_exit(self, positions_list):
        self.exit_methods.execute_bulk_exit(positions_list)

    def finalize_bulk_exit_result(self):
        self.exit_methods.finalize_bulk_exit_result()

    def exit_position(self, position_data_to_exit: dict):
        self.exit_methods.exit_position(position_data_to_exit)

    def exit_position_from_dialog(self, symbol_or_pos_data):
        self.exit_methods.exit_position_from_dialog(symbol_or_pos_data)

    def exit_option_positions(self, option_type):
        self.exit_methods.exit_option_positions(option_type)
