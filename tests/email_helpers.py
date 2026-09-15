"""Drive the real email endpoints for existing workspace regression fixtures."""
def latest_code(app):
    return app.state.emails.mailer.messages[-1]['code']


async def verify_registration(client,app):
    me=await client.get('/api/beta/me')
    headers={'X-CSRF-Token':me.json()['csrf']} if me.status_code==200 else {}
    identity=client.cookies.get('mio_email_pending').split('.')[0]
    return await client.post('/api/beta/verify-email-code',headers=headers,
                             json={'pending_id':identity,'code':latest_code(app)})


async def complete_registration(client,app,name,password,**extra):
    me=await client.get('/api/beta/me')
    headers={'X-CSRF-Token':me.json()['csrf']} if me.status_code==200 else {}
    response=await client.post('/api/beta/register',headers=headers,json={'email':name.lower()+'@example.org','username':name,'password':password,'confirmation':password,**extra})
    if response.status_code!=200:return response
    verified=await verify_registration(client,app)
    if verified.json().get('status')!='verified':
        raise AssertionError('Fixture email must finish provisioning: '+str(verified.json().get('status')))
    entered=await client.get('/')
    if entered.status_code!=200: raise AssertionError('Fixture workspace must provision')
    return verified
