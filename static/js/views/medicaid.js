function _hslGradient(n) {
  return Array.from({ length: n }, (_, i) => {
    const t = n <= 1 ? 0 : i / (n - 1);
    return `hsl(${Math.round(t * 50)}, 88%, 52%)`;
  });
}

let _medicaidTab = 'datasets';
let _medicaidStateFilter = null;
let _selectedMedicaidState = 'all';
let _medFilterTimer = null;
let _medicaidDsParam = '';
let _medColFilters = {};

function _removeMedCell(id) {
  document.getElementById(id)?.remove();
  const grid = document.getElementById('med-sector-grid');
  if (grid && !grid.children.length) grid.remove();
}

function _loadSectorZip(sector) {
  const el = document.getElementById('pie-state-topsector-zip');
  const lbl = document.getElementById('pie-state-topsector-label');
  if (!el) return;
  el.innerHTML = '<div class="med-placeholder-text">Loading…</div>';
  const url = `/api/stats/medicaid-top-sector-cities?datasets=${encodeURIComponent(_medicaidDsParam)}${sector ? `&sector=${encodeURIComponent(sector)}` : ''}`;
  fetch(url).then(r => r.json()).then(({ sector: s, data }) => {
    if (!document.getElementById('pie-state-topsector-zip')) return;
    if (!data || !data.length) {
      _removeMedCell('med-cell-sectorzip');
      return;
    }
    el.innerHTML = '';
    if (lbl) lbl.textContent = `— ${s}`;
    document.querySelectorAll('.sector-chip').forEach(c => {
      const isActive = c.dataset.sector === s;
      c.style.color = isActive ? 'var(--accent)' : 'var(--muted)';
      c.style.fontWeight = isActive ? '700' : '500';
      c.style.borderBottom = isActive ? '2px solid var(--accent)' : '2px solid transparent';
    });

    // Insight lines above the chart
    const insightEl = document.getElementById('pie-state-topsector-insight');
    const acronym = s.split(/\s+/).map(w => w[0] || '').join('').toUpperCase().slice(0, 5);

    function setInsight(d) {
      if (!insightEl) return;
      const pct = ((d.value / d.city_total) * 100).toFixed(1);
      insightEl.innerHTML = `
        <div style="font-size:13px;font-weight:600;color:var(--text)">${d.value.toLocaleString()} records</div>
        <div style="font-size:12px;color:var(--muted)">${pct}% of ${esc(d.label)} records are ${esc(acronym)}***</div>`;
    }

    if (insightEl) {
      if (data.length) setInsight(data[0]);
      else insightEl.innerHTML = '';
    }

    const sliced = data.slice(0, 10);
    const zipColors = _hslGradient(sliced.length);
    drawPieChart('pie-state-topsector-zip', sliced, zipColors, {
      unit: 'records',
      centerLabel: 'cities',
      legendFmt: (_v, pct) => pct + '%',
      pctFn: d => (d.value / d.city_total) * 100,
      onHover: d => setInsight(d),
      onLeave: () => { if (data.length) setInsight(data[0]); },
    });
  });
}

function renderMedicaidStatStrip(state, byState, medDatasets, totalStates) {
  const dsList = state === 'all' ? medDatasets : (byState[state] || []);
  const entities = dsList.reduce((s, d) => s + (d.entity_count || 0), 0);
  const targets  = dsList.reduce((s, d) => s + (d.target_count || 0), 0);
  const statesVal = state === 'all' ? totalStates : 1;
  return `
    <div class="stats-grid" style="margin-bottom:20px">
      <div class="stat-card"><div class="stat-value">${dsList.length}</div><div class="stat-label">Datasets</div></div>
      <div class="stat-card"><div class="stat-value">${entities.toLocaleString()}</div><div class="stat-label">Total International References</div></div>
      <div class="stat-card"><div class="stat-value">${targets.toLocaleString()}</div><div class="stat-label">Records in Current Dataset</div></div>
      <div class="stat-card"><div class="stat-value">${statesVal}</div><div class="stat-label">States</div></div>
    </div>`;
}

async function renderMedicaidView(tab) {
  if (tab) _medicaidTab = tab;
  const content = document.getElementById('content');
  const medDatasets = allDatasets.filter(d => (d.tags || []).includes('sector.usmed.debarment'));

  // Group by state
  const byState = {};
  for (const ds of medDatasets) {
    const m = ds.name.match(/^us_([a-z]{2})_/);
    const state = m ? (US_STATE_NAMES[m[1]] || m[1].toUpperCase()) : 'Federal';
    if (!byState[state]) byState[state] = [];
    byState[state].push(ds);
  }
  // Sort: states whose total target_count is 0 (no charts will populate when
  // selected) get pushed to the bottom. Within each group, sort by entity_count
  // desc so the most data-rich states stay at the top.
  const stateList = Object.entries(byState).sort((a, b) => {
    const aTargets = a[1].reduce((s, d) => s + (d.target_count || 0), 0);
    const bTargets = b[1].reduce((s, d) => s + (d.target_count || 0), 0);
    const aEmpty = aTargets === 0;
    const bEmpty = bTargets === 0;
    if (aEmpty !== bEmpty) return aEmpty ? 1 : -1;
    return b[1].reduce((s, d) => s + (d.entity_count || 0), 0)
         - a[1].reduce((s, d) => s + (d.entity_count || 0), 0);
  });

  const tabBar = `
    <div class="med-tab-bar">
      <button onclick="renderMedicaidView('datasets')" class="med-tab-btn${_medicaidTab==='datasets'?' active':''}">Datasets</button>
      <button onclick="renderMedicaidView('records')" class="med-tab-btn${_medicaidTab==='records'?' active':''}">Records</button>
      <button onclick="renderMedicaidView('nppes')" class="med-tab-btn${_medicaidTab==='nppes'?' active':''}">NPPES</button>
    </div>`;

  const statStrip = `<div id="med-stat-strip">${renderMedicaidStatStrip('all', byState, medDatasets, stateList.length)}</div>`;

  let bodyHtml;
  if (_medicaidTab === 'datasets') {
    // Ensure selected state is valid
    if (_selectedMedicaidState !== 'all' && !byState[_selectedMedicaidState]) {
      _selectedMedicaidState = 'all';
    }

    bodyHtml = tabBar + statStrip + `
      <div class="med-split-layout">

        <!-- State list (left panel) -->
        <div class="med-state-panel">
          <div class="med-state-panel-header">
            <div style="position:relative">
              <svg style="position:absolute;left:9px;top:50%;transform:translateY(-50%);color:var(--muted)" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
              <input id="med-state-search" type="text" placeholder="Filter states…" oninput="filterMedicaidStates(this.value)"
                class="med-search-input" style="width:100%">
            </div>
          </div>
          <div id="med-state-list">
            ${renderMedicaidStateRows(stateList, medDatasets)}
          </div>
        </div>

        <!-- Dataset panel (right) -->
        <div style="flex:1;overflow-y:auto;padding:20px 24px" id="med-datasets-panel">
          ${renderMedicaidStateDatasets(_selectedMedicaidState, byState, medDatasets)}
        </div>
      </div>`;

  } else if (_medicaidTab === 'nppes') {
    bodyHtml = tabBar + renderNppesView(byState);
  } else {
    bodyHtml = tabBar + statStrip + `<div id="med-records-body"><div class="loading"><div class="spinner"></div><div class="loading-text">Loading Medicaid exclusion records…</div></div></div>`;
  }

  if (!BannerAnimation.isActive() || !document.getElementById('medicaid-banner-canvas')) {
    content.innerHTML = `<div class="home-banner-wrap home-banner-wrap--sm"><canvas id="medicaid-banner-canvas"></canvas></div><div id="medicaid-body">${bodyHtml}</div>`;
    BannerAnimation.init(document.getElementById('medicaid-banner-canvas'));
  } else {
    document.getElementById('medicaid-body').innerHTML = bodyHtml;
  }

  if (_medicaidTab === 'nppes') {
    const _t = document.getElementById('nppes-tile-total');
    if (_t) _t.textContent = '~8,000,000';
  } else if (_medicaidTab === 'records') {
    await loadMedicaidPage(0);
  }
}

function renderMedicaidStateRows(stateList, medDatasets) {
  const allActive = _selectedMedicaidState === 'all';
  const allRow = `<div class="country-row med-state-row" data-state="all" onclick="selectMedicaidState('all')"
    style="background:${allActive ? 'var(--surface2)' : 'transparent'};border-left:3px solid ${allActive ? 'var(--accent)' : 'transparent'}">
    <span style="font-size:18px;line-height:1;flex-shrink:0">🇺🇸</span>
    <div style="flex:1;min-width:0">
      <div style="font-size:13px;color:${allActive ? 'var(--text)' : 'var(--muted)'};font-weight:${allActive ? '600' : '400'}">All States</div>
      <div class="med-state-subtext">${medDatasets.length} dataset${medDatasets.length !== 1 ? 's' : ''}</div>
    </div>
  </div>`;

  const rows = stateList.map(([state, dsList]) => {
    const active = _selectedMedicaidState === state;
    const n = dsList.reduce((s, d) => s + (d.target_count || 0), 0);
    const color = STATE_COLOR_MAP[state] || '#9ca3af';
    return `<div class="country-row med-state-row" data-state="${esc(state)}" onclick="selectMedicaidState('${esc(state)}')"
      style="background:${active ? 'var(--surface2)' : 'transparent'};border-left:3px solid ${active ? 'var(--accent)' : 'transparent'}">
      <span class="med-color-dot" style="width:12px;height:12px;background:${color}"></span>
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;color:${active ? 'var(--text)' : 'var(--muted)'};font-weight:${active ? '600' : '400'};white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(state)}</div>
        <div class="med-state-subtext">
          ${dsList.length} dataset${dsList.length !== 1 ? 's' : ''} · ${fmtNum(n)} excluded
        </div>
      </div>
    </div>`;
  }).join('');

  return allRow + rows;
}

function renderMedicaidStateDatasets(state, byState, medDatasets) {
  const dsList = state === 'all' ? medDatasets : (byState[state] || []);
  const sorted = [...dsList].sort((a, b) => (b.target_count || 0) - (a.target_count || 0));
  const totalTargets = dsList.reduce((s, d) => s + (d.target_count || 0), 0);
  const color = state !== 'all' ? (STATE_COLOR_MAP[state] || '#9ca3af') : null;

  const header = `
    <div style="display:flex;align-items:center;gap:14px;margin-bottom:18px;flex-wrap:wrap">
      ${color
        ? `<span class="med-color-dot" style="width:20px;height:20px;background:${color}"></span>`
        : `<span style="font-size:24px">🇺🇸</span>`}
      <div>
        <div style="font-size:18px;font-weight:700">${state === 'all' ? 'All States' : esc(state)}</div>
        <div style="font-size:12px;color:var(--muted);margin-top:3px">
          ${dsList.length} dataset${dsList.length !== 1 ? 's' : ''} · ${fmtNum(totalTargets)} records
        </div>
      </div>
    </div>`;

  const cards = sorted.map(ds => {
    const m = ds.name.match(/^us_([a-z]{2})_/);
    const dsState = m ? (US_STATE_NAMES[m[1]] || m[1].toUpperCase()) : 'Federal';
    const statusColor = ds.result === 'success' ? 'var(--green)' : ds.result ? 'var(--red)' : 'var(--muted)';
    const tags = (ds.tags || []).slice(0, 3).map(t =>
      `<span style="padding:2px 7px;background:var(--tag-bg);color:var(--tag-text);border-radius:10px;font-size:10px">${t}</span>`
    ).join(' ');
    return `<div class="dataset-card" style="margin-bottom:10px" onclick="showDetail('${ds.name}')">
      <div class="card-header">
        <div style="flex:1">
          <div class="card-title">${esc(ds.title)}</div>
          <div class="card-name">${esc(ds.name)}</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-shrink:0">
          <span style="font-size:10px;color:${statusColor}">${ds.result || '—'}</span>
          <div class="status-dot ${ds.result === 'success' ? 'success' : ds.result ? 'error' : 'unknown'}"></div>
        </div>
      </div>
      ${ds.summary ? `<div class="card-summary">${esc(ds.summary)}</div>` : ''}
      <div class="card-meta">
        ${ds.entity_count ? `<div class="meta-item"><strong>${ds.entity_count.toLocaleString()}</strong> excluded providers</div>` : ''}
        ${state === 'all' ? `<div class="meta-item">${esc(dsState)}</div>` : ''}
        ${ds.updated_at ? `<div class="meta-item">${ds.updated_at}</div>` : ''}
        ${ds.frequency  ? `<div class="meta-item">${ds.frequency}</div>` : ''}
      </div>
      ${tags ? `<div class="card-tags" style="margin-top:8px">${tags}</div>` : ''}
    </div>`;
  }).join('');

  // Sector pie chart — shown for all states and per-state
  let sectorSection = '';
  if (state === 'all') {
    const dsParam = medDatasets.map(d => d.name).join(',');
    _medicaidDsParam = dsParam;
    sectorSection = `
      <div id="med-sector-grid" class="med-sector-grid" style="margin-bottom:24px">
        <div id="med-cell-year-all" style="grid-column:1/-1">
          <div class="med-chart-label">Exclusions by Year <span style="font-weight:400;color:var(--muted)">(first seen — top 10 states)</span></div>
          <div id="bar-all-year"><div class="med-placeholder-text">Loading…</div></div>
          <div id="bar-all-year-legend" style="display:flex;flex-wrap:wrap;gap:12px;margin-top:10px;font-size:11px;color:var(--muted)"></div>
        </div>
        <div id="med-cell-state-all" style="grid-column:1/-1">
          <div class="med-chart-label">Offenses by State <span style="font-weight:400;color:var(--muted)">(top 10)</span></div>
          <div id="bar-all-states"></div>
        </div>
        <div id="med-cell-sector-all">
          <div class="med-chart-label">Offenses by Sector <span style="font-weight:400;color:var(--muted)">(top 10)</span></div>
          <div id="pie-all-sector"><div class="med-placeholder-text">Loading…</div></div>
          <div id="pie-all-sector-footnote" style="margin-top:10px;font-size:10px;color:var(--muted);line-height:1.5"></div>
        </div>
        <div id="med-cell-city-all">
          <div class="med-chart-label">Offenses by City <span style="font-weight:400;color:var(--muted)">(top 10)</span></div>
          <div id="pie-all-city"><div class="med-placeholder-text">Loading…</div></div>
        </div>
      </div>`;
    setTimeout(() => {
      fetch(`/api/stats/medicaid-by-sector?datasets=${encodeURIComponent(dsParam)}`)
        .then(r => r.json()).then(data => {
          const el = document.getElementById('pie-all-sector');
          if (!el) return;
          if (!data.length) { el.innerHTML = '<div style="font-size:13px;color:var(--muted);padding:20px 0">No data available</div>'; return; }
          el.innerHTML = '';
          const sliced = data.slice(0, 10).sort((a, b) => b.value - a.value);
          drawLollipopChart('pie-all-sector', sliced, 'var(--accent)');
          const fn = document.getElementById('pie-all-sector-footnote');
          if (fn && data.length > sliced.length) fn.textContent = `** Showing top 10 of ${data.length} sectors`;
        });
      fetch(`/api/stats/medicaid-by-city?datasets=${encodeURIComponent(dsParam)}`)
        .then(r => r.json()).then(data => {
          const el = document.getElementById('pie-all-city');
          if (!el) return;
          if (!data.length) { el.innerHTML = '<div style="font-size:13px;color:var(--muted);padding:20px 0">No data available</div>'; return; }
          el.innerHTML = '';
          drawLollipopChart('pie-all-city', data.slice(0, 10).sort((a, b) => b.value - a.value), 'var(--green)');
        });
      // Compute state totals from allDatasets index — no API call needed
      {
        const stateData = Object.entries(byState)
          .map(([stateName, dsList]) => ({
            label: stateName,
            value: dsList.reduce((s, d) => s + (d.entity_count || 0), 0)
          }))
          .sort((a, b) => b.value - a.value)
          .slice(0, 10);
        const el = document.getElementById('bar-all-states');
        if (el && stateData.length) {
          drawHorizontalBarChart('bar-all-states', stateData, 'var(--accent2)', { marginLeft: 160 });
        }
      }
      fetch('/api/stats/medicaid-year-by-state')
        .then(r => r.json()).then(({ sectors, states }) => {
          const el = document.getElementById('bar-all-year');
          if (!el) return;
          if (!states.length) {
            el.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:8px 0">Data loads after entity cache warms — revisit in a few minutes.</div>';
            return;
          }
          el.innerHTML = '';
          drawHorizontalStackedBarChart('bar-all-year', sectors, states);
          const legendEl = document.getElementById('bar-all-year-legend');
          if (legendEl) {
            const COLORS = ['#60a5fa','#34d399','#facc15','#a78bfa','#ef4444','#f97316','#9ca3af'];
            legendEl.innerHTML = sectors.map((s, i) =>
              `<span style="display:flex;align-items:center;gap:5px">
                <span style="width:10px;height:10px;border-radius:2px;background:${COLORS[i]};flex-shrink:0"></span>
                ${esc(s)}
              </span>`
            ).join('');
          }
        });
    }, 0);
  } else {
    const dsParam = (byState[state] || []).map(d => d.name).join(',');
    sectorSection = `
      <div id="med-sector-grid" class="med-sector-grid">
        <div id="med-cell-sector">
          <div class="med-chart-label">Offenses by Sector</div>
          <div id="pie-state-sector" class="med-chart-host"><div class="med-placeholder-text">Loading…</div></div>
          <div id="pie-sector-footnote" style="margin-top:10px;font-size:10px;color:var(--muted);line-height:1.5"></div>
        </div>
        <div id="med-cell-city">
          <div class="med-chart-label">Offenses by City</div>
          <div id="pie-state-city" class="med-chart-host"><div class="med-placeholder-text">Loading…</div></div>
        </div>
        <div id="med-cell-sectorzip">
          <div class="med-chart-label" style="margin-bottom:4px">
            Sector by City <span id="pie-state-topsector-label" style="font-weight:400;color:var(--muted)"></span>
          </div>
          <div id="pie-state-topsector-chips" style="display:flex;flex-wrap:wrap;gap:0;margin-bottom:10px;border-bottom:1px solid var(--border)"></div>
          <div id="pie-state-topsector-insight" style="margin-bottom:10px;line-height:1.6"></div>
          <div id="pie-state-topsector-zip" class="med-chart-host"><div class="med-placeholder-text">Loading…</div></div>
          <div style="margin-top:10px;font-size:10px;color:var(--muted);line-height:1.5">*** Illegible records that could not be read are not included in this dataset.</div>
        </div>
      </div>`;

    _medicaidDsParam = dsParam;

    const targets = dsList.reduce((s, d) => s + (d.target_count || 0), 0);
    const centerVal = targets >= 1000 ? (targets / 1000).toFixed(1) + 'k' : targets.toLocaleString();

    setTimeout(() => {
      fetch(`/api/stats/medicaid-by-sector?datasets=${encodeURIComponent(dsParam)}`)
        .then(r => r.json()).then(data => {
          const el = document.getElementById('pie-state-sector');
          if (!el) return;
          if (!data.length) { el.innerHTML = '<div style="font-size:13px;color:var(--muted);padding:20px 0">No data available</div>'; return; }
          el.innerHTML = '';
          const sliced = data.slice(0, 20);
          const sectorColors = _hslGradient(sliced.length);
          // Build chips and attach event listeners
          const chipsEl = document.getElementById('pie-state-topsector-chips');
          if (chipsEl) {
            chipsEl.innerHTML = sliced.map((d) => {
              const acronym = d.label.split(/\s+/).map(w => w[0] || '').join('').toUpperCase().slice(0, 5);
              return `<a class="sector-chip" data-sector="${esc(d.label)}" href="#"
                onmouseover="if(this.style.fontWeight!=='700'){this.style.color='var(--text)'}"
                onmouseout="if(this.style.fontWeight!=='700'){this.style.color='var(--muted)'}"
                title="${esc(d.label)}">${esc(acronym)}</a>`;
            }).join('');
            chipsEl.querySelectorAll('.sector-chip').forEach(btn => {
              btn.addEventListener('click', (e) => { e.preventDefault(); _loadSectorZip(btn.dataset.sector); });
            });
          }
          drawPieChart('pie-state-sector', sliced, sectorColors, {
            unit: 'records',
            centerValue: centerVal,
            centerLabel: data.length + ' sectors',
            totalOverride: targets,
            legendFmt: (_v, pct) => pct + '%',
          });
          const footnoteEl = document.getElementById('pie-sector-footnote');
          if (footnoteEl && data.length > sliced.length) {
            footnoteEl.textContent = `** Showing top 20 of ${data.length} sectors`;
          }
          // Load first sector zip by default
          _loadSectorZip(sliced[0]?.label);
        });
      fetch(`/api/stats/medicaid-by-city?datasets=${encodeURIComponent(dsParam)}`)
        .then(r => r.json()).then(data => {
          const el = document.getElementById('pie-state-city');
          if (!el) return;
          if (!data.length) { el.innerHTML = '<div style="font-size:13px;color:var(--muted);padding:20px 0">No data available</div>'; return; }
          el.innerHTML = '';
          drawPieChart('pie-state-city', data.slice(0, 20), null, {
            unit: 'records',
            centerLabel: 'cities',
            legendFmt: (_v, pct) => pct + '%',
          });
        });
    }, 0);
  }

  return header + sectorSection + cards;
}

function selectMedicaidState(state) {
  _selectedMedicaidState = state;
  const medDatasets = allDatasets.filter(d => (d.tags || []).includes('sector.usmed.debarment'));
  const byState = {};
  for (const ds of medDatasets) {
    const m = ds.name.match(/^us_([a-z]{2})_/);
    const s = m ? (US_STATE_NAMES[m[1]] || m[1].toUpperCase()) : 'Federal';
    if (!byState[s]) byState[s] = [];
    byState[s].push(ds);
  }
  // Update active state in left panel
  document.querySelectorAll('#med-state-list .country-row').forEach(row => {
    const active = row.dataset.state === state;
    row.style.background = active ? 'var(--surface2)' : 'transparent';
    row.style.borderLeft = `3px solid ${active ? 'var(--accent)' : 'transparent'}`;
    const name = row.querySelector('div > div:first-child');
    if (name) { name.style.color = active ? 'var(--text)' : 'var(--muted)'; name.style.fontWeight = active ? '600' : '400'; }
  });
  const totalStates = Object.keys(byState).length;
  const strip = document.getElementById('med-stat-strip');
  if (strip) strip.innerHTML = renderMedicaidStatStrip(state, byState, medDatasets, totalStates);
  const panel = document.getElementById('med-datasets-panel');
  if (panel) panel.innerHTML = renderMedicaidStateDatasets(state, byState, medDatasets);
}

function filterMedicaidStates(q) {
  const lower = q.toLowerCase();
  document.querySelectorAll('#med-state-list .country-row').forEach(row => {
    const name = row.querySelector('div > div:first-child');
    const text = (name?.textContent || '').toLowerCase();
    row.style.display = text.includes(lower) ? '' : 'none';
  });
}

function buildMedicaidTable(rows, cols) {
  const filterRow = `<tr>${cols.map(k => `
    <th style="padding:3px 6px;background:var(--surface2);border-bottom:1px solid var(--border)">
      <input data-col="${k}" type="text" placeholder="…"
        value="${esc(_medColFilters[k] || '')}"
        oninput="filterMedicaidColumn('${k}', this.value)"
        class="med-col-filter-input">
    </th>`).join('')}</tr>`;

  const thead = `<thead>
    <tr style="position:sticky;top:0;z-index:2">${cols.map(k => {
      const w = COL_WIDTHS[k] || 130;
      return `<th class="med-th" style="min-width:${w}px">${ES_COL_LABELS[k] || k}</th>`;
    }).join('')}</tr>
    <tr style="position:sticky;top:34px;z-index:2">${filterRow.slice(filterRow.indexOf('<tr>') + 4, filterRow.lastIndexOf('</tr>'))}</tr>
  </thead>`;

  const tbody = `<tbody>${rows.map((r, i) => {
    const bg = i % 2 === 0 ? 'var(--bg)' : 'var(--surface)';
    const cells = cols.map(k => {
      const val = r[k] || '';
      let cell = '';
      if (k === 'schema') {
        const color = SCHEMA_COLORS[val] || 'var(--muted)';
        cell = val ? `<span style="padding:2px 8px;background:${color}18;color:${color};border-radius:10px;font-size:11px;font-weight:600">${esc(val)}</span>` : '';
      } else if (k === '_dataset') {
        cell = val ? `<span style="font-size:11px;color:var(--accent);font-family:monospace">${esc(val)}</span>` : '';
      } else if (k === 'id') {
        cell = val ? `<span style="font-family:monospace;font-size:10px;color:var(--muted)">${esc(val)}</span>` : '';
      } else if (k === 'first_seen' || k === 'last_seen' || k === 'last_change') {
        cell = val ? `<span style="font-size:11px;color:var(--muted)">${esc(String(val).slice(0,10))}</span>` : '';
      } else {
        cell = val ? `<span style="font-size:11px">${esc(String(val))}</span>` : '';
      }
      return `<td class="med-td">${cell || '<span style="color:var(--border)">—</span>'}</td>`;
    }).join('');
    return `<tr style="background:${bg}">${cells}</tr>`;
  }).join('')}</tbody>`;

  return thead + tbody;
}

function filterMedicaidColumn(col, val) {
  clearTimeout(_medFilterTimer);
  if (val.trim()) _medColFilters[col] = val.trim().toLowerCase();
  else delete _medColFilters[col];
  _medFilterTimer = setTimeout(() => {
    const d = window._medRecordsData;
    if (!d) return;
    const globalQ = (document.querySelector('#med-records-body input[placeholder="Filter records…"]')?.value || '').toLowerCase().trim();
    const filtered = _applyMedicaidFilters(d.results, globalQ);
    const tbl = document.getElementById('med-table');
    if (tbl) tbl.innerHTML = buildMedicaidTable(filtered, d.cols);
  }, 200);
}

function _applyMedicaidFilters(results, globalQ) {
  return results.filter(r => {
    if (globalQ && !Object.values(r).some(v => v && String(v).toLowerCase().includes(globalQ))) return false;
    for (const [col, q] of Object.entries(_medColFilters)) {
      const v = r[col];
      if (!v || !String(v).toLowerCase().includes(q)) return false;
    }
    return true;
  });
}

async function loadMedicaidPage(offset) {
  const body = document.getElementById('med-records-body');
  if (!body) return;

  const PAGE = 500;
  const res = await fetch(`/api/medicaid-records?offset=${offset}&limit=${PAGE}`);
  const data = await res.json();

  if (!data.results.length && offset === 0) {
    body.innerHTML = `<div class="empty"><div class="empty-icon">🏥</div><div>No records found</div></div>`;
    return;
  }

  const allKeys = new Set(data.results.flatMap(r => Object.keys(r)));

  if (offset === 0) {
    // First page — build the full shell
    const cols = ES_COL_PRIORITY.filter(k => allKeys.has(k));
    allKeys.forEach(k => { if (!cols.includes(k)) cols.push(k); });
    window._medRecordsData = { results: data.results, cols, total: data.total, loaded: data.results.length };

    const header = `
      <div style="display:flex;align-items:center;gap:12px;padding:12px 0;flex-wrap:wrap">
        <span id="med-count" style="font-size:13px;color:var(--muted)">
          Showing <strong style="color:var(--text)">${data.results.length.toLocaleString()}</strong> of
          <strong style="color:var(--text)">${data.total.toLocaleString()}</strong> excluded providers ·
          <strong style="color:var(--text)">${data.searched.length}</strong> state datasets
        </span>
        <div style="position:relative;margin-left:auto">
          <svg style="position:absolute;left:9px;top:50%;transform:translateY(-50%);color:var(--muted)" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input type="text" placeholder="Filter records…" oninput="filterMedicaidRecords(this.value)"
            class="med-search-input" style="width:220px;border-radius:var(--radius);padding-right:10px">
        </div>
      </div>`;

    const moreBtn = data.loaded < data.total
      ? `<div id="med-load-more" style="text-align:center;padding:16px">
           <button onclick="loadMedicaidPage(${PAGE})" class="med-load-more-btn">
             Load next ${PAGE.toLocaleString()} records (${(data.total - PAGE).toLocaleString()} remaining)
           </button>
         </div>` : '';

    _medColFilters = {};
    body.innerHTML = header +
      `<div style="overflow-x:auto"><table id="med-table" style="width:100%;border-collapse:collapse;font-size:12px">${buildMedicaidTable(data.results, cols)}</table></div>` +
      moreBtn;

  } else {
    // Append more rows to existing table
    const d = window._medRecordsData;
    d.results = d.results.concat(data.results);
    d.loaded = d.results.length;

    const tbl = document.getElementById('med-table');
    if (tbl) tbl.innerHTML = buildMedicaidTable(d.results, d.cols);

    const nextOffset = offset + PAGE;
    const moreEl = document.getElementById('med-load-more');
    if (moreEl) {
      if (nextOffset < data.total) {
        moreEl.innerHTML = `<button onclick="loadMedicaidPage(${nextOffset})" class="med-load-more-btn">
          Load next ${PAGE.toLocaleString()} records (${(data.total - nextOffset).toLocaleString()} remaining)
        </button>`;
      } else {
        moreEl.remove();
      }
    }

    const countEl = document.getElementById('med-count');
    if (countEl) {
      countEl.innerHTML = `Showing <strong style="color:var(--text)">${d.results.length.toLocaleString()}</strong> of
        <strong style="color:var(--text)">${data.total.toLocaleString()}</strong> excluded providers`;
    }
  }
}


function filterMedicaidRecords(q) {
  clearTimeout(_medFilterTimer);
  _medFilterTimer = setTimeout(() => {
    const d = window._medRecordsData;
    if (!d) return;
    const filtered = _applyMedicaidFilters(d.results, q.toLowerCase().trim());
    const tbl = document.getElementById('med-table');
    if (tbl) tbl.innerHTML = buildMedicaidTable(filtered, d.cols);
  }, 200);
}

// ── NPPES tab ─────────────────────────────────────────────────────────────────

let _nppesSkip = 0;

function renderNppesView(byState) {
  let stateAbbrev = '';
  if (_selectedMedicaidState !== 'all') {
    const ds = (byState[_selectedMedicaidState] || [])[0];
    if (ds) {
      const m = ds.name.match(/^us_([a-z]{2})_/);
      stateAbbrev = m ? m[1].toUpperCase() : '';
    }
  }

  return `
    <div style="padding:4px 0 24px">
      <div class="stats-grid" style="margin-bottom:20px" id="nppes-stat-strip">
        <div class="stat-card"><div class="stat-value" id="nppes-tile-total">—</div><div class="stat-label">Registered NPIs</div></div>
        <div class="stat-card"><div class="stat-value" id="nppes-tile-individuals">—</div><div class="stat-label">Individuals (NPI-1)</div></div>
        <div class="stat-card"><div class="stat-value" id="nppes-tile-orgs">—</div><div class="stat-label">Organizations (NPI-2)</div></div>
        <div class="stat-card"><div class="stat-value" id="nppes-tile-active">—</div><div class="stat-label">Active Providers</div></div>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:18px;font-weight:700;margin-bottom:8px">National Plan and Provider Enumeration System</div>
        <p style="font-size:13px;color:var(--muted);line-height:1.6;max-width:720px;margin:0">
          Any entity that electronically transmits health information in connection with a HIPAA-covered transaction
          (claims, eligibility checks, referrals, etc.) is required to obtain an NPI. NPPES is the system that issues
          and tracks those identifiers.
        </p>
      </div>
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px;margin-bottom:20px">
        <div style="display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap">
          <div>
            <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">State</label>
            <input id="nppes-state" type="text" placeholder="CA" value="${stateAbbrev}" maxlength="2"
              style="width:80px;background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:6px 8px;font-size:12px;color:var(--text);outline:none;box-sizing:border-box;text-transform:uppercase"
              onkeydown="if(event.key==='Enter')nppesSearch(0)">
          </div>
          <div>
            <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Zip Code</label>
            <input id="nppes-zip" type="text" placeholder="90210, 90211, …"
              style="width:200px;background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:6px 8px;font-size:12px;color:var(--text);outline:none;box-sizing:border-box"
              onkeydown="if(event.key==='Enter')nppesSearch(0)">
          </div>
          <div>
            <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">Registrant Type</label>
            <select id="nppes-type"
              style="background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:6px 8px;font-size:12px;color:var(--text);outline:none;cursor:pointer">
              <option value="">All</option>
              <option value="NPI-1">NPI-1 · Individual</option>
              <option value="NPI-2">NPI-2 · Organization</option>
            </select>
          </div>
          <button onclick="nppesSearch(0)" class="med-load-more-btn" style="padding:7px 20px">Search</button>
          <span style="font-size:11px;color:var(--muted)">200 results per page (API max)</span>
        </div>
      </div>
      <div id="nppes-results"></div>
    </div>`;
}

async function nppesSearch(skip) {
  _nppesSkip = skip || 0;
  const state           = (document.getElementById('nppes-state')?.value.trim() || '').toUpperCase();
  const zipRaw          = document.getElementById('nppes-zip')?.value || '';
  const enumerationType = document.getElementById('nppes-type')?.value || '';
  const resultsEl       = document.getElementById('nppes-results');
  if (!resultsEl) return;

  const zips = zipRaw.split(',').map(z => z.trim()).filter(Boolean);

  if (!state) {
    resultsEl.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:8px 0">Enter a state abbreviation (e.g. CA).</div>';
    return;
  }
  if (!zips.length) {
    resultsEl.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:8px 0">Enter at least one zip code.</div>';
    return;
  }

  resultsEl.innerHTML = '<div class="loading"><div class="spinner"></div><div class="loading-text">Querying NPI Registry…</div></div>';

  const makeQs = zip => new URLSearchParams({
    number: '', enumeration_type: enumerationType, taxonomy_description: '', name_purpose: '',
    first_name: '', use_first_name_alias: '', last_name: '', organization_name: '',
    address_purpose: '', city: '', state, postal_code: zip, country_code: '',
    limit: 200, skip: _nppesSkip, pretty: 'on', version: '2.1',
  }).toString();

  try {
    const responses = await Promise.all(zips.map(zip => fetch(`/api/nppes?${makeQs(zip)}`).then(r => r.json())));

    // Merge and deduplicate by NPI number
    const seen = new Set();
    const results = [];
    let total = 0;
    for (const data of responses) {
      if (data.Errors) continue;
      total += data.result_count || 0;
      for (const r of (data.results || [])) {
        if (!seen.has(r.number)) { seen.add(r.number); results.push(r); }
      }
    }

    if (responses.every(d => d.Errors)) {
      const msg = responses[0].Errors?.[0]?.description || 'API error';
      resultsEl.innerHTML = `<div style="color:var(--red);padding:8px 0;font-size:12px">${esc(msg)}</div>`;
      return;
    }

    // Update stat tiles
    const npi1 = results.filter(r => r.enumeration_type === 'NPI-1').length;
    const npi2 = results.filter(r => r.enumeration_type === 'NPI-2').length;
    const active = results.filter(r => (r.basic || {}).status === 'A').length;
    const setTile = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    setTile('nppes-tile-individuals', npi1.toLocaleString());
    setTile('nppes-tile-orgs',        npi2.toLocaleString());
    setTile('nppes-tile-active',      active.toLocaleString());

    if (!results.length) {
      resultsEl.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">No results found.</div>';
      return;
    }

    const cols = ['NPI', 'Name', 'Type', 'Credential', 'Primary Specialty', 'City / State', 'Status'];
    const thead = `<thead><tr>${cols.map(h =>
      `<th style="padding:8px 12px;text-align:left;background:var(--surface2);color:var(--muted);font-size:11px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;border-bottom:1px solid var(--border);white-space:nowrap">${h}</th>`
    ).join('')}</tr></thead>`;

    const cell = (v, extra = '') =>
      `<td style="padding:7px 12px;border-bottom:1px solid var(--border);font-size:11px;${extra}">${v}</td>`;
    const tbody = `<tbody>${results.map((r, i) => {
      const b = r.basic || {}, isOrg = r.enumeration_type === 'NPI-2';
      const name = isOrg ? (b.organization_name || '—') : ([b.first_name, b.middle_name, b.last_name].filter(Boolean).join(' ') || '—');
      const tax  = (r.taxonomies || []).find(t => t.primary) || (r.taxonomies || [])[0];
      const addr = (r.addresses || []).find(a => a.address_purpose === 'LOCATION') || (r.addresses || [])[0];
      return `<tr style="background:${i % 2 === 0 ? 'var(--bg)' : 'var(--surface)'}">
        ${cell(`<span style="font-family:monospace;color:var(--muted)">${esc(r.number || '')}</span>`)}
        ${cell(esc(name), 'font-size:12px')}
        ${cell(`<span style="color:${isOrg ? 'var(--yellow)' : 'var(--accent)'}">${isOrg ? 'Organization' : 'Individual'}</span>`)}
        ${cell(esc(b.credential || '—'), 'color:var(--muted)')}
        ${cell(esc(tax?.desc || '—'))}
        ${cell(esc(addr ? [addr.city, addr.state].filter(Boolean).join(', ') : '—'), 'color:var(--muted)')}
        ${cell(b.status === 'A' ? '<span style="color:var(--green)">Active</span>' : `<span style="color:var(--red)">${esc(b.status || 'Unknown')}</span>`)}
      </tr>`;
    }).join('')}</tbody>`;

    resultsEl.innerHTML =
      `<div style="font-size:12px;color:var(--muted);margin-bottom:10px">
         ${results.length.toLocaleString()} result${results.length !== 1 ? 's' : ''} across ${zips.length} zip code${zips.length !== 1 ? 's' : ''}
       </div>` +
      `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">${thead}${tbody}</table></div>`;

  } catch (e) {
    resultsEl.innerHTML = `<div style="color:var(--red);padding:8px 0;font-size:12px">Request failed: ${esc(e.message)}</div>`;
  }
}

