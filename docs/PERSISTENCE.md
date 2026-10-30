# Activar el guardado permanente

El código soporta dos opciones. Publicar la app no contrata ninguna de ellas y el plan Free de Render sigue siendo temporal si no se conecta una base de datos. El aviso amarillo permanece visible mientras ese sea el caso.

## Opción A: PostgreSQL externo, manteniendo Render Free

Puedes usar un proveedor PostgreSQL administrado, por ejemplo Neon, si su cuenta ofrece un plan gratuito adecuado. Revisa el plan y las condiciones antes de crear recursos; la app no crea ni contrata servicios automáticamente.

1. Crea un proyecto PostgreSQL en el proveedor, preferentemente en una región europea próxima al servicio de Render.
2. Copia la cadena de conexión que facilite el proveedor, con formato `postgresql://…`. Contiene credenciales: introdúcela únicamente en la configuración segura de Render, nunca en Git ni en el chat.
3. En Render → certicoex → Environment, añade **`CERTICOEX_DATABASE_URL`** con esa cadena completa.
4. Guarda y despliega. No necesitas cambiar el usuario ni la contraseña de acceso de la app. `CERTICOEX_EPHEMERAL` puede permanecer en `true`: la app detecta el PostgreSQL conectado y deja de utilizar el disco temporal como almacén principal.
5. Espera a que el nuevo despliegue finalice correctamente y abre la app. El aviso amarillo de almacenamiento temporal debe desaparecer. En el historial, guarda una formación ficticia y comprueba que la recuperas desde otro navegador.

Las conexiones remotas exigen TLS con comprobación de la CA y del nombre del servidor. Una conexión inválida impide superar la comprobación de salud; la app no cambia silenciosamente a almacenamiento temporal. No desactives TLS para resolver un fallo de conexión.

La base de datos guarda empresas, perfil, firma, plantillas, duración de cursos, borradores, historial y los archivos Word/PDF. El disco local solo actúa como caché de trabajo. Los certificados anteriores permanecen inmutables al recuperar una formación para corregirla: generar crea otra entrada y otros documentos. La firma vigente se usa para la nueva emisión, no para cambiar los archivos antiguos.

## Opción B: disco persistente de Render

Esta opción puede requerir un plan de pago. Revisa el precio en Render antes de aceptarlo. No hay ningún disco ni cambio de plan añadido al Blueprint.

1. Con la app detenida o en una ventana de mantenimiento, configura un disco persistente montado en **`/app/data`**.
2. Mantén `CERTICOEX_DATA=/app/data` (es el valor de la imagen Docker).
3. Deja `CERTICOEX_DATABASE_URL` sin configurar y cambia `CERTICOEX_EPHEMERAL` a **`false`** cuando el disco esté realmente montado.
4. Despliega y verifica guardado y recuperación. Se usa SQLite dentro de ese disco. Todos los dispositivos que acceden a esta instalación comparten sus datos.

No marques el almacenamiento como permanente sin montar el disco: la app no puede comprobar desde dentro las condiciones de retención del proveedor. No copies una base SQLite en uso; detén la app antes de hacer una copia de respaldo del disco o usa las herramientas de respaldo del proveedor.

## Datos anteriores y límites

Al abrir la nueva versión se importan las empresas de la agenda de ese navegador sin sobrescribir empresas que ya existan en el servidor. La copia anterior del navegador se conserva. El perfil, la firma y las plantillas de la instalación anterior se importan si sus archivos aún existen en el disco donde arranca la app. Un redepliegue de Render Free puede haberlos eliminado antes de que la nueva versión arranque: esos archivos no pueden recuperarse por código y quizá debas volver a subirlos una vez.

La plantilla inicial se restaura automáticamente. El historial empieza con las emisiones realizadas desde esta versión: los documentos antiguos sin registro no se convierten automáticamente en formaciones históricas.

Las vistas previas caducan a las 24 horas y se limpian al generar. Los documentos del historial no caducan por la app, pero consumen espacio de la base de datos. Revisa las cuotas, las políticas de inactividad y las copias de seguridad del proveedor. El almacenamiento permanente no sustituye una copia de seguridad. Sigue siendo una instalación privada de un solo técnico/perfil, sin separación de usuarios.

## Pruebas de desarrollo

`CERTICOEX_TEST_PDF=1 .venv/bin/python -m unittest discover -s tests -v` prueba las funciones y conversiones reales.

`.venv/bin/python scripts/browser_smoke.py` prueba el navegador: cursos, empresas, borradores incompletos, recuperación, edición, revisión de DNI/NIE, búsqueda en el historial y descargas.

`.venv/bin/python scripts/test_database_persistence.py` usa un PostgreSQL temporal local en Docker, emite documentos, detiene la app, elimina por completo su disco local y arranca otra instancia. Comprueba que empresas, perfil, firma, cursos, borradores, historial y ambos documentos se conservan idénticos, y que una segunda instancia los comparte. No toca una base de datos de producción ni imprime credenciales.
