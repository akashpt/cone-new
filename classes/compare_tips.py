import cv2
import numpy as np
import os
from math import sqrt
from pathlib import Path
from paths import *
import json

DELTA_E_THRESH = 4.0
# BAD_SAVE_DIR = "bad_tip_results"
# os.makedirs(BAD_SAVE_DIR, exist_ok=True)
# TIP_COLLECTION = "tip_colors"
# os.makedirs(TIP_COLLECTION, exist_ok=True)

# -----------------------------
# Detect inner circle (unchanged)
# -----------------------------
def detect_inner_circle(img):
    if img is None:
        raise FileNotFoundError("Image not found")
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    def detect_outer_hough(gray_img, h, w):
        blur = cv2.GaussianBlur(gray_img, (7,7), 2)
        circles = cv2.HoughCircles(
            blur, cv2.HOUGH_GRADIENT, dp=1.0, minDist=200,
            param1=100, param2=50,
            minRadius=int(min(h,w)*0.2), maxRadius=int(min(h,w)*0.48)
        )
        if circles is None:
            return None
        circles = np.uint16(np.around(circles[0]))
        largest = max(circles, key=lambda c: c[2])
        return (int(largest[0]), int(largest[1]), int(largest[2]))

    outer = detect_outer_hough(gray, h, w)

    if outer is None:
        blur = cv2.GaussianBlur(gray, (9,9), 2)
        _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11,11))
        th = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel, iterations=2)
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if cnts:
            cx_img, cy_img = w//2, h//2
            best = None; best_score = -1
            for c in cnts:
                area = cv2.contourArea(c)
                if area < 1000:
                    continue
                (cx_c, cy_c), r_c = cv2.minEnclosingCircle(c)
                dist = np.hypot(cx_c - cx_img, cy_c - cy_img)
                score = area - dist * area * 0.0005
                if score > best_score:
                    best_score = score
                    best = (int(cx_c), int(cy_c), int(r_c))
            if best:
                outer = best

    if outer is None:
        outer = (w//2, h//2, int(min(w,h)*0.33))

    ox, oy, orad = outer

    roi_r = max(20, int(orad * 0.25))
    x1 = max(0, ox - roi_r); y1 = max(0, oy - roi_r)
    x2 = min(w-1, ox + roi_r); y2 = min(h-1, oy + roi_r)
    roi = gray[y1:y2, x1:x2]

    inner = None
    if roi.size:
        roi_blur = cv2.GaussianBlur(roi, (5,5), 0)
        _, roi_th = cv2.threshold(roi_blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        k2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5,5))
        roi_th = cv2.morphologyEx(roi_th, cv2.MORPH_OPEN, k2, iterations=2)
        cnts2, _ = cv2.findContours(roi_th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None; best_score = -1
        cx_roi = roi.shape[1]//2; cy_roi = roi.shape[0]//2
        for c in cnts2:
            area = cv2.contourArea(c)
            if area < 30:
                continue
            (cx2, cy2), r2 = cv2.minEnclosingCircle(c)
            dist = np.hypot(cx2 - cx_roi, cy2 - cy_roi)
            score = area - dist*5
            if score > best_score:
                best_score = score
                best = (int(cx2 + x1), int(cy2 + y1), int(max(3, r2)))
        if best:
            inner = best

    if inner is None:
        inner = (ox, oy, max(3, int(orad * 0.12)))

    ix, iy, irad = inner

    # force minimum
    if irad < 157:
        irad = 158

    # ensure inner < outer
    if irad >= orad:
        irad = max(3, int(orad * 0.12))
        if irad < 157:
            irad = 158

    return (ix, iy, irad)


# -----------------------------
# Mean LAB inside circle
# -----------------------------
def mean_lab_circle(img, circle):
    x, y, r = circle
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.circle(mask, (x, y), r, 255, -1)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    idx = mask == 255
    return tuple(lab[:,:,i][idx].mean() for i in range(3))

# -----------------------------
# ΔE (Lab Euclidean)
# -----------------------------
def delta_e(l1, l2):
    return sqrt(sum((l1[i] - l2[i])**2 for i in range(3)))

# -----------------------------
# MAIN: test vs GOOD FOLDER
# -----------------------------
def compare_tips(test_img):
    test = test_img
    if test is None:
        raise FileNotFoundError("Test image not found")

    # -------------------------------------------------
    # ✅ Load selected GOOD tip images from settings.json
    # -------------------------------------------------
    if not SETTINGS_JSON.exists():
        raise FileNotFoundError(f"settings.json not found: {SETTINGS_JSON}")

    with open(SETTINGS_JSON, "r", encoding="utf-8") as f:
        settings = json.load(f)

    selected_names = settings.get("values", {}).get("tip_images_selected", [])

    if not selected_names:
        return "bad"
        raise RuntimeError("No selected GOOD tip images in settings (tip_images_selected is empty)")
    
    tip_confidence = settings.get("values", {}).get("tip_confidence",5.0)

    # Build ref_files ONLY from selected list
    exts = {".bmp", ".png", ".jpg", ".jpeg"}
    ref_files = []
    for name in selected_names:
        p = GOOD_TIP / name
        if p.exists() and p.suffix.lower() in exts:
            ref_files.append(p)
        else:
            print(f"⚠ Missing/invalid selected tip image: {p}")

    ref_files = sorted(ref_files)

    if not ref_files:
        return "bad"
        raise RuntimeError("Selected tip images not found in GOOD_TIP folder!")

    # -------------------------------------------------
    # ✅ Your existing logic continues (unchanged)
    # -------------------------------------------------
    circ_test = detect_inner_circle(test)
    lab_test = mean_lab_circle(test, circ_test)

    dEs = []
    for rf in ref_files:
        ref = cv2.imread(str(rf))
        if ref is None:
            continue

        circ_ref = detect_inner_circle(ref)
        lab_ref = mean_lab_circle(ref, circ_ref)

        dE = delta_e(lab_ref, lab_test)
        dEs.append((rf.name, dE))

    if not dEs:
        raise RuntimeError("All selected references failed to load/read!")

    min_name, min_dE = min(dEs, key=lambda x: x[1])
    print("tip_value -->", min_dE)

    status = "good" if min_dE <= int(tip_confidence) else "bad"

    # print("\nBEST MATCH :", min_name)
    # print(f"MIN ΔE     : {min_dE:.2f}")
    # print("FINAL      :", status.upper())

    # # Save BAD image
    if status == "bad":
        marked = test.copy()
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # ms precision
        filename = f"tip_image_{ts}"
        tip_path = os.path.join(GOOD_TIP,filename+'.bmp')
        cv2.imwrite(tip_path, marked)
    #     x, y, r = circ_test
    #     cv2.rectangle(marked, (x-r, y-r), (x+r, y+r), (0,0,255), 4)
    #     out_path = os.path.join(BAD_SAVE_DIR, filename+'.jpg')
    #     cv2.imwrite(out_path, marked)
    #     print("❌ Saved BAD image:", out_path)

    # return status, min_dE, dEs
    return status

# -----------------------------
# RUN
# -----------------------------
# if __name__ == "__main__":
#     # GOOD_TIPS_FOLDER = r"D:\Texa\cone_inspection\prediction\con_tip\good_tips"
#     TEST_IMAGE = r"D:\Texa\cone_inspection\lambhodhara\tip\capture\capture_20251206_163459_698_2590x1942.bmp"
#     test_img_path = cv2.imread(str(TEST_IMAGE))
#     status = compare_tips(test_img_path)
#     print(status)


def detect_thread_circle(img):
    if img is None:
        raise FileNotFoundError("Image not found")

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # ---------- Hough helper ----------
    def detect_outer_hough(gray_img, h, w):
        blur = cv2.GaussianBlur(gray_img, (7,7), 2)
        circles = cv2.HoughCircles(
            blur, cv2.HOUGH_GRADIENT, dp=1.0, minDist=200,
            param1=100, param2=50,
            minRadius=int(min(h,w)*0.2), maxRadius=int(min(h,w)*0.48)
        )
        if circles is None:
            return None
        circles = np.uint16(np.around(circles[0]))
        largest = max(circles, key=lambda c: c[2])
        return (int(largest[0]), int(largest[1]), int(largest[2]))

    outer = detect_outer_hough(gray, h, w)

    # ---------- Fallback methods ----------
    if outer is None:
        blur = cv2.GaussianBlur(gray, (9,9), 2)
        _, th = cv2.threshold(
            blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11,11))
        th = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel, iterations=2)
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if cnts:
            cx_img, cy_img = w//2, h//2
            best = None; best_score = -1
            for c in cnts:
                area = cv2.contourArea(c)
                if area < 1000: continue
                (cx_c, cy_c), r_c = cv2.minEnclosingCircle(c)
                dist = np.hypot(cx_c - cx_img, cy_c - cy_img)
                score = area - dist * area * 0.0005
                if score > best_score:
                    best_score = score
                    best = (int(cx_c), int(cy_c), int(r_c))
            if best:
                outer = best

    if outer is None:
        outer = (w//2, h//2, int(min(w,h)*0.33))

    ox, oy, orad = outer

    # ---------- INNER detection ----------
    roi_r = max(20, int(orad * 0.25))
    x1 = max(0, ox - roi_r); y1 = max(0, oy - roi_r)
    x2 = min(w-1, ox + roi_r); y2 = min(h-1, oy + roi_r)
    roi = gray[y1:y2, x1:x2]

    inner = None
    if roi.size:
        roi_blur = cv2.GaussianBlur(roi, (5,5), 0)
        _, roi_th = cv2.threshold(roi_blur, 0, 255,
                                  cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        k2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5,5))
        roi_th = cv2.morphologyEx(roi_th, cv2.MORPH_OPEN, k2, iterations=2)

        cnts2, _ = cv2.findContours(roi_th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None; best_score = -1
        cx_roi = roi.shape[1]//2; cy_roi = roi.shape[0]//2

        for c in cnts2:
            area = cv2.contourArea(c)
            if area < 30: continue
            (cx2, cy2), r2 = cv2.minEnclosingCircle(c)
            dist = np.hypot(cx2 - cx_roi, cy2 - cy_roi)
            score = area - dist*5
            if score > best_score:
                best_score = score
                best = (int(cx2 + x1), int(cy2 + y1), int(max(3, r2)))
        if best:
            inner = best

    if inner is None:
        inner = (ox, oy, max(3, int(orad * 0.12)))

    ix, iy, irad = inner

    # force minimum
    if irad < 157:
        irad = 145

    # ensure valid hierarchy
    if irad >= orad:
        irad = max(3, int(orad * 0.12))
        if irad < 157:
            irad = 145

    # ---------- Create annulus mask ----------
    mask_full = np.zeros((h, w), dtype=np.uint8)
    cv2.circle(mask_full, (ox, oy), orad, 255, thickness=-1)
    inner_tip_img = None
    # if irad < 165:
    if irad < 250:
        cv2.circle(mask_full, (ix, iy), irad, 0, thickness=-1)
        # ---------- Inner tip crop ----------
        x1 = max(0, ix - irad)
        y1 = max(0, iy - irad)
        x2 = min(w-1, ix + irad)
        y2 = min(h-1, iy + irad)

        inner_tip_img = img[y1:y2, x1:x2].copy()
    

    # ---------- return data ----------
    return mask_full, inner_tip_img,orad

