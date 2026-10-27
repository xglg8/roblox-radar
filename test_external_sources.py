import json
import unittest
from unittest.mock import patch
import external_sources as sources
import test_radar
from test_radar import game


class ParseTest(unittest.TestCase):
    def test_identity_and_missing_are_not_zero(self):
        g={'universeId':11,'rootPlaceId':1,'name':'A','playing':0}
        rows=sources.parse('romanum',{'games':[g], 'fetchedAt':'2026-10-01T01:00:00Z'},{'11':'1'})
        self.assertEqual(rows[0]['ccu'],0)
        self.assertEqual(rows[0]['extra']['provider_observed_at'],'2026-10-01T01:00:00Z')
        g['playing']=None
        self.assertEqual(sources.parse('romanum',{'games':[g]},{'11':'1'}),[])
        g['rootPlaceId']=2
        with self.assertRaises(ValueError):sources.parse('romanum',{'games':[g]},{'11':'1'})

    def test_rowatcher_keeps_observations_not_revenue_models(self):
        g={'universeId':'11','rootPlaceId':'1','name':'A','playing':100,'estimatedDailyRevenueRobux':300,
           '_latestSnapshot':{'time':'2026-10-01T01:00:00Z'}, 'likes':'20'}
        rows=sources.parse('rowatcher',{'success':True,'data':[g]},{'11':'1'})
        self.assertEqual(rows[0]['extra']['likes'],'20')
        self.assertNotIn('estimatedDailyRevenueRobux',rows[0]['extra'])
        with self.assertRaises(ValueError):sources.parse('rowatcher',{'success':True,'data':[g,g]},{'11':'1'})


class ExternalTest(unittest.TestCase):
    setUp=test_radar.RadarTest.setUp
    tearDown=test_radar.RadarTest.tearDown
    snap=test_radar.RadarTest.snap

    def test_same_run_and_provider_time(self):
        import radar
        self.snap('2026-10-01',[game(1,100)])
        row=game(1,200);row['extra']={'provider_observed_at':'2026-10-01T01:00:00Z'}
        radar.save_snapshot(self.db,'1','roblox','rowatcher','2026-10-01','2026-10-01T09:01:00+00:00',[row],1,'test')
        c=radar.comparison(self.db)
        r=c['source_checks'][0]['external']['rowatcher']
        self.assertEqual(r['ccu'],200)
        self.assertEqual(r['status'],'time_mismatch')
        self.assertIsNone(r['difference'])
        self.snap('2026-10-01',[game(1,300)],hour='10')
        self.assertEqual(radar.comparison(self.db)['source_checks'][0]['external']['rowatcher']['status'],'missing')

    def test_missing_batches_report_partial_coverage(self):
        self.snap('2026-10-01',[game(1,100),game(2,200)])
        self.db.execute("INSERT INTO cache VALUES ('universe:1','11')")
        class Http:
            def get(self,url):
                return {'games':[{'universeId':11,'rootPlaceId':1,'name':'A','playing':50}]}
        http=Http()
        rows=sources.collect('romanum',http,self.db,'1')
        self.assertEqual(len(rows),1)
        self.assertIn('1/2',http.issues[0])

    def test_stops_after_three_failed_batches(self):
        self.snap('2026-10-01',[game(i,100) for i in range(1,42)])
        self.db.executemany('INSERT INTO cache VALUES (?,?)', [('universe:'+str(i),str(i+100)) for i in range(1,42)])
        class Http:
            calls=0
            def get(self,url):
                self.calls+=1
                raise ValueError('service unavailable')
        http=Http()
        self.assertEqual(sources.collect('romanum',http,self.db,'1'),[])
        self.assertEqual(http.calls,3)
        self.assertTrue(any('Stopped' in e for e in http.issues))


if __name__=='__main__':unittest.main()
