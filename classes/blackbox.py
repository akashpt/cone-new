import cv2
import numpy as np

def draw_annulus_bbox_with_status(
    bgr_img,
    status,
    *,
    blur_ksize=(7, 7),
    blur_sigma=2,
    dp=1.2,
    min_dist=500,
    param1=100,
    param2=40,
    min_radius_ratio=0.30,
    max_radius_ratio=0.48,
    thickness=4,
    font_scale=1.2,
    text_thickness=3,
):
   
    if bgr_img is None or not isinstance(bgr_img, np.ndarray):
        raise ValueError("Invalid bgr_img")

    # -------- status normalize --------
    if isinstance(status, str):
        s = status.strip().lower()
        is_good = s in ("good", "ok", "pass", "passed", "true", "1")
    else:
        is_good = bool(status)
    
    s = str(status).lower()
    print("The Exact Status is :", repr(status))  # shows spaces/newlines too

    if s == "tip_bad":
        status = "TIP BAD"
    elif s in ("top_wh_bad", "top_uv_bad"):
        status = "TOP BAD"
    elif s in ("bottom_wh_bad", "bottom_uv_bad"):
        status = "BOTTOM BAD"
    else:
        status = "BAD"


    label = "GOOD" if is_good else status
    color = (0, 255, 0) if is_good else (0, 0, 255)  # green / red (BGR)

    # -------- preprocess --------
    gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, blur_ksize, blur_sigma)

    h, w = bgr_img.shape[:2]
    mn = min(h, w)

    # -------- detect outer circle --------
    circles = cv2.HoughCircles(
        blur,
        cv2.HOUGH_GRADIENT,
        dp=dp,
        minDist=min_dist,
        param1=param1,
        param2=param2,
        minRadius=int(mn * min_radius_ratio),
        maxRadius=int(mn * max_radius_ratio),
    )

    if circles is None:
        # if no circle, still return labeled image (optional)
        out = bgr_img.copy()
        cv2.putText(out, "CIRCLE NOT FOUND", (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 3, cv2.LINE_AA)
        cv2.putText(out, f"STATUS: {label}", (30, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.1, color, 3, cv2.LINE_AA)
        return out, {
            "found": False,
            "status": label,
            "center": None,
            "radius": None,
            "bbox": None,
        }

    circles = np.uint16(np.around(circles))
    x_c, y_c, r_outer = map(int, circles[0][0])

    # -------- bbox --------
    x1 = max(0, x_c - r_outer)
    y1 = max(0, y_c - r_outer)
    x2 = min(w - 1, x_c + r_outer)
    y2 = min(h - 1, y_c + r_outer)

    # -------- draw --------
    out = bgr_img.copy()
    cv2.rectangle(out, (x1, y1), (x2, y2), color, thickness)

    # label bg box for readability
    text = f"{label}"
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, text_thickness)
    tx, ty = x1, max(0, y1 - 10)
    y_text = max(th + 10, ty)

    # filled background (black) behind text
    cv2.rectangle(out, (tx, y_text - th - 10), (tx + tw + 20, y_text + baseline + 5), (0, 0, 0), -1)
    cv2.putText(out, text, (tx + 10, y_text - 5), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, text_thickness, cv2.LINE_AA)

    return out, {
        "found": True,
        "status": label,
        "center": (x_c, y_c),
        "radius": r_outer,
        "bbox": (x1, y1, x2, y2),
    }



# IMG_PATH = r"D:\Texa\cone_inspection\lambhodhara\tip_colors\tip_image_20251212_150627_696.bmp"
# OUT_PATH = r"D:\Texa\cone_inspection\lambhodhara\bbox_result_good.bmp"

# img = cv2.imread(IMG_PATH)
# out_img, info = draw_annulus_bbox_with_status(img, status="good")  # or "bad" / True / False

# cv2.imwrite(OUT_PATH, out_img)
# print("Saved:", OUT_PATH)
# print(info)

