(function(){
    'use strict';

    const SHOW_DELAY = 180;
    const HIDE_DELAY = 100;
    const VIEWPORT_PADDING = 12;
    const ANCHOR_GAP = 12;
    const MAX_IMAGE_WIDTH = 404;
    const MAX_IMAGE_HEIGHT = 444;
    const THUMBNAIL_CONTAINER_SELECTOR = [
        '.input-thumb',
        '.smart-node-input-thumb',
        '.thumb-item',
        '.smart-group-single-thumb',
        '.input-item',
        '.rh-param-thumb',
        '.output-img-wrap',
        '.minimax-ref-slot.filled',
        '.minimax-ref-clip',
        '.minimax-material-card',
        '.compare-thumb',
        '.ref-thumb',
        '[data-image-hover-preview]'
    ].join(',');
    const INTERACTIVE_SELECTOR = 'button, a, input, textarea, select, [contenteditable="true"], .mini-x, .input-thumb-remove, [data-minimax-ref-thumb-delete]';
    const BUSY_BODY_CLASSES = [
        'canvas-node-drag',
        'canvas-node-resize',
        'canvas-board-pan',
        'canvas-selecting',
        'canvas-zoom-preview',
        'canvas-minimax-pane-resize',
        'smart-node-drag',
        'smart-node-resize',
        'smart-composer-resize',
        'smart-llm-instr-resize',
        'smart-prompt-split-resize'
    ];

    let overlay = null;
    let previewImage = null;
    let statusText = null;
    let activeAnchor = null;
    let activeSource = '';
    let activeFallback = '';
    let showTimer = 0;
    let hideTimer = 0;
    let loadToken = 0;
    let positionFrame = 0;
    let anchorSnapshot = null;

    function ensureOverlay(){
        if(overlay?.isConnected) return overlay;
        overlay = document.createElement('div');
        overlay.className = 'image-hover-preview';
        overlay.setAttribute('role', 'img');
        overlay.setAttribute('aria-label', '图片放大预览');
        overlay.setAttribute('aria-hidden', 'true');
        overlay.innerHTML = '<div class="image-hover-preview-status"><span>图片加载失败</span></div><img alt="图片放大预览" draggable="false">';
        document.body.appendChild(overlay);
        previewImage = overlay.querySelector('img');
        statusText = overlay.querySelector('.image-hover-preview-status span');
        return overlay;
    }

    function clearTimer(name){
        if(name === 'show' && showTimer){ clearTimeout(showTimer); showTimer = 0; }
        if(name === 'hide' && hideTimer){ clearTimeout(hideTimer); hideTimer = 0; }
    }

    function isImagePreviewCandidate(img){
        if(!img || img.tagName !== 'IMG' || img.dataset.previewKind === 'video') return false;
        if(img.closest('.image-hover-preview, .canvas-asset-card, .asset-item')) return false;
        return Boolean(img.closest(THUMBNAIL_CONTAINER_SELECTOR) || img.closest('.log-thumbs'));
    }

    function resolveThumbnail(target){
        if(!(target instanceof Element) || target.closest(INTERACTIVE_SELECTOR)) return null;
        let img = target.closest('img');
        if(!isImagePreviewCandidate(img)){
            const container = target.closest(THUMBNAIL_CONTAINER_SELECTOR);
            img = container?.querySelector?.('img:not([data-preview-kind="video"])') || null;
        }
        if(!isImagePreviewCandidate(img)) return null;
        return {anchor:img, img};
    }

    function sourceForImage(img){
        const owner = img.closest('[data-original-src], [data-original-url], [data-preview-original], [data-source-url], [data-url], [data-image-hover-preview]');
        const original = img.dataset.originalSrc
            || img.dataset.originalUrl
            || img.dataset.previewOriginal
            || img.dataset.imageHoverPreview
            || owner?.dataset.previewOriginal
            || owner?.dataset.imageHoverPreview
            || owner?.dataset.sourceUrl
            || owner?.dataset.originalSrc
            || owner?.dataset.originalUrl
            || img.dataset.url
            || owner?.dataset.url
            || img.currentSrc
            || img.src
            || '';
        let resolved = String(original || '');
        try {
            if(/^asset:\/\//i.test(resolved) && typeof window.displayMediaUrl === 'function'){
                resolved = window.displayMediaUrl({url:resolved}) || resolved;
            }
        } catch(_) {}
        const fallback = img.dataset.previewSrc || img.dataset.thumbnailUrl || owner?.dataset.thumbnailUrl || img.currentSrc || img.src || '';
        return {source:resolved || fallback, fallback};
    }

    function interactionIsBusy(){
        if(BUSY_BODY_CLASSES.some(name => document.body.classList.contains(name))) return true;
        const shell = document.getElementById('shell');
        if(shell?.classList.contains('port-dragging') || shell?.classList.contains('panning') || shell?.classList.contains('selecting')) return true;
        if(document.querySelector('.prompt-editor-modal.open, .image-edit-modal.open, .log-modal.open, .shortcut-modal.open, .workflow-transfer-panel.open, .error-modal.open, dialog[open], [role="dialog"][open]')) return true;
        return Boolean(document.querySelector(
            'path.link.temp, .magnetic-port-overlay .floating-connection-port, .input-thumb.dragging, [data-dragging="1"]'
        ));
    }

    function rectChanged(rect){
        if(!anchorSnapshot) return true;
        return ['left','top','right','bottom','width','height'].some(key => Math.abs(Number(rect[key]) - Number(anchorSnapshot[key])) > .5);
    }

    function rememberRect(rect){
        anchorSnapshot = {left:rect.left, top:rect.top, right:rect.right, bottom:rect.bottom, width:rect.width, height:rect.height};
    }

    function positionOverlay(){
        if(!overlay?.classList.contains('is-visible') || !activeAnchor?.isConnected) return;
        const anchorRect = activeAnchor.getBoundingClientRect();
        const previewRect = overlay.getBoundingClientRect();
        const width = Math.min(previewRect.width, window.innerWidth - VIEWPORT_PADDING * 2);
        const height = Math.min(previewRect.height, window.innerHeight - VIEWPORT_PADDING * 2);
        let left = anchorRect.left;
        let top = anchorRect.top - height - ANCHOR_GAP;
        if(top < VIEWPORT_PADDING) top = anchorRect.bottom + ANCHOR_GAP;
        if(top + height > window.innerHeight - VIEWPORT_PADDING){
            const above = anchorRect.top - height - ANCHOR_GAP;
            top = above >= VIEWPORT_PADDING ? above : Math.max(VIEWPORT_PADDING, window.innerHeight - VIEWPORT_PADDING - height);
        }
        left = Math.min(left, window.innerWidth - VIEWPORT_PADDING - width);
        left = Math.max(VIEWPORT_PADDING, left);
        overlay.style.left = `${Math.round(left)}px`;
        overlay.style.top = `${Math.round(top)}px`;
        rememberRect(anchorRect);
    }

    function sizeLoadedImage(){
        const naturalWidth = Math.max(1, previewImage.naturalWidth || 1);
        const naturalHeight = Math.max(1, previewImage.naturalHeight || 1);
        const availableWidth = Math.max(120, Math.min(MAX_IMAGE_WIDTH, window.innerWidth - VIEWPORT_PADDING * 2 - 16));
        const availableHeight = Math.max(100, Math.min(MAX_IMAGE_HEIGHT, window.innerHeight - VIEWPORT_PADDING * 2 - 16));
        const scale = Math.min(availableWidth / naturalWidth, availableHeight / naturalHeight);
        const width = Math.max(1, Math.round(naturalWidth * scale));
        const height = Math.max(1, Math.round(naturalHeight * scale));
        previewImage.style.width = `${width}px`;
        previewImage.style.height = `${height}px`;
        overlay.style.width = `${width + 16}px`;
        overlay.style.height = `${height + 16}px`;
    }

    function beginPositionWatch(){
        cancelAnimationFrame(positionFrame);
        const watch = () => {
            positionFrame = 0;
            if(!overlay?.classList.contains('is-visible') || !activeAnchor?.isConnected) return;
            if(interactionIsBusy()){ hideNow(); return; }
            const rect = activeAnchor.getBoundingClientRect();
            if(rectChanged(rect)) positionOverlay();
            positionFrame = requestAnimationFrame(watch);
        };
        positionFrame = requestAnimationFrame(watch);
    }

    function loadSource(source, fallback){
        const token = ++loadToken;
        overlay.classList.remove('is-loaded', 'is-error');
        overlay.classList.add('is-loading');
        if(statusText) statusText.textContent = '图片加载失败';
        previewImage.removeAttribute('style');
        overlay.style.width = '340px';
        overlay.style.height = '260px';
        let fallbackTried = false;
        previewImage.onload = () => {
            if(token !== loadToken) return;
            sizeLoadedImage();
            overlay.classList.remove('is-loading', 'is-error');
            overlay.classList.add('is-loaded');
            positionOverlay();
        };
        previewImage.onerror = () => {
            if(token !== loadToken) return;
            if(!fallbackTried && fallback && fallback !== previewImage.getAttribute('src')){
                fallbackTried = true;
                previewImage.src = fallback;
                return;
            }
            overlay.classList.remove('is-loading', 'is-loaded');
            overlay.classList.add('is-error');
            positionOverlay();
        };
        previewImage.src = source;
    }

    function showResolved(resolved){
        if(!resolved?.anchor?.isConnected || interactionIsBusy()) return;
        const {source, fallback} = sourceForImage(resolved.img);
        if(!source) return;
        ensureOverlay();
        const sourceChanged = source !== activeSource || fallback !== activeFallback;
        activeAnchor = resolved.anchor;
        activeSource = source;
        activeFallback = fallback;
        anchorSnapshot = null;
        overlay.classList.add('is-visible');
        overlay.setAttribute('aria-hidden', 'false');
        if(sourceChanged || previewImage.getAttribute('src') !== source) loadSource(source, fallback);
        positionOverlay();
        beginPositionWatch();
    }

    function scheduleShow(resolved){
        clearTimer('hide');
        clearTimer('show');
        if(!resolved || interactionIsBusy()) return;
        if(overlay?.classList.contains('is-visible')){
            showResolved(resolved);
            return;
        }
        showTimer = window.setTimeout(() => {
            showTimer = 0;
            showResolved(resolved);
        }, SHOW_DELAY);
    }

    function hideNow(){
        clearTimer('show');
        clearTimer('hide');
        cancelAnimationFrame(positionFrame);
        positionFrame = 0;
        loadToken++;
        if(overlay){
            overlay.classList.remove('is-visible', 'is-loading', 'is-loaded', 'is-error');
            overlay.setAttribute('aria-hidden', 'true');
        }
        activeAnchor = null;
        activeSource = '';
        activeFallback = '';
        anchorSnapshot = null;
    }

    function scheduleHide(){
        clearTimer('show');
        clearTimer('hide');
        hideTimer = window.setTimeout(hideNow, HIDE_DELAY);
    }

    document.addEventListener('pointerover', event => {
        const resolved = resolveThumbnail(event.target);
        if(!resolved) return;
        if(event.relatedTarget instanceof Node && resolved.anchor.contains(event.relatedTarget)) return;
        scheduleShow(resolved);
    }, true);

    document.addEventListener('pointerout', event => {
        if(!activeAnchor && !showTimer) return;
        const leaving = resolveThumbnail(event.target);
        if(!leaving) return;
        if(event.relatedTarget instanceof Node && leaving.anchor.contains(event.relatedTarget)) return;
        const entering = resolveThumbnail(event.relatedTarget);
        if(entering){ scheduleShow(entering); return; }
        scheduleHide();
    }, true);

    document.addEventListener('pointerdown', hideNow, true);
    document.addEventListener('pointercancel', hideNow, true);
    document.addEventListener('dragstart', hideNow, true);
    document.addEventListener('contextmenu', hideNow, true);
    document.addEventListener('wheel', hideNow, {capture:true, passive:true});
    document.addEventListener('keydown', event => { if(event.key === 'Escape') hideNow(); }, true);
    window.addEventListener('blur', hideNow);
    document.addEventListener('visibilitychange', () => { if(document.hidden) hideNow(); });
    window.addEventListener('resize', () => {
        if(!overlay?.classList.contains('is-visible')) return;
        if(previewImage?.complete && previewImage.naturalWidth) sizeLoadedImage();
        positionOverlay();
    });
    window.addEventListener('scroll', () => {
        if(overlay?.classList.contains('is-visible')) positionOverlay();
    }, true);

    window.ImageHoverPreview = Object.freeze({
        showDelay:SHOW_DELAY,
        hideDelay:HIDE_DELAY,
        maxWidth:420,
        maxHeight:460,
        hide:hideNow,
        showForElement(element){
            const resolved = resolveThumbnail(element);
            if(resolved) showResolved(resolved);
            return Boolean(resolved);
        },
        state(){
            return {
                visible:Boolean(overlay?.classList.contains('is-visible')),
                source:activeSource,
                anchor:activeAnchor,
                overlay
            };
        }
    });
})();
