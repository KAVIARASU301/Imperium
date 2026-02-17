from PySide6.QtWidgets import QMessageBox, QLabel
from PySide6.QtCore import Qt


def show_message(
    parent,
    title: str,
    message: str,
    *,
    icon=QMessageBox.Information,
    buttons=QMessageBox.Ok,
    min_width: int = 460,
    min_height: int = 260,
    align: Qt.AlignmentFlag = Qt.AlignCenter
):
    msg = QMessageBox(parent)
    msg.setWindowTitle(title)
    msg.setText(message)
    msg.setIcon(icon)
    msg.setStandardButtons(buttons)
    msg.setWindowModality(Qt.WindowModal)

    # ---- PROFESSIONAL MINIMUM FOOTPRINT ----
    msg.setMinimumWidth(min_width)
    msg.setMinimumHeight(min_height)
    msg.resize(min_width, min_height)

    # ---- Apply style ----
    msg.setStyleSheet("""
        QMessageBox {
            background-image: url("assets/textures/texture.png");
            background-color: #161A25;
            color: #E0E0E0;
            border: 1px solid #3A4458;
            border-radius: 6px;
        }

        QMessageBox QLabel {
            color: #E0E0E0;
            font-size: 13px;
            background: transparent;
        }

        QMessageBox QPushButton {
            background-color: #212635;
            color: #E0E0E0;
            border: 1px solid #3A4458;
            border-radius: 6px;
            padding: 8px 18px;
            min-width: 80px;
            font-weight: 500;
        }

        QMessageBox QPushButton:hover {
            background-color: #29C7C9;
            color: #161A25;
            border-color: #29C7C9;
        }

        QMessageBox QPushButton:pressed {
            background-color: #1f8a8c;
        }
    """)

    # ---- FINALIZE LABEL BEHAVIOR ----
    for label in msg.findChildren(QLabel):
        label.setAlignment(align | Qt.AlignVCenter)
        label.setWordWrap(True)
        label.setMaximumWidth(min_width - 56)

    return msg.exec()
