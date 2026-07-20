# bridge.py
import base64
import cv2
from PyQt5.QtCore import QObject, pyqtSlot, pyqtSignal, QTimer, QUrl

#modified by Gokul

import os

from classes.pdf_compress import compress_pdf

try:
    from classes import mvsdk
    MVSDK_AVAILABLE = True
except ImportError:
    MVSDK_AVAILABLE = False
    print("MindVision SDK not available. Camera functions will be disabled.")
import time
from classes.main import Process
import json
from classes.Plc_connection import *
from pathlib import Path
from classes.zoom import ImageViewerDialog
from paths import DB_PATH, GOOD_TIP, SETTINGS_JSON, APP_DIR, PREDICTION
import numpy as np
from classes.database import execute,fetch_all,fetch_one

def file_url_to_path(url: str) -> str:
    """Convert file:// URL to system file path"""
    if not url:
        return ""
    
    url = url.strip()
    
    # Remove file:// schemes - handle 3 or 4 slashes
    if url.startswith("file:///"):
        path = url[8:]  # Remove "file:///" (8 chars)
    elif url.startswith("file://"):
        path = url[7:]  # Remove "file://" (7 chars)
    else:
        path = url
    
    # Ensure path starts with / for absolute paths
    if path and not path.startswith("/"):
        # Relative path - make it absolute using DB_PATH directory
        db_dir = os.path.dirname(DB_PATH)
        path = os.path.join(db_dir, path)
    
    # Normalize the path (removes .., double slashes, etc)
    return os.path.abspath(path)

def hhmm_to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)

def to_intervals(start_hhmm: str, end_hhmm: str):
    """
    Convert a shift time to 1 or 2 intervals in minutes [start,end)
    Handles midnight-crossing shifts.
    Example:
      06:00-14:00 -> [(360, 840)]
      22:00-06:00 -> [(1320, 1440), (0, 360)]
    """
    s = hhmm_to_min(start_hhmm)
    e = hhmm_to_min(end_hhmm)

    if s == e:
        return None  # invalid (0 length or 24h)
    if s < e:
        return [(s, e)]
    # crosses midnight
    return [(s, 1440), (0, e)]

def overlaps(intervals_a, intervals_b) -> bool:
    for a1, a2 in intervals_a:
        for b1, b2 in intervals_b:
            if max(a1, b1) < min(a2, b2):   # real overlap
                return True
    return False


class Bridge(QObject):
    # Sends base64-encoded JPEG frame to JS
    frame_signal = pyqtSignal(str)
    frame_cone = pyqtSignal(str)
    channelChanged = pyqtSignal(str)
    colorList = pyqtSignal(list)
    countList = pyqtSignal(list)
    pdfReady = pyqtSignal(str)   # emits file:///... when pdf is ready
    pdfError = pyqtSignal(str)  # emits error message if failed


    def __init__(self, app_ref=None, tips_dir=GOOD_TIP):
        super().__init__()
        self.app_ref = app_ref

        #modified by Gokul
        self._active_shift_id = None

        # Basler camera objects
        self.camera = None
        self.converter = None
        self.latest_frame = None
        
        self.cap = None
        self.monoCamera = False
        self.pFrameBuffer = None
        self.FrameBufferSize = 0

        # Timer for grabbing frames
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._grab_and_send_frame)
        self._shift_report_sent = set()
        self.is_running = False
        self.process = Process(
            set_exposure_cb=self._set_exposure_from_process,
            capture_frame_cb=self._capture_frame_for_process,
            send_cone_images_cb=self.send_cone_images,
            )

        self.process_thread = None 
        self.defect_dialog = None
        # self.settings_path = settings_path
        self.tips_dir = tips_dir
        self._data = self._load()
        
        #modified by Gokul
        self.shift_timer = QTimer(self)
        self.shift_timer.timeout.connect(self._check_shift_end)
        self.shift_timer.start(60_000)  # 60 seconds

        # self.loops()

    from PyQt5.QtCore import QRunnable, QThreadPool

    class ShiftMailJob(QRunnable):
        def __init__(self, bridge, shift_id, name, start_dt, end_dt):
            super().__init__()
            self.bridge = bridge
            self.shift_id = shift_id
            self.name = name
            self.start_dt = start_dt
            self.end_dt = end_dt

        def run(self):
            try:
                # 1) Generate PDF
                pdf_url = self.bridge.reports_export_pdf(
                    date_from=self.start_dt.strftime("%Y-%m-%d"),
                    date_to=self.end_dt.strftime("%Y-%m-%d"),
                    shift_id=str(self.shift_id),
                    color_id="",
                    count_id="",
                    mode="summary_only"
                )
                if not pdf_url:
                    print("❌ PDF generation failed:", self.name)
                    return

                pdf_path = file_url_to_path(pdf_url)

                # 2) Compress
                try:
                    out = pdf_path.replace(".pdf", "_compressed.pdf")
                    compress_pdf(pdf_path, out, quality="screen")
                    final_pdf = out if os.path.exists(out) else pdf_path
                except Exception as e:
                    print("⚠️ compress failed, using original:", e)
                    final_pdf = pdf_path

                # 3) Send mail
                from classes.mailer import send_shift_mail
                send_shift_mail(self.name, self.end_dt, final_pdf)
                print("✅ Shift mail sent:", self.name)

                # ✅ 4) LOG to DB (prevents duplicates after restart)
                # try:
                #     from datetime import datetime
                #     shift_end_key = self.end_dt.isoformat(timespec="minutes")
                #     execute("""
                #         INSERT INTO shift_report_log (shift_id, shift_end, sent_at)
                #         VALUES (?, ?, ?)
                #     """, (
                #         int(self.shift_id),
                #         shift_end_key,
                #         datetime.now().isoformat(timespec="seconds")
                #     ))
                #     print("✅ shift_report_log inserted:", self.shift_id, shift_end_key)
                # except Exception as e:
                #     print("⚠️ shift_report_log insert failed:", e)

            except Exception as e:
                print("❌ ShiftMailJob error:", e)
 

    def send_threshold_values(self, top_value, bottom_value):
        payload = {
            "top": top_value,
            "bottom": bottom_value
        }
        self.thresholdSignal.emit(json.dumps(payload))


    def all_channels_off(self):
        """
        Turn OFF all outputs on B1 and B2 when UI/Controller closes.
        """
        try:
            conveyor_off()
            unlock_cone()
            piston_up_stop()
            piston_down_stop()
            piston_rotate_stop()
            uv_light_off()
            white_light_off()
            green_light_off()
            red_light_off()
            buzzer_off()

            print("[Bridge] All PLC outputs OFF")
        except Exception as e:
            print("all_channels_off error:", e)

    def _encode_bgr_to_base64(self, bgr_img):
        """Encode cv2 BGR image → JPEG base64 string."""
        ok, buf = cv2.imencode(".jpg", bgr_img)
        if not ok:
            return ""
        return base64.b64encode(buf).decode("ascii")
    
    def _is_valid_img(self,img):
        
        return (
            img is not None
            and isinstance(img, np.ndarray)
            and img.size > 0
            and img.shape[0] > 0
            and img.shape[1] > 0
        )


    @pyqtSlot()
    def loadColors(self):
        if not os.path.exists(PREDICTION):
            self.colorList.emit([])
            return

        colors = [
            d for d in os.listdir(PREDICTION)
            if os.path.isdir(os.path.join(PREDICTION, d))
        ]
        self.colorList.emit(colors)

    @pyqtSlot(str)
    def loadCounts(self, color):
        path = os.path.join(PREDICTION, color)
        if not os.path.exists(path):
            self.countList.emit([])
            return

        counts = [
            d for d in os.listdir(path)
            if os.path.isdir(os.path.join(path, d))
        ]
        self.countList.emit(counts)

    def send_cone_images(self, thread_bgr, tip_bgr, orad):
        
        if not self._is_valid_img(thread_bgr) or not self._is_valid_img(tip_bgr):
            return None
        
        thread_b64 = self._encode_bgr_to_base64(thread_bgr)
        
        tip_b64    = self._encode_bgr_to_base64(tip_bgr)

        payload = {
            "thread": thread_b64,
            "tip": tip_b64,
            "orad": orad
        }
        self.frame_cone.emit(json.dumps(payload))


    def _set_exposure_from_process(self, exposure_us):
        """
        Called from Process thread to change camera exposure.
        """
        if self.camera is None:
            print("⚠️ No camera, cannot set exposure")
            return False
        ok = self.set_exposure(self.camera, exposure_us)
        print(f"[EXPOSURE] Request {exposure_us} us -> ok={ok}")
        return ok
    
    def _capture_frame_for_process(self):
        """
        Called from Process thread to get the latest frame (BGR numpy array).
        Returns None if no frame available yet.
        """
        if self.latest_frame is None:
            print("⚠️ No latest_frame available yet")
            return None

        # return a copy so Process can modify without affecting live view
        return self.latest_frame.copy()

    # --------- CAMERA SETUP ---------
    def set_exposure(self,camera, exposure_us):
        """Best-effort set exposure (µs). Returns True if one method succeeded."""
        if exposure_us is None or camera is None:
            return False
        try:
            mvsdk.CameraSetAeState(camera, 0)   # manual exposure
            mvsdk.CameraSetExposureTime(camera, float(exposure_us))
            return True
        except Exception as e:
            print("set_exposure error:", e)
            return False
            
    def _open_camera(self):
        if not MVSDK_AVAILABLE or mvsdk._sdk is None:
            print("MindVision SDK not available. Cannot open camera.")
            # return False
            return True

        if self.camera is not None:
            return True

        try:
            mvsdk.CameraSdkInit(1)

            DevList = mvsdk.CameraEnumerateDevice()
            if len(DevList) == 0:
                print("❌ No MindVision camera detected")
                # return False
                return True

            DevInfo = DevList[0]

            print("Name:", DevInfo.GetFriendlyName())
            print("Port:", DevInfo.GetPortType())
            print("SN:", DevInfo.GetSn())

            try:
                ip_info = mvsdk.CameraGigeGetIp(DevInfo)
                print("Camera IP:", ip_info[0])
                print("Subnet Mask:", ip_info[1])
                print("Gateway:", ip_info[2])
                print("PC IP:", ip_info[3])
            except Exception:
                pass

            hCamera = mvsdk.CameraInit(DevInfo, -1, -1)

            cap = mvsdk.CameraGetCapability(hCamera)
            monoCamera = (cap.sIspCapacity.bMonoSensor != 0)

            # continuous mode
            mvsdk.CameraSetTriggerMode(hCamera, 0)

            # stable live preview defaults
            mvsdk.CameraSetAeState(hCamera, 0)
            mvsdk.CameraSetExposureTime(hCamera, 30000.0)

            if monoCamera:
                mvsdk.CameraSetIspOutFormat(hCamera, mvsdk.CAMERA_MEDIA_TYPE_MONO8)
            else:
                mvsdk.CameraSetIspOutFormat(hCamera, mvsdk.CAMERA_MEDIA_TYPE_BGR8)

            mvsdk.CameraPlay(hCamera)

            FrameBufferSize = (
                cap.sResolutionRange.iWidthMax *
                cap.sResolutionRange.iHeightMax *
                (1 if monoCamera else 3)
            )
            pFrameBuffer = mvsdk.CameraAlignMalloc(FrameBufferSize, 16)

            self.camera = hCamera
            self.cap = cap
            self.monoCamera = monoCamera
            self.pFrameBuffer = pFrameBuffer
            self.FrameBufferSize = FrameBufferSize
            self.converter = None   # keep field same for compatibility

            print("✅ Camera opened and grabbing")
            return True

        except Exception as e:
            print(f"Camera open error: {e}")
            self.camera = None
            self.cap = None
            self.monoCamera = False
            self.pFrameBuffer = None
            self.FrameBufferSize = 0
            return False
            

    def _close_camera(self):
        try:
            if self.timer.isActive():
                self.timer.stop()

            if self.camera is not None:
                try:
                    mvsdk.CameraStop(self.camera)
                except Exception:
                    pass

                try:
                    mvsdk.CameraUnInit(self.camera)
                except Exception:
                    pass

            if self.pFrameBuffer is not None:
                try:
                    mvsdk.CameraAlignFree(self.pFrameBuffer)
                except Exception:
                    pass

            self.camera = None
            self.cap = None
            self.monoCamera = False
            self.pFrameBuffer = None
            self.FrameBufferSize = 0

            print("📴 Camera closed")
        except Exception as e:
            print(f"Camera close error: {e}")

    # --------- GRAB + SEND ---------
    def _grab_and_send_frame(self):
        if self.camera is None:
            return

        try:
            pRawData, FrameHead = mvsdk.CameraGetImageBuffer(self.camera, 1000)

            mvsdk.CameraImageProcess(self.camera, pRawData, self.pFrameBuffer, FrameHead)
            mvsdk.CameraReleaseImageBuffer(self.camera, pRawData)

            frame_data = (mvsdk.c_ubyte * FrameHead.uBytes).from_address(self.pFrameBuffer)
            frame = np.frombuffer(frame_data, dtype=np.uint8)

            if self.monoCamera:
                frame = frame.reshape((FrameHead.iHeight, FrameHead.iWidth))
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            else:
                frame = frame.reshape((FrameHead.iHeight, FrameHead.iWidth, 3))

            self.latest_frame = frame.copy()

            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ok:
                return

            jpg_bytes = buf.tobytes()
            b64 = base64.b64encode(jpg_bytes).decode("ascii")
            self.frame_signal.emit(b64)

        except mvsdk.CameraException as e:
            print(f"Frame grab error: {e}")
        except Exception as e:
            print(f"Frame processing error: {e}")

    # --------- SLOTS CALLED FROM JS ---------
    # @pyqtSlot(result=str)
    # def startCamera(self):
    #     try:
    #         print("▶️ startCamera() called from JS")

    #         if self.is_running:
    #             print("⚠️ Camera already running")
    #             return "already_running"

    #         if machine_status() is False:
    #             return "plc_connection"

    #         if not air_pressure():
    #             return "Air_pressure"

    #         if not self._open_camera():
    #             print("Cannot start camera")
    #             return "camera"

    #         if not self.timer.isActive():
    #             self.timer.start(40)  # ~25 FPS

    #         self.is_running = True

    #         self.process.ui_cycle_on_request = True

    #         if self.process.auto_mode and not self.process.running_loop:
    #             self.loops()

    #         return "ok"

    #     except Exception as e:
    #         print("startCamera error:", e)
    #         return "process"
        
        # self.process.reset_pressed = False
    
    @pyqtSlot(result=str)
    def startCamera(self):
        try:
            print("▶️ startCamera() called from JS - TEST IMAGE MODE")

            if self.is_running:
                print("⚠️ Already running")
                return "already_running"

            # # ✅ Main original image path
            # test_image_path = r"D:\TrainImg\Green_20s_softouch\top\top_wh_image_20260211_122827_042.bmp"

            # frame = cv2.imread(test_image_path)

            # if frame is None:
            #     print("❌ Test image not found:", test_image_path)
            #     return "image_not_found"

            # ==================================================
            # 1) Show original image in main frame
            # ==================================================
            self.latest_frame = frame.copy()

            b64 = self._encode_bgr_to_base64(frame)
            if not b64:
                print("❌ Failed to encode main image")
                return "encode_failed"

            self.frame_signal.emit(b64)

            # ==================================================
            # 2) Use existing prebuilt function to get tip/thread
            # ==================================================
            from classes.compare_tips import detect_thread_circle

            mask_full, inner_tip_img, orad = detect_thread_circle(frame)

            # ✅ thread image / tip removed image
            thread_img = cv2.bitwise_and(frame, frame, mask=mask_full)

            # ==================================================
            # 3) Send side frame images
            # ==================================================
            self.send_cone_images(thread_img, inner_tip_img, orad)

            self.is_running = True

            print("✅ Main frame + thread image + tip image displayed")
            return "ok"

        except Exception as e:
            print("startCamera test image error:", e)
            return "process"    
        
        
    
    def loops(self):
        if self.process_thread is None or not self.process_thread.isRunning():
            from classes.worker import ProcessWorker
            self.process_thread = ProcessWorker(self.process)
            self.process_thread.openImage.connect(self.show_defect_popup)
            self.process_thread.closeImage.connect(self.close_defect_popup)
            self.process_thread.finished.connect(self._on_process_finished)
            self.process_thread.start()

    

    def show_defect_popup(self, image_path: str):
        p = Path(image_path)
        if not p.is_absolute():
            p = (APP_DIR / p).resolve()

        if not p.exists():
            print("❌ Image not found:", p)
            return

        # ✅ Close previous if exists
        self.close_defect_popup()

        print("📷 Opening image:", p)

        self.defect_dialog = ImageViewerDialog(str(p), parent=self)
        self.defect_dialog.show()
    
    def close_defect_popup(self):
        if self.defect_dialog:
            print("❌ Closing defect popup")
            self.defect_dialog.close()
            self.defect_dialog.deleteLater()
            self.defect_dialog = None

    def _on_process_finished(self):
        print("✅ Process main_loop finished")


    @pyqtSlot()
    def stopCamera(self):
        """Called from JS: stop live view"""
        print("⏹ stopCamera() called from JS")
        self.is_running = False
        if self.timer.isActive():
            self.timer.stop()

       # ---- tell process loop to stop ----
        # self.process.cycle_on_pressed = False
        # self.process.cycle_run = False
        self.process.ui_cycle_off_request = True
        self.process.cycle_off_pressed = True

        # ---- wait for thread to finish (non-blocking in JS side) ----
        if self.process_thread is not None and self.process_thread.isRunning():
            # You can choose a timeout (ms) to avoid hanging forever
            self.process_thread.wait(2000)
        self.process_thread = None
        self._close_camera()
        self.process.ui_reset_request = True
        # Keep camera open if you want fast restart;
        # If you want to fully close:
        # self._close_camera()

    @pyqtSlot()
    def resetSystem(self):
        """Optional: from Reset button"""
        print("🔁 resetSystem() called from JS")
        # Stop + fully close camera
        self.is_running = False
        self._close_camera()
        self.process.ui_reset_request = True
        # Add PLC reset or other logic here if needed

    @pyqtSlot()
    def Emergency(self):
        """Called from JS UI 'Emergency' button"""
        print("[UI] EMERGENCY button pressed")
        self.is_running = False
        self._close_camera()
        self.process.ui_emergency_request = True


    @pyqtSlot(result=bool)
    def showController(self):
        if not self.is_running:
            self.app_ref.open_controller_window()
            return True
        else:
            return False

    @pyqtSlot(result=str)
    def getCameraStatus(self):
        """
        Called from JS: await bridge.getCameraStatus()
        Returns JSON: { "connected": bool, "message": "..." }
        """
        try:
            ok = self._open_camera()
            msg = "Connected" if ok else "Disconnected"
        except Exception as e:
            ok = False
            msg = f"Error: {e}"

        return json.dumps({"connected": ok, "message": msg})
    
    # ================= SNAPSHOT HELPERS =================
    def _snapshot_b1(self):
        """
        Returns board 1 snapshot:
        { "bits": [bool x8], "status": { "di1": {...}, ... } }
        """
        bits = [False] * 8
        status = {}

        for i, b in enumerate(bits, start=1):
            status[f"di{i}"] = {
                "status": "OFF",
                "raw": False,
            }

        return {"bits": bits, "status": status}
        

    def _snapshot_b2(self):
        """
        Returns board 2 snapshot.
        DI7 = auto/manual selector; DI8 = emergency.
        We map them to ON/OFF for UI.
        """
        try:
            di1 = bool(start_point_sensor())
            di2 = bool(mid_point_sensor())
            di3 = bool(end_point_sensor())
            di4 = bool(machine_reset())
            di5 = bool(machine_status())
            di6 = bool(air_pressure())

            mode7 = auto_mode_triggering()
            di7 = mode7 == "auto"

            di8 = bool(emergency_triggering())

            bits = [di1, di2, di3, di4, di5, di6, di7, di8]

        except Exception:
            bits = [False] * 8
            mode7 = "error"

        labels = {
            1: "START SENSOR",
            2: "MID SENSOR",
            3: "END SENSOR",
            4: "RESET",
            5: "MACHINE STATUS",
            6: "AIR PRESSURE",
            7: f"AUTO ({mode7})",
            8: "EMERGENCY",
        }

        status = {}
        for i, b in enumerate(bits, start=1):
            status[f"di{i}"] = {
                "status": "ON" if b else "OFF",
                "raw": bool(b),
                "label": labels.get(i, f"DI{i}")
            }

        return {"bits": bits, "status": status}

    # Backward-compat for your JS fallback (checkAllChannels('b1'/'b2'))
    @pyqtSlot(str, result=str)
    def checkAllChannels(self, board):
        board = (board or "").lower()
        try:
            if board == "b1":
                snap = self._snapshot_b1()
            elif board == "b2":
                snap = self._snapshot_b2()
            else:
                return json.dumps({"_error": f"Unknown board '{board}'"})
            return json.dumps(snap)
        except Exception as e:
            return json.dumps({"_error": str(e)})

    # ================== SET INDIVIDUAL CHANNEL (used by executeSetChannel) ==================
    @pyqtSlot(str, str, str, result=str)
    def checkIndividualChannel(self, board, channel, state):
        """
        JS: await bridge.checkIndividualChannel(board, channel, state);
        board  : 'b1' or 'b2'
        channel: '1'..'8'
        state  : 'on' or 'off'
        """
        board = (board or "").lower()
        try:
            ch = int(channel)
        except Exception:
            return json.dumps({"ok": False, "message": f"Invalid channel '{channel}'"})

        state = (state or "").lower()
        if state not in ("on", "off"):
            return json.dumps({"ok": False, "message": f"Invalid state '{state}'"})
        
        if board == "b2":
            mode7 = auto_mode_triggering()
            if mode7 == "auto":
                # JS will show this in the small status label
                return json.dumps({
                    "ok": False,
                    "message": "AUTO mode ON – switch to MANUAL to change channels"
                })

        try:
            if board == "b1":
                ok = self._set_b1_channel(ch, state)
            elif board == "b2":
                ok = self._set_b2_channel(ch, state)
            else:
                return json.dumps({"ok": False, "message": f"Unknown board '{board}'"})

            if not ok:
                return json.dumps({"ok": False, "message": "Write failed"})

            # Emit signal so JS can update any pills/cards live
            payload = json.dumps({"board": board, "channel": ch, "state": state})
            self.channelChanged.emit(payload)

            return json.dumps({"ok": True, "board": board, "channel": ch, "state": state})
        except Exception as e:
            return json.dumps({"ok": False, "message": str(e)})

    def _set_b1_channel(self, ch: int, state: str) -> bool:
        return False

    @pyqtSlot()
    def showSetting(self):
        self.app_ref.open_setting_window()


    def _load(self):
        # if os.path.exists(SETTINGS_JSON):
        try:
            with open(SETTINGS_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("❌ settings load error:", e)

    def _save(self):
        try:
            with open(SETTINGS_JSON, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            print("❌ settings save error:", e)


    @pyqtSlot(str, str)
    def settings_save_key(self, key, value):
        self._data.setdefault("values", {})
        self._data.setdefault("locked", {})

        if key == "tip_images_selected":
            self._data["values"][key] = json.loads(value)
        else:
            self._data["values"][key] = value

        self._data["locked"][key] = True   # ✅ lock after save
        self._save()

    @pyqtSlot(str)
    def settings_lock(self, key):
        self._data.setdefault("locked", {})
        self._data["locked"][key] = True
        self._save()

    @pyqtSlot(str)
    def settings_unlock(self, key):
        self._data.setdefault("locked", {})
        self._data["locked"][key] = False
        self._save()

    @pyqtSlot(result=str)
    def settings_get_all(self):
        self._data.setdefault("values", {})
        self._data.setdefault("locked", {})
        self._data.setdefault("training_mode",False)
        return json.dumps(self._data)

    
    @pyqtSlot(result=str)
    def get_tip_images(self):
        folder = self.tips_dir
        exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

        if not os.path.isdir(folder):
            print("❌ good_tips folder not found:", folder)
            return "[]"

        items = []
        for name in sorted(os.listdir(folder)):
            ext = os.path.splitext(name)[1].lower()
            if ext not in exts:
                continue

            abs_path = os.path.abspath(os.path.join(folder, name))
            url = QUrl.fromLocalFile(abs_path).toString()
            items.append({"name": name, "url": url})

        return json.dumps(items)
    

    @pyqtSlot(str, result=str)
    def delete_good_images(self, names_json: str):
        """
        Deletes selected images from GOOD image folder
        """
        try:
            names = json.loads(names_json or "[]")
            deleted, missing, failed = [], [], []

            for name in names:
                safe_name = os.path.basename(name)   # 🔒 security
                img_path = os.path.join(GOOD_TIP, safe_name)

                if not os.path.exists(img_path):
                    missing.append(safe_name)
                    continue

                try:
                    os.remove(img_path)
                    deleted.append(safe_name)
                except Exception as e:
                    failed.append({"name": safe_name, "err": str(e)})

            return json.dumps({
                "ok": True,
                "deleted": deleted,
                "missing": missing,
                "failed": failed
            })

        except Exception as e:
            return json.dumps({"ok": False, "err": str(e)})


     # ---------- LIST ----------
    
    
    @pyqtSlot(result=str)
    def shift_list(self) -> str:
        try:
            rows = fetch_all("""
                SELECT id, shift_name, start_time, end_time, status
                FROM shift_table
                WHERE status=1
                ORDER BY id DESC
            """)

            data = [{
                "id": r[0],
                "shift_name": r[1],
                "start_time": r[2],
                "end_time": r[3],
                "status": r[4],
            } for r in rows]

            return json.dumps(data)

        except Exception as e:
            print("shift_list error:", e)
            return json.dumps([])

    # ---------- ADD ----------
    @pyqtSlot(str, str, str, result=str)
    def shift_add(self, shift_name: str, start_hhmm: str, end_hhmm: str) -> str:
        try:
            shift_name = (shift_name or "").strip()
            start_hhmm = (start_hhmm or "").strip()
            end_hhmm   = (end_hhmm or "").strip()

            if not shift_name:
                return "Shift name required"
            if len(start_hhmm) != 5 or ":" not in start_hhmm:
                return "Invalid start time"
            if len(end_hhmm) != 5 or ":" not in end_hhmm:
                return "Invalid end time"

            new_intervals = to_intervals(start_hhmm, end_hhmm)
            if not new_intervals:
                return "Start and End time cannot be same"

            # Get all active shifts
            existing = fetch_all("""
                SELECT id, shift_name, start_time, end_time
                FROM shift_table
                WHERE status=1
            """)

            # Check overlap with any existing shift
            for (sid, sname, sstart, send) in existing:
                try:
                    ex_intervals = to_intervals(str(sstart), str(send))
                    if ex_intervals and overlaps(new_intervals, ex_intervals):
                        return f"Shift time overlaps with existing shift: {sname} ({sstart}-{send})"
                except Exception:
                    # if any bad stored time format
                    return f"Existing shift has invalid time format: ID {sid}"

            # Insert only if no overlap
            new_id = execute("""
                INSERT INTO shift_table (shift_name, start_time, end_time, status)
                VALUES (?, ?, ?, 1)
            """, (shift_name, start_hhmm, end_hhmm))

            if not new_id:
                return "DB Insert Failed"

            return "OK"

        except Exception as e:
            return f"Error: {e}"
        

    @pyqtSlot(str, result=str)
    def shift_delete(self, shift_id: str) -> str:
        try:
            rows = execute("""
                UPDATE shift_table
                SET status=0, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (shift_id,))

            if rows == 0:
                return "No row found"

            return "OK"

        except Exception as e:
            return f"Error: {e}"

    #modified by Kevin
    # def _check_shift_end(self):
    #     from datetime import datetime, date, time as dtime, timedelta
        
    #     try:
    #         # 1️⃣ Get latest active shift
    #         row = fetch_one("""
    #             SELECT id, shift_name, start_time, end_time
    #             FROM shift_table
    #             WHERE status = 1
                
    #         """)
    #         if not row:
    #             return

    #         shift_id, name, start, end = row

    #         # 2️⃣ EARLY LOCK - Check if already sent in DB
    #         sent_db = fetch_one("""
    #             SELECT 1 FROM shift_report_log WHERE shift_id = ?
    #         """, (shift_id,))
    #         if sent_db:
    #             self._shift_report_sent.add(shift_id)
    #             return
            
    #         # Memory lock to prevent double-processing in same session
    #         if shift_id in self._shift_report_sent:
    #             return

    #         # 3️⃣ Convert shift times to datetime
    #         sh, sm = map(int, start.split(":"))
    #         eh, em = map(int, end.split(":"))
    #         today = date.today()
    #         start_dt = datetime.combine(today, dtime(sh, sm))
    #         end_dt = datetime.combine(today, dtime(eh, em))
    #         if end_dt <= start_dt:  # overnight shift
    #             end_dt += timedelta(days=1)

    #         now = datetime.now()
    #         if now < end_dt:
    #             return  # shift not yet ended

    #         # 4️⃣ Check if there are records for this shift
    #         record_count = fetch_one("""
    #             SELECT COUNT(*) FROM cone_entry WHERE shift_id = ?
    #         """, (shift_id,))
    #         if not record_count or record_count[0] == 0:
    #             print(f"❌ [ERROR] No records found for shift '{name}' (ID: {shift_id}). Cannot generate report.")
    #             self._shift_report_sent.add(shift_id)  # Mark as attempted
    #             return

    #         print(f"📋 [INFO] Shift '{name}' ended. Generating report...")

    #         # 5️⃣ Generate PDF (use date only, not timestamps)
    #         # Note: mode="summary_only" excludes images to keep PDF size small for email
    #         pdf_url = self.reports_export_pdf(
    #             date_from=start_dt.strftime("%Y-%m-%d"),
    #             date_to=end_dt.strftime("%Y-%m-%d"),
    #             shift_id=str(shift_id),
    #             color_id="",
    #             count_id="",
    #             mode="summary_only"  # Don't include images to reduce file size
    #         )

    #         if not pdf_url:
    #             print(f"❌ [ERROR] PDF generation failed for shift '{name}'")
    #             self._shift_report_sent.add(shift_id)
    #             return

    #         # ✅ 6️⃣ Convert file:/// URL → real file path using improved function
    #         pdf_path = file_url_to_path(pdf_url)

    #         print(f"[DEBUG] Original PDF URL: {pdf_url}")
    #         print(f"[DEBUG] Converted PDF path: {pdf_path}")

    #         # Verify file exists
    #         if not os.path.exists(pdf_path):
    #             print(f"❌ [ERROR] PDF file not found at: {pdf_path}")
    #             if os.path.exists(os.path.dirname(pdf_path)):
    #                 files = os.listdir(os.path.dirname(pdf_path))
    #                 print(f"[DEBUG] Available files: {files}")
    #             self._shift_report_sent.add(shift_id)
    #             return

    #         print(f"✅ [OK] PDF verified at: {pdf_path}")

    #         # 7️⃣ Compress PDF before sending
    #         try:
    #             from classes.pdf_compress import compress_pdf
    #             # Generate output path for compressed PDF
    #             compressed_pdf_path = pdf_path.replace(".pdf", "_compressed.pdf")
    #             compress_pdf(pdf_path, compressed_pdf_path, quality="screen")
    #             final_pdf_path = compressed_pdf_path
                
    #             # Check file size
    #             size_mb = os.path.getsize(final_pdf_path) / (1024 * 1024)
    #             print(f"✅ [OK] PDF compressed to {size_mb:.2f} MB")
                
    #             if size_mb > 20:
    #                 print(f"⚠️  [WARN] Compressed PDF still large ({size_mb:.2f} MB). Gmail limit is ~25MB.")
                    
    #         except Exception as e:
    #             print(f"⚠️  [WARN] PDF compression failed: {e}")
    #             # Use original PDF, but check its size
    #             final_pdf_path = pdf_path
    #             size_mb = os.path.getsize(final_pdf_path) / (1024 * 1024)
    #             print(f"[DEBUG] Original PDF size: {size_mb:.2f} MB")
                
    #             if size_mb > 25:
    #                 print(f"❌ [ERROR] PDF size ({size_mb:.2f} MB) exceeds Gmail limit (25MB). Cannot send.")
    #                 print(f"[SOLUTION] Try compressing with higher quality or reducing report scope.")
    #                 self._shift_report_sent.add(shift_id)
    #                 return

    #         # 8️⃣ Send email with the PDF
    #         email_sent = False
    #         try:
    #             from classes.mailer import send_shift_mail
    #             send_shift_mail(name, end_dt, final_pdf_path)
    #             email_sent = True
    #             print(f"✅ [SUCCESS] Email sent for shift '{name}' to kgokul282004@gmail.com")
    #         except Exception as e:
    #             print(f"❌ [ERROR] Email sending failed: {e}")
    #             self._shift_report_sent.add(shift_id)
    #             return

    #         # 9️⃣ Log sent report in DB ONLY after successful email
    #         if not email_sent:
    #             print(f"❌ [ERROR] Email not sent. Not logging to database.")
    #             self._shift_report_sent.add(shift_id)
    #             return
                
    #         try:
    #             execute("""
    #                 INSERT INTO shift_report_log (shift_id, shift_end, sent_at)
    #                 VALUES (?, ?, ?)
    #             """, (shift_id, end_dt.isoformat(timespec="minutes"),
    #                   datetime.now().isoformat(timespec="seconds")))
    #             print(f"✅ [DB] Report logged as sent in database")
    #         except Exception as e:
    #             print(f"⚠️  [WARN] Failed to log report in DB: {e}")
            
    #         # 🔟 Mark as sent in memory to prevent re-processing
    #         self._shift_report_sent.add(shift_id)
    #         print(f"✅ [COMPLETE] Shift '{name}' report sent successfully (OneTime execution).")

    #     except Exception as e:
    #         print(f"❌ [ERROR] Shift end check error: {e}")



    #modified by Gokul

    isEnd = True
    def _check_shift_end(self):
        """
        Old logic, but FIXED:

        ✅ If app starts AFTER shift ended -> catch-up send (latest ended shift)
        ✅ Prevents duplicates using shift_key = shift_id + shift_end_time
        ✅ Uses DB shift_report_log to avoid duplicates after restart
        ✅ Still starts NEXT shift immediately like your code
        """

        from datetime import datetime, date, time as dtime, timedelta
        from PyQt5.QtCore import QThreadPool

        try:
            now = datetime.now()

            # 1️⃣ Load all ACTIVE shifts
            shifts = fetch_all("""
                SELECT id, shift_name, start_time, end_time
                FROM shift_table
                WHERE status = 1
                ORDER BY start_time
            """) or []

            if not shifts:
                return

            # Helper → convert HH:MM to datetime range (overnight supported)
            def build_range(start_str, end_str, now_dt):
                """
                Returns (start_dt, end_dt) for the shift instance that is relevant for 'now_dt'.
                Works for normal + overnight shifts.
                Example overnight: 22:00 -> 06:00
                - at 01:00, start is yesterday 22:00, end is today 06:00
                - at 23:00, start is today 22:00, end is tomorrow 06:00
                """
                sh, sm = map(int, str(start_str).split(":"))
                eh, em = map(int, str(end_str).split(":"))

                st = dtime(sh, sm)
                et = dtime(eh, em)

                # Normal shift (same day)
                if et > st:
                    start_dt = datetime.combine(now_dt.date(), st)
                    end_dt   = datetime.combine(now_dt.date(), et)
                    return start_dt, end_dt

                # Overnight shift
                if now_dt.time() < et:
                    # after midnight but before end -> started yesterday
                    start_dt = datetime.combine(now_dt.date() - timedelta(days=1), st)
                    end_dt   = datetime.combine(now_dt.date(), et)
                else:
                    # before midnight and after start -> ends tomorrow
                    start_dt = datetime.combine(now_dt.date(), st)
                    end_dt   = datetime.combine(now_dt.date() + timedelta(days=1), et)

                return start_dt, end_dt

            # DB guard (restart-safe)
            def already_sent_db(shift_id, end_dt):
                key = end_dt.isoformat(timespec="minutes")
                row = fetch_one("""
                    SELECT 1 FROM shift_report_log
                    WHERE shift_id=? AND shift_end=?
                    LIMIT 1
                """, (int(shift_id), key))
                return bool(row)

            # start mail job with correct key lock
            def start_mail_job(shift_id, name, start_dt, end_dt):
                shift_key = f"{int(shift_id)}|{end_dt.isoformat(timespec='minutes')}"

                # memory guard (same run)
                if shift_key in self._shift_report_sent:
                    return

                # DB guard (after restart)
                if already_sent_db(shift_id, end_dt):
                    self._shift_report_sent.add(shift_key)
                    print("ℹ️ [SHIFT] Already mailed (DB):", shift_key)
                    return

                self._shift_report_sent.add(shift_key)
                print(f"📨 [SHIFT] Start mail job: {name} (id={shift_id}) end={end_dt}")

                QThreadPool.globalInstance().start(
                    Bridge.ShiftMailJob(
                        bridge=self,
                        shift_id=shift_id,
                        name=name,
                        start_dt=start_dt,
                        end_dt=end_dt
                    )
                )

            # --------------------------------------------------
            # 2️⃣ NO ACTIVE SHIFT → CHECK FOR SHIFT START
            #     IF NONE ACTIVE -> CATCH-UP latest ended shift (important fix!)
            # --------------------------------------------------
            if self._active_shift_id is None:

                # A) Find active shift now
                for sid, name, start, end in shifts:
                    start_dt, end_dt = build_range(start, end, now)

                    if start_dt <= now < end_dt:
                        self._active_shift_id = sid
                        # print(f"🟢 [SHIFT STARTED] {name} ({start}-{end})")
                        return

                # B) Catch-up: app started late, so send the most recent ended shift (if not sent)
                ended = []
                for sid, name, start, end in shifts:
                    start_dt, end_dt = build_range(start, end, now)
                    if now >= end_dt:
                        ended.append((end_dt, sid, name, start_dt))

                if ended:
                    ended.sort(key=lambda x: x[0])   # sort by end_dt
                    end_dt, sid, name, start_dt = ended[-1]  # most recent ended
                    start_mail_job(sid, name, start_dt, end_dt)
                return

            # --------------------------------------------------
            # 3️⃣ ACTIVE SHIFT → CHECK END
            # --------------------------------------------------
            active = next((s for s in shifts if s[0] == self._active_shift_id), None)
            if not active:
                self._active_shift_id = None
                return
            shift_id, name, start, end = active
            start_dt, end_dt = build_range(start, end, now)

            # still running
            if now < end_dt:
                return

            # --------------------------------------------------
            # 4️⃣ SHIFT ENDED
            # --------------------------------------------------
            print(f"🔴 [SHIFT ENDED] {name} ({start}-{end})")

            # ✅ IMPORTANT FIX: do NOT use only shift_id (that blocks next day mails)
            start_mail_job(shift_id, name, start_dt, end_dt)

            # --------------------------------------------------
            # 5️⃣ START NEXT SHIFT IMMEDIATELY
            # --------------------------------------------------
            self._active_shift_id = None

            for sid2, name2, start2, end2 in shifts:
                s2_dt, e2_dt = build_range(start2, end2, now)
                if s2_dt <= now < e2_dt:
                    self._active_shift_id = sid2
                    print(f"🟢 [NEXT SHIFT STARTED] {name2} ({start2}-{end2})")
                    break

        except Exception as e:
            print("❌ shift background error:", e)
            
    @pyqtSlot(bool)
    def set_training_mode(self, state):
        try:
            import json
            with open(SETTINGS_JSON, "r+", encoding="utf-8") as f:
                data = json.load(f)
                data["training_mode"] = bool(state)
                f.seek(0)
                json.dump(data, f, indent=2)
                f.truncate()
            print("Training Mode ENABLED" if state else "Training Mode DISABLED")
        except Exception as e:
            print("training issue:", e)
        
    
    @pyqtSlot()
    def showReport(self):
        self.app_ref.open_report_window()

    def _null_if_empty(self,x):
        if x is None: 
            return None
        x = str(x).strip()
        return None if x == "" or x.lower() == "all" else x
    
    #modified by Gokul
    def _get_shift_window_dt(self, date_str: str, shift_id: int):
        """
        Returns (start_dt_str, end_dt_str) for selected shift on selected date.
        Supports overnight shift (ex: 22:00-06:00).
        """
        from datetime import datetime, timedelta

        row = fetch_one("""
            SELECT start_time, end_time
            FROM shift_table
            WHERE id=? AND status=1
            LIMIT 1
        """, (int(shift_id),))

        if not row:
            return None, None

        start_hhmm = str(row[0])[:5]
        end_hhmm   = str(row[1])[:5]

        start_dt = datetime.strptime(f"{date_str} {start_hhmm}", "%Y-%m-%d %H:%M")
        end_dt   = datetime.strptime(f"{date_str} {end_hhmm}", "%Y-%m-%d %H:%M")

        # overnight shift fix
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

        return (
            start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_dt.strftime("%Y-%m-%d %H:%M:%S")
        )
    
    def _list_dirs(self,path: str):
        try:
            return sorted([
                d for d in os.listdir(path)
                if os.path.isdir(os.path.join(path, d))
            ])
        except Exception:
            return []

    @pyqtSlot()
    def open_mail_popup(self):
        try:
            from classes.mailer import open_mail_receivers_popup
            # parent can be self.app_ref or None
            open_mail_receivers_popup(parent=self.app_ref)
        except Exception as e:
            print("open_mail_popup error:", e)



    @pyqtSlot(result=str)
    def reports_filter_options(self):
        shifts = fetch_all("SELECT id, shift_name FROM shift_table WHERE status=1 ORDER BY id DESC") or []
        colors = self._list_dirs(f"{PREDICTION}")
    
        color_counts = {}
        for c in colors:
            color_counts[c] = self._list_dirs(os.path.join(PREDICTION, c))

        data = {
            "shifts": [{"id": r[0], "name": r[1]} for r in shifts],
            "colors": [{"id": c, "name": c} for c in colors],     # id=name because folder name is key
            "counts_map": color_counts                              # counts depend on selected color
        }
        return json.dumps(data)
    

    @pyqtSlot(str, str, str, str, str, result=str)
    def reports_summary(self, date_from, date_to, shift_id, color_id, count_id):
        shift_id = self._null_if_empty(shift_id)
        color_id = self._null_if_empty(color_id)
        count_id = self._null_if_empty(count_id)

        q = """
        SELECT
        COUNT(*) AS total_cones,
        SUM(CASE WHEN defect_img_path IS NOT NULL AND defect_img_path != '' THEN 1 ELSE 0 END) AS with_images,
        SUM(CASE WHEN tip_result   = 'bad' THEN 1 ELSE 0 END) AS tip_bad,
        SUM(CASE WHEN top_result   = 'bad' THEN 1 ELSE 0 END) AS top_bad,
        SUM(CASE WHEN bottom_result= 'bad' THEN 1 ELSE 0 END) AS bottom_bad,
        SUM(CASE WHEN tip_result='good' AND top_result='good' AND bottom_result='good' THEN 1 ELSE 0 END) AS all_good
        FROM cone_entry
        WHERE DATE(created_at) BETWEEN DATE(?) AND DATE(?)
        AND (? IS NULL OR shift_id = ?)
        AND (? IS NULL OR cone_color = ?)
        AND (? IS NULL OR cone_count = ?);
        """
        row = fetch_one(q, (
            date_from, date_to,
            shift_id, shift_id,
            color_id, color_id,
            count_id, count_id
        )) or (0,0,0,0,0,0)

        data = {
            "total_cones": row[0] or 0,
            "with_images": row[1] or 0,
            "tip_bad": row[2] or 0,
            "top_bad": row[3] or 0,
            "bottom_bad": row[4] or 0,
            "all_good": row[5] or 0,
        }
        return json.dumps(data)
    

#     @pyqtSlot(str, str, str, str, str, str, int, int, result=str)
#     def reports_list(self, date_from, date_to, shift_id, color_id, count_id, mode, limit, offset):
#         shift_id = self._null_if_empty(shift_id)
#         color_id = self._null_if_empty(color_id)
#         count_id = self._null_if_empty(count_id)
#         mode = self._null_if_empty(mode)  # tip/top/bottom/image or None

#         q = """
# SELECT
#     ce.id,
#     ce.created_at,
#     COALESCE(st.shift_name,'') as shift_name,
#     COALESCE(ce.cone_color,'') as cone_color,
#     COALESCE(ce.cone_count,'') as cone_count,
#     COALESCE(ce.tip_result,'') as tip_result,
#     COALESCE(ce.top_result,'') as top_result,
#     COALESCE(ce.bottom_result,'') as bottom_result,
#     COALESCE(ce.defect_img_path,'') as defect_img_path
#     FROM cone_entry ce
#     LEFT JOIN shift_table st ON st.id = ce.shift_id
#     WHERE {time_where}
#     AND (? IS NULL OR ce.shift_id = ?)
#     AND (? IS NULL OR ce.cone_color = ?)
#     AND (? IS NULL OR ce.cone_count = ?)
#     AND (? IS NULL OR (
#             (?='tip' AND ce.tip_result='bad') OR
#             (?='top' AND ce.top_result='bad') OR
#             (?='bottom' AND ce.bottom_result='bad') OR
#             (?='image' AND ce.defect_img_path IS NOT NULL AND ce.defect_img_path!='')
#         ))
#     ORDER BY ce.id DESC
#     LIMIT ? OFFSET ?;
#         """

#         rows = fetch_all(q, (
#             date_from, date_to,
#             shift_id, shift_id,
#             color_id, color_id,
#             count_id, count_id,
#             mode, mode, mode, mode, mode,
#             int(limit), int(offset)
#         )) or []

#         out = []
#         for r in rows:
#             out.append({
#                 "id": r[0],
#                 "created_at": r[1],
#                 "shift": r[2],
#                 "cone_color": r[3],
#                 "cone_count": r[4],
#                 "tip": r[5],
#                 "top": r[6],
#                 "bottom": r[7],
#                 "img": f"{APP_DIR}/{r[8]}",   # path stored in DB
#             })

#         return json.dumps(out)







    @pyqtSlot(str, str, str, str, str, str, int, int, result=str)
    def reports_list(self, date_from, date_to, shift_id, color_id, count_id, mode, limit, offset):
        shift_id = self._null_if_empty(shift_id)
        color_id = self._null_if_empty(color_id)
        count_id = self._null_if_empty(count_id)
        mode = self._null_if_empty(mode)  # tip/top/bottom/image or None

        # ✅ If shift selected and single date → filter by exact shift time window
        if shift_id is not None and date_from == date_to:
            sdt, edt = self._get_shift_window_dt(date_from, int(shift_id))
            time_where = "ce.created_at >= ? AND ce.created_at < ?"
            time_args = [sdt, edt]
        else:
            time_where = "DATE(ce.created_at) BETWEEN DATE(?) AND DATE(?)"
            time_args = [date_from, date_to]

        q = f"""
        SELECT
        ce.id,
        ce.created_at,
        COALESCE(st.shift_name,'') as shift_name,
        COALESCE(ce.cone_color,'') as cone_color,
        COALESCE(ce.cone_count,'') as cone_count,
        COALESCE(ce.tip_result,'') as tip_result,
        COALESCE(ce.top_result,'') as top_result,
        COALESCE(ce.bottom_result,'') as bottom_result,
        COALESCE(ce.defect_img_path,'') as defect_img_path
        FROM cone_entry ce
        LEFT JOIN shift_table st ON st.id = ce.shift_id
        WHERE {time_where}
        AND (? IS NULL OR ce.shift_id = ?)
        AND (? IS NULL OR ce.cone_color = ?)
        AND (? IS NULL OR ce.cone_count = ?)
        AND (? IS NULL OR (
                (?='tip' AND ce.tip_result='bad') OR
                (?='top' AND ce.top_result='bad') OR
                (?='bottom' AND ce.bottom_result='bad') OR
                (?='image' AND ce.defect_img_path IS NOT NULL AND ce.defect_img_path!='')
            ))
        ORDER BY ce.id DESC
        LIMIT ? OFFSET ?;
        """

        params = []
        params += time_args
        params += [
            shift_id, shift_id,
            color_id, color_id,
            count_id, count_id,
            mode, mode, mode, mode, mode,
            int(limit), int(offset)
        ]

        rows = fetch_all(q, tuple(params)) or []

        out = []
        for r in rows:
            out.append({
                "id": r[0],
                "created_at": r[1],
                "shift": r[2],
                "cone_color": r[3],
                "cone_count": r[4],
                "tip": r[5],
                "top": r[6],
                "bottom": r[7],
                "img": f"{APP_DIR}/{r[8]}",
            })

        return json.dumps(out)
    #modified by Gokul
    @pyqtSlot(str, str, str, str, str, str, result=str)
    def reports_export_pdf_async(self, date_from, date_to, shift_id, color_id, count_id, mode):
        """
        Use this for DOWNLOAD button.
        Runs PDF generation in background thread, and emits pdfReady/pdfError.
        """
        try:
            from PyQt5.QtCore import QRunnable, QThreadPool

            bridge = self

            class PdfJob(QRunnable):
                def run(self):
                    try:
                        url = bridge.reports_export_pdf(date_from, date_to, shift_id, color_id, count_id, mode)
                        if url:
                            bridge.pdfReady.emit(url)
                        else:
                            bridge.pdfError.emit("PDF generation failed (empty output).")
                    except Exception as e:
                        bridge.pdfError.emit(str(e))

            QThreadPool.globalInstance().start(PdfJob())
            return "OK"
        except Exception as e:
            return f"Error: {e}"


    @pyqtSlot(str, str, str, str, str, str, result=str)
    def reports_export_pdf(self, date_from, date_to, shift_id, color_id, count_id, mode):
        try:
            import os
            from datetime import datetime

            # ---------------- normalize filters ----------------
            shift_id = self._null_if_empty(shift_id)   # str or None
            color_id = self._null_if_empty(color_id)   # str or None
            count_id = self._null_if_empty(count_id)   # str or None
            mode = self._null_if_empty(mode)           # str or None

            # ---------------- DB: summary ----------------
            total_cones_row = fetch_one("""
                SELECT COUNT(*)
                FROM cone_entry
                WHERE DATE(created_at) BETWEEN DATE(?) AND DATE(?)
                AND (? IS NULL OR shift_id = ?)
                AND (? IS NULL OR cone_color = ?)
                AND (? IS NULL OR cone_count = ?)
            """, (date_from, date_to,
                shift_id, shift_id,
                color_id, color_id,
                count_id, count_id)) or (0,)

            total_cones = int(total_cones_row[0] or 0)

            defect_cnt_row = fetch_one("""
                SELECT COUNT(*)
                FROM cone_entry
                WHERE DATE(created_at) BETWEEN DATE(?) AND DATE(?)
                AND (? IS NULL OR shift_id = ?)
                AND (? IS NULL OR cone_color = ?)
                AND (? IS NULL OR cone_count = ?)
                AND defect_img_path IS NOT NULL AND defect_img_path != ''
            """, (date_from, date_to,
                shift_id, shift_id,
                color_id, color_id,
                count_id, count_id)) or (0,)

            total_defects = int(defect_cnt_row[0] or 0)

            shift_count_value = total_cones

            # If color not given, pick latest non-empty color from filtered data (optional)
            if not color_id:
                c_row = fetch_one("""
                    SELECT cone_color
                    FROM cone_entry
                    WHERE DATE(created_at) BETWEEN DATE(?) AND DATE(?)
                    AND (? IS NULL OR shift_id = ?)
                    AND cone_color IS NOT NULL AND cone_color != ''
                    ORDER BY id DESC
                    LIMIT 1
                """, (date_from, date_to, shift_id, shift_id))
                color_id = (c_row[0] if c_row else "") or ""

            # ---------------- defect rows ----------------
            rows = fetch_all("""
                SELECT
                    created_at,
                    COALESCE(cone_count,'') as cone_count,
                    COALESCE(tip_result,'') as tip_result,
                    COALESCE(top_result,'') as top_result,
                    COALESCE(top_uv_result,'') as top_uv_result,
                    COALESCE(bottom_result,'') as bottom_result,
                    COALESCE(bottom_uv_result,'') as bottom_uv_result,
                    COALESCE(defect_img_path,'') as defect_img_path
                FROM cone_entry
                WHERE DATE(created_at) BETWEEN DATE(?) AND DATE(?)
                AND (? IS NULL OR shift_id = ?)
                AND (? IS NULL OR cone_color = ?)
                AND (? IS NULL OR cone_count = ?)
                AND defect_img_path IS NOT NULL AND defect_img_path != ''
                ORDER BY id ASC
            """, (date_from, date_to,
                shift_id, shift_id,
                color_id, color_id,
                count_id, count_id)) or []

            # For non-summary mode, check if there are defect images
            if mode != "summary_only" and total_defects == 0:
                print("⚠ No defect images in selected filters. PDF not generated.")
                return ""

            # ---------------- output path ----------------
            export_dir = os.path.join(os.path.dirname(DB_PATH), "exports")
            os.makedirs(export_dir, exist_ok=True)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"report_{date_from}_to_{date_to}_{ts}.pdf"
            filepath = os.path.join(export_dir, filename)

            # ---------------- ReportLab imports ----------------
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.enums import TA_CENTER
            from reportlab.lib import colors
            from reportlab.lib.units import cm

            # ---------------- Header/Footer content ----------------
            # ✅ Use your logo image
            LOGO_PATH = str((APP_DIR / "/home/texa_innovates/conei_lamb_main/static/images/logo.png").resolve())

            COMPANY_LINES = [
                "Lambothara Textiles Ltd.,",
                "826, Thzhaiyuthu, Palani - 6246618",
                "Ph_no : 9047052057, 8508552058",
                "E-mail : mill@lambotharatextiles.com"
            ]
            FOOTER_TEXT = "Texa Innovates @Coimbatore mail_Id : info@texainnovates.com"

            def _draw_logo(canvas, w, h, is_first: bool):
                """Draw TEXA logo top-right on every page.
                First page slightly bigger, other pages slightly smaller."""
                if not os.path.exists(LOGO_PATH):
                    return

                if is_first:
                    logo_w = 3.8 * cm  # bigger on first page
                    logo_h = 1.7 * cm
                else:
                    logo_w = 3.0 * cm  # smaller on other pages
                    logo_h = 1.3 * cm

                logo_x = w - logo_w - 1.6 * cm   # same right margin
                logo_y = h - logo_h - 2.0 * cm   # same top offset

                canvas.drawImage(
                    LOGO_PATH,
                    logo_x, logo_y,
                    logo_w, logo_h,
                    preserveAspectRatio=True,
                    mask="auto"
                )

            def draw_first_page(canvas, doc):
                canvas.saveState()
                w, h = A4

                # Border
                canvas.setLineWidth(2)
                canvas.rect(1 * cm, 1 * cm, w - 2 * cm, h - 2 * cm)

                # Left company block (only first page)
                x_left = 1.6 * cm
                y_top = h - 2.1 * cm
                for i, line in enumerate(COMPANY_LINES):
                    canvas.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 10 if i == 0 else 9)
                    canvas.drawString(x_left, y_top - i * 0.45 * cm, line)

                # Logo (first page bigger)
                _draw_logo(canvas, w, h, is_first=True)

                # Footer (every page)
                canvas.setFont("Helvetica", 9)
                canvas.drawRightString(
                    w - 1.5 * cm, 1.2 * cm,
                    f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                canvas.drawCentredString(w / 2.0, 0.6 * cm, FOOTER_TEXT)

                canvas.restoreState()

            def draw_other_pages(canvas, doc):
                canvas.saveState()
                w, h = A4

                # Border
                canvas.setLineWidth(2)
                canvas.rect(1 * cm, 1 * cm, w - 2 * cm, h - 2 * cm)

                # Logo (other pages smaller)
                _draw_logo(canvas, w, h, is_first=False)

                # Footer (every page)
                canvas.setFont("Helvetica", 9)
                canvas.drawRightString(
                    w - 1.5 * cm, 1.2 * cm,
                    f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                canvas.drawCentredString(w / 2.0, 0.6 * cm, FOOTER_TEXT)

                canvas.restoreState()

            # ---------------- PDF document (same margins) ----------------
            doc = SimpleDocTemplate(
                filepath,
                pagesize=A4,
                topMargin=4.2 * cm,
                bottomMargin=2 * cm,
                leftMargin=1.5 * cm,
                rightMargin=1.5 * cm
            )

            styles = getSampleStyleSheet()
            styles.add(ParagraphStyle(
                name="TitleRed",
                alignment=TA_CENTER,
                fontSize=18,
                textColor=colors.darkred
            ))

            story = []

            # ===================== REPORT SUMMARY =====================
            story.append(Paragraph("REPORT SUMMARY", styles["TitleRed"]))
            story.append(Spacer(1, 10))

            total_good_cones = shift_count_value - total_defects

            import sqlite3
            conn = sqlite3.connect(DB_PATH)  # your DB path
            cursor = conn.cursor()
            cursor.execute("SELECT shift_name FROM shift_table WHERE id = ?", (shift_id,))
            row = cursor.fetchone()
            shift_name = row[0] if row else "All"


            summary_table = [
                ["Shift ID", shift_id if shift_id is not None else "All"],
                ["Shift Name", shift_name],
                ["Cone Color", color_id if color_id is not None else ""],
                ["Total Cones", str(shift_count_value)],
                ["Total Good Cones", str(total_good_cones)],
                ["Total Defects", str(total_defects)],
            ]

            t_sum = Table(summary_table, colWidths=[6 * cm, 6 * cm])
            t_sum.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
            ]))
            story.append(t_sum)
            story.append(Spacer(1, 20))

            # ===================== DEFECT TABLE =====================
            story.append(Paragraph("DEFECT TABLE", styles["TitleRed"]))
            story.append(Spacer(1, 10))

            def abs_img(p: str) -> str:
                p = (p or "").strip()
                if not p:
                    return ""
                return p if os.path.isabs(p) else str((APP_DIR / p).resolve())

            def defect_name(tip, top, top_uv, bottom, bottom_uv) -> str:
                if str(tip).lower() == "bad": return "TIP DEFECT"
                if str(top).lower() == "bad": return "TOP DEFECT"
                if str(top_uv).lower() == "bad": return "TOP UV DEFECT"
                if str(bottom).lower() == "bad": return "BOTTOM DEFECT"
                if str(bottom_uv).lower() == "bad": return "BOTTOM UV DEFECT"
                return "UNKNOWN"

            # ✅ Your current behavior: even summary_only includes images in table
            table_data = [["ID No", "Defect Time", "Cone Count ID", "Image", "Defect Name"]]
            for i, r in enumerate(rows, 1):
                created_at = r[0]
                cone_count_val = r[1]
                tip_res = r[2]
                top_res = r[3]
                top_uv_res = r[4]
                bottom_res = r[5]
                bottom_uv_res = r[6]
                img_rel = r[7]

                img_path = abs_img(img_rel)
                # ✅ IMPORTANT: lazy=1 for BOTH modes (prevents heavy memory use)
                img_cell = Image(img_path, 3.2 * cm, 3.2 * cm, lazy=1) if (img_path and os.path.exists(img_path)) else "No Image"
                dname = defect_name(tip_res, top_res, top_uv_res, bottom_res, bottom_uv_res)

                table_data.append([i, str(created_at), str(cone_count_val), img_cell, dname])

            defect_table = Table(table_data, colWidths=[1.5 * cm, 4 * cm, 3 * cm, 4 * cm, 3.5 * cm])
            defect_table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]))
            story.append(defect_table)

            if mode == "summary_only":
                story.append(Spacer(1, 10))
                story.append(Paragraph(f"Total Defects Found: {len(rows)}", styles["Normal"]))

            # ✅ Build with header/footer on every page
            doc.build(story, onFirstPage=draw_first_page, onLaterPages=draw_other_pages)

            file_url = "file:///" + filepath.replace("\\", "/")
            print("✅ PDF Exported Successfully:", filepath)
            return file_url

        except Exception as e:
            print("reports_export_pdf error:", e)
            return ""
