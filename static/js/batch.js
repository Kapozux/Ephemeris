// 侧栏的「补全 N 天的日记」：按钮、进度条、停止。后端是 diary.py 的 diary-batch 任务。
import { $, ask } from './util.js';
import { S, daysChanged } from './state.js';

let BATCHPOLL = null;

export function refreshBatchButton() {
  const missing = S.days.filter(d => !d.has_diary && (d.chars || 0) >= 800).length;
  const box = $('#batch');
  box.dataset.want = missing ? '1' : '';          // 「按窗口」模式下要藏起来，但别忘了本来该不该显示
  if (!missing) { box.classList.add('hidden'); return; }
  if (S.mode === 'day') box.classList.remove('hidden');
  if (!$('#batchBtn').dataset.stop) $('#batchBtn').textContent = `补全 ${missing} 天的日记 · 约 $${(missing * 0.01).toFixed(2)}`;
}

async function pollBatch() {
  const s = await fetch('/api/diary/batch').then(r => r.json());
  const bar = $('#batchBar');
  if (s.running) {
    bar.classList.remove('hidden');
    const p = s.total ? ((s.done + s.failed) / s.total * 100) : 0;
    bar.querySelector('i').style.transform = `scaleX(${p / 100})`;
    bar.querySelector('span').textContent = `${s.done + s.failed}/${s.total}　${s.current || ''}`;
    $('#batchBtn').textContent = '停止';
    $('#batchBtn').dataset.stop = '1';
    return;
  }
  clearInterval(BATCHPOLL); BATCHPOLL = null;
  bar.classList.add('hidden');
  delete $('#batchBtn').dataset.stop;
  refreshBatchButton();
  daysChanged();                       // 跑完重新拉一次日历，把新标记全部落上
}

// 打开页面时补全已经在跑（比如早上的流程正在写）：接着盯进度
export async function watchBatch() {
  const s = await fetch('/api/diary/batch').then(r => r.json());
  if (s.running) { BATCHPOLL = setInterval(pollBatch, 3000); pollBatch(); }
}

$('#batchBtn').onclick = async () => {
  if ($('#batchBtn').dataset.stop) { await fetch('/api/diary/batch', { method: 'DELETE' }); return; }
  const missing = S.days.filter(d => !d.has_diary && (d.chars || 0) >= 800).length;
  // 花钱的：跟别处一样先在原地问一句（util.ask），不用浏览器的 confirm
  ask($('#batchBtn'), `给 ${missing} 天各写一篇，顺序跑，约 ${Math.ceil(missing * 20 / 60)} 分钟、$${(missing * 0.01).toFixed(2)}`, async () => {
    await fetch('/api/diary/batch', { method: 'POST' });
    BATCHPOLL = setInterval(pollBatch, 3000); pollBatch();
  });
};
