import json
import logging
from datetime import datetime, date
from typing import Dict, Any, List, Optional
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QLineEdit,
    QTextEdit, QListWidget, QListWidgetItem, QComboBox, QSpinBox, QDateEdit,
    QGroupBox, QGridLayout, QCheckBox, QButtonGroup, QStackedWidget, QMessageBox
)

from utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class JournalDialog(QDialog):
    def __init__(
            self,
            config_manager: ConfigManager,
            parent=None,
            enforce_read_time: bool = False,
            read_time_seconds: int = 300
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.entries: List[Dict[str, Any]] = []
        self._current_entry_id: Optional[str] = None

        self._enforce_read_time = enforce_read_time
        self._read_time_seconds = read_time_seconds
        self._read_time_remaining = read_time_seconds

        self.setWindowTitle("Trading Journal")
        self.setMinimumSize(980, 720)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._setup_ui()
        self._apply_styles()
        self._setup_read_timer()  # Must be before _set_mode
        self._load_entries()
        self._connect_signals()
        self._set_mode("read")

    def _setup_ui(self):
        self.container = QWidget(self)
        self.container.setObjectName("mainContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(20, 14, 20, 20)
        layout.setSpacing(14)

        layout.addLayout(self._create_header())

        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self._create_read_mode())
        self.mode_stack.addWidget(self._create_write_mode())
        layout.addWidget(self.mode_stack, 1)

    def _create_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(12)

        title = QLabel("TRADING JOURNAL")
        title.setObjectName("dialogTitle")

        self.read_btn = QPushButton("READ")
        self.read_btn.setObjectName("modeButton")
        self.read_btn.setCheckable(True)

        self.write_btn = QPushButton("WRITE")
        self.write_btn.setObjectName("modeButton")
        self.write_btn.setCheckable(True)

        self.edit_btn = QPushButton("EDIT")
        self.edit_btn.setObjectName("modeButton")
        self.edit_btn.setCheckable(True)

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.read_btn)
        self.mode_group.addButton(self.write_btn)
        self.mode_group.addButton(self.edit_btn)

        self.read_timer_label = QLabel("")
        self.read_timer_label.setObjectName("timerLabel")

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeButton")

        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.read_btn)
        header.addWidget(self.write_btn)
        header.addWidget(self.edit_btn)
        header.addStretch()
        header.addWidget(self.read_timer_label)
        header.addWidget(self.close_btn)

        return header

    def _create_read_mode(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        sidebar = QVBoxLayout()
        sidebar.setSpacing(8)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search entries...")
        self.search_input.setObjectName("searchInput")

        self.range_filter = QComboBox()
        self.range_filter.addItems(["All", "Today", "7 Days", "30 Days"])
        self.range_filter.setObjectName("rangeFilter")
        self.range_filter.setFixedWidth(90)

        filter_row.addWidget(self.search_input, 1)
        filter_row.addWidget(self.range_filter)

        self.entry_list = QListWidget()
        self.entry_list.setObjectName("entryList")

        self.delete_btn = QPushButton("DELETE")
        self.delete_btn.setObjectName("dangerOutlineButton")

        sidebar.addLayout(filter_row)
        sidebar.addWidget(self.entry_list, 1)
        sidebar.addWidget(self.delete_btn)

        details = QVBoxLayout()
        details.setSpacing(8)
        self.details_title = QLabel("Select an entry")
        self.details_title.setObjectName("detailsTitle")

        self.details_body = QTextEdit()
        self.details_body.setReadOnly(True)
        # FIXED: Allow text selection in read mode
        self.details_body.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        self.details_body.setViewportMargins(10, 8, 10, 8)
        self.details_body.setObjectName("detailsBody")

        details.addWidget(self.details_title)
        details.addWidget(self.details_body, 1)

        layout.addLayout(sidebar, 1)
        layout.addLayout(details, 2)

        return container

    def _create_write_mode(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Session Details - More compact
        meta_group = QGroupBox("Session Details")
        meta_layout = QGridLayout(meta_group)
        meta_layout.setHorizontalSpacing(12)
        meta_layout.setVerticalSpacing(8)
        meta_layout.setContentsMargins(12, 16, 12, 12)

        self.entry_date = QDateEdit()
        self.entry_date.setCalendarPopup(True)
        self.entry_date.setDate(date.today())
        self.entry_date.setObjectName("dateInput")

        self.session_type = QComboBox()
        self.session_type.addItems(["Pre-market", "Intraday", "Post-market", "Weekly Review"])
        self.session_type.setObjectName("sessionInput")

        self.entry_title = QLineEdit()
        self.entry_title.setPlaceholderText("Session title")
        self.entry_title.setObjectName("titleInput")

        self.entry_tags = QLineEdit()
        self.entry_tags.setPlaceholderText("Tags: breakout, discipline, volatility")
        self.entry_tags.setObjectName("tagsInput")

        meta_layout.addWidget(QLabel("Date"), 0, 0)
        meta_layout.addWidget(self.entry_date, 0, 1)
        meta_layout.addWidget(QLabel("Session"), 0, 2)
        meta_layout.addWidget(self.session_type, 0, 3)
        meta_layout.addWidget(QLabel("Title"), 1, 0)
        meta_layout.addWidget(self.entry_title, 1, 1, 1, 3)
        meta_layout.addWidget(QLabel("Tags"), 2, 0)
        meta_layout.addWidget(self.entry_tags, 2, 1, 1, 3)

        layout.addWidget(meta_group)

        # FIXED: Thoughts & Execution - Consolidated to 3 fields
        text_group = QGroupBox("Thoughts & Execution")
        text_layout = QVBoxLayout(text_group)
        text_layout.setSpacing(8)
        text_layout.setContentsMargins(12, 16, 12, 12)

        # 1. Plan & Context (Market + Plan combined)
        plan_context_label = QLabel("Plan & Context")
        plan_context_label.setObjectName("fieldLabel")
        self.plan_context = QTextEdit()
        self.plan_context.setPlaceholderText(
            "Market context, catalysts, levels, plan, triggers, setups, risk limits...")
        self.plan_context.setViewportMargins(8, 6, 8, 6)
        self.plan_context.setMaximumHeight(80)
        self.plan_context.setObjectName("textInput")

        # 2. Execution & Results (What happened)
        execution_label = QLabel("Execution & Results")
        execution_label.setObjectName("fieldLabel")
        self.execution_notes = QTextEdit()
        self.execution_notes.setPlaceholderText("What you executed, adjustments, outcomes, P&L, what worked/didn't...")
        self.execution_notes.setViewportMargins(8, 6, 8, 6)
        self.execution_notes.setMaximumHeight(80)
        self.execution_notes.setObjectName("textInput")

        # 3. Lessons & Actions (Reflection + Next steps combined)
        lessons_actions_label = QLabel("Lessons & Actions")
        lessons_actions_label.setObjectName("fieldLabel")
        self.lessons_actions = QTextEdit()
        self.lessons_actions.setPlaceholderText("Key lessons, improvements, action items, checklists, reminders...")
        self.lessons_actions.setViewportMargins(8, 6, 8, 6)
        self.lessons_actions.setMaximumHeight(80)
        self.lessons_actions.setObjectName("textInput")

        text_layout.addWidget(plan_context_label)
        text_layout.addWidget(self.plan_context)
        text_layout.addWidget(execution_label)
        text_layout.addWidget(self.execution_notes)
        text_layout.addWidget(lessons_actions_label)
        text_layout.addWidget(self.lessons_actions)

        layout.addWidget(text_group)

        # Emotions & Mistakes - Side by side
        reflection_row = QHBoxLayout()
        reflection_row.setSpacing(12)

        self.emotion_checks = self._create_checkbox_group(
            "Emotional State",
            ["Confident", "Anxious", "Overconfident", "Disciplined", "Impulsive", "Calm"]
        )
        self.mistake_checks = self._create_checkbox_group(
            "Mistakes Made",
            ["Overtrading", "Revenge", "FOMO", "Ignored Stop", "Bad Risk", "Emotional"]
        )

        reflection_row.addWidget(self.emotion_checks)
        reflection_row.addWidget(self.mistake_checks)

        layout.addLayout(reflection_row)

        # Ratings - More compact
        ratings_group = QGroupBox("Performance Ratings")
        ratings_layout = QGridLayout(ratings_group)
        ratings_layout.setHorizontalSpacing(12)
        ratings_layout.setVerticalSpacing(8)
        ratings_layout.setContentsMargins(12, 16, 12, 12)

        self.discipline_rating = QSpinBox()
        self.discipline_rating.setRange(1, 10)
        self.discipline_rating.setValue(5)
        self.discipline_rating.setObjectName("ratingInput")

        self.confidence_rating = QSpinBox()
        self.confidence_rating.setRange(1, 10)
        self.confidence_rating.setValue(5)
        self.confidence_rating.setObjectName("ratingInput")

        self.risk_rating = QSpinBox()
        self.risk_rating.setRange(1, 10)
        self.risk_rating.setValue(5)
        self.risk_rating.setObjectName("ratingInput")

        ratings_layout.addWidget(QLabel("Discipline"), 0, 0)
        ratings_layout.addWidget(self.discipline_rating, 0, 1)
        ratings_layout.addWidget(QLabel("Confidence"), 0, 2)
        ratings_layout.addWidget(self.confidence_rating, 0, 3)
        ratings_layout.addWidget(QLabel("Risk Mgmt"), 0, 4)
        ratings_layout.addWidget(self.risk_rating, 0, 5)

        layout.addWidget(ratings_group)

        # Action buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        self.save_btn = QPushButton("SAVE ENTRY")
        self.save_btn.setObjectName("primaryButton")

        self.cancel_btn = QPushButton("CANCEL")
        self.cancel_btn.setObjectName("ghostButton")

        button_row.addStretch()
        button_row.addWidget(self.cancel_btn)
        button_row.addWidget(self.save_btn)

        layout.addLayout(button_row)

        return container

    def _create_checkbox_group(self, title: str, items: List[str]) -> QGroupBox:
        group = QGroupBox(title)
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)
        layout.setContentsMargins(12, 16, 12, 12)

        checkboxes = []
        for idx, item in enumerate(items):
            checkbox = QCheckBox(item)
            checkbox.setObjectName("checkboxItem")
            checkboxes.append(checkbox)
            layout.addWidget(checkbox, idx // 2, idx % 2)

        group.checkboxes = checkboxes
        return group

    def _setup_read_timer(self):
        self._read_timer = QTimer(self)
        self._read_timer.timeout.connect(self._tick_read_timer)

    def _tick_read_timer(self):
        if self._read_time_remaining > 0:
            self._read_time_remaining -= 1
            mins, secs = divmod(self._read_time_remaining, 60)
            self.read_timer_label.setText(f"Review time: {mins:02d}:{secs:02d}")
        else:
            self._read_timer.stop()
            self.read_timer_label.setText("Review complete")

    def _connect_signals(self):
        self.read_btn.clicked.connect(lambda: self._set_mode("read"))
        self.write_btn.clicked.connect(lambda: self._reset_form())
        self.edit_btn.clicked.connect(lambda: self._handle_edit_mode())
        self.close_btn.clicked.connect(self._handle_close)

        self.search_input.textChanged.connect(self._filter_entries)
        self.range_filter.currentIndexChanged.connect(self._filter_entries)
        self.entry_list.currentItemChanged.connect(self._display_entry_details)

        self.save_btn.clicked.connect(self._save_entry)
        self.cancel_btn.clicked.connect(lambda: self._set_mode("read"))
        self.delete_btn.clicked.connect(self._delete_entry)

    def _set_mode(self, mode: str):
        if mode == "read":
            self.mode_stack.setCurrentIndex(0)
            self.read_btn.setChecked(True)
            if self._enforce_read_time:
                self._read_time_remaining = self._read_time_seconds
                self._read_timer.start(1000)
            else:
                self.read_timer_label.setText("")
        elif mode in ("write", "edit"):
            self.mode_stack.setCurrentIndex(1)
            if mode == "write":
                self.write_btn.setChecked(True)
            else:
                self.edit_btn.setChecked(True)
            self._read_timer.stop()
            self.read_timer_label.setText("")

    def _handle_edit_mode(self):
        entry = self._get_selected_entry()
        if not entry:
            QMessageBox.information(self, "Select Entry", "Select an entry to edit.")
            return
        self._populate_form(entry)
        self._set_mode("edit")

    def _load_entries(self):
        self.entries = self.config_manager.load_journal_entries()
        self._filter_entries()

    def _filter_entries(self):
        self.entry_list.clear()
        query = self.search_input.text().lower()
        range_filter = self.range_filter.currentText()

        now = datetime.now()
        range_map = {
            "Today": 0,
            "7 Days": 7,
            "30 Days": 30,
        }

        for entry in sorted(self.entries, key=lambda e: e.get("updated_at", ""), reverse=True):
            if range_filter in range_map:
                entry_date_str = entry.get("trade_date")
                if entry_date_str:
                    try:
                        entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d")
                        days_diff = (now - entry_date).days
                        if days_diff > range_map[range_filter]:
                            continue
                    except ValueError:
                        continue

            title = entry.get("title", "")
            tags = entry.get("tags", "")
            emotions = ", ".join(entry.get("emotions", []))
            mistakes = ", ".join(entry.get("mistakes", []))

            searchable = f"{title} {tags} {emotions} {mistakes}".lower()
            if query and query not in searchable:
                continue

            item = QListWidgetItem(f"{entry.get('trade_date', 'N/A')} - {title}")
            item.setData(Qt.UserRole, entry)
            self.entry_list.addItem(item)

    def _get_selected_entry(self) -> Optional[Dict[str, Any]]:
        current_item = self.entry_list.currentItem()
        return current_item.data(Qt.UserRole) if current_item else None

    def _display_entry_details(self):
        entry = self._get_selected_entry()
        if not entry:
            self.details_title.setText("Select an entry")
            self.details_body.clear()
            return

        self.details_title.setText(entry.get("title", "Untitled"))

        html_parts = [
            f"<div style='line-height: 1.6;'>",
            f"<p style='margin-bottom: 8px;'><b style='color: #29C7C9;'>Date:</b> {entry.get('trade_date', 'N/A')}</p>",
            f"<p style='margin-bottom: 8px;'><b style='color: #29C7C9;'>Session:</b> {entry.get('session_type', 'N/A')}</p>",
        ]

        if entry.get("tags"):
            html_parts.append(
                f"<p style='margin-bottom: 12px;'><b style='color: #29C7C9;'>Tags:</b> {entry.get('tags')}</p>")

        # Display consolidated fields
        plan_context = " ".join(filter(None, [
            entry.get("market_context", ""),
            entry.get("trade_plan", ""),
            entry.get("plan_context", "")
        ])).strip()

        if plan_context:
            html_parts.append(
                f"<p style='margin-top: 12px; margin-bottom: 4px;'><b style='color: #A9B1C3;'>Plan & Context:</b></p>")
            html_parts.append(f"<p style='margin-bottom: 12px;'>{plan_context}</p>")

        if entry.get("execution_notes"):
            html_parts.append(f"<p style='margin-bottom: 4px;'><b style='color: #A9B1C3;'>Execution & Results:</b></p>")
            html_parts.append(f"<p style='margin-bottom: 12px;'>{entry.get('execution_notes')}</p>")

        lessons_actions = " ".join(filter(None, [
            entry.get("lessons_learned", ""),
            entry.get("next_actions", ""),
            entry.get("lessons_actions", "")
        ])).strip()

        if lessons_actions:
            html_parts.append(f"<p style='margin-bottom: 4px;'><b style='color: #A9B1C3;'>Lessons & Actions:</b></p>")
            html_parts.append(f"<p style='margin-bottom: 12px;'>{lessons_actions}</p>")

        emotions = entry.get("emotions", [])
        if emotions:
            html_parts.append(f"<p style='margin-bottom: 4px;'><b style='color: #A9B1C3;'>Emotions:</b></p>")
            html_parts.append(f"<p style='margin-bottom: 12px;'>{', '.join(emotions)}</p>")

        mistakes = entry.get("mistakes", [])
        if mistakes:
            html_parts.append(f"<p style='margin-bottom: 4px;'><b style='color: #A9B1C3;'>Mistakes:</b></p>")
            html_parts.append(f"<p style='margin-bottom: 12px;'>{', '.join(mistakes)}</p>")

        ratings = entry.get("ratings", {})
        if ratings:
            html_parts.append(f"<p style='margin-bottom: 4px;'><b style='color: #A9B1C3;'>Ratings:</b></p>")
            html_parts.append(f"<p>Discipline: {ratings.get('discipline', 'N/A')}/10 | "
                              f"Confidence: {ratings.get('confidence', 'N/A')}/10 | "
                              f"Risk: {ratings.get('risk', 'N/A')}/10</p>")

        html_parts.append("</div>")
        self.details_body.setHtml("".join(html_parts))

    def _collect_form_data(self) -> Dict[str, Any]:
        return {
            "trade_date": self.entry_date.date().toString("yyyy-MM-dd"),
            "session_type": self.session_type.currentText(),
            "title": self.entry_title.text().strip(),
            "tags": self.entry_tags.text().strip(),
            "plan_context": self.plan_context.toPlainText().strip(),
            "execution_notes": self.execution_notes.toPlainText().strip(),
            "lessons_actions": self.lessons_actions.toPlainText().strip(),
            "emotions": self._collect_checks(self.emotion_checks),
            "mistakes": self._collect_checks(self.mistake_checks),
            "ratings": {
                "discipline": self.discipline_rating.value(),
                "confidence": self.confidence_rating.value(),
                "risk": self.risk_rating.value(),
            }
        }

    def _collect_checks(self, container: QWidget) -> List[str]:
        values = []
        for checkbox in getattr(container, "checkboxes", []):
            if checkbox.isChecked():
                values.append(checkbox.text())
        return values

    def _save_entry(self):
        data = self._collect_form_data()
        if not data.get("title"):
            QMessageBox.warning(self, "Missing Title", "Add a title for this journal entry.")
            return

        now = datetime.now().isoformat(timespec="seconds")
        if self._current_entry_id:
            for entry in self.entries:
                if entry.get("id") == self._current_entry_id:
                    entry.update(data)
                    entry["updated_at"] = now
                    break
        else:
            data.update({
                "id": str(uuid4()),
                "created_at": now,
                "updated_at": now,
            })
            self.entries.append(data)

        self.config_manager.save_journal_entries(self.entries)
        self._current_entry_id = None
        self._reset_form(clear_only=True)
        self._load_entries()
        self._set_mode("read")

    def _delete_entry(self):
        entry = self._get_selected_entry()
        if not entry:
            QMessageBox.information(self, "Select Entry", "Select an entry to delete.")
            return
        confirm = QMessageBox.question(
            self,
            "Delete Entry",
            "Are you sure you want to delete this journal entry?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.entries = [e for e in self.entries if e.get("id") != entry.get("id")]
        self.config_manager.save_journal_entries(self.entries)
        self._load_entries()

    def _populate_form(self, entry: Dict[str, Any]):
        self._current_entry_id = entry.get("id")
        trade_date = entry.get("trade_date")
        if trade_date:
            try:
                self.entry_date.setDate(datetime.strptime(trade_date, "%Y-%m-%d").date())
            except ValueError:
                self.entry_date.setDate(date.today())
        else:
            self.entry_date.setDate(date.today())
        self.session_type.setCurrentText(entry.get("session_type", "Intraday"))
        self.entry_title.setText(entry.get("title", ""))
        self.entry_tags.setText(entry.get("tags", ""))

        # Handle consolidated fields with backward compatibility
        plan_context = entry.get("plan_context") or " ".join(filter(None, [
            entry.get("market_context", ""),
            entry.get("trade_plan", "")
        ]))
        self.plan_context.setPlainText(plan_context)

        self.execution_notes.setPlainText(entry.get("execution_notes", ""))

        lessons_actions = entry.get("lessons_actions") or " ".join(filter(None, [
            entry.get("lessons_learned", ""),
            entry.get("next_actions", "")
        ]))
        self.lessons_actions.setPlainText(lessons_actions)

        self._set_checks(self.emotion_checks, entry.get("emotions", []))
        self._set_checks(self.mistake_checks, entry.get("mistakes", []))

        ratings = entry.get("ratings", {})
        self.discipline_rating.setValue(ratings.get("discipline", 5))
        self.confidence_rating.setValue(ratings.get("confidence", 5))
        self.risk_rating.setValue(ratings.get("risk", 5))

    def _set_checks(self, container: QWidget, values: List[str]):
        for checkbox in getattr(container, "checkboxes", []):
            checkbox.setChecked(checkbox.text() in values)

    def _reset_form(self, clear_only: bool = False):
        self._current_entry_id = None
        self.entry_date.setDate(date.today())
        self.session_type.setCurrentIndex(1)
        self.entry_title.clear()
        self.entry_tags.clear()
        self.plan_context.clear()
        self.execution_notes.clear()
        self.lessons_actions.clear()
        self._set_checks(self.emotion_checks, [])
        self._set_checks(self.mistake_checks, [])
        self.discipline_rating.setValue(5)
        self.confidence_rating.setValue(5)
        self.risk_rating.setValue(5)
        if not clear_only:
            self._set_mode("write")

    def _handle_close(self):
        self.close()

    def _apply_styles(self):
        self.setStyleSheet("""
            #mainContainer {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #0F141B, stop:1 #0A0E14);
                border: 1px solid #1E2633;
                border-radius: 12px;
            }

            #dialogTitle {
                color: #E5E9F0;
                font-size: 17px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }

            #modeButton {
                background: #1A2028;
                color: #9BA5B4;
                border: 1px solid #2A3340;
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: 600;
                font-size: 12px;
                letter-spacing: 0.3px;
            }

            #modeButton:hover {
                background: #1F2730;
                border-color: #3A4654;
            }

            #modeButton:checked {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #263043, stop:1 #1E2838);
                border-color: #29C7C9;
                color: #E9F5F5;
            }

            #timerLabel {
                color: #7A8BA8;
                font-size: 11px;
                font-weight: 500;
            }

            #closeButton {
                background: transparent;
                color: #8A95A8;
                border: none;
                font-size: 18px;
                padding: 4px 8px;
            }

            #closeButton:hover {
                color: #F85149;
            }

            #searchInput, #rangeFilter, #titleInput, #tagsInput, #sessionInput, #dateInput,
            #ratingInput {
                background: #151B24;
                color: #E5E9F0;
                border: 1px solid #2A3340;
                border-radius: 6px;
                padding: 7px 10px;
                font-size: 13px;
                selection-background-color: #29C7C9;
                selection-color: #0B1018;
            }

            #searchInput:focus, #titleInput:focus, #tagsInput:focus {
                border-color: #29C7C9;
                background: #171E28;
            }

            #textInput {
                background: #151B24;
                color: #E5E9F0;
                border: 1px solid #2A3340;
                border-radius: 6px;
                padding: 0px;
                font-size: 13px;
                line-height: 1.5;
                selection-background-color: #29C7C9;
                selection-color: #0B1018;
            }

            #textInput:focus {
                border-color: #29C7C9;
                background: #171E28;
            }

            QTextEdit {
                background: #151B24;
                color: #E5E9F0;
                border: 1px solid #2A3340;
                border-radius: 6px;
                font-size: 13px;
                selection-background-color: #29C7C9;
                selection-color: #0B1018;
            }

            #entryList {
                background: #0D1117;
                color: #D9DEE7;
                border: 1px solid #1D2632;
                border-radius: 8px;
                padding: 4px;
                font-size: 13px;
            }

            #entryList::item {
                padding: 8px;
                border-radius: 4px;
            }

            #entryList::item:selected {
                background: #1E2836;
                color: #29C7C9;
            }

            #entryList::item:hover {
                background: #151C26;
            }

            #detailsTitle {
                color: #E5E9F0;
                font-size: 15px;
                font-weight: 700;
            }

            #detailsBody {
                background: #0D1117;
                color: #D0D6E0;
                border: 1px solid #1E2633;
                border-radius: 8px;
                font-size: 13px;
                line-height: 1.6;
            }

            QGroupBox {
                border: 1px solid #222B38;
                border-radius: 8px;
                margin-top: 8px;
                color: #A9B1C3;
                font-weight: 600;
                font-size: 12px;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }

            #fieldLabel {
                color: #8A95A8;
                font-size: 11px;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }

            #checkboxItem {
                color: #B8C0CC;
                font-size: 12px;
                spacing: 6px;
            }

            #checkboxItem::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #2A3340;
                border-radius: 3px;
                background: #151B24;
            }

            #checkboxItem::indicator:checked {
                background: #29C7C9;
                border-color: #29C7C9;
            }

            #primaryButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #29C7C9, stop:1 #23B1B3);
                color: #0B1018;
                border: none;
                border-radius: 6px;
                padding: 9px 20px;
                font-weight: 700;
                font-size: 12px;
                letter-spacing: 0.3px;
            }

            #primaryButton:hover {
                background: #2DD4D6;
            }

            #ghostButton {
                background: transparent;
                border: 1px solid #2E3A4C;
                color: #B8C0CC;
                border-radius: 6px;
                padding: 9px 20px;
                font-weight: 600;
                font-size: 12px;
            }

            #ghostButton:hover {
                background: #1A2028;
                border-color: #3A4654;
            }

            #dangerOutlineButton {
                background: transparent;
                border: 1px solid rgba(248, 81, 73, 0.5);
                color: #F85149;
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: 600;
                font-size: 12px;
            }

            #dangerOutlineButton:hover {
                background: rgba(248, 81, 73, 0.12);
                border-color: #F85149;
            }

            QLabel {
                color: #9BA5B4;
                font-size: 12px;
            }

            QSpinBox {
                background: #151B24;
                color: #E5E9F0;
                border: 1px solid #2A3340;
                border-radius: 6px;
                padding: 6px;
                font-size: 13px;
            }

            QSpinBox::up-button, QSpinBox::down-button {
                background: #1E2633;
                border: none;
                width: 16px;
            }

            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background: #29C7C9;
            }

            QComboBox::drop-down {
                border: none;
                width: 20px;
            }

            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #8A95A8;
            }
        """)