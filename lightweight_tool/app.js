'use strict';
const $=id=>document.getElementById(id), token=document.querySelector('meta[name="local-token"]').content;
let state=null,batch=null,currentTemplate='',attachments=[],revision=0,dirty=false,sending=false,uploading=false,saving=false,accountBusy=false;
const selected=new Set(),recordSelected=new Set(),drafts=new Map(),MAX_BYTES=10*1024*1024;
let recordPage=1,contactFormSender='',contactUploading=false,whatsappDrafts={},refreshingContacts=false,recordsBusy=false,recordsPolling=false;
function composeLocked(){return sending||uploading||saving||accountBusy||refreshingContacts;}
function lockControls(){for(const control of document.querySelectorAll('.compose-field'))control.disabled=composeLocked();if(!composeLocked()){renderContacts();renderAttachments();}updateSelection();}
function pendingAccount(){return !!($('accountEmail').value.trim()||$('accountLabel').value.trim());}
function lockMailControls(){for(const control of document.querySelectorAll('.mail-control'))control.disabled=sending||accountBusy||contactUploading;$('inbox').disabled=sending||accountBusy;const pending=pendingAccount();$('connect').disabled=sending||accountBusy||contactUploading||pending;$('password').disabled=sending||accountBusy||pending;$('accountPendingNotice').hidden=!pending;lockContactControls();}
function countryLabel(country){return country==='测试'?'🧪 测试邮箱':country;}
function normalizeCountry(country){return country==='印尼'?'印度尼西亚':country;}
function normalizeStateCountries(value){
 value.countries=[...new Set(value.countries.map(normalizeCountry))].sort();
 for(const rows of [value.contacts,value.messages,value.templates])for(const row of rows)row.country=normalizeCountry(row.country);
 for(const key of ['country_templates','partner_websites']){
  const mapping=value[key];
  if(Object.hasOwn(mapping,'印尼')&&!Object.hasOwn(mapping,'印度尼西亚'))mapping['印度尼西亚']=mapping['印尼'];
  delete mapping['印尼'];
 }
 return value;
}
function notice(message){$('notice').textContent=message;$('notice').hidden=false;}
function el(tag,text){const element=document.createElement(tag);if(text!==undefined)element.textContent=text;return element;}
async function api(path,data){if(data!==undefined&&Object.hasOwn(data,'country'))data={...data,country:normalizeCountry(data.country)};if(data!==undefined&&state&&['connect','preview','approve','send-check','send','schedule','inbox'].includes(path))data={...data,sender:state.sender};const response=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{'X-Local-Token':token,'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw Error(result.error||'请求失败');return result;}
async function perform(button,action){button.disabled=true;try{await action();}catch(error){notice(error.message);}finally{if(button.id==='deleteSelectedRecords')renderRecords();else if(button.id!=='send'&&button.id!=='approve')button.disabled=button.classList.contains('compose-field')?composeLocked():button.classList.contains('mail-control')?(accountBusy||sending):sending;}}
function invalidate(){revision++;batch=null;$('review').hidden=true;$('send').disabled=true;$('approval').checked=false;$('approval').disabled=false;$('approve').disabled=true;}
function markDirty(){dirty=true;$('templateDirty').textContent='· 未保存修改';$('templateDirty').classList.add('dirty');invalidate();}
function composeData(){return {name:$('templateName').value,language:$('templateLanguage').value,country:$('templateCountry').value,subject:$('subject').value,body:$('body').value,links:$('links').value,attachments:attachments.map(item=>item.id)};}
function stashDraft(){if(currentTemplate)drafts.set(currentTemplate+'|'+$('templateCountry').value,{...composeData(),attachments:attachments.map(item=>({...item})),dirty});}
function renderTemplateOptions(){const previous=currentTemplate;$('template').replaceChildren();for(const template of state.templates){const isTest=template.id==='test-en';const option=el('option',(isTest?'🧪 测试 · ':'')+(template.country?'['+template.country+'] ':'[通用] ')+template.name);option.value=template.id;option.classList.toggle('test-option',isTest);$('template').append(option);}if(previous&&state.templates.some(item=>item.id===previous))$('template').value=previous;}
function updateWebsiteHint(){const country=$('templateCountry').value||$('contactCountry').value;const url=state.partner_websites[country]||'https://hiwin-partners.com/en';$('websiteHint').textContent=country?'此国家网站：'+url+'。模板中的 {{partner_website}} 会自动替换。':'通用模板：每封按收件人国家选择网站，无对应版本用英文。';}
function loadTemplate(ident,scope){const stored=state.templates.find(item=>item.id===ident);if(!stored)return;const key=ident+'|'+(scope===undefined?(stored.country||''):scope);currentTemplate=ident;const template=drafts.get(key)||stored;$('template').value=ident;$('templateName').value=template.name;$('templateLanguage').value=template.language;$('templateCountry').value=scope===undefined?(template.country||''):scope;$('subject').value=template.subject;$('body').value=template.body;$('links').value=template.links||'';attachments=(template.attachments||[]).map(item=>({...item}));dirty=!!template.dirty;$('templateDirty').textContent=dirty?'· 未保存修改':'';$('templateDirty').classList.toggle('dirty',dirty);$('template').classList.toggle('test-template',ident==='test-en');$('testTemplateNotice').hidden=ident!=='test-en';renderAttachments();updateWebsiteHint();invalidate();}
function loadCountryTemplate(country){stashDraft();const saved=state.country_templates[country];const fallback=country==='台湾'?'outreach-zh-TW':country==='测试'?'test-en':'outreach-en';const candidate=[saved,fallback,'outreach-en',state.preferred_template].find(id=>state.templates.some(item=>item.id===id))||state.templates[0]?.id;loadTemplate(candidate,country==='测试'?'':country);}
function sizeLabel(size){return size>=1024*1024?(size/(1024*1024)).toFixed(2)+' MB':Math.max(1,Math.ceil(size/1024))+' KB';}
function renderAttachments(){$('attachmentList').replaceChildren();for(const file of attachments){const row=el('div');row.className='attachment';const info=el('span',file.name+' · '+sizeLabel(file.size));info.className='attachment-info';const remove=el('button','移除');remove.className='secondary compose-field';remove.disabled=composeLocked();remove.onclick=()=>{attachments=attachments.filter(item=>item.id!==file.id);markDirty();renderAttachments();};row.append(info,remove);$('attachmentList').append(row);}if(!attachments.length){const empty=el('p','尚未添加附件');empty.className='hint';$('attachmentList').append(empty);}}
function matchesContact(contact){return (!$('contactCountry').value||contact.country===$('contactCountry').value)&&(!$('onlyWithEmail').checked||!!contact.email)&&(contact.name+' '+contact.email).toLowerCase().includes($('search').value.toLowerCase());}
function visibleContacts(){return state.contacts.filter(matchesContact);}
function updateSelection(){const rows=state.contacts.filter(contact=>selected.has(contact.id)),emails=new Set(rows.map(contact=>contact.email.toLowerCase()));$('selectedCount').textContent=`已选择 ${rows.length} 家机构 · ${emails.size} 个邮箱`;const byCountry=new Map();for(const row of rows)byCountry.set(row.country,(byCountry.get(row.country)||0)+1);const hidden=rows.filter(row=>!matchesContact(row)).length;$('selectedSummary').textContent=rows.length?[...byCountry].map(([country,count])=>country+' '+count+' 家').join(' · ')+(hidden?`；其中 ${hidden} 家在当前筛选之外`:''):'先勾选需要联系的机构';const testRows=rows.filter(row=>row.country==='测试');$('testSelectionNotice').hidden=!testRows.length;$('testSelectionNotice').textContent=testRows.length===rows.length?'🧪 已选测试邮箱 · 本批只做测试发送。':'⚠️ 同时选中了测试邮箱和正式机构，请清空已选并分批发送。';document.querySelector('.selection-bar').classList.toggle('test-selection',testRows.length>0);$('preview').disabled=!rows.length||composeLocked();$('refreshContacts').disabled=composeLocked()||!state.sheet_configured;}
function renderCountries(){for(const id of ['contactCountry','recordCountry','templateCountry']){const current=$(id).value;$(id).replaceChildren();const all=el('option',id==='templateCountry'?'通用模板（不绑定国家）':'全部国家 / 地区');all.value='';$(id).append(all);for(const country of state.countries){const option=el('option',countryLabel(country));option.value=country;option.classList.toggle('test-option',country==='测试');$(id).append(option);}$(id).value=state.countries.includes(current)?current:'';$(id).classList.toggle('test-filter',$(id).value==='测试');}$('countrySuggestions').replaceChildren();for(const country of state.countries){const option=el('option');option.value=country;$('countrySuggestions').append(option);}}
function renderContacts(){const visible=visibleContacts();$('contacts').replaceChildren();for(const country of state.countries){const group=visible.filter(contact=>contact.country===country);if(!group.length)continue;const heading=el('div',countryLabel(country)+' · '+group.length+' 家');heading.className='country-heading'+(country==='测试'?' test-heading':'');$('contacts').append(heading);for(const contact of group){const row=el('div');row.className='contact'+(contact.country==='测试'?' test-contact':'');const checkbox=el('input');checkbox.type='checkbox';checkbox.id='contact-'+contact.id;checkbox.checked=selected.has(contact.id);checkbox.disabled=!contact.email||composeLocked();checkbox.className='compose-field';checkbox.onchange=()=>{checkbox.checked?selected.add(contact.id):selected.delete(contact.id);invalidate();updateSelection();};const label=el('label');label.htmlFor=checkbox.id;const detail=el('div',contact.email||contact.email_error||'缺邮箱');detail.className='hint';if(contact.blocked)detail.append(el('span',' · 已停止联系，发送前需再次确认'));const agency=el('strong',contact.name);if(contact.country==='测试'){label.className='test-label';const badge=el('span','TEST · 测试');badge.className='test-badge';agency.append(badge);}label.append(agency,detail);row.append(checkbox,label);$('contacts').append(row);}}if(!visible.length){const empty=el('div','当前筛选没有机构。可以在下方新增机构与国家。');empty.className='empty';$('contacts').append(empty);}const available=visible.filter(contact=>contact.email).length;$('selectVisible').disabled=!available||composeLocked();$('counts').textContent=`当前列表 ${visible.length} 家 · ${available} 家有可选邮箱`;updateSelection();}
function renderAccounts(){
 $('mailAccount').replaceChildren();for(const account of state.mail_accounts){const option=el('option',account.label+' · '+account.email+(account.connected_in_session?' · 已连接':' · 未连接'));option.value=account.email;$('mailAccount').append(option);}$('mailAccount').value=state.sender;$('activeSender').textContent='当前发件人：'+state.sender;$('connect').textContent=(state.mail_connected?'重新验证 ':'连接 ')+state.sender;$('passwordLabel').textContent=state.sender+' 的邮箱密码';$('password').placeholder='请输入这个员工自己的密码';$('accountConnectionHint').textContent=state.mail_connected?'本次运行已连接；切换回来可直接使用。重启工具后需重新连接。':'当前员工尚未连接，请输入这个员工自己的密码。';lockMailControls();
 const current=$('recordSender').value;const accounts=new Map(state.mail_accounts.map(account=>[account.email,account.label]));for(const row of state.messages)if(!accounts.has(row.sender))accounts.set(row.sender,row.sender);$('recordSender').replaceChildren();const all=el('option','全部发件员工');all.value='';$('recordSender').append(all);for(const [address,name] of accounts){const option=el('option',name+' · '+address);option.value=address;$('recordSender').append(option);}$('recordSender').value=accounts.has(current)?current:'';
}
function mailBody(message){
 const body=el('div');body.className='mail-body';
 for(const part of message.body_sections||[{kind:'text',text:message.body}]){
  if(part.kind==='divider'){body.append(el('hr'));continue;}
  const bold=['signature-name','contact'].includes(part.kind),section=el(bold?'strong':'div');
  section.className=part.kind==='greeting'?'mail-greeting':part.kind==='signature-name'?'mail-signature-name':part.kind==='contact'?'mail-contact':'mail-text';
  for(const run of part.runs||[{text:part.text}]){
   if(run.url){const link=el('a',run.text);link.href=run.url;link.target='_blank';link.rel='noopener';section.append(link);}
   else section.append(run.bold?el('strong',run.text):document.createTextNode(run.text));
  }
  body.append(section);
 }
 return body;
}
function messageType(message){return message.message_type||(message.country==='测试'?'test':'formal');}
function filteredRecords(){
 const country=$('recordCountry').value,sender=$('recordSender').value,type=$('recordType').value;
 return state.messages.filter(message=>(!country||message.country===country)&&(!sender||message.sender===sender)&&(!type||messageType(message)===type));
}
function clockParts(value,zone){
 const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value));
 return Object.fromEntries(parts.map(part=>[part.type,part.value]));
}
function recordTime(value,message){
 if(!value)return '';
 const japan=clockParts(value,'Asia/Tokyo'),day=japan.year+'/'+japan.month+'/'+japan.day,time=japan.hour+':'+japan.minute;
 if(!message.recipient_timezone)return '日本 '+day+' '+time+'（当地时间未设定）';
 const local=clockParts(value,message.recipient_timezone),localDay=local.year+'/'+local.month+'/'+local.day;
 const label=message.recipient_timezone==='Asia/Jakarta'?'雅加达当地':'当地';
 return '日本 '+day+' '+time+'（'+label+' '+(localDay===day?'':localDay+' ')+local.hour+':'+local.minute+'）';
}
function confirmSend(review,scheduled){
 const dialog=$('sendConfirm'),items=review.recipients.length?review.recipients:batch.messages;
 $('sendConfirmTitle').textContent=(review.required?'正式邮件':'测试邮件')+(scheduled?' · 定时发送确认':' · 发送前再次确认');
 $('sendConfirmSummary').textContent='发件人：'+review.sender+'\n本批共 '+review.count+' 封，请核对收件人。';
 const zones=new Set(batch.messages.map(message=>message.recipient_timezone));
 $('sendConfirmTime').hidden=!scheduled;
 if(scheduled)$('sendConfirmTime').textContent=zones.size===1?'计划：'+recordTime(review.scheduled_at,batch.messages[0]):'各收件人的计划时间见下方（日本时间／当地时间）。';
 $('sendConfirmRecipients').replaceChildren();
 for(const item of items){
  const row=el('li');row.append(el('strong',item.name),el('small',item.recipient));
  if(scheduled&&zones.size>1)row.append(el('small',recordTime(review.scheduled_at,batch.messages.find(message=>message.recipient===item.recipient))));
  const warnings=[];
  if(item.previous_count)warnings.push('已有 '+item.previous_count+' 条记录，最近状态：'+item.last_status);
  if(item.stopped)warnings.push('已标记停止联系');
  if(warnings.length){const warning=el('small','提醒：'+warnings.join('；'));warning.className='send-warning';row.append(warning);}
  $('sendConfirmRecipients').append(row);
 }
 $('sendConfirmAccept').textContent=scheduled?'确认定时发送':'确认现在发送';
 dialog.returnValue='cancel';
 return new Promise(resolve=>{dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true});dialog.showModal();});
}
function renderRecords(){
 if(!state)return;
 const rows=filteredRecords(),available=new Set(rows.filter(row=>row.can_delete).map(row=>row.id));
 for(const id of recordSelected)if(!available.has(id))recordSelected.delete(id);
 $('recordType').classList.toggle('test-filter',$('recordType').value==='test');
 $('recordCountry').classList.toggle('test-filter',$('recordCountry').value==='测试');$('logs').replaceChildren();
 const pageSize=Number($('recordPageSize').value),pages=Math.max(1,Math.ceil(rows.length/pageSize));recordPage=Math.min(recordPage,pages);
 const start=(recordPage-1)*pageSize,pageRows=rows.slice(start,start+pageSize),pageIds=pageRows.filter(row=>row.can_delete).map(row=>row.id);
 $('recordCount').textContent=`当前显示 ${rows.length?start+1:0}–${start+pageRows.length} 条 · 筛选后 ${rows.length} 条 · 全部 ${state.messages.length} 条`;
 $('recordPageInfo').textContent=`第 ${recordPage} / ${pages} 页`;$('recordPrev').disabled=recordPage<=1;$('recordNext').disabled=recordPage>=pages;
 $('recordSelectedCount').textContent='已选 '+recordSelected.size+' 条';
 $('deleteSelectedRecords').disabled=!recordSelected.size||sending||recordsBusy;
 $('clearRecordSelection').disabled=!recordSelected.size||sending||recordsBusy;
 $('selectFilteredRecords').disabled=!available.size||sending||recordsBusy;
 const pageCheck=$('recordSelectPage');pageCheck.disabled=!pageIds.length||sending||recordsBusy;
 pageCheck.checked=!!pageIds.length&&pageIds.every(id=>recordSelected.has(id));pageCheck.indeterminate=pageIds.some(id=>recordSelected.has(id))&&!pageCheck.checked;
 for(const message of pageRows){
  const row=el('tr'),checkCell=el('td'),check=el('input');checkCell.className='record-check';check.type='checkbox';check.setAttribute('aria-label','选择记录：'+message.name+' · '+message.subject);
  check.checked=recordSelected.has(message.id);check.disabled=!message.can_delete||sending||recordsBusy;
  check.onchange=()=>{check.checked?recordSelected.add(message.id):recordSelected.delete(message.id);renderRecords();};checkCell.append(check);
  const isTest=messageType(message)==='test',region=el('td'),badge=el('span',isTest?'测试发送':'正式发送');badge.className='record-kind'+(isTest?' record-kind-test':'');region.append(badge,el('small',countryLabel(message.country)));if(isTest)row.className='test-record';
  const account=state.mail_accounts.find(item=>item.email===message.sender),employee=el('td',account?account.label:message.sender);const address=el('small',message.sender);address.className='sender-address';employee.append(address);
  const recipient=el('td',message.name);recipient.append(el('small',message.recipient),el('small',message.subject));if(message.attachments.length)recipient.append(el('small','附件：'+message.attachments.map(file=>file.name).join('、')));
  const sent=el('td',message.status==='服务器已接受'?'已发送（服务器已接受）':message.status);sent.className='record-status';
  if(message.scheduled_at)sent.append(el('small','计划：'+recordTime(message.scheduled_at,message)));
  if(message.sent_at)sent.append(el('small','发送：'+recordTime(message.sent_at,message)));
  if(message.error)sent.append(el('small',message.error));
  const reply=el('td',message.reply_status||'尚未检测到关联回复');if(message.reply_at)reply.append(el('small','回复记录：'+recordTime(message.reply_at,message)));reply.append(el('small',message.next_step||''));const synced=el('td',message.sheet_synced?'已同步':'待同步'),actions=el('td');
  if(message.can_cancel_schedule){
   const cancel=el('button','取消定时');cancel.className='secondary';cancel.disabled=sending||recordsBusy;
   cancel.onclick=()=>perform(cancel,async()=>{const count=state.messages.filter(item=>item.batch_id===message.batch_id).length;if(!window.confirm('取消本批 '+count+' 封邮件的定时发送？\n已发出的邮件不受影响。'))return;await api('schedule/cancel',{id:message.batch_id});notice('定时任务已取消。');await refreshRecords();});actions.append(cancel);
  }else{
   const stop=el('button','停止联系');stop.className='secondary';stop.disabled=sending||recordsBusy||message.schedule_status==='running';
   stop.onclick=()=>perform(stop,async()=>{await api('stop',{email:message.recipient});notice('已标记停止联系；手动发送正式邮件时仍可再次确认。');await refresh();});
   const remove=el('button','删除记录');remove.className='secondary';remove.disabled=!message.can_delete||sending||recordsBusy;
   remove.onclick=()=>perform(remove,async()=>{if(!window.confirm('删除这条本地记录？\n'+message.name+' · '+message.recipient+'\n'+message.subject+'\n\n已发邮件和 Google Sheet 日志保留。'))return;await deleteRecordIds([message.id]);});actions.append(stop,remove);
  }
  row.append(checkCell,region,employee,recipient,sent,reply,synced,actions);$('logs').append(row);
 }
 if(!rows.length){const row=el('tr'),empty=el('td','当前筛选没有发送记录。');empty.colSpan=8;row.append(empty);$('logs').append(row);}
}
async function refreshRecords(){const result=await api('records');state.messages=result.messages.map(message=>({...message,country:normalizeCountry(message.country)}));renderRecords();}
async function deleteRecordIds(ids){
 recordsBusy=true;renderRecords();
 try{const result=await api('records/delete',{ids});for(const id of ids)recordSelected.delete(id);notice('已从本地列表删除 '+result.count+' 条记录。');await refreshRecords();}
 finally{recordsBusy=false;renderRecords();}
}
function updateSendMode(){
 const scheduled=$('sendMode').value==='scheduled',isTest=!!batch&&batch.messages.every(message=>messageType(message)==='test');
 $('scheduleFields').hidden=$('scheduleHint').hidden=!scheduled;
 $('send').textContent=scheduled?'安排定时发送（已批准）':isTest?'发送测试邮件（已批准）':'发送正式邮件（已批准）';
 if(scheduled){const parts=clockParts(new Date(Date.now()+60000).toISOString(),'Asia/Tokyo');$('scheduleAt').min=parts.year+'-'+parts.month+'-'+parts.day+'T'+parts.hour+':'+parts.minute;}
}
async function refresh(){const previousSender=state&&state.sender;state=normalizeStateCountries(await api('state'));if(previousSender&&previousSender!==state.sender){invalidate();$('password').value='';notice('发件员工已切换为 '+state.sender+'，请重新预览确认。');}renderAccounts();renderContactConfig();$('sheetLink').hidden=!state.spreadsheet_url;if(state.spreadsheet_url)$('sheetLink').href=state.spreadsheet_url;$('mailBadge').textContent=state.mail_connected?'公司邮箱已连接':'公司邮箱未连接';$('sheetBadge').textContent=state.sheet_saved?'Sheet 已保存 · 自动恢复':state.sheet_configured?'Sheet 已配置 · 尚未保存':'Sheet 未连接';if(document.activeElement!==$('bridgeUrl'))$('bridgeUrl').value=state.bridge_url||'';$('bridgeSecret').placeholder=state.sheet_configured?'已有密钥，可留空；更换地址时重新填写':'首次填写同步密钥';$('restoreSheet').hidden=!state.sheet_store_error;$('sheetHelp').textContent=state.sheet_store_error||(state.sheet_saved?'连接配置已保存在本机 Mac 钥匙串，重新启动会自动恢复。':'部署地址与同步密钥保存到本机 Mac 钥匙串；下次启动自动恢复。');renderCountries();renderTemplateOptions();let removed=false;for(const ident of [...selected]){const contact=state.contacts.find(row=>row.id===ident);if(!contact||!contact.email){selected.delete(ident);removed=true;}}if(removed)invalidate();$('contactsSourceHint').textContent='联系人来源：Google Sheet「邮件跟踪」。修改机构名称、国家、收件邮箱后点击刷新；请保留同一机构的编号。'+(state.contacts_refreshed_at?' 上次刷新：'+new Date(state.contacts_refreshed_at).toLocaleString('zh-CN',{timeZone:'Asia/Tokyo'})+'（东京）':' 尚未从云端刷新。');renderContacts();renderRecords();if(!state.templates.some(item=>item.id===currentTemplate))loadTemplate(state.preferred_template);updateWebsiteHint();}
function freezeCompose(value){sending=value;lockControls();lockMailControls();lockContactControls();$('inbox').disabled=value;$('sync').disabled=value;renderRecords();}
for(const id of ['subject','body','links','templateName','templateLanguage'])$(id).addEventListener('input',markDirty);
$('template').onchange=()=>{stashDraft();loadTemplate($('template').value);};$('contactCountry').onchange=()=>{$('contactCountry').classList.toggle('test-filter',$('contactCountry').value==='测试');loadCountryTemplate($('contactCountry').value);renderContacts();};$('search').oninput=renderContacts;$('recordCountry').onchange=$('recordSender').onchange=$('recordType').onchange=()=>{recordPage=1;recordSelected.clear();renderRecords();};$('recordPageSize').onchange=()=>{recordPage=1;renderRecords();};$('recordPrev').onclick=()=>{recordPage--;renderRecords();};$('recordNext').onclick=()=>{recordPage++;renderRecords();};$('templateCountry').onchange=()=>{markDirty();updateWebsiteHint();};
$('onlyWithEmail').onchange=renderContacts;
$('selectVisible').onclick=()=>{const rows=visibleContacts().filter(row=>row.email);const combined=new Set([...selected,...rows.map(row=>row.id)]);if(combined.size>30)return notice('当前选择合计超过 30 家，请缩小筛选范围或逐家勾选。');for(const row of rows)selected.add(row.id);invalidate();renderContacts();};$('clearSelection').onclick=()=>{selected.clear();invalidate();renderContacts();};
$('refreshContacts').onclick=()=>perform($('refreshContacts'),async()=>{refreshingContacts=true;invalidate();lockControls();const button=$('refreshContacts');button.textContent='正在读取 Google Sheet…';notice('正在读取 Google Sheet 的联系人名单…');try{const result=await api('contacts/refresh',{});await refresh();notice(`联系人已刷新：云端 ${result.count} 家 · 新增 ${result.added} · 更新 ${result.updated} · 移除 ${result.removed}`+(result.invalid_emails?`；${result.invalid_emails} 行邮箱格式无效，请在表格修正。`:'。')+' 本地新增联系人、发送记录与停止联系标记已保留；请重新预览。');}finally{refreshingContacts=false;button.textContent='从 Google Sheet 刷新联系人';lockControls();}});
$('addContact').onclick=()=>perform($('addContact'),async()=>{const added=await api('contact',{name:$('newAgency').value,email:$('newEmail').value,country:$('newCountry').value});await refresh();$('contactCountry').value=added.country;loadCountryTemplate(added.country);$('search').value='';if(added.email)selected.add(added.id);invalidate();renderContacts();$('newAgency').value='';$('newEmail').value='';notice('已新增 '+added.name+'（'+added.country+'）');});
async function persistTemplate(asNew){saving=true;lockControls();try{const result=await api('template',{...composeData(),id:asNew?null:currentTemplate});drafts.delete(currentTemplate+'|'+$('templateCountry').value);drafts.delete(result.id+'|'+$('templateCountry').value);currentTemplate=result.id;await refresh();loadTemplate(result.id);notice('模板已保存，主题、正文、附件与链接将在下次启动时保留。');}finally{saving=false;lockControls();}}
async function deleteCurrentTemplate(){
 const template=state.templates.find(item=>item.id===currentTemplate);if(!template)return;
 if(!window.confirm('删除模板「'+template.name+'」'+(template.country?'（'+template.country+'）':'')+'？\n'+(dirty?'未保存的修改也会丢弃。\n':'')+'发送记录与附件文件保留；该国家将使用通用模板。'))return;
 saving=true;lockControls();try{const ident=currentTemplate;await api('template/delete',{id:ident});for(const key of [...drafts.keys()])if(key.startsWith(ident+'|'))drafts.delete(key);currentTemplate='';invalidate();await refresh();loadCountryTemplate($('contactCountry').value);notice('模板已删除；发送记录与附件文件保留。');}finally{saving=false;lockControls();}
}
$('deleteTemplate').onclick=()=>perform($('deleteTemplate'),deleteCurrentTemplate);
$('saveTemplate').onclick=()=>perform($('saveTemplate'),()=>persistTemplate(false));$('newTemplate').onclick=()=>perform($('newTemplate'),()=>persistTemplate(true));
function fileBase64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('无法读取附件：'+file.name));reader.readAsDataURL(file);});}
function setQrPreview(id,file){const img=$(id);img.dataset.file=file||'';img.hidden=!file;if(file)img.src='/assets/'+file;else img.removeAttribute('src');}
function whatsappFields(){return {phone:$('whatsappPhone').value,url:$('whatsappUrl').value,qr:$('whatsappQrPreview').dataset.file||''};}
function setWhatsappFields(value){$('whatsappPhone').value=value.phone||'';$('whatsappUrl').value=value.url||'';setQrPreview('whatsappQrPreview',value.qr);$('whatsappScopeHint').textContent=$('sharedWhatsapp').checked?'修改此处会同步用于所有选择共用 WhatsApp 的 sender。':'只修改当前 sender 的 WhatsApp。';}
function lockContactControls(){for(const control of document.querySelectorAll('.contact-control'))control.disabled=sending||accountBusy||contactUploading;}
function renderContactConfig(force=false){lockContactControls();if(!force&&contactFormSender===state.sender)return;contactFormSender=state.sender;const profile=state.contact_config;$('contactSettingsSender').textContent='当前 sender：'+state.sender;$('signatureName').value=profile.signature_name||'';$('contactEmail').value=profile.contact_email||'';$('lineUrl').value=profile.line_url||'';setQrPreview('lineQrPreview',profile.line_qr);$('sharedWhatsapp').checked=profile.use_shared_whatsapp;whatsappDrafts={shared:{...profile.shared_whatsapp},own:{...profile.whatsapp}};setWhatsappFields(profile.effective_whatsapp);}
$('sharedWhatsapp').onchange=()=>{whatsappDrafts[$('sharedWhatsapp').checked?'own':'shared']=whatsappFields();setWhatsappFields(whatsappDrafts[$('sharedWhatsapp').checked?'shared':'own']);invalidate();};
for(const [input,preview] of [['lineQrUpload','lineQrPreview'],['whatsappQrUpload','whatsappQrPreview']])$(input).onchange=async()=>{const file=$(input).files[0];if(!file)return;if(file.size>2*1024*1024){$(input).value='';return notice('二维码图片不能超过 2 MB。');}contactUploading=true;lockContactControls();lockMailControls();try{const uploaded=await api('contact-qr',{content:await fileBase64(file)});setQrPreview(preview,uploaded.file);invalidate();notice('二维码已上传，请点击「保存联系方式与二维码」。');}catch(error){notice(error.message);}finally{contactUploading=false;$(input).value='';lockContactControls();lockMailControls();}};
$('removeLineQr').onclick=()=>{setQrPreview('lineQrPreview','');invalidate();notice('LINE 二维码已移除，请保存联系方式。');};
$('removeWhatsappQr').onclick=()=>{setQrPreview('whatsappQrPreview','');invalidate();notice('WhatsApp 二维码已移除，请保存联系方式。');};
for(const id of ['signatureName','contactEmail','lineUrl','whatsappPhone','whatsappUrl'])$(id).oninput=invalidate;
$('saveContactSettings').onclick=()=>perform($('saveContactSettings'),async()=>{await api('contact-config',{sender:state.sender,signature_name:$('signatureName').value,contact_email:$('contactEmail').value,line_url:$('lineUrl').value,line_qr:$('lineQrPreview').dataset.file||'',use_shared_whatsapp:$('sharedWhatsapp').checked,whatsapp:whatsappFields()});invalidate();await refresh();renderContactConfig(true);notice('联系方式与二维码已保存。请重新生成预览并批准。');});
$('files').onchange=async()=>{const files=[...$('files').files];if(!files.length)return;if(attachments.reduce((sum,file)=>sum+file.size,0)+files.reduce((sum,file)=>sum+file.size,0)>MAX_BYTES){$('files').value='';return notice('每封邮件附件合计不能超过 10 MB。');}uploading=true;lockControls();try{for(const file of files){notice('正在保存附件：'+file.name);const saved=await api('upload',{name:file.name,content:await fileBase64(file)});attachments.push(saved);markDirty();renderAttachments();}notice('附件已添加。可保存到当前模板，并在发送前预览检查。');}catch(error){notice(error.message);}finally{uploading=false;$('files').value='';lockControls();}};
async function accountAction(action){accountBusy=true;invalidate();lockControls();lockMailControls();try{await action();}catch(error){notice(error.message);}finally{try{await refresh();}catch(error){notice(error.message);}accountBusy=false;lockControls();lockMailControls();}}
const accountChannel=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('hiwin-mail-account'):null;
let syncingAccount=false;
async function syncAccountView(){if(!state||sending||accountBusy||syncingAccount)return;syncingAccount=true;try{await refresh();}catch(error){notice(error.message);}finally{syncingAccount=false;}}
if(accountChannel)accountChannel.onmessage=syncAccountView;
window.addEventListener('focus',syncAccountView);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)syncAccountView();});
$('mailAccount').onchange=()=>{const email=$('mailAccount').value;$('password').value='';return accountAction(async()=>{const result=await api('account/select',{email});if(accountChannel)accountChannel.postMessage({sender:result.sender});notice(result.message);});};
$('addAccount').onclick=()=>accountAction(async()=>{const added=await api('account',{email:$('accountEmail').value,label:$('accountLabel').value});const result=await api('account/select',{email:added.email});if(accountChannel)accountChannel.postMessage({sender:result.sender});$('password').value='';$('accountEmail').value='';$('accountLabel').value='';$('addAccountDetails').open=false;notice(result.message);});
$('accountEmail').oninput=$('accountLabel').oninput=()=>{$('password').value='';lockMailControls();};
$('connect').onclick=()=>{if(pendingAccount()){notice('请先点击「保存并切换员工」，然后输入这个员工自己的密码。');return;}const password=$('password').value;if(!password){notice(state.mail_connected?'当前员工本次运行已连接，可以直接预览发送。重新验证时请先输入密码。':'请输入 '+state.sender+' 自己的邮箱密码。');return;}$('password').value='';return accountAction(async()=>{const result=await api('connect',{password});if(accountChannel)accountChannel.postMessage({sender:state.sender});notice(result.message);});};
$('generateSecret').onclick=()=>perform($('generateSecret'),async()=>{const bytes=crypto.getRandomValues(new Uint8Array(32)),key=Array.from(bytes,value=>value.toString(16).padStart(2,'0')).join('');$('bridgeSecret').value=key;try{await navigator.clipboard.writeText(key);notice('同步密钥已生成并复制。请粘贴到 Google 的 BRIDGE_SECRET 属性并保存，再回此页保存连接。');}catch(error){$('bridgeSecret').type='text';$('bridgeSecret').select();notice('密钥已生成，请手动复制选中的内容，粘贴到 Google 的 BRIDGE_SECRET 属性。');}});
$('connectSheet').onclick=()=>perform($('connectSheet'),async()=>{const result=await api('sheet',{url:$('bridgeUrl').value,secret:$('bridgeSecret').value});$('bridgeSecret').value='';$('bridgeSecret').type='password';notice(result.ok?'Sheet 连接已保存到 Mac 钥匙串并通过验证；下次启动会自动恢复。':'连接已保存到 Mac 钥匙串；'+result.error);await refresh();});
$('restoreSheet').onclick=()=>perform($('restoreSheet'),async()=>{const result=await api('sheet/restore',{});notice(result.ok?'已恢复保存的 Sheet 连接':result.error);await refresh();});
$('preview').onclick=()=>perform($('preview'),async()=>{const version=revision;const result=await api('preview',{...composeData(),ids:[...selected],template_id:currentTemplate});if(version!==revision)return notice('准备预览时内容发生变化，请重新生成。');batch=result;$('previewList').replaceChildren();const isTest=batch.messages.every(message=>messageType(message)==='test');$('testReviewNotice').hidden=!isTest;updateSendMode();$('previewDetails').open=true;$('previewToggle').textContent='邮件预览 · '+batch.messages.length+' 封（点击展开 / 收起）';$('send').classList.toggle('test-send',isTest);const countries=[...new Set(batch.messages.map(message=>countryLabel(message.country)))];$('reviewSummary').textContent=`${batch.messages.length} 封邮件 · ${countries.join('、')} · 语言：${batch.language||'未指定'}`;$('reviewAttachments').replaceChildren();const files=el('p',batch.attachments.length?'本批附件：'+batch.attachments.map(file=>file.name+'（'+sizeLabel(file.size)+'）').join('、'):'本批无附件');files.className='hint';$('reviewAttachments').append(files);for(const [index,message] of batch.messages.entries()){const view=el('details');view.open=index===0;view.className='preview'+(messageType(message)==='test'?' test-preview':'');view.append(el('summary',message.name+' → '+message.recipient),el('p','国家 / 地区：'+countryLabel(message.country)+' · 发件人：'+batch.sender),el('p','主题：'+message.subject),mailBody(message));for(const link of message.links){const anchor=el('a',link.label||link.url);anchor.href=link.url;anchor.target='_blank';anchor.rel='noopener';view.append(anchor,el('br'));}const qrInfo=batch.line_signature||batch.whatsapp_signature;if(qrInfo){const signature=el('div');signature.className='line-signature';const anchor=el('a');anchor.href=qrInfo.url;anchor.target='_blank';anchor.rel='noopener';const qr=el('img');qr.src=qrInfo.preview_url;qr.width=180;qr.height=180;qr.alt=qrInfo.channel==='WhatsApp'?'WhatsApp QR Code':'LINE 加好友 QR Code';anchor.append(qr);signature.append(anchor,el('p',qrInfo.channel==='WhatsApp'?'Scan the QR code or click to contact us on WhatsApp.':'掃描 QR Code 或點擊 LINE 連結加入好友。'));view.append(signature);}$('previewList').append(view);}$('review').hidden=false;$('approval').checked=false;$('approval').disabled=false;$('approve').disabled=true;$('send').disabled=true;$('reviewState').textContent='未批准。请核对以上实际内容、链接、附件与二维码。';$('review').scrollIntoView({behavior:'smooth',block:'start'});});
$('approval').onchange=()=>{$('approve').disabled=!$('approval').checked||!batch;};
$('approve').onclick=()=>perform($('approve'),async()=>{if(!batch)return;const version=revision,ident=batch.id;await api('approve',{id:ident,digest:batch.digest});if(version!==revision||!batch||batch.id!==ident)return;$('send').disabled=false;$('previewDetails').open=false;$('reviewState').textContent='本批 '+batch.messages.length+' 封已批准。可展开预览核对，或选择发送方式。';$('approval').disabled=true;$('review').scrollIntoView({behavior:'smooth',block:'start'});});
$('sendMode').onchange=updateSendMode;
$('recordSelectPage').onchange=()=>{const size=Number($('recordPageSize').value),rows=filteredRecords().slice((recordPage-1)*size,recordPage*size);for(const row of rows.filter(item=>item.can_delete))$('recordSelectPage').checked?recordSelected.add(row.id):recordSelected.delete(row.id);renderRecords();};
$('selectFilteredRecords').onclick=()=>{for(const row of filteredRecords().filter(item=>item.can_delete))recordSelected.add(row.id);renderRecords();};
$('clearRecordSelection').onclick=()=>{recordSelected.clear();renderRecords();};
$('deleteSelectedRecords').onclick=()=>perform($('deleteSelectedRecords'),async()=>{const ids=[...recordSelected];if(!ids.length)return;if(!window.confirm('删除已勾选的 '+ids.length+' 条本地记录？\n\n已发邮件和 Google Sheet 日志保留。'))return;await deleteRecordIds(ids);});
$('send').onclick=()=>perform($('send'),async()=>{
 if(!batch)return;
 const ident=batch.id,scheduled=$('sendMode').value==='scheduled',scheduleAt=$('scheduleAt').value;
 if(scheduled&&!scheduleAt){$('send').disabled=false;return notice('请选择日本时间的定时发送时间。');}
 freezeCompose(true);$('sendMode').disabled=$('scheduleAt').disabled=true;
 try{
  const request={id:ident,...(scheduled?{scheduled_at:scheduleAt}:{})},review=await api('send-check',request);
  if(!batch||batch.id!==ident)return;
  if(review.required||scheduled){
   if(!await confirmSend(review,scheduled)){
    $('reviewState').textContent='已取消操作；本批仍已批准，可再次确认。';return;
   }
  }
  $('reviewState').textContent=scheduled?'正在保存定时任务…':'发送中，请保持工具运行。';
  await api(scheduled?'schedule':'send',{...request,confirmation:review.digest});batch=null;
  $('reviewState').textContent=scheduled?'定时任务已保存，可在记录中查看或取消。':'执行结束，查看下方发送记录。';$('approval').disabled=false;await refresh();
 }finally{freezeCompose(false);$('sendMode').disabled=$('scheduleAt').disabled=false;if(batch&&batch.id===ident)$('send').disabled=false;}
});
setInterval(async()=>{
 if(!state||sending||accountBusy||recordsBusy||recordsPolling||document.hidden||!state.messages.some(row=>['scheduled','awaiting_mail','running'].includes(row.schedule_status)))return;
 recordsPolling=true;try{await refreshRecords();}catch(error){notice('定时状态刷新失败，可点击刷新记录。');}finally{recordsPolling=false;}
},15000);
$('inbox').onclick=()=>accountAction(async()=>{const result=await api('inbox',{});notice(`已检查 ${state.sender} 的 ${result.scanned} 封来信，更新 ${result.matched} 条关联记录。`);});$('sync').onclick=()=>perform($('sync'),async()=>{const result=await api('sync',{});notice(result.ok?`已同步 ${result.count} 条记录`:result.error);await refresh();});$('refresh').onclick=()=>perform($('refresh'),refresh);
window.addEventListener('beforeunload',event=>{if(dirty||composeLocked()){event.preventDefault();event.returnValue='';}});
refresh().catch(error=>notice(error.message));
