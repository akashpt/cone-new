# ============================================================
# mailer.py
# PURPOSE:
# - Send defect report PDFs
# - Send shift-end emails with PDF attachment
# - Manage receiver mail IDs via a popup (stored in JSON)
# ============================================================

import smtplib
from email.message import EmailMessage
import ssl
import os
import json
import re
from pathlib import Path
from paths import MAIL_JSON_PATH


# ===============================
# CONFIG
# ===============================
SENDER_EMAIL = "kevintexainnovates@gmail.com"
SENDER_PASSWORD = "cpqa kkdc vobg dhuv"   # Gmail App Password only


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ===============================
# JSON HELPERS (MAIL IDS)
# ===============================
def _ensure_mail_json_exists():
    """Ensure the JSON exists with a default structure (no overwrite)."""
    try:
        if not MAIL_JSON_PATH.exists():
            MAIL_JSON_PATH.write_text(json.dumps({"mail_ids": []}, indent=2), encoding="utf-8")
    except Exception as e:
        print("❌ Could not create mail json:", e)


def load_mail_ids():
    """
    Reads mail IDs from mailIdsForSendingReport.json
    Format:
      { "mail_ids": ["a@b.com", "c@d.com"] }
    Returns cleaned unique list.
    """
    _ensure_mail_json_exists()
    try:
        data = json.loads(MAIL_JSON_PATH.read_text(encoding="utf-8")) or {}
        mails = data.get("mail_ids", [])
        mails = [m.strip().lower() for m in mails if isinstance(m, str) and m.strip()]
        return sorted(set(mails))
    except Exception as e:
        print("❌ load_mail_ids failed:", e)
        return []


def save_mail_ids(mails):
    """Saves mail IDs (unique + sorted) to mailIdsForSendingReport.json."""
    try:
        mails = [m.strip().lower() for m in mails if isinstance(m, str) and m.strip()]
        mails = sorted(set(mails))
        MAIL_JSON_PATH.write_text(json.dumps({"mail_ids": mails}, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        print("❌ save_mail_ids failed:", e)
        return False


# ===============================
# SEND DEFECT REPORT FUNCTION
# ===============================
# def send_defect_report(pdf_path):
#     """
#     Sends an email with the cone defect report PDF attached.
#     Recipients are loaded from mailIdsForSendingReport.json
#     """
#     if not os.path.exists(pdf_path):
#         print("❌ PDF not found:", pdf_path)
#         return

#     recipients = load_mail_ids()
#     if not recipients:
#         print("❌ No recipients configured in mailIdsForSendingReport.json")
#         return

#     msg = EmailMessage()
#     msg["From"] = SENDER_EMAIL
#     msg["To"] = ", ".join(recipients)
#     msg["Subject"] = "Cone Defect Report"
#     msg.set_content("Attached is the cone defect inspection report.")

#     with open(pdf_path, "rb") as f:
#         pdf_data = f.read()
#         pdf_name = os.path.basename(pdf_path)

#     msg.add_attachment(
#         pdf_data,
#         maintype="application",
#         subtype="pdf",
#         filename=pdf_name
#     )

#     context = ssl.create_default_context()
#     try:
#         with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
#             server.login(SENDER_EMAIL, SENDER_PASSWORD)
#             server.send_message(msg)
#         print(f"📧 Defect report email sent successfully to: {recipients}")
#     except Exception as e:
#         print("❌ Email sending failed:", e)


# ===============================
# SEND SHIFT END EMAIL FUNCTION
# ===============================
# def send_shift_mail(shift_name, shift_end_dt, pdf_path):
#     """
#     Sends an email notifying the end of a shift with PDF attachment.
#     Recipients are loaded from mailIdsForSendingReport.json
#     """
#     if not os.path.exists(pdf_path):
#         print("❌ PDF not found:", pdf_path)
#         raise FileNotFoundError(f"PDF file not found: {pdf_path}")

#     # Check file size before sending
#     file_size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
#     if file_size_mb > 25:
#         error_msg = f"PDF size ({file_size_mb:.2f} MB) exceeds Gmail limit (25MB)"
#         print(f"❌ {error_msg}")
#         raise ValueError(error_msg)

#     recipients = load_mail_ids()
#     if not recipients:
#         raise ValueError("No recipients configured in mailIdsForSendingReport.json")

#     msg = EmailMessage()
#     msg["From"] = SENDER_EMAIL
#     msg["To"] = ", ".join(recipients)
#     msg["Subject"] = f"Shift {shift_name} Report - {shift_end_dt.strftime('%Y-%m-%d %H:%M')}"
#     msg.set_content(
#         f"Shift {shift_name} ended at {shift_end_dt}.\n\n"
#         f"Attached is the PDF report.\n\n"
#         f"File size: {file_size_mb:.2f} MB"
#     )

#     with open(pdf_path, "rb") as f:
#         pdf_data = f.read()
#         pdf_name = os.path.basename(pdf_path)

#     msg.add_attachment(
#         pdf_data,
#         maintype="application",
#         subtype="pdf",
#         filename=pdf_name
#     )

#     context = ssl.create_default_context()
#     try:
#         with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
#             server.login(SENDER_EMAIL, SENDER_PASSWORD)
#             server.send_message(msg)
#         print(f"📧 Shift {shift_name} email sent successfully to: {recipients} (File: {file_size_mb:.2f} MB)")
#     except smtplib.SMTPDataError as e:
#         if "552" in str(e) or "message size" in str(e).lower():
#             error_msg = f"Email size error (Gmail limit 25MB): {file_size_mb:.2f} MB file"
#             print(f"❌ {error_msg}")
#             raise RuntimeError(error_msg)
#         else:
#             raise
#     except Exception as e:
#         print(f"❌ Shift email failed: {e}")
#         raise


def send_shift_mail(shift_name, shift_end_dt, pdf_path):
    """
    Shift mail behavior:
    - No data        → Machine not running mail
    - No defects     → No defects mail
    - Defects exist  → Send PDF report
    """

    from classes.database import fetch_one, execute, get_current_shift_id  # adjust import if needed

    shift_id = get_current_shift_id()

    if not shift_id:
        print("⚠️ No active shift → Mail skipped")
        return

    recipients = load_mail_ids()
    if not recipients:
        print("❌ No recipients configured")
        return

    # ===============================
    # ✅ GET TOTAL & DEFECT COUNT
    # ===============================
    total_row = fetch_one(
        "SELECT COUNT(*) FROM cone_entry WHERE shift_id = ?",
        (shift_id,)
    )
    total = int(total_row[0]) if total_row else 0

    defect_row = fetch_one(
        """
        SELECT COUNT(*) FROM cone_entry
        WHERE shift_id = ?
        AND (
            tip_result = 'NG' OR
            top_result = 'NG' OR
            bottom_result = 'NG'
        )
        """,
        (shift_id,)
    )
    defects = int(defect_row[0]) if defect_row else 0

    # ===============================
    # ✅ PREVENT DUPLICATE MAIL
    # ===============================
    already_sent = fetch_one(
        "SELECT 1 FROM shift_report_log WHERE shift_id = ? LIMIT 1",
        (shift_id,)
    )
    if already_sent:
        print(f"⚠️ Shift {shift_id} mail already sent → Skipped")
        return

    # ===============================
    # ✅ CREATE EMAIL
    # ===============================
    msg = EmailMessage()
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = f"Shift {shift_name} Report - {shift_end_dt.strftime('%Y-%m-%d %H:%M')}"

    # ===============================
    # ❌ CASE 1: NO DATA
    # ===============================
    if total == 0:
        msg.set_content(
            f"Shift {shift_name} ended at {shift_end_dt}.\n\n"
            f"⚠️ Machine not running on this shift.\n"
            f"No production data recorded."
        )
        print("⚠️ No data → Machine not running mail")

    # ===============================
    # ✅ CASE 2: NO DEFECTS
    # ===============================
    elif defects == 0:
        msg.set_content(
            f"Shift {shift_name} ended at {shift_end_dt}.\n\n"
            f"✅ Machine running normally.\n"
            f"Total cones checked: {total}\n"
            f"No defects found."
        )
        print("✅ No defects → Info mail")

    # ===============================
    # 📧 CASE 3: DEFECTS FOUND
    # ===============================
    else:
        if not os.path.exists(pdf_path):
            print("❌ PDF not found:", pdf_path)
            return

        file_size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        if file_size_mb > 25:
            print(f"❌ PDF too large: {file_size_mb:.2f} MB")
            return

        msg.set_content(
            f"Shift {shift_name} ended at {shift_end_dt}.\n\n"
            f"⚠️ Defects detected.\n"
            f"Total cones: {total}\n"
            f"Defective cones: {defects}\n\n"
            f"Attached is the detailed report."
        )

        with open(pdf_path, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="pdf",
                filename=os.path.basename(pdf_path)
            )

        print("📧 Defects found → Sending PDF report")

    # ===============================
    # ✅ SEND MAIL
    # ===============================
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)

        print(f"📧 Shift {shift_name} email sent successfully")

        # ✅ LOG MAIL
        execute(
            "INSERT INTO shift_report_log (shift_id, shift_end, sent_at) VALUES (?, ?, datetime('now'))",
            (shift_id, shift_end_dt.strftime("%Y-%m-%d %H:%M:%S"))
        )

    except Exception as e:
        print("❌ Email sending failed:", e)

# ===============================
# POPUP (INSIDE SAME FILE) - FIXED CLOSE BEHAVIOR
# ===============================
def open_mail_receivers_popup(parent=None):
    """
    Texa Innovates LIGHT theme popup (matches Settings window UI).
    Close button closes only this popup (does not close Settings).
    """
    try:
        from PyQt5.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
            QPushButton, QListWidget, QListWidgetItem, QMessageBox, QFrame
        )
        from PyQt5.QtCore import Qt
    except Exception as e:
        print("❌ PyQt5 not available for popup:", e)
        return

    # ===== Texa light theme colors (match your settings screenshot) =====
    BG       = "#fbf3f4"   # soft pink-white background
    CARD     = "#ffffff"   # white cards
    BORDER   = "#e7d7da"   # soft border
    MAROON   = "#6e0c15"   # texa burgundy
    MAROON_2 = "#8c1b25"   # hover
    TEXT     = "#2b2b2b"
    MUTED    = "#6b5b5e"

    QSS = f"""
    QDialog {{
        background: {BG};
        color: {TEXT};
    }}

    QLabel#title {{
        font-size: 16px;
        font-weight: 900;
        color: {MAROON};
    }}
    QLabel#sub {{
        font-size: 12px;
        color: {MUTED};
    }}

    QFrame#card {{
        background: {CARD};
        border: 1px solid {BORDER};
        border-radius: 14px;
    }}

    QLineEdit {{
        background: #ffffff;
        border: 1px solid {BORDER};
        border-radius: 12px;
        padding: 10px 12px;
        color: {TEXT};
        font-size: 13px;
    }}
    QLineEdit:focus {{
        border: 1px solid {MAROON};
    }}

    QPushButton {{
        border-radius: 12px;
        padding: 10px 14px;
        font-size: 13px;
        font-weight: 800;
    }}

    QPushButton#primary {{
        background: {MAROON};
        color: white;
        border: 1px solid {MAROON};
    }}
    QPushButton#primary:hover {{
        background: {MAROON_2};
        border: 1px solid {MAROON_2};
    }}

    QPushButton#ghost {{
        background: #ffffff;
        color: {MAROON};
        border: 1px solid {BORDER};
    }}
    QPushButton#ghost:hover {{
        background: #fff7f8;
        border: 1px solid {MAROON};
    }}

    QPushButton#danger {{
        background: #d06a73;   /* similar to your Remove color */
        color: white;
        border: 1px solid #d06a73;
    }}
    QPushButton#danger:hover {{
        background: #c65b65;
        border: 1px solid #c65b65;
    }}

    QListWidget {{
        background: #ffffff;
        border: 1px solid {BORDER};
        border-radius: 12px;
        padding: 6px;
        outline: none;
        font-size: 13px;
    }}
    QListWidget::item {{
        padding: 10px 10px;
        margin: 4px;
        border-radius: 10px;
        background: #fff7f8;
        color: {TEXT};
        border: 1px solid transparent;
    }}
    QListWidget::item:selected {{
        background: rgba(110,12,21,0.12);
        border: 1px solid rgba(110,12,21,0.35);
    }}
    """

    class MailReceiversDialog(QDialog):
        def __init__(self):
            super().__init__(None)  # ✅ No parent => closing won't close settings
            self.setWindowTitle("Shift Report Receivers")
            self.setWindowModality(Qt.ApplicationModal)
            self.setMinimumWidth(560)

            self.setStyleSheet(QSS)

            root = QVBoxLayout(self)
            root.setContentsMargins(18, 18, 18, 18)
            root.setSpacing(12)

            # ---------- Header ----------
            header = QHBoxLayout()
            header.setSpacing(10)

            left = QVBoxLayout()
            left.setSpacing(2)

            title = QLabel("Shift Report Receivers")
            title.setObjectName("title")
            sub = QLabel("Add / remove emails that will receive shift-end report PDFs")
            sub.setObjectName("sub")

            left.addWidget(title)
            left.addWidget(sub)

            btn_x = QPushButton("✕")
            btn_x.setObjectName("ghost")
            btn_x.setFixedSize(42, 38)
            btn_x.clicked.connect(self.close)

            header.addLayout(left, 1)
            header.addWidget(btn_x)
            root.addLayout(header)

            # ---------- Card ----------
            card = QFrame()
            card.setObjectName("card")
            card_l = QVBoxLayout(card)
            card_l.setContentsMargins(14, 14, 14, 14)
            card_l.setSpacing(10)

            # input row
            row = QHBoxLayout()
            row.setSpacing(10)

            self.inp = QLineEdit()
            self.inp.setPlaceholderText("example@mail.com")
            self.inp.returnPressed.connect(self._add)

            self.btn_add = QPushButton("Add")
            self.btn_add.setObjectName("primary")
            self.btn_add.setFixedHeight(40)
            self.btn_add.clicked.connect(self._add)

            row.addWidget(self.inp, 1)
            row.addWidget(self.btn_add)
            card_l.addLayout(row)

            self.listw = QListWidget()
            self.listw.setMinimumHeight(240)
            card_l.addWidget(self.listw, 1)

            root.addWidget(card)

            # ---------- Bottom actions ----------
            actions = QHBoxLayout()
            actions.setSpacing(10)

            self.btn_remove = QPushButton("Remove Selected")
            self.btn_remove.setObjectName("danger")
            self.btn_remove.setFixedHeight(40)
            self.btn_remove.clicked.connect(self._remove)

            self.btn_close = QPushButton("Close")
            self.btn_close.setObjectName("ghost")
            self.btn_close.setFixedHeight(40)
            self.btn_close.clicked.connect(self.close)

            actions.addWidget(self.btn_remove)
            actions.addStretch(1)
            actions.addWidget(self.btn_close)
            root.addLayout(actions)

            self._reload()

        def _reload(self):
            self.listw.clear()
            mails = load_mail_ids()
            for m in mails:
                self.listw.addItem(QListWidgetItem(m))
            self.btn_remove.setEnabled(self.listw.count() > 0)

        def _add(self):
            email = (self.inp.text() or "").strip().lower()
            if not email:
                return

            if not EMAIL_RE.match(email):
                QMessageBox.warning(self, "Invalid Email", "Please enter a valid email address.")
                return

            mails = load_mail_ids()
            if email in mails:
                QMessageBox.information(self, "Already Exists", "This email already exists.")
                self.inp.selectAll()
                self.inp.setFocus()
                return

            mails.append(email)
            if not save_mail_ids(mails):
                QMessageBox.warning(self, "Save Failed", "Could not save mail IDs.")
                return

            self.inp.clear()
            self.inp.setFocus()
            self._reload()

        def _remove(self):
            item = self.listw.currentItem()
            if not item:
                QMessageBox.information(self, "Select Email", "Select an email to remove.")
                return

            email = item.text().strip().lower()
            r = QMessageBox.question(
                self,
                "Confirm Remove",
                f"Remove this email?\n\n{email}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if r != QMessageBox.Yes:
                return

            mails = [m for m in load_mail_ids() if m.strip().lower() != email]
            if not save_mail_ids(mails):
                QMessageBox.warning(self, "Save Failed", "Could not save mail IDs.")
                return

            self._reload()

    dlg = MailReceiversDialog()
    dlg.show()


from datetime import datetime

send_shift_mail(
    shift_name="Shift 1",
    shift_end_dt=datetime.now(),
    pdf_path="report_1.pdf"
)