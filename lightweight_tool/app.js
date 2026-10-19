'use strict';
const $=id=>document.getElementById(id), token=document.querySelector('meta[name="local-token"]').content;
let state=null,batch=null,currentTemplate='',attachments=[],revision=0,dirty=false,sending=false,uploading=false,saving=false,accountBusy=false;
const selected=new Set(),recordSelected=new Set(),drafts=new Map(),MAX_BYTES=10*1024*1024;
// Membership is supplied as fixed recipient record IDs, independent of names,
// email changes, Sheet order and refreshes.
let contactGroupMembers=new Map();
let recordPage=1,contactFormSender='',contactUploading=false,whatsappDrafts={},refreshingContacts=false,recordsBusy=false,recordsPolling=false,deletingContacts=false;
function composeLocked(){return sending||uploading||saving||accountBusy||refreshingContacts||deletingContacts;}
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
function markDirty(){dirty=true;updateActivityHint();$('templateDirty').textContent='· 未保存修改';$('templateDirty').classList.add('dirty');invalidate();}
function composeData(){return {activity_id:currentActivityId(),name:$('templateName').value,language:$('templateLanguage').value,country:$('templateCountry').value,subject:$('subject').value,body:$('body').value,links:$('links').value,attachments:attachments.map(item=>item.id)};}
function stashDraft(){if(currentTemplate)drafts.set(currentTemplate+'|'+$('templateCountry').value,{...composeData(),attachments:attachments.map(item=>({...item})),dirty});}
function templateActivityId(template){return template.activity_id||'first-contact';}
function templateMatchesActivity(template){return template.id==='test-en'||templateActivityId(template)===currentActivityId();}
function currentPurposeTemplates(){return (state?.templates||[]).filter(template=>template.id!=='test-en'&&templateMatchesActivity(template));}
function renderTemplateOptions(){
 if(!state)return;
 const options=$('template'),business=currentPurposeTemplates(),test=$('contactCountry').value==='测试'?state.templates.find(template=>template.id==='test-en'):null;
 const visible=[...business,...(test?[test]:[])],matches=visible.some(template=>template.id===currentTemplate);
 options.replaceChildren();
 if(!matches){const placeholder=el('option',currentTemplate?'草稿已保留 · 请选择本轮模板':business.length?'请选择本轮模板':'本轮暂无模板 · 可另存新模板');placeholder.value='';placeholder.disabled=true;options.append(placeholder);}
 for(const template of business){const option=el('option',(template.country?'['+template.country+'] ':'[通用] ')+template.name);option.value=template.id;options.append(option);}
 if(test){const group=el('optgroup');group.label='测试邮箱专用';const option=el('option','🧪 '+test.name);option.value=test.id;option.classList.add('test-option');group.append(option);options.append(group);}
 options.value=matches?currentTemplate:'';
 const stored=state.templates.find(template=>template.id===currentTemplate);
 $('templateFilterHint').textContent='仅显示本轮模板 · '+business.length+' 个'+(test?' · 另有测试模板':'')+(!matches&&stored?'；已保留“'+(stored.id==='test-en'?'测试模板':activityName(templateActivityId(stored)))+'”草稿，请选择本轮模板。':'');
}
function updateWebsiteHint(){const country=$('templateCountry').value||$('contactCountry').value;const url=state.partner_websites[country]||'https://hiwin-partners.com/en';$('websiteHint').textContent=country?'此国家网站：'+url+'。模板中的 {{partner_website}} 会自动替换。':'通用模板：每封按收件人国家选择网站，无对应版本用英文。';}
function loadTemplate(ident,scope){const stored=state.templates.find(item=>item.id===ident);if(!stored)return;const key=ident+'|'+(scope===undefined?(stored.country||''):scope);currentTemplate=ident;const template=drafts.get(key)||stored;$('templateName').value=template.name;$('templateLanguage').value=template.language;$('templateCountry').value=scope===undefined?(template.country||''):scope;$('subject').value=template.subject;$('body').value=template.body;$('links').value=template.links||'';attachments=(template.attachments||[]).map(item=>({...item}));dirty=!!template.dirty;$('templateDirty').textContent=dirty?'· 未保存修改':'';$('templateDirty').classList.toggle('dirty',dirty);$('template').classList.toggle('test-template',ident==='test-en');$('testTemplateNotice').hidden=ident!=='test-en';renderTemplateOptions();renderAttachments();updateWebsiteHint();updateActivityHint();invalidate();}
const DEFAULT_ACTIVITIES=[{id:'first-contact',name:'首次合作介绍',purpose:'介绍公司与住宿合作'},{id:'itf-2026',name:'ITF 展位邀约',purpose:'邀请参展商到 J3302 交流'},{id:'responsible-followup',name:'负责人跟进',purpose:'向已核实的负责人继续联系'}];
function currentActivityId(){return $('activity').value||'first-contact';}
function activityName(id){return (state?.activities||DEFAULT_ACTIVITIES).find(item=>item.id===id)?.name||(id?'未知活动':'历史发送（活动未标记）');}
function renderActivities(){
 const previous=currentActivityId();$('activity').replaceChildren();
 for(const item of state.activities||DEFAULT_ACTIVITIES){const option=el('option',item.name);option.value=item.id;$('activity').append(option);}
 $('activity').value=(state.activities||DEFAULT_ACTIVITIES).some(item=>item.id===previous)?previous:'first-contact';
 const activity=$('recordActivity').value;$('recordActivity').replaceChildren();
 for(const [id,name] of [['','全部活动'],['legacy','历史发送（活动未标记）'],...(state.activities||DEFAULT_ACTIVITIES).map(item=>[item.id,item.name])]){const option=el('option',name);option.value=id;$('recordActivity').append(option);}
 $('recordActivity').value=[...$('recordActivity').children].some(item=>item.value===activity)?activity:'';
 updateActivityHint();
}
function recommendedTemplate(country=$('contactCountry').value){
 const activity=currentActivityId(),rows=state.templates||[];
 if(country==='测试')return rows.find(item=>item.id==='test-en');
 const config=(state.activities||[]).find(item=>item.id===activity),mapped=state.activity_country_templates?.[activity]?.[country]||config?.recommended_templates?.[country||'default'];
 if(mapped){const preferred=rows.find(item=>item.id===mapped&&item.activity_id===activity);if(preferred)return preferred;}
 const saved=rows.find(item=>item.id===state.country_templates?.[country]);
 if(saved&&(saved.activity_id===activity||activity==='first-contact'&&!saved.activity_id))return saved;
 const eligible=rows.filter(item=>item.id!=='test-en'&&item.activity_id===activity);
 if(mapped){const found=eligible.find(item=>item.id===mapped);if(found)return found;}
 const language=country==='台湾'?'zh':'en';
 const match=eligible.find(item=>item.country===country&&country)||eligible.find(item=>!item.country&&(language==='zh'?/中文|zh/i:/English|en/i).test(item.language));
 if(match)return match;
 // Compatibility for old untagged introductory templates only.
 if(activity==='first-contact')return rows.find(item=>item.id===(country==='台湾'?'outreach-zh-TW':'outreach-en'));
 return null;
}
function updateActivityHint(){
 if(!state)return;const template=recommendedTemplate(),stored=state.templates.find(item=>item.id===currentTemplate);
 const purpose=(state.activities||DEFAULT_ACTIVITIES).find(item=>item.id===currentActivityId())?.purpose||'';
 $('activityHint').textContent=purpose+(template?' · 推荐：'+template.name:' · 当前国家尚无对应模板，请选择或另存合适模板。')+(dirty?' · 正文有未保存修改，已保留。':'')+(stored&&!templateMatchesActivity(stored)?' · 当前模板属于“'+activityName(templateActivityId(stored))+'”，请切换。':'');
 $('useRecommendedTemplate').disabled=!template||composeLocked();
}
function loadCountryTemplate(country){
 renderTemplateOptions();updateWebsiteHint();updateActivityHint();
 if(dirty)return;
 const template=recommendedTemplate(country);if(!template)return;
 stashDraft();loadTemplate(template.id,country==='测试'?'':template.country||'');
}
function changeActivity(){
 if(!state||composeLocked())return;stashDraft();invalidate();
 const activity=(state.activities||DEFAULT_ACTIVITIES).find(item=>item.id===currentActivityId());
 const group=activeContactGroup();
 if(group&&!group.id.startsWith('itf-')&&activity?.id==='itf-2026')notice('已切换为 ITF 展位邀约，请选择相应 ITF 快捷名单。');
 loadCountryTemplate($('contactCountry').value);renderContacts();updateActivityHint();
}
function contactMessages(contact){return (state.messages||[]).filter(item=>String(item.recipient||'').trim().toLowerCase()===contact.email.toLowerCase());}
function contactActivityFacts(contact){
 const saved=contact.history?.by_activity?.[currentActivityId()],rows=contactMessages(contact).filter(item=>item.activity_id===currentActivityId());
 return {accepted:saved?.accepted_count??rows.filter(confirmedSent).length,pending:saved?.pending_count??rows.filter(item=>['scheduled','awaiting_mail','running'].includes(item.schedule_status)||['待发送','发送中'].includes(item.status)).length,uncertain:saved?.uncertain_count??rows.filter(item=>item.status==='结果待核对').length};
}
function confirmedSent(message){return message.confirmed_sent===true||message.status==='服务器已接受';}
function hasReply(message){return message.reply_status==='已回复';}
function currentRoundLabel(contact){const fact=contactActivityFacts(contact);return fact.uncertain?'结果待核对':fact.pending?'已安排 / 正在发送':fact.accepted?'已发送 '+fact.accepted+' 封':'本轮未发';}
function eligibleUnsent(contact){const fact=contactActivityFacts(contact);return !!contact.email&&!contact.blocked&&!fact.accepted&&!fact.pending&&!fact.uncertain;}
function contactHistoryLabel(contact){
 const rows=contactMessages(contact),sent=rows.filter(confirmedSent),last=sent[0],count=contact.history?.accepted_count??sent.length;
 const time=contact.history?.last_sent_at||last?.sent_at;
 return count?'历史已发送 '+count+' 封'+(time?' · 最近 '+recordTime(time,last||{}):''):'没有已确认发送记录';
}
function contactRole(contact){return contact.email_role||'邮箱角色未核实';}
function showContactHistory(contact){
 $('recordDetailTitle').textContent=contact.name+' · 联系历史';const content=$('recordDetailContent');content.replaceChildren();
 content.append(el('p',contact.email+' · '+contactRole(contact)));
 if(contact.company_name)content.append(el('p','公司：'+contact.company_name));
 if(contact.contact_name||contact.contact_title)content.append(el('p','联系人：'+[contact.contact_name,contact.contact_title].filter(Boolean).join(' · ')));
 if(contact.phone)content.append(el('p','电话：'+contact.phone));
 content.append(el('p','当前活动：'+activityName(currentActivityId())+' · '+currentRoundLabel(contact)),el('p',contactHistoryLabel(contact)));
 const company=contact.company_id;const contacts=company?state.contacts.filter(item=>item.company_id===company):[contact];
 if(contacts.length>1){content.append(el('h3','同公司邮箱'));for(const item of contacts)content.append(el('p',item.email+' · '+contactRole(item)+' · '+contactHistoryLabel(item)));}
 const rows=contactMessages(contact);for(const message of rows){const entry=el('div');entry.className='history-entry';const button=el('button','查看邮件详情');button.className='secondary';button.onclick=()=>showRecordDetail(message);entry.append(el('strong',activityName(message.activity_id)+' · '+message.status),el('p',message.subject),el('small',recordTime(message.sent_at||message.scheduled_at,message)),button);content.append(entry);}
 if(!rows.length)content.append(el('p','当前可见记录中没有此邮箱的邮件正文。已删除的本地记录仍计入历史发送统计。'));
 if(!$('recordDetail').open)$('recordDetail').showModal();
}
function sizeLabel(size){return size>=1024*1024?(size/(1024*1024)).toFixed(2)+' MB':Math.max(1,Math.ceil(size/1024))+' KB';}
function renderAttachments(){$('attachmentList').replaceChildren();for(const file of attachments){const row=el('div');row.className='attachment';const info=el('span',file.name+' · '+sizeLabel(file.size));info.className='attachment-info';const remove=el('button','移除');remove.className='secondary compose-field';remove.disabled=composeLocked();remove.onclick=()=>{attachments=attachments.filter(item=>item.id!==file.id);markDirty();renderAttachments();};row.append(info,remove);$('attachmentList').append(row);}if(!attachments.length){const empty=el('p','尚未添加附件');empty.className='hint';$('attachmentList').append(empty);}}
function activeContactGroup(){return (state.contact_groups||[]).find(group=>group.id===$('contactGroup').value);}
function renderContactGroups(){
 const previous=$('contactGroup').value;contactGroupMembers=new Map((state.contact_groups||[]).map(group=>[group.id,new Set(group.contact_ids)]));
 $('contactGroup').replaceChildren();const all=el('option','全部机构（按下方条件筛选）');all.value='';$('contactGroup').append(all);
 for(const group of state.contact_groups||[]){const option=el('option',group.name);option.value=group.id;$('contactGroup').append(option);}
 const valid=contactGroupMembers.has(previous);$('contactGroup').value=valid?previous:'';
 if(previous&&!valid){selected.clear();invalidate();}
 $('contactGroupError').hidden=!state.contact_groups_error;$('contactGroupError').textContent=state.contact_groups_error||'';
}
function matchesContact(contact){const group=activeContactGroup();return (!$('contactGroup').value||(group&&(!group.country||contact.country===group.country)&&contactGroupMembers.get(group.id)?.has(contact.id)))&&(!$('contactCountry').value||contact.country===$('contactCountry').value)&&(!$('onlyWithEmail').checked||!!contact.email)&&(contact.name+' '+contact.email).toLowerCase().includes($('search').value.toLowerCase());}
function changeContactGroup(){
 selected.clear();invalidate();$('search').value='';$('onlyWithEmail').checked=false;
 const group=activeContactGroup();if(group){$('contactCountry').value=group.country||'';$('contactCountry').classList.toggle('test-filter',false);if(group.id.startsWith('itf-'))$('activity').value='itf-2026';if(group.country||group.id.startsWith('itf-'))loadCountryTemplate(group.country||'');else updateWebsiteHint();updateActivityHint();}
 renderContacts();
}
function changeContactCountry(){
 const group=activeContactGroup();if(group?.country&&$('contactCountry').value&&$('contactCountry').value!==group.country){$('contactGroup').value='';selected.clear();invalidate();}
 $('contactCountry').classList.toggle('test-filter',$('contactCountry').value==='测试');loadCountryTemplate($('contactCountry').value);renderContacts();
}
function visibleContacts(){return state.contacts.filter(matchesContact);}
function renderSelectedContacts(rows){
 const list=$('selectedContacts'),scrollTop=list.scrollTop;list.replaceChildren();list.hidden=!rows.length;
 for(const contact of rows){
  const row=el('li'),info=el('div'),email=el('span',contact.email),detail=el('small',countryLabel(contact.country)+(matchesContact(contact)?'':' · 当前筛选外'));
  email.className='selected-email';info.append(el('strong',contact.name),email,detail);
  const remove=el('button','取消选择');remove.className='secondary compose-field';remove.disabled=composeLocked();
  remove.setAttribute('aria-label','取消选择：'+contact.name+' · '+contact.email);
  remove.onclick=()=>{if(composeLocked())return;selected.delete(contact.id);invalidate();renderContacts();};
  row.append(info,remove);list.append(row);
 }
 list.scrollTop=scrollTop;
}
function entitySummaryText(rows,mailboxKey){
 const companies=new Set(),mailboxes=new Set(),categories={internal:new Set(),test:new Set(),unconfirmed:new Set()};
 for(const row of rows){const address=String(row[mailboxKey]||'').trim().toLowerCase();if(address)mailboxes.add(address);
  if(row.entity_type==='external_company'&&row.company_id)companies.add(row.company_id);
  else if(address){const kind=['internal','test'].includes(row.entity_type)?row.entity_type:'unconfirmed';categories[kind].add(address);}
 }
 return [`${companies.size} 家已关联公司`,...(categories.internal.size?[`${categories.internal.size} 个内部邮箱`]:[]),...(categories.test.size?[`${categories.test.size} 个测试邮箱`]:[]),...(categories.unconfirmed.size?[`${categories.unconfirmed.size} 个待确认归属邮箱`]:[]),`${mailboxes.size} 个邮箱`].join(' · ');
}
function updateSelection(){const rows=state.contacts.filter(contact=>selected.has(contact.id));$('selectedCount').textContent='已选择 '+entitySummaryText(rows,'email')+' · '+rows.length+' 个联系人';const byCountry=new Map();for(const row of rows)byCountry.set(row.country,(byCountry.get(row.country)||0)+1);const hidden=rows.filter(row=>!matchesContact(row)).length;$('selectedSummary').textContent=rows.length?[...byCountry].map(([country,count])=>country+' '+count+' 个').join(' · ')+(hidden?`；其中 ${hidden} 个在当前筛选之外`:''):'先勾选需要联系的机构';renderSelectedContacts(rows);const testRows=rows.filter(row=>row.country==='测试');$('testSelectionNotice').hidden=!testRows.length;$('testSelectionNotice').textContent=testRows.length===rows.length?'🧪 已选测试邮箱 · 本批只做测试发送。':'⚠️ 同时选中了测试邮箱和正式机构，请清空已选并分批发送。';document.querySelector('.selection-bar').classList.toggle('test-selection',testRows.length>0);$('preview').disabled=!rows.length||composeLocked();$('refreshContacts').disabled=composeLocked()||!state.sheet_configured;$('deleteSelectedContacts').disabled=!rows.length||composeLocked();}
async function removeContacts(ids){
 if(composeLocked())return;
 const rows=state.contacts.filter(row=>ids.includes(row.id));
 if(!rows.length)return;
 const names=rows.slice(0,8).map(row=>row.name+' · '+(row.email||'缺邮箱')).join('\n');
 if(!confirm('删除 '+rows.length+' 个联系人？\n'+names+(rows.length>8?'\n…':'')+'\n\n仅从本地工具移除该机构及邮箱，表格原数据与发送历史保留。刷新或重启后仍保持删除。\n涉及的预览与批准失效；有定时任务请先取消。'))return;
 deletingContacts=true;lockControls();
 try{
  const result=await api('contacts/delete',{ids:rows.map(row=>row.id)});
  for(const row of rows)selected.delete(row.id);
  invalidate();await refresh();
  notice('已删除 '+result.count+' 个联系人；表格原数据与发送历史保留。');
 }finally{deletingContacts=false;lockControls();}
}
$('deleteSelectedContacts').onclick=()=>perform($('deleteSelectedContacts'),()=>removeContacts([...selected]));
function renderCountries(){for(const id of ['contactCountry','recordCountry','templateCountry']){const current=$(id).value;$(id).replaceChildren();const all=el('option',id==='templateCountry'?'通用模板（不绑定国家）':'全部国家 / 地区');all.value='';$(id).append(all);for(const country of state.countries){const option=el('option',countryLabel(country));option.value=country;option.classList.toggle('test-option',country==='测试');$(id).append(option);}$(id).value=state.countries.includes(current)?current:'';$(id).classList.toggle('test-filter',$(id).value==='测试');}$('countrySuggestions').replaceChildren();for(const country of state.countries){const option=el('option');option.value=country;$('countrySuggestions').append(option);}}
function renderContacts(){
 const active=activeContactGroup();$('contactGroupHint').textContent=active?`${active.name}已加载 ${active.loaded_count} / ${active.total_count} 个联系人。切换快捷名单会清空已选联系人。`:'切换快捷名单会清空已选联系人。';
 const visible=visibleContacts();$('contacts').replaceChildren();
 for(const country of state.countries){const group=visible.filter(contact=>contact.country===country);if(!group.length)continue;
  const heading=el('div',countryLabel(country)+' · '+group.length+' 个联系人');heading.className='country-heading'+(country==='测试'?' test-heading':'');$('contacts').append(heading);
  for(const contact of group){
   const row=el('div');row.className='contact'+(contact.country==='测试'?' test-contact':'');
   const checkbox=el('input');checkbox.type='checkbox';checkbox.id='contact-'+contact.id;checkbox.checked=selected.has(contact.id);checkbox.disabled=!contact.email||composeLocked();checkbox.className='compose-field';checkbox.onchange=()=>{checkbox.checked?selected.add(contact.id):selected.delete(contact.id);invalidate();updateSelection();};
   const info=el('div');info.className='contact-info';const label=el('label');label.htmlFor=checkbox.id;
   const agency=el('strong',contact.name);if(contact.country==='测试'){label.className='test-label';const badge=el('span','TEST · 测试');badge.className='test-badge';agency.append(badge);}label.append(agency,el('div',contact.email||contact.email_error||'缺邮箱'));
   const role=el('small',(contact.company_name&&contact.company_name!==contact.name?'公司：'+contact.company_name+' · ':'')+contactRole(contact)+(contact.contact_name?' · '+contact.contact_name:'')+(contact.contact_title?' · '+contact.contact_title:''));role.className='hint';
   const round=el('span',activityName(currentActivityId())+'：'+currentRoundLabel(contact));round.className='status-chip'+(eligibleUnsent(contact)?' status-ready':'');
   const history=el('div',contactHistoryLabel(contact));history.className='contact-history hint';
   info.append(label,role,round,history);
   if(contact.blocked){const stop=el('p',(contact.stop_label||'停止原因未核实')+' · '+(contact.stop_next_action||'发送前人工核对停止原因'));stop.className='stop-warning';info.append(stop);}
   const actions=el('div');actions.className='contact-actions';const detail=el('button','历史');detail.className='secondary';detail.onclick=()=>showContactHistory(contact);detail.setAttribute('aria-label','查看联系历史：'+contact.name);
   const remove=el('button','删除');remove.className='secondary compose-field contact-delete';remove.disabled=composeLocked();remove.setAttribute('aria-label','删除联系人：'+contact.name+' · '+(contact.email||'缺邮箱'));remove.onclick=()=>perform(remove,()=>removeContacts([contact.id]));actions.append(detail,remove);
   row.append(checkbox,info,actions);$('contacts').append(row);
  }
 }
 if(!visible.length){const empty=el('div','当前筛选没有联系人。可以调整条件或新增联系人。');empty.className='empty';$('contacts').append(empty);}
 const available=visible.filter(contact=>contact.email).length,unsent=visible.filter(eligibleUnsent).length;
 $('selectVisible').disabled=!available||composeLocked();$('selectUnsent').disabled=!unsent||composeLocked();
 $('counts').textContent=`当前 ${visible.length} 个联系人 · ${available} 个有邮箱 · 本轮可选未发 ${unsent} 个`;
 updateSelection();updateActivityHint();
}
function renderAccounts(){
 $('mailAccount').replaceChildren();for(const account of state.mail_accounts){const option=el('option',account.label+' · '+account.email+(account.connected_in_session?' · 已连接':' · 未连接'));option.value=account.email;$('mailAccount').append(option);}$('mailAccount').value=state.sender;$('activeSender').textContent='当前发件人：'+state.sender;$('connect').textContent=(state.mail_connected?'重新验证 ':'连接 ')+state.sender;$('passwordLabel').textContent=state.sender+' 的邮箱密码';$('password').placeholder='请输入这个员工自己的密码';$('accountConnectionHint').textContent=state.mail_connected?'本次运行已连接；切换回来可直接使用。重启工具后需重新连接。':'当前员工尚未连接，请输入这个员工自己的密码。';lockMailControls();
 const current=$('recordSender').value;const accounts=new Map(state.mail_accounts.map(account=>[account.email,account.label]));for(const row of state.messages)if(!accounts.has(row.sender))accounts.set(row.sender,row.sender);$('recordSender').replaceChildren();const all=el('option','全部发件员工');all.value='';$('recordSender').append(all);for(const [address,name] of accounts){const option=el('option',name+' · '+address);option.value=address;$('recordSender').append(option);}$('recordSender').value=accounts.has(current)?current:'';
}
function mailBody(message){
 const body=el('div');body.className='mail-body';
 for(const part of message.body_sections||[{kind:'text',text:message.body}]){
  if(part.kind==='divider'){body.append(el('hr'));continue;}
  const bold=['signature-name','contact'].includes(part.kind),section=el(part.kind==='heading'?'h'+part.level:bold?'strong':'div');
  section.className=part.kind==='heading'?'mail-heading mail-heading-'+part.level:part.kind==='greeting'?'mail-greeting':part.kind==='signature-name'?'mail-signature-name':part.kind==='contact'?'mail-contact':'mail-text';
  for(const run of part.runs||[{text:part.text}]){
   let fragment=document.createTextNode(run.text);
   if(run.url){const link=el('a',run.text);link.href=run.url;link.target='_blank';link.rel='noopener';fragment=link;}
   if(run.italic){const emphasis=el('em');emphasis.append(fragment);fragment=emphasis;}
   if(run.bold){const strong=el('strong');strong.append(fragment);fragment=strong;}
   section.append(fragment);
  }
  body.append(section);
 }
 return body;
}
function messageType(message){return message.message_type||(message.country==='测试'?'test':'formal');}
function recordMatchesView(message,view){
 if(view==='unreplied')return confirmedSent(message)&&!hasReply(message)&&!recordMatchesView(message,'errors');
 if(view==='replied')return hasReply(message);
 if(view==='errors')return /失败|退信|待核对|异常/.test(message.status+' '+(message.reply_status||''))||!!message.error;
 if(view==='unsynced')return !message.sheet_synced;
 if(view==='history')return !message.activity_id;
 return true;
}
function filteredRecords(){
 const country=$('recordCountry').value,sender=$('recordSender').value,type=$('recordType').value,activity=$('recordActivity').value,group=$('recordGroup').value,view=$('recordView').value;
 const keywords=$('recordSearch').value.trim().toLowerCase().split(/\s+/).filter(Boolean),members=contactGroupMembers.get(group);
 const emails=new Set(state.contacts.filter(item=>members?.has(item.id)).map(item=>item.email.toLowerCase()));
 const accounts=new Map(state.mail_accounts.map(account=>[account.email,account.label]));
 return state.messages.filter(message=>{
  if((country&&message.country!==country)||(sender&&message.sender!==sender)||(type&&messageType(message)!==type))return false;
  if(activity&&(activity==='legacy'?!!message.activity_id:message.activity_id!==activity))return false;
  if(group&&!members?.has(message.contact_id)&&!emails.has(message.recipient.toLowerCase()))return false;
  if(!recordMatchesView(message,view))return false;
  const text=[message.name,message.recipient,message.subject,message.sender,accounts.get(message.sender),countryLabel(message.country),activityName(message.activity_id),message.template_name,message.email_role,messageType(message)==='test'?'测试发送':'正式发送',message.status==='服务器已接受'?'已发送 服务器已接受':message.status,message.reply_status||'尚未检测到关联回复',message.recommended_next_action||message.next_step,message.stop_label,message.error,message.sheet_synced?'已同步':'待同步',...(message.attachments||[]).map(file=>file.name)].join(' ').toLowerCase();
  return keywords.every(keyword=>text.includes(keyword));
 });
}
function renderRecordGroups(){
 const previous=$('recordGroup').value;$('recordGroup').replaceChildren();
 for(const item of [{id:'',name:'全部名单'},...(state.contact_groups||[])]){const option=el('option',item.name);option.value=item.id;$('recordGroup').append(option);}
 $('recordGroup').value=contactGroupMembers.has(previous)?previous:'';
}
function clockParts(value,zone){
 const parts=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value));
 return Object.fromEntries(parts.map(part=>[part.type,part.value]));
}
function recordTime(value,message){
 if(!value)return '';
 if(Number.isNaN(new Date(value).getTime()))return String(value);
 const japan=clockParts(value,'Asia/Tokyo'),day=japan.year+'/'+japan.month+'/'+japan.day,time=japan.hour+':'+japan.minute;
 if(!message.recipient_timezone)return '日本 '+day+' '+time+'（当地时间未设定）';
 const local=clockParts(value,message.recipient_timezone),localDay=local.year+'/'+local.month+'/'+local.day;
 const label=message.recipient_timezone==='Asia/Jakarta'?'雅加达当地':'当地';
 return '日本 '+day+' '+time+'（'+label+' '+(localDay===day?'':localDay+' ')+local.hour+':'+local.minute+'）';
}
function confirmSend(review,scheduled){
 const dialog=$('sendConfirm'),items=review.recipients.length?review.recipients:batch.messages;
 $('sendConfirmTitle').textContent=(review.required?'正式邮件':'测试邮件')+(scheduled?' · 定时发送确认':' · 发送前再次确认');
 const previous=review.historical_count??items.filter(item=>(item.accepted_count??item.previous_count)>0).length,sameRound=items.filter(item=>item.current_activity_count||item.current_activity_pending||item.current_activity_uncertain).length,stopped=review.stopped_count??items.filter(item=>item.stopped).length;
 $('sendConfirmSummary').textContent='联系目的：'+activityName(batch.activity_id||currentActivityId())+'\n发件人：'+review.sender+'\n模板：'+(batch.template_name||$('templateName').value)+' · 语言：'+(batch.language||$('templateLanguage').value)+'\n附件：'+((batch.attachments||[]).map(item=>item.name).join('、')||'无')+'\n本批 '+review.count+' 封 · 历史联系过 '+previous+' 个邮箱 · 本轮已有记录 '+sameRound+' 个 · 停止联系 '+stopped+' 个';
 const zones=new Set(batch.messages.map(message=>message.recipient_timezone));$('sendConfirmTime').hidden=!scheduled;
 if(scheduled)$('sendConfirmTime').textContent=zones.size===1?'计划：'+recordTime(review.scheduled_at,batch.messages[0]):'各收件人的计划时间见下方（日本时间／当地时间）。';
 $('sendConfirmRecipients').replaceChildren();
 for(const item of items){const row=el('li');row.append(el('strong',item.name),el('small',item.recipient+' · '+(item.email_role||'邮箱角色未核实')));
  if(scheduled&&zones.size>1)row.append(el('small',recordTime(review.scheduled_at,batch.messages.find(message=>message.recipient===item.recipient))));
  const warnings=[];if((item.accepted_count??item.previous_count)>0)warnings.push('历史已发送 '+(item.accepted_count??item.previous_count)+' 封'+(item.last_sent_at?'，最近 '+recordTime(item.last_sent_at,item):''));
  if(item.current_activity_count)warnings.push('本轮已发送 '+item.current_activity_count+' 封，确认再次发送');if(item.current_activity_pending)warnings.push('本轮已有待发送记录');if(item.current_activity_uncertain)warnings.push('本轮有结果待核对记录，请先核实');
  if((item.historical_uncertain_count??item.history_uncertain_count??0)>(item.current_activity_uncertain||0)||!item.current_activity_uncertain&&item.last_status==='结果待核对')warnings.push('历史发送有结果待核对，请先到邮箱核实');
  if((item.historical_pending_count??item.history_pending_count??0)>(item.current_activity_pending||0)||!item.current_activity_pending&&['待发送','待定时发送','发送中'].includes(item.last_status))warnings.push('其他活动已有待发送记录，请核实时间与收件人');
  if(item.stopped)warnings.push((item.stop_label||'已停止联系')+'：'+(item.stop_next_action||'请核实再发送'));
  if(item.same_company_warnings?.length)warnings.push(...item.same_company_warnings.map(value=>typeof value==='string'?value:value.message||value.email||JSON.stringify(value)));
  if(warnings.length){const warning=el('small',warnings.join('；'));warning.className='send-warning';row.append(warning);}$('sendConfirmRecipients').append(row);
 }
 for(const warning of review.same_company_warnings||[]){const row=el('li');row.className='send-warning';row.textContent='同公司邮箱提醒：'+(typeof warning==='string'?warning:(warning.message||(warning.name||warning.company_id||''))+' · '+(warning.recipients||warning.emails||[]).join('、'));$('sendConfirmRecipients').append(row);}
 $('sendConfirmAccept').textContent=scheduled?'确认定时发送':'确认现在发送';dialog.returnValue='cancel';
 return new Promise(resolve=>{dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true});dialog.showModal();});
}
function sendStatus(message){return message.status==='服务器已接受'?'已发送 · 服务器已接受':message.delivery_status||message.status;}
function nextAction(message){
 if(message.recommended_next_action)return message.recommended_next_action;
 if(message.stop_next_action)return message.stop_next_action;
 if(recordMatchesView(message,'errors'))return '核实失败或退信原因，确认地址后再联系';
 if(hasReply(message))return '查看关联回复，人工接手跟进';
 if(message.reply_status==='自动回复')return '查看自动回复，确认返岗日期后人工安排跟进';
 if(message.reply_status==='待人工匹配')return '核对来信是否与此封邮件相关';
 if(['scheduled','awaiting_mail','running'].includes(message.schedule_status))return '等待定时任务执行，可查看或取消';
 if(confirmedSent(message))return '等待回复，按需要电话确认负责人或预约';
 return message.next_step||'人工核对发送状态';
}
function showRecordDetail(message){
 const content=$('recordDetailContent');content.replaceChildren();$('recordDetailTitle').textContent=message.name+' · 邮件详情';
 const meta=el('dl');meta.className='mail-metadata';
 for(const [name,value] of [['联系目的',activityName(message.activity_id)],['收件人',message.name+' <'+message.recipient+'>'],['邮箱角色',message.email_role||'未核实'],...(message.company_name?[['公司',message.company_name]]:[]),...(message.contact_name||message.contact_title?[['联系人',[message.contact_name,message.contact_title].filter(Boolean).join(' · ')]]:[]),...(message.phone?[['电话',message.phone]]:[]),['发件人',message.sender],['发送状态',sendStatus(message)],['发送时间',recordTime(message.sent_at,message)||'无已确认发送时间'],['主题',message.subject],['模板',message.template_name||'历史记录未保存模板名称'],['语言',message.template_language||'历史记录未保存语言'],['附件',(message.attachments||[]).map(item=>item.name+(item.size?'（'+sizeLabel(item.size)+'）':'')).join('、')||'无'],['下一步',nextAction(message)]]){meta.append(el('dt',name),el('dd',value));}
 content.append(meta,el('h3','当时保存的邮件正文'));
 if(typeof message.body==='string')content.append(mailBody(message));else content.append(el('p','这条历史记录未保存邮件正文。'));
 content.append(el('h3','关联回复证据'),el('p',message.reply_status||'尚未检测到关联回复'));
 const evidence=message.reply_detail||message.reply_evidence;
 if(evidence){const detail=el('dl');detail.className='mail-metadata';for(const [label,value] of [['来信人',evidence.sender||evidence.from],['来信主题',evidence.subject],['收到时间',recordTime(evidence.received_at||evidence.date,message)],['检测时间',recordTime(evidence.detected_at,message)],['关联依据',evidence.match_reason]])if(value)detail.append(el('dt',label),el('dd',value));content.append(detail);const text=evidence.text||evidence.body||evidence.excerpt;if(text)content.append(el('pre',text));else content.append(el('p','已保存关联信息，未保存这封来信的正文。'));}
 else if(message.reply_at||hasReply(message))content.append(el('p','历史记录已保存来信状态，但未保存来信正文与详细证据。请在邮箱核对。'));
 else content.append(el('p','当前未检测到可关联的回复，可点击“检查当前邮箱回复与退信”更新。'));
 content.append(el('h3','记录信息'),el('p',(message.sheet_synced?'Sheet 已同步':'Sheet 待同步')+' · 邮件 ID：'+message.id));
 if(!$('recordDetail').open)$('recordDetail').showModal();
}
function renderRecords(){
 if(!state)return;const rows=filteredRecords(),available=new Set(rows.filter(row=>row.can_delete).map(row=>row.id));
 for(const id of recordSelected)if(!available.has(id))recordSelected.delete(id);
 $('recordType').classList.toggle('test-filter',$('recordType').value==='test');$('recordCountry').classList.toggle('test-filter',$('recordCountry').value==='测试');$('logs').replaceChildren();
 const pageSize=Number($('recordPageSize').value)||5,pages=Math.max(1,Math.ceil(rows.length/pageSize));recordPage=Math.max(1,Math.min(recordPage,pages));
 const start=(recordPage-1)*pageSize,pageRows=rows.slice(start,start+pageSize),pageIds=pageRows.filter(row=>row.can_delete).map(row=>row.id);
 $('recordCount').textContent=`当前筛选 ${entitySummaryText(rows,'recipient')} · ${rows.length} 封邮件；显示 ${rows.length?start+1:0}–${start+pageRows.length} 封 / 全部 ${state.messages.length} 封`;
 $('recordSummary').replaceChildren();
 for(const [view,label,count] of [['','全部',rows.length],['unreplied','已发待回复',rows.filter(row=>recordMatchesView(row,'unreplied')).length],['replied','已有回复',rows.filter(row=>recordMatchesView(row,'replied')).length],['errors','需核对',rows.filter(row=>recordMatchesView(row,'errors')).length],['unsynced','待同步',rows.filter(row=>recordMatchesView(row,'unsynced')).length]]){const item=el('span',label+' '+count+' 封');item.className='summary-count';$('recordSummary').append(item);}
 $('recordPageInfo').textContent=`第 ${recordPage} / ${pages} 页`;$('recordPrev').disabled=recordPage<=1;$('recordNext').disabled=recordPage>=pages;
 $('recordSelectedCount').textContent='已选 '+recordSelected.size+' 条';$('deleteSelectedRecords').disabled=!recordSelected.size||sending||recordsBusy;$('clearRecordSelection').disabled=!recordSelected.size||sending||recordsBusy;$('selectFilteredRecords').disabled=!available.size||sending||recordsBusy;
 const pageCheck=$('recordSelectPage');pageCheck.disabled=!pageIds.length||sending||recordsBusy;pageCheck.checked=!!pageIds.length&&pageIds.every(id=>recordSelected.has(id));pageCheck.indeterminate=pageIds.some(id=>recordSelected.has(id))&&!pageCheck.checked;
 for(const message of pageRows){
  const row=el('article');row.className='record-card'+(messageType(message)==='test'?' test-record':'');
  const header=el('div');header.className='record-card-heading';const check=el('input');check.type='checkbox';check.className='record-checkbox';check.setAttribute('aria-label','选择记录：'+message.name+' · '+message.subject);check.checked=recordSelected.has(message.id);check.disabled=!message.can_delete||sending||recordsBusy;check.onchange=()=>{check.checked?recordSelected.add(message.id):recordSelected.delete(message.id);renderRecords();};
  const recipient=el('div');recipient.className='record-recipient';recipient.append(el('strong',message.name),el('span',message.recipient));
  const badge=el('span',activityName(message.activity_id));badge.className='record-kind';header.append(check,recipient,badge);row.append(header);
  const subject=el('p',message.subject);subject.className='record-subject';row.append(subject);
  const account=state.mail_accounts.find(item=>item.email===message.sender);const meta=el('p',countryLabel(message.country)+' · '+(account?account.label+' <'+message.sender+'>':message.sender)+' · '+(messageType(message)==='test'?'测试发送':'正式发送')+(message.template_name?' · '+message.template_name:''));meta.className='hint';row.append(meta);
  const statuses=el('div');statuses.className='record-statuses';const delivery=el('div');delivery.append(el('small','发送'),el('strong',sendStatus(message)));if(message.sent_at)delivery.append(el('small',recordTime(message.sent_at,message)));if(message.scheduled_at)delivery.append(el('small','计划：'+recordTime(message.scheduled_at,message)));if(message.error)delivery.append(el('small',message.error));
  const reply=el('div');reply.append(el('small','回复'),el('strong',message.reply_status||'尚未检测到关联回复'));if(message.reply_at)reply.append(el('small','检测时间：'+recordTime(message.reply_at,message)));statuses.append(delivery,reply);row.append(statuses);
  const next=el('p','下一步：'+nextAction(message));next.className='record-next';row.append(next);
  if(message.stop_label){const stopped=el('p',message.stop_label+' · '+(message.stop_next_action||'人工核对后联系'));stopped.className='stop-warning';row.append(stopped);}
  const actions=el('div');actions.className='record-actions';const detail=el('button','查看邮件详情');detail.onclick=()=>showRecordDetail(message);actions.append(detail);
  if(!message.sheet_synced){const sync=el('span','Sheet 待同步');sync.className='hint';actions.append(sync);}
  if(message.can_cancel_schedule){const cancel=el('button','取消定时');cancel.className='secondary';cancel.disabled=sending||recordsBusy;cancel.onclick=()=>perform(cancel,async()=>{const count=state.messages.filter(item=>item.batch_id===message.batch_id).length;if(!window.confirm('取消本批 '+count+' 封邮件的定时发送？\n已发出的邮件不受影响。'))return;await api('schedule/cancel',{id:message.batch_id});notice('定时任务已取消。');await refreshRecords();});actions.append(cancel);}
  else{const maintenance=el('details');maintenance.className='record-row-maintenance';maintenance.append(el('summary','更多操作'));const stop=el('button','手动停止联系');stop.className='secondary';stop.disabled=sending||recordsBusy||message.schedule_status==='running';stop.onclick=()=>perform(stop,async()=>{if(!window.confirm('手动停止联系 '+message.recipient+'？\n这会保留发送与回复记录，并将邮箱排除在“全选本轮未发”之外。'))return;await api('stop',{email:message.recipient,reason:'manual'});notice('已标记手动停止联系。');await refresh();});const remove=el('button','删除本地记录');remove.className='secondary';remove.disabled=!message.can_delete||sending||recordsBusy;remove.onclick=()=>perform(remove,async()=>{if(!window.confirm('删除这条本地记录？\n'+message.name+' · '+message.recipient+'\n'+message.subject+'\n\n已发邮件和 Google Sheet 日志保留。'))return;await deleteRecordIds([message.id]);});maintenance.append(stop,remove);actions.append(maintenance);}
  row.append(actions);$('logs').append(row);
 }
 if(!rows.length){const empty=el('div','当前筛选没有记录，请调整活动、名单或搜索条件。');empty.className='empty';$('logs').append(empty);}
}
async function refreshRecords(){const result=await api('records');state.messages=result.messages.map(message=>({...message,country:normalizeCountry(message.country)}));renderRecords();state.send_progress=result.send_progress;if(result.contacts)state.contacts=result.contacts.map(contact=>({...contact,country:normalizeCountry(contact.country)}));renderContacts();renderSendProgress(result.send_progress);}
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
async function refresh(){const initialLoad=!state,previousSender=state&&state.sender;state=normalizeStateCountries(await api('state'));if(previousSender&&previousSender!==state.sender){invalidate();$('password').value='';notice('发件员工已切换为 '+state.sender+'，请重新预览确认。');}renderAccounts();renderContactConfig();$('sheetLink').hidden=!state.spreadsheet_url;if(state.spreadsheet_url)$('sheetLink').href=state.spreadsheet_url;$('mailBadge').textContent=state.mail_connected?'公司邮箱已连接':'公司邮箱未连接';$('sheetBadge').textContent=state.sheet_saved?'Sheet 已保存 · 自动恢复':state.sheet_configured?'Sheet 已配置 · 尚未保存':'Sheet 未连接';if(document.activeElement!==$('bridgeUrl'))$('bridgeUrl').value=state.bridge_url||'';$('bridgeSecret').placeholder=state.sheet_configured?'已有密钥，可留空；更换地址时重新填写':'首次填写同步密钥';$('restoreSheet').hidden=!state.sheet_store_error;$('sheetHelp').textContent=state.sheet_store_error||(state.sheet_saved?'连接配置已保存在本机 Mac 钥匙串，重新启动会自动恢复。':'部署地址与同步密钥保存到本机 Mac 钥匙串；下次启动自动恢复。');renderCountries();renderContactGroups();renderRecordGroups();renderActivities();renderTemplateOptions();let removed=false;for(const ident of [...selected]){const contact=state.contacts.find(row=>row.id===ident);if(!contact||!contact.email){selected.delete(ident);removed=true;}}if(removed)invalidate();$('contactsSourceHint').textContent='名单从 Google Sheet「邮件跟踪」读取，本地新增仅保存在工具。表格支持批量新增，每行填写独立机构编号、机构名称、国家、收件邮箱，再点击上方刷新。'+(state.contacts_refreshed_at?' 上次刷新：'+new Date(state.contacts_refreshed_at).toLocaleString('zh-CN',{timeZone:'Asia/Tokyo'})+'（东京）':' 尚未从云端刷新。');renderContacts();renderRecords();renderSendProgress(state.send_progress);if(!state.templates.some(item=>item.id===currentTemplate)){const preferred=state.templates.find(item=>item.id===state.preferred_template);if(initialLoad&&preferred?.activity_id)$('activity').value=preferred.activity_id;const choice=preferred&&templateMatchesActivity(preferred)?preferred:recommendedTemplate()||currentPurposeTemplates()[0];if(choice&&!dirty)loadTemplate(choice.id);renderContacts();}renderTemplateOptions();updateWebsiteHint();}
function freezeCompose(value){sending=value;lockControls();lockMailControls();lockContactControls();$('inbox').disabled=value;$('sync').disabled=value;renderRecords();}
let sessionSendBatch='';
function renderSendProgress(progress){
 const active=progress?.status==='sending',complete=progress?.status==='complete';
 if(active&&progress.batch_id)sessionSendBatch=progress.batch_id;
 $('sendProgress').classList.toggle('is-sending',active);
 $('sendProgress').hidden=!progress||complete&&progress.batch_id!==sessionSendBatch;if($('sendProgress').hidden)return;
 const label=active?'发送中':complete?'发送完成':progress.status==='needs_review'||progress.status==='结果待核对'?'发送已暂停':progress.status==='scheduled'?'已安排定时发送':'本批发送进度';
 const title=`${label} ${progress.processed}/${progress.total}`;
 $('sendProgressTitle').textContent=title;$('sendProgressBar').max=Math.max(1,progress.total);$('sendProgressBar').value=progress.processed;
 const detail=[`服务器已接受 ${progress.accepted} 封`];
 if(progress.failed)detail.push(`失败 ${progress.failed} 封`);
 if(progress.uncertain)detail.push(`结果待核对 ${progress.uncertain} 封`);
 if(progress.remaining)detail.push(`未发送 ${progress.remaining} 封`);
 if(progress.current_name)detail.push('正在发送：'+progress.current_name);
 detail.push(progress.sheet_pending?`Sheet 待同步 ${progress.sheet_pending} 条`:active&&!progress.processed?'发完后自动同步 Sheet':'Sheet 已同步');
 $('sendProgressDetail').textContent=detail.join(' · ');if(sending)$('reviewState').textContent=title+' · '+detail.join(' · ');
}
let sendProgressTimer=null,sendProgressPolling=false;
async function pollSendProgress(ident){
 if(sendProgressPolling)return;sendProgressPolling=true;
 try{const result=await api('send-progress?id='+encodeURIComponent(ident));if(!sending)return;if(state)state.send_progress=result.progress;renderSendProgress(result.progress);}
 catch(error){if(sending)$('sendProgressDetail').textContent='进度暂时无法更新，邮件仍在后台处理；请勿重复发送。';}
 finally{sendProgressPolling=false;}
}
for(const id of ['subject','body','links','templateName','templateLanguage'])$(id).addEventListener('input',markDirty);
function replaceBodyRange(start,end,text,selectionStart=start,selectionEnd=selectionStart+text.length){
 const body=$('body');body.setRangeText(text,start,end,'select');body.focus();body.setSelectionRange(selectionStart,selectionEnd);markDirty();
}
function toggleBodyInline(marker,placeholder){
 if(composeLocked())return;
 const body=$('body'),start=body.selectionStart,end=body.selectionEnd,width=marker.length,text=body.value.slice(start,end);
 if(start===end){replaceBodyRange(start,end,marker+placeholder+marker,start+width,start+width+placeholder.length);return;}
 const leftStars=body.value.slice(0,start).match(/\*+$/)?.[0].length||0,rightStars=body.value.slice(end).match(/^\*+/)?.[0].length||0;
 const outerMatch=width===1?leftStars%2===1&&rightStars%2===1:leftStars>=width&&rightStars>=width;
 if(outerMatch){replaceBodyRange(start-width,end+width,text,start-width,end-width);return;}
 const lines=text.split('\n'),nonempty=lines.filter(line=>line.trim());
 const unwrap=nonempty.length&&nonempty.every(line=>{const text=line.trim(),left=text.match(/^\*+/)?.[0].length||0,right=text.match(/\*+$/)?.[0].length||0;return text.length>width*2&&(width===1?left%2===1&&right%2===1:left>=width&&right>=width);});
 const formatted=lines.map(line=>line.replace(/^(\s*)(.*?)(\s*)$/,(_,leading,content,trailing)=>content?leading+(unwrap?content.slice(width,-width):marker+content+marker)+trailing:line)).join('\n');
 if(lines.length===1&&!unwrap){const leading=text.length-text.trimStart().length;replaceBodyRange(start,end,formatted,start+leading+width,start+formatted.length-(text.length-text.trimEnd().length)-width);}
 else replaceBodyRange(start,end,formatted);
}
function toggleBodyHeading(){
 if(composeLocked())return;
 const body=$('body'),start=body.value.slice(0,body.selectionStart).lastIndexOf('\n')+1;
 const lineEnd=body.value.indexOf('\n',Math.max(body.selectionStart,body.selectionEnd-1)),end=lineEnd<0?body.value.length:lineEnd;
 const lines=body.value.slice(start,end).split('\n'),nonempty=lines.filter(line=>line.trim());
 const unwrap=nonempty.length&&nonempty.every(line=>/^##[ \t]+/.test(line));
 const formatted=lines.map(line=>line.trim()?(unwrap?line.replace(/^##[ \t]+/,''):'## '+line.replace(/^#{1,3}[ \t]+/,'')):line).join('\n');
 if(!nonempty.length)replaceBodyRange(start,end,'## 标题文字',start+3,start+7);
 else replaceBodyRange(start,end,formatted);
}
for(const id of ['formatBold','formatItalic','formatHeading'])$(id).onmousedown=event=>event.preventDefault();
$('formatBold').onclick=()=>toggleBodyInline('**','加粗文字');
$('formatItalic').onclick=()=>toggleBodyInline('*','斜体文字');
$('formatHeading').onclick=toggleBodyHeading;
$('body').onkeydown=event=>{if((event.metaKey||event.ctrlKey)&&!event.altKey&&!event.shiftKey&&['b','i'].includes(event.key.toLowerCase())){event.preventDefault();event.key.toLowerCase()==='b'?toggleBodyInline('**','加粗文字'):toggleBodyInline('*','斜体文字');}};
$('template').onchange=()=>{stashDraft();loadTemplate($('template').value);};$('activity').onchange=changeActivity;$('useRecommendedTemplate').onclick=()=>{const template=recommendedTemplate();if(!template)return;stashDraft();loadTemplate(template.id);renderContacts();notice('已使用推荐模板。之前的未保存内容仍保留在原模板草稿中。');};$('contactCountry').onchange=changeContactCountry;$('search').oninput=renderContacts;$('recordActivity').onchange=$('recordGroup').onchange=$('recordView').onchange=$('recordCountry').onchange=$('recordSender').onchange=$('recordType').onchange=()=>{recordPage=1;recordSelected.clear();renderRecords();};$('recordSearch').oninput=()=>{recordPage=1;recordSelected.clear();renderRecords();};$('recordPageSize').onchange=()=>{recordPage=1;renderRecords();};$('recordPrev').onclick=()=>{recordPage--;renderRecords();};$('recordNext').onclick=()=>{recordPage++;renderRecords();};$('templateCountry').onchange=()=>{markDirty();updateWebsiteHint();};
$('contactGroup').onchange=changeContactGroup;
$('onlyWithEmail').onchange=renderContacts;
$('selectUnsent').onclick=()=>{selected.clear();for(const row of visibleContacts().filter(eligibleUnsent))selected.add(row.id);invalidate();renderContacts();};$('selectVisible').onclick=()=>{const rows=visibleContacts().filter(row=>row.email);for(const row of rows)selected.add(row.id);invalidate();renderContacts();};$('clearSelection').onclick=()=>{selected.clear();invalidate();renderContacts();};
$('refreshContacts').onclick=()=>perform($('refreshContacts'),async()=>{refreshingContacts=true;invalidate();lockControls();const button=$('refreshContacts');button.textContent='正在读取 Google Sheet…';notice('正在读取 Google Sheet 的联系人名单…');try{const result=await api('contacts/refresh',{});await refresh();notice(`联系人已刷新：云端 ${result.count} 家 · 新增 ${result.added} · 更新 ${result.updated} · 移除 ${result.removed}`+(result.invalid_emails?`；${result.invalid_emails} 行邮箱格式无效，请在表格修正。`:'。')+' 本地新增联系人、发送记录与停止联系标记已保留；请重新预览。');}finally{refreshingContacts=false;button.textContent='从 Google Sheet 刷新联系人';lockControls();}});
$('addContact').onclick=()=>perform($('addContact'),async()=>{const added=await api('contact',{name:$('newAgency').value,email:$('newEmail').value,country:$('newCountry').value});await refresh();$('contactGroup').value='';$('contactCountry').value=added.country;loadCountryTemplate(added.country);$('search').value='';if(added.email)selected.add(added.id);invalidate();renderContacts();$('newAgency').value='';$('newEmail').value='';notice('已新增 '+added.name+'（'+added.country+'）');});
async function persistTemplate(asNew){const stored=state.templates.find(item=>item.id===currentTemplate);if(!asNew&&stored&&!templateMatchesActivity(stored))return notice('当前模板属于“'+activityName(templateActivityId(stored))+'”。请使用“另存为新模板”保存到本轮联系目的，保留原模板。');saving=true;lockControls();try{const result=await api('template',{...composeData(),id:asNew?null:currentTemplate});drafts.delete(currentTemplate+'|'+$('templateCountry').value);drafts.delete(result.id+'|'+$('templateCountry').value);currentTemplate=result.id;await refresh();loadTemplate(result.id);notice('模板已保存，主题、正文、附件与链接将在下次启动时保留。');}finally{saving=false;lockControls();}}
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
$('preview').onclick=()=>perform($('preview'),async()=>{const stored=state.templates.find(item=>item.id===currentTemplate);if(stored&&!templateMatchesActivity(stored))return notice('当前模板属于“'+activityName(templateActivityId(stored))+'”，请先选择本轮对应模板。');const scope=$('templateCountry').value,rows=state.contacts.filter(item=>selected.has(item.id));if(scope&&rows.some(item=>item.country!=='测试'&&item.country!==scope))return notice('当前模板绑定“'+countryLabel(scope)+'”，但所选联系人包含其他国家。请切换合适模板，或将模板保存范围设为通用并核对语言。');const version=revision;const result=await api('preview',{...composeData(),ids:[...selected],template_id:currentTemplate});if(version!==revision)return notice('准备预览时内容发生变化，请重新生成。');batch=result;$('previewList').replaceChildren();const isTest=batch.messages.every(message=>messageType(message)==='test');$('testReviewNotice').hidden=!isTest;updateSendMode();$('previewDetails').open=true;$('previewToggle').textContent='邮件预览 · '+batch.messages.length+' 封（点击展开 / 收起）';$('send').classList.toggle('test-send',isTest);const countries=[...new Set(batch.messages.map(message=>countryLabel(message.country)))];$('reviewSummary').textContent=`目的：${activityName(batch.activity_id||currentActivityId())} · ${batch.messages.length} 封邮件 · ${countries.join('、')} · 模板：${batch.template_name||$('templateName').value} · 语言：${batch.language||'未指定'}`;$('reviewAttachments').replaceChildren();const files=el('p',batch.attachments.length?'本批附件：'+batch.attachments.map(file=>file.name+'（'+sizeLabel(file.size)+'）').join('、'):'本批无附件');files.className='hint';$('reviewAttachments').append(files);for(const [index,message] of batch.messages.entries()){const view=el('details');view.open=index===0;view.className='preview'+(messageType(message)==='test'?' test-preview':'');view.append(el('summary',message.name+' → '+message.recipient),el('p','国家 / 地区：'+countryLabel(message.country)+' · 发件人：'+batch.sender),el('p','主题：'+message.subject),mailBody(message));for(const link of message.links){const anchor=el('a',link.label||link.url);anchor.href=link.url;anchor.target='_blank';anchor.rel='noopener';view.append(anchor,el('br'));}const qrInfo=batch.line_signature||batch.whatsapp_signature;if(qrInfo){const signature=el('div');signature.className='line-signature';const anchor=el('a');anchor.href=qrInfo.url;anchor.target='_blank';anchor.rel='noopener';const qr=el('img');qr.src=qrInfo.preview_url;qr.width=180;qr.height=180;qr.alt=qrInfo.channel==='WhatsApp'?'WhatsApp QR Code':'LINE 加好友 QR Code';anchor.append(qr);signature.append(anchor,el('p',qrInfo.channel==='WhatsApp'?'Scan the QR code or click to contact us on WhatsApp.':'掃描 QR Code 或點擊 LINE 連結加入好友。'));view.append(signature);}$('previewList').append(view);}$('review').hidden=false;$('approval').checked=false;$('approval').disabled=false;$('approve').disabled=true;$('send').disabled=true;$('reviewState').textContent='未批准。请核对以上实际内容、链接、附件与二维码。';$('review').scrollIntoView({behavior:'smooth',block:'start'});});
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
  if(!scheduled){sessionSendBatch=ident;renderSendProgress({batch_id:ident,status:'sending',total:batch.messages.length,processed:0,accepted:0,failed:0,uncertain:0,remaining:batch.messages.length,sheet_pending:0});sendProgressTimer=setInterval(()=>pollSendProgress(ident),1000);}
  const result=await api(scheduled?'schedule':'send',{...request,confirmation:review.digest});batch=null;
  if(result.sheet_sync&&!result.sheet_sync.ok)notice(result.sheet_sync.error);
  $('reviewState').textContent=scheduled?'定时任务已保存，可在记录中查看或取消。':'执行结束，查看下方发送记录。';$('approval').disabled=false;await refresh();
 }finally{if(sendProgressTimer){clearInterval(sendProgressTimer);sendProgressTimer=null;}freezeCompose(false);$('sendMode').disabled=$('scheduleAt').disabled=false;if(batch&&batch.id===ident)$('send').disabled=false;}
});
setInterval(async()=>{
 if(!state||sending||accountBusy||recordsBusy||recordsPolling||document.hidden||(!state.messages.some(row=>['scheduled','awaiting_mail','running'].includes(row.schedule_status))&&state.send_progress?.status!=='sending'))return;
 recordsPolling=true;try{await refreshRecords();}catch(error){notice('定时状态刷新失败，可点击刷新记录。');}finally{recordsPolling=false;}
},15000);
$('inbox').onclick=()=>accountAction(async()=>{
 notice(`正在检查 ${state.sender} 最近 30 天的回复与退信，请保持工具运行。`);
 const result=await api('inbox',{}),details=[`${result.complete?'检查完成':'检查未全部完成'}：${result.sender}，读取 ${result.fetched} 封来信，更新 ${result.matched} 条关联记录。`,
  `文件夹：${result.folders.map(folder=>folder.name+(folder.ok?'':'（未完成）')).join('、')}。`];
 if(result.warnings.length)details.push(...result.warnings);
 details.push(result.sheet_sync.ok?`Google Sheet 已同步，本次同步 ${result.sheet_sync.count} 条记录。`:result.sheet_sync.error);
 notice(details.join(' '));
});$('sync').onclick=()=>perform($('sync'),async()=>{const result=await api('sync',{});notice(result.ok?`已同步 ${result.count} 条记录`:result.error);await refresh();});$('refresh').onclick=()=>perform($('refresh'),refresh);
window.addEventListener('beforeunload',event=>{if(dirty||composeLocked()){event.preventDefault();event.returnValue='';}});
refresh().catch(error=>notice(error.message));
