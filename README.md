# Attendance Discrepancy Mailer

A small internal tool: HR uploads the sumHR "Daily Attendance Report" CSV,
picks a date range and which issues to check for, reviews the flagged list,
and sends one combined email per employee.

## How it works

1. **Upload** — HR uploads the CSV, optionally narrows the date range, and
   ticks which issue types to check this time (late comers, missing
   clock-in/out, anomalies, leave-reason-not-mentioned). Nothing is checked
   that isn't ticked — so a "late comers" run and a "missing clock-in" run
   can be done completely separately, on separate weeks/months if needed.
2. **Review** — every flagged employee is shown with their issue list. HR
   can untick anyone she doesn't want to email this round, and optionally
   turn on "CC manager."
3. **Send** — clicking send generates one combined email per selected
   employee (a table of their date/issue/in-time/out-time) and sends it via
   the configured SMTP account.

## First-time setup

```bash
cd attendance-app
python3 -m venv venv
source venv/bin/activate          # on Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:4141** in a browser.

## 1. Add the employee email mapping

Edit `data/employee_mapping.csv`. Columns:

- `Employee Code` — must exactly match the code in the sumHR export (e.g. `BA0009`)
- `Employee Name` — optional
- `Email ID` — required, where the mail goes
- `Manager Email` — optional, used only if "CC manager" is ticked
- `Department` — optional, not currently used

## 2. Set up the sending email (Settings page)

If it will send from a **Gmail** account:

1. Turn on 2-Step Verification on that Google account (Google Account →
   Security).
2. Go to Google Account → Security → **App Passwords**, create one for
   "Mail", and copy the 16-character password.
3. In the app's **Settings** page, use:
   - SMTP Host: `smtp.gmail.com`
   - SMTP Port: `587`
   - Sender Email: the Gmail address
   - App Password: the 16-character password from step 2 (not the real
     Gmail password)

If it's an **Outlook/Microsoft 365** account instead, SMTP host is usually
`smtp.office365.com`, port `587`, and it uses the normal account password
(unless the org enforces app passwords too — IT will know).

## 3. Edit the email wording

Also on the Settings page — subject, intro, and outro text are all
editable any time, no code changes needed. `{name}`, `{date_from}`,
`{date_to}` get filled in automatically.

## Notes / current limitations (v1)

- Review sessions are kept in memory only — if the server restarts between
  upload and send, you'll need to re-upload. Fine for how this tool is
  used (upload → review → send, all in one sitting).
- CC is currently just "CC each employee's manager" (per-employee, from
  the mapping file). A fixed CC list (e.g. always CC one HR manager) can
  be added easily — say the word once you have the name/email to add.
- No login/auth yet — this is meant to run on a machine only HR can reach,
  or behind whatever access control the hosting platform provides. Worth
  adding basic password protection before putting it on the open internet.

## Deploying so HR can use it without your laptop running

Free/cheap options that work well for a low-traffic internal tool like
this: **Render** or **Railway** (both have a "Web Service from GitHub
repo" flow — push this folder to a GitHub repo, connect it, it builds and
gives you a URL). Ask if you want a step-by-step for either.
