#!/usr/bin/env python3
"""Local, approval-first SMTP outbox. Standard library only."""
import argparse, base64, hashlib, hmac, imaplib, json, os, re, secrets
import smtplib, sqlite3, ssl, threading, time, urllib.request, urllib.parse
import html, mimetypes, subprocess
from datetime import datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid, parseaddr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
LOCAL_CONFIG = json.loads((ROOT/'local_config.json').read_text()) if (ROOT/'local_config.json').exists() else {}
ADDRESS = LOCAL_CONFIG.get('sender_email') or 'employee@hiwin-japan.co.jp'
DEFAULT_SENDER_NAME = LOCAL_CONFIG.get('sender_name') or 'Employee'
# Both company domains were observed signed in at webmail1039.onamae.ne.jp.
# Do not route an unverified domain's credentials to this shared SMTP server.
COMPANY_MAIL_DOMAINS = frozenset({'hiwin-japan.co.jp', 'andclan.co.jp'})
SPREADSHEET = os.environ.get('HIWIN_SPREADSHEET_ID') or LOCAL_CONFIG.get('spreadsheet_id', '')
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
SYNC_LOCK = threading.Lock()
# Verified credentials stay scoped to their employee for this process only.
MAIL_SESSIONS = {}
SCHEDULER_STOP = threading.Event()
SCHEDULE_PENDING = ('scheduled', 'awaiting_mail')
SCHEDULE_CANCELABLE = (*SCHEDULE_PENDING, 'needs_review', 'missed')
SETTINGS = {'sender': ADDRESS, 'smtp_host': 'mail1039.onamae.ne.jp', 'smtp_port': 465,
            'imap_host': 'mail1039.onamae.ne.jp', 'imap_port': 993,
            'password': '', 'bridge_url': '', 'bridge_secret': '',
            'sheet_saved': False, 'sheet_store_error': ''}
DB_PATH = ROOT / 'outbox.sqlite3'
EMAIL_RE = re.compile(r'^[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
BRIDGE_URL_RE = re.compile(r'https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec')
KEYCHAIN_SERVICE = 'jp.hiwin.local-mail.sheet-bridge'
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_SHEET_REQUEST_BYTES = 250000
MAX_SHEET_CHUNK_ROWS = 40
DEFAULT_WHATSAPP = {'phone': '', 'url': '', 'qr': '', **LOCAL_CONFIG.get('default_whatsapp', {})}
WEBSITE_ROUTES = {'台湾':'tw', '日本':'jp', '马来西亚':'my', '泰国':'th', '印度尼西亚':'id', '菲律宾':'ph'}
WEBSITE_ALIASES = {'taiwan':'台湾','japan':'日本','malaysia':'马来西亚','thailand':'泰国',
                   'indonesia':'印度尼西亚','philippines':'菲律宾','馬來西亞':'马来西亚','泰國':'泰国',
                   '菲律賓':'菲律宾'}
LOCAL_TIMEZONES = {'台湾':'Asia/Taipei','日本':'Asia/Tokyo','新加坡':'Asia/Singapore',
                  '马来西亚':'Asia/Kuala_Lumpur','泰国':'Asia/Bangkok',
                  '印度尼西亚':'Asia/Jakarta','菲律宾':'Asia/Manila','测试':'Asia/Tokyo'}
ACTIVITIES = (
    {'id':'itf-2026','name':'ITF 2026 展位邀约','purpose':'邀请参展商到 HIWIN J3302 展位交流',
     'recommended_group_ids':['itf-2026-agencies','itf-2026-related','itf-2026-airlines'],
     'template_intent':'展位邀约'},
    {'id':'first-contact','name':'首次合作介绍','purpose':'介绍公司与住宿产品，建立合作联系',
     'recommended_group_ids':[],'template_intent':'合作介绍'},
    {'id':'responsible-followup','name':'负责人跟进','purpose':'向已核实的负责人继续沟通合作事项',
     'recommended_group_ids':[],'template_intent':'负责人跟进'},
)
LEGACY_ACTIVITY_NAME = '历史发送（活动未标记）'
STOP_REASONS = {
    'human_reply':('已回复，转人工跟进','查看来信并人工跟进，确认后再联系'),
    'address_invalid':('旧地址失效','通过电话／官网核实替代邮箱，暂停向旧地址发送'),
    'manual':('手动停止联系','核对停止联系原因及对方意愿，再决定后续处理'),
    'legacy_unknown':('历史停止标记，原因待核对','核对历史记录及停止原因，再决定是否联系'),
}
PENDING_MESSAGE_STATES = frozenset({'待发送','发送中','待定时发送','待连接邮箱','待重新确认'})
SHEET_STOP = {'stop_reason':'manual','stop_label':'表格标记勿再联系','stop_next_action':STOP_REASONS['manual'][1]}

def empty_history():
    return {'accepted_count':0,'last_sent_at':'','last_status':'','reply_status':'','reply_at':'','attempt_count':0,'by_activity':{}}

def set_pref(c, key, value, table='preferences'):
    c.execute(f'INSERT INTO {table} VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))

def invalidate_drafts(c):
    c.execute("UPDATE batches SET status='invalidated',approved_digest=NULL WHERE status IN ('draft','approved')")

def session_password(sender):
    return MAIL_SESSIONS.get(sender) or (SETTINGS['password'] if sender==SETTINGS['sender'] else '')

def activity_rows():
    return [{**row,'recommended_group_ids':list(row['recommended_group_ids'])} for row in ACTIVITIES]

def activity_fields(ident, allow_empty=False):
    ident=str(ident or '')
    if not ident and allow_empty:
        return {'activity_id':'','activity_name':LEGACY_ACTIVITY_NAME,'activity_purpose':''}
    found=next((row for row in ACTIVITIES if row['id']==ident),None)
    if not found:
        raise ValueError('请选择有效的发信活动')
    return {'activity_id':ident,'activity_name':found['name'],'activity_purpose':found['purpose']}

def contact_metadata():
    """Local annotations supplement the existing four-field Sheet bridge."""
    try:
        value=json.loads((ROOT/'contact_metadata.json').read_text(encoding='utf-8'))
    except (FileNotFoundError,OSError,UnicodeError,ValueError):
        return {}
    value=value.get('contacts',value) if isinstance(value,dict) else value
    if isinstance(value,list):
        value={row['id']:row for row in value if isinstance(row,dict) and isinstance(row.get('id'),str)}
    return value if isinstance(value,dict) else {}

def contact_identity(row, metadata=None):
    # Message rows have their own Message-ID in `id`; their contact association
    # must use `contact_id`, while roster rows use the contact's `id`.
    contact_id=(row.get('contact_id') or '') if 'contact_id' in row else (row.get('id') or '')
    item=(metadata if metadata is not None else contact_metadata()).get(contact_id, {})
    item=item if isinstance(item,dict) else {}
    # A contact ID can keep its company relationship when its address changes,
    # but a person's role/name must not be borrowed by a replacement mailbox.
    bound_email=str(item.get('email','')).strip().lower()
    current_email=str(row.get('email',row.get('recipient','')) or '').strip().lower()
    person=item if not bound_email or bound_email==current_email else {}
    entity_types={'external_company','internal','unconfirmed','test'}
    entity_type=item.get('entity_type')
    if not isinstance(entity_type,str) or entity_type not in entity_types:entity_type=row.get('entity_type')
    if not isinstance(entity_type,str) or entity_type not in entity_types:
        entity_type='test' if normalize_country(row.get('country'))=='测试' else 'unconfirmed'
    return {'company_id':str(item.get('company_id') or row.get('company_id') or contact_id),
            'company_name':str(item.get('company_name') or row.get('company_name') or row.get('name') or ''),
            'entity_type':entity_type,
            'email_role':str(person.get('email_role') or row.get('email_role') or '未核实'),
            'contact_name':str(person.get('contact_name') or row.get('contact_name') or ''),
            'contact_title':str(person.get('contact_title',person.get('title','')) or row.get('contact_title') or ''),
            'phone':str(item.get('phone') or row.get('phone') or '')}

def inferred_stop_reason(c, address):
    rows=c.execute('SELECT reply_status,error,next_step,status FROM messages WHERE lower(recipient)=? ORDER BY rowid DESC',(address,)).fetchall()
    if any(row['status']=='已停止' for row in rows):
        # Old manual and automatic stop statuses are ambiguous; never assume refusal.
        return 'legacy_unknown'
    if any(row['reply_status']=='已回复' for row in rows):
        return 'human_reply'
    if any(row['reply_status']=='退信' and (re.search(r'(?<![\d.])5\.1\.[1236](?![\d.])',row['error'] or '') or
           '地址不存在或无效' in (row['next_step'] or '')) for row in rows):
        return 'address_invalid'
    return 'legacy_unknown'

def stop_details(c):
    details={row['email'].lower():dict(row) for row in c.execute('SELECT * FROM stop_details')}
    result={}
    for row in c.execute('SELECT email FROM stops'):
        address=row['email'].lower()
        item=details.get(address,{})
        reason=item.get('reason') or inferred_stop_reason(c,address)
        if reason not in STOP_REASONS:reason='legacy_unknown'
        label,step=STOP_REASONS[reason]
        result[address]={'stop_reason':reason,'stop_label':label,'stop_next_action':step,
                         'stop_recorded_at':item.get('recorded_at',''),'stop_source_id':item.get('source_id','')}
    return result

def record_stop(c, address, reason, source_id=''):
    address=str(address).strip().lower()
    c.execute('INSERT OR IGNORE INTO stops VALUES(?)',(address,))
    old=c.execute('SELECT reason FROM stop_details WHERE email=?',(address,)).fetchone()
    # A reply or bounce must not silently replace an explicit manual stop.
    if old and old['reason']=='manual' and reason!='manual':return
    c.execute('INSERT INTO stop_details(email,reason,recorded_at,source_id) VALUES(?,?,?,?) ON CONFLICT(email) DO UPDATE SET reason=excluded.reason,recorded_at=excluded.recorded_at,source_id=excluded.source_id',
              (address,reason,now(),source_id))

def stop_contact(data):
    address=str(data.get('email','')).strip().lower()
    if not EMAIL_RE.fullmatch(address):raise ValueError('邮箱格式错误')
    if data.get('reason','manual')!='manual':
        raise ValueError('人工操作只能记录手动停止联系；回复及地址失效须根据实际来信判断')
    with LOCK,db() as c:
        record_stop(c,address,'manual')
    # Retain each message's sending and reply facts; the stop is a mailbox rule.
    return {'ok':True,'stop_reason':'manual','stop_label':STOP_REASONS['manual'][0]}

def mailbox_histories(rows):
    result={}
    for row in rows:
        address=str(row.get('recipient') or '').lower()
        history=result.setdefault(address,empty_history())
        history['attempt_count']+=1
        if not history['last_status']:history['last_status']=row['status']
        if row.get('reply_status') and not history['reply_status']:
            history.update(reply_status=row['reply_status'],reply_at=row.get('reply_at') or '')
        if row.get('sent_at'):
            history['accepted_count']+=1
            if row['sent_at']>history['last_sent_at']:history['last_sent_at']=row['sent_at']
        ident=row.get('activity_id') or ''
        bucket=history['by_activity'].setdefault(ident,{'accepted_count':0,'last_sent_at':'','pending_count':0,'uncertain_count':0})
        if row.get('sent_at'):
            bucket['accepted_count']+=1
            if row['sent_at']>bucket['last_sent_at']:bucket['last_sent_at']=row['sent_at']
        bucket['pending_count']+=row['status'] in PENDING_MESSAGE_STATES
        bucket['uncertain_count']+=row['status']=='结果待核对'
    return result

def partner_website(country):
    country = normalize_country(country)
    country = WEBSITE_ALIASES.get(country.lower(), country)
    return 'https://hiwin-partners.com/' + WEBSITE_ROUTES.get(country, 'en')

def contact_variables(value):
    value = re.sub(r'https://hiwin-partners\.cnai5002\.chatgpt\.site/?', '{{partner_website}}', value)
    # Only bare homepages; subpages and ?query/#fragment (UTM tracking) links stay as written.
    value = re.sub(r'https?://hiwin-partners\.com(?:/(?:tw|jp|en|my|th|id|ph))?/?(?![A-Za-z0-9/_?#-])', '{{partner_website}}', value)
    phone=DEFAULT_WHATSAPP.get('phone','')
    if phone:
        value = value.replace(phone, '{{sender_whatsapp_phone}}')
        value = value.replace('https://wa.me/'+re.sub(r'[^0-9]','',phone), '{{sender_whatsapp}}')
    value = value.replace('Email: {{sender_email}}','Email: {{sender_contact_email}}').replace('Email：{{sender_email}}','Email：{{sender_contact_email}}')
    return value

def save_sheet_keychain(url, secret):
    encoded = base64.b64encode(json.dumps({'url': url, 'secret': secret}).encode()).decode()
    # Keep secrets out of process arguments, shell history and files.
    command = f'add-generic-password -U -a {SPREADSHEET} -s {KEYCHAIN_SERVICE} -w {encoded}\n'
    subprocess.run(['/usr/bin/security', '-i'], input=command, text=True,
                   capture_output=True, check=True, timeout=25)
    saved = read_sheet_keychain()
    if not saved or saved != {'url': url, 'secret': secret}:
        raise ValueError('无法保存到 Mac 钥匙串，请解锁钥匙串后重试。')

def read_sheet_keychain():
    result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-a',
                             SPREADSHEET, '-s', KEYCHAIN_SERVICE, '-w'],
                            capture_output=True, text=True, timeout=25)
    if result.returncode == 44:
        return None
    if result.returncode:
        raise ValueError('Mac 钥匙串暂时不可访问，解锁后可重新恢复连接。')
    config = json.loads(base64.b64decode(result.stdout.strip(), validate=True))
    if not BRIDGE_URL_RE.fullmatch(str(config.get('url', ''))) or len(config.get('secret', '')) < 32:
        raise ValueError('已保存的 Sheet 配置无效，请重新保存。')
    return config

def restore_sheet_settings():
    try:
        saved = read_sheet_keychain()
        if saved:
            SETTINGS.update(bridge_url=saved['url'], bridge_secret=saved['secret'], sheet_saved=True,
                            sheet_store_error='')
        return bool(saved)
    except Exception:
        SETTINGS['sheet_store_error'] = '未能读取 Mac 钥匙串；可解锁后点击恢复，或重新保存连接。'
        return False

def configure_sheet(data):
    url = str(data.get('url', '')).strip()
    if not BRIDGE_URL_RE.fullmatch(url):
        raise ValueError('请输入 Google Apps Script /exec 部署地址')
    secret = str(data.get('secret', ''))
    if not secret and url == SETTINGS['bridge_url']:
        secret = SETTINGS['bridge_secret']
    if len(secret) < 32:
        raise ValueError('请输入至少 32 个字符的同步密钥；修改部署地址时需要重新填入密钥。')
    with LOCK:
        try:
            save_sheet_keychain(url, secret)
        except Exception:
            raise ValueError('Sheet 配置未保存：请检查 Mac 钥匙串是否已解锁，然后重试。') from None
        SETTINGS.update(bridge_url=url, bridge_secret=secret, sheet_saved=True, sheet_store_error='')
        result = sync_sheet(probe=True)
    return {**result, 'saved': True}

def normalize_country(value):
    value = str(value or '').strip() or '未分类'
    if value in ('台灣', '臺灣', '台湾'):
        return '台湾'
    return '印度尼西亚' if value == '印尼' else value

def now():
    return datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(timespec='seconds')

def tls_context():
    """Keep full verification; supplement missing macOS Python CA roots."""
    context = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        return context
    context.load_verify_locations(cafile=certifi.where())
    return context

def connection_error(exc):
    if isinstance(exc, ssl.SSLCertVerificationError):
        reason = 'TLS 证书验证失败；尚未验证密码。需要检查本机可信证书配置或服务器证书。'
    elif isinstance(exc, smtplib.SMTPAuthenticationError):
        reason = (f'SMTP 服务器拒绝身份验证（代码 {exc.smtp_code}）。'
                  f"本次登录账号：{SETTINGS['sender']}；服务器：{SETTINGS['smtp_host']}:{SETTINGS['smtp_port']}。"
                  '请确认选中了正确员工，再核对该账号的密码和 SMTP 使用权限。')
    elif isinstance(exc, (TimeoutError, ConnectionRefusedError)):
        reason = 'SMTP 服务器连接超时或拒绝连接；请检查网络、服务器地址和端口。'
    elif isinstance(exc, smtplib.SMTPNotSupportedError):
        reason = 'SMTP 服务器不支持当前身份验证方式；需要核对服务器设置。'
    elif isinstance(exc, ValueError):
        reason = str(exc)
    else:
        reason = f'SMTP 连接未完成（{type(exc).__name__}），不能据此判断密码错误。'
    return reason + ' 密码未保存。'

def db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c

def contact_roster():
    path = ROOT/'contacts.json'
    return json.loads((path if path.exists() else ROOT/'contacts.example.json').read_text())

def contact_groups(roster):
    groups=[{'id':'taipei-179','name':'台北 179 家','country':'台湾',
             'contact_ids':[f'TW-{number:03d}' for number in range(1,180)]}]
    error=''
    try:
        config=json.loads((ROOT/'contact_groups.json').read_text(encoding='utf-8'))
        if not isinstance(config,dict) or not isinstance(config.get('groups'),list):
            raise ValueError('顶层须为包含 groups 数组的对象')
        custom=[];seen={'taipei-179'}
        for item in config['groups']:
            if not isinstance(item,dict):raise ValueError('每个名单须为对象')
            ident=item.get('id');name=item.get('name');members=item.get('contact_ids')
            if not isinstance(ident,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',ident) or ident in seen:
                raise ValueError('名单 id 须为唯一的小写字母／数字／连字符，不能使用 taipei-179')
            if not isinstance(name,str) or not name.strip():raise ValueError('名单名称不能为空')
            if not isinstance(members,list) or any(not isinstance(member,str) or not member or member.strip()!=member for member in members):
                raise ValueError('contact_ids 须为不含空白编号的字符串数组')
            if len(set(members))!=len(members):raise ValueError('同一名单内的联系人编号不能重复')
            custom.append({'id':ident,'name':name.strip(),'country':'','contact_ids':members})
            seen.add(ident)
        groups.extend(custom)
    except FileNotFoundError:
        pass
    except (OSError,UnicodeError,ValueError) as exc:
        reason='JSON 格式错误' if isinstance(exc,json.JSONDecodeError) else str(exc)
        error='快捷名单配置未加载（contact_groups.json）：'+reason+'；请修正本地文件后刷新页面。'
    for group in groups:
        members=set(group['contact_ids'])
        group['total_count']=len(members)
        group['loaded_count']=sum(row['id'] in members and (not group['country'] or row['country']==group['country']) for row in roster)
    return groups,error

def sender_defaults():
    path = ROOT/'sender_contacts.json'
    return json.loads(path.read_text()) if path.exists() else {}

def initialize():
    with db() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS batches(
          id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL,
          approved_digest TEXT, status TEXT NOT NULL, created TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS messages(
          id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, contact_id TEXT NOT NULL,
          name TEXT NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL,
          body TEXT NOT NULL, status TEXT NOT NULL, sent_at TEXT,
          reply_status TEXT DEFAULT '', reply_at TEXT DEFAULT '',
          next_step TEXT DEFAULT '', error TEXT DEFAULT '',
          sheet_synced INTEGER DEFAULT 0, UNIQUE(batch_id, recipient));
          CREATE TABLE IF NOT EXISTS stops(email TEXT PRIMARY KEY);
          CREATE TABLE IF NOT EXISTS stop_details(email TEXT PRIMARY KEY,reason TEXT NOT NULL,
          recorded_at TEXT NOT NULL DEFAULT '',source_id TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS inbox_events(id TEXT PRIMARY KEY, received TEXT);
          CREATE TABLE IF NOT EXISTS extra_contacts(id TEXT PRIMARY KEY,name TEXT NOT NULL,
          email TEXT NOT NULL,country TEXT NOT NULL,blocked INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS deleted_contacts(id TEXT PRIMARY KEY,deleted_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY,name TEXT NOT NULL,
          language TEXT NOT NULL,subject TEXT NOT NULL,body TEXT NOT NULL,
          links TEXT NOT NULL DEFAULT '',attachments TEXT NOT NULL DEFAULT '[]');
          CREATE TABLE IF NOT EXISTS deleted_templates(id TEXT PRIMARY KEY,deleted_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS contact_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS mail_accounts(email TEXT PRIMARY KEY,label TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS attachments(id TEXT PRIMARY KEY,name TEXT NOT NULL,
          size INTEGER NOT NULL,sha256 TEXT NOT NULL,mime TEXT NOT NULL,path TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS schedules(batch_id TEXT PRIMARY KEY,scheduled_at TEXT NOT NULL,
          status TEXT NOT NULL,batch_digest TEXT NOT NULL,review TEXT NOT NULL,error TEXT NOT NULL DEFAULT '');
        ''')
        existing = {r[1] for r in c.execute('PRAGMA table_info(messages)')}
        for column, definition in [('country', "TEXT NOT NULL DEFAULT '未分类'"),
                                   ('attachments', "TEXT NOT NULL DEFAULT '[]'"),
                                   ('sender', f"TEXT NOT NULL DEFAULT '{ADDRESS}'"),
                                   ('deleted_at', "TEXT NOT NULL DEFAULT ''"),
                                   ('message_type', "TEXT NOT NULL DEFAULT ''"),
                                   ('send_mode', "TEXT NOT NULL DEFAULT 'immediate'"),
                                   ('scheduled_at', "TEXT NOT NULL DEFAULT ''"),
                                   ('recipient_timezone', "TEXT NOT NULL DEFAULT ''"),
                                   ('attempted_at', "TEXT NOT NULL DEFAULT ''"),
                                   ('activity_id', "TEXT NOT NULL DEFAULT ''"),
                                   ('activity_name', "TEXT NOT NULL DEFAULT ''"),
                                   ('activity_purpose', "TEXT NOT NULL DEFAULT ''"),
                                   ('company_id', "TEXT NOT NULL DEFAULT ''"),
                                   ('email_role', "TEXT NOT NULL DEFAULT ''"),
                                   ('contact_name', "TEXT NOT NULL DEFAULT ''"),
                                   ('contact_title', "TEXT NOT NULL DEFAULT ''"),
                                   ('template_id', "TEXT NOT NULL DEFAULT ''"),
                                   ('template_name', "TEXT NOT NULL DEFAULT ''"),
                                   ('template_language', "TEXT NOT NULL DEFAULT ''"),
                                   ('reply_text', "TEXT NOT NULL DEFAULT ''"),
                                   ('reply_sender', "TEXT NOT NULL DEFAULT ''"),
                                   ('reply_subject', "TEXT NOT NULL DEFAULT ''"),
                                   ('reply_date', "TEXT NOT NULL DEFAULT ''"),
                                   ('reply_message_id', "TEXT NOT NULL DEFAULT ''"),
                                   ('reply_match_reason', "TEXT NOT NULL DEFAULT ''")]:
            if column not in existing:
                c.execute(f'ALTER TABLE messages ADD COLUMN {column} {definition}')
        existing = {r[1] for r in c.execute('PRAGMA table_info(templates)')}
        for column in ('country', 'activity_id', 'folder'):
            if column not in existing:
                c.execute(f"ALTER TABLE templates ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        for row in c.execute('SELECT email FROM stops').fetchall():
            if not c.execute('SELECT 1 FROM stop_details WHERE email=?',(row['email'].lower(),)).fetchone():
                c.execute('INSERT INTO stop_details(email,reason) VALUES(?,?)',(row['email'].lower(),inferred_stop_reason(c,row['email'].lower())))
        for table in ('messages', 'extra_contacts', 'templates'):
            c.execute(f"UPDATE {table} SET country='印度尼西亚' WHERE country='印尼'")
        c.execute("INSERT OR IGNORE INTO preferences(key,value) SELECT 'country-template:印度尼西亚',value FROM preferences WHERE key='country-template:印尼'")
        c.execute("DELETE FROM preferences WHERE key='country-template:印尼'")
        roster = contact_roster()
        for contact in roster:
            c.execute("UPDATE messages SET country=? WHERE contact_id=? AND country='未分类'",
                      (normalize_country(contact.get('country')), contact['id']))
        c.execute("UPDATE messages SET message_type=CASE WHEN country='测试' THEN 'test' ELSE 'formal' END WHERE message_type=''")
        for template in json.loads((ROOT / 'default_templates.json').read_text()):
            if c.execute('SELECT 1 FROM deleted_templates WHERE id=?',(template['id'],)).fetchone():
                continue
            c.execute('INSERT OR IGNORE INTO templates(id,name,language,subject,body,country) VALUES(?,?,?,?,?,?)',
                      tuple(template[k] for k in ('id','name','language','subject','body'))+(template.get('country',''),))
            # Upgrade only an unchanged starter template; preserve the user's edits.
            old_body=template['body'].replace('{{sender_name}}',DEFAULT_SENDER_NAME)
            c.execute('UPDATE templates SET body=? WHERE id=? AND name=? AND language=? AND subject=? AND body=?',
                      (template['body'],template['id'],template['name'],template['language'],template['subject'],old_body))
        # Replace known old partner URLs and fixed WhatsApp details, keeping other user copy.
        for row in c.execute('SELECT id,body,links FROM templates').fetchall():
            c.execute('UPDATE templates SET body=?,links=? WHERE id=?',
                      (contact_variables(row['body']), contact_variables(row['links']), row['id']))
        c.execute("INSERT OR IGNORE INTO preferences VALUES('template','outreach-zh-TW')")
        c.execute('INSERT OR IGNORE INTO mail_accounts VALUES(?,?)',(ADDRESS,DEFAULT_SENDER_NAME))
        c.execute("INSERT OR IGNORE INTO preferences VALUES('mail_account',?)",(ADDRESS,))
        # A process crash after SMTP DATA may mean delivery; never automatically retry.
        c.execute("UPDATE messages SET status='结果待核对',next_step='检查已发送邮件／收件人后再决定',sheet_synced=0 WHERE status='发送中'")
        c.execute("UPDATE batches SET status='结果待核对' WHERE status='sending'")
        c.execute("UPDATE messages SET status='本批已暂停',next_step='定时发送中断，请核对后重新安排',sheet_synced=0 WHERE batch_id IN (SELECT batch_id FROM schedules WHERE status='running') AND status='待发送'")
        c.execute("UPDATE schedules SET status='needs_review',error='定时发送中断，需人工核对' WHERE status='running'")
        overdue=[r[0] for r in c.execute("SELECT batch_id FROM schedules WHERE status IN ('scheduled','awaiting_mail') AND scheduled_at<=?",(now(),))]
        for ident in overdue:
            mark_schedule(c,ident,'missed','已错过时间','工具停止期间已错过计划时间，请取消后重新安排')
    os.chmod(DB_PATH, 0o600)

def mail_accounts():
    with db() as c:
        return [dict(row) for row in c.execute('SELECT * FROM mail_accounts ORDER BY rowid')]

def save_mail_account(data):
    address = str(data.get('email','')).strip().lower()
    label = str(data.get('label','')).strip() or address
    if not EMAIL_RE.fullmatch(address) or address.split('@')[1] not in COMPANY_MAIL_DOMAINS:
        raise ValueError('请输入 @hiwin-japan.co.jp 或 @andclan.co.jp 的完整员工邮箱；其他域名需先核对邮件服务器。')
    if len(label)>100 or any(ord(ch)<32 for ch in label):
        raise ValueError('员工名称无效')
    with LOCK, db() as c:
        c.execute('INSERT INTO mail_accounts VALUES(?,?) ON CONFLICT(email) DO UPDATE SET label=excluded.label',
                  (address,label))
    return {'email':address,'label':label}

def select_mail_account(address):
    address = str(address).strip().lower()
    with LOCK, db() as c:
        if not c.execute('SELECT 1 FROM mail_accounts WHERE email=?',(address,)).fetchone():
            raise ValueError('请先添加这个员工邮箱')
        if address != SETTINGS['sender']:
            SETTINGS.update(sender=address,password=MAIL_SESSIONS.get(address,''))
            invalidate_drafts(c)
        c.execute("UPDATE preferences SET value=? WHERE key='mail_account'",(address,))
    connected=bool(SETTINGS['password'])
    return {'ok':True,'sender':address,'connected':connected,
            'message':f'已切换为 {address}；'+('本次运行已连接，可以直接使用。' if connected else '请用这个员工自己的邮箱密码连接。')}

def restore_mail_account():
    MAIL_SESSIONS.clear()
    with db() as c:
        row = c.execute("SELECT value FROM preferences WHERE key='mail_account'").fetchone()
        if row and c.execute('SELECT 1 FROM mail_accounts WHERE email=?',(row[0],)).fetchone():
            SETTINGS.update(sender=row[0],password='')

def require_current_sender(data):
    if data.get('sender') != SETTINGS['sender']:
        raise ValueError('当前发件员工已变化，请刷新页面并重新预览确认。')

def connect_mail(data):
    with LOCK:
        require_current_sender(data)
        password = str(data.get('password',''))
        if not password:
            raise ValueError('请输入当前员工自己的邮箱密码。')
        SETTINGS['password'] = password
        try:
            client = smtp()
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                client.close()
        except Exception as exc:
            SETTINGS['password'] = ''
            MAIL_SESSIONS.pop(SETTINGS['sender'],None)
            raise ValueError(connection_error(exc)) from None
        MAIL_SESSIONS[SETTINGS['sender']] = password
    return {'ok':True,'message':SETTINGS['sender']+' 已连接；密码只在当前运行内存中'}

def test_recipient(row, roster):
    """Only an actual test contact gets the resend exception, never a real alias."""
    address = str(row.get('recipient', row.get('email', ''))).lower()
    ident = row.get('contact_id', row.get('id'))
    matches = [item for item in roster if item['email'].lower() == address]
    return (normalize_country(row.get('country')) == '测试' and bool(address) and
            any(item['id'] == ident for item in matches) and
            all(normalize_country(item.get('country')) == '测试' for item in matches))

def contacts():
    fallback = contact_roster()
    with db() as c:
        snapshot = c.execute("SELECT value FROM preferences WHERE key='sheet_contacts'").fetchone()
        roster = json.loads(snapshot['value']) if snapshot else fallback
        known = {row['id'] for row in roster}
        extras = [row for row in fallback if normalize_country(row.get('country'))=='测试']
        extras += [dict(r) for r in c.execute('SELECT * FROM extra_contacts ORDER BY rowid')]
        for row in extras:
            if row['id'] not in known:
                roster.append(row);known.add(row['id'])
        stopped = stop_details(c)
        deleted = {r[0] for r in c.execute('SELECT id FROM deleted_contacts')}
        histories=mailbox_histories([dict(row) for row in c.execute('SELECT recipient,status,sent_at,reply_status,reply_at,activity_id FROM messages ORDER BY rowid DESC')])
    metadata=contact_metadata()
    for row in roster:
        row['country'] = normalize_country(row.get('country'))
        row.update(contact_identity(row,metadata))
        row['history']=histories.get(row['email'].lower()) or empty_history()
        row['blocked'] = (not test_recipient(row, roster) and
                          bool(row.get('blocked') or row['email'].lower() in stopped))
        stop=stopped.get(row['email'].lower()) or (SHEET_STOP if row.get('blocked') else {})
        row.update({'stop_reason':'','stop_label':'','stop_next_action':'',**stop})
    return [row for row in roster if row['id'] not in deleted]

def sheet_bridge_request_data(payload):
    payload=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
    ts,nonce=str(int(time.time())),secrets.token_hex(16)
    signature=hmac.new(SETTINGS['bridge_secret'].encode(),(ts+'\n'+nonce+'\n'+payload).encode(),hashlib.sha256).hexdigest()
    return json.dumps({'timestamp':ts,'nonce':nonce,'payload':payload,'signature':signature},ensure_ascii=False).encode()

def sheet_bridge_request(payload):
    if not SETTINGS['bridge_url'] or not SETTINGS['bridge_secret']:
        raise ValueError('Google Sheet 尚未连接，请先保存连接。')
    request=urllib.request.Request(SETTINGS['bridge_url'],data=sheet_bridge_request_data(payload),
        headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=45,context=tls_context()) as response:
        return json.load(response)

def refresh_sheet_contacts():
    with LOCK:
        try:
            result=sheet_bridge_request({'action':'contacts'})
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError('联系人刷新失败：'+type(exc).__name__+'；原名单已保留，可稍后重试。') from None
        if not result.get('ok') or not isinstance(result.get('contacts'),list):
            raise ValueError('Sheet 连接尚不支持联系人读取，请将 SheetBridge.gs 更新并部署新版本；原名单已保留。')
        if result.get('spreadsheet_id')!=SPREADSHEET or result.get('source_sheet')!='邮件跟踪':
            raise ValueError('联系人来源不匹配；原名单已保留。')
        rows=result['contacts']
        if not rows or len(rows)>10000:
            raise ValueError('表格名单为空或超过 10000 行；原名单已保留。')
        roster=[];known=set();invalid_emails=0
        for index,row in enumerate(rows,2):
            if not isinstance(row,dict):
                raise ValueError('表格联系人结构无效；原名单已保留。')
            ident,name=str(row.get('id','')).strip(),str(row.get('name','')).strip()
            country=normalize_country(row.get('country'))
            address=str(row.get('email','')).strip().lower()
            if not ident or len(ident)>100 or not name or len(name)>180 or len(country)>50:
                raise ValueError(f'邮件跟踪第 {index} 行需填写有效的机构编号、机构名称和国家；原名单已保留。')
            if ident in known:
                raise ValueError('机构编号重复：'+ident+'；请修正表格后重试，原名单已保留。')
            known.add(ident)
            if address and not EMAIL_RE.fullmatch(address):
                invalid_emails+=1
                email_error='表格邮箱格式无效，请填写单一完整邮箱。'
                address=''
            else:
                email_error=''
            roster.append({'id':ident,'name':name,'country':country,'email':address,
                           'blocked':bool(row.get('blocked')),'email_error':email_error})
        previous={row['id']:row for row in contacts()}
        with db() as c:
            snapshot=c.execute("SELECT value FROM preferences WHERE key='sheet_contacts'").fetchone()
        old_source=json.loads(snapshot['value']) if snapshot else contact_roster()
        previous_source={row['id']:row for row in old_source if normalize_country(row.get('country'))!='测试'}
        new={row['id']:row for row in roster}
        added=len(set(new)-set(previous_source))
        removed=len(set(previous_source)-set(new))
        updated=sum(any(previous_source[ident].get(key)!=row.get(key) for key in ('name','country','email','blocked')) for ident,row in new.items() if ident in previous_source)
        stamp=now()
        with db() as c:
            stopped=stop_details(c)
            # Preserve company-level/manual and ambiguous legacy safeguards.
            # An invalid old mailbox is not evidence that a replacement is invalid.
            for row in roster:
                old=previous.get(row['id'])
                detail=stopped.get(old['email'].lower()) if old else None
                if detail and detail['stop_reason']!='address_invalid' and row['email'] and row['email']!=old['email']:
                    record_stop(c,row['email'],detail['stop_reason'],detail.get('stop_source_id',''))
            set_pref(c,'sheet_contacts',json.dumps(roster,ensure_ascii=False))
            set_pref(c,'contacts_refreshed_at',stamp)
            invalidate_drafts(c)
        return {'ok':True,'count':len(roster),'added':added,'updated':updated,'removed':removed,
                'invalid_emails':invalid_emails,'refreshed_at':stamp,'source_sheet':'邮件跟踪'}

def add_contact(data):
    name = str(data.get('name', '')).strip()
    address = str(data.get('email', '')).strip().lower()
    country = normalize_country(data.get('country'))
    if not name or len(name) > 180 or len(country) > 50:
        raise ValueError('请输入机构名称和有效的国家／地区')
    if address and not EMAIL_RE.fullmatch(address):
        raise ValueError('收件邮箱格式不正确')
    with LOCK:
        if address and any(row['email'].lower() == address for row in contacts()):
            raise ValueError('这个邮箱已在名单中，请直接选择已有机构')
        ident = 'LOCAL-' + secrets.token_hex(8)
        with db() as c:
            c.execute('INSERT INTO extra_contacts(id,name,email,country) VALUES(?,?,?,?)', (ident,name,address,country))
    return {'id':ident,'name':name,'email':address,'country':country}

def delete_contacts(ids):
    if not isinstance(ids,list) or not ids or any(not isinstance(ident,str) or not ident for ident in ids):
        raise ValueError('请选择要删除的联系人')
    ids=set(ids)
    with LOCK, db() as c:
        known={row['id'] for row in contacts()}
        if not ids <= known:
            raise ValueError('联系人已变化，请刷新页面后重试')
        affected=[]
        for batch in c.execute("SELECT id,status,payload FROM batches WHERE status IN ('draft','approved','scheduled','sending')"):
            if any(message['contact_id'] in ids for message in json.loads(batch['payload'])['messages']):
                task=c.execute('SELECT status FROM schedules WHERE batch_id=?',(batch['id'],)).fetchone()
                if batch['status']=='sending' or (task and task['status']=='running'):
                    raise ValueError('这些联系人所在批次正在发送，请发送结束后再删除')
                if batch['status']=='scheduled':
                    raise ValueError('这些联系人有定时任务，请先取消定时任务再删除')
                affected.append(batch['id'])
        c.executemany('INSERT INTO deleted_contacts VALUES(?,?)',[(ident,now()) for ident in ids])
        c.executemany("UPDATE batches SET status='invalidated',approved_digest=NULL WHERE id=?",[(ident,) for ident in affected])
    return {'ok':True,'count':len(ids)}

def upload_attachment(data):
    name = Path(str(data.get('name', '')).replace('\\', '/')).name
    if not name or name in ('.','..') or len(name) > 180 or any(ord(ch)<32 for ch in name):
        raise ValueError('附件文件名无效')
    try:
        content = base64.b64decode(data.get('content', ''), validate=True)
    except Exception:
        raise ValueError('无法读取附件内容') from None
    if not content or len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError('单个附件需要大于 0 字节，且不超过 10 MB')
    ident = secrets.token_hex(16)
    directory = DB_PATH.parent / 'uploads'
    directory.mkdir(exist_ok=True, mode=0o700)
    path = directory / ident
    with path.open('xb') as f:
        f.write(content)
    os.chmod(path,0o600)
    row = {'id':ident,'name':name,'size':len(content),'sha256':hashlib.sha256(content).hexdigest(),
           'mime':mimetypes.guess_type(name)[0] or 'application/octet-stream'}
    with db() as c:
        c.execute('INSERT INTO attachments VALUES(?,?,?,?,?,?)',tuple(row[k] for k in ('id','name','size','sha256','mime'))+(str(path),))
    return row

def attachment_metadata(ids):
    if not isinstance(ids,list) or len(ids)>20 or len(set(ids))!=len(ids):
        raise ValueError('附件列表无效')
    rows = []
    with db() as c:
        for ident in ids:
            row = c.execute('SELECT id,name,size,sha256,mime FROM attachments WHERE id=?',(ident,)).fetchone()
            if not row:
                raise ValueError('附件不存在，请重新上传')
            rows.append(dict(row))
    if sum(row['size'] for row in rows)>MAX_ATTACHMENT_BYTES:
        raise ValueError('每封邮件附件合计不能超过 10 MB')
    return rows

def read_attachment(metadata):
    with db() as c:
        row = c.execute('SELECT * FROM attachments WHERE id=?',(metadata['id'],)).fetchone()
    if not row:
        raise ValueError('附件已丢失，请重新预览并批准')
    try:
        content = Path(row['path']).read_bytes()
    except OSError:
        raise ValueError('附件文件已丢失，请重新上传并批准') from None
    if hashlib.sha256(content).hexdigest()!=metadata['sha256'] or len(content)!=metadata['size']:
        raise ValueError('附件内容已改变，请重新上传、预览并批准')
    return content

def parse_links(value):
    if not isinstance(value,str) or len(value)>12000:
        raise ValueError('网页链接内容无效')
    result=[]
    for line in value.splitlines():
        if not line.strip(): continue
        label, separator, url = line.partition('|')
        if not separator: url,label=label,''
        url,label=url.strip(),label.strip()
        parsed=urllib.parse.urlsplit(url.replace('{{partner_website}}', partner_website('')))
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or any(ch.isspace() for ch in url):
            raise ValueError('网页链接需是完整的 http:// 或 https:// 地址；每行一个链接')
        result.append({'label':label,'url':url})
    if len(result)>20:
        raise ValueError('每个模板最多保存 20 个网页链接')
    return result

def template_rows():
    with db() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM templates ORDER BY rowid')]
    for row in rows:
        row['country']=normalize_country(row['country']) if row['country'] else ''
        row['attachments']=attachment_metadata(json.loads(row['attachments']))
    return rows

def save_template(data):
    name,language=str(data.get('name','')).strip(),str(data.get('language','')).strip()
    subject,body=str(data.get('subject','')),str(data.get('body',''))
    if not name or len(name)>120 or not language or len(language)>50 or not subject.strip() or not body.strip():
        raise ValueError('请填写模板名称、语言、主题和正文')
    if '\r' in subject or '\n' in subject or len(body)>100000:
        raise ValueError('主题不能换行，正文不能超过 100000 字符')
    render(subject,{'name':'示例机构','id':'EXAMPLE'})
    render(body,{'name':'示例机构','id':'EXAMPLE'})
    links=str(data.get('links',''));parse_links(links)
    attached=attachment_metadata(data.get('attachments',[]))
    country=normalize_country(data['country']) if data.get('country') else ''
    if len(country)>50 or any(ord(ch)<32 for ch in country):
        raise ValueError('模板国家／地区无效')
    ident=str(data.get('id') or ('template-'+secrets.token_hex(8)))
    with LOCK,db() as c:
        if data.get('id') and not c.execute('SELECT 1 FROM templates WHERE id=?',(ident,)).fetchone():
            raise ValueError('模板不存在，请另存为新模板')
        previous=c.execute('SELECT country,activity_id,folder FROM templates WHERE id=?',(ident,)).fetchone()
        activity_id=str(data.get('activity_id',previous['activity_id'] if previous else '') or '')
        activity_fields(activity_id,allow_empty=True)
        # Folders only organise the picker; an empty folder means "group by country".
        folder=str(data.get('folder',previous['folder'] if previous else '') or '').strip()
        if len(folder)>30 or any(ord(ch)<32 for ch in folder):
            raise ValueError('模板文件夹名称最多 30 字，不能换行')
        if previous and previous['country']!=country:
            ident='template-'+secrets.token_hex(8)
        c.execute('INSERT INTO templates(id,name,language,subject,body,links,attachments,country,activity_id,folder) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,language=excluded.language,subject=excluded.subject,body=excluded.body,links=excluded.links,attachments=excluded.attachments,activity_id=excluded.activity_id,folder=excluded.folder',
                  (ident,name,language,subject,body,links,json.dumps([r['id'] for r in attached]),country,activity_id,folder))
        set_pref(c,'template',ident)
        if country:
            if activity_id:
                set_pref(c,'activity-country-template:'+activity_id+':'+country,ident)
            if not activity_id or activity_id=='first-contact':
                set_pref(c,'country-template:'+country,ident)
    return {'id':ident}

def delete_template(data):
    ident=str(data.get('id',''))
    with LOCK,db() as c:
        if not c.execute('SELECT 1 FROM templates WHERE id=?',(ident,)).fetchone():
            raise ValueError('模板不存在，请刷新页面')
        if c.execute('SELECT COUNT(*) FROM templates').fetchone()[0]<=1:
            raise ValueError('请至少保留一个模板；先另存为新模板后再删除')
        for row in c.execute("SELECT b.payload FROM schedules s JOIN batches b ON b.id=s.batch_id WHERE s.status IN ('scheduled','awaiting_mail','running','needs_review','missed')"):
            if json.loads(row['payload']).get('template_id')==ident:
                raise ValueError('此模板有未结束的定时任务，请先取消定时任务再删除')
        for row in c.execute("SELECT id,payload FROM batches WHERE status IN ('draft','approved')").fetchall():
            if json.loads(row['payload']).get('template_id')==ident:
                c.execute("UPDATE batches SET status='invalidated',approved_digest=NULL WHERE id=?",(row['id'],))
        c.execute('INSERT OR REPLACE INTO deleted_templates VALUES(?,?)',(ident,now()))
        c.execute('DELETE FROM templates WHERE id=?',(ident,))
        c.execute("DELETE FROM preferences WHERE key LIKE 'country-template:%' AND value=?",(ident,))
        c.execute("DELETE FROM preferences WHERE key LIKE 'activity-country-template:%' AND value=?",(ident,))
        fallback=c.execute("SELECT id FROM templates ORDER BY CASE id WHEN 'outreach-zh-TW' THEN 0 WHEN 'outreach-en' THEN 1 ELSE 2 END,rowid LIMIT 1").fetchone()[0]
        c.execute("UPDATE preferences SET value=? WHERE key='template' AND value=?",(fallback,ident))
    return {'ok':True,'id':ident}

def render(template, contact):
    account=next((row for row in mail_accounts() if row['email']==SETTINGS['sender']),None)
    sender_name=account['label'] if account else SETTINGS['sender']
    profile=contact_config()
    sender_name=profile.get('signature_name') or sender_name
    value = template.replace('{{agency_name}}', contact['name']).replace('{{test_id}}', contact['id'])
    value = value.replace('{{sender_name}}',sender_name).replace('{{sender_email}}',SETTINGS['sender'])
    value = value.replace('{{sender_contact_email}}',profile.get('contact_email') or SETTINGS['sender'])
    value = value.replace('{{partner_website}}',partner_website(contact.get('website_country',contact.get('country'))))
    value = value.replace('{{sender_whatsapp_phone}}',profile['effective_whatsapp']['phone'])
    value = value.replace('{{sender_whatsapp}}',profile['effective_whatsapp']['url'])
    if '{{sender_line}}' in value:
        if not profile.get('line_url'):
            raise ValueError('当前员工尚未配置 LINE 联系方式，请先设置后再预览台湾版。')
        value = value.replace('{{sender_line}}',profile['line_url'])
    if re.search(r'\{\{.*?\}\}', value):
        raise ValueError('模板存在未替换变量；支持 agency_name、test_id、sender_name、sender_email、sender_contact_email、sender_line、sender_whatsapp、sender_whatsapp_phone、partner_website')
    return value

def stored_contact(key):
    with db() as c:
        row=c.execute('SELECT value FROM contact_settings WHERE key=?',(key,)).fetchone()
    return json.loads(row['value']) if row else {}

def contact_config():
    defaults=sender_defaults().get(SETTINGS['sender'],{})
    profile={**defaults,'signature_name':'','contact_email':'','use_shared_whatsapp':True,
             'whatsapp':dict(DEFAULT_WHATSAPP),**stored_contact(SETTINGS['sender'])}
    shared={**DEFAULT_WHATSAPP,**stored_contact('shared-whatsapp')}
    profile['shared_whatsapp']=shared
    profile['effective_whatsapp']=shared if profile['use_shared_whatsapp'] else profile['whatsapp']
    return profile

def qr_files():
    files={row['line_qr'] for row in sender_defaults().values()}
    files.add(DEFAULT_WHATSAPP['qr'])
    with db() as c:
        profiles=[json.loads(row[0]) for row in c.execute('SELECT value FROM contact_settings')]
    for profile in profiles:
        files.update(file for file in (profile.get('line_qr'),profile.get('qr'),profile.get('whatsapp',{}).get('qr')) if file)
    return files

def upload_contact_qr(data):
    try:
        content=base64.b64decode(data.get('content',''),validate=True)
    except Exception:
        raise ValueError('二维码图片编码无效') from None
    if not content or len(content)>2*1024*1024:
        raise ValueError('二维码图片不能超过 2 MB')
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        extension='png'
    elif content.startswith(b'\xff\xd8\xff'):
        extension='jpg'
    else:
        raise ValueError('请上传 PNG 或 JPG 二维码图片')
    file='contact-qr-'+hashlib.sha256(content).hexdigest()+'.'+extension
    with LOCK,db() as c:
        (ROOT/'assets').mkdir(exist_ok=True)
        (ROOT/'assets'/file).write_bytes(content)
        c.execute('INSERT OR IGNORE INTO contact_settings VALUES(?,?)',('qr-upload:'+file,json.dumps({'qr':file})))
    return {'file':file,'preview_url':'/assets/'+file}

def save_contact_config(data):
    require_current_sender(data)
    profile={key:str(data.get(key,'')).strip() for key in ('signature_name','contact_email','line_url','line_qr')}
    if len(profile['signature_name'])>100 or any(ord(ch)<32 for ch in profile['signature_name']):
        raise ValueError('署名姓名无效')
    if profile['contact_email'] and not EMAIL_RE.fullmatch(profile['contact_email']):
        raise ValueError('联系邮箱格式无效')
    profile['use_shared_whatsapp']=bool(data.get('use_shared_whatsapp',True))
    whatsapp={key:str(data.get('whatsapp',{}).get(key,'')).strip() for key in ('phone','url','qr')}
    if len(whatsapp['phone'])>100 or any(ord(ch)<32 for ch in whatsapp['phone']):
        raise ValueError('WhatsApp 电话格式无效')
    for url in (profile['line_url'],whatsapp['url']):
        if url:
            parse_links(url)
    if profile['line_qr'] and not profile['line_url'] or whatsapp['qr'] and not whatsapp['url']:
        raise ValueError('有二维码时请同时填写相应联系链接')
    for file in (profile['line_qr'],whatsapp['qr']):
        if file and (file not in qr_files() or not (ROOT/'assets'/file).is_file()):
            raise ValueError('请重新上传二维码图片')
    with LOCK,db() as c:
        if profile['use_shared_whatsapp']:
            set_pref(c,'shared-whatsapp',json.dumps(whatsapp),'contact_settings')
            profile['whatsapp']=contact_config()['whatsapp']
        else:
            profile['whatsapp']=whatsapp
        set_pref(c,SETTINGS['sender'],json.dumps(profile),'contact_settings')
        invalidate_drafts(c)
    return {'ok':True,'contact_config':contact_config()}

def make_signature(url,file,channel):
    content=(ROOT/'assets'/file).read_bytes()
    checksum=hashlib.sha256(content).hexdigest()
    return {'url':url,'file':file,'size':len(content),'sha256':checksum,'channel':channel,
            'cid':channel.lower()+'-'+checksum[:24]+'@hiwin.local','preview_url':'/assets/'+file,
            'mime':'image/jpeg' if file.endswith('.jpg') else 'image/png'}

def is_taiwan_template(data):
    return data.get('template_id') in ('outreach-zh-TW','outreach-en-TW') or '{{sender_line}}' in str(data.get('body','')) or data.get('language') in ('繁體中文（台灣）','繁體中文（台湾）','zh-TW')

def line_signature(data):
    if not is_taiwan_template(data):
        return None
    profile = contact_config()
    if not profile.get('line_url'):
        raise ValueError('当前员工尚未配置 LINE 链接与二维码，请先设置后再预览台湾版。')
    return make_signature(profile['line_url'],profile['line_qr'],'LINE') if profile.get('line_qr') else None

def whatsapp_signature(data):
    if is_taiwan_template(data):
        return None
    if data.get('template_id') not in ('outreach-en','test-en') and not str(data.get('language','')).lower().startswith(('en','英语','英語','英文')):
        return None
    profile=contact_config()['effective_whatsapp']
    return make_signature(profile['url'],profile['qr'],'WhatsApp') if profile.get('qr') else None

def read_line_qr(signature):
    files = qr_files()
    if signature.get('file') not in files:
        raise ValueError('二维码配置无效，请重新预览。')
    try:
        content = (ROOT/'assets'/signature['file']).read_bytes()
    except OSError:
        raise ValueError('二维码已丢失，请重新配置并预览。') from None
    if len(content) != signature['size'] or hashlib.sha256(content).hexdigest() != signature['sha256']:
        raise ValueError('二维码内容已变化，请重新预览并批准。')
    return content

def uppercase_signature_name(body):
    for marker in ('\n敬祝 商祺\n','\nBest regards,\n'):
        content, closing, signature=body.rpartition(marker)
        if closing:
            name, newline, remaining=signature.partition('\n')
            return content+closing+name.upper()+newline+remaining
    return body

def body_sections(body, taiwan=False):
    parts=[]
    greeting, newline, remaining=body.partition('\n')
    if (taiwan and greeting.endswith('團隊您好：')) or (greeting.startswith('Dear ') and greeting.endswith(' Team,')):
        parts.append({'kind':'greeting','text':greeting})
        body=remaining.lstrip('\n')
    marker='\n敬祝 商祺\n' if '\n敬祝 商祺\n' in body else '\nBest regards,\n'
    content, closing, signature=body.rpartition(marker)
    if closing:
        parts.extend([{'kind':'text','text':content.rstrip()}, {'kind':'divider'},
                      {'kind':'text','text':marker.strip()}])
        for index,line in enumerate(signature.split('\n')):
            kind='signature-name' if index==0 else 'contact' if line.startswith(('Email:','Email：','LINE:','LINE：','WhatsApp:','HIWIN 公司官網：','HIWIN corporate website:','株式会社 HIWIN 公司官網：','HIWIN Co., Ltd. corporate website:')) else 'text'
            parts.append({'kind':kind,'text':line.upper() if kind=='signature-name' else line})
    else:
        parts.append({'kind':'text','text':body})
    formatted=[]
    for part in parts:
        if part['kind']=='text':
            position=0
            for match in re.finditer(r'(?m)^(#{1,3})[ \t]+([^\n]+)',part['text']):
                if match.start()>position:
                    segment=part['text'][position:match.start()]
                    if position:segment=segment.removeprefix('\n')
                    formatted.append({'kind':'text','text':segment.removesuffix('\n')})
                formatted.append({'kind':'heading','level':len(match[1]),'text':match[2]})
                position=match.end()
            if position<len(part['text']):
                segment=part['text'][position:]
                if position:segment=segment.removeprefix('\n')
                if segment:formatted.append({'kind':'text','text':segment})
        else:
            formatted.append(part)
    for part in formatted:
        if 'text' in part:
            part['runs']=inline_runs(part['text'],part['kind'] in ('text','heading'))
    return formatted

def inline_runs(text, emphasize=False, bold=False, italic=False):
    # Only a small Markdown subset is accepted; all output remains escaped.
    # URLs/emails are opaque so punctuation inside them never becomes formatting.
    # [显示文字](https://...) shows only the text in HTML; plain text keeps the URL.
    pattern=(r'(?P<mdlink>\[[^\[\]\n]+\]\(https?://[^\s()<>]+\))'
             r'|(?P<strong_em>\*\*\*(?=\S)[^\n]+?(?<=\S)\*\*\*)'
             r'|(?P<strong>\*\*(?=\S)[^\n]+?(?<=\S)\*\*)'
             r'|(?P<em>(?<!\*)\*(?!\*)(?=\S)[^\n*]+?(?<=\S)\*(?!\*))'
             r'|(?P<url>https?://[^\s<>。，；、）\]]+)'
             r'|(?P<email>[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})'
             r'|(?P<brand>株式会社[ \t]*HIWIN\b|\bHIWIN[ \t]+Co\.,[ \t]*Ltd\.|\b(?:Apartment[ \t]+Hotel[ \t]+11|HIWIN)\b)')
    runs=[]
    def literal(value, **extras):
        if value:
            run={'text':value,**extras}
            if bold:run['bold']=True
            if italic:run['italic']=True
            runs.append(run)
    position=0
    for match in re.finditer(pattern,text,re.IGNORECASE):
        literal(text[position:match.start()])
        value=match.group(0);kind=match.lastgroup
        if kind in ('strong_em','strong','em'):
            width={'strong_em':3,'strong':2,'em':1}[kind]
            runs.extend(inline_runs(value[width:-width],emphasize,
                                    bold or kind in ('strong_em','strong'),
                                    italic or kind in ('strong_em','em')))
        elif kind=='mdlink':
            label,_,url=value[1:-1].partition('](')
            literal(label,url=url,label=True)
        elif kind=='url':
            literal(value,url=value)
        elif kind=='brand' and emphasize:
            literal(value,bold=True)
        else:
            literal(value)
        position=match.end()
    literal(text[position:])
    return runs

def plain_text_body(body):
    body=re.sub(r'(?m)^#{1,3}[ \t]+','',body)
    return ''.join(run['text']+(' ('+run['url']+')' if run.get('label') else '') for run in inline_runs(body))

def email_html(body, signature=None, taiwan=False):
    sections=[]
    for part in body_sections(body,taiwan or bool(signature and signature.get('channel','LINE')=='LINE')):
        if part['kind']=='divider':
            sections.append('<hr style="border:0;border-top:1px solid #dce5e0;margin:20px 0">')
            continue
        fragments=[]
        for run in part['runs']:
            # Email clients may discard white-space CSS; encode line breaks in HTML.
            fragment=html.escape(run['text']).replace('\r\n','\n').replace('\r','\n').replace('\n','<br>')
            if run.get('url'):
                fragment='<a href="'+html.escape(run['url'],quote=True)+'">'+fragment+'</a>'
            if run.get('italic'):
                fragment='<em>'+fragment+'</em>'
            if run.get('bold'):
                fragment='<strong>'+fragment+'</strong>'
            fragments.append(fragment)
        linked=''.join(fragments)
        if part['kind']=='greeting':
            sections.append('<div style="font-size:16px;font-weight:400;line-height:1.7;margin:0 0 16px">'+linked+'</div>')
        elif part['kind']=='heading':
            level=part['level'];size={1:20,2:18,3:16}[level]
            sections.append('<h'+str(level)+' style="font-size:'+str(size)+'px;font-weight:700;line-height:1.7;margin:10px 0 4px">'+linked+'</h'+str(level)+'>')
        elif part['kind'] in ('signature-name','contact'):
            size='16' if part['kind']=='signature-name' else '14'
            sections.append('<div style="font-size:'+size+'px;font-weight:700;line-height:1.7"><strong>'+linked+'</strong></div>')
        else:
            sections.append('<div style="font-size:14px;line-height:1.7">'+linked+'</div>')
    qr = ''
    if signature:
        is_whatsapp=signature.get('channel')=='WhatsApp'
        alt='WhatsApp QR Code' if is_whatsapp else 'LINE 加好友 QR Code'
        caption='Scan the QR code or click to contact us on WhatsApp.' if is_whatsapp else '掃描 QR Code 或點擊上方 LINE 連結加入好友。'
        qr = ('<div style="margin-top:16px"><a href="'+html.escape(signature['url'],quote=True)+'">'
              '<img src="cid:'+signature['cid']+'" width="180" height="180" alt="'+alt+'" style="display:block;border:0">'
              '</a><p style="margin:6px 0">'+caption+'</p></div>')
    return '<html><body><div style="font-family:Arial,sans-serif;color:#1d2e2e">'+''.join(sections)+qr+'</div></body></html>'

def digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def create_batch(data):
    template=None
    if data.get('template_id'):
        with db() as c:
            template=c.execute('SELECT * FROM templates WHERE id=?',(str(data['template_id']),)).fetchone()
            if not template:
                raise ValueError('模板已删除或不存在，请刷新后重新选择模板')
    activity=activity_fields(data.get('activity_id') or (template['activity_id'] if template else '') or 'first-contact')
    selected = set(data.get('ids', []))
    all_contacts = contacts()
    roster = [r for r in all_contacts if r['id'] in selected]
    if not roster or len(roster) != len(selected):
        raise ValueError('请选择有效收件人')
    test_rows = [r for r in roster if r['country']=='测试']
    if test_rows and len(test_rows)!=len(roster):
        raise ValueError('测试邮箱和正式旅行社请分批发送，清空已选后重新选择。')
    if data.get('template_id')=='test-en' and not test_rows:
        raise ValueError('当前是测试模板，请选择测试邮箱，或切换正式合作模板。')
    if not data.get('subject', '').strip() or not data.get('body', '').strip():
        raise ValueError('主题和正文不能为空')
    attached=attachment_metadata(data.get('attachments',[]))
    for file in attached:
        read_attachment(file)
    links=parse_links(data.get('links',''))
    language=str(data.get('language',''))
    taiwan_signature=line_signature(data)
    english_signature=None if taiwan_signature else whatsapp_signature(data)
    signature=taiwan_signature or english_signature
    template_country=normalize_country(data['country']) if data.get('country') else ''
    if template_country and any(r['country'] not in (template_country,'测试') for r in roster):
        raise ValueError('当前模板仅适用于 '+template_country+'；请按国家分批，或改用通用模板。')
    ident = secrets.token_hex(8)
    seen, rendered = set(), []
    for r in roster:
        if r['country']=='测试' and template_country:
            r={**r,'website_country':template_country}
        address = r['email'].lower()
        if not EMAIL_RE.fullmatch(address) or address in seen:
            raise ValueError('邮箱无效或同批邮箱重复')
        seen.add(address)
        subject = render(data['subject'], r)
        if '\n' in subject or '\r' in subject:
            raise ValueError('邮件主题不能包含换行')
        if test_recipient(r,all_contacts):
            subject+=' [TEST '+datetime.now(ZoneInfo('Asia/Tokyo')).strftime('%Y%m%d-%H%M%S')+'-'+ident[:6]+']'
        body=uppercase_signature_name(render(data['body'], r))
        actual_links=[{'label':render(item['label'],r),'url':render(item['url'],r)} for item in links]
        if actual_links:
            heading='Links:' if language.lower().startswith('en') else '相關連結：'
            body=body.rstrip()+'\n\n'+heading+'\n'+'\n'.join('['+item['label']+']('+item['url']+')' if item['label'] and not re.search(r'[\[\]\n]',item['label']) and not re.search(r'[()<>]',item['url']) else (item['label']+'：' if item['label'] else '')+item['url'] for item in actual_links)
        rendered.append({'contact_id': r['id'], 'name': r['name'], 'recipient': address,
                         **activity,**{key:r.get(key,'') for key in ('company_id','email_role','contact_name','contact_title')},
                         'message_type':'test' if test_recipient(r,all_contacts) else 'formal',
                         'recipient_timezone':LOCAL_TIMEZONES.get(r.get('website_country',r['country']),''),
                         'country':r['country'],'subject': subject, 'body': plain_text_body(body),'links':actual_links,
                         'body_sections':body_sections(body,is_taiwan_template(data)),
                         'partner_website':partner_website(r.get('website_country',r['country'])),
                         'html':email_html(body,signature,is_taiwan_template(data))})
    payload = {'sender': SETTINGS['sender'], 'messages': rendered,'attachments':attached,
               'language':language,'template_id':str(data.get('template_id','')),'country':template_country,
               'template_name':template['name'] if template else '临时编辑内容',
               'template_language':language or (template['language'] if template else ''),**activity}
    if taiwan_signature:
        payload['line_signature'] = taiwan_signature
    if english_signature:
        payload['whatsapp_signature'] = english_signature
    with db() as c:
        c.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',
                  (ident, json.dumps(payload, ensure_ascii=False), digest(payload), None, 'draft', now()))
    return {'id': ident, 'digest': digest(payload), **payload}

def approve_batch(ident, expected):
    with LOCK, db() as c:
        batch = c.execute('SELECT * FROM batches WHERE id=?', (ident,)).fetchone()
        if not batch or batch['status'] != 'draft' or batch['digest'] != expected:
            raise ValueError('预览已变化，请重新生成并确认')
        if json.loads(batch['payload'])['sender'] != SETTINGS['sender']:
            raise ValueError('发件员工已变化，请重新预览确认')
        c.execute("UPDATE batches SET approved_digest=digest,status='approved' WHERE id=?", (ident,))
    return {'status': 'approved'}

def smtp(sender=None, password=None):
    sender = sender or SETTINGS['sender']
    password = SETTINGS['password'] if password is None else password
    if not password:
        raise ValueError('请先连接公司邮箱；网页登录不能替代 SMTP 密码')
    client = smtplib.SMTP_SSL(SETTINGS['smtp_host'], SETTINGS['smtp_port'],
                              context=tls_context(), timeout=30)
    try:
        client.login(sender, password)
    except smtplib.SMTPAuthenticationError:
        MAIL_SESSIONS.pop(sender,None)
        if sender==SETTINGS['sender']:SETTINGS['password']=''
        client.close()
        raise
    except Exception:
        client.close()
        raise
    return client

def approved_payload(c, ident, scheduled=False):
    batch = c.execute('SELECT * FROM batches WHERE id=?', (ident,)).fetchone()
    if not batch or batch['status'] != ('scheduled' if scheduled else 'approved') or batch['approved_digest'] != batch['digest']:
        raise ValueError('必须先确认本批模板和完整名单；已执行批次请新建预览再发送')
    payload = json.loads(batch['payload'])
    if digest(payload) != batch['digest'] or (not scheduled and payload['sender'] != SETTINGS['sender']):
        raise ValueError('内容或发件邮箱发生变化，请重新预览确认')
    return batch, payload

def send_review(c, batch, payload, roster, scheduled_at=''):
    stopped = stop_details(c)
    recipients=[]
    history=[]
    ident=payload.get('activity_id','')
    prior_rows=[dict(row) for row in c.execute('SELECT id,contact_id,recipient,status,sent_at,reply_status,reply_at,activity_id FROM messages WHERE batch_id<>? ORDER BY rowid DESC',(batch['id'],))]
    histories=mailbox_histories(prior_rows)
    companies={}
    for message in payload['messages']:
        if message.get('message_type')=='test' or ('message_type' not in message and test_recipient(message, roster)):
            continue
        prior=[dict(row) for row in c.execute(
            "SELECT id,status,sent_at,reply_status,reply_at FROM messages WHERE recipient=? AND batch_id<>? AND status NOT IN ('发送失败','本批已暂停','已取消定时','定时失败','已错过时间') ORDER BY rowid DESC",
            (message['recipient'],batch['id']))]
        address=message['recipient'].lower()
        contact=next((row for row in roster if row['id']==message['contact_id']),{})
        flagged=address in stopped or any(
            row.get('blocked') for row in roster
            if row['id']==message['contact_id'] or row['email'].lower()==address)
        facts=histories.get(address,{})
        current=facts.get('by_activity',{}).get(ident,{}) if ident else {}
        stop=stopped.get(address) or (SHEET_STOP if flagged else {})
        company_id=message.get('company_id') or contact.get('company_id') or message['contact_id']
        companies.setdefault(company_id,[]).append({'name':message['name'],'recipient':address})
        recipients.append({'name':message['name'],'recipient':address,
                           'previous_count':len(prior),'last_status':prior[0]['status'] if prior else '',
                           'last_sent_at':facts.get('last_sent_at',''),'accepted_count':facts.get('accepted_count',0),
                           'historical_pending_count':sum(item.get('pending_count',0) for item in facts.get('by_activity',{}).values()),
                           'historical_uncertain_count':sum(item.get('uncertain_count',0) for item in facts.get('by_activity',{}).values()),
                           'current_activity_count':current.get('accepted_count',0),
                           'current_activity_pending':current.get('pending_count',0),
                           'current_activity_uncertain':current.get('uncertain_count',0),
                           'company_id':company_id,'email_role':message.get('email_role') or contact.get('email_role','未核实'),
                           'stopped':bool(flagged),'stop_reason':'','stop_label':'','stop_next_action':'',**stop})
        history.append(prior)
    warnings=[{'company_id':company,'recipients':[item['recipient'] for item in members],
               'names':[item['name'] for item in members],'message':'同一公司选择了多个邮箱，请确认本次联系对象'}
              for company,members in companies.items() if len({item['recipient'] for item in members})>1]
    snapshot={'batch_id':batch['id'],'batch_digest':batch['digest'],'recipients':recipients,'history':history,'same_company_warnings':warnings}
    if scheduled_at:snapshot['scheduled_at']=scheduled_at
    return {'required':bool(recipients),'digest':digest(snapshot),'sender':payload['sender'],
            'count':len(payload['messages']),'recipients':recipients,'scheduled_at':scheduled_at,
            'activity_id':ident,'activity_name':payload.get('activity_name') or LEGACY_ACTIVITY_NAME,
            'historical_count':sum(row['accepted_count']>0 for row in recipients),
            'current_activity_count':sum(row['current_activity_count']>0 for row in recipients),
            'current_activity_pending_count':sum(row['current_activity_pending']>0 or row['current_activity_uncertain']>0 for row in recipients),
            'historical_unresolved_count':sum(row['historical_pending_count']>0 or row['historical_uncertain_count']>0 for row in recipients),
            'stopped_count':sum(row['stopped'] for row in recipients),'same_company_warnings':warnings}

def schedule_time(value):
    try:
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?',value):
            raise ValueError()
        stamp=datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Tokyo'))
        if stamp<=datetime.fromisoformat(now()):raise ValueError()
    except (ValueError,TypeError):
        raise ValueError('请选择未来的发送时间（日本时间）') from None
    return stamp.isoformat(timespec='seconds')

def check_send(ident, scheduled_at=None):
    with LOCK, db() as c:
        batch,payload=approved_payload(c,ident)
        stamp=schedule_time(scheduled_at) if scheduled_at is not None else ''
        return send_review(c,batch,payload,contacts(),stamp)

def insert_message_rows(c, ident, payload, roster, scheduled_at=''):
    for m in payload['messages']:
        kind=m.get('message_type') or ('test' if test_recipient(m,roster) else 'formal')
        c.execute('INSERT INTO messages(id,batch_id,contact_id,name,recipient,subject,body,status,country,attachments,sender,message_type,send_mode,scheduled_at,recipient_timezone,activity_id,activity_name,activity_purpose,company_id,email_role,contact_name,contact_title,template_id,template_name,template_language) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (make_msgid(domain=payload['sender'].split('@')[1]),ident,m['contact_id'],m['name'],m['recipient'],m['subject'],m['body'],
                   '待定时发送' if scheduled_at else '待发送',m.get('country','未分类'),json.dumps(payload.get('attachments',[]),ensure_ascii=False),
                   payload['sender'],kind,'scheduled' if scheduled_at else 'immediate',scheduled_at,
                   m.get('recipient_timezone',LOCAL_TIMEZONES.get(m.get('country'),'')),
                   payload.get('activity_id',''),payload.get('activity_name',''),payload.get('activity_purpose',''),
                   m.get('company_id',''),m.get('email_role',''),m.get('contact_name',''),m.get('contact_title',''),
                   payload.get('template_id',''),payload.get('template_name',''),payload.get('template_language',payload.get('language',''))))

def schedule_batch(ident, scheduled_at, confirmation):
    with LOCK, db() as c:
        batch,payload=approved_payload(c,ident)
        if not SETTINGS['password']:raise ValueError('请先连接当前发件邮箱，再安排定时发送')
        stamp=schedule_time(scheduled_at)
        roster=contacts()
        review=send_review(c,batch,payload,roster,stamp)
        if not isinstance(confirmation,str) or not hmac.compare_digest(confirmation,review['digest']):
            raise ValueError('请再次确认定时发送的时间、收件人和发送历史')
        # Persist only frozen approved content and the authorization, never credentials.
        c.execute('INSERT INTO schedules(batch_id,scheduled_at,status,batch_digest,review) VALUES(?,?,?,?,?)',
                  (ident,stamp,'scheduled',batch['digest'],json.dumps(review,ensure_ascii=False)))
        insert_message_rows(c,ident,payload,roster,stamp)
        c.execute("UPDATE batches SET status='scheduled' WHERE id=?",(ident,))
    return {'ok':True,'scheduled_at':stamp,'count':len(payload['messages'])}

def mark_schedule(c, ident, state, message_status, reason):
    c.execute('UPDATE schedules SET status=?,error=? WHERE batch_id=?',(state,reason,ident))
    c.execute("UPDATE messages SET status=?,error=?,next_step=?,sheet_synced=0 WHERE batch_id=? AND status NOT IN ('服务器已接受','结果待核对','退信')",
              (message_status,reason,'取消定时后可重新安排' if state in ('missed','needs_review') else '',ident))

def cancel_schedule(ident):
    with LOCK, db() as c:
        task=c.execute('SELECT * FROM schedules WHERE batch_id=?',(ident,)).fetchone()
        if not task or task['status'] not in SCHEDULE_CANCELABLE:
            raise ValueError('任务已开始发送或已结束，不能取消')
        mark_schedule(c,ident,'cancelled','已取消定时','')
        c.execute("UPDATE batches SET status='cancelled' WHERE id=?",(ident,))
    return {'ok':True}

def process_schedules(current_time=None, transport_factory=None, pause=8):
    stamp=current_time or now()
    with LOCK, db() as c:
        due=[dict(row) for row in c.execute("SELECT * FROM schedules WHERE status IN ('scheduled','awaiting_mail') AND scheduled_at<=? ORDER BY scheduled_at,batch_id",(stamp,))]
    for task in due:
        ident=task['batch_id']
        with LOCK:
            execution_time=current_time or now()
            with db() as c:
                active=c.execute('SELECT status FROM schedules WHERE batch_id=?',(ident,)).fetchone()
                if not active or active['status'] not in SCHEDULE_PENDING:continue
                if datetime.fromisoformat(execution_time)-datetime.fromisoformat(task['scheduled_at'])>timedelta(minutes=5):
                    mark_schedule(c,ident,'missed','已错过时间','已超过计划时间 5 分钟，请取消后重新安排')
                    continue
                try:
                    batch,payload=approved_payload(c,ident,scheduled=True)
                    review=send_review(c,batch,payload,contacts())
                    original={row['recipient']:row for row in json.loads(task['review'])['recipients']}
                    if any(row['stopped'] and (not original.get(row['recipient'],{}).get('stopped') or
                           ('stop_reason' in original.get(row['recipient'],{}) and row['stop_reason']!=original[row['recipient']]['stop_reason'])) for row in review['recipients']):
                        mark_schedule(c,ident,'needs_review','待重新确认','收件人新增或变更停止联系原因，请核对后重新安排')
                        continue
                    if not session_password(payload['sender']) and transport_factory is None:
                        mark_schedule(c,ident,'awaiting_mail','待连接邮箱','到时未连接发件邮箱；请连接，将在计划时间后 5 分钟内继续发送')
                        continue
                    c.execute("UPDATE schedules SET status='running',error='' WHERE batch_id=?",(ident,))
                except ValueError as exc:
                    mark_schedule(c,ident,'failed','定时失败',str(exc))
                    continue
            try:
                send_batch(ident,transport=transport_factory(payload['sender']) if transport_factory else None,pause=pause,scheduled=True)
            except Exception as exc:
                with db() as c:mark_schedule(c,ident,'failed','定时失败',str(exc) if isinstance(exc,ValueError) else type(exc).__name__)

def scheduler_loop():
    while not SCHEDULER_STOP.wait(5):
        try:process_schedules()
        except Exception as exc:print('Scheduler check failed: '+type(exc).__name__,flush=True)

def send_batch(ident, transport=None, pause=8, confirmation=None, scheduled=False):
    with LOCK:
        if not scheduled and not transport and not SETTINGS['password']:
            raise ValueError('请先连接公司邮箱，再发送已批准批次')
        with db() as c:
            batch,payload=approved_payload(c,ident,scheduled=scheduled)
            if scheduled:
                task=c.execute('SELECT * FROM schedules WHERE batch_id=?',(ident,)).fetchone()
                if not task or task['status']!='running' or task['batch_digest']!=batch['digest']:
                    raise ValueError('定时任务未授权或已执行')
            attachment_contents=[(file,read_attachment(file)) for file in payload.get('attachments',[])]
            signature=payload.get('line_signature') or payload.get('whatsapp_signature')
            qr_content=read_line_qr(signature) if signature else None
            current_contacts = contacts()
            review=send_review(c,batch,payload,current_contacts)
            if not scheduled and review['required'] and (not isinstance(confirmation,str) or not hmac.compare_digest(confirmation,review['digest'])):
                raise ValueError('请再次确认正式邮件的收件人和发送历史；如记录有变化，点击发送重新确认')
            c.execute("UPDATE batches SET status='sending' WHERE id=?", (ident,))
            if scheduled:
                rows=[dict(row) for row in c.execute('SELECT * FROM messages WHERE batch_id=?',(ident,))]
                expected={(m['contact_id'],m['recipient'],m['subject'],m['body']) for m in payload['messages']}
                actual={(m['contact_id'],m['recipient'],m['subject'],m['body']) for m in rows}
                if len(rows)!=len(payload['messages']) or actual!=expected or any(row['deleted_at'] for row in rows):
                    raise ValueError('定时任务记录已变化，请取消后重新安排')
                c.execute("UPDATE messages SET status='待发送',error='',next_step='',sheet_synced=0 WHERE batch_id=?",(ident,))
            else:insert_message_rows(c,ident,payload,current_contacts)
        client = None
        failed = False
        try:
            client = transport or (smtp(payload['sender'],session_password(payload['sender'])) if scheduled else smtp())
            with db() as c:
                rows = c.execute('SELECT * FROM messages WHERE batch_id=? ORDER BY rowid', (ident,)).fetchall()
            for index, row in enumerate(rows):
                message = EmailMessage()
                message['From'], message['To'] = payload['sender'], row['recipient']
                message['Subject'], message['Message-ID'], message['Date'] = row['subject'], row['id'], formatdate(localtime=True)
                message.set_content(row['body'])
                frozen=next(item for item in payload['messages'] if item['contact_id']==row['contact_id'])
                message.add_alternative(frozen.get('html') or email_html(row['body'],signature),subtype='html')
                if signature:
                    subtype=signature.get('mime','image/png').split('/')[1]
                    filename='HIWIN-'+signature.get('channel','LINE')+('.jpg' if subtype=='jpeg' else '.png')
                    message.get_payload()[-1].add_related(qr_content,maintype='image',subtype=subtype,
                        cid='<'+signature['cid']+'>',disposition='inline',filename=filename)
                for file,content in attachment_contents:
                    maintype,subtype=file['mime'].split('/',1)
                    message.add_attachment(content,maintype=maintype,subtype=subtype,filename=file['name'])
                with db() as c:
                    c.execute("UPDATE messages SET status='发送中',attempted_at=? WHERE id=?", (now(),row['id']))
                try:
                    refused = client.send_message(message)
                    if refused:
                        raise smtplib.SMTPRecipientsRefused(refused)
                except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
                    set_result(row['id'], '发送失败', type(exc).__name__)
                    failed = True
                    break
                except Exception as exc:
                    set_result(row['id'], '结果待核对', type(exc).__name__)
                    failed = True
                    break
                else:
                    set_result(row['id'], '服务器已接受', '')
                    if index + 1 < len(rows) and pause:
                        time.sleep(pause)
        except Exception as exc:
            failed = True
            with db() as c:
                c.execute("UPDATE messages SET status='发送失败',error=? WHERE batch_id=? AND status='待发送'", (type(exc).__name__, ident))
        finally:
            if client and not transport:
                try:
                    client.quit()
                except Exception:
                    client.close()
            with db() as c:
                c.execute("UPDATE messages SET status='本批已暂停',next_step='本批发生异常，未发送的记录需新批次确认' WHERE batch_id=? AND status='待发送'", (ident,))
                c.execute("UPDATE batches SET status=? WHERE id=?", ('needs_review' if failed else 'complete', ident))
                if scheduled:c.execute('UPDATE schedules SET status=? WHERE batch_id=?',('failed' if failed else 'complete',ident))
            sync_result=sync_sheet()
    return {**status(),'send_progress':send_progress(ident),'sheet_sync':sync_result}

def set_result(ident, state, error):
    with db() as c:
        c.execute('UPDATE messages SET status=?,error=?,sent_at=?,sheet_synced=0 WHERE id=?',
                  (state, error, now() if state == '服务器已接受' else None, ident))

def log_rows():
    with db() as c:
        rows=[dict(r) for r in c.execute("SELECT * FROM messages WHERE deleted_at='' ORDER BY rowid DESC")]
        tasks={r['batch_id']:r['status'] for r in c.execute('SELECT batch_id,status FROM schedules')}
        active={r[0] for r in c.execute("SELECT id FROM batches WHERE status='sending'")}
        stops=stop_details(c)
    metadata=contact_metadata()
    for row in rows:
        row['country']=normalize_country(row['country'])
        row['attachments']=json.loads(row['attachments'])
        row['recipient_timezone']=row['recipient_timezone'] or LOCAL_TIMEZONES.get(row['country'],'')
        row['schedule_status']=tasks.get(row['batch_id'],'')
        row['can_cancel_schedule']=row['schedule_status'] in SCHEDULE_CANCELABLE
        row['can_delete']=row['batch_id'] not in active and row['status'] not in ('待发送','发送中') and row['schedule_status'] not in (*SCHEDULE_PENDING,'running','needs_review','missed')
        identity=contact_identity(row,metadata)
        for key,value in identity.items():
            if not row.get(key):row[key]=value
        row['activity_name']=row['activity_name'] or LEGACY_ACTIVITY_NAME
        row['confirmed_sent']=bool(row['sent_at'])
        # Reply/manual-stop statuses used to overwrite status; sent_at remains the
        # physical SMTP acceptance evidence and is shown independently.
        row['send_status']='服务器已接受' if row['sent_at'] else row['status']
        stop=stops.get(str(row['recipient'] or '').lower(),{})
        row.update({'stop_reason':'','stop_label':'','stop_next_action':'',**stop})
        row['recommended_next_action']=stop.get('stop_next_action') or row['next_step'] or {
            '服务器已接受':'等待回复，按活动计划人工跟进',
            '发送失败':'核实错误及邮箱地址，确认后新建批次重发',
            '结果待核对':'检查已发送邮件及收件情况，核对后再决定是否重发',
            '退信':'核对退信原因，通过电话／官网核实收件邮箱',
            '已回复':'查看来信并人工跟进',
        }.get(row['status'],'查看发送状态并完成当前操作')
        row['reply_detail']={'text':row['reply_text'],'sender':row['reply_sender'],'subject':row['reply_subject'],
                             'received_at':row['reply_date'],'detected_at':row['reply_at'],
                             'message_id':row['reply_message_id'],'match_reason':row['reply_match_reason']
                             } if row['reply_message_id'] or row['reply_text'] or row['reply_sender'] else None
    return rows

def delete_records(ids):
    if not isinstance(ids,list) or not ids or len(ids)>10000 or any(not isinstance(ident,str) or not ident for ident in ids) or len(set(ids))!=len(ids):
        raise ValueError('请选择有效且不重复的记录')
    with LOCK, db() as c:
        placeholders=','.join('?' for _ in ids)
        rows=[dict(row) for row in c.execute(f"SELECT * FROM messages WHERE id IN ({placeholders}) AND deleted_at=''",ids)]
        if len(rows)!=len(ids):raise ValueError('有记录已删除或不存在，请刷新后重新选择')
        for row in rows:
            batch=c.execute('SELECT status FROM batches WHERE id=?',(row['batch_id'],)).fetchone()
            task=c.execute('SELECT status FROM schedules WHERE batch_id=?',(row['batch_id'],)).fetchone()
            if row['status'] in ('待发送','发送中') or (batch and batch['status']=='sending'):
                raise ValueError('有批次仍在发送，请执行结束后再删除记录')
            if task and task['status'] in (*SCHEDULE_PENDING,'running','needs_review','missed'):
                raise ValueError('请先取消定时任务，再删除其记录')
        # Keep delivery/reply evidence, but remove it from the local list.
        c.execute(f'UPDATE messages SET deleted_at=? WHERE id IN ({placeholders})',[now(),*ids])
    return {'ok':True,'count':len(ids)}

def send_progress(ident=None):
    with db() as c:
        if ident is None:
            batch=c.execute("SELECT * FROM batches WHERE EXISTS(SELECT 1 FROM messages WHERE batch_id=batches.id) ORDER BY (status='sending') DESC,rowid DESC LIMIT 1").fetchone()
        else:
            batch=c.execute('SELECT * FROM batches WHERE id=?',(ident,)).fetchone()
        if not batch:return None
        rows=[dict(row) for row in c.execute('SELECT status,name,recipient,sent_at,sheet_synced FROM messages WHERE batch_id=? ORDER BY rowid',(batch['id'],))]
    accepted=sum(bool(row['sent_at']) for row in rows)
    failed=sum(row['status']=='发送失败' for row in rows)
    uncertain=sum(row['status']=='结果待核对' for row in rows)
    processed=accepted+failed+uncertain
    current=next((row for row in rows if row['status']=='发送中'),None)
    total=len(json.loads(batch['payload'])['messages'])
    return {'batch_id':batch['id'],'status':batch['status'],'total':total,'processed':processed,
            'accepted':accepted,'failed':failed,'uncertain':uncertain,'remaining':max(0,total-processed),
            'current_name':current['name'] if current else '',
            'sheet_pending':sum(not row['sheet_synced'] for row in rows)}

def status():
    return {'messages': log_rows(), 'mail_connected': bool(SETTINGS['password']),
            'sheet_configured': bool(SETTINGS['bridge_url'] and SETTINGS['bridge_secret']),
            'sheet_saved':SETTINGS.get('sheet_saved',False),'sheet_store_error':SETTINGS.get('sheet_store_error',''),
            'bridge_url':SETTINGS['bridge_url'],'sender': SETTINGS['sender'],
            'smtp_endpoint':f"{SETTINGS['smtp_host']}:{SETTINGS['smtp_port']}",
            'send_progress':send_progress(),'activities':activity_rows(),'template_folders':True}

def app_state():
    info=status()
    roster=contacts()
    groups,groups_error=contact_groups(roster)
    countries=sorted(set(WEBSITE_ROUTES)|{r['country'] for r in roster}|{r['country'] for r in info['messages']})
    with db() as c:
        preferred=c.execute("SELECT value FROM preferences WHERE key='template'").fetchone()
        contacts_stamp=c.execute("SELECT value FROM preferences WHERE key='contacts_refreshed_at'").fetchone()
        country_templates={r['key'].removeprefix('country-template:'):r['value'] for r in c.execute("SELECT * FROM preferences WHERE key LIKE 'country-template:%'")}
        activity_country_templates={}
        for row in c.execute("SELECT * FROM preferences WHERE key LIKE 'activity-country-template:%'"):
            activity,country=row['key'].removeprefix('activity-country-template:').split(':',1)
            activity_country_templates.setdefault(activity,{})[country]=row['value']
    return {'contacts':roster,'countries':countries,'templates':template_rows(),
            'contact_groups':groups,'contact_groups_error':groups_error,
            'country_templates':country_templates,'activity_country_templates':activity_country_templates,
            'partner_websites':{country:partner_website(country) for country in countries},
            'contact_config':contact_config(),
            'contacts_refreshed_at':contacts_stamp[0] if contacts_stamp else '',
            'spreadsheet_url':'https://docs.google.com/spreadsheets/d/'+urllib.parse.quote(SPREADSHEET,safe='')+'/edit' if SPREADSHEET else '',
            'mail_accounts':[{**account,'connected_in_session':bool(MAIL_SESSIONS.get(account['email']))} for account in mail_accounts()],
            'preferred_template':preferred[0] if preferred else 'outreach-zh-TW',**info}

def sheet_sync_chunks(rows):
    chunk=[]
    for row in rows:
        candidate=chunk+[row]
        if chunk and (len(candidate)>MAX_SHEET_CHUNK_ROWS or len(sheet_bridge_request_data({'rows':candidate}))>MAX_SHEET_REQUEST_BYTES):
            yield chunk
            chunk=[]
        chunk.append(row)
        if len(sheet_bridge_request_data({'rows':chunk}))>MAX_SHEET_REQUEST_BYTES:
            raise ValueError('单条邮件记录过大，无法同步到 Sheet；完整记录仍保存在本地')
    if chunk:yield chunk

def sync_sheet(probe=False):
    with SYNC_LOCK:
        if not SETTINGS['bridge_url'] or not SETTINGS['bridge_secret']:
            return {'ok': False, 'error': 'Google Sheet 尚未连接；记录已保存本地'}
        with db() as c:
            pending = [dict(r) for r in c.execute('SELECT * FROM messages WHERE sheet_synced=0')]
        if not pending and not probe:
            return {'ok': True, 'count': 0}
        synced=0
        try:
            for chunk in sheet_sync_chunks(pending) if pending else [[]]:
                result=sheet_bridge_request({'rows':chunk})
                if not result.get('ok'):
                    raise ValueError('同步端点拒绝请求，请检查部署与表格连接')
                if result.get('count')!=len(chunk):
                    raise ValueError('Sheet 返回的同步笔数不一致，请重试同步')
                with db() as c:
                    # Commit each acknowledged chunk; leave concurrent changes pending.
                    for row in chunk:
                        changed=c.execute('UPDATE messages SET sheet_synced=1 WHERE id=? AND status=? AND reply_status=? AND reply_at=? AND next_step=?',
                                  (row['id'], row['status'], row['reply_status'], row['reply_at'], row['next_step']))
                        synced+=changed.rowcount
            return {'ok': True, 'count': synced}
        except Exception as exc:
            reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            return {'ok': False, 'count':synced,'error':f'Sheet 同步失败：{reason}；已同步 {synced} 条，剩余记录可只重试同步，邮件不会重发'}

def delivery_blocks(msg):
    blocks=[]
    for part in msg.walk():
        if part.get_content_type() == 'message/delivery-status':
            payload=part.get_payload()
            if isinstance(payload,list):
                blocks.extend(block for block in payload if block.get('Action'))
    return blocks

def is_delivery_notice(msg):
    if msg.get_content_type() == 'multipart/report' or delivery_blocks(msg):
        return True
    address=parseaddr(msg.get('From',''))[1].lower()
    subject=str(msg.get('Subject','')).lower()
    return ('mailer-daemon' in address or address.startswith('postmaster@')) and any(
        token in subject for token in ('undeliver','delivery','returned','failure','未送达','未送達','配信不能'))

def incoming_references(msg):
    refs=set()
    delivery=is_delivery_notice(msg)
    for part in msg.walk():
        fields=['In-Reply-To','References','Original-Message-ID']
        if delivery and part is not msg:
            fields.append('Message-ID')
        for field in fields:
            refs.update(re.findall(r'<[^<>\s]+>',str(part.get(field,''))))
        # Some delivery servers put the original headers in the explanatory text.
        if delivery and part.get_content_type() == 'text/plain':
            try:text=part.get_content()
            except (LookupError,UnicodeError,ValueError):continue
            if isinstance(text,str):
                refs.update(re.findall(r'(?im)^(?:Original-)?Message-ID:\s*(<[^<>\s]+>)',text))
    return refs

def delivery_result(msg, recipient=None):
    blocks=delivery_blocks(msg)
    failed=[block for block in blocks if str(block.get('Action','')).lower()=='failed']
    reason='；'.join(' / '.join(str(block.get(field,'')) for field in
                    ('Final-Recipient','Action','Status','Diagnostic-Code')).strip()
                    for block in blocks)
    reason=re.sub(r'\s+',' ',reason)[:1600]
    if not failed:
        return '投递通知','人工核对投递通知；不计为人工回复',reason,False
    addresses={str(block.get('Final-Recipient','')).split(';')[-1].strip().lower() for block in failed}
    if recipient and addresses-{''} and recipient.lower() not in addresses:
        return '投递通知','原邮件转投其他地址后失败；联系对方确认收件及转发设置，暂不改原邮箱',reason,False
    codes=[str(block.get('Status','')).strip() for block in failed]
    if any(code in ('4.2.2','5.2.2') for code in codes):
        return '退信','收件邮箱满或容量异常；请对方清理邮箱／提供替代地址，人工确认后再重发',reason,False
    invalid=all(re.fullmatch(r'5\.1\.[1236]',code) for code in codes)
    if invalid:
        return '退信','收件地址不存在或无效；暂停向旧地址发送，通过官网／电话核实新邮箱',reason,True
    return '退信','投递失败；人工核对服务器、认证或内部转投问题，暂缓重发',reason,False

def classify(msg):
    if is_delivery_notice(msg):
        kind,step,_,_=delivery_result(msg)
        return kind,step
    if msg.get('Auto-Submitted','no').lower()!='no' or msg.get('X-Autoreply') or msg.get('X-Autorespond'):
        return '自动回复','人工检查返岗日期；不自动发送后续邮件'
    return '已回复','人工查看邮件并准备下一步回复'

def incoming_text(msg):
    try:
        part=msg.get_body(preferencelist=('plain','html'))
        if part is None:return ''
        value=part.get_content()
    except (AttributeError,LookupError,UnicodeError,ValueError):
        return ''
    if not isinstance(value,str):return ''
    if part.get_content_type()=='text/html':
        value=re.sub(r'(?is)<(script|style)\b[^>]*>.*?</\1>','',value)
        value=re.sub(r'(?i)<br\s*/?>|</(?:p|div|li|tr)>','\n',value)
        value=html.unescape(re.sub(r'<[^>]+>','',value))
    return value.replace('\r\n','\n').strip()[:32000]

def record_incoming(raw, sender=None):
    sender=sender or SETTINGS['sender']
    msg=BytesParser(policy=policy.default).parsebytes(raw)
    legacy_id=hashlib.sha256(raw if sender==ADDRESS else sender.encode()+b'\n'+raw).hexdigest()
    mail_id=str(msg.get('Message-ID','')).strip()
    event_id=hashlib.sha256((sender+'\n'+mail_id).encode()).hexdigest() if re.fullmatch(r'<[^<>\s]+>',mail_id) else legacy_id
    refs=incoming_references(msg)
    text=incoming_text(msg)
    with LOCK,db() as c:
        if c.execute('SELECT 1 FROM inbox_events WHERE id IN (?,?)',(event_id,legacy_id)).fetchone():
            return 0
        matched=[r for r in c.execute('SELECT * FROM messages WHERE sender=?',(sender,)) if r['id'] in refs]
        kind,step=classify(msg)
        reference_match=bool(matched)
        if not matched:
            address=parseaddr(msg.get('From',''))[1].lower()
            matched=c.execute('SELECT * FROM messages WHERE lower(recipient)=? AND sender=?',(address,sender)).fetchall()
            if matched:
                kind,step='待人工匹配','同邮箱来信没有原邮件引用，请人工确认关联'
        updated=0
        for row in matched:
            row_kind,row_step,reason,stop=kind,step,'',kind=='已回复'
            if reference_match and is_delivery_notice(msg):
                row_kind,row_step,reason,stop=delivery_result(msg,row['recipient'])
            if row['reply_status']=='已回复' or row['status']=='已停止':
                continue
            # Preserve reviewed details (including manual audit results) on re-scans.
            if row['reply_status']==row_kind and row['next_step']:
                continue
            if row_kind in ('自动回复','待人工匹配','投递通知') and row['reply_status']=='退信':
                continue
            state='退信' if row_kind=='退信' else ('已回复' if row_kind=='已回复' else row['status'])
            c.execute('UPDATE messages SET status=?,reply_status=?,reply_at=?,next_step=?,error=?,reply_text=?,reply_sender=?,reply_subject=?,reply_date=?,reply_message_id=?,reply_match_reason=?,sheet_synced=0 WHERE id=?',
                      (state,row_kind,now(),row_step,reason or row['error'],text,str(msg.get('From','')),str(msg.get('Subject','')),
                       str(msg.get('Date','')),mail_id,'邮件引用匹配' if reference_match else '同邮箱来信，待人工确认关联',row['id']))
            if stop:
                record_stop(c,row['recipient'],'human_reply' if row_kind=='已回复' else 'address_invalid',row['id'])
            updated+=1
        if matched:
            c.execute('INSERT INTO inbox_events VALUES(?,?)',(event_id,now()))
    return updated

def decode_imap_name(value):
    def segment(match):
        encoded=match.group(1)
        if not encoded:return '&'
        try:
            return base64.b64decode(encoded.replace(',','/')+'='*((-len(encoded))%4)).decode('utf-16-be')
        except (ValueError,UnicodeError):
            return match.group(0)
    return re.sub(r'&([^-]*)-',segment,value.decode('utf-8',errors='replace'))

def incoming_folders(client):
    folders=[{'mailbox':'INBOX','name':'INBOX'}]
    warnings=[]
    try:
        result,listed=client.list()
        if result!='OK':raise ValueError('folder list')
    except (imaplib.IMAP4.error,OSError,ValueError):
        return folders,['无法列出邮件文件夹，本次仅检查收件箱']
    excluded={'sent','sentitems','sentmessages','sentmail','draft','drafts','outbox',
              '已发送','已寄出','寄件备份','寄件備份','寄件匣','已发送邮件','已发送项目',
              '草稿','草稿箱','发件箱','送信済み','送信済みメール','送信済みアイテム','下書き'}
    for entry in listed or []:
        literal=None
        if isinstance(entry,tuple):entry,literal=entry
        if not isinstance(entry,bytes):
            warnings.append('有文件夹名称无法解析，未检查该文件夹');continue
        match=re.fullmatch(rb'\(([^)]*)\)\s+("(?:[^"\\]|\\.)*"|NIL)\s+(.+)',entry)
        if not match:
            warnings.append('有文件夹名称无法解析，未检查该文件夹');continue
        flags=set(match[1].lower().split())
        if flags & {b'\\noselect',b'\\sent',b'\\drafts'}:continue
        mailbox=match[3]
        if mailbox.startswith(b'{'):
            if not isinstance(literal,bytes):
                warnings.append('有文件夹名称无法解析，未检查该文件夹');continue
            mailbox=b'"'+literal.replace(b'\\',b'\\\\').replace(b'"',b'\\"')+b'"'
        unquoted=mailbox[1:-1] if mailbox.startswith(b'"') and mailbox.endswith(b'"') else mailbox
        unquoted=re.sub(rb'\\(.)',rb'\1',unquoted)
        name=decode_imap_name(unquoted)
        delimiter=match[2].strip(b'"').decode('ascii',errors='replace')
        leaf=name.rsplit(delimiter,1)[-1] if delimiter!='NIL' else name
        if re.sub(r'[\s_-]','',leaf).lower() in excluded:continue
        if unquoted.upper()==b'INBOX':continue
        if not any(f['mailbox']==mailbox for f in folders):
            folders.append({'mailbox':mailbox,'name':name})
    return folders,warnings

def check_inbox():
    with LOCK:
        if not SETTINGS['password']:
            raise ValueError('先在工具中连接当前公司邮箱：'+SETTINGS['sender'])
        sender=SETTINGS['sender']
        client=imaplib.IMAP4_SSL(SETTINGS['imap_host'],SETTINGS['imap_port'],ssl_context=tls_context(),timeout=30)
        count=scanned=fetched_count=0
        folder_results=[]
        warnings=[]
        try:
            try:client.login(sender,SETTINGS['password'])
            except imaplib.IMAP4.error:
                raise ValueError('公司邮箱 IMAP 登录失败；请核对当前员工的密码及收信权限') from None
            folders,warnings=incoming_folders(client)
            since=(datetime.now(ZoneInfo('Asia/Tokyo'))-timedelta(days=30)).strftime('%d-%b-%Y')
            for folder in folders:
                info={'name':folder['name'],'scanned':0,'fetched':0,'matched':0,'ok':False}
                folder_results.append(info)
                try:
                    result,_=client.select(folder['mailbox'],readonly=True)
                    if result!='OK':raise ValueError('无法只读打开')
                    result,values=client.uid('search',None,'SINCE',since)
                    if result!='OK':raise ValueError('无法搜索最近30天来信')
                    if not values or not isinstance(values[0],bytes):raise ValueError('搜索结果不完整')
                    ids=values[0].split()
                    info['scanned']=len(ids);scanned+=len(ids)
                    for start in range(0,len(ids),10):
                        chunk=ids[start:start+10]
                        result,fetched=client.uid('fetch',b','.join(chunk),'(UID BODY.PEEK[])')
                        if result!='OK':
                            warnings.append(folder['name']+'：有一批来信读取失败，请重试检查');continue
                        messages=[item[1] for item in fetched or [] if isinstance(item,tuple) and isinstance(item[1],bytes)]
                        info['fetched']+=len(messages);fetched_count+=len(messages)
                        if len(messages)!=len(chunk):
                            warnings.append(folder['name']+'：有来信未完整返回，请重试检查')
                        for raw in messages:
                            updated=record_incoming(raw,sender)
                            count+=updated;info['matched']+=updated
                    info['ok']=info['scanned']==info['fetched']
                except (imaplib.IMAP4.error,OSError,ValueError):
                    warnings.append(folder['name']+'：文件夹检查未完成，请重试检查')
            sync_result=sync_sheet()
            return {'ok':True,'complete':not warnings,'sender':sender,'matched':count,'scanned':scanned,
                    'fetched':fetched_count,'folders':folder_results,'warnings':list(dict.fromkeys(warnings)),
                    'sheet_sync':sync_result}
        finally:
            try:client.logout()
            except Exception:pass

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, value, code=200, content_type='application/json'):
        raw = value if isinstance(value, bytes) else value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type + ('' if isinstance(value,bytes) else '; charset=utf-8'))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def valid_host(self):
        hosts=self.headers.get_all('Host', [])
        return len(hosts)==1 and hosts[0] in {
            f'127.0.0.1:{self.server.server_port}',
            f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self.valid_host():
            return self.respond({'error': 'Invalid host'}, 403)
        if self.path == '/':
            return self.respond((ROOT / 'index.html').read_text().replace('__CSRF__', TOKEN), content_type='text/html')
        static={'/app.js':('app.js','application/javascript'),'/style.css':('style.css','text/css')}
        if self.path in static:
            file,mime=static[self.path]
            return self.respond((ROOT/file).read_text(),content_type=mime)
        assets={'/assets/'+file:file for file in qr_files()}
        if self.path in assets:
            file=assets[self.path]
            return self.respond((ROOT/'assets'/file).read_bytes(),content_type='image/jpeg' if file.endswith('.jpg') else 'image/png')
        if self.headers.get('X-Local-Token') != TOKEN:
            return self.respond({'error': 'Forbidden'}, 403)
        if self.path == '/api/state':
            return self.respond(app_state())
        if self.path == '/api/records':
            return self.respond({'messages':log_rows(),'send_progress':send_progress(),
                                 'contacts':contacts(),'activities':activity_rows()})
        if urllib.parse.urlsplit(self.path).path == '/api/send-progress':
            ident=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('id',[None])[0]
            progress=send_progress(ident)
            return self.respond({'progress':progress},200 if progress else 404)
        self.respond({'error': 'Not found'}, 404)

    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-Local-Token') != TOKEN:
            return self.respond({'error': 'Forbidden'}, 403)
        origin = self.headers.get('Origin')
        if origin and origin != f"http://{self.headers.get('Host')}":
            return self.respond({'error': 'Invalid origin'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            limit=MAX_ATTACHMENT_BYTES*4//3+10000 if self.path in ('/api/upload','/api/contact-qr') else 300000
            if not 0 < size <= limit:
                raise ValueError('请求过大或为空')
            data = json.loads(self.rfile.read(size))
            if self.path not in POST_ROUTES:
                return self.respond({'error': 'Not found'}, 404)
            action, locked, sender_bound = POST_ROUTES[self.path]
            if not locked:
                return self.respond(action(data))
            with LOCK:
                if sender_bound:
                    require_current_sender(data)
                self.respond(action(data))
        except Exception as exc:
            self.respond({'error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}, 400)

def restore_sheet(_):
    restored=restore_sheet_settings()
    return {'ok':restored,'error':SETTINGS['sheet_store_error'] or '本机还没有保存过 Sheet 连接'}

# path: (action, run under LOCK, require the page's sender to match)
POST_ROUTES = {
    '/api/connect': (lambda d: connect_mail(d), False, False),
    '/api/account': (lambda d: save_mail_account(d), False, False),
    '/api/account/select': (lambda d: select_mail_account(d.get('email','')), False, False),
    '/api/contact-config': (lambda d: save_contact_config(d), True, False),
    '/api/contact-qr': (lambda d: upload_contact_qr(d), False, False),
    '/api/sheet': (lambda d: configure_sheet(d), False, False),
    '/api/sheet/restore': (lambda d: restore_sheet(d), True, False),
    '/api/contact': (lambda d: add_contact(d), False, False),
    '/api/contacts/refresh': (lambda d: refresh_sheet_contacts(), False, False),
    '/api/contacts/delete': (lambda d: delete_contacts(d.get('ids')), False, False),
    '/api/template': (lambda d: save_template(d), False, False),
    '/api/template/delete': (lambda d: delete_template(d), False, False),
    '/api/upload': (lambda d: upload_attachment(d), True, False),
    '/api/preview': (lambda d: create_batch(d), True, True),
    '/api/approve': (lambda d: approve_batch(d['id'], d['digest']), True, True),
    '/api/send-check': (lambda d: check_send(d['id'], *([d['scheduled_at']] if 'scheduled_at' in d else [])), True, True),
    '/api/schedule': (lambda d: schedule_batch(d['id'], d.get('scheduled_at'), d.get('confirmation')), True, True),
    '/api/schedule/cancel': (lambda d: cancel_schedule(d['id']), False, False),
    '/api/send': (lambda d: send_batch(d['id'], confirmation=d.get('confirmation')), True, True),
    '/api/records/delete': (lambda d: delete_records(d['ids'] if 'ids' in d else [d['id']]), False, False),
    '/api/sync': (lambda d: sync_sheet(), False, False),
    '/api/inbox': (lambda d: check_inbox(), True, True),
    '/api/stop': (lambda d: stop_contact(d), False, False),
}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    initialize()
    restore_mail_account()
    restore_sheet_settings()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    SCHEDULER_STOP.clear()
    threading.Thread(target=scheduler_loop,daemon=True,name='mail-scheduler').start()
    print(f'HIWIN tool: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SCHEDULER_STOP.set()
        server.server_close()
