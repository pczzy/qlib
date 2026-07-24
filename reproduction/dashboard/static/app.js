const $ = (q) => document.querySelector(q);
const fmt = (n, digits = 2) => Number(n).toFixed(digits);
const pct = (n) => `${fmt(Number(n) * 100, 1)}%`;
const chinaTime = (value, withDate = true) => new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: withDate ? "numeric" : undefined,
  month: withDate ? "2-digit" : undefined,
  day: withDate ? "2-digit" : undefined,
  hour: "2-digit", minute: "2-digit", second: "2-digit",
  hour12: false,
}).format(new Date(value));
let DATA;
let REVIEW_KIND="filtered";

function renderKpis(d) {
  const s = d.summary, a = d.audit;
  const items = [
    ["有效模型", a.valid_recorders, `/ ${a.expected_recorders}`],
    ["组合模型", s.selected_model_count, "SELECTED"],
    ["稳健信号", s.filtered_rows, `/ ${s.unfiltered_rows}`],
    ["下载进度", `${s.data_fetch.progress_percent || 100}%`, s.data_fetch.status.toUpperCase()],
  ];
  $("#kpis").innerHTML = items.map(x => `<div class="kpi"><label>${x[0]}</label><strong>${x[1]}</strong><small>${x[2]}</small></div>`).join("");
}

function renderChart(metrics, selected) {
  const groups = {};
  metrics.forEach(m => {
    groups[m.algorithm] ||= {ic: [], rank: []};
    groups[m.algorithm].ic.push(+m.IC);
    groups[m.algorithm].rank.push(+m["Rank IC"]);
  });
  const allRows = Object.entries(groups).map(([name,v]) => ({
    name, ic: v.ic.reduce((a,b)=>a+b,0)/v.ic.length,
    rank:v.rank.reduce((a,b)=>a+b,0)/v.rank.length,
    selected: false,
  }));
  const weightSum = selected.reduce((sum, row) => sum + (+row.weight), 0);
  const portfolio = {
    name: "入选组合",
    ic: weightSum ? selected.reduce((sum, row) => sum + (+row.IC)*(+row.weight), 0) / weightSum : 0,
    rank: weightSum ? selected.reduce((sum, row) => sum + (+row["Rank IC"])*(+row.weight), 0) / weightSum : 0,
    selected: true,
  };
  const rows = [...allRows, portfolio];
  const w=760,h=230,p=34, zero=130, scale=1300, gap=(w-p*2)/rows.length;
  const bars = rows.map((r,i) => {
    const x=p+i*gap+gap*.22, bw=gap*.22;
    const bar=(v,xx,cls) => {
      const hh=Math.abs(v*scale), y=v>=0?zero-hh:zero;
      return `<rect class="${cls}${r.selected?" selected-bar":""}" x="${xx}" y="${y}" width="${bw}" height="${hh}" rx="2"><title>${r.name} ${cls==="bar-rank"?"Rank IC":"IC"}: ${v.toFixed(4)}</title></rect>`;
    };
    const divider = r.selected ? `<line class="portfolio-divider" x1="${x-gap*.18}" y1="45" x2="${x-gap*.18}" y2="195"/>` : "";
    return `${divider}${bar(r.rank,x,"bar-rank")}${bar(r.ic,x+bw+4,"bar-ic")}<text class="${r.selected?"portfolio-label":""}" x="${x}" y="215">${r.name.replace("DoubleEnsemble","D-Ensemble")}</text>`;
  }).join("");
  $("#metricChart").innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="全部模型分算法均值与入选组合加权指标"><line class="axis" x1="${p}" y1="${zero}" x2="${w-p}" y2="${zero}"/>${bars}</svg>`;
  $("#metricSummary").innerHTML=`
    <span><small>左侧口径</small><b>全部 25 个模型 · 分算法简单均值</b></span>
    <span class="portfolio-metric"><small>入选组合 · 加权 Rank IC</small><b>${portfolio.rank >= 0 ? "+" : ""}${portfolio.rank.toFixed(4)}</b></span>
    <span class="portfolio-metric"><small>入选组合 · 加权 IC</small><b>${portfolio.ic >= 0 ? "+" : ""}${portfolio.ic.toFixed(4)}</b></span>`;
}

function renderWeights(rows) {
  $("#weightSum").textContent=`Σ ${fmt(rows.reduce((a,r)=>a+(+r.weight),0),3)}`;
  $("#weights").innerHTML=rows.sort((a,b)=>b.weight-a.weight).map(r => `
    <div class="weight-row"><span title="${r.algorithm}">${r.algorithm}</span>
    <div class="track"><div class="fill" style="width:${+r.weight*100}%"></div></div>
    <code>${pct(r.weight)}</code></div>`).join("");
}

function renderStocks(kind="filtered") {
  $("#stockRows").innerHTML=DATA[kind].map((r,i)=>`<tr>
    <td>${String(i+1).padStart(2,"0")}</td><td>${r.name}</td><td>${r.code}</td>
    <td class="score">${fmt(r.avg_score,4)}</td><td>${pct(r.pos_ratio)}</td>
    <td>${fmt((+r.ROC20-1)*100,1)}%</td><td>${fmt(+r.STD20*100,1)}%</td></tr>`).join("");
}

function renderLineage(s) {
  $("#trainEnd").textContent=s.model_train_end; $("#testEnd").textContent=s.model_test_end;
  $("#dataEnd").textContent=s.prediction_date;
  $("#modelHash").textContent=s.model_data_archive_sha256.slice(0,12)+"…";
  $("#currentHash").textContent=s.current_data_archive_sha256.slice(0,12)+"…";
  const badge=$("#lineageStatus");
  badge.textContent=s.model_uses_current_archive?"同源":"预测已更新";
  badge.className=`status-pill ${s.model_uses_current_archive?"ok":"warn"}`;
}

function renderLiveData(d) {
  const live=d.live_data;
  const sources=live.data_sources;
  const release=$("#releaseTag");
  release.textContent=live.release_tag || "—";
  release.href=live.release_asset_url || "https://github.com/chenditc/investment_data/releases";
  $("#releasePublished").textContent=live.release_published_at ? `${chinaTime(live.release_published_at)} GMT+8` : "—";
  $("#calendarEnd").textContent=live.calendar_end || "—";
  const fetch=$("#fetchStatus");
  fetch.textContent=(live.fetch_status || "UNKNOWN").toUpperCase();
  const failed=["failed","error","superseded"].includes(live.fetch_status);
  fetch.className=`status-pill ${failed?"fail":["ready","already_downloaded"].includes(live.fetch_status)?"ok":"warn"}`;
  $("#githubDataEnd").textContent=sources.github.end || "—";
  $("#githubDataDays").textContent=`${sources.github.trading_days} 个交易日 · Release ${sources.github.release_tag || "—"}`;
  const sina=sources.sina;
  $("#sinaDataRange").textContent=sina.trading_days ? `${sina.start} → ${sina.end}` : "暂无增量";
  $("#sinaDataDays").textContent=sina.trading_days ? `${sina.trading_days} 个交易日 · 自动直连` : "当前完全来自 GitHub";
  $("#sinaDates").innerHTML=sina.dates.length
    ? sina.dates.map(day=>`<span>${day}</span>`).join("")
    : '<span class="empty-source">当前无新浪补充</span>';
  $("#periodRows").innerHTML=live.training_windows.map(w=>`<tr>
    <td><b>${w.horizon_months} 个月</b></td>
    <td>${w.train[0]} <i>→</i> ${w.train[1]}</td>
    <td>${w.valid[0]} <i>→</i> ${w.valid[1]}</td>
    <td>${w.test[0]} <i>→</i> ${w.test[1]}</td>
  </tr>`).join("");
}

function renderEvents(events) {
  $("#events").innerHTML=events.slice(-6).reverse().map(e=>`<div class="event ${["error","failed","failure"].includes((e.level||"").toLowerCase())?"error":""}">
    <time>${chinaTime(e.utc)} GMT+8</time>
    <strong>${e.event.replaceAll("_"," ").toUpperCase()}</strong><p>${e.detail||e.level}</p></div>`).join("");
}

function duration(seconds) {
  const days=Math.floor(seconds/86400);
  const hours=Math.floor((seconds%86400)/3600);
  const minutes=Math.floor((seconds%3600)/60);
  const secs=seconds%60;
  return [days&&`${days}天`, (days||hours)&&`${hours}时`, `${minutes}分`, `${secs}秒`].filter(Boolean).join(" ");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function renderTraining(training) {
  const active=training.active_count;
  const badge=$("#trainingStatus");
  const hasFailure=training.failed_count>0;
  badge.textContent=active ? `${active} 个模型训练中` : hasFailure ? `${training.failed_count} 个异常` : "当前无训练";
  badge.className=`status-pill ${active?"ok":hasFailure?"fail":""}`;
  $("#trainingSummary").textContent=`训练日志口径 · 已完成 ${training.completed_count}/5 · 每分钟自动刷新`;
  const labels={running:"训练中",completed:"已完成",failed:"失败",interrupted:"异常中断",pending:"等待"};
  const classes={running:"ok",completed:"ok",failed:"fail",interrupted:"fail",pending:""};
  $("#trainingRows").innerHTML=training.models.map(p=>{
    const progress=Math.min(100, p.task_total ? p.task_current/p.task_total*100 : 0);
    const resource=p.pid
      ? `PID ${p.pid} · ${duration(p.elapsed_seconds)} · CPU ${fmt(p.cpu_percent,1)}% · MEM ${fmt(p.memory_percent,1)}%`
      : "—";
    return `<tr>
      <td><b>${escapeHtml(p.model)}</b></td>
      <td><span class="status-pill ${classes[p.state]}">${labels[p.state]}</span></td>
      <td><div class="task-progress"><div style="width:${progress}%"></div></div><small>${p.task_current}/${p.task_total}</small></td>
      <td class="training-detail" title="${escapeHtml(p.detail)}">${escapeHtml(p.detail)}<small>${p.log_updated_utc?chinaTime(p.log_updated_utc)+" GMT+8":"—"}</small></td>
      <td>${p.started_utc?chinaTime(p.started_utc)+" GMT+8":"—"}</td>
      <td class="training-resource">${resource}</td>
    </tr>`;
  }).join("");
}

function valuePct(value) {
  return value == null ? "—" : pct(value);
}

function renderReview(kind=REVIEW_KIND) {
  REVIEW_KIND=kind;
  const review=DATA.review;
  $("#reviewDefinition").textContent=`统计口径：${review.label_definition}；“待复盘”不计入胜率。`;
  const aggregate=review.aggregates.find(x=>x.kind===kind && x.top_n===10);
  const cards=aggregate ? [
    ["Top10 个股胜率", valuePct(aggregate.stock_win_rate)],
    ["Top10 交易日胜率", valuePct(aggregate.day_win_rate)],
    ["Top10 平均收益", valuePct(aggregate.average_return)],
    ["Top10 平均超额", valuePct(aggregate.average_excess_return)],
    ["已复盘 / 待复盘", `${aggregate.realized_days} / ${aggregate.pending_days}`],
  ] : [];
  $("#reviewKpis").innerHTML=cards.map(x=>`<div><label>${x[0]}</label><strong>${x[1]}</strong></div>`).join("");
  const rows=review.daily.filter(x=>x.kind===kind && x.top_n===10).reverse();
  $("#reviewRows").innerHTML=rows.map((r,rowIndex)=>`<tr class="review-summary-row" data-review-row="${rowIndex}">
    <td>${r.date}</td><td>${r.outcome_date || "—"}</td><td>Top${r.top_n}</td>
    <td>${r.realized_count}/${r.selected_count}</td><td>${valuePct(r.stock_win_rate)}</td>
    <td class="${r.average_return>0?"positive":r.average_return<0?"negative":""}">${valuePct(r.average_return)}</td>
    <td>${valuePct(r.benchmark_return)}</td>
    <td class="${r.excess_return>0?"positive":r.excess_return<0?"negative":""}">${valuePct(r.excess_return)}</td>
    <td><span class="status-pill ${r.status==="realized"?"ok":"warn"}">${r.status==="realized"?"已复盘":"待复盘"}</span></td>
    <td><button class="detail-button" data-review-detail="${rowIndex}" aria-expanded="false">查看</button></td>
  </tr><tr class="review-detail-row" data-review-detail-row="${rowIndex}" hidden>
    <td colspan="10"><div class="stock-detail">
      <div class="stock-detail-head"><b>${r.date} · ${kind==="filtered"?"过滤后":"原始信号"} Top${r.top_n}</b><span>收益区间 ${r.outcome_date ? `${r.date} 后首个交易日收盘 → ${r.outcome_date} 收盘` : "尚未形成"}</span></div>
      <div class="table-wrap"><table class="stock-detail-table">
        <thead><tr><th>排名</th><th>股票</th><th>代码</th><th>预测分</th><th>看涨模型占比</th><th>实际收益</th><th>结果</th></tr></thead>
        <tbody>${(r.stocks || []).map(s=>`<tr>
          <td>${s.rank}</td><td>${escapeHtml(s.name)}</td><td>${escapeHtml(s.instrument)}</td>
          <td>${s.avg_score==null?"—":fmt(s.avg_score,4)}</td><td>${valuePct(s.pos_ratio)}</td>
          <td class="${s.return>0?"positive":s.return<0?"negative":""}">${valuePct(s.return)}</td>
          <td>${s.return==null?"待复盘":s.return>0?"盈利":s.return<0?"亏损":"持平"}</td>
        </tr>`).join("")}</tbody>
      </table></div>
    </div></td>
  </tr>`).join("");
  document.querySelectorAll("[data-review-row]").forEach(row=>row.addEventListener("click",()=>{
    const button=row.querySelector("[data-review-detail]");
    const detail=document.querySelector(`[data-review-detail-row="${row.dataset.reviewRow}"]`);
    const opening=detail.hidden;
    detail.hidden=!opening;
    button.textContent=opening?"收起":"查看";
    button.setAttribute("aria-expanded",String(opening));
  }));
}

async function load(showToast=false) {
  try {
    const res=await fetch("/api/dashboard",{cache:"no-store"}); if(!res.ok) throw new Error(await res.text());
    DATA=await res.json(); const s=DATA.summary;
    $("#predictionDate").textContent=s.prediction_date;
    $("#updatedAt").textContent=`流水线完成于 ${chinaTime(DATA.pipeline.last_success_utc)} GMT+8`;
    renderKpis(DATA); renderLiveData(DATA); renderChart(DATA.metrics, DATA.selected); renderWeights(DATA.selected); renderStocks(); renderLineage(s); renderReview(); renderTraining(DATA.training); renderEvents(DATA.events);
    if(showToast){ $("#toast").textContent="数据已刷新"; $("#toast").className="show"; setTimeout(()=>$("#toast").classList.remove("show"),1800); }
  } catch(e) { $("#toast").textContent=`加载失败：${e.message}`; $("#toast").className="show fail"; }
}
document.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>{
  if (b.dataset.table) {
    document.querySelectorAll("[data-table]").forEach(x=>x.classList.remove("active")); b.classList.add("active"); renderStocks(b.dataset.table);
  } else if (b.dataset.reviewKind) {
    document.querySelectorAll("[data-review-kind]").forEach(x=>x.classList.remove("active")); b.classList.add("active"); renderReview(b.dataset.reviewKind);
  }
}));
$("#refresh").addEventListener("click",()=>load(true));
setInterval(()=>$("#clock").textContent=`${chinaTime(Date.now(), false)} GMT+8`,1000);
setInterval(()=>load(false),60000);
load();
