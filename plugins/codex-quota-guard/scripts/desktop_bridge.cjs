'use strict';
// Version-specific adapter for the existing desktop IPC. Never starts a router,
// edits app files, impersonates a desktop owner, or answers approvals.
const net = require('node:net'), readline = require('node:readline'), crypto = require('node:crypto');
const V = {'thread-owner-discovery':1,'thread-follower-start-turn':2,'thread-follower-interrupt-turn':4,'thread-follower-steer-turn':1};
function applyPatches(root, patches) {
  for (const p of patches) {
    if (!Array.isArray(p.path) || p.path.some(x=>['__proto__','constructor','prototype'].includes(x))) throw Error('unsupported patch');
    if (!p.path.length) { if(p.op==='replace'||p.op==='add')root=p.value;else throw Error('root removal'); continue; }
    let obj=root; for(const key of p.path.slice(0,-1)){ if(obj==null)throw Error('patch gap');obj=obj[key]; }
    const key=p.path.at(-1);
    if(p.op==='remove'){if(Array.isArray(obj))obj.splice(Number(key),1);else delete obj[key];}
    else if(p.op==='add'){if(Array.isArray(obj))obj.splice(Number(key),0,p.value);else obj[key]=p.value;}
    else if(p.op==='replace')obj[key]=p.value; else throw Error('unsupported patch');
  } return root;
}
function summarize(s, owner) {
  let turns=s.turns||[];
  if(s.turnHistory?.kind==='canonical') {
    const h=s.turnHistory.history;
    turns=(h.islands||[]).flatMap(i=>(i.entries||[]).map(e=>h.entitiesByKey?.[e.value]).filter(Boolean));
    if(!turns.length)turns=Object.values(h.entitiesByKey||{}).sort((a,b)=>(a.turnStartedAtMs||0)-(b.turnStartedAtMs||0));
  }
  const last=turns.at(-1)||{};
  return {id:s.id,owner,title:s.title||s.id,cwd:s.cwd,turn_id:last.turnId||null,status:last.status||null,
    runtime_status:s.threadRuntimeStatus?.type||'unknown',waiting:!!s.requests?.length||!!s.threadRuntimeStatus?.activeFlags?.length||!!s.unconfirmedTurnSubmissions?.length,
    is_child:!!s.agentNickname||s.threadSource==='subagent'||(typeof s.source==='object'&&!!s.source?.subAgent)||String(s.threadSource||'').toLowerCase().includes('subagent'),
    ephemeral:!!s.ephemeral||!!s.sideConversation,model:s.latestModel,model_provider:s.modelProvider,
    message_id:last.params?.clientUserMessageId||null,goal_status:s.threadGoal?.status||null,
    updated_at:Date.now()/1000};
}
class Bridge {
  constructor(emit) {this.emit=emit;this.compatible=true;this.buffer=Buffer.alloc(0);this.client='initializing-client';this.pending=new Map();this.states=new Map();this.followed=new Set();this.timers=new Map();}
  connect(){
    this.socket=net.connect('\\\\.\\pipe\\codex-ipc');
    this.socket.on('connect',async()=>{try{const r=await this.request('initialize',{clientType:'quota-guard'});this.client=r.result.clientId;this.emit({event:'connected'});}catch(e){this.emit({event:'error',error:e.message});this.socket.destroy();}});
    this.socket.on('error',e=>this.emit({event:'error',error:e.code||'pipe error'}));
    this.socket.on('close',()=>{this.emit({event:'disconnected'});for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(Error('disconnected'));}this.pending.clear();this.states.clear();if(require.main===module)setTimeout(()=>process.exit(0),20);});
    this.socket.on('data',d=>{try{this.buffer=Buffer.concat([this.buffer,d]);while(this.buffer.length>=4){const n=this.buffer.readUInt32LE();if(!n||n>268435456)throw Error('bad frame');if(this.buffer.length<n+4)break;const m=JSON.parse(this.buffer.subarray(4,4+n));this.buffer=this.buffer.subarray(4+n);this.receive(m);}}catch(e){this.emit({event:'error',error:'IPC frame incompatible'});this.socket.destroy();}});
  }
  write(m){const b=Buffer.from(JSON.stringify(m)),h=Buffer.alloc(4);h.writeUInt32LE(b.length);this.socket.write(Buffer.concat([h,b]));}
  request(method,params,target,timeout=15000){return new Promise((resolve,reject)=>{const id=crypto.randomUUID();const timer=setTimeout(()=>{this.pending.delete(id);reject(Error('timeout; outcome unknown'));},timeout);this.pending.set(id,{resolve,reject,timer});try{this.write({type:'request',requestId:id,sourceClientId:this.client,method,params,version:V[method]||0,...target?{targetClientId:target}:{},timeoutMs:timeout});}catch(e){clearTimeout(timer);this.pending.delete(id);reject(e);}});}
  follow(id){this.followed.add(id);this.write({type:'broadcast',method:'thread-stream-following-changed',sourceClientId:this.client,version:1,params:{conversationId:id,hostId:'local',following:true}});}
  publish(id){if(this.timers.has(id))return;this.timers.set(id,setTimeout(()=>{this.timers.delete(id);const x=this.states.get(id);if(x)this.emit({event:'thread',thread:summarize(x.state,x.owner)});},100));}
  receive(m){
    if(m.type==='response'){const p=this.pending.get(m.requestId);if(p){clearTimeout(p.timer);this.pending.delete(m.requestId);if(m.resultType==='success')p.resolve(m);else p.reject(Error(m.error||'IPC failed'));}return;}
    if(m.type==='client-discovery-request'){this.write({type:'client-discovery-response',requestId:m.requestId,response:{canHandle:false}});return;}
    if(m.type!=='broadcast')return;
    if(m.method==='client-status-changed'&&m.params?.status==='disconnected'){for(const [id,x]of this.states)if(x.owner===m.params.clientId){this.states.delete(id);this.emit({event:'unavailable',id});}return;}
    if(m.method!=='thread-stream-state-changed'||m.params?.hostId!=='local'||!this.followed.has(m.params?.conversationId))return;
    const id=m.params.conversationId,c=m.params.change;
    if(m.version!==11){this.compatible=false;this.states.clear();this.emit({event:'incompatible',id,observedVersion:m.version});return;}
    if(!this.compatible)return;
    try{
      if(c.type==='snapshot')this.states.set(id,{state:c.conversationState,revision:c.revision,owner:m.sourceClientId});
      else if(c.type==='patches'){const x=this.states.get(id);if(!x||x.owner!==m.sourceClientId||x.revision!==c.baseRevision){this.states.delete(id);this.emit({event:'unavailable',id});this.follow(id);return;}x.state=applyPatches(x.state,c.patches);x.revision=c.revision;}
      this.publish(id);
    }catch{this.states.delete(id);this.emit({event:'unavailable',id});this.follow(id);}
  }
  async command(c){
    if(c.op==='follow'){for(const id of c.threads)this.follow(id);return {};}
    if(c.op==='snapshot'){const x=this.states.get(c.thread);if(!x)throw Error('no live snapshot');return summarize(x.state,x.owner);}
    if(!['interrupt','resume','checkpoint','queue_start'].includes(c.op))throw Error('unknown command');
    if(!this.compatible)throw Error('desktop protocol incompatible');
    const x=this.states.get(c.thread);if(!x)throw Error('no live snapshot');const t=summarize(x.state,x.owner);
    const owner=await this.request('thread-owner-discovery',{hostId:'local',conversationId:c.thread});
    if(!this.compatible)throw Error('desktop protocol incompatible');
    if(owner.handledByClientId!==x.owner)throw Error('owner changed');
    const latest=this.states.get(c.thread);if(!latest||latest.owner!==x.owner)throw Error('state unavailable');
    const now=summarize(latest.state,latest.owner);
    if(now.turn_id!==c.expected_turn||now.waiting)throw Error('turn changed or waiting for user');
    if(c.op==='interrupt'){
      if(now.status!=='inProgress'||now.runtime_status!=='active')throw Error('not active');
      const r=await this.request('thread-follower-interrupt-turn',{conversationId:c.thread,mode:'user-stop',expectedTurnId:c.expected_turn},x.owner);
      return r.result;
    }
    if(c.op==='checkpoint'){
      if(now.status!=='inProgress'||now.runtime_status!=='active')throw Error('not active');
      return (await this.request('thread-follower-steer-turn',{conversationId:c.thread,input:[{type:'text',text:c.text,text_elements:[]}]},x.owner)).result;
    }
    if(c.op==='queue_start'){
      if(now.status!=='completed'||!['idle','notLoaded'].includes(now.runtime_status)||now.is_child||now.ephemeral||![null,'complete','completed'].includes(now.goal_status))throw Error('previous task not complete');
    }else if(now.status!=='interrupted'||!['idle','notLoaded'].includes(now.runtime_status))throw Error('not a paused turn');
    const request={threadId:c.thread,input:[{type:'text',text:c.text,text_elements:[]}],clientUserMessageId:c.operation_id};
    return (await this.request('thread-follower-start-turn',{conversationId:c.thread,turnStart:{request,context:{inheritThreadSettings:true}}},x.owner,30000)).result;
  }
}
if(require.main===module){
  const emit=m=>process.stdout.write(JSON.stringify(m)+'\n');const bridge=new Bridge(emit);bridge.connect();
  readline.createInterface({input:process.stdin}).on('line',async line=>{let c;try{c=JSON.parse(line);const result=await bridge.command(c);emit({id:c.id,result});}catch(e){emit({id:c?.id,error:e.message});}}).on('close',()=>{bridge.socket?.destroy();process.exit(0);});
}
module.exports={applyPatches,summarize,Bridge};
