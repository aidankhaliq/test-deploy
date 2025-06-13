#!/usr/bin/env python3
"""
Migration script to transfer data from SQLite to PostgreSQL.
Run this script after setting up your PostgreSQL database on Render.
"""

import os
import sys
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime

# Add the parent directory to the path so we can import app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import get_db_connection

def get_sqlite_connection():
    """Get a connection to the SQLite database."""
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def get_postgres_connection():
    """Get a connection to the PostgreSQL database."""
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL environment variable not set")
    
    # Convert postgres:// to postgresql:// for psycopg2
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    
    conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
    return conn

def create_tables(conn):
    """Create all necessary tables in PostgreSQL."""
    with conn.cursor() as cursor:
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                security_answer TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                reset_token TEXT,
                bio TEXT,
                urls TEXT,
                profile_picture TEXT,
                dark_mode INTEGER DEFAULT 0,
                name TEXT,
                phone TEXT,
                location TEXT,
                website TEXT,
                avatar TEXT,
                timezone TEXT,
                datetime_format TEXT,
                is_active INTEGER DEFAULT 1
            )
        ''')
        
        # Quiz results table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quiz_results_enhanced (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                language TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                score INTEGER NOT NULL,
                total INTEGER NOT NULL,
                percentage REAL NOT NULL,
                passed INTEGER DEFAULT 0,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                question_details TEXT NOT NULL,
                points_earned INTEGER DEFAULT 0,
                streak_bonus INTEGER DEFAULT 0,
                time_bonus INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Legacy quiz results table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quiz_results (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                language TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                score INTEGER NOT NULL,
                total INTEGER NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Notifications table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_read INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Achievements table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS achievements (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                achievement_type TEXT NOT NULL,
                achievement_name TEXT NOT NULL,
                description TEXT NOT NULL,
                earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # User progress table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_progress (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                words_learned INTEGER DEFAULT 0,
                conversation_count INTEGER DEFAULT 0,
                accuracy_rate REAL DEFAULT 0,
                daily_streak INTEGER DEFAULT 0,
                last_activity_date DATE,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Study list table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS study_list (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                note TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Chat sessions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                language TEXT NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Chat messages table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_messages (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                message TEXT NOT NULL,
                bot_response TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES chat_sessions (session_id)
            )
        ''')
        
        # Quiz questions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quiz_questions (
                id SERIAL PRIMARY KEY,
                language TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                question TEXT NOT NULL,
                options TEXT NOT NULL,
                answer TEXT NOT NULL,
                question_type TEXT DEFAULT 'multiple_choice',
                points INTEGER DEFAULT 10,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Password resets table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS password_resets (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token TEXT NOT NULL,
                expiry TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Account activity table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_activity (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                activity_type TEXT NOT NULL,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        conn.commit()
        print("✅ All tables created successfully!")

def migrate_data(sqlite_conn, pg_conn):
    """Migrate data from SQLite to PostgreSQL."""
    # Get all tables from SQLite
    cursor = sqlite_conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row['name'] for row in cursor.fetchall()]
    
    # Migrate each table
    for table in tables:
        print(f"Migrating table: {table}")
        
        # Skip sqlite_sequence table
        if table == 'sqlite_sequence':
            continue
        
        # Get all data from the table
        cursor.execute(f"SELECT * FROM {table}")
        rows = cursor.fetchall()
        
        if not rows:
            print(f"  No data in table {table}")
            continue
        
        # Get column names
        columns = [description[0] for description in cursor.description]
        
        # Insert data into PostgreSQL
        pg_cursor = pg_conn.cursor()
        
        for row in rows:
            # Convert row to dict
            row_dict = dict(row)
            
            # Handle special cases for each table
            if table == 'quiz_questions' and 'options' in row_dict:
                # Ensure options is a valid JSON string
                try:
                    if isinstance(row_dict['options'], str):
                        json.loads(row_dict['options'])
                except:
                    # If not valid JSON, convert to JSON string
                    row_dict['options'] = json.dumps(row_dict['options'])
            
            # Build the INSERT query
            placeholders = ', '.join(['%s'] * len(columns))
            columns_str = ', '.join(columns)
            query = f"INSERT INTO {table} ({columns_str}) VALUES ({placeholders})"
            
            # Get values in the correct order
            values = [row_dict[col] for col in columns]
            
            try:
                pg_cursor.execute(query, values)
            except Exception as e:
                print(f"  Error inserting row into {table}: {e}")
                print(f"  Row data: {row_dict}")
                # Continue with next row
                continue
        
        pg_conn.commit()
        print(f"  Migrated {len(rows)} rows from {table}")

def main():
    """Main function to run the migration."""
    print("Starting migration from SQLite to PostgreSQL...")
    
    # Check if DATABASE_URL is set
    if not os.environ.get('DATABASE_URL'):
        print("Error: DATABASE_URL environment variable not set")
        print("Please set the DATABASE_URL environment variable to your PostgreSQL connection string")
        sys.exit(1)
    
    # Connect to both databases
    try:
        sqlite_conn = get_sqlite_connection()
        pg_conn = get_postgres_connection()
        
        # Create tables in PostgreSQL
        create_tables(pg_conn)
        
        # Migrate data
        migrate_data(sqlite_conn, pg_conn)
        
        print("✅ Migration completed successfully!")
    except Exception as e:
        print(f"Error during migration: {e}")
    finally:
        # Close connections
        if 'sqlite_conn' in locals():
            sqlite_conn.close()
        if 'pg_conn' in locals():
            pg_conn.close()

if __name__ == "__main__":
    main() 