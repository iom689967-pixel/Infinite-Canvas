(function(){
    function errorMessage(data, fallback=''){
        const detail = data?.detail;
        if(typeof detail === 'string' && detail.trim()) return detail;
        if(detail && typeof detail.message === 'string' && detail.message.trim()) return detail.message;
        if(typeof data?.message === 'string' && data.message.trim()) return data.message;
        return fallback;
    }

    function serverCanvas(data){
        return data?.detail?.canvas || data?.canvas || null;
    }

    async function request({canvasId, logId, deleteMedia=false, baseUpdatedAt=0}){
        const payload = {
            log_id:String(logId || ''),
            delete_unreferenced_media:Boolean(deleteMedia),
            reset_referencing_nodes:Boolean(deleteMedia),
            base_updated_at:Number(baseUpdatedAt || 0),
        };
        const response = await fetch(`/api/canvases/${encodeURIComponent(canvasId)}/logs/delete`, {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        return {response, data, payload};
    }

    window.CanvasLogCleanup = {request, errorMessage, serverCanvas};
})();
