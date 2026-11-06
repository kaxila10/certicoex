"""Exercise department accounts in real browsers, with temporary fictitious data."""
from io import BytesIO
import json
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
    page.wait_for_function("document.querySelector('#template').options.length > 1")


def profile(page, name, signature):
    page.locator('#open-profile').click()
    page.locator('#technician-name').fill(name)
    page.locator('#signature').set_input_files(signature)
    page.locator('#profile-form button[type=submit]').click()
    expect(page.locator('#profile-dialog')).not_to_be_visible()


def emit(page):
    page.locator('#company').fill('Empresa compartida de prueba')
    page.locator('#cif').fill('B12345678')
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
                assert owner.locator('.brand-logo').evaluate('(image) => image.complete && image.naturalWidth > 0')
                assert owner.locator('link[rel=icon]').get_attribute('href').startswith('/logo.png')
                owner.locator('#open-profile').click()
                expect(owner.locator('#technician-name')).to_have_value('Administrador de prueba')
                expect(owner.locator('#signature-preview')).to_be_visible()
                owner.locator('[data-close=profile-dialog]').click()
                owner.locator('#open-users').click()
                for username in ('tecnico2', 'tecnico3', 'tecnico4'):
                    owner.locator('#new-username').fill(username)
                    owner.locator('#initial-password').fill(PASSWORD)
                    owner.locator('#create-user').click()
                    expect(owner.locator('#users-list')).to_contain_text(username)
                expect(owner.locator('#users-list .saved-training')).to_have_count(4)
                owner.locator('[data-close=users-dialog]').click()
                login(a, 'tecnico2'); login(b, 'tecnico3')
                expect(a.locator('#open-users')).to_be_hidden()
                expect(a.locator('#archive-tools')).to_be_hidden()
                assert a_context.request.get(BASE + '/api/users').status == 403
                profile(a, 'Técnico Alfa', blue); profile(b, 'Técnico Beta', red)
                a.reload(); a.wait_for_function("document.querySelector('#template').options.length > 1")
                a.locator('#open-profile').click()
                expect(a.locator('#technician-name')).to_have_value('Técnico Alfa')
                a.locator('[data-close=profile-dialog]').click()
                a_word, a_pdf = emit(a)
                b.reload(); b.wait_for_function("document.querySelector('#template').options.length > 1")
                expect(b.locator('#saved-company')).to_contain_text('Empresa compartida de prueba')
                b_word, b_pdf = emit(b)
                for context, word, own_name, other_name in ((a_context,a_word,'Técnico Alfa','Técnico Beta'),(b_context,b_word,'Técnico Beta','Técnico Alfa')):
                    response = context.request.get(BASE + word)
                    assert response.status == 200
                    text = '\n'.join(p.text for p in Document(BytesIO(response.body())).paragraphs)
                    assert own_name in text and other_name not in text
                assert b_context.request.get(BASE + a_pdf).status == 404
                b_context.request.delete(BASE + a_word.rsplit('/',1)[0])
                assert a_context.request.get(BASE + a_pdf).status == 200
                owner.locator('#open-users').click()
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
                a.locator('#open-account').click()
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
                b.once('dialog',lambda dialog: dialog.accept()); b.locator('#logout').click()
                expect(b.locator('#login-form')).to_be_visible()
                assert a_context.request.get(BASE + a_pdf).status == 200
                login(b,'tecnico3')
                assert b_context.request.get(BASE + b_pdf).status == 404
                other_tab = a_context.new_page(); other_tab.goto(BASE)
                expect(other_tab.locator('#account-username')).to_have_text('tecnico2')
                a.once('dialog',lambda dialog: dialog.accept()); a.locator('#logout').click()
                expect(a.locator('#login-form')).to_be_visible()
                expect(other_tab.locator('#login-form')).to_be_visible()
                login(a,'tecnico2','own-test-only-password-789')
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
                browser.close()
            print('PASS: four accounts, owner migration, separate profiles/signatures and certificates, shared companies/templates, download isolation, deactivation, reset, self password change, logout across tabs, mobile, no worker archives or JS errors.')
        finally:
            process.terminate(); process.wait(timeout=10)
