"""Authenticated context for the shared frontend; no credentials or CSRF in HTML."""
import hashlib
import json
import re


def frontend_context(paths, principal):
    if not principal:
        raise RuntimeError('Authenticated frontend requires a principal')
    namespace = hashlib.sha256(('\0'.join((str(paths.data_root.resolve()), paths.instance_id,
                                          principal['username']))).encode()).hexdigest()[:32]
    return {'username': principal['username'], 'instance_id': paths.instance_id,
            'public_beta': getattr(paths, 'public_beta', False),
            'storage_namespace': namespace,
            'capabilities': {'manage_own_providers': 'manage_own_providers' in principal.get('permissions', []),
                             'system_admin': False, 'local_generation': False,
                             'legacy_online_generation': False}}


def authenticated_html(html, paths, principal):
    if 'id="instance-context"' in html:
        return html
    context = json.dumps(frontend_context(paths, principal), ensure_ascii=True).replace('<', '\\u003c')
    # Remove old, late injection. Initialization must precede every inline consumer.
    html = re.sub(r'<script\s+src=["\']/static/js/instance-session\.js[^"\']*["\']\s*></script>', '', html)
    bootstrap = ('<script type="application/json" id="instance-context">'+context+'</script>'
                 '<script src="/static/js/instance-storage.js"></script>'
                 '<script src="/static/js/instance-session.js"></script>'
                 '<script src="/static/js/instance-ui.js" defer></script>'
                 '<link rel="stylesheet" href="/static/css/instance-ui.css">')
    return re.sub(r'<head(?:\s[^>]*)?>', lambda m: m.group(0)+bootstrap, html, count=1, flags=re.I)
