// Human-readable labels and arg summaries for each tool name.

export const TOOL_LABELS = {
  search_config_files:   'Reading config files',
  propose_config_changes:'Proposing changes',
  patch_config_key:      'Patching config key',
  patch_config_block:    'Patching config block',
  add_nodered_flow:      'Adding Node-RED flow',
  edit_nodered_tab:      'Editing Node-RED tab',
  get_nodered_flows:     'Reading Node-RED flows',
  save_memory:           'Saving memory',
  search_memory:         'Searching memory',
  delete_memory:         'Retiring memory',
  memory_doctor:         'Checking memory health',
  get_entity_states:     'Reading entity states',
  set_ha_text_entity:    'Updating HA entity',
  schedule_ai_task:      'Scheduling task',
  get_ha_issues:         'Reading HA issues',
  reload_config:         'Reloading config',
  search_past_sessions:  'Searching history',
  list_dashboards:       'Listing dashboards',
  create_dashboard:      'Creating dashboard',
  delete_dashboard:      'Deleting dashboard',
  list_changesets:       'Listing applied changes',
  restore_changeset:     'Preparing restore',
  outline_file:          'Mapping file',
  read_range:            'Reading lines',
  search_entities:       'Finding entities',
  list_automations:      'Listing automations',
  get_automation:        'Reading automation',
  upsert_automation:     'Staging automation',
  remove_automation:     'Staging automation removal',
  get_automation_traces: 'Reading automation traces',
}

export function toolLabel(name) {
  return TOOL_LABELS[name] || name.replace(/_/g, ' ')
}

export function makeArgsSummary(name, args) {
  if (!args) return ''
  try {
    const a = typeof args === 'string' ? JSON.parse(args) : args
    if (name === 'search_config_files' && a.search_pattern) return `"${a.search_pattern}"`
    if (name === 'propose_config_changes' && a.changes) return `${a.changes.length} file(s)`
    if (name === 'patch_config_key') return `${a.file_path} · ${a.key_path}`
    if (name === 'patch_config_block') return `${a.file_path} · ${a.anchor}`
    if (name === 'save_memory' && a.text) return `${a.kind || ''}: ${a.text.slice(0, 50)}`
    if (name === 'delete_memory' && a.id !== undefined) return `#${a.id}`
    if (name === 'search_memory' && a.query) return `"${a.query}"`
    if (name === 'edit_nodered_tab' && a.tab_id) return `tab ${a.tab_id}`
    if (name === 'get_entity_states') {
      if (a.query) return `"${a.query}"`
      if (a.domain_filter) return a.domain_filter
    }
    if (name === 'search_past_sessions' && a.query) return `"${a.query}"`
    if (name === 'set_ha_text_entity' && a.entity_id) return a.entity_id
    if (name === 'schedule_ai_task' && a.entity_id) return `${a.name || ''} → ${a.entity_id}`
    if (name === 'create_dashboard' && a.title) return a.title
    if (name === 'delete_dashboard' && a.url_path) return a.url_path
    if (name === 'restore_changeset' && a.changeset_id) return a.changeset_id
    if (name === 'outline_file' && a.file_path) return a.file_path
    if (name === 'read_range' && a.file_path) return `${a.file_path}:${a.start_line}-${a.end_line}`
    if (name === 'search_entities') return [a.query && `"${a.query}"`, a.domain, a.area].filter(Boolean).join(' · ')
    if (name === 'list_automations' && a.query) return `"${a.query}"`
    if (['get_automation', 'remove_automation', 'get_automation_traces'].includes(name) && a.automation) return a.automation
    if (name === 'upsert_automation' && a.automation_yaml) {
      const m = /alias:\s*["']?([^\n"']+)/.exec(a.automation_yaml)
      return m ? m[1].trim() : ''
    }
  } catch {}
  return ''
}

export function makeResultSummary(name, result) {
  if (!result) return ''
  if (result.success === false) return result.error ? result.error.slice(0, 60) : 'error'
  if (result.changeset_id) return `changeset ${result.changeset_id}`
  if (name === 'search_config_files' && result.files) return `${result.files.length} file(s)`
  if (name === 'get_entity_states' && result.entities) return `${result.entities.length} entities`
  if (name === 'get_nodered_flows' && result.flows) return `${result.flows.length} flow(s)`
  if (name === 'save_memory' && result.id) return result.duplicate ? `already stored #${result.id}` : `#${result.id}`
  if (name === 'search_memory' && result.count !== undefined) return `${result.count} memor${result.count === 1 ? 'y' : 'ies'}`
  if (name === 'memory_doctor' && result.stale) return `${result.stale.length} stale, ${result.candidates} to review`
  if (name === 'search_past_sessions' && result.sessions) return `${result.sessions.length} session(s)`
  if (name === 'set_ha_text_entity' && result.entity_id) return result.entity_id
  if (name === 'schedule_ai_task' && result.task_id) return `task ${result.task_id}`
  if (name === 'list_changesets' && result.changesets) return `${result.changesets.length} changeset(s)`
  return 'ok'
}
