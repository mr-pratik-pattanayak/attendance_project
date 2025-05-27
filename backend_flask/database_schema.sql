-- Create the database
CREATE DATABASE IF NOT EXISTS attendance_app;
USE attendance_app;

-- user Table
-- add phone no column 
CREATE TABLE user (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(50) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE,
    phone VARCHAR(15) not null ,
    password VARCHAR(255) NOT NULL,
    role ENUM('admin', 'teacher') NOT NULL
);

-- Student Table
CREATE TABLE student (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(50) NOT NULL,
    class VARCHAR(50) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE,
    phone VARCHAR(15) NOT NULL
);

-- Session Table
CREATE TABLE session (
    id INT PRIMARY KEY AUTO_INCREMENT,
    session_name VARCHAR(100) NOT NULL,
    session_code VARCHAR(50) NOT NULL UNIQUE,
    expiry_time DATETIME NOT NULL,
    created_by int not null,
    class VARCHAR(50) NOT NULL,
    ADD CONSTRAINT fk_created_by FOREIGN KEY (created_by) REFERENCES user(id) ON DELETE CASCADE ON UPDATE CASCADE
);

-- Attendance Table
CREATE TABLE attendance (
    id INT PRIMARY KEY AUTO_INCREMENT,
    student_id INT NOT NULL,
    session_id INT NOT NULL,
    status VARCHAR(10) NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES student(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES session(id) ON DELETE CASCADE
);
