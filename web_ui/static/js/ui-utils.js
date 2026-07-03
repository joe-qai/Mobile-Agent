// UI工具函数 - 自定义弹窗和Toast
class UIUtils {
    static init() {
        // 创建Toast容器
        if (!document.getElementById('toast-container')) {
            const container = document.createElement('div');
            container.id = 'toast-container';
            container.className = 'toast-container';
            document.body.appendChild(container);
        }
        
        // 创建确认弹窗容器
        if (!document.getElementById('confirm-overlay')) {
            const overlay = document.createElement('div');
            overlay.id = 'confirm-overlay';
            overlay.className = 'confirm-overlay';
            overlay.innerHTML = `
                <div class="confirm-dialog">
                    <div class="confirm-icon confirm-icon-warning">
                        <i class="fa fa-exclamation-circle"></i>
                    </div>
                    <h3 class="confirm-title" id="confirm-title">确认操作</h3>
                    <p class="confirm-message" id="confirm-message">确定要执行此操作吗？</p>
                    <div class="confirm-actions">
                        <button class="confirm-btn confirm-btn-cancel" id="confirm-cancel">取消</button>
                        <button class="confirm-btn confirm-btn-confirm" id="confirm-ok">确定</button>
                    </div>
                </div>
            `;
            document.body.appendChild(overlay);
            
            // 绑定事件
            document.getElementById('confirm-cancel').addEventListener('click', () => {
                UIUtils.hideConfirm();
            });
        }
    }
    
    // 显示Toast通知
    static toast(type, title, message, duration = 3000) {
        UIUtils.init();
        
        const container = document.getElementById('toast-container');
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        
        const icons = {
            success: 'fa-check-circle',
            error: 'fa-times-circle',
            warning: 'fa-exclamation-circle',
            info: 'fa-info-circle'
        };
        
        toast.innerHTML = `
            <div class="toast-icon">
                <i class="fa ${icons[type] || icons.info}"></i>
            </div>
            <div class="toast-content">
                <div class="toast-title">${title}</div>
                <div class="toast-message">${message}</div>
            </div>
            <button class="toast-close" onclick="this.parentElement.remove()">
                <i class="fa fa-times"></i>
            </button>
        `;
        
        container.appendChild(toast);
        
        // 自动关闭
        setTimeout(() => {
            if (toast.parentElement) {
                toast.style.animation = 'slideOutRight 0.3s ease forwards';
                setTimeout(() => toast.remove(), 300);
            }
        }, duration);
    }
    
    // 显示成功Toast
    static success(title, message) {
        UIUtils.toast('success', title, message);
    }
    
    // 显示错误Toast
    static error(title, message) {
        UIUtils.toast('error', title, message);
    }
    
    // 显示警告Toast
    static warning(title, message) {
        UIUtils.toast('warning', title, message);
    }
    
    // 显示信息Toast
    static info(title, message) {
        UIUtils.toast('info', title, message);
    }
    
    // 显示确认弹窗
    static confirm(options) {
        UIUtils.init();
        
        const overlay = document.getElementById('confirm-overlay');
        const titleEl = document.getElementById('confirm-title');
        const messageEl = document.getElementById('confirm-message');
        const confirmBtn = document.getElementById('confirm-ok');
        const iconEl = overlay.querySelector('.confirm-icon');
        
        // 设置内容
        titleEl.textContent = options.title || '确认操作';
        messageEl.textContent = options.message || '确定要执行此操作吗？';
        
        // 设置图标类型
        const iconClass = options.type === 'danger' ? 'confirm-icon-danger' : 
                         options.type === 'info' ? 'confirm-icon-info' : 'confirm-icon-warning';
        iconEl.className = `confirm-icon ${iconClass}`;
        
        const iconName = options.type === 'danger' ? 'fa-times-circle' : 
                        options.type === 'info' ? 'fa-info-circle' : 'fa-exclamation-circle';
        iconEl.innerHTML = `<i class="fa ${iconName}"></i>`;
        
        // 设置确认按钮文字
        confirmBtn.textContent = options.okText || '确定';
        
        // 移除之前的事件监听
        const newConfirmBtn = confirmBtn.cloneNode(true);
        confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);
        
        // 添加新的事件监听
        newConfirmBtn.addEventListener('click', () => {
            UIUtils.hideConfirm();
            if (options.onConfirm) {
                options.onConfirm();
            }
        });
        
        // 显示弹窗
        overlay.classList.add('show');
    }
    
    // 隐藏确认弹窗
    static hideConfirm() {
        const overlay = document.getElementById('confirm-overlay');
        if (overlay) {
            overlay.classList.remove('show');
        }
    }
    
    // 替代alert
    static alert(title, message, onClose) {
        UIUtils.confirm({
            title: title,
            message: message,
            type: 'info',
            okText: '确定',
            onConfirm: onClose
        });
    }
    
    // 替代prompt - 自定义输入弹窗（支持单选选项）
    static prompt(options) {
        UIUtils.init();
        
        // 创建prompt弹窗容器
        let promptOverlay = document.getElementById('prompt-overlay');
        if (!promptOverlay) {
            promptOverlay = document.createElement('div');
            promptOverlay.id = 'prompt-overlay';
            promptOverlay.className = 'prompt-overlay';
            promptOverlay.innerHTML = `
                <div class="prompt-dialog">
                    <div class="prompt-icon prompt-icon-info">
                        <i class="fa fa-question-circle"></i>
                    </div>
                    <h3 class="prompt-title" id="prompt-title">请输入</h3>
                    <p class="prompt-message" id="prompt-message">请输入内容</p>
                    <div class="prompt-content" id="prompt-content">
                        <input type="text" class="prompt-input" id="prompt-input" placeholder="">
                    </div>
                    <div class="prompt-actions">
                        <button class="prompt-btn prompt-btn-cancel" id="prompt-cancel">取消</button>
                        <button class="prompt-btn prompt-btn-confirm" id="prompt-ok">确定</button>
                    </div>
                </div>
            `;
            document.body.appendChild(promptOverlay);
            
            // 绑定取消按钮事件
            document.getElementById('prompt-cancel').addEventListener('click', () => {
                UIUtils.hidePrompt();
            });
        }
        
        const titleEl = document.getElementById('prompt-title');
        const messageEl = document.getElementById('prompt-message');
        const contentEl = document.getElementById('prompt-content');
        const confirmBtn = document.getElementById('prompt-ok');
        
        // 设置内容
        titleEl.textContent = options.title || '请输入';
        messageEl.textContent = options.message || '请输入内容';
        
        // 根据是否有选项来决定显示输入框还是单选按钮
        if (options.options && options.options.length > 0) {
            // 显示单选按钮
            let optionsHtml = '<div class="prompt-radio-group">';
            options.options.forEach((opt, index) => {
                const isSelected = options.defaultValue === opt.value || 
                                  (index === 0 && !options.defaultValue);
                optionsHtml += `
                    <label class="prompt-radio-label">
                        <input type="radio" name="prompt-radio" value="${opt.value}" ${isSelected ? 'checked' : ''}>
                        <span class="prompt-radio-dot"></span>
                        <span class="prompt-radio-text">${opt.label}</span>
                    </label>
                `;
            });
            optionsHtml += '</div>';
            contentEl.innerHTML = optionsHtml;
        } else {
            // 显示输入框
            contentEl.innerHTML = '<input type="text" class="prompt-input" id="prompt-input" placeholder="">';
            const inputEl = document.getElementById('prompt-input');
            inputEl.value = options.defaultValue || '';
            inputEl.placeholder = options.placeholder || '';
            
            // 聚焦输入框
            setTimeout(() => {
                inputEl.focus();
            }, 100);
        }
        
        // 移除之前的事件监听
        const newConfirmBtn = confirmBtn.cloneNode(true);
        confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);
        
        // 添加新的事件监听
        newConfirmBtn.addEventListener('click', () => {
            let value = '';
            if (options.options && options.options.length > 0) {
                // 获取选中的单选按钮值
                const selectedRadio = contentEl.querySelector('input[name="prompt-radio"]:checked');
                value = selectedRadio ? selectedRadio.value : '';
            } else {
                // 获取输入框值
                const inputEl = document.getElementById('prompt-input');
                value = inputEl.value;
            }
            UIUtils.hidePrompt();
            if (options.onConfirm) {
                options.onConfirm(value);
            }
        });
        
        // 显示弹窗
        promptOverlay.classList.add('show');
    }
    
    // 隐藏prompt弹窗
    static hidePrompt() {
        const overlay = document.getElementById('prompt-overlay');
        if (overlay) {
            overlay.classList.remove('show');
        }
    }
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    UIUtils.init();
});

// 全局暴露
window.UIUtils = UIUtils;