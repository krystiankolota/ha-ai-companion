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

function renderSuggestions(data) {
    const list = document.getElementById('suggestionsList');
    const status = document.getElementById('suggestionsStatus');
    const suggestions = data.suggestions || [];

    if (data.generated_at) {
        status.textContent = `Last generated: ${formatGeneratedAt(data.generated_at)}`;
        status.style.display = 'block';
    }

    if (suggestions.length === 0) {
        list.innerHTML = '<p class="suggestions-empty">No suggestions yet. Click "Generate suggestions" to get started.</p>';
        return;
    }

    list.innerHTML = suggestions.map((s, i) => {
        const icon = CATEGORY_ICONS[s.category] || CATEGORY_ICONS.other;
        const entities = (s.entities || []).map(e => `<span class="suggestion-entity">${e}</span>`).join('');
        return `
        <div class="suggestion-card">
            <div class="suggestion-card-header">
                <span class="suggestion-icon">${icon}</span>
                <span class="suggestion-title">${escapeHtml(s.title)}</span>
                <span class="suggestion-category">${escapeHtml(s.category || 'other')}</span>
            </div>
            <p class="suggestion-description">${escapeHtml(s.description)}</p>
            ${entities ? `<div class="suggestion-entities">${entities}</div>` : ''}
            ${s.implementation_hint ? `<pre class="suggestion-hint">${escapeHtml(s.implementation_hint)}</pre>` : ''}
            <button class="btn btn-secondary btn-add-to-chat" data-index="${i}">Add to chat</button>
        </div>`;
    }).join('');

    // Wire up "Add to chat" buttons
    list.querySelectorAll('.btn-add-to-chat').forEach(btn => {
        btn.addEventListener('click', () => {
            const s = suggestions[parseInt(btn.dataset.index)];
            addSuggestionToChat(s);
        });
    });
}

function addSuggestionToChat(suggestion) {
    // Switch to the Chat tab
    document.querySelector('.tab-btn[data-tab="chat"]').click();

    const input = document.getElementById('messageInput');
    if (input) {
        input.value = `Please implement this automation suggestion:\n\n**${suggestion.title}**\n${suggestion.description}`;
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

document.addEventListener('DOMContentLoaded', () => {
    initTabs();

    const genBtn = document.getElementById('generateSuggestionsBtn');
    if (genBtn) genBtn.addEventListener('click', generateSuggestions);
});
