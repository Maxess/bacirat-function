# -*- coding: utf-8 -*-
"""واسط بارگذاری کلاینت API موجود در ریپو (../api/persianmeet_api.py) بدون کپی‌کردن کد."""
import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parent.parent.parent / "api"
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from persianmeet_api import PersianMeetAPI, PersianMeetError  # noqa: E402,F401
