from flask import Flask, request, jsonify
from flask_mysqldb import MySQL
from flask_cors import CORS
from geopy.distance import geodesic
from werkzeug.utils import secure_filename
import pandas as pd
import os
import qrcode
import io
import base64
from datetime import datetime, timedelta
import uuid # For generating unique session codes
import MySQLdb # For specific error handling

app = Flask(__name__)
CORS(app)

# MySQL config
app.config['MYSQL_HOST'] = '127.0.0.1'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = '6296930416'
app.config['MYSQL_DB'] = 'attendance_app'

mysql = MySQL(app)

# Example: Allowed location (your campus)
ALLOWED_LOCATION = (20.2961, 85.8245)  # lat, lng
ALLOWED_RADIUS = 0.1  # in km

# ================================
#  API Routes
# ================================

# Add Student
@app.route('/add_student', methods=['POST'])
def add_student():
    data = request.get_json()

    if not data:
        return jsonify({"message": "Request payload is missing or not valid JSON."}), 400

    student_id = data.get('id')
    name = data.get('name')
    class_name = data.get('class')  # Assuming JSON key is 'class'
    email = data.get('email')
    phone = data.get('phone')
    requesting_user_id = data.get('request_id')

    required_fields = {
        "id": student_id,
        "name": name,
        "class": class_name,
        "email": email,
        "phone": phone,
        "request_id": requesting_user_id
    }

    missing_fields = [key for key, value in required_fields.items() if value is None]
    if missing_fields:
        return jsonify({"message": f"Missing required fields: {', '.join(missing_fields)}"}), 400

    cur = None
    try:
        cur = mysql.connection.cursor()

        # Authorization check
        cur.execute("SELECT role FROM user WHERE id = %s", (requesting_user_id,))
        user_role_result = cur.fetchone()

        if not user_role_result or user_role_result[0] not in ('ADMIN', 'TEACHER'):
            return jsonify({'message': 'User not authorized to add students.'}), 403

        # Check if student ID already exists
        cur.execute("SELECT id FROM student WHERE id = %s", (student_id,))
        if cur.fetchone():
            return jsonify({'message': f"Student with ID {student_id} already exists."}), 409

        cur.execute("INSERT INTO student (id, name, class, email, phone) VALUES (%s, %s, %s, %s, %s)",
                    (student_id, name, class_name, email, phone))
        mysql.connection.commit()
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in add_student: {e}")
        mysql.connection.rollback()
        return jsonify({'message': 'Failed to add student due to a database error.'}), 500
    finally:
        if cur:
            cur.close()

    return jsonify({'message': 'Student added successfully!'}), 201

# add session
@app.route('/add_session', methods=['POST'])
def add_session():
    data = request.get_json()

    if not data:
        return jsonify({"message": "Request payload is missing or not valid JSON."}), 400

    session_name = data.get('session_name')
    expiry_time_str = data.get('expiry_time')
    created_by = data.get('created_by')

    required_fields = {
        "session_name": session_name,
        "expiry_time": expiry_time_str,
        "created_by": created_by
    }

    missing_fields = [key for key, value in required_fields.items() if value is None]
    if missing_fields:
        return jsonify({"message": f"Missing required fields: {', '.join(missing_fields)}"}), 400

    # Validate expiry_time format
    try:
        datetime.strptime(expiry_time_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return jsonify({"message": "Invalid expiry_time format. Expected YYYY-MM-DD HH:MM:SS"}), 400

    cur = None
    try:
        cur = mysql.connection.cursor()

        # Authorization: Check if the creator is an ADMIN or TEACHER
        cur.execute("SELECT role FROM user WHERE id = %s", (created_by,))
        user_role_result = cur.fetchone()
        if not user_role_result or user_role_result[0] not in ('ADMIN', 'TEACHER'):
            return jsonify({'message': 'User not authorized to create sessions.'}), 403

        # Auto-generate a unique session code based on timestamp
        session_code = f"SESSION_{int(datetime.now().timestamp())}"

        # Insert into database (id will auto-increment)
        cur.execute("""
            INSERT INTO session (session_name, session_code, expiry_time, created_by)
            VALUES (%s, %s, %s, %s)
        """, (session_name, session_code, expiry_time_str, created_by))

        mysql.connection.commit()
        session_id_server = cur.lastrowid  # Get the auto-generated id

    except MySQLdb.Error as e:
        app.logger.error(f"Database error in add_session: {e}")
        mysql.connection.rollback()
        return jsonify({'message': 'Failed to add session due to a database error.'}), 500
    finally:
        if cur:
            cur.close()

    return jsonify({
        "message": "Session added successfully!",
        "session_id": session_id_server,
        "session_code": session_code
    }), 201


# Generate QR Code
@app.route('/generate_qr', methods=['POST'])
def generate_qr():
    data = request.get_json()
    expiry_minutes = data.get('expiry_minutes', 5)
    location_lat = data.get('location_lat', ALLOWED_LOCATION[0])
    location_long = data.get('location_long', ALLOWED_LOCATION[1])
    session_code = f"session_{int(datetime.now().timestamp())}"
    expiry_time = datetime.now() + timedelta(minutes=expiry_minutes)

    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO session (session_code, expiry_time, location_lat, location_long) VALUES (%s, %s, %s, %s)",
                (session_code, expiry_time, location_lat, location_long))
    mysql.connection.commit()
    session_id = cur.lastrowid
    cur.close()

    qr_data = {'session_id': session_id, 'expiry_time': str(expiry_time)}
    qr_img = qrcode.make(str(qr_data))
    buffered = io.BytesIO()
    qr_img.save(buffered, format="PNG")
    qr_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

    return jsonify({'qr_code': qr_base64, 'session_id': session_id, 'session_code': session_code})

# mark attendance
@app.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    data = request.get_json()
    student_id = data['student_id']
    session_id = data['session_id']
    lat = data['latitude']
    lng = data['longitude']

    cur = mysql.connection.cursor()
    cur.execute("SELECT expiry_time, location_lat, location_long FROM session WHERE id = %s", (session_id,))
    result = cur.fetchone()
    if not result:
        return jsonify({'message': 'Invalid session'}), 400

    expiry_time, session_lat, session_lng = result
    if datetime.now() > expiry_time:
        status = 'absent'
    else:
        user_location = (lat, lng)
        session_location = (float(session_lat), float(session_lng))
        distance_km = geodesic(session_location, user_location).km
        status = 'present' if distance_km <= ALLOWED_RADIUS else 'absent'

    cur.execute("""
        INSERT INTO attendance (student_id, session_id, status, timestamp)
        VALUES (%s, %s, %s, %s)
    """, (student_id, session_id, status, datetime.now()))
    mysql.connection.commit()
    cur.close()

    return jsonify({'status': status})


# Attendance Report
@app.route('/attendance_report', methods=['GET'])
def attendance_report():
    student_id = request.args.get('student_id')

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT session_id, status, timestamp
        FROM attendance
        WHERE student_id = %s
        ORDER BY timestamp DESC
    """, (student_id,))
    records = cur.fetchall()
    cur.close()

    result = [{'session_id': row[0], 'status': row[1], 'timestamp': str(row[2])} for row in records]
    return jsonify(result)

# get all the students 
@app.route('/get_students', methods=['GET'])
def get_students():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM student")
    students = cur.fetchall()
    cur.close()

    result = [{'id': row[0], 'name': row[1], 'class': row[2],'email': row[3], 'phone': row[4]} for row in students]
    return jsonify(result)

# update student
@app.route('/update_student', methods=['PUT'])
def update_student():
    data = request.get_json()
    id = data['id']
    name = data['name']
    class_name = data['class_name']
    email = data['email']
    phone = data['phone']
    cur = mysql.connection.cursor()
    cur.execute("UPDATE student SET name=%s, email=%s, class=%s, phone=%s WHERE id=%s", (name, email, class_name, phone, id))
    mysql.connection.commit()
    cur.close()
    return jsonify({'message': 'Student updated successfully!'})

# delete student
@app.route('/delete_student', methods=['DELETE'])
def delete_student():
    data = request.get_json()
    id = data['id']
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM student WHERE id=%s", (id,))
    mysql.connection.commit()
    cur.close()
    return jsonify({'message': 'Student deleted successfully!'})

# delete session
@app.route('/delete_session', methods=['DELETE'])
def delete_session():
    data = request.get_json()
    id = data['id']
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM session WHERE id=%s", (id,))
    mysql.connection.commit()
    cur.close()
    return jsonify({'message': 'Session deleted successfully!'})

# get all the sessions
@app.route('/get_sessions', methods=['GET'])
def get_sessions():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM session")
    sessions = cur.fetchall()
    cur.close()

    result = [{'id': row[0], 'session_name': row[5], 'session_code': row[1], 'location_lat': row[2], 'location_long': row[3], 'expiry_time': str(row[4])} for row in sessions]
    return jsonify(result)


# get specific session's attendance
@app.route('/get_session_attendance', methods=['GET'])
def get_session_attendance():
    data = request.get_json()
    session_id = data['session_id']
    if not session_id:
        return jsonify({'message': 'session_id is required'}), 400
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT student_id, status, timestamp
        FROM attendance
        WHERE session_id = %s
    """, (session_id,))
    records = cur.fetchall()
    cur.close()

    if not records:
        return jsonify({'message': 'No attendance records found for this session'}), 404

    result = [{'student_id': row[1], 'status': row[3], 'timestamp': str(row[4])} for row in records]
    return jsonify(result)

# bulk student import from excel sheet 

# allow file upload 
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
allowed_extensions = {'xlsx', 'xls'}
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions
# import students from excel sheet
@app.route('/import_students', methods=['POST'])
def import_students():
    if 'file' not in request.files:
        return jsonify({'message': 'No file part'}), 400
    file=request.files['file']
    if file.filename == '':
        return jsonify({'message': 'No selected file'}), 400
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath=os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        df=pd.read_excel(filepath)
        cur = mysql.connection.cursor()
        for index, row in df.iterrows():
            id = row['id']
            name = row['name']
            email = row['email']
            cur.execute("INSERT INTO student (id, name, email) VALUES (%s, %s, %s)", (id, name, email))
        mysql.connection.commit()
        cur.close()
        return jsonify({'message': 'Students imported successfully!'}), 201
    return jsonify({'message': 'Invalid file format'}), 400

# register admin
@app.route('/register_admin', methods=['POST'])
def register_admin():
    data = request.get_json()
    name = data['name']
    email = data['email']
    phone = data['phone']
    password = data['password']
    role = data['role']
    if role not in ['ADMIN', 'TEACHER']:
        return jsonify({'message': 'Invalid role!'}), 400
    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO user (name, email, phone, password, role) VALUES (%s, %s, %s, %s, %s)", (name, email, phone, password, role))
    mysql.connection.commit()
    cur.close()
    return jsonify({'message': 'Admin registered successfully!'}), 201

# login admin
@app.route('/login_admin', methods=['POST'])
def login_admin():
    data = request.get_json()
    email = data['email']
    password = data['password']
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM user WHERE email=%s AND password=%s", (email, password))
    admin = cur.fetchone()
    cur.close()
    if admin:
        return jsonify({'message': 'Login successful!'}), 200
    else:
        return jsonify({'message': 'Invalid credentials!'}), 401

# delete teacher
@app.route('/delete_teacher', methods=['DELETE'])
def delete_teacher():
    data = request.get_json()
    id = data['id']
    request_id = data['request_id']

    cur = mysql.connection.cursor()
    cur.execute("SELECT role FROM user WHERE id=%s ", (request_id,))
    result = cur.fetchone()
    if not result or result[0] != 'ADMIN':
        return jsonify({'message': 'only admin can delete teacher!'}), 404

    cur.execute("SELECT role FROM user WHERE id=%s ", (id,))
    result = cur.fetchone()
    if not result or result[0] != 'TEACHER':
        return jsonify({'message': 'Teacher not found!'}), 404
    cur.execute("DELETE FROM user WHERE id=%s", (id,))
    mysql.connection.commit()
    cur.close()
    return jsonify({'message': 'Teacher deleted successfully!'}), 200

# get all the teachers
@app.route('/get_teachers', methods=['GET'])
def get_teachers():
    request_id = request.args.get('request_id')
    cur = mysql.connection.cursor()
    cur.execute("select role from user where id=%s", (request_id,))
    result = cur.fetchone()
    if not result or result[0] != 'ADMIN':
        return jsonify({'message': 'only admin can view teachers!'}), 403
    cur.execute("SELECT * FROM user WHERE role='TEACHER'")
    teachers = cur.fetchall()
    cur.close()
    result = [{'id': row[0], 'name': row[1], 'email': row[2], 'phone' : row[3], 'role': row[5]} for row in teachers]
    return jsonify(result)

# add teacher
@app.route('/add_teacher', methods=['POST'])
def add_teacher():
    data = request.get_json()
    name = data['name']
    email = data['email']
    phone = data['phone']
    password = data['password']
    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO user (name, email, phone, password, role) VALUES (%s, %s, %s, %s,%s)", (name, email, phone, password, 'TEACHER'))
    mysql.connection.commit()
    cur.close()
    return jsonify({'message': 'Teacher added successfully!'}), 201

# update teacher
@app.route('/update_teacher', methods=['PUT'])
def update_teacher():
    data = request.get_json()
    id = data['id']
    name = data['name']
    email = data['email']
    phone = data['phone']
    cur = mysql.connection.cursor()
    cur.execute("UPDATE user SET name=%s, email=%s, phone=%s WHERE id=%s", (name, email, phone, id))
    mysql.connection.commit()
    cur.close()
    return jsonify({'message': 'Teacher updated successfully!'}), 200

# student login
@app.route('/student_login', methods=['POST'])
def student_login():
    data = request.get_json()
    id = data['id']
    email = data['email'] # email is used for password
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM student WHERE id=%s AND email=%s", (id, email))
    student = cur.fetchone()
    cur.close()
    if student:
        return jsonify({'message': 'Login successful!'}), 200
    else:
        return jsonify({'message': 'Invalid credentials!'}), 401
    


# ================================
# Run the App
# ================================
if __name__ == '__main__':
    app.run(debug=True)
