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
    class_name = data.get('class')

    required_fields = {
        "session_name": session_name,
        "expiry_time": expiry_time_str,
        "created_by": created_by,
        "class": class_name
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
            INSERT INTO session (session_name, session_code, expiry_time, created_by, class)
            VALUES (%s, %s, %s, %s, %s)
        """, (session_name, session_code, expiry_time_str, created_by, class_name))

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
    if not data:
        return jsonify({'message': 'Request payload is missing or not valid JSON.'}), 400
    session_id = data.get('session_id')
    requesting_user_id = data.get('requesting_user_id')
    # Validate presence and type
    if not all([session_id, requesting_user_id]):
        return jsonify({'message': 'Missing session_id or requesting_user_id in request.'}), 400
    try:
        session_id = int(session_id)
        requesting_user_id = int(requesting_user_id)
    except (ValueError, TypeError):
        return jsonify({'message': 'session_id and requesting_user_id must be integers.'}), 400
    try:
        cur = mysql.connection.cursor()
        # Fetch session details
        cur.execute("""
            SELECT session_code, expiry_time, created_by 
            FROM session 
            WHERE id = %s
        """, (session_id,))
        session = cur.fetchone()
        # Check if session exists
        if not session:
            return jsonify({'message': 'Session not found.'}), 404
        # Unpack session details
        session_code, expiry_time, created_by = session
        # Fetch requesting user's role
        cur.execute("SELECT role FROM user WHERE id = %s", (requesting_user_id,))
        user = cur.fetchone()
        if not user:
            return jsonify({'message': 'Requesting user not found.'}), 404
        user_role = user[0]
        # Check expiration
        if datetime.now() > expiry_time:
            app.logger.info(f"Generating QR for expired session ID: {session_id}")
        # Authorization check
        if requesting_user_id != created_by and user_role != 'ADMIN':
            return jsonify({'message': 'Not authorized to generate QR for this session.'}), 403
        # Prepare QR data
        formatted_expiry_time = expiry_time.strftime('%Y-%m-%d %H:%M:%S')
        qr_data = {
            'session_id': session_id,
            'session_code': session_code,
            'expiry_time': formatted_expiry_time
        }
        # Generate QR
        qr_img = qrcode.make(str(qr_data))
        buffered = io.BytesIO()
        qr_img.save(buffered, format="PNG")
        qr_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in generate_qr: {e}")
        return jsonify({'message': 'Failed to generate QR due to a database error.'}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error in generate_qr: {e}")
        return jsonify({'message': 'An unexpected error occurred while generating QR code.'}), 500
    finally:
        if cur:
            cur.close()
    return jsonify({
        'qr_code': qr_base64,
        'session_id': session_id,
        'session_code': session_code,
        'expiry_time': formatted_expiry_time
    }), 200

# mark attendance
@app.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    data = request.get_json()
    if not data:
        return jsonify({'message': 'Request payload is missing or not valid JSON.'}), 400
    student_id = data.get('student_id')
    session_id = data.get('session_id')
    lat = data.get('latitude')
    lng = data.get('longitude')
    # Validate required fields
    if not all([student_id, session_id, lat, lng]):
        return jsonify({'message': 'Missing required fields in request.'}), 400
    try:
        student_id = int(student_id)
        session_id = int(session_id)
        lat = float(lat)
        lng = float(lng)
    except (ValueError, TypeError):
        return jsonify({'message': 'Invalid data type for student_id, session_id, latitude, or longitude.'}), 400
    cur = None
    current_time = datetime.now()
    try:
        cur = mysql.connection.cursor()
        # Check session existence and expiry
        cur.execute("SELECT expiry_time FROM session WHERE id = %s", (session_id,))
        result = cur.fetchone()
        if not result:
            return jsonify({'message': 'Invalid session ID.'}), 400
        expiry_time = result[0]
        if current_time > expiry_time:
            status = 'ABSENT'
        else:
            # Compare student's location to default allowed location
            user_location = (lat, lng)
            distance_km = geodesic(ALLOWED_LOCATION, user_location).km
            status = 'PRESENT' if distance_km <= ALLOWED_RADIUS else 'ABSENT'
        # Check if attendance already marked for this student and session
        cur.execute("SELECT id FROM attendance WHERE student_id = %s AND session_id = %s", (student_id, session_id))
        if cur.fetchone():
            # Optionally, you could update the existing record or simply inform the user.
            # For now, let's prevent duplicate entries and inform.
            return jsonify({'message': 'Attendance already marked for this session.', 'status': 'already_marked'}), 409
        # Record attendance
        cur.execute("""
            INSERT INTO attendance (student_id, session_id, status, timestamp)
            VALUES (%s, %s, %s, %s)
        """, (student_id, session_id, status, current_time))
        mysql.connection.commit()
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in mark_attendance: {e}")
        mysql.connection.rollback()
        return jsonify({'message': 'Database error occurred while marking attendance.'}), 500
    finally:
        if cur:
            cur.close()
    return jsonify({'message': f'Attendance marked as {status}.', 'status': status}), 200

# finalize attendance
@app.route('/finalize_attendance', methods=['POST'])
def finalize_attendance():
    data = request.get_json()
    if not data:
        return jsonify({'message': 'Request payload is missing or not valid JSON.'}), 400
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'message': 'session_id is required.'}), 400
    try:
        session_id = int(session_id)
    except (ValueError, TypeError):
        return jsonify({'message': 'Invalid session_id.'}), 400
    cur = None
    current_time = datetime.now()
    try:
        cur = mysql.connection.cursor()
        # Check if session exists
        cur.execute("SELECT class FROM session WHERE id = %s", (session_id,))
        session_result = cur.fetchone()
        if not session_result:
            return jsonify({'message': 'Invalid session ID.'}), 400
        class_name = session_result[0]
        # Find students of this class who haven't marked attendance yet
        cur.execute("""
            SELECT s.id 
            FROM student s
            WHERE s.class = %s
            AND s.id NOT IN (
                SELECT student_id FROM attendance WHERE session_id = %s
            )
        """, (class_name, session_id))
        absent_students = cur.fetchall()
        if absent_students:
            # Insert 'absent' records for them
            cur.executemany("""
                INSERT INTO attendance (student_id, session_id, status, timestamp)
                VALUES (%s, %s, %s, %s)
            """, [(student_id[0], session_id, 'ABSENT', current_time) for student_id in absent_students])
            mysql.connection.commit()
            num_absent = len(absent_students)
        else:
            num_absent = 0
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in finalize_session: {e}")
        mysql.connection.rollback()
        return jsonify({'message': 'Database error occurred while finalizing session.'}), 500
    finally:
        if cur:
            cur.close()
    return jsonify({
        'message': f'Session finalized successfully. {num_absent} students marked as absent.',
        'absent_count': num_absent
    }), 200


# Attendance Report for perticular student
@app.route('/attendance_report', methods=['GET'])
def attendance_report():
    student_id = request.args.get('student_id')
    if not student_id:
        return jsonify({'message': 'student_id parameter is required.'}), 400
    try:
        student_id = int(student_id)
    except ValueError:
        return jsonify({'message': 'student_id must be an integer.'}), 400
    cur = None
    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT a.session_id, a.status, a.timestamp
            FROM attendance a
            JOIN session s ON a.session_id = s.id
            WHERE a.student_id = %s
            ORDER BY a.timestamp DESC
        """, (student_id,))
        records = cur.fetchall()
        present_count = 0
        absent_count = 0
        detailed_records = []
        if records:
            for row in records:
                status = row[1]  # a.status
                if status == 'PRESENT':
                    present_count += 1
                elif status == 'ABSENT':
                    absent_count += 1
                # Add other status counts here if needed
                detailed_records.append({
                    'session_id': row[0],
                    'status': status,
                    'timestamp': str(row[2])
                })
        response_data = {'present_count': present_count, 'absent_count': absent_count, 'records': detailed_records,}
        total_session = present_count + absent_count
        if total_session > 0:
            response_data['attendance_percentage'] = (present_count / total_session) * 100
        else:
            response_data['attendance_percentage'] = 0.0
        response_data['total_session'] = total_session
        return jsonify(response_data), 200
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in attendance_report: {e}")
        return jsonify({'message': 'Failed to retrieve attendance report due to a database error.'}), 500
    finally:
        if cur:
            cur.close()


# get all the students 
@app.route('/get_all_student', methods=['GET'])
def get_all_student():
    request_id = request.args.get('request_id')
    if not request_id:
        return jsonify({'message': 'request_id parameter is required.'}), 400
    try:
        request_id = int(request_id)
    except ValueError:
        return jsonify({'message': 'request_id must be an integer.'}), 400
    cur = None
    try:
        cur = mysql.connection.cursor()     
        # Check if the requesting user is an ADMIN or TEACHER
        cur.execute("SELECT role FROM user WHERE id = %s", (request_id,))
        user_role_result = cur.fetchone()
        if not user_role_result or user_role_result[0] not in ('ADMIN', 'TEACHER'):
            return jsonify({'message': 'User not authorized to view students.'}), 403 
        # Fetch all students
        cur.execute("SELECT id, name, class, email, phone FROM student")
        students = cur.fetchall()
        if not students:
            return jsonify({'message': 'No students found.'}), 404
        response_data = []
        for row in students:
            response_data.append({
                'id': row[0],
                'name': row[1],
                'class': row[2],
                'email': row[3],
                'phone': row[4]
            })
        return jsonify({'student_count': len(response_data), 'students': response_data}), 200
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in get_students: {e}")
        return jsonify({'message': 'Failed to retrieve students due to a database error.'}), 500
    finally:
        if cur:
            cur.close()

# get student by class 
@app.route('/get_student_by_class', methods=['GET'])
def get_student_by_class():
    class_name = request.args.get('class_name')
    request_id = request.args.get('request_id')
    if not class_name or not request_id:
        return jsonify({'message': 'class_name and request_id parameters are required.'}), 400
    try:
        request_id = int(request_id)
    except ValueError:
        return jsonify({'message': 'request_id must be an integer.'}), 400
    cur = None
    try:
        cur = mysql.connection.cursor()
        # Check if the requesting user is an ADMIN or TEACHER
        cur.execute("SELECT role FROM user WHERE id = %s", (request_id,))
        user_role_result = cur.fetchone()
        if not user_role_result or user_role_result[0] not in ('ADMIN', 'TEACHER'):
            return jsonify({'message': 'User not authorized to view students.'}), 403
        # Fetch students by class
        cur.execute("SELECT id, name, class, email, phone FROM student WHERE class = %s", (class_name,))
        students = cur.fetchall()
        if not students:
            return jsonify({'message': 'No students found for this class.'}), 404
        response_data = []
        for row in students:
            response_data.append({
                'id': row[0],
                'name': row[1],
                'class': row[2],
                'email': row[3],
                'phone': row[4]
            })
        return jsonify({'student_count': len(response_data), 'students': response_data}), 200
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in get_student_by_class: {e}")
        return jsonify({'message': 'Failed to retrieve students due to a database error.'}), 500
    finally:
        if cur:
            cur.close()


# update student
@app.route('/update_student', methods=['PUT'])
def update_student():
    data = request.get_json()
    request_id = data.get('request_id')
    student_id = data['student_id']
    if not request_id or not student_id:
        return jsonify({'message': 'request_id and student_id are required.'}), 400
    cur=None
    try:
        cur=mysql.connection.cursor()
        # Check if the requesting user is an ADMIN or TEACHER
        cur.execute("SELECT role FROM user WHERE id = %s", (request_id,))
        user_role_result = cur.fetchone()
        if not user_role_result or user_role_result[0] not in ('ADMIN', 'TEACHER'):
            return jsonify({'message': 'User not authorized to update students.'}), 403
        # Fetch the student details
        cur.execute("SELECT name, email, class, phone FROM student WHERE id = %s", (student_id,))
        student = cur.fetchone()
        if not student:
            return jsonify({'message': 'Student not found.'}), 404
        # Update the student details
        name = data.get('name', student[0])
        email = data.get('email', student[1])
        class_name = data.get('class', student[2])
        phone = data.get('phone', student[3])
        if not name or not email or not class_name or not phone:
            return jsonify({'message': 'name, email, class, and phone are required.'}), 400
        cur.execute("UPDATE student SET name=%s, email=%s, class=%s, phone=%s WHERE id=%s", (name, email, class_name, phone, student_id))
        mysql.connection.commit()
        return jsonify({'message': 'Student updated successfully!'}), 200
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in update_student: {e}")
        mysql.connection.rollback()
        return jsonify({'message': 'Failed to update student due to a database error.'}), 500
    finally:
        if cur:
            cur.close()

# delete attendance for delete student (by student id )
@app.route('/delete_attendance_by_student_id', methods=['DELETE'])
def delete_attendance_by_student_id():
    data = request.get_json()
    student_id = data.get('student_id')
    request_id = data.get('request_id')
    if not student_id or not request_id:
        return jsonify({'message': 'student_id and request_id are required.'}), 400
    cur = None
    try:
        cur = mysql.connection.cursor()
        # Check if the requesting user is an ADMIN or TEACHER
        cur.execute("SELECT role FROM user WHERE id = %s", (request_id,))
        user_role_result = cur.fetchone()
        if user_role_result is None or user_role_result[0] not in ('ADMIN', 'TEACHER'):
            return jsonify({'message': 'User not authorized to delete attendance records.'}), 403
        # Check if the student exists
        cur.execute("SELECT id FROM student WHERE id = %s", (student_id,))
        student = cur.fetchone()
        if not student:
            return jsonify({'message': 'Student not found.'}), 404
        # Check if attendance records exist
        cur.execute("SELECT id FROM attendance WHERE student_id = %s", (student_id,))
        attendance_records = cur.fetchall()
        if not attendance_records:
            return jsonify({'message': 'No attendance records found for this student.'}), 404
        # Delete attendance records
        cur.execute("DELETE FROM attendance WHERE student_id = %s", (student_id,))
        mysql.connection.commit()
        return jsonify({'message': 'Attendance records deleted successfully!'}), 200
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in delete_attendance_by_student_id: {e}")
        mysql.connection.rollback()
        return jsonify({'message': 'Failed to delete attendance records due to a database error.'}), 500
    finally:
        if cur:
            cur.close()

# delete student
@app.route('/delete_student', methods=['DELETE'])
def delete_student():
    data = request.get_json()
    student_id = data['student_id']
    request_id = data['request_id']
    if not student_id or not request_id:
        return jsonify({'message': 'student_id and request_id are required.'}), 400
    cur = None
    try:
        cur=mysql.connection.cursor()
        # Check if the requesting user is an ADMIN or TEACHER
        cur.execute("SELECT role FROM user WHERE id = %s", (request_id,))
        user_role_result = cur.fetchone()
        if not user_role_result or user_role_result[0] not in ('ADMIN', 'TEACHER'):
            return jsonify({'message': 'User not authorized to delete students.'}), 403
        # Check if the student exists
        cur.execute("SELECT id FROM student WHERE id = %s", (student_id,))
        student = cur.fetchone()
        if not student:
            return jsonify({'message': 'Student not found.'}), 404
        id = student[0]
        # Check if the student has any attendance records
        cur.execute("SELECT id FROM attendance WHERE student_id = %s", (id,))
        attendance = cur.fetchone()
        if attendance:
            return jsonify({'message': 'Cannot delete student with existing attendance records.'}), 400
        # Delete the student
        cur.execute("DELETE FROM student WHERE id = %s", (id,))
        mysql.connection.commit()
        return jsonify({'message': 'Student deleted successfully!'}), 200
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in delete_student: {e}")
        mysql.connection.rollback()
        return jsonify({'message': 'Failed to delete student due to a database error.'}), 500
    finally:
        if cur:
            cur.close()  

# delete attendance_by_session
@app.route('/delete_attendance_by_session', methods=['DELETE'])
def delete_attendance_by_session():
    data=request.get_json()
    request_id=data['request_id'] #user id 
    id=data['id'] #session id
    if not request_id or not id :
        return jsonify({'message': 'request_id and id are required!'}),400
    cur = None
    try:
        cur=mysql.connection.cursor()
        # Check if the requesting user is an ADMIN or TEACHER
        cur.execute("SELECT role FROM user WHERE id = %s", (request_id,))
        user_role_result = cur.fetchone()
        if not user_role_result or user_role_result[0] not in ('ADMIN', 'TEACHER'):
            return jsonify({'message': 'User not authorized to delete attendance records.'}), 403
        # check if the session is exist or not 
        cur.execute("SELECT id FROM session WHERE id = %s", (id,))
        session = cur.fetchone()
        if not session:
            return jsonify({'message' : 'session not found'}), 404
        # check attendance record 
        cur.execute("SELECT id FROM attendance WHERE session_id = %s", (id,))
        attendance = cur.fetchone()
        if not attendance:
            return jsonify({'message': 'No attendance records found for this session.'}), 404
        # Delete attendance records for the session
        cur.execute("DELETE FROM attendance WHERE session_id = %s", (id,))
        mysql.connection.commit()
        return jsonify({'message': 'Attendance records deleted successfully!'}), 200
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in delete_attendance_by_session: {e}")
        mysql.connection.rollback()
        return jsonify({'message': 'Failed to delete attendance records due to a database error.'}), 500
    finally:
        if cur:
            cur.close()

# delete session
@app.route('/delete_session', methods=['DELETE'])
def delete_session():
    data = request.get_json()
    request_id=data['request_id']
    id = data['id']
    if not request_id or not id :
        return jsonify({'message':'request_id and id are required'}), 400
    cur = None
    try:
        cur = mysql.connection.cursor()
        # check the requesting user is an ADMIN or a TEACHER
        cur.execute("SELECT role FROM user WHERE id = %s ",(request_id,))
        user_role_result=cur.fetchone()
        if not user_role_result or user_role_result[0] not in ('ADMIN', 'TEACHER'):
            return jsonify({'message': 'User not authorized to delete attendance records.'}), 403
        # check if session is exist or not 
        cur.execute("SELECT id FROM session WHERE id = %s",(id,))
        session = cur.fetchone()
        if not session:
            return jsonify({'message' : 'session not found'}), 404
        cur.execute("DELETE FROM session WHERE id = %s",(id,))
        mysql.connection.commit()
        return jsonify({'message': 'Session deleted successfully!'}), 200
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in delete_session: {e}")
        mysql.connection.rollback()
        return jsonify({'message': 'Failed to delete session due to a database error.'}), 500
    finally:
        if cur:
            cur.close()
    

# get all the sessions
@app.route('/get_sessions', methods=['GET'])
def get_sessions():
    id = request.args.get('id')
    cur = None
    try:
        cur = mysql.connection.cursor()
        # check if the requesting user is an ADMIN or TEACHER
        cur.execute("SELECT role FROM user WHERE id = %s", (id,))
        user_role_result = cur.fetchone()
        if not user_role_result or user_role_result[0] not in ('ADMIN', 'TEACHER'):
            return jsonify({'message': 'User not authorized to view sessions.'}), 403
        # Fetch all sessions
        cur.execute("SELECT * FROM session")
        sessions = cur.fetchall()
        if not sessions:
            return jsonify({'message': 'No sessions found.'}), 404
        # Format the session data
        result = []
        for row in sessions:
            # get creater name for more clarity
            create=row[4]
            cur.execute("SELECT name FROM user WHERE id = %s", (create,))
            created_by = cur.fetchone()
            creator_name = created_by[0] if created_by else "Unknown"
            result.append({
                'id': row[0],
                'session_name': row[1],
                'session_code': row[2],
                'expiry_time': str(row[3]),
                'created_by': row[4],
                'created_by_name': creator_name,
                'class': row[5],
            })
        return jsonify({'session_count': len(result), 'sessions': result}), 200
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in get_sessions: {e}")
        return jsonify({'message': 'Failed to retrieve sessions due to a database error.'}), 500
    finally:
        if cur:
            cur.close()
            
# get specific session's attendance
@app.route('/get_session_attendance', methods=['GET'])
def get_session_attendance():
    session_id = request.args.get('session_id')
    request_id = request.args.get('request_id')
    if not session_id or not request_id:
        return jsonify({'message': 'session_id and request_id are required.'}), 400
    cur = None
    try:
        session_id = int(session_id)
        request_id = int(request_id)
        cur = mysql.connection.cursor()
        # Check if the requesting user is an ADMIN or TEACHER
        cur.execute("SELECT role FROM user WHERE id = %s", (request_id,))
        user_role_result = cur.fetchone()
        if not user_role_result or user_role_result[0] not in ('ADMIN', 'TEACHER'):
            return jsonify({'message': 'User not authorized to view attendance.'}), 403
        # check if session exists
        cur.execute("SELECT id FROM session WHERE id = %s", (session_id,))
        session_result = cur.fetchone()
        if not session_result:
            return jsonify({'message': 'Session not found.'}), 404
        # fetch the session name 
        cur.execute("SELECT session_name FROM session WHERE id = %s", (session_id,))
        session_name_result = cur.fetchone()
        session_name = session_name_result[0] if session_name_result else "Unknown"
        # check if attendance records exist for the session
        cur.execute("SELECT id FROM attendance WHERE session_id = %s", (session_id,))
        attendance_result = cur.fetchone()
        if not attendance_result:
            return jsonify({'message': 'No attendance records found for this session.'}), 404
        # Fetch attendance records
        cur.execute("SELECT * FROM attendance WHERE session_id = %s", (session_id,))
        records = cur.fetchall()
        if not records:
            return jsonify({'message': 'No attendance records found for this session'}), 404
        result = []
        for row in records:
            cur.execute("SELECT name FROM student WHERE id = %s", (row[1],))
            student_name = cur.fetchone()
            student_name = student_name[0] if student_name else "Unknown"
            result.append({
                'student_id': row[1],
                'student_name': student_name,
                'status': row[3],
                'timestamp': str(row[4]) if row[4] else None
            })
        cur.close()
        return jsonify({'session_name': session_name, 'attendance_records': result, 'record_count': len(result)}), 200
    except ValueError:
        return jsonify({'message': 'session_id and request_id must be integers.'}), 400
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in get_session_attendance: {e}")
        mysql.connection.rollback()
        return jsonify({'message': 'Failed to retrieve session attendance due to a database error.'}), 500
    finally:
        if cur:
            cur.close()

# bulk student import from excel sheet 

# allow file upload 
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16*1024*1024 #16mb
allowed_extensions = {'xlsx', 'xls'}
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions
# import students from excel sheet
@app.route('/import_students', methods=['POST'])
def import_students():
    request_id = request.form.get('request_id')
    if not request_id:
        return jsonify({'message': 'request_id is required.'}), 400
    try:
        request_id = int(request_id)
    except ValueError:
        return jsonify({'message': 'request_id must be an integer.'}), 400
    cur = None
    try:
        cur = mysql.connection.cursor()
        # Check if the requesting user is an ADMIN or TEACHER
        cur.execute("SELECT role FROM user WHERE id = %s", (request_id,))
        user_role_result = cur.fetchone()
        if not user_role_result or user_role_result[0] not in ('ADMIN', 'TEACHER'):
            return jsonify({'message': 'User not authorized to import students.'}), 403
        if 'file' not in request.files:
            return jsonify({'message': 'No file part'}), 400
        file=request.files['file']
        if file.filename == '':
            return jsonify({'message': 'No selected file'}), 400
        if not allowed_file(file.filename):
            return jsonify({'message': 'Invalid file type. Only .xlsx and .xls files are allowed.'}), 400
        filename = secure_filename(file.filename)
        filepath=os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        df=pd.read_excel(filepath)
        expected_columns = {'id', 'name', 'class', 'email', 'phone'}
        if not expected_columns.issubset(df.columns):
            os.remove(filepath)
            return jsonify({'message': f'Excel file must contain the following columns: {", ".join(expected_columns)}'}), 400
        student_count=0
        for index, row in df.iterrows():
            id = row['id']
            name = row['name']
            class_name=row['class']
            email = row['email']
            phone = row['phone']
            # check student already in  database or not 
            cur.execute("SELECT id FROM student WHERE id = %s", (id,))
            student=cur.fetchone()
            if student:
                continue #skip if student already exists
            cur.execute("INSERT INTO student (id, name, class, email, phone) VALUES (%s, %s, %s, %s, %s)", (id, name, class_name, email,phone))
            student_count += 1
        mysql.connection.commit()
        os.remove(filepath)
        return jsonify({'message': 'Students imported successfully!', 'student_count': student_count}), 201
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in import_students: {e}")
        mysql.connection.rollback()
        return jsonify({'message': 'Failed to import students due to a database error.'}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error in import_students: {e}")
        return jsonify({'message': 'An unexpected error occurred while importing students.'}), 500
    finally:
        if cur:
            cur.close()
    
# register user
@app.route('/register_user', methods=['POST'])
def register_user():
    data = request.get_json()
    name = data.get('name')
    email = data.get('email')
    phone = data.get('phone')
    password = data.get('password')
    role = data.get('role')
    if not name or not email or not phone or not password or not role:
        return jsonify({'message': 'All fields are required!'}), 400
    if role not in ['ADMIN', 'TEACHER']:
        return jsonify({'message': 'Invalid role!'}), 400
    cur=None
    try:
        cur=mysql.connection.cursor()
        # check user is exit or not 
        cur.execute("SELECT email FROM user WHERE email=%s", (email,))
        existing_user=cur.fetchone()
        if existing_user:
            return jsonify({'message': 'User already exists with this email, Try another one or login!'}), 400
        cur.execute("INSERT INTO user (name, email, phone, password, role) VALUES (%s, %s, %s, %s, %s)", (name, email, phone, password, role))
        mysql.connection.commit()
        new_user_id = cur.lastrowid
        return jsonify({'message': 'User registered successfully!', 'user_id': new_user_id}), 201
    except MySQLdb.Error as e:
        app.logger.error(f"Database error in register_user: {e}")
        mysql.connection.rollback()
        return jsonify({'message': 'Failed to register user due to a database error.'}), 500
    finally:
        if cur:
            cur.close()
    

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
