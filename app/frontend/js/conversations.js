function openConversationDialog(conversation) {
      editingConversation = conversation;
      $('conversation-name').value = conversation.title;
      const select = $('conversation-project');
      select.innerHTML = '<option value="">Hội thoại riêng</option>';
      projects.forEach((project) => {
        const option = document.createElement('option');
        option.value = project.id;
        option.textContent = project.name;
        select.appendChild(option);
      });
      select.value = conversation.project_id || '';
      $('conversation-dialog').showModal();
      $('conversation-name').focus();
      $('conversation-name').select();
    }

    $('cancel-conversation').addEventListener('click', () => $('conversation-dialog').close());
    $('conversation-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!editingConversation) return;
      const title = $('conversation-name').value.trim();
      if (!title) return;
      const previousId = editingConversation.id;
      try {
        const updated = await fetchJson(`/conversations/${previousId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title,
            project_id: $('conversation-project').value || null,
          }),
        });
        if (conversationId === previousId) currentProjectId = updated.project_id || null;
        editingConversation = null;
        $('conversation-dialog').close();
        await loadProjects();
        await loadConversations();
        updateProjectIndicators();
        message('chat-message', 'Đã cập nhật tên và vị trí hội thoại.', 'success');
      } catch (error) {
        await showAppAlert(error.message, 'Không thể cập nhật hội thoại');
      }
    });

    $('delete-conversation-dialog').addEventListener('click', async () => {
      if (!editingConversation) return;
      const deleted = await deleteConversation(editingConversation.id);
      if (deleted) {
        editingConversation = null;
        $('conversation-dialog').close();
      }
    });

function resetChat() {
      conversationId = null;
      $('chat-box').innerHTML = '<div id="chat-empty" class="chat-empty">Bắt đầu cuộc trò chuyện mới.<br>Dữ liệu nhạy cảm sẽ được che trước khi rời khỏi máy.</div>';
      $('chat-input').value = '';
      clearAttachment();
      message('chat-message', '');
      document.querySelectorAll('.conversation-item').forEach((item) => item.classList.remove('active'));
      $('chat-input').focus();
    }

    function applyConversationConfig(conversation) {
      $('provider').value = conversation.provider;
      $('api-url').value = conversation.api_url;
      $('model').value = conversation.model;
    }

    async function openConversation(id) {
      try {
        const conversation = await fetchJson(`/conversations/${id}`);
        conversationId = conversation.id;
        currentProjectId = conversation.project_id || null;
        await loadProjects();
        await loadConversations();
        applyConversationConfig(conversation);
        $('chat-box').innerHTML = '';
        if (!conversation.messages.length) {
          $('chat-box').innerHTML = '<div id="chat-empty" class="chat-empty">Cuộc trò chuyện chưa có tin nhắn.</div>';
        } else {
          conversation.messages.forEach((item) => addBubble(
            item.role,
            item.content,
            item.attachment_name,
            item.sources || [],
          ));
        }
        document.querySelectorAll('.conversation-item').forEach((item) => item.classList.toggle('active', item.dataset.id === id));
        updateProjectIndicators();
        message('chat-message', currentProjectId ? 'Đã tải hội thoại cùng bộ nhớ Project.' : 'Đã tải lịch sử từ PostgreSQL.', 'success');
        if (sidebarMedia.matches) setSidebarCollapsed(true, false);
      } catch (error) {
        message('chat-message', error.message, 'error');
      }
    }

    async function deleteConversation(id) {
      if (!await showAppConfirm('Xóa cuộc trò chuyện này? Hành động không thể hoàn tác.')) return false;
      try {
        await fetchJson(`/conversations/${id}`, { method: 'DELETE' });
        if (conversationId === id) resetChat();
        await loadConversations();
        await loadProjects();
        return true;
      } catch (error) {
        message('chat-message', error.message, 'error');
        return false;
      }
    }

    async function loadConversations() {
      const list = $('conversation-list');
      const sequence = ++conversationLoadSequence;
      const search = $('conversation-search').value.trim();
      try {
        const params = new URLSearchParams();
        if (currentProjectId) params.set('project_id', currentProjectId);
        else params.set('unassigned_only', 'true');
        if (search) params.set('search', search);
        const data = await fetchJson(`/conversations?${params.toString()}`);
        if (sequence !== conversationLoadSequence) return;
        list.innerHTML = '';
        if (!data.conversations.length) {
          const emptyText = search
            ? 'Không tìm thấy tên hội thoại phù hợp.'
            : (currentProjectId ? 'Project chưa có cuộc trò chuyện.' : 'Chưa có hội thoại riêng.');
          list.innerHTML = `<div class="conversation-empty">${emptyText}</div>`;
          return;
        }
        data.conversations.forEach((conversation) => {
          const item = document.createElement('div');
          item.className = `conversation-item${conversation.id === conversationId ? ' active' : ''}`;
          item.dataset.id = conversation.id;

          const openButton = document.createElement('button');
          openButton.type = 'button';
          openButton.className = 'conversation-open';
          const title = document.createElement('span');
          title.className = 'conversation-title';
          title.textContent = conversation.title;
          const meta = document.createElement('span');
          meta.className = 'conversation-meta';
          meta.textContent = `${conversation.model} · ${conversation.message_count} tin nhắn`;
          openButton.append(title, meta);
          openButton.addEventListener('click', () => openConversation(conversation.id));

          const menuButton = document.createElement('button');
          menuButton.type = 'button';
          menuButton.className = 'conversation-menu';
          menuButton.textContent = '···';
          menuButton.title = 'Đổi tên hoặc chuyển Project';
          menuButton.setAttribute('aria-label', `Quản lý hội thoại ${conversation.title}`);
          menuButton.addEventListener('click', () => openConversationDialog(conversation));
          item.append(openButton, menuButton);
          list.appendChild(item);
        });
      } catch (error) {
        if (sequence !== conversationLoadSequence) return;
        list.innerHTML = '';
        const errorBox = document.createElement('div');
        errorBox.className = 'conversation-empty';
        errorBox.textContent = error.message;
        list.appendChild(errorBox);
      }
    }
