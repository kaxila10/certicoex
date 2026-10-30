import re

LETTERS = 'TRWAGMYFPDXBNJZSQVHLCKE'


def identity_status(value):
    value = re.sub(r'[\s.\-]', '', value).upper()
    if not value:
        return 'empty'
    if not re.fullmatch(r'(?:\d{8}|[XYZ]\d{7})[A-Z]',value):
        return 'format'
    number = value[:-1]
    if number[0] in 'XYZ':
        number = str('XYZ'.index(number[0])) + number[1:]
    return 'valid' if value[-1] == LETTERS[int(number)%23] else 'letter'


def review_students(students):
    seen = set()
    issues = []
    for index, student in enumerate(students, 1):
        dni = re.sub(r'[\s.\-]', '', student.get('dni','')).upper()
        errors = []
        if not student.get('name','').strip(): errors.append('Falta el nombre')
        status = identity_status(dni)
        if status == 'empty': errors.append('Falta DNI/NIE')
        elif status == 'format': errors.append('Formato de documento poco habitual')
        elif status == 'letter': errors.append('Letra de DNI/NIE incorrecta')
        if dni and dni in seen: errors.append('Documento repetido')
        if dni: seen.add(dni)
        if errors: issues.append({'row':index,'messages':errors})
    return issues
