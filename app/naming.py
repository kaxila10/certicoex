from datetime import date
import re
import unicodedata
from urllib.parse import quote


def clean_part(value, limit, fallback):
    value = unicodedata.normalize('NFC',value)
    value = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|\u202a-\u202e\u2066-\u2069]', ' ', value)
    value = ' '.join(value.split()).strip('. ')
    return value.encode('utf8')[:limit].decode('utf8',errors='ignore').strip('. ') or fallback


def document_name(course, company, course_date):
    day = course_date if isinstance(course_date,date) else date.fromisoformat(course_date)
    course = clean_part(course,140,'Curso')
    company = clean_part(company.split(',',1)[0],70,'Empresa')
    return f'{course} - {company} - {day:%d-%m-%Y}'


def disposition(name, kind, inline=False):
    filename = name+'.'+kind
    ascii_name = unicodedata.normalize('NFKD',filename).encode('ascii',errors='ignore').decode('ascii')
    return f'{"inline" if inline else "attachment"}; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename,safe="")}'
