"""Exercise department accounts in real browsers, with temporary fictitious data."""
from io import BytesIO
import json
import re
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from docx import Document
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, expect
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'test-only-department-password-123'
BASE = 'http://127.0.0.1:8003'


def sign(path, color):
    image = Image.new('RGBA', (450, 100), color)
    ImageDraw.Draw(image).text((15, 30), 'FIRMA FICTICIA', fill='white', font_size=28)
    image.save(path)


def login(page, username, password=PASSWORD):
    page.goto(BASE)
    expect(page.locator('#login-form')).to_be_visible()
    page.locator('#login-username').fill(username)
    page.locator('#login-password').fill(password)
    page.locator('#login-submit').click()
    expect(page.locator('#account-username')).to_have_text(username)
    expect(page.locator('#template option').nth(1)).to_be_attached()


def profile(page, name, signature, title='TÉCNICO EN PRL', salutation='D.'):
    page.locator('#open-profile').click()
    page.locator('#technician-name').fill(name)
    page.locator('#technician-title').fill(title)
    page.locator('#technician-salutation').select_option(salutation)
    page.locator('#signature').set_input_files(signature)
    page.locator('#profile-form button[type=submit]').click()
    expect(page.locator('#profile-dialog')).not_to_be_visible()


def account_action(page, target):
    if page.locator('#account-menu').get_attribute('open') is None:
        page.locator('#account-menu > summary').click()
    page.locator('#'+target).click()


def use_company(page):
    page.locator('#open-companies').click()
    expect(page.locator('#companies-dialog')).to_be_visible()
    card = page.locator('#companies-list .saved-training').filter(has=page.locator('h3', has_text='Empresa compartida de prueba'))
    if card.count():
        card.get_by_role('button', name='Usar empresa').click()
    else:
        page.locator('#company-name').fill('Empresa compartida de prueba')
        page.locator('#company-cif').fill('B12345678')
        page.locator('#save-company').click()
    expect(page.locator('#companies-dialog')).not_to_be_visible()
    expect(page.locator('#company-search')).to_have_value('Empresa compartida de prueba')
    expect(page.locator('#company-cif-label')).to_have_text('B12345678')


def emit(page):
    page.locator('#template').select_option(label='Plantilla compartida adaptable')
    use_company(page)
    page.locator('#course-date').fill('2026-11-06')
    page.locator('#hours').fill('8')
    page.locator('#paste-text').fill('12345678Z GARCIA LOPEZ, ANA')
    page.locator('#parse-paste').click(); page.locator('#apply-import').click()
    page.locator('#generate').click()
    expect(page.locator('#download-pdf')).to_be_visible(timeout=30000)
    return page.locator('#download-word').get_attribute('href'), page.locator('#download-pdf').get_attribute('href')


with tempfile.TemporaryDirectory(prefix='certicoex-accounts-browser-') as directory:
    root = Path(directory); data = root / 'data'; data.mkdir()
    sign(data / 'signature.png', '#508060')
    (data / 'profile.json').write_text(json.dumps({'name': 'Administrador de prueba'}))
    blue = root / 'blue.png'; red = root / 'red.png'; sign(blue, '#1858c0'); sign(red, '#c02838')
    with (root / 'server.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8003'], cwd=ROOT,
                                   env={**os.environ, 'CERTICOEX_DATA':str(data), 'CERTICOEX_AUTH_REQUIRED':'true', 'CERTICOEX_MULTIUSER':'true', 'CERTICOEX_USERNAME':'tecnico', 'CERTICOEX_PASSWORD':PASSWORD, 'RENDER':''}, stdout=log, stderr=log)
        try:
            for _ in range(80):
                try:
                    with urlopen(BASE + '/api/health', timeout=1) as response:
                        if response.status == 200: break
                except OSError: time.sleep(.1)
            else: raise RuntimeError('Test server did not start')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox'])
                admin_context = browser.new_context(viewport={'width':1440,'height':1100})
                a_context = browser.new_context(viewport={'width':1440,'height':1100})
                b_context = browser.new_context(viewport={'width':1440,'height':1100})
                owner = admin_context.new_page(); a = a_context.new_page(); b = b_context.new_page()
                errors = []
                for page in (owner, a, b): page.on('pageerror', lambda error: errors.append(str(error)))
                login(owner, 'tecnico')
                template = Document()
                for text in ('CERTIFICADO DE PRUEBA', '{NOMBRE} · {DNI/NIE}',
                             '{EMPRESA} · {CIF} · {HORAS} · {FECHA}', '{FIRMA}',
                             '{CARGO_TECNICO}', '{TRATAMIENTO_TECNICO} {TECNICO}'):
                    template.add_paragraph(text)
                source = BytesIO(); template.save(source)
                uploaded = admin_context.request.post(BASE + '/api/templates', multipart={
                    'name': 'Plantilla compartida adaptable',
                    'file': {'name': 'plantilla.docx', 'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'buffer': source.getvalue()}})
                assert uploaded.status == 200
                template_id = uploaded.json()['id']
                settings = admin_context.request.put(BASE + f'/api/templates/{template_id}/settings', data={'name':'Plantilla compartida adaptable','hours':8,'expected_pages':1})
                assert settings.status == 200
                modified = Document(BytesIO(source.getvalue())); modified.add_paragraph('WORD ACTUALIZADO DE PRUEBA')
                modified.add_page_break(); modified.add_paragraph('')
                replacement = BytesIO(); modified.save(replacement)
                owner.locator('#open-templates').click()
                template_card = owner.locator('#templates-list .saved-training').filter(has=owner.locator('h3',has_text='Plantilla compartida adaptable'))
                template_card.get_by_role('button',name='Sustituir Word',exact=True).click()
                expect(owner.locator('#replace-template-dialog')).to_be_visible()
                owner.locator('#replace-template-file').set_input_files({'name':'revision.docx','mimeType':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','buffer':replacement.getvalue()})
                owner.locator('#replace-template-save').click()
                expect(owner.locator('#templates-dialog')).to_be_visible()
                expect(template_card).to_contain_text('revision.docx')
                expect(template_card.get_by_role('button',name='Recuperar anterior')).to_be_visible()
                saved = admin_context.request.get(BASE + '/api/templates').json()
                updated = next(t for t in saved if t['id']==template_id)
                assert updated['hours']==8 and updated['expected_pages']==1 and updated['version']==1
                assert admin_context.request.get(BASE + f'/api/templates/{template_id}/file').body()==replacement.getvalue()
                owner.once('dialog',lambda dialog:dialog.accept())
                template_card.get_by_role('button',name='Recuperar anterior').click()
                expect(template_card).to_contain_text('plantilla.docx')
                assert admin_context.request.get(BASE + f'/api/templates/{template_id}/file').body()==source.getvalue()
                owner.locator('[data-close=templates-dialog]').click()
                assert owner.locator('.brand-logo').evaluate('(image) => image.complete && image.naturalWidth > 0')
                assert owner.locator('link[rel=icon]').get_attribute('href').startswith('/logo.png')
                owner.locator('#open-profile').click()
                expect(owner.locator('#technician-name')).to_have_value('Administrador de prueba')
                expect(owner.locator('#signature-preview')).to_be_visible()
                owner.locator('[data-close=profile-dialog]').click()
                account_action(owner, 'open-users')
                for username in ('tecnico2', 'tecnico3', 'tecnico4'):
                    owner.locator('#new-username').fill(username)
                    owner.locator('#initial-password').fill(PASSWORD)
                    owner.locator('#create-user').click()
                    expect(owner.locator('#users-list')).to_contain_text(username)
                expect(owner.locator('#users-list .saved-training')).to_have_count(4)
                owner.locator('[data-close=users-dialog]').click()
                login(a, 'tecnico2'); login(b, 'tecnico3')
                a.locator('#hours').fill('5')
                a.locator('#open-templates').click()
                expect(a.locator('#templates-dialog')).to_be_visible()
                expect(a.locator('#hours')).to_have_value('5')
                expect(a.locator('#templates-list').get_by_role('button', name='Eliminar', exact=True)).to_have_count(0)
                expect(a.locator('#templates-list').get_by_role('button', name='Sustituir Word', exact=True)).to_have_count(0)
                expect(a.locator('#templates-list').get_by_role('button', name='Recuperar anterior', exact=True)).to_have_count(0)
                card = a.locator('#templates-list .saved-training').filter(has=a.locator('h3', has_text='Plantilla compartida adaptable'))
                expect(card).to_contain_text('Archivo Word: plantilla.docx')
                with a.expect_download() as saved:
                    card.get_by_role('link', name='Descargar Word').click()
                downloaded = saved.value
                assert downloaded.suggested_filename == 'plantilla.docx'
                assert Path(downloaded.path()).read_bytes() == source.getvalue()
                card.get_by_role('button', name='Usar plantilla').click()
                expect(a.locator('#templates-dialog')).not_to_be_visible()
                expect(a.locator('#template option:checked')).to_have_text('Plantilla compartida adaptable')
                expect(a.locator('#open-users')).to_be_hidden()
                expect(a.locator('#archive-tools')).to_be_hidden()
                assert a_context.request.get(BASE + '/api/users').status == 403
                profile(a, 'Técnico Alfa', blue)
                profile(b, 'Técnico Beta', red, 'TÉCNICA SUPERIOR EN PRL', 'Dña.')
                a.reload(); expect(a.locator('#template option').nth(1)).to_be_attached()
                a.locator('#open-profile').click()
                expect(a.locator('#technician-name')).to_have_value('Técnico Alfa')
                expect(a.locator('#technician-title')).to_have_value('TÉCNICO EN PRL')
                expect(a.locator('#technician-salutation')).to_have_value('D.')
                a.locator('[data-close=profile-dialog]').click()
                b.reload(); expect(b.locator('#template option').nth(1)).to_be_attached()
                b.locator('#open-profile').click()
                expect(b.locator('#technician-title')).to_have_value('TÉCNICA SUPERIOR EN PRL')
                expect(b.locator('#technician-salutation')).to_have_value('Dña.')
                b.locator('[data-close=profile-dialog]').click()
                a_word, a_pdf = emit(a)
                b.reload(); expect(b.locator('#template option').nth(1)).to_be_attached()
                expect(b.locator('#saved-company')).to_contain_text('Empresa compartida de prueba')
                b_word, b_pdf = emit(b)
                for context, word, pdf, own_name, other_name, title, salutation in (
                        (a_context,a_word,a_pdf,'Técnico Alfa','Técnico Beta','TÉCNICO EN PRL','D.'),
                        (b_context,b_word,b_pdf,'Técnico Beta','Técnico Alfa','TÉCNICA SUPERIOR EN PRL','Dña.')):
                    response = context.request.get(BASE + word)
                    assert response.status == 200
                    text = '\n'.join(p.text for p in Document(BytesIO(response.body())).paragraphs)
                    assert own_name in text and other_name not in text
                    assert title in text and f'{salutation} {own_name}' in text and '{' not in text
                    pdf_text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(context.request.get(BASE + pdf).body())).pages)
                    assert title in pdf_text and f'{salutation} {own_name}' in pdf_text
                assert b_context.request.get(BASE + a_pdf).status == 404
                b_context.request.delete(BASE + a_word.rsplit('/',1)[0])
                assert a_context.request.get(BASE + a_pdf).status == 200
                account_action(owner, 'open-users')
                card = owner.locator('#users-list .saved-training').filter(has=owner.locator('h3',has_text='tecnico2'))
                owner.once('dialog', lambda dialog: dialog.accept())
                card.get_by_role('button',name='Desactivar',exact=True).click()
                expect(card).to_contain_text('Desactivado')
                a.reload(); expect(a.locator('#login-form')).to_be_visible()
                owner.once('dialog', lambda dialog: dialog.accept())
                card.get_by_role('button',name='Activar',exact=True).click()
                expect(card).to_contain_text('Activo')
                card.get_by_role('button',name='Restablecer contraseña').click()
                owner.locator('#reset-password').fill('reset-test-only-password-456')
                owner.locator('#reset-password-save').click()
                expect(owner.locator('#reset-password-dialog')).not_to_be_visible()
                login(a, 'tecnico2', 'reset-test-only-password-456')
                a.locator('#open-profile').click()
                expect(a.locator('#technician-name')).to_have_value('Técnico Alfa')
                a.locator('[data-close=profile-dialog]').click()
                account_action(a, 'open-account')
                a.locator('#current-password').fill('reset-test-only-password-456')
                a.locator('#new-password').fill('own-test-only-password-789')
                a.locator('#repeat-password').fill('own-test-only-password-789')
                a.locator('#password-save').click()
                expect(a.locator('#password-dialog')).not_to_be_visible()
                a_word, a_pdf = emit(a)
                a.locator('#preview').click(); b.locator('#preview').click()
                for page, name in ((a,'Técnico Alfa'), (b,'Técnico Beta')):
                    expect(page.locator('#preview-dialog')).to_be_visible(timeout=30000)
                    word = page.locator('#preview-word').get_attribute('href')
                    response = page.context.request.get(BASE + word)
                    assert name in '\n'.join(p.text for p in Document(BytesIO(response.body())).paragraphs)
                    page.locator('[data-close=preview-dialog]').click()
                b.once('dialog',lambda dialog: dialog.accept()); account_action(b, 'logout')
                expect(b.locator('#login-form')).to_be_visible()
                assert a_context.request.get(BASE + a_pdf).status == 200
                login(b,'tecnico3')
                assert b_context.request.get(BASE + b_pdf).status == 404
                other_tab = a_context.new_page(); other_tab.goto(BASE)
                expect(other_tab.locator('#account-username')).to_have_text('tecnico2')
                a.once('dialog',lambda dialog: dialog.accept()); account_action(a, 'logout')
                expect(a.locator('#login-form')).to_be_visible()
                expect(other_tab.locator('#login-form')).to_be_visible()
                login(a,'tecnico2','own-test-only-password-789')
                # The compact workspace keeps company edits separate until saved.
                a.set_viewport_size({'width':1440,'height':900})
                use_company(a)
                a.locator('#open-companies').click()
                a.locator('#company-filter').fill('B12345678')
                a.locator('#companies-list').get_by_role('button', name='Editar', exact=True).click()
                a.locator('#company-name').fill('Cambio sin guardar')
                a.locator('[data-close=companies-dialog]').click()
                expect(a.locator('#company-search')).to_have_value('Empresa compartida de prueba')
                a.locator('#open-companies').click()
                a.locator('#company-filter').fill('B12345678')
                a.locator('#companies-list').get_by_role('button', name='Editar', exact=True).click()
                a.locator('#company-cif').fill('B12345679')
                a.locator('#save-company').click()
                expect(a.locator('#companies-dialog')).not_to_be_visible()
                a.locator('#company-search').fill('B12345679')
                a.locator('#company-search').press('Enter')
                expect(a.locator('#company-cif-label')).to_have_text('B12345679')
                expect(a.locator('#company-search')).to_have_value('Empresa compartida de prueba')
                a.locator('#template').select_option(label='Plantilla compartida adaptable')
                a.locator('#hours').fill('8'); a.locator('#course-date').fill('2026-11-06')
                # Editing another course in its dialog preserves the active formation.
                a.locator('#open-templates').click()
                other_course = a.locator('#templates-list .saved-training').filter(has=a.locator('h3',has_text='Manipulado, envasado y control de maquinaria'))
                other_course.get_by_role('button',name='Editar curso').click()
                a.locator('#default-hours').fill('12')
                a.locator('#course-settings-form button').last.click()
                expect(a.locator('#templates-dialog')).to_be_visible()
                a.locator('[data-close=templates-dialog]').click()
                expect(a.locator('#template option:checked')).to_have_text('Plantilla compartida adaptable')
                expect(a.locator('#hours')).to_have_value('8')
                for name, dni in (('Ana de Prueba','00000000T'),('Luis de Ejemplo','00000001R'),('Marta de Prueba','00000002W'),('Pedro de Ejemplo','00000003B')):
                    a.locator('#add-student').click()
                    a.locator('#student-rows tr').last.locator('input').first.fill(name)
                    a.locator('#student-rows tr').last.locator('input').last.fill(dni)
                expect(a.locator('#student-rows .row-status').last).to_have_text('Revisar DNI')
                expect(a.locator('#review-summary')).to_contain_text('1 pendiente')
                a.locator('#review-summary').click()
                expect(a.locator('#student-rows tr').last.locator('input').last).to_be_focused()
                for width, height in ((1440,900),(1366,768)):
                    a.set_viewport_size({'width':width,'height':height})
                    a.evaluate('window.scrollTo(0,0)')
                    assert a.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    formation = a.locator('.formation-card').bounding_box(); pupils = a.locator('.student-card').bounding_box()
                    assert abs(formation['y'] - pupils['y']) < 2 and formation['x'] < pupils['x']
                    bar = a.locator('.action-bar').bounding_box()
                    assert bar['y'] >= max(formation['y']+formation['height'],pupils['y']+pupils['height']), 'The action bar must not cover the cards'
                    button = a.locator('#generate').bounding_box()
                    assert button['y'] >= 0 and button['y']+button['height'] <= height, (width,height,button)
                    a.screenshot(path=str(ROOT.parent / 'work' / 'compact-ui' / f'desktop-{width}.png'),full_page=True)
                a.locator('#student-rows tr').last.locator('input').last.fill('00000003A')
                expect(a.locator('#review-summary')).to_contain_text('sin avisos')
                a.locator('#paste-text').fill('12345678Z Persona de Prueba')
                a.locator('#parse-paste').click()
                expect(a.locator('#review-summary')).to_contain_text('pendiente de añadir')
                a.locator('#review-summary').click()
                expect(a.locator('#apply-import')).to_be_focused()
                a.locator('#cancel-import').click()
                expect(a.locator('#student-rows tr')).to_have_count(4)
                expect(a.locator('#import-inputs')).to_be_visible()
                # A Word with a trailing blank page produces actionable layout notices.
                meta = next(t for t in admin_context.request.get(BASE + '/api/templates').json() if t['id']==template_id)
                replaced = admin_context.request.put(BASE + f'/api/templates/{template_id}/file', multipart={'version':str(meta['version']),'file':{'name':'salto.docx','mimeType':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','buffer':replacement.getvalue()}})
                assert replaced.status == 200
                a.locator('#preview').click()
                expect(a.locator('#preview-dialog')).to_be_visible(timeout=30000)
                expect(a.locator('#preview-layout-report')).to_contain_text('Posibles páginas vacías')
                a.locator('#preview-layout-report').get_by_role('button',name='Ver página 2',exact=True).click()
                expect(a.locator('#pdf-preview')).to_have_attribute('src',re.compile('#page=2$'))
                a.screenshot(path=str(ROOT.parent / 'work' / 'layout-preview.png'),full_page=True)
                a.locator('[data-close=preview-dialog]').click()
                a.locator('#generate').click()
                expect(a.locator('#download-pdf')).to_be_visible(timeout=30000)
                expect(a.locator('#layout-report')).to_contain_text('Posibles páginas vacías')
                a.locator('#layout-report').get_by_role('button',name='Ver página 2',exact=True).click()
                expect(a.locator('#preview-dialog')).to_be_visible()
                a.locator('[data-close=preview-dialog]').click()
                restored = admin_context.request.post(BASE + f'/api/templates/{template_id}/restore',data={'version':replaced.json()['version']})
                assert restored.status == 200
                a.locator('#generate').click()
                expect(a.locator('#download-pdf')).to_be_visible(timeout=30000)
                expect(a.locator('#layout-report')).to_be_hidden()
                a.set_viewport_size({'width':390,'height':844})
                a.screenshot(path=str(ROOT.parent / 'work' / 'compact-ui' / 'mobile.png'),full_page=True)
                a.set_viewport_size({'width':390,'height':1000})
                a.locator('#open-templates').click()
                expect(a.locator('#templates-dialog')).to_be_visible()
                assert a.locator('#templates-dialog').evaluate('(d) => d.scrollWidth <= d.clientWidth')
                a.screenshot(path=str(ROOT.parent / 'work' / 'templates-mobile.png'), full_page=True)
                a.locator('[data-close=templates-dialog]').click()
                for page in (a, owner):
                    for width in (390,768,1440):
                        page.set_viewport_size({'width':width,'height':1000})
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}'
                with sqlite3.connect(data / 'certicoex.sqlite3') as db:
                    assert db.execute("SELECT count(*) FROM records WHERE kind IN ('history','draft','preview')").fetchone()[0] == 0
                    assert db.execute("SELECT count(*) FROM assets WHERE id LIKE 'jobs/%'").fetchone()[0] == 0
                assert not errors, errors
                owner.locator('[data-close=users-dialog]').click()
                owner.screenshot(path=str(ROOT.parent / 'work' / 'accounts-desktop.png'), full_page=True)
                a.set_viewport_size({'width':390,'height':1000})
                a.screenshot(path=str(ROOT.parent / 'work' / 'accounts-mobile.png'), full_page=True)
                # Only the administrator can delete; cancel preserves the Word.
                owner.locator('#open-templates').click()
                card = owner.locator('#templates-list .saved-training').filter(has=owner.locator('h3', has_text='Plantilla compartida adaptable'))
                card.get_by_role('button', name='Usar plantilla').click()
                owner.locator('#open-templates').click()
                owner.once('dialog', lambda dialog: dialog.dismiss())
                card.get_by_role('button', name='Eliminar', exact=True).click()
                expect(card).to_be_visible()
                template_id = uploaded.json()['id']
                assert admin_context.request.get(BASE + f'/api/templates/{template_id}/file').status == 200
                owner.once('dialog', lambda dialog: dialog.accept())
                card.get_by_role('button', name='Eliminar', exact=True).click()
                expect(card).to_have_count(0)
                expect(owner.locator('#template')).to_have_value('')
                expect(owner.locator('#hours')).to_have_value('')
                assert admin_context.request.get(BASE + f'/api/templates/{template_id}/file').status == 404
                owner.locator('#templates-list').get_by_role('button', name='Usar plantilla').click()
                owner.locator('#open-templates').click()
                owner.once('dialog', lambda dialog: dialog.accept())
                owner.locator('#templates-list').get_by_role('button', name='Eliminar', exact=True).click()
                expect(owner.locator('#templates-list')).to_have_text('Todavía no hay plantillas guardadas.')
                expect(owner.locator('#template option')).to_have_count(1)
                owner.locator('[data-close=templates-dialog]').click()
                a.reload()
                a.locator('#open-templates').click()
                expect(a.locator('#templates-list')).to_have_text('Todavía no hay plantillas guardadas.')
                browser.close()
            print('PASS: four accounts, owner migration, separate profiles/signatures and certificates, shared companies/templates, download isolation, deactivation, reset, self password change, logout across tabs, mobile, no worker archives or JS errors.')
        finally:
            process.terminate(); process.wait(timeout=10)
