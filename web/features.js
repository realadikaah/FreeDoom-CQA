/* FreeDoom Quality Intelligence: new presentation views. No changes to official scoring. */
function dashboardV6(){
  const all=state.conversations||[];
  const done=all.filter(c=>c.final_score!=null);
  const mean=done.length?done.reduce((s,c)=>s+Number(c.final_score),0)/done.length:null;
  const pending=(state.appeals||[]).filter(a=>a.status==='pending').length;
  const languages=['RU','KZ','EN'].map(k=>({key:k,n:all.filter(x=>x.language===k).length}));
  const bars=languages.map(x=>`<div class="lang-meter"><div class="lang-meter-top"><span><i class="lang-dot lang-${x.key.toLowerCase()}"></i>${x.key==='RU'?'Русский':x.key==='KZ'?'Қазақша':'English'}</span><strong>${x.n} диалогов</strong></div><div class="progress-track"><div class="progress-fill lang-fill-${x.key.toLowerCase()}" style="width:${all.length?Math.round(x.n/all.length*100):0}%"></div></div></div>`).join('');
  $('#content').innerHTML=`<div class="dashboard-intro"><div class="eyebrow">КАЧЕСТВО КЛИЕНТСКОГО СЕРВИСА</div><h1>Контроль качества.<br><span>Новый уровень.</span></h1><p>FreeDoom превращает анализ диалогов в понятные решения для команды поддержки: от оценки и проверки доказательств до предотвращения повторных ошибок.</p><div class="actions">${btn('Смотреть диалоги →','go-conversations','primary')}${btn('Центр проблем','go-insights','secondary')}</div><div class="intro-glow" aria-hidden="true"><div class="intro-orbit orbit-one"></div><div class="intro-orbit orbit-two"></div><div class="intro-core">F</div></div></div>
  <div class="section-kicker">ПОКАЗАТЕЛИ КАЧЕСТВА <span class="dash-live"><i></i> По сохранённым оценкам</span></div>
  <div class="grid-stats stats-v6">${[
    ['Диалогов загружено',all.length,'↗','DEV + PROD'],
    ['Оценено',done.length,'◎',`${all.length?Math.round(done.length/all.length*100):0}% от всех записей`],
    ['Средний балл',score(mean),'◉','По закреплённым результатам'],
    ['Апелляций в работе',pending,'⇄','Ожидают проверки']
  ].map(([label,value,ico,meta])=>`<div class="stat"><div class="stat-top">${label}<span class="stat-icon">${ico}</span></div><b>${value}</b><div class="foot">${meta}</div></div>`).join('')}</div>
  <div class="section-kicker" style="margin-top:30px">ДВА НОВЫХ ИНСТРУМЕНТА <span class="section-caption">Помощь оператору и руководителю</span></div>
  <div class="feature-grid"><button type="button" class="feature-tile feature-coach" data-feature="open-coach"><div class="feature-icon">✧</div><div class="feature-tag">ПРЕДОТВРАЩЕНИЕ</div><h2>AI Coach</h2><p>Проверьте черновик ответа до отправки клиенту. Найдите риски, получите объяснение и безопасную альтернативу.</p><span class="feature-link">Перейти к помощнику <b>↗</b></span></button><button type="button" class="feature-tile feature-insights" data-feature="open-insights"><div class="feature-icon">◈</div><div class="feature-tag">СИСТЕМНЫЙ АНАЛИЗ</div><h2>Root Cause Analysis</h2><p>Увидьте повторяющиеся слабые места в реальных оценках и примеры диалогов, на которые стоит обратить внимание.</p><span class="feature-link">Посмотреть закономерности <b>↗</b></span></button></div>
  <div class="layout-2 dashboard-bottom"><section class="panel"><div class="panel-header"><div><div class="micro-eyebrow">ДАННЫЕ</div><h2>Языки обращений</h2><p>Входящий набор диалогов</p></div><span class="panel-icon">◷</span></div><div class="panel-body">${bars}</div></section>
  <section class="panel"><div class="panel-header"><div><div class="micro-eyebrow">ПРОЦЕСС</div><h2>От анализа к улучшению</h2><p>Цикл контроля качества</p></div><span class="panel-icon">↗</span></div><div class="panel-body"><div class="process-item"><b>01</b><div><strong>Оценить</strong><span>8 критериев и точные реплики</span></div></div><div class="process-item"><b>02</b><div><strong>Проверить</strong><span>Аудит и апелляции с историей</span></div></div><div class="process-item"><b>03</b><div><strong>Улучшить</strong><span>AI Coach и повторяющиеся причины</span></div></div></div></section></div>
  <div class="dashboard-disclaimer">Данные на панели — только из загруженных диалогов и сохранённых оценок. Новые инструменты не меняют официальный итог и не отправляют ответы клиенту.</div>`;
}

async function renderInsightsV6(){
  const group=state.insightGroup||'DEV';
  $('#content').innerHTML=head('Центр проблем','Повторяющиеся паттерны на основании сохранённых оценок',`<select class="form-control" id="insightGroup"><option value="DEV">DEV · разработка</option><option value="PROD">PROD · контроль</option></select>${btn('Открыть аудит','go-audit')}`)+`<div id="insightContent" class="loading-v6">Собираем закономерности по сохранённым оценкам…</div>`;
  $('#insightGroup').value=group;
  $('#insightGroup').onchange=e=>{state.insightGroup=e.target.value;state.insightSelected=null;renderInsightsV6()};
  try {state.insightData=await api('/insights/root-causes?group_id='+encodeURIComponent(group));drawInsightsV6()}
  catch(e){$('#insightContent').innerHTML=`<div class="note warning">Не удалось собрать аналитику: ${esc(e.message)}</div>`}
}
function drawInsightsV6(){
  if(state.page!=='insights')return;
  const d=state.insightData;
  if(!d)return;
  const patterns=d.patterns||[];
  if(!patterns.some(x=>x.id===state.insightSelected))state.insightSelected=patterns[0]?.id||null;
  const current=patterns.find(x=>x.id===state.insightSelected);
  const percent=d.scored?Math.round(d.with_issues/d.scored*100):0;
  $('#insightContent').innerHTML=`
  <div class="insight-lead"><div class="lead-icon">◈</div><div><div class="eyebrow">ROOT CAUSE ANALYSIS</div><h2>От отдельных ошибок — к повторяющимся закономерностям.</h2><p>Выберите проблему, чтобы увидеть конкретные доказательства и управленческое действие. Отчёт создаётся локально, без вызовов Gemini.</p></div><span class="soft-chip">0 API-вызовов</span></div>
  <div class="grid-stats insight-stats"><div class="stat"><div class="stat-top">Всего диалогов <span class="stat-icon">▤</span></div><b>${d.total}</b><div class="foot">Набор ${esc(d.group_id)}</div></div>
  <div class="stat"><div class="stat-top">С оценкой <span class="stat-icon">◉</span></div><b>${d.scored}</b><div class="foot">Закреплённая ИИ-оценка</div></div>
  <div class="stat"><div class="stat-top">С отклонениями <span class="stat-icon">⌁</span></div><b>${d.with_issues}</b><div class="foot">${percent}% оценённых диалогов</div></div>
  <div class="stat"><div class="stat-top">Типов проблем <span class="stat-icon">◈</span></div><b>${patterns.length}</b><div class="foot">С ненулевой встречаемостью</div></div></div>
  ${patterns.length?`<div class="insights-layout"><section class="panel pattern-panel"><div class="panel-header"><div><div class="micro-eyebrow">РАСПРЕДЕЛЕНИЕ</div><h2>Повторяющиеся проблемы</h2><p>Нажмите, чтобы изучить примеры</p></div></div><div class="pattern-list">${patterns.map(p=>`<button class="pattern-item ${p.id===state.insightSelected?'is-active':''}" data-feature="select-pattern" data-pattern="${esc(p.id)}"><div class="pattern-top"><strong>${esc(p.title)}</strong><span>${p.count}</span></div><div class="pattern-meter"><i style="width:${Math.min(100,p.share)}%"></i></div><div class="pattern-bottom"><span>${p.share}% применимых оценок</span>${p.category==='risk'?'<em>Приоритетный риск</em>':''}</div></button>`).join('')}</div></section>
  <section class="panel pattern-detail"><div class="panel-header"><div><div class="micro-eyebrow">РАЗБОР</div><h2>${esc(current.title)}</h2><p>${esc(current.description)}</p></div><span class="pill ${current.category==='risk'?'warning':'neutral'}">${current.category==='risk'?'Приоритет':'Качество'}</span></div><div class="panel-body"><div class="cause-count"><strong>${current.count}</strong><span>случаев в ${current.applicable} применимых оценках</span></div>
  <div class="suggestion-box"><span class="suggestion-tag">✧ РЕКОМЕНДАЦИЯ РУКОВОДИТЕЛЮ</span><p>${esc(current.recommendation)}</p></div><div class="detail-subtitle">Примеры с доказательствами</div>
  <div class="cause-examples">${current.examples.map(e=>`<div class="cause-example"><div class="cause-example-head"><div><strong>${esc(e.conversation_id)}</strong><span>${esc(e.language)} · значение: ${e.value===null?'N/A':e.value===true?'Да':e.value===false?'Нет':esc(e.value)}</span></div><button class="inline-link" data-feature="open-case" data-cid="${esc(e.conversation_id)}">К диалогу ↗</button></div><p class="cause-rationale">${esc(e.rationale)}</p>${e.evidence.map(t=>`<blockquote class="cause-quote"><span>Реплика #${t.number} · ${esc(t.speaker)} · ${esc(t.timestamp)}</span>${esc(t.text)}</blockquote>`).join('')}</div>`).join('')}</div>
  </div></section></div>`:`<div class="panel panel-body empty-state">${d.scored?'По сохранённым оценкам отклонений не найдено.':'Сохранённых ИИ-оценок пока нет. Загрузите диалоги и запустите оценку.'}</div>`}
  <div class="methodology-note"><b>Что означает этот отчёт?</b> ${esc(d.methodology)} Одна переписка может попасть сразу в несколько категорий. Рекомендации не заменяют проверку регламента компании.</div>`;
}

function coachSelectedRecord(){
  const choices=state.conversations.filter(x=>x.group_id===(state.coachGroup||'DEV'));
  return choices.find(x=>x.conversation_id===state.coachConversationId)||choices[0]||null;
}
async function renderCoachV6(){
  if(!state.coachGroup)state.coachGroup=state.selected?.group_id||'DEV';
  if(!state.coachConversationId)state.coachConversationId=state.selected?.conversation_id||'';
  const rec=coachSelectedRecord();
  if(rec)state.coachConversationId=rec.conversation_id;
  $('#content').innerHTML=head('AI Coach','Предотвращение ошибок до отправки ответа клиенту',`<button class="btn secondary" data-feature="coach-example">↗ Загрузить пример</button><span class="soft-chip">Черновик · не отправляется клиенту</span>`)+`<div class="coach-intro"><div class="coach-orb">✧</div><div><div class="eyebrow">AI-ASSISTED QUALITY</div><h2>Хороший сервис начинается<br>до нажатия «Отправить».</h2><p>Проверьте черновик оператора по правилам качества. Найдите возможные риски и получите более точную формулировку.</p></div></div>
  <div class="coach-flow"><span><b>01</b> Выбрать диалог</span><i>→</i><span><b>02</b> Написать черновик</span><i>→</i><span><b>03</b> Проверить и улучшить</span></div>
  <div class="coach-layout"><section class="panel coach-form-panel"><div class="panel-header"><div><div class="micro-eyebrow">КОНТЕКСТ</div><h2>Выбранный разговор</h2><p>Последние реплики клиента и оператора</p></div></div><div class="panel-body"><div class="coach-filters"><label class="field-label" for="coachGroup">Набор</label><select class="form-control" id="coachGroup"><option>DEV</option><option>PROD</option></select><label class="field-label" for="coachDialog">Диалог</label><select class="form-control" id="coachDialog">${state.conversations.filter(x=>x.group_id===state.coachGroup).map(x=>`<option value="${esc(x.conversation_id)}" ${x.conversation_id===state.coachConversationId?'selected':''}>${esc(x.conversation_id)} · ${esc(x.language)}</option>`).join('')}</select></div><div id="coachContext" class="coach-context">${rec?'Загрузка…':'В выбранном наборе нет диалогов.'}</div>
  <div class="coach-compose"><label for="coachDraft">Черновик ответа оператора</label><textarea id="coachDraft" class="form-control" rows="6" maxlength="3000" placeholder="Напишите, что оператор собирается ответить клиенту…">${esc(state.coachDraft||'')}</textarea><div class="coach-char"><span>Ничего не отправляется клиенту автоматически</span><span id="coachCharCount">${(state.coachDraft||'').length}/3000</span></div></div>
  <label class="coach-consent"><input type="checkbox" id="coachConsent"> <span>Разрешаю передачу выбранной переписки и черновика внешней модели Gemini для анализа.</span></label>
  <div class="coach-actions"><button class="btn secondary" data-feature="run-coach-local">Экспресс-проверка · 0 API</button><button class="btn primary" data-feature="run-coach-ai">✧ Анализ с Gemini</button></div></div></section>
  <section class="panel coach-output-panel"><div class="panel-header"><div><div class="micro-eyebrow">РЕЗУЛЬТАТ</div><h2>Рекомендации помощника</h2><p>Подсказки, а не официальная оценка</p></div><span class="pill neutral" id="coachSourceChip">Ожидает</span></div><div class="panel-body" id="coachOutput"><div class="coach-empty"><div>✧</div><strong>Готов проверить ваш ответ</strong><p>Выберите диалог и напишите черновик. Экспресс-проверка использует только локальные правила, а полноценный анализ — Gemini.</p></div></div></section></div>
  <div class="methodology-note"><b>Важно.</b> AI Coach — дополнительный режим обучения. Он не влияет на официальные 8 критериев, экспорт для жюри и апелляции. Перед использованием подсказки сотрудник обязан проверить факты и регламенты.</div>`;
  $('#coachGroup').value=state.coachGroup;
  $('#coachGroup').onchange=e=>{state.coachGroup=e.target.value;state.coachConversationId='';state.coachResult=null;renderCoachV6()};
  $('#coachDialog').onchange=e=>{state.coachConversationId=e.target.value;state.coachResult=null;loadCoachContext()};
  $('#coachDraft').oninput=e=>{state.coachDraft=e.target.value;state.coachResult=null;$('#coachCharCount').textContent=e.target.value.length+'/3000';$('#coachSourceChip').textContent='Ожидает';$('#coachOutput').innerHTML='<div class="coach-empty"><div>✧</div><strong>Черновик изменён</strong><p>Запустите проверку, чтобы получить актуальные рекомендации для нового текста.</p></div>'};
  await loadCoachContext();
  if(state.coachResult)drawCoachOutput(state.coachResult);
}
async function loadCoachContext(){
  const rec=coachSelectedRecord();const root=$('#coachContext');if(!root||!rec)return;
  try{
    const full=await api('/conversations/'+encodeURIComponent(rec.conversation_id));
    if(state.page!=='coach'||state.coachConversationId!==rec.conversation_id)return;
    root.innerHTML=full.transcript.slice(-7).map((t,i)=>`<div class="coach-message ${esc(t.speaker)}"><span>${esc(t.speaker==='client'?'Клиент':t.speaker==='operator'?'Оператор':t.speaker)} · ${esc(t.timestamp)} · #${full.transcript.length-Math.min(full.transcript.length,7)+i+1}</span><p>${esc(t.text)}</p></div>`).join('')||'<div class="empty-state">Нет реплик</div>';
  }catch(e){root.textContent='Не удалось прочитать диалог: '+e.message}
}
function drawCoachOutput(d){
  const root=$('#coachOutput');if(!root)return;
  const levels={low:'Низкий сигнал',medium:'Есть замечания',high:'Возможный высокий риск'};
  $('#coachSourceChip').textContent=d.mode==='ai'?'Gemini · внешняя модель':'Экспресс · локальные правила';
  root.innerHTML=`<div class="coach-result"><div class="coach-risk risk-${esc(d.risk_level)}"><span>РЕЗУЛЬТАТ АНАЛИЗА</span><strong>${levels[d.risk_level]||'Проверить'}</strong><p>${esc(d.summary)}</p></div>${d.issues?.length?`<div class="coach-result-title">Что стоит проверить · ${d.issues.length}</div>${d.issues.map(issue=>`<div class="coach-issue"><div><span class="risk-pin pin-${esc(issue.severity)}"></span><strong>${esc(issue.title)}</strong></div><p>${esc(issue.explanation)}</p>${issue.evidence_turns?.length?`<small>Контекст: реплики ${issue.evidence_turns.map(x=>'#'+x).join(', ')}</small>`:''}</div>`).join('')}`:'<div class="note">Явных признаков риска по выбранному режиму не найдено. Это не гарантирует корректность ответа.</div>'}
  ${d.suggested_reply?`<div class="coach-suggestion"><div><span class="suggestion-tag">✧ ПРЕДЛОЖЕНИЕ ИИ</span><button type="button" class="inline-link" data-feature="copy-suggestion">Копировать</button></div><p id="coachSuggestedText">${esc(d.suggested_reply)}</p></div>`:d.mode==='local'?'<div class="coach-suggestion"><span class="suggestion-tag">ТОЛЬКО ПРОВЕРКА ПРАВИЛ</span><p>Для индивидуальной формулировки улучшенного ответа включите анализ Gemini. Экспресс-проверка намеренно не генерирует ответы.</p></div>':''}
  <div class="coach-result-footer">${d.mode==='local'?'Без отправки данных третьим сторонам.':'Транскрипт и черновик обработаны внешним API Gemini.'} Итоговая оценка диалога не изменена.</div></div>`;
}
async function executeCoach(mode){
  const rec=coachSelectedRecord();if(!rec)return notify('Выберите диалог',true);
  const draft=$('#coachDraft')?.value?.trim();if(!draft)return notify('Сначала напишите черновик ответа',true);
  const consent=$('#coachConsent')?.checked||false;
  if(mode==='ai'&&!consent)return notify('Подтвердите передачу данных Gemini',true);
  const root=$('#coachOutput');root.innerHTML='<div class="coach-empty"><div class="coach-loader">✧</div><strong>Проверяем черновик…</strong><p>Ищем риски и сверяем ответ с контекстом.</p></div>';
  document.querySelectorAll('[data-feature^="run-coach-"]').forEach(x=>x.disabled=true);
  try {const d=await post('/coach/analyze',{conversation_id:rec.conversation_id,draft,mode,acknowledged_external:consent});state.coachResult=d;drawCoachOutput(d)}
  catch(e){root.innerHTML=`<div class="note warning">${esc(e.message)}</div>`;notify(e.message,true)}
  finally{document.querySelectorAll('[data-feature^="run-coach-"]').forEach(x=>x.disabled=false)}
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-feature]');if(!b)return;
  const action=b.dataset.feature;
  if(action==='open-coach'){state.coachConversationId=state.selected?.conversation_id||'';return nav('coach')}
  if(action==='open-insights')return nav('insights');
  if(action==='coach-example'){state.coachGroup='DEV';state.coachConversationId='p1-ru-003';state.coachDraft='Название тарифа смотрите там же. Хорошего дня!';state.coachResult=null;return renderCoachV6()}
  if(action==='select-pattern'){state.insightSelected=b.dataset.pattern;return drawInsightsV6()}
  if(action==='open-case')return openConversation(b.dataset.cid);
  if(action==='run-coach-local')return executeCoach('local');
  if(action==='run-coach-ai')return executeCoach('ai');
  if(action==='copy-suggestion'){
    try{await navigator.clipboard.writeText(state.coachResult?.suggested_reply||'');notify('Черновик скопирован')}
    catch(_){notify('Не удалось скопировать; выделите текст вручную',true)}
  }
});
