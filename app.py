from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3, os, random, io, base64, qrcode
from datetime import datetime, date, time
from database import init_db

app = Flask(__name__)
app.secret_key = 'hospital_secret_key'

DB_PATH = 'hospital.db'

# ------------------- Initialize DB if not present -------------------
if not os.path.exists(DB_PATH):
    init_db()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
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
    current_time = now.strftime("%H:%M")

    conn = get_db_connection()
    conn.execute("""
        UPDATE appointments
        SET status = 'Expired'
        WHERE LOWER(status) = 'scheduled'
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

# ===================================================================
# ADMIN PORTAL
# ===================================================================
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
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'admin' not in session:
        return redirect(url_for('login_admin'))
    
    conn = get_db_connection()
    doctors = conn.execute("SELECT * FROM doctors ORDER BY id DESC").fetchall()
    
    # Financial & Operational Analytics
    total_revenue_row = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM payments").fetchone()
    total_revenue = total_revenue_row[0] if total_revenue_row else 0
    
    total_patients_row = conn.execute("SELECT COUNT(*) FROM patients").fetchone()
    total_patients = total_patients_row[0] if total_patients_row else 0
    
    total_appts_row = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()
    total_appointments = total_appts_row[0] if total_appts_row else 0
    
    scheduled_appts_row = conn.execute("SELECT COUNT(*) FROM appointments WHERE LOWER(status) = 'scheduled'").fetchone()
    scheduled_appts = scheduled_appts_row[0] if scheduled_appts_row else 0
    
    completed_appts_row = conn.execute("SELECT COUNT(*) FROM appointments WHERE LOWER(status) = 'completed'").fetchone()
    completed_appts = completed_appts_row[0] if completed_appts_row else 0
    
    conn.close()
    
    analytics = {
        'total_revenue': total_revenue,
        'total_patients': total_patients,
        'total_appointments': total_appointments,
        'scheduled_appts': scheduled_appts,
        'completed_appts': completed_appts,
        'total_doctors': len(doctors)
    }
    
    return render_template('admin/dashboard.html', doctors=doctors, analytics=analytics)

@app.route('/admin/delete_doctor/<int:doctor_id>', methods=['POST'])
def delete_doctor(doctor_id):
    if 'admin' not in session:
        return redirect(url_for('login_admin'))
    conn = get_db_connection()
    conn.execute("DELETE FROM doctors WHERE id = ?", (doctor_id,))
    conn.commit()
    conn.close()
    flash('Doctor removed from hospital directory.', 'success')
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

        # Check if email is already registered
        existing_doc = conn.execute("SELECT 1 FROM doctors WHERE email = ?", (email,)).fetchone()
        if existing_doc:
            conn.close()
            flash("A doctor with this email is already registered.", "danger")
            return redirect(url_for('add_doctor'))

        # Generate a unique 3-digit id
        while True:
            random_id = random.randint(100, 999)
            existing = conn.execute("SELECT 1 FROM doctors WHERE id = ?", (random_id,)).fetchone()
            if not existing:
                break

        try:
            conn.execute("""
                INSERT INTO doctors (id, name, specialization, experience, mobile, email, password)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (random_id, name, specialization, experience, mobile, email, password))
            conn.commit()
            conn.close()
            flash("Doctor onboarded successfully!", "success")
            return redirect(url_for('admin_dashboard'))
        except sqlite3.IntegrityError:
            conn.close()
            flash("A doctor with this email already exists.", "danger")
            return redirect(url_for('add_doctor'))

    return render_template('admin/add_doctor.html')

@app.route('/admin/patients')
def view_patients():
    if 'admin' not in session:
        return redirect(url_for('login_admin'))
    conn = get_db_connection()
    patients = conn.execute("SELECT * FROM patients ORDER BY id DESC").fetchall()
    conn.close()
    return render_template('admin/view_patients.html', patients=patients)

@app.route('/admin/payments')
def view_payments():
    if 'admin' not in session:
        return redirect(url_for('login_admin'))
    conn = get_db_connection()
    payments = conn.execute("""
        SELECT payments.id, patients.name, payments.amount, payments.created_at, payments.method, payments.transaction_id
        FROM payments
        JOIN patients ON payments.patient_id = patients.id
        ORDER BY payments.created_at DESC, payments.id DESC
    """).fetchall()
    conn.close()
    return render_template('admin/view_payments.html', payments=payments)

@app.route("/admin/view_appointments")
def view_appointments():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.id, a.appointment_date, a.appointment_time, a.status,
               d.name AS doctor_name, p.name AS patient_name
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        JOIN patients p ON a.patient_id = p.id
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
    """)
    rows = cursor.fetchall()
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


# ===================================================================
# DOCTOR PORTAL
# ===================================================================
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
            session['doctor_name'] = doctor['name']
            session['doctor'] = doctor['email']
            return redirect(url_for('doctor_dashboard'))
        else:
            flash('Invalid doctor email or password.', 'danger')
    return render_template('login_doctor.html')

@app.route('/doctor/dashboard')
def doctor_dashboard():
    if 'doctor_id' not in session:
        return redirect(url_for('login_doctor'))

    conn = get_db_connection()
    doctor = conn.execute(
        "SELECT * FROM doctors WHERE id=?",
        (session['doctor_id'],)
    ).fetchone()

    # Get Today's consultation queue count
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_appts = conn.execute("""
        SELECT COUNT(*) FROM appointments 
        WHERE doctor_id = ? AND appointment_date = ? AND LOWER(status) = 'scheduled'
    """, (session['doctor_id'], today_str)).fetchone()[0]

    recent_appts = conn.execute("""
        SELECT a.id, a.patient_id, p.name AS patient_name, p.age, p.gender, p.blood_group,
               a.appointment_date, a.appointment_time, a.status
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        WHERE a.doctor_id = ?
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
        LIMIT 5
    """, (session['doctor_id'],)).fetchall()

    conn.close()
    return render_template('doctor/dashboard.html', doctor=doctor, today_count=today_appts, recent_appts=recent_appts)

@app.route('/doctor/appointments')
def doctor_appointments():
    if 'doctor_id' not in session:
        return redirect(url_for('login_doctor'))

    conn = get_db_connection()
    appointments = conn.execute("""
        SELECT a.id,
               a.patient_id,
               p.name AS patient_name,
               p.age,
               p.gender,
               p.mobile,
               p.blood_group,
               a.appointment_date,
               a.appointment_time,
               a.status
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        WHERE a.doctor_id = ?
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
    """, (session['doctor_id'],)).fetchall()
    conn.close()

    return render_template('doctor/appointments.html', appointments=appointments)

@app.route('/doctor/appointment/complete/<int:appt_id>', methods=['POST', 'GET'])
def complete_appointment(appt_id):
    if 'doctor_id' not in session:
        return redirect(url_for('login_doctor'))

    conn = get_db_connection()
    conn.execute("""
        UPDATE appointments 
        SET status = 'Completed' 
        WHERE id = ? AND doctor_id = ?
    """, (appt_id, session['doctor_id']))
    conn.commit()
    conn.close()

    flash(f"Appointment #APT-{appt_id} marked as Completed.", "success")
    return redirect(url_for('doctor_appointments'))

@app.route('/doctor/patient_history/<int:patient_id>')
def doctor_patient_history(patient_id):
    if 'doctor_id' not in session:
        return redirect(url_for('login_doctor'))

    conn = get_db_connection()
    patient = conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
    if not patient:
        conn.close()
        flash("Patient record not found.", "danger")
        return redirect(url_for('doctor_appointments'))

    # All prescriptions for this patient
    prescriptions = conn.execute("""
        SELECT pr.*, d.name AS doctor_name, d.specialization
        FROM prescriptions pr
        JOIN doctors d ON pr.doctor_id = d.id
        WHERE pr.patient_id = ?
        ORDER BY pr.id DESC
    """, (patient_id,)).fetchall()

    # All appointment history
    appointments = conn.execute("""
        SELECT a.*, d.name AS doctor_name, d.specialization
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.patient_id = ?
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
    """, (patient_id,)).fetchall()

    conn.close()
    return render_template('doctor/patient_history.html', patient=patient, prescriptions=prescriptions, appointments=appointments)

@app.route('/doctor/prescriptions', methods=['GET', 'POST'])
def doctor_prescriptions():
    if 'doctor_id' not in session:
        return redirect(url_for('login_doctor'))
    
    conn = get_db_connection()
    patients = conn.execute("""
        SELECT DISTINCT p.id, p.name, p.gender, p.age
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        WHERE a.doctor_id = ?
        ORDER BY p.name ASC
    """, (session['doctor_id'],)).fetchall()

    selected_patient_id = request.args.get('patient_id', type=int)
    appointment_id = request.args.get('appointment_id', type=int)

    if request.method == 'POST':
        patient_id = request.form['patient_id']
        diagnosis = request.form['diagnosis'].strip()
        medicine = request.form['medicine'].strip()

        conn.execute("""
            INSERT INTO prescriptions (doctor_id, patient_id, diagnosis, medicine)
            VALUES (?, ?, ?, ?)
        """, (session['doctor_id'], patient_id, diagnosis, medicine))
        
        # If writing prescription for an active appointment, auto-mark completed
        conn.execute("""
            UPDATE appointments
            SET status = 'Completed'
            WHERE doctor_id = ? AND patient_id = ? AND LOWER(status) = 'scheduled'
        """, (session['doctor_id'], patient_id))

        conn.commit()
        conn.close()
        flash("Prescription issued successfully and dispatched to Patient Portal!", "success")
        return redirect(url_for('doctor_dashboard'))

    conn.close()
    return render_template('doctor/prescriptions.html', patients=patients, selected_patient_id=selected_patient_id, appointment_id=appointment_id)

@app.route('/doctor/update_profile', methods=['GET', 'POST'])
def update_doctor_profile():
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
    session.pop('doctor_name', None)
    session.pop('doctor', None)
    flash("Doctor logged out successfully.", "success")
    return redirect(url_for('index'))


# ===================================================================
# PATIENT PORTAL
# ===================================================================
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
            session['patient_name'] = patient['name']
            return redirect(url_for('patient_dashboard'))
        else:
            flash("Invalid email or password", "danger")
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

        # Check if email is already registered
        existing_email = conn.execute("SELECT 1 FROM patients WHERE email = ?", (email,)).fetchone()
        if existing_email:
            conn.close()
            flash("An account with this email is already registered. Please sign in or use a different email.", "danger")
            return redirect(url_for('register_patient'))

        # Generate a unique 3-digit ID for patients too
        while True:
            random_id = random.randint(100, 999)
            existing = conn.execute("SELECT 1 FROM patients WHERE id = ?", (random_id,)).fetchone()
            if not existing:
                break

        try:
            conn.execute("""
                INSERT INTO patients (id, name, age, dob, gender, mobile, blood_group, address, email, password)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (random_id, name, age, dob, gender, mobile, blood_group, address, email, password))
            conn.commit()
            conn.close()
            flash("Registered successfully! Please sign in with your credentials.", "success")
            return redirect(url_for('login_patient'))
        except sqlite3.IntegrityError:
            conn.close()
            flash("An account with this email or mobile already exists. Please try another.", "danger")
            return redirect(url_for('register_patient'))

    return render_template('register_patient.html')

@app.route('/patient/dashboard')
def patient_dashboard():
    if 'patient' not in session:
        return redirect(url_for('login_patient'))
    
    conn = get_db_connection()
    patient = conn.execute("SELECT * FROM patients WHERE email=?", (session['patient'],)).fetchone()
    if not patient:
        conn.close()
        session.pop('patient', None)
        return redirect(url_for('login_patient'))

    # Fetch patient's upcoming / recent appointments
    appointments = conn.execute("""
        SELECT a.*, d.name AS doctor_name, d.specialization
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.patient_id = ?
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
        LIMIT 5
    """, (patient['id'],)).fetchall()

    # Fetch patient's recent prescriptions
    prescriptions = conn.execute("""
        SELECT pr.*, d.name AS doctor_name, d.specialization
        FROM prescriptions pr
        JOIN doctors d ON pr.doctor_id = d.id
        WHERE pr.patient_id = ?
        ORDER BY pr.id DESC
        LIMIT 5
    """, (patient['id'],)).fetchall()

    # Fetch recent payments
    payments = conn.execute("""
        SELECT * FROM payments
        WHERE patient_id = ?
        ORDER BY id DESC
        LIMIT 5
    """, (patient['id'],)).fetchall()

    conn.close()
    return render_template('patient/dashboard.html', 
                           patient=patient, 
                           appointments=appointments, 
                           prescriptions=prescriptions,
                           payments=payments)

@app.route('/patient/prescriptions')
def patient_prescriptions():
    if 'patient' not in session:
        return redirect(url_for('login_patient'))

    conn = get_db_connection()
    patient = conn.execute("SELECT * FROM patients WHERE email=?", (session['patient'],)).fetchone()
    
    prescriptions = conn.execute("""
        SELECT pr.*, d.name AS doctor_name, d.specialization, d.mobile AS doctor_phone
        FROM prescriptions pr
        JOIN doctors d ON pr.doctor_id = d.id
        WHERE pr.patient_id = ?
        ORDER BY pr.id DESC
    """, (patient['id'],)).fetchall()
    
    conn.close()
    return render_template('patient/prescriptions.html', patient=patient, prescriptions=prescriptions)

@app.route('/patient/appointments')
def patient_appointments():
    if 'patient' not in session:
        return redirect(url_for('login_patient'))

    conn = get_db_connection()
    patient = conn.execute("SELECT * FROM patients WHERE email=?", (session['patient'],)).fetchone()
    
    appointments = conn.execute("""
        SELECT a.*, d.name AS doctor_name, d.specialization, d.mobile AS doctor_phone
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.patient_id = ?
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
    """, (patient['id'],)).fetchall()
    
    conn.close()
    return render_template('patient/appointments.html', patient=patient, appointments=appointments)

@app.route('/patient/payments')
def patient_payments():
    if 'patient' not in session:
        return redirect(url_for('login_patient'))

    conn = get_db_connection()
    patient = conn.execute("SELECT * FROM patients WHERE email=?", (session['patient'],)).fetchone()
    
    payments = conn.execute("""
        SELECT p.*, a.appointment_date, a.appointment_time, d.name AS doctor_name
        FROM payments p
        LEFT JOIN appointments a ON p.appointment_id = a.id
        LEFT JOIN doctors d ON a.doctor_id = d.id
        WHERE p.patient_id = ?
        ORDER BY p.id DESC
    """, (patient['id'],)).fetchall()
    
    conn.close()
    return render_template('patient/payments.html', patient=patient, payments=payments)

# ------------------- PRINTABLE e-PRESCRIPTION & INVOICES -------------------
@app.route('/prescription/print/<int:prescription_id>')
def print_prescription(prescription_id):
    conn = get_db_connection()
    rx = conn.execute("""
        SELECT pr.*, d.name AS doctor_name, d.specialization, d.mobile AS doctor_mobile, d.email AS doctor_email,
               p.name AS patient_name, p.age, p.gender, p.blood_group, p.mobile AS patient_mobile, p.address
        FROM prescriptions pr
        JOIN doctors d ON pr.doctor_id = d.id
        JOIN patients p ON pr.patient_id = p.id
        WHERE pr.id = ?
    """, (prescription_id,)).fetchone()
    conn.close()

    if not rx:
        flash("Prescription record not found.", "danger")
        return redirect(url_for('index'))

    return render_template('printable/prescription.html', rx=rx)

@app.route('/payment/invoice/<int:payment_id>')
def print_invoice(payment_id):
    conn = get_db_connection()
    invoice = conn.execute("""
        SELECT p.*, pt.name AS patient_name, pt.email AS patient_email, pt.mobile AS patient_mobile, pt.address,
               a.appointment_date, a.appointment_time, d.name AS doctor_name, d.specialization
        FROM payments p
        JOIN patients pt ON p.patient_id = pt.id
        LEFT JOIN appointments a ON p.appointment_id = a.id
        LEFT JOIN doctors d ON a.doctor_id = d.id
        WHERE p.id = ?
    """, (payment_id,)).fetchone()
    conn.close()

    if not invoice:
        flash("Invoice not found.", "danger")
        return redirect(url_for('index'))

    return render_template('printable/invoice.html', invoice=invoice)

@app.route('/patient/forgot', methods=['GET', 'POST'])
def patient_forgot_password():
    msg = None
    if request.method == 'POST':
        name = request.form['name']
        dob = request.form['dob']
        new_password = request.form['password']

        conn = get_db_connection()
        patient = conn.execute(
            "SELECT * FROM patients WHERE name=? AND dob=?",
            (name, dob)
        ).fetchone()

        if patient:
            conn.execute(
                "UPDATE patients SET password=? WHERE id=?",
                (new_password, patient['id'])
            )
            conn.commit()
            msg = "Password updated successfully."
        else:
            msg = "Name or Date of Birth did not match our records."
        conn.close()

    return render_template('patient_forgot.html', msg=msg)

@app.route('/logout/patient')
def logout_patient():
    session.pop('patient', None)
    session.pop('patient_name', None)
    session.pop('user_id', None)
    session.pop('role', None)
    flash("Patient logged out successfully.", "success")
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

        session['patient'] = email
        flash("Profile updated successfully.", "success")
        return redirect(url_for('patient_dashboard'))

    conn.close()
    return render_template('patient/update_profile.html', patient=patient)

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

        upi_id = request.form.get('upi_id') if method == "UPI" else None
        transaction_id = request.form.get('transaction_id') if method in ["UPI", "Card"] else None

        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO payments (appointment_id, patient_id, amount, method, upi_id, transaction_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (appointment_id, patient['id'], amount, method, upi_id, transaction_id, 'Success'))
        payment_id = cursor.lastrowid
        conn.commit()
        conn.close()

        flash("Payment completed successfully! You can view and print your receipt below.", "success")
        return redirect(url_for('print_invoice', payment_id=payment_id))

    # Fetch patient appointments for dropdown
    appointments = conn.execute("""
        SELECT a.*, d.name AS doctor_name, d.specialization
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.patient_id = ?
        ORDER BY a.appointment_date DESC, a.appointment_time DESC
    """, (patient['id'],)).fetchall()
    conn.close()

    return render_template("patient/payment.html", appointments=appointments)

# ------------------- PATIENT BOOK APPOINTMENT (SMART VALIDATIONS) -------------------
@app.route('/patient/book_appointment', methods=['GET', 'POST'])
def book_appointment():
    if 'patient' not in session:
        return redirect(url_for('login_patient'))
    
    conn = get_db_connection()
    doctors = conn.execute("SELECT * FROM doctors ORDER BY name ASC").fetchall()
    patient = conn.execute("SELECT * FROM patients WHERE email=?", (session['patient'],)).fetchone()

    today_str = datetime.now().strftime("%Y-%m-%d")

    if request.method == 'POST':
        doctor_id = request.form['doctor_id']
        appt_date = request.form['date']   # Expect 'YYYY-MM-DD'
        appt_time = request.form['time']   # Expect 'HH:MM'

        # 1. Past Date / Time Validation
        now = datetime.now()
        try:
            booking_dt = datetime.strptime(f"{appt_date} {appt_time}", "%Y-%m-%d %H:%M")
            if booking_dt < now:
                conn.close()
                flash("Cannot schedule an appointment in the past. Please select a future date and time.", "danger")
                return render_template('patient/book_appointment.html', doctors=doctors, today=today_str)
        except ValueError:
            pass

        # 2. Double-Booking Conflict Prevention
        conflict = conn.execute("""
            SELECT 1 FROM appointments
            WHERE doctor_id = ? AND appointment_date = ? AND appointment_time = ?
              AND LOWER(status) NOT IN ('cancelled', 'expired')
        """, (doctor_id, appt_date, appt_time)).fetchone()

        if conflict:
            doc_name = conn.execute("SELECT name FROM doctors WHERE id=?", (doctor_id,)).fetchone()
            doc_display = doc_name['name'] if doc_name else 'the specialist'
            conn.close()
            flash(f"Conflict: Dr. {doc_display} already has an appointment scheduled at {appt_time} on {appt_date}. Please choose another time slot.", "danger")
            return render_template('patient/book_appointment.html', doctors=doctors, today=today_str)

        # 3. Schedule Appointment
        conn.execute("""
            INSERT INTO appointments (patient_id, doctor_id, appointment_date, appointment_time, status)
            VALUES (?, ?, ?, ?, 'scheduled')
        """, (patient['id'], doctor_id, appt_date, appt_time))
        conn.commit()
        conn.close()
        
        flash("Appointment booked successfully! Our medical desk looks forward to seeing you.", "success")
        return redirect(url_for('patient_dashboard'))

    conn.close()
    return render_template('patient/book_appointment.html', doctors=doctors, today=today_str)

# ------------------- RUN APP -------------------
if __name__ == '__main__':
    app.run(debug=True)
