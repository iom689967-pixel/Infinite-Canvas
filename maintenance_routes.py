"""Behaviour audit shared by the native gate and the first-upgrade Caddy barrier.

Unknown HTTP routes fail closed during draining. HTTP verbs alone never grant access.
"""
import re

CONTROL = {'GET /healthz', 'GET /__mio/health', 'GET /login', 'GET /register',
           'GET /verify-email', 'GET /resend-verification', 'GET /workspace',
           'GET /api/auth/me', 'POST /api/auth/logout', 'GET /api/beta/me',
           'POST /api/beta/login', 'POST /api/beta/logout', 'GET /api/beta/email-code-pending',
           'POST /api/beta/enter', 'POST /__mio/handoff', 'POST /api/beta/cancel-email-code'}
READ = {
    'GET /', 'GET /api/config', 'GET /api/providers', 'GET /api/models',
    'GET /api/instance/provider-settings', 'GET /api/instance/providers', 'GET /api/instance/storage',
    'GET /api/storage-files', 'GET /api/storage-files/{kind}/{rel_path:path}',
    'GET /api/asset-classification-prompt', 'GET /api/media-preview', 'GET /api/image-jpeg',
    'GET /api/local-assets', 'GET /api/runninghub/workflows',
    'GET /api/runninghub/workflows/{workflow_id:path}', 'GET /api/image-params',
    'GET /api/conversations', 'GET /api/conversations/{conversation_id}',
    'GET /api/canvases', 'GET /api/projects', 'GET /api/canvases/trash',
    'GET /api/canvases/{canvas_id}', 'GET /api/canvases/{canvas_id}/meta',
    'GET /api/canvas-assets', 'GET /api/smart-canvas/prompt-templates',
    'GET /api/asset-library', 'GET /api/prompt-libraries', 'GET /api/history', 'GET /api/queue_status',
    'GET /api/shared-folders', 'GET /api/shared-folders/{folder_id}/tree',
    'GET /api/shared-folders/{folder_id}/file',
}
RECOVERY = {
    'GET /api/canvas-image-tasks/{task_id}', 'POST /api/canvas-image-tasks/{task_id}/refresh',
    'DELETE /api/canvas-image-tasks/{task_id}', 'POST /api/canvas-llm/cancel',
    'GET /api/midjourney/tasks/{task_id}', 'GET /api/runninghub/query',
    'POST /api/angle/poll_status', 'GET /api/download-output',
    'POST /api/asset-library/items/{item_id}/avatar-status',
}
LOCAL_WRITE = {
    'POST /api/canvases', 'PUT /api/canvases/{canvas_id}', 'DELETE /api/canvases/{canvas_id}',
    'POST /api/canvases/{canvas_id}/meta', 'POST /api/canvases/{canvas_id}/touch',
    'POST /api/canvases/{canvas_id}/logs/delete', 'POST /api/canvases/{canvas_id}/restore',
    'POST /api/projects', 'POST /api/projects/{project_id}', 'DELETE /api/projects/{project_id}',
    'POST /api/conversations', 'DELETE /api/conversations/{conversation_id}',
    'PUT /api/instance/provider-settings', 'PUT /api/instance/providers', 'DELETE /api/instance/providers',
}
NEW_MODEL = {
    'POST '+path for path in ('/api/online-image', '/api/midjourney/submit', '/api/midjourney/actions',
    '/api/midjourney/modal', '/api/canvas-image-tasks', '/api/canvas-video-tasks', '/api/canvas-video',
    '/api/canvas-llm', '/api/chat', '/api/chat/agent', '/api/chat/stream', '/api/angle/generate',
    '/generate', '/api/generate', '/api/ms/generate', '/api/runninghub/submit',
    '/api/runninghub/workflow-submit', '/api/local-assets/caption', '/api/local-assets/classify',
    '/api/asset-library/items/classify', '/api/providers/test-connection', '/api/providers/probe-async',
    '/api/instance/provider-settings/test-connection', '/api/instance/provider-settings/probe-async')}
NEW_UPLOAD = {'POST '+path for path in (
    '/api/ai/upload', '/api/ai/upload-base64', '/api/local-assets/upload', '/api/local-assets/import-urls',
    '/api/temp-sh/upload', '/api/cloud-video/upload', '/api/runninghub/upload-asset',
    '/api/asset-library/items/{item_id}/register-avatar', '/api/canvas-assets/download')}
NEW_REGISTRATION = {'POST '+path for path in ('/api/beta/register', '/api/beta/verify-email',
    '/api/beta/verify-email-code', '/api/beta/resend-verification', '/api/beta/resend-email-code')}


def path_regex(template):
    pieces = re.split(r'(\{[^}]+\})', template)
    return '^' + ''.join('.*' if p.endswith(':path}') else '[^/]+' if p.startswith('{')
                        else re.escape(p) for p in pieces) + '$'


GROUPS = [(name, [(key.split(' ', 1)[0], re.compile(path_regex(key.split(' ', 1)[1])))
                 for key in keys]) for name, keys in (
    ('control', CONTROL), ('read', READ), ('recovery', RECOVERY), ('local_write', LOCAL_WRITE),
    ('model', NEW_MODEL), ('upload', NEW_UPLOAD), ('registration', NEW_REGISTRATION))]

MODEL_PHASES = {
    '/api/canvas-llm': 'llm', '/api/chat': 'llm', '/api/chat/stream': 'llm_stream',
    '/api/chat/agent': 'agent', '/api/local-assets/caption': 'caption',
    '/api/local-assets/classify': 'classification', '/api/asset-library/items/classify': 'classification',
    '/api/canvas-video': 'video', '/api/canvas-video-tasks': 'video',
}


def classify(method, path):
    if method in {'GET', 'HEAD'} and path.startswith('/static/'):
        return 'control'
    if method in {'GET', 'HEAD'} and path.startswith(('/assets/', '/output/')):
        return 'read'
    method = 'GET' if method == 'HEAD' else method
    for name, routes in GROUPS:
        if any(verb == method and regex.fullmatch(path) for verb, regex in routes):
            return MODEL_PHASES.get(path, 'provider_probe' if '/provider' in path else 'image') if name == 'model' else name
    return 'unreviewed'


def caddy_allowed(sealed=False):
    """JSON match sets: OR across routes; AND method/path within each matcher."""
    keys = CONTROL if sealed else CONTROL | READ | RECOVERY | LOCAL_WRITE
    result = []
    for method in sorted({key.split(' ', 1)[0] for key in keys}):
        paths = [key.split(' ', 1)[1] for key in sorted(keys) if key.startswith(method+' ')]
        result.append({'method': [method, 'HEAD'] if method == 'GET' else [method],
                       'path_regexp': {'pattern': '(?:'+'|'.join(path_regex(path) for path in paths)+')'}})
    result.append({'method': ['GET', 'HEAD'], 'path': ['/static/*']})
    if not sealed:
        result.append({'method': ['GET', 'HEAD'], 'path': ['/assets/*', '/output/*']})
    return result
