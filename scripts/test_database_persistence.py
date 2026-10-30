"""Verify PostgreSQL with two app instances and a completely replaced cache disk.

Requires Docker and the dev dependencies. Uses a temporary, local PostgreSQL;
never reads or changes a production database and never prints credentials.
"""
from io import BytesIO
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
from PIL import Image
import psycopg

ROOT=Path(__file__).resolve().parents[1]
docker=['docker','--host=unix:///var/run/docker.sock']
env=os.environ.copy()
for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):
    env.pop(key,None)
env['POSTGRES_PASSWORD']=secrets.token_urlsafe(32)
name='certicoex-postgres-test-'+uuid.uuid4().hex[:10]


def run(*args,check=True):
    return subprocess.run([*docker,*args],env=env,capture_output=True,text=True,check=check)


def start_app(folder,url,logs):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    app_env={**os.environ,'CERTICOEX_DATA':str(folder),'CERTICOEX_DATABASE_URL':url,'CERTICOEX_AUTH_REQUIRED':'false','CERTICOEX_PASSWORD':'','RENDER':'false','CERTICOEX_EPHEMERAL':'true'}
    process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=app_env,stdout=logs,stderr=logs)
    client=httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=180)
    for _ in range(100):
        try:
            health=client.get('/api/health')
            if health.status_code==200:
                assert not health.json()['storage_ephemeral'] and health.json()['storage_external']
                return process,client
        except httpx.TransportError:pass
        time.sleep(.1)
    process.terminate();process.wait(timeout=10);client.close()
    raise RuntimeError('La app no pudo arrancar contra PostgreSQL.')


started=False;apps=[];clients=[]
try:
    run('run','--detach','--name',name,'--memory','256m','-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD','-e','POSTGRES_USER=certicoex_test','-e','POSTGRES_DB=certicoex_test','postgres:17-alpine')
    started=True
    address=run('port',name,'5432/tcp').stdout.strip()
    # Only the disposable database on loopback uses plain local TCP. Hosted
    # connections always override the URL to require verified TLS.
    url=f'postgresql://certicoex_test:{env["POSTGRES_PASSWORD"]}@{address}/certicoex_test?sslmode=disable'
    for _ in range(100):
        try:
            with psycopg.connect(url,connect_timeout=1):break
        except psycopg.OperationalError:time.sleep(.2)
    else:raise RuntimeError('PostgreSQL no quedó disponible.')
    with tempfile.TemporaryDirectory(prefix='certicoex-pg-') as temp:
        temp=Path(temp)
        with (temp/'app.log').open('w') as logs:
            process,client=start_app(temp/'instance-one',url,logs);apps.append(process);clients.append(client)
            course=client.get('/api/templates').json()[0]
            response=client.put('/api/templates/'+course['id']+'/settings',json={'name':'Curso persistente','hours':8});assert response.status_code==200
            b=BytesIO();Image.new('RGB',(300,100),'white').save(b,format='PNG')
            assert client.post('/api/profile',data={'name':'Técnico de prueba'},files={'signature':('firma.png',b.getvalue(),'image/png')}).status_code==200
            signature=client.get('/api/profile/signature').content
            assert client.post('/api/companies',json={'name':'Empresa persistente','cif':'B12345678'}).status_code==200
            draft=client.post('/api/drafts',json={'title':'Borrador persistente','company':'Empresa persistente','students':[{'name':'Ana','dni':''}]}).json()
            batch={'template_id':course['id'],'company':'Empresa persistente','cif':'B12345678','hours':8,'course_date':'2026-10-30','students':[{'name':'Ana García López','dni':'12345678Z'}]}
            response=client.post('/api/generate',json=batch);assert response.status_code==200,response.text
            result=response.json();assert result['pdf'],result
            word=client.get(result['word']).content;pdf=client.get(result['pdf']).content
            process.terminate();process.wait(timeout=10);apps.remove(process);client.close();clients.remove(client)
            shutil.rmtree(temp/'instance-one')
            process,client=start_app(temp/'brand-new-instance',url,logs);apps.append(process);clients.append(client)
            assert client.get('/api/profile').json()['name']=='Técnico de prueba'
            assert client.get('/api/profile/signature').content==signature
            assert client.get('/api/companies').json()[0]['name']=='Empresa persistente'
            assert client.get('/api/templates').json()[0]['name']=='Curso persistente'
            assert client.get('/api/drafts').json()[0]['id']==draft['id']
            assert client.get('/api/history',params={'q':'garcia'}).json()['total']==1
            assert client.get(result['word']).content==word
            assert client.get(result['pdf']).content==pdf
            batch['students'][0]['name']='Ana García Corregida'
            response=client.post('/api/generate',json=batch);assert response.status_code==200,response.text
            assert client.get('/api/history').json()['total']==2
            assert client.get(result['pdf']).content==pdf
            # A simultaneous, separate installation sees the same records.
            process2,client2=start_app(temp/'second-device',url,logs);apps.append(process2);clients.append(client2)
            assert client2.get('/api/drafts').json()[0]['id']==draft['id']
            assert client2.get(result['pdf']).content==pdf
            print('PASS: PostgreSQL real; reinicio con disco eliminado; empresas, perfil/firma, cursos, borradores, historial y Word/PDF intactos; segunda instancia y nueva versión conservan el original.')
finally:
    for client in clients:client.close()
    for process in apps:
        process.terminate();process.wait(timeout=10)
    if started:run('rm','--force','--volumes',name,check=False)
