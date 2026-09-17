/* Pure selection contracts shared by both canvases. No identity or network IO. */
(function(root){
    'use strict';
    const categories={llm:'chat_models',image:'image_models',video:'video_models'};
    function reason(providers,providerId,model,purpose,loadError=''){
        if(loadError)return '模型目录暂时加载失败，请刷新配置；已保留原选择';
        if(!providerId || !model)return '尚未选择 Provider 和模型';
        const p=(providers||[]).find(p=>p.id===providerId);
        if(!p)return '原 Provider 已不存在，请主动重新选择';
        if(p.enabled===false)return '原 Provider 已停用，请主动重新选择';
        if(!(p[categories[purpose]]||[]).includes(model))return '原模型或用途已不存在，请主动重新选择';
        const cap=p.capabilities?.[purpose]?.[model];
        if(!cap)return '模型能力尚未加载，请刷新配置；已保留原选择';
        return cap.executable ? '' : (cap.reason || '该模型用途目前不可执行');
    }
    function assertAvailable(providers,id,model,purpose,loadError=''){
        const message=reason(providers,id,model,purpose,loadError);
        if(message){const error=new Error(message);error.code='model_selection_invalid';throw error;}
    }
    function assertPrompt(providers,id,model,prompt,referenceCount=0){
        const cap=(providers||[]).find(p=>p.id===id)?.capabilities?.image?.[model];
        const rule=cap?.prompt_limits?.[referenceCount ? 'image' : 'text'];
        const count=Array.from(String(prompt || '')).length;
        if(rule?.limit && count>rule.limit){
            const error=new Error(`当前模型最多支持 ${rule.limit} 字符，最终 Prompt 为 ${count} 字符；请缩短后重试`);
            error.code='model_prompt_too_long';throw error;
        }
    }
    function errorMessage(detail,fallback='请求未完成，请查看事件编号或检查配置'){
        if(detail && typeof detail==='object' && typeof detail.message==='string')return detail.message+(detail.event_id ? ` · 事件 ${detail.event_id}` : '');
        return typeof detail==='string' ? detail : fallback;
    }
    function preserve(value,defaultValue){return value || defaultValue || '';}
    root.PersonalModelSelection=Object.freeze({reason,assertAvailable,assertPrompt,errorMessage,preserve});
})(typeof module==='object'?module.exports:window);
