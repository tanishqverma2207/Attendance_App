"""
email_sender.py
Builds the combined per-employee email (plain text + simple HTML table)
and sends it via SMTP using credentials from config.json.
"""

import json
import os
import csv
import uuid
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from attendance_parser import CATEGORY_LABELS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "data", "config.json")
EMPLOYEE_MAP_PATH = os.path.join(BASE_DIR, "data", "employee_mapping.csv")
TEMPLATE_PATH = os.path.join(BASE_DIR, "data", "email_template.json")
HISTORY_PATH = os.path.join(BASE_DIR, "data", "send_history.json")

EMPLOYEE_COLUMNS = ["Employee Code", "Employee Name", "Email ID", "Manager Email", "Department"]


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return None
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def load_employee_map():
    """Returns dict: employee_code -> {email, manager_email, name}"""
    mapping = {}
    if not os.path.exists(EMPLOYEE_MAP_PATH):
        return mapping
    with open(EMPLOYEE_MAP_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("Employee Code") or "").strip()
            if not code:
                continue
            mapping[code] = {
                "email": (row.get("Email ID") or "").strip(),
                "manager_email": (row.get("Manager Email") or "").strip(),
                "name": (row.get("Employee Name") or "").strip(),
            }
    return mapping


def load_employee_rows():
    """Returns the raw list of employee mapping rows (dicts with original column names), for the editable UI."""
    rows = []
    if not os.path.exists(EMPLOYEE_MAP_PATH):
        return rows
    with open(EMPLOYEE_MAP_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("Employee Code") or "").strip()
            if not code:
                continue
            rows.append({col: (row.get(col) or "").strip() for col in EMPLOYEE_COLUMNS})
    return rows


def save_employee_rows(rows):
    os.makedirs(os.path.dirname(EMPLOYEE_MAP_PATH), exist_ok=True)
    with open(EMPLOYEE_MAP_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EMPLOYEE_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in EMPLOYEE_COLUMNS})


def upsert_employee_row(code, name, email, manager_email, department):
    rows = load_employee_rows()
    new_row = {
        "Employee Code": code.strip(),
        "Employee Name": name.strip(),
        "Email ID": email.strip(),
        "Manager Email": manager_email.strip(),
        "Department": department.strip(),
    }
    for i, row in enumerate(rows):
        if row["Employee Code"] == code.strip():
            rows[i] = new_row
            save_employee_rows(rows)
            return
    rows.append(new_row)
    save_employee_rows(rows)


def delete_employee_row(code):
    rows = load_employee_rows()
    rows = [r for r in rows if r["Employee Code"] != code.strip()]
    save_employee_rows(rows)


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def append_history(mode, date_from, date_to, categories, results):
    history = load_history()
    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now().strftime("%d-%b-%Y %H:%M"),
        "mode": mode,  # "reviewed" or "direct"
        "date_from": date_from,
        "date_to": date_to,
        "categories": categories,
        "results": results,
        "sent_count": sum(1 for r in results if r["status"] == "SENT"),
        "total_count": len(results),
    }
    history.insert(0, entry)  # newest first
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)
    return entry


def load_template():
    default = {
        "subject": "Attendance Discrepancy Notice - {name} ({date_from} to {date_to})",
        "intro": (
            "Hi {name},\n\n"
            "The following attendance discrepancies were noticed for the period "
            "{date_from} to {date_to}. Please review and respond if any correction "
            "or clarification is needed.\n"
        ),
        "outro": "\nRegards,\nHR Team",
    }
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r") as f:
            return {**default, **json.load(f)}
    return default


def save_template(tpl):
    with open(TEMPLATE_PATH, "w") as f:
        json.dump(tpl, f, indent=2)


def build_email_body(emp_name, issues, date_from, date_to, template):
    intro = template["intro"].format(name=emp_name, date_from=date_from, date_to=date_to)
    outro = template["outro"]

    lines = [intro, ""]
    lines.append(f"{'Date':<12}{'Day':<6}{'Issue':<32}{'In Time':<10}{'Out Time':<10}")
    lines.append("-" * 70)
    for issue in issues:
        label = CATEGORY_LABELS.get(issue["category"], issue["category"])
        lines.append(
            f"{issue['date']:<12}{issue['day']:<6}{label:<32}{issue['in_time']:<10}{issue['out_time']:<10}"
        )
    lines.append("")
    lines.append(outro)
    return "\n".join(lines)


def build_email_html(emp_name, issues, date_from, date_to, template):
    intro = template["intro"].format(name=emp_name, date_from=date_from, date_to=date_to).replace("\n", "<br>")
    outro = template["outro"].replace("\n", "<br>")

    rows_html = ""
    for issue in issues:
        label = CATEGORY_LABELS.get(issue["category"], issue["category"])
        rows_html += (
            f"<tr>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;'>{issue['date']}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;'>{issue['day']}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;'>{label}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;'>{issue['in_time']}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;'>{issue['out_time']}</td>"
            f"</tr>"
        )

    html = f"""
    <div style="font-family:Arial,sans-serif;font-size:14px;color:#222;">
      <p>{intro}</p>
      <table style="border-collapse:collapse;">
        <tr style="background:#f2f2f2;">
          <th style='padding:6px 10px;border:1px solid #ddd;'>Date</th>
          <th style='padding:6px 10px;border:1px solid #ddd;'>Day</th>
          <th style='padding:6px 10px;border:1px solid #ddd;'>Issue</th>
          <th style='padding:6px 10px;border:1px solid #ddd;'>In Time</th>
          <th style='padding:6px 10px;border:1px solid #ddd;'>Out Time</th>
        </tr>
        {rows_html}
      </table>
      <p>{outro}</p>
    </div>
    """
    return html


def send_email(to_email, cc_emails, subject, text_body, html_body, config):
    msg = MIMEMultipart("alternative")
    msg["From"] = config["sender_email"]
    msg["To"] = to_email
    if cc_emails:
        msg["Cc"] = ", ".join(cc_emails)
    msg["Subject"] = subject

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    recipients = [to_email] + (cc_emails or [])

    with smtplib.SMTP(config["smtp_host"], config["smtp_port"]) as server:
        server.starttls()
        server.login(config["sender_email"], config["sender_app_password"])
        server.sendmail(config["sender_email"], recipients, msg.as_string())
