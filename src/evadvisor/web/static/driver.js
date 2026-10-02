// Страница водителя: выбор автомобиля и места, запрос рекомендаций, список и карта.
(() => {
  const $ = (id) => document.getElementById(id);
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem("evadvisor." + k)); } catch { return null; } },
    set(k, v) { try { localStorage.setItem("evadvisor." + k, JSON.stringify(v)); } catch { /* приватный режим */ } },
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (x, d = 0) => Number(x).toLocaleString("ru-RU", { maximumFractionDigits: d, minimumFractionDigits: d });
  const PLUGS = { TYPE2_SOCKET: "Type 2 (свой кабель)", TYPE2_CABLE: "Type 2", TYPE1_CABLE: "Type 1", CCS2: "CCS",
                  CCS1: "CCS1", CHADEMO: "CHAdeMO", TESLA: "Tesla Supercharger" };
  const STATUS_RU = { Available: "свободна", Occupied: "занята", OutOfService: "не работает", Unknown: "нет данных",
                      Reserved: "забронирована", EvseNotFound: "нет данных" };
  const POPULAR = ["Tesla Model 3", "Tesla Model Y", "Skoda Enyaq", "Volkswagen ID.3", "Renault Zoe"];
  // Вероятность: цвет + словесная оценка (цвет никогда не единственный носитель смысла)
  const prob = (p) => p >= 0.7 ? { c: "#0ca30c", w: "скорее свободна" } :
                      p >= 0.4 ? { c: "#fab219", w: "может быть занята" } : { c: "#d03b3b", w: "скорее занята" };

  // ---------------------------------------------------------------- карта
  const map = L.map("map", { zoomControl: true }).setView([46.8, 8.2], 8);
  // Подложка — OpenStreetMap (без ключа). В тёмной теме тайлы затемняются CSS-фильтром (класс map-dark),
  // маркеры и маршрут лежат в других слоях и не меняют цвет.
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  function setTiles() { $("map").classList.toggle("map-dark", window.isDarkTheme()); }
  setTiles();
  document.addEventListener("themechange", setTiles);

  let me = null, markers = [], routeLine = null, items = [];
  const meIcon = L.divIcon({ className: "", html: '<div class="me-pin"></div>', iconSize: [18, 18], iconAnchor: [9, 9] });

  function setPosition(lat, lon, label, fly = true) {
    const pos = { lat, lon, label: label || `Точка на карте (${lat.toFixed(4)}, ${lon.toFixed(4)})` };
    store.set("pos", pos);
    $("address").value = pos.label;
    if (me) me.setLatLng([lat, lon]);
    else me = L.marker([lat, lon], { icon: meIcon, title: "Вы здесь", zIndexOffset: 1000 }).addTo(map);
    $("map-hint").hidden = true;
    if (fly) map.flyTo([lat, lon], Math.max(map.getZoom(), 13), { duration: 0.6 });
  }
  map.on("click", (e) => setPosition(e.latlng.lat, e.latlng.lng));

  // ---------------------------------------------------------------- комбобокс
  function combo(input, list, { search, render, pick, minLength = 1, delay = 0 }) {
    let found = [], active = -1, timer = null;
    const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
    const draw = () => {
      list.innerHTML = found.map((it, i) => `<li role="option" data-i="${i}" aria-selected="${i === active}">${render(it)}</li>`).join("");
      list.hidden = found.length === 0;
      input.setAttribute("aria-expanded", String(!list.hidden));
    };
    const choose = (i) => { if (found[i]) { pick(found[i]); close(); } };
    input.addEventListener("input", () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < minLength) { found = []; close(); return; }
      timer = setTimeout(async () => { found = await search(q); active = found.length ? 0 : -1; draw(); }, delay);
    });
    input.addEventListener("keydown", (e) => {
      if (list.hidden) return;
      if (e.key === "ArrowDown") { active = Math.min(found.length - 1, active + 1); draw(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); draw(); e.preventDefault(); }
      else if (e.key === "Enter") { choose(active); e.preventDefault(); }
      else if (e.key === "Escape") close();
    });
    list.addEventListener("mousedown", (e) => { const li = e.target.closest("li"); if (li) { e.preventDefault(); choose(+li.dataset.i); } });
    input.addEventListener("blur", () => setTimeout(close, 150));
  }

  // ---------------------------------------------------------------- автомобиль
  let vehicles = [], vehicle = store.get("vehicle");
  const matchVehicles = (q) => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    return vehicles.filter((v) => words.every((w) => v.label.toLowerCase().includes(w))).slice(0, 15);
  };
  function showVehicle(v) {
    const plugs = [...new Set(v.plugs.map((p) => PLUGS[p] || p))];
    $("vehicle-info").innerHTML = plugs.map((p) => `<span class="tag">🔌 ${esc(p)}</span>`).join("") +
      `<span class="tag">AC до ${fmt(v.ac)} кВт</span>` +
      (v.dc ? `<span class="tag">DC до ${fmt(v.dc)} кВт</span>` : `<span class="tag">без быстрой зарядки</span>`);
  }
  function pickVehicle(v) { vehicle = v; store.set("vehicle", v); $("vehicle").value = v.label; showVehicle(v); $("popular").hidden = true; }
  fetch("/api/vehicles").then((r) => r.json()).then((list) => {
    vehicles = list;
    if (vehicle) { $("vehicle").value = vehicle.label; showVehicle(vehicle); $("popular").hidden = true; }
    $("popular").innerHTML = POPULAR.filter((p) => matchVehicles(p).length)
      .map((p) => `<button type="button" class="chip" data-q="${esc(p)}">${esc(p)}</button>`).join("");
  });
  $("popular").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    $("vehicle").value = b.dataset.q; $("vehicle").focus(); $("vehicle").dispatchEvent(new Event("input"));
  });
  combo($("vehicle"), $("vehicle-list"), {
    search: async (q) => matchVehicles(q),
    render: (v) => `<span>${esc(v.label)}</span><span class="sub">AC ${fmt(v.ac)}${v.dc ? " · DC " + fmt(v.dc) : ""} кВт</span>`,
    pick: pickVehicle,
  });
  $("vehicle").addEventListener("input", () => { if (vehicle && $("vehicle").value !== vehicle.label) { vehicle = null; $("vehicle-info").innerHTML = ""; } });

  // ---------------------------------------------------------------- место
  combo($("address"), $("address-list"), {
    minLength: 3, delay: 250,
    search: async (q) => { const r = await fetch("/api/geocode?q=" + encodeURIComponent(q)); return r.ok ? r.json() : []; },
    render: (it) => `<span>${esc(it.label)}</span>`,
    pick: (it) => setPosition(it.lat, it.lon, it.label),
  });
  $("locate").addEventListener("click", () => {
    if (!navigator.geolocation) { showError("Браузер не умеет определять местоположение — введите адрес или нажмите на карту."); return; }
    $("locate").setAttribute("aria-busy", "true");
    navigator.geolocation.getCurrentPosition(
      (p) => { $("locate").removeAttribute("aria-busy"); setPosition(p.coords.latitude, p.coords.longitude, "Моё местоположение"); },
      () => { $("locate").removeAttribute("aria-busy"); showError("Не удалось определить местоположение — введите адрес или нажмите на карту."); },
      { enableHighAccuracy: true, timeout: 10000 });
  });
  const saved = store.get("pos");
  if (saved) setPosition(saved.lat, saved.lon, saved.label, false), map.setView([saved.lat, saved.lon], 12);

  // ---------------------------------------------------------------- фильтры
  const filterIds = ["dc_only", "open_24h", "public_only"];
  const updateFilterCount = () => {
    const n = filterIds.filter((id) => $(id).checked).length + ($("has_cable").checked ? 0 : 1);
    $("filters-count").textContent = n ? `· выбрано ${n}` : "";
  };
  [...filterIds, "has_cable"].forEach((id) => $(id).addEventListener("change", updateFilterCount));

  // ---------------------------------------------------------------- поиск
  function showError(text) { $("form-error").innerHTML = text ? `<div class="notice">${esc(text)}</div>` : ""; }
  $("search").addEventListener("submit", async (e) => {
    e.preventDefault();
    showError("");
    const pos = store.get("pos");
    if (!vehicle) { showError("Выберите автомобиль из списка — начните вводить марку."); $("vehicle").focus(); return; }
    if (!pos) { showError("Укажите, где вы: введите адрес, нажмите 📍 или кликните на карту."); $("address").focus(); return; }
    $("go").setAttribute("aria-busy", "true");
    $("go").textContent = "Ищем станции…";
    $("results").innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
    if (matchMedia("(max-width: 960px)").matches) $("results").scrollIntoView({ behavior: "smooth", block: "start" });
    try {
      const r = await fetch("/api/recommend", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vehicle_id: vehicle.id, lat: pos.lat, lon: pos.lon,
          energy_kwh: +document.querySelector("input[name=energy]:checked").value,
          dc_only: $("dc_only").checked, open_24h: $("open_24h").checked,
          public_only: $("public_only").checked, has_cable: $("has_cable").checked,
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "ошибка сервера");
      render(data, pos);
    } catch (err) {
      $("results").innerHTML = `<div class="notice">Не удалось подобрать станции: ${esc(err.message)}. Попробуйте ещё раз.</div>`;
    } finally {
      $("go").removeAttribute("aria-busy");
      $("go").textContent = "Найти станции";
    }
  });

  function render(data, pos) {
    markers.forEach((m) => m.remove()); markers = [];
    if (routeLine) { routeLine.remove(); routeLine = null; }
    items = data.items;
    const meta = [];
    if (data.status_age_min != null) meta.push(`статусы ${Math.round(data.status_age_min)} мин назад`);
    if (data.temperature_c != null) meta.push(`${fmt(data.temperature_c)} °C`);
    const warnings = data.warnings.map((w) => `<div class="notice">${esc(w)}</div>`).join("");
    if (!items.length) {
      $("results").innerHTML = warnings + `<div class="empty-state"><div class="big">🔍</div>Подходящих станций не нашлось.
        Попробуйте убрать фильтры или выбрать другое место.</div>`;
      return;
    }
    $("results").innerHTML = `
      <div class="results-head"><h2>Лучшие станции рядом</h2><span class="small muted">${meta.join(" · ")}</span></div>
      ${warnings}
      <div class="legend" style="margin:8px 0 10px">
        <span><i class="dot" style="background:#0ca30c"></i>скорее свободна</span>
        <span><i class="dot" style="background:#fab219"></i>может быть занята</span>
        <span><i class="dot" style="background:#d03b3b"></i>скорее занята</span>
      </div>
      ${items.map(card).join("")}`;

    const bounds = [[pos.lat, pos.lon]];
    items.forEach((it, i) => {
      const pr = prob(it.p_station);
      const icon = L.divIcon({ className: "", iconSize: [30, 30], iconAnchor: [15, 30],
        html: `<div class="marker-pin" style="background:${pr.c}"><span>${it.rank}</span></div>` });
      const m = L.marker([it.lat, it.lon], { icon, title: it.name, riseOnHover: true }).addTo(map)
        .bindTooltip(`<b>${it.rank}. ${esc(it.name)}</b><br>≈ ${Math.round(it.total_min)} мин · ${pr.w} (${Math.round(it.p_station * 100)} %)`);
      m.on("click", () => select(i, true));
      markers.push(m); bounds.push([it.lat, it.lon]);
    });
    map.flyToBounds(bounds, { padding: [50, 50], duration: 0.6, maxZoom: 15 });
    document.querySelectorAll(".result").forEach((el) => {
      const i = +el.dataset.i;
      el.addEventListener("click", (e) => { if (!e.target.closest("a")) select(i, false); });
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") select(i, false); });
      el.addEventListener("mouseenter", () => highlight(i, true));
      el.addEventListener("mouseleave", () => highlight(i, false));
    });
  }

  function card(it, i) {
    const pr = prob(it.p_station);
    const plugs = [...new Set(it.plugs.map((p) => PLUGS[p] || p))].join(", ");
    const power = it.effective_kw ? `${fmt(it.effective_kw)} кВт` : "мощность не указана";
    return `
      <article class="result" data-i="${i}" tabindex="0" aria-label="${it.rank}. ${esc(it.name)}">
        <div class="result-top">
          <span class="rank" style="background:${pr.c}">${it.rank}</span>
          <div><h3>${esc(it.name)}</h3><div class="addr">${esc([it.street, [it.postal_code, it.city].filter(Boolean).join(" ")].filter(Boolean).join(", "))} · ${esc(it.operator)}</div></div>
          <div class="total"><div class="t">≈ ${Math.round(it.total_min)} мин</div><div class="c">дорога + зарядка</div></div>
        </div>
        <div class="facts">
          <span class="tag">🔌 ${esc(plugs)}</span>
          <span class="tag">⚡ ${it.power_class === "DC" ? "быстрая" : "обычная"}, ${power}</span>
          <span class="tag">🚗 ${fmt(it.distance_km, 1)} км · ${Math.round(it.eta_min)} мин</span>
          ${it.is_open_24h ? '<span class="tag">24/7</span>' : ""}
        </div>
        <div class="prob">
          <span>К приезду ${pr.w}</span><strong>${Math.round(it.p_station * 100)} %</strong>
          <span class="bar"><span style="width:${(it.p_station * 100).toFixed(0)}%;background:${pr.c}"></span></span>
        </div>
        <div class="details" hidden></div>
      </article>`;
  }

  function highlight(i, on) {
    const el = markers[i] && markers[i].getElement();
    if (el) el.querySelector(".marker-pin")?.classList.toggle("hl", on);
  }

  async function select(i, scroll) {
    const it = items[i], pos = store.get("pos");
    document.querySelectorAll(".result").forEach((el) => el.classList.toggle("active", +el.dataset.i === i));
    const card = document.querySelector(`.result[data-i="${i}"]`);
    if (scroll) card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    markers[i].openTooltip();
    fetch(`/api/route?lat=${pos.lat}&lon=${pos.lon}&lat2=${it.lat}&lon2=${it.lon}`).then((x) => x.json()).then((r) => {
      if (routeLine) routeLine.remove();
      routeLine = L.geoJSON(r.geometry, { style: { color: getComputedStyle(document.documentElement).getPropertyValue("--accent"),
        weight: 5, opacity: .85, dashArray: r.straight ? "8 8" : null } }).addTo(map);
      map.flyToBounds(routeLine.getBounds(), { padding: [70, 70], duration: 0.5, maxZoom: 15 });
    });

    const det = card.querySelector(".details");
    if (!det.hidden) { det.hidden = true; return; }
    det.hidden = false;
    det.innerHTML = '<div class="small muted">Загружаем подробности…</div>';
    const prof = await fetch(`/api/station/${it.station_id}/profile`).then((x) => x.json());
    const nowH = new Date().getHours();
    const hours = Array.from({ length: 24 }, (_, h) => prof.hours.find((x) => x.hour_local === h));
    const bars = hours.map((h, k) => `<i class="${k === nowH ? "now" : ""}" title="${k}:00 — обычно свободно ${h ? Math.round(h.p_free * 100) : "?"} %"
        style="height:${h ? Math.max(6, h.p_free * 100) : 4}%"></i>`).join("");
    det.innerHTML = `
      <p class="why">${esc(it.explanation)}</p>
      <div class="table-wrap"><table class="points"><thead><tr><th>Точка</th><th>Ток</th><th class="num">кВт</th><th>Сейчас</th><th class="num">К приезду</th></tr></thead><tbody>
      ${it.points.map((p) => `<tr><td class="mono">${esc(p.evse_id)}</td><td>${p.power_class}</td><td class="num">${p.power_kw ? fmt(p.power_kw) : "?"}</td>
        <td>${STATUS_RU[p.status_now] || p.status_now}</td><td class="num">${Math.round(p.p_free * 100)} %</td></tr>`).join("")}
      </tbody></table></div>
      <div class="small muted" style="margin-top:8px">Обычно свободно по часам сегодня (${prof.source === "station" ? "эта станция" : "в среднем по кантону"}), оранжевым — текущий час:</div>
      <div class="spark">${bars}</div><div class="spark-axis"><span>0:00</span><span>6:00</span><span>12:00</span><span>18:00</span><span>23:00</span></div>
      <p style="margin:10px 0 0"><a href="https://www.google.com/maps/dir/?api=1&destination=${it.lat},${it.lon}" target="_blank" rel="noopener">Построить маршрут в Google Картах ↗</a></p>`;
  }
})();
