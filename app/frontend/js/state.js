let conversationId = null;
    let selectedFile = null;
    let currentProjectId = null;
    let projects = [];
    let editingProjectId = null;
    let editingConversation = null;
    let activeChatController = null;
    let conversationSearchTimer = null;
    let conversationLoadSequence = 0;
    const $ = (id) => document.getElementById(id);
    const sidebarMedia = window.matchMedia('(max-width: 760px)');
    let appDialogResolver = null;

    function finishAppDialog(result) {
      const resolve = appDialogResolver;
      appDialogResolver = null;
      if ($('app-confirm-dialog').open) $('app-confirm-dialog').close();
      if (resolve) resolve(result);
    }

    function showAppDialog({
      title = 'Thông báo',
      message: dialogMessage,
      confirmText = 'Đồng ý',
      cancelText = 'Hủy',
      danger = false,
      alertOnly = false,
    }) {
      if (appDialogResolver) finishAppDialog(false);
      const dialog = $('app-confirm-dialog');
      dialog.classList.toggle('danger', danger);
      $('app-confirm-icon').textContent = danger ? '!' : 'i';
      $('app-confirm-title').textContent = title;
      $('app-confirm-message').textContent = dialogMessage;
      $('app-confirm-ok').textContent = confirmText;
      $('app-confirm-cancel').textContent = cancelText;
      $('app-confirm-cancel').hidden = alertOnly;
      dialog.showModal();
      requestAnimationFrame(() => $('app-confirm-ok').focus());
      return new Promise((resolve) => { appDialogResolver = resolve; });
    }

    function showAppAlert(dialogMessage, title = 'Thông báo') {
      return showAppDialog({ title, message: dialogMessage, confirmText: 'Đóng', alertOnly: true });
    }

    function showAppConfirm(dialogMessage, title = 'Xác nhận xóa') {
      return showAppDialog({ title, message: dialogMessage, confirmText: 'Xóa', danger: true });
    }

    $('app-confirm-ok').addEventListener('click', () => finishAppDialog(true));
    $('app-confirm-cancel').addEventListener('click', () => finishAppDialog(false));
    $('app-confirm-dialog').addEventListener('cancel', (event) => {
      event.preventDefault();
      finishAppDialog(false);
    });

    function savedSidebarCollapsed() {
      try { return localStorage.getItem('masking-sidebar-collapsed') === 'true'; }
      catch { return false; }
    }

    function setSidebarCollapsed(collapsed, remember = !sidebarMedia.matches) {
      $('app-card').classList.toggle('sidebar-collapsed', collapsed);
      $('sidebar-toggle').setAttribute('aria-expanded', String(!collapsed));
      $('sidebar-toggle').title = collapsed ? 'Mở lịch sử' : 'Đóng lịch sử';
      $('mobile-sidebar-toggle').setAttribute('aria-expanded', String(!collapsed));
      $('mobile-sidebar-toggle').title = collapsed ? 'Mở lịch sử' : 'Đóng lịch sử';
      document.body.classList.toggle('sidebar-open-mobile', sidebarMedia.matches && !collapsed);
      if (remember) {
        try { localStorage.setItem('masking-sidebar-collapsed', String(collapsed)); } catch { }
      }
    }

    function initializeSidebar() {
      setSidebarCollapsed(sidebarMedia.matches ? true : savedSidebarCollapsed(), false);
    }

    $('sidebar-toggle').addEventListener('click', () => {
      setSidebarCollapsed(!$('app-card').classList.contains('sidebar-collapsed'));
    });
    $('collapsed-projects').addEventListener('click', () => {
      setSidebarCollapsed(false);
      requestAnimationFrame(() => $('project-section').scrollIntoView({ behavior: 'smooth', block: 'nearest' }));
    });
    $('mobile-sidebar-toggle').addEventListener('click', () => setSidebarCollapsed(false, false));
    $('sidebar-close').addEventListener('click', () => setSidebarCollapsed(true, false));
    $('sidebar-backdrop').addEventListener('click', () => setSidebarCollapsed(true, false));
    sidebarMedia.addEventListener('change', (event) => {
      setSidebarCollapsed(event.matches ? true : savedSidebarCollapsed(), false);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && sidebarMedia.matches) setSidebarCollapsed(true, false);
    });

    function message(id, text, kind = '') {
      const el = $(id);
      el.textContent = text;
      el.className = `message ${kind}`;
    }
