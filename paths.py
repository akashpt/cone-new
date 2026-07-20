import os, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def app_path() -> Path:
    """
    Read-only application path
    - Normal run   → project root
    - PyInstaller  → _MEIPASS
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def run_path() -> Path:
    """
    Read/write runtime path
    - Normal run   → project root
    - PyInstaller  → exe folder
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.cwd()

# os.environ["PYLON_ROOT"] = "/opt/pylon"
# os.environ["GENICAM_GENTL64_PATH"] = "/opt/pylon/lib/gentl"

APP_DIR = app_path()

INDEX_HTML = APP_DIR / "templates/index.html"
CONTROLLER_HTML = APP_DIR / "templates/controller.html"
SETTING_HTML = APP_DIR / "templates/setting.html"
REPORTS_HTML = APP_DIR / "templates/reports.html"


RUN_DIR = run_path()

RUN_DIR = RUN_DIR/"ConeI_data"
RUN_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = RUN_DIR / "cone_inspection.db"

PREDICTION = RUN_DIR / "prediction_file"

GOOD_TIP = RUN_DIR / "good_tips"
os.makedirs(GOOD_TIP, exist_ok=True)

SETTINGS_JSON = RUN_DIR / "settings.json"
# os.makedirs(SETTINGS_JSON, exist_ok=True)

MAIL_JSON_PATH = RUN_DIR / "mailIdsForSendingReport.json"

DEFECT_SAVE_DIR = RUN_DIR/ "defects"
os.makedirs(DEFECT_SAVE_DIR, exist_ok=True)

