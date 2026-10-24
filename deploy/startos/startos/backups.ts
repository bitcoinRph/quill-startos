import { sdk } from './sdk'

// `models` is deliberately absent: the speech models are re-downloaded and
// verified by checksum on the first start after a restore.
export const { createBackup, restoreInit } = sdk.setupBackups(async () =>
  sdk.Backups.ofVolumes('main'),
)
