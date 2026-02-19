from PySide6.QtCore import (
    Qt,
    QPropertyAnimation,
    QEasingCurve,
    QRectF,
    Property,
    Signal
)
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath
from PySide6.QtWidgets import QWidget, QApplication
import sys


class ImperiumCheckBox(QWidget):
    """
    Institutional square checkbox.
    Fully QCheckBox-compatible.
    """

    toggled = Signal(bool)
    clicked = Signal(bool)
    stateChanged = Signal(int)

    def __init__(self, text="", parent=None):
        super().__init__(parent)

        self._checked = False
        self._text = text
        self._progress = 0.0

        self.setFixedHeight(24)
        self.setMinimumWidth(160)
        self.setCursor(Qt.PointingHandCursor)

        self._animation = QPropertyAnimation(self, b"progress")
        self._animation.setDuration(120)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

    # -----------------------------------
    # Qt animated property
    # -----------------------------------
    def getProgress(self):
        return self._progress

    def setProgress(self, value):
        self._progress = value
        self.update()

    progress = Property(float, getProgress, setProgress)

    # -----------------------------------
    # API Compatibility
    # -----------------------------------
    def isChecked(self):
        return self._checked

    def setChecked(self, state: bool):
        if self._checked == state:
            return

        self._checked = state

        # Animate
        self._animation.stop()
        self._animation.setStartValue(0.0 if state else 1.0)
        self._animation.setEndValue(1.0 if state else 0.0)
        self._animation.start()

        # Emit signals (QCheckBox compatible)
        self.toggled.emit(self._checked)
        self.stateChanged.emit(
            Qt.CheckState.Checked if self._checked else Qt.CheckState.Unchecked
        )

    def toggle(self):
        self.setChecked(not self._checked)
        self.clicked.emit(self._checked)

    def text(self):
        return self._text

    def setText(self, text: str):
        self._text = text
        self.update()

    # -----------------------------------
    # Paint
    # -----------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        box_size = 16
        box_rect = QRectF(2, 4, box_size, box_size)

        if self._checked:
            fill_color = QColor("#29C7C9")
            border_color = QColor("#29C7C9")
        else:
            fill_color = QColor("#1E2533")
            border_color = QColor("#3A4458")

        painter.setBrush(fill_color)
        painter.setPen(QPen(border_color, 1.5))
        painter.drawRoundedRect(box_rect, 3, 3)

        # Tick animation
        if self._progress > 0:
            pen = QPen(QColor("#0F1117"), 2)
            painter.setPen(pen)

            path = QPainterPath()
            path.moveTo(6, 12)
            path.lineTo(9, 15)
            path.lineTo(15, 7)

            painter.setClipRect(QRectF(2, 4, box_size * self._progress, box_size))
            painter.drawPath(path)

        painter.setClipping(False)
        painter.setPen(QPen(QColor("#E0E0E0")))
        painter.drawText(24, 16, self._text)

    # -----------------------------------
    # Mouse
    # -----------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle()


# -----------------------------------
# Run preview
# -----------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)

    demo = QWidget()
    demo.setStyleSheet("background-color: #161A25;")
    demo.resize(320, 180)

    cb1 = ImperiumCheckBox("Automate", demo)
    cb1.move(40, 40)

    cb2 = ImperiumCheckBox("Use Trailing SL", demo)
    cb2.move(40, 80)

    # Signal test
    cb1.toggled.connect(lambda v: print("Automate toggled:", v))
    cb2.stateChanged.connect(lambda v: print("State changed:", v))

    demo.show()
    sys.exit(app.exec())
