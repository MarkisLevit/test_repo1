/* Склад — Telegram Mini App. Чистий JS без збірки. */
(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const inTelegram = !!(tg && tg.initData);
  const $app = document.getElementById("app");

  const state = {
    me: null,
    locations: [],
    filter: { q: "", location_id: null, low: false },
  };

  // ------------------------------------------------------------ утиліти

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => (Number.isInteger(n) ? String(n) : String(+Number(n).toFixed(3)));
  const fmtDate = (iso) =>
    new Date(iso).toLocaleString("uk-UA", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
  const tgAtLeast = (v) => !!(inTelegram && tg.isVersionAtLeast && tg.isVersionAtLeast(v));
  const haptic = (kind = "light") => { try { tgAtLeast("6.1") && tg.HapticFeedback.impactOccurred(kind); } catch (_) {} };

  function toast(text) {
    const el = document.getElementById("toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toast.t);
    toast.t = setTimeout(() => (el.hidden = true), 2600);
  }

  function confirmDialog(text) {
    return new Promise((resolve) => {
      if (tgAtLeast("6.2")) tg.showConfirm(text, (ok) => resolve(!!ok));
      else resolve(window.confirm(text));
    });
  }

  function detailText(detail) {
    if (!detail) return "";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join("; ");
    return JSON.stringify(detail);
  }

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers);
    if (inTelegram) headers.Authorization = "tma " + tg.initData;
    const init = { method: opts.method || "GET", headers };
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.json);
    } else if (opts.body) {
      init.body = opts.body;
    }
    const res = await fetch("/api" + path, init);
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(detailText(data.detail) || "Помилка " + res.status);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ------------------------------------------------------------ кнопки Telegram

  let mainHandler = null;
  function setMain(text, handler) {
    clearMain();
    if (inTelegram) {
      mainHandler = async () => {
        tg.MainButton.showProgress();
        try { await handler(); } finally { tg.MainButton.hideProgress(); }
      };
      tg.MainButton.setParams({ text, is_visible: true, is_active: true });
      tg.MainButton.onClick(mainHandler);
    } else {
      const wrap = document.createElement("div");
      wrap.className = "sticky-main";
      wrap.innerHTML = `<button class="btn" type="button">${esc(text)}</button>`;
      const btn = wrap.firstElementChild;
      btn.onclick = async () => {
        btn.disabled = true;
        try { await handler(); } finally { btn.disabled = false; }
      };
      $app.appendChild(wrap);
    }
  }
  function clearMain() {
    if (inTelegram) {
      if (mainHandler) tg.MainButton.offClick(mainHandler);
      mainHandler = null;
      tg.MainButton.hide();
    }
  }

  function setBack(visible) {
    if (!inTelegram || !tgAtLeast("6.1")) return;
    visible ? tg.BackButton.show() : tg.BackButton.hide();
  }
  function goBack() {
    if (history.length > 1 && sessionStorage.getItem("navigated")) history.back();
    else location.replace("#/");
  }
  if (inTelegram && tgAtLeast("6.1")) tg.BackButton.onClick(goBack);

  // ------------------------------------------------------------ маршрутизація

  const routes = [
    [/^#?\/?$/, viewItems],
    [/^#\/locations$/, viewLocations],
    [/^#\/new$/, () => viewItemForm(null)],
    [/^#\/item\/(\d+)$/, (m) => viewItem(+m[1])],
    [/^#\/item\/(\d+)\/edit$/, (m) => viewItemForm(+m[1])],
    [/^#\/loc\/new$/, () => viewLocationForm(null)],
    [/^#\/loc\/(\d+)\/edit$/, (m) => viewLocationForm(+m[1])],
  ];

  async function render() {
    clearMain();
    closeSheet();
    const hash = location.hash || "#/";
    for (const [re, view] of routes) {
      const m = hash.match(re);
      if (m) {
        setBack(!/^#?\/?$|^#\/locations$/.test(hash));
        try {
          await view(m);
        } catch (e) {
          $app.innerHTML = `<div class="center"><p>⚠️ ${esc(e.message)}</p><a href="#/">На головну</a></div>`;
        }
        window.scrollTo(0, 0);
        return;
      }
    }
    location.replace("#/");
  }

  window.addEventListener("hashchange", () => {
    sessionStorage.setItem("navigated", "1");
    render();
  });

  const canEdit = () => state.me && state.me.can_edit;
  const locById = (id) => state.locations.find((l) => l.id === id);

  async function loadLocations() {
    state.locations = await api("/locations");
  }

  function locationOptions(selected, { excludeIds = new Set(), emptyLabel = "— не вказано —" } = {}) {
    let html = `<option value="">${esc(emptyLabel)}</option>`;
    for (const l of state.locations) {
      if (excludeIds.has(l.id)) continue;
      const pad = "  ".repeat(l.depth);
      html += `<option value="${l.id}" ${l.id === selected ? "selected" : ""}>${pad}${esc(l.name)}</option>`;
    }
    return html;
  }

  function tabs(active) {
    return `<nav class="tabs">
      <a href="#/" class="${active === "items" ? "active" : ""}"><span class="ic">📦</span>Речі</a>
      <a href="#/locations" class="${active === "locations" ? "active" : ""}"><span class="ic">🗄️</span>Місця</a>
    </nav>`;
  }

  // ------------------------------------------------------------ сканер QR

  function canScan() {
    return tgAtLeast("6.4");
  }
  function scan() {
    tg.showScanQrPopup({ text: "Наведіть камеру на QR-код речі або місця" }, (text) => {
      const m = String(text).match(/(item|loc)_(\d+)/);
      if (!m) {
        toast("Це не QR-код складу");
        return false;
      }
      haptic("medium");
      setTimeout(() => openStartParam(m[0]), 0);
      return true; // закрити сканер
    });
  }

  function openStartParam(param) {
    const m = String(param || "").match(/^(item|loc)_(\d+)$/);
    if (!m) return false;
    if (m[1] === "item") location.hash = `#/item/${m[2]}`;
    else {
      state.filter = { q: "", location_id: +m[2], low: false };
      if (location.hash === "#/" || location.hash === "") render();
      else location.hash = "#/";
    }
    return true;
  }

  // ------------------------------------------------------------ екран: список речей

  async function viewItems() {
    const f = state.filter;
    const [stats] = await Promise.all([api("/stats"), loadLocations()]);
    $app.innerHTML = `
      <div class="stats">
        <button class="stat" data-act="all"><b>${stats.items}</b><span>позицій</span></button>
        <button class="stat" data-act="locs"><b>${stats.locations}</b><span>місць</span></button>
        <button class="stat warn ${f.low ? "active" : ""}" data-act="low"><b>${stats.low}</b><span>закінчується</span></button>
      </div>
      <div class="search">
        <input id="q" type="search" placeholder="Пошук за назвою або артикулом" value="${esc(f.q)}" autocomplete="off" enterkeyhint="search">
        ${canScan() ? `<button class="icon-btn" id="scan" aria-label="Сканувати QR">⌗</button>` : ""}
      </div>
      <div class="filters">
        <select id="loc" aria-label="Місце">${locationOptions(f.location_id, { emptyLabel: "📍 Усі місця" })}</select>
      </div>
      <div class="list" id="list"><div class="center muted">Завантаження…</div></div>
      ${canEdit() ? `<button class="fab" id="add" aria-label="Додати річ">＋</button>` : ""}
      ${tabs("items")}`;

    const $q = document.getElementById("q");
    let t;
    $q.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(() => { f.q = $q.value.trim(); loadList(); }, 250);
    });
    $q.addEventListener("keydown", (e) => { if (e.key === "Enter") $q.blur(); });
    document.getElementById("loc").onchange = (e) => {
      f.location_id = e.target.value ? +e.target.value : null;
      loadList();
    };
    $app.querySelector('[data-act="locs"]').onclick = () => (location.hash = "#/locations");
    $app.querySelector('[data-act="low"]').onclick = () => { f.low = !f.low; render(); };
    $app.querySelector('[data-act="all"]').onclick = () => {
      state.filter = { q: "", location_id: null, low: false };
      render();
    };
    const $scan = document.getElementById("scan");
    if ($scan) $scan.onclick = scan;
    const $add = document.getElementById("add");
    if ($add) $add.onclick = () => (location.hash = "#/new");
    await loadList();
  }

  async function loadList() {
    const f = state.filter;
    const params = new URLSearchParams();
    if (f.q) params.set("q", f.q);
    if (f.location_id) params.set("location_id", f.location_id);
    if (f.low) params.set("low", "1");
    const items = await api("/items?" + params);
    const $list = document.getElementById("list");
    if (!$list) return;
    if (!items.length) {
      const filtered = f.q || f.location_id || f.low;
      $list.innerHTML = `<div class="center muted">${
        filtered ? "Нічого не знайдено" : canEdit() ? "Склад порожній. Натисніть ＋, щоб додати першу річ." : "Склад порожній."
      }</div>`;
      return;
    }
    $list.innerHTML = items.map(itemCard).join("");
    $list.querySelectorAll(".card").forEach((el) => (el.onclick = () => (location.hash = `#/item/${el.dataset.id}`)));
  }

  function itemCard(it) {
    const cls = it.quantity === 0 ? "zero" : it.is_low ? "low" : "";
    const thumb = it.thumb_url
      ? `<img class="thumb" src="${esc(it.thumb_url)}" alt="" loading="lazy">`
      : `<div class="thumb">📦</div>`;
    const sub = [it.location_path ? "📍 " + it.location_path : "📍 місце не вказано", it.sku].filter(Boolean).join(" · ");
    return `<div class="card" data-id="${it.id}" role="button" tabindex="0">
      ${thumb}
      <div class="grow"><div class="name">${esc(it.name)}</div><div class="sub">${esc(sub)}</div></div>
      <div class="qty ${cls}">${fmt(it.quantity)} ${esc(it.unit)}</div>
    </div>`;
  }

  // ------------------------------------------------------------ екран: картка речі

  async function viewItem(id) {
    const [it] = await Promise.all([api(`/items/${id}`), state.locations.length ? null : loadLocations()]);
    const photo = it.photo_url
      ? `<img class="hero" src="${esc(it.photo_url)}" alt="${esc(it.name)}" id="photo">`
      : `<div class="hero">📦</div>`;
    const loc = it.location_path
      ? `<a href="#/" data-loc="${it.location_id}">${esc(it.location_path)}</a>`
      : `<span class="muted">не вказано</span>`;
    const history = it.history
      .map((h) => {
        const d = h.delta > 0 ? `<span class="plus">+${fmt(h.delta)}</span>` : h.delta < 0 ? `<span class="minus">${fmt(h.delta)}</span>` : "";
        return `<div class="line">
          <div class="top"><span>${d} ${esc(h.note)}</span><span class="muted">→ ${fmt(h.quantity_after)}</span></div>
          <div class="muted" style="font-size:13px">${fmtDate(h.created_at)}${h.user_name ? " · " + esc(h.user_name) : ""}</div>
        </div>`;
      })
      .join("");

    $app.innerHTML = `
      ${photo}
      <h1 style="margin-top:12px">${esc(it.name)}</h1>
      ${it.sku ? `<div class="muted">Артикул: ${esc(it.sku)}</div>` : ""}

      <div class="stepper">
        ${canEdit() ? `<button class="icon-btn" data-delta="-1" aria-label="Мінус один">−</button>` : "<span></span>"}
        <div><div class="big">${fmt(it.quantity)} <small>${esc(it.unit)}</small></div>
          ${it.is_low ? `<div class="badge-low">⚠️ закінчується (мінімум ${fmt(it.min_quantity)})</div>` : ""}</div>
        ${canEdit() ? `<button class="icon-btn" data-delta="1" aria-label="Плюс один">+</button>` : "<span></span>"}
      </div>
      ${canEdit() ? `<div class="actions">
          <button class="btn secondary" data-adjust="in">📥 Прийняти</button>
          <button class="btn secondary" data-adjust="out">📤 Видати</button>
        </div>` : ""}

      <h2>Інформація</h2>
      <div class="section">
        <div class="line"><span class="k">Місце</span><span class="v">${loc}</span></div>
        <div class="line"><span class="k">Мінімальний залишок</span><span class="v">${it.min_quantity ? fmt(it.min_quantity) + " " + esc(it.unit) : "—"}</span></div>
        ${it.description ? `<div class="line"><span class="v" style="text-align:left;white-space:pre-wrap">${esc(it.description)}</span></div>` : ""}
      </div>

      <h2>QR-код для етикетки</h2>
      <details class="section"><summary>Показати QR-код</summary>
        <div class="inner"><img class="qr" src="/api/qr/item/${it.id}.png" alt="QR-код">
        <p class="muted" style="font-size:13px">Роздрукуйте й наклейте на річ або коробку. Скануйте кнопкою ⌗ у застосунку або камерою телефона.</p></div>
      </details>

      <h2>Історія</h2>
      <div class="section history">${history || `<div class="line muted">Порожньо</div>`}</div>

      ${canEdit() ? `<div class="actions">
          <button class="btn secondary" id="edit">✏️ Редагувати</button>
          <button class="btn danger" id="del">🗑 Видалити</button>
        </div>` : ""}`;

    const $photo = document.getElementById("photo");
    if ($photo) $photo.onclick = () => lightbox(it.photo_url);
    const $loc = $app.querySelector("[data-loc]");
    if ($loc) $loc.onclick = (e) => {
      e.preventDefault();
      state.filter = { q: "", location_id: it.location_id, low: false };
      location.hash = "#/";
    };
    if (!canEdit()) return;

    $app.querySelectorAll("[data-delta]").forEach((b) => (b.onclick = async () => {
      const delta = +b.dataset.delta;
      try {
        await api(`/items/${id}/adjust`, { method: "POST", json: { delta } });
        haptic();
        render();
      } catch (e) { toast(e.message); }
    }));
    $app.querySelectorAll("[data-adjust]").forEach((b) => (b.onclick = () => adjustSheet(it, b.dataset.adjust)));
    document.getElementById("edit").onclick = () => (location.hash = `#/item/${id}/edit`);
    document.getElementById("del").onclick = async () => {
      if (!(await confirmDialog(`Видалити «${it.name}»? Історію теж буде видалено.`))) return;
      await api(`/items/${id}`, { method: "DELETE" });
      toast("Видалено");
      location.replace("#/");
    };
  }

  function lightbox(src) {
    const el = document.createElement("div");
    el.className = "lightbox";
    el.innerHTML = `<img src="${esc(src)}" alt="">`;
    el.onclick = () => el.remove();
    document.body.appendChild(el);
  }

  // ------------------------------------------------------------ нижня панель (sheet)

  function openSheet(html) {
    const root = document.getElementById("sheet-root");
    root.innerHTML = `<div class="overlay"><div class="sheet" role="dialog" aria-modal="true">${html}</div></div>`;
    root.querySelector(".overlay").addEventListener("click", (e) => { if (e.target.classList.contains("overlay")) closeSheet(); });
    return root.querySelector(".sheet");
  }
  function closeSheet() {
    document.getElementById("sheet-root").innerHTML = "";
  }

  function adjustSheet(it, mode) {
    const sheet = openSheet(`
      <h3>${esc(it.name)}</h3>
      <div class="seg"><button data-m="in">📥 Прийняти</button><button data-m="out">📤 Видати</button></div>
      <div class="form">
        <label class="field"><span>Кількість, ${esc(it.unit)}</span>
          <input id="amt" type="number" inputmode="decimal" min="0" step="any" value="1"></label>
        <label class="field"><span>Коментар (кому видано, звідки надійшло…)</span>
          <input id="note" type="text" maxlength="200"></label>
        <button class="btn block" id="ok">Зберегти</button>
      </div>`);
    const setMode = (m) => {
      mode = m;
      sheet.querySelectorAll(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.m === m));
    };
    setMode(mode);
    sheet.querySelectorAll(".seg button").forEach((b) => (b.onclick = () => setMode(b.dataset.m)));
    const $amt = sheet.querySelector("#amt");
    $amt.focus();
    $amt.select();
    sheet.querySelector("#ok").onclick = async () => {
      const amount = parseFloat(String($amt.value).replace(",", "."));
      if (!(amount > 0)) return toast("Вкажіть кількість більше нуля");
      try {
        await api(`/items/${it.id}/adjust`, {
          method: "POST",
          json: { delta: mode === "in" ? amount : -amount, note: sheet.querySelector("#note").value },
        });
        haptic("medium");
        closeSheet();
        render();
      } catch (e) { toast(e.message); }
    };
  }

  // ------------------------------------------------------------ екран: форма речі

  async function viewItemForm(id) {
    if (!canEdit()) throw new Error("Немає прав на редагування");
    await loadLocations();
    const it = id
      ? await api(`/items/${id}`)
      : { name: "", description: "", sku: "", quantity: 1, unit: "шт", min_quantity: 0, location_id: state.filter.location_id, photo_url: null };

    $app.innerHTML = `
      <h1>${id ? "Редагування" : "Нова річ"}</h1>
      <form class="form" id="f" style="margin-top:12px" novalidate>
        <label class="photo-pick">
          <span id="preview" style="display:contents">${
            it.photo_url ? `<img src="${esc(it.photo_url)}" alt="">` : `<span style="font-size:32px">📷</span><span>Додати фото</span>`
          }</span>
          <input type="file" accept="image/*" id="file">
        </label>
        ${it.photo_url ? `<button type="button" class="btn danger block" id="rmphoto" style="margin:-4px 0 12px">Видалити фото</button>` : ""}
        <label class="field"><span>Назва *</span><input name="name" required maxlength="300" value="${esc(it.name)}"></label>
        <label class="field"><span>Місце зберігання</span><select name="location_id">${locationOptions(it.location_id)}</select></label>
        <div class="two">
          <label class="field"><span>Кількість</span><input name="quantity" type="number" inputmode="decimal" min="0" step="any" value="${fmt(it.quantity)}"></label>
          <label class="field"><span>Одиниця</span><input name="unit" maxlength="20" list="units" value="${esc(it.unit)}"></label>
        </div>
        <datalist id="units"><option value="шт"><option value="кг"><option value="м"><option value="л"><option value="уп"><option value="компл"></datalist>
        <div class="two">
          <label class="field"><span>Артикул / інв. №</span><input name="sku" maxlength="100" value="${esc(it.sku)}"></label>
          <label class="field"><span>Мін. залишок</span><input name="min_quantity" type="number" inputmode="decimal" min="0" step="any" value="${fmt(it.min_quantity)}"></label>
        </div>
        <label class="field"><span>Опис</span><textarea name="description" maxlength="5000">${esc(it.description)}</textarea></label>
        ${state.locations.length ? "" : `<p class="muted" style="font-size:13px">Порада: спершу створіть місця зберігання на вкладці «Місця».</p>`}
      </form>`;

    let file = null;
    const $file = document.getElementById("file");
    $file.onchange = () => {
      file = $file.files[0] || null;
      if (file) document.getElementById("preview").innerHTML = `<img src="${URL.createObjectURL(file)}" alt="">`;
    };
    const $rm = document.getElementById("rmphoto");
    if ($rm) $rm.onclick = async () => {
      await api(`/items/${id}/photo`, { method: "DELETE" });
      toast("Фото видалено");
      render();
    };

    const form = document.getElementById("f");
    form.addEventListener("submit", (e) => e.preventDefault());
    setMain(id ? "Зберегти" : "Додати річ", async () => {
      const fd = new FormData(form);
      const body = {
        name: String(fd.get("name") || "").trim(),
        description: String(fd.get("description") || ""),
        sku: String(fd.get("sku") || "").trim(),
        quantity: parseFloat(String(fd.get("quantity") || "0").replace(",", ".")) || 0,
        unit: String(fd.get("unit") || "шт").trim() || "шт",
        min_quantity: parseFloat(String(fd.get("min_quantity") || "0").replace(",", ".")) || 0,
        location_id: fd.get("location_id") ? +fd.get("location_id") : null,
      };
      if (!body.name) {
        toast("Вкажіть назву");
        form.querySelector('[name="name"]').focus();
        return;
      }
      try {
        const saved = id
          ? await api(`/items/${id}`, { method: "PATCH", json: body })
          : await api("/items", { method: "POST", json: body });
        if (file) {
          const up = new FormData();
          up.append("file", file);
          await api(`/items/${saved.id}/photo`, { method: "POST", body: up });
        }
        haptic("medium");
        toast(id ? "Збережено" : "Додано");
        location.replace(`#/item/${saved.id}`);
      } catch (e) { toast(e.message); }
    });
  }

  // ------------------------------------------------------------ екран: місця

  async function viewLocations() {
    await loadLocations();
    const rows = state.locations
      .map((l) => `<div class="loc-row" data-id="${l.id}">
          <span style="padding-left:${l.depth * 18}px">${l.depth ? "↳" : "🗄️"}</span>
          <span class="grow"><b>${esc(l.name)}</b>${l.description ? `<div class="muted" style="font-size:13px">${esc(l.description)}</div>` : ""}</span>
          <span class="count">${l.total_count}</span>
          ${canEdit() ? `<button class="icon-btn" data-edit="${l.id}" aria-label="Редагувати" style="width:36px;height:36px;font-size:16px">✏️</button>` : ""}
        </div>`)
      .join("");
    $app.innerHTML = `
      <h1>Місця зберігання</h1>
      <p class="muted" style="margin:0 0 12px">Склад › стелаж › полиця › коробка. Натисніть, щоб побачити речі.</p>
      <div class="section">${rows || `<div class="center muted">Ще немає місць${canEdit() ? ". Натисніть ＋" : ""}</div>`}</div>
      ${canEdit() ? `<button class="fab" id="add" aria-label="Додати місце">＋</button>` : ""}
      ${tabs("locations")}`;
    $app.querySelectorAll(".loc-row").forEach((r) => (r.onclick = (e) => {
      if (e.target.closest("[data-edit]")) return;
      state.filter = { q: "", location_id: +r.dataset.id, low: false };
      location.hash = "#/";
    }));
    $app.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => (location.hash = `#/loc/${b.dataset.edit}/edit`)));
    const $add = document.getElementById("add");
    if ($add) $add.onclick = () => (location.hash = "#/loc/new");
  }

  function descendants(id) {
    const out = new Set([id]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const l of state.locations) if (l.parent_id && out.has(l.parent_id) && !out.has(l.id)) { out.add(l.id); grew = true; }
    }
    return out;
  }

  async function viewLocationForm(id) {
    if (!canEdit()) throw new Error("Немає прав на редагування");
    await loadLocations();
    const loc = id ? locById(id) : { name: "", description: "", parent_id: null };
    if (!loc) throw new Error("Місце не знайдено");
    $app.innerHTML = `
      <h1>${id ? "Редагування місця" : "Нове місце"}</h1>
      <form class="form" id="f" style="margin-top:12px" novalidate>
        <label class="field"><span>Назва *</span><input name="name" maxlength="200" value="${esc(loc.name)}" placeholder="Напр.: Стелаж А, полиця 2"></label>
        <label class="field"><span>Знаходиться в</span>
          <select name="parent_id">${locationOptions(loc.parent_id, { excludeIds: id ? descendants(id) : new Set(), emptyLabel: "— верхній рівень —" })}</select></label>
        <label class="field"><span>Опис</span><textarea name="description" maxlength="2000">${esc(loc.description)}</textarea></label>
      </form>
      ${id ? `
        <h2>QR-код місця</h2>
        <div class="section" style="padding:14px;text-align:center"><img class="qr" src="/api/qr/loc/${id}.png" alt="QR-код">
          <p class="muted" style="font-size:13px">Наклейте на полицю — скан покаже все, що на ній лежить.</p></div>
        <div class="actions" style="grid-template-columns:1fr"><button class="btn danger" id="del">🗑 Видалити місце</button></div>` : ""}`;

    const form = document.getElementById("f");
    form.addEventListener("submit", (e) => e.preventDefault());
    setMain(id ? "Зберегти" : "Створити", async () => {
      const fd = new FormData(form);
      const body = {
        name: String(fd.get("name") || "").trim(),
        description: String(fd.get("description") || ""),
        parent_id: fd.get("parent_id") ? +fd.get("parent_id") : null,
      };
      if (!body.name) return toast("Вкажіть назву");
      try {
        await api(id ? `/locations/${id}` : "/locations", { method: id ? "PUT" : "POST", json: body });
        toast(id ? "Збережено" : "Створено");
        location.replace("#/locations");
      } catch (e) { toast(e.message); }
    });
    const $del = document.getElementById("del");
    if ($del) $del.onclick = async () => {
      if (!(await confirmDialog(`Видалити «${loc.name}»? Речі та вкладені місця перейдуть на рівень вище.`))) return;
      await api(`/locations/${id}`, { method: "DELETE" });
      toast("Видалено");
      location.replace("#/locations");
    };
  }

  // ------------------------------------------------------------ старт

  async function boot() {
    if (tg) {
      tg.ready();
      try { tg.expand(); } catch (_) {}
    }
    try {
      state.me = await api("/me");
    } catch (e) {
      $app.innerHTML = `<div class="center"><div style="font-size:48px">🔒</div><p>${esc(e.message)}</p>
        ${inTelegram ? "" : `<p class="muted">Відкрийте застосунок через бота в Telegram.</p>`}</div>`;
      return;
    }
    // Глибоке посилання: t.me/<бот>?startapp=item_5 або кнопка бота з ?startapp=...
    const params = new URLSearchParams(location.search);
    const start = (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) || params.get("startapp") || "";
    if (params.get("low") === "1") state.filter.low = true;
    const m = start.match(/^(item|loc)_(\d+)$/);
    const base = location.pathname + location.search;
    if (m && m[1] === "item") history.replaceState(null, "", `${base}#/item/${m[2]}`);
    if (m && m[1] === "loc") {
      state.filter.location_id = +m[2];
      history.replaceState(null, "", `${base}#/`);
    }
    render();
  }

  boot();
})();
