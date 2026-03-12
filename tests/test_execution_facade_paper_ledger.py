from datetime import date
from unittest.mock import Mock

from core.execution.execution_facade import ExecutionFacade
from core.utils.data_models import Contract, Position


def _build_facade(get_position):
    return ExecutionFacade(
        get_instrument_data=lambda: {},
        get_settings=lambda: {},
        get_active_order_confirmation_dialog=lambda: None,
        set_active_order_confirmation_dialog=lambda _: None,
        create_order_confirmation_dialog=lambda _: None,
        warning_user=lambda *_: None,
        execute_orders=lambda *_: None,
        get_position=get_position,
        record_completed_exit_trade=Mock(),
        update_account_info=lambda: None,
        update_account_summary_widget=lambda: None,
        refresh_positions=lambda: None,
        publish_status=lambda *_: None,
    )


def _position(symbol: str = "NIFTY24JAN22000CE") -> Position:
    return Position(
        symbol="NIFTY",
        tradingsymbol=symbol,
        quantity=25,
        average_price=100.0,
        ltp=100.0,
        pnl=0.0,
        contract=Contract(
            symbol="NIFTY",
            strike=22000,
            option_type="CE",
            expiry=date(2026, 1, 29),
            tradingsymbol=symbol,
            instrument_token=123,
            lot_size=25,
        ),
        order_id="entry-order",
    )


def test_paper_exit_uses_captured_entry_snapshot_for_entry_time():
    current_position = _position()
    facade = _build_facade(get_position=lambda _: current_position)
    processed = set()

    facade.on_paper_trade_update(
        order_data={
            "order_id": "entry-1",
            "status": "COMPLETE",
            "tradingsymbol": current_position.tradingsymbol,
            "transaction_type": "BUY",
            "entry_qty": 25,
            "exit_qty": 0,
            "average_price": 101.5,
            "exchange_timestamp": "2026-01-10T09:15:00",
        },
        processed_order_ids=processed,
    )

    facade.on_paper_trade_update(
        order_data={
            "order_id": "exit-1",
            "status": "COMPLETE",
            "tradingsymbol": current_position.tradingsymbol,
            "transaction_type": "SELL",
            "entry_qty": 0,
            "exit_qty": 25,
            "average_price": 103.0,
            "exchange_timestamp": "2026-01-10T09:17:00",
        },
        processed_order_ids=processed,
    )

    recorder = facade._record_completed_exit_trade
    assert recorder.call_count == 1

    call = recorder.call_args
    original_position = call.kwargs["original_position"]
    confirmed_order = call.kwargs["confirmed_order"]

    assert original_position.entry_time.isoformat() == "2026-01-10T09:15:00"
    assert original_position.average_price == 101.5
    assert confirmed_order["filled_quantity"] == 25


def test_paper_exit_maps_fifo_entries_for_partial_scale_out():
    current_position = _position()
    facade = _build_facade(get_position=lambda _: current_position)
    processed = set()

    # Two separate BUY entries
    facade.on_paper_trade_update(
        order_data={
            "order_id": "entry-a",
            "status": "COMPLETE",
            "tradingsymbol": current_position.tradingsymbol,
            "transaction_type": "BUY",
            "entry_qty": 10,
            "exit_qty": 0,
            "average_price": 100.0,
            "exchange_timestamp": "2026-01-10T09:15:00",
        },
        processed_order_ids=processed,
    )
    facade.on_paper_trade_update(
        order_data={
            "order_id": "entry-b",
            "status": "COMPLETE",
            "tradingsymbol": current_position.tradingsymbol,
            "transaction_type": "BUY",
            "entry_qty": 15,
            "exit_qty": 0,
            "average_price": 102.0,
            "exchange_timestamp": "2026-01-10T09:16:00",
        },
        processed_order_ids=processed,
    )

    # Exit all 25 in one order should be mapped to 10+15 FIFO snapshots
    facade.on_paper_trade_update(
        order_data={
            "order_id": "exit-all",
            "status": "COMPLETE",
            "tradingsymbol": current_position.tradingsymbol,
            "transaction_type": "SELL",
            "entry_qty": 0,
            "exit_qty": 25,
            "average_price": 103.0,
            "exchange_timestamp": "2026-01-10T09:18:00",
        },
        processed_order_ids=processed,
    )

    recorder = facade._record_completed_exit_trade
    assert recorder.call_count == 2

    first = recorder.call_args_list[0].kwargs
    second = recorder.call_args_list[1].kwargs

    assert first["original_position"].entry_time.isoformat() == "2026-01-10T09:15:00"
    assert second["original_position"].entry_time.isoformat() == "2026-01-10T09:16:00"
    assert first["confirmed_order"]["filled_quantity"] == 10
    assert second["confirmed_order"]["filled_quantity"] == 15
