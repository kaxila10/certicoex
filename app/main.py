from datetime import date, datetime, timedelta, timezone
from io import BytesIO, StringIO
from pathlib import Path
import csv
import json
import os
import re
import shutil
import unicodedata
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from openpyxl import load_workbook
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator

from .documents import check_zip, convert_pdf, inspect_template, office_binary, render_batch
from .access import authorized, configured
from .defaults import seed_templates
from .storage import open_store, StorageUnavailable
from .validation import review_students
from .naming import document_name, disposition

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('CERTICOEX_DATA', ROOT / '.local/data'))
for folder in ('templates', 'jobs'):
    (DATA / folder).mkdir(parents=True, exist_ok=True)
seed_templates(DATA, ROOT / 'defaults')
app = FastAPI(title='Certicoex', docs_url=None, redoc_url=None)


@app.middleware('http')
async def local_requests(request: Request, call_next):
    if request.url.path != '/api/health':
        if not configured():
            return JSONResponse({'detail': 'Configura una contraseña de acceso de al menos 16 caracteres en el servicio.'}, status_code=503, headers={'Cache-Control':'no-store'})
        if not authorized(request.headers.get('authorization')):
            return JSONResponse({'detail': 'Introduce el usuario y la contraseña de la aplicación.'}, status_code=401, headers={'WWW-Authenticate':'Basic realm="Certicoex", charset="UTF-8"', 'Cache-Control':'no-store'})
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
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; frame-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
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
        path.write_bytes(content)
    return path


def store_template(data, name):
    fields = inspect_template(data)
    key = uuid.uuid4().hex
    (DATA / 'templates' / (key + '.docx')).write_bytes(data)
    meta = {'id': key, 'name': name.strip()[:120] or 'Curso sin título', 'fields': fields}
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
    return {'status': 'ok', 'pdf_available': bool(office_binary()), 'storage_ephemeral':ephemeral, 'storage_external':external}


@app.get('/api/templates')
def templates():
    return open_store(DATA).all('template')


@app.post('/api/templates')
def upload_template(file: UploadFile = File(...), name: str = Form(...)):
    if not (file.filename or '').lower().endswith('.docx'):
        raise ValueError('Sube una plantilla Word .docx.')
    data = file.file.read(10_000_001)
    if len(data) > 10_000_000:
        raise ValueError('La plantilla supera 10 MB.')
    try:
        return store_template(data, name)
    except (KeyError, TypeError) as exc:
        raise ValueError('No se puede leer esta plantilla Word.') from exc


@app.get('/api/profile')
def profile():
    db = open_store(DATA)
    data = db.get('settings','profile',{'name':''})
    return {**data, 'has_signature': db.asset('signature') is not None}


@app.get('/api/profile/signature')
def signature_file():
    content = open_store(DATA).asset('signature')
    if content is None:
        raise HTTPException(404)
    return Response(content,media_type='image/png')


@app.post('/api/profile')
def save_profile(name: str = Form(...), signature: UploadFile | None = File(None)):
    name = name.strip()
    if not name or len(name) > 160:
        raise ValueError('Introduce tu nombre completo (máximo 160 caracteres).')
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
                temp = DATA / 'signature.tmp'
                temp.write_bytes(out.getvalue())
                temp.replace(DATA / 'signature.png')
                assets['signature']=out.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError('No se puede leer la imagen de firma.') from exc
    atomic_json(DATA / 'profile.json', {'name': name})
    open_store(DATA).bundle('settings','profile',{'name':name},assets)
    return profile()


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
    key=existing['id'] if existing else uuid.uuid4().hex
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


@app.put('/api/templates/{key}/settings')
def course_settings(key: str, payload: CourseSettings):
    db=open_store(DATA)
    meta=db.get('template',key)
    if not meta: raise HTTPException(404,'Curso no encontrado.')
    if not payload.name.strip(): raise ValueError('Introduce un nombre para el curso.')
    meta.update(name=payload.name.strip(),hours=payload.hours)
    return db.put('template',key,meta)


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
    if not re.fullmatch(r'[a-f0-9]{32}',key): raise HTTPException(404)
    record={'id':key,'updated':datetime.now(timezone.utc).isoformat(),'data':payload.model_dump()}
    return open_store(DATA).put('draft',key,record)


@app.get('/api/drafts')
def drafts():
    return open_store(DATA).all('draft')


@app.delete('/api/drafts/{key}')
def delete_draft(key: str):
    open_store(DATA).delete('draft',key)
    return {'deleted':True}


@app.get('/api/history')
def history(q: str='', course_date: str='', offset: int=0):
    records=open_store(DATA).all('history')
    needle=normalize(q)
    records=[r for r in records if (not course_date or r['data']['course_date']==course_date) and (not needle or needle in normalize(json.dumps(r,ensure_ascii=False)))]
    offset=max(0,offset)
    return {'total':len(records),'items':records[offset:offset+50]}


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value).lower()) if not unicodedata.combining(c)).strip()


def tabular(rows):
    rows = [list(row) for row in rows if any(str(c or '').strip() for c in row)]
    if not rows:
        raise ValueError('El listado está vacío.')
    if len(rows) > 501 or max(map(len, rows)) > 40:
        raise ValueError('Admite hasta 500 alumnos y 40 columnas por importación.')
    aliases = {'name': ['nombre completo', 'nombre y apellidos', 'trabajador', 'alumno', 'nombre', 'nombres'], 'surname': ['apellidos', 'apellido', 'primer apellido'], 'surname2': ['segundo apellido'], 'dni': ['dni/nie', 'dni', 'nie', 'nif', 'documento', 'dni / nie']}
    first = [normalize(c or '') for c in rows[0]]
    mapping = {field: next((first.index(a) for a in names if a in first), -1) for field, names in aliases.items()}
    has_header = mapping['name'] >= 0 or mapping['dni'] >= 0
    width = max(map(len, rows))
    headers = [str(c or f'Columna {i+1}') for i, c in enumerate(rows[0])] if has_header else [f'Columna {i+1}' for i in range(width)]
    if not has_header:
        mapping = {'name': 0, 'surname': -1, 'surname2': -1, 'dni': 1 if width > 1 else -1}
    return {'headers': headers, 'rows': [[str(c).strip() if c is not None else '' for c in row] for row in rows[int(has_header):]], 'mapping': mapping}


class Paste(BaseModel):
    text: str = Field(max_length=150_000)


@app.post('/api/import/paste')
def paste_list(payload: Paste):
    text = payload.text.strip()
    if not text:
        raise ValueError('Pega el listado de alumnos.')
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters='\t;,')
        rows = list(csv.reader(StringIO(text), dialect))
    except csv.Error:
        # Also accept "Nombre Apellidos 12345678Z" on each line.
        rows = []
        for line in text.splitlines():
            match = re.match(r'^(.*?)\s+([XYZxyz\d][\d .-]{5,}[A-Za-z])\s*$', line)
            rows.append([match[1], match[2]] if match else [line, ''])
    return tabular(rows)


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
def generate(payload: Batch):
    template = template_path(payload.template_id)
    current = profile()
    if not current['name'] or not current['has_signature']:
        raise ValueError('Guarda tu nombre y una imagen de firma en el perfil del técnico.')
    seen = set()
    for s in payload.students:
        dni = re.sub(r'[\s.\-]', '', s.dni).upper()
        if dni in seen:
            raise ValueError('Hay documentos de identidad repetidos. Corrige el listado antes de generar.')
        seen.add(dni)
    clean_jobs()
    key = uuid.uuid4().hex
    folder = DATA / 'jobs' / key
    folder.mkdir()
    d = payload.course_date
    common = {'EMPRESA': payload.company, 'CIF': payload.cif, 'HORAS': f'{payload.hours:g}', 'FECHA': f'{d.day} de {MONTHS[d.month-1]} de {d.year}', 'TECNICO': current['name']}
    students = [s.model_dump() for s in payload.students]
    output = folder / 'certificados.docx'
    db=open_store(DATA)
    meta=db.get('template',payload.template_id,{})
    filename=document_name(meta.get('name','Curso'),payload.company,d)
    render_batch(template, students, common, db.asset('signature'), output)
    result = {'word': f'/api/files/{key}/docx', 'count': len(students), 'pdf': None, 'pages': None, 'filename':filename}
    try:
        _, pages = convert_pdf(output, folder)
        result.update(pdf=f'/api/files/{key}/pdf', pages=pages)
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


@app.get('/api/files/{key}/{kind}')
def download(key: str, kind: str, inline: bool = False):
    if not re.fullmatch(r'[a-f0-9]{32}', key) or kind not in ('pdf', 'docx'):
        raise HTTPException(404)
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
