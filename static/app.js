const $ = id => document.getElementById(id);
let templates = [], profile = {}, students = [], imported = null, busy = false, revision = 0;
let importPreview = [];
let importReading = false;
const temporaryKeys = new Set();
let toastTimer;
const COMPANY_KEY = 'certicoex.companies.v1';
let companies = [];
let currentDraftId = null, historyOffset = 0;
let signedInUser = null, resetUserId = null;
function showCompanies(selected = '') {
  $('saved-company').replaceChildren(new Option('Nueva empresa / introducir datos', ''));
  companies.sort((a,b) => a.name.localeCompare(b.name,'es')).forEach(c => $('saved-company').add(new Option(`${c.name} · ${c.cif}`,c.id)));
  $('saved-company').value = selected;
}
async function loadCompanies(selected = '') {
  companies = await api('/api/companies'); showCompanies(selected);
}
async function migrateCompanies() {
  try {
    const old = JSON.parse(localStorage.getItem(COMPANY_KEY) || '[]');
    if (Array.isArray(old) && old.length) await api('/api/companies/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(old)});
  } catch { toast('La agenda anterior de este navegador no se pudo importar. Sus datos se conservan.'); }
  await loadCompanies();
}
async function saveCompany(notify = true) {
  if (!$('company').reportValidity() || !$('cif').reportValidity()) return;
  const entry = await api('/api/companies',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:$('saved-company').value || null,name:$('company').value.trim(),cif:$('cif').value.trim()})});
  await loadCompanies(entry.id);
  try { localStorage.setItem(COMPANY_KEY,JSON.stringify(companies)); } catch {}
  $('company-save-status').textContent = 'Empresa guardada en la app';
  if (notify) toast('Empresa y CIF guardados.');
}
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 6500); }
async function api(url, options = {}) {
  if (signedInUser) options.headers = {...options.headers, 'X-Certicoex-User': signedInUser.id};
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); } catch { throw new Error('No se puede leer la respuesta del servidor.'); }
  if (!response.ok) {
    if (data.code === 'session_changed') { location.reload(); throw new Error('La cuenta activa ha cambiado.'); }
    if (response.status === 401) { location.replace('/login'); throw new Error('Inicia sesión de nuevo.'); }
    let message = data.detail;
    if (Array.isArray(message)) message = message.map(e => `${e.loc.slice(1).join(' · ')}: ${e.msg}`).join('\n');
    throw new Error(message || 'No se pudo completar la operación.');
  }
  return data;
}
function run(task) { return task().catch(error => toast(error.message)); }
function dirty() { revision++; if (currentDraftId) $('draft-status').textContent = 'Cambios sin guardar'; $('downloads').hidden = true; $('generation-status').textContent = ''; }
function summary() {
  $('summary-template').textContent = templates.find(t => t.id === $('template').value)?.name || 'Sin seleccionar';
  $('summary-company').textContent = $('company').value.trim() || 'Por completar';
  $('summary-count').textContent = `${students.length} ${students.length === 1 ? 'persona' : 'personas'}`;
  $('student-count').textContent = students.length;
}
function updateProfile() {
  $('technician-name').value = profile.name || '';
  $('signature-preview').hidden = !profile.has_signature;
  if (profile.has_signature) $('signature-preview').src = '/api/profile/signature?v=' + Date.now();
  const ready = profile.name && profile.has_signature;
  $('profile-status').classList.toggle('ready', !!ready);
  $('profile-status').querySelector('b').textContent = ready ? profile.name : 'Configura tu perfil';
  $('profile-status').querySelector('small').textContent = ready ? 'Firma guardada · Cambiar perfil' : 'Añade tu nombre y tu firma.';
}
async function loadTemplates(selected) {
  templates = await api('/api/templates');
  const old = selected || $('template').value;
  $('template').replaceChildren(new Option('Selecciona una plantilla', ''));
  templates.forEach(t => $('template').add(new Option(t.name, t.id)));
  $('template').value = templates.some(t => t.id === old) ? old : templates.length === 1 ? templates[0].id : '';
  if (selected === undefined) $('hours').value = templates.find(t => t.id === $('template').value)?.hours ?? '';
  summary();
}
function cleanDni(value) { return value.toUpperCase().replace(/[\s.\-]/g, ''); }
function validateRows(list, body, status, existing = []) {
  const counts = new Map();
  [...list, ...existing].forEach(s => { const dni = cleanDni(s.dni); if (dni) counts.set(dni, (counts.get(dni) || 0) + 1); });
  let missing = 0, duplicate = 0, unusual = 0, wrongLetter = 0;
  [...body.children].forEach((row, i) => {
    const s = list[i], inputs = row.querySelectorAll('input');
    const emptyName = !s.name.trim(), emptyDni = !s.dni.trim(), repeated = (counts.get(cleanDni(s.dni)) || 0) > 1;
    inputs[0].classList.toggle('invalid', emptyName); inputs[1].classList.toggle('invalid', emptyDni || repeated);
    inputs[0].setAttribute('aria-invalid', String(emptyName)); inputs[1].setAttribute('aria-invalid', String(emptyDni || repeated));
    if (emptyName || emptyDni) missing++;
    if (repeated) duplicate++;
    const dni = cleanDni(s.dni), standard = /^(\d{8}|[XYZ]\d{7})[A-Z]$/.test(dni);
    let badLetter = false;
    if (standard) {
      const number = dni.slice(0,-1).replace(/^[XYZ]/,c => String('XYZ'.indexOf(c)));
      badLetter = 'TRWAGMYFPDXBNJZSQVHLCKE'[Number(number)%23] !== dni.slice(-1);
    }
    inputs[1].classList.toggle('input-warning',!emptyDni && !repeated && (!standard || badLetter));
    inputs[1].title = badLetter ? 'La letra de control del DNI/NIE no coincide.' : '';
    if (!emptyDni && !standard) unusual++;
    if (badLetter) wrongLetter++;
  });
  const messages = [];
  if (missing) messages.push(`${missing} fila(s) con datos incompletos.`);
  if (duplicate) messages.push(`${duplicate} fila(s) con DNI/NIE repetido.`);
  if (unusual) messages.push(`${unusual} documento(s) con formato poco habitual: revísalos. Comprueba que son los documentos correctos.`);
  if (wrongLetter) messages.push(`${wrongLetter} documento(s) con letra de control incorrecta. Revísalos antes de generar.`);
  status.classList.toggle('review-ok', messages.length === 0);
  status.textContent = messages.join(' ') || (list.length ? 'Listado revisado: sin datos vacíos, duplicados ni errores de letra de DNI/NIE.' : '');
  return !missing && !duplicate;
}
function validateStudents() {
  const valid = validateRows(students, $('student-rows'), $('student-validation'));
  if (imported) validateImport();
  return valid;
}
function renderStudents() {
  $('student-rows').replaceChildren();
  students.forEach((student, i) => {
    const row = document.createElement('tr');
    const index = document.createElement('td'); index.textContent = i + 1; row.append(index);
    ['name', 'dni'].forEach(field => {
      const td = document.createElement('td'), input = document.createElement('input');
      input.value = student[field]; input.maxLength = field === 'name' ? 180 : 30;
      input.setAttribute('aria-label', `${field === 'name' ? 'Nombre' : 'DNI/NIE'} del alumno ${i + 1}`);
      input.autocomplete = 'off'; input.spellcheck = false;
      input.addEventListener('input', () => { student[field] = input.value; dirty(); validateStudents(); });
      td.append(input); row.append(td);
    });
    const td = document.createElement('td'), remove = document.createElement('button');
    remove.textContent = '×'; remove.className = 'remove-row'; remove.setAttribute('aria-label', `Eliminar alumno ${i+1}`);
    remove.onclick = () => { students.splice(i, 1); dirty(); renderStudents(); }; td.append(remove); row.append(td);
    $('student-rows').append(row);
  });
  $('student-table-wrap').hidden = !students.length; $('empty-students').hidden = !!students.length;
  summary(); validateStudents();
}
function showImport(data) {
  imported = data;
  $('mapping-panel').hidden = false;
  $('import-info').textContent = `${data.rows.length} alumnos detectados${data.sheet ? ` en la hoja «${data.sheet}»` : ''}${data.page_count ? ` · ${data.page_count} página(s)` : ''}. Revisa y corrige los datos antes de añadirlos al listado.`;
  $('column-mapping').hidden = data.mode === 'text';
  $('import-warnings').replaceChildren();
  (data.warnings || []).forEach(message => { const p = document.createElement('p'); p.textContent = message; $('import-warnings').append(p); });
  $('import-unparsed').hidden = !data.unparsed?.length;
  $('unparsed-lines').textContent = (data.unparsed || []).map(line => `Línea ${line.line}: ${line.text}`).join('\n');
  $('import-source').hidden = !data.extracted_text;
  $('extracted-text').value = data.extracted_text || '';
  ['name', 'dni', 'surname', 'surname2'].forEach(field => {
    const select = $('map-' + field);
    select.replaceChildren(new Option(field.startsWith('surname') ? 'No usar / ya incluido' : 'Selecciona columna', '-1'));
    data.headers.forEach((header, i) => select.add(new Option(header, String(i))));
    select.value = String(data.mapping[field]);
  });
  importSample();
}
function mappedStudents() {
  const map = Object.fromEntries(['name', 'dni', 'surname', 'surname2'].map(field => [field, Number($('map-'+field).value)]));
  if (map.name < 0 || map.dni < 0 || map.name === map.dni) throw new Error('Selecciona columnas diferentes para nombre y DNI/NIE.');
  const selected = Object.values(map).filter(i => i >= 0);
  if (new Set(selected).size !== selected.length) throw new Error('Cada columna debe usarse una sola vez.');
  return imported.rows.map(row => ({ name: [row[map.name], row[map.surname], row[map.surname2]].filter(Boolean).join(' '), dni: row[map.dni] || '' }));
}
function importSample() {
  try {
    importPreview = mappedStudents();
    $('import-sample').textContent = 'Puedes editar o eliminar cualquier fila. Los documentos con avisos se conservan tal como se han leído.';
    renderImportPreview();
  } catch (error) {
    importPreview = []; $('import-rows').replaceChildren();
    $('import-sample').textContent = error.message; $('apply-import').disabled = true;
  }
}
function validateImport() {
  const valid = validateRows(importPreview, $('import-rows'), $('import-validation'), students);
  const tooMany = students.length + importPreview.length > 500;
  if (tooMany) $('import-validation').textContent += ' El lote admite hasta 500 alumnos.';
  $('apply-import').disabled = !valid || !importPreview.length || tooMany;
  $('apply-import').textContent = `Añadir ${importPreview.length} alumno(s) al listado`;
}
function renderImportPreview() {
  $('import-rows').replaceChildren();
  importPreview.forEach((student, i) => {
    const row = document.createElement('tr'), number = document.createElement('td');
    number.textContent = i + 1; row.append(number);
    ['name', 'dni'].forEach(field => {
      const td = document.createElement('td'), input = document.createElement('input');
      input.value = student[field]; input.maxLength = field === 'name' ? 180 : 30;
      input.autocomplete = 'off'; input.spellcheck = false;
      input.setAttribute('aria-label', `${field === 'name' ? 'Nombre' : 'DNI/NIE'} a importar ${i + 1}`);
      input.oninput = () => { student[field] = input.value; validateImport(); };
      td.append(input); row.append(td);
    });
    const td = document.createElement('td'), remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'remove-row'; remove.textContent = '×';
    remove.setAttribute('aria-label', `Quitar de la importación al alumno ${i + 1}`);
    remove.onclick = () => { importPreview.splice(i, 1); renderImportPreview(); };
    td.append(remove); row.append(td); $('import-rows').append(row);
  });
  validateImport();
}
async function readImport(task, message) {
  if (importReading) return;
  importReading = true; imported = null; importPreview = []; $('mapping-panel').hidden = true;
  const controls = ['parse-paste', 'excel-file', 'document-file', 'paste-text', 'apply-import', 'cancel-import', 'clear-temporary'];
  controls.forEach(id => $(id).disabled = true);
  $('import-reading').textContent = message;
  try { showImport(await task()); }
  finally {
    controls.forEach(id => $(id).disabled = false);
    $('import-reading').textContent = '';
    importReading = false;
    if (imported) validateImport();
    else $('apply-import').disabled = true;
  }
}
async function importExcel(file) {
  if (!file) return;
  const data = new FormData(); data.append('file', file);
  try { await readImport(() => api('/api/import/excel', {method:'POST', body:data}), 'Leyendo Excel…'); }
  finally { $('excel-file').value = ''; }
}
async function importDocument(file) {
  if (!file) return;
  const data = new FormData(); data.append('file', file);
  try { await readImport(() => api('/api/import/document', {method:'POST', body:data}), 'Leyendo el documento… Las fotos y los PDF escaneados pueden tardar un poco.'); }
  finally { $('document-file').value = ''; }
}
function setTab(selected) {
  ['paste', 'document', 'excel'].forEach(tab => {
    $(tab + '-panel').hidden = tab !== selected;
    $('tab-' + tab).classList.toggle('selected', tab === selected);
    $('tab-' + tab).setAttribute('aria-selected', String(tab === selected));
  });
}
function payload(preview) {
  if (!$('course-form').reportValidity()) return null;
  if (!profile.name || !profile.has_signature) { $('profile-dialog').showModal(); toast('Guarda tu nombre y firma antes de generar.'); return null; }
  if (!students.length) throw new Error('Añade al menos un alumno.');
  if (!validateStudents()) throw new Error('Corrige los datos incompletos o repetidos del listado.');
  return {template_id:$('template').value, company:$('company').value.trim(), cif:$('cif').value.trim(), hours:Number($('hours').value), course_date:$('course-date').value, students, preview};
}
async function generate(preview) {
  if (busy) return;
  const data = payload(preview); if (!data) return;

  busy = true; const startedAt = revision;
  ['preview', 'generate', 'clear-temporary'].forEach(id => $(id).disabled = true);
  $('downloads').hidden = true;
  $('generation-status').textContent = preview ? 'Preparando la vista previa…' : `Generando ${students.length} certificados. El PDF puede tardar unos instantes…`;
  try {
    const result = await api('/api/generate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    if (result.temporary_minutes) temporaryKeys.add(result.word.split('/')[3]);
    if (!preview) { await loadCompanies(); const entry = companies.find(c => cleanDni(c.cif) === cleanDni(data.cif)); if (entry) showCompanies(entry.id); try { localStorage.setItem(COMPANY_KEY,JSON.stringify(companies)); } catch {} }
    if (startedAt !== revision) { $('generation-status').textContent = 'Los datos cambiaron durante la generación. Vuelve a generar para usar los datos actuales.'; return; }
    if (preview && result.pdf) {
      $('pdf-preview').src = result.pdf + '?inline=true'; $('preview-word').href = result.word;
      $('preview-dialog').showModal(); $('generation-status').textContent = 'Vista previa de todos los alumnos preparada.';
    } else {
      $('download-word').href = result.word; $('download-pdf').hidden = !result.pdf;
      if (result.pdf) $('download-pdf').href = result.pdf;
      const retention = result.temporary_minutes ? `Descargas temporales durante un máximo de ${result.temporary_minutes} minutos. Guarda tus documentos en Google Drive y pulsa «Borrar alumnos y archivos temporales».` : preview ? 'Vista previa disponible durante 24 horas.' : 'Documentos conservados en el historial.';
      $('result-info').textContent = `${result.count} certificado(s)${result.pages ? ` · ${result.pages} página(s) en el PDF` : ''}. ${retention}`;
      $('downloads').querySelector('h3').textContent = preview ? 'Prueba en Word disponible' : 'Tu lote está listo';
      $('downloads').hidden = false;
      $('generation-status').textContent = result.warning || 'Documentos generados correctamente.';
    }
    if (result.warning) toast(result.warning);
  } finally { busy = false; ['preview', 'generate', 'clear-temporary'].forEach(id => $(id).disabled = false); }
}

$('clear-temporary').onclick = () => run(async () => {
  if (busy || importReading || !confirm('¿Has guardado los documentos que necesitas? Se borrarán los alumnos de esta pantalla y sus archivos temporales de esta sesión.')) return;
  $('clear-temporary').disabled = true;
  try {
    await Promise.all([...temporaryKeys].map(async key => { await api(`/api/files/${key}`, {method:'DELETE'}); temporaryKeys.delete(key); }));
    students = []; imported = null; importPreview = []; currentDraftId = null;
    ['paste-text', 'extracted-text', 'excel-file', 'document-file'].forEach(id => $(id).value = '');
    ['import-rows', 'import-warnings'].forEach(id => $(id).replaceChildren());
    $('unparsed-lines').textContent = ''; $('import-info').textContent = ''; $('import-sample').textContent = ''; $('import-validation').textContent = '';
    $('mapping-panel').hidden = true; $('pdf-preview').removeAttribute('src');
    ['download-word', 'download-pdf', 'preview-word'].forEach(id => $(id).removeAttribute('href'));
    if ($('preview-dialog').open) $('preview-dialog').close();
    $('result-info').textContent = ''; $('draft-status').textContent = ''; dirty(); renderStudents();
    toast('Alumnos y archivos temporales de esta sesión eliminados.');
  } finally { $('clear-temporary').disabled = false; }
});
['open-profile', 'profile-shortcut', 'profile-status'].forEach(id => $(id).onclick = () => $('profile-dialog').showModal());
$('open-template').onclick = () => $('template-dialog').showModal();
document.querySelectorAll('[data-close]').forEach(button => button.onclick = () => $(button.dataset.close).close());
$('profile-form').onsubmit = event => { event.preventDefault(); run(async () => {
  const data = new FormData(); data.append('name', $('technician-name').value);
  if ($('signature').files[0]) data.append('signature', $('signature').files[0]);
  profile = await api('/api/profile', {method:'POST', body:data}); updateProfile(); dirty();
  $('signature').value = ''; $('profile-dialog').close(); toast('Perfil del técnico guardado.');
}); };
$('template-form').onsubmit = event => { event.preventDefault(); run(async () => {
  const data = new FormData(); data.append('name', $('template-name').value); data.append('file', $('template-file').files[0]);
  const result = await api('/api/templates', {method:'POST', body:data});
  if ($('template-hours').value) await api(`/api/templates/${result.id}/settings`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:result.name,hours:Number($('template-hours').value)})});
  await loadTemplates(result.id); $('hours').value = templates.find(t => t.id === result.id)?.hours ?? ''; dirty();
  $('template-dialog').close(); $('template-form').reset(); toast('Plantilla añadida.');
}); };
$('course-form').onsubmit = e => e.preventDefault();
$('save-company').onclick = () => run(() => saveCompany());
$('saved-company').onchange = () => {
  const selected = companies.find(e => e.id === $('saved-company').value);
  $('company').value = selected?.name || '';
  $('cif').value = selected?.cif || '';
  $('company-save-status').textContent = selected ? 'Datos de la empresa recuperados' : '';
  dirty(); summary();
};
$('company').addEventListener('input', () => {
  const selected = companies.find(e => e.id === $('saved-company').value);
  if (selected && selected.name !== $('company').value) {
    $('saved-company').value = '';
    $('cif').value = '';
  }
  $('company-save-status').textContent = '';
});
$('cif').addEventListener('input', () => { $('company-save-status').textContent = ''; });
['company', 'cif', 'hours', 'course-date', 'template'].forEach(id => $(id).addEventListener('input', () => {dirty(); summary();}));
$('add-student').onclick = () => { students.push({name:'', dni:''}); dirty(); renderStudents(); $('student-rows').lastChild.querySelector('input').focus(); };
$('clear-students').onclick = () => { if (confirm('¿Vaciar el listado de alumnos?')) { students = []; dirty(); renderStudents(); } };
['paste', 'document', 'excel'].forEach(tab => $('tab-' + tab).onclick = () => setTab(tab));
$('excel-file').onchange = e => run(() => importExcel(e.target.files[0]));
$('document-file').onchange = e => run(() => importDocument(e.target.files[0]));
$('drop-zone').onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('excel-file').click(); } };
['dragover', 'dragleave', 'drop'].forEach(type => $('drop-zone').addEventListener(type, e => {
  e.preventDefault(); $('drop-zone').classList.toggle('dragging', type === 'dragover');
  if (type === 'drop') run(() => importExcel(e.dataTransfer.files[0]));
}));
$('parse-paste').onclick = () => run(() => readImport(() => api('/api/import/paste', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:$('paste-text').value})}), 'Separando nombres y documentos…'));
['name', 'dni', 'surname', 'surname2'].forEach(field => $('map-'+field).onchange = importSample);
$('add-import-student').onclick = () => { importPreview.push({name:'', dni:''}); renderImportPreview(); $('import-rows').lastChild.querySelector('input').focus(); };
$('cancel-import').onclick = () => { imported = null; importPreview = []; $('mapping-panel').hidden = true; };
$('correct-extracted').onclick = () => {
  $('paste-text').value = $('extracted-text').value; setTab('paste');
  imported = null; importPreview = []; $('mapping-panel').hidden = true; $('paste-text').focus();
  toast('Corrige el texto y pulsa «Leer listado» para volver a revisarlo.');
};
$('apply-import').onclick = () => run(async () => {
  validateImport(); if ($('apply-import').disabled) return;
  const rows = importPreview.map(s => ({name:s.name.trim(), dni:s.dni.trim()}));
  students.push(...rows); imported = null; importPreview = []; $('mapping-panel').hidden = true; $('paste-text').value = ''; dirty(); renderStudents(); toast(`${rows.length} alumnos añadidos. Revisa sus datos.`);
});
$('preview').onclick = () => run(() => generate(true)); $('generate').onclick = () => run(() => generate(false));
run(async () => {
  const actor = await api('/api/auth/me'); signedInUser = actor;
  const [p, , health] = await Promise.all([api('/api/profile'), loadTemplates(), api('/api/health'), migrateCompanies()]);
  profile = p; updateProfile(); $('trial-notice').hidden = !health.storage_ephemeral;
  signedInUser = actor;
  $('account-bar').hidden = !actor.accounts_enabled;
  $('account-username').textContent = actor.username;
  $('open-users').hidden = !actor.accounts_enabled || actor.role !== 'admin';
  $('archive-tools').hidden = !health.retains_students;
  $('temporary-tools').hidden = !!health.retains_students;
  if (!health.retains_students) {
    $('storage-footer').textContent = 'Alumnos sin historial · Descargas temporales · Guarda los certificados en tu Google Drive.';
  }
});

window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
window.addEventListener('storage', event => { if (event.key === 'certicoex.auth-change') location.reload(); });
$('logout').onclick = () => run(async () => {
  if (busy || importReading) return toast('Espera a que termine la operación antes de cerrar sesión.');
  if (students.length && !confirm('¿Cerrar sesión? Se vaciará el listado y se eliminarán tus descargas temporales. Guarda los documentos que necesites.')) return;
  await api('/api/auth/logout', {method:'POST'});
  try { localStorage.setItem('certicoex.auth-change', String(Date.now())); } catch {}
  location.replace('/login');
});
$('open-account').onclick = () => { $('password-form').reset(); $('password-dialog').showModal(); };
$('password-form').onsubmit = event => { event.preventDefault(); run(async () => {
  if ($('new-password').value !== $('repeat-password').value) throw new Error('Las contraseñas nuevas no coinciden.');
  const button = $('password-save'); button.disabled = true;
  try {
    await api('/api/auth/password', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({current_password:$('current-password').value,new_password:$('new-password').value})});
    $('password-form').reset(); $('password-dialog').close(); toast('Contraseña actualizada. Las otras sesiones de tu cuenta han quedado cerradas.');
  } finally { button.disabled = false; }
}); };

async function loadUsers() {
  const records = await api('/api/users'); $('users-list').replaceChildren();
  records.forEach(record => {
    const {card, actions} = trainingCard(record.username, `${record.role === 'admin' ? 'Administrador' : 'Técnico'} · ${record.active ? 'Activo' : 'Desactivado'}`);
    if (record.id !== signedInUser.id) {
      actions.append(action(record.active ? 'Desactivar' : 'Activar', async () => {
        if (!confirm(`¿${record.active ? 'Desactivar' : 'Activar'} la cuenta «${record.username}»?`)) return;
        await api(`/api/users/${record.id}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({active:!record.active})});
        await loadUsers();
      }), action('Restablecer contraseña', async () => {
        resetUserId = record.id; $('reset-user-label').textContent = record.username;
        $('reset-password-form').reset(); $('reset-password').type = 'password'; $('reset-password-dialog').showModal();
      }));
    }
    $('users-list').append(card);
  });
}
$('open-users').onclick = () => run(async () => { await loadUsers(); $('users-dialog').showModal(); });
$('new-user-form').onsubmit = event => { event.preventDefault(); run(async () => {
  const button = $('create-user'); button.disabled = true;
  try {
    const record = await api('/api/users', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('new-username').value,password:$('initial-password').value})});
    $('new-user-form').reset(); $('initial-password').type = 'password'; await loadUsers();
    toast(`Cuenta «${record.username}» creada. El técnico podrá guardar su nombre y firma en «Mi perfil».`);
  } finally { button.disabled = false; }
}); };
function randomPassword(input) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  input.value = [...crypto.getRandomValues(new Uint8Array(20))].map(n => alphabet[n & 63]).join(''); input.type = 'text';
}
$('generate-password').onclick = () => randomPassword($('initial-password'));
$('generate-reset-password').onclick = () => randomPassword($('reset-password'));
$('reset-password-form').onsubmit = event => { event.preventDefault(); run(async () => {
  const button = $('reset-password-save'); button.disabled = true;
  try {
    await api(`/api/users/${resetUserId}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:$('reset-password').value})});
    $('reset-password-form').reset(); $('reset-password-dialog').close(); toast('Contraseña restablecida. Las sesiones anteriores de esa cuenta se han cerrado.');
  } finally { button.disabled = false; }
}); };

$('template').addEventListener('change', () => { $('hours').value = templates.find(t => t.id === $('template').value)?.hours ?? ''; dirty(); });
$('open-course').onclick = () => {
  const course = templates.find(t => t.id === $('template').value);
  if (!course) return toast('Selecciona primero un curso.');
  $('course-name').value = course.name; $('default-hours').value = course.hours ?? ''; $('course-dialog').showModal();
};
$('course-settings-form').onsubmit = event => { event.preventDefault(); run(async () => {
  const id = $('template').value;
  await api(`/api/templates/${id}/settings`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('course-name').value,hours:$('default-hours').value ? Number($('default-hours').value) : null})});
  await loadTemplates(id); $('hours').value = templates.find(t => t.id === id).hours ?? '';
  dirty(); $('course-dialog').close(); toast('Curso y duración habitual guardados.');
}); };
function trainingData() {
  return {template_id:$('template').value,company:$('company').value,cif:$('cif').value,hours:$('hours').value,course_date:$('course-date').value,students:students.map(s => ({...s}))};
}
function restoreTraining(data, id = null) {
  if ((students.length || $('company').value) && !confirm('¿Sustituir los datos del formulario por esta formación?')) return false;
  if (data.template_id && !templates.some(t => t.id === data.template_id)) { toast('La plantilla de esta formación no está disponible. Añádela antes de recuperar sus datos.'); return false; }
  $('template').value = data.template_id || ''; $('company').value = data.company || ''; $('cif').value = data.cif || '';
  $('hours').value = data.hours ?? ''; $('course-date').value = data.course_date || '';
  $('saved-company').value = companies.find(c => cleanDni(c.cif) === cleanDni(data.cif || ''))?.id || '';
  students = (data.students || []).map(s => ({name:s.name || '',dni:s.dni || ''})); currentDraftId = id;
  dirty(); renderStudents(); $('draft-status').textContent = id ? 'Borrador recuperado' : 'Copia recuperada: las nuevas emisiones usarán tu perfil actual';
  return true;
}
$('save-draft').onclick = () => run(async () => {
  const snapshot = trainingData(), started = revision;
  $('save-draft').disabled = true;
  try {
    const result = await api(currentDraftId ? `/api/drafts/${currentDraftId}` : '/api/drafts',{method:currentDraftId ? 'PUT' : 'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(snapshot)});
    currentDraftId = result.id; $('draft-status').textContent = started === revision ? 'Borrador guardado' : 'Borrador guardado; hay cambios posteriores sin guardar'; toast('Borrador guardado.');
  } finally { $('save-draft').disabled = false; }
});
function trainingCard(title, detail) {
  const card = document.createElement('section'); card.className = 'saved-training';
  const heading = document.createElement('h3'); heading.textContent = title;
  const info = document.createElement('p'); info.textContent = detail;
  const actions = document.createElement('div'); actions.className = 'saved-actions'; card.append(heading,info,actions);
  return {card,actions};
}
function action(label, task) { const button = document.createElement('button'); button.className = 'button secondary small'; button.textContent = label; button.onclick = () => run(task); return button; }
async function loadDrafts() {
  const records = await api('/api/drafts'); $('draft-list').replaceChildren();
  if (!records.length) $('draft-list').textContent = 'Todavía no tienes borradores.';
  records.forEach(record => {
    const d = record.data, {card,actions} = trainingCard(d.company || 'Borrador sin empresa', `${d.course_date || 'Sin fecha'} · ${d.students.length} alumno(s) · Guardado ${new Date(record.updated).toLocaleString('es-ES')}`);
    actions.append(action('Recuperar',async () => { if (restoreTraining(d,record.id)) $('drafts-dialog').close(); }),action('Eliminar',async () => {
      if (!confirm('¿Eliminar este borrador?')) return;
      await api(`/api/drafts/${record.id}`,{method:'DELETE'}); if (currentDraftId === record.id) currentDraftId = null; await loadDrafts();
    })); $('draft-list').append(card);
  });
}
$('open-drafts').onclick = () => run(async () => { await loadDrafts(); $('drafts-dialog').showModal(); });
async function loadHistory() {
  const params = new URLSearchParams({q:$('history-search').value,course_date:$('history-date').value,offset:historyOffset});
  const records = await api('/api/history?'+params); $('history-list').replaceChildren();
  $('history-total').textContent = `${records.total} formación(es). Mostrando ${records.items.length ? historyOffset+1 : 0}–${historyOffset+records.items.length}.`;
  $('history-prev').disabled = historyOffset === 0; $('history-next').disabled = historyOffset+50 >= records.total;
  if (!records.items.length) $('history-list').textContent = 'No hay certificados que coincidan con la búsqueda.';
  records.items.forEach(record => {
    const d = record.data, {card,actions} = trainingCard(`${d.company} · ${record.course}`, `${d.course_date} · ${d.students.length} alumno(s) · ${record.technician} · Emitido ${new Date(record.created).toLocaleString('es-ES')}`);
    [['Word',record.files.word],['PDF',record.files.pdf]].forEach(([label,url]) => { if (url) { const link = document.createElement('a'); link.className = 'button secondary small'; link.textContent = 'Descargar '+label; link.href = url; actions.append(link); } });
    actions.append(action('Recuperar para corregir',async () => { if (restoreTraining(d)) { $('history-dialog').close(); toast('Datos recuperados. Generar creará una nueva versión y conservará la anterior.'); } }));
    $('history-list').append(card);
  });
}
$('open-history').onclick = () => run(async () => { historyOffset = 0; await loadHistory(); $('history-dialog').showModal(); });
$('history-search-form').onsubmit = event => { event.preventDefault(); historyOffset = 0; run(loadHistory); };
$('history-prev').onclick = () => { historyOffset = Math.max(0,historyOffset-50); run(loadHistory); };
$('history-next').onclick = () => { historyOffset += 50; run(loadHistory); };
