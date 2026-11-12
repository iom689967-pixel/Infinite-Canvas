/* Kie controls consume the existing server capability schema; no UI route IDs. */
(function(root){
    'use strict';
    const cache = new Map();
    const pending = new Map();
    const key = (provider, model) => `${provider}:${model}`;
    const field = (schema, name) => (schema?.fields || []).find(item => item.key === name);
    const values = item => (item?.options || []).map(option => String(option.value));
    const isKie = provider => provider?.id === 'kie' || provider?.protocol === 'kie';
    const peek = (provider, model) => cache.get(key(provider, model)) || null;
    async function load(provider, model){
        const id = key(provider, model);
        if(cache.has(id)) return cache.get(id);
        if(pending.has(id)) return pending.get(id);
        const request = (async () => {
            const response = await fetch(`/api/image-params?provider_id=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`);
            if(!response.ok) throw new Error('Kie 模型参数加载失败，请重试');
            const schema = await response.json();
            if(!field(schema, 'aspect_ratio') || !field(schema, 'resolution')) throw new Error('Kie 模型参数不完整');
            cache.set(id, schema);
            return schema;
        })();
        pending.set(id, request);
        try { return await request; } finally { pending.delete(id); }
    }
    function normalize(schema, input){
        const output = {...input, resolution:String(input.resolution || '').toUpperCase()};
        for(const name of ['aspect_ratio','resolution','output_format']){
            const item = field(schema, name);
            if(!item){ output[name] = ''; continue; }
            if(!values(item).includes(output[name])) output[name] = item.default || values(item)[0];
        }
        const exclusions = field(schema, 'resolution')?.aspect_ratio_exclusions || {};
        if((exclusions[output.resolution] || []).includes(output.aspect_ratio)) output.resolution = field(schema, 'resolution').default;
        return output;
    }
    function validate(schema, input, count=0){
        for(const name of ['aspect_ratio','resolution','output_format']){
            const item = field(schema, name);
            if(item && !values(item).includes(input[name])) throw new Error(`${schema.label} 的${item.label}无效，请重新选择`);
        }
        const exclusions = field(schema, 'resolution')?.aspect_ratio_exclusions || {};
        if((exclusions[input.resolution] || []).includes(input.aspect_ratio)) throw new Error(`${input.aspect_ratio} 仅支持 1K，请调整后再生成`);
        if(count > Number(schema.reference_image_limit)) throw new Error(`${schema.label} 最多支持 ${schema.reference_image_limit} 张参考图`);
        return input;
    }
    function syncSelects(schema, input, selects){
        for(const name of ['aspect_ratio','resolution','output_format']){
            const select = selects[name];
            if(!select) continue;
            const item = field(schema, name);
            select.hidden = !item;
            if(!item) continue;
            const exclusions = field(schema, 'resolution')?.aspect_ratio_exclusions || {};
            select.replaceChildren(...item.options.map(choice => {
                const option = document.createElement('option');
                option.value = choice.value;
                option.textContent = choice.label;
                option.disabled = name === 'aspect_ratio' ? (exclusions[input.resolution] || []).includes(choice.value)
                    : name === 'resolution' ? (exclusions[choice.value] || []).includes(input.aspect_ratio) : false;
                return option;
            }));
            select.value = input[name];
            select.disabled = false;
        }
    }
    root.KieImageParams = {isKie, load, peek, field, normalize, validate, syncSelects};
})(typeof window === 'undefined' ? globalThis : window);
