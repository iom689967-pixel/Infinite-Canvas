"""Drive the real email endpoints for existing workspace regression fixtures."""
from urllib.parse import urlsplit,parse_qs


def latest_token(app):
    return parse_qs(urlsplit(app.state.emails.mailer.messages[-1]['link']).fragment)['token'][0]


async def complete_registration(client,app,name,password,**extra):
    response=await client.post('/api/beta/register',json={'email':name.lower()+'@example.org','username':name,'password':password,'confirmation':password,**extra})
    if response.status_code!=200:return response
    verified=await client.post('/api/beta/verify-email',json={'token':latest_token(app)})
    if verified.json().get('status')!='verified':
        raise AssertionError('Fixture email must finish provisioning: '+str(verified.json().get('status')))
    return await client.post('/api/beta/login',json={'identifier':name.lower()+'@example.org','password':password})
