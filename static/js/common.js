// KnowLearn 前端公共工具：接口调用、轻提示、服务状态指示（交互设计第 1 章）
const API = '/api/v1';

class ApiError extends Error {
  constructor(code, message, status) { super(message); this.code = code; this.status = status; }
}

async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(API + path, options);
  } catch (e) {
    throw new ApiError(-1, '网络异常，请稍后重试', 0);
  }
  let body = null;
  try { body = await res.json(); } catch (e) { /* 非 JSON */ }
  if (!body || body.code !== 0) {
    throw new ApiError(body ? body.code : res.status, (body && body.message) || `请求失败（HTTP ${res.status}）`, res.status);
  }
  return body.data;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

let toastTimer = null;
function toast(msg, isError = false) {
  let el = document.getElementById('toast');
  if (!el) { el = document.createElement('div'); el.id = 'toast'; el.className = 'toast'; document.body.appendChild(el); }
  el.textContent = msg;
  el.classList.toggle('err', isError);
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2000);
}

function fmtDuration(ms) {
  if (ms == null) return '—';
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} 秒`;
  return `${Math.floor(s / 60)} 分 ${String(Math.round(s % 60)).padStart(2, '0')} 秒`;
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso), p = n => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// 服务状态：加载时 1 次，之后每 30 秒 1 次（交互决策 I2）
const COMPONENT_NAMES = { milvus: 'Milvus', mongodb: 'MongoDB', minio: 'MinIO', embedding_model: '向量模型', rerank_model: '重排模型' };
async function checkHealth() {
  const el = document.getElementById('health');
  if (!el) return;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 5000);
    const data = await api('/health', { signal: ctrl.signal });
    clearTimeout(timer);
    const bad = Object.entries(data.components).filter(([, v]) => v !== 'ok');
    el.className = 'health ' + (data.status === 'ok' ? 'ok' : 'degraded');
    el.innerHTML = `<i class="dot"></i>${data.status === 'ok' ? '服务正常' : '部分服务异常'}`;
    el.title = bad.length ? bad.map(([k, v]) => `${COMPONENT_NAMES[k] || k}：${v}`).join('\n') : '所有组件正常';
  } catch (e) {
    el.className = 'health down';
    el.innerHTML = '<i class="dot"></i>无法连接服务';
    el.title = '';
  }
}
document.addEventListener('DOMContentLoaded', () => { checkHealth(); setInterval(checkHealth, 30000); });
