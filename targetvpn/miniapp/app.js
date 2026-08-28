/* TargetVPN Mini App */
const tg = window.Telegram?.WebApp;
const API = (location.origin.includes('localhost') || location.origin.startsWith('http'))
  ? location.origin : '';

const state = { token: '', user: null, sub: null, devices: [], plans: [], nodes: [], subUrl: '',
                trialAvailable: false, supportUrl: '', methods: [], promo: null, timer: null };

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function haptic(type = 'light') {
  try { tg?.HapticFeedback?.impactOccurred(type); } catch (_) {}
}

function toast(text, ms = 2200) {
  const el = $('#toast');
  el.textContent = text;
  el.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add('hidden'), ms);
}

async function api(path, { method = 'GET', body, auth = true } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (auth && state.token) headers.Authorization = `Bearer ${state.token}`;
  const resp = await fetch(API + path, {
    method, headers, body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    let detail = 'Ошибка сервера';
    try { detail = (await resp.json()).detail || detail; } catch (_) {}
    const err = new Error(typeof detail === 'string' ? detail : 'Ошибка сервера');
    err.status = resp.status;
    throw err;
  }
  return resp.status === 204 ? null : resp.json();
}

/* ---------- Форматирование ---------- */

const plural = (n, forms) => {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return forms[0];
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return forms[1];
  return forms[2];
};

function humanDuration(hours) {
  if (hours < 24) return `${hours} ${plural(hours, ['час', 'часа', 'часов'])}`;
  const days = Math.round(hours / 24);
  if (days % 30 === 0 && days >= 30) {
    const months = days / 30;
    return `${months} ${plural(months, ['месяц', 'месяца', 'месяцев'])}`;
  }
  return `${days} ${plural(days, ['день', 'дня', 'дней'])}`;
}

const fmtDate = (iso) => new Date(iso).toLocaleString('ru-RU',
  { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });

const PLATFORMS = { android: '🤖', ios: '🍏', windows: '🪟', macos: '💻', linux: '🐧', tv: '📺', other: '📱' };

/* ---------- Инициализация ---------- */

async function boot() {
  try { tg?.ready(); tg?.expand(); tg?.setHeaderColor?.('#0b0e14'); tg?.setBackgroundColor?.('#0b0e14'); } catch (_) {}
  try { tg?.disableVerticalSwipes?.(); } catch (_) {}

  const initData = tg?.initData || '';
  try {
    const auth = await api('/api/auth', {
      method: 'POST', auth: false,
      body: { initData, start_param: tg?.initDataUnsafe?.start_param || '' },
    });
    state.token = auth.token;
    state.user = auth.user;
  } catch (err) {
    if (err.status === 403) return showBlocked(err.message);
    $('#splash').innerHTML =
      `<div class="screen-center"><div class="big-emoji">⚠️</div><h2>Не удалось войти</h2>
       <p class="muted">${esc(err.message)}</p>
       <p class="muted">Откройте приложение через бота TargetVPN.</p></div>`;
    return;
  }

  await refresh();
  $('#splash').classList.add('hidden');
  $('#app').classList.remove('hidden');
  if (state.user.role !== 'user') $('#tab-admin').classList.remove('hidden');
  bindNav();
}

function showBlocked(reason) {
  $('#splash').classList.add('hidden');
  $('#blocked-reason').textContent = reason || 'Обратитесь в поддержку.';
  $('#blocked').classList.remove('hidden');
}

async function refresh() {
  const data = await api('/api/state');
  state.user = data.user;
  state.sub = data.subscription;
  state.devices = data.devices;
  state.subUrl = data.sub_url;
  state.supportUrl = data.support_url;
  state.trialAvailable = data.trial_available;
  state.methods = data.payment_methods || [];
  renderHome();
  renderDevices();
  renderProfile();
}

function bindNav() {
  $$('.tabbar-btn').forEach((btn) => btn.addEventListener('click', () => {
    haptic();
    goto(btn.dataset.goto);
  }));
  $('#btn-support').addEventListener('click', () => {
    if (state.supportUrl) tg?.openTelegramLink?.(state.supportUrl) || window.open(state.supportUrl);
  });
  $('#btn-add-device').addEventListener('click', openAddDevice);
  $('#btn-copy-sub').addEventListener('click', () => copy(state.subUrl, 'Ссылка-подписка скопирована'));
  $('#promo-apply').addEventListener('click', checkPromo);
  $('#sheet').addEventListener('click', (e) => {
    if (e.target.classList.contains('sheet-backdrop')) closeSheet();
  });
  $$('#admin-tabs .tab').forEach((tab) => tab.addEventListener('click', () => {
    $$('#admin-tabs .tab').forEach((t) => t.classList.toggle('active', t === tab));
    renderAdmin(tab.dataset.tab);
  }));
}

function goto(page) {
  $$('.page').forEach((p) => p.classList.toggle('hidden', p.dataset.page !== page));
  $$('.tabbar-btn').forEach((b) => b.classList.toggle('active', b.dataset.goto === page));
  window.scrollTo({ top: 0 });
  if (page === 'plans') loadPlans();
  if (page === 'admin') renderAdmin('stats');
}

/* ---------- Главная ---------- */

function renderHome() {
  const card = $('#status-card');
  const actions = $('#home-actions');
  clearInterval(state.timer);

  if (state.sub) {
    const trial = state.sub.is_trial ? '<span class="badge trial">Пробный</span>' : '';
    card.innerHTML = `
      <div class="status-head">
        <div class="status-state"><span class="dot on"></span>Подписка активна</div>${trial}
      </div>
      <div class="status-plan">${esc(state.sub.plan_title)}</div>
      <div class="status-meta">Устройств: ${state.sub.devices_used} из ${state.sub.devices} ·
        до ${fmtDate(state.sub.expires_at)}</div>
      <div class="countdown" id="countdown"></div>`;
    startCountdown(state.sub.seconds_left);
    actions.innerHTML = `
      <button class="btn btn-primary wide" data-act="devices">📱 Мои ключи и устройства</button>
      <button class="btn btn-ghost wide" data-act="plans">💎 Продлить или сменить тариф</button>`;
  } else {
    card.innerHTML = `
      <div class="status-head">
        <div class="status-state"><span class="dot off"></span>Подписка не активна</div>
      </div>
      <div class="status-plan">Нет доступа</div>
      <div class="status-meta">Выберите тариф — ключи выдаются мгновенно.</div>`;
    actions.innerHTML = `
      ${state.trialAvailable
        ? `<button class="btn btn-primary wide" data-act="trial">🎁 Пробные 24 часа · 3 устройства</button>` : ''}
      <button class="btn ${state.trialAvailable ? 'btn-ghost' : 'btn-primary'} wide" data-act="plans">💎 Выбрать тариф</button>`;
  }

  actions.querySelectorAll('button').forEach((btn) => btn.addEventListener('click', () => {
    haptic();
    if (btn.dataset.act === 'trial') return activateTrial(btn);
    goto(btn.dataset.act);
  }));
}

function startCountdown(seconds) {
  const box = $('#countdown');
  if (!box) return;
  let left = seconds;
  const draw = () => {
    if (left <= 0) { clearInterval(state.timer); refresh(); return; }
    const d = Math.floor(left / 86400), h = Math.floor((left % 86400) / 3600);
    const m = Math.floor((left % 3600) / 60), s = left % 60;
    box.innerHTML = [[d, 'дней'], [h, 'часов'], [m, 'минут'], [s, 'секунд']]
      .map(([v, l]) => `<div class="time-box"><b>${String(v).padStart(2, '0')}</b><small>${l}</small></div>`)
      .join('');
    left -= 1;
  };
  draw();
  state.timer = setInterval(draw, 1000);
}

async function activateTrial(btn) {
  btn.disabled = true;
  try {
    await api('/api/trial', { method: 'POST' });
    toast('Пробный доступ активирован на 24 часа');
    haptic('medium');
    await refresh();
    goto('devices');
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
}

/* ---------- Тарифы ---------- */

async function loadPlans() {
  const list = $('#plans-list');
  list.innerHTML = '<div class="empty">Загружаем тарифы…</div>';
  try {
    state.plans = await api('/api/plans');
  } catch (err) {
    list.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    return;
  }
  const paid = state.plans.filter((p) => !p.is_trial);
  if (!paid.length) { list.innerHTML = '<div class="empty">Тарифы скоро появятся</div>'; return; }

  list.innerHTML = paid.map((plan) => {
    const price = state.promo ? state.promo.prices[plan.id] ?? plan.price_rub : plan.price_rub;
    const old = plan.old_price_rub && plan.old_price_rub > price ? `<s>${plan.old_price_rub} ₽</s>` : '';
    return `
      <div class="plan ${plan.is_popular ? 'popular' : ''}" data-plan="${plan.id}">
        <div class="plan-head">
          <div>
            <div class="plan-name">${esc(plan.emoji)} ${esc(plan.title)}
              ${plan.is_popular ? '<span class="badge hot">Хит</span>' : ''}</div>
            <div class="plan-desc">${esc(plan.description)}</div>
          </div>
          <div class="plan-price">${old}<b>${price} ₽</b></div>
        </div>
        <div class="plan-props">
          <span class="prop">📅 ${humanDuration(plan.duration_hours)}</span>
          <span class="prop">📱 ${plan.devices} ${plural(plan.devices, ['устройство', 'устройства', 'устройств'])}</span>
          <span class="prop">${plan.traffic_gb ? '📊 ' + plan.traffic_gb + ' ГБ' : '♾ Безлимит'}</span>
        </div>
        <button class="btn btn-primary wide" data-buy="${plan.id}">Оформить за ${price} ₽</button>
      </div>`;
  }).join('');

  list.querySelectorAll('[data-buy]').forEach((btn) => btn.addEventListener('click', () => {
    haptic();
    openPayment(Number(btn.dataset.buy));
  }));
}

async function checkPromo() {
  const code = $('#promo-input').value.trim();
  const status = $('#promo-status');
  if (!code) { state.promo = null; status.textContent = ''; return loadPlans(); }
  const paid = state.plans.filter((p) => !p.is_trial);
  if (!paid.length) return;
  try {
    const prices = {};
    let info = null;
    for (const plan of paid) {
      const res = await api('/api/promo/check', { method: 'POST', body: { code, plan_id: plan.id } });
      prices[plan.id] = res.price_rub;
      info = res;
    }
    state.promo = { code, prices };
    status.innerHTML = `<span class="badge ok">Промокод применён: −${info.discount_percent}%` +
      `${info.bonus_days ? ` и +${info.bonus_days} дн.` : ''}</span>`;
    loadPlans();
  } catch (err) {
    state.promo = null;
    status.innerHTML = `<span class="badge bad">${esc(err.message)}</span>`;
    loadPlans();
  }
}

/* ---------- Оплата ---------- */

const PAY_LABELS = {
  stars: '⭐️ Telegram Stars',
  cryptobot: '💎 Криптой (USDT/TON)',
  lzt: '🐝 Переводом на LZT Market',
};

function openPayment(planId) {
  const plan = state.plans.find((p) => p.id === planId);
  if (!plan) return;
  const price = state.promo ? state.promo.prices[plan.id] ?? plan.price_rub : plan.price_rub;
  const methods = state.methods.length ? state.methods : ['stars'];
  openSheet(`
    <div class="sheet-title">${esc(plan.emoji)} ${esc(plan.title)} · ${price} ₽</div>
    <p class="muted" style="margin-top:-6px">${humanDuration(plan.duration_hours)},
      ${plan.devices} ${plural(plan.devices, ['устройство', 'устройства', 'устройств'])}</p>
    <div class="stack" style="margin-top:14px">
      ${methods.map((m, i) => `<button class="btn ${i === 0 ? 'btn-primary' : 'btn-ghost'} wide"
        data-pay="${m}">${PAY_LABELS[m] || m}</button>`).join('')}
    </div>
    <p class="muted" style="font-size:12px;margin-top:14px">
      Подписка активируется автоматически сразу после оплаты.</p>`);

  $('#sheet-body').querySelectorAll('[data-pay]').forEach((btn) =>
    btn.addEventListener('click', () => pay(plan, btn.dataset.pay, btn)));
}

async function pay(plan, method, btn) {
  btn.disabled = true;
  btn.textContent = 'Готовим счёт…';
  try {
    const res = await api('/api/purchase', {
      method: 'POST',
      body: { plan_id: plan.id, method, promo_code: state.promo?.code || '' },
    });
    closeSheet();
    if (method === 'stars' && res.invoice_link) {
      tg?.openInvoice?.(res.invoice_link, (status) => {
        if (status === 'paid') { toast('Оплата прошла, активируем подписку…'); pollPayment(res.payment_id); }
        else if (status === 'failed') toast('Платёж не прошёл');
      });
    } else if (method === 'lzt') {
      openLztInstructions(res);
      pollPayment(res.payment_id, 60);
    } else if (res.invoice_url) {
      tg?.openTelegramLink?.(res.invoice_url) || window.open(res.invoice_url, '_blank');
      toast('Счёт открыт в CryptoBot. После оплаты вернитесь сюда.');
      pollPayment(res.payment_id, 40);
    }
  } catch (err) {
    toast(err.message);
    btn.disabled = false;
  }
}

function openLztInstructions(res) {
  openSheet(`
    <div class="sheet-title">Перевод на LZT Market</div>
    <p class="muted" style="margin-top:-6px;font-size:13px">
      Переведите <b>${res.amount_native} ₽</b> и обязательно укажите комментарий —
      по нему платёж находится автоматически, обычно за минуту.</p>
    <div class="field" style="margin-top:12px"><label>Комментарий к переводу</label>
      <div class="key-box" id="lzt-comment">${esc(res.comment)}</div></div>
    <div class="stack" style="margin-top:12px">
      <button class="btn btn-ghost wide" id="lzt-copy">📋 Скопировать комментарий</button>
      <button class="btn btn-primary wide" id="lzt-open">Открыть форму перевода</button>
    </div>
    <p class="muted" style="font-size:12px;margin-top:12px">
      Окно можно закрыть — подписка включится сама, придёт уведомление от бота.</p>`);
  document.querySelector('#lzt-copy').addEventListener('click',
    () => copy(res.comment, 'Комментарий скопирован'));
  document.querySelector('#lzt-open').addEventListener('click', () => {
    if (res.invoice_url) tg?.openLink?.(res.invoice_url) || window.open(res.invoice_url, '_blank');
  });
}

async function pollPayment(paymentId, attempts = 20) {
  for (let i = 0; i < attempts; i += 1) {
    await new Promise((r) => setTimeout(r, 3000));
    try {
      const res = await api(`/api/payments/${paymentId}`);
      if (res.paid) {
        haptic('medium');
        toast('Подписка активирована 🎉');
        await refresh();
        goto('devices');
        return;
      }
    } catch (_) { /* повторим */ }
  }
}

/* ---------- Устройства ---------- */

function renderDevices() {
  const list = $('#devices-list');
  const limit = $('#devices-limit');
  const addBtn = $('#btn-add-device');

  if (!state.sub) {
    limit.textContent = 'Устройства доступны после оформления подписки.';
    list.innerHTML = '<div class="empty">Нет активной подписки</div>';
    addBtn.classList.add('hidden');
    return;
  }
  addBtn.classList.remove('hidden');
  const used = state.devices.filter((d) => d.is_active).length;
  limit.textContent = `Использовано ${used} из ${state.sub.devices} по тарифу «${state.sub.plan_title}».`;
  addBtn.disabled = used >= state.sub.devices;

  if (!state.devices.length) {
    list.innerHTML = '<div class="empty">Пока нет устройств — добавьте первое</div>';
    return;
  }
  list.innerHTML = state.devices.map((d) => `
    <div class="device">
      <div class="device-ico">${PLATFORMS[d.platform] || '📱'}</div>
      <div class="device-info">
        <b>${esc(d.name)}</b>
        <small>${d.node_flag ? esc(d.node_flag) + ' ' + esc(d.node_title) + ' · ' : ''}${d.is_active ? 'Активно' : 'Отключено'} · ${d.used_traffic_gb} ГБ</small>
      </div>
      <button class="btn btn-sm btn-ghost" data-key="${d.id}">Ключ</button>
    </div>`).join('');
  list.querySelectorAll('[data-key]').forEach((btn) =>
    btn.addEventListener('click', () => openDeviceSheet(Number(btn.dataset.key))));
}

async function openAddDevice() {
  if (!state.sub) return toast('Сначала оформите подписку');
  try { state.nodes = await api('/api/nodes'); } catch (_) { state.nodes = []; }
  const locations = state.nodes.length > 1 ? `
      <div class="field"><label>Локация</label>
        <select class="input" id="dev-node">
          ${state.nodes.map((n) => `<option value="${n.id}" ${n.is_default ? 'selected' : ''}>
            ${esc(n.flag)} ${esc(n.title)}</option>`).join('')}
        </select></div>` : '';
  openSheet(`
    <div class="sheet-title">Новое устройство</div>
    <div class="stack">
      <div class="field"><label>Название</label>
        <input class="input" id="dev-name" placeholder="Например: iPhone Паши" maxlength="40" /></div>
      <div class="field"><label>Платформа</label>
        <select class="input" id="dev-platform">
          <option value="android">Android</option>
          <option value="ios">iPhone / iPad</option>
          <option value="windows">Windows</option>
          <option value="macos">macOS</option>
          <option value="linux">Linux</option>
          <option value="tv">Android TV</option>
          <option value="other">Другое</option>
        </select></div>
      ${locations}
      <button class="btn btn-primary wide" id="dev-save">Создать ключ</button>
    </div>`);
  $('#dev-save').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const nodeSelect = document.querySelector('#dev-node');
      const device = await api('/api/devices', {
        method: 'POST',
        body: {
          name: $('#dev-name').value.trim() || 'Устройство',
          platform: $('#dev-platform').value,
          node_id: nodeSelect ? Number(nodeSelect.value) : null,
        },
      });
      await refresh();
      openDeviceSheet(device.id);
      toast('Устройство добавлено');
      haptic('medium');
    } catch (err) {
      toast(err.message);
      btn.disabled = false;
    }
  });
}

function openDeviceSheet(deviceId) {
  const device = state.devices.find((d) => d.id === deviceId);
  if (!device) return;
  const link = device.config_url || '';
  openSheet(`
    <div class="sheet-title">${PLATFORMS[device.platform] || '📱'} ${esc(device.name)}</div>
    <p class="muted" style="margin-top:-6px;font-size:12.5px">
      ${device.node_title ? esc(device.node_flag) + ' ' + esc(device.node_title) + ' · ' : ''}
      Вставьте ключ в клиент или импортируйте ссылку-подписку.</p>
    <div class="key-box" id="key-box">${esc(link) || 'Ключ выдаётся…'}</div>
    <div class="stack" style="margin-top:12px">
      <button class="btn btn-primary wide" data-act="copy">📋 Скопировать ключ</button>
      <button class="btn btn-ghost wide" data-act="import">📲 Открыть в клиенте</button>
      <div class="row">
        <button class="btn btn-ghost btn-sm" data-act="refresh">♻️ Перевыпустить</button>
        <button class="btn btn-danger btn-sm" data-act="delete">🗑 Удалить</button>
      </div>
    </div>`);

  $('#sheet-body').querySelectorAll('[data-act]').forEach((btn) => btn.addEventListener('click', async () => {
    const act = btn.dataset.act;
    if (act === 'copy') return copy(link, 'Ключ скопирован');
    if (act === 'import') {
      // Универсальный импорт: клиенты перехватывают схему vless://
      window.location.href = link;
      return;
    }
    if (act === 'refresh') {
      btn.disabled = true;
      try {
        await api(`/api/devices/${deviceId}/refresh`, { method: 'POST' });
        await refresh();
        closeSheet();
        toast('Ключ перевыпущен');
      } catch (err) { toast(err.message); btn.disabled = false; }
      return;
    }
    if (act === 'delete') {
      if (!confirm('Удалить устройство и отозвать его ключ?')) return;
      try {
        await api(`/api/devices/${deviceId}`, { method: 'DELETE' });
        await refresh();
        closeSheet();
        toast('Устройство удалено');
      } catch (err) { toast(err.message); }
    }
  }));
}

/* ---------- Профиль ---------- */

function renderProfile() {
  const u = state.user;
  $('#profile-card').innerHTML = `
    <div class="card">
      <div class="list-row" style="background:none;border:0;padding:0">
        <div class="device-ico">${u.role === 'user' ? '👤' : '🛠'}</div>
        <div class="grow"><b>${esc(u.first_name || 'Пользователь')}</b>
          <small>${u.username ? '@' + esc(u.username) : 'ID ' + u.tg_id}</small></div>
        ${u.role !== 'user' ? `<span class="badge hot">${u.role === 'owner' ? 'Владелец' : 'Админ'}</span>` : ''}
      </div>
    </div>
    <div class="card">
      <h3 style="margin:0 0 10px;font-size:15px">Приглашайте друзей</h3>
      <p class="muted" style="font-size:13px;margin:0 0 12px">
        За первую оплату каждого приглашённого вам начисляются бонусные дни.
        Приглашено: <b>${u.referrals}</b>.</p>
      <button class="btn btn-primary wide" id="btn-ref">🔗 Поделиться ссылкой</button>
    </div>
    <div class="card">
      <h3 style="margin:0 0 10px;font-size:15px">Ссылка-подписка</h3>
      <p class="muted" style="font-size:13px;margin:0 0 12px">
        Один адрес для всех устройств: клиент сам подтянет обновлённые ключи.</p>
      <button class="btn btn-ghost wide" id="btn-sub-copy">Скопировать</button>
    </div>
    <button class="btn btn-ghost wide" id="btn-support2">💬 Написать в поддержку</button>`;

  $('#btn-ref').addEventListener('click', () => {
    const text = 'TargetVPN — быстрый доступ без блокировок. Пробный период 24 часа бесплатно:';
    const url = `https://t.me/share/url?url=${encodeURIComponent(u.ref_link)}&text=${encodeURIComponent(text)}`;
    tg?.openTelegramLink?.(url) || window.open(url, '_blank');
  });
  $('#btn-sub-copy').addEventListener('click', () => copy(state.subUrl, 'Ссылка скопирована'));
  $('#btn-support2').addEventListener('click', () => {
    if (state.supportUrl) tg?.openTelegramLink?.(state.supportUrl) || window.open(state.supportUrl);
  });
}

/* ---------- Утилиты UI ---------- */

function copy(text, message) {
  if (!text) return toast('Нечего копировать');
  navigator.clipboard?.writeText(text).then(() => toast(message), () => {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
    toast(message);
  });
  haptic();
}

function openSheet(html) {
  $('#sheet-body').innerHTML = html;
  $('#sheet').classList.remove('hidden');
}

function closeSheet() { $('#sheet').classList.add('hidden'); }

boot();
