"""Read student lists while keeping unrecognised lines available for review."""
import csv
from io import StringIO
import re
import unicodedata


DOCUMENT = re.compile(r'(?<![\w])(?:[XYZ](?:[ .-]*\d){7}|\d(?:[ .-]*\d){7})[ .-]*[A-Z](?![\w])', re.I)
DATE = re.compile(r'(?<!\d)(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2})(?!\d)')
ALIASES = {
    'name': ['nombre completo', 'nombre y apellidos', 'trabajador', 'trabajadores', 'alumno', 'nombre', 'nombres'],
    'surname': ['apellidos', 'apellido', 'primer apellido'],
    'surname2': ['segundo apellido'],
    'dni': ['dni/nie', 'dni/nif', 'dni', 'nie', 'nif', 'documento', 'documento de identidad', 'pasaporte', 'dni / nie', 'dni / nif'],
}


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value).lower()) if not unicodedata.combining(c)).strip()


def clean_document(value):
    return re.sub(r'[\s.\-]', '', value).upper()


def _date_notice(dates):
    return 'Se han separado las fechas del listado (' + ', '.join(sorted(dates)) + '). La fecha de la formación del formulario no cambia.'


def tabular(rows):
    rows = [list(row) for row in rows if any(str(c if c is not None else '').strip() for c in row)]
    if not rows:
        raise ValueError('El listado está vacío.')
    if len(rows) > 501 or max(map(len, rows)) > 40:
        raise ValueError('Admite hasta 500 alumnos y 40 columnas por importación.')
    first = [normalize(c if c is not None else '') for c in rows[0]]
    mapping = {field: next((first.index(a) for a in names if a in first), -1) for field, names in ALIASES.items()}
    has_header = mapping['name'] >= 0 or mapping['dni'] >= 0
    width = max(map(len, rows))
    headers = [str(rows[0][i] or f'Columna {i+1}') if i < len(rows[0]) else f'Columna {i+1}' for i in range(width)] if has_header else [f'Columna {i+1}' for i in range(width)]
    if not has_header:
        if len(rows) > 500:
            raise ValueError('Admite hasta 500 alumnos por importación.')
        scores = [sum(bool(DOCUMENT.fullmatch(str(row[i]).strip())) for row in rows if i < len(row)) for i in range(width)]
        dni = scores.index(max(scores)) if max(scores) else (1 if width > 1 else -1)
        name = next((i for i in range(width) if i != dni), 0)
        mapping = {'name': name, 'surname': -1, 'surname2': -1, 'dni': dni}
    data = [[str(c).strip() if c is not None else '' for c in row] for row in rows[int(has_header):]]
    if mapping['name'] >= 0 and mapping['surname'] < 0 and mapping['surname2'] < 0:
        for row in data:
            if mapping['name'] < len(row):
                row[mapping['name']] = _name(row[mapping['name']])
    dates = {date for row in data for cell in row if not DOCUMENT.fullmatch(cell) for date in DATE.findall(cell)}
    return {'headers': headers, 'rows': data, 'mapping': mapping, 'mode': 'table', 'warnings': [_date_notice(dates)] if dates else []}


def _line(value):
    # WhatsApp's exported timestamps and sender are metadata, not student data.
    value = re.sub(r'^\s*(?:\[[\d/.,:\s\-apmAPM]+\]|\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s*-)[^:\n]+:\s*', '', value)
    value = re.sub(r'^\s*(?:[•*\-]\s+|\d{1,3}[.)]\s+)', '', value)
    return value.strip()


def _header(line):
    text = normalize(line)
    return bool(re.fullmatch(r'(?:dni|nif|nie|trabajador(?:es)?|alumnos?|nombre(?:s)?|completo|apellidos|formacion|prl|fecha|listado|de|y|[/\s:;,.|\-])+', text))


def _name(value):
    value = re.sub(r'\b(?:DNI\s*/\s*(?:NIE|NIF)|DNI|NIE|NIF|NOMBRE(?:\s+Y\s+APELLIDOS)?|TRABAJADOR)\s*:', '', value, flags=re.I)
    value = ' '.join(value.strip(' \t,;:|·-').split())
    parts = value.split(',')
    if len(parts) == 2 and all(p.strip() for p in parts):
        value = parts[1].strip() + ' ' + parts[0].strip()
    return value


def _name_only(line):
    value = _name(line)
    return bool(2 <= len(value.split()) <= 12 and len(value) <= 180 and not any(c.isdigit() for c in value)
                and not re.search(r'[@:/;!?]', value) and not _header(value)
                and not re.match(r'^(?:hola|buenos|buenas|gracias|saludos|adjunto|os envio|te envio)\b', normalize(value)))


def parse_text(text):
    text = unicodedata.normalize('NFKC', text).replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        raise ValueError('Pega el listado de alumnos.')
    if len(text) > 150_000:
        raise ValueError('El texto supera 150.000 caracteres. Divide el listado.')
    # Commas inside APELLIDOS, NOMBRE are not automatically CSV separators.
    delimiter = '\t' if '\t' in text else ';' if ';' in text else ','
    if delimiter in text:
        try:
            table = list(csv.reader(StringIO(text), delimiter=delimiter))
            first = [normalize(c) for c in table[0]]
            known_header = any(c in values for c in first for values in ALIASES.values())
            document_cells = sum(any(DOCUMENT.fullmatch(c.strip()) for c in row) for row in table)
            consistent = len({len(row) for row in table if any(row)}) == 1
            if known_header or consistent and (delimiter != ',' or document_cells) and all(len(row) > 1 for row in table if any(row)):
                return tabular(table)
        except csv.Error:
            pass
    lines = [(i, _line(line)) for i, line in enumerate(text.splitlines(), 1) if line.strip()]
    rows, unparsed, dates = [], [], set()
    used = set()
    for position, (number, line) in enumerate(lines):
        if position in used or not line or _header(line):
            continue
        found_dates = DATE.findall(line)
        matches = list(DOCUMENT.finditer(line))
        if len(matches) != 1:
            # A name followed by its document on the next line is common in messages.
            if not matches and _name_only(line) and position + 1 < len(lines):
                next_number, next_line = lines[position + 1]
                next_matches = list(DOCUMENT.finditer(next_line))
                if next_number == number + 1 and len(next_matches) == 1 and not _name(DATE.sub('', DOCUMENT.sub('', next_line))):
                    rows.append([_name(line), clean_document(next_matches[0][0])])
                    dates.update(DATE.findall(next_line))
                    used.add(position + 1)
                    continue
            unparsed.append({'line': number, 'text': line})
            continue
        match = matches[0]
        name = _name(DATE.sub('', line[:match.start()] + ' ' + line[match.end():]))
        if not name and position + 1 < len(lines):
            next_number, next_line = lines[position + 1]
            if next_number == number + 1 and not DOCUMENT.search(next_line) and _name_only(next_line):
                name = _name(next_line)
                used.add(position + 1)
        if len(name) > 180 or not name or not any(c.isalpha() for c in name):
            unparsed.append({'line': number, 'text': line})
            continue
        dates.update(found_dates)
        rows.append([name, clean_document(match[0])])
    if len(rows) > 500:
        raise ValueError('Admite hasta 500 alumnos por importación.')
    warnings = []
    if dates:
        warnings.append(_date_notice(dates))
    if unparsed:
        warnings.append(f'{len(unparsed)} línea(s) no se han convertido en alumnos. Revísalas: pueden contener alumnos pendientes.')
    return {'headers': ['Nombre y apellidos', 'DNI/NIE'], 'rows': rows,
            'mapping': {'name': 0, 'surname': -1, 'surname2': -1, 'dni': 1},
            'mode': 'text', 'warnings': warnings, 'unparsed': unparsed}
