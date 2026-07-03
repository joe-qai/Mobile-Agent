/**
 * 公共JS模块 - 封装所有页面的通用功能
 * 包含：侧边栏管理、API请求、SPA路由、页面初始化等
 */

// ==================== API 请求封装 ====================
const Api = {
    baseUrl: '/api',

    async get(url, params = {}) {
        const queryString = new URLSearchParams(params).toString();
        const fullUrl = queryString ? `${this.baseUrl}${url}?${queryString}` : `${this.baseUrl}${url}`;
        try {
            const response = await fetch(fullUrl);
            return await response.json();
        } catch (error) {
            console.error('API GET Error:', error);
            throw error;
        }
    },

    async post(url, data = {}) {
        try {
            const response = await fetch(`${this.baseUrl}${url}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            return await response.json();
        } catch (error) {
            console.error('API POST Error:', error);
            throw error;
        }
    },

    async put(url, data = {}) {
        try {
            const response = await fetch(`${this.baseUrl}${url}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            return await response.json();
        } catch (error) {
            console.error('API PUT Error:', error);
            throw error;
        }
    },

    async delete(url) {
        try {
            const response = await fetch(`${this.baseUrl}${url}`, {
                method: 'DELETE'
            });
            return await response.json();
        } catch (error) {
            console.error('API DELETE Error:', error);
            throw error;
        }
    }
};


// ==================== SPA 路由管理 ====================
const Router = {
    /** 支持SPA无刷新切换的路径（/agent 有独立布局，不纳入） */
    routes: ['/', '/dashboard', '/projects', '/scripts', '/apks', '/tasks', '/reports'],

    _currentPagePath: null,
    _navigating: false,
    _pageTimers: [],
    _unmountHooks: [],

    init() {
        this._currentPagePath = window.location.pathname;
        this.setupNavigationInterceptor();
        this.setupPopstateListener();
    },

    /** 注册页面卸载钩子（用于关闭 WebSocket、清除长连接等），SPA 切换前会被调用 */
    registerUnmount(fn) {
        if (typeof fn === 'function') this._unmountHooks.push(fn);
    },

    /** 执行所有已注册的卸载钩子，并清空列表 */
    _runUnmount() {
        const hooks = this._unmountHooks;
        this._unmountHooks = [];
        hooks.forEach(fn => {
            try { fn(); } catch (e) { console.warn('unmount hook error:', e); }
        });
    },

    setupNavigationInterceptor() {
        document.addEventListener('click', (e) => {
            const link = e.target.closest('a[data-nav-link]');
            if (!link) return;
            const href = link.getAttribute('href');
            if (!href || !this.isNavigable(href)) return;
            e.preventDefault();
            this.navigate(href);
        });
    },

    isNavigable(path) {
        return this.routes.includes(path);
    },

    async navigate(path) {
        if (path === this._currentPagePath || this._navigating) return;
        this._navigating = true;

        // 触发上一页的卸载钩子（关闭 WebSocket、长连接等，避免访问已替换的 DOM）
        this._runUnmount();

        // 清除上一页的定时器（防止后台刷新访问已移除的DOM元素）
        this.clearPageTimers();

        const contentContainer = document.getElementById('main-content');
        if (!contentContainer) {
            window.location.href = path;
            return;
        }

        // 显示加载状态（保留容器尺寸避免布局跳动）
        const origMinHeight = contentContainer.style.minHeight;
        contentContainer.style.minHeight = contentContainer.offsetHeight + 'px';
        contentContainer.innerHTML = `
            <div class="flex items-center justify-center h-full min-h-[300px]">
                <div class="flex flex-col items-center">
                    <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
                    <p class="mt-3 text-gray-400 text-sm">加载中...</p>
                </div>
            </div>
        `;

        try {
            const response = await fetch(path);
            if (!response.ok) throw new Error('页面加载失败');

            const html = await response.text();
            const newDoc = new DOMParser().parseFromString(html, 'text/html');

            // 1) 移除上一页注入的内联样式
            document.querySelectorAll('style[data-spa-style]').forEach(s => s.remove());

            // 2) 注入新页面的内联 <style>（<head> 里的）
            newDoc.querySelectorAll('head style').forEach(style => {
                const ns = document.createElement('style');
                ns.textContent = style.textContent;
                ns.setAttribute('data-spa-style', 'true');
                document.head.appendChild(ns);
            });

            // 3) 加载缺失的 <link> CSS
            newDoc.querySelectorAll('head link[rel="stylesheet"]').forEach(link => {
                const href = link.getAttribute('href');
                if (!href || document.querySelector(`link[href="${href}"]`)) return;
                const nl = document.createElement('link');
                nl.rel = 'stylesheet';
                nl.href = href;
                document.head.appendChild(nl);
            });

            // 4) 替换主内容区
            const newContent = newDoc.querySelector('#main-content');
            contentContainer.innerHTML = newContent ? newContent.innerHTML : newDoc.body.innerHTML;
            contentContainer.style.minHeight = origMinHeight || '';

            // 5) 更新侧边栏激活状态
            this.updateSidebarActive(path);

            // 6) 更新浏览器标题
            document.title = newDoc.title || document.title;

            // 7) pushState
            history.pushState({ path }, '', path);
            this._currentPagePath = path;

            // 8) 执行新页面的内联脚本 + 页面初始化
            this.executeInlineScripts(newDoc);
            this.runPageInit(path);

        } catch (error) {
            console.error('SPA导航失败:', error);
            window.location.href = path;
        } finally {
            this._navigating = false;
        }
    },

    /** 执行新页面中非外链的内联 <script> */
    executeInlineScripts(newDoc) {
        // 已加载的外链脚本列表（跳过重复加载）
        const loadedSrcs = new Set();
        document.querySelectorAll('script[src]').forEach(s => {
            loadedSrcs.add(s.getAttribute('src'));
        });

        // 先执行 <head> 中的内联脚本（如 tailwind config）
        newDoc.querySelectorAll('head script:not([src])').forEach(originalScript => {
            const text = originalScript.textContent;
            if (!text || !text.trim()) return;
            // 跳过空脚本或 tailwind CDN 初始化脚本（已由首次加载处理）
            if (text.includes('tailwind.config')) return; // tailwind config 仅首页有，已全局生效
            try {
                const script = document.createElement('script');
                script.textContent = text;
                document.body.appendChild(script);
                script.remove();
            } catch (error) {
                console.warn('Head脚本执行失败:', error);
            }
        });

        // 执行 <body> 中的内联脚本（页面逻辑）
        // SPA导航时脚本可能重复执行：用 IIFE 隔离作用域避免重复声明 SyntaxError，
        // 同时将顶层函数/变量导出到 window 供 onclick 调用
        newDoc.querySelectorAll('body script:not([src])').forEach(originalScript => {
            const text = originalScript.textContent;
            if (!text || !text.trim()) return;
            // 提取顶层 function 名和 let/const 变量名（供导出到 window）
            const funcNames = [];
            const varNames = [];
            const funcRegex = /^\s*function\s+([a-zA-Z_$][\w$]*)\s*\(/gm;
            const asyncFuncRegex = /^\s*async\s+function\s+([a-zA-Z_$][\w$]*)\s*\(/gm;
            const varRegex = /^\s*(?:let|const)\s+([a-zA-Z_$][\w$]*)\s*[=,;]/gm;
            let m;
            while ((m = funcRegex.exec(text)) !== null) funcNames.push(m[1]);
            while ((m = asyncFuncRegex.exec(text)) !== null) funcNames.push(m[1]);
            while ((m = varRegex.exec(text)) !== null) varNames.push(m[1]);
            // 将顶层 let/const 替换为 var（IIFE 内仍需 var 以避免同作用域重复声明）
            const safeText = text.replace(/^(\s*)(let|const)\s/gm, '$1var ');
            // 构建导出语句
            const exports = [...funcNames, ...varNames]
                .map(n => `try{window.${n}=${n}}catch(e){}`)
                .join(';');
            const wrapped = `(function(){\n${safeText}\n${exports}\n})();`;
            try {
                const script = document.createElement('script');
                script.textContent = wrapped;
                document.body.appendChild(script);
                script.remove();
            } catch (error) {
                console.warn('Body脚本执行失败:', error);
            }
        });

        // 加载缺失的外链脚本（如 marked.js 仅 agent 页有）
        newDoc.querySelectorAll('script[src]').forEach(s => {
            const src = s.getAttribute('src');
            if (!src || loadedSrcs.has(src)) return;
            const ns = document.createElement('script');
            ns.src = src;
            document.head.appendChild(ns);
            loadedSrcs.add(src);
        });
    },

    /** 各页面数据加载入口 */
    runPageInit(path) {
        // Sidebar 已初始化过，不再重复添加事件监听
        // UIUtils 在首次加载已初始化

        const initMap = {
            '/': () => {
                // 滚动动画
                const observer = new IntersectionObserver((entries) => {
                    entries.forEach(entry => {
                        if (entry.isIntersecting) entry.target.classList.add('visible');
                    });
                }, { threshold: 0.1 });
                document.querySelectorAll('.animate-on-scroll:not(.visible)').forEach(el => observer.observe(el));
            },
            '/dashboard': () => {
                if (typeof loadStats === 'function') loadStats();
                if (typeof loadRecentTasks === 'function') loadRecentTasks();
                if (typeof loadDeviceStatus === 'function') loadDeviceStatus();
                // Chart.js may load async during SPA navigation - wait for it
                function initDashboardCharts() {
                    if (typeof Chart === 'undefined') {
                        setTimeout(initDashboardCharts, 100);
                        return;
                    }
                    try { if (typeof initCharts === 'function') initCharts(); } catch(e) { console.warn('initCharts failed:', e); }
                    if (typeof loadTrends === 'function') loadTrends(7);
                }
                initDashboardCharts();
                // 注册定时器，导航离开时自动清除
                Router.registerTimer(setInterval(() => { if (typeof loadStats === 'function') loadStats(); }, 30000));
                Router.registerTimer(setInterval(() => { if (typeof loadRecentTasks === 'function') loadRecentTasks(); }, 30000));
                Router.registerTimer(setInterval(() => { if (typeof loadDeviceStatus === 'function') loadDeviceStatus(); }, 10000));
            },
            '/projects': () => {
                if (typeof loadProjects === 'function') loadProjects();
            },
            '/scripts': () => {
                if (typeof loadProjectFilter === 'function') loadProjectFilter();
                if (typeof loadScripts === 'function') loadScripts();
            },
            '/apks': () => {
                if (typeof loadDevices === 'function') loadDevices();
                if (typeof loadApks === 'function') loadApks();
            },
            '/tasks': () => {
                if (typeof loadPageInit === 'function') loadPageInit();
            },
            '/reports': () => {
                if (typeof loadReports === 'function') loadReports();
            }
        };

        const init = initMap[path];
        if (init) init();
    },

    updateSidebarActive(path) {
        const sidebar = document.getElementById('sidebar');
        if (!sidebar) return;

        sidebar.querySelectorAll('.sidebar-item').forEach(item => {
            item.classList.remove('active', 'text-white');
            item.classList.add('text-gray-300');
            const icon = item.querySelector('i');
            if (icon) { icon.classList.remove('text-blue-400'); icon.classList.add('text-gray-400'); }
            const text = item.querySelector('.sidebar-text');
            if (text) text.classList.remove('font-medium');
        });

        const activeLink = sidebar.querySelector(`a[href="${path}"]`);
        if (activeLink) {
            activeLink.classList.remove('text-gray-300');
            activeLink.classList.add('active', 'text-white');
            const icon = activeLink.querySelector('i');
            if (icon) { icon.classList.remove('text-gray-400'); icon.classList.add('text-blue-400'); }
            const text = activeLink.querySelector('.sidebar-text');
            if (text) text.classList.add('font-medium');
            const group = activeLink.closest('.sidebar-group');
            if (group) group.classList.remove('sidebar-group-collapsed');
        }
    },

    setupPopstateListener() {
        window.addEventListener('popstate', (e) => {
            if (e.state && e.state.path && e.state.path !== this._currentPagePath) {
                this.navigate(e.state.path);
            }
        });
    },

    /** 注册页面定时器，导航离开时自动清除 */
    registerTimer(id) {
        this._pageTimers.push(id);
    },

    /** 清除所有已注册的页面定时器 */
    clearPageTimers() {
        this._pageTimers.forEach(id => clearInterval(id));
        this._pageTimers.forEach(id => clearTimeout(id));
        this._pageTimers = [];
    }
};


// ==================== 侧边栏管理 ====================
const Sidebar = {
    _initialized: false,

    init() {
        if (this._initialized) return;
        this._initialized = true;
        this.initGroupToggles();
    },

    initGroupToggles() {
        document.querySelectorAll('.sidebar-group-toggle').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const groupContainer = btn.closest('.sidebar-group');
                if (!groupContainer) return;
                groupContainer.classList.toggle('sidebar-group-collapsed');
                const chevron = btn.querySelector('.sidebar-group-chevron');
                if (chevron) {
                    chevron.classList.toggle('fa-chevron-down');
                    chevron.classList.toggle('fa-chevron-right');
                }
            });
        });
    }
};


// ==================== 页面初始化管理 ====================
const PageInit = {
    init(options = {}) {
        const { onReady = null } = options;
        Sidebar.init();
        if (onReady && typeof onReady === 'function') {
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', onReady);
            } else {
                onReady();
            }
        }
    }
};


// ==================== 工具函数 ====================
const Utils = {
    formatTime(timestamp) {
        if (!timestamp) return '-';
        const date = new Date(timestamp);
        const now = new Date();
        const diff = now - date;
        if (diff < 60000) return '刚刚';
        if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前';
        if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前';
        return date.toLocaleDateString('zh-CN');
    },

    formatDateTime(timestamp) {
        if (!timestamp) return '-';
        const hasTimezone = timestamp.includes('+') || timestamp.includes('Z');
        if (hasTimezone) {
            const date = new Date(timestamp);
            if (!isNaN(date.getTime())) return date.toLocaleString('zh-CN');
        }
        const date = new Date(timestamp.replace(' ', 'T') + 'Z');
        if (!isNaN(date.getTime())) return date.toLocaleString('zh-CN');
        return timestamp;
    },

    debounce(func, wait = 300) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => { clearTimeout(timeout); func(...args); };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    },

    throttle(func, limit = 300) {
        let inThrottle;
        return function executedFunction(...args) {
            if (!inThrottle) { func(...args); inThrottle = true; setTimeout(() => inThrottle = false, limit); }
        };
    },

    deepClone(obj) { return JSON.parse(JSON.stringify(obj)); },
    isEmptyObject(obj) { return Object.keys(obj).length === 0; },
    getUrlParam(name) { const params = new URLSearchParams(window.location.search); return params.get(name); }
};


// ==================== 全局初始化 ====================
document.addEventListener('DOMContentLoaded', () => {
    Router.init();
    if (window.UIUtils) UIUtils.init();
});


// ==================== 导出全局对象 ====================
window.Api = Api;
window.Sidebar = Sidebar;
window.PageInit = PageInit;
window.Utils = Utils;
window.Router = Router;
window.formatDateTime = Utils.formatDateTime.bind(Utils);
window.registerUnmount = (fn) => Router.registerUnmount(fn);
