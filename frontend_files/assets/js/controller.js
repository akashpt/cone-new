 let bridge = window.bridge || null;
    const channelState = { b1: {}, b2: {} };
    let cameraManualOverride = false; let cameraConnectedManual = false;

    function initWebChannel(onReady) {
      const MAX_TRIES = 200; let tries = 0;
      (function waitForQt() {
        if (window.qt && window.qt.webChannelTransport) {
          try {
            new QWebChannel(qt.webChannelTransport, function (ch) {
              bridge = ch.objects.bridge || bridge || null;
              if (onReady) onReady(bridge);
            });
          } catch (e) { console.error('QWebChannel init failed', e); if (onReady) onReady(bridge); }
          return;
        }
        if (++tries > MAX_TRIES) { console.warn('qt not found, continuing simulated.'); if (onReady) onReady(bridge); return; }
        setTimeout(waitForQt, 100);
      })();
    }


  const toast = (msg) => {
    const t = document.getElementById("toast");
    if (!t) { console.log("TOAST:", msg); return; }
    t.textContent = msg;
    t.classList.add("show");
    setTimeout(() => t.classList.remove("show"), 1400);
  };

  function setCameraUIConnected(connected, msg="") {
      const camTop = document.getElementById('cameraTop');
      const spinner = document.getElementById('camSpinner');
      const pill = document.getElementById('camToggle');
      const camText = document.getElementById('camOkText');
      const camMsg = document.getElementById('camMsg');

      if (connected) {
        if (spinner) spinner.style.display = 'none';
        if (camTop) camTop.classList.add('no-spinner');
        if (pill) { pill.classList.remove('off'); pill.classList.add('connected'); pill.setAttribute('aria-pressed','true'); }
        if (camText) camText.textContent = 'Connected';
        if (camMsg) camMsg.textContent = msg || 'Connected';
      } else {
        if (spinner) spinner.style.display = '';
        if (camTop) camTop.classList.remove('no-spinner');
        if (pill) { pill.classList.remove('connected'); pill.classList.add('off'); pill.setAttribute('aria-pressed','false'); }
        if (camText) camText.textContent = 'Disconnected';
        if (camMsg) camMsg.textContent = msg || 'No camera connection';
      }
    }

    async function init() {
      refreshCamera();
      const btnModbus = document.getElementById('btnModbus'); const btnCheckAll = document.getElementById('btnCheckAll'); const btnCheckIndividual = document.getElementById('btnCheckIndividual');
      // const camToggle = document.getElementById('camToggle'); if (camToggle) { camToggle.addEventListener('click', (ev) => { ev.preventDefault(); cameraManualOverride = true; cameraConnectedManual = !cameraConnectedManual; setCameraUIConnected(cameraConnectedManual, 'manual'); }); }
      btnModbus.addEventListener('click', () => { const modbusArea = document.getElementById('modbusArea'); const currentlyVisible = modbusArea.style.display === 'block'; if (currentlyVisible) { modbusArea.style.display = 'none'; document.getElementById('channelPanel').style.display = 'none'; } else { modbusArea.style.display = 'block'; } });

      function setActiveButton(targetBtn) { [btnCheckAll, btnCheckIndividual].forEach(b => b.classList.remove('active')); if (targetBtn) targetBtn.classList.add('active'); }

      btnCheckAll.onclick = async () => { setActiveButton(btnCheckAll); document.getElementById('allCheckNote').textContent = 'Checking all channels...'; await fetchAndRenderAllBoards(); setTimeout(() => { document.getElementById('allCheckNote').textContent = ''; }, 900); };

      btnCheckIndividual.onclick = () => { setActiveButton(btnCheckIndividual); const panel = document.getElementById('channelPanel'); panel.style.display = (panel.style.display === 'block') ? 'none' : 'block'; if (panel.style.display === 'block') populateTwoBoards(8); };

      document.getElementById('btnResetIndividual').onclick = () => resetChannels(); document.getElementById('modalClose').onclick = hideModal; document.getElementById('btnModalClose2').onclick = hideModal; document.getElementById('btnModalRefresh').onclick = async () => { document.getElementById('modalNote').textContent = 'Refreshing...'; await fetchAndRenderAllBoards(); setTimeout(() => { document.getElementById('modalNote').textContent = ''; }, 800); };

      document.getElementById('modbusArea').style.display = 'none';
    }

 // Safe helper: produce a default snapshot if bridge call fails
function emptySnapshot() {
  return {
    b1: { bits: [false, false, false, false, false, false, false, false], status: {} },
    b2: { bits: [false, false, false, false, false, false, false, false], status: {} },
    _simulated: true
  };
}

// Replace your existing fetchAndRenderAllBoards() with this
async function fetchAndRenderAllBoards() {
  const modelDatas = document.getElementById('model-datas');
  const modalNote = document.getElementById('modalNote');

  try {
    // try Python bridge first - your Bridge should expose getAllChannels()
    let parsed = null;
    if (bridge && typeof bridge.getAllChannels === 'function') {
      try {
        const txt = await bridge.getAllChannels();
        parsed = txt ? JSON.parse(txt) : null;
      } catch (e) {
        console.warn("bridge.getAllChannels failed:", e);
        parsed = null;
      }
    } else if (bridge && typeof bridge.checkAllChannels === 'function') {
      // Backwards compatibility: if you still have checkAllChannels(board) use it per-board
      parsed = { b1: { bits: [] }, b2: { bits: [] } };
      try {
        const rawB1 = await bridge.checkAllChannels('b1'); parsed.b1 = JSON.parse(rawB1 || '{}');
      } catch(e){ parsed.b1 = null; }
      try {
        const rawB2 = await bridge.checkAllChannels('b2'); parsed.b2 = JSON.parse(rawB2 || '{}');
      } catch(e){ parsed.b2 = null; }
    }

    // validate parsed shape, otherwise use simulated
    if (!parsed || (!parsed.b1 && !parsed.b2)) {
      console.warn("fetchAndRenderAllBoards: invalid snapshot from bridge, using simulated");
      const s = emptySnapshot();
      modelDatas.innerHTML = buildSideBySideBoardsHtml(s.b1, s.b2);
      showModal();
      if (modalNote) modalNote.textContent = 'Showing simulated data';
      return;
    }

    // Normalize: ensure b1 and b2 objects exist with .bits
    const b1 = parsed.b1 && parsed.b1.bits ? parsed.b1 : { bits: (parsed.b1 && parsed.b1.bits) || parsed.b1 || [] };
    const b2 = parsed.b2 && parsed.b2.bits ? parsed.b2 : { bits: (parsed.b2 && parsed.b2.bits) || parsed.b2 || [] };

    // Ensure arrays of length 8
    function normBits(x) {
      const arr = Array.isArray(x.bits) ? x.bits.slice(0,8) : [];
      while (arr.length < 8) arr.push(false);
      return { bits: arr, status: x.status || {} };
    }

    const nb1 = normBits(b1);
    const nb2 = normBits(b2);

    // Build HTML (this returns a string — guaranteed)
    const html = buildSideBySideBoardsHtml(nb1, nb2) || '';
    modelDatas.innerHTML = html;

    // clear any modal note and show modal
    if (modalNote) modalNote.textContent = '';
    showModal();

  } catch (err) {
    console.error("fetchAndRenderAllBoards error:", err);
    if (modelDatas) modelDatas.innerHTML = '';            // never leave 'undefined'
    if (modalNote) modalNote.textContent = 'Error fetching data';
    showModal();
  }
}

function buildSideBySideBoardsHtml(b1, b2) {
  function boardHtml(obj, title, boardKey) {
    const s = obj.status || {};
    return `
      <div class="board-col" style="padding:12px;">
        <div class="board-title">${title}</div>
        <div class="di-grid">
          ${[1,2,3,4,5,6,7,8].map(i => {
            const key = 'di' + i;
            const st = (s[key] && s[key].status) ? s[key].status : (obj.bits && obj.bits[i-1] ? 'ON' : 'OFF');
            const cls = st === 'ON' ? 'pill on' : 'pill off';
            // add data attributes so updateChannel can target this pill
            return `<div class="di-box">
                      <div class="di-label">DI ${i}</div>
                      <div class="${cls}" data-board="${boardKey}" data-channel="${i}">${st}</div>
                    </div>`;
          }).join('')}
        </div>
        <div class="source-note">Source: Controller</div>
      </div>`;
  }

  return `
    <div class="two-boards" style="margin:0;">
      ${boardHtml(b1 || {}, 'Board B1', 'b1')}
      ${boardHtml(b2 || {}, 'Board B2', 'b2')}
    </div>
  `;
}



    function showModal() { const modal = document.getElementById('myModal'); modal.style.display = 'block'; modal.setAttribute('aria-hidden', 'false'); const refresh = document.getElementById('btnModalRefresh'); if (refresh) refresh.focus(); }
    function hideModal() { const modal = document.getElementById('myModal'); modal.style.display = 'none'; modal.setAttribute('aria-hidden', 'true'); }

    // populateTwoBoards (stacked: Board B1 on top, B2 below)
   function populateTwoBoards(n) {
  const wrap = document.getElementById('boardsWrap');
  wrap.innerHTML = ''; // clear

  const two = document.createElement('div');
  two.className = 'two-boards stacked';
  two.style.margin = '0';

  ['b1','b2'].forEach(board => {
    const col = document.createElement('div');
    col.className = 'board-col individual-board';

    const title = document.createElement('div');
    title.className = 'individual-title';
    title.textContent = board === 'b1' ? 'Board B1' : 'Board B2';
    col.appendChild(title);

    const grid = document.createElement('div');
    grid.className = 'individual-grid';

    for (let i = 1; i <= n; i++) {
      const di = document.createElement('div');
      di.className = 'di-card';
      // crucial: use data attributes as strings
      di.dataset.board = board;
      di.dataset.channel = String(i);

      const label = document.createElement('div');
      label.className = 'di-label-small';
      label.textContent = `DI ${i}`;

      const toggles = document.createElement('div');
      toggles.className = 'di-toggle-wrap';

      const btnOn = document.createElement('button');
      btnOn.className = 'di-toggle on';
      btnOn.type = 'button';
      btnOn.textContent = 'ON';
      btnOn.title = `Set DI ${i} ON`;

      const btnOff = document.createElement('button');
      btnOff.className = 'di-toggle off';
      btnOff.type = 'button';
      btnOff.textContent = 'OFF';
      btnOff.title = `Set DI ${i} OFF`;

      toggles.appendChild(btnOn);
      toggles.appendChild(btnOff);

      const status = document.createElement('div');
      status.className = 'di-status';
      status.textContent = '—';

      const inner = document.createElement('div');
      inner.style.display = 'flex';
      inner.style.flexDirection = 'column';
      inner.style.flex = '1 1 auto';
      inner.appendChild(toggles);
      inner.appendChild(status);

      di.appendChild(label);
      di.appendChild(inner);
      grid.appendChild(di);

      // Restore any saved state (use string keys)
      const saved = (channelState[board] && channelState[board][String(i)]) ? channelState[board][String(i)] : null;
      if (saved === 'on') { btnOn.classList.add('active'); btnOff.classList.remove('active'); status.textContent = 'ON'; }
      else if (saved === 'off') { btnOff.classList.add('active'); btnOn.classList.remove('active'); status.textContent = 'OFF'; }

      // Event listeners call executeSetChannel (unchanged)
      btnOn.addEventListener('click', async (ev) => {
        ev.preventDefault();
        btnOn.classList.add('active'); btnOff.classList.remove('active'); status.textContent = 'Sending...';
        await executeSetChannel(board, i, 'on');
      });

      btnOff.addEventListener('click', async (ev) => {
        ev.preventDefault();
        btnOff.classList.add('active'); btnOn.classList.remove('active'); status.textContent = 'Sending...';
        await executeSetChannel(board, i, 'off');
      });
    }

    col.appendChild(grid);
    two.appendChild(col);
  });

  wrap.appendChild(two);
}


    function toggleChannelUI(board, channel, state) {
      const row = document.querySelector(`[data-board="${board}"][data-channel="${channel}"]`);
      if (!row) return;
      const onBtn = row.querySelector('.di-toggle.on, .small-toggle.on');
      const offBtn = row.querySelector('.di-toggle.off, .small-toggle.off');
      if (onBtn) onBtn.classList.remove('active');
      if (offBtn) offBtn.classList.remove('active');
      if (state === 'on' && onBtn) onBtn.classList.add('active');
      if (state === 'off' && offBtn) offBtn.classList.add('active');
    }

    function executeSetChannel(board, channel, state) {
      const row = document.querySelector(`[data-board="${board}"][data-channel="${channel}"]`);
      if (!row) return;
      const statusEl = row.querySelector('.di-status, .channel-status');
      if (statusEl) statusEl.textContent = 'Sending...';
      (async () => {
        if (!bridge || !bridge.checkIndividualChannel) {
          persistChannelState(board, channel, state);
          toggleChannelUI(board, channel, state);
          return;
        }
        try {
          let resText = null;
          try { resText = await bridge.checkIndividualChannel(board, String(channel), String(state)); } catch (e) { try { resText = await bridge.checkIndividualChannel(String(channel), String(state)); } catch (_) { resText = null; } }
          let parsed = null; try { parsed = resText ? JSON.parse(resText) : null; } catch (_) { parsed = null; }
          if (parsed && parsed.ok) { persistChannelState(board, channel, state); toggleChannelUI(board, channel, state); } else { if (statusEl) statusEl.textContent = (parsed && parsed.message) ? parsed.message : 'Error'; }
        } catch (err) { console.error(err); if (statusEl) statusEl.textContent = 'Comm error'; }
      })();
    }

    function persistChannelState(board, channel, state, statusHtml) {
      channelState[board] = channelState[board] || {};
      channelState[board][channel] = state;
      const row = document.querySelector(`[data-board="${board}"][data-channel="${channel}"]`);
      if (!row) return;
      const statusEl = row.querySelector('.di-status, .channel-status');
      if (statusHtml && typeof statusHtml === 'string') { if (statusEl) statusEl.innerHTML = statusHtml; return; }
      if (statusEl) statusEl.textContent = (state === 'on') ? 'ON' : 'OFF';
    }

    function resetChannels() {
      channelState.b1 = {};
      channelState.b2 = {};
      const rows = document.querySelectorAll('[data-board][data-channel]');
      rows.forEach(r => {
        const onBtn = r.querySelector('.di-toggle.on, .small-toggle.on');
        const offBtn = r.querySelector('.di-toggle.off, .small-toggle.off');
        if (onBtn) onBtn.classList.remove('active');
        if (offBtn) offBtn.classList.remove('active');
        const status = r.querySelector('.di-status, .channel-status');
        if (status) status.textContent = '—';
      });
    }

   async function refreshCamera() {
      if (!bridge || !bridge.getCameraStatus) {
        setCameraUIConnected(false);
        return;
      }

      try {
        const s = JSON.parse(await bridge.getCameraStatus());
        const ok = !!s.connected;
        setCameraUIConnected(ok, s.message || "");
      } catch (e) {
        setCameraUIConnected(false, "Unknown");
      }
    }
    function attachBridgeListeners() {
    if (!bridge || !bridge.channelChanged) {
        console.warn("Bridge signal missing!");
        return;
    }

    bridge.channelChanged.connect(function (msg) {
        const ev = JSON.parse(msg);

        // Example:
        // { board:"b1", channel:3, state:"on" }

        updateChannel(ev.board, ev.channel, ev.state);
    });
}


function updateChannel(board, channel, state) {
  // ensure channel is string for dataset match
  const chStr = String(channel);

  // Update individual card if present
  const row = document.querySelector(`[data-board="${board}"][data-channel="${chStr}"]`);
  if (row) {
    const onBtn = row.querySelector('.di-toggle.on');
    const offBtn = row.querySelector('.di-toggle.off');
    const status = row.querySelector('.di-status');

    if (state === 'on') {
      if (onBtn) onBtn.classList.add('active');
      if (offBtn) offBtn.classList.remove('active');
      if (status) status.textContent = 'ON';
    } else {
      if (offBtn) offBtn.classList.add('active');
      if (onBtn) onBtn.classList.remove('active');
      if (status) status.textContent = 'OFF';
    }
    // persist state using string key
    channelState[board] = channelState[board] || {};
    channelState[board][chStr] = state;
  }

  // Update the check-all pill if present
  // pills created by buildSideBySideBoardsHtml have data-board & data-channel
  const pill = document.querySelector(`.pill[data-board="${board}"][data-channel="${chStr}"]`);
  if (pill) {
    pill.classList.remove('on','off');
    pill.classList.add(state === 'on' ? 'on' : 'off');
    pill.textContent = (state === 'on') ? 'ON' : 'OFF';
  }
}


let trainingMode = false;

/* ---------- UI ---------- */
function applyTrainingUI(enabled) {
  const btn = document.getElementById("btnTrain");
  if (!btn) return;
  btn.classList.toggle("active", enabled);
  btn.textContent = enabled ? "🟢 Training Mode ON" : "🎓 Training Mode OFF";
}

/* ---------- READ FROM BACKEND ---------- */
async function loadTrainingMode() {
  try {
    if (!bridge || !bridge.settings_get_all) return;

    const raw = await bridge.settings_get_all();
    const data = JSON.parse(raw || "{}");

    // support both root and values
    trainingMode = Boolean(
      data.training_mode ??
      (data.values && data.values.training_mode)
    );

    applyTrainingUI(trainingMode);

  } catch (e) {
    console.error("Load training mode failed:", e);
  }
}

/* ---------- BUTTON TOGGLE ---------- */
document.getElementById("btnTrain")?.addEventListener("click", async () => {
  trainingMode = !trainingMode;
  applyTrainingUI(trainingMode);

  toast(trainingMode ? "Training Mode Enabled" : "Training Mode Disabled");

  try {
    if (bridge && bridge.set_training_mode) {
      await bridge.set_training_mode(trainingMode);   // ✅ true / false
    }
  } catch (e) {
    console.error("Training mode save failed:", e);
  }
});


document.addEventListener('DOMContentLoaded', function () {
  init();
  initWebChannel(function (bridgeObj) {
    // bridgeObj is provided by initWebChannel; ensure global set and then attach listeners
    if (bridgeObj) window.bridge = bridgeObj;
    try { refreshCamera(); } catch (e) { console.warn(e); }
    try { attachBridgeListeners(); } catch (e) { console.warn("attachBridgeListeners failed", e); }
    try { loadTrainingMode(); } catch (e) { console.warn(e); }
  });
});

