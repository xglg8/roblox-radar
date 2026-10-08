import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import genre_trends as genres
import radar


class GenreTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=radar.connect(Path(self.tmp.name)/'db.sqlite3');genres.initialize(self.db)

    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def snap(self,day,counts,cohort='same'):
        with self.db:
            sid=self.db.execute("INSERT INTO genre_snapshots(day,scope,name,cohort,source_ids,observed_at) VALUES (?,'all_regions','Test',?,'[]',?)",(day,cohort,day+'T02:00:00+00:00')).lastrowid
            for i,category in enumerate(counts):
                self.db.execute('INSERT INTO genre_entries VALUES (?,?,?,?,?,?)',(sid,str(i),str(i),category,'{}',1))
        return sid

    def test_fps_requires_explicit_official_evidence(self):
        self.assertEqual(genres.classify({'genre':'FPS','genre_l1':'Shooter','genre_l2':'Deathmatch Shooter'}),'fps')
        self.assertEqual(genres.classify({'genre_l1':'Shooter','name':'Best FPS Simulator'}),'shooter')
        self.assertEqual(genres.classify({'name':'FPS Simulator'}),'unknown')
        self.assertEqual(genres.classify({'genre_l1':'Simulation'}),'simulation')
        self.assertEqual(genres.classify({'genre_l1':'Survival','genre_l2':'Horror Survival'}),'horror')

    def test_unknown_in_denominator_and_percentage_points(self):
        self.snap('2026-10-01',['fps','unknown'])
        self.snap('2026-10-07',['fps','unknown'])
        self.snap('2026-10-08',['fps','fps','simulation','unknown'])
        group=genres.report(self.db,'2026-10-08')['groups'][0]
        self.assertEqual(group['total_games'],4)
        self.assertAlmostEqual(sum(g['share_pct'] for g in group['rows']),100)
        row=next(g for g in group['rows'] if g['category']=='fps')
        self.assertEqual(row['share_pct'],50)
        for period in ('daily','weekly'):
            self.assertEqual(row[period]['count_change'],1)
            self.assertEqual(row[period]['share_pp_change'],0)

    def test_exact_date_and_same_cohort(self):
        self.snap('2026-10-06',['fps'])
        self.snap('2026-10-07',['fps'],cohort='other')
        self.snap('2026-10-08',['fps'])
        row=genres.report(self.db,'2026-10-08')['groups'][0]['rows'][0]
        self.assertIsNone(row['daily']['count_change'])
        self.assertIsNone(row['weekly']['share_pp_change'])

    def test_missing_today_does_not_reuse_yesterday(self):
        self.snap('2026-10-07',['fps'])
        self.assertEqual(genres.report(self.db,'2026-10-08')['groups'],[])

    def test_seven_and_fifteen_day_changes_and_disappearing_category(self):
        self.snap('2026-09-23',['simulation','fps'])
        self.snap('2026-10-01',['simulation','simulation','fps','unknown'])
        self.snap('2026-10-08',['simulation','simulation','simulation','unknown'])
        group=genres.report(self.db,'2026-10-08')['groups'][0]
        row=next(g for g in group['rows'] if g['category']=='simulation')
        self.assertEqual(group['baselines']['half_monthly']['day'],'2026-09-23')
        self.assertEqual(row['weekly'],{'count_change':1,'share_pp_change':25})
        self.assertEqual(row['half_monthly'],{'count_change':2,'share_pp_change':25})
        message='\n'.join(genres.message_parts(self.db,'2026-10-08'))
        self.assertIn('15天 2026-09-23',message)
        self.assertIn('FPS：0款',message)
        self.assertIn('15天-1款/-50.00百分点',message)

    def test_fifteen_days_does_not_use_nearby_date_or_other_cohort(self):
        self.snap('2026-09-23',['fps'],cohort='other')
        self.snap('2026-09-24',['fps'])
        self.snap('2026-10-08',['simulation'])
        group=genres.report(self.db,'2026-10-08')['groups'][0]
        self.assertIsNone(group['baselines']['half_monthly'])
        for row in group['rows']:
            self.assertIsNone(row['half_monthly']['count_change'])
            self.assertIsNone(row['half_monthly']['share_pp_change'])

    def test_empty_categories_remain_available_as_baselines(self):
        self.snap('2026-10-07',['simulation'])
        self.snap('2026-10-08',['fps'])
        group=genres.report(self.db,'2026-10-08')['groups'][0]
        fps=next(g for g in group['rows'] if g['category']=='fps')
        self.assertEqual(fps['daily']['share_pp_change'],100)

    def test_collect_deduplicates_regions_and_preserves_missing_metadata(self):
        import regional
        regional.initialize(self.db)
        self.db.execute("INSERT INTO runs(id,started_at,status) VALUES ('r','2026-10-08','success')")
        raw=[{'game_id':'1','name':'One','universe_id':101},{'game_id':'2','name':'Two','universe_id':102}]
        self.db.execute('INSERT INTO region_samples VALUES (?,?,?,?,?,?)',('r','north','us','now','country=us',json.dumps(raw)))
        self.db.execute('INSERT INTO region_samples VALUES (?,?,?,?,?,?)',('r','south','br','now','country=br',json.dumps(raw[1:])))
        self.db.commit()
        panels={'missing_regions':[], 'regions':[
            {'snapshot':{'id':1,'region':'north','name':'North','run_id':'r','cohort':'n'},'rows':[dict(g,score=50) for g in raw]},
            {'snapshot':{'id':2,'region':'south','name':'South','run_id':'r','cohort':'s'},'rows':[dict(raw[1],score=60)]}]}
        class Http:
            def get(self,url):
                return {'data':[{'id':101,'genre_l1':'Simulation'}]}
        with patch.object(regional,'report',return_value=panels):
            data=genres.collect(self.db,Http(),'2026-10-08')
        group=next(g for g in data['groups'] if g['snapshot']['scope']=='all_regions')
        self.assertEqual(group['total_games'],2)
        self.assertEqual(next(g for g in group['rows'] if g['category']=='unknown')['count'],1)


if __name__=='__main__':unittest.main()
