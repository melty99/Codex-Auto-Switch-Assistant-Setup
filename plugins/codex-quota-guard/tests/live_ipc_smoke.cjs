// Opt-in test: real local IPC router, synthetic owner and state, no actual user chat.
// Does not start inference, create a Codex chat, or send mutations to real chat owners.
const net=require('node:net'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {Bridge}=require('../scripts/desktop_bridge.cjs');
const tid='quota-guard-test-'+crypto.randomUUID(),turn='test-turn';
const owner=net.connect('\\\\.\\pipe\\codex-ipc');let id='initializing-client',buf=Buffer.alloc(0),follower,revision=0,calls=[];
let state={id:tid,title:'Isolated quota guard transport test',cwd:'C:/test',requests:[],source:'test',threadSource:'user',modelProvider:'openai',threadRuntimeStatus:{type:'active',activeFlags:[]},turns:[{turnId:turn,status:'inProgress'}]};
function send(m){let b=Buffer.from(JSON.stringify(m)),h=Buffer.alloc(4);h.writeUInt32LE(b.length);owner.write(Buffer.concat([h,b]));}
function snapshot(){if(follower)send({type:'broadcast',method:'thread-stream-state-changed',sourceClientId:id,targetClientIds:[follower],version:11,params:{conversationId:tid,hostId:'local',change:{type:'snapshot',revision:++revision,conversationState:state}}});}
let ready;const initialized=new Promise(r=>ready=r);
owner.on('connect',()=>send({type:'request',requestId:'init',sourceClientId:id,method:'initialize',version:0,params:{clientType:'quota-guard-test'}}));
owner.on('data',d=>{buf=Buffer.concat([buf,d]);while(buf.length>=4&&buf.length>=4+buf.readUInt32LE()){let n=buf.readUInt32LE(),m=JSON.parse(buf.subarray(4,n+4));buf=buf.subarray(n+4);
 if(m.type==='response'&&m.requestId==='init'){id=m.result.clientId;ready();}
 if(m.type==='client-discovery-request')send({type:'client-discovery-response',requestId:m.requestId,response:{canHandle:m.request?.params?.conversationId===tid}});
 if(m.type==='broadcast'&&m.method==='thread-stream-following-changed'&&m.params?.conversationId===tid){follower=m.sourceClientId;snapshot();}
 if(m.type==='request'&&m.params?.conversationId===tid){let result={};
  if(m.method==='thread-owner-discovery')result={supportsUntrustedAppInput:true};
  else if(m.method==='thread-follower-interrupt-turn'){assert.equal(m.version,4);assert.equal(m.params.expectedTurnId,turn);calls.push('interrupt');state.turns[0].status='interrupted';state.threadRuntimeStatus.type='idle';result={ok:true,interruptedTurnId:turn};snapshot();}
  else if(m.method==='thread-follower-start-turn'){assert.equal(m.version,2);assert.equal(m.params.turnStart.context.inheritThreadSettings,true);calls.push('resume');state.turns.push({turnId:'next',status:'inProgress'});state.threadRuntimeStatus.type='active';result={result:{turn:{id:'next'}}};snapshot();}
  send({type:'response',requestId:m.requestId,resultType:'success',method:m.method,handledByClientId:id,result});
 }
}});
owner.on('error',e=>{console.error(e.code);process.exit(1);});
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const bridge=new Bridge(()=>{});
(async()=>{try{await initialized;bridge.connect();for(let i=0;i<50&&bridge.client==='initializing-client';i++)await delay(100);bridge.follow(tid);for(let i=0;i<50&&!bridge.states.has(tid);i++)await delay(100);
 const s=await bridge.command({op:'snapshot',thread:tid});assert.equal(s.status,'inProgress');
 await bridge.command({op:'interrupt',thread:tid,expected_turn:turn});await delay(150);
 assert.equal((await bridge.command({op:'snapshot',thread:tid})).status,'interrupted');
 await bridge.command({op:'resume',thread:tid,expected_turn:turn,operation_id:crypto.randomUUID(),text:'isolated test'});await delay(150);
 await assert.rejects(()=>bridge.command({op:'resume',thread:tid,expected_turn:turn,text:'duplicate'}));
 assert.deepEqual(calls,['interrupt','resume']);console.log('PASS: live IPC framing, owner discovery, pause, resume, duplicate rejection; synthetic owner only.');
}catch(e){console.error(e);process.exitCode=1;}finally{bridge.socket?.destroy();owner.destroy();setTimeout(()=>process.exit(process.exitCode||0),100);}})();
setTimeout(()=>{console.error('Test timed out');process.exit(1);},15000).unref();
