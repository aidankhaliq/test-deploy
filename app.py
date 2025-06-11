# app.py - Language Learning Application
# This is the main application file that handles routes and user authentication

# Standard library imports
import os
import json
import random
import secrets
import smtplib
import sqlite3
import hashlib
import uuid
import traceback
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict, Counter
from random import sample, shuffle
from functools import wraps
import time

# Third-party imports
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import google.generativeai as genai
import pandas as pd
import io
import csv
import difflib
import mimetypes

# Initialize Flask application
app = Flask(__name__)
CORS(app)

# Use environment variable for secret key in production
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')

# --- File Upload Configuration ---
UPLOAD_FOLDER = 'static/uploads/'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# --- SMTP Configuration for Email Notifications ---
# These settings are used for sending OTP and password reset emails
USE_CONSOLE_OTP = False  # Set to False to send actual emails

# SMTP settings for Gmail
smtp_server = os.environ.get('SMTP_SERVER', "smtp.gmail.com")
smtp_port = int(os.environ.get('SMTP_PORT', 465))  # SSL port for Gmail
smtp_user = os.environ.get('SMTP_USER', "kishenkish18@gmail.com")  # Replace with your actual email
smtp_password = os.environ.get('SMTP_PASSWORD', "pxcu tasw ndev hnvf")  # Replace with your actual password

# --- Google Gemini AI Configuration ---
API_KEY = os.environ.get('GEMINI_API_KEY', "AIzaSyC0M-x3-IiWaroDCwHo59vcF4GBWijhPC0")
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

# Add Gemini Vision model for image support
vision_model = genai.GenerativeModel('gemini-pro-vision')

# --- Flashcards now load from database instead of Excel files ---

# --- Database Functions ---
# These functions handle database connections and common database operations

def get_db_connection():
    """
    Creates and returns a connection to the SQLite database with proper configuration.

    Returns:
        sqlite3.Connection: A configured database connection
    """
    # Get the absolute path to the database file
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # This allows accessing columns by name
    conn.execute('PRAGMA busy_timeout = 30000')  # 30 second timeout to avoid locking issues
    return conn

def get_user_id(email):
    """
    Retrieves a user's ID from their email address.

    Args:
        email (str): The user's email address

    Returns:
        int or None: The user's ID if found, None otherwise
    """
    conn = None
    try:
        conn = get_db_connection()
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        return user['id'] if user else None
    finally:
        # Ensure connection is closed even if an exception occurs
        if conn:
            conn.close()

def check_login(email, password, security_answer):
    """
    Validates user login credentials including security answer.

    Args:
        email (str): The user's email
        password (str): The user's password (plain text)
        security_answer (str): Answer to the security question

    Returns:
        dict or None: User data if authentication succeeds, None otherwise
    """
    with get_db_connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user['password'], password) and user['security_answer'] == security_answer:
            # Check if account is active
            is_active = user['is_active'] if 'is_active' in user.keys() else 1
            if is_active == 0:
                # Account is deactivated, return None but don't indicate why for security
                return None
            return user
        return None

def register_user(username, email, password, security_answer):
    """
    Registers a new user in the database.

    Args:
        username (str): The user's chosen username
        email (str): The user's email address
        password (str): The user's password (will be hashed)
        security_answer (str): Answer to the security question

    Returns:
        bool: True if registration succeeded, False otherwise
    """
    with get_db_connection() as conn:
        # Check if email already exists
        if conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            return False

        try:
            conn.execute(
                "INSERT INTO users (username, email, password, security_answer) VALUES (?, ?, ?, ?)",
                (username, email, generate_password_hash(password), security_answer))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # This handles cases like username uniqueness constraint violations
            return False

# --- Helper Functions ---
# These utility functions support various features throughout the application

def allowed_file(filename):
    """Check if the uploaded file has an allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_otp():
    """Generates a 6-digit OTP"""
    return ''.join(random.choices('0123456789', k=6))

def send_otp(email, otp):
    """
    Sends an OTP to the user's email address.

    Args:
        email (str): The recipient's email address
        otp (str): The OTP to send

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    if USE_CONSOLE_OTP:
        print(f"OTP for {email}: {otp}")
        return True

    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = email
        msg['Subject'] = "Your OTP for Language Learning"

        body = f"""
        Hello,

        Your verification code is: {otp}

        This code is valid for 10 minutes.
        If you did not request this code, please ignore this email.

        Best regards,
        Language Learning App Team
        """
        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
            return True
    except Exception as e:
        print(f"Error sending OTP email: {str(e)}")
        return False

def add_notification(user_id, notification_message):
    """
    Adds a notification for a user.

    Args:
        user_id (int): The ID of the user to notify
        notification_message (str): The notification message
    """
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO notifications (user_id, message, timestamp) VALUES (?, ?, ?)",
            (user_id, notification_message, datetime.now())
        )
        conn.commit()

# --- Admin Decorator ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Admin access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Routes ---

@app.route('/')
def index():
    """Home page: show dashboard if logged in, else show landing page"""
    if 'user_id' in session:
        return render_template('dashboard.html', username=session['username'], is_admin=session.get('is_admin', False))
    else:
        return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handles user login"""
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        security_answer = request.form.get('security_answer')

        # First check if the account exists and is deactivated
        with get_db_connection() as conn:
            existing_user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if existing_user and check_password_hash(existing_user['password'], password) and existing_user['security_answer'] == security_answer:
                # Valid credentials, but check if account is deactivated
                is_active = existing_user['is_active'] if 'is_active' in existing_user.keys() else 1
                if is_active == 0:
                    flash('Your account has been deactivated. Would you like to reactivate it?', 'info')
                    return redirect(url_for('reactivate_account'))
        
        user = check_login(email, password, security_answer)
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['email'] = user['email']
            session['is_admin'] = user['is_admin'] if 'is_admin' in user.keys() else False

            # Generate and send OTP
            otp = generate_otp()
            session['otp'] = otp
            session['otp_timestamp'] = datetime.now().isoformat()
            if send_otp(email, otp):
                return redirect(url_for('verify_otp'))
            else:
                flash('Error sending OTP. Please try again.', 'error')
        else:
            flash('Invalid email, password, or security answer.', 'error')

    return render_template('login.html')

@app.route('/verify_otp', methods=['GET', 'POST'])
def verify_otp():
    """Handles OTP verification"""
    if 'otp' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        user_otp = request.form.get('otp')
        if user_otp == session['otp']:
            # Check if OTP is expired (5 minutes)
            otp_time = datetime.fromisoformat(session['otp_timestamp'])
            if (datetime.now() - otp_time).total_seconds() > 300:
                flash('OTP has expired. Please login again.', 'error')
                return redirect(url_for('login'))
            
            # Add login notification
            add_notification(session['user_id'], f"Welcome back! You logged in successfully at {datetime.now().strftime('%I:%M %p')}")
            
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid OTP. Please try again.', 'error')

    return render_template('verify_otp.html')

@app.route('/resend_otp', methods=['GET'])
def resend_otp():
    """Resends the OTP"""
    if 'email' not in session:
        return redirect(url_for('login'))
    otp = generate_otp()
    session['otp'] = otp
    session['otp_timestamp'] = datetime.now().isoformat()
    if send_otp(session['email'], otp):
        flash('New OTP has been sent to your email.', 'success')
    else:
        flash('Error sending OTP. Please try again.', 'error')
    return redirect(url_for('verify_otp'))

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    """Handles password reset requests"""
    if request.method == 'POST':
        email = request.form.get('email')
        security_answer = request.form.get('security_answer')

        with get_db_connection() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE email = ? AND security_answer = ?",
                (email, security_answer)
            ).fetchone()

            if user:
                # Generate reset token
                reset_token = secrets.token_urlsafe(32)
                expiry = datetime.now() + timedelta(hours=1)
                conn.execute(
                    "INSERT INTO password_resets (user_id, token, expiry) VALUES (?, ?, ?)",
                    (user['id'], reset_token, expiry)
                )
                conn.commit()
                # Send reset email
                reset_link = url_for('reset_password', reset_token=reset_token, _external=True)
                msg = MIMEMultipart()
                msg['From'] = smtp_user
                msg['To'] = email
                msg['Subject'] = "Password Reset Request"
                body = f"""
                You requested a password reset for your Language Learning account.

                Click the following link to reset your password:
                {reset_link}

                This link will expire in 1 hour.
                If you didn't request this reset, please ignore this email.
                """
                msg.attach(MIMEText(body, 'plain'))
                try:
                    with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
                        server.login(smtp_user, smtp_password)
                        server.send_message(msg)
                    flash('Password reset instructions have been sent to your email.', 'success')
                except Exception as e:
                    print(f"Error sending password reset email: {e}")
                    flash('Error sending reset email. Please try again.', 'error')
            else:
                flash('Invalid email or security answer.', 'error')
    return render_template('forgot_password.html')

@app.route('/reset_password/<reset_token>', methods=['GET', 'POST'])
def reset_password(reset_token):
    """Handles password reset"""
    with get_db_connection() as conn:
        reset = conn.execute(
            "SELECT * FROM password_resets WHERE token = ? AND expiry > ?",
            (reset_token, datetime.now())
        ).fetchone()
        if not reset:
            flash('Invalid or expired reset token.', 'error')
            return redirect(url_for('login'))
        if request.method == 'POST':
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            if new_password != confirm_password:
                flash('Passwords do not match.', 'error')
            else:
                conn.execute(
                    "UPDATE users SET password = ? WHERE id = ?",
                    (generate_password_hash(new_password), reset['user_id'])
                )
                conn.execute("DELETE FROM password_resets WHERE token = ?", (reset_token,))
            conn.commit()
            flash('Your password has been reset successfully.', 'success')
            return redirect(url_for('login'))
    return render_template('reset_password.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Handles user registration"""
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        security_answer = request.form.get('security_answer')
        
        if register_user(username, email, password, security_answer):
            # Get the newly created user ID to send welcome notification
            with get_db_connection() as conn:
                new_user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
                if new_user:
                    add_notification(new_user['id'], f"🎉 Welcome to the Language Learning Platform, {username}! Start your learning journey today.")
            
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Email already exists or invalid data.', 'error')

    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    """Displays the user's dashboard"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    return render_template('dashboard.html',
                         username=session['username'],
                         is_admin=session.get('is_admin', False))

@app.route('/logout')
def logout():
    """Handles user logout"""
    session.clear()
    return redirect(url_for('index'))

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    """Handles user settings"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    with get_db_connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
        
        if request.method == 'POST':
            action = request.form.get('action')
            
            if action == 'save_profile':
                # Handle profile information update
                name = request.form.get('name', '').strip()
                email = request.form.get('email', '').strip()
                phone = request.form.get('phone', '').strip()
                location = request.form.get('location', '').strip()
                website = request.form.get('website', '').strip()
                bio = request.form.get('bio', '').strip()
                selected_avatar = request.form.get('selected_avatar', '').strip()
                
                # Handle profile picture upload
                profile_picture = None
                if 'profile_picture' in request.files:
                    file = request.files['profile_picture']
                    if file and file.filename != '' and allowed_file(file.filename):
                        import os
                        import uuid
                        # Create uploads directory if it doesn't exist
                        upload_dir = os.path.join('static', 'uploads')
                        os.makedirs(upload_dir, exist_ok=True)
                        
                        # Generate unique filename
                        file_extension = file.filename.rsplit('.', 1)[1].lower()
                        filename = f"{uuid.uuid4().hex}.{file_extension}"
                        file.save(os.path.join(upload_dir, filename))
                        profile_picture = filename
                
                # Update user profile
                try:
                    if profile_picture:
                        # Clear avatar if uploading new picture
                        conn.execute("""
                            UPDATE users SET name = ?, email = ?, phone = ?, location = ?, 
                            website = ?, bio = ?, profile_picture = ?, avatar = NULL 
                            WHERE id = ?
                        """, (name, email, phone, location, website, bio, profile_picture, session['user_id']))
                    elif selected_avatar:
                        # Clear profile picture if selecting avatar
                        conn.execute("""
                            UPDATE users SET name = ?, email = ?, phone = ?, location = ?, 
                            website = ?, bio = ?, avatar = ?, profile_picture = NULL 
                            WHERE id = ?
                        """, (name, email, phone, location, website, bio, selected_avatar, session['user_id']))
                    else:
                        # Update without changing pictures
                        conn.execute("""
                            UPDATE users SET name = ?, email = ?, phone = ?, location = ?, 
                            website = ?, bio = ? WHERE id = ?
                        """, (name, email, phone, location, website, bio, session['user_id']))
                    
                    conn.commit()
                    flash('Profile updated successfully!', 'success')
                except Exception as e:
                    flash(f'Error updating profile: {str(e)}', 'error')
                    
            elif action == 'change_password':
                # Handle password change
                current_password = request.form.get('current_password')
                new_password = request.form.get('new_password')
                confirm_password = request.form.get('confirm_password')
                
                if not current_password or not new_password or not confirm_password:
                    flash('All password fields are required.', 'error')
                elif not check_password_hash(user['password'], current_password):
                    flash('Current password is incorrect.', 'error')
                elif new_password != confirm_password:
                    flash('New passwords do not match.', 'error')
                elif len(new_password) < 8:
                    flash('New password must be at least 8 characters.', 'error')
                else:
                    try:
                        conn.execute(
                            "UPDATE users SET password = ? WHERE id = ?",
                            (generate_password_hash(new_password), session['user_id'])
                        )
                        conn.commit()
                        flash('Password changed successfully!', 'success')
                    except Exception as e:
                        flash(f'Error changing password: {str(e)}', 'error')
                        
            elif action == 'save_preferences':
                # Handle preferences update
                timezone = request.form.get('timezone', '').strip()
                datetime_format = request.form.get('datetime_format', '').strip()
                
                try:
                    conn.execute("""
                        UPDATE users SET timezone = ?, datetime_format = ? WHERE id = ?
                    """, (timezone, datetime_format, session['user_id']))
                    conn.commit()
                    flash('Preferences saved successfully!', 'success')
                except Exception as e:
                    flash(f'Error saving preferences: {str(e)}', 'error')
            
            # Refresh user data after any update
            user = conn.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    
    # Get available avatars (create some default ones if directory is empty)
    import os
    avatars = []
    avatar_dir = os.path.join('static', 'avatars')
    if os.path.exists(avatar_dir):
        avatars = [f for f in os.listdir(avatar_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif'))]
    
    return render_template('settings.html', user=user, avatars=avatars)

@app.route('/notifications', methods=['GET', 'POST'])
def notifications():
    """Displays user notifications and handles notification operations"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        action = request.json.get('action')
        if action == 'mark_read':
            notification_id = request.json.get('notification_id')
            with get_db_connection() as conn:
                conn.execute(
                    "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
                    (notification_id, session['user_id'])
                )
                conn.commit()
            return jsonify({'success': True})
        elif action == 'mark_all_read':
            with get_db_connection() as conn:
                conn.execute(
                    "UPDATE notifications SET is_read = 1 WHERE user_id = ?",
                    (session['user_id'],)
                )
                conn.commit()
            return jsonify({'success': True})
    with get_db_connection() as conn:
        notifications = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY timestamp DESC",
            (session['user_id'],)
        ).fetchall()
        
        # Separate notifications into read and unread
        unread_notifications = [n for n in notifications if n['is_read'] == 0]
        read_notifications = [n for n in notifications if n['is_read'] == 1]
        
    return render_template('notifications.html', 
                         notifications=notifications,
                         unread_notifications=unread_notifications,
                         read_notifications=read_notifications,
                         username=session.get('username', 'User'))

@app.route('/notifications/count')
def notifications_count():
    """Returns the count of unread notifications"""
    if 'user_id' not in session:
        return jsonify({'count': 0})

    with get_db_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) as count FROM notifications WHERE user_id = ? AND is_read = 0",
            (session['user_id'],)
        ).fetchone()['count']

    return jsonify({'count': count})

@app.route('/notifications/json')
def notifications_json():
    """Returns notifications as JSON"""
    if 'user_id' not in session:
        return jsonify([])

    with get_db_connection() as conn:
        notifications = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY timestamp DESC LIMIT 5",
            (session['user_id'],)
        ).fetchall()

    return jsonify([{
        'id': n['id'],
        'message': n['message'],
        'timestamp': n['timestamp'],
        'is_read': bool(n['is_read'])
    } for n in notifications])

@app.route('/mark_notification_read', methods=['POST'])
def mark_notification_read():
    """Mark a specific notification as read"""
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 401
    
    data = request.get_json()
    notification_id = data.get('notification_id')
    
    if not notification_id:
        return jsonify({'status': 'error', 'message': 'Notification ID required'}), 400
    
    try:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
                (notification_id, session['user_id'])
            )
            conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    """Handles admin login (no security answer required)"""
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        # Only check email and password for admin login
        with get_db_connection() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user and user['is_admin'] and check_password_hash(user['password'], password):
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['email'] = user['email']
                session['is_admin'] = True
                return redirect(url_for('admin_dashboard'))
            else:
                flash('Invalid credentials or insufficient permissions.', 'error')
    return render_template('admin_login.html')

@app.route('/admin_dashboard')
def admin_dashboard():
    """Displays the admin dashboard"""
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    languages = ['English', 'Chinese', 'Malay', 'Spanish', 'French', 'Portuguese', 'Tamil']
    difficulties = ['beginner', 'intermediate', 'advanced']
    quiz_data = {lang: {diff: [] for diff in difficulties} for lang in languages}
    with get_db_connection() as conn:
        users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        questions = conn.execute("SELECT * FROM quiz_questions").fetchall()
        for q in questions:
            lang = q['language']
            diff = q['difficulty'].lower()
            if lang in quiz_data and diff in quiz_data[lang]:
                quiz_data[lang][diff].append({
                    'id': q['id'],
                    'question': q['question'],
                    'options': json.loads(q['options']) if q['options'] else [],
                    'answer': q['answer'],
                    'question_type': q['question_type'],
                    'points': q['points']
                })
    return render_template('admin_dashboard.html', users=users, languages=languages, quiz_data=quiz_data)

@app.route('/admin/reset_progress/<int:user_id>', methods=['POST'])
def admin_reset_progress(user_id):
    """Resets a user's progress"""
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    with get_db_connection() as conn:
        conn.execute("DELETE FROM quiz_results WHERE user_id = ?", (user_id,))
        conn.commit()
    return jsonify({'success': True})

@app.route('/admin/toggle_admin/<int:user_id>', methods=['POST'])
def admin_toggle_admin(user_id):
    """Toggles admin status for a user"""
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    with get_db_connection() as conn:
        user = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
        if user:
            new_status = 0 if user['is_admin'] else 1
            conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (new_status, user_id))
            conn.commit()
            return jsonify({'success': True, 'new_status': new_status})
    return jsonify({'success': False, 'message': 'User not found'})

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
    """Deletes a user"""
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': 'Unauthorized'})
    with get_db_connection() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    return jsonify({'success': True})

@app.route('/admin_logout')
def admin_logout():
    """Handles admin logout"""
    session.clear()
    return redirect(url_for('index'))

@app.route('/chatbot', methods=['GET', 'POST'])
def chatbot():
    """Handles the chatbot interface"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        message = request.form.get('message')
        if message:
            response = get_gemini_response(message)
            return jsonify({'response': response})
    
    return render_template('chatbot.html')

@app.route('/chat_history/sessions')
def get_chat_sessions():
    """Returns chat session history"""
    if 'user_id' not in session:
        return jsonify([])
    with get_db_connection() as conn:
        sessions = conn.execute(
            "SELECT * FROM chat_sessions WHERE user_id = ? ORDER BY last_message_at DESC",
            (session['user_id'],)
        ).fetchall()
        session_data = []
        for s in sessions:
            # Get the first user message for this session
            first_msg_row = conn.execute(
                "SELECT message FROM chat_messages WHERE session_id = ? AND message IS NOT NULL AND TRIM(message) != '' ORDER BY timestamp ASC LIMIT 1",
                (s['session_id'],)
            ).fetchone()
            first_message = first_msg_row['message'] if first_msg_row else ''
            session_data.append({
                'id': s['session_id'],
                'session_id': s['session_id'],
                'language': s['language'],
                'started_at': s['started_at'],
                'last_message_at': s['last_message_at'],
                'first_message': first_message
            })
    return jsonify(session_data)

@app.route('/chat_history/session/<session_id>')
def get_session_messages(session_id):
    """Returns messages for a specific chat session"""
    if 'user_id' not in session:
        return jsonify([])
    
    with get_db_connection() as conn:
        # First verify this session belongs to the current user
        session_check = conn.execute(
            "SELECT 1 FROM chat_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, session['user_id'])
        ).fetchone()
        
        if not session_check:
            return jsonify([])  # Session doesn't belong to this user
            
        messages = conn.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY timestamp",
            (session_id,)
        ).fetchall()
    
    return jsonify([{
        'id': m['id'],
        'message': m['message'],
        'bot_response': m['bot_response'],
        'timestamp': m['timestamp']
    } for m in messages])

@app.route('/chat_history/delete/<session_id>', methods=['DELETE'])
def delete_chat_session(session_id):
    """Delete a specific chat session and all its messages"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        with get_db_connection() as conn:
            # First verify this session belongs to the current user
            session_check = conn.execute(
                "SELECT 1 FROM chat_sessions WHERE session_id = ? AND user_id = ?",
                (session_id, session['user_id'])
            ).fetchone()
            
            if not session_check:
                return jsonify({'error': 'Session not found or access denied'}), 404
            
            # Delete all messages in this session first (due to foreign key constraint)
            conn.execute(
                "DELETE FROM chat_messages WHERE session_id = ?",
                (session_id,)
            )
            
            # Delete the session itself
            conn.execute(
                "DELETE FROM chat_sessions WHERE session_id = ? AND user_id = ?",
                (session_id, session['user_id'])
            )
            
            conn.commit()
            
            return jsonify({'success': True, 'message': 'Chat session deleted successfully'})
            
    except Exception as e:
        print(f'Error deleting chat session {session_id}: {e}')
        return jsonify({'error': 'Failed to delete chat session'}), 500

@app.route('/chat_history/delete_all', methods=['DELETE'])
def delete_all_chat_sessions():
    """Delete all chat sessions and messages for the current user"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        with get_db_connection() as conn:
            # Get all session IDs for this user
            sessions = conn.execute(
                "SELECT session_id FROM chat_sessions WHERE user_id = ?",
                (session['user_id'],)
            ).fetchall()
            
            # Delete all messages for this user's sessions
            conn.execute("""
                DELETE FROM chat_messages 
                WHERE session_id IN (
                    SELECT session_id FROM chat_sessions WHERE user_id = ?
                )
            """, (session['user_id'],))
            
            # Delete all sessions for this user
            deleted_count = conn.execute(
                "DELETE FROM chat_sessions WHERE user_id = ?",
                (session['user_id'],)
            ).rowcount
            
            conn.commit()
            
            return jsonify({
                'success': True, 
                'message': f'Deleted {deleted_count} chat sessions successfully'
            })
            
    except Exception as e:
        print(f'Error deleting all chat sessions for user {session["user_id"]}: {e}')
        return jsonify({'error': 'Failed to delete chat sessions'}), 500

def get_gemini_response(prompt):
    """
    Gets a response from the Gemini AI model.
    Args:
        prompt (str): The user's input prompt
    Returns:
        str: The AI's response
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Error getting Gemini response: {str(e)}")
        return "I apologize, but I'm having trouble processing your request right now. Please try again later."



def ensure_user_columns():
    """Ensures all required columns exist in the users table and creates additional tables"""
    with get_db_connection() as conn:
        # Get existing columns
        existing_columns = [row['name'] for row in conn.execute("PRAGMA table_info(users);")]
        
        # Add missing columns to users table
        columns_to_add = [
            ('is_admin', 'INTEGER DEFAULT 0'),
            ('is_active', 'INTEGER DEFAULT 1'),
            ('timezone', 'TEXT'),
            ('datetime_format', 'TEXT'),
            ('name', 'TEXT'),
            ('phone', 'TEXT'),
            ('location', 'TEXT'),
            ('website', 'TEXT'),
            ('bio', 'TEXT'),
            ('profile_picture', 'TEXT'),
            ('avatar', 'TEXT'),
        ]
        
        for col, typ in columns_to_add:
            if col not in existing_columns:
                try:
                    conn.execute(f'ALTER TABLE users ADD COLUMN {col} {typ};')
                    print(f"✅ Column '{col}' added to users table!")
                except Exception as e:
                    print(f"Column '{col}' already exists or error: {e}")
        
        # Create additional tables if they don't exist
        conn.execute('''
            CREATE TABLE IF NOT EXISTS study_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                note TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE(user_id, word)
            )
        ''')
        print("✅ Table 'study_list' created/verified!")

        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                words_learned INTEGER DEFAULT 0,
                conversation_count INTEGER DEFAULT 0,
                accuracy_rate FLOAT DEFAULT 0.0,
                daily_streak INTEGER DEFAULT 0,
                last_activity_date DATE,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        print("✅ Table 'user_progress' created/verified!")

        conn.execute('''
            CREATE TABLE IF NOT EXISTS achievements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                achievement_type TEXT NOT NULL,
                achievement_name TEXT NOT NULL,
                description TEXT NOT NULL,
                earned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        print("✅ Table 'achievements' created/verified!")

        conn.execute('''
            CREATE TABLE IF NOT EXISTS account_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                activity_type TEXT NOT NULL,
                details TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        print("✅ Table 'account_activity' created/verified!")

        # Create password_resets table if it doesn't exist
        conn.execute('''
            CREATE TABLE IF NOT EXISTS password_resets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT NOT NULL,
                expiry DATETIME NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        print("✅ Table 'password_resets' created/verified!")

        conn.commit()

@app.route('/chat', methods=['POST'])
def chat():
    """Handles chat messages with optional image support"""
    if 'user_id' not in session:
        return jsonify({'error': 'Please log in to use the chatbot'}), 401
    try:
        # Accept both JSON and multipart/form-data
        if request.content_type and request.content_type.startswith('multipart/form-data'):
            print("Received multipart/form-data")
            message = request.form.get('message', '').strip()
            language = request.form.get('language', 'english').strip()
            session_id = request.form.get('session_id')
            image_file = request.files.get('image')
        else:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data received'}), 400
            message = data.get('message', '').strip()
            language = data.get('language', 'english').strip()
            session_id = data.get('session_id')
            image_file = None
            
        if not message and not image_file:
            return jsonify({'error': 'Message or image is required'}), 400
            
        # Create or update chat session
        with get_db_connection() as conn:
            if session_id:
                conn.execute('''
                    UPDATE chat_sessions 
                    SET last_message_at = CURRENT_TIMESTAMP
                    WHERE session_id = ? AND user_id = ?
                ''', (session_id, session['user_id']))
            else:
                session_id = f"sess-{secrets.token_hex(16)}"
                conn.execute('''
                    INSERT INTO chat_sessions (session_id, user_id, language)
                    VALUES (?, ?, ?)
                ''', (session_id, session['user_id'], language))
                
        # If image is present, use Gemini Vision
        if image_file and allowed_file(image_file.filename):
            filename = secure_filename(image_file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            try:
                image_file.save(file_path)
                print(f"Image saved to {file_path}")
            except Exception as e:
                print(f"Error saving image: {e}")
                return jsonify({'error': 'Failed to save the uploaded image.'}), 500

            # Try Gemini Vision first
            gemini_failed = False
            prompt = f"""
You are a helpful and encouraging language tutor. The user has uploaded an image and asked: '{message}'.

1. First, carefully analyze the image and provide a detailed description of what is shown, including any objects, people, text, or context. If possible, include interesting facts or cultural notes about what you see.
2. Then, translate your description into {language} in a clear and natural way, as if explaining to a language learner.
3. Finally, provide the English translation of your {language} response on a new line.

Format your response exactly like this:
[Response in {language}]
[English translation]

Response:
"""
            try:
                with open(file_path, 'rb') as img_f:
                    image_bytes = img_f.read()
                response = vision_model.generate_content([prompt, image_bytes])
                if response and hasattr(response, 'text') and response.text.strip():
                    response_text = response.text.strip()
                else:
                    print("Empty or invalid response from Gemini Vision API")
                    gemini_failed = True
            except Exception as e:
                print(f"Error in Gemini Vision image processing: {str(e)}")
                gemini_failed = True

            # If Gemini Vision failed, use BLIP as fallback
            if gemini_failed:
                print("Falling back to BLIP image captioning...")
                try:
                    from PIL import Image
                    from transformers import BlipProcessor, BlipForConditionalGeneration
                    processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
                    blip_model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")
                    raw_image = Image.open(file_path).convert('RGB')
                    inputs = processor(raw_image, return_tensors="pt")
                    out = blip_model.generate(**inputs)
                    caption = processor.decode(out[0], skip_special_tokens=True)
                    print(f"BLIP caption: {caption}")
                    # Now translate the caption using Gemini text model
                    translation_prompt = f"Translate the following image description into {language}, then provide the English translation on a new line.\nDescription: {caption}\n\nFormat:\n[Response in {language}]\n[English translation]\n\nResponse:"
                    response_text = get_gemini_response(translation_prompt)
                except Exception as blip_e:
                    print(f"BLIP image captioning error: {blip_e}")
                    response_text = f'[Sorry, there was an error analyzing the image. Please ensure all dependencies are installed.]\n[Sorry, there was an error analyzing the image. Please ensure all dependencies are installed.]'
        else:
            # Text-only response
            prompt_text = f"You are a helpful and encouraging language tutor teaching {language}. The student's message is: '{message}'\nPlease provide a very concise response that directly addresses the student's message. Avoid unnecessary details or conversational filler unless specifically asked.\nProvide your response in the following format:\n[Response in {language}]\n[English translation]\nResponse:"
            response_text = get_gemini_response(prompt_text)
        
        # Split the response into the target language response and English translation
        response_parts = response_text.split('\n', 1)
        target_language_response = response_parts[0].strip()
        english_translation = response_parts[1].strip() if len(response_parts) > 1 else ""
        
        # Store the message and response
        with get_db_connection() as conn:
            conn.execute('''
                INSERT INTO chat_messages (session_id, message, bot_response)
                VALUES (?, ?, ?)
            ''', (session_id, message, response_text))
            conn.commit()
        
        return jsonify({
            'response': target_language_response,
            'translation': english_translation,
            'session_id': session_id
        }), 200
        
    except Exception as e:
        print(f"Error in chat route: {str(e)}")
        print(f"Full traceback: {traceback.format_exc()}")
        return jsonify({'error': 'An error occurred processing your message'}), 500

@app.route('/quiz', methods=['GET', 'POST'])
def quiz_select():
    """Quiz language and difficulty selector page"""
    languages = ['English', 'Chinese', 'Malay', 'Spanish', 'French', 'Portuguese', 'Tamil']
    difficulties = ['Beginner', 'Intermediate', 'Advanced']
    if request.method == 'POST':
        language = request.form.get('language')
        difficulty = request.form.get('difficulty')
        return redirect(url_for('quiz_questions', language=language, difficulty=difficulty))
    return render_template('quiz_select.html', languages=languages, difficulties=difficulties)

@app.route('/quiz/questions', methods=['GET', 'POST'])
def quiz_questions():
    language = request.args.get('language') or request.form.get('language')
    difficulty = request.args.get('difficulty') or request.form.get('difficulty')
    user_id = session.get('user_id')

    # If it's a GET request, fetch new random questions and store in session
    if request.method == 'GET':
        # Clear any previous quiz results from the session
        session.pop('quiz_result', None)

        print(f"[DEBUG] GET /quiz/questions: Language={language}, Difficulty={difficulty}") # Debug log
        questions = []
        if language and difficulty:
            with get_db_connection() as conn:
                db_questions = conn.execute(
                    "SELECT * FROM quiz_questions WHERE language = ? AND LOWER(difficulty) = ? ORDER BY RANDOM() LIMIT 10",
                    (language, difficulty.lower())
                ).fetchall()

                # Process fetched questions to parse options/explanations as needed
                for q in db_questions:
                    options = json.loads(q['options']) if q['options'] else []

                    # Replicate question processing logic for display
                    if q['question_type'] == 'word_matching' and options:
                        # Handle both old and new word matching formats
                        pairs = []
                        if isinstance(options, dict) and 'pairs' in options:
                            # Newer format: {"pairs": [{"key": "Dog", "value": "狗 (Gǒu)"}], "format": "key_value_matching"}
                            pairs = [(pair['key'], pair['value']) for pair in options['pairs']]
                        elif isinstance(options, list):
                            # Older format: [["Dog", "狗 (Gǒu)"], ["Cat", "猫 (Māo)"]]
                            pairs = [(pair[0], pair[1]) for pair in options]
                        
                        if pairs:
                            left = [pair[0] for pair in pairs]
                            right = [pair[1] for pair in pairs]
                            random.shuffle(left)
                            random.shuffle(right)
                            processed_options = list(zip(left, right))
                            questions.append({
                                'id': q['id'],
                                'question': q['question'],
                                'options': processed_options,
                                'original_options': pairs, # Store normalized pairs for grading
                                'answer': q['answer'],
                                'question_type': q['question_type'],
                                'points': q['points']
                            })
                    elif q['question_type'] in ['error_spotting', 'idiom_interpretation', 'cultural_nuances'] and options and isinstance(options, dict):
                        questions.append({
                            'id': q['id'],
                            'question': q['question'],
                            'options': options.get('options', []), # Extract the list of options
                            'original_options': options,  # Store the full dict for explanation
                            'answer': q['answer'],
                            'question_type': q['question_type'],
                            'points': q['points']
                        })
                    elif q['question_type'] == 'fill_in_the_blanks' and options and isinstance(options, dict):
                         questions.append({
                             'id': q['id'],
                             'question': q['question'],
                             'options': options, # Store the hint dict
                             'original_options': options, # Keep original for consistency
                             'answer': q['answer'],
                             'question_type': q['question_type'],
                             'points': q['points']
                         })
                    elif q['question_type'] == 'complex_rephrasing' and options and isinstance(options, dict):
                         questions.append({
                             'id': q['id'],
                             'question': q['question'],
                             'options': options.get('options', []), # Extract the list of options
                             'original_options': options,  # Keep original for consistency
                             'answer': q['answer'],
                             'question_type': q['question_type'],
                             'points': q['points']
                         })
                    else:
                        # For other types, options are already a list or simple value
                        questions.append({
                            'id': q['id'],
                            'question': q['question'],
                            'options': options, # Options should already be in the correct format (list or string)
                            'original_options': options, # Keep original for consistency
                            'answer': q['answer'],
                            'question_type': q['question_type'],
                            'points': q['points']
                        })

        # Store the fetched and processed questions in the session for the POST request
        session['quiz_questions'] = questions
        session['quiz_start_time'] = time.time()
        print(f"[DEBUG] GET /quiz/questions: Stored {len(questions)} questions in session.") # Debug log
        return render_template('quiz_questions.html', language=language, difficulty=difficulty, questions=questions)

    # POST: grade answers
    # Retrieve questions from the session to grade against the correct set
    print(f"[DEBUG] POST /quiz/questions: Attempting to retrieve questions from session.") # Debug log
    questions = session.get('quiz_questions', [])
    print(f"[DEBUG] POST /quiz/questions: Retrieved {len(questions)} questions from session.") # Debug log
    if not questions:
        flash('Could not retrieve quiz questions from session. Please try the quiz again.', 'danger')
        return redirect(url_for('quiz_select'))

    user_answers = request.form.to_dict()
    start_time = session.get('quiz_start_time', time.time())
    end_time = time.time()
    time_taken = int(end_time - start_time)
    correct = 0
    # total = len(questions) # Total is now based on the number of questions retrieved from session
    review = []

    # Create a dictionary of questions by ID for easy lookup from the session questions
    questions_by_id = {q['id']: q for q in questions}

    # Iterate over submitted answers to grade
    # Exclude language and difficulty fields from user_answers
    question_answers = {}
    for key, value in user_answers.items():
        if key.startswith('q'):
            # Extract question ID, handling the '_matches' suffix for word matching
            if key.endswith('_matches'):
                qid_str = key[1:- len('_matches')]
            else:
                qid_str = key[1:]

            try:
                qid = int(qid_str)
                if qid not in question_answers:
                    question_answers[qid] = {}
                if key.endswith('_matches'):
                    # Store the raw JSON string for word matching
                    question_answers[qid]['word_matching_matches'] = value
                else:
                    # Store the answer for other types
                    question_answers[qid]['answer'] = value
            except ValueError:
                print(f"Warning: Could not parse question ID from key: {key}")
                continue # Skip this key if QID is not a valid integer

    # Iterate over the questions from the session to ensure all are considered, even if no answer submitted
    for q in questions:
        qid = q['id']
        user_ans_data = question_answers.get(qid, {})

        # qid = int(qid_str) # This line is no longer needed here
        # user_ans_raw = user_answers.get(qid_str) # This line is no longer needed here

        # q = questions_by_id.get(qid) # This lookup is no longer needed here as we iterate over questions directly

        # if q: # This check is implicitly handled by iterating over 'questions'
        user_ans = None
        is_correct = False
        correct_ans = ''
        explanation = ''

        # Get the original options and question type from the session question
        original_options = q.get('original_options', q.get('options'))
        question_type = q['question_type']

        # Grading logic based on question type
        if question_type in ['multiple_choice', 'fill_in_the_blanks', 'phrase_completion', 'context_response']:
             user_ans_raw = user_ans_data.get('answer')
             if user_ans_raw is not None:
                 user_ans = str(user_ans_raw).strip()
             else:
                 user_ans = "" # Or handle missing answer

             correct_answers = [a.strip().lower() for a in str(q['answer']).split(';')]
             is_correct = user_ans.lower() in correct_answers
             correct_ans = q['answer']

        elif question_type == 'word_matching':
             # For word matching, user_ans_raw is a JSON string of selected pairs
             try:
                 user_matches_raw = user_ans_data.get('word_matching_matches')
                 if user_matches_raw is not None:
                     user_matches = json.loads(user_matches_raw)
                 else:
                     user_matches = [] # Handle missing word matching data

                 user_ans = user_matches # Store the parsed list of pairs
                 # Correct pairs are in original_options for word_matching
                 correct_pairs = [list(pair) for pair in original_options]
                 # Compare sets of tuples for order-independent matching
                 is_correct = Counter(tuple(pair) for pair in user_matches) == Counter(tuple(pair) for pair in correct_pairs)
                 correct_ans = user_matches if is_correct else correct_pairs # Show user's answer if correct, otherwise correct pairs
             except (json.JSONDecodeError, TypeError):
                 user_ans = [] # Invalid JSON
                 is_correct = False
                 correct_ans = [list(pair) for pair in original_options] # Show correct pairs on error
                 flash(f'Error processing word matching answer for question {qid}. Please try the quiz again.', 'warning')

        elif question_type in ['error_spotting', 'idiom_interpretation', 'cultural_nuances', 'complex_rephrasing']:
              user_ans_raw = user_ans_data.get('answer')
              if user_ans_raw is not None:
                  user_ans = str(user_ans_raw).strip()
              else:
                  user_ans = "" # Or handle missing answer

              correct_ans = str(q['answer']).strip() # Correct answer is stored in the 'answer' field, strip it

              # Debug log for comparison values
              print(f"[DEBUG] QID {qid} ({question_type}) Grading: User='{user_ans.lower()}', Correct='{correct_ans.lower()}'")

              is_correct = user_ans.lower() == correct_ans.lower() # Compare stripped and lowercased answers

              # Special handling for complex_rephrasing to ignore ending punctuation
              if question_type == 'complex_rephrasing':
                  user_ans_cleaned = user_ans.rstrip('.?! ') # Strip ., ?, !, and space from end
                  correct_ans_cleaned = correct_ans.rstrip('.?! ') # Strip ., ?, !, and space from end
                  is_correct = user_ans_cleaned.lower() == correct_ans_cleaned.lower()

              # Extract explanation if available in original_options (for types that have it)
              if question_type in ['error_spotting', 'idiom_interpretation', 'cultural_nuances']:
                   if isinstance(original_options, dict):
                        explanation = original_options.get('explanation', '')


         # Append result to review list
        review.append({
             'question': q['question'],
             'type': question_type,
             'user_answer': user_ans,
             'correct_answer': correct_ans,
             'is_correct': is_correct,
             'options': {'explanation': explanation} if explanation else None # Include explanation if found
         })

    # If question not found in session questions (shouldn't happen with correct flow)
    # else:
    #     print(f"Warning: Question with ID {qid} not found in session questions for grading. This answer will be skipped.")
    #     # Optionally add a placeholder review item or handle as error

    # Update total and correct based on processed review items
    total = len(review)
    correct = sum(item['is_correct'] for item in review)

    # Points system (re-calculated based on the graded questions)
    points_per = {'Beginner': 1, 'Intermediate': 3, 'Advanced': 5}
    bonus_perfect = {'Beginner': 5, 'Intermediate': 8, 'Advanced': 10}
    points = correct * points_per.get(difficulty, 1) # Use difficulty from form
    bonus = 0
    if correct == total:
        bonus += bonus_perfect.get(difficulty, 0) # Use difficulty from form
    if time_taken < 60: # Time bonus logic remains
        bonus += 5

    # Streaks (simple: check last 5 quizzes) - logic seems okay, uses user_id
    with get_db_connection() as conn:
         last_quizzes = conn.execute("SELECT passed FROM quiz_results_enhanced WHERE user_id = ? ORDER BY timestamp DESC LIMIT 5", (user_id,)).fetchall()
         streak = 0
         for qz in last_quizzes:
             if qz['passed']:
                 streak += 1
             else:
                 break
         # If current quiz is perfect and there was a streak, extend it
         if correct == total and (not last_quizzes or last_quizzes[0]['passed']):
             streak += 1
         # Apply streak bonus only if streak is > 1 after this quiz
         if streak > 1:
             bonus += 5 # Add streak bonus

    total_points = points + bonus

    # Save result using language and difficulty from form
    with get_db_connection() as conn:
        # Capitalize difficulty for legacy table
        difficulty_cap = difficulty.capitalize() if difficulty else ''
        conn.execute(
            "INSERT INTO quiz_results_enhanced (user_id, language, difficulty, score, total, percentage, passed, timestamp, question_details, points_earned, streak_bonus, time_bonus) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, language, difficulty, correct, total, (correct/total)*100, int(correct==total), datetime.now(), json.dumps(review), points, 5 if streak > 1 else 0, 5 if time_taken < 60 else 0)
        )
        # --- Add this block to also insert into legacy quiz_results for progress tracking ---
        conn.execute(
            "INSERT INTO quiz_results (user_id, language, difficulty, score, total, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, language, difficulty_cap, correct, total, datetime.now())
        )
        conn.commit()
        
        # Create quiz completion notification
        percentage_score = int((correct/total)*100)
        quiz_notification = f"You scored {correct}/{total} ({percentage_score}%) in {language} {difficulty} quiz"
        add_notification(user_id, quiz_notification)
        
        # Create achievement notifications for special scores
        if correct == total:  # Perfect score
            add_notification(user_id, "🎉 Perfect Score! You got 100% on your quiz!")
        elif percentage_score >= 90:  # Excellent score
            add_notification(user_id, "⭐ Excellent! You scored 90% or higher!")
        elif percentage_score >= 80:  # Good score
            add_notification(user_id, "👍 Good job! You're making great progress!")
        
        # === ACHIEVEMENT UNLOCK LOGIC ===
        # Helper to check if achievement already unlocked
        def has_achievement(user_id, ach_type):
            return conn.execute(
                'SELECT 1 FROM achievements WHERE user_id = ? AND achievement_type = ?',
                (user_id, ach_type)
            ).fetchone() is not None
        
        now = datetime.now()
        hour = now.hour
        today = now.date()
        # Night Owl: Complete a quiz after midnight (00:00-05:59)
        if 0 <= hour < 6 and not has_achievement(user_id, 'night_owl'):
            conn.execute(
                'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                (user_id, 'night_owl', 'Night Owl', 'Complete a quiz after midnight')
            )
        # Early Bird: Complete a quiz before 7am (05:00-06:59)
        if 5 <= hour < 7 and not has_achievement(user_id, 'early_bird'):
            conn.execute(
                'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                (user_id, 'early_bird', 'Early Bird', 'Complete a quiz before 7am')
            )
        # Speed Demon: Finish a quiz in under 60 seconds
        if time_taken < 60 and not has_achievement(user_id, 'speed_demon'):
            conn.execute(
                'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                (user_id, 'speed_demon', 'Speed Demon', 'Finish a quiz in record time')
            )
        # Hot Streak: 5 correct quizzes in a row
        last_5 = conn.execute("SELECT passed FROM quiz_results_enhanced WHERE user_id = ? ORDER BY timestamp DESC LIMIT 5", (user_id,)).fetchall()
        if len(last_5) == 5 and all(qz['passed'] for qz in last_5):
            if not has_achievement(user_id, 'hot_streak'):
                conn.execute(
                    'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                    (user_id, 'hot_streak', 'Hot Streak', '5 correct quizzes in a row')
                )
        # Perfectionist: 100% in 3 quizzes in a row
        last_3 = conn.execute("SELECT score, total FROM quiz_results_enhanced WHERE user_id = ? ORDER BY timestamp DESC LIMIT 3", (user_id,)).fetchall()
        if len(last_3) == 3 and all(qz['score'] == qz['total'] for qz in last_3):
            if not has_achievement(user_id, 'perfectionist'):
                conn.execute(
                    'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                    (user_id, 'perfectionist', 'Perfectionist', 'Get 100% in 3 quizzes in a row')
                )
        # Comeback Kid: Perfect score after failing a quiz
        last_2 = conn.execute("SELECT score, total FROM quiz_results_enhanced WHERE user_id = ? ORDER BY timestamp DESC LIMIT 2", (user_id,)).fetchall()
        if len(last_2) == 2 and last_2[1]['score'] < last_2[1]['total'] and last_2[0]['score'] == last_2[0]['total']:
            if not has_achievement(user_id, 'comeback_kid'):
                conn.execute(
                    'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                    (user_id, 'comeback_kid', 'Comeback Kid', 'Perfect score after failing a quiz')
                )
        # Consistency Champ: Complete quizzes 5 days in a row
        last_5_days = conn.execute("SELECT DISTINCT DATE(timestamp) as d FROM quiz_results_enhanced WHERE user_id = ? ORDER BY d DESC LIMIT 5", (user_id,)).fetchall()
        if len(last_5_days) == 5:
            days = [row['d'] for row in last_5_days]
            days_dt = [datetime.strptime(d, '%Y-%m-%d').date() for d in days]
            days_dt.sort()
            if all((days_dt[i] - days_dt[i-1]).days == 1 for i in range(1, 5)):
                if not has_achievement(user_id, 'consistency_champ'):
                    conn.execute(
                        'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                        (user_id, 'consistency_champ', 'Consistency Champ', 'Complete quizzes 5 days in a row')
                    )
        # Quick Thinker: 5 correct answers under 10 seconds each (in this quiz)
        quick_count = 0
        for item in review:
            if item['is_correct'] and isinstance(item['user_answer'], str) and time_taken/total < 10:
                quick_count += 1
        if quick_count >= 5 and not has_achievement(user_id, 'quick_thinker'):
            conn.execute(
                'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                (user_id, 'quick_thinker', 'Quick Thinker', '5 correct answers under 10 seconds each')
            )
        # Basic Vocab: 300 points in Beginner quizzes (per language)
        beginner_pts = conn.execute("SELECT SUM(points_earned) as pts FROM quiz_results_enhanced WHERE user_id = ? AND language = ? AND difficulty = 'Beginner'", (user_id, language)).fetchone()['pts'] or 0
        if beginner_pts >= 300 and not has_achievement(user_id, f'basic_vocab_{language}'):
            conn.execute(
                'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                (user_id, f'basic_vocab_{language}', 'Basic Vocab', f'300 points in Beginner quizzes for {language}')
            )
        # Language Guru: Maintain a 5 quiz streak (per language)
        last_5_lang = conn.execute("SELECT passed FROM quiz_results_enhanced WHERE user_id = ? AND language = ? ORDER BY timestamp DESC LIMIT 5", (user_id, language)).fetchall()
        if len(last_5_lang) == 5 and all(qz['passed'] for qz in last_5_lang):
            if not has_achievement(user_id, f'language_guru_{language}'):
                conn.execute(
                    'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                    (user_id, f'language_guru_{language}', 'Language Guru', f'Maintain a 5 quiz streak in {language}')
                )
        # Fluency Builder: Complete 10 Advanced quizzes (per language)
        adv_count = conn.execute("SELECT COUNT(*) as cnt FROM quiz_results_enhanced WHERE user_id = ? AND language = ? AND difficulty = 'Advanced'", (user_id, language)).fetchone()['cnt'] or 0
        if adv_count >= 10 and not has_achievement(user_id, f'fluency_builder_{language}'):
            conn.execute(
                'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                (user_id, f'fluency_builder_{language}', 'Fluency Builder', f'Complete 10 Advanced quizzes in {language}')
            )
        # Master of Language: Reach 1500 total points (per language)
        total_pts = conn.execute("SELECT SUM(points_earned) as pts FROM quiz_results_enhanced WHERE user_id = ? AND language = ?", (user_id, language)).fetchone()['pts'] or 0
        if total_pts >= 1500 and not has_achievement(user_id, f'master_of_language_{language}'):
            conn.execute(
                'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                (user_id, f'master_of_language_{language}', 'Master of Language', f'Reach 1500 total points in {language}')
            )
        # Quiz Explorer: Take quizzes in 5 different languages
        lang_count = conn.execute("SELECT COUNT(DISTINCT language) as cnt FROM quiz_results_enhanced WHERE user_id = ?", (user_id,)).fetchone()['cnt'] or 0
        if lang_count >= 5 and not has_achievement(user_id, 'quiz_explorer'):
            conn.execute(
                'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                (user_id, 'quiz_explorer', 'Quiz Explorer', 'Take quizzes in 5 different languages')
            )
        # Polyglot: Complete quizzes in all languages
        all_langs = set(['English', 'Spanish', 'French', 'Tamil', 'Malay', 'Portuguese', 'Chinese'])
        user_langs = set([row['language'] for row in conn.execute("SELECT DISTINCT language FROM quiz_results_enhanced WHERE user_id = ?", (user_id,)).fetchall()])
        if all_langs.issubset(user_langs) and not has_achievement(user_id, 'polyglot'):
            conn.execute(
                'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                (user_id, 'polyglot', 'Polyglot', 'Complete quizzes in all languages')
            )
        conn.commit()
        
        # Create streak notification
        if streak > 1:
            add_notification(user_id, f"🔥 Hot Streak! You're on a {streak} quiz winning streak!")
        
        # Create time bonus notification
        if time_taken < 60:
            add_notification(user_id, "⚡ Speed Bonus! You completed the quiz in under 60 seconds!")

    # Store the correct quiz result in session before redirecting
    # session['quiz_result'] = {
    #     'language': language, # Use language from form
    #     'difficulty': difficulty, # Use difficulty from form
    #     'correct': correct,
    #     'total': total,
    #     'time_taken': time_taken,
    #     'review': review,
    #     'points': points,
    #     'bonus': bonus,
    #     'total_points': total_points,
    #     'streak': streak
    # }
    print(f"[DEBUG] POST /quiz/questions: Stored quiz_result in session for language {language}, difficulty {difficulty}.") # Debug log - kept for now, will remove once confirmed fixed

    # Redirect to quiz_results without language/difficulty args, it will read from session
    return redirect(url_for('quiz_results'))

@app.route('/quiz/results')
def quiz_results():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    with get_db_connection() as conn:
        # Fetch the most recent quiz result for the user from the database
        result_db = conn.execute(
            "SELECT * FROM quiz_results_enhanced WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
            (user_id,)
        ).fetchone()

    if not result_db:
        # If no result found in DB (shouldn't happen in normal flow after submission, but good fallback)
        flash('Could not retrieve quiz results.', 'danger')
        return redirect(url_for('dashboard'))

    # Prepare data for the template, mapping DB columns to template variables
    # Calculate combined bonus and total points
    combined_bonus = result_db['streak_bonus'] + result_db['time_bonus'] # Assuming streak_bonus and time_bonus are stored
    total_points_calculated = result_db['points_earned'] + combined_bonus

    # Calculate time taken from session start time if available
    time_taken_value = "N/A" # Default if not available
    quiz_start_time = session.get('quiz_start_time')
    if quiz_start_time:
        try:
            time_taken_value = int(time.time() - quiz_start_time)
        except (TypeError, ValueError):
            pass # Keep as N/A if calculation fails

    # Map DB data to template variables
    result_for_template = {
        'language': result_db['language'],
        'difficulty': result_db['difficulty'],
        'correct': result_db['score'], # Map 'score' from DB to 'correct' for template
        'total': result_db['total'],
        'time_taken': time_taken_value,
        'points': result_db['points_earned'], # Map 'points_earned' to 'points'
        'bonus': combined_bonus, # Use the calculated combined bonus
        'total_points': total_points_calculated, # Use the calculated total points
        'streak': result_db['streak_bonus'], # Map 'streak_bonus' to 'streak'
        'review': json.loads(result_db['question_details']) # Load the review data from JSON string
    }

    # Pass the retrieved data to the template
    return render_template('quiz_results.html', **result_for_template)

@app.route('/progress')
def progress():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    languages = ['English', 'Spanish', 'French', 'Tamil', 'Malay', 'Portuguese', 'Chinese']
    LEVEL_REQUIREMENTS = {
        'beginner': {'points': 300},
        'intermediate': {'points': 700},
        'advanced': {'points': 1500}
    }
    progress_dict = {
        'progress': {},
        'total_points': 0,
        'total_quizzes': 0,
        'recent_activity': [],
        'LEVEL_REQUIREMENTS': LEVEL_REQUIREMENTS
    }
    with get_db_connection() as conn:
        # Fetch all achievements for the user once
        user_achievements = conn.execute('SELECT achievement_type FROM achievements WHERE user_id = ?', (user_id,)).fetchall()
        achievement_types = set([row['achievement_type'] for row in user_achievements])
        # Gather per-language stats
        for language in languages:
            # Icon mapping for language
            icon_map = {
                'English': 'flag-usa',
                'Spanish': 'flag',
                'French': 'flag',
                'German': 'flag',
                'Italian': 'flag',
                'Portuguese': 'flag',
                'Japanese': 'flag'
            }
            icon = icon_map.get(language, 'flag')
            # Points and quizzes
            quizzes_legacy = conn.execute('SELECT difficulty, COUNT(*) as cnt FROM quiz_results WHERE user_id = ? AND language = ? GROUP BY difficulty', (user_id, language)).fetchall()
            quizzes_enhanced = conn.execute('SELECT difficulty, COUNT(*) as cnt FROM quiz_results_enhanced WHERE user_id = ? AND language = ? GROUP BY difficulty', (user_id, language)).fetchall()
            counts = {'beginner': 0, 'intermediate': 0, 'advanced': 0}
            for row in quizzes_legacy:
                diff = row['difficulty'].lower()
                if diff in counts:
                    counts[diff] += row['cnt']
            for row in quizzes_enhanced:
                diff = row['difficulty'].lower()
                if diff in counts:
                    counts[diff] += row['cnt']
            beginner_pts = counts['beginner']
            intermediate_pts = counts['intermediate']
            advanced_pts = counts['advanced']
            beginner_pts = min(beginner_pts, 300)
            if beginner_pts < 300:
                intermediate_pts = 0
                advanced_pts = 0
            else:
                intermediate_pts = min(intermediate_pts, 400)
                if intermediate_pts < 400:
                    advanced_pts = 0
                else:
                    advanced_pts = min(advanced_pts, 800)
            total_pts = beginner_pts + intermediate_pts + advanced_pts
            # Set current level
            if total_pts >= 701:
                current_level = 'advanced'
            elif total_pts >= 301:
                current_level = 'intermediate'
            else:
                current_level = 'beginner'
            # Quizzes taken
            quizzes_taken = sum(counts.values())
            # Streaks (simple: check last 5 quizzes)
            last_quizzes = conn.execute("SELECT passed FROM quiz_results_enhanced WHERE user_id = ? AND language = ? ORDER BY timestamp DESC LIMIT 10", (user_id, language)).fetchall()
            streak = 0
            highest_streak = 0
            temp_streak = 0
            for qz in last_quizzes:
                if qz['passed']:
                    temp_streak += 1
                    if temp_streak > highest_streak:
                        highest_streak = temp_streak
                else:
                    temp_streak = 0
            streak = temp_streak
            # Badges (populate from achievements)
            badges = set()
            # Add global achievements
            for ach in achievement_types:
                # Per-language achievements: basic_vocab_Language, language_guru_Language, fluency_builder_Language, master_of_language_Language
                if ach.startswith('basic_vocab_') and ach.endswith(language):
                    badges.add('basic_vocab')
                elif ach.startswith('language_guru_') and ach.endswith(language):
                    badges.add('language_guru')
                elif ach.startswith('fluency_builder_') and ach.endswith(language):
                    badges.add('fluency_builder')
                elif ach.startswith('master_of_language_') and ach.endswith(language):
                    badges.add('master_of_language')
                # Global achievements
                elif ach in [
                    'hot_streak', 'night_owl', 'early_bird', 'polyglot', 'comeback_kid', 'speed_demon',
                    'consistency_champ', 'quick_thinker', 'quiz_explorer', 'perfectionist']:
                    badges.add(ach)
            # Recent activity for this language
            recent_activity = conn.execute("SELECT * FROM quiz_results_enhanced WHERE user_id = ? AND language = ? ORDER BY timestamp DESC LIMIT 5", (user_id, language)).fetchall()
            activity_list = []
            for act in recent_activity:
                activity_list.append({
                    'language': language,
                    'difficulty': act['difficulty'],
                    'score': act['score'],
                    'total': act['total'],
                    'timestamp': act['timestamp'],
                    'passed': act['passed'],
                    'points_earned': act['points_earned'],
                    'streak_bonus': act['streak_bonus'],
                    'time_bonus': act['time_bonus']
                })
            # Compose per-language dict
            progress_dict['progress'][language] = {
                'icon': icon,
                'total_points': total_pts,
                'quizzes_taken': quizzes_taken,
                'current_level': current_level,
                'badges': list(badges),
                'streak': streak,
                'highest_streak': highest_streak,
                'next_level': True if current_level != 'advanced' else False,
                # Add more fields as needed
            }
            progress_dict['recent_activity'].extend(activity_list)
            progress_dict['total_points'] += total_pts
            progress_dict['total_quizzes'] += quizzes_taken
    return render_template('progress.html', progress=progress_dict)

@app.route('/admin/import-questions', methods=['POST'])
@admin_required
def admin_import_questions():
    language = request.form.get('language')
    difficulty = request.form.get('difficulty')
    question_type = request.form.get('question_type')
    file = request.files.get('excel_file')
    
    if not file or not file.filename.endswith(('.xlsx', '.xls')):
        flash('Please upload a valid Excel file.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    # Validate question types for intermediate level
    if difficulty == 'Intermediate' and question_type not in ['Phrase Completion', 'Error Spotting', 'Context Response']:
        flash('Invalid question type for intermediate level.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    try:
        df = pd.read_excel(file)
        inserted = 0
        with get_db_connection() as conn:
            # Get column names from the DataFrame and convert to lowercase for case-insensitive matching
            original_columns = df.columns.tolist()
            df.columns = df.columns.str.lower()
            lower_columns = df.columns.tolist()

            # --- Debugging: Print detected columns ---
            print(f"Detected columns (original): {original_columns}")
            print(f"Detected columns (lowercase): {lower_columns}")
            # ----------------------------------------

            if difficulty == 'Beginner':
                if question_type == 'Multiple Choice':
                    required_cols_lower = {'question', 'options', 'correct answer'}
                    if not required_cols_lower.issubset(df.columns):
                        # More specific error message
                        missing = list(required_cols_lower - set(df.columns))
                        flash(f'Excel file missing required columns for Multiple Choice: {", ".join(missing)}.', 'danger')
                        return redirect(url_for('admin_dashboard'))
                    for index, row in df.iterrows(): # Use index to report row number
                        try:
                            # Access data using the lowercase required column names
                            question_text = str(row.get('question', '')).strip()
                            
                            options_raw = row.get('options', '')
                            # Ensure options_raw is treated as string before splitting
                            options_text = str(options_raw).split(';') if options_raw is not None else []
                            options = [opt.strip() for opt in options_text if opt.strip()]

                            answer = str(row.get('correct answer', '')).strip()

                            if not question_text or not options or not answer:
                                flash(f'Row {index+2}: Missing data in required columns for Multiple Choice. Skipping row.', 'danger')
                                continue # Skip this row
                            conn.execute(
                                """
                                INSERT INTO quiz_questions (language, difficulty, question, options, answer, question_type, points)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (language, difficulty, question_text, json.dumps(options), answer, 'multiple_choice', 10) # Assuming 10 points for beginner MC
                            )
                            inserted += 1
                        except Exception as e:
                            flash(f'Error processing row {index+2} for Multiple Choice: {str(e)}', 'danger')
                            continue # Skip row with processing error
                elif question_type == 'Word Matching':
                    required_cols_lower = {'question', 'pairs'}
                    if not required_cols_lower.issubset(df.columns):
                        # More specific error message
                        missing = list(required_cols_lower - set(df.columns))
                        flash(f'Excel file missing required columns for Word Matching: {", ".join(missing)}.', 'danger')
                        return redirect(url_for('admin_dashboard'))
                    for index, row in df.iterrows(): # Use index to report row number
                        try:
                            question_text = str(row.get('question', '')).strip()
                            
                            pairs_raw = row.get('pairs', '')
                            if not pairs_raw:
                                flash(f'Row {index+2}: Missing pairs data for Word Matching. Skipping row.', 'danger')
                                continue
                            
                            # Parse pairs more carefully to handle special characters
                            pairs_text = str(pairs_raw).strip()
                            pairs_list = []
                            
                            # Split by semicolon to get individual pairs
                            pair_items = [item.strip() for item in pairs_text.split(';') if item.strip()]
                            
                            for pair_item in pair_items:
                                if ':' in pair_item:
                                    # Split only on the first colon to handle colons in Chinese text
                                    key_part, value_part = pair_item.split(':', 1)
                                    key = key_part.strip()
                                    value = value_part.strip()
                                    
                                    if key and value:
                                        pairs_list.append({
                                            'key': key,
                                            'value': value
                                        })
                            
                            if not question_text or not pairs_list:
                                flash(f'Row {index+2}: Missing data or invalid format in required columns for Word Matching. Skipping row.', 'danger')
                                continue
                            
                            # Store pairs as a proper JSON structure
                            pairs_data = {
                                'pairs': pairs_list,
                                'format': 'key_value_matching'
                            }
                            
                            conn.execute(
                                """
                                INSERT INTO quiz_questions (language, difficulty, question, options, answer, question_type, points)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (language, difficulty, question_text, json.dumps(pairs_data), '', 'word_matching', 10)
                            )
                            inserted += 1
                        except Exception as e:
                            flash(f'Error processing row {index+2} for Word Matching: {str(e)}', 'danger')
                            continue # Skip row with processing error
                elif question_type == 'Fill-in-the-blanks':
                    required_cols_lower = {'question', 'answer', 'hint'}
                    if not required_cols_lower.issubset(df.columns):
                        # More specific error message
                        missing = list(required_cols_lower - set(df.columns))
                        flash(f'Excel file missing required columns for Fill-in-the-blanks: {", ".join(missing)}.', 'danger')
                        return redirect(url_for('admin_dashboard'))
                    for index, row in df.iterrows(): # Use index to report row number
                        try:
                            question_text = str(row.get('question', '')).strip()
                            answer = str(row.get('answer', '')).strip()
                            hint = str(row.get('hint', '')).strip()

                            if not question_text or not answer:
                                flash(f'Row {index+2}: Missing data in required columns for Fill-in-the-blanks. Skipping row.', 'danger')
                                continue
                            conn.execute(
                                """
                                INSERT INTO quiz_questions (language, difficulty, question, options, answer, question_type, points)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (language, difficulty, question_text, json.dumps({'hint': hint}), answer, 'fill_in_the_blanks', 10) # Assuming 10 points for beginner FIB
                            )
                            inserted += 1
                        except Exception as e:
                            flash(f'Error processing row {index+2} for Fill-in-the-blanks: {str(e)}', 'danger')
                            continue # Skip row with processing error
            elif difficulty == 'Intermediate':
                if question_type == 'Phrase Completion':
                    required_cols_lower = {'phrase start', 'options', 'correct completion'}
                    if not required_cols_lower.issubset(df.columns):
                         # More specific error message
                        missing = list(required_cols_lower - set(df.columns))
                        flash(f'Excel file missing required columns for Phrase Completion: {", ".join(missing)}.', 'danger')
                        return redirect(url_for('admin_dashboard'))
                    for index, row in df.iterrows(): # Use index to report row number
                        try:
                            question_text = str(row.get('phrase start', '')).strip()
                            
                            options_raw = row.get('options', '')
                            # Ensure options_raw is treated as string before splitting
                            options_text = str(options_raw).split(';') if options_raw is not None else []
                            options = [opt.strip() for opt in options_text if opt.strip()]
                            
                            answer = str(row.get('correct completion', '')).strip()
                            if not question_text or not options or not answer:
                                flash(f'Row {index+2}: Missing data in required columns for Phrase Completion. Skipping row.', 'danger')
                                continue
                            conn.execute(
                                """
                                INSERT INTO quiz_questions (language, difficulty, question, options, answer, question_type, points)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (language, difficulty, question_text, json.dumps(options), answer, 'phrase_completion', 10) # Assuming 10 points
                            )
                            inserted += 1
                        except Exception as e:
                             flash(f'Error processing row {index+2} for Phrase Completion: {str(e)}', 'danger')
                             continue # Skip row with processing error
                elif question_type == 'Error Spotting':
                    required_cols_lower = {'question', 'options', 'correct answer', 'explanation'}
                    if not required_cols_lower.issubset(df.columns):
                        # More specific error message
                        missing = list(required_cols_lower - set(df.columns))
                        flash(f'Excel file missing required columns for Error Spotting: {", ".join(missing)}.', 'danger')
                        return redirect(url_for('admin_dashboard'))
                    for index, row in df.iterrows(): # Use index to report row number
                        try:
                            question_text = str(row.get('question', '')).strip()

                            options_raw = row.get('options', '')
                            # Ensure options_raw is treated as string before splitting
                            options_text = str(options_raw).split(';') if options_raw is not None else []
                            options = [opt.strip() for opt in options_text if opt.strip()]

                            answer = str(row.get('correct answer', '')).strip()

                            explanation_raw = row.get('explanation', '')
                             # Ensure explanation_raw is treated as string
                            explanation = str(explanation_raw).strip() if explanation_raw is not None else ''

                            if not question_text or not options or not answer or not explanation:
                                flash(f'Row {index+2}: Missing data in required columns for Error Spotting. Skipping row.', 'danger')
                                continue
                            conn.execute("INSERT INTO quiz_questions (language, difficulty, question, options, answer, question_type, points) VALUES (?, ?, ?, ?, ?, ?, ?)", (language, difficulty, question_text, json.dumps({'options': options, 'explanation': explanation}), answer, 'error_spotting', 10))
                            inserted += 1
                        except Exception as e:
                             flash(f'Error processing row {index+2} for Error Spotting: {str(e)}', 'danger')
                             continue # Skip row with processing error
                elif question_type == 'Context Response':
                    required_cols_lower = {'question', 'options', 'correct answer'}
                    if not required_cols_lower.issubset(df.columns):
                         # More specific error message
                        missing = list(required_cols_lower - set(df.columns))
                        flash(f'Excel file missing required columns for Context Response: {", ".join(missing)}.', 'danger')
                        return redirect(url_for('admin_dashboard'))
                    for index, row in df.iterrows(): # Use index to report row number
                        try:
                            question_text = str(row.get('question', '')).strip()
                            
                            options_raw = row.get('options', '')
                             # Ensure options_raw is treated as string before splitting
                            options_text = str(options_raw).split(';') if options_raw is not None else []
                            options = [opt.strip() for opt in options_text if opt.strip()]

                            answer = str(row.get('correct answer', '')).strip()

                            if not question_text or not options or not answer:
                                flash(f'Row {index+2}: Missing data in required columns for Context Response. Skipping row.', 'danger')
                                continue
                            conn.execute(
                                """
                                INSERT INTO quiz_questions (language, difficulty, question, options, answer, question_type, points)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (language, difficulty, question_text, json.dumps(options), answer, 'context_response', 10)
                            )
                            inserted += 1
                        except Exception as e:
                            flash(f'Error processing row {index+2} for Context Response: {str(e)}', 'danger')
                            continue # Skip row with processing error
            elif difficulty == 'Advanced':
                if question_type == 'Idiom interpretation':
                    # Check for required columns with flexible options/explanation naming
                    has_question_col = 'question' in df.columns
                    has_answer_col = 'correct answer' in df.columns
                    has_options_col = 'option' in df.columns or 'options' in df.columns
                    has_explanation_col = 'explanation' in df.columns or 'explanations' in df.columns

                    if not (has_question_col and has_answer_col and has_options_col and has_explanation_col):
                        # More specific error message
                        missing = []
                        if not has_question_col: missing.append('question')
                        if not has_answer_col: missing.append('correct answer')
                        if not has_options_col: missing.append('option/options')
                        if not has_explanation_col: missing.append('explanation/explanations')
                        flash(f'Excel file missing required columns for Idiom interpretation: {", ".join(missing)}.', 'danger')
                        return redirect(url_for('admin_dashboard'))
                    for index, row in df.iterrows(): # Use index to report row number
                        try:
                            # Access data using the lowercased column names from the DataFrame
                            question_text = str(row.get('question', '')).strip()

                            # Determine the actual column names used in the file for options and explanation
                            options_col_used = 'options' if 'options' in df.columns else 'option'
                            explanation_col_used = 'explanation' if 'explanation' in df.columns else 'explanations'

                            options_raw = row.get(options_col_used, '')
                            # Ensure options_raw is treated as string before splitting
                            options_text = str(options_raw).split(';') if options_raw is not None else []
                            options = [opt.strip() for opt in options_text if opt.strip()]

                            answer = str(row.get('correct answer', '')).strip()
                            explanation_raw = row.get(explanation_col_used, '')
                             # Ensure explanation_raw is treated as string
                            explanation = str(explanation_raw).strip() if explanation_raw is not None else ''

                            if not question_text or not options or not answer or not explanation:
                                flash(f'Row {index+2}: Missing data in required columns for Idiom interpretation. Skipping row.', 'danger')
                                continue
                            conn.execute("INSERT INTO quiz_questions (language, difficulty, question, options, answer, question_type, points) VALUES (?, ?, ?, ?, ?, ?, ?)", (language, difficulty, question_text, json.dumps({'options': options, 'explanation': explanation}), answer, 'idiom_interpretation', 15))
                            inserted += 1
                        except Exception as e:
                            flash(f'Error processing row {index+2} for Idiom interpretation: {str(e)}', 'danger')
                            # Optionally log the full traceback server-side
                            continue # Skip row with processing error
                elif question_type == 'Cultural nuances':
                    # Check for required columns with flexible options/explanation naming
                    has_question_col = 'question' in df.columns
                    has_answer_col = 'correct answer' in df.columns
                    has_options_col = 'option' in df.columns or 'options' in df.columns
                    has_explanation_col = 'explanation' in df.columns or 'explanations' in df.columns
                    if not (has_question_col and has_answer_col and has_options_col and has_explanation_col):
                        # More specific error message
                        missing = []
                        if not has_question_col: missing.append('question')
                        if not has_answer_col: missing.append('correct answer')
                        if not has_options_col: missing.append('option/options')
                        if not has_explanation_col: missing.append('explanation/explanations')
                        flash(f'Excel file missing required columns for Cultural nuances: {", ".join(missing)}.', 'danger')
                        return redirect(url_for('admin_dashboard'))
                    for index, row in df.iterrows(): # Use index to report row number
                        try:
                            # Access data using the lowercased column names from the DataFrame
                            question_text = str(row.get('question', '')).strip()

                            # Determine the actual column names used in the file for options and explanation
                            options_col_used = 'options' if 'options' in df.columns else 'option'
                            explanation_col_used = 'explanation' if 'explanation' in df.columns else 'explanations'

                            options_raw = row.get(options_col_used, '')
                            # Ensure options_raw is treated as string before splitting
                            options_text = str(options_raw).split(';') if options_raw is not None else []
                            options = [opt.strip() for opt in options_text if opt.strip()]

                            answer = str(row.get('correct answer', '')).strip()
                            explanation_raw = row.get(explanation_col_used, '')
                            # Ensure explanation_raw is treated as string
                            explanation = str(explanation_raw).strip() if explanation_raw is not None else ''

                            if not question_text or not options or not answer or not explanation:
                                flash(f'Row {index+2}: Missing data in required columns for Cultural nuances. Skipping row.', 'danger')
                                continue
                            conn.execute("INSERT INTO quiz_questions (language, difficulty, question, options, answer, question_type, points) VALUES (?, ?, ?, ?, ?, ?, ?)", (language, difficulty, question_text, json.dumps({'options': options, 'explanation': explanation}), answer, 'cultural_nuances', 15))
                            inserted += 1
                        except Exception as e:
                            flash(f'Error processing row {index+2} for Cultural nuances: {str(e)}', 'danger')
                            # Optionally log the full traceback server-side
                            continue # Skip row with processing error
                elif question_type == 'Complex rephrasing':
                    # Check for required columns with flexible options naming
                    has_question_col = 'question' in df.columns
                    has_answer_col = 'correct answer' in df.columns
                    has_options_col = 'option' in df.columns or 'options' in df.columns
                    if not (has_question_col and has_answer_col and has_options_col):
                         # More specific error message
                        missing = []
                        if not has_question_col: missing.append('question')
                        if not has_answer_col: missing.append('correct answer')
                        if not has_options_col: missing.append('option/options')
                        flash(f'Excel file missing required columns for Complex rephrasing: {", ".join(missing)}.', 'danger')
                        return redirect(url_for('admin_dashboard'))
                    for index, row in df.iterrows(): # Use index to report row number
                        try:
                            # Access data using the lowercased column names from the DataFrame
                            question_text = str(row.get('question', '')).strip()

                            # Determine the actual column name used in the file for options
                            options_col_used = 'options' if 'options' in df.columns else 'option'

                            options_raw = row.get(options_col_used, '')
                            # Ensure options_raw is treated as string before splitting
                            options_text = str(options_raw).split(';') if options_raw is not None else []
                            options = [opt.strip() for opt in options_text if opt.strip()]

                            answer = str(row.get('correct answer', '')).strip()

                            if not question_text or not options or not answer:
                                flash(f'Row {index+2}: Missing data in required columns for Complex rephrasing. Skipping row.', 'danger')
                                continue
                            conn.execute("INSERT INTO quiz_questions (language, difficulty, question, options, answer, question_type, points) VALUES (?, ?, ?, ?, ?, ?, ?)", (language, difficulty, question_text, json.dumps(options), answer, 'complex_rephrasing', 15))
                            inserted += 1
                        except Exception as e:
                            flash(f'Error processing row {index+2} for Complex rephrasing: {str(e)}', 'danger')
                            # Optionally log the full traceback server-side
                            continue # Skip row with processing error
            conn.commit()
        flash(f'{inserted} questions imported successfully!', 'success')
    except Exception as e:
        flash(f'Error importing questions: {str(e)}', 'danger')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/download-template/<difficulty>')
@admin_required
def download_excel_template(difficulty):
    output = io.BytesIO()
    qtype = request.args.get('question_type', 'Multiple Choice')
    
    if difficulty == 'Beginner':
        if qtype == 'Multiple Choice':
            df = pd.DataFrame({'Question': ['Which of these is a fruit?'], 'Options': ['Apple; Bread; Carrot; Banana'], 'Correct Answer': ['Apple']})
        elif qtype == 'Word Matching':
            df = pd.DataFrame({'Question': ['Match the animals to their young'], 'Pairs': ['Dog:Puppy; Cat:Kitten; Cow:Calf; Horse:Foal']})
        elif qtype == 'Fill-in-the-blanks':
            df = pd.DataFrame({'Question': ['The sky is ___.'], 'Answer': ['blue'], 'Hint': ['Color of clear daytime sky']})
    elif difficulty == 'Intermediate':
        if qtype == 'Phrase Completion':
            df = pd.DataFrame({
                'Phrase Start': ['Complete the phrase: "The early bird catches the ________"'],
                'Options': ['worm; fish; bird'],
                'Correct Completion': ['worm']
            })
        elif qtype == 'Error Spotting':
            df = pd.DataFrame({
                'Question': ['Identify the error in the sentence: "Neither of the students have completed their assignments."'],
                'Options': ['had; have; has'],
                'Correct Answer': ['have'],
                'Explanation': ['"Neither" is a singular subject, so it requires a singular verb "has" instead of "have".']
            })
        elif qtype == 'Context Response':
            df = pd.DataFrame({
                'Question': ['What is the most appropriate response when someone asks "How are you today?"'],
                'Options': ['Not bad; Could be better; I\'m fine, thank you'],
                'Correct Answer': ['I\'m fine, thank you']
            })
        else:
            df = pd.DataFrame()
    elif difficulty == 'Advanced':
        if qtype == 'Idiom interpretation':
            df = pd.DataFrame({'Question': ['Example idiom question?'], 'Options': ['Option 1; Option 2; Option 3'], 'Correct Answer': ['Option 1'], 'Explanation': ['Explanation here.']})
        elif qtype == 'Cultural nuances':
            df = pd.DataFrame({'Question': ['Example cultural nuance question?'], 'Options': ['Option A; Option B; Option C'], 'Correct Answer': ['Option A'], 'Explanation': ['Explanation here.']})
        elif qtype == 'Complex rephrasing':
            df = pd.DataFrame({'Question': ['Original sentence to rephrase.'], 'Options': ['Rephrased 1; Rephrased 2; Rephrased 3'], 'Correct Answer': ['Rephrased 1']})
        else:
            df = pd.DataFrame()
    
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, download_name=f'{difficulty}_{qtype}_template.xlsx', as_attachment=True)

@app.route('/admin/delete_questions', methods=['POST'])
@admin_required
def admin_delete_questions():
    data = request.get_json()
    ids = data.get('ids')
    if not ids or not isinstance(ids, list):
        return jsonify({'success': False, 'message': 'No question IDs provided'}), 400
    with get_db_connection() as conn:
        conn.executemany("DELETE FROM quiz_questions WHERE id = ?", [(qid,) for qid in ids])
        conn.commit()
    return jsonify({'success': True})

@app.route('/translate', methods=['POST'])
def translate():
    data = request.get_json()
    text = data.get('text', '')
    if not text:
        return jsonify({'translation': ''})
    # Use Gemini or a placeholder for translation
    try:
        # You can replace this with a real translation API or Gemini prompt
        prompt = f"Translate this to English: {text}"
        response = model.generate_content(prompt)
        translation = response.text.strip() if hasattr(response, 'text') else str(response)
    except Exception:
        translation = 'Translation unavailable.'
    return jsonify({'translation': translation})

@app.route('/admin/edit_question', methods=['POST'])
@admin_required
def admin_edit_question():
    """Handles saving edits to a quiz question from the admin modal."""
    data = request.get_json()
    question_id = data.get('id')
    question_type = data.get('question_type')
    question_text = data.get('question')
    options_data = data.get('options') # This could be a list or a dict depending on type
    correct_answer = data.get('answer')

    # Basic validation
    if not question_id or not question_type or not question_text:
        return jsonify({'success': False, 'message': 'Missing required fields: id, type, or question.'}), 400

    # Handle specific validation based on question type if needed (e.g., correct_answer for non-matching)
    if question_type != 'word_matching' and correct_answer is None:
         return jsonify({'success': False, 'message': 'Missing correct answer.'}), 400

    # Format options data to JSON string for database storage
    # The frontend JS already formats this into the correct list/dict structure
    # Just need to convert to JSON string
    options_json_string = json.dumps(options_data) if options_data is not None else None

    try:
        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE quiz_questions
                SET question = ?, options = ?, answer = ?
                WHERE id = ?
                """,
                (question_text, options_json_string, correct_answer, question_id)
            )
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error saving question edit: {e}")
        # Log traceback for more detailed error info
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'}), 500

# --- Study List Endpoints ---
@app.route('/study_list')
def get_study_list():
    if 'user_id' not in session:
        return jsonify([])
    with get_db_connection() as conn:
        rows = conn.execute('SELECT word, added_at, note, language FROM study_list WHERE user_id = ? ORDER BY added_at DESC', (session['user_id'],)).fetchall()
    # Always return note and language, even if None
    return jsonify([{'word': row['word'], 'added_at': row['added_at'], 'note': row['note'] if row['note'] is not None else '', 'language': row['language'] if row['language'] is not None else 'english'} for row in rows])

@app.route('/add_to_study_list', methods=['POST'])
def add_to_study_list():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 403
    data = request.get_json()
    words = data.get('words')
    language = data.get('language', 'english').strip()
    if words and isinstance(words, list):
        added = 0
        with get_db_connection() as conn:
            for word in words:
                word = word.strip()
                if not word:
                    continue
                try:
                    conn.execute('INSERT OR IGNORE INTO study_list (user_id, word, language, added_at, note) VALUES (?, ?, ?, CURRENT_TIMESTAMP, NULL)', (session['user_id'], word, language))
                    added += 1
                except Exception as e:
                    print(f"Error adding word '{word}' to study list: {e}")
            conn.commit()
        return jsonify({'status': 'success', 'added': added})
    # Fallback to single word
    word = data.get('word', '').strip()
    if not word:
        return jsonify({'status': 'error', 'message': 'No word provided'}), 400
    try:
        with get_db_connection() as conn:
            conn.execute('INSERT OR IGNORE INTO study_list (user_id, word, language, added_at, note) VALUES (?, ?, ?, CURRENT_TIMESTAMP, NULL)', (session['user_id'], word, language))
            conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        print(f"Error in add_to_study_list: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/save_study_note', methods=['POST'])
def save_study_note():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 403
    data = request.get_json()
    word = data.get('word', '').strip()
    note = data.get('note', '').strip()
    if not word:
        return jsonify({'status': 'error', 'message': 'No word provided'}), 400
    with get_db_connection() as conn:
        conn.execute('UPDATE study_list SET note = ? WHERE user_id = ? AND word = ?', (note, session['user_id'], word))
        conn.commit()
    return jsonify({'status': 'success'})

@app.route('/remove_from_study_list', methods=['POST'])
def remove_from_study_list():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 403
    data = request.get_json()
    word = data.get('word', '').strip()
    if not word:
        return jsonify({'status': 'error', 'message': 'No word provided'}), 400
    try:
        with get_db_connection() as conn:
            conn.execute('DELETE FROM study_list WHERE user_id = ? AND word = ?', (session['user_id'], word))
            conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        print(f"Error in remove_from_study_list: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- Progress Stats Endpoint ---
@app.route('/get_progress_stats')
def get_progress_stats():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    try:
        with get_db_connection() as conn:
            # Get or create progress record
            progress = conn.execute('SELECT * FROM user_progress WHERE user_id = ?',
                (session['user_id'],)
            ).fetchone()

            if not progress:
                # Initialize progress for new users
                conn.execute(
                    'INSERT INTO user_progress (user_id) VALUES (?)',
                    (session['user_id'],)
                )
                conn.commit()
                progress = conn.execute(
                    'SELECT * FROM user_progress WHERE user_id = ?',
                    (session['user_id'],)
                ).fetchone()

            # Update daily streak
            today = datetime.now().date()
            last_activity = datetime.strptime(progress['last_activity_date'], '%Y-%m-%d').date() if progress['last_activity_date'] else None
            
            if last_activity:
                days_diff = (today - last_activity).days
                if days_diff == 1:  # Consecutive day
                    new_streak = progress['daily_streak'] + 1
                elif days_diff == 0:  # Same day
                    new_streak = progress['daily_streak']
                else:  # Streak broken
                    new_streak = 1
            else:
                new_streak = 1

            # Calculate additional stats
            words_learned = conn.execute(
                'SELECT COUNT(*) as count FROM study_list WHERE user_id = ?',
                (session['user_id'],)
            ).fetchone()['count']

            conversation_count = conn.execute(
                'SELECT COUNT(DISTINCT session_id) as count FROM chat_sessions WHERE user_id = ?',
                (session['user_id'],)
            ).fetchone()['count']

            # Calculate accuracy rate from quiz results
            quiz_stats = conn.execute('''
                SELECT 
                    SUM(score) as total_score,
                    SUM(total) as total_questions
                FROM quiz_results 
                WHERE user_id = ?
            ''', (session['user_id'],)).fetchone()

            accuracy_rate = 0
            if quiz_stats['total_questions'] and quiz_stats['total_questions'] > 0:
                accuracy_rate = (quiz_stats['total_score'] / quiz_stats['total_questions']) * 100

            # Calculate overall progress percentage (weighted average)
            progress_percentage = min(100, (
                (words_learned * 0.4) +  # 40% weight for words learned
                (conversation_count * 0.3) +  # 30% weight for conversations
                (accuracy_rate * 0.3)  # 30% weight for accuracy
            ))

            # Check for new achievements
            achievements = []
            if words_learned >= 10 and not conn.execute(
                'SELECT 1 FROM achievements WHERE user_id = ? AND achievement_type = ?',
                (session['user_id'], 'words_10')
            ).fetchone():
                achievements.append({
                    'type': 'words_10',
                    'name': 'Word Collector',
                    'description': 'Learned 10 words'
                })
                conn.execute(
                    'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                    (session['user_id'], 'words_10', 'Word Collector', 'Learned 10 words')
                )

            if new_streak >= 7 and not conn.execute(
                'SELECT 1 FROM achievements WHERE user_id = ? AND achievement_type = ?',
                (session['user_id'], 'streak_7')
            ).fetchone():
                achievements.append({
                    'type': 'streak_7',
                    'name': 'Consistent Learner',
                    'description': '7-day learning streak'
                })
                conn.execute(
                    'INSERT INTO achievements (user_id, achievement_type, achievement_name, description) VALUES (?, ?, ?, ?)',
                    (session['user_id'], 'streak_7', 'Consistent Learner', '7-day learning streak')
                )

            # Update progress record
            conn.execute('''
                UPDATE user_progress 
                SET words_learned = ?,
                    conversation_count = ?,
                    accuracy_rate = ?,
                    daily_streak = ?,
                    last_activity_date = ?,
                    last_updated = CURRENT_TIMESTAMP
                WHERE user_id = ?
            ''', (words_learned, conversation_count, accuracy_rate, new_streak, today, session['user_id']))
            conn.commit()

            # Get all achievements
            user_achievements = conn.execute('''
                SELECT achievement_name, description, earned_at
                FROM achievements
                WHERE user_id = ?
                ORDER BY earned_at DESC
            ''', (session['user_id'],)).fetchall()

            return jsonify({
                'words_learned': words_learned,
                'conversation_count': conversation_count,
                'accuracy_rate': round(accuracy_rate, 1),
                'progress_percentage': round(progress_percentage, 1),
                'daily_streak': new_streak,
                'new_achievements': achievements,
                'achievements': [dict(achievement) for achievement in user_achievements]
            })

    except Exception as e:
        print(f"Error getting progress stats: {e}")
        return jsonify({'error': 'Error calculating progress'}), 500

# --- Pronunciation Check Endpoint ---
@app.route('/check_pronunciation', methods=['POST'])
def check_pronunciation():
    data = request.get_json()
    expected = data.get('expected', '').strip()
    spoken = data.get('spoken', '').strip()
    language = data.get('language', 'english')
    if not expected or not spoken:
        return jsonify({'similarity': 0, 'feedback': 'Missing input.'}), 400
    # Compute similarity (case-insensitive, ignore punctuation)
    def normalize(s):
        import re
        return re.sub(r'[^\w\s]', '', s).lower()
    expected_norm = normalize(expected)
    spoken_norm = normalize(spoken)
    similarity = difflib.SequenceMatcher(None, expected_norm, spoken_norm).ratio()
    similarity_pct = int(round(similarity * 100))
    # Feedback message
    if similarity_pct > 90:
        feedback = '✅ Excellent! Your pronunciation is very close.'
    elif similarity_pct > 75:
        feedback = '👍 Good! Minor differences detected.'
    elif similarity_pct > 50:
        feedback = '🙂 Not bad, but try again for better accuracy.'
    else:
        feedback = '❌ Quite different. Listen and try to match the phrase.'
    return jsonify({'similarity': similarity_pct, 'feedback': feedback})

# --- Flashcards Endpoints ---
@app.route('/flashcards')
def flashcards():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('flashcards.html', username=session['username'])

@app.route('/get_flashcards/<language>')
def get_flashcards(language):
    if 'username' not in session:
        return jsonify({'error': 'Please log in to access flashcards'}), 401
    
    # Map frontend language value to database value
    language_map = {
        'english': 'English',
        'spanish': 'Spanish',
        'french': 'French',
        'chinese': 'Chinese',
        'tamil': 'Tamil',
        'malay': 'Malay',
        'portuguese': 'Portuguese',
    }
    db_language = language_map.get(language.lower(), language)
    flashcards = []
    try:
        with get_db_connection() as conn:
            # Strictly select 50 random questions for the selected language only
            questions = conn.execute("""
                SELECT question, options, answer, question_type, points 
                FROM quiz_questions 
                WHERE language = ? 
                ORDER BY RANDOM() 
                LIMIT 50
            """, (db_language,)).fetchall()
            
            for q in questions:
                question_text = q['question']
                answer = q['answer']
                
                # Parse options from JSON or handle different formats
                options = []
                question_type = q['question_type'] if 'question_type' in q.keys() else 'multiple_choice'
                
                try:
                    if q['options']:
                        options_data = json.loads(q['options'])
                        
                        # Handle Word Matching questions
                        if question_type == 'word_matching':
                            if isinstance(options_data, dict) and 'pairs' in options_data:
                                pairs = options_data['pairs']
                                options = []
                                correct_pairs = []
                                for pair in pairs:
                                    if isinstance(pair, dict) and 'key' in pair and 'value' in pair:
                                        options.extend([pair['key'], pair['value']])
                                        correct_pairs.append(f"{pair['key']} : {pair['value']}")
                                answer = ' | '.join(correct_pairs)
                            elif isinstance(options_data, list):
                                options = []
                                correct_pairs = []
                                for pair in options_data:
                                    if isinstance(pair, list) and len(pair) >= 2:
                                        options.extend([pair[0], pair[1]])
                                        correct_pairs.append(f"{pair[0]} : {pair[1]}")
                                answer = ' | '.join(correct_pairs)
                            else:
                                options = list(options_data.values()) if options_data else []
                        elif isinstance(options_data, dict):
                            if 'options' in options_data:
                                options = options_data['options']
                            elif isinstance(options_data, dict) and len(options_data) > 0:
                                options = list(options_data.values())
                        elif isinstance(options_data, list):
                            options = options_data
                        else:
                            options = str(q['options']).split(';')
                except (json.JSONDecodeError, TypeError):
                    if q['options']:
                        options = [opt.strip() for opt in str(q['options']).split(';') if opt.strip()]
                
                # Only include questions that have valid data
                if question_text and answer and options:
                    flashcards.append({
                        'question': question_text,
                        'options': [opt.strip() for opt in options if opt.strip()],
                        'answer': answer.strip(),
                        'type': q['question_type'] if 'question_type' in q.keys() else 'multiple_choice',
                        'points': q['points'] if 'points' in q.keys() else 10
                    })
                    
    except Exception as e:
        print(f'Error reading flashcards for {language} from database: {e}')
        return jsonify({'error': 'Error loading flashcards'}), 500
    
    return jsonify(flashcards)

# --- Account Management Endpoints ---
@app.route('/deactivate_account', methods=['POST'])
def deactivate_account():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Get the deactivation reason from the form
    deactivation_reason = request.form.get('deactivation_reason', '').strip()
    
    if not deactivation_reason:
        flash('Please provide a reason for deactivation.', 'error')
        return redirect(url_for('settings'))
    
    try:
        with get_db_connection() as conn:
            # Deactivate the account
            conn.execute(
                "UPDATE users SET is_active = 0 WHERE id = ?",
                (session['user_id'],)
            )
            
            # Store the deactivation reason in account_activity table
            conn.execute(
                "INSERT INTO account_activity (user_id, activity_type, details) VALUES (?, ?, ?)",
                (session['user_id'], 'deactivation', deactivation_reason)
            )
            
            conn.commit()
            
            # Add notification
            add_notification(
                session['user_id'],
                "Your account has been deactivated. You can reactivate it at any time."
            )
            
            flash('Your account has been deactivated. You have been logged out.', 'info')
            session.clear()
            return redirect(url_for('login'))
            
    except Exception as e:
        flash(f'Error deactivating account: {str(e)}', 'error')
        return redirect(url_for('settings'))

@app.route('/reactivate_account', methods=['GET', 'POST'])
def reactivate_account():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        security_answer = request.form.get('security_answer')
        reason = request.form.get('reason')
        if not all([email, password, security_answer, reason]):
            flash('All fields are required', 'error')
            return redirect(url_for('reactivate_account'))
        try:
            with get_db_connection() as conn:
                user = conn.execute(
                    "SELECT * FROM users WHERE email = ? AND is_active = 0",
                    (email,)
                ).fetchone()
                if not user:
                    flash('No deactivated account found with this email', 'error')
                    return redirect(url_for('reactivate_account'))
                if not (check_password_hash(user['password'], password) and user['security_answer'] == security_answer):
                    flash('Invalid credentials', 'error')
                    return redirect(url_for('reactivate_account'))
                conn.execute(
                    "UPDATE users SET is_active = 1 WHERE email = ?",
                    (email,)
                )
                conn.commit()
                add_notification(
                    user['id'],
                    "Your account has been reactivated. Welcome back!"
                )
                conn.execute(
                    "INSERT INTO account_activity (user_id, activity_type, details) VALUES (?, ?, ?)",
                    (user['id'], 'reactivation', reason)
                )
                conn.commit()
                flash('Your account has been reactivated successfully!', 'success')
                return redirect(url_for('login'))
        except Exception as e:
            flash(f'Error reactivating account: {str(e)}', 'error')
            return redirect(url_for('reactivate_account'))
    return render_template('reactivate_account.html')

# --- BLIP Image Captioning Route ---
@app.route('/blip_caption', methods=['POST'])
def blip_caption():
    if 'username' not in session:
        return jsonify({'error': 'Please log in to use image captioning'}), 401
    if 'image' not in request.files:
        return jsonify({'error': 'No image file provided'}), 400
    image_file = request.files['image']
    try:
        from PIL import Image
        from transformers import BlipProcessor, BlipForConditionalGeneration
        import torch
        processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
        model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")
        raw_image = Image.open(image_file).convert('RGB')
        inputs = processor(raw_image, return_tensors="pt")
        out = model.generate(**inputs)
        caption = processor.decode(out[0], skip_special_tokens=True)
        return jsonify({'caption': caption})
    except Exception as e:
        print(f"BLIP image captioning error: {e}")
        return jsonify({'error': 'BLIP image captioning failed. Please ensure all dependencies are installed.'}), 500

@app.route('/close_account', methods=['POST'])
def close_account():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    close_reason = request.form.get('close_reason', '').strip()
    if not close_reason:
        flash('Please provide a reason for closing your account.', 'error')
        return redirect(url_for('settings'))
    user_id = session['user_id']
    try:
        with get_db_connection() as conn:
            # Log the closure reason before deleting
            conn.execute(
                "INSERT INTO account_activity (user_id, activity_type, details) VALUES (?, ?, ?)",
                (user_id, 'closure', close_reason)
            )
            # Delete all user-related data (order matters due to FKs)
            conn.execute("DELETE FROM chat_messages WHERE session_id IN (SELECT session_id FROM chat_sessions WHERE user_id = ?)", (user_id,))
            conn.execute("DELETE FROM chat_sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM achievements WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_progress WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM study_list WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM quiz_results WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM quiz_results_enhanced WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM account_activity WHERE user_id = ? AND activity_type != 'closure'", (user_id,))
            # Finally, delete the user
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
        session.clear()
        flash('Your account and all data have been permanently deleted.', 'success')
        return redirect(url_for('index'))
    except Exception as e:
        flash(f'Error closing account: {str(e)}', 'error')
        return redirect(url_for('settings'))

@app.route('/examples')
def examples():
    """Show example screenshots of the website"""
    return render_template('examples.html')

@app.route('/services')
def services():
    return render_template('services.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

if __name__ == '__main__':
    ensure_user_columns()
    # Use environment variable for debug mode
    debug_mode = os.environ.get('DEBUG', 'True').lower() == 'true'
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=debug_mode)