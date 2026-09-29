const test=require('node:test'),assert=require('node:assert/strict');
const {applyPatches,summarize,Bridge}=require('../scripts/desktop_bridge.cjs');
test('patch ordering and arrays',()=>{let x={a:[1,2],b:{c:1}};x=applyPatches(x,[{op:'add',path:['a',1],value:3},{op:'remove',path:['a',0]},{op:'replace',path:['b','c'],value:4}]);assert.deepEqual(x,{a:[3,2],b:{c:4}});});
test('invalid patches do not alter prototypes',()=>{assert.throws(()=>applyPatches({},[{op:'add',path:['__proto__','x'],value:1}]));assert.equal({}.x,undefined);});
test('canonical history respects visible island order',()=>{let x={id:'x',requests:[],threadRuntimeStatus:{type:'idle'},turnHistory:{kind:'canonical',history:{entitiesByKey:{new:{turnId:'new',status:'interrupted'},old:{turnId:'old',status:'completed'}},islands:[{entries:[{value:'old'},{value:'new'}]}]}}};let y=summarize(x,'owner');assert.equal(y.turn_id,'new');assert.equal(y.status,'interrupted');assert.equal(y.owner,'owner');assert.equal(y.waiting,false);});
test('pending approvals and submissions are waiting',()=>{for(const x of [{requests:[{}]},{threadRuntimeStatus:{activeFlags:['waitingOnApproval']}},{unconfirmedTurnSubmissions:[{}]}])assert.equal(summarize(x,'owner').waiting,true);});
test('summary never exports prompt or transcript',()=>{const x={id:'x',turns:[{turnId:'a',status:'completed',items:[{text:'secret'}],params:{input:[{text:'secret'}]}}]};assert.equal(JSON.stringify(summarize(x,'owner')).includes('secret'),false);});
test('new incompatible protocol invalidates all cached states and blocks mutations',async()=>{
 const events=[],b=new Bridge(e=>events.push(e));b.followed.add('chat');b.states.set('chat',{state:{id:'chat'},owner:'owner'});
 b.receive({type:'broadcast',method:'thread-stream-state-changed',version:12,params:{hostId:'local',conversationId:'chat',change:{type:'snapshot',conversationState:{id:'chat'}}}});
 assert.equal(b.states.size,0);assert.equal(b.compatible,false);assert.equal(events[0].observedVersion,12);
 await assert.rejects(b.command({op:'resume',thread:'chat'}),/incompatible/);
 b.receive({type:'broadcast',method:'thread-stream-state-changed',version:11,params:{hostId:'local',conversationId:'chat',change:{type:'snapshot',conversationState:{id:'chat'}}}});
 assert.equal(b.states.size,0);
});
function queueBridge(overrides={}) {
 const calls=[],b=new Bridge(()=>{});
 b.states.set('chat',{owner:'owner',state:{id:'chat',requests:[],threadRuntimeStatus:{type:'idle'},turns:[{turnId:'previous',status:'completed'}],...overrides}});
 b.request=async(method,params)=>{calls.push({method,params});return method==='thread-owner-discovery'?{handledByClientId:'owner'}:{result:{turn:{id:'next'}}};};
 return {b,calls};
}
test('queue start inherits original chat settings and sends exact user text',async()=>{
 const {b,calls}=queueBridge();const result=await b.command({op:'queue_start',thread:'chat',expected_turn:'previous',operation_id:'op',text:'User task\nsecond line'});
 assert.equal(result.turn.id,'next');const params=calls[1].params;
 assert.equal(calls[1].method,'thread-follower-start-turn');assert.equal(params.turnStart.context.inheritThreadSettings,true);
 assert.equal(params.turnStart.request.input[0].text,'User task\nsecond line');assert.equal(params.turnStart.request.clientUserMessageId,'op');
});
test('queue cannot treat interruption, error, approval or active goal as completion',async()=>{
 for(const overrides of [{turns:[{turnId:'previous',status:'interrupted'}]},{turns:[{turnId:'previous',status:'failed'}]},{requests:[{}]},{threadGoal:{status:'active'}},{threadGoal:{status:'paused'}},{threadRuntimeStatus:{type:'active'}}]){
  const {b,calls}=queueBridge(overrides);
  await assert.rejects(b.command({op:'queue_start',thread:'chat',expected_turn:'previous',text:'next'}));
  assert.equal(calls.filter(c=>c.method==='thread-follower-start-turn').length,0);
 }
});
test('queue rechecks turn after owner discovery before sending',async()=>{
 const {b,calls}=queueBridge();b.request=async(method)=>{calls.push(method);b.states.get('chat').state.turns=[{turnId:'manual',status:'completed'}];return {handledByClientId:'owner'};};
 await assert.rejects(b.command({op:'queue_start',thread:'chat',expected_turn:'previous',text:'next'}),/turn changed/);
 assert.equal(calls.length,1);
});
