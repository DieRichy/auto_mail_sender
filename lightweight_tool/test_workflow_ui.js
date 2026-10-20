// Public fixtures only: execute the real UI handlers without SMTP or private data.
const assert=require('node:assert/strict');
const {test}=require('node:test');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');

function ui(){
 const elements=new Map();
 function element(tag='div'){return {tagName:tag.toUpperCase(),value:'',checked:false,hidden:false,disabled:false,children:[],dataset:{},scrollTop:0,textContent:'',open:false,
  classList:{toggle(){},add(){},remove(){},contains(){return false;}},append(...items){this.children.push(...items);},prepend(...items){this.children.unshift(...items);},
  replaceChildren(...items){this.children=items;},setAttribute(){},removeAttribute(){},addEventListener(){},scrollIntoView(){},showModal(){this.open=true;}};}
 const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
 const context=vm.createContext({document:{getElementById:get,createElement:element,createTextNode:text=>({textContent:text}),
  querySelector:selector=>selector.startsWith('meta')?{content:'test-token'}:get(selector),querySelectorAll:()=>[],addEventListener(){}},
  window:{addEventListener(){},confirm(){return false;}},fetch:()=>new Promise(()=>{}),setInterval(){},clearInterval(){},setTimeout(){return 1;},clearTimeout(){},Intl,Date,console});
 vm.runInContext(readFileSync(__dirname+'/app.js','utf8'),context);
 const run=source=>vm.runInContext(source,context);
 run(`state={contacts:[
  {id:'NEW',entity_type:'external_company',company_id:'C1',name:'New Recipient',country:'台湾',email:'new@example.com'},
  {id:'HISTORY',entity_type:'external_company',company_id:'C2',name:'Historic Recipient',country:'台湾',email:'old@example.com'},
  {id:'CURRENT',entity_type:'external_company',company_id:'C3',name:'Current Recipient',country:'台湾',email:'current@example.com'},
  {id:'PENDING',entity_type:'external_company',company_id:'C4',name:'Pending Recipient',country:'台湾',email:'pending@example.com'},
  {id:'UNCERTAIN',entity_type:'external_company',company_id:'C5',name:'Uncertain Recipient',country:'台湾',email:'uncertain@example.com'},
  {id:'STOP',entity_type:'external_company',company_id:'C6',name:'Stopped Recipient',country:'台湾',email:'stop@example.com',blocked:true,stop_label:'手动停止联系'},
  {id:'EMPTY',entity_type:'external_company',company_id:'C7',name:'Missing Address',country:'台湾',email:''}
 ],countries:['台湾','日本'],contact_groups:[{id:'itf-2026-agencies',name:'ITF 旅行社',country:'',contact_ids:['NEW','HISTORY','CURRENT','PENDING','UNCERTAIN','STOP','EMPTY'],loaded_count:7,total_count:7}],
 activities:[{id:'first-contact',name:'首次合作介绍'},{id:'itf-2026',name:'ITF 展位邀约'},{id:'responsible-followup',name:'负责人跟进'}],
 templates:[{id:'intro',activity_id:'first-contact',name:'Intro',country:'',language:'English',subject:'Introduction',body:'Intro draft',attachments:[]},
 {id:'itf-zh',activity_id:'itf-2026',name:'ITF invitation Taiwan',country:'台湾',language:'繁體中文',subject:'Visit J3302',body:'ITF zh body',attachments:[]},
 {id:'itf-en',activity_id:'itf-2026',name:'ITF invitation',country:'',language:'English',subject:'Visit J3302',body:'ITF en body',attachments:[]}],
 messages:[
 {id:'old',entity_type:'external_company',company_id:'C2',contact_id:'HISTORY',name:'Historic Recipient',recipient:'old@example.com',sender:'sender@example.com',country:'台湾',activity_id:'',subject:'Historic subject',body:'ACTUAL FROZEN BODY',status:'服务器已接受',confirmed_sent:true,sent_at:'2026-10-01T02:00:00Z',attachments:[{name:'frozen.pdf',size:300}],sheet_synced:true,can_delete:true},
 {id:'current',entity_type:'external_company',company_id:'C3',contact_id:'CURRENT',name:'Current Recipient',recipient:'current@example.com',sender:'sender@example.com',country:'台湾',activity_id:'itf-2026',subject:'Invitation subject',body:'Current frozen body',status:'服务器已接受',confirmed_sent:true,sent_at:'2026-10-12T02:00:00Z',attachments:[],sheet_synced:true,can_delete:true},
 {id:'pending',entity_type:'external_company',company_id:'C4',contact_id:'PENDING',name:'Pending Recipient',recipient:'pending@example.com',sender:'sender@example.com',country:'台湾',activity_id:'itf-2026',subject:'Pending invitation',body:'Pending',status:'待发送',schedule_status:'scheduled',attachments:[],sheet_synced:false,can_delete:false,can_cancel_schedule:true},
 {id:'uncertain',entity_type:'external_company',company_id:'C5',contact_id:'UNCERTAIN',name:'Uncertain Recipient',recipient:'uncertain@example.com',sender:'sender@example.com',country:'台湾',activity_id:'itf-2026',subject:'Needs check',body:'Uncertain',status:'结果待核对',attachments:[],sheet_synced:false,can_delete:false}
 ],mail_accounts:[{email:'sender@example.com',label:'Sender'}],sender:'sender@example.com',country_templates:{},partner_websites:{},sheet_configured:true};
 $('activity').value='itf-2026';$('recordPageSize').value='5';renderContactGroups();renderActivities();renderRecordGroups();loadTemplate('itf-zh');renderContacts();renderRecords();`);
 const json=source=>JSON.parse(run('JSON.stringify('+source+')'));
 const text=node=>[node.textContent||'',...(node.children||[]).map(text)].join(' ');
 return {get,run,json,text};
}

test('all current-round unsent excludes sent, active, uncertain, stopped and missing email, while legacy remains eligible',()=>{
 const {get,run,json,text}=ui();get('contactGroup').value='itf-2026-agencies';get('contactGroup').onchange();
 get('selectUnsent').onclick();assert.deepEqual(json('[...selected]'),['NEW','HISTORY']);
 assert.match(text(get('contacts')),/历史已发送 1 封/);assert.match(text(get('contacts')),/本轮未发/);
 assert.match(text(get('contacts')),/结果待核对/);assert.match(text(get('contacts')),/邮箱角色未核实/);
 assert.equal(run('contactActivityFacts(state.contacts[1]).accepted'),0);
});

test('dirty draft is preserved when country or purpose changes and both purpose mismatch and draft warning remain visible',()=>{
 const {get,run}=ui();run("loadTemplate('intro');$('body').value='My unsaved draft';markDirty();");
 get('activity').value='itf-2026';get('activity').onchange();get('contactCountry').value='日本';get('contactCountry').onchange();
 assert.equal(get('body').value,'My unsaved draft');assert.equal(get('activity').value,'itf-2026');
 assert.match(get('activityHint').textContent,/未保存修改/);assert.match(get('activityHint').textContent,/首次合作介绍/);
 get('useRecommendedTemplate').onclick();assert.equal(get('body').value,'ITF en body');assert.equal(get('activity').value,'itf-2026');
 assert.equal(run("drafts.get('intro|').body"),'My unsaved draft');
});

test('missing purpose-specific template never falls back to an unrelated introduction',()=>{
 const {get,run}=ui();run("state.templates=state.templates.filter(item=>item.activity_id!=='itf-2026');loadTemplate('intro');");
 get('activity').value='itf-2026';get('activity').onchange();
 assert.equal(run('currentTemplate'),'intro');assert.equal(get('activity').value,'itf-2026');assert.equal(get('useRecommendedTemplate').disabled,true);
 assert.match(get('activityHint').textContent,/尚无对应模板/);
});

test('preview prevents wrong-purpose template and mixed-country use without changing the draft',async()=>{
 const {get,run}=ui();run("loadTemplate('intro');selected.add('NEW');");await get('preview').onclick();assert.match(get('notice').textContent,/当前模板属于/);
 run("loadTemplate('itf-zh');state.contacts[0].country='日本';");await get('preview').onclick();assert.match(get('notice').textContent,/包含其他国家/);
 assert.equal(get('body').value,'ITF zh body');
});

test('record details expose frozen content rather than current draft, and legacy purpose remains unclassified',()=>{
 const {get,run,text}=ui();get('body').value='UNSENT EDITED BODY';
 get('logs').children[0].children.at(-1).children[0].onclick();const detail=text(get('recordDetailContent'));
 assert.equal(get('recordDetail').open,true);assert.match(detail,/ACTUAL FROZEN BODY/);assert.match(detail,/Historic subject/);assert.match(detail,/frozen.pdf/);
 assert.match(detail,/历史发送（活动未标记）/);assert.doesNotMatch(detail,/UNSENT EDITED BODY/);
 run("state.messages[0].reply_status='已回复';state.messages[0].reply_at='2026-10-02T01:00:00Z';showRecordDetail(state.messages[0]);");
 assert.match(text(get('recordDetailContent')),/未保存来信正文/);
});

test('bounce and auto response evidence do not count as human replies; malformed Date cannot break details',()=>{
 const {get,run,json,text}=ui();run(`state.messages.push({...state.messages[0],id:'bounce',activity_id:'itf-2026',status:'退信',reply_status:'退信',reply_at:'2026-10-02T01:00:00Z',reply_detail:{sender:'mailer-daemon@example.com',subject:'Delivery failed',received_at:'not-a-date',text:'Address invalid'}});
 state.messages.push({...state.messages[0],id:'auto',reply_status:'自动回复',reply_at:'2026-10-02T01:00:00Z'});renderRecords();`);
 get('recordView').value='replied';get('recordView').onchange();assert.deepEqual(json('filteredRecords().map(item=>item.id)'),[]);
 get('recordView').value='errors';get('recordView').onchange();assert.deepEqual(json('filteredRecords().map(item=>item.id)'),['uncertain','bounce']);
 run("showRecordDetail(state.messages.find(item=>item.id==='bounce'))");assert.match(text(get('recordDetailContent')),/not-a-date/);assert.match(text(get('recordDetailContent')),/Address invalid/);
});

test('activity, group, sender and keyword filters compose; pagination and safe selection still operate',()=>{
 const {get,json}=ui();get('recordActivity').value='itf-2026';get('recordGroup').value='itf-2026-agencies';get('recordSender').value='sender@example.com';get('recordActivity').onchange();
 assert.deepEqual(json('filteredRecords().map(item=>item.id)'),['current','pending','uncertain']);
 get('recordSearch').value='invitation current';get('recordSearch').oninput();assert.deepEqual(json('filteredRecords().map(item=>item.id)'),['current']);
 get('selectFilteredRecords').onclick();assert.deepEqual(json('[...recordSelected]'),['current']);
 get('recordSearch').value='';get('recordSearch').oninput();get('recordPageSize').value='1';get('recordPageSize').onchange();get('recordNext').onclick();assert.match(get('recordPageInfo').textContent,/第 2 \/ 3 页/);
 assert.equal(get('recordSelectPage').disabled,true); // active scheduled row cannot be deleted
});

test('send confirmation clearly lists purpose, real draft metadata, same-company warning and current uncertain results',()=>{
 const {get,run,text}=ui();run(`batch={activity_id:'itf-2026',template_name:'ITF invitation',language:'English',attachments:[{name:'hotel.pdf'}],messages:[state.messages[1]]};
 confirmSend({required:true,sender:'sender@example.com',count:1,historical_count:0,stopped_count:0,recipients:[{name:'Current Recipient',recipient:'current@example.com',previous_count:1,accepted_count:0,current_activity_uncertain:1}],same_company_warnings:['Two addresses at the same company']},false);`);
 assert.match(get('sendConfirmSummary').textContent,/ITF 展位邀约/);assert.match(get('sendConfirmSummary').textContent,/hotel.pdf/);
 assert.match(text(get('sendConfirmRecipients')),/结果待核对/);assert.match(text(get('sendConfirmRecipients')),/Two addresses/);assert.doesNotMatch(text(get('sendConfirmRecipients')),/历史已发送 0 封/);
});


test('saved country-specific introductory preference is retained and cannot replace ITF purpose',()=>{
 const {get,run}=ui();run("state.templates.push({id:'custom-intro',name:'Saved Taiwan custom',country:'台湾',language:'繁體中文',subject:'Custom subject',body:'Saved custom body',attachments:[]});state.country_templates['台湾']='custom-intro';$('activity').value='first-contact';$('contactCountry').value='台湾';dirty=false;loadCountryTemplate('台湾');");
 assert.equal(run('currentTemplate'),'custom-intro');assert.equal(get('body').value,'Saved custom body');
 get('activity').value='itf-2026';get('activity').onchange();assert.equal(run('currentTemplate'),'itf-zh');assert.equal(get('body').value,'ITF zh body');
});


test('country-scoped template can preview actual content for test recipient and carries selected activity',async()=>{
 const {get,run,json}=ui();run("state.contacts[0].country='测试';selected.add('NEW');let capturedPreview=null;api=async(path,data)=>{if(path==='preview'){capturedPreview=data;throw Error('Fixture stopped after preview request');}};");
 await get('preview').onclick();assert.equal(json('capturedPreview.activity_id'),'itf-2026');assert.equal(json('capturedPreview.country'),'台湾');assert.match(get('notice').textContent,/Fixture stopped/);
});

test('purpose-scoped preferred template precedes unrelated legacy country preference',()=>{
 const {get,run}=ui();run("state.activity_country_templates={'itf-2026':{'台湾':'itf-zh'}};state.country_templates['台湾']='intro';$('contactCountry').value='台湾';dirty=false;loadCountryTemplate('台湾');");
 assert.equal(run('currentTemplate'),'itf-zh');assert.equal(get('body').value,'ITF zh body');
});


test('legacy pending and uncertain attempts remain visible in final confirmation without calling them sent',()=>{
 const {get,run,text}=ui();run(`batch={activity_id:'itf-2026',template_name:'ITF invitation',language:'English',attachments:[],messages:[state.messages[1]]};
 confirmSend({required:true,sender:'sender@example.com',count:2,recipients:[{name:'Uncertain old attempt',recipient:'one@example.com',previous_count:1,accepted_count:0,last_status:'结果待核对',current_activity_uncertain:0},{name:'Pending old attempt',recipient:'two@example.com',previous_count:1,accepted_count:0,last_status:'待定时发送',current_activity_pending:0}],same_company_warnings:[]},false);`);
 assert.match(text(get('sendConfirmRecipients')),/历史发送有结果待核对/);assert.match(text(get('sendConfirmRecipients')),/其他活动已有待发送/);assert.doesNotMatch(text(get('sendConfirmRecipients')),/历史已发送 0 封/);
});

test('record totals distinguish companies, mailboxes and individual emails',()=>{
 const {get,run}=ui();run("state.messages.push({...state.messages[0],id:'second-old',company_id:'C2'});state.messages[0].company_id='C2';renderRecords();");
 assert.match(get('recordCount').textContent,/4 家已关联公司 · 4 个邮箱 · 5 封邮件/);
});


test('all-activity unresolved facts are warned even when latest record is accepted',()=>{
 const {get,run,text}=ui();run(`batch={activity_id:'itf-2026',template_name:'ITF invitation',language:'English',attachments:[],messages:[state.messages[1]]};
 confirmSend({required:true,sender:'sender@example.com',count:1,recipients:[{name:'Recipient',recipient:'one@example.com',accepted_count:1,last_status:'服务器已接受',historical_uncertain_count:1,historical_pending_count:1,current_activity_uncertain:0,current_activity_pending:0}],same_company_warnings:[]},false);`);
 assert.match(text(get('sendConfirmRecipients')),/历史发送有结果待核对/);assert.match(text(get('sendConfirmRecipients')),/其他活动已有待发送/);
});

test('saving existing template cannot silently reclassify it to another purpose',async()=>{
 const {get,run}=ui();run("loadTemplate('intro');$('activity').value='itf-2026';");await get('saveTemplate').onclick();
 assert.match(get('notice').textContent,/另存为新模板/);assert.equal(run("state.templates.find(item=>item.id==='intro').activity_id"),'first-contact');
});


test('initial preferred template selects its purpose once, later refresh preserves explicit activity',async()=>{
 const {get,run}=ui();run("const initialFixture=state;initialFixture.templates.push({...initialFixture.templates[0],id:'follow-up',name:'Responsible followup',activity_id:'responsible-followup'});initialFixture.preferred_template='follow-up';state=null;currentTemplate='';api=async()=>initialFixture;renderContactConfig=()=>{};");
 await run('refresh()');assert.equal(get('activity').value,'responsible-followup');assert.equal(run('currentTemplate'),'follow-up');
 get('activity').value='first-contact';get('activity').onchange();assert.equal(run('currentTemplate'),'intro');await run('refresh()');
 assert.equal(get('activity').value,'first-contact');assert.equal(run('currentTemplate'),'intro');
});


test('historical completed batch is hidden and only active sending progress is sticky',()=>{
 const {get,run}=ui();let sticky=false;get('sendProgress').classList.toggle=(name,value)=>{if(name==='is-sending')sticky=value;};
 run("renderSendProgress({batch_id:'historic',status:'complete',total:1,processed:1,accepted:1,failed:0,uncertain:0,remaining:0,sheet_pending:0});");assert.equal(get('sendProgress').hidden,true);assert.equal(sticky,false);
 run("renderSendProgress({batch_id:'this-session',status:'sending',total:2,processed:0,accepted:0,failed:0,uncertain:0,remaining:2,sheet_pending:0});");assert.equal(get('sendProgress').hidden,false);assert.equal(sticky,true);
 run("renderSendProgress({batch_id:'this-session',status:'complete',total:2,processed:2,accepted:2,failed:0,uncertain:0,remaining:0,sheet_pending:0});");assert.equal(get('sendProgress').hidden,false);assert.equal(sticky,false);assert.match(get('sendProgressTitle').textContent,/发送完成 2\/2/);
 run("renderSendProgress(null);");assert.equal(get('sendProgress').hidden,true);assert.equal(sticky,false);
});


test('contact and record details expose available verified phone and responsible-person metadata',()=>{
 const {get,run,text}=ui();run("Object.assign(state.contacts[0],{company_name:'Verified Company',contact_name:'Ms Chen',contact_title:'Manager',phone:'+886 2 1234'});showContactHistory(state.contacts[0]);");
 assert.match(text(get('recordDetailContent')),/Verified Company/);assert.match(text(get('recordDetailContent')),/Ms Chen/);assert.match(text(get('recordDetailContent')),/Manager/);assert.match(text(get('recordDetailContent')),/886 2 1234/);
 run("Object.assign(state.messages[0],{company_name:'Stored Company',contact_name:'Mr Lin',phone:'+886 3 5678'});showRecordDetail(state.messages[0]);");
 assert.match(text(get('recordDetailContent')),/Stored Company/);assert.match(text(get('recordDetailContent')),/Mr Lin/);assert.match(text(get('recordDetailContent')),/886 3 5678/);
});


test('activity changes before initial state arrives do not access absent data',()=>{
 const {get,run}=ui();run('state=null;');get('activity').value='itf-2026';assert.doesNotThrow(()=>get('activity').onchange());
});


test('record company totals exclude internal, test and unknown classifications',()=>{
 const {get,run}=ui();run("state.messages[3].entity_type='internal';renderRecords();");
 assert.match(get('recordCount').textContent,/3 家已关联公司 · 1 个内部邮箱 · 4 个邮箱 · 4 封邮件/);
 run("delete state.messages[0].entity_type;state.messages[1].entity_type='test';delete state.messages[2].company_id;renderRecords();");
 assert.match(get('recordCount').textContent,/0 家已关联公司 · 1 个内部邮箱 · 1 个测试邮箱 · 2 个待确认归属邮箱 · 4 个邮箱 · 4 封邮件/);
});

test('selected recipients count only explicit external company associations and distinct classified mailboxes',()=>{
 const {get,run}=ui();run("state.contacts[1].company_id='C1';state.contacts[2].entity_type='internal';state.contacts[3].entity_type='test';delete state.contacts[4].entity_type;delete state.contacts[5].company_id;for(const id of ['NEW','HISTORY','CURRENT','PENDING','UNCERTAIN','STOP'])selected.add(id);updateSelection();");
 assert.match(get('selectedCount').textContent,/1 家已关联公司 · 1 个内部邮箱 · 1 个测试邮箱 · 2 个待确认归属邮箱 · 6 个邮箱 · 6 个联系人/);
});

test('template dropdown lists only current-purpose versions and does not repeat the purpose in each label',()=>{
 const {get,run}=ui();
 const options=()=>get('template').children.filter(item=>item.tagName==='OPTION'&&item.value).map(item=>item.value);
 assert.deepEqual(options(),['itf-zh','itf-en']);
 assert.doesNotMatch(get('template').children[0].textContent,/ITF 展位邀约 ·/);
 run("state.templates.push({id:'legacy-intro',name:'Saved introduction',language:'English',country:'',subject:'s',body:'b',attachments:[]},{id:'follow',activity_id:'responsible-followup',name:'Followup',language:'繁體中文',country:'台湾',subject:'s',body:'b',attachments:[]});");
 get('activity').value='first-contact';get('activity').onchange();assert.deepEqual(options(),['intro','legacy-intro']);
 get('activity').value='responsible-followup';get('activity').onchange();assert.deepEqual(options(),['follow']);
 get('contactCountry').value='台湾';get('contactCountry').onchange();assert.equal(run('currentTemplate'),'follow');assert.equal(get('template').value,'follow');
});

test('switching purpose with an unsaved draft shows a neutral selection and restores draft after switching back',()=>{
 const {get,run}=ui();get('activity').value='first-contact';get('activity').onchange();run("$('body').value='Unfinished introduction';attachments=[{id:'draft-file',name:'draft.pdf',size:100}];markDirty();");
 get('activity').value='itf-2026';get('activity').onchange();
 assert.equal(get('template').value,'');assert.equal(get('template').children[0].disabled,true);
 assert.equal(get('body').value,'Unfinished introduction');assert.match(get('templateFilterHint').textContent,/首次合作介绍/);
 assert.deepEqual(get('template').children.filter(item=>item.value).map(item=>item.value),['itf-zh','itf-en']);
 get('useRecommendedTemplate').onclick();assert.equal(run('currentTemplate'),'itf-en');assert.equal(get('template').value,'itf-en');
 get('activity').value='first-contact';get('activity').onchange();
 assert.equal(get('body').value,'Unfinished introduction');assert.equal(get('template').value,'intro');
 assert.equal(run('attachments[0].id'),'draft-file');
});

test('test template is separate and appears when testing, while same-purpose business versions remain available',()=>{
 const {get,run}=ui();run("state.templates.push({id:'test-en',name:'English test',language:'English',subject:'Test',body:'Only testing',attachments:[]});");
 get('contactCountry').value='测试';get('contactCountry').onchange();
 assert.equal(run('currentTemplate'),'test-en');assert.equal(get('template').value,'test-en');
 assert.deepEqual(get('template').children.filter(item=>item.tagName==='OPTION').map(item=>item.value),['itf-zh','itf-en']);
 const group=get('template').children.find(item=>item.tagName==='OPTGROUP');assert.equal(group.label,'测试邮箱专用');assert.equal(group.children[0].value,'test-en');
 get('contactCountry').value='台湾';get('contactCountry').onchange();assert.equal(get('template').value,'itf-zh');assert.equal(get('template').children.some(item=>item.tagName==='OPTGROUP'),false);
});

test('empty purpose and removed template never select an unrelated default during refresh',async()=>{
 const {get,run}=ui();run("const fixture=state;fixture.templates=fixture.templates.filter(item=>item.id!=='itf-zh');fixture.preferred_template='intro';api=async()=>fixture;renderContactConfig=()=>{};");
 await run('refresh()');assert.equal(run('currentTemplate'),'itf-en');assert.equal(get('template').value,'itf-en');
 run("state.templates=state.templates.filter(item=>item.activity_id!=='itf-2026');renderTemplateOptions();");
 assert.equal(get('template').value,'');assert.equal(get('template').children.every(item=>!item.value),true);assert.equal(get('body').value,'ITF en body');
});

test('untagged introductory draft is not silently retagged or previewed for another purpose',async()=>{
 const {get,run}=ui();run("state.templates.push({id:'legacy',name:'Old introduction',language:'English',country:'',subject:'Old subject',body:'Old introduction',attachments:[]});$('activity').value='first-contact';loadTemplate('legacy');markDirty();selected.add('NEW');");
 get('activity').value='itf-2026';get('activity').onchange();assert.equal(get('template').value,'');
 await get('preview').onclick();assert.match(get('notice').textContent,/当前模板属于“首次合作介绍”/);
 await get('saveTemplate').onclick();assert.match(get('notice').textContent,/另存为新模板/);assert.equal(run("state.templates.find(item=>item.id==='legacy').activity_id"),undefined);
});

test('template picker groups by folder, puts archived last and never recommends archived templates',()=>{
 const {get,run,json,text}=ui();
 run(`state.templates.push(
  {id:'us',activity_id:'first-contact',name:'Western US',country:'美国',language:'English',subject:'s',body:'b',attachments:[]},
  {id:'ca-zh',activity_id:'first-contact',name:'繁體中文 · 欧美首次合作 · 美加中文团',country:'',language:'繁體中文',subject:'s',body:'b',attachments:[]},
  {id:'tw-general',activity_id:'first-contact',name:'繁體中文 · 台灣 合作',country:'',language:'繁體中文',subject:'s',body:'b',attachments:[]},
  {id:'tw-old',activity_id:'first-contact',name:'Old Taiwan',country:'台湾',language:'繁體中文',folder:'已归档',subject:'s',body:'b',attachments:[]},
  {id:'custom',activity_id:'first-contact',name:'Custom folder',country:'台湾',language:'繁體中文',folder:'ITF 展后',subject:'s',body:'b',attachments:[]},
  {id:'de',activity_id:'first-contact',name:'Western DE',country:'德国',language:'English',subject:'s',body:'b',attachments:[]});`);
 assert.deepEqual(json("state.templates.filter(t=>t.activity_id==='first-contact').map(templateFolder)"),['通用','北美','北美','台湾','已归档','ITF 展后','欧洲']);
 get('activity').value='first-contact';get('activity').onchange();get('contactCountry').value='台湾';get('contactCountry').onchange();
 const folders=get('templateFolders').children.map(box=>box.children[0].children[0].textContent);
 assert.equal(folders[0],'台湾');assert.equal(get('templateFolders').children[0].children[0].children[0].children[0].className,'flag-img');assert.equal(folders.at(-1),'🗄️ 已归档');assert.ok(folders.includes('🇺🇸🇨🇦 北美'));assert.ok(folders.includes('🇪🇺 欧洲'));assert.ok(folders.indexOf('🌐 通用')>folders.indexOf('🇪🇺 欧洲'));
 assert.notEqual(run('currentTemplate'),'tw-old');
 run("state.country_templates['台湾']='tw-old';");assert.equal(run("recommendedTemplate('台湾')?.id"),'custom');
 get('templateSearch').value='custom';run('renderTemplatePicker()');
 assert.deepEqual(get('templateFolders').children.map(box=>box.open),[true]);assert.match(text(get('templateFolders')),/Custom folder/);
});

test('choosing a template from the picker loads it and keeps the sticky action summary current',()=>{
 const {get,run}=ui();run("selected.add('NEW');updateSelection();chooseTemplate('itf-en');");
 assert.equal(run('currentTemplate'),'itf-en');assert.equal(get('body').value,'ITF en body');assert.equal(get('templatePickerName').textContent,'ITF invitation');
 assert.match(get('actionRecipients').textContent,/已选 1 个收件人/);assert.match(get('actionTemplate').textContent,/ITF invitation/);
 run("$('body').value='edited';markDirty();");assert.equal(get('saveTemplate').textContent,'保存修改');assert.match(get('actionTemplate').textContent,/未保存修改/);
});

test('record summary chips count the whole scope and apply their view filter',()=>{
 const {get,run,json}=ui();run("state.messages[0].reply_status='已回复';renderRecords();");
 const chip=label=>get('recordSummary').children.find(item=>item.textContent.startsWith(label));
 chip('已有回复').onclick();assert.equal(get('recordView').value,'replied');assert.deepEqual(json('filteredRecords().map(item=>item.id)'),['old']);
 assert.match(chip('全部').textContent,/全部 4 封/);assert.match(chip('需核对').textContent,/需核对 1 封/);
});

test('errors stay visible while ordinary notices are scheduled to close',()=>{
 const {get,run}=ui();run("setTimeout=()=>{globalThis.timerCount=(globalThis.timerCount||0)+1;return 1;}");
 run("notice('saved')");assert.equal(run('globalThis.timerCount'),1);
 run("notice('failed','error')");assert.equal(run('globalThis.timerCount'),1);assert.equal(get('noticeBar').hidden,false);
 get('noticeClose').onclick();assert.equal(get('noticeBar').hidden,true);
});

test('changing the bound country asks before saving a copy and the copy gets a distinguishable name',async()=>{
 const {get,run}=ui();let saved=null;run("loadTemplate('itf-zh');$('templateCountry').value='日本';");
 run("window.confirm=()=>false;api=async(path,data)=>{if(path==='template')globalThis.savedTemplate=data;return {id:'copy'};};");
 await get('saveTemplate').onclick();assert.equal(run('globalThis.savedTemplate'),undefined);
 run("window.confirm=()=>true;window.prompt=()=>'ITF invitation Japan';refresh=async()=>{state.templates.push({...state.templates[1],id:'copy',country:'日本',name:'ITF invitation Japan'});};");
 await get('saveTemplate').onclick();saved=run('globalThis.savedTemplate');
 assert.equal(saved.name,'ITF invitation Japan');assert.equal(saved.id,'itf-zh');assert.match(get('notice').textContent,/已另存为新模板「ITF invitation Japan」，原模板保留/);
});

test('both select-all buttons replace the selection with the current list',()=>{
 const {get,run,json}=ui();run("selected.add('EMPTY');selected.add('STOP');");
 get('selectVisible').onclick();assert.deepEqual(json('[...selected]'),['NEW','HISTORY','CURRENT','PENDING','UNCERTAIN','STOP']);
 get('selectUnsent').onclick();assert.deepEqual(json('[...selected]'),['NEW','HISTORY']);
});
