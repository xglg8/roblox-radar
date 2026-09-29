import json
import tempfile
import unittest
from pathlib import Path

import feishu
import radar


class FeishuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = radar.connect(Path(self.tmp.name) / 'db.sqlite3')
        self.db.execute("INSERT INTO runs(id,started_at,status) VALUES ('r','2026-09-29T01:00:00+00:00','success')")
        self.db.commit()
        rows = [dict(game_id=str(i),name='中文 Roblox 游戏 '+str(i),ccu=501-i,url=f'https://www.roblox.com/games/{i}') for i in range(1,501)]
        radar.save_snapshot(self.db,'r','roblox','rolimons','2026-09-29','2026-09-29T01:00:00+00:00',rows,500,'test')
        self.f = {'webhook_url':'https://open.feishu.cn/open-apis/bot/v2/hook/test-token','enabled':True}

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def test_all_rows_bounded_and_only_roblox(self):
        parts = feishu.report_parts(self.db,'2026-09-29')
        text = '\n'.join(parts)
        self.assertEqual(sum(line.startswith('#') for line in text.splitlines()),500)
        self.assertNotIn('Steam',text)
        self.assertIn('无基准',text)
        for p in parts:
            self.assertLess(len(json.dumps(feishu.signed_payload(p,'secret'),ensure_ascii=False).encode()),18000)

    def test_success_not_resent(self):
        sent=[]
        sender=lambda f,t:sent.append(t)
        feishu.deliver(self.db,'2026-09-29',self.f,sender,lambda _:None)
        n=len(sent)
        feishu.deliver(self.db,'2026-09-29',self.f,sender,lambda _:None)
        self.assertEqual(len(sent),n)

    def test_rejection_resumes_at_failed_part(self):
        sent=[]
        def first(f,t):
            if len(sent)==1:raise feishu.Rejected('rate limited')
            sent.append(t)
        with self.assertRaises(feishu.Rejected):
            feishu.deliver(self.db,'2026-09-29',self.f,first,lambda _:None)
        feishu.deliver(self.db,'2026-09-29',self.f,lambda f,t:sent.append(t),lambda _:None)
        self.assertEqual(len(sent),len(feishu.report_parts(self.db,'2026-09-29')))

    def test_uncertain_does_not_retry_blindly(self):
        def fail(f,t):raise TimeoutError('unknown delivery')
        with self.assertRaises(TimeoutError):
            feishu.deliver(self.db,'2026-09-29',self.f,fail,lambda _:None)
        with self.assertRaisesRegex(RuntimeError,'uncertain'):
            feishu.deliver(self.db,'2026-09-29',self.f,lambda f,t:self.fail('must not send'),lambda _:None)

    def test_stale_date_rejected(self):
        with self.assertRaises(ValueError):feishu.report_parts(self.db,'2026-09-30')

    def test_signature_shape_and_url_validation(self):
        p=feishu.signed_payload('hello','secret',123)
        self.assertEqual(p['timestamp'],'123')
        self.assertEqual(len(p['sign']),44)
        for url in ['http://open.feishu.cn/open-apis/bot/v2/hook/x','https://evil.example/open-apis/bot/v2/hook/x','https://open.feishu.cn/open-apis/bot/v2/hook/']:
            with self.assertRaises(ValueError):feishu.validate({'webhook_url':url})


if __name__=='__main__':unittest.main()
