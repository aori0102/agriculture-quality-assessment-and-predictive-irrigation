const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../widget.html'),'utf8');
const keys=['madp','fc','pwp','sat'];
function el(){return {value:'',textContent:'',style:{},dataset:{},children:[],disabled:false,classList:{toggle(){}},events:{},addEventListener(n,f){this.events[n]=f},append(...items){this.children.push(...items)},replaceChildren(...items){this.children=items},focus(){},setAttribute(){},remove(){},click(){return this.events.click?.()}}}
async function boot({preset=0,partial=false,unknown=false,blankDescription=false,live=true,fail=false,mismatch=false,stale=false,reject=false,restore=false,missingTimestamp=false,nodeId='NODE_001',catalogNodes=null,deviceDetails={id:4317587,orgId:1181191}}={}){
 const ids=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],el()]));
 const cards=keys.map(key=>{const c=el();c.dataset.key=key;c.input=el();c.reading=el();c.querySelector=s=>s==='input'?c.input:c.reading;return c});
 let clock=1000, callbacks;
 const values=[.25,.35,.15,.5,'Unhealthy',0,'SAVED: node=NODE_001; Custom',preset,unknown?'':nodeId,'Bed A','Tomato'].map((value,index)=>({index,value:String(value),lastUpdated:100}));
 if(partial)values[2].value='';
 if(blankDescription)values[9].value=null;
 if(catalogNodes!==null)values.push({index:11,value:JSON.stringify({v:1,nodes:catalogNodes}),lastUpdated:100});
 if(missingTimestamp)values[6].lastUpdated=null;
 const writes=[],links=[];
 class Bridge{
  isBridgeAvailable(){return true}
  async getDeviceInfo(){return deviceDetails}
  setCallbacks(c){callbacks=c}
  async getValues(){return values.map(v=>({...v}))}
  sendValue(index,value){
   writes.push({index,value});
   if(fail){callbacks.onError('write failed');return}
   if(mismatch&&index===1)return;
   values[index]={index,value:String(value),lastUpdated:++clock};
   if(index===7)values[6]={index:6,value:'DRAFT: Custom; edit fields, then Apply',lastUpdated:++clock};
   if(index===5&&!stale)values[6]={index:6,value:reject?'REJECTED: node=NODE_001; invalid':restore?'SAVED: node=NODE_001; unsaved drafts discarded':'SAVED: node='+nodeId+'; Custom',lastUpdated:++clock};
  }
 }
 const context={document:{querySelector(s){return s.startsWith('#')?ids[s.slice(1)]:cards.find(c=>s.includes('"'+c.dataset.key+'"'))},querySelectorAll(){return cards},createElement(tag){const e=el();if(tag==='a')e.click=()=>links.push(e.href);return e},body:{appendChild(){}}},setTimeout(f){clock+=1000;return setImmediate(f)},clearTimeout:clearImmediate,Date:class extends Date{static now(){return clock}},console};
 if(live)context.BlynkBridge=Bridge;
 vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
 await new Promise(r=>setImmediate(r));
 return {ids,cards,writes,links,values,push:item=>callbacks.onValueUpdated(item),change(k,v){const input=cards[keys.indexOf(k)].input;input.value=String(v);input.events.input({target:input})},apply(){return ids.applyBtn.click()}};
}
test('fractions display as percentages',async()=>{const w=await boot();assert.equal(Number(w.cards[0].reading.textContent),25);assert.equal(Number(w.cards[2].reading.textContent),15)});
test('slider and Reset are local; Apply writes fractions before trigger',async()=>{const w=await boot();w.change('madp',30);assert.equal(w.writes.length,0);w.ids.resetBtn.click();assert.equal(Number(w.cards[0].reading.textContent),25);w.change('madp',30);await w.apply();assert.deepEqual(w.writes,[{index:0,value:.30},{index:1,value:.35},{index:2,value:.15},{index:3,value:.5},{index:5,value:1}]);assert.match(w.ids.message.textContent,/Saved to Edge/)});
test('preset switches to Custom before writing thresholds',async()=>{const w=await boot({preset:1});w.change('fc',37);await w.apply();assert.deepEqual(w.writes[0],{index:7,value:0});assert.equal(w.writes.at(-1).index,5)});
for(const option of ['partial','unknown'])test(option+' blocks writes',async()=>{const w=await boot({[option]:true});w.change('madp',30);await w.apply();assert.equal(w.writes.length,0)});
for(const option of ['fail','mismatch'])test(option+' never triggers Apply',async()=>{const w=await boot({[option]:true});w.change('fc',37);await w.apply();assert.ok(!w.writes.some(v=>v.index===5));assert.doesNotMatch(w.ids.message.textContent,/Saved to Edge/)});
test('stale saved status cannot confirm new Apply',async()=>{const w=await boot({stale:true});w.change('madp',30);await w.apply();assert.match(w.ids.message.textContent,/not confirmed/i)});
test('backend rejection preserves draft',async()=>{const w=await boot({reject:true});w.change('madp',30);await w.apply();assert.match(w.ids.message.textContent,/REJECTED/);assert.equal(Number(w.cards[0].reading.textContent),30)});
test('MADP must stay between PWP and FC',async()=>{const w=await boot();for(const v of [0,15,35,100]){w.change('madp',v);await w.apply()}assert.equal(w.writes.length,0)});
test('navigation discovers registered nodes from V19 without changing streams',async()=>{const w=await boot({catalogNodes:[{n:'NODE_001',d:4317587,h:'sgp1.blynk.cloud'},{n:'greenhouse-east',d:4333323,h:'sgp1.blynk.cloud'}]});assert.deepEqual(w.ids.profileSelect.children.map(option=>option.textContent),['Go to node…','NODE_001','greenhouse-east']);w.ids.profileSelect.value='greenhouse-east';w.ids.profileSelect.events.change();assert.deepEqual(w.links,['https://sgp1.blynk.cloud/dashboard/1181191/global/devices/474839/organization/1181191/devices/4333323/dashboard']);assert.equal(w.writes.length,0)});
test('current node remains navigable before V19 is bound',async()=>{const w=await boot();assert.match(w.ids.navigationStatus.textContent,/Bind V19/);w.ids.profileSelect.value='NODE_001';w.ids.profileSelect.events.change();assert.deepEqual(w.links,['https://sgp1.blynk.cloud/dashboard/1181191/global/devices/474839/organization/1181191/devices/4317587/dashboard'])});
test('catalog ID opens without bridge device info',async()=>{const w=await boot({deviceDetails:{id:null,orgId:null},catalogNodes:[{n:'NODE_001',d:4317587,h:'sgp1.blynk.cloud'}]});w.ids.profileSelect.value='NODE_001';w.ids.profileSelect.events.change();assert.equal(w.links.length,1);assert.match(w.links[0],/devices\/4317587\/dashboard$/)});
test('Open button stays unavailable while edits are unapplied',async()=>{const w=await boot();w.change('madp',30);assert.equal(w.ids.openProfile.disabled,true);w.ids.profileSelect.value='NODE_001';w.ids.openProfile.click();assert.equal(w.links.length,0);assert.match(w.ids.message.textContent,/Apply or Reset/)});
test('preview applies locally',async()=>{const w=await boot({live:false});w.change('madp',30);await w.apply();assert.equal(w.writes.length,0);assert.match(w.ids.message.textContent,/locally/)});

test('reconnect restoration cannot confirm Apply',async()=>{const w=await boot({restore:true});w.change('madp',30);await w.apply();assert.match(w.ids.message.textContent,/reconnected/)});
test('missing timestamp prevents Apply trigger',async()=>{const w=await boot({missingTimestamp:true});w.change('madp',30);await w.apply();assert.ok(!w.writes.some(v=>v.index===5));assert.match(w.ids.message.textContent,/timestamp unavailable/)});
test('duplicate clicks and edits are blocked during submission',async()=>{const w=await boot();w.change('madp',30);const saving=w.apply();assert.ok(w.ids.applyBtn.disabled);assert.ok(w.cards.every(c=>c.input.disabled));w.change('madp',55);await w.apply();await saving;assert.equal(w.writes.filter(v=>v.index===5).length,1);assert.equal(Number(w.cards[0].reading.textContent),30)});
test('local sliders work before device/node setup is complete',async()=>{const w=await boot({unknown:true});assert.ok(w.cards.every(c=>!c.input.disabled));w.change('madp',30);assert.equal(Number(w.cards[0].reading.textContent),30);assert.equal(w.writes.length,0);assert.equal(w.ids.applyBtn.disabled,true);assert.match(w.ids.setupMessage.textContent,/V7/)});
test('missing threshold can be filled locally before Apply',async()=>{const w=await boot({partial:true});assert.ok(w.cards.every(c=>!c.input.disabled));assert.equal(w.ids.applyBtn.disabled,true);w.change('pwp',15);assert.equal(w.ids.applyBtn.disabled,false);await w.apply();assert.match(w.ids.message.textContent,/Saved to Edge/)});

test('arbitrary V7 identity requires no catalog or device metadata API',async()=>{const w=await boot({nodeId:'greenhouse-east-42'});assert.equal(w.ids.profileSelect.value,'');assert.equal(w.ids.nodeId.textContent,'greenhouse-east-42');w.change('madp',30);assert.equal(w.ids.applyBtn.disabled,false)});
test('node identity change blocks an existing draft',async()=>{const w=await boot();w.change('madp',30);w.push({index:8,value:'NODE_002',lastUpdated:200});await w.apply();assert.equal(w.writes.length,0);assert.match(w.ids.setupMessage.textContent,/reload/i)});
test('name editing stays local until Apply writes V9',async()=>{const w=await boot();w.ids.editNameBtn.click();w.ids.plantNameInput.value='Cherry Tomato';w.ids.plantNameInput.events.input();assert.equal(w.writes.length,0);await w.apply();assert.ok(w.writes.some(v=>v.index===10&&v.value==='Cherry Tomato'));assert.equal(w.writes.at(-1).index,5);assert.equal(w.ids.plantName.textContent,'Cherry Tomato')});
test('Reset discards edited plant name',async()=>{const w=await boot();w.ids.editNameBtn.click();w.ids.plantNameInput.value='Changed';w.ids.plantNameInput.events.input();w.ids.resetBtn.click();assert.equal(w.ids.plantName.textContent,'Tomato');assert.equal(w.writes.length,0)});
test('empty or control-character names cannot be submitted',async()=>{const w=await boot();for(const name of ['   ','bad\nname','x'.repeat(129)]){w.ids.editNameBtn.click();w.ids.plantNameInput.value=name;w.ids.plantNameInput.events.input();await w.apply()}assert.equal(w.writes.length,0)});

for(const nodeId of ['','   ','bad\nnode','n'.repeat(129)])test('invalid node identity blocks Apply: '+JSON.stringify(nodeId),async()=>{const w=await boot({nodeId});w.change('madp',30);await w.apply();assert.equal(w.writes.length,0);assert.match(w.ids.setupMessage.textContent,/V7/)});
test('identity change found by readback blocks all writes',async()=>{const w=await boot();w.change('madp',30);w.values[8].value='different-node';await w.apply();assert.equal(w.writes.length,0);assert.match(w.ids.setupMessage.textContent,/reload/i)});
test('missing identity can arrive later without reload',async()=>{const w=await boot({unknown:true});w.push({index:8,value:'new-node',lastUpdated:200});w.change('madp',30);assert.equal(w.ids.applyBtn.disabled,false)});
test('identity loss during submission aborts subsequent writes',async()=>{const w=await boot();w.change('madp',30);const saving=w.apply();w.push({index:8,value:'',lastUpdated:200});await saving;assert.equal(w.writes.length,0);assert.match(w.ids.setupMessage.textContent,/reload/i)});

test('an uncatalogued node can save its profile with matching status',async()=>{const w=await boot({nodeId:'zone-east;bed-42'});w.change('madp',30);await w.apply();assert.match(w.ids.message.textContent,/Saved to Edge/);assert.ok(!w.writes.some(v=>v.index===8||v.index===7))});
test('navigation does not retarget a dynamically identified node',async()=>{const w=await boot({nodeId:'zone-west',catalogNodes:[{n:'zone-west',d:4317587,h:'sgp1.blynk.cloud'},{n:'zone-east',d:4333323,h:'sgp1.blynk.cloud'}]});w.ids.profileSelect.value='zone-east';w.ids.profileSelect.events.change();assert.equal(w.ids.nodeId.textContent,'zone-west');assert.equal(w.writes.length,0)});
test('new catalog push refreshes the node list',async()=>{const w=await boot({catalogNodes:[{n:'NODE_001',d:4317587,h:'sgp1.blynk.cloud'}]});w.push({index:11,value:JSON.stringify({v:1,nodes:[{n:'NODE_001',d:4317587,h:'sgp1.blynk.cloud'},{n:'new-node',d:4422001,h:'sgp1.blynk.cloud'}]}),lastUpdated:200});assert.ok(w.ids.profileSelect.children.some(option=>option.value==='new-node'))});
test('invalid catalog cannot create a navigation link',async()=>{const w=await boot({catalogNodes:[{n:'NODE_001',d:4317587,h:'evil.example'}]});assert.match(w.ids.navigationStatus.textContent,/Invalid/);assert.ok(!w.ids.profileSelect.children.some(option=>option.value==='evil.example'))});

test('description displays V8 and updates without writes',async()=>{const w=await boot();assert.equal(w.ids.plantDescription.textContent,'Bed A');w.push({index:9,value:'North greenhouse bed',lastUpdated:200});assert.equal(w.ids.plantDescription.textContent,'North greenhouse bed');assert.equal(w.writes.length,0)});
test('blank V8 and browser preview show the default description',async()=>{const w=await boot();w.push({index:9,value:'   ',lastUpdated:200});assert.equal(w.ids.plantDescription.textContent,'Monitor soil moisture and adjust irrigation settings for this plant.');assert.equal(w.writes.length,0);assert.equal(w.ids.applyBtn.disabled,true);const preview=await boot({live:false});assert.equal(preview.ids.plantDescription.textContent,w.ids.plantDescription.textContent)});
test('unset V8 is an allowed empty description for Apply',async()=>{const w=await boot({blankDescription:true});w.change('madp',30);assert.equal(w.ids.applyBtn.disabled,false);await w.apply();assert.match(w.ids.message.textContent,/Saved to Edge/)});
