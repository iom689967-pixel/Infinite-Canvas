/* Registration credentials/codes stay in the form/memory, never browser storage or URLs. */
function MioEmailVerification() {
    'use strict';
    const registration = document.querySelector('#registration-panel');
    const verification = document.querySelector('#verification-panel');
    const form = registration.querySelector('form');
    const codeForm = document.querySelector('#code-form');
    const input = document.querySelector('#email-code');
    const resend = document.querySelector('#code-resend');
    const changeEmail = document.querySelector('#code-change-email');
    const message = document.querySelector('#message');
    let pendingId = null, resendAt = 0, busy = false, operation = '', originalEmail = '', originalName = '';

    function notice(text, error = false) {
        message.className = error ? 'error' : '';
        message.textContent = text;
    }
    function controls() {
        form.querySelector('button').disabled = busy;
        codeForm.querySelector('button').disabled = busy;
        form.querySelector('button').textContent = busy && operation === 'register' ? '正在注册…' : '注册';
        codeForm.querySelector('button').textContent = busy && operation === 'verify' ? '正在验证…' : '验证并进入';
        changeEmail.disabled = busy;
        const seconds = Math.max(0, Math.ceil((resendAt - Date.now()) / 1000));
        resend.disabled = busy || seconds > 0;
        resend.textContent = busy && operation === 'resend' ? '正在发送…' : '重新发送验证码' + (seconds ? `（${seconds}s）` : '');
        registration.setAttribute('aria-busy', String(busy));
        verification.setAttribute('aria-busy', String(busy));
    }
    function showPending(state) {
        pendingId = state.pending_id;
        document.querySelector('#masked-email').textContent = state.masked_email;
        resendAt = Date.now() + (state.resend_after_seconds || 0) * 1000;
        registration.hidden = true;
        verification.hidden = false;
        input.value = '';
        input.setCustomValidity('');
        controls();
        input.focus();
    }
    async function request(path, values) {
        const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(values), cache: 'no-store'});
        const state = await response.json();
        if (!response.ok) {
            notice(state.detail || '请求未完成，请稍后重试。', true);
            return null;
        }
        return state;
    }
    async function action(kind, callback) {
        if (busy) return;
        busy = true; operation = kind; controls(); notice('');
        try { await callback(); }
        catch (_) { notice('请求未完成，请稍后重试。', true); }
        finally { busy = false; controls(); }
    }
    form.addEventListener('submit', event => {
        event.preventDefault();
        action('register', async () => {
            const values = Object.fromEntries(new FormData(form));
            originalEmail = values.email; originalName = values.username;
            let state;
            try { state = await request('/api/beta/register', values); }
            finally { form.elements.password.value = ''; form.elements.confirmation.value = ''; }
            if (state && state.verification_required) { form.reset(); showPending(state); }
        });
    });
    input.addEventListener('input', () => {
        input.value = input.value.replace(/[\s-]/g, '');
        input.setCustomValidity('');
    });
    input.addEventListener('invalid', () => input.setCustomValidity('请输入 6 位数字验证码'));
    input.addEventListener('paste', event => {
        const value = (event.clipboardData?.getData('text') || '').replace(/[\s-]/g, '');
        if (/^[0-9]{6}$/.test(value)) { event.preventDefault(); input.value = value; input.setCustomValidity(''); }
    });
    codeForm.addEventListener('submit', event => {
        event.preventDefault();
        action('verify', async () => {
            if (!/^[0-9]{6}$/.test(input.value)) { notice('请输入 6 位数字验证码', true); return; }
            const state = await request('/api/beta/verify-email-code', {pending_id: pendingId, code: input.value});
            if (state) { input.value = ''; pendingId = null; location.replace('/'); }
        });
    });
    resend.addEventListener('click', () => action('resend', async () => {
        const state = await request('/api/beta/resend-email-code', {pending_id: pendingId});
        if (state) { showPending(state); notice('新验证码已发送，请检查邮箱。'); }
        else {
            // Server deadlines remain authoritative across tabs, refreshes and network errors.
            const response = await fetch('/api/beta/email-code-pending', {cache: 'no-store'});
            if (response.ok) { const current = await response.json(); resendAt = Date.now() + current.resend_after_seconds * 1000; }
        }
    }));
    changeEmail.addEventListener('click', () => action('change', async () => {
        if (!await request('/api/beta/cancel-email-code', {pending_id: pendingId})) return;
        pendingId = null; input.value = ''; verification.hidden = true; registration.hidden = false;
        input.setCustomValidity('');
        form.elements.email.value = originalEmail; form.elements.username.value = originalName;
        form.elements.email.focus();
    }));
    const timer = setInterval(controls, 500);
    window.addEventListener('pagehide', () => clearInterval(timer), {once: true});
    // The HttpOnly pending cookie is an opaque ownership credential, never a code.
    fetch('/api/beta/email-code-pending', {cache: 'no-store'}).then(async response => {
        if (response.ok) showPending(await response.json());
    }).catch(() => {});
    controls();
}
