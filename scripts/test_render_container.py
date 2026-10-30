"""Smoke-test the built Render image with a random, temporary password.

Requires Docker, httpx, Pillow and pypdf. No credentials are saved or printed.
Run: .venv/bin/python scripts/test_render_container.py
"""
from io import BytesIO
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid

import httpx
from PIL import Image, ImageDraw
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
docker = ['docker', '--host=unix:///var/run/docker.sock']
docker_env = os.environ.copy()
for selector in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):
    docker_env.pop(selector, None)
docker_env['CERTICOEX_PASSWORD'] = secrets.token_urlsafe(32)
name = 'certicoex-render-test-' + uuid.uuid4().hex[:10]
process_started = False


def command(*args, check=True):
    return subprocess.run([*docker,*args], env=docker_env, text=True, capture_output=True, check=check)


try:
    command('run','--detach','--name',name,'--memory','512m','--cpus','1',
            '-p','127.0.0.1::10000','-e','CERTICOEX_PASSWORD','-e','CERTICOEX_EPHEMERAL=true','certicoex:render')
    process_started=True
    published=command('port',name,'10000/tcp').stdout.strip()
    base='http://' + published
    with httpx.Client(base_url=base, timeout=180) as client:
        for _ in range(100):
            try:
                health=client.get('/api/health')
                if health.status_code==200:break
            except httpx.TransportError:
                pass
            time.sleep(.1)
        else:
            raise AssertionError('El contenedor no arrancó.')
        assert health.json()['pdf_available'],health.text
        assert health.json()['storage_ephemeral']
        assert client.get('/').status_code==401
        assert client.get('/api/templates').status_code==401
        auth=('tecnico',docker_env['CERTICOEX_PASSWORD'])
        assert client.get('/',auth=auth).status_code==200
        templates=client.get('/api/templates',auth=auth).json()
        assert len(templates)==1,templates
        image=Image.new('RGB',(600,180),'white')
        ImageDraw.Draw(image).text((40,60),'FIRMA DE PRUEBA',fill='black',font_size=30)
        b=BytesIO();image.save(b,format='PNG')
        response=client.post('/api/profile',auth=auth,data={'name':'Técnico de prueba'},files={'signature':('firma.png',b.getvalue(),'image/png')})
        assert response.status_code==200,response.text
        response=client.post('/api/generate',auth=auth,json={
            'template_id':templates[0]['id'],'company':'Empresa de prueba S. L.','cif':'B12345678',
            'hours':8,'course_date':'2026-10-30',
            'students':[{'name':'Ana García López','dni':'12345678Z'}, {'name':'Luis Pérez Martín','dni':'87654321X'}],
        })
        assert response.status_code==200,response.text
        result=response.json();assert result['pdf'],result
        assert client.get(result['word']).status_code==401
        assert client.get(result['pdf']).status_code==401
        word=client.get(result['word'],auth=auth);pdf=client.get(result['pdf'],auth=auth)
        assert word.status_code==pdf.status_code==200
        reader=PdfReader(BytesIO(pdf.content));assert len(reader.pages)==2
        for i,student in enumerate(('Ana García López','Luis Pérez Martín')):
            text=reader.pages[i].extract_text()
            assert student in text
            assert '1. INTRODUCCIÓN' in text
            assert '9. SEGURIDAD VIAL.' in text
        out=ROOT/'.local/render-validation';out.mkdir(parents=True,exist_ok=True)
        (out/'certificados.pdf').write_bytes(pdf.content)
        (out/'certificados.docx').write_bytes(word.content)
    assert command('exec',name,'id','-u').stdout.strip()=='10001'
    print('PASS: imagen Docker, memoria limitada a 512 MB, usuario sin privilegios, acceso privado y Word/PDF de 2 alumnos.')
except Exception:
    if process_started:
        print(command('logs','--tail','30',name,check=False).stdout)
    raise
finally:
    if process_started:
        command('rm','--force',name,check=False)
