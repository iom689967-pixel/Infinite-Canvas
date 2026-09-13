/* Reconnect transport only; never repeats application writes or generation calls. */
(function (global) {
    'use strict';
    global.MioWorkspaceSocket = function (url) {
        let socket = null, timer = null, stopped = false, attempt = 0;
        const client = {
            onopen: null, onmessage: null,
            get readyState() { return socket ? socket.readyState : WebSocket.CLOSED; },
            send(value) { if (socket && socket.readyState === WebSocket.OPEN) socket.send(value); },
            close() {
                stopped = true;
                clearTimeout(timer); timer = null;
                if (socket) socket.close();
                global.removeEventListener('online', online);
            }
        };
        function retry() {
            if (stopped || timer !== null) return;
            timer = setTimeout(() => { timer = null; connect(); }, Math.min(8000, 500 * (2 ** attempt++)));
        }
        function connect() {
            if (stopped) return;
            try { socket = new WebSocket(url); } catch (_) { retry(); return; }
            socket.onopen = event => { attempt = 0; if (client.onopen) client.onopen(event); };
            socket.onmessage = event => { if (client.onmessage) client.onmessage(event); };
            socket.onclose = event => {
                // Explicit auth rejection is final; only network/restart disconnects retry.
                if (event.code === 1008) client.close(); else retry();
            };
            socket.onerror = () => { if (socket) socket.close(); };
        }
        function online() {
            if (stopped || (socket && socket.readyState <= WebSocket.OPEN)) return;
            clearTimeout(timer); timer = null; connect();
        }
        global.addEventListener('online', online);
        global.addEventListener('pagehide', () => client.close(), {once: true});
        connect();
        return client;
    };
})(window);
