export const CHANGESET_TOOLS = [
  'propose_config_changes', 'patch_config_key', 'patch_config_block', 'add_nodered_flow',
  'edit_nodered_tab', 'restore_changeset', 'upsert_automation', 'remove_automation',
]

export function changesetFromResult(functionName, result) {
  if (!CHANGESET_TOOLS.includes(functionName) || !result?.success || !result.changeset_id) return null
  return {
    changeset_id: result.changeset_id,
    total_files: result.total_files,
    files: result.files,
    reason: result.reason,
    diff_stats: result.diff_stats || [],
  }
}
