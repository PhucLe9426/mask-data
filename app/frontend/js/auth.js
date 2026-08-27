let currentUser = null;
let authMode = 'login';

function setAuthMode(mode) {
  authMode = mode;
  const registering = mode === 'register';
  $('auth-mode-eyebrow').textContent = registering ? 'Bắt đầu bảo mật' : 'Chào mừng trở lại';
  $('auth-title').textContent = registering ? 'Tạo tài khoản' : 'Đăng nhập';
  $('auth-subtitle').textContent = registering
    ? 'Tạo không gian riêng để lưu Project và lịch sử của bạn.'
    : 'Đăng nhập để tiếp tục cuộc trò chuyện của bạn.';
  $('auth-submit').querySelector('span').textContent = registering ? 'Tạo tài khoản' : 'Đăng nhập';
  $('auth-switch-prompt').textContent = registering ? 'Đã có tài khoản?' : 'Chưa có tài khoản?';
  $('auth-switch').textContent = registering ? 'Đăng nhập' : 'Đăng ký ngay';
  $('auth-password').autocomplete = registering ? 'new-password' : 'current-password';
  $('auth-password-confirm-field').hidden = !registering;
  $('auth-password-confirm').required = registering;
  $('auth-password-confirm').value = '';
  $('auth-password-confirm').setCustomValidity('');
  $('auth-message').textContent = '';
}

function showAuthView(messageText = '') {
  currentUser = null;
  document.body.classList.remove('auth-loading', 'auth-authenticated');
  document.body.classList.add('auth-anonymous');
  $('auth-message').textContent = messageText;
  $('auth-password').value = '';
  requestAnimationFrame(() => $('auth-email').focus());
}

function showAuthenticatedApp(user) {
  currentUser = user;
  document.body.classList.remove('auth-loading', 'auth-anonymous');
  document.body.classList.add('auth-authenticated');
  $('account-email').textContent = user.email;
  $('account-popover-email').textContent = user.email;
  $('account-avatar').textContent = user.email.charAt(0).toUpperCase();
  $('account-role').textContent = user.role === 'admin' ? 'Quản trị viên' : 'Người dùng';
}

async function initializeAuth() {
  try {
    const data = await fetchJson('/auth/me');
    showAuthenticatedApp(data.user);
    return true;
  } catch {
    showAuthView();
    return false;
  }
}

function handleAuthenticationExpired() {
  if (currentUser) showAuthView('Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.');
}

$('auth-switch').addEventListener('click', () => setAuthMode(authMode === 'login' ? 'register' : 'login'));

$('auth-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const password = $('auth-password').value;
  const passwordConfirm = $('auth-password-confirm').value;
  if (authMode === 'register' && password !== passwordConfirm) {
    $('auth-password-confirm').setCustomValidity('Mật khẩu nhập lại không khớp');
    $('auth-password-confirm').reportValidity();
    $('auth-message').textContent = 'Mật khẩu nhập lại không khớp.';
    return;
  }
  $('auth-password-confirm').setCustomValidity('');
  const submit = $('auth-submit');
  submit.disabled = true;
  $('auth-message').textContent = '';
  try {
    const payload = {
      email: $('auth-email').value.trim(),
      password,
    };
    if (authMode === 'register') payload.password_confirm = passwordConfirm;
    const data = await fetchJson(`/auth/${authMode}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    showAuthenticatedApp(data.user);
    await Promise.all([loadProjects(), loadConversations()]);
  } catch (error) {
    $('auth-message').textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

$('auth-password-confirm').addEventListener('input', () => {
  const matches = $('auth-password-confirm').value === $('auth-password').value;
  $('auth-password-confirm').setCustomValidity(matches ? '' : 'Mật khẩu nhập lại không khớp');
});

document.querySelectorAll('.password-toggle').forEach((button) => {
  button.addEventListener('click', () => {
    const input = $(button.dataset.passwordTarget);
    const showing = input.type === 'text';
    input.type = showing ? 'password' : 'text';
    button.textContent = showing ? 'Hiện' : 'Ẩn';
  });
});

function updateAuthThemeButton() {
  const isDark = document.documentElement.dataset.theme === 'dark';
  $('auth-theme-toggle').textContent = isDark ? '☀' : '☾';
  $('auth-theme-toggle').title = isDark ? 'Giao diện sáng' : 'Giao diện tối';
}

$('auth-theme-toggle').addEventListener('click', () => {
  $('theme-toggle').click();
  updateAuthThemeButton();
});

updateAuthThemeButton();

$('account-toggle').addEventListener('click', () => {
  const popover = $('account-popover');
  popover.hidden = !popover.hidden;
  $('account-toggle').setAttribute('aria-expanded', String(!popover.hidden));
});

document.addEventListener('click', (event) => {
  if (!event.target.closest('.account-menu')) {
    $('account-popover').hidden = true;
    $('account-toggle').setAttribute('aria-expanded', 'false');
  }
});

$('logout-button').addEventListener('click', async () => {
  try { await fetchJson('/auth/logout', { method: 'POST' }); } catch { }
  conversationId = null;
  currentProjectId = null;
  projects = [];
  $('account-popover').hidden = true;
  showAuthView('Bạn đã đăng xuất.');
});
