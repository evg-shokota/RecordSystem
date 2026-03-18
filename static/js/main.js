/**
 * main.js — базовий JS для системи обліку А5027
 */

'use strict';

// =====================================================
// AJAX helper
// =====================================================

async function apiGet(url) {
    const resp = await fetch(url, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    });
    if (resp.status === 401) { window.location = '/login'; return null; }
    return resp.json();
}

async function apiPost(url, data) {
    const resp = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
        },
        body: JSON.stringify(data)
    });
    if (resp.status === 401) { window.location = '/login'; return null; }
    return resp.json();
}

// =====================================================
// Автодоповнення для словника майна
// =====================================================

function initAutocomplete(inputEl, suggestionsEl, url) {
    let debounceTimer;

    inputEl.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        const query = inputEl.value.trim();
        if (query.length < 2) {
            suggestionsEl.style.display = 'none';
            return;
        }
        debounceTimer = setTimeout(async () => {
            const data = await apiGet(`${url}?q=${encodeURIComponent(query)}`);
            if (!data || !data.items) return;
            renderSuggestions(inputEl, suggestionsEl, data.items);
        }, 200);
    });

    document.addEventListener('click', (e) => {
        if (!suggestionsEl.contains(e.target) && e.target !== inputEl) {
            suggestionsEl.style.display = 'none';
        }
    });
}

function renderSuggestions(inputEl, suggestionsEl, items) {
    suggestionsEl.innerHTML = '';
    if (!items.length) {
        suggestionsEl.style.display = 'none';
        return;
    }
    items.forEach(item => {
        const div = document.createElement('div');
        div.className = 'autocomplete-item';
        div.textContent = item.name + (item.unit_of_measure ? ` (${item.unit_of_measure})` : '');
        div.dataset.id = item.id;
        div.dataset.name = item.name;
        div.addEventListener('click', () => {
            inputEl.value = item.name;
            inputEl.dataset.itemId = item.id;
            suggestionsEl.style.display = 'none';
            inputEl.dispatchEvent(new CustomEvent('item-selected', { detail: item }));
        });
        suggestionsEl.appendChild(div);
    });
    suggestionsEl.style.display = 'block';
}

// =====================================================
// Підтвердження небезпечних дій — Bootstrap Modal
// =====================================================

/**
 * showConfirm(message, onConfirm, opts)
 * opts: { title, confirmLabel, confirmClass, cancelLabel, onCancel }
 * Якщо #confirmModal не існує (напр. login page) — fallback до window.confirm
 */
function showConfirm(message, onConfirm, opts) {
    opts = opts || {};
    var modal = document.getElementById('confirmModal');
    if (!modal) {
        if (window.confirm(message)) onConfirm();
        else if (opts.onCancel) opts.onCancel();
        return;
    }
    var msgEl   = document.getElementById('confirmModalMessage');
    var titleEl = document.getElementById('confirmModalTitle');
    var okBtn   = document.getElementById('confirmModalOk');
    var cancelEl= document.getElementById('confirmModalCancel');

    if (titleEl)  titleEl.textContent  = opts.title  || 'Підтвердити дію';
    if (msgEl)    msgEl.textContent    = message;
    if (okBtn) {
        okBtn.textContent  = opts.confirmLabel || 'Підтвердити';
        okBtn.className    = 'btn btn-sm ' + (opts.confirmClass || 'btn-danger');
    }
    if (cancelEl) cancelEl.textContent = opts.cancelLabel || 'Скасувати';

    var bsModal = bootstrap.Modal.getOrCreateInstance(modal);

    // Прибираємо старі обробники (clone trick)
    var newOk     = okBtn.cloneNode(true);
    var newCancel = cancelEl ? cancelEl.cloneNode(true) : null;
    okBtn.parentNode.replaceChild(newOk, okBtn);
    if (cancelEl && newCancel) cancelEl.parentNode.replaceChild(newCancel, cancelEl);

    newOk.addEventListener('click', function() {
        bsModal.hide();
        onConfirm();
    });

    if (newCancel && opts.onCancel) {
        newCancel.addEventListener('click', function() {
            // data-bs-dismiss="modal" вже є на кнопці — modal закриється сам
            setTimeout(opts.onCancel, 200);
        });
    }

    bsModal.show();
}

// Зворотна сумісність
function confirmAction(message, callback) { showConfirm(message, callback); }

// =====================================================
// Loading state для кнопок
// setBtnLoading(btn, true)  — показати спіннер, задизейблити
// setBtnLoading(btn, false) — відновити оригінальний HTML
// =====================================================

function setBtnLoading(btn, loading) {
    if (!btn) return;
    if (loading) {
        btn._origHtml = btn.innerHTML;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>';
        btn.disabled = true;
    } else {
        if (btn._origHtml !== undefined) btn.innerHTML = btn._origHtml;
        btn.disabled = false;
    }
}

// =====================================================
// Toast повідомлення
// =====================================================

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const el = document.createElement('div');
    el.className = `toast-item toast-${type}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => el.classList.add('show'), 10);
    setTimeout(() => {
        el.classList.remove('show');
        setTimeout(() => el.remove(), 300);
    }, 3500);
}

// =====================================================
// data-confirm — глобальний перехоплювач для форм і кнопок
// Додай data-confirm="Текст підтвердження" до <form> або <button>/<a>
// Опційно: data-confirm-title="Заголовок" data-confirm-class="btn-danger"
// =====================================================

document.addEventListener('DOMContentLoaded', function() {
    // Форми з data-confirm замість onsubmit="return confirm(...)"
    document.addEventListener('submit', function(e) {
        var form = e.target.closest('form[data-confirm]');
        if (!form) return;
        var msg   = form.dataset.confirm;
        var title = form.dataset.confirmTitle || 'Підтвердити дію';
        var cls   = form.dataset.confirmClass || 'btn-danger';
        e.preventDefault();
        showConfirm(msg, function() {
            form.removeAttribute('data-confirm');
            form.submit();
        }, { title: title, confirmClass: cls });
    }, true);

    // Кнопки/посилання з data-confirm
    document.addEventListener('click', function(e) {
        var el = e.target.closest('[data-confirm]:not(form)');
        if (!el) return;
        var msg   = el.dataset.confirm;
        var title = el.dataset.confirmTitle || 'Підтвердити дію';
        var cls   = el.dataset.confirmClass || 'btn-danger';
        e.preventDefault();
        e.stopPropagation();
        var href = el.tagName === 'A' ? el.href : null;
        showConfirm(msg, function() {
            if (href) { location.href = href; return; }
            // Для кнопок у формі — submit батьківської форми
            var parentForm = el.closest('form');
            if (parentForm) {
                el.removeAttribute('data-confirm');
                parentForm.submit();
            }
        }, { title: title, confirmClass: cls });
    }, true);
});

// =====================================================
// Глобальний loading state для submit-кнопок
// Для форм без data-confirm: кнопка з type=submit автоматично
// отримує спіннер при submit (запобігає подвійному кліку).
// Щоб вимкнути для конкретної кнопки: data-no-loading
// =====================================================

document.addEventListener('DOMContentLoaded', function() {
    document.addEventListener('submit', function(e) {
        var form = e.target.closest('form');
        if (!form || form.dataset.confirm) return; // з data-confirm — не чіпаємо (там окрема логіка)
        var btn = form.querySelector('[type=submit]:not([data-no-loading])');
        if (btn) setBtnLoading(btn, true);
    });
});

// =====================================================
// Чекбокси для масового вибору
// =====================================================

function initCheckboxSelect(tableSelector) {
    const table = document.querySelector(tableSelector);
    if (!table) return;

    const selectAll = table.querySelector('.select-all');
    const checkboxes = table.querySelectorAll('.row-check');

    if (selectAll) {
        selectAll.addEventListener('change', () => {
            checkboxes.forEach(cb => cb.checked = selectAll.checked);
            updateBulkActions();
        });
    }

    checkboxes.forEach(cb => {
        cb.addEventListener('change', updateBulkActions);
    });
}

function getSelectedIds(tableSelector) {
    const table = document.querySelector(tableSelector);
    if (!table) return [];
    return Array.from(table.querySelectorAll('.row-check:checked'))
        .map(cb => parseInt(cb.dataset.id));
}

function updateBulkActions() {
    const selected = document.querySelectorAll('.row-check:checked').length;
    const bulkBar = document.getElementById('bulk-actions');
    if (bulkBar) {
        bulkBar.style.display = selected > 0 ? 'flex' : 'none';
        const countEl = bulkBar.querySelector('.selected-count');
        if (countEl) countEl.textContent = `Обрано: ${selected}`;
    }
}

// =====================================================
// SearchableSelect — select з полем пошуку
// =====================================================

/**
 * SearchableSelect — перетворює hidden input + масив опцій на searchable select.
 *
 * Використання:
 *   const ss = new SearchableSelect({
 *     container: document.getElementById('myWrapper'),  // елемент куди рендерити
 *     hiddenInput: document.getElementById('myHidden'), // hidden input для значення
 *     options: [
 *       { value: '1', label: 'Назва', group: 'Група', meta: { unit: 'шт' } },
 *       ...
 *     ],
 *     placeholder: 'Оберіть...',
 *     allowClear: true,
 *     size: 'sm',   // 'sm' | '' | 'lg'
 *     onChange: (value, option) => { ... }
 *   });
 *   ss.setValue('3');   // програмно встановити
 *   ss.reset();         // скинути
 *   ss.getValue();      // поточне value
 */
class SearchableSelect {
    constructor(cfg) {
        this.cfg        = cfg;
        this.options    = cfg.options || [];
        this.value      = cfg.hiddenInput ? cfg.hiddenInput.value : '';
        this.label      = '';
        this._open      = false;
        this._focused   = -1;

        // Встановити початковий label якщо є value
        if (this.value) {
            const found = this.options.find(o => String(o.value) === String(this.value));
            if (found) this.label = found.label;
        }

        this._build();
    }

    _build() {
        const cfg = this.cfg;
        const wrap = cfg.container;
        wrap.classList.add('ss-wrapper');
        wrap._ssInstance = this;  // зберегти посилання для пошуку через DOM

        // Display (кнопка-відображення)
        this._display = document.createElement('div');
        this._display.className = 'ss-display' + (cfg.size === 'sm' ? ' ss-sm' : '');
        this._display.tabIndex = 0;
        this._display.setAttribute('role', 'combobox');

        this._labelEl = document.createElement('span');
        this._labelEl.className = 'ss-label' + (this.label ? '' : ' ss-placeholder');
        this._labelEl.textContent = this.label || (cfg.placeholder || '— оберіть —');
        this._display.appendChild(this._labelEl);

        if (cfg.allowClear !== false) {
            this._clearBtn = document.createElement('button');
            this._clearBtn.type = 'button';
            this._clearBtn.className = 'ss-clear';
            this._clearBtn.innerHTML = '&#x2715;';
            this._clearBtn.title = 'Скинути';
            this._clearBtn.style.display = this.value ? '' : 'none';
            this._clearBtn.addEventListener('click', e => { e.stopPropagation(); this.reset(); });
            this._display.appendChild(this._clearBtn);
        }

        const arrow = document.createElement('span');
        arrow.className = 'ss-arrow bi bi-chevron-down';
        this._display.appendChild(arrow);

        wrap.appendChild(this._display);

        // Dropdown — рендерується в body щоб не обрізатись overflow:hidden таблиць
        this._dropdown = document.createElement('div');
        this._dropdown.className = 'ss-dropdown ss-dropdown-fixed';
        this._dropdown.style.display = 'none';

        // Поле пошуку
        const sw = document.createElement('div');
        sw.className = 'ss-search-wrap';
        this._searchInput = document.createElement('input');
        this._searchInput.type = 'text';
        this._searchInput.placeholder = 'Пошук...';
        this._searchInput.autocomplete = 'off';
        sw.appendChild(this._searchInput);
        this._dropdown.appendChild(sw);

        // Список опцій
        this._optionsList = document.createElement('div');
        this._optionsList.className = 'ss-options';
        this._dropdown.appendChild(this._optionsList);

        // Додаємо дропдаун прямо в body
        document.body.appendChild(this._dropdown);

        // Прив'язати події
        // mousedown + preventDefault — щоб відкриття/закриття не перехоплювало фокус від searchInput
        this._display.addEventListener('mousedown', e => {
            e.preventDefault();
            e.stopPropagation();
            this._toggle();
        });
        this._display.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this._toggle(); }
            if (e.key === 'Escape') this._close();
        });

        // mousedown всередині dropdown не закриває і не перехоплює фокус від searchInput
        this._dropdown.addEventListener('mousedown', e => e.stopPropagation());

        this._searchInput.addEventListener('input', () => this._renderOptions(this._searchInput.value));
        this._searchInput.addEventListener('keydown', e => {
            if (e.key === 'Escape') { e.stopPropagation(); this._close(); }
            if (e.key === 'ArrowDown') { e.preventDefault(); this._moveFocus(1); }
            if (e.key === 'ArrowUp')   { e.preventDefault(); this._moveFocus(-1); }
            if (e.key === 'Enter')     { e.preventDefault(); this._selectFocused(); }
        });

        document.addEventListener('mousedown', () => {
            if (this._open) this._close();
        });

        // Оновлювати позицію при прокрутці
        this._scrollHandler = () => { if (this._open) this._positionDropdown(); };
        window.addEventListener('scroll', this._scrollHandler, true);

        this._renderOptions('');
    }

    _positionDropdown() {
        const rect = this._display.getBoundingClientRect();
        const dd = this._dropdown;
        const viewH = window.innerHeight;
        const ddH = 280; // max-height з CSS

        // Відкрити вниз або вгору залежно від місця
        const spaceBelow = viewH - rect.bottom;
        if (spaceBelow >= 150 || spaceBelow >= ddH) {
            dd.style.top  = (rect.bottom + window.scrollY) + 'px';
            dd.style.bottom = 'auto';
        } else {
            dd.style.top  = 'auto';
            dd.style.bottom = (viewH - rect.top - window.scrollY) + 'px';
        }
        dd.style.left  = (rect.left + window.scrollX) + 'px';
        dd.style.width = rect.width + 'px';
    }

    _toggle() {
        this._open ? this._close() : this._openDropdown();
    }

    _openDropdown() {
        this._open = true;
        this._display.classList.add('ss-open');
        this._positionDropdown();
        this._dropdown.style.display = 'flex';
        this._searchInput.value = '';
        this._renderOptions('');
        this._focused = -1;
        // Якщо всередині Bootstrap Modal — вимкнути enforceFocus щоб дати фокус searchInput в body
        this._modalEl = this._display.closest('.modal');
        if (this._modalEl) {
            const bsModal = bootstrap.Modal.getInstance(this._modalEl);
            if (bsModal) { bsModal._config.focus = false; }
            this._modalEl.addEventListener('focusin', this._modalFocusTrap = e => {
                e.stopImmediatePropagation();
            }, true);
        }
        setTimeout(() => this._searchInput.focus(), 0);
    }

    _close() {
        this._open = false;
        this._display.classList.remove('ss-open');
        this._dropdown.style.display = 'none';
        // Відновити enforceFocus модалі
        if (this._modalEl) {
            if (this._modalFocusTrap) {
                this._modalEl.removeEventListener('focusin', this._modalFocusTrap, true);
                this._modalFocusTrap = null;
            }
            const bsModal = bootstrap.Modal.getInstance(this._modalEl);
            if (bsModal) { bsModal._config.focus = true; }
            this._modalEl = null;
        }
    }

    _renderOptions(query) {
        const q = query.toLowerCase().trim();
        this._optionsList.innerHTML = '';
        this._focused = -1;

        const filtered = q
            ? this.options.filter(o => o.label.toLowerCase().includes(q) || (o.group || '').toLowerCase().includes(q))
            : this.options;

        if (!filtered.length) {
            const noRes = document.createElement('div');
            noRes.className = 'ss-no-results';
            noRes.textContent = q ? 'Нічого не знайдено' : 'Список порожній';
            this._optionsList.appendChild(noRes);
            return;
        }

        // Групування
        const groups = {};
        const noGroup = [];
        filtered.forEach(o => {
            if (o.group) {
                (groups[o.group] = groups[o.group] || []).push(o);
            } else {
                noGroup.push(o);
            }
        });

        const renderOption = (o) => {
            const div = document.createElement('div');
            div.className = 'ss-option' + (String(o.value) === String(this.value) ? ' ss-selected' : '');
            div.textContent = o.label;
            div.dataset.value = o.value;
            div.addEventListener('mousedown', e => { e.preventDefault(); this._select(o); });
            this._optionsList.appendChild(div);
        };

        noGroup.forEach(renderOption);
        Object.entries(groups).forEach(([name, opts]) => {
            const gl = document.createElement('div');
            gl.className = 'ss-group-label';
            gl.textContent = name;
            this._optionsList.appendChild(gl);
            opts.forEach(renderOption);
        });
    }

    _moveFocus(dir) {
        const items = this._optionsList.querySelectorAll('.ss-option');
        if (!items.length) return;
        items[this._focused]?.classList.remove('ss-focused');
        this._focused = Math.max(0, Math.min(items.length - 1, this._focused + dir));
        items[this._focused].classList.add('ss-focused');
        items[this._focused].scrollIntoView({ block: 'nearest' });
    }

    _selectFocused() {
        const items = this._optionsList.querySelectorAll('.ss-option');
        if (this._focused >= 0 && items[this._focused]) {
            const val = items[this._focused].dataset.value;
            const opt = this.options.find(o => String(o.value) === val);
            if (opt) this._select(opt);
        }
    }

    _select(option) {
        this.value = option.value;
        this.label = option.label;
        this._labelEl.textContent = option.label;
        this._labelEl.classList.remove('ss-placeholder');
        if (this._clearBtn) this._clearBtn.style.display = '';
        if (this.cfg.hiddenInput) this.cfg.hiddenInput.value = option.value;
        this._close();
        if (this.cfg.onChange) this.cfg.onChange(option.value, option);
    }

    reset() {
        this.value = '';
        this.label = '';
        this._labelEl.textContent = this.cfg.placeholder || '— оберіть —';
        this._labelEl.classList.add('ss-placeholder');
        if (this._clearBtn) this._clearBtn.style.display = 'none';
        if (this.cfg.hiddenInput) this.cfg.hiddenInput.value = '';
        this._renderOptions('');
        if (this.cfg.onChange) this.cfg.onChange('', null);
    }

    setValue(val) {
        const opt = this.options.find(o => String(o.value) === String(val));
        if (opt) this._select(opt);
        else this.reset();
    }

    getValue() { return this.value; }

    /** Оновити список опцій (для динамічного додавання нових позицій) */
    addOption(option) {
        this.options.push(option);
        this._renderOptions(this._searchInput ? this._searchInput.value : '');
    }
}

// =====================================================
// Захист від незбережених змін (unsaved changes guard)
// =====================================================

(function() {
    // Активується на сторінках з атрибутом data-unsaved-guard або класом unsaved-guard
    let _isDirty = false;
    let _saveDraftFn = null;  // callback для збереження чернетки

    window.UnsavedGuard = {
        enable(saveDraftCallback) {
            _isDirty = false;
            _saveDraftFn = saveDraftCallback || null;
        },
        markDirty()  { _isDirty = true; },
        markClean()  { _isDirty = false; },
        isDirty()    { return _isDirty; },
    };

    // beforeunload — браузерний діалог при закритті вкладки/F5
    window.addEventListener('beforeunload', function(e) {
        if (!_isDirty) return;
        e.preventDefault();
        e.returnValue = '';
    });

    // Перехоплення кліків по навігації всередині застосунку
    document.addEventListener('click', function(e) {
        if (!_isDirty) return;
        const link = e.target.closest('a[href]');
        if (!link) return;
        const href = link.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('javascript')) return;
        // Не перехоплювати якщо це submit-кнопка або посилання на поточну сторінку
        if (link.classList.contains('no-guard')) return;

        e.preventDefault();
        _showUnsavedModal(href);
    });

    function _showUnsavedModal(targetHref) {
        let modal = document.getElementById('_unsavedGuardModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = '_unsavedGuardModal';
            modal.className = 'modal fade';
            modal.tabIndex = -1;
            modal.innerHTML = `
<div class="modal-dialog modal-dialog-centered">
  <div class="modal-content">
    <div class="modal-header bg-warning-subtle">
      <h5 class="modal-title"><i class="bi bi-exclamation-triangle-fill text-warning me-2"></i>Незбережені зміни</h5>
      <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
    </div>
    <div class="modal-body">
      <p class="mb-0">Ви маєте незбережені зміни. Якщо перейдете зараз — вони будуть втрачені.</p>
    </div>
    <div class="modal-footer gap-2 flex-wrap">
      <button type="button" class="btn btn-outline-secondary btn-sm" data-bs-dismiss="modal">
        <i class="bi bi-arrow-left me-1"></i>Залишитись
      </button>
      <button type="button" class="btn btn-warning btn-sm" id="_ugSaveDraft">
        <i class="bi bi-floppy me-1"></i>Зберегти чернетку
      </button>
      <button type="button" class="btn btn-danger btn-sm" id="_ugLeave">
        <i class="bi bi-box-arrow-right me-1"></i>Покинути без збереження
      </button>
    </div>
  </div>
</div>`;
            document.body.appendChild(modal);
        }

        const bsModal = new bootstrap.Modal(modal);
        bsModal.show();

        document.getElementById('_ugLeave').onclick = function() {
            _isDirty = false;
            bsModal.hide();
            window.location.href = targetHref;
        };

        const saveDraftBtn = document.getElementById('_ugSaveDraft');
        if (_saveDraftFn) {
            saveDraftBtn.style.display = '';
            saveDraftBtn.onclick = function() {
                bsModal.hide();
                _saveDraftFn(targetHref);
            };
        } else {
            saveDraftBtn.style.display = 'none';
        }
    }
})();

// =====================================================
// StockItemRow — вибір майна зі складу (єдиний клас
// для накладних, РВ та будь-яких інших документів)
// =====================================================

/**
 * StockItemRow — ініціалізує один рядок вибору майна зі складу.
 *
 * Використання:
 *   const row = new StockItemRow(container, stockData, options);
 *
 * container  — DOM-елемент рядка (tr або div) що містить:
 *   .item-sel       — hidden input для item_id
 *   .item-ss-wrap   — контейнер для SearchableSelect
 *   .cat-sel        — <select> категорії
 *   .qty-input      — input кількості
 *   .price-sel      — <select> ціни (якщо кілька)
 *   .price-input    — <input> ціни (якщо одна)
 *   .balance-cell   — елемент відображення залишку
 *   .unit-label     — елемент одиниці виміру
 *   .serial-field   — input серійних номерів (опційно)
 *
 * stockData  — масив записів складу від сервера:
 *   [{item_id, item_name, unit_of_measure, has_serial_number,
 *     category, price, balance}, ...]
 *
 * options (необов'язково):
 *   onChange(row)   — callback при будь-якій зміні
 *   showSerial      — true (default) — показувати поле серійних номерів
 */
class StockItemRow {
    constructor(container, stockData, options = {}) {
        this._el      = container;
        this._opts    = options;
        this._ss      = null; // SearchableSelect instance

        // Побудувати індекс і список опцій зі stockData
        this._index   = {};   // {item_id: [stock_records]}
        this._options = [];   // [{value, label, meta}] для SearchableSelect — унікальні

        const seen = new Set();
        for (const s of stockData) {
            if (!this._index[s.item_id]) this._index[s.item_id] = [];
            this._index[s.item_id].push(s);
            if (!seen.has(s.item_id)) {
                seen.add(s.item_id);
                this._options.push({
                    value: String(s.item_id),
                    label: s.item_name,
                    meta:  { unit: s.unit_of_measure, serial: s.has_serial_number },
                });
            }
        }

        this._init();
    }

    // ── Геттери поточних значень ──────────────────────────────────
    get itemId()   { return parseInt(this._el.querySelector('.item-sel')?.value) || null; }
    get category() { return this._el.querySelector('.cat-sel')?.value || ''; }
    get qty()      { return parseFloat(this._el.querySelector('.qty-input')?.value) || 0; }
    get price() {
        const sel = this._el.querySelector('.price-sel[name]');
        if (sel) return parseFloat(sel.value) || 0;
        return parseFloat(this._el.querySelector('.price-input')?.value) || 0;
    }
    get sum()      { return this.qty * this.price; }

    // ── Встановити початкові значення (при редагуванні) ───────────
    setValue({ itemId, category, price, qty, serials } = {}) {
        if (itemId) this._ss?.setValue(String(itemId));
        if (category) {
            const cat = this._el.querySelector('.cat-sel');
            if (cat) {
                cat.value = category;
                cat.dispatchEvent(Object.assign(new Event('change'), { _keepPrice: String(price || 0) }));
            }
        }
        if (qty != null) {
            const q = this._el.querySelector('.qty-input');
            if (q) q.value = qty;
        }
        if (serials) {
            const sf = this._el.querySelector('.serial-field');
            if (sf && !sf.disabled) sf.value = serials;
        }
    }

    // ── Ініціалізація ─────────────────────────────────────────────
    _init() {
        const el         = this._el;
        const hiddenInp  = el.querySelector('.item-sel');
        const ssWrap     = el.querySelector('.item-ss-wrap');
        const catSel     = el.querySelector('.cat-sel');
        const qtyInp     = el.querySelector('.qty-input');

        if (!hiddenInp || !ssWrap || !catSel) return;

        // SearchableSelect
        this._ss = new SearchableSelect({
            container:   ssWrap,
            hiddenInput: hiddenInp,
            options:     this._options,
            placeholder: '— оберіть майно —',
            allowClear:  false,
            size:        'sm',
            onChange: (val) => { if (val) this._onItemChange(val); },
        });

        catSel.addEventListener('change', (e) => {
            this._updatePriceAndBalance(null, e._keepPrice || null);
        });

        if (qtyInp) qtyInp.addEventListener('input', () => this._notify());
    }

    _onItemChange(itemId) {
        const rows      = this._index[parseInt(itemId)] || [];
        const opt       = this._options.find(o => o.value === String(itemId));
        const unit      = opt?.meta?.unit      || 'шт';
        const hasSerial = opt?.meta?.serial    || 0;
        const el        = this._el;

        // Одиниця виміру
        const unitEl = el.querySelector('.unit-label');
        if (unitEl) unitEl.textContent = unit;

        // Категорії — тільки ті що є в наявності
        const catSel     = el.querySelector('.cat-sel');
        const currentCat = catSel.value;
        catSel.innerHTML = '';
        const avail = [...new Set(rows.map(r => r.category))];
        ['I', 'II', 'III'].filter(c => avail.includes(c)).forEach(c => {
            catSel.add(new Option('Кат. ' + c, c, c === currentCat, c === currentCat));
        });
        if (!catSel.value && catSel.options.length) catSel.value = catSel.options[0].value;

        this._updatePriceAndBalance(itemId);

        // Серійні номери
        if (this._opts.showSerial !== false) {
            const sf = el.querySelector('.serial-field');
            if (sf) {
                if (hasSerial) {
                    sf.disabled     = false;
                    sf.required     = true;
                    sf.placeholder  = "обов'язково через кому";
                    sf.classList.add('border-warning');
                } else {
                    sf.disabled     = true;
                    sf.required     = false;
                    sf.placeholder  = 'не потрібно';
                    sf.classList.remove('border-warning');
                }
            }
        }

        this._notify();
    }

    _updatePriceAndBalance(itemId, keepPrice = null) {
        const id       = itemId || this.itemId;
        const cat      = this._el.querySelector('.cat-sel')?.value;
        const rows     = (this._index[parseInt(id)] || []).filter(r => r.category === cat);
        const priceSel = this._el.querySelector('.price-sel');
        const priceInp = this._el.querySelector('.price-input');
        const balCell  = this._el.querySelector('.balance-cell');

        if (!rows.length) {
            if (priceSel) { priceSel.style.display = 'none'; priceSel.name = ''; }
            if (priceInp) { priceInp.style.display = ''; priceInp.name = 'price[]'; priceInp.value = '0'; }
            if (balCell)  balCell.textContent = '—';
            this._notify();
            return;
        }

        if (rows.length > 1) {
            // Кілька цін — показуємо dropdown
            const cur = keepPrice || (priceSel && priceSel.value) || String(rows[0].price);
            if (priceSel) {
                priceSel.innerHTML = '';
                rows.forEach(r => {
                    const label = r.price.toFixed(2) + ' грн  (залишок: ' + r.balance + ')';
                    priceSel.add(new Option(label, r.price));
                });
                if ([...priceSel.options].some(o => o.value === cur)) priceSel.value = cur;
                priceSel.style.display = '';
                priceSel.name          = 'price[]';
                priceSel.onchange = () => {
                    const r = rows.find(r => String(r.price) === priceSel.value) || rows[0];
                    if (balCell) {
                        balCell.textContent = r.balance;
                        const freeQty = parseFloat(r.qty_free !== undefined ? r.qty_free : r.balance) || 0;
                        balCell.classList.remove('text-danger', 'text-warning', 'text-success');
                        if (freeQty <= 0) balCell.classList.add('text-danger');
                        else if (freeQty < 5) balCell.classList.add('text-warning');
                        else balCell.classList.add('text-success');
                    }
                    this._notify();
                };
            }
            if (priceInp) { priceInp.style.display = 'none'; priceInp.name = ''; }
            const sel = rows.find(r => String(r.price) === (priceSel?.value)) || rows[0];
            if (balCell) {
                balCell.textContent = sel.balance;
                const freeQty = parseFloat(sel.qty_free !== undefined ? sel.qty_free : sel.balance) || 0;
                balCell.classList.remove('text-danger', 'text-warning', 'text-success');
                if (freeQty <= 0) balCell.classList.add('text-danger');
                else if (freeQty < 5) balCell.classList.add('text-warning');
                else balCell.classList.add('text-success');
            }
        } else {
            // Одна ціна — звичайне поле
            if (priceSel) { priceSel.style.display = 'none'; priceSel.name = ''; }
            if (priceInp) {
                priceInp.style.display = '';
                priceInp.name          = 'price[]';
                priceInp.value         = rows[0].price;
            }
            if (balCell) {
                balCell.textContent = rows[0].balance;
                const freeQty = parseFloat(rows[0].qty_free !== undefined ? rows[0].qty_free : rows[0].balance) || 0;
                balCell.classList.remove('text-danger', 'text-warning', 'text-success');
                if (freeQty <= 0) balCell.classList.add('text-danger');
                else if (freeQty < 5) balCell.classList.add('text-warning');
                else balCell.classList.add('text-success');
            }
        }

        this._notify();
    }

    _notify() {
        if (this._opts.onChange) this._opts.onChange(this);
    }
}


// =====================================================
// DictItemAdder — inline додавання позиції до словника
// майна прямо під рядком таблиці (без модальних вікон).
//
// Використання:
//   // 1. Передати опції норм один раз на сторінці:
//   DictItemAdder.setNormOptions([{value, label, group, meta:{unit}},...]);
//
//   // 2. Для кожного рядка таблиці:
//   const addTr = DictItemAdder.buildTr(colspan);   // створити рядок-розширення
//   mainTr.after(addTr);
//   DictItemAdder.init(addTr, {
//       onSaved(newOpt) { /* новий {value, label, meta} — вибрати його */ },
//       triggerEl,       // елемент що відкриває блок (опційно, для toggle)
//       prefillName,     // рядок для поля "Назва" (опційно)
//   });
//
//   // 3. Кнопка відкриття:
//   btn.addEventListener('click', () => DictItemAdder.toggle(addTr, prefillName));
// =====================================================

const DictItemAdder = (() => {
    let _normOptions = [];

    /** Встановити опції словника норм (один раз на сторінці через Jinja2) */
    function setNormOptions(opts) { _normOptions = opts || []; }

    /** Побудувати <tr class="dict-add-row"> з повною формою. colspan — кількість колонок під даними. */
    function buildTr(colspan = 8) {
        const tr = document.createElement('tr');
        tr.className = 'dict-add-row';
        tr.style.display = 'none';
        tr.innerHTML = `
            <td></td>
            <td colspan="${colspan - 1}">
              <div class="border rounded p-2 mb-1" style="font-size:13px;background:var(--card-bg,#f8f9fa)">
                <div class="fw-medium mb-2 text-muted small">
                  <i class="bi bi-plus-circle me-1"></i>Нова позиція словника майна
                </div>
                <div class="alert alert-danger py-1 small mb-2 dict-add-error" style="display:none"></div>
                <div class="row g-2 mb-2">
                  <div class="col-md-5">
                    <label class="form-label form-label-sm mb-1">Назва <span class="text-danger">*</span></label>
                    <input type="text" class="form-control form-control-sm dict-name" placeholder="Назва майна">
                  </div>
                  <div class="col-md-2">
                    <label class="form-label form-label-sm mb-1">Од. виміру</label>
                    <input type="text" class="form-control form-control-sm dict-unit" value="шт" placeholder="шт, пар...">
                  </div>
                  <div class="col-md-2">
                    <label class="form-label form-label-sm mb-1">Сезон</label>
                    <select class="form-select form-select-sm dict-season">
                      <option value="demi">Демісезон</option>
                      <option value="winter">Зима</option>
                      <option value="summer">Літо</option>
                    </select>
                  </div>
                  <div class="col-md-3">
                    <label class="form-label form-label-sm mb-1">Стать</label>
                    <select class="form-select form-select-sm dict-gender">
                      <option value="unisex">Унісекс</option>
                      <option value="male">Чол.</option>
                      <option value="female">Жін.</option>
                    </select>
                  </div>
                </div>
                <div class="border rounded px-3 py-2 mb-2" style="background:var(--bs-body-bg,#fff)">
                  <div class="row g-0">
                    <div class="col-6 col-md-3 py-1">
                      <div class="form-check form-switch mb-0">
                        <input type="checkbox" class="form-check-input dict-inventory" role="switch">
                        <label class="form-check-label small">Інвентарне</label>
                      </div>
                    </div>
                    <div class="col-6 col-md-3 py-1">
                      <div class="form-check form-switch mb-0">
                        <input type="checkbox" class="form-check-input dict-serial" role="switch">
                        <label class="form-check-label small">Серійний №</label>
                      </div>
                    </div>
                    <div class="col-6 col-md-3 py-1">
                      <div class="form-check form-switch mb-0">
                        <input type="checkbox" class="form-check-input dict-passport" role="switch">
                        <label class="form-check-label small">Паспорт</label>
                      </div>
                    </div>
                    <div class="col-6 col-md-3 py-1">
                      <div class="form-check form-switch mb-0">
                        <input type="checkbox" class="form-check-input dict-exploit" role="switch">
                        <label class="form-check-label small">Акт вв. експл.</label>
                      </div>
                    </div>
                  </div>
                </div>
                <div class="mb-2">
                  <label class="form-label form-label-sm mb-1">
                    Прив'язка до словника норм
                    <span class="text-muted fw-normal">(необов'язково)</span>
                  </label>
                  <input type="hidden" class="dict-norm-id">
                  <div class="dict-norm-ss"></div>
                </div>
                <div class="d-flex gap-2">
                  <button type="button" class="btn btn-sm btn-success btn-dict-save">
                    <i class="bi bi-check-lg me-1"></i>Зберегти та обрати
                  </button>
                  <button type="button" class="btn btn-sm btn-outline-secondary btn-dict-cancel">Скасувати</button>
                </div>
              </div>
            </td>`;
        return tr;
    }

    /**
     * Ініціалізувати вже вставлений addTr.
     * opts.onSaved(newOpt)  — callback після збереження, отримує {value, label, meta}
     * opts.allSsWraps       — масив або NodeList .item-ss-wrap для оновлення всіх SS на сторінці
     */
    function init(addTr, opts = {}) {
        const normHidden = addTr.querySelector('.dict-norm-id');

        // SearchableSelect для норм
        new SearchableSelect({
            container:   addTr.querySelector('.dict-norm-ss'),
            hiddenInput: normHidden,
            options:     _normOptions,
            placeholder: '— не прив\'язано —',
            allowClear:  true,
            size:        'sm',
            onChange(val, opt) {
                if (opt && opt.meta && opt.meta.unit) {
                    const unitInp = addTr.querySelector('.dict-unit');
                    if (!unitInp.value || unitInp.value === 'шт') unitInp.value = opt.meta.unit;
                }
            }
        });

        addTr.querySelector('.btn-dict-cancel').addEventListener('click', () => {
            addTr.style.display = 'none';
        });

        addTr.querySelector('.btn-dict-save').addEventListener('click', async () => {
            const name  = addTr.querySelector('.dict-name').value.trim();
            const errEl = addTr.querySelector('.dict-add-error');
            if (!name) { errEl.textContent = 'Введіть назву'; errEl.style.display = ''; return; }
            errEl.style.display = 'none';

            const fd = new FormData();
            fd.append('name',            name);
            fd.append('unit_of_measure', addTr.querySelector('.dict-unit').value.trim() || 'шт');
            fd.append('season',          addTr.querySelector('.dict-season').value);
            fd.append('gender',          addTr.querySelector('.dict-gender').value);
            if (addTr.querySelector('.dict-inventory').checked) fd.append('is_inventory', '1');
            if (addTr.querySelector('.dict-serial').checked)    fd.append('has_serial_number', '1');
            if (addTr.querySelector('.dict-passport').checked)  fd.append('needs_passport', '1');
            if (addTr.querySelector('.dict-exploit').checked)   fd.append('needs_exploitation_act', '1');
            if (normHidden.value) fd.append('norm_dict_id', normHidden.value);

            const saveBtn = addTr.querySelector('.btn-dict-save');
            saveBtn.disabled = true;
            const resp = await fetch('/settings/items/add', { method: 'POST', body: fd });
            const data = await resp.json();
            saveBtn.disabled = false;

            if (data.ok) {
                const unit      = addTr.querySelector('.dict-unit').value.trim() || 'шт';
                const hasSerial = addTr.querySelector('.dict-serial').checked;
                const newOpt    = { value: String(data.id), label: name, meta: { unit, serial: hasSerial ? 1 : 0 } };

                // Оновити всі SS на сторінці
                const wraps = opts.allSsWraps
                    ? (typeof opts.allSsWraps === 'function' ? opts.allSsWraps() : opts.allSsWraps)
                    : document.querySelectorAll('.item-ss-wrap');
                wraps.forEach(w => { if (w._ssInstance) w._ssInstance.addOption(newOpt); });

                addTr.style.display = 'none';
                if (opts.onSaved) opts.onSaved(newOpt);
                if (typeof showToast === 'function') showToast(`"${name}" додано до словника`, 'success');
            } else {
                errEl.textContent = data.msg || data.error || 'Помилка збереження';
                errEl.style.display = '';
            }
        });
    }

    /**
     * Toggle показу/приховування рядка-розширення.
     * prefillName — рядок для поля "Назва" (наприклад, текст з поля пошуку).
     */
    function toggle(addTr, prefillName) {
        const visible = addTr.style.display !== 'none';
        addTr.style.display = visible ? 'none' : '';
        if (!visible) {
            if (prefillName) addTr.querySelector('.dict-name').value = prefillName;
            setTimeout(() => addTr.querySelector('.dict-name').focus(), 0);
        }
    }

    return { setNormOptions, buildTr, init, toggle };
})();


// =====================================================
// Ініціалізація при завантаженні
// =====================================================

document.addEventListener('DOMContentLoaded', () => {
    // Ініціалізація чекбоксів якщо є таблиця
    initCheckboxSelect('.selectable-table');
});
