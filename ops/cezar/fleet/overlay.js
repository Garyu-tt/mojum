/* Cezar Fleet visibility. Read-only, no agent/PR writes, no untrusted HTML. */
(() => {
  "use strict";
  if (window.__cezarFleetR01) return;
  window.__cezarFleetR01 = true;
  const id = "cezar-fleet-r01";
  const style = document.createElement("style");
  style.textContent = `
    #${id} { position: fixed; z-index: 2147480000; bottom: 16px; right: 16px;
      color: #eef3fa; font: 13px/1.45 system-ui,-apple-system,sans-serif; max-width: min(355px,calc(100vw - 24px)); }
    #${id} button { border:1px solid #465877; background:#182234; color:#ecf3fb; border-radius:11px;
      cursor:pointer; padding:9px 12px; font: inherit; box-shadow:0 2px 14px #0007; }
    #${id} button:focus-visible {outline:2px solid #75baff;outline-offset:2px;}
    #${id} .fleet-panel {width:min(345px,calc(100vw - 28px));max-height:min(75vh,550px);overflow:auto;
      border:1px solid #43536c;background:#111b2b;border-radius:13px;padding:14px;
      box-shadow:0 12px 36px #000b;margin-bottom:7px;}
    #${id} .fleet-row {display:flex;gap:10px;justify-content:space-between;margin:6px 0;align-items:center;}
    #${id} .fleet-num {font-variant-numeric:tabular-nums;font-weight:700;white-space:nowrap;}
    #${id} .fleet-muted {color:#a8b7cf;font-size:12px;}
    #${id} .fleet-sub {border-top:1px solid #35445a;padding-top:8px;margin-top:10px;}
    #${id} .fleet-title {font-size:15px;font-weight:650;margin-bottom:6px;}
    #${id} .fleet-agent {padding:4px 0;border-bottom:1px dotted #394960;}
    #${id} .fleet-warn {color:#f2c078;}
    #${id} .fleet-good {color:#91dbbb;}
    #${id} .fleet-small {font-size:11px;}
    @media(max-width:600px){ #${id}{bottom:10px;right:10px;}#${id} .fleet-panel{max-height:60vh;} }
  `;
  document.head.appendChild(style);
  const host = document.createElement("aside");
  host.id = id;
  host.setAttribute("aria-label", "Состояние флота Cezar");
  const panel = document.createElement("div");
  panel.className = "fleet-panel";
  panel.hidden = true;
  panel.setAttribute("role", "region");
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.textContent = "Флот Cezar · загрузка";
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-controls", "cezar-fleet-r01-body");
  panel.id = "cezar-fleet-r01-body";
  host.append(panel, toggle);
  document.body.appendChild(host);

  let last = null;
  let pending = false;
  function el(tag, value, cls) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    node.textContent = String(value);
    return node;
  }
  function row(label, value, cls) {
    const wrap = document.createElement("div");
    wrap.className = "fleet-row";
    wrap.append(el("span", label), el("span", value, "fleet-num "+(cls||"")));
    panel.appendChild(wrap);
  }
  function clock(iso) {
    if (!iso) return "нет данных";
    const t = new Date(iso);
    if (Number.isNaN(t.getTime())) return "нет данных";
    return new Intl.DateTimeFormat("ru-RU",{
      timeZone:"Europe/Kyiv",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"
    }).format(t);
  }
  function render(d) {
    panel.replaceChildren();
    if (!d || d.schema !== "cezar.fleet.visibility.v1") {
      toggle.textContent = "Флот Cezar · нет данных";
      panel.appendChild(el("p","Нет подтверждённой телеметрии; показатели не приравниваются к нулю.","fleet-warn"));
      return;
    }
    const s=d.schedules || {}, m=d.models || {}, c=d.cezar || {}, g=d.github || {};
    const exists = typeof s.enabled === "number", ready = s.snapshot_fresh === true;
    const configured=exists?s.enabled:"?";
    toggle.textContent="Флот · "+configured+" расписаний · "+(typeof m.inference_busy_known==="number"?m.inference_busy_known:"?")+" занято";
    panel.appendChild(el("div","Флот Mojum / Cezar","fleet-title"));
    panel.appendChild(el("p","Раздельный учёт. «Активных: 0» в Cezar означает только его собственные задачи.","fleet-muted"));
    row("Cezar выполняет", typeof c.active==="number"?c.active:"неизвестно");
    row("Расписания ChatGPT",exists?configured:"неизвестно",ready?"fleet-good":"fleet-warn");
    if (exists) row("Разработчики / QA",s.developers_enabled+" / "+s.qa_enabled);
    row("Запускались за 90 мин",typeof s.ran_last_90m==="number"?s.ran_last_90m:"нет данных");
    row("Модели заняты",typeof m.capacity_slots_known==="number"
      ?m.inference_busy_known+" / "+m.capacity_slots_known+(m.capacity_complete?"":" (частично)"):"неизвестно");
    row("Открытые PR GitHub",typeof g.open_prs==="number"?g.open_prs:"неизвестно");
    row("Внешние прямо сейчас","неизвестно","fleet-warn");
    panel.appendChild(el("p",
      "Для подсчёта выполняющихся внешних агентов нужен heartbeat от их исполнителей. Расписания и PR не являются таким подтверждением.",
      "fleet-muted fleet-sub"));
    const agents=Array.isArray(s.agents)?s.agents:[];
    if(agents.length) {
      panel.appendChild(el("div","Зарегистрированные агенты","fleet-title fleet-sub"));
      for(const a of agents) {
        const item=document.createElement("div");
        item.className="fleet-agent";
        item.appendChild(el("div",a.name||"неизвестный агент"));
        const lastLabel={completed:"завершён",in_progress:"выполнялся",failed:"ошибка",no_update:"нет результата"}[a.last_status]||"статус неизвестен";
        item.appendChild(el("div","Последний запуск: "+clock(a.last_run)+" ("+lastLabel+") · Следующий: "+clock(a.next_run),
          "fleet-muted fleet-small"));
        panel.appendChild(item);
      }
    }
    panel.appendChild(el("p","Источник расписаний: "+clock(s.observed_at)+
      (ready?"":" · снимок устарел")+
      ". Обновление серверной телеметрии: "+clock(d.generated_at)+".","fleet-muted fleet-sub"));
  }
  toggle.addEventListener("click", () => {
    panel.hidden=!panel.hidden;
    toggle.setAttribute("aria-expanded",String(!panel.hidden));
    if (!panel.hidden) poll();
  });
  async function poll() {
    if (pending) return;
    pending=true;
    try {
      const url="/assets/cezar-fleet-r01.json?_="+Date.now();
      const resp=await fetch(url,{cache:"no-store",credentials:"same-origin",redirect:"error",
        signal:AbortSignal.timeout(10000)});
      if(!resp.ok)throw Error("HTTP "+resp.status);
      const data=await resp.json();
      if(!data || data.schema!=="cezar.fleet.visibility.v1")throw Error("schema");
      last=data;
      render(data);
    } catch (_) {
      if (!last) render(null);
      else {
        render(last);
        panel.prepend(el("p","Нет свежей связи с телеметрией. Показан последний снимок.","fleet-warn"));
      }
    } finally {pending=false;}
  }
  poll();
  setInterval(() => { if (!document.hidden) poll(); }, 60000);
  document.addEventListener("visibilitychange", () => { if(!document.hidden)poll(); });
})();
