"""Optional end-to-end check. Requires Playwright and Chromium on this machine.

Uses a temporary data directory; never writes the real technician's profile.
Run from the repository root: python3 scripts/browser_smoke.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path
from urllib.request import urlopen

from openpyxl import Workbook
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
artifacts = ROOT / '.local/validation'
artifacts.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='certicoex-browser-') as folder:
    data = Path(folder)
    shutil.copytree(ROOT / '.local/data/templates', data / 'templates')
    sign = Image.new('RGB', (600, 180), 'white')
    ImageDraw.Draw(sign).text((30, 65), 'FIRMA FICTICIA - PRUEBA', fill='#007f75', font_size=30)
    signature = data / 'firma.png'; sign.save(signature)
    workbook=Workbook();sheet=workbook.active;sheet.append(['Nombre','Apellidos','DNI'])
    sheet.append(['Ana','García López','12345678Z'])
    excel=data/'alumnos.xlsx';workbook.save(excel)
    log = (artifacts/'browser-server.log').open('w')
    process = subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8001'],cwd=ROOT,env={**os.environ,'CERTICOEX_DATA':str(data)},stdout=log,stderr=log)
    try:
        for _ in range(50):
            try:
                with urlopen('http://127.0.0.1:8001/api/health',timeout=1) as response:
                    if response.status == 200: break
            except OSError: time.sleep(.1)
        else: raise RuntimeError('No arrancó el servidor de prueba.')
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path=shutil.which('chromium'),args=['--no-sandbox'])
            context=browser.new_context(viewport={'width':1440,'height':1100},locale='es-ES',accept_downloads=True)
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8001')
            page.wait_for_function("() => document.querySelector('#template').options.length > 1")
            page.locator('#open-profile').click()
            page.locator('#technician-name').fill('Técnico de prueba')
            page.locator('#signature').set_input_files(signature)
            page.locator('#profile-form button[type=submit]').click()
            page.wait_for_function("() => !document.querySelector('#profile-dialog').open")
            page.locator('#company').fill('Empresa de prueba S. L.')
            page.locator('#cif').fill('B12345678');page.locator('#hours').fill('8');page.locator('#course-date').fill('2026-10-30')
            page.locator('#excel-file').set_input_files(excel)
            page.locator('#apply-import').click();page.wait_for_function("() => document.querySelector('#student-count').textContent === '1'")
            page.locator('#tab-paste').click()
            page.locator('#paste-text').fill('Nombre completo\tDNI/NIE\nLuis Pérez Martín\t87654321X\nAlumno a eliminar\t22222222J')
            page.locator('#parse-paste').click();page.locator('#apply-import').click()
            page.wait_for_function("() => document.querySelector('#student-count').textContent === '3'")
            page.locator('#student-rows tr').last.locator('button').click()
            page.locator('#student-rows tr').first.locator('input').first.fill('Ana García López Corregida')
            page.locator('#preview').click()
            page.wait_for_function("() => document.querySelector('#preview-dialog').open",timeout=60000)
            assert '/api/files/' in page.locator('#pdf-preview').get_attribute('src')
            page.locator('[data-close=preview-dialog]').click()
            page.locator('#generate').click()
            page.wait_for_function("() => !document.querySelector('#downloads').hidden",timeout=60000)
            for kind in ('word','pdf'):
                with page.expect_download() as result:page.locator('#download-'+kind).click()
                result.value.save_as(artifacts/('ejemplo-certificados.'+('docx' if kind=='word' else 'pdf')))
            reader=PdfReader(artifacts/'ejemplo-certificados.pdf');assert len(reader.pages)==2
            assert 'Ana García López Corregida' in reader.pages[0].extract_text()
            assert 'Luis Pérez Martín' in reader.pages[1].extract_text()
            assert 'Alumno a eliminar' not in ''.join(p.extract_text() for p in reader.pages)
            page.screenshot(path=str(artifacts/'app-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth'), 'Desbordamiento horizontal móvil'
            page.screenshot(path=str(artifacts/'app-mobile.png'),full_page=True)
            assert not errors, errors
            browser.close()
            print('PASS: perfil, firma, Excel, pegar lista, corregir/eliminar, vista previa, Word y PDF, móvil; sin errores JS.')
    finally:
        process.terminate();process.wait(timeout=10);log.close()
