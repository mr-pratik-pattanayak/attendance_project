from flask import Flask, request, jsonify
from flask_mysqldb import MySQL
from flask_cors import CORS
from geopy.distance import geodesic
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


# ================================
# Run the App
# ================================
if __name__ == '__main__':
    app.run(debug=True)
