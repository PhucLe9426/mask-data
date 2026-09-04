function exportTitle() {
      return document.querySelector('.conversation-item.active .conversation-title')?.textContent?.trim()
        || 'Tổng hợp AI';
    }

    async function downloadAssistantExport(format, content, sources, trigger) {
      const buttons = trigger.closest('.bubble-export-menu').querySelectorAll('button');
      buttons.forEach((button) => { button.disabled = true; });
      trigger.textContent = 'Đang tạo...';
      try {
        const response = await fetch('/exports', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ format, title: exportTitle(), content, sources }),
        });
        if (!response.ok) {
          const error = await response.json().catch(() => ({}));
          throw new Error(error.detail || `Lỗi HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition') || '';
        const filename = disposition.match(/filename="([^"]+)"/i)?.[1] || `tong-hop-ai.${format}`;
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        trigger.closest('details').open = false;
        message('chat-message', `Đã tạo file ${format.toUpperCase()}.`, 'success');
      } catch (error) {
        await showAppAlert(error.message, 'Không thể xuất file');
      } finally {
        buttons.forEach((button) => { button.disabled = false; });
        trigger.textContent = trigger.dataset.label;
      }
    }

    function createExportMenu(content, sources) {
      const details = document.createElement('details');
      details.className = 'bubble-export';
      const summary = document.createElement('summary');
      summary.textContent = '↓ Xuất file';
      summary.title = 'Tải câu trả lời này xuống';
      const menu = document.createElement('div');
      menu.className = 'bubble-export-menu';
      [
        ['docx', 'Word (.docx)'],
        ['xlsx', 'Excel (.xlsx)'],
        ['pdf', 'PDF (.pdf)'],
      ].forEach(([format, label]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.label = label;
        button.textContent = label;
        button.addEventListener('click', () => downloadAssistantExport(format, content, sources, button));
        menu.appendChild(button);
      });
      details.append(summary, menu);
      return details;
    }

function addBubble(role, content, attachmentName = null, sources = [], exportable = true) {
      $('chat-empty')?.remove();
      const bubble = document.createElement('div');
      bubble.className = `bubble ${role}`;
      if (attachmentName) {
        const attachment = document.createElement('div');
        attachment.className = 'bubble-attachment';
        attachment.textContent = `📎 ${attachmentName}`;
        bubble.appendChild(attachment);
      }
      const body = document.createElement('div');
      body.textContent = content;
      bubble.appendChild(body);
      if (role === 'assistant' && sources.length) {
        const sourceBox = document.createElement('div');
        sourceBox.className = 'bubble-sources';
        const sourceLabel = document.createElement('span');
        sourceLabel.className = 'bubble-sources-label';
        sourceLabel.textContent = `Nguồn tham khảo · ${sources.length}`;
        sourceBox.appendChild(sourceLabel);
        sources.forEach((source) => {
          const details = document.createElement('details');
          details.className = 'bubble-source';
          const summary = document.createElement('summary');
          summary.textContent = source.name;
          const excerpt = document.createElement('p');
          excerpt.textContent = source.excerpt || 'Không có đoạn trích.';
          details.append(summary, excerpt);
          sourceBox.appendChild(details);
        });
        bubble.appendChild(sourceBox);
      }
      if (role === 'assistant' && exportable) {
        bubble.appendChild(createExportMenu(content, sources));
      }
      $('chat-box').appendChild(bubble);
      $('chat-box').scrollTop = $('chat-box').scrollHeight;
    }

    function clearAttachment() {
      selectedFile = null;
      $('file-input').value = '';
      $('selected-file').textContent = 'TXT, CSV, PDF, DOCX, XLSX, XLS · tối đa 10 MB';
      $('remove-file').style.display = 'none';
    }

    $('attach-file').addEventListener('click', () => $('file-input').click());
    $('remove-file').addEventListener('click', clearAttachment);
    $('file-input').addEventListener('change', () => {
      const file = $('file-input').files[0];
      if (!file) return clearAttachment();
      if (file.size > 10 * 1024 * 1024) {
        clearAttachment();
        return message('chat-message', 'File vượt quá giới hạn 10 MB.', 'error');
      }
      selectedFile = file;
      $('selected-file').textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
      $('remove-file').style.display = 'inline-block';
      message('chat-message', 'File sẽ được đọc và che dữ liệu cục bộ trước khi gửi.', 'success');
    });

    async function sendChat() {
      if (activeChatController) {
        activeChatController.abort();
        return;
      }
      const text = $('chat-input').value.trim();
      const apiKey = $('api-key').value.trim();
      const apiUrl = $('api-url').value.trim();
      const model = $('model').value.trim();
      if (!apiUrl || !model || !apiKey) {
        message('chat-message', 'Vui lòng hoàn tất cấu hình Public LLM.', 'error');
        openModelSettings(!apiUrl ? 'api-url' : (!model ? 'model' : 'api-key'));
        return;
      }
      if (!text && !selectedFile) return message('chat-message', 'Vui lòng nhập tin nhắn hoặc đính kèm file.', 'error');

      const button = $('chat-send');
      const fileToSend = selectedFile;
      const displayText = text || 'Hãy đọc, phân tích và trả lời dựa trên nội dung tệp.';
      $('attach-file').disabled = true;
      button.classList.add('running');
      button.setAttribute('aria-label', 'Hủy xử lý');
      button.title = 'Hủy xử lý';
      activeChatController = new AbortController();
      updateChatProgress({ progress: 1, message: fileToSend ? 'Đang tải file lên backend cục bộ...' : 'Đang gửi yêu cầu...' });
      message('chat-message', 'Bạn có thể theo dõi tiến trình hoặc hủy yêu cầu đang chạy.');
      addBubble('user', displayText, fileToSend?.name);
      $('chat-input').value = '';

      try {
        let result = null;
        const handleEvent = (event) => {
          if (event.type === 'progress') updateChatProgress(event);
          if (event.type === 'result') result = event.data;
        };
        if (fileToSend) {
          const formData = new FormData();
          formData.append('file', fileToSend);
          formData.append('text', text);
          formData.append('api_url', apiUrl);
          formData.append('api_key', apiKey);
          formData.append('model', model);
          formData.append('provider', $('provider').value);
          if (conversationId) formData.append('conversation_id', conversationId);
          if (currentProjectId) formData.append('project_id', currentProjectId);
          await fetchChatStream('/chat/file/stream', {
            method: 'POST',
            body: formData,
            signal: activeChatController.signal,
          }, handleEvent);
        } else {
          await fetchChatStream('/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal: activeChatController.signal,
            body: JSON.stringify({
              text,
              api_url: apiUrl,
              api_key: apiKey,
              model,
              provider: $('provider').value,
              conversation_id: conversationId,
              project_id: currentProjectId,
            }),
          }, handleEvent);
        }
        if (!result) throw new Error('Kết nối kết thúc trước khi nhận được câu trả lời.');
        conversationId = result.conversation_id;
        currentProjectId = result.project_id || currentProjectId;
        addBubble('assistant', result.final_text, null, result.sources || []);
        clearAttachment();
        await loadProjects();
        await loadConversations();
        message('chat-message', `Đã che ${result.entity_count} entity trước khi gửi.`, 'success');
      } catch (error) {
        if (error.name === 'AbortError') {
          message('chat-message', 'Đã hủy yêu cầu. Hội thoại dở dang không được lưu.', 'success');
        } else {
          addBubble('assistant', `Không thể trả lời: ${error.message}`, null, [], false);
          message('chat-message', error.message, 'error');
        }
      } finally {
        activeChatController = null;
        hideChatProgress();
        button.classList.remove('running');
        button.setAttribute('aria-label', 'Gửi tin nhắn');
        button.title = 'Gửi tin nhắn';
        $('attach-file').disabled = false;
        $('chat-input').focus();
      }
    }

    $('chat-send').addEventListener('click', sendChat);
    $('project-back').addEventListener('click', () => selectProject(null));
    $('conversation-search').addEventListener('input', () => {
      clearTimeout(conversationSearchTimer);
      conversationSearchTimer = setTimeout(loadConversations, 250);
    });
    $('conversation-search').addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && $('conversation-search').value) {
        $('conversation-search').value = '';
        clearTimeout(conversationSearchTimer);
        loadConversations();
      }
    });
    $('new-chat').addEventListener('click', () => {
      resetChat();
      if (sidebarMedia.matches) setSidebarCollapsed(true, false);
    });
    $('chat-input').addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendChat();
      }
    });
