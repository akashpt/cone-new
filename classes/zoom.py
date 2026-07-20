from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QGestureEvent, QPinchGesture
# Optional Pillow fallback for loading images if Qt/Skia can't parse them
try:
    from PIL import Image
except Exception:
    Image = None


class ImageViewerDialog(QtWidgets.QDialog):
    def __init__(self, image_path, parent=None):
        if parent is not None and not isinstance(parent, QtWidgets.QWidget):
            parent = None
        super().__init__(parent)
        self.setWindowTitle("Defect Image Viewer")
        self.resize(900, 700)

        self.scale_factor = 1.0

        # ---------- Main layout ----------
        main_layout = QtWidgets.QVBoxLayout(self)

        # ---------- CLOSE BUTTON (RED, BOLD) ----------
        close_btn = QtWidgets.QPushButton("✖ CLOSE")
        close_btn.setFixedWidth(120)
        close_btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #5a1929;
                color: white;
                font-weight: bold;
                padding: 6px 10px;
                border-radius: 6px;
                font-size: 15px;
            }
            QPushButton:hover {
                background-color: #5a191f;
            }
        """)
        close_btn.clicked.connect(self.close)

        # Align button right
        top_layout = QtWidgets.QHBoxLayout()
        top_layout.addStretch(1)
        top_layout.addWidget(close_btn)
        main_layout.addLayout(top_layout)

        # ---------- Scroll area (panning) ----------
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)

        self.label = QtWidgets.QLabel()
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        self.scroll_area.setWidget(self.label)

        main_layout.addWidget(self.scroll_area)

        # ---------- Control buttons (manual zoom) ----------
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch(1)

        self.btn_zoom_in = QtWidgets.QPushButton("Zoom +")
        self.btn_zoom_out = QtWidgets.QPushButton("Zoom -")
        self.btn_reset = QtWidgets.QPushButton("Reset 100%")
        self.btn_fit = QtWidgets.QPushButton("Fit to Window")

        btn_layout.addWidget(self.btn_zoom_in)
        btn_layout.addWidget(self.btn_zoom_out)
        btn_layout.addWidget(self.btn_reset)
        btn_layout.addWidget(self.btn_fit)

        btn_layout.addStretch(1)
        main_layout.addLayout(btn_layout)

        # ---------- Connections ----------
        self.btn_zoom_in.clicked.connect(lambda: self.zoom(1.1))
        self.btn_zoom_out.clicked.connect(lambda: self.zoom(0.9))
        self.btn_reset.clicked.connect(self.reset_zoom)
        self.btn_fit.clicked.connect(self.fit_to_window)

        # ---------- Load image ----------
        self.original_pixmap = QtGui.QPixmap(image_path)
        if self.original_pixmap.isNull():
            # Qt failed to load (skia unsupported) — try Pillow fallback
            if Image is not None:
                try:
                    im = Image.open(image_path).convert("RGBA")
                    w, h = im.size
                    data = im.tobytes("raw", "RGBA")
                    qimg = QtGui.QImage(data, w, h, QtGui.QImage.Format_RGBA8888)
                    self.original_pixmap = QtGui.QPixmap.fromImage(qimg)
                except Exception as e:
                    print("Image load fallback failed:", e)

        if self.original_pixmap.isNull():
            self.label.setText("Image not found or unsupported format")
        else:
            self.fit_to_window()

        # Enable pinch gesture for touch zoom
        self.grabGesture(QtCore.Qt.PinchGesture)

    # ---------- Core zoom apply ----------
    def apply_zoom(self):
        if self.original_pixmap.isNull():
            return

        w = int(self.original_pixmap.width() * self.scale_factor)
        h = int(self.original_pixmap.height() * self.scale_factor)

        # avoid too small
        if w < 50 or h < 50:
            return

        scaled = self.original_pixmap.scaled(
            w, h,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation
        )
        self.label.setPixmap(scaled)

    # ---------- Generic zoom helper ----------
    def zoom(self, factor: float):
        self.scale_factor *= factor
        self.scale_factor = max(0.2, min(5.0, self.scale_factor))  # clamp
        self.apply_zoom()

    # ---------- Reset to 100% ----------
    def reset_zoom(self):
        self.scale_factor = 1.0
        self.apply_zoom()

    # ---------- Fit image to scroll area window ----------
    def fit_to_window(self):
        if self.original_pixmap.isNull():
            return

        vp_size = self.scroll_area.viewport().size()
        if vp_size.width() <= 0 or vp_size.height() <= 0:
            return

        img_w = self.original_pixmap.width()
        img_h = self.original_pixmap.height()

        factor_w = vp_size.width() / img_w
        factor_h = vp_size.height() / img_h

        self.scale_factor = min(factor_w, factor_h)
        self.scale_factor = max(0.2, min(5.0, self.scale_factor))
        self.apply_zoom()

    # ---------- Mouse wheel zoom ----------
    def wheelEvent(self, event: QtGui.QWheelEvent):
        angle = event.angleDelta().y()
        factor = 1.1 if angle > 0 else 0.9
        self.zoom(factor)

    # ---------- Gesture routing ----------
    def event(self, e: QtCore.QEvent):
        if e.type() == QtCore.QEvent.Gesture:
            return self.gestureEvent(e)
        return super().event(e)

    # ---------- Pinch gesture ----------
    def gestureEvent(self, event: QGestureEvent):
        pinch = event.gesture(QtCore.Qt.PinchGesture)
        if pinch:
            if pinch.changeFlags() & QPinchGesture.ScaleFactorChanged:
                factor = pinch.scaleFactor()
                self.zoom(factor)
        return True
