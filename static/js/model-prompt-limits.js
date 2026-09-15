/* Shared by both canvases. Numeric limits come only from the backend capability map. */
(function(root){
    'use strict';
    let capabilitiesPromise;
    const knownErrors = new Map();
    // Python str.strip whitespace, including characters JS trim handles differently.
    const trimPrompt = value => String(value ?? '').replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g, '');
    const length = value => Array.from(trimPrompt(value)).length;
    const number = value => Number(value).toLocaleString('en-US');
    function resolve(mapping, payload){
        const config = mapping[payload.provider_id] || {};
        const mode = (payload.reference_images || []).some(ref => ref?.url) ? 'image' : 'text';
        const model = config.routes?.[payload.model]?.[mode] || payload.model;
        const cap = config.models?.[model];
        return cap ? {...cap, model} : null;
    }
    function errorFromDetails(details){
        const error = new Error(details.message);
        error.modelPromptValidation = details;
        knownErrors.set(details.message, details);
        if(knownErrors.size > 100) knownErrors.delete(knownErrors.keys().next().value);
        return error;
    }
    function check(mapping, payload){
        const cap = resolve(mapping, payload);
        const current = length(payload.prompt);
        if(cap && current > cap.limit){
            const exceeded = current - cap.limit;
            throw errorFromDetails({code:'model_prompt_too_long', provider:payload.provider_id,
                model:cap.model, current_length:current, limit:cap.limit, exceeded,
                message:`当前模型 ${cap.label} 最多支持 ${number(cap.limit)} 字符。当前 Prompt 为 ${number(current)} 字符，超出 ${number(exceeded)} 字符，请缩短后重试。`});
        }
        return cap;
    }
    async function validate(payload){
        if(!capabilitiesPromise){
            capabilitiesPromise = fetch('/api/model-prompt-limits').then(response => {
                if(!response.ok) throw new Error('模型输入限制尚未加载，请稍后重试。');
                return response.json();
            }).catch(error => { capabilitiesPromise = null; throw error; });
        }
        return check(await capabilitiesPromise, payload);
    }
    function details(error){
        return error?.modelPromptValidation || knownErrors.get(error?.message || String(error || '')) || null;
    }
    function fromData(data, fallback='生成失败，请稍后重试。'){
        if(data?.validation?.code === 'model_prompt_too_long') return errorFromDetails(data.validation);
        return new Error(typeof data?.detail === 'string' ? data.detail : (data?.error || fallback));
    }
    async function responseError(response){
        let data;
        try { data = await response.json(); } catch(_) { return new Error('生成请求未通过校验，请检查输入后重试。'); }
        return fromData(data);
    }
    function sanitizeHistory(entry, error){
        const validation = details(error);
        if(!validation) return entry;
        Object.assign(entry, {failureKind:'validation', validation,
            error:'Prompt too long for selected model', errorMessage:validation.message,
            prompt:'', displayPrompt:'', promptHtml:'', userInstruction:'', referenceElement:null,
            references:[], refs:[], settings:{}, request:{provider:validation.provider, model:validation.model,
                current_length:validation.current_length, limit:validation.limit}});
        return entry;
    }
    root.ModelPromptLimits = {validate, check, resolve, length, details, fromData, responseError, sanitizeHistory};
})(globalThis);
