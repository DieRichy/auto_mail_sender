// Paste into a standalone Apps Script project. Do not put secrets in this file.
const SPREADSHEET_ID = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID');
const LOG_TAB = '轻量工具日志';
const CONTACT_TAB = '邮件跟踪';
const HEADERS = ['邮件ID','批次ID','机构编号','机构名称','收件邮箱','实际主题','发送状态','发送时间（东京）','回复状态','回复检测时间','下一步','错误','实际正文','发件员工邮箱','国家／地区','附件名称'];

function doPost(e) {
  const lock = LockService.getScriptLock();
  let locked = false;
  try {
    if (!e || !e.postData || e.postData.contents.length > 300000) throw Error('invalid request');
    const request = JSON.parse(e.postData.contents);
    const secret = PropertiesService.getScriptProperties().getProperty('BRIDGE_SECRET');
    if (!secret || secret.length < 32) throw Error('bridge not configured');
    if (!/^[0-9]{10}$/.test(request.timestamp) || Math.abs(Date.now()/1000 - Number(request.timestamp)) > 300) throw Error('expired');
    if (!/^[a-f0-9]{32}$/.test(request.nonce) || typeof request.payload !== 'string') throw Error('invalid request');
    const signed = request.timestamp + '\n' + request.nonce + '\n' + request.payload;
    const mac = Utilities.computeHmacSha256Signature(signed, secret, Utilities.Charset.UTF_8)
      .map(b => ('0' + ((b + 256) % 256).toString(16)).slice(-2)).join('');
    const supplied = String(request.signature || '');
    let mismatch = mac.length ^ supplied.length;
    for (let i=0;i<mac.length;i++) mismatch |= mac.charCodeAt(i) ^ (supplied.charCodeAt(i)||0);
    if (mismatch) throw Error('unauthorized');
    const data = JSON.parse(request.payload);
    if (data.action === 'contacts') {
      lock.waitLock(10000); locked = true;
      const cache = CacheService.getScriptCache();
      if (cache.get(request.nonce)) throw Error('replayed');
      const result = readContacts();
      cache.put(request.nonce,'used',600);
      return jsonResponse(result);
    }
    if (!Array.isArray(data.rows) || data.rows.length > 1000) throw Error('invalid rows');
    for (const row of data.rows) {
      if (typeof row.id !== 'string' || !/^<[^<>\s]+>$/.test(row.id) || !row.recipient) throw Error('invalid row');
    }
    lock.waitLock(10000); locked = true;
    const cache = CacheService.getScriptCache();
    if (cache.get(request.nonce)) throw Error('replayed');
    const book = SpreadsheetApp.openById(SPREADSHEET_ID);
    let sheet = book.getSheetByName(LOG_TAB);
    if (!sheet) {
      sheet = book.insertSheet(LOG_TAB);
      sheet.getRange(1,1,1,HEADERS.length).setValues([HEADERS]).setFontWeight('bold').setBackground('#eeeeee');
      sheet.setFrozenRows(1);
      sheet.setColumnWidths(1,HEADERS.length,180);
      sheet.setColumnWidth(6,350); sheet.setColumnWidth(13,500);
    }
    if (sheet.getMaxColumns()<HEADERS.length) sheet.insertColumnsAfter(sheet.getMaxColumns(),HEADERS.length-sheet.getMaxColumns());
    sheet.getRange(1,1,1,HEADERS.length).setValues([HEADERS]).setFontWeight('bold').setBackground('#eeeeee');
    const index = new Map();
    if (sheet.getLastRow()>1) sheet.getRange(2,1,sheet.getLastRow()-1,1).getDisplayValues().forEach((r,i)=>index.set(r[0],i+2));
    let next = sheet.getLastRow()+1;
    for (const row of data.rows) {
      const attachments=typeof row.attachments==='string'?JSON.parse(row.attachments):row.attachments||[];
      const values = [row.id,row.batch_id,row.contact_id,row.name,row.recipient,row.subject,row.status,row.sent_at,row.reply_status,row.reply_at,row.next_step,row.error,row.body,row.sender||'',row.country||'',attachments.map(file=>file.name).join('、')]
        .map(v => { const text=String(v == null?'':v); return /^[=+@-]/.test(text)?"'"+text:text; });
      const position = index.get(row.id) || next++;
      if (position>sheet.getMaxRows()) sheet.insertRowsAfter(sheet.getMaxRows(),position-sheet.getMaxRows());
      sheet.getRange(position,1,1,HEADERS.length).setNumberFormat('@').setValues([values]).setWrap(true).setVerticalAlignment('top').setBackground(row.country==='测试'?'#fff4df':'#ffffff');
      index.set(row.id,position);
    }
    SpreadsheetApp.flush();
    cache.put(request.nonce,'used',600);
    return jsonResponse({ok:true,count:data.rows.length,columns:HEADERS.length});
  } catch (error) {
    return jsonResponse({ok:false,error:'Request rejected. Check deployment and bridge settings.'});
  } finally { if (locked) lock.releaseLock(); }
}

function readContacts() {
  const sheet = SpreadsheetApp.openById(SPREADSHEET_ID).getSheetByName(CONTACT_TAB);
  if (!sheet || sheet.getLastRow() < 2 || sheet.getLastRow() > 10001) throw Error('invalid contacts sheet');
  const values = sheet.getRange(1,1,sheet.getLastRow(),sheet.getLastColumn()).getDisplayValues();
  const headers = values.shift().map(value => value.trim());
  const columns = {};
  ['机构编号','机构名称','国家','收件邮箱'].forEach(name => {
    columns[name] = headers.indexOf(name);
    if (columns[name] < 0) throw Error('missing contact column');
  });
  const stopColumn = headers.indexOf('勿再联系');
  const statusColumn = headers.indexOf('发送状态');
  const contacts = values.filter(row => row.some(value => value.trim())).map(row => {
    const stop = stopColumn < 0 ? '' : row[stopColumn].trim().toLowerCase();
    const status = statusColumn < 0 ? '' : row[statusColumn].trim();
    return {id:row[columns['机构编号']].trim(),name:row[columns['机构名称']].trim(),
      country:row[columns['国家']].trim(),email:row[columns['收件邮箱']].trim(),
      blocked:['是','yes','true','1','勿再联系','停止联系'].includes(stop) || ['勿再联系','停止联系','已停止'].includes(status)};
  });
  return {ok:true,contacts:contacts,source_sheet:CONTACT_TAB,spreadsheet_id:SPREADSHEET_ID};
}

function doGet() { return jsonResponse({ok:true,service:'HIWIN Sheet Bridge',version:3,writes:'signed POST only'}); }
function jsonResponse(value) { return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON); }
