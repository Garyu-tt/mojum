/* Cezar Fleet visibility. Read-only, no agent/PR writes, no untrusted HTML. */
(() => {
  "use strict";
  if (window.__cezarFleetR01) return;
  window.__cezarFleetR01 = true;
  const id = "cezar-fleet-r01";
  const style = document.createElement("style");
  style.textContent = `
    #${id} { position: fixed; z-index: 2147480000; bottom: 16px; right: 16px;
      color: #eef3fa; font: 13px/1.45 system-ui,-apple-system,sans-serif; max-width: min(560px,calc(100vw - 24px)); }
    #${id} button { border:1px solid #465877; background:#182234; color:#ecf3fb; border-radius:11px;
      cursor:pointer; padding:11px 15px; font: inherit; font-weight:650; box-shadow:0 2px 14px #0007; }
    #${id} button:focus-visible {outline:2px solid #75baff;outline-offset:2px;}
    #${id} .fleet-panel {width:min(540px,calc(100vw - 28px));max-height:min(78vh,760px);overflow:auto;
      border:1px solid #43536c;background:#111b2b;border-radius:13px;padding:14px;
      box-shadow:0 12px 36px #000b;margin-bottom:7px;}
    #${id} .fleet-row {display:flex;gap:10px;justify-content:space-between;margin:6px 0;align-items:center;}
    #${id} .fleet-num {font-variant-numeric:tabular-nums;font-weight:700;white-space:nowrap;}
    #${id} .fleet-muted {color:#a8b7cf;font-size:12px;}
    #${id} .fleet-sub {border-top:1px solid #35445a;padding-top:8px;margin-top:10px;}
    #${id} .fleet-title {font-size:15px;font-weight:650;margin-bottom:6px;}
    #${id} .fleet-agent {padding:6px 0;border-bottom:1px dotted #394960;}
    #${id} a {color:#afd1ff;text-decoration:underline;text-underline-offset:2px;}
    #${id} a:focus-visible{outline:2px solid #75baff;}
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
  panel.hidden = false;
  panel.setAttribute("role", "region");
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.textContent = "Mojum · синхронизация: загрузка";
  toggle.setAttribute("aria-expanded", "true");
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
  function elapsed(iso) {
    const t=Date.parse(iso||"");
    if(!Number.isFinite(t)) return "время неизвестно";
    const m=Math.max(0,Math.floor((Date.now()-t)/60000));
    return m<1?"только что":m<60?m+" мин назад":
      m<1440?Math.floor(m/60)+" ч "+(m%60)+" мин назад":
      Math.floor(m/1440)+" дн назад";
  }
  function render(d) {
    panel.replaceChildren();
    if (!d || d.schema !== "cezar.fleet.visibility.v1") {
      toggle.textContent = "Флот Cezar · нет данных";
      panel.appendChild(el("p","Нет подтверждённой телеметрии; показатели не приравниваются к нулю.","fleet-warn"));
      return;
    }
    const s=d.schedules || {}, m=d.models || {}, c=d.cezar || {}, g=d.github || {}, h=d.github_history || {};
    const exists = typeof s.enabled === "number", ready = s.snapshot_fresh === true;
    const configured=exists?s.enabled:"?";
    toggle.textContent="Mojum · "+configured+" расписаний · "+(typeof g.open_prs==="number"?g.open_prs:"?")+" PR · "+(typeof c.active==="number"?c.active:"?")+" задач Cezar активны";
    panel.appendChild(el("div","Mojum: единая сводка источников","fleet-title"));
    panel.appendChild(el("p","Раздельный учёт. «Активных: 0» в Cezar означает только его собственные задачи.","fleet-muted"));
    row("Cezar выполняет", typeof c.active==="number"?c.active:"неизвестно");
    row("История Cezar (не весь Mojum)",typeof c.total==="number"?c.total:"неизвестно");
    row("Расписания ChatGPT",exists?configured:"неизвестно",ready?"fleet-good":"fleet-warn");
    if (exists) row("Разработчики / QA",s.developers_enabled+" / "+s.qa_enabled);
    row("Запускались за 90 мин",typeof s.ran_last_90m==="number"?s.ran_last_90m:"нет данных");
    row("Модели заняты",typeof m.capacity_slots_known==="number"
      ?m.inference_busy_known+" / "+m.capacity_slots_known+(m.capacity_complete?"":" (частично)"):"неизвестно");
    row("Открытые PR GitHub",typeof g.open_prs==="number"?g.open_prs:"неизвестно");
    const recent=Array.isArray(g.latest_open_prs)?g.latest_open_prs:[];
    const cached=g.fallback===true;
    const dateKey=cached?"updated_at":"created_at";
    const newest=recent.filter(x=>x&&x[dateKey]&&Number.isFinite(Date.parse(x[dateKey])))
      .sort((a,b)=>Date.parse(b[dateKey])-Date.parse(a[dateKey]))[0];
    if(cached)panel.appendChild(el("p","GitHub: отображается последний локальный снимок, а не прямая связь. Данные от "+clock(g.observed_at),"fleet-warn fleet-small"));
    if(newest){
      row(cached?"Последнее изменение PR (кеш)":"Последний созданный PR",
        "#"+newest.number+" · "+elapsed(newest[dateKey]));
      if((Date.now()-Date.parse(newest[dateKey]))>35*60000)
        panel.appendChild(el("p",cached?
          "В локальном снимке нет более свежих изменений PR; создание новых PR отсюда не подтверждается.":
          "Новых PR не видно "+elapsed(newest[dateKey])+
          ". Публикации могли остановиться, но это не доказывает остановку внешних агентов.","fleet-warn fleet-small"));
    }else row("Последняя активность PR","нет данных","fleet-warn");

    row("Открытые Issues GitHub",typeof g.open_issues==="number"?g.open_issues:"неизвестно");
    row("Merged в последних закрытых PR",typeof h.merged_in_visible_sample==="number"?h.merged_in_visible_sample:"неизвестно",h.snapshot_fresh===false?"fleet-warn":"");
    row("Внешние прямо сейчас","неизвестно","fleet-warn");
    panel.appendChild(el("p",
      "Внешние ChatGPT-агенты отдельно от Cezar. Для них видны последние запуски и GitHub-артефакты; живой heartbeat пока не поступает.",
      "fleet-muted fleet-sub"));
    // GitHub is the repository's task/artifact source of truth. These are
    // links to real records, never synthetic Cezar runs or confirmed workers.
    function publicRecords(label, records, kind, dateField) {
      if (!Array.isArray(records) || !records.length) return;
      panel.appendChild(el("div",label,"fleet-title fleet-sub"));
      for (const entry of records.slice(0,12)) {
        if (!entry || !Number.isSafeInteger(entry.number) || entry.number < 1) continue;
        const item=document.createElement("div");
        item.className="fleet-agent";
        const link=document.createElement("a");
        link.href="https://github.com/Garyu-tt/mojum/"+(kind==="issue"?"issues/":"pull/")+entry.number;
        link.target="_blank";
        link.rel="noopener noreferrer";
        link.textContent="#"+entry.number+" "+String(entry.title||"").slice(0,160);
        item.appendChild(link);
        const stage=kind==="issue"?"Issue":"PR";
        const state=entry.outcome==="merged"?" · слит":
          entry.outcome==="closed-unmerged"?" · закрыт без слияния":
          entry.draft===true?" · черновик":" · открыт";
        item.appendChild(el("div",stage+state+" · "+clock(entry[dateField]),
          "fleet-muted fleet-small"));
        panel.appendChild(item);
      }
    }
    publicRecords(cached?"Недавно обновлённые PR (кеш)":"Новые открытые PR (GitHub)",
      g.latest_open_prs,"pr",cached?"updated_at":"created_at");
    publicRecords("Открытые Issues (GitHub)",g.latest_open_issues,"issue",cached?"updated_at":"created_at");
    if(h.available===true) {
      publicRecords("Недавние закрытые / слитые PR (GitHub)",h.recent,"pr","updated_at");
      panel.appendChild(el("p","История: "+clock(h.observed_at)+
        (h.snapshot_fresh===true?"":" · кеш устарел")+
        "; только последние 20 закрытых PR, не вся история задач.",
        "fleet-muted"));
    } else {
      panel.appendChild(el("p","История слияний GitHub недоступна; это не ноль выполненных задач.",
        "fleet-warn fleet-sub"));
    }
    const qa=d.qa_fleet||{};
    if(qa.available===true && Array.isArray(qa.workers)){
      panel.appendChild(el("div","CPU QA: 17 локальных слотов","fleet-title fleet-sub"));
      const active=(qa.snapshot_fresh===true && Number.isSafeInteger(qa.active_requests))?
        String(qa.active_requests):"неизвестно (устарело)";
      row("Модельные запросы выполняются",active,qa.snapshot_fresh===true?"fleet-good":"fleet-warn");
      row("Задания в текущем цикле",typeof qa.assigned==="number"?qa.assigned:"?");
      row("Завершены / ошибки",String(qa.completed_tasks??"?")+" / "+String(qa.failed_tasks??"?"));
      panel.appendChild(el("p","Это действительные запросы к локальным моделям, но не одобренные pull request. GitHub-слияния не выполняются.","fleet-muted"));
      for(const v of qa.workers){
        if(!v || !Number.isSafeInteger(v.slot) || v.slot<0 || v.slot>=17)continue;
        if(v.pr==null && v.state==="queued")continue;
        const entry=document.createElement("div");entry.className="fleet-agent";
        const state={queued:"в очереди",fetching_patch:"получает diff",inflight:"ожидает ответ модели",
          completed:"ответ получен",failed:"ошибка"}[v.state]||"неизвестно";
        entry.appendChild(el("div","QA слот "+(v.slot+1)+" · модель "+v.port+" · "+state));
        if(Number.isSafeInteger(v.pr)&&v.pr>0){
          const link=document.createElement("a");
          link.href="https://github.com/Garyu-tt/mojum/pull/"+v.pr;
          link.rel="noopener noreferrer";link.target="_blank";
          link.textContent="PR #"+v.pr;
          entry.appendChild(link);
        }
        if(v.summary)entry.appendChild(el("div",String(v.summary).slice(0,350),"fleet-muted fleet-small"));
        panel.appendChild(entry);
      }
      panel.appendChild(el("p","Последнее измерение: "+clock(qa.generated_at)+
        (qa.snapshot_fresh===true?"":" · устарело"),"fleet-muted fleet-small"));
    }else{
      panel.appendChild(el("p","Локальная QA-фабрика: нет проверенных данных.","fleet-warn fleet-sub"));
    }
    const receipts=d.reports||{};
    if(receipts.available===true && Array.isArray(receipts.agents)){
      row("Последние отчёты исполнителей",receipts.agents.length);
      panel.appendChild(el("div","Что сообщали агенты · не live heartbeat","fleet-title fleet-sub"));
      if(!receipts.agents.length)
        panel.appendChild(el("p","Пока нет отчётов исполнителей.","fleet-muted"));
      for(const rep of receipts.agents.slice(0,10)){
        if(!rep||typeof rep.agent!=="string") continue;
        const name={peer1:"Разработчик 1",peer2:"Разработчик 2",
          peer3:"Разработчик 3",peer4:"Разработчик 4",qa:"Независимая QA"}[rep.agent]||"Агент";
        const phase={observed:"подтверждён прошлый результат",started:"сообщил о старте",
          progress:"сообщил о прогрессе",completed:"сообщил о завершении",
          blocked:"сообщил о блокере",failed:"сообщил об ошибке"}[rep.phase]||"неизвестно";
        const wrap=document.createElement("div");wrap.className="fleet-agent";
        wrap.appendChild(el("div",name+" · "+phase+" · "+elapsed(rep.reported_at)));
        wrap.appendChild(el("div",String(rep.note||"").slice(0,240),"fleet-muted"));
        if(Number.isSafeInteger(rep.pr)&&rep.pr>0){
          const link=document.createElement("a");
          link.href="https://github.com/Garyu-tt/mojum/pull/"+rep.pr;
          link.rel="noopener noreferrer";link.target="_blank";
          link.textContent="GitHub PR #"+rep.pr;
          wrap.appendChild(link);
        }
        wrap.appendChild(el("div",rep.source==="owner-observation"?
          "Источник: проверка ChatGPT-автоматизации":"Источник: самоотчёт исполнителя",
          "fleet-muted fleet-small"));
        panel.appendChild(wrap);
      }
    } else {
      panel.appendChild(el("p","Отчёты исполнителей недоступны.","fleet-warn fleet-sub"));
    }
    const agents=Array.isArray(s.agents)?s.agents:[];
    if(agents.length) {
      panel.appendChild(el("div","Зарегистрированные агенты","fleet-title fleet-sub"));
      for(const a of agents) {
        const item=document.createElement("div");
        item.className="fleet-agent";
        item.appendChild(el("div",a.name||"неизвестный агент"));
        const lastLabel={completed:"завершён",in_progress:"был запущен, живой статус неизвестен",failed:"ошибка",no_update:"нет результата"}[a.last_status]||"статус неизвестен";
        item.appendChild(el("div","Последний запуск: "+clock(a.last_run)+" · "+elapsed(a.last_run)+" ("+lastLabel+") · Следующий: "+clock(a.next_run),
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
