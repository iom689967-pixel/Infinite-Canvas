'use strict';
document.getElementById('instance-login').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button');
    const error = document.getElementById('login-error');
    error.textContent = '';
    button.disabled = true;
    try {
        const response = await fetch('/api/auth/login', {method: 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'}, cache: 'no-store',
            body: JSON.stringify({username: form.elements.username.value, password: form.elements.password.value})});
        form.elements.password.value = '';
        if (!response.ok) {
            error.textContent = response.status === 429 ? '尝试过于频繁，请稍后再试。' : '账号或密码不正确。';
            return;
        }
        location.replace('/');
    } catch (_) {
        form.elements.password.value = '';
        error.textContent = '无法连接实例，请稍后重试。';
    } finally { button.disabled = false; }
});
