"""Public account pages. Tokens stay out of HTML, query strings and browser storage."""
from instance_auth import PASSWORD_MAX_LENGTH
from public_beta_store import REGISTRATION_PASSWORD_MIN_LENGTH
from pathlib import Path

STYLE='''body{margin:0;background:#f7f7f5;color:#252525;font:16px system-ui,sans-serif;display:grid;min-height:100vh;place-items:center}main{width:min(380px,85vw);padding:36px;background:white;border:1px solid #ddd;border-radius:20px}h1{font-size:28px}p{line-height:1.6;color:#666}label{display:block;margin:18px 0}input{display:block;box-sizing:border-box;width:100%;padding:12px;margin-top:8px;border:1px solid #bbb;border-radius:8px;font:inherit}button,nav a,.primary{display:inline-block;padding:12px 20px;background:#252525;color:white;border:0;border-radius:9px;font:inherit;cursor:pointer}button:disabled{opacity:.5}a{color:inherit;margin-right:12px}#message{line-height:1.6}#message.error{color:#a32c2c}[hidden]{display:none!important}'''


def page(kind,config):
    title={'home':'Mio Canvas','login':'登录 Mio Canvas','register':'注册 Mio Canvas',
           'verify-email':'验证 Mio Canvas 邮箱','resend-verification':'验证邮箱'}[kind]
    script=''
    email='<label>邮箱<input name="email" type="email" maxlength="254" autocomplete="email" required></label>'
    if kind=='home':
        registration='<a href="/register">注册</a>' if config.registration_mode!='closed' else ''
        body='<p>Public Beta · 免费使用 · API 由用户自行配置</p><nav><a href="/login">登录</a>'+registration+'</nav>'
    elif kind=='register' and config.registration_mode=='closed':
        body='<p>当前 Mio Canvas Public Beta 暂未开放注册。</p><p><a href="/login">已有账号，登录</a></p>'
    elif kind=='verify-email':
        body='<p id="verification-state">验证中…</p><p><a id="enter" class="primary" href="/login" hidden>进入 Mio Canvas</a><a href="/resend-verification">重新发送验证邮件</a></p>'
        script="""(async()=>{let token=new URLSearchParams(location.hash.slice(1)).get('token')||'';history.replaceState(null,'','/verify-email');const state=document.querySelector('#verification-state');if(!token){state.textContent='链接已失效，请重新发送验证邮件。';return;}try{const pending=fetch('/api/beta/verify-email',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});token='';const r=await pending;const d=await r.json();state.textContent=d.detail||'验证失败，请稍后重试。';document.querySelector('#enter').hidden=!['verified','used'].includes(d.status);}catch(_){state.textContent='验证请求未完成，请稍后打开原邮件链接或尝试登录。';}})();"""
    elif kind=='resend-verification':
        body='<p>邮箱验证已改为 6 位数字验证码。请返回注册页继续验证；旧链接仍在原有效期内可用。</p><p><a class="primary" href="/register">继续注册 / 验证</a><a href="/login">返回登录</a></p>'
    else:
        password_bounds=f'maxlength="{PASSWORD_MAX_LENGTH}"'
        if kind=='register': password_bounds=f'minlength="{REGISTRATION_PASSWORD_MIN_LENGTH}" '+password_bounds
        password='<label>密码<input name="password" type="password" '+password_bounds+' autocomplete="'+('new-password' if kind=='register' else 'current-password')+'" required></label>'
        if kind=='register':
            body='<p>Public Beta · 免费使用<br>API 由用户自行配置</p><p>Public Beta 当前开放少量测试名额。</p><form>'
            body+='<label>用户名 / 昵称<input name="username" minlength="3" maxlength="32" pattern="[A-Za-z0-9][A-Za-z0-9_-]{2,31}" autocomplete="nickname" required></label>'+email+password
            body+='<label>确认密码<input name="confirmation" type="password" '+password_bounds+' autocomplete="new-password" required></label>'
            if config.registration_mode=='invite':body+='<label>测试邀请码<input name="invite_code" type="password" maxlength="1024" required></label>'
            body+='<button type="submit">注册</button></form><p><a href="/login">已有账号，登录</a></p>'
        else:
            body='<form><label>邮箱 / 用户名<input name="identifier" maxlength="512" autocomplete="username" required></label>'+password+'<button type="submit">登录</button></form><p>'
            if config.registration_mode!='closed':body+='<a href="/register">注册账号</a>'
            body+='<a href="/register">继续邮箱验证</a></p>'
    if kind in {'login','register'} and not (kind=='register' and config.registration_mode=='closed'):
        endpoint={'login':'login','register':'register'}[kind]
        success="location.replace('/');" if kind=='login' else "message.textContent=(d.detail||'请求已处理。')+(d.masked_email?' '+d.masked_email:'');"
        script="""document.querySelector('form').addEventListener('submit',async e=>{e.preventDefault();const button=e.target.querySelector('button');const message=document.querySelector('#message');button.disabled=true;message.className='';try{const values=Object.fromEntries(new FormData(e.target));const r=await fetch('/api/beta/"""+endpoint+"""',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const d=await r.json();if(!r.ok){message.className='error';message.textContent=d.detail||'请求失败，请稍后重试。';return;}e.target.reset();"""+success+"""}catch(_){message.className='error';message.textContent='请求未完成，请稍后重试。';}finally{button.disabled=false;}});"""
    if kind=='register' and config.registration_mode!='closed':
        body='<section id="registration-panel">'+body+'</section>'+'''
<section id="verification-panel" hidden aria-labelledby="code-heading">
<h2 id="code-heading">验证邮箱</h2>
<p>我们已向 <strong id="masked-email"></strong> 发送了 6 位验证码</p>
<form id="code-form"><label for="email-code">邮箱验证码</label>
<input id="email-code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code"
 maxlength="6" pattern="[0-9]{6}" required aria-describedby="code-hint">
<p id="code-hint">请输入最新邮件中的验证码</p><button type="submit">验证并进入</button></form>
<p><button id="code-resend" type="button">重新发送验证码</button></p>
<button id="code-change-email" type="button">返回修改邮箱</button>
</section>'''
        script=(Path(__file__).resolve().parent/'static/js/email-verification.js').read_text()
        script+='\nMioEmailVerification();'
        style=STYLE+'#email-code{font:600 28px monospace;letter-spacing:.42em;width:100%;text-align:center;padding:16px 8px}#code-hint{font-size:13px}#code-resend,#code-change-email{background:transparent;color:#555;padding:4px 0}#code-heading{font-size:24px}'
    else: style=STYLE
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title><style>'+style+'</style></head><body><main><h1>'+title+'</h1>'+body+'<p id="message" role="status" aria-live="polite"></p></main><script>'+script+'</script></body></html>'
