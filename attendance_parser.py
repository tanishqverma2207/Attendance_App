"""
attendance_parser.py
Reads a sumHR "Daily Attendance Report" CSV export and classifies each
employee's daily row into issue categories, based on rules agreed with HR.

Rules:
- Shift: 10:00 AM - 7:00 PM
- Clock-in 10:00:01 - 10:30:00  -> "late_soft"
- Clock-in after 10:30:00       -> "late_hard"
- Clock-out is only checked for being MISSING (no early-leave check)
- Status handling:
    Weekly Off / Optional Holiday / Leave (...)  -> skipped entirely
    Absent                                       -> "leave_reason_missing"
    Present + missing In Time                    -> "unfilled_clockin"
    Present + missing Out Time                   -> "unfilled_clockout"
    Present + Out Time earlier than In Time       -> "anomaly"  (midnight rollover etc.)
"""

import csv
import io
from datetime import datetime, date, time

CATEGORY_LABELS = {
    "late_soft": "Late Comer (soft - 10:00 to 10:30)",
    "late_hard": "Late Comer (after 10:30)",
    "unfilled_clockin": "Unfilled Clock-in",
    "unfilled_clockout": "Unfilled Clock-out",
    "anomaly": "Anomaly - needs manual check",
    "leave_reason_missing": "Leave Reason Not Mentioned (Absent)",
}

CATEGORY_COLORS = {
    "late_soft": "#B4761F",
    "late_hard": "#9A4A0A",
    "unfilled_clockin": "#3B6E96",
    "unfilled_clockout": "#2F7A6B",
    "anomaly": "#7C5CBF",
    "leave_reason_missing": "#C1443C",
}

SKIP_STATUSES_PREFIXES = ("Weekly Off", "Optional Holiday", "Leave")

LATE_SOFT_START = time(10, 0, 1)
LATE_SOFT_END = time(10, 30, 0)


def _parse_time(value):
    """Parse HH:MM:SS -> datetime.time, or None if blank/invalid."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M:%S").time()
    except ValueError:
        return None


def _parse_date(value):
    """Parse DD-MM-YYYY -> date object."""
    value = (value or "").strip()
    try:
        return datetime.strptime(value, "%d-%m-%Y").date()
    except ValueError:
        return None


def parse_csv(file_bytes):
    """
    Parses the raw uploaded CSV bytes into a list of row dicts.
    Handles the sumHR export's extra title row on line 1.
    """
    text = file_bytes.decode("utf-8-sig")
    lines = text.splitlines()

    # Find the actual header row (contains "Employee Code")
    header_idx = None
    for i, line in enumerate(lines):
        if "Employee Code" in line:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find header row - is this a sumHR attendance export?")

    reader = csv.DictReader(lines[header_idx:])
    rows = list(reader)
    return rows


def classify_rows(rows, date_from=None, date_to=None):
    """
    Given raw parsed rows, returns:
        employees: dict keyed by Employee Code -> {
            name, code,
            issues: [ {date, day, category, in_time, out_time, note}, ... ]
        }
    Optionally filtered to a date range (inclusive), both `date` objects.
    """
    employees = {}

    for row in rows:
        code = (row.get("Employee Code") or "").strip()
        name = (row.get("Employee Name") or "").strip()
        if not code:
            continue

        shift_date = _parse_date(row.get("ShiftDate"))
        if shift_date is None:
            continue
        if date_from and shift_date < date_from:
            continue
        if date_to and shift_date > date_to:
            continue

        status = (row.get("Status") or "").strip()
        day = (row.get("Day") or "").strip()
        in_time_raw = row.get("In Time")
        out_time_raw = row.get("Out Time")
        in_time = _parse_time(in_time_raw)
        out_time = _parse_time(out_time_raw)

        category = None
        note = None

        if status.startswith(SKIP_STATUSES_PREFIXES):
            continue  # Weekly Off / Holiday / any Leave -> already explained, skip
        elif status == "Absent":
            category = "leave_reason_missing"
            note = "No clock-in/out and no leave marked for this day."
        elif status == "Present":
            if not in_time and not out_time:
                category = "unfilled_clockin"  # both missing -> treat as clock-in issue primarily
                note = "Both clock-in and clock-out are missing."
            elif not in_time:
                category = "unfilled_clockin"
                note = "Clock-in missing."
            elif not out_time:
                category = "unfilled_clockout"
                note = "Clock-out missing."
            elif out_time < in_time:
                category = "anomaly"
                note = "Clock-out time is earlier than clock-in time (possible midnight rollover or stray swipe) - please verify manually."
            elif LATE_SOFT_START <= in_time <= LATE_SOFT_END:
                category = "late_soft"
                note = f"Clocked in at {in_time.strftime('%H:%M')} (soft late window)."
            elif in_time > LATE_SOFT_END:
                category = "late_hard"
                note = f"Clocked in at {in_time.strftime('%H:%M')} (after 10:30 AM)."
            else:
                continue  # on time, nothing to flag
        else:
            # Unknown/other status - skip rather than guess
            continue

        if code not in employees:
            employees[code] = {"code": code, "name": name, "issues": []}

        employees[code]["issues"].append({
            "date": shift_date.strftime("%d-%m-%Y"),
            "day": day,
            "category": category,
            "in_time": in_time_raw or "-",
            "out_time": out_time_raw or "-",
            "note": note,
        })

    return employees


def filter_by_categories(employees, selected_categories):
    """
    Returns a new employees dict containing only issues whose category
    is in selected_categories. Employees with zero remaining issues
    are dropped entirely.
    """
    result = {}
    for code, emp in employees.items():
        filtered_issues = [i for i in emp["issues"] if i["category"] in selected_categories]
        if filtered_issues:
            result[code] = {"code": code, "name": emp["name"], "issues": filtered_issues}
    return result
