import base64, io, json, shutil, smtplib, ssl, tempfile, unittest
from pathlib import Path
from email.message import EmailMessage
from unittest.mock import patch, Mock
import app

class FakeSMTP:
    def __init__(self, error=None):
        self.sent=[]; self.error=error
    def send_message(self, message):
        if self.error: raise self.error
        self.sent.append(message); return {}

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=app.DB_PATH
        self.old_root=app.ROOT;self.old_whatsapp=app.DEFAULT_WHATSAPP
        app.ROOT=Path(self.temp.name)
        for file in ('index.html','default_templates.json','contacts.example.json'):
            shutil.copy(self.old_root/file,app.ROOT/file)
        (app.ROOT/'sender_contacts.json').write_text(json.dumps({app.ADDRESS:{'line_url':'https://line.me/ti/p/demo-one','line_qr':'employee-one-line.png'},'second.employee@andclan.co.jp':{'line_url':'https://line.me/ti/p/demo-two','line_qr':'employee-two-line.png'}}))
        (app.ROOT/'assets').mkdir()
        image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        for name in ('employee-one-line.png','employee-two-line.png','hiwin-whatsapp.png'):(app.ROOT/'assets'/name).write_bytes(image)
        app.DEFAULT_WHATSAPP={'phone':'+81 00-0000-0000','url':'https://wa.me/810000000000','qr':'hiwin-whatsapp.png'}
        app.DB_PATH=app.ROOT/'test.sqlite3'
        app.MAIL_SESSIONS.clear()
        app.SETTINGS.update(sender=app.ADDRESS,password='',bridge_url='',bridge_secret='',sheet_saved=False,sheet_store_error='')
        app.initialize()
    def tearDown(self):
        app.DB_PATH=self.old;app.ROOT=self.old_root;app.DEFAULT_WHATSAPP=self.old_whatsapp;self.temp.cleanup()
    def preview(self):
        return app.create_batch({'ids':['TEST-20261002-001'], 'subject':'Test {{agency_name}} {{test_id}}', 'body':'Hello {{agency_name}}'})
    def approved(self):
        batch=self.preview(); app.approve_batch(batch['id'],batch['digest']); return batch
    def real_approved(self):
        batch=app.create_batch({'ids':['SG-007'],'subject':'Partnership','body':'Hello'})
        app.approve_batch(batch['id'],batch['digest']);return batch
    def sent(self):
        batch=self.approved(); smtp=FakeSMTP(); app.send_batch(batch['id'],smtp,pause=0)
        return smtp.sent[0]
    def reply(self, sent, sender='other.person@example.com', auto=False):
        message=EmailMessage(); message['From']=sender; message['To']=app.ADDRESS
        message['In-Reply-To']=sent['Message-ID']; message['Subject']='Re: Test'
        if auto: message['Auto-Submitted']='auto-replied'
        message.set_content('Response'); return message

    def http_handler(self, host, origin=None):
        handler=app.Handler.__new__(app.Handler)
        handler.server=Mock(server_port=8765)
        handler.headers=EmailMessage()
        if host is not None:handler.headers['Host']=host
        if origin is not None:handler.headers['Origin']=origin
        handler.headers['X-Local-Token']=app.TOKEN
        body=json.dumps({'sender':app.ADDRESS,'id':'example-batch'}).encode()
        handler.headers['Content-Length']=str(len(body))
        handler.rfile=io.BytesIO(body)
        handler.respond=Mock()
        return handler

    def test_http_both_local_addresses_allow_page_and_same_origin_actions(self):
        for host in ('127.0.0.1:8765','localhost:8765'):
            with self.subTest(host=host):
                handler=self.http_handler(host,'http://'+host)
                handler.path='/'
                handler.do_GET()
                self.assertEqual(handler.respond.call_args.kwargs['content_type'],'text/html')
                handler.path='/api/send-check'
                with patch.object(app,'check_send',return_value={'required':True}) as check:
                    handler.do_POST()
                check.assert_called_once_with('example-batch')
                handler.respond.assert_called_with({'required':True})

    def test_http_rejects_external_hosts_duplicate_hosts_and_foreign_origins(self):
        for host in (None,'example.com:8765','localhost.example.com:8765','127.0.0.1:9999','localhost:9999','localhost:8765@evil.com'):
            with self.subTest(host=host):
                handler=self.http_handler(host)
                handler.path='/'
                handler.do_GET()
                handler.respond.assert_called_with({'error':'Invalid host'},403)
        handler=self.http_handler('localhost:8765')
        handler.headers['Host']='localhost:8765'
        self.assertFalse(handler.valid_host())
        for origin in ('http://example.com','http://localhost:9999','http://127.0.0.1:8765','null'):
            with self.subTest(origin=origin):
                handler=self.http_handler('localhost:8765',origin)
                handler.path='/api/send-check'
                with patch.object(app,'check_send') as check:handler.do_POST()
                check.assert_not_called()
                handler.respond.assert_called_with({'error':'Invalid origin'},403)

    def test_localhost_actions_still_require_local_token(self):
        handler=self.http_handler('localhost:8765','http://localhost:8765')
        del handler.headers['X-Local-Token']
        handler.path='/api/send-check'
        with patch.object(app,'check_send') as check:handler.do_POST()
        check.assert_not_called()
        handler.respond.assert_called_with({'error':'Forbidden'},403)
    def test_approval_required(self):
        batch=self.preview()
        with self.assertRaises(ValueError): app.send_batch(batch['id'],FakeSMTP(),pause=0)
        self.assertEqual(app.log_rows(),[])
    def test_template_render_and_frozen_hash(self):
        batch=self.approved()
        self.assertIn('HIWIN Test Agency',batch['messages'][0]['subject'])
        with app.db() as c:
            payload=json.loads(c.execute('SELECT payload FROM batches').fetchone()[0]); payload['messages'][0]['recipient']='changed@example.com'
            c.execute('UPDATE batches SET payload=?',(json.dumps(payload),))
        with self.assertRaises(ValueError): app.send_batch(batch['id'],FakeSMTP(),pause=0)
    def test_formal_send_requires_double_check_and_allows_repeat(self):
        first=self.real_approved();transport=FakeSMTP()
        review=app.check_send(first['id'])
        self.assertTrue(review['required']);self.assertEqual(review['recipients'][0]['previous_count'],0)
        with self.assertRaises(ValueError):app.send_batch(first['id'],transport,pause=0)
        self.assertEqual(transport.sent,[]);self.assertEqual(app.log_rows(),[])
        app.send_batch(first['id'],transport,pause=0,confirmation=review['digest'])
        self.assertEqual(app.log_rows()[0]['status'],'服务器已接受')
        batch=self.real_approved()
        with self.assertRaises(ValueError): app.send_batch(batch['id'],FakeSMTP(),pause=0)
        review=app.check_send(batch['id'])
        self.assertEqual(review['recipients'][0]['previous_count'],1)
        with self.assertRaises(ValueError):app.send_batch(batch['id'],transport,pause=0,confirmation=app.digest({'other':'batch'}))
        app.send_batch(batch['id'],transport,pause=0,confirmation=review['digest'])
        self.assertEqual(len(transport.sent),2)
        with self.assertRaises(ValueError):app.send_batch(batch['id'],transport,pause=0,confirmation=review['digest'])
    def test_test_recipient_can_repeat_after_stopping_with_new_approval(self):
        self.sent()
        with app.db() as c:
            c.execute('INSERT INTO stops VALUES(?)',('workflow-test@example.com',))
            c.execute("UPDATE messages SET status='已停止'")
        contact=next(row for row in app.contacts() if row['id']=='TEST-20261002-001')
        self.assertFalse(contact['blocked'])
        batch=self.approved();transport=FakeSMTP();app.send_batch(batch['id'],transport,pause=0)
        self.assertEqual(len(transport.sent),1)
        self.assertEqual(len(app.log_rows()),2)
        self.assertEqual(app.log_rows()[0]['status'],'服务器已接受')
        self.assertEqual(app.log_rows()[1]['status'],'已停止')
        with self.assertRaises(ValueError):app.send_batch(batch['id'],FakeSMTP(),pause=0)
    def test_stopped_formal_recipient_can_send_after_updated_double_check(self):
        batch=self.real_approved();address=batch['messages'][0]['recipient']
        old_review=app.check_send(batch['id'])
        with app.db() as c:c.execute('INSERT INTO stops VALUES(?)',(address,))
        self.assertTrue(next(row for row in app.contacts() if row['id']=='SG-007')['blocked'])
        self.real_approved()  # Stopped contacts still permit a fresh preview.
        transport=FakeSMTP()
        with self.assertRaises(ValueError):app.send_batch(batch['id'],transport,pause=0,confirmation=old_review['digest'])
        self.assertEqual(transport.sent,[])
        review=app.check_send(batch['id']);self.assertTrue(review['recipients'][0]['stopped'])
        app.send_batch(batch['id'],transport,pause=0,confirmation=review['digest'])
        self.assertEqual(len(transport.sent),1)
    def test_test_alias_cannot_bypass_stopped_real_agency(self):
        address=next(row for row in app.contacts() if row['id']=='SG-007')['email']
        with app.db() as c:
            # An imported legacy test label must not exempt a real recipient.
            c.execute('INSERT INTO extra_contacts VALUES(?,?,?,?,0)',('test-alias','Duplicate test label',address,'测试'))
            c.execute('INSERT INTO stops VALUES(?)',(address,))
        alias={'id':'test-alias'}
        self.assertTrue(next(row for row in app.contacts() if row['id']==alias['id'])['blocked'])
        batch=app.create_batch({'ids':[alias['id']],'subject':'x','body':'x'})
        app.approve_batch(batch['id'],batch['digest'])
        self.assertTrue(app.check_send(batch['id'])['required'])
        self.assertTrue(app.check_send(batch['id'])['recipients'][0]['stopped'])
        with self.assertRaises(ValueError):app.send_batch(batch['id'],FakeSMTP(),pause=0)

    def test_double_check_expires_when_other_batch_sends(self):
        first=self.real_approved();second=self.real_approved()
        review=app.check_send(second['id'])
        app.send_batch(first['id'],FakeSMTP(),pause=0,confirmation=app.check_send(first['id'])['digest'])
        transport=FakeSMTP()
        with self.assertRaises(ValueError):app.send_batch(second['id'],transport,pause=0,confirmation=review['digest'])
        self.assertEqual(transport.sent,[])
        self.assertEqual(app.check_send(second['id'])['recipients'][0]['previous_count'],1)

    def test_delete_record_removes_from_list_and_preserves_reply_and_repeat_evidence(self):
        batch=self.real_approved();transport=FakeSMTP()
        app.send_batch(batch['id'],transport,pause=0,confirmation=app.check_send(batch['id'])['digest'])
        row=app.log_rows()[0]
        app.delete_record(row['id']);self.assertEqual(app.log_rows(),[])
        app.initialize();self.assertEqual(app.log_rows(),[])
        with self.assertRaises(ValueError):app.delete_record(row['id'])
        second=self.real_approved()
        self.assertEqual(app.check_send(second['id'])['recipients'][0]['previous_count'],1)
        self.assertEqual(app.record_incoming(self.reply(transport.sent[0]).as_bytes()),1)
        self.assertTrue(app.check_send(second['id'])['recipients'][0]['stopped'])
        self.assertEqual(app.log_rows(),[])

    def test_delete_record_rejects_active_batch(self):
        self.sent();row=app.log_rows()[0]
        with app.db() as c:c.execute("UPDATE batches SET status='sending' WHERE id=?",(row['batch_id'],))
        with self.assertRaises(ValueError):app.delete_record(row['id'])
        self.assertEqual(len(app.log_rows()),1)

    def test_deleted_records_still_count_towards_daily_limit(self):
        self.sent();app.delete_record(app.log_rows()[0]['id'])
        with app.db() as c:
            c.execute("INSERT INTO messages(id,batch_id,contact_id,name,recipient,subject,body,status,sent_at) SELECT '<prior-'||n||'@example.com>','old','old','Test','other'||n||'@example.com','x','x','服务器已接受',? FROM (WITH RECURSIVE nums(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM nums WHERE n<29) SELECT n FROM nums)",(app.now(),))
        with self.assertRaises(ValueError):app.send_batch(self.approved()['id'],FakeSMTP(),pause=0)

    def test_bulk_delete_removes_only_selected_and_is_atomic(self):
        for _ in range(3):self.sent()
        rows=app.log_rows()
        with self.assertRaises(ValueError):app.delete_records([rows[0]['id'],'missing'])
        self.assertEqual(len(app.log_rows()),3)
        with self.assertRaises(ValueError):app.delete_records([rows[0]['id'],rows[0]['id']])
        with app.db() as c:c.execute("UPDATE batches SET status='sending' WHERE id=?",(rows[1]['batch_id'],))
        with self.assertRaises(ValueError):app.delete_records([rows[0]['id'],rows[1]['id']])
        self.assertEqual(len(app.log_rows()),3)
        with app.db() as c:c.execute("UPDATE batches SET status='complete' WHERE id=?",(rows[1]['batch_id'],))
        self.assertEqual(app.delete_records([rows[0]['id'],rows[1]['id']])['count'],2)
        self.assertEqual([row['id'] for row in app.log_rows()],[rows[2]['id']])
        app.initialize();self.assertEqual(len(app.log_rows()),1)

    def planned(self, formal=False, stamp='2030-02-01T13:00'):
        app.SETTINGS['password']='session-test-only'
        app.MAIL_SESSIONS[app.SETTINGS['sender']]='session-test-only'
        with patch.object(app,'now',return_value='2030-02-01T12:00:00+09:00'):
            batch=self.real_approved() if formal else self.approved()
            review=app.check_send(batch['id'],stamp)
            result=app.schedule_batch(batch['id'],stamp,review['digest'])
        return batch,result

    def run_plans(self, at, transport=None):
        transport=transport or FakeSMTP()
        with patch.object(app,'now',return_value=at):
            app.process_schedules(transport_factory=lambda sender:transport,pause=0)
        return transport

    def test_schedule_freezes_records_and_sends_only_once_at_due_time(self):
        batch,result=self.planned(formal=True)
        self.assertEqual(result['scheduled_at'],'2030-02-01T13:00:00+09:00')
        row=app.log_rows()[0]
        self.assertEqual(row['status'],'待定时发送');self.assertEqual(row['message_type'],'formal')
        self.assertEqual(row['send_mode'],'scheduled');self.assertFalse(row['can_delete'])
        transport=self.run_plans('2030-02-01T12:59:59+09:00')
        self.assertEqual(transport.sent,[])
        self.run_plans('2030-02-01T13:00:00+09:00',transport)
        self.run_plans('2030-02-01T13:01:00+09:00',transport)
        self.assertEqual(len(transport.sent),1)
        self.assertEqual(transport.sent[0]['Subject'],batch['messages'][0]['subject'])
        self.assertEqual(transport.sent[0].get_body(preferencelist=('plain',)).get_content().strip(),batch['messages'][0]['body'])
        sent=app.log_rows()[0];self.assertEqual(sent['id'],row['id'])
        self.assertEqual(sent['status'],'服务器已接受');self.assertEqual(sent['schedule_status'],'complete')
        self.assertEqual(sent['sent_at'],'2030-02-01T13:00:00+09:00');self.assertTrue(sent['can_delete'])

    def test_scheduling_requires_connection_approval_future_time_and_bound_confirmation(self):
        batch=self.approved()
        with patch.object(app,'now',return_value='2030-02-01T12:00:00+09:00'):
            with self.assertRaises(ValueError):app.schedule_batch(batch['id'],'2030-02-01T13:00','wrong')
            app.SETTINGS['password']='dummy'
            for stamp in ('2030-02-01T11:00','not a date','2030-02-01T13:00+09:00'):
                with self.assertRaises(ValueError):app.check_send(batch['id'],stamp)
            review=app.check_send(batch['id'],'2030-02-01T13:00')
            with self.assertRaises(ValueError):app.schedule_batch(batch['id'],'2030-02-01T14:00',review['digest'])
            app.schedule_batch(batch['id'],'2030-02-01T13:00',review['digest'])
            with self.assertRaises(ValueError):app.schedule_batch(batch['id'],'2030-02-01T13:00',review['digest'])
        self.assertEqual(len(app.log_rows()),1)

    def test_cancel_schedule_prevents_send_and_allows_bulk_delete(self):
        batch,_=self.planned()
        with self.assertRaises(ValueError):app.delete_records([app.log_rows()[0]['id']])
        app.cancel_schedule(batch['id'])
        self.assertEqual(app.log_rows()[0]['status'],'已取消定时')
        self.assertEqual(self.run_plans('2030-02-01T13:00:00+09:00').sent,[])
        app.delete_records([app.log_rows()[0]['id']]);self.assertEqual(app.log_rows(),[])
        with self.assertRaises(ValueError):app.cancel_schedule(batch['id'])

    def test_due_schedule_waits_for_account_then_sends_with_its_credentials(self):
        batch,_=self.planned();app.MAIL_SESSIONS.clear();app.SETTINGS['password']=''
        with patch.object(app,'now',return_value='2030-02-01T13:00:00+09:00'),patch.object(app,'smtp') as smtp:
            app.process_schedules(pause=0)
        smtp.assert_not_called();self.assertEqual(app.log_rows()[0]['status'],'待连接邮箱')
        app.MAIL_SESSIONS[app.ADDRESS]='reconnected-dummy'
        client=Mock();client.send_message.return_value={}
        with patch.object(app,'now',return_value='2030-02-01T13:01:00+09:00'),patch.object(app,'smtp',return_value=client) as smtp:
            app.process_schedules(pause=0)
        smtp.assert_called_once_with(app.ADDRESS,'reconnected-dummy')
        self.assertEqual(app.log_rows()[0]['status'],'服务器已接受')

    def test_scheduler_keeps_original_sender_without_switching_active_account(self):
        batch,_=self.planned()
        employee=self.employee();app.select_mail_account(employee['email'])
        app.SETTINGS['password']='employee-current';app.MAIL_SESSIONS[employee['email']]='employee-current'
        client=Mock();client.send_message.return_value={}
        with patch.object(app,'now',return_value='2030-02-01T13:00:00+09:00'),patch.object(app,'smtp',return_value=client) as smtp:
            app.process_schedules(pause=0)
        smtp.assert_called_once_with(app.ADDRESS,'session-test-only')
        self.assertEqual(client.send_message.call_args.args[0]['From'],app.ADDRESS)
        self.assertEqual(app.SETTINGS['sender'],employee['email']);self.assertEqual(app.SETTINGS['password'],'employee-current')

    def test_schedule_restart_preserves_future_tasks_but_marks_overdue_as_missed(self):
        self.planned()
        with patch.object(app,'now',return_value='2030-02-01T12:30:00+09:00'):app.initialize()
        self.assertEqual(app.log_rows()[0]['status'],'待定时发送')
        with patch.object(app,'now',return_value='2030-02-01T13:00:01+09:00'):app.initialize()
        self.assertEqual(app.log_rows()[0]['status'],'已错过时间')
        self.assertEqual(self.run_plans('2030-02-01T13:01:00+09:00').sent,[])

    def test_schedule_after_sleep_marks_missed_and_does_not_catch_up(self):
        self.planned()
        self.assertEqual(self.run_plans('2030-02-01T13:06:00+09:00').sent,[])
        self.assertEqual(app.log_rows()[0]['status'],'已错过时间')

    def test_new_stop_after_scheduling_requires_review_before_sending(self):
        batch,_=self.planned(formal=True)
        with app.db() as c:c.execute('INSERT INTO stops VALUES(?)',(batch['messages'][0]['recipient'],))
        self.assertEqual(self.run_plans('2030-02-01T13:00:00+09:00').sent,[])
        self.assertEqual(app.log_rows()[0]['status'],'待重新确认')

    def test_scheduled_approved_repeat_still_sends_when_history_grows(self):
        first,_=self.planned(formal=True)
        second,_=self.planned(formal=True,stamp='2030-02-01T14:00')
        transport=self.run_plans('2030-02-01T13:00:00+09:00')
        self.run_plans('2030-02-01T14:00:00+09:00',transport)
        self.assertEqual(len(transport.sent),2)

    def test_schedule_payload_tampering_fails_without_smtp(self):
        batch,_=self.planned()
        with app.db() as c:
            payload=json.loads(c.execute('SELECT payload FROM batches WHERE id=?',(batch['id'],)).fetchone()[0])
            payload['messages'][0]['recipient']='other@example.com'
            c.execute('UPDATE batches SET payload=? WHERE id=?',(json.dumps(payload),batch['id']))
        self.assertEqual(self.run_plans('2030-02-01T13:00:00+09:00').sent,[])
        self.assertEqual(app.log_rows()[0]['status'],'定时失败')

    def test_scheduled_unknown_delivery_is_not_retried(self):
        self.planned()
        transport=FakeSMTP(TimeoutError())
        self.run_plans('2030-02-01T13:00:00+09:00',transport)
        self.assertEqual(app.log_rows()[0]['status'],'结果待核对')
        with patch.object(app,'now',return_value='2030-02-01T13:00:10+09:00'):app.initialize()
        self.run_plans('2030-02-01T13:01:00+09:00',transport)
        self.assertEqual(app.log_rows()[0]['status'],'结果待核对')

    def test_schedule_reservations_respect_daily_limit(self):
        with patch.object(app,'now',return_value='2030-02-01T12:00:00+09:00'):
            self.planned()
            with app.db() as c:
                c.execute("INSERT INTO messages(id,batch_id,contact_id,name,recipient,subject,body,status,sent_at) SELECT '<prior-'||n||'@example.com>','old','old','Test','other'||n||'@example.com','x','x','服务器已接受',? FROM (WITH RECURSIVE nums(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM nums WHERE n<29) SELECT n FROM nums)",('2030-02-01T12:00:00+09:00',))
            with self.assertRaises(ValueError):self.planned()

    def test_record_types_and_recipient_timezones_are_frozen(self):
        self.sent();self.assertEqual(app.log_rows()[0]['message_type'],'test')
        batch=self.real_approved();app.send_batch(batch['id'],FakeSMTP(),pause=0,confirmation=app.check_send(batch['id'])['digest'])
        formal=app.log_rows()[0];self.assertEqual(formal['message_type'],'formal');self.assertEqual(formal['recipient_timezone'],'Asia/Singapore')
        app.initialize();self.assertEqual(app.log_rows()[0]['message_type'],'formal')

    def test_legacy_record_type_is_set_after_country_restoration(self):
        self.sent()
        with app.db() as c:c.execute("UPDATE messages SET country='未分类',message_type=''")
        app.initialize()
        row=app.log_rows()[0]
        self.assertEqual(row['country'],'测试');self.assertEqual(row['message_type'],'test')

    def test_new_test_contact_can_repeat_after_stop(self):
        contact=app.add_contact({'country':'测试','name':'Second test mailbox','email':'test@example.com'})
        with app.db() as c:c.execute('INSERT INTO stops VALUES(?)',(contact['email'],))
        for attempt in range(2):
            batch=app.create_batch({'ids':[contact['id']],'subject':'Test','body':'Hello'})
            app.approve_batch(batch['id'],batch['digest']);transport=FakeSMTP()
            app.send_batch(batch['id'],transport,pause=0);self.assertEqual(len(transport.sent),1)
        self.assertEqual(len(app.log_rows()),2)
    def test_test_resends_still_count_towards_daily_limit(self):
        self.sent()
        with app.db() as c:
            c.execute("INSERT INTO messages(id,batch_id,contact_id,name,recipient,subject,body,status,sent_at) SELECT '<prior-'||n||'@example.com>','old','old','Test','other'||n||'@example.com','x','x','服务器已接受',? FROM (WITH RECURSIVE nums(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM nums WHERE n<29) SELECT n FROM nums)",(app.now(),))
        with self.assertRaises(ValueError):app.send_batch(self.approved()['id'],FakeSMTP(),pause=0)
    def test_ambiguous_delivery_not_retried(self):
        batch=self.approved(); app.send_batch(batch['id'],FakeSMTP(TimeoutError()),pause=0)
        self.assertEqual(app.log_rows()[0]['status'],'结果待核对')
        with self.assertRaises(ValueError): app.send_batch(batch['id'],FakeSMTP(),pause=0)
    def test_recovery_after_crash(self):
        self.sent()
        with app.db() as c:c.execute("UPDATE messages SET status='发送中'")
        app.initialize(); self.assertEqual(app.log_rows()[0]['status'],'结果待核对')
    def test_different_person_reply_and_dedup(self):
        reply=self.reply(self.sent()).as_bytes()
        self.assertEqual(app.record_incoming(reply),1)
        self.assertEqual(app.record_incoming(reply),0)
        self.assertEqual(app.log_rows()[0]['reply_status'],'已回复')
        with app.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM stops').fetchone()[0],1)
    def test_auto_reply_is_not_human_reply(self):
        app.record_incoming(self.reply(self.sent(),auto=True).as_bytes())
        self.assertEqual(app.log_rows()[0]['reply_status'],'自动回复')
        self.assertEqual(app.log_rows()[0]['status'],'服务器已接受')
    def test_same_email_without_reference_needs_review(self):
        self.sent(); message=EmailMessage();message['From']='workflow-test@example.com';message.set_content('Unrelated?')
        app.record_incoming(message.as_bytes());self.assertEqual(app.log_rows()[0]['reply_status'],'待人工匹配')
    def test_dsn_failed_stops_contact(self):
        sent=self.sent()
        raw=('MIME-Version: 1.0\r\nContent-Type: multipart/report; report-type=delivery-status; boundary="dsn"\r\nReferences: '+sent['Message-ID']+'\r\n\r\n--dsn\r\nContent-Type: text/plain\r\n\r\nDelivery failed\r\n--dsn\r\nContent-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; example.com\r\n\r\nFinal-Recipient: rfc822; workflow-test@example.com\r\nAction: failed\r\nStatus: 5.1.1\r\n\r\n--dsn--\r\n').encode()
        app.record_incoming(raw);self.assertEqual(app.log_rows()[0]['status'],'退信')
    def test_sheet_failure_does_not_resend(self):
        smtp=FakeSMTP();batch=self.approved()
        with patch.object(app,'sync_sheet',return_value={'ok':False}): app.send_batch(batch['id'],smtp,pause=0)
        self.assertEqual(len(smtp.sent),1);self.assertEqual(app.log_rows()[0]['status'],'服务器已接受')
        self.assertEqual(app.log_rows()[0]['sheet_synced'],0)
    def test_invalid_variables_and_missing_email(self):
        with self.assertRaises(ValueError):app.create_batch({'ids':['TEST-20261002-001'],'subject':'{{unknown}}','body':'x'})
        with self.assertRaises(ValueError):app.create_batch({'ids':['MY-012'],'subject':'x','body':'x'})
    def test_connection_required_without_consuming_approval(self):
        batch=self.approved()
        with self.assertRaises(ValueError):app.send_batch(batch['id'])
        self.assertEqual(app.log_rows(),[])
    def test_tls_error_is_not_reported_as_bad_password(self):
        text=app.connection_error(ssl.SSLCertVerificationError('missing issuer'))
        self.assertIn('尚未验证密码',text)
        self.assertNotIn('身份验证（',text)
    def test_authentication_error_shows_code_not_credentials(self):
        text=app.connection_error(smtplib.SMTPAuthenticationError(535,b'private server diagnostic'))
        self.assertIn('535',text)
        self.assertNotIn('private',text)
    def test_tls_keeps_verification_enabled(self):
        context=app.tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
    def test_sheet_restores_without_exposing_secret(self):
        saved={'url':'https://script.google.com/macros/s/TEST/exec','secret':'x'*64}
        with patch.object(app,'read_sheet_keychain',return_value=saved):
            self.assertTrue(app.restore_sheet_settings())
        state=app.app_state()
        self.assertTrue(state['sheet_saved'])
        self.assertNotIn(saved['secret'],json.dumps(state))
    def test_sheet_connection_probes_even_when_outbox_is_empty(self):
        with patch.object(app,'save_sheet_keychain') as save,patch.object(app,'sync_sheet',return_value={'ok':True,'count':0}) as sync:
            result=app.configure_sheet({'url':'https://script.google.com/macros/s/TEST/exec','secret':'x'*64})
        self.assertTrue(result['saved']);save.assert_called_once();sync.assert_called_once_with(probe=True)
    def test_saved_secret_not_reused_for_new_endpoint(self):
        app.SETTINGS.update(bridge_url='https://script.google.com/macros/s/OLD/exec',bridge_secret='x'*64)
        with self.assertRaises(ValueError):app.configure_sheet({'url':'https://script.google.com/macros/s/NEW/exec','secret':''})
    def test_country_and_template_survive_reinitialization(self):
        contact=app.add_contact({'country':'臺灣','name':'台灣測試旅行社','email':'taiwan@example.com'})
        template=app.save_template({'name':'台灣客製','language':'繁體中文','subject':'您好 {{agency_name}}','body':'合作洽詢','links':'官網 | https://example.com/hotel','attachments':[]})
        app.initialize();self.assertEqual(app.contacts()[-1]['country'],'台湾')
        self.assertEqual(next(row for row in app.template_rows() if row['id']==template['id'])['links'],'官網 | https://example.com/hotel')
        batch=app.create_batch({'ids':[contact['id']],'subject':'測試','body':'x'})
        app.approve_batch(batch['id'],batch['digest']);app.send_batch(batch['id'],FakeSMTP(),pause=0,confirmation=app.check_send(batch['id'])['digest'])
        self.assertEqual(app.log_rows()[0]['country'],'台湾')

    def cloud_contacts(self, rows):
        return {'ok':True,'contacts':rows,'source_sheet':'邮件跟踪','spreadsheet_id':app.SPREADSHEET}

    def test_contact_refresh_updates_cloud_fields_and_keeps_local_contacts_history_and_stops(self):
        self.sent();history=app.log_rows()
        local=app.add_contact({'country':'台湾','name':'Local Agency','email':'local@example.com'})
        with app.db() as c:c.execute('INSERT INTO stops VALUES(?)',('partner7@example.com',))
        rows=[{'id':'SG-007','name':'Updated JTB','country':'日本','email':'new-jtb@example.com','blocked':False},
              {'id':'TW-NEW','name':'New Agency','country':'臺灣','email':'new@example.com','blocked':False}]
        batch=self.approved()
        with patch.object(app,'sheet_bridge_request',return_value=self.cloud_contacts(rows)):
            result=app.refresh_sheet_contacts()
        self.assertEqual(result['count'],2)
        roster={row['id']:row for row in app.contacts()}
        self.assertEqual(roster['SG-007']['name'],'Updated JTB');self.assertTrue(roster['SG-007']['blocked'])
        self.assertEqual(roster['SG-007']['email'],'new-jtb@example.com')
        self.assertEqual(roster['TW-NEW']['country'],'台湾')
        self.assertIn(local['id'],roster);self.assertIn('TEST-20261002-001',roster)
        self.assertNotIn('SG-012',roster);self.assertEqual(app.log_rows(),history)
        app.initialize();self.assertEqual(next(row for row in app.contacts() if row['id']=='TW-NEW')['name'],'New Agency')
        self.assertTrue(app.app_state()['contacts_refreshed_at'])
        with self.assertRaises(ValueError):app.send_batch(batch['id'],FakeSMTP(),pause=0)

    def test_contact_refresh_failure_keeps_original_roster(self):
        original=app.contacts()
        for result in [self.cloud_contacts([]),{'ok':False},self.cloud_contacts([
                {'id':'x','name':'one','country':'台湾','email':''},
                {'id':'x','name':'two','country':'台湾','email':''}]),self.cloud_contacts([{'id':'','name':'No ID','email':''}])]:
            with patch.object(app,'sheet_bridge_request',return_value=result):
                with self.assertRaises(ValueError):app.refresh_sheet_contacts()
            self.assertEqual(app.contacts(),original)
        with patch.object(app,'sheet_bridge_request',side_effect=TimeoutError):
            with self.assertRaises(ValueError):app.refresh_sheet_contacts()
        self.assertEqual(app.contacts(),original)

    def test_contact_refresh_invalid_email_stays_visible_but_unselectable(self):
        rows=[{'id':'TW-BAD','name':'Needs email correction','country':'台湾','email':'a@example.com; b@example.com','blocked':False}]
        with patch.object(app,'sheet_bridge_request',return_value=self.cloud_contacts(rows)):
            result=app.refresh_sheet_contacts()
        self.assertEqual(result['invalid_emails'],1)
        row=next(row for row in app.contacts() if row['id']=='TW-BAD')
        self.assertEqual(row['email'],'');self.assertTrue(row['email_error'])
        with self.assertRaises(ValueError):app.create_batch({'ids':['TW-BAD'],'subject':'x','body':'x'})

    def test_contact_refresh_request_is_signed_and_contains_no_send_action(self):
        app.SETTINGS.update(bridge_url='https://script.google.com/macros/s/TEST/exec',bridge_secret='x'*64)
        response=Mock();response.read.return_value=b'{"ok":true}'
        context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
        with patch.object(app.urllib.request,'urlopen',return_value=context) as urlopen:
            self.assertTrue(app.sheet_bridge_request({'action':'contacts'})['ok'])
        request=urlopen.call_args.args[0];data=json.loads(request.data)
        self.assertEqual(json.loads(data['payload']),{'action':'contacts'})
        expected=app.hmac.new(b'x'*64,(data['timestamp']+'\n'+data['nonce']+'\n'+data['payload']).encode(),app.hashlib.sha256).hexdigest()
        self.assertEqual(data['signature'],expected)
    def test_old_records_migrate_without_losing_sent_data(self):
        with app.db() as c:
            c.execute('DROP TABLE messages')
            c.execute("CREATE TABLE messages(id TEXT PRIMARY KEY,batch_id TEXT,contact_id TEXT,name TEXT,recipient TEXT,subject TEXT,body TEXT,status TEXT,sent_at TEXT,reply_status TEXT DEFAULT '',reply_at TEXT DEFAULT '',next_step TEXT DEFAULT '',error TEXT DEFAULT '',sheet_synced INTEGER DEFAULT 0)")
            c.execute("INSERT INTO messages(id,contact_id,status,sent_at) VALUES('<old@example.com>','SG-007','服务器已接受','2026-10-02T12:00:00+09:00')")
        app.initialize();row=app.log_rows()[0]
        self.assertEqual(row['country'],'新加坡');self.assertEqual(row['sent_at'],'2026-10-02T12:00:00+09:00')
        self.assertEqual(row['attachments'],[])
        self.assertEqual(row['sender'],app.ADDRESS)
    def file(self):
        return app.upload_attachment({'name':'飯店介紹.txt','content':base64.b64encode('附件內容'.encode()).decode()})
    def test_mime_contains_correct_attachment_and_clickable_links(self):
        file=self.file()
        batch=app.create_batch({'ids':['TEST-20261002-001'],'subject':'附件測試','body':'飯店介紹','language':'繁體中文','attachments':[file['id']],'links':'官網 | https://example.com/hotel?x=1&y=2'})
        app.approve_batch(batch['id'],batch['digest']);transport=FakeSMTP();app.send_batch(batch['id'],transport,pause=0)
        message=transport.sent[0];attached=list(message.iter_attachments())
        self.assertEqual(attached[0].get_filename(),'飯店介紹.txt')
        self.assertEqual(attached[0].get_payload(decode=True),'附件內容'.encode())
        self.assertIn('href="https://example.com/hotel?x=1&amp;y=2"',message.get_body(preferencelist=('html',)).get_content())
        self.assertEqual(app.log_rows()[0]['attachments'][0]['sha256'],file['sha256'])
    def test_attachment_change_after_approval_blocks_send(self):
        file=self.file();batch=app.create_batch({'ids':['TEST-20261002-001'],'subject':'x','body':'x','attachments':[file['id']]})
        app.approve_batch(batch['id'],batch['digest'])
        with app.db() as c:path=c.execute('SELECT path FROM attachments').fetchone()[0]
        Path(path).write_text('changed')
        transport=FakeSMTP()
        with self.assertRaises(ValueError):app.send_batch(batch['id'],transport,pause=0)
        self.assertEqual(transport.sent,[]);self.assertEqual(app.log_rows(),[])
    def test_invalid_link_and_duplicate_attachment_are_rejected(self):
        with self.assertRaises(ValueError):app.parse_links('點此 | javascript:alert(1)')
        file=self.file()
        with self.assertRaises(ValueError):app.attachment_metadata([file['id'],file['id']])
    def test_template_saves_attachments_and_links(self):
        file=self.file();template=app.save_template({'id':'outreach-zh-TW','name':'台灣版本','language':'繁體中文','subject':'x','body':'x','links':'https://example.com','attachments':[file['id']]})
        app.initialize();stored=next(row for row in app.template_rows() if row['id']==template['id'])
        self.assertEqual(stored['attachments'][0]['id'],file['id']);self.assertEqual(stored['links'],'https://example.com')

    def employee(self):
        return app.save_mail_account({'email':'colleague@hiwin-japan.co.jp','label':'同事'})
    def test_employee_profiles_restore_without_passwords(self):
        employee=self.employee();app.select_mail_account(employee['email']);app.SETTINGS['password']='private-test-password'
        app.initialize();app.restore_mail_account()
        self.assertEqual(app.SETTINGS['sender'],employee['email']);self.assertEqual(app.SETTINGS['password'],'')
        self.assertIn(employee,app.mail_accounts());self.assertNotIn('private-test-password',json.dumps(app.app_state()))
    def test_employee_switch_permanently_invalidates_approved_batch(self):
        batch=self.approved();employee=self.employee();app.SETTINGS['password']='old'
        app.select_mail_account(employee['email']);self.assertEqual(app.SETTINGS['password'],'')
        app.select_mail_account(app.ADDRESS)
        with self.assertRaises(ValueError):app.send_batch(batch['id'],FakeSMTP(),pause=0)
        self.assertEqual(app.log_rows(),[])
    def test_external_mail_account_is_rejected(self):
        for address in ('user@gmail.com','bad-address','user@hiwin-japan.co.jp.evil.test'):
            with self.assertRaises(ValueError):app.save_mail_account({'email':address})
        self.assertEqual(len(app.mail_accounts()),1)
    def test_smtp_uses_selected_employee_and_shared_host(self):
        employee=self.employee();app.select_mail_account(employee['email']);client=Mock()
        with patch.object(app.smtplib,'SMTP_SSL',return_value=client) as factory:
            app.connect_mail({'sender':employee['email'],'password':'dummy-password'})
        client.login.assert_called_once_with(employee['email'],'dummy-password')
        self.assertEqual(factory.call_args.args,('mail1039.onamae.ne.jp',465));client.quit.assert_called_once()
    def test_andclan_employee_can_save_switch_and_login_with_own_address(self):
        employee=app.save_mail_account({'email':'second.employee@andclan.co.jp','label':'Second Employee'})
        app.select_mail_account(employee['email']);app.initialize();app.restore_mail_account()
        self.assertEqual(app.SETTINGS['sender'],employee['email'])
        client=Mock()
        with patch.object(app.smtplib,'SMTP_SSL',return_value=client) as factory:
            app.connect_mail({'sender':employee['email'],'password':'dummy-password'})
        client.login.assert_called_once_with(employee['email'],'dummy-password')
        self.assertEqual(factory.call_args.args,('mail1039.onamae.ne.jp',465))
        self.assertNotIn('dummy-password',json.dumps(app.app_state()))
    def test_auth_failure_identifies_actual_login_without_server_diagnostic(self):
        employee=app.save_mail_account({'email':'second.employee@andclan.co.jp'})
        app.select_mail_account(employee['email'])
        with patch.object(app,'smtp',side_effect=smtplib.SMTPAuthenticationError(535,b'private diagnostic')):
            with self.assertRaises(ValueError) as caught:
                app.connect_mail({'sender':employee['email'],'password':'dummy-password'})
        self.assertIn(employee['email'],str(caught.exception))
        self.assertIn('mail1039.onamae.ne.jp:465',str(caught.exception))
        self.assertNotIn('private diagnostic',str(caught.exception))
        self.assertNotIn('dummy-password',str(caught.exception))
        self.assertEqual(app.SETTINGS['password'],'')
    def test_stale_account_and_failed_auth_cannot_keep_old_password(self):
        employee=self.employee();app.select_mail_account(employee['email'])
        with self.assertRaises(ValueError):app.connect_mail({'sender':app.ADDRESS,'password':'old'})
        with patch.object(app,'smtp',side_effect=smtplib.SMTPAuthenticationError(535,b'secret diagnostic')):
            with self.assertRaises(ValueError):app.connect_mail({'sender':employee['email'],'password':'wrong'})
        self.assertEqual(app.SETTINGS['password'],'');self.assertEqual(app.SETTINGS['sender'],employee['email'])
    def test_verified_employees_switch_back_without_reentering_passwords(self):
        employee=self.employee();client=Mock()
        with patch.object(app.smtplib,'SMTP_SSL',return_value=client):
            app.connect_mail({'sender':app.ADDRESS,'password':'employee-dummy'})
            app.select_mail_account(employee['email'])
            self.assertEqual(app.SETTINGS['password'],'')
            app.connect_mail({'sender':employee['email'],'password':'colleague-dummy'})
            result=app.select_mail_account(app.ADDRESS)
            self.assertTrue(result['connected']);self.assertEqual(app.SETTINGS['password'],'employee-dummy')
            result=app.select_mail_account(employee['email'])
            self.assertTrue(result['connected']);self.assertEqual(app.SETTINGS['password'],'colleague-dummy')
        self.assertEqual(client.login.call_args_list[0].args,(app.ADDRESS,'employee-dummy'))
        self.assertEqual(client.login.call_args_list[1].args,(employee['email'],'colleague-dummy'))
        state=json.dumps(app.app_state())
        self.assertNotIn('employee-dummy',state);self.assertNotIn('colleague-dummy',state)
        with app.db() as c:
            self.assertNotIn('employee-dummy',json.dumps(list(c.iterdump())))
    def test_failed_employee_auth_does_not_disconnect_other_verified_employee(self):
        employee=self.employee()
        with patch.object(app.smtplib,'SMTP_SSL',return_value=Mock()):
            app.connect_mail({'sender':app.ADDRESS,'password':'employee-dummy'})
        app.select_mail_account(employee['email'])
        with patch.object(app,'smtp',side_effect=smtplib.SMTPAuthenticationError(535,b'private')):
            with self.assertRaises(ValueError):app.connect_mail({'sender':employee['email'],'password':'wrong-dummy'})
        self.assertNotIn(employee['email'],app.MAIL_SESSIONS)
        app.select_mail_account(app.ADDRESS);self.assertEqual(app.SETTINGS['password'],'employee-dummy')
    def test_blank_password_cannot_erase_verified_employee_connection(self):
        with patch.object(app.smtplib,'SMTP_SSL',return_value=Mock()):
            app.connect_mail({'sender':app.ADDRESS,'password':'employee-dummy'})
        with self.assertRaises(ValueError):app.connect_mail({'sender':app.ADDRESS,'password':''})
        self.assertEqual(app.SETTINGS['password'],'employee-dummy')
    def test_startup_restores_selection_but_no_employee_password(self):
        with patch.object(app.smtplib,'SMTP_SSL',return_value=Mock()):
            app.connect_mail({'sender':app.ADDRESS,'password':'employee-dummy'})
        self.assertTrue(app.MAIL_SESSIONS)
        app.restore_mail_account()
        self.assertEqual(app.MAIL_SESSIONS,{})
        self.assertEqual(app.SETTINGS['password'],'')
        self.assertFalse(any(row['connected_in_session'] for row in app.app_state()['mail_accounts']))
    def test_smtp_auth_rejection_invalidates_only_that_employees_session(self):
        employee=self.employee()
        with patch.object(app.smtplib,'SMTP_SSL',return_value=Mock()):
            app.connect_mail({'sender':app.ADDRESS,'password':'employee-dummy'})
            app.select_mail_account(employee['email'])
            app.connect_mail({'sender':employee['email'],'password':'colleague-dummy'})
        client=Mock();client.login.side_effect=smtplib.SMTPAuthenticationError(535,b'private')
        with patch.object(app.smtplib,'SMTP_SSL',return_value=client):
            with self.assertRaises(smtplib.SMTPAuthenticationError):app.smtp()
        self.assertEqual(app.SETTINGS['password'],'');self.assertNotIn(employee['email'],app.MAIL_SESSIONS)
        app.select_mail_account(app.ADDRESS);self.assertEqual(app.SETTINGS['password'],'employee-dummy')
    def test_employee_from_header_record_and_shared_repeat_warning(self):
        employee=self.employee();app.select_mail_account(employee['email']);batch=self.real_approved()
        transport=FakeSMTP();app.send_batch(batch['id'],transport,pause=0,confirmation=app.check_send(batch['id'])['digest']);sent=transport.sent[0]
        self.assertEqual(sent['From'],employee['email']);self.assertEqual(app.log_rows()[0]['sender'],employee['email'])
        app.select_mail_account(app.ADDRESS);batch=self.real_approved()
        with self.assertRaises(ValueError):app.send_batch(batch['id'],FakeSMTP(),pause=0)
        review=app.check_send(batch['id']);self.assertEqual(review['recipients'][0]['previous_count'],1)
        app.send_batch(batch['id'],transport,pause=0,confirmation=review['digest'])
        self.assertEqual(transport.sent[1]['From'],app.ADDRESS)
    def test_reply_matching_is_scoped_to_employee(self):
        sent=self.sent();reply=self.reply(sent).as_bytes();employee=self.employee();app.select_mail_account(employee['email'])
        self.assertEqual(app.record_incoming(reply),0);self.assertEqual(app.log_rows()[0]['reply_status'],'')
        app.select_mail_account(app.ADDRESS)
        self.assertEqual(app.record_incoming(reply),1);self.assertEqual(app.record_incoming(reply),0)
        self.assertEqual(app.log_rows()[0]['reply_status'],'已回复')
    def test_imap_reads_selected_employee(self):
        employee=self.employee();app.select_mail_account(employee['email']);sent=self.sent();reply=self.reply(sent,sender='agency@example.com').as_bytes()
        client=Mock();client.select.return_value=('OK',[]);client.uid.side_effect=[('OK',[b'1']),('OK',[(b'1',reply)])]
        app.SETTINGS['password']='dummy-password'
        with patch.object(app.imaplib,'IMAP4_SSL',return_value=client) as factory:result=app.check_inbox()
        client.login.assert_called_once_with(employee['email'],'dummy-password');client.select.assert_called_once_with('INBOX',readonly=True)
        self.assertEqual(factory.call_args.args,('mail1039.onamae.ne.jp',993));self.assertEqual(result['matched'],1)
        self.assertEqual(app.log_rows()[0]['sender'],employee['email'])
    def test_mixed_test_and_real_recipient_batch_is_rejected(self):
        with self.assertRaises(ValueError):app.create_batch({'ids':['TEST-20261002-001','SG-007'],'subject':'x','body':'x'})
    def test_test_template_cannot_target_real_agency(self):
        with self.assertRaises(ValueError):app.create_batch({'ids':['SG-007'],'subject':'x','body':'x','template_id':'test-en'})
        app.create_batch({'ids':['TEST-20261002-001'],'subject':'x','body':'x','template_id':'outreach-zh-TW'})
    def test_template_sender_variables_follow_employee(self):
        employee=self.employee();app.select_mail_account(employee['email'])
        batch=app.create_batch({'ids':['TEST-20261002-001'],'subject':'{{sender_name}}','body':'{{sender_name}} {{sender_email}}'})
        self.assertTrue(batch['messages'][0]['subject'].startswith('同事 [TEST '))
        self.assertEqual(batch['messages'][0]['body'],'同事 colleague@hiwin-japan.co.jp')
    def test_starter_template_upgrade_preserves_user_edits(self):
        template=next(row for row in app.template_rows() if row['id']=='outreach-zh-TW')
        old_body=template['body'].replace('{{sender_name}}',app.DEFAULT_SENDER_NAME)
        with app.db() as c:
            c.execute('UPDATE templates SET body=? WHERE id=?',(old_body,'outreach-zh-TW'))
            c.execute("UPDATE templates SET body='Custom signed message' WHERE id='outreach-en'")
        app.initialize();templates={row['id']:row for row in app.template_rows()}
        self.assertIn('{{sender_name}}',templates['outreach-zh-TW']['body'])
        self.assertEqual(templates['outreach-en']['body'],'Custom signed message')
    def template_preview(self, ident):
        row=next(row for row in app.template_rows() if row['id']==ident)
        return app.create_batch({**row,'ids':['TEST-20261002-001'],'template_id':ident,'attachments':[]})
    def test_taiwan_line_link_and_inline_qr_follow_each_employee(self):
        second=app.save_mail_account({'email':'second.employee@andclan.co.jp','label':'Second Employee'})
        for address,url,file in [(app.ADDRESS,'https://line.me/ti/p/demo-one','employee-one-line.png'),
                                 (second['email'],'https://line.me/ti/p/demo-two','employee-two-line.png')]:
            app.select_mail_account(address);batch=self.template_preview('outreach-zh-TW')
            signature=batch['line_signature'];self.assertEqual(signature['url'],url)
            self.assertEqual(signature['file'],file);self.assertIn('LINE：'+url,batch['messages'][0]['body'])
            app.approve_batch(batch['id'],batch['digest']);transport=FakeSMTP()
            app.send_batch(batch['id'],transport,pause=0);message=transport.sent[0]
            self.assertIn(url,message.get_body(preferencelist=('plain',)).get_content())
            self.assertIn('cid:'+signature['cid'],message.get_body(preferencelist=('html',)).get_content())
            images=[part for part in message.walk() if part.get_content_type()=='image/png']
            self.assertEqual(len(images),1);self.assertEqual(images[0]['Content-ID'],'<'+signature['cid']+'>')
            self.assertEqual(images[0].get_content_disposition(),'inline')
            self.assertEqual(images[0].get_payload(decode=True),app.read_line_qr(signature))
    def test_english_has_shared_whatsapp_inline_qr_for_both_senders(self):
        second=app.save_mail_account({'email':'second.employee@andclan.co.jp'})
        for address in [app.ADDRESS,second['email']]:
            app.select_mail_account(address);batch=self.template_preview('outreach-en')
            self.assertNotIn('line_signature',batch)
            self.assertIn('+81 00-0000-0000',batch['messages'][0]['body'])
            signature=batch['whatsapp_signature']
            self.assertEqual(signature['file'],'hiwin-whatsapp.png')
            self.assertIn(app.DEFAULT_WHATSAPP['url'],batch['messages'][0]['body'])
            app.approve_batch(batch['id'],batch['digest']);transport=FakeSMTP()
            app.send_batch(batch['id'],transport,pause=0)
            message=transport.sent[0]
            images=[part for part in message.walk() if part.get_content_type()=='image/png']
            self.assertEqual(len(images),1)
            self.assertEqual(images[0].get_content_disposition(),'inline')
            self.assertEqual(images[0].get_payload(decode=True),app.read_line_qr(signature))
            self.assertIn('cid:'+signature['cid'],message.get_body(preferencelist=('html',)).get_content())
            self.assertIn('Scan the QR code',batch['messages'][0]['html'])
            self.assertEqual(batch['messages'][0]['body_sections'][0]['kind'],'greeting')
    def test_qr_change_after_approval_prevents_sending(self):
        batch=self.template_preview('outreach-zh-TW');app.approve_batch(batch['id'],batch['digest'])
        transport=FakeSMTP()
        with patch.object(Path,'read_bytes',return_value=b'changed QR'):
            with self.assertRaises(ValueError):app.send_batch(batch['id'],transport,pause=0)
        self.assertEqual(transport.sent,[]);self.assertEqual(app.log_rows(),[])
    def test_test_template_does_not_include_line_qr(self):
        batch=self.template_preview('test-en');self.assertNotIn('line_signature',batch)
    def test_unconfigured_employee_cannot_use_another_employees_line(self):
        employee=self.employee();app.select_mail_account(employee['email'])
        with self.assertRaises(ValueError):self.template_preview('outreach-zh-TW')

    def contact_data(self, shared=True, **changes):
        profile=app.contact_config()
        return {'sender':app.SETTINGS['sender'],'signature_name':profile['signature_name'],
                'contact_email':profile['contact_email'],'line_url':profile.get('line_url',''),
                'line_qr':profile.get('line_qr',''),'use_shared_whatsapp':shared,
                'whatsapp':profile['effective_whatsapp'],**changes}

    def test_custom_contact_persists_shared_and_personal_whatsapp(self):
        second=app.save_mail_account({'email':'second.employee@andclan.co.jp','label':'Second Employee'})
        shared={'phone':'+81 11','url':'https://wa.me/8111','qr':'hiwin-whatsapp.png'}
        app.save_contact_config(self.contact_data(whatsapp=shared,signature_name='Custom Name',contact_email='contact@example.com'))
        app.initialize();batch=self.template_preview('outreach-en')
        self.assertIn('CUSTOM NAME',batch['messages'][0]['body'])
        self.assertIn('contact@example.com',batch['messages'][0]['body'])
        app.select_mail_account(second['email'])
        self.assertEqual(app.contact_config()['effective_whatsapp'],shared)
        own={'phone':'+81 22','url':'https://wa.me/8122','qr':''}
        app.save_contact_config(self.contact_data(shared=False,whatsapp=own))
        self.assertNotIn('whatsapp_signature',self.template_preview('outreach-en'))
        app.select_mail_account(app.ADDRESS)
        self.assertEqual(app.contact_config()['effective_whatsapp'],shared)

    def test_contact_change_invalidates_approved_batch_and_rejects_wrong_sender(self):
        batch=self.template_preview('outreach-en');app.approve_batch(batch['id'],batch['digest'])
        with self.assertRaises(ValueError):app.save_contact_config(self.contact_data(sender='wrong@example.com'))
        app.save_contact_config(self.contact_data())
        transport=FakeSMTP()
        with self.assertRaises(ValueError):app.send_batch(batch['id'],transport,pause=0)
        self.assertEqual(transport.sent,[])

    def test_whatsapp_image_change_after_approval_blocks_send(self):
        batch=self.template_preview('outreach-en');app.approve_batch(batch['id'],batch['digest'])
        with patch.object(Path,'read_bytes',return_value=b'changed'):
            with self.assertRaises(ValueError):app.send_batch(batch['id'],FakeSMTP(),pause=0)
        self.assertEqual(app.log_rows(),[])

    def test_country_website_routes_including_fallback_and_mixed_generic_batch(self):
        for country,code in [('臺灣','tw'),('日本','jp'),('马来西亚','my'),('泰国','th'),('印尼','id'),('菲律宾','ph'),('新加坡','en'),('未知','en')]:
            self.assertEqual(app.partner_website(country),'https://hiwin-partners.com/'+code)
        self.assertNotIn('印尼',app.app_state()['countries'])
        self.assertIn('印度尼西亚',app.app_state()['countries'])
        added=app.add_contact({'country':'印尼','name':'Indonesia Agency','email':'indonesia@example.com'})
        self.assertEqual(added['country'],'印度尼西亚')
        ids=[]
        for index,country in enumerate(['泰国','马来西亚','菲律宾','新加坡']):
            ids.append(app.add_contact({'country':country,'name':country,'email':f'country{index}@example.com'})['id'])
        batch=app.create_batch({'ids':ids,'subject':'Hello','body':'{{partner_website}}','links':'Partner | {{partner_website}}'})
        for message in batch['messages']:
            url=app.partner_website(message['country'])
            self.assertIn(url,message['body']);self.assertEqual(message['links'][0]['url'],url)
            self.assertIn('href="'+url+'"',message['html'])

    def test_legacy_indonesia_country_merge_preserves_existing_template_choice(self):
        base=next(row for row in app.template_rows() if row['id']=='outreach-en')
        legacy=app.save_template({**base,'country':'印度尼西亚','attachments':[]})
        canonical=app.save_template({**base,'id':None,'name':'Indonesia custom','country':'印度尼西亚','attachments':[]})
        with app.db() as c:
            c.execute("UPDATE templates SET country='印尼' WHERE id=?",(legacy['id'],))
            c.execute("INSERT INTO preferences VALUES('country-template:印尼',?)",(legacy['id'],))
            c.execute("INSERT INTO extra_contacts VALUES('legacy-id','Legacy agency','legacy@example.com','印尼',0)")
        app.initialize()
        state=app.app_state()
        self.assertNotIn('印尼',state['countries'])
        self.assertNotIn('印尼',state['country_templates'])
        self.assertEqual(state['country_templates']['印度尼西亚'],canonical['id'])
        self.assertEqual(next(r for r in state['contacts'] if r['id']=='legacy-id')['country'],'印度尼西亚')
        self.assertEqual(next(r for r in state['templates'] if r['id']==legacy['id'])['country'],'印度尼西亚')

    def test_country_template_save_creates_copy_and_remembers_choice(self):
        base=next(row for row in app.template_rows() if row['id']=='outreach-en')
        saved=app.save_template({**base,'country':'泰国','attachments':[]})
        self.assertNotEqual(saved['id'],base['id'])
        app.initialize()
        self.assertEqual(app.app_state()['country_templates']['泰国'],saved['id'])
        row=next(row for row in app.template_rows() if row['id']==saved['id'])
        batch=app.create_batch({**row,'ids':['TEST-20261002-001'],'template_id':row['id']})
        self.assertIn('https://hiwin-partners.com/th',batch['messages'][0]['body'])
        with self.assertRaises(ValueError):app.create_batch({**row,'ids':['SG-007'],'template_id':row['id']})
        self.assertEqual(next(row for row in app.template_rows() if row['id']==base['id'])['country'],'')

    def test_delete_country_template_clears_mapping_and_survives_restart(self):
        base=next(row for row in app.template_rows() if row['id']=='outreach-en')
        saved=app.save_template({**base,'country':'泰国','attachments':[]})
        app.delete_template(saved)
        app.initialize();state=app.app_state()
        self.assertNotIn(saved['id'],[row['id'] for row in state['templates']])
        self.assertNotIn('泰国',state['country_templates'])
        self.assertIn(state['preferred_template'],[row['id'] for row in state['templates']])
        self.assertIn('outreach-en',[row['id'] for row in state['templates']])

    def test_deleted_starter_does_not_return_after_restart_or_accept_stale_save(self):
        row=next(row for row in app.template_rows() if row['id']=='outreach-en')
        app.delete_template({'id':row['id']});app.initialize()
        self.assertNotIn(row['id'],[item['id'] for item in app.template_rows()])
        with self.assertRaises(ValueError):app.save_template(row)
        with self.assertRaises(ValueError):self.template_preview_deleted(row)
        with self.assertRaises(ValueError):app.delete_template({'id':row['id']})

    def template_preview_deleted(self,row):
        return app.create_batch({**row,'ids':['TEST-20261002-001'],'template_id':row['id'],'attachments':[]})

    def test_delete_template_invalidates_approval_but_preserves_history_and_files(self):
        upload=app.upload_attachment({'name':'brochure.pdf','content':base64.b64encode(b'demo brochure').decode()})
        row=next(row for row in app.template_rows() if row['id']=='outreach-en')
        app.save_template({**row,'attachments':[upload['id']]})
        self.sent();history=app.log_rows()
        batch=self.template_preview('outreach-en');app.approve_batch(batch['id'],batch['digest'])
        app.delete_template({'id':'outreach-en'})
        self.assertEqual(app.log_rows(),history)
        self.assertEqual(app.read_attachment(app.attachment_metadata([upload['id']])[0]),b'demo brochure')
        smtp=FakeSMTP()
        with self.assertRaises(ValueError):app.send_batch(batch['id'],smtp,pause=0)
        self.assertEqual(smtp.sent,[])

    def test_delete_last_template_is_rejected(self):
        for row in app.template_rows()[1:]:app.delete_template({'id':row['id']})
        row=app.template_rows()[0]
        with self.assertRaises(ValueError):app.delete_template({'id':row['id']})
        self.assertEqual(len(app.template_rows()),1)

    def test_template_delete_http_dispatch(self):
        handler=self.http_handler('localhost:8765','http://localhost:8765')
        handler.path='/api/template/delete'
        with patch.object(app,'delete_template',return_value={'ok':True}) as remove:handler.do_POST()
        remove.assert_called_once_with({'sender':app.ADDRESS,'id':'example-batch'})
        handler.respond.assert_called_with({'ok':True})

    def test_delete_template_with_active_schedule_requires_cancel(self):
        app.SETTINGS['password']='session-test-only'
        app.MAIL_SESSIONS[app.SETTINGS['sender']]='session-test-only'
        batch=self.template_preview('outreach-en');app.approve_batch(batch['id'],batch['digest'])
        with patch.object(app,'now',return_value='2030-02-01T12:00:00+09:00'):
            review=app.check_send(batch['id'],'2030-02-01T13:00')
            app.schedule_batch(batch['id'],'2030-02-01T13:00',review['digest'])
        for status in ('scheduled','awaiting_mail','running','needs_review','missed'):
            with app.db() as c:c.execute('UPDATE schedules SET status=?',(status,))
            with self.assertRaises(ValueError):app.delete_template({'id':'outreach-en'})
        with app.db() as c:c.execute("UPDATE schedules SET status='scheduled'")
        app.cancel_schedule(batch['id'])
        app.delete_template({'id':'outreach-en'})
        self.assertNotIn('outreach-en',[r['id'] for r in app.template_rows()])

    def test_taiwan_english_has_attachments_copy_line_and_english_signature(self):
        batch=self.template_preview('outreach-en-TW')
        message=batch['messages'][0]
        self.assertIn('English-language brochure and detailed rate sheet',message['body'])
        self.assertIn('follow up by phone',message['body'])
        self.assertIn('https://hiwin-partners.com/tw',message['body'])
        self.assertIn('Best regards,',message['body'])
        self.assertIn('divider',[part['kind'] for part in message['body_sections']])
        self.assertEqual(batch['line_signature']['channel'],'LINE')
        self.assertNotIn('whatsapp_signature',batch)
        self.assertNotIn('台湾',app.app_state()['country_templates'])

    def test_taiwan_english_without_line_qr_does_not_add_whatsapp(self):
        app.save_contact_config(self.contact_data(line_qr=''))
        batch=self.template_preview('outreach-en-TW')
        self.assertNotIn('line_signature',batch)
        self.assertNotIn('whatsapp_signature',batch)

    def test_legacy_partner_url_migration_preserves_custom_copy(self):
        with app.db() as c:
            c.execute("UPDATE templates SET body='Custom copy https://hiwin-partners.cnai5002.chatgpt.site/',links='Partner | https://hiwin-partners.com/en' WHERE id='outreach-en'")
        app.initialize()
        template=next(row for row in app.template_rows() if row['id']=='outreach-en')
        self.assertEqual(template['body'],'Custom copy {{partner_website}}')
        self.assertEqual(template['links'],'Partner | {{partner_website}}')
        self.assertEqual(app.contact_variables('See http://hiwin-partners.com and https://hiwin-partners.com/'),'See {{partner_website}} and {{partner_website}}')

    def test_contact_qr_upload_is_served_and_used_as_inline_image(self):
        content=(app.ROOT/'assets'/'hiwin-whatsapp.png').read_bytes()
        file='contact-qr-'+app.hashlib.sha256(content).hexdigest()+'.png'
        path=app.ROOT/'assets'/file;existed=path.exists()
        try:
            uploaded=app.upload_contact_qr({'content':base64.b64encode(content).decode()})
            self.assertEqual(uploaded['file'],file);self.assertIn(file,app.qr_files())
            app.save_contact_config(self.contact_data(whatsapp={**app.DEFAULT_WHATSAPP,'qr':file}))
            batch=self.template_preview('outreach-en')
            app.approve_batch(batch['id'],batch['digest']);transport=FakeSMTP()
            app.send_batch(batch['id'],transport,pause=0)
            images=[part for part in transport.sent[0].walk() if part.get_content_maintype()=='image']
            self.assertEqual(len(images),1);self.assertEqual(images[0].get_payload(decode=True),content)
        finally:
            if not existed:path.unlink(missing_ok=True)

    def test_custom_contact_rejects_unsafe_links_and_unuploaded_qr(self):
        with self.assertRaises(ValueError):app.save_contact_config(self.contact_data(line_url='javascript:alert(1)'))
        with self.assertRaises(ValueError):app.save_contact_config(self.contact_data(line_qr='../app.py'))
        with self.assertRaises(ValueError):app.upload_contact_qr({'content':base64.b64encode(b'not an image').decode()})

    def test_removing_line_qr_keeps_taiwan_text_layout_and_contact_link(self):
        app.save_contact_config(self.contact_data(line_qr=''))
        batch=self.template_preview('outreach-zh-TW')
        self.assertNotIn('line_signature',batch)
        self.assertNotIn('whatsapp_signature',batch)
        self.assertEqual(batch['messages'][0]['body_sections'][0]['kind'],'greeting')
        self.assertIn('LINE：https://line.me/ti/p/demo-one',batch['messages'][0]['body'])
        self.assertIn('font-size:16px;font-weight:400',batch['messages'][0]['html'])

    def test_test_subjects_are_unique_and_frozen_for_approval_and_logs(self):
        first=self.template_preview('outreach-zh-TW')
        second=self.template_preview('outreach-zh-TW')
        self.assertNotEqual(first['messages'][0]['subject'],second['messages'][0]['subject'])
        app.approve_batch(first['id'],first['digest']);transport=FakeSMTP()
        app.send_batch(first['id'],transport,pause=0)
        subject=first['messages'][0]['subject']
        self.assertEqual(str(transport.sent[0]['Subject']),subject)
        self.assertEqual(app.log_rows()[0]['subject'],subject)
        formal=app.create_batch({'ids':['SG-007'],'subject':'HIWIN 日本住宿合作','body':'Hello'})
        self.assertEqual(formal['messages'][0]['subject'],'HIWIN 日本住宿合作')

    def test_html_line_breaks_are_explicit_in_sent_mime(self):
        for separator in ('\n','\r\n','\r'):
            with self.subTest(separator=repr(separator)):
                body=separator.join(['第一段 HIWIN','','第二段','https://example.com/?x=1&y=2','','<第三段>'])
                batch=app.create_batch({'ids':['TEST-20261002-001'],'subject':'Line break test','body':body})
                app.approve_batch(batch['id'],batch['digest'])
                transport=FakeSMTP()
                app.send_batch(batch['id'],transport,pause=0)
                message=transport.sent[0]
                rendered=message.get_body(preferencelist=('html',)).get_content()
                self.assertIn('第一段 <strong>HIWIN</strong><br><br>第二段<br><a href=',rendered)
                self.assertIn('</a><br><br>&lt;第三段&gt;',rendered)
                self.assertNotIn('white-space:',rendered)
                self.assertNotIn('第二段\n',rendered)
                self.assertIn('第一段 HIWIN\n\n第二段\n',message.get_body(preferencelist=('plain',)).get_content())

    def test_brand_emphasis_preserves_urls_emails_and_escapes_recipient_text(self):
        body='HIWIN operates Apartment hotel 11.\nhttps://example.com/HIWIN?x=1&y=2\nHIWIN@example.com\n<script>HIWIN</script>'
        rendered=app.email_html(body)
        self.assertIn('<strong>HIWIN</strong> operates <strong>Apartment hotel 11</strong>',rendered)
        self.assertIn('href="https://example.com/HIWIN?x=1&amp;y=2"',rendered)
        self.assertNotIn('<strong>HIWIN</strong>@example.com',rendered)
        self.assertNotIn('<script>',rendered)
        self.assertIn('&lt;script&gt;',rendered)
        full_names=app.email_html('株式会社 HIWIN 營運 Apartment Hotel 11.\nHIWIN Co., Ltd. operates Apartment Hotel 11.')
        self.assertIn('<strong>株式会社 HIWIN</strong>',full_names)
        self.assertIn('<strong>HIWIN Co., Ltd.</strong>',full_names)

    def test_markdown_formats_preview_and_sent_html_with_clean_plain_text(self):
        source='Dear {{agency_name}} Team,\n\n## 大阪住宿合作\n\n**重点房型**与*旅行需求*，***欢迎联系***。\n\nBest regards,\n{{sender_name}}'
        batch=app.create_batch({'ids':['TEST-20261002-001'],'subject':'Formatting','body':source})
        row=batch['messages'][0]
        self.assertIn('<h2 ',row['html']);self.assertIn('>大阪住宿合作</h2>',row['html'])
        self.assertIn('<strong>重点房型</strong>',row['html'])
        self.assertIn('<em>旅行需求</em>',row['html'])
        self.assertIn('<strong><em>欢迎联系</em></strong>',row['html'])
        heading=next(part for part in row['body_sections'] if part['kind']=='heading')
        self.assertEqual(heading['level'],2);self.assertEqual(heading['runs'],[{'text':'大阪住宿合作'}])
        self.assertIn('大阪住宿合作\n\n重点房型与旅行需求，欢迎联系。',row['body'])
        self.assertNotIn('**',row['body']);self.assertNotIn('## ',row['body'])
        app.approve_batch(batch['id'],batch['digest']);smtp=FakeSMTP()
        app.send_batch(batch['id'],smtp,pause=0)
        self.assertEqual(smtp.sent[0].get_body(preferencelist=('plain',)).get_content().rstrip(),row['body'].rstrip())
        self.assertIn('<strong>重点房型</strong>',smtp.sent[0].get_body(preferencelist=('html',)).get_content())
        self.assertEqual(app.log_rows()[0]['body'],row['body'])

    def test_manual_styles_on_links_preserve_url_and_both_styles(self):
        body='**https://example.com/path?x=1&y=2** and *https://example.com/italic*\nhttps://example.com/a**b**\nname*tag@example.com'
        rendered=app.email_html(body)
        self.assertIn('<strong><a href="https://example.com/path?x=1&amp;y=2">',rendered)
        self.assertIn('<em><a href="https://example.com/italic">',rendered)
        self.assertIn('href="https://example.com/a**b**"',rendered)
        self.assertIn('name*tag@example.com',rendered)
        self.assertIn('https://example.com/a**b**',app.plain_text_body(body))

    def test_nested_markdown_and_html_are_safe(self):
        rendered=app.email_html('**bold with *italic* inside**\n**<img src=x onerror=alert(1)>**\n## <script>title</script>')
        self.assertIn('<strong><em>italic</em></strong>',rendered)
        self.assertNotIn('<img',rendered);self.assertNotIn('<script>',rendered)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;',rendered)
        self.assertIn('&lt;script&gt;title&lt;/script&gt;',rendered)

    def test_unfinished_markup_and_heading_without_space_stay_literal(self):
        source='**unfinished\n* spaced *\n##Not a heading\n#### Not supported'
        self.assertEqual(app.plain_text_body(source),source)
        self.assertNotIn('<strong>',app.email_html(source))
        self.assertNotIn('<h',app.email_html(source).split('<body>')[1])

    def test_formatted_template_persists_and_approval_freezes_rendered_content(self):
        base=next(row for row in app.template_rows() if row['id']=='outreach-en')
        saved=app.save_template({**base,'body':'## Osaka stays\n**Room options** and *family trips*','attachments':[]})
        app.initialize()
        stored=next(row for row in app.template_rows() if row['id']==saved['id'])
        self.assertIn('**Room options**',stored['body'])
        batch=self.template_preview(saved['id']);app.approve_batch(batch['id'],batch['digest'])
        app.save_template({**stored,'body':'**New copy**','attachments':[]})
        smtp=FakeSMTP();app.send_batch(batch['id'],smtp,pause=0)
        rendered=smtp.sent[0].get_body(preferencelist=('html',)).get_content()
        self.assertIn('<strong>Room options</strong>',rendered)
        self.assertNotIn('New copy',rendered)

if __name__=='__main__':unittest.main()
