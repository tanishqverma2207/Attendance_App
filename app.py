import os
import uuid
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, flash

from attendance_parser import parse_csv, classify_rows, filter_by_categories, CATEGORY_LABELS, CATEGORY_COLORS
from email_sender import (
    load_config, save_config, load_employee_map, load_template, save_template,
    build_email_body, build_email_html, send_email,
    load_employee_rows, upsert_employee_row, delete_employee_row,
    load_history, append_history,
)

app = Flask(__name__)
app.secret_key = "change-this-secret-key"  # replace before deploying

# Simple in-memory store: upload_id -> {"employees": {...}, "date_from": str, "date_to": str}
# Fine for a small internal single-user tool. Restarting the server clears it.
STORE = {}


def parse_date_input(value):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


@app.route("/")
def index():
    return render_template("upload.html", categories=CATEGORY_LABELS, category_colors=CATEGORY_COLORS)


def _process_upload_form():
    """
    Shared by /upload and /send-direct: reads the uploaded file + form
    fields, returns (employees, date_from_str, date_to_str, error) where
    error is None on success or a string to flash on failure.
    """
    file = request.files.get("attendance_file")
    if not file or file.filename == "":
        return None, None, None, None, "Please choose a CSV file exported from sumHR."

    date_from = parse_date_input(request.form.get("date_from"))
    date_to = parse_date_input(request.form.get("date_to"))
    selected_categories = request.form.getlist("categories")
    if not selected_categories:
        selected_categories = list(CATEGORY_LABELS.keys())

    try:
        rows = parse_csv(file.read())
    except ValueError as e:
        return None, None, None, None, str(e)

    employees = classify_rows(rows, date_from=date_from, date_to=date_to)
    employees = filter_by_categories(employees, selected_categories)

    emp_map = load_employee_map()
    for code, emp in employees.items():
        info = emp_map.get(code, {})
        emp["email"] = info.get("email", "")
        emp["manager_email"] = info.get("manager_email", "")
        if info.get("name"):
            emp["name"] = info["name"]

    return employees, request.form.get("date_from") or "", request.form.get("date_to") or "", selected_categories, None


@app.route("/upload", methods=["POST"])
def upload():
    employees, date_from_str, date_to_str, categories, error = _process_upload_form()
    if error:
        flash(error)
        return redirect(url_for("index"))

    upload_id = str(uuid.uuid4())
    STORE[upload_id] = {
        "employees": employees,
        "date_from": date_from_str,
        "date_to": date_to_str,
        "categories": categories,
    }

    return redirect(url_for("review", upload_id=upload_id))


@app.route("/send-direct", methods=["POST"])
def send_direct():
    """Skip the review screen entirely and email everyone flagged right away."""
    employees, date_from_str, date_to_str, categories, error = _process_upload_form()
    if error:
        flash(error)
        return redirect(url_for("index"))

    cfg = load_config()
    if not cfg:
        flash("Email sending isn't configured yet. Go to Settings and add SMTP details first.")
        return redirect(url_for("settings"))

    template = load_template()
    results = []

    for code, emp in employees.items():
        if not emp.get("email"):
            results.append({"code": code, "name": emp["name"], "status": "SKIPPED - no email on file"})
            continue

        subject = template["subject"].format(name=emp["name"], date_from=date_from_str, date_to=date_to_str)
        text_body = build_email_body(emp["name"], emp["issues"], date_from_str, date_to_str, template)
        html_body = build_email_html(emp["name"], emp["issues"], date_from_str, date_to_str, template)

        try:
            send_email(emp["email"], [], subject, text_body, html_body, cfg)
            results.append({"code": code, "name": emp["name"], "status": "SENT"})
        except Exception as e:
            results.append({"code": code, "name": emp["name"], "status": f"FAILED - {e}"})

    append_history("direct", date_from_str, date_to_str, categories, results)

    return render_template("results.html", results=results)


@app.route("/review/<upload_id>")
def review(upload_id):
    data = STORE.get(upload_id)
    if not data:
        flash("This review session has expired. Please upload the file again.")
        return redirect(url_for("index"))

    cfg = load_config()
    return render_template(
        "review.html",
        upload_id=upload_id,
        employees=data["employees"],
        category_labels=CATEGORY_LABELS,
        date_from=data["date_from"],
        date_to=data["date_to"],
        has_config=cfg is not None,
        missing_email_codes=[c for c, e in data["employees"].items() if not e.get("email")],
    )


@app.route("/send/<upload_id>", methods=["POST"])
def send(upload_id):
    data = STORE.get(upload_id)
    if not data:
        flash("This review session has expired. Please upload the file again.")
        return redirect(url_for("index"))

    cfg = load_config()
    if not cfg:
        flash("Email sending isn't configured yet. Go to Settings and add SMTP details first.")
        return redirect(url_for("settings"))

    include_codes = set(request.form.getlist("include"))
    cc_global = request.form.get("cc_manager") == "on"

    template = load_template()
    results = []

    for code, emp in data["employees"].items():
        if code not in include_codes:
            continue
        if not emp.get("email"):
            results.append({"code": code, "name": emp["name"], "status": "SKIPPED - no email on file"})
            continue

        cc_list = []
        if cc_global and emp.get("manager_email"):
            cc_list.append(emp["manager_email"])

        subject = template["subject"].format(
            name=emp["name"], date_from=data["date_from"], date_to=data["date_to"]
        )
        text_body = build_email_body(emp["name"], emp["issues"], data["date_from"], data["date_to"], template)
        html_body = build_email_html(emp["name"], emp["issues"], data["date_from"], data["date_to"], template)

        try:
            send_email(emp["email"], cc_list, subject, text_body, html_body, cfg)
            results.append({"code": code, "name": emp["name"], "status": "SENT"})
        except Exception as e:
            results.append({"code": code, "name": emp["name"], "status": f"FAILED - {e}"})

    append_history("reviewed", data["date_from"], data["date_to"], data.get("categories", []), results)

    return render_template("results.html", results=results)


@app.route("/preview/<upload_id>")
def preview(upload_id):
    data = STORE.get(upload_id)
    if not data:
        flash("This review session has expired. Please upload the file again.")
        return redirect(url_for("index"))

    code = request.args.get("code")
    emp = data["employees"].get(code)
    if not emp:
        flash("Couldn't find that employee in this review batch.")
        return redirect(url_for("review", upload_id=upload_id))

    template = load_template()
    subject = template["subject"].format(name=emp["name"], date_from=data["date_from"], date_to=data["date_to"])
    html_body = build_email_html(emp["name"], emp["issues"], data["date_from"], data["date_to"], template)

    return render_template("preview.html", upload_id=upload_id, emp=emp, subject=subject, html_body=html_body)


@app.route("/history")
def history():
    return render_template("history.html", batches=load_history(), category_labels=CATEGORY_LABELS)


@app.route("/employees", methods=["GET"])
def employees_page():
    return render_template("employees.html", rows=load_employee_rows())


@app.route("/employees/save", methods=["POST"])
def employees_save():
    code = request.form.get("code", "").strip()
    if not code:
        flash("Employee Code is required.")
        return redirect(url_for("employees_page"))
    upsert_employee_row(
        code=code,
        name=request.form.get("name", ""),
        email=request.form.get("email", ""),
        manager_email=request.form.get("manager_email", ""),
        department=request.form.get("department", ""),
    )
    flash(f"Saved {code}.")
    return redirect(url_for("employees_page"))


@app.route("/employees/delete/<code>", methods=["POST"])
def employees_delete(code):
    delete_employee_row(code)
    flash(f"Removed {code}.")
    return redirect(url_for("employees_page"))


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        cfg = {
            "smtp_host": request.form.get("smtp_host", "smtp.gmail.com"),
            "smtp_port": int(request.form.get("smtp_port", 587)),
            "sender_email": request.form.get("sender_email", ""),
            "sender_app_password": request.form.get("sender_app_password", ""),
        }
        save_config(cfg)

        template = {
            "subject": request.form.get("tpl_subject", ""),
            "intro": request.form.get("tpl_intro", ""),
            "outro": request.form.get("tpl_outro", ""),
        }
        save_template(template)

        flash("Settings saved.")
        return redirect(url_for("settings"))

    cfg = load_config() or {}
    template = load_template()
    return render_template("settings.html", cfg=cfg, template=template)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=4141)
