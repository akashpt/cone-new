import cv2
import numpy as np
from pathlib import Path
from path import *

def detect_thread_circle(img):
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
        irad = 145

    # ensure inner < outer
    if irad >= orad:
        irad = max(3, int(orad * 0.12))
        if irad < 157:
            irad = 145

    # ---------- Create annulus mask (outer minus inner) ----------
    mask_full = np.zeros((h, w), dtype=np.uint8)
    cv2.circle(mask_full, (ox, oy), orad, 255, thickness=-1)   # fill outer
    if irad < 165:
        cv2.circle(mask_full, (ix, iy), irad, 0, thickness=-1)     # cut out inner

    import uuid

    uid = uuid.uuid4().hex
    annulus_file = f"annulus_{uid}.bmp"

    annulus_color = cv2.bitwise_and(img, img, mask=mask_full)
    cv2.imwrite(annulus_file, annulus_color)
    print("Saved:", annulus_file)


# if __name__ == "__main__":
#     def process_folder(folder_path):
#         for file in os.listdir(folder_path):
#             if file.lower().endswith((".bmp", ".png", ".jpg", ".jpeg")):
#                 img_path = os.path.join(folder_path, file)

#                 img = cv2.imread(img_path)

#                 if img is None:
#                     print("❌ Cannot read:", img_path)
#                     continue

#                 print("▶ Processing:", img_path)
#                 detect_thread_circle(img)

#     # ===== Usage =====
#     process_folder("input_images")