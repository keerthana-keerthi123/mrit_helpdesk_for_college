import json
import socket
import urllib.request
from io import BytesIO
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file
import qrcode
from difflib import SequenceMatcher
import threading
import subprocess
import time
import re

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "knowledge_base.json"
CALENDAR_PDF = BASE_DIR / "7th-sem-calendar-of-events.pdf"

app = Flask(__name__, static_folder="static", template_folder="templates")

# ---------------- NETWORK HANDLING ----------------
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def get_ngrok_url():
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels") as response:
            data = json.load(response)
            for tunnel in data.get("tunnels", []):
                if "public_url" in tunnel:
                    return tunnel["public_url"]
    except Exception:
        return None

def start_ngrok():
    try:
        subprocess.Popen(
            ["ngrok", "http", "5000"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print("⚠️ Ngrok not found. Install from https://ngrok.com/download and connect your account.")
        print("   Once installed, run: ngrok config add-authtoken YOUR_TOKEN")

# ---------------- KNOWLEDGE BASE ----------------
def load_kb():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

KB = load_kb()

PDF_DATA = KB.get("pdf_data", {})
CAL_EVENTS = PDF_DATA.get("calendar_events", [])
TT_A = PDF_DATA.get("timetable_A", [])
TT_B = PDF_DATA.get("timetable_B", [])
SUBJECTS = PDF_DATA.get("subjects", [])
SEM_QNA = KB.get("semester_qna", [])

# ---------------- TEXT & QUERY SYSTEM ----------------
def normalize_text(q: str) -> str:
    synonyms = {
        "faculties": "faculty",
        "professors": "faculty",
        "teachers": "faculty",
        "lecturers": "faculty",
        "staffs": "staff",
        "incharge": "hod",
        "head of department": "hod",
        "leader": "hod",
        "head": "hod",
        "dept": "department",
        "academic calendar": "calendar",
        "calendar of events": "calendar",
        "event calendar": "calendar",
        "events calendar": "calendar",
        "time table": "timetable",
        "time-table": "timetable",
        "fees": "fee",
        "examination": "exam",
        "7th sem": "seventh semester",
        "7th semester": "seventh semester",
        "7 th sem": "seventh semester",
    }
    q = q.lower().strip()
    for word, replacement in synonyms.items():
        q = q.replace(word, replacement)
    return q

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def contains_any(q: str, words):
    return any(w in q for w in words)

def intent_match(q: str, keywords):
    return any(k in q or similarity(q, k) > 0.72 for k in keywords)

def find_department(query_lc: str):
    for dept in KB.get("departments", []):
        name = dept.get("name", "").lower()
        short = dept.get("short", "").lower()
        if name in query_lc or short in query_lc:
            return dept
    best = None
    best_score = 0.0
    for dept in KB.get("departments", []):
        for key in [dept.get("name", ""), dept.get("short", "")]:
            score = similarity(query_lc, key.lower())
            if score > best_score:
                best_score = score
                best = dept
    if best_score > 0.6:
        return best
    return None

def find_calendar_event(q: str):
    best = None
    best_score = 0.0
    for ev in CAL_EVENTS:
        text = (ev.get("title", "") + " " + ev.get("date", "")).lower()
        score = similarity(q, text)
        if score > best_score:
            best_score = score
            best = ev
    if best_score > 0.55:
        return best
    return None

def find_semantic_qna(q: str):
    best = None
    best_score = 0.0
    for qa in SEM_QNA:
        ques = qa.get("question", "").lower()
        score = similarity(q, ques)
        if score > best_score:
            best_score = score
            best = qa
    if best_score > 0.7:
        return best
    return None

def find_subject_by_name_or_code(q: str):
    best = None
    best_score = 0.0
    for s in SUBJECTS:
        for key in [s.get("name", ""), s.get("code", "")]:
            score = similarity(q, key.lower())
            if score > best_score:
                best_score = score
                best = s
    if best_score > 0.6:
        return best
    return None

def find_day_timetable(tt_list, day_name):
    for row in tt_list:
        if row.get("day", "").lower() == day_name.lower():
            return row
    return None

def build_full_timetable_html(tt_list, section_label):
    if not tt_list:
        return "Timetable information is not available."
    
    max_periods = max(len(r.get("periods", [])) for r in tt_list)
    header_cells = "".join(f"<th>P{i}</th>" for i in range(1, max_periods + 1))
    rows_html = []
    
    for r in tt_list:
        periods = r.get("periods", [])
        row_cells = "".join(f"<td>{p}</td>" for p in periods)
        if len(periods) < max_periods:
            row_cells += "".join("<td>-</td>" for _ in range(max_periods - len(periods)))
        rows_html.append(f"<tr><td>{r.get('day','')}</td>{row_cells}</tr>")
    
    table = f"""
    <div class="timetable-container">
      <strong>📅 7th Semester {section_label.upper()} Timetable</strong>
      <table class="timetable">
        <thead>
          <tr><th>Day</th>{header_cells}</tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
    </div>
    """
    return table

def build_single_day_table_html(row, section_label):
    periods = row.get("periods", [])
    if not periods:
        return f"No timetable available for {row.get('day', 'this day')}."
    
    header_cells = "".join(f"<th>P{i}</th>" for i in range(1, len(periods) + 1))
    row_cells = "".join(f"<td>{p}</td>" for p in periods)
    
    table = f"""
    <div class="timetable-container">
      <strong>📋 {row.get('day','').capitalize()} - 7th Sem {section_label.upper()}</strong>
      <table class="timetable">
        <thead>
          <tr><th>Day</th>{header_cells}</tr>
        </thead>
        <tbody>
          <tr><td>{row.get('day','')}</td>{row_cells}</tr>
        </tbody>
      </table>
    </div>
    """
    return table

def answer_query(query: str) -> str:
    q = normalize_text(query)

    college = KB.get("college", {})
    principal = college.get("principal", {})
    vice_principal = college.get("vice principal", {})

    # ---- Direct semester_qna ----
    qa = find_semantic_qna(q)
    if qa:
        return qa.get("answer", "Information not available.")

    # ---- Calendar link ----
    if intent_match(q, ["calendar", "schedule of events", "exam schedule", "academic schedule"]):
        return "You can view or download the Academic Calendar here: <a href='/calendar' target='_blank'>Open Academic Calendar (PDF)</a>"

    # ---- Specific calendar event ----
    if intent_match(q, ["independence day", "ganesha", "deepavali", "conference", "rajyotsava",
                       "phase-1", "phase-2", "cie-1", "cie-2", "industrial visit",
                       "last working day", "lab internals", "report submission", "practical exams", "theory exams"]):
        ev = find_calendar_event(q)
        if ev:
            return f"{ev.get('title', 'Event')}: {ev.get('date', 'Date not available')}."

    # ---- Vice Principal ----
    if any(word in q for word in ["vice principal", "viceprincipal", "vp", "assistant principal"]):
        name = vice_principal.get("name", "Not available")
        spec = vice_principal.get("specialization", "")
        detail = f" (Specialization: {spec})" if spec else ""
        return f"Vice Principal: {name}{detail}"

    # ---- Principal ----
    if any(word in q for word in ["principal", "head of college", "college principal"]):
        name = principal.get("name", "Not available")
        spec = principal.get("specialization", "")
        contact = principal.get("contact", "")
        extra = []
        if spec:
            extra.append(f"Specialization: {spec}")
        if contact:
            extra.append(f"Contact: {contact}")
        detail = " · ".join(extra)
        return f"Principal: {name}" + (f" ({detail})" if detail else "")

    # ---- HOD ----
    if intent_match(q, ["hod", "head of department"]):
        dept = find_department(q)
        if dept:
            return f"HOD of {dept['name']}: {dept.get('hod', 'Not available')}"
        else:
            return "Please specify a valid department for HOD information."

    # ---- Faculty ----
    if intent_match(q, ["faculty", "professor", "staff"]):
        dept = find_department(q)
        if dept:
            members = ", ".join(f['name'] for f in dept.get("faculty", []))
            return f"{dept['name']} Faculty Members: {members}"
        else:
            return "Please specify a valid department for faculty information."

    # ---- Fees ----
    if intent_match(q, ["fee", "exam fee", "payment", "tuition"]):
        fees = KB.get("fees", {})
        exam_last = fees.get("exam_fee_last_date", "N/A")
        tuition_last = fees.get("tuition_fee_last_date", "N/A")
        portal = fees.get("payment_portal", "N/A")

        dept = find_department(q)
        if dept:
            short = dept.get("short", "").lower()
            dept_fees = fees.get("department_fees", {}).get(short, {})
            t = dept_fees.get("tuition")
            e = dept_fees.get("exam")
            parts = []
            if t:
                parts.append(f"{dept['name']} Tuition: {t}")
            if e:
                parts.append(f"{dept['name']} Exam Fee: {e}")
            parts.append(f"Tuition Last Date: {tuition_last}")
            parts.append(f"Exam Fee Last Date: {exam_last}")
            parts.append(f"Payment via: {portal}")
            return " | ".join(parts)

        return f"Tuition Last Date: {tuition_last} | Exam Fee Last Date: {exam_last} | Payment via: {portal}"

    # ---- Departments (only general info) ----
    if intent_match(q, ["department", "cse", "ece", "computer", "electronics"]) and not intent_match(q, ["hod", "faculty", "professor", "staff"]):
        dept = find_department(q)
        if dept:
            name = dept.get("name", "Department")
            loc = dept.get("location", "Location not available")
            courses = ", ".join(dept.get("courses", [])) or "Not specified"
            return f"{name} is located at {loc}. Courses offered: {courses}."
        else:
            return "Please specify a valid department."

    # ---- Timetable (HTML structured table) ----
    if intent_match(q, ["timetable", "class schedule", "time table", "periods"]):
        section = "A"
        if " section b" in q or " b " in q or "sem b" in q:
            section = "B"

        tt_list = TT_A if section == "A" else TT_B
        days = ["monday", "tuesday", "wednesday", "thursday", "friday"]
        day_in_q = None
        for d in days:
            if d in q:
                day_in_q = d
                break

        if day_in_q:
            row = find_day_timetable(tt_list, day_in_q)
            if row:
                return build_single_day_table_html(row, section)
            else:
                return f"Timetable for {day_in_q.capitalize()} (7th sem {section}) not available."
        else:
            return build_full_timetable_html(tt_list, section)

    # ---- Subjects (codes, faculty, credits) ----
    if intent_match(q, ["subject", "code", "credits", "faculty for", "who teaches", "teacher of"]):
        subj = find_subject_by_name_or_code(q)
        if subj:
            code = subj.get("code", "")
            name = subj.get("name", "")
            fac = subj.get("faculty", "Faculty not specified")
            credits = subj.get("credits", None)
            parts = [f"{code} — {name}", f"Faculty: {fac}"]
            if credits is not None:
                parts.append(f"Credits: {credits}")
            return " | ".join(parts)
        else:
            return "Please specify a valid subject."

    # ---- Facilities ----
    if intent_match(q, ["library", "canteen", "hostel", "facility", "facilities"]):
        facs = KB.get("facilities", [])
        for f in facs:
            name_l = f.get("name", "").lower()
            if name_l and name_l in q:
                loc = f.get("location", "Location not available")
                hours = f.get("hours", "")
                dirn = f.get("directions", "")
                notes = f.get("notes", "")
                parts = [f"{f['name']} — {loc}"]
                if hours:
                    parts.append(f"Hours: {hours}")
                if notes:
                    parts.append(f"Notes: {notes}")
                if dirn:
                    parts.append(f"Directions: {dirn}")
                return " | ".join(parts)
        brief = []
        for f in facs:
            brief.append(f"{f.get('name', 'Facility')} — {f.get('location', 'Location not available')}")
        if brief:
            return "Facilities: " + " | ".join(brief)

    # ---- Labs ----
    if intent_match(q, ["lab", "laboratory"]):
        labs = KB.get("labs", [])
        for lab in labs:
            name_l = lab.get("name", "").lower()
            if name_l and (name_l in q or name_l.split()[0] in q):
                loc = lab.get("location", "Location not available")
                dirn = lab.get("directions", "")
                parts = [f"{lab['name']} — {loc}"]
                if dirn:
                    parts.append(f"Directions: {dirn}")
                return " | ".join(parts)
        short = []
        for lab in labs:
            short.append(f"{lab.get('name', 'Lab')} — {lab.get('location', 'Location not available')}")
        if short:
            return "Labs: " + " | ".join(short)

    # ---- Events ----
    if intent_match(q, ["event", "orientation", "hackathon", "function"]):
        events = KB.get("events", [])
        if not events:
            return "No events information is available right now."
        lines = []
        for e in events:
            title = e.get("title", "Event")
            date = e.get("date", "Date N/A")
            venue = e.get("venue", "Venue N/A")
            lines.append(f"{title} — {date} at {venue}")
        return "Upcoming / scheduled events: " + " | ".join(lines)

    # ---- College name ----
    if intent_match(q, ["college name", "what is this college", "which college", "name of college"]):
        return f"This helpdesk is for: {college.get('name', 'Our College')}."

    # ---- Directions generic ----
    if contains_any(q, ["where is", "location of", "how to reach", "how do i go"]):
        dept = find_department(q)
        if dept:
            loc = dept.get("location", "Location not available")
            dirn = dept.get("directions", "")
            ans = f"{dept['name']} is at {loc}."
            if dirn:
                ans += f" Directions: {dirn}"
            return ans

        facs = KB.get("facilities", [])
        for f in facs:
            name_l = f.get("name", "").lower()
            if name_l and name_l in q:
                loc = f.get("location", "Location not available")
                dirn = f.get("directions", "")
                ans = f"{f['name']} is at {loc}."
                if dirn:
                    ans += f" Directions: {dirn}"
                return ans

    # ---- Fallback ----
    return ("I can help with details about principal, <strong>vice principal</strong>, HOD, faculty, fees, <strong>timetable tables</strong>, "
            "departments, labs, facilities, semester calendar events, subjects, and academic calendar PDF. "
            "Try asking: 'Vice principal', 'CSE HOD', '<strong>Monday timetable for 7th sem A</strong>', "
            "'Who teaches IoT?', or 'Exam fee last date?'.")

def split_questions(text: str):
    parts = re.split(r"\s*[\.\?\;]+\s*|\s+and\s+", text)
    questions = [q.strip() for q in parts if q.strip()]
    return questions

# ---------------- ROUTES ----------------
@app.route("/")
def home():
    base_url = getattr(app, "public_url", None)
    if not base_url:
        base_url = f"http://{get_local_ip()}:5000"
    college_name = KB.get("college", {}).get("name", "Our College")
    return render_template("index.html", base_url=base_url, college_name=college_name)

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(force=True)
    query = (data.get("question") or "").strip()
    if not query:
        return jsonify({"answer": "Please type a question."})
    questions = split_questions(query)
    answers = []
    for q in questions:
        ans = answer_query(q)
        answers.append(ans)
    return jsonify({"answer": "<br>".join(answers)})

@app.route("/calendar")
def calendar_pdf():
    return send_file(
        CALENDAR_PDF,
        download_name="7th-sem-calendar-of-events.pdf",
        mimetype="application/pdf",
    )

@app.route("/qr")
def qr():
    url = getattr(app, "public_url", None)
    if not url:
        url = f"http://{get_local_ip()}:5000"
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")

# ---------------- RUN SERVER ----------------
if __name__ == "__main__":
    print("🚀 Starting Flask server...")
    threading.Thread(target=start_ngrok).start()
    print("🌐 Creating public ngrok tunnel... Please wait 5–10 seconds.")
    time.sleep(8)
    app.public_url = get_ngrok_url()
    if app.public_url:
        print(f"✅ PUBLIC URL: {app.public_url}")
        print("📱 Open /qr to scan from any network.")
    else:
        print("⚠️ Could not detect ngrok link. The app is only accessible on local Wi‑Fi.")
    app.run(host="0.0.0.0", port=5000, debug=True)
