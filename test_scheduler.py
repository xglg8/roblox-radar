import unittest
from datetime import datetime, timedelta, timezone

from scheduler import next_daily
from radar import hourly_config


class SchedulerTest(unittest.TestCase):
    def test_hourly_keeps_ccu_and_daily_enrichment(self):
        c = {'timezone_offset_hours': 8, 'top_n': 500, 'regional_enabled': True, 'genre_trends_enabled': True}
        for hour in range(24):
            result = hourly_config(c, datetime(2026, 10, 27, hour))
            self.assertEqual(result['top_n'], 500)
            self.assertEqual(result['regional_enabled'], hour == 10)
            self.assertEqual(result['genre_trends_enabled'], hour == 10)
        self.assertTrue(c['regional_enabled'])

    def test_before_daily_slot(self):
        current = datetime(2026, 9, 29, 8, 45, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(next_daily(current), current.replace(hour=10, minute=0))

    def test_at_slot_uses_next_day(self):
        current = datetime(2026, 9, 29, 10, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(next_daily(current), current + timedelta(days=1))

    def test_year_boundary(self):
        current = datetime(2026, 12, 31, 23, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(next_daily(current).isoformat(), "2027-01-01T10:00:00+08:00")
