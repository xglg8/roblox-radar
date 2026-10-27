import unittest
import radar
import test_radar
from test_radar import game


class GrowthTest(unittest.TestCase):
    setUp=test_radar.RadarTest.setUp
    tearDown=test_radar.RadarTest.tearDown
    snap=test_radar.RadarTest.snap

    def test_thresholds_zero_small_base_and_missing(self):
        self.snap('2026-10-27',[game(1,500),game(2,1000),game(3,1),game(4,0)],hour='08')
        self.snap('2026-10-27',[game(1,600),game(2,1500),game(3,10),game(4,500),game(5,2000)],hour='09')
        r=radar.ranking(self.db);by={g['game_id']:g['growth'] for g in r['rows']}
        self.assertEqual(by['1']['level'],'rising')
        self.assertEqual(by['2']['level'],'strong')
        for gid in ('3','4','5'):self.assertEqual(by[gid]['level'],'none')
        self.assertEqual(by['4']['periods']['hourly']['state'],'zero_baseline')
        self.assertEqual(by['5']['periods']['hourly']['state'],'outside_previous_sample')
        self.assertEqual(r['growth_summary']['flagged_count'],2)

    def test_24h_and_weekly_use_nearby_times_not_latest_of_date(self):
        self.snap('2026-10-20',[game(1,100)],hour='09')
        self.snap('2026-10-26',[game(1,200)],hour='09')
        self.snap('2026-10-26',[game(1,9000)],hour='23')
        self.snap('2026-10-27',[game(1,1000)],hour='09')
        g=radar.ranking(self.db)['rows'][0]['growth']
        self.assertEqual(g['periods']['daily']['ccu_change'],800)
        self.assertEqual(g['periods']['weekly']['ccu_change'],900)
        self.assertEqual(len(g['alerts']),2)

    def test_outside_time_tolerance_is_not_hourly_growth(self):
        self.snap('2026-10-27',[game(1,100)],hour='07')
        self.snap('2026-10-27',[game(1,1000)],hour='09')
        r=radar.ranking(self.db)
        self.assertEqual(r['rows'][0]['growth']['level'],'none')
        self.assertIsNone(r['growth_summary']['baselines']['hourly'])

    def test_decline_and_other_sources_are_not_flagged(self):
        self.snap('2026-10-27',[game(1,1000)],hour='08')
        self.snap('2026-10-27',[game(1,500)],hour='09')
        self.assertEqual(radar.ranking(self.db)['rows'][0]['growth']['level'],'none')
        self.snap('2026-10-27',[game(1,1000)],source='official')
        self.assertIsNone(radar.ranking(self.db,source='official')['growth_summary'])

    def test_daily_report_fallback_is_explicitly_labelled(self):
        self.snap('2026-10-26',[game(1,100)],hour='02')
        self.snap('2026-10-27',[game(1,600)],hour='06')
        r=radar.ranking(self.db)
        self.assertEqual(r['rows'][0]['growth']['alerts'][0]['label'],'较昨日日报')
        self.assertEqual(r['growth_summary']['baselines']['daily']['observed_at'],'2026-10-26T02:00:00+00:00')
        self.assertIsNone(r['growth_summary']['baselines']['hourly'])
        self.assertEqual(r['growth_summary']['assessed_count'],1)

    def test_daily_and_weekly_flags_are_independent(self):
        self.snap('2026-10-20',[game(1,2000),game(2,100)],hour='09')
        self.snap('2026-10-26',[game(1,100),game(2,2000)],hour='09')
        self.snap('2026-10-27',[game(1,1000),game(2,1000)],hour='09')
        rows={g['game_id']:g for g in radar.ranking(self.db)['rows']}
        self.assertEqual([a['period'] for a in rows['1']['growth']['alerts']],['daily'])
        self.assertEqual([a['period'] for a in rows['2']['growth']['alerts']],['weekly'])


if __name__=='__main__':unittest.main()
