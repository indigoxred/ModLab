"""Run filesystem/helper work off Qt; deliver results only on the owning UI thread."""
from concurrent.futures import Future
from threading import Thread


def run(parent, operation, finished):
    from PyQt6.QtCore import QTimer
    future = Future()
    def worker():
        try: future.set_result(operation())
        except BaseException as error: future.set_exception(error)
    timer = QTimer(parent)
    def poll():
        if not future.done(): return
        timer.stop(); timer.deleteLater()
        try: result = future.result()
        except BaseException as error: finished(None, str(error)); return
        finished(result, None)
    timer.timeout.connect(poll)
    timer.start(100)
    Thread(target=worker, name='ModLab preparation', daemon=True).start()
