// 把后端的错误响应整理成给人看的一句话。
// 后端错误通常是 {"detail": "...", "errors": [...]}；只取 detail，不把整段响应文本
// （可能包含校验错误明细、HTML 错误页或很长的内容）放进界面提示和画布日志。
(function(root){
    const LOG_ERROR_MAX_CHARS = 2000;
    const TEXT_ERROR_MAX_CHARS = 500;

    function truncate(text, max){
        const value = String(text ?? '');
        return value.length > max ? `${value.slice(0, max)}…（已截断，共 ${value.length} 个字符）` : value;
    }

    // data 是已解析的 JSON 响应体
    function message(data, fallback='请求失败'){
        if(!data) return fallback;
        if(typeof data === 'string') return data || fallback;
        const detail = data.detail ?? data.error ?? data.message;
        if(typeof detail === 'string') return detail || fallback;
        if(Array.isArray(detail)){
            const messages = detail.map(item => {
                if(typeof item === 'string') return item;
                const loc = Array.isArray(item?.loc) ? item.loc.filter(x => x !== 'body').join('.') : '';
                const msg = item?.msg || item?.message || JSON.stringify(item);
                return loc ? `${loc}: ${msg}` : msg;
            }).filter(Boolean);
            return messages.join('\n') || fallback;
        }
        if(detail && typeof detail === 'object') return detail.message || detail.msg || JSON.stringify(detail);
        return fallback;
    }

    // text 是原始响应文本：是 JSON 就只取 detail，否则截断
    function fromText(text, fallback='请求失败'){
        const raw = String(text ?? '').trim();
        if(!raw) return fallback;
        if(raw.startsWith('{') || raw.startsWith('[')){
            try { return message(JSON.parse(raw), fallback); } catch(_) { /* 不是 JSON，按普通文本处理 */ }
        }
        if(/^<(!doctype|html)/i.test(raw)) return fallback;
        return truncate(raw, TEXT_ERROR_MAX_CHARS);
    }

    async function fromResponse(response, fallback='请求失败'){
        let text = '';
        try { text = await response.text(); } catch(_) { return fallback; }
        return fromText(text, fallback);
    }

    // 写进画布日志的错误文本：接受 Error、字符串或任意值
    function forLog(error){
        if(error === undefined || error === null || error === '') return '';
        const text = typeof error === 'string' ? error : (error?.message ?? String(error));
        return truncate(fromText(text, text), LOG_ERROR_MAX_CHARS);
    }

    root.ApiErrors = {message, fromText, fromResponse, forLog, LOG_ERROR_MAX_CHARS, TEXT_ERROR_MAX_CHARS};
    if(typeof module !== 'undefined' && module.exports) module.exports = root.ApiErrors;
})(globalThis);
