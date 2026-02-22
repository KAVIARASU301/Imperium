import re

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap, QTransform
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QHBoxLayout,
    QPushButton,
    QWidget,
    QCheckBox,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QColorDialog,
    QFileDialog,
    QGraphicsPixmapItem,
)


class SetupPanelMixin:

    def _build_setup_dialog(self, compact_combo_style: str, compact_spinbox_style: str):
        self.setup_dialog = QDialog(self)
        self.setup_dialog.setWindowTitle("Auto Trader Setup")
        self.setup_dialog.setModal(False)
        self.setup_dialog.setMinimumWidth(980)
        self.setup_dialog.setMinimumHeight(620)
        self.setup_dialog.setStyleSheet("""
            QDialog {
                background: #161A25;
                color: #E0E0E0;
            }
            QGroupBox {
                border: 1px solid #3A4458;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 10px;
                font-weight: 600;
                color: #9CCAF4;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
            }
            QLabel {
                color: #B0B0B0;
                font-size: 11px;
                font-weight: 600;
            }
        """)

        layout = QVBoxLayout(self.setup_dialog)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        content_row = QHBoxLayout()
        content_row.setSpacing(8)

        INPUT_W = 85
        COMBO_W = 150

        col1 = QVBoxLayout()
        col1.setSpacing(7)
        col2 = QVBoxLayout()
        col2.setSpacing(7)
        col3 = QVBoxLayout()
        col3.setSpacing(7)

        def _set_input_w(widget, w=INPUT_W):
            widget.setFixedWidth(w)

        def _set_combo_w(widget, w=COMBO_W):
            widget.setFixedWidth(w)

        def _compact_form(group_title):
            grp = QGroupBox(group_title)
            frm = QFormLayout(grp)
            frm.setLabelAlignment(Qt.AlignLeft)
            frm.setContentsMargins(8, 6, 8, 6)
            frm.setSpacing(5)
            frm.setHorizontalSpacing(8)
            return grp, frm

        # ═══════════════════════════════════════════════════════════════
        # COL 1 — Automation + Stacker + ATR / Signal
        # ═══════════════════════════════════════════════════════════════

        # ── Automation ────────────────────────────────────────────────
        auto_group, auto_form = _compact_form("Automation")
        _set_input_w(self.automation_stoploss_input)
        _set_input_w(self.max_profit_giveback_input)
        _set_combo_w(self.automation_route_combo)

        auto_form.addRow("Stop Loss", self.automation_stoploss_input)
        auto_form.addRow("Max Profit Giveback", self.max_profit_giveback_input)

        giveback_strategy_row = QWidget()
        giveback_strategy_layout = QHBoxLayout(giveback_strategy_row)
        giveback_strategy_layout.setContentsMargins(0, 0, 0, 0)
        giveback_strategy_layout.setSpacing(6)
        giveback_strategy_layout.addWidget(self.max_giveback_atr_reversal_check)
        giveback_strategy_layout.addWidget(self.max_giveback_ema_cross_check)
        giveback_strategy_layout.addWidget(self.max_giveback_atr_divergence_check)
        giveback_strategy_layout.addWidget(self.max_giveback_range_breakout_check)
        giveback_strategy_layout.addStretch()

        auto_form.addRow("Giveback On", giveback_strategy_row)
        auto_form.addRow("Route", self.automation_route_combo)

        col1.addWidget(auto_group)

        # ── Stacker ───────────────────────────────────────────────────
        stacker_group, stacker_form = _compact_form("Stacker")

        _set_input_w(self.stacker_step_input)
        _set_input_w(self.stacker_max_input)

        stacker_note = QLabel("Pyramid: adds a position every N pts of profit.")
        stacker_note.setStyleSheet("color:#8A9BA8; font-size:10px;")
        stacker_form.addRow(stacker_note)

        stacker_form.addRow("Enable", self.stacker_enabled_check)
        stacker_form.addRow("Step (pts)", self.stacker_step_input)
        stacker_form.addRow("Max Stacks", self.stacker_max_input)

        col1.addWidget(stacker_group)

        # ── ATR / Signal ──────────────────────────────────────────────
        signal_group, signal_form = _compact_form("ATR / Signal")

        _set_input_w(self.atr_base_ema_input)
        _set_input_w(self.atr_distance_input)
        _set_input_w(self.cvd_atr_distance_input)
        _set_input_w(self.cvd_ema_gap_input)

        signal_form.addRow("ATR Base EMA", self.atr_base_ema_input)
        signal_form.addRow("ATR Distance", self.atr_distance_input)
        signal_form.addRow("CVD ATR Dist", self.cvd_atr_distance_input)
        signal_form.addRow("CVD EMA Gap", self.cvd_ema_gap_input)

        col1.addWidget(signal_group)
        col1.addStretch()

        # (COL2 and COL3 remain unchanged — omitted here for brevity)
        content_row.addLayout(col1, 1)
        content_row.addLayout(col2, 1)
        content_row.addLayout(col3, 1)

        layout.addLayout(content_row, 1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.setup_dialog.hide)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)


    def _open_setup_dialog(self):
        self.setup_dialog.show()
        self.setup_dialog.raise_()
        self.setup_dialog.activateWindow()



    def _set_color_button(self, btn: QPushButton, color_hex: str):
        btn.setText(color_hex.upper())
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {color_hex};
                color: #111;
                font-weight: 700;
                border: 1px solid #3A4458;
                border-radius: 4px;
                padding: 4px 8px;
            }}
            """
        )



    def _pick_color(self, button_attr: str, color_attr: str):
        current = getattr(self, color_attr, "#26A69A")
        picked = QColorDialog.getColor(QColor(current), self, "Select Color")
        if not picked.isValid():
            return
        new_color = picked.name()
        setattr(self, color_attr, new_color)
        self._set_color_button(getattr(self, button_attr), new_color)
        self._apply_visual_settings()
        self._persist_setup_values()



    def _on_pick_background_image(self, target: str):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Background Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not file_path:
            return

        if target == "window":
            self._window_bg_image_path = file_path
        elif target == "chart":
            self._chart_bg_image_path = file_path
        else:
            return

        self._update_bg_image_labels()
        self._apply_background_image()
        self._persist_setup_values()



    def _on_clear_background_image(self, target: str):
        if target == "window":
            self._window_bg_image_path = ""
        elif target == "chart":
            self._chart_bg_image_path = ""
        else:
            return

        self._update_bg_image_labels()
        self._apply_background_image()
        self._persist_setup_values()



    def _update_bg_image_labels(self):
        window_name = self._window_bg_image_path.split('/')[-1] if self._window_bg_image_path else "No image selected"
        chart_name = self._chart_bg_image_path.split('/')[-1] if self._chart_bg_image_path else "No image selected"
        self.window_bg_image_label.setText(window_name)
        self.chart_bg_image_label.setText(chart_name)



    def _apply_background_image(self):
        window_image_path = self._window_bg_image_path
        chart_image_path = self._chart_bg_image_path

        # reset top-level styling
        self.setStyleSheet("")
        self.price_plot.setStyleSheet("")
        self.plot.setStyleSheet("")

        if window_image_path:
            normalized_window = window_image_path.replace('\\', '/')
            self.setStyleSheet(
                f"QDialog#autoTraderWindow {{background-image: url('{normalized_window}'); background-position: center;}}"
            )

        chart_image_applied = False
        if chart_image_path:
            chart_pixmap = QPixmap(chart_image_path)
            if not chart_pixmap.isNull():
                self._ensure_chart_bg_items()
                self._price_bg_item.setPixmap(chart_pixmap)
                self._cvd_bg_item.setPixmap(chart_pixmap)
                self._price_bg_item.show()
                self._cvd_bg_item.show()
                self._sync_chart_bg_item_geometry(self.price_plot, self._price_bg_item)
                self._sync_chart_bg_item_geometry(self.plot, self._cvd_bg_item)
                chart_image_applied = True

        if chart_image_applied:
            self.price_plot.setBackground(None)
            self.plot.setBackground(None)
        else:
            self._clear_chart_bg_items()
            self.price_plot.setBackground("#161A25")
            self.plot.setBackground("#161A25")

        show_grid = self.show_grid_lines_check.isChecked() if hasattr(self, 'show_grid_lines_check') else True
        self.price_plot.showGrid(x=show_grid, y=show_grid, alpha=0.12)
        self.plot.showGrid(x=show_grid, y=show_grid, alpha=0.12)



    def _ensure_chart_bg_items(self):
        if not hasattr(self, '_price_bg_item'):
            self._price_bg_item = QGraphicsPixmapItem()
            self._price_bg_item.setZValue(-1e9)
            self.price_plot.plotItem.vb.addItem(self._price_bg_item, ignoreBounds=True)
            self.price_plot.plotItem.vb.sigRangeChanged.connect(
                lambda *_: self._sync_chart_bg_item_geometry(self.price_plot, self._price_bg_item)
            )

        if not hasattr(self, '_cvd_bg_item'):
            self._cvd_bg_item = QGraphicsPixmapItem()
            self._cvd_bg_item.setZValue(-1e9)
            self.plot.plotItem.vb.addItem(self._cvd_bg_item, ignoreBounds=True)
            self.plot.plotItem.vb.sigRangeChanged.connect(
                lambda *_: self._sync_chart_bg_item_geometry(self.plot, self._cvd_bg_item)
            )



    def _clear_chart_bg_items(self):
        if hasattr(self, '_price_bg_item'):
            self._price_bg_item.hide()
        if hasattr(self, '_cvd_bg_item'):
            self._cvd_bg_item.hide()



    @staticmethod
    def _sync_chart_bg_item_geometry(plot_widget, pixmap_item: QGraphicsPixmapItem):
        pixmap = pixmap_item.pixmap()
        if pixmap.isNull():
            return

        x_range, y_range = plot_widget.plotItem.vb.viewRange()
        x_min, x_max = float(x_range[0]), float(x_range[1])
        y_min, y_max = float(y_range[0]), float(y_range[1])
        width = max(1.0, float(pixmap.width()))
        height = max(1.0, float(pixmap.height()))

        sx = (x_max - x_min) / width if width else 1.0
        sy = (y_max - y_min) / height if height else 1.0

        transform = QTransform()
        transform.translate(x_min, y_max)
        transform.scale(sx, -sy)
        pixmap_item.setTransform(transform)



    def _on_setup_visual_settings_changed(self, *_):
        self._apply_visual_settings()
        self._persist_setup_values()



    def _apply_visual_settings(self):
        self._chart_line_width = float(self.chart_line_width_input.value())
        self._chart_line_opacity = float(self.chart_line_opacity_input.value())
        self._confluence_line_width = float(self.confluence_line_width_input.value())
        self._confluence_line_opacity = float(self.confluence_line_opacity_input.value())
        self._ema_line_opacity = float(self.ema_line_opacity_input.value())

        self.price_prev_curve.setPen(pg.mkPen(self._price_line_color, width=max(1.0, self._chart_line_width - 0.4), style=Qt.DashLine))
        self.price_today_curve.setPen(pg.mkPen(self._price_line_color, width=self._chart_line_width))
        self.price_today_tick_curve.setPen(pg.mkPen(self._price_line_color, width=max(0.8, self._chart_line_width - 1.0)))

        self.prev_curve.setPen(pg.mkPen(self._chart_line_color, width=max(1.0, self._chart_line_width - 0.4), style=Qt.DashLine))
        self.today_curve.setPen(pg.mkPen(self._chart_line_color, width=self._chart_line_width))
        self.today_tick_curve.setPen(pg.mkPen(self._chart_line_color, width=max(0.8, self._chart_line_width - 1.0)))

        for curve in (self.price_prev_curve, self.price_today_curve, self.price_today_tick_curve, self.prev_curve, self.today_curve, self.today_tick_curve):
            curve.setOpacity(self._chart_line_opacity)

        self._set_color_button(self.chart_line_color_btn, self._chart_line_color)
        self._set_color_button(self.price_line_color_btn, self._price_line_color)
        self._set_color_button(self.confluence_short_color_btn, self._confluence_short_color)
        self._set_color_button(self.confluence_long_color_btn, self._confluence_long_color)

        for period, cb in self.setup_ema_default_checks.items():
            self.ema_checkboxes[period].setChecked(cb.isChecked())
            self._on_ema_toggled(period, cb.isChecked())

        self.simulator_run_btn.setVisible(not self.hide_simulator_btn_check.isChecked())
        self._apply_background_image()
        self._recolor_existing_confluence_lines()



    def _recolor_existing_confluence_lines(self):
        line_map = getattr(self, "_confluence_line_map", {})
        for key, pairs in line_map.items():
            is_short = key.startswith("S:")
            color = self._confluence_short_color if is_short else self._confluence_long_color
            for _, line in pairs:
                line.setPen(pg.mkPen(color, width=self._confluence_line_width))
                line.setOpacity(self._confluence_line_opacity)
