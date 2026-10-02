// Страница водителя: выбор автомобиля и места, запрос рекомендаций, список и карта.
(() => {
  const $ = (id) => document.getElementById(id);
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem("evadvisor." + k)); } catch { return null; } },
    set(k, v) { try { localStorage.setItem("evadvisor." + k, JSON.stringify(v)); } catch { /* приватный режим */ } },
  };
  const PLUGS = { TYPE2_SOCKET: "Type 2 (свой кабель)", TYPE2_CABLE: "Type 2", TYPE1_CABLE: "Type 1", CCS2: "CCS",
                  CCS1: "CCS1", CHADEMO: "CHAdeMO", TESLA: "Tesla" };
  const STATUS_RU = { Available: "свободна", Occupied: "занята", OutOfService: "неисправна", Unknown: "нет данных",
                      Reserved: "забронирована", EvseNotFound: "нет данных" };
  const probColor = (p) => (p >= 0.7 ? "#0ca30c" : p >= 0.4 ? "#fab219" : "#d03b3b");
  const probWord = (p) => (p >= 0.7 ? "высокая" : p >= 0.4 ? "средняя" : "низкая");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---------- карта
  const map = L.map("map").setView([46.8, 8.2], 8);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  let me = null, markers = [], routeLine = null, lastItems = [];
  const meIcon = L.divIcon({ className: "", html: '<div class="me-pin"></div>', iconSize: [16, 16] });

  function setPosition(lat, lon, label, fly = true) {
    const pos = { lat, lon, label: label || `${lat.toFixed(4)}, ${lon.toFixed(4)}` };
    store.set("pos", pos);
    $("address").value = pos.label;
    if (me) me.setLatLng([lat, lon]); else me = L.marker([lat, lon], { icon: meIcon, title: "Вы здесь" }).addTo(map);
    if (fly) map.setView([lat, lon], Math.max(map.getZoom(), 12));
  }
  map.on("click", (e) => setPosition(e.latlng.lat, e.latlng.lng));

  // ---------- автомобиль
  let vehicles = [], vehicle = store.get("vehicle");
  fetch("/api/vehicles").then((r) => r.json()).then((list) => {
    vehicles = list;
    if (vehicle) { $("vehicle").value = vehicle.label; showVehicle(vehicle); }
  });
  function showVehicle(v) {
    const plugs = [...new Set(v.plugs.map((p) => PLUGS[p] || p))].join(", ");
    $("vehicle-info").textContent = `${plugs}; AC до ${v.ac} кВт` + (v.dc ? `, DC до ${v.dc} кВт` : ", без быстрой зарядки");
  }
  function suggest(input, listEl, items, onPick, render) {
    listEl.innerHTML = items.map((it, i) => `<li data-i="${i}">${esc(render(it))}</li>`).join("");
    listEl.hidden = items.length === 0;
    listEl.onclick = (e) => { const li = e.target.closest("li"); if (li) { onPick(items[+li.dataset.i]); listEl.hidden = true; } };
  }
  $("vehicle").addEventListener("input", (e) => {
    const words = e.target.value.toLowerCase().split(/\s+/).filter(Boolean);
    const found = words.length ? vehicles.filter((v) => words.every((w) => v.label.toLowerCase().includes(w))).slice(0, 12) : [];
    suggest($("vehicle"), $("vehicle-list"), found, (v) => {
      vehicle = v; store.set("vehicle", v); $("vehicle").value = v.label; showVehicle(v);
    }, (v) => v.label);
  });

  // ---------- адрес
  let addrTimer = null;
  $("address").addEventListener("input", (e) => {
    clearTimeout(addrTimer);
    const q = e.target.value.trim();
    if (q.length < 3) { $("address-list").hidden = true; return; }
    addrTimer = setTimeout(async () => {
      const r = await fetch("/api/geocode?q=" + encodeURIComponent(q));
      const items = r.ok ? await r.json() : [];
      suggest($("address"), $("address-list"), items, (it) => setPosition(it.lat, it.lon, it.label), (it) => it.label);
    }, 300);
  });
  $("locate").addEventListener("click", () => {
    if (!navigator.geolocation) { say("Браузер не поддерживает геолокацию — укажите адрес или кликните на карте."); return; }
    say("Определяем местоположение…");
    navigator.geolocation.getCurrentPosition(
      (p) => { setPosition(p.coords.latitude, p.coords.longitude, "Моё местоположение"); say(""); },
      () => say("Не удалось определить местоположение — укажите адрес или кликните на карте."),
      { enableHighAccuracy: true, timeout: 10000 });
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".suggest")) { $("vehicle-list").hidden = true; $("address-list").hidden = true; }
  });
  const saved = store.get("pos");
  if (saved) setPosition(saved.lat, saved.lon, saved.label);

  // ---------- поиск
  function say(html) { $("status").innerHTML = html ? `<p class="small">${html}</p>` : ""; }
  $("search").addEventListener("submit", async (e) => {
    e.preventDefault();
    const pos = store.get("pos");
    if (!vehicle) { say("Выберите автомобиль из списка."); return; }
    if (!pos) { say("Укажите, где вы: адрес, «Я здесь» или клик на карте."); return; }
    $("go").setAttribute("aria-busy", "true");
    say("Получаем текущие статусы станций и считаем вероятности…");
    $("results").innerHTML = "";
    try {
      const body = {
        vehicle_id: vehicle.id, lat: pos.lat, lon: pos.lon,
        energy_kwh: +document.querySelector("input[name=energy]:checked").value,
        dc_only: $("dc_only").checked, open_24h: $("open_24h").checked,
        public_only: $("public_only").checked, has_cable: $("has_cable").checked,
      };
      const r = await fetch("/api/recommend", { method: "POST", headers: { "Content-Type": "application/json" },
                                                body: JSON.stringify(body) });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Ошибка сервера");
      render(data, pos);
    } catch (err) {
      say("Не удалось подобрать станции: " + esc(err.message));
    } finally {
      $("go").removeAttribute("aria-busy");
    }
  });

  function render(data, pos) {
    markers.forEach((m) => m.remove()); markers = [];
    if (routeLine) { routeLine.remove(); routeLine = null; }
    lastItems = data.items;
    const notes = [];
    if (data.status_age_min != null) notes.push(`Статусы станций: ${Math.round(data.status_age_min)} мин назад.`);
    if (data.temperature_c != null) notes.push(`Температура ~${Math.round(data.temperature_c)} °C.`);
    say(notes.join(" ") + (data.warnings.length ? data.warnings.map((w) => `<div class="notice">${esc(w)}</div>`).join("") : ""));
    if (!data.items.length) { $("results").innerHTML = '<p class="empty">Подходящих станций не найдено.</p>'; return; }

    $("results").innerHTML = `<div class="legend"><span><i class="dot" style="background:#0ca30c"></i>высокая вероятность ≥ 70 %</span>
      <span><i class="dot" style="background:#fab219"></i>средняя</span><span><i class="dot" style="background:#d03b3b"></i>низкая &lt; 40 %</span></div>` +
      data.items.map((it, i) => `
      <article class="result" data-i="${i}">
        <h4><span><span class="rank">${it.rank}</span>${esc(it.name || it.street)}</span><span class="total">≈ ${Math.round(it.total_min)} мин</span></h4>
        <div class="meta">${esc(it.street || "")}, ${esc(it.postal_code || "")} ${esc(it.city || "")} · ${esc(it.operator)}</div>
        <div>${it.plugs.map((p) => `<span class="badge">${esc(PLUGS[p] || p)}</span>`).join("")}
             <span class="badge">${it.power_class} ${it.effective_kw ? Math.round(it.effective_kw) + " кВт" : "мощность ?"}</span>
             <span class="badge">${it.distance_km.toFixed(1)} км · ${Math.round(it.eta_min)} мин</span>
             ${it.is_open_24h ? '<span class="badge">24/7</span>' : ""}</div>
        <div class="prob" title="Вероятность, что к приезду будет свободна хотя бы одна подходящая точка">
          <span>Свободна к приезду:</span><span class="bar"><span style="width:${(it.p_station * 100).toFixed(0)}%;background:${probColor(it.p_station)}"></span></span>
          <strong>${(it.p_station * 100).toFixed(0)} %</strong><span class="muted">(${probWord(it.p_station)})</span>
        </div>
        <p class="why">${esc(it.explanation)}</p>
        <div class="details" hidden></div>
      </article>`).join("");

    const bounds = [[pos.lat, pos.lon]];
    data.items.forEach((it, i) => {
      const icon = L.divIcon({ className: "", iconSize: [26, 26],
        html: `<div class="marker-pin" style="background:${probColor(it.p_station)}">${it.rank}</div>` });
      const m = L.marker([it.lat, it.lon], { icon, title: it.name }).addTo(map)
        .bindTooltip(`${it.rank}. ${esc(it.name)} — свободна с вероятностью ${(it.p_station * 100).toFixed(0)} %`);
      m.on("click", () => select(i, true));
      markers.push(m); bounds.push([it.lat, it.lon]);
    });
    map.fitBounds(bounds, { padding: [40, 40] });
    document.querySelectorAll(".result").forEach((el) => el.addEventListener("click", () => select(+el.dataset.i, false)));
  }

  async function select(i, scroll) {
    const it = lastItems[i], pos = store.get("pos");
    document.querySelectorAll(".result").forEach((el) => el.classList.toggle("active", +el.dataset.i === i));
    const card = document.querySelector(`.result[data-i="${i}"]`);
    if (scroll) card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    const r = await fetch(`/api/route?lat=${pos.lat}&lon=${pos.lon}&lat2=${it.lat}&lon2=${it.lon}`).then((x) => x.json());
    if (routeLine) routeLine.remove();
    routeLine = L.geoJSON(r.geometry, { style: { color: "#2a78d6", weight: 4, dashArray: r.straight ? "6 6" : null } }).addTo(map);
    map.fitBounds(routeLine.getBounds(), { padding: [60, 60] });

    const det = card.querySelector(".details");
    if (!det.hidden) return;
    const prof = await fetch(`/api/station/${it.station_id}/profile`).then((x) => x.json());
    const bars = prof.hours.map((h) => `<div title="${h.hour_local}:00 — свободно ${(h.p_free * 100).toFixed(0)} %"
        style="flex:1;align-self:end;height:${Math.max(4, h.p_free * 60)}px;background:#2a78d6;border-radius:3px 3px 0 0;margin:0 1px"></div>`).join("");
    det.innerHTML = `
      <table class="points"><thead><tr><th>Точка</th><th>Ток</th><th>кВт</th><th>Сейчас</th><th>К приезду</th></tr></thead><tbody>
      ${it.points.map((p) => `<tr><td class="mono">${esc(p.evse_id)}</td><td>${p.power_class}</td><td>${p.power_kw ? Math.round(p.power_kw) : "?"}</td>
        <td>${STATUS_RU[p.status_now] || p.status_now}</td><td>${(p.p_free * 100).toFixed(0)} %</td></tr>`).join("")}
      </tbody></table>
      <p class="small muted" style="margin:.4rem 0 .2rem">Обычно свободно по часам сегодня (${prof.source === "station" ? "эта станция" : "кантон"}):</p>
      <div style="display:flex;height:64px;align-items:end">${bars || '<span class="muted small">нет истории</span>'}</div>
      <div class="small muted" style="display:flex;justify-content:space-between"><span>0:00</span><span>12:00</span><span>23:00</span></div>
      <a href="https://www.google.com/maps/dir/?api=1&destination=${it.lat},${it.lon}" target="_blank" rel="noopener">Открыть маршрут в картах ↗</a>`;
    det.hidden = false;
  }
})();
