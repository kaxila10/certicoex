const form = document.getElementById('login-form');
form.onsubmit = async event => {
  event.preventDefault();
  const button = document.getElementById('login-submit'), error = document.getElementById('login-error');
  button.disabled = true; error.textContent = '';
  try {
    const response = await fetch('/api/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('login-username').value.trim(),password:document.getElementById('login-password').value})});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Revisa el usuario y la contraseña.');
    document.getElementById('login-password').value = '';
    try { localStorage.setItem('certicoex.auth-change', String(Date.now())); } catch {}
    location.replace('/');
  } catch (reason) { error.textContent = reason.message || 'No se pudo iniciar sesión.'; }
  finally { button.disabled = false; }
};
fetch('/api/auth/me').then(response => { if (response.ok) location.replace('/'); }).catch(() => {});
