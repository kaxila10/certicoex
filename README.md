# Certicoex

Aplicación personal para generar certificados PRL desde plantillas Word. Importa alumnos desde Excel `.xlsx`, pega listados o introdúcelos a mano, corrige los datos y obtén **un Word y un PDF con todo el lote**. Incluye perfil del técnico con firma en imagen y vista previa de todos los alumnos juntos.

Para usar la descarga de escritorio, consulta `LEEME-PRIMERO.txt`. Con Python 3.12+ y LibreOffice instalados, `INICIAR.bat` (Windows) o `bash INICIAR.command` (Mac/Linux) prepara las dependencias y abre la app en el navegador de ese ordenador. Los lanzadores de Windows/Mac no se han ejecutado en esos sistemas; la aplicación y sus documentos se han validado en Linux.

## Arranque

Requiere Python 3.12 y LibreOffice Writer. Cada tarea cloud ya está aislada; usa el checkout existente sin crear un worktree.

```bash
cd /workspace/certicoex
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
bash scripts/setup_office.sh
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Se accede desde el navegador de la máquina del usuario al puerto local 8000. No requiere Excel ni Microsoft Word. En otros sistemas se instala LibreOffice con su instalador y se puede indicar su ejecutable en `CERTICOEX_LIBREOFFICE`. El script local soporta Debian 13 sin permisos de administrador. En el entorno cloud, servidores, navegadores y conversiones pueden necesitar ejecución fuera del sandbox por el uso de sockets/IPC locales.

## Plantillas

Sube un `.docx` desde «Añadir plantilla», con estos campos, respetando las llaves:

| Campo | Contenido |
| --- | --- |
| `{NOMBRE}` | Nombre y apellidos del alumno; puede repetirse |
| `{DNI/NIE}` | Documento del alumno |
| `{EMPRESA}` / `{CIF}` | Empresa que recibe la formación |
| `{HORAS}` / `{FECHA}` | Duración y fecha de formación |
| `{TECNICO}` | Nombre del perfil |
| `{FIRMA}` | Imagen de firma; colocar en un párrafo propio |

Los campos del alumno y la firma deben estar en el cuerpo, fuera de cabeceras y pies. Retira nombres y firmas fijos de otras personas. El título, temario, logotipos y estructura vienen del Word. La firma en imagen no es una firma electrónica con certificado digital. Se usa un curso y una empresa por lote.

La plantilla del usuario se adaptó conservando la posición flotante de la firma y sustituyendo el nombre fijo del técnico. Una copia sin la firma anterior, sin datos de alumnos y sin metadatos de autor se distribuye en `defaults/`; los datos personales y las plantillas adicionales se guardan fuera de Git. Para volver a importar ese mismo ejemplo:

```bash
PYTHONPATH=. .venv/bin/python scripts/import_example.py '/ruta/al/ejemplo.docx'
```

Este comando específico espera el rótulo «TÉCNICO EN PRL» y una sola imagen en el cuerpo. No modifica el original. Para otras plantillas, usa la carga normal con los campos anteriores.

## Alumnos y almacenamiento

El Excel usa su hoja activa y la primera fila de encabezados. Se muestran hoja, número de filas y selectores de nombre, apellidos, segundo apellido y DNI/NIE. Puedes pegar celdas con tabuladores o separadores `;` o `,`, o una línea por alumno terminada en su documento. La importación añade al listado existente. Se admite hasta 500 alumnos por lote, para el uso habitual de 2–50.

Se bloquean datos vacíos y documentos repetidos (ignorando puntos, guiones, espacios y mayúsculas). Los formatos poco habituales producen un aviso; no se comprueba la letra de control ni se verifica la identidad.

Perfil, firma, empresas, cursos, borradores, historial y documentos se guardan en una base de datos. Por defecto se usa SQLite en `.local/data/certicoex.sqlite3` (carpeta configurable con `CERTICOEX_DATA`). Si se configura `CERTICOEX_DATABASE_URL`, se usa PostgreSQL externo con TLS verificado y el disco local actúa como caché. Los datos sin guardar del formulario siguen perdiéndose al recargar: pulsa «Guardar borrador» para conservar una formación incompleta. Las vistas previas caducan a las 24 horas; los documentos del historial no caducan por la app. No se borran del historial vaciando la caché. Consulta [guardado permanente](docs/PERSISTENCE.md) para activar persistencia real en Render y conocer sus límites.

El uso local puede funcionar sin contraseña, limitado a la interfaz local. El despliegue en Render exige una contraseña de al menos 16 caracteres y protege tanto la interfaz como las APIs y las descargas mediante HTTP Basic sobre HTTPS. No hay separación entre técnicos: el perfil sigue siendo único por instalación. Consulta [la guía de Render](docs/RENDER.md) para preparar una prueba con almacenamiento temporal. El uso compartido con perfiles privados es una fase posterior.

LibreOffice conserva las propiedades del Word, secciones y cabeceras; la app independiza la numeración por alumno. Las fuentes disponibles pueden hacer variar la maquetación frente a Microsoft Word. La instalación cloud incluye Carlito como sustituta de Calibri. Revisa todos los certificados, especialmente con nombres/empresas largos o nuevas plantillas. No se garantiza una página por alumno para cualquier plantilla.

## Pruebas

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
CERTICOEX_TEST_PDF=1 .venv/bin/python -m unittest discover -s tests -v
```

La última orden incluye conversiones reales Word→PDF y requiere LibreOffice. Las pruebas cubren importación de 50 alumnos, marcadores fragmentados, formato y caracteres XML, perfil/firma, duplicados, validación, origen de solicitudes y disponibilidad del Word si falla el PDF. Usan datos ficticios en carpetas temporales.

En una máquina con Playwright y Chromium instalados, `python3 scripts/browser_smoke.py` prueba el flujo completo en un navegador con un perfil temporal, sin modificar el perfil real. Requiere haber cargado al menos una plantilla en `.local/data/templates`. Las capturas y ejemplos generados se guardan en `.local/validation/` y contienen datos ficticios. El navegador automatizado es una herramienta de desarrollo opcional, no una dependencia de la app.

## Agenda de empresas

El selector «Empresa guardada» recupera nombre y CIF. El botón «Guardar empresa y CIF» conserva los datos sin generar documentos; también se guardan automáticamente al generar un lote válido. Se pueden corregir el CIF y guardar de nuevo. La agenda se guarda en la base de datos de la app y se comparte entre los dispositivos que acceden a esta instalación. Se importa la agenda anterior del navegador sin sobrescribir empresas actuales y se conserva una copia local de compatibilidad. En Render Free, conectar PostgreSQL externo o montar un disco persistente sigue siendo un requisito para sobrevivir a los reinicios.

## Borradores, cursos e historial

- «Guardar borrador» admite empresa, fecha, horas o alumnos incompletos. «Borradores» permite recuperar, actualizar o eliminar cada borrador. No se guardan automáticamente los cambios sin pulsar el botón.
- «Configurar curso» guarda el nombre y la duración habitual de su plantilla. Al seleccionar ese curso se rellena la duración, editable para cada formación.
- La tabla revisa campos vacíos, documentos duplicados, formato y letra de control de DNI/NIE. Vacíos y duplicados bloquean la generación; formato y letra muestran avisos para revisar y permiten documentos especiales. No verifica identidades.
- Cada lote generado se archiva en «Historial», con su fecha, empresa, alumnos, técnico, Word y PDF (cuando se pudo convertir). Se puede buscar por empresa, alumno o documento y filtrar por fecha. Se muestran 50 entradas por página. Las vistas previas no se archivan.
- «Recuperar para corregir» copia la formación al formulario y genera una versión nueva con el perfil actual. Los documentos originales se conservan íntegros.

La persistencia PostgreSQL se prueba también con `.venv/bin/python scripts/test_database_persistence.py`, eliminando todo el disco local entre dos instancias de la app. La prueba no usa ninguna base de datos real del usuario.

Los archivos descargados usan el nombre del curso, la empresa hasta la primera coma y la fecha de formación en formato DD-MM-AAAA, por ejemplo `Prevención agrícola - Hortícola del Sur - 30-10-2026.pdf`. Se conservan los acentos y se eliminan caracteres incompatibles con nombres de archivo. La vista previa usa el mismo lote completo y no crea entradas en el historial.
