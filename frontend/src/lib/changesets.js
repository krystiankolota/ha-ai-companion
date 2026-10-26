// Any staging tool returns a top-level changeset_id; a tool-name allowlist drifted and hid approvals.
export function changesetFromResult(result) {
  if (!result?.success || !result.changeset_id) return null
  return {
    changeset_id: result.changeset_id,
    total_files: result.total_files,
    files: result.files,
    reason: result.reason,
    diff_stats: result.diff_stats || [],
    config_check_errors: result.config_check_errors,
  }
}
