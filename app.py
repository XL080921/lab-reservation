import sqlite3, os, json
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import parse_qs
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, g

app = Flask(__name__)
app.secret_key = os.urandom(24)
DATABASE = os.path.join(os.path.dirname(__file__), "lab.db")

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db: db.close()

def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS equipment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            model TEXT DEFAULT '',
            category TEXT DEFAULT '',
            location TEXT DEFAULT '',
            status TEXT DEFAULT 'available' CHECK(status IN ('available','in_use','maintenance','damaged')),
            description TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equipment_id INTEGER NOT NULL REFERENCES equipment(id),
            user_name TEXT NOT NULL,
            purpose TEXT DEFAULT '',
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled','completed')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id INTEGER NOT NULL REFERENCES reservations(id),
            admin_name TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('approve','reject')),
            comment TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS damage_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equipment_id INTEGER NOT NULL REFERENCES equipment(id),
            reservation_id INTEGER,
            reporter TEXT NOT NULL,
            description TEXT NOT NULL,
            severity TEXT DEFAULT 'minor' CHECK(severity IN ('minor','major','critical')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db.commit()
    db.close()

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            flash("需要管理员权限", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def get_req_data():
    if request.is_json:
        return request.get_json(silent=True) or {}
    if request.form:
        d = {}
        for k in request.form: d[k] = request.form[k]
        return d
    raw = request.get_data(as_text=True)
    if raw:
        try:
            parsed = parse_qs(raw)
            return {k: v[0] if isinstance(v,list) else v for k,v in parsed.items()}
        except: pass
    return {}

def check_time_conflict(equipment_id, start, end, exclude_id=None):
    db = get_db()
    query = """
        SELECT id, user_name, start_time, end_time FROM reservations
        WHERE equipment_id = ? AND status IN ('pending','approved')
          AND start_time < ? AND end_time > ?
    """
    params = [equipment_id, end, start]
    if exclude_id:
        query += " AND id != ?"
        params.append(exclude_id)
    conflicts = db.execute(query, params).fetchall()
    return [dict(r) for r in conflicts]

@app.route("/")
def index():
    db = get_db()
    equipment = db.execute("SELECT * FROM equipment ORDER BY category, name").fetchall()
    return render_template("index.html", equipment=equipment, now=datetime.now().strftime("%Y-%m-%dT%H:%M"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        pw = request.form.get("password","")
        if pw == "admin123":
            session["is_admin"] = True
            session["admin_name"] = "管理员"
            flash("管理员登录成功", "success")
            return redirect(url_for("admin"))
        flash("密码错误", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("已退出登录", "info")
    return redirect(url_for("index"))

@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    equipment = db.execute("SELECT * FROM equipment ORDER BY id").fetchall()
    reservations = db.execute("""
        SELECT r.*, e.name as equipment_name FROM reservations r
        JOIN equipment e ON r.equipment_id = e.id
        ORDER BY r.created_at DESC
    """).fetchall()
    damage_reports = db.execute("""
        SELECT d.*, e.name as equipment_name FROM damage_reports d
        JOIN equipment e ON d.equipment_id = e.id
        ORDER BY d.created_at DESC
    """).fetchall()
    return render_template("admin.html",
        equipment=equipment, reservations=reservations,
        damage_reports=damage_reports,
        now=datetime.now().strftime("%Y-%m-%dT%H:%M"))

@app.route("/stats")
def stats():
    db = get_db()
    total_eq = db.execute("SELECT COUNT(*) FROM equipment").fetchone()[0]
    status_count = db.execute(
        "SELECT status, COUNT(*) as cnt FROM equipment GROUP BY status"
    ).fetchall()
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    month_reservations = db.execute(
        "SELECT COUNT(*) FROM reservations WHERE created_at >= ?", [month_start]
    ).fetchone()[0]
    total_req = db.execute("SELECT COUNT(*) FROM reservations WHERE status IN ('approved','rejected')").fetchone()[0]
    approved = db.execute("SELECT COUNT(*) FROM reservations WHERE status='approved'").fetchone()[0]
    approve_rate = round(approved / total_req * 100, 1) if total_req > 0 else 0
    top_equipment = db.execute("""
        SELECT e.name, COUNT(r.id) as cnt
        FROM equipment e LEFT JOIN reservations r ON e.id = r.equipment_id AND r.status='approved'
        GROUP BY e.id ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    damage_count = db.execute(
        "SELECT severity, COUNT(*) as cnt FROM damage_reports GROUP BY severity"
    ).fetchall()
    return render_template("stats.html",
        total_eq=total_eq, status_count=status_count,
        month_reservations=month_reservations, approve_rate=approve_rate,
        top_equipment=top_equipment, damage_count=damage_count)

@app.route("/api/equipment", methods=["GET"])
def api_equipment_list():
    db = get_db()
    rows = db.execute("SELECT * FROM equipment ORDER BY category, name").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/equipment/<int:eid>", methods=["GET"])
def api_equipment_get(eid):
    db = get_db()
    eq = db.execute("SELECT * FROM equipment WHERE id=?", [eid]).fetchone()
    if not eq: return jsonify({"error":"设备不存在"}), 404
    return jsonify(dict(eq))

@app.route("/api/equipment", methods=["POST"])
@admin_required
def api_equipment_add():
    data = get_req_data()
    db = get_db()
    db.execute("""
        INSERT INTO equipment (name, model, category, location, description)
        VALUES (?,?,?,?,?)
    """, [data.get("name",""), data.get("model",""), data.get("category",""),
          data.get("location",""), data.get("description","")])
    db.commit()
    return jsonify({"ok":True, "id": db.execute("SELECT last_insert_rowid()").fetchone()[0]})

@app.route("/api/equipment/<int:eid>", methods=["PUT"])
@admin_required
def api_equipment_update(eid):
    data = get_req_data()
    db = get_db()
    db.execute("""
        UPDATE equipment SET name=?, model=?, category=?, location=?, status=?, description=?
        WHERE id=?
    """, [data.get("name",""), data.get("model",""), data.get("category",""),
          data.get("location",""), data.get("status","available"),
          data.get("description",""), eid])
    db.commit()
    return jsonify({"ok":True})

@app.route("/api/equipment/<int:eid>", methods=["DELETE"])
@admin_required
def api_equipment_delete(eid):
    db = get_db()
    db.execute("DELETE FROM equipment WHERE id=?", [eid])
    db.commit()
    return jsonify({"ok":True})

@app.route("/api/reservations", methods=["POST"])
def api_reservation_create():
    data = get_req_data()
    equipment_id = int(data.get("equipment_id",0))
    user_name = data.get("user_name","").strip()
    start_time = data.get("start_time","")
    end_time = data.get("end_time","")
    purpose = data.get("purpose","")

    if not user_name: return jsonify({"error":"请输入借用人姓名"}), 400
    if not start_time or not end_time: return jsonify({"error":"请填写预约时间"}), 400
    if start_time >= end_time: return jsonify({"error":"开始时间必须早于结束时间"}), 400

    db = get_db()
    eq = db.execute("SELECT * FROM equipment WHERE id=?", [equipment_id]).fetchone()
    if not eq: return jsonify({"error":"设备不存在"}), 404
    if eq["status"] in ("maintenance","damaged"):
        return jsonify({"error":"该设备当前不可预约（维修/损坏中）"}), 400

    conflicts = check_time_conflict(equipment_id, start_time, end_time)
    if conflicts:
        return jsonify({
            "error": "该时间段已被预约，存在时间冲突",
            "conflicts": conflicts
        }), 409

    db.execute("""
        INSERT INTO reservations (equipment_id, user_name, purpose, start_time, end_time)
        VALUES (?,?,?,?,?)
    """, [equipment_id, user_name, purpose, start_time, end_time])
    db.commit()
    rid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return jsonify({"ok":True, "id":rid, "message":"预约申请已提交，等待管理员审批"})

@app.route("/api/reservations/<int:rid>/cancel", methods=["POST"])
def api_reservation_cancel(rid):
    db = get_db()
    r = db.execute("SELECT * FROM reservations WHERE id=?", [rid]).fetchone()
    if not r: return jsonify({"error":"预约不存在"}), 404
    if r["status"] in ("approved","completed"):
        return jsonify({"error":"已审批通过的预约无法取消，请联系管理员"}), 400
    db.execute("UPDATE reservations SET status='cancelled' WHERE id=?", [rid])
    db.commit()
    return jsonify({"ok":True})

@app.route("/api/approve/<int:rid>", methods=["POST"])
@admin_required
def api_approve(rid):
    db = get_db()
    r = db.execute("SELECT * FROM reservations WHERE id=?", [rid]).fetchone()
    if not r: return jsonify({"error":"预约不存在"}), 404
    if r["status"] != "pending": return jsonify({"error":"该预约不在待审批状态"}), 400

    conflicts = check_time_conflict(r["equipment_id"], r["start_time"], r["end_time"], exclude_id=rid)
    if conflicts:
        return jsonify({"error":"审批时检测到时间冲突","conflicts":conflicts}), 409

    action = request.form.get("action","approve")
    comment = request.form.get("comment","")
    db.execute("UPDATE reservations SET status=? WHERE id=?", [action + "d" if action=="approve" else action + "ed", rid])
    db.execute("INSERT INTO approvals (reservation_id, admin_name, action, comment) VALUES (?,?,?,?)",
               [rid, session.get("admin_name","管理员"), action, comment])
    if action == "approve":
        db.execute("UPDATE equipment SET status='in_use' WHERE id=?", [r["equipment_id"]])
    db.commit()
    return jsonify({"ok":True})

@app.route("/api/reservations/<int:rid>/complete", methods=["POST"])
@admin_required
def api_complete(rid):
    db = get_db()
    r = db.execute("SELECT * FROM reservations WHERE id=?", [rid]).fetchone()
    if not r: return jsonify({"error":"预约不存在"}), 404
    db.execute("UPDATE reservations SET status='completed' WHERE id=?", [rid])
    active = db.execute(
        "SELECT COUNT(*) FROM reservations WHERE equipment_id=? AND status IN ('approved','pending') AND id!=?",
        [r["equipment_id"], rid]
    ).fetchone()[0]
    if active == 0:
        db.execute("UPDATE equipment SET status='available' WHERE id=?", [r["equipment_id"]])
    db.commit()
    return jsonify({"ok":True})

@app.route("/api/damage", methods=["POST"])
def api_damage_report():
    data = get_req_data()
    db = get_db()
    db.execute("""
        INSERT INTO damage_reports (equipment_id, reservation_id, reporter, description, severity)
        VALUES (?,?,?,?,?)
    """, [data.get("equipment_id"), data.get("reservation_id") or None,
          data.get("reporter",""), data.get("description",""), data.get("severity","minor")])
    db.execute("UPDATE equipment SET status='damaged' WHERE id=?", [data.get("equipment_id")])
    db.commit()
    return jsonify({"ok":True, "message":"报损信息已提交"})

@app.route("/api/calendar/<int:eid>")
def api_calendar(eid):
    db = get_db()
    reservations = db.execute("""
        SELECT id, user_name, purpose, start_time, end_time, status
        FROM reservations
        WHERE equipment_id=? AND status IN ('approved','pending')
        ORDER BY start_time
    """, [eid]).fetchall()
    return jsonify([dict(r) for r in reservations])

if __name__ == "__main__":
    if not os.path.exists(DATABASE):
        init_db()
        db = sqlite3.connect(DATABASE)
        sample_equipment = [
            ("示波器 TDS2024C", "TDS2024C", "电子测量", "A101实验室", "4通道数字存储示波器，200MHz"),
            ("信号发生器 AFG31000", "AFG31000", "电子测量", "A101实验室", "任意波形发生器"),
            ("万用表 Fluke 87V", "Fluke 87V", "电子测量", "A102实验室", "高精度数字万用表"),
            ("3D打印机 Ultimaker S5", "Ultimaker S5", "制造设备", "B201实验室", "双喷头FDM打印机"),
            ("激光切割机", "LC-1390", "制造设备", "B202实验室", "CO2激光切割机，100W"),
            ("高速离心机", "Sorvall LYNX 6000", "生物设备", "C301实验室", "最高转速29000rpm"),
            ("PCR仪", "ProFlex 3x32", "生物设备", "C302实验室", "三模块PCR扩增仪"),
            ("光谱分析仪", "AQ6370D", "光学设备", "D101实验室", "600-1700nm光谱分析"),
        ]
        db.executemany(
            "INSERT INTO equipment (name, model, category, location, description) VALUES (?,?,?,?,?)",
            sample_equipment
        )
        db.commit()
        db.close()
        print("[OK] Database initialized with 8 sample devices")
    app.run(debug=True, host="0.0.0.0", port=5000)
