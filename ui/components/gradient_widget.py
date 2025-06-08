# -*- coding: utf-8 -*-
"""
Файл: ui/components/gradient_widget.py
Описание: Виджет с градиентным фоном (тема по умолчанию).
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QLinearGradient, QColor

class GradientBackgroundWidget(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(self.rect().topLeft(), self.rect().bottomRight())
        gradient.setColorAt(0, QColor(20, 0, 30))
        gradient.setColorAt(1, QColor(90, 0, 120))
        painter.fillRect(self.rect(), gradient)
