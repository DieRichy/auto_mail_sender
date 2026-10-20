// Exercises the actual UI handlers without a server, browser or private roster.
const assert=require('node:assert/strict');
const {test}=require('node:test');
const {readFileSync}=require('node:fs');
const vm=require('node:vm');

function ui(){
 const elements=new Map();
 function element(){return {value:'',checked:false,hidden:false,children:[],dataset:{},scrollTop:0,
  classList:{toggle(){},add(){},remove(){}},append(...items){this.children.push(...items);},prepend(...items){this.children.unshift(...items);},
  replaceChildren(...items){this.children=items;},setAttribute(){},removeAttribute(){},addEventListener(){}};}
 const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
 const context=vm.createContext({document:{getElementById:get,createElement:element,createTextNode:text=>({textContent:text}),
  querySelector:selector=>selector.startsWith('meta')?{content:'test-token'}:get(selector),querySelectorAll:()=>[],addEventListener(){}},
  window:{addEventListener(){}},fetch:()=>new Promise(()=>{}),setInterval(){},clearInterval(){},setTimeout(){return 1;},clearTimeout(){},Intl,console});
 vm.runInContext(readFileSync(__dirname+'/app.js','utf8'),context);
 const run=source=>vm.runInContext(source,context);
 run(`state={contacts:[
  {id:'TW-001',name:'Taiwan One',country:'台湾',email:'tw@example.com'},
  {id:'JP-ITF-001',name:'Japan Exhibitor',country:'日本',email:'jp@example.com'},
  {id:'ITF-NO-EMAIL',name:'No Email Exhibitor',country:'台湾',email:''},
  {id:'SG-001',name:'Unrelated Agency',country:'新加坡',email:'sg@example.com'}
 ],countries:['台湾','日本','新加坡'],contact_groups:[
  {id:'taipei-179',name:'台北 179 家',country:'台湾',contact_ids:['TW-001'],loaded_count:1,total_count:179},
  {id:'itf-2026',name:'ITF Taiwan 2026 参展商',country:'',contact_ids:['TW-001','JP-ITF-001','ITF-NO-EMAIL','MISSING'],loaded_count:3,total_count:4}
 ],contact_groups_error:'',templates:[],country_templates:{},partner_websites:{},sheet_configured:true,activities:[{id:'first-contact',name:'首次合作介绍',purpose:'介绍公司与住宿合作'},{id:'itf-2026',name:'ITF 展位邀约',purpose:'邀请参展商到 J3302 交流'},{id:'responsible-followup',name:'负责人跟进',purpose:'向已核实的负责人继续联系'}]};
 renderContactGroups();`);
 const ids=()=>JSON.parse(run('JSON.stringify(visibleContacts().map(row=>row.id))'));
 return {get,run,ids};
}

test('ITF selection clears prior recipients and search, resets to all countries, and renders loaded count',()=>{
 const {get,run,ids}=ui();
 get('contactCountry').value='台湾';get('search').value='unrelated';get('onlyWithEmail').checked=true;
 run("selected.add('SG-001');batch={id:'prior-preview'};");get('contactGroup').value='itf-2026';get('contactGroup').onchange();
 assert.equal(get('contactCountry').value,'');assert.equal(get('search').value,'');assert.equal(get('onlyWithEmail').checked,false);
 assert.equal(run('selected.size'),0);assert.equal(run('batch'),null);
 assert.deepEqual(ids(),['TW-001','JP-ITF-001','ITF-NO-EMAIL']);
 assert.match(get('contactGroupHint').textContent,/ITF Taiwan 2026 参展商已加载 3 \/ 4 个联系人/);
 get('selectVisible').onclick();assert.deepEqual(JSON.parse(run('JSON.stringify([...selected])')),['TW-001','JP-ITF-001']);
});

test('country and search narrow ITF without exiting the group; only valid email rows are selectable',()=>{
 const {get,run,ids}=ui();get('contactGroup').value='itf-2026';get('contactGroup').onchange();
 run("selected.add('TW-001');");get('contactCountry').value='日本';get('contactCountry').onchange();
 assert.equal(get('contactGroup').value,'itf-2026');assert.equal(run('selected.has("TW-001")'),true);
 assert.deepEqual(ids(),['JP-ITF-001']);
 get('contactCountry').value='';get('contactCountry').onchange();get('search').value='exhibitor';get('search').oninput();
 assert.deepEqual(ids(),['JP-ITF-001','ITF-NO-EMAIL']);
 get('onlyWithEmail').checked=true;get('onlyWithEmail').onchange();assert.deepEqual(ids(),['JP-ITF-001']);
});

test('Taipei selection retains Taiwan default and exiting to another country clears selection',()=>{
 const {get,run,ids}=ui();get('contactGroup').value='taipei-179';get('contactGroup').onchange();
 assert.equal(get('contactCountry').value,'台湾');assert.deepEqual(ids(),['TW-001']);
 get('selectVisible').onclick();assert.equal(run('selected.size'),1);
 get('contactCountry').value='新加坡';get('contactCountry').onchange();
 assert.equal(get('contactGroup').value,'');assert.equal(run('selected.size'),0);assert.deepEqual(ids(),['SG-001']);
});

test('an unavailable group clears prior selection and displays the configuration issue',()=>{
 const {get,run}=ui();get('contactGroup').value='itf-2026';get('contactGroup').onchange();get('selectVisible').onclick();
 run("state.contact_groups=state.contact_groups.slice(0,1);state.contact_groups_error='快捷名单配置未加载：JSON 格式错误';renderContactGroups();renderContacts();");
 assert.equal(get('contactGroup').value,'');assert.equal(run('selected.size'),0);
 assert.equal(get('contactGroupError').hidden,false);assert.match(get('contactGroupError').textContent,/JSON 格式错误/);
 assert.deepEqual(get('contactGroup').children.map(option=>option.value),['','taipei-179']);
});

test('three ITF groups stay independent when switching groups and narrowing countries',()=>{
 const {get,run,ids}=ui();
 run(`state.contact_groups=[state.contact_groups[0],
  {id:'itf-2026-agencies',name:'ITF 旅行社',country:'',contact_ids:['TW-001','JP-ITF-001'],loaded_count:2,total_count:2},
  {id:'itf-2026-related',name:'ITF 旅行相关',country:'',contact_ids:['ITF-NO-EMAIL'],loaded_count:1,total_count:1},
  {id:'itf-2026-airlines',name:'ITF 航空公司',country:'',contact_ids:['SG-001'],loaded_count:1,total_count:1}
 ];renderContactGroups();`);
 assert.deepEqual(get('contactGroup').children.slice(2).map(option=>option.textContent),['ITF 旅行社','ITF 旅行相关','ITF 航空公司']);
 const scenarios=[['itf-2026-agencies',['TW-001','JP-ITF-001'],'日本',['JP-ITF-001']],
  ['itf-2026-related',['ITF-NO-EMAIL'],'台湾',['ITF-NO-EMAIL']],
  ['itf-2026-airlines',['SG-001'],'新加坡',['SG-001']]];
 for(const [group,all,country,narrowed] of scenarios){
  run("selected.add('TW-001');");get('search').value='prior search';get('contactGroup').value=group;get('contactGroup').onchange();
  assert.equal(run('selected.size'),0);assert.equal(get('search').value,'');assert.equal(get('contactCountry').value,'');
  assert.deepEqual(ids(),all);
  get('contactCountry').value=country;get('contactCountry').onchange();
  assert.equal(get('contactGroup').value,group);assert.deepEqual(ids(),narrowed);
 }
});
