/**
 * Suggestions tab — load and generate AI automation suggestions.
 */

const CATEGORY_ICONS = {
    lighting: '💡',
    climate: '🌡️',
    security: '🔒',
    energy: '⚡',
    comfort: '🛋️',
    other: '🤖',
};

function formatGeneratedAt(iso) {
    if (!iso) return '';
    try {
        const d = new Date(iso);
        return d.toLocaleString();
    } catch {
        return iso;
    }
}

let dismissedTitles = new Set();

async function loadDismissed() {
    try {
        const resp = await fetch('api/suggestions/dismissed');
        if (resp.ok) {
            const data = await resp.json();
            dismissedTitles = new Set(data.dismissed || []);
        }
    } catch (e) {
        console.warn('Failed to load dismissed suggestions:', e);
    }
}

async function dismissSuggestion(title, card) {
    try {
        await fetch('api/suggestions/dismiss', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        });
        dismissedTitles.add(title);
        card.style.transition = 'opacity 0.3s';
        card.style.opacity = '0';
        setTimeout(() => card.remove(), 300);
    } catch (e) {
        console.warn('Failed to dismiss suggestion:', e);
    }
}

function renderSuggestions(data) {
    const list = document.getElementById('suggestionsList');
    const status = document.getElementById('suggestionsStatus');
    const allSuggestions = data.suggestions || [];
    const suggestions = allSuggestions.filter(s => !dismissedTitles.has(s.title));

    if (data.generated_at) {
        const dismissedCount = allSuggestions.length - suggestions.length;
        const extra = dismissedCount > 0 ? ` (${dismissedCount} dismissed)` : '';
        status.textContent = `Last generated: ${formatGeneratedAt(data.generated_at)}${extra}`;
        status.style.display = 'block';
    }

    if (suggestions.length === 0) {
        list.innerHTML = '<p class="suggestions-empty">No suggestions yet. Click "Generate suggestions" to get started.</p>';
        return;
    }

    list.innerHTML = suggestions.map((s, i) => {
        const icon = CATEGORY_ICONS[s.category] || CATEGORY_ICONS.other;
        const entities = (s.entities || []).map(e => `<span class="suggestion-entity">${e}</span>`).join('');
        const typeBadge = s.type === 'improvement'
            ? `<span class="suggestion-type suggestion-type--improvement">improvement</span>`
            : `<span class="suggestion-type suggestion-type--new">new</span>`;
        const yamlBlock = s.yaml_block
            ? `<div class="suggestion-yaml-wrap">
                <div class="suggestion-yaml-header">
                    <span class="suggestion-yaml-label">YAML</span>
                    <button class="btn-copy-yaml" data-index="${i}" title="Copy YAML">Copy</button>
                </div>
                <pre class="suggestion-hint suggestion-yaml">${escapeHtml(s.yaml_block)}</pre>
               </div>`
            : (s.implementation_hint ? `<pre class="suggestion-hint">${escapeHtml(s.implementation_hint)}</pre>` : '');
        return `
        <div class="suggestion-card" data-title="${escapeHtml(s.title)}">
            <div class="suggestion-card-header">
                <span class="suggestion-icon">${icon}</span>
                <span class="suggestion-title">${escapeHtml(s.title)}</span>
                ${typeBadge}
                <span class="suggestion-category">${escapeHtml(s.category || 'other')}</span>
                <button class="btn btn-dismiss" title="Don't suggest this again">✕</button>
            </div>
            <p class="suggestion-description">${escapeHtml(s.description)}</p>
            ${entities ? `<div class="suggestion-entities">${entities}</div>` : ''}
            ${yamlBlock}
            <button class="btn btn-secondary btn-add-to-chat" data-index="${i}">Add to chat</button>
        </div>`;
    }).join('');

    // Wire up buttons
    list.querySelectorAll('.btn-add-to-chat').forEach(btn => {
        btn.addEventListener('click', () => {
            const s = suggestions[parseInt(btn.dataset.index)];
            addSuggestionToChat(s);
        });
    });

    list.querySelectorAll('.btn-dismiss').forEach(btn => {
        btn.addEventListener('click', () => {
            const card = btn.closest('.suggestion-card');
            const title = card.dataset.title;
            dismissSuggestion(title, card);
        });
    });

    list.querySelectorAll('.btn-copy-yaml').forEach(btn => {
        btn.addEventListener('click', () => {
            const s = suggestions[parseInt(btn.dataset.index)];
            if (!s || !s.yaml_block) return;
            navigator.clipboard.writeText(s.yaml_block).then(() => {
                btn.textContent = 'Copied!';
                setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
            }).catch(() => {
                btn.textContent = 'Failed';
                setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
            });
        });
    });
}

function addSuggestionToChat(suggestion) {
    // Switch to the Chat tab
    document.querySelector('.tab-btn[data-tab="chat"]').click();

    const input = document.getElementById('messageInput');
    if (input) {
        let msg = `Please implement this automation suggestion:\n\n**${suggestion.title}**\n${suggestion.description}`;
        if (suggestion.yaml_block) {
            msg += `\n\nStarting YAML:\n\`\`\`yaml\n${suggestion.yaml_block}\n\`\`\``;
        }
        input.value = msg;
        input.focus();
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

async function loadSuggestions() {
    try {
        const resp = await fetch('api/suggestions');
        if (resp.ok) {
            const data = await resp.json();
            renderSuggestions(data);
        }
    } catch (e) {
        console.warn('Failed to load suggestions:', e);
    }
}

async function generateSuggestions() {
    const btn = document.getElementById('generateSuggestionsBtn');
    const status = document.getElementById('suggestionsStatus');
    const list = document.getElementById('suggestionsList');

    btn.disabled = true;
    btn.textContent = 'Generating…';
    status.textContent = 'Fetching entity states and generating suggestions…';
    status.style.display = 'block';
    list.innerHTML = '';

    try {
        const resp = await fetch('api/suggestions/generate', { method: 'POST' });
        if (resp.ok) {
            const data = await resp.json();
            renderSuggestions(data);
        } else {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            status.textContent = `Error: ${err.detail || 'Generation failed'}`;
        }
    } catch (e) {
        status.textContent = `Error: ${e.message}`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Generate suggestions';
    }
}

// Tab switching logic
function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const target = btn.dataset.tab;

            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));

            btn.classList.add('active');
            const panel = document.getElementById(`tab-${target}`);
            if (panel) panel.classList.add('active');

            // Load suggestions when switching to that tab
            if (target === 'suggestions') {
                loadSuggestions();
            }
        });
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    await loadDismissed();
    initTabs();

    const genBtn = document.getElementById('generateSuggestionsBtn');
    if (genBtn) genBtn.addEventListener('click', generateSuggestions);
});
