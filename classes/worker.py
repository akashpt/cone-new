from PyQt5.QtCore import QThread, pyqtSignal


class ProcessWorker(QThread):
    openImage = pyqtSignal(str)
    closeImage = pyqtSignal()

    def __init__(self, process):
        super().__init__()
        self.process = process

        self.process.open_image_request_cb = self.openImage.emit
        self.process.close_image_request_cb = self.closeImage.emit

    def run(self):
        try:
            print("🧪 Starting light-only inspection test")

            result, result_image, record_id = (
                self.process.light_inspection_sequence()
            )

            print(
                f"✅ Light inspection completed: "
                f"result={result}"
            )

        except Exception as e:
            print(f"❌ ProcessWorker error: {e}")