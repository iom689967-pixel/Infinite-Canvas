/* Smart-only persistence. Transport, identity and DOM are injected by the page. */
(function(root){
'use strict';
const clone = value => JSON.parse(JSON.stringify(value));
const stable = value => JSON.stringify(value, (_, v) => v && !Array.isArray(v) && typeof v === 'object'
    ? Object.keys(v).sort().reduce((o,k)=>(o[k]=v[k],o),{}) : v);
const payload = canvas => Object.fromEntries(['title','icon','nodes','connections','viewport','logs','settings'].map(k=>[k,clone(canvas[k] ?? ({title:'',icon:'sparkles',nodes:[],connections:[],viewport:{},logs:[],settings:{}}[k]))]));
const equal = (a,b) => stable(payload(a)) === stable(payload(b));
class Coordinator {
    constructor(options){
        this.o=options; this.id=options.id; this.epoch=options.epoch || crypto.randomUUID();
        this.scope=options.scope; this.key=`smart-draft:${this.id}:${this.epoch}`;
        this.rev=0; this.ack=0; this.flight=null; this.timer=null; this.block=null;
        this.remote=null; this.sent=null; this.reserved=false; this.disposed=false; this.storageError=false;
        this.base=clone(options.server); this.version=Number(this.base.updated_at); this.draft=payload(options.server);
        this.recoveries=this.readDrafts(); if(this.recoveries.length)this.block='recovery';
        this.emit();
    }
    loaded(changed=false){if(changed)this.mark();else if(!this.dirty())this.draft=payload(this.o.capture());}
    dirty(){return this.rev!==this.ack;}
    protected(){return this.dirty() || !!this.flight || !!this.block || this.reserved;}
    state(){return {revision:this.rev,ackRevision:this.ack,version:this.version,observedVersion:Number(this.remote?.updated_at||0),
        inFlight:!!this.flight,dirty:this.dirty(),status:this.block || (this.flight||this.reserved?'saving':this.dirty()?'unsaved':'saved'),
        storageError:this.storageError,recoveries:this.recoveries.map(x=>({key:x.key,at:x.at})),hasDraft:this.dirty()};}
    emit(){this.o.onState?.(this.state());}
    readDrafts(){
        try {const out=[];for(let i=0;i<this.o.storage.length;i++){
            const key=this.o.storage.key(i);if(!key?.startsWith(`smart-draft:${this.id}:`))continue;
            try {const d=JSON.parse(this.o.storage.getItem(key));if(d.schema===1&&d.scope===this.scope&&d.id===this.id&&d.draft&&d.base)out.push({...d,key});}catch(_){this.storageError=true;}
        }return out.sort((a,b)=>b.at-a.at);}catch(_){this.storageError=true;return [];}
    }
    persist(){
        if(!this.dirty() && !this.sent && !this.externalUncertain)return;
        try {
            const body=JSON.stringify({schema:1,scope:this.scope,id:this.id,at:Date.now(),draft:this.draft,base:this.base,sent:this.sent,externalUncertain:!!this.externalUncertain},(k,v)=>{
                if(/^(api_?key|authorization|cookie|password|csrf|access_token|refresh_token)$/i.test(k))return undefined;
                if(typeof v==='string'&&/^data:/i.test(v))throw Error('binary_not_a_reference');
                return v;
            });
            // No silent eviction: exceeding the bounded browser budget leaves an explicit warning.
            if(body.length*2>2*1024*1024 || this.readDrafts().filter(d=>d.key!==this.key).length>=20)throw Error('draft_budget');
            this.o.storage.setItem(this.key,body);this.storageError=false;
        }catch(_){this.storageError=true;}
    }
    clearOwn(){try{this.o.storage.removeItem(this.key);
        if(this.restoredKey){const current=this.readDrafts().find(d=>d.key===this.restoredKey);
            if(current && stable(current)===stable(this.restoredRecord))this.o.storage.removeItem(this.restoredKey);
            this.restoredKey=null;this.restoredRecord=null;}
        this.recoveries=this.readDrafts();
    }catch(_){this.storageError=true;}}
    mark(schedule=true){
        if(this.disposed)return;
        const next=payload(this.o.capture());
        if(!equal(next,this.draft)){this.draft=next;this.rev++;this.persist();}
        clearTimeout(this.timer);this.timer=null;
        if(schedule && this.dirty()&&!this.block&&!this.flight&&!this.reserved)this.timer=setTimeout(()=>{this.timer=null;void this.flush();},this.o.debounce??450);
        this.emit();
    }
    async flush(){
        this.mark(false);
        if(this.disposed||this.block||this.reserved)return false;
        if(this.flight){await this.flight.promise;return this.dirty()&&!this.block?this.flush():!this.protected();}
        if(!this.dirty())return true;
        const ticket={epoch:this.epoch,id:this.id,revision:this.rev,snapshot:clone(this.draft),version:this.version};
        this.flight=ticket;this.sent={snapshot:ticket.snapshot,version:ticket.version};this.persist();this.emit();
        ticket.promise=this.write(ticket);
        await ticket.promise;
        if(this.dirty()&&!this.block&&!this.disposed)return this.flush();
        return !this.protected();
    }
    rejectedAuth(){
        // Called before the shared session deactivates scoped storage. Only the matching PUT response qualifies.
        if(this.flight){this.sent=null;this.block='auth';this.persist();this.emit();}
    }
    async write(ticket){
        try {
            const result=await this.o.put({...ticket.snapshot,base_updated_at:ticket.version,client_id:this.o.clientId});
            if(this.disposed||this.flight!==ticket||ticket.epoch!==this.epoch)return;
            if(result.status===409){this.remote=result.canvas||null;this.block='conflict';this.sent=null;}
            else if(result.status===200 && result.canvas?.id===this.id && Number(result.canvas.updated_at)>ticket.version && equal(result.canvas,ticket.snapshot)){
                this.base=clone(result.canvas);this.version=Number(result.canvas.updated_at);this.ack=ticket.revision;this.sent=null;
                this.o.onVersion?.(this.version);
            } else if(result.status>=400 && result.status<500 || result.maintenance){this.block=result.status===401?'auth':result.maintenance?'maintenance':'error';this.sent=null;}
            else this.block='uncertain';
        }catch(e){if(!this.disposed && this.flight===ticket){this.block=e?.code==='maintenance'?'maintenance':e?.code==='auth'?'auth':'uncertain';if(this.block!=='uncertain')this.sent=null;}}
        finally{
            if(!this.disposed && this.flight===ticket){
                this.flight=null;
                if(this.remote && Number(this.remote.updated_at)>this.version && !this.block){
                    if(this.dirty())this.block='conflict';else this.acceptRemote(this.remote);
                }
                if(this.dirty()||this.sent)this.persist();else if(!this.block)this.clearOwn();
                this.emit();
            }
        }
    }
    acceptRemote(server){
        this.base=clone(server);this.version=Number(server.updated_at);this.draft=payload(server);this.ack=this.rev;
        this.remote=null;this.o.apply(clone(server),{remote:true});this.o.onVersion?.(this.version);
    }
    observe(server){
        if(this.disposed||server?.id!==this.id||!Number.isFinite(Number(server.updated_at)))return false;
        if(Number(server.updated_at)<=this.version)return !this.dirty() && !this.flight && !this.block && Number(server.updated_at)===this.version && equal(server,this.draft);
        // Capture any draft changed by an asynchronous result before considering replacement.
        this.mark(false);this.remote=clone(server);
        if(this.flight){this.emit();return false;}
        if(this.dirty()||this.block||this.o.busy?.()){this.block=this.block||'conflict';this.persist();this.emit();return false;}
        this.acceptRemote(server);this.emit();return true;
    }
    async readCurrent(){
        const revision=this.rev, version=this.version, epoch=this.epoch, sequence=(this.readSequence||0)+1;
        this.readSequence=sequence;
        const server=await this.o.get();
        if(this.disposed||this.flight||this.reserved||revision!==this.rev||version!==this.version||epoch!==this.epoch||sequence!==this.readSequence)return null;
        if(server?.id!==this.id||!Number.isFinite(Number(server.updated_at))||Number(server.updated_at)<this.version)throw Error('invalid_version');
        return server;
    }
    async check(){
        if(this.flight||this.reserved||this.disposed)return false;
        try{
            const server=await this.readCurrent();if(!server)return false;
            this.remote=clone(server);
            if(this.externalUncertain){this.block='uncertain';this.emit();return false;}
            if(this.sent && Number(server.updated_at)>this.sent.version && equal(server,this.sent.snapshot)){
                this.base=clone(server);this.version=Number(server.updated_at);this.o.onVersion?.(this.version);
                if(equal(this.draft,this.sent.snapshot))this.ack=this.rev;
                this.sent=null;this.block=null;
                if(this.dirty())this.persist();else this.clearOwn();
            }else if(!this.sent && Number(server.updated_at)===this.version && equal(server,this.base) && !['conflict','recovery'].includes(this.block))this.block=null;
            else this.block=this.sent && Number(server.updated_at)===this.version?'uncertain':'conflict';
            this.emit();return !this.block;
        }catch(_){this.block=this.block||'error';this.emit();return false;}
    }
    async keepCurrent(){
        if(this.block!=='recovery'||this.flight||this.reserved||this.sent||this.externalUncertain)return false;
        const server=await this.readCurrent();
        if(!server)return false;
        this.remote=clone(server);
        // Explicit choice of the current branch. Older branches remain recoverable, never auto-merged.
        this.block=Number(server.updated_at)===this.version && equal(server,this.base)?null:'conflict';
        this.persist();this.emit();return !this.block;
    }
    restore(key){
        if(this.flight||this.dirty()||this.disposed)return false;
        const item=this.readDrafts().find(x=>x.key===key);if(!item)return false;
        // Keep the old branch until the newly-owned draft has actually been persisted.
        this.draft=clone(item.draft);this.base=clone(item.base);this.version=Number(item.base.updated_at);
        this.rev++;this.sent=item.sent||null;this.externalUncertain=!!item.externalUncertain;this.block=this.sent||this.externalUncertain?'uncertain':'conflict';
        this.o.apply({...clone(this.base),...clone(this.draft),id:this.id});this.o.onVersion?.(this.version);
        this.persist();this.restoredKey=key;this.restoredRecord=item;this.emit();return true;
    }
    async resolveRestored(){
        if(!this.restoredKey)return false;
        if(this.sent||this.externalUncertain)return this.check();
        try{const server=await this.readCurrent();if(!server)return false;this.remote=server;
            if(server.id===this.id && Number(server.updated_at)===this.version && equal(server,this.base)){this.block=null;this.emit();return true;}
            this.block='conflict';this.emit();return false;
        }catch(_){this.block='error';this.emit();return false;}
    }
    async discard(){
        if(this.flight||this.reserved||this.sent||this.externalUncertain||this.o.busy?.())return false;
        const server=await this.readCurrent();if(!server)return false;
        this.acceptRemote(server);this.block=null;this.clearOwn();
        // Only an explicitly discarded or fully acknowledged adopted branch is removed.
        if(this.restoredKey){this.o.storage.removeItem(this.restoredKey);this.restoredKey=null;}
        this.recoveries=this.readDrafts();if(this.recoveries.length)this.block='recovery';this.emit();return true;
    }
    async externalWrite(action){
        if(!await this.flush() || this.protected())throw Error('请先处理未保存修改');
        this.reserved=true;this.emit();
        try{return await action();}catch(error){this.block='uncertain';this.externalUncertain=true;this.persist();throw error;}finally{this.reserved=false;this.emit();if(this.dirty()&&!this.block)void this.flush();}
    }
    dispose(){this.mark(false);this.disposed=true;clearTimeout(this.timer);}
}
const api={Coordinator,payload,equal};root.SmartSave=api;
if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
