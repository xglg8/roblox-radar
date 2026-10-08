import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import radar
import regional


def payload(country, games):
    return {"sortId": "top-trending", "appliedFilters": f"device=all,age=all,country={country}",
            "games": [{"rootPlaceId": gid, "name": f"Game {gid}", "playerCount": 1000000-gid} for gid in games]}


class RegionalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = radar.connect(Path(self.tmp.name) / "db.sqlite3")
        self.c = {"device": "all", "sort_id": "top-trending", "groups": {"panel": {"name": "Test", "countries": ["us", "ca"]}}}
        self.patcher = patch.object(regional, "settings", return_value=self.c)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop(); self.db.close(); self.tmp.cleanup()

    def collect(self, day, games, fail=False):
        rid = day
        self.db.execute("INSERT INTO runs(id,started_at,status) VALUES (?,?,'success')", (rid, day+"T02:00:00+00:00")); self.db.commit()
        class Http:
            def get(self, url):
                from urllib.parse import urlparse, parse_qs
                country = parse_qs(urlparse(url).query)["country"][0]
                if fail and country == "ca":
                    raise ValueError("network failure")
                return payload(country, games[country])
        return regional.collect(self.db, Http(), rid, day)

    def test_filter_must_be_confirmed(self):
        for country, device in (("jp", "all"), ("us", "computer")):
            with self.assertRaises(ValueError): regional.parse_sample(payload("us", [1]), country, device)
        d = payload("us", [1]); del d["appliedFilters"]
        with self.assertRaises(ValueError): regional.parse_sample(d, "us")

    def test_rank_order_not_global_ccu(self):
        rows = regional.parse_sample(payload("us", [99, 1]), "us")
        self.assertEqual([g["game_id"] for g in rows], ["99", "1"])
        self.assertNotIn("ccu", rows[0])

    def test_index_country_weight_and_missing_game(self):
        rows = regional.aggregate({"us": regional.parse_sample(payload("us", [1, 2]), "us"), "ca": regional.parse_sample(payload("ca", [2]), "ca")})
        self.assertEqual(rows[0]["game_id"], "2")
        self.assertEqual(rows[0]["score"], 99.5)
        self.assertEqual(rows[1]["score"], 50)

    def test_incomplete_panel_not_published(self):
        with self.assertLogs("radar.regional", level="WARNING"):
            errors = self.collect("2026-10-08", {"us": [1], "ca": [2]}, True)
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM region_snapshots").fetchone()[0], 0)
        self.assertEqual(len(regional.report(self.db, "2026-10-08")["missing_regions"]), 1)

    def test_daily_weekly_exact_cohort(self):
        self.collect("2026-10-01", {"us": [2, 1], "ca": [2, 1]})
        self.collect("2026-10-07", {"us": [2, 1], "ca": [2, 1]})
        self.collect("2026-10-08", {"us": [1, 2], "ca": [1, 2]})
        r = regional.report(self.db, "2026-10-08")["regions"][0]["rows"][0]
        for key in ("daily", "weekly"):
            self.assertEqual(r[key]["rank_change"], 1)
            self.assertEqual(r[key]["score_change"], 1)
        self.c["groups"]["panel"]["countries"] = ["us"]
        self.assertEqual(regional.report(self.db, "2026-10-08")["regions"], [])

    def test_missing_date_not_previous_available(self):
        self.collect("2026-10-06", {"us": [1], "ca": [1]})
        self.collect("2026-10-08", {"us": [1], "ca": [1]})
        r = regional.report(self.db, "2026-10-08")["regions"][0]["rows"][0]
        self.assertEqual(r["daily"]["state"], "no_baseline")

    def test_payload_schema_validation(self):
        for games in ([], [1, 1]):
            with self.assertRaises(ValueError): regional.parse_sample(payload("us", games), "us")


if __name__ == "__main__":
    unittest.main()
