import cv2
import numpy as np
import os
from pathlib import Path
import uuid

MIN_CONTOUR_AREA = 50000
BLUR_KSIZE = (9, 9)
OUTER_CLOSE_K = (21, 21)
OUTER_OPEN_K = (7, 7)

INNER_ROI_RATIO = 0.22
INNER_MIN_AREA = 80
INNER_CLOSE_K = (7, 7)
INNER_OPEN_K = (5, 5)


INNER_MIN = 158
INNER_MAX_RATIO = 0.55
ROI_RATIO = 0.28
BOTTOM_FORCE_INNER = True

# INNER_ROI_RATIO = 0.22
# INNER_MIN_AREA = 80
# INNER_CLOSE_K = (7, 7)
# INNER_OPEN_K = (5, 5)

DEBUG_SAVE = True


def preprocess_outer(gray):
    blur = cv2.GaussianBlur(gray, BLUR_KSIZE, 0)

    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, OUTER_CLOSE_K)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, k_close, iterations=2)

    k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, OUTER_OPEN_K)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, k_open, iterations=1)

    return blur, th


def get_largest_contour_mask(binary, min_area=MIN_CONTOUR_AREA):
    cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not cnts:
        return None, None

    largest = max(cnts, key=cv2.contourArea)
    area = cv2.contourArea(largest)

    if area < min_area:
        return None, None

    mask = np.zeros(binary.shape, dtype=np.uint8)
    cv2.drawContours(mask, [largest], -1, 255, thickness=cv2.FILLED)

    return mask, largest


def remove_side_objects_using_circular_limit(mask, contour):
    """
    Remove left/right support objects by limiting mask to the main circular yarn radius.
    """
    if contour is None:
        return mask

    M = cv2.moments(contour)
    if M["m00"] == 0:
        return mask

    cx = int(M["m10"] / M["m00"])
    cy = int(M["m01"] / M["m00"])

    pts = contour.reshape(-1, 2).astype(np.float32)
    radii = np.sqrt((pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2)

    r_med = np.median(radii)
    r_keep = int(r_med * 0.985)

    limiter = np.zeros_like(mask)
    cv2.circle(limiter, (cx, cy), r_keep, 255, -1)

    cleaned = cv2.bitwise_and(mask, limiter)
    return cleaned


# def detect_inner_hole(gray, outer_mask):
#     h, w = gray.shape[:2]

#     ys, xs = np.where(outer_mask > 0)
#     if len(xs) == 0:
#         return None, None

#     cx = int(np.mean(xs))
#     cy = int(np.mean(ys))

#     outer_area = np.count_nonzero(outer_mask)
#     est_outer_r = int(np.sqrt(outer_area / np.pi))
#     roi_r = max(40, int(est_outer_r * INNER_ROI_RATIO))

#     x1 = max(0, cx - roi_r)
#     y1 = max(0, cy - roi_r)
#     x2 = min(w, cx + roi_r)
#     y2 = min(h, cy + roi_r)

#     roi = gray[y1:y2, x1:x2]
#     if roi.size == 0:
#         return None, None

#     roi_blur = cv2.GaussianBlur(roi, (5, 5), 0)

#     _, roi_th = cv2.threshold(
#         roi_blur, 0, 255,
#         cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
#     )

#     k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, INNER_CLOSE_K)
#     roi_th = cv2.morphologyEx(roi_th, cv2.MORPH_CLOSE, k_close, iterations=2)

#     k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, INNER_OPEN_K)
#     roi_th = cv2.morphologyEx(roi_th, cv2.MORPH_OPEN, k_open, iterations=1)

#     cnts, _ = cv2.findContours(roi_th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

#     if not cnts:
#         return None, roi_th

#     best = None
#     best_score = -1

#     cx_roi = roi.shape[1] // 2
#     cy_roi = roi.shape[0] // 2

#     for c in cnts:
#         area = cv2.contourArea(c)
#         if area < INNER_MIN_AREA:
#             continue

#         M = cv2.moments(c)
#         if M["m00"] == 0:
#             continue

#         ccx = int(M["m10"] / M["m00"])
#         ccy = int(M["m01"] / M["m00"])

#         dist = np.hypot(ccx - cx_roi, ccy - cy_roi)
#         score = area - dist * 10

#         if score > best_score:
#             best_score = score
#             best = c

#     if best is None:
#         return None, roi_th

#     inner_mask_full = np.zeros_like(gray)
#     best_shifted = best + np.array([[[x1, y1]]], dtype=np.int32)
#     cv2.drawContours(inner_mask_full, [best_shifted], -1, 255, thickness=cv2.FILLED)

#     return inner_mask_full, roi_th


def detect_inner_hole_circle(gray, outer_mask):
    """
    Detect inner tip boundary by scanning from yarn region inward
    until strong intensity/color change is found.
    Returns:
        inner_mask_full, roi_debug, (cx, cy, r)
    """
    h, w = gray.shape[:2]

    ys, xs = np.where(outer_mask > 0)
    if len(xs) == 0:
        return None, None, None

    # center from separated cone mask
    cx = int(np.mean(xs))
    cy = int(np.mean(ys))

    outer_area = np.count_nonzero(outer_mask)
    est_outer_r = int(np.sqrt(outer_area / np.pi))

    # ROI around center
    roi_r = max(70, int(est_outer_r * 0.30))

    x1 = max(0, cx - roi_r)
    y1 = max(0, cy - roi_r)
    x2 = min(w, cx + roi_r)
    y2 = min(h, cy + roi_r)

    roi_gray = gray[y1:y2, x1:x2]
    roi_mask = outer_mask[y1:y2, x1:x2]

    if roi_gray.size == 0:
        return None, None, None

    # smooth a little
    roi_blur = cv2.GaussianBlur(roi_gray, (7, 7), 0)

    cx_roi = roi_blur.shape[1] // 2
    cy_roi = roi_blur.shape[0] // 2

    # expected tip radius range
    min_rr = max(22, int(est_outer_r * 0.08))
    max_rr = max(min_rr + 5, int(est_outer_r * 0.20))

    boundary_points = []
    debug_vis = cv2.cvtColor(roi_blur, cv2.COLOR_GRAY2BGR)

    # scan many directions
    for ang in range(0, 360, 3):
        theta = np.deg2rad(ang)

        # go from outer yarn side inward
        start_r = int(est_outer_r * 0.22)
        end_r = 8

        values = []
        coords = []

        for r in range(start_r, end_r, -1):
            x = int(cx_roi + r * np.cos(theta))
            y = int(cy_roi + r * np.sin(theta))

            if x < 0 or x >= roi_blur.shape[1] or y < 0 or y >= roi_blur.shape[0]:
                continue

            # only inside separated cone
            if roi_mask[y, x] == 0:
                continue

            values.append(int(roi_blur[y, x]))
            coords.append((x, y))

        if len(values) < 8:
            continue

        values = np.array(values, dtype=np.float32)

        # detect strongest inward intensity change
        diffs = np.abs(np.diff(values))
        if len(diffs) == 0:
            continue

        idx = int(np.argmax(diffs))
        max_jump = diffs[idx]

        # require clear change
        if max_jump < 4:
            continue

        px, py = coords[idx + 1]
        boundary_points.append((px, py))
        cv2.circle(debug_vis, (px, py), 1, (0, 255, 255), -1)

    if len(boundary_points) < 20:
        return None, debug_vis, None

    pts = np.array(boundary_points, dtype=np.float32)

    # fit circle from detected boundary points
    (icx_roi, icy_roi), ir = cv2.minEnclosingCircle(pts.astype(np.int32))

    # reject bad radius
    if ir < min_rr:
        ir = min_rr
    if ir > max_rr:
        ir = max_rr

    icx = int(icx_roi + x1)
    icy = int(icy_roi + y1)

    # enlarge a bit to remove full tip ring
    ir = int(ir * 1.12)

    min_cut = int(est_outer_r * 0.10)
    max_cut = int(est_outer_r * 0.22)

    if ir < min_cut:
        ir = min_cut
    if ir > max_cut:
        ir = max_cut

    inner_mask_full = np.zeros_like(gray)
    cv2.circle(inner_mask_full, (icx, icy), ir, 255, -1)

    # draw final fitted circle in debug
    cv2.circle(debug_vis, (int(icx_roi), int(icy_roi)), int(ir), (0, 0, 255), 2)
    cv2.circle(debug_vis, (int(icx_roi), int(icy_roi)), 3, (255, 255, 0), -1)

    return inner_mask_full, debug_vis, (icx, icy, ir)


def detect_thread_circle2(img, postion):
    if img is None:
        raise FileNotFoundError("Image not found")

    postion = str(postion).strip().lower()
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    blur, outer_th = preprocess_outer(gray)
    print(outer_th)
    outer_mask, outer_contour = get_largest_contour_mask(outer_th)
    if outer_mask is None:
        raise RuntimeError("Could not detect outer cone contour")

    # ✅ remove side support objects
    outer_mask = remove_side_objects_using_circular_limit(outer_mask, outer_contour)

    cnts_clean, _ = cv2.findContours(outer_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    outer_contour = max(cnts_clean, key=cv2.contourArea) if cnts_clean else outer_contour

    final_mask = outer_mask.copy()

    roi_debug = None
    inner_mask = None
    inner_circle = None
    
    if postion == "top":
        inner_mask, roi_debug, inner_circle = detect_inner_hole_circle(gray, outer_mask)
        if inner_mask is not None:
            final_mask = cv2.subtract(final_mask, inner_mask)

    k_final = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    final_mask = cv2.morphologyEx(final_mask, cv2.MORPH_CLOSE, k_final, iterations=1)

    result = cv2.bitwise_and(img, img, mask=final_mask)

    debug_img = img.copy()
    if outer_contour is not None:
        cv2.drawContours(debug_img, [outer_contour], -1, (0, 255, 0), 3)

    if postion == "top" and inner_circle is not None:
        icx, icy, ir = inner_circle
        cv2.circle(debug_img, (icx, icy), ir, (0, 0, 255), 3)
        cv2.circle(debug_img, (icx, icy), 4, (255, 255, 0), -1)

    save_dir = Path("train_cone45") / postion
    save_dir.mkdir(parents=True, exist_ok=True)

    debug_dir = Path("train_cone_debug") / postion
    debug_dir.mkdir(parents=True, exist_ok=True)

    uid = uuid.uuid4().hex

    out_file = save_dir / f"uv_{uid}.bmp"
    th_file = debug_dir / f"outer_threshold_{uid}.png"
    mask_file = debug_dir / f"final_mask_{uid}.png"
    overlay_file = debug_dir / f"overlay_{uid}.png"

    cv2.imwrite(str(out_file), result)
    cv2.imwrite(str(th_file), outer_th)
    cv2.imwrite(str(mask_file), final_mask)
    cv2.imwrite(str(overlay_file), debug_img)

    if DEBUG_SAVE and roi_debug is not None:
        roi_file = debug_dir / f"inner_roi_threshold_{uid}.png"
        cv2.imwrite(str(roi_file), roi_debug)

    print("Saved:", out_file)
    return result


# def process_folder(folder_path, postion):
#     for file in os.listdir(folder_path):
#         if file.lower().endswith((".bmp", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")):
#             img_path = os.path.join(folder_path, file)

#             img = cv2.imread(img_path)
#             if img is None:
#                 print("❌ Cannot read:", img_path)
#                 continue

#             print("▶ Processing:", img_path)
            
#             try:
#                 detect_thread_circle(img, postion)
#             except Exception as e:
#                 print(f"❌ Failed on {img_path}: {e}")


# if __name__ == "__main__":
#     img = cv2.imread(r"D:\b7\Orange_40_2s_polino\bottom_good\bottom_wh_image_20260306_113015_895.bmp")
#     postion = "bottom"
#     detect_thread_circle(img, postion)
#     # process_folder(r"D:\b7\Orange_40_2s_polino\top_good", "top")
#     process_folder(r"Orange_40_2s_polino/bottom_good", "bottom")






# #_____________________temporary code ________________________________________


# import cv2
# import numpy as np
# import os
# from pathlib import Path
# import uuid

# # ---------------------- Tunable Parameters ----------------------
# MIN_CONTOUR_AREA = 50000
# BLUR_KSIZE = (9, 9)
# OUTER_CLOSE_K = (21, 21)
# OUTER_OPEN_K = (7, 7)

# INNER_ROI_RATIO = 0.22
# INNER_MIN_AREA = 80
# INNER_CLOSE_K = (7, 7)
# INNER_OPEN_K = (5, 5)

# INNER_MIN = 158
# INNER_MAX_RATIO = 0.55
# ROI_RATIO = 0.28
# BOTTOM_FORCE_INNER = True

# DEBUG_SAVE = True

# # ---------------------- Enhancement Function ----------------------
# def enhance_for_cross_thread(img):
#     """
#     Enhance the cropped cone image to make cross‑thread defects more visible.
#     Applies CLAHE on the luminance channel and a mild sharpening.
#     """
#     # Convert to LAB and apply CLAHE to L-channel
#     lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
#     l, a, b = cv2.split(lab)
#     clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
#     l_enh = clahe.apply(l)
#     lab_enh = cv2.merge([l_enh, a, b])
#     enhanced = cv2.cvtColor(lab_enh, cv2.COLOR_LAB2BGR)

#     # Mild sharpening (unsharp mask)
#     blurred = cv2.GaussianBlur(enhanced, (0, 0), 1.5)
#     sharpened = cv2.addWeighted(enhanced, 1.5, blurred, -0.5, 0)

#     return sharpened

# # ---------------------- Original Functions (unchanged) ----------------------
# def preprocess_outer(gray):
#     blur = cv2.GaussianBlur(gray, BLUR_KSIZE, 0)
#     _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
#     k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, OUTER_CLOSE_K)
#     th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, k_close, iterations=2)
#     k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, OUTER_OPEN_K)
#     th = cv2.morphologyEx(th, cv2.MORPH_OPEN, k_open, iterations=1)
#     return blur, th

# def get_largest_contour_mask(binary, min_area=MIN_CONTOUR_AREA):
#     cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
#     if not cnts:
#         return None, None
#     largest = max(cnts, key=cv2.contourArea)
#     area = cv2.contourArea(largest)
#     if area < min_area:
#         return None, None
#     mask = np.zeros(binary.shape, dtype=np.uint8)
#     cv2.drawContours(mask, [largest], -1, 255, thickness=cv2.FILLED)
#     return mask, largest

# def remove_side_objects_using_circular_limit(mask, contour):
#     if contour is None:
#         return mask
#     M = cv2.moments(contour)
#     if M["m00"] == 0:
#         return mask
#     cx = int(M["m10"] / M["m00"])
#     cy = int(M["m01"] / M["m00"])
#     pts = contour.reshape(-1, 2).astype(np.float32)
#     radii = np.sqrt((pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2)
#     r_med = np.median(radii)
#     r_keep = int(r_med * 0.985)
#     limiter = np.zeros_like(mask)
#     cv2.circle(limiter, (cx, cy), r_keep, 255, -1)
#     cleaned = cv2.bitwise_and(mask, limiter)
#     return cleaned

# def detect_inner_hole_circle(gray, outer_mask):
#     h, w = gray.shape[:2]
#     ys, xs = np.where(outer_mask > 0)
#     if len(xs) == 0:
#         return None, None, None
#     cx = int(np.mean(xs))
#     cy = int(np.mean(ys))
#     outer_area = np.count_nonzero(outer_mask)
#     est_outer_r = int(np.sqrt(outer_area / np.pi))
#     roi_r = max(70, int(est_outer_r * 0.30))
#     x1 = max(0, cx - roi_r)
#     y1 = max(0, cy - roi_r)
#     x2 = min(w, cx + roi_r)
#     y2 = min(h, cy + roi_r)
#     roi_gray = gray[y1:y2, x1:x2]
#     roi_mask = outer_mask[y1:y2, x1:x2]
#     if roi_gray.size == 0:
#         return None, None, None
#     roi_blur = cv2.GaussianBlur(roi_gray, (7, 7), 0)
#     cx_roi = roi_blur.shape[1] // 2
#     cy_roi = roi_blur.shape[0] // 2
#     min_rr = max(22, int(est_outer_r * 0.08))
#     max_rr = max(min_rr + 5, int(est_outer_r * 0.20))
#     boundary_points = []
#     debug_vis = cv2.cvtColor(roi_blur, cv2.COLOR_GRAY2BGR)
#     for ang in range(0, 360, 3):
#         theta = np.deg2rad(ang)
#         start_r = int(est_outer_r * 0.22)
#         end_r = 8
#         values = []
#         coords = []
#         for r in range(start_r, end_r, -1):
#             x = int(cx_roi + r * np.cos(theta))
#             y = int(cy_roi + r * np.sin(theta))
#             if x < 0 or x >= roi_blur.shape[1] or y < 0 or y >= roi_blur.shape[0]:
#                 continue
#             if roi_mask[y, x] == 0:
#                 continue
#             values.append(int(roi_blur[y, x]))
#             coords.append((x, y))
#         if len(values) < 8:
#             continue
#         values = np.array(values, dtype=np.float32)
#         diffs = np.abs(np.diff(values))
#         if len(diffs) == 0:
#             continue
#         idx = int(np.argmax(diffs))
#         max_jump = diffs[idx]
#         if max_jump < 4:
#             continue
#         px, py = coords[idx + 1]
#         boundary_points.append((px, py))
#         cv2.circle(debug_vis, (px, py), 1, (0, 255, 255), -1)
#     if len(boundary_points) < 20:
#         return None, debug_vis, None
#     pts = np.array(boundary_points, dtype=np.float32)
#     (icx_roi, icy_roi), ir = cv2.minEnclosingCircle(pts.astype(np.int32))
#     if ir < min_rr:
#         ir = min_rr
#     if ir > max_rr:
#         ir = max_rr
#     icx = int(icx_roi + x1)
#     icy = int(icy_roi + y1)
#     ir = int(ir * 1.12)
#     min_cut = int(est_outer_r * 0.10)
#     max_cut = int(est_outer_r * 0.22)
#     if ir < min_cut:
#         ir = min_cut
#     if ir > max_cut:
#         ir = max_cut
#     inner_mask_full = np.zeros_like(gray)
#     cv2.circle(inner_mask_full, (icx, icy), ir, 255, -1)
#     cv2.circle(debug_vis, (int(icx_roi), int(icy_roi)), int(ir), (0, 0, 255), 2)
#     cv2.circle(debug_vis, (int(icx_roi), int(icy_roi)), 3, (255, 255, 0), -1)
#     return inner_mask_full, debug_vis, (icx, icy, ir)

# # ---------------------- Main Processing Function (modified) ----------------------
# def detect_thread_circle2(img, postion):
#     if img is None:
#         raise FileNotFoundError("Image not found")

#     postion = str(postion).strip().lower()
#     h, w = img.shape[:2]
#     gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

#     blur, outer_th = preprocess_outer(gray)
#     print(outer_th)
#     outer_mask, outer_contour = get_largest_contour_mask(outer_th)
#     if outer_mask is None:
#         raise RuntimeError("Could not detect outer cone contour")

#     # ✅ remove side support objects
#     outer_mask = remove_side_objects_using_circular_limit(outer_mask, outer_contour)

#     cnts_clean, _ = cv2.findContours(outer_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
#     outer_contour = max(cnts_clean, key=cv2.contourArea) if cnts_clean else outer_contour

#     final_mask = outer_mask.copy()

#     roi_debug = None
#     inner_mask = None
#     inner_circle = None
    
#     if postion == "top":
#         inner_mask, roi_debug, inner_circle = detect_inner_hole_circle(gray, outer_mask)
#         if inner_mask is not None:
#             final_mask = cv2.subtract(final_mask, inner_mask)

#     k_final = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
#     final_mask = cv2.morphologyEx(final_mask, cv2.MORPH_CLOSE, k_final, iterations=1)

#     # Background cropped result
#     result = cv2.bitwise_and(img, img, mask=final_mask)

#     # ---- ENHANCE the cropped image for better defect visibility ----
#     enhanced_result = enhance_for_cross_thread(result)

#     debug_img = img.copy()
#     if outer_contour is not None:
#         cv2.drawContours(debug_img, [outer_contour], -1, (0, 255, 0), 3)

#     if postion == "top" and inner_circle is not None:
#         icx, icy, ir = inner_circle
#         cv2.circle(debug_img, (icx, icy), ir, (0, 0, 255), 3)
#         cv2.circle(debug_img, (icx, icy), 4, (255, 255, 0), -1)

#     save_dir = Path("train_cone") / postion
#     save_dir.mkdir(parents=True, exist_ok=True)

#     debug_dir = Path("train_cone_debug") / postion
#     debug_dir.mkdir(parents=True, exist_ok=True)

#     uid = uuid.uuid4().hex

#     out_file = save_dir / f"uv_{uid}.bmp"
#     th_file = debug_dir / f"outer_threshold_{uid}.png"
#     mask_file = debug_dir / f"final_mask_{uid}.png"
#     overlay_file = debug_dir / f"overlay_{uid}.png"

#     # Save the ENHANCED image instead of the raw cropped one
#     cv2.imwrite(str(out_file), enhanced_result)
#     cv2.imwrite(str(th_file), outer_th)
#     cv2.imwrite(str(mask_file), final_mask)
#     cv2.imwrite(str(overlay_file), debug_img)

#     if DEBUG_SAVE and roi_debug is not None:
#         roi_file = debug_dir / f"inner_roi_threshold_{uid}.png"
#         cv2.imwrite(str(roi_file), roi_debug)

#     print("Saved:", out_file)
#     return enhanced_result   # optionally return the enhanced image

# # # def process_folder(folder_path, postion):
# # #     for file in os.listdir(folder_path):
# # #         if file.lower().endswith((".bmp", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")):
# # #             img_path = os.path.join(folder_path, file)
# # #             img = cv2.imread(img_path)
# # #             if img is None:
# # #                 print("❌ Cannot read:", img_path)
# # #                 continue
# # #             print("▶ Processing:", img_path)
# # #             try:
# # #                 detect_thread_circle2(img, postion)
# # #             except Exception as e:
# # #                 print(f"❌ Failed on {img_path}: {e}")

# # # if __name__ == "__main__":
# # #     # Example usage – adjust paths as needed
# # #     process_folder(r"Siro_24s_pc_needs/bottom_whiteLight", "bottom")
# # #     # process_folder(r"D:\b7\Orange_40_2s_polino\top_good", "top")
    