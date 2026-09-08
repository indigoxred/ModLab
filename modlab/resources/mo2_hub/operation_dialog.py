"""Keep an explicit operation visible until asynchronous publication completes."""
from PyQt6.QtWidgets import QDialog


class OperationDialog(QDialog):
    def reject(self):
        if not getattr(self, 'operation_running', False):
            super().reject()

    def accept(self):
        if not getattr(self, 'operation_running', False):
            super().accept()

    def closeEvent(self, event):
        if getattr(self, 'operation_running', False):
            event.ignore()
        else:
            super().closeEvent(event)
