const state = {
  currentSlug: null,
  project: null,
  polling: null,
  recorder: null,
  stream: null,
  chunks: [],
  recorderMode: null,
  recorderBlock: null,
  lastJobs: null,
  planDirty: false,
  narrationFingerprint: null,
  processedTerminals: {},
  pendingJobSyncs: {},
  mediaInteractionUntil: 0,
  syncInFlight: false,
  pollInFlight: false,
  chronologyHints: {},
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

function formatClock(seconds = 0) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${minutes}:${String(secs).padStart(2, '0')}`;
}

async function api(path, options = {}) {
  const r = await fetch(path, options);
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      detail = body.detail || JSON.stringify(body);
    } catch {}
    throw new Error(detail);
  }
  const type = r.headers.get('content-type') || '';
  return type.includes('application/json') ? r.json() : r;
}

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[c]));
}

function toast(message, isError = false) {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast ${isError ? 'toast-error' : ''}`;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 5200);
}

function jobCard(job) {
  if (!job || job.status === 'idle') return '';
  const error = job.error ? `<div class="error">${escapeHtml(job.error)}</div>` : '';
  return `<div class="progress-track"><div class="progress-fill" style="width:${job.progress || 0}%"></div></div>
    <div class="progress-message">${escapeHtml(job.message || job.status)} · ${job.progress || 0}%</div>${error}`;
}

function updateJobCard(selector, job) {
  const el = $(selector);
  if (!el) return;
  const sig = JSON.stringify({
    status: job?.status || 'idle', progress: job?.progress || 0,
    message: job?.message || '', error: job?.error || ''
  });
  if (el.dataset.jobSig === sig) return;
  el.dataset.jobSig = sig;
  el.innerHTML = jobCard(job);
}

function clearJobCard(selector) {
  const el = $(selector);
  if (!el) return;
  el.dataset.jobSig = '';
  el.innerHTML = '';
}

function projectFormat(p) {
  return p.settings.format === 'short_9_16'
    ? `SHORT 9:16 · ${p.settings.short_seconds}s`
    : `YOUTUBE 16:9 · ~${p.settings.desired_minutes} min`;
}

async function loadSystem() {
  try {
    const s = await api('/api/system');
    if ($('#appVersion')) $('#appVersion').textContent = s.version || '6.0.2';
    const problems = [];
    if (!s.api_key_configured) problems.push('Gemini');
    if (!s.ffmpeg || !s.ffprobe) problems.push('FFmpeg');
    if (!s.voice?.ok) problems.push('voz local');
    const badge = $('#systemBadge');
    if (problems.length) {
      badge.textContent = `Pendente: ${problems.join(' · ')}`;
      badge.className = 'badge bad';
    } else {
      badge.textContent = `Pronto · v${s.version} · Gemini + voz local`;
      badge.className = 'badge good';
    }
  } catch {
    $('#systemBadge').textContent = 'Falha no diagnóstico';
  }
}

async function loadProjects() {
  const projects = await api('/api/projects');
  const area = $('#projectList');
  area.innerHTML = projects.length ? '' : '<p class="subtitle">Nenhum projeto.</p>';
  for (const p of projects) {
    const button = document.createElement('button');
    button.className = `project-item ${p.slug === state.currentSlug ? 'active' : ''}`;
    button.innerHTML = `<strong>${escapeHtml(p.name)}</strong><small>${p.format === 'short_9_16' ? 'Short 9:16' : 'YouTube 16:9'} · ${escapeHtml(p.status)}</small>`;
    button.onclick = () => openProject(p.slug);
    area.appendChild(button);
  }
}

async function openProject(slug) {
  state.currentSlug = slug;
  state.lastJobs = null;
  state.planDirty = false;
  state.narrationFingerprint = null;
  state.processedTerminals = {};
  state.pendingJobSyncs = {};
  state.mediaInteractionUntil = 0;
  state.chronologyHints = {};
  state.project = await api(`/api/projects/${slug}`);
  $('#welcomeView').classList.add('hidden');
  $('#projectView').classList.remove('hidden');
  $('#projectTitle').textContent = state.project.settings.name;
  $('#projectProduct').textContent = state.project.settings.product || 'Tema geral / sem produto específico';
  $('#projectFormatBadge').textContent = projectFormat(state.project);
  $('#notesText').value = state.project.notes || '';
  renderAll();
  await loadVoiceProfile();
  await loadProjects();
  startPolling();
}

function renderAll() {
  renderFootage();
  renderProjectSettings();
  renderDirectorKnowledge();
  renderCatalog();
  renderDirectedEvidence();
  renderPlan();
  renderNarration();
  renderAssembly();
  renderPublication();
}

function renderFootage() {
  const files = state.project?.footage || [];
  $('#footageList').innerHTML = files.length
    ? files.map((name) => `<span class="file-chip">${escapeHtml(name)}</span>`).join('')
    : '<p class="subtitle">A pasta ainda está vazia.</p>';
}

function renderProjectSettings() {
  const settings = state.project?.settings || {};
  const audio = $('#projectAudioMode');
  const master = $('#createMaster');
  const masterProfile = $('#masterProfile');
  const createDelivery = $('#createDelivery');
  const delivery = $('#deliveryProfile');
  if (audio) audio.value = settings.audio_mode || 'narration';
  if (master) master.checked = settings.create_master !== false;
  if (masterProfile) masterProfile.value = settings.master_profile || 'premiere_hq';
  if (createDelivery) createDelivery.checked = settings.create_delivery !== false;
  if (delivery) delivery.value = settings.delivery_profile || 'youtube_1080p';
  syncExportUI();
  const notice = $('#narrationAudioModeNotice');
  const voiceToolbar = document.querySelector('.narration-toolbar');
  if (voiceToolbar) voiceToolbar.classList.toggle('hidden', (settings.audio_mode || 'narration') === 'original');
  if (notice) {
    if ((settings.audio_mode || 'narration') === 'original') {
      notice.className = 'notice original-audio-notice';
      notice.innerHTML = '<b>Áudio original ativo.</b> A etapa 5 é dispensada. O corte seguirá as tomadas escolhidas no roteiro e preservará diálogos, reações e sons originais em volume normal; siga direto para a etapa 6.';
    } else if (settings.audio_mode === 'mixed') {
      notice.className = 'notice';
      notice.innerHTML = '<b>Modo misto.</b> Narração e áudio original podem coexistir. Na etapa 6, cada tomada pode ficar mutada, ambiente baixo ou áudio original em destaque.';
    } else {
      notice.className = 'notice';
      notice.innerHTML = '<b>Narração principal.</b> O áudio original fica mutado por padrão. Gravações manuais preservam 100% do início e do fim, sem corte automático.';
    }
  }
}

function exportSpecs() {
  const fps = Number(state.project?.settings?.fps || 30);
  const highFps = fps > 30;
  return {
    premiere_hq: `H.264 High · ~${highFps ? 70 : 50} Mb/s · AAC 384 kbps / 48 kHz · pensado para finalização no Premiere`,
    premiere_max: `H.264 High · ~${highFps ? 100 : 80} Mb/s · AAC 384 kbps / 48 kHz · máxima qualidade, arquivo significativamente maior`,
    youtube_1080p: `1080p · H.264 High · ~${highFps ? 18 : 12} Mb/s · AAC 384 kbps · Fast Start`,
    social_1080p: `1080p · H.264 High · ~${highFps ? 14 : 10} Mb/s · AAC 256 kbps · Fast Start`,
  };
}

function syncExportUI(changedToggle = null) {
  const master = $('#createMaster');
  const masterProfile = $('#masterProfile');
  const delivery = $('#createDelivery');
  const deliveryProfile = $('#deliveryProfile');
  if (!master || !masterProfile || !delivery || !deliveryProfile) return;

  // Friendly guard: the app must always produce at least one file. If the user
  // tries to switch off the last output, keep it enabled instead of allowing a
  // broken configuration that would only fail at render time.
  if (!master.checked && !delivery.checked) {
    if (changedToggle === master) delivery.checked = true;
    else master.checked = true;
    toast('Mantenha pelo menos uma saída ativa: Master ou arquivo de publicação.', true);
  }

  masterProfile.disabled = !master.checked;
  deliveryProfile.disabled = !delivery.checked;
  $('#masterOutputCard')?.classList.toggle('enabled', master.checked);
  $('#deliveryOutputCard')?.classList.toggle('enabled', delivery.checked);

  const specs = exportSpecs();
  const masterSpec = $('#masterSpec');
  const deliverySpec = $('#deliverySpec');
  if (masterSpec) masterSpec.textContent = specs[masterProfile.value] || specs.premiere_hq;
  if (deliverySpec) deliverySpec.textContent = specs[deliveryProfile.value] || specs.youtube_1080p;

  const activeCount = Number(master.checked) + Number(delivery.checked);
  const badge = $('#exportSummaryBadge');
  if (badge) {
    badge.textContent = activeCount === 2 ? '2 saídas' : '1 saída';
    badge.className = 'badge good';
  }
  const title = $('#exportSelectionTitle');
  const text = $('#exportSelectionText');
  const assemble = $('#assembleButton');
  if (master.checked && delivery.checked) {
    if (title) title.textContent = 'Master + arquivo de publicação';
    if (text) text.textContent = 'Serão gerados dois arquivos independentes: um para editar e outro para publicar.';
    if (assemble) assemble.textContent = '2. Gerar Master + publicação';
  } else if (master.checked) {
    if (title) title.textContent = 'Somente Master para Premiere';
    if (text) text.textContent = 'Será gerado apenas o arquivo Master de alta qualidade.';
    if (assemble) assemble.textContent = '2. Gerar Master para Premiere';
  } else {
    if (title) title.textContent = 'Somente arquivo de publicação';
    if (text) text.textContent = 'Será gerado apenas o MP4 otimizado para o destino selecionado.';
    if (assemble) assemble.textContent = '2. Gerar arquivo de publicação';
  }
}

function renderDirectorKnowledge() {
  const knowledge = state.project?.knowledge || { workflow_mode: 'autopilot', editing_style: 'auto', chronology_source: 'auto', locked_facts: [], chronology_mode: 'guided', footage_order: state.project?.footage || [], chronology_notes: '' };
  const mode = $('#chronologyMode');
  const workflow = $('#workflowMode');
  const editing = $('#editingStyle');
  const facts = $('#lockedFacts');
  const notes = $('#chronologyNotes');
  if (workflow) workflow.value = knowledge.workflow_mode || 'autopilot';
  if (editing) editing.value = knowledge.editing_style || 'auto';
  if (mode) mode.value = knowledge.chronology_mode || 'guided';
  document.body.classList.remove('mode-autopilot', 'mode-quick_review', 'mode-director');
  document.body.classList.add(`mode-${knowledge.workflow_mode || 'autopilot'}`);
  const directorPanel = $('#directorAdvanced');
  const evidencePanel = $('#evidenceLabAdvanced');
  if ((knowledge.workflow_mode || 'autopilot') === 'director') { if (directorPanel) directorPanel.open = true; if (evidencePanel) evidencePanel.open = true; }
  if (facts && document.activeElement !== facts) facts.value = (knowledge.locked_facts || []).join('\n');
  if (notes && document.activeElement !== notes) notes.value = knowledge.chronology_notes || '';
  const order = [...(knowledge.footage_order || state.project?.footage || [])];
  if (state.project?.knowledge) state.project.knowledge.footage_order = order;
  const area = $('#footageOrder');
  if (!area) return;
  area.innerHTML = order.length ? order.map((name, index) => {
    const hint = state.chronologyHints?.[name];
    const recorded = hint?.recorded_at ? `<small class="recorded-at">gravado: ${escapeHtml(hint.recorded_at)}</small>` : '';
    return `<div class="order-row" data-index="${index}">
      <span class="order-index">${String(index + 1).padStart(2, '0')}</span><span class="order-name">${escapeHtml(name)}${recorded}</span>
      <button class="secondary order-up" data-index="${index}" ${index === 0 ? 'disabled' : ''}>↑</button>
      <button class="secondary order-down" data-index="${index}" ${index === order.length - 1 ? 'disabled' : ''}>↓</button>
    </div>`;
  }).join('') : '<p class="subtitle">Adicione filmagens primeiro.</p>';
  $$('.order-up').forEach((btn) => btn.onclick = () => moveFootage(Number(btn.dataset.index), -1));
  $$('.order-down').forEach((btn) => btn.onclick = () => moveFootage(Number(btn.dataset.index), 1));
}

function moveFootage(index, delta) {
  if (!state.project?.knowledge) state.project.knowledge = { workflow_mode: 'autopilot', editing_style: 'auto', chronology_source: 'auto', locked_facts: [], chronology_mode: 'guided', footage_order: [...(state.project?.footage || [])], chronology_notes: '' };
  const order = state.project.knowledge.footage_order || [];
  const target = index + delta;
  if (target < 0 || target >= order.length) return;
  [order[index], order[target]] = [order[target], order[index]];
  renderDirectorKnowledge();
}

async function suggestChronology() {
  if (!state.currentSlug) return;
  const status = $('#chronologySuggestionStatus');
  if (status) status.textContent = 'Lendo metadados das filmagens...';
  try {
    const result = await api(`/api/projects/${state.currentSlug}/chronology/suggestion`);
    state.chronologyHints = Object.fromEntries((result.hints || []).map((h) => [h.filename, h]));
    if (result.confidence === 'unavailable') {
      if (status) status.textContent = result.message || 'Sem metadados suficientes.';
      renderDirectorKnowledge();
      return toast(result.message || 'Sem dados de gravação suficientes; ordene manualmente.', true);
    }
    if (!state.project?.knowledge) state.project.knowledge = {};
    state.project.knowledge.footage_order = [...(result.suggested_order || state.project.footage || [])];
    renderDirectorKnowledge();
    if (status) status.textContent = result.message || '';
    toast('Sugestão aplicada na tela. Confira a sequência e clique em “Salvar direção”.');
  } catch (e) {
    if (status) status.textContent = '';
    toast(e.message, true);
  }
}


async function persistKnowledge({ manualChronology = false } = {}) {
  if (!state.project?.knowledge) state.project.knowledge = {};
  const existing = state.project.knowledge;
  const payload = {
    workflow_mode: $('#workflowMode')?.value || existing.workflow_mode || 'autopilot',
    editing_style: $('#editingStyle')?.value || existing.editing_style || 'auto',
    chronology_source: manualChronology ? 'manual' : (existing.chronology_source || 'auto'),
    locked_facts: ($('#lockedFacts')?.value || '').split(/\r?\n/).map((x) => x.trim()).filter(Boolean),
    chronology_mode: $('#chronologyMode')?.value || existing.chronology_mode || 'guided',
    footage_order: [...(existing.footage_order || state.project.footage || [])],
    chronology_notes: ($('#chronologyNotes')?.value || '').trim(),
    updated_at: existing.updated_at || '',
  };
  const result = await api(`/api/projects/${state.currentSlug}/knowledge`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
  });
  state.project.knowledge = result.knowledge;
  state.project.footage = [...result.knowledge.footage_order];
  renderDirectorKnowledge();
  return result.knowledge;
}

async function saveDirectorKnowledge() {
  await persistKnowledge({ manualChronology: true });
  toast('Direção avançada salva. O Autopilot respeitará cronologia, fatos e estilo.');
  await refreshProject(true);
}

async function saveWorkflowMode() {
  try {
    await persistKnowledge({ manualChronology: false });
    toast($('#workflowMode').value === 'autopilot' ? 'Autopilot definido como modo principal.' : 'Modo de trabalho atualizado.');
  } catch (e) { toast(e.message, true); }
}

function renderDirectedEvidence() {
  const result = state.project?.directed_evidence;
  const area = $('#directedEvidenceResults');
  if (!area) return;
  const files = state.project?.footage || [];
  const select = $('#manualEvidenceFile');
  if (select) {
    const current = select.value;
    select.innerHTML = files.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
    if (files.includes(current)) select.value = current;
    if (!select.dataset.bound) {
      select.dataset.bound = '1';
      select.onchange = updateManualEvidencePlayer;
    }
    updateManualEvidencePlayer();
  }
  if (!result) { area.innerHTML = ''; return; }
  const candidates = result.candidates || [];
  const warnings = (result.warnings || []).length ? `<div class="notice">${result.warnings.map((w) => `<div>${escapeHtml(w)}</div>`).join('')}</div>` : '';
  area.innerHTML = `<div class="directed-title"><b>Busca:</b> ${escapeHtml(result.target || '')} · ${candidates.length} candidato(s)</div>${warnings}` +
    (candidates.length ? candidates.map((c, index) => `<article class="directed-candidate">
      <div><span class="eyebrow">CANDIDATO ${index + 1} · ${Math.round(Number(c.confidence || 0) * 100)}%</span><h4>${escapeHtml(c.filename)}</h4><p>${Number(c.source_start_seconds).toFixed(2)}–${Number(c.source_end_seconds).toFixed(2)}s · ${escapeHtml(c.detection_source || '')}</p><p>${escapeHtml(c.observed_evidence || '')}</p><div>${evidenceList(c.visual_phenomena || [])}</div></div>
      <div class="button-row"><a class="secondary link-button" target="_blank" href="/api/projects/${state.currentSlug}/evidence/search/preview/${index}">▶ Ver microtrecho</a><button class="primary accept-directed" data-index="${index}">✓ Confirmar evidência</button></div>
    </article>`).join('') : '<div class="notice">Nenhum candidato atingiu o gate. Use a marcação manual abaixo se você sabe onde o evento está.</div>');
  $$('.accept-directed').forEach((btn) => btn.onclick = async () => {
    try {
      await api(`/api/projects/${state.currentSlug}/evidence/search/accept/${btn.dataset.index}`, { method: 'POST' });
      toast('Evidência confirmada e adicionada ao catálogo com confiança 100%.');
      await refreshProject(true);
    } catch (e) { toast(e.message, true); }
  });
}

function updateManualEvidencePlayer() {
  const select = $('#manualEvidenceFile'), player = $('#manualEvidencePlayer');
  const status = $('#manualEvidencePlayerStatus');
  if (!select || !player || !state.currentSlug || !select.value) return;
  const expected = `/api/projects/${state.currentSlug}/footage-preview/${encodeURIComponent(select.value)}`;

  if (!player.dataset.bound) {
    player.dataset.bound = '1';
    player.playsInline = true;
    player.onloadedmetadata = () => {
      if (status) status.textContent = `Prévia pronta · ${formatClock(player.duration || 0)} · H.264 compatível com o navegador`;
    };
    player.onerror = () => {
      if (status) status.textContent = 'Não foi possível abrir a prévia. O Video Factory tentou usar um proxy H.264 compatível.';
      toast('Falha ao carregar a prévia do Evidence Lab. Veja o diagnóstico antes de marcar evidência.', true);
    };
  }

  const currentPath = (() => {
    try { return new URL(player.src || '', window.location.href).pathname; } catch (_) { return ''; }
  })();
  if (currentPath !== expected) {
    if (status) status.textContent = 'Preparando prévia compatível…';
    player.pause();
    player.removeAttribute('src');
    player.load();
    player.src = expected;
    player.load();
  }
}

async function startDirectedEvidenceSearch() {
  const target = ($('#evidenceTarget')?.value || '').trim();
  if (target.length < 3) return toast('Descreva exatamente a evidência que precisa ser encontrada.', true);
  try {
    await api(`/api/projects/${state.currentSlug}/evidence/search`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target, filenames: [], max_candidates: 6 })
    });
    toast('Busca dirigida iniciada. O Evidence Lab vai procurar e verificar microtrechos.');
  } catch (e) { toast(e.message, true); }
}

async function saveManualEvidence() {
  const filename = $('#manualEvidenceFile')?.value || '';
  const label = ($('#manualEvidenceLabel')?.value || '').trim();
  const start = Number($('#manualEvidenceStart')?.value || 0);
  const end = Number($('#manualEvidenceEnd')?.value || 0);
  if (!filename || label.length < 3 || !(end > start)) return toast('Informe arquivo, descrição, início e fim válidos.', true);
  try {
    const kind = $('#manualEvidenceKind')?.value || 'phenomenon';
    const payload = {
      filename, start_seconds: start, end_seconds: end, label, event_kind: kind,
      actions: (kind === 'action' || kind === 'speech') ? [label] : [],
      states: kind === 'state' ? [label] : [],
      visual_phenomena: kind === 'phenomenon' ? [label] : [],
      critical_action: Boolean($('#manualEvidenceCritical')?.checked),
    };
    await api(`/api/projects/${state.currentSlug}/evidence/manual`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    toast('Evidência manual registrada. Ela passa a ter prioridade máxima.');
    await refreshProject(true);
  } catch (e) { toast(e.message, true); }
}

function evidenceList(items) {
  const values = (items || []).filter(Boolean);
  if (!values.length) return '<span class="evidence-empty">—</span>';
  return values.map((item) => `<span class="evidence-chip">${escapeHtml(String(item))}</span>`).join('');
}

function renderEvidenceSegment(segment, index) {
  const confidence = Math.round(Number(segment.confidence || 0) * 100);
  const evidenceTimes = (segment.evidence_timestamps || []).map((value) => `${Number(value).toFixed(2)}s`);
  const kind = escapeHtml(segment.event_kind || 'broll');
  const critical = Boolean(segment.critical_action);
  const sourceBadge = segment.verified_by_user ? '<span class="evidence-badge manual">CONFIRMADA PELO AUTOR</span>' : segment.detection_source === 'directed_search_verified' ? '<span class="evidence-badge directed">BUSCA DIRIGIDA</span>' : '';
  const status = critical ? '<span class="evidence-badge critical">CRÍTICO</span>' : '<span class="evidence-badge">EVIDÊNCIA</span>';

  const phenomena = segment.visual_phenomena || [];
  return `<article class="evidence-item ${critical ? 'critical' : ''}">
    <div class="evidence-item-head">
      <div>
        <span class="evidence-index">${String(index + 1).padStart(2, '0')}</span>
        ${status}
        ${sourceBadge}
        <span class="evidence-badge kind">${kind}</span>
      </div>
      <div class="evidence-time">${Number(segment.start_seconds).toFixed(2)}–${Number(segment.end_seconds).toFixed(2)}s</div>
    </div>
    <p class="evidence-description">${escapeHtml(segment.description || '')}</p>
    <div class="evidence-meta-grid">
      <div><b>ID</b><span>${escapeHtml(segment.event_id || '—')}</span></div>
      <div><b>Confiança</b><span>${confidence}%</span></div>
      <div><b>Estilo</b><span>${escapeHtml(segment.shot_style || 'normal')}</span></div>
      <div><b>Uso</b><span>${segment.use === false ? 'não usar' : escapeHtml(segment.recommended_use || 'utilizável')}</span></div>
    </div>
    <div class="evidence-group"><b>Ações observadas</b><div>${evidenceList(segment.actions)}</div></div>
    <div class="evidence-group"><b>Estados observados</b><div>${evidenceList(segment.states)}</div></div>
    <div class="evidence-group"><b>Fenômenos visuais</b><div class="${phenomena.length ? 'phenomena-found' : ''}">${evidenceList(phenomena)}</div></div>
    <div class="evidence-group"><b>Objetos</b><div>${evidenceList(segment.objects)}</div></div>
    <div class="evidence-group"><b>Instantes de evidência</b><div>${evidenceList(evidenceTimes)}</div></div>
  </article>`;
}

function renderCatalog() {
  const videos = state.project?.catalog?.videos || [];
  const area = $('#catalogSummary');
  const notice = $('#analysisModeNotice');
  if (notice) {
    if (!videos.length) {
      notice.innerHTML = '<span class="subtitle">A análise profissional ainda não foi executada.</span>';
    } else if (state.project?.analysis_professional) {
      notice.innerHTML = '<b class="success">✓ Evidence Engine ativo.</b> Ações rápidas e fenômenos visuais possuem IDs temporais para o sincronismo final.';
    } else {
      notice.innerHTML = '<b class="error">Análise antiga detectada.</b> Execute “Analisar filmagens” novamente antes da etapa 6. Seu roteiro e suas gravações não serão apagados.';
    }
  }
  if (!videos.length) {
    area.innerHTML = '<p class="subtitle">Nenhuma análise ainda.</p>';
    return;
  }
  area.innerHTML = `<h3>${videos.length} arquivo(s) analisado(s)</h3>` + videos.map((v) => {
    const segments = v.segments || [];
    const useful = segments.filter((x) => x.use).length;
    const holds = segments.filter((x) => ['hold', 'demo'].includes(x.shot_style)).length;
    const critical = segments.filter((x) => x.critical_action).length;
    const phenomena = [...new Set(segments.flatMap((x) => x.visual_phenomena || []).filter(Boolean))];
    const phenomenaSummary = phenomena.length
      ? `<div class="evidence-phenomena-summary"><b>Fenômenos visuais detectados:</b> ${evidenceList(phenomena)}</div>`
      : '<div class="evidence-phenomena-summary empty"><b>Fenômenos visuais:</b> nenhum fenômeno específico foi marcado nesta análise.</div>';
    const details = segments.length
      ? `<details class="evidence-details"><summary>Ver ${segments.length} evidência(s) com timestamps e eventos</summary><div class="evidence-timeline">${segments.map(renderEvidenceSegment).join('')}</div></details>`
      : '<div class="notice">Nenhuma evidência temporal foi retornada para este arquivo.</div>';
    return `<div class="catalog-video"><div class="catalog-sequence">SEQUÊNCIA ${String(v.sequence_index || 0).padStart(2, '0')}</div><h3>${escapeHtml(v.filename)}</h3><p>${escapeHtml(v.summary || '')}</p>
      <small>${useful} evidências úteis · ${critical} evento(s) crítico(s) · ${holds} tomada(s) sustentadas/demonstrativas</small>
      ${phenomenaSummary}
      ${details}
    </div>`;
  }).join('');
}

function renderPlan() {
  const blocks = state.project?.plan?.blocks || [];
  const area = $('#scriptBlocks');
  if (!blocks.length) {
    area.innerHTML = '<p class="subtitle">Gere o roteiro depois da análise.</p>';
    return;
  }
  area.innerHTML = blocks.map((b) => `<article class="script-block" data-block="${b.number}">
    <div class="block-head"><div><span class="eyebrow">BLOCO ${String(b.number).padStart(2, '0')} · ${escapeHtml(b.block_type || 'body')}</span><h3>${escapeHtml(b.title)}</h3></div><span class="pill">${escapeHtml(b.pacing || 'normal')}</span></div>
    <textarea class="narration-edit" data-number="${b.number}" ${b.narration ? '' : 'placeholder="Deixe vazio para uma demonstração sem narração"'}>${escapeHtml(b.narration || '')}</textarea>
    <div class="clip-list">${(b.clips || []).map((c) => `<div><b>${escapeHtml(c.filename)}</b> · ${Number(c.start_seconds).toFixed(1)}–${Number(c.end_seconds).toFixed(1)}s · ${escapeHtml(c.shot_style || 'normal')} · ${escapeHtml(c.vertical_strategy || 'crop')}<br><span>${escapeHtml(c.reason || '')}</span></div>`).join('')}</div>
  </article>`).join('');
  $$('.narration-edit').forEach((t) => t.addEventListener('input', () => { state.planDirty = true; }));
}

async function savePlan() {
  if (!state.project?.plan) return toast('Não há roteiro para salvar.', true);
  const plan = structuredClone(state.project.plan);
  $$('.narration-edit').forEach((t) => {
    const block = plan.blocks.find((x) => x.number === Number(t.dataset.number));
    if (block) block.narration = t.value.trim();
  });
  await api(`/api/projects/${state.currentSlug}/plan`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(plan)
  });
  state.planDirty = false;
  toast('Roteiro salvo. O sincronismo final será reconstruído depois da narração.');
  await refreshProject(true);
}

async function loadVoiceProfile() {
  try {
    const r = await api('/api/voice/profile');
    const p = r.profile, e = r.engine;
    $('#voiceEngineBadge').textContent = e?.ok ? 'Motor disponível' : 'Motor indisponível';
    $('#voiceEngineBadge').className = `badge ${e?.ok ? 'good' : 'bad'}`;
    $('#voiceProfileStatus').innerHTML = p.prepared
      ? '<b class="success">✓ Sua voz está preparada.</b> O perfil será reutilizado em todos os projetos.'
      : '<b>Configuração necessária.</b> Grave ou envie uma amostra limpa de 20–30 segundos.';
    $('#pronunciationText').value = Object.entries(p.pronunciation_rules || {}).map(([k, v]) => `${k}=${v}`).join('\n');
    if (p.prepared) {
      $('#voiceReferencePlayer').src = `/api/voice/reference?x=${p.updated_at || 'profile'}`;
      $('#voiceReferencePlayer').classList.remove('hidden');
    }
  } catch (e) {
    $('#voiceProfileStatus').innerHTML = `<span class="error">${escapeHtml(e.message)}</span>`;
  }
}

function narrationSignature(blocks) {
  return JSON.stringify((blocks || []).map((b) => ({
    number: b.number, title: b.title, narration: b.narration, needs_voice: b.needs_voice,
    approved: b.approved, approved_source: b.approved_source, approved_candidate: b.approved_candidate,
    approved_version: b.approved_version,
    candidates: (b.candidates || []).map((c) => ({
      candidate: c.candidate, duration_seconds: c.duration_seconds, quality: c.quality,
      seed: c.seed, spoken_text: c.spoken_text, quality_warning: c.quality_warning
    }))
  })));
}

function renderNarration() {
  const blocks = state.project?.voice_blocks || [];
  const area = $('#narrationBlocks');
  const audioMode = state.project?.settings?.audio_mode || 'narration';
  if (audioMode === 'original') {
    state.narrationFingerprint = 'original-audio-mode';
    area.innerHTML = '<div class="original-audio-skip"><h3>✓ Narração não é necessária</h3><p>Este projeto usa o áudio original das filmagens. O Video Factory preserva fala, reações e sons dos trechos escolhidos e você pode seguir direto para <b>6. Sincronismo & Montagem</b>.</p></div>';
    return;
  }
  const signature = narrationSignature(blocks);
  if (signature === state.narrationFingerprint && area.childElementCount) return;
  state.narrationFingerprint = signature;
  if (!blocks.length) {
    area.innerHTML = '<p class="subtitle">Primeiro gere o roteiro.</p>';
    return;
  }
  area.innerHTML = blocks.map((b) => {
    if (!b.needs_voice) return `<article class="record-card recorded"><h3>Bloco ${b.number} · ${escapeHtml(b.title)}</h3><p>Sem narração — trecho demonstrativo/ambiente.</p><span class="success">✓ Pronto</span></article>`;
    const candidates = (b.candidates || []).map((c) => {
      const version = encodeURIComponent(c.seed || c.duration_seconds || c.candidate);
      const warning = c.quality_warning ? `<div class="warning-text">${escapeHtml(c.quality_warning)}</div>` : '';
      return `<div class="candidate"><div><b>Alternativa ${c.candidate}</b> <small>${Number(c.duration_seconds || 0).toFixed(1)} s · ${escapeHtml(c.quality || '')}</small></div>${warning}
        <audio controls preload="metadata" src="${c.url}?v=${version}"></audio>
        <button class="primary approve-candidate" data-block="${b.number}" data-candidate="${c.candidate}">✓ Aprovar esta voz</button></div>`;
    }).join('');
    const approvedVersion = encodeURIComponent(b.approved_version || `${b.approved_source || 'voz'}-${b.approved_candidate || 0}`);
    return `<article class="record-card ${b.approved ? 'recorded' : ''}" id="voiceBlock-${b.number}">
      <div class="block-head"><div><span class="eyebrow">BLOCO ${String(b.number).padStart(2, '0')}</span><h3>${escapeHtml(b.title)}</h3></div><span class="${b.approved ? 'success' : 'error'}">${b.approved ? '✓ APROVADA' : 'PENDENTE'}</span></div>
      <p class="narration-text">${escapeHtml(b.narration)}</p>
      ${b.approved ? `<div class="approved-row"><audio controls preload="metadata" src="${b.approved_url}?v=${approvedVersion}"></audio><span>Fonte: ${escapeHtml(b.approved_source || 'voz')}</span><button class="danger delete-approved" data-block="${b.number}">Descartar aprovação</button></div>` : ''}
      <div class="progress-card block-voice-progress" id="voiceProgressBlock-${b.number}"></div>
      <div class="candidate-grid">${candidates}</div>
      <div class="button-row"><button class="secondary generate-block" data-block="${b.number}">Gerar/regerar alternativas</button><button class="secondary manual-record" data-block="${b.number}">● Gravar eu mesmo</button><button class="danger manual-stop hidden" data-block="${b.number}">■ Parar</button></div>
    </article>`;
  }).join('');
  bindNarrationButtons();
}

function bindNarrationButtons() {
  $$('.approve-candidate').forEach((x) => x.onclick = async () => {
    await api(`/api/projects/${state.currentSlug}/voice/approve/${x.dataset.block}/${x.dataset.candidate}`, { method: 'POST' });
    toast('Voz aprovada.');
    await refreshProject(true);
  });
  $$('.generate-block').forEach((x) => x.onclick = () => generateVoice(Number(x.dataset.block)));
  $$('.delete-approved').forEach((x) => x.onclick = async () => {
    await api(`/api/projects/${state.currentSlug}/recording/${x.dataset.block}`, { method: 'DELETE' });
    await refreshProject(true);
  });
  $$('.manual-record').forEach((x) => x.onclick = () => startRecording('block', Number(x.dataset.block)));
  $$('.manual-stop').forEach((x) => x.onclick = () => stopRecording());
}

async function generateVoice(block = null) {
  const body = { quality: $('#voiceQuality').value, candidates: Number($('#voiceCandidates').value) };
  const url = block ? `/api/projects/${state.currentSlug}/voice/generate/${block}` : `/api/projects/${state.currentSlug}/voice/generate-all`;
  if (block) {
    clearJobCard('#voiceProgress');
    updateJobCard(`#voiceProgressBlock-${block}`, { status: 'queued', progress: 0, message: `Bloco ${block} na fila` });
    document.querySelector(`#voiceBlock-${block}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  await api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  toast(block ? `Gerando alternativas do bloco ${block}...` : 'Gerando todas as falas...');
}

async function startRecording(mode, block = null) {
  try {
    if (state.recorder && state.recorder.state === 'recording') return;
    state.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false } });
    state.chunks = [];
    state.recorderMode = mode;
    state.recorderBlock = block;
    state.recorder = new MediaRecorder(state.stream);
    state.recorder.ondataavailable = (e) => { if (e.data.size) state.chunks.push(e.data); };
    state.recorder.onstop = recordingStopped;
    // Start capturing BEFORE telling the creator to speak. This gives the
    // browser/codec a pre-roll and avoids losing the first plosive/syllable.
    state.recorder.start(250);
    state.recordingArmedAt = Date.now();
    if (mode === 'reference') {
      $('#recordReferenceButton').classList.add('hidden');
      $('#stopReferenceButton').classList.remove('hidden');
      $('#voiceProfileProgress').innerHTML = '<span class="recording-dot"></span> Gravando amostra...';
    } else {
      document.querySelector(`.manual-record[data-block="${block}"]`)?.classList.add('hidden');
      document.querySelector(`.manual-stop[data-block="${block}"]`)?.classList.remove('hidden');
      toast(`Gravando bloco ${block}. Aguarde “FALE AGORA” para começar.`);
      setTimeout(() => {
        if (state.recorder?.state === 'recording' && state.recorderBlock === block) {
          toast(`🎙️ FALE AGORA — bloco ${block}`);
        }
      }, 800);
    }
  } catch (e) {
    toast(`Não consegui acessar o microfone: ${e.message}`, true);
  }
}

function stopRecording() {
  if (state.recorder?.state !== 'recording') return;
  // Capture a short post-roll after the click. The server removes only confirmed
  // edge silence and keeps a safety handle, so the final syllable remains protected.
  if (state.stopRecordingPending) return;
  state.stopRecordingPending = true;
  toast('Finalizando gravação… protegendo a última sílaba e limpando apenas o silêncio da borda.');
  setTimeout(() => {
    if (state.recorder?.state === 'recording') state.recorder.stop();
    state.stopRecordingPending = false;
  }, 550);
}

async function recordingStopped() {
  state.stream?.getTracks().forEach((t) => t.stop());
  const blob = new Blob(state.chunks, { type: state.recorder.mimeType || 'audio/webm' });
  try {
    if (state.recorderMode === 'reference') {
      await uploadVoiceReference(blob, 'amostra.webm');
    } else {
      const form = new FormData();
      form.append('block_number', String(state.recorderBlock));
      form.append('audio', blob, `bloco_${state.recorderBlock}.webm`);
      await api(`/api/projects/${state.currentSlug}/recording`, { method: 'POST', body: form });
      toast(`Bloco ${state.recorderBlock} gravado e aprovado com corte inteligente de silêncio e margens de segurança.`);
      await refreshProject(true);
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    $('#recordReferenceButton')?.classList.remove('hidden');
    $('#stopReferenceButton')?.classList.add('hidden');
    state.recorder = null;
    state.chunks = [];
  }
}

async function uploadVoiceReference(blob, name) {
  $('#voiceProfileProgress').innerHTML = '<div class="progress-message">Limpando a amostra e criando seu perfil local. Na primeira vez, o modelo pode ser baixado automaticamente...</div>';
  const form = new FormData();
  form.append('audio', blob, name);
  try {
    await api('/api/voice/profile', { method: 'POST', body: form });
    toast('Sua voz foi preparada com sucesso.');
    $('#voiceProfileProgress').innerHTML = '<div class="success">✓ Perfil de voz criado.</div>';
    await loadVoiceProfile();
  } catch (e) {
    $('#voiceProfileProgress').innerHTML = `<div class="error">${escapeHtml(e.message)}</div>`;
    throw e;
  }
}

function renderSyncMap() {
  const sync = state.project?.sync;
  const area = $('#syncMap');
  const badge = $('#syncReadyBadge');
  const buildButton = $('#buildSyncButton');
  const assembleButton = $('#assembleButton');
  const voicesReady = (state.project?.voice_blocks || []).every((b) => b.approved);
  const analysisReady = Boolean(state.project?.analysis_professional);

  buildButton.disabled = !voicesReady || !analysisReady;
  if (!analysisReady) {
    badge.textContent = 'Reanálise necessária'; badge.className = 'badge bad'; assembleButton.disabled = true;
    area.innerHTML = '<div class="notice"><b>Catálogo profissional ausente.</b> O Autopilot pode refazer esta etapa automaticamente, ou use Analisar filmagens.</div>';
    return;
  }
  if (!voicesReady) {
    badge.textContent = 'Faltam narrações'; badge.className = 'badge bad'; assembleButton.disabled = true;
    area.innerHTML = '<p class="subtitle">Aprove ou grave as falas pendentes. Depois o Autopilot retoma daqui sem refazer o que já está válido.</p>';
    return;
  }
  if (!sync) {
    badge.textContent = 'Pronto para preparar'; badge.className = 'badge'; assembleButton.disabled = true;
    area.innerHTML = '<p class="subtitle">Clique em “Preparar corte profissional” ou use o Autopilot. O Continuity Engine decide os cortes depois de verificar as evidências.</p>';
    return;
  }
  const isV6 = String(sync.version || '').startsWith('6') && Array.isArray(sync.visual_runs) && sync.visual_runs.length;
  if (!isV6) {
    badge.textContent = 'Corte v6 necessário'; badge.className = 'badge bad'; assembleButton.disabled = true;
    area.innerHTML = '<div class="notice"><b>Este mapa é da arquitetura anterior ao Scene Continuity Engine v6.</b> Clique em “Preparar corte profissional”. Roteiro, análise e suas gravações são reaproveitados.</div>';
    return;
  }

  badge.textContent = sync.ready ? '✓ Corte aprovado' : 'Bloqueado para revisão';
  badge.className = `badge ${sync.ready ? 'good' : 'bad'}`;
  assembleButton.disabled = !sync.ready;
  const options = state.project?.evidence_options || [];
  const optionHtml = (selected) => options.map((o) => `<option value="${escapeHtml(o.event_id)}" ${o.event_id === selected ? 'selected' : ''}>${escapeHtml(o.filename)} · ${Number(o.start_seconds).toFixed(1)}–${Number(o.end_seconds).toFixed(1)}s · ${escapeHtml((o.description || '').slice(0, 90))}</option>`).join('');
  const warnings = (sync.warnings || []).length ? `<div class="sync-warnings"><b>Avisos:</b>${sync.warnings.map((w) => `<div>${escapeHtml(w)}</div>`).join('')}</div>` : '';
  const q = sync.edit_quality;
  const qualityHtml = q ? `<div class="quality-guard ${q.passed ? 'ok' : 'bad'}"><b>Edit Quality Guard:</b> ${q.passed ? '✓ aprovado' : '⚠ autocorreção/QA'} · ${q.run_count || 0} tomadas reais · ${Number(q.cuts_per_minute || 0).toFixed(1)} cortes/min · estilo ${escapeHtml(q.editing_style || 'auto')}${(q.repairs || []).length ? `<p><b>Autocorreções:</b> ${escapeHtml(q.repairs.join(' | '))}</p>` : ''}${(q.warnings || []).length ? `<p>${escapeHtml(q.warnings.join(' | '))}</p>` : ''}</div>` : '';
  const qa = sync.rough_cut_qa;
  const qaHtml = qa ? `<div class="rough-qa ${qa.approved ? 'approved' : 'rejected'}"><b>Crítico multimodal do rascunho:</b> ${qa.approved ? '✓ aprovado' : '✗ reprovado'} · confiança ${Math.round(Number(qa.confidence || 0) * 100)}%<p>${escapeHtml(qa.summary || '')}</p>${(qa.issues || []).map((i) => `<div class="qa-issue ${escapeHtml(i.severity)}"><b>${escapeHtml(i.issue_type)}</b> · ${Number(i.output_start_seconds || 0).toFixed(1)}s — ${escapeHtml(i.description || '')}</div>`).join('')}</div>` : '';
  const runs = sync.visual_runs || [];
  const runsHtml = `<div class="visual-runs">${runs.map((r) => `<article class="visual-run"><div class="run-meta"><b>${escapeHtml(r.run_id)}</b><span>${Number(r.output_start_seconds).toFixed(1)}–${Number(r.output_end_seconds).toFixed(1)}s</span><span>${(r.beat_ids || []).length} batida(s) cobertas</span><span>${escapeHtml(r.verification || '')}</span></div><h4>${escapeHtml(r.filename)}</h4><div>${Number(r.source_start_seconds).toFixed(2)}–${Number(r.source_end_seconds).toFixed(2)}s no original</div><small>${escapeHtml(r.reason || '')}</small><div class="sync-actions"><a class="secondary link-button" target="_blank" href="/api/projects/${state.currentSlug}/sync/run/${encodeURIComponent(r.run_id)}">▶ Prévia da tomada longa</a></div><div class="run-audio-control"><label>Áudio desta tomada</label><select class="run-audio-select" data-run="${escapeHtml(r.run_id)}"><option value="mute" ${r.audio_strategy === 'mute' ? 'selected' : ''}>🔇 Mutado</option><option value="ambient" ${r.audio_strategy === 'ambient' ? 'selected' : ''}>🔉 Áudio original baixo (ambiente)</option><option value="feature" ${r.audio_strategy === 'feature' ? 'selected' : ''}>🔊 Usar áudio original desta tomada</option></select><button class="secondary save-run-audio" data-run="${escapeHtml(r.run_id)}">Aplicar áudio</button></div></article>`).join('')}</div>`;
  const mode = state.project?.knowledge?.workflow_mode || 'autopilot';
  const beatDetails = `<details class="semantic-details" ${mode === 'director' ? 'open' : ''}><summary>Detalhes avançados: ${sync.beats.length} batidas semânticas e troca manual de evidência</summary>${(sync.beats || []).map((b) => {
    const statusClass = b.verification === 'verified' ? 'verified' : b.verification === 'manual' ? 'manual' : b.verification === 'semantic' ? 'semantic' : 'failed';
    const statusText = b.verification === 'verified' ? '✓ VERIFICADA' : b.verification === 'manual' ? '✓ MANUAL' : b.verification === 'semantic' ? '✓ SEMÂNTICA' : '✗ SEM EVIDÊNCIA';
    const preview = b.filename ? `<a class="secondary link-button" target="_blank" href="/api/projects/${state.currentSlug}/sync/preview/${encodeURIComponent(b.beat_id)}">▶ Prévia</a>` : '';
    return `<article class="sync-beat ${statusClass}"><div class="block-head"><div><span class="eyebrow">${escapeHtml(b.beat_id)} · ${Number(b.output_start_seconds).toFixed(1)}–${Number(b.output_end_seconds).toFixed(1)}s</span><h3>${escapeHtml(b.text)}</h3></div><span class="sync-status">${statusText}</span></div>${b.filename ? `<p><b>Evidência:</b> ${escapeHtml(b.filename)} · ${Number(b.source_start_seconds).toFixed(2)}–${Number(b.source_end_seconds).toFixed(2)}s<br><small>${escapeHtml(b.evidence_description || '')}</small></p>` : '<p class="error">Sem evidência.</p>'}<div class="sync-actions">${preview}<select class="sync-override" data-beat="${escapeHtml(b.beat_id)}"><option value="">Trocar evidência...</option>${optionHtml(b.event_id)}</select><button class="secondary apply-sync-override" data-beat="${escapeHtml(b.beat_id)}">Aplicar</button></div></article>`;
  }).join('')}</details>`;
  area.innerHTML = warnings + qualityHtml + qaHtml + `<div class="sync-summary"><b>${runs.length} tomadas reais para ${sync.beats.length} batidas semânticas</b> · duração ${Number(sync.total_duration_seconds || 0).toFixed(1)} s · cronologia ${escapeHtml(sync.chronology_mode || 'guided')}</div>` + runsHtml + beatDetails;
  $$('.save-run-audio').forEach((btn) => btn.onclick = async () => {
    const runId = btn.dataset.run;
    const select = document.querySelector(`.run-audio-select[data-run="${CSS.escape(runId)}"]`);
    if (!select) return;
    try {
      await api(`/api/projects/${state.currentSlug}/sync/run/${encodeURIComponent(runId)}/audio`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ audio_strategy: select.value }) });
      toast(`Áudio da tomada ${runId} atualizado.`);
      await refreshProject(true);
    } catch (e) { toast(e.message, true); }
  });
  $$('.apply-sync-override').forEach((btn) => btn.onclick = async () => {
    const beat = btn.dataset.beat;
    const select = document.querySelector(`.sync-override[data-beat="${CSS.escape(beat)}"]`);
    if (!select?.value) return toast('Escolha uma evidência.', true);
    try {
      await api(`/api/projects/${state.currentSlug}/sync/beat/${encodeURIComponent(beat)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ event_id: select.value }) });
      toast('Evidência aplicada. O Continuity Engine está reconstruindo as tomadas e revalidando o corte.');
      await refreshProject(true);
    } catch (e) { toast(e.message, true); }
  });
}

function renderAssembly() {
  const blocks = state.project?.voice_blocks || [];
  const checklist = $('#recordingChecklist');
  if (!blocks.length) {
    checklist.innerHTML = '<p class="subtitle">Roteiro ainda não criado.</p>';
  } else {
    checklist.innerHTML = blocks.map((b) => `<div class="check-item ${b.approved ? 'ok' : 'missing'}">${b.approved ? '✓' : '✗'} Bloco ${b.number}: ${escapeHtml(b.title)}${!b.needs_voice ? ' · sem narração' : ''}</div>`).join('');
  }
  renderSyncMap();
  const area = $('#resultArea');
  if (state.project?.result_exists) {
    const stale = state.project.result_stale ? '<div class="notice"><b>Resultado anterior.</b> O roteiro, áudio ou sincronismo mudou depois desta renderização. Gere novamente antes de considerar os arquivos atuais.</div>' : '';
    const settings = state.project?.settings || {};
    const masterLabel = settings.master_profile === 'premiere_max' ? 'Master Máxima' : 'Master HQ';
    const masterSpec = settings.master_profile === 'premiere_max' ? 'H.264 High · ~80/100 Mb/s' : 'H.264 High · ~50/70 Mb/s';
    const outputCards = [
      state.project?.master_exists ? `<article class="result-output-card ${!state.project?.delivery_exists ? 'primary-output' : ''}"><h4>✓ ${masterLabel} para Premiere</h4><p>MASTER_DRSAYGON_VIDEO_FACTORY.mp4 · ${masterSpec}</p><a class="secondary link-button" href="/api/projects/${state.currentSlug}/result/master">Abrir/baixar Master</a></article>` : '',
      state.project?.delivery_exists ? `<article class="result-output-card primary-output"><h4>✓ Arquivo para publicação</h4><p>CONTEUDO_PRINCIPAL.mp4 · ${settings.delivery_profile === 'social_1080p' ? 'Redes sociais 1080p HQ' : 'YouTube 1080p HQ'}</p><a class="secondary link-button" href="/api/projects/${state.currentSlug}/result/delivery">Abrir/baixar publicação</a></article>` : '',
    ].filter(Boolean).join('');
    const previewName = state.project?.delivery_exists ? 'CONTEUDO_PRINCIPAL.mp4' : 'MASTER_DRSAYGON_VIDEO_FACTORY.mp4';
    area.innerHTML = `${stale}<div class="result-card"><h3>✓ Renderização concluída</h3><p class="subtitle">Prévia: ${previewName}</p><video controls preload="metadata" src="/api/projects/${state.currentSlug}/result?v=${Date.now()}"></video><div class="result-output-grid">${outputCards}</div><div class="button-row"><button class="secondary" onclick="openFolder('result')">Abrir 05_RESULTADO</button></div></div>`;
  } else area.innerHTML = '';
}

function renderPublication() {
  const p = state.project?.publication, area = $('#publicationArea');
  const generateButton = $('#generatePublicationButton');
  if (generateButton) generateButton.textContent = p?.version === '6.0.0' ? 'Regenerar pacote SEO inteligente' : 'Gerar pacote SEO inteligente';

  const revision = state.project?.result_revision || 0;
  const stale = state.project?.result_stale ? '<div class="notice"><b>Atenção:</b> esta renderização é anterior a alguma mudança de roteiro/sincronismo.</div>' : '';
  const previewName = state.project?.delivery_exists ? 'CONTEUDO_PRINCIPAL.mp4' : 'MASTER_DRSAYGON_VIDEO_FACTORY.mp4';
  const previewRole = state.project?.delivery_exists ? 'arquivo de publicação' : 'Master para Premiere';
  const previewHref = state.project?.delivery_exists ? `/api/projects/${state.currentSlug}/result/delivery` : `/api/projects/${state.currentSlug}/result/master`;
  const videoHtml = state.project?.result_exists
    ? `${stale}<section class="publication-video-preview"><div class="publication-video-head"><div><span class="eyebrow">VÍDEO FINAL</span><h3>${previewName}</h3><p>Prévia do ${previewRole}. Revise o conteúdo antes de copiar os metadados de publicação.</p></div><span class="badge good">✓ pronto</span></div><video controls preload="metadata" src="/api/projects/${state.currentSlug}/result?v=${revision}"></video><div class="button-row"><a class="primary link-button" href="${previewHref}">Abrir/baixar MP4</a><button class="secondary" onclick="openFolder('result')">Abrir 05_RESULTADO</button></div></section>`
    : '<section class="publication-video-preview empty"><span class="eyebrow">VÍDEO FINAL</span><h3>Ainda não há MP4 final</h3><p>Conclua a montagem para visualizar o vídeo aqui.</p></section>';

  if (!p) {
    area.innerHTML = videoHtml + '<div class="publication-separator"></div><p class="subtitle">Pacote de publicação ainda não gerado.</p>';
    return;
  }

  const tags = p.tags || [];
  const oldPackage = p.version !== '6.0.0' ? '<div class="notice"><b>Pacote antigo detectado.</b> Clique em “Gerar pacote SEO inteligente” para aplicar o Publication Engine v6: identifica o tipo de conteúdo, evita títulos redundantes, pesquisa apenas quando pertinente e gera exatamente 25 tags.</div>' : '';
  const sourceLinks = (p.research_sources || []).filter((s) => s.url).map((s) => `<a class="source-chip" target="_blank" rel="noopener noreferrer" href="${escapeHtml(s.url)}">${escapeHtml(s.title || new URL(s.url).hostname)}</a>`).join('');
  const researchLabel = p.research_status === 'grounded' ? '✓ pesquisa web com fontes grounded' : p.research_status === 'official_web' ? '✓ fontes oficiais/primárias encontradas na Web' : p.research_status === 'unavailable' ? 'fontes web indisponíveis; o sistema foi instruído a não inventar fatos' : p.research_status === 'skipped' ? 'dispensada para este tipo de conteúdo' : 'não executada';
  const strategy = p.content_type ? `<span class="badge">${escapeHtml(p.content_type)}</span>` : '';
  const researchHtml = p.version === '6.0.0' ? `<div class="research-status ${['grounded','official_web'].includes(p.research_status) ? 'grounded' : ''}"><b>Estratégia editorial:</b> ${strategy} <b>Pesquisa:</b> ${researchLabel}${sourceLinks ? `<div class="source-chips">${sourceLinks}</div>` : ''}</div>` : '';
  const titleCount = [...(p.recommended_title || '')].length;

  area.innerHTML = videoHtml + `<div class="publication-separator"><span>METADADOS PARA PUBLICAÇÃO</span></div>${oldPackage}${researchHtml}<div class="publication-grid">
    <div class="publication-field featured"><div class="publication-field-head"><label>Título recomendado</label><span class="metadata-counter ${titleCount > 100 ? 'bad' : ''}">${titleCount}/100 caracteres</span></div><div class="publication-title">${escapeHtml(p.recommended_title)}</div><button onclick="copyText(${JSON.stringify(p.recommended_title).replace(/</g, '\\u003c')})" class="secondary">Copiar título</button></div>
    <div class="publication-field"><div class="publication-field-head"><label>Descrição</label><span class="metadata-counter">${[...(p.description || '')].length}/5000</span></div><textarea readonly rows="18">${escapeHtml(p.description)}</textarea><button class="secondary copy-publication" data-field="description">Copiar descrição</button></div>
    <div class="publication-field"><label>Comentário fixado</label><textarea readonly rows="5">${escapeHtml(p.pinned_comment)}</textarea><button class="secondary copy-publication" data-field="pinned_comment">Copiar comentário</button></div>
    <div class="publication-field"><div class="publication-field-head"><label>25 tags de busca</label><span class="metadata-counter ${tags.length === 25 ? '' : 'bad'}">${tags.length}/25 tags</span></div><textarea readonly rows="6">${escapeHtml(tags.join(', '))}</textarea><button class="secondary copy-publication" data-field="tags">Copiar 25 tags</button></div>
    <details class="publication-extra"><summary>Extras · alternativas de título e thumbnail</summary><div class="publication-field"><label>Alternativas de título</label>${(p.alternative_titles || []).map((t) => `<div class="alternative-title">${escapeHtml(t)} <span>${[...t].length}/100</span></div>`).join('') || '<p class="subtitle">Nenhuma alternativa.</p>'}</div><div class="publication-field"><label>Conceito de thumbnail</label><p>${escapeHtml(p.thumbnail_idea || '')}</p></div></details>
  </div>`;
  $$('.copy-publication').forEach((b) => b.onclick = () => copyText(b.dataset.field === 'tags' ? tags.join(', ') : p[b.dataset.field]));
}


async function copyText(value) { await navigator.clipboard.writeText(value); toast('Copiado.'); }

function mediaIsBusy() {
  return Date.now() < state.mediaInteractionUntil || $$('audio,video').some((m) => !m.paused && !m.ended);
}
function markMediaInteraction(ms = 2500) { state.mediaInteractionUntil = Math.max(state.mediaInteractionUntil, Date.now() + ms); }
document.addEventListener('pointerdown', (e) => { if (e.target instanceof HTMLMediaElement) markMediaInteraction(3500); }, true);
document.addEventListener('play', (e) => { if (e.target instanceof HTMLMediaElement) markMediaInteraction(3500); }, true);
document.addEventListener('playing', (e) => { if (e.target instanceof HTMLMediaElement) markMediaInteraction(3500); }, true);
document.addEventListener('seeking', (e) => { if (e.target instanceof HTMLMediaElement) markMediaInteraction(3500); }, true);
document.addEventListener('waiting', (e) => { if (e.target instanceof HTMLMediaElement) markMediaInteraction(3500); }, true);

async function refreshProject(force = false) {
  if (!state.currentSlug) return false;
  if (state.planDirty && !force) return false;
  if (mediaIsBusy() && !force) return false;
  state.project = await api(`/api/projects/${state.currentSlug}`);
  renderAll();
  return true;
}

function terminalToken(job) { return `${job?.status || ''}|${job?.updated_at || ''}`; }
function isTerminal(job) { return job?.status === 'completed' || job?.status === 'failed'; }

async function syncTerminalJob(name, job) {
  const token = terminalToken(job);
  if (!token || state.processedTerminals[name] === token) return;
  if (mediaIsBusy() || state.syncInFlight) {
    state.pendingJobSyncs[name] = { token, job: structuredClone(job) };
    return;
  }
  state.syncInFlight = true;
  try {
    state.project = await api(`/api/projects/${state.currentSlug}`);
    if (name === 'analysis') { renderFootage(); renderCatalog(); renderAssembly(); }
    else if (name === 'evidence_search') { renderDirectedEvidence(); renderCatalog(); }
    else if (name === 'script') { renderPlan(); renderNarration(); renderAssembly(); }
    else if (name === 'voice') { renderNarration(); renderAssembly(); }
    else if (name === 'sync' || name === 'assembly') renderAssembly();
    else if (name === 'publication') renderPublication();
    else if (name === 'autopilot') { renderCatalog(); renderPlan(); renderNarration(); renderAssembly(); renderPublication(); }
    state.processedTerminals[name] = token;
    delete state.pendingJobSyncs[name];
  } finally { state.syncInFlight = false; }
}

async function flushPendingJobSyncs() {
  if (mediaIsBusy() || state.syncInFlight) return;
  const entries = Object.entries(state.pendingJobSyncs);
  if (!entries.length) return;
  const [name, pending] = entries[0];
  await syncTerminalJob(name, pending.job);
}

function updateVoiceJob(job) {
  if (job?.block_number) {
    clearJobCard('#voiceProgress');
    updateJobCard(`#voiceProgressBlock-${job.block_number}`, job);
  } else {
    $$('.block-voice-progress').forEach((el) => { el.innerHTML = ''; el.dataset.jobSig = ''; });
    updateJobCard('#voiceProgress', job);
  }
}

async function poll() {
  if (!state.currentSlug || state.pollInFlight) return;
  state.pollInFlight = true;
  try {
    const jobs = await api(`/api/projects/${state.currentSlug}/jobs`);
    updateJobCard('#autopilotProgress', jobs.autopilot);
    updateJobCard('#analysisProgress', jobs.analysis);
    updateJobCard('#scriptProgress', jobs.script);
    updateJobCard('#evidenceSearchProgress', jobs.evidence_search);
    updateVoiceJob(jobs.voice);
    const activeEditJob = (jobs.assembly?.status === 'running' || jobs.assembly?.status === 'queued') ? jobs.assembly : jobs.sync;
    updateJobCard('#activeEditProgress', activeEditJob);
    updateJobCard('#publicationProgress', jobs.publication);

    if (!state.lastJobs) {
      for (const [name, job] of Object.entries(jobs)) if (isTerminal(job)) state.processedTerminals[name] = terminalToken(job);
    } else {
      for (const [name, job] of Object.entries(jobs)) {
        const previous = state.lastJobs[name];
        if (isTerminal(job) && (!isTerminal(previous) || terminalToken(previous) !== terminalToken(job))) await syncTerminalJob(name, job);
      }
    }
    state.lastJobs = structuredClone(jobs);
    await flushPendingJobSyncs();

    const logs = await api(`/api/projects/${state.currentSlug}/logs`);
    const logText = logs.logs || '';
    if ($('#logsText').textContent !== logText) $('#logsText').textContent = logText;
  } catch {} finally { state.pollInFlight = false; }
}

async function pollingLoop() { await poll(); state.polling = setTimeout(pollingLoop, 2500); }
function startPolling() { clearTimeout(state.polling); state.pollInFlight = false; pollingLoop(); }
async function openFolder(folder) { await api(`/api/projects/${state.currentSlug}/open/${folder}`, { method: 'POST' }); }

$$('.tab').forEach((t) => t.onclick = () => {
  $$('.tab').forEach((x) => x.classList.remove('active'));
  $$('.tab-panel').forEach((x) => x.classList.add('hidden'));
  t.classList.add('active');
  $(`#tab-${t.dataset.tab}`).classList.remove('hidden');
  if (t.dataset.tab === 'voiceprofile') loadVoiceProfile();
});

$('#newProjectButton').onclick = () => $('#newProjectDialog').showModal();
$('#cancelProjectButton').onclick = () => $('#newProjectDialog').close();
$$('input[name="format"]').forEach((r) => r.onchange = () => {
  const short = r.form.elements.format.value === 'short_9_16';
  $('#longDurationField').classList.toggle('hidden', short);
  $('#shortDurationField').classList.toggle('hidden', !short);
});
$('input[name="short_seconds"]').oninput = (e) => $('#shortSecondsLabel').textContent = e.target.value;
$('input[name="desired_minutes"]').oninput = (e) => $('#longMinutesLabel').textContent = e.target.value;

$('#newProjectForm').onsubmit = async (e) => {
  e.preventDefault();
  const f = new FormData(e.target), format = f.get('format');
  const data = { name: f.get('name'), product: f.get('product'), objective: f.get('objective'), format, desired_minutes: Number(f.get('desired_minutes') || 8), short_seconds: Number(f.get('short_seconds') || 60), audio_mode: f.get('audio_mode') || 'narration', create_master: true, master_profile: 'premiere_hq', create_delivery: true, delivery_profile: 'youtube_1080p' };
  try {
    const p = await api('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    $('#newProjectDialog').close(); e.target.reset(); await loadProjects(); await openProject(p.slug); toast('Projeto criado. Copie agora suas filmagens.');
  } catch (err) { toast(err.message, true); }
};

$('#saveProjectAudioButton').onclick = async () => {
  try {
    await api(`/api/projects/${state.currentSlug}/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ audio_mode: $('#projectAudioMode').value }) });
    toast('Estratégia de áudio salva. O sincronismo será recalculado quando necessário.');
    await refreshProject(true);
  } catch (e) { toast(e.message, true); }
};
$('#createMaster').onchange = (e) => syncExportUI(e.currentTarget);
$('#createDelivery').onchange = (e) => syncExportUI(e.currentTarget);
$('#masterProfile').onchange = () => syncExportUI();
$('#deliveryProfile').onchange = () => syncExportUI();
$('#saveExportSettingsButton').onclick = async () => {
  try {
    const payload = {
      create_master: $('#createMaster').checked,
      master_profile: $('#masterProfile').value,
      create_delivery: $('#createDelivery').checked,
      delivery_profile: $('#deliveryProfile').value,
    };
    await api(`/api/projects/${state.currentSlug}/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    toast('Saídas de vídeo salvas.');
    await refreshProject(true);
  } catch (e) { toast(e.message, true); }
};
$('#openRootButton').onclick = () => openFolder('root');
$('#openFootageButton').onclick = () => openFolder('footage');
$('#openInfoButton').onclick = () => openFolder('information');
$('#openPublicationButton').onclick = () => openFolder('publication');
$('#autopilotButton').onclick = async () => { try { await persistKnowledge({ manualChronology: false }); await api(`/api/projects/${state.currentSlug}/autopilot`, { method: 'POST' }); toast('Autopilot iniciado. Ele reaproveita o que já está válido e só pausa se precisar de você.'); } catch (e) { toast(e.message, true); } };
$('#workflowMode').onchange = saveWorkflowMode;
$('#saveKnowledgeButton').onclick = async () => { try { await saveDirectorKnowledge(); } catch (e) { toast(e.message, true); } };
$('#suggestChronologyButton').onclick = suggestChronology;
$('#searchEvidenceButton').onclick = startDirectedEvidenceSearch;
$('#manualEvidenceLast15').onclick = () => {
  const player = $('#manualEvidencePlayer');
  if (!player || !Number.isFinite(player.duration)) return toast('Aguarde a prévia ficar pronta.', true);
  player.currentTime = Math.max(0, player.duration - 15);
  player.focus();
};
$('#markEvidenceStart').onclick = () => { $('#manualEvidenceStart').value = ($('#manualEvidencePlayer').currentTime || 0).toFixed(2); };
$('#markEvidenceEnd').onclick = () => { $('#manualEvidenceEnd').value = ($('#manualEvidencePlayer').currentTime || 0).toFixed(2); };
$('#saveManualEvidence').onclick = saveManualEvidence;
$('#saveNotesButton').onclick = async () => { await api(`/api/projects/${state.currentSlug}/notes`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes: $('#notesText').value }) }); toast('Observações salvas.'); };
$('#analyzeButton').onclick = async () => { await api(`/api/projects/${state.currentSlug}/analyze`, { method: 'POST' }); toast('Análise de evidências iniciada.'); };
$('#generateScriptButton').onclick = async () => { await api(`/api/projects/${state.currentSlug}/generate-script`, { method: 'POST' }); toast('Roteiro em criação.'); };
$('#savePlanButton').onclick = savePlan;
$('#recordReferenceButton').onclick = () => startRecording('reference');
$('#stopReferenceButton').onclick = stopRecording;
$('#voiceReferenceFile').onchange = async (e) => { const f = e.target.files[0]; if (f) try { await uploadVoiceReference(f, f.name); } catch {} e.target.value = ''; };
$('#savePronunciationButton').onclick = async () => { await api('/api/voice/pronunciations', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: $('#pronunciationText').value }) }); toast('Dicionário salvo.'); };
$('#generateAllVoiceButton').onclick = () => generateVoice(null);
$('#buildSyncButton').onclick = async () => { try { await api(`/api/projects/${state.currentSlug}/sync/build`, { method: 'POST' }); toast('Continuity Engine: preparação do corte iniciada.'); } catch (e) { toast(e.message, true); } };
$('#assembleButton').onclick = async () => { try { await api(`/api/projects/${state.currentSlug}/assemble`, { method: 'POST' }); toast('Render final iniciado. O progresso ficará fixo acima do mapa.'); } catch (e) { toast(e.message, true); } };
$('#generatePublicationButton').onclick = async () => { await api(`/api/projects/${state.currentSlug}/publication`, { method: 'POST' }); toast('Gerando pacote de publicação.'); };

window.openFolder = openFolder;
window.copyText = copyText;
loadSystem();
loadProjects();
