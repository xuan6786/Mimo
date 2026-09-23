/* ==========================================================================
   MiMo 助手 · 网页版前端逻辑（原生 JS，无任何第三方库）
   ========================================================================== */
'use strict';

/* ------------------------------------------------------------------ 工具 -- */

const $ = (id) => document.getElementById(id);

const uid = () => Math.random().toString(36).slice(2, 9) + Date.now().toString(36).slice(-4);

function escapeHtml(str) {
  return String(str == null ? '' : str).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function fmtTime(ts) {
  const d = new Date(ts || Date.now());
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtNum(n) {
  if (n == null) return '0';
  return n >= 10000 ? (n / 1000).toFixed(1) + 'k' : String(n);
}

function toast(message, kind) {
  const box = $('toasts');
  const node = document.createElement('div');
  node.className = 'toast' + (kind ? ' ' + kind : '');
  node.textContent = message;
  box.appendChild(node);
  setTimeout(() => {
    node.style.transition = 'opacity .25s ease, transform .25s ease';
    node.style.opacity = '0';
    node.style.transform = 'translateX(14px)';
    setTimeout(() => node.remove(), 260);
  }, kind === 'err' ? 5200 : 2600);
}

async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    toast('已复制到剪贴板', 'ok');
    return true;
  } catch (err) {
    toast('复制失败，请手动选择文本', 'err');
    return false;
  }
}

/* -------------------------------------------------------- 代码高亮 -- */

const KEYWORDS = {
  js: 'const let var function return if else for while do switch case break continue class new import from export default async await try catch finally throw typeof instanceof of in delete void yield static extends super this null undefined true false NaN Infinity',
  ts: 'const let var function return if else for while do switch case break continue class new import from export default async await try catch finally throw typeof instanceof of in delete void yield static extends super this null undefined true false interface type enum implements public private protected readonly declare namespace as any unknown never string number boolean',
  py: 'def class return if elif else for while import from as pass break continue try except finally raise with lambda yield global nonlocal assert del in is not and or None True False async await match case',
  java: 'public private protected class interface extends implements new return if else for while do switch case break continue try catch finally throw throws static final void int long double float boolean char String var this super import package null true false instanceof enum abstract synchronized',
  go: 'package import func var const type struct interface map chan return if else for range switch case break continue go defer select nil true false make new len cap append',
  rust: 'fn let mut const static struct enum impl trait for while loop if else match return use pub mod crate self super where as dyn ref move async await unsafe Some None Ok Err true false',
  c: 'int char float double void long short unsigned signed struct union enum typedef static const extern return if else for while do switch case break continue sizeof goto include define NULL true false',
  sh: 'if then else elif fi for while do done case esac function return exit echo export source local read set unset cd ls cat grep sed awk rm mkdir touch chmod sudo npm pip python git',
  json: 'true false null',
  sql: 'select from where group by order having insert into values update set delete create table alter drop join left right inner outer on as and or not null limit offset distinct count sum avg min max',
  css: '',
  yaml: 'true false null',
  php: 'function return if else elseif for foreach while do switch case break continue class extends implements new echo print public private protected static const use namespace try catch throw null true false',
};

const COMMENT_RE = {
  js: '\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/',
  ts: '\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/',
  java: '\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/',
  go: '\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/',
  rust: '\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/',
  c: '\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/',
  css: '\\/\\*[\\s\\S]*?\\*\\/',
  py: '#[^\\n]*',
  sh: '#[^\\n]*',
  yaml: '#[^\\n]*',
  php: '\\/\\/[^\\n]*|#[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/',
  sql: '--[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/',
};

const LANG_ALIAS = {
  javascript: 'js', jsx: 'js', node: 'js', mjs: 'js', cjs: 'js',
  typescript: 'ts', tsx: 'ts',
  python: 'py', py3: 'py',
  golang: 'go', rs: 'rust', cpp: 'c', 'c++': 'c', h: 'c', hpp: 'c',
  bash: 'sh', shell: 'sh', zsh: 'sh', console: 'sh', powershell: 'sh', ps1: 'sh',
  yml: 'yaml', json5: 'json', jsonc: 'json',
  html: 'html', xml: 'html', vue: 'html', svg: 'html',
  scss: 'css', less: 'css',
};

const STRING_PATTERN = '"(?:[^"\\\\\\n]|\\\\.)*"|\'(?:[^\'\\\\\\n]|\\\\.)*\'|`(?:[^`\\\\]|\\\\.)*`';

function highlightHtml(code) {
  // HTML/XML：标签名、属性名、字符串、注释
  const re = /(<!--[\s\S]*?-->)|(<\/?[a-zA-Z][\w:-]*)|([a-zA-Z-]+)(?==)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g;
  let out = '';
  let last = 0;
  let m;
  while ((m = re.exec(code)) !== null) {
    out += escapeHtml(code.slice(last, m.index));
    if (m[1]) out += '<span class="tok-com">' + escapeHtml(m[1]) + '</span>';
    else if (m[2]) out += '<span class="tok-key">' + escapeHtml(m[2]) + '</span>';
    else if (m[3]) out += '<span class="tok-fn">' + escapeHtml(m[3]) + '</span>';
    else out += '<span class="tok-str">' + escapeHtml(m[4]) + '</span>';
    last = m.index + m[0].length;
  }
  out += escapeHtml(code.slice(last));
  return out;
}

function highlight(code, rawLang) {
  const lang = LANG_ALIAS[String(rawLang || '').toLowerCase()] || String(rawLang || '').toLowerCase();
  if (lang === 'html') return highlightHtml(code);

  const kw = KEYWORDS[lang];
  if (!kw) return escapeHtml(code);

  const parts = [];
  const comment = COMMENT_RE[lang];
  if (comment) parts.push('(' + comment + ')');
  else parts.push('(\\u0000)'); // 占位，保证分组下标一致
  parts.push('(' + STRING_PATTERN + ')');
  parts.push('(\\b\\d+(?:\\.\\d+)?\\b)');
  if (kw.trim()) parts.push('(\\b(?:' + kw.trim().split(/\s+/).join('|') + ')\\b)');
  else parts.push('(\\u0000)');

  let re;
  try {
    re = new RegExp(parts.join('|'), 'g');
  } catch (err) {
    return escapeHtml(code);
  }

  let out = '';
  let last = 0;
  let m;
  while ((m = re.exec(code)) !== null) {
    if (m[0] === '') { re.lastIndex++; continue; }
    out += escapeHtml(code.slice(last, m.index));
    let cls = '';
    if (m[1]) cls = 'tok-com';
    else if (m[2]) cls = 'tok-str';
    else if (m[3]) cls = 'tok-num';
    else if (m[4]) cls = 'tok-key';
    out += cls ? '<span class="' + cls + '">' + escapeHtml(m[0]) + '</span>' : escapeHtml(m[0]);
    last = m.index + m[0].length;
  }
  out += escapeHtml(code.slice(last));
  return out;
}

/* -------------------------------------------------------- Markdown -- */

function inlineFormat(text) {
  const codes = [];
  let t = text.replace(/`([^`\n]+)`/g, (m, c) => {
    codes.push(c);
    return '\u0001C' + (codes.length - 1) + '\u0001';
  });

  t = escapeHtml(t);

  // 链接 [文本](url)
  t = t.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  // 裸链接
  t = t.replace(/(^|[\s(])(https?:\/\/[^\s<>()]+[^\s<>().,;:!?])/g,
    '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  t = t.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  t = t.replace(/(^|[^*\w])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  t = t.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
  t = t.replace(/\u0001C(\d+)\u0001/g,
    (m, i) => '<code class="inline">' + escapeHtml(codes[Number(i)] || '') + '</code>');
  return t;
}

function renderMarkdown(src) {
  let text = String(src == null ? '' : src).replace(/\r\n?/g, '\n');

  // 1) 抽出围栏代码块
  const blocks = [];
  text = text.replace(/```([^\n`]*)\n?([\s\S]*?)(?:```|$)/g, (m, lang, code) => {
    blocks.push({ lang: String(lang || '').trim(), code: code.replace(/\n+$/, '') });
    return '\n\u0000BLOCK' + (blocks.length - 1) + '\u0000\n';
  });

  const out = [];
  const lines = text.split('\n');
  const stack = [];
  let para = [];
  let i = 0;

  const flushPara = () => {
    if (para.length) {
      out.push('<p>' + para.map(inlineFormat).join('<br>') + '</p>');
      para = [];
    }
  };
  const closeLevel = () => {
    const top = stack.pop();
    if (!top) return;
    if (top.li) out.push('</li>');
    out.push('</' + top.type + '>');
  };
  const closeLists = () => { while (stack.length) closeLevel(); };
  const openList = (type, indent) => {
    flushPara();
    while (stack.length) {
      const top = stack[stack.length - 1];
      if (indent > top.indent) { stack.push({ type, indent, li: false }); out.push('<' + type + '>'); return; }
      if (indent === top.indent && top.type === type) return;
      closeLevel();
    }
    stack.push({ type, indent, li: false });
    out.push('<' + type + '>');
  };
  const addLi = (html) => {
    const top = stack[stack.length - 1];
    if (!top) return;
    if (top.li) out.push('</li>');
    out.push('<li>' + html);
    top.li = true;
  };

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // 代码块占位
    const blockMatch = trimmed.match(/^\u0000BLOCK(\d+)\u0000$/);
    if (blockMatch) {
      flushPara();
      closeLists();
      const blk = blocks[Number(blockMatch[1])];
      out.push(renderCodeBlock(blk.lang, blk.code));
      i++;
      continue;
    }

    // 空行
    if (!trimmed) {
      flushPara();
      closeLists();
      i++;
      continue;
    }

    // 标题
    const h = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushPara();
      closeLists();
      const level = Math.min(h[1].length, 4);
      out.push('<h' + level + '>' + inlineFormat(h[2].replace(/\s+#+\s*$/, '')) + '</h' + level + '>');
      i++;
      continue;
    }

    // 分隔线
    if (/^([-*_])\s*(\1\s*){2,}$/.test(trimmed)) {
      flushPara();
      closeLists();
      out.push('<hr>');
      i++;
      continue;
    }

    // 表格
    if (trimmed.includes('|') && i + 1 < lines.length && /^\s*\|?[\s:|-]*-[\s:|-]*\|?[\s:|-]*$/.test(lines[i + 1])) {
      const sep = lines[i + 1];
      if (/\|/.test(sep) || /-{2,}/.test(sep)) {
        flushPara();
        closeLists();
        const parseRow = (row) => row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
        const header = parseRow(trimmed);
        const aligns = parseRow(sep).map((c) => {
          const l = c.startsWith(':');
          const r = c.endsWith(':');
          if (l && r) return 'center';
          if (r) return 'right';
          if (l) return 'left';
          return '';
        });
        let html = '<table><thead><tr>';
        header.forEach((cell, idx) => {
          const a = aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : '';
          html += '<th' + a + '>' + inlineFormat(cell) + '</th>';
        });
        html += '</tr></thead><tbody>';
        i += 2;
        while (i < lines.length && lines[i].trim() && lines[i].includes('|')) {
          const cells = parseRow(lines[i].trim());
          html += '<tr>';
          cells.forEach((cell, idx) => {
            const a = aligns[idx] ? ' style="text-align:' + aligns[idx] + '"' : '';
            html += '<td' + a + '>' + inlineFormat(cell) + '</td>';
          });
          html += '</tr>';
          i++;
        }
        html += '</tbody></table>';
        out.push(html);
        continue;
      }
    }

    // 引用
    if (/^>\s?/.test(trimmed)) {
      flushPara();
      closeLists();
      const buf = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        buf.push(lines[i].trim().replace(/^>\s?/, ''));
        i++;
      }
      out.push('<blockquote>' + buf.map((l) => (l ? inlineFormat(l) : '')).join('<br>') + '</blockquote>');
      continue;
    }

    // 列表
    const ul = line.match(/^(\s*)[-*+]\s+(.*)$/);
    const ol = line.match(/^(\s*)\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      const indent = (ul ? ul[1] : ol[1]).replace(/\t/g, '  ').length;
      const content = ul ? ul[2] : ol[2];
      openList(ul ? 'ul' : 'ol', indent);
      addLi(inlineFormat(content));
      i++;
      continue;
    }

    // 普通段落
    closeLists();
    para.push(trimmed);
    i++;
  }

  flushPara();
  closeLists();
  return out.join('\n');
}

function renderCodeBlock(lang, code) {
  const label = lang || 'text';
  return '<div class="code-block">' +
    '<div class="code-head"><span class="lang">' + escapeHtml(label) + '</span>' +
    '<button class="copy" data-code="' + encodeURIComponent(code) + '">复制</button></div>' +
    '<pre><code>' + highlight(code, lang) + '</code></pre></div>';
}

/* ------------------------------------------------------------- 状态 -- */

const LS = {
  convs: 'mimo.convs',
  current: 'mimo.current',
  theme: 'mimo.theme',
  prefs: 'mimo.prefs',
};

const store = {
  config: null,
  convs: [],
  currentId: null,
  streaming: false,
  controller: null,
  prefs: { showReasoning: true, reasonEcho: true, search: false, tools: true },
};

function currentConv() {
  let conv = store.convs.find((c) => c.id === store.currentId);
  if (!conv) {
    conv = newConv();
    store.convs.unshift(conv);
    store.currentId = conv.id;
  }
  return conv;
}

function newConv() {
  return {
    id: uid(),
    title: '新对话',
    model: (store.config && store.config.model) || '',
    createdAt: Date.now(),
    messages: [],
  };
}

function saveState() {
  const write = (convs) => {
    localStorage.setItem(LS.convs, JSON.stringify(convs));
    localStorage.setItem(LS.current, store.currentId || '');
    localStorage.setItem(LS.prefs, JSON.stringify(store.prefs));
  };
  try {
    write(store.convs.slice(0, 60));
  } catch (err) {
    // 多半是超出 localStorage 配额（工具返回内容比较占地方），逐步丢老会话
    try {
      write(store.convs.slice(0, 8));
    } catch (err2) {
      /* 还是失败就放弃本次写入，不打断对话 */
    }
  }
}

function loadState() {
  try {
    const convs = JSON.parse(localStorage.getItem(LS.convs) || '[]');
    store.convs = Array.isArray(convs) ? convs : [];
    store.currentId = localStorage.getItem(LS.current) || null;
    const prefs = JSON.parse(localStorage.getItem(LS.prefs) || '{}');
    Object.assign(store.prefs, prefs || {});
  } catch (err) {
    store.convs = [];
  }
  if (!store.convs.length) {
    store.convs = [newConv()];
  }
  if (!store.convs.some((c) => c.id === store.currentId)) {
    store.currentId = store.convs[0].id;
  }
}

/* --------------------------------------------------------- 消息渲染 -- */

function buildMessageNode(msg, index, canRegenerate) {
  const isUser = msg.role === 'user';
  const wrap = document.createElement('div');
  wrap.className = 'msg ' + (isUser ? 'user' : 'assistant');
  wrap.dataset.index = String(index);

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = isUser ? '你' : 'M';
  wrap.appendChild(avatar);

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  if (!isUser) {
    const head = document.createElement('div');
    head.className = 'bubble-head';
    head.innerHTML = '<span class="who">MiMo</span>' +
      '<span class="meta">' + escapeHtml(msg.model || (store.config && store.config.model) || '') +
      (msg.ts ? ' · ' + fmtTime(msg.ts) : '') + '</span>';
    head.appendChild(buildActions(msg, index, canRegenerate));
    bubble.appendChild(head);
  }

  const body = document.createElement('div');
  body.className = 'msg-body';
  bubble.appendChild(body);

  const usage = document.createElement('div');
  usage.className = 'usage-line';
  usage.hidden = true;
  bubble.appendChild(usage);

  const errBox = document.createElement('div');
  errBox.className = 'err-box';
  errBox.hidden = true;
  bubble.appendChild(errBox);

  wrap.appendChild(bubble);

  const node = {
    wrap, bubble, body, usage, errBox, reasoningEl: null,
    md: null,
    // 取当前文本块，没有就新建一个（工具调用会把文本块「切断」）
    textEl() {
      if (!this.md) {
        this.md = document.createElement('div');
        this.md.className = 'md';
        this.body.appendChild(this.md);
      }
      return this.md;
    },
    closeText() {
      this.md = null;
    },
  };

  if (isUser) {
    const el = node.textEl();
    el.textContent = msg.content || '';
    el.style.whiteSpace = 'pre-wrap';
  } else if (Array.isArray(msg.parts) && msg.parts.length) {
    // 从历史记录重建：按顺序还原文本块与工具卡片
    msg.parts.forEach((part) => {
      if (part.kind === 'text') {
        const el = node.textEl();
        el.innerHTML = renderMarkdown(part.text || '');
        node.closeText();
      } else if (part.kind === 'tool') {
        appendToolCard(node, part);
      }
    });
    if (msg.content && !msg.parts.some((p) => p.kind === 'text' && p.text)) {
      node.textEl().innerHTML = renderMarkdown(msg.content);
    }
  } else {
    node.textEl().innerHTML = renderMarkdown(msg.content || '');
  }

  return node;
}

/* --------------------------------------------------- 工具调用卡片 -- */

const TOOL_ICON = '<svg viewBox="0 0 16 16"><path d="M6.5 2.5a2.6 2.6 0 013.4 3.4l4 4a1.4 1.4 0 01-2 2l-4-4A2.6 2.6 0 014.5 4.5l1.7 1.7 1.3-1.3z"/></svg>';

function toolStatusText(part) {
  if (part.status === 'running') return '执行中…';
  const time = part.elapsed != null ? ' · ' + Number(part.elapsed).toFixed(2) + 's' : '';
  return (part.status === 'error' ? '失败' : '完成') + time;
}

function appendToolCard(node, part) {
  const card = document.createElement('div');
  card.className = 'tool-card' + (part.status === 'error' ? ' error' : '');

  const head = document.createElement('div');
  head.className = 'tool-head';
  head.innerHTML = TOOL_ICON +
    '<span class="tool-name">' + escapeHtml(part.name || '工具') + '</span>' +
    (part.source ? '<span class="tool-source">' + escapeHtml(part.source) + '</span>' : '') +
    '<span class="tool-status">' + escapeHtml(toolStatusText(part)) + '</span>' +
    '<span class="tool-caret">›</span>';
  card.appendChild(head);

  const body = document.createElement('div');
  body.className = 'tool-body';

  const argsLabel = document.createElement('div');
  argsLabel.className = 'tool-label';
  argsLabel.textContent = '参数';
  const args = document.createElement('pre');
  args.className = 'tool-args';
  args.textContent = JSON.stringify(part.args || {}, null, 2);
  body.appendChild(argsLabel);
  body.appendChild(args);

  const resLabel = document.createElement('div');
  resLabel.className = 'tool-label';
  resLabel.textContent = '返回';
  const res = document.createElement('pre');
  res.className = 'tool-result';
  res.textContent = part.result != null ? part.result : '…';
  body.appendChild(resLabel);
  body.appendChild(res);

  card.appendChild(body);

  head.addEventListener('click', () => card.classList.toggle('open'));

  node.body.appendChild(card);
  part._el = { card, head, body, res };
  return card;
}

function updateToolCard(part) {
  const ref = part && part._el;
  if (!ref) return;
  ref.card.classList.toggle('error', part.status === 'error');
  const status = ref.head.querySelector('.tool-status');
  if (status) status.textContent = toolStatusText(part);
  if (ref.res) ref.res.textContent = part.result != null ? part.result : '';
}

function buildActions(msg, index, canRegenerate) {
  const box = document.createElement('div');
  box.className = 'msg-actions';

  const mk = (title, svg, handler) => {
    const b = document.createElement('button');
    b.className = 'icon-btn';
    b.title = title;
    b.innerHTML = svg;
    b.addEventListener('click', handler);
    return b;
  };

  box.appendChild(mk('复制', '<svg viewBox="0 0 16 16"><rect x="5.5" y="5.5" width="8" height="8" rx="2"/><path d="M10.5 3.5H4a2 2 0 00-2 2v6"/></svg>', () => {
    copyText(msg.content || msg.reasoning || '');
  }));

  if (msg.role === 'assistant' && canRegenerate) {
    box.appendChild(mk('重新生成', '<svg viewBox="0 0 16 16"><path d="M13.5 8a5.5 5.5 0 11-1.7-4M13.5 2v4h-4"/></svg>', () => {
      regenerate(index);
    }));
  }

  box.appendChild(mk('删除', '<svg viewBox="0 0 16 16"><path d="M3 5h10M6.5 5V3h3v2M5 5l.8 8h4.4l.8-8"/></svg>', () => {
    const conv = currentConv();
    conv.messages.splice(index, 1);
    saveState();
    renderAll();
  }));

  return box;
}

function applyUsage(node, msg) {
  const parts = [];
  if (msg.usage) {
    parts.push('输入 ' + fmtNum(msg.usage.prompt_tokens) + ' tokens');
    parts.push('输出 ' + fmtNum(msg.usage.completion_tokens) + ' tokens');
    parts.push('合计 ' + fmtNum(msg.usage.total_tokens));
  }
  if (msg.finishReason && msg.finishReason !== 'stop') parts.push('结束原因：' + msg.finishReason);
  if (msg.elapsed) parts.push('耗时 ' + msg.elapsed.toFixed(1) + 's');
  node.usage.innerHTML = parts.map((p) => '<span>' + escapeHtml(p) + '</span>').join('');
  node.usage.hidden = parts.length === 0;
}

function ensureReasoning(node, msg, live) {
  if (!store.prefs.showReasoning || !msg.reasoning) return;
  if (!node.reasoningEl) {
    const details = document.createElement('details');
    details.className = 'reasoning' + (live ? ' live' : '');
    details.open = true;
    const summary = document.createElement('summary');
    const body = document.createElement('div');
    body.className = 'r-body';
    details.appendChild(summary);
    details.appendChild(body);
    node.bubble.insertBefore(details, node.body);
    node.reasoningEl = { details, summary, body };
  }
  const ref = node.reasoningEl;
  ref.body.textContent = msg.reasoning;
  ref.summary.textContent = live ? '正在思考…' : '已深度思考（' + msg.reasoning.length + ' 字）';
  if (live) {
    ref.details.open = true;
    ref.body.scrollTop = ref.body.scrollHeight;
  }
}

/* --------------------------------------------------------- 整体渲染 -- */

function renderAll() {
  const conv = currentConv();
  const box = $('messages');
  box.innerHTML = '';

  $('convTitle').value = conv.title || '新对话';
  $('modelSelect').value = conv.model || (store.config && store.config.model) || '';

  if (!store.config || !store.config.has_key) {
    const warn = document.createElement('div');
    warn.className = 'warn-bar';
    warn.innerHTML = '<span>还没有配置 API Key，无法发起对话。</span>';
    const btn = document.createElement('button');
    btn.textContent = '去设置';
    btn.addEventListener('click', openSettings);
    warn.appendChild(btn);
    box.appendChild(warn);
  }

  if (!conv.messages.length) {
    box.appendChild(buildEmpty());
  } else {
    conv.messages.forEach((msg, index) => {
      const node = buildMessageNode(msg, index, index === conv.messages.length - 1);
      box.appendChild(node.wrap);
      if (msg.reasoning) ensureReasoning(node, msg, false);
      if (msg.error) {
        node.errBox.textContent = msg.error;
        node.errBox.hidden = false;
      }
      applyUsage(node, msg);
    });
  }

  renderConvList();
  renderStat();
  bindCodeCopy();
  scrollToBottom(false);
}

function buildEmpty() {
  const box = document.createElement('div');
  box.className = 'empty';
  box.innerHTML =
    '<div class="logo">M</div>' +
    '<h2>我是 MiMo，小米研发的 AI 助手</h2>' +
    '<p>可以问我任何问题，也可以点下面的示例快速开始</p>';
  const cards = document.createElement('div');
  cards.className = 'cards';
  const samples = [
    ['写一段代码', '用 Python 写一个带重试的 HTTP 请求函数'],
    ['解释概念', '用通俗的例子解释什么是向量数据库'],
    ['翻译润色', '把下面这段话润色得更专业：这个方案我觉得还行'],
    ['分析数据', '给一份电商用户留存分析的分析框架'],
  ];
  samples.forEach(([title, text]) => {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = '<b>' + escapeHtml(title) + '</b><span>' + escapeHtml(text) + '</span>';
    card.addEventListener('click', () => {
      $('input').value = text;
      autoGrow();
      $('input').focus();
    });
    cards.appendChild(card);
  });
  box.appendChild(cards);
  return box;
}

function renderConvList() {
  const list = $('convList');
  list.innerHTML = '';
  const sorted = store.convs.slice().sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
  sorted.forEach((conv) => {
    const item = document.createElement('div');
    item.className = 'conv-item' + (conv.id === store.currentId ? ' active' : '');
    const dot = document.createElement('span');
    dot.className = 'dot';
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = conv.title || '新对话';
    name.title = conv.title || '新对话';
    const del = document.createElement('button');
    del.className = 'del';
    del.title = '删除对话';
    del.innerHTML = '<svg viewBox="0 0 16 16"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9"/></svg>';
    del.addEventListener('click', (ev) => {
      ev.stopPropagation();
      removeConv(conv.id);
    });
    item.appendChild(dot);
    item.appendChild(name);
    item.appendChild(del);
    item.addEventListener('click', () => switchConv(conv.id));
    list.appendChild(item);
  });
}

function renderStat() {
  const conv = currentConv();
  const turns = conv.messages.filter((m) => m.role === 'user').length;
  const tokens = conv.messages.reduce((sum, m) => sum + ((m.usage && m.usage.total_tokens) || 0), 0);
  $('sessionStat').textContent = turns + ' 轮对话 · ' + fmtNum(tokens) + ' tokens';
}

function bindCodeCopy() {
  document.querySelectorAll('.code-head .copy').forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      let code = '';
      try { code = decodeURIComponent(btn.dataset.code || ''); } catch (e) { code = btn.dataset.code || ''; }
      copyText(code);
    });
  });
}

function scrollToBottom(force) {
  const box = $('messages');
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 140;
  if (force || nearBottom) {
    requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });
  }
}

/* --------------------------------------------------------- 会话操作 -- */

function switchConv(id) {
  if (store.streaming) {
    toast('正在生成中，请先停止或等待完成');
    return;
  }
  store.currentId = id;
  saveState();
  renderAll();
  closeSidebar();
}

function removeConv(id) {
  const idx = store.convs.findIndex((c) => c.id === id);
  if (idx < 0) return;
  if (store.streaming && store.convs[idx].id === store.currentId) {
    toast('正在生成中，无法删除当前对话', 'err');
    return;
  }
  store.convs.splice(idx, 1);
  if (!store.convs.length) store.convs.push(newConv());
  if (store.currentId === id) store.currentId = store.convs[0].id;
  saveState();
  renderAll();
  toast('已删除对话', 'ok');
}

function newChat() {
  if (store.streaming) stopStreaming();
  const conv = newConv();
  store.convs.unshift(conv);
  store.currentId = conv.id;
  saveState();
  renderAll();
  closeSidebar();
  $('input').focus();
}

function clearCurrent() {
  const conv = currentConv();
  if (!conv.messages.length) return;
  if (!window.confirm('确定清空当前对话的全部消息？此操作不可撤销。')) return;
  conv.messages = [];
  conv.title = '新对话';
  saveState();
  renderAll();
  toast('已清空当前对话', 'ok');
}

/* --------------------------------------------------------- 发送/流式 -- */

function buildApiMessages(conv) {
  const out = [];
  conv.messages.forEach((m) => {
    if (m.role === 'system') return;
    if (!m.content && !m.reasoning) return;
    const item = { role: m.role, content: m.content || '' };
    if (m.role === 'assistant' && store.prefs.reasonEcho && m.reasoning) {
      item.reasoning_content = m.reasoning;
    }
    out.push(item);
  });
  return out;
}

function setStreaming(on) {
  store.streaming = on;
  const btn = $('sendBtn');
  btn.classList.toggle('stop', on);
  btn.querySelector('span').textContent = on ? '停止' : '发送';
  $('hint').textContent = on ? '正在生成…' : '';
  $('titleSub').textContent = on ? '正在生成…' : '就绪';
}

function stopStreaming() {
  if (store.controller) {
    try { store.controller.abort(); } catch (err) { /* ignore */ }
  }
}

async function sendMessage(text) {
  if (store.streaming) return;
  const conv = currentConv();
  const content = String(text || '').trim();
  if (!content) return;

  if (!store.config || !store.config.has_key) {
    toast('请先在设置里配置 API Key', 'err');
    openSettings();
    return;
  }

  conv.messages.push({ role: 'user', content: content, ts: Date.now() });
  if (!conv.title || conv.title === '新对话') {
    conv.title = content.slice(0, 22) + (content.length > 22 ? '…' : '');
  }
  saveState();
  renderAll();
  scrollToBottom(true);
  await runCompletion();
}

async function runCompletion() {
  const conv = currentConv();
  const msg = {
    role: 'assistant',
    content: '',
    reasoning: '',
    parts: [],
    usage: null,
    model: conv.model || (store.config && store.config.model) || '',
    ts: Date.now(),
  };
  conv.messages.push(msg);
  const index = conv.messages.length - 1;

  const node = buildMessageNode(msg, index, true);
  $('messages').appendChild(node.wrap);
  bindCodeCopy();

  setStreaming(true);
  store.controller = new AbortController();

  const startedAt = Date.now();
  let renderPending = false;
  let sawError = false;
  let currentPart = null;

  // 追加一段正文；工具调用会把文本块「切断」，之后新开一块
  const pushText = (text) => {
    if (!currentPart || currentPart.kind !== 'text') {
      currentPart = { kind: 'text', text: '' };
      msg.parts.push(currentPart);
      node.closeText();
    }
    currentPart.text += text;
  };

  const paint = () => {
    renderPending = false;
    const el = node.textEl();
    const text = currentPart && currentPart.kind === 'text' ? currentPart.text : '';
    let html = renderMarkdown(text);
    if (store.streaming) html += '<span class="caret"></span>';
    if (!text && msg.reasoning) {
      html = '<p style="color:var(--text-dim)">思考中…</p>' + html;
    }
    el.innerHTML = html;
    if (msg.reasoning) ensureReasoning(node, msg, store.streaming);
    bindCodeCopy();
    scrollToBottom(false);
  };
  const schedulePaint = () => {
    if (renderPending) return;
    renderPending = true;
    requestAnimationFrame(paint);
  };

  // 收尾时按 parts 重建一次，保证 DOM 与数据一致（例如被中断的情况）
  const rebuildBody = () => {
    node.body.innerHTML = '';
    node.md = null;
    msg.parts.forEach((part) => {
      if (part.kind === 'text') {
        node.textEl().innerHTML = renderMarkdown(part.text || '');
        node.closeText();
      } else if (part.kind === 'tool') {
        appendToolCard(node, part);
      }
    });
    if (!msg.parts.length && msg.content) {
      node.textEl().innerHTML = renderMarkdown(msg.content);
    }
    bindCodeCopy();
  };

  const payload = {
    model: msg.model,
    messages: buildApiMessages(conv),
    stream: true,
  };
  if (store.prefs.search) payload.extra_body = { forced_search: true };
  payload.tools = store.prefs.tools !== false;

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: store.controller.signal,
    });

    if (!resp.ok) {
      let detail = 'HTTP ' + resp.status;
      try {
        const data = await resp.json();
        detail = data.error || data.message || detail;
      } catch (err) { /* ignore */ }
      throw new Error(detail);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let cut;
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          let ev;
          try { ev = JSON.parse(raw); } catch (err) { continue; }

          if (ev.type === 'reasoning') {
            msg.reasoning += ev.text || '';
            schedulePaint();
          } else if (ev.type === 'content') {
            msg.content += ev.text || '';
            pushText(ev.text || '');
            schedulePaint();
          } else if (ev.type === 'tool_call') {
            const part = {
              kind: 'tool',
              id: ev.id || ('tool_' + msg.parts.length),
              name: ev.name || '工具',
              source: ev.source || '',
              args: ev.arguments || {},
              status: 'running',
              result: '',
            };
            msg.parts.push(part);
            currentPart = null;
            node.closeText();
            appendToolCard(node, part);
            $('hint').textContent = '正在执行 ' + part.name + '…';
            scrollToBottom(false);
          } else if (ev.type === 'tool_result') {
            let part = null;
            for (let i = msg.parts.length - 1; i >= 0; i -= 1) {
              const item = msg.parts[i];
              if (item.kind === 'tool' && item.id === ev.id && item.status === 'running') { part = item; break; }
            }
            if (!part) {
              for (let i = msg.parts.length - 1; i >= 0; i -= 1) {
                const item = msg.parts[i];
                if (item.kind === 'tool' && item.name === ev.name) { part = item; break; }
              }
            }
            if (part) {
              part.status = ev.ok ? 'done' : 'error';
              part.result = String(ev.content == null ? '' : ev.content).slice(0, 8000);
              part.elapsed = ev.elapsed;
              updateToolCard(part);
            }
            scrollToBottom(false);
          } else if (ev.type === 'round') {
            $('hint').textContent = '第 ' + ev.index + ' 轮工具调用…';
          } else if (ev.type === 'notice') {
            (msg.notices = msg.notices || []).push(ev.message || '');
            $('hint').textContent = ev.message || '';
          } else if (ev.type === 'usage') {
            msg.usage = ev.usage || null;
          } else if (ev.type === 'retry') {
            $('hint').textContent = '第 ' + ev.attempt + ' 次重试…';
          } else if (ev.type === 'error') {
            sawError = true;
            msg.error = ev.message || '调用失败';
          } else if (ev.type === 'done') {
            if (ev.usage) msg.usage = ev.usage;
            if (ev.finish_reason) msg.finishReason = ev.finish_reason;
            if (ev.model) msg.model = ev.model;
            if (ev.rounds) msg.rounds = ev.rounds;
            if (ev.content && !msg.content) {
              msg.content = ev.content;
              pushText(ev.content);
            }
            if (ev.reasoning && !msg.reasoning) msg.reasoning = ev.reasoning;
          }
        }
      }
    }
  } catch (err) {
    if (err && err.name === 'AbortError') {
      msg.stopped = true;
    } else {
      sawError = true;
      msg.error = (err && err.message) || String(err);
    }
  } finally {
    setStreaming(false);
    store.controller = null;
    msg.elapsed = (Date.now() - startedAt) / 1000;

    // 还没结束的工具卡片标记为中断
    msg.parts.forEach((part) => {
      if (part.kind === 'tool' && part.status === 'running') {
        part.status = 'error';
        part.result = part.result || '（生成被中断，未拿到结果）';
      }
    });

    if (!msg.content && !msg.reasoning && !msg.error && !msg.parts.some((p) => p.kind === 'tool')) {
      msg.error = msg.stopped ? '已停止生成。' : '没有收到任何内容，请稍后重试。';
    }

    rebuildBody();
    if (msg.reasoning) ensureReasoning(node, msg, false);
    if (node.reasoningEl) {
      node.reasoningEl.details.classList.remove('live');
      node.reasoningEl.details.open = false;
      node.reasoningEl.summary.textContent = '已深度思考（' + msg.reasoning.length + ' 字）';
    }
    if (msg.error) {
      node.errBox.textContent = msg.error;
      node.errBox.hidden = false;
    }
    applyUsage(node, msg);
    bindCodeCopy();
    renderStat();
    saveState();
    scrollToBottom(false);
    if (sawError) toast('生成过程中出现错误，详情见消息下方', 'err');
  }
}

async function regenerate(index) {
  if (store.streaming) return;
  const conv = currentConv();
  if (index !== conv.messages.length - 1) {
    toast('只能重新生成最后一条回复', 'err');
    return;
  }
  const target = conv.messages[index];
  if (!target || target.role !== 'assistant') return;
  conv.messages.splice(index, 1);
  saveState();
  renderAll();
  await runCompletion();
}

/* ----------------------------------------------------------- 设置 -- */

async function loadTools(force) {
  const summary = $('toolSummary');
  summary.textContent = force ? '正在重新加载…' : '加载中…';
  try {
    const resp = await fetch(force ? '/api/tools/reload' : '/api/tools', {
      method: force ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await resp.json();
    renderTools(data);
    return data;
  } catch (err) {
    summary.textContent = '加载失败：' + ((err && err.message) || err);
    return null;
  }
}

function renderTools(data) {
  if (!data) return;
  const list = $('toolList');
  const notes = $('toolNotes');
  list.innerHTML = '';
  notes.innerHTML = '';

  const tools = data.tools || [];
  const prompts = data.prompts || [];
  $('toolSummary').textContent = data.enabled
    ? tools.length + ' 个工具' + (prompts.length ? ' · ' + prompts.length + ' 段提示词' : '') +
      ' · 最多 ' + (data.max_iterations || 6) + ' 轮'
    : '工具调用未开启';

  if (!tools.length && !prompts.length) {
    const catalog = data.catalog || {};
    const skills = catalog.skills || [];
    const mcp = catalog.mcp || [];
    if (!data.enabled && (skills.length || mcp.length)) {
      const head = document.createElement('div');
      head.className = 'tool-row';
      head.innerHTML = '<div class="info"><span>检测到以下能力，勾选上方开关后即可使用：</span></div>';
      list.appendChild(head);
      skills.forEach((name) => list.appendChild(catalogRow('skill', name, 'skills/' + name)));
      mcp.forEach((name) => list.appendChild(catalogRow('mcp', name, 'mcp.json 中已启用')));
    } else {
      const empty = document.createElement('div');
      empty.className = 'tool-row';
      empty.innerHTML = '<div class="info"><span>没有加载到工具。把技能放进 skills/ 目录，或在 mcp.json 里启用 MCP Server。</span></div>';
      list.appendChild(empty);
    }
  }

  tools.forEach((tool) => {
    const row = document.createElement('div');
    row.className = 'tool-row';
    const badge = document.createElement('span');
    badge.className = 'badge ' + (tool.source || 'builtin');
    badge.textContent = tool.source || 'builtin';
    const info = document.createElement('div');
    info.className = 'info';
    const name = document.createElement('b');
    name.textContent = tool.name;
    const desc = document.createElement('span');
    desc.textContent = tool.description || '';
    info.appendChild(name);
    info.appendChild(desc);
    row.appendChild(badge);
    row.appendChild(info);
    row.addEventListener('click', () => runToolFromPanel(tool));
    row.title = '点击试跑这个工具';
    list.appendChild(row);
  });

  prompts.forEach((prompt) => {
    const row = document.createElement('div');
    row.className = 'tool-row';
    const badge = document.createElement('span');
    badge.className = 'badge skill';
    badge.textContent = 'prompt';
    const info = document.createElement('div');
    info.className = 'info';
    const name = document.createElement('b');
    name.textContent = prompt.name || '';
    const desc = document.createElement('span');
    desc.textContent = prompt.description || '提示词技能，内容会拼进系统提示词';
    info.appendChild(name);
    info.appendChild(desc);
    row.appendChild(badge);
    row.appendChild(info);
    list.appendChild(row);
  });

  (data.notes || []).forEach((note) => {
    const line = document.createElement('div');
    line.textContent = '· ' + note;
    notes.appendChild(line);
  });
  (data.errors || []).forEach((err) => {
    const line = document.createElement('div');
    line.className = 'err';
    line.textContent = '! ' + err;
    notes.appendChild(line);
  });
}

function catalogRow(source, name, note) {
  const row = document.createElement('div');
  row.className = 'tool-row';
  const badge = document.createElement('span');
  badge.className = 'badge ' + source;
  badge.textContent = source;
  const info = document.createElement('div');
  info.className = 'info';
  const title = document.createElement('b');
  title.textContent = name;
  const desc = document.createElement('span');
  desc.textContent = note;
  info.appendChild(title);
  info.appendChild(desc);
  row.appendChild(badge);
  row.appendChild(info);
  return row;
}

async function runToolFromPanel(tool) {
  const raw = window.prompt('试跑工具 ' + tool.name + '\n请输入参数（JSON，留空表示 {}）', '{}');
  if (raw === null) return;
  let args = {};
  try {
    args = raw.trim() ? JSON.parse(raw) : {};
  } catch (err) {
    toast('参数不是合法 JSON：' + err.message, 'err');
    return;
  }
  try {
    const resp = await fetch('/api/tool/call', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: tool.name, arguments: args }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.message || ('HTTP ' + resp.status));
    toast((data.ok ? '✓ ' : '✗ ') + tool.name + '（' + data.elapsed + 's）\n' + String(data.content || '').slice(0, 160),
      data.ok ? 'ok' : 'err');
  } catch (err) {
    toast('执行失败：' + ((err && err.message) || err), 'err');
  }
}

function openSettings() {
  const cfg = store.config || {};
  $('fApiKey').value = '';
  $('keyTip').textContent = cfg.has_key ? ('当前：' + cfg.key_masked) : '尚未配置';
  $('fBaseUrl').value = cfg.base_url || '';
  $('fModel').value = cfg.model || '';
  $('fSystem').value = cfg.system_prompt || '';
  $('fTimeout').value = cfg.timeout || 180;
  $('fRetries').value = cfg.max_retries != null ? cfg.max_retries : 3;
  $('fProxy').value = cfg.proxy || '';
  $('fMaxTokens').value = (cfg.params && cfg.params.max_completion_tokens) || 4096;
  $('fTemp').value = (cfg.params && cfg.params.temperature != null) ? cfg.params.temperature : 1.0;
  $('fTopP').value = (cfg.params && cfg.params.top_p != null) ? cfg.params.top_p : 0.95;
  syncSliderLabels();
  $('testResult').textContent = '';
  $('testResult').className = 'test-result';
  $('fToolsEnabled').checked = !!(cfg.tools && cfg.tools.enabled);
  $('settingsModal').hidden = false;
  loadTools(false);
}

function closeSettings() {
  $('settingsModal').hidden = true;
}

function syncSliderLabels() {
  $('vTemp').textContent = Number($('fTemp').value).toFixed(2);
  $('vTopP').textContent = Number($('fTopP').value).toFixed(2);
}

async function saveSettings() {
  const body = {
    base_url: $('fBaseUrl').value.trim(),
    model: $('fModel').value.trim(),
    system_prompt: $('fSystem').value,
    timeout: Number($('fTimeout').value) || 180,
    max_retries: Number($('fRetries').value) || 0,
    proxy: $('fProxy').value.trim(),
    params: {
      temperature: Number($('fTemp').value),
      top_p: Number($('fTopP').value),
      max_completion_tokens: Number($('fMaxTokens').value) || 4096,
    },
    tools: { enabled: $('fToolsEnabled').checked },
  };
  const key = $('fApiKey').value.trim();
  if (key) body.api_key = key;

  try {
    const resp = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!data.ok) throw new Error(data.message || '保存失败');
    store.config = data.config;
    applyConfigToUi();
    toast('设置已保存', 'ok');
    closeSettings();
    renderAll();
    // 工具开关可能变了，重新拉一次清单
    loadTools(false);
  } catch (err) {
    toast('保存失败：' + (err.message || err), 'err');
  }
}

async function testConnection() {
  const btn = $('testBtn');
  const out = $('testResult');
  btn.disabled = true;
  out.className = 'test-result';
  out.textContent = '正在测试…';
  try {
    const resp = await fetch('/api/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: $('fModel').value.trim() || undefined }),
    });
    const data = await resp.json();
    if (data.ok) {
      out.className = 'test-result ok';
      out.textContent = '连接成功（' + data.latency + 's）：' + (data.reply || '').slice(0, 40);
    } else {
      out.className = 'test-result bad';
      out.textContent = data.message || '连接失败';
    }
  } catch (err) {
    out.className = 'test-result bad';
    out.textContent = '请求失败：' + (err.message || err);
  } finally {
    btn.disabled = false;
  }
}

function resetSettingsForm() {
  $('fBaseUrl').value = 'https://api.xiaomimimo.com/v1';
  $('fModel').value = 'mimo-v2.6-pro';
  $('fSystem').value = '你是MiMo（中文名称也是MiMo），是小米公司研发的AI智能助手。\n今天的日期：{date} {week}，你的知识截止日期是2024年12月。';
  $('fTimeout').value = 180;
  $('fRetries').value = 3;
  $('fProxy').value = '';
  $('fMaxTokens').value = 4096;
  $('fTemp').value = 1.0;
  $('fTopP').value = 0.95;
  syncSliderLabels();
}

function applyConfigToUi() {
  const cfg = store.config || {};
  $('brandModel').textContent = cfg.model || '—';
  const sel = $('modelSelect');
  sel.innerHTML = '';
  const ids = new Set();
  (cfg.models || []).forEach((m) => { if (m && m.id) ids.add(m.id); });
  if (cfg.model) ids.add(cfg.model);
  ids.forEach((id) => {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = id;
    sel.appendChild(opt);
  });
  const dl = $('modelList');
  dl.innerHTML = '';
  ids.forEach((id) => {
    const opt = document.createElement('option');
    opt.value = id;
    dl.appendChild(opt);
  });
  const conv = currentConv();
  sel.value = conv.model || cfg.model || '';
  $('titleSub').textContent = cfg.has_key ? '就绪' : '未配置 API Key';

  // 工具开关状态同步到输入框上方的「工具」标签
  const toolsOn = !!(cfg.tools && cfg.tools.enabled);
  const chip = $('toolsChip');
  if (chip) {
    chip.classList.toggle('disabled', !toolsOn);
    chip.title = toolsOn ? '允许模型调用工具（内置 / 技能 / MCP）' : '工具扩展未开启，可在设置里打开';
    const input = $('toolsToggle');
    input.disabled = !toolsOn;
    if (!toolsOn) {
      input.checked = false;
    } else if (store.prefs.tools !== false) {
      input.checked = true;
    }
  }
}

/* ------------------------------------------------------------ 导出 -- */

function exportMarkdown() {
  const conv = currentConv();
  if (!conv.messages.length) {
    toast('当前对话为空', 'err');
    return;
  }
  const lines = ['# ' + (conv.title || 'MiMo 对话'), '', '> 模型：' + (conv.model || '') + '　导出时间：' + new Date().toLocaleString(), ''];
  conv.messages.forEach((m) => {
    if (m.role === 'user') {
      lines.push('## 我', '', m.content || '', '');
    } else if (m.role === 'assistant') {
      if (m.reasoning) lines.push('<details><summary>思考过程</summary>', '', m.reasoning, '', '</details>', '');
      lines.push('## MiMo', '');
      (m.parts || []).forEach((part) => {
        if (part.kind !== 'tool') return;
        lines.push('> **工具调用** `' + part.name + '`  ');
        lines.push('> 参数：`' + JSON.stringify(part.args || {}) + '`  ');
        lines.push('> 状态：' + (part.status === 'error' ? '失败' : '完成') + '  ');
        lines.push('> 返回：');
        lines.push('> ```');
        String(part.result || '').split('\n').forEach((line) => lines.push('> ' + line));
        lines.push('> ```', '');
      });
      lines.push(m.content || '', '');
      if (m.usage) lines.push('`tokens: ' + (m.usage.total_tokens || 0) + '`', '');
    }
  });
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = (conv.title || 'mimo-chat').replace(/[\\/:*?"<>|]/g, '_') + '.md';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast('已导出 Markdown', 'ok');
}

/* ------------------------------------------------------------ 主题 -- */

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(LS.theme, theme);
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
}

/* ------------------------------------------------------------ 输入 -- */

function autoGrow() {
  const ta = $('input');
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 220) + 'px';
}

function openSidebar() { document.body.classList.add('side-open'); }
function closeSidebar() { document.body.classList.remove('side-open'); }

/* ------------------------------------------------------------ 初始化 -- */

function bindEvents() {
  $('newChat').addEventListener('click', newChat);
  $('clearBtn').addEventListener('click', clearCurrent);
  $('exportBtn').addEventListener('click', exportMarkdown);
  $('openSettings').addEventListener('click', openSettings);
  $('themeBtn').addEventListener('click', toggleTheme);
  $('openSide').addEventListener('click', openSidebar);
  $('closeSide').addEventListener('click', closeSidebar);
  $('scrim').addEventListener('click', closeSidebar);

  $('sendBtn').addEventListener('click', () => {
    if (store.streaming) { stopStreaming(); return; }
    const ta = $('input');
    const text = ta.value;
    ta.value = '';
    autoGrow();
    sendMessage(text);
  });

  $('input').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
      ev.preventDefault();
      $('sendBtn').click();
    }
  });
  $('input').addEventListener('input', autoGrow);

  $('modelSelect').addEventListener('change', (ev) => {
    const conv = currentConv();
    conv.model = ev.target.value;
    saveState();
    toast('已切换模型：' + conv.model, 'ok');
  });

  $('convTitle').addEventListener('change', (ev) => {
    const conv = currentConv();
    conv.title = ev.target.value.trim() || '新对话';
    saveState();
    renderConvList();
  });

  const syncPrefs = () => {
    store.prefs.showReasoning = $('thinkToggle').checked;
    store.prefs.reasonEcho = $('reasonEchoToggle').checked;
    store.prefs.search = $('searchToggle').checked;
    store.prefs.tools = $('toolsToggle').checked;
    saveState();
  };
  ['thinkToggle', 'reasonEchoToggle', 'searchToggle', 'toolsToggle'].forEach((id) => {
    $(id).addEventListener('change', syncPrefs);
  });

  // 设置弹窗
  document.querySelectorAll('[data-close]').forEach((node) => {
    node.addEventListener('click', closeSettings);
  });
  $('saveCfgBtn').addEventListener('click', saveSettings);
  $('testBtn').addEventListener('click', testConnection);
  $('resetBtn').addEventListener('click', resetSettingsForm);
  $('reloadToolsBtn').addEventListener('click', () => loadTools(true));
  $('fTemp').addEventListener('input', syncSliderLabels);
  $('fTopP').addEventListener('input', syncSliderLabels);

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      if (!$('settingsModal').hidden) closeSettings();
      else if (store.streaming) stopStreaming();
    }
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'k') {
      ev.preventDefault();
      newChat();
    }
  });

  // 点击代码块复制按钮（事件委托兜底）
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest && ev.target.closest('.code-head .copy');
    if (!btn) return;
    let code = '';
    try { code = decodeURIComponent(btn.dataset.code || ''); } catch (e) { code = btn.dataset.code || ''; }
    copyText(code);
  });

  window.addEventListener('beforeunload', (ev) => {
    if (store.streaming) {
      ev.preventDefault();
      ev.returnValue = '';
    }
  });
}

async function loadConfig() {
  try {
    const resp = await fetch('/api/config');
    store.config = await resp.json();
  } catch (err) {
    toast('读取配置失败，请确认服务已启动', 'err');
    store.config = { has_key: false, models: [], params: {} };
  }
  applyConfigToUi();
}

async function init() {
  applyTheme(localStorage.getItem(LS.theme) ||
    (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));

  loadState();

  // 同步偏好开关
  $('thinkToggle').checked = !!store.prefs.showReasoning;
  $('reasonEchoToggle').checked = !!store.prefs.reasonEcho;
  $('searchToggle').checked = !!store.prefs.search;

  bindEvents();
  await loadConfig();
  renderAll();
  autoGrow();
  $('input').focus();
}

document.addEventListener('DOMContentLoaded', init);
