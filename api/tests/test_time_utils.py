import unittest

from analytics_engine.time_utils import (
    date_key,
    local_iso,
    parse_instant,
    split_by_local_day,
    utc_iso,
)


class TimeUtilsTests(unittest.TestCase):
    def test_requires_an_absolute_timestamp(self):
        with self.assertRaisesRegex(ValueError, "explicit offset"):
            parse_instant("2026-01-01T12:00:00")

    def test_canonicalizes_equivalent_offsets_to_utc(self):
        self.assertEqual(utc_iso("2026-01-01T06:00:00-06:00"), "2026-01-01T12:00:00.000Z")
        self.assertEqual(date_key("2026-01-01T05:30:00Z", "America/Chicago"), "2025-12-31")

    def test_chicago_local_rendering_uses_the_date_specific_dst_offset(self):
        self.assertEqual(local_iso("2026-01-15T12:00:00Z", "America/Chicago"), "2026-01-15T06:00:00.000-06:00")
        self.assertEqual(local_iso("2026-07-15T12:00:00Z", "America/Chicago"), "2026-07-15T07:00:00.000-05:00")

    def test_local_day_splitting_preserves_23_and_25_hour_dst_days(self):
        spring = list(split_by_local_day(
            "2026-03-08T06:00:00Z", "2026-03-09T05:00:00Z", "America/Chicago"
        ))
        fall = list(split_by_local_day(
            "2026-11-01T05:00:00Z", "2026-11-02T06:00:00Z", "America/Chicago"
        ))
        self.assertEqual((spring[0][2] - spring[0][1]).total_seconds() / 3600, 23)
        self.assertEqual((fall[0][2] - fall[0][1]).total_seconds() / 3600, 25)
        self.assertEqual([item[0] for item in spring], ["2026-03-08"])
        self.assertEqual([item[0] for item in fall], ["2026-11-01"])


if __name__ == "__main__":
    unittest.main()
