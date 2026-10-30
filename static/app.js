const $ = id => document.getElementById(id);
let templates = [], profile = {}, students = [], imported = null, busy = false, revision = 0;
let toastTimer;
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 6500); }
async function api(url, options = {}) {
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); } catch { throw new Error('No se puede leer la respuesta del servidor.'); }
  if (!response.ok) {
    let message = data.detail;
    if (Array.isArray(message)) message = message.map(e => `${e.loc.slice(1).join(' · ')}: ${e.msg}`).join('\n');
    throw new Error(message || 'No se pudo completar la operación.');
  }
  return data;
}
function run(task) { return task().catch(error => toast(error.message)); }
function dirty() { revision++; $('downloads').hidden = true; $('generation-status').textContent = ''; }
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
  summary();
}
function cleanDni(value) { return value.toUpperCase().replace(/[\s.\-]/g, ''); }
function validateStudents() {
  const counts = new Map();
  students.forEach(s => { const dni = cleanDni(s.dni); if (dni) counts.set(dni, (counts.get(dni) || 0) + 1); });
  let missing = 0, duplicate = 0, unusual = 0;
  [...$('student-rows').children].forEach((row, i) => {
    const s = students[i], inputs = row.querySelectorAll('input');
    const emptyName = !s.name.trim(), emptyDni = !s.dni.trim(), repeated = (counts.get(cleanDni(s.dni)) || 0) > 1;
    inputs[0].classList.toggle('invalid', emptyName); inputs[1].classList.toggle('invalid', emptyDni || repeated);
    inputs[0].setAttribute('aria-invalid', String(emptyName)); inputs[1].setAttribute('aria-invalid', String(emptyDni || repeated));
    if (emptyName || emptyDni) missing++;
    if (repeated) duplicate++;
    if (!emptyDni && !/^(\d{8}|[XYZ]\d{7})[A-Z]$/.test(cleanDni(s.dni))) unusual++;
  });
  const messages = [];
  if (missing) messages.push(`${missing} fila(s) con datos incompletos.`);
  if (duplicate) messages.push(`${duplicate} fila(s) con DNI/NIE repetido.`);
  if (unusual) messages.push(`${unusual} documento(s) con formato poco habitual: revísalos. No se comprueba la letra de control.`);
  $('student-validation').textContent = messages.join(' ');
  return !missing && !duplicate;
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
  if (!data.rows.length) throw new Error('El listado no contiene alumnos.');
  imported = data;
  $('mapping-panel').hidden = false;
  $('import-info').textContent = `${data.rows.length} filas detectadas${data.sheet ? ` en la hoja «${data.sheet}»` : ''}. Confirma las columnas antes de añadirlas.`;
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
  try { const s = mappedStudents()[0]; $('import-sample').textContent = `Primera fila: ${s.name || '(sin nombre)'} · ${s.dni || '(sin documento)'}`; }
  catch { $('import-sample').textContent = 'Selecciona las columnas de nombre y documento.'; }
}
async function importExcel(file) {
  if (!file) return;
  const data = new FormData(); data.append('file', file);
  showImport(await api('/api/import/excel', {method:'POST', body:data}));
  $('excel-file').value = '';
}
function setTab(excel) {
  $('excel-panel').hidden = !excel; $('paste-panel').hidden = excel;
  $('tab-excel').classList.toggle('selected', excel); $('tab-paste').classList.toggle('selected', !excel);
  $('tab-excel').setAttribute('aria-selected', String(excel)); $('tab-paste').setAttribute('aria-selected', String(!excel));
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
  ['preview', 'generate'].forEach(id => $(id).disabled = true);
  $('downloads').hidden = true;
  $('generation-status').textContent = preview ? 'Preparando la vista previa…' : `Generando ${students.length} certificados. El PDF puede tardar unos instantes…`;
  try {
    const result = await api('/api/generate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    if (startedAt !== revision) { $('generation-status').textContent = 'Los datos cambiaron durante la generación. Vuelve a generar para usar los datos actuales.'; return; }
    if (preview && result.pdf) {
      $('pdf-preview').src = result.pdf + '?inline=true'; $('preview-word').href = result.word;
      $('preview-dialog').showModal(); $('generation-status').textContent = 'Vista previa del primer alumno preparada.';
    } else {
      $('download-word').href = result.word; $('download-pdf').hidden = !result.pdf;
      if (result.pdf) $('download-pdf').href = result.pdf;
      $('result-info').textContent = `${result.count} certificado(s)${result.pages ? ` · ${result.pages} página(s) en el PDF` : ''}. Descargas disponibles durante 24 horas.`;
      $('downloads').querySelector('h3').textContent = preview ? 'Prueba en Word disponible' : 'Tu lote está listo';
      $('downloads').hidden = false;
      $('generation-status').textContent = result.warning || 'Documentos generados correctamente.';
    }
    if (result.warning) toast(result.warning);
  } finally { busy = false; ['preview', 'generate'].forEach(id => $(id).disabled = false); }
}
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
  const result = await api('/api/templates', {method:'POST', body:data}); await loadTemplates(result.id); dirty();
  $('template-dialog').close(); $('template-form').reset(); toast('Plantilla añadida.');
}); };
$('course-form').onsubmit = e => e.preventDefault();
['company', 'cif', 'hours', 'course-date', 'template'].forEach(id => $(id).addEventListener('input', () => {dirty(); summary();}));
$('add-student').onclick = () => { students.push({name:'', dni:''}); dirty(); renderStudents(); $('student-rows').lastChild.querySelector('input').focus(); };
$('clear-students').onclick = () => { if (confirm('¿Vaciar el listado de alumnos?')) { students = []; dirty(); renderStudents(); } };
$('tab-excel').onclick = () => setTab(true); $('tab-paste').onclick = () => setTab(false);
$('excel-file').onchange = e => run(() => importExcel(e.target.files[0]));
$('drop-zone').onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('excel-file').click(); } };
['dragover', 'dragleave', 'drop'].forEach(type => $('drop-zone').addEventListener(type, e => {
  e.preventDefault(); $('drop-zone').classList.toggle('dragging', type === 'dragover');
  if (type === 'drop') run(() => importExcel(e.dataTransfer.files[0]));
}));
$('parse-paste').onclick = () => run(async () => showImport(await api('/api/import/paste', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:$('paste-text').value})})));
['name', 'dni', 'surname', 'surname2'].forEach(field => $('map-'+field).onchange = importSample);
$('apply-import').onclick = () => run(async () => {
  const rows = mappedStudents(); if (students.length + rows.length > 500) throw new Error('El lote admite hasta 500 alumnos.');
  students.push(...rows); imported = null; $('mapping-panel').hidden = true; $('paste-text').value = ''; dirty(); renderStudents(); toast(`${rows.length} alumnos añadidos. Revisa sus datos.`);
});
$('preview').onclick = () => run(() => generate(true)); $('generate').onclick = () => run(() => generate(false));
run(async () => { const [p, , health] = await Promise.all([api('/api/profile'), loadTemplates(), api('/api/health')]); profile = p; updateProfile(); $('trial-notice').hidden = !health.storage_ephemeral; });
