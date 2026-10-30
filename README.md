# Certicoex

Aplicación personal para generar certificados PRL desde plantillas Word. Importa alumnos desde Excel `.xlsx`, pega listados o introdúcelos a mano, corrige los datos y obtén **un Word y un PDF con todo el lote**. Incluye perfil del técnico con firma en imagen y vista previa del primer alumno.

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

La plantilla del usuario se adaptó **solo en el almacenamiento local ignorado por Git**, conservando la posición flotante de la firma y sustituyendo el nombre fijo del técnico. Para volver a importar ese mismo ejemplo:

```bash
PYTHONPATH=. .venv/bin/python scripts/import_example.py '/ruta/al/ejemplo.docx'
```

Este comando específico espera el rótulo «TÉCNICO EN PRL» y una sola imagen en el cuerpo. No modifica el original. Para otras plantillas, usa la carga normal con los campos anteriores.

## Alumnos y almacenamiento

El Excel usa su hoja activa y la primera fila de encabezados. Se muestran hoja, número de filas y selectores de nombre, apellidos, segundo apellido y DNI/NIE. Puedes pegar celdas con tabuladores o separadores `;` o `,`, o una línea por alumno terminada en su documento. La importación añade al listado existente. Se admite hasta 500 alumnos por lote, para el uso habitual de 2–50.

Se bloquean datos vacíos y documentos repetidos (ignorando puntos, guiones, espacios y mayúsculas). Los formatos poco habituales producen un aviso; no se comprueba la letra de control ni se verifica la identidad.

Perfil, firma, plantillas y documentos se guardan en `.local/data/`, configurable mediante `CERTICOEX_DATA`, fuera de Git. Los alumnos permanecen en memoria de la página y se pierden al recargar. Sus datos aparecen en los documentos. Las descargas caducan a las 24 horas y los archivos caducados se borran al generar el siguiente lote. Para una limpieza inmediata, detén la app y vacía `.local/data/jobs/`.

El uso local puede funcionar sin contraseña, limitado a la interfaz local. El despliegue en Render exige una contraseña de al menos 16 caracteres y protege tanto la interfaz como las APIs y las descargas mediante HTTP Basic sobre HTTPS. No hay separación entre técnicos: el perfil sigue siendo único por instalación. Consulta [la guía de Render](docs/RENDER.md) para preparar una prueba con almacenamiento temporal. El uso compartido con perfiles privados es una fase posterior.

LibreOffice conserva las propiedades del Word, secciones y cabeceras; la app independiza la numeración por alumno. Las fuentes disponibles pueden hacer variar la maquetación frente a Microsoft Word. La instalación cloud incluye Carlito como sustituta de Calibri. Revisa el primer certificado, especialmente con nombres/empresas largos o nuevas plantillas. No se garantiza una página por alumno para cualquier plantilla.

## Pruebas

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
CERTICOEX_TEST_PDF=1 .venv/bin/python -m unittest discover -s tests -v
```

La última orden incluye conversiones reales Word→PDF y requiere LibreOffice. Las pruebas cubren importación de 50 alumnos, marcadores fragmentados, formato y caracteres XML, perfil/firma, duplicados, validación, origen de solicitudes y disponibilidad del Word si falla el PDF. Usan datos ficticios en carpetas temporales.

En una máquina con Playwright y Chromium instalados, `python3 scripts/browser_smoke.py` prueba el flujo completo en un navegador con un perfil temporal, sin modificar el perfil real. Requiere haber cargado al menos una plantilla en `.local/data/templates`. Las capturas y ejemplos generados se guardan en `.local/validation/` y contienen datos ficticios. El navegador automatizado es una herramienta de desarrollo opcional, no una dependencia de la app.

## Agenda de empresas

El selector «Empresa guardada» recupera nombre y CIF. El botón «Guardar empresa y CIF» conserva los datos sin generar documentos; también se guardan automáticamente al generar un lote válido. Se pueden corregir el CIF y guardar de nuevo. Los datos se guardan en el almacenamiento local del navegador (`certicoex.companies.v1`), fuera del almacenamiento temporal de Render. Persisten al recargar y reiniciar el servicio, pero no se comparten entre dispositivos/navegadores y se pierden al borrar los datos del sitio o cerrar una sesión de incógnito. No se guardan alumnos en esta agenda.
