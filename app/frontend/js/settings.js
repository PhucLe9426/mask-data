const MODEL_CONFIG_STORAGE_KEY = 'masking-model-config';

const providerDefaults = {
  openai_compatible: {
    apiUrl: 'https://api.openai.com/v1/chat/completions',
    model: '',
  },
  anthropic: {
    apiUrl: 'https://api.anthropic.com/v1/messages',
    model: '',
  },
  gemini: {
    apiUrl: 'https://generativelanguage.googleapis.com/v1beta/models',
    model: 'gemini-3.6-flash',
  },
};

function providerDisplayName() {
  return $('provider').selectedOptions[0]?.textContent || 'Public LLM';
}

function updateModelSummary() {
  const provider = providerDisplayName();
  const model = $('model').value.trim();
  const apiUrl = $('api-url').value.trim();
  const hasKey = Boolean($('api-key').value.trim());
  const configured = Boolean(apiUrl && model && hasKey);

  $('model-summary').classList.toggle('configured', configured);
  $('model-summary-name').textContent = model ? `${provider} · ${model}` : `${provider} · Chưa chọn model`;
  $('model-summary-detail').textContent = hasKey
    ? (configured ? 'Đã sẵn sàng · API key chỉ dùng trong phiên này' : 'Đã nhập API key · Cấu hình chưa đầy đủ')
    : 'Chưa nhập API key · Bấm để mở Cài đặt';
}

function updateLoadModelsButton() {
  $('load-models').disabled = !$('api-key').value.trim();
}

function clearModelOptions(status = 'Nhập API key để tải danh sách model.') {
  $('model-options').innerHTML = '';
  $('model-load-status').textContent = status;
  $('model-load-status').className = 'model-load-status';
}

async function loadAvailableModels() {
  const apiKey = $('api-key').value.trim();
  const apiUrl = $('api-url').value.trim();
  if (!apiKey) {
    clearModelOptions('Vui lòng nhập API key trước khi tải model.');
    $('model-load-status').classList.add('error');
    $('api-key').focus();
    return;
  }
  if (!apiUrl) {
    clearModelOptions('Vui lòng nhập API URL trước khi tải model.');
    $('model-load-status').classList.add('error');
    $('api-url').focus();
    return;
  }

  const button = $('load-models');
  button.disabled = true;
  button.classList.add('loading');
  button.textContent = 'Đang tải...';
  $('model-load-status').textContent = 'Đang kết nối nhà cung cấp...';
  $('model-load-status').className = 'model-load-status';

  try {
    const data = await fetchJson('/llm/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider: $('provider').value,
        api_url: apiUrl,
        api_key: apiKey,
      }),
    });
    const options = $('model-options');
    options.innerHTML = '';
    data.models.forEach((item) => {
      const option = document.createElement('option');
      option.value = item.id;
      if (item.display_name && item.display_name !== item.id) option.label = item.display_name;
      options.appendChild(option);
    });
    $('model-load-status').textContent = `Đã tải ${data.models.length} model · Bấm hoặc gõ để tìm kiếm.`;
    $('model-load-status').className = 'model-load-status success';
    $('model').focus();
  } catch (error) {
    clearModelOptions(`${error.message} Bạn vẫn có thể nhập model thủ công.`);
    $('model-load-status').classList.add('error');
  } finally {
    button.classList.remove('loading');
    button.textContent = 'Tải models';
    updateLoadModelsButton();
  }
}

function loadSavedModelConfig() {
  try {
    const saved = JSON.parse(localStorage.getItem(MODEL_CONFIG_STORAGE_KEY) || 'null');
    if (!saved || typeof saved !== 'object') return;
    if (providerDefaults[saved.provider]) $('provider').value = saved.provider;
    if (typeof saved.apiUrl === 'string') $('api-url').value = saved.apiUrl;
    if (typeof saved.model === 'string') $('model').value = saved.model;
  } catch {
    // Cấu hình lỗi hoặc localStorage bị chặn: dùng giá trị mặc định trong HTML.
  }
}

function saveModelConfig() {
  try {
    localStorage.setItem(MODEL_CONFIG_STORAGE_KEY, JSON.stringify({
      provider: $('provider').value,
      apiUrl: $('api-url').value.trim(),
      model: $('model').value.trim(),
    }));
  } catch {
    // Ứng dụng vẫn dùng được trong phiên hiện tại nếu trình duyệt chặn lưu trữ.
  }
}

function openModelSettings(focusId = null) {
  const dialog = $('settings-dialog');
  if (!dialog.open) dialog.showModal();
  requestAnimationFrame(() => $(focusId || 'provider').focus());
}

function closeModelSettings() {
  $('settings-dialog').close();
  updateModelSummary();
}

function initializeModelSettings() {
  loadSavedModelConfig();
  updateModelSummary();
  updateLoadModelsButton();
}

$('settings-open').addEventListener('click', () => openModelSettings());
$('model-summary').addEventListener('click', () => openModelSettings());
$('settings-close').addEventListener('click', closeModelSettings);
$('settings-close-icon').addEventListener('click', closeModelSettings);

$('provider').addEventListener('change', () => {
  const defaults = providerDefaults[$('provider').value] || providerDefaults.openai_compatible;
  $('api-url').value = defaults.apiUrl;
  $('model').value = defaults.model;
  clearModelOptions('Nhập API key phù hợp rồi tải danh sách model.');
  updateModelSummary();
});

['api-url', 'model', 'api-key'].forEach((id) => {
  $(id).addEventListener('input', updateModelSummary);
});

$('api-key').addEventListener('input', updateLoadModelsButton);
$('api-key').addEventListener('change', () => {
  if ($('api-key').value.trim()) loadAvailableModels();
});
$('api-url').addEventListener('change', () => clearModelOptions('API URL đã thay đổi · tải lại danh sách model.'));
$('load-models').addEventListener('click', loadAvailableModels);

$('settings-form').addEventListener('submit', (event) => {
  event.preventDefault();
  saveModelConfig();
  updateModelSummary();
  $('settings-dialog').close();
  message('chat-message', 'Đã lưu cấu hình model. API key không được lưu.', 'success');
});
