/* Админ-панель TargetVPN: тарифы, пользователи, промокоды, статистика, логи. */

async function renderAdmin(tab) {
  const body = document.querySelector('#admin-body');
  body.innerHTML = '<div class="empty">Загрузка…</div>';
  try {
    if (tab === 'stats') return adminStats(body);
    if (tab === 'plans') return adminPlans(body);
    if (tab === 'users') return adminUsers(body);
    if (tab === 'promos') return adminPromos(body);
    if (tab === 'logs') return adminLogs(body);
  } catch (err) {
    body.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}

async function adminStats(body) {
  const s = await api('/api/admin/stats');
  body.innerHTML = `
    <div class="stat-grid">
      <div class="stat"><b>${s.users_total}</b><small>Всего пользователей</small></div>
      <div class="stat"><b>${s.subs_active}</b><small>Активных подписок</small></div>
      <div class="stat"><b>${s.revenue_month.toFixed(0)} ₽</b><small>Выручка за 30 дней</small></div>
      <div class="stat"><b>${s.revenue_total.toFixed(0)} ₽</b><small>Выручка всего</small></div>
      <div class="stat"><b>${s.devices_active}</b><small>Активных устройств</small></div>
      <div class="stat"><b>${s.trials_active}</b><small>На пробном</small></div>
      <div class="stat"><b>${s.new_users_today}</b><small>Новых за сутки</small></div>
      <div class="stat"><b>${s.users_banned}</b><small>Заблокировано</small></div>
    </div>
    <div class="card">
      <div class="list-row" style="background:none;border:0;padding:0">
        <div class="grow"><b>VPN-нода</b><small>Marzban API</small></div>
        <span class="badge ${s.node_online ? 'ok' : 'bad'}">${s.node_online ? 'Онлайн' : 'Недоступна'}</span>
      </div>
    </div>
    <button class="btn btn-ghost wide" id="btn-broadcast">📢 Рассылка всем пользователям</button>
    <div id="admin-payments" class="stack"></div>`;

  document.querySelector('#btn-broadcast').addEventListener('click', adminBroadcast);

  const payments = await api('/api/admin/payments?limit=15');
  document.querySelector('#admin-payments').innerHTML =
    `<h3 style="margin:16px 0 2px;font-size:15px">Последние платежи</h3>` +
    (payments.length ? payments.map((p) => `
      <div class="list-row">
        <div class="grow"><b>${p.amount_rub} ₽ · ${esc(p.provider)}</b>
          <small>ID ${p.tg_id ?? '—'} · ${fmtDate(p.at)}</small></div>
        <span class="badge ${p.status === 'paid' ? 'ok' : 'bad'}">${p.status === 'paid' ? 'Оплачен' : esc(p.status)}</span>
      </div>`).join('') : '<div class="empty">Платежей пока нет</div>');
}

function adminBroadcast() {
  openSheet(`
    <div class="sheet-title">Рассылка</div>
    <div class="stack">
      <div class="field"><label>Текст сообщения</label>
        <textarea class="input" id="bc-text" rows="5" placeholder="Что сообщить пользователям?"></textarea></div>
      <label class="muted" style="font-size:13px">
        <input type="checkbox" id="bc-active" /> только с активной подпиской</label>
      <button class="btn btn-primary wide" id="bc-send">Отправить</button>
    </div>`);
  document.querySelector('#bc-send').addEventListener('click', async (e) => {
    const text = document.querySelector('#bc-text').value.trim();
    if (!text) return toast('Введите текст');
    e.currentTarget.disabled = true;
    try {
      const res = await api('/api/admin/broadcast', {
        method: 'POST',
        body: { text, only_active: document.querySelector('#bc-active').checked },
      });
      closeSheet();
      toast(`Поставлено в очередь: ${res.queued}`);
    } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
  });
}

/* --- Тарифы --- */

async function adminPlans(body) {
  const plans = await api('/api/admin/plans');
  body.innerHTML = `
    <button class="btn btn-primary wide" id="plan-new">+ Новый тариф</button>
    ${plans.map((p) => `
      <div class="list-row">
        <div class="device-ico">${esc(p.emoji)}</div>
        <div class="grow"><b>${esc(p.title)} · ${p.price_rub} ₽</b>
          <small>${humanDuration(p.duration_hours)} · ${p.devices} устр. · код ${esc(p.code)}
            ${p.is_trial ? '· пробный' : ''}</small></div>
        <span class="badge ${p.is_active ? 'ok' : 'bad'}">${p.is_active ? 'вкл' : 'выкл'}</span>
        <button class="btn btn-sm btn-ghost" data-edit="${p.id}">✏️</button>
      </div>`).join('')}`;

  document.querySelector('#plan-new').addEventListener('click', () => planForm(null));
  body.querySelectorAll('[data-edit]').forEach((btn) => btn.addEventListener('click', () =>
    planForm(plans.find((p) => p.id === Number(btn.dataset.edit)))));
}

function planForm(plan) {
  const v = plan || { code: '', title: '', description: '', emoji: '🚀', price_rub: 149,
                      duration_hours: 720, devices: 1, traffic_gb: 0, is_trial: false,
                      is_active: true, is_popular: false, sort_order: 50, old_price_rub: null };
  openSheet(`
    <div class="sheet-title">${plan ? 'Редактирование тарифа' : 'Новый тариф'}</div>
    <div class="stack">
      <div class="grid-2">
        <div class="field"><label>Название</label>
          <input class="input" id="pf-title" value="${esc(v.title)}" /></div>
        <div class="field"><label>Код (латиницей)</label>
          <input class="input" id="pf-code" value="${esc(v.code)}" ${plan ? 'disabled' : ''} /></div>
      </div>
      <div class="field"><label>Описание</label>
        <input class="input" id="pf-desc" value="${esc(v.description)}" /></div>
      <div class="grid-2">
        <div class="field"><label>Цена, ₽</label>
          <input class="input" id="pf-price" type="number" min="0" value="${v.price_rub}" /></div>
        <div class="field"><label>Старая цена, ₽</label>
          <input class="input" id="pf-old" type="number" min="0" value="${v.old_price_rub ?? ''}" /></div>
      </div>
      <div class="grid-2">
        <div class="field"><label>Длительность, часов</label>
          <input class="input" id="pf-hours" type="number" min="1" value="${v.duration_hours}" /></div>
        <div class="field"><label>Устройств</label>
          <input class="input" id="pf-dev" type="number" min="1" max="20" value="${v.devices}" /></div>
      </div>
      <div class="grid-2">
        <div class="field"><label>Трафик, ГБ (0 — безлимит)</label>
          <input class="input" id="pf-gb" type="number" min="0" value="${v.traffic_gb}" /></div>
        <div class="field"><label>Эмодзи</label>
          <input class="input" id="pf-emoji" value="${esc(v.emoji)}" maxlength="4" /></div>
      </div>
      <div class="field"><label>Порядок в списке</label>
        <input class="input" id="pf-sort" type="number" value="${v.sort_order}" /></div>
      <label class="muted" style="font-size:13px">
        <input type="checkbox" id="pf-active" ${v.is_active ? 'checked' : ''} /> тариф активен</label>
      <label class="muted" style="font-size:13px">
        <input type="checkbox" id="pf-popular" ${v.is_popular ? 'checked' : ''} /> отметка «Хит»</label>
      <label class="muted" style="font-size:13px">
        <input type="checkbox" id="pf-trial" ${v.is_trial ? 'checked' : ''} /> пробный тариф (выдаётся один раз)</label>
      <button class="btn btn-primary wide" id="pf-save">Сохранить</button>
    </div>`);

  document.querySelector('#pf-save').addEventListener('click', async (e) => {
    const num = (id) => Number(document.querySelector(id).value);
    const oldPrice = document.querySelector('#pf-old').value;
    const payload = {
      id: plan?.id ?? null,
      code: (plan?.code || document.querySelector('#pf-code').value).trim().toLowerCase(),
      title: document.querySelector('#pf-title').value.trim(),
      description: document.querySelector('#pf-desc').value.trim(),
      emoji: document.querySelector('#pf-emoji').value.trim() || '🚀',
      price_rub: num('#pf-price'),
      old_price_rub: oldPrice === '' ? null : Number(oldPrice),
      duration_hours: num('#pf-hours'),
      devices: num('#pf-dev'),
      traffic_gb: num('#pf-gb'),
      sort_order: num('#pf-sort'),
      is_active: document.querySelector('#pf-active').checked,
      is_popular: document.querySelector('#pf-popular').checked,
      is_trial: document.querySelector('#pf-trial').checked,
    };
    if (!payload.code || !payload.title) return toast('Заполните код и название');
    e.currentTarget.disabled = true;
    try {
      await api('/api/admin/plans', { method: 'POST', body: payload });
      closeSheet();
      toast('Тариф сохранён');
      renderAdmin('plans');
    } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
  });
}

/* --- Пользователи --- */

async function adminUsers(body, query = '') {
  const users = await api(`/api/admin/users?q=${encodeURIComponent(query)}&limit=50`);
  body.innerHTML = `
    <div class="promo-row">
      <input class="input" id="u-search" placeholder="ID, @username или имя" value="${esc(query)}" />
      <button class="btn btn-ghost" id="u-find">Найти</button>
    </div>
    ${users.length ? users.map((u) => `
      <div class="list-row">
        <div class="grow">
          <b>${esc(u.first_name || 'Без имени')} ${u.username ? '@' + esc(u.username) : ''}</b>
          <small>ID ${u.tg_id} · ${u.plan_title
            ? esc(u.plan_title) + ' до ' + fmtDate(u.expires_at) : 'без подписки'} · устройств: ${u.devices}</small>
        </div>
        ${u.is_banned ? '<span class="badge bad">бан</span>' : ''}
        ${u.role !== 'user' ? `<span class="badge hot">${u.role === 'owner' ? 'owner' : 'admin'}</span>` : ''}
        <button class="btn btn-sm btn-ghost" data-user="${u.tg_id}">⚙️</button>
      </div>`).join('') : '<div class="empty">Никого не нашли</div>'}`;

  const search = () => adminUsers(body, document.querySelector('#u-search').value.trim());
  document.querySelector('#u-find').addEventListener('click', search);
  document.querySelector('#u-search').addEventListener('keydown', (e) => { if (e.key === 'Enter') search(); });
  body.querySelectorAll('[data-user]').forEach((btn) => btn.addEventListener('click', () =>
    userSheet(users.find((u) => u.tg_id === Number(btn.dataset.user)))));
}

async function userSheet(user) {
  const plans = await api('/api/admin/plans');
  const isOwner = state.user.role === 'owner';
  openSheet(`
    <div class="sheet-title">${esc(user.first_name || 'Пользователь')} · ${user.tg_id}</div>
    <p class="muted" style="margin-top:-8px;font-size:13px">
      ${user.plan_title ? `Тариф «${esc(user.plan_title)}» до ${fmtDate(user.expires_at)}` : 'Подписки нет'} ·
      Пробный ${user.trial_used ? 'использован' : 'доступен'}</p>
    <div class="stack">
      <div class="field"><label>Выдать тариф</label>
        <select class="input" id="g-plan">
          <option value="">— вручную по часам —</option>
          ${plans.filter((p) => p.is_active).map((p) =>
            `<option value="${p.id}">${esc(p.title)} · ${humanDuration(p.duration_hours)} · ${p.devices} устр.</option>`).join('')}
        </select></div>
      <div class="grid-2">
        <div class="field"><label>Часов (если вручную)</label>
          <input class="input" id="g-hours" type="number" min="1" placeholder="720" /></div>
        <div class="field"><label>Устройств</label>
          <input class="input" id="g-dev" type="number" min="1" max="20" placeholder="3" /></div>
      </div>
      <button class="btn btn-primary wide" id="g-grant">🎁 Выдать / продлить</button>
      <button class="btn btn-ghost wide" id="g-revoke">⛔️ Отозвать подписку</button>
      <div class="field"><label>Причина блокировки</label>
        <input class="input" id="g-reason" value="${esc(user.ban_reason || '')}" placeholder="Например: абуз" /></div>
      <button class="btn ${user.is_banned ? 'btn-ghost' : 'btn-danger'} wide" id="g-ban">
        ${user.is_banned ? '✅ Разблокировать' : '🚫 Заблокировать'}</button>
      ${isOwner && user.role !== 'owner' ? `
        <button class="btn btn-ghost wide" id="g-role">
          ${user.role === 'admin' ? '👤 Снять права админа' : '🛠 Назначить админом'}</button>` : ''}
    </div>`);

  const run = async (btn, fn) => {
    btn.disabled = true;
    try { await fn(); closeSheet(); renderAdmin('users'); }
    catch (err) { toast(err.message); btn.disabled = false; }
  };

  document.querySelector('#g-grant').addEventListener('click', (e) => run(e.currentTarget, async () => {
    const planId = document.querySelector('#g-plan').value;
    const hours = document.querySelector('#g-hours').value;
    const devices = document.querySelector('#g-dev').value;
    if (!planId && !hours) throw new Error('Выберите тариф или укажите часы');
    await api('/api/admin/grant', { method: 'POST', body: {
      tg_id: user.tg_id,
      plan_id: planId ? Number(planId) : null,
      hours: hours ? Number(hours) : null,
      devices: devices ? Number(devices) : null,
    }});
    toast('Подписка выдана');
  }));

  document.querySelector('#g-revoke').addEventListener('click', (e) => run(e.currentTarget, async () => {
    await api('/api/admin/revoke', { method: 'POST', body: { tg_id: user.tg_id } });
    toast('Подписка отозвана');
  }));

  document.querySelector('#g-ban').addEventListener('click', (e) => run(e.currentTarget, async () => {
    await api('/api/admin/ban', { method: 'POST', body: {
      tg_id: user.tg_id, banned: !user.is_banned,
      reason: document.querySelector('#g-reason').value.trim(),
    }});
    toast(user.is_banned ? 'Разблокирован' : 'Заблокирован');
  }));

  document.querySelector('#g-role')?.addEventListener('click', (e) => run(e.currentTarget, async () => {
    await api('/api/admin/role', { method: 'POST', body: {
      tg_id: user.tg_id, role: user.role === 'admin' ? 'user' : 'admin',
    }});
    toast('Роль обновлена');
  }));
}

/* --- Промокоды --- */

async function adminPromos(body) {
  const promos = await api('/api/admin/promos');
  body.innerHTML = `
    <button class="btn btn-primary wide" id="promo-new">+ Новый промокод</button>
    ${promos.length ? promos.map((p) => `
      <div class="list-row">
        <div class="grow"><b>${esc(p.code)}</b>
          <small>−${p.discount_percent}% ${p.bonus_days ? '· +' + p.bonus_days + ' дн.' : ''}
            · использован ${p.used_count}${p.max_uses ? ' из ' + p.max_uses : ''}</small></div>
        <span class="badge ${p.is_active ? 'ok' : 'bad'}">${p.is_active ? 'вкл' : 'выкл'}</span>
        <button class="btn btn-sm btn-danger" data-del="${p.id}">🗑</button>
      </div>`).join('') : '<div class="empty">Промокодов нет</div>'}`;

  document.querySelector('#promo-new').addEventListener('click', () => {
    openSheet(`
      <div class="sheet-title">Новый промокод</div>
      <div class="stack">
        <div class="field"><label>Код</label>
          <input class="input" id="pr-code" placeholder="TARGET25" /></div>
        <div class="grid-2">
          <div class="field"><label>Скидка, %</label>
            <input class="input" id="pr-disc" type="number" min="0" max="100" value="25" /></div>
          <div class="field"><label>Бонусных дней</label>
            <input class="input" id="pr-days" type="number" min="0" value="0" /></div>
        </div>
        <div class="field"><label>Лимит применений (0 — без лимита)</label>
          <input class="input" id="pr-max" type="number" min="0" value="0" /></div>
        <button class="btn btn-primary wide" id="pr-save">Создать</button>
      </div>`);
    document.querySelector('#pr-save').addEventListener('click', async (e) => {
      const code = document.querySelector('#pr-code').value.trim();
      if (!code) return toast('Введите код');
      e.currentTarget.disabled = true;
      try {
        await api('/api/admin/promos', { method: 'POST', body: {
          code,
          discount_percent: Number(document.querySelector('#pr-disc').value),
          bonus_days: Number(document.querySelector('#pr-days').value),
          max_uses: Number(document.querySelector('#pr-max').value),
          is_active: true,
        }});
        closeSheet();
        toast('Промокод создан');
        renderAdmin('promos');
      } catch (err) { toast(err.message); e.currentTarget.disabled = false; }
    });
  });

  body.querySelectorAll('[data-del]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!confirm('Удалить промокод?')) return;
    await api(`/api/admin/promos/${btn.dataset.del}`, { method: 'DELETE' });
    renderAdmin('promos');
  }));
}

/* --- Логи --- */

async function adminLogs(body) {
  const logs = await api('/api/admin/logs?limit=60');
  body.innerHTML = logs.length ? logs.map((l) => `
    <div class="list-row">
      <div class="grow"><b>${esc(l.action)} ${esc(l.target)}</b>
        <small>${esc(l.details || '')} · админ ${l.admin} · ${fmtDate(l.at)}</small></div>
    </div>`).join('') : '<div class="empty">Действий пока не было</div>';
}
