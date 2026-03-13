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
// Підтвердження небезпечних дій
// =====================================================

function confirmAction(message, callback) {
    if (window.confirm(message)) {
        callback();
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
                    if (balCell) balCell.textContent = r.balance;
                    this._notify();
                };
            }
            if (priceInp) { priceInp.style.display = 'none'; priceInp.name = ''; }
            const sel = rows.find(r => String(r.price) === (priceSel?.value)) || rows[0];
            if (balCell) balCell.textContent = sel.balance;
        } else {
            // Одна ціна — звичайне поле
            if (priceSel) { priceSel.style.display = 'none'; priceSel.name = ''; }
            if (priceInp) {
                priceInp.style.display = '';
                priceInp.name          = 'price[]';
                priceInp.value         = rows[0].price;
            }
            if (balCell) balCell.textContent = rows[0].balance;
        }

        this._notify();
    }

    _notify() {
        if (this._opts.onChange) this._opts.onChange(this);
    }
}


// =====================================================
// Ініціалізація при завантаженні
// =====================================================

document.addEventListener('DOMContentLoaded', () => {
    // Ініціалізація чекбоксів якщо є таблиця
    initCheckboxSelect('.selectable-table');
});
