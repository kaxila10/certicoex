from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import json
import os
import re
import shutil
import unicodedata
import uuid
from tempfile import TemporaryDirectory

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from openpyxl import load_workbook
from PIL import Image, UnidentifiedImageError
from lxml.etree import XMLSyntaxError
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from .documents import check_zip, convert_pdf, inspect_template, office_binary, render_batch
from .access import authorized, configured
from .defaults import seed_templates
from .storage import open_store, StorageUnavailable, TemplateConflict
from .validation import review_students
from .naming import clean_part, document_name, disposition
from .rosters import parse_text, tabular
from .roster_files import extract_list
from .temporary_jobs import temporary_jobs, LIFETIME_SECONDS
from . import accounts
from .layout import inspect_layout

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('CERTICOEX_DATA', ROOT / '.local/data'))
for folder in ('templates', 'jobs'):
    (DATA / folder).mkdir(parents=True, exist_ok=True)
seed_templates(DATA, ROOT / 'defaults')
app = FastAPI(title='Certicoex', docs_url=None, redoc_url=None)


def retains_students():
    return not accounts.enabled() and os.environ.get('CERTICOEX_RETAIN_STUDENTS', 'false').lower() == 'true'


@app.middleware('http')
async def local_requests(request: Request, call_next):
    path = request.url.path
    public_path = path in ('/api/health', '/api/auth/login', '/login', '/login.js', '/style.css', '/favicon.ico', '/logo.png')
    if path != '/api/health':
        if not configured():
            return JSONResponse({'detail': 'Configura una contraseña de acceso de al menos 16 caracteres en el servicio.'}, status_code=503, headers={'Cache-Control':'no-store'})
        if accounts.enabled():
            token = request.cookies.get(accounts.COOKIE)
            try:
                actor = await run_in_threadpool(lambda: accounts.session_user(open_store(DATA), token)) if token and not public_path else None
            except StorageUnavailable:
                return JSONResponse({'detail': 'No se puede acceder al almacenamiento de usuarios. Vuelve a intentarlo en unos instantes.'}, status_code=503, headers={'Cache-Control': 'no-store'})
            if not public_path and not actor:
                if path == '/':
                    return RedirectResponse('/login', status_code=303, headers={'Cache-Control': 'no-store'})
                return JSONResponse({'detail': 'Tu sesión ha terminado. Inicia sesión de nuevo.'}, status_code=401, headers={'Cache-Control': 'no-store'})
            if actor and request.headers.get('x-certicoex-user', actor['id']) != actor['id']:
                return JSONResponse({'detail': 'La cuenta activa ha cambiado. Recarga la página antes de continuar.', 'code': 'session_changed'}, status_code=409, headers={'Cache-Control': 'no-store'})
            request.state.user = actor
        elif not public_path and not authorized(request.headers.get('authorization')):
            return JSONResponse({'detail': 'Introduce el usuario y la contraseña de la aplicación.'}, status_code=401, headers={'WWW-Authenticate':'Basic realm="Certicoex", charset="UTF-8"', 'Cache-Control':'no-store'})
        else:
            request.state.user = accounts.owner()
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        from urllib.parse import urlsplit
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail': 'Origen de solicitud no permitido.'}, status_code=403)
        if int(request.headers.get('content-length', 0)) > 15_000_000:
            return JSONResponse({'detail': 'El archivo supera el límite de 15 MB.'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Cache-Control'] = 'no-store'
    if path in ('/logo.png', '/favicon.ico'):
        response.headers['Cache-Control'] = 'public, max-age=86400'
    elif path in ('/section-icons.png', '/user-icon.png'):
        response.headers['Cache-Control'] = 'private, max-age=86400'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; frame-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
    return response


@app.get('/login')
def login_page():
    return FileResponse(ROOT / 'static' / 'login.html')


@app.get('/favicon.ico')
def favicon():
    return FileResponse(ROOT / 'static' / 'logo.png', media_type='image/png')


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


@app.post('/api/auth/login')
def login(payload: Credentials, request: Request):
    if not accounts.enabled():
        raise HTTPException(409, 'El acceso por cuentas no está activado en esta instalación.')
    client = request.client.host if request.client else 'unknown'
    if not accounts.check_rate(client):
        raise HTTPException(429, 'Demasiados intentos. Espera 15 minutos antes de volver a intentarlo.')
    db = open_store(DATA)
    actor = accounts.authenticate(db, payload.username, payload.password)
    if not actor:
        accounts.failed_login(client)
        raise HTTPException(401, 'Usuario o contraseña incorrectos.')
    response = JSONResponse(accounts.public(actor))
    response.set_cookie(accounts.COOKIE, accounts.create_session(db, actor), max_age=accounts.SESSION_SECONDS,
                        httponly=True, samesite='lax', secure=request.url.scheme == 'https' or os.environ.get('RENDER', '').lower() == 'true', path='/')
    return response


@app.get('/api/auth/me')
def me(request: Request):
    return {**accounts.public(request.state.user), 'accounts_enabled': accounts.enabled()}


@app.post('/api/auth/logout')
def logout(request: Request):
    accounts.logout(open_store(DATA), request.cookies.get(accounts.COOKIE))
    temporary_jobs.delete_owner(request.state.user['id'])
    response = JSONResponse({'logged_out': True})
    response.delete_cookie(accounts.COOKIE, path='/')
    return response


def admin(request):
    if not accounts.enabled() or request.state.user['role'] != 'admin':
        raise HTTPException(403, 'Esta acción está reservada al administrador.')


@app.get('/api/users')
def list_users(request: Request):
    admin(request)
    rows = open_store(DATA).all('account')
    if not any(row['id'] == 'owner' for row in rows):
        rows.insert(0, accounts.owner())
    return [accounts.public(row) for row in rows]


class NewAccount(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=12, max_length=200)


@app.post('/api/users')
def create_user(payload: NewAccount, request: Request):
    admin(request)
    db = open_store(DATA)
    name = accounts.username(payload.username)
    if name == accounts.owner()['username'].strip().lower():
        raise ValueError('Ese usuario ya existe. Elige otro nombre.')
    key = uuid.uuid4().hex
    actor = {'id': key, 'username': name, 'role': 'technician', 'active': True, 'version': 0,
             'password_hash': accounts.password_hash(payload.password)}
    db.create_account(actor)
    return accounts.public(actor)


class AccountChange(BaseModel):
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=200)


@app.patch('/api/users/{key}')
def update_user(key: str, payload: AccountChange, request: Request):
    admin(request)
    if key == 'owner' or key == request.state.user['id']:
        raise ValueError('Gestiona tu propia contraseña desde «Mi cuenta».')
    db = open_store(DATA)
    actor = db.get('account', key)
    if not actor:
        raise HTTPException(404, 'Usuario no encontrado.')
    if payload.active is not None:
        actor['active'] = payload.active
        actor['version'] += 1
        if not payload.active:
            temporary_jobs.delete_owner(key)
    if payload.password is not None:
        actor['password_hash'] = accounts.password_hash(payload.password)
        actor['version'] += 1
        temporary_jobs.delete_owner(key)
    db.put('account', key, actor)
    return accounts.public(actor)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


@app.post('/api/auth/password')
def change_password(payload: PasswordChange, request: Request):
    if not accounts.enabled():
        raise HTTPException(409, 'El acceso por cuentas no está activado.')
    db = open_store(DATA)
    actor = request.state.user
    client = request.client.host if request.client else 'unknown'
    if not accounts.check_rate(client):
        raise HTTPException(429, 'Demasiados intentos. Espera 15 minutos antes de volver a intentarlo.')
    if not accounts.authenticate(db, actor['username'], payload.current_password):
        accounts.failed_login(client)
        raise HTTPException(403, 'La contraseña actual no es correcta.')
    changed = {**actor, 'password_hash': accounts.password_hash(payload.new_password), 'version': actor.get('version', 0) + 1}
    db.put('account', actor['id'], changed)
    accounts.logout(db, request.cookies.get(accounts.COOKIE))
    response = JSONResponse({'changed': True})
    response.set_cookie(accounts.COOKIE, accounts.create_session(db, changed), max_age=accounts.SESSION_SECONDS,
                        httponly=True, samesite='lax', secure=request.url.scheme == 'https' or os.environ.get('RENDER', '').lower() == 'true', path='/')
    return response


@app.exception_handler(ValueError)
async def invalid_input(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=422)


@app.exception_handler(StorageUnavailable)
async def unavailable_storage(request, exc):
    return JSONResponse({'detail':'No se puede acceder al almacenamiento. Revisa la conexión de la base de datos en Render y vuelve a intentarlo.'},status_code=503)


def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
    temp.replace(path)


def template_path(key):
    if not re.fullmatch(r'[a-f0-9]{32}', key):
        raise HTTPException(404, 'Plantilla no encontrada.')
    content = open_store(DATA).asset('templates/'+key)
    if content is None:
        raise HTTPException(404, 'Plantilla no encontrada.')
    path = DATA / 'templates' / (key + '.docx')
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists() or path.read_bytes() != content:
        temporary = path.with_name('.' + key + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def store_template(data, name, filename=None):
    fields = inspect_template(data)
    key = uuid.uuid4().hex
    (DATA / 'templates' / (key + '.docx')).write_bytes(data)
    meta = {'id': key, 'name': name.strip()[:120] or 'Curso sin título', 'fields': fields}
    if filename:
        original = filename.replace('\\', '/').rsplit('/', 1)[-1]
        meta['filename'] = clean_part(Path(original).stem, 200, 'Plantilla') + '.docx'
    atomic_json(DATA / 'templates' / (key + '.json'), meta)
    db = open_store(DATA)
    db.bundle('template',key,meta,{'templates/'+key:data})
    return meta


@app.get('/api/health')
def health():
    if not configured():
        raise HTTPException(503, 'Falta configurar el acceso privado.')
    db = open_store(DATA)
    db.get('settings','profile')
    external = bool(os.environ.get('CERTICOEX_DATABASE_URL'))
    ephemeral = not external and os.environ.get('CERTICOEX_EPHEMERAL', '').lower() == 'true'
    return {'status': 'ok', 'pdf_available': bool(office_binary()), 'storage_ephemeral':ephemeral, 'storage_external':external,
            'retains_students': retains_students(), 'temporary_minutes': LIFETIME_SECONDS // 60,
            'accounts_enabled': accounts.enabled()}


@app.get('/api/templates')
def templates():
    return open_store(DATA).all('template')


@app.get('/api/templates/{key}/file')
def download_template(key: str):
    meta, content = open_store(DATA).template_snapshot(key)
    if meta is None:
        raise HTTPException(404, 'Plantilla no encontrada.')
    name = Path(meta['filename']).stem if meta.get('filename') else meta.get('name', 'Plantilla')
    name = clean_part(name, 200, 'Plantilla')
    return Response(content, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        headers={'Content-Disposition': disposition(name, 'docx')})


def invalidate_template_cache(key):
    for suffix in ('.docx', '.json'):
        (DATA / 'templates' / (key + suffix)).unlink(missing_ok=True)


def replace_saved_template(key, content, fields, filename, version, restore=False):
    if not re.fullmatch(r'[a-f0-9]{32}', key):
        raise HTTPException(404, 'Plantilla no encontrada.')
    try:
        meta = open_store(DATA).replace_template(key, content, fields, filename, version, restore)
    except TemplateConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if meta is None:
        raise HTTPException(404, 'No se encuentra la plantilla o su copia anterior.')
    invalidate_template_cache(key)
    return meta


@app.put('/api/templates/{key}/file')
def replace_template(key: str, request: Request, file: UploadFile = File(...), version: int = Form(..., ge=0)):
    if accounts.enabled():
        admin(request)
    if not (file.filename or '').lower().endswith('.docx'):
        raise ValueError('Sube una plantilla Word .docx.')
    content = file.file.read(10_000_001)
    if len(content) > 10_000_000:
        raise ValueError('La plantilla supera 10 MB.')
    try:
        fields = inspect_template(content)
    except (KeyError, TypeError, XMLSyntaxError) as exc:
        raise ValueError('No se puede leer esta plantilla Word.') from exc
    original = file.filename.replace('\\', '/').rsplit('/', 1)[-1]
    filename = clean_part(Path(original).stem, 200, 'Plantilla') + '.docx'
    return replace_saved_template(key, content, fields, filename, version)


class TemplateVersion(BaseModel):
    version: int = Field(ge=0)


@app.post('/api/templates/{key}/restore')
def restore_template(key: str, request: Request, payload: TemplateVersion):
    if accounts.enabled():
        admin(request)
    return replace_saved_template(key, None, None, None, payload.version, restore=True)


@app.delete('/api/templates/{key}')
def delete_template(key: str, request: Request):
    if accounts.enabled():
        admin(request)
    if not re.fullmatch(r'[a-f0-9]{32}', key):
        raise HTTPException(404, 'Plantilla no encontrada.')
    open_store(DATA).delete_template(key)
    for suffix in ('.json', '.docx'):
        (DATA / 'templates' / (key + suffix)).unlink(missing_ok=True)
    return {'deleted': True}


@app.post('/api/templates')
def upload_template(file: UploadFile = File(...), name: str = Form(...)):
    if not (file.filename or '').lower().endswith('.docx'):
        raise ValueError('Sube una plantilla Word .docx.')
    data = file.file.read(10_000_001)
    if len(data) > 10_000_000:
        raise ValueError('La plantilla supera 10 MB.')
    try:
        return store_template(data, name, file.filename)
    except (KeyError, TypeError, XMLSyntaxError) as exc:
        raise ValueError('No se puede leer esta plantilla Word.') from exc


@app.get('/api/profile')
def profile(request: Request):
    return user_profile(request.state.user['id'])


def profile_signature(db, user_id):
    content = db.asset('signatures/' + user_id)
    if content is None and user_id == 'owner':
        content = db.asset('signature')
    return content


def user_profile(user_id):
    db = open_store(DATA)
    data = db.get('profile', user_id)
    if data is None:
        data = db.get('settings', 'profile', {'name': ''}) if user_id == 'owner' else {'name': ''}
    return {'title': '', 'salutation': '', **data,
            'has_signature': profile_signature(db, user_id) is not None}


@app.get('/api/profile/signature')
def signature_file(request: Request):
    content = profile_signature(open_store(DATA), request.state.user['id'])
    if content is None:
        raise HTTPException(404)
    return Response(content,media_type='image/png')


@app.post('/api/profile')
def save_profile(request: Request, name: str = Form(...), signature: UploadFile | None = File(None),
                 title: str | None = Form(None), salutation: str | None = Form(None)):
    name = name.strip()
    if not name or len(name) > 160:
        raise ValueError('Introduce tu nombre completo (máximo 160 caracteres).')
    user_id = request.state.user['id']
    current = user_profile(user_id)
    title = current['title'] if title is None else title.strip()
    salutation = current['salutation'] if salutation is None else salutation.strip()
    if len(title) > 120 or '\n' in title or '\r' in title:
        raise ValueError('Introduce un cargo de una sola línea y máximo 120 caracteres.')
    if salutation not in ('', 'D.', 'Dña.'):
        raise ValueError('Selecciona el tratamiento D. o Dña.')
    data = {'name': name, 'title': title, 'salutation': salutation}
    legacy = not accounts.enabled() and user_id == 'owner'
    assets={}
    if signature and signature.filename:
        raw = signature.file.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError('La firma debe ocupar menos de 5 MB.')
        try:
            with Image.open(BytesIO(raw)) as im:
                if im.format not in ('PNG', 'JPEG') or im.width * im.height > 12_000_000:
                    raise ValueError('Usa una firma PNG o JPG de hasta 12 megapíxeles.')
                im.load()
                out = BytesIO()
                im.convert('RGBA').save(out, format='PNG')
                if legacy:
                    temp = DATA / 'signature.tmp'
                    temp.write_bytes(out.getvalue())
                    temp.replace(DATA / 'signature.png')
                assets['signature' if legacy else 'signatures/' + user_id]=out.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError('No se puede leer la imagen de firma.') from exc
    if legacy:
        atomic_json(DATA / 'profile.json', data)
    open_store(DATA).bundle('settings' if legacy else 'profile', 'profile' if legacy else user_id, data, assets)
    return user_profile(user_id)


class Company(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1,max_length=200)
    cif: str = Field(min_length=1,max_length=30)

    @field_validator('name','cif')
    @classmethod
    def clean(cls,value):
        value=value.strip()
        if not value or any(ord(c)<32 for c in value): raise ValueError('Completa empresa y CIF.')
        return value


@app.get('/api/companies')
def companies():
    return sorted(open_store(DATA).all('company'),key=lambda e:normalize(e['name']))


@app.post('/api/companies')
def save_company(payload: Company):
    db=open_store(DATA)
    normalized=lambda v:re.sub(r'[\s.\-]','',v).upper()
    existing=next((e for e in db.all('company') if normalized(e['cif'])==normalized(payload.cif)),None)
    if not existing and payload.id: existing=db.get('company',payload.id)
    key=existing['id'] if existing else uuid.uuid5(uuid.NAMESPACE_URL, 'certicoex/company/' + normalized(payload.cif)).hex
    return db.put('company',key,{'id':key,'name':payload.name,'cif':payload.cif})


@app.post('/api/companies/import')
def migrate_companies(payload: list[Company]):
    if len(payload)>2000: raise ValueError('Importa como máximo 2000 empresas.')
    db=open_store(DATA)
    known={re.sub(r'[\s.\-]','',e['cif']).upper() for e in db.all('company')}
    for entry in payload:
        normalized=re.sub(r'[\s.\-]','',entry.cif).upper()
        if normalized not in known:
            save_company(entry);known.add(normalized)
    return companies()


class CourseSettings(BaseModel):
    name: str = Field(min_length=1,max_length=120)
    hours: float | None = Field(default=None,gt=0,le=1000,allow_inf_nan=False)
    expected_pages: int | None = Field(default=None, ge=1, le=20)


@app.put('/api/templates/{key}/settings')
def course_settings(key: str, payload: CourseSettings):
    db=open_store(DATA)
    meta=db.get('template',key)
    if not meta: raise HTTPException(404,'Curso no encontrado.')
    if not payload.name.strip(): raise ValueError('Introduce un nombre para el curso.')
    meta.update(name=payload.name.strip(),hours=payload.hours)
    if payload.expected_pages is not None:
        meta['expected_pages'] = payload.expected_pages
    updated = db.update_template(key, meta)
    if updated is None:
        raise HTTPException(404, 'Curso no encontrado.')
    return updated


class Review(BaseModel):
    students: list[dict[str,str]] = Field(max_length=500)


@app.post('/api/review')
def review(payload: Review):
    return {'issues':review_students(payload.students)}


class Draft(BaseModel):
    title: str = Field(default='',max_length=200)
    template_id: str = Field(default='',max_length=64)
    company: str = Field(default='',max_length=200)
    cif: str = Field(default='',max_length=30)
    hours: str = Field(default='',max_length=20)
    course_date: str = Field(default='',max_length=20)
    students: list[dict[str,str]] = Field(default_factory=list,max_length=500)

    @field_validator('students')
    @classmethod
    def safe_rows(cls, rows):
        for row in rows:
            if set(row)-{'name','dni'} or len(row.get('name',''))>180 or len(row.get('dni',''))>30:
                raise ValueError('Fila de alumno no válida.')
        return rows


@app.post('/api/drafts')
def new_draft(payload: Draft):
    key=uuid.uuid4().hex
    return update_draft(key,payload)


@app.put('/api/drafts/{key}')
def update_draft(key: str, payload: Draft):
    if not retains_students():
        raise HTTPException(409, 'El guardado de alumnos está desactivado. Los borradores no se conservan.')
    if not re.fullmatch(r'[a-f0-9]{32}',key): raise HTTPException(404)
    record={'id':key,'updated':datetime.now(timezone.utc).isoformat(),'data':payload.model_dump()}
    return open_store(DATA).put('draft',key,record)


@app.get('/api/drafts')
def drafts():
    if not retains_students():
        return []
    return open_store(DATA).all('draft')


@app.delete('/api/drafts/{key}')
def delete_draft(key: str):
    open_store(DATA).delete('draft',key)
    return {'deleted':True}


@app.get('/api/history')
def history(q: str='', course_date: str='', offset: int=0):
    if not retains_students():
        return {'total': 0, 'items': []}
    records=open_store(DATA).all('history')
    needle=normalize(q)
    records=[r for r in records if (not course_date or r['data']['course_date']==course_date) and (not needle or needle in normalize(json.dumps(r,ensure_ascii=False)))]
    offset=max(0,offset)
    return {'total':len(records),'items':records[offset:offset+50]}


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value).lower()) if not unicodedata.combining(c)).strip()


class Paste(BaseModel):
    text: str = Field(max_length=150_000)


@app.post('/api/import/paste')
def paste_list(payload: Paste):
    return parse_text(payload.text)


@app.post('/api/import/document')
def import_document(file: UploadFile = File(...)):
    return extract_list(file.file.read(10_000_001), file.filename or '')


@app.post('/api/import/excel')
def import_excel(file: UploadFile = File(...)):
    if not (file.filename or '').lower().endswith('.xlsx'):
        raise ValueError('Usa Excel .xlsx. Para .xls, guárdalo como .xlsx o copia y pega las celdas.')
    raw = file.file.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValueError('El Excel supera 10 MB.')
    check_zip(raw)
    try:
        workbook = load_workbook(BytesIO(raw), read_only=True, data_only=True)
        sheet = workbook.active
        rows = list(sheet.iter_rows(min_row=1, max_row=502, max_col=41, values_only=True))
        # Trim unused trailing columns, preserving interior empty cells.
        rows = [list(row) for row in rows]
        for row in rows:
            while row and row[-1] is None:
                row.pop()
        result = tabular(rows)
        result['sheet'] = sheet.title
        workbook.close()
        return result
    except (KeyError, TypeError, OSError) as exc:
        raise ValueError('No se puede leer el Excel. Comprueba que es un archivo .xlsx válido.') from exc


class Student(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    dni: str = Field(min_length=1, max_length=30)

    @field_validator('name', 'dni')
    @classmethod
    def trim(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError('Nombre y documento deben contener texto válido.')
        return value


class Batch(BaseModel):
    title: str = Field(default='',max_length=200)
    template_id: str
    company: str = Field(min_length=1, max_length=200)
    cif: str = Field(min_length=1, max_length=30)
    hours: float = Field(gt=0, le=1000, allow_inf_nan=False)
    course_date: date
    students: list[Student] = Field(min_length=1, max_length=500)
    preview: bool = False

    @field_validator('company', 'cif')
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError('Completa los datos de la empresa.')
        return value


MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def clean_jobs():
    cutoff = datetime.now(timezone.utc).timestamp() - timedelta(hours=24).total_seconds()
    for folder in (DATA / 'jobs').iterdir():
        if folder.is_dir() and folder.stat().st_mtime < cutoff:
            shutil.rmtree(folder)
    db=open_store(DATA)
    for job in db.all('preview'):
        if job['created'] < cutoff:
            for kind in ('pdf','docx'): db.delete_asset('jobs/'+job['id']+'/'+kind)
            db.delete('preview',job['id'])


@app.post('/api/generate')
def generate(payload: Batch, request: Request):
    meta, content = open_store(DATA).template_snapshot(payload.template_id)
    if meta is None:
        raise HTTPException(404, 'Plantilla no encontrada.')
    template = BytesIO(content)
    user_id = request.state.user['id']
    current = user_profile(user_id)
    if not current['name'] or not current['has_signature']:
        raise ValueError('Guarda tu nombre y una imagen de firma en el perfil del técnico.')
    fields = meta.get('fields', [])
    if (('CARGO_TECNICO' in fields and not current['title']) or
            ('TRATAMIENTO_TECNICO' in fields and not current['salutation'])):
        raise ValueError('Completa tu cargo y tratamiento en Mi perfil para utilizar esta plantilla.')
    seen = set()
    for s in payload.students:
        dni = re.sub(r'[\s.\-]', '', s.dni).upper()
        if dni in seen:
            raise ValueError('Hay documentos de identidad repetidos. Corrige el listado antes de generar.')
        seen.add(dni)
    if not retains_students():
        return generate_temporary(payload, template, current, user_id, meta)
    clean_jobs()
    key = uuid.uuid4().hex
    folder = DATA / 'jobs' / key
    folder.mkdir()
    d = payload.course_date
    common = {'EMPRESA': payload.company, 'CIF': payload.cif, 'HORAS': f'{payload.hours:g}',
              'FECHA': f'{d.day} de {MONTHS[d.month-1]} de {d.year}', **technician_fields(current)}
    students = [s.model_dump() for s in payload.students]
    output = folder / 'certificados.docx'
    db=open_store(DATA)
    filename=document_name(meta.get('name','Curso'),payload.company,d)
    render_batch(template, students, common, profile_signature(db, user_id), output)
    result = {'word': f'/api/files/{key}/docx', 'count': len(students), 'pdf': None, 'pages': None, 'filename':filename}
    try:
        pdf, pages = convert_pdf(output, folder)
        result.update(pdf=f'/api/files/{key}/pdf', pages=pages)
        result['layout'] = inspect_layout(pdf, output, len(students), meta.get('expected_pages', 1), common)
    except RuntimeError as exc:
        result['warning'] = str(exc)
    assets={}
    for kind in ('docx','pdf'):
        if kind=='pdf' and not result['pdf']: continue
        path=folder/('certificados.'+kind)
        if path.exists(): assets['jobs/'+key+'/'+kind]=path.read_bytes()
    if payload.preview:
        db.bundle('preview',key,{'id':key,'created':datetime.now(timezone.utc).timestamp(),'filename':filename},assets)
    else:
        record={'id':key,'created':datetime.now(timezone.utc).isoformat(),'course':meta.get('name','Curso'),
                'technician':current['name'],'data':payload.model_dump(mode='json'),'files':result,
                'issues':review_students(students)}
        db.bundle('history',key,record,assets)
        save_company(Company(name=payload.company,cif=payload.cif))
        result['history_id']=key
    return result


def technician_fields(current):
    return {'TECNICO': current['name'], 'CARGO_TECNICO': current['title'],
            'TRATAMIENTO_TECNICO': current['salutation']}


def generate_temporary(payload, template, current, user_id, meta):
    key = uuid.uuid4().hex
    db = open_store(DATA)
    filename = document_name(meta.get('name', 'Curso'), payload.company, payload.course_date)
    d = payload.course_date
    common = {'EMPRESA': payload.company, 'CIF': payload.cif, 'HORAS': f'{payload.hours:g}',
              'FECHA': f'{d.day} de {MONTHS[d.month-1]} de {d.year}', **technician_fields(current)}
    result = {'word': f'/api/files/{key}/docx', 'pdf': None, 'pages': None,
              'count': len(payload.students), 'filename': filename, 'temporary_minutes': LIFETIME_SECONDS // 60}
    with TemporaryDirectory(prefix='certicoex-certificates-') as temporary:
        folder = Path(temporary)
        output = folder / 'certificados.docx'
        render_batch(template, [s.model_dump() for s in payload.students], common, profile_signature(db, user_id), output)
        files = {'docx': output.read_bytes()}
        try:
            pdf, pages = convert_pdf(output, folder)
            files['pdf'] = pdf.read_bytes()
            result.update(pdf=f'/api/files/{key}/pdf', pages=pages)
            result['layout'] = inspect_layout(pdf, output, len(payload.students), meta.get('expected_pages', 1), common)
        except RuntimeError as exc:
            result['warning'] = str(exc)
    # No student metadata, certificates or previews are written to the database.
    temporary_jobs.put(key, files, filename, owner=user_id)
    if not payload.preview:
        save_company(Company(name=payload.company, cif=payload.cif))
    return result


@app.delete('/api/files/{key}')
def delete_temporary_files(key: str, request: Request):
    if not re.fullmatch(r'[a-f0-9]{32}', key):
        raise HTTPException(404)
    temporary_jobs.delete(key, owner=request.state.user['id'])
    return {'deleted': True}


@app.get('/api/files/{key}/{kind}')
def download(key: str, kind: str, request: Request, inline: bool = False):
    if not re.fullmatch(r'[a-f0-9]{32}', key) or kind not in ('pdf', 'docx'):
        raise HTTPException(404)
    if not retains_students():
        job = temporary_jobs.get(key, owner=request.state.user['id'])
        if not job or kind not in job['files']:
            raise HTTPException(404, 'Archivo eliminado o caducado. Vuelve a generar los certificados.')
        return Response(job['files'][kind], media_type='application/pdf' if kind == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        headers={'Content-Disposition': disposition(job['filename'], kind, inline)})
    db=open_store(DATA)
    content=db.asset('jobs/'+key+'/'+kind)
    preview=db.get('preview',key)
    if preview and preview['created']<datetime.now(timezone.utc).timestamp()-86400:
        raise HTTPException(404,'La vista previa ha caducado. Vuelve a generarla.')
    record=db.get('history',key)
    name=(preview or {}).get('filename') or (record or {}).get('files',{}).get('filename')
    if not name and record:
        name=document_name(record['course'],record['data']['company'],record['data']['course_date'])
    name=name or 'certificados'
    if content is not None:
        return Response(content,media_type='application/pdf' if kind=='pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':disposition(name,kind,inline)})
    path = DATA / 'jobs' / key / ('certificados.' + kind)
    if not path.exists() or path.stat().st_mtime < datetime.now(timezone.utc).timestamp() - 86400:
        raise HTTPException(404, 'Archivo caducado. Vuelve a generar los certificados.')
    return FileResponse(path, media_type='application/pdf' if kind == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', filename=path.name, content_disposition_type='inline' if inline else 'attachment')


app.mount('/', StaticFiles(directory=ROOT / 'static', html=True), name='web')
