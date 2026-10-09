// =============================================================================
// Antigravity 一键汉化引擎 v1.2
// 设计要点：
//   - 纯 Web API（无 require/network），preload 沙箱环境可用
//   - 多级瀑布翻译管道 + O(1) 缓存
//   - 输入框/代码区/思考链物理隔离（最全防护集）
//   - 微任务批量调度 + 防重入锁 + 熔断器（杜绝 CPU 风暴）
//   - Shadow DOM 穿透 + SPA 路由 + 焦点恢复全生命周期兜底
//   - 不修改任何全局原型
// =============================================================================
;(function () {
    'use strict';
    if (typeof window === 'undefined' || typeof document === 'undefined') return;
    if (window.__AGY_ZH_ACTIVE__) return; // 防重复挂载
    window.__AGY_ZH_ACTIVE__ = true;

    // ---------------- 1. 词典数据（由部署脚本注入） ----------------
    var DATA = /*__AGY_ZH_DATA__*/ {};
    var DICT = DATA.exact || {};
    var PREFIX_RULES = DATA.prefix || {};
    var CORE = DATA.core || {};
    var REGEX_RULES = [];
    var EXEMPT = DATA.exempt || [];
    var MENTION_CATEGORIES = new Set();
    var i, k;
    for (i = 0; i < (DATA.regex || []).length; i++) {
        try {
            REGEX_RULES.push({ re: new RegExp(DATA.regex[i].pattern), rp: DATA.regex[i].replacement });
        } catch (e) { /* 单条非法不拖垮引擎 */ }
    }
    var rawCats = DATA.mentionCategories || [];
    for (i = 0; i < rawCats.length; i++) MENTION_CATEGORIES.add(rawCats[i]);
    // 含中文的分类名也加入白名单（首次翻译后菜单项会变成中文，需再次命中）
    MENTION_CATEGORIES.forEach(function (c) {
        var zh = DICT[c];
        if (zh) MENTION_CATEGORIES.add(zh);
    });

    // ---------------- 2. 查找索引（预构建，全部 O(1)） ----------------
    var lowerDict = new Map();
    var normDict = new Map();
    function normalizeSpace(s) { return s.replace(/\s+/g, ' '); }
    for (k in DICT) {
        var lk = k.toLowerCase();
        if (!lowerDict.has(lk)) lowerDict.set(lk, DICT[k]);
        var nk = normalizeSpace(lk);
        if (!normDict.has(nk)) normDict.set(nk, DICT[k]);
    }
    // 前缀规则：按 key 长度降序（长前缀优先），且只保留足够长的（防误伤）
    var prefixList = [];
    for (k in PREFIX_RULES) {
        if (k.length >= 16) prefixList.push([k, PREFIX_RULES[k]]);
    }
    prefixList.sort(function (a, b) { return b[0].length - a[0].length; });
    // 核心词联合正则（长词优先）
    function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
    var coreKeys = Object.keys(CORE).sort(function (a, b) { return b.length - a.length; });
    var CORE_UNION = coreKeys.length
        ? new RegExp('\\b(' + coreKeys.map(escRe).join('|') + ')\\b', 'gi') : null;

    var stringCache = new Map();
    var MAX_CACHE = 6000;
    function cacheGet(t) { return stringCache.get(t); }
    function cachePut(t, v) { if (stringCache.size < MAX_CACHE) stringCache.set(t, v); }

    var CJK_RE = /[\u4e00-\u9fff]/;
    var LETTER_RE = /[a-zA-Z]/;
    var MONTHS = { Jan: '1月', Feb: '2月', Mar: '3月', Apr: '4月', May: '5月', Jun: '6月',
        Jul: '7月', Aug: '8月', Sep: '9月', Oct: '10月', Nov: '11月', Dec: '12月' };
    var MONTH_DATE_RE = /\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s*(\d{1,2})\b/g;

    // ---------------- 3. 翻译管道（多级瀑布，逐级短路） ----------------
    function translateString(text) {
        if (!text || typeof text !== 'string') return text;
        var trimmed = text.trim();
        if (!trimmed) return text;
        if (!LETTER_RE.test(trimmed)) return text; // 纯中文/数字/标点：零开销返回（收敛关键）

        var hit = cacheGet(trimmed);
        if (hit !== undefined) return text.replace(trimmed, hit);

        var out = pipeline(trimmed);
        out = normalizeMonths(out);
        out = fixMacKeys(out);
        cachePut(trimmed, out);
        return text.replace(trimmed, out);
    }

    // ---------------- macOS 键位修正 ----------------
    // 词典源自 VS Code，其快捷键文案按 Windows 写死（Ctrl+X）。
    // macOS 上 Command 才是对应键，直接照搬会让用户按错键。
    // 这里只在检测到 macOS 时把 Ctrl/Alt 换成 Command/Option，Windows 保持原样。
    var IS_MAC_OS = (function () {
        try {
            if (typeof navigator !== 'undefined' && navigator.platform) {
                return /Mac|iPhone|iPad/i.test(navigator.platform);
            }
            if (typeof process !== 'undefined' && process.platform) {
                return process.platform === 'darwin';
            }
        } catch (e) { /* 忽略 */ }
        return false;
    })();

    // 键位映射：只在 macOS 上把 Ctrl/Alt 换成 Command/Option。
    // Shift/Win/Meta 不动。Cmd/Ctrl 这类跨平台并列写法保留原样。
    var MAC_KEY_MAP = [
        [/\bCtrl\s*\+/g, '⌘'],
        [/\bCtrl\b(?!\+)/g, 'Control'],
        [/\bAlt\s*\+/g, '⌥'],
        [/\bAlt\b(?!\+)/g, 'Option'],
        [/\bCmd\b/g, '⌘']
    ];

    // 这些文本本身在讲平台差异或并列写法，改键名反而误导
    var MAC_KEY_SKIP = [
        /Windows.*(Ctrl|Control)/i,
        /Ctrl.*Windows/i,
        /(Windows|Linux).*(macOS|Mac)/i,
        /Cmd\s*\/\s*Ctrl/i,
        /Ctrl\s*\/\s*Alt/i
    ];

    function fixMacKeys(s) {
        if (!IS_MAC_OS || !s) return s;
        // 仅当译文里含 Windows 键名时才处理，避免无谓开销
        if (!/\b(Ctrl|Alt|Cmd)\b/.test(s)) return s;
        for (var k = 0; k < MAC_KEY_SKIP.length; k++) {
            if (MAC_KEY_SKIP[k].test(s)) return s;
        }
        var out = s;
        for (var i = 0; i < MAC_KEY_MAP.length; i++) {
            out = out.replace(MAC_KEY_MAP[i][0], MAC_KEY_MAP[i][1]);
        }
        return out;
    }

    function pipeline(t) {
        var j;
        // 0. 豁免清单（高频时钟等动态文本，翻译反而添乱）
        for (j = 0; j < EXEMPT.length; j++) {
            if (t.indexOf(EXEMPT[j]) !== -1) return t;
        }
        // 1. 精确匹配
        if (DICT[t]) return DICT[t];
        // 2. 正则动态规则（锚定 ^$，处理计数/时间等动态文本）
        for (j = 0; j < REGEX_RULES.length; j++) {
            var r = REGEX_RULES[j];
            if (r.re.test(t)) {
                var rt = t.replace(r.re, r.rp);
                if (rt !== t) return rt;
            }
        }
        var lt = t.toLowerCase();
        // 3. 大小写不敏感精确匹配
        if (lowerDict.has(lt)) return lowerDict.get(lt);
        // 4. 空白归一化匹配（换行/多空格折叠）
        var nt = normalizeSpace(lt);
        if (normDict.has(nt)) return normDict.get(nt);
        // 5. 尾部标点剥离重建（匹配 "Save" 当文本是 "Save." 时）
        var m = t.match(/^(.*?)([.!?,:;…]+)$/s);
        if (m && m[1]) {
            var body = m[1].trim(), tail = m[2];
            if (DICT[body]) return DICT[body] + zhPunct(tail);
            var lb = body.toLowerCase();
            if (lowerDict.has(lb)) return lowerDict.get(lb) + zhPunct(tail);
        }
        // 6. 前缀规则（截断文本兜底：仅开头命中 + 不含中文）
        if (!CJK_RE.test(t) && t.length <= 500) {
            for (j = 0; j < prefixList.length; j++) {
                if (t.indexOf(prefixList[j][0]) === 0) {
                    return t.replace(prefixList[j][0], prefixList[j][1]);
                }
            }
        }
        // 7. 核心词分词（仅 ≤3 词的短文本且不含中文，宁缺毋滥）
        if (!CJK_RE.test(t) && CORE_UNION) {
            var words = t.split(/\s+/);
            if (words.length <= 3) {
                var rt2 = t.replace(CORE_UNION, function (w) {
                    var v = CORE[w.toLowerCase()];
                    return v || w;
                });
                if (rt2 !== t) return rt2;
            }
        }
        return t; // 未命中：原样返回（缓存为身份映射）
    }

    function normalizeMonths(s) {
        // 月份归一化："更新于 Mar月5日" → "更新于 3月5日"；仅处理已含中文的短文本，
        // 避免误伤正文中合法的英文月份
        if (s.length > 60 || !CJK_RE.test(s)) return s;
        return s.replace(MONTH_DATE_RE, function (m, mon, day) {
            return MONTHS[mon] ? MONTHS[mon] + day + '日' : m;
        });
    }

    function zhPunct(tail) {
        var map = { '.': '。', '?': '？', '!': '！', ':': '：', ';': '；', ',': '，', '…': '……' };
        var out = '';
        for (var x = 0; x < tail.length; x++) out += (map[tail[x]] || tail[x]);
        return out;
    }

    // ---------------- 4. 安全防护（物理免疫沙盒） ----------------
    var skipTags = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, KBD: 1, SAMP: 1, VAR: 1, TEXTAREA: 1 };
    var codeClassPattern = /(?:^|[\s_-])(monaco-editor|editor-instance|hljs|shiki|prism|codemirror|line-content|gutter|codeblock|code-block|code-line|view-line)(?:$|[\s_-])/i;
    var THINK_CONTAINER = ['cursor-edit', 'thought-content', 'thinking-content', 'thought-box',
        'thought-container', 'conversation-container', 'chat-message-view', 'prose',
        'stream-markdown-body', 'stream-markdown', 'path-label', 'breadcrumb',
        'workspace-dropdown-item', 'folder-item', 'user-message', 'cursor-text',
        'monaco-editor', 'editor-instance', 'input-area', 'chat-input'];
    var ACTION_PILL_RE = /^(Explored|Ran|Viewed|Edited|Thought|Thinking|Working)$/i;

    var skipCache = new WeakMap();

    function shouldSkipNode(node) {
        if (!node) return true;
        var element = node.nodeType === 3 ? node.parentElement : node;
        if (!element) return false;

        var cached = skipCache.get(element);
        if (cached !== undefined) return cached;

        var verdict = judgeElement(element);
        skipCache.set(element, verdict);
        return verdict;
    }

    function judgeElement(element) {
        // 0. 自定义禁区属性（页面可用 data-ag-localization-skip="true" 声明某区域永不翻译）
        if (element.hasAttribute && element.hasAttribute('data-ag-localization-skip')) return true;

        // 1. 脚本/样式标签
        if (skipTags[element.tagName]) return true;

        // 2. 输入框/富文本编辑器（绝对物理免疫）
        if (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA') return true;
        if (element.getAttribute && (
            element.getAttribute('contenteditable') === 'true' ||
            element.getAttribute('role') === 'textbox' ||
            element.getAttribute('data-lexical-editor') === 'true')) return true;

        // 3. 斜杠命令与 @提及菜单保护（防止 /boost 等命令词被翻译），
        //    仅白名单分类（Rules/对话/目录 等）放行
        var menuLabel = element.closest && element.closest('[data-testid="menu-option-label"]');
        if (menuLabel) {
            var labelText = (menuLabel.innerText || menuLabel.textContent || '').trim();
            return !MENTION_CATEGORIES.has(labelText);
        }

        // 4. 思考折叠栏标题按钮放行（"Thought for 4.2s" → "思考了 4.2 秒"）
        if (element.closest && element.closest('button[data-testid="thinking-collapsible-trigger"]')) return false;

        // 5. 执行步骤药丸标签放行（Ran/Edited/Viewed 等 ≤25 字符，且不在用户输入/正文内）
        var textContent = (element.innerText || element.textContent || '').trim();
        var isActionPill = textContent.length <= 25 && ACTION_PILL_RE.test(textContent);
        if (element.tagName === 'CODE') { if (!isActionPill) return true; }
        if (element.tagName === 'PRE') return true;

        // 6. 代码标记属性
        if (element.getAttribute) {
            if (element.getAttribute('data-language') || element.getAttribute('data-code') ||
                element.getAttribute('data-line') || element.getAttribute('data-line-number')) return true;
        }

        // 7. 向上递归检查祖先（思考链/用户消息/代码编辑器/输入区容器）
        var cur = element;
        while (cur && cur !== document.body) {
            if (skipCache.has(cur)) return skipCache.get(cur);
            if (cur.classList) {
                for (var c = 0; c < THINK_CONTAINER.length; c++) {
                    if (cur.classList.contains(THINK_CONTAINER[c])) return true;
                }
                if (codeClassPattern.test(cur.className)) return true;
            }
            if (cur.getAttribute) {
                if (cur.getAttribute('data-message-id')) return true;
                // 用户消息气泡（data-testid 形态，物理隔离方案）
                if (cur.getAttribute('data-testid') === 'user-input-step') return true;
                if (cur.getAttribute('data-ag-localization-skip')) return true;
                var tid = cur.getAttribute('data-testid');
                if (tid === 'thinking-collapsible-content' || tid === 'thought-content' ||
                    tid === 'thinking-content' || tid === 'thought-box') return true;
                if (cur.getAttribute('role') === 'code' ||
                    cur.getAttribute('contenteditable') === 'true' ||
                    cur.getAttribute('role') === 'textbox') return true;
            }
            if (cur.tagName === 'PRE') return true;
            cur = cur.parentElement;
        }
        return false;
    }

    // ---------------- 5. DOM 遍历与属性翻译 ----------------
    var ATTRS = ['placeholder', 'title', 'aria-label', 'data-tooltip', 'data-placeholder'];
    var translatedNodes = new WeakSet();

    // 属性提示文案翻译：placeholder/aria-label/title 是纯展示内容，
    // 即使元素位于输入区/思考链等跳过区也可以安全翻译（ATTRS 不含 value，用户输入绝不动）
    function translateAttrs(node) {
        if (!node.hasAttribute) return;
        for (var a = 0; a < ATTRS.length; a++) {
            var attr = ATTRS[a];
            if (node.hasAttribute(attr)) {
                var attrVal = node.getAttribute(attr);
                if (attrVal && LETTER_RE.test(attrVal)) {
                    var attrOut = translateString(attrVal);
                    if (attrVal !== attrOut) node.setAttribute(attr, attrOut);
                }
            }
        }
    }

    function translateNode(node) {
        if (!node) return;
        if (node.nodeType === 3) { // 文本节点
            if (translatedNodes.has(node)) return;
            var original = node.nodeValue;
            if (!original || !original.trim()) return;
            if (shouldSkipNode(node)) { translatedNodes.add(node); return; }
            var translated = translateString(original);
            if (original !== translated) {
                node.nodeValue = translated;
                translatedNodes.add(node);
            } else if (!LETTER_RE.test(original)) {
                translatedNodes.add(node);
            }
        } else if (node.nodeType === 1) { // 元素节点
            translateAttrs(node); // 提示属性优先翻译，不受跳过区限制
            if (shouldSkipNode(node)) return;
            if (node.shadowRoot) {
                observeRoot(node.shadowRoot);
                translateNode(node.shadowRoot);
            }
            for (var c2 = 0; c2 < node.childNodes.length; c2++) {
                translateNode(node.childNodes[c2]);
            }
        } else if (node.nodeType === 11) { // 文档片段
            for (var c3 = 0; c3 < node.childNodes.length; c3++) {
                translateNode(node.childNodes[c3]);
            }
        }
    }

    // ---------------- 6. 批量调度器（微任务聚合 + 防重入 + 熔断） ----------------
    var pendingAdded = new Set();
    var pendingText = new Set();
    var pendingAttrs = new Map();
    var isTranslating = false;
    var batchScheduled = false;
    var burstCount = 0, burstWindowStart = 0;
    var BURST_LIMIT = 400, BURST_WINDOW = 3000, COOLDOWN = 800;
    var observers = [];

    function scheduleBatch() {
        if (batchScheduled) return;
        batchScheduled = true;
        var run = function () { batchScheduled = false; processBatch(); };
        if (typeof queueMicrotask === 'function') queueMicrotask(run);
        else if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
        else setTimeout(run, 0);
    }

    function processBatch() {
        if (isTranslating) return;
        // 熔断器：3 秒窗口内批次数超限 → 暂停观察，冷却后重连（防理论性风暴）
        var now = Date.now();
        if (now - burstWindowStart > BURST_WINDOW) { burstWindowStart = now; burstCount = 0; }
        if (++burstCount > BURST_LIMIT) {
            disconnectAll();
            setTimeout(function () { reconnectAll(); safeScan(); }, COOLDOWN);
            burstCount = 0;
            return;
        }
        isTranslating = true;
        try {
            // 属性变更（提示属性是展示文案，不受跳过区限制）
            if (pendingAttrs.size) {
                pendingAttrs.forEach(function (attrs, target) {
                    attrs.forEach(function (attrName) {
                        var v = target.getAttribute && target.getAttribute(attrName);
                        if (v && LETTER_RE.test(v)) {
                            var o = translateString(v);
                            if (v !== o) target.setAttribute(attrName, o);
                        }
                    });
                });
                pendingAttrs.clear();
            }
            // 原地文本变更（characterData）
            if (pendingText.size) {
                pendingText.forEach(function (node) {
                    translatedNodes.delete(node);
                    var orig = node.nodeValue;
                    if (orig && orig.trim() && !shouldSkipNode(node)) {
                        var tr = translateString(orig);
                        if (orig !== tr) node.nodeValue = tr;
                        if (!LETTER_RE.test(orig) || orig !== tr) translatedNodes.add(node);
                    }
                });
                pendingText.clear();
            }
            // 新增节点（祖先剪枝：只处理最外层，消灭 O(N²)）
            if (pendingAdded.size) {
                var roots = [];
                pendingAdded.forEach(function (node) {
                    var p = node.parentElement, covered = false;
                    while (p) { if (pendingAdded.has(p)) { covered = true; break; } p = p.parentElement; }
                    if (!covered) roots.push(node);
                });
                pendingAdded.clear();
                for (var r = 0; r < roots.length; r++) {
                    var node = roots[r];
                    if (node.shadowRoot) observeRoot(node.shadowRoot);
                    translateNode(node);
                }
            }
        } catch (e) {
            try { console.warn('[Antigravity汉化] 批处理异常（已恢复）:', e); } catch (_) {}
        } finally {
            isTranslating = false;
        }
    }

    var OBSERVER_CONFIG = {
        childList: true, subtree: true, characterData: true, attributes: true,
        attributeFilter: ATTRS
    };
    var observedRoots = new WeakSet();

    function makeObserver() {
        return new MutationObserver(function (mutations) {
            var need = false;
            for (var x = 0; x < mutations.length; x++) {
                var mu = mutations[x];
                if (mu.type === 'childList') {
                    var added = mu.addedNodes;
                    for (var y = 0; y < added.length; y++) {
                        var n = added[y];
                        if (n.nodeType === 1 && n.shadowRoot) observeRoot(n.shadowRoot);
                        pendingAdded.add(n);
                        need = true;
                    }
                } else if (mu.type === 'characterData') {
                    pendingText.add(mu.target);
                    need = true;
                } else if (mu.type === 'attributes') {
                    var set = pendingAttrs.get(mu.target);
                    if (!set) { set = new Set(); pendingAttrs.set(mu.target, set); }
                    set.add(mu.attributeName);
                    need = true;
                }
            }
            if (need) scheduleBatch();
        });
    }

    function observeRoot(root) {
        if (!root || observedRoots.has(root)) return;
        observedRoots.add(root);
        var ob = makeObserver();
        ob.observe(root, OBSERVER_CONFIG);
        observers.push({ ob: ob, root: root });
        if (observers.length > 60) observers.shift(); // 有界观察器列表
    }

    function disconnectAll() {
        for (var x = 0; x < observers.length; x++) { try { observers[x].ob.disconnect(); } catch (_) {} }
    }
    function reconnectAll() {
        for (var x = 0; x < observers.length; x++) {
            try { observers[x].ob.observe(observers[x].root, OBSERVER_CONFIG); } catch (_) {}
        }
    }

    // ---------------- 7. 全生命周期兜底 ----------------
    function safeScan() {
        if (document.body) {
            try { translateNode(document.body); } catch (e) {}
            observeRoot(document.body);
        }
    }

    // Shadow DOM 穿透：未来新建的 shadow root 自动纳管
    try {
        var origAttachShadow = Element.prototype.attachShadow;
        Element.prototype.attachShadow = function () {
            var sr = origAttachShadow.apply(this, arguments);
            observeRoot(sr);
            return sr;
        };
    } catch (_) {}

    // 窗口标题翻译（"Antigravity - xxx" → 中文）
    try {
        var titleDesc = Object.getOwnPropertyDescriptor(Document.prototype, 'title') ||
            Object.getOwnPropertyDescriptor(HTMLDocument.prototype, 'title');
        if (titleDesc && titleDesc.set) {
            var origTitleSet = titleDesc.set;
            Object.defineProperty(document, 'title', {
                configurable: true, enumerable: true,
                get: function () { return titleDesc.get.call(document); },
                set: function (val) { origTitleSet.call(document, translateString(val)); }
            });
        }
    } catch (_) {}

    function startObserver() {
        safeScan();
        // 渐进式多阶挂载兜底（托盘唤醒/异步组件挂载延迟）
        var delays = [50, 150, 400, 1000, 2500];
        for (var d = 0; d < delays.length; d++) setTimeout(safeScan, delays[d]);
        // 焦点/可见性恢复
        window.addEventListener('focus', safeScan);
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') safeScan();
        });
        // SPA 路由切换
        try {
            var origPush = history.pushState;
            history.pushState = function () {
                var r = origPush.apply(this, arguments);
                setTimeout(safeScan, 50);
                return r;
            };
            var origReplace = history.replaceState;
            history.replaceState = function () {
                var r = origReplace.apply(this, arguments);
                setTimeout(safeScan, 50);
                return r;
            };
            window.addEventListener('popstate', function () { setTimeout(safeScan, 50); });
        } catch (_) {}
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', startObserver);
    } else {
        startObserver();
    }

    // 调试句柄（F12 控制台可用 window.__AGY_ZH_DEBUG__.stats() 查看运行状态）
    window.__AGY_ZH_DEBUG__ = {
        version: '1.0.0',
        stats: function () {
            return {
                active: true,
                dictSize: Object.keys(DICT).length,
                cacheSize: stringCache.size,
                prefixRules: prefixList.length,
                regexRules: REGEX_RULES.length,
                observers: observers.length
            };
        },
        rescan: safeScan,
        translate: translateString
    };
})();
