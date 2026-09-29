import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import radar


def game(gid, ccu, name=None):
    return dict(game_id=str(gid), name=name or str(gid), ccu=ccu, url="https://www.roblox.com/games/" + str(gid))


class RadarTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = radar.connect(Path(self.temp.name) / "test.sqlite3")
        self.serial = 0

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def snap(self, day, rows, platform="roblox", source="rolimons", hour="09"):
        self.serial += 1
        rid = str(self.serial)
        at = day + "T" + hour + ":00:00+00:00"
        self.db.execute("INSERT INTO runs(id,started_at,status) VALUES (?,?,'success')", (rid, at))
        self.db.commit()
        return radar.save_snapshot(self.db, rid, platform, source, day, at, rows, len(rows), "test")

    def test_daily_weekly_and_zero_denominator(self):
        self.snap("2026-09-21", [game(1, 50), game(2, 100)])
        self.snap("2026-09-27", [game(1, 0), game(2, 80)])
        self.snap("2026-09-28", [game(1, 150), game(2, 70)])
        r = radar.ranking(self.db)["rows"][0]
        self.assertEqual(r["daily"]["rank_change"], 1)
        self.assertEqual(r["weekly"]["rank_change"], 1)
        self.assertEqual(r["daily"]["ccu_change"], 150)
        self.assertIsNone(r["daily"]["ccu_pct"])
        self.assertEqual(r["weekly"]["ccu_pct"], 200)

    def test_missing_exact_date_is_not_previous_available(self):
        self.snap("2026-09-26", [game(1, 100)])
        self.snap("2026-09-28", [game(1, 200)])
        self.assertEqual(radar.ranking(self.db)["rows"][0]["daily"]["state"], "no_baseline")

    def test_outside_sample_not_claimed_new_game(self):
        self.snap("2026-09-27", [game(2, 100)])
        self.snap("2026-09-28", [game(1, 200)])
        r = radar.ranking(self.db)["rows"][0]
        self.assertEqual(r["daily"]["state"], "outside_previous_sample")
        self.assertIsNone(r["daily"]["rank_change"])

    def test_same_day_keeps_both_and_uses_latest(self):
        self.snap("2026-09-28", [game(1, 100)])
        self.snap("2026-09-28", [game(1, 120)], hour="10")
        self.assertEqual(radar.ranking(self.db)["rows"][0]["ccu"], 120)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 2)

    def test_ties_deterministic(self):
        self.snap("2026-09-28", [game(10, 100), game(2, 100)])
        self.assertEqual([r["game_id"] for r in radar.ranking(self.db)["rows"]], ["2", "10"])

    def test_bad_batch_never_published(self):
        self.db.execute("INSERT INTO runs(id,started_at,status) VALUES ('bad','x','running')"); self.db.commit()
        for rows, limit in [([game(1, 1)], 500), ([game(1, 1), game(1, 2)], 2), ([game(1, -1)], 1)]:
            with self.assertRaises(ValueError):
                radar.save_snapshot(self.db, "bad", "roblox", "rolimons", "2026-09-28", "x", rows, limit, "test")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 0)

    def test_comparison_does_not_mix_days_or_ids(self):
        self.snap("2026-09-27", [game(1, 900)], "steam", "official")
        self.snap("2026-09-28", [game(1, 200)])
        self.snap("2026-09-28", [game(1, 180)], source="official")
        c = radar.comparison(self.db)
        self.assertFalse(any(p["platform"] == "steam" for p in c["platforms"]))
        self.assertEqual(c["source_checks"][0]["difference"], 20)

    def test_csv_unicode_and_formula_escaping(self):
        self.snap("2026-09-28", [game(1, 100, '=HYPERLINK("bad")'), game(2, 50, "中文游戏")])
        rows = list(csv.reader(io.StringIO(radar.csv_data(radar.ranking(self.db)).decode("utf-8-sig"))))
        self.assertTrue(rows[1][5].startswith("'="))
        self.assertEqual(rows[2][5], "中文游戏")

    def test_source_failure_retains_other_source(self):
        c = {"steam_enabled": False, "top_n": 1, "timezone_offset_hours": 8}
        with patch.object(radar, "rolimons", return_value=[game(1, 100)]), patch.object(radar, "official", side_effect=ValueError("upstream failed")):
            with self.assertLogs("radar", level="ERROR"):
                result = radar._collect(c, self.db)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(radar.ranking(self.db)["rows"]), 1)

    def test_steam_uses_current_not_peak(self):
        class FakeHttp:
            def get(self, url):
                if "GetMostPlayedGames" in url:
                    return {"response": {"ranks": [{"appid": 730, "rank": 1, "peak_in_game": 999}]}}
                if "GetNumberOfCurrentPlayers" in url:
                    return {"response": {"result": 1, "player_count": 123}}
                return {"730": {"data": {"name": "Example"}}}
        rows = radar.steam(FakeHttp(), self.db)
        self.assertEqual(rows[0]["ccu"], 123)
        self.assertEqual(rows[0]["extra"]["peak_in_game"], 999)

    def test_steam_partial_and_low_coverage(self):
        class FakeHttp:
            failures = {5}
            def get(self, url):
                if "GetMostPlayedGames" in url:
                    return {"response": {"ranks": [{"appid": i, "rank": i, "peak_in_game": 999} for i in range(1, 6)]}}
                gid = int(url.split("appid=")[-1])
                if gid in self.failures:
                    raise ValueError("not available")
                return {"response": {"result": 1, "player_count": 123}}
        for i in range(1, 6):
            self.db.execute("INSERT INTO cache VALUES (?,?)", (f"steam:{i}", f"Game {i}"))
        self.db.commit()
        http = FakeHttp()
        with self.assertLogs("radar", level="WARNING"):
            self.assertEqual(len(radar.steam(http, self.db)), 4)
        self.assertEqual(http.candidate_count, 5)
        self.assertEqual(len(http.issues), 1)
        http.failures = {4, 5}
        with self.assertLogs("radar", level="WARNING"), self.assertRaises(ValueError):
            radar.steam(http, self.db)


if __name__ == "__main__":
    unittest.main()
