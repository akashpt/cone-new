from PyQt5.QtCore import QThread, pyqtSignal

class ProcessWorker(QThread):
    finished = pyqtSignal()
    openImage = pyqtSignal(str)
    closeImage = pyqtSignal() 

    def __init__(self, process):
        super().__init__()
        self.process = process
        self.process.open_image_request_cb = self.openImage.emit
        self.process.close_image_request_cb = self.closeImage.emit

    def run(self):
        # This runs in a separate thread
        
        try:
            self.process.main_loop()
        finally:
            # Emit when the loop exits, so GUI can clean up
            self.finished.emit()
