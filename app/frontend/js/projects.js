function currentProject() {
      return projects.find((project) => project.id === currentProjectId) || null;
    }

    function updateProjectIndicators() {
      const project = currentProject();
      $('conversation-section-label').textContent = project ? `Hội thoại · ${project.name}` : 'Hội thoại riêng';
      $('project-back').hidden = !project;
      $('project-context-badge').hidden = !project;
      $('project-context-badge').textContent = project ? `◆ Project: ${project.name}` : '';
      document.querySelectorAll('.project-item').forEach((item) => {
        item.classList.toggle('active', (item.dataset.id || null) === currentProjectId);
      });
    }

    function renderProjects() {
      const list = $('project-list');
      list.innerHTML = '';

      const addProjectRow = (project) => {
        const item = document.createElement('div');
        item.className = 'project-item';
        item.dataset.id = project.id;

        const openButton = document.createElement('button');
        openButton.type = 'button';
        openButton.className = 'project-open';
        const name = document.createElement('span');
        name.className = 'project-name';
        name.textContent = ` ${project.name}`;
        const count = document.createElement('span');
        count.className = 'project-count';
        count.textContent = `${project.conversation_count || 0} hội thoại · ${project.document_count || 0} tài liệu`;
        openButton.append(name, count);
        openButton.addEventListener('click', () => selectProject(project.id));
        item.appendChild(openButton);

        const editButton = document.createElement('button');
        editButton.type = 'button';
        editButton.className = 'project-edit';
        editButton.textContent = '···';
        editButton.title = 'Sửa Project và bộ nhớ';
        editButton.addEventListener('click', () => openProjectDialog(project));
        item.appendChild(editButton);
        list.appendChild(item);
      };

      projects.forEach(addProjectRow);
      updateProjectIndicators();
    }

    async function loadProjects() {
      try {
        const data = await fetchJson('/projects');
        projects = data.projects;
        if (currentProjectId && !projects.some((project) => project.id === currentProjectId)) {
          currentProjectId = null;
        }
        renderProjects();
      } catch (error) {
        $('project-list').innerHTML = `<div class="conversation-empty"></div>`;
        $('project-list').firstElementChild.textContent = error.message;
      }
    }

    async function selectProject(projectId) {
      currentProjectId = projectId;
      resetChat();
      updateProjectIndicators();
      await loadConversations();
    }

    function formatDocumentSize(bytes) {
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    async function loadProjectDocuments() {
      const list = $('project-document-list');
      list.innerHTML = '';
      if (!editingProjectId) return;
      try {
        const data = await fetchJson(`/projects/${editingProjectId}/documents`);
        if (!data.documents.length) {
          list.innerHTML = '<div class="project-document-empty">Project chưa có tài liệu.</div>';
          return;
        }
        data.documents.forEach((projectDocument) => {
          const item = document.createElement('div');
          item.className = 'project-document-item';
          const copy = document.createElement('div');
          const name = document.createElement('span');
          name.className = 'project-document-name';
          name.textContent = projectDocument.name;
          const meta = document.createElement('span');
          meta.className = 'project-document-meta';
          meta.textContent = formatDocumentSize(projectDocument.size_bytes);
          copy.append(name, meta);
          const remove = document.createElement('button');
          remove.type = 'button';
          remove.className = 'project-document-delete';
          remove.textContent = '×';
          remove.title = `Xóa ${projectDocument.name}`;
          remove.addEventListener('click', async () => {
            if (!await showAppConfirm(`Xóa tài liệu "${projectDocument.name}" khỏi Project?`)) return;
            try {
              await fetchJson(`/projects/${editingProjectId}/documents/${projectDocument.id}`, { method: 'DELETE' });
              await loadProjectDocuments();
              await loadProjects();
            } catch (error) {
              await showAppAlert(error.message, 'Không thể xóa tài liệu');
            }
          });
          item.append(copy, remove);
          list.appendChild(item);
        });
      } catch (error) {
        const errorBox = document.createElement('div');
        errorBox.className = 'project-document-empty';
        errorBox.textContent = error.message;
        list.appendChild(errorBox);
      }
    }

    function openProjectDialog(project = null) {
      editingProjectId = project?.id || null;
      $('project-dialog-title').textContent = project ? 'Chỉnh sửa Project' : 'Tạo Project';
      $('project-name').value = project?.name || '';
      $('project-description').value = project?.description || '';
      $('project-memory').value = project?.memory || '';
      $('delete-project').hidden = !project;
      $('upload-project-document').disabled = !project;
      $('project-document-note').textContent = project
        ? 'TXT, PDF, DOCX, XLSX, XLS và các định dạng văn bản · tối đa 10 MB'
        : 'Lưu Project trước khi thêm tài liệu.';
      $('project-document-input').value = '';
      $('project-dialog').showModal();
      loadProjectDocuments();
      $('project-name').focus();
    }

    $('upload-project-document').addEventListener('click', () => $('project-document-input').click());
    $('project-document-input').addEventListener('change', async () => {
      const file = $('project-document-input').files[0];
      if (!file || !editingProjectId) return;
      if (file.size > 10 * 1024 * 1024) {
        $('project-document-input').value = '';
        await showAppAlert('File vượt quá giới hạn 10 MB.', 'Không thể tải tài liệu');
        return;
      }
      const button = $('upload-project-document');
      button.disabled = true;
      button.textContent = 'Đang đọc...';
      try {
        const formData = new FormData();
        formData.append('file', file);
        await fetchJson(`/projects/${editingProjectId}/documents`, {
          method: 'POST',
          body: formData,
        });
        await loadProjectDocuments();
        await loadProjects();
      } catch (error) {
        await showAppAlert(error.message, 'Không thể tải tài liệu');
      } finally {
        $('project-document-input').value = '';
        button.disabled = false;
        button.textContent = '+ Thêm tài liệu';
      }
    });

    $('new-project').addEventListener('click', () => openProjectDialog());
    $('cancel-project').addEventListener('click', () => $('project-dialog').close());
    $('project-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const payload = {
        name: $('project-name').value.trim(),
        description: $('project-description').value.trim(),
        memory: $('project-memory').value.trim(),
      };
      if (!payload.name) return;
      try {
        const project = await fetchJson(editingProjectId ? `/projects/${editingProjectId}` : '/projects', {
          method: editingProjectId ? 'PUT' : 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        currentProjectId = project.id;
        $('project-dialog').close();
        resetChat();
        await loadProjects();
        await loadConversations();
        message('chat-message', 'Đã lưu Project và bộ nhớ dùng chung.', 'success');
      } catch (error) {
        await showAppAlert(error.message, 'Không thể lưu Project');
      }
    });

    $('delete-project').addEventListener('click', async () => {
      if (!editingProjectId || !await showAppConfirm('Xóa Project này? Các hội thoại sẽ được giữ lại nhưng không còn thuộc Project.')) return;
      try {
        await fetchJson(`/projects/${editingProjectId}`, { method: 'DELETE' });
        if (currentProjectId === editingProjectId) currentProjectId = null;
        $('project-dialog').close();
        resetChat();
        await loadProjects();
        await loadConversations();
      } catch (error) {
        await showAppAlert(error.message, 'Không thể xóa Project');
      }
    });
