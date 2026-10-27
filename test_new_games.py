import unittest
import new_games
import radar
import test_radar
from test_radar import game


class NewGamesTest(unittest.TestCase):
    setUp=test_radar.RadarTest.setUp
    tearDown=test_radar.RadarTest.tearDown
    snap=test_radar.RadarTest.snap

    def created(self,gid,stamp):
        new_games.remember(self.db,{'rootPlaceId':gid,'id':gid+100,'created':stamp},'2026-10-27T09:00:00+00:00')
        self.db.commit()

    def test_old_or_unknown_creation_is_not_new_game(self):
        self.snap('2026-10-01',[game(1,1000)])
        self.snap('2026-10-27',[game(1,1000),game(2,1000),game(3,1000)])
        self.created(2,'2020-01-01T00:00:00Z')
        r=radar.ranking(self.db)
        self.assertFalse(any(g['novelty']['hot_new'] for g in r['rows']))
        self.assertEqual(r['new_game_summary']['recent_count'],2)
        self.assertFalse(next(g for g in r['rows'] if g['game_id']=='1')['novelty']['recently_observed'])

    def test_recent_creation_and_ccu_or_growth_required(self):
        self.snap('2026-10-26',[game(1,1000),game(2,400),game(3,20)])
        self.snap('2026-10-27',[game(1,1000),game(2,500),game(3,300),game(4,5000)])
        for gid in (1,2,3):self.created(gid,'2026-10-10T00:00:00Z')
        self.created(4,'2026-11-01T00:00:00Z')
        r=radar.ranking(self.db)
        self.assertEqual({g['game_id'] for g in r['rows'] if g['novelty']['hot_new']},{'1','2'})
        self.assertEqual(r['new_game_summary']['known_creation_count'],3)

    def test_initial_sample_is_not_recently_entered(self):
        self.snap('2026-10-27',[game(1,1000)])
        self.assertFalse(radar.ranking(self.db)['rows'][0]['novelty']['recently_observed'])


if __name__=='__main__':unittest.main()
