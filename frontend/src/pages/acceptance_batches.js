import {request} from '../api.js';
import {actionForm} from '../controls.js';
import {el} from '../dom.js';
import {dataView, status, titleBlock} from '../widgets.js';

const page = {
  title: '验收批覆盖率工作台',
  module: '验收批与首测覆盖率',
  caption: '验收批覆盖率工作台',
  detail: '按色样册开验收批，圈点时刻锁定各试色位预测区间。覆盖率只统计首测点，超差首测计入分母；先交复样的点仍算缺测。封批可带缺测发布，晚到首测补入原批并另发一版，同回执重试不重复计入。',
  path: '/acceptance_batches',
  layout: 'stack',
  actions: [
    {
      name: 'open_batch',
      label: '按色样册开验收批',
      path: '/acceptance_batches/open-batch',
      fields: [
        {name: 'code', label: '验收批号', kind: 'text', initial: 'BATCH-SAND-01'},
        {name: 'family', label: '色样册', kind: 'text', initial: 'SAND-BATCH'}
      ],
      help: '色样册须已有干样凭证；开批后才能圈定未测试色位。',
      roles: ['operator', 'reviewer'],
      success: '已开批'
    },
    {
      name: 'add_slot',
      label: '圈定未测试色位并锁区间',
      path: '/acceptance_batches/add-slot',
      fields: [
        {name: 'batch', label: '验收批号', kind: 'text', initial: 'BATCH-SAND-01'},
        {name: 'study', label: '签认簿号', kind: 'text', initial: 'COLOR-REF'}
      ],
      help: '按签认点坐标入批，同时固化该点 95% 预测区间；封批后不能再圈点。',
      roles: ['operator', 'reviewer'],
      success: '已圈入'
    },
    {
      name: 'register_reading',
      label: '登记一条首测/复样读数',
      path: '/acceptance_batches/register-reading',
      fields: [
        {name: 'batch', label: '验收批号', kind: 'text', initial: 'BATCH-SAND-01'},
        {name: 'site_key', label: '样张坐标索引', kind: 'text', initial: 'PANEL-W8:94:61'},
        {name: 'reading_kind', label: '读数类型', kind: 'text', initial: '首测'},
        {name: 'delta_e', label: '实测 ΔE76', kind: 'float', initial: 1.57},
        {name: 'receipt', label: '回执编号', kind: 'text', initial: 'PROOF-QA11'}
      ],
      help: '首测决定覆盖率与缺测状态，复样只留痕不顶替首测；同回执重试幂等。',
      roles: ['operator', 'reviewer'],
      success: '已登记'
    },
    {
      name: 'register_readings',
      label: '同时提交一批读数',
      path: '/acceptance_batches/register-readings',
      fields: [
        {
          name: 'readings', label: '读数清单 JSON', kind: 'json',
          initial: [
            {site_key: 'PANEL-W8:94:61', reading_kind: '首测', delta_e: 1.57, receipt: 'PROOF-QA11'},
            {site_key: 'PANEL-W8:32:21', reading_kind: '首测', delta_e: 2.81, receipt: 'PROOF-QA12'}
          ]
        },
        {name: 'batch', label: '验收批号', kind: 'text', initial: 'BATCH-SAND-01'}
      ],
      help: '同一请求里的多个点在一个事务内全部计入；整单回执重试时不重复计入。',
      roles: ['operator', 'reviewer'],
      success: '已登记'
    },
    {
      name: 'seal_batch',
      label: '封批发布（可带缺测）',
      path: '/acceptance_batches/seal-batch',
      fields: [
        {name: 'batch', label: '验收批号', kind: 'text', initial: 'BATCH-SAND-01'}
      ],
      help: '生成第 1 版快照：实测数、覆盖率、命中/超差与缺测点清单。',
      roles: ['reviewer'],
      success: '已封批'
    },
    {
      name: 'reissue_batch',
      label: '晚到首测补发版本',
      path: '/acceptance_batches/reissue-batch',
      fields: [
        {name: 'batch', label: '验收批号', kind: 'text', initial: 'BATCH-SAND-01'}
      ],
      help: '仅当封批后有新的首测补入原批时另发一版；纯复样或同回执重试不发版，旧版与试色签收照旧保留。',
      roles: ['reviewer'],
      success: '已补发'
    }
  ]
};

const batchColumns = [
  {name: 'code', label: '验收批号'},
  {name: 'family', label: '色样册'},
  {name: 'state', label: '批状态'},
  {name: 'current_version', label: '当前版次'}
];

const versionColumns = [
  {name: 'version_no', label: '版次'},
  {name: 'sealed_at', label: '发布时间(UTC)'},
  {name: 'total_slots', label: '圈定点数'},
  {name: 'measured_slots', label: '首测实测数'},
  {name: 'hits', label: '区间内'},
  {name: 'misses', label: '超差'},
  {name: 'coverage', label: '首测覆盖率'},
  {name: 'missing_slots', label: '缺测点数'},
  {name: 'missing_site_keys', label: '缺测试色位'}
];

const slotColumns = [
  {name: 'site_key', label: '样张坐标索引'},
  {name: 'study', label: '签认簿'},
  {name: 'lower_de', label: '锁定区间下端'},
  {name: 'upper_de', label: '锁定区间上端'},
  {name: 'first_de', label: '首测 ΔE76'},
  {name: 'first_hit', label: '首测是否在区间内'},
  {name: 'first_receipt', label: '首测回执'}
];

const readingColumns = [
  {name: 'id', label: '流水号'},
  {name: 'site_key', label: '样张坐标索引'},
  {name: 'reading_kind', label: '读数类型'},
  {name: 'delta_e', label: 'ΔE76'},
  {name: 'receipt', label: '回执编号'}
];

function tableBox(title, rows, columns) {
  return el('section', {class: 'ledger'},
    el('h2', {}, title),
    dataView(rows, columns, 'table'));
}

export const {title} = page;
export const key = 'acceptance_batches';

export async function render(root, role) {
  const queryInput = el('input', {type: 'text', value: 'BATCH-SAND-01', 'aria-label': '验收批号'});
  const drill = el('div', {class: 'drill'});
  const notice = status('');

  async function loadDrill() {
    const code = queryInput.value.trim();
    if (!code) {
      drill.replaceChildren(status('输入验收批号后查询各版快照与台账。'));
      return;
    }
    try {
      const [versions, slots, readings] = await Promise.all([
        request(`/acceptance_batches/versions?batch=${encodeURIComponent(code)}`),
        request(`/acceptance_batches/slots?batch=${encodeURIComponent(code)}`),
        request(`/acceptance_batches/readings?batch=${encodeURIComponent(code)}`)
      ]);
      drill.replaceChildren(
        tableBox(`版本快照 · ${code}`, versions, versionColumns),
        tableBox(`试色位与锁定区间 · ${code}`, slots, slotColumns),
        tableBox(`读数流水（首测/复样）· ${code}`, readings, readingColumns));
    } catch (error) {
      drill.replaceChildren(status(error.message, 'error'));
    }
  }

  const batchPanel = el('section', {class: 'ledger', 'aria-label': page.caption});
  const refreshBatches = async () => {
    try {
      const rows = await request(page.path);
      batchPanel.replaceChildren(el('h2', {}, page.caption), dataView(rows, batchColumns, 'table'));
    } catch (error) {
      batchPanel.replaceChildren(status(error.message, 'error'));
    }
  };

  const refreshAll = async () => {
    await refreshBatches();
    await loadDrill();
  };

  const actions = page.actions.filter(x => x.roles.includes(role));
  root.replaceChildren(
    titleBlock(page.module, page.title, page.detail),
    el('div', {class: 'work stack'},
      el('div', {},
        batchPanel,
        el('section', {class: 'ledger'},
          el('h2', {}, '单批工作台查询'),
          el('form', {class: 'query-bar'},
            el('label', {}, el('span', {}, '验收批号'), queryInput),
            el('button', {type: 'submit'}, '查询每版实测数/覆盖率/缺测点')),
          notice, drill)),
      actions.length ? el('section', {class: 'actions'},
        actions.map(x => actionForm(x, refreshAll))) : null));

  queryInput.closest('form').addEventListener('submit', event => {
    event.preventDefault();
    loadDrill();
  });
  await refreshAll();
}
