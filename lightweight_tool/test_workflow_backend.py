"""Isolated acceptance tests for activity, mailbox evidence and stop reasons."""
import json
import unittest
from email.message import EmailMessage
from unittest.mock import patch

import app
import test_app as fixtures


class WorkflowBackendTests(unittest.TestCase):
    # Reuse the existing temporary directory and harmless one-pixel test assets.
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    delivery_notice = fixtures.WorkflowTests.delivery_notice
    cloud_contacts = fixtures.WorkflowTests.cloud_contacts
    http_handler = fixtures.WorkflowTests.http_handler

    def batch(self, activity='itf-2026', ids=None, template_id=None):
        data={'ids':ids or ['SG-007'],'activity_id':activity,'subject':'Invitation','body':'Hello'}
        if template_id:data['template_id']=template_id
        batch=app.create_batch(data)
        app.approve_batch(batch['id'],batch['digest'])
        return batch

    def send(self, batch, error=None):
        transport=fixtures.FakeSMTP(error=error)
        app.send_batch(batch['id'],transport,pause=0,confirmation=app.check_send(batch['id'])['digest'])
        return transport

    def metadata(self):
        value={'contacts':{
            'SG-007':{'company_id':'COMPANY-1','company_name':'Example Company','email':'partner7@example.com',
                      'email_role':'公共邮箱','contact_name':'','contact_title':'','phone':'+65 0000'},
            'SG-012':{'company_id':'COMPANY-1','company_name':'Example Company','email':'partner12@example.com',
                      'email_role':'负责人邮箱','contact_name':'Manager','contact_title':'日本线负责人','phone':'+65 0001'}}}
        (app.ROOT/'contact_metadata.json').write_text(json.dumps(value))

    def test_activity_and_template_identity_are_frozen_after_template_changes(self):
        saved=app.save_template({'name':'ITF Invitation','language':'en','subject':'Invitation','body':'Hello',
                                 'activity_id':'itf-2026'})
        batch=self.batch(template_id=saved['id'])
        self.assertEqual(batch['activity_name'],'ITF 2026 展位邀约')
        app.save_template({'id':saved['id'],'name':'Changed template','language':'en','subject':'Changed','body':'Changed',
                           'activity_id':'first-contact'})
        self.send(batch)
        row=app.log_rows()[0]
        self.assertEqual((row['activity_id'],row['template_name'],row['subject']),
                         ('itf-2026','ITF Invitation','Invitation'))
        self.assertEqual(next(row for row in app.template_rows() if row['id']==saved['id'])['activity_id'],'first-contact')
        with self.assertRaises(ValueError):
            app.save_template({'name':'Wrong','language':'en','subject':'s','body':'b','activity_id':'unknown'})

    def test_activity_country_preference_preserves_other_purpose_choice(self):
        first=app.save_template({'name':'Introduction','language':'zh-TW','subject':'s','body':'b',
                                 'country':'台湾','activity_id':'first-contact'})
        followup=app.save_template({'name':'Follow up','language':'zh-TW','subject':'s','body':'b',
                                    'country':'台湾','activity_id':'responsible-followup'})
        invitation=app.save_template({'name':'ITF','language':'zh-TW','subject':'s','body':'b',
                                      'country':'台湾','activity_id':'itf-2026'})
        state=app.app_state()
        self.assertEqual(state['country_templates']['台湾'],first['id'])
        self.assertEqual(state['activity_country_templates']['responsible-followup']['台湾'],followup['id'])
        self.assertEqual(state['activity_country_templates']['itf-2026']['台湾'],invitation['id'])
        app.delete_template({'id':invitation['id']})
        self.assertNotIn('itf-2026',app.app_state()['activity_country_templates'])

    def test_legacy_activity_is_never_inferred_and_approved_payload_stays_unchanged(self):
        payload={'sender':app.ADDRESS,'messages':[{'contact_id':'SG-007','name':'Example Company',
                 'recipient':'partner7@example.com','subject':'ITF Taiwan J3302','body':'ITF 2026 invitation',
                 'country':'新加坡','message_type':'formal'}]}
        raw=json.dumps(payload)
        with app.db() as c:
            c.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',('legacy',raw,app.digest(payload),app.digest(payload),'approved',app.now()))
            c.execute('INSERT INTO messages(id,batch_id,contact_id,name,recipient,subject,body,status,sent_at) VALUES(?,?,?,?,?,?,?,?,?)',
                      ('<legacy@example.com>','legacy','SG-007','Example Company','partner7@example.com','ITF Taiwan J3302','ITF 2026 invitation','服务器已接受',app.now()))
        app.initialize();app.initialize()
        row=app.log_rows()[0]
        self.assertEqual(row['activity_id'],'')
        self.assertEqual(row['activity_name'],'历史发送（活动未标记）')
        self.assertIsNone(row['reply_detail'])
        with app.db() as c:
            self.assertEqual(c.execute('SELECT payload FROM batches WHERE id="legacy"').fetchone()[0],raw)
            _,actual=app.approved_payload(c,'legacy')
        self.assertEqual(actual,payload)
        check=app.check_send(self.batch()['id'])
        self.assertEqual((check['historical_count'],check['current_activity_count']),(1,0))

    def test_company_association_does_not_mark_other_mailbox_as_sent(self):
        self.metadata()
        self.send(self.batch())
        roster={row['id']:row for row in app.contacts()}
        self.assertEqual(roster['SG-007']['history']['by_activity']['itf-2026']['accepted_count'],1)
        self.assertEqual(roster['SG-012']['history']['accepted_count'],0)
        self.assertEqual(roster['SG-012']['company_id'],roster['SG-007']['company_id'])
        self.assertEqual(roster['SG-012']['contact_name'],'Manager')
        check=app.check_send(self.batch(ids=['SG-007','SG-012'])['id'])
        self.assertEqual(len(check['same_company_warnings']),1)
        self.assertEqual(check['same_company_warnings'][0]['company_id'],'COMPANY-1')
        self.assertEqual([row['accepted_count'] for row in check['recipients']],[1,0])

    def test_legacy_message_ids_never_become_company_ids_and_local_contact_can_merge(self):
        self.metadata()
        local=app.add_contact({'name':'Example Company','country':'新加坡','email':'local-manager@example.com'})
        path=app.ROOT/'contact_metadata.json'
        metadata=json.loads(path.read_text())
        metadata['contacts'][local['id']]={'company_id':'COMPANY-1','company_name':'Example Company',
                                           'email':local['email'],'email_role':'负责人邮箱','contact_name':'Local Manager'}
        path.write_text(json.dumps(metadata))
        self.send(self.batch())
        self.send(self.batch())
        self.send(self.batch(ids=[local['id']]))
        with app.db() as c:
            c.execute("UPDATE messages SET company_id='',email_role='',contact_name='',contact_title=''")
        rows=app.log_rows()
        self.assertEqual(len({row['id'] for row in rows}),3)
        self.assertEqual({row['company_id'] for row in rows},{'COMPANY-1'})
        self.assertEqual({row['company_name'] for row in rows},{'Example Company'})
        self.assertEqual(next(row for row in rows if row['contact_id']==local['id'])['contact_name'],'Local Manager')
        self.assertTrue(all(row['company_id']!=row['id'] for row in rows))
        self.assertEqual(app.contact_identity({'id':'<orphan@example.com>','contact_id':''}, {})['company_id'],'')

    def test_entity_type_uses_explicit_metadata_and_keeps_unknown_contacts_unconfirmed(self):
        self.assertEqual(app.contact_identity({'id':'NEW','country':'台湾'}, {})['entity_type'],'unconfirmed')
        self.assertEqual(app.contact_identity({'id':'TEST','country':'测试'}, {})['entity_type'],'test')
        self.assertEqual(app.contact_identity({'id':'KNOWN','country':'台湾','entity_type':'internal'}, {})['entity_type'],'internal')
        self.assertEqual(app.contact_identity({'id':'UNKNOWN','entity_type':'invalid'}, {})['entity_type'],'unconfirmed')
        self.metadata()
        path=app.ROOT/'contact_metadata.json'
        metadata=json.loads(path.read_text())
        metadata['contacts']['SG-007']['entity_type']='external_company'
        path.write_text(json.dumps(metadata))
        self.assertEqual(next(row for row in app.contacts() if row['id']=='SG-007')['entity_type'],'external_company')
        self.send(self.batch())
        self.assertEqual(app.log_rows()[0]['entity_type'],'external_company')
        self.assertEqual(app.log_rows()[0]['message_type'],'formal')

    def test_accepted_hidden_history_survives_later_failed_resend(self):
        self.send(self.batch())
        accepted=app.log_rows()[0]
        app.delete_records([accepted['id']])
        self.send(self.batch(),error=app.smtplib.SMTPDataError(550,b'rejected'))
        self.assertEqual(app.log_rows()[0]['status'],'发送失败')
        history=next(row for row in app.contacts() if row['id']=='SG-007')['history']
        self.assertEqual(history['last_status'],'发送失败')
        self.assertEqual(history['by_activity']['itf-2026']['accepted_count'],1)
        check=app.check_send(self.batch()['id'])
        self.assertEqual(check['recipients'][0]['current_activity_count'],1)
        self.assertEqual(check['current_activity_count'],1)

    def test_activity_history_includes_uncertain_and_pending_separately(self):
        self.send(self.batch(),error=TimeoutError('connection lost after DATA'))
        contact=next(row for row in app.contacts() if row['id']=='SG-007')
        self.assertEqual(contact['history']['by_activity']['itf-2026']['uncertain_count'],1)
        batch=self.batch()
        with app.db() as c:
            app.insert_message_rows(c,batch['id'],batch,app.contacts(),scheduled_at='2030-02-01T13:00:00+09:00')
        contact=next(row for row in app.contacts() if row['id']=='SG-007')
        bucket=contact['history']['by_activity']['itf-2026']
        self.assertEqual((bucket['accepted_count'],bucket['pending_count'],bucket['uncertain_count']),(0,1,1))

    def test_other_activity_unresolved_history_is_visible_in_final_confirmation(self):
        self.send(self.batch(activity='first-contact'),error=TimeoutError('connection lost after DATA'))
        pending=self.batch(activity='responsible-followup')
        with app.db() as c:
            app.insert_message_rows(c,pending['id'],pending,app.contacts(),scheduled_at='2030-02-01T13:00:00+09:00')
        invitation=self.batch()
        review=app.check_send(invitation['id'])
        row=review['recipients'][0]
        self.assertEqual((row['current_activity_count'],row['current_activity_pending'],row['current_activity_uncertain']),(0,0,0))
        self.assertEqual((row['historical_pending_count'],row['historical_uncertain_count']),(1,1))
        self.assertEqual(review['historical_unresolved_count'],1)
        with app.db() as c:
            c.execute('UPDATE messages SET status="已取消定时" WHERE batch_id=?',(pending['id'],))
        self.assertNotEqual(app.check_send(invitation['id'])['digest'],review['digest'])

    def test_manual_stop_preserves_sending_evidence_and_invalidates_review(self):
        self.send(self.batch(activity='first-contact'))
        before=app.log_rows()[0]
        batch=self.batch()
        review=app.check_send(batch['id'])
        app.stop_contact({'email':'partner7@example.com','reason':'manual'})
        after=app.log_rows()[0]
        self.assertEqual((after['status'],after['sent_at'],after['reply_status']),
                         (before['status'],before['sent_at'],before['reply_status']))
        self.assertTrue(after['confirmed_sent'])
        self.assertEqual(after['stop_reason'],'manual')
        self.assertNotEqual(app.check_send(batch['id'])['digest'],review['digest'])
        with self.assertRaises(ValueError):
            app.send_batch(batch['id'],fixtures.FakeSMTP(),pause=0,confirmation=review['digest'])
        with self.assertRaises(ValueError):app.stop_contact({'email':'partner7@example.com','reason':'human_reply'})

    def test_new_reply_keeps_header_and_body_evidence_separate_from_send_result(self):
        sent=self.send(self.batch()).sent[0]
        reply=EmailMessage()
        reply['From']='Actual Manager <manager@example.net>'
        reply['To']=app.ADDRESS
        reply['Subject']='Re: Invitation'
        reply['Date']='Tue, 13 Oct 2026 09:00:00 +0800'
        reply['Message-ID']='<actual-reply@example.net>'
        reply['In-Reply-To']=sent['Message-ID']
        reply.set_content('Please meet us on Friday at 11:00.')
        self.assertEqual(app.record_incoming(reply.as_bytes()),1)
        self.assertEqual(app.record_incoming(reply.as_bytes()),0)
        row=app.log_rows()[0]
        self.assertEqual((row['send_status'],row['reply_status'],row['stop_reason']),
                         ('服务器已接受','已回复','human_reply'))
        evidence=row['reply_detail']
        self.assertEqual(evidence['text'],'Please meet us on Friday at 11:00.')
        self.assertEqual(evidence['sender'],'Actual Manager <manager@example.net>')
        self.assertEqual(evidence['received_at'],str(reply['Date']))
        self.assertEqual(evidence['message_id'],'<actual-reply@example.net>')
        self.assertEqual(evidence['match_reason'],'邮件引用匹配')
        self.assertTrue(evidence['detected_at'])
        self.assertNotEqual(evidence['received_at'],evidence['detected_at'])

    def test_invalid_old_mailbox_stop_does_not_transfer_to_replacement(self):
        self.metadata()
        sent=self.send(self.batch(ids=['SG-012'])).sent[0]
        raw=self.delivery_notice(sent,recipient='partner12@example.com')
        self.assertEqual(app.record_incoming(raw),1)
        self.assertEqual(app.log_rows()[0]['stop_reason'],'address_invalid')
        rows=[{'id':'SG-012','name':'Example Company','country':'新加坡','email':'replacement@example.com','blocked':False}]
        with patch.object(app,'sheet_bridge_request',return_value=self.cloud_contacts(rows)):
            app.refresh_sheet_contacts()
        replacement=next(row for row in app.contacts() if row['id']=='SG-012')
        self.assertFalse(replacement['blocked'])
        self.assertEqual(replacement['company_id'],'COMPANY-1')
        self.assertEqual((replacement['email_role'],replacement['contact_name']),('未核实',''))
        self.assertEqual(replacement['history']['accepted_count'],0)
        with app.db() as c:
            self.assertTrue(c.execute('SELECT 1 FROM stops WHERE email="partner12@example.com"').fetchone())

    def test_legacy_ambiguous_stop_remains_blocked_and_known_reply_can_be_named(self):
        self.send(self.batch())
        with app.db() as c:
            c.execute('INSERT INTO stops VALUES(?)',('partner7@example.com',))
            c.execute('INSERT INTO stops VALUES(?)',('partner12@example.com',))
            c.execute('UPDATE messages SET reply_status="已回复" WHERE recipient="partner7@example.com"')
        app.initialize()
        roster={row['id']:row for row in app.contacts()}
        self.assertEqual(roster['SG-007']['stop_reason'],'human_reply')
        self.assertEqual(roster['SG-012']['stop_reason'],'legacy_unknown')
        self.assertTrue(roster['SG-012']['blocked'])
        self.assertIsNone(app.log_rows()[0]['reply_detail'])

    def test_scheduled_new_stop_reason_requires_review_and_never_sends(self):
        app.SETTINGS['password']='test-only'
        with app.db() as c:app.record_stop(c,'partner7@example.com','human_reply')
        batch=self.batch()
        with patch.object(app,'now',return_value='2030-02-01T12:00:00+09:00'):
            review=app.check_send(batch['id'],'2030-02-01T13:00')
            app.schedule_batch(batch['id'],'2030-02-01T13:00',review['digest'])
        app.stop_contact({'email':'partner7@example.com'})
        factory=fixtures.Mock()
        app.process_schedules(current_time='2030-02-01T13:00:00+09:00',transport_factory=factory,pause=0)
        factory.assert_not_called()
        self.assertEqual(app.log_rows()[0]['status'],'待重新确认')

    def test_records_api_refreshes_contact_history_with_message_facts(self):
        self.send(self.batch())
        handler=self.http_handler('localhost:8765')
        handler.path='/api/records'
        handler.do_GET()
        value=handler.respond.call_args.args[0]
        self.assertEqual({row['id'] for row in value['activities']},{'itf-2026','first-contact','responsible-followup'})
        self.assertEqual(next(row for row in value['contacts'] if row['id']=='SG-007')['history']['accepted_count'],1)
        self.assertEqual(value['messages'][0]['activity_id'],'itf-2026')


if __name__=='__main__':
    unittest.main()
