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
    id= data['id']
    name = data['name']
    email = data['email']

    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO student (id, name, email) VALUES (%s, %s, %s)", (id, name, email))
    mysql.connection.commit()
    cur.close()

    return jsonify({'message': 'Student added successfully!'}), 201

# add session
@app.route('/add_session', methods=['POST'])
def add_session():
    data = request.json
    id = data.get('id')
    session_name = data.get('session_name')
    session_code = data.get('session_code')
    location_lat = data.get('location_lat')
    location_long = data.get('location_long')
    expiry_time = data.get('expiry_time')  
    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO session (id, session_name, session_code, location_lat, location_long, expiry_time) VALUES (%s, %s, %s, %s, %s, %s)",
                (id, session_name, session_code, location_lat, location_long, expiry_time))
    mysql.connection.commit()

    return jsonify({"message": "Session added successfully!"})


# Generate QR Code
@app.route('/generate_qr', methods=['POST'])
def generate_qr():
    data = request.get_json()
    expiry_minutes = data.get('expiry_minutes', 5)
    session_code = f"session_{int(datetime.now().timestamp())}"
    expiry_time = datetime.now() + timedelta(minutes=expiry_minutes)

    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO session (session_code, expiry_time) VALUES (%s, %s)", (session_code, expiry_time))
    mysql.connection.commit()
    session_id = cur.lastrowid
    cur.close()

    qr_data = {'session_id': session_id, 'expiry_time': str(expiry_time)}
    qr_img = qrcode.make(str(qr_data))
    buffered = io.BytesIO()
    qr_img.save(buffered, format="PNG")
    qr_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

    return jsonify({'qr_code': qr_base64, 'session_id': session_id})

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

    result = [{'id': row[0], 'name': row[1], 'email': row[2]} for row in students]
    return jsonify(result)

# update student
@app.route('/update_student', methods=['PUT'])
def update_student():
    data = request.get_json()
    id = data['id']
    name = data['name']
    email = data['email']
    cur = mysql.connection.cursor()
    cur.execute("UPDATE student SET name=%s, email=%s WHERE id=%s", (name, email, id))
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
    password = data['password']
    role = data['role']
    if role not in ['ADMIN', 'TEACHER']:
        return jsonify({'message': 'Invalid role!'}), 400
    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO user (name, email, password, role) VALUES (%s, %s, %s, %s)", (name, email, password, role))
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
    
    cur = mysql.connection.cursor()
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
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM user WHERE role='TEACHER'")
    teachers = cur.fetchall()
    cur.close()

    result = [{'id': row[0], 'name': row[1], 'email': row[2], 'role': row[3]} for row in teachers]
    return jsonify(result)

# add teacher
@app.route('/add_teacher', methods=['POST'])
def add_teacher():
    data = request.get_json()
    name = data['name']
    email = data['email']
    password = data['password']
    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO user (name, email, password, role) VALUES (%s, %s, %s, %s)", (name, email, password, 'TEACHER'))
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
    cur = mysql.connection.cursor()
    cur.execute("UPDATE user SET name=%s, email=%s WHERE id=%s", (name, email, id))
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
