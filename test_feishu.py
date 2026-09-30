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

    def test_ccu_values_come_from_daily_and_weekly_baselines(self):
        for day, count in [('2026-09-28',250),('2026-09-22',1000)]:
            self.db.execute("INSERT INTO runs(id,started_at,status) VALUES (?,?,'success')",(day,day+'T02:00:00+00:00'))
            self.db.commit()
            radar.save_snapshot(self.db,day,'roblox','rolimons',day,day+'T02:00:00+00:00',
                [dict(game_id='1',name='Game',ccu=count,url='https://www.roblox.com/games/1')],1,'test')
        text='\n'.join(feishu.report_parts(self.db,'2026-09-29'))
        row=next(line for line in text.splitlines() if line.startswith('#1 '))
        self.assertIn('日CCU +250人（+100.00%）',row)
        self.assertIn('周CCU -500人（-50.00%）',row)
        self.assertIn('周 CCU 减少 Top 3',text)
        report=radar.ranking(self.db,day='2026-09-29')
        import csv,io
        exported=list(csv.DictReader(io.StringIO(radar.csv_data(report).decode('utf-8-sig'))))[0]
        self.assertEqual(float(exported['daily_ccu_pct']),100)
        self.assertEqual(float(exported['weekly_ccu_pct']),-50)

    def test_ccu_missing_and_zero_baseline(self):
        self.assertEqual(feishu.ccu_change({'state':'no_baseline'}),'暂无基准')
        self.assertEqual(feishu.ccu_change({'state':'outside_previous_sample'}),'前期未入样本')
        self.assertEqual(feishu.ccu_change({'state':'matched','ccu_change':50,'ccu_pct':None}),'+50人（基准为0，涨跌幅不适用）')

    def test_supplement_has_separate_deduplication(self):
        sent=[]
        sender=lambda f,text:sent.append(text)
        feishu.deliver(self.db,'2026-09-29',self.f,sender,lambda _:None)
        original=len(sent)
        feishu.deliver(self.db,'2026-09-29',self.f,sender,lambda _:None,edition='ccu-update')
        self.assertGreater(len(sent),original)
        self.assertTrue(all('CCU变化补充' in text for text in sent[original:]))
        total=len(sent)
        feishu.deliver(self.db,'2026-09-29',self.f,sender,lambda _:None,edition='ccu-update')
        feishu.deliver(self.db,'2026-09-29',self.f,sender,lambda _:None)
        self.assertEqual(len(sent),total)

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

    def test_transient_rejection_recovers_without_resending_summary(self):
        received, attempts, waits = [], [], []
        def sender(f, text):
            attempts.append(text)
            if len(attempts) == 2:
                raise feishu.Rejected('temporary rejection', code=11233)
            received.append(text)
        with self.assertLogs('radar.feishu', level='WARNING'):
            feishu.deliver(self.db,'2026-09-29',self.f,sender,waits.append)
        total=len(feishu.report_parts(self.db,'2026-09-29'))
        self.assertEqual(len(received), total)
        self.assertEqual(len(set(received)), total)
        self.assertEqual(len(attempts), total+1)
        self.assertIn(3, waits)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM feishu_outbox WHERE state='sent'").fetchone()[0],total)

    def test_retry_exhaustion_stays_pending(self):
        attempts=[]
        def reject(f,text):
            attempts.append(text)
            raise feishu.Rejected('temporary rejection',code=11233)
        with self.assertLogs('radar.feishu', level='WARNING'), self.assertRaises(feishu.Rejected):
            feishu.deliver(self.db,'2026-09-29',self.f,reject,lambda _:None)
        self.assertEqual(len(attempts),4)
        self.assertEqual(self.db.execute('SELECT state FROM feishu_outbox WHERE part=0').fetchone()[0],'pending')

    def test_timeout_not_retried_by_backoff(self):
        attempts=[]
        def uncertain(f,text):
            attempts.append(text)
            raise TimeoutError('unknown delivery')
        with self.assertRaises(TimeoutError):
            feishu.send_with_retry(self.f,'test',uncertain,lambda _:self.fail('must not retry'))
        self.assertEqual(len(attempts),1)

    def test_http_rate_limit_is_retried(self):
        attempts=[]
        def sender(f,text):
            attempts.append(text)
            if len(attempts)==1:raise feishu.Rejected('HTTP 429',code=429)
        with self.assertLogs('radar.feishu',level='WARNING'):
            feishu.send_with_retry(self.f,'test',sender,lambda _:None)
        self.assertEqual(len(attempts),2)

    def test_signature_shape_and_url_validation(self):
        p=feishu.signed_payload('hello','secret',123)
        self.assertEqual(p['timestamp'],'123')
        self.assertEqual(len(p['sign']),44)
        for url in ['http://open.feishu.cn/open-apis/bot/v2/hook/x','https://evil.example/open-apis/bot/v2/hook/x','https://open.feishu.cn/open-apis/bot/v2/hook/']:
            with self.assertRaises(ValueError):feishu.validate({'webhook_url':url})


if __name__=='__main__':unittest.main()
