# -*- coding: utf-8 -*-
"""Тесты слоя хранения. Не требуют aiogram и сети."""
import os
import shutil
import sqlite3
import unittest
import uuid
from datetime import datetime, timedelta

import storage

TEST_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_test_tmp")


class StorageTest(unittest.TestCase):
    def setUp(self):
        os.makedirs(TEST_ROOT, exist_ok=True)
        self.tmp = os.path.join(TEST_ROOT, uuid.uuid4().hex)
        os.makedirs(self.tmp, exist_ok=True)
        storage.DB_NAME = os.path.join(self.tmp, "test.db")
        storage.init_db()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_slots_created(self):
        storage.ensure_slots_for_days(3)
        rows = storage.get_slots_for_date(datetime.now().date().isoformat())
        self.assertEqual(len(rows), storage.WORKDAY_END - storage.WORKDAY_START)

    def test_ensure_is_idempotent(self):
        storage.ensure_slots_for_days(3)
        storage.ensure_slots_for_days(3)
        rows = storage.get_slots_for_date(datetime.now().date().isoformat())
        self.assertEqual(len(rows), storage.WORKDAY_END - storage.WORKDAY_START)

    def test_book_and_double_book(self):
        storage.ensure_slots_for_days(1)
        today = datetime.now().date().isoformat()
        code = storage.book_slot(today, 10, "Иван", "89001234567")
        self.assertIsNotNone(code)
        self.assertEqual(len(code), 6)
        # повторная запись на тот же слот невозможна
        self.assertIsNone(storage.book_slot(today, 10, "Пётр", "89007654321"))
        # слот пропал из свободных
        self.assertNotIn(10, storage.get_free_slots_for_date(today))

    def test_cancel(self):
        storage.ensure_slots_for_days(1)
        today = datetime.now().date().isoformat()
        code = storage.book_slot(today, 11, "Иван", "89001234567")
        self.assertTrue(storage.cancel_booking(code))
        self.assertFalse(storage.cancel_booking(code))
        self.assertIn(11, storage.get_free_slots_for_date(today))

    def test_booking_code_is_unique(self):
        storage.ensure_slots_for_days(1)
        today = datetime.now().date().isoformat()
        codes = {storage.book_slot(today, h, "Клиент", "89001234567")
                 for h in range(9, 18)}
        self.assertEqual(len(codes), storage.WORKDAY_END - storage.WORKDAY_START)

    def test_upcoming_dates_skip_full_day(self):
        storage.ensure_slots_for_days(3)
        now = datetime.now()
        today = now.date().isoformat()
        for h in storage.get_free_slots_for_date(today):
            storage.book_slot(today, h, "Клиент", "89001234567")
        dates = storage.upcoming_free_dates(3, now)
        self.assertNotIn(today, dates)

    def test_clear_old_slots_keeps_bookings(self):
        past = (datetime.now().date() - timedelta(days=1)).isoformat()
        conn = sqlite3.connect(storage.DB_NAME)
        conn.execute(
            "INSERT INTO slots (date, hour, is_booked, booked_by, phone, booking_code) "
            "VALUES (?, 9, 1, 'Старый', '89001234567', 'ABC123')", (past,))
        conn.execute("INSERT INTO slots (date, hour, is_booked) VALUES (?, 10, 0)", (past,))
        conn.commit()
        conn.close()

        storage.clear_old_slots()
        left = storage.get_slots_for_date(past)
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0]["hour"], 9)


if __name__ == "__main__":
    unittest.main()


