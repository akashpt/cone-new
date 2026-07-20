let bridge = null;

new QWebChannel(qt.webChannelTransport, function(channel) {
  bridge = channel.objects.bridge;

  const colorSelect = document.getElementById("cone_color");
  const countSelect = document.getElementById("cone_count");

  bridge.colorList.connect(colors => {
    colorSelect.innerHTML = "";
    colors.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      colorSelect.appendChild(opt);
    });

    if (colors.length > 0) {
      bridge.loadCounts(colors[0]);
    }
  });

  bridge.countList.connect(counts => {
    countSelect.innerHTML = "";
    counts.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      countSelect.appendChild(opt);
    });
  });

  /* ---------- EVENTS ---------- */


  

  colorSelect.addEventListener("change", () => {
    bridge.loadCounts(colorSelect.value);
  });

  /* ---------- INIT ---------- */
  bridge.loadColors();

  window.requestAnimationFrame(() => {
    try { boot(); }
      catch (e) { console.error("boot() failed:", e); }
    });
});

const $ = (id) => document.getElementById(id);

const show = (id, on) => {
  const el = $(id);
  if (!el) return;
  el.style.display = on ? "" : "none";
};

const toast = (msg) => {
  const t = $("toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 1400);
};


let selectedShiftId = null;

function setSelectedShift(id, labelText) {
  selectedShiftId = id;
  document.getElementById("shift_selected").textContent = labelText || "None";
  document.getElementById("shift_remove").disabled = !selectedShiftId;
}

function fmtTime(t) {
  // input is "HH:MM" -> keep
  return t || "";
}

async function refreshShiftList() {
  if (!bridge || !bridge.shift_list) return;

  bridge.shift_list(function(resp) {
    // resp should be JSON string
    let data = [];
    try { data = JSON.parse(resp); } catch(e){ data = []; }

    const tbody = document.getElementById("shift_tbody");
    tbody.innerHTML = "";

    if (!data.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="padding:12px; opacity:0.8;">No shifts added</td></tr>`;
      setSelectedShift(null, "None");
      return;
    }

    data.forEach(row => {
      const tr = document.createElement("tr");
      tr.style.cursor = "pointer";
      tr.innerHTML = `
        <td style="padding:10px; border-top:1px solid rgba(255,255,255,0.06);">${row.id}</td>
        <td style="padding:10px; border-top:1px solid rgba(255,255,255,0.06);">${row.shift_name || ""}</td>
        <td style="padding:10px; border-top:1px solid rgba(255,255,255,0.06);">${fmtTime(row.start_time)}</td>
        <td style="padding:10px; border-top:1px solid rgba(255,255,255,0.06);">${fmtTime(row.end_time)}</td>
        <td style="padding:10px; border-top:1px solid rgba(255,255,255,0.06);">${row.status == 1 ? "Active" : "Inactive"}</td>
      `;

      tr.onclick = () => {
        // highlight selection
        [...tbody.children].forEach(x => x.style.background = "");
        tr.style.background = "rgba(94,19,40,0.28)";
        setSelectedShift(row.id, `${row.shift_name} (#${row.id})`);
      };

      tbody.appendChild(tr);
    });
  });
}


// Add shift
document.getElementById("shift_add")?.addEventListener("click", async () => {
  if (!bridge?.shift_add) return toast("Bridge not ready");

  const name  = document.getElementById("shift_name").value.trim();
  const start = document.getElementById("shift_start").value;
  const end   = document.getElementById("shift_end").value;

  if (!name) return toast("Enter shift name");
  if (!start) return toast("Select start time");
  if (!end) return toast("Select end time");

  try {
    const resp = await bridge.shift_add(name, start, end);   // ✅ correct
    if (resp === "OK") {
      toast("Shift added");
      document.getElementById("shift_name").value = "";
      document.getElementById("shift_start").value = "";
      document.getElementById("shift_end").value = "";
      await refreshShiftList();
    } else {
      toast(resp || "Failed");
    }
  } catch (e) {
    console.error(e);
    toast("Failed");
  }
});

// Remove shift
document.getElementById("shift_remove")?.addEventListener("click", async () => {
  if (!selectedShiftId) return;
  if (!bridge?.shift_delete) return toast("Bridge not ready");

  try {
    const resp = await bridge.shift_delete(String(selectedShiftId));  // ✅ correct
    if (resp === "OK") {
      toast("Shift removed");
      selectedShiftId = null;
      await refreshShiftList();
    } else {
      toast(resp || "Failed");
    }
  } catch (e) {
    console.error(e);
    toast("Failed");
  }
});

const rangeOptions = (n = 50) =>
  Array.from({ length: n }, (_, i) => `<option value="${i + 1}">${i + 1}</option>`).join("");

/* ===================================================
   BOOT + TAB NAVIGATION
=================================================== */
// async function boot() {
//   // Tabs: only quick + entry (your entry section is commented now, but keep safe)
//   document.querySelectorAll(".tab").forEach((btn) => {
//     btn.addEventListener("click", () => {
//       document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
//       btn.classList.add("active");
//       const key = btn.dataset.sub; // quick | entry
//       show("sub-quick", key === "quick");
//       show("sub-entry", key === "entry");
//     });
//   });

//   // Fill dropdown ranges (you can change counts here)
//   if ($("cone_color")) $("cone_color").innerHTML = rangeOptions(20);      // example: 1..20 colors
//   if ($("cone_count")) $("cone_count").innerHTML = rangeOptions(50);      // 1..50
//   if ($("tip_confidence")) $("tip_confidence").innerHTML = rangeOptions(100); // 1..100
//   if ($("top_confidence")) $("top_confidence").innerHTML = rangeOptions(100);
//   if ($("bottom_confidence")) $("bottom_confidence").innerHTML = rangeOptions(100);

//   bindQuickButtons();
//   await loadSavedGlobal();
// }

/* ===================================================
   QUICK SETUP BUTTONS
=================================================== */
function bindQuickButtons() {
  // Save
  if ($("s_color")) $("s_color").onclick = () => saveKey("cone_color", $("cone_color").value);
  if ($("s_count")) $("s_count").onclick = () => saveKey("cone_count", $("cone_count").value);
  if ($("s_tip_confidence")) $("s_tip_confidence").onclick = () => saveKey("tip_confidence", $("tip_confidence").value);
  if ($("s_top_confidence")) $("s_top_confidence").onclick = () => saveKey("top_confidence", $("top_confidence").value);
  if ($("s_bottom_confidence")) $("s_bottom_confidence").onclick = () => saveKey("bottom_confidence", $("bottom_confidence").value);

  // Unlock (✖)
  if ($("c_color")) $("c_color").onclick = () => unlockKey("cone_color", "cone_color", "s_color");
  if ($("c_count")) $("c_count").onclick = () => unlockKey("cone_count", "cone_count", "s_count");
  if ($("c_tip_confidence")) $("c_tip_confidence").onclick = () => unlockKey("tip_confidence", "tip_confidence", "s_tip_confidence");
  if ($("c_top_confidence")) $("c_top_confidence").onclick = () => unlockKey("top_confidence", "top_confidence", "s_top_confidence");
  if ($("c_bottom_confidence")) $("c_bottom_confidence").onclick = () => unlockKey("bottom_confidence", "bottom_confidence", "s_bottom_confidence");
}

async function unlockKey(key, ctrlId, saveBtnId) {
  try {
    if (bridge && bridge.settings_unlock) {
      await bridge.settings_unlock(key); // global
    }
  } catch (e) {
    console.error(e);
  }

  const ctrl = $(ctrlId);
  const btn = $(saveBtnId);

  if (ctrl) { ctrl.disabled = false; ctrl.style.opacity = 1.0; }
  if (btn)  { btn.disabled = false; btn.style.opacity = 1.0; }

  toast("Unlocked");
}

async function saveKey(key, value) {
  try {
    if (!bridge || !bridge.settings_save_key) {
      toast("Bridge missing settings_save_key()");
      return;
    }

    await bridge.settings_save_key(key, String(value));
    toast("Saved ✓");

    // Lock the current field (disable select + save button)
    const map = {
      cone_color: ["cone_color", "s_color"],
      cone_count: ["cone_count", "s_count"],
      tip_confidence: ["tip_confidence", "s_tip_confidence"],
      top_confidence: ["top_confidence", "s_top_confidence"],
      bottom_confidence: ["bottom_confidence", "s_bottom_confidence"]
    };

    if (map[key]) {
      const [ctrl, btn] = map[key];
      if ($(ctrl)) { $(ctrl).disabled = true; $(ctrl).style.opacity = 0.6; }
      if ($(btn))  { $(btn).disabled = true; $(btn).style.opacity = 0.6; }
    }

    await loadSavedGlobal();
  } catch (err) {
    console.error(err);
    toast("Save failed");
  }
}

/* ===================================================
   LOAD SAVED VALUES + LOCK STATE
=================================================== */
async function loadSavedGlobal() {
  try {
    if (!bridge || !bridge.settings_get_all) return;

    const raw = await bridge.settings_get_all(); // JSON string
    const data = JSON.parse(raw || "{}");

    const v = data.values || {};
    const locked = data.locked || {};

    // set values if present
    if ($("cone_color") && v.cone_color != null) $("cone_color").value = String(v.cone_color);
    if ($("cone_count") && v.cone_count != null) $("cone_count").value = String(v.cone_count);
    if ($("tip_confidence") && v.tip_confidence != null) $("tip_confidence").value = String(v.tip_confidence);
    if ($("top_confidence") && v.top_confidence != null) $("top_confidence").value = String(v.top_confidence);
    if ($("bottom_confidence") && v.bottom_confidence != null) $("bottom_confidence").value = String(v.bottom_confidence);

    // apply lock state
    applyLock("cone_color", "cone_color", "s_color", toBool(locked.cone_color));
    applyLock("cone_count", "cone_count", "s_count", toBool(locked.cone_count));
    applyLock("tip_confidence", "tip_confidence", "s_tip_confidence", toBool(locked.tip_confidence));
    applyLock("top_confidence", "top_confidence", "s_top_confidence", toBool(locked.top_confidence));
    applyLock("bottom_confidence", "bottom_confidence", "s_bottom_confidence", toBool(locked.bottom_confidence));
  } catch (e) {
    console.error(e);
  }
}

const toBool = (x) => {
  if (typeof x === "boolean") return x;
  if (typeof x === "number") return x === 1;
  if (typeof x === "string") {
    const s = x.trim().toLowerCase();
    return s === "true" || s === "1" || s === "yes";
  }
  return false;
};

function applyLock(key, ctrlId, btnId, isLocked) {
  const ctrl = $(ctrlId);
  const btn = $(btnId);
  if (!ctrl || !btn) return;

  if (isLocked) {
    ctrl.disabled = true; ctrl.style.opacity = 0.6;
    btn.disabled  = true; btn.style.opacity  = 0.6;
  } else {
    ctrl.disabled = false; ctrl.style.opacity = 1.0;
    btn.disabled  = false; btn.style.opacity  = 1.0;
  }
}


// ---------------- Tip Images State ----------------
let TIP_IMAGES = [];              // [{name, url}]
let TIP_SELECTED = new Set();     // Set of filenames

function updateTipCount() {
  const el = $("tip_count");
  if (el) el.textContent = String(TIP_SELECTED.size);
}

function tipCardHTML(item, checked) {
  return `
    <div class="tip-card">
      <label style="cursor:pointer;">
        <input type="checkbox"
               class="imageCheckBox"
               data-name="${item.name}"
               ${checked ? "checked" : ""}>

        <div class="cone-img-wrap" style="margin-top:6px;">
          <img src="${item.url}" alt="${item.name}" class="ConeImageStyle">
        </div>
       
      </label>
    </div>
  `;
}



function renderTipImages() {
  const wrap = document.getElementById("defectImageContainer");
  if (!wrap) return;

  wrap.innerHTML = TIP_IMAGES.map(item => `
    <div class="tip-card">
      <label style="cursor:pointer;">
        <input type="checkbox"
               class="imageCheckBox"
               data-name="${item.name}"
               ${TIP_SELECTED.has(item.name) ? "checked" : ""}>

        <div class="cone-img-wrap">
          <img src="${item.url}" class="ConeImageStyle">
        </div>

      </label>
    </div>
  `).join("");

  // checkbox change handler
  wrap.querySelectorAll(".imageCheckBox").forEach(cb => {
    cb.addEventListener("change", () => {
      const name = cb.dataset.name;
      if (cb.checked) {
        TIP_SELECTED.add(name);
      } else {
        TIP_SELECTED.delete(name);
      }
    updateTipCount();                 // ✅ FIX: update count immediately
    // await saveTipSelectedSilently();  // ✅ optional: keep backend synced
    });
  });
  updateTipCount()
}


async function deleteSelectedTipImages() {
  try {
    if (!bridge?.delete_good_images) {
      toast("Bridge missing delete_good_images()");
      return;
    }

    const list = Array.from(TIP_SELECTED);
    if (list.length === 0) {
      toast("No images selected");
      return;
    }

    const res = await bridge.delete_good_images(JSON.stringify(list));
    const data = JSON.parse(res || "{}");

    TIP_SELECTED.clear();

    // reload images from backend
    await loadTipImagesFromBackend();

    // clear saved selection
    if (bridge?.settings_save_key) {
      await bridge.settings_save_key("tip_images_selected", JSON.stringify([]));
    }

    toast(`Deleted ${data.deleted?.length || 0} image(s) ✓`);
  } catch (e) {
    console.error(e);
    toast("Delete failed");
  }
  updateTipCount()
}


// Optional helper: live-save without toast spam
async function saveTipSelectedSilently() {
  try {
    if (!bridge?.settings_save_key) return;
    await bridge.settings_save_key("tip_images_selected", JSON.stringify(Array.from(TIP_SELECTED)));
  } catch {}
}


function bindTipRefreshButton() {
  const refreshBtn = document.getElementById("tip_refresh");
  if (!refreshBtn) return;

  refreshBtn.onclick = async () => {
    try {
      if (!bridge || !bridge.get_tip_images) {
        toast("Bridge not ready");
        return;
      }

      toast("Refreshing images...");
      await loadTipImagesFromBackend();   // already exists in your code
      toast("Tip images refreshed ✓");

    } catch (e) {
      console.error("Tip refresh error:", e);
      toast("Failed to refresh images");
    }
  };
}




// ---------------- BOOT ----------------
async function boot() {
  // tabs
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const key = btn.dataset.sub; // quick | entry
      show("sub-quick", key === "quick");
      show("sub-entry", key === "entry");
      show("sub-shift", key === "shift");
      if (key === "shift") {
        refreshShiftList();  
      }
    });
  });

  // Fill dropdown ranges (you can change counts here)
  // if ($("cone_color")) $("cone_color").innerHTML = rangeOptions(20);      // example: 1..20 colors
  // if ($("cone_count")) $("cone_count").innerHTML = rangeOptions(50);      // 1..50
  if ($("tip_confidence")) $("tip_confidence").innerHTML = rangeOptions(15); // 1..100
  if ($("top_confidence")) $("top_confidence").innerHTML = rangeOptions(85);
  if ($("bottom_confidence")) $("bottom_confidence").innerHTML = rangeOptions(85);

  bindQuickButtons();

  await loadSavedGlobal();

  bindTipButtons();
  bindTipRefreshButton();

  // Load from backend: images + selected list
  await loadTipImagesFromBackend();
  await refreshShiftList();
  attachClickableTimePicker("shift_start", 5); // 5 min step (change to 15 if needed)
  attachClickableTimePicker("shift_end", 5);


  //modified by Gokul
  // ---- Shift Report Receivers button ----
const mailBtn = document.getElementById("btnShiftMailReceivers");
if (mailBtn) {
  mailBtn.onclick = () => {
    if (!bridge || typeof bridge.open_mail_popup !== "function") {
      toast("Bridge not ready");
      return;
    }
    bridge.open_mail_popup();
  };
}

}

// ---------------- Tip Buttons ----------------
function bindTipButtons() {
  const saveBtn = $("tip_save");
  const deleteBtn = $("tip_delete");
  const clearBtn = $("tip_clear");

  if (saveBtn) {
    saveBtn.onclick = async () => {
      try {
        if (!bridge?.settings_save_key) {
          toast("Bridge missing settings_save_key()");
          return;
        }
        const list = Array.from(TIP_SELECTED);
        await bridge.settings_save_key("tip_images_selected", JSON.stringify(list));
        toast("Saved ✓");
      } catch (e) {
        console.error(e);
        toast("Save failed");
      }
    };
  }

  if (deleteBtn) {
    deleteBtn.onclick = deleteSelectedTipImages;
  }

  if (clearBtn) {
    clearBtn.onclick = async () => {
      TIP_SELECTED.clear();
      renderTipImages();
      try {
        if (bridge?.settings_save_key) {
          await bridge.settings_save_key("tip_images_selected", JSON.stringify([]));
        }
      } catch {}
      toast("Cleared");
    };
  }

  updateTipCount();
}

// ---------------- Backend load ----------------
async function loadTipImagesFromBackend() {
  try {
    // 1) get images from folder
    const rawList = await bridge.get_tip_images();
    TIP_IMAGES = JSON.parse(rawList || "[]");

    // 2) get selected images from settings
    const rawAll = await bridge.settings_get_all();
    const data = JSON.parse(rawAll || "{}");
    const v = data.values || {};

    // IMPORTANT: must be array
    const selected = Array.isArray(v.tip_images_selected)
      ? v.tip_images_selected
      : [];

    TIP_SELECTED = new Set(selected);

    // 3) now render
    renderTipImages();

  } catch (e) {
    console.error("Tip load error:", e);
  }
  updateTipCount()
}


let _timePopupEl = null;

function buildTimeList(stepMinutes = 5) {
  const out = [];
  for (let h = 0; h < 24; h++) {
    for (let m = 0; m < 60; m += stepMinutes) {
      const hh = String(h).padStart(2, "0");
      const mm = String(m).padStart(2, "0");
      out.push(`${hh}:${mm}`);
    }
  }
  return out;
}

function closeTimePopup() {
  if (_timePopupEl) {
    _timePopupEl.remove();
    _timePopupEl = null;
  }
}

function openTimePopupForInput(inputEl, stepMinutes = 5) {
  closeTimePopup();

  const rect = inputEl.getBoundingClientRect();
  const popup = document.createElement("div");
  popup.className = "time-popup";

  // position below input
  const left = rect.left;
  const top  = rect.bottom + 6;

  popup.style.left = `${Math.max(8, left)}px`;
  popup.style.top  = `${Math.max(8, top)}px`;

  const times = buildTimeList(stepMinutes);
  popup.innerHTML = times.map(t => `<div class="trow" data-t="${t}">${t}</div>`).join("");

  popup.addEventListener("click", (e) => {
    const row = e.target.closest(".trow");
    if (!row) return;
    inputEl.value = row.dataset.t;       // ✅ set HH:MM
    inputEl.dispatchEvent(new Event("change", { bubbles: true }));
    closeTimePopup();
  });

  document.body.appendChild(popup);
  _timePopupEl = popup;

  // close on outside click
  setTimeout(() => {
    const onDoc = (ev) => {
      if (!_timePopupEl) return document.removeEventListener("mousedown", onDoc, true);
      if (ev.target === inputEl) return;
      if (_timePopupEl.contains(ev.target)) return;
      closeTimePopup();
      document.removeEventListener("mousedown", onDoc, true);
    };
    document.addEventListener("mousedown", onDoc, true);
  }, 0);
}

function attachClickableTimePicker(inputId, stepMinutes = 5) {
  const el = document.getElementById(inputId);
  if (!el) return;

  // Make it feel like picker (avoid typing)
  el.setAttribute("readonly", "readonly");
  el.addEventListener("keydown", (e) => e.preventDefault());

  el.addEventListener("click", () => openTimePopupForInput(el, stepMinutes));
  el.addEventListener("focus", () => openTimePopupForInput(el, stepMinutes));
}
