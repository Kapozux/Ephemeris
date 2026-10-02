// 几块界面共用的状态，只有这几样；各块自己的状态留在自己的文件里。
export const S = {
  days: [],        // /api/days：日历上的每一天
  byDate: {},      // 同上，按日期索引
  sel: null,       // 正看着的那天
  mode: 'day',     // 侧栏模式：day（按天）| win（按窗口）
  wsel: null,      // 按窗口模式下正看着的窗口
};

export function setDays(days) {
  S.days = days;
  S.byDate = {};
  days.forEach(x => S.byDate[x.date] = x);
}

// 日历数据变了（补全跑完、导入了新文件）：改的人喊一声，main.js 统一重拉、重画
export const daysChanged = () => document.dispatchEvent(new Event('days-changed'));
