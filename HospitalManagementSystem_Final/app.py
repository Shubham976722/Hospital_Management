from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3, os, random, io, base64, qrcode
from datetime import datetime
from database import init_db

app = Flask(__name__)
app.secret_key = 'hospital_secret_key'

DB_PATH = 'hospital.db'

# ------------------- Initialize DB if not present -------------------
if not os.path.exists(DB_PATH):
    init_db()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ----------------- AUTO EXPIRE OLD APPOINTMENTS -----------------
def expire_old_appointments():
    """
    Marks past 'scheduled' appointments as 'expired' using current system date/time.
    Compares against appointment_date (YYYY-MM-DD) and appointment_time (HH:MM[:SS]).
    """
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M:%S")

    conn = get_db_connection()
    conn.execute("""
        UPDATE appointments
        SET status = 'expired'
        WHERE status = 'scheduled'
          AND (
                appointment_date < ?
                OR (appointment_date = ? AND appointment_time < ?)
              )
    """, (current_date, current_date, current_time))
    conn.commit()
    conn.close()

@app.before_request
def before_request():
    # Keep DB status up-to-date for doctor/patient views
    expire_old_appointments()

# ------------------- HOME PAGE -------------------
@app.route('/')
def index():
    return render_template('index.html')

# ------------------- ADMIN LOGIN -------------------
@app.route('/login/admin', methods=['GET', 'POST'])
def login_admin():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        conn = get_db_connection()
        admin = conn.execute(
            "SELECT * FROM admin WHERE username=? AND password=?",
            (username, password)
        ).fetchone()
        conn.close()
        if admin:
            session['admin'] = username
            return redirect(url_for('admin_dashboard'))
        else:
            flash("Invalid admin credentials", "danger")
    return render_template('login_admin.html', user_type='admin')

@app.route('/admin/home')
def admin_home():
    if 'admin' not in session:
        return redirect(url_for('login_admin'))
    return render_template('admin_home.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'admin' not in session:
        return redirect(url_for('login_admin'))
    conn = get_db_connection()
    doctors = conn.execute("SELECT * FROM doctors").fetchall()
    conn.close()
    return render_template('admin/dashboard.html', doctors=doctors)

@app.route('/admin/delete_doctor/<int:doctor_id>', methods=['POST'])
def delete_doctor(doctor_id):
    if 'admin' not in session:
        return redirect(url_for('login_admin'))
    conn = get_db_connection()
    conn.execute("DELETE FROM doctors WHERE id = ?", (doctor_id,))
    conn.commit()
    conn.close()
    flash('Doctor deleted successfully.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add_doctor', methods=['GET', 'POST'])
def add_doctor():
    if 'admin' not in session:
        return redirect(url_for('login_admin'))

    if request.method == 'POST':
        name = request.form['name'].strip()
        specialization = request.form['specialization'].strip()
        experience = request.form['experience'].strip()
        mobile = request.form['mobile'].strip()
        email = request.form['email'].strip()
        password = request.form['password']

        conn = get_db_connection()

        # Generate a unique 3-digit id
        while True:
            random_id = random.randint(100, 999)
            existing = conn.execute("SELECT 1 FROM doctors WHERE id = ?", (random_id,)).fetchone()
            if not existing:
                break

        conn.execute("""
            INSERT INTO doctors (id, name, specialization, experience, mobile, email, password)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (random_id, name, specialization, experience, mobile, email, password))
        conn.commit()
        conn.close()

        flash("Doctor added successfully", "success")
        return redirect(url_for('admin_dashboard'))

    return render_template('admin/add_doctor.html')

@app.route('/admin/patients')
def view_patients():
    if 'admin' not in session:
        return redirect(url_for('login_admin'))
    conn = get_db_connection()
    patients = conn.execute("SELECT * FROM patients").fetchall()
    conn.close()
    return render_template('admin/view_patients.html', patients=patients)

@app.route('/admin/payments')
def view_payments():
    if 'admin' not in session:
        return redirect(url_for('login_admin'))
    conn = get_db_connection()
    payments = conn.execute("""
        SELECT payments.id, patients.name, payments.amount, payments.created_at
        FROM payments
        JOIN patients ON payments.patient_id = patients.id
        ORDER BY payments.created_at DESC, payments.id DESC
    """).fetchall()
    conn.close()
    return render_template('admin/view_payments.html', payments=payments)

# -------- Admin - Appointments (live Expired/Active using SQLite datetime) --------
@app.route("/admin/view_appointments")
def view_appointments():
    from datetime import datetime
    conn = get_db_connection()
    cursor = conn.cursor()

    # Step 1: Update expired appointments in one go
    cursor.execute("""
        UPDATE appointments
        SET status='Expired'
        WHERE status != 'Expired'
          AND datetime(appointment_date || ' ' || appointment_time) < datetime('now')
    """)
    conn.commit()

    # Step 2: Fetch updated appointment list with JOINs
    cursor.execute("""
        SELECT a.id, a.appointment_date, a.appointment_time, a.status,
               d.name AS doctor_name, p.name AS patient_name
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        JOIN patients p ON a.patient_id = p.id
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
    """)
    rows = cursor.fetchall()

    # Step 3: Convert to list of dictionaries for template
    appointments = [
        {
            "id": row["id"],
            "appointment_date": row["appointment_date"],
            "appointment_time": row["appointment_time"],
            "doctor_name": row["doctor_name"],
            "patient_name": row["patient_name"],
            "status": row["status"]
        }
        for row in rows
    ]

    conn.close()
    return render_template("view_appointments.html", appointments=appointments)

@app.route('/logout/admin')
def logout_admin():
    session.pop('admin', None)
    flash('Admin logged out successfully.', 'success')
    return redirect(url_for('index'))

# ------------------- DOCTOR LOGIN -------------------
@app.route('/login/doctor', methods=['GET', 'POST'])
def login_doctor():
    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password']
        conn = get_db_connection()
        doctor = conn.execute(
            'SELECT * FROM doctors WHERE email = ? AND password = ?',
            (email, password)
        ).fetchone()
        conn.close()
        if doctor:
            session['doctor_id'] = doctor['id']
            return redirect(url_for('doctor_dashboard'))
        else:
            flash('Invalid email or password.', 'danger')
    return render_template('login_doctor.html')

@app.route('/doctor/dashboard')
def doctor_dashboard():
    if 'doctor_id' not in session:
        return redirect(url_for('login_doctor'))
    conn = get_db_connection()
    doctor = conn.execute("SELECT * FROM doctors WHERE id=?", (session['doctor_id'],)).fetchone()
    conn.close()
    return render_template('doctor/dashboard.html', doctor=doctor)


@app.route('/doctor/appointments')
def doctor_appointments():
    if 'doctor_id' not in session:
        return redirect(url_for('login_doctor'))

    conn = get_db_connection()
    appointments = conn.execute("""
        SELECT a.id,
               p.name AS patient_name,
               a.appointment_date,
               a.appointment_time,
               a.status
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        WHERE a.doctor_id = ?
        ORDER BY a.appointment_date ASC, a.appointment_time ASC
    """, (session['doctor_id'],)).fetchall()
    conn.close()

    # Convert to list and update status for expired ones
    updated_appointments = []
    now = datetime.now()

    for appt in appointments:
        appt_date = datetime.strptime(appt['appointment_date'], "%Y-%m-%d")
        appt_time = datetime.strptime(appt['appointment_time'], "%H:%M").time()
        appt_datetime = datetime.combine(appt_date, appt_time)

        status = "Expired" if appt_datetime < now else appt['status']
        updated_appointments.append((
            appt['id'],
            appt['patient_name'],
            appt['appointment_date'],
            appt['appointment_time'],
            status
        ))

    return render_template('doctor/appointments.html', appointments=updated_appointments)

@app.route('/doctor/prescriptions', methods=['GET', 'POST'])
def doctor_prescriptions():
    if 'doctor_id' not in session:
        return redirect(url_for('login_doctor'))
    conn = get_db_connection()
    patients = conn.execute("""
        SELECT DISTINCT p.id, p.name
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        WHERE a.doctor_id = ?
        ORDER BY p.name ASC
    """, (session['doctor_id'],)).fetchall()

    if request.method == 'POST':
        patient_id = request.form['patient_id']
        diagnosis = request.form['diagnosis'].strip()
        medicine = request.form['medicine'].strip()
        conn.execute("""
            INSERT INTO prescriptions (doctor_id, patient_id, diagnosis, medicine)
            VALUES (?, ?, ?, ?)
        """, (session['doctor_id'], patient_id, diagnosis, medicine))
        conn.commit()
        conn.close()
        flash("Prescription submitted", "success")
        return redirect(url_for('doctor_dashboard'))

    conn.close()
    return render_template('doctor/prescriptions.html', patients=patients)

@app.route('/doctor/update_profile', methods=['GET', 'POST'])
def update_doctor_profile():
    """
    Doctors table columns: id, name, specialization, email, password
    """
    if 'doctor_id' not in session:
        return redirect(url_for('login_doctor'))
    conn = get_db_connection()
    doctor = conn.execute("SELECT * FROM doctors WHERE id=?", (session['doctor_id'],)).fetchone()

    if request.method == 'POST':
        name = request.form['name'].strip()
        specialization = request.form['specialization'].strip()
        email = request.form['email'].strip()

        conn.execute("""
            UPDATE doctors
            SET name = ?, specialization = ?, email = ?
            WHERE id = ?
        """, (name, specialization, email, session['doctor_id']))
        conn.commit()
        conn.close()
        flash("Profile updated successfully", "success")
        return redirect(url_for('doctor_dashboard'))

    conn.close()
    return render_template('doctor/update_profile.html', doctor=doctor)

@app.route('/logout/doctor')
def logout_doctor():
    session.pop('doctor_id', None)
    flash("Logged out", "success")
    return redirect(url_for('index'))

# ------------------- PATIENT LOGIN / REGISTER -------------------
@app.route('/login/patient', methods=['GET', 'POST'])
def login_patient():
    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password']
        conn = get_db_connection()
        patient = conn.execute(
            "SELECT * FROM patients WHERE email=? AND password=?",
            (email, password)
        ).fetchone()
        conn.close()
        if patient:
            session["user_id"] = patient['id']
            session["role"] = "patient"
            session['patient'] = email
            return redirect(url_for('patient_dashboard'))
        else:
            flash("Invalid credentials", "danger")
    return render_template('login_patient.html', user_type='patient')

@app.route('/register/patient', methods=['GET', 'POST'])
def register_patient():
    if request.method == 'POST':
        name = request.form['name'].strip()
        age = request.form['age']
        dob = request.form['dob']
        gender = request.form['gender']
        mobile = request.form['mobile']
        blood_group = request.form['blood_group']
        address = request.form['address']
        email = request.form['email'].strip()
        password = request.form['password']

        conn = get_db_connection()

        # Generate a unique 3-digit ID for patients too
        while True:
            random_id = random.randint(100, 999)
            existing = conn.execute("SELECT 1 FROM patients WHERE id = ?", (random_id,)).fetchone()
            if not existing:
                break

        conn.execute("""
            INSERT INTO patients (id, name, age,dob, gender, mobile, blood_group, address, email, password)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (random_id, name, age, dob, gender, mobile, blood_group, address, email, password))
        conn.commit()
        conn.close()
        flash("Registered successfully!", "success")
        return redirect(url_for('login_patient'))

    return render_template('register_patient.html')

@app.route('/patient/dashboard')
def patient_dashboard():
    if 'patient' not in session:
        return redirect(url_for('login_patient'))
    conn = get_db_connection()
    patient = conn.execute("SELECT * FROM patients WHERE email=?", (session['patient'],)).fetchone()
    conn.close()
    return render_template('patient/dashboard.html', patient=patient)

@app.route('/logout/patient')
def logout_patient():
    session.pop('patient', None)
    flash("Logged out", "success")
    return redirect(url_for('index'))

@app.route('/patient/update_profile', methods=['GET', 'POST'])
def update_profile():
    if 'patient' not in session:
        return redirect(url_for('login_patient'))
    conn = get_db_connection()
    patient = conn.execute("SELECT * FROM patients WHERE email=?", (session['patient'],)).fetchone()

    if request.method == 'POST':
        name = request.form['name'].strip()
        age = request.form['age']
        gender = request.form['gender']
        email = request.form['email'].strip()

        conn.execute("""
            UPDATE patients
            SET name = ?, age = ?, gender = ?, email = ?
            WHERE id = ?
        """, (name, age, gender, email, patient['id']))
        conn.commit()
        conn.close()

        # Keep session in sync if email changed
        session['patient'] = email
        flash("Profile updated", "success")
        return redirect(url_for('patient_dashboard'))

    conn.close()
    return render_template('patient/update_profile.html', patient=patient)

# ------------------- PATIENT VIEW APPOINTMENTS -------------------

@app.route("/admin/payments", methods=["GET"])
def admin_payments():
    if 'admin' not in session:
        return redirect(url_for("login_admin"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.amount, p.date, pt.name AS patient_name
        FROM payments p
        JOIN patients pt ON p.patient_id = pt.id
        ORDER BY p.date DESC
    """)
    payments = cursor.fetchall()
    conn.close()

    return render_template("admin/payments.html", payments=payments)

# -------------------- PATIENT PAYMENT --------------------
@app.route('/patient/payment', methods=['GET', 'POST'])
def patient_payment():
    if 'patient' not in session:
        return redirect(url_for('login_patient'))

    conn = get_db_connection()
    patient = conn.execute("SELECT * FROM patients WHERE email=?", (session['patient'],)).fetchone()

    if request.method == 'POST':
        appointment_id = request.form['appointment_id']
        amount = request.form['amount']
        method = request.form['method']

        upi_id = request.form['upi_id'] if method == "UPI" else None
        transaction_id = request.form['transaction_id'] if method in ["UPI", "Card"] else None

        conn.execute("""
            INSERT INTO payments (appointment_id, patient_id, amount, method, upi_id, transaction_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (appointment_id, patient['id'], amount, method, upi_id, transaction_id, 'Success'))
        
        conn.commit()
        conn.close()

        flash("Payment successful!", "success")
        return redirect(url_for('patient_dashboard'))

    # Fetch patient appointments to let them select which appointment they are paying for
    appointments = conn.execute("""
        SELECT * FROM appointments WHERE patient_id=?
    """, (patient['id'],)).fetchall()
    conn.close()

    return render_template("patient/payment.html", appointments=appointments)



# ------------------- PATIENT BOOK APPOINTMENT -------------------
@app.route('/patient/book_appointment', methods=['GET', 'POST'])
def book_appointment():
    if 'patient' not in session:
        return redirect(url_for('login_patient'))
    conn = get_db_connection()
    doctors = conn.execute("SELECT * FROM doctors").fetchall()
    patient = conn.execute("SELECT * FROM patients WHERE email=?", (session['patient'],)).fetchone()

    if request.method == 'POST':
        doctor_id = request.form['doctor_id']
        date = request.form['date']   # Expect 'YYYY-MM-DD'
        time = request.form['time']   # Expect 'HH:MM' or 'HH:MM:SS'

        conn.execute("""
            INSERT INTO appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (?, ?, ?, ?, 'scheduled')
        """, (patient['id'], doctor_id, date, time))
        conn.commit()
        conn.close()
        flash("Appointment booked", "success")
        return redirect(url_for('patient_dashboard'))

    conn.close()
    return render_template('patient/book_appointment.html', doctors=doctors)

# ------------------- RUN APP -------------------
if __name__ == '__main__':
    app.run(debug=True)
