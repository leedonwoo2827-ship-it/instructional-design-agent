// 발음 사전 편집 페이지 — /api/dict로 GET/POST/DELETE.

(function () {
  'use strict';

  const dictBody = document.getElementById('dictBody');
  const dictCount = document.getElementById('dictCount');
  const addForm = document.getElementById('addForm');
  const newKey = document.getElementById('newKey');
  const newValue = document.getElementById('newValue');
  const searchInput = document.getElementById('searchInput');
  const previewInput = document.getElementById('previewInput');
  const previewBtn = document.getElementById('previewBtn');
  const previewOutput = document.getElementById('previewOutput');
  const errorBox = document.getElementById('errorBox');

  let rules = {};

  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.classList.remove('hidden');
    setTimeout(() => errorBox.classList.add('hidden'), 5000);
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function renderTable() {
    const q = (searchInput.value || '').toLowerCase().trim();
    const entries = Object.entries(rules)
      .sort((a, b) => a[0].localeCompare(b[0], 'en'))
      .filter(([k, v]) =>
        !q || k.toLowerCase().includes(q) || v.toLowerCase().includes(q)
      );

    dictCount.textContent = Object.keys(rules).length;

    if (entries.length === 0) {
      dictBody.innerHTML = `<tr><td colspan="3" class="empty">${
        q ? '검색 결과 없음' : '아직 항목이 없습니다. 위에서 추가해주세요.'
      }</td></tr>`;
      return;
    }

    dictBody.innerHTML = entries
      .map(([k, v]) => `
        <tr data-key="${escapeHtml(k)}">
          <td class="key-cell">${escapeHtml(k)}</td>
          <td><input class="cell-edit value-edit" type="text" value="${escapeHtml(v)}" maxlength="128"></td>
          <td>
            <button class="btn-save" disabled title="발음 수정 저장">저장</button>
            <button class="btn-del" title="이 항목 삭제">삭제</button>
          </td>
        </tr>
      `)
      .join('');

    dictBody.querySelectorAll('tr').forEach((row) => {
      const key = row.dataset.key;
      const valEdit = row.querySelector('.value-edit');
      const saveBtn = row.querySelector('.btn-save');
      const delBtn = row.querySelector('.btn-del');
      const original = valEdit.value;

      valEdit.addEventListener('input', () => {
        saveBtn.disabled = valEdit.value.trim() === original || !valEdit.value.trim();
      });
      valEdit.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !saveBtn.disabled) {
          e.preventDefault();
          saveBtn.click();
        }
      });

      saveBtn.addEventListener('click', async () => {
        const newVal = valEdit.value.trim();
        if (!newVal || newVal === original) return;
        saveBtn.disabled = true;
        const oldText = saveBtn.textContent;
        saveBtn.textContent = '저장 중…';
        try {
          await upsertRule(key, newVal);
          row.classList.add('row-saved');
          setTimeout(() => row.classList.remove('row-saved'), 1200);
        } catch (err) {
          row.classList.add('row-error');
          setTimeout(() => row.classList.remove('row-error'), 1500);
          showError(`저장 실패: ${err.message}`);
          saveBtn.disabled = false;
          saveBtn.textContent = oldText;
        }
        // 성공 시에는 renderTable이 행을 새로 그려서 별도 복구 불필요
      });

      delBtn.addEventListener('click', async () => {
        if (!confirm(`"${key}" 항목을 삭제할까요?`)) return;
        delBtn.disabled = true;
        const oldText = delBtn.textContent;
        delBtn.textContent = '삭제 중…';
        try {
          await deleteRule(key);
        } catch (err) {
          showError(`삭제 실패: ${err.message}`);
          delBtn.disabled = false;
          delBtn.textContent = oldText;
        }
      });
    });
  }

  async function loadDict() {
    try {
      const res = await fetch('/api/dict');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      rules = data.rules || {};
      renderTable();
    } catch (err) {
      dictBody.innerHTML = `<tr><td colspan="3" class="empty">불러오기 실패: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  async function upsertRule(key, value) {
    const res = await fetch('/api/dict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, value }),
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    const data = await res.json();
    rules = data.rules || {};
    renderTable();
  }

  async function deleteRule(key) {
    const res = await fetch(`/api/dict/${encodeURIComponent(key)}`, { method: 'DELETE' });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    const data = await res.json();
    rules = data.rules || {};
    renderTable();
  }

  const addBtn = addForm.querySelector('button[type="submit"]');
  addForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const k = newKey.value.trim();
    const v = newValue.value.trim();
    if (!k || !v) return;
    addBtn.disabled = true;
    newKey.disabled = true;
    newValue.disabled = true;
    const oldLabel = addBtn.textContent;
    addBtn.textContent = '추가 중…';
    try {
      await upsertRule(k, v);
      newKey.value = '';
      newValue.value = '';
    } catch (err) {
      showError(`추가 실패: ${err.message}`);
    } finally {
      addBtn.disabled = false;
      newKey.disabled = false;
      newValue.disabled = false;
      addBtn.textContent = oldLabel;
      newKey.focus();
    }
  });

  searchInput.addEventListener('input', renderTable);

  async function runPreview() {
    const text = previewInput.value;
    if (!text.trim()) {
      previewOutput.classList.add('hidden');
      return;
    }
    previewBtn.disabled = true;
    const oldLabel = previewBtn.textContent;
    previewBtn.textContent = '변환 중…';
    try {
      const res = await fetch('/api/dict/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        let msg = res.statusText;
        try { msg = (await res.json()).detail || msg; } catch {}
        throw new Error(msg);
      }
      const data = await res.json();
      previewOutput.classList.remove('hidden', 'error-out');
      previewOutput.textContent = data.text || '(빈 결과)';
    } catch (err) {
      previewOutput.classList.remove('hidden');
      previewOutput.classList.add('error-out');
      previewOutput.textContent = `변환 실패: ${err.message}`;
    } finally {
      previewBtn.disabled = false;
      previewBtn.textContent = oldLabel;
    }
  }

  previewBtn.addEventListener('click', runPreview);
  previewInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      runPreview();
    }
  });

  loadDict();
})();
