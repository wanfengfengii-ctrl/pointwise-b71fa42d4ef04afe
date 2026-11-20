"""Persistence for V2 turnover takeovers and split-route page intake."""
import sqlite3


def initialize_turnover(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS turnovers (
            seal TEXT PRIMARY KEY NOT NULL,
            table_id TEXT NOT NULL,
            table_digest TEXT NOT NULL,
            control BLOB NOT NULL,
            receipt BLOB NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS turnover_batches (
            seal TEXT NOT NULL REFERENCES turnovers(seal),
            batch_id TEXT NOT NULL,
            table_id TEXT NOT NULL,
            PRIMARY KEY (seal, batch_id),
            UNIQUE (batch_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS turnover_pages (
            seal TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            page_id TEXT NOT NULL,
            byte_length INTEGER NOT NULL CHECK (byte_length > 0),
            body_digest TEXT NOT NULL,
            PRIMARY KEY (seal, batch_id, sequence),
            UNIQUE (seal, batch_id, page_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS turnover_codes (
            seal TEXT NOT NULL REFERENCES turnovers(seal),
            code TEXT NOT NULL,
            segment TEXT NOT NULL,
            PRIMARY KEY (seal, code)
        )
    ''')
    # 页在控制件到达之前先到：只核对自洽摘要，等待接管依据。
    db.execute('''
        CREATE TABLE IF NOT EXISTS staged_pages (
            batch_id TEXT NOT NULL,
            table_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            page_id TEXT NOT NULL,
            body BLOB NOT NULL,
            body_digest TEXT NOT NULL,
            PRIMARY KEY (batch_id, page_id),
            UNIQUE (batch_id, sequence)
        )
    ''')
    # 接管已提交、批尚未齐套：已核验身份与原字节的页。
    db.execute('''
        CREATE TABLE IF NOT EXISTS pending_pages (
            batch_id TEXT NOT NULL,
            table_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            page_id TEXT NOT NULL,
            body BLOB NOT NULL,
            body_digest TEXT NOT NULL,
            PRIMARY KEY (batch_id, page_id),
            UNIQUE (batch_id, sequence)
        )
    ''')
    # 两种入口共同的原件身份清册，支撑同件复领与异件拒绝。
    db.execute('''
        CREATE TABLE IF NOT EXISTS batch_directories (
            batch_id TEXT PRIMARY KEY NOT NULL,
            table_id TEXT NOT NULL,
            table_digest TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS directory_pages (
            batch_id TEXT NOT NULL REFERENCES batch_directories(batch_id),
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            page_id TEXT NOT NULL,
            byte_length INTEGER NOT NULL CHECK (byte_length > 0),
            body_digest TEXT NOT NULL,
            PRIMARY KEY (batch_id, sequence),
            UNIQUE (batch_id, page_id)
        )
    ''')
