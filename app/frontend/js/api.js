function updateThemeButton() {
      const isDark = document.documentElement.dataset.theme === 'dark';
      $('theme-toggle').textContent = isDark ? '☀' : '☾';
      $('theme-toggle').setAttribute('aria-label', isDark ? 'Chuyển sang giao diện sáng' : 'Chuyển sang giao diện tối');
      $('theme-toggle').title = isDark ? 'Giao diện sáng' : 'Giao diện tối';
    }

    $('theme-toggle').addEventListener('click', () => {
      const nextTheme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = nextTheme;
      try { localStorage.setItem('masking-theme', nextTheme); } catch { }
      updateThemeButton();
    });

    async function fetchJson(path, options = {}) {
      const response = await fetch(path, { cache: 'no-store', ...options });
      if (response.status === 204) return null;
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `Lỗi HTTP ${response.status}`);
      return data;
    }

    function updateChatProgress(event) {
      const percent = Math.max(0, Math.min(100, Number(event.progress) || 0));
      $('chat-progress').hidden = false;
      $('progress-label').textContent = event.message || 'Đang xử lý...';
      $('progress-percent').textContent = `${percent}%`;
      $('progress-bar').style.width = `${percent}%`;
    }

    function hideChatProgress() {
      $('chat-progress').hidden = true;
      $('progress-bar').style.width = '0%';
      $('progress-percent').textContent = '0%';
    }

    async function fetchChatStream(path, options, onEvent) {
      const response = await fetch(path, { cache: 'no-store', ...options });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `Lỗi HTTP ${response.status}`);
      }
      if (!response.body) throw new Error('Trình duyệt không hỗ trợ nhận tiến trình từ máy chủ.');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split('\n');
        buffer = done ? '' : lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line);
          if (event.type === 'error') throw new Error(event.message || 'Không thể xử lý tin nhắn.');
          onEvent(event);
        }
        if (done) break;
      }
    }

    async function api(path, body) {
      return fetchJson(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    }

    async function checkHealth() {
      try {
        const response = await fetch('/health', { cache: 'no-store' });
        if (!response.ok) throw new Error();
        const health = await response.json();
        if (health.database === 'ok') {
          $('status').classList.add('online');
          $('status').classList.remove('warning');
          $('status').lastElementChild.textContent = 'API và PostgreSQL hoạt động';
        } else {
          $('status').classList.add('warning');
          $('status').lastElementChild.textContent = 'PostgreSQL chưa sẵn sàng';
        }
      } catch {
        $('status').lastElementChild.textContent = 'Không kết nối được API';
      }
    }
