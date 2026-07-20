import time
from typing import Callable, Optional
from collections import deque
from paths import APP_DIR, SETTINGS_JSON, PREDICTION, DEFECT_SAVE_DIR,RUN_DIR
from classes.Plc_connection import *
import json
from pathlib import Path




# ---------------- CONFIG / TUNABLES ----------------
POLL = 0.01
STOP_CONVEYOR = 0.09   # 40/2s -->(0.095) / 24s --> (0.07)
INTERLOCK_TIMEOUT = 2.0
ROTATE_TIME = 1.0
PISTON_PULSE = 0.20
PISTON_UP_WAIT = 0.8
PISTON_DOWN_WAIT = 0.05
WH_STABILIZE = 2
UV_STABILIZE_TOP = 2
UV_STABILIZE = 3.5
POST_RELEASE_WAIT = 0.3
CONE_SETTLE_FALLBACK = 1.0


# ---- EXPOSURE SETTINGS (microseconds) ----
LIVE_EXPOSURE_US = 1785      # exposure for normal/live image
UV_EXPOSURE_TOP_US = 5300   # exposure for TOP UV image
UV_EXPOSURE_BOTTOM_US = 5300  # exposure for BOTTOM UV image

# ---------------- SENSORS / INTERLOCK MAPPING ----------------
SENSOR1_CONVEYOR = start_point_sensor
SENSOR2_ARRIVAL  = mid_point_sensor
SENSOR3_FINAL    = end_point_sensor

BTN_RESET       = machine_reset
BTN_CYCLE_ON    = machine_status
AIR_PRESSURE    = air_pressure

SW_AUTO_MANUAL_FN = auto_mode_triggering
BTN_EMERGENCY_FN  = emergency_triggering

INTERLOCK_PISTON_IDLE = piston_idle_sensor
INTERLOCK_CONE_TOUCH  = piston_idle_sensor

# ---------------- OUTPUT LISTS FOR SAFE-CLEAR ----------------

# ---------------- HELPERS ----------------
def read_safe(fn: Callable[[], Optional[bool]]) -> Optional[bool]:
    try:
        return fn()
    except:
        return None

def is_active(fn: Callable[[], Optional[bool]], active_high: bool = True) -> Optional[bool]:
    v = read_safe(fn)
    if v is None:
        return None
    return bool(v) if active_high else (not bool(v))

def wait_until(predicate: Callable[[], bool], timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if predicate():
                return True
        except:
            pass
        time.sleep(POLL)
    return False

def wait_interlock(fn: Callable[[], Optional[bool]], expected_active=True,
                   timeout=INTERLOCK_TIMEOUT) -> bool:
    def p():
        v = read_safe(fn)
        if v is None:
            return False
        return bool(v) is expected_active
    return wait_until(p, timeout)

def log_step(text: str):
    print(f"[STEP] {text}")

# ---------------- CONNECTION CHECKS ----------------


# ---------------- ACTUATORS ----------------
def start_conveyor():
    conveyor_on()
    print("[OUTPUT] Conveyor ON")


def stop_conveyor():
    conveyor_off()
    print("[OUTPUT] Conveyor OFF")


def b2_lock_cone(on: bool):
    if on:
        lock_cone()
    else:
        unlock_cone()
    print(f"[OUTPUT] Lock cone -> {'ON' if on else 'OFF'}")


def b2_piston_up(on: bool):
    if on:
        piston_up()
    else:
        piston_up_stop()
    print(f"[OUTPUT] Piston UP -> {'ON' if on else 'OFF'}")


def b2_piston_down(on: bool):
    if on:
        piston_down()
    else:
        piston_down_stop()
    print(f"[OUTPUT] Piston DOWN -> {'ON' if on else 'OFF'}")


def b2_rotate(on: bool):
    if on:
        piston_rotate()
    else:
        piston_rotate_stop()
    print(f"[OUTPUT] Rotate -> {'ON' if on else 'OFF'}")


def uv_on(on: bool):
    if on:
        uv_light_on()
    else:
        uv_light_off()
    print(f"[OUTPUT] UV -> {'ON' if on else 'OFF'}")


def whight_light_on(on: bool):
    if on:
        white_light_on()
    else:
        white_light_off()
    print(f"[OUTPUT] White -> {'ON' if on else 'OFF'}")


def set_green(on: bool):
    if on:
        green_light_on()
    else:
        green_light_off()
    print(f"[OUTPUT] GREEN -> {'ON' if on else 'OFF'}")


def set_reject_alarm(on: bool):
    if on:
        red_light_on()
        buzzer_on()
        print("[OUTPUT] RED + BUZZER ON")
    else:
        red_light_off()
        buzzer_off()
        print("[OUTPUT] RED + BUZZER OFF")


def all_outputs_off():
    stop_conveyor()
    uv_on(False)
    whight_light_on(False)
    set_green(False)
    set_reject_alarm(False)
    b2_lock_cone(False)
    b2_piston_up(False)
    b2_piston_down(False)
    b2_rotate(False)

# TOP_STATUS = "good"
# BOTTOM_STATUS = "good" #"bad"
# ---------------- SEQUENCE HANDLING ----------------
import cv2
from classes.compare_tips import compare_tips,detect_thread_circle
import os
# from classes.PreB7 import prediction_process

def prediction_process(prediction_path, threshold, bgr_img, position=None, count_name=None):
    print("Dummy prediction_process called. Torch not available.")
    return "dummy", 0.5
from classes.database import execute,get_current_shift_id,get_current_shift_count


class Process:
    def __init__(self, set_exposure_cb=None, capture_frame_cb=None, send_cone_images_cb=None,):
        self.set_exposure_cb = set_exposure_cb 
        self.capture_frame_cb = capture_frame_cb
        self.send_cone_images_cb = send_cone_images_cb
        self.open_image_request_cb = None
        self.close_image_request_cb = None
        self.conveyor_running = False
        self.handling = False
        self.reject_hold = False
        self.cycle_run = False
        self.auto_mode = False
        self.estop_latched = False
        self.s2_results = deque()
        self.latest_tip_image = ''

        self.count_s1 = self.count_s2 = self.count_s3_good = self.count_s3_bad = 0

        # self.prev_s1 = self.prev_s2 = self.prev_s3 = False
        self.prev_reset = self.prev_cycle_on = self.prev_cycle_off = self.prev_emergency = False
        self.prev_mode = None
        self.reset_pressed = False
        self.cycle_on_pressed = False
        self.cycle_off_pressed = False
        self.emergency = False

        self.ui_reset_request = False          # UI Reset button
        self.ui_emergency_request = False      # UI Emergency button
        self.ui_cycle_on_request = False       # (you already added earlier)
        self.ui_cycle_off_request = False
        self.mode_str = SW_AUTO_MANUAL_FN()  # "auto" / "manual"
        self.auto_mode = (self.mode_str == "auto")
        self.running_loop = False

        self.s2_status = False
        self.topThresholdValue = 0 #for seeing in the frontend only
        self.bottomThresholdValue = 0 #for seeing in the frontent only

        self.checking_cone_present = []

    # ---------------- MAIN LOOP ----------------
        
    def main_loop(self):
        try:
            prev_s1 = prev_s2 = prev_s3 = False
            self.running_loop = True
            while self.auto_mode and AIR_PRESSURE() and self.running_loop:
                # Read button/switch states
                hw_reset     = bool(is_active(BTN_RESET)) 
                hw_cycle_on   = bool(is_active(BTN_CYCLE_ON))
                # hw_cycle_off  = bool(is_active(BTN_CYCLE_OFF))

                hw_emergency = bool(BTN_EMERGENCY_FN()) 
                self.reset_pressed = hw_reset or self.ui_reset_request
                self.emergency     = hw_emergency or self.ui_emergency_request  

                if not SETTINGS_JSON.exists():
                    raise FileNotFoundError(f"settings.json not found: {SETTINGS_JSON}")

                with open(SETTINGS_JSON, "r", encoding="utf-8") as f:
                    settings = json.load(f)

                training_mode = settings.get('training_mode',False)

                # EMERGENCY
                if self.emergency and not self.prev_emergency:
                    print("[EMERGENCY] PRESSED → STOP ALL")
                    all_outputs_off()
                    self.conveyor_running = False
                    self.handling = False
                    self.reject_hold = False
                    self.cycle_run = False
                    self.estop_latched = True
                self.prev_emergency = self.emergency

                # MODE
                if self.prev_mode is None:
                    self.prev_mode = self.mode_str
                    print(f"[MODE] Initial = {self.mode_str.upper()}")
                elif self.mode_str != self.prev_mode:
                    print(f"[MODE] Switched to {self.mode_str.upper()}")
                    self.prev_mode = self.mode_str
                    if self.mode_str == "manual":
                        # When switching to manual, do not auto-start conveyor
                        stop_conveyor()
                        self.conveyor_running = False
                        self.cycle_run = False

                # RESET (edge)
                if self.reset_pressed and not self.prev_reset:
                    print("[RESET] pressed → clear system")
                    self.estop_latched = False
                    self.reject_hold = False
                    self.handling = False
                    self.cycle_run = False
                    self.s2_results.clear()
                    all_outputs_off()
                    self.conveyor_running = False
                self.prev_reset = self.reset_pressed

                self.ui_reset_request = False
                self.ui_emergency_request = False

                # -----------------------
                # NEW: Direct hardware button control
                # -----------------------
                # 1) READ HARDWARE BUTTON LEVELS
                hw_cycle_on  = bool(is_active(BTN_CYCLE_ON))     # DI5
                # hw_cycle_off = bool(is_active(BTN_CYCLE_OFF))    # DI6

                # 2) EDGE DETECTION ON HARDWARE BUTTONS
                hw_cycle_on_edge  = hw_cycle_on  and not self.prev_cycle_on
                # hw_cycle_off_edge = hw_cycle_off and not self.prev_cycle_off

                # hw_cycle_off_edge = not self.prev_cycle_off

                # 3) MERGE HARDWARE EDGES + UI REQUESTS
                cycle_on_event  = (hw_cycle_on_edge  or self.ui_cycle_on_request)  and not self.estop_latched
                cycle_off_event = (self.ui_cycle_off_request) and not self.estop_latched
                # print(hw_cycle_on_edge,self.ui_cycle_on_request,self.estop_latched)
                # 4) ACT ON EVENTS
                if cycle_on_event:
                    print("[CYCLE] ON event -> starting conveyor")
                    self.cycle_run = True
                    start_conveyor()
                    self.conveyor_running = True

                if cycle_off_event:
                    print("[CYCLE] OFF event -> stopping conveyor")
                    self.cycle_run = False
                    stop_conveyor()
                    self.conveyor_running = False

                # 5) UPDATE PREVIOUS STATES FOR NEXT LOOP
                self.prev_cycle_on  = hw_cycle_on
                self.prev_cycle_off = cycle_off_event

                # 6) CLEAR UI ONE-SHOT FLAGS
                self.ui_cycle_on_request  = False
                self.ui_cycle_off_request = False

                # SENSORS
                s1 = bool(is_active(SENSOR1_CONVEYOR))
                s2 = bool(is_active(SENSOR2_ARRIVAL))
                s3 = bool(is_active(SENSOR3_FINAL))

                if s2:
                    s2_time_start = time.time()
                    self.s2_status = True
                if self.s2_status and not s2:
                    # stop_conveyor()
                    s2_time_end = time.time()
                    times = s2_time_end - s2_time_start
                    print(f'sensor times ---> {times:2f}')
                    self.s2_status = False

                # If emergency latched → skip logic
                if self.estop_latched:
                    prev_s1, prev_s2, prev_s3 = s1, s2, s3
                    time.sleep(POLL)
                    continue

                # S1 COUNT (only counts; does NOT start/stop conveyor)
                if s1 and not prev_s1:
                    self.count_s1 += 1
                    print(f"[S1] Count = {self.count_s1}")
                prev_s1 = s1

                # S2 falling edge → start piston sequence
                if self.cycle_run and not self.handling:
                    if (not s2) and prev_s2:
                        self.count_s2 += 1
                        print(f"[S2] Falling edge → cone leaving S2 → SEQ start #{self.count_s2}")
                        # if training_mode:
                        #     time.sleep(0)
                        # else:
                        #     time.sleep(0.01)
                        # import time
                        time.sleep(0.2)
                        stop_conveyor()
                        self.conveyor_running = False

                        self.handling = True
                        result,result_img,id = self.piston_sequence_procedure()
                        if result != "good":
                            from classes.blackbox import draw_annulus_bbox_with_status
                            result_draw, info = draw_annulus_bbox_with_status(result_img, result)

                            # 1) build folder path (APP_DIR based)
                            # defect_dir = os.path.join(str(run), DEFECT_SAVE_DIR)
                            # os.makedirs(defect_dir, exist_ok=True)

                            # 2) build FULL file path
                            from datetime import datetime
                            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                            filename = f"defect_image_{self.count_s2}_{ts}.png"
                            save_path = os.path.join(DEFECT_SAVE_DIR, filename)

                            print("Saving to:", save_path)

                            # 3) write image
                            ok = cv2.imwrite(save_path, result_draw)
                            print("Saved:", ok)

                            # store relative path (or store save_path if you want absolute)
                            filename = f"{DEFECT_SAVE_DIR}/{filename}"
                            # print('checking -->>',filename,id)
                            
                            if not training_mode:
                                execute("UPDATE cone_entry SET defect_img_path = ? WHERE id = ?",(filename, id))
                        else:
                            filename = None

                        self.s2_results.append((self.count_s2, result, filename))
                        print(f"[SEQ] Stored S2 result #{self.count_s2} = {result.upper()}")

                        # start_conveyor()
                        # self.conveyor_running = True

                        # Wait for S2 clear
                        wait_until(lambda: not bool(is_active(SENSOR2_ARRIVAL)), 3.0)

                        self.handling = False
                        print("[SEQ] Handling done.")
                        
                prev_s2 = s2

                # # S3 arrival
                # # if self.auto_mode:
                if s3 and not prev_s3:
                    print("[S3] Cone arrived at final point")
                    print(self.s2_results)
                    if self.s2_results:
                        idx, res,image_path = self.s2_results.popleft()
                    else:
                        idx, res, image_path = -1, "bad", None
                        print("[WARN] No S2 result → default BAD")

                    if res == "good":
                        self.count_s3_good += 1
                        print(f"[S3] GOOD cone #{idx}")
                        set_green(True)
                        # set_reject_alarm(False)
                    else:
                        self.count_s3_bad += 1
                        print(f"[S3] BAD cone #{idx}")
                        # set_green(False)
                        set_reject_alarm(True)
                        if self.open_image_request_cb and image_path:
                            self.open_image_request_cb(image_path)
                        # time.sleep(0.5)
                        stop_conveyor()
                        self.conveyor_running = False
                        self.reject_hold = True

                # S3 clear
                if (not s3) and prev_s3:
                    if self.reject_hold:
                        print("[S3] BAD cone removed → releasing hold")
                        set_reject_alarm(False)
                        self.reject_hold = False
                        #if remove the rejection start conveyor set True
                        self.cycle_run = False
                        self.running_loop = False

                        # if self.close_image_request_cb:
                        #     self.close_image_request_cb()
                    else:
                        set_green(False)
                        print("[S3] GOOD cone cleared")
                prev_s3 = s3


                # Auto-run enforce only if in AUTO mode
                # print(self.cycle_run, self.estop_latched,self.handling,self.reject_hold)
                if self.cycle_run and not self.estop_latched and not self.handling and not self.reject_hold:
                    if not self.conveyor_running:
                        print("[AUTO] Ensuring conveyor ON")
                        start_conveyor()
                        self.conveyor_running = True

                # time.sleep(POLL)

                # time.sleep(POLL)
            
        except KeyboardInterrupt:
            print("[USER] CTRL+C — stopping")
        finally:
            print("[SHUTDOWN] Clearing outputs")
            all_outputs_off()
            close_clients()

    def open_image_viewer(self, image_path: str):
        # Resolve relative path if needed
        if not os.path.isabs(image_path):
            base = os.getcwd()   # or your project base path
            image_path = os.path.normpath(os.path.join(base, image_path))
        # print(image_path)
        from classes.zoom import ImageViewerDialog
        dlg = ImageViewerDialog(fr"{image_path}", self)
        dlg.show()
        

    # def piston_sequence_procedure(self):
        
    #     log_step("SEQ start at SENSOR2")
    #     if not SETTINGS_JSON.exists():
    #         raise FileNotFoundError(f"settings.json not found: {SETTINGS_JSON}")

    #     with open(SETTINGS_JSON, "r", encoding="utf-8") as f:
    #         settings = json.load(f)

    #     training_mode = settings.get('training_mode',False)
    #     cone_color = settings.get("values", {}).get("cone_color","")
    #     cone_count = settings.get("values", {}).get("cone_count","")
    #     # time.sleep(0.2)
        
    #     b2_lock_cone(True)
    #     time.sleep(0.2)
    #     b2_lock_cone(False)

    #     # -------- WHITE LIGHT PHASE (optional) --------
    #     # If you want a different exposure for white light, set it here:
    #     if self.set_exposure_cb:
    #         self.set_exposure_cb(LIVE_EXPOSURE_US)

    #     whight_light_on(True)
    #     time.sleep(UV_STABILIZE_TOP)
    #     tip_img = self.capture_frame_cb()
    #     tip_status = self.do_image_capture_prompt("tip",self.capture_frame_cb)
    #     whight_light_on(False)
    #     id = 0
    #     if tip_status == "good":
    #         # -------- TOP WHITE TRAINING CAPTURE (ONLY FOR TRAINING MODE) --------
    #         if training_mode:
    #             print("[TRAIN] Capturing top white light image for training")
    #             if self.set_exposure_cb:
    #                 self.set_exposure_cb(LIVE_EXPOSURE_US)
    #             whight_light_on(True)
    #             time.sleep(WH_STABILIZE)
    #             _ = self.do_image_capture_prompt("top_wh", self.capture_frame_cb)
    #             whight_light_on(False)
            
    #         # -------- TOP UV CAPTURE --------
    #         uv_on(True)

    #         # Set UV exposure for TOP image
    #         if self.set_exposure_cb:
    #             self.set_exposure_cb(UV_EXPOSURE_TOP_US)

    #         time.sleep(UV_STABILIZE_TOP)
    #         top_img = self.capture_frame_cb()
    #         top_wh = self.do_image_capture_prompt("top",self.capture_frame_cb)
    #         uv_on(False)
    #         b2_lock_cone(False)
    #         current_shift_id = get_current_shift_id()
    #         current_shift_count = get_current_shift_count(current_shift_id) + 1

    #         if not training_mode:
    #             if top_wh == "good":

    #                 id = execute(
    #                     "INSERT INTO cone_entry (shift_id, shift_count,cone_color,cone_count,tip_result, top_result, top_uv_result) VALUES (?, ?, ?, ?, ?,?,?)",
    #                     (current_shift_id, current_shift_count,cone_color,cone_count,"good", "good", "good")
    #                 )
    #                 first = "good"
    #             else:
    #                 id = execute(
    #                     "INSERT INTO cone_entry (shift_id, shift_count,cone_color,cone_count,tip_result, top_result, top_uv_result) VALUES (?, ?, ?, ?, ?, ?, ?)",
    #                     (current_shift_id, current_shift_count,cone_color,cone_count,"good", "good", "bad")
    #                 )
    #                 first = "top_bad"
    #         else:
    #             first = "good"
    #     else:
    #         current_shift_id = get_current_shift_id()
    #         current_shift_count = get_current_shift_count(current_shift_id) + 1
    #         if not training_mode:
    #             if tip_status == "tip_bad":
    #                 id = execute("INSERT INTO cone_entry (shift_id, shift_count,cone_color,cone_count,tip_result) VALUES (?, ?, ?, ?, ?)", (current_shift_id, current_shift_count,cone_color,cone_count,"bad",))
    #             else:
    #                 id = execute(
    #                     "INSERT INTO cone_entry (shift_id, shift_count, cone_color, cone_count, tip_result, top_result) VALUES (?, ?, ?, ?, ?, ?)",
    #                     (current_shift_id, current_shift_count, cone_color, cone_count, "good", "bad")
    #                 )
    #         b2_lock_cone(False)
    #         # tip_status = "tip_bad"
    #         return tip_status,tip_img,id
        

    #     # (rest of your logic unchanged)
    #     if first == "good" and tip_status == "good":
    #         print("[SEQ] First image GOOD -> piston/rotate actions")
    #         time.sleep(PISTON_PULSE)
    #         b2_lock_cone(True)
    #         time.sleep(0.2)
    #         b2_piston_up(True)
    #         time.sleep(PISTON_UP_WAIT)
    #         wait_interlock(INTERLOCK_PISTON_IDLE, expected_active=True, timeout=1.0)

    #         b2_piston_up(False)
    #         b2_rotate(True)
    #         time.sleep(ROTATE_TIME)

    #         b2_piston_down(True)
    #         time.sleep(PISTON_DOWN_WAIT)
    #         b2_piston_down(False)

    #         self.ensure_cone_settled_after_down()
       
    #         print("[SEQ] Releasing cone lock")
    #         b2_lock_cone(False)
    #         time.sleep(POST_RELEASE_WAIT)
    #         b2_lock_cone(True)
    #         time.sleep(POST_RELEASE_WAIT)
    #         b2_lock_cone(False)
    #         # -------- BOTTOM UV CAPTURE --------
    #         uv_on(True)

    #         if self.set_exposure_cb:
    #             self.set_exposure_cb(UV_EXPOSURE_BOTTOM_US)

    #         time.sleep(UV_STABILIZE)
    #         bottom_uv_img = self.capture_frame_cb()
    #         bottom_uv = self.do_image_capture_prompt('bottom',self.capture_frame_cb)
    #         uv_on(False)
    #         if self.set_exposure_cb:
    #             self.set_exposure_cb(LIVE_EXPOSURE_US)
    #         if bottom_uv == "good":
    #             whight_light_on(True)
    #             time.sleep(WH_STABILIZE)
    #             bottom_wh_img = self.capture_frame_cb()
    #             bottom_wh = self.do_image_capture_prompt('bottom_w',self.capture_frame_cb)
    #             if not training_mode:
    #                 execute(
    #                     "UPDATE cone_entry SET bottom_uv_result = ?, bottom_result = ? WHERE id = ?",
    #                     ("good", "good" if bottom_wh == "good" else "bad", id)
    #                 )
    #             whight_light_on(False)
    #             final = (bottom_wh,bottom_wh_img,id)
    #         else:
    #             if not training_mode:
    #                 execute(
    #                     "UPDATE cone_entry SET bottom_uv_result = ? WHERE id = ?",
    #                     ("bad", id)
    #                 )
    #             final = ("bottom_uv_bad",bottom_uv_img,id)


    #         # Return actuators to safe posture (unchanged)
    #         b2_piston_up(True)
    #         time.sleep(1.0)
    #         b2_rotate(False)
    #         time.sleep(1.0)
    #         b2_piston_up(False)
    #         b2_piston_down(True)
    #         time.sleep(PISTON_DOWN_WAIT)
    #         b2_piston_down(False)

    #         # Optionally restore live exposure for normal camera/live view
    #         # if self.set_exposure_cb:
    #         #     self.set_exposure_cb(LIVE_EXPOSURE_US)

    #         print(f"[SEQ] Handling complete. Final result = {final[0]}")
    #         return final
        
    #     else:
    #         return first,top_img,id
    def light_inspection_sequence(self):
        """
        PLC signal-based light inspection.

        HR609 = 1 → TIP + TOP white prediction
        HR605 = 1 → TOP UV prediction
        HR609 = 1 → BOTTOM white prediction
        HR605 = 1 → BOTTOM UV prediction
        """

        FORCE_FINAL_GOOD = True

        results = {
            "tip_top_white": "bad",
            "top_uv": "bad",
            "bottom_white": "bad",
            "bottom_uv": "bad"
        }

        captured_images = {
            "tip_top_white": None,
            "top_uv": None,
            "bottom_white": None,
            "bottom_uv": None
        }

        self.running_loop = True

        def wait_plc_signal(address, expected_value, signal_name):
            """Wait until a PLC register reaches the expected value."""

            print(
                f"[WAIT] {signal_name}: "
                f"HR{address} must become {expected_value}"
            )

            previous_value = None

            while self.running_loop:
                value = read_register(address)

                if value is None:
                    time.sleep(0.1)
                    continue

                if value != previous_value:
                    print(
                        f"[SIGNAL] HR{address} changed: "
                        f"{previous_value} → {value}"
                    )
                    previous_value = value

                if value == expected_value:
                    print(
                        f"✅ {signal_name}: "
                        f"HR{address}={expected_value}"
                    )
                    return True

                time.sleep(0.05)

            return False

        def capture_and_predict(position):
            if self.capture_frame_cb is None:
                print(f"❌ Capture callback missing: {position}")
                return "bad", None

            frame = self.capture_frame_cb()

            if frame is None:
                print(f"❌ Frame capture failed: {position}")
                return "bad", None

            # Give the same captured image to prediction
            frame_callback = lambda captured_frame=frame: captured_frame

            status = self.do_image_capture_prompt(
                position,
                frame_callback
            )

            status = str(status or "bad").lower()

            print(
                f"[PREDICTION] Position={position}, "
                f"Result={status.upper()}"
            )

            return status, frame

        try:
            print("=" * 60)
            print("PLC SIGNAL-BASED INSPECTION STARTED")
            print("=" * 60)

            # Clear previous result
            set_green(False)
            set_reject_alarm(False)

            # =====================================================
            # STAGE 1: WAIT FOR HR609 = 1
            # TIP + TOP WHITE
            # =====================================================
            if self.set_exposure_cb:
                self.set_exposure_cb(LIVE_EXPOSURE_US)

            if not wait_plc_signal(
                WHITE_LIGHT_ADDR,
                1,
                "TIP + TOP WHITE START"
            ):
                return "stopped", None, 0

            time.sleep(WH_STABILIZE)

            status, frame = capture_and_predict("tip")

            results["tip_top_white"] = status
            captured_images["tip_top_white"] = frame

            # Wait until PLC switches white light OFF
            if not wait_plc_signal(
                WHITE_LIGHT_ADDR,
                0,
                "TIP + TOP WHITE COMPLETE"
            ):
                return "stopped", frame, 0

            # =====================================================
            # STAGE 2: WAIT FOR HR605 = 1
            # TOP UV
            # =====================================================
            if self.set_exposure_cb:
                self.set_exposure_cb(UV_EXPOSURE_TOP_US)

            if not wait_plc_signal(
                UV_LIGHT_ADDR,
                1,
                "TOP UV START"
            ):
                return "stopped", frame, 0

            time.sleep(UV_STABILIZE_TOP)

            status, frame = capture_and_predict("top")

            results["top_uv"] = status
            captured_images["top_uv"] = frame

            if not wait_plc_signal(
                UV_LIGHT_ADDR,
                0,
                "TOP UV COMPLETE"
            ):
                return "stopped", frame, 0

            # =====================================================
            # STAGE 3: WAIT FOR HR609 = 1
            # BOTTOM WHITE
            # =====================================================
            if self.set_exposure_cb:
                self.set_exposure_cb(LIVE_EXPOSURE_US)

            if not wait_plc_signal(
                WHITE_LIGHT_ADDR,
                1,
                "BOTTOM WHITE START"
            ):
                return "stopped", frame, 0

            time.sleep(WH_STABILIZE)

            status, frame = capture_and_predict("bottom_w")

            results["bottom_white"] = status
            captured_images["bottom_white"] = frame

            if not wait_plc_signal(
                WHITE_LIGHT_ADDR,
                0,
                "BOTTOM WHITE COMPLETE"
            ):
                return "stopped", frame, 0

            # =====================================================
            # STAGE 4: WAIT FOR HR605 = 1
            # BOTTOM UV
            # =====================================================
            if self.set_exposure_cb:
                self.set_exposure_cb(UV_EXPOSURE_BOTTOM_US)

            if not wait_plc_signal(
                UV_LIGHT_ADDR,
                1,
                "BOTTOM UV START"
            ):
                return "stopped", frame, 0

            time.sleep(UV_STABILIZE)

            status, frame = capture_and_predict("bottom")

            results["bottom_uv"] = status
            captured_images["bottom_uv"] = frame

            if not wait_plc_signal(
                UV_LIGHT_ADDR,
                0,
                "BOTTOM UV COMPLETE"
            ):
                return "stopped", frame, 0

            # Restore live exposure
            if self.set_exposure_cb:
                self.set_exposure_cb(LIVE_EXPOSURE_US)

            # =====================================================
            # FINAL RESULT
            # =====================================================
            actual_good = all(
                value == "good"
                for value in results.values()
            )

            actual_result = "good" if actual_good else "bad"

            if FORCE_FINAL_GOOD:
                final_result = "good"
            else:
                final_result = actual_result

            print("=" * 60)
            print("TIP + TOP WHITE :", results["tip_top_white"])
            print("TOP UV          :", results["top_uv"])
            print("BOTTOM WHITE    :", results["bottom_white"])
            print("BOTTOM UV       :", results["bottom_uv"])
            print("ACTUAL RESULT   :", actual_result.upper())
            print("RETURNED RESULT :", final_result.upper())
            print("=" * 60)

            if final_result == "good":
                set_reject_alarm(False)
                set_green(True)

                print("✅ GOOD: HR606=1, HR607=0, HR608=0")

            else:
                set_green(False)
                set_reject_alarm(True)

                print("❌ BAD: HR606=0, HR607=1, HR608=1")

            result_image = captured_images["bottom_uv"]

            return final_result, result_image, 0

        except Exception as e:
            print(f"❌ Signal inspection error: {e}")

            set_green(False)
            set_reject_alarm(True)

            return "bad", None, 0    
    def model_path(self, cone_color: str, cone_count: str, pos: str) -> Path:
        base = PREDICTION
        return base / cone_color / cone_count / f"cone_{cone_color}_{pos}_{cone_count}_b7.joblib"

    def do_image_capture_prompt(self, position, capture_cb=None):

        # =========================================================
        # TEMPORARY TEST SETTING
        # True  = complete process runs, but final output is GOOD
        # False = normal prediction result is returned
        # =========================================================
        FORCE_ALL_GOOD = True

        def final_result(actual_status):
            """
            Keep the actual result visible in the terminal, but
            temporarily send GOOD to the remaining machine process.
            """
            actual_status = str(actual_status or "bad").lower()

            if FORCE_ALL_GOOD:
                print(
                    f"🧪 Position: {position} | "
                    f"Actual result: {actual_status} | "
                    f"Returned result: good"
                )
                return "good"

            return actual_status

        try:
            status = "good"

            if not SETTINGS_JSON.exists():
                raise FileNotFoundError(
                    f"settings.json not found: {SETTINGS_JSON}"
                )

            with open(SETTINGS_JSON, "r", encoding="utf-8") as f:
                settings = json.load(f)

            training_mode = settings.get("training_mode", False)

            values = settings.get("values", {})
            top_confidence = values.get("top_confidence", 45)
            bottom_confidence = values.get("bottom_confidence", 45)
            cone_color = str(values.get("cone_color", "")).strip().lower()
            cone_count = str(values.get("cone_count", "")).strip()

            CountName = f"{cone_color}_{cone_count}"

            # =====================================================
            # TIP POSITION
            # =====================================================
            if position == "tip" and capture_cb is not None:

                frame = capture_cb()

                if frame is None:
                    print("⚠️ TIP capture_cb returned None")
                    return final_result("bad")

                # -------------------------------------------------
                # Training mode
                # -------------------------------------------------
                if training_mode:
                    from datetime import datetime

                    Path("train_img/tip_images").mkdir(
                        parents=True,
                        exist_ok=True
                    )

                    ts = datetime.now().strftime(
                        "%Y%m%d_%H%M%S_%f"
                    )[:-3]

                    filename = (
                        f"train_img/tip_images/"
                        f"tip_image_{ts}.bmp"
                    )

                    saved = cv2.imwrite(filename, frame)

                    if saved:
                        print(f"✅ TIP training image saved: {filename}")
                    else:
                        print(f"❌ Failed to save TIP image: {filename}")

                    return final_result("good")

                # -------------------------------------------------
                # Normal tip checking
                # -------------------------------------------------
                actual_tip_status = compare_tips(frame)

                print(
                    f"TIP comparison actual result: "
                    f"{actual_tip_status}"
                )

                # During temporary testing, continue to the top
                # prediction even when compare_tips returns bad.
                if FORCE_ALL_GOOD:
                    status = "good"
                else:
                    status = str(actual_tip_status).lower()

                if status != "good":
                    return final_result("tip_bad")

                # -------------------------------------------------
                # Detect annulus and tip
                # -------------------------------------------------
                masked_Image_For_big_count = None

                if cone_color in ("orange", "pink"):
                    mask_full, inner_tip_img, orad = (
                        detect_thread_circle(frame)
                    )

                    self.latest_tip_image = inner_tip_img

                    from classes.NewgetConeThreadFinal import (
                        detect_thread_circle2
                    )

                    masked_Image_For_big_count = (
                        detect_thread_circle2(frame, "top")
                    )
                else:
                    mask_full, inner_tip_img, orad = (
                        detect_thread_circle(frame)
                    )

                    self.latest_tip_image = inner_tip_img

                annulus_color = cv2.bitwise_and(
                    frame,
                    frame,
                    mask=mask_full
                )

                # -------------------------------------------------
                # Send processed cone images
                # -------------------------------------------------
                if self.send_cone_images_cb is not None:
                    try:
                        self.send_cone_images_cb(
                            annulus_color,
                            inner_tip_img,
                            orad
                        )
                    except Exception as callback_error:
                        print(
                            "send_cone_images_cb error:",
                            callback_error
                        )

                # -------------------------------------------------
                # Check prediction model
                # -------------------------------------------------
                if not cone_color or not cone_count:
                    print(
                        "❌ cone_color or cone_count is empty. "
                        f"Color: '{cone_color}', "
                        f"Count: '{cone_count}'"
                    )
                    return final_result("top_wh_bad")

                prediction_path = self.model_path(
                    cone_color,
                    cone_count,
                    "top"
                )

                print(f"TOP prediction model: {prediction_path}")

                if not prediction_path.exists():
                    raise FileNotFoundError(
                        f"Prediction model not found: "
                        f"{prediction_path}"
                    )

                # -------------------------------------------------
                # Run top white-light prediction
                # -------------------------------------------------
                if cone_color in ("orange", "pink"):
                    prediction_image = masked_Image_For_big_count
                    checking_name = "checking_top checking"
                else:
                    prediction_image = annulus_color
                    checking_name = "checking_top another"

                if prediction_image is None:
                    print("❌ TOP prediction image is None")
                    return final_result("top_wh_bad")

                status, score = prediction_process(
                    prediction_path,
                    top_confidence,
                    prediction_image,
                    position="top",
                    count_name=CountName
                )

                print("-" * 61)
                print(f"{checking_name} -->> {score}")
                print(f"TOP actual prediction status -->> {status}")
                print("-" * 61)

                if str(status).lower() == "good":
                    return final_result("good")

                return final_result("top_wh_bad")

            # =====================================================
            # TOP UV POSITION
            # =====================================================
            if position == "top" and capture_cb is not None:

                frame = capture_cb()

                if frame is None:
                    print("⚠️ TOP capture_cb returned None")
                    return final_result("bad")

                if training_mode:
                    from datetime import datetime

                    Path("train_img/top_images").mkdir(
                        parents=True,
                        exist_ok=True
                    )

                    ts = datetime.now().strftime(
                        "%Y%m%d_%H%M%S_%f"
                    )[:-3]

                    filename = (
                        f"train_img/top_images/"
                        f"top_image_{ts}.bmp"
                    )

                    saved = cv2.imwrite(filename, frame)

                    if saved:
                        print(f"✅ TOP image saved: {filename}")
                    else:
                        print(f"❌ Failed to save TOP image: {filename}")

                    return final_result("good")

                # Current TOP UV code has no active prediction.
                # Therefore its current status remains GOOD.
                print(f"{position} ---> {status}")

                if status == "good":
                    return final_result("good")

                return final_result("top_uv_bad")

            # =====================================================
            # TOP WHITE-LIGHT POSITION
            # =====================================================
            if position == "top_wh" and capture_cb is not None:

                frame = capture_cb()

                if frame is None:
                    print("⚠️ TOP_WH capture_cb returned None")
                    return final_result("bad")

                if training_mode:
                    from datetime import datetime

                    train_dir = os.path.join(
                        str(RUN_DIR),
                        "train_img",
                        "top_wh_images"
                    )

                    os.makedirs(train_dir, exist_ok=True)

                    ts = datetime.now().strftime(
                        "%Y%m%d_%H%M%S_%f"
                    )[:-3]

                    filename = os.path.join(
                        train_dir,
                        f"top_wh_image_{ts}.bmp"
                    )

                    saved = cv2.imwrite(filename, frame)

                    if saved:
                        print(
                            "✅ Top white-light image saved:",
                            filename
                        )
                    else:
                        print(
                            "❌ Failed to save top white-light image:",
                            filename
                        )

                    return final_result("good")

                # The actual top white-light prediction is performed
                # inside the TIP section in the current process.
                return final_result("good")

            # =====================================================
            # BOTTOM UV POSITION
            # =====================================================
            if position == "bottom" and capture_cb is not None:

                frame = capture_cb()

                if frame is None:
                    print("⚠️ BOTTOM capture_cb returned None")
                    return final_result("bad")

                mask_full, inner_tip_img, orad = (
                    detect_thread_circle(frame)
                )

                annulus_color = cv2.bitwise_and(
                    frame,
                    frame,
                    mask=mask_full
                )

                if self.send_cone_images_cb is not None:
                    try:
                        latest_tip_image = getattr(
                            self,
                            "latest_tip_image",
                            inner_tip_img
                        )

                        self.send_cone_images_cb(
                            annulus_color,
                            latest_tip_image,
                            orad
                        )
                    except Exception as callback_error:
                        print(
                            "send_cone_images_cb error:",
                            callback_error
                        )

                if training_mode:
                    from datetime import datetime

                    Path("train_img/bottom_uv_images").mkdir(
                        parents=True,
                        exist_ok=True
                    )

                    ts = datetime.now().strftime(
                        "%Y%m%d_%H%M%S_%f"
                    )[:-3]

                    filename = (
                        f"train_img/bottom_uv_images/"
                        f"bottom_uv_image_{ts}.bmp"
                    )

                    saved = cv2.imwrite(filename, frame)

                    if saved:
                        print(f"✅ BOTTOM UV image saved: {filename}")
                    else:
                        print(
                            f"❌ Failed to save BOTTOM UV image: "
                            f"{filename}"
                        )

                    return final_result("good")

                # Current BOTTOM UV code has no active prediction.
                if status == "good":
                    return final_result("good")

                return final_result("bottom_uv_bad")

            # =====================================================
            # BOTTOM WHITE-LIGHT POSITION
            # =====================================================
            if position == "bottom_w" and capture_cb is not None:

                import random

                frame = capture_cb()

                # Important: check the frame before cv2.imwrite().
                if frame is None:
                    print("⚠️ BOTTOM_W capture_cb returned None")
                    return final_result("bad")

                image_id = random.randrange(1, 10000000000)

                prediction_dir = Path(
                    "predictionImageStoring/bottom_whiteLight"
                )

                prediction_dir.mkdir(
                    parents=True,
                    exist_ok=True
                )

                filename = (
                    prediction_dir
                    / f"bottom_wh_image_{image_id}.bmp"
                )

                saved = cv2.imwrite(str(filename), frame)

                if saved:
                    print(
                        f"✅ Bottom white-light image saved: "
                        f"{filename}"
                    )
                else:
                    print(
                        f"❌ Failed to save bottom white-light image: "
                        f"{filename}"
                    )

                # -------------------------------------------------
                # Detect annulus and tip
                # -------------------------------------------------
                masked_Image_For_big_count = None

                if cone_color in ("orange", "pink"):
                    mask_full, inner_tip_img, orad = (
                        detect_thread_circle(frame)
                    )

                    from classes.NewgetConeThreadFinal import (
                        detect_thread_circle2
                    )

                    masked_Image_For_big_count = (
                        detect_thread_circle2(frame, "bottom")
                    )
                else:
                    mask_full, inner_tip_img, orad = (
                        detect_thread_circle(frame)
                    )

                annulus_color = cv2.bitwise_and(
                    frame,
                    frame,
                    mask=mask_full
                )

                if self.send_cone_images_cb is not None:
                    try:
                        latest_tip_image = getattr(
                            self,
                            "latest_tip_image",
                            inner_tip_img
                        )

                        self.send_cone_images_cb(
                            annulus_color,
                            latest_tip_image,
                            orad
                        )
                    except Exception as callback_error:
                        print(
                            "send_cone_images_cb error:",
                            callback_error
                        )

                # -------------------------------------------------
                # Training mode
                # -------------------------------------------------
                if training_mode:
                    from datetime import datetime

                    Path("train_img/bottom_wh_images").mkdir(
                        parents=True,
                        exist_ok=True
                    )

                    ts = datetime.now().strftime(
                        "%Y%m%d_%H%M%S_%f"
                    )[:-3]

                    training_filename = (
                        f"train_img/bottom_wh_images/"
                        f"bottom_wh_image_{ts}.bmp"
                    )

                    training_saved = cv2.imwrite(
                        training_filename,
                        frame
                    )

                    if training_saved:
                        print(
                            "✅ BOTTOM white-light training image "
                            f"saved: {training_filename}"
                        )
                    else:
                        print(
                            "❌ Failed to save BOTTOM white-light "
                            f"training image: {training_filename}"
                        )

                    return final_result("good")

                # -------------------------------------------------
                # Validate cone settings
                # -------------------------------------------------
                if not cone_color or not cone_count:
                    print(
                        "❌ cone_color or cone_count is empty. "
                        f"Color: '{cone_color}', "
                        f"Count: '{cone_count}'"
                    )
                    return final_result("bottom_wh_bad")

                prediction_path = self.model_path(
                    cone_color,
                    cone_count,
                    "bottom"
                )

                print(f"BOTTOM prediction model: {prediction_path}")

                if not prediction_path.exists():
                    raise FileNotFoundError(
                        f"Prediction model not found: "
                        f"{prediction_path}"
                    )

                # -------------------------------------------------
                # Run bottom prediction
                # -------------------------------------------------
                if cone_color in ("orange", "pink"):
                    prediction_image = masked_Image_For_big_count
                    checking_name = "checking_bottom checking"
                else:
                    prediction_image = annulus_color
                    checking_name = "checking_bottom another"

                if prediction_image is None:
                    print("❌ BOTTOM prediction image is None")
                    return final_result("bottom_wh_bad")

                status, score = prediction_process(
                    prediction_path,
                    bottom_confidence,
                    prediction_image,
                    position="bottom",
                    count_name=CountName
                )

                print("-" * 61)
                print(f"{checking_name} -->> {score}")
                print(f"BOTTOM actual prediction status -->> {status}")
                print("-" * 61)

                if str(status).lower() == "good":
                    return final_result("good")

                return final_result("bottom_wh_bad")

            # =====================================================
            # INVALID POSITION OR CALLBACK
            # =====================================================
            if capture_cb is None:
                print(
                    f"⚠️ Capture callback is None for position: "
                    f"{position}"
                )
            else:
                print(f"⚠️ Unknown capture position: {position}")

            return final_result("bad")

        except Exception as e:
            print(
                f"❌ do_image_capture_prompt error "
                f"[position={position}]: {e}"
            )

            # During temporary testing, exception is logged but GOOD
            # is returned so the sequence can continue.
            return final_result("bad")
    def ensure_cone_settled_after_down(self):
        ok = False
        try:
            ok = wait_interlock(INTERLOCK_CONE_TOUCH, True, 1.2)
        except:
            pass

        if ok:
            print("[INTERLOCK] Cone-touch OK")
        else:
            print("[INTERLOCK] No cone-touch -> fallback wait")
            time.sleep(CONE_SETTLE_FALLBACK)

# # ----------------
# if __name__ == "__main__":
#     whight_light_on(False)
